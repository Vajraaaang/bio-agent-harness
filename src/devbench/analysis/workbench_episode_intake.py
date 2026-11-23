"""Bounded bulk source-set intake for the existing question-to-proposal API.

Headers and aligned IDs are observations. Biological identity, dependence and
the intended contrast remain attributed declarations supplied through the chat.
No fit, conversion or method choice happens in this compiler.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import math
import os
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from devbench.core.population_data import bound_query_file
from devbench.study.evidence import _read

from .checkpoints import atomic_json, digest
from .rna_input_set_contract import (
    MAX_METADATA_BYTES,
    MAX_SOURCE_BYTES,
    source_set_identity,
    validate_source_declarations,
)
from .workbench_inputs import _COMPRESSED_AXES, _TABLE_HEADERS, _input_suffix

FORMAT = "devbench.workbench-bulk-intake/1"


def bind_inherited_count_source(inventory, counts_layer, measurement):
    """Keep the declared source separate from alternate count candidates."""
    records = [item for slot in inventory["slots"].values() for item in slot["objects"]]
    candidates = [item["path"] for item in records if item["count1_admissible"]]
    requested = (
        counts_layer if counts_layer in {"X", "raw/X"} else "layers/" + counts_layer
    )
    selected = next((item for item in records if item["path"] == requested), None)
    admitted = requested in candidates
    hold = selected["count1_hold"] if selected else "declared_count_source_absent"
    if admitted and measurement == "normalized_expression":
        admitted, hold = False, "count_source_representation_conflict"
    return {
        "requested_path": requested,
        "source_sha256": inventory["source_sha256"],
        "count1_candidate_paths": candidates,
        "requested_count1_admissible": admitted,
        "hold": hold,
    }


DECLARATION_FIELDS = {
    "biological_unit",
    "population_unit_column",
    "specimen_column",
    "library_column",
    "observation_column",
    "condition_column",
    "identity_status",
    "dependence",
    "batch_columns",
    "contrast_numerator",
    "contrast_denominator",
    "declaration_provenance",
}
_DESIGN_FIELDS = {
    "biological_unit",
    "population_unit_column",
    "observation_column",
    "condition_column",
    "contrast_numerator",
    "contrast_denominator",
}
_FACT_CLASSES = ("Observed", "Supplied", "Derived", "Inferred", "Unknown/Conflicting")
_HDF5_METADATA_BYTES = 8 * 1024 * 1024
_AXIS_FILENAMES = {"features.tsv", "genes.tsv", "barcodes.tsv"}
_CONTRAST_FIELDS = {"contrast_numerator", "contrast_denominator"}
_MAX_CONDITION_LEVELS = 64
_MAX_FORM_CONDITION_LEVELS = 256


class MatrixIntakeNeedsInformation(ValueError):
    """Bound input can be inspected further after resolving its scope."""

    def __init__(self, message, fields=()):
        super().__init__(message)
        self.fields = list(fields)


_CHOICES = {
    "biological_unit": [
        "donor",
        "organism",
        "experimental_culture",
        "donor_derived_cell_line",
    ],
    "identity_status": ["declared_from_experimental_records"],
    "dependence": ["independent", "paired"],
}
_LABELS = {
    "biological_unit": "Independent biological unit type",
    "population_unit_column": "Biological-unit identity column",
    "specimen_column": "Specimen identity column",
    "library_column": "Library identity column",
    "observation_column": "Sample identity column matching expression columns",
    "condition_column": "Condition column",
    "identity_status": "Confirm identity from experimental records",
    "dependence": "Independent or paired observations",
    "batch_columns": "Batch columns, or none",
    "contrast_numerator": "Condition to compare",
    "contrast_denominator": "Reference condition",
    "declaration_provenance": "Source of experimental identity and design declarations",
}


def validate_bulk_declarations(value):
    """Validate bounded answers without promoting suggestions into facts."""
    if not isinstance(value, dict) or set(value) - DECLARATION_FIELDS:
        raise ValueError("Use the declared bulk intake fields")
    result = deepcopy(value)
    for key, supplied in result.items():
        if isinstance(supplied, dict):
            if set(supplied) != {"value", "origin", "source"}:
                raise ValueError(
                    "Attributed declarations require value, origin and source"
                )
            if supplied["origin"] not in {
                "user_declaration",
                "metadata_declaration",
                "assistant_suggestion",
                "unknown",
            }:
                raise ValueError("Unsupported declaration origin")
            if (
                not isinstance(supplied["source"], str)
                or not 1 <= len(supplied["source"]) <= 4000
            ):
                raise ValueError("Attribute declarations to a bounded source record")
            supplied = supplied["value"]
        if key == "batch_columns" and isinstance(supplied, list):
            if len(supplied) > 8 or any(
                not isinstance(item, str) or not 1 <= len(item) <= 200
                for item in supplied
            ):
                raise ValueError("Declare at most eight bounded batch columns")
        elif (
            not isinstance(supplied, str)
            or len(supplied) > (4000 if key == "declaration_provenance" else 200)
            or "\x00" in supplied
        ):
            raise ValueError("Bulk intake declarations must be bounded text")
    return result


def _answers(supplied):
    values, attributed = {}, {}
    for key, item in validate_bulk_declarations(supplied).items():
        declaration = (
            item
            if isinstance(item, dict)
            else {
                "value": item,
                "origin": "user_declaration",
                "source": "explicit_workbench_clarification",
            }
        )
        attributed[key] = deepcopy(declaration)
        if declaration["origin"] in {"user_declaration", "metadata_declaration"}:
            value = declaration["value"]
            values[key] = value.strip() if isinstance(value, str) else value
    return values, attributed


def _field(key, columns):
    known_choices = key.endswith("_column") or (key.startswith("contrast_") and columns)
    choices = _CHOICES.get(key, columns if known_choices else None)
    return {
        "field": key,
        "scope": "bulk_episode_declarations",
        "label": _LABELS[key],
        "type": "select" if choices is not None else "text",
        "required": True,
        "options": [{"value": item, "label": item} for item in choices or []],
        "guidance": "Confirm from experimental records; column names and unique "
        "sample labels do not establish biological independence or scientific intent.",
    }


def _condition_options(columns, rows):
    options = {}
    remaining = _MAX_FORM_CONDITION_LEVELS
    for column in columns:
        labels = set()
        for row in rows:
            value = row[column]
            if _condition_label(value):
                labels.add(value)
            if len(labels) > min(remaining, _MAX_CONDITION_LEVELS):
                break
        if 2 <= len(labels) <= min(remaining, _MAX_CONDITION_LEVELS):
            options[column] = [
                {"value": value, "label": value} for value in sorted(labels)
            ]
            remaining -= len(labels)
        if remaining < 2:
            break
    return options


def _condition_label(value):
    return bool(value) and len(value) <= 200 and "\x00" not in value


def _design_clarifications(keys, columns, rows, condition, levels):
    dependencies = _condition_options(columns, rows) if keys & _CONTRAST_FIELDS else {}
    labels = levels if 2 <= len(levels) <= _MAX_CONDITION_LEVELS else []
    fields = []
    for key in sorted(keys):
        if key not in _CONTRAST_FIELDS:
            fields.append(_field(key, columns))
            continue
        field = _field(key, labels)
        if dependencies and condition not in columns:
            field["type"] = "select"
        field.update(depends_on="condition_column", options_by_condition=dependencies)
        fields.append(field)
    return fields


@contextmanager
def _bound_source(profile, maximum):
    path = Path(profile["source"])
    if (
        not path.is_absolute()
        or path.resolve() != path
        or path.is_symlink()
        or not path.is_file()
    ):
        raise ValueError(
            "Use canonical regular source paths from the selected input set"
        )
    with bound_query_file(path, profile["sha256"], maximum) as stream:
        if os.fstat(stream.fileno()).st_size != profile["size_bytes"]:
            raise ValueError("Input source byte count changed")
        yield stream


def _delimiter(path):
    return "\t" if _input_suffix(Path(path).name) in {".tsv", ".tsv.gz"} else ","


def _is_delimited_table(path):
    name = path.name.lower()
    return (
        _input_suffix(name) in _TABLE_HEADERS
        and name not in _AXIS_FILENAMES | _COMPRESSED_AXES
    )


@contextmanager
def _tabular_stream(profile, maximum):
    with _bound_source(profile, maximum) as stream:
        if profile["source"].lower().endswith(".gz"):
            with gzip.GzipFile(fileobj=stream) as decoded:
                yield decoded
        else:
            yield stream


def _bounded_text_lines(stream, maximum):
    remaining = maximum
    encoding = "utf-8-sig"
    while line := stream.readline(remaining + 1):
        remaining -= len(line)
        if remaining < 0:
            raise ValueError("Expression table exceeds the bounded decoded byte limit")
        yield line.decode(encoding)
        encoding = "utf-8"


@contextmanager
def _expression_rows(profile, maximum=MAX_SOURCE_BYTES):
    with _tabular_stream(profile, MAX_SOURCE_BYTES) as stream:
        yield csv.reader(
            _bounded_text_lines(stream, maximum),
            delimiter=_delimiter(profile["source"]),
            strict=True,
        )


def _metadata(profile):
    with _tabular_stream(profile, MAX_METADATA_BYTES) as stream:
        content = stream.read(MAX_METADATA_BYTES + 1)
    if len(content) > MAX_METADATA_BYTES:
        raise ValueError("Metadata exceeds its bounded intake limit")
    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8-sig"), newline=""),
        delimiter=_delimiter(profile["source"]),
        strict=True,
    )
    columns = reader.fieldnames or []
    if (
        not columns
        or len(columns) > 128
        or len(set(columns)) != len(columns)
        or any(not name or len(name) > 200 for name in columns)
    ):
        raise ValueError("Metadata requires distinct bounded column names")
    rows = []
    for row in reader:
        if (
            len(rows) >= 100_000
            or None in row
            or any(value is None or len(value) > 4000 for value in row.values())
        ):
            raise ValueError("Metadata rows exceed bounds or disagree with the header")
        rows.append(row)
    if not rows:
        raise ValueError("Metadata must contain observations")
    return columns, rows


def _expression_axis(profile):
    # Streaming labels/dimensions only; numerical validation belongs to the
    # existing supervised proposal inspector and supported observed-data loader.
    from devbench.study.biology_input_limits import PUBLIC_MAX_BULK_FEATURES

    with _expression_rows(profile) as reader:
        header = next(reader, [])
        if len(header) < 2 or header[0] not in {"gene_id", "gene", "gene_name", ""}:
            raise ValueError(
                "Expression table requires feature rows and named sample columns"
            )
        samples = header[1:]
        if (
            len(samples) > 100_000
            or len(set(samples)) != len(samples)
            or any(not value or len(value) > 200 for value in samples)
        ):
            raise ValueError("Expression sample IDs must be distinct bounded labels")
        genes = set()
        for row in reader:
            if (
                len(row) != len(header)
                or not row[0]
                or len(row[0]) > 200
                or row[0] in genes
                or len(genes) >= PUBLIC_MAX_BULK_FEATURES
            ):
                raise ValueError(
                    "Expression dimensions or feature IDs violate "
                    "the bounded table contract"
                )
            genes.add(row[0])
    if not genes:
        raise ValueError("Expression CSV contains no features")
    return samples, len(genes)


def _intake_profiles(record):
    if record.get("sources"):
        profiles = record["source_profiles"]
        identity = source_set_identity(profiles)
        if record["sources"] != [item["source"] for item in profiles]:
            raise ValueError(
                "Ordered source paths disagree with their inspected snapshots"
            )
        if record.get("input_set_sha256", identity) != identity:
            raise ValueError("Selected input-set identity changed")
        supplied = record.get("source_declarations", [{} for _ in profiles])
    else:
        profiles = [record["profile"]]
        if profiles[0]["source"] != record["source"]:
            raise ValueError(
                "Selected source path disagrees with its inspected snapshot"
            )
        identity = profiles[0]["sha256"]
        supplied = [record.get("declarations", {})]
    if len(supplied) != len(profiles):
        raise ValueError("Source declarations must follow the inspected source order")
    declarations = [
        validate_source_declarations(value, profile.get("observed"))
        for profile, value in zip(profiles, supplied, strict=True)
    ]
    return profiles, declarations, identity


def _matrix_file(path):
    return path.name.lower().endswith((".h5ad", ".h5", ".hdf5", ".mtx", ".mtx.gz"))


def _looks_like_expression(profile):
    path = Path(profile["source"])
    if _matrix_file(path):
        return True
    if not _is_delimited_table(path):
        return False
    with _expression_rows(profile, 256 * 1024) as rows:
        header = next(rows, [])
    return len(header) >= 2 and header[0] in {"gene_id", "gene", "gene_name"}


def _intake_roles(profiles, declarations):
    indices, inferred = {}, {}
    for index, declaration in enumerate(declarations):
        role = declaration.get("role")
        if role in {"expression", "metadata", "annotations"}:
            if role in indices:
                raise ValueError(
                    "Bulk intake requires one source for each selected role"
                )
            indices[role] = index
    if "expression" not in indices:
        candidates = [
            i
            for i, profile in enumerate(profiles)
            if not declarations[i].get("role") and _looks_like_expression(profile)
        ]
        if len(candidates) != 1:
            raise MatrixIntakeNeedsInformation("Select the exact expression source")
        indices["expression"] = inferred["expression"] = candidates[0]
    return indices, inferred


def _h5ad_observations(obs, names, samples_n):
    import h5py

    from devbench.core.population_data import _annotation, _node

    index_name = obs.attrs.get("_index", "_index")
    if isinstance(index_name, bytes):
        index_name = index_name.decode("utf-8")
    samples, used = _annotation(_node(obs, index_name), samples_n, _HDF5_METADATA_BYTES)
    if (
        any(not isinstance(value, str) for value in samples)
        or len(set(samples)) != samples_n
    ):
        raise ValueError("AnnData observation IDs must be unique nonmissing labels")
    remaining = _HDF5_METADATA_BYTES - used
    columns = {index_name: list(samples)}
    omitted = []
    for name in names:
        element = _node(obs, name)
        physical = element
        if isinstance(element, h5py.Group) and "categories" in element:
            physical = _node(element, "categories")
        if isinstance(physical, h5py.Dataset) and physical.dtype.kind in "biuf":
            if physical is not element or physical.shape != (samples_n,):
                omitted.append(name)
                continue
            size = physical.size * physical.dtype.itemsize
            if size > remaining:
                raise ValueError("AnnData metadata exceeds the bounded intake limit")
            labels = [
                str(value) if math.isfinite(value) else "" for value in physical[:]
            ]
            consumed = size + sum(len(value.encode("utf-8")) for value in labels)
            if consumed > remaining:
                raise ValueError("AnnData metadata exceeds the decoded intake limit")
        else:
            labels, consumed = _annotation(element, samples_n, remaining)
            labels = [value if isinstance(value, str) else "" for value in labels]
        columns[name] = labels
        remaining -= consumed
    return samples, index_name, columns, omitted


def _h5ad_axes(profile, layer):
    import h5py

    from devbench.core.population_data import _node

    from .workbench_inputs import _h5ad_header

    with (
        _bound_source(profile, MAX_SOURCE_BYTES) as stream,
        h5py.File(stream, "r") as handle,
    ):
        header = _h5ad_header(handle, 128)
        samples_n, genes = header["cells"], header["genes"]
        if samples_n > 100_000:
            raise ValueError(
                "AnnData observation axis exceeds the metadata intake limit"
            )
        if layer is not None:
            selected = _node(_node(handle, "layers"), layer)
            shape = (
                selected.shape
                if isinstance(selected, h5py.Dataset)
                else selected.attrs.get("shape", ())
            )
            if tuple(shape) != (samples_n, genes):
                raise ValueError(
                    "Declared measurement layer disagrees with the bound axes"
                )
        samples, index_name, columns, omitted = _h5ad_observations(
            _node(handle, "obs"),
            header["obs_columns"],
            samples_n,
        )
    rows = [
        dict(zip(columns, values, strict=True))
        for values in zip(*columns.values(), strict=True)
    ]
    return list(samples), genes, index_name, list(columns), rows, omitted


def _hdf5_axes(profile):
    import h5py

    from devbench.core.population_data import _node

    from .workbench_inputs import _hdf5_expression_header

    with (
        _bound_source(profile, MAX_SOURCE_BYTES) as stream,
        h5py.File(stream, "r") as handle,
    ):
        shape = _hdf5_expression_header(handle)
        remaining, axes = _HDF5_METADATA_BYTES, []
        for name in ("sample_names", "gene_names"):
            dataset = _node(handle, name)
            if h5py.check_string_dtype(dataset.dtype) is None:
                raise ValueError("HDF5 sample and feature IDs must be text")
            label_bytes = 256 if dataset.dtype.kind == "O" else dataset.dtype.itemsize
            bound = dataset.size * label_bytes
            if bound > remaining:
                raise ValueError("HDF5 labels exceed the bounded intake limit")
            labels = list(dataset.asstr()[:])
            if len(set(labels)) != len(labels) or any(
                not item.strip() or len(item) > 200 or "\x00" in item for item in labels
            ):
                raise ValueError(
                    "HDF5 sample and feature IDs must be unique bounded labels"
                )
            remaining -= max(bound, sum(len(item.encode("utf-8")) for item in labels))
            if remaining < 0:
                raise ValueError("HDF5 labels exceed the decoded intake limit")
            axes.append(labels)
    return axes[0], shape["genes"]


def _tenx_axes(profile, profiles):
    from devharness_core.storage import ChunkedExpressionStore, tenx_mtx_paths

    path = Path(profile["source"])

    bound = {Path(item["source"]): item for item in profiles}
    members = tenx_mtx_paths(path)
    if any(member not in bound for member in members):
        raise MatrixIntakeNeedsInformation(
            "Select both 10x feature and barcode companion sources"
        )
    matrix_members = [bound[member] for member in members]
    for item in matrix_members:
        with _bound_source(item, MAX_SOURCE_BYTES):
            pass
    with ChunkedExpressionStore(path) as store:
        samples, genes = list(store.sample_names), store.shape[0]
    for item in matrix_members:
        with _bound_source(item, MAX_SOURCE_BYTES):
            pass
    return samples, genes


def _exact_columns(samples, columns, rows):
    if len(rows) != len(samples):
        return []
    expected = set(samples)
    return [name for name in columns if {row[name] for row in rows} == expected]


def _observation_metadata(profiles, declarations, indices, inferred, samples, embedded):
    if "metadata" in indices:
        return _metadata(profiles[indices["metadata"]]), None
    if embedded is not None:
        return embedded
    possible = [
        (i, _metadata(profile))
        for i, profile in enumerate(profiles)
        if i != indices["expression"]
        and not declarations[i].get("role")
        and _is_delimited_table(Path(profile["source"]))
    ]
    matches = [(i, table) for i, table in possible if _exact_columns(samples, *table)]
    if possible and len(matches) != 1:
        raise MatrixIntakeNeedsInformation(
            "Select the exact sample metadata source and observation column"
        )
    if not matches:
        return ([], []), None
    index, table = matches[0]
    indices["metadata"] = inferred["metadata"] = index
    return table, None


def _read_matrix_axes(path, expression, profiles, layer):
    suffix = _input_suffix(path.name)
    if suffix == ".h5ad":
        samples, genes, index, columns, rows, omitted = _h5ad_axes(expression, layer)
        return samples, genes, ((columns, rows), index), omitted
    if layer is not None:
        raise ValueError("A named measurement layer requires AnnData")
    if _is_delimited_table(path):
        samples, genes = _expression_axis(expression)
    elif suffix in {".h5", ".hdf5"}:
        samples, genes = _hdf5_axes(expression)
    elif suffix in {".mtx", ".mtx.gz"}:
        samples, genes = _tenx_axes(expression, profiles)
    else:
        raise ValueError("Unsupported original expression format for Workbench intake")
    return samples, genes, None, []


def _align_observations(samples, columns, rows, observation):
    if not rows:
        return rows, {}
    exact = _exact_columns(samples, columns, rows)
    equivalent = len(exact) > 1 and all(
        all(row[name] == row[exact[0]] for name in exact[1:]) for row in rows
    )
    if observation is None:
        if len(exact) != 1 and not equivalent:
            raise MatrixIntakeNeedsInformation(
                "Confirm the metadata observation column that uniquely matches "
                "the expression sample IDs",
                [_field("observation_column", exact or columns)],
            )
        observation = "sample_id" if "sample_id" in exact else exact[0]
    if observation not in columns:
        raise MatrixIntakeNeedsInformation(
            "The declared observation column is absent",
            [_field("observation_column", exact or columns)],
        )
    if observation not in exact:
        raise ValueError(
            "Expression and metadata require an exact bijective sample-ID match"
        )
    by_id = {row[observation]: row for row in rows}
    rows = [by_id[sample] for sample in samples]
    derived = {
        "observation_column": observation,
        "sample_alignment": "exact_bijective_ids",
    }
    if equivalent:
        derived["equivalent_observation_columns"] = exact
    return rows, derived


def inspect_matrix_intake(record):
    """Inspect exact input axes and attributed facts for a bounded local audit."""
    profiles, declarations, identity = _intake_profiles(record)
    indices, inferred = _intake_roles(profiles, declarations)
    expression = profiles[indices["expression"]]
    path = Path(expression["source"])
    context = record.get("scientific_context", {})
    declared = declarations[indices["expression"]]
    supplied_layers = {
        value
        for value in (declared.get("counts_layer"), context.get("counts_layer"))
        if value
    }
    if len(supplied_layers) > 1:
        raise MatrixIntakeNeedsInformation("Measurement layer declarations conflict")
    layer = next(iter(supplied_layers), None)
    layer = None if layer == "X" else layer
    samples, genes, embedded, omitted = _read_matrix_axes(
        path,
        expression,
        profiles,
        layer,
    )
    (columns, rows), embedded_index = _observation_metadata(
        profiles,
        declarations,
        indices,
        inferred,
        samples,
        embedded,
    )
    values, attributed = _answers(context.get("bulk_episode_declarations", {}))
    rows, derived = _align_observations(
        samples,
        columns,
        rows,
        values.get("observation_column") or embedded_index,
    )
    observation = derived.get("observation_column")
    file_type = _input_suffix(path.name)
    observed = {
        "samples": len(samples),
        "genes": genes,
        "metadata_columns": columns,
        "input_set_sha256": identity,
        "expression_values_validated": False,
        "expression_file_type": file_type,
    }
    if path.suffix.lower() == ".h5ad":
        observed["matrix_location"] = "X" if layer is None else f"layers/{layer}"
    facts = {name: {} for name in _FACT_CLASSES}
    facts["Observed"] = observed
    facts["Derived"] = derived
    facts["Supplied"] = {
        "source_declarations": declarations,
        "design": {name: attributed[name] for name in values},
    }
    if context.get("counts_layer"):
        facts["Supplied"]["counts_layer"] = context["counts_layer"]
    facts["Inferred"] = {
        "source_roles": inferred,
        "design_suggestions": {
            name: item
            for name, item in attributed.items()
            if item["origin"] == "assistant_suggestion"
        },
    }
    addressed = {"observation_column"} if observation else set()
    facts["Unknown/Conflicting"] = {
        name: "Requires confirmation from the experimental record"
        for name in sorted(DECLARATION_FIELDS - values.keys() - addressed)
    }
    measurement = declared.get("measurement")
    if not measurement:
        facts["Unknown/Conflicting"]["measurement"] = (
            "Measurement provenance was not supplied"
        )
    if omitted:
        facts["Unknown/Conflicting"]["unavailable_metadata_columns"] = omitted
    return {
        "expression_path": path,
        "layer": layer,
        "source_roles": indices,
        "metadata_columns": columns,
        "metadata_rows": rows,
        "observation_column": observation,
        "sample_ids": samples,
        "gene_count": genes,
        "measurement": measurement or None,
        "fact_provenance": facts,
    }


def _legacy_bulk_expression(selected, roles, declarations):
    if "metadata" not in selected or any(
        selected[role].suffix.lower() != ".csv" for role in ("expression", "metadata")
    ):
        raise ValueError(
            "Descriptive intake is available for this matrix. "
            "The legacy bulk episode requires original CSV expression and metadata."
        )
    if len(roles) != len(declarations) or any(
        declarations[index].get("role") != role for role, index in roles.items()
    ):
        raise ValueError("The legacy bulk episode requires exact declared input roles")
    expression = declarations[roles["expression"]]
    if (
        expression.get("assay") != "bulk"
        or expression.get("measurement")
        not in {"non_umi_counts", "umi_counts", "normalized_expression"}
        or not expression.get("count_provenance")
    ):
        raise ValueError(
            "Declare bulk assay, observed measurement and its provenance "
            "for the expression source"
        )
    if "annotations" in selected and selected["annotations"].suffix.lower() != ".json":
        raise ValueError(
            "The episode annotation transport requires an admitted JSON mapping"
        )
    return expression


def _bulk_design_requirements(values, columns, rows, unknown_facts):
    condition = values.get("condition_column")
    levels = (
        sorted({row[condition] for row in rows if _condition_label(row[condition])})
        if condition in columns
        else []
    )
    missing = {
        key for key in DECLARATION_FIELDS if key not in values or values[key] == ""
    }
    missing.update(
        key
        for key, options in _CHOICES.items()
        if key in values and values[key] not in options
    )
    missing.update(
        key
        for key, value in values.items()
        if key.endswith("_column") and value not in columns
    )
    missing.update(key for key in _CONTRAST_FIELDS if values.get(key) not in levels)
    if values.get("contrast_numerator") == values.get("contrast_denominator"):
        missing.update(_CONTRAST_FIELDS)
    if missing:
        for key in missing:
            unknown_facts.setdefault(
                key,
                "Declaration is missing or conflicts with the "
                "observed or supported values",
            )
        current = missing & _DESIGN_FIELDS or missing
        return None, _design_clarifications(current, columns, rows, condition, levels)

    batches = values["batch_columns"]
    if isinstance(batches, str):
        batches = [name.strip() for name in batches.split(",")]
    if batches == ["none"]:
        batches = []
    if len(set(batches)) != len(batches) or any(
        name not in columns for name in batches
    ):
        unknown_facts["batch_columns"] = (
            "Declared batch columns disagree with the metadata"
        )
        return None, [_field("batch_columns", columns)]
    return batches, []


def _select_bulk_annotation(output, selected):
    if (
        not output.is_absolute()
        or output.resolve() != output
        or output.is_symlink()
        or any(
            output == path or output.is_relative_to(path.parent)
            for path in selected.values()
        )
    ):
        raise ValueError(
            "Compiled intake output must be canonical service state disjoint "
            "from selected input directories"
        )
    if "annotations" in selected:
        return "supplied_unverified"
    empty = output / "empty-annotations.json"
    if empty.exists() and (empty.is_symlink() or _read(empty, 100) != b"{}\n"):
        raise ValueError("Owned empty annotation artifact changed")
    selected["annotations"] = empty
    return "unknown_empty_artifact"


def _bulk_task(values, batches, expression, annotation_status, attributed):
    population_unit = values["population_unit_column"]
    dependence = values["dependence"]
    continuous = expression["measurement"] == "normalized_expression"
    return {
        "measurement": "continuous" if continuous else "raw_counts",
        "observation_id_label": values["observation_column"],
        "unit_hierarchy": {
            "population_unit": values["biological_unit"],
            "population_unit_label": population_unit,
            "specimen_label": values["specimen_column"],
            "library_label": values["library_column"],
            "identity_status": values["identity_status"],
        },
        "analysis_columns": {
            "condition": values["condition_column"],
            "population_unit": population_unit,
            "batches": batches,
            "blocking": [population_unit] if dependence == "paired" else [],
        },
        "context": {
            "requested_condition_contrast": {
                "numerator": values["contrast_numerator"],
                "denominator": values["contrast_denominator"],
            },
            "annotation_status": annotation_status,
            "intake_declarations": attributed,
            "measurement_provenance": expression["count_provenance"],
            "dependence": dependence,
        },
    }


def compile_bulk_intake(record, *, output_dir):
    """Compile an ordered, already registered source set; return a typed gate.

    ``output_dir`` is owned service state, disjoint from every selected source.
    A ready result is input for ``episode_planning.propose_analysis`` and is
    neither an accepted proposal nor execution authorization.
    """
    result = {
        "format": FORMAT,
        "status": "unsupported",
        "clarification_fields": [],
        "dataset": None,
    }
    try:
        profiles, declarations, identity = _intake_profiles(record)
        intake = inspect_matrix_intake(record)
        facts = intake["fact_provenance"]
        roles = intake["source_roles"]
        selected = {
            role: Path(profiles[index]["source"]) for role, index in roles.items()
        }
        result.update(
            fact_provenance=facts,
            observed={**facts["Observed"], **facts["Derived"]},
        )
        expression = _legacy_bulk_expression(selected, roles, declarations)
        context = record.get("scientific_context", {})
        values, attributed = _answers(context.get("bulk_episode_declarations", {}))
        values["observation_column"] = intake["observation_column"]
        result["attributed_declarations"] = attributed
        batches, clarification = _bulk_design_requirements(
            values,
            intake["metadata_columns"],
            intake["metadata_rows"],
            facts["Unknown/Conflicting"],
        )
        if clarification:
            result.update(status="clarification", clarification_fields=clarification)
            return result

        output = Path(output_dir)
        annotation_status = _select_bulk_annotation(output, selected)
        task = _bulk_task(values, batches, expression, annotation_status, attributed)
        # Pure validation; no question targets or observed gene IDs are invented.
        from .episode_planning import _dataset
        from .episodes import EpisodeLimits

        input_kind = (
            "normalized_expression"
            if task["measurement"] == "continuous"
            else "count_matrix"
        )
        dataset = {
            "assay": "bulk",
            "input_kind": input_kind,
            "public_task": task,
            "inputs": {key: str(path) for key, path in selected.items()},
        }
        _dataset(record["question"], dataset, EpisodeLimits(), {}, "local_supervised")
        # Recheck all selected snapshots after bounded inspection, before publication.
        for profile, declaration in zip(profiles, declarations, strict=True):
            maximum = (
                MAX_SOURCE_BYTES
                if declaration["role"] == "expression"
                else MAX_METADATA_BYTES
            )
            with _bound_source(profile, maximum):
                pass
        annotation = selected["annotations"]
        if annotation_status == "unknown_empty_artifact":
            output.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not annotation.exists():
                atomic_json(annotation, {})
        binding = {
            "investigation_id": record["id"],
            "thread_id": record["thread_id"],
            "revision": record["revision"],
            "question_sha256": digest(record["question"]),
            "source_order": [profile["source"] for profile in profiles],
            "input_set_sha256": identity,
            "sources": [
                {key: profile[key] for key in ("source", "sha256", "size_bytes")}
                for profile in profiles
            ],
            "source_declarations_sha256": digest(declarations),
            "declarations_sha256": digest(attributed),
            "annotation": {
                "source": str(annotation),
                "sha256": hashlib.sha256(
                    _read(annotation, MAX_METADATA_BYTES)
                ).hexdigest(),
                "status": annotation_status,
            },
            "contrast": task["context"]["requested_condition_contrast"],
        }
        result.update(
            status="ready",
            question=record["question"],
            dataset=dataset,
            binding=binding,
        )
    except MatrixIntakeNeedsInformation as error:
        facts = result.setdefault(
            "fact_provenance",
            {name: {} for name in _FACT_CLASSES},
        )
        facts["Unknown/Conflicting"]["input_binding"] = str(error)[:2000]
        result.update(
            status="clarification",
            clarification_fields=error.fields,
            reason=str(error)[:2000],
        )
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, csv.Error) as error:
        result.update(status="unsupported", dataset=None, reason=str(error)[:2000])
    return result


def propose_bulk_intake(record, *, output_dir, selector, **proposal_options):
    """Use the existing supervised planner only after intake declarations exist."""
    result = compile_bulk_intake(record, output_dir=output_dir)
    if result["status"] == "ready":
        from .episode_planning import propose_analysis

        result["planning"] = propose_analysis(
            Path(output_dir) / "planning",
            question=result["question"],
            dataset=result["dataset"],
            selector=selector,
            **proposal_options,
        )
    return result
