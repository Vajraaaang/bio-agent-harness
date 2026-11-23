"""Portable notebook cells and auditable single-cell output tables."""
from __future__ import annotations

import json
from pathlib import Path


CITATIONS = {
    "scanpy": {"title": "Scanpy preprocessing and clustering",
               "url": "https://scanpy.readthedocs.io/en/stable/tutorials/basics/clustering.html"},
    "scrublet": {"title": "Scrublet: Computational Identification of Cell Doublets",
                 "url": "https://doi.org/10.1016/j.cels.2018.11.005"},
    "harmony": {"title": "Fast, sensitive and accurate integration of single-cell data with Harmony",
                "url": "https://doi.org/10.1038/s41592-019-0619-0"},
    "celltypist": {"title": "Cross-tissue immune cell analysis reveals tissue-specific features in humans",
                   "url": "https://doi.org/10.1126/science.abl5197"},
}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def confined_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Analysis artifact paths must be bundle-relative")
    resolved = (Path(root) / path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError("Analysis artifact escapes its bundle")
    return resolved


def summary_text(claims):
    labels = ", ".join(f"{name}: {count}" for name, count in sorted(claims["predicted_labels"].items()))
    return ("# Single-cell analysis\n\n"
            f"Retained {claims['retained_cells']} of {claims['input_cells']} cells after QC; "
            f"{claims['features']} features were retained.\n\n"
            f"Leiden identified {claims['clusters']} exploratory clusters. "
            f"Computational cell-type predictions: {labels}.\n\n"
            f"Batch correction: {claims['batch_correction_status']}. "
            f"Doublet detection: {claims['doublet_detection_status']} (flags only).\n\n"
            "Counts are preserved separately from log-normalized expression and corrected embeddings. "
            "Cell-type labels are computational predictions; uncertain labels are retained. "
            "These outputs do not establish cell identity, causal effects or clinical validity.\n")


def export_outputs(data, parameters, reports, measurements, root):
    import pandas as pd
    from .plotting import render_figures

    root = Path(root)
    data.write_h5ad(root / "analysis.h5ad")
    embedding = pd.DataFrame(data.obsm["X_umap"], columns=["UMAP_1", "UMAP_2"], index=data.obs_names)
    embedding.index.name = "cell_id"
    embedding["cluster"] = data.obs["leiden"].astype(str)
    embedding["cell_type"] = data.obs["cell_type"].astype(str)
    embedding.to_csv(root / "embedding.csv", float_format="%.17g")
    qc = data.obs[["total_counts", "n_genes_by_counts", "log1p_total_counts",
                   "log1p_n_genes_by_counts", "pct_counts_mt"]].copy()
    qc.index.name = "cell_id"
    qc.to_csv(root / "qc.csv", float_format="%.17g")
    # Producer and reviewer render the same explicit serialized plotting inputs.
    render_figures(pd.read_csv(root / "embedding.csv", float_precision="round_trip"),
                   pd.read_csv(root / "qc.csv", float_precision="round_trip"), root / "figures")
    claims = {"input_cells": reports["qc"]["cells_input"], "retained_cells": data.n_obs,
              "features": data.n_vars, "clusters": int(data.obs["leiden"].nunique()),
              "predicted_labels": {str(k): int(v) for k,v in data.obs["cell_type"].value_counts().items()},
              "batch_correction_status": reports["preprocessing"]["batch_correction"]["status"],
              "doublet_detection_status": reports["doublets"]["status"]}
    write_json(root / "claims.json", claims)
    write_json(root / "reports.json", reports)
    write_json(root / "measurements.json", measurements)
    (root / "summary.md").write_text(summary_text(claims))
    used = ["scanpy", "scrublet"]
    if parameters["correct_batch"]:
        used.append("harmony")
    if parameters.get("model_path"):
        used.append("celltypist")
    write_json(root / "citations.json", {name: CITATIONS[name] for name in used})
    return claims


def build_analysis_notebook(path):
    """Editable scientific steps; no absolute host paths or private truth."""
    from .notebooks import write_notebook

    cells = [
'''import os
from pathlib import Path
bundle_root = Path.cwd().resolve()
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMBA_NUM_THREADS"):
    os.environ[key] = "1"
for key, directory in (("MPLCONFIGDIR", ".cache/matplotlib"), ("NUMBA_CACHE_DIR", ".cache/numba"), ("CELLTYPIST_FOLDER", ".cache/celltypist")):
    os.environ[key] = str(bundle_root / directory)
import sys
if (bundle_root / "source").is_dir():
    sys.path.insert(0, str(bundle_root / "source"))
import json
from devbench.analysis.resources import StageMeasurement
from devbench.analysis.pipeline import confined_path, export_outputs, write_json
parameters = json.loads((bundle_root / "parameters.json").read_text())
measurements, reports = [], {}
''',
'''from devbench.analysis.io import open_h5ad
with StageMeasurement("load_observed_counts") as measured:
    with open_h5ad(confined_path(bundle_root, parameters["data_path"]), layer=parameters.get("counts_layer")) as source:
        data = source.subset(max_bytes=parameters["max_matrix_bytes"])
measurements.append(measured.result)
write_json(bundle_root / "measurements.json", measurements)
print({"cells": data.n_obs, "features": data.n_vars})
''',
'''from devbench.analysis.singlecell import quality_control
with StageMeasurement("quality_control") as measured:
    data, reports["qc"] = quality_control(data, symbol_column=parameters.get("symbol_column"),
        batch_key=parameters.get("batch_key"), min_genes=parameters["min_genes"], mad_limit=parameters["mad_limit"])
measurements.append(measured.result)
write_json(bundle_root / "measurements.json", measurements)
print({key: reports["qc"][key] for key in ("cells_input", "cells_retained", "mitochondrial_features")})
''',
'''from devbench.analysis.singlecell import detect_doublets
with StageMeasurement("doublet_detection") as measured:
    data, reports["doublets"] = detect_doublets(data, batch_key=parameters.get("batch_key"), seed=parameters["seed"])
measurements.append(measured.result)
write_json(bundle_root / "measurements.json", measurements)
print(reports["doublets"]["status"])
''',
'''from devbench.analysis.singlecell import normalize_and_embed
with StageMeasurement("normalization_integration_clustering") as measured:
    data, reports["preprocessing"] = normalize_and_embed(data, n_top_genes=parameters["n_top_genes"],
        n_pcs=parameters["n_pcs"], seed=parameters["seed"], batch_key=parameters.get("batch_key"),
        correct_batch=parameters["correct_batch"])
measurements.append(measured.result)
write_json(bundle_root / "measurements.json", measurements)
print(reports["preprocessing"])
''',
'''from devbench.analysis.singlecell import marker_annotation, annotate_celltypist
with StageMeasurement("cell_type_annotation") as measured:
    data.obs["marker_cell_type"], reports["markers"] = marker_annotation(data, parameters["markers"])
    data.obs["cell_type"] = data.obs["marker_cell_type"]
    if parameters.get("model_path"):
        labels, scores, reports["classifier"] = annotate_celltypist(data,
            confined_path(bundle_root, parameters["model_path"]), expected_sha256=parameters["model_sha256"])
        data.obs["cell_type"], data.obs["cell_type_score"] = labels, scores
measurements.append(measured.result)
write_json(bundle_root / "measurements.json", measurements)
print(data.obs["cell_type"].value_counts().to_dict())
''',
'''with StageMeasurement("export_figures_and_results") as measured:
    claims = export_outputs(data, parameters, reports, measurements, bundle_root)
measurements.append(measured.result)
write_json(bundle_root / "measurements.json", measurements)
print(claims)
''',
'''from IPython.display import Image, display, Markdown
display(Markdown((bundle_root / "summary.md").read_text()))
display(Image(filename=str(bundle_root / "figures/embedding.png")))
display(Image(filename=str(bundle_root / "figures/qc.png")))
''']
    write_notebook(path, cells)
