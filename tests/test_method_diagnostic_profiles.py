"""Independent arithmetic and score/decision separation for versioned profiles."""
from copy import deepcopy
import hashlib
import json
import pytest
from devharness_core.methods import MethodContract
from devharness_core.provenance import canonical_json
from tests.test_method_diagnostic_outputs import contract


def profile_contract(family="absolute_mad", *, group_by=None, threshold=None):
    value = contract()
    value["validation"]["diagnostic_policy"] = {
        "format": "devbench.diagnostic-policy/2", "interpretation": "numerical_atypicality",
        "measurement_semantics": {}, "profile": {"family": family, "measurements": ["measurement"],
        "group_by": group_by or [], "aggregate": "max", "flag_threshold": threshold}}
    return value


def fixture(tmp_path, values=(1., 2., 3., 10.), scores=(1.5, .5, .5, 7.5), *, family="absolute_mad", threshold=None):
    before, after = tmp_path / "input.json", tmp_path / "output.json"
    rows = [{"cell_id": f"id-{i}", "measurement": x, "sample": "a"} for i, x in enumerate(values)]
    before.write_bytes(canonical_json({"format": "devbench.qc-observations/1", "observations": rows}))
    output = {"format": "devbench.observation-diagnostics/2", "input_sha256": hashlib.sha256(before.read_bytes()).hexdigest(),
        "evidence_scope": "numerical_atypicality", "retained_ids": [r["cell_id"] for r in rows], "records": []}
    for i, score in enumerate(scores):
        flag = None if threshold is None or score is None else score >= threshold
        reason = None if score is not None else "Zero MAD in reference"
        output["records"].append({"observation_id": rows[i]["cell_id"], "score": score,
            "score_reason": None if score is not None else "Profile score unavailable", "flag": flag,
            "flag_reason": "Flag threshold unresolved" if threshold is None else (
                "Profile score unavailable" if score is None else (
                "Unusual under the supplied representation" if flag else "Within reference")),
            "profiles": {"measurement": {"score": score, "reason": reason, "reference_count": len(rows), "reference_group": {}}},
            "evidence_refs": [f"/observations/{i}/measurement"]})
    after.write_bytes(canonical_json(output))
    return before, after, output, profile_contract(family, threshold=threshold)


def validated(tmp_path, **kwargs):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path, **kwargs)
    return validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_frozen_profile_contract_is_additive_and_closed():
    value = profile_contract()
    assert MethodContract.from_dict(value).to_dict() == value
    for mutation in [lambda p: p.update(family="unverified_magic"), lambda p: p.update(measurements=["foreign"]),
                     lambda p: p.update(group_by=["measurement"]), lambda p: p.update(flag_threshold=True)]:
        bad = deepcopy(value)
        mutation(bad["validation"]["diagnostic_policy"]["profile"])
        with pytest.raises(ValueError):
            MethodContract.from_dict(bad)


def test_mad_scores_survive_unresolved_flag_threshold(tmp_path):
    result = validated(tmp_path)
    assert result["score_available_ids"] == [f"id-{i}" for i in range(4)]
    assert result["deferred_flag_ids"] == result["retained_ids"]
    assert result["flagged_ids"] == [] and result["unavailable_ids"] == []
    assert result["diagnostic_measurements"]["available_fraction"] == 1.
    assert result["diagnostic_measurements"]["decision_fraction"] == 0.
    assert result["diagnostic_measurements"]["flag_rate"] is None
    assert result["arithmetic_fidelity"]["verified"] is True
    assert [r["score"] for r in result["diagnostic_profiles"]] == pytest.approx([1.5, .5, .5, 7.5])
    assert result["group_flag_rates"] == {"sample": {"a": None}}


def test_empirical_ties_have_hand_calculated_scores(tmp_path):
    result = validated(tmp_path, values=(1., 1., 3., 9.), scores=(.5, .5, .25, .75), family="empirical_two_tail")
    assert [r["score"] for r in result["diagnostic_profiles"]] == [.5, .5, .25, .75]


def test_zero_mad_is_actual_arithmetic_absence_not_missing_threshold(tmp_path):
    result = validated(tmp_path, values=(2., 2., 2., 2.), scores=(None,) * 4)
    assert result["unavailable_ids"] == result["retained_ids"]
    assert result["score_available_ids"] == []


def test_constant_rank_profile_is_computable(tmp_path):
    result = validated(tmp_path, values=(2., 2., 2., 2.), scores=(0.,) * 4, family="empirical_two_tail")
    assert len(result["score_available_ids"]) == 4


def test_frozen_threshold_controls_flag_decision_separately(tmp_path):
    result = validated(tmp_path, threshold=2.)
    assert result["flagged_ids"] == ["id-3"] and not result["deferred_flag_ids"]
    assert result["diagnostic_measurements"]["flag_rate"] == .25


@pytest.mark.parametrize("mutation", [
    lambda o: o["records"][0].update(score=88.),
    lambda o: o["records"][0]["profiles"]["measurement"].update(score=88.),
    lambda o: o["records"][0].update(score=None, score_reason="Profile score unavailable"),
    lambda o: o["records"][0]["profiles"].clear(),
    lambda o: o["records"][0]["profiles"]["measurement"].update(reference_count=1),
    lambda o: o["records"][0]["profiles"]["measurement"].update(reference_group={"sample": "a"}),
    lambda o: o["records"][0].update(flag=False, flag_reason="Within reference"),
    lambda o: o["records"][0].update(flag_reason="Confirmed technical damage"),
    lambda o: o["records"][0].update(evidence_refs=[]),
    lambda o: o["records"][0].update(evidence_refs=["/observations/1/measurement"]),
    lambda o: o["records"].reverse(),
    lambda o: o.update(format="devbench.observation-diagnostics/1"),
    lambda o: o["retained_ids"].pop(),
    lambda o: o["records"][0].update(score=True),
])
def test_candidate_arithmetic_omission_or_bypass_fails_closed(tmp_path, mutation):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path)
    mutation(output)
    after.write_bytes(canonical_json(output))
    with pytest.raises(ValueError):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_profile_reference_follows_frozen_group_not_pooled_values(tmp_path):
    from devbench.analysis.method_validation import compute_diagnostic_profiles
    before, _, _, value = fixture(tmp_path, values=(1., 3., 100., 106.))
    data = json.loads(before.read_bytes())
    for i, row in enumerate(data["observations"]):
        row["sample"] = "a" if i < 2 else "b"
    before.write_bytes(canonical_json(data))
    value["validation"]["diagnostic_policy"]["profile"]["group_by"] = ["sample"]
    profiles = compute_diagnostic_profiles(before, value["validation"])
    assert [r["score"] for r in profiles] == pytest.approx([1., 1., 1., 1.])
    assert profiles[2]["profiles"]["measurement"]["reference_group"] == {"sample": "b"}
    assert profiles[2]["profiles"]["measurement"]["reference_count"] == 2


def test_scientific_candidates_can_freeze_unimplemented_family_before_registry():
    from devbench.analysis.method_planner_contract import validate_scientific_candidates
    from tests.test_method_action_scope import item
    row = item()
    row["diagnostic_profile"] = profile_contract("future_scientific_family")["validation"]["diagnostic_policy"]["profile"]
    assert validate_scientific_candidates({"candidates": [row]})[0]["diagnostic_profile"] == row["diagnostic_profile"]


def test_extreme_dynamic_range_does_not_invent_zero_mad(tmp_path):
    from devbench.analysis.method_validation import compute_diagnostic_profiles
    before, _, _, value = fixture(tmp_path, values=(0., 5e-324, 1e308), scores=(1., 0., None))
    profiles = compute_diagnostic_profiles(before, value["validation"])
    assert profiles[0]["score"] == 1.
    assert profiles[1]["score"] == 0.
    assert profiles[2]["score"] is None
    assert profiles[2]["profiles"]["measurement"]["reason"] == "Nonfinite score calculation"


def test_partial_measurement_profiles_keep_available_evidence(tmp_path):
    from devbench.analysis.method_validation import compute_diagnostic_profiles
    before, _, _, value = fixture(tmp_path)
    data = json.loads(before.read_bytes())
    for row in data["observations"]:
        row["constant"] = 7.
    before.write_bytes(canonical_json(data))
    value["validation"]["qc_metrics"].append("constant")
    value["validation"]["diagnostic_policy"]["profile"]["measurements"].append("constant")
    profiles = compute_diagnostic_profiles(before, value["validation"])
    assert profiles[0]["score"] is None
    assert profiles[0]["profiles"]["measurement"]["score"] == pytest.approx(1.5)
    assert profiles[0]["profiles"]["constant"]["reason"] == "Zero MAD in reference"


def test_malformed_or_missing_measurements_never_acquire_scores(tmp_path):
    from devbench.analysis.method_validation import compute_diagnostic_profiles
    before, _, _, value = fixture(tmp_path)
    data = json.loads(before.read_bytes())
    data["observations"][0].pop("measurement")
    before.write_bytes(canonical_json(data))
    with pytest.raises(ValueError):
        compute_diagnostic_profiles(before, value["validation"])


def test_extractor_exposes_profiles_and_conditional_flag_availability(tmp_path):
    from devbench.analysis.method_validation import extract_observation_diagnostics
    before, after, _, value = fixture(tmp_path)
    result = extract_observation_diagnostics(before, after, value["validation"])
    assert result["deferred_flag_ids"] == result["retained_ids"]
    assert result["arithmetic_fidelity"]["verified"] is True
    assert result["profile_metrics"]["aggregate_mean"] == pytest.approx(2.5)
    assert result["profile_metrics"]["by_measurement"]["measurement"]["available_fraction"] == 1.


@pytest.mark.parametrize("change", [lambda p: p.update(family="empirical_two_tail"),
                                    lambda p: p.update(group_by=["sample"]),
                                    lambda p: p.update(flag_threshold=4.)])
def test_capability_binding_cannot_change_frozen_profile(change):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_method_action_scope import item
    candidate, value = item(), profile_contract()
    candidate["diagnostic_profile"] = deepcopy(value["validation"]["diagnostic_policy"]["profile"])
    ScientificMethodPlanner._check_action_binding(candidate, value)
    change(value["validation"]["diagnostic_policy"]["profile"])
    with pytest.raises(ValueError, match="profile"):
        ScientificMethodPlanner._check_action_binding(candidate, value)


def test_undeclared_profile_cannot_be_added_during_resolution():
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_method_action_scope import item
    with pytest.raises(ValueError, match="profile"):
        ScientificMethodPlanner._check_action_binding(item(), profile_contract())


def test_verified_profile_receipt_has_next_step_without_comparative_winner(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_extensible_methods import custom, service
    from tests.test_method_action_scope import item
    from tests.test_method_planner import Selector, recommendation, trial_spec
    api = service(tmp_path)
    value, source, lock = custom(tmp_path)
    declared = profile_contract()
    for key in ("action_scope", "inputs", "outputs", "validation"):
        value[key] = declared[key]
    method = api.register_method(value, source=source, environment_lock=lock, actor="scientist")
    before, _, output, _ = fixture(tmp_path)
    artifact = api.import_artifact(before, artifact_type="json", actor="scientist")
    analysis = api.create_analysis("Profile arithmetic fixture", actor="scientist")
    branch = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="scientist")
    step = api.add_step(branch["branch_id"], method["version_id"], {"dataset": artifact["artifact_id"]},
                        {"threshold": 4}, actor="scientist")
    planner = ScientificMethodPlanner(api)
    state = planner.create({"scientific_objective": "Inspect descriptive profiles", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor="scientist", data_classification="synthetic")
    row = item()
    row["diagnostic_profile"] = declared["validation"]["diagnostic_policy"]["profile"]
    selector = Selector({"candidates": [row]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": method["version_id"], "discovery": None}]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    planner.resolve_capabilities(state["session_id"], selector, [method["version_id"]], actor="scientist")
    spec = planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    class Executor:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            (output_dir / "diagnostics.json").write_bytes(canonical_json(output))
            return {"exit_code": 0, "runtime": "synthetic_fixture"}
    planner.run_trial(state["session_id"], spec["id"], actor="scientist", allow_data_execution=True, executor=Executor())
    assessment = planner.evidence_assessment(state["session_id"])
    next_step = assessment["diagnostic_next_steps"][0]
    assert next_step["score_available_count"] == 4 and next_step["deferred_flag_count"] == 4
    assert next_step["arithmetic_fidelity"]["verified"] is True
    assert next_step["exclusions_authorized"] is False
    assert not assessment["candidates"][0]["meets_evidence_floor"]
    assert planner.recommend(state["session_id"], Selector(recommendation(None)), actor="scientist")["choice"] is None


def test_tiny_nonzero_score_cannot_be_replaced_by_zero(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path, values=(-1., 0., 1e-14, 1., 2.), scores=(1., 1e-14, 0., 1., 2.))
    output["records"][1]["profiles"]["measurement"]["score"] = 0.
    output["records"][1]["score"] = 0.
    after.write_bytes(canonical_json(output))
    with pytest.raises(ValueError):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


@pytest.mark.parametrize("threshold, submitted", [(1.5, 1.4999999999), (1.5000000001, 1.5000000002)])
def test_reported_score_and_independent_score_agree_on_threshold_side(tmp_path, threshold, submitted):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path, threshold=threshold)
    output["records"][0]["score"] = submitted
    after.write_bytes(canonical_json(output))
    with pytest.raises(ValueError, match="flag|decision"):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_unresolved_threshold_keeps_tolerated_score_and_deferred_decision(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path)
    output["records"][0]["score"] = 1.4999999999
    after.write_bytes(canonical_json(output))
    result = validate_outputs(value, {"dataset": before}, {"dataset": after})
    assert result["deferred_flag_ids"] == result["retained_ids"]
    assert not result["flagged_ids"]


def test_positive_mad_ratio_underflow_is_absent_but_true_median_zero_available(tmp_path):
    from devbench.analysis.method_validation import compute_diagnostic_profiles
    before, _, _, value = fixture(tmp_path, values=(-1e308, 0., 5e-324, 1e308, 1e308), scores=(1., 0., 0., 1., 1.))
    profiles = compute_diagnostic_profiles(before, value["validation"])
    assert profiles[1]["score"] is None
    assert profiles[1]["profiles"]["measurement"]["reason"] == "Score below floating representation"
    assert profiles[2]["score"] == 0.
    assert profiles[2]["score_reason"] is None
    assert profiles[2]["profiles"]["measurement"]["reason"] is None


@pytest.mark.parametrize("forged_zero", [0., -0.])
def test_positive_underflow_cannot_acquire_verified_zero_score(tmp_path, forged_zero):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path, values=(-1e308, 0., 5e-324, 1e308, 1e308),
                                            scores=(1., forged_zero, 0., 1., 1.))
    with pytest.raises(ValueError):
        validate_outputs(value, {"dataset": before}, {"dataset": after})


def test_explicit_underflow_absence_is_valid_with_true_zero_retained(tmp_path):
    from devbench.analysis.method_validation import validate_outputs
    before, after, output, value = fixture(tmp_path, values=(-1e308, 0., 5e-324, 1e308, 1e308),
                                            scores=(1., None, 0., 1., 1.))
    output["records"][1]["profiles"]["measurement"]["reason"] = "Score below floating representation"
    after.write_bytes(canonical_json(output))
    result = validate_outputs(value, {"dataset": before}, {"dataset": after})
    assert result["unavailable_ids"] == ["id-1"]
    assert "id-2" in result["score_available_ids"]
    assert result["diagnostic_profiles"][2]["score"] == 0.
    assert result["arithmetic_fidelity"]["verified"] is True
