"""Finite proposal contracts; validation does not accept or execute a draft."""

from copy import deepcopy
from importlib import import_module

from jsonschema import Draft202012Validator
import pytest


def _api():
    return import_module("devbench.analysis.method_authoring_contract")


def _trial():
    return {"id": "prespecified-qc-trial", "candidates": ["Synthetic QC"],
            "purpose": "Compare recorded QC diagnostics without changing approved analysis",
            "subset_spec": {"kind": "full_dataset"},
            "subset_validity": {"preserves_preconditions": True, "rationale": "All synthetic sample groups retained"},
            "commands": ["python3 /method/main.py"],
            "metrics": [{"name": "retained_rows", "category": "signal_preservation", "direction": "descriptive",
                         "interpretation": "Observed rows retained; not an optimization of hypothesis significance"}],
            "metric_sources": {"retained_rows": {"kind": "output_json", "output": "qc_report", "field": "/after"}},
            "resource_limits": {"memory_mb": 512, "timeout_seconds": 60, "max_runs": 1},
            "inputs": {"Synthetic QC": {"dataset": "retained-artifact-id"}},
            "parameters": {"Synthetic QC": {"threshold": 4}}}


def _implementation():
    return {"source_code": "# A draft is retained for review; this code is not executed.\nraise RuntimeError('draft only')\n",
            "algorithm_summary": "Unvalidated synthetic draft matching the provided interface",
            "introduced_choices": ["Use a fixed seed specified by the coordinator"],
            "source_references": ["Coordinator-provided synthetic specification"]}


def test_trial_design_returns_detached_experiments_without_accepting_them():
    value = {"experiments": [_trial()]}
    result = _api().validate_trial_design(value)
    assert result == value["experiments"]
    result[0]["parameters"]["Synthetic QC"]["threshold"] = 99
    assert value["experiments"][0]["parameters"]["Synthetic QC"]["threshold"] == 4
    assert "status" not in result[0] and "step_ids" not in result[0]
    assert _api().validate_trial_design({"experiments": []}) == []


def test_unestablished_subset_validity_remains_explicit_in_a_proposal():
    trial = _trial()
    trial["subset_validity"] = {"preserves_preconditions": False, "rationale": "Representative donors are missing"}
    assert _api().validate_trial_design({"experiments": [trial]})[0]["subset_validity"]["preserves_preconditions"] is False


@pytest.mark.parametrize("field", ["metric_sources", "inputs", "parameters", "subset_validity", "resource_limits"])
def test_trial_design_requires_complete_documentary_inputs(field):
    trial = _trial()
    del trial[field]
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [trial]})


@pytest.mark.parametrize("field,value", [("status", "accepted"), ("step_ids", {}), ("approval", True),
                                         ("declaration_sha256", "a" * 64)])
def test_trial_proposals_cannot_supply_coordinator_state(field, value):
    trial = _trial()
    trial[field] = value
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [trial]})


def test_trial_proposal_root_is_closed_and_bounded():
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [], "approved": True})
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [dict(_trial(), id=f"trial-{i}") for i in range(5)]})
    with pytest.raises(ValueError, match="Duplicate"):
        _api().validate_trial_design({"experiments": [_trial(), _trial()]})


@pytest.mark.parametrize("mutation", ["duplicate_candidates", "input_missing", "input_extra", "parameter_missing",
                                      "metric_missing", "metric_extra", "too_many_runs", "too_few_runs",
                                      "invalid_memory", "invalid_timeout", "nonfinite_parameter"])
def test_trial_bindings_and_resource_budgets_are_checked(mutation):
    trial = _trial()
    if mutation == "duplicate_candidates":
        trial["candidates"].append("Synthetic QC")
    elif mutation == "input_missing":
        trial["inputs"] = {}
    elif mutation == "input_extra":
        trial["inputs"]["Not a declared candidate"] = {}
    elif mutation == "parameter_missing":
        trial["parameters"] = {}
    elif mutation == "metric_missing":
        trial["metric_sources"] = {}
    elif mutation == "metric_extra":
        trial["metric_sources"]["undeclared"] = {"kind": "runtime", "field": "elapsed_seconds"}
    elif mutation == "too_many_runs":
        trial["resource_limits"]["max_runs"] = 17
    elif mutation == "too_few_runs":
        trial["candidates"].append("Second QC")
        trial["inputs"]["Second QC"] = {}
        trial["parameters"]["Second QC"] = {}
    elif mutation == "invalid_memory":
        trial["resource_limits"]["memory_mb"] = True
    elif mutation == "invalid_timeout":
        trial["resource_limits"]["timeout_seconds"] = 0
    else:
        trial["parameters"]["Synthetic QC"]["threshold"] = float("nan")
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [trial]})


@pytest.mark.parametrize("source", [
    {"kind": "shell", "field": "echo 8"},
    {"kind": "output_json", "output": "qc_report", "field": "after"},
    {"kind": "runtime", "field": "untrusted_expression"},
    {"kind": "runtime", "field": "elapsed_seconds", "code": "print(8)"},
])
def test_metric_sources_accept_selectors_only(source):
    trial = _trial()
    trial["metric_sources"]["retained_rows"] = source
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [trial]})


@pytest.mark.parametrize("metric_name,pointer", [("p_value", "/after"), ("retained_rows", "/p_value"),
                                                ("retained_rows", "/nested/significant_genes")])
def test_outcome_directed_selection_metrics_and_selectors_are_rejected(metric_name, pointer):
    trial = _trial()
    trial["metrics"][0]["name"] = metric_name
    trial["metric_sources"] = {metric_name: {"kind": "output_json", "output": "qc_report", "field": pointer}}
    with pytest.raises(ValueError):
        _api().validate_trial_design({"experiments": [trial]})


def test_calibration_selector_is_allowed_without_substring_rejection():
    trial = _trial()
    trial["metric_sources"]["retained_rows"]["field"] = "/p_value_calibration"
    assert _api().validate_trial_design({"experiments": [trial]}) == [trial]


def test_implementation_draft_is_copied_and_never_executed():
    value = _implementation()
    result = _api().validate_implementation(value)
    assert result == value
    result["introduced_choices"].append("Another reviewable choice")
    assert len(value["introduced_choices"]) == 1


def test_large_bounded_source_is_supported_without_planner_context_string_limit():
    value = _implementation()
    value["source_code"] = "#" + "x" * 99_999
    assert len(_api().validate_implementation(value)["source_code"]) == 100_000


@pytest.mark.parametrize("source", ["", "   \n", "x" * 100_001, "print('safe')\x00", None, True])
def test_invalid_implementation_source_is_rejected(source):
    value = _implementation()
    value["source_code"] = source
    with pytest.raises(ValueError):
        _api().validate_implementation(value)


@pytest.mark.parametrize("field", ["contract", "environment", "execution", "approved", "method_version_id"])
def test_implementation_cannot_rewrite_coordinator_contract_or_approval(field):
    value = _implementation()
    value[field] = {}
    with pytest.raises(ValueError):
        _api().validate_implementation(value)


@pytest.mark.parametrize("field,value", [
    ("algorithm_summary", ""), ("algorithm_summary", "x" * 4097),
    ("introduced_choices", ["choice"] * 33), ("introduced_choices", ["choice\x00"]),
    ("source_references", ("a reference",)), ("source_references", ["x" * 4097]),
])
def test_implementation_metadata_is_strict_finite_and_bounded(field, value):
    draft = _implementation()
    draft[field] = value
    with pytest.raises(ValueError):
        _api().validate_implementation(draft)


def test_authoring_schemas_are_valid_and_roots_are_closed():
    for name in ("TRIAL_DESIGN_SCHEMA", "IMPLEMENTATION_SCHEMA"):
        schema = getattr(_api(), name)
        Draft202012Validator.check_schema(schema)
        assert schema["additionalProperties"] is False


def test_schema_derivation_does_not_mutate_existing_plan_contract():
    from devbench.analysis.method_planner_contract import PLAN_SCHEMA
    before = deepcopy(PLAN_SCHEMA)
    _api()
    assert PLAN_SCHEMA == before
