"""Observed public inputs only; no private references or model work on import."""
import csv
import hashlib
import io
from pathlib import Path

from .contracts import content_sha256
from .evidence import _read, decode_json
from .zarr_bounds import is_prepared_zarr_member, validate_prepared_zarr

MAX_MATRIX_BYTES = 16_000_000
MAX_INPUT_BYTES = 40_000_000
MAX_JSON_BYTES = 2_000_000


def input_inventory(root):
    """Hash only the declared public assets, never sibling private records."""
    root = Path(root)
    if (not root.is_absolute() or ".." in root.parts or not root.is_dir()
            or any(p.is_symlink() for p in (root, *root.parents))):
        raise ValueError("Public input roots must be absolute without symlinks")
    allowed = {"metadata.csv", "task_inputs.json", "calibration.json"}
    records, total = {}, 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Public input symlinks are forbidden")
        if path.is_dir():
            if path != root / "counts.zarr" and not path.is_relative_to(root / "counts.zarr"):
                raise ValueError("Undeclared public input directory")
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in allowed and not (
                relative.startswith("counts.zarr/") and is_prepared_zarr_member(relative[len("counts.zarr/"):])):
            raise ValueError("Undeclared public input member")
        if len(records)>=20000:
            raise ValueError("Public input member budget exceeded")
        if total >= MAX_INPUT_BYTES:
            raise ValueError("Public input byte budget exceeded")
        raw = _read(path, min(MAX_MATRIX_BYTES, MAX_INPUT_BYTES-total))
        total += len(raw)
        records[relative] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    if not allowed <= set(records):
        raise ValueError("Public input bundle lacks a required asset")
    return records


def load_public_inputs(root, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Public numerical input reading is disabled")
    from .biology_input_limits import validate_public_structure
    root = Path(root)
    inventory = input_inventory(root)
    task = decode_json(_read(root / "task_inputs.json", MAX_JSON_BYTES))
    calibration = decode_json(_read(root / "calibration.json", MAX_JSON_BYTES))
    validate_public_structure(task)
    validate_public_structure(calibration)
    if task.get("record_format") != "devbench.public-inputs/1" or task.get("kind") not in {"developmental_observation", "seqc_normalization"}:
        raise ValueError("Unknown public task input contract")
    genes, samples = task.get("gene_ids"), task.get("sample_ids")
    from .contracts import Identifier
    from pydantic import TypeAdapter
    axis = TypeAdapter(list[Identifier])
    genes, samples = axis.validate_python(genes), axis.validate_python(samples)
    if (not 1<=len(genes)<=5000 or len(set(genes))!=len(genes)
            or not 1<=len(samples)<=1000 or len(set(samples))!=len(samples)
            or len(genes)*len(samples)*8>MAX_MATRIX_BYTES):
        raise ValueError("Public task dimensions exceed their bounded distinct axes")
    layer = "continuous" if task["kind"] == "developmental_observation" else "raw_counts"
    if task.get("measurement") != layer:
        raise ValueError("Public measurement and requested analysis disagree")
    validate_prepared_zarr(root / "counts.zarr", expected_shape=(len(genes),len(samples)), measurement_type=layer)
    reader = csv.DictReader(io.StringIO(_read(root / "metadata.csv", MAX_JSON_BYTES).decode("utf-8")), strict=True)
    if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):
        raise ValueError("Public metadata columns must be distinct")
    rows = list(reader)
    if (any(None in row or any(value is None for value in row.values()) for row in rows)
            or [row.get("sample_id") for row in rows] != samples):
        raise ValueError("Public metadata rows do not align with expression columns")
    import numpy as np
    from devharness_core.storage import ChunkedExpressionStore
    with ChunkedExpressionStore(root / "counts.zarr") as store:
        if list(store.gene_names)!=genes or list(store.sample_names)!=samples:
            raise ValueError("Stored public axes differ from the declared input axes")
        values = np.concatenate([block for _, block in store.iter_gene_chunks(chunk_size=128)],axis=0)
    if values.nbytes>MAX_MATRIX_BYTES or not np.isfinite(values).all() or np.any(values<0):
        raise ValueError("Public expression violates its finite nonnegative source contract")
    if layer == "raw_counts" and (values.dtype.kind not in "iu" or np.any(values>2**53)):
        raise ValueError("Public read counts require integer storage and exact numeric transport")
    if input_inventory(root) != inventory:
        raise ValueError("Public inputs changed during reading")
    return task, values, rows, calibration, content_sha256(inventory)
