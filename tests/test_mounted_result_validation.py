"""Admission checks on synthetic mounted results before issuing a Tier A artifact."""

from types import SimpleNamespace

import numpy as np
import pytest

from devbench.engine.mounted_result_validation import verify_mounted_primary
from rna_kernels.mounted_bulk import run_mounted_bulk
from rna_kernels.mounted_rank import assess_rank_agreement
from tests.test_mounted_bulk import _build, _digest


def test_mounted_result_requires_the_bound_footer_and_original_axis(workspace_tmp_path):
    inputs = workspace_tmp_path / "inputs"
    outputs = workspace_tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    request = _build(inputs, "h5", 32)
    summary = run_mounted_bulk(inputs, outputs, _digest(request))
    path = outputs / "genes.parquet"
    artifact = SimpleNamespace(
        kind="table",
        representation="parquet",
        sha256=summary["genes_sha256"],
        permitted_uses=("admitted_research_inference",),
    )
    family = SimpleNamespace(
        family_id=summary["family_id"], feature_ids=tuple(f"g{i:05}" for i in range(32))
    )
    specification = SimpleNamespace(
        family=family, measurement="continuous", biological_unit="donor"
    )

    def open_artifact(_):
        return path.open("rb")

    verify_mounted_primary(
        artifact, open_artifact=open_artifact, summary=summary, spec=specification
    )
    with pytest.raises(ValueError, match="footer differs"):
        verify_mounted_primary(
            artifact,
            open_artifact=open_artifact,
            summary={**summary, "request_sha256": "0" * 64},
            spec=specification,
        )
    family.feature_ids = tuple(reversed(family.feature_ids))
    with pytest.raises(ValueError, match="original ordered feature family"):
        verify_mounted_primary(
            artifact, open_artifact=open_artifact, summary=summary, spec=specification
        )


def test_rank_agreement_with_zero_comparable_effects_requires_review():
    summary = assess_rank_agreement(
        np.ones(2),
        np.zeros(2),
        np.full(2, np.nan),
        np.full(2, np.nan),
    )
    assert summary["effect_comparison"] == "insufficient_comparable_effects"
    assert summary["hold"] == "Q-DESIGN"


def test_heterogeneous_primary_requires_a_routed_receipt():
    from devbench.core.contrast_design import MountedModelSpec
    from devbench.engine.mounted_result_validation import verify_mounted_solver

    spec = SimpleNamespace(
        mounted_model=MountedModelSpec(
            comparison="coefficient",
            units="independent",
            heterogeneous_independent=True,
        ),
        family=SimpleNamespace(primary=True),
        measurement="raw_counts",
        design=SimpleNamespace(technical_factors=()),
        continuous_covariates=(),
    )
    with pytest.raises(ValueError, match="regime route"):
        verify_mounted_solver({"solver": "edger_v4", "test": "qlf", "test_df": 1}, spec)


@pytest.mark.parametrize("implicit", [False, True])
def test_routed_admission_recomputes_design_and_refuses_unearned_calibration(
    workspace_tmp_path,
    implicit,
):
    import json
    from dataclasses import replace

    from devbench.engine.mounted_result_validation import verify_mounted_solver
    from tests.thesis_calibration_inputs import (
        SIMULATIONS,
        generate_cohort,
        stage_mounted_request,
    )

    root = workspace_tmp_path / "inputs"
    case = generate_cohort(SIMULATIONS[0], planted=False, index=0)
    if implicit:
        case = replace(
            case,
            request={k: v for k, v in case.request.items() if k != "model"},
            rows=[{**r, "batch": "constant"} for r in case.rows],
        )
    sha = stage_mounted_request(case, root)
    request = json.loads((root / "request.json").read_bytes())
    spec = SimpleNamespace(
        family=SimpleNamespace(primary=True),
        mounted_model=None if implicit else SimpleNamespace(comparison="coefficient"),
        measurement="raw_counts",
        metadata_sha256=request["metadata_sha256"],
    )
    details = {
        "normalization": "TMM",
        "filter": "filterByExpr",
        "n_eligible_genes": 192,
    }
    summary = {
        "request_sha256": sha,
        "solver": "edger_v4",
        "solver_version": "4.10.7",
        "solver_details": {
            **details,
            "abundance_trend": False,
            "engine": "edgeR",
            "version": "4.10.7",
            "legacy": False,
            "robust": True,
        },
        "sensitivity_solver": "limma_voom",
        "sensitivity_details": {
            **details,
            "engine": "limma",
            "version": "3.68.5",
            "solver": "limma_voom",
            "weights": "voom",
            "robust": True,
        },
        "rank_sensitivity_planned": False,
        "regime_route": "balanced_fixed_counts",
        "calibration_status": "uncalibrated_L1",
        "row_count": 192,
        "status_counts": {"estimated": 192},
        "units_per_arm": [10, 10],
        "test": "qlf",
        "test_df": 1,
        "model_columns": [
            "F",
            "df.prior",
            "df.residual.adj",
            "df.total",
            "s2.post",
            "sensitivity_effect",
            "sensitivity_p_value",
            "sensitivity_q_value",
            "sensitivity_code",
            "sensitivity_t",
            "sensitivity_df.prior",
            "sensitivity_df.total",
            "sensitivity_s2.post",
        ],
    }
    verify_mounted_solver(summary, spec, inputs=root)
    for changes in (
        {"regime_route": "large_or_heterogeneous_independent"},
        {"units_per_arm": [20, 20]},
        {"sensitivity_details": {}},
        {"calibration_status": "calibrated_L2"},
    ):
        with pytest.raises(ValueError, match="route|calibration"):
            verify_mounted_solver({**summary, **changes}, spec, inputs=root)


def _contrast_spec(levels, *, heterogeneous=False):
    from devbench.core.contrast_design import MountedConditionWeight, MountedModelSpec

    weights = tuple(
        MountedConditionWeight(
            level=level,
            weight=-1.0 if i == 0 else (1.0 if i == 1 else 0.0),
        )
        for i, level in enumerate(levels)
    )
    return SimpleNamespace(
        mounted_model=MountedModelSpec(
            comparison="contrast",
            units="independent",
            condition_weights=weights,
            heterogeneous_independent=heterogeneous,
        ),
        family=SimpleNamespace(primary=True),
        measurement="raw_counts",
        design=SimpleNamespace(technical_factors=()),
        continuous_covariates=(),
    )


def test_unrouted_primary_raw_count_bioconductor_receipts_are_refused(
    workspace_tmp_path,
):
    from devbench.core.contrast_design import MountedModelSpec
    from devbench.engine.mounted_result_validation import verify_mounted_solver
    from tests.thesis_calibration_inputs import (
        SIMULATIONS,
        generate_cohort,
        stage_mounted_request,
    )

    implicit = SimpleNamespace(
        mounted_model=None,
        family=SimpleNamespace(primary=True),
        measurement="raw_counts",
        design=SimpleNamespace(technical_factors=()),
        continuous_covariates=(),
    )
    with pytest.raises(ValueError, match="regime route"):
        verify_mounted_solver(
            {"solver": "edger_v4", "test": "qlf", "test_df": 1}, implicit
        )

    for units, solver, test in (
        ("independent", "edger_v4", "qlf"),
        ("paired_fixed", "edger_v4", "qlf"),
        ("mixed", "dream", "satterthwaite"),
    ):
        spec = SimpleNamespace(
            mounted_model=MountedModelSpec(comparison="coefficient", units=units),
            family=SimpleNamespace(primary=True),
            measurement="raw_counts",
            design=SimpleNamespace(technical_factors=()),
            continuous_covariates=(),
        )
        with pytest.raises(ValueError, match="regime route"):
            verify_mounted_solver({"solver": solver, "test": test, "test_df": 1}, spec)

    two_arm = _contrast_spec(("reference", "case"))
    with pytest.raises(ValueError, match="regime route"):
        verify_mounted_solver(
            {"solver": "edger_v4", "test": "qlf", "test_df": 1}, two_arm
        )

    from dataclasses import replace

    root = workspace_tmp_path / "two-arm-inputs"
    case = generate_cohort(SIMULATIONS[0], planted=False, index=0)
    sha = stage_mounted_request(
        replace(
            case,
            request={
                **case.request,
                "model": {
                    "comparison": "contrast",
                    "units": "independent",
                    "condition_weights": {"reference": -1.0, "case": 1.0},
                },
            },
        ),
        root,
    )
    import json

    request = json.loads((root / "request.json").read_bytes())
    staged_two_arm = SimpleNamespace(
        mounted_model=two_arm.mounted_model,
        family=two_arm.family,
        measurement="raw_counts",
        design=two_arm.design,
        continuous_covariates=(),
        metadata_sha256=request["metadata_sha256"],
    )
    with pytest.raises(ValueError, match="regime route"):
        verify_mounted_solver(
            {"request_sha256": sha, "solver": "edger_v4", "test": "qlf", "test_df": 1},
            staged_two_arm,
            inputs=root,
        )


def test_unrouted_non_routed_fits_remain_accepted(workspace_tmp_path):
    import json

    from devbench.core.contrast_design import MountedModelSpec
    from devbench.engine.mounted_result_validation import verify_mounted_solver
    from devharness_controlled.native_contracts import KERNEL_VERSIONS
    from devharness_core.provenance import canonical_json, hash_artifact

    three_arm = _contrast_spec(("reference", "mid", "case"))
    verify_mounted_solver(
        {"solver": "edger_v4", "test": "qlf", "test_df": 1},
        three_arm,
    )

    root = workspace_tmp_path / "three-arm-inputs"
    root.mkdir()
    rows = [
        {"sample_id": f"s{i}", "donor": f"d{i}", "condition": cond}
        for cond, start in (("reference", 0), ("mid", 4), ("case", 8))
        for i in range(start, start + 4)
    ]
    import csv

    meta = root / "metadata.csv"
    with meta.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("sample_id", "donor", "condition"))
        writer.writeheader()
        writer.writerows(rows)
    expr = root / "expression.h5"
    expr.write_bytes(b"placeholder")
    fam = root / "family.json"
    fam.write_bytes(b"{}")
    req = root / "request.json"
    req.write_bytes(
        canonical_json(
            {
                "format": "devbench.mounted-bulk/1",
                "expression": "expression.h5",
                "expression_sha256": hash_artifact(expr)["sha256"],
                "metadata_sha256": hash_artifact(meta)["sha256"],
                "family_sha256": hash_artifact(fam)["sha256"],
                "measurement": "raw_counts",
                "layer": None,
                "biological_unit": "donor",
                "unit_column": "donor",
                "condition_column": "condition",
                "technical_factors": [],
                "numerator": "case",
                "denominator": "reference",
                "model": {
                    "comparison": "contrast",
                    "units": "independent",
                    "condition_weights": {"reference": -1.0, "mid": 0.0, "case": 1.0},
                },
            }
        )
    )
    req_sha = json.loads(req.read_bytes()) and hash_artifact(req)["sha256"]
    staged_three_arm = SimpleNamespace(
        mounted_model=three_arm.mounted_model,
        family=three_arm.family,
        measurement="raw_counts",
        design=three_arm.design,
        continuous_covariates=(),
        metadata_sha256=hash_artifact(meta)["sha256"],
    )
    verify_mounted_solver(
        {"request_sha256": req_sha, "solver": "edger_v4", "test": "qlf", "test_df": 1},
        staged_three_arm,
        inputs=root,
    )

    for comparison, test, test_df in (("lrt", "lrt", 2), ("nested", "qlf", 2)):
        verify_mounted_solver(
            {"solver": "edger_v4", "test": test, "test_df": test_df},
            SimpleNamespace(
                mounted_model=MountedModelSpec(
                    comparison=comparison, units="independent"
                ),
                family=SimpleNamespace(primary=True),
                measurement="raw_counts",
                design=SimpleNamespace(technical_factors=()),
                continuous_covariates=(),
            ),
        )

    verify_mounted_solver(
        {"solver": "deseq2", "test": "wald", "shrinkage": "apeglm", "test_df": 1},
        SimpleNamespace(
            mounted_model=MountedModelSpec(
                comparison="coefficient", units="independent"
            ),
            family=SimpleNamespace(primary=False),
            measurement="raw_counts",
            design=SimpleNamespace(technical_factors=()),
            continuous_covariates=(),
        ),
    )

    verify_mounted_solver(
        {
            "solver": "limma_voom",
            "solver_version": KERNEL_VERSIONS["limma"],
            "test": "moderated_t",
            "test_df": 1,
            "solver_details": {
                "normalization": "full_family_RLE",
                "variance_model": "voom_observation_weights_robust_empirical_bayes",
            },
        },
        SimpleNamespace(
            mounted_model=MountedModelSpec(
                comparison="coefficient",
                units="independent",
                sensitivity_method="limma_voom",
            ),
            family=SimpleNamespace(primary=False),
            measurement="raw_counts",
            design=SimpleNamespace(technical_factors=()),
            continuous_covariates=(),
        ),
    )

    verify_mounted_solver(
        {"solver": "gaussian_ols", "test": "t", "test_df": 1},
        SimpleNamespace(
            mounted_model=MountedModelSpec(
                comparison="coefficient", units="independent"
            ),
            family=SimpleNamespace(primary=True),
            measurement="continuous",
            design=SimpleNamespace(technical_factors=()),
            continuous_covariates=(),
        ),
    )

    verify_mounted_solver(
        {"solver": "edger_v4", "test": "qlf", "test_df": 1},
        SimpleNamespace(
            mounted_model=MountedModelSpec(
                comparison="coefficient",
                units="independent",
                lengths_sha256="a" * 64,
            ),
            family=SimpleNamespace(primary=True),
            measurement="tximport_estimated_counts",
            design=SimpleNamespace(technical_factors=()),
            continuous_covariates=(),
        ),
    )

    verify_mounted_solver(
        {"solver": "pydeseq2", "test": "wald", "test_df": 1},
        SimpleNamespace(
            mounted_model=MountedModelSpec(
                comparison="coefficient", units="independent"
            ),
            family=SimpleNamespace(primary=True),
            measurement="raw_counts",
            design=SimpleNamespace(technical_factors=()),
            continuous_covariates=(),
        ),
        runtime="python_only",
    )
