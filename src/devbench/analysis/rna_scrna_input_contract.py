"""Bounded, source-bound AnnData inspection for scientific planning."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
import re
from types import SimpleNamespace

from devharness_core.provenance import canonical_json


BINDING_FORMAT = "devbench.scrna-input-binding/1"
INSPECTION_FORMAT = "devbench.scrna-input/1"
INVENTORY_BINDING_FORMAT = "devbench.scrna-input-binding/2"
INVENTORY_INSPECTION_FORMAT = "devbench.scrna-input/2"
INVENTORY_FORMAT = "devbench.inherited-analysis-inventory/1"
ANN_DATA_SLOTS = ("X", "layers", "obs", "var", "obsm", "varm", "obsp", "varp", "uns", "raw")
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


@dataclass(frozen=True)
class InheritedAnalysisInventory:
    source_sha256: str
    slots: dict

    def to_dict(self):
        return {"format": INVENTORY_FORMAT, "source_sha256": self.source_sha256, "slots": self.slots}


def _provenance_records(values, source_sha256):
    if not isinstance(values, (tuple, list)) or len(values) > 128:
        raise ValueError("Bound inherited provenance to at most 128 object records")
    result = {}
    required = {"path", "artifact_sha256", "source"}
    ancestry = {"fit_artifact_sha256", "training_observation_ids_sha256", "training_feature_ids_sha256",
                "training_labels_sha256", "configuration_sha256"}
    choices = {
        "representation": ("original_counts", "normalized_expression", "corrected_expression",
                           "imputed_expression", "estimated_counts", "unknown"),
        "label_origin": ("orthogonal_measurement", "independent_assignment",
                         "external_reference_classification", "expression_based_study_annotation",
                         "result_selected_subgroup", "unknown"),
        "training_scope": ("training_only", "full_cohort", "unknown"),
    }
    for row in values:
        if (type(row) is not dict or not required <= row.keys()
                or row.keys() - required - choices.keys() - ancestry
                or any(type(row[key]) is not str or not row[key].strip() or len(row[key]) > 4000
                       or "\x00" in row[key] for key in required)
                or any(key in row and row[key] not in options for key, options in choices.items())
                or any(key in row and (not isinstance(row[key], str)
                    or not re.fullmatch("[0-9a-f]{64}", row[key])) for key in ancestry)):
            raise ValueError("Invalid inherited object provenance record")
        if row["artifact_sha256"] != source_sha256:
            raise ValueError("Inherited provenance does not bind this artifact")
        if row["path"] in result:
            raise ValueError("Conflicting inherited provenance for one object")
        result[row["path"]] = deepcopy(row)
    return result


def _inspect_count_values(element, shape, budget):
    import h5py
    import numpy as np
    from devbench.core.population_data import _node, _validate_sparse_storage

    if len(shape) != 2 or min(shape) < 1 or shape[0] > 2_000_000 or shape[1] > 100_000:
        raise ValueError("Inherited count matrix exceeds supported axes")
    if isinstance(element, h5py.Dataset):
        values = element
        metadata_bytes = 0
    else:
        encoding = element.attrs.get("encoding-type")
        if encoding not in ("csr_matrix", "csc_matrix"):
            raise ValueError("Unsupported inherited count matrix storage")
        values, indices, indptr = (_node(element, key) for key in ("data", "indices", "indptr"))
        if any(not isinstance(item, h5py.Dataset) for item in (values, indices, indptr)):
            raise ValueError("Sparse count components must be datasets")
        metadata_bytes = indices.size * indices.dtype.itemsize + indptr.size * indptr.dtype.itemsize
    size = values.size * values.dtype.itemsize + metadata_bytes
    if size > budget:
        return False, 0, "count_value_budget_exceeded"
    if values.dtype.kind not in "iuf":
        return False, 0, "count_values_not_numeric"
    if metadata_bytes or not isinstance(element, h5py.Dataset):
        sparse = SimpleNamespace(_element=element, shape=shape,
                                 matrix=SimpleNamespace(format=encoding[:3]))
        _validate_sparse_storage(sparse, 1024 * 1024)
        coordinates = indices[:]
        if coordinates.size > 1:
            noncanonical = coordinates[1:] <= coordinates[:-1]
            ends = indptr[1:-1]
            ends = ends[(ends > 0) & (ends < coordinates.size)]
            noncanonical[ends - 1] = False
            if noncanonical.any():
                return False, size, "noncanonical_sparse_coordinates"
    row_bytes = values.dtype.itemsize * (shape[1] if values.ndim == 2 else 1)
    block = max(1, 1024 * 1024 // row_bytes)
    for start in range(0, values.shape[0], block):
        chunk = values[start:start + block]
        if (not np.isfinite(chunk).all() or np.any(chunk < 0)
                or np.any(chunk >= 1 << 53)
                or (chunk.dtype.kind == "f" and np.any(chunk != np.floor(chunk)))):
            return False, size, "values_not_exact_nonnegative_integer_counts"
    return True, size, None


def _axis_digest(group, expected):
    import hashlib
    import h5py
    from devbench.core.population_data import _node, _annotation

    if (not isinstance(group, h5py.Group)
            or ("_index" in group.attrs and group.attrs.get_id("_index").shape != ())):
        raise ValueError("Unsupported inherited count axis encoding")
    name = group.attrs.get("_index", "_index")
    values, _ = _annotation(_node(group, name), expected, 8 * 1024 * 1024)
    values = list(values)
    if any(not isinstance(value, str) for value in values) or len(set(values)) != expected:
        raise ValueError("Inherited count axes require unique nonmissing identifiers")
    return hashlib.sha256(canonical_json(values)).hexdigest()


def audit_inherited_anndata(handle, source_sha256, provenance=()):
    """Inventory the ten slots and check candidate counts inside a bounded read."""
    import h5py
    from .workbench_inputs import _h5ad_header, _names
    from devbench.core.population_data import _node

    if not isinstance(source_sha256, str) or not re.fullmatch("[0-9a-f]{64}", source_sha256):
        raise ValueError("Inventory needs an exact source SHA-256")
    _closed_storage(handle)
    header = _h5ad_header(handle, 128)
    declared = _provenance_records(provenance, source_sha256)
    slots, seen, budget = {}, set(), 64 * 1024 * 1024
    for name in ANN_DATA_SLOTS:
        records = []
        slots[name] = {"present": name in handle, "objects": records}
        if name not in handle:
            continue
        slot = _node(handle, name)
        if name == "X":
            members = [("X", slot)]
        else:
            if not isinstance(slot, h5py.Group):
                raise ValueError("AnnData slot requires a group: " + name)
            members = [(name + "/" + key, _node(slot, key)) for key in _names(slot, 128)]
        for path, element in members:
            seen.add(path)
            declaration = declared.get(path)
            representation = (declaration or {}).get("representation", "unknown")
            matrix_slot = name in {"X", "layers"} or path == "raw/X"
            shape = list(element.shape) if isinstance(element, h5py.Dataset) else None
            if isinstance(element, h5py.Group):
                attr = element.attrs.get_id("shape") if "shape" in element.attrs else None
                if attr is not None and attr.shape == (2,):
                    shape = [int(v) for v in element.attrs["shape"]]
            if shape is not None:
                shape = [int(v) for v in shape]
            aligned = None
            if shape is not None and name in {"obsm", "varm", "obsp", "varp"}:
                axis_size = header["cells" if name.startswith("obs") else "genes"]
                aligned = (shape == [axis_size, axis_size] if name.endswith("p")
                           else bool(shape) and shape[0] == axis_size)
            record = {
                "path": path, "shape": shape, "aligned": aligned, "provenance": declaration,
                "provenance_status": "supplied" if declaration else "unknown",
                "integer_counts_verified": None, "count1_admissible": False, "axes": None,
                "count_storage_bytes_checked": 0,
                "count1_hold": "original_counts_provenance_required",
                "primary_pp_label_basis_candidate": name == "obs" and (declaration or {}).get("label_origin")
                    in {"orthogonal_measurement", "independent_assignment"},
                "primary_pp_label_admissible": False,
                # T15 owns physical partition custody. A supplied training scope
                # cannot by itself authorize reuse of an inherited representation.
                "held_out_admissible": False,
            }
            if matrix_slot and representation not in {"original_counts", "unknown"}:
                record["count1_hold"] = "transformed_or_estimated_measurement"
            if matrix_slot and (path == "raw/X" or representation == "original_counts"):
                feature_axis = "raw/var" if path == "raw/X" else "var"
                var = _node(slot, "var") if path == "raw/X" else _node(handle, "var")
                if (shape is None or len(shape) != 2 or shape[0] != header["cells"]
                        or (feature_axis == "var" and shape[1] != header["genes"])):
                    raise ValueError("Inherited count dimensions contradict the declared axes")
                checked, used, reason = _inspect_count_values(element, shape, budget)
                budget -= used
                record["count_storage_bytes_checked"] = used
                record["integer_counts_verified"] = checked
                record["count1_admissible"] = checked and representation == "original_counts"
                record["count1_hold"] = reason or (None if record["count1_admissible"]
                    else record["count1_hold"])
                if checked:
                    record["axes"] = {"observation_axis": "obs", "feature_axis": feature_axis,
                        "observation_ids_sha256": _axis_digest(_node(handle, "obs"), shape[0]),
                        "feature_ids_sha256": _axis_digest(var, shape[1])}
            records.append(record)
    if declared.keys() - seen:
        raise ValueError("Inherited provenance names an absent AnnData object")
    inventory = InheritedAnalysisInventory(source_sha256, slots).to_dict()
    if len(canonical_json(inventory)) > MAX_METADATA_BYTES:
        raise ValueError("Inherited AnnData inventory exceeds its metadata byte budget")
    return inventory


def _header_worker(root, name, root_identity, expected, expected_sha256, provenance=None):
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
                if provenance is None:
                    _closed_storage(handle)
                header = _h5ad_header(handle, 128)
                if provenance is not None:
                    inventory = audit_inherited_anndata(handle, expected_sha256, provenance)
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Registered AnnData changed during header inspection")
    _unchanged(root, (name,), root_identity, expected)
    if header["cells"] > 2_000_000 or header["genes"] > 100_000:
        raise ValueError("AnnData shape exceeds the header-admission bounds")
    header.update(file_type="h5ad", expression_values_read=False, observation_ids_read=False,
        feature_ids_read=False, annotation_values_read=False)
    return header if provenance is None else {"header": header, "inventory": inventory}


def inspect_registered_scrna_input(methods, binding, *, actor=None):
    """Derive a bounded profile from exact immutable artifact bytes, not client metadata."""
    fields = {"format", "anndata_artifact_id", "declarations"}
    inventory_requested = isinstance(binding, dict) and binding.get("format") == INVENTORY_BINDING_FORMAT
    if (type(binding) is not dict or set(binding) != (fields | {"inherited_provenance"} if inventory_requested else fields)
            or binding["format"] not in {BINDING_FORMAT, INVENTORY_BINDING_FORMAT}):
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
        arguments = (str(path.parent), path.name, root_identity, expected, artifact["sha256"])
        if inventory_requested:
            supplied = binding["inherited_provenance"]
            _provenance_records(supplied, artifact["sha256"])
            arguments += (supplied,)
        header = run_sandboxed(_header_worker, arguments,
            max_mem_mb=512, timeout_sec=20, diagnostics=diagnostics)
    except HarnessResourceInterrupt as error:
        raise ValueError("AnnData header inspection exceeded its supervision bounds: " + str(error)[:400]) from error
    except RemoteWorkerError as error:
        if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
            raise ValueError("AnnData header worker failed and cleanup is unverified") from error
        raise ValueError("AnnData header rejected: " + str(error).splitlines()[0][:400]) from error
    if diagnostics.get("cleanup", {}).get("verified_stopped") is not True:
        raise ValueError("AnnData header worker cleanup is unverified")
    inventory = header["inventory"] if inventory_requested else None
    if inventory_requested:
        header = header["header"]
    # Reverify the manifest and content after the independently opened worker.
    if methods.artifact_path(identity) != path or methods.get("artifact", identity) != artifact:
        raise ValueError("AnnData artifact identity changed during inspection")
    raw_selected = inventory_requested and declarations["counts_layer"] == "raw/X" and any(
        item["path"] == "raw/X" for item in inventory["slots"]["raw"]["objects"])
    if declarations["counts_layer"] != "X" and declarations["counts_layer"] not in header["layers"] and not raw_selected:
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
    if inventory_requested:
        from .workbench_episode_intake import bind_inherited_count_source

        result.update(format=INVENTORY_INSPECTION_FORMAT, inherited_analysis=inventory)
        result["count_source"] = bind_inherited_count_source(
            inventory, declarations["counts_layer"], declarations["measurement"])
        objects = [item for slot in inventory["slots"].values() for item in slot["objects"]]
        result["inspection"]["count_values_checked"] = any(item["count_storage_bytes_checked"] for item in objects)
        result["inspection"]["count_axes_checked"] = any(item["axes"] for item in objects)
        result["binding"].update(format=INVENTORY_BINDING_FORMAT,
                                 inherited_provenance=deepcopy(binding["inherited_provenance"]))
        result["unknowns"][0]["reason"] = (
            "Only inventoried count candidates were checked. Other expression values remain unassessed.")
        result["unknowns"][1]["reason"] = (
            "Axis digests apply only to checked count candidates. Cross-file alignment remains unassessed.")
    if len(canonical_json(result)) > MAX_METADATA_BYTES:
        raise ValueError("AnnData scientific header profile exceeds its bounded metadata budget")
    return result


def verify_scrna_inspection(methods, inspection):
    if type(inspection) is not dict or inspection.get("format") not in (INSPECTION_FORMAT, INVENTORY_INSPECTION_FORMAT):
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
