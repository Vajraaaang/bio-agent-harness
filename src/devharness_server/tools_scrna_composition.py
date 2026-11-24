"""Pinned pertpy scCODA adapter and mounted composition worker."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Literal

from devbench.core._files import read_bounded_file
from devbench.core.scientific_types import FrozenContract
from devbench.core.scrna_composition import (
    MAX_COMPOSITION_BYTES,
    CompositionInput,
    CompositionResult,
    CompositionSpec,
    Digest,
    ScCODARuntime,
    run_sccoda,
    write_composition_receipt,
)
from devbench.core.scrna_composition_solver import run_pertpy as run_pertpy
from rna_kernels.canonical import canonical_json
from rna_kernels.scrna_upstream_contract import MAX_SUMMARY_BYTES, SUMMARY_FORMAT

COMPOSITION_OPERATION = "scrna_sccoda"


class CompositionRequest(FrozenContract):
    format: Literal["devbench.mounted-sccoda/1"] = "devbench.mounted-sccoda/1"
    input_sha256: Digest
    spec_sha256: Digest
    runtime: ScCODARuntime


def _verified_bytes(path: Path, digest: str, limit: int) -> bytes:
    data = read_bounded_file(path, limit)
    if sha256(data).hexdigest() != digest:
        raise ValueError("Mounted composition input digest mismatch")
    return data


def run_mounted_composition(
    inputs: Path, outputs: Path, request_sha256: str
) -> CompositionResult:
    """Verify mounted inputs and publish a Bayesian receipt with artifact custody."""
    _check_mounts(inputs, outputs, request_sha256)
    raw = _verified_bytes(inputs / "request.json", request_sha256, 8192)
    request = CompositionRequest.model_validate_json(raw)
    if canonical_json(request.model_dump(mode="json")) != raw:
        raise ValueError("Composition request must use its canonical byte encoding")
    data_raw = _verified_bytes(
        inputs / "composition_input.json", request.input_sha256, MAX_COMPOSITION_BYTES
    )
    spec_raw = _verified_bytes(
        inputs / "composition_spec.json", request.spec_sha256, 8192
    )
    data = CompositionInput.model_validate_json(data_raw)
    spec = CompositionSpec.model_validate_json(spec_raw)
    if (
        canonical_json(data.model_dump(mode="json")) != data_raw
        or canonical_json(spec.model_dump(mode="json")) != spec_raw
    ):
        raise ValueError("Composition inputs must use their canonical byte encoding")
    result = run_sccoda(data, spec, runtime=request.runtime)
    _publish_composition(result, outputs, request_sha256)
    return result


def _check_mounts(inputs: Path, outputs: Path, request_sha256: str) -> None:
    if any(
        not path.is_absolute() or path != path.resolve() or not path.is_dir()
        for path in (inputs, outputs)
    ):
        raise ValueError("Expected canonical absolute composition mounts")
    if (
        inputs.is_relative_to(outputs)
        or outputs.is_relative_to(inputs)
        or any(outputs.iterdir())
        or not re.fullmatch(r"[a-f0-9]{64}", request_sha256)
    ):
        raise ValueError("Expected sibling mounts, empty output and request digest")


def _publish_composition(
    result: CompositionResult, outputs: Path, request_sha256: str
) -> None:
    with tempfile.TemporaryDirectory(prefix=".composition-", dir=outputs) as scratch:
        result_path = Path(scratch) / "composition_result.json"
        manifest_path = Path(scratch) / "summary.json"
        digest = write_composition_receipt(result_path, result)
        manifest = {
            "format": SUMMARY_FORMAT,
            "operation": COMPOSITION_OPERATION,
            "request_sha256": request_sha256,
            "claim_scope": "checked_observation",
            "status": "completed" if result.status == "succeeded" else result.status,
            "output_sha256": {result_path.name: digest},
        }
        if result.reason is not None:
            manifest["reason"] = result.reason
        payload = canonical_json(manifest)
        if len(payload) > MAX_SUMMARY_BYTES:
            raise ValueError("Composition worker summary exceeds its byte bound")
        manifest_path.write_bytes(payload)
        published = result_path.rename(outputs / result_path.name)
        try:
            manifest_path.rename(outputs / manifest_path.name)
        except OSError:
            published.unlink(missing_ok=True)
            raise


def main() -> None:
    from devbench.analysis.resources import configure_threads

    configure_threads()
    if sys.platform == "linux":
        # XLA sizes its pools using the visible CPUs within the sandbox quota.
        allowed_cpus = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, allowed_cpus[:2])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_sha256")
    args = parser.parse_args()
    run_mounted_composition(Path("/inputs"), Path("/outputs"), args.request_sha256)


if __name__ == "__main__":
    main()
