"""Offline campaign controls; fixture answers are not scientific evaluation."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


def api():
    assert importlib.util.find_spec("devbench.analysis.rna_investigation_evaluation") is not None, "RNA campaign evaluator is missing"
    from devbench.analysis import rna_investigation_evaluation
    return rna_investigation_evaluation


def protocol():
    return {
        "format": "devbench.rna-investigation-protocol/1", "scope": "software_fixture",
        "splits": ["development", "calibration", "locked_test"],
        "arms": ["strong_single", "conditional_0", "conditional_1", "conditional_2", "sequential_second", "deterministic"],
        "stages": ["pre_fit_methods", "post_fit_explanation"], "replicates": 1, "ordering_seed": 71,
        "model": {"backbone": "offline-fixture", "version": "1", "seed_supported": False},
        "runtime": {"identity": "offline-fixture-runtime/1"},
        "storage_limits": {"record_json_bytes": 1000000, "aggregate_json_bytes": 16000000},
        "allocation_policy": {"version": "public-prerequisites-and-ambiguity/1", "maximum_children": 2},
        "budgets": [{"level": "small", "kind": "bounded_observed_tokens", "max_calls": 4,
                     "max_total_seconds": 20, "max_context_bytes": 16000, "soft_token_target": 8000,
                     "max_total_tokens": None, "memory_mb": 512, "max_children": 2,
                     "reconciliation": {"tokens": 1000, "calls": 1, "seconds": 5, "context_bytes": 16000}}],
        "prompts": {"parent": "Investigate data, objective, assumptions and uncertainty.",
                    "reconsider": "Reassess alternatives and missing assumptions before deciding.",
                    "investigator": "Independently investigate the neutral evidence.",
                    "reconcile": "Reconcile evidence and preserve disagreement and limitations."},
        "opportunities": {"tools": ["validate_design_matrix"], "max_tool_calls": 1, "max_fits": 0,
                          "max_read_bytes": 64000, "same_evidence": True, "same_tools": True},
        "error_taxonomy": ["invalid_answer", "timeout", "tool_error", "budget_exhausted", "unknown_usage", "blocked", "capture_error"],
        "abstention_policy": "Credit only a reference-supported scoped refusal; retain coverage and failure denominators.",
        "scorer": {"version": "deterministic-invariants/1", "expert_interpretation": "pending"},
        "analysis": {"unit": "family", "grouping": "connected_family_cohort_donor_provenance_mechanism/1",
                     "bootstrap_seed": 79, "bootstrap_resamples": 100, "confidence": .95},
        "decision_rule": {"authorship": "test_author", "primary_endpoint": "action_validity",
                          "candidate_arm": "conditional_1", "minimum_improvement": .05,
                          "risk_margin": 0, "minimum_families": 5, "maximum_interval_width": .2,
                          "max_token_ratio": 1.1, "subgroup_vetoes": ["unit_validity", "unsupported_biology"],
                          "precision_justification": "Small controls are intentionally underpowered; retain the baseline."},
    }


@pytest.fixture
def storage():
    base = Path(__file__).resolve().parents[1] / "reports/rna-investigation-evaluation/workspaces"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="fixture-", dir=base))


def cases(storage):
    items = []
    for index, split in enumerate(("development", "calibration", "locked_test")):
        ref = storage / ("reference-" + str(index) + ".json")
        ref.write_text(json.dumps({"format": "devbench.rna-synthetic-invariants/1",
            "field_equals": {"/action": "investigate", "/biological_unit": "donor", "/estimand": "B-minus-A"},
            "expected_refusal": False, "required_assumptions": ["independent_donors"],
            "required_evidence": ["design-audit"], "hypothesis_family": ["g0", "g1"],
            "forbidden_claim_scopes": ["direct_causality", "promoter_binding"],
            "interpretation_scope": "not_assessed", "private_canary": "fixture-reference-canary"}))
        items.append({"case_id": "case" + str(index), "split": split, "family_id": "family" + str(index),
            "cohort_ids": ["cohort" + str(index)], "donor_ids": ["donor" + str(index)],
            "provenance_ids": ["provenance" + str(index)], "stage": "pre_fit_methods", "stratum": "balanced",
            "generator_manifest": {"generator": "test-authored-software-control/1", "seed": index,
                                   "mechanism": "control-mechanism-" + str(index), "family_id": "family" + str(index)},
            "public_data": {"observed": {"independent_units": True, "representation": "raw_counts", "valid_design": True},
                            "declarations": {"biological_unit": "donor", "estimand": "B-minus-A"},
                            "unknowns": [], "evidence_ids": ["design-audit"], "hypothesis_family": ["g0", "g1"],
                            "ambiguity_eligible": True}, "reference_path": str(ref)})
    return items


def episode_factory(root, case):
    from tests.test_analysis_episodes import create
    return create(root)[0]


def prepare(storage):
    return api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=cases(storage),
        episode_factory=episode_factory, allow_reference_access=True)


def test_protocol_rejects_family_leakage_before_creating_a_campaign(storage):
    data = cases(storage)
    data[2]["family_id"] = data[0]["family_id"]
    data[2]["generator_manifest"]["family_id"] = data[2]["family_id"]
    with pytest.raises(ValueError, match="family.*split|split.*family"):
        api().validate_protocol(protocol(), data)
    assert not (storage / "campaign").exists()


@pytest.mark.parametrize("relation", ["cohort_ids", "donor_ids", "provenance_ids"])
def test_related_views_cannot_cross_tuning_and_test_splits(storage, relation):
    data = cases(storage)
    data[2][relation] = data[0][relation]
    with pytest.raises(ValueError, match="split|leak"):
        api().validate_protocol(protocol(), data)


def test_counterbalanced_order_preserves_identical_case_replicates_and_budget_levels(storage):
    data, spec = cases(storage), protocol()
    spec["replicates"] = 2
    spec["storage_limits"]["aggregate_json_bytes"] = 64000000
    second = copy.deepcopy(spec["budgets"][0]); second["level"] = "large"; second["soft_token_target"] *= 2
    spec["budgets"].append(second)
    first = api().counterbalanced_schedule(spec, data)
    assert first == api().counterbalanced_schedule(spec, list(reversed(data)))
    assert len(first) == len(data) * 2 * 2 * len(spec["arms"])
    groups = {}
    for item in first:
        groups.setdefault((item["case_id"], item["replicate"], item["budget_level"]), []).append(item["arm"])
    assert all(set(arms) == set(spec["arms"]) for arms in groups.values())
    assert len({tuple(arms) for arms in groups.values()}) > 1
    assert all(item["model_seed"] is None for item in first)


def test_prospective_scoring_and_failure_denominator_are_sealed(storage):
    prepared = prepare(storage)
    fixture = api().OfflineParticipant({"case2:conditional_1:parent": {"status": "timeout", "usage": None}})
    receipt = api().run_offline_campaign(prepared, fixture)
    result = api().evaluate_campaign(prepared, receipt)
    assert result["scope"] == "software_fixture"
    assert result["denominator"] == 18
    assert result["statuses"]["timeout"] == 1
    assert result["unknown_usage_attempts"] == 1
    assert result["decision"]["status"] == "indeterminate"
    assert result["decision"]["retain_baseline"] is True
    assert result["expert_interpretation"] == "pending"
    assert result["scientific_improvement_claim"] is False
    assert all(item["attempt_sha256"] and item["episode_root_hash"] for item in result["attempts"])
    assert "fixture-reference-canary" not in json.dumps(result)


def test_strong_single_gets_reconsideration_and_independent_arms_get_private_contexts(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    attempts = api().verified_attempts(prepared, receipt)
    strong = next(item for item in attempts if item["arm"] == "strong_single")
    pair = next(item for item in attempts if item["arm"] == "conditional_2")
    seq = next(item for item in attempts if item["arm"] == "sequential_second")
    assert [call["role"] for call in strong["calls"]] == ["parent", "reconsider", "reconcile"]
    assert [call["role"] for call in pair["calls"]] == ["parent", "investigator_1", "investigator_2", "reconcile"]
    assert [call["role"] for call in seq["calls"]] == ["parent", "investigator_1", "reconcile"]
    assert all(call["context_has_parent_answer"] is False for call in pair["calls"] if call["role"].startswith("investigator"))
    assert strong["usage"]["tokens"] == sum(call["usage"]["tokens"] for call in strong["calls"])
    assert pair["usage"]["tokens"] == sum(call["usage"]["tokens"] for call in pair["calls"])
    assert seq["execution_order"] == "sequential_private_viewpoint"


def test_missing_usage_stops_further_dispatch_and_is_not_zero(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case0:strong_single:parent": {"status": "completed", "usage": None}}))
    item = next(x for x in api().verified_attempts(prepared, receipt) if x["case_id"] == "case0" and x["arm"] == "strong_single")
    assert item["status"] == "unknown_usage"
    assert len(item["calls"]) == 1
    assert item["usage"]["tokens"] is None
    assert item["usage"]["cost_usd"] is None
    remaining = [x for x in api().verified_attempts(prepared, receipt) if x["order"] > item["order"]]
    assert remaining
    assert all(x["status"] == "blocked" and x["stop_reason"] == "campaign_stopped_after_unknown_usage" for x in remaining)
    assert all(x["calls"] == [] and x["usage"]["tokens"] == 0 for x in remaining)


def test_native_strict_cap_blocks_before_any_participant_call(storage):
    spec = protocol(); spec["scope"] = "native_research"
    spec["model"]["backbone"] = "current-authorized-backbone"
    spec["budgets"][0].update(kind="hard_total_tokens", max_total_tokens=8000, soft_token_target=None)
    called = []
    with pytest.raises(PermissionError, match="strict|capability"):
        api().prepare_campaign(storage / "campaign", protocol=spec, cases=cases(storage),
            episode_factory=lambda *args: called.append(args), allow_reference_access=True,
            native_capability={"hard_total_tokens": False})
    assert not called


def test_prompt_size_is_an_operational_cap_and_preflight_is_preserved(storage):
    prepared = prepare(storage)
    fixture = api().OfflineParticipant({"case0:strong_single:parent": {"answer": {"text": "x" * 20000}}})
    receipt = api().run_offline_campaign(prepared, fixture)
    item = next(x for x in api().verified_attempts(prepared, receipt) if x["case_id"] == "case0" and x["arm"] == "strong_single")
    assert item["status"] == "budget_exhausted"
    assert item["calls"][-1]["status"] == "unlaunched_context_cap"
    assert item["calls"][-1]["usage"]["tokens"] == 0


def test_gene_family_and_failed_estimates_cannot_be_hidden_or_invented():
    answer = {"action": "investigate", "gene_statistics": {"rows": [
        {"hypothesis_id": "g0", "estimate_status": "estimated", "p_value": .2, "q_value": .1},
        {"hypothesis_id": "g1", "estimate_status": "failed", "p_value": 0, "q_value": 0}],
        "tested_hypotheses": 2, "represented_hypotheses": 2, "failed_estimates": 1,
        "selection": "whole_family", "coverage": "complete"}}
    reference = {"field_equals": {}, "required_assumptions": [], "required_evidence": [],
                 "hypothesis_family": ["g0", "g1"], "expected_refusal": False,
                 "forbidden_claim_scopes": ["direct_causality", "promoter_binding"]}
    checks = api().score_invariants(answer, reference, status="completed")
    assert checks["multiplicity"] is False
    assert checks["failed_estimates"] is False
    assert checks["expert_interpretation"] is None


def test_replay_is_verified_read_only_without_runner_or_scoring(storage, monkeypatch):
    prepared = prepare(storage)
    seal = api().run_offline_campaign(prepared, api().OfflineParticipant())
    result = api().evaluate_campaign(prepared, seal)
    before = {str(path): path.read_bytes() for path in prepared.root.rglob("*") if path.is_file()}
    def forbidden(*args, **kwargs):
        raise AssertionError("Replay executed a participant or scorer")
    monkeypatch.setattr(api(), "score_invariants", forbidden)
    monkeypatch.setattr(api(), "run_offline_campaign", forbidden)
    replay = api().replay_campaign_scores(prepared.root, result["score_seal_sha256"])
    assert replay == result
    assert before == {str(path): path.read_bytes() for path in prepared.root.rglob("*") if path.is_file()}


def test_attempt_drift_cannot_be_scored(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    path = Path(api().verified_attempts(prepared, receipt)[0]["attempt_path"])
    path.chmod(0o600); path.write_text("{}")
    with pytest.raises(ValueError, match="changed|integrity|hash"):
        api().evaluate_campaign(prepared, receipt)


def test_soft_token_overrun_is_a_retained_failure_and_stops_campaign_dispatch(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case0:conditional_2:parent": {"usage": {"tokens": 20000, "cost_usd": 0, "scope": "artificial_fixture_measurement"}}}))
    attempt = next(x for x in api().verified_attempts(prepared, receipt) if x["case_id"] == "case0" and x["arm"] == "conditional_2")
    assert attempt["status"] == "budget_exhausted"
    assert attempt["usage"]["soft_target_overrun"] is True
    assert len(attempt["calls"]) == 1
    assert attempt["stop_reason"] == "soft_token_target_exceeded"
    remaining = [x for x in api().verified_attempts(prepared, receipt) if x["order"] > attempt["order"]]
    assert remaining
    assert all(x["calls"] == [] and x["status"] == "blocked" for x in remaining)
    assert all(x["stop_reason"] == "campaign_stopped_after_budget_overrun" for x in remaining)


def test_actual_deterministic_baseline_uses_registered_dispatcher(storage, monkeypatch):
    data = cases(storage)
    for case in data:
        case["deterministic_tools"] = [{"tool": "validate_design_matrix", "arguments": {
            "metadata_csv_path": "@metadata", "formula_str": "~ condition"}}]
    prepared = api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    from devharness_server.dispatcher import ToolDispatcher
    original, calls = ToolDispatcher.execute, []
    def tracked(self, tool, arguments):
        calls.append(tool)
        return original(self, tool, arguments)
    monkeypatch.setattr(ToolDispatcher, "execute", tracked)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant(), allow_synthetic_execution=True)
    methods = [x for x in api().verified_attempts(prepared, receipt) if x["arm"] == "deterministic"]
    assert calls == ["validate_design_matrix"] * 3
    assert all(item["status"] == "completed" and item["tool_events"][0]["succeeded"] for item in methods)


def test_native_admission_cannot_omit_model_data_compute_time_or_cost_before_dispatch(storage):
    admission = {"authorized": True, "model": "current", "data_manifest_sha256": "0" * 64,
                 "compute": {"cpu": 1, "memory_mb": 512}, "wall_seconds": 10,
                 "cost_usd": 1, "protocol_sha256": "0" * 64}
    for missing in ("model", "data_manifest_sha256", "compute", "wall_seconds", "cost_usd"):
        incomplete = {key: value for key, value in admission.items() if key != missing}
        with pytest.raises(PermissionError, match="admission|authorized"):
            api().run_native_campaign(None, admission=incomplete, actions_factory=None, backend=None,
                allow_model_execution=True, allow_data_execution=True)


def test_offline_example_has_all_arms_budget_levels_and_stage_strata(storage):
    prepared = api().prepare_offline_example(storage / "example", seed=41)
    manifest = prepared.manifest
    assert set(manifest["protocol"]["arms"]) == set(api().ARMS)
    assert len(manifest["protocol"]["budgets"]) == 2
    assert {case["stage"] for case in manifest["cases"]} == {"pre_fit_methods", "post_fit_explanation"}
    assert all(case["generator_manifest"]["scope"] == "software_fixture" for case in manifest["cases"])
    assert all(case["deterministic_tools"][0]["tool"] == "validate_design_matrix" for case in manifest["cases"])
    assert len({case["family_id"] for case in manifest["cases"]}) == 3
    mechanisms = {}
    for case in manifest["cases"]:
        mechanisms.setdefault(case["generator_manifest"]["mechanism"], set()).add(case["split"])
    assert len(mechanisms) == 3 and all(len(splits) == 1 for splits in mechanisms.values())


@pytest.mark.parametrize("explicit", [False, True])
def test_supported_abstention_keeps_answer_status_and_coverage(storage, explicit):
    data = cases(storage)
    data[2]["public_data"]["observed"]["valid_design"] = False
    path = Path(data[2]["reference_path"])
    reference = json.loads(path.read_text())
    reference["field_equals"]["/action"] = "abstain"
    reference["expected_refusal"] = True
    reference["required_assumptions"] = []
    path.write_text(json.dumps(reference))
    prepared = api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case2:strong_single:parent": {"status": "abstained"}} if explicit else None))
    result = api().evaluate_campaign(prepared, receipt)
    attempt = next(item for item in result["attempts"] if item["case_id"] == "case2" and item["arm"] == "strong_single")
    assert attempt["status"] == "abstained"
    assert attempt["answer"]["action"] == "abstain"
    assert attempt["scores"]["appropriate_refusal"] == 1
    assert attempt["scores"]["action_validity"] == 1
    assert attempt["scores"]["evidence_coverage"] == 1


def test_malformed_answer_is_a_scored_failure_not_a_missing_denominator(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case2:strong_single:reconcile": {"answer": {"assumptions": 4, "evidence_claims": 7, "gene_statistics": {"rows": "invalid"}}}}))
    result = api().evaluate_campaign(prepared, receipt)
    assert result["denominator"] == 18
    attempt = next(item for item in result["attempts"] if item["case_id"] == "case2" and item["arm"] == "strong_single")
    assert attempt["scores"]["action_validity"] == 0


def test_adoption_gate_checks_predeclared_benefit_risk_cost_and_precision():
    spec = protocol(); spec["scope"] = "native_research"
    effects = [{"arm": "conditional_1", "budget_level": "small", "stage": "pre_fit_methods", "paired_cases": 8,
                "independent_families": 8, "effect": .1, "interval": [.06, .14]}]
    attempts = [{"arm": arm, "split": "locked_test", "stage": "pre_fit_methods", "stratum": "balanced", "budget_level": "small",
                 "case_id": "c", "replicate": 0, "scores": {"unit_validity": 1, "unsupported_biology": 1},
                 "usage": {"complete": True, "tokens": tokens, "cost_usd": .2}}
                for arm, tokens in (("strong_single", 100), ("conditional_1", 150))]
    decision = api().assess_adoption(spec, effects, attempts)
    assert decision["retain_baseline"] is True
    assert "resource_cost_criterion_failed" in decision["reasons"]
    attempts[1]["usage"]["tokens"] = 100
    attempts[1]["scores"]["unit_validity"] = 0
    decision = api().assess_adoption(spec, effects, attempts)
    assert "subgroup_risk_veto" in decision["reasons"]
    attempts[1]["scores"]["unit_validity"] = 1
    effects[0]["interval"] = [0, .2]
    decision = api().assess_adoption(spec, effects, attempts)
    assert "minimum_benefit_not_demonstrated" in decision["reasons"]


def test_actual_input_bytes_must_match_across_arms_before_any_answer(storage):
    from tests.test_analysis_episodes import inputs
    from devbench.analysis.episodes import AnalysisEpisode
    count = []
    def unequal_factory(root, case):
        task, paths = inputs(root)
        count.append(1)
        text = paths["expression"].read_text()
        paths["expression"].write_text(text.replace("g0,1.0,", "g0," + str(len(count)) + ".0,", 1))
        return AnalysisEpisode.create(root / "episode", question="Estimate B minus A while respecting donors",
            assay="bulk", public_task=task, inputs=paths, allow_data_execution=True)
    with pytest.raises(ValueError, match="same.*input|input.*same|evidence.*match"):
        api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=cases(storage),
            episode_factory=unequal_factory, allow_reference_access=True)


def test_postfit_nomination_without_verified_outputs_cannot_launch_children(storage):
    data = cases(storage)
    data[2]["stage"] = "post_fit_explanation"
    data[2]["public_data"]["evidence_ready"] = False
    prepared = api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    attempt = next(item for item in api().verified_attempts(prepared, receipt)
                   if item["case_id"] == "case2" and item["arm"] == "conditional_2")
    assert attempt["allocation"] == 0
    assert all(not call["role"].startswith("investigator") for call in attempt["calls"])


def test_postfit_nominal_ready_flag_cannot_replace_receipt_bound_outputs(storage):
    data = cases(storage)
    data[2]["stage"] = "post_fit_explanation"
    data[2]["public_data"]["evidence_ready"] = True
    prepared = api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    attempt = next(item for item in api().verified_attempts(prepared, receipt)
                   if item["case_id"] == "case2" and item["arm"] == "conditional_2")
    assert attempt["allocation"] == 0
    assert attempt["status"] == "abstained"
    assert all(not call["role"].startswith("investigator") for call in attempt["calls"])


def test_generator_mechanism_cannot_leak_after_family_relabeling(storage):
    data = cases(storage)
    for case in data:
        case["generator_manifest"]["mechanism"] = "shared-mechanism"
    with pytest.raises(ValueError, match="mechanism.*split|split.*mechanism"):
        api().validate_protocol(protocol(), data)


def test_generator_mechanism_omission_cannot_bypass_split_grouping(storage):
    data = cases(storage)
    for case in data:
        del case["generator_manifest"]["mechanism"]
    with pytest.raises(ValueError, match="mechanism|generator.*manifest"):
        api().validate_protocol(protocol(), data)


def test_serialized_native_capability_flags_cannot_authorize_setup(storage):
    spec = protocol(); spec["scope"] = "native_research"
    calls = []
    with pytest.raises(PermissionError, match="audited.*backend|native.*authority|serialized"):
        api().prepare_campaign(storage / "campaign", protocol=spec, cases=cases(storage),
            episode_factory=lambda *args: calls.append(args), allow_reference_access=True,
            native_capability={"hard_total_tokens": True, "private_contexts_verified": True})
    assert calls == []
    assert not (storage / "campaign").exists()


def test_current_audited_native_authority_blocks_before_factory_or_output(storage):
    from tests.test_rna_investigation_backend import native_backend
    backend = native_backend(storage / "native")
    spec = protocol(); spec["scope"] = "native_research"
    calls = []
    with pytest.raises(PermissionError, match="blocked_backend_capability"):
        api().prepare_campaign(storage / "campaign", protocol=spec, cases=cases(storage),
            episode_factory=lambda *args: calls.append(args), allow_reference_access=True,
            native_backend=backend)
    assert calls == []
    assert not (storage / "campaign").exists()


def test_malformed_alternative_set_keeps_full_scores_and_replay(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case2:conditional_1:reconcile": {"answer": {"alternatives": 4}}}))
    result = api().evaluate_campaign(prepared, receipt)
    assert result["denominator"] == 18
    attempt = next(item for item in result["attempts"] if item["case_id"] == "case2" and item["arm"] == "conditional_1")
    assert attempt["status"] == "failed"
    assert attempt["scores"]["action_validity"] == 0
    assert api().replay_campaign_scores(prepared.root, result["score_seal_sha256"])["denominator"] == 18


def test_scoring_ignores_mutated_in_memory_policy_and_keeps_disk_freeze(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    original = copy.deepcopy(prepared.manifest["protocol"]["decision_rule"])
    prepared.manifest["protocol"]["decision_rule"].update(minimum_improvement=0, minimum_families=1)
    prepared.manifest["protocol"]["scope"] = "native_research"
    result = api().evaluate_campaign(prepared, receipt)
    assert result["scope"] == "software_fixture"
    assert result["decision"]["frozen_rule"] == original
    assert result["decision"]["retain_baseline"] is True


def test_postfit_facts_are_derived_from_verified_matching_episode_outputs(storage):
    from tests.test_analysis_episodes import create, run_contrast
    source = storage / "postfit-source"; source.mkdir()
    prior, _ = create(source)
    run_contrast(prior)
    prior.step({"kind": "finish"})
    data = cases(storage)
    data[2]["stage"] = "post_fit_explanation"
    data[2]["public_data"]["evidence_ready"] = True
    data[2]["public_data"]["hypothesis_family"] = ["g" + str(index) for index in range(12)]
    data[2]["postfit_output_binding"] = {"episode_path": str(prior.root),
        "episode_root_hash": prior.receipt["root_hash"], "target_id": "mean_contrast"}
    prepared = api().prepare_campaign(storage / "campaign", protocol=protocol(), cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    case = next(item for item in prepared.manifest["cases"] if item["case_id"] == "case2")
    assert case["postfit_evidence"]["status"] == "verified"
    assert case["public_data"]["observed"]["fitted_output"] == prior.results[-1]["target_results"][0]
    assert len(case["public_data"]["observed"]["fitted_output"]["payload"]["gene_statistics"]) == 12
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    attempt = next(item for item in api().verified_attempts(prepared, receipt)
                   if item["case_id"] == "case2" and item["arm"] == "conditional_2")
    assert attempt["allocation"] == 2
    assert attempt["status"] == "completed"


def test_oversized_response_is_retained_as_bounded_failure_not_unreadable_attempt(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case0:strong_single:parent": {"answer": {"text": "x" * 1_100_000},
            "usage": {"tokens": 100, "cost_usd": 0, "scope": "artificial_fixture_measurement"}}}))
    attempts = api().verified_attempts(prepared, receipt)
    attempt = next(item for item in attempts if item["case_id"] == "case0" and item["arm"] == "strong_single")
    assert attempt["status"] == "failed"
    assert attempt["stop_reason"] == "response_output_bound"
    assert Path(attempt["attempt_path"]).stat().st_size <= api().MAX_JSON_BYTES
    rejected = attempt["calls"][0]["rejected_output"]
    assert Path(rejected["path"]).stat().st_size > api().MAX_RESPONSE_BYTES
    result = api().evaluate_campaign(prepared, receipt)
    assert result["denominator"] == 18
    assert result["statuses"]["failed"] == 1


@pytest.mark.parametrize("malformed", [{"answer": {"action": []}}, {"answer": {"action": {}}}, {"status": []}, {"status": {}}])
def test_malformed_action_and_status_containers_are_sealed_with_known_usage(storage, malformed):
    prepared = prepare(storage)
    response = {**malformed, "usage": {"tokens": 100, "cost_usd": 0, "scope": "artificial_fixture_measurement"}}
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({"case0:strong_single:parent": response}))
    attempts = api().verified_attempts(prepared, receipt)
    item = next(attempt for attempt in attempts if attempt["case_id"] == "case0" and attempt["arm"] == "strong_single")
    assert item["status"] == "failed"
    assert item["usage"]["complete"] is True and item["usage"]["tokens"] == 100
    assert len(item["calls"]) == 1
    capture = json.loads(Path(item["calls"][0]["capture_path"]).read_text())
    if "status" in malformed:
        assert capture["response"]["supplied_status"] == malformed["status"]
    else:
        assert capture["response"]["answer"] == malformed["answer"]
    result = api().evaluate_campaign(prepared, receipt)
    assert result["denominator"] == 18
    assert result["statuses"]["failed"] == 1


@pytest.mark.parametrize("action", [[], {}, 1, None])
def test_direct_scorer_rejects_malformed_action_without_type_error(action):
    checks = api().score_invariants({"action": action}, {"field_equals": {}, "expected_refusal": False}, status="completed")
    assert checks["action_validity"] is False
    assert checks["appropriate_refusal"] is False


@pytest.mark.parametrize("status", [[], {}])
def test_direct_scorer_rejects_status_containers_without_type_error(status):
    checks = api().score_invariants({"action": "investigate"}, {"field_equals": {}, "expected_refusal": False}, status=status)
    assert checks["action_validity"] is False


def test_malformed_read_request_container_is_a_sealed_failure(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        "case0:strong_single:parent": {"read_requests": 1,
            "usage": {"tokens": 100, "cost_usd": 0, "scope": "artificial_fixture_measurement"}}}))
    attempt = next(item for item in api().verified_attempts(prepared, receipt)
                   if item["case_id"] == "case0" and item["arm"] == "strong_single")
    assert attempt["status"] == "failed"
    assert attempt["usage"]["tokens"] == 100
    capture = json.loads(Path(attempt["calls"][0]["capture_path"]).read_text())
    assert capture["response"]["supplied_read_requests"] == 1


def test_aggregate_result_uses_the_frozen_explicit_storage_bound_for_replay(storage):
    data, spec = cases(storage), protocol()
    spec["budgets"][0]["max_context_bytes"] = 200000
    spec["budgets"][0]["reconciliation"]["context_bytes"] = 200000
    prepared = api().prepare_campaign(storage / "campaign", protocol=spec, cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    responses = {}
    for case in data:
        for arm in spec["arms"]:
            for role in ("parent", "reconsider", "investigator_1", "investigator_2", "reconcile"):
                responses[":".join((case["case_id"], arm, role))] = {"answer": {
                    "action": "investigate", "biological_unit": "donor", "estimand": "B-minus-A",
                    "assumptions": ["independent_donors"], "evidence_refs": ["design-audit"],
                    "uncertainty": ["x" * 32000], "alternatives": ["assess_assumptions"]},
                    "usage": {"tokens": 1, "cost_usd": 0, "scope": "artificial_fixture_measurement"}}
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant(responses))
    result = api().evaluate_campaign(prepared, receipt)
    result_path = prepared.root / "results.json"
    assert api().MAX_JSON_BYTES < result_path.stat().st_size <= spec["storage_limits"]["aggregate_json_bytes"]
    assert api().replay_campaign_scores(prepared.root, result["score_seal_sha256"])["denominator"] == 18


def test_invalid_timing_retains_known_usage_and_actual_capture(storage):
    prepared = prepare(storage)
    trial = next(item for item in prepared.manifest["schedule"] if item["arm"] != "deterministic")
    key = ":".join((trial["case_id"], trial["arm"], "parent"))
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({key: {
        "usage": {"tokens": 100, "cost_usd": .25, "scope": "artificial_fixture_measurement"},
        "elapsed_seconds": []}}))
    attempts = api().verified_attempts(prepared, receipt)
    attempt = next(item for item in attempts if item["trial_id"] == trial["trial_id"])
    assert attempt["status"] == "capture_error"
    assert attempt["usage"]["tokens"] == 100 and attempt["usage"]["cost_usd"] == .25
    assert attempt["usage"]["elapsed_seconds"] is None and attempt["usage"]["complete"] is False
    assert attempt["usage"]["unknown_dimensions"] == ["elapsed_seconds"]
    capture = json.loads(Path(attempt["calls"][0]["capture_path"]).read_text())
    assert capture["response"]["elapsed_seconds"] == []
    assert all(item["calls"] == [] for item in attempts if item["order"] > trial["order"])
    assert api().evaluate_campaign(prepared, receipt)["denominator"] == 18


@pytest.mark.parametrize("cost", [1e308, 10 ** 308])
def test_finite_cost_overflow_keeps_per_call_proof_and_seals_full_denominator(storage, cost):
    prepared = prepare(storage)
    trial = next(item for item in prepared.manifest["schedule"] if item["arm"] != "deterministic")
    responses = {":".join((trial["case_id"], trial["arm"], role)): {
        "usage": {"tokens": 1, "cost_usd": cost, "scope": "artificial_fixture_measurement"}}
        for role in ("parent", "reconsider", "investigator_1", "investigator_2", "reconcile")}
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant(responses))
    attempts = api().verified_attempts(prepared, receipt)
    attempt = next(item for item in attempts if item["trial_id"] == trial["trial_id"])
    assert attempt["status"] == "capture_error"
    assert attempt["stop_reason"] == "aggregate_cost_overflow"
    assert attempt["usage"]["tokens"] == 2 and attempt["usage"]["cost_usd"] is None
    assert len(attempt["calls"]) == 2
    assert all(call["usage"]["cost_usd"] == cost and Path(call["capture_path"]).is_file() for call in attempt["calls"])
    assert len(attempts) == 18
    assert all(item["calls"] == [] for item in attempts if item["order"] > trial["order"])
    assert api().evaluate_campaign(prepared, receipt)["decision"]["status"] == "indeterminate"


@pytest.mark.parametrize("dimension", ["cost_usd", "elapsed_seconds"])
def test_integer_accounting_totals_retain_components_but_reject_unsupported_sum(dimension):
    calls = [{"usage": {"tokens": 100, "cost_usd": 10 ** 308 if dimension == "cost_usd" else 1},
              "elapsed_seconds": 10 ** 308 if dimension == "elapsed_seconds" else .001,
              "launch_confirmed": True} for _ in range(2)]
    usage = api()._call_usage(calls)
    assert usage["tokens"] == 200
    assert usage[dimension] is None and usage["complete"] is False
    assert usage["unknown_dimensions"] == [dimension]
    assert usage["aggregation_errors"] == [dimension + "_overflow"]
    assert all((call["usage"] if dimension == "cost_usd" else call)[dimension] == 10 ** 308 for call in calls)


@pytest.mark.parametrize("cost", [1e308, 10 ** 308, 10 ** 400, None, []])
def test_standalone_adoption_defers_unsupported_currency_totals(cost):
    spec = protocol(); spec["scope"] = "native_research"
    effects = [{"arm": "conditional_1", "budget_level": "small", "stage": "pre_fit_methods", "paired_cases": 8,
                "independent_families": 8, "effect": .1, "interval": [.06, .14]}]
    attempts = [{"arm": arm, "split": "locked_test", "stage": "pre_fit_methods", "stratum": "balanced", "budget_level": "small",
                 "case_id": "c" + str(index), "replicate": 0, "scores": {"unit_validity": 1, "unsupported_biology": 1},
                 "usage": {"complete": True, "tokens": 100, "cost_usd": cost}}
                for arm in ("strong_single", "conditional_1") for index in range(2)]
    decision = api().assess_adoption(spec, effects, attempts)
    assert decision["status"] == "indeterminate" and decision["retain_baseline"] is True
    assert "missing_usage" in decision["reasons"]


def test_postseal_scoring_preserves_exact_unbounded_token_counts(storage):
    prepared = prepare(storage)
    trial = next(item for item in prepared.manifest["schedule"]
                 if item["arm"] == "strong_single" and item["case_id"] == "case2")
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        ":".join((trial["case_id"], trial["arm"], "parent")): {
            "usage": {"tokens": 10 ** 400, "cost_usd": .25, "scope": "artificial_fixture_measurement"}}}))
    attempts = api().verified_attempts(prepared, receipt)
    attempt = next(item for item in attempts if item["trial_id"] == trial["trial_id"])
    assert attempt["status"] == "budget_exhausted" and attempt["usage"]["complete"] is True
    assert attempt["usage"]["tokens"] == 10 ** 400 and attempt["usage"]["cost_usd"] == .25
    assert len(attempt["calls"]) == 1 and attempt["usage"]["soft_target_overrun"] is True
    capture = json.loads(Path(attempt["calls"][0]["capture_path"]).read_text())
    assert capture["response"]["usage"]["tokens"] == 10 ** 400
    assert all(item["calls"] == [] for item in attempts if item["order"] > trial["order"])
    result = api().evaluate_campaign(prepared, receipt)
    assert result["denominator"] == 18 and result["decision"]["retain_baseline"] is True
    assert Path(result["score_seal_path"]).is_file()
    replay = api().replay_campaign_scores(prepared.root, result["score_seal_sha256"])
    replayed = next(item for item in replay["attempts"] if item["trial_id"] == trial["trial_id"])
    assert replay["denominator"] == 18 and replayed["usage"]["tokens"] == 10 ** 400


@pytest.mark.parametrize(("delta", "exceeded"), [(-1, False), (0, False), (1, True)])
def test_adoption_compares_exact_integer_token_ratio_boundary(delta, exceeded):
    spec = protocol(); spec["scope"] = "native_research"
    numerator, denominator = spec["decision_rule"]["max_token_ratio"].as_integer_ratio()
    baseline, candidate = denominator * 10 ** 400, numerator * 10 ** 400 + delta
    effects = [{"arm": "conditional_1", "budget_level": "small", "stage": "pre_fit_methods", "paired_cases": 8,
                "independent_families": 8, "effect": .1, "interval": [.06, .14]}]
    attempts = [{"arm": arm, "split": "locked_test", "stage": "pre_fit_methods", "stratum": "balanced", "budget_level": "small",
                 "case_id": "c", "replicate": 0, "scores": {"unit_validity": 1, "unsupported_biology": 1},
                 "usage": {"complete": True, "tokens": tokens, "cost_usd": .25}}
                for arm, tokens in (("strong_single", baseline), ("conditional_1", candidate))]
    decision = api().assess_adoption(spec, effects, attempts)
    assert ("resource_cost_criterion_failed" in decision["reasons"]) is exceeded
    assert attempts[0]["usage"]["tokens"] == baseline and attempts[1]["usage"]["tokens"] == candidate


@pytest.mark.parametrize("ratio", [None, [], True, "1.1", -1, 10 ** 400])
def test_adoption_defers_an_unsupported_token_ratio(ratio):
    spec = protocol(); spec["scope"] = "native_research"
    spec["decision_rule"]["max_token_ratio"] = ratio
    effects = [{"arm": "conditional_1", "budget_level": "small", "stage": "pre_fit_methods", "paired_cases": 8,
                "independent_families": 8, "effect": .1, "interval": [.06, .14]}]
    attempts = [{"arm": arm, "split": "locked_test", "stage": "pre_fit_methods", "stratum": "balanced", "budget_level": "small",
                 "case_id": "c", "replicate": 0, "scores": {"unit_validity": 1, "unsupported_biology": 1},
                 "usage": {"complete": True, "tokens": 100, "cost_usd": .25}}
                for arm in ("strong_single", "conditional_1")]
    decision = api().assess_adoption(spec, effects, attempts)
    assert decision["status"] == "indeterminate" and decision["retain_baseline"] is True


@pytest.mark.parametrize(("dimension", "oversized"), [("cost_usd", False), ("elapsed_seconds", False), ("cost_usd", True)])
def test_unrepresentable_integer_telemetry_retains_capture_and_seals_denominator(storage, dimension, oversized):
    prepared = prepare(storage)
    trial = next(item for item in prepared.manifest["schedule"] if item["arm"] != "deterministic")
    response = {"usage": {"tokens": 100, "cost_usd": .25, "scope": "artificial_fixture_measurement"},
                "elapsed_seconds": .001}
    if dimension == "cost_usd":
        response["usage"][dimension] = 10 ** 400
    else:
        response[dimension] = 10 ** 400
    if oversized:
        response["answer"] = {"text": "x" * 1_100_000}
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant({
        ":".join((trial["case_id"], trial["arm"], "parent")): response}))
    attempts = api().verified_attempts(prepared, receipt)
    attempt = next(item for item in attempts if item["trial_id"] == trial["trial_id"])
    assert attempt["status"] == ("failed" if oversized else "unknown_usage" if dimension == "cost_usd" else "capture_error")
    assert attempt["usage"]["tokens"] == 100
    assert attempt["usage"][dimension] is None
    assert attempt["usage"]["unknown_dimensions"] == [dimension]
    other = "elapsed_seconds" if dimension == "cost_usd" else "cost_usd"
    assert attempt["usage"][other] == (.001 if other == "elapsed_seconds" else .25)
    assert len(attempt["calls"]) == 1 and attempt["calls"][0]["launch_confirmed"] is True
    capture = json.loads(Path(attempt["calls"][0]["capture_path"]).read_text())
    raw = json.loads(Path(attempt["calls"][0]["rejected_output"]["path"]).read_text()) if oversized else capture["response"]
    retained = raw["usage"] if dimension == "cost_usd" else raw
    assert retained[dimension] == 10 ** 400
    assert all(item["calls"] == [] for item in attempts if item["order"] > trial["order"])
    result = api().evaluate_campaign(prepared, receipt)
    assert result["denominator"] == 18 and result["decision"]["status"] == "indeterminate"
    assert api().replay_campaign_scores(prepared.root, result["score_seal_sha256"])["denominator"] == 18


@pytest.mark.parametrize("cost", [None, {}, [], True, -1, "0.25", float("nan"), float("inf"), 10 ** 400])
def test_invalid_accounting_scalar_retains_independently_known_dimensions(cost):
    usage = {"tokens": 100, "cost_usd": cost}
    assert api()._known_usage(usage) is False
    aggregate = api()._call_usage([{"usage": usage, "elapsed_seconds": .001, "launch_confirmed": True}])
    assert aggregate["tokens"] == 100 and aggregate["elapsed_seconds"] == .001
    assert aggregate["cost_usd"] is None and aggregate["unknown_dimensions"] == ["cost_usd"]


@pytest.mark.parametrize("cost", [0, 1, .25, 1e308])
def test_bounded_positive_accounting_scalar_remains_known(cost):
    usage = {"tokens": 100, "cost_usd": cost}
    assert api()._known_usage(usage) is True
    aggregate = api()._call_usage([{"usage": usage, "elapsed_seconds": .001, "launch_confirmed": True}])
    assert aggregate["cost_usd"] == cost and aggregate["complete"] is True


@pytest.mark.parametrize("cost", [float("nan"), float("inf")])
def test_nonfinite_fixture_telemetry_is_rejected_before_participation(cost):
    with pytest.raises(ValueError):
        api().OfflineParticipant({"unused": {"usage": {"tokens": 100, "cost_usd": cost}}})


def test_deterministic_request_schema_is_validated_before_any_factory_setup(storage):
    data, spec, called = cases(storage), protocol(), []
    for case in data:
        case["deterministic_tools"] = [{"tool": "validate_design_matrix", "arguments": {
            "metadata_csv_path": "@metadata", "formula_str": 1}}]
    with pytest.raises(ValueError, match="deterministic.*schema|schema.*deterministic"):
        api().prepare_campaign(storage / "campaign", protocol=spec, cases=data,
            episode_factory=lambda *args: called.append(args), allow_reference_access=True)
    assert called == []
    assert not (storage / "campaign").exists()


def test_connected_case_views_are_one_independent_analysis_unit(storage):
    data, spec = cases(storage), protocol()
    template = data.pop()
    template["generator_manifest"]["mechanism"] = "same-matrix-same-mechanism"
    for index in range(5):
        case = copy.deepcopy(template)
        case["case_id"] = "locked-view-" + str(index)
        case["family_id"] = "relabeled-family-" + str(index)
        case["generator_manifest"]["family_id"] = case["family_id"]
        data.append(case)
    prepared = api().prepare_campaign(storage / "campaign", protocol=spec, cases=data,
        episode_factory=episode_factory, allow_reference_access=True)
    result = api().evaluate_campaign(prepared, api().run_offline_campaign(prepared, api().OfflineParticipant()))
    effect = next(item for item in result["paired_effects"] if item["arm"] == "conditional_1" and item["stage"] == "pre_fit_methods")
    assert effect["independent_families"] == 1
    assert effect["interval"] is None
    assert effect["paired_cases"] == 5
    assert result["decision"]["status"] == "indeterminate"


def test_readonly_replay_rechecks_the_preserved_protected_reference(storage):
    prepared = prepare(storage)
    receipt = api().run_offline_campaign(prepared, api().OfflineParticipant())
    result = api().evaluate_campaign(prepared, receipt)
    first = next(iter(prepared.manifest["plans"].values()))
    reference = Path(first["plan_path"]).parent / "reference.json"
    reference.chmod(0o600); reference.write_text("{}")
    with pytest.raises(ValueError, match="reference.*changed|changed.*reference"):
        api().replay_campaign_scores(prepared.root, result["score_seal_sha256"])
