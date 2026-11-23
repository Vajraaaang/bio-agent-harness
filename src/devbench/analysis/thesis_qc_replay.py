"""Replay the coordinator transformations fed by accepted single-cell QC."""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.core._files import read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.population_contract import LIMITS
from devbench.core.scrna_composition import (
    CompositionObservationSpec,
    composition_from_h5ad,
)
from devbench.core.scrna_qc_selection import AcceptedQCSelection, QCSelectionBinding
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.scrna_general_pseudobulk import (
    QC_LABEL_FILE_LIMITS,
    QC_LABEL_MAP,
    QC_LABEL_QUALIFICATION,
    GeneralPseudobulkSpec,
    retained_qc_label_files,
    stage_general_pseudobulk,
)
from devbench.platform.scrna_qc_handoff import (
    QC_COMPOSITION_REPLAY_FORMAT,
    QC_LABEL_BASIS_PREFIX,
    QC_REPLAY_BASIS_PREFIX,
    _parse_mask,
)
from devharness_core.provenance import copy_artifact, hash_artifact
from rna_kernels.canonical import canonical_json

from .integration_metrics import ExternalLabelEvidence

if TYPE_CHECKING:
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

_MAX_RECORD = 2_000_000
_MAX_METRICS = 64 * 1024**2
_SHA = re.compile(r"[a-f0-9]{64}")
_PATH = re.compile(r"replay_bundle/derivations/qc-[0-9]{3}\.json")
_BULK_FORMAT = "devbench.scrna-general-pseudobulk/1"
_BULK_FILES = ("expression.h5", "metadata.csv", "family.json")


@dataclass(frozen=True, slots=True)
class _Recipe:
    binding: QCSelectionBinding
    document: dict[str, Any]
    outputs: dict[str, str]
    labels: dict[str, str]
    digest: str


def qc_coordinator_identity() -> dict[str, str]:
    import h5py

    from .thesis_joint_replay import coordinator_identity

    return {
        "coordinator_sha256": hashlib.sha256(
            canonical_json(coordinator_identity())
        ).hexdigest(),
        "h5py": h5py.version.version,
        "hdf5": h5py.version.hdf5_version,
        "pandas": version("pandas"),
    }


def _decode(raw: bytes, digest: str) -> _Recipe:
    if len(raw) > _MAX_RECORD or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("QC replay recipe differs from its retained digest")
    document = decode_receipt_json(raw)
    if not isinstance(document, dict) or canonical_json(document) != raw:
        raise ValueError("QC replay requires an exact canonical object")
    binding = QCSelectionBinding.model_validate(document["qc_selection"])
    labels = {}
    if document["format"] == _BULK_FORMAT:
        if document["status"] != "ready":
            raise ValueError("QC replay requires a ready pseudobulk capsule")
        labels = retained_qc_label_files(document)
        if document["label_qualification"] is not None and not labels:
            raise ValueError("QC replay requires the retained qualified label files")
        outputs = {name: document["artifact_sha256"][name] for name in _BULK_FILES}
    elif document["format"] == QC_COMPOSITION_REPLAY_FORMAT:
        outputs = {"composition_input.json": document["composition_input_sha256"]}
    else:
        raise ValueError("Unsupported QC replay derivation")
    label_source = (document["label_qualification"]["source_sha256"],) if labels else ()
    digests = (
        document["qc_mask_sha256"], *outputs.values(), *labels.values(), *label_source,
    )
    if (
        any(
            not isinstance(value, str) or not _SHA.fullmatch(value)
            for value in digests
        )
        or document["specification"]["source_sha256"] != binding.source_sha256
    ):
        raise ValueError(
            "QC replay requires its original source and exact output digests"
        )
    return _Recipe(binding, document, outputs, labels, digest)


def _registered_label_digests(state, version_id):
    digests = set()
    for key in state.sources.get(version_id, ()):
        if key.startswith(QC_LABEL_BASIS_PREFIX):
            retained = state.basis[key]
            expected = key.removeprefix(QC_LABEL_BASIS_PREFIX)
            if retained["kind"] != "source" or retained["sha256"] != expected:
                raise OperationConflict("Qualified label basis digest changed")
            digests.add(expected)
    return digests


def _retained_recipe_refs(workflow, revision, recipes):
    def read(db):
        if workflow.authority._revision(db, workflow.lineage_id) != revision:
            raise OperationConflict("QC replay capture requires its current ledger")
        state = workflow._state(db)
        refs = {}
        for recipe in recipes:
            accepted = state.outcome(recipe["step_id"])
            envelope = state.dispatches[accepted["attempt_id"]]["envelope"]
            for item in envelope["inputs"].values():
                version_id = item["version_id"]
                for basis in state.sources.get(version_id, ()):
                    if not basis.startswith(QC_REPLAY_BASIS_PREFIX):
                        continue
                    identity = basis.removeprefix(QC_REPLAY_BASIS_PREFIX)
                    retained = state.basis[basis]
                    if retained["kind"] != "source" or retained["sha256"] != identity:
                        raise OperationConflict("QC replay basis digest changed")
                    parents = {
                        state.artifacts[parent][0]["sha256"]
                        for parent in state.derived_parents.get(version_id, ())
                    }
                    labels = _registered_label_digests(state, version_id)
                    refs.setdefault(identity, []).append(
                        (item["sha256"], parents, labels)
                    )
        return refs

    return workflow._read_projection(read)


def _require_qc_recipe(recipe: _Recipe, qc: dict[str, Any] | None) -> None:
    binding = recipe.binding
    if (
        qc is None
        or qc["operation"] != "scrna_batch_qc"
        or qc["receipt_sha256"] != binding.receipt_sha256
        or qc["inputs"] != {"expression.h5ad": binding.source_sha256}
        or qc["outputs"].get("qc_metrics.csv") != binding.metrics_sha256
    ):
        raise OperationBlocked("qc_replay_requires_exact_retained_qc")


def capture_qc_derivations(
    workflow: AdaptiveWorkflow,
    executor: TierAExecutor,
    revision: int,
    bulk: list[dict[str, Any]],
    single_cell: list[dict[str, Any]],
) -> tuple[dict[str, bytes], list[dict[str, Any]]]:
    """Capture digest-bound recipes for registered count inputs in this package."""
    refs = _retained_recipe_refs(workflow, revision, [*bulk, *single_cell])
    qc = {item["step_id"]: item for item in single_cell}
    files, captured = {}, []
    for position, (identity, consumers) in enumerate(sorted(refs.items()), start=1):
        executor._require_unprotected((identity,))
        with executor.training.open(identity) as handle:
            raw = handle.read(_MAX_RECORD + 1)
        recipe = _decode(raw, identity)
        label_sources = set(recipe.labels.values())
        if recipe.labels:
            label_sources.add(recipe.document["label_qualification"]["source_sha256"])
            executor._require_unprotected(tuple(sorted(label_sources)))
            for digest in recipe.labels.values():
                with executor.training.open(digest):
                    pass
        _require_qc_recipe(recipe, qc.get(recipe.binding.step_id))
        exact = {recipe.binding.source_sha256, recipe.binding.metrics_sha256}
        if any(
            digest not in recipe.outputs.values() or parents != exact
            or labels != label_sources
            for digest, parents, labels in consumers
        ):
            raise OperationConflict(
                "QC replay recipe differs from its registered inputs"
            )
        path = f"replay_bundle/derivations/qc-{position:03d}.json"
        files[path] = raw
        captured.append(
            {
                "path": path,
                "sha256": identity,
                "qc_step_id": recipe.binding.step_id,
                "outputs": recipe.outputs,
                **({"label_inputs": recipe.labels} if recipe.labels else {}),
            }
        )
    return files, captured


def _load_recipes(root, captured, single_cell):
    qc = {item["step_id"]: item for item in single_cell}
    recipes = []
    if len(captured) > 512:
        raise ValueError("QC replay recipe count exceeds its bound")
    for item in captured:
        path = item["path"]
        if not _PATH.fullmatch(path):
            raise ValueError("QC replay recipe path is outside its inventory")
        recipe = _decode(read_bounded_file(root / path, _MAX_RECORD), item["sha256"])
        if (
            recipe.outputs != item["outputs"]
            or recipe.binding.step_id != item["qc_step_id"]
            or recipe.labels != item.get("label_inputs", {})
        ):
            raise ValueError("QC replay descriptor differs from its recipe")
        _require_qc_recipe(recipe, qc.get(recipe.binding.step_id))
        recipes.append(recipe)
    return recipes


def _replayed_mask(recipe: _Recipe, recovered: dict[str, Path]) -> AcceptedQCSelection:
    binding = recipe.binding
    path = recovered.get(binding.metrics_sha256)
    if path is None:
        raise ValueError("QC derivation requires freshly replayed metrics")
    raw = read_bounded_file(path, _MAX_METRICS)
    if hashlib.sha256(raw).hexdigest() != binding.metrics_sha256:
        raise ValueError("Replayed QC metrics differ from their digest")
    cells, passed = _parse_mask(raw)
    selection = AcceptedQCSelection(binding, cells, passed)
    if selection.mask_sha256() != recipe.document["qc_mask_sha256"]:
        raise ValueError("Replayed QC mask differs from the staged mask")
    return selection


def _check_label_inputs(source: Path, recipes: list[_Recipe]) -> None:
    checked = set()
    for recipe in recipes:
        for name, digest in recipe.labels.items():
            if (name, digest) in checked:
                continue
            try:
                observed = hash_artifact(
                    source / digest, max_bytes=QC_LABEL_FILE_LIMITS[name],
                    max_entries=1,
                )
            except FileNotFoundError as error:
                raise ValueError(
                    f"Missing qualified label replay input: {name}"
                ) from error
            if observed["kind"] != "file" or observed["sha256"] != digest:
                raise ValueError(f"Qualified label replay input differs: {name}")
            checked.add((name, digest))


def _rebuild_inputs(recipe, path, destination, selection, source):
    raw_spec = canonical_json(recipe.document["specification"])
    if recipe.document["format"] == _BULK_FORMAT:
        spec = GeneralPseudobulkSpec.model_validate_json(raw_spec)
        labels = None
        if recipe.labels:
            labels = ExternalLabelEvidence(
                source / recipe.labels[QC_LABEL_MAP],
                source / recipe.labels[QC_LABEL_QUALIFICATION],
                recipe.labels[QC_LABEL_QUALIFICATION],
            )
        capsule = stage_general_pseudobulk(
            path, spec, destination, qc_selection=selection, label_evidence=labels,
        )
        for name in recipe.labels:
            (capsule.directory / name).unlink()
        if capsule.pipeline is None or capsule.provenance_sha256 != recipe.digest:
            raise ValueError("Replayed pseudobulk capsule differs from its provenance")
    else:
        spec = CompositionObservationSpec.model_validate_json(raw_spec)
        data = composition_from_h5ad(path, spec, qc_selection=selection)
        destination.mkdir(mode=0o700)
        (destination / "composition_input.json").write_bytes(
            canonical_json(data.model_dump(mode="json"))
        )


def run_qc_derivations(
    root: Path,
    destination: Path,
    source: Path,
    captured: list[dict[str, Any]],
    single_cell: list[dict[str, Any]],
    recovered: dict[str, Path],
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    """Run QC once, then rebuild and verify its coordinator-derived inputs."""
    from .thesis_single_cell_replay import run_single_cell_recipe

    if not captured:
        return {}, {}
    recipes = _load_recipes(root, captured, single_cell)
    _check_label_inputs(source, recipes)
    needed = {recipe.binding.step_id for recipe in recipes}
    prior = {
        item["step_id"]: run_single_cell_recipe(
            root, destination / f"single-cell-{position:03d}", source, recovered, item
        )
        for position, item in enumerate(single_cell, start=1)
        if item["step_id"] in needed
    }
    verified = {}
    with tempfile.TemporaryDirectory(dir=destination, prefix=".qc-sources-") as tmp:
        originals = {}
        for position, recipe in enumerate(recipes, start=1):
            digest = recipe.binding.source_sha256
            if digest not in originals:
                path = Path(tmp) / f"{digest}.h5ad"
                copied = copy_artifact(
                    source / digest,
                    path,
                    max_bytes=LIMITS["max_file_bytes"],
                    max_entries=1,
                )
                if copied["sha256"] != digest:
                    raise ValueError("QC derivation original source differs")
                originals[digest] = path
            selection = _replayed_mask(recipe, recovered)
            output = destination / f"qc-derived-{position:03d}"
            _rebuild_inputs(recipe, originals[digest], output, selection, source)
            for name, expected in recipe.outputs.items():
                file = output / name
                actual = hash_artifact(
                    file, max_bytes=LIMITS["max_file_bytes"], max_entries=1
                )["sha256"]
                if actual != expected:
                    raise ValueError(f"QC replay did not reproduce {name}")
                recovered.setdefault(expected, file)
            verified[recipe.digest] = recipe.outputs
    return prior, verified
