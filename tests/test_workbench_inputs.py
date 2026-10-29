"""Read-only discovery and supervised metadata profiles; no scientific fitting."""
import hashlib
import json
import os
from pathlib import Path

import pytest


def api():
    from devbench.analysis import workbench_inputs
    return workbench_inputs


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path.resolve() / "inputs"
    root.mkdir()
    return root


@pytest.fixture
def supervised(monkeypatch):
    from devharness_core import watchdog
    calls = []
    def run(target, args=(), **kwargs):
        calls.append((target, args, kwargs))
        value = target(*args)
        kwargs["diagnostics"].update(cleanup={"verified_stopped": True})
        return value
    monkeypatch.setattr(watchdog, "run_sandboxed", run)
    return calls


def h5ad(path):
    import h5py
    import numpy as np
    with h5py.File(path, "w") as handle:
        handle.attrs["encoding-type"] = "anndata"
        # Unallocated expression dataset: huge logical matrix, small file.
        handle.create_dataset("X", shape=(50000, 20000), dtype="float32", chunks=(1, 20000))
        for name, size, columns in [("obs", 50000, ["batch", "donor", "sample"]), ("var", 20000, ["gene_symbols"])]:
            group = handle.create_group(name)
            group.attrs["_index"] = "_index"
            group.attrs["column-order"] = np.array(columns, dtype=h5py.string_dtype())
            group.create_dataset("_index", shape=(size,), dtype="S16")
            for column in columns:
                group.create_dataset(column, shape=(size,), dtype="S16")
        handle.create_group("layers").create_dataset("counts", shape=(50000, 20000), dtype="int32", chunks=(1, 20000))
        handle.create_group("obsm").create_dataset("X_umap", shape=(50000, 2), dtype="float32", chunks=True)


def test_discovery_finds_only_bounded_regular_supported_inputs(inputs):
    (inputs / "a.json").write_text('{"samples":[]}')
    (inputs / "ignore.fastq").write_text("not a supported input")
    (inputs / "nested").mkdir()
    (inputs / "nested/b.csv").write_text("sample,batch\na,A\n")
    result = api().InputDiscovery([inputs]).list()
    rows = result["inputs"]
    assert {row["filename"] for row in rows} == {"a.json", "b.csv"}
    assert all(Path(row["path"]).is_absolute() and row["source"] == row["path"] for row in rows)
    assert all(row["hash_status"] == "ready" and len(row["sha256"]) == 64 for row in rows)


def test_symlink_roots_and_escape_paths_are_rejected(inputs, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (inputs / "linked.json").symlink_to(outside)
    (inputs / "linked-directory").symlink_to(tmp_path, target_is_directory=True)
    discovery = api().InputDiscovery([inputs])
    assert discovery.list()["inputs"] == []
    for source in [outside, inputs / "linked.json", inputs / "linked-directory/outside.json", str(inputs) + "/../outside.json", "relative.json"]:
        with pytest.raises((ValueError, PermissionError)):
            discovery.inspect(source)
    with pytest.raises((ValueError, PermissionError)):
        api().InputDiscovery([inputs / "linked-directory"])


def test_fifo_is_not_opened_or_listed(inputs):
    pipe = inputs / "pipe.csv"
    os.mkfifo(pipe)
    discovery = api().InputDiscovery([inputs])
    assert discovery.list()["inputs"] == []
    with pytest.raises((ValueError, PermissionError)):
        discovery.inspect(pipe)


def test_depth_file_and_hash_budgets_are_explicit(inputs):
    (inputs / "deep").mkdir()
    (inputs / "deep/hidden.json").write_text("{}")
    (inputs / "a.json").write_text('{"longer":true}')
    (inputs / "b.json").write_text("{}")
    discovery = api().InputDiscovery([inputs], max_depth=0, max_files=1, list_hash_bytes=1)
    result = discovery.list()
    assert len(result["inputs"]) == 1 and result["truncated"]
    assert result["inputs"][0]["sha256"] is None
    assert result["inputs"][0]["hash_status"] == "pending_budget"


def test_hash_cache_invalidates_after_content_change(inputs, supervised):
    source = inputs / "data.csv"
    source.write_text("sample,batch\na,A\n")
    discovery = api().InputDiscovery([inputs])
    first = discovery.inspect(source)
    assert discovery.inspect(source) == first and len(supervised) == 1
    source.write_text("sample,donor\nb,B\n")
    second = discovery.inspect(source)
    assert second["sha256"] != first["sha256"] and len(supervised) == 2
    assert second["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert second["observed"]["columns"] == ["sample", "donor"]


def test_h5ad_profile_reads_headers_without_expression_values(inputs, supervised, monkeypatch):
    import h5py
    source = inputs / "cells.h5ad"
    h5ad(source)
    original = h5py.Dataset.__getitem__
    def forbid_expression(self, key):
        if self.name == "/X" or self.name.startswith(("/layers/", "/obsm/", "/obs/", "/var/")):
            raise AssertionError("Metadata profile must not read matrix or annotation values")
        return original(self, key)
    monkeypatch.setattr(h5py.Dataset, "__getitem__", forbid_expression)
    profile = api().InputDiscovery([inputs]).inspect(source)
    assert profile["observed"]["cells"] == 50000 and profile["observed"]["genes"] == 20000
    assert profile["observed"]["obs_columns"] == ["batch", "donor", "sample"]
    assert profile["observed"]["var_columns"] == ["gene_symbols"]
    assert profile["observed"]["layers"] == ["counts"] and profile["observed"]["obsm_keys"] == ["X_umap"]
    assert profile["observed"]["expression_values_read"] is False
    assert profile["declarations"] == {}
    assert profile["inferred"]["candidate_columns"]["donor"] == ["donor"]
    assert profile["inferred"]["candidate_columns"]["capture_unit"] == ["batch", "sample"]
    assert {item["field"] for item in profile["unknowns"]} >= {"assay", "measurement", "capture_unit_identity"}
    assert supervised[0][0].__name__ == "_inspect_worker"
    assert supervised[0][2]["max_mem_mb"] <= 1024 and supervised[0][2]["timeout_sec"] <= 60


def test_hdf5_external_links_fail_closed(inputs, supervised):
    import h5py
    source = inputs / "linked.h5ad"
    with h5py.File(source, "w") as handle:
        handle["X"] = h5py.ExternalLink("/outside/not-authorized.h5ad", "/X")
    with pytest.raises((ValueError, PermissionError), match="link"):
        api().InputDiscovery([inputs]).inspect(source)


def test_header_inspection_does_not_claim_table_rows_are_cells(inputs, supervised):
    source = inputs / "measurements.tsv"
    source.write_text("sample\tdonor\tcondition\na\td1\tcontrol\n")
    profile = api().InputDiscovery([inputs]).inspect(source)
    assert profile["observed"]["columns"] == ["sample", "donor", "condition"]
    assert profile["observed"]["cells"] is None and profile["observed"]["genes"] is None
    assert profile["declarations"] == {}


def test_small_structured_json_reports_keys_without_claiming_assay(inputs, supervised):
    source = inputs / "counts.json"
    source.write_text(json.dumps({"format": "supplied-format/1", "counts": [[1, 2]], "samples": ["a"]}))
    profile = api().InputDiscovery([inputs]).inspect(source)
    assert profile["observed"]["top_level_keys"] == ["format", "counts", "samples"]
    assert profile["observed"]["cells"] is None and profile["declarations"] == {}


def test_large_json_is_not_deserialized_for_header_profile(inputs, supervised):
    source = inputs / "large.json"
    source.write_text(json.dumps({"large": "x" * 2000}))
    profile = api().InputDiscovery([inputs], header_bytes=128).inspect(source)
    assert profile["observed"]["header_status"] == "not_inspected_byte_budget"
    assert profile["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_changed_input_during_worker_is_not_published_or_cached(inputs, supervised, monkeypatch):
    source = inputs / "changing.csv"
    source.write_text("sample,batch\na,A\n")
    module = api()
    original = module._hash_fd
    def change(fd, maximum):
        result = original(fd, maximum)
        source.write_text("sample,donor\na,B\n")
        return result
    monkeypatch.setattr(module, "_hash_fd", change)
    discovery = module.InputDiscovery([inputs])
    with pytest.raises(ValueError, match="changed"):
        discovery.inspect(source)
    assert not discovery._profiles


def test_replaced_configured_root_cannot_expand_authority(inputs, tmp_path):
    discovery = api().InputDiscovery([inputs])
    inputs.rename(tmp_path / "old-inputs")
    inputs.mkdir()
    (inputs / "new.json").write_text("{}")
    with pytest.raises(ValueError, match="root.*changed"):
        discovery.list()


def test_missing_worker_cleanup_does_not_cache_a_profile(inputs, monkeypatch):
    from devharness_core import watchdog
    source = inputs / "data.csv"
    source.write_text("sample,batch\na,A\n")
    monkeypatch.setattr(watchdog, "run_sandboxed", lambda target, args, **kwargs: target(*args))
    discovery = api().InputDiscovery([inputs])
    with pytest.raises(RuntimeError, match="cleanup"):
        discovery.inspect(source)
    assert not discovery._profiles


def test_real_supervised_header_worker(inputs):
    source = inputs / "data.csv"
    source.write_text("sample,batch\na,A\n")
    result = api().InputDiscovery([inputs]).inspect(source)
    assert result["observed"]["columns"] == ["sample", "batch"]
    assert result["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_real_supervised_h5ad_worker_uses_only_headers(inputs):
    source = inputs / "large-logical-matrix.h5ad"
    h5ad(source)
    assert source.stat().st_size < 100000
    result = api().InputDiscovery([inputs]).inspect(source)
    assert (result["observed"]["cells"], result["observed"]["genes"]) == (50000, 20000)
    assert result["observed"]["expression_values_read"] is False
    assert result["inspection"]["worker_cleanup_verified"] is True


def test_header_and_file_budgets_fail_before_publishing_profile(inputs, supervised):
    source = inputs / "too-wide.csv"
    source.write_text("sample," + "x" * 300 + "\na,1\n")
    for options in [{"max_file_bytes": 4}, {"header_bytes": 10}, {"max_columns": 1}]:
        discovery = api().InputDiscovery([inputs], **options)
        with pytest.raises(ValueError, match="budget|bounds"):
            discovery.inspect(source)
        assert not discovery._profiles


def test_hashing_replacement_cannot_return_an_old_digest(inputs, monkeypatch):
    source = inputs / "changing.json"
    source.write_text("{}")
    module = api()
    original = module._hash_fd
    def replace(fd, maximum):
        value = original(fd, maximum)
        replacement = inputs / "replacement.json"
        replacement.write_text('{"changed":true}')
        replacement.replace(source)
        return value
    monkeypatch.setattr(module, "_hash_fd", replace)
    with pytest.raises(ValueError, match="changed"):
        module.InputDiscovery([inputs]).list()


def test_sparse_header_shape_is_read_without_sparse_arrays(inputs, supervised, monkeypatch):
    import h5py
    source = inputs / "sparse.h5ad"
    h5ad(source)
    with h5py.File(source, "r+") as handle:
        del handle["X"]
        group = handle.create_group("X")
        group.attrs["shape"] = [50000, 20000]
        group.attrs["encoding-type"] = "csr_matrix"
        group.create_dataset("data", shape=(1000000,), dtype="float32", chunks=True)
        group.create_dataset("indices", shape=(1000000,), dtype="int32", chunks=True)
        group.create_dataset("indptr", shape=(50001,), dtype="int64", chunks=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("Sparse values must not be read for a header profile")
    monkeypatch.setattr(h5py.Dataset, "__getitem__", forbidden)
    result = api().InputDiscovery([inputs]).inspect(source)
    assert result["observed"]["cells"] == 50000


def test_resource_interruption_is_an_explicit_service_error_without_cache(inputs, monkeypatch):
    from devharness_core import watchdog
    source = inputs / "data.csv"
    source.write_text("sample,batch\na,A\n")
    def interrupt(*args, **kwargs):
        raise watchdog.HarnessResourceInterrupt("Worker timed out after 20 seconds")
    monkeypatch.setattr(watchdog, "run_sandboxed", interrupt)
    discovery = api().InputDiscovery([inputs])
    with pytest.raises(RuntimeError, match="inspection.*resource|inspection.*timed"):
        discovery.inspect(source)
    assert not discovery._profiles


@pytest.mark.parametrize("suffix", [".json", ".csv", ".tsv", ".h5ad"])
def test_source_path_admits_supported_regular_files_without_hashing(inputs, monkeypatch, suffix):
    source = inputs / ("data" + suffix)
    source.write_bytes(b"metadata admission precedes header validation")
    module = api()
    def forbidden(*args, **kwargs):
        raise AssertionError("Metadata admission must not hash or parse the file")
    monkeypatch.setattr(module, "_hash_fd", forbidden)
    discovery = module.InputDiscovery([inputs])
    assert discovery.source_path(source) == source
    assert not discovery._hashes and not discovery._profiles


def test_source_path_denies_symlinks_pipes_outside_and_oversize(inputs, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    linked = inputs / "linked.json"
    linked.symlink_to(outside)
    pipe = inputs / "pipe.tsv"
    os.mkfifo(pipe)
    oversized = inputs / "large.csv"
    oversized.write_text("header,too-large\n")
    discovery = api().InputDiscovery([inputs], max_file_bytes=4)
    for source in [outside, linked, pipe, oversized]:
        with pytest.raises((ValueError, PermissionError)):
            discovery.source_path(source)


def test_source_path_revalidates_metadata_identity(inputs, monkeypatch):
    source = inputs / "changing.json"
    source.write_text("{}")
    module = api()
    original = module._unchanged
    def mutate(root, parts, root_identity, expected):
        source.write_text('{"changed":true}')
        return original(root, parts, root_identity, expected)
    monkeypatch.setattr(module, "_unchanged", mutate)
    with pytest.raises(ValueError, match="changed"):
        module.InputDiscovery([inputs]).source_path(source)
