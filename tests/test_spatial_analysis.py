import hashlib
from itertools import permutations, product

import numpy as np
import pandas as pd
import pytest


def _spatial_inputs(root, *, planted=True):
    from devbench.core.spatial_analysis import SpatialPatternSpec

    rng = np.random.default_rng(56)
    x, y = np.indices((8, 8)).reshape(2, -1)
    x, y = np.tile(x, 3), np.tile(y, 3)
    ids = [f"spot-{i}" for i in range(len(x))]
    counts = rng.integers(30, 65, len(x))
    if planted:
        counts += x * 120
    totals = 1500 + rng.integers(0, 60, len(x))
    cell_type = np.where(x < 4, "A", "B") if planted else rng.choice(["A", "B"], len(x))
    matrix = pd.DataFrame(
        {
            "spot_id": ids,
            "gradient": counts,
            "background": totals - counts,
            "constant": 0,
        }
    )
    metadata = pd.DataFrame(
        {
            "spot_id": ids,
            "x": x,
            "y": y,
            "donor_id": np.repeat(["d1", "d1", "d2"], 64),
            "section_id": np.repeat(["s1", "s2", "s1"], 64),
            "cell_type": cell_type,
        }
    )
    paths = [root / f"{name}.csv" for name in ("counts", "metadata")]
    for path, table in zip(paths, (matrix, metadata), strict=True):
        table.to_csv(path, index=False)
    return paths, SpatialPatternSpec(
        counts_sha256=hashlib.sha256(paths[0].read_bytes()).hexdigest(),
        metadata_sha256=hashlib.sha256(paths[1].read_bytes()).hexdigest(),
        coordinate_frame="registered-tissue",
        coordinate_units="um",
        k=4,
        n_perm=199,
        seed=82,
    )


def test_gradient_and_type_colocalization_recovered(tmp_path):
    from devbench.core.spatial_analysis import fit_spatial_pattern

    paths, spec = _spatial_inputs(tmp_path)
    result = fit_spatial_pattern(*paths, spec)
    genes = {row["gene_id"]: row for row in result["gene_statistics"]}
    signal = genes["gradient"]
    assert signal["moran_i"] > 0.7 and signal["geary_c"] < 0.3
    assert 0 < signal["moran_p"] <= signal["moran_q"] < 0.05
    assert 0 < signal["geary_p"] <= signal["geary_q"] < 0.05
    constant = genes["constant"]
    assert constant["status"] == "unestimable"
    assert constant["moran_p"] is constant["moran_q"] is None
    assert constant["geary_p"] is constant["geary_q"] is None
    assert np.isfinite(result["library_size_diagnostic"]["moran_i"])
    pair = result["neighborhood_enrichment"][0]
    assert pair["cell_types"] == ["A", "A"]
    assert pair["z_score"] > 4 and 0 < pair["p_value"] <= pair["q_value"] < 0.05
    assert result["clinical_release_allowed"] is False
    assert (
        fit_spatial_pattern(*paths, spec)["receipt_sha256"] == result["receipt_sha256"]
    )


def test_knn_keeps_donor_section_blocks_disjoint_and_handles_isolate():
    from devbench.core.spatial_analysis import build_spatial_graph

    coordinates = np.array([[0, 0], [1, 0], [0, 0], [1, 0], [0, 0]])
    blocks = [("d1", "s1"), ("d1", "s1"), ("d1", "s2"), ("d1", "s2"), ("d2", "s1")]
    graph = build_spatial_graph(coordinates, blocks, k=4).toarray()
    assert {(i, j) for i, j in zip(*np.nonzero(graph), strict=True)} == {
        (0, 1),
        (1, 0),
        (2, 3),
        (3, 2),
    }
    assert np.all(graph[-1] == 0)


def test_spatial_null(tmp_path):
    from devbench.core.spatial_analysis import fit_spatial_pattern

    paths, spec = _spatial_inputs(tmp_path, planted=False)
    result = fit_spatial_pattern(*paths, spec)
    signals = [row for row in result["gene_statistics"] if row["moran_p"] is not None]
    assert signals and all(row["moran_q"] > 0.05 for row in signals)
    assert all(row["q_value"] > 0.05 for row in result["neighborhood_enrichment"])


@pytest.mark.parametrize(
    "updates",
    [
        {"coordinate_frame": "X_umap"},
        {"coordinate_frame": "PCA-2"},
        {"coordinate_units": None},
        {"coordinate_units": "pixels"},
    ],
)
def test_refuses_embedding_or_missing_physical_units(tmp_path, updates):
    from devbench.core.spatial_analysis import fit_spatial_pattern

    paths, spec = _spatial_inputs(tmp_path)
    with pytest.raises(ValueError, match="coordinate"):
        fit_spatial_pattern(*paths, spec.model_copy(update=updates))


def test_zero_library_spots_do_not_create_compositional_spatial_signal(tmp_path):
    from devbench.core.spatial_analysis import SpatialPatternSpec, fit_spatial_pattern

    counts, metadata = tmp_path / "counts.csv", tmp_path / "metadata.csv"
    spots = [f"s{i}" for i in range(12)]
    pd.DataFrame({"spot_id": spots, "gene": [0] * 6 + [100] * 6}).to_csv(
        counts, index=False
    )
    pd.DataFrame(
        {
            "spot_id": spots,
            "donor_id": "d",
            "section_id": "section",
            "x": range(12),
            "y": 0,
            "cell_type": "A",
        }
    ).to_csv(metadata, index=False)
    result = fit_spatial_pattern(
        counts,
        metadata,
        SpatialPatternSpec(
            counts_sha256=hashlib.sha256(counts.read_bytes()).hexdigest(),
            metadata_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest(),
            coordinate_frame="registered-tissue",
            coordinate_units="um",
            k=2,
            n_perm=99,
            seed=35,
        ),
    )
    assert result["zero_library_spot_ids"] == spots[:6]
    assert result["gene_statistics"][0]["moran_p"] is None


def test_collapsed_spatial_coordinates_are_refused():
    from devbench.core.spatial_analysis import build_spatial_graph

    with pytest.raises(ValueError, match="duplicate_spatial_coordinates"):
        build_spatial_graph(np.zeros((3, 2)), [("donor", "section")] * 3, k=2)


def test_depth_gradient_null_and_optional_cell_type(tmp_path):
    from devbench.core.spatial_analysis import fit_spatial_pattern

    paths, spec = _spatial_inputs(tmp_path)
    rng = np.random.default_rng(1776)
    x, y = np.tile(np.indices((12, 12)).reshape(2, -1).T, (2, 1)).T
    depths = np.where(x < 6, 48, 4800)
    counts = np.stack([rng.multinomial(depth, np.full(32, 1 / 32)) for depth in depths])
    ids = [f"spot-{i:03}" for i in range(len(x))]
    pd.DataFrame(counts).assign(spot_id=ids).to_csv(paths[0], index=False)
    metadata = pd.DataFrame(
        dict(
            spot_id=ids,
            x=x,
            y=y,
            donor_id=np.repeat(["d1", "d2"], 144),
            section_id="s1",
        )
    )
    metadata.assign(cell_type="A").to_csv(paths[1], index=False)
    spec = spec.model_copy(update={"seed": 1777})

    def fit():
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        return fit_spatial_pattern(
            *paths,
            spec.model_copy(
                update=dict(
                    zip(("counts_sha256", "metadata_sha256"), hashes, strict=True)
                )
            ),
        )

    result = fit()
    for q in ("moran_q", "geary_q"):
        assert sum(row[q] <= 0.05 for row in result["gene_statistics"]) / 32 < 0.10
    assert result["library_size_diagnostic"]["p_value"] < 0.05
    metadata.to_csv(paths[1], index=False)
    without_types = fit()
    assert without_types["neighborhood_enrichment"] == []
    assert without_types["gene_statistics"] == result["gene_statistics"]


def test_spatial_graph_does_not_bridge_empty_glass():
    from devbench.core.spatial_analysis import build_spatial_graph

    coordinates = np.array([[0, 0], [1, 0], [100, 0], [101, 0]])
    blocks = [("donor", "section")] * 4
    for graph in (
        build_spatial_graph(coordinates, blocks, k=2),
        build_spatial_graph(coordinates, blocks, k=2, max_radius=1),
    ):
        assert graph.nnz == 4
        assert graph[:2, 2:].nnz == 0


def test_analytical_svg_is_not_limited_by_permutation_resolution(tmp_path):
    from devbench.core.spatial_analysis import fit_spatial_pattern

    paths, spec = _spatial_inputs(tmp_path)
    result = fit_spatial_pattern(*paths, spec.model_copy(update={"n_perm": 1}))
    signal = next(
        row for row in result["gene_statistics"] if row["gene_id"] == "gradient"
    )
    assert 0 < signal["moran_p"] <= signal["moran_q"] <= 0.05
    assert 0 < signal["geary_p"] <= signal["geary_q"] <= 0.05
    permuted = fit_spatial_pattern(
        *paths, spec.model_copy(update={"svg_test": "permutation", "n_perm": 1})
    )
    permuted_signal = next(
        row for row in permuted["gene_statistics"] if row["gene_id"] == "gradient"
    )
    assert permuted_signal["moran_i"] == signal["moran_i"]
    assert permuted_signal["geary_c"] == signal["geary_c"]
    assert min(permuted_signal["moran_p"], permuted_signal["geary_p"]) >= 0.5


def test_blocked_randomization_moments_match_exact_enumeration():
    from devbench.core.spatial_analysis import _randomization_moments

    blocks = [np.arange(5), np.arange(5, 8)]
    left, right = np.array([(0, 1), (0, 2), (2, 3), (3, 4), (5, 6), (6, 7)]).T
    degrees = np.bincount(np.r_[left, right], minlength=8).astype(float)
    values = np.array([-2, -1, 0, 1, 2, -5, 2, 3], dtype=float)
    exact = []
    for first, second in product(permutations(values[:5]), permutations(values[5:])):
        permuted = np.array(first + second)
        exact.append(
            [
                permuted[left] @ permuted[right],
                np.sum((permuted[left] - permuted[right]) ** 2),
            ]
        )
    mean, variance = _randomization_moments(values[:, None], blocks, degrees)
    assert mean[:, 0] == pytest.approx(np.mean(exact, axis=0))
    assert variance[:, 0] == pytest.approx(np.var(exact, axis=0))
