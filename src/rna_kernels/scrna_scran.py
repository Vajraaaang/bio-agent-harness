"""Run pinned scran pooling on a bounded full-transcriptome count export."""

from __future__ import annotations

import subprocess
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.core._files import read_bounded_file

from .canonical import canonical_json
from .scrna_droplets import _solver_env

if TYPE_CHECKING:
    import pandas as pd

    from devbench.analysis.checkpoints import CheckpointStore

_RSCRIPT = Path("/usr/local/bin/Rscript")
_LIBRARY = Path("/opt/rna/scrna-R-library/scran")
_VERSION = "1.40.0"
_DRIVER = Path(__file__).resolve().parent / "r/scrna_scran.R"
_MAX_MTX_BYTES = 256 * 1024**2
_MAX_NONZERO = 8_000_000
_MAX_CELLS = 100_000
_MAX_FACTOR_BYTES = 32 * _MAX_CELLS


def _pinned_runtime(folder: Path) -> bool:
    if not all(path.exists() for path in (_RSCRIPT, _LIBRARY, _DRIVER)):
        return False
    try:
        result = subprocess.run(
            [str(_RSCRIPT), "--vanilla", str(_DRIVER), "--probe"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=folder,
            env=_solver_env(folder),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout == _VERSION.encode()


def _write_sparse_counts(
    source_path: Path,
    selected: pd.Index,
    destination: Path,
) -> tuple[int, int]:
    import numpy as np
    from scipy import sparse

    from devbench.analysis.singlecell import validate_counts
    from devbench.core.expression_io import open_h5ad

    with (
        open_h5ad(source_path, layer="counts") as source,
        destination.open("xb") as sink,
    ):
        positions = source.obs.index.get_indexer(selected)
        if len(set(positions)) != len(selected) or np.any(positions < 0):
            raise ValueError("scran_cell_axis_differs_from_QC")
        cells, features = len(positions), source.shape[1]
        sink.write(b"%%MatrixMarket matrix coordinate real general\n")
        dims = sink.tell()
        sink.write(f"{features} {cells} {0:010d}\n".encode())
        entries = 0
        for start in range(0, cells, 128):
            data = source.subset(positions[start : start + 128], max_bytes=64 * 1024**2)
            validate_counts(data.X)
            block = sparse.csr_matrix(data.X, dtype=np.float64)
            block.sum_duplicates()
            block.eliminate_zeros()
            validate_counts(block)
            entries += block.nnz
            if entries > _MAX_NONZERO:
                raise ValueError("scran_full_count_export_exceeds_sparse_budget")
            _write_coo(sink, block.tocoo(), start)
        sink.seek(dims)
        sink.write(f"{features} {cells} {entries:010d}\n".encode())
    return features, entries


def _write_coo(sink, block, start: int) -> None:
    import numpy as np

    for offset in range(0, block.nnz, 32_768):
        chunk = slice(offset, offset + 32_768)
        triplets = np.column_stack(
            (
                block.col[chunk] + 1,
                block.row[chunk] + start + 1,
                block.data[chunk].astype(np.int64),
            )
        )
        np.savetxt(sink, triplets, fmt="%d")
        if sink.tell() > _MAX_MTX_BYTES:
            raise ValueError("scran_full_count_export_exceeds_byte_budget")


def _fit(
    source: Path,
    selected: pd.Index,
    scratch: Path,
) -> tuple[Any, dict[str, Any]]:
    import numpy as np

    matrix = scratch / "full-counts.mtx"
    result = scratch / "size-factors.txt"
    features, entries = _write_sparse_counts(source, selected, matrix)
    subprocess.run(
        [str(_RSCRIPT), "--vanilla", str(_DRIVER), str(matrix), str(result)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=scratch,
        env=_solver_env(scratch),
        timeout=240,
        check=True,
    )
    raw = read_bounded_file(result, _MAX_FACTOR_BYTES)
    factors = np.asarray(raw.splitlines(), dtype="<f8")
    if (
        factors.shape != (len(selected),)
        or not np.isfinite(factors).all()
        or (factors <= 0).any()
    ):
        raise ValueError("invalid_scran_size_factors")
    return factors, {
        "status": "completed",
        "features": features,
        "input_nonzeros": entries,
        "size_factor_float64_sha256": sha256(factors.tobytes()).hexdigest(),
        "pool_sizes": list(range(21, min(101, len(selected) - 1) + 1, 5)),
        "minimum_mean": 0.1,
        "invalid_size_factor_policy": "refuse",
    }


def _scran_report(
    source: Path,
    retained: pd.Index,
    folder: Path,
) -> dict[str, Any]:
    import pandas as pd

    from devbench.analysis.checkpoints import digest

    report = {
        "method": "scran_compute_sum_factors",
        "requested_package_version": _VERSION,
        "qc_passing_cells": len(retained),
        "cell_axis_sha256": digest(retained.tolist()),
        "input_scope": "qc_passing_cells_full_transcriptome_raw_counts",
        "transformation": "log1p(counts / scran_size_factor)",
    }
    if not 22 <= len(retained) <= _MAX_CELLS:
        return {**report, "status": "refused", "reason": "scran_pooling_cohort_bound"}
    with tempfile.TemporaryDirectory(prefix=".scran-", dir=folder) as directory:
        scratch = Path(directory)
        if not _pinned_runtime(scratch):
            return {
                **report,
                "status": "unavailable",
                "reason": "scran_runtime_unavailable",
            }
        try:
            factors, details = _fit(source, retained, scratch)
        except subprocess.TimeoutExpired:
            return {**report, "status": "failed", "reason": "scran_solver_timeout"}
        except subprocess.CalledProcessError as error:
            return {
                **report,
                "status": "failed",
                "reason": "scran_solver_exit",
                "exit_code": error.returncode,
            }
        except ValueError as error:
            return {**report, "status": "refused", "reason": str(error)[:128]}
    pd.DataFrame({"scran_size_factor": factors}, index=retained).to_csv(
        folder / "factors.csv",
    )
    return {**report, **details, "observed_package_version": _VERSION}


def run_scran_factors(source: Path, store: CheckpointStore, retained: pd.Index) -> Path:
    """Retain a factor checkpoint or an explicit solver disposition."""

    def run(folder: Path) -> dict[str, Any]:
        report = _scran_report(source, retained, folder)
        (folder / "report.json").write_bytes(canonical_json(report))
        return report

    return store.run("scran_factors", run, dependencies=("qc",))
