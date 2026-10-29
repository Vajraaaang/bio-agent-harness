"""Three-arm local software controls, never native or scientific qualification.

The independent arithmetic checks below are newly authored public fixtures. They
do not open existing task definitions, protected references, or grading inputs.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

import pytest


ARMS = ("checked_workflow", "analyst_same_tools_checks", "analyst_plus_reviewer")
ENDPOINTS = ("numeric_correctness", "design_validity", "appropriate_refusal", "claim_support", "replay_validity")
CALLS = []


def api():
    name = "devbench.analysis.rna_workflow_evaluation"
    assert importlib.util.find_spec(name) is not None, "Additive three-arm workflow evaluator is not implemented"
    return __import__(name, fromlist=["prepare_comparison"])


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fixture_difference(data):
    return statistics.mean(data["right"]) - statistics.mean(data["left"])


def response(output, *, status="completed", tokens=0, cost=0.0):
    return {"status": status, "output": output,
        "usage": {"tokens": tokens, "cost_usd": cost, "elapsed_seconds": 0.01},
        "interventions": []}


def checked_driver(request):
    CALLS.append(deepcopy(request))
    return response({"estimate": fixture_difference(request["case"]["public_data"]),
        "claim": "Right minus left; local arithmetic fixture only."})


def analyst_driver(request):
    CALLS.append(deepcopy(request))
    data = request["case"]["public_data"]
    value = fixture_difference(data)
    if request["phase"] == "initial" and data["mode"] in {"correctable", "unresolved"}:
        value += 10
    if request["phase"] == "revision":
        value = request["feedback"]["suggested_estimate"]
    result = response({"estimate": value, "claim": "Right minus left; local arithmetic fixture only."}, tokens=13)
    if data.get("unknown_cost"):
        result["usage"]["cost_usd"] = None
    if data.get("unknown_tokens"):
        result["usage"]["tokens"] = None
    if data.get("intervention"):
        result["interventions"] = [{"kind": "operator_note", "text": "Fixture correction was declared."}]
    return result


def reviewer_driver(request):
    CALLS.append(deepcopy(request))
    data = request["case"]["public_data"]
    value = fixture_difference(data)
    if data["mode"] in {"introduced", "unresolved"}:
        value += 10
    return response({"suggested_estimate": value, "reason": "Synthetic reviewer feedback."}, tokens=7)


def failing_driver(request):
    CALLS.append(deepcopy(request))
    raise RuntimeError("deliberate local driver failure")


def malformed_usage_driver(request):
    result = checked_driver(request)
    result["usage"]["cost_usd"] = -1
    return result


def retry_once_driver(request):
    CALLS.append(deepcopy(request))
    if request["driver_attempt"] == 1:
        result = response(None, status="failed", tokens=5, cost=0.01)
        result.update(error_type="TransientFixtureFailure", retryable=True)
        return result
    return response({"estimate": fixture_difference(request["case"]["public_data"]),
        "claim": "Right minus left; local arithmetic fixture only."}, tokens=2, cost=0.02)


def protocol():
    return {"format": "devbench.rna-workflow-comparison-protocol/2", "scope": "software_fixture",
        "arms": list(ARMS), "splits": ["development", "calibration", "locked_test"],
        "replicates": 2, "ordering_seed": 417, "seed_supported": True,
        "identities": {"tools": {"difference": digest("fixture-difference/1")},
            "checks": {"finite_estimate": digest("finite-estimate/1")},
            "evidence": {"source": digest("public-arithmetic-fixtures/1")},
            "model": {"name": "no-provider-fixture", "version": "1"},
            "memory": {"version": "empty-fixture-memory/1", "sha256": digest([])},
            "resources": {"max_calls_per_attempt": 4, "max_driver_attempts": 2,
                "max_context_bytes": 64000, "max_output_bytes": 64000,
                "max_elapsed_seconds": 30, "max_cost_usd": 1.0}},
        "stop_on_unknown_usage": True,
        "endpoints": list(ENDPOINTS), "primary_endpoint": "numeric_correctness",
        "endpoint_scope": "coordinator_checked_software_fixture_only",
        "coordinator_checks": {name: api().driver_identity(checked_outcomes) for name in ENDPOINTS}}


def cases():
    result = []
    for index, (split, mode) in enumerate(zip(("development", "calibration", "locked_test"),
            ("correctable", "introduced", "unresolved"))):
        result.append({"case_id": "fixture-" + str(index), "dataset_id": "dataset-" + str(index),
            "family_id": "family-" + str(index), "cohort_ids": ["cohort-" + str(index)],
            "donor_ids": ["donor-" + str(index)], "provenance_ids": ["source-" + str(index)],
            "split": split, "public_data": {"left": [1, 2, 3], "right": [4, 5, 6], "mode": mode}})
    return result


@pytest.fixture(autouse=True)
def reset_calls():
    CALLS.clear()


def identities(module, drivers=None):
    drivers = drivers or (checked_driver, analyst_driver, reviewer_driver)
    return {name: module.driver_identity(driver) for name, driver in
        zip(("deterministic", "analyst", "reviewer"), drivers)}


def prepare(tmp_path, *, spec=None, data=None, drivers=None):
    module = api()
    root = tmp_path / "comparison"
    prepared = module.prepare_comparison(root, protocol=spec or protocol(),
        cases=data or cases(), driver_identities=identities(module, drivers))
    return module, root, prepared


def authorization():
    return {"scope": "local_fixture", "allow_model_execution": False,
        "allow_native_execution": False, "allow_paid_execution": False}


def run(module, prepared, *, drivers=None, permission=None):
    deterministic, analyst, reviewer = drivers or (checked_driver, analyst_driver, reviewer_driver)
    return module.run_comparison(prepared, deterministic_driver=deterministic,
        analyst_driver=analyst, reviewer_driver=reviewer,
        authorization=permission if permission is not None else authorization())


def checked_outcomes(receipt):
    """New local coordinator checks, separate from driver or reviewer verdicts."""
    records = []
    for attempt in receipt["attempts"]:
        initial = attempt.get("initial_output")
        final = attempt.get("final_output")
        initial_errors = ["wrong_estimate"] if initial is not None and initial["estimate"] != 3 else []
        final_errors = ["wrong_estimate"] if final is not None and final["estimate"] != 3 else []
        measured = final is not None and attempt["status"] == "completed"
        endpoints = {name: {"status": "not_assessed", "value": None,
            "reason": "Not independently assessed by this arithmetic fixture."} for name in ENDPOINTS}
        if measured:
            endpoints["numeric_correctness"] = {"status": "assessed", "value": not final_errors,
                "evidence_sha256": digest({"expected": 3, "observed": final["estimate"]})}
            endpoints["claim_support"] = {"status": "assessed", "value": not final_errors,
                "evidence_sha256": digest(final)}
        records.append({"attempt_id": attempt["attempt_id"], "attempt_sha256": attempt["attempt_sha256"],
            "scope": "coordinator_checked_software_fixture_only", "endpoints": endpoints,
            "initial_errors": initial_errors if initial is not None else None,
            "final_errors": final_errors if final is not None else None,
            "check_source_sha256": api().driver_identity(checked_outcomes)["source_sha256"]})
    return records


def complete(tmp_path, *, spec=None, data=None, drivers=None):
    module, root, prepared = prepare(tmp_path, spec=spec, data=data, drivers=drivers)
    receipt = run(module, prepared, drivers=drivers)
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=checked_outcomes(receipt))
    return module, root, prepared, receipt, report


def test_three_arm_fixture_prepare_run_summarize_and_readonly_replay(tmp_path):
    module, root, _, receipt, report = complete(tmp_path)
    assert report["scope"] == "software_fixture"
    assert report["scientific_improvement_claim"] is False
    assert report["denominator"] == 18
    assert set(report["arms"]) == set(ARMS)
    assert len(receipt["attempts"]) == 18
    assert CALLS and {item["phase"] for item in CALLS} >= {"workflow", "initial", "review", "revision"}
    before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    CALLS.clear()
    replay = module.replay_comparison(root, report["report_seal_sha256"])
    after = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert replay["valid"] is True
    assert replay["report_seal_sha256"] == report["report_seal_sha256"]
    assert after == before
    assert CALLS == []


def test_paired_schedule_has_identical_seed_and_replication_across_arms(tmp_path):
    _, _, _, receipt, _ = complete(tmp_path)
    grouped = {}
    for attempt in receipt["attempts"]:
        grouped.setdefault((attempt["case_id"], attempt["replicate"]), []).append(attempt)
    assert len(grouped) == 6
    for group in grouped.values():
        assert {item["arm"] for item in group} == set(ARMS)
        assert len({item["seed"] for item in group}) == 1
        assert type(group[0]["seed"]) is int
    assert len({tuple(item["arm"] for item in group) for group in grouped.values()}) > 1


def test_analyst_tools_checks_and_evidence_are_equal_between_analyst_arms(tmp_path):
    complete(tmp_path)
    initials = [call for call in CALLS if call["phase"] == "initial"]
    assert {call["arm"] for call in initials} == set(ARMS[1:])
    for call in initials:
        assert call["identities"] == protocol()["identities"]
        assert "checked_outcomes" not in call and "reference" not in call
        assert "feedback" not in call


def test_reviewed_revision_retains_initial_feedback_final_and_binds_each(tmp_path):
    _, _, _, receipt, _ = complete(tmp_path)
    for attempt in receipt["attempts"]:
        if attempt["arm"] != "analyst_plus_reviewer":
            assert attempt.get("feedback") is None
            continue
        assert attempt["initial_sha256"] == digest(attempt["initial_output"])
        assert attempt["feedback_sha256"] == digest(attempt["feedback"])
        assert attempt["final_sha256"] == digest(attempt["final_output"])
        review = next(call for call in attempt["calls"] if call["phase"] == "review")
        revision = next(call for call in attempt["calls"] if call["phase"] == "revision")
        assert review["request"]["initial_output"] == attempt["initial_output"]
        assert revision["request"]["feedback"] == attempt["feedback"]


def test_coordinator_checked_errors_distinguish_help_harm_and_unresolved(tmp_path):
    _, _, _, _, report = complete(tmp_path)
    reviewed = report["arms"]["analyst_plus_reviewer"]
    assert reviewed["errors"] == {"corrected": 2, "introduced": 2, "unresolved": 2, "not_assessed": 0}
    assert reviewed["completion"]["completed"] == 6
    assert reviewed["endpoints"]["numeric_correctness"]["passed"] == 2
    assert reviewed["endpoints"]["numeric_correctness"]["failed"] == 4
    for endpoint in ("design_validity", "appropriate_refusal", "replay_validity"):
        assert reviewed["endpoints"][endpoint]["assessed"] == 0
        assert reviewed["endpoints"][endpoint]["not_assessed"] == 6


@pytest.mark.parametrize("relation", ["dataset_id", "family_id", "cohort_ids", "donor_ids", "provenance_ids"])
def test_related_dataset_views_cannot_cross_splits_before_any_output(tmp_path, relation):
    module = api()
    data = cases()
    data[2][relation] = deepcopy(data[0][relation])
    root = tmp_path / "comparison"
    with pytest.raises(ValueError, match="split|related|leak"):
        module.prepare_comparison(root, protocol=protocol(), cases=data, driver_identities=identities(module))
    assert not root.exists()
    assert CALLS == []


def test_protocol_mutation_after_preparation_cannot_change_executed_identity(tmp_path):
    spec = protocol()
    module, _, prepared = prepare(tmp_path, spec=spec)
    spec["identities"]["checks"].clear()
    receipt = run(module, prepared)
    assert all(call["identities"]["checks"] == protocol()["identities"]["checks"] for call in CALLS)
    assert len(receipt["attempts"]) == 18


def test_driver_source_identity_cannot_be_replaced_after_freeze(tmp_path):
    module, _, prepared = prepare(tmp_path)
    with pytest.raises((PermissionError, ValueError), match="identity|driver|source"):
        run(module, prepared, drivers=(checked_driver, failing_driver, reviewer_driver))
    assert CALLS == []


def test_trusted_fixture_driver_must_be_source_backed_not_serialized_or_closure(tmp_path):
    module = api()
    for driver in ({"function": "checked_driver", "authorized": True}, lambda request: checked_driver(request)):
        with pytest.raises((TypeError, ValueError, PermissionError)):
            module.driver_identity(driver)
    assert CALLS == []


@pytest.mark.parametrize("field", ["allow_model_execution", "allow_native_execution", "allow_paid_execution"])
def test_declarative_model_native_or_paid_authority_cannot_dispatch(tmp_path, field):
    module, _, prepared = prepare(tmp_path)
    permission = authorization()
    permission[field] = True
    with pytest.raises(PermissionError, match="fixture|native|model|paid|authority"):
        run(module, prepared, permission=permission)
    assert CALLS == []


def test_claimed_native_scope_is_rejected_during_preparation(tmp_path):
    spec = protocol()
    spec["scope"] = "native_research"
    module = api()
    with pytest.raises(PermissionError, match="native|fixture|scope"):
        module.prepare_comparison(tmp_path / "comparison", protocol=spec, cases=cases(), driver_identities=identities(module))
    assert CALLS == []


def test_failed_driver_attempts_and_full_scheduled_denominator_are_retained(tmp_path):
    drivers = (checked_driver, failing_driver, reviewer_driver)
    _, _, _, receipt, report = complete(tmp_path, drivers=drivers)
    assert report["denominator"] == 18
    failed = [attempt for attempt in receipt["attempts"] if attempt["status"] == "failed"]
    assert failed
    for attempt in failed:
        assert attempt["calls"]
        assert any(call["error_type"] == "RuntimeError" for call in attempt["calls"])
        assert attempt["final_output"] is None
    assert report["unknown_usage_attempts"] > 0
    assert report["statuses"]["blocked"] > 0


@pytest.mark.parametrize("dimension", ["cost", "tokens"])
def test_unknown_usage_stops_further_dispatch_without_erasing_known_dimensions(tmp_path, dimension):
    data = cases()
    for item in data:
        item["public_data"]["unknown_" + dimension] = True
    _, _, _, receipt, report = complete(tmp_path, data=data)
    assert report["denominator"] == 18
    assert len(receipt["attempts"]) == 18
    assert report["unknown_usage_attempts"] > 0
    assert report["statuses"]["blocked"] > 0
    unknown = next(attempt for attempt in receipt["attempts"] if attempt["usage"]["complete"] is False)
    if dimension == "cost":
        assert unknown["usage"]["cost_usd"] is None
        assert unknown["usage"]["tokens"] is not None
    else:
        assert unknown["usage"]["tokens"] is None
        assert unknown["usage"]["cost_usd"] is not None
    actual_calls = sum(len(attempt["calls"]) for attempt in receipt["attempts"])
    assert len(CALLS) == actual_calls
    assert report["usage"]["request_attempts"] == actual_calls


def test_interventions_and_every_role_are_included_in_usage(tmp_path):
    data = cases()
    for item in data:
        item["public_data"]["intervention"] = True
    _, _, _, receipt, report = complete(tmp_path, data=data)
    assert report["usage"]["request_attempts"] == 30
    assert report["usage"]["tokens"] == 276
    assert report["usage"]["cost_usd"] == 0
    assert report["intervention_count"] == 18
    assert sum(len(call["interventions"]) for attempt in receipt["attempts"] for call in attempt["calls"]) == 18


def test_known_failed_retry_is_retained_and_included_in_total_consumption(tmp_path):
    drivers = (retry_once_driver, analyst_driver, reviewer_driver)
    _, _, _, receipt, report = complete(tmp_path, drivers=drivers)
    baseline = [attempt for attempt in receipt["attempts"] if attempt["arm"] == "checked_workflow"]
    assert len(baseline) == 6
    for attempt in baseline:
        assert attempt["status"] == "completed"
        assert [call["status"] for call in attempt["calls"]] == ["failed", "completed"]
        assert [call["request"]["driver_attempt"] for call in attempt["calls"]] == [1, 2]
        assert attempt["usage"]["tokens"] == 7
        assert attempt["usage"]["cost_usd"] == pytest.approx(0.03)
    assert report["usage"]["request_attempts"] == 36
    assert report["usage"]["tokens"] == 318
    assert report["usage"]["cost_usd"] == pytest.approx(0.18)


def test_invalid_usage_is_a_retained_failure_not_negative_or_zero_cost(tmp_path):
    drivers = (malformed_usage_driver, analyst_driver, reviewer_driver)
    _, _, _, receipt, report = complete(tmp_path, drivers=drivers)
    invalid = [call for attempt in receipt["attempts"] for call in attempt["calls"] if call.get("invalid_usage")]
    assert invalid
    assert report["usage"]["cost_usd"] is None
    assert any(call["raw_response"]["usage"]["cost_usd"] == -1 for call in invalid)


def test_unbound_coordinator_outcome_cannot_supply_success(tmp_path):
    module, _, prepared = prepare(tmp_path)
    receipt = run(module, prepared)
    outcomes = checked_outcomes(receipt)
    outcomes[0]["attempt_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="outcome|binding|attempt|hash"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=outcomes)


def test_missing_endpoint_checks_are_not_promoted_from_execution_completion(tmp_path):
    module, _, prepared = prepare(tmp_path)
    receipt = run(module, prepared)
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=[])
    assert report["statuses"]["completed"] == 18
    for arm in report["arms"].values():
        for endpoint in ENDPOINTS:
            assert arm["endpoints"][endpoint]["assessed"] == 0
            assert arm["endpoints"][endpoint]["not_assessed"] == 6
    assert report["scientific_improvement_claim"] is False


def test_reviewer_cannot_self_certify_a_checked_outcome(tmp_path):
    module, _, prepared = prepare(tmp_path)
    receipt = run(module, prepared)
    outcomes = checked_outcomes(receipt)
    outcomes[0]["scope"] = "reviewer_self_report"
    with pytest.raises((PermissionError, ValueError), match="scope|coordinator|checked"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=outcomes)


def test_mutating_retained_artifact_invalidates_readonly_replay(tmp_path):
    module, root, _, _, report = complete(tmp_path)
    manifest = root / "manifest.json"
    assert manifest.is_file()
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ValueError, match="changed|hash|seal|identity"):
        module.replay_comparison(root, report["report_seal_sha256"])


def test_wrong_external_report_seal_cannot_validate_a_copy(tmp_path):
    module, root, _, _, _ = complete(tmp_path)
    with pytest.raises(ValueError, match="seal|hash|identity"):
        module.replay_comparison(root, "0" * 64)


def test_campaign_cannot_be_reexecuted_or_preparation_overwritten(tmp_path):
    module, root, prepared, _, _ = complete(tmp_path)
    call_count = len(CALLS)
    with pytest.raises((FileExistsError, ValueError), match="exist|sealed|preserv|run"):
        run(module, prepared)
    with pytest.raises(FileExistsError):
        module.prepare_comparison(root, protocol=protocol(), cases=cases(), driver_identities=identities(module))
    assert len(CALLS) == call_count


def test_relative_output_path_is_rejected_before_execution():
    module = api()
    with pytest.raises(ValueError, match="absolute"):
        module.prepare_comparison(Path("relative-comparison"), protocol=protocol(), cases=cases(),
            driver_identities=identities(module))
    assert CALLS == []
