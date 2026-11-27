"""Plan, execute and serve a private, receipt-bound shared motor-neuron UMAP."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from uuid import uuid4

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.analysis.workbench_inputs import InputDiscovery
from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import (
    ACTIVE,
    JobStore,
    _orphaned_worker,
    _worker_alive,
    digest,
)

from .code import SOURCE_LIMIT, code_view, implementation_path, read_code

if TYPE_CHECKING:
    from devbench.platform.plugin_mcp import Plugin

    from .models import _SharedMotorUMAPPlan

_OPERATION = "shared_motor_umap"
_FORMAT = "devbench.shared-motor-umap/1"
_PACKAGES = (
    "anndata",
    "h5py",
    "matplotlib",
    "numpy",
    "pandas",
    "scikit-learn",
    "scipy",
    "umap-learn",
)
_ARTIFACTS = {
    "umap": ("shared_motor_umap.png", "image/png", 8 * 1024**2),
    "markers": ("motor_state_markers.png", "image/png", 8 * 1024**2),
    "coordinates": ("coordinates.csv", "text/csv", 32 * 1024**2),
    "composition": ("composition.csv", "text/csv", 1024**2),
    "parameters": ("parameters.json", "application/json", 1024**2),
}
_LIMITATIONS = (
    "Counts describe captured cells with the input motor-neuron annotations.",
    "Stage and capture can influence the map. Embryo identities are unverified.",
    "The map cannot establish lineage transitions or embryo-level abundance changes.",
)


def _runtime():
    try:
        versions = {
            package: importlib.metadata.version(package) for package in _PACKAGES
        }
    except importlib.metadata.PackageNotFoundError as error:
        raise OperationBlocked("shared_motor_umap_runtime_unavailable") from error
    return {
        "compute_target": "local_worker",
        "python": platform.python_version(),
        "packages": versions,
        "implementation_sha256": hash_bounded_file(implementation_path(), SOURCE_LIMIT),
        "network_policy": "offline",
    }


def _require_runtime(pinned):
    current = _runtime()
    # Plans issued before AnnData was added to the package inventory still pin
    # the exact kernel bytes and every package in the original inventory.
    legacy = {
        **current,
        "packages": {
            key: version
            for key, version in current["packages"].items()
            if key != "anndata"
        },
    }
    if pinned not in (current, legacy):
        raise OperationBlocked("shared_motor_umap_runtime_changed")
    return current


def plan_shared_motor_umap(plugin: Plugin, args: _SharedMotorUMAPPlan):
    from devbench.platform.plugin_graph import registered_plugin_runtime
    from devbench.platform.plugin_mcp import _admit_session

    if any(item.role != "expression" for item in args.inputs):
        raise OperationBlocked("shared_motor_umap_requires_three_expression_inputs")
    profiles, previous = plugin._plan_inputs(args, private_only=True)
    parameters = args.parameters.model_dump()
    for profile in profiles:
        observed = profile["observed"]
        if observed["file_type"] != "h5ad":
            raise OperationBlocked("shared_motor_umap_requires_three_h5ad_inputs")
        columns = set(observed["obs_columns"])
        if not {parameters["stage_column"], parameters["label_column"]} <= columns:
            raise OperationBlocked("shared_motor_umap_missing_observation_columns")
        if parameters["counts_layer"] not in observed["layers"]:
            raise OperationBlocked("shared_motor_umap_missing_counts_layer")
    if len({profile["sha256"] for profile in profiles}) != 3:
        raise OperationBlocked("shared_motor_umap_requires_distinct_input_contents")
    plan = {
        "format": "devbench.plugin-plan/1",
        "plan_id": uuid4().hex,
        "operation": _OPERATION,
        "objective": args.objective,
        "input_root": str(plugin.input_root),
        "allow_external_input_files": plugin.discovery.allow_external_files,
        "data_classification": "private",
        "profiles": profiles,
        "input_declarations": [item.model_dump(mode="json") for item in args.inputs],
        "design": {"parameters": parameters},
        "runtime": _runtime(),
        "mounted_image_id": None,
    }
    plugin._retain_plan_scope(plan, args, previous)
    plan["graph_runtime"] = registered_plugin_runtime(plan)
    session_id = args.session_id or _admit_session(
        plugin.jobs.workspace, plan, plugin.discovery
    )
    plan["session_id"] = session_id
    plan_id, plan_sha = plugin.jobs.freeze(plan)
    return {
        "status": "local_studio_only",
        "execution_route": "offline_studio",
        "session_id": session_id,
        "plan_id": plan_id,
        "plan_sha256": plan_sha,
        **plugin._handoff(session_id, "workflows", allow_data_execution=True),
    }


def _check_scope(workspace, plan):
    from devbench.platform.plugin_mcp import _ledger, _lineage, _roots

    if plan["operation"] != _OPERATION or plan["data_classification"] != "private":
        raise OperationBlocked("shared_motor_umap_wrong_plan")
    session_id = plan["session_id"]
    broker = EvidenceBroker.open_existing(_ledger(workspace).scientific)
    lineage = _lineage(session_id, plan)
    broker.check_delivery(
        lineage["context_id"],
        lineage["source_id"],
        lineage_id=session_id,
        role="scientist",
    )
    broker.authority.ledger._read(
        lambda db: broker._query_dependencies(db, _roots(plan))
    )


def run_shared_motor_umap_plan(jobs: JobStore, run_id: str, plan: dict):
    """Run the reviewed offline action in the normal Plugin worker."""
    from devbench.platform.plugin_mcp import Input, _profile

    runtime = _require_runtime(plan["runtime"])
    root = Path(plan["input_root"])
    reader = InputDiscovery(
        [root],
        allow_external_files=plan["allow_external_input_files"],
        timeout_seconds=60,
    )

    def verify():
        _check_scope(jobs.workspace, plan)
        for declaration, frozen in zip(
            plan["input_declarations"], plan["profiles"], strict=True
        ):
            item = Input.model_validate(declaration).model_copy(
                update={"data_classification": "private"}
            )
            current = _profile(reader, root, item)
            if (current["sha256"], current["size_bytes"]) != (
                frozen["sha256"],
                frozen["size_bytes"],
            ):
                raise OperationBlocked("input_snapshot_changed")

    jobs.stage(run_id, step="verifying_inputs")
    verify()
    run_root = jobs.workspace / "runs" / run_id
    run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    e09, e10, e11 = (Path(item["source"]) for item in plan["profiles"])
    with TemporaryDirectory(dir=run_root, prefix=".motor-") as temporary:
        os.environ["MPLCONFIGDIR"] = str(Path(temporary) / "matplotlib")
        from devbench.analysis.shared_motor_umap import run_shared_motor_umap

        result_dir = Path(temporary) / "result"
        result_dir.mkdir(mode=0o700)
        jobs.stage(run_id, step="fitting_shared_umap")
        result = run_shared_motor_umap(
            (e09, e10, e11), result_dir, **plan["design"]["parameters"]
        )
        jobs.stage(run_id, step="verifying_outputs")
        verify()
        expected = {kind: spec[0] for kind, spec in _ARTIFACTS.items()}
        if result["artifacts"] != expected:
            raise OperationBlocked("shared_motor_umap_artifact_contract_mismatch")
        artifacts = {
            kind: hash_bounded_file(result_dir / filename, size)
            for kind, (filename, _media, size) in _ARTIFACTS.items()
        }
        receipt = {
            "format": _FORMAT,
            "operation": _OPERATION,
            "session_id": plan["session_id"],
            "plan_id": plan["plan_id"],
            "plan_sha256": digest(plan),
            "run_id": run_id,
            "runtime": runtime,
            "cells": result["cells"],
            "features": result["features"],
            "stages": result["stages"],
            "artifacts": artifacts,
            "clinical_release_allowed": False,
        }
        result_dir.rename(run_root / "shared-motor-umap")
    return {**receipt, "receipt_sha256": digest(receipt)}


def _studio_record(app, session_id):
    from devbench.platform.plugin_mcp import studio_job_readback

    if app.offline_only is not True:
        return None
    with studio_job_readback(app.workspace) as db:
        if db is None:
            return None
        row = db.execute(
            "SELECT * FROM plugin_plans WHERE session_id=? "
            "AND json_extract(plan_json, '$.operation') = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id, _OPERATION),
        ).fetchone()
        if row is None:
            return None
        plan = JobStore._plan(row)
        if plan["operation"] != _OPERATION:
            return None
        run = db.execute(
            "SELECT * FROM plugin_runs WHERE session_id=? AND plan_id=?",
            (session_id, plan["plan_id"]),
        ).fetchone()
    _check_scope(app.workspace, plan)
    return plan, row["plan_sha256"], run


def _studio_receipt(workspace, plan, plan_sha, run):
    if run is None or run["status"] != "completed" or not run["receipt_json"]:
        raise OperationBlocked("shared_motor_umap_result_unavailable")
    receipt = json.loads(run["receipt_json"])
    receipt_sha = receipt.pop("receipt_sha256", None)
    if (
        digest(receipt) != receipt_sha
        or receipt.get("format") != _FORMAT
        or receipt.get("operation") != _OPERATION
        or receipt.get("session_id") != plan["session_id"]
        or receipt.get("plan_id") != plan["plan_id"]
        or receipt.get("plan_sha256") != plan_sha
        or run["plan_sha256"] != plan_sha
        or receipt.get("run_id") != run["run_id"]
    ):
        raise OperationBlocked("shared_motor_umap_receipt_identity_mismatch")
    if plan.get("graph_runtime") is not None:
        from devbench.platform.plugin_graph import verify_registered_plugin_result

        verify_registered_plugin_result(workspace, run["run_id"], plan, receipt)
    return receipt


def studio_view(app, session_id: str):
    record = _studio_record(app, session_id)
    if record is None:
        return None
    plan, plan_sha, run = record
    view = {
        "plan_id": plan["plan_id"],
        "plan_sha256": plan_sha,
        "can_run": app.allow_data_execution is True and run is None,
        "status": run["status"] if run else "awaiting_review",
        "inputs": [
            {"name": Path(item["source"]).name, "cells": item["observed"]["cells"]}
            for item in plan["profiles"]
        ],
        "parameters": plan["design"]["parameters"],
        "limitations": list(_LIMITATIONS),
    }
    if run is None:
        if plan.get("dependencies"):
            from devbench.platform.plugin_graph import require_plugin_dependencies

            try:
                require_plugin_dependencies(app.workspace, plan)
            except OperationBlocked as error:
                view.update(can_run=False, error=error.state)
        return view
    stage = json.loads(run["stage_json"])
    view.update(
        run_id=run["run_id"], progress=stage.get("step"), error=run["error_text"]
    )
    if run["status"] in ACTIVE:
        created = stage.get("process_created_at")
        interrupted = (
            run["status"] == "running"
            and _orphaned_worker(app.workspace, run["pid"], created)
            if stage.get("daemon_managed")
            else not _worker_alive(run["pid"], created)
        )
        if interrupted:
            view.update(status="interrupted", error="worker_not_running")
    if run["status"] == "completed":
        receipt = _studio_receipt(app.workspace, plan, plan_sha, run)
        if "graph_execution" in receipt:
            view["graph_execution"] = receipt["graph_execution"]
        base = f"/api/scientific/adaptive/{session_id}/motor-umap"
        view.update(
            cells=receipt["cells"],
            features=receipt["features"],
            stages=receipt["stages"],
            code=code_view(plan, receipt, base),
            artifacts={
                kind: {"url": f"{base}/{kind}/{identity}", "sha256": identity}
                for kind, identity in receipt["artifacts"].items()
            },
        )
        try:
            settings = json.loads(_read_studio_artifact(app, receipt, "parameters")[0])
        except (OSError, ValueError, KeyError, OperationBlocked):
            return view
        view["method"] = {
            key: settings[key]
            for key in (
                "normalization",
                "common_symbols",
                "hvg_source",
                "min_fraction_detected",
                "pca_dimensions",
                "pca_explained_variance_fraction",
                "pca_gene_sd_floor",
                "umap",
                "population",
                "marker_genes_held_out",
                "marker_genes_unavailable",
                "limitations",
            )
            if key in settings
        }
    return view


def studio_launch(app, session_id: str, body: dict):
    if app.offline_only is not True or app.allow_data_execution is not True:
        raise PermissionError("Shared motor UMAP requires offline Studio execution")
    if (
        type(body) is not dict
        or body.keys() != {"plan_id", "plan_sha256", "allow_data_execution"}
        or body["allow_data_execution"] is not True
    ):
        raise PermissionError("Review and authorize the exact shared motor UMAP plan")
    record = _studio_record(app, session_id)
    if record is None:
        raise KeyError("Unknown shared motor UMAP plan")
    plan, plan_sha, _run = record
    if body["plan_id"] != plan["plan_id"] or body["plan_sha256"] != plan_sha:
        raise OperationBlocked("plan_identity_mismatch")
    _require_runtime(plan["runtime"])
    jobs = JobStore(app.workspace)
    if plan.get("dependencies"):
        from devbench.platform.plugin_graph import require_plugin_dependencies

        require_plugin_dependencies(jobs, plan)
    jobs.launch(session_id, plan["plan_id"], plan_sha)
    return studio_view(app, session_id)


def studio_artifact(app, session_id: str, kind: str, identity: str):
    if app.offline_only is not True:
        raise PermissionError("Shared motor UMAP artifacts require offline Studio")
    record = _studio_record(app, session_id)
    if record is None or kind not in _ARTIFACTS:
        raise KeyError("Unknown shared motor UMAP artifact")
    return _verified_artifact(app, record, kind, identity)


def studio_graph_artifact(app, session_id: str, kind: str, identity: str):
    """Read the exact current graph artifact for offline Studio presentation."""
    if app.offline_only is not True:
        raise PermissionError("Shared motor UMAP artifacts require offline Studio")
    record = _studio_record(app, session_id)
    if record is None or record[0].get("graph_runtime") is None:
        return None
    if kind not in _ARTIFACTS:
        raise KeyError("Unknown shared motor UMAP artifact")
    return _verified_artifact(app, record, kind, identity)


def _verified_artifact(app, record, kind, identity):
    receipt = _studio_receipt(app.workspace, *record)
    if (
        re.fullmatch(r"[a-f0-9]{64}", identity) is None
        or receipt["artifacts"].get(kind) != identity
    ):
        raise KeyError("Unknown shared motor UMAP artifact")
    return _read_studio_artifact(app, receipt, kind)


def studio_code_artifact(app, session_id: str, kind: str, identity: str):
    if app.offline_only is not True:
        raise PermissionError("Shared motor UMAP code requires offline Studio")
    record = _studio_record(app, session_id)
    if record is None or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
        raise KeyError("Unknown shared motor UMAP code artifact")
    receipt = _studio_receipt(app.workspace, *record)
    content, filename = read_code(record[0], receipt, kind)
    if sha256(content).hexdigest() != identity:
        raise KeyError("Unknown shared motor UMAP code artifact")
    return content, "text/plain", filename


def _read_studio_artifact(app, receipt, kind):
    filename, media_type, limit = _ARTIFACTS[kind]
    path = app._path("runs", receipt["run_id"], "shared-motor-umap", filename)
    content = read_bounded_file(path, limit)
    if sha256(content).hexdigest() != receipt["artifacts"][kind]:
        raise OperationBlocked("shared_motor_umap_artifact_digest_mismatch")
    if media_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise OperationBlocked("shared_motor_umap_invalid_image")
    return content, media_type, filename
