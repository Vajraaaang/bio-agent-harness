"""Prospective Gaussian selection diagnostic, outside the production package."""
import os
for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"

import argparse
import hashlib
import importlib.metadata
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta, f

from devharness_core.provenance import ExecutionDAG, canonical_json, verify_execution
from devharness_core.storage import ChunkedExpressionStore
from devharness_server.tools_stat import _candidate_designs, fit_temporal_trajectory

CRITERIA = ("aic", "bic", "gcv")


def save(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metadata(n):
    return pd.DataFrame({
        "sample_id": [f"S{i}" for i in range(n)],
        "specimen_id": [f"U{i}" for i in range(n)],
        "time": np.repeat(np.linspace(0, 1, 6), n // 6),
        "numeric_time": np.repeat(np.linspace(0, 1, 6), n // 6),
        "batch": np.tile(["B0", "B1"], n // 2),
    })


class Geometry:
    def __init__(self, meta):
        self.meta = meta
        self.n = len(meta)
        self.designs, rejected = _candidate_designs(meta, "1 + C(batch) + numeric_time", [2, 3, 4, 5])
        if rejected or sorted(self.designs) != [2, 3, 4, 5]:
            raise ValueError("The prespecified candidate set is not entirely feasible")
        self.q, self.null_q, self.ranks, self.temporal, self.records = [], None, [], [], []
        for df, (formula, frame, diagnostics) in sorted(self.designs.items()):
            if not diagnostics["is_full_rank"] or diagnostics["condition_number"] > 1000:
                raise ValueError("Unqualified rank or conditioning")
            x = np.asarray(frame, dtype=float)
            temporal = [i for term, sl in frame.design_info.term_name_slices.items()
                        if "numeric_time" in term for i in range(sl.start, sl.stop)]
            null = np.delete(x, temporal, axis=1)
            q, _ = np.linalg.qr(x, mode="reduced")
            q0, r0 = np.linalg.qr(null, mode="reduced")
            if np.linalg.matrix_rank(r0) != null.shape[1]:
                raise ValueError("Reduced design is rank deficient")
            if self.null_q is None:
                self.null_q = q0
            elif not np.allclose(q0 @ q0.T, self.null_q @ self.null_q.T, atol=1e-12, rtol=0):
                raise ValueError("Candidates do not share the same null space")
            if not np.allclose(q @ q.T @ q0, q0, atol=1e-12, rtol=0):
                raise ValueError("Reduced space is not contained in each candidate")
            self.q.append(q)
            self.ranks.append(x.shape[1])
            self.temporal.append(len(temporal))
            self.records.append({"df": df, "formula": formula, "columns": list(frame.columns),
                                 "matrix": x.tolist(), "diagnostics": diagnostics})

    def evaluate(self, y):
        y = np.asarray(y, dtype=float)
        if y.ndim != 2 or y.shape[0] != self.n or not np.isfinite(y).all():
            raise ValueError("Finite sample-by-replicate input required")
        if np.any(np.ptp(y, axis=0) == 0):
            raise ValueError("Constant input has no residual-variance information")
        residual0 = y - self.null_q @ (self.null_q.T @ y)
        rss0 = np.sum(residual0 * residual0, axis=0)
        residuals = [y - q @ (q.T @ y) for q in self.q]
        rss = np.asarray([np.sum(e * e, axis=0) for e in residuals])
        reference_scale = np.maximum(np.sum((y - y.mean(axis=0)) ** 2, axis=0), np.finfo(float).tiny)
        if np.any(rss <= 1e-12 * reference_scale):
            raise ValueError("Residual variance is numerically unresolved")
        improvement = rss0 - rss
        if np.any(improvement < -1e-10 * reference_scale):
            raise ValueError("Nested-model residual arithmetic failed")
        ranks = np.asarray(self.ranks)[:, None]
        df_num = np.asarray(self.temporal)[:, None]
        statistic = (np.maximum(improvement, 0) / df_num) / (rss / (self.n - ranks))
        conditional = f.sf(statistic, df_num, self.n - ranks)
        minus2ll = self.n * (np.log(2 * np.pi) + 1 + np.log(rss / self.n))
        scores = {"aic": minus2ll + 2 * (ranks + 1),
                  "bic": minus2ll + np.log(self.n) * (ranks + 1),
                  "gcv": self.n * rss / (self.n - ranks) ** 2}
        if not np.isfinite(statistic).all() or not np.isfinite(conditional).all():
            raise ValueError("Nonfinite Gaussian statistic")
        result = {"fixed_df3_p": conditional[1]}
        for criterion, score in scores.items():
            # Rows are ordered by df, matching the worker's score-then-df tie rule.
            selected = np.argmin(score, axis=0)
            index = np.arange(y.shape[1])
            result[criterion + "_p"] = conditional[selected, index]
            result[criterion + "_df"] = np.asarray([2, 3, 4, 5])[selected]
            result[criterion + "_f"] = statistic[selected, index]
        return result


def mc_pvalue(observed, reference):
    observed, reference = np.asarray(observed), np.asarray(reference)
    if observed.ndim != 1 or reference.ndim != 1 or not len(reference):
        raise ValueError("Nonempty one-dimensional reference required")
    if (not np.isfinite(observed).all() or not np.isfinite(reference).all()
            or np.any((observed < 0) | (observed > 1))
            or np.any((reference < 0) | (reference > 1))):
        raise ValueError("Finite probabilities required")
    return (1 + np.searchsorted(np.sort(reference), observed, side="right")) / (len(reference) + 1)


def generated(geometry, count, seed, scenario, chunk_size):
    rng = np.random.default_rng(seed)
    pieces = {}
    t = geometry.meta.numeric_time.to_numpy()
    batch = (geometry.meta.batch == "B1").to_numpy(dtype=float)
    mean = 10 + 2 * batch
    sd = np.ones(geometry.n)
    if scenario == "heteroskedastic_null":
        sd = 0.5 + 3.5 * t
    if scenario in ("linear_signal", "oscillatory_signal"):
        curve = t if scenario == "linear_signal" else np.sin(2 * np.pi * t)
        mean = mean + 0.5 * (curve - curve.mean()) / curve.std()
    first = None
    for start in range(0, count, chunk_size):
        size = min(chunk_size, count - start)
        z = rng.normal(size=(geometry.n, size))
        y = z if scenario == "reference" else mean[:, None] + sd[:, None] * z
        if first is None:
            first = y[:, :5].copy()
        for key, values in geometry.evaluate(y).items():
            pieces.setdefault(key, []).append(values)
    return {key: np.concatenate(values) for key, values in pieces.items()}, first


def binomial_record(values, alpha):
    count, n = int(np.sum(values <= alpha)), len(values)
    return {"rejected": count, "replicates": n, "rate": count / n,
            "exact_binomial_interval_95": [
                float(beta.ppf(.025, count, n - count + 1)) if count else 0.0,
                float(beta.ppf(.975, count + 1, n - count)) if count < n else 1.0]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    protocol, freeze = json.loads(args.protocol.read_bytes()), json.loads(args.freeze.read_bytes())
    for name, expected in freeze["files"].items():
        if sha(Path(name)) != expected:
            raise ValueError("Frozen input changed: " + name)
    args.output.mkdir(exist_ok=False)
    started = time.monotonic()
    dag = ExecutionDAG(args.output / "audit", actor_id="synthetic-diagnostic-coordinator")
    initial = dag.append("diagnostic_inputs", {"freeze": dag.add_artifact(args.freeze),
                                              "protocol": dag.add_artifact(args.protocol),
                                              "prototype": dag.add_artifact(Path(__file__))})
    save(args.output / "environment.json", {
        "versions": {name: importlib.metadata.version(name) for name in
                     ("numpy", "pandas", "scipy", "statsmodels", "patsy", "zarr", "devharness")},
        "thread_bounds": {name: os.environ[name] for name in
                          ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")},
    })
    environment_node = dag.append("diagnostic_environment",
        {"environment": dag.add_artifact(args.output / "environment.json")}, parents=[initial])
    results, checks = [], []
    for setting_index, n in enumerate(protocol["sample_sizes"]):
        geometry = Geometry(metadata(n))
        setting = args.output / f"n{n}"
        setting.mkdir()
        save(setting / "designs.json", geometry.records)
        reference, _ = generated(geometry, protocol["reference_draws"],
                                 protocol["seed"] + setting_index * 100,
                                 "reference", protocol["chunk_size"])
        np.savez_compressed(setting / "reference.npz", **reference)
        reference_node = dag.append("null_reference", {
            "design": dag.add_artifact(setting / "designs.json"),
            "statistics": dag.add_artifact(setting / "reference.npz"),
            "sample_size": n,
        }, parents=[environment_node])
        worker_y = None
        for scenario_index, scenario in enumerate(protocol["scenarios"], 1):
            observed, first = generated(geometry, protocol["evaluation_draws"],
                                        protocol["seed"] + setting_index * 100 + scenario_index,
                                        scenario, protocol["chunk_size"])
            if scenario == "homoskedastic_null":
                worker_y = first
                transformed = 7 * first + (30 + 5 * (geometry.meta.batch == "B1").to_numpy())[:, None]
                invariant = geometry.evaluate(transformed)
                direct = geometry.evaluate(first)
                errors = {key: float(np.max(np.abs(invariant[key] - direct[key]))) for key in direct}
                if any(error > protocol["numeric_tolerance"] for error in errors.values()):
                    raise ValueError("Nuisance/scale invariance failed")
                checks.append({"n": n, "invariance_max_errors": errors})
            rows = []
            for criterion in CRITERIA:
                adjusted = mc_pvalue(observed[criterion + "_p"], reference[criterion + "_p"])
                observed[criterion + "_mc_p"] = adjusted
                raw = observed[criterion + "_p"] <= protocol["alpha"]
                corrected = adjusted <= protocol["alpha"]
                rows.append({"n": n, "scenario": scenario, "criterion": criterion,
                             "conditional": binomial_record(observed[criterion + "_p"], protocol["alpha"]),
                             "selection_mc": binomial_record(adjusted, protocol["alpha"]),
                             "paired_raw_only": int(np.sum(raw & ~corrected)),
                             "paired_mc_only": int(np.sum(~raw & corrected)),
                             "calibration_pool_shared": True,
                             "binomial_interval_scope": "Evaluation uncertainty conditional on this fixed simulated reference pool; not total Monte Carlo uncertainty"})
            np.savez_compressed(setting / (scenario + ".npz"), **observed)
            save(setting / (scenario + ".json"), rows)
            dag.append("scenario_evaluation", {"scenario": scenario,
                "statistics": dag.add_artifact(setting / (scenario + ".npz")),
                "summary": dag.add_artifact(setting / (scenario + ".json"))}, parents=[reference_node])
            results.extend(rows)

        # Independent implementation comparison against actual supervised workers.
        genes = [f"check_{i}" for i in range(worker_y.shape[1])]
        expression = setting / "worker-input.zarr"
        meta_path = setting / "worker-metadata.csv"
        ChunkedExpressionStore.from_dataframe_to_zarr(
            pd.DataFrame(worker_y.T, index=genes, columns=geometry.meta.sample_id), expression)
        geometry.meta.to_csv(meta_path, index=False)
        expected = geometry.evaluate(worker_y)
        for criterion in CRITERIA:
            actual = fit_temporal_trajectory(str(expression), str(meta_path),
                "1 + C(batch) + numeric_time", "time", genes,
                selection_criterion=criterion, spline_dfs=[2, 3, 4, 5],
                data_type="continuous", strict=True)
            save(setting / (criterion + "-worker.json"), actual)
            comparisons = []
            for i, gene in enumerate(genes):
                record = actual["genes"][gene]
                delta_p = abs(record["p_value"] - expected[criterion + "_p"][i])
                delta_f = abs(record["test_statistic"] - expected[criterion + "_f"][i])
                if (record["selected_df"] != expected[criterion + "_df"][i]
                        or delta_p > protocol["numeric_tolerance"]
                        or delta_f > protocol["numeric_tolerance"]):
                    raise ValueError("Actual worker disagrees with prototype")
                comparisons.append({"gene": gene, "p_error": float(delta_p), "f_error": float(delta_f)})
            checks.append({"n": n, "criterion": criterion, "actual_worker_comparisons": comparisons})
            dag.append("actual_worker_check", {
                "input": dag.add_artifact(expression), "metadata": dag.add_artifact(meta_path),
                "result": dag.add_artifact(setting / (criterion + "-worker.json")),
                "comparison": comparisons}, parents=[reference_node])
        print(json.dumps({"completed_sample_size": n, "elapsed_seconds": time.monotonic() - started}), flush=True)
    b, alpha = protocol["reference_draws"], protocol["alpha"]
    rank = math.floor(alpha * (b + 1))
    summary = {"scope": protocol["scope"], "results": results, "checks": checks,
        "reference_draws_per_design": b, "mc_p_value_minimum": 1 / (b + 1),
        "nominal_rejection_grid_level": rank / (b + 1),
        "conditional_type_I_rate_reference_pool_interval_95": [float(beta.ppf(.025, rank, b + 1 - rank)),
                                                               float(beta.ppf(.975, rank, b + 1 - rank))],
        "reference_pool_interval_scope": "Across independent simulated calibration pools under a continuous pivotal null; not a clinical acceptance interval",
        "elapsed_seconds": time.monotonic() - started, "clinical_release_allowed": False,
        "model_participant_calls": 0, "new_colab_runs": 0}
    for name, expected in freeze["files"].items():
        if sha(Path(name)) != expected:
            raise ValueError("Frozen input changed during study: " + name)
    save(args.output / "summary.json", summary)
    dag.append("diagnostic_complete", {"summary": dag.add_artifact(args.output / "summary.json")}, parents=[initial])
    receipt = dag.finalize()
    save(args.output / "receipt.json", receipt)
    verdict = verify_execution(receipt["manifest_path"], receipt["root_hash"])
    save(args.output / "audit-verification.json", verdict)
    if not verdict["valid"]:
        raise ValueError("Diagnostic DAG does not verify")
    print(json.dumps({"receipt": receipt, "audit_valid": verdict["valid"],
                      "elapsed_seconds": summary["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    main()
