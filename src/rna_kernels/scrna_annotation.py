"""Mounted single-cell label transfer and independent-donor reliability estimates."""

from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from scipy import io, sparse

from devbench.analysis.singlecell import (
    marker_annotation,
    symbol_count_view,
    validate_counts,
)
from devbench.core._files import hash_bounded_file
from devbench.core.expression_io import open_expression
from devbench.core.scientific_types import content_sha256
from devbench.core.scrna_annotation import AnnotationParameters
from rna_kernels.canonical import canonical_json

Parameters = AnnotationParameters


def _read(path):
    with open_expression(path) as source:
        n, p = source.shape
        if not 1 <= n <= 100_000 or not 1 <= p <= 60_000:
            raise ValueError("annotation matrix exceeds mounted axis bound")
        source.validate_sparse_storage()
        data = source.subset(max_bytes=256 * 1024**2)
    validate_counts(data.X)
    return data


def _strings(frame, key):
    if key not in frame or frame[key].isna().any():
        raise ValueError(f"missing annotation field {key}")
    values = frame[key].astype(str)
    if any(not s.strip() for s in values):
        raise ValueError(f"empty annotation field {key}")
    return values


def _r_predict(query, reference, paths, params, temp):
    from shutil import copyfile

    # The subprocess receives counts and the fixed frozen reference fields only.
    for name, data in (("query", query), ("reference", reference)):
        io.mmwrite(temp / (name + ".mtx"), sparse.coo_matrix(data.X.T))
        pd.DataFrame({"gene": data.var_names}).to_csv(
            temp / (name + "_genes.csv"), index=False
        )
        pd.DataFrame({"cell": data.obs_names}).to_csv(
            temp / (name + "_cells.csv"), index=False
        )
    reference.obs[[params.label_key]].to_csv(temp / "labels.csv", index=False)
    if params.method == "azimuth":
        copyfile(paths["model"], temp / "ref.Rds")
        copyfile(paths["index"], temp / "idx.annoy")
    subprocess.run(
        [
            "Rscript",
            "--vanilla",
            str(Path(__file__).with_name("r") / "scrna_annotation.R"),
            params.method,
            str(temp),
            params.label_key,
        ],
        check=True,
        timeout=7200,
    )
    frame = pd.read_csv(
        temp / "predictions.csv", dtype=str, keep_default_na=False
    ).set_index("cell")
    if not frame.index.equals(query.obs_names):
        raise ValueError("R annotation returned mismatched cell identities")
    return frame.label.replace("", "uncertain")


def _predict(query, reference, paths, params, temp):
    method = params.method
    if method == "marker_panel":
        predictions, _ = marker_annotation(query, params.panels)
        return predictions
    if method == "celltypist":
        import celltypist

        # Train in the isolated runtime; no pickled model from the caller.
        train = symbol_count_view(reference)
        model = celltypist.train(train, labels=params.label_key, n_jobs=1)
        result = celltypist.annotate(
            symbol_count_view(query),
            model=model,
            mode="best match",
            majority_voting=False,
        )
        return result.predicted_labels["predicted_labels"]
    if method == "scarches":
        import scvi

        scvi.settings.seed = params.seed
        scvi.model.SCVI.setup_anndata(reference, batch_key=params.donor_key)
        base = scvi.model.SCVI(reference)
        base.train(max_epochs=params.reference_epochs, accelerator="cpu", devices=1)
        labeled = scvi.model.SCANVI.from_scvi_model(
            base, labels_key=params.label_key, unlabeled_category="__unknown__"
        )
        labeled.train(max_epochs=params.label_epochs, accelerator="cpu", devices=1)
        scvi.model.SCANVI.prepare_query_anndata(query, labeled)
        query.obs[params.label_key] = "__unknown__"
        transfer = scvi.model.SCANVI.load_query_data(query, labeled)
        transfer.train(
            max_epochs=params.query_epochs,
            accelerator="cpu",
            devices=1,
            plan_kwargs={"weight_decay": 0},
        )
        posterior = transfer.predict(soft=True)
        if not posterior.index.equals(query.obs_names):
            raise ValueError("query mapping lost cell identities")
        return posterior.idxmax(axis=1)
    if method == "popv":
        import popv

        source = popv.preprocessing.Process_Query(
            query,
            reference,
            ref_labels_key=params.label_key,
            ref_batch_key=params.donor_key,
            query_batch_key=params.donor_key,
            prediction_mode="retrain",
            cl_obo_folder=[
                str(paths[k])
                for k in ("ontology", "ontology_graph", "ontology_embedding")
            ],
            save_path_trained_models=str(temp),
        ).adata
        popv.annotation.annotate_data(source)
        result = source.obs.loc[source.obs["_dataset"] == "query", "popv_prediction"]
        # popV can filter low-count cells. Missing query predictions abstain.
        if not result.index.is_unique or not result.index.isin(query.obs_names).all():
            raise ValueError("popV returned unknown cell identities")
        return result.astype("object").reindex(query.obs_names).fillna("uncertain")
    return _r_predict(query, reference, paths, params, temp)


def _evaluate(predictions, donors, truth, classes, params):
    cal = donors.isin(params.calibration_donors)
    test = ~cal
    if params.calibration_donors and (
        not set(params.calibration_donors) <= set(donors) or not test.any()
    ):
        raise ValueError(
            "declared calibration donors require an independent test partition"
        )
    confidence = {}
    for label in sorted(set(predictions) - {"uncertain"}):
        group = cal & predictions.eq(label)
        # Equal donor weight; this estimates classwise precision on calibration
        # donors and does not treat thousands of cells as independent evidence.
        confidence[label] = (
            float(truth[group].eq(label).groupby(donors[group]).mean().mean())
            if truth is not None and group.any()
            else None
        )
    inferred = predictions.copy()
    if params.calibration_donors:
        inferred = inferred.where(
            predictions.map(confidence).fillna(0) >= params.min_confidence, "uncertain"
        )
        inferred = inferred.where(predictions.map(confidence).notna(), "uncertain")
    by_class = {}
    for label in sorted(
        set(classes)
        | (set(truth[test]) if truth is not None else set())
        | set(confidence)
    ):
        subset = (
            test & truth.eq(label)
            if truth is not None
            else test & predictions.eq(label)
        )
        by_class[label] = {
            "rare_in_reference": float(classes.eq(label).mean())
            <= params.rare_prevalence,
            "calibrated_confidence": confidence.get(label),
            "evaluated_cells": int(subset.sum()),
            "evaluated_donors": int(donors[subset].nunique()),
            "abstention_rate": float(inferred[subset].eq("uncertain").mean())
            if subset.any()
            else None,
            "held_out_recall": float(inferred[subset].eq(label).mean())
            if truth is not None and subset.any()
            else None,
        }
    stability = {}
    if truth is not None:
        for donor in sorted(set(donors[test])):
            subset = donors.eq(donor)
            recalls = inferred[subset].eq(truth[subset]).groupby(truth[subset]).mean()
            stability[donor] = {
                "cells": int(subset.sum()),
                "balanced_accuracy": float(recalls.mean()),
            }
    stable = (
        bool(params.calibration_donors)
        and len(stability) >= 2
        and all(
            s["balanced_accuracy"] >= params.min_stability for s in stability.values()
        )
        and all(
            s["evaluated_donors"] >= 2
            and s["held_out_recall"] is not None
            and s["held_out_recall"] >= params.min_stability
            for s in by_class.values()
        )
    )
    cells = [
        {
            "cell_id": str(cell),
            "donor": str(donors[cell]),
            "label": str(inferred[cell]),
            "candidate": str(predictions[cell]),
            "calibrated_confidence": confidence.get(predictions[cell]),
            "partition": "calibration" if cal[cell] else "held_out",
        }
        for cell in predictions.index
    ]
    return cells, by_class, stability, stable


def run_m2(op, paths, params, outputs):
    if op != "scrna_annotate_cells":
        raise ValueError("unknown annotation operation")
    p = Parameters.model_validate(params)
    additional = {
        "popv": {"ontology", "ontology_graph", "ontology_embedding"},
        "azimuth": {"model", "index"},
    }.get(p.method, set())
    if set(paths) != {"expression", "reference", *additional}:
        raise ValueError("incorrect method-specific annotation ports")
    query, reference = _read(paths["expression"]), _read(paths["reference"])
    donors = _strings(query.obs, p.donor_key)
    training = _strings(reference.obs, p.donor_key)
    classes = _strings(reference.obs, p.label_key)
    if set(training) & set(donors):
        raise ValueError("reference donors overlap query donors")
    if query.obs_names.intersection(reference.obs_names).size:
        raise ValueError("reference and query cell identities overlap")
    if classes.isin(["uncertain", "__unknown__"]).any():
        raise ValueError("reserved abstention label in reference")
    truth = _strings(query.obs, p.truth_key) if p.truth_key else None
    # A donor never contributes features or labels to another query donor's
    # adaptation. Remove evaluation labels before handing any data to a backend.
    query.obs = query.obs[[p.donor_key]].copy()
    reference.obs = reference.obs[[p.donor_key, p.label_key]].copy()
    if len(set(classes)) > 1024:
        raise ValueError("annotation reference exceeds class bound")
    results = []
    for donor in sorted(set(donors)):
        group = query[donors.eq(donor)].copy()
        with TemporaryDirectory() as temp:
            labels = _predict(group, reference.copy(), paths, p, Path(temp))
        if not labels.index.equals(group.obs_names) or labels.isna().any():
            raise ValueError("annotation labels must cover exact query axis")
        labels = labels.astype(str)
        abstained = (
            labels.str.strip()
            .str.casefold()
            .isin({"", "unknown", "__unknown__", "uncertain", "unassigned"})
        )
        results.append(labels.mask(abstained, "uncertain"))
    predictions = pd.concat(results).loc[query.obs_names]
    cells, by_class, stability, stable = _evaluate(
        predictions, donors, truth, classes, p
    )
    result = {
        "status": "completed",
        "method": p.method,
        "label_provenance": p.label_provenance,
        "provenance_basis": "supplied_reference_declaration",
        "input_sha256": {k: hash_bounded_file(v, 1 << 40) for k, v in paths.items()},
        "parameters_sha256": content_sha256(p.model_dump(mode="json")),
        "reference_donors": sorted(set(training)),
        "calibration_donors": sorted(p.calibration_donors),
        "per_class": by_class,
        "held_out_donor_stability": stability,
        "held_out_stability_passed": stable,
        "abstention_rate": sum(c["label"] == "uncertain" for c in cells) / len(cells),
        "per_class_denominator": "truth"
        if truth is not None
        else "predicted_candidate",
        "prediction_scale": "candidate_reference_transfer",
        "cells": cells,
        "evidence_level": "L1",
        "clinical_release_allowed": False,
        "e5_label_checks_passed": bool(
            stable
            and p.label_provenance != "internal_clustering"
            and p.method != "azimuth"
        ),
        "e5_promoted": False,
    }
    sidecar = {
        "axis_labels": {"x": "Cell type", "y": "Fraction of held-out cells abstained"},
        "transformations": [
            "Group held-out predictions by measured class when available."
        ],
        "sample_count": len(cells),
        "donor_count": len(set(donors)),
        "columns": ["class", "count", "fraction"],
        "rows": [
            [label, values["evaluated_cells"], values["abstention_rate"]]
            for label, values in by_class.items()
        ],
        "source_receipt_sha256": content_sha256(result),
    }
    paths_out = {}
    for port, payload in (("result", result), ("plot_sidecar", sidecar)):
        path = outputs / (port + ".json")
        with path.open("xb") as stream:
            stream.write(canonical_json(payload))
        paths_out[port] = path
    return paths_out
