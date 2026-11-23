"""Frozen public RNA benchmark scoring, without execution or model authority.

This module ingests already retained records. It runs no workflow or estimator.
Independent numerical references measure conformance to a frozen analysis;
synthetic truth measures accuracy on generated cases. Neither establishes
biological validity. Content hashes bind supplied bytes, not their authorship.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

FORMAT = "devbench.rna-benchmark/1"
MAX_GENES = 100_000
MAX_CASES = 256
MAX_DOCUMENT_BYTES = 64_000_000
DESIGN_KEYS = ("inputs", "formula", "contrast", "biological_unit", "gene_ids",
               "failed_hypothesis_policy")
INFRASTRUCTURE_REASONS = frozenset({
    "dependency_missing", "dependency_version_mismatch", "timeout", "quota_exhausted",
    "resource_limit", "workflow_failure", "missing_output", "provider_unavailable",
    "installation_failure", "unsupported_model",
})


def _bytes(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError) as error:
        raise ValueError("Benchmark records require finite JSON values") from error
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("Benchmark document exceeds its bounded JSON budget")
    return raw


def _sha(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _keys(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("Use the exact " + label + " fields")


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 1000


def _digest(value):
    return type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _number(value, *, probability=False):
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and (not probability or 0 <= value <= 1))
    except OverflowError:
        return False


def _ids(values):
    return (type(values) is list and 1 <= len(values) <= MAX_GENES
            and all(_text(value) for value in values) and len(set(values)) == len(values))


def _rows(rows, genes, policy, *, bh=True):
    if (type(rows) is not list or len(rows) != len(genes)
            or any(type(row) is not dict for row in rows)):
        raise ValueError("Result must retain the full declared gene family")
    by_id = {}
    for row in rows:
        _keys(row, ("gene_id", "effect", "p_value", "q_value", "missing_reason"), "gene-result")
        if not _text(row["gene_id"]) or row["gene_id"] in by_id:
            raise ValueError("Gene identities must be unique strings")
        for name in ("effect", "p_value", "q_value"):
            if row[name] is not None and not _number(row[name], probability=name != "effect"):
                raise ValueError("Statistics must be finite and probabilities must be bounded")
        missing = any(row[name] is None for name in ("effect", "p_value", "q_value"))
        if (missing and not _text(row["missing_reason"])) or (not missing and row["missing_reason"] is not None):
            raise ValueError("Missing estimates require a reason; estimated rows cannot have one")
        if (row["p_value"] is None) != (row["q_value"] is None):
            raise ValueError("The frozen BH policy retains paired missing p and q values")
        if row["p_value"] is not None and row["q_value"] < row["p_value"]:
            raise ValueError("A reported q-value cannot be below its p-value")
        by_id[row["gene_id"]] = row
    if set(by_id) != set(genes):
        raise ValueError("Result must retain exactly the declared gene family")
    if bh:
        available = [(row["p_value"], gene) for gene, row in by_id.items() if row["p_value"] is not None]
        denominator = len(genes) if policy == "retain_null_use_one_for_bh_denominator" else len(available)
        running = 1.0
        ordered = sorted(available)
        for rank in range(len(ordered), 0, -1):
            pvalue, gene = ordered[rank - 1]
            running = min(running, pvalue * denominator / rank)
            if not math.isclose(by_id[gene]["q_value"], running, abs_tol=1e-12, rel_tol=1e-8):
                raise ValueError("Reported q-values differ from the frozen full-family BH policy")
    return by_id


def _validate(spec):
    _bytes(spec)
    _keys(spec, ("format", "benchmark_id", "candidate", "arms", "policy", "cases"), "benchmark")
    if spec["format"] != FORMAT or not _text(spec["benchmark_id"]):
        raise ValueError("Use a named RNA benchmark contract")
    arms = spec["arms"]
    if (type(arms) is not dict or not _text(spec["candidate"]) or not 3 <= len(arms) <= 16
            or not {"latchbio", "clawbio", spec["candidate"]} <= set(arms)
            or spec["candidate"] in {"latchbio", "clawbio"}):
        raise ValueError("Freeze the candidate, LatchBio and ClawBio arms")
    for name, arm in arms.items():
        _keys(arm, ("revision", "source_sha256", "environment_sha256", "configuration_sha256"), "arm-provenance")
        if (not _text(name) or not _text(arm["revision"])
                or any(not _digest(arm[key]) for key in arm if key.endswith("sha256"))):
            raise ValueError("Every arm needs immutable source, environment and configuration identities")
    policy = spec["policy"]
    _keys(policy, ("alpha", "effect_atol", "effect_rtol", "probability_atol", "probability_rtol",
                   "maximum_observed_fdp", "minimum_observed_power", "maximum_effect_rmse",
                   "minimum_truth_cases", "minimum_invalid_cases"), "scoring-policy")
    for key in ("alpha", "maximum_observed_fdp", "minimum_observed_power"):
        if not _number(policy[key], probability=True):
            raise ValueError("Freeze bounded scientific thresholds")
    if not 0 < policy["alpha"] < 1:
        raise ValueError("Alpha must be strictly between zero and one")
    for key in ("effect_atol", "effect_rtol", "probability_atol", "probability_rtol", "maximum_effect_rmse"):
        if not _number(policy[key]) or policy[key] < 0:
            raise ValueError("Freeze finite nonnegative numerical tolerances")
    for key in ("minimum_truth_cases", "minimum_invalid_cases"):
        if type(policy[key]) is not int or not 1 <= policy[key] <= MAX_CASES:
            raise ValueError("Freeze a positive minimum number of required cases")
    cases = spec["cases"]
    if type(cases) is not list or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Freeze a bounded case roster")
    seen = set()
    for case in cases:
        _keys(case, ("case_id", "kind", *DESIGN_KEYS, "reference"), "case")
        if not _text(case["case_id"]) or case["case_id"] in seen:
            raise ValueError("Case identities must be unique")
        seen.add(case["case_id"])
        if case["kind"] not in {"valid_design", "invalid_design"}:
            raise ValueError("Case must declare whether its design is valid")
        _keys(case["inputs"], ("measurements_sha256", "metadata_sha256", "reference_sha256",
                              "sample_order_sha256"), "input-identity")
        if any(not _digest(value) for value in case["inputs"].values()):
            raise ValueError("Freeze all input identities")
        if not _text(case["formula"]) or not _text(case["biological_unit"]) or not _ids(case["gene_ids"]):
            raise ValueError("Freeze formula, biological unit and complete gene family")
        _keys(case["contrast"], ("factor", "numerator", "denominator"), "contrast")
        if (any(not _text(value) for value in case["contrast"].values())
                or case["contrast"]["numerator"] == case["contrast"]["denominator"]):
            raise ValueError("Freeze distinct contrast levels and direction")
        if case["failed_hypothesis_policy"] not in {
                "retain_null_use_one_for_bh_denominator", "exclude_failed_hypotheses"}:
            raise ValueError("Freeze the failed-hypothesis BH policy")
        reference = case["reference"]
        kind = reference.get("kind") if type(reference) is dict else None
        _keys(reference, ("kind", "source_sha256", "evidence_sha256",
                          "required_reasons" if kind == "invalid_design" else "rows"), "reference")
        if not _digest(reference["source_sha256"]) or not _digest(reference["evidence_sha256"]):
            raise ValueError("Reference evidence and source must be identified")
        if kind == "independent_numerical_reference":
            if reference["source_sha256"] in {arm["source_sha256"] for arm in arms.values()}:
                raise ValueError("Numerical reference must be independent, not an arm self-match")
            _rows(reference["rows"], case["gene_ids"], case["failed_hypothesis_policy"])
        elif kind == "synthetic_truth":
            truth = reference["rows"]
            if type(truth) is not list or len(truth) != len(case["gene_ids"]):
                raise ValueError("Synthetic truth retains the complete gene family")
            identities = []
            for row in truth:
                _keys(row, ("gene_id", "effect", "differential"), "synthetic-truth")
                if (not _text(row["gene_id"]) or (row["effect"] is not None and not _number(row["effect"]))
                        or (row["differential"] is not None and type(row["differential"]) is not bool)
                        or (row["effect"] is None) != (row["differential"] is None)):
                    raise ValueError("Synthetic truth needs finite effects and explicit eligibility")
                identities.append(row["gene_id"])
            if len(set(identities)) != len(identities) or set(identities) != set(case["gene_ids"]):
                raise ValueError("Synthetic truth must match every declared gene exactly once")
        elif kind == "invalid_design":
            reasons = reference["required_reasons"]
            if (type(reasons) is not list or not reasons or any(not _text(reason) for reason in reasons)
                    or len(set(reasons)) != len(reasons) or set(reasons) & INFRASTRUCTURE_REASONS):
                raise ValueError("Invalid-design truth needs scientific refusal causes")
        else:
            raise ValueError("Use synthetic truth or an independent numerical/design reference")
        if (case["kind"] == "invalid_design") != (kind == "invalid_design"):
            raise ValueError("Reference kind must match the declared case validity")
        payload = {key: value for key, value in reference.items() if key != "evidence_sha256"}
        if _sha(payload) != reference["evidence_sha256"]:
            raise ValueError("Reference evidence digest must bind the complete frozen reference")


def freeze_benchmark(spec):
    """Validate and hash a declared pre-result contract, without asserting timing.

    Retain this digest outside the run and pass it back to ``score_benchmark``.
    Actual preregistration time and reference independence require external audit.
    """
    _validate(spec)
    return {**deepcopy(spec), "contract_sha256": _sha(spec)}


def _verified_blob(key, blobs, expected):
    raw = blobs.get(key)
    if type(raw) is not bytes or len(raw) > MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest() != key:
        return False
    try:
        return json.loads(raw) == expected
    except (ValueError, UnicodeDecodeError):
        return False


def _close(actual, expected, policy, *, probability=False):
    if actual is None or expected is None:
        return actual is expected
    prefix = "probability" if probability else "effect"
    return math.isclose(actual, expected, abs_tol=policy[prefix + "_atol"],
                        rel_tol=policy[prefix + "_rtol"])


def _score_case(case, result, policy):
    base = {"case_id": case["case_id"], "outcome": result["outcome"],
            "score_track": case["reference"]["kind"], "correct_refusal": None,
            "successful_scientific_trial": False, "correct_gene_count": 0,
            "gene_denominator": len(case["gene_ids"]), "numerical_conformance": None,
            "observed_fdp": None, "observed_power": None, "effect_rmse": None,
            "scientific_criteria_passed": False, "diagnostics": []}
    outcome = result["outcome"]
    if outcome in {"execution_failure", "not_run"}:
        base["diagnostics"].append("no_scientific_outcome")
        return base
    if case["kind"] == "invalid_design":
        base["correct_refusal"] = (outcome == "scientific_refusal" and not result["rows"]
            and set(result["reasons"]) == set(case["reference"]["required_reasons"]))
        # Diagnostic correctness is credited without calling it a successful trial.
        return base
    if outcome != "result":
        base["diagnostics"].append("valid_design_refused")
        return base
    try:
        rows = _rows(result["rows"], case["gene_ids"], case["failed_hypothesis_policy"])
        if result["reasons"]:
            raise ValueError("A result cannot also claim a terminal refusal")
    except ValueError as error:
        base["diagnostics"].append(str(error))
        return base
    reference = case["reference"]
    if reference["kind"] == "independent_numerical_reference":
        for expected in reference["rows"]:
            row = rows[expected["gene_id"]]
            matched = all(_close(row[name], expected[name], policy, probability=name != "effect")
                          for name in ("effect", "p_value", "q_value"))
            matched = matched and row["missing_reason"] == expected["missing_reason"]
            base["correct_gene_count"] += int(matched)
        base["numerical_conformance"] = base["correct_gene_count"] / base["gene_denominator"]
        base["scientific_criteria_passed"] = base["numerical_conformance"] == 1.0
        # Conformance is never substituted for a successful biological trial.
        return base
    discoveries = false_discoveries = alternatives = true_discoveries = 0
    effect_errors, missing_effect, invalid_ineligible = [], False, False
    for truth in reference["rows"]:
        row = rows[truth["gene_id"]]
        if truth["effect"] is None:
            missing = all(row[name] is None for name in ("effect", "p_value", "q_value"))
            base["correct_gene_count"] += int(missing)
            if not missing:
                invalid_ineligible = True
                base["diagnostics"].append("ineligible_gene_has_estimates:" + truth["gene_id"])
            continue
        is_discovery = row["q_value"] is not None and row["q_value"] <= policy["alpha"]
        discoveries += int(is_discovery)
        alternatives += int(truth["differential"])
        false_discoveries += int(is_discovery and not truth["differential"])
        true_discoveries += int(is_discovery and truth["differential"])
        if row["effect"] is None:
            missing_effect = True
        else:
            effect_errors.append(row["effect"] - truth["effect"])
        base["correct_gene_count"] += int(_close(row["effect"], truth["effect"], policy)
            and row["p_value"] is not None and is_discovery == truth["differential"])
    base["observed_fdp"] = false_discoveries / max(discoveries, 1)
    base["observed_power"] = true_discoveries / alternatives if alternatives else None
    # hypot avoids overflow from squaring a large but finite submitted effect.
    rmse = math.hypot(*effect_errors) / math.sqrt(len(effect_errors)) if effect_errors and not missing_effect else None
    if rmse is not None and math.isfinite(rmse):
        base["effect_rmse"] = rmse
    elif rmse is not None:
        base["diagnostics"].append("effect_error_exceeds_finite_metric_range")
    base["scientific_criteria_passed"] = (
        not invalid_ineligible and base["observed_fdp"] <= policy["maximum_observed_fdp"]
        and (not alternatives or base["observed_power"] >= policy["minimum_observed_power"])
        and base["effect_rmse"] is not None and base["effect_rmse"] <= policy["maximum_effect_rmse"])
    base["successful_scientific_trial"] = base["scientific_criteria_passed"]
    return base


def score_benchmark(contract, submissions, *, expected_contract_sha256, evidence_blobs):
    """Score complete matched retained records; never launch or authorize a model.

    ``winner`` means a strict observed Pareto leader on these frozen records,
    not population-level superiority. Byte verification cannot authenticate
    execution, true preregistration or the independence of supplied references.
    """
    if type(contract) is not dict:
        raise ValueError("A frozen benchmark contract is required")
    spec = {key: value for key, value in contract.items() if key != "contract_sha256"}
    if (not _digest(expected_contract_sha256) or contract.get("contract_sha256") != expected_contract_sha256
            or _sha(spec) != expected_contract_sha256):
        raise ValueError("The benchmark differs from its externally retained frozen digest")
    _validate(spec)
    if type(submissions) is not list or len(submissions) > len(spec["arms"]) * len(spec["cases"]):
        if type(submissions) is list:
            raise ValueError("Duplicate or excessive arm-case submissions")
        raise ValueError("Submissions must be a bounded list")
    if type(evidence_blobs) is not dict:
        raise ValueError("Supply bounded evidence bytes by SHA-256")
    by_case = {case["case_id"]: case for case in spec["cases"]}
    indexed = {}
    for submission in submissions:
        _keys(submission, ("arm", "case_id", "result", "execution_evidence_sha256"), "submission")
        if (not _text(submission["arm"]) or not _text(submission["case_id"])
                or not _digest(submission["execution_evidence_sha256"])):
            raise ValueError("Submission identities must be text and evidence a SHA-256 digest")
        key = submission["arm"], submission["case_id"]
        if key in indexed:
            raise ValueError("Duplicate arm-case submission; retries cannot be cherry-picked")
        if key[0] not in spec["arms"] or key[1] not in by_case:
            raise ValueError("Submission names an arm or case outside the frozen roster")
        result = submission["result"]
        _bytes(result)
        _keys(result, ("outcome", "rows", "reasons"), "result-envelope")
        if result["outcome"] not in {"result", "scientific_refusal", "execution_failure", "not_run"}:
            raise ValueError("Keep scientific and infrastructure outcomes distinct")
        if (type(result["rows"]) is not list or len(result["rows"]) > MAX_GENES
                or type(result["reasons"]) is not list or len(result["reasons"]) > 32
                or any(not _text(reason) for reason in result["reasons"])):
            raise ValueError("Use bounded result rows and explicit reason codes")
        indexed[key] = submission
    unverified_references = []
    for case in spec["cases"]:
        reference = case["reference"]
        payload = {key: value for key, value in reference.items() if key != "evidence_sha256"}
        if not _verified_blob(reference["evidence_sha256"], evidence_blobs, payload):
            unverified_references.append(case["case_id"])
    missing, unverified, incomplete, scored = [], [], [], []
    for arm, provenance in spec["arms"].items():
        for case in spec["cases"]:
            key = arm, case["case_id"]
            label = {"arm": arm, "case_id": case["case_id"]}
            item = indexed.get(key)
            if item is None:
                missing.append(label)
                continue
            proof = {"format": "devbench.rna-benchmark-execution/1", "kind": "execution_record",
                     **label, "contract_sha256": expected_contract_sha256,
                     "case_sha256": _sha(case), "arm_provenance": provenance,
                     "executed_design": {key: case[key] for key in DESIGN_KEYS},
                     "result_sha256": _sha(item["result"])}
            if not _verified_blob(item["execution_evidence_sha256"], evidence_blobs, proof):
                unverified.append(label)
                continue
            result = item["result"]
            if (result["outcome"] in {"execution_failure", "not_run"}
                    or set(result["reasons"]) & INFRASTRUCTURE_REASONS
                    or (result["outcome"] == "result" and not result["rows"])):
                incomplete.append(label)
            scored.append({"arm": arm, **_score_case(case, result, spec["policy"])})
    summaries = {}
    for arm in spec["arms"]:
        rows = [row for row in scored if row["arm"] == arm]
        truth = [row for row in rows if row["score_track"] == "synthetic_truth"]
        invalid = [row for row in rows if row["score_track"] == "invalid_design"]
        numeric = [row for row in rows if row["score_track"] == "independent_numerical_reference"]
        def average(values):
            return math.fsum(value / len(values) for value in values) if values else None
        summaries[arm] = {
            "scored_cases": len(rows),
            "correct_gene_fraction": average([row["correct_gene_count"] / row["gene_denominator"] for row in truth]),
            "correct_refusal_fraction": average([float(row["correct_refusal"] is True) for row in invalid]),
            "numerical_conformance_fraction": average([row["numerical_conformance"] or 0.0 for row in numeric]),
            "observed_fdp": average([row["observed_fdp"] for row in truth if row["observed_fdp"] is not None]),
            "observed_power": average([row["observed_power"] for row in truth if row["observed_power"] is not None]),
            "effect_rmse": average([row["effect_rmse"] for row in truth if row["effect_rmse"] is not None]),
            "successful_scientific_trials": sum(row["successful_scientific_trial"] for row in rows),
            "correct_diagnostic_refusals": sum(row["correct_refusal"] is True for row in rows),
        }
    blockers = []
    if missing or unverified or incomplete or unverified_references:
        blockers.append("incomplete_matched_evidence")
    if sum(case["reference"]["kind"] == "synthetic_truth" for case in spec["cases"]) < spec["policy"]["minimum_truth_cases"]:
        blockers.append("insufficient_synthetic_truth_cases")
    if sum(case["kind"] == "invalid_design" for case in spec["cases"]) < spec["policy"]["minimum_invalid_cases"]:
        blockers.append("insufficient_invalid_design_cases")
    candidate = spec["candidate"]
    candidate_rows = [row for row in scored if row["arm"] == candidate]
    if any((row["score_track"] == "invalid_design" and row["correct_refusal"] is not True)
           or (row["score_track"] != "invalid_design" and not row["scientific_criteria_passed"])
           for row in candidate_rows):
        blockers.append("candidate_failed_frozen_scientific_criteria")
    winner = None
    if not blockers:
        primary = ("correct_gene_fraction", "correct_refusal_fraction")
        candidate_metrics = summaries[candidate]
        dominates = all(
            all(candidate_metrics[key] is not None and other[key] is not None
                and candidate_metrics[key] >= other[key] for key in primary)
            and any(candidate_metrics[key] > other[key] for key in primary)
            for arm, other in summaries.items() if arm != candidate)
        if dominates:
            winner = candidate
        else:
            blockers.append("no_strict_dominance_over_every_comparator")
    report = {"format": "devbench.rna-benchmark-score/1", "benchmark_id": spec["benchmark_id"],
        "contract_sha256": expected_contract_sha256,
        "comparison_status": "incomplete" if missing or unverified or incomplete or unverified_references else "matched",
        "winner": winner, "winner_scope": "observed_frozen_cases_only", "winner_blockers": blockers,
        "missing_arm_cases": missing, "unverified_arm_cases": unverified,
        "incomplete_arm_cases": incomplete, "unverified_references": unverified_references,
        "arms": summaries, "cases": scored, "fdr_control_established": False,
        "scientific_superiority_established": False, "biological_agreement": "not_assessed",
        "execution_authenticity": "not_established_by_content_hashes",
        "reference_independence": "declared_source_distinctness_requires_independent_review",
        "preregistration_timing": "external_retention_required",
        "model_execution_authorized": False, "model_sessions_started_by_scorer": 0,
        "clinical_release_authorized": False}
    _bytes(report)
    return report
