"""Bounded synthetic diagnostics through the registered trusted ToolDispatcher.

No fitting, private task data, grader access, model calls or method ranking.
"""
import json
from pathlib import Path


def main():
    import numpy as np
    import pandas as pd
    from devharness_server.dispatcher import ToolDispatcher, capture_tool_events

    output = Path(__file__).resolve().parent
    inputs = output / ".runtime-diagnostics"
    inputs.mkdir(exist_ok=False)
    samples = [f"synthetic_sample_{i:02}" for i in range(24)]
    time = np.repeat([8.5, 10.5], 12)
    rng = np.random.default_rng(128)
    counts = inputs / "counts.csv"
    pd.DataFrame(rng.poisson(20, (30, 24)), index=[f"gene_{i:02}" for i in range(30)], columns=samples).to_csv(counts)
    balanced = inputs / "balanced.csv"
    confounded = inputs / "confounded.csv"
    metadata = pd.DataFrame({"sample_id": samples, "embryonic_age": [f"E{value}" for value in time],
        "batch": ["a", "b"] * 12, "subject_id": [f"generated_subject_{i:02}" for i in range(24)]})
    metadata.to_csv(balanced, index=False)
    metadata["batch"] = ["a"] * 12 + ["b"] * 12
    metadata.to_csv(confounded, index=False)
    for path in (counts, balanced, confounded):
        path.chmod(0o444)
    evidence = {"scope": "Synthetic software diagnostic control; no fit or biological validation",
        "data": {"samples": 24, "genes": 30, "measurement": "generated Poisson integer counts", "seed": 128,
            "units": "24 separately generated synthetic units; subject declarations come from generator, not sample label uniqueness",
            "groups": "12 generated samples at each of two nominal stages; balanced batch versus fully confounded batch",
            "unknowns": "No real biological cohort, experimental quality or calibrated clinical diagnostic thresholds"},
        "objective": "Check recorded QC and detect a fully confounded design before any fit",
        "source_of_requirements": "User authorized experiments; exact simulation and formula are assistant-selected software controls",
        "criteria_frozen_before_execution": {"balanced_design": "full rank", "confounded_design": "rank deficient; do not infer a causal batch-adjusted age effect",
            "qc": "retain actual diagnostic status, including unavailable or warning findings"},
        "method_scope": "Pre-fit diagnostic checks only; no method selection, significance test or biological recommendation"}
    dispatcher = ToolDispatcher(allowed_root=inputs)
    with capture_tool_events() as events:
        evidence["qc"] = json.loads(dispatcher.execute("audit_expression_quality", {
            "counts_path": str(counts), "metadata_path": str(balanced), "time_col": "embryonic_age",
            "max_cells": 24, "top_genes": 30, "n_neighbors": 5, "seed": 128}))
        evidence["balanced_design"] = json.loads(dispatcher.execute("validate_design_matrix", {
            "metadata_csv_path": str(balanced), "formula_str": "1 + numeric_time + C(batch)", "time_col": "embryonic_age"}))
        evidence["confounded_design"] = json.loads(dispatcher.execute("validate_design_matrix", {
            "metadata_csv_path": str(confounded), "formula_str": "1 + numeric_time + C(batch)", "time_col": "embryonic_age"}))
    evidence["actual_tool_events"] = events
    evidence["checks"] = {"balanced_full_rank": bool(evidence["balanced_design"]["is_full_rank"]),
        "confounded_rank_deficient": not evidence["confounded_design"]["is_full_rank"], "actual_tool_calls": len(events)}
    evidence["conclusion"] = "Observed software controls only; no estimates fitted, no causal batch-adjusted effect claimed"
    (output / "diagnostic-demo-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"qc_status": evidence["qc"]["status"], **evidence["checks"]}))
    assert evidence["checks"]["balanced_full_rank"] and evidence["checks"]["confounded_rank_deficient"]


if __name__ == "__main__":
    main()
