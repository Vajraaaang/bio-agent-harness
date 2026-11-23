"""Curated single-cell operations using Tier A's existing dispatch custody."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Final, Literal, cast

from devbench.adaptive.models import Port
from devbench.engine.provenance import canonical_json
from devbench.platform.adaptive_workflow_state import (
    AdaptiveStep,
    ArtifactVersion,
    BasisUse,
    InputLink,
)
from devharness_controlled.native_contracts import (
    SCCODA_VERSIONS,
    nb_countsplit_kernel_images,
    nb_fit_kernel_images,
    nb_marker_kernel_images,
    nb_pseudobulk_kernel_images,
    pathway_kernel_images,
    reviewed_trajectory_kernel_images,
    sccoda_kernel_images,
    single_cell_kernel_images,
    trajectory_kernel_images,
)
from rna_kernels.scrna_upstream_contract import (
    DONOR_TRAJECTORY,
    MAX_SUMMARY_BYTES,
    NB_FOLD_AGGREGATE,
    NB_MARKER_FIT,
    NB_SEALED_REPRESENTATIONS,
    REQUEST_FORMAT,
    SCRNA_OPERATIONS,
    SUMMARY_FORMAT,
    CountSplitParameters,
    OperationID,
    SingleCellOperation,
    SingleCellPort,
    SingleCellStepSpec,
    marker_fit_basis_sha256,
)

from .tier_a_pathways import (
    NB_MARKER_ORA,
    NB_MARKER_ORA_RESULT,
    PATHWAY_INPUTS,
    PATHWAY_OPERATIONS,
    PATHWAY_PROFILE,
    PATHWAY_WORKER,
    PathwayStageSpec,
    check_pathway_bindings,
    check_pathway_summary,
    is_curated_sealed_input,
    open_pathway_input,
    require_confirmed_marker_fit,
    require_current_marker_ora_output,
    require_current_pooled_pathway_input,
)

if TYPE_CHECKING:
    from .tier_a_executor import TierAExecutor

_PURPOSE: Final[Literal["checked_observation"]] = "checked_observation"
_FORMAT = "tier-a-scrna-upstream"
_OUTPUT_ID = re.compile(r"tier-a-scrna-([a-f0-9]{32})-([a-z_]{1,40})")
_REASON = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_LIMITS = {"max_memory_mb": 2048, "timeout_seconds": 300}
_ORIGINAL_INPUTS = {
    "expression",
    "droplets",
    "droplet_provenance",
    "labels",
    "label_qualification",
    "composition_input",
    "composition_spec",
    "split_dispersion",
    "trajectory_expression",
    "trajectory_coordinates",
    "trajectory_spec",
}
_REVIEWED_INPUTS = {"droplet_provenance", "label_qualification", "split_dispersion"}
_SCCODA = "scrna_sccoda"
_SCCODA_WORKER = "devharness_server.tools_scrna_composition"


def is_single_cell_operation(operation: str) -> bool:
    """Recognize curated checked-observation workers on the shared DAG route."""
    return operation in SCRNA_OPERATIONS or operation in PATHWAY_OPERATIONS


def _operation(operation: str) -> SingleCellOperation:
    if operation in PATHWAY_OPERATIONS:
        return PATHWAY_OPERATIONS[operation]
    return SCRNA_OPERATIONS[cast(OperationID, operation)]


def _profile(operation: str) -> str:
    return PATHWAY_PROFILE if operation in PATHWAY_OPERATIONS else _FORMAT


def _output_ports(operation: SingleCellOperation) -> list[dict[str, Any]]:
    return [
        {"name": name, "artifact_kind": port.kind, "required": port.required}
        for name, port in operation.outputs.items()
    ]


def _require_image(
    executor: TierAExecutor, operation: str, *, reviewed_trajectory: bool = False
) -> None:
    nb_runtime = {
        NB_FOLD_AGGREGATE: (nb_pseudobulk_kernel_images, "donor aggregation"),
        NB_MARKER_FIT: (nb_fit_kernel_images, "marker fit"),
        "scrna_nb_fold_cluster": (nb_marker_kernel_images, "fold-clustering"),
        "scrna_nb_countsplit": (nb_countsplit_kernel_images, "count-split"),
    }.get(operation)
    if nb_runtime is not None:
        images, stage = nb_runtime
        if executor.mounted_image_id not in images():
            raise PermissionError(f"Tier A requires a pinned NB {stage} runtime")
        return
    if operation in PATHWAY_OPERATIONS:
        if executor.mounted_image_id not in pathway_kernel_images():
            raise PermissionError("Tier A requires a pinned pathway runtime")
        return
    if operation == _SCCODA and executor.mounted_image_id not in sccoda_kernel_images():
        raise PermissionError("Tier A requires a pinned scCODA runtime")
    if operation == DONOR_TRAJECTORY:
        trajectory_images = (
            reviewed_trajectory_kernel_images()
            if reviewed_trajectory
            else trajectory_kernel_images()
        )
        if executor.mounted_image_id not in trajectory_images:
            raise PermissionError("Tier A requires a pinned donor trajectory runtime")
        return
    if executor.mounted_image_id not in single_cell_kernel_images():
        raise PermissionError("Tier A requires a pinned single-cell runtime")


def register_single_cell_step(
    executor: TierAExecutor,
    declaration: SingleCellStepSpec | Mapping[str, Any],
    *,
    step_id: str,
    branch_id: str,
    biological_unit: str,
    sources: Mapping[str, tuple[str, str | None]],
    basis: tuple[BasisUse, ...] = (),
) -> AdaptiveStep:
    """Bind a checked operation to original versions or declared upstream ports."""
    executor.ledger._writable()
    spec = SingleCellStepSpec.model_validate(declaration)
    _require_roster_runtime(executor, spec)
    parameters = canonical_json(spec.model_dump(mode="json"))
    from hashlib import sha256

    step = build_checked_step(
        executor,
        spec.operation,
        sha256(parameters).hexdigest(),
        step_id=step_id,
        branch_id=branch_id,
        biological_unit=biological_unit,
        sources=sources,
        basis=basis,
    )
    executor._parameters.put(parameters)
    return step


CuratedStepSpec = SingleCellStepSpec | PathwayStageSpec


def build_checked_step(
    executor: TierAExecutor,
    operation_id: str,
    parameters_sha256: str,
    *,
    step_id: str,
    branch_id: str,
    biological_unit: str,
    sources: Mapping[str, tuple[str, str | None]],
    basis: tuple[BasisUse, ...],
) -> AdaptiveStep:
    """Build the shared checked-stage profile over fixed typed ports."""
    _require_image(executor, operation_id)
    if operation_id == DONOR_TRAJECTORY and biological_unit != "donor":
        raise ValueError("Donor trajectory requires donor biological units")
    operation = _operation(operation_id)
    if sources.keys() != operation.inputs.keys():
        raise ValueError("Single-cell action requires its exact input ports")
    inputs = tuple(
        InputLink(
            port=Port(name=name, artifact_kind=port.kind),
            source_id=sources[name][0],
            source_port=sources[name][1],
        )
        for name, port in operation.inputs.items()
    )
    return AdaptiveStep.model_validate(
        {
            "step_id": step_id,
            "branch_id": branch_id,
            "operation_id": operation_id,
            "environment_sha256": executor.environment_sha256,
            "parameters_sha256": parameters_sha256,
            "purpose": _PURPOSE,
            "profile": {
                "profile_id": _profile(operation_id),
                "version": 1,
                "operations": [
                    {
                        "operation_id": operation_id,
                        "output_kinds": sorted(
                            {port.kind for port in operation.outputs.values()}
                        ),
                        **_LIMITS,
                    }
                ],
                "input_representations": sorted(
                    {port.representation for port in operation.inputs.values()}
                ),
                "biological_units": [biological_unit],
                "permitted_claims": [_PURPOSE],
            },
            "inputs": inputs,
            "outputs": _output_ports(operation),
            "basis": basis,
        }
    )


def load_single_cell_spec(
    executor: TierAExecutor,
    envelope: dict[str, Any],
) -> CuratedStepSpec:
    parameters = envelope["step"]["parameters_sha256"]
    executor._require_unprotected(
        (parameters, *(pin["sha256"] for pin in envelope["pins"]))
    )
    operation = envelope["step"]["operation_id"]
    schema = PathwayStageSpec if operation in PATHWAY_OPERATIONS else SingleCellStepSpec
    with executor._parameters.open(parameters) as source:
        spec = schema.model_validate_json(source.read())
    if spec.operation != operation:
        raise ValueError("Single-cell operation differs from its retained parameters")
    return spec


def _check_profile(step: AdaptiveStep, spec: CuratedStepSpec) -> None:
    profile = step.profile
    if spec.operation == DONOR_TRAJECTORY and profile.biological_units != ("donor",):
        raise ValueError("Donor trajectory requires donor biological units")
    operation = _operation(spec.operation)
    if (
        profile.profile_id != _profile(spec.operation)
        or profile.version != 1
        or profile.permitted_claims != (_PURPOSE,)
        or len(profile.biological_units) != 1
        or len(profile.operations) != 1
        or set(profile.input_representations)
        != {port.representation for port in operation.inputs.values()}
    ):
        raise PermissionError(
            "Single-cell action requires its curated procedure profile"
        )
    capability = profile.operations[0]
    if (
        capability.operation_id != spec.operation
        or capability.effects
        or capability.max_memory_mb > _LIMITS["max_memory_mb"]
        or capability.timeout_seconds > _LIMITS["timeout_seconds"]
        or set(capability.output_kinds)
        != {port.kind for port in operation.outputs.values()}
    ):
        raise PermissionError("Single-cell action exceeds its registered capability")


def _check_source_port(
    item: ArtifactVersion,
    port: SingleCellPort,
    *,
    unit: str,
    upstream: bool,
    sealed: bool,
) -> None:
    permitted = not item.permitted_uses if sealed else _PURPOSE in item.permitted_uses
    if (item.kind, item.representation, item.biological_unit) != (
        port.kind,
        port.representation,
        unit,
    ) or not permitted:
        raise ValueError("Single-cell input differs from its registered port")
    if upstream and not _OUTPUT_ID.fullmatch(item.version_id):
        raise PermissionError("Single-cell derived input must come from Tier A custody")


def validate_single_cell_step(
    executor: TierAExecutor,
    step: AdaptiveStep,
    envelope: dict[str, Any],
) -> tuple[CuratedStepSpec, dict[str, ArtifactVersion]]:
    _require_image(
        executor,
        step.operation_id,
        reviewed_trajectory=bool(
            envelope.get("thesis_scope", {}).get("trajectory_evidence")
        ),
    )
    if (
        envelope.get("mode") != "adaptive/1"
        or envelope.get("environment_sha256") != executor.environment_sha256
        or step.environment_sha256 != executor.environment_sha256
        or step.purpose != _PURPOSE
        or step.primary
        or step.comparison
        or step.held_out
        or step.oci_method_version_id is not None
        or "manual_local_oci" in envelope
    ):
        raise PermissionError("Single-cell Tier A requires a checked observation")
    executor._require_current_identity()
    spec = load_single_cell_spec(executor, envelope)
    if isinstance(spec, SingleCellStepSpec):
        _require_roster_runtime(executor, spec)
    _check_profile(step, spec)
    operation = _operation(spec.operation)
    if [port.model_dump(mode="json") for port in step.outputs] != _output_ports(
        operation
    ) or envelope["inputs"].keys() != operation.inputs.keys():
        raise ValueError(
            "Single-cell action has different declared input or output ports"
        )
    inputs = {
        name: ArtifactVersion.model_validate(value)
        for name, value in envelope["inputs"].items()
    }
    _check_sources(inputs, spec, step.profile.biological_units[0])
    if isinstance(spec, PathwayStageSpec):
        check_pathway_bindings(spec, inputs, envelope["basis"], envelope["lineage_id"])
        if spec.operation == NB_MARKER_ORA:
            require_confirmed_marker_fit(executor, spec, inputs)
        require_current_pooled_pathway_input(
            executor, spec, inputs.get("pathway_statistics")
        )
    _check_reviewed_inputs(inputs, envelope["basis"])
    if "qc_metrics" in inputs:
        _check_qc_source(executor, inputs)
    if spec.operation == "scrna_nb_fold_cluster":
        _accepted_nb_receipt(
            executor, inputs, envelope["lineage_id"], "scrna_nb_countsplit"
        )
    if spec.operation == NB_FOLD_AGGREGATE:
        _check_nb_fold_ancestry(executor, inputs, envelope["lineage_id"])
    if spec.operation == NB_MARKER_FIT:
        _check_nb_marker_fit(executor, step, inputs, envelope)
    return spec, inputs


def _require_roster_runtime(executor: TierAExecutor, spec: SingleCellStepSpec) -> None:
    if (
        isinstance(spec.parameters, CountSplitParameters)
        and spec.parameters.marker_cohort_columns is not None
        and executor.mounted_image_id not in nb_pseudobulk_kernel_images()
    ):
        raise PermissionError("Tier A requires a pinned NB donor roster runtime")


def _check_qc_source(
    executor: TierAExecutor, inputs: dict[str, ArtifactVersion]
) -> None:
    """Bind the QC mask and feature sidecar to their original expression."""
    from .tier_a_executor import _RECORD_LIMIT, _read_record

    metrics = inputs["qc_metrics"]
    identity = _OUTPUT_ID.fullmatch(metrics.version_id)
    if identity is None or identity[2] != "qc_metrics":
        raise PermissionError("Expected retained QC metrics")
    saved = _read_record(
        executor._directory(identity[1]) / "receipt.json", _RECORD_LIMIT
    )
    if saved["outputs"].get("qc_metrics") != metrics.model_dump(mode="json"):
        raise PermissionError("QC metrics differ from their retained receipt")
    features = inputs.get("qc_features")
    if features is not None and saved["outputs"].get("qc_features") != (
        features.model_dump(mode="json")
    ):
        raise PermissionError("QC metrics and features must come from the same run")
    expression = next(
        (pin for pin in saved["consumed"] if pin["port"] == "expression"), None
    )
    if expression is None or expression["sha256"] != inputs["expression"].sha256:
        raise PermissionError("QC metrics were computed on different original counts")


def _accepted_nb_receipt(
    executor: TierAExecutor,
    inputs: dict[str, ArtifactVersion],
    lineage: str,
    operation: str,
):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow, request_digest

    def identity(port: str) -> str:
        match = _OUTPUT_ID.fullmatch(inputs[port].version_id)
        if match is None or match[2] != port:
            raise PermissionError("NB analysis requires retained ancestor ports")
        return match[1]

    attempts = {identity(port) for port in inputs}
    if len(attempts) != 1:
        reason = (
            "aggregation receipt" if operation == NB_FOLD_AGGREGATE else "split receipt"
        )
        raise PermissionError(f"NB inputs must share one {reason}")
    attempt = next(iter(attempts))
    flow = AdaptiveWorkflow(executor.ledger, lineage)

    def read(db):
        state = flow._state(db)
        dispatch, result = state.dispatches.get(attempt), state.outcomes.get(attempt)
        if not dispatch or not result or result["status"] != "succeeded":
            raise PermissionError("NB analysis requires an accepted ancestor attempt")
        envelope = dispatch["envelope"]
        if (
            envelope["step"]["operation_id"] != operation
            or flow.authority._checked_attempt(db, dispatch["token"])[
                "acceptance_state"
            ]
            != "accepted"
        ):
            raise PermissionError("NB analysis requires the exact accepted operation")
        return envelope, result["receipt_sha256"]

    envelope, recorded = flow._read_projection(read)
    receipt = executor.load_receipt(envelope, attempt_id=attempt)
    if (
        receipt is None
        or request_digest(receipt.model_dump(mode="json")) != recorded
        or any(receipt.outputs.get(port) != value for port, value in inputs.items())
    ):
        raise PermissionError("NB inputs differ from the accepted ancestor receipt")
    return receipt


def _check_nb_fold_ancestry(executor, inputs, lineage):
    split = _accepted_nb_receipt(
        executor,
        {
            port: inputs[port]
            for port in ("inference_counts", "split_report", "donor_roster")
        },
        lineage,
        "scrna_nb_countsplit",
    )
    labels = _accepted_nb_receipt(
        executor,
        {"cluster_labels": inputs["cluster_labels"]},
        lineage,
        "scrna_nb_fold_cluster",
    )
    sources = {pin.port: (pin.version_id, pin.sha256) for pin in labels.consumed}
    if sources != {
        port: (split.outputs[port].version_id, split.outputs[port].sha256)
        for port in ("clustering_counts", "split_report")
    }:
        raise PermissionError(
            "NB labels must descend from the matching clustering fold"
        )


def _check_nb_marker_fit(executor, step, inputs, envelope):
    review = marker_fit_basis_sha256(
        step.parameters_sha256,
        inputs["aggregation_report"].sha256,
    )
    if step.profile.biological_units != ("donor",) or not any(
        row["kind"] == "review_basis" and row["sha256"] == review
        for row in envelope["basis"].values()
    ):
        raise PermissionError(
            "Tier A requires the reviewed marker fit plan and donor scope"
        )
    _accepted_nb_receipt(executor, inputs, envelope["lineage_id"], NB_FOLD_AGGREGATE)


def _check_sources(
    inputs: dict[str, ArtifactVersion],
    spec: CuratedStepSpec,
    unit: str,
) -> None:
    for name, port in _operation(spec.operation).inputs.items():
        _check_source_port(
            inputs[name],
            port,
            unit=unit,
            upstream=name not in _ORIGINAL_INPUTS and name not in PATHWAY_INPUTS,
            sealed=is_curated_sealed_input(spec.operation, name, port.representation),
        )
    if len({item.unit_membership_sha256 for item in inputs.values()}) != 1:
        raise ValueError(
            "Single-cell actions require the same declared biological units"
        )


def _check_reviewed_inputs(inputs, basis):
    reviewed = {
        item["sha256"] for item in basis.values() if item["kind"] == "review_basis"
    }
    if any(
        inputs[port].sha256 not in reviewed for port in _REVIEWED_INPUTS & inputs.keys()
    ):
        raise PermissionError(
            "Single-cell qualification requires a current review basis"
        )


@contextmanager
def open_single_cell_output(
    executor: TierAExecutor,
    artifact: ArtifactVersion,
) -> Iterator[BinaryIO]:
    if artifact.representation in NB_SEALED_REPRESENTATIONS:
        raise PermissionError("NB held-out artifact remains sealed")
    if artifact.representation == NB_MARKER_ORA_RESULT:
        match = _OUTPUT_ID.fullmatch(artifact.version_id)
        if match is None or match[2] != "pathway_result":
            raise PermissionError("Expected the retained NB marker ORA artifact")
        require_current_marker_ora_output(executor, artifact, match[1])
    with _open_retained_single_cell_output(executor, artifact) as stream:
        yield stream


@contextmanager
def _open_retained_single_cell_output(
    executor: TierAExecutor,
    artifact: ArtifactVersion,
) -> Iterator[BinaryIO]:
    from .tier_a_executor import _RECORD_LIMIT, _read_record

    identity = _OUTPUT_ID.fullmatch(artifact.version_id)
    permitted = (
        ()
        if artifact.representation in NB_SEALED_REPRESENTATIONS
        else (
            _PURPOSE,
            "exploratory",
        )
    )
    if identity is None or artifact.permitted_uses != permitted:
        raise PermissionError("Expected a retained checked single-cell artifact")
    attempt, port = identity.groups()
    saved = _read_record(executor._directory(attempt) / "receipt.json", _RECORD_LIMIT)
    if saved["outputs"].get(port) != artifact.model_dump(mode="json"):
        raise PermissionError("Single-cell artifact differs from its retained receipt")
    executor._require_unprotected((artifact.sha256,))
    with executor._outputs.open(artifact.sha256) as stream:
        yield stream


@contextmanager
def open_single_cell_input(
    executor: TierAExecutor,
    name: str,
    artifact: ArtifactVersion,
    *,
    operation: str = "",
) -> Iterator[BinaryIO]:
    if is_curated_sealed_input(operation, name, artifact.representation):
        with _open_retained_single_cell_output(executor, artifact) as stream:
            yield stream
    elif name in PATHWAY_INPUTS:
        with open_pathway_input(executor, name, artifact) as stream:
            yield stream
    elif name not in _ORIGINAL_INPUTS or (
        name == "trajectory_expression" and is_single_cell_output(artifact)
    ):
        with open_single_cell_output(executor, artifact) as stream:
            yield stream
    else:
        with executor.training.open(artifact.sha256) as stream:
            yield stream


def mounted_single_cell_input_ports(
    spec: CuratedStepSpec,
) -> Mapping[str, SingleCellPort]:
    if isinstance(spec, PathwayStageSpec) and spec.request.camera is None:
        return {}
    return _operation(spec.operation).inputs


def stage_single_cell_request(
    executor: TierAExecutor,
    stage: Path,
    spec: CuratedStepSpec,
    inputs: dict[str, ArtifactVersion],
    *,
    envelope: dict[str, Any],
) -> str:
    from .tier_a_executor import _sha, _write_record

    mount = stage / "inputs"
    mount.mkdir(mode=0o700)
    (stage / "outputs").mkdir(mode=0o700)
    for name, port in mounted_single_cell_input_ports(spec).items():
        destination = mount / port.filename
        with (
            open_single_cell_input(
                executor,
                name,
                inputs[name],
                operation=spec.operation,
            ) as source,
            destination.open("xb") as output,
        ):
            shutil.copyfileobj(source, output, length=1024 * 1024)
        destination.chmod(0o400)
    request = mounted_single_cell_request(executor, spec, inputs, envelope=envelope)
    _write_record(mount / "request.json", request)
    return _sha(canonical_json(request))


def mounted_single_cell_request(
    executor: TierAExecutor,
    spec: CuratedStepSpec,
    inputs: dict[str, ArtifactVersion],
    *,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(spec, PathwayStageSpec):
        return spec.request.model_dump(mode="json")
    if spec.operation == _SCCODA:
        from devbench.core.scrna_composition import ScCODARuntime
        from devharness_server.tools_scrna_composition import CompositionRequest

        return CompositionRequest(
            input_sha256=inputs["composition_input"].sha256,
            spec_sha256=inputs["composition_spec"].sha256,
            runtime=ScCODARuntime(
                image_id=executor.mounted_image_id,
                **SCCODA_VERSIONS,
            ),
        ).model_dump(mode="json")
    request = {
        "format": REQUEST_FORMAT,
        **spec.model_dump(mode="json"),
        "inputs": {name: item.sha256 for name, item in inputs.items()},
    }
    if "droplet_provenance" in inputs:
        request["runtime_image_id"] = executor.mounted_image_id
    evidence = envelope.get("thesis_scope", {}).get("trajectory_evidence")
    if spec.operation == DONOR_TRAJECTORY and evidence is not None:
        request["trajectory_evidence"] = evidence
    return request


def execute_mounted_scrna(stage: Path, request: dict, identity: tuple):
    from .mounted_kernel_oci import run_mounted_container
    from .tier_a_executor import TierAExecutor, _read_record, _runtime_identity, _sha

    if _runtime_identity(TierAExecutor.backend_id, *identity[2:]) != identity[:2]:
        raise PermissionError("Tier A code or runtime identity changed")
    request_sha = request["request_sha256"]
    operation = request["operation"]
    module = _worker_module(operation)
    transport = run_mounted_container(
        stage,
        request_sha=request_sha,
        image_id=identity[2],
        attempt_id=request["attempt_id"],
        timeout=request["timeout"],
        memory_mb=request["memory_mb"],
        worker_module=module,
    )
    tool = {
        "tool": "mounted_pathways"
        if operation in PATHWAY_OPERATIONS
        else "mounted_scrna",
        "request_sha256": request_sha,
        "oci": transport,
    }
    if transport["error_type"] is not None:
        return None, tool
    try:
        summary = _read_record(stage / "outputs" / "summary.json", MAX_SUMMARY_BYTES)
    except (OSError, ValueError) as error:
        return None, {**tool, "error_type": type(error).__name__}
    return summary, {**tool, "result_sha256": _sha(canonical_json(summary))}


def _worker_module(operation: str) -> str:
    if operation in PATHWAY_OPERATIONS:
        return PATHWAY_WORKER
    return _SCCODA_WORKER if operation == _SCCODA else "rna_kernels.mounted_scrna"


def _check_mounted_summary(
    summary: dict[str, Any],
    spec: CuratedStepSpec,
    directory: Path,
    request: str,
) -> dict[str, str]:
    if isinstance(spec, PathwayStageSpec):
        return check_pathway_summary(summary, spec, directory, request)
    retained = summary["output_sha256"]
    operation = _operation(spec.operation)
    known = {port.filename for port in operation.outputs.values()}
    if (
        summary["format"] != SUMMARY_FORMAT
        or summary["operation"] != spec.operation
        or summary["request_sha256"] != request
        or summary.get("claim_scope") != _PURPOSE
        or summary["status"]
        not in {
            "completed",
            "partial",
            "rejected",
            "unavailable",
            "refused",
            "failed",
        }
        or not isinstance(retained, dict)
        or not retained.keys() <= known
        or {item.name for item in directory.iterdir()} != {"summary.json", *retained}
    ):
        raise ValueError("Single-cell worker summary or artifact inventory changed")
    return retained


def collect_single_cell_outputs(
    executor: TierAExecutor,
    spec: CuratedStepSpec,
    directory: Path,
    result: dict[str, Any] | None,
    *,
    request_sha: str,
    attempt: str,
    membership: ArtifactVersion,
) -> tuple[dict[str, ArtifactVersion], str | None, dict[str, Any]]:
    if result is None:
        return {}, "registered_single_cell_stage_failed", {}
    digests = _check_mounted_summary(result, spec, directory, request_sha)
    outputs = _retain_outputs(executor, directory, spec, digests, attempt, membership)
    evidence = {"stage_status": result["status"]}
    if isinstance(spec, PathwayStageSpec):
        evidence["pathway_claim_scope"] = result["claim_scope"]
    if result["status"] == "completed":
        required = {
            name
            for name, port in _operation(spec.operation).outputs.items()
            if port.required
        }
        if not required <= outputs.keys():
            raise ValueError("Single-cell worker omitted required artifacts")
        return outputs, None, evidence
    reason = result.get("reason") or "single_cell_stage_incomplete"
    if not _REASON.fullmatch(reason):
        reason = "single_cell_stage_incomplete"
    return {}, reason, {**evidence, "retained_evidence": digests}


def _retain_outputs(
    executor: TierAExecutor,
    directory: Path,
    spec: CuratedStepSpec,
    digests: dict[str, str],
    attempt: str,
    membership: ArtifactVersion,
) -> dict[str, ArtifactVersion]:
    from devharness_core.provenance import hash_artifact

    outputs = {}
    for name, port in _operation(spec.operation).outputs.items():
        if port.filename not in digests:
            continue
        path = directory / port.filename
        identity = hash_artifact(path, max_bytes=port.max_bytes, max_entries=1)
        digest = executor._outputs.put_file(path)
        if digest != digests[port.filename] or digest != identity["sha256"]:
            raise ValueError(
                "Single-cell worker artifact bytes do not match its digest"
            )
        outputs[name] = ArtifactVersion(
            version_id=f"tier-a-scrna-{attempt}-{name}",
            sha256=digest,
            kind=port.kind,
            representation=port.representation,
            biological_unit=membership.biological_unit,
            unit_membership_sha256=membership.unit_membership_sha256,
            graph_sha256=digest if port.kind == "graph" else None,
            label_origin="rna_derived"
            if spec.operation == "scrna_nb_fold_cluster" and name == "cluster_labels"
            else "none",
            permitted_uses=()
            if port.representation in NB_SEALED_REPRESENTATIONS
            else (_PURPOSE, "exploratory"),
        )
    return outputs


def is_single_cell_output(artifact: ArtifactVersion) -> bool:
    return _OUTPUT_ID.fullmatch(artifact.version_id) is not None
