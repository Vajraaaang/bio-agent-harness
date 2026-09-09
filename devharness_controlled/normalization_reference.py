"""Prespecified research normalization references; clinical qualification is not established."""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from .contracts import FrozenContract, NonemptyText, parse_json_bytes


GeneIdentifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]


def _validated_gene_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    """Require one bounded immutable collection without changing identifier identity."""
    if type(value) is not tuple:
        raise ValueError("Gene identifiers must be an immutable tuple")
    if not 1 <= len(value) <= 5000:
        raise ValueError("Gene identifiers must contain 1 to 5000 entries")
    for identifier in value:
        if type(identifier) is not str:
            raise ValueError("Gene identifiers must be strings")
        if not identifier or identifier != identifier.strip():
            raise ValueError("Gene identifiers must be trimmed and nonblank")
        if any(ord(char) < 32 or ord(char) == 127 for char in identifier):
            raise ValueError("Gene identifiers must not contain ASCII controls or DEL")
        if len(identifier.encode("utf-8", errors="strict")) > 256:
            raise ValueError("Gene identifiers must not exceed 256 UTF-8 bytes")
    if len(value) != len(set(value)):
        raise ValueError("Gene identifiers must be unique")
    return value


class NormalizationReference(FrozenContract):
    """An explicit study-bound reference set with no inferred biological qualification."""

    schema_version: Literal["normalization_reference/1"]
    reference_id: NonemptyText
    study_id: NonemptyText
    gene_ids: Annotated[tuple[GeneIdentifier, ...], Field(min_length=1, max_length=5000)]
    annotation_reference: NonemptyText
    selection_basis: NonemptyText
    eligibility_policy: Literal["all_positive"]
    qualification_status: Literal["not_established"]

    @field_validator("gene_ids")
    @classmethod
    def valid_gene_ids(cls, value):
        return _validated_gene_ids(value)


def parse_normalization_reference_bytes(raw: bytes) -> NormalizationReference:
    """Check strict bounded JSON before accepting JSON arrays as immutable tuples."""
    parse_json_bytes(raw)
    return NormalizationReference.model_validate_json(raw)


def reference_genes_for_panel(
    reference: NormalizationReference, *, study_id: str, panel_genes: tuple[str, ...]
) -> tuple[str, ...]:
    """Revalidate study and panel binding, returning references in signed panel order."""
    reference = NormalizationReference.model_validate(reference)
    if type(study_id) is not str or reference.study_id != study_id:
        raise ValueError("Normalization reference study must exactly match the plan study")
    panel_genes = _validated_gene_ids(panel_genes)
    reference_genes = set(reference.gene_ids)
    if not reference_genes.issubset(panel_genes):
        raise ValueError("The signed panel must include every normalization reference gene")
    return tuple(gene for gene in panel_genes if gene in reference_genes)
