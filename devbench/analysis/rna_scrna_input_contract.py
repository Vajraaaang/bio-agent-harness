"""Verified AnnData header bindings for the governed scientific planning route.

The adapter verifies registered bytes and storage/header structure only. It never
decodes expression values, annotation values or observation/feature identities.
Declarations, inferred roles and unassessed scientific properties remain distinct.
"""
from __future__ import annotations

from copy import deepcopy
import os

from devharness_core.provenance import canonical_json


BINDING_FORMAT = "devbench.scrna-input-binding/1"
INSPECTION_FORMAT = "devbench.scrna-input/1"
MAX_BYTES = 100_000_000
MAX_METADATA_BYTES = 64_000
_REQUIRED = {"assay", "measurement", "count_provenance", "counts_layer", "capture_unit_key",
             "capture_units_confirmed", "annotation_provenance"}
UNASSESSED_FACTS = ("observation_ids_unique", "metrics_finite", "assay_semantics_known",
    "biological_units_independent", "design_identifiable", "exclusion_evidence_supported", "exclusion_authorized")


def validate_scrna_declarations(value):
    if type(value) is not dict or not _REQUIRED <= set(value) or set(value) - _REQUIRED - {"biological_independence"}:
        raise ValueError("Use explicit AnnData assay, measurement, count-layer and capture provenance declarations")
    result = deepcopy(value)
    if type(result["assay"]) is not str or type(result["measurement"]) is not str or result["assay"] not in {"scrna", "snrna"} or result["measurement"] not in {
            "umi_counts", "non_umi_counts", "normalized_expression"}:
        raise ValueError("Declare the actual supported RNA assay and measurement representation")
    for field in ("count_provenance", "annotation_provenance", "counts_layer", "capture_unit_key"):
        maximum = 4000 if field.endswith("provenance") else 200
        if type(result[field]) is not str or not result[field].strip() or len(result[field]) > maximum:
            raise ValueError("An explicit bounded declaration is required for " + field)
        if field.endswith("provenance"):
            result[field] = result[field].strip()
    if result["capture_units_confirmed"] is not True:
        raise ValueError("Capture-unit meaning must be confirmed from experimental records")
    independence = result.setdefault("biological_independence", None)
    if independence is not None:
        if (type(independence) is not dict or set(independence) != {"independent", "unit_column", "evidence"}
                or type(independence["independent"]) is not bool
                or type(independence["unit_column"]) is not str or not independence["unit_column"].strip()
                or len(independence["unit_column"]) > 200
                or type(independence["evidence"]) is not str or not independence["evidence"].strip()
                or len(independence["evidence"]) > 4000):
            raise ValueError("Biological independence needs an explicit source record and unit column, or null")
    return result


def _closed_storage(handle, maximum=16384):
    """Reject unbound storage without reading any dataset values."""
    import h5py
    pending, visited, examined = [handle], set(), 0
    while pending:
        group = pending.pop()
        identity = h5py.h5o.get_info(group.id).addr
        if identity in visited:
            continue
        visited.add(identity)
        for name in group:
            examined += 1
            if examined > maximum:
                raise ValueError("AnnData storage metadata exceeds the bounded traversal budget")
            if not isinstance(group.get(name, getlink=True), h5py.HardLink):
                raise ValueError("AnnData binding cannot contain external or symbolic HDF5 links")
            item = group[name]
            if isinstance(item, h5py.Group):
                pending.append(item)
            elif isinstance(item, h5py.Dataset) and (item.is_virtual or item.external):
                raise ValueError("AnnData binding cannot depend on unregistered external or virtual dataset storage")


def _header_worker(root, name, root_identity, expected, expected_sha256):
    from .resources import configure_threads
    configure_threads()
    from .workbench_inputs import _open_file, _signature, _hash_fd, _h5ad_header, _unchanged
    import h5py
    with _open_file(root, (name,), root_identity) as descriptor:
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Registered AnnData changed before header inspection")
        if _hash_fd(descriptor, MAX_BYTES) != expected_sha256:
            raise ValueError("Registered AnnData hash differs from its artifact binding")
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as source:
            with h5py.File(source, "r") as handle:
                _closed_storage(handle)
                header = _h5ad_header(handle, 128)
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Registered AnnData changed during header inspection")
    _unchanged(root, (name,), root_identity, expected)
    if header["cells"] > 2_000_000 or header["genes"] > 100_000:
        raise ValueError("AnnData shape exceeds the header-admission bounds")
    header.update(file_type="h5ad", expression_values_read=False, observation_ids_read=False,
        feature_ids_read=False, annotation_values_read=False)
    return header


def inspect_registered_scrna_input(methods, binding, *, actor=None):
    """Derive a bounded profile from exact immutable artifact bytes, not client metadata."""
    if (type(binding) is not dict or set(binding) != {"format", "anndata_artifact_id", "declarations"}
            or binding["format"] != BINDING_FORMAT):
        raise ValueError("Use an exact versioned registered AnnData RNA binding")
    declarations = validate_scrna_declarations(binding["declarations"])
    identity = binding["anndata_artifact_id"]
    if type(identity) is not str or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("AnnData binding requires an exact registered artifact identity")
    path = methods.artifact_path(identity)
    artifact = methods.get("artifact", identity)
    if artifact["artifact_type"] != "anndata" or artifact["suffix"].lower() != ".h5ad" or artifact["size_bytes"] > MAX_BYTES:
        raise ValueError("AnnData RNA binding requires a bounded registered .h5ad artifact")
    from .workbench_inputs import _directory, _signature
    descriptor = _directory(path.parent)
    try:
        info = os.fstat(descriptor)
        root_identity = (info.st_dev, info.st_ino)
    finally:
        os.close(descriptor)
    expected = _signature(path.stat())
    from devharness_core.watchdog import run_sandboxed, HarnessResourceInterrupt, RemoteWorkerError
    diagnostics = {}
    try:
        header = run_sandboxed(_header_worker, (str(path.parent), path.name, root_identity, expected, artifact["sha256"]),
            max_mem_mb=512, timeout_sec=20, diagnostics=diagnostics)
    except HarnessResourceInterrupt as error:
        raise ValueError("AnnData header inspection exceeded its supervision bounds: " + str(error)[:400]) from error
    except RemoteWorkerError as error:
        if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
            raise ValueError("AnnData header worker failed and cleanup is unverified") from error
        raise ValueError("AnnData header rejected: " + str(error).splitlines()[0][:400]) from error
    if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
        raise ValueError("AnnData header worker cleanup is unverified")
    # Reverify the manifest and content after the independently opened worker.
    if methods.artifact_path(identity) != path or methods.get("artifact", identity) != artifact:
        raise ValueError("AnnData artifact identity changed during inspection")
    if declarations["counts_layer"] != "X" and declarations["counts_layer"] not in header["layers"]:
        raise ValueError("Declared measurement layer is absent from the verified AnnData header")
    if declarations["capture_unit_key"] not in header["obs_columns"]:
        raise ValueError("Declared capture-unit column is absent from the verified AnnData header")
    independence = declarations["biological_independence"]
    if independence is not None and independence["unit_column"] not in header["obs_columns"]:
        raise ValueError("Declared biological-unit column is absent from the verified AnnData header")
    reference = {key: artifact[key] for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")}
    result = {"format": INSPECTION_FORMAT, "binding": {"format": BINDING_FORMAT,
        "anndata_artifact_id": identity, "declarations": declarations}, "input_artifact_refs": {"anndata": reference},
        "input_integrity_valid": True, "observed": header, "declarations": declarations, "inferred": {},
        "biological_units": {"observation_unit": "cell", "independent_unit_count": None,
            "independence_verified": False, "capture_column_values_read": False},
        "unknowns": [{"field": field, "reason": reason} for field, reason in [
            ("measurement_values", "Expression values were not decoded; count validity and normalization are not verified."),
            ("observation_identity", "Cell and feature identifier values, uniqueness and cross-file alignment were not inspected."),
            ("biological_independence", "Capture labels and supplied records do not establish independent biological replication in this header audit."),
            ("design_and_contrasts", "Design rank, groups, repeated specimens and batch/condition confounding remain unassessed."),
            ("annotation_validity", "Annotation values, accuracy and scientific validity were not evaluated.")]],
        "inspection": {"method": "supervised_registered_anndata_headers", "worker_cleanup_verified": True,
            "analysis_executed": False, "model_sessions_attempted": 0}}
    if len(canonical_json(result)) > MAX_METADATA_BYTES:
        raise ValueError("AnnData scientific header profile exceeds its bounded metadata budget")
    return result


def verify_scrna_inspection(methods, inspection):
    if type(inspection) is not dict or inspection.get("format") != INSPECTION_FORMAT:
        raise ValueError("RNA AnnData audit requires its typed registered inspection")
    actual = inspect_registered_scrna_input(methods, inspection.get("binding"))
    if actual != inspection:
        raise ValueError("AnnData header, artifact, declarations or unknowns changed from the retained binding")
    return actual


def header_only_facts():
    return {"input_integrity_valid": True, **{field: None for field in UNASSESSED_FACTS}}


def match_scrna_scientific_contract(contract, inspection):
    """Keep a separately typed companion consistent with the header's limits."""
    assay = contract["assay"]
    expected = "normalized_expression" if inspection["declarations"]["measurement"] == "normalized_expression" else "direct_counts"
    if assay["representation"] != expected or assay["orientation"] != "cell_by_feature":
        raise ValueError("Scientific assay representation or orientation contradicts the AnnData declaration/header")
    if any(assay.get(field) is not None for field in ("feature_ids_sha256", "observation_ids_sha256", "metadata_sha256")):
        raise ValueError("AnnData header audit cannot establish cell, feature or annotation-value digests")
    if any(unit["independence"] == "independent" for unit in contract["biological_units"]):
        raise ValueError("AnnData header audit cannot upgrade supplied records into verified biological independence")
    if not any(item["field"] == "biological_units" for item in contract["unknowns"]):
        raise ValueError("Unverified AnnData biological independence must remain an explicit scientific unknown")
    return contract
