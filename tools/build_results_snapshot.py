"""Build a portable, synthetic-only results snapshot from the retained diagnostic."""
import argparse
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plot_results(output, study):
    data = json.loads((output / "summary.json").read_bytes())
    # Standard scientific plotting; no external fonts, CDN, or runtime service.
    os.environ["MPLCONFIGDIR"] = str(study / "matplotlib-cache")
    os.environ["XDG_CACHE_HOME"] = str(study / "font-cache")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({"font.size": 10, "svg.fonttype": "none",
                         "svg.hashsalt": "devharness-selection-20260909",
                         "axes.spines.top": False, "axes.spines.right": False})
    figure, axes = plt.subplots(1, 2, figsize=(10, 5.6), sharey=True)
    labels = [f"n={n}  {criterion.upper()}" for n in (24, 48, 96) for criterion in ("aic", "bic", "gcv")]
    for axis, scenario, title in zip(axes, ("homoskedastic_null", "heteroskedastic_null"),
                                     ("Equal variance", "Unequal variance — stress case")):
        rows = [row for row in data["results"] if row["scenario"] == scenario]
        for method, offset, color, marker, label in (
            ("conditional", -.12, "#61666c", "s", "Conditional F"),
            ("selection_mc", .12, "#096c6b", "o", "Selection Monte Carlo")):
            rates = np.asarray([row[method]["rate"] for row in rows]) * 100
            intervals = np.asarray([row[method]["exact_binomial_interval_95"] for row in rows]) * 100
            axis.errorbar(rates, np.arange(9) + offset,
                          xerr=np.vstack((rates - intervals[:, 0], intervals[:, 1] - rates)),
                          fmt=marker, color=color, markersize=4, capsize=2, linewidth=1, label=label)
        axis.axvline(5, color="#aa5b39", linestyle="--", linewidth=1)
        axis.set_xlim(0, 16)
        axis.set_xticks([0, 5, 10, 15])
        axis.set_xlabel("Null rejection rate (%)")
        axis.set_title(title, loc="left", pad=14)
        axis.set_yticks(np.arange(9), labels)
        axis.grid(axis="x", color="#e6e6e6", linewidth=.6)
        axis.set_axisbelow(True)
    axes[0].invert_yaxis()
    figure.legend(*axes[0].get_legend_handles_labels(), frameon=False, loc="upper center", bbox_to_anchor=(.55, 1), ncol=2)
    figure.text(.08, .025, "20,000 evaluation draws per setting. Error bars: pointwise 95% intervals conditional on the reference pool.\nDashed line: nominal 5%. This is a synthetic diagnostic; unequal variance remains miscalibrated.",
                fontsize=9, color="#41464d")
    figure.tight_layout(rect=(0, .105, 1, .92))
    figure.savefig(output / "null-calibration.svg", metadata={"Date": None})
    figure.savefig(output / "null-calibration.png", dpi=180)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--refresh-plots", action="store_true")
    args = parser.parse_args()
    study, output = args.study.expanduser().resolve(), args.output.expanduser().resolve()
    project = Path(__file__).resolve().parents[1]
    if args.refresh_plots:
        plot_results(output, study)
        return
    output.mkdir(parents=True, exist_ok=False)
    original = json.loads((study / "main-02/summary.json").read_bytes())
    checks = json.loads((study / "coordinator-postcheck.json").read_bytes())
    if not checks["replay_numerical_arrays_identical"] or not all(row["valid"] for row in checks["audits"]):
        raise ValueError("Source verification is incomplete")
    if original["clinical_release_allowed"] is not False:
        raise ValueError("Unsupported clinical-release claim")
    command = json.loads((study / "main-02-command.json").read_bytes())
    data = {
        "schema_version": "devharness.public-research-snapshot/1",
        "title": "Gaussian spline-selection diagnostic",
        "software_version": "0.4.11",
        "synthetic_only": True,
        "clinical_release_allowed": False,
        "scope": original["scope"],
        "protocol": json.loads((study / "main-protocol.json").read_bytes()),
        "results": original["results"],
        "verification": {
            "replay_arrays_equal": checks["arrays_compared"],
            "replay_summary_equal_except_elapsed": checks["summary_equal_excluding_elapsed_seconds"],
            "saved_execution_roots_verified": len(checks["audits"]),
            "main_worker_comparisons": sum(len(row.get("actual_worker_comparisons", [])) for row in original["checks"]),
            "maximum_sampled_worker_error": max(
                max(pair["p_error"], pair["f_error"])
                for row in original["checks"] for pair in row.get("actual_worker_comparisons", [])),
        },
        "timing": {"diagnostic_seconds": original["elapsed_seconds"],
                   "supervised_command_seconds": command["elapsed_seconds"]},
        "memory": {
            "max_sampled_driver_rss_bytes": command["max_sampled_driver_rss_bytes"],
            "sample_count": command["sample_count"],
            "incomplete_samples": command["incomplete_samples"],
            "aggregate_peak_bytes": None,
            "scope": command["memory_scope"],
        },
        "reference_pool": {
            "draws_per_design": original["reference_draws_per_design"],
            "p_value_minimum": original["mc_p_value_minimum"],
            "nominal_rejection_grid_level": original["nominal_rejection_grid_level"],
            "conditional_type_I_rate_interval_95": original["conditional_type_I_rate_reference_pool_interval_95"],
            "scope": original["reference_pool_interval_scope"],
        },
        "execution": {"live_model_participants": 0, "colab_runs": 0, "native_interval_studies": 0,
                      "coordinator_usage_tokens": None,
                      "initial_launch": "Failed before numerical output: process-tree inspection denied by macOS. Original failure retained; a separate driver-only observer completed the subsequent launch."},
        "limitations": [
            "Prototype only; the production API still returns selection-conditional inference.",
            "Unequal-variance null rejection remains 8.610% to 9.065% at a nominal 5% threshold.",
            "No negative-binomial, endpoint-interval, multiple-testing/FDR, correlated-error or clinical qualification.",
            "Worker parity is sampled; main comparisons selected df2/3 and preflight also selected df5. df4 selection was not exercised by those worker comparisons.",
            "Evaluation intervals condition on one shared reference pool; they do not include all calibration randomness.",
            "Local roots are not authenticated human signatures or qualified external retention.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    shutil.copyfile(study / "main-protocol.json", output / "protocol.json")
    shutil.copyfile(study / "selection_mc.py", output / "selection_mc.py")
    runtime = json.loads((project / "results/evidence-reader-2026-09-08.eb4h4yej/package-source-report.json").read_bytes())
    (output / "expected-runtime.json").write_text(json.dumps(runtime["source_hashes"], indent=2) + "\n")
    source_map = []
    for source in sorted((study / "main-02").glob("n*/*.npz")):
        destination = output / "data" / source.relative_to(study / "main-02")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        source_map.append({"snapshot_path": destination.relative_to(output).as_posix(),
                           "source_path": source.relative_to(project).as_posix(),
                           "sha256": digest(source)})
    for source in sorted((study / "main-02").glob("n*/designs.json")):
        destination = output / "data" / source.relative_to(study / "main-02")
        shutil.copyfile(source, destination)
        source_map.append({"snapshot_path": destination.relative_to(output).as_posix(),
                           "source_path": source.relative_to(project).as_posix(),
                           "sha256": digest(source)})
    (output / "provenance.json").write_text(json.dumps({
        "source_summary_path": (study / "main-02/summary.json").relative_to(project).as_posix(),
        "source_summary_sha256": digest(study / "main-02/summary.json"),
        "source_main_protocol_sha256": digest(study / "main-protocol.json"),
        "source_main_freeze_sha256": digest(study / "main-freeze.json"),
        "source_prototype_sha256": digest(study / "selection_mc.py"),
        "execution_roots": checks["audits"],
        "files": source_map,
        "scope": "Portable derivative snapshot. Complete CAS/DAG archives and original observer failure remain in local results archives; they are not all included here. This snapshot supports array/summary recomputation, not independent reconstruction of every original execution event.",
    }, indent=2) + "\n")
    fields = ["samples", "scenario", "criterion", "method", "draws", "rejected", "rate",
              "conditional_interval_lower_95", "conditional_interval_upper_95"]
    with (output / "results.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in data["results"]:
            for method in ("conditional", "selection_mc"):
                entry = row[method]
                writer.writerow(dict(zip(fields, (
                    row["n"], row["scenario"], row["criterion"], method,
                    entry["replicates"], entry["rejected"], entry["rate"],
                    *entry["exact_binomial_interval_95"]))))

    plot_results(output, study)
    print(json.dumps({"output": str(output), "summary_rows": len(data["results"]),
                      "array_files": len(list((output / "data").glob("n*/*.npz"))),
                      "synthetic_only": True}))


if __name__ == "__main__":
    main()
