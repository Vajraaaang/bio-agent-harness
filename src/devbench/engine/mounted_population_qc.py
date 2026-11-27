"""Mounted, digest-bound QC and count-preserving population aggregation."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.platform.scrna_population_analysis import stage_population_pseudobulk_hdf5
from rna_kernels.canonical import canonical_json


def run_mounted_population_qc(inputs: Path, outputs: Path, request_sha: str) -> dict:
    raw = read_bounded_file(inputs / "request.json", 64 << 10)
    if hashlib.sha256(raw).hexdigest() != request_sha:
        raise ValueError("Population QC request digest mismatch")
    request = json.loads(raw)
    if (
        request.keys() != {"format", "source", "specification"}
        or request["format"] != "devbench.mounted-population-qc/1"
        or request["source"] not in {"expression.h5ad", "expression.zarr"}
    ):
        raise ValueError("Invalid population QC mount or request format")
    summary = stage_population_pseudobulk_hdf5(
        inputs / request["source"], request["specification"], outputs / "pseudobulk.h5"
    )
    result = {
        **summary,
        "request_sha256": request_sha,
        "clinical_release_allowed": False,
    }
    with (outputs / "summary.json").open("xb") as stream:
        stream.write(canonical_json(result))
    return result


def execute_population_qc(stage: Path, *, request_sha: str, **execution) -> dict:
    """Publish L1 only after the host checks cleanup, isolation and artifact bytes."""
    from devbench.engine.mounted_kernel_oci import run_mounted_container

    proof = run_mounted_container(
        stage, request_sha=request_sha, worker_module=__name__, **execution
    )
    if (
        not proof.get("cleanup_confirmed")
        or proof.get("error_type")
        or proof.get("claim_tier") == "none"
    ):
        raise RuntimeError(
            "Mounted population QC did not complete with verified cleanup"
        )
    outputs = stage / "outputs"
    raw = read_bounded_file(outputs / "summary.json", 1 << 20)
    result = json.loads(raw)
    if (
        result.get("request_sha256") != request_sha
        or result.get("clinical_release_allowed") is not False
        or result["matrix"]["sha256"]
        != hash_bounded_file(outputs / "pseudobulk.h5", result["matrix"]["size_bytes"])
    ):
        raise ValueError("Mounted population QC output digest mismatch")
    return {
        "execution_lane": "tier1_curated",
        "claim_tier": "L1_execution_verified",
        "oci_isolated": proof["oci_isolated"],
        "isolation_backend": proof["isolation_backend"],
        "cleanup_confirmed": True,
        "clinical_release_allowed": False,
        "request_sha256": request_sha,
        "summary_sha256": hashlib.sha256(raw).hexdigest(),
        "matrix_sha256": result["matrix"]["sha256"],
    }


if __name__ == "__main__":
    import resource

    run_mounted_population_qc(Path("/inputs"), Path("/outputs"), sys.argv[1])
    # Operational telemetry stays outside the deterministic scientific payload.
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with Path("/outputs/resource-usage.json").open("xb") as usage:
        usage.write(
            canonical_json(
                {"peak_rss_bytes": peak * (1 if sys.platform == "darwin" else 1024)}
            )
        )
