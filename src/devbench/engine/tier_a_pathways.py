"""Operator admission and sealed inputs for the mounted pathway worker."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, BinaryIO, Final, Literal

from pydantic import Field, model_serializer, model_validator

from devbench.core._files import read_bounded_file
from devbench.core._hashing import contract_json_bytes
from devbench.core.contrast_design import TIER_A_BULK_OUTPUT
from devbench.core.joint_bh import JOINT_BH_OUTPUT_PREFIX
from devbench.core.rna_pathway_analysis import (
    CameraInput,
    DesignVerifier,
    PathwayPlan,
    PathwayRequest,
    PathwayResult,
    TestedGeneUniverse,
    UniverseVerifier,
    admit_pathway_request,
    matches_foreground_direction,
)
from devbench.core.scrna_marker_inference import MarkerCustodyVerifier, MarkerResult
from devbench.engine.runtime_contracts import FrozenContract
from devbench.platform.adaptive_workflow_state import (
    AdaptiveStep,
    ArtifactVersion,
    BasisUse,
    Digest,
    Identity,
)
from devharness_server.tools_rna_pathways import (
    MAX_MATRIX_BYTES,
    MAX_RESULT_BYTES,
    SUMMARY_FORMAT,
)
from rna_kernels.scrna_upstream_contract import (
    NB_MARKER_FIT_PARQUET,
    SingleCellOperation,
    SingleCellPort,
    is_sealed_nb_input_port,
)

if TYPE_CHECKING:
    from sqlite3 import Connection

    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.thesis_joint_bh_release import JointBHRelease

    from .tier_a_executor import TierAExecutor

PATHWAY_WORKER = "devharness_server.tools_rna_pathways"
PATHWAY_PROFILE = "tier-a-rna-pathways"
_CAMERA = "rna_pathway_camera"
_TESTED = "rna_pathway_tested"
_MARKERS = "rna_pathway_marker_ora"
NB_MARKER_ORA: Final = "rna_pathway_nb_marker_ora"
NB_MARKER_ORA_RESULT = "nb_marker_ora_result_json"
_RESULT = MappingProxyType(
    {
        "pathway_result": SingleCellPort(
            "results.json",
            "rna_pathway_result_json",
            max_bytes=MAX_RESULT_BYTES,
        ),
    }
)
PATHWAY_OPERATIONS = MappingProxyType(
    {
        _CAMERA: SingleCellOperation(
            MappingProxyType(
                {
                    "pathway_expression": SingleCellPort(
                        "expression.bin",
                        "pathway_expression_f64",
                        "continuous_matrix",
                        MAX_MATRIX_BYTES,
                    ),
                    "pathway_design": SingleCellPort(
                        "design.bin",
                        "pathway_design_f64",
                        max_bytes=MAX_MATRIX_BYTES,
                    ),
                }
            ),
            _RESULT,
        ),
        _TESTED: SingleCellOperation(
            MappingProxyType(
                {
                    "pathway_statistics": SingleCellPort(
                        "statistics.parquet", "parquet"
                    ),
                }
            ),
            _RESULT,
        ),
        _MARKERS: SingleCellOperation(
            MappingProxyType(
                {
                    "pathway_markers": SingleCellPort(
                        "marker-rows.json",
                        "pathway_marker_rows_json",
                    ),
                }
            ),
            _RESULT,
        ),
        NB_MARKER_ORA: SingleCellOperation(
            MappingProxyType(
                {
                    "marker_fit": SingleCellPort(
                        "marker-fit.parquet",
                        NB_MARKER_FIT_PARQUET,
                        max_bytes=256 * 1024**2,
                    ),
                }
            ),
            MappingProxyType(
                {
                    "pathway_result": SingleCellPort(
                        "results.json",
                        NB_MARKER_ORA_RESULT,
                        max_bytes=MAX_RESULT_BYTES,
                    ),
                }
            ),
        ),
    }
)
PATHWAY_INPUTS = frozenset(
    name for operation in PATHWAY_OPERATIONS.values() for name in operation.inputs
)
_BULK_OUTPUT_ID = re.compile(r"tier-a-([a-f0-9]{32})")


def _operation(request: PathwayRequest) -> str:
    if request.camera is not None:
        return _CAMERA
    if request.marker_rows_sha256 is not None:
        return _MARKERS
    return _TESTED


class PathwayStageSpec(FrozenContract):
    """Privately retained typed recipe bound to its operator-admitted lineage."""

    format: Literal["devbench.tier-a-pathways/1"] = "devbench.tier-a-pathways/1"
    operation: Literal[
        "rna_pathway_camera",
        "rna_pathway_tested",
        "rna_pathway_marker_ora",
        "rna_pathway_nb_marker_ora",
    ]
    lineage_id: Identity
    admitted_request: PathwayRequest = Field(alias="request")
    marker_review_sha256: Digest | None = None
    nb_marker_fit_sha256: Digest | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_recipe(self, serialize):
        fields = serialize(self)
        if "admitted_request" in fields:
            fields["request"] = fields.pop("admitted_request")
        if self.nb_marker_fit_sha256 is None:
            fields.pop("nb_marker_fit_sha256", None)
        return fields

    @model_validator(mode="after")
    def _check_operation(self) -> PathwayStageSpec:
        route = _operation(self.admitted_request)
        if self.nb_marker_fit_sha256 is not None:
            if route != _MARKERS:
                raise ValueError("Confirmed NB fits require post-clustering ORA")
            route = NB_MARKER_ORA
        if self.operation != route or (
            (route in {_MARKERS, NB_MARKER_ORA})
            != (self.marker_review_sha256 is not None)
        ):
            raise ValueError("Pathway recipe differs from its admitted method")
        return self

    @property
    def request(self) -> PathwayRequest:
        """Project the verified selection into the pinned worker's v1 format."""
        request = self.admitted_request
        if request.plan.foreground_direction == "all":
            return request
        assert request.universe is not None
        return request.model_copy(
            update={
                "plan": request.plan.model_copy(update={"foreground_direction": "all"}),
                "universe": request.universe.model_copy(
                    update={"selected": request.foreground}
                ),
            }
        )

    @property
    def expected_inputs(self) -> dict[str, str]:
        if self.nb_marker_fit_sha256 is not None:
            return {"marker_fit": self.nb_marker_fit_sha256}
        camera, universe = self.admitted_request.camera, self.admitted_request.universe
        if camera is not None:
            return {
                "pathway_expression": camera.expression_sha256,
                "pathway_design": camera.design_sha256,
            }
        assert universe is not None
        port = "pathway_markers" if self.operation == _MARKERS else "pathway_statistics"
        return {port: universe.gene_statistics_sha256}

    @property
    def evidence_sha256(self) -> frozenset[str]:
        if self.nb_marker_fit_sha256 is not None:
            # The native route resolves the accepted independent review on use.
            return frozenset()
        camera, universe = self.admitted_request.camera, self.admitted_request.universe
        if camera is not None:
            return frozenset((camera.design_receipt_sha256,))
        assert universe is not None
        receipts = {universe.result_receipt_sha256, universe.filter_receipt_sha256}
        if self.marker_review_sha256 is not None:
            receipts.add(self.marker_review_sha256)
        return frozenset(receipts)

    def request_bytes(self) -> bytes:
        return contract_json_bytes(self.request.model_dump(mode="json"))


def check_pathway_bindings(
    spec: PathwayStageSpec,
    inputs: Mapping[str, ArtifactVersion],
    basis: Mapping[str, Mapping[str, Any]],
    lineage_id: str,
) -> None:
    if spec.lineage_id != lineage_id:
        raise PermissionError("Pathway recipe belongs to a different lineage")
    if {port: item.sha256 for port, item in inputs.items()} != spec.expected_inputs:
        raise PermissionError("Pathway input changed after operator verification")
    allowed_basis = (
        {"review_basis"}
        if spec.operation == _CAMERA
        else {
            "source",
            "review_basis",
        }
    )
    live = {
        item["sha256"]
        for item in basis.values()
        if item["kind"] in allowed_basis and item["use"] == "compute"
    }
    if spec.evidence_sha256 - live:
        raise PermissionError("A current pathway evidence basis is required")


@contextmanager
def open_pathway_input(
    executor: TierAExecutor,
    name: str,
    artifact: ArtifactVersion,
) -> Iterator[BinaryIO]:
    if name == "pathway_statistics":
        with executor.open_output(artifact) as stream:
            yield stream
    else:
        with executor.training.open(artifact.sha256) as stream:
            yield stream


def _require_pooled_release(
    workflow: AdaptiveWorkflow,
    db: Connection,
    version_id: str,
    universe: TestedGeneUniverse,
) -> tuple[JointBHRelease, ArtifactVersion]:
    from devbench.platform.thesis_joint_bh import read_joint_event
    from devbench.platform.thesis_joint_bh_release import (
        _RECORD,
        _VERSION,
        _decode_release,
    )

    identity = _VERSION.fullmatch(version_id)
    if identity is None:
        raise PermissionError("Invalid joint BH output identity")
    row = read_joint_event(db, workflow.lineage_id, _RECORD + identity[1])
    if row is None:
        raise PermissionError("Joint BH pathway input requires its retained release")
    release = _decode_release(row)
    if release.receipt_sha256() != universe.result_receipt_sha256:
        raise PermissionError("The tested universe differs from its execution receipt")
    for artifact in release.outputs.values():
        if artifact.version_id == version_id and (
            artifact.sha256 == universe.gene_statistics_sha256
        ):
            return release, artifact
    raise PermissionError("Joint BH pathway input differs from its retained release")


def _verify_tested_receipt(
    executor: TierAExecutor,
    workflow: AdaptiveWorkflow,
    artifact: ArtifactVersion,
    universe: TestedGeneUniverse,
    *,
    connection: Connection | None = None,
) -> None:
    if artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX):
        with workflow._read_connection(connection) as db:
            _, retained = _require_pooled_release(
                workflow, db, artifact.version_id, universe
            )
        if artifact != retained:
            raise PermissionError(
                "Joint BH pathway input differs from its retained release"
            )
        return

    from .tier_a_executor import _RECORD_LIMIT, _read_record

    identity = _BULK_OUTPUT_ID.fullmatch(artifact.version_id)
    if identity is None:
        raise PermissionError(
            "Enrichment requires a retained registered Tier A DE result"
        )
    receipt = _read_record(
        executor._directory(identity[1]) / "receipt.json", _RECORD_LIMIT
    )
    if (
        sha256(contract_json_bytes(receipt)).hexdigest()
        != universe.result_receipt_sha256
    ):
        raise PermissionError("The tested universe differs from its execution receipt")


def require_current_pooled_pathway_input(
    executor: TierAExecutor, spec: PathwayStageSpec, artifact: ArtifactVersion | None
) -> None:
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    if artifact is not None and artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX):
        workflow = AdaptiveWorkflow(executor.ledger, spec.lineage_id)
        _verify_retained_tested_family(
            executor, workflow, artifact, spec.admitted_request
        )


def _verify_retained_tested_family(
    executor: TierAExecutor,
    workflow: AdaptiveWorkflow,
    artifact: ArtifactVersion,
    request: PathwayRequest,
    *,
    connection: Connection | None = None,
) -> None:
    import pyarrow.parquet as pq

    universe = request.universe
    assert universe is not None
    _verify_tested_receipt(
        executor, workflow, artifact, universe, connection=connection
    )
    genes = []
    direction = request.plan.foreground_direction
    directional_foreground = set(request.foreground) if direction != "all" else set()
    columns = ["gene_id", "estimate_status", "p_value", "q_value"]
    if directional_foreground:
        columns.append("effect")
    with open_pathway_input(executor, "pathway_statistics", artifact) as stream:
        table = pq.ParquetFile(stream)
        if table.metadata.num_rows > 100_000:
            raise ValueError("Gene-statistics evidence exceeds its row budget")
        for batch in table.iter_batches(batch_size=1024, columns=columns):
            for row in batch.to_pylist():
                if row["estimate_status"] != "estimated":
                    continue
                p, q = row["p_value"], row["q_value"]
                if p is None or q is None or not 0 <= p <= q <= 1:
                    raise ValueError(
                        "Retained gene-statistics probabilities are invalid"
                    )
                gene = row["gene_id"]
                if gene in directional_foreground and not matches_foreground_direction(
                    row.get("effect"), direction
                ):
                    raise PermissionError(
                        "ORA foreground conflicts with the retained effect direction"
                    )
                genes.append(gene)
    if tuple(genes) != universe.genes:
        raise PermissionError(
            "Pathway analysis requires the exact ordered filtered tested background"
        )
    # Rank keys are bound to universe.genes on admission and checked against the
    # ordered retained family here, including genes outside the ORA foreground.


def register_pathway_step(
    executor: TierAExecutor,
    workflow: AdaptiveWorkflow,
    plan: PathwayPlan,
    *,
    step_id: str,
    branch_id: str,
    biological_unit: str,
    sources: Mapping[str, tuple[str, str | None]],
    basis: tuple[BasisUse, ...] = (),
    universe: TestedGeneUniverse | None = None,
    verify_universe: UniverseVerifier | None = None,
    camera: CameraInput | None = None,
    verify_design: DesignVerifier | None = None,
    foreground: Sequence[str] = (),
    ranked_scores: Mapping[str, float] | None = None,
    marker_result: MarkerResult | None = None,
    verify_marker: MarkerCustodyVerifier | None = None,
) -> AdaptiveStep:
    """Seal an operator's verified method inputs before registering dispatch."""
    executor.ledger._writable()
    if workflow.ledger.path != executor.ledger.path:
        raise PermissionError("Pathway registration requires the bound workflow")
    request = admit_pathway_request(
        plan,
        universe=universe,
        verify_universe=verify_universe,
        camera=camera,
        verify_design=verify_design,
        foreground=foreground,
        ranked_scores=ranked_scores,
        marker_result=marker_result,
        verify_marker=verify_marker,
    )
    spec = PathwayStageSpec.model_validate(
        {
            "operation": _operation(request),
            "lineage_id": workflow.lineage_id,
            "request": request,
            "marker_review_sha256": (
                marker_result.proof.independent_check_sha256
                if marker_result is not None and marker_result.proof is not None
                else None
            ),
        }
    )
    return _seal_pathway_stage(
        executor,
        workflow,
        spec,
        step_id=step_id,
        branch_id=branch_id,
        biological_unit=biological_unit,
        sources=sources,
        basis=basis,
    )


def register_confirmed_nb_marker_ora(
    executor: TierAExecutor,
    workflow: AdaptiveWorkflow,
    plan: PathwayPlan,
    *,
    fit_step_id: str,
    step_id: str,
    branch_id: str,
    foreground: Sequence[str] | None = None,
) -> AdaptiveStep:
    """Register ORA over a confirmed sealed fit and its exact tested universe."""
    authority = _nb_authority(executor, workflow.lineage_id)
    if authority.flow.ledger.path != workflow.ledger.path:
        raise PermissionError("NB marker ORA requires the bound workflow")
    result, universe = authority.confirm(fit_step_id)
    assert result.proof is not None
    if foreground is None:
        foreground = (
            universe.selected
            if plan.foreground_direction == "all"
            else tuple(
                row.gene_id
                for row in result.gene_statistics
                if row.selected
                and matches_foreground_direction(row.score, plan.foreground_direction)
            )
        )
    request = admit_pathway_request(
        plan,
        universe=universe,
        verify_universe=authority.verify_universe,
        foreground=foreground,
        marker_result=result,
        verify_marker=authority.verify_marker,
    )
    fit = authority.retained_fit(universe)
    spec = PathwayStageSpec(
        operation=NB_MARKER_ORA,
        lineage_id=workflow.lineage_id,
        request=request,
        marker_review_sha256=result.proof.independent_check_sha256,
        nb_marker_fit_sha256=fit.subject.marker_fit_sha256,
    )
    return _seal_pathway_stage(
        executor,
        workflow,
        spec,
        step_id=step_id,
        branch_id=branch_id,
        biological_unit="donor",
        sources={"marker_fit": (fit_step_id, "marker_fit")},
        basis=(),
    )


def _seal_pathway_stage(
    executor: TierAExecutor,
    workflow: AdaptiveWorkflow,
    spec: PathwayStageSpec,
    *,
    step_id: str,
    branch_id: str,
    biological_unit: str,
    sources: Mapping[str, tuple[str, str | None]],
    basis: tuple[BasisUse, ...],
) -> AdaptiveStep:
    from .tier_a_scrna import build_checked_step

    retained = contract_json_bytes(spec.model_dump(mode="json"))
    identity = sha256(retained).hexdigest()
    step = build_checked_step(
        executor,
        spec.operation,
        identity,
        step_id=step_id,
        branch_id=branch_id,
        biological_unit=biological_unit,
        sources=sources,
        basis=basis,
    )
    with _pooled_pathway_source(workflow, spec, sources) as connection:
        _verify_current_sources(executor, workflow, step, spec, connection=connection)
        executor._parameters.put(retained)
    return step


@contextmanager
def _pooled_pathway_source(
    workflow: AdaptiveWorkflow,
    spec: PathwayStageSpec,
    sources: Mapping[str, tuple[str, str | None]],
) -> Iterator[Connection | None]:
    version_id, source_port = sources.get("pathway_statistics", ("", None))
    if (
        spec.operation != _TESTED
        or source_port is not None
        or not version_id.startswith(JOINT_BH_OUTPUT_PREFIX)
    ):
        yield None
        return
    assert spec.admitted_request.universe is not None
    with workflow.authority._execution_tx(workflow.lineage_id) as db:
        release, artifact = _require_pooled_release(
            workflow, db, version_id, spec.admitted_request.universe
        )
        projection = workflow._projection(db)
        state, _ = projection
        if version_id not in state.artifacts:
            # Each joint family member can change the tested q-values.
            parents = [
                state.outcomes[item.attempt_id]["outputs"][TIER_A_BULK_OUTPUT][
                    "version_id"
                ]
                for item in release.sources
            ]
            workflow._validate_artifact_parents(db, state, parents)
            workflow._event(
                db,
                "artifact",
                {
                    "artifact": artifact.model_dump(mode="json"),
                    "basis": [],
                    "parents": parents,
                },
                projection=projection,
            )
        yield db


def _nb_authority(executor: TierAExecutor, lineage: str):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    from .nb_marker_confirmation import NBMarkerConfirmationAuthority
    from .workflow_operations import OperationBlocked

    if executor.marker_review_verifier is None:
        raise OperationBlocked("nb_marker_confirmation_unavailable")
    return NBMarkerConfirmationAuthority(
        AdaptiveWorkflow(executor.ledger, lineage),
        executor,
        executor.marker_review_verifier,
    )


def require_confirmed_marker_fit(executor, spec, inputs) -> None:
    authority = _nb_authority(executor, spec.lineage_id)
    authority.require_pathway_fit(
        spec.admitted_request.universe,
        inputs["marker_fit"].sha256,
        spec.marker_review_sha256,
    )


def is_curated_sealed_input(operation: str, port: str, representation: str) -> bool:
    return is_sealed_nb_input_port(operation, port, representation) or (
        operation == NB_MARKER_ORA
        and port == "marker_fit"
        and representation == NB_MARKER_FIT_PARQUET
    )


def require_current_marker_ora_output(
    executor: TierAExecutor,
    artifact: ArtifactVersion,
    attempt_id: str,
) -> None:
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    from .tier_a_executor import _RECORD_LIMIT, _read_record
    from .workflow_operations import OperationBlocked

    manifest = _read_record(executor._directory(attempt_id) / "io.json", _RECORD_LIMIT)
    workflow = AdaptiveWorkflow(executor.ledger, manifest["token"]["lineage_id"])
    with workflow._read_connection() as db:
        state = workflow._state(db)
        dispatch = state.dispatches.get(attempt_id)
        if dispatch is None or dispatch["token"] != manifest["token"]:
            raise OperationBlocked("nb_marker_ora_receipt_unavailable")
        envelope = dispatch["envelope"]
        step = envelope["step"]
        if step["operation_id"] != NB_MARKER_ORA or artifact != workflow._current_use(
            db,
            step["step_id"],
            "pathway_result",
            "compute",
            envelope["generation"],
        ):
            raise OperationBlocked("nb_marker_ora_receipt_unavailable")
    if executor.load_receipt(envelope, attempt_id=attempt_id) is None:
        raise OperationBlocked("nb_marker_ora_receipt_unavailable")


def _verify_current_sources(
    executor: TierAExecutor,
    workflow: AdaptiveWorkflow,
    step: AdaptiveStep,
    spec: PathwayStageSpec,
    *,
    connection: Connection | None = None,
) -> None:
    def checked(db):
        state = workflow._state(db)
        if any(ref.basis_id not in state.basis for ref in step.basis):
            raise PermissionError("A current pathway evidence basis is required")
        inputs = workflow._step_inputs(state, step)
        basis = workflow._computation_basis(db, state, step, inputs)
        check_pathway_bindings(spec, inputs, basis, workflow.lineage_id)
        return inputs

    inputs = workflow._read_projection(checked, connection=connection)
    if spec.operation == NB_MARKER_ORA:
        require_confirmed_marker_fit(executor, spec, inputs)
    elif spec.operation == _TESTED:
        _verify_retained_tested_family(
            executor,
            workflow,
            inputs["pathway_statistics"],
            spec.admitted_request,
            connection=connection,
        )
    else:
        for port, artifact in inputs.items():
            with open_pathway_input(executor, port, artifact):
                pass


def check_pathway_summary(
    summary: dict[str, Any],
    spec: PathwayStageSpec,
    directory: Path,
    request_sha: str,
) -> dict[str, str]:
    data = read_bounded_file(directory / "results.json", MAX_RESULT_BYTES)
    result = PathwayResult.model_validate_json(data)
    if (
        result.method != spec.request.method
        or result.request_sha256 != request_sha
        or summary != _pathway_summary(result, data)
        or {p.name for p in directory.iterdir()} != {"summary.json", "results.json"}
    ):
        raise ValueError("Pathway output differs from its exact request and receipt")
    return {"results.json": sha256(data).hexdigest()}


def _pathway_summary(result: PathwayResult, data: bytes) -> dict[str, Any]:
    return {
        "format": SUMMARY_FORMAT,
        "operation": result.method,
        "request_sha256": result.request_sha256,
        "status": result.status,
        "reason": result.reason,
        "output_sha256": {"results.json": sha256(data).hexdigest()},
        "claim_scope": result.claim_scope,
    }
