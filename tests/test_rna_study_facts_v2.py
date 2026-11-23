"""Synthetic intake controls for the inherited-analysis and capability contracts."""
import hashlib

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix


def _write_source(path, *, raw_counts=None):
    counts = np.array([[1, 0, 4], [0, 2, 5], [2, 3, 0]], dtype=np.int32)
    original = ad.AnnData(
        csr_matrix(counts if raw_counts is None else raw_counts),
        obs=pd.DataFrame({"donor": ["a", "b", "c"], "cluster": ["A", "A", "B"]},
                         index=["c1", "c2", "c3"]),
        var=pd.DataFrame(index=["g1", "g2", "g3"]),
    )
    current = original[:, :2].copy()
    current.X = np.log1p(counts[:, :2]).astype(float)
    current.raw = original
    current.layers["imputed"] = counts[:, :2].copy()
    current.obsm["X_pca"] = np.ones((3, 2))
    current.varm["PCs"] = np.ones((2, 2))
    current.obsp["neighbors"] = csr_matrix(np.eye(3))
    current.varp["correlation"] = np.eye(2)
    current.uns["pca"] = {"method": "software-control"}
    current.write_h5ad(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _claim(path, sha, **fields):
    return {"path": path, "artifact_sha256": sha, "source": "declared synthetic source record", **fields}


def test_inherited_slots_raw_axes_and_scientific_holds(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import audit_inherited_anndata

    path = tmp_path / "inherited.h5ad"
    sha = _write_source(path)
    provenance = [
        _claim("X", sha, representation="normalized_expression"),
        _claim("layers/imputed", sha, representation="imputed_expression"),
        _claim("raw/X", sha, representation="original_counts"),
        _claim("obs/cluster", sha, label_origin="expression_based_study_annotation"),
        _claim("obsm/X_pca", sha, training_scope="full_cohort"),
    ]
    with h5py.File(path, "r") as handle:
        audit = audit_inherited_anndata(handle, sha, provenance)
    assert set(audit["slots"]) == {"X", "layers", "obs", "var", "obsm", "varm", "obsp", "varp", "uns", "raw"}
    assert all(slot["present"] for slot in audit["slots"].values())
    records = {item["path"]: item for slot in audit["slots"].values() for item in slot["objects"]}
    assert records["raw/X"]["shape"] == [3, 3]
    assert records["raw/X"]["integer_counts_verified"]
    assert records["raw/X"]["count1_admissible"]
    assert records["raw/X"]["axes"]["feature_axis"] == "raw/var"
    assert len(records["raw/X"]["axes"]["feature_ids_sha256"]) == 64
    assert not records["X"]["count1_admissible"]
    assert not records["layers/imputed"]["count1_admissible"]
    assert not records["obs/cluster"]["primary_pp_label_admissible"]
    assert not records["obsm/X_pca"]["held_out_admissible"]
    assert records["obsp/neighbors"]["provenance_status"] == "unknown"
    assert records["obsp/neighbors"]["shape"] == [3, 3]
    assert records["obsp/neighbors"]["aligned"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha


def test_raw_slot_is_not_evidence_of_original_counts(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import audit_inherited_anndata

    path = tmp_path / "unknown.h5ad"
    sha = _write_source(path)
    with h5py.File(path, "r") as handle:
        audit = audit_inherited_anndata(handle, sha)
        raw = next(item for item in audit["slots"]["raw"]["objects"] if item["path"] == "raw/X")
        assert raw["integer_counts_verified"]
        assert not raw["count1_admissible"]
        with pytest.raises(ValueError, match="artifact"):
            audit_inherited_anndata(handle, sha, [_claim("raw/X", "0" * 64, representation="original_counts")])


def test_fractional_raw_values_fail_count_admission(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import audit_inherited_anndata

    path = tmp_path / "fractional.h5ad"
    sha = _write_source(path, raw_counts=np.full((3, 3), 1.5))
    with h5py.File(path, "r") as handle:
        audit = audit_inherited_anndata(handle, sha, [_claim("raw/X", sha, representation="original_counts")])
    raw = next(item for item in audit["slots"]["raw"]["objects"] if item["path"] == "raw/X")
    assert not raw["integer_counts_verified"]
    assert not raw["count1_admissible"]


def test_capability_does_not_confer_qualification():
    from devbench.core.procedure_profile import OperationCapability, ProcedureProfile

    operation = OperationCapability(operation_id="prefit-inspection", output_kinds=("inventory",),
                                    effects=("read_registered_input",), max_memory_mb=512, timeout_seconds=20)
    fields = dict(profile_id="count-intake", version=1, operations=(operation,), input_representations=("original_counts",),
                  biological_units=("donor",), assumptions=("exact axis binding",), controls=("bounded read",))
    profile = ProcedureProfile(**fields, permitted_claims=("checked_observation",))
    assert profile.format == "devbench.procedure-profile/1"
    assert not profile.qualification_evidence
    with pytest.raises(ValueError, match="qualification evidence"):
        ProcedureProfile(**fields, permitted_claims=("qualified_procedure_result",))
    with pytest.raises(ValueError):
        profile.version = 2


def test_registered_intake_retains_alternate_count_source_without_substitution(tmp_path):
    from devbench.analysis.rna_scrna_input_contract import (
        INVENTORY_BINDING_FORMAT, inspect_registered_scrna_input, verify_scrna_inspection,
    )
    from devbench.analysis.methods import MethodService

    source = tmp_path / "registered.h5ad"
    sha = _write_source(source)
    methods = MethodService(tmp_path / "methods")
    artifact = methods.import_artifact(source, artifact_type="anndata", actor="software-test")
    binding = {
        "format": INVENTORY_BINDING_FORMAT,
        "anndata_artifact_id": artifact["artifact_id"],
        "declarations": {
            "assay": "scrna", "measurement": "normalized_expression", "counts_layer": "X",
            "count_provenance": "Synthetic input with separately supplied raw counts",
            "capture_unit_key": "donor", "capture_units_confirmed": True,
            "annotation_provenance": "Synthetic expression-derived labels",
        },
        "inherited_provenance": [
            _claim("X", sha, representation="normalized_expression"),
            _claim("raw/X", sha, representation="original_counts"),
        ],
    }
    inspected = inspect_registered_scrna_input(methods, binding)
    assert inspected["count_source"] == {
        "requested_path": "X", "source_sha256": sha, "count1_candidate_paths": ["raw/X"],
        "requested_count1_admissible": False, "hold": "transformed_or_estimated_measurement",
    }
    assert inspected["inspection"]["count_values_checked"]
    assert inspected["biological_units"]["independent_unit_count"] is None
    assert verify_scrna_inspection(methods, inspected) == inspected
    from devbench.analysis.method_planner import ScientificMethodPlanner

    planner = ScientificMethodPlanner(methods)
    plan = planner.create(
        {"scientific_objective": "Inspect inherited source provenance", "computational_task": "scRNA QC"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor="software-test", data_classification="synthetic", rna_input_contract=binding,
    )
    summary = plan["context"]["dataset_summary"]
    assert summary["observed"]["scrna_input"] == inspected
    assert summary["observed"]["biological_units_independent"] is None
    binding["declarations"].update(measurement="umi_counts", counts_layer="raw/X")
    raw_selected = inspect_registered_scrna_input(methods, binding)
    assert raw_selected["count_source"]["requested_path"] == "raw/X"
    assert raw_selected["count_source"]["requested_count1_admissible"]
    assert raw_selected["count_source"]["hold"] is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha
