"""Independent coverage for compressed-axis traversal, Zarr v3, and solver custody."""

import hashlib
import json
from pathlib import Path

import anndata as ad
import numpy as np
import pytest
from numcodecs import VLenUTF8, Zstd
from scipy import sparse

from devbench.core._rna_solver_inputs import numeric_matrix, read_solver_csv
from devbench.core.expression_io import open_expression
from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    assess_answerability,
)


def _v3_group(root, attributes):
    root.mkdir(parents=True)
    (root / "zarr.json").write_text(
        json.dumps(
            {
                "zarr_format": 3,
                "node_type": "group",
                "attributes": attributes,
            }
        )
    )


def _v3_array(root, values, *, string=False):
    root.mkdir()
    values = np.asarray(values, dtype=object if string else "<i4")
    chunk = min(3, len(values))
    codecs = (
        [{"name": "vlen-utf8"}]
        if string
        else [{"name": "bytes", "configuration": {"endian": "little"}}]
    )
    codecs.append({"name": "zstd", "configuration": {"level": 0, "checksum": False}})
    attrs = (
        {"encoding-type": "string-array", "encoding-version": "0.2.0"}
        if string
        else {"encoding-type": "array", "encoding-version": "0.2.0"}
    )
    (root / "zarr.json").write_text(
        json.dumps(
            {
                "zarr_format": 3,
                "node_type": "array",
                "shape": [len(values)],
                "attributes": attrs,
                "data_type": "string" if string else "int32",
                "chunk_grid": {
                    "name": "regular",
                    "configuration": {"chunk_shape": [chunk]},
                },
                "chunk_key_encoding": {
                    "name": "default",
                    "configuration": {"separator": "/"},
                },
                "fill_value": "" if string else 0,
                "codecs": codecs,
            }
        )
    )
    (root / "c").mkdir()
    for start in range(0, len(values), chunk):
        piece = np.full(chunk, "" if string else 0, dtype=values.dtype)
        part = values[start : start + chunk]
        piece[: len(part)] = part
        raw = VLenUTF8().encode(piece) if string else piece.tobytes()
        (root / "c" / str(start // chunk)).write_bytes(Zstd(level=0).encode(raw))


def _v3(root, x, layout):
    _v3_group(root, {"encoding-type": "anndata", "encoding-version": "0.1.0"})
    for axis, count in zip(("obs", "var"), x.shape, strict=True):
        _v3_group(
            root / axis,
            {
                "encoding-type": "dataframe",
                "encoding-version": "0.2.0",
                "_index": "_index",
                "column-order": [],
            },
        )
        _v3_array(
            root / axis / "_index", [f"{axis}{i}" for i in range(count)], string=True
        )
    _v3_group(
        root / "X",
        {
            "encoding-type": layout + "_matrix",
            "encoding-version": "0.1.0",
            "shape": list(x.shape),
        },
    )
    for key in ("data", "indices", "indptr"):
        _v3_array(root / "X" / key, getattr(x, key))


@pytest.mark.parametrize("encoding", ["h5ad", "zarr2", "zarr3"])
@pytest.mark.parametrize("layout", ["csr", "csc"])
def test_sparse_row_stream_with_selection_and_resume(tmp_path, encoding, layout):
    dense = np.arange(56, dtype=np.int32).reshape(8, 7)
    dense[dense % 4 != 0] = 0
    x = getattr(sparse, layout + "_matrix")(dense)
    path = Path(tmp_path).resolve() / (
        "data.h5ad" if encoding == "h5ad" else "data.zarr"
    )
    if encoding == "zarr3":
        _v3(path, x, layout)
    elif encoding == "h5ad":
        ad.AnnData(x).write_h5ad(path)
    else:
        ad.AnnData(x).write_zarr(path)
    with open_expression(path) as source:
        source.validate_sparse_storage(64 * 1024)
        selected = list(source.iter_rows(block_rows=2, start=1, features=[5, 0]))
        np.testing.assert_array_equal(
            sparse.vstack([b.X for b in selected]).toarray(), dense[1:, [5, 0]]
        )
    assert source.closed


def test_large_string_csv_through_disk_backed_numeric_matrix(tmp_path):
    path = tmp_path.resolve() / "counts.csv"
    rows, cols = 4097, 257
    header = ",".join(f"g{i}" for i in range(cols)) + "\n"
    row = ",".join(["000000001"] * cols) + "\n"
    with path.open("w") as stream:
        stream.write(header)
        for _ in range(rows):
            stream.write(row)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    frame = read_solver_csv(path, digest)
    assert frame.iloc[0, 0] == "000000001"
    numeric = numeric_matrix(frame)
    assert isinstance(numeric, np.memmap)
    assert numeric.shape == (rows, cols) and numeric.sum() == rows * cols


def test_open_lane_count_model_and_replication_stay_independent():
    sha = "a" * 64
    request = AnswerabilityRequest(
        question="custom_estimand",
        assay="open",
        entry_point="custom",
        requires_count_model=True,
    )
    measured = AttestedMeasurement(
        kind="gene_counts",
        artifact_sha256=sha,
        receipt_sha256=sha,
        representation_class="size_factor_normalized",
    )
    assert (
        assess_answerability(request, measurements=(measured,)).reason
        == "count1_ancestry_required"
    )
    counts = measured.model_copy(
        update={
            "representation_class": "count_1_raw_counts",
            "count1_ancestry_sha256": sha,
        }
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=sha,
        observation_axis_sha256=sha,
        estimability="confounded",
        independent_units_per_group=(3, 3),
    )
    assert (
        assess_answerability(request, measurements=(counts,), design=design).reason
        == "design_confounded"
    )
    assert assess_answerability(request, measurements=(counts,)).claim_tier == "none"


def test_large_parquet_preserves_all_gene_values_and_legacy_digest(tmp_path):
    from types import SimpleNamespace

    import pyarrow.parquet as pq

    from devbench.engine.provenance import canonical_json
    from devbench.engine.tier_a_executor import _local_result_digest, _result_table

    rng = np.random.default_rng(208129)
    values = rng.normal(size=(2300, 1000))
    genes = [f"g{i}" for i in range(len(values))]
    rows = [
        {
            "hypothesis_id": gene,
            "gene_id": gene,
            "estimate_status": "estimated",
            "values": row.tolist(),
        }
        for gene, row in zip(genes, values, strict=True)
    ]
    payload = {
        "kind": "effects",
        "analysis_performed": True,
        "gene_statistics": rows,
        "multiplicity_scope": [],
        "selection_conditional_inference": False,
        "biological_unit": "donor",
        "analysis_layer": "continuous",
        "effect_definition": "frozen continuous donor contrast",
        "reference_condition": "control",
        "condition_number_validated": True,
        "design_rank": 2,
        "design_column_count": 2,
        "computed_contrast": {
            "reference_condition": "control",
            "comparison_condition": "case",
            "effect_scale": "unchanged_input_expression",
            "normalization": "unchanged_input",
        },
    }
    result = {
        "target_results": [
            {
                "target_id": "mean_contrast",
                "estimate_status": "estimated",
                "payload": payload,
            }
        ]
    }
    spec = SimpleNamespace(
        family=SimpleNamespace(feature_ids=genes, family_id="family"),
        family_sha256="f" * 64,
        biological_unit="donor",
        measurement="continuous",
        contrast=SimpleNamespace(denominator="control", numerator="case"),
    )
    file, failure = _result_table(
        spec, result, "e" * 64, destination=tmp_path / "genes.parquet"
    )
    assert failure is None and file.stat().st_size > 16_000_000
    reader = pq.ParquetFile(file)
    assert reader.num_row_groups > 1
    start = 0
    for batch in reader.iter_batches(batch_size=64, columns=["gene_id", "values"]):
        stop = start + len(batch)
        assert batch[0].to_pylist() == genes[start:stop]
        np.testing.assert_array_equal(
            np.asarray(batch[1].to_pylist()), values[start:stop]
        )
        start = stop
    assert start == len(values)
    assert (
        _local_result_digest(rows[0])
        == hashlib.sha256(canonical_json(rows[0])).hexdigest()
    )


@pytest.mark.parametrize(
    "dependency",
    [
        "external_link",
        "external_storage",
        "virtual",
        "soft",
        "cycle",
        "object_ref",
        "unknown_filter",
        "indirect_index",
    ],
)
def test_hdf5_unbound_components_rejected_before_anndata_decode(
    tmp_path, monkeypatch, dependency
):
    import h5py

    from devbench.core.hdf5_custody import validate_h5ad_custody

    source = tmp_path / "derivative.h5ad"
    ad.AnnData(sparse.csc_matrix(np.eye(3))).write_h5ad(source)
    # Uncreated local fixture names exercise metadata refusal without opening
    # any separate HDF5 file or raw payload.
    with h5py.File(source, "r+") as handle:
        group = handle["obs"].create_group("nested")
        if dependency == "external_link":
            group["bad"] = h5py.ExternalLink(str(tmp_path / "unused.h5"), "/")
        elif dependency == "external_storage":
            group.create_dataset(
                "bad",
                shape=(2,),
                dtype="f8",
                external=[(str(tmp_path / "unused.raw"), 0, 16)],
            )
        elif dependency == "virtual":
            layout = h5py.VirtualLayout((2,), "f8")
            layout[:] = h5py.VirtualSource(
                str(tmp_path / "unused.h5"), "data", shape=(2,)
            )
            group.create_virtual_dataset("bad", layout)
        elif dependency == "soft":
            del handle["X"]["data"]
            handle["X"]["data"] = h5py.SoftLink("/missing")
        elif dependency == "cycle":
            group["loop"] = group
        elif dependency == "object_ref":
            group.attrs["reference"] = handle["var"].ref
        elif dependency == "indirect_index":
            handle["obs"].attrs["_index"] = "/var/_index"
        else:
            group.create_dataset(
                "bad",
                shape=(2,),
                dtype="i4",
                chunks=(2,),
                compression=32112,
                allow_unknown_filter=True,
            )

    def unexpected_read(*args, **kwargs):
        pytest.fail("AnnData attempted to deserialize before custody validation")

    monkeypatch.setattr(ad.io, "read_elem", unexpected_read)
    with pytest.raises(ValueError, match="HDF5"):
        validate_h5ad_custody(source)
    with pytest.raises(ValueError, match="HDF5"):
        with open_expression(source):
            pass


def test_hdf5_eager_annotation_budget_precedes_materialization(tmp_path, monkeypatch):
    import h5py

    from devbench.core.hdf5_custody import validate_h5ad_custody

    source = tmp_path / "review.h5ad"
    ad.AnnData(sparse.csr_matrix(np.eye(3))).write_h5ad(source)
    validate_h5ad_custody(
        source, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_h5ad_custody(source, expected_sha256="f" * 64)
    with h5py.File(source, "r+") as handle:
        handle["obs"].create_dataset(
            "excessive", shape=(1 << 29,), chunks=(512,), dtype="i4"
        )
    monkeypatch.setattr(ad.io, "read_elem", lambda *args: pytest.fail("unbounded read"))
    with pytest.raises(ValueError, match="materialization budget"):
        with open_expression(source):
            pass


def test_staging_uses_the_frozen_source_and_exposes_verified_member_digests(
    tmp_path, monkeypatch
):
    from devbench.engine import mounted_matrix_vault as custody

    source = tmp_path / "input.zarr"
    source.mkdir()
    chunk = source / "0"
    chunk.write_bytes(b"declared synthetic chunk")
    chunk.chmod(0o444)
    source.chmod(0o555)
    vault = custody.MountedZarrVault(tmp_path / "vault")
    monkeypatch.setattr(custody, "ZERO_COPY_THRESHOLD", 0)
    with pytest.raises(ValueError, match="expected"):
        vault.put(source, expected_sha256="0" * 64)
    assert not list(vault.root.iterdir())
    try:
        identity = vault.put(source)
        record = vault.attestation(identity)
        assert record["storage_mode"] == "zero_copy"
        vault.put(source, expected_sha256=identity, immutable_copy=True)
        assert vault.attestation(identity)["storage_mode"] == "immutable_copy"
        stage = tmp_path / "worker.zarr"
        actual = vault.stage(identity, stage, record=record)
        assert actual == record and (stage / "0").stat().st_ino == chunk.stat().st_ino
        assert vault.member_digests(identity, record) == (
            identity,
            hashlib.sha256(chunk.read_bytes()).hexdigest(),
        )
    finally:
        source.chmod(0o755)
