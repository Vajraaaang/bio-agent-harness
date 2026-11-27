"""Run a separate, source-bound latent diagnostic for an accepted paired fit."""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass, replace
from hashlib import sha256
from importlib.resources import files
from pathlib import Path
from tempfile import mkdtemp
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np

from devbench.core._files import hash_bounded_file, read_bounded_file, read_bounded_json
from devbench.core.execution_contracts import ResourceProfile
from devbench.engine.mounted_kernel_oci import (
    _INTENT,
    _RECOVERED,
    _clean,
    _configuration,
    _daemon,
    _identity,
    _mount_arguments,
    _oci_lock,
    _owned,
    _private_runtime,
    _start_container,
)
from devbench.engine.permissions import require_protected_storage
from devbench.engine.tier_a_executor import _RECORD_LIMIT, _read_record, _write_record
from devharness_controlled.native_contracts import kernel_test_platform
from devharness_controlled.native_runtime import NativeRuntimeError
from devharness_core.provenance import canonical_json
from devharness_core.storage import ChunkedExpressionStore
from rna_kernels.mounted_bulk import _check_matrix_storage, _metadata, _request
from rna_kernels.mounted_model import _write_design_inputs

if TYPE_CHECKING:
    from devbench.core.contrast_design import PipelineSpec
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow, ArtifactVersion

_FORMAT = "devbench.paired-tximport-svaseq/1"
_REQUEST = "devbench.paired-tximport-svaseq-request/1"
_DRIVER_SHA = "49e57aa16affeb76644fda7dc49288051e106249b0ef586e97c9a888813c72f6"
_VERSIONS = {"R": "4.6.1", "edgeR": "4.10.7", "sva": "3.60.0"}
_MAX_ENTRIES = 4_000_000
_MAX_BYTES = 2 * 1024**2
_TIMEOUT = 120
_MEMORY_MB = 2048
_MATRICES = ("counts.bin", "lengths.bin", "design.bin", "reduced.bin")


@dataclass(frozen=True)
class _Subject:
    output: ArtifactVersion
    outcome: dict[str, Any]
    envelope: dict[str, Any]
    execution: tuple[int, str, str]


def _subject(workflow: AdaptiveWorkflow, step_id: str) -> _Subject:
    def read(db):
        authority, lineage = workflow.authority, workflow.lineage_id
        execution = authority._load_state(db, lineage)
        authority._active(db, lineage, execution)
        artifact = workflow._current_use(db, step_id, "result", "claim", None)
        state = workflow._state(db)
        outcome = state.outcome(step_id)
        return _Subject(
            artifact,
            outcome,
            state.dispatches[outcome["attempt_id"]]["envelope"],
            (execution["epoch"], execution["binding_id"], execution["grant_id"]),
        )

    return workflow._read_projection(read)


def _accepted_sources(
    executor: TierAExecutor, subject: _Subject
) -> tuple[PipelineSpec, str, str]:
    envelope = subject.envelope
    attempt = subject.outcome["attempt_id"]
    receipt = executor.load_receipt(envelope, attempt_id=attempt)
    if receipt is None or receipt.outputs.get("result") != subject.output:
        raise ValueError("Paired diagnostic needs the accepted Tier A result receipt")
    receipt_sha = sha256(canonical_json(receipt.model_dump(mode="json"))).hexdigest()
    if receipt_sha != subject.outcome["receipt_sha256"]:
        raise ValueError("Paired diagnostic receipt differs from the accepted record")
    retained = _read_record(executor._directory(attempt) / "io.json", _RECORD_LIMIT)[
        "tool"
    ]
    transport = retained.get("oci", {})
    image = transport.get("image_id")
    if (
        retained.get("tool") != "mounted_bulk"
        or not isinstance(image, str)
        or image != executor.mounted_image_id
        or kernel_test_platform(image) is None
        or transport.get("cleanup_confirmed") is not True
    ):
        raise ValueError(
            "Paired diagnostic requires the locked image of the accepted fit"
        )
    spec = executor._pipeline(envelope)
    model = spec.mounted_model
    if (
        spec.measurement != "tximport_estimated_counts"
        or model is None
        or model.units != "paired_fixed"
        or model.comparison != "coefficient"
        or model.lengths_sha256 is None
        or retained["method"]["solver"] != "edger_v4"
    ):
        raise ValueError(
            "Paired svaseq requires a frozen paired tximport edgeR coefficient"
        )
    return spec, image, retained["request_sha256"]


def _driver(mount: Path) -> None:
    installed = files("devharness_server").joinpath("r", "paired_tximport_svaseq.R")
    with installed.open("rb") as handle:
        raw = handle.read(65_537)
    if len(raw) > 65_536 or sha256(raw).hexdigest() != _DRIVER_SHA:
        raise ValueError(
            "Paired tximport diagnostic driver differs from its version pin"
        )
    path = mount / "diagnostic.R"
    path.write_bytes(raw)
    path.chmod(0o400)


def _stage_inputs(executor, subject, spec, root, mount):
    from devbench.platform.adaptive_workflow import ArtifactVersion

    stage = root / "source"
    stage.mkdir(mode=0o700)
    versions = {
        port: ArtifactVersion.model_validate(row)
        for port, row in subject.envelope["inputs"].items()
    }
    request_sha = executor._stage_mounted_inputs(versions, stage, spec)[
        "request_sha256"
    ]
    inputs = stage / "inputs"
    request = _request(inputs, request_sha)
    expression = inputs / request["expression"]
    lengths = inputs / "lengths.h5"
    _check_matrix_storage(expression)
    _check_matrix_storage(lengths)
    with ChunkedExpressionStore(expression) as matrix:
        if math.prod(matrix.shape) > _MAX_ENTRIES:
            raise ValueError(
                "Paired tximport diagnostic exceeds its matrix-entry bound"
            )
        if tuple(matrix.gene_names) != spec.family.feature_ids:
            raise ValueError("Paired tximport diagnostic feature axis changed")
        samples = matrix.sample_names
        _, _, cohort = _metadata(
            read_bounded_file(inputs / "metadata.csv", 5_000_000),
            samples,
            request,
        )
        reduced = np.delete(cohort.design, 1, axis=1)
        _write_design_inputs(
            mount,
            matrix,
            replace(cohort, reduced=reduced),
            spec.measurement,
            lengths,
        )
        genes, groups, units = matrix.gene_names, cohort.labels, cohort.unit_ids
    return cohort.design, reduced, genes, samples, groups, units, request_sha


def _checked_driver(stage: Path, request_sha: str, image: str) -> dict:
    """Use the mounted worker's container isolation without changing its contract."""
    directory = stage.parent
    binding = _identity(request_sha, image, uuid4().hex)
    with _oci_lock(directory), _private_runtime(stage) as runtime:
        platform = _configuration(runtime, image)
        if platform != kernel_test_platform(image):
            raise ValueError(
                "Paired tximport diagnostic kernel platform differs from lock"
            )
        profile = ResourceProfile(memory_mb=_MEMORY_MB, timeout_sec=_TIMEOUT)
        args = _mount_arguments(
            stage,
            image,
            binding["name"],
            request_sha,
            _TIMEOUT,
            _MEMORY_MB,
            platform,
            resource_profile=profile,
        )
        if args[-4:] != ["python3.13", "-m", "rna_kernels.mounted_bulk", request_sha]:
            raise ValueError("Mounted isolation command differs from its frozen tail")
        command = [
            "/usr/local/bin/Rscript",
            "--vanilla",
            "/inputs/diagnostic.R",
            request_sha,
        ]
        args[-4:] = command
        intent = {**binding, **_daemon(runtime)}
        _write_record(directory / _INTENT, intent)
        error = None
        try:
            container = runtime._call(args)
            inspected = _owned(runtime, binding["name"], image, request_sha, container)
            expected = ["--signal=KILL", str(_TIMEOUT), *command]
            if inspected["Config"].get("Cmd") != expected:
                raise NativeRuntimeError(
                    "Paired diagnostic command differs from its pin"
                )
            _start_container(runtime, stage, intent, container, profile, expected)
        except NativeRuntimeError as failure:
            error = failure
        proof = _clean(runtime, stage, intent)
        if intent != {**binding, **_daemon(runtime)}:
            raise NativeRuntimeError("Paired diagnostic cleanup daemon mismatch")
        _write_record(directory / _RECOVERED, proof)
        if error is not None:
            raise error
        return {**proof, "platform": platform}


def _check_design(reported: dict, matrix: np.ndarray) -> None:
    rank = int(np.linalg.matrix_rank(matrix))
    profiles, columns = matrix.shape
    condition = float(np.linalg.cond(matrix))
    if (
        rank != columns
        or not math.isfinite(condition)
        or condition > 1000
        or profiles <= rank
        or reported["passes"] is not True
        or (rank, columns, profiles - rank)
        != (
            reported["rank"],
            reported["columns"],
            reported["residual_df"],
        )
        or not math.isclose(reported["condition_number"], condition, rel_tol=1e-7)
    ):
        raise ValueError("Paired diagnostic design evidence does not match its matrix")


def _score_matrix(stats: dict, samples: list[str], residual: int) -> np.ndarray:
    count = stats["n_factors"]
    scores = stats["scores"]
    if type(count) is not int or not 0 <= count < residual or type(scores) is not list:
        raise ValueError("Paired diagnostic scores differ from the residual support")
    if count == 0 and scores == []:
        return np.empty((len(samples), 0))
    if [row["sample_id"] for row in scores] != samples:
        raise ValueError("Paired diagnostic scores differ from the sample axis")
    values = np.asarray([row["values"] for row in scores])
    if values.shape != (len(samples), count) or values.dtype.kind not in "iuf":
        raise ValueError("Paired diagnostic scores have invalid dimensions or dtype")
    if not np.isfinite(values).all():
        raise ValueError("Paired diagnostic scores must be finite")
    return values


def _diagnostics(path, request_sha, samples, unit_ids, design, reduced) -> dict:
    result = read_bounded_json(path, _MAX_BYTES)
    if (
        set(result) != {"request_sha256", "diagnostics"}
        or result["request_sha256"] != request_sha
    ):
        raise ValueError("Paired tximport diagnostic response differs from its request")
    stats = result["diagnostics"]
    if (
        (stats.get("method"), stats.get("factor_estimator"), stats.get("seed"))
        != ("svaseq", "leek", 0)
        or stats.get("versions") != _VERSIONS
        or stats.get("input_scale")
        != "tximport_TMM_offset_normalized_estimated_expression"
        or stats.get("observed_profiles") != len(samples)
        or stats.get("declared_donors") != len(set(unit_ids))
        or any(field in stats for field in ("p_value", "q_value", "effect"))
    ):
        raise ValueError("Paired diagnostic result differs from the frozen methodology")
    for key, matrix in (("full", design), ("reduced", reduced)):
        _check_design(stats["design"][key], matrix)
    if stats.get("status") == "unavailable":
        if (
            not stats.get("reason")
            or stats["scores"] is not None
            or stats["n_factors"] is not None
        ):
            raise ValueError("Unavailable paired diagnostic cannot carry estimates")
        return stats
    if stats.get("status") != "completed" or stats.get("reason") is not None:
        raise ValueError("Unknown paired diagnostic status")
    scores = _score_matrix(stats, samples, len(samples) - design.shape[1])
    for key, matrix in (("full", design), ("reduced", reduced)):
        _check_design(stats["augmented_design"][key], np.column_stack((matrix, scores)))
    return stats


def _execute(executor, subject, spec, image, primary_request, root, workflow, step_id):
    stage = root / "work"
    mount = stage / "inputs"
    mount.mkdir(mode=0o700, parents=True)
    outputs = stage / "outputs"
    outputs.mkdir(mode=0o700)
    design, reduced, genes, samples, groups, units, original_request = _stage_inputs(
        executor,
        subject,
        spec,
        root,
        mount,
    )
    if original_request != primary_request:
        raise ValueError(
            "Paired diagnostic does not restage the accepted mounted inputs"
        )
    _driver(mount)
    matrices = {
        name: hash_bounded_file(mount / name, _MAX_ENTRIES * 8) for name in _MATRICES
    }
    record = {
        "format": _REQUEST,
        "driver_sha256": _DRIVER_SHA,
        "matrices": matrices,
        "primary_request_sha256": original_request,
        "primary_receipt_sha256": subject.outcome["receipt_sha256"],
        "genes": genes,
        "samples": samples,
        "groups": groups,
        "unit_ids": units,
        "columns": [f"coef_{i + 1}" for i in range(design.shape[1])],
        "numerator": spec.contrast.numerator,
        "denominator": spec.contrast.denominator,
    }
    _write_record(mount / "request.json", record)
    checksum = sha256(canonical_json(record)).hexdigest()
    if _subject(workflow, step_id) != subject:
        raise ValueError("Accepted paired diagnostic inputs changed before launch")
    transport = _checked_driver(stage, checksum, image)
    result = outputs / "diagnostics.json"
    stats = _diagnostics(result, checksum, samples, units, design, reduced)
    if _subject(workflow, step_id) != subject:
        raise ValueError("Accepted primary fit changed during the paired diagnostic")
    _accepted_sources(executor, subject)
    return {
        "format": _FORMAT,
        "diagnostic_only": True,
        "scientific_validity_established": False,
        "primary": {
            "step_id": step_id,
            "attempt_id": subject.outcome["attempt_id"],
            "receipt_sha256": subject.outcome["receipt_sha256"],
            "artifact_sha256": subject.output.sha256,
            "parameters_sha256": subject.envelope["step"]["parameters_sha256"],
            "expression_sha256": spec.expression_sha256,
            "metadata_sha256": spec.metadata_sha256,
            "lengths_sha256": spec.mounted_model.lengths_sha256,
            "family_sha256": spec.family_sha256,
            "request_sha256": original_request,
        },
        "driver_sha256": _DRIVER_SHA,
        "request_sha256": checksum,
        "output_sha256": hash_bounded_file(result, _MAX_BYTES),
        "sample_axis_sha256": sha256(canonical_json(samples)).hexdigest(),
        "matrix_sha256": matrices,
        "transport": transport,
        "diagnostics": stats,
    }


def run_paired_tximport_svaseq(
    workflow: AdaptiveWorkflow,
    executor: TierAExecutor,
    step_id: str,
    destination: Path,
) -> dict[str, Any]:
    """Retain a checked diagnostic beside a current paired tximport coefficient."""
    if (
        workflow.ledger is not executor.ledger
        or executor.allow_data_execution is not True
    ):
        raise PermissionError(
            "Paired diagnostics require the active Tier A execution grant"
        )
    destination = Path(destination)
    if not destination.is_absolute() or destination != destination.resolve():
        raise ValueError("Use a canonical absolute path for the paired diagnostic")
    require_protected_storage(destination.parent)
    if destination.exists():
        raise FileExistsError(destination)
    subject = _subject(workflow, step_id)
    spec, image, primary_request = _accepted_sources(executor, subject)
    scratch = Path(mkdtemp(dir=destination.parent, prefix=".paired-svaseq-"))
    try:
        result = _execute(
            executor,
            subject,
            spec,
            image,
            primary_request,
            scratch,
            workflow,
            step_id,
        )
        _write_record(destination, result)
        return {"sha256": sha256(canonical_json(result)).hexdigest(), "result": result}
    finally:
        # Keep the recovery binding and private mounts if OCI cleanup is uncertain.
        if not (scratch / _INTENT).exists() or (scratch / _RECOVERED).exists():
            shutil.rmtree(scratch)
