"""Offline diagnostic admission through retained inspection and normal planner paths."""
from copy import deepcopy
import json

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.method_planner_contract import validate_scientific_candidates
from devbench.analysis.method_validation import validate_outputs
from devharness_core.methods import MethodContract, diagnostic_profile_schema
from tests.test_extensible_methods import custom, service
from tests.test_method_action_scope import item
from tests.test_method_diagnostic_profiles import fixture, profile_contract
from tests.test_method_planner import Selector


UNIVERSAL = {"input_integrity_valid", "observation_ids_unique", "metrics_finite"}
POLICY = {"sample_key": "sample", "qc_metrics": ["measurement"]}


def plan(tmp_path, *, mutate=None, policy=POLICY, artifact_type="json", observed=None):
    before, after, output, interface = fixture(tmp_path)
    if mutate:
        data = json.loads(before.read_bytes())
        mutate(data)
        before.write_text(json.dumps(data))
    api = service(tmp_path)
    artifact = api.import_artifact(before, artifact_type=artifact_type, actor="fixture")
    summary = {"observed": deepcopy(observed or {}), "inferred": {}, "unknowns": [],
               "artifact_ids": [artifact["artifact_id"]]}
    if policy is not None:
        summary["observed"]["observation_input_policy"] = deepcopy(policy)
    planner = ScientificMethodPlanner(api)
    state = planner.create({"scientific_objective": "Assess numerical profiles without a chosen method or cutoff",
                            "computational_task": "quality_control"}, summary, actor="scientist",
                           data_classification="synthetic")
    return planner, state, summary, artifact, before, after, output, interface


def candidate(family="absolute_mad", *, hard=UNIVERSAL):
    row = item()
    row["diagnostic_profile"] = profile_contract(family)["validation"]["diagnostic_policy"]["profile"]
    row["parameter_candidates"] = []
    row["assumptions"] = [{"statement": field, "status": "unknown", "evidence": None,
        "hard_precondition": True, "field": field, "operator": "equals", "expected": True} for field in sorted(hard)]
    return row


def generate(planner, state, row=None):
    selector = Selector({"candidates": [] if row is None else [row]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    return selector.contexts[0]


def test_science_gets_retained_numeric_facts_before_selection_without_history_backfill(tmp_path):
    planner, state, summary, artifact, *_ = plan(tmp_path)
    original = deepcopy(summary)
    context = generate(planner, state)
    assert {k: context["dataset_summary"]["observed"][k] for k in UNIVERSAL} == dict.fromkeys(UNIVERSAL, True)
    receipt = context["prerequisite_inspection"]
    assert receipt["inspection_id"]
    assert receipt["input_sha256"] == artifact["sha256"]
    assert receipt["origin"] == "devharness.coordinator-prerequisites/1"
    assert set(receipt["requested_fields"]) == UNIVERSAL
    assert planner.methods.get("method_prerequisite_inspection", receipt["inspection_id"])["observed"] == dict.fromkeys(UNIVERSAL, True)
    assert "numeric" in context["observation_fact_catalog"]["metrics_finite"]
    assert summary == original == planner.get(state["session_id"])["context"]["dataset_summary"]
    assert context["user_parameter_requirements"] == []
    assert "registry" not in context and "method_version_id" not in str(context)
    later = planner._inspect_applicability(candidate(), summary, [artifact["artifact_id"]], actor="scientist")
    assert later["prerequisite_inspections"] == [receipt["inspection_id"]]


@pytest.mark.parametrize("family,scores", [("absolute_mad", (1.5,.5,.5,7.5)),
                                           ("empirical_two_tail", (.75,.25,.25,.75))])
def test_exact_frozen_profiles_bind_and_validate_through_normal_coordinator(tmp_path, family, scores):
    planner, state, _, _, before, after, _, _ = plan(tmp_path)
    # Retained selector proposes scientific serialization before any registry read.
    row = candidate(family)
    class ObservedFactSelector(Selector):
        def select(self, context, output, **kwargs):
            # Exercise a selector consuming the actual coordinator evidence and
            # machine paths, rather than merely checking prompt wording.
            assert set(context["observation_fact_catalog"]) == UNIVERSAL
            for assumption in self.answers[0]["candidates"][0]["assumptions"]:
                assert context["dataset_summary"]["observed"][assumption["field"]] is True
            return super().select(context, output, **kwargs)
    selector = ObservedFactSelector({"candidates": [row]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    context = selector.contexts[0]
    assert context["dataset_summary"]["observed"]["metrics_finite"] is True
    value, source, lock = custom(tmp_path)
    profile = profile_contract(family)
    value.update(action_scope=profile["action_scope"], validation=profile["validation"])
    value["parameters"] = {"type": "object", "additionalProperties": False, "properties": {}}
    method = planner.methods.register_method(value, source=source, environment_lock=lock, actor="fixture")
    selector = Selector({"bindings": [{"candidate": row["name"], "method_version_id": method["version_id"], "discovery": None}]})
    resolved = planner.resolve_capabilities(state["session_id"], selector, [method["version_id"]], actor="scientist")
    assert resolved[0]["applicability"] == "eligible"
    assert resolved[0]["diagnostic_profile"] == row["diagnostic_profile"]
    before, after, _, _ = fixture(tmp_path, family=family, scores=scores)
    assert validate_outputs(value, {"dataset": before}, {"dataset": after})["arithmetic_fidelity"]["verified"]


@pytest.mark.parametrize("family", ["robust_covariance", "absolute deviation divided by median absolute deviation"])
def test_open_scientific_family_survives_but_is_never_substituted(tmp_path, family):
    planner, state, *_ = plan(tmp_path)
    row = candidate(family)
    generate(planner, state, row)
    selector = Selector({"bindings": [{"candidate": row["name"], "method_version_id": None, "discovery": None}]})
    result = planner.resolve_capabilities(state["session_id"], selector, [], actor="scientist")[0]
    assert result["diagnostic_profile"] == row["diagnostic_profile"]
    assert result["method_version_id"] is None and result["availability"] == "unknown"
    with pytest.raises(ValueError):
        MethodContract.from_dict(profile_contract(family))


@pytest.mark.parametrize("field", ["measurements_numeric_typed", "covariance_identifiable", "assay_semantics_known",
                                  "biological_units_independent", "design_identifiable", "zero_scale_absent"])
def test_unknown_hard_fields_still_block_without_aliases(tmp_path, field):
    planner, state, _, artifact, *_ = plan(tmp_path)
    row = candidate(hard=UNIVERSAL | {field})
    context = generate(planner, state, row)
    assert field not in context["dataset_summary"]["observed"]
    result = planner._inspect_applicability(row, state["context"]["dataset_summary"], [artifact["artifact_id"]], actor="scientist")
    assert result["applicability"] == "blocked_unknown"
    assert any(a["field"] == field and a["status"] == "unknown" for a in result["assumptions"])


@pytest.mark.parametrize("mutate", [
    lambda d: d["observations"][0].update(measurement="1"),
    lambda d: d["observations"][0].update(measurement=float("inf")),
    lambda d: d["observations"][0].update(measurement=True),
    lambda d: d["observations"][1].update(cell_id=d["observations"][0]["cell_id"]),
    lambda d: d["observations"][0].update(sample=7),
])
def test_failed_science_inspection_withholds_spoofed_universal_success(tmp_path, mutate):
    planner, state, summary, *_ = plan(tmp_path, mutate=mutate, observed=dict.fromkeys(UNIVERSAL, True))
    context = generate(planner, state)
    assert context["prerequisite_inspection"]["failures"]
    assert not UNIVERSAL & context["dataset_summary"]["observed"].keys()
    assert UNIVERSAL <= summary["observed"].keys()
    assert any(u["field"] == "coordinator_numeric_inspection" for u in context["dataset_summary"]["unknowns"])


@pytest.mark.parametrize("policy,artifact_type", [(None,"json"), ({},"json"), (POLICY,"file"),
                                                 ({"sample_key":7,"qc_metrics":["measurement"]},"json")])
def test_missing_roles_or_unsupported_artifact_is_explicitly_unavailable(tmp_path, policy, artifact_type, monkeypatch):
    planner, state, *_ = plan(tmp_path, policy=policy, artifact_type=artifact_type)
    if policy is None or policy == {} or artifact_type == "file":
        def forbidden(*args, **kwargs):
            pytest.fail("Unsupported input must not be read for observation inspection")
        monkeypatch.setattr("devbench.analysis.method_planner.observation_prerequisite_inspection", forbidden)
    context = generate(planner, state)
    assert context["prerequisite_inspection"]["failures"]
    assert not UNIVERSAL & context["dataset_summary"]["observed"].keys()


def test_schema_describes_mathematical_identifiers_without_closing_scientific_space():
    schema = diagnostic_profile_schema()
    assert "enum" not in schema["properties"]["family"]
    description = schema["properties"]["family"]["description"]
    assert "absolute_mad" in description and "empirical_two_tail" in description
    assert "midrank" in description and "unscaled" in description
    assert "null" in schema["properties"]["aggregate"]["description"]
    assert validate_scientific_candidates({"candidates":[candidate("unimplemented_scientific_family")]})


def test_version_one_diagnostic_interface_and_output_replay_stay_unchanged(tmp_path):
    from tests.test_method_diagnostic_outputs import artifacts, contract
    before, after, output = artifacts(tmp_path)
    original = deepcopy(contract())
    assert MethodContract.from_dict(original).to_dict() == original
    result = validate_outputs(original, {"dataset":before}, {"dataset":after})
    assert result["passed"] and result["flagged_ids"] == ["opaque-b"]
    assert json.loads(after.read_bytes()) == output


def test_scientific_selection_does_not_query_the_execution_registry(tmp_path, monkeypatch):
    planner, state, *_ = plan(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Scientific selection queried the execution registry")
    monkeypatch.setattr(planner.methods, "method", forbidden)
    monkeypatch.setattr(planner.methods, "list_registry", forbidden, raising=False)
    context = generate(planner, state, candidate())
    assert "registry" not in context


def test_prose_aggregation_remains_unavailable_without_normalization(tmp_path):
    planner, state, *_ = plan(tmp_path)
    row = candidate()
    row["diagnostic_profile"]["aggregate"] = "maximum across measurement scores"
    generate(planner, state, row)
    selector = Selector({"bindings": [{"candidate": row["name"], "method_version_id": None, "discovery": None}]})
    resolved = planner.resolve_capabilities(state["session_id"], selector, [], actor="scientist")[0]
    assert resolved["diagnostic_profile"]["aggregate"] == row["diagnostic_profile"]["aggregate"]
    value = profile_contract()
    value["validation"]["diagnostic_policy"]["profile"]["aggregate"] = row["diagnostic_profile"]["aggregate"]
    with pytest.raises(ValueError):
        MethodContract.from_dict(value)


def test_true_zero_scale_stays_distinct_from_finite_numeric_input(tmp_path):
    planner, state, *_ = plan(tmp_path, mutate=lambda d: [r.update(measurement=2.) for r in d["observations"]])
    context = generate(planner, state, candidate())
    assert context["dataset_summary"]["observed"]["metrics_finite"] is True
    assert "zero_scale_absent" not in context["dataset_summary"]["observed"]
    assert "measurement_variance_positive" not in context["dataset_summary"]["observed"]


@pytest.mark.parametrize("policy,artifact_type", [(None,"json"), ({},"json"), (POLICY,"file")])
def test_unrelated_general_plans_keep_summary_and_receive_only_unavailable_receipt(tmp_path, policy, artifact_type):
    planner, state, summary, *_ = plan(tmp_path, policy=policy, artifact_type=artifact_type,
                                     observed={"independent_samples": True})
    context = generate(planner, state)
    assert context["dataset_summary"] == summary
    assert context["prerequisite_inspection"]["failures"]
    assert planner.get(state["session_id"])["context"]["dataset_summary"] == summary


def test_bounded_nested_json_parse_failure_is_retained_before_scientific_selection(tmp_path):
    planner, _, *_ = plan(tmp_path)
    before = tmp_path / "nested-input.json"
    # Keep bytes small while exceeding the decoder's nesting limit. Construct
    # serialized input directly so fixture serialization is not the failure.
    before.write_text('{"format":"devbench.qc-observations/1","observations":['
                      '{"cell_id":"one","sample":"a","measurement":' +
                      '[' * 2000 + '1' + ']' * 2000 + '}]}')
    artifact = planner.methods.import_artifact(before, artifact_type="json", actor="fixture")
    summary = {"observed": {**dict.fromkeys(UNIVERSAL, True), "observation_input_policy": deepcopy(POLICY)},
               "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]}
    state = planner.create({"scientific_objective": "Inspect numerical observations without a chosen method or cutoff",
                            "computational_task": "quality_control"}, summary,
                           actor="scientist", data_classification="synthetic")
    context = generate(planner, state)
    receipt = context["prerequisite_inspection"]
    assert any("RecursionError" in failure for failure in receipt["failures"])
    assert receipt["observed"] == {}
    assert not UNIVERSAL & context["dataset_summary"]["observed"].keys()
    assert any(u["field"] == "coordinator_numeric_inspection" and u["blocking"]
               for u in context["dataset_summary"]["unknowns"])
    assert planner.methods.get("method_prerequisite_inspection", receipt["inspection_id"])["failures"] == receipt["failures"]
    assert planner.get(state["session_id"])["context"]["dataset_summary"] == summary
