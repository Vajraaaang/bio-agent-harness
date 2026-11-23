"""Generic synthetic evidence views; no fit, biological golden, or model call."""
import copy
import hashlib
import json
from pathlib import Path

import pytest


def api():
    from devbench.analysis.episode_evidence_queries import query_result, summarize_result
    return query_result, summarize_result


def result():
    return {"diagnostics": {"status": "observed", "normalization": {"method": "retained_RLE"},
            "warnings": ["independent review absent"]}, "targets": [{"target_id": "contrast-x", "payload": {
        "gene_statistics": [
            {"gene_id": "plantZ", "hypothesis_id": "z", "estimate_status": "estimated", "values": [2.0], "p_value": .01, "q_value": .03},
            {"gene_id": "plantA", "hypothesis_id": "a", "estimate_status": "estimated", "values": [-2.0], "p_value": .01, "q_value": .03},
            {"gene_id": "plant0", "hypothesis_id": "0", "estimate_status": "unavailable", "values": [], "p_value": None, "q_value": None, "missing_reason": "all_zero"}],
        "multiplicity_scope": ["z", "a"], "analysis_layer": "raw_counts"}}]}


@pytest.fixture
def bound(tmp_path):
    contents = {"counts": "gene_id,r1,c1,r2,c2\nplantZ,0,8,2,6\nplantA,10,4,8,2\nplant0,0,0,0,0\n",
        "metadata": "sample_id,donor_id,condition\nr1,d1,R\nc1,d1,C\nr2,d2,R\nc2,d2,C\n",
        "annotation": json.dumps({"gene_annotations": {"plantZ": {"symbol": "Z"}, "plantA": [{"symbol": "A"}, {"symbol": "other"}]}})}
    bindings = {"sample_ids": ["r1", "c1", "r2", "c2"], "gene_ids": ["plantZ", "plantA", "plant0"], "measurement": "raw_counts"}
    for role, content in contents.items():
        path = tmp_path / (role + (".json" if role == "annotation" else ".csv"))
        path.write_text(content)
        bindings[role + "_path"] = str(path)
        bindings[role + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return bindings


def test_summary_is_generic_finite_and_does_not_mutate_result():
    _, summarize = api()
    source = result(); before = copy.deepcopy(source)
    summary = summarize(source)
    target = summary["targets"][0]
    assert target["attempted_gene_rows"] == 3
    assert target["tested_gene_rows"] == 2
    assert target["status_counts"] == {"estimated": 2, "unavailable": 1}
    assert target["quantiles"]["effect"]["median"] == 0
    assert summary["normalization"]["status"] == "available"
    assert summary["warnings"] == ["independent review absent"]
    assert source == before
    json.dumps(summary, allow_nan=False)


def test_stable_sort_filter_pagination_preserves_original_family():
    query, _ = api(); source = result(); before = copy.deepcopy(source)
    view = query(source, {"sort_by": "p_value", "limit": 1})
    assert view["rows"][0]["gene_id"] == "plantA"
    assert view["matched_rows"] == 3 and view["omitted_due_to_budget"]["rows"] == 2
    assert query(source, {"sort_by": "p_value", "offset": 1, "limit": 1})["rows"][0]["gene_id"] == "plantZ"
    aggregate = query(source, {"mode": "aggregate", "filters": {"q_value_lte": .05}, "limit": 1})
    assert aggregate["aggregate"]["attempted_gene_rows"] == 2
    assert aggregate["aggregate"]["quantiles"]["effect"]["median"] == 0
    assert source == before


@pytest.mark.parametrize("payload", [{"filters": {"eval": "x"}}, {"path": "/tmp/file"}, {"limit": 21}, {"limit": True}, {"sort_by": "secret"}, {"filters": {"q_value_lte": float("nan")}}])
def test_query_rejects_unregistered_fields_and_invalid_budgets(payload):
    query, _ = api()
    with pytest.raises(ValueError): query(result(), payload)


def test_raw_normalized_counts_and_zero_aware_pairs(bound):
    query, _ = api()
    view = query(result(), {"mode": "counts", "gene_ids": ["plantZ"], "samples": ["r1", "c1"]}, bindings=bound)
    assert view["rows"][0]["counts"] == {"r1": 0, "c1": 8}
    assert query(result(), {"mode": "counts", "layer": "normalized"}, bindings=bound)["status"] == "unavailable"
    normalized = query(result(), {"mode": "counts", "layer": "normalized", "gene_ids": ["plantZ"]}, bindings=dict(bound, size_factors={"r1": 1, "c1": 2, "r2": 1, "c2": 2}))
    assert normalized["rows"][0]["counts"]["c1"] == 4
    pairs = query(result(), {"mode": "paired", "gene_ids": ["plantZ"], "pairs": [{"reference": "r1", "comparison": "c1"}]}, bindings=bound)
    pair = pairs["rows"][0]["pairs"][0]
    assert pair["difference"] == 8 and pair["sign"] == 1
    assert pair["log2_ratio"] is None and pair["log2_ratio_missing_reason"] == "zero_in_pair"
    assert pair["comparison_share"] == pytest.approx(8 / 12)
    assert pair["reference_share"] == 0


def test_input_hash_and_sample_gene_axes_must_match(bound):
    query, _ = api()
    for changed in ({"counts_sha256": "0" * 64}, {"sample_ids": ["wrong"]}, {"gene_ids": ["wrong"]}):
        with pytest.raises(ValueError, match="hash|axis"):
            query(result(), {"mode": "counts"}, bindings=dict(bound, **changed))
    metadata = Path(bound["metadata_path"]); metadata.write_text(metadata.read_text().replace("r1,", "wrong,"))
    altered = dict(bound, metadata_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="axis"):
        query(result(), {"mode": "counts"}, bindings=altered)


def test_annotation_missing_ambiguity_is_explicit(bound):
    query, _ = api()
    view = query(result(), {"mode": "annotations"}, bindings=bound)
    rows = {row["gene_id"]: row for row in view["rows"]}
    assert rows["plantZ"]["annotation_status"] == "available"
    assert rows["plantA"]["annotation_status"] == "ambiguous"
    assert rows["plant0"]["annotation_status"] == "unavailable"
    assert rows["plant0"]["annotation"] is None


def test_output_is_bounded_and_budget_omission_not_unavailability():
    query, summarize = api(); source = result()
    source["targets"][0]["payload"]["gene_statistics"] *= 50
    for row in source["targets"][0]["payload"]["gene_statistics"]:
        row["missing_reason"] = "x" * 18000
    view = query(source, {"limit": 20})
    assert len(json.dumps(view, allow_nan=False).encode()) <= 12 * 1024
    assert len(view["rows"]) <= 20
    assert view["status"] == "available" and view["omitted_due_to_budget"]["fields"]
    assert len(json.dumps(summarize(source), allow_nan=False).encode()) <= 12 * 1024


def test_gene_values_and_full_requested_pair_records_are_not_diagnostic_summaries(bound):
    query, _ = api()
    view = query(result(), {})
    assert next(row for row in view["rows"] if row["gene_id"] == "plantZ")["values"] == [2.0]
    pairs = [{"reference": "r1", "comparison": "c1"}] * 12
    view = query(result(), {"mode": "paired", "gene_ids": ["plantZ"], "pairs": pairs}, bindings=bound)
    assert len(view["rows"][0]["pairs"]) == 12
    assert view["rows"][0]["pairs"][0]["difference"] == 8


def test_source_retained_normalization_is_used_with_exact_axis(bound):
    query, _ = api(); source = result()
    source["diagnostics"]["normalization"]["per_sample_size_factors"] = {"r1": 1, "c1": 2, "r2": 1, "c2": 2}
    view = query(source, {"mode": "counts", "layer": "normalized", "gene_ids": ["plantZ"]}, bindings=bound)
    assert view["rows"][0]["counts"]["c1"] == 4
    source["diagnostics"]["normalization"]["per_sample_size_factors"] = {"pooled_donor": 1}
    assert query(source, {"mode": "counts", "layer": "normalized"}, bindings=bound)["status"] == "unavailable"


def test_count_diagnostics_work_before_fit_without_creating_statistics(bound):
    query, _ = api()
    view = query({"diagnostics": {"status": "audited"}}, {"mode": "counts", "gene_ids": ["plantZ"]}, bindings=bound)
    assert view["rows"][0]["counts"]["c1"] == 8
    assert "p_value" not in view["rows"][0]
    unavailable = query(result(), {"mode": "gene_statistics", "target_id": "absent"})
    assert unavailable["status"] == "unavailable"


def test_annotation_duplicate_mapping_keys_rejected(bound):
    query, _ = api(); path = Path(bound["annotation_path"])
    path.write_text('{"gene_annotations":{"plantZ":"first","plantZ":"second"}}')
    altered = dict(bound, annotation_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="duplicate"):
        query(result(), {"mode": "annotations"}, bindings=altered)


def test_continuous_counts_and_overflow_normalization_are_unavailable(bound):
    query, _ = api()
    assert query(result(), {"mode": "counts"}, bindings=dict(bound, measurement="continuous"))["status"] == "unavailable"
    source = result(); source["diagnostics"]["normalization"]["per_sample_size_factors"] = dict.fromkeys(bound["sample_ids"], 5e-324)
    assert query(source, {"mode": "counts", "layer": "normalized"}, bindings=bound)["status"] == "unavailable"


def test_huge_annotation_field_omission_still_allows_pagination(bound):
    query, _ = api(); path = Path(bound["annotation_path"])
    path.write_text(json.dumps({"gene_annotations": {"plantZ": {str(i): "x" * 100 for i in range(400)}}}))
    altered = dict(bound, annotation_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    view = query(result(), {"mode": "annotations", "gene_ids": ["plantZ"]}, bindings=altered)
    assert view["rows"][0]["gene_id"] == "plantZ"
    assert view["rows"][0]["annotation_status"] == "available"
    assert "annotation" not in view["rows"][0]
    assert "rows[0].annotation" in view["omitted_due_to_budget"]["fields"]
    assert view["next_offset"] is None
    assert len(json.dumps(view).encode()) <= 10 * 1024


def test_prepared_zarr_explicitly_unavailable_without_conversion(bound, tmp_path):
    query, _ = api(); path = tmp_path / "prepared.zarr"; path.mkdir()
    view = query(result(), {"mode": "counts"}, bindings=dict(bound, counts_path=str(path)))
    assert view["status"] == "unavailable" and view["missing_reason"] == "unsupported_counts_transport"


def test_count_reader_preserves_legacy_integer_input_convention(bound):
    query, _ = api(); path = Path(bound["counts_path"])
    path.write_text(path.read_text().replace("plantZ,0,8", "plantZ,0,8.0"))
    altered = dict(bound, counts_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="raw_counts"):
        query(result(), {"mode": "counts"}, bindings=altered)


def test_normalization_explicit_observation_axis_cannot_disagree(bound):
    query, _ = api(); source = result()
    source["diagnostics"]["normalization"].update(per_sample_size_factors=dict.fromkeys(bound["sample_ids"], 1), observation_ids=["other"])
    assert query(source, {"mode": "counts", "layer": "normalized"}, bindings=bound)["status"] == "unavailable"


def test_paired_sign_counts_and_absolute_difference_dominance(bound):
    query, _ = api()
    payload = {"mode": "paired", "pairs": [{"reference": "r1", "comparison": "c1"}, {"reference": "r2", "comparison": "c2"}]}
    view = query(result(), payload, bindings=bound)
    rows = {row["gene_id"]: row for row in view["rows"]}
    assert rows["plantZ"]["pair_sign_counts"] == {"positive": 2, "negative": 0, "zero": 0}
    assert rows["plantZ"]["descriptive_dominance"] == pytest.approx(8 / 12)
    assert rows["plantZ"]["absolute_difference_shares"] == pytest.approx([8 / 12, 4 / 12])
    assert rows["plant0"]["descriptive_dominance"] is None
    assert rows["plant0"]["descriptive_dominance_missing_reason"] == "all_pair_differences_zero"
    assert rows["plantA"]["pair_sign_counts"] == {"positive": 0, "negative": 2, "zero": 0}


def declared_bindings(bound):
    from devbench.analysis.checkpoints import digest
    task = {"observation_id_label": "sample_id", "population_unit_label": "donor_id",
        "analysis_columns": {"condition": "condition"},
        "context": {"dependence": "paired", "requested_condition_contrast": {"numerator": "C", "denominator": "R"}},
        "questions": [{"target_id": "contrast-x", "contrast": "C minus R"}]}
    return dict(bound, public_task=task, public_task_sha256=digest(task))


def test_pairs_derive_only_from_bound_declarations_and_metadata(bound):
    query, _ = api()
    bindings = declared_bindings(bound)
    view = query(result(), {"mode": "paired", "target_id": "contrast-x", "gene_ids": ["plantZ"]}, bindings=bindings)
    assert [(p["reference"], p["comparison"]) for p in view["rows"][0]["pairs"]] == [("r1", "c1"), ("r2", "c2")]
    assert view["pairing_scope"] == "hash_bound_metadata_declarations_not_biological_identity_or_independence_validation"
    assert view["pairing_declarations"]["public_task_sha256"] == bindings["public_task_sha256"]
    assert view["pairing_declarations"]["distinct_declared_units"] == 2


@pytest.mark.parametrize("change,match", [("hash", "hash"), ("condition", "columns"), ("dependence", "paired"),
    ("contrast", "contrast"), ("target", "target"), ("duplicate", "ambiguous"), ("incomplete", "incomplete")])
def test_derived_pairs_reject_missing_ambiguous_or_unbound_declarations(bound, change, match):
    from devbench.analysis.checkpoints import digest
    query, _ = api(); bindings = declared_bindings(bound)
    task = bindings["public_task"]
    if change == "hash": bindings["public_task_sha256"] = "0" * 64
    elif change == "condition": task["analysis_columns"] = {}
    elif change == "dependence": task["context"]["dependence"] = "unknown"
    elif change == "contrast": task["context"].pop("requested_condition_contrast")
    elif change == "target": task["questions"] = [{"target_id": "another"}]
    elif change in {"duplicate", "incomplete"}:
        path = Path(bindings["metadata_path"])
        path.write_text(path.read_text().replace("c2,d2,C", "c2,d2,R" if change == "duplicate" else "c2,d3,C"))
        bindings["metadata_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if change != "hash": bindings["public_task_sha256"] = digest(task)
    with pytest.raises(ValueError, match=match):
        query(result(), {"mode": "paired", "target_id": "contrast-x"}, bindings=bindings)


def test_derived_normalized_pairs_require_exact_retained_factor_axis(bound):
    query, _ = api(); bindings = declared_bindings(bound); source = result()
    source["diagnostics"]["normalization"]["per_sample_size_factors"] = {"r1": 1, "c1": 2, "r2": 1, "c2": 2}
    view = query(source, {"mode": "paired", "layer": "normalized", "gene_ids": ["plantZ"]}, bindings=bindings)
    assert view["rows"][0]["pairs"][0]["difference"] == 4
    source["diagnostics"]["normalization"]["observation_ids"] = ["pooled1", "pooled2"]
    unavailable = query(source, {"mode": "paired", "layer": "normalized"}, bindings=bindings)
    assert unavailable["status"] == "unavailable" and unavailable["missing_reason"] == "retained_normalization_factors_unavailable"
