"""Contract-only checks for signed native analyses; no model or container execution."""
import hashlib
import importlib
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError


def api():
    source = Path(__file__).resolve().parents[1] / "devharness_controlled" / "native_contracts.py"
    assert source.is_file(), "Native signed-plan contract implementation is missing"
    return importlib.import_module("devharness_controlled.native_contracts")


def plan_values(**updates):
    values = {
        "schema_version": "controlled_native_analysis/1",
        "plan_id": "native-synthetic-plan",
        "revision": 1,
        "intended_use_ref": "synthetic-verification-only",
        "study_id": "synthetic-study",
        "data_classification": "synthetic",
        "engine": "edger_qlf",
        "normalization": "rle",
        "hypothesis_genes": ("Sox2", "β-actin"),
        "panel_genes": ("Other", "β-actin", "Sox2"),
        "sample_ids": ("sample-3", "sample-1", "sample-2"),
        "temporal_columns": (2, 4),
        "time_unit": "days",
        "basis_definition_ref": "SOP-SYNTHETIC/fixed-temporal-basis/1",
        "counts_sha256": "a" * 64,
        "metadata_sha256": "b" * 64,
        "full_design_sha256": "c" * 64,
        "reduced_design_sha256": "d" * 64,
        "driver_sha256": "e" * 64,
        "package_lock_sha256": "f" * 64,
        "implementation_sha256": "1" * 64,
        "environment_sha256": "2" * 64,
        "runtime_image_id": "sha256:" + "3" * 64,
        "platform": "linux/amd64",
        "max_memory_mb": 256,
        "timeout_sec": 60,
        "max_cpus": 1,
    }
    return {**values, **updates}


def plan(**updates):
    return api().NativeAnalysisPlan(**plan_values(**updates))


def test_native_plan_has_distinct_schema_and_explicit_version_pins():
    from devharness_controlled.contracts import AnalysisPlan, FrozenContract, parse_plan_bytes

    module = api()
    value = plan()
    assert isinstance(value, FrozenContract)
    assert not isinstance(value, AnalysisPlan)
    assert value.schema_version == "controlled_native_analysis/1"
    assert module.EXPECTED_R_VERSION == "4.6.1"
    assert module.EXPECTED_EDGER_VERSION == "4.10.5"
    assert module.EXPECTED_DESEQ2_VERSION == "1.52.0"
    with pytest.raises(ValidationError):
        parse_plan_bytes(value.canonical_bytes())
    with pytest.raises(ValidationError):
        plan(schema_version="controlled_analysis/1")


def test_native_plan_preserves_gene_sample_order_and_canonical_utf8(tmp_path):
    value = plan()
    encoded = value.canonical_bytes()
    expected = json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert encoded == expected
    assert "β-actin".encode("utf-8") in encoded
    assert value.sha256() == hashlib.sha256(encoded).hexdigest()
    decoded = api().parse_native_plan_bytes(encoded)
    assert decoded == value
    assert decoded.hypothesis_genes == ("Sox2", "β-actin")
    assert decoded.panel_genes == ("Other", "β-actin", "Sox2")
    assert decoded.sample_ids == ("sample-3", "sample-1", "sample-2")
    assert decoded.temporal_columns == (2, 4)
    assert b'"deseq2_fit_type":null' in encoded
    source = tmp_path / "native-plan.json"
    source.write_bytes(encoded)
    assert api().load_native_plan(source) == value


@pytest.mark.parametrize("field", ["hypothesis_genes", "panel_genes", "sample_ids", "temporal_columns"])
def test_native_plan_collections_are_immutable_and_python_lists_are_rejected(field):
    value = plan()
    assert isinstance(getattr(value, field), tuple)
    with pytest.raises(TypeError):
        getattr(value, field)[0] = "replacement"
    with pytest.raises(ValidationError):
        plan(**{field: list(getattr(value, field))})
    with pytest.raises(ValidationError):
        value.revision = 2


@pytest.mark.parametrize("fit_type", ["parametric", "mean"])
def test_deseq2_requires_explicit_supported_fit_type(fit_type):
    value = plan(engine="deseq2_lrt", deseq2_fit_type=fit_type)
    assert api().parse_native_plan_bytes(value.canonical_bytes()) == value


@pytest.mark.parametrize("updates", [
    {"engine": "deseq2_lrt"},
    {"engine": "deseq2_lrt", "deseq2_fit_type": None},
    {"engine": "deseq2_lrt", "deseq2_fit_type": "local"},
    {"engine": "deseq2_lrt", "deseq2_fit_type": "glmGamPoi"},
    {"engine": "edger_qlf", "deseq2_fit_type": "mean"},
    {"engine": "edger_qlf", "deseq2_fit_type": "parametric"},
    {"engine": "edger_qlf:4.10.5"},
    {"engine": "DESeq2"},
    {"normalization": "tmm"},
    {"normalization": "none"},
])
def test_engine_configuration_cannot_be_implicit_or_cross_engine(updates):
    with pytest.raises(ValidationError):
        plan(**updates)


@pytest.mark.parametrize("updates", [
    {"extra": "ignored"}, {"formula": "1 + malicious()"}, {"command": "Rscript arbitrary.R"},
    {"callback": "arbitrary.module:run"}, {"counts_path": "/tmp/other-counts.tsv"},
    {"runtime_image": "r-base:latest"}, {"selection_criterion": "bic"},
    {"engine_version": "999"}, {"r_version": "4.0.0"},
    {"clinical_release_allowed": True},
    {"schema_version": "controlled_native_analysis/2"},
    {"plan_id": ""}, {"plan_id": " "}, {"study_id": "\t"},
    {"revision": 0}, {"revision": True}, {"revision": "1"},
    {"intended_use_ref": " "}, {"intended_use_ref": "x" * 2049},
    {"time_unit": ""}, {"time_unit": "\n"}, {"time_unit": 7},
    {"basis_definition_ref": " "}, {"basis_definition_ref": "x" * 2049},
    {"data_classification": "clinical"}, {"data_classification": None},
    {"platform": "linux/arm64"}, {"platform": "darwin/amd64"},
    {"runtime_image_id": "r-base:4.6.1"}, {"runtime_image_id": "sha256:" + "A" * 64},
    {"runtime_image_id": "sha256:" + "3" * 63},
    {"runtime_image_id": "sha256:" + "3" * 64 + "\n"},
    {"runtime_image_id": "registry.example/image@sha256:" + "3" * 64},
    {"max_memory_mb": 127}, {"max_memory_mb": 65537}, {"max_memory_mb": "256"},
    {"max_memory_mb": 256.0}, {"max_memory_mb": True},
    {"timeout_sec": 0}, {"timeout_sec": 86401}, {"timeout_sec": False},
    {"timeout_sec": float("nan")}, {"timeout_sec": float("inf")},
    {"max_cpus": 0}, {"max_cpus": 9}, {"max_cpus": 1.0}, {"max_cpus": True},
])
def test_native_plan_rejects_unlocked_or_ambiguous_fields(updates):
    with pytest.raises(ValidationError):
        plan(**updates)


@pytest.mark.parametrize("field", [
    "plan_id", "revision", "intended_use_ref", "study_id", "data_classification", "engine",
    "normalization", "hypothesis_genes", "panel_genes", "sample_ids", "temporal_columns",
    "time_unit", "basis_definition_ref", "counts_sha256", "metadata_sha256",
    "full_design_sha256", "reduced_design_sha256", "driver_sha256", "package_lock_sha256",
    "implementation_sha256", "environment_sha256", "runtime_image_id", "platform",
    "max_memory_mb", "timeout_sec", "max_cpus",
])
def test_native_plan_requires_every_locked_input(field):
    values = plan_values()
    del values[field]
    with pytest.raises(ValidationError):
        api().NativeAnalysisPlan(**values)


@pytest.mark.parametrize("updates", [
    {"hypothesis_genes": ()}, {"hypothesis_genes": ("Sox2", "Sox2")},
    {"hypothesis_genes": ("Sox2", "missing")}, {"hypothesis_genes": (" ",)},
    {"hypothesis_genes": tuple(f"gene-{i}" for i in range(101))},
    {"panel_genes": ()}, {"panel_genes": ("Sox2",)},
    {"panel_genes": ("Other", "Sox2", "β-actin", "Sox2")},
    {"panel_genes": ("Sox2", "β-actin", " ")},
    {"panel_genes": tuple(f"gene-{i}" for i in range(5001))},
    {"sample_ids": ("sample-1", "sample-2")},
    {"sample_ids": ("sample-1", "sample-2", "sample-2")},
    {"sample_ids": ("sample-1", "sample-2", " ")},
    {"sample_ids": tuple(f"sample-{i}" for i in range(513))},
    {"temporal_columns": ()}, {"temporal_columns": (1,)},
    {"temporal_columns": (2, 2)}, {"temporal_columns": (4, 2)},
    {"temporal_columns": (2, 3, 4, 5, 6, 7)},
    {"temporal_columns": (2.0,)}, {"temporal_columns": ("2",)},
    {"temporal_columns": (True,)},
])
def test_native_plan_rejects_invalid_families_samples_and_temporal_columns(updates):
    with pytest.raises(ValidationError):
        plan(**updates)


@pytest.mark.parametrize("field", [
    "counts_sha256", "metadata_sha256", "full_design_sha256", "reduced_design_sha256",
    "driver_sha256", "package_lock_sha256", "implementation_sha256", "environment_sha256",
])
@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n", 1])
def test_native_plan_requires_exact_lowercase_sha256_digests(field, value):
    with pytest.raises(ValidationError):
        plan(**{field: value})


@pytest.mark.parametrize("classification", ["synthetic", "public_processed", "restricted_clinical"])
def test_classification_declaration_does_not_confer_release_authority(classification):
    value = plan(data_classification=classification)
    assert "clinical_release_allowed" not in value.model_dump()
    assert "approved" not in value.model_dump()


def test_documented_collection_and_resource_bounds_are_inclusive():
    genes = tuple(f"gene-{i}" for i in range(5000))
    value = plan(hypothesis_genes=genes[:100], panel_genes=genes,
                 sample_ids=tuple(f"sample-{i}" for i in range(512)),
                 temporal_columns=(2, 3, 4, 5, 6), max_memory_mb=65536,
                 timeout_sec=86400, max_cpus=8)
    assert len(value.panel_genes) == 5000
    assert len(value.hypothesis_genes) == 100
    assert len(value.sample_ids) == 512
    assert api().parse_native_plan_bytes(value.canonical_bytes()) == value
    assert plan(temporal_columns=(2,), max_memory_mb=128, timeout_sec=1).timeout_sec == 1


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"a":NaN}',
    b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}', b'\xff',
    b'\xef\xbb\xbf{}', b'{"a":"\\ud800"}', b'[]', b'null', b'1', b'{',
])
def test_native_json_parser_rejects_malformed_duplicate_or_nonfinite_values(raw):
    with pytest.raises((ValueError, UnicodeError)):
        api().parse_native_plan_bytes(raw)


def test_native_json_parser_rejects_a_duplicate_even_when_last_value_is_valid():
    raw = plan().canonical_bytes().replace(b'"revision":1', b'"revision":99,"revision":1')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        api().parse_native_plan_bytes(raw)


def test_native_plan_load_rejects_relative_oversized_symlink_and_nonregular_files(tmp_path):
    module = api()
    with pytest.raises(ValueError, match="absolute"):
        module.load_native_plan("native-plan.json")
    with pytest.raises(TypeError, match="bytes"):
        module.parse_native_plan_bytes("{}")
    raw = b" " * (1024 * 1024 + 1)
    with pytest.raises(ValueError, match="limit|1 MB"):
        module.parse_native_plan_bytes(raw)
    source = tmp_path / "large.json"
    source.write_bytes(raw)
    with pytest.raises(ValueError, match="limit|1 MB"):
        module.load_native_plan(source)
    source.write_bytes(plan().canonical_bytes())
    link = tmp_path / "symlink.json"
    link.symlink_to(source)
    with pytest.raises((OSError, ValueError)):
        module.load_native_plan(link)
    with pytest.raises((OSError, ValueError)):
        module.load_native_plan(tmp_path)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError, match="regular file"):
        module.load_native_plan(fifo)


@pytest.mark.parametrize("updates", [
    {"schema_version": "controlled_analysis/1"}, {"engine": "arbitrary"},
    {"engine": "deseq2_lrt"}, {"deseq2_fit_type": "mean"},
    {"hypothesis_genes": ["Sox2"]}, {"sample_ids": ("s1", "s1", "s2")},
    {"counts_sha256": "invalid"}, {"temporal_columns": (4, 2)},
    {"max_cpus": True}, {"normalization": "none"}, {"extra": True},
])
def test_unvalidated_model_copy_is_rejected_before_hashing_or_validation(updates):
    original = plan()
    tampered = original.model_copy(update=updates)
    with pytest.raises(ValidationError):
        api().NativeAnalysisPlan.model_validate(tampered)
    with pytest.raises(ValidationError):
        tampered.canonical_bytes()
    with pytest.raises(ValidationError):
        tampered.sha256()


def test_unvalidated_model_construct_is_rejected_before_hashing():
    tampered = api().NativeAnalysisPlan.model_construct(**plan_values(max_cpus=True))
    with pytest.raises(ValidationError):
        tampered.sha256()


def test_locked_order_is_part_of_the_signed_plan_digest():
    original = plan()
    assert original.sha256() != plan(hypothesis_genes=("β-actin", "Sox2")).sha256()
    assert original.sha256() != plan(panel_genes=("Sox2", "β-actin", "Other")).sha256()
    assert original.sha256() != plan(sample_ids=("sample-1", "sample-2", "sample-3")).sha256()
