import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("scanpy")
pytest.importorskip("nbclient")


@pytest.fixture(scope="module")
def executed_bundle(tmp_path_factory):
    from tests.test_analysis_singlecell import counts_fixture
    from devbench.analysis.pipeline import build_analysis_notebook
    from devbench.analysis.notebooks import execute_notebook
    folder = tmp_path_factory.mktemp("workbench-notebook")
    (folder / "inputs").mkdir()
    counts_fixture().write_h5ad(folder / "inputs/input.h5ad")
    parameters = {"data_path": "inputs/input.h5ad", "counts_layer": None, "min_genes": 3,
                  "mad_limit": None, "batch_key": "batch", "correct_batch": False,
                  "n_top_genes": 30, "n_pcs": 8, "seed": 128, "max_matrix_bytes": 10_000_000,
                  "markers": {"T cell": ["Cd3d", "Cd3e", "Trac", "Il7r"],
                              "B cell": ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]},
                  "model_path": None, "model_sha256": None}
    (folder / "parameters.json").write_text(json.dumps(parameters))
    build_analysis_notebook(folder / "notebook.template.ipynb")
    execute_notebook(folder / "notebook.template.ipynb", folder / "notebook.ipynb", cell_timeout=60)
    return folder


def test_notebook_has_no_host_paths_and_reviewer_checks_real_outputs(executed_bundle):
    import nbformat
    from devbench.analysis.review import review_bundle
    notebook = nbformat.read(executed_bundle / "notebook.ipynb", as_version=4)
    assert all("/Users/" not in cell.source and "/content/" not in cell.source for cell in notebook.cells)
    for path in ("analysis.h5ad", "figures/embedding.png", "figures/qc.png", "summary.md", "measurements.json"):
        assert (executed_bundle / path).is_file()
    report = review_bundle(executed_bundle)
    assert report["passed"], report
    assert report["checks"]["source_counts_match"]
    assert report["checks"]["normalization_recomputed"]
    assert report["checks"]["figures_regenerated"]


def test_reviewer_rejects_a_changed_count_even_with_matching_producer_metric(executed_bundle, tmp_path):
    import anndata as ad
    import shutil
    from devbench.analysis.review import review_bundle
    folder = tmp_path / "corrupted"
    shutil.copytree(executed_bundle, folder)
    data = ad.read_h5ad(folder / "analysis.h5ad")
    counts = data.layers["counts"].tolil()
    counts[0, 0] += 10
    data.layers["counts"] = counts.tocsr()
    data.obs.iloc[0, data.obs.columns.get_loc("total_counts")] += 10
    data.write_h5ad(folder / "analysis.h5ad")
    report = review_bundle(folder)
    assert not report["passed"]
    assert not report["checks"]["source_counts_match"]


def test_reviewer_flags_unsupported_prose_and_changed_figure(executed_bundle, tmp_path):
    import shutil
    from devbench.analysis.review import review_bundle
    folder = tmp_path / "corrupted"
    shutil.copytree(executed_bundle, folder)
    with (folder / "summary.md").open("a") as handle:
        handle.write("\nThis proves a causal disease mechanism.\n")
    shutil.copyfile(folder / "figures/qc.png", folder / "figures/embedding.png")
    report = review_bundle(folder)
    assert not report["passed"]
    assert not report["checks"]["summary_claims_match"]
    assert not report["checks"]["figures_regenerated"]
