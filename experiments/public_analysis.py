"""Local numerical reference workflow on explicitly staged public bulk snapshots."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from devharness_core.stages import parse_stage
from devharness_core.storage import ChunkedExpressionStore
from devharness_server.dispatcher import ToolDispatcher


def analyze_public_task(instruction: str, workspace: Path) -> dict:
    """Calculate observed endpoints and exploratory fits without using private goldens."""
    work = Path(workspace).resolve()
    inputs = json.loads((work / "task_inputs.json").read_text())
    metadata = pd.read_csv(work / "metadata.csv", dtype={"sample_id": str, "specimen_id": str})
    dispatcher = ToolDispatcher()
    quality = json.loads(dispatcher.execute("audit_expression_quality", {
        "counts_path": str(work / "counts.zarr"), "metadata_path": str(work / "metadata.csv")}))
    if quality["status"] == "fail":
        raise ValueError("Public reference inputs failed QC: " + json.dumps(quality["diagnostics"]))
    design = json.loads(dispatcher.execute("validate_design_matrix", {
        "metadata_csv_path": str(work / "metadata.csv"), "formula_str": "1 + numeric_time"}))
    if not design["is_full_rank"]:
        raise ValueError("Public reference time design is rank deficient")
    fit = json.loads(dispatcher.execute("fit_temporal_trajectory", {
        "counts_path": str(work / "counts.zarr"), "metadata_path": str(work / "metadata.csv"),
        "formula_str": "1 + numeric_time", "time_col": "embryonic_age",
        "gene_list": inputs["fit_genes"], "data_type": "continuous", "selection_criterion": "aic", "strict": True}))
    if fit["family"] != "gaussian" or fit["status"] != "ok":
        raise ValueError("Public RPKM workflow did not produce a fully successful continuous fit")
    with ChunkedExpressionStore(work / "counts.zarr") as store:
        metadata = metadata.set_index("sample_id").loc[store.sample_names].reset_index()
        stages = sorted(metadata.embryonic_age.unique(), key=parse_stage)
        means = {}
        for annotation in inputs["regulator_annotations"]:
            vector = store.get_gene_vector(annotation["id"])
            means[annotation["symbol"]] = np.asarray([float(np.mean(vector[metadata.embryonic_age == stage])) for stage in stages])
    tbr1 = means["Tbr1"]
    kinetics = (tbr1 - tbr1.min()) / np.ptp(tbr1) if np.ptp(tbr1) else np.zeros_like(tbr1)
    gene_statistics = {gene:{"pvalue": result["p_value"], "qvalue": result["padj"]} for gene,result in fit["genes"].items()}
    independent = ("specimen_id" in metadata and "specimen_provenance" in metadata
                   and not metadata.specimen_id.isna().any()
                   and not metadata.specimen_provenance.astype(str).str.contains("unverified|unknown", case=False).any())
    return {"stage_kinetics":dict(zip(stages,map(float,kinetics))), "master_regulators":list(means),
            "condition_number_validated":True, "peak_stages":{gene:stages[int(np.argmax(profile))] for gene,profile in means.items()},
            "gene_effects":{gene:float(np.log1p(profile[-1])-np.log1p(profile[0])) for gene,profile in means.items()},
            "stage_order":list(stages), "chronology_scope":"collection_stage_reference",
            "batch_known":bool("batch" in metadata and not metadata.batch.isna().any()
                               and not metadata.batch.astype(str).str.lower().isin(["unknown", ""]).any()),
            "independent_specimens_known":bool(independent), "gene_statistics":gene_statistics,
            "biological_rationale":"Measured mouse bulk RPKM replicate means define descriptive collection-stage peaks and changes. "
                "Gaussian spline candidates were compared by AIC with explicit QC, rank checks and BH-adjusted requested-gene tests. "
                "P-values condition on selected complexity and assumed independent columns; animal identities and technical batch are not verified. "
                "These are exploratory observational findings, not causal regulator validation or clinical biomarker evidence."}
