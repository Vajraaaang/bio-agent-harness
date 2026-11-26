"""Bounded empirical evidence for the COUNT-1 source-clarification gate."""

from math import exp, factorial, fsum, sqrt

import numpy as np

SUSPECTED_TRANSFORM = "suspected_transformed_integer_counts"
CONFLICTING_PROVENANCE = "conflicting_count_provenance"
_MAX_LEVEL = 20
_MAX_LIBRARIES = 128
_BLOCK_ENTRIES = 65_536
_TRANSFORMS = frozenset(
    {
        "normalized_expression",
        "corrected_expression",
        "imputed_expression",
        "estimated_counts",
        "log",
        "ln",
        "log1p",
        "log2",
        "log2(1+x)",
        "log10",
        "log10(1+x)",
        "cpm",
        "tpm",
        "rpkm",
        "fpkm",
        "rlog",
        "vst",
        "sct",
        "sctransform",
        "zscore",
        "scaled",
    }
)


def declares_count_transform(representation, transformation) -> bool:
    """Interpret only processing history explicitly attached to the selected matrix."""
    return any(
        isinstance(label, str) and label.strip().casefold() in _TRANSFORMS
        for label in (representation, transformation)
    )


class CountDistribution:
    """Summarize validated integer blocks without materializing the logical matrix.

    Sparse callers supply its full logical size and the actual library coordinates
    of stored entries (or a scalar for one library). Omitted entries count as zero.
    Dense blocks follow storage order. Library totals cover up to 128 evenly spaced
    libraries; gene-by-sample tables of at most 128 samples are covered completely.
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
        if major_width < 1 or (
            total_values is not None
            and (total_values < 0 or total_values % major_width)
        ):
            raise ValueError("Invalid logical count matrix dimensions")
        library_count = major_width
        if libraries_in_rows:
            library_count = (
                _MAX_LIBRARIES if total_values is None else total_values // major_width
            )
        self._library_count = library_count
        self._stride = max(1, (library_count + _MAX_LIBRARIES - 1) // _MAX_LIBRARIES)
        self._depths = np.zeros((library_count + self._stride - 1) // self._stride)
        self._bins = np.zeros(_MAX_LEVEL, dtype=np.int64)
        self._seen = 0
        self._nonzeros = 0
        self._unindexed = False
        self._max = 0
        self._min_nonzero: int | None = None

    def observe(self, block, *, library_indices=None) -> None:
        """Add bounded slices, optionally indexed by sparse library coordinates."""
        values = np.asarray(block).reshape(-1)
        if library_indices is not None:
            library_indices = self._coordinates(library_indices, values.size)
        elif values.size:
            self._unindexed = True
        for start in range(0, values.size, _BLOCK_ENTRIES):
            part = values[start : start + _BLOCK_ENTRIES]
            indices = library_indices
            if isinstance(indices, np.ndarray):
                indices = indices[start : start + part.size]
            self._observe_slice(part, indices)
            self._seen += part.size

    def _coordinates(self, supplied, size):
        ids = np.asarray(supplied)
        if (
            self.total_values is None
            or ids.dtype.kind not in "iu"
            or (ids.size and (ids.min() < 0 or ids.max() >= self._library_count))
            or (ids.ndim and ids.size != size)
        ):
            raise ValueError("Invalid sparse library coordinates or logical size")
        return int(ids) if ids.ndim == 0 else ids.reshape(-1)

    def _observe_slice(self, values, indices):
        nonzeros = int(np.count_nonzero(values))
        self._nonzeros += nonzeros
        if nonzeros == 0:
            return
        positive = values if nonzeros == values.size else values[values != 0]
        minimum = int(positive.min())
        self._min_nonzero = (
            minimum if self._min_nonzero is None else min(self._min_nonzero, minimum)
        )
        maximum = int(positive.max())
        self._max = max(self._max, maximum)
        small = positive if maximum <= _MAX_LEVEL else positive[positive <= _MAX_LEVEL]
        self._bins += np.bincount(
            small.astype(np.intp, copy=False), minlength=_MAX_LEVEL + 1
        )[1:]
        if self._max < 2**53:
            self._add_depths(values, indices)

    def _add_depths(self, values, indices):
        width, stride = self.major_width, self._stride
        if indices is None and self.libraries_in_rows:
            first = (self._seen // width + stride - 1) // stride
            end = self._seen + values.size
            last = min((end - 1) // width // stride + 1, self._depths.size)
            for sample in range(first, last):
                start = sample * stride * width - self._seen
                self._depths[sample] += values[max(0, start) : start + width].sum(
                    dtype=float
                )
            return
        if indices is None and self._seen % width == 0 and values.size % width == 0:
            self._depths += values.reshape(-1, width)[:, ::stride].sum(
                axis=0, dtype=float
            )
            return
        if indices is None:
            indices = (np.arange(values.size) + self._seen) % width
        if np.isscalar(indices):
            library = int(indices)
            if library % stride == 0 and library // stride < self._depths.size:
                self._depths[library // stride] += values.sum(dtype=float)
            return
        keep = (indices % stride) == 0
        slots = (indices[keep] // stride).astype(np.intp, copy=False)
        self._depths += np.bincount(
            slots,
            weights=values[keep].astype(float, copy=False),
            minlength=self._depths.size,
        )

    def _library_depths(self, size):
        if self._unindexed and self._seen != size:
            # Storage offsets in an unindexed sparse stream are not coordinates.
            return self._depths[:0]
        if self.libraries_in_rows and self.total_values is None:
            return self._depths[: size // self.major_width]
        return self._depths

    def _normalized_depths(self, depths) -> bool:
        if depths.size < 4 or not 100 <= self._max < 2**53:
            return False
        median = float(np.median(depths))
        if median < 100_000 and abs(median - 10_000) > 200:
            return False
        # Rounding a fixed-sum matrix leaves much less between-library scatter
        # than ordinary counting noise, even at naturally large library sizes.
        mean = float(depths.mean())
        return float(depths.std()) < min(0.001 * mean, 0.25 * sqrt(mean))

    def _compressed_counts(self, size, depths) -> bool:
        if self._max > 16 or self._nonzeros * 10 < 7 * size:
            return False
        bins = self._bins[: self._max]
        levels = bins[bins > 0]
        if levels.size < 3:
            return False
        flat = (
            levels.size >= 4
            and levels.size == self._max
            and levels.min() >= 2
            and int(levels.max()) * 10 <= int(levels.min()) * 11
        )
        if flat and (
            levels.min() >= 16
            or (size <= 128 and depths.size >= 2 and depths.min() == depths.max())
        ):
            return True
        if self._nonzeros < 64 or (self._min_nonzero == 1 and levels.size > 12):
            return False
        values = np.arange(1, self._max + 1, dtype=float)
        mean = float(values @ bins) / size
        variance = float(values**2 @ bins) / size - mean**2
        # A short range alone is common at low read depth. Require reduced
        # variance and an upper cutoff with several absent Poisson-tail entries.
        if variance >= 0.82 * mean:
            return False
        expected_zeros = size * exp(-mean)
        expected = [
            expected_zeros * mean**k / factorial(k) for k in range(self._max + 1)
        ]
        missing_tail = size - fsum(expected)
        zeros = size - self._nonzeros
        if missing_tail < 4 or (
            missing_tail < 8
            and zeros >= 0.8 * expected_zeros
            and variance >= 0.6 * mean
        ):
            return False
        # A moderately short Poisson sample can have the same cutoff and mean.
        # Use all bins, including zeros and the omitted tail. This discrepancy
        # corroborates clarification; it does not assign a transformation p-value.
        discrepancy = missing_tail + fsum(
            (actual - target) ** 2 / target
            for actual, target in zip((zeros, *bins), expected, strict=True)
        )
        return bool(discrepancy >= self._max + 2 * sqrt(2 * self._max))

    @property
    def suspected_transform(self) -> bool:
        size = self._seen if self.total_values is None else self.total_values
        depths = self._library_depths(size)
        return self._normalized_depths(depths) or self._compressed_counts(size, depths)
