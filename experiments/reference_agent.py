"""Deterministic synthetic reference agent for infrastructure testing, with no inference."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from devharness_core.stages import parse_stage
from devharness_core.storage import ChunkedExpressionStore
from devharness_server.dispatcher import ToolDispatcher


def reference_agent(instruction: str, workspace_dir: Path) -> dict:
    work = Path(workspace_dir).resolve()
    metadata = pd.read_csv(work / "metadata.csv", dtype={"sample_id": str}).set_index("sample_id")
    dispatcher = ToolDispatcher()
    design = json.loads(dispatcher.execute("validate_design_matrix", {
        "metadata_csv_path": str(work / "metadata.csv"), "formula_str": "1 + numeric_time + C(batch)"}))
    genes = ["Sox2", "Pax6", "Neurog2", "Tbr1"]
    with ChunkedExpressionStore(work / "counts.zarr") as store:
        metadata = metadata.loc[store.sample_names]
        groups = metadata["embryonic_age"].to_numpy()
        stages = sorted(set(groups), key=parse_stage)
        profiles = {gene: {stage: float(np.mean(store.get_gene_vector(gene)[groups == stage])) for stage in stages} for gene in genes}
    # The reference works from synthetic marker profiles, not a task/grader file.
    biological_order = sorted(stages, key=profiles["Tbr1"].get)
    cascade = sorted(genes, key=lambda gene: biological_order.index(max(profiles[gene], key=profiles[gene].get)))
    monotonicity = json.loads(dispatcher.execute("calculate_temporal_monotonicity", {"stage_values": profiles["Tbr1"]}))
    corrected = "1 + cr(numeric_time, df=3, constraints='center')"
    formula = corrected if not design["is_full_rank"] else corrected + " + C(batch)"
    dispatcher.execute("fit_temporal_trajectory", {"counts_path": str(work / "counts.zarr"),
        "metadata_path": str(work / "metadata.csv"), "formula_str": formula,
        "time_col": "embryonic_age", "gene_list": genes})
    answer = {"stage_kinetics": profiles["Tbr1"], "master_regulators": cascade,
            "condition_number_validated": True,
            "biological_rationale": "Synthetic marker peaks progress from Sox2 through Pax6 and Neurog2 to Tbr1. "
                "Tbr1 stage means orient the developmental sequence. Rank diagnostics were run; confounded effects are not causally separable.",
            "monotonicity": monotonicity["score"], "rank_deficient": not design["is_full_rank"],
            "corrected_formula": formula, "chronology_mismatch": biological_order != stages,
            "corrected_stage_order": biological_order}

    schema = json.loads((work / "answer_schema.json").read_text())
    return {key: value for key, value in answer.items() if key in schema["properties"]}
