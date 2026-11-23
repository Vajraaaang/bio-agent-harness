"""Local projection onto a fitted exploratory lineage tree."""

from collections import defaultdict
from collections.abc import Mapping, Sequence

import numpy as np
from numpy.typing import NDArray


def project_junction_branches(
    coordinates: NDArray[np.float64],
    assignments: NDArray[np.int_],
    centers: NDArray[np.float64],
    edges: Sequence[tuple[int, int]],
    branches: Mapping[int, str],
) -> list[str]:
    """Resolve local arms within split clusters and retain ambiguous junctions."""
    children: dict[int, list[int]] = defaultdict(list)
    parents = {child: parent for parent, child in edges}
    for parent, child in edges:
        children[parent].append(child)

    labels = [branches[node] for node in assignments]
    for node, outgoing in children.items():
        if len(outgoing) < 2:
            continue
        neighbors = outgoing + ([parents[node]] if node in parents else [])
        members = np.flatnonzero(assignments == node)
        directions = centers[neighbors] - centers[node]
        squared_lengths = np.sum(directions**2, axis=1)
        valid = squared_lengths > 0
        if not valid.any():
            continue
        directions = directions[valid]
        neighbor_ids = np.asarray(neighbors)[valid]
        offsets = coordinates[members] - centers[node]
        fractions = np.clip(
            offsets @ directions.T / squared_lengths[valid], 0.0, 1.0
        )
        residuals = offsets[:, None, :] - fractions[:, :, None] * directions
        distances = np.sum(residuals**2, axis=2)
        best = distances.argmin(axis=1)
        minimum = distances[np.arange(len(members)), best]
        tied = np.isclose(distances, minimum[:, None], rtol=1e-10, atol=1e-12)
        for row, member in enumerate(members):
            if tied[row].sum() == 1 and fractions[row, best[row]] > 0:
                # The inbound edge inherits the branch of the junction itself.
                neighbor = neighbor_ids[best[row]]
                if neighbor != parents.get(node):
                    labels[member] = branches[neighbor]
    return labels
