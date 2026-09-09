"""Execute signed fixed-design plans against preserved inputs, with sealed evidence.

The built-in admission policy accepts synthetic and public processed data only.
No patient-use authorization is implemented. The local filesystem and clock remain
operator trust boundaries; retain receipts independently in qualified storage.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import os
import platform
import shutil
import time
from pathlib import Path

from devharness_core.provenance import ArtifactLimitError, ExecutionDAG, copy_artifact, hash_artifact, verify_execution
from devharness_core.watchdog import run_sandboxed
from devharness_controlled.contracts import AnalysisPlan, canonical_json_bytes, parse_plan_bytes, parse_json_bytes
from devharness_controlled.trust import Approval, TrustedSigner, verify_plan_approvals


PACKAGES = ("devharness_controlled", "devharness_core", "devharness_server")
DEPENDENCIES = ("numpy", "pandas", "scipy", "statsmodels", "patsy", "scikit-learn",
                "zarr", "numcodecs", "h5py", "pydeseq2", "psutil", "pydantic", "cryptography", "PyNaCl", "matplotlib")
THREAD_VARIABLES = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024


class ControlledExecutionError(ValueError):
    """A controlled workflow gate rejected the attempted execution."""


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def execution_identity():
    """Return identities to include in a plan *before* collecting its approvals.

    Includes all shipped Python modules in the controlled numerical path and
    explicit library/platform/thread versions. This is an identity record, not a
    dependency lock or qualification of the recorded versions.
    """
    # The actual numerical process is spawned after these bounds are established.
    for name in THREAD_VARIABLES:
        os.environ[name] = "1"
    # PyDESeq2 imports pyplot even for nonplotting fits. Fresh workers must not
    # inherit a notebook-only backend module absent from their isolated venv.
    os.environ["MPLBACKEND"] = "Agg"
    root = Path(__file__).resolve().parent.parent
    source = {}
    for package in PACKAGES:
        for path in sorted((root / package).rglob("*.py")):
            if path.is_symlink() or path.resolve() != path:
                raise ControlledExecutionError("implementation_symlink")
            source[path.relative_to(root).as_posix()] = hash_artifact(path)["sha256"]
    environment = {
        "python": platform.python_version(), "implementation": platform.python_implementation(),
        "system": platform.system(), "machine": platform.machine(),
        "dependencies": {name: importlib.metadata.version(name) for name in DEPENDENCIES},
        "threads": {name: os.environ.get(name) for name in THREAD_VARIABLES},
        "plotting_backend": os.environ["MPLBACKEND"],
    }
    return {"implementation_sha256": _digest(source), "environment_sha256": _digest(environment),
            "source": source, "environment": environment}


def _check_identity(plan, identity):
    for key in ("implementation_sha256", "environment_sha256"):
        if getattr(plan, key) != identity[key]:
            raise ControlledExecutionError(key + "_mismatch")


def _absolute(path):
    path = Path(path).expanduser().absolute()
    if path.resolve() != path:
        raise ControlledExecutionError("path_symlink_or_noncanonical")
    return path


def _save(path, value):
    data = canonical_json_bytes(value)
    with path.open("xb") as handle:
        os.chmod(path, 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _snapshot(source, destination, expected, byte_limit):
    source = _absolute(source)
    limits = {"max_bytes": byte_limit, "max_entries": 10000}
    try:
        before = hash_artifact(source, **limits)
        if before["sha256"] != expected:
            raise ControlledExecutionError("input_hash_mismatch: " + destination.name)
        copy_artifact(source, destination, **limits)
        after = hash_artifact(destination, **limits)
        if ({key: value for key, value in after.items() if key != "name"}
                != {key: value for key, value in before.items() if key != "name"}
                or hash_artifact(source, **limits) != before):
            raise ControlledExecutionError("input_changed_during_snapshot: " + destination.name)
    except ArtifactLimitError as error:
        raise ControlledExecutionError("input_size_or_inventory_limit: " + destination.name) from error
    for path in ([destination] if destination.is_file() else list(destination.rglob("*"))):
        if path.is_file():
            path.chmod(0o400)
    return after


def _formula(plan):
    return " + ".join(["1", "numeric_time"] + [f"C({name})" for name in plan.categorical_covariates]
                      + list(plan.continuous_covariates))


def _validate_inputs(plan, expression, metadata_path):
    """All matrix/metadata materialization occurs inside the supervised worker."""
    import numpy as np
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore
    from devharness_server.tools_stat import _aligned_metadata, _candidate_designs

    if expression.suffix == ".zarr":
        # Zarr can invoke arbitrary registered codecs, including pickle. Admit
        # only the simple numeric/string v2 arrays emitted by our writer.
        for name in ("expression", "gene_names", "sample_names"):
            descriptor = expression / name / ".zarray"
            if descriptor.stat().st_size > 65536:
                raise ControlledExecutionError("oversized_zarr_descriptor")
            spec = parse_json_bytes(descriptor.read_bytes())
            dtype = np.dtype(spec["dtype"])
            compressor = spec.get("compressor")
            if (spec.get("zarr_format") != 2 or spec.get("filters") not in (None, [])
                    or spec.get("dimension_separator", ".") not in (".", "/")
                    or dtype.hasobject or dtype.kind not in ("biuf" if name == "expression" else "US")
                    or compressor is not None and compressor.get("id") != "blosc"):
                raise ControlledExecutionError("unapproved_zarr_encoding")

    with metadata_path.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    if not header or len(set(header)) != len(header):
        raise ControlledExecutionError("duplicate_or_missing_metadata_columns")
    metadata = pd.read_csv(metadata_path, dtype=str, keep_default_na=False)
    required = ("sample_id", plan.time_column, plan.biological_unit_column,
                *plan.categorical_covariates, *plan.continuous_covariates)
    for column in required:
        if column not in metadata or metadata[column].str.strip().eq("").any():
            raise ControlledExecutionError("missing_metadata: " + column)
        if (metadata[column] != metadata[column].str.strip()).any():
            raise ControlledExecutionError("noncanonical_metadata_whitespace: " + column)
    if metadata[plan.biological_unit_column].duplicated().any():
        raise ControlledExecutionError("repeated_biological_units")
    if metadata[plan.biological_unit_column].str.lower().isin(
            {"na", "n/a", "nan", "none", "null", "unknown", "unverified", "unavailable", "not_available"}).any():
        raise ControlledExecutionError("missing_biological_unit_identity")
    # Parse time explicitly as a finite numeric quantity in the declared unit;
    # permissive developmental-label parsing belongs to the research workflow.
    for column in (plan.time_column, *plan.continuous_covariates):
        try:
            values = pd.to_numeric(metadata[column], errors="raise").to_numpy(float)
        except ValueError as error:
            raise ControlledExecutionError("nonnumeric_metadata: " + column) from error
        if not np.isfinite(values).all():
            raise ControlledExecutionError("nonfinite_metadata: " + column)
    with ChunkedExpressionStore(expression) as store:
        if store.sample_names[0] != plan.reference_sample_id:
            raise ControlledExecutionError("reference_sample_order_mismatch")
        if plan.data_type == "counts":
            if tuple(store.gene_names) != plan.calibration_genes:
                raise ControlledExecutionError("locked_calibration_panel_mismatch")
            if store.shape[0] * store.shape[1] * 8 > 16 * 1024 * 1024:
                raise ControlledExecutionError("calibration_panel_exceeds_memory_bound")
        if not set(plan.hypothesis_genes).issubset(store.gene_names):
            raise ControlledExecutionError("missing_hypothesis_genes")
        aligned = _aligned_metadata(store, str(metadata_path), plan.time_column, plan.categorical_covariates)
        original = metadata.set_index("sample_id").loc[store.sample_names]
        for column in plan.categorical_covariates:
            if aligned[column].isna().any() or aligned[column].tolist() != original[column].tolist():
                raise ControlledExecutionError("categorical_metadata_changed: " + column)
        designs, rejected = _candidate_designs(aligned, _formula(plan), [plan.spline_df])
        design = designs[plan.spline_df][2]
        if not design["is_full_rank"] or design["condition_number"] > 1000:
            raise ControlledExecutionError("design_condition_number_exceeds_1000")
        return {"biological_unit_column": plan.biological_unit_column,
                "biological_unit_count": len(metadata), "independence_verified": False,
                "independence_scope": "unique declared IDs; biological independence needs study review",
                "reference_sample_id": plan.reference_sample_id, "time_unit": plan.time_unit,
                "sample_ids": store.sample_names, "design": design}


def _controlled_worker(plan_json, expression, metadata):
    from devharness_server.tools_stat import _fit_worker

    plan = parse_plan_bytes(plan_json.encode("utf-8"))
    _check_identity(plan, execution_identity())
    validation = _validate_inputs(plan, Path(expression), Path(metadata))
    result = _fit_worker(expression, metadata, _formula(plan), plan.time_column,
                         list(plan.hypothesis_genes), selection_criterion="fixed",
                         spline_dfs=[plan.spline_df], data_type=plan.data_type,
                         calibration_max_genes=len(plan.calibration_genes) or len(plan.hypothesis_genes), strict=True,
                         categorical_columns=plan.categorical_covariates)
    _check_identity(plan, execution_identity())
    return {"input_validation": validation, "analysis": result}


def _result_gates(plan, result):
    """Refuse partial/empty fits and a silently substituted normalization variant."""
    import math
    from devharness_server.tools_stat import bh_adjust

    if result.get("status") != "ok" or set(result.get("genes", {})) != set(plan.hypothesis_genes):
        raise ControlledExecutionError("incomplete_analysis")
    expected_normalization = "median_of_ratios" if plan.data_type == "counts" else "none"
    if result.get("normalization_method") != expected_normalization:
        raise ControlledExecutionError("unapproved_normalization_variant")
    genes = [result["genes"][name] for name in plan.hypothesis_genes]
    for gene in genes:
        if gene.get("status") != "ok" or gene.get("selected_df") != plan.spline_df:
            raise ControlledExecutionError("failed_or_uninformative_hypothesis")
        if not all(isinstance(gene.get(key), (int, float)) and math.isfinite(gene[key])
                   for key in ("p_value", "padj")) or not 0 <= gene["p_value"] <= gene["padj"] <= 1:
            raise ControlledExecutionError("invalid_probability")
    for gene, expected in zip(genes, bh_adjust([gene["p_value"] for gene in genes])):
        if not math.isclose(gene["padj"], expected, rel_tol=1e-12, abs_tol=1e-15):
            raise ControlledExecutionError("incorrect_bh_adjustment")


def execute_plan(plan: AnalysisPlan, approvals, signers, *, counts_path, metadata_path, output_dir):
    """Always use a new output directory. Attempts with valid plan schemas are sealed.

    Inputs/signers originate from a trusted coordinator. This function never takes
    private keys, never contacts an LLM and cannot authorize clinical release.
    """
    # Revalidate even model instances: Pydantic's model_copy/model_construct are
    # convenient trusted APIs that can otherwise bypass field validation.
    plan = parse_plan_bytes(plan.canonical_bytes())
    root = _absolute(output_dir)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    root.chmod(0o700)
    dag = ExecutionDAG(root / "audit", actor_id="controlled-numerical-coordinator")
    started = time.monotonic()
    outcome = {"schema_version": "controlled_execution/1", "plan_sha256": plan.sha256(),
               "status": "failed", "clinical_release_allowed": False,
               "memory_peak_mb": None, "memory_measurement_scope": "unavailable; watchdog enforces worker limit",
               "identity_scope": "configured-key approvals; coordinator OS identity remains caller-asserted"}
    previous = dag.append("attempt_started", {"plan_sha256": plan.sha256()})
    snapshots = {}
    try:
        _save(root / "plan.json", plan.model_dump(mode="json"))
        dag.add_artifact(root / "plan.json")
        # Preserve bounded JSON request content before semantic parsing so a
        # malformed signature/registry is still reviewable after rejection.
        for filename, items in (("approvals.json", approvals), ("trust-registry.json", signers)):
            raw = [item.model_dump(mode="json") if isinstance(item, (Approval, TrustedSigner)) else item for item in items]
            encoded = canonical_json_bytes(raw)
            if len(encoded) > 1024 * 1024:
                raise ControlledExecutionError("approval_request_exceeds_1mb")
            _save(root / filename, raw)
            dag.add_artifact(root / filename)
        approvals = [Approval.model_validate_json(canonical_json_bytes(
            a.model_dump(mode="json") if isinstance(a, Approval) else a)) for a in approvals]
        signers = [TrustedSigner.model_validate_json(canonical_json_bytes(
            s.model_dump(mode="json") if isinstance(s, TrustedSigner) else s)) for s in signers]
        receipts = verify_plan_approvals(plan, approvals, signers)
        previous = dag.append("plan_approvals_verified", {"receipts": receipts}, parents=[previous])
        if plan.data_classification == "restricted_clinical":
            raise ControlledExecutionError("clinical_admission_unconfigured: synthetic/public verification only")
        identity = execution_identity()
        _check_identity(plan, identity)
        _save(root / "environment.json", identity)
        dag.add_artifact(root / "environment.json")
        source_root = Path(__file__).resolve().parent.parent
        for name, expected in identity["source"].items():
            if dag.add_artifact(source_root / name)["sha256"] != expected:
                raise ControlledExecutionError("implementation_changed_during_snapshot")
        stage = root / "inputs"
        stage.mkdir(mode=0o700)
        suffix = Path(counts_path).suffix.lower()
        # HDF5 links, virtual datasets and external raw storage can reference
        # unhashed bytes outside an apparently self-contained .h5 file.
        if suffix not in {".zarr", ".csv"}:
            raise ControlledExecutionError("unsupported_expression_format")
        expression = stage / ("expression" + suffix)
        metadata = stage / "metadata.csv"
        for source, target, expected, limit in (
            (counts_path, expression, plan.counts_sha256, MAX_INPUT_BYTES),
            (metadata_path, metadata, plan.metadata_sha256, MAX_METADATA_BYTES),
        ):
            snapshots[target.name] = _snapshot(source, target, expected, limit)
            dag.add_artifact(target)
        previous = dag.append("inputs_preserved", {"inputs": snapshots}, parents=[previous])
        verify_plan_approvals(plan, approvals, signers)  # expiry is checked at launch too
        previous = dag.append("numerical_execution_started", {
            "worker": "devharness_controlled.executor._controlled_worker", "formula": _formula(plan),
            "selection_criterion": "fixed", "hypothesis_genes": list(plan.hypothesis_genes),
            "timeout_sec": plan.timeout_sec, "max_memory_mb": plan.max_memory_mb,
            "generated_code": False, "model_service_calls": 0,
        }, parents=[previous])
        result = run_sandboxed(_controlled_worker,
                              (plan.canonical_bytes().decode(), str(expression), str(metadata)),
                              max_mem_mb=plan.max_memory_mb, timeout_sec=plan.timeout_sec)
        _save(root / "numerical-result.json", result)
        dag.add_artifact(root / "numerical-result.json")
        previous = dag.append("numerical_execution_returned", {"result_sha256": hash_artifact(
            root / "numerical-result.json")["sha256"]}, parents=[previous])
        _result_gates(plan, result["analysis"])
        _check_identity(plan, execution_identity())
        outcome["status"] = "analysis_complete_pending_review"
    except Exception as error:
        outcome.update(error_type=type(error).__name__, error=str(error)[:6000])
    finally:
        changes = []
        for name, expected in snapshots.items():
            try:
                if hash_artifact(root / "inputs" / name) != expected:
                    changes.append(name)
            except Exception:
                changes.append(name)
        if changes:
            outcome.update(status="failed", error_type="InputIntegrityError",
                           error="snapshot_modified_during_execution", modified_inputs=changes)
        outcome["elapsed_seconds"] = time.monotonic() - started
        _save(root / "outcome.json", outcome)
        dag.add_artifact(root / "outcome.json")
        dag.append("attempt_finished", outcome, parents=[previous])
        receipt = dag.finalize(analysis_fingerprint=plan.sha256())
        _save(root / "receipt.json", receipt)
        verification = verify_execution(root / "audit" / "manifest.json", receipt["root_hash"])
        if not verification["valid"]:
            raise ControlledExecutionError("audit_verification_failed: " + "; ".join(verification["errors"]))
        from devharness_controlled.verification import verify_controlled_execution
        controlled_verification = verify_controlled_execution(root / "audit/manifest.json", receipt["root_hash"], signers)
        _save(root / "controlled-verification.json", controlled_verification)
        if outcome["status"] == "analysis_complete_pending_review" and not controlled_verification["valid"]:
            raise ControlledExecutionError("controlled_evidence_verification_failed: " + "; ".join(controlled_verification["errors"]))
    return {**outcome, "receipt": receipt, "audit_verification": verification,
            "controlled_verification": controlled_verification}
