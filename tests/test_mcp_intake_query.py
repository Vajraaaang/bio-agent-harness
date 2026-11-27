"""Exercise custody, design and disclosure using real synthetic files and the WAL."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import anndata as ad
import h5py
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import scipy.sparse as sp

from devbench.engine.workflow_operations import OperationBlocked
from devbench.mcp import answerability, intake, query, schemas
from devbench.mcp.context import ControlPlane
from devbench.platform.plugin_jobs import JobStore, digest

pytestmark = pytest.mark.wal


@pytest.fixture
def control(workspace_tmp_path):
    source = workspace_tmp_path / "inputs"
    source.mkdir()
    jobs = JobStore(workspace_tmp_path / "workspace")
    return ControlPlane(
        SimpleNamespace(input_root=source, jobs=jobs, mounted_image_id=None)
    )


def declaration(path, kind="metadata"):
    return {"path": str(path), "file_kind": kind, "data_classification": "synthetic"}


def register(
    control,
    path,
    *,
    sid=None,
    revision=None,
    kind="metadata",
    rep="metadata_only",
    classification="synthetic",
    origin="raw counts",
):
    result = intake.register_investigation_input(
        control,
        {
            "input": {
                **declaration(path, kind),
                "investigation_role": "primary_cohort",
                "data_classification": classification,
            },
            "provenance_declaration": {
                "source": "Synthetic fixture",
                "origin": "user_declaration",
                "biological_unit": "donor",
                "count_provenance": origin,
            },
            "representation_class": rep,
            "session_id": sid,
            "expected_revision": revision,
        },
    )
    schemas.validator("register_investigation_input", output=True).validate(result)
    return result


def inspect(control, path, kind="expression", **extra):
    result = intake.inspect_inputs(
        control, {"inputs": [{**declaration(path, kind), **extra}]}
    )
    schemas.validator("inspect_inputs", output=True).validate(result)
    return result["inputs"][0]


def page(control, admission, **options):
    ref = admission["artifact"] or admission["opaque_artifact"]
    result = query.query_artifact_table(
        control,
        {
            "session_id": admission["session_id"],
            "artifact_id": ref["artifact_id"],
            "artifact_sha256": ref["sha256"],
            **options,
        },
    )
    schemas.validator("query_artifact_table", output=True).validate(result)
    return result


def test_raw_attributes_and_monotonic_scope(control):
    path = control.input_root / "counts.csv"
    path.write_text("gene,s1,s2\na,1,3\nb,3,2\n")
    observed = inspect(control, path)
    assert observed["axes"]["features"] == 2
    assert observed["count_1_forensics"]["integer_valued"]
    public = register(control, path, kind="expression", rep="count_1_raw_counts")
    sid = public["session_id"]
    assert control.artifact(sid, public["artifact"]["artifact_id"])["detail"][
        "count_1_verified"
    ]
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        page(control, public)
    path2 = control.input_root / "copy.csv"
    path2.write_bytes(path.read_bytes())
    private = register(
        control, path2, sid=sid, revision=public["revision"], classification="clinical"
    )
    assert private["private_tainted"] and private["artifact"] is None
    assert set(private["opaque_artifact"]) == {"artifact_id", "sha256"}
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        inspect(control, path)
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        page(control, public)


def test_clinical_alias_fenced_before_schema_projection(control):
    path = control.input_root / "metadata.csv"
    path.write_text("sample,patientID,unknown_PRIVATE_SENTINEL\ns1,123,masked\n")
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        inspect(control, path, "metadata")
    grant = register(control, path)
    assert grant["private_tainted"]
    assert "PRIVATE_SENTINEL" not in json.dumps(grant)


def test_descriptor_pin_and_fractional_decimal(control):
    counts = control.input_root / "matrix.tsv"
    counts.write_text("gene\ts\nG\t1.0000000000000000001\n")
    assert not inspect(control, counts)["count_1_forensics"]["integer_valued"]
    symlink = control.input_root / "alias.tsv"
    symlink.symlink_to(counts)
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        inspect(control, symlink)


def test_parquet_filter_order_group_median_page_and_tamper(control):
    path = control.input_root / "observed.parquet"
    table = pa.table({"batch": ["a", "b", "a", "a", "b"], "score": [1, 12, None, 5, 4]})
    pq.write_table(table, path, row_group_size=2)
    result = register(control, path)
    read = page(
        control,
        result,
        columns=["score"],
        filters=[{"column": "score", "op": "gte", "value": 4}],
        order_by=[{"column": "score", "descending": True}],
        limit=2,
    )
    assert (
        read["rows"] == [[12], [5]]
        and read["total_matching_rows"] == 3
        and read["next_offset"] == 2
    )
    result2 = page(
        control,
        result,
        aggregate={
            "group_by": ["batch"],
            "metrics": [
                {"column": "score", "fn": "count"},
                {"column": "score", "fn": "mean"},
                {"column": "score", "fn": "median"},
            ],
        },
        order_by=[{"column": "median_score", "descending": True}],
    )
    assert result2["rows"] == [["b", 2, 8, 8], ["a", 2, 3, 3]]
    path.write_bytes(path.read_bytes() + b"modified")
    with pytest.raises(OperationBlocked, match="plan_identity_mismatch"):
        page(control, result)


def test_h5ad_obs_counts_and_external_link(control):
    path = control.input_root / "sample.h5ad"
    obs = pd.DataFrame(
        {"cell_type": pd.Categorical(["aa", "bb", "aa"]), "batch": [1, 2, 1]},
        index=["C1", "C2", "C3"],
    )
    ad.AnnData(X=sp.csr_matrix([[1, 0], [3, 2], [0, 1]]), obs=obs).write_h5ad(path)
    profile = inspect(control, path)
    assert profile["count_1_forensics"]["integer_valued"]
    assert profile["axes"]["observations"] == 3
    registered = register(control, path, kind="expression", rep="count_1_raw_counts")
    assert page(control, registered, table_slot="obs", columns=["cell_type", "batch"])[
        "rows"
    ] == [["aa", 1], ["bb", 2], ["aa", 1]]
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        page(control, registered)
    with h5py.File(path, "r+") as handle:
        handle["outside"] = h5py.ExternalLink("other.h5", "/outside")
    with pytest.raises(ValueError, match="external|symbolic"):
        inspect(control, path)


@pytest.mark.parametrize("sparse", [False, True])
def test_zarr_merkle_binds_chunks_and_counts(control, sparse):
    path = control.input_root / "sample.zarr"
    data = np.array([[1, 0], [3, 2], [0, 1]])
    ad.AnnData(X=sp.csr_matrix(data) if sparse else data).write_zarr(path)
    profile = inspect(control, path)
    assert profile["merkle_root_sha256"] == profile["sha256"]
    assert profile["count_1_forensics"]["integer_valued"]
    assert profile["axes"]["features"] == 2
    pin = register(control, path, kind="expression", rep="count_1_raw_counts")
    record = control.artifact(pin["session_id"], pin["artifact"]["artifact_id"])
    intake.verify_artifact_identity(control, record)
    (path / "unrelated").write_bytes(b"new")
    with pytest.raises(OperationBlocked, match="plan_identity_mismatch"):
        intake.verify_artifact_identity(control, record)


def test_mtx_manifest_real_identity(control):
    path = control.input_root / "matrix.mtx"
    path.write_text(
        "%%MatrixMarket matrix coordinate integer general\n"
        "% test\n2 2 2\n1 1 1\n2 2 3\n"
    )
    result = register(control, path, kind="expression", rep="count_1_raw_counts")
    ref = result["artifact"]
    record = control.artifact(result["session_id"], ref["artifact_id"])
    assert ref["format"] == "json"
    assert (
        record["detail"]["source_sha256"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert record["detail"]["count_1_verified"]


def test_answerability_confounds_repeat_units_and_no_code_evaluation(control):
    path = control.input_root / "samples.csv"
    path.write_text(
        "donor,condition,batch,library\n"
        "d1,A,1,r1\nd1,A,1,r2\nd2,A,1,r3\nd3,B,2,r4\nd4,B,2,r5\n"
    )
    registration = register(control, path)
    aid = registration["artifact"]["artifact_id"]
    args = {
        "session_id": registration["session_id"],
        "question_text": "Group effect",
        "axis_b_class": "B1",
        "assay": "bulk_rna_seq",
        "entry_point": "count_matrix",
        "artifact_ids": [aid],
        "candidate_kernels": [],
        "design": {
            "metadata_artifact_id": aid,
            "biological_unit": "donor",
            "formula": "~ condition",
            "batch_columns": ["batch"],
            "forensics_verified": True,
            "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
        },
    }
    response = answerability.check_answerability(control, args)
    assert response["status"] == "refused"
    assert response["design_diagnostics"]["biological_units_per_group"] == {
        "A": 2,
        "B": 2,
    }
    assert not response["design_diagnostics"]["full_rank"]
    args["design"]["formula"] = "~ eval('0')"
    with pytest.raises(ValueError):
        answerability.check_answerability(control, args)
    opened = answerability.check_answerability(
        control,
        {
            "question_text": "Custom",
            "axis_b_class": "B_OPEN",
            "assay": "open",
            "entry_point": "custom",
        },
    )
    assert opened["status"] == "tier2_exploratory_only"


def test_json_projection_bytes_cap_and_previously_embargoed_input(control):
    path = control.input_root / "summary.json"
    path.write_text(
        json.dumps([{"label": str(i), "value": "a" * 4096} for i in range(40)])
    )
    grant = register(control, path, rep="derived_summary")
    result = page(control, grant, limit=40)
    assert (
        0 < result["returned_rows"] < 40
        and result["next_offset"] == result["returned_rows"]
    )
    assert len(json.dumps(result).encode()) <= schemas.PAYLOAD_BYTES
    sid = grant["session_id"]
    sha = grant["artifact"]["sha256"]
    with control.authority.daemon_transaction() as db:
        control.authority._append_execution_record(
            db,
            sid,
            {
                "kind": "execution_event",
                "record_id": "embargo-" + sha,
                "event": "protected_artifact",
                "state": {"sha256": sha},
                "state_sha256": digest({"sha256": sha}),
            },
        )
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        page(control, grant)


def test_large_json_sequence_and_hidden_manifest_source(control):
    path = control.input_root / "records.json"
    with path.open("w") as handle:
        handle.write("[")
        for i in range(5500):
            if i:
                handle.write(",")
            json.dump({"row": i, "message": "m" * 1000}, handle)
        handle.write("]")
    registered = register(control, path, rep="derived_summary")
    result = page(control, registered, columns=["row"], limit=2, offset=5498)
    assert result["total_matching_rows"] == 5500
    assert result["rows"] == [[5498], [5499]]
    manifest = control.input_root / "manifest.json"
    manifest.write_text(json.dumps([{"fastq": "sequencing/patientID-42.fastq.gz"}]))
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        inspect(control, manifest, "manifest")


def test_one_spatial_table_binds_its_expression_and_obs(control):
    import zarr

    path = control.input_root / "tissue.zarr"
    zarr.open_group(str(path), mode="w")
    table = path / "tables" / "cells"
    table.parent.mkdir()
    ad.AnnData(X=np.array([[1, 2], [2, 3]])).write_zarr(table)
    result = inspect(control, path)
    assert result["format"] == "spatialdata_zarr"
    assert result["axes"]["observations"] == 2
    assert result["count_1_forensics"]["integer_valued"]


def test_public_500mb_36k_feature_h5ad_header_and_hash(control):
    path = control.input_root / "wide.h5ad"
    shape = (4, 36_000)
    ad.AnnData(X=sp.csr_matrix(shape, dtype=np.int32)).write_h5ad(path)
    # Sparse filesystem padding exercises the real full-file hash past the old
    # public-size cutoff without requiring a dense matrix or large fixture.
    with path.open("r+b") as handle:
        handle.truncate(500 * 1024**2)
    observed = inspect(control, path)
    assert observed["size_bytes"] == 500 * 1024**2
    assert observed["axes"]["features"] == 36_000
    assert len(observed["sha256"]) == 64


def test_b1_diagnostics_preserve_count_ancestry_and_metadata_binding(
    control, monkeypatch
):
    from devbench.mcp import registry

    metadata = control.input_root / "design.csv"
    metadata.write_text("donor,condition\nd1,A\nd2,A\nd3,B\nd4,B\n")
    counts = control.input_root / "input.csv"
    counts.write_text("gene,d1,d2,d3,d4\nx,0,1,3,2\n")
    admitted = register(control, counts, kind="expression", rep="count_1_raw_counts")
    sid = admitted["session_id"]
    design = register(control, metadata, sid=sid)
    monkeypatch.setattr(
        registry,
        "list_kernels_and_methods",
        lambda *_: {
            "kernels": [
                {
                    "kernel_id": "synthetic_design_fixture",
                    "required_representations": ["count_1_raw_counts", "metadata_only"],
                    "runtime_ready": True,
                    "calibration_status": "uncalibrated_L1",
                }
            ]
        },
    )
    args = {
        "session_id": sid,
        "question_text": "Compare conditions",
        "assay": "bulk_rna_seq",
        "entry_point": "count_matrix",
        "axis_b_class": "B1",
        "artifact_ids": [admitted["artifact"]["artifact_id"]],
        "design": {
            "metadata_artifact_id": design["artifact"]["artifact_id"],
            "formula": "~condition",
            "biological_unit": "donor",
            "forensics_verified": True,
            "contrast": {"factor": "condition", "numerator": "B", "denominator": "A"},
        },
    }
    result = answerability.check_answerability(control, args)
    assert result["status"] == "ready"
    assert result["design_diagnostics"]["residual_df"] == 2
    assert result["recommended_kernels"][0]["calibration_status"] == "uncalibrated_L1"

    unknown = control.input_root / "source.csv"
    unknown.write_text("gene,d1,d2,d3,d4\nx,2,1,3,2\n")
    transformed = register(
        control,
        unknown,
        sid=sid,
        kind="expression",
        rep="count_1_raw_counts",
        origin="rounded normalized counts",
    )
    args["artifact_ids"] = [transformed["artifact"]["artifact_id"]]
    result = answerability.check_answerability(control, args)
    assert result["status"] == "awaiting_information"
    assert result["recommended_kernels"] == []


def test_nullable_anndata_and_sparse_large_offset(control):
    import sys

    path = control.input_root / "nullable.h5ad"
    obs = pd.DataFrame(
        {
            "label": pd.Categorical(["x", None, "x"]),
            "number": pd.array([1, None, 3], dtype="Int64"),
        },
        index=["a", "b", "c"],
    )
    ad.AnnData(X=sp.csr_matrix((3, 2), dtype=np.int32), obs=obs).write_h5ad(path)
    grant = register(control, path, kind="expression", rep="count_1_raw_counts")
    result = page(
        control, grant, table_slot="obs", columns=["label", "number"], offset=1
    )
    assert result["rows"] == [[None, None], ["x", 3]]
    assert page(control, grant, table_slot="obs", offset=sys.maxsize + 1)["rows"] == []


def test_clinical_hardlink_alias_is_a_header_even_when_object_seen(control):
    path = control.input_root / "aliased.h5ad"
    obs = pd.DataFrame({"aa": [1, 2]}, index=["a", "b"])
    ad.AnnData(X=sp.csr_matrix((2, 2), dtype=np.int32), obs=obs).write_h5ad(path)
    with h5py.File(path, "r+") as h5:
        h5["obs/patientID"] = h5["obs/aa"]
    result = register(control, path, kind="expression", rep="count_1_raw_counts")
    assert result["private_tainted"] and result["artifact"] is None


def _directory_with_payload(control, name, payload):
    directory = control.input_root / name
    directory.mkdir()
    (directory / ".zgroup").write_text('{"zarr_format":2}')
    (directory / "payload.bin").write_bytes(payload)
    return directory


def test_private_leaf_alias_taints_a_differently_named_store(control):
    leaf = control.input_root / "restricted.csv"
    leaf.write_text("sample,observation\nS1,does-not-leave\n")
    register(control, leaf, classification="private")
    tree = _directory_with_payload(control, "atlas.zarr", leaf.read_bytes())
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        inspect(control, tree)
    scoped = register(
        control, tree, kind="expression", rep="integrated_latent_or_graph"
    )
    assert scoped["private_tainted"] and scoped["artifact"] is None
    assert "payload" not in json.dumps(scoped)


def test_newly_embargoed_directory_member_revokes_preexisting_binding(control):
    raw = b"sample,value\nS1,1\n"
    tree = _directory_with_payload(control, "atlas.zarr", raw)
    grant = register(control, tree, kind="expression", rep="integrated_latent_or_graph")
    sid, ref = grant["session_id"], grant["artifact"]
    registered = control.artifact(sid, ref["artifact_id"])
    sha = hashlib.sha256(raw).hexdigest()
    with control.authority.daemon_transaction() as db:
        control.authority._append_execution_record(
            db,
            sid,
            {
                "kind": "execution_event",
                "record_id": "embargo-" + sha,
                "event": "protected_artifact",
                "state": {"sha256": sha},
                "state_sha256": digest({"sha256": sha}),
            },
        )
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        intake.verify_artifact_identity(control, registered)
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        inspect(control, tree)
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        register(control, tree)


def test_newly_restricted_leaf_closes_session_and_tree_rebinding(control):
    raw = b"sample,value\nS1,1\n"
    tree = _directory_with_payload(control, "data.zarr", raw)
    public = register(
        control, tree, kind="expression", rep="integrated_latent_or_graph"
    )
    sid = public["session_id"]
    leaf = control.input_root / "observations.csv"
    leaf.write_bytes(raw)
    register(control, leaf, classification="clinical")
    assert control.protected(sid)
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        inspect(control, tree)


def test_raw_matrix_cannot_be_disclosed_as_a_plot_sidecar(control):
    from devbench.mcp.context import store_artifact

    metadata = control.input_root / "metadata.csv"
    metadata.write_text("sample,group\ns1,A\n")
    sid = register(control, metadata)["session_id"]
    output = control.workspace / "runs" / "synthetic-plot" / "data.json"
    output.parent.mkdir(parents=True)
    output.write_text(
        json.dumps(
            {"columns": ["gene_id", "s1", "s2", "p_value"], "rows": [["G", 1, 3, 0.9]]}
        )
    )
    sha = hashlib.sha256(output.read_bytes()).hexdigest()
    ref = {
        "artifact_id": "a-" + sha,
        "sha256": sha,
        "format": "json",
        "kind": "plot_sidecar",
        "representation_class": "derived_summary",
        "claim_tier": "L1_execution_verified",
        "size_bytes": output.stat().st_size,
    }
    with control.authority.daemon_transaction() as db:
        control._enroll_source(db, sid, ref, "synthetic")
        store_artifact(db, sid, ref, output, "synthetic", {}, producer="synthetic-plot")
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        query.query_artifact_table(
            control,
            {
                "session_id": sid,
                "artifact_id": ref["artifact_id"],
                "artifact_sha256": sha,
                "table_slot": "plot_sidecar",
            },
        )
