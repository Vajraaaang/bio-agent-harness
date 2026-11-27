"""Replay accepted Tier B training artifacts with their current local owner."""

from __future__ import annotations

import json
import platform
import re
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from devbench.analysis.evidence_access import EvidenceBroker
from devbench.core._files import read_bounded_file
from devbench.core._hashing import canonical_sha256
from devbench.engine.exploratory_images import exploratory_image
from devbench.engine.exploratory_oci import ExploratoryOCI
from devbench.engine.method_executor import LocalDockerExecutor
from devbench.engine.methods import MethodService, exploratory_method
from devbench.engine.partition_authority import embargoed
from devbench.engine.permissions import require_protected_storage
from devbench.engine.provenance import hash_artifact
from devbench.engine.tier2_artifacts import LANE_TAGS
from devbench.engine.tier2_input_custody import check_stage
from devbench.engine.tier2_staging import ENTRY_LIMIT
from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)
from devbench.platform.adaptive_workflow import AdaptiveStep, AdaptiveWorkflow
from devbench.platform.adaptive_workflow_state import ArtifactVersion

_OUTPUT_VERSION = re.compile(r"[a-f0-9]{32}-[0-9]+")


@dataclass(frozen=True)
class ExploratoryReplayMaterial:
    recipe: dict[str, Any]
    envelope: dict[str, Any]
    step: AdaptiveStep
    contract: dict[str, Any]
    parameters: dict[str, Any]
    source: Path
    lock: Path


def is_exploratory_thesis_artifact(artifact: ArtifactVersion) -> bool:
    return (
        _OUTPUT_VERSION.fullmatch(artifact.version_id) is not None
        and "exploratory" in artifact.permitted_uses
    )


@contextmanager
def open_exploratory_thesis_artifact(
    workflow: AdaptiveWorkflow,
    executor: ExploratoryOCI,
    artifact: ArtifactVersion,
) -> Iterator[BinaryIO]:
    """Open a current, accepted training artifact from its exact owning receipt."""
    if (
        not is_exploratory_thesis_artifact(artifact)
        or type(executor) is not ExploratoryOCI
        or executor.ledger.path != workflow.ledger.path
    ):
        raise PermissionError("Thesis export requires an owned Tier B artifact")

    def verify_current(db):
        state = workflow._state(db)
        row = state.artifacts.get(artifact.version_id)
        if row is None or row[1] is None or row[0] != artifact.model_dump(mode="json"):
            raise OperationBlocked("exploratory_thesis_artifact_unavailable")
        step_id, generation = row[1]
        outcome = state.outcome(step_id)
        if (
            not outcome
            or state.generations[step_id] != generation
            or outcome["status"] != "succeeded"
            or row[0] not in outcome["outputs"].values()
        ):
            raise OperationBlocked("exploratory_thesis_artifact_unavailable")
        dispatch = state.dispatches[outcome["attempt_id"]]
        if dispatch["token"]["backend_id"] != executor.backend_id:
            raise PermissionError("Thesis export requires a Tier B execution receipt")
        _check_scope(db, workflow, dispatch["envelope"], outcome)
        if executor._retained_receipt(state, step_id) is None:
            raise OperationBlocked("exploratory_replay_receipt_missing")

    workflow._read_projection(verify_current)
    if artifact.representation == "exploratory_zarr":
        raise OperationBlocked("directory_artifact_requires_local_vault")
    vault = (
        executor.binary_training
        if artifact.representation == "exploratory_parquet"
        else executor.artifacts
    )
    with vault.open(artifact.sha256) as stream:
        yield stream
    workflow._read_projection(verify_current)


def _check_code_scope(db, source_sha256, lock_sha256):
    if EvidenceBroker.has_private_content(db, (source_sha256, lock_sha256)):
        raise OperationBlocked("private_data_local_studio_only")
    if any(embargoed(db, identity) for identity in (source_sha256, lock_sha256)):
        raise OperationBlocked("protected_replay_artifact")


def _check_scope(db, flow, envelope, outcome):
    step = envelope["step"]
    if step["held_out"] or step["purpose"] != "exploratory":
        raise OperationBlocked("protected_evaluation")
    artifacts = (*envelope["inputs"].values(), *outcome["outputs"].values())
    if any(item.get("data_classification") == "private" for item in artifacts) or (
        EvidenceBroker.has_private_content(db, [item["sha256"] for item in artifacts])
    ):
        raise OperationBlocked("private_data_local_studio_only")
    if any(embargoed(db, item["sha256"]) for item in artifacts):
        raise OperationBlocked("protected_replay_artifact")
    if (flow.authority._load_state(db, flow.lineage_id) or {}).get(
        "status"
    ) == "cancelled":
        raise OperationBlocked("exploratory_replay_authority_cancelled")
    flow._guard(db, envelope)
    for name in outcome["outputs"]:
        flow._current_use(db, step["step_id"], name, "claim", None)
        try:
            lineage = flow.disclosure_provenance(
                step["step_id"],
                name,
                connection=db,
            )
        except KeyError as error:
            raise OperationBlocked("exploratory_replay_ancestry_unavailable") from error
        if any(embargoed(db, digest) for digest in lineage["dependencies"]):
            raise OperationBlocked("protected_replay_artifact")


def _material(db, flow, executor, state, step_id) -> ExploratoryReplayMaterial:
    outcome = state.outcome(step_id)
    if not outcome or outcome["status"] != "succeeded":
        raise OperationBlocked("exploratory_replay_attempt_unavailable")
    dispatch = state.dispatches[outcome["attempt_id"]]
    if dispatch["token"]["backend_id"] != executor.backend_id:
        raise PermissionError("Replay requires a retained Tier B attempt")
    envelope = dispatch["envelope"]
    _check_scope(db, flow, envelope, outcome)
    retained = executor._retained_receipt(state, step_id)
    if retained is None:
        raise OperationBlocked("exploratory_replay_receipt_missing")
    _, receipt = retained
    step = AdaptiveStep.model_validate(envelope["step"])
    method = executor.methods.method(step.oci_method_version_id)
    contract = method["contract"]
    checked = exploratory_method(contract)
    directory = executor._directory(dispatch["token"])
    io = executor._load(directory / "io.json")
    if "parameters" in io["frozen"]:
        parameters = checked.validate_parameters(io["frozen"]["parameters"])
    else:
        raw = read_bounded_file(directory / "method" / "parameters.json", 1_000_000)
        parameters = checked.validate_parameters(json.loads(raw))
    image_id = exploratory_image(contract["environment"]["image"]).image_id
    if (
        checked.version_id != step.oci_method_version_id
        or canonical_sha256(parameters) != step.parameters_sha256
        or io["runtime"].get("image_id") != image_id
        or receipt.cleanup_state != "confirmed"
        or not receipt.outputs
    ):
        raise OperationConflict("Tier B replay material differs from its receipt")
    _check_code_scope(
        db,
        contract["implementation"]["source_sha256"],
        contract["environment"]["lock_sha256"],
    )
    source = executor.methods.artifact_path(method["source_artifact"])
    lock = executor.methods.artifact_path(method["environment_artifact"])
    expected = {
        Path(contract["outputs"][name]["path"]).name: item.sha256
        for name, item in receipt.outputs.items()
    }
    if expected != io["runtime"].get("diagnostic_collection", {}).get("files"):
        raise OperationConflict("Tier B replay output manifest differs from collection")
    recipe = {
        "step_id": step_id,
        "receipt_sha256": outcome["receipt_sha256"],
        "method_version_id": checked.version_id,
        "parameters_sha256": step.parameters_sha256,
        "source_sha256": contract["implementation"]["source_sha256"],
        "lock_sha256": contract["environment"]["lock_sha256"],
        "image_id": image_id,
        "architecture": platform.machine(),
        "inputs": {pin.port: pin.sha256 for pin in receipt.consumed},
        "outputs": expected,
    }
    if pins := io["runtime"].get("matrix_attestations"):
        recipe["matrix_attestations"] = {k: v["source"] for k, v in pins.items()}
    return ExploratoryReplayMaterial(
        recipe, envelope, step, contract, parameters, source, lock
    )


def capture_exploratory_replay(
    workflow: AdaptiveWorkflow, executor: ExploratoryOCI, revision: int
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if (
        type(executor) is not ExploratoryOCI
        or executor.ledger.path != workflow.ledger.path
    ):
        raise PermissionError("Thesis replay requires the current Tier B owner")

    def read(db):
        if workflow.authority._revision(db, workflow.lineage_id) != revision:
            raise OperationConflict("Thesis replay requires the current revision")
        state = workflow._state(db)
        recipes, omitted = [], {}
        for step_id, step in sorted(state.nodes.items()):
            result = state.outcome(step_id)
            if step.oci_method_version_id is None or not result:
                continue
            if result["status"] != "succeeded":
                omitted[step_id] = "no_accepted_outputs"
                continue
            try:
                material = _material(db, workflow, executor, state, step_id)
            except OperationBlocked as error:
                omitted[step_id] = error.state
            else:
                recipes.append(material.recipe)
        return recipes, omitted

    return workflow._read_projection(read)


def prepare_exploratory_replay(
    owner: Path, lineage_id: str, recipes: list[dict[str, Any]]
) -> tuple[AdaptiveWorkflow, ExploratoryOCI, list[ExploratoryReplayMaterial]]:
    """Resolve an exact, existing authority without initializing new records."""
    from devbench.analysis.thesis_synthesis import _canonical_path

    root = require_protected_storage(_canonical_path(owner))
    ledger = WorkflowOperationLedger(root / LEDGER_NAME, readonly=True)
    existing = ("training", "evaluation", "runs")
    if any(not (root / "adaptive-oci" / name).is_dir() for name in existing):
        raise PermissionError("Tier B replay requires its existing artifact vaults")
    executor = ExploratoryOCI(MethodService(root, readonly=True), ledger, ())
    flow = AdaptiveWorkflow(ledger, lineage_id)

    def read(db):
        state = flow._state(db)
        plans = []
        for recipe in recipes:
            plan = _material(db, flow, executor, state, recipe["step_id"])
            if plan.recipe != recipe:
                raise OperationConflict(
                    "Tier B replay recipe differs from current evidence"
                )
            plans.append(plan)
        return plans

    return flow, executor, flow._read_projection(read)


def _verify_output_bytes(hashes, collected, maximum):
    for name, expected in hashes.items():
        if (
            hash_artifact(
                collected / name,
                max_bytes=maximum,
                max_entries=ENTRY_LIMIT,
            )["sha256"]
            != expected
        ):
            raise ValueError("Tier B replay artifact changed before publication")


def run_exploratory_recipe(
    output: Path,
    flow: AdaptiveWorkflow,
    executor: ExploratoryOCI,
    material: ExploratoryReplayMaterial,
) -> dict[str, Any]:
    """Use the isolated executor with a fresh replay identity and no new fit claim."""

    actual = flow._read_projection(
        lambda db: _material(db, flow, executor, flow._state(db), material.step.step_id)
    )
    if actual != material:
        raise OperationConflict("Tier B replay differs from its sealed recipe")
    material = actual

    def recheck(db):
        state = flow._state(db)
        outcome = state.outcome(material.step.step_id)
        if (
            not outcome
            or outcome["status"] != "succeeded"
            or outcome["receipt_sha256"] != material.recipe["receipt_sha256"]
        ):
            raise OperationBlocked("exploratory_replay_attempt_unavailable")
        _check_scope(db, flow, material.envelope, outcome)
        for name, item in material.envelope["inputs"].items():
            executor.guard_artifact(
                db,
                item,
                record=material.recipe.get("matrix_attestations", {}).get(name),
            )
        _check_code_scope(
            db, material.recipe["source_sha256"], material.recipe["lock_sha256"]
        )

    def observe(event):
        try:
            if event["event"] in {"container_create_intent", "worker_exec_intent"}:
                check_stage(
                    executor.zarr_vault,
                    work,
                    matrix_pins,
                    capture_mount=event["event"] == "container_create_intent",
                )
            flow._read_projection(recheck)
        except (OperationBlocked, OperationConflict, PermissionError):
            return False
        return True

    flow._read_projection(recheck)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    work = output.parent / ("replay-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    collected = work / "outputs"
    collected.mkdir(mode=0o700)
    matrix_pins: dict[str, dict] = {}
    inputs = executor._stage_oci_inputs(
        material.envelope,
        material.step,
        work,
        matrix_pins=matrix_pins,
        source_records=material.recipe.get("matrix_attestations"),
    )
    runtime = LocalDockerExecutor()
    runtime.set_observer(observe)
    receipt = runtime.execute(
        material.contract,
        inputs,
        material.parameters,
        material.source,
        material.lock,
        collected,
        **(
            {"matrix_mounts": (executor.zarr_vault, matrix_pins)} if matrix_pins else {}
        ),
        frozen={
            "format": "devbench.standalone-exploratory-replay/1",
            "image_id": material.recipe["image_id"],
            "replay_id": uuid.uuid4().hex,
            "source_receipt_sha256": material.recipe["receipt_sha256"],
            "method_version_id": material.recipe["method_version_id"],
            "parameters_sha256": material.recipe["parameters_sha256"],
            "input_sha256": material.recipe["inputs"],
        },
    )
    try:
        check_stage(executor.zarr_vault, work, matrix_pins)
        flow._read_projection(recheck)
        files, hashes, _ = executor._check_collected_outputs(
            receipt,
            material.contract,
            inputs,
            material.step,
            collected,
        )
        if hashes != material.recipe["outputs"] or not files:
            raise ValueError("Tier B replay did not reproduce its exact artifacts")
        _verify_output_bytes(
            hashes, collected, material.contract["execution"]["max_output_bytes"]
        )
        collected.rename(output)
    finally:
        shutil.rmtree(work)
    return {
        **LANE_TAGS,
        "outputs": hashes,
        "claim_scope": "exploratory_only",
        "implementation_trust": "unreviewed",
        "clinical_release_allowed": False,
    }
