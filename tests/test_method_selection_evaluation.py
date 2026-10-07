"""Offline evaluator checks; fixture choices never become live planner choices."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


def api():
    name = "devbench.analysis.method_selection_evaluation"
    assert importlib.util.find_spec(name) is not None, "The sealed pilot evaluator is not implemented"
    return __import__(name, fromlist=["prepare_pilot"])


def prepared(tmp_path):
    module = api()
    root = tmp_path / "pilot"
    root.mkdir()
    manifest = module.prepare_pilot(root, seed=726381)
    return module, root, manifest


def test_preparation_is_deterministic_and_does_not_prescribe_methods(tmp_path):
    module, root, manifest = prepared(tmp_path)
    other = tmp_path / "second"
    other.mkdir()
    again = module.prepare_pilot(other, seed=726381)
    assert manifest["content_sha256"] == again["content_sha256"]
    assert len(manifest["case_ids"]) == 4
    for identity in manifest["case_ids"]:
        public = module.public_case(root, identity)
        assert public["evaluation_purpose"] == "method_selection"
        assert public["user_parameter_requirements"] == []
        serialized = json.dumps(public).casefold()
        for excluded in ("threshold_mad", "mad filter", "expected_removed", "damage_labels", "rare_ids", "hidden_reference", "onlyoffice", "stdlib", "runtime_image"):
            assert excluded not in serialized
        assert set(public) == {"task", "dataset_summary", "evaluation_purpose", "user_parameter_requirements"}


def test_preparation_refuses_overwrite_and_relative_paths(tmp_path):
    module, root, _ = prepared(tmp_path)
    with pytest.raises(FileExistsError):
        module.prepare_pilot(root, seed=726381)
    with pytest.raises(ValueError, match="absolute"):
        module.prepare_pilot(Path("relative-pilot"), seed=12)


def test_diagnostic_cycle_freezes_additive_policy_without_scientific_parameters(tmp_path):
    module = api()
    root = tmp_path / "diagnostic-pilot"
    manifest = module.prepare_pilot(root, seed=572636, diagnostic_outputs=True)
    rubric = json.loads((root / "private/evaluator/rubric.json").read_text())
    assert rubric["format"] == "devbench.synthetic-qc-rubric/2"
    assert "flag_detection" in rubric
    for identity in manifest["case_ids"]:
        public = module.public_case(root, identity)
        assert public["user_parameter_requirements"] == []
        assert "diagnostic" in public["task"]["scientific_objective"]
        assert "damaged_ids" not in json.dumps(public)


def test_profile_cycle_freezes_separate_score_and_decision_criteria(tmp_path):
    module = api()
    root = tmp_path / "profiles"
    manifest = module.prepare_pilot(root, seed=391754, diagnostic_profiles=True)
    rubric = json.loads((root / "private/evaluator/rubric.json").read_text())
    assert rubric["format"] == "devbench.synthetic-qc-rubric/3"
    assert rubric["profile_evaluation"]["arithmetic_required"] is True
    context = module.public_case(root, manifest["case_ids"][0])
    assert context["user_parameter_requirements"] == []
    assert "continuous" in context["task"]["scientific_objective"]


def test_diagnostic_grading_requires_frozen_blinded_review_before_reference_access(tmp_path):
    module = api()
    root = tmp_path / "diagnostic-pilot"
    manifest = module.prepare_pilot(root, seed=572636, diagnostic_outputs=True)
    seals = seal_all(module, root, manifest)
    with pytest.raises(ValueError, match="blinded review"):
        module.evaluate_pilot(root, manifest["content_sha256"], seals)
    assert not (root / "grading-started.json").exists()
    review = root / "blinded-review.md"
    review.write_text("Independent synthetic review: no outputs; uncertainty remains.\n")
    module.seal_blinded_review(root, seals, review)
    with pytest.raises(ValueError, match="review|sealed"):
        module.require_live_open(root)
    review.write_text("Changed after sealing")
    with pytest.raises(ValueError, match="blinded review"):
        module.evaluate_pilot(root, manifest["content_sha256"], seals)
    assert not (root / "grading-started.json").exists()


def test_legacy_rubric_cannot_grade_diagnostic_outputs_without_new_freeze(tmp_path):
    module, root, manifest = prepared(tmp_path)
    seals = seal_all(module, root, manifest)
    identity = manifest["case_ids"][0]
    path = root / "evidence/decisions" / (identity + ".json")
    record = json.loads(path.read_text())
    output = root / "private/cases" / identity / "fixture.json"
    output.write_text("{}")
    record["outputs"] = [{"candidate": "Offline diagnostic fixture", "output_kind": "observation_diagnostic",
                          "status": "success", "path": str(output), "sha256": module.file_digest(output)}]
    path.write_text(json.dumps(record))
    seals[identity] = module.file_digest(path)
    with pytest.raises(ValueError, match="frozen v2 rubric"):
        module.evaluate_pilot(root, manifest["content_sha256"], seals)
    assert not (root / "grading-started.json").exists()


def test_source_freeze_detects_input_or_rubric_mutation(tmp_path):
    module, root, manifest = prepared(tmp_path)
    module.verify_preparation(root, manifest["content_sha256"])
    case = manifest["case_ids"][0]
    dataset = root / "private" / "cases" / case / "dataset.json"
    dataset.write_text(dataset.read_text() + " ")
    with pytest.raises(ValueError, match="changed|hash"):
        module.verify_preparation(root, manifest["content_sha256"])


def test_hidden_reference_changes_cannot_change_public_context(tmp_path):
    module, root, manifest = prepared(tmp_path)
    before = [module.public_case(root, case) for case in manifest["case_ids"]]
    hidden = root / "private" / "evaluator" / "reference.json"
    hidden.write_text('{"do_not_expose": "SECRET-CANARY"}')
    after = [module.public_case(root, case) for case in manifest["case_ids"]]
    assert after == before


def score_fixture():
    rows = [{"cell_id": str(i), "sample": "s1" if i < 3 else "s2", "group": "A" if i < 3 else "B",
             "signal_a": float(i), "signal_b": float(i * 2), "signal_c": float(5 - i)} for i in range(6)]
    reference = {"identifiability": "known_synthetic", "valid_ids": ["0", "1", "3", "4"],
                 "damaged_ids": ["2", "5"], "rare_valid_ids": ["4"],
                 "signal_fields": ["signal_a", "signal_b", "signal_c"], "group_field": "group"}
    return {"format": "devbench.qc-observations/1", "observations": rows}, reference


def test_oracle_fixture_scores_sensitivity_and_preservation_without_model_claims():
    module = api()
    data, reference = score_fixture()
    score = module.score_selection(data, reference, {"retained_ids": reference["valid_ids"]})
    assert score["damage_sensitivity"] == {"numerator": 2, "denominator": 2, "value": 1.0}
    assert score["valid_false_removal"]["value"] == 0
    assert score["rare_valid_preservation"]["value"] == 1
    assert score["global_signal_distortion"] == 0
    assert score["worst_group_signal_distortion"] == 0
    assert score["criteria_passed"] is True


def test_identity_fixture_cannot_pass_damage_detection():
    module = api()
    data, reference = score_fixture()
    score = module.score_selection(data, reference, {"retained_ids": [row["cell_id"] for row in data["observations"]]})
    assert score["damage_sensitivity"]["value"] == 0
    assert score["valid_false_removal"]["value"] == 0
    assert score["criteria_passed"] is False


def test_all_remove_is_failure_not_zero_signal_distortion():
    data, reference = score_fixture()
    score = api().score_selection(data, reference, {"retained_ids": []})
    assert score["criteria_passed"] is False
    assert score["global_signal_distortion"] is None
    assert score["worst_group_signal_distortion"] is None
    assert score["valid_false_removal"]["value"] == 1


def test_metrics_without_a_denominator_are_na():
    data, reference = score_fixture()
    reference.update(damaged_ids=[], valid_ids=[row["cell_id"] for row in data["observations"]], rare_valid_ids=[])
    score = api().score_selection(data, reference, {"retained_ids": reference["valid_ids"]})
    assert score["damage_sensitivity"] == {"numerator": 0, "denominator": 0, "value": None}
    assert score["rare_valid_preservation"] == {"numerator": 0, "denominator": 0, "value": None}
    assert score["criteria_passed"] is True


@pytest.mark.parametrize("output", [
    {"retained_ids": ["0", "0"]}, {"retained_ids": ["alien"]}, {"retained_ids": ["1", "0"]},
    {"retained_ids": [0]}, {"retained_ids": ["0"], "accuracy": 1.0}, [],
])
def test_candidate_cannot_forge_reference_scores_or_invalid_ids(output):
    data, reference = score_fixture()
    with pytest.raises(ValueError):
        api().score_selection(data, reference, output)


def test_signal_scaling_is_fixed_by_input_valid_values():
    data, reference = score_fixture()
    score = api().score_selection(data, reference, {"retained_ids": ["0", "3", "4"]})
    assert score["global_signal_distortion"] == pytest.approx(0.21081851067789192)
    assert score["worst_group_signal_distortion"] == pytest.approx(0.31622776601683794)
    assert score["criteria_passed"] is False


def test_ambiguous_truth_does_not_invent_error_scores():
    data, _ = score_fixture()
    score = api().score_selection(data, {"identifiability": "ambiguous"}, {"retained_ids": ["0"]})
    assert score["criteria_passed"] is False
    assert score["damage_sensitivity"]["value"] is None
    assert score["status"] == "scientific_selection_unidentified"


def diagnostic_fixture(tmp_path, flagged=("2", "5"), unavailable=()):
    data, reference = score_fixture()
    before, after = tmp_path / "input.json", tmp_path / "diagnostics.json"
    before.write_text(json.dumps(data))
    after.write_text(json.dumps({"format": "devbench.observation-diagnostics/1",
        "evidence_scope": "numerical_atypicality",
        "input_sha256": api().file_digest(before),
        "retained_ids": [row["cell_id"] for row in data["observations"]],
        "records": [{"observation_id": row["cell_id"],
                     "status": "unavailable" if row["cell_id"] in unavailable else "flagged" if row["cell_id"] in flagged else "not_flagged",
                     "score": None if row["cell_id"] in unavailable else float(i),
                     "reason": "Insufficient evidence" if row["cell_id"] in unavailable else
                               "Unusual under the supplied representation" if row["cell_id"] in flagged else "Within reference",
                     "evidence_refs": ["/observations/" + str(i) + "/signal_a"]}
                    for i, row in enumerate(data["observations"])]}))
    policy = {"comparator": "observation_diagnostic", "invariants": [], "counts_layer": "not_applicable",
              "qc_metrics": ["signal_a"], "diagnostic_policy": {"format": "devbench.diagnostic-policy/1",
              "interpretation": "numerical_atypicality", "measurement_semantics": {}}}
    return before, reference, after, policy


def test_actual_flags_are_scored_separately_from_exclusion(tmp_path):
    score = api().score_diagnostics(*diagnostic_fixture(tmp_path))
    assert score["flagging_detection"]["damage_sensitivity"]["value"] == 1
    assert score["flagging_detection"]["valid_false_flag"]["value"] == 0
    assert score["exclusion_recovery"]["damage_sensitivity"]["value"] == 0
    assert score["exclusion_recovery"]["valid_false_removal"]["value"] == 0
    assert score["retained_count"] == 6
    assert score["flagging_detection"]["criteria_passed"] is True


def test_all_flag_and_all_unavailable_controls_cannot_claim_detection(tmp_path):
    before, ref, after, policy = diagnostic_fixture(tmp_path, flagged=tuple(str(i) for i in range(6)))
    score = api().score_diagnostics(before, ref, after, policy)
    assert score["flagging_detection"]["criteria_passed"] is False
    assert score["flagging_detection"]["valid_false_flag"]["value"] == 1
    before, ref, after, policy = diagnostic_fixture(tmp_path, flagged=(), unavailable=tuple(str(i) for i in range(6)))
    score = api().score_diagnostics(before, ref, after, policy)
    assert score["flagging_detection"]["criteria_passed"] is False
    assert score["coverage"]["value"] == 0


def test_ambiguous_diagnostic_has_no_invented_detection_or_stability_score(tmp_path):
    before, _, after, policy = diagnostic_fixture(tmp_path)
    score = api().score_diagnostics(before, {"identifiability": "ambiguous"}, after, policy)
    assert score["flagging_detection"]["damage_sensitivity"]["value"] is None
    assert score["flagging_detection"]["criteria_passed"] is False
    assert score["stability"]["value"] is None
    assert score["stability"]["reason"]


def profile_fixture(tmp_path, *, family="absolute_mad", threshold=None, constant=False):
    # Independently hand-calculated: median=1.5, MAD=1; rank tail scores
    # for four distinct values are .75,.25,.25,.75. Constant rank is zero.
    values = [0, 1, 2, 9] if not constant else [2, 2, 2, 2]
    scores = ([1.5, .5, .5, 7.5] if family == "absolute_mad" else [.75, .25, .25, .75])
    if constant: scores = [None] * 4 if family == "absolute_mad" else [0.] * 4
    before, after = tmp_path / "profile-input.json", tmp_path / "profile-output.json"
    before.write_text(json.dumps({"format": "devbench.qc-observations/1", "observations": [
        {"cell_id": str(i), "group": "A", "sample": "S1", "quality_a": value, **{name: value for name in api().SIGNALS}}
        for i, value in enumerate(values)]}))
    records = []
    for i, score in enumerate(scores):
        flag = None if threshold is None or score is None else score >= threshold
        records.append({"observation_id": str(i), "score": score,
            "score_reason": "Profile score unavailable" if score is None else None,
            "flag": flag, "flag_reason": "Flag threshold unresolved" if threshold is None else
                "Profile score unavailable" if score is None else "Unusual under the supplied representation" if flag else "Within reference",
            "profiles": {"quality_a": {"score": score, "reason": "Zero MAD in reference" if score is None else None,
                "reference_count": 4, "reference_group": {}}}, "evidence_refs": [f"/observations/{i}/quality_a"]})
    after.write_text(json.dumps({"format": "devbench.observation-diagnostics/2", "input_sha256": api().file_digest(before),
        "evidence_scope": "numerical_atypicality", "retained_ids": [str(i) for i in range(4)], "records": records}))
    policy = {"comparator": "observation_diagnostic", "invariants": [], "counts_layer": "not_applicable", "qc_metrics": ["quality_a"],
        "diagnostic_policy": {"format": "devbench.diagnostic-policy/2", "interpretation": "numerical_atypicality", "measurement_semantics": {},
        "profile": {"family": family, "measurements": ["quality_a"], "group_by": [], "aggregate": "max", "flag_threshold": threshold}}}
    reference = {"identifiability": "identifiable", "valid_ids": ["0", "1", "2"], "damaged_ids": ["3"], "rare_valid_ids": [], "signal_fields": list(api().SIGNALS)}
    return before, reference, after, policy


@pytest.mark.parametrize("family,mean,auc", [("absolute_mad", 2.5, 1.), ("empirical_two_tail", .5, 5 / 6)])
def test_numeric_profiles_survive_deferred_flags_with_independent_arithmetic(tmp_path, family, mean, auc):
    score = api().score_diagnostics(*profile_fixture(tmp_path, family=family))
    assert score["score_coverage"]["value"] == 1
    assert score["decision_coverage"]["value"] == 0
    assert score["profile_metrics"]["aggregate_mean"] == mean
    assert score["arithmetic_fidelity"]["verified"] is True
    assert score["flagging_detection"]["damage_sensitivity"]["value"] is None
    assert score["flagging_detection"]["valid_false_flag"]["value"] is None
    assert score["ranking_selectivity"]["value"] == pytest.approx(auc)
    assert score["criteria_passed"] is False


@pytest.mark.parametrize("family,coverage", [("absolute_mad", 0), ("empirical_two_tail", 1)])
def test_constant_profiles_keep_unavailability_distinct_from_valid_zero(tmp_path, family, coverage):
    score = api().score_diagnostics(*profile_fixture(tmp_path, family=family, constant=True))
    assert score["score_coverage"]["value"] == coverage
    assert score["arithmetic_fidelity"]["verified"] is True
    assert score["ranking_selectivity"]["value"] == (.5 if coverage else None)


@pytest.mark.parametrize("false_score", [None, 0.])
def test_missing_or_false_zero_profile_scores_fail_calculation_fidelity(tmp_path, false_score):
    before, reference, after, policy = profile_fixture(tmp_path)
    output = json.loads(after.read_text())
    for row in output["records"]:
        row["score"] = row["profiles"]["quality_a"]["score"] = false_score
    after.write_text(json.dumps(output))
    with pytest.raises(ValueError, match="arithmetic"):
        api().score_diagnostics(before, reference, after, policy)


def test_thresholded_profile_classification_and_ambiguous_rank_scope(tmp_path):
    before, reference, after, policy = profile_fixture(tmp_path, threshold=4.)
    score = api().score_diagnostics(before, reference, after, policy)
    assert score["decision_coverage"]["value"] == 1
    assert score["flagging_detection"]["damage_sensitivity"]["value"] == 1
    assert score["flagging_detection"]["valid_false_flag"]["value"] == 0
    score = api().score_diagnostics(before, {"identifiability": "ambiguous"}, after, policy)
    assert score["ranking_selectivity"]["value"] is None


def seal_all(module, root, manifest, accepted=True):
    digests = {}
    for identity in manifest["case_ids"]:
        digests[identity] = module.seal_decision(root, identity, {"choice": None, "rationale": "Missing measurement provenance prevents attributing quality; obtain assay metadata."},
            accepted=accepted, terminal_status="completed" if accepted else "planning_failed", outputs=[], state={})
    return digests


def test_grading_requires_all_decisions_sealed_and_prevents_later_dispatch(tmp_path):
    module, root, manifest = prepared(tmp_path)
    with pytest.raises(ValueError, match="sealed"):
        module.evaluate_pilot(root, manifest["content_sha256"], {})
    seals = seal_all(module, root, manifest)
    module.evaluate_pilot(root, manifest["content_sha256"], seals)
    with pytest.raises(ValueError, match="[Gg]rading|sealed"):
        module.require_live_open(root)


def test_decision_tamper_is_rejected_before_truth_is_read(tmp_path):
    module, root, manifest = prepared(tmp_path)
    seals = seal_all(module, root, manifest)
    path = root / "evidence" / "decisions" / (manifest["case_ids"][0] + ".json")
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="decision|hash"):
        module.evaluate_pilot(root, manifest["content_sha256"], seals)
    assert not (root / "grading-started.json").exists()


def test_invalid_or_missing_recommendation_does_not_become_abstention(tmp_path):
    module, root, manifest = prepared(tmp_path)
    seals = seal_all(module, root, manifest, accepted=False)
    result = module.evaluate_pilot(root, manifest["content_sha256"], seals)
    assert all(case["decision_class"] == "planning_or_execution_failure" for case in result["cases"])
    assert all(case["scientific_abstention_verified"] is False for case in result["cases"])


def test_null_recommendation_requires_independent_abstention_review(tmp_path):
    module, root, manifest = prepared(tmp_path)
    seals = seal_all(module, root, manifest)
    result = module.evaluate_pilot(root, manifest["content_sha256"], seals)
    assert all(case["decision_class"] == "abstention_pending_review" for case in result["cases"])
    assert all(case["scientific_abstention_verified"] is False for case in result["cases"])


def test_preparation_files_are_isolated_from_candidate_payloads(tmp_path):
    module, root, manifest = prepared(tmp_path)
    for identity in manifest["case_ids"]:
        public = module.public_case(root, identity)
        assert not any(key in json.dumps(public) for key in ("valid_ids", "damaged_ids", "rare_valid_ids", "criteria_passed"))
        assert all(label not in identity for label in ("clean", "damage", "hetero", "insufficient"))
        assert "artifact_ids" not in public["dataset_summary"]


def test_rejected_choice_cannot_claim_selected_numerical_recovery(tmp_path, monkeypatch):
    module, root, manifest = prepared(tmp_path)
    seals = seal_all(module, root, manifest, accepted=False)
    identity = manifest["case_ids"][0]
    path = root / "evidence/decisions" / (identity + ".json")
    record = json.loads(path.read_text())
    output = root / "private/cases" / identity / "offline-test-output.json"
    output.write_text('{"retained_ids": []}')
    record["recommendation"]["choice"] = "Offline fixture"
    record["outputs"] = [{"candidate": "Offline fixture", "status": "success", "path": str(output), "sha256": module.file_digest(output)}]
    path.write_text(json.dumps(record))
    seals[identity] = module.file_digest(path)
    monkeypatch.setattr(module, "score_selection", lambda *args: {"status": "offline_fixture", "criteria_passed": True})
    result = module.evaluate_pilot(root, manifest["content_sha256"], seals)
    case = next(row for row in result["cases"] if row["case_id"] == identity)
    assert case["candidate_scores"]["Offline fixture"]["criteria_passed"] is True
    assert case["selected_score"] is None
    assert case["numerical_recovery_demonstrated"] is False
    module.plot_pilot(root, result)
    plotted = json.loads((root / "plot-data.json").read_text())
    assert next(row for row in plotted if row["case_id"] == identity)["selected_output_sha256"] is None
