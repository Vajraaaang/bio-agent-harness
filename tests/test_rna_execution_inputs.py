"""Synthetic custody/format checks; these fixtures select no scientific method."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile

import pytest

from devbench.analysis.methods import MethodService
from devbench.analysis.rna_execution_inputs import prepare_execution_inputs, load_execution_inputs


@pytest.fixture
def protected_workspace():
    # Sealed evidence must remain outside platform scratch. Scope the fixture
    # explicitly; do not change TMPDIR or weaken the protected-storage check.
    root = Path(__file__).resolve().parents[1] / "reports" / "local-rna-analyst-2026-10-28" / "increment-02" / "test-workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="execution-inputs-", dir=root))


def inputs(tmp_path, *, representation="normalized_expression", metadata=None, matrix=None):
    service = MethodService(tmp_path / "methods")
    samples = ["s1", "s2", "s3", "s4"]
    values = matrix or {"gene_ids": ["g1", "g2"], "sample_ids": samples,
                        "matrix": [[1.0, 2.25, 3.5, 4.0], [5.0, 6.0, 7.0, 8.0]]}
    rows = metadata or [{"sample_id": sample, "group": "A" if i < 2 else "B",
                         "unit": "u" + str(i + 1), "batch": "b" + str(i % 2)}
                        for i, sample in enumerate(samples)]
    refs = {}
    for port, data in (("matrix", values), ("metadata", {"samples": rows})):
        path = tmp_path / (port + ".json")
        path.write_text(json.dumps(data), encoding="utf-8")
        refs[port] = service.import_artifact(path, artifact_type="json", actor="synthetic-test")
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
               "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {
                   "format": "devbench.bulk-rna-input/1", "representation": representation,
                   "axes": ["gene", "sample"], "provenance": {"source": "synthetic engineering fixture"},
                   "biological_independence": {"independent_units": True},
                   "metadata_roles": {"condition": "group", "biological_unit": "unit", "batch": "batch"}}}
    return service, binding, values, rows


def ports(*, infer=False, encoding="json"):
    return {"format": "devbench.rna-execution-ports/2", "ports": {
        "counts": {"source": "matrix", "artifact_type": "file" if encoding != "json" else "json",
                   "encoding": encoding, "required": True, "parameter": "counts_path"},
        "metadata": {"source": "metadata", "artifact_type": "table" if encoding != "json" else "json",
                     "encoding": "csv" if encoding == "hdf5" else encoding, "required": True, "parameter": "metadata_path"}},
        "accepted_representations": ["raw_counts", "normalized_expression"],
        "objective": {"intent": "infer" if infer else "descriptive"},
        "design": {"contrasts": [{"field": "group", "reference": "A", "target": "B"}] if infer else [],
                   "covariates": [], "chronological_field": None, "chronological_order": []},
        "string_columns": ["sample_id", "group", "unit", "batch"]}


def registered_ports(service, *, infer=False, encoding="csv", representation="normalized_expression"):
    """Declare a real consumer/parser without running its scientific worker."""
    selected = ports(infer=infer, encoding=encoding)
    selected["method_version_id"] = service.register_dispatcher("fit_temporal_trajectory", actor="synthetic-test")["version_id"]
    selected["parameters"] = {"formula_str": "~ C(group)", "time_col": "unused_fixture_time",
        "gene_list": ["g1", "g2"], "selection_criterion": "fixed", "spline_dfs": [3],
        "data_type": "continuous" if representation == "normalized_expression" else "counts"}
    selected["string_columns"] = ["sample_id"]
    return selected


def prepare(tmp_path, **kwargs):
    service, binding, matrix, rows = inputs(tmp_path, **kwargs)
    retained = prepare_execution_inputs(binding, registered_ports(service), artifact_service=service, output=tmp_path / "bridge")
    return service, binding, matrix, rows, retained


def test_csv_bridge_preserves_values_ids_order_and_original_references(protected_workspace):
    tmp_path = protected_workspace
    service, binding, matrix, rows, result = prepare(tmp_path)
    record = load_execution_inputs(result["record_ref"], artifact_service=service, dataset_binding=binding)
    assert record["format"] == "devbench.rna-execution-inputs/2"
    assert record["original_refs"]["matrix"]["artifact_id"] == binding["matrix_artifact_id"]
    assert record["execution_refs"]["counts"]["artifact_type"] == "file"
    with service.artifact_path(record["execution_refs"]["counts"]["artifact_id"]).open(newline="") as handle:
        actual = list(csv.reader(handle))
    assert actual[0] == ["gene_id", *matrix["sample_ids"]]
    assert [row[0] for row in actual[1:]] == matrix["gene_ids"]
    assert [[json.loads(value) for value in row[1:]] for row in actual[1:]] == matrix["matrix"]
    assert type(json.loads(actual[1][1])) is float and type(json.loads(actual[2][1])) is float
    from devharness_core.storage import ChunkedExpressionStore
    from devharness_server.tools_stat import _metadata
    parser_matrix = tmp_path / "registered-matrix-parser.csv"
    parser_matrix.write_bytes(service.artifact_path(record["execution_refs"]["counts"]["artifact_id"]).read_bytes())
    with ChunkedExpressionStore(parser_matrix) as parsed:
        assert parsed.gene_names == matrix["gene_ids"] and parsed.sample_names == matrix["sample_ids"]
        observed = parsed.get_chunk(slice(None), slice(None))
        assert observed.tolist() == matrix["matrix"]
        assert all(type(value.item()) is float for row in observed for value in row)
    parser_metadata = tmp_path / "registered-metadata-parser.csv"
    parser_metadata.write_bytes(service.artifact_path(record["execution_refs"]["metadata"]["artifact_id"]).read_bytes())
    assert _metadata(str(parser_metadata), "unused_fixture_time").to_dict("records") == rows
    assert record["capabilities"] == ["bulk_gene_expression", "known_measurement_representation", "exact_sample_alignment"]
    assert record["transformation_receipt_ref"]["sha256"]
    assert result["root_hash"] and result["manifest_path"]
    with pytest.raises((ValueError, FileExistsError)):
        prepare_execution_inputs(binding, registered_ports(service), artifact_service=service, output=tmp_path / "bridge")


def test_json_ports_use_original_refs_without_rewriting(protected_workspace):
    tmp_path = protected_workspace
    mixed = {"gene_ids": ["g1"], "sample_ids": ["s1", "s2", "s3", "s4"], "matrix": [[1, 2.25, 3, 4.0]]}
    service, binding, _, _ = inputs(tmp_path, matrix=mixed)
    result = prepare_execution_inputs(binding, ports(encoding="json"), artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(result["record_ref"], artifact_service=service)
    assert record["execution_refs"]["counts"]["artifact_id"] == binding["matrix_artifact_id"]
    assert record["execution_refs"]["metadata"]["artifact_id"] == binding["metadata_artifact_id"]


def test_csv_requires_actual_registered_parser_not_caller_string_dtype_claim(tmp_path):
    service, binding, _, _ = inputs(tmp_path)
    with pytest.raises(ValueError, match="registered|parser"):
        prepare_execution_inputs(binding, ports(encoding="csv"), artifact_service=service, output=tmp_path / "bridge")
    selected = registered_ports(service)
    selected["string_columns"] = ["sample_id", "unsafe"]
    with pytest.raises(ValueError, match="parser|string"):
        prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")


def test_registered_csv_rejects_mixed_matrix_numeric_types(tmp_path):
    mixed = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"],
             "matrix": [[1, 2.25, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]}
    service, binding, _, _ = inputs(tmp_path, matrix=mixed)
    with pytest.raises(ValueError, match="type|lossless"):
        prepare_execution_inputs(binding, registered_ports(service), artifact_service=service, output=tmp_path / "bridge")


def test_registered_hdf5_preserves_raw_integer_count_types_and_values(protected_workspace):
    tmp_path = protected_workspace
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"],
              "matrix": [[0, 1, 2, 3], [4, 5, 6, 9007199254740993]]}
    service, binding, _, _ = inputs(tmp_path, matrix=matrix, representation="raw_counts")
    selected = registered_ports(service, encoding="hdf5", representation="raw_counts")
    retained = prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(retained, artifact_service=service)
    source = service.artifact_path(record["execution_refs"]["counts"]["artifact_id"])
    actual_parser_input = tmp_path / "registered-count-parser.h5"
    actual_parser_input.write_bytes(source.read_bytes())
    from devharness_core.storage import ChunkedExpressionStore
    with ChunkedExpressionStore(actual_parser_input) as store:
        observed = store.get_chunk(slice(None), slice(None))
        assert store.gene_names == matrix["gene_ids"] and store.sample_names == matrix["sample_ids"]
        assert observed.tolist() == matrix["matrix"]
        assert all(type(value.item()) is int for row in observed for value in row)
    assert record["execution_refs"]["counts"]["suffix"] == ".h5"


@pytest.mark.parametrize("attack", ["expression_itemsize", "label_itemsize", "unreviewed_vlen_encoding"])
def test_hdf5_schema_bounds_reject_tiny_file_with_unsafe_logical_allocation_before_read(tmp_path, monkeypatch, attack):
    import h5py
    import numpy as np
    from devbench.analysis.rna_execution_inputs import _check_hdf5
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"],
              "matrix": [[1, 2, 3, 4], [4, 3, 2, 1]]}
    malformed = tmp_path / "unsafe-unallocated.h5"
    with h5py.File(malformed, "x") as handle:
        handle.create_dataset("expression", shape=(2, 4), dtype=np.dtype("S1000000000") if attack == "expression_itemsize" else np.int64)
        handle.create_dataset("gene_names", shape=(2,), dtype=np.dtype("S1000000000") if attack == "label_itemsize"
            else h5py.string_dtype("ascii" if attack == "unreviewed_vlen_encoding" else "utf-8"))
        handle.create_dataset("sample_names", shape=(4,), dtype=h5py.string_dtype("utf-8"))
    assert malformed.stat().st_size < 20_000
    reads = []
    def forbid_allocation(dataset, selection):
        reads.append((dataset.name, selection))
        raise AssertionError("Unsafe HDF5 logical storage must be rejected before allocating any dataset read")
    monkeypatch.setattr(h5py.Dataset, "__getitem__", forbid_allocation)
    with pytest.raises(ValueError, match="dtype|encoding|logical|itemsize|storage|bound"):
        _check_hdf5(malformed, matrix)
    assert reads == []


def test_shared_vlen_utf8_heap_is_refused_before_any_label_materialization(tmp_path, monkeypatch):
    import h5py
    import numpy as np
    from devbench.analysis.rna_execution_inputs import _check_hdf5
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2"], "matrix": [[1.0, 2.0], [3.0, 4.0]]}
    path = tmp_path / "tiny-shared-vlen-heap.h5"
    with h5py.File(path, "x") as handle:
        handle.create_dataset("expression", data=np.asarray(matrix["matrix"], dtype=np.float64))
        handle.create_dataset("gene_names", data=["λ" * 410, "λ" * 410], dtype=h5py.string_dtype("utf-8"))
        handle["sample_names"] = handle["gene_names"]
        assert handle["sample_names"].id == handle["gene_names"].id
    assert path.stat().st_size < 20_000
    reads = []
    def prevent_heap_materialization(dataset, selection):
        reads.append(dataset.name)
        raise AssertionError("Reject variable-length heaps before materializing labels")
    monkeypatch.setattr(h5py.Dataset, "__getitem__", prevent_heap_materialization)
    with pytest.raises(ValueError, match="fixed|variable|heap|dtype|encoding|bound"):
        _check_hdf5(path, matrix)
    assert reads == []


def test_registered_hdf5_bridge_uses_fixed_width_utf8_and_preserves_gene_sample_ids(protected_workspace):
    tmp_path = protected_workspace
    import h5py
    from devharness_core.storage import ChunkedExpressionStore
    genes, samples = ["001λ", "NA🧬", "🧬" * 200], ["001λ", "NA🧬", "s3é", "s4"]
    matrix = {"gene_ids": genes, "sample_ids": samples,
              "matrix": [[1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0], [2.0, 3.0, 1.0, 4.0]]}
    rows = [{"sample_id": sample, "unit": "u" + str(i + 1), "group": "A" if i < 2 else "B", "batch": "b" + str(i % 2)}
            for i, sample in enumerate(samples)]
    service, binding, _, _ = inputs(tmp_path, matrix=matrix, metadata=rows)
    selected = registered_ports(service, encoding="hdf5")
    selected["parameters"]["gene_list"] = genes
    retained = prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(retained, artifact_service=service)
    path = service.artifact_path(record["execution_refs"]["counts"]["artifact_id"])
    with h5py.File(path, "r") as handle:
        for name, expected in (("gene_names", genes), ("sample_names", samples)):
            declared = h5py.check_string_dtype(handle[name].dtype)
            assert declared.encoding == "utf-8" and declared.length == max(len(value.encode("utf-8")) for value in expected)
            assert handle[name].dtype.kind == "S" and 1 <= handle[name].dtype.itemsize <= 800
    parser_input = tmp_path / "registered-unicode-parser.h5"
    retained_bytes = path.read_bytes()
    parser_input.write_bytes(retained_bytes)
    assert parser_input.read_bytes() == retained_bytes
    with ChunkedExpressionStore(parser_input) as store:
        assert store.gene_names == genes and store.sample_names == samples
        assert store.get_chunk(slice(None), slice(None)).tolist() == matrix["matrix"]


def test_reviewed_hdf5_label_reads_are_bounded_and_preserve_utf8_ids(tmp_path, monkeypatch):
    import h5py
    from devbench.analysis.rna_execution_inputs import _write_hdf5, _check_hdf5
    matrix = {"gene_ids": ["g" + str(i) + "λ" for i in range(70)], "sample_ids": ["s1", "s2"],
              "matrix": [[float(i), float(i + 1)] for i in range(70)]}
    path = tmp_path / "bounded-labels.h5"
    _write_hdf5(path, matrix)
    original = h5py.Dataset.__getitem__
    sizes = []
    def bounded_read(dataset, selection):
        if dataset.name in {"/gene_names", "/sample_names"}:
            assert isinstance(selection, slice) and selection.start is not None and selection.stop is not None
            sizes.append(selection.stop - selection.start)
            assert sizes[-1] <= 32
        return original(dataset, selection)
    monkeypatch.setattr(h5py.Dataset, "__getitem__", bounded_read)
    _check_hdf5(path, matrix)
    assert len(sizes) >= 4


@pytest.mark.parametrize("attack", ["unknown_unit_label", "repeated_donor", "missing_donor"])
def test_unique_specimens_cannot_hide_unknown_or_dependent_biological_units(tmp_path, attack):
    rows = [{"sample_id": "s" + str(i), "unit": "specimen" + str(i), "group": "A" if i < 3 else "B",
             "batch": "b" + str(i % 2), "donor_id": "donor" + str(i)} for i in range(1, 5)]
    if attack == "unknown_unit_label": rows[0]["unit"] = "unknown"
    elif attack == "repeated_donor": rows[1]["donor_id"] = rows[0]["donor_id"]
    else: rows[1]["donor_id"] = None
    service, binding, _, _ = inputs(tmp_path, metadata=rows)
    with pytest.raises(ValueError, match="precondition|biological|unknown|repeated"):
        prepare_execution_inputs(binding, ports(infer=True), artifact_service=service, output=tmp_path / "bridge")


def test_bridge_loader_uses_read_only_service_without_creating_scratch_files(protected_workspace, monkeypatch):
    tmp_path = protected_workspace
    service, binding, _, _, retained = prepare(tmp_path)
    reader = MethodService(service.root, readonly=True)
    def forbid_scratch(*args, **kwargs):
        raise AssertionError("A read-only bridge load must not create scratch files")
    monkeypatch.setattr("devbench.analysis.rna_execution_inputs.tempfile.TemporaryDirectory", forbid_scratch)
    assert load_execution_inputs(retained, artifact_service=reader, dataset_binding=binding)["format"] == "devbench.rna-execution-inputs/2"


def test_reader_binding_rejects_normalized_integral_values_reinterpreted_as_counts(protected_workspace):
    tmp_path = protected_workspace
    from devbench.analysis.rna_execution_inputs import validate_execution_method_binding
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"],
              "matrix": [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]}
    service, binding, _, _ = inputs(tmp_path, matrix=matrix)
    selected = registered_ports(service)
    retained = prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(retained, artifact_service=service)
    validate_execution_method_binding(record, selected["method_version_id"], selected["parameters"], artifact_service=service)
    changed = {**selected["parameters"], "data_type": "counts"}
    with pytest.raises(ValueError, match="method|parameters|reader|representation"):
        validate_execution_method_binding(record, selected["method_version_id"], changed, artifact_service=service)


def auto_reader_ports(service, *, encoding="hdf5"):
    selected = ports(encoding=encoding)
    selected["method_version_id"] = service.register_dispatcher("analyze_batch_effects", actor="synthetic-test")["version_id"]
    selected["parameters"] = {"time_col": "unused_fixture_time", "gene_list": ["g1", "g2"]}
    selected["string_columns"] = ["sample_id"]
    return selected


@pytest.mark.parametrize("representation,numeric_type,observed", [
    ("normalized_expression", int, "counts"),
    ("raw_counts", float, "continuous"),
])
def test_actual_auto_reader_refuses_conflicting_declared_hdf5_representation(tmp_path, representation, numeric_type, observed):
    import h5py
    import numpy as np
    from devharness_core.storage import ChunkedExpressionStore
    from devharness_server.normalization import expression_summary
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"],
              "matrix": [[numeric_type(value) for value in row] for row in ([1, 2, 3, 4], [4, 3, 2, 1])]}
    consumer_input = tmp_path / "actual-auto-reader.h5"
    with h5py.File(consumer_input, "x") as handle:
        handle.create_dataset("expression", data=np.asarray(matrix["matrix"], dtype=np.int64 if numeric_type is int else np.float64))
        handle.create_dataset("gene_names", data=matrix["gene_ids"], dtype=h5py.string_dtype("utf-8"))
        handle.create_dataset("sample_names", data=matrix["sample_ids"], dtype=h5py.string_dtype("utf-8"))
    with ChunkedExpressionStore(consumer_input) as store:
        assert expression_summary(store)["data_type"] == observed
    service, binding, _, _ = inputs(tmp_path, matrix=matrix, representation=representation)
    with pytest.raises(ValueError, match="representation|interpretation|data.type|consumer"):
        prepare_execution_inputs(binding, auto_reader_ports(service), artifact_service=service, output=tmp_path / "bridge")
    assert not (tmp_path / "bridge").exists()


def test_bridge_joins_metadata_by_sample_id_and_retains_source_order(protected_workspace):
    tmp_path = protected_workspace
    rows = [{"sample_id": "s" + str(i), "group": "A" if i < 3 else "B", "unit": "u" + str(i), "batch": "b" + str(i % 2)}
            for i in (4, 1, 3, 2)]
    service, binding, _, _ = inputs(tmp_path, metadata=rows)
    result = prepare_execution_inputs(binding, registered_ports(service), artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(result["record_ref"], artifact_service=service)
    receipt = json.loads(__import__("pathlib").Path(record["transformation_receipt_ref"]["path"]).read_bytes())
    assert receipt["metadata_alignment"]["source_order_sample_ids"] == ["s4", "s1", "s3", "s2"]
    with service.artifact_path(record["execution_refs"]["metadata"]["artifact_id"]).open(newline="") as handle:
        assert [row["sample_id"] for row in csv.DictReader(handle)] == ["s1", "s2", "s3", "s4"]


@pytest.mark.parametrize("attack", ["duplicate_gene", "duplicate_sample", "duplicate_metadata", "nonfinite", "unknown_representation", "counts_confusion"])
def test_invalid_or_unsupported_inputs_are_rejected_before_derivation(tmp_path, attack):
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"], "matrix": [[1.0, 2.25, 3.5, 4.0], [5, 6, 7, 8]]}
    rows = [{"sample_id": "s" + str(i), "group": "A", "unit": "u" + str(i), "batch": "b"} for i in range(1, 5)]
    representation = "normalized_expression"
    if attack == "duplicate_gene": matrix["gene_ids"] = ["g1", "g1"]
    elif attack == "duplicate_sample": matrix["sample_ids"][1] = "s1"
    elif attack == "duplicate_metadata": rows[1]["sample_id"] = "s1"
    elif attack == "nonfinite": matrix["matrix"][0][1] = float("nan")
    elif attack == "unknown_representation": representation = None
    else: representation = "raw_counts"
    service, binding, _, _ = inputs(tmp_path, matrix=matrix, metadata=rows, representation=representation)
    with pytest.raises(ValueError):
        prepare_execution_inputs(binding, ports(), artifact_service=service, output=tmp_path / "bridge")
    assert not (tmp_path / "bridge").exists()


@pytest.mark.parametrize("attack", ["unit_unknown", "unit_repeated", "confounded", "invalid_contrast", "rank_deficient"])
def test_inference_preconditions_block_before_derivation(tmp_path, attack):
    service, binding, _, rows = inputs(tmp_path)
    requested = ports(infer=True)
    if attack == "unit_unknown": binding["declarations"]["biological_independence"] = None
    elif attack == "invalid_contrast": requested["design"]["contrasts"][0]["target"] = "absent"
    elif attack == "rank_deficient":
        requested["design"]["covariates"] = ["duplicate_group"]
        for row in rows: row["duplicate_group"] = row["group"]
    if attack in {"unit_repeated", "confounded", "rank_deficient"}:
        if attack == "unit_repeated": rows[1]["unit"] = rows[0]["unit"]
        elif attack == "confounded":
            for row in rows: row["batch"] = row["group"]
        path = tmp_path / "changed-metadata.json"
        path.write_text(json.dumps({"samples": rows}))
        binding["metadata_artifact_id"] = service.import_artifact(path, artifact_type="json", actor="synthetic-test")["artifact_id"]
    with pytest.raises(ValueError, match="precondition|independent|contrast|confounding|rank"):
        prepare_execution_inputs(binding, requested, artifact_service=service, output=tmp_path / "bridge")


@pytest.mark.parametrize("target", ["time", "condition"])
def test_numeric_categorical_batch_labels_cannot_hide_complete_target_confounding(protected_workspace, target):
    tmp_path = protected_workspace
    samples = ["s" + str(i + 1) for i in range(8)]
    rows = [{"sample_id": sample, "unit": "u" + str(i + 1), "group": 1 + i // 2,
             "time": 1 + i // 2, "batch": 2 ** (i // 2)} for i, sample in enumerate(samples)]
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": samples,
              "matrix": [[float(i + 1) for i in range(8)], [float(8 - i) for i in range(8)]]}
    service, binding, _, _ = inputs(tmp_path, matrix=matrix, metadata=rows)
    binding["declarations"]["metadata_roles"]["time"] = "time"
    requested = ports(infer=True)
    requested["design"] = {"contrasts": [{"field": "group", "reference": "1", "target": "4"}] if target == "condition" else [],
        "covariates": [], "chronological_field": "time" if target == "time" else None,
        "chronological_order": ["1", "2", "3", "4"] if target == "time" else []}
    with pytest.raises(ValueError, match="complete.confounding|complete_confounding"):
        prepare_execution_inputs(binding, requested, artifact_service=service, output=tmp_path / "infer")
    assert not (tmp_path / "infer").exists()
    retained = prepare_execution_inputs(binding, ports(), artifact_service=service, output=tmp_path / "descriptive")
    record = load_execution_inputs(retained, artifact_service=service)
    assert json.loads(service.artifact_path(record["original_refs"]["metadata"]["artifact_id"]).read_bytes())["samples"] == rows
    assert "identifiable_design" not in record["capabilities"]


def test_numeric_batch_categories_with_overlap_preserve_numeric_nuisance_design(protected_workspace):
    tmp_path = protected_workspace
    samples = ["s" + str(i + 1) for i in range(8)]
    rows = [{"sample_id": sample, "unit": "u" + str(i + 1), "group": "A" if i < 4 else "B",
             "time": 1 + i // 2, "batch": 1 + i % 2, "nuisance": float(i * i)} for i, sample in enumerate(samples)]
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": samples,
              "matrix": [[float(i + 1) for i in range(8)], [float(8 - i) for i in range(8)]]}
    service, binding, _, _ = inputs(tmp_path, matrix=matrix, metadata=rows)
    selected = ports(infer=True)
    selected["design"] = {"contrasts": [], "covariates": ["nuisance"],
        "chronological_field": "time", "chronological_order": ["1", "2", "3", "4"]}
    retained = prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(retained, artifact_service=service)
    receipt = json.loads(__import__("pathlib").Path(record["transformation_receipt_ref"]["path"]).read_bytes())
    assert receipt["design_audit"]["rank"] == receipt["design_audit"]["columns"] == 4
    assert receipt["design_audit"]["blockers"] == []


def test_source_or_derived_artifact_tampering_is_rejected(protected_workspace):
    tmp_path = protected_workspace
    service, binding, _, _, retained = prepare(tmp_path)
    record = load_execution_inputs(retained["record_ref"], artifact_service=service)
    path = service.artifact_path(record["execution_refs"]["counts"]["artifact_id"])
    path.chmod(0o644)
    path.write_bytes(path.read_bytes().replace(b"2.25", b"9.25"))
    with pytest.raises(ValueError, match="hash|changed"):
        load_execution_inputs(retained["record_ref"], artifact_service=service)


def test_unrelated_dataset_binding_is_rejected(protected_workspace):
    tmp_path = protected_workspace
    service, binding, _, _, result = prepare(tmp_path)
    unrelated = deepcopy(binding)
    unrelated["metadata_artifact_id"] = binding["matrix_artifact_id"]
    with pytest.raises(ValueError, match="original|binding|metadata"):
        load_execution_inputs(result["record_ref"], artifact_service=service, dataset_binding=unrelated)


@pytest.mark.parametrize("value", ["NA", "", "001", None])
def test_unsafe_csv_metadata_type_or_missingness_conversion_is_rejected(tmp_path, value):
    rows = [{"sample_id": "s" + str(i), "unit": "u" + str(i), "group": "A", "batch": "b", "unsafe": value} for i in range(1, 5)]
    service, binding, _, _ = inputs(tmp_path, metadata=rows)
    with pytest.raises(ValueError, match="lossless|type|missing"):
        prepare_execution_inputs(binding, registered_ports(service), artifact_service=service, output=tmp_path / "bridge")


def test_bulk_original_limits_are_not_silently_increased(tmp_path, monkeypatch):
    service, binding, _, _ = inputs(tmp_path)
    monkeypatch.setattr("devbench.analysis.rna_execution_inputs.MAX_MATRIX_BYTES", 10)
    with pytest.raises(ValueError, match="bound|100 MB|byte"):
        prepare_execution_inputs(binding, ports(), artifact_service=service, output=tmp_path / "bridge")
