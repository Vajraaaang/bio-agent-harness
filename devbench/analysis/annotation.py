"""Bounded doublet and annotation stages; unavailable references stay explicit."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .checkpoints import atomic_json
from .io import open_expression
from .singlecell import detect_doublets, marker_annotation, annotate_celltypist, gene_symbols
from .streaming import read_table


def run_doublets(source_path, store, *, layer, batch_key, seed, max_batch_bytes=1024**3):
    metrics = read_table(store.path("qc")/"metrics.csv")
    groups = metrics[batch_key].astype(str)
    stages = []
    with open_expression(Path(source_path), layer=layer, batch_key=batch_key) as source:
        for index, group in enumerate(sorted(groups.unique())):
            rows = np.flatnonzero((groups == group).to_numpy() & metrics["qc_pass"].to_numpy(dtype=bool))
            if not len(rows):
                continue
            name = f"doublets_{index:04d}"
            def operation(folder):
                # One biological library at a time; a large library is refused
                # by the allocation guard, never silently sampled or pooled.
                data = source.subset(rows, max_bytes=max_batch_bytes)
                data.obs = metrics.iloc[rows][[batch_key]].copy()
                # This detached, per-library read is owned by this stage. Only
                # predicted metadata is needed; another expression copy is not.
                result, report = detect_doublets(data, batch_key=batch_key, seed=seed, copy=False)
                result.obs[["doublet_score", "predicted_doublet"]].to_csv(folder/"predictions.csv")
                atomic_json(folder/"report.json", report)
                return report
            store.run(name, operation, dependencies=("qc",))
            stages.append(name)
    def finish(folder):
        frame = pd.concat([read_table(store.path(name)/"predictions.csv") for name in stages])
        frame = frame.loc[metrics.index[metrics["qc_pass"].to_numpy(dtype=bool)]]
        frame.to_csv(folder/"predictions.csv")
        reports = [json.loads((store.path(name)/"report.json").read_text()) for name in stages]
        available = frame["doublet_score"].notna()
        report = {"method": "scanpy_scrublet_per_observed_batch", "filtering": "flag_only",
            "status": "completed" if available.all() else "partial" if available.any() else "unavailable",
            "batches": [batch for report in reports for batch in report["batches"]],
            "scope": "predictions_not_known_doublet_truth; batch_assumed_to_identify_library"}
        atomic_json(folder/"report.json", report)
        return report
    return store.run("doublets", finish, dependencies=stages)


def run_annotation(source_path, store, *, layer, panels=None, model_path=None, model_sha256=None,
                   block_rows=128, max_block_bytes=64*1024**2):
    metrics = read_table(store.path("qc")/"metrics.csv")
    keep = metrics["qc_pass"].to_numpy(dtype=bool)
    stages = []
    panels = panels or {}
    if model_path is None and not panels:
        def unavailable(folder):
            pd.DataFrame({"cell_type": "uncertain"}, index=metrics.index[keep]).to_csv(folder/"predictions.csv")
            report = {"status": "unavailable_no_model_or_marker_panel", "method": None,
                      "original_annotation_reproduced": False,
                      "scope": "no_inferred_cell_identity; external_labels_are_not_ground_truth"}
            atomic_json(folder/"report.json", report)
            return report
        return store.run("annotation", unavailable, dependencies=("qc",))
    with open_expression(Path(source_path), layer=layer) as source:
        symbols, _ = gene_symbols(source)
        requested = {str(marker).upper() for values in panels.values() for marker in values}
        features = None if model_path else np.flatnonzero(np.isin(symbols.str.upper(), list(requested)))
        if features is not None and not len(features):
            raise ValueError("annotation_marker_panel_has_no_observed_genes")
        for start in range(0, source.shape[0], block_rows):
            stop = min(start+block_rows, source.shape[0])
            rows = np.arange(start, stop)[keep[start:stop]]
            if not len(rows):
                continue
            name = f"annotation_{start:08d}"
            def operation(folder):
                data = source.subset(rows, features, max_bytes=max_block_bytes)
                data.obs = pd.DataFrame(index=data.obs_names)
                if model_path:
                    labels, confidence, report = annotate_celltypist(data, model_path, expected_sha256=model_sha256)
                    frame = pd.DataFrame({"cell_type": labels, "score": confidence})
                else:
                    labels, report = marker_annotation(data, panels,
                        library_totals=metrics.iloc[rows]["total_counts"].to_numpy())
                    frame = pd.DataFrame({"cell_type": labels})
                frame.to_csv(folder/"predictions.csv")
                atomic_json(folder/"report.json", report)
                return {"cells": len(frame), "status": report["status"]}
            store.run(name, operation, dependencies=("qc",))
            stages.append(name)
        def finish(folder):
            frame = pd.concat([read_table(store.path(name)/"predictions.csv") for name in stages])
            if not frame.index.equals(metrics.index[keep]):
                raise ValueError("annotation_row_identity_mismatch")
            frame.to_csv(folder/"predictions.csv")
            report = {"status": "completed", "method": "celltypist" if model_path else "marker_panel",
                "model_sha256": model_sha256, "original_annotation_reproduced": False,
                "uncertain_cells": int((frame.cell_type == "uncertain").sum()),
                "scope": "independent_computational_predictions_not_experimental_accuracy",
                "chunk_reports": [str(store.path(name).relative_to(store.root)/"report.json") for name in stages]}
            atomic_json(folder/"report.json", report)
            return report
        return store.run("annotation", finish, dependencies=stages)
