"""Exact, bounded-memory median-of-ratios normalization over raw counts."""

from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO

import numpy as np

DEFAULT_RLE_SCRATCH_BYTES = 512 * 1024 * 1024
_BLOCK_ROWS = 128
_RANK_READ_KEYS = 16_384
_KEY_BYTES = 8
_SIGN_BIT = np.uint64(1 << 63)
_MISSING = np.uint64(2**64 - 1)


def _count_blocks(iter_blocks, sample_count):
    for block in iter_blocks():
        if (
            block.ndim != 2
            or block.shape[1] != sample_count
            or block.dtype.kind not in "iuf"
        ):
            raise ValueError(
                "RLE requires numeric blocks with the declared sample axis"
            )
        for start in range(0, block.shape[0], _BLOCK_ROWS):
            counts = block[start : start + _BLOCK_ROWS]
            if (
                not np.isfinite(counts).all()
                or (counts < 0).any()
                or (counts > 2**53 - 1).any()
                or (counts.dtype.kind == "f" and (counts != np.floor(counts)).any())
            ):
                raise ValueError(
                    "RLE requires finite nonnegative integer counts in the exact range"
                )
            yield counts


def _rank_key(spool: BinaryIO, start: int, total: int, rank: int) -> int:
    """Select an exact ordered key by counting one byte at a time."""
    prefix = 0
    for shift in range(56, -1, -8):
        histogram = np.zeros(256, dtype=np.int64)
        spool.seek(start)
        for offset in range(0, total, _RANK_READ_KEYS):
            size = min(_RANK_READ_KEYS, total - offset)
            keys = np.fromfile(spool, dtype=np.uint64, count=size)
            if keys.size != size:
                raise OSError("RLE scratch ended before the complete sample axis")
            if shift < 56:
                keys = keys[(keys >> (shift + 8)) == prefix]
            digits = ((keys >> shift) & 255).astype(np.intp)
            histogram += np.bincount(digits, minlength=256)
        cumulative = histogram.cumsum()
        digit = int(np.searchsorted(cumulative, rank, side="right"))
        if digit == 256:
            raise ValueError("RLE scratch does not contain the requested rank")
        if digit:
            rank -= int(cumulative[digit - 1])
        prefix = (prefix << 8) | digit
    return prefix


def _from_ordered_key(key: int) -> float:
    bits = key ^ int(_SIGN_BIT) if key & int(_SIGN_BIT) else key ^ int(_MISSING)
    return float(np.uint64(bits).view(np.float64))


def _write_ratios(spool, iter_blocks, sample_count, reference_count, poscounts):
    used = np.zeros(sample_count, dtype=np.int64)
    written = 0
    for block in _count_blocks(iter_blocks, sample_count):
        positive = block > 0
        selected = positive.any(axis=1) if poscounts else positive.all(axis=1)
        if not selected.any():
            continue
        counts = block[selected]
        if written + len(counts) > reference_count:
            raise ValueError("RLE reference changed between scans")
        usable = counts > 0
        logs = np.zeros(counts.shape, dtype=np.float64)
        np.log(counts, out=logs, where=usable, dtype=np.float64)
        logs -= logs.mean(axis=1, keepdims=True)
        # IEEE 754 bits are ordered after flipping the sign bit for positive
        # values or complementing every bit for negative values.
        bits = logs.view(np.uint64)
        ordered = np.where(np.signbit(logs), ~bits, bits ^ _SIGN_BIT)
        ordered[~usable] = _MISSING
        for sample in range(sample_count):
            spool.seek((sample * reference_count + written) * _KEY_BYTES)
            ordered[:, sample].tofile(spool)
        used += usable.sum(axis=0)
        written += len(counts)
    if written != reference_count:
        raise ValueError("RLE reference changed between scans")
    return used


def streaming_median_ratio_factors(
    iter_blocks: Callable[[], Iterable[np.ndarray]],
    sample_count: int,
    *,
    scratch_dir: Path | None = None,
    max_scratch_bytes: int = DEFAULT_RLE_SCRATCH_BYTES,
):
    """Compute exact RLE factors from repeatable, immutable gene × sample blocks.

    The first scan chooses ratio or poscounts on the complete feature axis. The
    second spools sortable log-ratios in sample-major order. Fixed-size radix
    scans select the middle order statistics without materializing a gene axis
    in memory. Working RAM is O(samples), excluding the caller's input blocks.
    Scratch is private, capped, and closed on completion or failure.
    """
    if type(sample_count) is not int or sample_count < 1:
        raise ValueError("RLE requires at least one sample")
    if type(max_scratch_bytes) is not int or max_scratch_bytes < 1:
        raise ValueError("RLE requires a positive scratch byte budget")
    complete = expressed = 0
    for counts in _count_blocks(iter_blocks, sample_count):
        positive = counts > 0
        complete += int(positive.all(axis=1).sum())
        expressed += int(positive.any(axis=1).sum())
    reference_count = complete or expressed
    if not reference_count:
        raise ValueError(
            "no_normalization_reference: The count family contains only zeros"
        )
    if sample_count * reference_count * _KEY_BYTES > max_scratch_bytes:
        raise ValueError("RLE reference exceeds its scratch byte budget")

    medians = np.empty(sample_count)
    with TemporaryFile(mode="w+b", buffering=0, dir=scratch_dir) as spool:
        usable_counts = _write_ratios(
            spool,
            iter_blocks,
            sample_count,
            reference_count,
            not complete,
        )
        if (usable_counts == 0).any():
            raise ValueError(
                "no_sample_normalization_reference: A sample has no positive "
                "reference counts"
            )
        for sample, usable in enumerate(usable_counts):
            start = sample * reference_count * _KEY_BYTES
            low = (int(usable) - 1) // 2
            high = int(usable) // 2
            lower = _from_ordered_key(_rank_key(spool, start, reference_count, low))
            upper = (
                lower
                if high == low
                else _from_ordered_key(
                    _rank_key(spool, start, reference_count, high),
                )
            )
            medians[sample] = (lower + upper) / 2.0
    factors = np.exp(medians - medians.mean())
    if not np.isfinite(factors).all() or (factors <= 0).any():
        raise ValueError(
            "invalid_size_factors: Normalization did not produce finite "
            "positive factors"
        )
    return factors, {
        "method": "median_of_ratios" if complete else "median_of_ratios_poscounts",
        "reference_gene_count": reference_count,
        "factor_centering": "geometric_mean_one",
        "scope": "complete_declared_gene_family",
        "assumption": "Most reference genes are stable or changes are balanced",
    }
