"""Fixed binary bridge to the image's pinned edgeR quasi-likelihood solver."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .streaming_rle import streaming_median_ratio_factors

_DRIVER = Path(__file__).resolve().parent.parent / "devharness_server/r/mounted_edger.R"
_MAX_BINARY_BYTES = 512 * 1024**2
_REASONS = {
    1: ("filtered", "independent_low_count_filter"),
    2: ("failed", "zero_condition_marginal"),
    3: ("failed", "solver_fit_unavailable"),
    4: ("failed", "residual_variance_unavailable"),
}


def _blocks(source):
    for _, data in source.iter_gene_chunks(256):
        yield data


def _write_matrices(directory, source, design, factors):
    genes, samples = source.shape
    if genes * samples * 8 > _MAX_BINARY_BYTES:
        raise ValueError("Count matrix exceeds the binary solver's scratch bound")
    scanned = 0
    with (directory / "counts.bin").open("xb") as output:
        for block in _blocks(source):
            np.asarray(block, dtype="<f8").tofile(output)
            scanned += len(block)
    if scanned != genes:
        raise ValueError("Count solver matrix did not cover the feature axis")
    np.asarray(design, dtype="<f8").tofile(directory / "design.bin")
    np.asarray(factors, dtype="<f8").tofile(directory / "rle.bin")


def _fit_count(source, design, scratch, *, solver):
    genes, samples = source.shape
    with tempfile.TemporaryDirectory(dir=scratch) as name:
        directory = Path(name)
        factors, normalization = streaming_median_ratio_factors(
            lambda: _blocks(source),
            samples,
            scratch_dir=directory,
        )
        _write_matrices(directory, source, design, factors)
        command = [
            "Rscript",
            "--vanilla",
            str(_DRIVER),
            str(directory),
            str(genes),
            str(samples),
            str(design.shape[1]),
            solver,
        ]
        with (directory / "diagnostics.log").open("xb") as diagnostics:
            try:
                subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=diagnostics,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=240,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise ValueError(f"Mounted {solver} execution failed") from error
        result = directory / "result.bin"
        if result.stat().st_size != 3 * genes * 8:
            raise ValueError(
                "Mounted count result does not cover the exact gene inventory"
            )
        effects, probabilities, codes = np.fromfile(result, dtype="<f8").reshape(
            3, genes
        )
        with (directory / "edger-version.txt").open("r", encoding="ascii") as stream:
            version = stream.read(32).strip()
    eligible = codes == 0
    expected_major = "4" if solver == "edger_v4" else "1"
    if (
        not np.isin(codes, (0, 1, 2, 3)).all()
        or not np.isfinite(effects[eligible]).all()
        or not ((0 <= probabilities[eligible]) & (probabilities[eligible] <= 1)).all()
        or not np.isnan(probabilities[~eligible]).all()
        or not re.fullmatch(expected_major + r"\.\d+\.\d+", version)
    ):
        raise ValueError(
            "Mounted count output contains invalid statuses, probabilities or version"
        )
    return (
        effects,
        probabilities,
        codes.astype(np.int8),
        {
            "solver": "edger_v4_qlf" if solver == "edger_v4" else "deseq2_wald_apeglm",
            "solver_version": version,
            "normalization": {**normalization, "size_factors": factors.tolist()},
            "low_count_filter": "mean_RLE_normalized_counts_at_least_one",
        },
    )
