"""Execute the exact frozen graph on the durable daemon worker."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import digest

from .context import ControlPlane
from .receipts import publish
from .script_runtime import inspect_isolation as inspect_isolation
from .script_runtime import run_script

_MAX_FILE = 64 * 1024**2
_FILE_NAME = re.compile(r"[a-zA-Z0-9][A-Za-z0-9_.-]{0,127}\Z")
_KINDS = {
    "png": "figure",
    "svg": "figure",
    "json": "table",
    "parquet": "table",
    "csv": "table",
    "tsv": "table",
    "h5ad": "expression_matrix",
    "zip": "replay_package",
}


def _declarations(expected, root):
    result = {}
    for value in expected:
        item = {"path": value} if isinstance(value, str) else dict(value)
        name = item.get("path", item.get("filename", item.get("name")))
        if not isinstance(name, str) or not _FILE_NAME.fullmatch(name):
            raise OperationBlocked("invalid_request")
        port = item.get("port", Path(name).stem)
        if (
            not isinstance(port, str)
            or not _FILE_NAME.fullmatch(port)
            or port in result
        ):
            raise OperationBlocked("invalid_request")
        fmt = item.get("format", Path(name).suffix[1:].lower())
        kind = item.get("kind", _KINDS.get(fmt))
        if kind is None:
            raise OperationBlocked("invalid_request")
        result[port] = {
            **item,
            "path": root / name,
            "kind": kind,
            "format": fmt,
            "representation_class": item.get("representation_class", "derived_summary"),
        }
    return result


def _script(ctx, jobs, rid, plan, bindings, directory):
    outputs = run_script(ctx, jobs, rid, plan, bindings, directory)
    files = _declarations(plan["exploratory_code"]["expected_outputs"], outputs)
    if {x["path"] for x in files.values()} != set(outputs.iterdir()):
        raise OperationBlocked("scientific_refusal")
    return {
        "metrics": {},
        "tables": [],
        "files": files,
        "limitations": ["Exploratory script outputs carry execution evidence only."],
    }


def _matrix(ctx, jobs, rid, plan, bindings, directory):
    if len(bindings) != 1 or not set(bindings) <= {"expression", "matrix", "counts"}:
        raise OperationBlocked("invalid_request")
    source = Path(__file__).with_name("matrix_audit.py").read_text()
    if (
        hashlib.sha256(source.encode()).hexdigest()
        != plan["runtime_pin"]["script_sha256"]
    ):
        raise OperationBlocked("plan_identity_mismatch")
    image = plan["runtime_pin"].get("oci_image_digest")
    if not image:
        raise OperationBlocked("runtime_prerequisite_missing")
    script = {
        **plan,
        "_curated_diagnostic": True,
        "script_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "exploratory_code": {
            "source_code": source,
            "language": "python",
            "base_image_id": image,
            "expected_outputs": ["summary.json"],
        },
    }
    out = run_script(ctx, jobs, rid, script, bindings, directory) / "summary.json"
    metrics = json.loads(read_bounded_file(out, 16384))
    if set(metrics) != {"genes", "observations"} or not all(
        type(n) is int and n >= 0 for n in metrics.values()
    ):
        raise OperationBlocked("scientific_refusal")
    return {
        "metrics": metrics,
        "tables": [],
        "files": _declarations(["summary.json"], out.parent),
        "limitations": [
            "Biological replication requires evidence beyond matrix dimensions."
        ],
    }


def _execute_unit(ctx, jobs, rid, plan, bindings, directory):
    if plan["plan_kind"] == "tier2_exploratory_code":
        return _script(ctx, jobs, rid, plan, bindings, directory)
    if plan["kernel"]["kernel_id"] == "matrix_audit":
        return _matrix(ctx, jobs, rid, plan, bindings, directory)
    from .registry import execute_kernel

    return execute_kernel(jobs, rid, plan, bindings, directory, ctx=ctx)


def _units(plan):
    steps = [plan, *list(plan.get("graph_steps") or ())]
    variants = (plan.get("sweep_spec") or {}).get("parameter_sets", [{}])
    for i, params in enumerate(variants):
        for step in steps:
            key = step.get("step_key", "root")
            if not _FILE_NAME.fullmatch(key):
                raise OperationBlocked("invalid_request")
            unit = {**plan, **step}
            code = step.get("exploratory_code")
            if code:
                unit["exploratory_code"] = {
                    **code,
                    "parameters": {**code.get("parameters", {}), **params},
                }
                unit["script_sha256"] = hashlib.sha256(
                    code["source_code"].encode()
                ).hexdigest()
            else:
                kernel = unit["kernel"]
                unit["kernel"] = {
                    **kernel,
                    "parameters": {**kernel["parameters"], **params},
                }
                unit["runtime_pin"] = plan["runtime_pin"]["steps"][key]
            yield f"{i}-{key}", unit


def _collect(directory, files, prefix):
    from .schemas import ARTIFACT, StrictValidator

    result = {}
    for port, spec in files.items():
        if not _FILE_NAME.fullmatch(port):
            raise OperationBlocked("invalid_request")
        path = Path(spec["path"])
        if not path.is_relative_to(directory) or path.resolve(strict=True) != path:
            raise OperationBlocked("input_scope_violation")
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > _MAX_FILE
        ):
            raise OperationBlocked("resource_quota_exceeded")
        sha = hash_bounded_file(path, _MAX_FILE)
        ref = {
            "artifact_id": "a-" + sha,
            "sha256": sha,
            "size_bytes": info.st_size,
            "kind": spec["kind"],
            "format": spec["format"],
            "representation_class": spec.get("representation_class", "derived_summary"),
            "claim_tier": "L1_execution_verified",
        }
        if not StrictValidator(ARTIFACT).is_valid(ref):
            raise OperationBlocked("invalid_request")
        if spec["kind"] == "plot_sidecar":
            from .plot_sidecars import verify_plot_sidecar

            verify_plot_sidecar(path, spec["format"])
        path.chmod(0o400)
        if spec["kind"] == "figure":
            side = spec.get("sidecar_port", port + "_sidecar")
            if side not in files or files[side]["kind"] != "plot_sidecar":
                raise OperationBlocked("scientific_refusal")
            spec = {**spec, "sidecar_port": prefix + side}
        result[prefix + port] = ref, path, spec
    return result


def _bind(ctx, plan):
    records = {
        port: ctx.artifact(plan["session_id"], ref["artifact_id"], ref["sha256"])
        for port, ref in plan["input_bindings"].items()
    }
    from rna_kernels.m2_contract import KERNEL_AXES

    from .registry_m2 import validate_bindings

    purpose = plan["epistemic_purpose"]
    if purpose == "confirmatory" and any(
        r["detail"].get("donor_conflict_halted")
        or r["ref"]["representation_class"] == "ambient_adjusted_counts"
        for r in records.values()
    ):
        raise OperationBlocked("scientific_refusal")
    kernel = plan.get("kernel") or {}
    from .species_scope import require_within_species

    require_within_species(kernel.get("kernel_id"), records)
    if kernel.get("kernel_id") in KERNEL_AXES:
        validate_bindings(kernel["kernel_id"], records, kernel["parameters"], purpose)
        return records
    required = set(plan.get("runtime_pin", {}).get("required_representations", ()))
    count_families = {
        "count_1_raw_counts",
        "aggregated_unit_counts",
        "metadata_only",
        "reference_only",
    }
    if (
        required
        and required <= count_families
        and any(
            r["ref"]["kind"] == "expression_matrix"
            and not r["detail"].get("count_1_verified")
            for r in records.values()
        )
    ):
        raise OperationBlocked("scientific_refusal")
    return records


def _save_unit(directory, name, unit, current):
    from devbench.platform.plugin_jobs import canonical

    evidence = {**current, "unit_sha256": digest(unit), "files": {}}
    for port, spec in current["files"].items():
        path = Path(spec["path"])
        evidence["files"][port] = {
            **spec,
            "path": str(path.relative_to(directory)),
            "sha256": hash_bounded_file(path, _MAX_FILE),
        }
    raw = canonical(evidence)
    with (directory / "outcome.json").open("xb") as handle:
        handle.write(raw)
    (directory / "outcome.json").chmod(0o400)
    return {"key": name, "sha256": hashlib.sha256(raw).hexdigest()}


def _resume_unit(ctx, bindings, source, directory, unit, saved):
    from .intake import verify_artifact_identity

    raw = read_bounded_file(source / "outcome.json", 4 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != saved["sha256"]:
        raise OperationBlocked("plan_identity_mismatch")
    current = json.loads(raw)
    if current.pop("unit_sha256") != digest(unit):
        raise OperationBlocked("plan_identity_mismatch")
    if (unit.get("kernel") or {}).get("kernel_id") == "matrix_audit":
        driver = Path(__file__).with_name("matrix_audit.py")
        if (
            hash_bounded_file(driver, 512 * 1024)
            != unit["runtime_pin"]["script_sha256"]
        ):
            raise OperationBlocked("plan_identity_mismatch")
    for record in bindings.values():
        verify_artifact_identity(
            ctx, record, until=float("inf"), allow_private=ctx.offline_only
        )
    for port, spec in current["files"].items():
        old = source / spec["path"]
        if old.resolve(strict=True) != old or not old.is_relative_to(source):
            raise OperationBlocked("input_scope_violation")
        if hash_bounded_file(old, _MAX_FILE) != spec["sha256"]:
            raise OperationBlocked("plan_identity_mismatch")
        target = directory / ("verified-" + port)
        shutil.copyfile(old, target)
        if hash_bounded_file(target, _MAX_FILE) != spec.pop("sha256"):
            raise OperationBlocked("plan_identity_mismatch")
        spec["path"] = target
    return current


def _execute_graph(ctx, jobs, rid, plan, root):
    checkpoint = jobs.resume_checkpoint(rid)
    prior = checkpoint["state"]["completed_units"] if checkpoint else []
    completed, files = [], {}
    result = {"metrics": {}, "tables": [], "limitations": []}
    variants = list(_units(plan))
    if [entry["key"] for entry in prior] != [
        name for name, _ in variants[: len(prior)]
    ]:
        raise OperationBlocked("plan_identity_mismatch")
    for index, (name, unit) in enumerate(variants):
        directory = root / name
        directory.mkdir(mode=0o700)
        bindings = _bind(ctx, unit)
        jobs.stage(rid, step="executing", completed=index, total=len(variants))
        if index < len(prior):
            source = root.parents[1] / checkpoint["run_id"] / "control-plane" / name
            current = _resume_unit(ctx, bindings, source, directory, unit, prior[index])
        else:
            current = _execute_unit(ctx, jobs, rid, unit, bindings, directory)
        prefix = "" if len(variants) == 1 else name + "."
        files.update(_collect(directory, current["files"], prefix))
        result["metrics"].update({prefix + k: v for k, v in current["metrics"].items()})
        result["tables"].extend(
            {**t, "rows": t["rows"][:20]} for t in current["tables"]
        )
        result["limitations"].extend(current["limitations"])
        completed.append(_save_unit(directory, name, unit, current))
        jobs.checkpoint(
            rid,
            step="completed_units",
            state={
                "worker_protocol": "devbench.control-worker/1",
                "phase": "completed_units",
                "plan_sha256": digest(plan),
                "completed_units": completed,
            },
        )
    return files, result


def execute(jobs, rid, plan):
    stage = json.loads(jobs.managed_run(rid)["stage_json"])
    config = {
        "offline_only": stage.get("_offline_only") is True,
        "allow_external_input_files": stage.get("_allow_external_input_files") is True
        and plan.get("allow_external_input_files") is True,
    }
    ctx = ControlPlane(
        SimpleNamespace(jobs=jobs, input_root=Path(plan["input_root"])), config=config
    )
    root = jobs.workspace / "runs" / rid / "control-plane"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    files, result = _execute_graph(ctx, jobs, rid, plan, root)
    result["limitations"] = list(dict.fromkeys(result["limitations"]))
    row = {
        "session_id": plan["session_id"],
        "run_id": rid,
        "status": "completed",
        "plan_id": plan["plan_id"],
        "plan_sha256": digest(plan),
        "plan": plan,
    }
    with jobs.worker_admission(rid) as (db, sealed):
        if digest(sealed) != row["plan_sha256"]:
            raise OperationBlocked("plan_identity_mismatch")
        return publish(ctx, db, row, files, result, root)
