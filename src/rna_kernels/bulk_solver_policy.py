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
    if sensitivity_method is not None:
        return _voom(
            sensitivity_method, measurement, runtime, comparison, units, primary,
        )
    if not primary:
        return _sensitivity(measurement, runtime, comparison, units)
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
