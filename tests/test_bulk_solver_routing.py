"""ADR-15 arbitration depends only on the pre-fit cohort and count-family size."""

from dataclasses import replace

import numpy as np
import pytest

from devbench.core.contrast_design import MountedModelSpec
from rna_kernels.bulk_solver_policy import freeze_bulk_solver
from rna_kernels.mounted_model import (
    _fit_config,
    compile_mounted_model,
    freeze_mounted_solver,
    validate_mounted_model,
)


def _choice(**overrides):
    request = {
        "measurement": "raw_counts",
        "runtime": "bioconductor",
        "comparison": "coefficient",
        "units": "independent",
        "primary": True,
        "single_factor": True,
        "units_per_arm": (10, 10),
    }
    return freeze_bulk_solver(**{**request, **overrides})


@pytest.mark.parametrize(("count", "trend"), [(0, False), (499, False), (500, True)])
def test_trend_uses_size_of_eligible_family(count, trend):
    result = _choice(n_eligible_genes=count)
    assert result.solver == "edger_v4" and result.test == "qlf"
    assert result.abundance_trend is trend
    assert result.normalization == "TMM" and result.filter_mode == "filterByExpr"
    assert result.sensitivity_solver == "limma_voom"
    assert _choice().abundance_trend is None  # resolved by the pre-fit R filter


@pytest.mark.parametrize(
    ("size", "primary", "sensitivity", "rank"),
    [
        (14, "edger_v4", "limma_voom", False),
        (15, "limma_voom_qw", "edger_v4", False),
        (19, "limma_voom_qw", "edger_v4", False),
        (20, "limma_voom_qw", "edger_v4", True),
    ],
)
def test_independent_donor_thresholds(size, primary, sensitivity, rank):
    result = _choice(units_per_arm=(size, size))
    assert (result.solver, result.sensitivity_solver) == (primary, sensitivity)
    assert result.rank_sensitivity_planned is rank
    assert result.calibration_status == "uncalibrated_L1"


def test_paired_heterogeneous_and_unbalanced_hypotheses_keep_their_design():
    assert _choice(units="paired_fixed", units_per_arm=(20, 20)).solver == "edger_v4"
    declared = _choice(heterogeneous_independent=True)
    assert (
        declared.solver == "limma_voom_qw" and declared.sensitivity_solver == "edger_v4"
    )
    independent = _choice(units_per_arm=(9, 10))
    repeated = _choice(units="mixed", units_per_arm=(9, 10))
    assert (independent.solver, independent.test) == ("dream", "moderated_t")
    assert (repeated.solver, repeated.test) == ("dream", "satterthwaite")
    assert (
        repeated.calibration_status
        == independent.calibration_status
        == "uncalibrated_L1"
    )


@pytest.mark.parametrize(("sizes", "rank"), [((10, 11), False), ((20, 25), True)])
def test_declared_heterogeneity_precedes_unequal_arm_routing(sizes, rank):
    choice = _choice(units_per_arm=sizes, heterogeneous_independent=True)
    assert choice.regime_route == "large_or_heterogeneous_independent"
    assert choice.solver == "limma_voom_qw" and choice.sensitivity_solver == "edger_v4"
    assert choice.rank_sensitivity_planned is rank


def test_heterogeneous_primary_requires_an_approved_voom_image(workspace_tmp_path):
    from devharness_controlled.native_contracts import LIMMA_VOOM_KERNEL_IMAGES
    from tests.test_thesis_bulk_acceptance import _make_cohort, _pipeline

    plan = _pipeline(_make_cohort(workspace_tmp_path, 12), family_id="heterogeneous")
    plan = plan.model_copy(
        update={
            "mounted_model": MountedModelSpec(
                comparison="coefficient",
                units="independent",
                heterogeneous_independent=True,
            )
        }
    )
    approved = frozenset(LIMMA_VOOM_KERNEL_IMAGES.values())
    with pytest.raises(ValueError, match="pinned limma-voom runtime"):
        plan.require_mounted_runtime(
            "sha256:" + "f" * 64, approved_voom_image_ids=approved
        )
    plan.require_mounted_runtime(
        LIMMA_VOOM_KERNEL_IMAGES["linux/arm64"], approved_voom_image_ids=approved
    )


def test_explicit_comparators_and_omnibus_are_preserved():
    for sizes in ((12, 8, 10), (52, 52, 52)):
        frozen = _choice(comparison="contrast", units_per_arm=sizes)
        assert frozen.solver == "edger_v4" and frozen.regime_route is None
    assert _choice(primary=False).solver == "deseq2"
    explicit = _choice(primary=False, sensitivity_method="limma_voom")
    assert explicit.solver == "limma_voom" and explicit.normalization is None
    assert _choice(comparison="lrt", units_per_arm=(20, 20)).test == "lrt"
    assert _choice(measurement="tximport_estimated_counts").solver == "edger_v4"


@pytest.mark.parametrize(
    ("sizes", "heterogeneous", "solver"),
    [
        ((9, 10), False, "dream"),
        ((20, 25), False, "dream"),
        ((10, 10), False, "edger_v4"),
        ((10, 10), True, "limma_voom_qw"),
        ((15, 15), False, "limma_voom_qw"),
    ],
)
def test_two_arm_compiled_contrasts_keep_their_scale_and_prefit_route(
    sizes,
    heterogeneous,
    solver,
):
    labels = ["control"] * sizes[0] + ["case"] * sizes[1]
    rows = [
        {"condition": group, "donor": f"d{i}", "batch": str(i % 2)}
        for i, group in enumerate(labels)
    ]
    for scale in (1, 2.5):
        opts = {
            "comparison": "contrast",
            "units": "independent",
            "condition_weights": {"control": -scale, "case": scale},
            "heterogeneous_independent": heterogeneous,
        }
        request = {
            "condition_column": "condition",
            "unit_column": "donor",
            "numerator": "case",
            "denominator": "control",
            "measurement": "raw_counts",
            "technical_factors": ["batch"],
            "continuous_covariates": [],
            "model": opts,
        }
        model = compile_mounted_model(rows, request)
        np.testing.assert_array_equal(model.contrast, [0, scale, 0])
        route = freeze_mounted_solver(model, request, True, "bioconductor")
        assert route.solver == solver and route.normalization == "TMM"
        assert route.filter_mode == "filterByExpr"
        if solver == "dream":
            assert (
                route.test == "moderated_t"
                and route.regime_route == "unbalanced_independent"
            )


@pytest.mark.parametrize("outcome", ["p_value", "discoveries", "calibration_status"])
def test_fit_outcomes_cannot_select_a_solver_or_upgrade_evidence(outcome):
    with pytest.raises(TypeError):
        _choice(**{outcome: 1})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"units_per_arm": (3, True)},
        {"units_per_arm": (1, 1)},
        {"units_per_arm": (14,)},
        {"n_eligible_genes": -1},
        {"n_eligible_genes": 499.5},
        {"heterogeneous_independent": 1},
        {"units": "paired_fixed", "units_per_arm": (9, 10)},
    ],
)
def test_unusable_prefit_information_is_refused(kwargs):
    with pytest.raises(ValueError):
        _choice(**kwargs)


def test_prefit_route_is_invariant_to_label_exchange_and_ignores_measured_values():
    from tests.thesis_calibration_inputs import SIMULATIONS, generate_cohort

    null = generate_cohort(SIMULATIONS[0], index=0, planted=False)
    planted = generate_cohort(SIMULATIONS[0], index=0, planted=True)
    assert not np.array_equal(null.source.counts, planted.source.counts)
    choices = []
    for case in (null, planted):
        model = compile_mounted_model(case.rows, case.request)
        flipped = replace(
            model, labels=["X" if x == "case" else "Y" for x in model.labels]
        )
        for compiled in (model, flipped):
            choice = freeze_mounted_solver(compiled, case.request, True, "bioconductor")
            config = _fit_config(choice, compiled, case.source, case.request)
            assert config["units_per_arm"] == (10, 10)
            assert config["sensitivity_solver"] == "limma_voom"
            assert (
                config["abundance_trend"] is None and config["normalization"] == "TMM"
            )
            choices.append(choice)
    assert len(set(choices)) == 1


def test_study_declaration_survives_governed_transport():
    model = MountedModelSpec(comparison="coefficient", units="independent")
    assert "heterogeneous_independent" not in model.request()
    assert "heterogeneous_independent" not in model.model_dump()
    declared = model.model_copy(update={"heterogeneous_independent": True}).request()
    assert declared["heterogeneous_independent"] is True
    validate_mounted_model(declared, "raw_counts")
    with pytest.raises(ValueError):
        validate_mounted_model(declared, "continuous")


def test_qualified_stratum_receipts_require_all_frozen_gates():
    import json
    from pathlib import Path

    from scipy.stats import binomtest

    audit = (
        Path(__file__).resolve().parents[1] / "docs/audits/2026-11-26-m0-edger-ablation"
    )
    evidence = json.loads((audit / "validation-evidence.json").read_bytes())
    replay = json.loads((audit / "parity-receipts.json").read_bytes())
    history = evidence["historical"]
    assert history["distinct_matrices"] == 2200 and history["refusals"] == 200
    assert not history["failed_cohorts"]
    assert set(evidence["validation"]) == {"B-I", "B-P", "S-I", "S-P", "P-P"}
    for regime, row in evidence["validation"].items():
        n = row["completed"]
        k = row["any_rejection"]
        assert 1200 <= n == row["planned"] <= 1350 and not row["failed_cohorts"]
        assert k == len(set(row["rejecting_cases"]))
        upper = binomtest(k, n, alternative="less").proportion_ci(1 - 0.025 / 5).high
        assert row["upper"] == pytest.approx(upper, abs=1e-12)
        assert row["ceiling"] == 0.075 and row["null_gate_passed"] == (upper <= 0.075)
        historical = history["regimes"][regime]
        sentinel = regime.replace("-", "").lower() + "-planted-000"
        passed = (
            upper <= 0.075
            and historical["null_completed"] == historical["planted_completed"] == 200
            and historical["empirical_null_fdr"] <= 0.075
            and historical["planted_tpr"] >= historical["planted_tpr_floor"]
            and all(
                replay[m]["cases"][sentinel]["passed"]
                and replay[m]["cases"]["bi-confounded-000"]["passed"]
                for m in ("exact_match_same_arch", "cross_arch_calibrated_match")
            )
        )
        assert (
            row["calibration_status"]
            == evidence["calibration_status"][regime]
            == ("calibrated_L2" if passed else "uncalibrated_L1")
        )
