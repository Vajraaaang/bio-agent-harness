"""Bulk intake options and read-only compressed input contracts."""

import csv
import gzip
import hashlib
import io
from pathlib import Path

import pytest

from devbench.analysis.workbench_episode_intake import (
    compile_bulk_intake,
    inspect_matrix_intake,
)
from devbench.core.protected_partitions import PartitionVault
from devbench.core.publication_families import FamilySpec
from tests.test_workbench_episode_intake import record


def test_both_contrasts_offer_observed_labels_for_each_condition(tmp_path):
    request = record(tmp_path)
    declarations = request["scientific_context"]["bulk_episode_declarations"]
    for name in ("condition_column", "contrast_numerator", "contrast_denominator"):
        declarations.pop(name)
    result = compile_bulk_intake(request, output_dir=tmp_path / "state")
    fields = {field["field"]: field for field in result["clarification_fields"]}
    expected = [{"value": label, "label": label} for label in ("A", "B")]

    assert result["status"] == "clarification"
    assert "condition_column" in fields
    for name in ("contrast_numerator", "contrast_denominator"):
        assert fields[name]["type"] == "select"
        assert fields[name]["depends_on"] == "condition_column"
        assert fields[name]["options_by_condition"]["condition"] == expected
        assert fields[name]["options"] == []

    declarations["condition_column"] = "condition"
    updated = compile_bulk_intake(request, output_dir=tmp_path / "state")
    for field in updated["clarification_fields"]:
        assert field["field"] in {"contrast_numerator", "contrast_denominator"}
        assert field["options"] == expected


def _replace_source(request, index, path):
    request["sources"][index] = str(path)
    request["source_profiles"][index] = {
        "source": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
        "observed": {},
    }


def _transcode_tables(request, suffix):
    for index, original in enumerate(request["sources"]):
        original = Path(original)
        path = original.with_suffix(suffix)
        with original.open(newline="") as stream:
            buffer = io.StringIO(newline="")
            delimiter = "\t" if suffix.startswith(".tsv") else ","
            csv.writer(buffer, delimiter=delimiter).writerows(csv.reader(stream))
        raw = buffer.getvalue().encode()
        path.write_bytes(gzip.compress(raw) if suffix.endswith(".gz") else raw)
        _replace_source(request, index, path)


@pytest.mark.parametrize("suffix", [".csv.gz", ".tsv.gz"])
def test_compressed_axes_join_exact_ids_without_implying_legacy_execution(
    tmp_path, suffix
):
    request = record(tmp_path)
    expected = inspect_matrix_intake(request)
    _transcode_tables(request, suffix)

    result = inspect_matrix_intake(request)

    assert result["sample_ids"] == expected["sample_ids"]
    assert result["gene_count"] == expected["gene_count"]
    assert result["metadata_rows"] == expected["metadata_rows"]
    assert result["fact_provenance"]["Observed"]["expression_file_type"] == suffix
    compiled = compile_bulk_intake(request, output_dir=tmp_path / "state")
    assert compiled["status"] == "unsupported"
    assert "requires original CSV" in compiled["reason"]


def test_compressed_metadata_rejects_oversized_decoded_payload(tmp_path):
    from devbench.analysis.rna_input_set_contract import MAX_METADATA_BYTES

    request = record(tmp_path)
    oversized = tmp_path / "metadata.csv.gz"
    oversized.write_bytes(gzip.compress(b"a" * (MAX_METADATA_BYTES + 1)))
    _replace_source(request, 1, oversized)

    with pytest.raises(ValueError, match="Metadata exceeds its bounded intake limit"):
        inspect_matrix_intake(request)


@pytest.mark.parametrize("suffix", [".csv", ".tsv", ".csv.gz", ".tsv.gz"])
def test_governed_registration_preserves_exact_table_and_membership_axes(
    tmp_path, suffix
):
    import h5py
    import numpy as np

    from devbench.analysis.workbench_bulk_registration import (
        register_governed_bulk_inputs,
    )

    request = record(tmp_path)
    with Path(request["sources"][0]).open(newline="") as source:
        header, *rows = csv.reader(source)
    if suffix != ".csv":
        _transcode_tables(request, suffix)
    vault = PartitionVault(tmp_path.resolve() / "vault")

    artifacts, roots = register_governed_bulk_inputs(request, vault)

    expression, metadata, family = artifacts
    assert roots == tuple(row["sha256"] for row in request["source_profiles"])
    assert len({item.unit_membership_sha256 for item in artifacts}) == 1
    assert expression.representation == "raw_counts_h5"
    assert metadata.representation == "metadata_csv"
    assert family.representation == "family_spec_json"
    assert "candidate" in family.version_id
    assert {item.biological_unit for item in artifacts} == {"donor"}
    with vault.open(expression.sha256) as source, h5py.File(source) as handle:
        assert handle["sample_names"].asstr()[:].tolist() == header[1:]
        assert handle["gene_names"].asstr()[:].tolist() == [row[0] for row in rows]
        np.testing.assert_array_equal(
            handle["expression"][:], [[int(value) for value in row[1:]] for row in rows]
        )
    with vault.open(metadata.sha256) as stream:
        annotated = list(csv.DictReader(io.StringIO(stream.read().decode())))
    assert [row["sample_id"] for row in annotated] == header[1:]
    with vault.open(family.sha256) as stream:
        candidate = FamilySpec.model_validate_json(stream.read())
    assert candidate.feature_ids == tuple(row[0] for row in rows)
    assert (candidate.numerator, candidate.denominator) == ("B", "A")


def test_governed_registration_holds_without_explicit_unit_provenance(tmp_path):
    from devbench.analysis.workbench_bulk_registration import (
        register_governed_bulk_inputs,
    )

    request = record(tmp_path)
    request["scientific_context"]["bulk_episode_declarations"].pop(
        "declaration_provenance"
    )
    vault = PartitionVault(tmp_path.resolve() / "vault")

    assert register_governed_bulk_inputs(request, vault) is None
    assert list(vault.root.iterdir()) == []


def test_declared_count_conversion_refuses_fractional_matrix_values(tmp_path):
    from devbench.analysis.workbench_bulk_registration import (
        register_governed_bulk_inputs,
    )

    request = record(tmp_path)
    path = Path(request["sources"][0])
    with path.open(newline="") as source:
        rows = list(csv.reader(source))
    rows[1][1] = "0.5"
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)
    _replace_source(request, 0, path)
    vault = PartitionVault(tmp_path.resolve() / "vault")

    with pytest.raises(ValueError, match="raw counts must be exact"):
        register_governed_bulk_inputs(request, vault)

    assert list(vault.root.iterdir()) == []


def test_governed_original_hdf5_registration_retains_exact_bytes(tmp_path):
    import h5py

    from devbench.analysis.workbench_bulk_registration import (
        register_governed_bulk_inputs,
    )

    request = record(tmp_path)
    with Path(request["sources"][0]).open(newline="") as source:
        header, *rows = csv.reader(source)
    path = tmp_path / "original.h5"
    with h5py.File(path, "x") as handle:
        handle.create_dataset(
            "expression", data=[[int(x) for x in r[1:]] for r in rows]
        )
        handle.create_dataset("sample_names", data=[key.encode() for key in header[1:]])
        handle.create_dataset("gene_names", data=[row[0].encode() for row in rows])
    _replace_source(request, 0, path)
    vault = PartitionVault(tmp_path.resolve() / "vault")

    (expression, _, _), roots = register_governed_bulk_inputs(request, vault)

    assert (
        expression.sha256 == roots[0] == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    with vault.open(expression.sha256) as stream:
        assert stream.read() == path.read_bytes()


def test_independent_three_column_metadata_reuses_observation_identity(tmp_path):
    from devbench.analysis.workbench_bulk_registration import (
        mounted_design_columns,
        register_governed_bulk_inputs,
    )

    request = record(tmp_path)
    path = Path(request["sources"][1])
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    columns = ("sample_id", "condition", "batch")
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    request["source_profiles"][1].update(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size_bytes=path.stat().st_size,
    )
    values = request["scientific_context"]["bulk_episode_declarations"]
    values.update(dict.fromkeys(
        ("population_unit_column", "specimen_column", "library_column"), "sample_id",
    ))
    vault = PartitionVault(tmp_path.resolve() / "vault")
    artifacts, _ = register_governed_bulk_inputs(request, vault)
    aliases = mounted_design_columns(values)
    assert len(set(aliases.values())) == 3
    with vault.open(artifacts[1].sha256) as stream:
        mounted = csv.DictReader(io.StringIO(stream.read().decode()))
        assert all(
            row[column] == row["sample_id"]
            for row in mounted for column in aliases.values()
        )
