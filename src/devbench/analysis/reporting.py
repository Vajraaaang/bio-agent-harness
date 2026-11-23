"""Descriptive scRNA reports with explicit reference and inference scopes."""
from __future__ import annotations

import json

import numpy as np

from .checkpoints import atomic_json
from .concordance import compare_predictions, mixing_metrics
from .streaming import read_table


METHOD_CITATIONS = {
    "QC": "https://scanpy.readthedocs.io/en/stable/generated/scanpy.pp.calculate_qc_metrics.html",
    "HVG": "https://scanpy.readthedocs.io/en/stable/generated/scanpy.pp.highly_variable_genes.html",
    "Harmony": "https://doi.org/10.1038/s41592-019-0619-0",
    "scVI": "https://doi.org/10.1038/s41592-018-0229-2",
    "Scrublet": "https://doi.org/10.1016/j.cels.2018.11.005",
    "Batch ASW": "https://scib.readthedocs.io/en/latest/api/scib.metrics.silhouette_batch.html",
}


def report_text(report):
    rows = ["# Single-cell analysis", "", f"Execution tier: `{report['execution_tier']}`.", "",
        f"QC retained {report['cells_retained']:,} of {report['cells_input']:,} cells; "
        f"{report['HVG_count']:,} selected genes were used for the embeddings.", "",
        "Both conditions use identical QC-passing cells, HVGs, PCA, neighbor settings, resolution and seed. "
        "Only the integration of the latent representation differs.", "",
        "| Condition | Clusters | ARI vs stored clusters | NMI vs stored clusters | Batch ASW | Cell-type ASW |",
        "|---|---:|---:|---:|---:|---:|"]
    def value(v):
        return "unavailable" if v is None else f"{v:.4f}"
    for name, condition in report["conditions"].items():
        cluster = condition["clustering"]
        rows.append(f"| {name} | {condition['clusters']} | {value(cluster.get('ARI'))} | "
            f"{value(cluster.get('NMI'))} | {value(condition['batch_mixing']['batch_ASW'])} | "
            f"{value(condition['batch_mixing']['cell_type_ASW'])} |")
    rows.extend(["", "Batch ASW is mean 1 − |batch silhouette| within each eligible stored cell-type group, "
        "then averaged equally across groups (0–1, higher means more mixing). Cell-type ASW is the signed "
        "silhouette against those stored predictions (−1 to 1, higher means greater separation). "
        "Distances are evaluated without subsampling in the PCA/Harmony embeddings, using bounded chunks. "
        "Batch ASW excludes reference types without an estimable within-type batch contrast; their coverage "
        "is reported below. Cell-type ASW uses all retained cells with available labels. UMAP is used for visualization only.", ""])
    for name,condition in report['conditions'].items():
        details=condition['batch_mixing']['ASW_details']
        rows.append(f"{name}: batch ASW covers {details['batch_ASW_eligible_cells']:,} cells across "
                    f"{details['eligible_reference_types']} eligible reference types; cell-type ASW has "
                    f"{details['reference_label_cells']:,} reference-labelled cells.")
    for name,change in report["integration_tradeoff"].items():
        rows.append(f"{name} versus uncorrected: batch ASW change {value(change['batch_ASW_delta'])}; "
                    f"cell-type ASW change {value(change['cell_type_ASW_delta'])}.")
    rows.extend(["", "Large mixing gains with little label-ASW change require inspection. Ten batches from one "
        "timepoint can differ biologically, and stored prediction labels may miss over-correction. "
        "Improvement in both proxies is not proof of safe biological integration.", "",
        "## QC agreement with stored columns", "",
        "These comparisons cover all input cells before filtering. Differences are findings, not automatic failures.", "",
        "| Metric | Stored median | Recomputed median | Stored p95 | Recomputed p95 | Matching / paired cells | Maximum absolute difference |",
        "|---|---:|---:|---:|---:|---:|---:|"])
    for name,field in report["QC_concordance"]["fields"].items():
        if field["status"]!="compared":
            rows.append(f"| {name} | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable |")
            continue
        old,new=field['reference_distribution'],field['recomputed_distribution']
        rows.append(f"| {name} | {value(old['median'])} | {value(new['median'])} | {value(old['p95'])} | "
                    f"{value(new['p95'])} | {field['matching_cells']} / {field['paired_finite_cells']} | "
                    f"{value(field['maximum_absolute_difference'])} |")
    confidence=report["reference_confidence_all_input"]
    if confidence:
        rows.extend(["", "Stored confidence scores for all input cells: " +
            ", ".join(f"q{q}={value(v)}" for q,v in confidence['quantiles'].items()) +
            ". Their calibration and original classifier provenance are unknown."])
    rows.extend(["", f"Batch integration: {report['integration']['execution_status']}. "
        f"Annotation: {report['annotation']['status']}. "
        f"Doublet detection: {report['doublets']['status']} (flags only).", "",
        "Existing cell types, Leiden clusters and confidence scores are fixed external computational "
        "predictions. Agreement is concordance, not accuracy. The original classifier is unavailable; "
        "this analysis does not reproduce it. Neither annotation is established as ground truth.", "",
        "Higher batch mixing alone does not establish preserved biology or a causal batch adjustment. "
        "Cells and technical batches are not automatically independent biological replicates.", "",
        "Selected raw counts, full-library QC totals, normalized expression and corrected embeddings "
        "are separate artifacts. The original H5AD is an external, hash-bound input and is not copied.", "",
        "This bundle is research evidence. Hashes and a numerical reviewer do not establish clinical "
        "validity, regulatory compliance or authenticated approval.", "", "Method references:", ""])
    rows.extend(f"- [{name}]({url})" for name, url in METHOD_CITATIONS.items())
    return "\n".join(rows)+"\n"


def render_concordance_figures(store, conditions, output, *, batch_key):
    from .figure_artifacts import export_figures
    return export_figures(store, conditions, output, batch_key=batch_key)


def compile_report(store, parameters, conditions):
    metrics = read_table(store.path("qc")/"metrics.csv")
    reference = read_table(store.path("qc")/"external-reference.csv")
    annotation = json.loads((store.path("annotation")/"report.json").read_text())
    predictions = read_table(store.path("annotation")/"predictions.csv")
    doublets = json.loads((store.path("doublets")/"report.json").read_text())
    analyses = {}
    for condition, latent_stage in conditions.items():
        table = read_table(store.path(condition+"_graph")/"embedding.csv")
        predicted = table[["leiden"]].copy()
        if annotation["status"] == "completed":
            predicted["cell_type"] = predictions.loc[table.index, "cell_type"]
        report = compare_predictions(reference, predicted)
        report["clusters"] = int(table.leiden.nunique())
        report["batch_mixing"] = mixing_metrics(
            np.load(store.path(latent_stage)/"latent.npy", allow_pickle=False),
            table[parameters["batch_key"]], reference.loc[table.index, "celltypes"] if "celltypes" in reference else None,
            seed=parameters["seed"])
        report["annotation_method"] = annotation
        analyses[condition] = report
    changes={}
    if "uncorrected" in analyses:
        baseline=analyses["uncorrected"]["batch_mixing"]
        for name,report in analyses.items():
            if name=="uncorrected":
                continue
            changes[name]={key+"_delta":(report['batch_mixing'][key]-baseline[key]
                if report['batch_mixing'][key] is not None and baseline[key] is not None else None)
                for key in ('batch_ASW','cell_type_ASW')}
    from .concordance import confidence_distribution
    integration=json.loads((store.path("integration_status")/"report.json").read_text())
    integration['execution_status']='completed' if any(name!='uncorrected' for name in conditions) else integration['status']
    return {"format": "devbench.scrna.report/1", "execution_tier": parameters["execution_tier"],
        "cells_input": len(metrics), "cells_retained": int(metrics.qc_pass.sum()),
        "HVG_count": int(read_table(store.path("hvg")/"features.csv").highly_variable.sum()),
        "observed_batches": int(metrics[parameters["batch_key"]].nunique()),
        "conditions": analyses, "annotation": annotation, "doublets": doublets,
        "integration": integration,
        "input_info": json.loads((store.path("qc")/"report.json").read_text()).get("input_info", {}),
        "QC_concordance":json.loads((store.path("qc")/"concordance.json").read_text()),
        "reference_confidence_all_input":confidence_distribution(reference['conf_score']) if 'conf_score' in reference else None,
        "integration_tradeoff":changes,
        "scope": "descriptive_concordance_with_external_predictions_not_accuracy",
        "classifier_reproduction_claimed": False, "clinical_validation_claimed": False}


def export_report(store, root, parameters, conditions):
    report = compile_report(store, parameters, conditions)
    atomic_json(root/"report.json", report)
    atomic_json(root/"citations.json", METHOD_CITATIONS)
    stages = [json.loads(path.read_text()) for path in sorted(store.root.glob("*/receipt.json"))]
    atomic_json(root/"measurements.json", {
        "execution_tier": parameters["execution_tier"],
        "stages": [{"stage": record["stage"], **record["measurement"]} for record in stages],
        "wall_seconds_sum_of_recorded_stages": sum(record["measurement"]["wall_seconds"] for record in stages),
        "sampled_peak_rss_bytes": max((record["measurement"]["sampled_peak_rss_bytes"] for record in stages), default=None),
        "scope": "numerical_stage_samples; setup_imports_hashing_and_review_not_in_summed_wall_time",
        "token_usage": None, "external_model_calls": 0,
    })
    (root/"summary.md").write_text(report_text(report))
    render_concordance_figures(store, conditions, root/"figures", batch_key=parameters["batch_key"])
    from .html_report import write_interactive_figures
    write_interactive_figures(store,conditions,root/"figures",batch_key=parameters["batch_key"])
    return report
