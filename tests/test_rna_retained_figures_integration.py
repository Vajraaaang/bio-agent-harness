"""Staged integration controls, never historical-R or public execution proof."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.analysis import rna_comparison_outputs as outputs
from tests.test_rna_retained_figures import _governed_figure_run


def test_output_bundle_three_is_explicitly_distinct_from_two() -> None:
    assert outputs.FORMAT == "devbench.rna-analysis-output-bundle/2"
    # The /2 extractor has no authority to mint an authored-figure bundle.
    with pytest.raises(ValueError, match="closed bounded actual output bundle /2"):
        outputs.validate_comparison_outputs(
            {"format": "devbench.rna-analysis-output-bundle/3", "retained_figures": []},
            artifact_service=None,
        )


def test_generic_fixture_cannot_be_relabelled_full_original_airway(
    workspace_tmp_path: Path,
) -> None:
    service, run, plan = _governed_figure_run(workspace_tmp_path)
    with pytest.raises(ValueError, match="original input gene-universe lineage"):
        outputs.extract_comparison_outputs(
            [run["run_id"]], plan, artifact_service=service
        )


def test_legacy_bundle_cannot_add_new_figure_authority_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="closed bounded"):
        outputs.validate_comparison_outputs(
            {"format": outputs.FORMAT, "retained_figures": [], "native_outputs": []},
            artifact_service=None,
        )
