"""Fixed mounted ports shared by the M2 forensic and droplet QC adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from devbench.core._files import hash_bounded_file


@dataclass(frozen=True)
class OutputPort:
    filename: str
    kind: str
    format: str
    representation: str = "derived_summary"


# Workers return the subset of these ports they actually produced. Successful
# ambient adjustment also requires the quarantined matrix and its receipt.
OUTPUTS = {
    "result": OutputPort("result.json", "table", "json"),
    "plot_sidecar": OutputPort("plot_sidecar.json", "plot_sidecar", "json"),
    "metrics": OutputPort("metrics.parquet", "table", "parquet"),
    "adjusted": OutputPort(
        "adjusted.h5ad", "expression_matrix", "h5ad", "ambient_adjusted_counts"
    ),
}
KERNEL_AXES = {
    "donor_forensics": "A1",
    "scrna_ambient_decontamination": "A2",
    "snrna_droplet_qc": "A2",
    "scrna_doublets": "A2",
}


def source_digest(path: Path) -> str:
    if not path.is_dir():
        return hash_bounded_file(path, 1 << 40)
    # Match the daemon's directory Merkle framing without importing its service.
    if path.resolve(strict=True) != path or not path.is_absolute():
        raise ValueError("Expected canonical directory")
    count = 0

    def branch(folder, depth):
        nonlocal count
        if depth > 32:
            raise ValueError("Directory nesting exceeds budget")
        h = hashlib.sha256(b"devbench.input-tree/1\0")
        entries = []
        for entry in folder.iterdir():
            count += 1
            if count > 200_000 or entry.is_symlink():
                raise ValueError("Unadmitted directory member")
            entries.append(entry)
        for entry in sorted(entries):
            if entry.is_dir():
                kind, digest = "dir", branch(entry, depth + 1)
            else:
                kind, digest = "file", hash_bounded_file(entry, 1 << 40)
            h.update(
                json.dumps([kind, entry.name, digest], separators=(",", ":")).encode()
                + b"\n"
            )
        return h.hexdigest()

    return branch(path, 0)
