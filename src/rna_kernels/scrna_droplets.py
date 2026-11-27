"""Bounded raw-droplet admission and curated cell-calling adapters.

The coordinator supplies attested provenance and the exact mounted image. A
Cell Ranger HDF5 layout can hold either raw or filtered droplets. Shape and
integer counts alone cannot establish the upstream filtering history.
"""

from __future__ import annotations

import csv
import math
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal

import h5py
import numpy as np

from devharness_core.provenance import canonical_json, hash_artifact
from devharness_core.storage import _h5_member

_SHA256 = re.compile(r"[a-f0-9]{64}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_GEM_WELL = re.compile(r".+-([1-9][0-9]*)")
_MAX_FILE_BYTES = 512 * 1024**2
_MAX_SPARSE_BYTES = 256 * 1024**2
_MAX_AXIS_BYTES = 24 * 1024**2
_MAX_FEATURES = 100_000
_MAX_DROPLETS = 1_000_000
_CHUNK_ENTRIES = 131_072
_MAX_DROPLET_TOTAL = 2**31 - 1
_CORRECTION = "devbench.droplet_correction"
_EMPTYDROPS_DRIVER = Path(__file__).resolve().parent / "r/scrna_emptydrops.R"
_R_PACKAGES = (
    'if (requireNamespace("DropletUtils", quietly=TRUE)) '
    'cat(as.character(utils::packageVersion("DropletUtils")))'
)
_EMPTYDROPS_FIELDS = ("barcode", "total_umi", "p_value", "fdr", "limited", "called")

Method = Literal["emptydrops", "cellbender"]


def _digest(document: object) -> str:
    return sha256(canonical_json(document)).hexdigest()


@dataclass(frozen=True)
class RawDropletProvenance:
    """An upstream receipt must attest that the bound source was unfiltered."""

    source_sha256: str
    capture_id: str
    origin_receipt_sha256: str
    source_kind: str
    filter_state: str

    def sha256(self) -> str:
        return _digest({"format": "devbench.raw-droplet-provenance/1", **asdict(self)})


@dataclass(frozen=True)
class DropletInput:
    source_sha256: str
    capture_id: str
    rna_features: int
    feature_ids: tuple[str, ...] = field(repr=False)
    barcodes: tuple[str, ...] = field(repr=False)
    rna_totals: np.ndarray = field(repr=False, compare=False)


@dataclass(frozen=True)
class EmptyDropsAction:
    lower: int = 100
    niters: int = 10_000
    fdr: float = 0.001
    seed: int = 0

    def __post_init__(self) -> None:
        _integer(self.lower, 1, _MAX_DROPLET_TOTAL, "lower")
        _integer(self.niters, 1, 100_000, "niters")
        _integer(self.seed, 0, 2**31 - 1, "seed")
        _probability(self.fdr, "fdr")


@dataclass(frozen=True)
class CellBenderAction:
    expected_cells: int
    total_droplets_included: int
    fpr: float = 0.01

    def __post_init__(self) -> None:
        _integer(self.expected_cells, 1, _MAX_DROPLETS - 1, "expected_cells")
        _integer(
            self.total_droplets_included,
            self.expected_cells + 1,
            _MAX_DROPLETS,
            "total_droplets_included",
        )
        _probability(self.fpr, "fpr")


DropletAction = EmptyDropsAction | CellBenderAction


@dataclass(frozen=True)
class DropletRuntime:
    """Package pin forwarded only after the coordinator verifies its image."""

    method: Method
    image_id: str
    package_version: str

    def __post_init__(self) -> None:
        if (
            self.method not in ("emptydrops", "cellbender")
            or not self.image_id.startswith("sha256:")
            or not _SHA256.fullmatch(self.image_id[7:])
            or not _VERSION.fullmatch(self.package_version)
        ):
            raise ValueError("Cell calling requires an exact image and package pin")


class _RuntimeUnavailable(Exception):
    """This image cannot run the frozen adapter."""


def _integer(value: int, minimum: int, maximum: int, name: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")


def _probability(value: float, name: str) -> None:
    if type(value) not in (int, float) or not 0 < value < 1:
        raise ValueError(f"{name} must be strictly between zero and one")


def _regular_h5(path: Path) -> dict:
    if path.suffix != ".h5" or not path.is_file() or path.resolve() != path:
        raise ValueError("Expected an absolute local Cell Ranger v3 HDF5 file")
    return hash_artifact(path, max_bytes=_MAX_FILE_BYTES, max_entries=1)


def _checked_provenance(provenance: RawDropletProvenance, expected: str) -> None:
    if not _SHA256.fullmatch(expected) or provenance.sha256() != expected:
        raise ValueError("Raw-droplet provenance differs from its expected receipt")
    if (
        provenance.source_kind != "raw_feature_bc_matrix"
        or provenance.filter_state != "unfiltered"
        or not _SHA256.fullmatch(provenance.source_sha256)
        or not _SHA256.fullmatch(provenance.origin_receipt_sha256)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", provenance.capture_id)
    ):
        raise ValueError("Require origin-attested unfiltered raw_feature_bc_matrix")


def _matrix_group(handle: h5py.File, *, corrected: bool) -> h5py.Group:
    if not corrected:
        if _CORRECTION in handle.attrs or "droplet_latents" in handle:
            raise ValueError(
                "CellBender-derived expression is quarantined from raw counts"
            )
        if set(handle) != {"matrix"}:
            raise ValueError(
                "Require unfiltered Cell Ranger v3 raw droplets, not AnnData"
            )
    matrix = _h5_member(handle, "matrix", field="Cell Ranger matrix")
    if not isinstance(matrix, h5py.Group):
        raise ValueError("Cell Ranger v3 matrix must be a group")
    return matrix


def _dataset(group: h5py.Group, name: str, length: int | None = None) -> h5py.Dataset:
    node = _h5_member(group, name, field=f"Cell Ranger {name}")
    if not isinstance(node, h5py.Dataset) or node.ndim != 1:
        raise ValueError(f"Cell Ranger {name} must be a one-dimensional dataset")
    if length is not None and node.shape != (length,):
        raise ValueError(f"Cell Ranger {name} length disagrees with its axis")
    return node


def _text_axis(
    group: h5py.Group,
    name: str,
    length: int,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    node = _dataset(group, name, length)
    if (
        node.dtype.kind != "S"
        or node.dtype.itemsize > 512
        or node.size * max(node.dtype.itemsize, 24) > _MAX_AXIS_BYTES
    ):
        raise ValueError(f"Cell Ranger {name} requires a bounded fixed-width text axis")
    labels = []
    reader = node.asstr()
    for start in range(0, length, 2_048):
        labels.extend(reader[start : start + 2_048].tolist())
    if any(any(ord(ch) < 32 for ch in label) for label in labels):
        raise ValueError(f"Cell Ranger {name} labels cannot contain control characters")
    if not allow_empty and any(not label.strip() for label in labels):
        raise ValueError(f"Cell Ranger {name} labels must be nonempty text")
    return tuple(labels)


def _matrix_shape(matrix: h5py.Group) -> tuple[int, int]:
    shape = _dataset(matrix, "shape", 2)
    if shape.dtype.kind not in "iu":
        raise ValueError("Cell Ranger dimensions require integers")
    features, droplets = map(int, shape[:])
    _integer(features, 1, _MAX_FEATURES, "features")
    _integer(droplets, 1, _MAX_DROPLETS, "droplets")
    return features, droplets


def _read_feature_axes(
    matrix: h5py.Group,
    n_features: int,
    n_droplets: int,
) -> tuple[tuple[str, ...], tuple[str, ...], np.ndarray]:
    features = _h5_member(matrix, "features", field="Cell Ranger features")
    if not isinstance(features, h5py.Group):
        raise ValueError("Cell Ranger v3 feature annotation must be a group")
    ids = _text_axis(features, "id", n_features)
    _text_axis(features, "name", n_features)
    if "genome" in features:
        _text_axis(features, "genome", n_features, allow_empty=True)
    rna = (
        np.asarray(_text_axis(features, "feature_type", n_features))
        == "Gene Expression"
    )
    barcodes = _text_axis(matrix, "barcodes", n_droplets)
    if len(set(ids)) != n_features or len(set(barcodes)) != n_droplets or not rna.any():
        raise ValueError(
            "Require unique barcodes and feature IDs and measured gene expression"
        )
    return ids, barcodes, rna


def _sparse_storage(
    matrix: h5py.Group,
    n_features: int,
    n_droplets: int,
) -> tuple[h5py.Dataset, h5py.Dataset, np.ndarray]:
    values = _dataset(matrix, "data")
    indices = _dataset(matrix, "indices", values.size)
    pointers = _dataset(matrix, "indptr", n_droplets + 1)
    if (
        values.dtype.kind not in "iuf"
        or indices.dtype.kind not in "iu"
        or pointers.dtype.kind not in "iu"
        or sum(node.nbytes for node in (values, indices, pointers)) > _MAX_SPARSE_BYTES
    ):
        raise ValueError("Cell Ranger CSC storage exceeds dtype or allocation limits")
    ptr = np.asarray(pointers[:], dtype=np.int64)
    widths = np.diff(ptr)
    if (
        ptr[0] != 0
        or ptr[-1] != values.size
        or np.any(ptr < 0)
        or np.any(ptr > values.size)
        or np.any(widths < 0)
        or np.any(widths > n_features)
    ):
        raise ValueError("Invalid Cell Ranger sparse pointers")
    return values, indices, ptr


def _rna_totals(
    values: h5py.Dataset,
    indices: h5py.Dataset,
    ptr: np.ndarray,
    rna: np.ndarray,
) -> np.ndarray:
    n_droplets = len(ptr) - 1
    totals = np.zeros(n_droplets, dtype=np.int64)
    start = 0
    while start < n_droplets:
        end = min(
            start + 2_048,
            np.searchsorted(ptr, ptr[start] + _CHUNK_ENTRIES, side="right") - 1,
        )
        left, right = ptr[start], ptr[end]
        if left != right:
            offsets = ptr[start : end + 1] - left
            totals[start:end] = _rna_totals_block(
                values[left:right], indices[left:right], offsets, rna
            )
        start = end
    if np.any(totals > _MAX_DROPLET_TOTAL):
        raise ValueError("Droplet RNA library size exceeds the supported integer range")
    totals.setflags(write=False)
    return totals


def _rna_totals_block(
    values: np.ndarray,
    indices: np.ndarray,
    offsets: np.ndarray,
    rna: np.ndarray,
) -> np.ndarray:
    if np.any(indices < 0) or np.any(indices >= len(rna)):
        raise ValueError("Cell Ranger sparse index exceeds the feature axis")
    if (
        np.any(~np.isfinite(values))
        or np.any(values < 0)
        or np.any(values > _MAX_DROPLET_TOTAL)
        or np.any(values != np.floor(values))
    ):
        raise ValueError("Cell Ranger data must contain nonnegative integer counts")
    filled = np.diff(offsets) != 0
    starts = offsets[:-1][filled]
    unsorted = indices[1:] <= indices[:-1]
    unsorted[starts[1:] - 1] = False
    if unsorted.any():
        raise ValueError(
            "Cell Ranger sparse indices must be sorted and distinct per barcode"
        )
    totals = np.zeros(len(filled), dtype=np.int64)
    totals[filled] = np.add.reduceat(
        np.where(rna[indices], values, 0).astype(np.int64), starts
    )
    return totals


def _inspect_h5(
    source: Path,
    *,
    corrected: bool,
) -> tuple[tuple[str, ...], tuple[str, ...], np.ndarray, int]:
    with h5py.File(source, "r") as handle:
        matrix = _matrix_group(handle, corrected=corrected)
        features, droplets = _matrix_shape(matrix)
        ids, barcodes, rna = _read_feature_axes(matrix, features, droplets)
        values, indices, ptr = _sparse_storage(matrix, features, droplets)
        return ids, barcodes, _rna_totals(values, indices, ptr, rna), int(rna.sum())


def verify_raw_droplet_input(
    source: Path,
    provenance: RawDropletProvenance,
    *,
    expected_provenance_sha256: str,
) -> DropletInput:
    """Validate the provenance binding, layout, one capture and every count."""
    _checked_provenance(provenance, expected_provenance_sha256)
    if _regular_h5(source)["sha256"] != provenance.source_sha256:
        raise ValueError(
            "Raw-droplet input identity disagrees with upstream provenance"
        )
    ids, barcodes, totals, n_rna = _inspect_h5(source, corrected=False)
    wells = {_capture_suffix(barcode) for barcode in barcodes}
    if None in wells or len(wells) != 1:
        raise ValueError(
            "Run droplet calling within one verified capture, never pooled wells"
        )
    return DropletInput(
        provenance.source_sha256, provenance.capture_id, n_rna, ids, barcodes, totals
    )


def _capture_suffix(barcode: str) -> str | None:
    match = _GEM_WELL.fullmatch(barcode)
    return match[1] if match else None


def _solver_env(scratch: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "R_LIBS_USER": "/opt/rna/scrna-R-library:/opt/rna/R-library",
        "PYTHONPATH": (
            "/opt/rna/cellbender-python:/opt/rna/scrna-python:/opt/rna/python"
        ),
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "XDG_CACHE_HOME": str(scratch / "cache"),
        "MPLCONFIGDIR": str(scratch / "matplotlib"),
    }


def _package_version(method: Method, *, scratch: Path, env: dict[str, str]) -> str:
    if method == "emptydrops":
        rscript = shutil.which("Rscript", path=env["PATH"])
        if not rscript:
            raise _RuntimeUnavailable("Rscript_missing")
        probe = [rscript, "--vanilla", "-e", _R_PACKAGES]
    else:
        probe = [
            sys.executable,
            "-c",
            "from importlib.metadata import version; print(version('cellbender'))",
        ]
    try:
        result = subprocess.run(
            probe,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=scratch,
            env=env,
            timeout=30,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise _RuntimeUnavailable("pinned_droplet_package_unavailable") from None
    installed = result.stdout.strip()
    if not installed:
        raise _RuntimeUnavailable("pinned_droplet_package_unavailable")
    return installed


def _solver_command(
    source: Path,
    destination: Path,
    action: DropletAction,
    runtime: DropletRuntime,
    env: dict[str, str],
) -> list[str]:
    if isinstance(action, EmptyDropsAction):
        rscript = shutil.which("Rscript", path=env["PATH"])
        if rscript is None or not _EMPTYDROPS_DRIVER.is_file():
            raise _RuntimeUnavailable("pinned_emptydrops_driver_unavailable")
        return [
            rscript,
            "--vanilla",
            str(_EMPTYDROPS_DRIVER),
            str(source),
            str(destination),
            str(action.lower),
            str(action.niters),
            str(action.fdr),
            str(action.seed),
            runtime.package_version,
        ]
    return [
        sys.executable,
        "-m",
        "cellbender.base_cli",
        "remove-background",
        "--input",
        str(source),
        "--output",
        str(destination),
        "--expected-cells",
        str(action.expected_cells),
        "--total-droplets-included",
        str(action.total_droplets_included),
        "--fpr",
        str(action.fpr),
        "--cpu-threads",
        "1",
    ]


def _stage_cellbender_input(source: Path, scratch: Path) -> Path:
    """Keep UMI values exact while avoiding unsigned scalar promotion in Torch."""
    destination = scratch / "signed_umi_input.h5"
    with h5py.File(source, "r") as raw, h5py.File(destination, "x") as staged:
        original = _matrix_group(raw, corrected=False)
        matrix = staged.create_group("matrix")
        for name in ("shape", "indices", "indptr", "barcodes"):
            original.copy(name, matrix)
        observed = original["features"]
        features = matrix.create_group("features")
        for name in ("id", "name", "feature_type", "genome"):
            if name in observed:
                observed.copy(name, features)
        values = original["data"]
        output = matrix.create_dataset("data", shape=values.shape, dtype=np.int32)
        for start in range(0, values.size, _CHUNK_ENTRIES):
            block = values[start : start + _CHUNK_ENTRIES]
            signed = block.astype(np.int32)
            if not np.array_equal(block, signed) or np.any(signed < 0):
                raise ValueError("CellBender scratch encoding did not preserve UMIs")
            output[start : start + len(block)] = signed
    return destination


def _run_solver(
    source: Path, destination: Path, action: DropletAction, runtime: DropletRuntime
) -> None:
    scratch = destination.parent
    env = _solver_env(scratch)
    if (
        _package_version(runtime.method, scratch=scratch, env=env)
        != runtime.package_version
    ):
        raise _RuntimeUnavailable("pinned_droplet_package_version_mismatch")
    solver_input = (
        _stage_cellbender_input(source, scratch)
        if isinstance(action, CellBenderAction)
        else source
    )
    command = _solver_command(solver_input, destination, action, runtime, env)
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=scratch,
        env=env,
        timeout=240,
    )


def _read_calls(
    path: Path,
    source: DropletInput,
    action: EmptyDropsAction,
) -> dict[str, int]:
    ref = hash_artifact(path, max_bytes=64 * 1024**2, max_entries=1)
    if ref["kind"] != "file":
        raise ValueError("EmptyDrops output must be a bounded CSV")
    called = tested = limited = 0
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        if tuple(next(reader, ())) != _EMPTYDROPS_FIELDS:
            raise ValueError("EmptyDrops output has an unexpected schema")
        for row, barcode, total in zip(
            reader, source.barcodes, source.rna_totals, strict=True
        ):
            if len(row) != 6 or row[0] != barcode or int(row[1]) != total:
                raise ValueError(
                    "EmptyDrops output differs from original barcode or UMI totals"
                )
            p_value, fdr, is_limited, is_called = row[2:]
            status = _droplet_test_status(
                p_value, fdr, is_limited, is_called, action.fdr
            )
            if status[0] != (total > action.lower):
                raise ValueError(
                    "EmptyDrops output differs from the frozen testing population"
                )
            tested += status[0]
            limited += status[1]
            called += status[2]
    return {
        "barcodes_tested": tested,
        "barcodes_called": called,
        "monte_carlo_limited_barcodes": limited,
    }


def _droplet_test_status(
    p_value: str, fdr: str, limited: str, called: str, threshold: float
) -> tuple[bool, bool, bool]:
    if (p_value, fdr, limited, called) == ("", "", "", "FALSE"):
        return False, False, False
    p, q = float(p_value), float(fdr)
    if (
        not math.isfinite(p)
        or not math.isfinite(q)
        or not 0 <= p <= q <= 1
        or limited not in ("TRUE", "FALSE")
        or called not in ("TRUE", "FALSE")
        or (called == "TRUE") != (q <= threshold)
    ):
        raise ValueError(
            "Invalid EmptyDrops probability, Monte Carlo flag or cell call"
        )
    return True, limited == "TRUE", called == "TRUE"


def _quarantine_cellbender(
    destination: Path,
    source: DropletInput,
    action_sha256: str,
) -> dict[str, int]:
    _regular_h5(destination)
    ids, barcodes, corrected_totals, rna_features = _inspect_h5(
        destination,
        corrected=True,
    )
    if (
        ids != source.feature_ids
        or barcodes != source.barcodes
        or rna_features != source.rna_features
    ):
        raise ValueError(
            "CellBender output differs from the original complete matrix axes"
        )
    with h5py.File(destination, "r+") as handle:
        handle.attrs[_CORRECTION] = "cellbender_corrected"
        handle.attrs["devbench.original_source_sha256"] = source.source_sha256
        handle.attrs["devbench.droplet_action_sha256"] = action_sha256
    if np.any(corrected_totals > source.rna_totals):
        raise ValueError("CellBender correction increased RNA UMIs in a droplet")
    return {
        "barcodes_in_corrected_matrix": len(barcodes),
        "rna_umis_input": int(source.rna_totals.sum()),
        "rna_umis_corrected": int(corrected_totals.sum()),
    }


def _action_record(
    method: Method,
    action: DropletAction,
    provenance_sha256: str,
    source: DropletInput,
    runtime: DropletRuntime | None,
) -> dict:
    parameters = asdict(action)
    if method == "emptydrops":
        parameters["retain"] = "disabled"
    else:
        parameters["seed"] = 1234  # CellBender 0.4.0 fixed internal seed.
        parameters["device"] = "cpu"
        parameters["working_count_storage"] = "lossless_signed_int32_hdf5"
    runtime_record = asdict(runtime) if runtime is not None else None
    document = {
        "format": "devbench.droplet-action/1",
        "method": method,
        "parameters": parameters,
        "runtime": runtime_record,
        "source_sha256": source.source_sha256,
        "source_provenance_sha256": provenance_sha256,
        "capture_id": source.capture_id,
        "input_barcodes": len(source.barcodes),
        "input_rna_features": source.rna_features,
        "clinical_release_allowed": False,
    }
    return {**document, "action_sha256": _digest(document), "artifacts": {}}


def _refusal(source: DropletInput, action: DropletAction) -> str | None:
    if isinstance(action, EmptyDropsAction):
        totals = source.rna_totals
        if not np.any(totals > action.lower) or not np.any(
            (totals > 0) & (totals <= action.lower)
        ):
            return "emptydrops_requires_nonempty_ambient_and_tested_droplets"
    elif action.total_droplets_included > len(source.barcodes):
        return "cellbender_droplet_scope_exceeds_measured_barcodes"
    return None


def _finish_action(
    record: dict,
    destination: Path,
    source: DropletInput,
    action: DropletAction,
) -> dict:
    if isinstance(action, EmptyDropsAction):
        details = _read_calls(destination, source, action)
        role, kind, representation = "cell_calls", "table", "emptydrops_calls_csv"
        history = ["cell_calling"]
    else:
        details = _quarantine_cellbender(destination, source, record["action_sha256"])
        role = "corrected_expression"
        kind, representation = "continuous_matrix", "cellbender_corrected_tenx_h5"
        history = ["cellbender_corrected"]
    artifact = {
        "filename": destination.name,
        "sha256": hash_artifact(destination, max_bytes=_MAX_FILE_BYTES)["sha256"],
        "source_sha256": source.source_sha256,
        "action_sha256": record["action_sha256"],
        "kind": kind,
        "representation": representation,
        "measurement_history": history,
        "count1_admissible": False,
        "permitted_uses": ["checked_observation", "exploratory"],
    }
    return {
        **record,
        "status": "completed",
        "reason": None,
        "metrics": details,
        "artifacts": {role: artifact},
    }


def run_droplet_action(
    source: Path,
    output_dir: Path,
    provenance: RawDropletProvenance,
    *,
    expected_provenance_sha256: str,
    action: DropletAction,
    runtime: DropletRuntime | None = None,
) -> dict:
    """Run in an executor-owned read-only input mount and private output dir."""
    if (
        output_dir.resolve() != output_dir
        or not output_dir.is_dir()
        or any(output_dir.iterdir())
    ):
        raise ValueError("Cell calling requires an empty private output directory")
    original = verify_raw_droplet_input(
        source,
        provenance,
        expected_provenance_sha256=expected_provenance_sha256,
    )
    method: Method = (
        "emptydrops" if isinstance(action, EmptyDropsAction) else "cellbender"
    )
    record = _action_record(
        method, action, expected_provenance_sha256, original, runtime
    )
    if runtime is None:
        return {
            **record,
            "status": "unavailable",
            "reason": "pinned_droplet_runtime_required",
        }
    if (
        runtime.method != method
        or method == "cellbender"
        and runtime.package_version != "0.4.0"
    ):
        raise ValueError(
            "Pinned runtime does not match the selected cell-calling adapter"
        )
    refusal = _refusal(original, action)
    if refusal is not None:
        return {**record, "status": "refused", "reason": refusal}
    return _execute_action(source, output_dir, original, action, runtime, record)


def _execute_action(
    source: Path,
    output_dir: Path,
    original: DropletInput,
    action: DropletAction,
    runtime: DropletRuntime,
    record: dict,
) -> dict:
    with tempfile.TemporaryDirectory(prefix=".droplet-", dir=output_dir) as directory:
        scratch = Path(directory)
        filename = (
            "cell_calls.csv" if isinstance(action, EmptyDropsAction) else "corrected.h5"
        )
        destination = scratch / filename
        try:
            _run_solver(source, destination, action, runtime)
            if _regular_h5(source)["sha256"] != original.source_sha256:
                raise ValueError("Raw droplets changed during solver execution")
            finished = _finish_action(record, destination, original, action)
        except _RuntimeUnavailable as error:
            return {**record, "status": "unavailable", "reason": str(error)}
        except subprocess.TimeoutExpired:
            return {**record, "status": "failed", "reason": "droplet_solver_timeout"}
        except subprocess.CalledProcessError as error:
            return {
                **record,
                "status": "failed",
                "reason": "droplet_solver_exit",
                "exit_code": error.returncode,
            }
        except (OSError, ValueError, csv.Error):
            return {
                **record,
                "status": "failed",
                "reason": "droplet_output_validation_failed",
            }
        manifest = scratch / "summary.json"
        manifest.write_bytes(canonical_json(finished))
        destination.rename(output_dir / filename)
        manifest.rename(output_dir / manifest.name)
    return finished
