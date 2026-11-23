"""Shared explicit scientific display encodings; no analysis or threshold defaults."""
from __future__ import annotations

OKABE_ITO = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000")
SHAPES = ("o", "s", "^", "D", "p", "h", "*", "P")
PLOTLY_SHAPES = ("circle", "square", "triangle-up", "diamond", "pentagon", "hexagon", "star", "cross")
PALETTES = {"viridis", "magma", "coolwarm", "RdBu_r"}


def category(index):
    return OKABE_ITO[index % len(OKABE_ITO)], SHAPES[(index // len(OKABE_ITO)) % len(SHAPES)]


def continuous_domain(palette, low, high):
    if palette not in PALETTES:
        raise ValueError("unsupported_scientific_palette")
    if palette in {"coolwarm", "RdBu_r"}:
        bound = max(abs(low), abs(high))
        return -bound, bound
    return low, high
