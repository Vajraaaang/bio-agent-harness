"""Fixed mounted worker for curated RNA pathway and signed-regulon analyses."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any

import numpy as np

from devbench.core._files import read_bounded_file, read_bounded_json
from devbench.core._hashing import contract_json_bytes
from devbench.core.rna_pathway_analysis import (
    CameraInput,
    GeneSet,
    PathwayRequest,
    PathwayResult,
    PathwayRow,
    eligible_members,
    execute_ora,
    not_tested,
    result_for_request,
    testable_set,
    with_bh,
)

SUMMARY_FORMAT = "devbench.mounted-pathways-summary/1"
MAX_REQUEST_BYTES = 32_000_000
MAX_MATRIX_BYTES = 100_000_000
MAX_RESULT_BYTES = 8_000_000
_DRIVER = Path(__file__).resolve().parent / "r" / "rna_pathways.R"
_R_SCRIPT = Path("/usr/local/bin/Rscript")
_R_PATHS = "/opt/rna/pathway-R-library:/opt/rna/scrna-R-library:/opt/rna/R-library"
_VERSIONS = {"camera": "3.68.5", "fgsea": "1.38.0", "decoupleR": "2.17.0"}


def _checked_matrix(
    path: Path,
    expected: str,
    rows: int,
    columns: int,
) -> tuple[bytes, np.ndarray]:
    size = rows * columns * 8
    if not 0 < size <= MAX_MATRIX_BYTES:
        raise ValueError("CAMERA input exceeds the mounted matrix budget")
    payload = read_bounded_file(path, size)
    if len(payload) != size or sha256(payload).hexdigest() != expected:
        raise ValueError("CAMERA input differs from its dimensions or receipt digest")
    matrix = np.frombuffer(payload, dtype="<f8").reshape((rows, columns))
    if not np.isfinite(matrix).all():
        raise ValueError("CAMERA input contains nonfinite values")
    return payload, matrix


def _camera_payload(
    source: CameraInput,
    expression_path: Path | None,
    design_path: Path | None,
) -> dict[str, bytes]:
    if expression_path is None or design_path is None:
        raise ValueError("CAMERA requires aligned expression and design files")
    n = len(source.samples)
    columns = len(source.contrast)
    design_payload, design = _checked_matrix(
        design_path, source.design_sha256, n, columns
    )
    if n - columns < 2 or np.linalg.matrix_rank(design) != columns:
        raise ValueError("CAMERA requires a full-rank design with residual df >= 2")
    if source.design_scope == "blocked":
        _check_unit_blocks(design, source.biological_units)
    expression_payload, _ = _checked_matrix(
        expression_path,
        source.expression_sha256,
        len(source.genes),
        n,
    )
    return {"expression.bin": expression_payload, "design.bin": design_payload}


def _check_unit_blocks(design: np.ndarray, units: tuple[str, ...]) -> None:
    levels, inverse = np.unique(units, return_inverse=True)
    if len(levels) > design.shape[1]:
        raise ValueError("Repeated units require corresponding fixed blocks")
    indicators = np.eye(len(levels))[inverse]
    fitted = design @ np.linalg.lstsq(design, indicators, rcond=None)[0]
    if not np.allclose(fitted, indicators, atol=1e-8, rtol=0):
        raise ValueError("Repeated units require corresponding fixed blocks")


def _set_payload(item: GeneSet, members: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {"set_id": item.set_id, "members": members}
    if item.weights is not None:
        weights = dict(zip(item.genes, item.weights, strict=True))
        payload["weights"] = tuple(weights[gene] for gene in members)
    return payload


def _solver_payload(
    request: PathwayRequest,
    memberships: tuple[tuple[str, ...], ...],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "method": request.method,
        "genes": request.genes,
        "sets": [
            _set_payload(item, members)
            for item, members in zip(request.plan.gene_sets, memberships, strict=True)
            if testable_set(request, members)
        ],
        "min_set_size": request.plan.min_set_size,
        "max_set_size": request.plan.max_set_size,
        "seed": request.plan.seed,
    }
    if request.camera is not None:
        body.update(
            contrast=request.camera.contrast, samples=len(request.camera.samples)
        )
    else:
        body["scores"] = request.ranked_scores
    return body


def _invoke_solver(
    workspace: Path,
    solver: dict[str, Any],
    binaries: dict[str, bytes],
) -> dict[str, Any]:
    if workspace.resolve() != workspace or not workspace.is_absolute():
        raise ValueError("Pathway worker requires an absolute staging directory")
    with tempfile.TemporaryDirectory(prefix=".pathway-", dir=workspace) as scratch:
        directory = Path(scratch)
        (directory / "solver.json").write_bytes(contract_json_bytes(solver))
        for name, payload in binaries.items():
            (directory / name).write_bytes(payload)
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "R_LIBS_USER": _R_PATHS,
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": str(directory),
        }
        subprocess.run(
            [str(_R_SCRIPT), "--vanilla", str(_DRIVER), str(directory)],
            cwd=directory,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=240,
        )
        output = read_bounded_json(directory / "result.json", MAX_RESULT_BYTES)
        if not isinstance(output, dict):
            raise ValueError("Invalid pathway worker output")
    return output


def _unestimated(set_id: str, count: int) -> PathwayRow:
    return PathwayRow(
        set_id=set_id,
        tested_members=count,
        status="not_estimable",
        reason="registered_solver_estimate_unavailable",
    )


def _optional_number(raw: Mapping[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError("Pathway worker returned nonfinite statistics")
    return float(value)


def _estimated_row(
    request: PathwayRequest,
    set_id: str,
    count: int,
    raw: dict[str, Any],
) -> PathwayRow:
    score = _optional_number(raw, "score")
    p = _optional_number(raw, "p_value")
    if request.method == "decoupleR":
        if score is None or p is not None:
            raise ValueError("Activity scores cannot carry confirmatory probabilities")
    elif p is None:
        return _unestimated(set_id, count)
    direction = raw.get("direction")
    if direction is None and score:
        direction = "up" if score > 0 else "down"
    return PathwayRow(
        set_id=set_id,
        tested_members=count,
        status="estimated",
        score=score,
        p_value=p,
        q_value=p,
        direction=direction,
        inter_gene_correlation=_optional_number(raw, "correlation"),
        variance_inflation=_optional_number(raw, "vif"),
    )


def _verified_rows(
    request: PathwayRequest,
    memberships: tuple[tuple[str, ...], ...],
    records: Any,
) -> tuple[PathwayRow, ...]:
    eligible = {
        item.set_id: len(members)
        for item, members in zip(request.plan.gene_sets, memberships, strict=True)
        if testable_set(request, members)
    }
    if not isinstance(records, list) or len(records) > len(eligible):
        raise ValueError("Invalid pathway solver hypothesis family")
    observed = {}
    for row in records:
        set_id = row["set_id"]
        count = eligible.get(set_id)
        if count is None or set_id in observed or row["tested_members"] != count:
            raise ValueError("Pathway solver changed the eligible set inventory")
        observed[set_id] = _estimated_row(request, set_id, count, row)
    family = []
    for item, members in zip(request.plan.gene_sets, memberships, strict=True):
        if item.set_id not in eligible:
            family.append(not_tested(item.set_id, members))
        else:
            family.append(
                observed.get(item.set_id) or _unestimated(item.set_id, len(members))
            )
    return with_bh(family) if request.method != "decoupleR" else tuple(family)


def execute_pathway_request(
    request: PathwayRequest,
    *,
    expression_path: Path | None = None,
    design_path: Path | None = None,
    workspace: Path | None = None,
) -> PathwayResult:
    """Execute an admitted request only on the registered R version/package path."""
    if request.method == "ora":
        return execute_ora(request)
    binaries = (
        _camera_payload(request.camera, expression_path, design_path)
        if request.camera is not None
        else {}
    )
    memberships = eligible_members(request)
    solver = _solver_payload(request, memberships)
    if not solver["sets"]:
        rows = tuple(
            not_tested(item.set_id, members)
            for item, members in zip(request.plan.gene_sets, memberships, strict=True)
        )
        return result_for_request(request, rows=rows)
    if workspace is None or not _R_SCRIPT.is_file():
        return result_for_request(
            request,
            status="unavailable",
            reason=f"pinned_{request.method}_unavailable",
        )
    try:
        output = _invoke_solver(workspace, solver, binaries)
        return _solver_result(request, output, memberships)
    except subprocess.TimeoutExpired:
        return result_for_request(
            request, status="failed", reason="pathway_solver_timeout"
        )
    except subprocess.CalledProcessError:
        return result_for_request(
            request, status="failed", reason="pathway_solver_failed"
        )
    except (OSError, ValueError, KeyError, TypeError):
        return result_for_request(
            request, status="failed", reason="pathway_solver_contract_failed"
        )


def _solver_result(
    request: PathwayRequest,
    output: dict[str, Any],
    memberships: tuple[tuple[str, ...], ...],
) -> PathwayResult:
    if output["status"] == "unavailable":
        return result_for_request(
            request,
            status="unavailable",
            reason=f"pinned_{request.method}_unavailable",
        )
    if (
        output["status"] != "completed"
        or output["version"] != _VERSIONS[request.method]
    ):
        raise ValueError("Pathway runtime differs from the frozen package identity")
    rows = _verified_rows(request, memberships, output["rows"])
    return result_for_request(request, rows=rows, package_version=output["version"])


def run_mounted_pathways(
    inputs: Path, outputs: Path, request_sha256: str
) -> dict[str, Any]:
    """Worker admission relies on the coordinator's retained request digest."""
    payload = read_bounded_file(inputs / "request.json", MAX_REQUEST_BYTES)
    if not re.fullmatch("[a-f0-9]{64}", request_sha256) or (
        sha256(payload).hexdigest() != request_sha256
    ):
        raise ValueError("Mounted pathway request hash mismatch")
    request = PathwayRequest.model_validate_json(payload)
    if contract_json_bytes(request.model_dump(mode="json")) != payload:
        raise ValueError("Mounted pathway request must be the canonical typed document")
    names = {"request.json"}
    if request.camera is not None:
        names.update(("expression.bin", "design.bin"))
    if {entry.name for entry in inputs.iterdir()} != names:
        raise ValueError("Unexpected mounted pathway inputs")
    if (
        not outputs.is_absolute()
        or outputs.resolve() != outputs
        or any(outputs.iterdir())
    ):
        raise ValueError("Mounted pathway output directory must be empty and absolute")
    result = execute_pathway_request(
        request,
        expression_path=inputs / "expression.bin",
        design_path=inputs / "design.bin",
        workspace=outputs,
    )
    data = contract_json_bytes(result.model_dump(mode="json"))
    if len(data) > MAX_RESULT_BYTES:
        raise ValueError("Mounted pathway results exceed the output budget")
    (outputs / "results.json").write_bytes(data)
    summary = {
        "format": SUMMARY_FORMAT,
        "operation": request.method,
        "request_sha256": request_sha256,
        "status": result.status,
        "reason": result.reason,
        "output_sha256": {"results.json": sha256(data).hexdigest()},
        "claim_scope": result.claim_scope,
    }
    (outputs / "summary.json").write_bytes(contract_json_bytes(summary))
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_sha256")
    arguments = parser.parse_args(argv)
    run_mounted_pathways(Path("/inputs"), Path("/outputs"), arguments.request_sha256)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
