"""P3 local software controls; no biological or method-selection qualification.

All observations are tiny authored fixtures. The capture-grouping check uses
real AnnData/checkpoint paths and the detector's honest small-channel refusal;
it does not assert numerical Scrublet accuracy or use a model service.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Requires explicitly enabled local synthetic software fixtures",
)


def _fixture(root, *, paired=False, incomplete=False, coordinate=None,
             many_specimens=False, ambiguous=False, donor_count=4, conditions=("A", "B")):
    """Public donor/cell observations, not hidden benchmark mechanisms."""
    root.mkdir()
    rows = []
    for condition in conditions:
        for donor_number in range(donor_count):
            if incomplete and condition == "B" and donor_number == 0:
                continue
            donor = f"d{donor_number}" if paired else f"{condition}d{donor_number}"
            for cell_number in range(4):
                index = len(rows)
                specimen = f"{condition}s{donor_number}"
                if coordinate or many_specimens:
                    specimen += f"-{cell_number // 2}"
                row = {"sample_id": f"c{index}", "cell_id": f"c{index}",
                    "donor_id": donor, "specimen_id": specimen,
                    "library_id": f"lib-{specimen}", "condition": condition,
                    "capture_batch": "one-capture"}
                if coordinate:
                    row[coordinate] = cell_number // 2
                rows.append(row)
    ids = [row["sample_id"] for row in rows]
    genes = ["markerA", "markerB", *[f"g{i}" for i in range(40)]]
    with (root / "metadata.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (root / "expression.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *ids])
        for gene_number, gene in enumerate(genes):
            values = []
            for index, row in enumerate(rows):
                if gene_number < 2:
                    value = 80 if (index % 4 < 2) == (gene_number == 0) else 1
                    if ambiguous and index == 0:
                        value = 40
                else:
                    donor_number = int(row["donor_id"][-1])
                    value = 20 + gene_number + 5 * (donor_number % 3)
                    value += (index % 4) + (15 if row["condition"] == "B" and gene_number < 10 else 0)
                values.append(value)
            writer.writerow([gene, *values])
    annotations = {"cell_annotation_reference": {
        "reference_identity": "authored-two-type-panel", "version": "fixture-v1",
        "reference_scope": "synthetic marker support only",
        "markers": [{"label": "typeA", "gene_ids": ["markerA"]},
                    {"label": "typeB", "gene_ids": ["markerB"]}],
        "query_cell_labels_supplied": False}}
    (root / "annotations.json").write_text(json.dumps(annotations))
    roles = {"condition": "condition", "time": None, "dose": None,
        "covariate": None, "population_unit": "donor_id", "batches": ["capture_batch"]}
    if coordinate:
        roles["time" if coordinate == "visit_day" else "dose"] = coordinate
    task = {"measurement": "umi_counts", "population_unit_label": "donor_id",
        "observation_id_label": "sample_id", "gene_ids": genes,
        "unit_hierarchy": {"population_unit": "donor", "population_unit_label": "donor_id",
            "specimen_label": "specimen_id", "library_label": "library_id",
            "identity_status": "caller_declared"},
        "context": {"biological_unit": "donor", "condition_labels": ["A", "B"],
            "requested_condition_contrast": {"numerator": "B", "denominator": "A"}},
        "analysis_columns": roles,
        "questions": [{"target_id": "cell_state_effect", "estimand": "Donor-level within-type B minus A",
            "units": "log2 count-rate contrast", "contrast": "B minus A",
            "reference_population": "declared independent donors",
            "prediction_reference": "observed donor population"}]}
    return task, {"counts_path": str(root / "expression.csv"),
        "metadata_path": str(root / "metadata.csv"),
        "annotations_path": str(root / "annotations.json"), "allow_data_execution": True}


def _tool(tool, task, paths):
    from devbench.study.biology_tools_singlecell import analyze_singlecell_tool
    return analyze_singlecell_tool(tool, task, **paths)


def test_loader_retains_coordinator_bound_singlecell_column_roles(tmp_path):
    from devbench.study.biology_tool_io import load_observed_data
    task, paths = _fixture(tmp_path / "input", coordinate="visit_day")
    data = load_observed_data(task, **paths)
    assert data.context.get("analysis_columns") == task["analysis_columns"], (
        "Declared design coordinates must reach aggregation; metadata values alone do not assign column roles")


@pytest.mark.parametrize("coordinate", ["visit_day", "assigned_dose"])
def test_public_membership_audit_cannot_silently_pool_varying_declared_coordinate(tmp_path, coordinate):
    task, paths = _fixture(tmp_path / "input", coordinate=coordinate)
    result = _tool("pseudobulk_membership_audit", task, paths)
    diagnostic = result["diagnostics"]
    assert diagnostic["status"] == "support_unavailable", diagnostic
    assert "coordinate" in diagnostic["reason"] or "repeated" in diagnostic["reason"]
    assert result["target_results"] == [], "An audit must not fabricate target estimates"


@pytest.mark.parametrize("role_location", ["top", "context_only"])
def test_episode_prerequisite_blocks_coordinate_collapse_before_estimation(tmp_path, role_location):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = _fixture(tmp_path / "input", coordinate="visit_day")
    if role_location == "context_only":
        task["context"]["analysis_columns"] = task.pop("analysis_columns")
    episode = AnalysisEpisode.create(tmp_path / "episode", question="Compare B minus A within type at the declared visits",
        assay="scrna", public_task=task,
        inputs={"expression": paths["counts_path"], "metadata": paths["metadata_path"],
                "annotations": paths["annotations_path"]}, allow_data_execution=True)
    for name in ("audit_expression_quality", "validate_biological_units"):
        response = episode.step({"kind": "tool", "tool": name})
        assert response["status"] == "succeeded", response
    audit = episode.step({"kind": "tool", "tool": "pseudobulk_membership_audit"})
    assert audit["result"]["diagnostics"]["status"] == "support_unavailable", audit
    rejected = episode.step({"kind": "tool", "tool": "cell_type_state_analysis"})
    assert rejected["status"] == "rejected", rejected


def test_context_only_constant_visit_is_admitted_through_actual_episode(tmp_path):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = _fixture(tmp_path / "input", coordinate="visit_day")
    task["context"]["analysis_columns"] = task.pop("analysis_columns")
    metadata = Path(paths["metadata_path"])
    with metadata.open() as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row["visit_day"] = "0"
    with metadata.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    episode = AnalysisEpisode.create(tmp_path / "episode", question="B minus A at the observed visit",
        assay="scrna", public_task=task,
        inputs={"expression": paths["counts_path"], "metadata": paths["metadata_path"],
                "annotations": paths["annotations_path"]}, allow_data_execution=True)
    for name in ("audit_expression_quality", "validate_biological_units"):
        assert episode.step({"kind": "tool", "tool": name})["status"] == "succeeded"
    audit = episode.step({"kind": "tool", "tool": "pseudobulk_membership_audit"})
    assert audit["result"]["diagnostics"]["status"] == "audited", audit
    assert audit["result"]["diagnostics"]["analysis_columns"]["time"] == "visit_day"
    result = episode.step({"kind": "tool", "tool": "cell_type_state_analysis"})
    assert result["status"] == "succeeded", result
    assert result["result"]["target_results"][0]["payload"] is not None


@pytest.mark.parametrize("paired,expected", [(False, "independent_conditions"), (True, "complete_repeated")])
def test_public_membership_audit_exposes_independent_and_specimen_support(tmp_path, paired, expected):
    task, paths = _fixture(tmp_path / "input", paired=paired, many_specimens=True)
    result = _tool("pseudobulk_membership_audit", task, paths)
    diagnostic = result["diagnostics"]
    assert diagnostic["status"] == "audited", diagnostic
    assert diagnostic["unit_audit"]["cells_count_as_independent_donors"] is False
    support = diagnostic.get("design_support_by_type")
    assert isinstance(support, dict) and set(support) == {"typeA", "typeB"}, diagnostic
    for details in support.values():
        assert details["independent_units_per_condition"] == {"A": 4, "B": 4}
        assert details["repeated_unit_layout"] == expected
        assert details["specimen_count"] == 8
    assert diagnostic["missing_groups_filled_with_zeros"] is False


def test_complete_pairing_keeps_donor_blocking_rle_and_actual_test_family(tmp_path):
    task, paths = _fixture(tmp_path / "input", paired=True)
    result = _tool("cell_type_state_analysis", task, paths)
    target = result["target_results"][0]
    assert target["payload"] is not None, result
    rows = target["payload"]["gene_statistics"]
    tested = [row for row in rows if row.get("p_value") is not None]
    assert tested
    assert target["payload"]["multiplicity_scope"] == [row["hypothesis_id"] for row in tested]
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in tested)
    for details in result["diagnostics"]["cell_state_effect"]["fits_by_type"].values():
        assert details["design"]["paired_donor_blocking"] is True
        assert details["model_family_attempted"] == "negative_binomial"
        assert details["normalization"]["method"].startswith("median_of_ratios")
        assert details["calibration"]["method"]


def test_incomplete_pairing_stays_unsupported_without_successful_looking_estimates(tmp_path):
    task, paths = _fixture(tmp_path / "input", paired=True, incomplete=True)
    result = _tool("cell_type_state_analysis", task, paths)
    target = result["target_results"][0]
    assert target["payload"] is None, result
    diagnostic = result["diagnostics"]["cell_state_effect"]
    assert diagnostic["status"] in {"analysis_failed", "support_unavailable"}
    assert "incomplete_repeated_donor_covariance_model_not_implemented" in diagnostic["reason"]


@pytest.mark.parametrize("case", ["normalized_assay", "log1p_normalization", "batch_corrected_expression",
                                 "batch_correction", "log2", "scaling"])
def test_raw_count_route_rejects_explicit_transformation_contradiction(tmp_path, case):
    task, paths = _fixture(tmp_path / "input")
    if case == "normalized_assay":
        task["measurement_layer"] = {"measurement": "umi_counts", "assay_semantics": "normalized_expression"}
    else:
        task["transformation_history"] = [{"operation": case, "output_semantics": "normalized_expression"}]
    with pytest.raises(ValueError, match="(?i)(assay|transformation|count.*provenance|measurement)"):
        _tool("pseudobulk_membership_audit", task, paths)


def test_competing_markers_remain_unknown_and_reference_evidence_is_retained(tmp_path):
    task, paths = _fixture(tmp_path / "input", ambiguous=True)
    result = _tool("pseudobulk_membership_audit", task, paths)
    diagnostic = result["diagnostics"]
    assert diagnostic["status"] == "audited", result
    assert diagnostic["label_uncertainty_count"] >= 1, diagnostic
    assert diagnostic["uncertain_cells_not_used_for_type_specific_model"] >= 1
    included = {cell for row in diagnostic["membership"] for cell in row["cell_ids"]}
    assert "c0" not in included, "Tied marker support cannot become a confident type assignment"
    evidence = diagnostic.get("annotation_evidence")
    assert isinstance(evidence, dict), "Reference and competing-marker uncertainty must survive the public audit"
    assert evidence["reference_identity"] == "authored-two-type-panel"
    assert evidence["reference_version"] == "fixture-v1"
    assert evidence["uncertain_cell_count"] == diagnostic["label_uncertainty_count"]
    assert evidence["scores_are_calibrated_probabilities"] is False
    assert set(evidence["marker_labels"]) == {"typeA", "typeB"}


def test_default_workbench_does_not_choose_harmony_before_data_inspection():
    from devbench.analysis.workbench import default_parameters
    values = default_parameters(data_sha256="a" * 64)
    assert values["correction"] == "none", "Integration requires a recorded dataset-specific choice"


def test_doublet_stage_uses_capture_library_instead_of_shared_integration_batch(tmp_path):
    ad = pytest.importorskip("anndata")
    pytest.importorskip("scanpy")
    import numpy as np
    import pandas as pd
    from scipy import sparse
    from devbench.analysis.annotation import run_doublets
    from devbench.analysis.checkpoints import CheckpointStore

    names = [f"c{i}" for i in range(16)]
    observation = pd.DataFrame({"integration_batch": "same-batch",
        "library_id": ["capture-A"] * 8 + ["capture-B"] * 8}, index=names)
    source = tmp_path / "captures.h5ad"
    ad.AnnData(sparse.csr_matrix(np.full((16, 12), 3, dtype=np.int32)), obs=observation,
        var=pd.DataFrame(index=[f"g{i}" for i in range(12)])).write_h5ad(source)
    store = CheckpointStore(tmp_path / "checkpoints", {"fixture": "capture-groups"})
    def qc(folder):
        frame = observation.copy()
        frame["qc_pass"] = True
        frame.to_csv(folder / "metrics.csv")
        return {"scope": "authored grouping fixture"}
    store.run("qc", qc)
    try:
        destination = run_doublets(str(source), store, layer=None, batch_key="integration_batch",
            capture_key="library_id", seed=11)
    except TypeError as error:
        pytest.fail("Capture-aware doublet-stage contract is missing: " + str(error))
    report = json.loads((destination / "report.json").read_text())
    assert report["status"] == "unavailable"
    assert sorted(row["cells"] for row in report["batches"]) == [8, 8]
    assert all(row["status"] == "unavailable" for row in report["batches"])
    assert report["capture_key"] == "library_id"
    assert "assumed_to_identify_library" not in report["scope"]


def test_missing_requested_raw_layer_never_falls_back_to_integer_valued_x(tmp_path):
    ad = pytest.importorskip("anndata")
    import numpy as np
    import pandas as pd
    from devbench.analysis.io import open_expression
    source = tmp_path / "no-count-layer.h5ad"
    ad.AnnData(np.ones((4, 3)), obs=pd.DataFrame(index=[f"c{i}" for i in range(4)]),
        var=pd.DataFrame(index=[f"g{i}" for i in range(3)])).write_h5ad(source)
    with pytest.raises(ValueError, match="(?i)layer"):
        with open_expression(source, layer="counts"):
            pytest.fail("An absent requested count assay must remain unavailable")


@pytest.mark.parametrize("role", ["time", "dose", "covariate"])
def test_declared_coordinate_confounding_is_not_omitted(tmp_path, role):
    task, paths = _fixture(tmp_path / "input")
    task["analysis_columns"][role] = "condition"
    result = _tool("pseudobulk_membership_audit", task, paths)
    assert result["diagnostics"]["status"] == "support_unavailable"
    assert "coordinate" in result["diagnostics"]["reason"]


def test_missing_declared_coordinate_is_unavailable(tmp_path):
    task, paths = _fixture(tmp_path / "input")
    task["analysis_columns"]["time"] = "missing_visit"
    result = _tool("pseudobulk_membership_audit", task, paths)
    assert result["diagnostics"]["status"] == "support_unavailable"


def test_declared_generic_batch_confounding_is_audited_before_fitting(tmp_path):
    task, paths = _fixture(tmp_path / "input")
    task["analysis_columns"]["batches"] = ["condition"]
    result = _tool("pseudobulk_membership_audit", task, paths)
    assert result["diagnostics"]["status"] == "support_unavailable"
    assert "rank_deficient" in result["diagnostics"]["reason"]


def test_untouched_selected_counts_allow_normalized_x_history(tmp_path):
    task, paths = _fixture(tmp_path / "input")
    task["measurement_layer"] = {"measurement": "umi_counts", "assay_semantics": "raw_counts",
        "layer": "counts"}
    task["transformation_history"] = [{"operation": "log1p_normalization",
        "output_layer": "X", "output_semantics": "normalized_expression"}]
    result = _tool("pseudobulk_membership_audit", task, paths)
    assert result["diagnostics"]["status"] == "audited"
    task["transformation_history"][0]["output_layer"] = "counts"
    with pytest.raises(ValueError, match="(?i)(assay|transformation|provenance)"):
        _tool("pseudobulk_membership_audit", task, paths)


def test_per_type_support_retains_context_and_bounded_annotation(tmp_path):
    task, paths = _fixture(tmp_path / "input")
    result = _tool("pseudobulk_membership_audit", task, paths)
    diagnostic = result["diagnostics"]
    assert diagnostic["analysis_columns"] == task["analysis_columns"]
    assert diagnostic["support_policy"]["origin"] == "software_admission_policy"
    assert diagnostic["support_policy"]["scientific_sufficiency_established"] is False
    assert diagnostic["design_support_by_type"]["typeA"]["low_support_profiles"]
    assert all(row["library_ids"] for row in diagnostic["membership"])
    evidence = diagnostic["annotation_evidence"]
    assert len(evidence["cell_score_preview"]) <= 50
    assert evidence["marker_coverage"]["typeA"]["observed_count"] == 1
    assert all("margin" in row and "scores" in row for row in evidence["cell_score_preview"])


def test_integration_choice_provenance_and_legacy_validation():
    from devbench.analysis.workbench import default_parameters, _validated_parameters
    default = default_parameters(data_sha256="a" * 64)
    assert default["integration_decision"]["origin"] == "software_default"
    assert default["integration_decision"]["rationale"]
    explicit = default_parameters(data_sha256="a" * 64, correction="harmony",
        integration_rationale="Inspect condition-preserving embedding after observed site differences",
        capture_key="library_id")
    assert explicit["integration_decision"]["origin"] == "caller_supplied"
    assert explicit["integration_decision"]["method"] == "harmony"
    legacy = {key: value for key, value in explicit.items()
              if key not in {"integration_decision", "integration_rationale", "capture_key"}}
    assert _validated_parameters(legacy) == legacy


def test_missing_capture_role_does_not_pool_integration_batches(tmp_path):
    from devbench.analysis.annotation import run_doublets
    from devbench.analysis.checkpoints import CheckpointStore
    import pandas as pd
    store = CheckpointStore(tmp_path / "checkpoints", {"fixture": "missing-capture"})
    def qc(folder):
        pd.DataFrame({"qc_pass": [True, True], "batch": ["same", "same"]},
            index=["a", "b"]).to_csv(folder / "metrics.csv")
        return {}
    store.run("qc", qc)
    destination = run_doublets(str(tmp_path / "unread.h5ad"), store, layer=None,
        batch_key="batch", seed=0)
    report = json.loads((destination / "report.json").read_text())
    assert report["status"] == "unavailable"
    assert report["reason"] == "capture_library_role_not_declared"


def test_marker_stage_retains_competition_and_panel_identity():
    ad = pytest.importorskip("anndata")
    import numpy as np
    import pandas as pd
    from devbench.analysis.singlecell import marker_annotation
    data = ad.AnnData(np.array([[10, 10, 10, 10], [30, 30, 0, 0]]),
        obs=pd.DataFrame(index=["mixed", "supported"]), var=pd.DataFrame(index=["a", "b", "c", "d"]))
    labels, evidence = marker_annotation(data, {"typeA": ["a", "b"], "typeB": ["c", "d"]})
    assert labels["mixed"] == "uncertain"
    assert evidence["reference_identity"].startswith("sha256:")
    assert evidence["reference_version"]
    assert evidence["scores_are_calibrated_probabilities"] is False
    assert evidence["cell_score_preview"][0]["margin"] == 0
    assert set(evidence["cell_score_preview"][0]["competing_scores"]) == {"typeA", "typeB"}


def test_absent_type_has_missing_support_not_zero_expression(tmp_path):
    task, paths = _fixture(tmp_path / "input")
    path = Path(paths["annotations_path"])
    annotations = json.loads(path.read_text())
    annotations["cell_annotation_reference"]["markers"].append({"label": "absent", "gene_ids": ["unmeasured"]})
    path.write_text(json.dumps(annotations))
    result = _tool("pseudobulk_membership_audit", task, paths)
    diagnostic = result["diagnostics"]
    assert diagnostic["status"] == "audited"
    support = diagnostic["design_support_by_type"]["absent"]
    assert support["independent_units_per_condition"] == {"A": 0, "B": 0}
    assert support["status"] == "support_unavailable"
    assert len(support["missing_memberships"]) == 8
    assert all(row["cell_type"] != "absent" for row in diagnostic["membership"])
    assert diagnostic["missing_groups_filled_with_zeros"] is False


def test_cli_default_none_and_explicit_capture_role_are_bound(tmp_path, monkeypatch, capsys):
    from devbench.analysis import workbench
    observed = []
    def plan(source, parameters):
        observed.append(parameters)
        return {"scope": "parameter-binding-only"}
    monkeypatch.setattr(workbench, "resource_plan", plan)
    arguments = ["--data", str(tmp_path / "unread.h5ad"), "--out", str(tmp_path / "plan"), "--plan-only"]
    workbench.main(arguments)
    assert observed[-1]["correction"] == "none"
    assert observed[-1]["integration_decision"]["origin"] == "software_default"
    workbench.main(arguments + ["--correction", "harmony", "--capture-key", "capture_id",
        "--integration-rationale", "Authored parameter-binding control"])
    assert observed[-1]["integration_decision"]["origin"] == "caller_supplied"
    assert observed[-1]["capture_key"] == "capture_id"


def test_zero_annotation_margin_never_promotes_tied_markers(tmp_path):
    task, paths = _fixture(tmp_path / "input", ambiguous=True)
    from devbench.study.biology_tools_singlecell import analyze_singlecell_tool
    result = analyze_singlecell_tool("pseudobulk_membership_audit", task,
        options={"annotation_margin": 0}, **paths)
    assert result["diagnostics"]["label_uncertainty_count"] >= 1
    assert "c0" not in {cell for row in result["diagnostics"]["membership"] for cell in row["cell_ids"]}


@pytest.mark.parametrize("operation", ["batch_correction", "log2", "scaling"])
def test_count_transformation_operation_cannot_hide_behind_missing_semantics(tmp_path, operation):
    task, paths = _fixture(tmp_path / "input")
    task["transformation_history"] = [{"operation": operation}]
    with pytest.raises(ValueError, match="(?i)(transformation|provenance)"):
        _tool("pseudobulk_membership_audit", task, paths)


def test_reviewed_capture_roles_respect_explicit_none_and_legacy_absence():
    from devbench.analysis.workbench import default_parameters
    from devbench.analysis.workbench_proposals import _configuration
    parameters = default_parameters(data_sha256="a" * 64, capture_key="capture_id")
    declarations = {"assay": "scrna", "measurement": "umi_counts", "count_provenance": "authored fixture",
        "capture_unit_key": "capture_id", "capture_units_confirmed": True,
        "annotation_provenance": "no external annotation"}
    _, eligible = _configuration("Authored capture binding", parameters, declarations, 30)
    assert eligible is True
    parameters["capture_key"] = None
    declarations["capture_unit_key"] = parameters["batch_key"]
    assert _configuration("Authored capture binding", parameters, declarations, 30)[1] is False
    parameters.pop("capture_key")
    assert _configuration("Authored capture binding", parameters, declarations, 30)[1] is True


def test_explicit_null_condition_role_cannot_select_a_default_column(tmp_path):
    from devbench.study.biology_tool_io import validate_public_task
    task, _ = _fixture(tmp_path / "input")
    task["analysis_columns"]["condition"] = None
    with pytest.raises(ValueError, match="(?i)condition"):
        validate_public_task(task)


def test_paired_multispecimen_count_aggregation_matches_independent_exact_sums(tmp_path):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_singlecell import _pseudobulk
    task, paths = _fixture(tmp_path / "input", paired=True, many_specimens=True)
    with Path(paths["metadata_path"]).open() as handle:
        metadata = list(csv.DictReader(handle))
    with Path(paths["counts_path"]).open() as handle:
        rows = list(csv.reader(handle))
    cell_ids = rows[0][1:]
    counts = {row[0]: dict(zip(cell_ids, map(int, row[1:]))) for row in rows[1:]}
    expected, labels = {}, []
    for cell in metadata:
        identity = cell["sample_id"]
        label = "typeA" if counts["markerA"][identity] > counts["markerB"][identity] else "typeB"
        labels.append(label)
        key = (cell["donor_id"], cell["condition"], label)
        expected.setdefault(key, []).append(identity)
    data = load_observed_data(task, **paths)
    aggregated, details = _pseudobulk(data, labels, {}, population_ids=["typeA", "typeB"])
    assert len(details["membership"]) == len(expected)
    # Integer addition is exact here: the declared reference tolerance is zero.
    for index, profile in enumerate(details["membership"]):
        cells = expected[(profile["donor_id"], profile["condition"], profile["cell_type"])]
        for number, gene in enumerate(aggregated.gene_ids):
            assert aggregated.expression[number, index] == sum(counts[gene][cell] for cell in cells)
        assert set(profile["cell_ids"]) == set(cells)


def test_bh_values_match_independent_reference_over_the_actual_family(tmp_path):
    task, paths = _fixture(tmp_path / "input", paired=True)
    payload = _tool("cell_type_state_analysis", task, paths)["target_results"][0]["payload"]
    tested = sorted((row for row in payload["gene_statistics"] if row.get("p_value") is not None),
                    key=lambda row: row["p_value"])
    assert set(payload["multiplicity_scope"]) == {row["hypothesis_id"] for row in tested}
    reference = 1.0
    for rank in range(len(tested), 0, -1):
        row = tested[rank - 1]
        reference = min(reference, row["p_value"] * len(tested) / rank)
        assert row["q_value"] == pytest.approx(reference, rel=1e-12, abs=1e-15)


def test_new_contextual_support_previews_are_bounded_with_full_counts(tmp_path):
    task, paths = _fixture(tmp_path / "input", donor_count=32)
    path = Path(paths["annotations_path"])
    annotations = json.loads(path.read_text())
    annotations["cell_annotation_reference"]["markers"].append({"label": "absent", "gene_ids": ["unmeasured"]})
    path.write_text(json.dumps(annotations))
    diagnostic = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    support = diagnostic["design_support_by_type"]
    assert support["typeA"]["low_support_profile_count"] == 64
    assert len(support["typeA"]["low_support_profiles"]) == 50
    assert support["absent"]["missing_membership_count"] == 64
    assert len(support["absent"]["missing_memberships"]) == 50
    assert len(diagnostic["annotation_evidence"]["cell_score_preview"]) == 50


def test_requested_ab_cohort_matches_audit_and_estimator_with_extra_c(tmp_path):
    task, paths = _fixture(tmp_path / "input", conditions=("A", "B", "C"))
    audit = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert audit["status"] == "audited", audit
    assert {row["condition"] for row in audit["membership"]} == {"A", "B", "C"}
    cohort = audit["condition_cohort"]
    assert cohort["selected_condition_levels"] == ["A", "B"]
    assert cohort["unselected_condition_levels"] == ["C"]
    assert cohort["unselected_profile_count"] == 8
    for support in audit["design_support_by_type"].values():
        assert support["independent_units_per_condition"] == {"A": 4, "B": 4}
    result = _tool("cell_type_state_analysis", task, paths)
    assert result["target_results"][0]["payload"] is not None
    assert result["diagnostics"]["cell_state_effect"]["pseudobulk"]["condition_cohort"] == cohort
    assert {row["condition"] for row in audit["membership"] if row["condition"] in cohort["selected_condition_levels"]} == {"A", "B"}


def test_missing_requested_condition_refuses_both_audit_and_estimator(tmp_path):
    task, paths = _fixture(tmp_path / "input", conditions=("A", "C"))
    audit = _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]
    assert audit["status"] == "support_unavailable"
    assert audit["condition_cohort"]["missing_requested_condition_levels"] == ["B"]
    result = _tool("cell_type_state_analysis", task, paths)
    assert result["target_results"][0]["payload"] is None
    assert result["diagnostics"]["cell_state_effect"]["status"] == "support_unavailable"


def test_partial_top_roles_preserve_context_coordinate_and_block_pooling(tmp_path):
    from devbench.study.biology_tool_io import load_observed_data, validate_public_task
    task, paths = _fixture(tmp_path / "input", coordinate="visit_day")
    original = task["analysis_columns"]
    task["context"]["analysis_columns"] = original
    task["analysis_columns"] = {"condition": "condition"}
    data = load_observed_data(task, **paths)
    assert data.context["analysis_columns"] == original
    projected = validate_public_task(task)
    assert projected["analysis_columns"] == original
    assert validate_public_task(projected) == projected
    result = _tool("pseudobulk_membership_audit", task, paths)
    assert result["diagnostics"]["status"] == "support_unavailable"
    assert "coordinate" in result["diagnostics"]["reason"]


@pytest.mark.parametrize("layout", ["context_only", "complete_agreement"])
def test_context_role_controls_retain_serializable_idempotent_projection(tmp_path, layout):
    from devbench.study.biology_tool_io import validate_public_task
    task, paths = _fixture(tmp_path / "input")
    roles = dict(task["analysis_columns"])
    task["context"]["analysis_columns"] = roles
    if layout == "context_only":
        task.pop("analysis_columns")
    projected = validate_public_task(task)
    assert projected["analysis_columns"] == roles
    assert validate_public_task(projected) == projected
    assert json.loads(json.dumps(projected, allow_nan=False)) == projected
    assert _tool("pseudobulk_membership_audit", task, paths)["diagnostics"]["status"] == "audited"


@pytest.mark.parametrize("replacement", [None, "other_visit"])
def test_explicit_context_role_conflicts_are_rejected(tmp_path, replacement):
    from devbench.study.biology_tool_io import validate_public_task
    task, _ = _fixture(tmp_path / "input", coordinate="visit_day")
    task["context"]["analysis_columns"] = dict(task["analysis_columns"])
    task["analysis_columns"] = {"condition": "condition", "time": replacement}
    with pytest.raises(ValueError, match="(?i)(contradict|role)"):
        validate_public_task(task)
