"""Replay a sealed pooled BH publication from exact mounted member results."""

from __future__ import annotations

import hashlib
import platform
import tempfile
from collections.abc import Mapping
from functools import partial
from importlib.metadata import version
from pathlib import Path
from typing import Any, BinaryIO

from devbench.core._files import read_bounded_file
from devbench.core.contrast_design import PipelineSpec
from devbench.core.joint_bh import JOINT_BH_INPUT, adjust_joint_bh
from devbench.engine.joint_bh_parquet import encode_pooled_table, read_probabilities
from devbench.platform.adaptive_workflow_state import ArtifactVersion
from devbench.platform.thesis_joint_bh_release import JointBHRelease
from rna_kernels.canonical import canonical_json
from rna_kernels.mounted_bulk import MAX_PARQUET_BYTES


def coordinator_identity() -> dict[str, Any]:
    return {
        "architecture": platform.machine(),
        "packages": {name: version(name) for name in ("numpy", "pyarrow", "scipy")},
    }


def bind_mounted_releases(
    publications: Mapping[str, Any], recipes: Mapping[str, dict[str, Any]]
) -> dict[str, JointBHRelease]:
    current = {}
    for identity, raw in sorted(publications.items()):
        release = JointBHRelease.model_validate_json(canonical_json(raw))
        if (
            release.receipt_sha256() != identity
            or release.plan.sha256() != release.plan_sha256
            or tuple(item.aim_id for item in release.sources) != release.plan.aim_ids
            or release.outputs.keys() != set(release.plan.aim_ids)
        ):
            raise ValueError("Joint BH replay publication differs from its receipt")
        if not all(source.step_id in recipes for source in release.sources):
            continue
        for source, member in zip(release.sources, release.plan.members, strict=True):
            recipe = recipes[source.step_id]
            if (
                source.step_id != member.step_id
                or recipe["parameters_sha256"] != member.parameters_sha256
                or recipe["receipt_sha256"] != source.receipt_sha256
                or recipe["genes_sha256"] != source.artifact_sha256
            ):
                raise ValueError(
                    "Joint BH replay member differs from its source receipt"
                )
        current[identity] = release
    return current


def _read_plan(root: Path, recipe: dict[str, Any]) -> PipelineSpec:
    raw = read_bounded_file(root / recipe["plan_path"], 1_000_000)
    if hashlib.sha256(raw).hexdigest() != recipe["parameters_sha256"]:
        raise ValueError("Joint BH replay member plan differs from its captured digest")
    return PipelineSpec.model_validate_json(raw)


def _open_fit(path: Path, _artifact: ArtifactVersion) -> BinaryIO:
    return path.open("rb")


def replay_mounted_release(
    release: JointBHRelease,
    root: Path,
    destination: Path,
    recipes: Mapping[str, dict[str, Any]],
    fitted_files: Mapping[str, Path],
) -> dict[str, str]:
    specifications = {}
    measurements = {}
    artifacts = {}
    paths = {}
    for source in release.sources:
        aim_id = source.aim_id
        specification = _read_plan(root, recipes[source.step_id])
        specifications[aim_id] = specification
        path = fitted_files[source.step_id]
        paths[aim_id] = path
        artifact = release.outputs[aim_id].model_copy(
            update={
                "representation": JOINT_BH_INPUT,
                "sha256": source.artifact_sha256,
                "permitted_uses": (),
            }
        )
        artifacts[aim_id] = artifact
        measurements[aim_id] = read_probabilities(
            artifact,
            partial(_open_fit, path),
            family_sha256=specification.family_sha256,
        )

    pooled = adjust_joint_bh(
        release.plan, specifications=specifications, measurements=measurements
    )
    output = destination / ("joint-bh-" + release.receipt_sha256())
    with tempfile.TemporaryDirectory(dir=destination) as temporary:
        staging = Path(temporary) / "release"
        staging.mkdir(mode=0o700)
        digests = {}
        for ordinal, aim_id in enumerate(release.plan.aim_ids, start=1):
            path = paths[aim_id]
            raw = encode_pooled_table(
                artifacts[aim_id],
                partial(_open_fit, path),
                pooled[aim_id],
                publication_sha256=release.publication_sha256,
            )
            identity = hashlib.sha256(raw).hexdigest()
            if (
                len(raw) > MAX_PARQUET_BYTES
                or identity != release.outputs[aim_id].sha256
            ):
                raise ValueError(
                    "Joint BH replay did not reproduce the exact pooled Parquet"
                )
            (staging / f"aim-{ordinal:02d}.parquet").write_bytes(raw)
            digests[aim_id] = identity
        staging.rename(output)
    return digests
