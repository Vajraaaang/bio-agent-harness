"""Reviewed finite arithmetic over explicit publication reference evidence.

No function selects a method, threshold or scientific winner. Missing evidence
stays missing. A reference agreement statistic is not biological truth accuracy.
"""
from __future__ import annotations

from copy import deepcopy
import math

from devharness_core.provenance import canonical_json

GENE_TABLE_FORMAT = "devbench.rna-gene-table/2"
MAX_FEATURES = 100_000
MAX_TABLE_BYTES = 16_000_000
METRIC_FORMULAS = {name: "1" for name in (
    "gene_effect_absolute_difference", "gene_effect_direction_agreement", "gene_effect_rmse",
    "gene_table_coverage", "gene_set_precision", "gene_set_recall", "gene_set_f1", "gene_set_jaccard")}
_ALIGNMENT = {"estimand_id", "biological_unit", "contrast", "cohort", "processing", "effect_scale", "hypothesis", "shrinkage"}
_FAILED = {"missing", "filtered", "failed", "not_estimable", "not_tested"}


def _keys(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("Use the closed typed publication table/metric fields")


def _text(value, maximum=200):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError("Use bounded nonempty publication identifiers")


def _ids(values, *, allow_empty=True):
    if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > MAX_FEATURES or (not allow_empty and not values):
        raise ValueError("Use a bounded eligible feature universe")
    result = list(values)
    for value in result:
        _text(value)
    if len(set(result)) != len(result):
        raise ValueError("Publication feature IDs must be unique")
    return result


def _number(value, *, probability=False, nullable=True):
    if value is None and nullable:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or (probability and not 0 <= value <= 1):
        raise ValueError("Publication statistics require finite numbers with declared probability bounds")


def _uncertainty(value):
    if value is None:
        return
    _keys(value, {"lower", "upper", "level", "kind"})
    for key in ("lower", "upper", "level"):
        _number(value[key], nullable=False)
    if value["lower"] > value["upper"] or not 0 < value["level"] < 1 or value["kind"] not in {"confidence", "credible", "range"}:
        raise ValueError("Invalid declared uncertainty interval")


def validate_gene_table(value):
    """Validate full retained rows without changing values or adding estimates."""
    _keys(value, {"format", "table_id", "alignment", "family", "mapping", "rows", "limitations"})
    if value["format"] != GENE_TABLE_FORMAT:
        raise ValueError("Use the versioned typed gene table")
    _text(value["table_id"])
    _text(value["limitations"], 12_000)
    _keys(value["alignment"], _ALIGNMENT)
    for item in value["alignment"].values():
        if item is not None:
            _text(item, 4000)
    family = value["family"]
    _keys(family, {"family_id", "eligible_ids", "complete", "probability_semantics", "failed_hypothesis_policy"})
    _text(family["family_id"])
    eligible = set(_ids(family["eligible_ids"]))
    if type(family["complete"]) is not bool or family["failed_hypothesis_policy"] not in {
            "retain_null_use_one_for_bh_denominator", "exclude_failed_hypotheses", "unknown"}:
        raise ValueError("Declare complete tested-family and failed-hypothesis policies")
    semantics = family["probability_semantics"]
    _keys(semantics, {"p_value", "q_value"})
    if semantics["p_value"] not in {"frequentist_p", "conditional_p", "selection_adjusted_p", "unknown", "not_applicable"} or semantics["q_value"] not in {
            "benjamini_hochberg", "benjamini_yekutieli", "storey_q", "unknown", "not_applicable"}:
        raise ValueError("Declare the actual probability semantics; column names are insufficient")
    rows = value["rows"]
    if type(rows) is not list or len(rows) > MAX_FEATURES:
        raise ValueError("Gene table exceeds the feature bound")
    observed = _ids([row.get("feature_id") if type(row) is dict else None for row in rows])
    tested = set()
    for row in rows:
        _keys(row, {"feature_id", "tested", "status", "effect", "p_value", "q_value", "uncertainty"})
        if type(row["tested"]) is not bool or row["status"] not in (_FAILED | {"ok", "no_information"}):
            raise ValueError("Preserve actual typed tested/failed/filtered statuses")
        if row["tested"]:
            tested.add(row["feature_id"])
        elif row["status"] in {"ok", "no_information"}:
            raise ValueError("An untested row cannot contain a successful estimate")
        for name in ("effect", "p_value", "q_value"):
            _number(row[name], probability=name != "effect")
        _uncertainty(row["uncertainty"])
        if row["status"] in _FAILED and any(row[name] is not None for name in ("effect", "p_value", "q_value", "uncertainty")):
            raise ValueError("Failed, missing or filtered estimates must retain null values")
        if row["status"] == "filtered" and row["tested"]:
            raise ValueError("A filtered feature cannot be counted as tested")
        if semantics["q_value"] == "benjamini_hochberg" and row["q_value"] is not None:
            if row["p_value"] is None or row["q_value"] + 1e-15 < row["p_value"]:
                raise ValueError("The explicit BH contract requires q >= p")
    if not tested <= eligible or (family["complete"] and tested != eligible):
        raise ValueError("A complete tested family must retain every eligible hypothesis, including failures")
    mapping = value["mapping"]
    _keys(mapping, {"namespace", "annotation_ref", "records"})
    _text(mapping["namespace"])
    if mapping["annotation_ref"] is not None:
        _keys(mapping["annotation_ref"], {"path", "sha256", "size_bytes"})
    if type(mapping["records"]) is not list or len(mapping["records"]) > MAX_FEATURES:
        raise ValueError("Mapping records exceed the feature bound")
    sources, targets = [], []
    for item in mapping["records"]:
        _keys(item, {"source_id", "target_id", "status"})
        _text(item["source_id"])
        sources.append(item["source_id"])
        if item["status"] == "mapped":
            _text(item["target_id"])
            targets.append(item["target_id"])
        elif item["status"] not in {"unmapped", "ambiguous"} or item["target_id"] is not None:
            raise ValueError("Unknown mapping outcomes remain explicit, without invented targets")
    if len(set(sources)) != len(sources) or len(set(targets)) != len(targets) or set(sources) != set(observed):
        raise ValueError("Gene mappings require unique source and unambiguous target IDs for every retained row")
    if len(canonical_json(value)) > MAX_TABLE_BYTES:
        raise ValueError("Gene table exceeds its aggregate byte bound")
    return deepcopy(value)


def audit_bh_family(table):
    """Audit the declared full family; never replace supplied p/q values."""
    table = validate_gene_table(table)
    family = table["family"]
    base = {"status": "not_assessable", "family_size": len(family["eligible_ids"]),
            "audited_count": 0, "values_match": None, "reason_codes": []}
    semantics = family["probability_semantics"]["q_value"]
    if semantics != "benjamini_hochberg":
        return {**base, "status": "not_assessable" if semantics == "unknown" else "not_applicable",
                "reason_codes": ["unknown_probability_semantics" if semantics == "unknown" else "not_bh_adjustment"]}
    if not family["complete"]:
        return {**base, "reason_codes": ["incomplete_tested_family"]}
    if family["failed_hypothesis_policy"] == "unknown":
        return {**base, "reason_codes": ["unknown_failed_hypothesis_policy"]}
    rows = {row["feature_id"]: row for row in table["rows"]}
    hypotheses = [rows[identity] for identity in family["eligible_ids"]]
    if family["failed_hypothesis_policy"] == "exclude_failed_hypotheses":
        hypotheses = [row for row in hypotheses if row["p_value"] is not None]
    if not hypotheses:
        return {**base, "reason_codes": ["empty_tested_family"]}
    ordered = sorted(enumerate(hypotheses), key=lambda item: 1 if item[1]["p_value"] is None else item[1]["p_value"])
    adjusted = [None] * len(hypotheses)
    running = 1.0
    for rank in range(len(ordered), 0, -1):
        index, row = ordered[rank - 1]
        probability = 1.0 if row["p_value"] is None else row["p_value"]
        running = min(running, probability * len(ordered) / rank)
        adjusted[index] = running
    assessed = [(row, expected) for row, expected in zip(hypotheses, adjusted) if row["p_value"] is not None]
    if not assessed or any(row["q_value"] is None for row, _ in assessed):
        return {**base, "reason_codes": ["missing_adjusted_probabilities"]}
    valid = all(math.isclose(row["q_value"], expected, rel_tol=1e-10, abs_tol=1e-12) for row, expected in assessed)
    return {**base, "status": "computed" if valid else "failed", "audited_count": len(assessed),
            "values_match": valid, "reason_codes": [] if valid else ["bh_values_do_not_match_full_family"]}


def gene_set_agreement(reference_ids, actual_ids, eligible_ids, *, reference_complete):
    """Descriptive agreement with an explicitly complete eligible reference set."""
    if type(reference_complete) is not bool:
        raise ValueError("Reference completeness must be explicitly declared")
    reference, actual, eligible = (set(_ids(values)) for values in (reference_ids, actual_ids, eligible_ids))
    if not (reference | actual) <= eligible:
        raise ValueError("Reference/actual IDs outside the eligible universe require explicit resolution")
    overlap, union = len(reference & actual), len(reference | actual)
    result = {"status": "computed", "overlap": overlap, "precision": None, "recall": None, "f1": None,
        "jaccard": None, "denominators": {"reference": len(reference), "actual": len(actual), "union": union, "eligible": len(eligible)},
        "coverage": {"source_reference_ids": len(reference), "eligible_reference_ids": len(reference),
                     "eligible_actual_ids": len(actual), "unknown_reference_membership": 0 if reference_complete else len(eligible - reference)},
        "reason_codes": [], "scope": "publication_reference_agreement", "biological_truth_accuracy": None}
    if not reference_complete:
        return {**result, "status": "not_assessable", "reason_codes": ["incomplete_reference_membership"]}
    result["precision"] = overlap / len(actual) if actual else None
    result["recall"] = overlap / len(reference) if reference else None
    if not reference or not actual:
        return {**result, "status": "not_assessable", "reason_codes": ["zero_set_denominator"]}
    return {**result, "f1": 2 * overlap / (len(reference) + len(actual)), "jaccard": overlap / union}


def validate_gene_comparability(reference, actual, *, comparison_kind):
    """Validate selected numerical context without requiring effect estimates."""
    if comparison_kind not in {"effects", "gene_table_coverage", "selected_gene_set"}:
        raise ValueError("Select a registered gene-table comparability scope")
    reference, actual = validate_gene_table(reference), validate_gene_table(actual)
    mismatches = []
    fields = _ALIGNMENT if comparison_kind != "gene_table_coverage" else {"estimand_id", "biological_unit", "cohort", "processing"}
    for field in sorted(fields):
        left, right = reference["alignment"][field], actual["alignment"][field]
        if left is None or right is None or left == "unknown" or right == "unknown":
            mismatches.append("unknown:" + field)
        elif left != right:
            mismatches.append("incompatible:" + field)
    if reference["family"]["family_id"] != actual["family"]["family_id"] or set(reference["family"]["eligible_ids"]) != set(actual["family"]["eligible_ids"]):
        mismatches.append("incompatible:tested_family")
    if not reference["family"]["complete"] or not actual["family"]["complete"]:
        mismatches.append("incomplete_tested_family")
    if reference["mapping"]["namespace"] != actual["mapping"]["namespace"] or reference["mapping"]["annotation_ref"] != actual["mapping"]["annotation_ref"]:
        mismatches.append("incompatible:mapping_lineage")
    if any(item["status"] != "mapped" for table in (reference, actual) for item in table["mapping"]["records"]):
        mismatches.append("unknown:mapping_membership")
    reference_mapping = {item["source_id"]: item["target_id"] for item in reference["mapping"]["records"]}
    actual_mapping = {item["source_id"]: item["target_id"] for item in actual["mapping"]["records"]}
    if any(reference_mapping[key] != actual_mapping[key] for key in set(reference_mapping) & set(actual_mapping)):
        mismatches.append("incompatible:mapping_outcomes")
    if comparison_kind == "selected_gene_set":
        left, right = reference["family"]["probability_semantics"], actual["family"]["probability_semantics"]
        if left != right:
            mismatches.append("incompatible:probability_semantics")
        if any(value in {"unknown", "not_applicable"} for semantics in (left, right) for value in semantics.values()):
            mismatches.append("unknown:probability_semantics")
        if reference["family"]["failed_hypothesis_policy"] != actual["family"]["failed_hypothesis_policy"]:
            mismatches.append("incompatible:failed_hypothesis_policy")
    return {"status": "not_assessable" if mismatches else "comparable", "reason_codes": mismatches}


def compare_gene_effects(reference, actual, *, metric_id):
    """Selected effect/table arithmetic with metric-specific alignment checks."""
    if metric_id not in {"gene_effect_absolute_difference", "gene_effect_direction_agreement", "gene_effect_rmse", "gene_table_coverage"}:
        raise ValueError("Select an implemented registered gene-effect metric")
    reference, actual = validate_gene_table(reference), validate_gene_table(actual)
    mismatches = validate_gene_comparability(reference, actual,
        comparison_kind="gene_table_coverage" if metric_id == "gene_table_coverage" else "effects")["reason_codes"]
    left = {row["feature_id"]: row for row in reference["rows"]}
    right = {row["feature_id"]: row for row in actual["rows"]}
    matched = sorted(set(left) & set(right))
    pairs = [(left[key]["effect"], right[key]["effect"]) for key in matched
             if left[key]["status"] == right[key]["status"] == "ok" and left[key]["effect"] is not None and right[key]["effect"] is not None]
    denominators = {"reference_rows": len(left), "actual_rows": len(right), "matched_rows": len(matched),
                    "assessed_rows": len(pairs), "missing_rows": len(set(left) | set(right)
                        | set(reference["family"]["eligible_ids"]) | set(actual["family"]["eligible_ids"])) - len(pairs)}
    result = {"metric_id": metric_id, "formula_version": "1", "status": "not_assessable", "value": None,
              "units": "fraction" if metric_id in {"gene_effect_direction_agreement", "gene_table_coverage"} else actual["alignment"]["effect_scale"],
              "denominators": denominators, "reason_codes": mismatches, "scope": "publication_reference_agreement",
              "scientific_optimality_established": False}
    if mismatches:
        return result
    if not pairs:
        return {**result, "reason_codes": ["no_estimable_matched_effects"]}
    if metric_id == "gene_effect_direction_agreement":
        value = sum((x > 0) - (x < 0) == (y > 0) - (y < 0) for x, y in pairs) / len(pairs)
    elif metric_id == "gene_table_coverage":
        value = len(pairs) / len(reference["family"]["eligible_ids"]) if reference["family"]["eligible_ids"] else None
    elif metric_id == "gene_effect_absolute_difference":
        value = math.fsum(abs(x - y) / len(pairs) for x, y in pairs)
    else:
        # hypot avoids overflowing a square before the finite-output check.
        value = math.hypot(*(x - y for x, y in pairs)) / math.sqrt(len(pairs))
    if value is None or not math.isfinite(value):
        return {**result, "status": "failed", "reason_codes": ["nonfinite_metric_result"]}
    return {**result, "status": "computed", "value": value}


def select_gene_ids(table, rule, *, frozen_rule):
    """Apply one explicitly frozen selection rule without sensitivity fishing."""
    table = validate_gene_table(table)
    _keys(rule, {"field", "operator", "threshold", "probability_semantics", "family_id"})
    if rule != frozen_rule:
        raise ValueError("Selection threshold/rule differs from the frozen pre-run rule")
    if rule["field"] not in {"p_value", "q_value"} or rule["operator"] != "less_equal":
        raise ValueError("Use a reviewed explicit probability selection rule")
    _number(rule["threshold"], probability=True, nullable=False)
    if rule["probability_semantics"] != table["family"]["probability_semantics"][rule["field"]] or rule["family_id"] != table["family"]["family_id"]:
        raise ValueError("Selection probability semantics/tested family differ")
    if not table["family"]["complete"]:
        raise ValueError("Selected-set assessment requires the complete tested family")
    eligible = set(table["family"]["eligible_ids"])
    if any(row[rule["field"]] is None for row in table["rows"] if row["feature_id"] in eligible):
        raise ValueError("Selected-set membership is unknown for an eligible hypothesis with a missing probability")
    return [row["feature_id"] for row in table["rows"] if row["tested"] and row["status"] == "ok"
            and row[rule["field"]] is not None and row[rule["field"]] <= rule["threshold"]]
