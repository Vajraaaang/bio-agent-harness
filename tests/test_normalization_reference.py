"""Synthetic contract checks; these references carry no clinical qualification."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from devharness_controlled.contracts import FrozenContract, JSON_LIMIT_BYTES


def api():
    source = Path(__file__).resolve().parents[1] / "devharness_controlled" / "normalization_reference.py"
    assert source.is_file(), "Normalization reference contract implementation is missing"
    return importlib.import_module("devharness_controlled.normalization_reference")


def reference_values(**updates):
    values = {
        "schema_version": "normalization_reference/1",
        "reference_id": "synthetic-reference-v1",
        "study_id": "synthetic-study",
        "gene_ids": ("refB", "β-actin", "refA"),
        "annotation_reference": "synthetic-identifiers-v1",
        "selection_basis": "Prespecified synthetic references; stability not established",
        "eligibility_policy": "all_positive",
        "qualification_status": "not_established",
    }
    return {**values, **updates}


def reference(**updates):
    return api().NormalizationReference(**reference_values(**updates))


def test_reference_roundtrips_exact_canonical_utf8_and_declared_order():
    value = reference()
    assert isinstance(value, FrozenContract)
    encoded = value.canonical_bytes()
    expected = json.dumps(reference_values(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert encoded == expected
    assert "β-actin".encode("utf-8") in encoded
    assert value.sha256() == hashlib.sha256(encoded).hexdigest()
    parsed = api().parse_normalization_reference_bytes(encoded)
    assert parsed == value
    assert parsed.gene_ids == ("refB", "β-actin", "refA")
    assert type(parsed.gene_ids) is tuple


@pytest.mark.parametrize("field", tuple(reference_values()))
def test_every_reference_field_is_required_without_defaults(field):
    values = reference_values()
    del values[field]
    with pytest.raises(ValidationError):
        api().NormalizationReference(**values)


def test_reference_and_gene_ids_are_immutable():
    value = reference()
    with pytest.raises(ValidationError):
        value.reference_id = "replacement"
    with pytest.raises(TypeError):
        value.gene_ids[0] = "replacement"


@pytest.mark.parametrize("gene_ids", [
    ["refA"], {"refA"}, {"refA": True}, "refA", b"refA", None, (1,), (True,), (b"refA",),
])
def test_reference_rejects_mutable_or_coercible_python_gene_inputs(gene_ids):
    with pytest.raises(ValidationError):
        reference(gene_ids=gene_ids)


@pytest.mark.parametrize("updates", [
    {"extra": "ignored"}, {"clinical_release_allowed": True},
    {"qualification_status": "qualified"}, {"qualification_status": "clinically_validated"},
    {"qualification_status": True}, {"eligibility_policy": "drop_nonpositive"},
    {"eligibility_policy": "full_panel_fallback"}, {"schema_version": "normalization_reference/2"},
    {"gene_ids": ()}, {"gene_ids": ("refA", "refA")},
    {"gene_ids": tuple(f"ref{i}" for i in range(5001))},
])
def test_reference_rejects_undeclared_scope_or_invalid_collection(updates):
    with pytest.raises(ValidationError):
        reference(**updates)


@pytest.mark.parametrize("field", ["reference_id", "study_id", "annotation_reference", "selection_basis"])
@pytest.mark.parametrize("invalid", ["", " \t\n", "x" * 2049, 7, True, None])
def test_reference_requires_explicit_nonblank_strict_text(field, invalid):
    with pytest.raises(ValidationError):
        reference(**{field: invalid})


@pytest.mark.parametrize("identifier", [
    "", " ", " refA", "refA ", "\u00a0refA", "refA\u2003",
    "x" * 257, "β" * 129, "\ud800",
    *(f"ref{chr(code)}A" for code in (*range(32), 127)),
])
def test_reference_rejects_invalid_identifier_bytes_and_whitespace(identifier):
    with pytest.raises(ValidationError):
        reference(gene_ids=(identifier,))


@pytest.mark.parametrize("gene_ids", [
    ("x" * 256,), ("β" * 128,), ("ref A", "ref-a.1", "ß", "SS", "é", "e\u0301"),
    tuple(f"ref{i}" for i in range(5000)),
])
def test_reference_accepts_boundary_sizes_and_preserves_unicode_identity(gene_ids):
    value = reference(gene_ids=gene_ids)
    assert value.gene_ids == gene_ids
    assert api().parse_normalization_reference_bytes(value.canonical_bytes()).gene_ids == gene_ids


@pytest.mark.parametrize("raw", [
    b'{"reference_id":"one","reference_id":"two"}',
    b'{"metadata":{"nested":1,"nested":2}}',
    b'{"reference_id":NaN}', b'{"reference_id":Infinity}',
    b'{"reference_id":-Infinity}', b'{"reference_id":1e999}',
    b'\xff', b'\xef\xbb\xbf{}', b'{"reference_id":"\\ud800"}',
])
def test_parser_rejects_duplicates_nonfinite_and_invalid_utf8_before_model_validation(raw):
    with pytest.raises(ValueError) as caught:
        api().parse_normalization_reference_bytes(raw)
    assert not isinstance(caught.value, ValidationError)


@pytest.mark.parametrize("raw", ["{}", bytearray(b"{}"), memoryview(b"{}")])
def test_parser_requires_actual_bytes(raw):
    with pytest.raises(TypeError, match="bytes"):
        api().parse_normalization_reference_bytes(raw)


def test_parser_enforces_existing_one_mib_boundary():
    encoded = reference().canonical_bytes()
    at_limit = encoded + b" " * (JSON_LIMIT_BYTES - len(encoded))
    assert api().parse_normalization_reference_bytes(at_limit) == reference()
    with pytest.raises(ValueError, match="1 MB|limit"):
        api().parse_normalization_reference_bytes(at_limit + b" ")


@pytest.mark.parametrize("raw", [b"null", b"[]", b'"refA"', b"7", b"{}"])
def test_parser_requires_a_complete_reference_object(raw):
    with pytest.raises(ValidationError):
        api().parse_normalization_reference_bytes(raw)


@pytest.mark.parametrize("updates", [
    {"reference_id": "synthetic-reference-v2"}, {"study_id": "another-synthetic-study"},
    {"gene_ids": ("refA", "β-actin", "refB")}, {"gene_ids": ("refB", "refA")},
    {"annotation_reference": "synthetic-identifiers-v2"},
    {"selection_basis": "Another prespecified synthetic justification"},
])
def test_canonical_digest_binds_every_variable_reference_field(updates):
    assert reference(**updates).sha256() != reference().sha256()


def test_panel_mapping_returns_panel_order_without_changing_canonical_reference():
    value = reference()
    before = value.canonical_bytes()
    result = api().reference_genes_for_panel(
        value, study_id="synthetic-study", panel_genes=("other", "refA", "refB", "β-actin"))
    assert result == ("refA", "refB", "β-actin")
    assert type(result) is tuple
    assert value.gene_ids == ("refB", "β-actin", "refA")
    assert value.canonical_bytes() == before


def test_panel_mapping_allows_reference_to_equal_the_entire_panel():
    value = reference(gene_ids=("refB", "refA"))
    assert api().reference_genes_for_panel(
        value, study_id="synthetic-study", panel_genes=("refA", "refB")) == ("refA", "refB")


@pytest.mark.parametrize("study_id", ["other-study", "Synthetic-study", "synthetic-study ", "", None, 1])
def test_panel_mapping_requires_exact_study_binding(study_id):
    with pytest.raises(ValueError, match="study"):
        api().reference_genes_for_panel(reference(), study_id=study_id, panel_genes=reference().gene_ids)


@pytest.mark.parametrize("panel_genes", [
    (), ("other",), ("refA", "refB"), ("refA", "refB", "β-Actin"),
])
def test_panel_mapping_rejects_empty_or_incomplete_panels(panel_genes):
    with pytest.raises(ValueError):
        api().reference_genes_for_panel(reference(), study_id="synthetic-study", panel_genes=panel_genes)


@pytest.mark.parametrize("panel_genes", [
    ["refA"], {"refA"}, {"refA": True}, "refA", b"refA", None,
    ("refA", "refA"), ("refA", ""), ("refA", " bad"), ("refA", "bad "),
    ("refA", "bad\x00"), ("refA", "bad\x7f"), ("refA", "β" * 129),
    ("refA", 7), ("refA", True), ("refA", b"other"), ("refA", "\ud800"),
    tuple(["refA", *(f"other{i}" for i in range(5000))]),
])
def test_panel_mapping_requires_a_valid_immutable_unique_bounded_panel(panel_genes):
    with pytest.raises((ValueError, TypeError)):
        api().reference_genes_for_panel(
            reference(gene_ids=("refA",)), study_id="synthetic-study", panel_genes=panel_genes)


@pytest.mark.parametrize("updates", [
    {"gene_ids": ("refA", "refA")}, {"gene_ids": ["refA"]}, {"gene_ids": "refA"},
    {"gene_ids": ()}, {"gene_ids": (" refA",)}, {"qualification_status": "qualified"},
    {"reference_id": " "}, {"schema_version": "normalization_reference/2"}, {"extra": True},
])
def test_panel_mapping_revalidates_unchecked_model_copies(updates):
    tampered = reference().model_copy(update=updates)
    with pytest.raises(ValidationError):
        api().reference_genes_for_panel(
            tampered, study_id="synthetic-study", panel_genes=("refA", "refB", "β-actin"))


def test_panel_mapping_revalidates_unchecked_constructed_instances():
    values = reference_values()
    del values["qualification_status"]
    incomplete = api().NormalizationReference.model_construct(**values)
    with pytest.raises(ValidationError):
        api().reference_genes_for_panel(
            incomplete, study_id="synthetic-study", panel_genes=("refA", "refB", "β-actin"))
