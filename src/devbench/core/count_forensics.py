"""Conservative distribution evidence for the COUNT-1 clarification gate."""

import numpy as np

SUSPECTED_TRANSFORM = "suspected_transformed_integer_counts"
CONFLICTING_PROVENANCE = "conflicting_count_provenance"
_SMALL_PLATEAU_SIZE = 128


def declares_count_transform(representation, transformation) -> bool:
    """Interpret only processing history explicitly attached to the selected matrix."""
    return representation in (
        "normalized_expression",
        "corrected_expression",
        "imputed_expression",
        "estimated_counts",
    ) or (
        isinstance(transformation, str)
        and transformation.casefold() in ("log1p", "log2", "log2(1+x)", "tpm", "cpm")
    )


class CountDistribution:
    """Inspect validated integer values in bounded blocks, in storage order.

    For sparse inputs, pass the full logical size and stream canonical data
    entries. Missing entries are zeros. For dense streams the final size may
    be unknown. A storage vector has `major_width` entries; in a gene-by-sample
    table its columns are the libraries, while a CSR H5AD has library rows.
    """

    def __init__(
        self,
        *,
        total_values: int | None = None,
        major_width: int,
        libraries_in_rows: bool = True,
    ):
        self.total_values = total_values
        self.major_width = major_width
        self.libraries_in_rows = libraries_in_rows
        self._seen = 0
        self._possible = True
        # Bins for 1..8 cover integer log2(1+x) levels through x=255.
        self._bins = [0] * 8
        self._depths: dict[int, int] = {}

    def observe(self, block) -> None:
        """Add one block; a wider positive tail rules out this plateau."""
        if not self._possible:
            return
        values = np.asarray(block).reshape(-1)
        if not values.size:
            return
        if values.max() > len(self._bins):
            self._possible = False
            return
        for level in range(1, len(self._bins) + 1):
            self._bins[level - 1] += int(np.count_nonzero(values == level))
        # With fewer than 16 observations per level, eight flat bins occupy
        # at most 128 entries. Track all library depths for that small case.
        # Positions are used only after establishing that no zeros were omitted.
        if self._seen + values.size <= _SMALL_PLATEAU_SIZE:
            for pos, value in enumerate(values, start=self._seen):
                library = (
                    pos // self.major_width
                    if self.libraries_in_rows
                    else pos % self.major_width
                )
                self._depths[library] = self._depths.get(library, 0) + int(value)
        self._seen += values.size

    @property
    def suspected_transform(self) -> bool:
        """Hold dense, repeated, truncated plateaus for source clarification."""
        if not self._possible:
            return False
        levels = [n for n in self._bins if n]
        # At least four consecutive levels starting at one, twice each. The
        # smallest supported pattern therefore needs eight nonzero values.
        if len(levels) < 4 or self._bins[: len(levels)] != levels:
            return False
        if min(levels) < 2 or max(levels) * 10 > min(levels) * 11:
            return False
        logical_size = self._seen if self.total_values is None else self.total_values
        nonzeros = sum(levels)
        if nonzeros * 100 < 95 * logical_size:
            return False
        # At least 16 observations per level make a flat cutoff with >=95%
        # occupancy visible on its own. For smaller inputs require independent
        # corroboration from equal depths and no explicit or implicit zeros.
        if min(levels) >= 16:
            return True
        if nonzeros != logical_size or logical_size > _SMALL_PLATEAU_SIZE:
            return False
        depths = self._depths.values()
        return len(depths) >= 2 and min(depths) == max(depths)
