"""Representation-preserving bindings to the existing governed method ports.

JSON identity bindings cover the existing bulk /1 contract. File/table bridges
are admitted only for a verified registered reader and an exact safe round trip;
unsupported parser semantics are refused, never repaired by changing the data.
"""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import platform
import tempfile

from devharness_core.provenance import canonical_json, hash_artifact
from devbench.study.evidence import _read

from .checkpoints import digest
from .episode_proposals import _absolute, _load, _seal, require_disjoint_output
from .rna_input_contract import MAX_MATRIX_BYTES, MAX_SAMPLES, validate_bulk_rna_input

FORMAT = "devbench.rna-execution-inputs/2"
PORTS_FORMAT = "devbench.rna-execution-ports/2"
RECEIPT_FORMAT = "devbench.rna-input-transformation/2"
_ARTIFACT_FIELDS = {"artifact_id", "sha256", "size_bytes", "artifact_type", "suffix", "generated_by"}
_RECORD_FIELDS = {"format", "original_refs", "execution_refs", "transformation_receipt_ref", "declarations_ref",
                  "method_ports_sha256", "capabilities", "prerequisites"}
_READERS = {"fit_temporal_trajectory", "analyze_batch_effects", "validate_design_matrix", "bulk_condition_contrast"}


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 200:
        raise ValueError("Use bounded explicit method port/design identifiers")


def _source(path, maximum=1_000_000):
    path = _absolute(path)
    actual = hash_artifact(path, max_bytes=maximum, max_entries=1)
    if actual["kind"] != "file":
        raise ValueError("A retained source must be an ordinary bounded file")
    return {"path": str(path), "sha256": actual["sha256"], "size_bytes": actual["size_bytes"]}


def _read_source(reference, maximum=1_000_000, *, workspace=None):
    if type(reference) is not dict or set(reference) != {"path", "sha256", "size_bytes"}:
        raise ValueError("Use an exact immutable source reference")
    if (type(reference["path"]) is not str or type(reference["sha256"]) is not str or len(reference["sha256"]) != 64
            or type(reference["size_bytes"]) is not int or not 1 <= reference["size_bytes"] <= maximum):
        raise ValueError("Immutable source identity exceeds its explicit type/byte bounds")
    if workspace is not None and not _absolute(reference["path"]).is_relative_to(_absolute(workspace)):
        raise ValueError("Immutable source reference lies outside the assigned workspace")
    body = _read(_absolute(reference["path"]), maximum)
    if len(body) != reference["size_bytes"] or hashlib.sha256(body).hexdigest() != reference["sha256"]:
        raise ValueError("Immutable source hash or byte count changed")
    return _decode_json(body)


def _read_json(path, maximum, *, expected=None):
    path = _absolute(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("RNA input exceeds its existing byte bound")
    body = _read(path, maximum)
    if expected is not None and (len(body) != expected["size_bytes"] or hashlib.sha256(body).hexdigest() != expected["sha256"]):
        raise ValueError("Registered artifact content changed before its bounded JSON read")
    return _decode_json(body)


def _decode_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("RNA input contains duplicate JSON fields")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("RNA input JSON requires finite numbers")
    return json.loads(body, object_pairs_hook=unique, parse_constant=invalid)


def _artifact(service, identity, expected=None):
    path = service.artifact_path(identity)
    actual = service.get("artifact", identity)
    if (set(actual) != _ARTIFACT_FIELDS or actual["artifact_id"] != identity
            or digest({key: value for key, value in actual.items() if key != "artifact_id"}) != identity
            or (expected is not None and actual != expected)):
        raise ValueError("Original/derived artifact identity changed or is unrelated")
    return actual, path


def _ports(value):
    keys = {"format", "ports", "accepted_representations", "objective", "design", "string_columns"}
    if type(value) is not dict or not keys <= set(value) or set(value) - keys - {"method_version_id", "parameters"} or value["format"] != PORTS_FORMAT:
        raise ValueError("Use the closed explicit RNA method-port descriptor")
    if (type(value["ports"]) is not dict or not 1 <= len(value["ports"]) <= 32
            or len(canonical_json(value)) > 100_000):
        raise ValueError("Method ports exceed their bounds")
    for name, port in value["ports"].items():
        _text(name)
        if type(port) is not dict or set(port) != {"source", "artifact_type", "encoding", "required", "parameter"}:
            raise ValueError("Bind each source to an exact declared method port")
        if port["source"] not in {"matrix", "metadata"} or type(port["required"]) is not bool:
            raise ValueError("Unsupported RNA port source")
        if port["parameter"] is not None:
            _text(port["parameter"])
        if (port["encoding"] == "json" and port["artifact_type"] != "json"
                or port["encoding"] == "csv" and port["artifact_type"] != ("file" if port["source"] == "matrix" else "table")
                or port["encoding"] == "hdf5" and (port["source"] != "matrix" or port["artifact_type"] != "file")
                or port["encoding"] not in {"json", "csv", "hdf5"}):
            raise ValueError("Unsupported lossless encoding/port type")
    reps = value["accepted_representations"]
    if type(reps) is not list or not 1 <= len(reps) <= 2 or len(set(reps)) != len(reps) or set(reps) - {"raw_counts", "normalized_expression"}:
        raise ValueError("Declare the method's supported measurement representations")
    if value["objective"] not in ({"intent": "infer"}, {"intent": "descriptive"}):
        raise ValueError("Supply the actual descriptive/inferential objective")
    design = value["design"]
    required_design = {"contrasts", "covariates", "chronological_field", "chronological_order"}
    if type(design) is not dict or not required_design <= set(design) or set(design) - required_design - {"blocking"}:
        raise ValueError("Supply the complete explicit design")
    blocking = design.get("blocking", [])
    if type(blocking) is not list or len(blocking) > 1:
        raise ValueError("Declare at most one explicit biological-unit blocking column")
    for column in blocking:
        _text(column)
    for field, maximum in (("covariates", 16), ("chronological_order", MAX_SAMPLES), ("string_columns", 128)):
        values = value[field] if field == "string_columns" else design[field]
        if type(values) is not list or len(values) > maximum or len(set(values)) != len(values):
            raise ValueError("Design/parser labels must be bounded and unique")
        for item in values:
            _text(item)
    if design["chronological_field"] is not None:
        _text(design["chronological_field"])
    if type(design["contrasts"]) is not list or len(design["contrasts"]) > 16:
        raise ValueError("Contrasts exceed their bound")
    for contrast in design["contrasts"]:
        if type(contrast) is not dict or set(contrast) != {"field", "reference", "target"}:
            raise ValueError("Use explicit contrast fields and levels")
        for item in contrast.values():
            _text(item)
    return json.loads(canonical_json(value))


def _identity():
    versions = {}
    for name in ("numpy", "pandas", "devharness"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"code_sha256": _source(Path(__file__).resolve(), 1_000_000)["sha256"],
            "runtime_sha256": digest({"python": platform.python_version(), "platform": platform.platform(), "packages": versions})}


def _reader(service, ports, representation, *, inspection):
    if all(port["encoding"] == "json" for port in ports["ports"].values()):
        return None
    if "method_version_id" not in ports or "parameters" not in ports:
        raise ValueError("File/table conversion requires a verified registered parser/method contract")
    from devharness_core.methods import MethodContract
    from devharness_server.method_adapter import dispatcher_contract
    from devharness_core.units import BIOLOGICAL_UNIT_COLUMNS
    method = service.method(ports["method_version_id"])
    contract = method["contract"]
    implementation = contract["implementation"]
    if implementation["kind"] != "dispatcher" or implementation["tool_name"] not in _READERS:
        raise ValueError("Unsupported registered lossless CSV parser contract")
    live = dispatcher_contract(implementation["tool_name"])
    if live.version_id != ports["method_version_id"]:
        raise ValueError("Registered reader source/runtime contract changed")
    parameters = MethodContract.from_dict(contract).validate_parameters(ports["parameters"])
    declared = contract["inputs"]
    if set(ports["ports"]) != set(declared):
        raise ValueError("Port names differ from the exact registered method")
    for name, port in ports["ports"].items():
        if any(port[key] != declared[name].get(key) for key in ("artifact_type", "required", "parameter")):
            raise ValueError("Port type/parameter differs from the exact registered method")
        if port["source"] != ("metadata" if port["parameter"] in {"metadata_path", "metadata_csv_path"} else "matrix"):
            raise ValueError("Registered matrix/metadata port sources were swapped")
    if implementation["tool_name"] == "bulk_condition_contrast":
        from devharness_server.tools_bulk import validate_bulk_contrast_parameters
        from devbench.study.biology_tool_io import validate_public_task
        validate_bulk_contrast_parameters(parameters["public_task"], parameters["options"])
        public = validate_public_task(parameters["public_task"])
        expected = "continuous" if representation == "normalized_expression" else "raw_counts"
        if public["measurement"] != expected:
            raise ValueError("Bulk reader measurement differs from the original representation")
        if any(port["encoding"] != "csv" for port in ports["ports"].values()):
            raise ValueError("Bulk contrast reader requires exact CSV ports")
        if (not inspection["sample_metadata_complete"]
                or set(ports["string_columns"]) != set(inspection["sample_metadata_fields"])
                or any(type(value) is not str for row in inspection["sample_metadata"] for value in row.values())):
            raise ValueError("Bulk CSV metadata requires complete explicitly string-valued source fields")
        if expected == "raw_counts" and inspection["numeric_storage"] != "integer":
            raise ValueError("Bulk count CSV requires original integer storage; no count conversion is permitted")
        if digest(parameters["public_task"]["gene_ids"]) != inspection["gene_ids_sha256"]:
            raise ValueError("Bulk contrast gene family must equal the complete admitted feature axis")
        return {"tool_name": implementation["tool_name"], "method_version_id": method["version_id"],
            "contract_sha256": digest(contract), "parameters": parameters,
            "measurement_interpretation": {"mode": "explicit_bulk", "data_type": public["measurement"],
                "declared_representation": representation}}
    if set(ports["string_columns"]) - {"sample_id", *BIOLOGICAL_UNIT_COLUMNS}:
        raise ValueError("Caller string columns cannot change the actual registered parser")
    interpretation = None
    expected = "continuous" if representation == "normalized_expression" else "counts"
    matrix_consumed = any(port["source"] == "matrix" for port in ports["ports"].values())
    if implementation["tool_name"] == "fit_temporal_trajectory":
        if parameters.get("data_type") != expected:
            raise ValueError("Explicit data_type must preserve the declared measurement representation")
        interpretation = {"mode": parameters["data_type"], "data_type": expected, "declared_representation": representation}
    elif matrix_consumed:
        if implementation["tool_name"] != "analyze_batch_effects":
            raise ValueError("Unsupported actual consumer measurement interpretation contract")
        actual = "counts" if inspection["numeric_storage"] == "integer" and inspection["count_values_valid"] else "continuous"
        if actual != expected:
            raise ValueError("Actual auto consumer interpretation conflicts with the original measurement representation")
        interpretation = {"mode": "auto", "data_type": actual, "declared_representation": representation}
    return {"tool_name": implementation["tool_name"], "method_version_id": method["version_id"],
            "contract_sha256": digest(contract), "parameters": parameters, "measurement_interpretation": interpretation}


def _csv_rows(matrix, inspection, source):
    if source == "matrix":
        yield ["gene_id", *matrix["sample_ids"]]
        for identity, values in zip(matrix["gene_ids"], matrix["matrix"]):
            yield [identity, *(json.dumps(value, allow_nan=False) for value in values)]
    else:
        fields = inspection["sample_metadata_fields"]
        yield fields
        for row in inspection["sample_metadata"]:
            if set(row) != set(fields) or any(value is None for value in row.values()):
                raise ValueError("CSV cannot preserve absent keys/null metadata types losslessly")
            yield [row[field] for field in fields]


def _csv_chunks(rows, maximum):
    size = 0
    for row in rows:
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerow(row)
        encoded = buffer.getvalue().encode("utf-8")
        size += len(encoded)
        if size > maximum:
            raise ValueError("Derived CSV exceeds the existing byte bound")
        yield encoded


def _write_csv(path, rows, maximum):
    with path.open("xb") as output:
        for encoded in _csv_chunks(rows, maximum):
            output.write(encoded)


def _hdf5_label_width(values):
    if any(type(value) is not str or not value or len(value) > 200 for value in values):
        raise ValueError("HDF5 labels exceed the reviewed UTF-8 identifier bounds")
    try:
        width = max(len(value.encode("utf-8")) for value in values)
    except UnicodeEncodeError as error:
        raise ValueError("HDF5 identifiers require valid UTF-8 encoding") from error
    if not 1 <= width <= 800 or any("\x00" in value for value in values):
        raise ValueError("HDF5 fixed-width labels exceed the reviewed byte bounds or contain null bytes")
    return width


def _hdf5_dtype(matrix):
    """Bound the exact writer's logical storage before creating or reading it."""
    import numpy as np
    types = {type(value) for row in matrix["matrix"] for value in row}
    if types not in ({int}, {float}):
        raise ValueError("A homogeneous HDF5 array cannot preserve mixed numeric types losslessly")
    integer = types == {int}
    if integer and any(not -(2 ** 63) <= value < 2 ** 63 for row in matrix["matrix"] for value in row):
        raise ValueError("Raw integer storage exceeds the exact reviewed int64 capability")
    dtype = np.dtype(np.int64 if integer else np.float64)
    logical_bytes = len(matrix["gene_ids"]) * len(matrix["sample_ids"]) * dtype.itemsize
    logical_bytes += sum(len(values) * _hdf5_label_width(values) for values in (matrix["gene_ids"], matrix["sample_ids"]))
    if logical_bytes > MAX_MATRIX_BYTES:
        raise ValueError("Derived HDF5 logical storage exceeds the existing 100 MB byte bound")
    return dtype


def _write_hdf5(path, matrix):
    import h5py
    import numpy as np
    dtype = _hdf5_dtype(matrix)
    with h5py.File(path, "x") as handle:
        values = handle.create_dataset("expression", shape=(len(matrix["gene_ids"]), len(matrix["sample_ids"])),
                                       dtype=dtype, track_times=False)
        for name, labels in (("gene_names", matrix["gene_ids"]), ("sample_names", matrix["sample_ids"])):
            label_dtype = h5py.string_dtype("utf-8", length=_hdf5_label_width(labels))
            target = handle.create_dataset(name, shape=(len(labels),), dtype=label_dtype, track_times=False)
            for start in range(0, len(labels), 32):
                target[start:start + 32] = np.asarray([value.encode("utf-8") for value in labels[start:start + 32]], dtype=label_dtype)
        for start in range(0, len(matrix["matrix"]), 500):
            values[start:start + 500] = np.asarray(matrix["matrix"][start:start + 500], dtype=dtype)
    if path.stat().st_size > MAX_MATRIX_BYTES:
        raise ValueError("Derived HDF5 exceeds the existing 100 MB byte bound")


def _check_hdf5(path, matrix):
    import h5py
    dtype = _hdf5_dtype(matrix)
    if path.stat().st_size > MAX_MATRIX_BYTES:
        raise ValueError("Derived HDF5 physical storage exceeds the existing byte bound")
    with h5py.File(path, "r") as handle:
        if set(handle) != {"expression", "gene_names", "sample_names"}:
            raise ValueError("Derived HDF5 schema differs from the reviewed encoding")
        for name in handle:
            if not isinstance(handle.get(name, getlink=True), h5py.HardLink):
                raise ValueError("Derived HDF5 cannot follow external or symbolic resources")
            dataset = handle[name]
            if not isinstance(dataset, h5py.Dataset) or dataset.external or dataset.is_virtual or dataset.id.get_create_plist().get_nfilters():
                raise ValueError("Derived HDF5 cannot use external, virtual or filtered storage")
        if (handle["gene_names"].shape != (len(matrix["gene_ids"]),)
                or handle["sample_names"].shape != (len(matrix["sample_ids"]),)
                or handle["expression"].shape != (len(matrix["gene_ids"]), len(matrix["sample_ids"]))):
            raise ValueError("Derived HDF5 dimensions changed")
        if (handle["expression"].dtype != dtype or handle["expression"].dtype.itemsize != 8
                or handle["expression"].dtype.metadata is not None):
            raise ValueError("Derived HDF5 expression dtype/itemsize is outside reviewed exact storage")
        for name, expected in (("gene_names", matrix["gene_ids"]), ("sample_names", matrix["sample_ids"])):
            declared = h5py.check_string_dtype(handle[name].dtype)
            width = _hdf5_label_width(expected)
            if (declared is None or declared.encoding != "utf-8" or declared.length != width
                    or handle[name].dtype.kind != "S" or handle[name].dtype.itemsize != width):
                raise ValueError("Derived HDF5 labels require reviewed fixed-width UTF-8 storage; variable-length heaps are unsupported")
        # The fixed-width schema bounds each label block to 32 * 800 bytes
        # before any slice allocates. Compare bytes before Unicode decoding.
        for name, expected in (("gene_names", matrix["gene_ids"]), ("sample_names", matrix["sample_ids"])):
            for start in range(0, len(expected), 32):
                for actual, original in zip(handle[name][start:start + 32], expected[start:start + 32]):
                    encoded = original.encode("utf-8")
                    if not isinstance(actual, bytes) or len(actual) > 800 or actual != encoded:
                        raise ValueError("Derived HDF5 gene/sample IDs or order changed or exceed label bounds")
        for start in range(0, len(matrix["matrix"]), 500):
            for offset, row in enumerate(handle["expression"][start:start + 500]):
                for actual, original in zip(row, matrix["matrix"][start + offset]):
                    if canonical_json(actual.item()) != canonical_json(original):
                        raise ValueError("Derived HDF5 values or numeric types changed")


def _write_port(path, port, matrix, inspection):
    if port["encoding"] == "hdf5":
        _write_hdf5(path, matrix)
    else:
        _write_csv(path, _csv_rows(matrix, inspection, port["source"]), MAX_MATRIX_BYTES if port["source"] == "matrix" else 5_000_000)


def _operation(port):
    return {"json": "identity", "csv": "lossless_registered_csv_serialization",
            "hdf5": "lossless_registered_hdf5_serialization"}[port["encoding"]]


def _check_csv(path, source, matrix, inspection, reader):
    if source == "matrix":
        from devharness_core.storage import ChunkedExpressionStore
        with ChunkedExpressionStore(path) as store:
            if store.gene_names != matrix["gene_ids"] or store.sample_names != matrix["sample_ids"]:
                raise ValueError("Registered parser changed gene/sample IDs or order")
            offset = 0
            for names, values in store.iter_gene_chunks(500):
                for row in values:
                    original = matrix["matrix"][offset]
                    if any(canonical_json(value.item()) != canonical_json(before) for value, before in zip(row, original)):
                        raise ValueError("Registered CSV parser cannot preserve matrix values/types losslessly")
                    offset += 1
            interpretation = reader["measurement_interpretation"]
            if interpretation is not None and interpretation["mode"] == "auto":
                from devharness_server.normalization import expression_summary
                if expression_summary(store)["data_type"] != interpretation["data_type"]:
                    raise ValueError("Actual registered auto consumer changed the declared measurement interpretation")
    else:
        from devharness_server.tools_stat import _metadata
        frame = _metadata(str(path), reader["parameters"].get("time_col", "embryonic_age"))
        fields = inspection["sample_metadata_fields"]
        if set(frame.columns) != set(fields):
            raise ValueError("Registered CSV parser changes metadata columns; no lossless mapping")
        for position, original in enumerate(inspection["sample_metadata"]):
            for field, before in original.items():
                after = frame[field].iloc[position]
                if type(after).__module__.startswith("numpy"):
                    after = after.item()
                if type(after) is not type(before) or after != before:
                    raise ValueError("Registered CSV parser cannot preserve metadata values/types/missingness losslessly")


def _check_bulk_roundtrip(paths, matrix, inspection, reader, scratch):
    """Exercise the actual bulk loader on canonical source-preserving CSVs; never fit."""
    from devbench.study.biology_tool_io import load_observed_data
    annotations = Path(scratch) / "empty-annotations.json"
    annotations.write_bytes(b"{}")
    observed = load_observed_data(reader["parameters"]["public_task"],
        counts_path=paths["matrix"], metadata_path=paths["metadata"], annotations_path=annotations,
        allow_data_execution=True)
    if list(observed.gene_ids) != matrix["gene_ids"] or list(observed.sample_ids) != matrix["sample_ids"]:
        raise ValueError("Actual bulk loader changed matrix axes")
    # Python float/int equality checks the original integer exactly; NumPy's
    # common-dtype comparison would first round large integers to float64.
    for parsed, original in zip(observed.expression, matrix["matrix"]):
        if any(float(after) != before for after, before in zip(parsed, original)):
            raise ValueError("Actual bulk loader changed measurement precision")
    if observed.metadata.to_dict(orient="records") != inspection["sample_metadata"]:
        raise ValueError("Actual bulk loader changed metadata types, identities or alignment")


def _biological_design_audit(request, inspection):
    """Keep biological identity separate from the number of library rows."""
    from devharness_core.units import (
        CANDIDATE_BIOLOGICAL_UNIT_COLUMNS, UNKNOWN_UNIT_MARKERS,
        biological_identity_audit,
    )
    rows = inspection["sample_metadata"]
    unit = inspection.get("metadata_roles", {}).get("biological_unit")
    design = request["design"]
    blocking = design.get("blocking", [])
    blockers = set()
    declared_hierarchy = (inspection.get("biological_independence") or {}).get("unit_hierarchy")
    hierarchy = declared_hierarchy if type(declared_hierarchy) is dict else {}
    known_identity = (type(hierarchy.get("identity_status")) is str
                      and hierarchy["identity_status"] in {"declared", "verified"})
    if declared_hierarchy is not None and type(declared_hierarchy) is not dict:
        blockers.add("biological_unit_hierarchy_invalid")
    if hierarchy:
        labels = {name: hierarchy.get(name) for name in
                  ("population_unit_label", "specimen_label", "library_label")}
        if any(type(column) is not str or not column.strip() for column in labels.values()):
            blockers.add("biological_unit_hierarchy_invalid")
        else:
            if labels["population_unit_label"] != unit:
                blockers.add("biological_unit_hierarchy_role_mismatch")
            complete_columns = set()
            for column in labels.values():
                values = [row.get(column) for row in rows]
                if any(type(value) is not str or value.strip().lower() in UNKNOWN_UNIT_MARKERS for value in values):
                    blockers.add("independent_biological_units_unknown:" + column)
                else:
                    complete_columns.add(column)
            for child, parent, reason in (
                (labels["specimen_label"], labels["population_unit_label"], "specimen_crosses_biological_units"),
                (labels["library_label"], labels["specimen_label"], "library_crosses_specimens"),
            ):
                if {child, parent} <= complete_columns:
                    groups = {}
                    for row in rows:
                        groups.setdefault(row[child].strip(), set()).add(row[parent].strip())
                    if any(len(parents) > 1 for parents in groups.values()):
                        blockers.add(reason)
        if not known_identity:
            blockers.add("independent_biological_units_unknown:hierarchy_identity_status")
    identities = [row.get(unit) for row in rows] if unit else []
    complete = bool(identities) and all(type(value) is str and value.strip().lower() not in UNKNOWN_UNIT_MARKERS
                                       for value in identities)
    observations = {}
    if complete:
        for identity in identities:
            observations[identity.strip()] = observations.get(identity.strip(), 0) + 1
    if not complete:
        blockers.add("independent_biological_units_unknown:" + str(unit))
    model_columns = {item["field"] for item in design["contrasts"]} | set(design["covariates"]) | set(blocking)
    for column in (design["chronological_field"], inspection.get("metadata_roles", {}).get("batch")):
        if column is not None:
            model_columns.add(column)
    library = hierarchy.get("library_label")
    identity_audit = biological_identity_audit(rows, unit, population_unit=hierarchy.get("population_unit"),
        library_label=library if type(library) is str else None, design_columns=sorted(model_columns))
    for reason in identity_audit["problems"]:
        if reason.startswith("repeated_biological_unit_"):
            reason = "repeated_biological_units:" + reason.removeprefix("repeated_biological_unit_")
        elif reason.startswith("biological_unit_identity_unknown_"):
            reason = "independent_biological_units_unknown:" + reason.removeprefix("biological_unit_identity_unknown_")
        blockers.add(reason)
    paired = bool(blocking)
    if paired:
        if blocking != [unit]:
            blockers.add("blocking_column_must_match_biological_unit")
        if (hierarchy.get("population_unit_label") != unit
                or not known_identity):
            blockers.add("paired_biological_unit_hierarchy_required")
        if (len(design["contrasts"]) != 1 or design["chronological_field"] is not None
                or design["chronological_order"]):
            blockers.add("paired_design_requires_one_condition_contrast")
        elif complete:
            contrast = design["contrasts"][0]
            expected = {contrast["reference"], contrast["target"]}
            groups = {}
            for row in rows:
                groups.setdefault(row[unit].strip(), set()).add(row.get(contrast["field"]))
            if any(values != expected for values in groups.values()):
                blockers.add("paired_unit_missing_contrast_level:" + str(unit))
            if len(groups) < 2:
                blockers.add("paired_design_requires_independent_biological_units")
        if inspection["representation"] != "raw_counts":
            blockers.add("paired_design_requires_raw_counts")
    candidates = set(CANDIDATE_BIOLOGICAL_UNIT_COLUMNS) & set(inspection["sample_metadata_fields"])
    if unit is not None:
        candidates.add(unit)
    for column in sorted(candidates):
        values = [row.get(column) for row in rows]
        if any(type(value) is not str or value.strip().lower() in UNKNOWN_UNIT_MARKERS for value in values):
            blockers.add("independent_biological_units_unknown:" + column)
        elif not paired and len({value.strip() for value in values}) != len(values):
            blockers.add("repeated_biological_units:" + column)
    return {"blockers": sorted(blockers), "blocking_columns": list(blocking),
            "unit_count": len(observations) if complete else None,
            "biological_unit_observations": observations,
            "identity_findings": identity_audit["identity_findings"]}


def audit_execution_design(request, inspection):
    """Audit declared roles and fixed blocks without changing measurements."""
    import numpy as np
    from .rna_scientific_trial import _design_audit
    audit = _design_audit(request, inspection)
    blockers = set(audit["blockers"]) - {"design_rank_deficient_or_complete_confounding", "design_has_no_residual_degrees_of_freedom"}
    rows, roles, design = inspection["sample_metadata"], inspection.get("metadata_roles", {}), request["design"]
    infer = request["objective"]["intent"] == "infer"
    blocking = design.get("blocking", [])
    if (type(blocking) is not list or len(blocking) > 1
            or any(type(column) is not str or not column.strip() for column in blocking)):
        raise ValueError("Declare at most one explicit biological-unit blocking column")
    biological = {"blocking_columns": list(blocking)}
    if infer:
        biological = _biological_design_audit(request, inspection)
        blockers.discard("independent_biological_units_unknown_or_repeated")
        blockers.update(biological.pop("blockers"))
    targets = {item["field"] for item in design["contrasts"]}
    chronological = design["chronological_field"]
    fields = targets | set(design["covariates"]) | set(blocking)
    if chronological is not None:
        fields.add(chronological)
        targets.add(chronological)
    batch = roles.get("batch")
    if infer and batch is None and "batch" in inspection["sample_metadata_fields"]:
        blockers.add("observed_batch_requires_explicit_declaration")
    if infer and batch and targets:
        fields.add(batch)
        for target in targets:
            groups = {}
            for row in rows:
                groups.setdefault(canonical_json(row.get(batch)), set()).add(canonical_json(row.get(target)))
            if len({canonical_json(row.get(target)) for row in rows}) > 1 and all(len(values) == 1 for values in groups.values()):
                blockers.add("complete_confounding_with_categorical_batch:" + target)
    model_rows = rows
    if blocking == [roles.get("biological_unit")] and len(design["contrasts"]) == 1:
        condition = design["contrasts"][0]["field"]
        groups = {}
        for row in rows:
            key = (canonical_json(row.get(blocking[0])), canonical_json(row.get(condition)))
            groups.setdefault(key, []).append(row)
        for members in groups.values():
            if any(len({canonical_json(row.get(field)) for row in members}) > 1 for field in fields):
                blockers.add("repeated_units_across_design_coordinates_require_cluster_model")
        # The bulk estimator sums technical counts within unit × condition.
        # Audit the same number of observations without reading or fitting counts.
        model_rows = [members[0] for members in groups.values()]
    categorical = {roles[role] for role in ("condition", "sex", "batch") if role in roles}
    categorical.update(item["field"] for item in design["contrasts"])
    categorical.update(blocking)
    columns, column_names = [np.ones(len(model_rows))], ["Intercept"]
    for field in sorted(fields):
        values = [row.get(field) for row in model_rows]
        if any(value is None for value in values):
            blockers.add("design_covariate_missing:" + field)
            continue
        if field == chronological:
            order = design["chronological_order"]
            if all(str(value) in order for value in values):
                columns.append(np.array([order.index(str(value)) for value in values], dtype=float))
                column_names.append(field)
        elif field not in categorical and all(type(value) in (int, float) for value in values):
            columns.append(np.array(values, dtype=float))
            column_names.append(field)
        else:
            tokens = [canonical_json(value) for value in values]
            for level in sorted(set(tokens))[1:]:
                columns.append(np.array([float(value == level) for value in tokens]))
                column_names.append(field + "[" + level.decode("utf-8") + "]")
    rank = int(np.linalg.matrix_rank(np.column_stack(columns)))
    if rank != len(columns):
        blockers.add("design_rank_deficient_or_complete_confounding")
    if infer and len(model_rows) <= len(columns):
        blockers.add("design_has_no_residual_degrees_of_freedom")
    return {**audit, **biological, "rank": rank, "columns": len(columns),
            "design_columns": column_names, "analysis_observation_count": len(model_rows),
            "residual_degrees_of_freedom": len(model_rows) - rank,
            "blockers": sorted(blockers)}


def _facts(service, binding, ports):
    # Check JSON duplicate fields and the source-file bound before the existing
    # /1 inspection; its limits and scientific declaration semantics stay intact.
    original, documents = {}, {}
    if (type(binding) is not dict or set(binding) != {"format", "matrix_artifact_id", "metadata_artifact_id", "declarations"}
            or binding["format"] != "devbench.bulk-rna-binding/1"):
        raise ValueError("Use the exact original bulk RNA /1 binding")
    for name, maximum in (("matrix", MAX_MATRIX_BYTES), ("metadata", 5_000_000)):
        original[name], path = _artifact(service, binding[name + "_artifact_id"])
        if original[name]["artifact_type"] != "json":
            raise ValueError("Original bulk RNA /1 bindings require registered JSON artifacts")
        documents[name] = _read_json(path, maximum, expected=original[name])
    matrix, metadata = documents["matrix"], documents["metadata"]
    if (type(matrix) is not dict or set(matrix) != {"gene_ids", "sample_ids", "matrix"}
            or type(metadata) is not dict or set(metadata) != {"samples"}):
        raise ValueError("Bulk /1 requires exact matrix and sample metadata fields")
    try:
        inspection = validate_bulk_rna_input(binding["declarations"], gene_ids=matrix["gene_ids"], sample_ids=matrix["sample_ids"],
                                             metadata_rows=metadata["samples"], matrix=matrix["matrix"])
    except OverflowError as error:
        raise ValueError("Numeric measurements exceed the existing finite storage capability") from error
    inspection["input_artifact_refs"] = {name: {key: ref[key] for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")}
                                          for name, ref in original.items()}
    inspection["input_integrity_valid"] = True
    if inspection["representation"] not in ports["accepted_representations"]:
        raise ValueError("Unknown/unsupported measurement representation is not an execution input")
    audit = audit_execution_design({"objective": ports["objective"], "design": ports["design"]}, inspection)
    if ports["objective"]["intent"] == "infer" and audit["blockers"]:
        raise ValueError("Inference preconditions failed: " + ", ".join(audit["blockers"]))
    reader = _reader(service, ports, inspection["representation"], inspection=inspection)
    return original, documents["matrix"], inspection, audit, reader


def _prerequisites(ports, audit, declarations_ref):
    result = [{"prerequisite_id": "input_integrity_and_alignment", "status": "satisfied",
               "evidence_refs": [declarations_ref], "reason": "Recomputed bounded original artifact and exact ID checks"}]
    result.append({"prerequisite_id": "inference_design", "status": "satisfied" if ports["objective"]["intent"] == "infer" else "unknown",
                   "evidence_refs": [declarations_ref], "reason": "Necessary design checks passed; no independent scientific validation" if ports["objective"]["intent"] == "infer" else "No inferential preconditions were requested"})
    result.append({"prerequisite_id": "qualified_execution_route", "status": "unknown", "evidence_refs": [],
                   "reason": "Representation custody does not qualify or authorize an execution route"})
    return result


def prepare_execution_inputs(dataset_binding, method_ports, *, artifact_service, output):
    """Bind originals and exact derived formats; no method/worker is executed."""
    ports = _ports(method_ports)
    original, matrix, inspection, audit, reader = _facts(artifact_service, dataset_binding, ports)
    output = require_disjoint_output(output, [artifact_service.artifact_path(ref["artifact_id"]) for ref in original.values()])
    if not output.is_relative_to(_absolute(artifact_service.root).parent):
        raise ValueError("Execution-input output must stay inside the assigned service workspace")
    if output.exists():
        raise ValueError("Execution-input output must be fresh; immutable receipts cannot be overwritten")
    # Verify the exact registered consumers before creating a permanent bundle.
    with tempfile.TemporaryDirectory(prefix="rna-input-check-", dir=artifact_service.root) as scratch:
        bulk_reader = reader is not None and reader["tool_name"] == "bulk_condition_contrast"
        paths = {}
        for name, port in ports["ports"].items():
            if port["encoding"] != "json":
                path = Path(scratch) / (name + (".h5" if port["encoding"] == "hdf5" else ".csv"))
                _write_port(path, port, matrix, inspection)
                paths[port["source"]] = path
                if not bulk_reader:
                    _check_csv(path, port["source"], matrix, inspection, reader)
        if bulk_reader:
            _check_bulk_roundtrip(paths, matrix, inspection, reader, scratch)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    declarations_path = output / "declarations.json"
    with declarations_path.open("xb") as handle:
        handle.write(canonical_json(dataset_binding["declarations"]))
    declarations_path.chmod(0o444)
    declarations_ref = _source(declarations_path)
    execution, transformations = {}, []
    for name, port in ports["ports"].items():
        if port["encoding"] == "json":
            execution[name] = deepcopy(original[port["source"]])
        else:
            path = output / (name + (".h5" if port["encoding"] == "hdf5" else ".csv"))
            _write_port(path, port, matrix, inspection)
            execution[name] = artifact_service.import_artifact(path, artifact_type=port["artifact_type"], actor="rna-input-coordinator")
            path.chmod(0o444)
        transformations.append({"port": name, "source": port["source"], "encoding": port["encoding"],
            "source_artifact_id": original[port["source"]]["artifact_id"], "execution_artifact_id": execution[name]["artifact_id"],
            "operation": _operation(port),
            "scientific_filtering": False, "measurement_conversion": False})
    receipt = {"format": RECEIPT_FORMAT, "input_binding": deepcopy(dataset_binding), "method_ports": ports,
        "original_refs": original, "execution_refs": execution, "transformations": transformations,
        "metadata_alignment": inspection["metadata_alignment"], "measurement_values_sha256": inspection["measurement_values_sha256"],
        "gene_ids_sha256": inspection["gene_ids_sha256"], "sample_ids_sha256": inspection["sample_ids_sha256"],
        "metadata_sha256": inspection["metadata_sha256"], "aligned_metadata_sha256": inspection["aligned_metadata_sha256"],
        "design_audit": audit, "reader_contract": reader, **_identity()}
    receipt_path = output / "transformation-receipt.json"
    with receipt_path.open("xb") as handle:
        handle.write(canonical_json(receipt))
    receipt_path.chmod(0o444)
    capabilities = ["bulk_gene_expression", "known_measurement_representation", "exact_sample_alignment"]
    if ports["objective"]["intent"] == "infer":
        capabilities += ["independent_biological_units", "identifiable_design"]
        if ports["design"]["chronological_field"] is not None:
            capabilities.append("measured_time")
    record = {"format": FORMAT, "original_refs": original, "execution_refs": execution,
        "transformation_receipt_ref": _source(receipt_path), "declarations_ref": declarations_ref,
        "method_ports_sha256": digest(ports), "capabilities": capabilities,
        "prerequisites": _prerequisites(ports, audit, declarations_ref)}
    sealed = _seal(output / "binding", "execution-inputs.json", record, "rna_execution_inputs_prepared")
    return {"record_ref": _source(output / "binding" / "execution-inputs.json"),
            "manifest_path": sealed["manifest_path"], "root_hash": sealed["root_hash"]}


def load_execution_inputs(reference, *, artifact_service, dataset_binding=None, method_ports=None, workspace=None):
    """Revalidate source and derived content against the immutable transform."""
    if type(reference) is not dict:
        raise ValueError("Use an immutable execution-input reference")
    service_root = _absolute(artifact_service.root)
    workspace = _absolute(workspace) if workspace is not None else service_root.parent
    if not service_root.is_relative_to(workspace):
        raise ValueError("Artifact service lies outside the assigned execution-input workspace")
    if "manifest_path" in reference:
        manifest = _absolute(reference["manifest_path"])
        if not manifest.is_relative_to(workspace):
            raise ValueError("Execution-input manifest lies outside the assigned workspace")
        if not (manifest.parent.parent / "execution-inputs.json").is_relative_to(workspace):
            raise ValueError("Execution-input manifest body lies outside the assigned workspace")
        _, record, _ = _load(reference["manifest_path"], reference["root_hash"], "execution-inputs.json")
        if "record_ref" in reference and _read_source(reference["record_ref"], workspace=workspace) != record:
            raise ValueError("Execution-input source differs from its sealed record")
    else:
        record = _read_source(reference, workspace=workspace)
    if type(record) is not dict or set(record) != _RECORD_FIELDS or record["format"] != FORMAT:
        raise ValueError("Use the closed execution-input /2 record")
    receipt = _read_source(record["transformation_receipt_ref"], workspace=workspace)
    if receipt.get("format") != RECEIPT_FORMAT or any(receipt.get(key) != value for key, value in _identity().items()):
        raise ValueError("Transformation source/runtime identity changed")
    ports = _ports(receipt["method_ports"])
    binding = receipt["input_binding"]
    if dataset_binding is not None and binding != dataset_binding:
        raise ValueError("Original dataset binding differs from the transformation")
    if method_ports is not None and ports != _ports(method_ports):
        raise ValueError("Method ports differ from the immutable transformation")
    original, matrix, inspection, audit, reader = _facts(artifact_service, binding, ports)
    if record["original_refs"] != original or receipt["original_refs"] != original or record["execution_refs"] != receipt["execution_refs"]:
        raise ValueError("Original/derived artifact refs changed or are unrelated")
    if _read_source(record["declarations_ref"], workspace=workspace) != binding["declarations"] or record["method_ports_sha256"] != digest(ports):
        raise ValueError("Declarations or method-port identity changed")
    for key in ("metadata_alignment", "measurement_values_sha256", "gene_ids_sha256", "sample_ids_sha256", "metadata_sha256", "aligned_metadata_sha256"):
        if receipt[key] != inspection[key]:
            raise ValueError("Original values, sample order or metadata changed")
    if receipt["design_audit"] != audit or receipt["reader_contract"] != reader or set(record["execution_refs"]) != set(ports["ports"]):
        raise ValueError("Execution design/reader/port binding changed")
    expected_transforms = []
    for name, port in ports["ports"].items():
        ref, retained = _artifact(artifact_service, record["execution_refs"][name]["artifact_id"], record["execution_refs"][name])
        if port["encoding"] == "json":
            if ref != original[port["source"]]:
                raise ValueError("JSON identity binding cannot substitute an unrelated artifact")
        elif port["encoding"] == "hdf5":
            if retained.stat().st_size > MAX_MATRIX_BYTES:
                raise ValueError("Derived HDF5 exceeds the existing byte bound")
            _check_hdf5(retained, matrix)
        else:
            expected, size = hashlib.sha256(), 0
            for encoded in _csv_chunks(_csv_rows(matrix, inspection, port["source"]), MAX_MATRIX_BYTES if port["source"] == "matrix" else 5_000_000):
                expected.update(encoded)
                size += len(encoded)
            if expected.hexdigest() != ref["sha256"] or size != ref["size_bytes"]:
                raise ValueError("Derived values, columns or metadata differ from original lineage")
        expected_transforms.append({"port": name, "source": port["source"], "encoding": port["encoding"],
            "source_artifact_id": original[port["source"]]["artifact_id"], "execution_artifact_id": ref["artifact_id"],
            "operation": _operation(port),
            "scientific_filtering": False, "measurement_conversion": False})
    if receipt["transformations"] != expected_transforms or record["prerequisites"] != _prerequisites(ports, audit, record["declarations_ref"]):
        raise ValueError("Immutable transformation/prerequisite record changed")
    expected_capabilities = ["bulk_gene_expression", "known_measurement_representation", "exact_sample_alignment"]
    if ports["objective"]["intent"] == "infer":
        expected_capabilities += ["independent_biological_units", "identifiable_design"]
        if ports["design"]["chronological_field"] is not None:
            expected_capabilities.append("measured_time")
    if record["capabilities"] != expected_capabilities:
        raise ValueError("Execution capabilities cannot be supplied without recomputed evidence")
    return deepcopy(record)


def validate_execution_method_binding(execution_inputs, method_version_id, parameters, *, artifact_service):
    """Check the actual selected method against a validated bridge's reader.

Identity JSON has no format parser transformation. Its exact method declaration
belongs to the frozen workflow; this check does not infer scientific adequacy.
    """
    if type(execution_inputs) is not dict or set(execution_inputs) != _RECORD_FIELDS or execution_inputs["format"] != FORMAT:
        raise ValueError("Use a verified closed execution input record")
    receipt = _read_source(execution_inputs["transformation_receipt_ref"], workspace=_absolute(artifact_service.root).parent)
    from devharness_core.methods import MethodContract
    method = artifact_service.method(method_version_id)
    selected = MethodContract.from_dict(method["contract"]).validate_parameters(parameters)
    reader = receipt["reader_contract"]
    current_reader = _facts(artifact_service, receipt["input_binding"], _ports(receipt["method_ports"]))[4]
    if current_reader != reader:
        raise ValueError("Actual consumer interpretation or reader binding changed before dispatch")
    if reader is not None and (reader["method_version_id"] != method_version_id
            or reader["contract_sha256"] != digest(method["contract"]) or reader["parameters"] != selected):
        raise ValueError("Actual method/version/parameters differ from the representation-preserving reader contract")
    return {"method_version_id": method_version_id, "parameters_sha256": digest(selected),
            "reader_binding": "exact_registered_reader" if reader is not None else "identity_json_no_format_parser_transform"}
