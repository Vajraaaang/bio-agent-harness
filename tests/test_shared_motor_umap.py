from __future__ import annotations

import csv
import json
import math

import pytest


@pytest.fixture
def motor_sources(tmp_path):
    import numpy as np
    import pandas as pd
    from scipy import sparse

    ad = pytest.importorskip("anndata")
    rng = np.random.default_rng(79)
    names = [*(f"Gene{i}" for i in range(12)), "Chat", "Slc18a3", "Ebf2"]
    paths = []
    for stage, neurons in ((9, 0), (10, 4), (11, 7)):
        labels = ["Neuroblast: Motor neuron"] * (9 - neurons)
        labels += ["Neuron: Motor neuron"] * neurons + ["Other"] * 3
        observations = pd.DataFrame(
            {
                "timepoint": pd.Categorical([f"e{stage}"] * len(labels)),
                "annotation": pd.Categorical(labels),
            },
            index=[f"private-cell-{stage}-{i}" for i in range(len(labels))],
        )
        variable = pd.DataFrame({"highly_variable": [True] * len(names)}, index=names)
        counts = sparse.csr_matrix(rng.poisson(4, (len(labels), len(names))))
        data = ad.AnnData(counts.copy(), obs=observations, var=variable)
        data.layers["raw_counts"] = counts
        path = tmp_path / f"e{stage}.h5ad"
        data.write_h5ad(path)
        paths.append(path)
    return tuple(paths)


def test_three_stages_share_coordinates_and_keep_marker_genes_out(
    motor_sources, tmp_path
):
    from devbench.analysis.shared_motor_umap import run_shared_motor_umap

    pytest.importorskip("umap")
    pytest.importorskip("matplotlib")
    output = tmp_path / "output"
    result = run_shared_motor_umap(
        motor_sources,
        output,
        stage_column="timepoint",
        label_column="annotation",
        counts_layer="raw_counts",
    )
    assert result["cells"] == 27
    assert result["features"] == 12
    assert [row["all_cells"] for row in result["stages"]] == [12, 12, 12]
    assert [row["neuron"] for row in result["stages"]] == [0, 4, 7]
    assert [row["neuroblast"] for row in result["stages"]] == [9, 5, 2]
    assert [row["neuron_share_of_motor"] for row in result["stages"]] == [
        0,
        4 / 9,
        7 / 9,
    ]
    assert set(result["artifacts"]) == {
        "umap",
        "markers",
        "coordinates",
        "composition",
        "parameters",
    }
    for key in ("umap", "markers"):
        assert (
            (output / result["artifacts"][key])
            .read_bytes()
            .startswith(b"\x89PNG\r\n\x1a\n")
        )
    parameters = json.loads((output / "parameters.json").read_text())
    assert parameters["umap"]["seed"] == 42
    assert set(parameters["marker_genes_held_out"]) == {"Chat", "Slc18a3", "Ebf2"}
    assert not (set(parameters["features"]) & set(parameters["marker_genes_held_out"]))
    with (output / "coordinates.csv").open(newline="") as stream:
        coordinates = list(csv.DictReader(stream))
    assert len(coordinates) == 27
    assert set(coordinates[0]) == {"stage", "state", "umap_1", "umap_2"}
    assert {row["stage"] for row in coordinates} == {"e09", "e10", "e11"}
    assert all(
        math.isfinite(float(row[axis]))
        for row in coordinates
        for axis in (
            "umap_1",
            "umap_2",
        )
    )


def test_declared_stage_order_must_match_observed_labels(motor_sources, tmp_path):
    from devbench.analysis.shared_motor_umap import run_shared_motor_umap

    output = tmp_path / "output"
    with pytest.raises(ValueError, match="stage metadata"):
        run_shared_motor_umap(
            tuple(reversed(motor_sources)),
            output,
            stage_column="timepoint",
            label_column="annotation",
            counts_layer="raw_counts",
        )
    assert not (output / "shared_motor_umap.png").exists()


def test_inventory_uses_only_true_categorical_hvg_flags(motor_sources):
    import pandas as pd

    from devbench.analysis.shared_motor_umap import _inventory

    ad = pytest.importorskip("anndata")
    source = motor_sources[0]
    data = ad.read_h5ad(source)
    data.var["highly_variable"] = pd.Categorical(
        ["True", "False", "nan", None] + ["nan"] * (data.n_vars - 4),
        categories=["False", "True", "nan"],
    )
    data.write_h5ad(source)

    observed = _inventory(source, "e09", "timepoint", "annotation", "raw_counts")

    assert observed.variable == {"Gene0"}


def test_rejects_invalid_sparse_indices_without_rendering(motor_sources, tmp_path):
    import h5py

    from devbench.analysis.shared_motor_umap import run_shared_motor_umap

    with h5py.File(motor_sources[1], "r+") as source:
        source["layers/raw_counts/indices"][0] = 10_000
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="coordinates exceed"):
        run_shared_motor_umap(
            motor_sources,
            output,
            stage_column="timepoint",
            label_column="annotation",
            counts_layer="raw_counts",
        )
    assert not (output / "shared_motor_umap.png").exists()
