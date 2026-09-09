"""Data-dependent public snapshot tasks, counted separately from simulation tasks."""
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from jsonschema import validate

from benchmarks.matrix import _schema
from benchmarks.public_data import expression_digest, sha256_file
from devharness_core.stages import parse_stages
from devharness_core.storage import ChunkedExpressionStore

PUBLIC_VARIANTS = {
    "marker_peaks": "Find each supplied regulator's observed peak collection stage from arithmetic replicate means. If stages tie, choose the earlier numeric age. This is an observed peak, not a causal activation cascade.",
    "temporal_changes": "Calculate each supplied regulator's observed change as log1p(mean RPKM at the last collection age) minus log1p(mean RPKM at the first age). These are descriptive changes, not fitted causal coefficients.",
    "chronology_audit": "Order the collection stages numerically and assess whether technical batch and independent animal identity were supplied. Preserve unknown provenance; do not infer independent animals from replicate-like column labels.",
}


def _snapshot_identifier(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("snapshot_id must be a simple alphanumeric, underscore, or hyphen component")
    return value


def _snapshot_directory(dataset: dict) -> Path:
    snapshot_id = _snapshot_identifier(dataset.get("snapshot_id"))
    # Explicit runtime overrides support custom cache layouts and tests. They
    # are never serialized by build_public_tasks into published definitions.
    override = dataset.get("snapshot_dir")
    if override is not None:
        snapshot = Path(override).expanduser()
        if not snapshot.is_absolute():
            raise ValueError("Runtime snapshot_dir must be absolute")
        return snapshot.resolve()
    configured = os.environ.get("DEVHARNESS_DATA_DIR")
    root = Path(configured).expanduser() if configured is not None else Path(__file__).resolve().parent / "cache" / "public"
    if not root.is_absolute():
        raise ValueError("DEVHARNESS_DATA_DIR must be an absolute directory")
    root = root.resolve()
    snapshot = (root / snapshot_id).resolve()
    if not snapshot.is_relative_to(root):
        raise ValueError("snapshot_id resolves outside DEVHARNESS_DATA_DIR")
    return snapshot


def _public_schema() -> dict:
    schema = _schema("trajectory")
    for field in ("pseudotime", "branch_assignments", "lineage_graph"):
        schema["properties"].pop(field)
        schema["required"].remove(field)
    properties = {
        "peak_stages": {"type": "object", "additionalProperties": {"type": "string"}},
        "gene_effects": {"type": "object", "additionalProperties": {"type": "number"}},
        "stage_order": {"type": "array", "items": {"type": "string"}},
        "chronology_scope": {"const": "collection_stage_reference"},
        "batch_known": {"type": "boolean"},
        "independent_specimens_known": {"type": "boolean"},
    }
    schema["properties"].update(properties)
    schema["required"].extend(properties)
    return schema


def build_public_tasks(snapshot_dir: Path, output_dir: Optional[Path] = None) -> list:
    """Build three tasks from a completed actual snapshot, never synthetic fallback.

    Definitions reference the existing snapshot by manifest digest. Regulator IDs
    must come from a supplied verified annotation map; no symbols are guessed.
    """
    snapshot = Path(snapshot_dir).resolve()
    snapshot_id = _snapshot_identifier(snapshot.name)
    manifest_path = snapshot / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("source_kind") != "public_processed":
        raise ValueError("Public tasks require a processed public snapshot")
    annotations = manifest.get("gene_annotations", [])
    if len(annotations) != 4 or len({entry["symbol"] for entry in annotations}) != 4:
        raise ValueError("Public tasks require exactly four verified regulator annotations")
    if not {entry["id"] for entry in annotations} <= set(manifest["selection"]["genes"]):
        raise ValueError("Annotated regulator IDs must occur in the actual expression subset")
    output = Path(output_dir) if output_dir is not None else Path(__file__).parent / "task_definitions" / "expanded"
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    for variant, question in PUBLIC_VARIANTS.items():
        task_id = "public_bulk_" + variant
        children = [{"type": "equals", "config": {"field": "condition_number_validated", "expected": True}},
                    {"type": "label_set_jaccard", "config": {"field": "master_regulators", "expected": [entry["symbol"] for entry in annotations], "threshold": 1., "expected_count": 4}},
                    {"type": "equals", "config": {"field": "chronology_scope", "expected": "collection_stage_reference"}}]
        if variant == "marker_peaks":
            children.append({"type": "equals", "config": {"field": "peak_stages", "expected_from_truth": "peak_stages"}})
        elif variant == "temporal_changes":
            children.append({"type": "effect_calibration", "config": {"field": "gene_effects", "expected_from_truth": "gene_effects", "absolute_tolerance": .01, "relative_tolerance": .01, "threshold": 1.}})
        else:
            children.extend({"type": "equals", "config": {"field": field, "expected_from_truth": field}} for field in ("stage_order", "batch_known", "independent_specimens_known"))
        spec = {"schema_version": 1, "id": task_id,
                "task": question + " Analyze counts.zarr, metadata.csv and task_inputs.json using bounded reads. Validate a numeric age design before fitting. Return all fields required by answer_schema.json; stage_kinetics is the observed Tbr1 stage mean rescaled to [0,1] across observed stages (all zero if constant). Report p-values and BH q-values if fitting a model. Write final_discovery.json. These mouse bulk measurements provide collection-stage observational references, not known pseudotime, causal regulation, or clinical utility.",
                "metadata": {"track": "public_reference", "source_kind": "public_processed", "source_accession": manifest.get("source_accession"), "hypothesis": question, "clinical_scope": "none", "public_expression_observed": True},
                "dataset": {"builder": "public_subset", "variant": variant, "snapshot_id": snapshot_id, "source_manifest_sha256": sha256_file(manifest_path)},
                "answer_schema": _public_schema(), "grader": {"type": "all_of", "config": {"children": children}}}
        path = output / f"{task_id}.json"
        path.write_text(json.dumps(spec, indent=2, allow_nan=False) + "\n")
        paths.append(path)
    return paths


def stage_public_task(work_dir: Path, dataset: dict) -> dict:
    """Copy a verified bounded snapshot and derive descriptive reference answers."""
    if dataset.get("builder") != "public_subset" or dataset.get("variant") not in PUBLIC_VARIANTS:
        raise ValueError("Unknown public dataset builder or task variant")
    snapshot = _snapshot_directory(dataset)
    manifest_path = snapshot / "source_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Public snapshot '{dataset['snapshot_id']}' is unavailable. Set DEVHARNESS_DATA_DIR to the absolute parent directory containing the verified snapshot, or provide an explicit runtime snapshot_dir.")
    if sha256_file(manifest_path) != dataset["source_manifest_sha256"]:
        raise ValueError("Public snapshot manifest changed after task definition")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("source_kind") != "public_processed":
        raise ValueError("Refusing to label synthetic data as a public snapshot")
    source_metadata = snapshot / "processed_metadata.csv"
    if sha256_file(source_metadata) != manifest["metadata_sha256"]:
        raise ValueError("Public snapshot metadata digest mismatch")
    metadata = pd.read_csv(source_metadata)
    if expression_digest(snapshot / "processed_counts.zarr") != manifest["expression_sha256"]:
        raise ValueError("Public snapshot expression digest mismatch")
    stages = sorted(set(metadata.embryonic_age), key=lambda stage: float(parse_stages([stage])[0]))
    annotations = manifest["gene_annotations"]
    work = Path(work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    if (work / "counts.zarr").exists():
        raise FileExistsError("Refusing to overwrite staged expression")
    with ChunkedExpressionStore(snapshot / "processed_counts.zarr") as store:
        if store.shape != tuple(manifest["shape"]) or store.sample_names != metadata.sample_id.tolist():
            raise ValueError("Public expression and metadata axes do not match manifest")
        # Only four bounded gene vectors are used to derive observed references.
        peaks, effects, means = {}, {}, {}
        for annotation in annotations:
            values = store.get_gene_vector(annotation["id"])
            stage_means = np.asarray([np.mean(values[metadata.embryonic_age == stage]) for stage in stages])
            if not np.isfinite(stage_means).all() or np.any(stage_means < 0):
                raise ValueError("Invalid public expression measurements")
            symbol = annotation["symbol"]
            means[symbol] = stage_means
            peaks[symbol] = stages[int(np.argmax(stage_means))]
            effects[symbol] = float(np.log1p(stage_means[-1]) - np.log1p(stage_means[0]))
    shutil.copytree(snapshot / "processed_counts.zarr", work / "counts.zarr")
    shutil.copy2(source_metadata, work / "metadata.csv")
    tbr1 = means.get("Tbr1", means[annotations[-1]["symbol"]])
    span = float(np.ptp(tbr1))
    kinetics = np.zeros(len(stages)) if span == 0 else (tbr1 - np.min(tbr1)) / span
    independent_known = "specimen_id" in metadata and "specimen_provenance" in metadata and not metadata.specimen_id.isna().any() and not metadata.specimen_id.astype(str).str.lower().isin(["unknown", ""]).any() and not metadata.specimen_provenance.astype(str).str.contains("unverified|unknown|does not identify", case=False).any()
    truth = {"peak_stages": peaks, "gene_effects": effects, "stage_order": stages,
             "batch_known": bool("batch" in metadata and not metadata.batch.isna().any() and not metadata.batch.astype(str).str.lower().isin(["unknown", ""]).any()),
             "independent_specimens_known": bool(independent_known), "chronology_scope": "collection_stage_reference"}
    truth["reference_answer"] = {**truth, "stage_kinetics": dict(zip(stages, map(float, kinetics))),
                                 "master_regulators": [entry["symbol"] for entry in annotations], "condition_number_validated": True,
                                 "biological_rationale": "Observed public bulk replicate means and collection-stage labels. The four supplied transcription factors are candidate regulators; measured peaks and descriptive expression changes do not prove causal regulation. Unknown batch and animal identity prevent a claim of independently replicated causal adjustment."}
    validate(truth["reference_answer"], _public_schema())
    inputs = {"regulator_annotations": annotations, "fit_genes": [entry["id"] for entry in annotations], "initial_formula": "1 + numeric_time",
              "chronology_scope": "collection_stage_reference", "expression_kind": "continuous", "source_accession": manifest.get("source_accession"),
              "descriptive_effect_definition": "log1p(last_stage_arithmetic_mean)-log1p(first_stage_arithmetic_mean)"}
    (work / "task_inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    provenance = {**manifest, "snapshot_id": dataset["snapshot_id"], "snapshot_manifest_sha256": dataset["source_manifest_sha256"], "variant": dataset["variant"]}
    (work / "dataset_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    digest = hashlib.sha256(json.dumps(truth, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"metadata": provenance, "ground_truth": truth, "truth_digest": digest}
