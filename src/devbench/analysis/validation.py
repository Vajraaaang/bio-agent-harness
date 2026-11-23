"""Separate known-truth scoring; never imported by the analysis notebook.

The engineering thresholds are prespecified before the first local rung-1 run.
They are not modifications to the benchmark grader or clinical acceptance limits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROTOCOL = {
    "version": "workbench.synthetic-recovery/2",
    "scope": "two_cohort_engineering_check_not_general_biological_validation",
    "minimum_coverage": 1.0,
    "minimum_direction_accuracy": 0.8,
    "maximum_effect_rmse_log2": 0.6,
    "maximum_null_false_positive_rate": 0.1,
    "maximum_false_discovery_proportion": 0.1,
    "minimum_signal_recall": 0.5,
    "direction_minimum_true_effect_log2": 0.25,
    "q_threshold": 0.05,
    "inference_policy": "bonferroni_selected_candidate_then_BH/1",
    "method_options": {"spline_candidates": [3, 5, 8], "selection_criterion": "aic",
                       "timeout_sec": 60, "max_mem_mb": 2048},
}


def score_known_truth(result, truth, target_id):
    """Compare aligned claims with simulation truth after observed-data analysis."""
    import numpy as np

    target = truth["targets"][target_id]
    matches = [x for x in result["target_results"] if x["target_id"] == target_id]
    if len(matches) != 1:
        raise ValueError("Exactly one result must match the requested target")
    fragment = matches[0]
    base = {"protocol": PROTOCOL, "target_id": target_id,
            "scope": "synthetic_known_truth_only", "thresholds_are_clinically_validated": False}
    if target["expected_refusal"]:
        design = result.get("independent_design_check", {})
        proven = (type(design.get("rank")) is int and type(design.get("columns")) is int
                  and 0 < design["rank"] < design["columns"])
        error = result.get("diagnostics", {}).get("analysis_error", {}).get("code")
        correct = (fragment["claim_type"] == "refusal" and not fragment.get("payload")
                   and error == "rank_deficient_design" and proven
                   and "rank_deficient_design" in fragment.get("limitation_codes", []))
        return {**base, "passed": bool(correct), "metrics": {"correct_refusal": bool(correct)},
                "independent_design_check": design}
    genes = truth["gene_ids"]
    expected = np.asarray(target["effect"], dtype=float)
    nonnull = np.asarray(target["nonnull"], dtype=bool)
    if expected.ndim != 2 or expected.shape[0] != len(genes) or nonnull.shape != (len(genes),):
        raise ValueError("Reference arrays do not align with gene identities")
    if len(set(genes)) != len(genes) or not np.isfinite(expected).all():
        raise ValueError("Reference identities must be unique and estimates finite")
    lookup = {gene: i for i, gene in enumerate(genes)}
    estimates = np.full(expected.shape, np.nan)
    q_values = np.full(len(genes), np.nan)
    seen = set()
    for row in (fragment.get("payload") or {}).get("gene_statistics", []):
        gene = row["gene_id"]
        if gene not in lookup or gene in seen:
            raise ValueError("Unknown or duplicated result gene")
        seen.add(gene)
        if row["estimate_status"] != "estimated":
            continue
        coordinates = [f"grid_{i}" for i in range(expected.shape[1])]
        if row["coordinate_ids"] != coordinates or len(row["values"]) != len(coordinates):
            raise ValueError("Result coordinate order/dimensions do not match the reference")
        values = np.asarray(row["values"], dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Estimated effects must be finite")
        p, q = row["p_value"], row["q_value"]
        if (type(p) not in (int, float) or type(q) not in (int, float)
                or not np.isfinite([p, q]).all() or not 0 <= p <= q <= 1):
            raise ValueError("Estimated p/q values must be finite with 0 <= p <= q <= 1")
        estimates[lookup[gene]] = values
        q_values[lookup[gene]] = q
    complete = np.isfinite(estimates).all(axis=1) & np.isfinite(q_values)
    signal = (np.abs(expected) >= PROTOCOL["direction_minimum_true_effect_log2"]) & complete[:, None]
    discoveries = complete & (q_values <= PROTOCOL["q_threshold"])
    metrics = {
        "genes_expected": len(genes), "genes_estimated": int(complete.sum()),
        "coverage": float(complete.mean()),
        "direction_coordinates": int(signal.sum()),
        "direction_accuracy": float(np.mean(np.sign(estimates[signal]) == np.sign(expected[signal]))) if signal.any() else None,
        "effect_rmse_log2": float(np.sqrt(np.mean((estimates[complete] - expected[complete])**2))) if complete.any() else None,
        "null_genes": int((~nonnull).sum()),
        "false_positives_on_nulls": int((discoveries & ~nonnull).sum()),
        "null_false_positive_rate": float((discoveries & ~nonnull).sum()/(~nonnull).sum()) if (~nonnull).any() else None,
        "discoveries": int(discoveries.sum()),
        "false_discovery_proportion": float((discoveries & ~nonnull).sum()/max(1, discoveries.sum())),
        "signal_recall": float((discoveries & nonnull).sum()/nonnull.sum()) if nonnull.any() else None,
    }
    checks = {"complete_gene_coverage": metrics["coverage"] >= PROTOCOL["minimum_coverage"]}
    for name, threshold, direction in (
        ("direction_accuracy", "minimum_direction_accuracy", "minimum"),
        ("effect_rmse_log2", "maximum_effect_rmse_log2", "maximum"),
        ("null_false_positive_rate", "maximum_null_false_positive_rate", "maximum"),
        ("false_discovery_proportion", "maximum_false_discovery_proportion", "maximum"),
        ("signal_recall", "minimum_signal_recall", "minimum"),
    ):
        value = metrics[name]
        checks[name] = value is not None and (value >= PROTOCOL[threshold] if direction == "minimum" else value <= PROTOCOL[threshold])
    return {**base, "passed": bool(all(checks.values())), "checks": checks, "metrics": metrics,
            "uncertainty_scope": "single_realized_cohort_FDP_is_not_repeated_sampling_FDR_control"}


def analyze_observed_inputs(inputs):
    """Observed-data boundary: no case ID, seed, truth or private path enters it."""
    import numpy as np
    import pandas as pd
    from devbench.study.biology_tools_bulk import analyze_bulk_tool

    task = json.loads((inputs / "task_inputs.json").read_text())
    targets = {q["target_id"] for q in task["questions"]}
    if "sampled_mean" in targets:
        target_id, tool = "sampled_mean", "fit_temporal_trajectory"
    elif "adjusted_effect" in targets:
        target_id, tool = "adjusted_effect", "mean_condition_analysis"
    else:
        raise ValueError("This validation entry point supports temporal or adjusted-condition targets")
    result = analyze_bulk_tool(tool, task, counts_path=str(inputs / task["expression_path"]),
        metadata_path=str(inputs / task["metadata_path"]),
        annotations_path=str(inputs / task["public_annotations_path"]),
        options=PROTOCOL["method_options"], allow_data_execution=True)
    if target_id == "adjusted_effect":
        meta = pd.read_csv(inputs / task["metadata_path"])
        # This separately checks observed aliasing, without relying on a truth flag.
        design = pd.get_dummies(meta[["condition", "batch"]].astype(str), drop_first=True, dtype=float)
        design.insert(0, "intercept", 1.0)
        result["independent_design_check"] = {"rank": int(np.linalg.matrix_rank(design.to_numpy())),
                                               "columns": len(design.columns),
                                               "column_names": list(design.columns),
                                               "observations": len(design)}
    return target_id, result


def _write(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def run_validation(bundle, out):
    from devbench.analysis.resources import StageMeasurement

    bundle, out = Path(bundle), Path(out)
    if not bundle.is_absolute() or not out.is_absolute():
        raise ValueError("Paths must be absolute")
    out.mkdir(parents=True, exist_ok=False)
    _write(out / "protocol.json", PROTOCOL)
    stages = []
    stage = None
    try:
        with StageMeasurement("observed_input_analysis") as stage:
            target_id, result = analyze_observed_inputs(bundle / "inputs")
        stages.append(stage.result)
        _write(out / "analysis-original.json", result)
        from devbench.analysis.inference import adjust_selected_model_tests
        with StageMeasurement("model_search_multiplicity") as stage:
            result = adjust_selected_model_tests(result)
        stages.append(stage.result)
        _write(out / "analysis.json", result)
        # Truth is opened only after the modeling boundary returned its result.
        with StageMeasurement("separate_truth_scoring") as stage:
            truth_bytes = (bundle / "private/truth.json").read_bytes()
            scorecard = score_known_truth(result, json.loads(truth_bytes), target_id)
        stages.append(stage.result)
        _write(out / "scorecard.json", scorecard)
        _write(out / "reference-identity.json", {"sha256": hashlib.sha256(truth_bytes).hexdigest(),
                "scope": "coordinator_scoring_only_not_model_input"})
    except Exception as error:
        if stage is not None and stage.result and (not stages or stage.result is not stages[-1]):
            stages.append(stage.result)
        _write(out / "failure.json", {"type": type(error).__name__, "message": str(error)[:2000]})
        raise
    finally:
        _write(out / "measurements.json", stages)
    return scorecard


def main():
    from devbench.analysis.resources import configure_threads
    configure_threads()
    parser = argparse.ArgumentParser(description="Local known-truth workbench validation; no model calls")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run_validation(args.bundle, args.out)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
