"""Registered AnnData headers enter the governed RNA graph without invented facts."""
from copy import deepcopy

import pytest

from tests.test_scientific_workspace import tmp_path


def registered(tmp_path, *, duplicated_ids=False):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse
    from devbench.analysis.methods import MethodService
    source = tmp_path / "declared-cells.h5ad"
    data = ad.AnnData(sparse.csr_matrix(np.arange(24, dtype=np.int32).reshape(6, 4)),
        obs=pd.DataFrame({"capture": ["A", "A", "A", "B", "B", "B"], "donor": ["same"] * 6},
            index=["duplicate"] * 6 if duplicated_ids else [f"cell-{i}" for i in range(6)]),
        var=pd.DataFrame(index=[f"gene-{i}" for i in range(4)]))
    data.layers["counts"] = data.X.copy()
    data.obsm["X_umap"] = np.zeros((6, 2))
    data.write_h5ad(source)
    methods = MethodService(tmp_path / "methods")
    artifact = methods.import_artifact(source, artifact_type="anndata", actor="software-verifier")
    binding = {"format": "devbench.scrna-input-binding/1", "anndata_artifact_id": artifact["artifact_id"],
        "declarations": {"assay": "scrna", "measurement": "umi_counts", "count_provenance": "Synthetic integer software fixture",
            "counts_layer": "counts", "capture_unit_key": "capture", "capture_units_confirmed": True,
            "annotation_provenance": "No biological annotations supplied", "biological_independence": None}}
    return methods, artifact, binding


def test_registered_profile_separates_headers_declarations_and_unknown_units(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import inspect_registered_scrna_input, verify_scrna_inspection
    methods, artifact, binding = registered(tmp_path, duplicated_ids=True)
    before = methods.artifact_path(artifact["artifact_id"]).read_bytes()
    result = inspect_registered_scrna_input(methods, binding)
    assert result["format"] == "devbench.scrna-input/1"
    assert result["observed"]["cells"] == 6 and result["observed"]["genes"] == 4
    assert result["observed"]["obs_columns"] == ["capture", "donor"]
    assert result["observed"]["layers"] == ["counts"] and result["observed"]["obsm_keys"] == ["X_umap"]
    assert result["observed"]["expression_values_read"] is False
    assert result["observed"]["observation_ids_read"] is False
    assert result["observed"]["annotation_values_read"] is False
    assert result["declarations"] == binding["declarations"]
    assert result["biological_units"]["independent_unit_count"] is None
    assert result["biological_units"]["independence_verified"] is False
    assert result["input_artifact_refs"]["anndata"]["sha256"] == artifact["sha256"]
    assert any(row["field"] == "biological_independence" for row in result["unknowns"])
    assert verify_scrna_inspection(methods, result) == result
    assert methods.artifact_path(artifact["artifact_id"]).read_bytes() == before


@pytest.mark.parametrize("change", [
    {"counts_layer": "absent"}, {"capture_unit_key": "not-a-column"}, {"capture_units_confirmed": False},
    {"assay": "bulk"}, {"assay": []}, {"measurement": {}}, {"count_provenance": ""},
    {"biological_independence": {"independent": True, "unit_column": "missing", "evidence": "Claimed study record"}},
])
def test_invalid_or_unbound_declarations_fail_before_planning(tmp_path, change):
    from devbench.analysis.rna_scrna_input_contract import inspect_registered_scrna_input
    methods, _, binding = registered(tmp_path)
    binding["declarations"].update(change)
    with pytest.raises(ValueError):
        inspect_registered_scrna_input(methods, binding)
    assert methods.list_records("method_plan") == []


def test_tampered_artifact_header_or_retained_inspection_is_rejected(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import inspect_registered_scrna_input, verify_scrna_inspection
    methods, artifact, binding = registered(tmp_path)
    result = inspect_registered_scrna_input(methods, binding)
    fabricated = deepcopy(result)
    fabricated["observed"]["cells"] = 999
    with pytest.raises(ValueError, match="changed"):
        verify_scrna_inspection(methods, fabricated)
    path = methods.artifact_path(artifact["artifact_id"])
    path.chmod(0o644)
    with path.open("ab") as output:
        output.write(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        inspect_registered_scrna_input(methods, binding)


def test_external_hdf5_storage_is_not_treated_as_bound_input(tmp_path):
    import h5py
    from devbench.analysis.rna_scrna_input_contract import inspect_registered_scrna_input
    methods, _, binding = registered(tmp_path)
    source = tmp_path / "external.h5ad"
    import shutil
    shutil.copy2(methods.artifact_path(binding["anndata_artifact_id"]), source)
    source.chmod(0o644)
    with h5py.File(source, "a") as data:
        data["unregistered_external"] = h5py.ExternalLink("not-read.h5", "/matrix")
    ref = methods.import_artifact(source, artifact_type="anndata", actor="software-verifier")
    binding["anndata_artifact_id"] = ref["artifact_id"]
    with pytest.raises(ValueError, match="external|symbolic"):
        inspect_registered_scrna_input(methods, binding)


def test_planner_binds_verified_scrna_and_never_upgrades_header_prerequisites(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_entrypoints import is_rna_plan
    methods, artifact, binding = registered(tmp_path)
    planner = ScientificMethodPlanner(methods)
    original = {"observed": {"metrics_finite": True, "observation_ids_unique": True, "biological_units_independent": True},
        "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]}
    plan = planner.create({"scientific_objective": "Compare appropriate cellular QC methods", "computational_task": "scRNA QC"},
        original, actor="software-verifier", data_classification="synthetic", rna_input_contract=binding)
    assert is_rna_plan(plan)
    observed = plan["context"]["dataset_summary"]["observed"]
    assert "bulk_rna_input" not in observed
    assert observed["scrna_input"]["observed"]["cells"] == 6
    assert observed["input_integrity_valid"] is True
    for field in ("metrics_finite", "observation_ids_unique", "biological_units_independent"):
        assert observed[field] is None
    inspected, _, record = planner._observation_inspection(plan["context"]["dataset_summary"],
        [artifact["artifact_id"]], {"metrics_finite"}, actor="software-verifier", require_roles=True)
    assert inspected["observed"]["metrics_finite"] is None
    assert record["expression_values_read"] is False
    assert original["observed"]["metrics_finite"] is True
    with pytest.raises(ValueError, match="explicit registered"):
        planner.create({"scientific_objective": "Forged profile", "computational_task": "scRNA QC"},
            plan["context"]["dataset_summary"], actor="software-verifier")


def test_declared_representation_and_independence_are_not_numeric_or_replication_proof(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import inspect_registered_scrna_input
    methods, artifact, binding = registered(tmp_path)
    binding["declarations"].update(measurement="normalized_expression", counts_layer="X",
        biological_independence={"independent": True, "unit_column": "donor", "evidence": "Supplied study record, not independently checked"})
    before = methods.artifact_path(artifact["artifact_id"]).read_bytes()
    result = inspect_registered_scrna_input(methods, binding)
    assert result["declarations"]["measurement"] == "normalized_expression"
    assert result["declarations"]["biological_independence"]["independent"] is True
    assert result["biological_units"]["independence_verified"] is False
    assert result["biological_units"]["independent_unit_count"] is None
    assert result["observed"]["expression_values_read"] is False
    assert methods.artifact_path(artifact["artifact_id"]).read_bytes() == before


def test_null_scientific_prerequisite_remains_unknown_for_scrna_candidates(tmp_path):
    from devbench.analysis.method_planner import _applicability
    from tests.test_method_planner import candidate
    item = candidate()
    item["assumptions"] = [{"statement": "Measurements are finite", "evidence": None, "status": "unknown",
        "hard_precondition": True, "field": "metrics_finite", "operator": "equals", "expected": True}]
    resolved = _applicability(item, {"observed": {"scrna_input": {}, "metrics_finite": None}, "unknowns": []})
    assert resolved["assumptions"][0]["status"] == "unknown"
    assert resolved["applicability"] == "blocked_unknown"


def test_companion_cannot_contradict_header_or_upgrade_unverified_units(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import match_scrna_scientific_contract
    inspection = {"declarations": {"measurement": "umi_counts"}}
    contract = {"assay": {"representation": "direct_counts", "orientation": "cell_by_feature",
        "feature_ids_sha256": None, "observation_ids_sha256": None, "metadata_sha256": None},
        "biological_units": [], "unknowns": [{"field": "biological_units"}]}
    assert match_scrna_scientific_contract(contract, inspection) == contract
    for edit in ({"orientation": "feature_by_sample"}, {"representation": "normalized_expression"},
                 {"observation_ids_sha256": "a" * 64}):
        changed = deepcopy(contract)
        changed["assay"].update(edit)
        with pytest.raises(ValueError):
            match_scrna_scientific_contract(changed, inspection)
    upgraded = deepcopy(contract)
    upgraded["biological_units"] = [{"independence": "independent"}]
    with pytest.raises(ValueError, match="independence"):
        match_scrna_scientific_contract(upgraded, inspection)


def test_scrna_audit_uses_the_same_real_graph_and_stops_before_trial_selection(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_entrypoints import advance_workflow_to_gate
    from tests.test_method_planner import Selector, candidate
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    methods, artifact, binding = registered(tmp_path)
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Assess appropriate scRNA methods", "computational_task": "scRNA method selection"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor="software-verifier", data_classification="synthetic", rna_input_contract=binding)
    alternative = candidate("Scientifically appropriate but unavailable alternative")
    alternative["assumptions"] = []
    selector = Selector({"candidates": [alternative]}, {"bindings": [{"candidate": alternative["name"],
        "method_version_id": None, "discovery": None}]})
    result = advance_workflow_to_gate(planner, plan["session_id"], actor="software-verifier", selector=selector,
        scientific_workflow=RecordingScientificRuntime())
    assert result["gate"] == "review" and result["status"] == "awaiting_operator_review"
    assert [row["node"] for row in result["steps"]] == ["audit", "question", "discovery", "generate",
        "candidate_review", "resolve", "implementation", "design", "design_review"]
    assert len(selector.contexts) == 2 and planner.get(plan["session_id"])["experiments"] == []
    assert planner.get(plan["session_id"])["candidates"][0]["method_version_id"] is None
