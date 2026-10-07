"""Separate output review: recompute key quantities from the retained input."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile


def review_bundle(root):
    import anndata as ad
    import nbformat
    import numpy as np
    import pandas as pd
    from PIL import Image
    from scipy import sparse
    from .io import open_h5ad
    from .pipeline import CITATIONS, confined_path, summary_text
    from .plotting import render_figures
    from .singlecell import marker_annotation, annotate_celltypist

    root = Path(root)
    checks, errors = {}, []
    try:
        parameters = json.loads((root / "parameters.json").read_text())
        reports = json.loads((root / "reports.json").read_text())
        data = ad.read_h5ad(root / "analysis.h5ad")
        with open_h5ad(confined_path(root, parameters["data_path"]), layer=parameters.get("counts_layer")) as source:
            input_cells = source.shape[0]
            rows = source.obs.index.get_indexer(data.obs_names)
            columns = source.var.index.get_indexer(data.var_names)
            if np.any(rows < 0) or np.any(columns < 0):
                raise ValueError("output_identities_not_in_retained_input")
            observed = source.subset(rows, columns, max_bytes=parameters["max_matrix_bytes"])
            excluded = reports["qc"]["excluded_cell_ids"]
            checks["cell_accounting"] = (len(excluded) == len(set(excluded))
                and set(excluded).isdisjoint(data.obs_names)
                and set(excluded) | set(data.obs_names) == set(source.obs.index))
        expected_counts = sparse.csr_matrix(observed.X)
        counts = sparse.csr_matrix(data.layers["counts"])
        checks["source_counts_match"] = (counts != expected_counts).nnz == 0
        totals = np.asarray(expected_counts.sum(axis=1)).ravel()
        detected = np.asarray((expected_counts > 0).sum(axis=1)).ravel()
        checks["QC_counts_recomputed"] = bool(np.allclose(totals, data.obs["total_counts"]) and
                                               np.array_equal(detected, data.obs["n_genes_by_counts"]))
        symbols = observed.var["gene_symbols"] if "gene_symbols" in observed.var else observed.var_names
        mito = np.asarray(pd.Index(symbols).str.upper().str.startswith("MT-"), dtype=bool)
        mito_fraction = np.asarray(expected_counts[:, mito].sum(axis=1)).ravel()/totals*100
        checks["mitochondrial_fraction_recomputed"] = bool(mito.any() and
            np.allclose(mito_fraction, data.obs["pct_counts_mt"], rtol=1e-5, atol=1e-6))
        normalized = expected_counts.astype(float).multiply((10000/totals)[:, None]).tocsr()
        normalized.data = np.log1p(normalized.data)
        difference = sparse.csr_matrix(data.X) - normalized
        checks["normalization_recomputed"] = not difference.nnz or bool(np.max(np.abs(difference.data)) < 2e-5)
        embedding = pd.read_csv(root / "embedding.csv", index_col="cell_id", float_precision="round_trip")
        qc = pd.read_csv(root / "qc.csv", index_col="cell_id", float_precision="round_trip")
        checks["plot_table_identities"] = embedding.index.equals(data.obs_names) and qc.index.equals(data.obs_names)
        checks["embedding_table_matches"] = bool(np.allclose(embedding[["UMAP_1", "UMAP_2"]], data.obsm["X_umap"], atol=1e-7))
        checks["QC_plot_values_recomputed"] = bool(np.allclose(qc["log1p_total_counts"], np.log1p(totals)) and
            np.allclose(qc["log1p_n_genes_by_counts"], np.log1p(detected)) and
            np.allclose(qc["pct_counts_mt"], mito_fraction, rtol=1e-5))
        labels, _ = marker_annotation(observed, parameters["markers"])
        if parameters.get("model_path"):
            labels, _, _ = annotate_celltypist(observed, confined_path(root, parameters["model_path"]),
                                                expected_sha256=parameters["model_sha256"])
        checks["annotation_recomputed"] = labels.astype(str).equals(data.obs["cell_type"].astype(str))
        checks["figure_labels_match"] = (embedding["cell_type"].astype(str).tolist() == labels.astype(str).tolist())
        claims = {"input_cells": input_cells, "retained_cells": data.n_obs, "features": data.n_vars,
                  "clusters": int(data.obs["leiden"].nunique()),
                  "predicted_labels": {str(k): int(v) for k,v in labels.value_counts().items()},
                  "batch_correction_status": reports["preprocessing"]["batch_correction"]["status"],
                  "doublet_detection_status": reports["doublets"]["status"]}
        checks["numeric_claims_recomputed"] = claims == json.loads((root / "claims.json").read_text())
        checks["summary_claims_match"] = (root / "summary.md").read_text() == summary_text(claims)
        citations = json.loads((root / "citations.json").read_text())
        checks["registered_method_citations"] = bool(citations) and all(CITATIONS.get(k) == v for k,v in citations.items())
        notebook = nbformat.read(root / "notebook.ipynb", as_version=4)
        cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
        checks["notebook_executed_without_errors"] = bool(cells) and all(cell.execution_count is not None and
            not any(output.output_type == "error" for output in cell.outputs) for cell in cells)
        with tempfile.TemporaryDirectory(prefix=".review-figures-", dir=root) as directory:
            render_figures(embedding.reset_index(), qc.reset_index(), directory)
            matches = []
            for name in ("embedding.png", "qc.png"):
                with Image.open(root / "figures" / name) as actual, Image.open(Path(directory) / name) as expected:
                    matches.append(np.array_equal(np.asarray(actual.convert("RGBA")), np.asarray(expected.convert("RGBA"))))
            checks["figures_regenerated"] = all(matches)
    except Exception as error:
        errors.append({"type": type(error).__name__, "message": str(error)[:2000]})
    return {"record_format": "devbench.analysis.review/1", "passed": bool(checks and all(checks.values()) and not errors),
            "checks": checks, "errors": errors,
            "scope": "separate_numerical_consistency_and_artifact_review_not_independent_biological_validation",
            "limitations": ["Renderer and annotation code are shared with the producer; this is not an independent implementation of those methods.",
                            "References are checked against registered method citations, not a general literature or citation-entailment audit.",
                            "Free-form additions to the generated summary are flagged for human review.",
                            "This pass does not establish cell-type truth, batch correction quality or clinical validity."]}
