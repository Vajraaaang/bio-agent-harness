"""Synthetic controls for the trial verifier and receipt-bound pseudobulk sums."""
import hashlib
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

TRIAL = Path(__file__).resolve().parents[1] / "reports/live-scrna-motor-neuron-trial-2026-11-01"


def verifier():
    path = TRIAL / "verify_trial.py"
    assert path.is_file(), "v2 verifier is missing"
    sys.path.insert(0, str(TRIAL))
    spec = importlib.util.spec_from_file_location("scrna_trial_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bh_keeps_missing_hypotheses_in_denominator():
    assert verifier().bh_reference([0.01, None, 0.03, 0.8]) == pytest.approx([0.04, None, 0.06, 1.0])


def test_truth_metrics_exclude_annotation_markers_and_preserve_misses():
    rows = [{"gene_id": g, "component_id": "type", "estimate_status": "estimated",
             "values": [v], "p_value": .001, "q_value": q}
            for g, v, q in [("marker", 2, .001), ("positive", 1, .01), ("null", 1, .01)]]
    truth = {"type": {"marker": 2, "positive": 1, "missed": -1, "null": 0}}
    result = verifier().truth_metrics(rows, truth, {"marker"}, .05)
    assert result["pooled_realized_FDP_at_q05"] == .5
    assert result["by_cell_type"]["type"]["recall_at_q05"] == .5
    assert result["by_cell_type"]["type"]["direction_accuracy"] == 1
    assert result["by_cell_type"]["type"]["background_hypotheses"] == 3


def test_pseudobulk_receipt_binds_actual_integer_sums_and_axis_order():
    from devbench.study.biology_tool_io import ObservedData
    from devbench.study.biology_tools_singlecell import _pseudobulk
    ids = ("c1", "c2", "c3", "c4")
    meta = pd.DataFrame({"cell_id": ids, "donor_id": ["d1", "d1", "d2", "d2"],
                         "specimen_id": ["s1", "s1", "s2", "s2"],
                         "library_id": ["l1", "l1", "l2", "l2"],
                         "condition": ["A", "A", "B", "B"], "capture_batch": ["x"] * 4}, index=ids)
    data = ObservedData(np.array([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=float),
                        ("g1", "g2"), ids, meta, {}, "umi_counts",
                        {"population_unit": "donor", "population_unit_label": "donor_id",
                         "specimen_label": "specimen_id", "library_label": "library_id"}, (), {})
    result, details = _pseudobulk(data, ["t"] * 4, {})
    expected = np.array([[3, 7], [11, 15]], dtype="<i8")
    assert np.array_equal(result.expression, expected)
    binding = details["raw_count_binding"]
    assert binding["gene_ids"] == ["g1", "g2"]
    assert binding["pseudobulk_ids"] == ["pb0", "pb1"]
    assert binding["sha256"] == hashlib.sha256(expected.tobytes(order="C")).hexdigest()


@pytest.mark.parametrize("tamper", ["count", "axis", "membership"])
def test_independent_resum_rejects_count_axis_and_membership_tampering(tamper):
    expression = pd.DataFrame({"c1": [1, 5], "c2": [2, 6]}, index=["g1", "g2"])
    meta = pd.DataFrame({"embryo_id": ["e1", "e1"], "condition": ["A", "A"],
                         "specimen_id": ["s1", "s1"], "library_id": ["l1", "l1"]}, index=["c1", "c2"])
    expected = np.array([[3], [11]], dtype="<i8")
    diag = {"raw_count_binding": {"format": "pseudobulk-raw-count-sha256/1", "dtype": "little_endian_int64",
                                 "order": "C_genes_by_pseudobulk", "gene_ids": ["g1", "g2"],
                                 "pseudobulk_ids": ["pb0"], "shape": [2, 1],
                                 "sha256": hashlib.sha256(expected.tobytes(order="C")).hexdigest()},
            "membership": [{"pseudobulk_id": "pb0", "donor_id": "e1", "condition": "A", "cell_type": "t",
                            "cell_ids": ["c1", "c2"], "cell_count": 2, "specimen_ids": ["s1"], "library_ids": ["l1"]}],
            "batch_columns": [], "uncertain_cells_not_used_for_type_specific_model": 0}
    verifier().resummarize(expression, meta, diag)
    if tamper == "count":
        expression.loc["g1", "c1"] += 1
    elif tamper == "axis":
        diag["raw_count_binding"]["gene_ids"].reverse()
    else:
        diag["membership"][0]["cell_ids"] = ["c1", "c1"]
    with pytest.raises(ValueError):
        verifier().resummarize(expression, meta, diag)


def test_cell_state_zero_condition_boundaries_are_missing_not_finite_estimates(monkeypatch):
    """Even a solver reporting convergence cannot make a zero-group MLE finite."""
    from devbench.study import biology_tools_bulk as bulk
    from devbench.study import biology_tools_singlecell as module
    from devbench.study.biology_tool_io import ObservedData
    ids = tuple(f"d{i}" for i in range(8))
    genes = ("zero_early", "zero_late", "positive_both")
    values = np.array([[0, 0, 0, 0, 10, 15, 9, 14],
                       [10, 15, 9, 14, 0, 0, 0, 0],
                       [10, 15, 9, 14, 16, 20, 14, 18]], dtype=float)
    meta = pd.DataFrame({"donor_id": ids, "condition": ["A"] * 4 + ["B"] * 4,
                         "cell_type": ["t"] * 8}, index=ids)
    data = ObservedData(values, genes, ids, meta, {}, "umi_counts",
                        {"population_unit": "donor", "population_unit_label": "donor_id"}, (), {})
    def pseudobulk(data, settings, diagnostics):
        diagnostics.update(annotation_evidence={}, aggregation_definition="sum raw counts")
        return data, {"method": "test_annotation"}, ["t"], ["A", "B"]
    monkeypatch.setattr(module, "_state_pseudobulk", pseudobulk)
    monkeypatch.setattr(bulk, "_fit_family", lambda *args: ({g: object() for g in genes}, {}))
    monkeypatch.setattr(bulk, "_linear_estimate", lambda *args: ([1.0], [{"confidence_level": .95, "lower": .5, "upper": 1.5}], .01))
    target = {"target_id": "cell_state_effect", "estimand": "B minus A within type", "units": "log2 contrast",
              "contrast": "B minus A", "reference_population": "synthetic donors", "prediction_reference": "observed donors"}
    result = module._state_effect(data, target, {"batch_cols": []}, {})
    rows = result["payload"]["gene_statistics"]
    assert len(rows) == 3
    for row in rows[:2]:
        assert row["estimate_status"] == "unavailable"
        assert row["missing_reason"] == "zero_counts_in_condition_nonfinite_log_contrast_MLE"
        assert row.get("values") is None
        assert row.get("p_value") is None and row.get("q_value") is None
    assert rows[2]["estimate_status"] == "estimated"
    assert rows[2]["q_value"] == pytest.approx(.03)
    from devbench.analysis.episode_synthesis import target_family_complete
    assert target_family_complete(result, list(genes))
    # The boundary diagnostic assumes an identifiable, supported condition design.
    from dataclasses import replace
    for bad_meta, settings in [(meta.assign(condition="A"), {"batch_cols": []}),
                               (meta.assign(capture_batch=meta.condition), {"batch_cols": ["capture_batch"]})]:
        diagnostics = {}
        with pytest.raises((module._Unavailable, module._MethodUnavailable)):
            module._state_effect(replace(data, metadata=bad_meta), target, settings, diagnostics)
        assert "nonfinite_condition_contrast_genes" not in diagnostics["fits_by_type"]["t"]
        assert "unavailable" in diagnostics["fits_by_type"]["t"]


def test_scrna_projection_exposes_full_family_and_bounded_diagnostics():
    from devbench.analysis.episode_agent import _scrna_effect_projection
    from devbench.study.biology_answers import public_hypothesis_id
    rows = [{"gene_id": "g1", "component_id": "t", "hypothesis_id": public_hypothesis_id("g1", "t"),
             "estimate_status": "estimated", "p_value": .01, "q_value": .02},
            {"gene_id": "g2", "component_id": "t", "hypothesis_id": public_hypothesis_id("g2", "t"),
             "estimate_status": "unavailable", "p_value": None, "q_value": None,
             "missing_reason": "zero_counts_in_condition_nonfinite_log_contrast_MLE"}]
    target = {"target_id": "cell_state_effect", "payload": {"kind": "effects", "gene_statistics": rows,
               "multiplicity_scope": [rows[0]["hypothesis_id"]]}}
    diagnostics = {"cell_state_effect": {"fits_by_type": {"t": {
        "design": {"rank": 2, "column_count": 2, "residual_df": 6, "condition_number_validated": True},
        "calibration": {"gene_estimates": {"g1": {"dispersion_converged": True}, "g2": {"dispersion_converged": True}}},
        "nonfinite_condition_contrast_genes": ["g2"], "model_warnings": {"g1": ["do not expose free text"]},
        "plot_evidence": {"raw_counts": [[99]]}}}}}
    projection = _scrna_effect_projection(target, diagnostics, ["g1", "g2"], ["t"])
    assert projection["full_attempted_BH_denominator"] == 2
    assert projection["unavailable_reason_counts"] == {"zero_counts_in_condition_nonfinite_log_contrast_MLE": 1}
    assert projection["fit_diagnostics_by_type"]["t"]["nonfinite_condition_contrast_count"] == 1
    assert projection["fit_diagnostics_by_type"]["t"]["dispersion_status_counts"] == {"converged": 2}
    assert "g2" not in str(projection) and "raw_counts" not in str(projection)
    assert "do not expose" not in str(projection)
    rows.pop()
    assert _scrna_effect_projection(target, diagnostics, ["g1", "g2"], ["t"])["full_attempted_BH_denominator"] is None


def test_scrna_large_projection_preserves_receipts_and_rejects_summary_tampering(monkeypatch):
    import json
    import tempfile
    from devbench.analysis.episode_agent import project_observation, verify_projected_history
    from devbench.study.biology_broker import BiologyBroker
    from devbench.study.biology_tools_bulk import _bh
    from tests.test_analysis_episodes import create, run_contrast
    original = BiologyBroker._invoke_tool
    def invoke(self, tool, arguments):
        result = original(self, tool, arguments)
        if tool == "cell_type_state_analysis":
            result["diagnostics"]["transport_padding"] = "x" * 150_000
            payload = result["target_results"][0]["payload"]
            for row, reason in zip(payload["gene_statistics"][2:4],
                                   ["zero_counts_in_condition_nonfinite_log_contrast_MLE", "all_zero_gene"]):
                identifiers = {k: row[k] for k in ("hypothesis_id", "gene_id", "component_id")}
                row.clear()
                row.update(identifiers, estimate_status="unavailable", missing_reason=reason, p_value=None, q_value=None)
            payload["multiplicity_scope"] = _bh(payload["gene_statistics"])
        return result
    monkeypatch.setattr(BiologyBroker, "_invoke_tool", invoke)
    folder = TRIAL / "validation-20261102/projection-test-workspaces"
    folder.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="projection-", dir=folder) as directory:
        episode, _ = create(Path(directory), cellular=True, continuous=False)
        try:
            response = run_contrast(episode, cellular=True)
            projected = project_observation(episode, response)
            summary = projected["result_summary"]
            target = summary["targets"][0]
            assert target["full_attempted_BH_denominator"] == 28
            assert target["tested_gene_rows"] == 26
            assert target["family_structure_verified"] is True
            assert sum(target["unavailable_reason_counts"].values()) == 2
            assert "transport_padding" not in json.dumps(projected)
            assert "gene_statistics" not in json.dumps(projected)
            assert len(json.dumps(projected).encode()) < 128_000
            history = [{"action": {"kind": "tool", "tool": "cell_type_state_analysis"}, "observation": projected}]
            verify_projected_history(episode, history)
            for key in ("full_attempted_BH_denominator", "tested_gene_rows"):
                changed = json.loads(json.dumps(history))
                changed[0]["observation"]["result_summary"]["targets"][0][key] += 1
                with pytest.raises(ValueError, match="projection"):
                    verify_projected_history(episode, changed)
            checkpoint = episode.root / "checkpoints/action-0004/observation.json"
            retained = checkpoint.read_bytes()
            assert len(retained) > 128_000 and b"transport_padding" in retained
            checkpoint.write_bytes(retained + b" ")
            with pytest.raises(ValueError, match="changed|mismatch|projection"):
                verify_projected_history(episode, history)
            checkpoint.write_bytes(retained)
        finally:
            if episode.outcome is None:
                episode.abort("synthetic_projection_regression_complete")
