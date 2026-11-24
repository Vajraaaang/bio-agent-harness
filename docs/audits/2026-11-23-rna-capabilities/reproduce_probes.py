"""Reproduce the small synthetic stress tests in the 2026-11-23 capability audit."""

import hashlib
import json
import tempfile
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import binomtest

from devbench.analysis.rna_input_contract import validate_bulk_rna_input
from devbench.core.bulk_dtu import _usage_test
from devbench.core.spatial_analysis import (
    SpatialPatternSpec,
    build_spatial_graph,
    fit_spatial_pattern,
)
from devbench.platform.plugin_mcp import _verify_h5_counts
from devharness_server.tools_network import _enrichment_rows, _validate_enrichment
from rna_kernels.scrna_nb_countsplit import split_count_block


def usage_error():
    design = np.column_stack((np.ones(12), np.repeat([0, 1], 6)))
    results = []
    for multiplier in (1, 100):
        rng = np.random.default_rng(337)
        depths = np.full(12, 100)
        depths[[0, 6]] *= multiplier
        rejected = 0
        evaluated = 0
        for _ in range(250):
            probabilities = rng.beta(25, 25, size=12)
            successes = rng.binomial(depths, probabilities)
            p_value = _usage_test(successes, depths, design)["tx_p"]
            if p_value is not None:
                evaluated += 1
                rejected += p_value < 0.05
        interval = binomtest(rejected, evaluated).proportion_ci(method="exact")
        results.append(
            {
                "depth_multiplier": multiplier,
                "generated": 250,
                "estimable": evaluated,
                "rejected": int(rejected),
                "rate": rejected / evaluated,
                "exact_ci95": [interval.low, interval.high],
            }
        )
    return results


def split_dependence():
    original = np.random.default_rng(20261123).negative_binomial(
        4, 0.25, size=(60_000, 1)
    )
    counts = sparse.csr_matrix(original)
    results = []
    for theta in (1, 4, 16):
        discovery, testing = split_count_block(
            counts, np.array([theta]), 0.4, np.random.default_rng(618)
        )
        correlation = np.corrcoef(
            discovery.toarray().ravel(), testing.toarray().ravel()
        )[0, 1]
        results.append(
            {
                "true_theta": 4,
                "supplied_theta": theta,
                "correlation": float(correlation),
                "counts_conserved": (discovery + testing != counts).nnz == 0,
            }
        )
    return results


def enrichment_background():
    selected = ["G0", "G1", "G2"]
    results = []
    for size in (8, 1000):
        background = [f"G{i}" for i in range(size)]
        _validate_enrichment(selected, background, 1)
        (row,) = _enrichment_rows(
            set(selected), set(background), {"set": set(selected)}, "set"
        )
        results.append(
            {key: row[key] for key in ("background_size", "p_value", "q_value")}
        )
    return results


def integer_valued_log_input(workspace):
    transformed = np.log2(1 + np.array([[1, 3, 7, 15], [15, 7, 3, 1]]))
    with h5py.File(
        workspace / "in-memory.h5", "w", driver="core", backing_store=False
    ) as handle:
        dataset = handle.create_dataset("counts", data=transformed)
        hdf5_accepted = _verify_h5_counts(dataset, transformed.shape, None)
    samples = [f"sample-{i}" for i in range(4)]
    result = validate_bulk_rna_input(
        {
            "format": "devbench.bulk-rna-input/1",
            "representation": "raw_counts",
            "axes": ["gene", "sample"],
            "provenance": {"source": "audit-declaration"},
            "biological_independence": None,
        },
        gene_ids=["g1", "g2"],
        sample_ids=samples,
        metadata_rows=[{"sample_id": label} for label in samples],
        matrix=transformed,
    )
    return {
        "hdf5_accepted": hdf5_accepted,
        "bulk_count_values_valid": result["count_values_valid"],
    }


def spatial_depth(workspace):
    rng = np.random.default_rng(1776)
    grid = np.indices((12, 12)).reshape(2, -1).T
    x, y = np.tile(grid, (2, 1)).T
    depths = np.where(x < 6, 48, 4800)
    counts = np.stack([rng.multinomial(depth, np.full(32, 1 / 32)) for depth in depths])
    ids = [f"spot-{i:03}" for i in range(len(counts))]
    matrix = pd.DataFrame(counts, columns=[f"g{i:02}" for i in range(32)])
    matrix.insert(0, "spot_id", ids)
    metadata = pd.DataFrame(
        {
            "spot_id": ids,
            "x": x,
            "y": y,
            "donor_id": np.repeat(["d1", "d2"], 144),
            "section_id": "s1",
            "cell_type": "one_type",
        }
    )
    matrix_path, metadata_path = workspace / "counts.csv", workspace / "metadata.csv"
    matrix.to_csv(matrix_path, index=False)
    metadata.to_csv(metadata_path, index=False)
    spec = SpatialPatternSpec(
        counts_sha256=hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
        metadata_sha256=hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        coordinate_frame="synthetic-grid",
        coordinate_units="um",
        k=4,
        n_perm=199,
        seed=1777,
    )
    result = fit_spatial_pattern(matrix_path, metadata_path, spec)
    rows = result["gene_statistics"]
    return {
        "donors": 2,
        "spots": len(ids),
        "genes": len(rows),
        "depths": [48, 4800],
        "moran_q_below_0.05": sum(row["moran_q"] < 0.05 for row in rows),
        "geary_q_below_0.05": sum(row["geary_q"] < 0.05 for row in rows),
        "library_size_diagnostic": result["library_size_diagnostic"],
    }


def spatial_gap():
    coordinates = np.array([[0, 0], [1, 0], [100, 0], [101, 0]], dtype=float)
    graph = build_spatial_graph(coordinates, [("donor", "section")] * 4, k=2)
    left, right = sparse.triu(graph, k=1).nonzero()
    distances = np.linalg.norm(coordinates[left] - coordinates[right], axis=1)
    return {"edges": len(left), "cross_gap_edges": int((distances > 1).sum())}


def main():
    repo = Path(__file__).resolve().parents[3]
    scratch = repo / ".pytest-workspaces"
    scratch.mkdir(exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=scratch) as directory:
        panel = {
            "dtu": usage_error(),
            "count_split": split_dependence(),
            "standalone_enrichment": enrichment_background(),
            "integer_log": integer_valued_log_input(Path(directory)),
            "spatial_depth": spatial_depth(Path(directory)),
            "spatial_gap": spatial_gap(),
        }
    print(json.dumps(panel, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
