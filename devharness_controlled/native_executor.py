"""Signed-plan native NB execution on synthetic/public counts, with retained evidence.

This candidate has no clinical admission or release path. Fixed matrices are
preserved and tested as supplied; the study's basis definition and biological
independence still require qualified review.
"""

from __future__ import annotations

import csv
import io
import math
import re
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import islice
from pathlib import Path

from devharness_core.provenance import ExecutionDAG, hash_artifact, verify_execution

from .contracts import canonical_json_bytes
from .executor import ControlledExecutionError, _absolute, _save, _snapshot
from .native_contracts import (
    NativeAnalysisPlan,
    NativeEffectPlan,
    is_governed_plan,
    is_reference_plan,
    parse_native_plan_bytes,
)
from .native_runtime import (
    DRIVER,
    RESULT_LIMIT,
    LocalDocker,
    native_identity,
    native_lock_for_image,
    run_native,
)
from .trust import Approval, TrustedSigner, verify_plan_approvals

INPUTS = {
    "counts.csv": ("counts_sha256", 64 * 1024 * 1024),
    "metadata.csv": ("metadata_sha256", 8 * 1024 * 1024),
    "full-design.csv": ("full_design_sha256", 8 * 1024 * 1024),
    "reduced-design.csv": ("reduced_design_sha256", 8 * 1024 * 1024),
    "native_nb.R": ("driver_sha256", 1024 * 1024),
    "native-package-sources.lock.json": ("package_lock_sha256", 1024 * 1024),
}
IDENTITIES = (
    "implementation_sha256",
    "environment_sha256",
    "driver_sha256",
    "package_lock_sha256",
    "runtime_image_id",
    "platform",
)
NUMERIC_TEXT = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z"
)


def _require(value, message):
    if not value:
        raise ControlledExecutionError(message)


def native_request(plan):
    value = {
        "schema_version": "native_nb_request/1",
        "engine": plan.engine,
        "counts_csv": "/input/counts.csv",
        "metadata_csv": "/input/metadata.csv",
        "full_design_csv": "/input/full-design.csv",
        "reduced_design_csv": "/input/reduced-design.csv",
        "hypothesis_genes": list(plan.hypothesis_genes),
        "temporal_columns": list(plan.temporal_columns),
        "normalization": plan.normalization,
        "data_classification": plan.data_classification,
    }
    if plan.engine == "deseq2_lrt":
        value["deseq2_fit_type"] = plan.deseq2_fit_type
    if isinstance(plan, NativeEffectPlan):
        value["schema_version"] = "native_nb_request/2"
        value["effect"] = {
            "name": plan.effect_name,
            "reference_sample_id": plan.effect_reference_sample_id,
            "comparison_sample_id": plan.effect_comparison_sample_id,
            "confidence_level": plan.effect_confidence_level,
            "time_unit": plan.time_unit,
        }
    if is_reference_plan(plan):
        value["schema_version"] = (
            "native_nb_request/4"
            if isinstance(plan, NativeEffectPlan)
            else "native_nb_request/3"
        )
        value["normalization_reference"] = {
            "gene_ids": list(plan.normalization_reference.gene_ids),
            "specification_sha256": plan.normalization_reference.sha256(),
            "eligibility_policy": plan.normalization_reference.eligibility_policy,
        }
    return value


def _locked_count_matrix(plan, rows):
    import numpy as np

    count_rows = rows("counts.csv")
    _require(
        next(count_rows, []) == ["gene_id", *plan.sample_ids],
        "locked_count_sample_order_mismatch",
    )
    genes = []
    counts = np.empty((len(plan.panel_genes), len(plan.sample_ids)), dtype=float)
    maximum = 2147483647 if plan.engine == "deseq2_lrt" else 2**53 - 1
    for row in count_rows:
        _require(len(row) == len(plan.sample_ids) + 1, "invalid_count_row_width")
        genes.append(row[0])
        _require(len(genes) <= len(plan.panel_genes), "locked_panel_size_mismatch")
        values = []
        for cell in row[1:]:
            _require(
                bool(NUMERIC_TEXT.fullmatch(cell)), "missing_or_noncanonical_raw_count"
            )
            try:
                number = Decimal(cell)
            except InvalidOperation as error:
                raise ControlledExecutionError("nonnumeric_raw_count") from error
            _require(
                number.is_finite()
                and 0 <= number <= maximum
                and number == number.to_integral_value(),
                "invalid_or_unrepresentable_raw_count",
            )
            values.append(float(number))
        counts[len(genes) - 1] = values
    _require(tuple(genes) == plan.panel_genes, "locked_panel_order_mismatch")
    return genes, counts


def check_input_tables(plan, read):
    """Bounded CSV identity/design verification, including during historical review.

    ``read`` supplies preserved bytes by filename. Counts are checked inside the
    trusted native driver too; no formulas or code are evaluated from these CSVs.
    """
    import numpy as np

    def rows(name):
        raw = read(name)
        _require(len(raw) <= INPUTS[name][1], "native_input_size_limit: " + name)
        return csv.reader(io.StringIO(raw.decode("utf-8")), strict=True)

    def valid_ids(values):
        return all(
            value
            and value.strip() == value
            and len(value.encode("utf-8")) <= 256
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            for value in values
        )

    _require(
        valid_ids((*plan.panel_genes, *plan.sample_ids)),
        "invalid_native_count_identifiers",
    )
    _require(
        len(plan.panel_genes) * len(plan.sample_ids) * 8 <= 16 * 1024 * 1024,
        "native_dense_count_memory_limit",
    )

    genes, counts = _locked_count_matrix(plan, rows)
    libraries = counts.sum(axis=0)
    _require(np.all(libraries > 0), "zero_library_size")
    reference = np.all(counts > 0, axis=1)
    if is_reference_plan(plan):
        from .normalization_reference import reference_genes_for_panel

        declared = reference_genes_for_panel(
            plan.normalization_reference,
            study_id=plan.study_id,
            panel_genes=plan.panel_genes,
        )
        declared_set = set(declared)
        selected = np.array([name in declared_set for name in genes])
        _require(
            np.all(reference[selected]), "nonpositive_declared_reference_no_fallback"
        )
        reference = selected
    _require(np.any(reference), "no_positive_rle_reference_no_fallback")
    log_reference = np.log(counts[reference])
    centered_log = log_reference - log_reference.mean(axis=1, keepdims=True)
    norm = {
        "method": "DESeq2_ratio" if plan.engine == "deseq2_lrt" else "edgeR_RLE",
        "scope": "entire_input_panel",
        "reference_gene_ids": [
            name for name, keep in zip(genes, reference, strict=True) if keep
        ],
    }
    if is_reference_plan(plan):
        norm.update(
            scope="prespecified_reference_genes",
            reference_specification_sha256=plan.normalization_reference.sha256(),
        )
    if plan.engine == "deseq2_lrt":
        norm["size_factors"] = dict(
            zip(
                plan.sample_ids,
                np.exp(np.median(centered_log, axis=0)).tolist(),
                strict=True,
            )
        )
    else:
        # edgeR 4.10.5 takes medians on the ratio scale; DESeq2 1.52.0
        # takes medians on the log scale. Even-sized panels can differ.
        ratios = np.median(np.exp(centered_log), axis=0)
        factors = ratios / libraries
        factors /= np.exp(np.log(factors).mean())
        effective = libraries * factors
        centered = effective / np.exp(np.log(effective).mean())
        norm.update(
            normalization_factors=dict(
                zip(plan.sample_ids, factors.tolist(), strict=True)
            ),
            effective_library_sizes=dict(
                zip(plan.sample_ids, effective.tolist(), strict=True)
            ),
            centered_size_factors=dict(
                zip(plan.sample_ids, centered.tolist(), strict=True)
            ),
        )
    metadata = rows("metadata.csv")
    _require(
        next(metadata, []) == ["sample_id", "biological_unit_id", "numeric_time"],
        "invalid_native_metadata_columns",
    )
    data = list(islice(metadata, len(plan.sample_ids) + 1))
    _require(
        len(data) == len(plan.sample_ids) and all(len(row) == 3 for row in data),
        "invalid_native_metadata_shape",
    )
    _require(
        tuple(row[0] for row in data) == plan.sample_ids,
        "locked_metadata_sample_order_mismatch",
    )
    units = [row[1] for row in data]
    metadata_rows = data
    missing = {
        "na",
        "n/a",
        "nan",
        "none",
        "null",
        "unknown",
        "unverified",
        "unavailable",
        "not_available",
    }
    _require(
        len(set(units)) == len(units)
        and valid_ids(units)
        and all(u.lower() not in missing for u in units),
        "invalid_or_repeated_biological_unit_identity",
    )
    _require(
        all(
            NUMERIC_TEXT.fullmatch(row[2]) and math.isfinite(float(row[2]))
            for row in data
        )
        and len({float(row[2]) for row in data}) >= len(plan.temporal_columns) + 1,
        "invalid_native_time",
    )
    matrices, designs = {}, {}
    for name in ("full", "reduced"):
        design_rows = rows(name + "-design.csv")
        header = next(design_rows, [])
        _require(
            len(header) >= 2
            and header[0:2] == ["sample_id", "Intercept"]
            and len(set(header)) == len(header)
            and len(header) <= 33
            and all(
                re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in header[1:]
            ),
            "invalid_native_design_columns",
        )
        data = list(islice(design_rows, len(plan.sample_ids) + 1))
        _require(
            len(data) == len(plan.sample_ids)
            and all(len(row) == len(header) for row in data)
            and tuple(row[0] for row in data) == plan.sample_ids,
            "locked_design_sample_order_mismatch",
        )
        _require(
            all(NUMERIC_TEXT.fullmatch(cell) for row in data for cell in row[1:]),
            "noncanonical_native_design_value",
        )
        x = np.array([row[1:] for row in data], dtype=float)
        _require(
            np.isfinite(x).all() and np.all(x[:, 0] == 1),
            "invalid_numeric_native_design",
        )
        rank, condition = int(np.linalg.matrix_rank(x)), float(np.linalg.cond(x))
        _require(
            rank == x.shape[1] < x.shape[0]
            and math.isfinite(condition)
            and condition <= 1000,
            "rank_deficient_or_ill_conditioned_native_design",
        )
        matrices[name] = x
        designs[name + "_design"] = {
            "columns": header[1:],
            "rank": rank,
            "condition_number": condition,
            "residual_df": x.shape[0] - rank,
        }
    temporal = [i - 1 for i in plan.temporal_columns]
    _require(max(temporal) < matrices["full"].shape[1], "invalid_temporal_column_index")
    keep = [i for i in range(matrices["full"].shape[1]) if i not in temporal]
    _require(
        np.array_equal(matrices["full"][:, keep], matrices["reduced"])
        and [designs["full_design"]["columns"][i] for i in keep]
        == designs["reduced_design"]["columns"],
        "reduced_design_is_not_exact_non_temporal_subset",
    )
    validation = {
        "panel_gene_ids": list(plan.panel_genes),
        "sample_ids": list(plan.sample_ids),
        "biological_unit_count": len(units),
        "independence_verified": False,
        "temporal_columns": list(plan.temporal_columns),
        "temporal_names": [designs["full_design"]["columns"][i] for i in temporal],
        "normalization_from_counts": norm,
        **designs,
    }
    if isinstance(plan, NativeEffectPlan):
        from .native_effects import effect_design

        validation["effect_contrast"] = effect_design(
            plan,
            matrices["full"],
            designs["full_design"]["columns"],
            [float(row[2]) for row in metadata_rows],
            counts,
        )
    return validation


def _check_identity(plan, identity):
    for name in IDENTITIES:
        _require(getattr(plan, name) == identity[name], name + "_mismatch")


def _native_authorization(
    plan,
    approvals,
    signers,
    receipts,
    root,
    dag,
    preserve_authority,
    authority_store_dir,
    authority_administrators,
    authority_checkpoint,
):
    if not is_governed_plan(plan):
        _require(
            all(
                value is None
                for value in (
                    authority_store_dir,
                    authority_administrators,
                    authority_checkpoint,
                )
            ),
            "Authority configuration requires an explicitly governed plan",
        )

        def verify_start_approvals():
            return verify_plan_approvals(plan, approvals, signers)

        return None, verify_start_approvals, {}
    from .authority_evidence import administrator_values, selected_signers
    from .authority_store import MAX_SNAPSHOT_BYTES, AuthorityStore, authorize_snapshot

    _require(
        authority_store_dir is not None
        and authority_administrators is not None
        and authority_checkpoint is not None,
        (
            "authority_configuration_required: current store, administrators and"
            " checkpoint must be explicit"
        ),
    )
    authority_path = _absolute(authority_store_dir)
    _require(
        not authority_path.is_relative_to(root)
        and not root.is_relative_to(authority_path),
        "authority_store_and_execution_must_not_overlap",
    )
    store = AuthorityStore(authority_path)
    saved_authority = store.capture()
    administrators = administrator_values(authority_administrators)
    _require(
        len(canonical_json_bytes(authority_checkpoint)) <= MAX_SNAPSHOT_BYTES,
        "Authority checkpoint exceeds bound",
    )
    authority_refs = {}
    for name, value in (
        ("authority-snapshot.json", saved_authority),
        ("authority-administrators.json", administrators),
        ("authority-checkpoint.json", authority_checkpoint),
    ):
        _save(root / name, value)
        authority_refs[name] = dag.add_artifact(root / name)
    preserve_authority(
        {"references": authority_refs, "store_path": str(authority_path)}
    )
    chosen = selected_signers(signers, receipts)
    decision = authorize_snapshot(
        plan,
        chosen,
        administrators,
        saved_authority,
        expected_checkpoint=authority_checkpoint,
    )
    dispatch_decision = {}

    @contextmanager
    def governed_authority_guard():
        with store.launch_guard(
            plan, chosen, administrators, expected_checkpoint=authority_checkpoint
        ) as proof:
            _require(
                proof["decision"]["snapshot_sha256"]
                == authority_refs["authority-snapshot.json"]["sha256"],
                "Authority snapshot changed before dispatch",
            )
            dispatch_decision.clear()
            dispatch_decision.update(proof["decision"])
            yield dispatch_decision

    def governed_start_authorization():
        at = datetime.now(UTC)
        current = verify_plan_approvals(plan, approvals, signers, now=at)
        renewed = authorize_snapshot(
            plan,
            chosen,
            administrators,
            saved_authority,
            now=at,
            expected_checkpoint=authority_checkpoint,
        )
        dispatch_decision.clear()
        dispatch_decision.update(renewed)
        return current

    def governed_dispatch_authorization(at):
        verify_plan_approvals(plan, approvals, signers, now=at)
        authorize_snapshot(
            plan,
            chosen,
            administrators,
            saved_authority,
            now=at,
            expected_checkpoint=authority_checkpoint,
        )

    return (
        decision,
        governed_start_authorization,
        {
            "authority_guard": governed_authority_guard,
            "authorize_dispatch": governed_dispatch_authorization,
        },
    )


def _preserve_native_trust(root, dag, plan, operation, approvals, signers):
    if operation is not None:
        _require(
            type(operation) is dict
            and set(operation)
            == {
                "schema_version",
                "registry_id",
                "attempt_id",
                "plan_sha256",
                "submission_sha256",
            }
            and operation["schema_version"] == "operated_native_binding/1"
            and operation["plan_sha256"] == plan.sha256()
            and all(
                type(operation[key]) is str
                and re.fullmatch(r"[0-9a-f]{32}", operation[key])
                for key in ("registry_id", "attempt_id")
            )
            and type(operation["submission_sha256"]) is str
            and re.fullmatch(r"[0-9a-f]{64}", operation["submission_sha256"]),
            "invalid_operated_native_binding",
        )
        _save(root / "operation.json", operation)
        dag.add_artifact(root / "operation.json")
    _save(root / "plan.json", plan.model_dump(mode="json"))
    dag.add_artifact(root / "plan.json")
    for name, entries in (
        ("approvals.json", approvals),
        ("trust-registry.json", signers),
    ):
        if isinstance(entries, dict):
            entries = list(entries.values())
        _require(
            isinstance(entries, (list, tuple)), "approval_and_trust_lists_required"
        )
        raw = [
            v.model_dump(mode="json") if isinstance(v, (Approval, TrustedSigner)) else v
            for v in entries
        ]
        _require(
            len(canonical_json_bytes(raw)) <= 1024 * 1024,
            "approval_request_exceeds_1mb",
        )
        _save(root / name, raw)
        dag.add_artifact(root / name)


def _stage_native_inputs(
    root,
    dag,
    plan,
    snapshots,
    counts_path,
    metadata_path,
    full_design_path,
    reduced_design_path,
):
    from .verification import _read

    stage = root / "inputs"
    stage.mkdir(mode=0o700)
    supplied = {
        "counts.csv": counts_path,
        "metadata.csv": metadata_path,
        "full-design.csv": full_design_path,
        "reduced-design.csv": reduced_design_path,
        "native_nb.R": DRIVER,
        "native-package-sources.lock.json": native_lock_for_image(
            plan.runtime_image_id
        ),
    }
    for name, (key, limit) in INPUTS.items():
        _require(
            _absolute(supplied[name]).is_file(),
            "native_input_must_be_regular_file: " + name,
        )
        snapshots[name] = _snapshot(
            supplied[name], stage / name, getattr(plan, key), limit
        )
        dag.add_artifact(stage / name)
    validation = check_input_tables(
        plan,
        lambda name: _read(stage / name, snapshots[name]["sha256"], INPUTS[name][1]),
    )
    return stage, validation


def _retain_native_transport(root, dag, transport, logs, outputs):
    from .verification import _json, _read

    _save(root / "native-transport.json", transport)
    transport_ref = dag.add_artifact(root / "native-transport.json")
    for path in sorted(logs.iterdir()):
        dag.add_artifact(path)
    client_config = root / "native-client/config.json"
    if client_config.is_file():
        dag.add_artifact(client_config)
    result_ref, result = None, None
    result_path = outputs / "result.json"
    if result_path.exists() or result_path.is_symlink():
        # Preserve raw native bytes; canonical JSON is not a substitute for
        # the exact engine output (including a malformed/failed answer).
        raw = _read(result_path, limit=RESULT_LIMIT)
        result_ref = dag.add_artifact(result_path)
        try:
            result = _json(raw)
        except (ValueError, TypeError, UnicodeError):
            result = None
    return transport_ref, result_ref, result


def _modified_native_inputs(root, snapshots, request_ref):
    references = dict(snapshots)
    if request_ref is not None:
        references["request.json"] = request_ref
    changed = []
    for name, expected in references.items():
        try:
            if hash_artifact(root / "inputs" / name) != expected:
                changed.append(name)
        except Exception:
            changed.append(name)
    return changed


def execute_native_plan(
    plan: NativeAnalysisPlan,
    approvals,
    signers,
    *,
    counts_path,
    metadata_path,
    full_design_path,
    reduced_design_path,
    output_dir,
    operation=None,
    on_checkpoint=None,
    authority_store_dir=None,
    authority_administrators=None,
    authority_checkpoint=None,
):
    """Execute a supported, signed native plan in a fresh directory; seal failures."""
    from .native_results import validate_native_result
    from .verification import _json, _read

    plan = parse_native_plan_bytes(plan.canonical_bytes())
    root = _absolute(output_dir)
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    root.chmod(0o700)
    dag = ExecutionDAG(root / "audit", actor_id="controlled-native-coordinator")
    started = time.monotonic()
    outcome = {
        "schema_version": "controlled_native_execution/1",
        "plan_sha256": plan.sha256(),
        "status": "failed",
        "clinical_release_allowed": False,
        "qualification_status": "candidate_unqualified",
        "memory_peak_mb": None,
        "memory_measurement_scope": (
            "unknown; inspect transport for any verified container ceiling"
        ),
        "identity_scope": (
            "configured-key approvals; host execution is not remotely attested"
        ),
    }
    previous = dag.append(
        "attempt_started", {"plan_sha256": plan.sha256(), "workspace": str(root)}
    )
    snapshots: dict[str, dict[str, object]] = {}
    request_ref, runtime = None, None

    def preserve_authority(payload):
        nonlocal previous
        previous = dag.append(
            "authority_snapshot_preserved", payload, parents=[previous]
        )

    try:
        _preserve_native_trust(root, dag, plan, operation, approvals, signers)
        approvals = _json(_read(root / "approvals.json", limit=1024 * 1024))
        signers = _json(_read(root / "trust-registry.json", limit=1024 * 1024))
        receipts = verify_plan_approvals(plan, approvals, signers)
        previous = dag.append(
            "plan_approvals_verified", {"receipts": receipts}, parents=[previous]
        )
        _require(
            plan.data_classification != "restricted_clinical",
            "clinical_admission_unconfigured: synthetic/public verification only",
        )
        decision, start_authorization, authorization = _native_authorization(
            plan,
            approvals,
            signers,
            receipts,
            root,
            dag,
            preserve_authority,
            authority_store_dir,
            authority_administrators,
            authority_checkpoint,
        )
        if decision is not None:
            previous = dag.append(
                "authority_policy_verified", {"decision": decision}, parents=[previous]
            )
        runtime = LocalDocker()
        runtime.native_platform = plan.platform
        identity = native_identity(runtime)
        _check_identity(plan, identity)
        _save(root / "environment.json", identity)
        dag.add_artifact(root / "environment.json")
        source_root = Path(__file__).resolve().parent.parent
        for name, expected in identity["source"].items():
            _require(
                dag.add_artifact(source_root / name)["sha256"] == expected,
                "implementation_changed_during_snapshot",
            )
        stage, validation = _stage_native_inputs(
            root,
            dag,
            plan,
            snapshots,
            counts_path,
            metadata_path,
            full_design_path,
            reduced_design_path,
        )
        _save(stage / "request.json", native_request(plan))
        (stage / "request.json").chmod(0o400)
        request_ref = dag.add_artifact(stage / "request.json")
        previous = dag.append(
            "inputs_preserved",
            {
                "inputs": snapshots,
                "request": request_ref,
                "staging_dir": str(stage),
                "input_validation": validation,
            },
            parents=[previous],
        )
        outputs, logs = root / "native-output", root / "native-logs"
        outputs.mkdir(mode=0o700)
        logs.mkdir(mode=0o700)
        verify_plan_approvals(plan, approvals, signers)
        previous = dag.append(
            "native_execution_started",
            {
                "request_sha256": request_ref["sha256"],
                "runtime_image_id": plan.runtime_image_id,
                "generated_code": False,
                "model_service_calls": 0,
            },
            parents=[previous],
        )
        transport = run_native(
            plan,
            stage,
            outputs,
            logs,
            runtime=runtime,
            authorize_start=start_authorization,
            attempt_id=operation["attempt_id"] if operation is not None else None,
            on_checkpoint=on_checkpoint,
            **authorization,
        )
        transport_ref, result_ref, result = _retain_native_transport(
            root,
            dag,
            transport,
            logs,
            outputs,
        )
        previous = dag.append(
            "native_execution_returned",
            {"transport": transport_ref, "result": result_ref},
            parents=[previous],
        )
        _require(
            transport["status"] == "ok",
            "native_transport_failed: "
            + transport.get("error", "unknown transport failure"),
        )
        if result is None:
            raise ControlledExecutionError("missing_or_malformed_native_result")
        verdict = validate_native_result(plan, result)
        _require(
            verdict["usable"], "native_analysis_failed: " + str(result.get("error"))
        )
        outcome["qualification_limits"] = verdict["qualification_limits"]
        _check_identity(plan, native_identity(runtime))
        outcome["status"] = "analysis_complete_pending_review"
    except Exception as error:
        outcome.update(error_type=type(error).__name__, error=str(error)[:6000])
    finally:
        changed = _modified_native_inputs(root, snapshots, request_ref)
        if changed:
            outcome.update(
                status="failed",
                error_type="InputIntegrityError",
                error="native_snapshot_modified_during_execution",
                modified_inputs=changed,
            )
        outcome["elapsed_seconds"] = time.monotonic() - started
        _save(root / "outcome.json", outcome)
        dag.add_artifact(root / "outcome.json")
        dag.append("attempt_finished", outcome, parents=[previous])
        receipt = dag.finalize(analysis_fingerprint=plan.sha256())
        _save(root / "receipt.json", receipt)
        integrity = verify_execution(root / "audit/manifest.json", receipt["root_hash"])
        _require(integrity["valid"], "native_audit_integrity_failure")
        from .native_verification import verify_native_execution

        verified = verify_native_execution(
            root / "audit/manifest.json",
            receipt["root_hash"],
            signers,
            **(
                {"authority_administrators": authority_administrators}
                if authority_administrators is not None
                else {}
            ),
        )
        _save(root / "controlled-verification.json", verified)
        _require(
            outcome["status"] != "analysis_complete_pending_review"
            or verified["valid"],
            "native_controlled_evidence_verification_failed: "
            + "; ".join(verified["errors"]),
        )
    return {
        **outcome,
        "receipt": receipt,
        "audit_verification": integrity,
        "controlled_verification": verified,
    }


def _verify_erql_replay_authority(plan, replay_authority):
    if replay_authority is not None:
        from devbench.platform.workflow_entrypoints import ERQLReplayAuthority

        if type(replay_authority) is not ERQLReplayAuthority:
            raise TypeError("ERQL replay requires typed fresh execution authority")
        replay_authority.validate(plan)


def _retain_erql_transport(root, dag, transport, result, logs, outputs):
    _save(root / "transport.json", transport)
    dag.add_artifact(root / "transport.json")
    for path in sorted(logs.iterdir()):
        dag.add_artifact(path)
    raw = outputs / "result.json"
    if raw.exists() and not raw.is_symlink():
        result["raw_result"] = dag.add_artifact(raw)
    return raw


def _preserve_erql_family(root, dag, plan, result, family):
    from devbench.analysis.episode_synthesis import build_erql_family_synthesis
    from devbench.core.publication_families import FamilyEvidence

    if family is None:
        family = FamilyEvidence(
            family_id=plan.family_id,
            allocation_sha256=plan.allocation.sha256(),
            request_sha256=plan.request_sha256,
            status="withheld",
            reason="execution_failed",
        )
    _save(root / "family-evidence.json", family.model_dump(mode="json"))
    result["family_evidence"] = dag.add_artifact(root / "family-evidence.json")
    _save(
        root / "family-synthesis.json",
        build_erql_family_synthesis(plan.allocation, (family,)),
    )
    result["family_synthesis"] = dag.add_artifact(root / "family-synthesis.json")


def execute_erql_plan(
    plan, approvals, signers, *, request_path, output_dir, replay_authority=None
):
    """Execute a signed synthetic ERQL capsule and retain its raw evidence."""
    from devbench.engine.execution_snapshot import current_binding

    from .erql_contracts import (
        LOCK,
        REQUEST_LIMIT,
        ERQLFamilyPlan,
        driver_for_profile,
        parse_erql_plan,
        read_erql_request,
    )
    from .erql_results import validate_erql_result
    from .verification import _json, _read

    plan = parse_erql_plan(plan)
    _verify_erql_replay_authority(plan, replay_authority)
    root = _absolute(output_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    stage, outputs, logs = (
        root / name for name in ("inputs", "native-output", "native-logs")
    )
    for path in (stage, outputs, logs):
        path.mkdir(mode=0o700)
    dag = ExecutionDAG(root / "audit", actor_id="controlled-erql-coordinator")
    result = {
        "schema_version": "controlled_erql_execution/1",
        "status": "failed",
        "plan_sha256": plan.sha256(),
        "profile": plan.profile,
        "qualification_status": "candidate_unqualified",
        "clinical_release_allowed": False,
    }
    binding = current_binding()
    if binding is not None:
        result["execution_code"] = binding
    event = dag.append("attempt_started", result)
    request = family = None
    for name, value in (
        ("plan.json", plan.model_dump(mode="json")),
        (
            "approvals.json",
            [
                row.model_dump(mode="json") if isinstance(row, Approval) else row
                for row in approvals
            ],
        ),
        (
            "trust-registry.json",
            [
                row.model_dump(mode="json") if isinstance(row, TrustedSigner) else row
                for row in signers
            ],
        ),
    ):
        _save(root / name, value)
        dag.add_artifact(root / name)
    try:
        approvals = _json(_read(root / "approvals.json", limit=1024**2))
        signers = _json(_read(root / "trust-registry.json", limit=1024**2))

        def authorize():
            return verify_plan_approvals(plan, approvals, signers)

        authorize()
        driver = driver_for_profile(plan.profile)
        bindings = (
            ("request.json", request_path, plan.request_sha256, REQUEST_LIMIT),
            (driver.name, driver, plan.driver_sha256, 1024**2),
            ("erql-package-sources.lock.json", LOCK, plan.package_lock_sha256, 1024**2),
        )
        staged = {}
        for name, path, expected, limit in bindings:
            staged[name] = _snapshot(path, stage / name, expected, limit)
            dag.add_artifact(stage / name)
        request = read_erql_request(stage / "request.json", plan)
        runtime = LocalDocker(erql=True)
        replay_options = (
            {
                "attempt_id": replay_authority.token["attempt_id"],
                "authority_guard": replay_authority.guard,
                "authorize_dispatch": replay_authority.authorize_dispatch,
            }
            if replay_authority is not None
            else {}
        )
        transport = run_native(
            plan,
            stage,
            outputs,
            logs,
            authorize_start=authorize,
            runtime=runtime,
            **replay_options,
        )
        raw = _retain_erql_transport(root, dag, transport, result, logs, outputs)
        _require(
            transport["status"] == "ok",
            "erql_transport_failed: " + transport.get("error", "unknown"),
        )
        for name, reference in staged.items():
            _require(
                hash_artifact(stage / name) == reference,
                "ERQL staged input changed: " + name,
            )
        native = _json(_read(raw, result["raw_result"]["sha256"], RESULT_LIMIT))
        result["family"] = validate_erql_result(native, request)
        if isinstance(plan, ERQLFamilyPlan):
            from devbench.analysis.scientific_result_contract import (
                erql_family_evidence,
            )

            family = erql_family_evidence(plan, request, native)
        result["status"] = "analysis_complete_pending_review"
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error)[:4000])
    if isinstance(plan, ERQLFamilyPlan) and request is not None:
        _preserve_erql_family(root, dag, plan, result, family)
    _save(root / "outcome.json", result)
    dag.add_artifact(root / "outcome.json")
    dag.append("attempt_finished", result, parents=[event])
    receipt = dag.finalize(analysis_fingerprint=plan.sha256())
    _save(root / "receipt.json", receipt)
    integrity = verify_execution(root / "audit/manifest.json", receipt["root_hash"])
    _require(integrity["valid"], "erql_audit_integrity_failure")
    return {**result, "receipt": receipt, "audit_verification": integrity}
