"""Finite arithmetic on invented tables, never a scientific method ranking."""
from copy import deepcopy

import pytest

from devbench.analysis.rna_publication_metrics import (
    gene_set_agreement, validate_gene_table, audit_bh_family,
    compare_gene_effects, select_gene_ids,
)


def gene_table():
    return {"format": "devbench.rna-gene-table/2", "table_id": "synthetic-table",
        "alignment": {"estimand_id": "synthetic-effect", "biological_unit": "independent_fixture_unit",
            "contrast": "B versus A", "cohort": "synthetic", "processing": "identity",
            "effect_scale": "log2_ratio", "hypothesis": "condition coefficient equals zero", "shrinkage": "none"},
        "family": {"family_id": "synthetic-tested-family", "eligible_ids": ["g1", "g2", "g3"],
            "complete": True, "probability_semantics": {"p_value": "frequentist_p", "q_value": "benjamini_hochberg"},
            "failed_hypothesis_policy": "retain_null_use_one_for_bh_denominator"},
        "mapping": {"namespace": "synthetic_ids", "annotation_ref": None,
            "records": [{"source_id": gene, "target_id": gene, "status": "mapped"} for gene in ["g1", "g2", "g3"]]},
        "rows": [{"feature_id": "g1", "tested": True, "status": "ok", "effect": 2.0, "p_value": .01, "q_value": .03, "uncertainty": None},
                 {"feature_id": "g2", "tested": True, "status": "ok", "effect": -1.0, "p_value": .04, "q_value": .06, "uncertainty": None},
                 {"feature_id": "g3", "tested": True, "status": "failed", "effect": None, "p_value": None, "q_value": None, "uncertainty": None}],
        "limitations": "Invented numbers for engineering checks"}


def test_gene_set_numbers_describe_publication_reference_agreement():
    reference = {f"G{i:03d}" for i in range(100)}
    actual = {f"G{i:03d}" for i in range(85)} | {f"G{i:03d}" for i in range(100, 125)}
    universe = {f"G{i:03d}" for i in range(150)}
    result = gene_set_agreement(reference, actual, universe, reference_complete=True)
    assert result["overlap"] == 85
    assert result["precision"] == 85 / 110
    assert result["recall"] == 85 / 100
    assert result["f1"] == 170 / 210
    assert result["jaccard"] == 85 / 125
    assert result["status"] == "computed"
    assert result["denominators"] == {"reference": 100, "actual": 110, "union": 125, "eligible": 150}
    assert result["scope"] == "publication_reference_agreement"
    assert result["biological_truth_accuracy"] is None


def test_incomplete_reference_list_does_not_label_unreported_genes_false_positive():
    result = gene_set_agreement(["g1"], ["g1", "g2"], ["g1", "g2", "g3"], reference_complete=False)
    assert result["overlap"] == 1
    assert result["status"] == "not_assessable"
    assert all(result[key] is None for key in ("precision", "recall", "f1", "jaccard"))
    assert result["coverage"]["unknown_reference_membership"] == 2
    assert "false_positives" not in result


@pytest.mark.parametrize("reference,actual", [([], []), (["g1"], []), ([], ["g1"])])
def test_empty_set_denominators_do_not_receive_perfect_scores(reference, actual):
    result = gene_set_agreement(reference, actual, ["g1"], reference_complete=True)
    assert result["status"] == "not_assessable"
    assert result["f1"] is None
    assert result["precision"] is None if not actual else result["precision"] == 0
    assert result["recall"] is None if not reference else result["recall"] == 0


@pytest.mark.parametrize("reference,actual,universe", [(["outside"], [], ["g1"]), ([], ["outside"], ["g1"]),
    (["g1", "g1"], [], ["g1"]), ([], [], ["g1", "g1"])])
def test_unknown_membership_and_duplicate_ids_are_explicitly_rejected(reference, actual, universe):
    with pytest.raises(ValueError, match="universe|unique|eligible"):
        gene_set_agreement(reference, actual, universe, reference_complete=True)


def test_full_family_bh_audit_preserves_missing_values():
    table = gene_table()
    result = audit_bh_family(table)
    assert result["status"] == "computed" and result["family_size"] == 3
    assert result["audited_count"] == 2
    assert result["values_match"] is True
    assert table["rows"][2]["p_value"] is None and table["rows"][2]["q_value"] is None


def test_bh_subset_is_not_recomputed_as_a_complete_family():
    table = gene_table()
    table["family"]["complete"] = False
    table["rows"] = table["rows"][:1]
    table["mapping"]["records"] = table["mapping"]["records"][:1]
    result = audit_bh_family(table)
    assert result["status"] == "not_assessable"
    assert result["values_match"] is None
    assert "incomplete_tested_family" in result["reason_codes"]


def test_q_less_than_p_is_rejected_only_under_explicit_bh_semantics():
    table = gene_table()
    table["rows"][0]["q_value"] = .005
    with pytest.raises(ValueError, match="BH|q.*p"):
        validate_gene_table(table)
    table["family"]["probability_semantics"]["q_value"] = "storey_q"
    assert validate_gene_table(table)["rows"][0]["q_value"] == .005
    assert audit_bh_family(table)["status"] == "not_applicable"


def test_a_column_named_q_does_not_establish_bh_semantics():
    table = gene_table()
    table["family"]["probability_semantics"]["q_value"] = "unknown"
    assert audit_bh_family(table)["status"] == "not_assessable"


def test_storey_like_values_mislabeled_bh_fail_audit():
    table = gene_table()
    table["rows"][0]["q_value"] = .011
    result = audit_bh_family(table)
    assert result["status"] == "failed" and result["values_match"] is False


@pytest.mark.parametrize("attack", ["filtered_value", "failed_zero", "missing_p_zero", "duplicate_mapping", "nonfinite"])
def test_failed_filtered_or_ambiguous_results_cannot_become_successful_numbers(attack):
    table = gene_table()
    if attack == "filtered_value": table["rows"][0]["status"] = "filtered"
    elif attack == "failed_zero": table["rows"][2]["effect"] = 0
    elif attack == "missing_p_zero": table["rows"][2]["p_value"] = 0
    elif attack == "duplicate_mapping": table["mapping"]["records"][1]["target_id"] = "g1"
    else: table["rows"][0]["effect"] = float("nan")
    with pytest.raises(ValueError):
        validate_gene_table(table)


def test_effect_comparison_is_selected_and_reports_complete_denominators():
    reference = gene_table()
    actual = gene_table()
    actual["rows"][0]["effect"] = 3.0
    result = compare_gene_effects(reference, actual, metric_id="gene_effect_rmse")
    assert result["status"] == "computed" and result["value"] == pytest.approx((.5) ** .5)
    assert result["denominators"] == {"reference_rows": 3, "actual_rows": 3, "matched_rows": 3, "assessed_rows": 2, "missing_rows": 1}
    assert result["scientific_optimality_established"] is False


@pytest.mark.parametrize("field", ["contrast", "biological_unit", "cohort", "processing", "effect_scale", "hypothesis", "shrinkage"])
def test_metric_specific_incompatible_effects_abstain(field):
    reference = gene_table()
    actual = gene_table()
    actual["alignment"][field] = "changed"
    result = compare_gene_effects(reference, actual, metric_id="gene_effect_rmse")
    assert result["status"] == "not_assessable" and result["value"] is None
    assert "incompatible:" + field in result["reason_codes"]


def test_unknown_estimand_and_changed_testing_family_limit_assessment():
    reference = gene_table()
    actual = gene_table()
    actual["alignment"]["contrast"] = None
    assert compare_gene_effects(reference, actual, metric_id="gene_effect_rmse")["status"] == "not_assessable"
    actual = gene_table()
    actual["family"]["family_id"] = "another-family"
    result = compare_gene_effects(reference, actual, metric_id="gene_effect_direction_agreement")
    assert result["status"] == "not_assessable"


def test_changed_one_to_one_mapping_outcomes_do_not_silently_compare_wrong_entities():
    reference = gene_table()
    actual = gene_table()
    actual["mapping"]["records"][0]["target_id"] = "g2"
    actual["mapping"]["records"][1]["target_id"] = "g1"
    result = compare_gene_effects(reference, actual, metric_id="gene_effect_rmse")
    assert result["status"] == "not_assessable" and result["value"] is None
    assert "incompatible:mapping_outcomes" in result["reason_codes"]


def test_incomplete_table_keeps_declared_family_denominator_and_abstains():
    reference = gene_table()
    actual = gene_table()
    actual["family"]["complete"] = False
    actual["rows"] = actual["rows"][:1]
    actual["mapping"]["records"] = actual["mapping"]["records"][:1]
    result = compare_gene_effects(reference, actual, metric_id="gene_effect_rmse")
    assert result["status"] == "not_assessable" and result["value"] is None
    assert result["denominators"]["missing_rows"] == 2
    assert "incomplete_tested_family" in result["reason_codes"]


def test_finite_effect_inputs_cannot_overflow_the_mean_difference_result():
    reference = gene_table()
    actual = gene_table()
    for row in reference["rows"][:2]: row["effect"] = -1e308
    for row in actual["rows"][:2]: row["effect"] = 1e308
    result = compare_gene_effects(reference, actual, metric_id="gene_effect_absolute_difference")
    assert result["status"] == "failed" and result["value"] is None
    assert "nonfinite_metric_result" in result["reason_codes"]


def test_no_default_metric_or_unsupported_freeform_formula():
    with pytest.raises(ValueError, match="registered|metric"):
        compare_gene_effects(gene_table(), gene_table(), metric_id="eval(open('/etc/passwd'))")


def test_selected_set_threshold_must_match_frozen_rule():
    table = gene_table()
    table["family"]["eligible_ids"] = ["g1", "g2"]
    table["rows"][2].update(tested=False, status="filtered")
    table["rows"][0]["q_value"] = .02
    table["rows"][1].update(p_value=.06, q_value=.06)
    rule = {"field": "q_value", "operator": "less_equal", "threshold": .05,
            "probability_semantics": "benjamini_hochberg", "family_id": "synthetic-tested-family"}
    assert select_gene_ids(table, rule, frozen_rule=deepcopy(rule)) == ["g1"]
    changed = {**rule, "threshold": .1}
    with pytest.raises(ValueError, match="frozen|threshold"):
        select_gene_ids(table, changed, frozen_rule=rule)


@pytest.mark.parametrize("status", ["failed", "missing", "ok", "no_information"])
@pytest.mark.parametrize("field,semantics", [("p_value", "frequentist_p"), ("q_value", "benjamini_hochberg")])
def test_full_family_with_unknown_selected_probability_cannot_become_complete_identical_sets(status, field, semantics):
    table = gene_table()
    table["family"]["eligible_ids"] = ["g1", "g2"]
    table["rows"] = table["rows"][:2]
    table["mapping"]["records"] = table["mapping"]["records"][:2]
    table["rows"][0]["q_value"] = .02
    table["rows"][1].update(status=status, effect=None, p_value=None, q_value=None)
    assert validate_gene_table(table)["family"]["complete"] is True
    rule = {"field": field, "operator": "less_equal", "threshold": .05,
            "probability_semantics": semantics, "family_id": "synthetic-tested-family"}
    for actual in (table, deepcopy(table)):
        with pytest.raises(ValueError, match="unknown|missing|membership|probability|incomplete"):
            select_gene_ids(actual, rule, frozen_rule=deepcopy(rule))


@pytest.mark.parametrize("change", ["cohort", "contrast", "processing", "shrinkage", "mapping", "eligible_universe", "probability_semantics"])
def test_selected_gene_set_comparability_rejects_changed_context_despite_same_selected_ids(change):
    from devbench.analysis.rna_publication_metrics import validate_gene_comparability
    reference, actual = gene_table(), gene_table()
    if change in {"cohort", "contrast", "processing", "shrinkage"}:
        actual["alignment"][change] = "different"
    elif change == "mapping":
        actual["mapping"]["records"][0]["target_id"] = "g2"
        actual["mapping"]["records"][1]["target_id"] = "g1"
    elif change == "eligible_universe":
        actual["family"]["eligible_ids"].append("g4")
        actual["rows"].append({"feature_id": "g4", "tested": True, "status": "failed",
            "effect": None, "p_value": None, "q_value": None, "uncertainty": None})
        actual["mapping"]["records"].append({"source_id": "g4", "target_id": "g4", "status": "mapped"})
    else:
        actual["family"]["probability_semantics"]["q_value"] = "storey_q"
    assert [row["feature_id"] for row in reference["rows"] if row["q_value"] is not None and row["q_value"] < .05] == ["g1"]
    assert [row["feature_id"] for row in actual["rows"] if row["q_value"] is not None and row["q_value"] < .05] == ["g1"]
    result = validate_gene_comparability(reference, actual, comparison_kind="selected_gene_set")
    assert result["status"] == "not_assessable" and result["reason_codes"]


def test_set_context_comparability_does_not_require_effect_estimates():
    from devbench.analysis.rna_publication_metrics import validate_gene_comparability
    reference, actual = gene_table(), gene_table()
    for table in (reference, actual):
        for row in table["rows"]:
            row["effect"] = None
    result = validate_gene_comparability(reference, actual, comparison_kind="selected_gene_set")
    assert result == {"status": "comparable", "reason_codes": []}
