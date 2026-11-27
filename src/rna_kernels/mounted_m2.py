"""Execute a checksum-bound Tier 1 forensic or QC operation inside the RNA image."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from rna_kernels.m2_contract import KERNEL_AXES, OUTPUTS, source_digest


def _bound_inputs(inputs, request):
    paths = {}
    for port, binding in request["inputs"].items():
        name = binding["filename"]
        if (
            not re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z0-9]+(?:\.gz)?", name)
            or name.split(".")[0] != port
        ):
            raise ValueError("Invalid M2 input filename")
        path = inputs / name
        if source_digest(path) != binding["sha256"]:
            raise ValueError("M2 input digest differs")
        paths[port] = path
    if set(inputs.iterdir()) != {inputs / "request.json", *paths.values()}:
        raise ValueError("Unexpected M2 mounted input")
    return paths


def run_mounted_m2(inputs: Path, outputs: Path, request_sha256: str):
    raw = read_bounded_file(inputs / "request.json", 512 * 1024)
    if hashlib.sha256(raw).hexdigest() != request_sha256 or any(outputs.iterdir()):
        raise ValueError("M2 request or output custody mismatch")
    request = decode_receipt_json(raw)
    op = request["operation"]
    if op not in KERNEL_AXES:
        raise ValueError("Unknown M2 operation")
    paths = _bound_inputs(inputs, request)
    if op == "donor_forensics":
        from rna_kernels.donor_forensics import run_m2
    else:
        from rna_kernels.mounted_scrna import run_m2
    options = (
        {"primary_count_admitted": request["primary_count_admitted"] is True}
        if op == "scrna_ambient_decontamination"
        else {}
    )
    files = run_m2(op, paths, request["parameters"], outputs, **options)
    if "result" not in files or set(outputs.iterdir()) != set(files.values()):
        raise ValueError("Unexpected M2 output set")
    hashes = {}
    for port, path in files.items():
        if path != outputs / OUTPUTS[port].filename:
            raise ValueError("Unexpected M2 output path")
        hashes[path.name] = hash_bounded_file(path, 64 * 1024**2)
    summary = {
        "request_sha256": request_sha256,
        "operation": op,
        "output_sha256": hashes,
    }
    (outputs / "summary.json").write_text(json.dumps(summary, allow_nan=False))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_sha256")
    run_mounted_m2(
        Path("/inputs"), Path("/outputs"), parser.parse_args().request_sha256
    )
