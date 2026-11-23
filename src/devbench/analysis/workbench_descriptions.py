"""Bounded local descriptive evidence, without fitting or biological defaults.

Only ``InputDiscovery.describe`` dispatches the importable worker. Every read
uses the existing no-symlink descriptor boundary and supervised resource limits.
Annotation-name matches are alternatives, never inferred biological hierarchies.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass

from .workbench_request_contract import MOTOR_PROGRAM_TOPIC
from .workbench_inputs import (
    _h5ad_header,
    _hash_fd,
    _json_header,
    _open_file,
    _signature,
    _table_header,
    _unchanged,
)

FORMAT = "devbench.dataset-answer/1"
LIMITS = {
    "max_rows": 100000,
    "max_bytes": 4 * 1024**2,
    "max_categories": 64,
    "max_annotation_columns": 4,
    "max_output_bytes": 128 * 1024,
    "max_value_bytes": 512,
    "chunk_rows": 512,
}
TOPICS = {"dimensions", "columns", "layers", "annotations", "batches", "stored_results"}
EXACT_OBSERVATION_TOPICS = {
    "stored_celltypes": "celltypes",
    "stored_age": "age",
    "stored_sample_type": "sample_type",
}
TOPICS.update(EXACT_OBSERVATION_TOPICS)
TOPICS.add("stored_count_layer_audit")
TOPICS.add(MOTOR_PROGRAM_TOPIC)


def description_limits(supplied=None):
    supplied = {} if supplied is None else supplied
    if not isinstance(supplied, dict) or set(supplied) - set(LIMITS):
        raise ValueError("Use declared descriptive inspection limits")
    result = {**LIMITS, **supplied}
    if any(
        type(value) is not int or not 1 <= value <= LIMITS[key]
        for key, value in result.items()
    ):
        raise ValueError("Descriptive limits can only tighten the resource ceilings")
    return result


class _Unavailable(ValueError):
    """A supported bounded retrieval could not establish the requested fact."""


def _label(value, limits):
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    value = str(value)
    if not value.strip():
        return None
    if len(value.encode("utf-8")) > limits["max_value_bytes"] or "\x00" in value:
        raise _Unavailable("Annotation value exceeds the bounded label byte limit")
    return value


def _annotation_names(columns, topic):
    if topic == "batches":
        return [
            column
            for column in columns
            if re.sub("[^a-z0-9]", "_", column.lower()).strip("_")
            in {"batch", "batch_id", "capture_batch", "library", "library_id"}
        ]
    return [
        column
        for column in columns
        if re.search(
            r"cell.?type|annotation|cluster|class|subtype|population|step[12]",
            column,
            re.I,
        )
    ]


def _histogram_table(name, counts, missing, total, kind):
    return {
        "table_title": "Stored annotation: " + name,
        "column_name": name,
        "kind": kind,
        "columns": ["Stored label", "Observations", "Percent of all observations"],
        "rows": [
            [label, count, round(100 * count / total, 4)]
            for label, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        if total
        else [],
        "denominator": total,
        "missing_count": missing,
        "observed_count": total - missing,
        "distinct_count": len(counts),
        "truncated": False,
        "source_ref": "obs/" + name,
        "evidence_scope": "stored_annotation_values",
    }


def _h5_annotation_node(group, name):
    import h5py

    if not isinstance(group.get(name, getlink=True), h5py.HardLink):
        raise ValueError("External and symbolic HDF5 links are not permitted")
    value = group[name]
    if isinstance(value, h5py.Dataset) and (value.is_virtual or value.external):
        raise ValueError("External or virtual annotation storage is not permitted")
    return value


def _h5_annotation_columns(observed, topics, maximum):
    selected = []
    limitations, incomplete = [], False
    for topic in ("annotations", "batches", *EXACT_OBSERVATION_TOPICS):
        if topic not in topics:
            continue
        column = EXACT_OBSERVATION_TOPICS.get(topic)
        candidates = (
            [column] if column in observed["obs_columns"] else []
        ) if column else _annotation_names(observed["obs_columns"], topic)
        if not candidates:
            incomplete = True
            limitations.append(
                "No candidate stored "
                + topic
                + " columns were found; no annotation was computed."
            )
        elif len(candidates) > 1:
            limitations.append(
                "Alternative candidate columns for "
                + topic
                + ": "
                + ", ".join(candidates)
                + ". Names do not establish annotation roles or a biological hierarchy."
            )
        for name in candidates:
            if name not in [item[0] for item in selected]:
                selected.append((name, topic))
    if len(selected) > maximum:
        incomplete = True
        limitations.append(
            "Annotation column bound reached; uninspected alternatives: "
            + ", ".join(name for name, _ in selected[maximum:])
        )
    return selected[:maximum], limitations, incomplete


def _h5_annotation_values(value, limits):
    import h5py

    if isinstance(value, h5py.Dataset):
        return value, None
    if not isinstance(value, h5py.Group):
        raise _Unavailable("Stored annotation type is unavailable")
    if "codes" not in value or "categories" not in value:
        raise _Unavailable(
            "Stored annotation encoding is unavailable for this bounded reader"
        )
    codes = _h5_annotation_node(value, "codes")
    category_data = _h5_annotation_node(value, "categories")
    if not isinstance(codes, h5py.Dataset) or not isinstance(
        category_data, h5py.Dataset
    ):
        raise ValueError("Categorical annotations require dataset codes and categories")
    if (
        len(category_data.shape) != 1
        or category_data.shape[0] > limits["max_categories"]
    ):
        raise _Unavailable("Annotation categories exceed the bounded category limit")
    categories = [_label(item, limits) for item in category_data[:]]
    if codes.dtype.kind not in "iu":
        raise ValueError("Categorical annotation codes must be integers")
    return codes, categories


@dataclass
class _AnnotationByteBudget:
    """Count bytes consumed across all columns, including partial reads."""

    limit: int
    used: int = 0

    def check(self, pending=0):
        if self.used + pending > self.limit:
            raise _Unavailable("Annotation values exceed the bounded byte limit")


def _stored_category_label(item, categories):
    code = int(item)
    if code < -1 or code >= len(categories):
        raise ValueError("Stored categorical code has no category")
    return None if code == -1 else categories[code]


def _h5_annotation_counts(handle, name, total, limits, budget):
    import numpy as np

    if total > limits["max_rows"]:
        raise _Unavailable("Annotation row count exceeds the bounded row limit")
    value = _h5_annotation_node(handle["obs"], name)
    values, categories = _h5_annotation_values(value, limits)
    if categories is not None:
        budget.used += sum(len((item or "").encode()) for item in categories)
    if len(values.shape) != 1 or values.shape[0] != total:
        raise ValueError(
            "Stored annotation length disagrees with observation dimensions"
        )
    if values.dtype.kind not in "biufOSU":
        raise _Unavailable("Stored annotation dtype is unavailable")
    budget.check(values.size * values.dtype.itemsize)
    counts, missing = Counter(), 0
    for start in range(0, total, limits["chunk_rows"]):
        chunk = values[start : min(total, start + limits["chunk_rows"])]
        budget.used += int(chunk.nbytes)
        for item in chunk:
            if categories is not None:
                label = _stored_category_label(item, categories)
            else:
                scalar = item.item() if isinstance(item, np.generic) else item
                label = _label(scalar, limits)
                budget.used += len((label or "").encode())
            budget.check()
            if label is None:
                missing += 1
            else:
                counts[label] += 1
                if len(counts) > limits["max_categories"]:
                    raise _Unavailable(
                        "Annotation labels exceed the bounded category limit"
                    )
    return counts, missing


def _h5_annotations(handle, observed, topics, limits):
    total = observed["cells"]
    selected, limitations, incomplete = _h5_annotation_columns(
        observed,
        topics,
        limits["max_annotation_columns"],
    )
    tables = []
    budget = _AnnotationByteBudget(limits["max_bytes"])
    for name, topic in selected:
        try:
            counts, missing = _h5_annotation_counts(handle, name, total, limits, budget)
            tables.append(_histogram_table(name, counts, missing, total, topic))
        except _Unavailable as error:
            incomplete = True
            limitations.append(
                name + ": " + str(error) + "; no counts are reported for this column."
            )
    return tables, limitations, incomplete


def _delimited_metadata_rows(raw, suffix, limits):
    rows = csv.reader(
        io.StringIO(raw.decode("utf-8-sig")),
        delimiter="\t" if suffix == ".tsv" else ",",
        strict=True,
    )
    columns = next(rows, [])
    count = 0
    for row in rows:
        if len(row) != len(columns):
            raise ValueError("Metadata table has inconsistent row widths")
        count += 1
        if count > limits["max_rows"]:
            return (
                None,
                columns,
                [
                    "Metadata row count exceeds the bounded row limit; "
                    "total rows remain unknown."
                ],
            )
    return count, columns, []


def _metadata_rows(source, suffix, size, limits):
    if size > limits["max_bytes"]:
        return (
            None,
            [],
            [
                "Metadata content exceeds the bounded byte limit; "
                "total rows remain unknown."
            ],
        )
    raw = source.read(limits["max_bytes"] + 1)
    if len(raw) > limits["max_bytes"]:
        raise ValueError("Metadata input changed or exceeds its byte bound")
    if suffix in {".csv", ".tsv"}:
        return _delimited_metadata_rows(raw, suffix, limits)

    def invalid(_):
        raise ValueError("JSON metadata must contain finite values")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            invalid(value)
        return result

    value = json.loads(raw, parse_constant=invalid, parse_float=finite_float)
    rows = (
        value
        if isinstance(value, list)
        else value.get("records")
        if isinstance(value, dict)
        else None
    )
    if (
        isinstance(rows, list)
        and len(rows) <= limits["max_rows"]
        and all(isinstance(row, dict) for row in rows)
    ):
        return len(rows), [], []
    return (
        None,
        [],
        [
            "JSON structure does not establish an observation/feature orientation "
            "or generic record count."
        ],
    )


def _describe_source(source, suffix, filename, size, discovery_limits, limits, topics):
    from .workbench_inputs import _COMPRESSED_AXES, _MATRIX_HEADERS, _expression_header

    observed = {
        "file_type": suffix[1:],
        "cells": None,
        "genes": None,
        "obs_columns": [],
        "var_columns": [],
        "layers": [],
        "expression_values_read": False,
    }
    if suffix == ".h5ad":
        import h5py

        with h5py.File(source, "r") as handle:
            observed.update(_h5ad_header(handle, discovery_limits["max_columns"]))
            tables, limitations, incomplete = _h5_annotations(
                handle,
                observed,
                topics,
                limits,
            )
            if "stored_count_layer_audit" in topics:
                from .workbench_count_layer_audit import audit_count_layer

                try:
                    tables.append(audit_count_layer(handle, observed, limits["max_rows"]))
                except ValueError as error:
                    incomplete = True
                    limitations.append("Stored counts audit incomplete. " + str(error))
            if MOTOR_PROGRAM_TOPIC in topics:
                from .workbench_motor_programs import audit_motor_programs

                try:
                    tables.append(audit_motor_programs(handle, observed, limits["max_rows"]))
                except ValueError as error:
                    incomplete = True
                    limitations.append("Motor program screen incomplete. " + str(error))
        return observed, tables, limitations, incomplete
    if suffix in _MATRIX_HEADERS:
        observed.update(
            _expression_header(source, suffix, discovery_limits["header_bytes"])
        )
        limitations = []
        if len(observed.get("genome_headers", {})) > 1:
            limitations.append(
                "The legacy CellRanger file stores separate genome matrices. "
                "A combined shape is unknown."
            )
        return observed, [], limitations, False
    if filename.lower() in _COMPRESSED_AXES:
        observed["header_status"] = "not_applicable_headerless"
        return (
            observed,
            [],
            ["The selected 10x companion is a headerless feature or barcode file."],
            False,
        )
    header_bytes = discovery_limits["header_bytes"]
    max_columns = discovery_limits["max_columns"]
    if suffix in {".csv", ".tsv", ".csv.gz", ".tsv.gz"}:
        observed.update(
            _table_header(
                source, suffix, header_bytes, discovery_limits["max_table_columns"]
            )
        )
        if suffix.endswith(".gz"):
            return (
                observed,
                [],
                ["Only the compressed table header was inspected."],
                False,
            )
    else:
        observed.update(_json_header(source, size, header_bytes, max_columns))
    source.seek(0)
    rows, columns, limitations = _metadata_rows(source, suffix, size, limits)
    observed["metadata_rows"] = rows
    if columns:
        observed["columns"] = columns
    incomplete = bool({"annotations", "batches"} & set(topics))
    if incomplete:
        limitations.append(
            "Annotation frequency retrieval from generic metadata is unavailable "
            "without declared observation semantics."
        )
    return observed, [], limitations, incomplete


def _describe_worker(
    root,
    parts,
    root_identity,
    expected,
    expected_sha256,
    discovery_limits,
    limits,
    topics,
):
    from .resources import configure_threads
    from .workbench_inputs import _input_suffix

    configure_threads()
    suffix = _input_suffix(parts[-1])
    if suffix is None:
        raise ValueError("Input format is unavailable for bounded discovery")
    with _open_file(root, parts, root_identity) as descriptor:
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Input changed before supervised description")
        identity = _hash_fd(descriptor, discovery_limits["max_file_bytes"])
        if identity != expected_sha256:
            raise ValueError(
                "Input checksum changed; refresh before describing this source"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        with os.fdopen(os.dup(descriptor), "rb") as source:
            observed, tables, limitations, incomplete = _describe_source(
                source,
                suffix,
                parts[-1],
                expected[3],
                discovery_limits,
                limits,
                topics,
            )
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Input changed during supervised description")
    _unchanged(root, parts, root_identity, expected)
    result = make_description(
        observed,
        topics,
        filename=parts[-1],
        tables=tables,
        limitations=limitations,
        retrieval_incomplete=incomplete,
    )
    result.update(
        sha256=identity,
        inspection={
            "worker_cleanup_verified": True,
            "method": "trusted_supervised_read_only_description",
            "limits": limits,
            "analysis_executed": any(str(table.get("source_ref", "")).startswith("layers/counts") for table in tables),
            "model_sessions_attempted": 0,
        },
    )
    if len(json.dumps(result, allow_nan=False).encode()) > limits["max_output_bytes"]:
        raise ValueError("Description exceeds its bounded output byte limit")
    return result


def _add_header_tables(
    tables,
    observed,
    topics,
    observations,
    features,
    *,
    stored_matrix,
    is_h5ad,
):
    if "dimensions" in topics:
        rows = [["Observations", observations], ["Features", features]]
        if not stored_matrix:
            rows.append(["Metadata rows", observed.get("metadata_rows")])
        tables.insert(
            0,
            {
                "table_title": "Stored dimensions",
                "columns": ["Dimension", "Count"],
                "rows": rows,
                "denominator": None,
                "missing_count": None,
                "truncated": False,
                "source_ref": "matrix_shape",
            },
        )
    if "columns" in topics:
        rows = [["Observation", name] for name in observed.get("obs_columns", [])]
        rows += [["Feature", name] for name in observed.get("var_columns", [])]
        rows += [["Metadata", name] for name in observed.get("columns", [])]
        rows += [["JSON field", name] for name in observed.get("top_level_keys", [])]
        tables.append(
            {
                "table_title": "Stored metadata fields",
                "columns": ["Container", "Field"],
                "rows": rows,
                "denominator": None,
                "missing_count": None,
                "truncated": False,
                "source_ref": "metadata_headers",
            }
        )
    if "layers" in topics and is_h5ad:
        layers = ["X", *observed.get("layers", [])]
        tables.append(
            {
                "table_title": "Stored matrix locations",
                "columns": ["Location", "Measurement semantics"],
                "rows": [[name, "Unconfirmed"] for name in layers],
                "denominator": None,
                "missing_count": None,
                "truncated": False,
                "source_ref": "matrix_headers",
            }
        )


def make_description(
    observed,
    topics,
    *,
    filename,
    tables=None,
    limitations=None,
    retrieval_incomplete=False,
):
    """Format verified observations, including adapters exposing headers only."""
    tables, limitations = list(tables or []), list(limitations or [])
    observations, features = observed.get("cells"), observed.get("genes")
    is_h5ad = observed.get("file_type") in {"h5ad", ".h5ad", "anndata"}
    stored_matrix = is_h5ad or observed.get("file_type") in {
        "h5",
        "hdf5",
        "mtx",
        "mtx.gz",
    }
    if not stored_matrix:
        observations = features = None
    obs_ref = "obs/_index.shape" if is_h5ad else "matrix_shape"
    var_ref = "var/_index.shape" if is_h5ad else "matrix_shape"
    dimensions = [
        ("observations", observations, obs_ref),
        ("features", features, var_ref),
        ("metadata_rows", observed.get("metadata_rows"), "metadata_table"),
    ]
    facts = [
        {
            "name": key,
            "value": value,
            "origin": "observed" if value is not None else "unknown",
            "source_ref": ref,
        }
        for key, value, ref in dimensions
    ]
    if observations is not None and features is not None:
        message = (
            f"Dataset {filename} has {observations:,} observations "
            f"and {features:,} features in the stored matrix."
        )
    else:
        message = (
            f"Dataset {filename}: observation and feature dimensions "
            "are unknown from this metadata."
        )
    annotation_tables = [table for table in tables if table.get("column_name")]
    if annotation_tables:
        message += " " + " ".join(
            f"Stored column {table['column_name']} has {table['distinct_count']} "
            f"distinct labels; {table['missing_count']} of {table['denominator']} "
            "observations are missing labels."
            for table in annotation_tables
        )
        facts += [
            {
                "name": table["column_name"] + ".distinct_labels",
                "value": table["distinct_count"],
                "origin": "observed",
                "source_ref": table["source_ref"],
            }
            for table in annotation_tables
        ]
    _add_header_tables(
        tables,
        observed,
        topics,
        observations,
        features,
        stored_matrix=stored_matrix,
        is_h5ad=is_h5ad,
    )
    if "stored_results" in topics:
        limitations.append(
            "Stored marker/cluster result retrieval is unavailable for this input "
            "contract. No new results were computed."
        )
        message = "Stored result retrieval is unavailable for this input contract."
    if any(topic in topics for topic in ("annotations", "batches")) and not any(
        table.get("column_name") for table in tables
    ):
        limitations.append(
            "Requested annotation frequencies are unavailable; "
            "missing information is not a zero population."
        )
    audited_counts = any(str(table.get("source_ref", "")).startswith("layers/counts") for table in tables)
    limitations += [
        (
            "The feature dimension is the number of matrix columns. The analysis "
            "processed the named stored counts layer."
            if audited_counts else
            "Feature dimension is not a count of unique genes or expressed/nonzero "
            "genes; expression values were not read."
        ),
        "Stored dimensions and labels do not establish assay, independent "
        "biological units, annotation validity, raw counts or publicness.",
    ]
    header_available = observed.get(
        "header_status", "inspected"
    ) == "inspected" and any(
        key in observed
        for key in ("obs_columns", "var_columns", "columns", "top_level_keys")
    )
    available = {
        "dimensions": observations is not None
        or observed.get("metadata_rows") is not None,
        "columns": header_available,
        "layers": is_h5ad,
        "stored_results": False,
        **{
            topic: any(
                table.get("column_name") and table.get("kind") == topic
                for table in tables
            )
            for topic in ("annotations", "batches")
        },
    }
    if "columns" in topics and not header_available:
        limitations.append(
            "Requested metadata fields were not inspected within the header bound."
        )
    if "layers" in topics and not is_h5ad:
        limitations.append("Named matrix layers are unavailable for this input format.")
    descriptive_available = any(available.get(topic, False) for topic in topics)
    unavailable_topics = (
        retrieval_incomplete
        or any(not available.get(topic, False) for topic in topics)
        or "dimensions" in topics
        and (observations is None or features is None)
    )
    return {
        "format": FORMAT,
        "message": message,
        "filename": filename,
        "observed": observed,
        "tables": tables,
        "facts": facts,
        "limitations": list(dict.fromkeys(limitations)),
        "completion": "partial"
        if descriptive_available and unavailable_topics
        else "completed"
        if descriptive_available
        else "unavailable",
    }
