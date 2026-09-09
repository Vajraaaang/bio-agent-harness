"""Verify published files and independently recompute stored-array statistics."""
import os
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"

import argparse
import csv
import hashlib
import io
import json
import math
import stat
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

import numpy as np
from scipy.stats import beta, f

sys.dont_write_bytecode = True
EXCLUDED = {"SHA256SUMS", ".DS_Store"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


@contextmanager
def regular(path):
    require(not path.is_symlink(), "Symlink in snapshot")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as handle:
        require(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), "Nonregular snapshot file")
        yield handle


def read_bytes(path, limit=2_000_000):
    with regular(path) as handle:
        raw = handle.read(limit + 1)
    require(len(raw) <= limit, "Oversized snapshot input")
    return raw


def read_json(raw):
    def invalid(value):
        raise ValueError("Nonfinite JSON")
    require(len(raw) <= 2_000_000, "Oversized JSON")
    return json.loads(raw, parse_constant=invalid)


def arrays(raw, expected_length, evaluated):
    require(type(expected_length) is int and 0 < expected_length <= 100_000, "Invalid array length")
    expected = {"fixed_df3_p", *(criterion + suffix for criterion in ("aic", "bic", "gcv")
                                for suffix in ("_p", "_df", "_f"))}
    if evaluated:
        expected.update(criterion + "_mc_p" for criterion in ("aic", "bic", "gcv"))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) == len(expected), "Unexpected array archive members")
        require(all(item.file_size <= 1_000_000 for item in archive.infolist()), "Oversized array")
        for item in archive.infolist():
            with archive.open(item) as member:
                version = np.lib.format.read_magic(member)
                if version == (1, 0):
                    shape, _, dtype = np.lib.format.read_array_header_1_0(member)
                elif version == (2, 0):
                    shape, _, dtype = np.lib.format.read_array_header_2_0(member)
                else:
                    raise ValueError("Unsupported NPY header")
                require(shape == (expected_length,) and dtype.kind in "if" and dtype.itemsize <= 8,
                        "Unsafe array allocation")
    with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
        require(set(archive.files) == expected, "Unexpected array keys")
        values = {name: archive[name] for name in expected}
    for name, value in values.items():
        require(value.shape == (expected_length,) and value.dtype.kind in "if"
                and np.isfinite(value).all(), "Invalid numerical array")
        if name.endswith("_p"):
            require(np.all((value >= 0) & (value <= 1)), "Invalid probability")
        elif name.endswith("_df"):
            require(np.isin(value, [2, 3, 4, 5]).all(), "Unexpected selected df")
        else:
            require(np.all(value >= 0), "Negative F statistic")
    return values


def check_summary(record, values, alpha):
    n, count = len(values), int(np.sum(values <= alpha))
    interval = [
        float(beta.ppf(.025, count, n - count + 1)) if count else 0.,
        float(beta.ppf(.975, count + 1, n - count)) if count < n else 1.,
    ]
    require(record["replicates"] == n and record["rejected"] == count, "Rejection count mismatch")
    require(math.isclose(record["rate"], count / n, rel_tol=0, abs_tol=1e-15), "Rate mismatch")
    require(np.allclose(record["exact_binomial_interval_95"], interval, rtol=0, atol=1e-12),
            "Conditional evaluation interval mismatch")


def check_f_tails(values, samples):
    for criterion in ("aic", "bic", "gcv"):
        degrees = values[criterion + "_df"]
        require(np.all(samples - (degrees + 2) > 0), "Invalid residual degrees of freedom")
        expected = f.sf(values[criterion + "_f"], degrees, samples - (degrees + 2))
        require(np.allclose(values[criterion + "_p"], expected, rtol=0, atol=1e-12), "F-tail mismatch")


def verify(root, trusted_hash=None):
    root = root.expanduser().resolve()
    manifest = root / "SHA256SUMS"
    manifest_raw = read_bytes(manifest, 128_000)
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    require(trusted_hash is None or trusted_hash == manifest_hash, "Trusted manifest hash mismatch")
    expected = {}
    for line in manifest_raw.decode().splitlines():
        value, name = line.split("  ", 1)
        member = PurePosixPath(name)
        require(len(value) == 64 and all(c in "0123456789abcdef" for c in value), "Invalid digest")
        require(not member.is_absolute() and name == member.as_posix()
                and "\\" not in name and "\x00" not in name
                and all(part not in (".", "..") for part in member.parts)
                and name not in expected, "Invalid inventory member")
        expected[name] = value
    observed, captured, total = {}, {}, 0
    for index, path in enumerate(root.rglob("*")):
        require(index < 512, "Snapshot inventory exceeds its bound")
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.name in EXCLUDED:
            continue
        mode = path.lstat().st_mode
        require(not stat.S_ISLNK(mode), "Symlink in snapshot")
        if stat.S_ISDIR(mode):
            continue
        raw = read_bytes(path, 16_000_000)
        total += len(raw)
        require(total <= 64_000_000, "Snapshot bytes exceed the bound")
        captured[relative.as_posix()] = raw
        observed[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    require(observed == expected, "Snapshot bytes or exact inventory changed")
    # Parse and evaluate the same immutable bytes whose hashes were checked.
    summary, protocol = read_json(captured["summary.json"]), read_json(captured["protocol.json"])
    require(summary["schema_version"] == "devharness.public-research-snapshot/1"
            and protocol["schema_version"] == "gaussian_selection_mc_diagnostic/1",
            "Unsupported snapshot or protocol schema")
    require(summary["synthetic_only"] is True and summary["clinical_release_allowed"] is False,
            "Unsupported input or qualification claim")
    require(summary["protocol"] == protocol, "Protocol mismatch")
    draws, alpha = protocol["reference_draws"], protocol["alpha"]
    require(type(draws) is int and 0 < draws <= 100_000, "Invalid reference draw count")
    require(type(alpha) in (int, float) and math.isfinite(alpha) and 0 < alpha < 1,
            "Invalid nominal threshold")
    rank = math.floor(alpha * (draws + 1))
    require(1 <= rank <= draws, "Reference-pool interval requires an interior attainable rank")
    pool = summary["reference_pool"]
    require(type(pool["draws_per_design"]) is int and pool["draws_per_design"] == draws,
            "Reference-pool draw count mismatch")
    require(math.isclose(pool["p_value_minimum"], 1 / (draws + 1), rel_tol=0, abs_tol=1e-15)
            and math.isclose(pool["nominal_rejection_grid_level"], rank / (draws + 1),
                             rel_tol=0, abs_tol=1e-15), "Reference-pool resolution/grid mismatch")
    interval = pool["conditional_type_I_rate_interval_95"]
    require(type(interval) is list and len(interval) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in interval),
            "Malformed reference-pool interval")
    require(np.allclose(interval, beta.ppf([.025, .975], rank, draws + 1 - rank),
                        rtol=0, atol=1e-12), "Reference-pool interval mismatch")
    sizes, scenarios = protocol["sample_sizes"], protocol["scenarios"]
    require(type(sizes) is list and sizes and all(type(n) is int and n > 7 for n in sizes)
            and len(set(sizes)) == len(sizes), "Invalid or duplicate sample-size dimension")
    require(type(scenarios) is list and scenarios and all(type(name) is str and name for name in scenarios)
            and len(set(scenarios)) == len(scenarios), "Invalid or duplicate scenario dimension")
    rows = {(row["n"], row["scenario"], row["criterion"]): row for row in summary["results"]}
    require(len(rows) == len(summary["results"]) == 36, "Summary row count/identity mismatch")
    require(set(rows) == {(n, scenario, criterion) for n in sizes for scenario in scenarios
                         for criterion in ("aic", "bic", "gcv")}, "Protocol does not cover every summary row")
    checked = 0
    for n in protocol["sample_sizes"]:
        directory = f"data/n{n}"
        reference = arrays(captured[directory + "/reference.npz"], protocol["reference_draws"], False)
        check_f_tails(reference, n)
        for scenario in protocol["scenarios"]:
            observed_arrays = arrays(captured[directory + "/" + scenario + ".npz"],
                                     protocol["evaluation_draws"], True)
            check_f_tails(observed_arrays, n)
            for criterion in ("aic", "bic", "gcv"):
                record = rows[(n, scenario, criterion)]
                conditional = observed_arrays[criterion + "_p"]
                calibrated = (1 + np.searchsorted(np.sort(reference[criterion + "_p"]),
                                                  conditional, side="right")) / (protocol["reference_draws"] + 1)
                require(np.array_equal(calibrated, observed_arrays[criterion + "_mc_p"]),
                        "Monte Carlo rank calculation mismatch")
                check_summary(record["conditional"], conditional, protocol["alpha"])
                check_summary(record["selection_mc"], calibrated, protocol["alpha"])
                raw, corrected = conditional <= protocol["alpha"], calibrated <= protocol["alpha"]
                require(record["paired_raw_only"] == int(np.sum(raw & ~corrected))
                        and record["paired_mc_only"] == int(np.sum(~raw & corrected)),
                        "Paired comparison mismatch")
                checked += 1
    require(checked == len(rows), "Not every summary row was recomputed")
    require(len(captured["results.csv"]) <= 128_000, "Oversized CSV")
    csv_rows = list(csv.DictReader(captured["results.csv"].decode().splitlines()))
    require(len(csv_rows) == 72, "CSV row count mismatch")
    csv_ids = {(int(row["samples"]), row["scenario"], row["criterion"], row["method"]) for row in csv_rows}
    require(csv_ids == {(*key, method) for key in rows for method in ("conditional", "selection_mc")},
            "CSV row identities differ from the complete summary")
    for row in csv_rows:
        value = rows[(int(row["samples"]), row["scenario"], row["criterion"])][row["method"]]
        require(int(row["draws"]) == value["replicates"] and int(row["rejected"]) == value["rejected"],
                "CSV count mismatch")
        require(float(row["rate"]) == value["rate"], "CSV rate mismatch")
        require([float(row["conditional_interval_lower_95"]), float(row["conditional_interval_upper_95"])]
                == value["exact_binomial_interval_95"], "CSV interval mismatch")
    return {"snapshot_valid": True, "files_verified": len(expected), "summary_rows_recomputed": checked,
            "csv_rows_verified": len(csv_rows), "manifest_sha256": manifest_hash,
            "anchored": trusted_hash is not None, "clinical_release_allowed": False,
            "scope": "Published file identity and stored-array arithmetic; not original event reconstruction, clinical qualification, or model performance."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--expected-manifest-sha256")
    args = parser.parse_args()
    try:
        result = verify(args.root, args.expected_manifest_sha256)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(json.dumps({"snapshot_valid": False, "error": str(error), "clinical_release_allowed": False}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
