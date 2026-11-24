"""Frozen enrichment families and receipt-gated over-representation tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from math import isfinite
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_serializer, model_validator
from scipy.stats import hypergeom

from devbench.core._hashing import contract_json_bytes
from devbench.core.publication_families import budgeted_bh

from .scientific_types import FrozenContract

if TYPE_CHECKING:
    from devbench.core.scrna_marker_inference import MarkerCustodyVerifier, MarkerResult

PathwayMethod = Literal["camera", "fgsea", "ora", "decoupleR"]
ForegroundDirection = Literal["all", "up_only", "down_only"]
_GENE_LIMIT = 100_000
_SET_LIMIT = 4_096
Identifier = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Probability = Annotated[float, Field(ge=0, le=1)]
GeneAxis = Annotated[
    tuple[Identifier, ...], Field(min_length=1, max_length=_GENE_LIMIT)
]
_ASSUMPTIONS: dict[PathwayMethod, tuple[str, ...]] = {
    "camera": (
        "Competitive gene-set test with residual inter-gene variance inflation.",
        "The fitted contrast uses normalized expression and the verified unit design.",
    ),
    "fgsea": (
        "Pre-ranked gene-set null uses gene-level rank exchangeability under "
        "an inter-gene independence assumption.",
        "Ranking uses all tested genes. Ties use deterministic gene identifier order.",
    ),
    "ora": (
        "Competitive upper-tail hypergeometric test against filtered tested genes.",
        "Null assumes equal selection opportunity within the tested background.",
    ),
    "decoupleR": (
        "Weighted univariate linear model over the supplied signed regulon.",
        "Scores describe activity associations conditional on the supplied network.",
    ),
}


def _distinct(values: Sequence[str], name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must contain distinct identifiers")


class GeneSet(FrozenContract):
    set_id: Identifier
    genes: GeneAxis
    weights: tuple[float, ...] | None = Field(default=None, max_length=_GENE_LIMIT)

    @model_validator(mode="after")
    def _check_axis(self) -> GeneSet:
        _distinct(self.genes, "Gene-set axis")
        if self.weights is not None and (
            len(self.weights) != len(self.genes)
            or any(not isfinite(value) or value == 0 for value in self.weights)
        ):
            raise ValueError("Regulon weights require aligned nonzero finite values")
        return self


class PathwayPlan(FrozenContract):
    method: PathwayMethod | Literal["auto"] = "auto"
    foreground_direction: ForegroundDirection = "all"
    collection_id: Identifier
    collection_version: Identifier
    gene_sets: tuple[GeneSet, ...] = Field(min_length=1, max_length=_SET_LIMIT)
    min_set_size: int = Field(default=15, ge=2, le=_GENE_LIMIT)
    max_set_size: int = Field(default=500, ge=2, le=_GENE_LIMIT)
    seed: int = Field(default=0, ge=0, lt=2**31)

    @model_serializer(mode="wrap")
    def _preserve_undirected_plan(self, serialize):
        fields = serialize(self)
        if self.foreground_direction == "all":
            fields.pop("foreground_direction", None)
        return fields

    @model_validator(mode="after")
    def _check_collection(self) -> PathwayPlan:
        if self.foreground_direction != "all" and self.method != "ora":
            raise ValueError("Foreground direction requires ORA")
        _distinct([item.set_id for item in self.gene_sets], "Gene-set collection")
        if self.min_set_size > self.max_set_size:
            raise ValueError("Gene-set size limits must be ordered")
        if sum(len(item.genes) for item in self.gene_sets) > 1_000_000:
            raise ValueError("Gene-set collection exceeds its membership budget")
        if self.method == "decoupleR" and any(
            item.weights is None for item in self.gene_sets
        ):
            raise ValueError("decoupleR requires a supplied signed weighted network")
        return self


class TestedGeneUniverse(FrozenContract):
    """Expected projection of independently verified test and selection receipts."""

    result_receipt_sha256: Sha256
    gene_statistics_sha256: Sha256
    filter_receipt_sha256: Sha256
    ranked_scores_sha256: Sha256 | None = None
    genes: GeneAxis
    selected: tuple[Identifier, ...] = Field(default=(), max_length=_GENE_LIMIT)
    background_scope: Literal[
        "filtered_tested", "unfiltered_measured", "whole_genome"
    ] = "filtered_tested"
    selection_origin: Literal[
        "registered_de", "post_clustering_markers", "exploratory_marker_ranking"
    ] = "registered_de"

    @model_validator(mode="after")
    def _check_axis(self) -> TestedGeneUniverse:
        _distinct(self.genes, "Tested-gene universe")
        _distinct(self.selected, "Selected-gene axis")
        if not set(self.selected) <= set(self.genes):
            raise ValueError("Selected genes must be within the tested universe")
        return self


class CameraInput(FrozenContract):
    """Identities for aligned gene-by-sample expression and sample-by-term design."""

    expression_sha256: Sha256
    design_sha256: Sha256
    design_receipt_sha256: Sha256
    genes: GeneAxis
    samples: tuple[Identifier, ...] = Field(min_length=4, max_length=4_096)
    biological_units: tuple[Identifier, ...] = Field(min_length=4, max_length=4_096)
    contrast: tuple[float, ...] = Field(min_length=1, max_length=128)
    measurement: Literal["normalized_expression"] = "normalized_expression"
    design_scope: Literal["independent", "blocked"] = "independent"

    @model_validator(mode="after")
    def _check_axes(self) -> CameraInput:
        _distinct(self.genes, "Expression feature axis")
        _distinct(self.samples, "Sample axis")
        if len(self.samples) != len(self.biological_units):
            raise ValueError("Biological units must align to the sample axis")
        if self.design_scope == "independent":
            _distinct(self.biological_units, "Independent biological unit axis")
        elif len(set(self.biological_units)) < 3:
            raise ValueError("Blocked designs require three distinct biological units")
        if not any(self.contrast) or not all(map(isfinite, self.contrast)):
            raise ValueError("CAMERA requires a nonzero finite directed contrast")
        return self


class PathwayRequest(FrozenContract):
    """A mounted worker request constructed only after coordinator admission."""

    format: Literal["devbench.mounted-pathways/1"] = "devbench.mounted-pathways/1"
    plan: PathwayPlan
    method: PathwayMethod
    universe: TestedGeneUniverse | None = None
    camera: CameraInput | None = None
    foreground: tuple[Identifier, ...] = Field(default=(), max_length=_GENE_LIMIT)
    ranked_scores: tuple[float, ...] = Field(default=(), max_length=_GENE_LIMIT)
    marker_rows_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def _check_method(self) -> PathwayRequest:
        if self.plan.method not in ("auto", self.method):
            raise ValueError("Mounted pathway method differs from the frozen plan")
        if self.method == "camera":
            if (
                self.camera is None
                or self.universe is not None
                or self.ranked_scores
                or self.foreground
                or self.marker_rows_sha256
            ):
                raise ValueError("CAMERA requires its expression and design evidence")
        else:
            self._check_list_fields()
        return self

    def _check_list_fields(self) -> None:
        if self.universe is None or self.camera is not None:
            raise ValueError("Gene-list analysis requires its exact tested universe")
        _check_universe_scope(self.universe)
        if self.method == "ora":
            if self.ranked_scores:
                raise ValueError("Unexpected ranking input for ORA")
            _check_foreground(
                self.universe, self.foreground, self.plan.foreground_direction
            )
            self._check_marker_field(self.universe)
        elif self.foreground or self.marker_rows_sha256 is not None:
            raise ValueError("Unexpected selected foreground for the mounted method")
        else:
            if self.universe.selection_origin == "post_clustering_markers":
                raise PermissionError(
                    "Post-clustering scores require a qualified ranking route"
                )
            _check_ranks(self.universe, self.ranked_scores)

    def _check_marker_field(self, universe: TestedGeneUniverse) -> None:
        if universe.selection_origin == "post_clustering_markers":
            if self.marker_rows_sha256 != universe.gene_statistics_sha256:
                raise PermissionError("Mounted ORA requires a qualified marker receipt")
        elif self.marker_rows_sha256 is not None:
            raise ValueError("Unexpected marker evidence")

    @property
    def genes(self) -> tuple[str, ...]:
        if self.camera is not None:
            return self.camera.genes
        assert self.universe is not None
        return self.universe.genes


class PathwayRow(FrozenContract):
    set_id: Identifier
    tested_members: int = Field(ge=0)
    status: Literal["estimated", "not_tested", "not_estimable"]
    reason: Identifier | None = None
    overlap_count: int | None = Field(default=None, ge=0)
    score: float | None = None
    direction: Literal["up", "down"] | None = None
    inter_gene_correlation: Annotated[float, Field(ge=-1, le=1)] | None = None
    variance_inflation: Annotated[float, Field(ge=1)] | None = None
    p_value: Probability | None = None
    q_value: Probability | None = None

    @model_validator(mode="after")
    def _check_estimate(self) -> PathwayRow:
        if self.status != "estimated":
            if self.reason is None or any(
                value is not None
                for value in (
                    self.overlap_count,
                    self.score,
                    self.direction,
                    self.p_value,
                    self.q_value,
                    self.inter_gene_correlation,
                    self.variance_inflation,
                )
            ):
                raise ValueError(
                    "Untested pathway rows require a reason and null estimates"
                )
        elif (
            (self.p_value is None) != (self.q_value is None)
            or self.p_value is not None
            and self.q_value is not None
            and self.q_value < self.p_value
        ):
            raise ValueError("Pathway probabilities require 0 <= p <= q <= 1")
        return self


class PathwayResult(FrozenContract):
    format: Literal["devbench.rna-pathway-result/1"] = "devbench.rna-pathway-result/1"
    method: PathwayMethod
    request_sha256: Sha256
    status: Literal["completed", "unavailable", "failed"]
    reason: Identifier | None = None
    results: tuple[PathwayRow, ...] = Field(default=(), max_length=_SET_LIMIT)
    tested_gene_count: int = Field(ge=0)
    attempted_set_count: int = Field(ge=0)
    tested_set_count: int = Field(ge=0)
    assumptions: tuple[str, ...]
    package_version: str | None = None
    rank_ties: int | None = Field(default=None, ge=0)
    claim_scope: Literal["receipt_bound_enrichment", "exploratory_activity", "none"]
    causal_binding_evidence: Literal["unassessed"] = "unassessed"
    clinical_release_allowed: Literal[False] = False

    @model_validator(mode="after")
    def _check_claim(self) -> PathwayResult:
        if self.status != "completed" and (
            self.results
            or self.reason is None
            or self.attempted_set_count
            or self.tested_set_count
            or self.claim_scope != "none"
        ):
            raise ValueError("Unavailable analyses cannot carry inferential results")
        if self.attempted_set_count != sum(
            row.status != "not_tested" for row in self.results
        ) or self.tested_set_count != sum(
            row.status == "estimated" for row in self.results
        ):
            raise ValueError("Pathway fit counts differ from the retained results")
        if self.method == "decoupleR" and any(
            row.p_value is not None or row.q_value is not None for row in self.results
        ):
            raise ValueError(
                "Activity scores must remain separate from confirmatory tests"
            )
        return self


UniverseVerifier = Callable[[TestedGeneUniverse], bool]
DesignVerifier = Callable[[CameraInput], bool]


def _require_universe(
    universe: TestedGeneUniverse,
    verify_universe: UniverseVerifier | None,
) -> None:
    _check_universe_scope(universe)
    if not callable(verify_universe) or verify_universe(universe) is not True:
        raise PermissionError("Tested-gene and filtering receipts are unverified")


def _check_universe_scope(universe: TestedGeneUniverse) -> None:
    if universe.background_scope != "filtered_tested":
        raise PermissionError(
            "ORA and ranked analysis require the filtered tested background"
        )
    if universe.selection_origin == "exploratory_marker_ranking":
        raise PermissionError("exploratory_marker_ranking cannot supply this analysis")


def _check_foreground(
    universe: TestedGeneUniverse,
    foreground: tuple[str, ...],
    direction: ForegroundDirection,
) -> None:
    genes = set(foreground)
    selected = set(universe.selected)
    if (
        not genes
        or len(genes) != len(foreground)
        or not genes <= selected
        or not genes <= set(universe.genes)
        or (direction == "all" and genes != selected)
    ):
        raise PermissionError("ORA requires the exact nonempty frozen foreground")


def matches_foreground_direction(
    effect: float | None, direction: ForegroundDirection
) -> bool:
    if direction == "all":
        return True
    return (
        effect is not None
        and isfinite(effect)
        and (effect > 0 if direction == "up_only" else effect < 0)
    )


def _check_ranks(universe: TestedGeneUniverse, scores: tuple[float, ...]) -> None:
    if len(scores) != len(universe.genes) or not all(map(isfinite, scores)):
        raise ValueError("Ranked analysis requires finite scores for every tested gene")
    if len(set(scores)) < 2:
        raise ValueError("Ranked analysis requires a nonconstant statistic")
    if ranked_scores_sha256(universe.genes, scores) != universe.ranked_scores_sha256:
        raise PermissionError(
            "Ranked statistics differ from the verified result receipt"
        )


def ranked_scores_sha256(genes: tuple[str, ...], scores: tuple[float, ...]) -> str:
    """Bind the complete measured gene axis and its ordered ranking statistic."""
    return sha256(contract_json_bytes([genes, scores])).hexdigest()


def _verified_markers(
    universe: TestedGeneUniverse,
    foreground: tuple[str, ...],
    marker_result: MarkerResult | None,
    verify_marker: MarkerCustodyVerifier | None,
    direction: ForegroundDirection,
) -> str | None:
    if marker_result is None:
        if universe.selection_origin == "post_clustering_markers":
            raise PermissionError("Post-clustering foreground requires marker evidence")
        return None
    from devbench.core.scrna_marker_inference import (
        marker_rows_sha256,
        require_confirmatory_marker_ora,
    )

    require_confirmatory_marker_ora(
        marker_result,
        foreground=foreground,
        tested_background=universe.genes,
        verify_custody=verify_marker,
        direction=direction,
    )
    assert marker_result.proof is not None
    digest = marker_rows_sha256(marker_result.gene_statistics)
    if (
        universe.selection_origin != "post_clustering_markers"
        or universe.result_receipt_sha256
        != marker_result.proof.execution_receipt_sha256
        or universe.gene_statistics_sha256 != digest
    ):
        raise PermissionError("Marker selection differs from its tested-gene receipt")
    return digest


def _method_for_plan(plan: PathwayPlan, camera: CameraInput | None) -> PathwayMethod:
    if plan.method != "auto":
        return plan.method
    if camera is None:
        raise ValueError(
            "Select a gene-list method explicitly or supply the CAMERA design"
        )
    return "camera"


def admit_pathway_request(
    plan: PathwayPlan,
    *,
    universe: TestedGeneUniverse | None = None,
    verify_universe: UniverseVerifier | None = None,
    camera: CameraInput | None = None,
    verify_design: DesignVerifier | None = None,
    foreground: Sequence[str] = (),
    ranked_scores: Mapping[str, float] | None = None,
    marker_result: MarkerResult | None = None,
    verify_marker: MarkerCustodyVerifier | None = None,
) -> PathwayRequest:
    """Check custody at the host and freeze the selected operation and inputs."""
    method = _method_for_plan(plan, camera)
    if method == "camera":
        if (
            camera is None
            or not callable(verify_design)
            or verify_design(camera) is not True
        ):
            raise PermissionError(
                "CAMERA requires its independently checked design receipt"
            )
        if universe is not None or foreground or ranked_scores is not None:
            raise ValueError("CAMERA request has unexpected gene-list inputs")
        return PathwayRequest(plan=plan, method=method, camera=camera)
    if universe is None or camera is not None:
        raise ValueError("Gene-list analysis requires its exact tested universe")
    _require_universe(universe, verify_universe)
    if method == "ora":
        if isinstance(foreground, str):
            raise PermissionError("ORA requires the exact nonempty frozen foreground")
        chosen = tuple(foreground)
        _check_foreground(universe, chosen, plan.foreground_direction)
        proof = _verified_markers(
            universe, chosen, marker_result, verify_marker, plan.foreground_direction
        )
        return PathwayRequest(
            plan=plan,
            method=method,
            universe=universe,
            foreground=chosen,
            marker_rows_sha256=proof,
        )
    if ranked_scores is None or set(ranked_scores) != set(universe.genes):
        raise ValueError("Ranked analysis requires finite scores for every tested gene")
    if universe.selection_origin == "post_clustering_markers":
        raise PermissionError(
            "Post-clustering scores require a qualified ranking route"
        )
    return PathwayRequest(
        plan=plan,
        method=method,
        universe=universe,
        ranked_scores=tuple(ranked_scores[gene] for gene in universe.genes),
    )


def eligible_members(request: PathwayRequest) -> tuple[tuple[str, ...], ...]:
    universe = set(request.genes)
    return tuple(
        tuple(gene for gene in item.genes if gene in universe)
        for item in request.plan.gene_sets
    )


def testable_set(request: PathwayRequest, members: Sequence[str]) -> bool:
    return request.plan.min_set_size <= len(
        members
    ) <= request.plan.max_set_size and len(members) < len(request.genes)


def not_tested(set_id: str, members: Sequence[str]) -> PathwayRow:
    return PathwayRow(
        set_id=set_id,
        tested_members=len(members),
        status="not_tested",
        reason="frozen_set_size_or_complement",
    )


def result_for_request(
    request: PathwayRequest,
    *,
    status: Literal["completed", "unavailable", "failed"] = "completed",
    reason: str | None = None,
    rows: Sequence[PathwayRow] = (),
    package_version: str | None = None,
) -> PathwayResult:
    completed = status == "completed"
    return PathwayResult(
        method=request.method,
        request_sha256=sha256(
            contract_json_bytes(request.model_dump(mode="json"))
        ).hexdigest(),
        status=status,
        reason=reason,
        results=tuple(rows),
        tested_gene_count=len(request.genes),
        attempted_set_count=sum(row.status != "not_tested" for row in rows),
        tested_set_count=sum(row.status == "estimated" for row in rows),
        assumptions=_ASSUMPTIONS[request.method],
        package_version=package_version,
        rank_ties=(
            len(request.ranked_scores) - len(set(request.ranked_scores))
            if request.ranked_scores
            else None
        ),
        claim_scope=(
            "none"
            if not completed
            else "exploratory_activity"
            if request.method == "decoupleR"
            else "receipt_bound_enrichment"
        ),
    )


def with_bh(rows: Sequence[PathwayRow]) -> tuple[PathwayRow, ...]:
    """Keep failed fits in the denominator and prefiltered sets outside it."""
    family = [row for row in rows if row.status != "not_tested"]
    if not family:
        return tuple(rows)
    adjusted = budgeted_bh([row.p_value for row in family], "1", pad_failed=True)
    values = {
        row.set_id: entry["q_local"]
        for row, entry in zip(
            family,
            adjusted,
            strict=True,
        )
    }
    return tuple(
        row.model_copy(update={"q_value": max(row.p_value, float(values[row.set_id]))})
        if row.p_value is not None
        else row
        for row in rows
    )


def execute_ora(request: PathwayRequest) -> PathwayResult:
    """Exact upper-tail competitive ORA over the frozen tested universe."""
    if request.method != "ora" or request.universe is None:
        raise ValueError("Expected an admitted ORA request")
    foreground = set(request.foreground)
    rows = []
    for item, members in zip(
        request.plan.gene_sets, eligible_members(request), strict=True
    ):
        if not testable_set(request, members):
            rows.append(not_tested(item.set_id, members))
            continue
        observed = len(foreground.intersection(members))
        p = float(
            hypergeom.sf(
                observed - 1,
                len(request.genes),
                len(members),
                len(foreground),
            )
        )
        rows.append(
            PathwayRow(
                set_id=item.set_id,
                tested_members=len(members),
                status="estimated",
                overlap_count=observed,
                p_value=p,
                q_value=p,
            )
        )
    return result_for_request(request, rows=with_bh(rows))


def run_ora(
    plan: PathwayPlan,
    universe: TestedGeneUniverse,
    *,
    foreground: Sequence[str],
    verify_universe: UniverseVerifier | None = None,
    marker_result: MarkerResult | None = None,
    verify_marker: MarkerCustodyVerifier | None = None,
) -> PathwayResult:
    return execute_ora(
        admit_pathway_request(
            plan,
            universe=universe,
            foreground=foreground,
            verify_universe=verify_universe,
            marker_result=marker_result,
            verify_marker=verify_marker,
        )
    )
