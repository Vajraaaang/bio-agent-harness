"""Freeze a supported bulk solver before gene-level results exist."""

from dataclasses import dataclass
from typing import Literal

Measurement = Literal["raw_counts", "tximport_estimated_counts", "continuous"]
Runtime = Literal["bioconductor", "python_only"]
Comparison = Literal["coefficient", "contrast", "nested", "lrt"]
UnitDesign = Literal["independent", "paired_fixed", "mixed"]
SensitivityMethod = Literal["limma_voom"]


@dataclass(frozen=True)
class BulkSolverChoice:
    solver: str
    test: str
    shrinkage: str | None = None
    adjustment: Literal["BH"] = "BH"
    regime_route: str | None = None
    normalization: Literal["TMM"] | None = None
    filter_mode: Literal["filterByExpr"] | None = None
    abundance_trend: bool | None = None
    sensitivity_solver: str | None = None
    rank_sensitivity_planned: bool = False
    # Qualification applies to a verified validation envelope, never to a
    # new cohort solely because its design triggers the same solver.
    calibration_status: Literal["uncalibrated_L1"] = "uncalibrated_L1"


def _check_routing_inputs(units_per_arm, n_eligible_genes, heterogeneous_independent):
    if units_per_arm is not None and (
        not isinstance(units_per_arm, tuple)
        or not 2 <= len(units_per_arm) <= 32
        or any(type(n) is not int or not 3 <= n <= 4096 for n in units_per_arm)
    ):
        raise ValueError(
            "Each arm requires a bounded count of distinct biological units"
        )
    if n_eligible_genes is not None and (
        type(n_eligible_genes) is not int or not 0 <= n_eligible_genes <= 100_000
    ):
        raise ValueError("Invalid pre-fit eligible gene count")
    if type(heterogeneous_independent) is not bool:
        raise ValueError("Donor heterogeneity requires a pre-specified declaration")


def _route_counts(units, comparison, sizes, n_eligible_genes, heterogeneous):
    # General joint contrasts retain their explicit QL hypothesis. For two
    # levels any directed contrast is a positive multiple of the coefficient.
    if comparison == "contrast" and sizes is not None and len(sizes) != 2:
        return BulkSolverChoice("edger_v4", "qlf")
    # A null trend setting freezes the rule at this point. The R driver then
    # evaluates it on the *actual* expression-filtered family before fitting.
    trend = None if n_eligible_genes is None else n_eligible_genes >= 500
    large = sizes is not None and min(sizes) >= 15
    rank = units == "independent" and sizes is not None and min(sizes) >= 20
    if units == "mixed":
        solver, test, route, comparator = (
            "dream",
            "satterthwaite",
            "repeated_donor",
            None,
        )
    elif units == "independent" and sizes is not None and len(set(sizes)) > 1:
        solver, test, route, comparator = (
            "dream",
            "moderated_t",
            "unbalanced_independent",
            None,
        )
    elif units == "independent" and (heterogeneous or large):
        solver, test, route, comparator = (
            "limma_voom_qw",
            "moderated_t",
            "large_or_heterogeneous_independent",
            "edger_v4",
        )
    else:
        solver, test, route, comparator = (
            "edger_v4",
            "qlf",
            "balanced_fixed_counts",
            "limma_voom",
        )
    return BulkSolverChoice(
        solver,
        test,
        regime_route=route,
        normalization="TMM",
        filter_mode="filterByExpr",
        abundance_trend=trend,
        sensitivity_solver=comparator,
        rank_sensitivity_planned=rank and comparison == "coefficient",
    )


def require_dream_coefficient(measurement: Measurement, comparison: Comparison) -> None:
    """Validate the hypothesis implemented by the mounted dream driver."""
    if comparison != "coefficient":
        raise ValueError("Mounted dream requires a directed condition coefficient")
    if measurement not in ("raw_counts", "continuous"):
        raise ValueError("Mounted dream requires raw counts or continuous expression")


def freeze_bulk_solver(
    *,
    measurement: Measurement,
    runtime: Runtime,
    comparison: Comparison,
    units: UnitDesign,
    primary: bool,
    single_factor: bool,
    sensitivity_method: SensitivityMethod | None = None,
    units_per_arm: tuple[int, ...] | None = None,
    n_eligible_genes: int | None = None,
    heterogeneous_independent: bool = False,
) -> BulkSolverChoice:
    """Reject unavailable designs rather than selecting another model after a fit."""
    if (
        measurement not in ("raw_counts", "tximport_estimated_counts", "continuous")
        or runtime not in ("bioconductor", "python_only")
        or comparison not in ("coefficient", "contrast", "nested", "lrt")
        or units not in ("independent", "paired_fixed", "mixed")
    ):
        raise ValueError(
            "A supported frozen measurement, runtime, hypothesis and unit design "
            "are required"
        )
    _check_routing_inputs(units_per_arm, n_eligible_genes, heterogeneous_independent)
    if heterogeneous_independent and (
        units != "independent" or measurement != "raw_counts"
    ):
        raise ValueError("Donor heterogeneity requires an independent raw-count design")
    if (
        units == "paired_fixed"
        and units_per_arm is not None
        and (len(units_per_arm) != 2 or len(set(units_per_arm)) != 1)
    ):
        raise ValueError("Incomplete fixed pairing cannot identify a paired contrast")
    if sensitivity_method is not None:
        return _voom(
            sensitivity_method,
            measurement,
            runtime,
            comparison,
            units,
            primary,
        )
    if not primary:
        return _sensitivity(measurement, runtime, comparison, units)
    if (
        measurement == "raw_counts"
        and runtime == "bioconductor"
        and comparison in {"coefficient", "contrast"}
        and (
            units_per_arm is not None
            or n_eligible_genes is not None
            or heterogeneous_independent
        )
    ):
        if units == "mixed":
            require_dream_coefficient(measurement, comparison)
        return _route_counts(
            units,
            comparison,
            units_per_arm,
            n_eligible_genes,
            heterogeneous_independent,
        )
    return _primary(measurement, runtime, comparison, units, single_factor)


def _primary(measurement, runtime, comparison, units, single_factor):
    if units == "mixed":
        require_dream_coefficient(measurement, comparison)
        if runtime != "bioconductor":
            raise ValueError("This mixed-model comparison is unavailable")
        return BulkSolverChoice("dream", "satterthwaite")
    if measurement == "continuous":
        if comparison == "lrt":
            raise ValueError(
                "Gaussian bulk uses explicit coefficient or nested F tests"
            )
        return BulkSolverChoice("gaussian_ols", "f" if comparison == "nested" else "t")
    if runtime == "bioconductor":
        return BulkSolverChoice("edger_v4", "lrt" if comparison == "lrt" else "qlf")
    if (
        measurement != "raw_counts"
        or comparison != "coefficient"
        or units != "independent"
        or not single_factor
    ):
        raise ValueError(
            "Python-only counts support one independent, single-factor Wald coefficient"
        )
    return BulkSolverChoice("pydeseq2", "wald")


def _sensitivity(measurement, runtime, comparison, units):
    if (
        measurement != "raw_counts"
        or runtime != "bioconductor"
        or units == "mixed"
        or comparison == "nested"
    ):
        raise ValueError("No supported DESeq2 sensitivity for this frozen design")
    if comparison == "lrt":
        return BulkSolverChoice("deseq2", "lrt")
    return BulkSolverChoice(
        "deseq2", "wald", "apeglm" if comparison == "coefficient" else "ashr"
    )


def _voom(method, measurement, runtime, comparison, units, primary):
    if (
        method != "limma_voom"
        or primary
        or measurement != "raw_counts"
        or runtime != "bioconductor"
        or units == "mixed"
        or comparison not in {"coefficient", "contrast"}
    ):
        raise ValueError(
            "limma-voom sensitivity requires nonprimary raw counts, a pinned "
            "Bioconductor image and a fixed-effect directed contrast"
        )
    return BulkSolverChoice("limma_voom", "moderated_t")
