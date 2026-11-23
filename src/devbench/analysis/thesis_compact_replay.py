"""Replay the registered compact Tier A contrast in its original host runtime."""

from __future__ import annotations

import platform
import time
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from devbench.core._files import read_bounded_file
from devbench.core.contrast_design import PipelineSpec
from devbench.core.publication_families import FamilySpec
from devbench.core.scientific_types import FrozenContract, Sha256
from rna_kernels.canonical import canonical_json

COMPACT_TRANSPORT = "registered_bulk/1"
COMPACT_FILES = frozenset({"expression.csv", "metadata.csv", "family.json"})


class CompactReplayRequest(FrozenContract):
    format: Literal["devbench.thesis-compact-replay/1"] = (
        "devbench.thesis-compact-replay/1"
    )
    dispatch_request_sha256: Sha256
    code_sha256: Sha256
    environment_sha256: Sha256
    mounted_image_id: str | None
    allow_python_wald: bool
    platform: str
    timeout_seconds: Annotated[int, Field(gt=0, le=300)]
    memory_mb: Annotated[int, Field(gt=0, le=2048)]


def capture_compact_replay(executor, envelope, receipt, pipeline: PipelineSpec):
    executor._require_current_identity()
    if envelope["environment_sha256"] != executor.environment_sha256:
        raise ValueError("Compact replay requires the original Tier A runtime")
    capability = next(
        item
        for item in envelope["step"]["profile"]["operations"]
        if item["operation_id"] == envelope["step"]["operation_id"]
    )
    request = CompactReplayRequest(
        dispatch_request_sha256=receipt.request_sha256,
        code_sha256=executor.code_sha256,
        environment_sha256=executor.environment_sha256,
        mounted_image_id=executor.mounted_image_id,
        allow_python_wald=executor.allow_python_wald,
        platform=platform.machine(),
        timeout_seconds=capability["timeout_seconds"],
        memory_mb=capability["max_memory_mb"],
    )
    inputs = {
        "expression.csv": pipeline.expression_sha256,
        "metadata.csv": pipeline.metadata_sha256,
        "family.json": pipeline.family_sha256,
    }
    return canonical_json(request.model_dump(mode="json")), inputs


def _runtime(request: CompactReplayRequest):
    from devbench.engine.tier_a_executor import TierAExecutor, _runtime_identity

    identity = (
        request.code_sha256,
        request.environment_sha256,
        request.mounted_image_id,
        request.allow_python_wald,
    )
    if (
        request.platform != platform.machine()
        or _runtime_identity(TierAExecutor.backend_id, *identity[2:]) != identity[:2]
    ):
        raise ValueError(
            "Compact replay requires the recorded host runtime and platform"
        )
    return identity


def _bound_pipeline(root: Path, stage: Path, recipe: dict[str, Any]) -> PipelineSpec:
    raw = read_bounded_file(root / recipe["plan_path"], 1_000_000)
    if sha256(raw).hexdigest() != recipe["parameters_sha256"]:
        raise ValueError("Compact replay plan differs from its captured digest")
    pipeline = PipelineSpec.model_validate_json(raw)
    input_digests = {
        "expression.csv": pipeline.expression_sha256,
        "metadata.csv": pipeline.metadata_sha256,
        "family.json": pipeline.family_sha256,
    }
    family = stage / "inputs" / "family.json"
    if (
        pipeline.format != "devbench.tier-a-bulk-pipeline/1"
        or input_digests != recipe["inputs"]
        or FamilySpec.model_validate_json(read_bounded_file(family, 16_000_000))
        != pipeline.family
    ):
        raise ValueError("Compact replay inputs differ from the frozen pipeline")
    return pipeline


def replay_compact_fit(
    root: Path, stage: Path, raw: bytes, recipe: dict[str, Any]
) -> None:
    from devbench.engine.tier_a_executor import _execute_registered_bulk, _result_table
    from devharness_core.watchdog import run_sandboxed, worker_limits

    request = CompactReplayRequest.model_validate_json(raw)
    identity = _runtime(request)
    pipeline = _bound_pipeline(root, stage, recipe)
    task, options = pipeline.tool_request()
    invocation = {
        "counts_path": str(stage / "inputs" / "expression.csv"),
        "metadata_path": str(stage / "inputs" / "metadata.csv"),
        "public_task": task,
        "options": options,
    }
    diagnostics: dict[str, Any] = {}
    with worker_limits(
        deadline=time.monotonic() + request.timeout_seconds,
        max_mem_mb=request.memory_mb,
        scratch_dir=stage,
    ):
        result, tool = run_sandboxed(
            _execute_registered_bulk,
            (stage, invocation, identity),
            diagnostics=diagnostics,
        )
    if (
        result is None
        or diagnostics.get("cleanup", {}).get("verified_stopped") is not True
    ):
        raise ValueError(
            "Compact replay ended without a checked fit and worker cleanup"
        )
    payload, failure = _result_table(pipeline, result, request.dispatch_request_sha256)
    if payload is None or failure is not None:
        raise ValueError("Compact replay did not reproduce a fitted gene family")
    (stage / "outputs" / "genes.parquet").write_bytes(payload)
    (stage / "outputs" / "summary.json").write_bytes(
        canonical_json(
            {
                "format": "devbench.thesis-compact-replay-summary/1",
                "tool": tool["tool"],
                "dispatch_request_sha256": request.dispatch_request_sha256,
                "genes_sha256": sha256(payload).hexdigest(),
                "cleanup_confirmed": True,
            }
        )
    )
