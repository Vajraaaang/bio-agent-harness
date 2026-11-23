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
