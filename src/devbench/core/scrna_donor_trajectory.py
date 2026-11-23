"""Fixed spline contrasts over independent donor curves on shared pseudotime.

The Gaussian endpoint accepts continuous, normalized expression. It does not
provide a negative-binomial count fit, infer an axis, or test lineage topology.
"""

import csv
import hashlib
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, Self, TypedDict

import h5py
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from scipy.interpolate import BSpline
from scipy.sparse import csr_array
from scipy.stats import f

from ._files import read_bounded_file
from ._hashing import canonical_sha256
from .design import require_design_condition
from .h5ad_gene_blocks import H5adGeneBlocks
from .population_data import bound_query_file
from .publication_families import Sha, budgeted_bh
from .trajectory_evidence import DonorTrajectoryEvidence

_MAX_EXPRESSION_BYTES = 512 * 1024**2
_MAX_COORDINATE_BYTES = 16 * 1024**2
_MAX_CELLS = 100_000
_MAX_GENES = 20_000
_MAX_DONORS = 256
_MIN_DONORS_PER_ARM = 3
_MIN_INFERENCE_RESIDUAL_DF = 2
_COORDINATE_COLUMNS = ("cell_id", "donor_id", "condition", "pseudotime")
_DEGREE = 3
_Column = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")]
_Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_Floats = NDArray[np.float64]
_Arm = tuple[str, str]


class DonorTrajectorySpec(BaseModel):
    """Freeze the analysis inputs and spline specification before fitting.

    `knot_count` includes both endpoints on the common [0, 1] pseudotime scale.
    A trusted coordinator verifies the prospective declaration and independent
    axis evidence when `intent` is confirmatory.
    """

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )

    format: Literal["devbench.donor-trajectory/1"] = "devbench.donor-trajectory/1"
    method: Literal["donor_fixed_spline", "tradeSeq"] = "donor_fixed_spline"
    expression_sha256: Sha
    coordinates_sha256: Sha
    original_counts_sha256: Sha
    normalization_receipt_sha256: Sha
    axis_receipt_sha256: Sha
    layer: _Column | None = None
    measurement: Literal["normalized_expression", "raw_counts", "rounded_counts"]
    axis_kind: Literal["inferred_pseudotime", "collection_age"]
    axis_selection: Literal["same_expression", "independent", "unknown"]
    intent: Literal["exploratory", "confirmatory"]
    design: Literal["independent", "paired"]
    knot_count: int = Field(ge=2, le=8)
    bin_count: int = Field(ge=5, le=64)
    min_cells_per_bin: int = Field(ge=1)
    numerator: _Label
    denominator: _Label
    technical_factors: tuple[_Column, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def check_design_roles(self) -> Self:
        if self.numerator == self.denominator:
            raise ValueError("distinct_trajectory_conditions_required")
        if len(set(self.technical_factors)) != len(self.technical_factors) or set(
            self.technical_factors
        ).intersection(_COORDINATE_COLUMNS):
            raise ValueError("distinct_trajectory_technical_roles_required")
        if self.bin_count <= self.knot_count + 2:
            raise ValueError(
                "frozen_bins_must_allow_spline_residual_degrees_of_freedom"
            )
        return self


class _Cell(NamedTuple):
    donor: str
    condition: str
    pseudotime: float
    technical: tuple[str, ...]


@dataclass(frozen=True)
class _Bins:
    weights: csr_array
    anchor_columns: NDArray[np.intp]
    spline_projection: _Floats
    direction: _Floats
    minimum_cells: int
    maximum_cells: int
    knot_positions: tuple[float, ...]


@dataclass(frozen=True)
class _DonorModel:
    paired: bool
    matrix: _Floats
    projection: _Floats
    contrast_index: int
    variance_scale: float
    diagnostics: dict

    def modeled_curves(self, donor_arm_curves: _Floats) -> _Floats:
        if not self.paired:
            return donor_arm_curves
        control, treated = np.split(donor_arm_curves, 2)
        return treated - control


class TrajectoryGeneStatistic(TypedDict):
    gene_id: str
    effect: float | None
    p: float | None
    q: float | None
    estimate_status: Literal["estimated", "exploratory", "not_estimable"]
    missing_reason: str | None


class DonorTrajectoryResult(TypedDict):
    format: Literal["devbench.donor-trajectory-result/1"]
    specification_sha256: str
    status: Literal["completed", "no_estimable_genes"]
    input_sha256s: dict[str, str]
    input_receipts: dict[str, str]
    method: Literal["donor_fixed_cubic_regression_spline"]
    claim_scope: Literal["candidate_research_inference", "exploratory_only"]
    clinical_release_allowed: Literal[False]
    axis_kind: Literal["inferred_pseudotime"]
    axis_selection: Literal["same_expression", "independent", "unknown"]
    conditions: dict[str, str]
    n_cells: int
    n_donors: int
    donors_per_condition: dict[str, int]
    support: dict[str, int | bool | list[float]]
    condition_number_validated: Literal[True]
    design_diagnostics: dict[str, Any]
    effect_definition: str
    test_definition: str
    limitations: list[str]
    bh_family_size: int | None
    gene_statistics: list[TrajectoryGeneStatistic]


def _execution_scope(
    spec: DonorTrajectorySpec,
    verify_inference: Callable[[DonorTrajectorySpec], bool] | None,
) -> bool:
    if spec.method == "tradeSeq":
        raise ValueError("cell_level_tradeSeq_refused: use a donor-level fit")
    if spec.axis_kind != "inferred_pseudotime":
        raise ValueError("inferred_pseudotime_required: collection age is a reference")
    if spec.measurement != "normalized_expression" or (
        spec.layer and spec.layer.casefold() in {"counts", "raw", "raw_counts"}
    ):
        raise ValueError("normalized_expression_required: use a supported count model")
    if spec.intent == "exploratory":
        return False
    if spec.axis_selection != "independent":
        raise ValueError("independent_axis_evidence_required")
    if verify_inference is None or verify_inference(spec) is not True:
        raise ValueError("trajectory_inference_authorization_required")
    return True


def _declared_label(value: str | None) -> str:
    if value is None or not value or len(value) > 200 or value != value.strip():
        raise ValueError("trajectory_requires_nonmissing_exact_identifiers")
    return value


def _coordinate(row: dict[str, str], spec: DonorTrajectorySpec) -> tuple[str, _Cell]:
    if None in row or any(value is None for value in row.values()):
        raise ValueError("trajectory_coordinate_row_width_mismatch")
    try:
        time = float(row["pseudotime"])
    except ValueError as error:
        raise ValueError("pseudotime_requires_numeric_coordinates") from error
    if not 0 <= time <= 1:
        raise ValueError("pseudotime_outside_frozen_shared_support")
    condition = _declared_label(row["condition"])
    if condition not in {spec.numerator, spec.denominator}:
        raise ValueError("unexpected_trajectory_condition")
    return _declared_label(row["cell_id"]), _Cell(
        _declared_label(row["donor_id"]),
        condition,
        time,
        tuple(_declared_label(row[column]) for column in spec.technical_factors),
    )


def _read_coordinates(path: Path, spec: DonorTrajectorySpec) -> dict[str, _Cell]:
    content = read_bounded_file(path, _MAX_COORDINATE_BYTES)
    if hashlib.sha256(content).hexdigest() != spec.coordinates_sha256:
        raise ValueError("trajectory_coordinates_digest_mismatch")
    expected = {*_COORDINATE_COLUMNS, *spec.technical_factors}
    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8"), newline=""), strict=True
    )
    columns = reader.fieldnames or []
    if len(columns) != len(expected) or set(columns) != expected:
        raise ValueError("trajectory_coordinate_fields_mismatch")
    cells: dict[str, _Cell] = {}
    for row in reader:
        barcode, cell = _coordinate(row, spec)
        if barcode in cells or len(cells) == _MAX_CELLS:
            raise ValueError("trajectory_coordinate_ids_duplicate_or_over_budget")
        cells[barcode] = cell
    return cells


def _donor_arms(cells: Sequence[_Cell], spec: DonorTrajectorySpec) -> tuple[_Arm, ...]:
    control = {cell.donor for cell in cells if cell.condition == spec.denominator}
    treated = {cell.donor for cell in cells if cell.condition == spec.numerator}
    if min(len(control), len(treated)) < _MIN_DONORS_PER_ARM:
        raise ValueError("trajectory_needs_at_least_three_donors_per_condition")
    if len(control | treated) > _MAX_DONORS:
        raise ValueError("trajectory_donor_budget_exceeded")
    if spec.design == "paired" and control != treated:
        raise ValueError("trajectory_requires_complete_donor_pairs")
    if spec.design == "independent" and control.intersection(treated):
        raise ValueError("repeated_donors_require_paired_design")
    return (
        *((donor, spec.denominator) for donor in sorted(control)),
        *((donor, spec.numerator) for donor in sorted(treated)),
    )


def _technical_design(
    cells: Sequence[_Cell],
    arms: tuple[_Arm, ...],
    spec: DonorTrajectorySpec,
) -> tuple[_Floats, list[str]]:
    profiles: dict[_Arm, tuple[str, ...]] = {}
    for cell in cells:
        key = (cell.donor, cell.condition)
        if profiles.setdefault(key, cell.technical) != cell.technical:
            raise ValueError("trajectory_technical_levels_vary_within_donor_arm")
    labels = [profiles[arm] for arm in arms]
    columns: list[_Floats] = []
    terms: list[str] = []
    for index, factor in enumerate(spec.technical_factors):
        levels = [values[index] for values in labels]
        unique = sorted(set(levels))
        if len(unique) > 16:
            raise ValueError("trajectory_technical_factor_levels_over_budget")
        for level in unique[1:]:
            columns.append(
                np.asarray([value == level for value in levels], dtype=float)
            )
            terms.append(f"{factor}={level}")
    if not columns:
        return np.empty((len(arms), 0)), terms
    return np.column_stack(columns), terms


def _donor_model(
    cells: Sequence[_Cell],
    arms: tuple[_Arm, ...],
    spec: DonorTrajectorySpec,
) -> _DonorModel:
    nuisance, terms = _technical_design(cells, arms, spec)
    paired = spec.design == "paired"
    canceled = []
    if paired:
        control, treated = np.split(nuisance, 2)
        difference = treated - control
        changes = np.any(difference != 0, axis=0)
        canceled = [term for term, keep in zip(terms, changes, strict=True) if not keep]
        terms = [term for term, keep in zip(terms, changes, strict=True) if keep]
        matrix = np.column_stack((np.ones(len(control)), difference[:, changes]))
        model_terms = ["paired_condition_difference", *terms]
        contrast_index = 0
    else:
        indicator = [condition == spec.numerator for _, condition in arms]
        matrix = np.column_stack((np.ones(len(arms)), indicator, nuisance))
        model_terms = ["intercept", "condition", *terms]
        contrast_index = 1
    diagnostics = require_design_condition(matrix)
    shape_parameters = spec.knot_count + 1
    denominator_df = diagnostics["residual_df"] - shape_parameters + 1
    if denominator_df < _MIN_INFERENCE_RESIDUAL_DF:
        raise ValueError("insufficient_donor_degrees_of_freedom_for_frozen_spline")
    projection = np.linalg.pinv(matrix)
    weights = projection[contrast_index]
    return _DonorModel(
        paired,
        matrix,
        projection,
        contrast_index,
        float(weights @ weights),
        {
            **diagnostics,
            "model_terms": model_terms,
            "within_pair_canceled_technical_terms": canceled,
            "shape_test_df": [shape_parameters, denominator_df],
        },
    )


def _pseudotime_bins(
    cells: Sequence[_Cell],
    arms: tuple[_Arm, ...],
    spec: DonorTrajectorySpec,
) -> _Bins:
    times = np.asarray([cell.pseudotime for cell in cells])
    index_by_arm = {arm: index for index, arm in enumerate(arms)}
    arm_ids = np.asarray(
        [index_by_arm[cell.donor, cell.condition] for cell in cells],
        dtype=np.intp,
    )
    bins = np.minimum((times * spec.bin_count).astype(int), spec.bin_count - 1)
    rows = arm_ids * spec.bin_count + bins
    group_bins = len(arms) * spec.bin_count
    counts = np.bincount(rows, minlength=group_bins)
    if np.any(counts < spec.min_cells_per_bin):
        raise ValueError("insufficient_within_donor_pseudotime_support")
    grid = np.array([0.5, spec.bin_count - 0.5]) / spec.bin_count
    observed_min = np.full(len(arms), np.inf)
    observed_max = np.full(len(arms), -np.inf)
    np.minimum.at(observed_min, arm_ids, times)
    np.maximum.at(observed_max, arm_ids, times)
    if np.any(observed_min > grid[0]) or np.any(observed_max < grid[1]):
        raise ValueError("trajectory_contrast_requires_observed_support")
    cell_indexes = np.arange(len(cells))
    first_cell = np.full(len(arms), len(cells))
    np.minimum.at(first_cell, arm_ids, cell_indexes)
    weights = csr_array(
        (1 / counts[rows], (rows, cell_indexes)),
        shape=(group_bins, len(cells)),
    )
    positions = np.linspace(0, 1, spec.knot_count)
    knots = np.r_[[0.0] * _DEGREE, positions, [1.0] * _DEGREE]
    spline = BSpline.design_matrix(times, knots, _DEGREE)
    basis = (weights @ spline).toarray().reshape(len(arms), spec.bin_count, -1)
    # The B-spline basis sums to one. Use a unit intercept and drop that
    # coordinate in the shape comparison to remove donor-level shifts.
    basis[:, :, 0] = 1.0
    for matrix in basis:
        require_design_condition(matrix)
    grid_spline = BSpline.design_matrix(grid, knots, _DEGREE).toarray()
    return _Bins(
        weights,
        first_cell[arm_ids],
        np.linalg.pinv(basis)[:, 1:, :],
        grid_spline[1, 1:] - grid_spline[0, 1:],
        int(counts.min()),
        int(counts.max()),
        tuple(float(value) for value in positions),
    )


def _shape_probability(
    model: _DonorModel,
    curves: _Floats,
    parameters: _Floats,
) -> float | None:
    df_error = model.diagnostics["residual_df"]
    shape_df, denominator_df = model.diagnostics["shape_test_df"]
    residuals = curves - model.matrix @ parameters
    covariance = model.variance_scale * (residuals.T @ residuals) / df_error
    if not np.isfinite(covariance).all():
        return None
    contrast = parameters[model.contrast_index]
    try:
        if np.linalg.matrix_rank(covariance) < shape_df:
            return None
        distance = float(contrast @ np.linalg.solve(covariance, contrast))
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(distance) or distance < 0:
        return None
    statistic = distance * (denominator_df / (df_error * shape_df))
    probability = float(f.sf(statistic, shape_df, denominator_df))
    return probability if 0 <= probability <= 1 else None


def _estimate_gene(
    gene: str,
    donor_arm_curves: _Floats,
    bins: _Bins,
    model: _DonorModel,
    *,
    inferential: bool,
) -> TrajectoryGeneStatistic:
    curves = model.modeled_curves(donor_arm_curves)
    parameters = model.projection @ curves
    probability = _shape_probability(model, curves, parameters) if inferential else None
    effect = float(parameters[model.contrast_index] @ bins.direction)
    if not np.isfinite(effect) or inferential and probability is None:
        return {
            "gene_id": gene,
            "effect": None,
            "p": None,
            "q": None,
            "estimate_status": "not_estimable",
            "missing_reason": "singular_donor_covariance_or_nonfinite_contrast",
        }
    return {
        "gene_id": gene,
        "effect": effect,
        "p": probability,
        "q": None,
        "estimate_status": "estimated" if inferential else "exploratory",
        "missing_reason": None,
    }


def _gene_statistics(
    store: H5adGeneBlocks,
    bins: _Bins,
    model: _DonorModel,
    *,
    inferential: bool,
) -> list[TrajectoryGeneStatistic]:
    statistics: list[TrajectoryGeneStatistic] = []
    arms, _, n_bins = bins.spline_projection.shape
    for features, block in store.iter_gene_chunks(128):
        if block.dtype.kind != "f":
            raise ValueError("trajectory_requires_continuous_float_storage")
        if not np.isfinite(block).all():
            raise ValueError("trajectory_expression_contains_nonfinite_values")
        # Center each donor/arm before averaging. Its separate spline intercept
        # absorbs the subtraction and exactly constant genes stay constant.
        centered = block.astype(np.float64, copy=True)
        centered -= centered[:, bins.anchor_columns]
        means = (bins.weights @ centered.T).reshape(arms, n_bins, len(features))
        curves = bins.spline_projection @ means
        statistics.extend(
            _estimate_gene(
                gene, curves[:, :, index], bins, model, inferential=inferential
            )
            for index, gene in enumerate(features)
        )
    if inferential:
        probabilities = [row["p"] for row in statistics]
        adjusted = budgeted_bh(probabilities, "1", pad_failed=True)
        for row, result in zip(statistics, adjusted, strict=True):
            if row["p"] is not None:
                row["q"] = max(row["p"], result["q_local"])
    return statistics



def _check_trajectory_evidence(
    spec: DonorTrajectorySpec,
    evidence: DonorTrajectoryEvidence,
    inferential: bool,
) -> None:
    if not inferential or (
        evidence.specification_sha256,
        evidence.normalized_expression_sha256,
        evidence.coordinates_sha256,
        evidence.original_counts_sha256,
        evidence.normalization_receipt_sha256,
        evidence.axis_receipt_sha256,
    ) != (
        canonical_sha256(spec.model_dump(mode="json")),
        spec.expression_sha256,
        spec.coordinates_sha256,
        spec.original_counts_sha256,
        spec.normalization_receipt_sha256,
        spec.axis_receipt_sha256,
    ):
        raise ValueError("trajectory_evidence_subject_changed")

def fit_donor_trajectory(
    expression_path: Path,
    coordinates_path: Path,
    spec: DonorTrajectorySpec,
    *,
    verify_inference: Callable[[DonorTrajectorySpec], bool] | None = None,
    evidence: DonorTrajectoryEvidence | None = None,
) -> DonorTrajectoryResult:
    """Fit fixed cubic curves on an explicitly comparable pseudotime window.

    The declaration represents one selected shared lineage on a frozen [0, 1]
    scale. Covariates are categorical and constant within each donor/arm.
    For a paired design, inference uses differences from complete donor pairs.
    A confirmatory verifier must check the exact specification against trusted
    records for prospective design, normalization, and axis/feature separation.
    """
    spec = DonorTrajectorySpec.model_validate(spec)
    inferential = _execution_scope(spec, verify_inference)
    if evidence is not None:
        _check_trajectory_evidence(spec, evidence, inferential)
    if expression_path.suffix.lower() != ".h5ad":
        raise ValueError("trajectory_requires_bounded_normalized_h5ad")
    cells_by_id = _read_coordinates(coordinates_path, spec)
    with (
        bound_query_file(
            expression_path, spec.expression_sha256, _MAX_EXPRESSION_BYTES
        ) as stream,
        h5py.File(stream, "r") as handle,
    ):
        store = H5adGeneBlocks(
            handle,
            layer=spec.layer,
            max_cells=_MAX_CELLS,
            max_genes=_MAX_GENES,
        )
        if evidence is not None and canonical_sha256(
            store.gene_names
        ) != evidence.feature_axis_sha256:
            raise ValueError("trajectory_feature_axis_changed")
        if set(store.sample_names) != set(cells_by_id):
            raise ValueError("trajectory_coordinate_ids_must_match_expression")
        cells = [cells_by_id[identity] for identity in store.sample_names]
        arms = _donor_arms(cells, spec)
        model = _donor_model(cells, arms, spec)
        bins = _pseudotime_bins(cells, arms, spec)
        results = _gene_statistics(store, bins, model, inferential=inferential)
    has_estimates = any(row["estimate_status"] != "not_estimable" for row in results)
    return {
        "format": "devbench.donor-trajectory-result/1",
        "specification_sha256": canonical_sha256(spec.model_dump(mode="json")),
        "status": "completed" if has_estimates else "no_estimable_genes",
        "input_sha256s": {
            "expression": spec.expression_sha256,
            "coordinates": spec.coordinates_sha256,
            "original_counts": spec.original_counts_sha256,
        },
        "input_receipts": {
            "normalization": spec.normalization_receipt_sha256,
            "axis": spec.axis_receipt_sha256,
        },
        "method": "donor_fixed_cubic_regression_spline",
        "claim_scope": "candidate_research_inference"
        if inferential
        else "exploratory_only",
        "clinical_release_allowed": False,
        "axis_kind": "inferred_pseudotime",
        "axis_selection": spec.axis_selection,
        "conditions": {"numerator": spec.numerator, "denominator": spec.denominator},
        "n_cells": len(cells),
        "n_donors": len({donor for donor, _ in arms}),
        "donors_per_condition": {
            group: sum(arm == group for _, arm in arms)
            for group in (spec.denominator, spec.numerator)
        },
        "support": {
            "knot_count": spec.knot_count,
            "knot_positions": list(bins.knot_positions),
            "bin_count": spec.bin_count,
            "minimum_cells_in_bin": bins.minimum_cells,
            "maximum_cells_in_bin": bins.maximum_cells,
            "effect_pseudotime_bounds": [
                0.5 / spec.bin_count,
                (spec.bin_count - 0.5) / spec.bin_count,
            ],
            "exclusions_applied": False,
        },
        "condition_number_validated": True,
        "design_diagnostics": model.diagnostics,
        "effect_definition": "condition_difference_in_change_across_end_bin_centers",
        "test_definition": "joint_donor_shape_contrast_with_intercept_excluded",
        "limitations": [
            "Inference conditions on the frozen spline and shared inferred axis.",
            "Collection age and inferred pseudotime measure different quantities.",
            "The shape test assumes Gaussian donor errors with a common covariance.",
            "Unequal cell coverage can give donor curves different precision.",
        ],
        "bh_family_size": len(results) if inferential else None,
        "gene_statistics": results,
    }
