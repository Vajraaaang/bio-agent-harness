"""Compile an exact prefit two-level design over an original count inventory.

A ready design passes structural checks only. The caller must separately
admit the measurement, original target and scope, and population-label
provenance, including the independence of P-P labels from the tested RNA.
A worker adapter must bind the exact ordered profile IDs, numeric matrix and
contrast and enforce estimator-specific admission and qualification.
"""

import re
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from typing import Annotated, Final, Literal, cast

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_serializer,
    model_validator,
)

from rna_kernels.bulk_solver_policy import (
    SensitivityMethod,
    freeze_bulk_solver,
    require_dream_coefficient,
)

from .count_profiles import CountProfile, CountProfiles, _label
from .design import DESIGN_CONDITION_NUMBER_LIMIT, design_matrix_diagnostics
from .population_contract import content_sha256
from .publication_families import FamilySpec

Regime = Literal["B-I", "B-P", "S-I", "S-P", "P-P"]
_REGIMES = ("B-I", "B-P", "S-I", "S-P", "P-P")
_PAIRED = ("B-P", "S-P", "P-P")
_MAX_COLUMNS = 128


@dataclass(frozen=True)
class DesignSpec:
    regime: Regime
    numerator: str
    denominator: str
    population: str | None = None
    condition: str | None = None
    technical_factors: tuple[str, ...] = ()
    candidate_aliases: tuple[str, ...] = ()
    cohort_mode: Literal["contrast_pair_subcohort", "joint_multi_condition_model"] = (
        "contrast_pair_subcohort"
    )


_COLUMN_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$"
_LEVEL_PATTERN = r"^[A-Za-z0-9_.-]{1,160}$"
_Column = Annotated[str, StringConstraints(pattern=_COLUMN_PATTERN)]
_Level = Annotated[str, StringConstraints(pattern=_LEVEL_PATTERN)]
_Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_PIPELINE_FORMAT: Final = "devbench.tier-a-bulk-pipeline/1"

TIER_A_BULK_OPERATION = "bulk_condition_contrast"
TIER_A_BULK_OUTPUT = "result"
TIER_A_COUNT_REPRESENTATIONS = (
    "COUNT-1",
    "counts",
    "integer_counts",
    "raw_counts",
    "umi_counts",
    "non_umi_counts",
)
TIER_A_CONTINUOUS_REPRESENTATIONS = ("continuous", "normalized_expression")
TIER_A_MOUNTED_REPRESENTATIONS = {
    "continuous_h5": ".h5",
    "continuous_h5ad": ".h5ad",
    "continuous_zarr": ".zarr",
    "raw_counts_h5": ".h5",
    "raw_counts_h5ad": ".h5ad",
    "raw_counts_zarr": ".zarr",
    "tximport_counts_h5": ".h5",
}
TIER_A_MOUNTED_COUNT_REPRESENTATIONS = (
    "raw_counts_h5",
    "raw_counts_h5ad",
    "raw_counts_zarr",
    "tximport_counts_h5",
)
TIER_A_MOUNTED_CONTINUOUS_REPRESENTATIONS = (
    "continuous_h5",
    "continuous_h5ad",
    "continuous_zarr",
)
TIER_A_LENGTHS_REPRESENTATION = "effective_lengths_h5"
TIER_A_CONTROLS_REPRESENTATION = "independent_controls_json"


class MountedConditionWeight(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False
    )

    level: _Level
    weight: float = Field(ge=-1e6, le=1e6)


class MountedModelSpec(BaseModel):
    """Frozen generalized hypothesis and artifact bindings."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    comparison: Literal["coefficient", "contrast", "nested", "lrt"]
    units: Literal["independent", "paired_fixed", "mixed"]
    condition_weights: tuple[MountedConditionWeight, ...] = Field(
        default=(), max_length=32
    )
    reduced_terms: tuple[_Column, ...] | None = Field(default=None, max_length=32)
    lengths_sha256: _Digest | None = None
    latent_method: Literal["svaseq", "ruvg"] | None = None
    sv_method: Literal["be", "leek"] | None = None
    n_factors: int | None = Field(default=None, ge=1, le=32)
    controls_sha256: _Digest | None = None
    sensitivity_method: SensitivityMethod | None = None

    @model_serializer(mode="wrap")
    def retain_model_encoding(self, handler):
        record = handler(self)
        if self.sensitivity_method is None:
            record.pop("sensitivity_method", None)
        return record

    @model_validator(mode="after")
    def validate_hypothesis(self):
        levels = {row.level for row in self.condition_weights}
        if self.comparison == "contrast":
            weights = [row.weight for row in self.condition_weights]
            if (
                len(levels) < 2
                or len(levels) != len(weights)
                or not any(weights)
                or abs(sum(weights)) > 1e-8
            ):
                raise ValueError(
                    'A directed condition contrast requires distinct levels and '
                    'weights summing to zero'
                )
        elif levels:
            raise ValueError("Condition weights require a directed contrast")
        if self.reduced_terms is not None and (
            self.comparison not in {"nested", "lrt"}
            or len(set(self.reduced_terms)) != len(self.reduced_terms)
        ):
            raise ValueError(
                "A reduced design requires distinct terms and a nested test"
            )
        expected = {
            None: (False, False, False),
            "svaseq": (False, False, bool(self.sv_method)),
            "ruvg": (True, True, False),
        }[self.latent_method]
        if (
            bool(self.controls_sha256),
            self.n_factors is not None,
            bool(self.sv_method),
        ) != expected:
            raise ValueError(
                'Latent diagnostics require method-specific arguments and '
                'independent RUVg controls'
            )
        return self

    def artifact_digests(self):
        return {
            role: value
            for role, value in (
                ("lengths", self.lengths_sha256),
                ("controls", self.controls_sha256),
            )
            if value is not None
        }

    def request(self, controls=None):
        document = {"comparison": self.comparison, "units": self.units}
        if self.sensitivity_method is not None:
            document["sensitivity_method"] = self.sensitivity_method
        if self.condition_weights:
            document["condition_weights"] = {
                row.level: row.weight for row in self.condition_weights
            }
        if self.reduced_terms is not None:
            document["reduced_terms"] = list(self.reduced_terms)
        if self.lengths_sha256 is not None:
            document["lengths_sha256"] = self.lengths_sha256
        if self.latent_method == "svaseq":
            document["latent"] = {
                "method": "svaseq",
                "sv_method": self.sv_method or "be",
            }
        elif self.latent_method == "ruvg":
            document["latent"] = {
                "method": "ruvg",
                "n_factors": self.n_factors,
                "controls": controls,
            }
        return document


class ContrastSpec(BaseModel):
    """Exact direction of one registered two-condition comparison."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    numerator: _Level
    denominator: _Level

    @model_validator(mode="after")
    def distinct_conditions(self):
        if self.numerator == self.denominator:
            raise ValueError("The contrast requires distinct conditions")
        return self


class PipelineSpec(BaseModel):
    """Content-addressed parameters for the registered bulk contrast."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )

    format: Literal[
        "devbench.tier-a-bulk-pipeline/1", "devbench.tier-a-bulk-pipeline/2"
    ] = _PIPELINE_FORMAT
    design: DesignSpec
    contrast: ContrastSpec
    family: FamilySpec
    measurement: Literal["raw_counts", "continuous", "tximport_estimated_counts"]
    biological_unit: Literal[
        "donor",
        "organism",
        "experimental_culture",
        "donor_derived_cell_line",
    ]
    condition_column: _Column
    population_unit_column: _Column
    specimen_column: _Column
    library_column: _Column
    expression_sha256: _Digest
    metadata_sha256: _Digest
    family_sha256: _Digest
    continuous_covariates: tuple[_Column, ...] = Field(default=(), max_length=16)
    expression_layer: _Column | None = None
    mounted_model: MountedModelSpec | None = None

    @model_validator(mode="after")
    def bound_bulk_design(self):
        design, contrast, family = self.design, self.contrast, self.family
        _validate_spec(design)
        if (
            design.regime not in {"B-I", "B-P"}
            or design.candidate_aliases
            or design.cohort_mode == "joint_multi_condition_model"
            and self.mounted_model is None
        ):
            raise ValueError("The bulk solver requires a declared B-I or B-P design")
        directed = (contrast.numerator, contrast.denominator)
        if directed != (design.numerator, design.denominator) or directed != (
            family.numerator,
            family.denominator,
        ):
            raise ValueError("Design and family must bind the same directed contrast")
        if (
            family.population not in {"bulk", "observed biological units"}
            or not family.feature_ids
            or family.dependencies
            or family.request_sha256 is not None
            or family.weight is not None
            and Fraction(family.weight) != int(family.primary)
        ):
            raise ValueError(
                'The solver requires a complete primary or unweighted sensitivity '
                'family'
            )
        roles = (
            self.condition_column,
            self.population_unit_column,
            self.specimen_column,
            self.library_column,
            *design.technical_factors,
            *self.continuous_covariates,
        )
        if (
            len(roles) != len(set(roles))
            or "sample_id" in roles
            or any(not re.fullmatch(_COLUMN_PATTERN, name) for name in roles)
        ):
            raise ValueError("Bind distinct metadata columns for the bulk design")
        if self.format == _PIPELINE_FORMAT and (
            self.continuous_covariates
            or self.expression_layer
            or self.mounted_model is not None
        ):
            raise ValueError(
                "Numeric covariates and layers require the mounted pipeline contract"
            )
        model = self.mounted_model
        if (self.measurement == "tximport_estimated_counts") != bool(
            model and model.lengths_sha256
        ):
            raise ValueError(
                "tximport estimated counts require a pinned effective-length matrix"
            )
        if model is not None:
            self._validate_mounted_model(model)
        return self

    def _validate_mounted_model(self, model):
        if (self.design.regime == "B-I") != (model.units == "independent"):
            raise ValueError("Mounted unit model differs from the declared regime")
        nuisance = {*self.design.technical_factors, *self.continuous_covariates}
        if {"condition", "biological_unit"} & nuisance:
            raise ValueError(
                "Generalized nuisance terms cannot use reserved model term names"
            )
        terms = {"condition", *nuisance}
        fixed_pairing = model.units == "paired_fixed"
        if fixed_pairing:
            terms.add("biological_unit")
        if model.reduced_terms is not None and (
            not set(model.reduced_terms) < terms
            or "condition" in model.reduced_terms
            or fixed_pairing
            and "biological_unit" not in model.reduced_terms
        ):
            raise ValueError(
                "The reduced design must be a strict subset of the declared full design"
            )
        if model.latent_method and (
            self.measurement != "raw_counts" or model.units != "independent"
        ):
            raise ValueError(
                "Latent diagnostics require an independent raw-count design"
            )
        if model.units == "mixed":
            require_dream_coefficient(self.measurement, model.comparison)
        self._validate_mounted_sensitivity(model)
        if model.condition_weights:
            weights = {row.level: row.weight for row in model.condition_weights}
            if (
                weights.get(self.contrast.numerator, 0) <= 0
                or weights.get(self.contrast.denominator, 0) >= 0
            ):
                raise ValueError(
                    "Explicit weights must agree with the declared contrast direction"
                )

    def _validate_mounted_sensitivity(self, model: MountedModelSpec) -> None:
        if model.sensitivity_method is None:
            return
        if model.latent_method is not None:
            raise ValueError("limma-voom sensitivity does not use latent diagnostics")
        freeze_bulk_solver(
            measurement=self.measurement, runtime="bioconductor",
            comparison=model.comparison, units=model.units,
            primary=self.family.primary, single_factor=False,
            sensitivity_method=model.sensitivity_method,
        )

    @model_serializer(mode="wrap")
    def retain_pipeline_encoding(self, handler):
        data = handler(self)
        if self.mounted_model is None:
            data.pop("mounted_model", None)
        if self.format == _PIPELINE_FORMAT:
            data.pop("continuous_covariates", None)
            data.pop("expression_layer", None)
        return data

    def require_mounted_runtime(
        self, image_id, *, representation=None, allow_python_wald=False,
        approved_voom_image_ids: frozenset[str] = frozenset(),
    ):
        model = self.mounted_model
        if (
            model is not None
            and model.sensitivity_method is not None
            and image_id not in approved_voom_image_ids
        ):
            raise ValueError("Use the pinned limma-voom runtime")
        if (representation == "tximport_counts_h5") != (
            self.measurement == "tximport_estimated_counts"
        ):
            raise ValueError("tximport representation and measurement must agree")
        if self.design.regime != "B-I" and self.mounted_model is None:
            raise ValueError("Mounted bulk requires independent biological profiles")
        if (
            self.expression_layer
            and TIER_A_MOUNTED_REPRESENTATIONS.get(representation) != ".h5ad"
        ):
            raise ValueError("An expression layer requires mounted AnnData")
        if self.mounted_model is not None or self.measurement == "raw_counts":
            if self.mounted_model is not None and not image_id:
                raise ValueError(
                    "Generalized mounted analyses require a pinned Bioconductor image"
                )
            if not image_id and not allow_python_wald:
                raise ValueError(
                    'Mounted counts require a pinned image or a frozen '
                    'Python-only runtime'
                )
            from rna_kernels.bulk_solver_policy import freeze_bulk_solver

            freeze_bulk_solver(
                measurement=self.measurement,
                runtime="bioconductor" if image_id else "python_only",
                comparison=self.mounted_model.comparison
                if self.mounted_model
                else "coefficient",
                units=self.mounted_model.units if self.mounted_model else "independent",
                primary=self.family.primary,
                single_factor=not (
                    self.design.technical_factors or self.continuous_covariates
                ),
                sensitivity_method=model.sensitivity_method if model else None,
            )

    def tool_request(self):
        """Project the typed specification to the existing bounded tool inputs."""
        if self.format != _PIPELINE_FORMAT:
            raise ValueError(
                "Mounted pipeline parameters require a mounted expression artifact"
            )
        numerator, denominator = self.contrast.numerator, self.contrast.denominator
        batches = list(self.design.technical_factors)
        blocking = [self.population_unit_column] if self.design.regime == "B-P" else []
        units = (
            "input units"
            if self.measurement == "continuous"
            else "log2 count-rate contrast"
        )
        roles = {
            "condition": self.condition_column,
            "population_unit": self.population_unit_column,
            "batches": batches,
            "blocking": blocking,
            "time": None,
            "dose": None,
            "covariate": None,
        }
        public_task = {
            "measurement": self.measurement,
            "gene_ids": list(self.family.feature_ids),
            "observation_id_label": "sample_id",
            "unit_hierarchy": {
                "population_unit": self.biological_unit,
                "population_unit_label": self.population_unit_column,
                "specimen_label": self.specimen_column,
                "library_label": self.library_column,
                "identity_status": "declared",
            },
            "analysis_columns": roles,
            "context": {
                "condition_labels": [denominator, numerator],
                "requested_condition_contrast": {
                    "numerator": numerator,
                    "denominator": denominator,
                },
            },
            "questions": [
                {
                    "target_id": "mean_contrast",
                    "estimand": (
                        "Condition mean contrast over observed biological units"
                    ),
                    "units": units,
                    "contrast": f"{numerator} minus {denominator}",
                    "reference_population": "observed biological units",
                    "prediction_reference": "observed biological units",
                }
            ],
        }
        options = {
            "condition_col": self.condition_column,
            "reference_condition": denominator,
            "comparison_condition": numerator,
            "batch_cols": batches,
            "block_cols": blocking,
            "minimum_units": 3,
            "condition_number_limit": DESIGN_CONDITION_NUMBER_LIMIT,
        }
        return public_task, options


@dataclass(frozen=True)
class DesignColumn:
    role: Literal["intercept", "biological_unit", "technical", "target"]
    factor: str
    level: str | None
    reference: str | None


@dataclass(frozen=True)
class AliasProof:
    removed_column: DesignColumn
    removed_values: tuple[int, ...]
    # Column index, numerator, denominator for exact rational coefficients.
    coefficients: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class CompiledContrast:
    specification: DesignSpec
    status: Literal["ready", "held"]
    refusal_codes: tuple[str, ...]
    numerator: str
    denominator: str
    original_profile_ids: tuple[str, ...]
    cohort_profile_ids: tuple[str, ...]
    cohort_units: tuple[str, ...]
    profile_ids: tuple[str, ...]
    unit_counts: tuple[tuple[str, int], ...]
    pair_count: int | None
    incomplete_units: tuple[str, ...]
    columns: tuple[DesignColumn, ...]
    matrix: tuple[tuple[int, ...], ...]
    contrast: tuple[int, ...]
    rank: int | None
    residual_df: int | None
    estimable: bool | None
    alias_proofs: tuple[AliasProof, ...]
    format: str = field(default="devbench.erql-contrast-design/1", init=False)

    @property
    def design_sha256(self):
        return content_sha256(asdict(self))


def _validate_spec(specification):
    if (
        type(specification) is not DesignSpec
        or specification.regime not in _REGIMES
        or not _label(specification.numerator)
        or not _label(specification.denominator)
        or specification.numerator == specification.denominator
        or specification.cohort_mode
        not in ("contrast_pair_subcohort", "joint_multi_condition_model")
    ):
        raise ValueError("Use a typed two-level RNA contrast")
    if (
        not _label(specification.population)
        if specification.regime in ("S-I", "S-P")
        else specification.population is not None
    ) or (
        not _label(specification.condition)
        if specification.regime == "P-P"
        else specification.condition is not None
    ):
        raise ValueError(
            'Within-population designs require a population and P-P requires one '
            'condition'
        )
    for factors in (specification.technical_factors, specification.candidate_aliases):
        if (
            type(factors) is not tuple
            or len(factors) > 16
            or not all(map(_label, factors))
            or len(set(factors)) != len(factors)
        ):
            raise ValueError("Technical factors must be distinct and bounded")
    if not set(specification.candidate_aliases) <= set(specification.technical_factors):
        raise ValueError("An alias candidate must identify a declared technical factor")


def _target(row, regime):
    return row.key.population if regime == "P-P" else row.key.condition


def _in_unit_scope(row, spec, levels):
    if spec.regime == "P-P":
        return row.key.condition == spec.condition and row.key.population is not None
    return row.key.condition in levels and (
        (row.key.population is None) == spec.regime.startswith("B")
    )


def _reference_columns(columns, values, role, factor, labels, levels):
    reference = levels[0]
    for level in levels[1:]:
        columns.append(DesignColumn(role, factor, level, reference))
        values.append(tuple(int(label == level) for label in labels))


def _alias_proof(values, proposed_column, proposed_values):
    """Accept a least-squares candidate only after exact reconstruction."""
    coefficients = np.linalg.lstsq(
        np.asarray(values, dtype=float).T, proposed_values, rcond=None
    )[0]
    rational = tuple(
        Fraction(float(value)).limit_denominator(4096) for value in coefficients
    )
    rows = zip(*values, strict=False)
    for row, expected in zip(rows, proposed_values, strict=False):
        observed = sum(
            coefficient * entry
            for coefficient, entry in zip(rational, row, strict=False)
        )
        if observed != expected:
            return None
    terms = tuple(
        (index, value.numerator, value.denominator)
        for index, value in enumerate(rational)
        if value
    )
    return AliasProof(proposed_column, proposed_values, terms)


def prove_donor_aliases(
    donors: tuple[str, ...], factors: dict[str, tuple[str, ...]],
) -> tuple[tuple[DesignColumn, ...], tuple[AliasProof, ...]]:
    """Prove complete technical factors against the paired donor basis alone."""
    donor_levels = sorted(set(donors))
    columns = [DesignColumn("intercept", "intercept", None, None)]
    values = [(1,) * len(donors)]
    _reference_columns(
        columns, values, "biological_unit", "biological_unit", donors, donor_levels,
    )
    proofs: list[AliasProof] = []
    for factor, labels in factors.items():
        levels = sorted(set(labels))
        pairs = set(zip(donors, labels, strict=True))
        if not 1 < len(levels) == len(pairs) == len(donor_levels):
            continue
        candidates: list[DesignColumn] = []
        indicators: list[tuple[int, ...]] = []
        _reference_columns(candidates, indicators, "technical", factor, labels, levels)
        for column, entries in zip(candidates, indicators, strict=True):
            proof = _alias_proof(values, column, entries)
            if proof is None:
                raise ValueError(f"Could not prove exact donor alias for {factor}")
            proofs.append(proof)
    return tuple(columns), tuple(proofs)


def _unit_coverage(spec, levels, original_groups, admitted_groups, refusal):
    incomplete: list[str] = []
    pair_count = 0 if spec.regime in _PAIRED else None
    for unit in sorted(original_groups):
        originals, admitted = original_groups[unit], admitted_groups[unit]
        original_levels = [_target(row, spec.regime) for row in originals]
        if len(set(original_levels)) < len(original_levels):
            refusal.append("repeated_specimens_or_duplicate_profiles")
        if pair_count is not None:
            admitted_levels = [_target(row, spec.regime) for row in admitted]
            if len(admitted_levels) == 2 and set(admitted_levels) == set(levels):
                pair_count += 1
            else:
                incomplete.append(unit)
        else:
            if len(originals) != 1 and originals:
                refusal.append("independent_units_cross_target_levels")
            if len(admitted) != len(originals) or not admitted:
                incomplete.append(unit)
    return incomplete, pair_count


def _contrast_coverage(spec, levels, original_units, cohort, observed, refusal):
    paired = spec.regime in _PAIRED
    # A unit observed only in another population remains in scope for the
    # declared target condition. Its absence is not a target restriction.
    original_groups: dict[str, list[CountProfile]] = {
        unit: [] for unit in original_units
    }
    admitted_groups: dict[str, list[CountProfile]] = {
        unit: [] for unit in original_units
    }
    for row in cohort:
        original_groups[row.key.unit_id].append(row)
    for row in observed:
        admitted_groups[row.key.unit_id].append(row)
    unit_counts = tuple(
        (
            level,
            sum(
                any(_target(row, spec.regime) == level for row in rows)
                for rows in admitted_groups.values()
            ),
        )
        for level in levels
    )
    if spec.cohort_mode != "contrast_pair_subcohort":
        refusal.append("joint_multi_condition_model_unavailable")
    if not cohort:
        refusal.append("empty_target_cohort")
    incomplete, pair_count = _unit_coverage(
        spec,
        levels,
        original_groups,
        admitted_groups,
        refusal,
    )
    if incomplete:
        refusal.append("incomplete_pair" if paired else "incomplete_target_coverage")
    if pair_count is not None and pair_count < 3:
        refusal.append("insufficient_complete_pairs")
    if not paired and any(total < 3 for _, total in unit_counts):
        refusal.append("insufficient_independent_units_per_level")
    if any(row.status == "observed_zero" for row in observed):
        refusal.append("all_zero_profile_library")
    return unit_counts, pair_count, incomplete


def _technical_factor_groups(spec, observed, refusal):
    technical = [dict(row.technical_factors) for row in observed]
    factors = sorted(
        spec.technical_factors,
        key=lambda name: (name in spec.candidate_aliases, name),
    )
    groups = []
    for factor in factors:
        labels = tuple(row.get(factor) for row in technical)
        if None in labels:
            refusal.append("missing_technical_factor:" + factor)
        else:
            complete = cast(tuple[str, ...], labels)
            groups.append((factor, complete, sorted(set(complete))))
    return groups


def _contrast_matrix(spec, levels, observed, refusal):
    if not observed or spec.cohort_mode != "contrast_pair_subcohort":
        return (), (), (), None, None, None, ()
    paired = spec.regime in _PAIRED
    units = tuple(row.key.unit_id for row in observed)
    unit_levels = sorted(set(units)) if paired else []
    factor_groups = _technical_factor_groups(spec, observed, refusal)
    width = 2 + max(0, len(unit_levels) - 1) + sum(len(g[2]) - 1 for g in factor_groups)
    if width > _MAX_COLUMNS:
        refusal.append("design_column_limit_exceeded")
        return (), (), (), None, None, None, ()

    columns = [DesignColumn("intercept", "intercept", None, None)]
    values = [(1,) * len(observed)]
    proofs: list[AliasProof] = []
    if paired:
        _reference_columns(
            columns,
            values,
            "biological_unit",
            "biological_unit",
            units,
            unit_levels,
        )
    for factor, labels, factor_levels in factor_groups:
        candidate_columns: list[DesignColumn] = []
        candidate_values: list[tuple[int, ...]] = []
        _reference_columns(
            candidate_columns,
            candidate_values,
            "technical",
            factor,
            labels,
            factor_levels,
        )
        for column, entries in zip(candidate_columns, candidate_values, strict=False):
            proof = (
                _alias_proof(values, column, entries)
                if factor in spec.candidate_aliases
                else None
            )
            if proof is None:
                columns.append(column)
                values.append(entries)
            else:
                proofs.append(proof)
    nuisance_rank = int(np.linalg.matrix_rank(np.asarray(values, dtype=float).T))
    target_factor = "population" if spec.regime == "P-P" else "condition"
    _reference_columns(
        columns,
        values,
        "target",
        target_factor,
        tuple(_target(row, spec.regime) for row in observed),
        levels,
    )
    matrix = tuple(zip(*values, strict=False))
    diagnostics = design_matrix_diagnostics(matrix)
    rank = diagnostics["rank"]
    contrast = (0,) * (len(columns) - 1) + (1 if spec.numerator == levels[1] else -1,)
    estimable = rank > nuisance_rank
    residual_df = len(observed) - rank
    if not estimable:
        refusal.append("contrast_not_estimable")
    if not diagnostics["condition_number_validated"]:
        refusal.append(
            "rank_deficient_design" if rank < len(columns) else "ill_conditioned_design"
        )
    if residual_df <= 0:
        refusal.append("no_structural_residual_information")
    return tuple(columns), matrix, contrast, rank, residual_df, estimable, tuple(proofs)


def compile_contrast_design(
    counts: CountProfiles,
    specification: DesignSpec,
) -> CompiledContrast:
    """Retain original, target and observed profile inventories separately."""
    if type(counts) is not CountProfiles:
        raise ValueError("A compiled count profile inventory is required")
    _validate_spec(specification)
    spec = specification
    levels = tuple(sorted((spec.denominator, spec.numerator)))
    scope = [row for row in counts.profiles if _in_unit_scope(row, spec, levels)]
    cohort = [
        row
        for row in scope
        if _target(row, spec.regime) in levels
        and (spec.population is None or row.key.population == spec.population)
    ]
    observed = [row for row in cohort if row.status in ("observed", "observed_zero")]
    original_units = {row.key.unit_id for row in scope}
    refusal: list[str] = []
    unit_counts, pair_count, incomplete = _contrast_coverage(
        spec,
        levels,
        original_units,
        cohort,
        observed,
        refusal,
    )
    columns, matrix, contrast, rank, residual_df, estimable, proofs = _contrast_matrix(
        spec,
        levels,
        observed,
        refusal,
    )

    return CompiledContrast(
        specification=spec,
        status="held" if refusal else "ready",
        refusal_codes=tuple(dict.fromkeys(refusal)),
        numerator=spec.numerator,
        denominator=spec.denominator,
        original_profile_ids=tuple(row.profile_id for row in counts.profiles),
        cohort_profile_ids=tuple(row.profile_id for row in cohort),
        cohort_units=tuple(sorted(original_units)),
        profile_ids=tuple(row.profile_id for row in observed),
        unit_counts=unit_counts,
        pair_count=pair_count,
        incomplete_units=tuple(incomplete),
        columns=columns,
        matrix=matrix,
        contrast=contrast,
        rank=rank,
        residual_df=residual_df,
        estimable=estimable,
        alias_proofs=proofs,
    )
