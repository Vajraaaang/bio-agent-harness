"""Public, newly authored scoring controls; no private benchmark material."""
from copy import deepcopy
import hashlib
import importlib
import json

import pytest


def _api():
    try:
        return importlib.import_module("devbench.analysis.rna_benchmark_contract")
    except ModuleNotFoundError as error:
        if error.name != "devbench.analysis.rna_benchmark_contract":
            raise
        pytest.fail("The frozen RNA benchmark contract is not implemented")


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _spec():
    # These values are transparent contract controls, not an observed trial.
    spec = {
        "format": "devbench.rna-benchmark/1", "benchmark_id": "public-control",
        "candidate": "candidate",
        "arms": {name: {"revision": "test-v1", "source_sha256": digit * 64,
                        "environment_sha256": "e" * 64, "configuration_sha256": "f" * 64}
                 for name, digit in (("candidate", "1"), ("latchbio", "2"), ("clawbio", "3"))},
        "policy": {"alpha": 0.05, "effect_atol": 0.2, "effect_rtol": 0.0,
                   "probability_atol": 1e-8, "probability_rtol": 1e-6,
                   "maximum_observed_fdp": 0.1, "minimum_observed_power": 0.9,
                   "maximum_effect_rmse": 0.2, "minimum_truth_cases": 1,
                   "minimum_invalid_cases": 1},
        "cases": [{
            "case_id": "batch-valid", "kind": "valid_design",
            "inputs": {"measurements_sha256": "a" * 64, "metadata_sha256": "b" * 64,
                       "reference_sha256": "c" * 64, "sample_order_sha256": "d" * 64},
            "formula": "~ batch + condition",
            "contrast": {"factor": "condition", "numerator": "treated", "denominator": "control"},
            "biological_unit": "donor_id", "gene_ids": ["g1", "g2", "g3"],
            "failed_hypothesis_policy": "exclude_failed_hypotheses",
            "reference": {"kind": "synthetic_truth", "source_sha256": "4" * 64,
                          "evidence_sha256": "5" * 64,
                          "rows": [{"gene_id": "g1", "effect": 2.0, "differential": True},
                                   {"gene_id": "g2", "effect": 0.0, "differential": False},
                                   {"gene_id": "g3", "effect": None, "differential": None}]},
        }, {
            "case_id": "batch-confounded", "kind": "invalid_design",
            "inputs": {"measurements_sha256": "6" * 64, "metadata_sha256": "7" * 64,
                       "reference_sha256": "c" * 64, "sample_order_sha256": "d" * 64},
            "formula": "~ batch + condition",
            "contrast": {"factor": "condition", "numerator": "treated", "denominator": "control"},
            "biological_unit": "donor_id", "gene_ids": ["g1", "g2", "g3"],
            "failed_hypothesis_policy": "exclude_failed_hypotheses",
            "reference": {"kind": "invalid_design", "source_sha256": "8" * 64,
                          "evidence_sha256": "9" * 64,
                          "required_reasons": ["condition_batch_confounding"]},
        }],
    }
    for case in spec["cases"]:
        reference = case["reference"]
        reference["evidence_sha256"] = _sha({key: value for key, value in reference.items()
                                             if key != "evidence_sha256"})
    return spec


def _rows():
    return [{"gene_id": "g1", "effect": 2.0, "p_value": 0.001, "q_value": 0.002,
             "missing_reason": None},
            {"gene_id": "g2", "effect": 0.0, "p_value": 0.7, "q_value": 0.7,
             "missing_reason": None},
            {"gene_id": "g3", "effect": None, "p_value": None, "q_value": None,
             "missing_reason": "all_zero_gene"}]


def _submissions(contract, *, candidate_only=False):
    evidence, submissions = {}, []
    for case in contract["cases"]:
        ref = case["reference"]
        evidence[ref["evidence_sha256"]] = json.dumps(
            {key: value for key, value in ref.items() if key != "evidence_sha256"},
            sort_keys=True, separators=(",", ":")).encode()
    for arm in contract["arms"]:
        if candidate_only and arm != "candidate":
            continue
        for case in contract["cases"]:
            outcome = "result" if case["kind"] == "valid_design" else "scientific_refusal"
            payload = {"outcome": outcome, "rows": _rows() if outcome == "result" else [],
                       "reasons": [] if outcome == "result" else ["condition_batch_confounding"]}
            proof = {"format": "devbench.rna-benchmark-execution/1", "kind": "execution_record",
                     "arm": arm, "case_id": case["case_id"],
                     "contract_sha256": contract["contract_sha256"],
                     "case_sha256": _sha(case), "arm_provenance": contract["arms"][arm],
                     "executed_design": {key: case[key] for key in
                         ("inputs", "formula", "contrast", "biological_unit", "gene_ids",
                          "failed_hypothesis_policy")},
                     "result_sha256": _sha(payload)}
            evidence[_sha(proof)] = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
            submissions.append({"arm": arm, "case_id": case["case_id"], "result": payload,
                                "execution_evidence_sha256": _sha(proof)})
    return submissions, evidence


def _score(contract, submissions, evidence):
    return _api().score_benchmark(contract, submissions,
                                  expected_contract_sha256=contract["contract_sha256"],
                                  evidence_blobs=evidence)


def _resign(submission, evidence, contract):
    proof = json.loads(evidence[submission["execution_evidence_sha256"]])
    proof["result_sha256"] = _sha(submission["result"])
    key = _sha(proof)
    evidence[key] = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    submission["execution_evidence_sha256"] = key


def test_perfect_candidate_without_competitor_records_has_no_winner():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract, candidate_only=True)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None
    assert report["comparison_status"] == "incomplete"
    assert len(report["missing_arm_cases"]) == 4
    assert report["arms"]["candidate"]["correct_gene_fraction"] == 1.0
    assert report["scientific_superiority_established"] is False


def test_matched_tie_has_no_winner_and_correct_refusal_is_not_a_trial():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    report = _score(contract, submissions, evidence)
    assert report["comparison_status"] == "matched"
    assert report["winner"] is None
    refusal = next(row for row in report["cases"] if row["case_id"] == "batch-confounded")
    assert refusal["correct_refusal"] is True
    assert refusal["successful_scientific_trial"] is False
    assert report["arms"]["candidate"]["observed_fdp"] == 0.0
    assert report["arms"]["candidate"]["observed_power"] == 1.0
    assert report["fdr_control_established"] is False


def test_only_scientific_errors_in_matched_records_can_establish_scoped_leader():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    for submission in submissions:
        if submission["arm"] != "candidate" and submission["case_id"] == "batch-valid":
            submission["result"]["rows"][0]["effect"] = -2.0
            _resign(submission, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] == "candidate"
    assert report["winner_scope"] == "observed_frozen_cases_only"
    assert report["scientific_superiority_established"] is False


@pytest.mark.parametrize("outcome", ["execution_failure", "not_run"])
def test_infrastructure_failure_or_not_run_never_becomes_scientific_refusal(outcome):
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    item = submissions[-1]
    item["result"] = {"outcome": outcome, "rows": [], "reasons": ["dependency_missing"]}
    _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None and report["comparison_status"] == "incomplete"
    row = report["cases"][-1]
    assert row["correct_refusal"] is None
    assert row["successful_scientific_trial"] is False


@pytest.mark.parametrize("change", ["truncate", "duplicate", "q_below_p", "nan", "zero_fill"])
def test_bad_gene_output_is_not_correct_scientific_output(change):
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    item = submissions[0]
    if change == "truncate":
        item["result"]["rows"].pop()
    elif change == "duplicate":
        item["result"]["rows"][1] = deepcopy(item["result"]["rows"][0])
    elif change == "q_below_p":
        item["result"]["rows"][0]["q_value"] = 0.0001
    elif change == "nan":
        # JSON does not admit NaN. Reject before any comparison can use it.
        item["result"]["rows"][0]["effect"] = float("nan")
        with pytest.raises(ValueError, match="finite|JSON"):
            _score(contract, submissions, evidence)
        return
    else:
        item["result"]["rows"][2] = {"gene_id": "g3", "effect": 0.0, "p_value": 1.0,
                                     "q_value": 1.0, "missing_reason": None}
    _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None
    assert report["arms"]["candidate"]["correct_gene_fraction"] < 1.0


def test_refusing_every_case_does_not_win():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    item = submissions[0]
    item["result"] = {"outcome": "scientific_refusal", "rows": [],
                      "reasons": ["condition_batch_confounding"]}
    _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["arms"]["candidate"]["correct_gene_fraction"] == 0.0
    assert report["arms"]["candidate"]["correct_refusal_fraction"] == 1.0
    assert report["winner"] is None


def test_dependency_failure_disguised_as_refusal_does_not_score():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    item = submissions[1]
    item["result"]["reasons"] = ["condition_batch_confounding", "dependency_missing"]
    _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["cases"][1]["correct_refusal"] is False
    assert report["winner"] is None


@pytest.mark.parametrize("change", ["formula", "tolerance", "case_roster"])
def test_postfreeze_changes_do_not_silently_redefine_the_benchmark(change):
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    if change == "formula":
        contract["cases"][0]["formula"] = "~ condition"
    elif change == "tolerance":
        contract["policy"]["effect_atol"] = 10
    else:
        contract["cases"].pop()
    with pytest.raises(ValueError, match="frozen"):
        _score(contract, submissions, evidence)


@pytest.mark.parametrize("change", ["missing", "bytes", "version", "unit_tests"])
def test_unverified_or_mismatched_execution_evidence_cannot_win(change):
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    item = submissions[-1]
    key = item["execution_evidence_sha256"]
    if change == "missing":
        del evidence[key]
    elif change == "bytes":
        evidence[key] += b"changed"
    else:
        proof = json.loads(evidence[key])
        if change == "version":
            proof["arm_provenance"]["revision"] = "other-version"
        else:
            proof["kind"] = "unit_test_report"
        key = _sha(proof)
        evidence[key] = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
        item["execution_evidence_sha256"] = key
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None and report["comparison_status"] == "incomplete"
    assert report["unverified_arm_cases"]


def test_numerical_reference_self_match_is_not_independent_evidence():
    spec = _spec()
    spec["cases"][0]["reference"] = {"kind": "independent_numerical_reference",
        "source_sha256": spec["arms"]["candidate"]["source_sha256"],
        "evidence_sha256": "5" * 64, "rows": _rows()}
    with pytest.raises(ValueError, match="independent"):
        _api().freeze_benchmark(spec)


def test_numerical_reference_only_is_conformance_not_scientific_winner():
    spec = _spec()
    spec["cases"][0]["reference"] = {"kind": "independent_numerical_reference",
        "source_sha256": "4" * 64, "evidence_sha256": "5" * 64, "rows": _rows()}
    ref = spec["cases"][0]["reference"]
    ref["evidence_sha256"] = _sha({key: value for key, value in ref.items() if key != "evidence_sha256"})
    contract = _api().freeze_benchmark(spec)
    submissions, evidence = _submissions(contract)
    report = _score(contract, submissions, evidence)
    assert report["arms"]["candidate"]["numerical_conformance_fraction"] == 1.0
    assert report["winner"] is None
    assert "insufficient_synthetic_truth_cases" in report["winner_blockers"]


def test_duplicate_arm_case_cannot_be_selected_after_results():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    submissions.append(deepcopy(submissions[0]))
    with pytest.raises(ValueError, match="Duplicate"):
        _score(contract, submissions, evidence)


def test_missing_truth_evidence_or_silent_formula_change_prevents_comparison():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    del evidence[contract["cases"][0]["reference"]["evidence_sha256"]]
    report = _score(contract, submissions, evidence)
    assert report["comparison_status"] == "incomplete" and report["winner"] is None
    assert report["unverified_references"] == ["batch-valid"]
    submissions, evidence = _submissions(contract)
    item = submissions[0]
    proof = json.loads(evidence[item["execution_evidence_sha256"]])
    proof["executed_design"]["formula"] = "~ condition"
    key = _sha(proof)
    evidence[key] = json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    item["execution_evidence_sha256"] = key
    report = _score(contract, submissions, evidence)
    assert report["comparison_status"] == "incomplete" and report["winner"] is None


def test_fabricated_significance_for_ineligible_gene_blocks_scientific_criteria():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    for item in submissions:
        if item["case_id"] != "batch-valid":
            continue
        if item["arm"] == "candidate":
            item["result"]["rows"][0]["q_value"] = 0.0015
            item["result"]["rows"][2] = {"gene_id": "g3", "effect": 100.0, "p_value": 0.0,
                                          "q_value": 0.0, "missing_reason": None}
        else:
            item["result"]["rows"][0]["effect"] = -2.0
            item["result"]["rows"][1]["effect"] = 100.0
        _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None
    assert report["cases"][0]["successful_scientific_trial"] is False
    assert "ineligible_gene_has_estimates:g3" in report["cases"][0]["diagnostics"]


def test_extreme_finite_effect_is_a_scientific_error_not_an_overflow_crash():
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    submissions[0]["result"]["rows"][0]["effect"] = 1e308
    _resign(submissions[0], evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None
    assert report["cases"][0]["successful_scientific_trial"] is False
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("case_id", ["batch-valid", "batch-confounded"])
def test_empty_result_cannot_disguise_missing_comparator_output(case_id):
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    for item in submissions:
        if item["arm"] != "candidate" and item["case_id"] == case_id:
            item["result"] = {"outcome": "result", "rows": [], "reasons": []}
            _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None
    assert report["comparison_status"] == "incomplete"
    assert len(report["incomplete_arm_cases"]) == 2


def test_large_finite_errors_across_replicates_keep_report_finite():
    spec = _spec()
    other = deepcopy(spec["cases"][0])
    other["case_id"] = "batch-valid-second"
    spec["cases"].append(other)
    contract = _api().freeze_benchmark(spec)
    submissions, evidence = _submissions(contract)
    for item in submissions:
        if item["arm"] == "candidate" and item["result"]["outcome"] == "result":
            item["result"]["rows"][0]["effect"] = 1.7e308
            _resign(item, evidence, contract)
    report = _score(contract, submissions, evidence)
    assert report["winner"] is None
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("field", ["execution_evidence_sha256", "arm", "case_id"])
def test_nontext_submission_identity_is_rejected_with_contract_error(field):
    contract = _api().freeze_benchmark(_spec())
    submissions, evidence = _submissions(contract)
    submissions[0][field] = []
    with pytest.raises(ValueError, match="identit"):
        _score(contract, submissions, evidence)
