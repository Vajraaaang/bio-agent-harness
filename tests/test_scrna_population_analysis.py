"""Synthetic supplied-population contrasts; no live inputs or private answers."""
import copy
import hashlib
import importlib
import json

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy import sparse


def fixture(tmp_path, *, layout="csr", absent=False, bad=None, cells_per_profile=3):
    rng = np.random.default_rng(317)
    rows, values = [], []
    for stage in ("E1", "E2"):
        for donor in range(3):
            unit = f"{stage}-donor-{donor}"
            for population in ("P", "N"):
                if absent and stage == "E2" and population == "N":
                    continue
                for cell in range(cells_per_profile):
                    rows.append({"embryo": unit, "stage": stage, "type": population,
                                 "batch": "b-" + unit, "capture": "c-" + unit})
                    counts = rng.poisson(25, 24).astype(float)
                    counts[0] += 60 if population == "N" else 0
                    counts[-1] = 0
                    values.append(counts)
    values = np.array(values)
    if bad is not None:
        values[0, 0] = bad
    matrix = {"csr": sparse.csr_matrix, "dense": np.asarray, "csc": sparse.csc_matrix}[layout](values)
    data = ad.AnnData(X=sparse.csr_matrix(values.shape),
        obs=pd.DataFrame(rows, index=[f"cell{i}" for i in range(len(rows))]),
        var=pd.DataFrame(index=[f"gene{i}" for i in range(values.shape[1])]))
    data.layers["counts"] = matrix
    path = tmp_path / "synthetic.h5ad"
    data.write_h5ad(path)
    specification = {"format": "devbench.scrna-population-spec/1",
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "measurement": "umi_counts", "counts_layer": "counts",
        "populations": [{"label": "Progenitor", "column": "type", "values": ["P"]},
                        {"label": "Neuron", "column": "type", "values": ["N"]}],
        "biological_unit_column": "embryo", "biological_unit_identity": "declared",
        "stage_column": "stage", "stage_levels": ["E1", "E2"],
        "technical_alias_columns": ["batch", "capture"],
        "question": "Compare supplied populations at each recorded stage.",
        "requirement_source": {"origin": "synthetic_test_requirement", "unit_identity": "declared"},
        "procedure": "embryo_fixed_effect_stage_population_nb_v1", "choice_origin": "assistant_proposed"}
    return path, specification, values, data


def engine():
    try:
        return importlib.import_module("devbench.analysis.scrna_population_analysis")
    except ModuleNotFoundError:
        pytest.fail("The supplied-population analysis engine is not implemented")


def test_contract_rejects_ungranted_authority_and_formula_before_input(tmp_path):
    _, spec, _, _ = fixture(tmp_path)
    for key, value in (("allow_execution", True), ("formula", "embryo + stage * population")):
        altered = {**spec, key: value}
        with pytest.raises(ValueError, match="fields"):
            engine().run_population_analysis(str(tmp_path / "missing.h5ad"), altered, "audit")


def test_metadata_audit_binds_full_feature_universe_and_membership(tmp_path):
    path, spec, _, _ = fixture(tmp_path)
    result = engine().run_population_analysis(str(path), spec, "audit")
    assert result["audit"]["feature_count"] == 24
    assert result["audit"]["selected_cell_count"] == 36
    assert result["audit"]["population_cell_counts"] == {"Progenitor": 18, "Neuron": 18}
    assert result["audit"]["declared_biological_unit_count"] == 6
    assert result["audit"]["independence_verified"] is False
    assert len(result["audit"]["membership_sha256"]) == 64
    assert result["audit"]["expression_values_read"] is False
    assert result["source_sha256"] == spec["source_sha256"]


def test_numeric_design_accounts_for_embryos_and_aliases(tmp_path):
    path, spec, _, _ = fixture(tmp_path)
    result = engine().run_population_analysis(str(path), spec, "design")
    design = result["design"]
    assert design["diagnostics"]["rank"] == 8
    assert design["diagnostics"]["residual_df"] == 4
    assert design["diagnostics"]["condition_number_validated"] is True
    assert design["alias_columns_absorbed_by_biological_unit"] == ["batch", "capture"]
    assert design["stage_main_effects"] == "absorbed_by_biological_unit_fixed_effects"
    assert len(design["profiles"]) == 12
    assert len(design["contrasts"]) == 2
    matrix = np.array(design["matrix"])
    for contrast in design["contrasts"]:
        assert contrast["estimable"] is True
        assert contrast["paired_unit_count"] == 3
        vector = np.array(contrast["vector"])
        np.testing.assert_allclose(vector @ np.linalg.pinv(matrix) @ matrix, vector, atol=1e-12)
        assert sum(abs(vector)) == 1


@pytest.mark.parametrize("layout", ["csr", "dense"])
def test_streamed_pseudobulk_preserves_every_count_and_gene(tmp_path, layout):
    path, spec, values, data = fixture(tmp_path, layout=layout)
    result = engine().run_population_analysis(str(path), spec, "pseudobulk")
    panel = np.array(result["pseudobulk"]["counts"])
    profiles = result["design"]["profiles"]
    assert panel.shape == (24, 12)
    for index, profile in enumerate(profiles):
        label = "P" if profile["population"] == "Progenitor" else "N"
        selected = (data.obs.embryo == profile["biological_unit"]) & (data.obs.type == label)
        np.testing.assert_array_equal(panel[:, index], values[selected].sum(axis=0))
    assert result["pseudobulk"]["count_validation"]["all_source_entries_validated"] is True
    assert result["pseudobulk"]["feature_ids"] == list(data.var_names)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == spec["source_sha256"]


def test_missing_combinations_are_absent_profiles_and_design_refuses(tmp_path, monkeypatch):
    path, spec, _, _ = fixture(tmp_path, absent=True)
    module = engine()

    def unexpected(*args, **kwargs):
        pytest.fail("An unidentifiable contrast must neither materialize counts nor fit a model")

    monkeypatch.setattr(module, "_pseudobulk", unexpected)
    from devbench.core import population_statistics
    monkeypatch.setattr(population_statistics, "_fit_family", unexpected)
    result = module.run_population_analysis(str(path), spec, "contrast")
    assert result["status"] == "scientific_refusal"
    assert result["pseudobulk"] is None
    assert len(result["design"]["profiles"]) == 9
    assert len(result["design"]["missing_combinations"]) == 3
    assert result["design"]["diagnostics"]["condition_number_validated"] is False
    rows = result["gene_statistics"]
    assert [(row["gene_id"], row["stage"]) for row in rows] == [
        (f"gene{i}", stage) for i in range(24) for stage in spec["stage_levels"]
    ]
    assert all(row["estimate_status"] == "refused" and row["failure_code"] for row in rows)
    for field in (
        "log2_fold_change", "standard_error", "confidence_interval_lower",
        "confidence_interval_upper", "p_value", "q_value",
    ):
        assert all(row[field] is None for row in rows)
    assert result["testing_family"]["attempted_n"] == 48
    assert result["summary"]["refused_hypothesis_count"] == 48


def test_overlap_and_non_alias_technical_columns_are_not_silently_changed(tmp_path):
    path, spec, _, data = fixture(tmp_path)
    overlap = copy.deepcopy(spec)
    overlap["populations"][1]["values"].append("P")
    with pytest.raises(ValueError, match="overlap"):
        engine().run_population_analysis(str(path), overlap, "audit")
    data.obs["batch"] = data.obs["batch"].astype(str)
    data.obs.loc[data.obs.index[0], "batch"] = "second-capture"
    data.write_h5ad(path)
    spec["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = engine().run_population_analysis(str(path), spec, "design")
    assert result["status"] == "scientific_refusal"
    assert "technical_column_is_not_one_to_one_biological_unit_alias" in result["design"]["refusal_codes"]


@pytest.mark.parametrize("bad", [-1, 0.25, float("nan"), float("inf"), 2**53])
def test_invalid_counts_are_rejected_without_casting(tmp_path, bad):
    path, spec, _, _ = fixture(tmp_path, bad=bad)
    with pytest.raises(ValueError, match="counts"):
        engine().run_population_analysis(str(path), spec, "pseudobulk")


def test_source_digest_must_match_before_metadata_is_used(tmp_path):
    path, spec, _, _ = fixture(tmp_path)
    spec["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source_sha256"):
        engine().run_population_analysis(str(path), spec, "audit")


def test_streaming_refuses_csc_instead_of_unbounded_materialization(tmp_path):
    path, spec, _, _ = fixture(tmp_path, layout="csc")
    with pytest.raises(ValueError, match="CSR or dense"):
        engine().run_population_analysis(str(path), spec, "pseudobulk")


def test_feature_budget_refuses_without_feature_selection(tmp_path):
    path, spec, _, _ = fixture(tmp_path)
    spec["limits"] = {"max_features": 12}
    with pytest.raises(ValueError, match="feature"):
        engine().run_population_analysis(str(path), spec, "audit")


def test_full_gene_stage_family_contains_zero_gene_nulls_and_one_bh_family(tmp_path):
    from statsmodels.stats.multitest import multipletests
    path, spec, _, _ = fixture(tmp_path)
    result = engine().run_population_analysis(str(path), spec, "contrast")
    rows = result["gene_statistics"]
    assert len(rows) == 48
    assert {(r["gene_id"], r["stage"]) for r in rows} == {(f"gene{i}", s) for i in range(24) for s in ("E1", "E2")}
    zero = [row for row in rows if row["gene_id"] == "gene23"]
    assert all(row["p_value"] is None and row["q_value"] is None and row["estimate_status"] == "not_estimable" for row in zero)
    p = [1.0 if row["p_value"] is None else row["p_value"] for row in rows]
    q = multipletests(p, method="fdr_bh")[1]
    for row, expected in zip(rows, q):
        if row["p_value"] is not None:
            assert row["q_value"] == pytest.approx(expected)
            assert row["q_value"] >= row["p_value"]
    assert result["summary"]["full_feature_count"] == 24
    assert result["summary"]["attempted_hypothesis_count"] == 48
    assert result["testing_family"]["missing_hypothesis_policy"] == "internal_p_one_public_p_q_null"
    assert [event["phase"] for event in result["runtime_events"]] == ["audit", "design", "pseudobulk", "contrast"]
    assert result["diagnostics"]["normalization"]["method"].startswith("median_of_ratios")
    json.dumps(result, allow_nan=False)


def test_registered_tools_are_existing_dispatcher_contracts():
    from devharness_server.method_adapter import dispatcher_contract
    for stage in ("audit", "design", "pseudobulk", "contrast"):
        contract = dispatcher_contract("scrna_population_" + stage).to_dict()
        assert set(contract["inputs"]) == {"anndata"}
        assert contract["inputs"]["anndata"]["artifact_type"] == "anndata"
        assert set(contract["parameters"]["required"]) == {"specification"}
        assert contract["execution"]["max_output_bytes"] == 96_000_000
        if stage == "contrast":
            assert contract["action_scope"] == {"format": "devbench.method-action/1",
                "intent": "infer", "interpretation": "none", "exclusions": False}


def test_metadata_only_tools_never_read_count_values(tmp_path, monkeypatch):
    from devbench.analysis.io import BackedExpression
    path, spec, _, _ = fixture(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Metadata preflight must not read expression")
    monkeypatch.setattr(BackedExpression, "iter_rows", forbidden)
    for stage in ("audit", "design"):
        result = engine().run_population_analysis(str(path), spec, stage)
        assert result["audit"]["expression_values_read"] is False


def test_repeating_cells_does_not_change_biological_design_degrees_of_freedom(tmp_path):
    path, spec, _, data = fixture(tmp_path)
    original = engine().run_population_analysis(str(path), spec, "design")
    repeated = ad.concat([data, data], index_unique="copy")
    repeated.write_h5ad(path)
    spec["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    actual = engine().run_population_analysis(str(path), spec, "design")
    assert actual["design"]["matrix"] == original["design"]["matrix"]
    assert actual["design"]["diagnostics"] == original["design"]["diagnostics"]
    assert actual["audit"]["selected_cell_count"] == 2*original["audit"]["selected_cell_count"]


def test_complete_feature_panel_limit_rejects_before_count_materialization(tmp_path, monkeypatch):
    from devbench.analysis.io import BackedExpression
    path, spec, _, _ = fixture(tmp_path)
    spec["limits"] = {"max_panel_bytes": 128}
    def forbidden(*args, **kwargs):
        pytest.fail("Over-budget complete panel must be refused before reads")
    monkeypatch.setattr(BackedExpression, "iter_rows", forbidden)
    with pytest.raises(ValueError, match="Complete feature pseudobulk panel"):
        engine().run_population_analysis(str(path), spec, "pseudobulk")


def test_count_rejection_closes_hdf5_handle(tmp_path, monkeypatch):
    path, spec, _, _ = fixture(tmp_path, bad=-2)
    from devbench.core import population_data

    module = engine()
    original = population_data._open_selected
    handles = []
    def capture(*args):
        source = original(*args)
        handles.append(source)
        return source
    monkeypatch.setattr(population_data, "_open_selected", capture)
    with pytest.raises(ValueError, match="counts"):
        module.run_population_analysis(str(path), spec, "pseudobulk")
    assert handles and all(source.closed for source in handles)


def test_declared_feature_and_cell_identifiers_cannot_repeat(tmp_path):
    path, spec, _, data = fixture(tmp_path)
    data.var_names = ["duplicate"] * data.n_vars
    data.write_h5ad(path)
    spec["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="identifiers must be unique"):
        engine().run_population_analysis(str(path), spec, "audit")


def test_source_cannot_be_a_symlink(tmp_path):
    path, spec, _, _ = fixture(tmp_path)
    link = tmp_path / "link.h5ad"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="canonical absolute"):
        engine().run_population_analysis(str(link), spec, "audit")


def test_registered_supervised_audit_captures_actual_event_and_cleanup(tmp_path):
    from devharness_server.method_adapter import dispatcher_contract, execute_dispatcher
    path, spec, _, _ = fixture(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    result = execute_dispatcher(dispatcher_contract("scrna_population_audit"),
        {"anndata": str(path)}, {"specification": spec}, output)
    assert result["exit_code"] == 0, result
    assert result["tool_events"][0]["tool_name"] == "scrna_population_audit"
    artifact = json.loads((output / "result.json").read_text())
    assert artifact["supervision"]["cleanup"]["verified_stopped"] is True
    assert artifact["audit"]["feature_count"] == 24


def test_registered_pre_start_cancellation_does_not_read_input(tmp_path):
    import threading
    from devharness_core.watchdog import WorkerCancelled, cancel_workers_on
    from devharness_server.tools_scrna_population import scrna_population_audit
    path, spec, _, _ = fixture(tmp_path)
    event = threading.Event()
    event.set()
    with cancel_workers_on(event), pytest.raises(WorkerCancelled):
        scrna_population_audit(str(path), spec)


def test_registered_worker_timeout_retains_verified_cleanup(tmp_path):
    from devharness_core.watchdog import HarnessResourceInterrupt
    from devharness_server.tools_scrna_population import scrna_population_contrast
    path, spec, _, _ = fixture(tmp_path)
    spec["limits"] = {"timeout_seconds": 1}
    with pytest.raises(HarnessResourceInterrupt, match="timed out"):
        scrna_population_contrast(str(path), spec)


@pytest.mark.parametrize("defect", ["trailing_data", "nonmonotone", "start_not_zero", "negative_coordinate", "coordinate_out_of_range", "pointer_length", "index_length"])
def test_sparse_storage_structure_cannot_hide_or_invent_count_entries(tmp_path, defect):
    import h5py
    path, spec, _, _ = fixture(tmp_path)
    with h5py.File(path, "r+") as handle:
        group = handle["layers/counts"]
        if defect == "trailing_data":
            group["indptr"][-1] -= 1
        elif defect == "nonmonotone":
            group["indptr"][2] = 0
        elif defect == "start_not_zero":
            group["indptr"][0] = 1
        elif defect == "negative_coordinate":
            group["indices"][0] = -1
        elif defect == "coordinate_out_of_range":
            group["indices"][0] = 24
        else:
            name = "indptr" if defect == "pointer_length" else "indices"
            values = group[name][:-1]
            del group[name]
            group.create_dataset(name, data=values)
    spec["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="Sparse count storage"):
        engine().run_population_analysis(str(path), spec, "pseudobulk")


@pytest.mark.parametrize("external_kind", ["external", "virtual"])
def test_selected_hdf5_external_and_virtual_data_cannot_escape_source_hash(tmp_path, external_kind):
    import h5py
    path, spec, values, _ = fixture(tmp_path, layout="dense")
    with h5py.File(path, "r+") as handle:
        group = handle["layers"]
        attrs = dict(group["counts"].attrs)
        del group["counts"]
        if external_kind == "external":
            data = group.create_dataset("counts", shape=values.shape, dtype=values.dtype,
                external=[(str(tmp_path / "unbound.raw"), 0, h5py.h5f.UNLIMITED)])
            data[:] = values
        else:
            companion = tmp_path / "unbound.h5"
            with h5py.File(companion, "w") as external:
                external.create_dataset("data", data=values)
            layout = h5py.VirtualLayout(shape=values.shape, dtype=values.dtype)
            layout[:] = h5py.VirtualSource(str(companion), "data", shape=values.shape)
            data = group.create_virtual_dataset("counts", layout)
        data.attrs.update(attrs)
    spec["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="external or virtual"):
        engine().run_population_analysis(str(path), spec, "audit")


def test_hdf5_metadata_index_names_cannot_traverse_links(tmp_path):
    import h5py
    path, spec, _, _ = fixture(tmp_path)
    with h5py.File(path, "r+") as handle:
        handle["obs"].attrs["_index"] = "nested/axis"
        nested = handle["obs"].create_group("nested")
        nested["axis"] = handle["obs/_index"]
    spec["source_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="direct child"):
        engine().run_population_analysis(str(path), spec, "audit")


def test_one_cell_profiles_retain_explicit_support_warning(tmp_path):
    path, spec, _, _ = fixture(tmp_path, cells_per_profile=1)
    result = engine().run_population_analysis(str(path), spec, "design")
    warnings = result["design"]["warnings"]
    low_cells = next(item for item in warnings if item["code"] == "low_cell_count_population_profiles")
    assert low_cells["profile_count"] == 12
    assert low_cells["threshold_origin"] == "assistant_proposed_diagnostic"
    assert low_cells["exclusions_applied"] is False
    assert all(row["cell_count"] == 1 for row in low_cells["profiles"])


def test_full_hypothesis_family_has_child_side_preallocation_bound(tmp_path, monkeypatch):
    path, spec, _, _ = fixture(tmp_path)
    module = engine()
    monkeypatch.setattr(module, "MAX_HYPOTHESES", 47, raising=False)
    with pytest.raises(ValueError, match="Complete gene-by-stage family"):
        module.run_population_analysis(str(path), spec, "contrast")


def test_contrast_child_rejects_oversized_final_json_before_transport(tmp_path, monkeypatch):
    path, spec, _, _ = fixture(tmp_path, absent=True)
    module = engine()
    monkeypatch.setattr(module, "MAX_RESULT_BYTES", 500, raising=False)
    with pytest.raises(ValueError, match="result.*byte bound"):
        module.run_population_analysis(str(path), spec, "contrast")


def test_exact_contrast_adapter_persists_large_full_artifact_and_small_event(tmp_path, monkeypatch):
    import devharness_server.method_adapter as adapter
    from devharness_core.provenance import canonical_json
    path, spec, _, _ = fixture(tmp_path)
    from devharness_server import tools_scrna_population
    # Real registered function and live contract, replacing only its expensive
    # numerical worker with a deterministic synthetic full-result emitter.
    payload = {"format": "devbench.scrna-population-contrast/1", "status": "scientific_failure",
        "source_sha256": spec["source_sha256"], "specification_sha256": "a"*64,
        "summary": {"attempted_hypothesis_count": 60000, "full_feature_count": 30000},
        "audit": {"feature_count": 30000, "observation_ids": ["small"], "population_membership": [0]},
        "design": {"diagnostics": {"rank": 8}, "profiles": []}, "testing_family": {"attempted_n": 60000},
        "diagnostics": {"gene_fit_failures": {"g": "x"}}, "runtime_events": [], "limitations": [],
        "gene_statistics": [{"gene_id": str(index), "failure_code": "x" * 300, "p_value": None, "q_value": None}
                            for index in range(60000)]}
    monkeypatch.setattr(tools_scrna_population, "_run", lambda *_args: payload)
    contract = adapter.dispatcher_contract("scrna_population_contrast")
    assert set(contract.to_dict()["outputs"]) == {"result", "summary"}
    output = tmp_path / "large-output"
    output.mkdir()
    execution = adapter.execute_dispatcher(contract, {"anndata": str(path)}, {"specification": spec}, output)
    assert execution["exit_code"] == 0, execution
    body = (output / "result.json").read_bytes()
    assert 16_000_000 < len(body) < 96_000_000
    assert json.loads(body)["gene_statistics"] == payload["gene_statistics"]
    summary = json.loads((output / "summary.json").read_text())
    assert "gene_statistics" not in summary and "observation_ids" not in summary["audit"]
    assert summary["result_artifact"] == {"sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    assert len(canonical_json(execution)) < 256_000
    assert execution["tool_events"][0]["result"]["result_artifact"] == summary["result_artifact"]
    # Exercise the real artifact service, validation, audit DAG and retained-input
    # replay, not just the executor's local file write.
    from devbench.analysis.methods import MethodService
    methods = MethodService(tmp_path / "method-workspace")
    registered = methods.register_dispatcher("scrna_population_contrast", actor="synthetic-test")
    artifact = methods.import_artifact(path, artifact_type="anndata", actor="synthetic-test")
    analysis = methods.create_analysis("Synthetic large-result transport", actor="synthetic-test")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-test")
    step = methods.add_step(branch["branch_id"], registered["version_id"],
        {"anndata": artifact["artifact_id"]}, {"specification": spec}, actor="synthetic-test")
    with pytest.raises(PermissionError, match="fresh explicit data execution authority"):
        methods.run_step(step["step_id"], actor="synthetic-test")
    run = methods.run_step(step["step_id"], actor="synthetic-test", data_execution_authorized=True)
    assert run["status"] == "succeeded", run
    assert set(run["outputs"]) == {"result", "summary"}
    full_artifact = methods.get("artifact", run["outputs"]["result"])
    assert full_artifact["sha256"] == hashlib.sha256(body).hexdigest()
    assert full_artifact["size_bytes"] == len(body)
    assert methods.provenance(run["run_id"])["valid"] is True
    with pytest.raises(PermissionError, match="fresh explicit data execution authority"):
        methods.reproduce(run["run_id"], actor="synthetic-test")
    replay = methods.reproduce(run["run_id"], actor="synthetic-test", data_execution_authorized=True)
    assert replay["status"] == "succeeded", replay
    assert replay["reproduces"] == run["run_id"]
    assert methods.get("artifact", replay["outputs"]["result"])["sha256"] == full_artifact["sha256"]
    assert methods.provenance(replay["run_id"])["valid"] is True
