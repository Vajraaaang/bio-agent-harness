"""Independent review and live receipt checks for sealed NB marker fits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from devbench.core._hashing import decode_receipt_json
from devbench.core.rna_pathway_analysis import TestedGeneUniverse
from devbench.core.scientific_types import FrozenContract, content_sha256
from devbench.core.scrna_marker_inference import (
    DispersionProvenance,
    MarkerContext,
    MarkerProof,
    MarkerResult,
    MarkerRow,
    NegativeBinomialSplitProof,
    marker_rows_sha256,
    project_post_clustering_markers,
)
from devbench.platform.adaptive_workflow import AdaptiveWorkflow, request_digest
from devbench.platform.adaptive_workflow_state import ArtifactVersion, Digest, Identity
from devbench.platform.adaptive_workflow_state import _State as AdaptiveState

from .partition_authority import embargoed
from .scientific_authority import _projection_event
from .tier_a_executor import TierAExecutor
from .tier_a_scrna import _open_retained_single_cell_output
from .workflow_operations import OperationBlocked, OperationConflict, _digest, _json

_EVENT = "nb_marker_confirmation"
_RECORD = "nb-marker-confirmation-"
_CHECKS = {"design", "dispersion", "exposure", "calibration"}
_STATS = ("gene_id", "effect", "p_value", "q_value", "estimate_status")
_STATUSES: dict[str, Literal["tested", "independently_filtered", "not_estimable"]] = {
    "estimated": "tested",
    "filtered": "independently_filtered",
    "failed": "not_estimable",
}


class NBMarkerReviewSubject(FrozenContract):
    lineage_id: Identity
    fit_step_id: Identity
    fit_actor: Identity
    receipt_chain_sha256: Digest
    fit_receipt_sha256: Digest
    marker_fit_sha256: Digest
    solver_receipt_sha256: Digest
    rows_sha256: Digest
    context: MarkerContext
    runtime_sha256: Digest
    paired_donors: int = Field(ge=3)
    fdr_threshold: float = Field(gt=0, lt=1)

    def sha256(self) -> str:
        return content_sha256(self.model_dump(mode="json"))


class IndependentMarkerReview(FrozenContract):
    """An exact-subject decision returned by an installed host review verifier."""

    format: Literal["devbench.nb-marker-independent-review/1"] = (
        "devbench.nb-marker-independent-review/1"
    )
    subject_sha256: Digest
    review_receipt_sha256: Digest
    reviewer: Identity
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ]
    checks: dict[str, Digest]

    @model_validator(mode="after")
    def complete_review(self) -> IndependentMarkerReview:
        if self.checks.keys() != _CHECKS:
            raise ValueError(
                "Marker review requires independent evidence for every check"
            )
        return self


MarkerReviewVerifier = Callable[[NBMarkerReviewSubject], IndependentMarkerReview | None]


class RetainedNBMarkerConfirmation(FrozenContract):
    subject: NBMarkerReviewSubject
    review: IndependentMarkerReview
    proof_sha256: Digest
    universe_sha256: Digest


@dataclass(frozen=True, slots=True)
class _Stage:
    envelope: dict[str, Any]
    attempt_id: str
    receipt_sha256: str
    outputs: dict[str, ArtifactVersion]


def _accepted_stage(flow, db, state, step_id, operation) -> _Stage:
    step = state.nodes.get(step_id)
    if step is None or step.operation_id != operation:
        raise OperationBlocked("nb_marker_ancestor_unavailable")
    generation = state.generations[step_id]
    if (
        step.branch_id in state.retired
        or flow._held_ancestry(state, step_id)
        or (step_id, generation) in state.claim_stale
    ):
        raise OperationBlocked("nb_marker_ancestor_invalidated")
    outcome = state.outcome(step_id)
    if outcome is None or outcome["status"] != "succeeded":
        raise OperationBlocked("nb_marker_ancestor_unavailable")
    dispatch = state.dispatches[outcome["attempt_id"]]
    envelope = dispatch["envelope"]
    inputs = flow._step_inputs(state, step)
    if (
        {name: item.model_dump(mode="json") for name, item in inputs.items()}
        != envelope["inputs"]
        or flow._computation_basis(db, state, step, inputs) != envelope["basis"]
        or generation != envelope["generation"]
    ):
        raise OperationBlocked("nb_marker_ancestor_invalidated")
    attempt = flow.authority._checked_attempt(db, dispatch["token"])
    if (
        attempt["acceptance_state"] != "accepted"
        or attempt["output_record_id"] != outcome["accepted_record_id"]
        or attempt["verification_sha256"] != outcome["receipt_sha256"]
        or attempt["artifact_sha256"] != request_digest(outcome["outputs"])
    ):
        raise OperationConflict("NB marker acceptance identity changed")
    outputs = {
        port: ArtifactVersion.model_validate(doc)
        for port, doc in outcome["outputs"].items()
    }
    if any(
        item.version_id in state.stale_versions or embargoed(db, item.sha256)
        for item in (*inputs.values(), *outputs.values())
    ):
        raise OperationBlocked("nb_marker_ancestor_invalidated")
    return _Stage(envelope, outcome["attempt_id"], outcome["receipt_sha256"], outputs)


def _parent(state: AdaptiveState, stage: _Stage, *ports: str) -> str:
    parents = {
        state.artifacts[stage.envelope["inputs"][port]["version_id"]][1]
        for port in ports
    }
    if len(parents) != 1:
        raise OperationConflict("NB marker inputs require one exact ancestor")
    (parent,) = parents
    if parent is None or state.generations[parent[0]] != parent[1]:
        raise OperationBlocked("nb_marker_ancestor_invalidated")
    return parent[0]


@dataclass(frozen=True, slots=True)
class _Chain:
    fit: _Stage
    aggregation: _Stage
    clustering: _Stage
    split: _Stage

    @property
    def stages(self) -> tuple[_Stage, ...]:
        return (self.fit, self.aggregation, self.clustering, self.split)

    def sha256(self) -> str:
        return content_sha256([stage.receipt_sha256 for stage in self.stages])


def _chain(flow: AdaptiveWorkflow, db: Connection, fit_step_id: str) -> _Chain:
    state = flow._state(db)
    fit = _accepted_stage(flow, db, state, fit_step_id, "scrna_nb_marker_fit")
    aggregate_id = _parent(
        state, fit, "pseudobulk_counts", "pseudobulk_metadata", "aggregation_report"
    )
    aggregate = _accepted_stage(
        flow, db, state, aggregate_id, "scrna_nb_fold_pseudobulk"
    )
    cluster_id = _parent(state, aggregate, "cluster_labels")
    cluster = _accepted_stage(flow, db, state, cluster_id, "scrna_nb_fold_cluster")
    split_id = _parent(
        state, aggregate, "inference_counts", "split_report", "donor_roster"
    )
    if split_id != _parent(state, cluster, "clustering_counts", "split_report"):
        raise OperationConflict("NB marker labels and held-out fold ancestry differs")
    split = _accepted_stage(flow, db, state, split_id, "scrna_nb_countsplit")
    return _Chain(fit, aggregate, cluster, split)


def _read_record(db: Connection, lineage: str, fit_receipt: str):
    row = db.execute(
        """SELECT r.*,o.sha256 AS outbox_digest FROM scientific_records r
           LEFT JOIN scientific_outbox o ON r.lineage_id=o.lineage_id
             AND r.revision=o.revision
           WHERE r.lineage_id=? AND r.record_id=? AND r.kind='execution_event'
             AND length(CAST(r.payload AS BLOB)) <= 65536""",
        (lineage, _RECORD + fit_receipt),
    ).fetchone()
    if row is None:
        return None
    record = _projection_event(row)["record"]
    retained = RetainedNBMarkerConfirmation.model_validate_json(_json(record["state"]))
    if (
        record["event"] != _EVENT
        or retained.subject.lineage_id != lineage
        or retained.subject.fit_receipt_sha256 != fit_receipt
    ):
        raise OperationConflict("Marker confirmation record identity changed")
    return retained


def _verified_review(subject, chain, verifier) -> IndependentMarkerReview:
    review = verifier(subject)
    if review is None:
        raise OperationBlocked("independent_marker_confirmation_required")
    if type(review) is not IndependentMarkerReview:
        raise PermissionError("Marker confirmation requires a typed independent review")
    # Decode through the contract even if the verifier returned a model instance.
    review = IndependentMarkerReview.model_validate_json(
        _json(review.model_dump(mode="json"))
    )
    if (
        review.subject_sha256 != subject.sha256()
        or review.reviewer in {stage.envelope["actor"] for stage in chain.stages}
        or review.review_receipt_sha256
        in {stage.receipt_sha256 for stage in chain.stages}
    ):
        raise PermissionError(
            "Marker confirmation requires an independent exact-fit review"
        )
    return review


def _json_slot(executor, stage, port):
    with _open_retained_single_cell_output(executor, stage.outputs[port]) as stream:
        raw = stream.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("NB marker receipt exceeds its metadata budget")
    return decode_receipt_json(raw)


def _read_rows(executor, artifact, report, threshold):
    import pyarrow.parquet as pq

    with _open_retained_single_cell_output(executor, artifact) as stream:
        table = pq.ParquetFile(
            stream, thrift_string_size_limit=65_536, thrift_container_size_limit=16_384
        )
        footer = decode_receipt_json(table.schema_arrow.metadata[b"devbench.result"])
        if (
            not 0 < table.metadata.num_rows <= 100_000
            or footer.get("claim_scope") != "checked_observation"
            or footer.get("request_sha256") != report["native_request_sha256"]
            or footer.get("family_sha256") != report["family_sha256"]
        ):
            raise ValueError("Sealed NB table differs from its native fit receipt")
        return tuple(
            _marker_row(row, threshold)
            for batch in table.iter_batches(
                batch_size=1024, columns=_STATS, use_threads=False
            )
            for row in batch.to_pylist()
        )


def _marker_row(row: dict[str, Any], threshold: float) -> MarkerRow:
    q_value = row["q_value"]
    return MarkerRow(
        gene_id=row["gene_id"],
        score=row["effect"],
        p_value=row["p_value"],
        q_value=q_value,
        test_status=_STATUSES[row["estimate_status"]],
        selected=q_value is not None and q_value <= threshold,
    )


def _verified_reports(executor, chain):
    fit = _json_slot(executor, chain.fit, "marker_fit_report")
    aggregate = _json_slot(executor, chain.aggregation, "aggregation_report")
    split = _json_slot(executor, chain.split, "split_report")
    native = _json_slot(executor, chain.fit, "marker_solver_receipt")
    if (
        fit.get("format") != "devbench.nb-marker-fit/1"
        or fit.get("clinical_release_allowed") is not False
        or fit.get("marker_scope") != "awaiting_independent_confirmation"
        or native.get("solver") != "edger_v4"
        or native.get("test") != "qlf"
        or native.get("genes_sha256") != chain.fit.outputs["marker_fit"].sha256
        or fit.get("marker_fit_sha256") != native["genes_sha256"]
        or fit.get("solver_receipt_sha256")
        != chain.fit.outputs["marker_solver_receipt"].sha256
        or fit.get("design_sha256") != aggregate["design"]["design_sha256"]
        or fit.get("feature_ids_sha256") != aggregate["feature_ids_sha256"]
        or fit.get("family_sha256") != native.get("family_sha256")
    ):
        raise OperationConflict("NB marker fit and retained diagnostics disagree")
    return fit, aggregate, split


def _verify_receipt_chain(executor: TierAExecutor, chain: _Chain) -> None:
    for stage in chain.stages:
        receipt = executor.load_receipt(stage.envelope, attempt_id=stage.attempt_id)
        if receipt is None:
            raise OperationBlocked("nb_marker_receipt_missing")
        if request_digest(receipt.model_dump(mode="json")) != stage.receipt_sha256:
            raise OperationConflict(
                "NB marker fit requires the exact retained receipts"
            )


def _proposal(executor, flow, chain):
    _verify_receipt_chain(executor, chain)
    fit, aggregate, split = _verified_reports(executor, chain)
    threshold = fit["parameters"]["fdr_threshold"]
    context = MarkerContext(
        counts_sha256=split["counts_sha256"],
        cluster_labels_sha256=chain.clustering.outputs["cluster_labels"].sha256,
        cell_ids_sha256=split["cell_ids_sha256"],
        feature_ids_sha256=fit["feature_ids_sha256"],
        design_sha256=fit["design_sha256"],
        contrast_id="nb-marker-" + fit["family_sha256"][:24],
    )
    table = chain.fit.outputs["marker_fit"]
    rows = _read_rows(executor, table, fit, threshold)
    subject = NBMarkerReviewSubject(
        lineage_id=flow.lineage_id,
        fit_step_id=chain.fit.envelope["step"]["step_id"],
        fit_actor=chain.fit.envelope["actor"],
        receipt_chain_sha256=chain.sha256(),
        fit_receipt_sha256=chain.fit.receipt_sha256,
        marker_fit_sha256=table.sha256,
        solver_receipt_sha256=fit["solver_receipt_sha256"],
        rows_sha256=marker_rows_sha256(rows),
        context=context,
        runtime_sha256=chain.fit.envelope["environment_sha256"],
        paired_donors=aggregate["paired_donors"],
        fdr_threshold=threshold,
    )
    return subject, rows, split, fit["solver_version"]


class NBMarkerConfirmationAuthority:
    """Trusted host adapter for promoting a current fit after independent review."""

    def __init__(
        self,
        flow: AdaptiveWorkflow,
        executor: TierAExecutor,
        verifier: MarkerReviewVerifier,
    ):
        if (
            type(flow) is not AdaptiveWorkflow
            or type(executor) is not TierAExecutor
            or executor.ledger.path != flow.ledger.path
            or not callable(verifier)
        ):
            raise PermissionError(
                "Marker confirmation requires the bound host authority"
            )
        self.flow = flow
        self.executor = executor
        self.verifier = verifier

    def confirm(self, step_id: str) -> tuple[MarkerResult, TestedGeneUniverse]:
        flow = self.flow
        flow.ledger._writable()
        with flow._read_connection() as db:
            chain = _chain(flow, db, step_id)
        subject, rows, split, version = _proposal(self.executor, flow, chain)
        review = _verified_review(subject, chain, self.verifier)
        dispersion = {
            key: value
            for key, value in split["dispersion"].items()
            if key != "source_receipt_sha256"
        }
        proof = NegativeBinomialSplitProof(
            context=subject.context,
            rows_sha256=subject.rows_sha256,
            execution_receipt_sha256=subject.fit_receipt_sha256,
            independent_check_sha256=review.review_receipt_sha256,
            runtime_sha256=subject.runtime_sha256,
            method_version=version,
            fdr_threshold=subject.fdr_threshold,
            split_distribution=split["split_distribution"],
            splitting_receipt_sha256=chain.split.receipt_sha256,
            clustering_receipt_sha256=chain.clustering.receipt_sha256,
            clustering_counts_sha256=split["clustering_counts_sha256"],
            inference_counts_sha256=split["inference_counts_sha256"],
            dispersion=DispersionProvenance.model_validate(dispersion),
        )
        result = project_post_clustering_markers(
            rows,
            context=subject.context,
            method="nb_countsplit",
            proof=proof,
            verify_custody=lambda actual: actual == proof,
        )
        if result.marker_scope != "confirmatory_marker_eligible":
            raise OperationBlocked(result.reason)
        universe = TestedGeneUniverse(
            result_receipt_sha256=subject.fit_receipt_sha256,
            gene_statistics_sha256=subject.rows_sha256,
            filter_receipt_sha256=subject.solver_receipt_sha256,
            genes=tuple(row.gene_id for row in rows if row.test_status == "tested"),
            selected=tuple(row.gene_id for row in rows if row.selected),
            selection_origin="post_clustering_markers",
        )
        self._retain(
            RetainedNBMarkerConfirmation(
                subject=subject,
                review=review,
                proof_sha256=content_sha256(proof.model_dump(mode="json")),
                universe_sha256=content_sha256(universe.model_dump(mode="json")),
            )
        )
        return result, universe

    def _retain(self, accepted: RetainedNBMarkerConfirmation) -> None:
        flow, subject = self.flow, accepted.subject
        with flow.authority._execution_tx(flow.lineage_id) as db:
            if (
                _chain(flow, db, subject.fit_step_id).sha256()
                != subject.receipt_chain_sha256
            ):
                raise OperationConflict(
                    "NB marker ancestry changed before confirmation"
                )
            prior = _read_record(db, flow.lineage_id, subject.fit_receipt_sha256)
            if prior is not None:
                if prior != accepted:
                    raise OperationConflict("NB marker confirmation is immutable")
                return
            execution = flow.authority._load_state(db, flow.lineage_id)
            flow.authority._active(db, flow.lineage_id, execution)
            document = accepted.model_dump(mode="json")
            flow.authority._append_execution_record(
                db,
                flow.lineage_id,
                {
                    "kind": "execution_event",
                    "event": _EVENT,
                    "record_id": _RECORD + subject.fit_receipt_sha256,
                    "state": document,
                    "state_sha256": _digest(_json(document)),
                },
            )

    def _current(self, fit_receipt: str) -> RetainedNBMarkerConfirmation | None:
        flow = self.flow
        with flow._read_connection() as db:
            prior = _read_record(db, flow.lineage_id, fit_receipt)
            if prior is None:
                return None
            subject = prior.subject
            try:
                chain = _chain(flow, db, subject.fit_step_id)
            except OperationBlocked:
                return None
            if chain.sha256() != subject.receipt_chain_sha256:
                return None
        try:
            _verify_receipt_chain(self.executor, chain)
            review = _verified_review(subject, chain, self.verifier)
        except (OperationBlocked, OSError):
            return None
        if review != prior.review:
            return None
        with flow._read_connection() as db:
            try:
                same_chain = (
                    _chain(flow, db, subject.fit_step_id).sha256() == chain.sha256()
                )
            except OperationBlocked:
                return None
            if same_chain and _read_record(db, flow.lineage_id, fit_receipt) == prior:
                return prior
        return None

    def verify_marker(self, proof: MarkerProof) -> bool:
        retained = self._current(proof.execution_receipt_sha256)
        return retained is not None and retained.proof_sha256 == content_sha256(
            proof.model_dump(mode="json")
        )

    def verify_universe(self, universe: TestedGeneUniverse) -> bool:
        retained = self._current(universe.result_receipt_sha256)
        return retained is not None and retained.universe_sha256 == content_sha256(
            universe.model_dump(mode="json")
        )

    def require_pathway_fit(
        self,
        universe: TestedGeneUniverse,
        artifact_sha256: str,
        review_sha256: str,
    ) -> RetainedNBMarkerConfirmation:
        retained = self.retained_fit(universe)
        if (
            retained.subject.marker_fit_sha256 != artifact_sha256
            or retained.review.review_receipt_sha256 != review_sha256
        ):
            raise PermissionError(
                "Mounted ORA inputs differ from the confirmed marker fit"
            )
        return retained

    def retained_fit(
        self, universe: TestedGeneUniverse
    ) -> RetainedNBMarkerConfirmation:
        retained = self._current(universe.result_receipt_sha256)
        if retained is None:
            raise OperationBlocked("nb_marker_confirmation_unavailable")
        if retained.universe_sha256 != content_sha256(universe.model_dump(mode="json")):
            raise PermissionError("Mounted ORA requires the confirmed tested universe")
        return retained


def verify_m2_marker_execution(
    result,
    *,
    expression_sha256,
    parameters,
    request_sha256,
    runtime_sha256,
    reference_sha256=None,
):
    """Independently recalculate selections from sealed numerical intermediates.

    The host can check Clipper rejection sets or truncated normal probabilities
    without executing untrusted code. Validating the fitted biological null,
    reference covariance and selection history requires separate scientific
    review. A numerical check of this execution is not that review capability.
    """
    from devbench.core.scrna_marker_inference import (
        MarkerContext,
        MarkerRow,
        marker_rows_sha256,
    )
    from rna_kernels.scrna_cadet import Parameters as CadetParameters
    from rna_kernels.scrna_clusterde import Parameters as ClusterDEParameters

    method = result["method"]
    model = {"clusterde": ClusterDEParameters, "cadet": CadetParameters}[method]
    params = model.model_validate(parameters).model_dump(mode="json")
    context = MarkerContext.model_validate(result["context"])
    rows = tuple(MarkerRow.model_validate(row) for row in result["gene_statistics"])
    features = tuple(row.gene_id for row in rows)
    alpha = params["alpha"]
    if (
        context.counts_sha256 != expression_sha256
        or context.design_sha256 != content_sha256(params)
        or context.feature_ids_sha256 != content_sha256(features)
        or len(set(features)) != len(features)
        or result["rows_sha256"] != marker_rows_sha256(rows)
        or float(result["fdr_threshold"]) != alpha
        or result["evidence_level"] != "L1"
        or result["clinical_release_allowed"] is not False
        or result["independent_review_required"] is not True
    ):
        raise ValueError("M2 selective marker input, row or scientific scope mismatch")
    common = dict(
        context=context,
        rows_sha256=result["rows_sha256"],
        execution_receipt_sha256=content_sha256(
            [
                "m2-marker-execution/1",
                request_sha256,
                runtime_sha256,
                content_sha256(result),
            ]
        ),
        runtime_sha256=runtime_sha256.removeprefix("sha256:"),
        method_version=result["method_version"],
        fdr_threshold=alpha,
    )
    if method == "clusterde":
        proof = _replay_clusterde(common, result, rows)
    else:
        proof = _replay_cadet(
            common, result, rows, params["target_feature"], reference_sha256
        )
    return {
        "proof": proof.model_dump(mode="json"),
        "verification": "numerical_selection_replayed",
        "scientific_custody_verified": False,
        "evidence_level": "L1",
        "clinical_release_allowed": False,
    }


def _replay_clusterde(common, result, rows):
    from devbench.core.scrna_marker_inference import ClusterDEProof

    alpha = common["fdr_threshold"]
    request_sha256 = common["execution_receipt_sha256"]
    null_model = result["null_model"]
    diag = result["null_diagnostic"]
    if (
        content_sha256(null_model) != result["null_model_sha256"]
        or content_sha256(diag) != result["null_diagnostic_sha256"]
        or result["input_population"] != "single_batch"
    ):
        raise ValueError("ClusterDE null model or diagnostic digest differs")
    contrasts = [
        a - b for a, b in zip(diag["target_scores"], diag["null_scores"], strict=True)
    ]
    # Evaluate native Clipper BC independently of the kernel implementation.
    cutoff = next(
        (
            t
            for t in sorted({abs(v) for v in contrasts if v})
            if (1 + sum(v <= -t for v in contrasts))
            <= alpha * sum(v >= t for v in contrasts)
        ),
        None,
    )
    if cutoff != diag["threshold"]:
        raise ValueError("Incorrect ClusterDE threshold")
    for row, contrast in zip(rows, contrasts, strict=True):
        if (
            row.score != contrast
            or row.test_status != "tested"
            or row.p_value is not None
            or row.q_value is not None
            or row.selected != (cutoff is not None and contrast >= cutoff)
        ):
            raise ValueError("ClusterDE selection differs from score contrasts")
    return ClusterDEProof(
        **common,
        independent_check_sha256=content_sha256(
            ["clipper_bc_replay/1", request_sha256, contrasts, cutoff]
        ),
        # No scientific reviewer has independently assessed the fitted null.
        unimodal=False,
        input_population="single_batch",
        null_model_sha256=result["null_model_sha256"],
        null_diagnostic_sha256=result["null_diagnostic_sha256"],
    )


def _replay_cadet(common, result, rows, target, reference_sha256):
    import math

    from scipy.special import log_ndtr, logsumexp

    from devbench.core.scrna_marker_inference import CADETProof

    features = tuple(row.gene_id for row in rows)
    alpha = common["fdr_threshold"]
    request_sha256 = common["execution_receipt_sha256"]
    event = result["selection_event"]
    index = features.index(target)
    row = rows[index]
    if (
        result["null_regime"] != "independent_nb_features_and_units"
        or target != result["target_feature"]
        or result["gaussian_reference_sha256"] != reference_sha256
        or content_sha256(event) != result["selection_event_sha256"]
        or row.test_status != "tested"
        or row.score is None
        or row.p_value is None
        or row.q_value != row.p_value
        or row.selected != (row.p_value <= alpha)
        or any(
            r.test_status != "independently_filtered"
            or r.score is not None
            or r.selected
            or r.p_value is not None
            or r.q_value is not None
            for i, r in enumerate(rows)
            if i != index
        )
    ):
        raise ValueError("CADET target, reference or tested family differs")
    # Sigma_jj = 1 on the quantile-residual scale.
    sizes = event["cluster_sizes"]
    if len(sizes) != 2 or min(sizes) < 1:
        raise ValueError("Invalid CADET cluster sizes")
    sd = math.sqrt(sum(1 / n for n in sizes))
    if not math.isclose(sd, event["contrast_standard_error"]):
        raise ValueError("CADET contrast precision differs")
    z = row.score / sd
    intervals = [
        (-math.inf if lo is None else lo, math.inf if hi is None else hi)
        for lo, hi in event["standardized_intervals"]
    ]
    if (
        not any(lo <= z <= hi for lo, hi in intervals)
        or any(lo >= hi for lo, hi in intervals)
        or any(a[1] >= b[0] for a, b in zip(intervals, intervals[1:], strict=False))
    ):
        raise ValueError("Invalid CADET selection event")

    def logmass(lo, hi):
        if hi <= lo:
            return -math.inf
        if lo >= 0:
            lo, hi = -hi, -lo
        high, low = float(log_ndtr(hi)), float(log_ndtr(lo))
        return -math.inf if high <= low else high + math.log(-math.expm1(low - high))

    denom = logsumexp([logmass(a, b) for a, b in intervals])
    tails = logsumexp(
        [
            m
            for a, b in intervals
            for m in (logmass(a, min(b, -abs(z))), logmass(max(a, abs(z)), b))
        ]
    )
    p = math.exp(min(0, float(tails - denom)))
    if not math.isclose(p, row.p_value, rel_tol=1e-8, abs_tol=0):
        raise ValueError("CADET conditional probability differs")
    return CADETProof(
        **common,
        independent_check_sha256=content_sha256(
            ["truncated_normal_replay/1", request_sha256, p]
        ),
        selection_event_sha256=result["selection_event_sha256"],
        gaussian_reference_sha256=reference_sha256,
        target_feature=target,
    )
