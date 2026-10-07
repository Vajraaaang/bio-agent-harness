"""Independent retention diagnostics for bounded synthetic observation JSON."""
from copy import deepcopy
import json
import math

import pytest

from devbench.analysis.method_validation import coordinator_measurement_provenance, validate_outputs
from devharness_core.methods import MethodContract
from devharness_core.provenance import canonical_json
from tests.test_extensible_methods import custom


INVARIANTS = (
    "observation_ids_unique", "retained_observation_ids_unique",
    "retained_observation_ids_subset_of_input", "retained_observation_ids_in_input_order",
    "immutable_observation_rows_selected",
)


def _contract(tmp_path, metrics=("qc",), *, sample=True):
    value, _, _ = custom(tmp_path, comparator="observation_selection")
    value["validation"].update(qc_metrics=list(metrics), invariants=list(INVARIANTS))
    if sample:
        value["validation"]["sample_key"] = "sample"
    return value


def _data():
    return {"format": "devbench.qc-observations/1", "observations": [
        {"cell_id": "a", "sample": "one", "qc": 0.0, "constant": 7},
        {"cell_id": "b", "sample": "one", "qc": 2.0, "constant": 7},
        {"cell_id": "c", "sample": "two", "qc": 4.0, "constant": 7},
        {"cell_id": "d", "sample": "two", "qc": 6.0, "constant": 7},
    ]}


def _preview(tmp_path, *, data=None, output=None, contract=None):
    data = _data() if data is None else data
    output = {"retained_ids": ["a", "b"]} if output is None else output
    contract = _contract(tmp_path) if contract is None else contract
    before, after = tmp_path / "input.json", tmp_path / "output.json"
    before.write_text(json.dumps(data), encoding="utf-8")
    after.write_text(json.dumps(output), encoding="utf-8")
    result = validate_outputs(contract, {"dataset": before}, {"dataset": after})
    assert json.loads(before.read_text()) == data
    assert json.loads(after.read_text()) == output
    canonical_json(result)
    return result


def test_closed_selection_computes_hand_calculated_reference_diagnostics(tmp_path):
    contract = _contract(tmp_path, ("qc", "constant"))
    assert MethodContract.from_dict(contract).to_dict() == contract
    result = _preview(tmp_path, contract=contract)
    assert result["passed"] is True
    assert result["comparator"] == "observation_selection"
    assert result["invariants"] == dict.fromkeys(INVARIANTS, True)
    assert result["shape"] == {"before": [4, 2], "after": [2, 2]}
    assert result["removed_ids"] == ["c", "d"]
    assert result["added_ids"] == []
    assert result["reference_qc_quantiles"]["qc"]["before"] == pytest.approx([0, 1.5, 3, 4.5, 6])
    assert result["reference_qc_quantiles"]["qc"]["after"] == pytest.approx([0, .5, 1, 1.5, 2])
    assert result["reference_qc_means"]["qc"] == pytest.approx({
        "before": 3, "after": 1, "absolute_standardized_shift": 2 / math.sqrt(5)})
    assert result["reference_qc_means"]["constant"] == {
        "before": 7, "after": 7, "absolute_standardized_shift": 0}
    assert result["samples"]["two"] == {"before": 2, "retained": 0, "removed": 2, "removal_rate": 1.0}
    assert any("selection bias" in warning for warning in result["warnings"])
    assert "raw counts" in " ".join(result["limitations"])
    assert "retained_count_matrix_unchanged" not in result["invariants"]


@pytest.mark.parametrize("section", ["inputs", "outputs"])
@pytest.mark.parametrize("invalid", ["wrong_type", "wrong_name", "optional"])
def test_contract_requires_named_required_json_dataset(tmp_path, section, invalid):
    value = _contract(tmp_path)
    if invalid == "wrong_type":
        value[section]["dataset"]["artifact_type"] = "anndata"
    elif invalid == "wrong_name":
        value[section]["observations"] = value[section].pop("dataset")
    else:
        value[section]["dataset"]["required"] = False
    with pytest.raises(ValueError):
        MethodContract.from_dict(value)


@pytest.mark.parametrize("change", ["missing_qc", "identity_metric", "sample_metric", "count_invariant"])
def test_contract_rejects_unavailable_scientific_assertions(tmp_path, change):
    value = _contract(tmp_path)
    if change == "missing_qc":
        value["validation"]["qc_metrics"] = []
    elif change == "identity_metric":
        value["validation"]["qc_metrics"] = ["cell_id"]
    elif change == "sample_metric":
        value["validation"]["qc_metrics"] = ["sample"]
    else:
        value["validation"]["invariants"] = ["retained_count_matrix_unchanged"]
    with pytest.raises(ValueError):
        MethodContract.from_dict(value)


@pytest.mark.parametrize("comparator", ["json", "file", "single_cell_qc"])
def test_new_invariants_do_not_expand_existing_comparators(tmp_path, comparator):
    value = _contract(tmp_path)
    value["validation"]["comparator"] = comparator
    if comparator == "single_cell_qc":
        for section in ("inputs", "outputs"):
            value[section]["dataset"]["artifact_type"] = "anndata"
    with pytest.raises(ValueError):
        MethodContract.from_dict(value)


@pytest.mark.parametrize("invalid", [
    [], {}, {"format": "other", "observations": []},
    {"format": "devbench.qc-observations/1", "observations": "wrong"},
    {"format": "devbench.qc-observations/1", "observations": []},
    {"format": "devbench.qc-observations/1", "observations": [1]},
    {"format": "devbench.qc-observations/1", "observations": [{"qc": 1}]},
])
def test_malformed_input_is_rejected(tmp_path, invalid):
    with pytest.raises(ValueError):
        _preview(tmp_path, data=invalid)


@pytest.mark.parametrize("change", [
    "duplicate_ids", "numeric_id", "empty_id", "long_id", "long_column", "extra_top_level",
    "missing_measurement", "missing_qc", "missing_sample", "numeric_sample", "empty_sample",
    "string_measurement", "bool_measurement", "null_measurement", "nested_measurement",
    "array_measurement", "nan_measurement", "infinite_measurement", "unrepresentable_integer",
])
def test_invalid_observation_columns_fail_closed(tmp_path, change):
    data = _data()
    rows = data["observations"]
    if change == "duplicate_ids":
        rows[1]["cell_id"] = rows[0]["cell_id"]
    elif change == "numeric_id":
        rows[0]["cell_id"] = 1
    elif change == "empty_id":
        rows[0]["cell_id"] = ""
    elif change == "long_id":
        rows[0]["cell_id"] = "a" * 4097
    elif change == "long_column":
        for row in rows:
            row["q" * 4097] = 1
    elif change == "extra_top_level":
        data["reference_scores"] = {"accuracy": 1}
    elif change == "missing_measurement":
        rows[0].pop("constant")
    elif change == "missing_qc":
        for row in rows:
            row.pop("qc")
    elif change == "missing_sample":
        for row in rows:
            row.pop("sample")
    elif change == "numeric_sample":
        rows[0]["sample"] = 1
    elif change == "empty_sample":
        rows[0]["sample"] = ""
    else:
        rows[0]["qc"] = {
            "string_measurement": "1", "bool_measurement": True, "null_measurement": None,
            "nested_measurement": {"value": 1}, "array_measurement": [1],
            "nan_measurement": float("nan"), "infinite_measurement": float("inf"),
            "unrepresentable_integer": 10 ** 400,
        }[change]
    with pytest.raises(ValueError):
        _preview(tmp_path, data=data)


@pytest.mark.parametrize("output", [
    [], {}, {"retained_ids": "a"}, {"retained_ids": ["a", "a"]},
    {"retained_ids": ["a", "foreign"]}, {"retained_ids": ["b", "a"]},
    {"retained_ids": [1]}, {"retained_ids": [True]},
    {"retained_ids": ["a"], "qc": 0},
    {"retained_ids": ["a"], "reference_qc_means": {"qc": {"after": 0}}},
    {"retained_ids": ["a"], "observations": [{"cell_id": "a", "sample": "changed", "qc": 0}]},
    {"retained_ids": ["a"], "comparison_measurement_provenance": {}},
])
def test_candidate_cannot_change_identity_rows_or_submit_metrics(tmp_path, output):
    with pytest.raises(ValueError):
        _preview(tmp_path, output=output)


@pytest.mark.parametrize("section", ["inputs", "outputs"])
@pytest.mark.parametrize("change", ["missing", "undeclared", "wrong_type", "optional"])
def test_runtime_checks_named_dataset_contract_without_prior_parsing(tmp_path, section, change):
    contract = _contract(tmp_path)
    before, after = tmp_path / "input.json", tmp_path / "output.json"
    before.write_text(json.dumps(_data()))
    after.write_text(json.dumps({"retained_ids": ["a"]}))
    bindings = {"inputs": {"dataset": before}, "outputs": {"dataset": after}}
    if change == "missing":
        bindings[section] = {}
    elif change == "undeclared":
        bindings[section]["other"] = before
    elif change == "wrong_type":
        contract[section]["dataset"]["artifact_type"] = "file"
    else:
        contract[section]["dataset"]["required"] = False
    with pytest.raises(ValueError):
        validate_outputs(contract, bindings["inputs"], bindings["outputs"])


@pytest.mark.parametrize("budget", ["rows", "columns", "bytes"])
def test_observation_budgets_are_enforced(tmp_path, budget):
    data = _data()
    if budget == "rows":
        data["observations"] = [{"cell_id": str(i), "qc": i} for i in range(10001)]
    elif budget == "columns":
        data["observations"] = [{"cell_id": "a", **{"q" + str(i): i for i in range(129)}}]
    else:
        data["observations"][0]["cell_id"] = "a" * 16_000_001
    with pytest.raises(ValueError):
        _preview(tmp_path, data=data, contract=_contract(tmp_path, sample=False))


def test_maximum_rows_and_columns_are_accepted(tmp_path):
    data = {"format": "devbench.qc-observations/1", "observations": [
        {"cell_id": str(i), "qc": i} for i in range(10000)]}
    result = _preview(tmp_path, data=data, output={"retained_ids": ["0", "9999"]},
                      contract=_contract(tmp_path, sample=False))
    assert result["shape"]["before"] == [10000, 1]
    assert result["reference_qc_means"]["qc"]["absolute_standardized_shift"] == pytest.approx(0)
    metrics = ["q" + str(i) for i in range(128)]
    data["observations"] = [{"cell_id": "a", **dict.fromkeys(metrics, 1)}]
    result = _preview(tmp_path, data=data, output={"retained_ids": ["a"]},
                      contract=_contract(tmp_path, metrics, sample=False))
    assert len(result["reference_qc_means"]) == 128


@pytest.mark.parametrize("raw", [
    '{"retained_ids":["a"],"retained_ids":["b"]}',
    '{"retained_ids":[NaN]}',
])
def test_duplicate_keys_and_nonfinite_json_are_rejected(tmp_path, raw):
    before, after = tmp_path / "input.json", tmp_path / "output.json"
    before.write_text(json.dumps(_data()))
    after.write_text(raw)
    with pytest.raises(ValueError):
        validate_outputs(_contract(tmp_path), {"dataset": before}, {"dataset": after})


def test_empty_selection_is_observed_without_qualifying_metrics(tmp_path):
    result = _preview(tmp_path, output={"retained_ids": []})
    assert result["passed"] is True
    assert result["shape"]["after"] == [0, 2]
    assert any("no observations" in warning for warning in result["warnings"])
    assert result["reference_qc_quantiles"] == {}
    assert result["reference_qc_means"] == {}
    assert result["comparison_measurement_provenance"] == {}


def test_sample_metadata_is_optional_and_counts_layer_is_only_compatibility(tmp_path):
    data = _data()
    for row in data["observations"]:
        row.pop("sample")
    contract = _contract(tmp_path, sample=False)
    contract["validation"]["counts_layer"] = "not-a-count-matrix"
    contract["validation"]["invariants"] = []
    result = _preview(tmp_path, data=data, contract=contract)
    assert result["samples"] == {}
    assert result["invariants"] == dict.fromkeys(INVARIANTS, True)
    assert all(record["raw_counts_recomputed"] is False
               for record in result["comparison_measurement_provenance"].values())


def test_sample_and_group_metadata_coexist_with_numeric_measurements(tmp_path):
    data = {"format": "devbench.qc-observations/1", "observations": [
        {"cell_id": "c" + str(index), "sample": "s" + str(index // 2 + 1),
         "group": "A" if index < 4 else "B", "qc": index, "signal": 10 * (index // 2 + 1)}
        for index in range(8)]}
    contract = _contract(tmp_path, ("qc", "signal"))
    result = _preview(tmp_path, data=data, output={"retained_ids": ["c0", "c2", "c4", "c6"]},
                      contract=contract)
    assert contract["validation"]["sample_key"] == "sample"
    assert result["passed"] is True
    assert result["shape"] == {"before": [8, 2], "after": [4, 2]}
    assert result["invariants"]["immutable_observation_rows_selected"] is True
    assert result["samples"] == {sample: {"before": 2, "retained": 1, "removed": 1, "removal_rate": .5}
                                  for sample in ("s1", "s2", "s3", "s4")}
    assert set(result["reference_qc_means"]) == {"qc", "signal"}
    assert result["reference_qc_means"]["signal"] == {
        "before": 25, "after": 25, "absolute_standardized_shift": 0}
    assert result["reference_qc_quantiles"]["qc"]["after"] == pytest.approx([0, 1.5, 3, 4.5, 6])


@pytest.mark.parametrize("invalid_group", [None, 1, True, float("nan"), float("inf"), {}, [], "", "g" * 4097])
def test_optional_group_metadata_rejects_invalid_and_mixed_types(tmp_path, invalid_group):
    data = _data()
    for row in data["observations"]:
        row["group"] = "A"
    data["observations"][0]["group"] = invalid_group
    with pytest.raises(ValueError):
        _preview(tmp_path, data=data)


def test_optional_group_metadata_requires_consistent_columns(tmp_path):
    data = _data()
    data["observations"][0]["group"] = "A"
    with pytest.raises(ValueError):
        _preview(tmp_path, data=data)


@pytest.mark.parametrize("values", [[-1e308, 1e308], [5e-324, 5e-324], [0, 0]])
def test_finite_numeric_extremes_produce_finite_diagnostics(tmp_path, values):
    data = {"format": "devbench.qc-observations/1", "observations": [
        {"cell_id": str(i), "qc": value} for i, value in enumerate(values)]}
    result = _preview(tmp_path, data=data, output={"retained_ids": ["0"]},
                      contract=_contract(tmp_path, sample=False))
    assert result["reference_qc_means"]["qc"]["after"] == values[0]
    assert result["reference_qc_means"]["qc"]["absolute_standardized_shift"] == (1 if values[0] < 0 else 0)


def test_escaped_provenance_paths_and_comparable_reference_basis(tmp_path):
    metric = "qc/~measurement"
    data = _data()
    for row in data["observations"]:
        row[metric] = row.pop("qc")
    result = _preview(tmp_path, data=data, contract=_contract(tmp_path, (metric,)))
    alternate = _preview(tmp_path, data=data, output={"retained_ids": ["c", "d"]},
                         contract=_contract(tmp_path, (metric,)))
    fields = {"/reference_qc_quantiles/qc~1~0measurement/after/" + str(i) for i in range(5)}
    fields.update("/reference_qc_means/qc~1~0measurement/" + item
                  for item in ("after", "absolute_standardized_shift"))
    assert set(result["comparison_measurement_provenance"]) == fields
    for field in fields:
        record = coordinator_measurement_provenance(result, field)
        assert record == coordinator_measurement_provenance(alternate, field)
        assert record["origin"] == "devharness.method_validation/1"
        assert record["basis"] == "immutable_observation_rows_by_retained_ids"
        assert record["reference"] == {"input": "dataset", "measurement": metric}
        assert record["raw_counts_recomputed"] is False
        assert record["prerequisites"] == dict.fromkeys(INVARIANTS, True)


@pytest.mark.parametrize("change", [
    "origin", "basis", "comparator", "failed", "counts_claim", "wrong_input", "extra_reference",
    "wrong_measurement", "missing_prerequisite", "false_prerequisite", "extra_prerequisite",
    "false_invariant", "missing_invariant", "candidate_path", "before_path", "bad_index",
    "unescaped_path", "missing_value", "nonfinite_value", "boolean_value", "empty_retention", "mean_container",
])
def test_forged_or_incomplete_provenance_never_qualifies(tmp_path, change):
    result = deepcopy(_preview(tmp_path))
    field = "/reference_qc_means/qc/after"
    record = result["comparison_measurement_provenance"][field]
    if change in {"origin", "basis"}:
        record[change] = "candidate"
    elif change == "comparator":
        result["comparator"] = "json"
    elif change == "failed":
        result["passed"] = False
    elif change == "counts_claim":
        record["raw_counts_recomputed"] = True
    elif change == "wrong_input":
        record["reference"]["input"] = "other"
    elif change == "extra_reference":
        record["reference"]["counts_layer"] = "X"
    elif change == "wrong_measurement":
        record["reference"]["measurement"] = "constant"
    elif change == "missing_prerequisite":
        record["prerequisites"].pop(INVARIANTS[0])
    elif change == "false_prerequisite":
        record["prerequisites"][INVARIANTS[0]] = False
    elif change == "extra_prerequisite":
        record["prerequisites"]["made_up"] = True
    elif change == "false_invariant":
        result["invariants"][INVARIANTS[0]] = False
    elif change == "missing_invariant":
        result.pop("invariants")
    elif change in {"candidate_path", "before_path", "bad_index", "unescaped_path"}:
        field = {"candidate_path": "/qc_means/qc/after", "before_path": "/reference_qc_means/qc/before",
                 "bad_index": "/reference_qc_quantiles/qc/after/5",
                 "unescaped_path": "/reference_qc_means/qc/~measurement/after"}[change]
        result["comparison_measurement_provenance"][field] = record
    elif change == "missing_value":
        result["reference_qc_means"]["qc"].pop("after")
    elif change == "nonfinite_value":
        result["reference_qc_means"]["qc"]["after"] = float("inf")
    elif change == "boolean_value":
        result["reference_qc_means"]["qc"]["after"] = True
    elif change == "empty_retention":
        result["shape"]["after"][0] = 0
    elif change == "mean_container":
        result["reference_qc_means"]["qc"] = ["before", "after", "absolute_standardized_shift"]
    assert coordinator_measurement_provenance(result, field) is None
