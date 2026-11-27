"""Fixed spline contrasts over independent donor curves on shared pseudotime.

The Gaussian endpoint accepts continuous, normalized expression. It does not
provide a negative-binomial count fit, infer an axis, or test lineage topology.
"""

import csv
import io
import math
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple, Self, TypedDict

import h5py
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from scipy.interpolate import BSpline
from scipy.sparse import csr_array
from scipy.stats import f, t

from ._hashing import canonical_sha256
from .design import DESIGN_CONDITION_NUMBER_LIMIT, require_design_condition
from .h5ad_gene_blocks import H5adGeneBlocks
from .population_data import bound_query_file
from .publication_families import Sha, budgeted_bh
from .trajectory_evidence import DonorTrajectoryEvidence

_MIN_DONORS_PER_ARM = 3
_MIN_INFERENCE_RESIDUAL_DF = 2
_MIN_BIN_COVERAGE = 0.75
_MIN_SHARED_SPAN = 0.5
_EFFECT_GRID_SIZE = 257
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
    shape_rotation: _Floats
    effect_basis: _Floats
    effect_bounds: tuple[float, float]
    minimum_cells: int
    maximum_cells: int
    minimum_eligible_bins: int
    excluded_bins: int
    excluded_cells: int
    knot_positions: tuple[float, ...]


class _ArmError(NamedTuple):
    donors: slice
    design_basis: _Floats
    contrast_variance_scale: float


@dataclass(frozen=True)
class _DonorModel:
    paired: bool
    matrix: _Floats
    projection: _Floats
    contrast_index: int
    variance_scale: float
    arm_errors: tuple[_ArmError, ...]
    diagnostics: dict

    def modeled_curves(self, donor_arm_curves: _Floats) -> _Floats:
        if not self.paired:
            return donor_arm_curves
        control, treated = np.split(donor_arm_curves, 2)
        return treated - control


class TrajectoryGeneStatistic(TypedDict):
    gene_id: str
    effect: float | None
    endpoint_delta: float | None
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
    expected = {*_COORDINATE_COLUMNS, *spec.technical_factors}
    cells: dict[str, _Cell] = {}
    try:
        with bound_query_file(
            path, spec.coordinates_sha256, path.lstat().st_size
        ) as binary:
            stream = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            try:
                reader = csv.DictReader(stream, strict=True)
                columns = reader.fieldnames or []
                if len(columns) != len(expected) or set(columns) != expected:
                    raise ValueError("trajectory_coordinate_fields_mismatch")
                for row in reader:
                    barcode, cell = _coordinate(row, spec)
                    if barcode in cells:
                        raise ValueError(
                            "trajectory_coordinate_ids_duplicate_or_over_budget"
                        )
                    cells[barcode] = cell
            finally:
                stream.detach()
    except ValueError as error:
        if str(error) == "Evidence input hash mismatch":
            raise ValueError("trajectory_coordinates_digest_mismatch") from error
        raise
    return cells


def _donor_arms(cells: Sequence[_Cell], spec: DonorTrajectorySpec) -> tuple[_Arm, ...]:
    control = {cell.donor for cell in cells if cell.condition == spec.denominator}
    treated = {cell.donor for cell in cells if cell.condition == spec.numerator}
    if (
        spec.design == "independent"
        and min(len(control), len(treated)) < _MIN_DONORS_PER_ARM
    ):
        raise ValueError("trajectory_needs_at_least_three_donors_per_condition")
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
    shape_parameters = spec.knot_count + 1
    if paired:
        required_pairs = (
            matrix.shape[1] + shape_parameters - 1 + _MIN_INFERENCE_RESIDUAL_DF
        )
        if len(matrix) < required_pairs:
            raise ValueError(
                "trajectory_needs_more_complete_pairs_for_frozen_spline"
                f": found={len(matrix)}, required={required_pairs}"
            )
    diagnostics = require_design_condition(matrix)
    projection = np.linalg.pinv(matrix)
    weights = projection[contrast_index]
    arm_errors: list[_ArmError] = []
    test_diagnostics: dict[str, Any]
    if paired:
        denominator_df = diagnostics["residual_df"] - shape_parameters + 1
        test_diagnostics = {"shape_test_reference": "paired_hotelling_f"}
    else:
        control_count = sum(condition == spec.denominator for _, condition in arms)
        arm_dfs = {}
        # Separate residuals are orthogonal to the common mean-model design
        # within each arm, including the technical terms in the contrast.
        for label, donors in (
            (spec.denominator, slice(None, control_count)),
            (spec.numerator, slice(control_count, None)),
        ):
            local_design = matrix[donors]
            left, _, _ = np.linalg.svd(local_design, full_matrices=False)
            rank = int(np.linalg.matrix_rank(local_design))
            design_basis = left[:, :rank]
            residual_df = len(local_design) - rank
            if residual_df < _MIN_INFERENCE_RESIDUAL_DF:
                raise ValueError(
                    "insufficient_within_condition_donor_residual_df"
                    f": condition={label!r}, found={residual_df}, "
                    f"required={_MIN_INFERENCE_RESIDUAL_DF}"
                )
            arm_weights = weights[donors]
            arm_errors.append(
                _ArmError(donors, design_basis, float(arm_weights @ arm_weights))
            )
            arm_dfs[label] = residual_df
        denominator_df = min(arm_dfs.values())
        test_diagnostics = {
            "shape_test_reference": "separate_variance_minimum_residual_df_t",
            "residual_df_per_condition": arm_dfs,
        }
    return _DonorModel(
        paired,
        matrix,
        projection,
        contrast_index,
        float(weights @ weights),
        tuple(arm_errors),
        {
            **diagnostics,
            "model_terms": model_terms,
            "within_pair_canceled_technical_terms": canceled,
            "shape_test_df": [shape_parameters, denominator_df],
            **test_diagnostics,
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
    eligible = counts >= spec.min_cells_per_bin
    count_by_arm = counts.reshape(len(arms), spec.bin_count)
    eligible_by_arm = eligible.reshape(count_by_arm.shape)
    eligible_per_arm = eligible_by_arm.sum(axis=1)
    required_bins = max(
        spec.knot_count + 2, math.ceil(_MIN_BIN_COVERAGE * spec.bin_count)
    )
    if eligible_per_arm.min() < required_bins:
        arm_index = int(np.argmin(eligible_per_arm))
        raise ValueError(
            "insufficient_within_donor_pseudotime_support"
            f": donor_arm={arms[arm_index]!r}, found={eligible_per_arm[arm_index]}, "
            f"required={required_bins}"
        )
    kept_cells = eligible[rows]
    nominal_start = 0.5 / spec.bin_count
    nominal_end = 1 - nominal_start
    observed_min = np.full(len(arms), np.inf)
    observed_max = np.full(len(arms), -np.inf)
    np.minimum.at(observed_min, arm_ids[kept_cells], times[kept_cells])
    np.maximum.at(observed_max, arm_ids[kept_cells], times[kept_cells])
    bounds = (
        float(max(nominal_start, observed_min.max())),
        float(min(nominal_end, observed_max.min())),
    )
    if bounds[1] - bounds[0] < _MIN_SHARED_SPAN * (nominal_end - nominal_start):
        raise ValueError(
            "trajectory_contrast_requires_observed_support: shared_span_too_short"
        )
    cell_indexes = np.flatnonzero(kept_cells)
    retained_rows = rows[kept_cells]
    first_cell = np.full(len(arms), len(cells))
    np.minimum.at(first_cell, arm_ids[kept_cells], cell_indexes)
    weights = csr_array(
        (1 / counts[retained_rows], (retained_rows, cell_indexes)),
        shape=(group_bins, len(cells)),
    )
    positions = np.linspace(0, 1, spec.knot_count)
    knots = np.r_[[0.0] * _DEGREE, positions, [1.0] * _DEGREE]
    spline = BSpline.design_matrix(times, knots, _DEGREE)
    basis = (weights @ spline).toarray().reshape(len(arms), spec.bin_count, -1)
    # The B-spline basis sums to one. Use a unit intercept and drop that
    # coordinate in the shape comparison to remove donor-level shifts.
    basis[:, :, 0] = 1.0
    projection = np.zeros((len(arms), spec.knot_count + 1, spec.bin_count))
    for index, keep in enumerate(eligible_by_arm):
        observed_basis = basis[index, keep]
        require_design_condition(observed_basis)
        bin_counts = count_by_arm[index, keep]
        # Inverse sampling variance of a bin mean is proportional to its
        # cell count. The second-stage contrast still counts each donor once.
        relative_precision = np.sqrt(bin_counts / bin_counts.max())
        weighted_basis = relative_precision[:, None] * observed_basis
        if np.linalg.cond(weighted_basis) > DESIGN_CONDITION_NUMBER_LIMIT:
            raise ValueError(
                f"ill_conditioned_weighted_donor_spline: donor_arm={arms[index]!r}"
            )
        projection[index][:, keep] = (
            np.linalg.pinv(weighted_basis)[1:] * relative_precision
        )
    reference = np.linspace(*bounds, spec.bin_count)
    shape_basis = BSpline.design_matrix(reference, knots, _DEGREE).toarray()[:, 1:]
    shape_basis -= shape_basis.mean(axis=0)
    if np.linalg.matrix_rank(shape_basis) < shape_basis.shape[1]:
        raise ValueError("trajectory_shape_basis_rank_deficient_on_shared_support")
    _, rotation = np.linalg.qr(shape_basis)
    grid = np.linspace(*bounds, _EFFECT_GRID_SIZE)
    effect_basis = BSpline.design_matrix(grid, knots, _DEGREE).toarray()[:, 1:]
    effect_basis -= effect_basis[0].copy()
    return _Bins(
        weights,
        first_cell[arm_ids],
        projection,
        rotation.T,
        effect_basis,
        bounds,
        int(counts[eligible].min()),
        int(counts[eligible].max()),
        int(eligible_per_arm.min()),
        int((~eligible).sum()),
        int((~kept_cells).sum()),
        tuple(float(value) for value in positions),
    )


def _independent_shape_probability(
    model: _DonorModel,
    curves: _Floats,
    contrast: _Floats,
    rotation: _Floats,
) -> float | None:
    coordinates = curves @ rotation
    effect = contrast @ rotation
    shape_df, denominator_df = model.diagnostics["shape_test_df"]
    variance = np.zeros(shape_df)
    for arm in model.arm_errors:
        observations = coordinates[arm.donors]
        residual = observations - arm.design_basis @ (arm.design_basis.T @ observations)
        df = len(observations) - arm.design_basis.shape[1]
        variance += arm.contrast_variance_scale * np.square(residual).sum(axis=0) / df
    positive = variance > 0
    if (
        not positive.any()
        or not np.isfinite(variance).all()
        or np.any(effect[~positive] != 0)
    ):
        return None
    statistic = np.abs(effect[positive]) / np.sqrt(variance[positive])
    # Hsu's conservative Welch reference uses the smaller arm residual df.
    # It avoids estimating a tail df from two residuals in a three-donor arm.
    # Bonferroni combines the fixed directions without a covariance inverse.
    return float(min(1.0, shape_df * np.min(2 * t.sf(statistic, denominator_df))))


def _paired_shape_probability(
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
    contrast = parameters[model.contrast_index]
    probability = None
    if inferential:
        probability = (
            _paired_shape_probability(model, curves, parameters)
            if model.paired
            else _independent_shape_probability(
                model, curves, contrast, bins.shape_rotation
            )
        )
    deviations = bins.effect_basis @ contrast
    effect = float(deviations[np.argmax(np.abs(deviations))])
    endpoint_delta = float(deviations[-1])
    if not np.isfinite(deviations).all() or (inferential and probability is None):
        return {
            "gene_id": gene,
            "effect": None,
            "endpoint_delta": None,
            "p": None,
            "q": None,
            "estimate_status": "not_estimable",
            "missing_reason": "singular_donor_covariance_or_nonfinite_contrast",
        }
    return {
        "gene_id": gene,
        "effect": effect,
        "endpoint_delta": endpoint_delta,
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


@contextmanager
def _gene_store(path, spec):
    if path.suffix.lower() == ".zarr":
        from rna_kernels.trajectory_zarr import bound_trajectory_zarr

        with bound_trajectory_zarr(path, spec) as store:
            yield store
    elif path.suffix.lower() == ".h5ad":
        with bound_query_file(
            path, spec.expression_sha256, path.lstat().st_size
        ) as stream:
            with h5py.File(stream, "r") as handle:
                yield H5adGeneBlocks(handle, layer=spec.layer)
    else:
        raise ValueError("trajectory_requires_bounded_normalized_h5ad_or_zarr")


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
    cells_by_id = _read_coordinates(coordinates_path, spec)
    with _gene_store(expression_path, spec) as store:
        if (
            evidence is not None
            and canonical_sha256(store.gene_names) != evidence.feature_axis_sha256
        ):
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
            "minimum_eligible_bins_per_donor_arm": bins.minimum_eligible_bins,
            "excluded_donor_bins": bins.excluded_bins,
            "excluded_cells": bins.excluded_cells,
            "effect_pseudotime_bounds": list(bins.effect_bounds),
            "exclusions_applied": bool(bins.excluded_bins),
        },
        "condition_number_validated": True,
        "design_diagnostics": model.diagnostics,
        "effect_definition": (
            "Signed maximum absolute condition difference in change from the shared "
            "start on a 257-point grid. Endpoint delta is the corresponding "
            "change from the shared start to end."
        ),
        "test_definition": (
            "Paired Hotelling F test of donor shape without the intercept."
            if model.paired
            else "Bonferroni omnibus of separate-variance t tests on orthogonal "
            "donor shape contrasts with the smaller arm residual degrees of freedom."
        ),
        "limitations": [
            "Inference conditions on the frozen spline and shared inferred axis.",
            "Collection age and inferred pseudotime measure different quantities.",
            (
                "Paired inference assumes Gaussian pair differences with common "
                "covariance."
                if model.paired
                else "Independent inference allows different Gaussian donor variances "
                "between conditions and assumes common variance within each condition."
            ),
            "Bin means are weighted by cell counts within donors. Bin eligibility "
            "and the shared support window are determined from cell coordinates.",
        ],
        "bh_family_size": len(results) if inferential else None,
        "gene_statistics": results,
    }
