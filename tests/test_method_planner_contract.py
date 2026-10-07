"""Synthetic contract checks for separated scientific planning and resolution."""

from copy import deepcopy
from importlib import import_module

import pytest
from jsonschema import Draft202012Validator


def _api():
    return import_module("devbench.analysis.method_planner_contract")


def _task():
    return {"scientific_objective": "Assess a prespecified treatment contrast",
            "computational_task": "differential_expression", "constraints": ["Synthetic fixtures only"]}


def _dataset():
    return {"observed": {"biological_replicates": 4, "raw_counts": True},
            "inferred": {}, "unknowns": []}


def _candidate(name="Replicate-aware method"):
    return {
        "name": name, "method_family": "replicate_aware_inference",
        "source": {"type": "primary_paper", "reference": "doi:10.example/synthetic-method"},
        "assumptions": [{"statement": "Independent biological replication is required",
                         "status": "satisfied", "evidence": "Four donor labels in synthetic metadata",
                         "hard_precondition": True, "field": "biological_replicates",
                         "operator": "at_least", "expected": 3}],
        "expected_inputs": ["raw_counts", "donor_metadata"],
        "dataset_fit": "plausible", "objective_fit": "strong",
        "known_failure_modes": ["Confounding prevents identification"],
        "parameter_candidates": [{"name": "seed", "value": 17, "origin": "user",
                                  "rationale": "Reproducible synthetic trial"}],
    }


def _metric(name="null_calibration"):
    return {"name": name, "category": "calibration", "direction": "target_range",
            "interpretation": "Calibration under a prespecified null contrast"}


def _recommendation():
    return {"choice": None, "confidence": "no_recommendation",
            "scientific_confidence": "no_recommendation", "implementation_confidence": "unknown",
            "rationale": "No trial evidence exists yet", "params": {},
            "approval_actions": [{"action": "approve_recommendation", "required": True,
                                  "status": "not_requested"}]}


def _plan():
    candidate = dict(_candidate(), availability="implementation_required", method_version_id=None,
                     implementation_confidence="unknown", applicability="eligible", blockers=[], discovery=None)
    return {"task": _task(), "dataset_summary": _dataset(), "candidates": [candidate],
            "experiments": [], "results": [], "recommendation": _recommendation()}


def test_scientific_context_is_detached_and_contains_no_inventory():
    dataset = _dataset()
    prior = {"observations": ["Replicate identity was checked"]}
    context = _api().scientific_context(_task(), dataset, prior)
    assert set(context) == {"task", "dataset_summary", "prior_state"}
    context["dataset_summary"]["observed"]["raw_counts"] = False
    context["prior_state"]["observations"].append("New observation")
    assert dataset["observed"]["raw_counts"] is True
    assert len(prior["observations"]) == 1


@pytest.mark.parametrize("key", ["registry", "method_registry", "methodRegistry", "capabilities",
                                  "capability", "availability", "allowed_tools", "allowedTools",
                                  "executable_methods", "method_inventory"])
def test_scientific_context_rejects_nested_capability_fields(key):
    dataset = _dataset()
    dataset["observed"]["nested"] = [{key: ["registered-only-method"]}]
    with pytest.raises(ValueError, match="capabilit|registry|inventory"):
        _api().scientific_context(_task(), dataset)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), (1, 2), {1: "coerced-key"}])
def test_scientific_context_rejects_nonfinite_or_non_json_values(value):
    dataset = _dataset()
    dataset["observed"]["bad"] = value
    with pytest.raises(ValueError):
        _api().scientific_context(_task(), dataset)


def test_scientific_context_rejects_cycles_and_excessive_nesting():
    cyclic = {}
    cyclic["loop"] = cyclic
    with pytest.raises(ValueError):
        _api().scientific_context(_task(), _dataset(), cyclic)
    nested = {}
    for _ in range(40):
        nested = {"level": nested}
    with pytest.raises(ValueError):
        _api().scientific_context(_task(), _dataset(), nested)


def test_scientific_context_rejects_unbounded_collections_and_text():
    for value in ([0] * 1000, "x" * 100_000):
        dataset = _dataset()
        dataset["observed"]["large"] = value
        with pytest.raises(ValueError):
            _api().scientific_context(_task(), dataset)


def test_json_byte_budget_is_checked_before_serialization(monkeypatch):
    module = _api()
    dataset = _dataset()
    dataset["observed"]["large"] = ["x" * 16_000] * 130

    def unexpected_serialization(_):
        pytest.fail("Oversized payload reached serialization")

    monkeypatch.setattr(module, "canonical_json", unexpected_serialization)
    with pytest.raises(ValueError, match="bound"):
        module.scientific_context(_task(), dataset)


def test_candidates_preserve_order_unknowns_and_empty_discovery():
    first, second = _candidate(), _candidate("Unregistered alternative")
    second["assumptions"][0].update(status="unknown", evidence=None)
    value = {"candidates": [first, second]}
    candidates = _api().validate_scientific_candidates(value)
    assert [item["name"] for item in candidates] == [first["name"], second["name"]]
    assert candidates[1]["assumptions"][0]["status"] == "unknown"
    candidates[0]["parameter_candidates"][0]["value"] = 99
    assert first["parameter_candidates"][0]["value"] == 17
    assert _api().validate_scientific_candidates({"candidates": []}) == []


@pytest.mark.parametrize("path,value", [
    (("extra",), True),
    (("candidates", 0, "availability"), "registered"),
    (("candidates", 0, "source", "type"), "registry"),
    (("candidates", 0, "source", "unrecognized"), True),
    (("candidates", 0, "assumptions", 0, "status"), "probably"),
    (("candidates", 0, "assumptions", 0, "hard_precondition"), "true"),
    (("candidates", 0, "assumptions", 0, "operator"), "approximately"),
    (("candidates", 0, "parameter_candidates", 0, "value"), float("nan")),
    (("candidates", 0, "parameter_candidates", 0, "origin"), "best_guess"),
    (("candidates", 0, "expected_inputs"), ["input"] * 1000),
])
def test_scientific_candidate_structure_is_closed_finite_and_bounded(path, value):
    payload = {"candidates": [_candidate()]}
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        _api().validate_scientific_candidates(payload)


def test_candidate_names_must_be_unique():
    with pytest.raises(ValueError, match="Duplicate"):
        _api().validate_scientific_candidates({"candidates": [_candidate(), _candidate()]})


def _discovery():
    return {"source_type": "author_repository", "reference": "https://example.org/official-method",
            "version": None, "commit": None, "license": "BSD-3-Clause",
            "correspondence_evidence": "Author repository linked by the primary source",
            "required_environment": {"runtime": "python"}}


def test_resolution_allows_truthful_unavailable_and_unpinned_discovery():
    value = {"bindings": [{"candidate": "Unregistered alternative", "method_version_id": None,
                           "discovery": _discovery()}]}
    result = _api().validate_resolution(value)
    assert result == value
    result["bindings"][0]["discovery"]["required_environment"]["runtime"] = "R"
    assert value["bindings"][0]["discovery"]["required_environment"]["runtime"] == "python"
    value["bindings"][0]["discovery"] = None
    assert _api().validate_resolution(value) == value


@pytest.mark.parametrize("reference", ["http://example.org/method", "https:///missing-host",
                                        "https://name:secret@example.org/method", "https://example.org:bad/path"])
def test_resolution_rejects_invalid_authoritative_source_urls(reference):
    discovery = dict(_discovery(), reference=reference)
    with pytest.raises(ValueError):
        _api().validate_resolution({"bindings": [{"candidate": "Candidate", "method_version_id": None,
                                                 "discovery": discovery}]})


def test_resolution_cannot_rewrite_scientific_candidates_or_duplicate_bindings():
    binding = {"candidate": "Candidate", "method_version_id": None, "discovery": None}
    with pytest.raises(ValueError):
        _api().validate_resolution({"bindings": [binding], "candidates": [_candidate()]})
    with pytest.raises(ValueError, match="Duplicate"):
        _api().validate_resolution({"bindings": [binding, deepcopy(binding)]})


def test_recommendation_preserves_abstention_and_separate_confidences():
    recommendation = _recommendation()
    assert _api().validate_recommendation(recommendation) == recommendation
    recommendation.update(choice="Candidate", confidence="medium", scientific_confidence="high",
                          implementation_confidence="low")
    result = _api().validate_recommendation(recommendation)
    result["params"]["seed"] = 17
    assert recommendation["params"] == {}


@pytest.mark.parametrize("change", [
    {"choice": "Candidate"},
    {"confidence": "low"},
    {"scientific_confidence": "low"},
    {"choice": "Candidate", "confidence": "medium"},
])
def test_recommendation_choice_and_abstention_must_agree(change):
    with pytest.raises(ValueError):
        _api().validate_recommendation(dict(_recommendation(), **change))


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_model_recommendation_cannot_assert_human_approval(status):
    recommendation = _recommendation()
    recommendation["approval_actions"][0]["status"] = status
    with pytest.raises(ValueError):
        _api().validate_recommendation(recommendation)


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_harness_plan_can_represent_recorded_approval_status(status):
    plan = _plan()
    plan["recommendation"]["approval_actions"][0]["status"] = status
    assert _api().validate_plan(plan) == plan


@pytest.mark.parametrize("name", ["p_value", "P-value", "qValue", "significant_genes",
                                  "Number of significant genes", "p_values"])
def test_outcome_directed_selection_metrics_are_rejected(name):
    with pytest.raises(ValueError, match="outcome|hypothesis|selection"):
        _api().validate_metrics([_metric(name)])


@pytest.mark.parametrize("name", ["p_value_calibration", "null_fdr", "q_value_calibration",
                                  "false_positive_rate", "effect_size_stability", "signal_preservation"])
def test_calibration_and_scientific_safety_metrics_are_allowed(name):
    assert _api().validate_metrics([_metric(name)]) == [_metric(name)]


def test_plan_requires_explicit_trial_validity_and_allows_coordinator_state():
    plan = _plan()
    experiment = {"id": "trial-1", "candidates": [plan["candidates"][0]["name"]],
                  "subset_spec": {"unit": "complete biological replicates"},
                  "subset_validity": {"preserves_preconditions": False,
                                      "rationale": "The proposed subset loses donors"},
                  "commands": [], "metrics": [_metric()], "resource_limits": {"cpu": 1},
                  "inputs": {}, "parameters": {}, "state": "proposed"}
    plan["experiments"].append(experiment)
    result = _api().validate_plan(plan)
    assert result["experiments"][0]["subset_validity"]["preserves_preconditions"] is False
    del experiment["subset_validity"]
    with pytest.raises(ValueError):
        _api().validate_plan(plan)


def test_plan_preserves_failed_results_and_unknown_implementation_state():
    plan = _plan()
    plan["results"] = [{"experiment_id": "trial-1", "candidate": plan["candidates"][0]["name"],
                        "status": "failed", "metric_values": {}, "artifacts": [], "provenance": {},
                        "failures": ["Input precondition missing"], "run_id": "trial-run-1"}]
    assert _api().validate_plan(plan) == plan
    plan["results"][0]["metric_values"]["runtime"] = float("inf")
    with pytest.raises(ValueError):
        _api().validate_plan(plan)


def test_plan_candidates_retain_missing_preconditions():
    plan = _plan()
    plan["candidates"][0]["applicability"] = "blocked_unknown"
    plan["candidates"][0]["blockers"] = ["Raw input type is unknown"]
    plan["candidates"][0]["assumptions"][0].update(status="unknown", evidence=None)
    assert _api().validate_plan(plan) == plan
    plan["candidates"][0]["availability"] = "ready_to_execute"
    with pytest.raises(ValueError):
        _api().validate_plan(plan)


def test_exported_schema_roots_are_valid_and_closed():
    for name in ("SCIENTIFIC_SCHEMA", "RESOLUTION_SCHEMA", "RECOMMENDATION_SCHEMA", "PLAN_SCHEMA"):
        schema = getattr(_api(), name)
        Draft202012Validator.check_schema(schema)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
