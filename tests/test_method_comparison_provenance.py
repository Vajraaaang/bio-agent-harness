"""Coordinator-owned measurements and legacy scope guards; synthetic inputs only."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.methods import _digest
from tests.test_extensible_methods import FixtureExecutor, custom, service
from tests.test_method_planner import Selector, candidate, recommendation
from tests.test_method_planner_scientific_safeguards import comparable_plan, comparison_spec


def _anndata_comparison(tmp_path, field):
    """Exercise the real independent H5AD validator and sealed run receipts."""
    import anndata as ad

    api = service(tmp_path)
    contract, source, lock = custom(tmp_path, comparator="single_cell_qc")
    contract["inputs"]["dataset"]["artifact_type"] = "anndata"
    contract["outputs"]["dataset"].update(artifact_type="anndata", path="/outputs/result.h5ad")
    contract["validation"].update(sample_key="sample", qc_metrics=["total_counts"], invariants=[
        "observation_ids_unique", "retained_observation_ids_subset_of_input",
        "variables_unchanged", "retained_count_matrix_unchanged"])
    versions = []
    for name in ("Unknown QC", "Alternative QC"):
        item = deepcopy(contract)
        item.update(name=name, method_id="fixture." + name.split()[0].lower())
        versions.append(api.register_method(item, source=source, environment_lock=lock, actor="researcher"))
    original = ad.AnnData(np.array([[1, 0], [2, 3], [4, 5], [6, 7]], dtype=float),
                         obs=pd.DataFrame({"sample": ["a", "a", "b", "b"], "total_counts": [1., 5., 9., 13.]},
                                          index=["c1", "c2", "c3", "c4"]),
                         var=pd.DataFrame(index=["g1", "g2"]))
    path = tmp_path / "immutable-input.h5ad"
    original.write_h5ad(path)
    artifact = api.import_artifact(path, artifact_type="anndata", actor="researcher")
    analysis = api.create_analysis("Synthetic comparison", actor="researcher")
    branch = api.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="researcher")
    step = api.add_step(branch["branch_id"], versions[0]["version_id"], {"dataset": artifact["artifact_id"]},
                        {"threshold": 4}, actor="researcher")
    planner = ScientificMethodPlanner(api)
    state = planner.create({"scientific_objective": "Compare this synthetic software fixture", "computational_task": "quality_control"},
                           {"observed": {"independent_samples": True}, "inferred": {}, "unknowns": [],
                            "artifact_ids": [artifact["artifact_id"]]}, actor="researcher", data_classification="synthetic")
    names = ("Unknown QC", "Alternative QC")
    selector = Selector({"candidates": [candidate(name) for name in names]},
                        {"bindings": [{"candidate": name, "method_version_id": version["version_id"], "discovery": None}
                                      for name, version in zip(names, versions)]})
    planner.generate_candidates(state["session_id"], selector, actor="researcher")
    planner.resolve_capabilities(state["session_id"], selector, [item["version_id"] for item in versions], actor="researcher")
    spec = comparison_spec(step)
    spec["metric_sources"]["reference_error"] = {"kind": "validation", "field": field}
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="researcher")

    class ForgeOutputAnnotations:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            # IDs and count matrix are genuine; the output annotation is invented.
            output = ad.read_h5ad(inputs["dataset"])[[0, 1, 2], :].copy()
            output.obs["total_counts"] = 1_000_000.
            output.uns["comparison_measurement_provenance"] = {"origin": "devharness.method_validation/1"}
            output.write_h5ad(output_dir / "result.h5ad")
            return {"exit_code": 0, "runtime": "synthetic_fixture"}

    rows = planner.run_trial(state["session_id"], spec["id"], actor="researcher", allow_data_execution=True,
                             executor=ForgeOutputAnnotations())
    assert all(row["status"] == "success" for row in rows), rows
    return planner, state, api.get("run", rows[0]["provenance"]["run_id"])["validation"]


def test_forged_output_qc_quantiles_remain_observable_but_cannot_qualify(tmp_path):
    planner, state, validation = _anndata_comparison(tmp_path, "/qc_quantiles/total_counts/after/2")
    assert validation["qc_quantiles"]["total_counts"]["after"][2] == 1_000_000.
    assert validation["invariants"]["retained_count_matrix_unchanged"] is True
    assert not any(row["meets_evidence_floor"] for row in planner.evidence_assessment(state["session_id"])["candidates"])
    assert validation["reference_qc_quantiles"]["total_counts"]["after"][2] == 5.
    with pytest.raises(ValueError, match="evidence"):
        planner.recommend(state["session_id"], Selector(recommendation()), actor="researcher")


def test_reference_annotation_measurement_ignores_forged_output_values(tmp_path):
    planner, state, validation = _anndata_comparison(tmp_path, "/reference_qc_quantiles/total_counts/after/2")
    measurement = validation["comparison_measurement_provenance"]["/reference_qc_quantiles/total_counts/after/2"]
    assert measurement["origin"] == "devharness.method_validation/1"
    assert measurement["basis"] == "immutable_input_annotations_by_retained_ids"
    assert measurement["raw_counts_recomputed"] is False
    assert all(value is True for value in measurement["prerequisites"].values())
    assert validation["reference_qc_quantiles"]["total_counts"]["after"][2] == 5.
    assert all(row["meets_evidence_floor"] for row in planner.evidence_assessment(state["session_id"])["candidates"])


@pytest.mark.parametrize("change", ["missing", "unknown_origin", "unknown_basis", "wrong_pointer", "different_reference",
                                    "missing_prerequisite", "failed_prerequisite"])
def test_comparison_floor_requires_recognized_complete_measurement_provenance(tmp_path, monkeypatch, change):
    planner, state, *_ = comparable_plan(tmp_path, monkeypatch, execute=False)
    from devbench.analysis import methods
    trusted_validation = methods.validate_outputs

    def incomplete_validation(contract, inputs, outputs):
        result = trusted_validation(contract, inputs, outputs)
        provenance = result["comparison_measurement_provenance"]
        record = provenance["/scientific_metrics/reference_error"]
        if change == "missing":
            result.pop("comparison_measurement_provenance")
        elif change == "unknown_origin":
            record["origin"] = "method-self-report"
        elif change == "unknown_basis":
            record["basis"] = "candidate_annotation"
        elif change == "wrong_pointer":
            provenance["/another_metric"] = provenance.pop("/scientific_metrics/reference_error")
        elif change == "different_reference":
            record["reference"] = "Incompatible per-method reference: " + contract["name"]
        elif change == "missing_prerequisite":
            record["prerequisites"].pop("finite_comparison_values")
        else:
            record["prerequisites"]["finite_comparison_values"] = False
        return result

    monkeypatch.setattr(methods, "validate_outputs", incomplete_validation)
    planner.run_trial(state["session_id"], "qc-pilot", actor="researcher", allow_data_execution=True, executor=FixtureExecutor())
    assert not any(row["meets_evidence_floor"] for row in planner.evidence_assessment(state["session_id"])["candidates"])


@pytest.mark.parametrize("violation", ["counts", "missing_counts", "variables", "ids", "duplicate_ids", "sample_assignments"])
def test_reference_measurements_fail_closed_on_representation_changes(tmp_path, violation):
    import anndata as ad
    from devbench.analysis.method_validation import single_cell_preview

    original = ad.AnnData(np.array([[1., 2.], [3., 4.], [5., 6.]]),
                         obs=pd.DataFrame({"total_counts": [3., 7., 11.], "sample": ["one", "one", "two"]}, index=["a", "b", "c"]),
                         var=pd.DataFrame(index=["g1", "g2"]))
    changed = original[[0, 1], :].copy()
    if violation == "counts":
        changed.X[0, 0] = 42.
    elif violation == "missing_counts":
        changed.X = None
    elif violation == "variables":
        changed.var_names = ["other", "g2"]
    elif violation == "ids":
        changed.obs_names = ["new", "b"]
    elif violation == "duplicate_ids":
        changed.obs_names = ["a", "a"]
    else:
        changed.obs.loc["a", "sample"] = "two"
    before, after = tmp_path / "before.h5ad", tmp_path / "after.h5ad"
    original.write_h5ad(before)
    changed.write_h5ad(after)
    # Even an optional/empty contract invariant list cannot establish the floor.
    preview = single_cell_preview(before, after, {"counts_layer": "X", "sample_key": "sample", "qc_metrics": ["total_counts"], "invariants": []})
    assert preview.get("comparison_measurement_provenance") == {}
    assert preview.get("reference_qc_quantiles") == {}


def test_reference_annotation_basis_and_escaped_paths_are_explicit(tmp_path):
    import anndata as ad
    from devbench.analysis.method_validation import coordinator_measurement_provenance, single_cell_preview

    metric = "count/~annotation"
    original = ad.AnnData(np.array([[1., 2.], [3., 4.], [5., 6.]]),
                         obs=pd.DataFrame({metric: [103., 107., 111.]}, index=["a", "b", "c"]))
    retained = original[[0, 1], :].copy()
    retained.obs[metric] = -1_000.
    before, after = tmp_path / "before.h5ad", tmp_path / "after.h5ad"
    original.write_h5ad(before)
    retained.write_h5ad(after)
    preview = single_cell_preview(before, after, {"counts_layer": "X", "qc_metrics": [metric], "invariants": []})
    # The reference annotation median is 105; count-sum recomputation would be 5.
    assert preview["reference_qc_quantiles"][metric]["after"][2] == 105.
    field = "/reference_qc_quantiles/count~1~0annotation/after/2"
    provenance = coordinator_measurement_provenance(preview, field)
    assert provenance["raw_counts_recomputed"] is False
    assert provenance["reference"]["annotation"] == metric
    assert coordinator_measurement_provenance(preview, "/qc_quantiles/count~1~0annotation/after/2") is None


@pytest.mark.parametrize("action", ["save_recipe", "propose_selected", "prepare_full_execution", "approve_full_execution", "run_full"])
def test_historical_runtime_only_approval_cannot_authorize_new_scope(tmp_path, monkeypatch, action):
    planner, state, analysis, branch, step = comparable_plan(tmp_path, monkeypatch,
        metric_source={"kind": "runtime", "field": "elapsed_seconds"}, metric_category="runtime")
    legacy = planner.get(state["session_id"])
    legacy["recommendation"] = recommendation()
    digest = _digest(legacy["recommendation"])
    legacy["approvals"]["approve_recommendation"] = {"status": "approved", "actor": "historical-reviewer",
        "rationale": "Historical review preserved", "recommendation_sha256": digest}
    legacy["full_execution"] = {"step_id": step["step_id"], "recommendation_sha256": digest,
                                 "status": "approved" if action == "run_full" else "pending"}
    with planner.methods._db() as db:
        planner.methods._put(db, "method_plan", state["session_id"], legacy, update=True)
    before = planner.methods.database.read_bytes()
    records = {kind: planner.methods.list_records(kind) for kind in ("run", "step", "recipe", "proposal")}
    calls = []

    class CountExecution(FixtureExecutor):
        def execute(self, *args):
            calls.append(True)
            return super().execute(*args)

    operations = {
        "save_recipe": lambda: planner.save_recipe(state["session_id"], "New recipe", actor="researcher", rationale="New scope"),
        "propose_selected": lambda: planner.propose_selected(state["session_id"], actor="researcher", rationale="New scope"),
        "prepare_full_execution": lambda: planner.prepare_full_execution(state["session_id"], branch["branch_id"], step["inputs"], actor="researcher"),
        "approve_full_execution": lambda: planner.approve_full_execution(state["session_id"], actor="researcher", rationale="New scope"),
        "run_full": lambda: planner.run_full(state["session_id"], actor="researcher", allow_data_execution=True, executor=CountExecution()),
    }
    with pytest.raises(ValueError, match="evidence"):
        operations[action]()
    assert calls == []
    assert planner.get(state["session_id"]) == legacy
    assert planner.export(state["session_id"])["recommendation"]["choice"] == "Unknown QC"
    assert planner.methods.database.read_bytes() == before
    assert {kind: planner.methods.list_records(kind) for kind in records} == records
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
