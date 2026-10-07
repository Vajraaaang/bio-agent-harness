"""Public intake evidence, not protected reference answers or model evaluation."""
import csv
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import workspace
from tests.test_episode_planning import Selector, dataset


def rewrite_metadata(data, transform):
    path = Path(data["inputs"]["metadata"])
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    transform(rows)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def inspect(root, data):
    from devbench.analysis.episode_planning import propose_analysis
    selector = Selector({"kind": "request_information", "message": "Review the observed design and provenance."})
    result = propose_analysis(root / "planning", question="Assess the requested condition comparison",
        dataset=data, selector=selector, allow_data_read=True)
    assert result["status"] == "awaiting_information", result
    return selector.calls[0]


@pytest.mark.parametrize("confounded", [False, True])
def test_batch_support_distinguishes_designs_without_disclosing_batch_identifiers(workspace, confounded):
    data = dataset(workspace)
    def change(rows):
        for index, row in enumerate(rows):
            batch = index // 4 if confounded else index % 2
            row["batch"] = f"sensitive-library-{batch}"
    rewrite_metadata(data, change)
    context = inspect(workspace, data)
    design = context["observed"]["design_summary"]
    levels = design["batch_condition_support"][0]["levels"]
    assert len(levels) == 2
    assert all(len(level["conditions"]) == (1 if confounded else 2) for level in levels)
    assert all(sum(x["biological_units"] for x in level["conditions"]) == 4 for level in levels)
    assert "sensitive-library" not in json.dumps(context)
    assert design["identifiability"] == "not_evaluated"


@pytest.mark.parametrize("complete", [False, True])
def test_observed_pairing_is_visible_without_claiming_bulk_method_support(workspace, complete):
    data = dataset(workspace)
    def change(rows):
        for index, row in enumerate(rows):
            if complete or index == 4:
                row["donor_id"] = f"sensitive-donor-{index % 4}"
            elif index == 0:
                row["donor_id"] = "sensitive-donor-0"
    rewrite_metadata(data, change)
    context = inspect(workspace, data)
    design = context["observed"]["design_summary"]
    assert design["repeated_unit_layout"] == ("complete_repeated" if complete else "incomplete_repeated")
    assert design["units_with_varying_design_coordinates"] >= 1
    assert "sensitive-donor" not in json.dumps(context)
    admission = context["admission"]
    assert admission["software_support"]["design_compatibility"] == "not_yet_validated"
    assert "bulk_repeated_coordinates_not_supported" in admission["software_support"]["restrictions"]


def test_unverified_units_do_not_become_a_scientific_nonidentifiability_claim(workspace):
    data = dataset(workspace)
    data["public_task"]["unit_hierarchy"] = {"population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id", "identity_status": "unverified"}
    context = inspect(workspace, data)
    admission = context["admission"]
    assert admission["evidence_status"]["experimental_provenance"] == "not_independently_verified"
    assert admission["scientific_status"]["identifiability"] == "not_established"
    assert admission["software_support"]["proposal_candidate_available"] is False
    assert context["observed"]["design_summary"]["unit_condition_patterns"] is None
    assert admission["available_agent_actions"] == ["request_information", "refuse"]


def test_capture_identity_is_observed_separately_from_donor_identity(workspace):
    data = dataset(workspace, cellular=True, continuous=False)
    context = inspect(workspace, data)
    capture = context["observed"]["design_summary"]["capture_units"]
    assert capture == {"column_present": True, "observed_labels": 1,
        "provenance": "metadata_labels_not_independently_verified_capture_history"}
    assert context["admission"]["scientific_status"]["population_support"] == "not_evaluated"


def test_admission_keeps_question_status_separate_from_allowed_interpretation(workspace):
    context = inspect(workspace, dataset(workspace))
    admission = context["admission"]
    assert admission["format"] == "devbench.proposal-admission/1"
    assert admission["requested_claim"] == "not_classified"
    assert admission["permitted_interpretation"]["scope"] == "declared_scale_expression_association"
    assert admission["scientific_status"]["statistical_calibration"] == "not_established"
    assert admission["available_agent_actions"] == ["propose", "request_information", "refuse"]


def test_backed_proposal_uses_the_same_admission_dimensions_without_inventing_donor_information(workspace):
    from tests.test_workbench_planning import dataset as backed_dataset
    context = inspect(workspace, backed_dataset(workspace))
    admission = context["admission"]
    assert admission["permitted_interpretation"]["scope"] == "scrna_qc_integration_annotation"
    assert admission["scientific_status"]["identifiability"] == "not_established"
    assert admission["evidence_status"]["experimental_provenance"] == "not_independently_verified"
    assert "independent_donors" not in json.dumps(admission)
    assert context["allowed_workflows"] == ["scrna_qc_integration_annotation/1"]


@pytest.mark.parametrize("assay,kind", [("bulk", "raw_reads"), ("snrna", "count_matrix")])
def test_unsupported_inputs_are_not_given_a_supported_interpretation_scope(workspace, assay, kind):
    data = dataset(workspace, continuous=False)
    data.update(assay=assay, input_kind=kind)
    context = inspect(workspace, data)
    assert context["allowed_workflows"] == []
    assert context["admission"]["permitted_interpretation"]["scope"] == "unavailable_for_declared_input"
