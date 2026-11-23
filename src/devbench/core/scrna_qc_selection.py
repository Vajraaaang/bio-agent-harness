"""Ordered cell selection tied to an accepted single-cell QC receipt."""

from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated

from pydantic import Field

from devbench.adaptive.models import AdaptiveRecord
from rna_kernels.canonical import canonical_json


class QCSelectionBinding(AdaptiveRecord):
    lineage_id: str
    step_id: str
    generation: Annotated[int, Field(gt=0, strict=True)]
    receipt_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    metrics_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


@dataclass(frozen=True, slots=True)
class AcceptedQCSelection:
    binding: QCSelectionBinding
    cell_ids: tuple[str, ...]
    passed: tuple[bool, ...]

    def mask_sha256(self) -> str:
        document = [list(self.cell_ids), list(self.passed)]
        return sha256(canonical_json(document)).hexdigest()

    def align(
        self, source_sha256: str, cell_ids: Iterable[str]
    ) -> tuple[bool, ...]:
        if source_sha256 != self.binding.source_sha256:
            raise ValueError("QC selection belongs to another original count source")
        if self.cell_ids != tuple(cell_ids) or len(self.passed) != len(self.cell_ids):
            raise ValueError("QC selection must match the complete ordered cell axis")
        if not any(self.passed):
            raise ValueError("No cells pass the accepted QC selection")
        return self.passed
