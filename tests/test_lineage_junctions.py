"""Branch labels depend on local segments at a shared junction."""

import numpy as np

from rna_kernels.lineage_branches import project_junction_branches


def test_projection_retains_shared_junctions_and_resolves_outbound_arms():
    centers = np.array([[-2.0, 0.0], [0.0, 0.0], [2.0, 2.0], [2.0, -2.0]])
    coordinates = np.array(
        [[-0.5, 0], [0, 0], [0.5, 0], [0.4, 0.4], [0.4, -0.4], [1, 1]]
    )
    assignments = np.array([1, 1, 1, 1, 1, 2])
    branches = {0: "root", 1: "root", 2: "branch_2", 3: "branch_3"}
    edges = [(0, 1), (1, 2), (1, 3)]
    expected = ["root", "root", "root", "branch_2", "branch_3", "branch_2"]
    assert project_junction_branches(
        coordinates, assignments, centers, edges, branches
    ) == expected
    assert project_junction_branches(
        coordinates, assignments, centers, list(reversed(edges)), branches
    ) == expected
