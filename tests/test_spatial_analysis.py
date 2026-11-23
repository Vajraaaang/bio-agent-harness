import hashlib

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
