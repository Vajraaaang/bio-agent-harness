"""Staged integration controls, never historical-R or public execution proof."""
from __future__ import annotations

from pathlib import Path

import pytest

from devbench.analysis import rna_comparison_outputs as outputs
from tests.test_rna_retained_figures import _governed_figure_run


def test_output_bundle_three_is_explicitly_distinct_from_two() -> None:
    assert outputs.FORMAT == "devbench.rna-analysis-output-bundle/2"
    assert getattr(outputs, "FORMAT_V3", None) == "devbench.rna-analysis-output-bundle/3"


def test_generic_fixture_cannot_be_relabelled_full_original_airway(tmp_path: Path) -> None:
    service, run, plan = _governed_figure_run(tmp_path)
    with pytest.raises(ValueError, match="generic method"):
        outputs.extract_comparison_outputs([run["run_id"]], plan, artifact_service=service)


def test_legacy_bundle_cannot_add_new_figure_authority_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="closed bounded"):
        outputs.validate_comparison_outputs({"format": outputs.FORMAT, "retained_figures": [],
            "native_outputs": []}, artifact_service=None)
