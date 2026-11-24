"""Frozen method choices are determined before any measured gene fit."""

from dataclasses import FrozenInstanceError

import pytest

from rna_kernels.bulk_solver_policy import freeze_bulk_solver


def _plan(**changes):
    request = {
        "measurement": "raw_counts",
        "runtime": "bioconductor",
        "comparison": "coefficient",
        "units": "independent",
        "primary": True,
        "single_factor": True,
    }
    return freeze_bulk_solver(**{**request, **changes})


def test_count_solver_freezes_contrast_type_and_bh():
    primary = _plan()
    assert (primary.solver, primary.test, primary.adjustment) == (
        "edger_v4",
        "qlf",
        "BH",
    )
    with pytest.raises(FrozenInstanceError):
        primary.solver = "deseq2"
    assert _plan(comparison="nested").test == "qlf"
    assert _plan(comparison="lrt").test == "lrt"
    assert _plan(measurement="tximport_estimated_counts").solver == "edger_v4"


def test_sensitivity_freezes_shrinker_before_wald_or_lrt():
    assert _plan(primary=False).shrinkage == "apeglm"
    assert _plan(primary=False, comparison="contrast").shrinkage == "ashr"
    lrt = _plan(primary=False, comparison="lrt")
    assert (lrt.solver, lrt.test, lrt.shrinkage) == ("deseq2", "lrt", None)


@pytest.mark.parametrize(
    "unsupported",
    [
        {"comparison": "lrt"},
        {"comparison": "nested"},
        {"comparison": "contrast"},
        {"units": "mixed"},
        {"units": "paired_fixed"},
        {"single_factor": False},
        {"primary": False},
        {"measurement": "tximport_estimated_counts"},
    ],
)
def test_python_only_counts_fail_closed_outside_wald_scope(unsupported):
    assert _plan(runtime="python_only").test == "wald"
    with pytest.raises(ValueError):
        _plan(runtime="python_only", **unsupported)


def test_mixed_and_continuous_go_to_distinct_solvers():
    assert _plan(units="mixed").solver == "dream"
    assert _plan(measurement="continuous").solver == "gaussian_ols"
    assert _plan(measurement="continuous", units="mixed").solver == "dream"


@pytest.mark.parametrize("comparison", ["contrast", "nested", "lrt"])
def test_mixed_solver_refuses_hypotheses_the_mounted_driver_cannot_fit(comparison):
    with pytest.raises(ValueError, match="directed condition coefficient"):
        _plan(units="mixed", comparison=comparison)
