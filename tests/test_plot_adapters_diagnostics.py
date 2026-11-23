"""Synthetic descriptive geometry checks; these do not validate scientific findings."""
from copy import deepcopy
import importlib
import importlib.util
import math
from pathlib import Path
import subprocess

import pytest


def fixture_packets():
    """Public, labeled synthetic source packets for the browser fixture gallery."""
    measurements = [
        {"id": "log_reads", "name": "Synthetic log10 reads", "unit": "log10_read_count", "declared_transform": "log10(reads + 1); already applied in saved column"},
        {"id": "genes", "name": "Synthetic detected genes", "unit": "gene_count", "declared_transform": "identity"},
        {"id": "constant", "name": "Synthetic constant score", "unit": "score", "declared_transform": "identity"},
        {"id": "sparse", "name": "Synthetic sparse score", "unit": "score", "declared_transform": "identity"},
    ]
    scatter = {"x_measurement": deepcopy(measurements[0]), "y_measurement": deepcopy(measurements[1]),
        "histogram_scale": "count", "x_bin_edges": [0., 1., 2., 3.], "y_bin_edges": [0., 2., 4., 6.],
        "rows": [
            {"id": "synthetic-c1", "key": "source-1", "entity_kind": "cell", "x": 0., "y": 0., "group": "synthetic A", "status": "recorded"},
            {"id": "synthetic-c2", "key": "source-2", "entity_kind": "cell", "x": 1., "y": 2., "group": "synthetic B", "status": "recorded"},
            {"id": "synthetic-c3", "key": "source-3", "entity_kind": "cell", "x": 2., "y": 4., "group": "synthetic A", "status": "recorded"},
            {"id": "synthetic-c4", "key": "source-4", "entity_kind": "cell", "x": 3., "y": None, "group": "synthetic B", "status": "recorded", "reason": "synthetic_y_missing"},
            {"id": "synthetic-c5", "key": "source-5", "entity_kind": "cell", "x": None, "y": 6., "group": "synthetic C", "status": "recorded", "reason": "synthetic_x_missing"},
            {"id": "synthetic-failed", "key": "source-failed", "entity_kind": "cell", "x": 99., "y": 99., "group": "synthetic failed", "status": "failed", "reason": "synthetic_measurement_failed", "details": {"contributions": [{"kind": "cell", "id": "synthetic-origin", "value": 99.}]}}
        ]}
    values = [[1., 2., 1., None], [2., 4., 1., 9.], [3., 6., 1., None], [4., None, 1., None], [None, 10., 1., None], [100., 100., 1., 99.]]
    observations = [{"id": source["id"], "key": source["key"], "values": dict(zip([m["id"] for m in measurements], value)),
                     "group": source["group"], "status": source["status"], **({"reason": source["reason"]} if "reason" in source else {}),
                     **({"details": deepcopy(source["details"])} if "details" in source else {})}
                    for source, value in zip(scatter["rows"], values)]
    return [{"capability": "scatter_marginals", "data": scatter},
            {"capability": "measurement_correlation", "data": {"mode": "observations", "observation_kind": "cell",
                "measurements": measurements, "observations": observations, "missingness_policy": "pairwise_complete"}}]


def _adapter():
    name = "devbench.analysis.plot_adapters_diagnostics"
    assert importlib.util.find_spec(name) is not None, "descriptive diagnostic adapters must be implemented"
    return importlib.import_module(name)


def _data(capability):
    return deepcopy(next(p["data"] for p in fixture_packets() if p["capability"] == capability))


def _panels(capability, data=None):
    return _adapter().build_panels(capability, _data(capability) if data is None else data)


def _pair(panel, left, right):
    return next(r for r in panel["rows"] if r["entity_kind"] == "measurement_pair" and r["details"]["left_measurement"]["id"] == left and r["details"]["right_measurement"]["id"] == right)


@pytest.mark.parametrize("packet", fixture_packets(), ids=lambda p: p["capability"])
def test_contract_geometry_and_source_immutability(packet):
    from devbench.analysis.scientific_plot_contract import validate_panel
    before = deepcopy(packet)
    panels = _panels(packet["capability"], packet["data"])
    assert packet == before
    assert panels and any(p["marks"] for p in panels)
    for p in panels:
        validate_panel(p)
        assert p["capability"] == packet["capability"]
        assert {k for m in p["marks"] for k in m["keys"]} <= {r["key"] for r in p["rows"]}


def test_scatter_has_joint_points_and_independent_full_range_marginal_counts():
    data = _data("scatter_marginals")
    joint, xmargin, ymargin = _panels("scatter_marginals", data)
    assert len(joint["marks"]) == 3
    for margin, axis, expected in [(xmargin, "x", [1, 1, 2]), (ymargin, "y", [1, 1, 2])]:
        bins = [r for r in margin["rows"] if r["entity_kind"] == "bin"]
        assert [r["details"]["count"] for r in bins] == expected
        assert sum(r["details"]["count"] for r in bins) == 4
        keys = [key for r in bins for key in r["details"]["member_keys"]]
        assert len(keys) == len(set(keys)) == 4
        assert ("source-4" if axis == "x" else "source-5") in keys
        assert "source-failed" not in keys
        assert margin["axes"]["x"]["unit"] == data[axis + "_measurement"]["unit"]
        assert margin["axes"]["y"]["unit"] == "observations_per_bin"
        assert bins[-1]["details"]["right_edge_inclusive"] is True
        assert sum(m["height"] for m in margin["marks"]) == 4
    for p in (joint, xmargin, ymargin):
        retained = {r["key"]: r for r in p["rows"] if r["entity_kind"] != "bin"}
        assert set(retained) == {r["key"] for r in data["rows"]}
        for source in data["rows"]:
            assert retained[source["key"]]["details"]["source"] == source
            assert retained[source["key"]]["link"] == {"kind": source["entity_kind"], "id": source["id"]}
        assert retained["source-failed"]["reason"] == "synthetic_measurement_failed"


def test_marginal_density_integrates_to_one_with_explicit_unequal_bins():
    data = _data("scatter_marginals")
    data["histogram_scale"] = "density"
    data["x_bin_edges"] = [0., .5, 3.]
    for margin in _panels("scatter_marginals", data)[1:]:
        assert math.fsum(m["width"] * m["height"] for m in margin["marks"]) == pytest.approx(1.)
        assert margin["axes"]["y"]["unit"].startswith("probability_density_per_")


def test_marginals_keep_typed_identity_collisions_distinct():
    data = _data("scatter_marginals")
    data["rows"][1]["id"] = data["rows"][0]["id"]
    data["rows"][1]["entity_kind"] = "sample"
    for p in _panels("scatter_marginals", data):
        assert {tuple(r["link"].values()) for r in p["rows"] if r["key"] in {"source-1", "source-2"}} == {("cell", "synthetic-c1"), ("sample", "synthetic-c1")}


@pytest.mark.parametrize("change", ["range", "edges", "status", "definition", "key"])
def test_scatter_refuses_ambiguous_or_truncated_distributions(change):
    data = _data("scatter_marginals")
    if change == "range": data["x_bin_edges"] = [0., 1.]
    if change == "edges": data["y_bin_edges"] = [0., 2., 2., 6.]
    if change == "status": data["rows"][0]["status"] = "unknown"
    if change == "definition": del data["x_measurement"]["declared_transform"]
    if change == "key": data["rows"][1]["key"] = data["rows"][0]["key"]
    with pytest.raises(ValueError): _panels("scatter_marginals", data)


def test_correlation_uses_actual_pairs_and_never_fabricates_constant_diagonal():
    data = _data("measurement_correlation")
    p = _panels("measurement_correlation", data)[0]
    pair = _pair(p, "log_reads", "genes")
    assert pair["details"]["pearson_r"] == pytest.approx(1.)
    assert pair["details"]["n_pairs"] == 3
    assert pair["details"]["pair_member_ids"] == ["synthetic-c1", "synthetic-c2", "synthetic-c3"]
    assert pair["details"]["pair_member_keys"] == ["source-1", "source-2", "source-3"]
    assert pair["details"]["pair_member_links"] == [{"kind": "cell", "id": "synthetic-c" + str(i)} for i in (1, 2, 3)]
    assert _pair(p, "log_reads", "log_reads")["details"]["n_pairs"] == 4
    for left, right, reason in [("constant", "constant", "constant_measurement"), ("sparse", "sparse", "insufficient_pairs")]:
        missing = _pair(p, left, right)
        assert missing["details"]["pearson_r"] is None
        assert missing["reason"] == reason
        assert not any(m.get("value") is not None and missing["key"] in m["keys"] for m in p["marks"])
    assert p["color_scale"]["domain"] == [-1., 1.]
    assert p["color_scale"]["center"] == 0
    source = {r["key"]: r for r in p["rows"] if r["entity_kind"] == "cell"}
    assert set(source) == {o["key"] for o in data["observations"]}
    assert all(source[o["key"]]["details"]["source"] == o for o in data["observations"])
    assert source["source-failed"]["reason"] == "synthetic_measurement_failed"
    assert not any("p_value" in r["details"] for r in p["rows"])


def test_complete_case_policy_has_exact_shared_denominator():
    data = _data("measurement_correlation")
    data["missingness_policy"] = "complete_case"
    p = _panels("measurement_correlation", data)[0]
    pairs = [r for r in p["rows"] if r["entity_kind"] == "measurement_pair"]
    assert all(r["details"]["n_pairs"] == 1 for r in pairs)
    assert all(r["details"]["pair_member_keys"] == ["source-2"] for r in pairs)
    assert all(r["details"]["pearson_r"] is None for r in pairs)


def test_correlation_negative_and_large_magnitude_values_remain_finite():
    data = _data("measurement_correlation")
    data["measurements"] = data["measurements"][:2]
    data["observations"] = data["observations"][:3]
    for i, observation in enumerate(data["observations"], 1):
        observation["values"] = {"log_reads": i * 1e150, "genes": -i * 1e150}
    p = _panels("measurement_correlation", data)[0]
    assert _pair(p, "log_reads", "genes")["details"]["pearson_r"] == pytest.approx(-1.)


def test_known_nonperfect_correlation_is_calculated_and_source_values_preserved():
    data = _data("measurement_correlation")
    data["measurements"] = data["measurements"][:2]
    data["observations"] = data["observations"][:3]
    for x, y, observation in zip([1, 2, 3], [3, 1, 2], data["observations"]):
        observation["values"] = {"log_reads": x, "genes": y}
    p = _panels("measurement_correlation", data)[0]
    assert _pair(p, "log_reads", "genes")["details"]["pearson_r"] == pytest.approx(-.5)
    source = next(r for r in p["rows"] if r["key"] == "source-1")
    assert type(source["details"]["source"]["values"]["log_reads"]) is int


def test_scatter_preserves_original_saved_numeric_entries():
    data = _data("scatter_marginals")
    data["rows"][0]["x"] = 0
    for p in _panels("scatter_marginals", data):
        assert type(next(r for r in p["rows"] if r["key"] == "source-1")["details"]["source"]["x"]) is int


def test_retained_summary_correlation_routes_only_measured_variable_panels():
    asset = Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-summary-plots.js"
    script = """
const assert = require('node:assert/strict');
require(process.argv[1]);
globalThis.ScientificPlotPanels = {prepareWorkspace: value => value};
globalThis.BulkScientificFigure = {mount: (_container, options) => options.panelId};
const workspace = {format:'devbench.scientific-plot-workspace/1',panels:[
 {id:'sample-panel',capability:'bulk_sample_correlation_heatmap'},
 {id:'measurement-panel',capability:'measurement_correlation'}]};
assert.equal(WorkbenchSummaryPlots.mount({}, {view:'correlation',scientificWorkspace:workspace}), 'measurement-panel');
workspace.panels = workspace.panels.slice(0,1);
assert.throws(() => WorkbenchSummaryPlots.mount({}, {view:'correlation',scientificWorkspace:workspace}), /No compatible saved panel/);
"""
    result = subprocess.run(["node", "-e", script, str(asset)], text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


def _saved_matrix_data():
    data = _data("measurement_correlation")
    p = _panels("measurement_correlation", data)[0]
    ids = [m["id"] for m in data["measurements"]]
    data["mode"] = "validated_saved_matrix"
    data["saved_matrix"] = {"variable_ids": ids, "values": [[_pair(p, a, b)["details"]["pearson_r"] for b in ids] for a in ids],
        "pair_member_ids": [[_pair(p, a, b)["details"]["pair_member_ids"] for b in ids] for a in ids],
        "definition": "pearson_r_across_observations", "evidence_id": "synthetic-saved-correlation"}
    return data


def test_saved_matrix_is_validated_against_actual_observations_and_retained():
    data = _saved_matrix_data()
    p = _panels("measurement_correlation", data)[0]
    pair = _pair(p, "log_reads", "genes")
    assert pair["details"]["saved_entry"]["pearson_r"] == data["saved_matrix"]["values"][0][1]
    assert pair["details"]["saved_matrix_evidence_id"] == "synthetic-saved-correlation"


@pytest.mark.parametrize("change", ["constant_diagonal", "coefficient", "membership", "sample_ids", "definition"])
def test_saved_matrix_cannot_relabel_sample_correlations_or_change_actual_pairs(change):
    data = _saved_matrix_data()
    saved = data["saved_matrix"]
    if change == "constant_diagonal": saved["values"][2][2] = 1.
    if change == "coefficient": saved["values"][0][1] = .25
    if change == "membership": saved["pair_member_ids"][0][1] = ["synthetic-c1"]
    if change == "sample_ids": saved["variable_ids"] = ["S1", "S2", "S3", "S4"]
    if change == "definition": saved["definition"] = "sample_expression_correlation"
    with pytest.raises(ValueError): _panels("measurement_correlation", data)


@pytest.mark.parametrize("change", ["policy", "kind", "extra_column", "missing_column", "definition", "boolean"])
def test_correlation_requires_explicit_actual_measurement_contract(change):
    data = _data("measurement_correlation")
    if change == "policy": data["missingness_policy"] = "impute_zero"
    if change == "kind": del data["observation_kind"]
    if change == "extra_column": data["observations"][0]["values"]["invented_module"] = 1.
    if change == "missing_column": del data["observations"][0]["values"]["log_reads"]
    if change == "definition": del data["measurements"][0]["unit"]
    if change == "boolean": data["observations"][0]["values"]["genes"] = True
    with pytest.raises(ValueError): _panels("measurement_correlation", data)
