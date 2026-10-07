"""Contrast-reporting controls over tiny public fixtures, not method qualification."""
from copy import deepcopy
import json
import os

import pytest

from tests.test_analysis_episodes import inputs


LOCAL = pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Requires the authorized small local numerical fixtures",
)


def analyze(task, paths, *, cellular):
    if cellular:
        from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker
        return _analyze_singlecell_worker(
            "cell_type_state_analysis", task, counts_path=str(paths["expression"]),
            metadata_path=str(paths["metadata"]), annotations_path=str(paths["annotations"]),
            allow_data_execution=True)
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker
    return _analyze_bulk_worker(
        "mean_condition_analysis", task, str(paths["expression"]), str(paths["metadata"]),
        str(paths["annotations"]), {}, True)


@LOCAL
@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_reported_contrast_is_computed_not_copied_from_requested_prose(tmp_path, cellular, continuous):
    from devbench.study.biology_answers import TargetAnswer

    task, paths = inputs(tmp_path, cellular=cellular, continuous=continuous)
    original = analyze(task, paths, cellular=cellular)["target_results"][0]
    misleading = deepcopy(task)
    misleading["questions"][0].update(contrast="A minus B", units="absolute RNA molecules per cell")
    result = analyze(misleading, paths, cellular=cellular)
    target = result["target_results"][0]
    assert target["estimate_status"] == "estimated"
    assert target["contrast"] == "A minus B"  # Retained request, not a computed fact.
    assert target["units"] == "absolute RNA molecules per cell"
    payload = target["payload"]
    assert payload["effect_definition"] != target["contrast"]
    assert payload["gene_statistics"] == original["payload"]["gene_statistics"]
    contrast = payload["computed_contrast"]
    assert contrast["operation"] == "comparison_minus_reference"
    assert contrast["comparison_condition"] == "B"
    assert contrast["reference_condition"] == "A"
    assert contrast["effect_scale"] == ("unchanged_input_expression" if continuous else "log2_relative_expression")
    assert contrast["normalization"] == ("unchanged_input" if continuous else "RLE_library_size_offsets")
    assert "absolute RNA molecules" not in payload["effect_definition"]
    TargetAnswer.model_validate_json(json.dumps(target, allow_nan=False))

    # Reverse the structured computation, without relying on prose parsing.
    reversed_task = deepcopy(task)
    reversed_task["context"]["condition_labels"] = ["B", "A"]
    reversed_target = analyze(reversed_task, paths, cellular=cellular)["target_results"][0]
    reversed_payload = reversed_target["payload"]
    assert reversed_payload["computed_contrast"]["reference_condition"] == "B"
    assert reversed_payload["computed_contrast"]["comparison_condition"] == "A"
    for before, after in zip(payload["gene_statistics"], reversed_payload["gene_statistics"], strict=True):
        assert before["hypothesis_id"] == after["hypothesis_id"]
        assert before["estimate_status"] == after["estimate_status"] == "estimated"
        assert after["values"] == pytest.approx([-x for x in before["values"]], rel=1e-7, abs=1e-9)
        for interval, inverted in zip(before["intervals"], after["intervals"], strict=True):
            assert inverted["lower"] == pytest.approx(-interval["upper"], rel=1e-7, abs=1e-9)
            assert inverted["upper"] == pytest.approx(-interval["lower"], rel=1e-7, abs=1e-9)
        assert after["p_value"] == pytest.approx(before["p_value"], rel=1e-7, abs=1e-9)
        assert after["q_value"] == pytest.approx(before["q_value"], rel=1e-7, abs=1e-9)


def payload_record():
    return {
        "analysis_performed": True, "selection_conditional_inference": False,
        "biological_unit": "donor", "analysis_layer": "continuous",
        "effect_definition": "Computed condition contrast", "reference_condition": "A",
        "gene_statistics": [{"hypothesis_id": "g1", "gene_id": "g1", "estimate_status": "estimated",
                             "coordinate_ids": ["contrast"], "values": [2.0], "intervals": [],
                             "p_value": 0.03, "q_value": 0.03}],
        "multiplicity_scope": ["g1"],
        "computed_contrast": {
            "operation": "comparison_minus_reference", "reference_condition": "A", "comparison_condition": "B",
            "effect_scale": "unchanged_input_expression", "normalization": "unchanged_input",
        },
    }


@pytest.mark.parametrize("change", [
    {"reference_condition": "B"},
    {"effect_scale": "log2_relative_expression", "normalization": "RLE_library_size_offsets"},
    {"effect_scale": "log2_relative_expression"},
    {"normalization": "RLE_library_size_offsets"},
    {"operation": "causal_effect"},
])
def test_computed_contrast_rejects_inconsistent_scale_reference_and_operation(change):
    from pydantic import ValidationError
    from devbench.study.biology_answers import EffectsPayload

    record = payload_record()
    record["computed_contrast"].update(change)
    with pytest.raises(ValidationError):
        EffectsPayload.model_validate_json(json.dumps(record))


def test_computed_contrast_is_optional_for_historical_outputs_but_not_invented():
    from devbench.study.biology_answers import EffectsPayload

    record = payload_record()
    parsed = EffectsPayload.model_validate_json(json.dumps(record))
    assert parsed.computed_contrast.reference_condition == "A"
    record.pop("computed_contrast")
    assert EffectsPayload.model_validate_json(json.dumps(record)).computed_contrast is None


@pytest.mark.parametrize("change", [
    {"gene_statistics": [], "multiplicity_scope": []},
    {"analysis_performed": False, "gene_statistics": [], "multiplicity_scope": []},
    {"reference_condition": "C"},
])
def test_computed_contrast_requires_a_matching_performed_estimate(change):
    from pydantic import ValidationError
    from devbench.study.biology_answers import EffectsPayload

    record = payload_record()
    record.update(change)
    with pytest.raises(ValidationError):
        EffectsPayload.model_validate_json(json.dumps(record))


@LOCAL
def test_computed_cellular_contrast_does_not_fill_missing_type_estimates(tmp_path):
    import csv
    from devbench.study.biology_answers import TargetAnswer

    task, paths = inputs(tmp_path, cellular=True, continuous=False)
    with paths["expression"].open(newline="") as handle:
        rows = list(csv.reader(handle))
    # Type A retains all eight donors. Type B is present in only one donor per
    # condition, so its fits must remain unavailable under the unchanged rule.
    rare_type_cells = {2, 3, 18, 19}
    rows[1][1:] = ["1" if i in rare_type_cells else "80" for i in range(32)]
    rows[2][1:] = ["80" if i in rare_type_cells else "1" for i in range(32)]
    with paths["expression"].open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    result = analyze(task, paths, cellular=True)
    target = result["target_results"][0]
    payload = target["payload"]
    assert payload is not None, result
    assert payload["computed_contrast"]["comparison_condition"] == "B"
    missing = [row for row in payload["gene_statistics"] if row["component_id"] == "typeB"]
    assert len(missing) == len(task["gene_ids"])
    assert all(row["estimate_status"] == "unavailable" for row in missing)
    assert all(not row.get("values") and row.get("p_value") is None and row.get("q_value") is None for row in missing)
    assert "some_donor_type_fits_unavailable" in target["limitation_codes"]
    TargetAnswer.model_validate_json(json.dumps(target, allow_nan=False))


@LOCAL
def test_cellular_definition_preserves_the_actual_producer_definition():
    from types import SimpleNamespace
    from devbench.study.biology_tools_singlecell import _effect_payload

    data = SimpleNamespace(measurement="umi_counts", unit_hierarchy={"population_unit": "donor"})
    payload = _effect_payload(data, {"contrast": "misleading free text"}, [], [], "control",
                              definition="Computed factorial contrast on a control-normalized mean scale")
    assert payload["effect_definition"] == "Computed factorial contrast on a control-normalized mean scale"
    assert payload.get("computed_contrast") is None  # A multi-condition estimand is not a pairwise contrast.
