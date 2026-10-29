"""A bounded registered adapter calls, and does not replace, the bulk core."""
import copy
import csv
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import inputs


def bulk_inputs(tmp_path, *, continuous=True, confounded=False):
    task, paths = inputs(tmp_path, continuous=continuous, confounded=confounded)
    task["unit_hierarchy"] = {"population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id", "identity_status": "declared"}
    task["analysis_columns"]["time"] = None
    task["context"]["requested_condition_contrast"] = {"numerator": "B", "denominator": "A"}
    task["questions"] = [{"target_id": "mean_contrast", "estimand": "Condition mean contrast over observed biological units",
        "units": "input units" if continuous else "log2 count-rate contrast", "contrast": "B minus A",
        "reference_population": "observed biological units", "prediction_reference": "observed biological units"}]
    options = {"condition_col": "condition", "reference_condition": "A", "comparison_condition": "B",
        "batch_cols": ["batch"], "minimum_units": 3, "condition_number_limit": 1000.0}
    return task, paths, options


def test_adapter_is_a_registered_existing_executor_contract():
    from devharness_server.method_adapter import dispatcher_contract
    contract = dispatcher_contract("bulk_condition_contrast").to_dict()
    assert contract["implementation"]["kind"] == "dispatcher"
    assert set(contract["inputs"]) == {"counts", "metadata"}
    assert set(contract["parameters"]["required"]) == {"public_task", "options"}
    assert set(contract["outputs"]) == {"result"}


@pytest.mark.parametrize("continuous", [True, False])
def test_real_dispatcher_call_preserves_full_family_and_captured_evidence(tmp_path, continuous):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    task, paths, options = bulk_inputs(tmp_path, continuous=continuous)
    output = tmp_path / "outputs"
    output.mkdir()
    result = execute_dispatcher(dispatcher_contract("bulk_condition_contrast"),
        {"counts": paths["expression"], "metadata": paths["metadata"]},
        {"public_task": task, "options": options}, output)
    assert result["exit_code"] == 0, result
    assert len(result["tool_events"]) == 1
    event = result["tool_events"][0]
    assert event["tool_name"] == "bulk_condition_contrast" and event["succeeded"] is True
    actual = json.loads((output / "result.json").read_text())
    assert event["result"] == actual
    rows = actual["target_results"][0]["payload"]["gene_statistics"]
    assert [row["gene_id"] for row in rows] == task["gene_ids"]
    assert actual["diagnostics"]["gene_test_adjustment_family"]["attempted_hypothesis_ids"] == task["gene_ids"]
    assert actual["diagnostics"]["biological_units"]["analysis_unit_count"] == 8
    if continuous:
        assert all(row["values"][0] == pytest.approx(4.0) for row in rows)
    else:
        assert actual["method"]["count_model"] == "negative_binomial_empirical_bayes_dispersion"


def test_failed_gene_keeps_nulls_and_bh_position_through_registered_result(tmp_path):
    from scipy.stats import ttest_ind
    from statsmodels.stats.multitest import multipletests
    from devharness_server.dispatcher import ToolDispatcher
    task, paths, options = bulk_inputs(tmp_path)
    task["gene_ids"].append("failed_zero_variance")
    with paths["expression"].open("a", newline="") as handle:
        csv.writer(handle).writerow(["failed_zero_variance", *([0.0] * 8)])
    actual = json.loads(ToolDispatcher(allowed_root=tmp_path).execute("bulk_condition_contrast",
        {"counts_path": str(paths["expression"]), "metadata_path": str(paths["metadata"]),
         "public_task": task, "options": options}))
    rows = actual["target_results"][0]["payload"]["gene_statistics"]
    assert len(rows) == 13 and rows[-1]["estimate_status"] == "failed"
    assert rows[-1]["p_value"] is None and rows[-1]["q_value"] is None
    expected_p = ttest_ind([1., 2., 1., 2.], [5., 6., 5., 6.], equal_var=True).pvalue
    expected_q = multipletests([expected_p] * 12 + [1.0], method="fdr_bh")[1][0]
    assert rows[0]["p_value"] == pytest.approx(expected_p)
    assert rows[0]["q_value"] == pytest.approx(expected_q)


@pytest.mark.parametrize("defect", ["paired", "confounded"])
def test_bad_design_refusal_uses_existing_core_without_fitting(tmp_path, monkeypatch, defect):
    from devharness_server.tools_bulk import bulk_condition_contrast
    from devbench.study import biology_tools_bulk as core
    task, paths, options = bulk_inputs(tmp_path, confounded=defect == "confounded")
    if defect == "paired":
        with paths["metadata"].open(newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        for index, row in enumerate(rows):
            row["donor_id"] = "donor" + str(index % 4)
        with paths["metadata"].open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    def forbidden(*args, **kwargs):
        pytest.fail("A rejected design must not fit expression")
    monkeypatch.setattr(core, "_fit_family", forbidden)
    def inline(tool_name, public_task, *, counts_path, metadata_path, annotations_path, options, allow_data_execution):
        return core._analyze_bulk_worker(tool_name, public_task, counts_path, metadata_path,
                                        annotations_path, options, allow_data_execution)
    monkeypatch.setattr(core, "analyze_bulk_tool", inline)
    result = bulk_condition_contrast(str(paths["expression"]), str(paths["metadata"]), task, options)
    target = result["target_results"][0]
    assert target["payload"] is None
    assert target["estimate_status"] != "estimated"
    assert result["diagnostics"]["analysis_error"]


@pytest.mark.parametrize("defect", ["reversed", "different_condition", "dropped_batch", "formula", "extra_target",
                                    "no_hierarchy", "missing_minimum", "too_many_genes", "unselected_time"])
def test_invalid_parameters_fail_before_any_input_access(tmp_path, defect):
    from devharness_server.tools_bulk import bulk_condition_contrast
    task, _, options = bulk_inputs(tmp_path)
    if defect == "reversed":
        options["reference_condition"], options["comparison_condition"] = "B", "A"
    elif defect == "different_condition":
        options["condition_col"] = "other"
    elif defect == "dropped_batch":
        options.pop("batch_cols")
    elif defect == "formula":
        options["formula_str"] = "condition + arbitrary_expression()"
    elif defect == "extra_target":
        task["questions"].append(copy.deepcopy(task["questions"][0]))
    elif defect == "no_hierarchy":
        task.pop("unit_hierarchy")
    elif defect == "missing_minimum":
        options.pop("minimum_units")
    elif defect == "too_many_genes":
        task["gene_ids"] = ["g" + str(index) for index in range(100001)]
    elif defect == "unselected_time":
        task["analysis_columns"]["time"] = "age"
    with pytest.raises(ValueError):
        bulk_condition_contrast(str(tmp_path / "absent.csv"), str(tmp_path / "missing.csv"), task, options)


def test_bulk_contract_binds_transitive_study_source_without_changing_other_tools(monkeypatch):
    import devharness_server.method_adapter as adapter
    bulk = adapter.dispatcher_contract("bulk_condition_contrast")
    other = adapter.dispatcher_contract("calculate_temporal_monotonicity")
    original = adapter._stream_digest
    visited = []
    def altered(path, **kwargs):
        visited.append(Path(path))
        value, size = original(path, **kwargs)
        return ("a" * 64, size) if Path(path).name == "biology_tools_bulk.py" else (value, size)
    monkeypatch.setattr(adapter, "_stream_digest", altered)
    assert adapter.dispatcher_contract("bulk_condition_contrast").version_id != bulk.version_id
    assert adapter.dispatcher_contract("calculate_temporal_monotonicity").version_id == other.version_id
    assert not any("scoring" in path.name or "grader" in path.name for path in visited)


def test_undeclared_legacy_batch_is_not_silently_added(tmp_path):
    from devharness_server.tools_bulk import bulk_condition_contrast
    task, paths, options = bulk_inputs(tmp_path)
    task["analysis_columns"]["batches"] = []
    options.pop("batch_cols")
    with pytest.raises(ValueError, match="batch column must be explicitly declared"):
        bulk_condition_contrast(str(paths["expression"]), str(paths["metadata"]), task, options)


def test_no_batch_declaration_requires_no_batch_input_and_keeps_options(tmp_path, monkeypatch):
    from devharness_server.tools_bulk import bulk_condition_contrast
    from devbench.study import biology_tools_bulk as core
    task, paths, options = bulk_inputs(tmp_path)
    task["analysis_columns"]["batches"] = []
    options.pop("batch_cols")
    with paths["metadata"].open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = [name for name in reader.fieldnames if name != "batch"], list(reader)
    with paths["metadata"].open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{key: row[key] for key in fields} for row in rows])
    result = {"target_results": [], "diagnostics": {"fixture_delegation_only": True}}
    calls = []
    def unchanged(tool, supplied, **kwargs):
        calls.append((tool, supplied, kwargs))
        assert Path(kwargs["annotations_path"]).read_text() == "{}"
        assert Path(kwargs["annotations_path"]).parent.parent == paths["expression"].parent
        return result
    monkeypatch.setattr(core, "analyze_bulk_tool", unchanged)
    assert bulk_condition_contrast(str(paths["expression"]), str(paths["metadata"]), task, options) is result
    assert calls[0][0] == "mean_condition_analysis" and calls[0][1] == task
    assert calls[0][2]["options"] == {**options, "batch_cols": []}
    assert not Path(calls[0][2]["annotations_path"]).exists()


def test_public_task_bound_precedes_materializing_a_second_json_copy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import devharness_server.tools_bulk as adapter
    task, _, options = bulk_inputs(tmp_path)
    task["context"]["oversized_note"] = "x" * 5_000_001
    def forbidden(*args, **kwargs):
        pytest.fail("Reject unbounded public data before JSON copying")
    monkeypatch.setattr(adapter, "json", SimpleNamespace(dumps=forbidden, loads=json.loads))
    with pytest.raises(ValueError):
        adapter.validate_bulk_contrast_parameters(task, options)


@pytest.mark.parametrize("continuous", [True, False])
@pytest.mark.parametrize("defect", ["wrong_units", "missing_units", "wrong_alias", "causal_estimand",
                                    "unsupported_population", "unsupported_prediction", "reversed_target", "extra_claim"])
def test_target_metadata_cannot_overstate_the_computed_quantity(tmp_path, continuous, defect):
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters
    task, _, options = bulk_inputs(tmp_path, continuous=continuous)
    target = task["questions"][0]
    if defect == "wrong_units":
        target["units"] = "years"
    elif defect == "missing_units":
        target.pop("units")
    elif defect == "wrong_alias":
        target["measurement_units"] = "years"
    elif defect == "causal_estimand":
        target["estimand"] = "Causal effect of the treatment"
    elif defect == "unsupported_population":
        target["reference_population"] = "all patients"
    elif defect == "unsupported_prediction":
        target["prediction_reference"] = "new unseen patients"
    elif defect == "reversed_target":
        target["contrast"] = "A minus B"
    elif defect == "extra_claim":
        target["biological_conclusion"] = "Treatment causes the disease to resolve"
    with pytest.raises(ValueError):
        validate_bulk_contrast_parameters(task, options)


@pytest.mark.parametrize("continuous", [True, False])
def test_matching_measurement_units_alias_preserves_canonical_quantity(tmp_path, continuous):
    from devharness_server.tools_bulk import validate_bulk_contrast_parameters
    task, _, options = bulk_inputs(tmp_path, continuous=continuous)
    task["questions"][0]["measurement_units"] = task["questions"][0]["units"]
    validated, _ = validate_bulk_contrast_parameters(task, options)
    assert validated == task
