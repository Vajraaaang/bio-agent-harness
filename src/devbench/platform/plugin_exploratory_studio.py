"""Local authoring and schema-blind template binding in an offline Studio."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.core._files import read_bounded_file
from devbench.core.input_files import _open_file
from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.methods import MethodService, exploratory_method
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict

from .adaptive_workflow import ArtifactVersion
from .plugin_jobs import JobStore, canonical, digest
from .plugin_registered_method import OPERATION, freeze_registered_method

_SCRIPT_LIMIT = 512 * 1024
_PRIVATE = {
    "private",
    "clinical",
    "private_clinical",
    "controlled_access",
    "unclassified",
}


def _script(workspace, body):
    options = body.keys() & {"source_code", "script_path"}
    if len(options) != 1:
        raise ValueError("Supply exactly one script source")
    if "source_code" in options:
        code = body["source_code"].encode("utf-8")
    else:
        relative = Path(body["script_path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.lower() not in {".py", ".r"}
        ):
            raise ValueError("Select a Python or R script inside this workspace")
        if "evaluation" in relative.parts:
            raise OperationBlocked("holdout_input_forbidden")
        root = workspace.stat()
        with _open_file(workspace, relative.parts, (root.st_dev, root.st_ino)) as fd:
            with os.fdopen(os.dup(fd), "rb") as stream:
                code = stream.read(_SCRIPT_LIMIT + 1)
    if not 0 < len(code) <= _SCRIPT_LIMIT:
        raise ValueError("Exploratory source must be between 1 byte and 512 KiB")
    code.decode("utf-8", errors="strict")
    return code


def _local_method(workspace, body, ports, *, private_plan=None):
    from devbench.daemon.auth import local_actor

    image = exploratory_image(body["base_image_id"])
    if image.legacy:
        raise ValueError("Select a scientific Python or R base image")
    source = _script(workspace, body)
    source_sha = hashlib.sha256(source).hexdigest()
    lock = canonical(
        {"image_id": image.image_id, "platform": "linux/" + image.architecture}
    )
    limits = body["resource_profile"]
    execution = {
        "cpu": limits["cpus"],
        "pids": limits["pids_limit"],
        "memory_mb": limits["memory_mb"],
        "timeout_seconds": limits["timeout_sec"],
        "max_output_bytes": limits["max_output_bytes"],
        "network": "deny",
    }
    if "scratch_mb" in limits:
        execution["scratch_mb"] = limits["scratch_mb"]
    if limits.keys() - {
        "cpus",
        "pids_limit",
        "memory_mb",
        "timeout_sec",
        "max_output_bytes",
        "scratch_mb",
    }:
        raise ValueError("Unknown exploratory resource limit")
    filename = "main.py" if image.language == "python" else "main.R"
    entrypoint = (
        [image.executable, "/method/main.py"]
        if image.language == "python"
        else [image.executable, "--vanilla", "/method/main.R"]
    )
    contract = {
        "format": "devharness.method/1",
        "method_id": "local." + source_sha,
        "name": body["step_name"],
        "lifecycle": "custom",
        "implementation_trust": "unreviewed",
        "implementation": {
            "kind": "oci",
            "source_sha256": source_sha,
            "entrypoint": entrypoint,
        },
        "environment": {
            "image": image.reference,
            "lock_sha256": hashlib.sha256(lock).hexdigest(),
        },
        "execution": execution,
        "inputs": {name: {"artifact_type": "file", "required": True} for name in ports},
        "outputs": body["expected_outputs"],
        "parameters": body.get(
            "parameter_schema",
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        "provenance": {"origin": "offline_local_authoring"},
        "validation": {
            "comparator": "file",
            "invariants": [],
            "counts_layer": "X",
            "qc_metrics": [],
        },
    }
    exploratory_method(contract)
    if private_plan is not None:
        from .plugin_mcp import _roots

        sid = private_plan["session_id"]
        broker = EvidenceBroker.open_existing(JobStore(workspace).authority)
        for sha in {source_sha, digest(contract)}:
            # Record custody before a globally content-addressed object can exist.
            broker.register_source(
                "offline-script-" + digest([sid, sha]),
                content_sha256=sha,
                roots=_roots(private_plan),
                parents=("plugin-session-" + sid,),
                classification="structural",
                tags=("private",),
            )
    methods = MethodService(workspace / "methods", reconcile=False)
    with TemporaryDirectory(dir=workspace) as temporary:
        script_path, lock_path = (
            Path(temporary) / filename,
            Path(temporary) / "image.lock",
        )
        script_path.write_bytes(source)
        lock_path.write_bytes(lock)
        return methods.register_method(
            contract,
            source=script_path,
            environment_lock=lock_path,
            actor=local_actor(),
        )


def _tested_template(workspace, handoff):
    from .plugin_mcp import studio_job_readback
    from .plugin_registered_method import verify_registered_method_result

    keys = {"plan_id", "plan_sha256"}
    if not isinstance(handoff, dict) or not keys <= handoff.keys() <= (
        keys | {"studio_url"}
    ):
        raise ValueError("A template handoff requires its exact plan ID and digest")
    # A handoff URL locates Studio for the operator. Resolve the template by
    # its content identity in the current workspace, without dereferencing it.
    handoff = {key: handoff[key] for key in keys}
    with studio_job_readback(workspace) as db:
        row = db.execute(
            "SELECT * FROM plugin_plans WHERE plan_id=?", (handoff["plan_id"],)
        ).fetchone()
        plan = JobStore._plan(row, handoff["plan_id"], handoff["plan_sha256"])
        if plan["operation"] != OPERATION or JobStore.protected_inputs(db, plan):
            raise OperationBlocked("public_tested_template_required")
        run = db.execute(
            "SELECT * FROM plugin_runs WHERE session_id=? AND plan_id=? "
            "AND plan_sha256=? AND status='completed'",
            (plan["session_id"], plan["plan_id"], handoff["plan_sha256"]),
        ).fetchone()
        if run is None:
            raise OperationBlocked("public_tested_template_required")
    graph = verify_registered_method_result(
        workspace, run["run_id"], plan, json.loads(run["receipt_json"])
    )
    version = plan["registered_method"]["step"]["oci_method_version_id"]
    methods = MethodService(workspace / "methods", reconcile=False, readonly=True)
    return methods.method(version), {
        **handoff,
        "test_receipt_sha256": graph["receipt_sha256"],
    }


def _bind_inputs(workspace, previous, method, bindings, *, offline_studio=False):
    from .plugin_registered_method import _context

    if not isinstance(bindings, dict) or not 1 <= len(bindings) <= 16:
        raise ValueError("Select between one and sixteen retained inputs")
    contract = method["contract"]
    inputs = contract["inputs"]
    if (
        bindings.keys() - inputs.keys()
        or not {n for n, s in inputs.items() if s["required"]} <= bindings.keys()
    ):
        raise OperationBlocked("registered_method_input_ports_mismatch")
    flow, executor = _context(workspace, previous["session_id"], {})
    restricted = previous["data_classification"] in _PRIVATE
    if restricted and not offline_studio:
        raise OperationBlocked("private_data_local_studio_only")
    executor.private = restricted
    retained = {p["sha256"]: p for p in previous["profiles"]}
    bound = {}
    for name, sha in bindings.items():
        if not isinstance(sha, str) or sha not in retained:
            raise OperationBlocked("registered_method_input_unavailable")
        profile = retained[sha]
        path = Path(profile["source"])
        rep = executor.admit_input(
            path, expected_sha256=sha, artifact_type=inputs[name]["artifact_type"]
        )
        # The intake attests to storage. Physical unit and COUNT-1 lineage are unknown.
        artifact = ArtifactVersion(
            version_id="local-input-" + digest([sha, rep]),
            sha256=sha,
            kind="table",
            representation=rep,
            biological_unit="unspecified",
            unit_membership_sha256=digest(
                {"unit_membership": "unknown", "input_sha256": sha}
            ),
            permitted_uses=("exploratory",),
            data_classification="private" if restricted else None,
        )
        try:
            flow.register_artifact(artifact)
        except OperationConflict:
            prior = flow._read_projection(
                lambda db, key=artifact.version_id: flow._state(db).artifacts.get(key)
            )
            if prior is None or ArtifactVersion.model_validate(prior[0]) != artifact:
                raise
        bound[name] = artifact.version_id
    return bound


def studio_freeze_exploration(app, session_id, body):
    """Freeze local inputs and exact script bytes. Launch needs a separate approval."""
    from .plugin_graph_studio import _check_scope
    from .plugin_mcp import _lineage, _roots

    if app.offline_only is not True or not isinstance(body, dict):
        raise PermissionError("Private authoring requires offline Studio")
    shared = {"input_bindings", "parameters", "step_name"}
    fields = (
        {"template"}
        if "template" in body
        else {
            "source_code",
            "script_path",
            "base_image_id",
            "expected_outputs",
            "resource_profile",
            "parameter_schema",
        }
    )
    if body.keys() - shared - fields or not shared <= body.keys():
        raise ValueError("Unexpected or missing exploratory plan fields")
    jobs = JobStore(app.workspace)
    previous = jobs.plan(session_id)
    if previous.get("data_classification") not in _PRIVATE:
        raise OperationBlocked("private_session_required")
    _check_scope(app.workspace, previous)
    if "template" in body:
        method, handoff = _tested_template(app.workspace, body["template"])
    else:
        method, handoff = (
            _local_method(
                app.workspace, body, body["input_bindings"], private_plan=previous
            ),
            None,
        )
    bound = _bind_inputs(
        app.workspace, previous, method, body["input_bindings"], offline_studio=True
    )
    plan_id = uuid4().hex
    binding = freeze_registered_method(
        app.workspace,
        session_id,
        plan_id=plan_id,
        method_version_id=method["version_id"],
        step_name=body["step_name"],
        parameters=body["parameters"],
        input_versions=bound,
        offline_studio=True,
    )
    frozen = {
        "format": "devbench.plugin-plan/1",
        "plan_id": plan_id,
        "session_id": session_id,
        "operation": OPERATION,
        "data_classification": "private",
        "objective": body["step_name"],
        "design": {"parameters": body["parameters"]},
        "session_intake": _lineage(session_id, previous)["intake"],
        "session_roots": list(_roots(previous)),
        "private_bindings": body["input_bindings"],
        "registered_method": binding,
        "authoring_path": "synthetic_tested_template" if handoff else "offline_local",
        "script_sha256": method["contract"]["implementation"]["source_sha256"],
        "clinical_release_allowed": False,
        "template": handoff,
        **{
            key: previous[key]
            for key in ("input_root", "input_declarations", "profiles", "runtime")
        },
    }
    pid, sha = jobs.freeze(frozen)
    return {"session_id": session_id, "plan_id": pid, "plan_sha256": sha}


def studio_script_review(app, session_id, plan_id, plan_sha):
    """Return the frozen source and reviewed resource contract to the local operator."""
    from .plugin_graph_studio import _check_scope

    if app.offline_only is not True:
        raise PermissionError("Private script review requires offline Studio")
    plan = JobStore(app.workspace).plan(session_id, plan_id)
    if (
        plan.get("data_classification") != "private"
        or plan["operation"] != OPERATION
        or digest(plan) != plan_sha
    ):
        raise OperationBlocked("plan_identity_mismatch")
    _check_scope(app.workspace, plan)
    methods = MethodService(app.workspace / "methods", readonly=True, reconcile=False)
    method = methods.method(plan["registered_method"]["step"]["oci_method_version_id"])
    script = read_bounded_file(
        methods.artifact_path(method["source_artifact"]), _SCRIPT_LIMIT
    )
    if hashlib.sha256(script).hexdigest() != plan["script_sha256"]:
        raise OperationBlocked("registered_method_artifact_changed")
    return {
        "script": script.decode("utf-8"),
        "script_sha256": plan["script_sha256"],
        "image": method["contract"]["environment"]["image"],
        "limits": method["contract"]["execution"],
        "outputs": method["contract"]["outputs"],
        "inputs": plan["private_bindings"],
        "template": plan["template"],
    }


def _local_preview(executor, artifact, spec):
    """Return a bounded display of a sealed port, with no active document content."""
    import base64
    import struct

    from devbench.engine.tier2_artifacts import port_format

    sha, fmt = artifact["sha256"], port_format(spec)
    view = {"sha256": sha, "format": fmt, "claim_tier": artifact["claim_tier"]}
    if fmt == "zarr":
        pin = executor.zarr_vault.attestation(sha)
        return {
            **view,
            "size_bytes": pin["size_bytes"],
            "members": len(executor.zarr_vault.member_digests(sha, pin)),
        }
    with executor.artifacts.open(sha) as source:
        size = os.fstat(source.fileno()).st_size
        view["size_bytes"] = size
        if fmt == "parquet":
            import pyarrow.parquet as pq

            with pq.ParquetFile(
                source,
                thrift_string_size_limit=262144,
                thrift_container_size_limit=65536,
                arrow_extensions_enabled=False,
            ) as table:
                view.update(
                    rows=table.metadata.num_rows, columns=table.schema_arrow.names[:64]
                )
                if (
                    table.num_row_groups
                    and table.metadata.row_group(0).total_byte_size <= 4 << 20
                ):
                    batch = next(
                        table.iter_batches(
                            batch_size=8, row_groups=[0], columns=view["columns"]
                        ),
                        None,
                    )
                    if batch is not None:
                        view["preview_rows"] = [
                            [str(value)[:100] for value in row.values()]
                            for row in batch.to_pylist()
                        ]
        elif fmt == "h5ad":
            from devbench.core.expression_inventory import expression_inventory

            header = expression_inventory(executor.artifacts.root / sha)
            view.update(shape=header["shape"], layers=header["layers"][:64])
        elif fmt == "png" and 24 <= size <= 512 << 10:
            raw = source.read(size + 1)
            width, height = struct.unpack(">II", raw[16:24])
            if raw.startswith(b"\x89PNG\r\n\x1a\n") and 0 < width * height <= 8_000_000:
                view["png"] = base64.b64encode(raw).decode("ascii")
        elif fmt in {"csv", "tsv", "json", "plot_sidecar", "plotly_json", "svg"}:
            view.update(
                text=source.read(32 << 10).decode("utf-8", errors="replace"),
                truncated=size > 32 << 10,
            )
    return view


def studio_output_preview(app, session_id, body):
    from .plugin_graph_studio import _check_scope
    from .plugin_mcp import studio_job_readback
    from .plugin_registered_method import _bound_plan, verify_registered_method_result

    if (
        app.offline_only is not True
        or getattr(app, "evidence_delivery", None) is not None
    ):
        raise PermissionError(
            "Private output inspection requires a local offline operator"
        )
    if not isinstance(body, dict) or body.keys() != {"plan_id", "plan_sha256", "port"}:
        raise ValueError("Select one output from an exact frozen plan")
    with studio_job_readback(app.workspace) as db:
        if db is None:
            raise OperationBlocked("private_graph_plan_unavailable")
        row = db.execute(
            "SELECT * FROM plugin_plans WHERE session_id=? AND plan_id=?",
            (session_id, body["plan_id"]),
        ).fetchone()
        plan = JobStore._plan(row, body["plan_id"], body["plan_sha256"])
        if (
            plan.get("operation") != OPERATION
            or plan.get("data_classification") != "private"
        ):
            raise OperationBlocked("private_graph_plan_unavailable")
        run = db.execute(
            "SELECT run_id,receipt_json FROM plugin_runs "
            "WHERE session_id=? AND plan_id=? "
            "AND plan_sha256=? AND status='completed'",
            (session_id, body["plan_id"], body["plan_sha256"]),
        ).fetchone()
    if run is None:
        raise OperationBlocked("verified_receipt_unavailable")
    _check_scope(app.workspace, plan)
    receipt = json.loads(run["receipt_json"])
    verify_registered_method_result(
        app.workspace, run["run_id"], plan, receipt, offline_studio=True
    )
    _, flow, executor, contract, _, _ = _bound_plan(
        app.workspace, plan, readonly=True, offline_studio=True
    )
    port = body["port"]
    if not isinstance(port, str) or port not in receipt["registered_method_outputs"]:
        raise OperationBlocked("private_output_unavailable")
    artifact = receipt["registered_method_outputs"][port]
    flow.ledger._read(lambda db: executor.guard_artifact(db, artifact))
    result = _local_preview(executor, artifact, contract["outputs"][port])
    flow.ledger._read(lambda db: executor.guard_artifact(db, artifact))
    return result


def run_private_exploration(jobs, rid, plan):
    from .plugin_graph_studio import _check_scope
    from .plugin_mcp import _lineage, _replay_manifest, _roots, _write_replay
    from .plugin_registered_method import run_registered_method

    result = run_registered_method(
        jobs,
        rid,
        plan,
        verify_sources=lambda: _check_scope(jobs.workspace, plan),
    )
    receipt = {
        **result,
        "run_id": rid,
        "session_id": plan["session_id"],
        "operation": OPERATION,
        "clinical_release_allowed": False,
        "probability_bounds_status": "not_evaluated",
        "data_classification": "private",
    }
    root = jobs.workspace / "runs" / rid / "replay_bundle"
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = _replay_manifest(plan, receipt)
    _write_replay(root, plan, receipt, manifest)
    receipt["replay_bundle_sha256"] = digest(manifest)
    broker = EvidenceBroker.open_existing(jobs.authority)
    parents = (_lineage(plan["session_id"], plan)["source_id"],)
    evidence = {
        "parents": parents,
        "roots": _roots(plan),
        "classification": "target_outcome",
        "tags": ("private",),
    }
    for name, output in receipt["registered_method_outputs"].items():
        broker.register_source(
            "private-output-" + digest([rid, name]),
            content_sha256=output["sha256"],
            **evidence,
        )
    broker.register_source(
        "plugin-receipt-" + rid, content_sha256=digest(receipt), **evidence
    )
    return receipt
