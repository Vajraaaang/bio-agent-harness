"""Exact, representation-preserving multi-source RNA planning bindings.

The contract admits original artifacts and their bounded headers. It does not
join matrices, equate feature namespaces, repair identifiers, or establish
independent biological units. A trial must still bind every named artifact.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re

from devharness_core.provenance import canonical_json

from .checkpoints import digest


BINDING_FORMAT = "devbench.rna-input-set-binding/1"
INSPECTION_FORMAT = "devbench.rna-input-set/1"
MAX_SOURCES = 8
MAX_SOURCE_BYTES = 100_000_000
MAX_METADATA_BYTES = 1_000_000
ROLES = {"expression", "metadata", "annotations", "reference", "other"}
DECLARATION_FIELDS = {"role", "assay", "measurement", "count_provenance", "capture_unit_key",
                      "capture_units_confirmed", "annotation_provenance", "counts_layer"}
UNKNOWN_FIELDS = {
    "feature_alignment": "Feature identifiers, namespaces and cross-file overlap have not been compared.",
    "observation_identity": "Within-file and cross-file observation identifiers and collisions have not been assessed.",
    "metadata_alignment": "A metadata table is not joined by row position; explicit identifier and column mappings are required.",
    "measurement_compatibility": "Declared representation and provenance need review; expression values were not inspected or converted.",
    "biological_independence": "Different files and source labels do not establish independent donors, subjects or specimens.",
    "joint_execution": "No combined matrix was created. Execution requires a reviewed method accepting the complete named input set.",
}


def source_set_identity(profiles):
    """Order and exact original bytes are part of the source-set identity."""
    if type(profiles) is not list or not 2 <= len(profiles) <= MAX_SOURCES:
        raise ValueError("Select between two and eight distinct dataset sources")
    values = []
    for profile in profiles:
        if (type(profile) is not dict or type(profile.get("source")) is not str
                or not Path(profile["source"]).is_absolute()
                or not re.fullmatch(r"[0-9a-f]{64}", str(profile.get("sha256", "")))
                or type(profile.get("size_bytes")) is not int or profile["size_bytes"] < 0):
            raise ValueError("Every dataset needs its exact observed path, hash and byte count")
        values.append({key: profile[key] for key in ("sha256", "size_bytes")})
    if len({profile["source"] for profile in profiles}) != len(profiles):
        raise ValueError("A dataset source cannot appear twice in one input set")
    return digest({"format": BINDING_FORMAT, "sources": values})


def validate_source_declarations(value, observed=None):
    if type(value) is not dict or set(value) - DECLARATION_FIELDS:
        raise ValueError("Use explicit bounded per-source declarations")
    result = deepcopy(value)
    for key, item in result.items():
        if key == "capture_units_confirmed":
            if type(item) is not bool:
                raise ValueError("Capture confirmation must be an explicit boolean")
        elif type(item) is not str or len(item) > (4000 if key.endswith("provenance") else 200) or "\x00" in item:
            raise ValueError("Per-source declarations must be bounded text")
    if result.get("role") and result["role"] not in ROLES:
        raise ValueError("Declare expression, metadata, annotations, reference or other source role")
    if result.get("assay") and result["assay"] not in {"scrna", "snrna", "bulk"}:
        raise ValueError("Declare a supported RNA assay")
    if result.get("measurement") and result["measurement"] not in {"umi_counts", "non_umi_counts", "normalized_expression"}:
        raise ValueError("Declare the actual measurement representation")
    observed = observed or {}
    if result.get("capture_unit_key") and result["capture_unit_key"] not in observed.get("obs_columns", []):
        raise ValueError("Capture-unit choice must be present in this source's observed columns")
    if result.get("counts_layer") and result["counts_layer"] not in ["X", *observed.get("layers", [])]:
        raise ValueError("Measurement layer must be present in this source's observed header")
    return result


def aggregate_profile(profiles):
    identity = source_set_identity(profiles)
    return {"format": "devbench.dataset-profile/1", "source": profiles[0]["source"], "path": profiles[0]["source"],
        "filename": str(len(profiles)) + " datasets",
        "sha256": identity, "size_bytes": sum(profile["size_bytes"] for profile in profiles),
        "observed": {"file_type": "input_set", "source_count": len(profiles),
            "sources": [{key: deepcopy(profile[key]) for key in ("source", "filename", "sha256", "size_bytes", "observed")} for profile in profiles],
            "expression_values_read": False, "combined_matrix_created": False},
        "inferred": {}, "declarations": {},
        "unknowns": [{"field": key, "reason": reason} for key, reason in UNKNOWN_FIELDS.items()]}


def _registered_header_worker(root, name, root_identity, expected, expected_sha256, suffix):
    if suffix == ".h5ad":
        from .rna_scrna_input_contract import _header_worker
        return _header_worker(root, name, root_identity, expected, expected_sha256)
    from .resources import configure_threads
    configure_threads()
    from .workbench_inputs import _open_file, _signature, _hash_fd, _table_header, _json_header, _unchanged
    with _open_file(root, (name,), root_identity) as descriptor:
        if _signature(os.fstat(descriptor)) != tuple(expected) or _hash_fd(descriptor, MAX_SOURCE_BYTES) != expected_sha256:
            raise ValueError("Registered input-set artifact changed before inspection")
        observed = {"file_type": suffix[1:], "cells": None, "genes": None,
            "obs_columns": [], "var_columns": [], "layers": [], "obsm_keys": [], "expression_values_read": False}
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            if suffix in {".csv", ".tsv"}:
                observed.update(_table_header(stream, suffix, 256 * 1024, 128))
            elif suffix == ".json":
                observed.update(_json_header(stream, expected[3], 256 * 1024, 128))
            else:
                raise ValueError("Unsupported registered input-set format")
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Registered input-set artifact changed during inspection")
    _unchanged(root, (name,), root_identity, expected)
    return observed


def _inspect_artifact(methods, member):
    identity = member["artifact_id"]
    artifact = methods.get("artifact", identity)
    path = methods.artifact_path(identity)
    suffix = artifact["suffix"].lower()
    if (artifact["size_bytes"] > MAX_SOURCE_BYTES
            or (artifact["artifact_type"], suffix) not in {("anndata", ".h5ad"), ("json", ".json"), ("table", ".csv"), ("table", ".tsv")}):
        raise ValueError("Input-set members require bounded supported original data artifacts")
    # Artifact storage names are content IDs without extensions; use only the
    # registered suffix, never a client-chosen parsing hint or temporary rename.
    from .workbench_inputs import _directory, _signature
    from devharness_core.watchdog import run_sandboxed, HarnessResourceInterrupt, RemoteWorkerError
    descriptor = _directory(path.parent)
    try:
        info = os.fstat(descriptor)
        root_identity = (info.st_dev, info.st_ino)
    finally:
        os.close(descriptor)
    diagnostics = {}
    try:
        observed = run_sandboxed(_registered_header_worker,
            (str(path.parent), path.name, root_identity, _signature(path.stat()), artifact["sha256"], suffix),
            max_mem_mb=512, timeout_sec=20, diagnostics=diagnostics)
    except HarnessResourceInterrupt as error:
        raise ValueError("Input-set header inspection exceeded supervision bounds") from error
    except RemoteWorkerError as error:
        if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
            raise ValueError("Input-set header worker failed with unverified cleanup") from error
        raise ValueError("Input-set header rejected: " + str(error).splitlines()[0][:400]) from error
    if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
        raise ValueError("Input-set header worker cleanup is unverified")
    declarations = validate_source_declarations(member["declarations"], observed)
    if methods.artifact_path(identity) != path or methods.get("artifact", identity) != artifact:
        raise ValueError("Input-set member changed during header verification")
    return {"name": member["name"], "label": member["label"],
        "artifact_ref": {key: artifact[key] for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")},
        "observed": observed, "declarations": declarations,
        "unknowns": [{"field": "scientific_validity", "reason": "Byte/header checks do not establish assay validity or independent biological units."}]}


def inspect_registered_input_set(methods, binding, *, actor=None):
    if (type(binding) is not dict or set(binding) != {"format", "members"}
            or binding["format"] != BINDING_FORMAT or type(binding["members"]) is not list
            or not 2 <= len(binding["members"]) <= MAX_SOURCES):
        raise ValueError("Use an exact bounded registered RNA input-set binding")
    names, identities, members = set(), set(), []
    for index, member in enumerate(binding["members"], 1):
        if (type(member) is not dict or set(member) != {"name", "label", "artifact_id", "declarations"}
                or member["name"] != "source_" + str(index)
                or type(member["label"]) is not str or not 1 <= len(member["label"]) <= 200
                or "\x00" in member["label"] or not re.fullmatch(r"[0-9a-f]{64}", str(member["artifact_id"]))):
            raise ValueError("Input-set members need ordered source names, labels and exact artifact identities")
        if member["name"] in names or member["artifact_id"] in identities:
            raise ValueError("Repeated artifact identity cannot be counted as another independent dataset")
        names.add(member["name"]); identities.add(member["artifact_id"])
        members.append(_inspect_artifact(methods, member))
    result = {"format": INSPECTION_FORMAT, "binding": deepcopy(binding), "source_count": len(members),
        "input_set_sha256": digest({"format": BINDING_FORMAT, "sources": [
            {key: member["artifact_ref"][key] for key in ("sha256", "size_bytes")} for member in members]}),
        "input_artifact_refs": {member["name"]: member["artifact_ref"] for member in members},
        "input_integrity_valid": True, "sources": members,
        "observed": {"source_count": len(members), "combined_matrix_created": False,
            "expression_values_read": False, "observation_ids_read": False, "feature_ids_read": False},
        "unknowns": [{"field": key, "reason": reason} for key, reason in UNKNOWN_FIELDS.items()],
        "inspection": {"method": "supervised_registered_source_headers", "analysis_executed": False,
            "model_sessions_attempted": 0}}
    if len(canonical_json(result)) > MAX_METADATA_BYTES:
        raise ValueError("Input-set inspection exceeds its bounded metadata budget")
    return result


def verify_input_set_inspection(methods, inspection):
    if type(inspection) is not dict or inspection.get("format") != INSPECTION_FORMAT:
        raise ValueError("RNA input-set audit requires its typed registered inspection")
    actual = inspect_registered_input_set(methods, inspection.get("binding"))
    if actual != inspection:
        raise ValueError("Input-set headers, declarations or exact artifact identities changed")
    return actual


def header_only_facts():
    from .rna_scrna_input_contract import header_only_facts as single_header_facts
    return single_header_facts()
