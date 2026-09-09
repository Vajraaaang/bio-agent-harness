"""Evidence-aware empirical comparisons; no live inference or model judges."""
import copy
import json

import pytest


def run(task, condition, passed, repetition=0, **metadata):
    return {"task_id": task, "model": "model-a", "backend": "claude-cli",
            "condition": condition, "repetition": repetition, "passed": passed,
            "score": float(passed), "metadata": {"dataset_hash": "data-" + task,
                "task_spec_hash": "spec-" + task, "track": "trajectory",
                "source_kind": "synthetic", **metadata}}


def analyze(results):
    from experiments.analysis import analyze_runs
    return analyze_runs(results)


def test_exact_pairing_known_gain_and_mcnemar():
    rows = [run(str(i), condition, passed) for i, outcomes in enumerate(
        [(False, True), (False, True), (True, True), (False, False)])
        for condition, passed in zip(["baseline", "assisted"], outcomes)]
    result = analyze(rows)
    comparison = result["comparisons"][0]
    assert comparison["pairs"] == 4
    assert comparison["task_clusters"] == 4
    assert comparison["delta_percentage_points"] == 50
    assert comparison["baseline_pass_rate"] == .25
    assert comparison["assisted_pass_rate"] == .75
    assert comparison["mcnemar"]["p_value"] == .5
    assert comparison["bootstrap"]["unit"] == "task_cluster"
    assert comparison["exploratory_pilot"] is True
    assert result["pairing"]["coverage"] == 1
    assert result == analyze(rows)
    json.dumps(result, allow_nan=False)


def test_pairing_excludes_duplicates_missing_and_mismatched_fingerprints():
    rows = [run("good", "baseline", False), run("good", "assisted", True),
            run("missing", "baseline", True),
            run("changed", "baseline", False), run("changed", "assisted", True, dataset_hash="different"),
            run("dup", "baseline", False), run("dup", "baseline", False), run("dup", "assisted", True),
            run("nohash", "baseline", False, task_spec_hash=None)]
    result = analyze(rows)
    assert result["pairing"]["paired_runs"] == 2
    assert result["pairing"]["excluded_runs"] == 7
    assert result["pairing"]["exclusion_counts"] == {
        "missing_counterpart": 1, "fingerprint_mismatch": 2, "duplicate_condition": 3,
        "missing_fingerprint": 1}
    assert result["pairing"]["coverage"] == pytest.approx(2 / 9)


def test_task_spec_changes_and_source_changes_do_not_pair():
    rows = [run("spec", "baseline", False), run("spec", "assisted", True, task_spec_hash="changed"),
            run("source", "baseline", False), run("source", "assisted", True, source_kind="public")]
    result = analyze(rows)
    assert result["comparisons"] == []
    assert result["pairing"]["exclusion_counts"] == {"fingerprint_mismatch": 2, "stratum_mismatch": 2}


def test_repetition_clusters_are_resampled_together():
    rows = []
    for task, outcomes, repeats in [("gain", (False, True), 4), ("loss", (True, False), 1)]:
        for repetition in range(repeats):
            rows.extend(run(task, c, p, repetition) for c, p in zip(["baseline", "assisted"], outcomes))
    comparison = analyze(rows)["comparisons"][0]
    assert comparison["pairs"] == 5 and comparison["task_clusters"] == 2
    assert comparison["delta_percentage_points"] == pytest.approx(60)
    assert comparison["bootstrap"]["confidence_interval_percentage_points"] == [-100, 100]
    assert comparison["mcnemar"]["independence_assumption_satisfied"] is False


def test_grouping_uses_observed_model_and_preserves_unknown_cost():
    rows = [run("a", "assisted", True, model_ids=["actual-a"], cost_usd=None),
            run("b", "assisted", False, model_ids=["actual-b"], cost_usd=.1)]
    result = analyze(rows)
    assert [g["model"] for g in result["groups"]] == ["actual-a", "actual-b"]
    assert result["groups"][0]["cost_usd"]["reported"] == 0
    assert result["groups"][0]["cost_usd"]["total"] is None
    assert result["groups"][1]["cost_usd"]["total"] == .1


def fit(genes, succeeded=True, formula="numeric_time"):
    return {"tool_name": "fit_temporal_trajectory", "arguments": {"formula_str": formula},
            "succeeded": succeeded, "result": {"genes": genes}}


def test_nonsignificant_claims_require_observed_fit_q_values():
    rows = [run("a", "assisted", False,
                answer={"claimed_significant_genes": ["G1"]}, tool_calls=[fit({"G1": {"padj": .2}})]),
            run("b", "assisted", True,
                answer={"claimed_significant_genes": ["G1"]}, tool_calls=[fit({"G1": {"padj": .01}})]),
            run("c", "assisted", False,
                answer={"claimed_significant_genes": ["G1"]}, tool_calls=[fit({"G1": {"p_value": .8}})]),
            run("d", "assisted", False, answer={"master_regulators": ["G1"]})]
    metric = analyze(rows)["failure_taxonomy"]["nonsignificant_gene_claims"]
    assert metric == {"failures": 1, "observed": 2, "unknown": 2, "not_applicable": 0,
                      "rate": .5, "coverage": .5}


def test_partial_q_capture_is_unknown_unless_a_violation_is_observed():
    good = run("a", "assisted", True, answer={"claimed_significant_genes": ["G1", "G2"]},
               tool_calls=[fit({"G1": {"padj": .01}})])
    bad = copy.deepcopy(good)
    bad["metadata"]["tool_calls"][0]["result"]["genes"]["G1"]["padj"] = .2
    assert analyze([good])["failure_taxonomy"]["nonsignificant_gene_claims"]["unknown"] == 1
    assert analyze([bad])["failure_taxonomy"]["nonsignificant_gene_claims"]["failures"] == 1


def test_collinearity_blindness_requires_truth_and_capture():
    rows = [run("unchecked", "assisted", False, diagnostic_codes=["confounded"], tool_calls=[]),
            run("uncaptured", "baseline", False, diagnostic_codes=["confounded"]),
            run("warning", "baseline", False, diagnostic_codes=["confounded"],
                answer={"warnings": ["Rank deficient design"]}),
            run("checked", "assisted", True, diagnostic_codes=["confounded"], tool_calls=[{
                "tool_name": "validate_design_matrix", "arguments": {"formula_str": "numeric_time + C(batch)"},
                "succeeded": True, "result": {"is_full_rank": False}}])]
    metric = analyze(rows)["failure_taxonomy"]["collinearity_blindness"]
    assert metric["failures"] == 1 and metric["observed"] == 3 and metric["unknown"] == 1


def test_self_correction_requires_a_changed_successful_call():
    events = [fit({}, False, "numeric_time + C(batch)"), fit({}, True, "numeric_time + C(batch)"),
              fit({}, False, "numeric_time + C(batch)"), fit({}, True, "numeric_time")]
    metric = analyze([run("a", "assisted", True, tool_calls=events)])["tool_recovery"]
    assert metric["tool_errors"] == 2
    assert metric["corrected_calls"] == 1
    assert metric["successful_retries"] == 1


def test_context_burdens_have_coverage_and_do_not_claim_causality():
    rows = [run("a", "assisted", True, turns=[{
        "usage": {"input_tokens": 100, "output_tokens": 20}, "stdout_bytes": 500}]),
        run("b", "assisted", False, turns=[{"usage": None}, {"usage": None}]),
        run("c", "baseline", False)]
    context = analyze(rows)["context_degradation"]
    assert context["observational_only"] is True
    assert context["dimensions"]["turn_count"]["observed"] == 2
    assert context["dimensions"]["tokens"]["observed"] == 1
    assert context["dimensions"]["output_bytes"]["observed"] == 1
    assert context["dimensions"]["tokens"]["coverage"] == pytest.approx(1 / 3)
    assert context["turns"][0]["tokens"]["reported"] == 1


def test_malformed_records_are_explicitly_excluded_and_diagnostics_are_robust():
    rows = [None, {"passed": "yes"}, run("meta", "baseline", True),
            run("valid", "assisted", False, tool_calls=[None, "bad", {"succeeded": []}],
                turns=[None], answer={"claimed_significant_genes": [{}]}, failure_type="answer_error")]
    rows[2]["metadata"] = []
    result = analyze(rows)
    assert result["validation"]["excluded_records"] == 3
    assert result["validation"]["valid_records"] == 1
    assert result["failure_taxonomy"]["format_errors"]["failures"] == 1
    json.dumps(result, allow_nan=False)
    with pytest.raises(ValueError, match="list"):
        analyze({})


def test_report_includes_coverage_unknowns_and_exploratory_limits():
    from experiments.analysis import render_empirical_report
    result = analyze([run("a", "baseline", False), run("a", "assisted", True)])
    report = render_empirical_report(result)
    for phrase in ["unknown", "Exploratory", "percentage points", "task clusters", "observational", "Pairing exclusions"]:
        assert phrase in report
    assert "18" not in report
    assert "No matched" in render_empirical_report(analyze([]))


def test_trap_codes_and_missing_guards_use_observed_evidence():
    rows = [run("oversmooth", "assisted", False, diagnostic_codes=["oversmoothing"], tool_calls=[]),
            run("contrast", "assisted", True, diagnostic_codes=["invalid_contrast"],
                answer={"warnings": ["Invalid contrast: refusing this comparison"]}, tool_calls=[]),
            run("incomplete", "baseline", False, diagnostic_codes=["invalid_contrast"],
                tool_calls=[], tool_capture_complete=False),
            run("fit", "assisted", False, tool_calls=[fit({})]),
            run("emptycheck", "assisted", False, tool_calls=[{
                "tool_name": "validate_design_matrix", "succeeded": True, "result": {}}, fit({})])]
    taxonomy = analyze(rows)["failure_taxonomy"]
    assert taxonomy["oversmoothing_ignored"]["failures"] == 1
    assert taxonomy["invalid_contrast_ignored"]["observed"] == 1
    assert taxonomy["invalid_contrast_ignored"]["failures"] == 0
    assert taxonomy["invalid_contrast_ignored"]["unknown"] == 3
    assert taxonomy["missing_preflight_guard"]["failures"] == 2


def test_malformed_truth_is_unknown_and_failed_fits_are_not_q_truth():
    rows = [run("truth", "assisted", False, diagnostic_codes=None, tool_calls=[]),
            run("fit", "assisted", False, answer={"claimed_significant_genes": ["G1"]},
                tool_calls=[fit({"G1": {"padj": .4}}, succeeded=False)])]
    taxonomy = analyze(rows)["failure_taxonomy"]
    assert taxonomy["collinearity_blindness"]["unknown"] == 2
    assert taxonomy["nonsignificant_gene_claims"]["unknown"] == 2
    assert taxonomy["nonsignificant_gene_claims"]["rate"] is None


def test_single_task_interval_is_unavailable_and_reversed_order_is_stable():
    rows = [run("a", "assisted", True), run("a", "baseline", False)]
    first = analyze(rows)["comparisons"][0]
    second = analyze(list(reversed(rows)))["comparisons"][0]
    assert first == second
    assert first["bootstrap"]["confidence_interval_percentage_points"] is None
    assert first["bootstrap"]["status"] == "insufficient_task_clusters"
