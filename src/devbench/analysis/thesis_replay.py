"""Replay retained Tier A stages using verified local input custody."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.core._files import hash_bounded_file, read_bounded_file, read_bounded_json
from devbench.core.contrast_design import TIER_A_BULK_OPERATION, TIER_A_BULK_OUTPUT
from devbench.engine.permissions import require_protected_storage
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow_state import ArtifactVersion
from devharness_controlled.native_contracts import KERNEL_VERSIONS
from devharness_core.provenance import copy_artifact, hash_artifact
from rna_kernels.canonical import canonical_json
from rna_kernels.mounted_bulk import MAX_MATRIX_BYTES, MAX_PARQUET_BYTES
from rna_kernels.numerical_equivalence import (
    ReplayRuntime,
    verify_numerical_equivalence,
)

from .thesis_compact_replay import (
    COMPACT_FILES,
    COMPACT_TRANSPORT,
    capture_compact_replay,
    replay_compact_fit,
)
from .thesis_exploratory_replay import (
    capture_exploratory_replay,
    prepare_exploratory_replay,
    run_exploratory_recipe,
)
from .thesis_joint_replay import (
    bind_mounted_releases,
    coordinator_identity,
    replay_mounted_release,
)
from .thesis_single_cell_replay import (
    capture_single_cell_replay,
    run_single_cell_recipe,
)

if TYPE_CHECKING:
    from devbench.engine.exploratory_oci import ExploratoryOCI
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    from .thesis_synthesis import ThesisSynthesisSpec

_MAX_REQUEST = 1_000_000
_INDEX = "replay_bundle/index.json"
_FORM = "devbench.thesis-mounted-replay/1"
_MEMBERS = {
    "expression.h5",
    "expression.h5ad",
    "expression.zarr",
    "lengths.h5",
    "metadata.csv",
    "family.json",
}
_SHA = re.compile(r"[a-f0-9]{64}")
_SCRIPT = """\
import sys
from pathlib import Path

from devbench.analysis.thesis_replay import main


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    main(["--package-root", str(root), *sys.argv[1:]])
"""


def _accepted_bulk_attempts(
    workflow: AdaptiveWorkflow,
    spec: ThesisSynthesisSpec,
    executor: TierAExecutor,
    joint_steps: set[str],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    def read(db):
        if (
            workflow.authority._revision(db, workflow.lineage_id)
            != spec.expected_revision
        ):
            raise OperationConflict(
                "Thesis replay requires its current ledger revision"
            )
        state = workflow._state(db)
        attempts = []
        for name, node in sorted(state.nodes.items()):
            if node.operation_id != TIER_A_BULK_OPERATION or not state.outcome(name):
                continue
            try:
                workflow._current_use(db, name, TIER_A_BULK_OUTPUT, "claim", None)
            except OperationBlocked:
                continue
            result = state.outcome(name)
            dispatch = state.dispatches[result["attempt_id"]]
            envelope = dispatch["envelope"]
            joint = "joint_bh" in envelope.get("thesis_scope", {})
            if dispatch["token"]["backend_id"] == executor.backend_id and (
                name in joint_steps or not joint
            ):
                attempts.append((result, envelope))
        return attempts

    return workflow._read_projection(read)


def _capture_recipe(executor: TierAExecutor, envelope, attempt, key: str):
    from devbench.engine.tier_a_executor import _RECORD_LIMIT, _read_record
    from devbench.platform.adaptive_workflow import request_digest

    receipt = executor.load_receipt(envelope, attempt_id=attempt["attempt_id"])
    if receipt is None:
        raise ValueError("Thesis replay source receipt is missing")
    if request_digest(receipt.model_dump(mode="json")) != attempt["receipt_sha256"]:
        raise ValueError("Replay source differs from the accepted ledger receipt")
    directory = executor._directory(receipt.job_id)
    saved = _read_record(directory / "io.json", _RECORD_LIMIT)["tool"]
    pipeline = executor._pipeline(envelope)
    if saved.get("tool") == TIER_A_BULK_OPERATION:
        raw, inputs = capture_compact_replay(executor, envelope, receipt, pipeline)
        transport = {"transport": COMPACT_TRANSPORT, "platform": platform.machine()}
    elif saved.get("tool") == "mounted_bulk" and saved.get("oci") is not None:
        raw, inputs, transport = _capture_mounted_transport(
            executor, envelope, pipeline, saved
        )
    else:
        return None
    recipe = {
        "step_id": envelope["step"]["step_id"],
        "branch_id": envelope["step"]["branch_id"],
        "request_path": f"replay_bundle/requests/{key}.json",
        "request_sha256": hashlib.sha256(raw).hexdigest(),
        "plan_path": f"replay_bundle/plans/{key}.json",
        "parameters_sha256": envelope["step"]["parameters_sha256"],
        **transport,
        "inputs": inputs,
        "genes_sha256": receipt.outputs[TIER_A_BULK_OUTPUT].sha256,
        "receipt_sha256": attempt["receipt_sha256"],
        "metadata_schema": {
            "condition": pipeline.condition_column,
            "biological_unit": pipeline.population_unit_column,
            "specimen": pipeline.specimen_column,
            "library": pipeline.library_column,
            "technical_factors": list(pipeline.design.technical_factors),
            "continuous_covariates": list(pipeline.continuous_covariates),
        },
        "rng_seeds": {
            "rank_sensitivity": saved.get("rank_sensitivity", {}).get("seed")
        },
    }
    plan = canonical_json(pipeline.model_dump(mode="json"))
    if hashlib.sha256(plan).hexdigest() != recipe["parameters_sha256"]:
        raise ValueError("Replay plan differs from its registered parameters")
    return recipe, raw, plan, saved.get("method")


def _capture_mounted_transport(executor, envelope, pipeline, saved):
    transport = saved.get("oci")
    image_id = transport["image_id"]
    if (
        image_id != executor.mounted_image_id
        or transport["cleanup_confirmed"] is not True
    ):
        raise ValueError("Mounted replay requires the original cleanup-verified image")
    inputs = {
        name: ArtifactVersion.model_validate(raw)
        for name, raw in envelope["inputs"].items()
    }
    with tempfile.TemporaryDirectory(dir=executor.root) as temporary:
        staging = Path(temporary)
        staged = executor._stage_mounted_inputs(inputs, staging, pipeline)
        raw = read_bounded_file(staging / "inputs" / "request.json", _MAX_REQUEST)
    identity = hashlib.sha256(raw).hexdigest()
    if staged["request_sha256"] != identity or saved["request_sha256"] != identity:
        raise ValueError("Mounted request differs from its verified execution receipt")
    request = json.loads(raw)
    files = {
        request["expression"]: request["expression_sha256"],
        "metadata.csv": request["metadata_sha256"],
        "family.json": request["family_sha256"],
    }
    if pipeline.mounted_model is not None and pipeline.mounted_model.lengths_sha256:
        files["lengths.h5"] = pipeline.mounted_model.lengths_sha256
    return raw, files, {"image_id": image_id, "platform": transport["platform"]}


def replay_files(
    workflow: AdaptiveWorkflow,
    spec: ThesisSynthesisSpec,
    executor: TierAExecutor | None,
    *,
    joint_publications: Mapping[str, Any] | None = None,
    exploratory_executor: ExploratoryOCI | None = None,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    from devbench.engine.tier_a_executor import TierAExecutor
    from devharness_controlled.native_contracts import (
        KERNEL_IMAGES,
        MOUNTED_KERNEL_IMAGES,
        single_cell_kernel_images,
    )

    from .thesis_qc_replay import capture_qc_derivations, qc_coordinator_identity

    publications = joint_publications or {}
    joint_steps = {
        source["step_id"]
        for item in publications.values()
        for source in item["sources"]
    }
    files = {"replay_bundle/replay.py": _SCRIPT.encode()}
    recipes, methods = [], []
    single_cell: list[dict[str, Any]] = []
    omitted_single_cell: dict[str, str] = {}
    exploratory, omitted_exploratory = (
        capture_exploratory_replay(
            workflow, exploratory_executor, spec.expected_revision
        )
        if exploratory_executor is not None
        else ([], {})
    )
    if executor is not None:
        if (
            type(executor) is not TierAExecutor
            or executor.ledger.path != workflow.ledger.path
        ):
            raise PermissionError("Thesis replay requires the bound Tier A executor")
        for index, (result, envelope) in enumerate(
            _accepted_bulk_attempts(workflow, spec, executor, joint_steps)
        ):
            captured = _capture_recipe(
                executor, envelope, result, f"fit-{index + 1:03d}"
            )
            if captured is None:
                continue
            recipe, request, plan, method = captured
            recipes.append(recipe)
            files[recipe["request_path"]] = request
            files[recipe["plan_path"]] = plan
            if method is not None:
                methods.append({"step_id": recipe["step_id"], "observed": method})
        captured_files, single_cell, omitted_single_cell = capture_single_cell_replay(
            workflow, executor, spec.expected_revision
        )
        files.update(captured_files)
    selected = bind_mounted_releases(
        publications,
        {recipe["step_id"]: recipe for recipe in recipes},
    )
    replayable = {
        source.step_id for release in selected.values() for source in release.sources
    }
    omitted = joint_steps - replayable
    for recipe in recipes:
        if recipe["step_id"] in omitted:
            del files[recipe["request_path"]], files[recipe["plan_path"]]
    recipes = [recipe for recipe in recipes if recipe["step_id"] not in omitted]
    methods = [method for method in methods if method["step_id"] not in omitted]
    qc_files, qc_derivations = (
        capture_qc_derivations(
            workflow, executor, spec.expected_revision, recipes, single_cell
        )
        if executor is not None
        else ({}, [])
    )
    files.update(qc_files)
    kernel_images = {
        recipe["image_id"]
        for recipe in (*recipes, *single_cell)
        if "image_id" in recipe
    }
    images = sorted(kernel_images | {recipe["image_id"] for recipe in exploratory})
    locked = set(KERNEL_IMAGES.values()) | set(MOUNTED_KERNEL_IMAGES.values())
    locked.update(single_cell_kernel_images())
    files[_INDEX] = canonical_json(
        {
            "format": _FORM,
            "target_comparison": (
                "exact_artifact_bytes_same_architecture"
                if single_cell or exploratory
                else "exact_genes_parquet_same_architecture"
            ),
            "steps": recipes,
            "source_protocol": "local_directory_keyed_by_sha256",
            "cross_architecture_tolerances": None,
            **({"single_cell_steps": single_cell} if single_cell else {}),
            **({"exploratory_steps": exploratory} if exploratory else {}),
            **(
                {
                    "qc_derivations": qc_derivations,
                    "qc_coordinator": qc_coordinator_identity(),
                }
                if qc_derivations
                else {}
            ),
            **(
                {
                    "cross_aim_bh": list(selected),
                    "cross_aim_coordinator": coordinator_identity(),
                }
                if selected
                else {}
            ),
        }
    )
    files["replay_bundle/parameters.json"] = canonical_json(
        spec.model_dump(mode="json", exclude={"literature"}),
    )
    return files, {
        "coordinator_python": platform.python_version(),
        "coordinator_platform": platform.machine(),
        "container_image_digests": images,
        "pinned_kernel_packages": KERNEL_VERSIONS
        if kernel_images and kernel_images <= locked
        else {},
        "observed_methods": methods,
        "numerical_replay_available": bool(recipes or single_cell or exploratory),
        **({"single_cell_replay_available": True} if single_cell else {}),
        **({"qc_derivation_replay_available": True} if qc_derivations else {}),
        **(
            {"single_cell_replay_omissions": omitted_single_cell}
            if omitted_single_cell
            else {}
        ),
        **({"exploratory_replay_available": True} if exploratory else {}),
        **(
            {"exploratory_replay_omissions": omitted_exploratory}
            if omitted_exploratory
            else {}
        ),
        **(
            {"compact_fit_replay": True}
            if any(recipe.get("transport") == COMPACT_TRANSPORT for recipe in recipes)
            else {}
        ),
        **_joint_replay_status(len(publications), len(selected)),
    }


def _joint_replay_status(published: int, selected: int) -> dict[str, str]:
    if not published:
        return {}
    status = {0: "unavailable", published: "available"}.get(selected, "partial")
    return {"cross_aim_bh_replay": status}


def _hydrate(
    source: Path,
    target: Path,
    inputs: dict[str, str],
    *,
    compact=False,
    recovered=None,
) -> None:
    if not inputs or not inputs.keys() <= (COMPACT_FILES if compact else _MEMBERS):
        raise ValueError("Mounted thesis replay has an invalid input inventory")
    for name, digest in sorted(inputs.items()):
        if not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError("Mounted thesis replay requires an exact source digest")
        item = (recovered or {}).get(digest, source / digest)
        entries = 100_000 if name.endswith(".zarr") else 1
        if (
            hash_artifact(item, max_bytes=MAX_MATRIX_BYTES, max_entries=entries)[
                "sha256"
            ]
            != digest
        ):
            raise ValueError(
                f"Mounted thesis replay source differs from its digest: {name}"
            )
        copied = copy_artifact(
            item, target / name, max_bytes=MAX_MATRIX_BYTES, max_entries=entries
        )
        if copied["sha256"] != digest:
            raise ValueError(
                f"Mounted thesis replay copy differs from its digest: {name}"
            )


def compare_replayed_fit(
    source: Path,
    target: Path,
    *,
    mode: str,
    source_runtime: ReplayRuntime,
    target_runtime: ReplayRuntime,
    alpha: float = 0.05,
    blas_debug_oracle: bool = False,
) -> dict[str, Any]:
    """Assess two mounted fit directories without promoting downstream artifacts."""
    from rna_kernels.mounted_bulk import MAX_SUMMARY_BYTES

    summaries = [
        read_bounded_json(path / "summary.json", MAX_SUMMARY_BYTES)
        for path in (source, target)
    ]
    for summary in summaries:
        if not isinstance(summary, dict) or not all(
            isinstance(summary.get(k), str) and _SHA.fullmatch(summary[k])
            for k in ("genes_sha256", "request_sha256", "family_sha256")
        ):
            raise ValueError(
                "Mounted comparison requires bound genes, request and family"
            )
    receipt = verify_numerical_equivalence(
        read_bounded_file(source / "genes.parquet", MAX_PARQUET_BYTES),
        read_bounded_file(target / "genes.parquet", MAX_PARQUET_BYTES),
        mode=mode,
        source_runtime=source_runtime,
        target_runtime=target_runtime,
        source_summary=summaries[0],
        target_summary=summaries[1],
        alpha=alpha,
        blas_debug_oracle=blas_debug_oracle,
    )
    receipt["fits"] = {
        side: {
            "genes_sha256": receipt[side + "_sha256"],
            **{k: summary[k] for k in ("request_sha256", "solver", "tested_count")},
        }
        for side, summary in zip(("source", "target"), summaries, strict=True)
    }
    return receipt


def _run_recipe(
    root: Path,
    output: Path,
    source: Path,
    recipe: dict[str, Any],
    *,
    recovered=None,
) -> str:
    output.mkdir(mode=0o700)
    stage = output / ".work"
    inputs = stage / "inputs"
    inputs.mkdir(mode=0o700, parents=True)
    (stage / "outputs").mkdir(mode=0o700)
    transport = recipe.get("transport")
    if transport not in (None, COMPACT_TRANSPORT):
        raise ValueError("Unsupported thesis replay transport")
    compact = transport == COMPACT_TRANSPORT
    _hydrate(source, inputs, recipe["inputs"], compact=compact, recovered=recovered)
    request = read_bounded_file(root / recipe["request_path"], _MAX_REQUEST)
    if hashlib.sha256(request).hexdigest() != recipe["request_sha256"]:
        raise ValueError("Thesis replay request digest mismatch")
    if compact:
        replay_compact_fit(root, stage, request, recipe)
    else:
        (inputs / "request.json").write_bytes(request)
        _replay_mounted_fit(stage, recipe)
    genes = stage / "outputs" / "genes.parquet"
    digest = hash_bounded_file(genes, MAX_PARQUET_BYTES)
    if digest != recipe["genes_sha256"]:
        raise ValueError("Thesis replay did not reproduce the exact genes.parquet")
    genes.rename(output / "genes.parquet")
    (stage / "outputs" / "summary.json").rename(output / "summary.json")
    shutil.rmtree(stage)
    return digest


def _replay_mounted_fit(stage: Path, recipe: dict[str, Any]) -> None:
    from devbench.engine.mounted_kernel_oci import run_mounted_container

    result = run_mounted_container(
        stage,
        request_sha=recipe["request_sha256"],
        image_id=recipe["image_id"],
        attempt_id=uuid.uuid4().hex,
        timeout=270,
        memory_mb=2048,
    )
    if (
        result["error_type"] is not None
        or result["cleanup_confirmed"] is not True
        or result["platform"] != recipe["platform"]
    ):
        raise ValueError(
            "Mounted thesis replay did not complete on its recorded platform"
        )


def replay_thesis_package(
    root: Path,
    expected_root: str,
    *,
    output: Path,
    input_root: Path,
    allow_data_execution: bool = False,
    exploratory_owner: Path | None = None,
) -> dict[str, Any]:
    """Re-execute retained Tier A fits with exactly the captured local bytes."""
    from .thesis_qc_replay import qc_coordinator_identity, run_qc_derivations
    from .thesis_synthesis import _MAX_METADATA, _canonical_path, verify_thesis_package

    if allow_data_execution is not True:
        raise PermissionError(
            "Numerical replay requires explicit data execution authority"
        )
    verified = verify_thesis_package(root, expected_root)
    index = read_bounded_json(root / _INDEX, 1_000_000)
    single_cell = index.get("single_cell_steps", [])
    exploratory = index.get("exploratory_steps", [])
    qc_derivations = index.get("qc_derivations", [])
    if index["format"] != _FORM or not (index["steps"] or single_cell or exploratory):
        raise ValueError("No mounted numerical replay was captured")
    recipes = {recipe["step_id"]: recipe for recipe in index["steps"]}
    identities = [
        recipe["step_id"] for recipe in (*index["steps"], *single_cell, *exploratory)
    ]
    if len(set(identities)) != len(identities):
        raise ValueError("Thesis replay contains duplicate steps")
    manifest = read_bounded_json(root / "provenance_manifest.json", _MAX_METADATA)
    releases = bind_mounted_releases(manifest.get("cross_aim_bh", {}), recipes)
    if list(releases) != index.get("cross_aim_bh", []):
        raise ValueError("Thesis replay does not bind the complete pooled family")
    if releases and index.get("cross_aim_coordinator") != coordinator_identity():
        raise ValueError("Joint BH replay requires its recorded coordinator runtime")
    if qc_derivations and index.get("qc_coordinator") != qc_coordinator_identity():
        raise ValueError("QC replay requires its recorded coordinator runtime")
    source = require_protected_storage(_canonical_path(input_root))
    if not source.is_dir():
        raise ValueError("Replay inputs must be a local digest-keyed directory")
    exploratory_plans = _prepare_exploratory_steps(
        exploratory_owner, manifest, exploratory
    )
    destination = require_protected_storage(_canonical_path(output))
    destination.mkdir(mode=0o700)
    recovered: dict[str, Path] = {}
    qc_steps, rebuilt = run_qc_derivations(
        root,
        destination,
        source,
        qc_derivations,
        single_cell,
        recovered,
    )
    digests = {}
    fitted_files = {}
    for position, recipe in enumerate(index["steps"], start=1):
        fitted = destination / f"fit-{position:03d}"
        digest = _run_recipe(root, fitted, source, recipe, recovered=recovered)
        digests[recipe["step_id"]] = digest
        fitted_files[recipe["step_id"]] = fitted / "genes.parquet"
    pooled = {
        identity: replay_mounted_release(
            release, root, destination, recipes, fitted_files
        )
        for identity, release in releases.items()
    }
    cells = _replay_single_cell_steps(
        root,
        destination,
        source,
        single_cell,
        digests,
        fitted_files,
        prior=qc_steps,
        recovered=recovered,
    )
    custom = _replay_exploratory_steps(destination, exploratory_plans)
    return {
        **verified,
        **({"exact_genes_parquet_match": True} if digests else {}),
        "steps": digests,
        **(
            {"exact_single_cell_artifacts_match": True, "single_cell_steps": cells}
            if cells
            else {}
        ),
        **(
            {"exact_qc_derived_inputs_match": True, "qc_derivations": rebuilt}
            if rebuilt
            else {}
        ),
        **(
            {"exact_exploratory_artifacts_match": True, "exploratory_steps": custom}
            if custom
            else {}
        ),
        **(
            {"exact_pooled_parquet_match": True, "cross_aim_bh": pooled}
            if pooled
            else {}
        ),
    }


def _prepare_exploratory_steps(owner, manifest, recipes):
    if not recipes:
        return None
    if owner is None:
        raise PermissionError("Tier B replay requires its local owner")
    return prepare_exploratory_replay(
        owner, manifest["execution_dag"]["lineage_id"], recipes
    )


def _replay_exploratory_steps(destination, prepared):
    if prepared is None:
        return {}
    flow, executor, plans = prepared
    return {
        plan.step.step_id: run_exploratory_recipe(
            destination / f"tier-b-{index:03d}", flow, executor, plan
        )
        for index, plan in enumerate(plans, start=1)
    }


def _replay_single_cell_steps(
    root,
    destination,
    source,
    recipes,
    bulk,
    fitted_files,
    *,
    prior,
    recovered,
):
    recovered.update({digest: fitted_files[name] for name, digest in bulk.items()})
    completed = dict(prior)
    for index, recipe in enumerate(recipes, start=1):
        if recipe["step_id"] not in completed:
            completed[recipe["step_id"]] = run_single_cell_recipe(
                root,
                destination / f"single-cell-{index:03d}",
                source,
                recovered,
                recipe,
            )
    return completed


def main(argv: list[str] | None = None) -> None:
    from .thesis_synthesis import verify_thesis_package

    parser = argparse.ArgumentParser(
        description="Verify a thesis package and replay its retained Tier A stages.",
    )
    parser.add_argument(
        "--package-root", type=Path, required=True, help=argparse.SUPPRESS
    )
    parser.add_argument("--expected-root", required=True)
    parser.add_argument(
        "--input-root", type=Path, help="Local digest-keyed input directory"
    )
    parser.add_argument(
        "--output", type=Path, help="Fresh canonical absolute output directory"
    )
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument(
        "--tier-b-owner", type=Path, help="Live local Tier B method and ledger root"
    )
    args = parser.parse_args(argv)
    specified = (
        args.input_root is not None,
        args.output is not None,
        args.allow_data_execution,
    )
    if all(specified):
        result = replay_thesis_package(
            args.package_root,
            args.expected_root,
            output=args.output,
            input_root=args.input_root,
            allow_data_execution=True,
            exploratory_owner=args.tier_b_owner,
        )
    elif any(specified) or args.tier_b_owner is not None:
        parser.error(
            "Numerical replay requires --input-root, --output "
            "and --allow-data-execution"
        )
    else:
        result = verify_thesis_package(args.package_root, args.expected_root)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
