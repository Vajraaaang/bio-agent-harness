"""Compare fresh analysis with existing predictions, never call it accuracy."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path


def confidence_distribution(values):
    import numpy as np
    scores=np.asarray(values,dtype=float)
    finite=scores[np.isfinite(scores)]
    return {"finite_cells":len(finite),"unavailable_cells":int((~np.isfinite(scores)).sum()),
        "quantiles":{str(q):float(np.quantile(finite,q)) for q in (0,.1,.25,.5,.75,.9,1)} if len(finite) else {},
        "bins":[{"lower_inclusive":lo,"upper":hi,
                 "cells":int(((finite>=lo)&(finite<=hi if hi==1 else finite<hi)).sum())}
                for lo,hi in ((0,.5),(.5,.8),(.8,.95),(.95,1))],
        "outside_probability_range":int(((finite<0)|(finite>1)).sum()),
        "scope":"stored_scores_with_unknown_calibration"}


def compare_predictions(reference, predictions):
    import numpy as np
    from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score

    if (not reference.index.is_unique or not predictions.index.is_unique or predictions.empty
            or not predictions.index.isin(reference.index).all()):
        raise ValueError("Concordance requires unique, aligned observation identities")
    matched = reference.loc[predictions.index]
    report = {"scope": "concordance_with_prior_predictions_not_experimental_accuracy",
              "reference_status": "fixed_external_reference_with_unknown_annotation_provenance",
              "reference_cells": len(reference), "compared_cells": len(predictions),
              "retained_fraction": len(predictions)/len(reference)}
    if "conf_score" in matched:
        report["reference_confidence"] = confidence_distribution(matched["conf_score"])
    if "leiden" in matched and "leiden" in predictions:
        valid = matched["leiden"].notna() & predictions["leiden"].notna()
        report["clustering"] = {"cells": int(valid.sum()),
            "ARI": float(adjusted_rand_score(matched.loc[valid, "leiden"].astype(str), predictions.loc[valid, "leiden"].astype(str))),
            "NMI": float(normalized_mutual_info_score(matched.loc[valid, "leiden"].astype(str), predictions.loc[valid, "leiden"].astype(str))),
            "reference_clusters": int(matched.loc[valid, "leiden"].nunique()),
            "rerun_clusters": int(predictions.loc[valid, "leiden"].nunique())}
        if not valid.any():
            report["clustering"].update(ARI=None, NMI=None, status="unavailable_reference_labels")
    else:
        report["clustering"] = {"ARI": None, "NMI": None, "status": "unavailable"}
    if "cell_type" not in predictions:
        report["annotation"] = {"status": "unavailable_model_not_rerun", "label_agreement": None}
    else:
        annotation = {"status": "completed", "uncertain_cells": int((predictions["cell_type"] == "uncertain").sum())}
        for name in ("predicted_labels", "majority_voting", "celltypes"):
            if name not in matched:
                continue
            valid = matched[name].notna() & predictions["cell_type"].notna()
            agreement = matched.loc[valid, name].astype(str).to_numpy() == predictions.loc[valid, "cell_type"].astype(str).to_numpy()
            annotation[name] = {"cells": int(valid.sum()), "label_agreement": float(agreement.mean()) if len(agreement) else None}
            old_labels = set(matched.loc[valid, name].astype(str))
            new_labels = set(predictions.loc[valid, "cell_type"].astype(str))
            annotation[name]["label_vocabulary_overlap"] = {"shared": sorted(old_labels & new_labels),
                "reference_only": sorted(old_labels-new_labels), "new_annotation_only": sorted(new_labels-old_labels)}
            if "conf_score" in matched:
                confident = valid & (matched["conf_score"] >= .8)
                annotation[name]["reference_confidence_ge_0_8_cells"] = int(confident.sum())
                annotation[name]["reference_confidence_ge_0_8_agreement"] = float(np.mean(
                    matched.loc[confident, name].astype(str).to_numpy() == predictions.loc[confident, "cell_type"].astype(str).to_numpy())) if confident.any() else None
        report["annotation"] = annotation
    return report


def mixing_metrics(representation, batches, prior_types, *, seed):
    import numpy as np
    from sklearn.neighbors import NearestNeighbors
    from .integration_metrics import full_silhouette_metrics

    matrix = np.asarray(representation)
    batches = np.asarray(batches).astype(str)
    levels, encoded = np.unique(batches, return_inverse=True)
    k = min(15, len(matrix)-1)
    if k < 1 or not np.isfinite(matrix).all():
        raise ValueError("Mixing metrics require finite embeddings and multiple cells")
    neighbors = NearestNeighbors(n_neighbors=k+1, n_jobs=1).fit(matrix).kneighbors(matrix, return_distance=False)
    same, entropy = [], []
    for index, row in enumerate(neighbors):
        chosen = row[row != index][:k]
        same.append(float(np.mean(batches[chosen] == batches[index])))
        proportions = np.bincount(encoded[chosen], minlength=len(levels))/len(chosen)
        proportions = proportions[proportions > 0]
        entropy.append(float(-np.sum(proportions*np.log(proportions))/np.log(len(levels))) if len(levels)>1 else 0.0)
    result = {"neighbors": k, "same_batch_neighbor_fraction": float(np.mean(same)),
              "normalized_batch_neighbor_entropy": float(np.mean(entropy)),
              "scope": "descriptive_batch_mixing_with_prior_prediction_structure_not_biological_truth"}
    full=full_silhouette_metrics(matrix,batches,prior_types)
    result.update(batch_silhouette=full["batch_silhouette"],prior_celltype_silhouette=full["cell_type_ASW"],
                  batch_ASW=full["batch_ASW"],cell_type_ASW=full["cell_type_ASW"],ASW_details=full)
    return result


def _sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def run_concordance(input_path, output, *, counts_layer="counts", batch_key="batch",
                    min_genes=200, mad_limit=5.0, n_top_genes=2000, n_pcs=30,
                    seed=20260915, model_path=None, model_sha256=None,
                    max_matrix_bytes=4*1024**3):
    import numpy as np
    import pandas as pd
    from .io import open_h5ad
    from .pipeline import write_json
    from .resources import StageMeasurement
    from .singlecell import quality_control, detect_doublets, normalize_and_embed, annotate_celltypist, validate_counts

    source, output = Path(input_path), Path(output)
    if not source.is_absolute() or not output.is_absolute():
        raise ValueError("Concordance input/output paths must be absolute")
    if bool(model_path) != bool(model_sha256):
        raise ValueError("A model path and independently retained model hash must be supplied together")
    output.mkdir(parents=True, exist_ok=False)
    config = {"input_path": str(source), "input_sha256": _sha(source), "counts_layer": counts_layer,
              "batch_key": batch_key, "min_genes": min_genes, "mad_limit": mad_limit,
              "n_top_genes": n_top_genes, "n_pcs": n_pcs, "seed": seed,
              "model_path": str(model_path) if model_path else None, "model_sha256": model_sha256,
              "max_matrix_bytes": max_matrix_bytes, "prior_predictions_used_for_fitting": False}
    from importlib.metadata import version
    package = Path(__file__).resolve().parent
    config["implementation_sha256"] = {name: _sha(package / name) for name in
        ("concordance.py", "singlecell.py", "io.py", "resources.py", "pipeline.py")}
    config["runtime_versions"] = {name: version(name) for name in
        ("anndata", "scanpy", "numpy", "scipy", "pandas", "harmonypy", "igraph", "leidenalg", "umap-learn")}
    write_json(output / "configuration.json", config)
    stages, analyses = [], {}
    phase = None
    try:
        with StageMeasurement("load_counts_and_separate_reference_annotations") as phase:
            with open_h5ad(source, layer=counts_layer) as backed:
                reference = backed.obs.copy()
                if batch_key not in reference or reference[batch_key].isna().any():
                    raise ValueError("Observed batch identities are required")
                data = backed.subset(max_bytes=max_matrix_bytes)
            # Old QC, clusters, labels, scores and doublet flags do not enter fitting.
            data.obs = pd.DataFrame({batch_key: reference[batch_key]}, index=data.obs_names)
            validate_counts(data.X)
            if np.max(data.X.data, initial=0) <= 2**24:
                data.X = data.X.astype(np.float32)  # exact representation of admitted integer counts
            reference.to_csv(output / "prior-predictions.csv")
        stages.append(phase.result)
        with StageMeasurement("rerun_QC_and_doublet_detection") as phase:
            data, qc = quality_control(data, batch_key=batch_key, min_genes=min_genes, mad_limit=mad_limit)
            data, doublets = detect_doublets(data, batch_key=batch_key, seed=seed)
        stages.append(phase.result)
        write_json(output / "qc.json", qc)
        write_json(output / "doublets.json", doublets)
        data.obs.to_csv(output / "rerun-QC.csv")
        matched = reference.loc[data.obs_names]
        qc_comparison = {}
        for column in ("total_counts", "n_genes_by_counts", "log1p_total_counts", "pct_counts_mt"):
            if column in matched:
                difference = data.obs[column].to_numpy() - matched[column].to_numpy()
                qc_comparison[column] = {"mean_absolute_difference": float(np.mean(np.abs(difference))),
                                         "maximum_absolute_difference": float(np.max(np.abs(difference)))}
        write_json(output / "QC-concordance.json", qc_comparison)
        for correction in (False, True):
            name = "harmony" if correction else "uncorrected"
            with StageMeasurement(name + "_clustering") as phase:
                processed, method = normalize_and_embed(data, n_top_genes=n_top_genes, n_pcs=n_pcs,
                    seed=seed, batch_key=batch_key, correct_batch=correction)
                labels = processed.obs[["leiden"]].copy()
                if model_path:
                    labels["cell_type"], labels["cell_type_score"], annotation = annotate_celltypist(
                        processed, Path(model_path), expected_sha256=model_sha256)
                else:
                    annotation = {"status": "unavailable_model_not_rerun"}
                report = compare_predictions(reference, labels)
                latent = processed.obsm[method["neighbor_representation"]]
                report["batch_mixing"] = mixing_metrics(latent, processed.obs[batch_key],
                    matched["celltypes"] if "celltypes" in matched else None, seed=seed)
                report["method"] = method
                report["annotation_method"] = annotation
                labels.to_csv(output / (name + "-predictions.csv"))
                np.save(output / (name + "-PCA.npy"), latent, allow_pickle=False)
                embedding = pd.DataFrame(processed.obsm["X_umap"], index=processed.obs_names, columns=["UMAP_1", "UMAP_2"])
                embedding.to_csv(output / (name + "-UMAP.csv"))
                analyses[name] = report
                del processed
                gc.collect()
            stages.append(phase.result)
            write_json(output / (name + "-report.json"), report)
            write_json(output / "measurements.json", stages)
            print(json.dumps({"completed": name, "cells": len(labels)}), flush=True)
        summary = {"scope": "concordance_not_experimental_accuracy", "input_unchanged": _sha(source) == config["input_sha256"],
                   "batch_count": int(reference[batch_key].nunique()), "prior_prediction_columns_excluded_from_fitting": True,
                   "cells_input": len(reference), "cells_after_QC": data.n_obs, "conditions": analyses,
                   "annotation_complete": bool(model_path), "no_reference_dataset_downloads": True,
                   "limitations": ["Existing labels and clusters are earlier computational predictions, not experimental truth.",
                       "Repeated cells are not independent biological replicates.",
                    "Higher batch mixing alone does not establish better biology or a causal batch adjustment."]}
        summary["reference_provenance"] = "fixed_external_predictions; original classifier absent; no reproduction claim"
        summary["disagreement_attribution"] = "neither_annotation_or_clustering_is_established_as_ground_truth"
        summary["source_unchanged"] = all(_sha(package / name) == expected
                                         for name, expected in config["implementation_sha256"].items())
        if not summary["source_unchanged"] or not summary["input_unchanged"]:
            raise RuntimeError("Input or implementation changed during concordance measurement")
        write_json(output / "report.json", summary)
        return summary
    except Exception as error:
        if phase is not None and phase.result and (not stages or phase.result is not stages[-1]):
            stages.append(phase.result)
        write_json(output / "failure.json", {"type": type(error).__name__, "message": str(error)[:2000]})
        raise
    finally:
        write_json(output / "measurements.json", stages)


def main():
    from .resources import configure_threads
    configure_threads()
    from devharness_core.watchdog import run_sandboxed
    parser = argparse.ArgumentParser(description="Bounded local concordance with existing predictions")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--model-sha256")
    parser.add_argument("--timeout-sec", type=int, default=900)
    parser.add_argument("--memory-mb", type=int, default=8192)
    args = parser.parse_args()
    try:
        result = run_sandboxed(_command_worker, (str(args.input), str(args.out),
            str(args.model) if args.model else None, args.model_sha256),
            timeout_sec=args.timeout_sec, max_mem_mb=args.memory_mb)
    except Exception as error:
        from .pipeline import write_json
        args.out.mkdir(parents=True, exist_ok=True)
        write_json(args.out / "driver-failure.json", {"type": type(error).__name__, "message": str(error)[:2000],
            "configured_memory_limit_MB": args.memory_mb, "configured_timeout_seconds": args.timeout_sec})
        raise
    print(json.dumps(result, indent=2, allow_nan=False))


def _command_worker(source, output, model, model_hash):
    return run_concordance(source, output, model_path=model, model_sha256=model_hash)


if __name__ == "__main__":
    main()
