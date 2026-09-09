"""Semantic tamper controls for the portable public results verifier."""
import bisect
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.stats import beta, f


@pytest.fixture(scope="module")
def verifier():
    path = Path(__file__).resolve().parents[1] / "reports/selection-calibration/verify.py"
    spec = importlib.util.spec_from_file_location("public_snapshot_verifier", path)
    module = importlib.util.module_from_spec(spec)
    before = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def seal(root):
    values = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(root.rglob("*")) if path.is_file() and path.name != "SHA256SUMS"}
    (root / "SHA256SUMS").write_text("".join(value + "  " + name + "\n" for name, value in values.items()))
    return hashlib.sha256((root / "SHA256SUMS").read_bytes()).hexdigest()


def counts(probabilities):
    n, k = len(probabilities), int(sum(value <= .05 for value in probabilities))
    return {"replicates": n, "rejected": k, "rate": k / n,
            "exact_binomial_interval_95": [float(beta.ppf(.025, k, n-k+1)) if k else 0.,
                                           float(beta.ppf(.975, k+1, n-k)) if k < n else 1.]}


def table_arrays(n, length, shift):
    values = {"fixed_df3_p": np.linspace(.1, .9, length)}
    for index, criterion in enumerate(("aic", "bic", "gcv")):
        degrees = np.resize(np.array([2, 3, 4, 5]), length)
        statistic = np.linspace(.05, 12, length) + shift + index / 7
        values[criterion + "_df"] = degrees
        values[criterion + "_f"] = statistic
        values[criterion + "_p"] = f.sf(statistic, degrees, n-degrees-2)
    return values


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path.resolve() / "snapshot"
    root.mkdir()
    protocol = {"schema_version": "gaussian_selection_mc_diagnostic/1", "sample_sizes": [24, 48, 96],
                "reference_draws": 31, "evaluation_draws": 8, "alpha": .05,
                "scenarios": ["homoskedastic_null", "heteroskedastic_null", "linear_signal", "oscillatory_signal"]}
    summary = {"schema_version": "devharness.public-research-snapshot/1",
               "synthetic_only": True, "clinical_release_allowed": False, "protocol": protocol, "results": [],
               "reference_pool": {"draws_per_design": 31, "p_value_minimum": 1/32,
                                  "nominal_rejection_grid_level": 1/32,
                                  "conditional_type_I_rate_interval_95": beta.ppf([.025, .975], 1, 31).tolist()}}
    csv_rows = []
    for n in protocol["sample_sizes"]:
        directory = root / "data" / f"n{n}"
        directory.mkdir(parents=True)
        reference = table_arrays(n, 31, 0)
        np.savez_compressed(directory / "reference.npz", **reference)
        for offset, scenario in enumerate(protocol["scenarios"]):
            observed = table_arrays(n, 8, offset / 3)
            for criterion in ("aic", "bic", "gcv"):
                probability = observed[criterion + "_p"]
                ordered = sorted(reference[criterion + "_p"])
                calibrated = np.array([(1 + bisect.bisect_right(ordered, value)) / 32 for value in probability])
                observed[criterion + "_mc_p"] = calibrated
                raw, adjusted = probability <= .05, calibrated <= .05
                record = {"n": n, "scenario": scenario, "criterion": criterion,
                          "conditional": counts(probability), "selection_mc": counts(calibrated),
                          "paired_raw_only": int(sum(raw & ~adjusted)), "paired_mc_only": int(sum(~raw & adjusted))}
                summary["results"].append(record)
                for method in ("conditional", "selection_mc"):
                    result = record[method]
                    csv_rows.append([n, scenario, criterion, method, result["replicates"], result["rejected"],
                                     result["rate"], *result["exact_binomial_interval_95"]])
            np.savez_compressed(directory / (scenario + ".npz"), **observed)
    (root / "protocol.json").write_text(json.dumps(protocol))
    (root / "summary.json").write_text(json.dumps(summary))
    with (root / "results.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["samples", "scenario", "criterion", "method", "draws", "rejected", "rate",
                         "conditional_interval_lower_95", "conditional_interval_upper_95"])
        writer.writerows(csv_rows)
    return root, seal(root)


def test_complete_snapshot_recomputes_all_rows_and_records_anchor_scope(verifier, snapshot):
    root, anchor = snapshot
    assert verifier.verify(root, anchor)["summary_rows_recomputed"] == 36
    assert verifier.verify(root, anchor)["csv_rows_verified"] == 72
    assert verifier.verify(root)["anchored"] is False
    with pytest.raises(ValueError, match="Trusted manifest"):
        verifier.verify(root, "0" * 64)


def test_protocol_cannot_leave_reported_rows_unchecked(verifier, snapshot):
    root, _ = snapshot
    protocol = json.loads((root / "protocol.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    protocol["sample_sizes"] = [24]
    summary["protocol"] = protocol
    (root / "protocol.json").write_text(json.dumps(protocol))
    (root / "summary.json").write_text(json.dumps(summary))
    seal(root)  # Internally consistent hashes must not replace semantic coverage.
    with pytest.raises(ValueError, match="cover every summary"):
        verifier.verify(root)


def test_reference_f_statistics_must_match_their_probabilities(verifier, snapshot):
    root, _ = snapshot
    path = root / "data/n24/reference.npz"
    with np.load(path, allow_pickle=False) as source:
        values = {key: source[key] for key in source.files}
    values["aic_f"] = np.zeros(31)
    np.savez_compressed(path, **values)
    seal(root)
    with pytest.raises(ValueError, match="F-tail"):
        verifier.verify(root)


@pytest.mark.parametrize("key,value", [
    ("draws_per_design", 1), ("p_value_minimum", 0), ("nominal_rejection_grid_level", 1),
    ("conditional_type_I_rate_interval_95", [.95, 1.]),
])
def test_reference_pool_math_is_recomputed(verifier, snapshot, key, value):
    root, _ = snapshot
    summary = json.loads((root / "summary.json").read_text())
    summary["reference_pool"][key] = value
    (root / "summary.json").write_text(json.dumps(summary))
    seal(root)
    with pytest.raises(ValueError, match="Reference-pool"):
        verifier.verify(root)


def test_unknown_snapshot_schema_is_not_admitted(verifier, snapshot):
    root, _ = snapshot
    summary = json.loads((root / "summary.json").read_text())
    summary["schema_version"] = "unknown/99"
    (root / "summary.json").write_text(json.dumps(summary))
    seal(root)
    with pytest.raises(ValueError, match="Unsupported snapshot"):
        verifier.verify(root)
