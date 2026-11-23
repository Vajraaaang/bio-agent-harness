"""Fresh synthetic accounting controls, never a scientific campaign."""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def api():
    assert importlib.util.find_spec("devbench.analysis.rna_release_evaluation") is not None, (
        "T23 requires a separate v2.4 validator/accountant and inert score replay"
    )
    return importlib.import_module("devbench.analysis.rna_release_evaluation")


def identity(name):
    return {"name": name, "sha256": hashlib.sha256(name.encode()).hexdigest()}


def study(name, **changes):
    return {"study_id": name, "underlying_study_id": name, "biological_source_ids": [name + "-source"],
            "variant_parent_ids": [], "generator_identity": identity("same-synthetic-generator"),
            "partition": "development", "regime": "B-I", "stratum": "signal",
            "measurements": identity(name + "-measurements"), "requirement_source": None,
            "independent": True, "hidden_reference_access_by_participant": False, **changes}


def endpoint(**changes):
    return {"endpoint_id": "fdp", "kind": "fdp", "denominator": "independent_studies",
            "partition": "development", "regime": "B-I",
            "reporting_scope": "request", "arm_ids": ["C"], "family_ids": ["f"],
            "stratum": "signal", "bound_method": None, "confidence_allocation_id": None,
            "required_quantities": [], "ratification": "unratified", **changes}


def hypothesis(gene, false, **changes):
    return {"hypothesis_id": gene, "family_id": "f", "contrast_id": "treated-control",
            "population_id": "bulk", "reporting_scope": "request", "exposed": True,
            "retained": True, "withdrawn": False, "false_discovery": false,
            "adjudication": identity("fresh-engineering-labels"), **changes}


def records():
    m = api()
    p = m.unratified_protocol()
    p["studies"] = [study("s1"), study("s2")]
    p["endpoints"] = [endpoint()]
    p["testing_alpha"] = .05
    p["family_allocations"] = [{"family_id": "f", "population_id": "bulk", "contrast_id": "treated-control",
                                 "weight": 1., "required_primary": True}]
    p["resource_aggregation"] = dict.fromkeys(m.RESOURCE_KEYS, "sum")
    assignments, attempts, outcomes = [], [], []
    for name in ("s1", "s2"):
        aid, tid = name + "-C", name + "-attempt"
        assignments.append({"assignment_id": aid, "study_id": name, "arm_id": "C",
                            "endpoint_ids": ["fdp"], "expected_attempt_ids": [tid]})
        attempts.append({"attempt_id": tid, "assignment_id": aid, "retry_of": None,
            "status": "completed", "exposures_known": True, "hypotheses": [],
            "usage": dict.fromkeys(m.RESOURCE_KEYS, 0), "invocation": identity(tid),
            "interventions": [], "started_at": None, "ended_at": None, "x9": None})
        outcomes.append({"assignment_id": aid, "terminal_attempt_id": tid, "useful": True,
                         "answerable": True, "terminal_false_block": False, "harmful_closure": False,
                         "adjudication": identity("engineering-outcome-labels"), "independent": False})
    ledger = {"format": m.LEDGER_FORMAT, "protocol_sha256": m.record_digest(p),
              "assignments": assignments, "attempts": attempts, "outcomes": outcomes,
              "review_packets": [], "numerical_records": [], "fault_records": [], "integrity_records": []}
    return p, ledger


def refresh(p, ledger):
    ledger["protocol_sha256"] = api().record_digest(p)


def test_unratified_template_is_closed_blocked_and_never_qualifies():
    m = api()
    result = m.validate_release_protocol(m.unratified_protocol())
    assert result["protocol_id"] == "devbench.rna-v24-evaluation-protocol/1"
    assert result["admission"] == "blocked" and result["blockers"]
    assert not any(result["qualification"].values())
    p = m.unratified_protocol()
    p["invented_approval"] = True
    with pytest.raises(ValueError):
        m.validate_release_protocol(p)


def test_mean_study_fdp_is_not_pooled_discoveries():
    p, ledger = records()
    ledger["attempts"][0]["hypotheses"] = [hypothesis("g1", True)]
    ledger["attempts"][1]["hypotheses"] = [hypothesis("g" + str(i), False) for i in range(99)]
    score = api().account_release_campaign(p, ledger)
    assert score["fdp"]["fdp"]["C"]["mean_study_fdp"] == .5
    assert score["fdp"]["fdp"]["C"]["independent_n"] == 2
    assert not any(score["qualification"].values())


def test_withdrawn_partial_exposure_retry_and_unknown_spend_are_retained():
    p, ledger = records()
    original = ledger["attempts"][0]
    original.update(status="partial", hypotheses=[hypothesis("g", True, retained=False, withdrawn=True)])
    original["usage"]["tokens"] = None
    retry = deepcopy(original)
    retry.update(attempt_id="retry", retry_of=original["attempt_id"], status="completed", hypotheses=[])
    retry["usage"]["tokens"] = 17
    ledger["attempts"].append(retry)
    ledger["assignments"][0]["expected_attempt_ids"].append("retry")
    ledger["outcomes"][0]["terminal_attempt_id"] = "retry"
    score = api().account_release_campaign(p, ledger)
    assert score["attempt_statuses"]["partial"] == 1
    assert score["retries"] == 1
    assert score["fdp"]["fdp"]["C"]["mean_study_fdp"] == .5
    assert score["usage"]["tokens"] is None
    assert score["unknown_usage_attempts"] == [original["attempt_id"]]
    assert score["assigned_arm_studies"] == 2


@pytest.mark.parametrize("status", ["failed", "partial", "timeout", "cancelled", "blocked", "uncertain", "unlaunched"])
def test_every_terminal_failure_remains_in_assignment_and_useful_denominators(status):
    p, ledger = records()
    # Explicit positive adjudication control for this denominator test. The
    # separate non-independent-label controls require zero valid useful counts.
    for outcome in ledger["outcomes"]:
        outcome["independent"] = True
    ledger["attempts"][0]["status"] = status
    ledger["outcomes"][0]["useful"] = False
    score = api().account_release_campaign(p, ledger)
    assert score["assigned_arm_studies"] == 2
    assert score["useful_completion"]["C"]["denominator"] == 2
    assert score["useful_completion"]["C"]["numerator"] == 1
    assert score["fdp"]["fdp"]["C"]["mean_study_fdp"] == 0


def test_unknown_exposure_or_truth_is_unavailable_not_zero():
    p, ledger = records()
    ledger["attempts"][0]["exposures_known"] = False
    score = api().account_release_campaign(p, ledger)
    assert score["fdp"]["fdp"]["C"]["mean_study_fdp"] is None
    ledger["attempts"][0].update(exposures_known=True, hypotheses=[hypothesis("g", None)])
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["mean_study_fdp"] is None


def test_family_contrast_and_population_keys_are_not_collapsed():
    p, ledger = records()
    p["family_allocations"] = [
        {"family_id": "f", "population_id": "bulk", "contrast_id": "treated-control", "weight": 1/3, "required_primary": True},
        {"family_id": "other-contrast", "population_id": "bulk", "contrast_id": "other-control", "weight": 1/3, "required_primary": True},
        {"family_id": "other-population", "population_id": "other", "contrast_id": "treated-control", "weight": 1/3, "required_primary": True}]
    p["endpoints"][0]["family_ids"] = ["f", "other-contrast", "other-population"]
    ledger["attempts"][0]["hypotheses"] = [hypothesis("g", True),
        hypothesis("g", False, family_id="other-contrast", contrast_id="other-control"),
        hypothesis("g", False, family_id="other-population", population_id="other")]
    refresh(p, ledger)
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["mean_study_fdp"] == pytest.approx(1 / 6)


def test_removing_an_entire_assigned_arm_is_rejected():
    p, ledger = records()
    p["endpoints"][0]["arm_ids"].append("D")
    refresh(p, ledger)
    with pytest.raises(ValueError, match="assignment"):
        api().account_release_campaign(p, ledger)


@pytest.mark.parametrize("change", ["unbudgeted", "foreign_scope"])
def test_extra_exposed_hypotheses_cannot_disappear_outside_accounting_scope(change):
    p, ledger = records()
    ledger["attempts"][0]["hypotheses"] = [hypothesis("g", True,
        **({"family_id": "unknown"} if change == "unbudgeted" else {"reporting_scope": "unknown"}))]
    with pytest.raises(ValueError):
        api().account_release_campaign(p, ledger)


def test_initial_resolved_holds_closures_and_terminal_blocks_have_separate_denominators():
    p, ledger = records()
    for i in range(3):
        ledger["review_packets"].append({"packet_id": f"p{i}", "assignment_id": "s1-C", "attempt_id": "s1-attempt",
            "touchpoint_id": "touch" + str(i), "valid": i != 2, "consequential_error": i == 2,
            "detected": i == 2, "initial_hold": i == 0, "initial_hold_erroneous": i == 0,
            "resolved": i == 0, "harmful_closure": False, "adjudication": identity("synthetic-packet-labels"), "independent": True})
    score = api().account_release_campaign(p, ledger)
    assert score["review"]["initial_false_holds"]["point"] == .5
    assert score["review"]["resolved_false_holds"]["point"] == 1
    assert score["review"]["review_detection"]["point"] == 1
    assert score["terminal_false_blocking"]["C"]["scope_denominator"] == 2
    assert score["terminal_false_blocking"]["C"]["denominator"] == 0
    assert score["terminal_false_blocking"]["C"]["unknown_eligibility"] == 2
    assert score["terminal_false_blocking"]["C"]["point"] is None
    assert score["terminal_false_blocking"]["C"]["numerator"] == 0


def test_missing_review_adjudication_cannot_be_reported_as_success():
    p, ledger = records()
    ledger["review_packets"] = [{"packet_id": "p", "assignment_id": "s1-C", "attempt_id": "s1-attempt",
        "touchpoint_id": "touch", "valid": False, "consequential_error": True, "detected": True,
        "initial_hold": True, "initial_hold_erroneous": False, "resolved": False, "harmful_closure": False,
        "adjudication": None, "independent": None}]
    assert api().account_release_campaign(p, ledger)["review"]["review_detection"]["point"] is None


def test_unknown_answerability_does_not_create_a_favorable_false_block_rate():
    p, ledger = records()
    ledger["outcomes"][0]["answerable"] = None
    assert api().account_release_campaign(p, ledger)["terminal_false_blocking"]["C"]["point"] is None


def test_latency_needs_explicit_method_and_uses_all_attempts_per_study():
    p, ledger = records()
    p["latency"] = {"method": "nearest_rank", "unit": "assigned_study_wall_interval", "ratification": "unratified"}
    for i, row in enumerate(ledger["attempts"]):
        row.update(started_at="2026-11-14T10:00:00Z", ended_at=f"2026-11-14T10:0{i+1}:00Z")
    refresh(p, ledger)
    scores = api().account_release_campaign(p, ledger)
    assert scores["costs"]["C"]["latency_p50"] == 60
    assert scores["costs"]["C"]["latency_p95"] == 120
    ledger["attempts"][0]["ended_at"] = None
    assert api().account_release_campaign(p, ledger)["costs"]["C"]["latency_p95"] is None


def x9_records():
    p, ledger = records()
    p["studies"] = [study("s1")]
    p["endpoints"] = [endpoint(arm_ids=["A0", "B", "C", "D"])]
    p["x9"].update(packet=identity("same-packet"), packet_frozen_at="2026-11-14T09:00:00Z", cost_attribution="charged_to_total_budget")
    template_assignment, template_attempt, template_outcome = (deepcopy(ledger[k][0]) for k in ("assignments", "attempts", "outcomes"))
    ledger.update(assignments=[], attempts=[], outcomes=[])
    for arm, (retrieval, common) in zip(["A0", "B", "C", "D"], [(False, False), (False, True), (True, False), (True, True)]):
        aid, tid = "s1-" + arm, arm + "-attempt"
        ledger["assignments"].append({**template_assignment, "assignment_id": aid, "arm_id": arm, "expected_attempt_ids": [tid]})
        ledger["outcomes"].append({**template_outcome, "assignment_id": aid, "terminal_attempt_id": tid, "independent": True})
        x = {"retrieval_enabled": retrieval, "common_packet": common, "packet": p["x9"]["packet"] if common else None,
             "packet_received_before_result": True if common else None, "packet_cost": dict.fromkeys(api().RESOURCE_KEYS, 0),
             "actual_bindings": {key: identity(key) for key in api().Bindings.model_fields},
             "actual_total_budget": {"identity": identity("shared-budget"), "limits": dict.fromkeys(api().RESOURCE_KEYS, 100),
                                     "reserves": dict.fromkeys(api().RESOURCE_KEYS, 0), "enforcement": "observed_only"},
             "actual_clarification": identity("same-clarification"), "actual_intervention": identity("same-intervention"), "capture_correct": True}
        x["actual_numerical_output"] = identity("same-actual-numerical-output")
        ledger["attempts"].append({**deepcopy(template_attempt), "attempt_id": tid, "assignment_id": aid, "x9": x})
    refresh(p, ledger)
    return p, ledger


def test_x9_complete_engineering_controls_remain_inconclusive_without_supported_paired_interval():
    p, ledger = x9_records()
    score = api().account_release_campaign(p, ledger)["x9"]
    assert score["status"] == "inconclusive"
    assert score["paired_benefit_interval"] is None and not score["improved_reliability"]
    assert score["design"] == "unratified_engineering_proposal"


@pytest.mark.parametrize("missing", ["model", "runtime", "numerics", "tools", "budget", "budget_limits", "actual_usage", "adjudication", "packet_cost", "numerical_output"])
def test_x9_missing_actual_values_do_not_match_merely_because_both_are_null(missing):
    p, ledger = x9_records()
    for attempt in ledger["attempts"]:
        x = attempt["x9"]
        if missing in x["actual_bindings"]:
            x["actual_bindings"][missing] = None
        elif missing == "budget":
            x["actual_total_budget"]["identity"] = None
        elif missing == "budget_limits":
            x["actual_total_budget"]["limits"]["tokens"] = None
        elif missing == "actual_usage":
            attempt["usage"]["tokens"] = None
        elif missing == "packet_cost":
            x["packet_cost"]["tokens"] = None
        elif missing == "numerical_output":
            x["actual_numerical_output"] = None
        else:
            for outcome in ledger["outcomes"]:
                outcome["adjudication"] = None
    assert api().account_release_campaign(p, ledger)["x9"]["status"] == "not_assessed"


def test_x9_unmatched_actual_model_is_not_a_benefit_comparison():
    p, ledger = x9_records()
    ledger["attempts"][0]["x9"]["actual_bindings"]["model"] = identity("different")
    assert api().account_release_campaign(p, ledger)["x9"]["status"] == "not_assessed"


def test_x9_packet_cost_cannot_be_free_or_packet_changed():
    p, ledger = x9_records()
    ledger["attempts"][1]["x9"]["packet_cost"]["tokens"] = 5
    with pytest.raises(ValueError, match="packet_cost"):
        api().account_release_campaign(p, ledger)


def test_x9_total_budget_includes_retries_not_just_one_attempt():
    p, ledger = x9_records()
    original = ledger["attempts"][0]
    original["usage"]["tokens"] = 70
    retry = deepcopy(original)
    retry.update(attempt_id="retried", retry_of=original["attempt_id"])
    ledger["attempts"].append(retry)
    ledger["assignments"][0]["expected_attempt_ids"].append("retried")
    ledger["outcomes"][0]["terminal_attempt_id"] = "retried"
    assert api().account_release_campaign(p, ledger)["x9"]["status"] == "not_assessed"


def test_numerical_expected_denominator_keeps_missing_quantities():
    p, ledger = records()
    p["endpoints"].append(endpoint(endpoint_id="numeric", kind="numerical_agreement", denominator="numerical_quantities",
                                   required_quantities=["logFC", "p"]))
    p["tolerances"] = [{"tolerance_id": "effect", "quantity": "logFC", "scale": "linear", "absolute": 1e-10,
        "relative": 1e-8, "near_zero": "absolute_only", "boundary": "compare", "nonfinite": "unavailable",
        "missing": "unavailable", "regimes": ["B-I"], "ratification": "ratified"}]
    for a in ledger["assignments"]:
        a["endpoint_ids"].append("numeric")
    ledger["numerical_records"] = [{"record_id": "quantity", "assignment_id": "s1-C", "quantity": "logFC",
        "observed": 1.0, "expected": 1.0, "tolerance_id": "effect", "observed_status": "finite",
        "reference_status": "finite", "reference_binding": identity("fresh-independent-direct-R-engineering-output")}]
    refresh(p, ledger)
    scores = api().account_release_campaign(p, ledger)
    assert scores["numerical"]["expected_denominator"] == 4
    assert scores["numerical"]["missing_expected"] == 3
    assert sum(r["agreement"] is True for r in scores["numerical"]["quantities"]) == 1
    assert scores["qualification"]["numerical"] is False


def test_critical_integrity_keeps_opportunity_case_and_study_denominators():
    p, ledger = records()
    ledger["integrity_records"] = [{"record_id": "i" + str(i), "assignment_id": "s1-C", "case_id": "case",
        "opportunity_id": "o" + str(i), "critical_violation": i == 0, "accepted": i == 0,
        "adjudication": identity("synthetic-integrity-labels"), "independent": False} for i in range(3)]
    score = api().account_release_campaign(p, ledger)["critical_integrity"]
    assert (score["accepted_critical_violations"], score["opportunity_denominator"], score["case_denominator"],
            score["assigned_study_denominator"]) == (1, 3, 1, 2)
    assert score["unknown_opportunities"] == 3 and score["qualified"] is False


def test_missing_review_eligibility_keeps_rates_unavailable():
    p, ledger = records()
    for i in range(2):
        ledger["review_packets"].append({"packet_id": str(i), "assignment_id": "s1-C", "attempt_id": "s1-attempt",
            "touchpoint_id": "touch" + str(i), "valid": True if i == 0 else None,
            "consequential_error": False if i == 0 else None, "detected": False, "initial_hold": False,
            "initial_hold_erroneous": False, "resolved": False, "harmful_closure": False,
            "adjudication": identity("synthetic"), "independent": True})
    assert api().account_release_campaign(p, ledger)["review"]["initial_false_holds"]["point"] is None


def test_one_confidence_allocation_cannot_be_reused_across_arms():
    p, _ = records()
    p["endpoints"][0].update(arm_ids=["C", "D"], bound_method="empirical_bernstein", confidence_allocation_id="a")
    p["gates"] = [{"gate_id": "g", "endpoint_ids": ["fdp"], "threshold": None, "direction": None,
                    "owner_role": "statistical", "evidence": None, "ratification": "unratified"}]
    p["confidence"].update(global_delta=.05, allocations=[{"allocation_id": "a", "endpoint_id": "fdp",
        "family_id": "f", "stratum": "signal", "gate_id": "g", "delta": .05}])
    with pytest.raises(ValueError):
        api().validate_release_protocol(p)


def test_legacy_native_stays_blocked_without_launch(tmp_path, monkeypatch, capsys):
    import runpy
    from devbench.analysis import rna_investigation_evaluation as legacy
    def forbidden(*args, **kwargs):
        pytest.fail("historical native evaluator was launched")
    monkeypatch.setattr(legacy, "run_native_campaign", forbidden)
    module = runpy.run_path(str(ROOT / "scripts/run_rna_investigation_evaluation.py"))
    assert module["main"](["--native", "--output", str(tmp_path / "unused")]) == 2
    assert json.loads(capsys.readouterr().out)["provider_calls"] == 0
    assert not (tmp_path / "unused").exists()


def test_x9_common_packet_cannot_change_after_freeze():
    p, ledger = x9_records()
    ledger["attempts"][1]["x9"]["packet"] = identity("later-packet")
    with pytest.raises(ValueError, match="packet_identity"):
        api().account_release_campaign(p, ledger)


def test_explicit_existing_owner_adapters_keep_legacy_protocols_separate():
    p, ledger = records()
    from devbench.analysis import rna_investigation_evaluation as investigation
    from devbench.analysis import rna_scientific_trial_contract as contract
    from devbench.analysis import rna_scientific_trial as trial
    from devbench.analysis import rna_workflow_evaluation as workflow
    for module, function, arguments in ((investigation, "evaluate_v24_release_records", (p, ledger)),
        (contract, "validate_v24_release_protocol", (p,)), (trial, "inspect_v24_release_readiness", (p,)),
        (workflow, "summarize_v24_release_comparison", (p, ledger))):
        assert hasattr(module, function)
        assert getattr(module, function)(*arguments)["admission"] == "blocked"
    assert investigation.FORMAT == "devbench.rna-investigation-campaign/1"
    assert workflow.ARMS == ("checked_workflow", "analyst_same_tools_checks", "analyst_plus_reviewer")


def test_repeated_views_cluster_but_generator_code_does_not():
    p, ledger = records()
    assert api().account_release_campaign(p, ledger)["assigned_studies"] == 2
    p["studies"][1]["underlying_study_id"] = "s1"
    p["studies"][1]["biological_source_ids"] = p["studies"][0]["biological_source_ids"]
    refresh(p, ledger)
    assert api().account_release_campaign(p, ledger)["assigned_studies"] == 1
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["independent_n"] == 1


@pytest.mark.parametrize("mutation", ["partition", "hidden", "lineage", "assignment", "attempt", "protocol", "conflict"])
def test_conflicting_or_incomplete_records_raise(mutation):
    p, ledger = records()
    if mutation == "partition":
        p["studies"][1].update(underlying_study_id="s1", partition="locked_qualification")
    elif mutation == "hidden":
        p["studies"][0]["hidden_reference_access_by_participant"] = True
    elif mutation == "lineage":
        p["studies"][0]["biological_source_ids"] = []
    elif mutation == "assignment":
        ledger["assignments"].pop()
    elif mutation == "attempt":
        ledger["attempts"].pop()
    elif mutation == "conflict":
        ledger["attempts"][0]["hypotheses"] = [hypothesis("g", True), hypothesis("g", False)]
    refresh(p, ledger)
    if mutation == "protocol":
        p["sampling"]["fixed_n"] = 100
    with pytest.raises(ValueError):
        api().account_release_campaign(p, ledger)


@pytest.mark.parametrize("n,delta,expected", [(200, .05, .093253), (200, .005, .120252),
                                             (600, .05, .064370), (900, .05 / 6, .064225)])
def test_registered_empirical_bernstein_arithmetic(n, delta, expected):
    value = api().empirical_bernstein_upper([.05] * n, delta=delta, independent=True,
                                           sampling_frozen=True, stopping_frozen=True)
    assert value == pytest.approx(expected, abs=5e-7)


def test_bernstein_requires_independence_and_bessel_variance():
    m = api()
    for kw in ({"independent": False}, {"sampling_frozen": False}, {"stopping_frozen": False}):
        args = dict(delta=.05, independent=True, sampling_frozen=True, stopping_frozen=True)
        args.update(kw)
        with pytest.raises(ValueError):
            m.empirical_bernstein_upper([0, .1], **args)
    with pytest.raises(ValueError):
        m.empirical_bernstein_upper([0], delta=.05, independent=True, sampling_frozen=True, stopping_frozen=True)
    values = [0.] * 500 + [.1] * 500
    import math
    expected = .05 + math.sqrt(2 * (2.5 / 999) * math.log(40) / 1000) + 7 * math.log(40) / (3 * 999)
    assert m.empirical_bernstein_upper(values, delta=.05, independent=True,
        sampling_frozen=True, stopping_frozen=True) == pytest.approx(expected)


def test_exact_global_null_bound_is_separate():
    assert api().exact_global_null_upper(0, 100, delta=.05) == pytest.approx(1 - .05 ** .01)
    assert api().exact_global_null_upper(100, 100, delta=.05) == 1
    # Independently tabulated exact one-sided Clopper-Pearson control.
    assert api().exact_global_null_upper(1, 10, delta=.05) == pytest.approx(.3941633024365047)
    assert api().exact_global_null_upper(1, 10, delta=.9) == pytest.approx(.0545286199976708)


def test_confidence_overspend_and_post_result_freeze_reject():
    p, _ = records()
    p["confidence"].update(global_delta=.05, allocations=[
        {"allocation_id": "a", "endpoint_id": "fdp", "family_id": "f", "stratum": "signal", "gate_id": "g", "delta": .04},
        {"allocation_id": "b", "endpoint_id": "fdp", "family_id": "f", "stratum": "signal", "gate_id": "g2", "delta": .04}])
    with pytest.raises(ValueError):
        api().validate_release_protocol(p)
    p, _ = records()
    p["freeze"].update(frozen_at="2026-11-14T12:00:00Z", first_result_at="2026-11-14T11:00:00Z")
    with pytest.raises(ValueError):
        api().validate_release_protocol(p)


def test_score_replay_is_deterministic_portable_and_fresh(tmp_path, monkeypatch):
    m = api()
    p, ledger = records()
    receipt = m.write_score_package(tmp_path / "package", protocol=p, ledger=ledger, workspace=tmp_path)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("score replay launched a process"))
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("score replay contacted network"))
    result = m.replay_release_scores(tmp_path / "package", receipt["root_sha256"],
                                     tmp_path / "replay", workspace=tmp_path)
    assert result["scores_sha256"] == receipt["scores_sha256"]
    assert result["model_calls"] == result["numerical_jobs"] == 0
    assert not any(result["qualification"].values())
    with pytest.raises(ValueError):
        m.replay_release_scores(tmp_path / "package", receipt["root_sha256"], tmp_path / "replay", workspace=tmp_path)


def test_score_replay_cannot_write_inside_its_original_package(tmp_path):
    p, ledger = records()
    m = api()
    package = tmp_path / "package"
    receipt = m.write_score_package(package, protocol=p, ledger=ledger, workspace=tmp_path)
    with pytest.raises(ValueError):
        m.replay_release_scores(package, receipt["root_sha256"], package / "nested-replay", workspace=tmp_path)
    assert not (package / "nested-replay").exists()


def test_partitions_cannot_be_silently_pooled_by_one_endpoint():
    p, ledger = records()
    p["studies"][1]["partition"] = "pilot"
    refresh(p, ledger)
    with pytest.raises(ValueError, match="scope|assignment"):
        api().account_release_campaign(p, ledger)


def test_conflicting_owner_and_oversized_reserve_records_raise():
    p, _ = records()
    for role in ("statistical", "runtime"):
        owner = next(o for o in p["owners"] if o["role"] == role)
        owner.update(identity=identity("same-person"), independent=True)
    with pytest.raises(ValueError, match="owner"):
        api().validate_release_protocol(p)
    p, _ = records()
    p["arms"][0]["total_budget"]["limits"]["tokens"] = 10
    p["arms"][0]["total_budget"]["reserves"]["tokens"] = 11
    with pytest.raises(ValueError, match="reserve"):
        api().validate_release_protocol(p)


def test_tuple_hypothesis_keys_cannot_collide_on_delimiters():
    p, ledger = records()
    p["family_allocations"] = [
        {"family_id": "f", "population_id": "bulk", "contrast_id": "a:b", "weight": .5, "required_primary": True},
        {"family_id": "f:a", "population_id": "bulk", "contrast_id": "b", "weight": .5, "required_primary": True}]
    p["endpoints"][0]["family_ids"] = ["f", "f:a"]
    ledger["attempts"][0]["hypotheses"] = [hypothesis("g", True, contrast_id="a:b"),
                                             hypothesis("g", False, family_id="f:a", contrast_id="b")]
    refresh(p, ledger)
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["mean_study_fdp"] == .25


def test_declared_endpoint_cannot_be_removed_from_assignments():
    p, ledger = records()
    p["endpoints"].append(endpoint(endpoint_id="second-required-endpoint"))
    refresh(p, ledger)
    with pytest.raises(ValueError, match="endpoint"):
        api().account_release_campaign(p, ledger)


def test_unestablished_outcome_adjudication_keeps_usefulness_unavailable():
    p, ledger = records()
    score = api().account_release_campaign(p, ledger)
    assert score["useful_completion"]["C"]["point"] is None
    assert score["useful_completion"]["C"]["numerator"] == 0
    assert score["useful_completion"]["C"]["unvalidated_declared_useful_answers"] == 2


def test_variant_lineage_cycles_are_invalid():
    p, _ = records()
    p["studies"][0]["variant_parent_ids"] = ["s2"]
    p["studies"][1]["variant_parent_ids"] = ["s1"]
    with pytest.raises(ValueError, match="lineage"):
        api().validate_release_protocol(p)


def test_replay_refuses_hardlinked_members_before_decoding(tmp_path, monkeypatch):
    import os
    m = api()
    p, ledger = records()
    package = tmp_path / "package"
    receipt = m.write_score_package(package, protocol=p, ledger=ledger, workspace=tmp_path)
    os.link(package / "manifest.json", tmp_path / "linked-manifest.json")
    original = m.decode_json
    def forbid_decode(raw):
        pytest.fail("hardlinked package metadata was decoded")
    monkeypatch.setattr(m, "decode_json", forbid_decode)
    with pytest.raises(ValueError):
        m.replay_release_scores(package, receipt["root_sha256"], tmp_path / "replay", workspace=tmp_path)
    monkeypatch.setattr(m, "decode_json", original)
    assert not (tmp_path / "replay").exists()


@pytest.mark.parametrize("script", ["run_rna_investigation_evaluation.py", "run_rna_scientific_trial.py"])
def test_v24_cli_score_and_replay_never_construct_legacy_owners(tmp_path, monkeypatch, capsys, script):
    import runpy
    p, ledger = records()
    for name, value in (("protocol", p), ("ledger", ledger)):
        (tmp_path / (name + ".json")).write_text(json.dumps(value))
    from devbench.analysis import methods, method_planner, rna_investigation_evaluation as legacy
    def forbidden(*args, **kwargs):
        pytest.fail("v2.4 score/replay constructed a legacy owner")
    monkeypatch.setattr(methods, "MethodService", forbidden)
    monkeypatch.setattr(method_planner, "ScientificMethodPlanner", forbidden)
    monkeypatch.setattr(legacy, "prepare_offline_example", forbidden)
    module = runpy.run_path(str(ROOT / "scripts" / script))
    assert module["main"](["--v24-score", "--workspace", str(tmp_path), "--protocol", str(tmp_path / "protocol.json"),
        "--ledger", str(tmp_path / "ledger.json"), "--output", str(tmp_path / "package")]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert module["main"](["--v24-replay", "--workspace", str(tmp_path), "--package", str(tmp_path / "package"),
        "--expected-v24-root", receipt["root_sha256"], "--output", str(tmp_path / "replay")]) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["scores_sha256"] == receipt["scores_sha256"] and not any(replay["qualification"].values())


def scoped_records(kind, denominator):
    p, ledger = records()
    p["studies"][1]["stratum"] = "unrelated"
    p["endpoints"] = [endpoint(endpoint_id="target", kind=kind, denominator=denominator),
                       endpoint(endpoint_id="unrelated-cost", kind="cost_burden", denominator="all_assigned_studies", stratum="unrelated")]
    ledger["assignments"][0]["endpoint_ids"] = ["target"]
    ledger["assignments"][1]["endpoint_ids"] = ["unrelated-cost"]
    for row in ledger["outcomes"]:
        row["independent"] = True
    ledger["outcomes"][0]["useful"] = False
    ledger["attempts"][0]["usage"]["tokens"] = 10
    ledger["attempts"][1]["usage"]["tokens"] = 90
    refresh(p, ledger)
    return p, ledger


def packet_row(name, assignment="s1-C", *, harmful=False, valid=True, erroneous=True):
    return {"packet_id": name, "assignment_id": assignment,
        "attempt_id": "s1-attempt" if assignment == "s1-C" else "s2-attempt", "touchpoint_id": name,
        "valid": valid, "consequential_error": not valid, "detected": not valid,
        "initial_hold": erroneous, "initial_hold_erroneous": erroneous and valid, "resolved": False,
        "harmful_closure": harmful, "adjudication": identity("synthetic-independent-labels"), "independent": True}


@pytest.mark.parametrize("kind,denominator", [
    ("review_detection", "eligible_corrupted_packets"), ("initial_false_hold", "assessed_valid_packets"),
    ("resolved_hold", "initial_false_holds"), ("harmful_closure", "review_touchpoints"),
    ("useful_completion", "all_assigned_studies"), ("terminal_false_block", "answerable_assigned_studies"),
    ("cost_burden", "all_assigned_studies"), ("paired_benefit", "independent_studies")])
def test_every_metric_family_uses_its_declared_endpoint_assignment_scope(kind, denominator):
    p, ledger = scoped_records(kind, denominator)
    ledger["review_packets"] = [packet_row("target-packet", harmful=True, valid=kind != "review_detection")]
    baseline = api().account_release_campaign(p, ledger)
    target = baseline["endpoint_scores"]["target"]
    assert target["assignment_ids"] == ["s1-C"] and target["study_clusters"] == 1
    assert set(target["arms"]) == {"C"}
    ledger["review_packets"].extend(packet_row("unrelated-" + str(i), "s2-C", erroneous=False) for i in range(9))
    ledger["outcomes"][1]["useful"] = False
    ledger["outcomes"][1]["harmful_closure"] = True
    ledger["attempts"][1]["usage"]["tokens"] = 999
    after = api().account_release_campaign(p, ledger)
    assert after["endpoint_scores"]["target"] == target
    assert after["overview_scope"] == "all_assigned_descriptive_context_not_declared_endpoint_scores"
    if kind == "initial_false_hold":
        assert target["arms"]["C"]["point"] == 1.
    if kind == "useful_completion":
        assert target["arms"]["C"]["denominator"] == 1 and target["arms"]["C"]["point"] == 0.
    if kind == "cost_burden":
        assert target["arms"]["C"]["usage"]["tokens"] == 10


def test_numerical_and_integrity_endpoint_rows_exclude_unrelated_assignments():
    p, ledger = scoped_records("numerical_agreement", "numerical_quantities")
    p["endpoints"][0]["required_quantities"] = ["logFC"]
    p["tolerances"] = [{"tolerance_id": "effect", "quantity": "logFC", "scale": "linear", "absolute": .001,
        "relative": .001, "near_zero": "absolute_only", "boundary": "compare", "nonfinite": "unavailable",
        "missing": "unavailable", "regimes": ["B-I"], "ratification": "unratified"}]
    ledger["numerical_records"] = [{"record_id": "n" + str(i), "assignment_id": "s" + str(i+1) + "-C", "quantity": "logFC",
        "observed": 1., "expected": 1., "tolerance_id": "effect", "observed_status": "finite", "reference_status": "finite",
        "reference_binding": identity("engineering-reference")} for i in range(2)]
    refresh(p, ledger)
    scoped = api().account_release_campaign(p, ledger)["endpoint_scores"]["target"]["arms"]["C"]
    assert scoped["expected_denominator"] == 1
    assert [r["assignment_id"] for r in scoped["quantities"]] == ["s1-C"]
    p, ledger = scoped_records("critical_integrity", "all_assigned_studies")
    ledger["integrity_records"] = [{"record_id": "i" + str(i), "assignment_id": "s" + str(i+1) + "-C", "case_id": "case",
        "opportunity_id": "opportunity", "critical_violation": False, "accepted": False,
        "adjudication": identity("engineering-labels"), "independent": True} for i in range(2)]
    scoped = api().account_release_campaign(p, ledger)["endpoint_scores"]["target"]["arms"]["C"]
    assert scoped["assigned_study_denominator"] == scoped["opportunity_denominator"] == scoped["case_denominator"] == 1


def test_x9_untagged_retry_keeps_costs_and_missing_actual_identity_in_scope():
    p, ledger = x9_records()
    p["endpoints"][0]["kind"] = "x9"
    refresh(p, ledger)
    original = ledger["attempts"][0]
    original["usage"]["tokens"] = 70
    retry = deepcopy(original)
    retry.update(attempt_id="unmatched-retry", retry_of=original["attempt_id"], x9=None)
    ledger["attempts"].append(retry)
    ledger["assignments"][0]["expected_attempt_ids"].append("unmatched-retry")
    ledger["outcomes"][0]["terminal_attempt_id"] = "unmatched-retry"
    scores = api().account_release_campaign(p, ledger)
    for x9 in (scores["x9"], scores["endpoint_scores"]["fdp"]["comparison"]):
        assert x9["status"] == "not_assessed"
        assert "actual_total_budget_exceeded_including_retries" in x9["blockers"]
        assert "unmatched-retry" in x9["missing_actual_attempt_ids"]
        assert x9["attempts_considered"] == 5


def test_unknown_harmful_touchpoints_count_once_and_never_become_zero_rate():
    p, ledger = scoped_records("harmful_closure", "review_touchpoints")
    row = packet_row("unknown", harmful=None)
    row.update(adjudication=None, independent=None)
    repeated = deepcopy(row)
    repeated["packet_id"] = "second-packet-same-touchpoint"
    ledger["review_packets"] = [row, repeated]
    score = api().account_release_campaign(p, ledger)
    for result in (score["review"]["harmful_closure_touchpoints"], score["endpoint_scores"]["target"]["arms"]["C"]):
        assert result["denominator"] == result["unknown"] == 1
        assert result["point"] is None


def test_nonindependent_usefulness_is_only_an_unvalidated_declaration():
    p, ledger = scoped_records("useful_completion", "all_assigned_studies")
    ledger["outcomes"][0].update(useful=True, independent=False)
    scores = api().account_release_campaign(p, ledger)
    result = scores["endpoint_scores"]["target"]["arms"]["C"]
    assert result["numerator"] == 0 and result["unknown"] == 1 and result["point"] is None
    assert result["unvalidated_declared_useful_answers"] == 1
    assert scores["costs"]["C"]["useful_answers"] == 1  # Only the independent unrelated assignment.
    assert scores["costs"]["C"]["unvalidated_declared_useful_answers"] == 1


def test_declared_evaluator_identity_matches_validation_package_score_and_replay(tmp_path):
    p, ledger = records()
    actual = api()._implementation()
    p["evaluator_source"] = {**actual, "sha256": "0" * 64}
    refresh(p, ledger)
    for operation in (lambda: api().validate_release_protocol(p), lambda: api().account_release_campaign(p, ledger),
                      lambda: api().write_score_package(tmp_path / "refused", protocol=p, ledger=ledger, workspace=tmp_path)):
        with pytest.raises(ValueError, match="evaluator"):
            operation()
    assert not (tmp_path / "refused").exists()
    p["evaluator_source"] = actual
    refresh(p, ledger)
    receipt = api().write_score_package(tmp_path / "package", protocol=p, ledger=ledger, workspace=tmp_path)
    score = json.loads((tmp_path / "package/scores.json").read_text())
    assert score["evaluator"] == actual
    assert api().replay_release_scores(tmp_path / "package", receipt["root_sha256"], tmp_path / "replay", workspace=tmp_path)["evaluator"] == actual


@pytest.mark.parametrize("script,mode", [("run_rna_investigation_evaluation.py", "--offline-example"), ("run_rna_scientific_trial.py", "--prepare")])
@pytest.mark.parametrize("release_only", ["--protocol", "--ledger", "--package", "--expected-v24-root"])
def test_any_release_only_intent_rejects_before_legacy_dispatch(tmp_path, monkeypatch, script, mode, release_only):
    import runpy
    from devbench.analysis import methods, method_planner, rna_investigation_evaluation as legacy
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("legacy")
        raise AssertionError("legacy work reached")
    monkeypatch.setattr(methods, "MethodService", forbidden)
    monkeypatch.setattr(method_planner, "ScientificMethodPlanner", forbidden)
    monkeypatch.setattr(legacy, "prepare_offline_example", forbidden)
    argv = [mode, "--workspace", str(tmp_path), release_only, "unread", "--output", str(tmp_path / "out")]
    if script == "run_rna_scientific_trial.py":
        argv += ["--method-workspace", str(tmp_path / "methods")]
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(ROOT / "scripts" / script))["main"](argv)
    assert error.value.code == 2 and calls == [] and not (tmp_path / "out").exists()


def test_confidence_gate_membership_is_checked_after_positive_setup():
    p, _ = records()
    p["endpoints"][0].update(bound_method="empirical_bernstein", confidence_allocation_id="a")
    p["endpoints"].append(endpoint(endpoint_id="other"))
    p["gates"] = [{"gate_id": "gate", "endpoint_ids": ["fdp"], "threshold": None, "direction": None,
        "owner_role": "statistical", "evidence": None, "ratification": "unratified"}]
    p["confidence"].update(global_delta=.05, allocations=[{"allocation_id": "a", "endpoint_id": "fdp", "family_id": "f",
        "stratum": "signal", "gate_id": "gate", "delta": .05}])
    assert api().validate_release_protocol(p)["admission"] == "blocked"
    p["gates"][0]["endpoint_ids"] = ["other"]
    with pytest.raises(ValueError, match="confidence"):
        api().validate_release_protocol(p)


def test_resealed_package_cannot_replay_a_contradictory_evaluator(tmp_path):
    m = api()
    p, ledger = records()
    p["evaluator_source"] = m._implementation()
    refresh(p, ledger)
    package = tmp_path / "package"
    m.write_score_package(package, protocol=p, ledger=ledger, workspace=tmp_path)
    p["evaluator_source"]["sha256"] = "0" * 64
    refresh(p, ledger)
    (package / "protocol.json").write_bytes(m.canonical_json_bytes(p))
    (package / "ledger.json").write_bytes(m.canonical_json_bytes(ledger))
    manifest = json.loads((package / "manifest.json").read_text())
    for row in manifest["members"]:
        raw = (package / row["name"]).read_bytes()
        row.update(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
    raw = m.canonical_json_bytes(manifest)
    (package / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="evaluator"):
        m.replay_release_scores(package, hashlib.sha256(raw).hexdigest(), tmp_path / "refused", workspace=tmp_path)
    assert not (tmp_path / "refused").exists()


def test_declared_repository_protocol_source_cannot_disagree_with_its_fixed_identity():
    p, _ = records()
    p["protocol_source"] = {"name": api().PROTOCOL_ID, "sha256": "0" * 64}
    with pytest.raises(ValueError, match="protocol_source"):
        api().validate_release_protocol(p)
    p["protocol_source"]["sha256"] = hashlib.sha256((ROOT / "docs/agentic-rna-v24-evaluation-protocol.md").read_bytes()).hexdigest()
    assert api().validate_release_protocol(p)["admission"] == "blocked"


def eligibility_records(kind):
    denominators = {"initial_false_hold": "assessed_valid_packets", "review_detection": "eligible_corrupted_packets",
                    "resolved_hold": "initial_false_holds", "terminal_false_block": "answerable_assigned_studies"}
    p, ledger = records()
    p["endpoints"] = [endpoint(endpoint_id="eligibility", kind=kind, denominator=denominators[kind])]
    for assignment in ledger["assignments"]:
        assignment["endpoint_ids"] = ["eligibility"]
    for i, outcome in enumerate(ledger["outcomes"]):
        outcome.update(useful=False, independent=True, answerable=i == 0 if kind == "terminal_false_block" else True)
    if kind != "terminal_false_block":
        for i in range(2):
            row = packet_row("p" + str(i), "s1-C" if i == 0 else "s2-C", erroneous=False)
            if kind == "initial_false_hold":
                row.update(valid=i == 0)
            elif kind == "review_detection":
                row.update(valid=False, consequential_error=i == 0, detected=i == 0)
            else:
                row.update(initial_hold=True, initial_hold_erroneous=i == 0, resolved=i == 0)
            ledger["review_packets"].append(row)
    refresh(p, ledger)
    return p, ledger


@pytest.mark.parametrize("kind", ["initial_false_hold", "review_detection", "resolved_hold", "terminal_false_block"])
@pytest.mark.parametrize("judgment", ["independent", "absent", "nonindependent"])
def test_eligibility_is_adjudicated_before_negative_rows_are_excluded(kind, judgment):
    p, ledger = eligibility_records(kind)
    baseline = api().account_release_campaign(p, ledger)
    known = baseline["endpoint_scores"]["eligibility"]["arms"]["C"]
    expected = 1. if kind in {"review_detection", "resolved_hold"} else 0.
    assert known["point"] == expected and known["denominator"] == 1
    row = ledger["outcomes"][1] if kind == "terminal_false_block" else ledger["review_packets"][1]
    if judgment == "absent":
        row.update(adjudication=None, independent=None)
    elif judgment == "nonindependent":
        row["independent"] = False
    result = api().account_release_campaign(p, ledger)
    rate = result["endpoint_scores"]["eligibility"]["arms"]["C"]
    assert rate["denominator"] == 1 and rate["scope_denominator"] == 2
    assert result["assigned_studies"] == result["costs"]["C"]["assigned_studies"] == 2
    assert result["usage"] == baseline["usage"]
    if judgment == "independent":
        assert rate["point"] == expected and rate["unknown"] == 0 and rate["known_ineligible"] == 1
    else:
        assert rate["point"] is None and rate["unknown"] == rate["unknown_eligibility"] == 1
        assert rate["known_ineligible"] == 0
        if kind == "terminal_false_block":
            assert rate["unknown_answerability_studies"] == 1


@pytest.mark.parametrize("observed_hold", [False, None])
def test_resolution_eligibility_preserves_observed_no_hold_without_inventing_judgment(observed_hold):
    p, ledger = eligibility_records("resolved_hold")
    row = ledger["review_packets"][1]
    row.update(initial_hold=observed_hold, initial_hold_erroneous=None, valid=None, adjudication=None, independent=None)
    rate = api().account_release_campaign(p, ledger)["endpoint_scores"]["eligibility"]["arms"]["C"]
    assert rate["denominator"] == 1 and rate["scope_denominator"] == 2
    if observed_hold is False:
        assert rate["point"] == 1. and rate["unknown"] == 0 and rate["known_ineligible"] == 1
    else:
        assert rate["point"] is None and rate["unknown_eligibility"] == 1


def test_repeated_study_views_keep_unknown_answerability_once_without_losing_costs():
    p, ledger = eligibility_records("terminal_false_block")
    p["studies"][1].update(underlying_study_id="s1", biological_source_ids=p["studies"][0]["biological_source_ids"])
    ledger["outcomes"][1].update(adjudication=None, independent=None)
    ledger["attempts"][0]["usage"]["tokens"] = 10
    ledger["attempts"][1]["usage"]["tokens"] = 20
    refresh(p, ledger)
    result = api().account_release_campaign(p, ledger)
    rate = result["endpoint_scores"]["eligibility"]["arms"]["C"]
    assert rate["scope_denominator"] == rate["unknown_eligibility"] == rate["unknown_answerability_studies"] == 1
    assert rate["denominator"] == 0 and rate["point"] is None
    assert result["costs"]["C"]["usage"]["tokens"] == 30 and result["assignments"] == 2


@pytest.mark.parametrize("field", ["contrast_id", "population_id"])
@pytest.mark.parametrize("exposure", ["retained", "withdrawn", "unexposed"])
def test_hypothesis_tuple_must_match_its_frozen_family_before_accounting(field, exposure):
    p, ledger = records()
    ledger["attempts"][0]["hypotheses"] = [hypothesis("false", True)]
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["mean_study_fdp"] == .5
    flags = {} if exposure == "retained" else {"retained": False, "withdrawn": exposure == "withdrawn", "exposed": exposure == "withdrawn"}
    ledger["attempts"][0]["hypotheses"].extend(hypothesis("true-" + str(i), False, **{field: "never-frozen"}, **flags) for i in range(99))
    before = deepcopy(ledger)
    with pytest.raises(ValueError, match="hypothesis_frozen_family_binding_mismatch"):
        api().account_release_campaign(p, ledger)
    assert ledger == before


def test_duplicate_family_alias_cannot_dilute_fdp_but_distinct_frozen_pair_can():
    p, ledger = records()
    ledger["attempts"][0]["hypotheses"] = [hypothesis("false", True), hypothesis("true", False)]
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["mean_study_fdp"] == .25
    p["family_allocations"][0]["weight"] = .5
    p["family_allocations"].append({**p["family_allocations"][0], "family_id": "alias"})
    p["endpoints"][0]["family_ids"].append("alias")
    ledger["attempts"][0]["hypotheses"].append(hypothesis("true", False, family_id="alias"))
    refresh(p, ledger)
    with pytest.raises(ValueError, match="duplicate_frozen_population_contrast_family"):
        api().account_release_campaign(p, ledger)
    p["family_allocations"][1]["contrast_id"] = "different-frozen-contrast"
    ledger["attempts"][0]["hypotheses"][-1]["contrast_id"] = "different-frozen-contrast"
    refresh(p, ledger)
    assert api().account_release_campaign(p, ledger)["fdp"]["fdp"]["C"]["mean_study_fdp"] == pytest.approx(1 / 6)


def numerical_boundary_records(scale, boundary, near_zero):
    p, ledger = records()
    p["endpoints"][0].update(kind="numerical_agreement", denominator="numerical_quantities", required_quantities=["normalization_factor"])
    p["tolerances"] = [{"tolerance_id": "norm", "quantity": "normalization_factor", "scale": scale,
        "absolute": 1e-9, "relative": 1e-9, "near_zero": near_zero, "boundary": boundary,
        "nonfinite": "unavailable", "missing": "unavailable", "regimes": ["B-I"], "ratification": "ratified"}]
    ledger["numerical_records"] = [{"record_id": "normalization", "assignment_id": "s1-C", "quantity": "normalization_factor",
        "observed": 2., "expected": 2., "tolerance_id": "norm", "observed_status": "finite", "reference_status": "finite",
        "reference_binding": identity("explicit-synthetic-reference-control")}]
    refresh(p, ledger)
    return p, ledger


@pytest.mark.parametrize("observed,expected,scale,boundary,near_zero,agreement", [
    (2., 2., "log", "unavailable", "absolute_only", True),
    (1., 1., "log", "unavailable", "absolute_only", True),
    (1., 1.001, "log", "unavailable", "absolute_only", False),
    (.5, .5, "log", "unavailable", "absolute_only", True),
    (1., 1., "log", "unavailable", "unavailable", True),
    (0., 0., "log", "compare", "absolute_only", None),
    (-1., -1., "log", "compare", "absolute_only", None),
    (0., 0., "linear", "compare", "absolute_only", True),
    (0., 1e-6, "linear", "compare", "absolute_only", False),
    (0., 0., "linear", "compare", "unavailable", None),
    (0., 0., "linear", "unavailable", "absolute_only", None),
    (-2., -2., "linear", "unavailable", "absolute_only", True),
    (-2., -3., "linear", "unavailable", "absolute_only", False),
])
def test_numerical_domain_checks_use_raw_values_before_log_transform(observed, expected, scale, boundary, near_zero, agreement):
    p, ledger = numerical_boundary_records(scale, boundary, near_zero)
    ledger["numerical_records"][0].update(observed=observed, expected=expected)
    row = api().account_release_campaign(p, ledger)["endpoint_scores"]["fdp"]["arms"]["C"]["quantities"][0]
    assert row["agreement"] is agreement
    assert ledger["numerical_records"][0]["observed"] == observed


@pytest.mark.parametrize("status", ["missing", "failed", "nonfinite"])
def test_log_comparison_preserves_missing_and_failed_estimates(status):
    p, ledger = numerical_boundary_records("log", "compare", "absolute_only")
    ledger["numerical_records"][0].update(observed=None, observed_status=status)
    metric = api().account_release_campaign(p, ledger)["endpoint_scores"]["fdp"]["arms"]["C"]
    assert metric["quantities"][0]["agreement"] is None
    assert ledger["numerical_records"][0]["observed"] is None


@pytest.mark.parametrize("answerable", [True, False, None])
def test_related_view_cannot_erase_a_confirmed_false_block_or_inflate_independence(answerable):
    p, ledger = eligibility_records("terminal_false_block")
    ledger["outcomes"][1]["answerable"] = True
    ledger["outcomes"][0]["terminal_false_block"] = True
    baseline = api().account_release_campaign(p, ledger)["endpoint_scores"]["eligibility"]["arms"]["C"]
    assert baseline["numerator"] == 1 and baseline["denominator"] == 2 and baseline["point"] == .5
    variant = {**deepcopy(p["studies"][0]), "study_id": "s1-view", "variant_parent_ids": ["s1"]}
    p["studies"].append(variant)
    ledger["assignments"].append({**deepcopy(ledger["assignments"][0]), "assignment_id": "s1-view-C", "study_id": "s1-view",
                                 "expected_attempt_ids": ["s1-view-attempt"]})
    ledger["attempts"].append({**deepcopy(ledger["attempts"][0]), "assignment_id": "s1-view-C", "attempt_id": "s1-view-attempt",
                              "invocation": identity("s1-view-attempt")})
    ledger["outcomes"].append({**deepcopy(ledger["outcomes"][0]), "assignment_id": "s1-view-C", "terminal_attempt_id": "s1-view-attempt",
                              "answerable": answerable, "terminal_false_block": False})
    for i, attempt in enumerate(ledger["attempts"]):
        attempt["usage"]["tokens"] = 10 * (i + 1)
    refresh(p, ledger)
    result = api().account_release_campaign(p, ledger)
    metric = result["endpoint_scores"]["eligibility"]["arms"]["C"]
    assert result["assigned_studies"] == metric["scope_denominator"] == 2 and result["assignments"] == 3
    assert result["costs"]["C"]["usage"]["tokens"] == 60
    assert metric["confirmed_false_block_assignments"] == ["s1-C"] and metric["confirmed_false_block_studies"] == 1
    if answerable is True:
        assert metric["numerator"] == 1 and metric["denominator"] == 2 and metric["point"] == .5
    else:
        assert metric["point"] is None and metric["unknown_eligibility"] == 1
        if answerable is False:
            assert "mixed_view_answerability_policy_unratified" in metric["unavailable_reasons"]


@pytest.mark.parametrize("mutation", ["bytes", "extra", "symlink", "traversal", "duplicate_json"])
def test_replay_rejects_unsafe_or_changed_members_before_output(tmp_path, mutation):
    m = api()
    p, ledger = records()
    package = tmp_path / "package"
    receipt = m.write_score_package(package, protocol=p, ledger=ledger, workspace=tmp_path)
    if mutation == "bytes":
        (package / "ledger.json").write_text("{}")
    elif mutation == "extra":
        (package / "hidden-answer.json").write_text("{}")
    elif mutation == "symlink":
        (package / "ledger.json").unlink()
        (package / "ledger.json").symlink_to(package / "protocol.json")
    else:
        path = package / "manifest.json"
        document = json.loads(path.read_text())
        if mutation == "traversal":
            document["members"][0]["name"] = "../secret"
            raw = json.dumps(document).encode()
        else:
            raw = b'{"format":"first","format":"second"}'
        path.write_bytes(raw)
        receipt["root_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        m.replay_release_scores(package, receipt["root_sha256"], tmp_path / "refused", workspace=tmp_path)
    assert not (tmp_path / "refused").exists()


@pytest.mark.parametrize("script", ["run_rna_investigation_evaluation.py", "run_rna_scientific_trial.py"])
def test_v24_cli_validation_precedes_legacy_owners(tmp_path, monkeypatch, capsys, script):
    m = api()
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text(json.dumps(m.unratified_protocol()))
    import runpy
    module = runpy.run_path(str(ROOT / "scripts" / script))
    from devbench.analysis import methods, method_planner, rna_investigation_evaluation
    def forbidden(*a, **k):
        pytest.fail("v2.4 validation constructed a legacy runtime or campaign")
    monkeypatch.setattr(methods, "MethodService", forbidden)
    monkeypatch.setattr(method_planner, "ScientificMethodPlanner", forbidden)
    monkeypatch.setattr(rna_investigation_evaluation, "prepare_offline_example", forbidden)
    monkeypatch.setattr(rna_investigation_evaluation, "run_offline_campaign", forbidden)
    assert module["main"](["--v24-validate", "--workspace", str(tmp_path), "--protocol", str(protocol_path)]) == 2
    assert json.loads(capsys.readouterr().out)["admission"] == "blocked"
