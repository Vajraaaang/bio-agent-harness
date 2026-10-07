"""Frozen routing uses captured parent nominations, never outcome quality."""
from copy import deepcopy
import importlib

import pytest

from devbench.analysis.workflow_state import digest, planning_binding


def ref(label):
    return {"artifact_id": digest(label), "sha256": digest({"bytes": label}),
            "artifact_type": "json", "size_bytes": 200}


def policy(stage="pre_fit_methods", allocation=1):
    return {"format": "devbench.rna-investigation-policy/1", "stage": stage,
            "allocation": allocation, "budget_policy_ref": ref("budget"),
            "source_digest": digest("source"), "runtime_digest": digest("runtime"),
            "max_context_bytes": 48_000}


def inputs(stage="pre_fit_methods", allocation=1):
    phase = "method_scientific_planning" if stage == "pre_fit_methods" else "method_preliminary_explanation"
    capture, usage = ref("capture"), ref("usage")
    plan = {"session_id": "a" * 32, "context": {"task": {"scientific_objective": "Compare temporal models"}},
            "selection_history": [{"attempt": "b" * 32, "phase": phase, "status": "accepted",
                "context_sha256": digest("context"), "evidence": {"capture_ref": capture, "usage_ref": usage}}],
            "user_parameter_requirements": [], "scientific_candidates": []}
    axis = "temporal_shape_or_complexity" if stage == "pre_fit_methods" else "composition_vs_intrinsic"
    items = [{"id": "ambiguity-1", "axis": axis, "hypothesis_ids": ["linear-shape", "nonlinear-shape"],
              "evidence_refs": [ref("observation")], "missing_information_status": "sufficient",
              "discriminating_criterion": "held_in_prediction_error"}]
    features = {"format": "devbench.rna-investigation-trigger/1", "stage": stage, "action_intent": "infer",
                "scope_binding": planning_binding(plan), "question_ref": ref("question"), "requirement_refs": [],
                "prerequisite_checks": [{"check_id": name, "status": "satisfied", "evidence_ref": ref(name),
                    "action_scope": "infer"} for name in ("input_integrity", "representation", "join", "biological_units",
                                                         "contrast", "rank", "chronology")],
                "fixed_procedure_requirement_ref": None, "nomination_ref": ref("nomination"),
                "ambiguity_items": items, "evidence_ready": True, "prior_fork_count": 0,
                "budget_policy_ref": ref("budget"), "admissible_child_count": allocation}
    nomination = {"parent_session_id": plan["session_id"], "selection_attempt": "b" * 32,
                  "context_sha256": digest("context"), "capture_ref": capture, "usage_ref": usage,
                  "ambiguity_items_sha256": digest(items)}
    diagnostics = {"features": features, "nomination": nomination, "requirement_records": [],
                   "scope_supported": True, "capability_supported": True,
                   "parent_reconciliation_reserved": True, "unresolved_dispatch": False}
    return plan, diagnostics, policy(stage, allocation)


def decide(plan, diagnostics, selected):
    try:
        module = importlib.import_module("devbench.analysis.rna_investigations")
    except ModuleNotFoundError:
        module = None
    assert module is not None, "The deterministic investigation policy is missing"
    return module.decide_investigation(plan, diagnostics, policy=selected)


@pytest.mark.parametrize("allocation", [0, 1, 2])
def test_frozen_allocation_does_not_force_two_children(allocation):
    plan, diagnostics, selected = inputs(allocation=allocation)
    if allocation == 2:
        second = deepcopy(diagnostics["features"]["ambiguity_items"][0])
        second.update(id="ambiguity-2", axis="valid_nuisance_model", hypothesis_ids=["adjust", "stratify"])
        diagnostics["features"]["ambiguity_items"].append(second)
        diagnostics["nomination"]["ambiguity_items_sha256"] = digest(diagnostics["features"]["ambiguity_items"])
    decision = decide(plan, diagnostics, selected)
    assert decision["allocated_children"] == allocation
    assert decision["status"] == ("triggered" if allocation else "not_triggered")


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_failed_or_unknown_prerequisite_blocks_dispatch(status):
    plan, diagnostics, selected = inputs()
    diagnostics["features"]["prerequisite_checks"][3]["status"] = status
    outcome = decide(plan, diagnostics, selected)
    assert outcome["status"] == "blocked" and outcome["allocated_children"] == 0


@pytest.mark.parametrize("field,value", [("prior_fork_count", 1), ("admissible_child_count", 0),
                                          ("evidence_ready", False), ("nomination_ref", None)])
def test_routine_or_unresolved_case_does_not_dispatch(field, value):
    plan, diagnostics, selected = inputs()
    diagnostics["features"][field] = value
    outcome = decide(plan, diagnostics, selected)
    assert outcome["status"] == "not_triggered" and outcome["allocated_children"] == 0


@pytest.mark.parametrize("field", ["capability_supported", "parent_reconciliation_reserved"])
def test_unsupported_security_or_unreserved_parent_capacity_stops_children(field):
    plan, diagnostics, selected = inputs()
    diagnostics[field] = False
    assert decide(plan, diagnostics, selected)["allocated_children"] == 0


def test_fixed_procedure_requires_exact_explicit_requirement():
    plan, diagnostics, selected = inputs()
    requirement = {"name": "fixed_procedure", "value": "fixed df=3", "evidence": "User instruction, request 1"}
    plan["user_parameter_requirements"] = [requirement]
    fixed = ref("requirement")
    diagnostics["features"].update(fixed_procedure_requirement_ref=fixed, requirement_refs=[fixed],
                                  scope_binding=planning_binding(plan))
    diagnostics["requirement_records"] = [{"ref": fixed, "requirement": deepcopy(requirement)}]
    assert decide(plan, diagnostics, selected)["allocated_children"] == 0
    diagnostics["requirement_records"][0]["requirement"]["evidence"] = "Assistant default"
    with pytest.raises(ValueError, match="requirement"):
        decide(plan, diagnostics, selected)


def test_nomination_must_match_actual_accepted_parent_capture():
    plan, diagnostics, selected = inputs()
    diagnostics["nomination"]["capture_ref"] = ref("unmetered-classifier")
    with pytest.raises(ValueError, match="captured|nomination"):
        decide(plan, diagnostics, selected)


def test_stage_axes_and_numbers_cannot_choose_trigger():
    plan, diagnostics, selected = inputs()
    diagnostics["features"]["ambiguity_items"][0]["axis"] = "composition_vs_intrinsic"
    with pytest.raises(ValueError, match="axis"):
        decide(plan, diagnostics, selected)
    plan, diagnostics, selected = inputs()
    diagnostics["features"]["p_value"] = .001
    with pytest.raises(ValueError, match="exact"):
        decide(plan, diagnostics, selected)


def test_missing_critical_fact_is_not_an_eligible_ambiguity():
    plan, diagnostics, selected = inputs()
    diagnostics["features"]["ambiguity_items"][0]["missing_information_status"] = "critical_unknown"
    diagnostics["nomination"]["ambiguity_items_sha256"] = digest(diagnostics["features"]["ambiguity_items"])
    assert decide(plan, diagnostics, selected)["allocated_children"] == 0


def test_stale_scope_and_changed_nomination_bytes_fail_closed():
    plan, diagnostics, selected = inputs()
    diagnostics["features"]["scope_binding"] = digest("other-dataset")
    with pytest.raises(ValueError, match="binding"):
        decide(plan, diagnostics, selected)
    plan, diagnostics, selected = inputs()
    diagnostics["features"]["ambiguity_items"][0]["hypothesis_ids"][1] = "changed"
    with pytest.raises(ValueError, match="nomination"):
        decide(plan, diagnostics, selected)
