"""A separate reviewer pass over streamed source counts and emitted artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile


def review_figure_evidence(root, store, conditions, parameters):
    """Compare serialized evidence directly with saved scientific tables.

    Coordinate, population, category and provenance checks deliberately do not
    call the exporter's payload builder or infer success from regenerated PNGs.
    """
    import math
    import pandas as pd
    from .checkpoints import digest, sha256
    from .figure_artifacts import load_figure, renderer_identity, _table

    root = Path(root)
    metrics = _table(store, "qc", "metrics.csv")
    expected_ids = sorted(metrics.index[metrics.qc_pass.to_numpy(dtype=bool)].tolist())
    receipt_hash = sha256(store.path("qc") / "receipt.json")
    checks = {"figure_population_IDs_match_QC": True,
              "figure_coordinates_and_categories_match_saved_tables": True,
              "figure_scientific_bindings_match_receipts": True,
              "figure_labels_and_axis_semantics_match_tables": True,
              "figure_shared_category_colors_consistent": True}
    shared_colors = {}
    for figure_id in ("qc", *conditions):
        record, payload = load_figure(root, figure_id)
        is_qc = figure_id == "qc"
        expected_stage = "qc" if is_qc else figure_id + "_graph"
        table = metrics.loc[metrics.qc_pass] if is_qc else _table(store, expected_stage, "embedding.csv")
        checks["figure_population_IDs_match_QC"] &= (
            sorted(table.index) == expected_ids and sorted(payload["population_ids"]) == expected_ids
            and record["semantics"]["population_sha256"] == digest(expected_ids)
            and record["analysis_n"] == len(expected_ids))
        expected_binding = {"input_sha256": parameters["data_sha256"],
            "parameters_sha256": sha256(root / "parameters.json"),
            "analysis_source_sha256": parameters["source_sha256"],
            "generating_stage_receipts": {"qc": receipt_hash}}
        if not is_qc:
            expected_binding["generating_stage_receipts"][expected_stage] = sha256(store.path(expected_stage) / "receipt.json")
        semantics = record["semantics"]
        checks["figure_scientific_bindings_match_receipts"] &= (
            record["analysis_binding"] == expected_binding and record["renderer_identity"] == renderer_identity()
            and record["family"] == ("G02" if is_qc else "G08") and semantics["unit"] == "cell"
            and semantics["filter_sha256"] == digest({"qc_receipt_sha256": receipt_hash,
                "policy": "qc_pass", "retained_ids": expected_ids})
            and semantics["representation_or_layer"] == ("count_derived_QC:" + str(parameters.get("counts_layer") or "X")
                if is_qc else "UMAP_from:" + str(conditions[figure_id])))
        expected_columns = [("log1p_total_counts", "log1p_n_genes_by_counts", "pct_counts_mt", "QC: retained cells",
                            "log(1 + total counts)", "log(1 + detected genes)")] if is_qc else [
            ("UMAP_1", "UMAP_2", parameters["batch_key"], figure_id + ": Observed batch", "UMAP 1", "UMAP 2"),
            ("UMAP_1", "UMAP_2", "leiden", figure_id + ": Rerun Leiden cluster", "UMAP 1", "UMAP 2")]
        checks["figure_labels_and_axis_semantics_match_tables"] &= len(payload["panels"]) == len(expected_columns)
        all_rendered = set()
        for panel, (x, y, column, title, xlabel, ylabel) in zip(payload["panels"], expected_columns):
            checks["figure_labels_and_axis_semantics_match_tables"] &= (
                panel["title"] == title and panel["x_axis"]["field"] == x and panel["y_axis"]["field"] == y
                and panel["x_axis"]["label"] == xlabel and panel["y_axis"]["label"] == ylabel
                and panel["x_axis"]["transform"] == ("natural_log1p" if is_qc else "none")
                and panel["y_axis"]["transform"] == ("natural_log1p" if is_qc else "none")
                and panel["color"]["field"] == column)
            checks["figure_population_IDs_match_QC"] &= sorted(row["observation_id"] for row in panel["rows"]) == expected_ids
            for row in panel["rows"]:
                cell_id = row["observation_id"]
                if cell_id not in table.index:
                    checks["figure_coordinates_and_categories_match_saved_tables"] = False
                    continue
                source = table.loc[cell_id]
                expected = {}
                for name, field in (("x", x), ("y", y), ("color", column)):
                    value = source[field]
                    if name == "color" and not is_qc:
                        expected[name] = None if pd.isna(value) else str(value)
                    else:
                        expected[name] = float(value) if not pd.isna(value) and math.isfinite(float(value)) else None
                valid = all(value is not None for value in expected.values())
                checks["figure_coordinates_and_categories_match_saved_tables"] &= (
                    all(row[name] == value for name, value in expected.items())
                    and row["status"] == ("rendered" if valid else "excluded"))
                if valid:
                    all_rendered.add(cell_id)
            color = panel["color"]
            if color["kind"] == "categorical":
                previous = shared_colors.setdefault(color["grouping"], color["colors"])
                checks["figure_shared_category_colors_consistent"] &= previous == color["colors"]
            else:
                finite_colors = [float(value) for value in table[column] if not pd.isna(value) and math.isfinite(float(value))]
                checks["figure_coordinates_and_categories_match_saved_tables"] &= (
                    color["scale"] == {"kind": "linear", "min": min(finite_colors, default=0),
                                      "max": max(finite_colors, default=0), "clipped_n": 0})
        checks["figure_coordinates_and_categories_match_saved_tables"] &= record["rendered_n"] == len(all_rendered)
    return {key: bool(value) for key, value in checks.items()}


def review_streaming_bundle(root, source_path, parameters, store, conditions):
    import anndata as ad
    import nbformat
    import numpy as np
    from PIL import Image
    from scipy import sparse
    from .checkpoints import sha256
    from .io import open_expression
    from .singlecell import gene_symbols, validate_counts, filter_qc_metrics
    from .streaming import read_table
    from .reporting import compile_report, report_text, render_concordance_figures, METHOD_CITATIONS

    root = Path(root)
    checks, errors = {}, []
    try:
        checks["source_sha256_recomputed"] = sha256(source_path) == parameters["data_sha256"]
        metrics = read_table(store.path("qc")/"metrics.csv")
        selected = ad.read_h5ad(store.path("selected")/"selected.h5ad")
        counts_match, normalized_match, qc_match = True, True, True
        with open_expression(Path(source_path), layer=parameters["counts_layer"], batch_key=parameters["batch_key"]) as source:
            symbols, symbol_source = gene_symbols(source, parameters.get("symbol_column"))
            mito = np.asarray(symbols.str.upper().str.startswith("MT-"))
            checks["mitochondrial_features_present"] = bool(mito.any())
            checks["all_cell_identities_preserved"] = source.obs.index.equals(metrics.index)
            feature_indices = source.var.index.get_indexer(selected.var_names)
            if np.any(feature_indices < 0):
                raise ValueError("selected_feature_absent_from_input")
            checks["count_input_scope"] = selected.uns["source_matrix"] == source.matrix_source
            recomputed = metrics.copy()
            for block in source.iter_rows(block_rows=parameters["block_rows"], max_bytes=parameters["max_block_bytes"]):
                validate_counts(block.X)
                counts = sparse.csr_matrix(block.X, dtype=np.float64)
                totals = np.asarray(counts.sum(axis=1)).ravel()
                genes = np.asarray((counts > 0).sum(axis=1)).ravel()
                mitochondrial = np.asarray(counts[:, mito].sum(axis=1)).ravel()
                with np.errstate(divide="ignore", invalid="ignore"):
                    pct = 100*mitochondrial/totals
                expected = {"total_counts": totals, "n_genes_by_counts": genes,
                    "log1p_total_counts": np.log1p(totals), "log1p_n_genes_by_counts": np.log1p(genes),
                    "pct_counts_mt": pct}
                top20=np.full(block.n_obs,np.nan)
                if block.n_vars>=20:
                    counts.sum_duplicates()
                    for row in range(block.n_obs):
                        values=counts.data[counts.indptr[row]:counts.indptr[row+1]]
                        if totals[row]>0:
                            top20[row]=100*float(np.sort(values)[-20:].sum())/totals[row]
                expected["pct_counts_in_top_20_genes"]=top20
                for column, value in expected.items():
                    qc_match &= bool(np.allclose(metrics.loc[block.obs_names, column], value,
                                                 rtol=2e-6, atol=1e-8, equal_nan=True))
                    recomputed.loc[block.obs_names, column] = value
                positions = selected.obs_names.get_indexer(block.obs_names)
                included = positions >= 0
                if included.any():
                    raw = counts[included][:, feature_indices]
                    actual_counts = selected.layers["counts"][positions[included]]
                    counts_match &= (raw != actual_counts).nnz == 0
                    expected_x = raw.multiply((1e4/totals[included])[:, None]).tocsr()
                    expected_x.data = np.log1p(expected_x.data)
                    delta = expected_x - selected.X[positions[included]]
                    normalized_match &= not delta.nnz or bool(np.max(np.abs(delta.data)) < 2e-5)
            keep, _ = filter_qc_metrics(recomputed, batch_key=parameters["batch_key"],
                min_genes=parameters["min_genes"], mad_limit=parameters["mad_limit"],
                max_mito_percent=parameters.get("max_mito_percent"),
                mitochondrial_features=int(mito.sum()), symbol_source=symbol_source)
            checks["QC_selection_recomputed"] = (np.array_equal(keep, metrics.qc_pass.to_numpy(dtype=bool))
                and selected.obs_names.equals(source.obs.index[keep]))
        checks["full_transcriptome_QC_recomputed"] = bool(qc_match)
        from .qc_concordance import compare_qc
        stored_reference=read_table(store.path("qc")/"external-reference.csv")
        checks["stored_QC_comparison_recomputed"]=compare_qc(stored_reference,metrics)==json.loads((store.path("qc")/"concordance.json").read_text())
        checks["selected_raw_counts_match_source"] = bool(counts_match)
        checks["full_library_normalization_recomputed"] = bool(normalized_match)
        checks["stored_reference_excluded_from_fit_metadata"] = not any(
            name in selected.obs for name in ("celltypes", "conf_score", "leiden", "majority_voting", "predicted_labels"))
        checks["checkpoint_artifacts_verified"] = True
        for path in store.root.glob("*/receipt.json"):
            record = json.loads(path.read_text())
            store.verify(path.parent.name, tuple(record["dependencies"]))
        reference = read_table(store.path("qc")/"external-reference.csv")
        for name in conditions:
            table = read_table(store.path(name+"_graph")/"embedding.csv")
            checks[name+"_embedding_identities"] = table.index.equals(selected.obs_names)
            checks[name+"_finite_embedding"] = bool(np.isfinite(table[["UMAP_1", "UMAP_2"]]).all().all())
        expected_report = compile_report(store, parameters, conditions)
        checks['no_constant_embedding_accepted']=all(not row['batch_mixing']['ASW_details']['constant_embedding']
                                                    for row in expected_report['conditions'].values())
        checks["concordance_metrics_recomputed"] = expected_report == json.loads((root/"report.json").read_text())
        checks["summary_contains_supported_claims_only"] = (root/"summary.md").read_text() == report_text(expected_report)
        checks["registered_method_citations"] = json.loads((root/"citations.json").read_text()) == METHOD_CITATIONS
        checks.update(review_figure_evidence(root, store, conditions, parameters))
        notebook = nbformat.read(root/"notebook.ipynb", as_version=4)
        cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
        checks["notebook_executed_without_errors"] = bool(cells) and all(cell.execution_count is not None
            and not any(output.output_type == "error" for output in cell.outputs) for cell in cells)
        with tempfile.TemporaryDirectory(prefix=".review-", dir=root) as temp:
            render_concordance_figures(store, conditions, Path(temp), batch_key=parameters["batch_key"])
            matches = []
            for filename in ["qc.png", *(name+".png" for name in conditions)]:
                with Image.open(root/"figures"/filename) as actual, Image.open(Path(temp)/filename) as expected:
                    matches.append(np.array_equal(np.asarray(actual.convert("RGBA")), np.asarray(expected.convert("RGBA"))))
            checks["figures_regenerated_from_saved_tables"] = all(matches)
            from .html_report import write_interactive_figures
            write_interactive_figures(store,conditions,Path(temp),batch_key=parameters['batch_key'])
            checks['interactive_plot_data_regenerated']=json.loads((Path(temp)/'interactive-data.json').read_text())==json.loads((root/'figures/interactive-data.json').read_text())
    except Exception as error:
        errors.append({"type": type(error).__name__, "message": str(error)[:2000]})
    return {"format": "devbench.analysis.streaming-review/1", "execution_tier": parameters["execution_tier"],
        "passed": bool(checks) and all(checks.values()) and not errors, "checks": checks, "errors": errors,
        "reviewer": "separate_deterministic_process_not_an_LLM_or_qualified_independent_reviewer",
        "limits": ["QC quantities and selected counts are recomputed from the full external input.",
            "QC threshold policy, concordance metrics and renderer reuse producer functions.",
            "Figure IDs, coordinates, categories and receipt bindings are also compared directly with saved tables.",
            "Clustering stability, HVG population calibration and biological accuracy are not established.",
            "A registered citation list is not a literature-wide citation-entailment audit.",
            "No original CellTypist reproduction or clinical-validation claim is made."]}
