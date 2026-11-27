"""Run mounted sentinels, or compare two retained runs with --compare SRC DST.

On the coordinator, use `python -m tests.thesis_calibration_parity --compare SRC DST
--mode cross_arch_calibrated_match`. The command prints JSON and exits 1 on drift.
For an oracle run, record the configured backend in DEVBENCH_DEBUG_BLAS_BACKEND
inside the fitting image and pass --blas-debug-oracle when comparing.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

ROUTE_PROBES = {
    "declared-heterogeneous": "limma_voom_qw",
    "unbalanced-contrast": "dream",
    "repeated-donor": "dream",
}

_PYTHON_PACKAGES = ("numpy", "scipy", "pyarrow", "h5py")


def _capture_runtime():
    query = """
    packages <- c("edgeR", "DESeq2", "limma", "variancePartition",
                  "statmod", "BiocParallel", "jsonlite")
    versions <- lapply(setNames(packages, packages),
                       function(pkg) as.character(packageVersion(pkg)))
    versions$R <- as.character(getRversion())
    versions$Bioconductor <- as.character(packageVersion("BiocVersion"))
    lib <- sessionInfo()$BLAS
    if (length(lib)) lib <- normalizePath(lib, mustWork=FALSE)
    cat(jsonlite::toJSON(list(versions=versions, blas=lib),
                         auto_unbox=TRUE, null="null"))
    """
    run = subprocess.run(
        ["Rscript", "--vanilla", "-e", query],
        check=True,
        capture_output=True,
        timeout=30,
    )
    facts = json.loads(run.stdout)
    packages = {k: version(k) for k in _PYTHON_PACKAGES}
    thread_vars = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
    return {
        "arch": platform.machine(),
        "image_digest": os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE"),
        "package_versions": {
            **facts["versions"],
            **packages,
            "python": platform.python_version(),
        },
        "blas_threads": 1
        if all(os.environ.get(k) == "1" for k in thread_vars)
        else None,
        "blas_backend": os.environ.get("DEVBENCH_DEBUG_BLAS_BACKEND"),
        "blas_library": facts.get("blas"),
    }


def _run_sentinels() -> None:
    if os.environ.get("DEVBENCH_CALIBRATION_SOURCES") == "checkout":
        sys.path.insert(0, "/checkout/src")
    from devharness_core.provenance import hash_artifact
    from rna_kernels.mounted_bulk import run_mounted_bulk

    inputs, outputs = Path("/inputs"), Path("/outputs")
    sentinel_cases = tuple(sorted(inputs.iterdir()))
    if (
        len(sentinel_cases) != 6
        and {p.name for p in sentinel_cases} != ROUTE_PROBES.keys()
    ):
        raise ValueError("Expected the frozen historical or route-probe inventory")
    observations = {}
    for case in sentinel_cases:
        target = outputs / case.name
        target.mkdir()
        sha = hash_artifact(case / "request.json")["sha256"]
        try:
            manifest = run_mounted_bulk(case, target, sha)
        except ValueError as error:
            observations[case.name] = {"status": "refused", "reason": str(error)[:240]}
        else:
            observations[case.name] = {
                "status": "completed",
                "solver": manifest["solver"],
                "tested_count": manifest["tested_count"],
                "genes_sha256": manifest["genes_sha256"],
            }
        observations[case.name]["request_sha256"] = sha
    (outputs / "runtime.json").write_text(
        json.dumps(_capture_runtime()), encoding="utf-8"
    )
    (outputs / "sentinels.json").write_text(
        json.dumps(observations, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )


def _runtime(directory):
    # Read pins on the coordinator. Historical kernel images only run the generator.
    from devbench.core._files import read_bounded_json
    from devharness_controlled.native_contracts import (
        KERNEL_VERSIONS,
        kernel_test_platform,
    )
    from rna_kernels.numerical_equivalence import ReplayRuntime

    captured = read_bounded_json(directory / "runtime.json", 16_384)
    runtime = ReplayRuntime(**captured)
    if kernel_test_platform(runtime.image_digest) != "linux/" + runtime.cpu:
        raise ValueError("Recorded CPU and image do not match the pinned kernel lock")
    if not (KERNEL_VERSIONS.items() <= runtime.package_versions.items()):
        raise ValueError("Observed R package versions differ from the kernel lock")
    if not {"python", *_PYTHON_PACKAGES} <= runtime.package_versions.keys():
        raise ValueError("Missing observed Python runtime or numerical package version")
    return runtime


def compare_sentinels(
    source, target, *, mode, alpha=0.05, blas_debug_oracle=False, route_probes=False
):
    """Return per-fit ADR-16 receipts and the exact expected confounding refusal."""
    from devbench.analysis.thesis_replay import compare_replayed_fit
    from devbench.core._files import read_bounded_json
    from tests.thesis_calibration_inputs import SIMULATIONS

    refused = "bi-confounded-000"
    expected = (
        set(ROUTE_PROBES)
        if route_probes
        else {f"{spec.key}-planted-000" for spec in SIMULATIONS} | {refused}
    )
    result = {"mode": mode, "passed": False, "cases": {}, "violations": []}
    errors = result["violations"]
    try:
        runtimes = [_runtime(path) for path in (source, target)]
        result.update(
            source_arch=runtimes[0].cpu,
            target_arch=runtimes[1].cpu,
            same_arch=runtimes[0].cpu == runtimes[1].cpu,
            debug_oracle=blas_debug_oracle,
        )
        left, right = [
            read_bounded_json(path / "sentinels.json", 65_536)
            for path in (source, target)
        ]
        if set(left) != expected or set(right) != expected:
            raise ValueError("Incomplete or unexpected sentinel inventory")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
        return result
    for case in sorted(expected):
        try:
            a, b = left[case], right[case]
            if a["request_sha256"] != b["request_sha256"]:
                raise ValueError("Sentinel requests differ")
            if case == refused:
                agreed = (
                    a == b
                    and a["status"] == "refused"
                    and "rank, conditioning or residual support failed" in a["reason"]
                    and not any((source / case).iterdir())
                    and not any((target / case).iterdir())
                )
                result["cases"][case] = {"status": a["status"], "passed": agreed}
            else:
                receipt = compare_replayed_fit(
                    source / case,
                    target / case,
                    mode=mode,
                    source_runtime=runtimes[0],
                    target_runtime=runtimes[1],
                    alpha=alpha,
                    blas_debug_oracle=blas_debug_oracle,
                )
                result["cases"][case] = receipt
                observations_match = all(
                    obs["status"] == "completed"
                    and (not route_probes or obs["solver"] == ROUTE_PROBES[case])
                    and all(obs.get(k) == v for k, v in receipt["fits"][side].items())
                    for side, obs in (("source", a), ("target", b))
                )
                if not observations_match:
                    receipt["passed"] = False
                    receipt["violations"].append(
                        {"check": "sentinel_binding", "field": "recorded observation"}
                    )
                agreed = receipt["passed"]
            if not agreed:
                raise ValueError("Sentinel observations or verification checks failed")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            result["cases"].setdefault(case, {"passed": False, "error": str(exc)})
            errors.append(f"{case}: {exc}")
    result["passed"] = not errors
    return result


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("SOURCE", "TARGET"))
    parser.add_argument(
        "--mode", choices=("exact_match_same_arch", "cross_arch_calibrated_match")
    )
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--blas-debug-oracle", action="store_true")
    args = parser.parse_args(argv)
    if args.compare is None and args.mode is None and not args.blas_debug_oracle:
        _run_sentinels()
        return
    if not args.compare or not args.mode:
        parser.error("--compare and --mode are required for verification")
    receipt = compare_sentinels(
        *(p.absolute() for p in args.compare),
        mode=args.mode,
        alpha=args.alpha,
        blas_debug_oracle=args.blas_debug_oracle,
    )
    print(json.dumps(receipt, allow_nan=False, sort_keys=True))
    if not receipt["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
