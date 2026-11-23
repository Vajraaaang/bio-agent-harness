"""Read an accepted single-cell QC mask from its owning adaptive ledger."""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
from typing import TYPE_CHECKING

from devbench.core.scrna_qc_selection import AcceptedQCSelection, QCSelectionBinding
from devbench.engine.partition_authority import embargoed
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow_state import ArtifactVersion
from rna_kernels.canonical import canonical_json

if TYPE_CHECKING:
    from sqlite3 import Connection

    from devbench.core.scrna_composition import (
        CompositionInput,
        CompositionObservationSpec,
    )
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

_MAX_QC_BYTES = 64 * 1024**2
_MAX_CELLS = 250_000
QC_REPLAY_BASIS_PREFIX = "qc-replay:"
QC_LABEL_BASIS_PREFIX = "qc-label:"
QC_COMPOSITION_REPLAY_FORMAT = "devbench.scrna-qc-composition-replay/1"


def retain_qc_source_basis(
    flow: AdaptiveWorkflow, db: Connection, digest: str, *,
    prefix: str = QC_REPLAY_BASIS_PREFIX,
) -> str:
    """Bind a retained replay recipe or qualified label dependency by digest."""
    identity = prefix + digest
    prior = flow._state(db).basis.get(identity)
    if prior is None:
        flow.record_basis(identity, "source", digest, connection=db)
    elif prior["kind"] != "source" or prior["sha256"] != digest:
        raise OperationConflict("QC source basis differs from its retained digest")
    return identity


def _capture_qc_artifact(
    flow: AdaptiveWorkflow,
    qc_step_id: str,
    *,
    source_sha256: str,
    connection: Connection | None = None,
) -> tuple[QCSelectionBinding, ArtifactVersion, str]:
    from devbench.engine.tier_a_executor import TierAExecutor

    def read(db):
        state = flow._state(db)
        step = state.nodes.get(qc_step_id)
        if step is None or step.operation_id != "scrna_batch_qc":
            raise OperationBlocked("scrna_qc_handoff_wrong_operation")
        if embargoed(db, source_sha256):
            raise OperationBlocked("scrna_qc_handoff_source_protected")
        metrics = flow._current_use(db, qc_step_id, "qc_metrics", "compute", None)
        outcome = state.outcome(qc_step_id)
        dispatch = state.dispatches[outcome["attempt_id"]]
        envelope = dispatch["envelope"]
        if dispatch["token"]["backend_id"] != TierAExecutor.backend_id:
            raise OperationBlocked("scrna_qc_handoff_unregistered_worker")
        expression = envelope["inputs"]["expression"]
        if expression["sha256"] != source_sha256:
            raise OperationBlocked("scrna_qc_handoff_source_changed")
        if metrics.kind != "table" or metrics.representation != "scrna_qc_metrics_csv":
            raise OperationBlocked("scrna_qc_handoff_invalid_metrics")
        return QCSelectionBinding(
            lineage_id=flow.lineage_id,
            step_id=qc_step_id,
            generation=state.generations[qc_step_id],
            receipt_sha256=outcome["receipt_sha256"],
            source_sha256=source_sha256,
            metrics_sha256=metrics.sha256,
        ), metrics, expression["version_id"]

    return flow._read_projection(read, connection=connection)


def require_current_qc_selection(
    flow: AdaptiveWorkflow, binding: QCSelectionBinding
) -> None:
    current_qc_parents(flow, binding)


def current_qc_parents(
    flow: AdaptiveWorkflow,
    binding: QCSelectionBinding,
    *,
    connection: Connection | None = None,
) -> tuple[str, str]:
    """Resolve the exact current original-count and QC-metrics versions."""
    if flow.lineage_id != binding.lineage_id:
        raise PermissionError("QC selection belongs to a different workflow")
    current, metrics, source_version_id = _capture_qc_artifact(
        flow, binding.step_id, source_sha256=binding.source_sha256,
        connection=connection,
    )
    if current != binding:
        raise OperationBlocked("scrna_qc_handoff_receipt_changed")
    return source_version_id, metrics.version_id


def _parse_mask(raw: bytes) -> tuple[tuple[str, ...], tuple[bool, ...]]:
    reader = csv.reader(io.StringIO(raw.decode("utf-8"), newline=""), strict=True)
    header = next(reader, [])
    if (
        len(header) < 2
        or len(set(header)) != len(header)
        or "qc_pass" not in header[1:]
    ):
        raise ValueError("QC metrics require an observation ID axis and QC pass flags")
    flag_column = header.index("qc_pass")
    cells: list[str] = []
    passed: list[bool] = []
    flags = {"True": True, "False": False}
    for row in reader:
        if (
            len(row) != len(header)
            or not row[0]
            or row[flag_column] not in flags
            or len(cells) >= _MAX_CELLS
        ):
            raise ValueError("Invalid or unbounded QC metrics row")
        cells.append(row[0])
        passed.append(flags[row[flag_column]])
    if not cells or len(set(cells)) != len(cells):
        raise ValueError("QC metrics require distinct observation IDs")
    return tuple(cells), tuple(passed)


def accepted_qc_selection(
    flow: AdaptiveWorkflow,
    executor: TierAExecutor,
    qc_step_id: str,
    *,
    source_sha256: str,
) -> AcceptedQCSelection:
    """Verify the source and provenance before returning an ordered QC mask."""
    if executor.ledger.path != flow.ledger.path:
        raise PermissionError("QC selection requires the workflow's bound Tier A store")
    binding, artifact, _ = _capture_qc_artifact(
        flow, qc_step_id, source_sha256=source_sha256,
    )
    with executor.open_output(artifact) as stream:
        raw = stream.read(_MAX_QC_BYTES + 1)
    if len(raw) > _MAX_QC_BYTES or hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise ValueError("QC metrics exceed their byte bound or fail receipt identity")
    cells, passed = _parse_mask(raw)
    require_current_qc_selection(flow, binding)
    return AcceptedQCSelection(binding, cells, passed)


def stage_composition_after_qc(
    flow: AdaptiveWorkflow,
    executor: TierAExecutor,
    qc_step_id: str,
    source: Path,
    declaration: CompositionObservationSpec,
) -> CompositionInput:
    """Aggregate donor composition from the current mask on the original source."""
    data, selection = _stage_composition(
        flow, executor, qc_step_id, source, declaration,
    )
    require_current_qc_selection(flow, selection.binding)
    return data


def _stage_composition(
    flow: AdaptiveWorkflow,
    executor: TierAExecutor,
    qc_step_id: str,
    source: Path,
    declaration: CompositionObservationSpec,
) -> tuple[CompositionInput, AcceptedQCSelection]:
    from devbench.core.scrna_composition import composition_from_h5ad

    selection = accepted_qc_selection(
        flow, executor, qc_step_id, source_sha256=declaration.source_sha256,
    )
    data = composition_from_h5ad(source, declaration, qc_selection=selection)
    return data, selection


def register_composition_after_qc(
    flow: AdaptiveWorkflow,
    executor: TierAExecutor,
    qc_step_id: str,
    source: Path,
    declaration: CompositionObservationSpec,
    *,
    version_id: str,
    expected_revision: int | None = None,
) -> tuple[CompositionInput, ArtifactVersion]:
    """Register the checked composition input with immutable QC dependencies."""
    from rna_kernels.scrna_upstream_contract import SCRNA_OPERATIONS

    data, selection = _stage_composition(
        flow, executor, qc_step_id, source, declaration,
    )
    descriptor = SCRNA_OPERATIONS["scrna_sccoda"].inputs["composition_input"]
    artifact = ArtifactVersion(
        version_id=version_id,
        sha256=executor.training.put(canonical_json(data.model_dump(mode="json"))),
        kind=descriptor.kind,
        representation=descriptor.representation,
        biological_unit=data.biological_unit,
        unit_membership_sha256=data.biological_unit_mapping_sha256,
    )
    recipe = executor.training.put(canonical_json({
        "format": QC_COMPOSITION_REPLAY_FORMAT,
        "qc_selection": selection.binding.model_dump(mode="json"),
        "qc_mask_sha256": selection.mask_sha256(),
        "specification": declaration.model_dump(mode="json"),
        "composition_input_sha256": artifact.sha256,
    }))
    with flow.authority._execution_tx(flow.lineage_id) as db:
        revision = flow.authority._revision(db, flow.lineage_id)
        if expected_revision is not None and expected_revision != revision:
            raise OperationConflict("Composition registration revision changed")
        parents = current_qc_parents(flow, selection.binding, connection=db)
        basis = retain_qc_source_basis(flow, db, recipe)
        flow.register_artifact(
            artifact, parents=parents, basis=(basis,), connection=db,
        )
    return data, artifact
