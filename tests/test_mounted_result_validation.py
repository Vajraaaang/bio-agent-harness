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
