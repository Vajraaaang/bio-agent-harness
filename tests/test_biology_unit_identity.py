"""Public toy fixtures for consistent donor-identity admission across tools.

No benchmark tasks, private references, model sessions or external data are used.
Set DEVBENCH_RUN_LOCAL_FIXTURES=1 to authorize these small local input checks.
"""
import copy
import csv
import json
import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1",
    reason="Small observed-input fixtures require explicit local authorization",
)


@pytest.fixture
def observed_inputs(tmp_path):
    rows = [dict(sample_id=f"c{i}", cell_id=f"c{i}", donor_id=f"d{i // 4}",
                 specimen_id=f"s{i // 4}", library_id=f"l{i // 4}",
                 condition="A" if i < 16 else "B", capture_batch="capture0")
            for i in range(32)]
    with (tmp_path / "metadata.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (tmp_path / "expression.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *[row["sample_id"] for row in rows]])
        writer.writerow(["markerA", *[80 if i % 4 < 2 else 1 for i in range(32)]])
        writer.writerow(["markerB", *[1 if i % 4 < 2 else 80 for i in range(32)]])
        for gene in range(12):
            writer.writerow([f"g{gene}", *[10 + (i + 3 * gene) % 9 for i in range(32)]])
    annotations = {"cell_annotation_reference": {
        "reference_identity": "public_toy_markers", "reference_scope": "artificial types",
        "markers": [{"label": "typeA", "gene_ids": ["markerA"]},
                    {"label": "typeB", "gene_ids": ["markerB"]}],
        "query_cell_labels_supplied": False}}
    (tmp_path / "annotations.json").write_text(json.dumps(annotations))
    task = {
        "measurement": "umi_counts", "observation_id_label": "sample_id",
        "unit_hierarchy": {"population_unit": "donor", "population_unit_label": "donor_id",
                           "specimen_label": "specimen_id", "library_label": "library_id"},
        "context": {"biological_unit": "donor", "condition_labels": ["A", "B"]},
        "questions": [{"target_id": "cell_state_effect", "estimand": "Within-type donor contrast",
                       "units": "log2 relative expression", "contrast": "B minus A",
                       "reference_population": "Declared toy donors",
                       "prediction_reference": "Observed donor population"}],
    }
    paths = dict(counts_path=str(tmp_path / "expression.csv"),
                 metadata_path=str(tmp_path / "metadata.csv"),
                 annotations_path=str(tmp_path / "annotations.json"), allow_data_execution=True)
    return task, paths


@pytest.mark.parametrize("field", ["unit_hierarchy", "biological_unit_hierarchy", "context"])
@pytest.mark.parametrize("status", ["unverified", "unknown", "intentionally_unknown"])
def test_projected_identity_status_is_honored_by_both_unit_checks(observed_inputs, field, status):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_bulk import _unit_audit
    from devbench.study.biology_tools_singlecell import _Unavailable, _units

    task, paths = observed_inputs
    hierarchy = task.pop("unit_hierarchy")
    hierarchy["identity_status"] = status
    if field == "context":
        task["context"]["biological_units"] = hierarchy
    else:
        task[field] = hierarchy
    data = load_observed_data(task, **paths)
    assert data.unit_hierarchy["identity_status"] == status
    assert not _unit_audit(data)["identity_and_nesting_valid"]
    with pytest.raises(_Unavailable, match="biological_unit_identity_unverified"):
        _units(data)


@pytest.mark.parametrize("status", ["unverified", "unknown", "intentionally_unknown"])
def test_legacy_negative_declaration_cannot_be_overridden(observed_inputs, status):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_singlecell import _Unavailable, _units

    task, paths = observed_inputs
    task["unit_hierarchy"]["identity_status"] = "recorded"
    task["context"]["biological_units"] = dict(task["unit_hierarchy"], identity_status=status)
    with pytest.raises(_Unavailable, match="biological_unit_identity_unverified"):
        _units(load_observed_data(task, **paths))


@pytest.mark.parametrize("tool", ["pseudobulk_membership_audit", "cell_type_state_analysis"])
def test_registered_cellular_worker_withholds_unverified_estimates(observed_inputs, tool):
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker

    task, paths = observed_inputs
    task["unit_hierarchy"]["identity_status"] = "unverified"
    result = _analyze_singlecell_worker(tool, task, **paths)
    details = result["diagnostics"] if tool.endswith("audit") else result["diagnostics"]["cell_state_effect"]
    assert details["status"] == "support_unavailable"
    assert details["reason"] == "biological_unit_identity_unverified"
    for answer in result["target_results"]:
        assert answer["estimate_status"] == "unavailable"
        assert answer["payload"] is None


@pytest.mark.parametrize("column", ["donor_id", "specimen_id", "library_id"])
@pytest.mark.parametrize("value", ["unknown", "NA", "unidentified"])
def test_missing_identity_tokens_do_not_become_biological_units(observed_inputs, column, value):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_bulk import _unit_audit
    from devbench.study.biology_tools_singlecell import _Unavailable, _units

    task, paths = observed_inputs
    data = load_observed_data(task, **paths)
    data.metadata.loc[data.metadata.index[:4], column] = value
    assert not _unit_audit(data)["identity_and_nesting_valid"]
    with pytest.raises(_Unavailable, match="missing_biological_unit_identity"):
        _units(data)


def test_valid_declared_memberships_are_preserved_without_claiming_authentication(observed_inputs):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_singlecell import _pseudobulk, _units

    task, paths = observed_inputs
    data = load_observed_data(task, **paths)
    before = copy.deepcopy(data.metadata)
    result = _units(data)
    assert result["biological_unit_count"] == 8
    assert result["cells_count_as_independent_donors"] is False
    labels = ["typeA" if i % 4 < 2 else "typeB" for i in range(32)]
    aggregated, details = _pseudobulk(data, labels, {})
    assert aggregated.expression.shape == (14, 16)
    assert details["missing_groups_filled_with_zeros"] is False
    assert data.metadata.equals(before)
