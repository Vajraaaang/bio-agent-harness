"""Closed trusted FASTQ backend; no caller-selected code or executor.

The retained manifest binds external read/reference bytes by identity. Those
large bytes are not copied into the generic method artifact store. Network
access is explicit because Nextflow bootstraps pinned workflow sources/plugins.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

from devharness_core.methods import METHOD_FORMAT, MethodContract
from devharness_core.provenance import _digest, canonical_json, hash_artifact

FILES = {"count_matrix": "counts.tsv", "differential_expression": "differential-expression.tsv",
         "count_model_provenance": "count-model.json", "result": "result.json"}
_SALMON_FILES = {"effective_lengths": "salmon_gene_lengths.tsv",
                 "salmon_quant_provenance": "salmon_quant_provenance.json"}
MAX_BYTES = 100_000_000


def _files(execution):
    return (
        {**FILES, **_SALMON_FILES}
        if execution.get("execution_profile") == "qc-salmon"
        else FILES
    )


def fastq_source():
    return Path(__file__).resolve()


def _runtime_identity():
    from devharness_server.method_adapter import _runtime_identity as core_identity
    root = fastq_source().parent
    owners = set(root.glob("fastq*.py"))
    owners.update(root / name for name in ("__init__.py", "methods.py", "method_cli.py", "method_planner_cli.py", "method_executor.py",
        "method_validation.py", "method_planner.py", "method_planner_contract.py", "method_planner_recovery.py",
        "rna_design_audit.py", "rna_execution_inputs.py",
        "workflow_entrypoints.py", "workflow_actions.py", "workflow_state.py", "workflow_operations.py",
        "workflow_implementation.py", "workflow_science.py", "workflow_scientific_reviews.py",
        "workflow_discovery.py", "langgraph_workflow.py", "scientific_workspace.py"))
    owners.update(root.parent / name for name in (
        "platform/workflow_entrypoints.py", "platform/method_planner.py", "platform/scientific_workspace.py",
        "platform/workflow_actions.py", "platform/workflow_adaptive_actions.py", "platform/workflow_diagnostic_actions.py",
        "platform/workflow_serial_evidence.py", "platform/workflow_state.py", "platform/langgraph_workflow.py",
        "engine/method_provenance.py", "engine/method_executor.py", "engine/methods.py", "engine/workflow_operations.py"))
    if len(owners) > 64:
        raise ValueError("Trusted FASTQ source inventory exceeds its file bound")
    sources = {}
    total = 0
    for path in sorted(owners):
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            raise ValueError("Trusted FASTQ source must be a regular non-symlink file")
        identity = hash_artifact(path, max_bytes=min(2_000_000, 16_000_000 - total), max_entries=1)
        total += identity["size_bytes"]
        sources[path.name if path.parent == root else path.relative_to(root.parent).as_posix()] = identity["sha256"]
    return _digest({"core_and_bulk": core_identity("bulk_condition_contrast"), "owners": sources})


def fastq_contract(manifest):
    from .fastq_planner_binding import validate_fastq_manifest
    manifest = validate_fastq_manifest(manifest)
    if manifest["preflight_outcome"]["status"] != "passed":
        raise ValueError("Trusted FASTQ registration requires passed preflight")
    return fastq_execution_contract(manifest["execution"])


def fastq_execution_contract(execution):
    """Reusable exact runtime contract; data identity belongs to the input port."""
    from .fastq_execution import NEXTFLOW_DIST_SHA256, _absolute, _identity
    from .fastq_planner_binding import _execution_options

    options = _execution_options(execution)
    if options.get("quantification_only"):
        raise ValueError("Registered FASTQ DE requires the reviewed DE artifact family")
    if execution["nextflow_sha256"] != NEXTFLOW_DIST_SHA256:
        raise ValueError("Trusted FASTQ only admits the pinned official Nextflow distribution")
    executable = _absolute(execution["nextflow_path"], exists=True)
    actual = _identity(executable, maximum=100_000_000)
    if actual["sha256"] != execution["nextflow_sha256"]:
        raise ValueError("Trusted FASTQ pinned Nextflow executable identity changed")
    resources = execution["resources"]
    if (type(resources) is not dict or set(resources) != {"cpus", "memory_gb"} or type(resources["cpus"]) is not int
            or type(resources["memory_gb"]) is not int or not 1 <= resources["cpus"] <= 16
            or not 1 <= resources["memory_gb"] <= 64
            or type(execution["timeout_seconds"]) is not int
            or not 1 <= execution["timeout_seconds"] <= 3600):
        raise ValueError("Trusted FASTQ must fit existing method execution limits")
    return MethodContract.from_dict({
        "format": METHOD_FORMAT, "method_id": "devharness.trusted_fastq", "name": "Pinned FASTQ analysis",
        "lifecycle": "registered", "implementation_trust": "reviewed",
        "inputs": {"manifest": {"artifact_type": "json", "required": True}},
        "parameters": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
        "outputs": {
            name: {"artifact_type": "json" if filename.endswith(".json") else "table",
                   "path": "/outputs/" + filename, "required": True}
            for name, filename in _files(execution).items()
        },
        "implementation": {"kind": "trusted_fastq", "source_sha256": hash_artifact(fastq_source())["sha256"],
                           "execution_binding": execution},
        "environment": {"runtime": "trusted_fastq", "identity_sha256": _runtime_identity()},
        "execution": {"cpu": resources["cpus"], "memory_mb": resources["memory_gb"] * 1024,
                      "timeout_seconds": execution["timeout_seconds"], "pids": 256,
                      "max_output_bytes": MAX_BYTES, "network": "allow"},
        "validation": {"comparator": "json", "invariants": [], "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "registered_trusted_fastq_reference_bound_external_bytes"},
    })


def _json(path, maximum=1_000_000):
    from .method_executor import _path
    path = _path(path)
    expected = hash_artifact(path, max_bytes=maximum, max_entries=1)["sha256"]
    with path.open("rb") as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError("Bounded JSON artifact changed during verification")
    value = json.loads(body)
    canonical_json(value)
    return value


def _count_gene_ids(path, samples, salmon):
    with path.open(newline="") as stream:
        rows = csv.reader(stream, delimiter="\t")
        if next(rows, None) != ["gene_id", *samples]:
            raise ValueError(
                "Count-matrix sample identity/order differs from frozen design"
            )
        genes = []
        for row in rows:
            if len(row) != len(samples) + 1 or not row[0]:
                raise ValueError("Count matrix must align to the frozen sample axis")
            if salmon:
                if any(
                    not math.isfinite(float(value)) or float(value) < 0
                    for value in row[1:]
                ):
                    raise ValueError(
                        "Salmon counts must contain finite nonnegative estimates"
                    )
            elif any(not value.isascii() or not value.isdigit() for value in row[1:]):
                raise ValueError(
                    "Count matrix must contain direct nonnegative integer counts"
                )
            genes.append(row[0])
            if len(genes) > 100_000:
                raise ValueError("Count matrix exceeds the feature-family bound")
    if not genes or len(set(genes)) != len(genes):
        raise ValueError("Count matrix gene identities must be nonempty and unique")
    return genes


def _verify_salmon_products(record, provenance, controls, refs, paths, genes, samples):
    from .fastq_execution import SALMON_COUNT_SOURCE

    policy = provenance.get("numerical_policy", {})
    if (
        record.get("count_source") != SALMON_COUNT_SOURCE
        or provenance.get("count_source") != SALMON_COUNT_SOURCE
        or policy.get("estimated_count_rounding") is not False
        or policy.get("normalization") != "tximport_effective_length_and_TMM_2D_offset"
    ):
        raise ValueError(
            "Salmon DE requires estimated-count and effective-length provenance"
        )
    quant = _json(paths["salmon_quant_provenance"], MAX_BYTES)
    if (
        quant.get("count_source") != SALMON_COUNT_SOURCE
        or quant.get("counts_from_abundance") != "no"
        or quant.get("tx2gene", {}).get("sha256") != controls["tx2gene"]["sha256"]
        or any(
            quant["artifacts"][role]["sha256"] != refs[role]["sha256"]
            for role in ("count_matrix", "effective_lengths")
        )
    ):
        raise ValueError(
            "Salmon quantification differs from the frozen map and matrix identities"
        )
    with paths["effective_lengths"].open(newline="") as stream:
        rows = csv.reader(stream, delimiter="\t", strict=True)
        if next(rows, None) != ["gene_id", *samples]:
            raise ValueError(
                "Salmon effective lengths differ from the frozen sample axis"
            )
        for gene, row in zip(genes, rows, strict=True):
            if (
                len(row) != len(samples) + 1
                or row[0] != gene
                or any(
                    not math.isfinite(float(value)) or float(value) <= 0
                    for value in row[1:]
                )
            ):
                raise ValueError(
                    "Salmon effective lengths require positive aligned estimates"
                )


def _verified_artifact_paths(refs, work_dir):
    from .method_executor import _path

    paths = {}
    for name, ref in refs.items():
        path = _path(ref["path"])
        if not path.is_file() or not path.is_relative_to(work_dir):
            raise ValueError("FASTQ output escaped its retained workflow directory")
        actual = hash_artifact(path, max_bytes=MAX_BYTES, max_entries=1)
        if any(actual[key] != ref[key] for key in ("sha256", "size_bytes")):
            raise ValueError("FASTQ output identity mismatch")
        paths[name] = path
    return paths


def _de_gene_ids(path):
    with path.open(newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t")
        if not {"gene_id", "pvalue", "padj"} <= set(rows.fieldnames or []):
            raise ValueError("DE output must declare gene identities, p-values and BH q-values")
        emitted = []
        for row in rows:
            emitted.append(row.get("gene_id"))
            if len(emitted) > 100_000:
                raise ValueError("DE output exceeds the feature-family bound")
            p, q = (float(row[key]) if row[key] else None for key in ("pvalue", "padj"))
            if (any(x is not None and (not math.isfinite(x) or not 0 <= x <= 1) for x in (p, q))
                    or q is not None and (p is None or q < p)):
                raise ValueError("DE p-values and BH q-values must be finite, bounded, and q >= p")
    return emitted


def _verify_products(record, manifest, work_dir):
    """Verify exact identities, full feature family, and the frozen estimand."""
    design = manifest["frozen_preflight"]["design"]
    controls = manifest.get("execution", {})
    salmon = controls.get("execution_profile") == "qc-salmon"
    if (record.get("status") != "completed" or record.get("software_acceptance") is not True
            or record.get("formula") != design["formula"] or record.get("contrast") != design["contrast"]
            or record.get("count_model_execution") != "verified"):
        raise ValueError("FASTQ result did not complete the frozen formula and contrast")
    refs = record.get("artifacts", {})
    if set(refs) != set(_files(controls)) - {"result"}:
        raise ValueError("FASTQ did not emit exactly the declared artifact family")
    paths = _verified_artifact_paths(refs, work_dir)
    provenance = _json(paths["count_model_provenance"], MAX_BYTES)
    if provenance.get("status") != "completed" or provenance.get("design") != design:
        raise ValueError("Count-model provenance differs from frozen design")
    policy = provenance.get("numerical_policy", {})
    if policy.get("analysis_policy") != manifest["frozen_preflight"].get("analysis_policy"):
        raise ValueError("Count-model provenance differs from the frozen optional analysis policy")
    model_artifacts = ("count_matrix", "differential_expression")
    for key in (*model_artifacts, *(_SALMON_FILES if salmon else ())):
        if any(
            provenance.get(key, {}).get(field) != refs[key][field]
            for field in ("sha256", "size_bytes")
        ):
            raise ValueError("Count-model provenance artifact identity mismatch")
    genes = _count_gene_ids(paths["count_matrix"], design["sample_ids"], salmon)
    if salmon:
        _verify_salmon_products(
            record, provenance, controls, refs, paths, genes, design["sample_ids"],
        )
    emitted = _de_gene_ids(paths["differential_expression"])
    family = provenance.get("family", {})
    digest = hashlib.sha256(json.dumps(genes, separators=(",", ":")).encode()).hexdigest()
    if (emitted != genes or family.get("complete") is not True or family.get("gene_ids_sha256") != digest
            or family.get("emitted_gene_count") != len(genes) or family.get("result_row_count") != len(genes)):
        raise ValueError("DE result must retain the complete count-matrix gene family in order")
    return paths


def execute_fastq(contract, inputs, parameters, output_dir, *, execution_authorized=False,
                  cancellation_event=None, execution_observer=None):
    from .fastq_execution import run_fastq_plan
    from .fastq_planner_binding import _execution_options
    from .method_executor import _path
    if execution_authorized is not True:
        raise PermissionError("Trusted FASTQ requires fresh explicit data execution authority")
    if not isinstance(contract, MethodContract) or contract.to_dict()["implementation"]["kind"] != "trusted_fastq":
        raise ValueError("Only exact registered trusted FASTQ contracts use this adapter")
    if set(inputs) != {"manifest"}:
        raise ValueError("Trusted FASTQ requires its sole manifest input")
    contract.validate_parameters(parameters)
    manifest = _json(inputs["manifest"], 5_000_000)
    if fastq_contract(manifest).version_id != contract.version_id:
        raise ValueError("Trusted FASTQ live source, input binding, or frozen contract changed")
    output_dir = _path(output_dir)
    if (not output_dir.is_relative_to(Path(manifest["workspace"]))
            or not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("Trusted FASTQ requires a fresh flat output directory")
    work_dir = output_dir.parent / "fastq-workflow"
    work_dir.mkdir(mode=0o700)
    policy = manifest["execution"]
    kwargs = {"nextflow_executable": policy["nextflow_path"], "timeout_sec": policy["timeout_seconds"],
              "resources": policy["resources"], "execution_profile": policy["execution_profile"],
              "expected_nextflow_sha256": policy["nextflow_sha256"],
              **_execution_options(policy)}
    if cancellation_event is not None:
        kwargs["cancellation_event"] = cancellation_event
    if execution_observer is not None:
        kwargs["execution_observer"] = execution_observer
    summary = run_fastq_plan(manifest["source_plan"], work_dir, **kwargs)
    receipt = _path(summary["result_manifest"])
    if not receipt.is_relative_to(work_dir):
        raise ValueError("FASTQ result receipt escaped its retained workflow directory")
    record = _json(receipt, MAX_BYTES)
    if record.get("status") != summary.get("status"):
        raise ValueError("FASTQ result status differs from retained receipt")
    result = {"exit_code": 1, "runtime": "trusted_fastq", "outcome": record.get("status"),
              "workflow_result": summary, "retention": manifest["retention"]}
    for key in ("engine_launch_count", "engine_launch_attempt_count", "engine_process", "container_cleanup"):
        result[key] = record.get(key)
    attempted = bool(record.get("engine_launch_count") or record.get("engine_launch_attempt_count"))
    engine = record.get("engine_process") or {}
    cleanup = record.get("container_cleanup") or {}
    engine_exited = type(engine.get("exit_code")) is int and not engine.get("termination_errors")
    result["cleanup_required"] = (record.get("status") != "completed" and attempted
                                  and (not engine_exited or cleanup.get("status") != "complete"))
    result["cleanup_required"] |= record.get("salmon_model_cleanup_required") is True
    result["runtime_uncertain"] = result["cleanup_required"]
    if record.get("status") != "completed":
        result["error"] = record.get("reason", "FASTQ workflow did not complete")
        return result
    try:
        paths = _verify_products(record, manifest, work_dir)
        if cancellation_event is not None and cancellation_event.is_set():
            raise ValueError("Cancelled FASTQ execution cannot publish outputs")
        # Revalidate external bytes immediately before importing any references.
        if fastq_contract(manifest).version_id != contract.version_id:
            raise ValueError("FASTQ input or trusted source identity changed during execution")
        files = _files(policy)
        for name, path in paths.items():
            target = output_dir / files[name]
            with target.open("xb") as stream, path.open("rb") as source:
                shutil.copyfileobj(source, stream)
            if hash_artifact(target)["sha256"] != record["artifacts"][name]["sha256"]:
                raise ValueError("FASTQ output changed while publishing")
        (output_dir / FILES["result"]).write_bytes(canonical_json(record))
        if sum(path.stat().st_size for path in output_dir.iterdir()) > contract.to_dict()["execution"]["max_output_bytes"]:
            raise ValueError("FASTQ outputs exceed the cumulative method byte bound")
        if cancellation_event is not None and cancellation_event.is_set():
            raise ValueError("Cancelled FASTQ execution cannot publish outputs")
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        return result
    return {**result, "exit_code": 0}
