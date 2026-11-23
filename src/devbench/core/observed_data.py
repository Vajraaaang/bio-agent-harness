"""Observed-data records shared by scientific fitters and input adapters."""

from dataclasses import dataclass, field
from typing import Any


# Complete bulk families have a separate ceiling from synthetic datasets and
# the byte/runtime budgets of a fit.
PUBLIC_MAX_BULK_FEATURES = 100_000


@dataclass(frozen=True)
class ObservedData:
    expression: Any  # ndarray, imported only after explicit execution permission
    gene_ids: tuple[str, ...]
    sample_ids: tuple[str, ...]
    metadata: Any  # DataFrame indexed by sample ID
    annotations: dict[str, Any]
    measurement: str
    unit_hierarchy: dict[str, Any]
    targets: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)
