"""Capture and replay current, accessible mounted single-cell receipts."""

from __future__ import annotations

import re
import shutil
import uuid
from graphlib import TopologicalSorter
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import canonical_sha256
from devbench.engine.partition_authority import embargoed
from devbench.engine.tier_a_scrna import (
    _operation,
    _worker_module,
    is_single_cell_operation,
    load_single_cell_spec,
    mounted_single_cell_input_ports,
    mounted_single_cell_request,
)
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow_state import ArtifactVersion
from devharness_core.provenance import copy_artifact, hash_artifact
from rna_kernels.canonical import canonical_json
from rna_kernels.scrna_upstream_contract import (
    MAX_SUMMARY_BYTES,
    NB_SEALED_REPRESENTATIONS,
)

if TYPE_CHECKING:
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

_SHA = re.compile(r"[a-f0-9]{64}")
_REQUEST_PATH = re.compile(r"replay_bundle/requests/single-cell-[0-9]{3}\.json")
_TOOL = {"mounted_scrna", "mounted_pathways"}
_MAX_REQUEST_BYTES = 16_000_000


def _local_scope(db, envelope, outcome):
    for artifact in (*envelope["inputs"].values(), *outcome["outputs"].values()):
        if artifact["representation"] in NB_SEALED_REPRESENTATIONS:
            return "sealed_artifact"
        if not artifact["permitted_uses"] or embargoed(db, artifact["sha256"]):
            return "protected_artifact"
    return None


def _accepted_steps(workflow, executor, revision):
    def read(db):
        if workflow.authority._revision(db, workflow.lineage_id) != revision:
            raise OperationConflict("Thesis replay requires the current revision")
        state = workflow._state(db)
        accepted, dependencies, omitted = {}, {}, {}
        for name, node in sorted(state.nodes.items()):
            outcome = state.outcome(name)
            if not is_single_cell_operation(node.operation_id) or not (
                outcome and outcome["status"] == "succeeded"
            ):
                continue
            dispatch = state.dispatches[outcome["attempt_id"]]
            if dispatch["token"]["backend_id"] != executor.backend_id:
                continue
            envelope = dispatch["envelope"]
            issue = _local_scope(db, envelope, outcome)
            if issue:
                omitted[name] = issue
                continue
            try:
                for port in outcome["outputs"]:
                    workflow._current_use(db, name, port, "claim", None)
            except OperationBlocked:
                omitted[name] = "acceptance_no_longer_current"
                continue
            accepted[name] = (outcome, envelope)
            dependencies[name] = {
                parent[0]
                for pin in envelope["pins"]
                if (parent := state.artifacts.get(pin["version_id"], (None, None))[1])
                is not None
            }
        order = TopologicalSorter({
            name: sorted(parents & accepted.keys())
            for name, parents in dependencies.items()
        }).static_order()
        return [accepted[name] for name in order], omitted

    return workflow._read_projection(read)


def _capture(executor, outcome, envelope, position):
    from devbench.engine.tier_a_executor import _RECORD_LIMIT, _read_record
    from devbench.platform.adaptive_workflow import request_digest

    retained = executor.load_receipt(envelope, attempt_id=outcome["attempt_id"])
    if (
        retained is None
        or request_digest(retained.model_dump(mode="json")) != outcome["receipt_sha256"]
    ):
        raise OperationBlocked("single_cell_replay_receipt_unverified")
    directory = executor._directory(retained.job_id)
    tool = _read_record(directory / "io.json", _RECORD_LIMIT)["tool"]
    oci = tool.get("oci", {})
    if (
        tool.get("tool") not in _TOOL
        or oci.get("image_id") != executor.mounted_image_id
        or oci.get("cleanup_confirmed") is not True
        or not _SHA.fullmatch(tool.get("result_sha256", ""))
    ):
        raise OperationBlocked("single_cell_replay_runtime_unverified")
    spec = load_single_cell_spec(executor, envelope)
    artifacts = {
        name: ArtifactVersion.model_validate(raw)
        for name, raw in envelope["inputs"].items()
    }
    request = mounted_single_cell_request(executor, spec, artifacts, envelope=envelope)
    identity = canonical_sha256(request)
    if identity != tool.get("request_sha256"):
        raise OperationConflict("Single-cell replay request differs from its receipt")
    ports = _operation(spec.operation)
    capability = envelope["step"]["profile"]["operations"][0]
    recipe = {
        "step_id": envelope["step"]["step_id"],
        "branch_id": envelope["step"]["branch_id"],
        "operation": spec.operation,
        "request_path": f"replay_bundle/requests/single-cell-{position:03d}.json",
        "request_sha256": identity,
        "image_id": oci["image_id"],
        "platform": oci["platform"],
        "memory_mb": capability["max_memory_mb"],
        "timeout_seconds": max(1, capability["timeout_seconds"] - 30),
        "inputs": {
            port.filename: artifacts[name].sha256
            for name, port in mounted_single_cell_input_ports(spec).items()
        },
        "outputs": {
            ports.outputs[name].filename: artifact.sha256
            for name, artifact in retained.outputs.items()
        },
        "summary_sha256": tool["result_sha256"],
        "receipt_sha256": outcome["receipt_sha256"],
    }
    return recipe, canonical_json(request)


def capture_single_cell_replay(
    workflow: AdaptiveWorkflow,
    executor: TierAExecutor,
    revision: int,
) -> tuple[dict[str, bytes], list[dict[str, Any]], dict[str, str]]:
    """Freeze accepted requests without adding data bytes to the thesis export."""
    files, recipes = {}, []
    steps, omitted = _accepted_steps(workflow, executor, revision)
    for position, (outcome, envelope) in enumerate(steps, start=1):
        try:
            recipe, raw = _capture(executor, outcome, envelope, position)
        except OperationBlocked as error:
            omitted[envelope["step"]["step_id"]] = error.state
            continue
        recipes.append(recipe)
        files[recipe["request_path"]] = raw
    return files, recipes, omitted


def _check_inventory(receipt, ports, *, required=False):
    known = {port.filename: port.max_bytes for port in ports.values()}
    if not isinstance(receipt, dict) or not receipt.keys() <= known.keys():
        raise ValueError("Invalid mounted single-cell replay inventory")
    if required and any(
        port.required and port.filename not in receipt for port in ports.values()
    ):
        raise ValueError("Missing required mounted single-cell replay output")
    for digest in receipt.values():
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError("Single-cell replay requires exact content digests")
    return known


def _hydrate(input_root, inputs, recovered, manifest, limits):
    for name, digest in sorted(manifest.items()):
        location = recovered.get(digest, input_root / digest)
        try:
            identity = hash_artifact(location, max_bytes=limits[name], max_entries=1)
        except FileNotFoundError as error:
            raise ValueError(
                f"Mounted single-cell replay input missing: {name}"
            ) from error
        if identity["kind"] != "file" or identity["sha256"] != digest:
            raise ValueError(f"Mounted single-cell replay input differs: {name}")
        copied = copy_artifact(
            location, inputs / name, max_bytes=limits[name], max_entries=1
        )
        if copied["sha256"] != digest:
            raise ValueError(f"Mounted single-cell replay copy differs: {name}")


def run_single_cell_recipe(
    root: Path,
    output: Path,
    source: Path,
    recovered: dict[str, Path],
    recipe: dict[str, Any],
) -> dict[str, str]:
    """Execute only a registered stage and publish its verified result bytes."""
    from devbench.engine.mounted_kernel_oci import run_mounted_container

    operation = recipe["operation"]
    path = recipe["request_path"]
    if not is_single_cell_operation(operation) or not _REQUEST_PATH.fullmatch(path):
        raise ValueError("Unsupported mounted single-cell replay")
    ports = _operation(operation)
    inputs_bounds = _check_inventory(recipe["inputs"], ports.inputs)
    outputs_bounds = _check_inventory(recipe["outputs"], ports.outputs, required=True)
    request = read_bounded_file(root / path, _MAX_REQUEST_BYTES)
    if sha256(request).hexdigest() != recipe["request_sha256"]:
        raise ValueError("Mounted single-cell replay request differs")
    memory, timeout = recipe["memory_mb"], recipe["timeout_seconds"]
    if (
        type(memory) is not int or not 1 <= memory <= 2048
        or type(timeout) is not int or not 1 <= timeout <= 270
    ):
        raise ValueError("Mounted single-cell replay exceeds its resource limits")
    output.mkdir(mode=0o700)
    stage = output / ".work"
    inputs = stage / "inputs"
    outputs = stage / "outputs"
    inputs.mkdir(mode=0o700, parents=True)
    outputs.mkdir(mode=0o700)
    _hydrate(source, inputs, recovered, recipe["inputs"], inputs_bounds)
    (inputs / "request.json").write_bytes(request)
    run = run_mounted_container(
        stage,
        request_sha=recipe["request_sha256"],
        image_id=recipe["image_id"],
        attempt_id=uuid.uuid4().hex,
        timeout=timeout,
        memory_mb=memory,
        worker_module=_worker_module(operation),
    )
    if (
        run["error_type"] is not None
        or run["cleanup_confirmed"] is not True
        or run["platform"] != recipe["platform"]
    ):
        raise ValueError("Mounted single-cell replay requires its recorded platform")
    verified = {"summary.json": recipe["summary_sha256"], **recipe["outputs"]}
    limits = {"summary.json": MAX_SUMMARY_BYTES, **outputs_bounds}
    if set(path.name for path in outputs.iterdir()) != verified.keys():
        raise ValueError("Mounted single-cell replay output inventory differs")
    for name, digest in verified.items():
        if hash_bounded_file(outputs / name, limits[name]) != digest:
            raise ValueError(f"Mounted single-cell replay did not reproduce {name}")
    for name, digest in verified.items():
        retained = (outputs / name).rename(output / name)
        if name != "summary.json":
            recovered.setdefault(digest, retained)
    shutil.rmtree(stage)
    return verified
