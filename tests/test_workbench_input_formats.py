"""Bounded discovery for original 10x HDF5 and delimited sources."""

import gzip
import hashlib

import h5py
import pytest

from devbench.analysis.workbench_inputs import InputDiscovery
from devharness_core import watchdog


@pytest.fixture
def local_worker(monkeypatch):
    def run(target, args, *, diagnostics, **kwargs):
        try:
            return target(*args)
        finally:
            diagnostics["cleanup"] = {"verified_stopped": True}

    monkeypatch.setattr(watchdog, "run_sandboxed", run)


@pytest.mark.parametrize(
    ("name", "delimiter"),
    [("counts.csv", ","), ("counts.csv.gz", ","), ("counts.tsv.gz", "\t")],
)
def test_wide_table_header_retains_every_column(tmp_path, name, delimiter):
    path = tmp_path.resolve() / name
    columns = ["gene_id", *(f"sample_{index}" for index in range(512))]
    content = (delimiter.join(columns) + "\n").encode() + b"\xffprivate row\n"
    original = gzip.compress(content) if name.endswith(".gz") else content
    path.write_bytes(original)
    discovery = InputDiscovery([path.parent])

    assert discovery.list()["inputs"][0]["source"] == str(path)
    profile = discovery.inspect(path)

    assert profile["sha256"] == hashlib.sha256(original).hexdigest()
    assert profile["size_bytes"] == len(original)
    assert profile["observed"]["columns"] == columns
    assert profile["observed"]["rows"] is None
    assert profile["observed"]["expression_values_read"] is False
    assert b"private row" not in str(profile).encode()


def test_compressed_table_description_uses_only_its_header(tmp_path):
    path = tmp_path.resolve() / "metadata.tsv.gz"
    path.write_bytes(gzip.compress(b"id\tcondition\n\xffprivate row\n"))
    discovery = InputDiscovery([path.parent])
    result = discovery.describe(
        path,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        topics=["columns"],
    )

    assert result["completion"] == "completed"
    assert result["observed"]["columns"] == ["id", "condition"]
    assert "private row" not in str(result)


def test_explicit_wide_table_column_bound_does_not_expand_hdf5_metadata(tmp_path):
    discovery = InputDiscovery([tmp_path.resolve()], max_columns=512)

    assert discovery.limits["max_table_columns"] == 512
    assert discovery.limits["max_columns"] == 256


@pytest.mark.parametrize(
    "limits",
    [{"header_bytes": 10}, {"max_columns": 1}],
)
def test_compressed_header_obeys_decompressed_and_column_bounds(
    tmp_path,
    local_worker,
    limits,
):
    path = tmp_path.resolve() / "counts.csv.gz"
    path.write_bytes(gzip.compress(b"gene_id,sample_1,sample_2\n"))
    discovery = InputDiscovery([path.parent], **limits)

    with pytest.raises(ValueError, match="header.*budget|header.*bounds"):
        discovery.inspect(path)


def test_10x_companion_files_remain_headerless(tmp_path, local_worker):
    path = tmp_path.resolve() / "barcodes.tsv.gz"
    path.write_bytes(gzip.compress(b"private-barcode\n"))
    discovery = InputDiscovery([path.parent])
    profile = discovery.inspect(path)
    result = discovery.describe(
        path,
        expected_sha256=profile["sha256"],
        topics=["columns"],
    )

    assert "columns" not in profile["observed"]
    assert any("headerless" in item for item in result["limitations"])
    assert "private-barcode" not in str(result)


def _cellranger_group(group, genes, cells, *, modern):
    group.create_dataset("shape", data=[genes, cells], dtype="uint64")
    group.create_dataset("barcodes", shape=(cells,), dtype="S20", chunks=True)
    if modern:
        features = group.create_group("features")
        for name in ("id", "name", "feature_type"):
            features.create_dataset(name, shape=(genes,), dtype="S64", chunks=True)
    else:
        group.create_dataset("genes", shape=(genes,), dtype="S20", chunks=True)
        group.create_dataset("gene_names", shape=(genes,), dtype="S20", chunks=True)
    group.create_dataset("data", shape=(150_000_000,), dtype="uint32", chunks=True)
    group.create_dataset("indices", shape=(150_000_000,), dtype="uint32", chunks=True)
    group.create_dataset("indptr", shape=(cells + 1,), dtype="uint32", chunks=True)


@pytest.mark.parametrize("modern", [True, False])
def test_cellranger_reads_only_small_shape_values(
    tmp_path,
    local_worker,
    monkeypatch,
    modern,
):
    path = tmp_path.resolve() / "counts.h5"
    group_name = "matrix" if modern else "GRCh38"
    with h5py.File(path, "w") as handle:
        _cellranger_group(
            handle.create_group(group_name), 20_000, 85_000, modern=modern
        )
    original_read = h5py.Dataset.__getitem__

    def shape_only(dataset, key):
        assert dataset.name == f"/{group_name}/shape"
        return original_read(dataset, key)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", shape_only)
    discovery = InputDiscovery([path.parent])
    profile = discovery.inspect(path)
    observed = profile["observed"]
    assert (observed["genes"], observed["cells"]) == (20_000, 85_000)
    assert profile["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert profile["declarations"] == {}
    assert observed["expression_values_read"] is False
    assert observed["annotation_values_read"] is False
    described = discovery.describe(
        path,
        expected_sha256=profile["sha256"],
        topics=["dimensions"],
    )
    assert described["completion"] == "completed"
    assert {fact["name"]: fact["value"] for fact in described["facts"]} == {
        "observations": 85_000,
        "features": 20_000,
        "metadata_rows": None,
    }


def test_legacy_cellranger_preserves_separate_genome_dimensions(tmp_path, local_worker):
    path = tmp_path.resolve() / "counts.h5"
    with h5py.File(path, "w") as handle:
        _cellranger_group(handle.create_group("hg19"), 100, 10, modern=False)
        _cellranger_group(handle.create_group("mm10"), 200, 11, modern=False)

    observed = InputDiscovery([path.parent]).inspect(path)["observed"]

    assert observed["genes"] is observed["cells"] is None
    assert observed["genome_headers"] == {
        "hg19": {"genes": 100, "cells": 10},
        "mm10": {"genes": 200, "cells": 11},
    }


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        ("barcode_length", "dimensions disagree"),
        ("shape_link", "internal hard links"),
        ("virtual_shape", "external or virtual storage"),
    ],
)
def test_invalid_cellranger_headers_fail_closed(
    tmp_path, local_worker, invalid, message
):
    path = tmp_path.resolve() / "counts.h5"
    with h5py.File(path, "w", libver="latest") as handle:
        matrix = handle.create_group("matrix")
        _cellranger_group(matrix, 200, 11, modern=True)
        if invalid == "barcode_length":
            del matrix["barcodes"]
            matrix.create_dataset("barcodes", shape=(10,), dtype="S20")
        else:
            del matrix["shape"]
            if invalid == "shape_link":
                matrix["shape"] = h5py.ExternalLink("other.h5", "/shape")
            else:
                layout = h5py.VirtualLayout(shape=(2,), dtype="uint64")
                layout[:] = h5py.VirtualSource("other.h5", "/shape", shape=(2,))
                matrix.create_virtual_dataset("shape", layout)

    with pytest.raises(ValueError, match=message):
        InputDiscovery([path.parent]).inspect(path)
