"""Finalization resolves real retained steps and exact source/derived inputs."""
from copy import deepcopy

import pytest

from devbench.analysis import rna_scientific_trial as api
from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis.checkpoints import digest
from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
from devbench.analysis.rna_execution_inputs import load_execution_inputs
from devbench.analysis.method_planner import ScientificMethodPlanner
from tests.test_rna_comparison_outputs import prepared
from tests.test_rna_publication_profiles import source


def bound_snapshot(tmp_path):
    assert hasattr(api, "validate_assessment_workflow_snapshot")
    service, step, plan_ref = prepared(tmp_path)
    plan = profiles.load_assessment_plan(plan_ref, workspace=tmp_path, service=service)
    inputs = load_execution_inputs(plan["execution_inputs_ref"], artifact_service=service)
    receipt = profiles.read_reference_json(inputs["transformation_receipt_ref"], workspace=tmp_path, service=service)
    binding = receipt["input_binding"]
    dataset = {"format": "devbench.rna-trial-dataset/2", "binding": binding, "classification": "synthetic",
        "access_record": source(tmp_path / "dataset-access.json", {"scope": "Synthetic engineering fixture"}),
        "inspection_sha256": digest(inspect_registered_bulk_rna_input(service, binding))}
    return service, {"steps": [step]}, inputs, dataset


def test_assessment_workflow_uses_actual_retained_steps_and_input_lineage(tmp_path):
    service, snapshot, inputs, dataset = bound_snapshot(tmp_path)
    assert api.validate_assessment_workflow_snapshot(snapshot, methods=service,
        execution_inputs=inputs, dataset=dataset) == snapshot


@pytest.mark.parametrize("attack", ["arbitrary", "step_identity", "parameters", "input", "dataset", "method"])
def test_self_digested_workflow_or_unrelated_bindings_cannot_finalize(tmp_path, attack):
    service, snapshot, inputs, dataset = bound_snapshot(tmp_path)
    snapshot = deepcopy(snapshot)
    if attack == "arbitrary": snapshot = {"steps": [{"method": "self-declared implementation"}]}
    elif attack == "step_identity": snapshot["steps"][0]["step_id"] = "e" * 32
    elif attack == "parameters": snapshot["steps"][0]["parameters"] = {"invented": True}
    elif attack == "input": snapshot["steps"][0]["inputs"]["counts"] = inputs["original_refs"]["metadata"]["artifact_id"]
    elif attack == "dataset": dataset["binding"]["matrix_artifact_id"] = inputs["original_refs"]["metadata"]["artifact_id"]
    else: snapshot["steps"][0]["method_version_id"] = "f" * 64
    with pytest.raises((ValueError, KeyError), match="workflow|step|input|dataset|method|record|Bulk"):
        api.validate_assessment_workflow_snapshot(snapshot, methods=service, execution_inputs=inputs, dataset=dataset)


def test_actual_empty_planner_workflow_cannot_finalize_without_method_checks(tmp_path):
    service, _, inputs, dataset = bound_snapshot(tmp_path)
    planner = ScientificMethodPlanner(service)
    state = planner.create({"scientific_objective": "Synthetic empty workflow check", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in inputs["execution_refs"].values()]},
        actor="synthetic-test", data_classification="synthetic")
    empty = api.workflow_snapshot(planner, {"planner_session_id": state["session_id"], "experiment_ids": []})
    with pytest.raises(ValueError, match="nonempty|empty|experiment|step"):
        api.validate_assessment_workflow_snapshot(empty, methods=service, execution_inputs=inputs, dataset=dataset)


def test_same_artifact_ids_with_changed_declarations_are_not_the_original_binding(tmp_path):
    service, snapshot, inputs, dataset = bound_snapshot(tmp_path)
    changed = deepcopy(dataset)
    changed["binding"]["declarations"]["metadata_roles"]["condition"] = "batch"
    changed["inspection_sha256"] = digest(inspect_registered_bulk_rna_input(service, changed["binding"]))
    with pytest.raises(ValueError, match="binding|declaration|lineage|dataset"):
        api.validate_assessment_workflow_snapshot(snapshot, methods=service, execution_inputs=inputs, dataset=changed)
