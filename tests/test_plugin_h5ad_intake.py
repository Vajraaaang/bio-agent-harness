"""Out-of-core count verification and legacy AnnData intake over real MCP STDIO."""

import asyncio
import hashlib
import json
import os
import sys
from contextlib import asynccontextmanager

import h5py
import numpy as np
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from scipy import sparse

from devbench.platform.plugin_mcp import _verify_h5_counts
from devharness_controlled.native_contracts import single_cell_kernel_images

OLD_FILE_LIMIT = 100 * 1024**2
MAX_READ = 64 * 1024**2


def _write_kang_twin(path):
    """Reproduce Kang's original on-disk encodings with synthetic counts and labels."""
    cells, genes = 24_673, 15_706
    text = h5py.string_dtype("utf-8")
    obs = np.empty(cells, dtype=[("index", text), ("replicate", "i1")])
    obs["index"] = [f"cell-{i}" for i in range(cells)]
    obs["replicate"] = np.arange(cells) % 8
    var = np.empty(genes, dtype=[("index", text), ("name", text)])
    var["index"] = [f"gene-{i}" for i in range(genes)]
    var["name"] = var["index"]
    with h5py.File(path, "w") as handle:
        x = handle.create_group("X")
        x.attrs.update(h5sparse_format="csc", h5sparse_shape=[cells, genes])
        row = np.arange(genes, dtype="i4") % (cells // 2)
        indices = np.column_stack((row, row + cells // 2)).ravel()
        counts = np.ones(indices.size, dtype="f4")
        counts[::7] = 2
        counts[::61] = 3
        counts[::997] = 4
        x.create_dataset("indices", data=indices, compression="gzip")
        x.create_dataset("data", data=counts, compression="gzip")
        x.create_dataset("indptr", data=2 * np.arange(genes + 1, dtype="i4"))
        handle.create_dataset("obs", data=obs, compression="gzip")
        handle.create_dataset("var", data=var, compression="gzip")
        handle.create_dataset(
            "obsm",
            (cells,),
            dtype=[("X_pca", "f8", (30,)), ("X_umap", "f8", (2,))],
            compression="gzip",
        )
        handle.create_dataset(
            "uns/replicate_categories",
            data=[f"CATEGORY_VALUE_SENTINEL_{i}" for i in range(8)],
        )


@asynccontextmanager
async def _session(workspace, inputs, *, errlog=None):
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "devbench.platform.cli",
            "plugin-mcp",
            "--no-auto-studio",
            "--workspace",
            str(workspace),
            "--input-root",
            str(inputs),
            "--mounted-image-id",
            sorted(single_cell_kernel_images())[0],
        ],
        env={
            name: os.environ[name]
            for name in (
                "PATH",
                "PYTHONPATH",
                "DYLD_LIBRARY_PATH",
                "LD_LIBRARY_PATH",
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "MKL_NUM_THREADS",
            )
            if name in os.environ
        },
    )
    async with stdio_client(params, errlog=errlog or sys.stderr) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            yield client


def _qc_request(path):
    return {
        "inputs": [{"path": str(path), "data_classification": "public"}],
        "request": {
            "family_id": 2,
            "question": "single_cell_qc",
            "assay": "single_nucleus_rna_seq",
            "entry_point": "annotated_object",
        },
        "counts_layer": "X",
        "measurement": "umi_counts",
        "count_provenance": "Synthetic UMI count fixture",
        "batch_column": "replicate",
        "capture_units_confirmed": True,
    }


@pytest.mark.wal
def test_large_public_kang_twin_inspects_answers_and_stages_over_stdio(
    workspace_tmp_path,
):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    path = inputs / "kang.h5ad"
    _write_kang_twin(path)
    # An HDF5 container can have bytes beyond its last object. Cross the old source
    # limit without allocating a matrix or retaining a 100 MiB binary in the repo.
    with path.open("r+b") as stream:
        stream.truncate(OLD_FILE_LIMIT + 1)
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    request = _qc_request(path)
    request["request"]["assay"] = "single_cell_rna_seq"
    declarations = {
        key: request[key]
        for key in ("inputs", "counts_layer", "measurement", "count_provenance")
    }

    async def roundtrip():
        async with _session(workspace_tmp_path / "workspace", inputs) as client:
            inspected = await client.call_tool(
                "inspect_rna_inputs", {"inputs": request["inputs"]}
            )
            assert not inspected.isError, inspected.structuredContent
            (profile,) = inspected.structuredContent["inputs"]
            observed = profile["observed"]
            assert (observed["cells"], observed["genes"]) == (24_673, 15_706)
            assert observed["obs_columns"] == ["replicate"]
            assert observed["obsm_keys"] == ["X_pca", "X_umap"]
            assert profile["sha256"] == sha
            for axis in ("observation", "feature"):
                assert len(profile[f"{axis}_axis_sha256"]) == 64
            checked = await client.call_tool("check_answerability", request)
            assert not checked.isError, checked.structuredContent
            answer = checked.structuredContent
            assert answer["status"] == "ready", answer
            assert answer["attested_measurements"] == [
                {"kind": "gene_counts", "slot": "X"},
                {"kind": "batch_membership", "slot": "obs/replicate"},
            ]
            planned = await client.call_tool(
                "plan_rna_workflow",
                {
                    **declarations,
                    "operation": "single_cell",
                    "objective": "Inspect count QC by capture in a public Kang twin",
                    "answerability": request,
                    "parameters": {
                        "capture_units_confirmed": True,
                        "steps": [
                            {
                                "operation": "scrna_batch_qc",
                                "parameters": {"batch_key": "replicate"},
                                "sources": {
                                    "expression": {"id": "input", "port": None}
                                },
                            }
                        ],
                    },
                },
            )
            assert not planned.isError, planned.structuredContent
            frozen = planned.structuredContent
            assert frozen["status"] == "planned"
            assert frozen["inputs"][0]["sha256"] == sha
            assert frozen["answerability"] == answer
            assert len(frozen["plan_sha256"]) == 64
            for result in (inspected, checked, planned):
                assert "CATEGORY_VALUE_SENTINEL" not in result.model_dump_json()

    asyncio.run(roundtrip())


def _record_matrix_reads(monkeypatch, *datasets):
    """Reject oversize selections before h5py allocates their result buffers."""
    original = h5py.Dataset.__getitem__
    reads = {name: [] for name in datasets}

    def tracked(dataset, key, *args, **kwargs):
        if dataset.name in reads:
            extent = np.broadcast_to(np.uint8(0), dataset.shape)
            byte_count = extent[key].size * dataset.dtype.itemsize
            assert byte_count <= MAX_READ
            reads[dataset.name].append(byte_count)
        return original(dataset, key, *args, **kwargs)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", tracked)
    return reads


def test_oversize_dense_row_streams_through_last_value(workspace_tmp_path, monkeypatch):
    shape = (1, OLD_FILE_LIMIT // 4 + 1)
    with h5py.File(workspace_tmp_path / "wide.h5ad", "w") as handle:
        values = handle.create_dataset(
            "X",
            shape,
            dtype="f4",
            chunks=(1, 32_768),
            fillvalue=1,
        )
        reads = _record_matrix_reads(monkeypatch, "/X")
        assert values.nbytes > OLD_FILE_LIMIT
        assert _verify_h5_counts(values, shape, None)
        assert sum(reads["/X"]) >= values.nbytes
        values[0, -1] = 0.5
        assert not _verify_h5_counts(values, shape, None)


@pytest.mark.parametrize("layout", ["csr", "csc"])
def test_large_sparse_count_storage_streams_data_and_indexes(
    workspace_tmp_path,
    monkeypatch,
    layout,
):
    # Every segment has 128 distinct, sorted coordinates on a 16,384-member axis.
    width = 128
    major = OLD_FILE_LIMIT // (width * 8) + 1
    shape = (major, 16_384) if layout == "csr" else (16_384, major)
    nnz = major * width
    with h5py.File(workspace_tmp_path / "sparse.h5ad", "w") as handle:
        group = handle.create_group("X")
        indices = group.create_dataset(
            "indices",
            (nnz,),
            dtype="i4",
            chunks=(65_536,),
            compression="lzf",
        )
        coordinates = np.tile(np.arange(width, dtype="i4"), 512)
        for start in range(0, nnz, len(coordinates)):
            stop = min(nnz, start + len(coordinates))
            indices[start:stop] = coordinates[: stop - start]
        values = group.create_dataset(
            "data",
            (nnz,),
            dtype="f4",
            chunks=(65_536,),
            fillvalue=1,
        )
        pointers = group.create_dataset("indptr", data=width * np.arange(major + 1))
        assert indices.nbytes + values.nbytes + pointers.nbytes > OLD_FILE_LIMIT
        reads = _record_matrix_reads(monkeypatch, "/X/data", "/X/indices", "/X/indptr")
        assert _verify_h5_counts(group, shape, layout)
        for node in (values, indices, pointers):
            assert sum(reads[node.name]) >= node.nbytes
        values[-1] = 2.5
        assert not _verify_h5_counts(group, shape, layout)


@pytest.mark.parametrize("layout", ["csr", "csc"])
def test_implicit_zeros_distinguish_low_umi_counts_from_dense_plateau(
    workspace_tmp_path,
    layout,
):
    counts = np.array([[1, 2, 3, 4], [4, 3, 2, 1]])
    rows, cols = np.nonzero(counts)
    with h5py.File(workspace_tmp_path / "comparison.h5ad", "w") as handle:
        # Both slots store precisely the same eight positive integer values.
        for name, shape, expected in (
            ("plateau", (2, 4), False),
            ("snrna", (400, 128), True),
        ):
            matrix = sparse.coo_matrix((counts[rows, cols], (rows, cols)), shape=shape)
            matrix = matrix.asformat(layout)
            group = handle.create_group(name)
            for part in ("data", "indices", "indptr"):
                group.create_dataset(part, data=getattr(matrix, part))
            assert _verify_h5_counts(group, shape, layout) is expected


def _write_four_feature_counts(path, counts):
    with h5py.File(path, "w") as handle:
        handle.create_dataset("X", data=counts)
        handle.create_dataset("obs/_index", data=[b"a", b"b"])
        handle.create_dataset("obs/replicate", data=[b"rep-1", b"rep-2"])
        handle.create_dataset("var/_index", data=[b"w", b"x", b"y", b"z"])


@pytest.mark.parametrize("layout", ["csr", "csc"])
@pytest.mark.parametrize("library_rows", [True, False])
def test_library_sums_use_logical_sparse_coordinates(
    workspace_tmp_path, monkeypatch, layout, library_rows
):
    from devbench.platform import plugin_mcp

    rng = np.random.default_rng(145)
    raw = rng.negative_binomial(2, 0.2, (8, 600))
    raw[rng.random(raw.shape) < 0.93] = 0
    cpm = np.rint(1_000_000 * raw / raw.sum(axis=1, keepdims=True)).astype("int32")
    monkeypatch.setattr(plugin_mcp, "_H5_BLOCK", 256)
    with h5py.File(workspace_tmp_path / "sparse.h5", "w") as h5:
        for name, array, valid in (("counts", raw, True), ("rounded_cpm", cpm, False)):
            stored = sparse.coo_matrix(array if library_rows else array.T).asformat(
                layout
            )
            group = h5.create_group(name)
            for key in ("data", "indices", "indptr"):
                group.create_dataset(key, data=getattr(stored, key))
            flags = set()
            assert (
                _verify_h5_counts(
                    group,
                    stored.shape,
                    layout,
                    count1_flags=flags,
                    libraries_in_rows=library_rows,
                )
                is valid
            )
            assert flags == (
                set() if valid else {"suspected_transformed_integer_counts"}
            )


@pytest.mark.wal
def test_count1_log_plateau_and_safe_h5ad_diagnostics_over_stdio(workspace_tmp_path):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    logged_path = inputs / "logged.h5ad"
    raw = np.array([[1, 3, 7, 15], [15, 7, 3, 1]])
    _write_four_feature_counts(logged_path, np.log2(1 + raw))
    with h5py.File(logged_path, "r") as handle:
        assert not _verify_h5_counts(handle["X"], raw.shape, None)
        assert _verify_h5_counts(handle["X"], raw.shape, None, integer=False)
    malformed = inputs / "BAD_PATH_SENTINEL.h5ad"
    with h5py.File(malformed, "w") as handle:
        handle.create_dataset("X", data=[[1]])
        handle.create_group("obs").attrs["_index"] = "BAD_FIELD_SENTINEL"

    async def check(errlog):
        async with _session(
            workspace_tmp_path / "workspace", inputs, errlog=errlog
        ) as client:
            held = await client.call_tool(
                "check_answerability", _qc_request(logged_path)
            )
            assert (
                held.structuredContent["reason"]
                == "suspected_transformed_integer_counts"
            )
            unchecked = await client.call_tool(
                "plan_rna_workflow",
                {
                    "operation": "single_cell",
                    "objective": "Check the declared count input",
                    "inputs": _qc_request(logged_path)["inputs"],
                    "measurement": "umi_counts",
                    "counts_layer": "X",
                    "parameters": {
                        "capture_units_confirmed": True,
                        "steps": [
                            {
                                "operation": "scrna_batch_qc",
                                "parameters": {"batch_key": "replicate"},
                                "sources": {
                                    "expression": {"id": "input", "port": None}
                                },
                            }
                        ],
                    },
                },
            )
            assert unchecked.isError
            assert (
                unchecked.structuredContent["reason"]
                == "suspected_transformed_integer_counts"
            )
            for classification, reason in (
                ("public", "h5ad_header_inspection_failed"),
                ("private", "private_data_local_studio_only"),
            ):
                invalid = await client.call_tool(
                    "inspect_rna_inputs",
                    {
                        "inputs": [
                            {
                                "path": str(malformed),
                                "data_classification": classification,
                            }
                        ]
                    },
                )
                assert invalid.isError
                assert invalid.structuredContent["reason"] == reason
                assert "SENTINEL" not in json.dumps(invalid.model_dump(mode="json"))

    log = workspace_tmp_path / "mcp-stderr.log"
    with log.open("w") as errlog:
        asyncio.run(check(errlog))
    assert "SENTINEL" not in log.read_text()


@pytest.mark.wal
def test_count1_review_binds_the_exact_source_and_slot(workspace_tmp_path):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    path = inputs / "confirmed-raw.h5ad"
    # These genuine counts have the same distribution as the transformed fixture.
    _write_four_feature_counts(path, [[1, 2, 3, 4], [4, 3, 2, 1]])
    with path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    review = {
        "source_sha256": sha,
        "slot": "X",
        "evidence": "Test operator reviewed the eight original UMI integers",
    }

    async def check():
        async with _session(workspace_tmp_path / "workspace", inputs) as client:
            for override in ({"source_sha256": "0" * 64}, {"slot": "layers/other"}):
                held = await client.call_tool(
                    "check_answerability",
                    {
                        **_qc_request(path),
                        "count1_confirmation": {**review, **override},
                    },
                )
                assert (
                    held.structuredContent["reason"]
                    == "suspected_transformed_integer_counts"
                )
            confirmed = await client.call_tool(
                "check_answerability",
                {**_qc_request(path), "count1_confirmation": review},
            )
            assert not confirmed.isError, confirmed.structuredContent
            assert confirmed.structuredContent["status"] == "ready"
            assert len(confirmed.structuredContent["count1_confirmation_sha256"]) == 64

    asyncio.run(check())


def test_processing_history_applies_only_to_the_selected_count_slot(workspace_tmp_path):
    from devbench.platform.plugin_mcp import _verify_h5_counts

    with h5py.File(workspace_tmp_path / "layers.h5ad", "w") as handle:
        x = handle.create_dataset("X", data=[[1, 5], [7, 10]])
        x.attrs["transformation"] = "log2"
        counts = handle.create_group("layers").create_dataset("counts", data=x[:])
        flags = set()
        assert not _verify_h5_counts(x, x.shape, None, count1_flags=flags)
        assert flags == {"conflicting_count_provenance"}
        assert _verify_h5_counts(x, x.shape, None, integer=False)
        assert _verify_h5_counts(counts, counts.shape, None)
        x.attrs["transformation"] = "x" * 500_000
        with pytest.raises(ValueError, match="processing label exceeds"):
            _verify_h5_counts(x, x.shape, None)
