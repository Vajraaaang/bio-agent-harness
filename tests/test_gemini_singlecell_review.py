"""Synthetic regression evidence for external-review F2/F3 and adjacent admission paths."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from tests.test_rna_singlecell_hardening import _fixture, _tool

pytestmark = pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Requires explicitly enabled local synthetic software fixtures")


def _metadata(paths, change):
    path = Path(paths["metadata_path"])
    with path.open() as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    change(rows)
    fields = list(dict.fromkeys([*fields, *(key for row in rows for key in row)]))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _partial_pairing(paths):
    with Path(paths["metadata_path"]).open() as handle:
        cells = {row["sample_id"] for row in csv.DictReader(handle)
                 if row["donor_id"] == "d0" and row["condition"] == "B"}
    path = Path(paths["counts_path"])
    with path.open() as handle:
        rows = list(csv.reader(handle))
    for column, cell in enumerate(rows[0][1:], 1):
        if cell in cells and (column - 1) % 4 < 2:
            rows[1][column] = rows[2][column] = "40"
    with path.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)


def _assert_supported_numerics(result):
    target = result["target_results"][0]
    assert target["payload"] is not None, result
    tested = [row for row in target["payload"]["gene_statistics"] if row.get("p_value") is not None]
    assert tested
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in tested)
    assert set(target["payload"]["multiplicity_scope"]) == {row["hypothesis_id"] for row in tested}
    return target


def test_f2_partial_pairing_preserves_actual_supported_type_fit(tmp_path):
    task, paths = _fixture(tmp_path / "input", paired=True)
    _partial_pairing(paths)
    audit = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert audit["status"] == "audited", audit
    assert audit["design_support_by_type"]["typeA"]["status"] == "support_unavailable"
    assert audit["design_support_by_type"]["typeB"]["status"] == "supported_by_software_design_checks"
    result = _tool("cell_type_state_analysis", task, paths)
    target = _assert_supported_numerics(result)
    rows = target["payload"]["gene_statistics"]
    unavailable = [row for row in rows if row["component_id"] == "typeA"]
    assert len(unavailable) == len(task["gene_ids"])
    for row in unavailable:
        assert row["estimate_status"] in {"failed", "unavailable"}
        assert "incomplete_repeated_donor_covariance_model_not_implemented" in row["missing_reason"]
        assert all(row.get(key) is None for key in ("values", "p_value", "q_value", "intervals"))
    assert any(row["estimate_status"] == "estimated" for row in rows if row["component_id"] == "typeB")
    assert "some_donor_type_fits_unavailable" in target["limitation_codes"]
    fit = result["diagnostics"]["cell_state_effect"]["fits_by_type"]["typeB"]
    assert fit["design"]["paired_donor_blocking"] is True
    assert fit["model_family_attempted"] == "negative_binomial"
    assert fit["normalization"]["method"].startswith("median_of_ratios")
    assert fit["calibration"]["method"]


@pytest.mark.parametrize("role", ["time", "dose", "covariate", "mixed_batch"])
def test_f3_extra_c_cannot_block_selected_cohort_and_membership_is_disclosed(tmp_path, role):
    task, paths = _fixture(tmp_path / "input", conditions=("A", "B", "C"))
    if role != "mixed_batch":
        task["analysis_columns"][role] = "declared_coordinate"
    def change(rows):
        for index, row in enumerate(rows):
            if role == "mixed_batch":
                if row["condition"] == "C":
                    row["capture_batch"] = "extra" + str(index % 2)
            else:
                row["declared_coordinate"] = "t1" if row["condition"] == "C" else "t0"
    metadata = _metadata(paths, change)
    audit = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert audit["status"] == "audited", audit
    cohort = audit["condition_cohort"]
    assert cohort["selected_condition_levels"] == ["A", "B"]
    assert cohort["unselected_condition_levels"] == ["C"]
    assert {cell for row in audit["membership"] if row["condition"] == "C" for cell in row["cell_ids"]} == {
        row["cell_id"] for row in metadata if row["condition"] == "C"}
    assert all(set(support["independent_units_per_condition"]) == {"A", "B"}
               for support in audit["design_support_by_type"].values())
    result = _tool("cell_type_state_analysis", task, paths)
    _assert_supported_numerics(result)
    assert result["diagnostics"]["cell_state_effect"]["pseudobulk"]["condition_cohort"] == cohort


def test_f3_selected_annotation_and_fitted_values_equal_ab_only_population(tmp_path):
    baseline_task, baseline_paths = _fixture(tmp_path / "ab")
    extended_task, extended_paths = _fixture(tmp_path / "abc", conditions=("A", "B", "C"))
    path = Path(extended_paths["counts_path"])
    with path.open() as handle:
        rows = list(csv.reader(handle))
    # Extreme unselected C changes full-cohort marker centering/scaling.
    for column in range(33, len(rows[0])):
        rows[1][column] = "100000" if column % 2 else "1"
        rows[2][column] = "2"
    with path.open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    baseline_audit = _tool("pseudobulk_membership_audit", baseline_task, baseline_paths)["diagnostics"]
    extended_audit = _tool("pseudobulk_membership_audit", extended_task, extended_paths)["diagnostics"]
    assert extended_audit["status"] == "audited", extended_audit
    assert extended_audit["annotation_evidence"]["cell_score_preview"] == baseline_audit["annotation_evidence"]["cell_score_preview"]
    baseline = _tool("cell_type_state_analysis", baseline_task, baseline_paths)
    extended = _tool("cell_type_state_analysis", extended_task, extended_paths)
    assert _assert_supported_numerics(extended)["payload"] == _assert_supported_numerics(baseline)["payload"]


@pytest.mark.parametrize("role", ["time", "dose", "covariate", "mixed_batch"])
def test_selected_coordinate_or_batch_problem_remains_unavailable(tmp_path, role):
    task, paths = _fixture(tmp_path / "input", conditions=("A", "B", "C"))
    if role != "mixed_batch":
        task["analysis_columns"][role] = "declared_coordinate"
    def change(rows):
        for index, row in enumerate(rows):
            if role == "mixed_batch":
                if row["condition"] == "A":
                    row["capture_batch"] = "selected" + str(index % 2)
            else:
                row["declared_coordinate"] = "t1" if row["condition"] == "B" else "t0"
    _metadata(paths, change)
    audit = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert audit["status"] in {"support_unavailable", "analysis_failed"}, audit
    result = _tool("cell_type_state_analysis", task, paths)
    assert result["target_results"][0]["payload"] is None


def test_partial_pairing_admitted_episode_keeps_supported_type_results(tmp_path):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = _fixture(tmp_path / "input", paired=True)
    _partial_pairing(paths)
    episode = AnalysisEpisode.create(tmp_path / "episode", question="Donor-level B minus A",
        assay="scrna", public_task=task,
        inputs={"expression": paths["counts_path"], "metadata": paths["metadata_path"], "annotations": paths["annotations_path"]},
        allow_data_execution=True)
    for tool in ("audit_expression_quality", "validate_biological_units", "pseudobulk_membership_audit"):
        response = episode.step({"kind": "tool", "tool": tool})
        assert response["status"] == "succeeded", response
    response = episode.step({"kind": "tool", "tool": "cell_type_state_analysis"})
    assert response["status"] == "succeeded", response
    _assert_supported_numerics(response["result"])


@pytest.mark.parametrize("extra_problem", ["time", "mixed_batch"])
def test_selected_ab_cohort_reaches_estimator_through_episode_with_extra_c(tmp_path, extra_problem):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = _fixture(tmp_path / "input", conditions=("A", "B", "C"))
    if extra_problem == "time":
        task["analysis_columns"]["time"] = "visit"
    def change(rows):
        for index, row in enumerate(rows):
            if extra_problem == "time":
                row["visit"] = "extra" if row["condition"] == "C" else "selected"
            elif row["condition"] == "C":
                row["capture_batch"] = "extra" + str(index % 2)
    _metadata(paths, change)
    episode = AnalysisEpisode.create(tmp_path / "episode", question="B minus A in the requested donor cohort",
        assay="scrna", public_task=task,
        inputs={"expression": paths["counts_path"], "metadata": paths["metadata_path"], "annotations": paths["annotations_path"]},
        allow_data_execution=True)
    for tool in ("audit_expression_quality", "validate_biological_units", "pseudobulk_membership_audit"):
        response = episode.step({"kind": "tool", "tool": tool})
        assert response["status"] == "succeeded", response
    assert response["result"]["diagnostics"]["status"] == "audited", response
    response = episode.step({"kind": "tool", "tool": "cell_type_state_analysis"})
    assert response["status"] == "succeeded", response
    _assert_supported_numerics(response["result"])


def _perturbation_fixture(root, *, problem=None):
    task, paths = _fixture(root, conditions=("A", "B", "C"))
    task["questions"][0].update(target_id="assignment_effect", estimand="Donor-level assigned guide B versus control A")
    rows = _metadata(paths, lambda rows: None)
    if problem in {"selected_time", "extra_time"}:
        task["analysis_columns"]["time"] = "visit"
        def coordinates(rows):
            for row in rows:
                row["visit"] = "t1" if row["condition"] == ("B" if problem == "selected_time" else "C") else "t0"
        rows = _metadata(paths, coordinates)
    path = Path(paths["annotations_path"])
    annotations = json.loads(path.read_text())
    annotations["guide_assignments"] = {"randomization_unit": "donor", "negative_control": "A",
        "allocation": "not randomized" if problem == "nonrandom" else "randomized donor assignment",
        "records": [{"cell_id": row["cell_id"], "assignment": row["condition"],
            "assigned_targets": [] if row["condition"] == "A" else ["g0" if row["condition"] == "B" else "g1"]} for row in rows]}
    path.write_text(json.dumps(annotations))
    return task, paths


@pytest.mark.parametrize("problem", ["nonrandom", "selected_time"])
def test_perturbation_prerequisite_audit_cannot_certify_estimator_refusal(tmp_path, problem):
    task, paths = _perturbation_fixture(tmp_path / "input", problem=problem)
    from devbench.study.biology_tools_singlecell import analyze_singlecell_tool
    options = {"comparison_condition": "B"}
    audit = analyze_singlecell_tool("perturbation_design_audit", task, options=options, **paths)
    assert audit["diagnostics"]["status"] == "support_unavailable", audit
    result = analyze_singlecell_tool("perturbation_response_analysis", task, options=options, **paths)
    assert result["target_results"][0]["payload"] is None
    assert audit["diagnostics"]["reason"] in result["target_results"][0]["limitation_codes"]


def test_perturbation_selected_assignment_cohort_preserves_extra_membership(tmp_path):
    from devbench.study.biology_tools_singlecell import analyze_singlecell_tool
    task, paths = _perturbation_fixture(tmp_path / "input", problem="extra_time")
    options = {"comparison_condition": "B"}
    audit = analyze_singlecell_tool("perturbation_design_audit", task, options=options, **paths)
    assert audit["diagnostics"]["status"] == "audited", audit
    support = audit["diagnostics"]["design_support_by_target"]["assignment_effect"]
    assert support["design"]["independent_donors_per_condition"] == {"A": 4, "B": 4}
    assert support["pseudobulk"]["assignment_cohort"]["unselected_assignment_levels"] == ["C"]
    assert support["pseudobulk"]["assignment_cohort"]["unselected_cell_count"] == 16
    result = analyze_singlecell_tool("perturbation_response_analysis", task, options=options, **paths)
    _assert_supported_numerics(result)
    assert result["diagnostics"]["assignment_effect"]["pseudobulk"]["assignment_cohort"] == support["pseudobulk"]["assignment_cohort"]


def test_unselected_membership_retains_original_coordinate_and_batch_values(tmp_path):
    task, paths = _fixture(tmp_path / "input", conditions=("A", "B", "C"))
    task["analysis_columns"]["time"] = "visit"
    def change(rows):
        for index, row in enumerate(rows):
            row["visit"] = str(index) if row["condition"] == "C" else "0"
            if row["condition"] == "C":
                row["capture_batch"] = "capture" + str(index % 2)
    metadata = _metadata(paths, change)
    diagnostic = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert diagnostic["status"] == "audited", diagnostic
    by_cell = {row["cell_id"]: row for row in metadata}
    for profile in diagnostic["membership"]:
        if profile["condition"] != "C":
            continue
        assert profile["aggregation_performed"] is False
        assert profile["membership_role"] == "unselected_observations_only"
        assert profile["declared_coordinates"]["time"]["observed_values"] == sorted({by_cell[cell]["visit"] for cell in profile["cell_ids"]})
        assert profile["observed_batch_values"]["capture_batch"] == sorted({by_cell[cell]["capture_batch"] for cell in profile["cell_ids"]})


def test_perturbation_public_requested_contrast_is_used_without_repeating_options(tmp_path):
    task, paths = _perturbation_fixture(tmp_path / "input", problem="extra_time")
    audit = _tool("perturbation_design_audit", task, paths)
    assert audit["diagnostics"]["status"] == "audited", audit
    result = _tool("perturbation_response_analysis", task, paths)
    _assert_supported_numerics(result)
    cohort = result["diagnostics"]["assignment_effect"]["pseudobulk"]["assignment_cohort"]
    assert cohort["selected_assignment_levels"] == ["A", "B"]
    assert cohort["unselected_assignment_levels"] == ["C"]


def test_selected_cohort_respects_declared_nondefault_condition_column(tmp_path):
    task, paths = _fixture(tmp_path / "input", conditions=("A", "B", "C"))
    task["analysis_columns"]["condition"] = "treatment"
    def change(rows):
        for row in rows:
            row["treatment"] = row["condition"]
            row["condition"] = "irrelevant_observation"
    _metadata(paths, change)
    diagnostic = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert diagnostic["status"] == "audited", diagnostic
    assert diagnostic["condition_cohort"]["selected_condition_levels"] == ["A", "B"]
    _assert_supported_numerics(_tool("cell_type_state_analysis", task, paths))


def test_perturbation_prerequisite_rejects_failed_support_and_episode_remains_unsupported(tmp_path):
    from devbench.analysis.episodes import AnalysisEpisode
    from devbench.study.biology_broker import _audit_completed
    task, paths = _perturbation_fixture(tmp_path / "input", problem="selected_time")
    audit = _tool("perturbation_design_audit", task, paths)
    assert audit["diagnostics"]["status"] == "support_unavailable", audit
    event = {"status": "succeeded", "receipt_id": "local-synthetic-audit"}
    assert not _audit_completed(event, {event["receipt_id"]: audit})
    episode = AnalysisEpisode.create(tmp_path / "episode", question="Assigned B versus control A",
        assay="scrna", public_task=task,
        inputs={"expression": paths["counts_path"], "metadata": paths["metadata_path"], "annotations": paths["annotations_path"]},
        allow_data_execution=True)
    assert episode.supported is False, "Perturbation episodes remain an explicitly unsupported route"
    assert episode.step({"kind": "tool", "tool": "perturbation_design_audit"})["status"] == "rejected"
