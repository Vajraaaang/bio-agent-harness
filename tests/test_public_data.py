import json

import h5py
import numpy as np
import pandas as pd
import pytest
from scipy import sparse


def test_quoted_whitespace_is_parsed_incrementally_with_provenance(tmp_path):
    from benchmarks.public_data import convert_text_subset
    from devharness_core.storage import ChunkedExpressionStore
    source = tmp_path / "Mouse.RPKM.txt"
    source.write_text('"gene" "Brain.e8.5.1" "Brain.e10.5.1" "Heart.e8.5.1"\n"g1" 1.25 2.5 3\n"g2" 4 5.25 6\n"g3" 7 8 9\n')
    result = convert_text_subset(source, tmp_path / "subset", sample_ids=["Brain.e10.5.1", "Brain.e8.5.1"], gene_ids=["g2", "g1"], source_accession="E-MTAB-6798", gene_chunk_size=1)
    with ChunkedExpressionStore(result["zarr"]) as store:
        assert store.sample_names == ["Brain.e10.5.1", "Brain.e8.5.1"]
        assert store.gene_names == ["g2", "g1"]
        np.testing.assert_allclose(store.get_chunk(slice(None), slice(None)), [[5.25, 4], [2.5, 1.25]])
    manifest = json.loads(open(result["manifest"]).read())
    assert len(manifest["source_sha256"]) == 64
    assert manifest["source_kind"] == "public_processed"
    assert manifest["chronology_kind"] == "collection_stage_reference"
    assert manifest["expression_kind"] == "continuous"


@pytest.mark.parametrize("encoding", ["csr", "csc", "dense"])
def test_h5ad_sparse_and_dense_subset_preserves_requested_axes(tmp_path, encoding):
    from benchmarks.public_data import convert_h5ad_subset
    from devharness_core.storage import ChunkedExpressionStore
    source = tmp_path / "atlas.h5ad"
    dense = np.asarray([[0, 2, 0, 4], [5, 0, 7, 0], [0, 10, 11, 12]])
    with h5py.File(source, "w") as handle:
        for axis, names in (("obs", ["c0", "c1", "c2"]), ("var", ["g0", "g1", "g2", "g3"])):
            group = handle.create_group(axis)
            group.attrs["_index"] = "_index"
            group.create_dataset("_index", data=names, dtype=h5py.string_dtype())
        if encoding == "dense":
            handle.create_dataset("X", data=dense)
        else:
            matrix = getattr(sparse, encoding + "_matrix")(dense)
            group = handle.create_group("X")
            group.attrs.update({"encoding-type": encoding + "_matrix", "shape": dense.shape})
            for key in ("data", "indices", "indptr"):
                group.create_dataset(key, data=getattr(matrix, key))
    metadata = pd.DataFrame({"sample_id": ["c2", "c0"], "embryonic_age": ["E7.5", "E6.5"], "batch": ["pool1", "pool2"]})
    result = convert_h5ad_subset(source, tmp_path / "out", metadata=metadata, sample_ids=["c2", "c0"], gene_ids=["g3", "g1"], gene_chunk_size=1)
    with ChunkedExpressionStore(result["zarr"]) as store:
        np.testing.assert_array_equal(store.get_chunk(slice(None), slice(None)), dense[[2, 0]][:, [3, 1]].T)


def test_10x_incremental_conversion_and_malformed_input(tmp_path):
    from benchmarks.public_data import convert_10x_subset
    from devharness_core.storage import ChunkedExpressionStore
    (tmp_path / "matrix.mtx").write_text('%%MatrixMarket matrix coordinate integer general\n% tiny fixture\n3 2 3\n1 1 2\n2 2 7\n3 1 4\n')
    (tmp_path / "features.tsv").write_text('id1\tg1\tGene Expression\nid2\tg2\tGene Expression\nid3\tg3\tGene Expression\n')
    (tmp_path / "barcodes.tsv").write_text('c1\nc2\n')
    metadata = pd.DataFrame({"sample_id": ["c1", "c2"], "embryonic_age": ["E6.5", "E7.5"], "batch": ["a", "a"]})
    result = convert_10x_subset(tmp_path / "matrix.mtx", tmp_path / "features.tsv", tmp_path / "barcodes.tsv", tmp_path / "out", metadata=metadata, gene_ids=["id2", "id1"])
    with ChunkedExpressionStore(result["zarr"]) as store:
        np.testing.assert_array_equal(store.get_chunk(slice(None), slice(None)), [[0, 7], [2, 0]])


def test_budgeted_download_rejects_raw_and_cleans_partial(tmp_path, monkeypatch):
    from benchmarks.public_data import download_verified
    with pytest.raises(ValueError, match="processed"):
        download_verified("https://example.org/reads.bam", tmp_path / "reads.bam")
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b"x" * 8
    monkeypatch.setattr("benchmarks.public_data.urlopen", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="budget"):
        download_verified("https://example.org/counts.tsv", tmp_path / "counts.tsv", max_bytes=10)
    assert not (tmp_path / "counts.tsv").exists()
    assert not (tmp_path / "counts.tsv.part").exists()


def test_public_tasks_derive_goldens_from_snapshot_and_keep_them_private(tmp_path, monkeypatch):
    from benchmarks.datasets import build_public_tasks, stage_public_task
    from benchmarks.public_data import convert_text_subset
    from jsonschema import validate
    source = tmp_path / "brain.tsv"
    source.write_text('gene\tBrain.e8.5.1\tBrain.e10.5.1\nG1\t4\t1\nG2\t3\t2\nG3\t2\t3\nG4\t1\t4\n')
    snapshot = tmp_path / "snapshot"
    result = convert_text_subset(source, snapshot)
    manifest_path = snapshot / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["gene_annotations"] = [{"id": f"G{i+1}", "symbol": symbol} for i, symbol in enumerate(["Sox2", "Pax6", "Neurog2", "Tbr1"])]
    manifest_path.write_text(json.dumps(manifest))
    paths = build_public_tasks(snapshot, tmp_path / "tasks")
    monkeypatch.setenv("DEVHARNESS_DATA_DIR", str(tmp_path))
    assert len(paths) == 3
    for path in paths:
        spec = json.loads(path.read_text())
        assert spec["dataset"]["snapshot_id"] == "snapshot"
        assert "snapshot_dir" not in spec["dataset"]
        assert str(tmp_path) not in path.read_text()
        work = tmp_path / spec["id"]
        staged = stage_public_task(work, spec["dataset"])
        validate(staged["ground_truth"]["reference_answer"], spec["answer_schema"])
        assert staged["metadata"]["source_kind"] == "public_processed"
        assert staged["ground_truth"]["peak_stages"]["Tbr1"] == "E10.5"
        assert "ground_truth" not in (work / "dataset_provenance.json").read_text()
        assert not (work / "reference_answer.json").exists()
    monkeypatch.setenv("DEVHARNESS_DATA_DIR", str(tmp_path / "different_cache"))
    overridden = {**spec["dataset"], "snapshot_dir": str(snapshot)}
    assert stage_public_task(tmp_path / "runtime_override", overridden)["metadata"]["source_kind"] == "public_processed"
    with pytest.raises(ValueError, match="manifest changed"):
        stage_public_task(tmp_path / "bad_digest", {**overridden, "source_manifest_sha256": "0" * 64})


@pytest.mark.parametrize("snapshot_id", ["../outside", "/absolute", "a/b", "..", ""])
def test_public_snapshot_identifier_rejects_traversal(tmp_path, snapshot_id):
    from benchmarks.datasets import stage_public_task
    with pytest.raises(ValueError, match="snapshot_id"):
        stage_public_task(tmp_path / "work", {"builder": "public_subset", "variant": "chronology_audit", "snapshot_id": snapshot_id, "source_manifest_sha256": "0" * 64})


def test_public_snapshot_missing_data_has_actionable_error(tmp_path, monkeypatch):
    from benchmarks.datasets import stage_public_task
    monkeypatch.setenv("DEVHARNESS_DATA_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="DEVHARNESS_DATA_DIR"):
        stage_public_task(tmp_path / "work", {"builder": "public_subset", "variant": "chronology_audit", "snapshot_id": "missing", "source_manifest_sha256": "0" * 64})
    monkeypatch.setenv("DEVHARNESS_DATA_DIR", "relative/cache")
    with pytest.raises(ValueError, match="must be an absolute"):
        stage_public_task(tmp_path / "work", {"builder": "public_subset", "variant": "chronology_audit", "snapshot_id": "missing", "source_manifest_sha256": "0" * 64})
