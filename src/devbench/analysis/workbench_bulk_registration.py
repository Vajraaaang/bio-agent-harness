"""Register declared bulk inputs and an openly identified candidate family."""

import csv
import io
import math
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from shutil import copyfileobj
from tempfile import TemporaryDirectory

from devbench.core.population_data import _node
from devbench.core.protected_partitions import PartitionVault
from devbench.core.publication_families import FamilySpec
from devbench.platform.adaptive_workflow_state import ArtifactVersion
from devharness_core.provenance import canonical_json

from .checkpoints import digest
from .rna_input_set_contract import (
    COUNT_MEASUREMENTS,
    MAX_METADATA_BYTES,
    MAX_SOURCE_BYTES,
)
from .workbench_episode_intake import (
    MatrixIntakeNeedsInformation,
    _answers,
    _bound_source,
    _bulk_design_requirements,
    _expression_rows,
    _intake_profiles,
    _is_delimited_table,
    inspect_matrix_intake,
)

_UNIT_COLUMNS = ("population_unit_column", "specimen_column", "library_column")
_COLUMN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}")
_LEVEL = re.compile(r"[A-Za-z0-9_.-]{1,160}")
_MAX_COUNT = 2**53 - 1
_MAX_MOUNTED_METADATA = 5_000_000


def _explicit_bulk_sources(record):
    if len(record.get("sources") or []) != 2:
        return None
    profiles, declarations, _ = _intake_profiles(record)
    ordered = {item.get("role"): i for i, item in enumerate(declarations)}
    if ordered.keys() != {"expression", "metadata"}:
        return None
    expression, metadata = (
        profiles[ordered[role]] for role in ("expression", "metadata")
    )
    declared = declarations[ordered["expression"]]
    measurement = declared.get("measurement")
    matrix = Path(expression["source"])
    if (
        declared.get("assay") != "bulk"
        or measurement not in COUNT_MEASUREMENTS | {"normalized_expression"}
        or not (declared.get("count_provenance") or "").strip()
        or expression["sha256"] == metadata["sha256"]
        or expression.get("observed", {}).get("matrix_layout")
        in {"10x_cellranger", "10x_cellranger_legacy"}
        or not (
            _is_delimited_table(matrix) or matrix.suffix.lower() in {".h5", ".hdf5"}
        )
        or not _is_delimited_table(Path(metadata["source"]))
    ):
        return None
    return expression, metadata, measurement


def _usable_label(value):
    return (
        bool(value)
        and len(value) <= 200
        and value == value.strip()
        and "\x00" not in value
    )


def _bulk_design(record, intake):
    context = record.get("scientific_context", {})
    values, attributed = _answers(context.get("bulk_episode_declarations", {}))
    columns, rows = intake["metadata_columns"], intake["metadata_rows"]
    batches, clarification = _bulk_design_requirements(values, columns, rows, {})
    if clarification or values["observation_column"] != intake["observation_column"]:
        return None
    condition = values["condition_column"]
    unit, specimen, library = (values[name] for name in _UNIT_COLUMNS)
    unit_roles = (unit, specimen, library)
    observation_roles = {"sample_id", values["observation_column"]}
    design_roles = (condition, *batches)
    roles = (*design_roles, *unit_roles)
    if (
        len(set(design_roles)) != len(design_roles)
        or set(design_roles) & (observation_roles | set(unit_roles))
        or (values["dependence"] != "independent" and (
            len(set(unit_roles)) != len(unit_roles)
            or observation_roles & set(unit_roles)
        ))
        or not all(map(_COLUMN.fullmatch, roles))
        or not all(_usable_label(row[name]) for name in roles for row in rows)
        or not all(
            _LEVEL.fullmatch(values[name])
            for name in ("contrast_numerator", "contrast_denominator")
        )
        or not _consistent_units(values["dependence"], rows, unit, specimen, library)
    ):
        return None
    return values, attributed


def _consistent_units(dependence, rows, unit, specimen, library):
    donors = {row[unit] for row in rows}
    specimen_donors = {(row[specimen], row[unit]) for row in rows}
    library_specimens = {(row[library], row[specimen]) for row in rows}
    return (
        (dependence != "independent" or len(donors) == len(rows))
        and len(specimen_donors) == len({row[specimen] for row in rows})
        and len(library_specimens) == len({row[library] for row in rows})
    )


def mounted_design_columns(values):
    """Keep aliases explicit when independent roles share a recorded column."""
    unit_roles = [values[name] for name in _UNIT_COLUMNS]
    observation = values["observation_column"]
    if len(set(unit_roles)) == len(unit_roles) and not (
        {observation, "sample_id"} & set(unit_roles)
    ):
        return dict(zip(_UNIT_COLUMNS, unit_roles, strict=True))
    return {name: "devbench_" + name.removesuffix("_column") for name in _UNIT_COLUMNS}


def _metadata_payload(intake, values):
    observation = intake["observation_column"]
    columns = [
        column for column in intake["metadata_columns"]
        if column not in {observation, "sample_id"}
    ]
    mounted = mounted_design_columns(values)
    aliases = {
        output: values[role] for role, output in mounted.items()
        if output != values[role]
    }
    if set(columns) & aliases.keys():
        raise ValueError("Mounted bulk metadata aliases collide with declared columns")
    rows = intake["metadata_rows"]
    if any("sample_id" in row and row["sample_id"] != row[observation] for row in rows):
        raise ValueError("Declared observation IDs conflict with the stored sample_id")
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(["sample_id", *columns, *aliases])
    writer.writerows([
        row[name] for name in (observation, *columns, *aliases.values())
    ] for row in rows)
    raw = target.getvalue().encode()
    if len(raw) > _MAX_MOUNTED_METADATA:
        raise ValueError("Converted metadata exceeds the mounted input byte bound")
    return raw


def _number(value, *, continuous):
    if len(value) > 64:
        raise ValueError("Delimited expression value exceeds the numeric token bound")
    if continuous:
        try:
            parsed = float(value)
        except ValueError as error:
            raise ValueError(
                "Declared continuous expression must use finite values"
            ) from error
        if math.isfinite(parsed):
            return parsed
        raise ValueError("Declared continuous expression must use finite values")
    try:
        exact = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(
            "Declared raw counts must be exact nonnegative integers"
        ) from error
    if (
        not exact.is_finite()
        or not 0 <= exact <= _MAX_COUNT
        or exact != exact.to_integral_value()
    ):
        raise ValueError("Declared raw counts must be exact nonnegative integers")
    return int(exact)


def _write_axis(handle, name, labels):
    import h5py

    encoded = [label.encode("utf-8") for label in labels]
    width = max(map(len, encoded))
    declared = h5py.string_dtype("utf-8", length=width)
    handle.create_dataset(name, data=encoded, dtype=declared, track_times=False)


def _stage_delimited_matrix(profile, target, intake, *, continuous):
    import h5py

    samples, size = intake["sample_ids"], intake["gene_count"]
    if size * len(samples) * 8 > MAX_SOURCE_BYTES:
        raise ValueError("Converted expression exceeds the mounted input byte bound")
    with _expression_rows(profile) as rows, h5py.File(target, "x") as handle:
        header = next(rows)
        if header[1:] != samples:
            raise ValueError("Expression sample axis changed before conversion")
        matrix = handle.create_dataset(
            "expression",
            (size, len(samples)),
            dtype="float64" if continuous else "int64",
            track_times=False,
        )
        features = []
        for index, row in enumerate(rows):
            features.append(row[0])
            matrix[index] = [_number(value, continuous=continuous) for value in row[1:]]
        if len(features) != size:
            raise ValueError("Expression feature axis changed before conversion")
        _write_axis(handle, "gene_names", features)
        _write_axis(handle, "sample_names", samples)
    return features


def _stage_native_matrix(profile, target, intake, *, continuous):
    import h5py

    with _bound_source(profile, MAX_SOURCE_BYTES) as source:
        with h5py.File(source, "r") as handle:
            if len(handle) != 3:
                raise ValueError(
                    "Mounted HDF5 requires only its matrix and axis datasets"
                )
            matrix = _node(handle, "expression")
            dtype = "iuf" if continuous else "iu"
            if matrix.dtype.kind not in dtype or matrix.nbytes > MAX_SOURCE_BYTES:
                raise ValueError(
                    "Mounted HDF5 layout conflicts with declared measurement"
                )
            features = _node(handle, "gene_names").asstr()[:].tolist()
            samples = _node(handle, "sample_names").asstr()[:].tolist()
            if samples != intake["sample_ids"] or len(features) != intake["gene_count"]:
                raise ValueError("Mounted HDF5 axes differ from the inspected source")
        source.seek(0)
        with target.open("xb") as staged:
            copyfileobj(source, staged, 1024 * 1024)
    return features


def _membership(values, attributed, intake, roots):
    unit, specimen, library = (values[name] for name in _UNIT_COLUMNS)
    observation = values["observation_column"]
    return digest(
        {
            "source_roots": roots,
            "declarations": attributed,
            "sample_membership": [
                [row[name] for name in (observation, unit, specimen, library)]
                for row in intake["metadata_rows"]
            ],
        }
    )


def register_governed_bulk_inputs(
    record,
    training_vault: PartitionVault,
) -> tuple[tuple[ArtifactVersion, ...], tuple[str, ...]] | None:
    """Retain exact mounted inputs after declarations, before any run or review."""
    sources = _explicit_bulk_sources(record)
    if sources is None:
        return None
    expression, metadata, measurement = sources
    try:
        intake = inspect_matrix_intake(record)
    except MatrixIntakeNeedsInformation:
        return None
    design = _bulk_design(record, intake)
    if design is None:
        return None
    values, attributed = design
    roots = (expression["sha256"], metadata["sha256"])
    membership = _membership(values, attributed, intake, roots)
    continuous = measurement == "normalized_expression"
    representation = "continuous_h5" if continuous else "raw_counts_h5"
    transport = (
        _stage_delimited_matrix
        if _is_delimited_table(Path(expression["source"]))
        else _stage_native_matrix
    )
    payload = _metadata_payload(intake, values)
    with TemporaryDirectory(prefix="bulk-input-", dir=training_vault.root) as staging:
        target = Path(staging) / "expression.h5"
        features = transport(expression, target, intake, continuous=continuous)
        if (
            target.stat().st_size > MAX_SOURCE_BYTES
            or not all(map(_usable_label, features))
            or not all(map(_usable_label, intake["sample_ids"]))
        ):
            raise ValueError(
                "Mounted expression exceeds its bounded axis or file limits"
            )
        family = FamilySpec(
            family_id="workbench-candidate-all-observed-features",
            population="bulk",
            numerator=values["contrast_numerator"],
            denominator=values["contrast_denominator"],
            feature_ids=tuple(features),
        )
        # Recheck the original metadata identity after all bounded local staging.
        with _bound_source(metadata, MAX_METADATA_BYTES):
            pass
        expression_sha = training_vault.put_file(target)
    artifacts = (
        (
            "expression",
            "continuous_matrix" if continuous else "counts",
            representation,
            expression_sha,
            (measurement,),
        ),
        ("metadata", "table", "metadata_csv", training_vault.put(payload), ()),
        (
            "candidate-family",
            "table",
            "family_spec_json",
            training_vault.put(canonical_json(family.model_dump(mode="json"))),
            (),
        ),
    )
    return tuple(
        ArtifactVersion(
            version_id=f"workbench-bulk-{role}-{sha[:22]}-{membership[:16]}",
            sha256=sha,
            kind=kind,
            representation=format_name,
            biological_unit=values["biological_unit"],
            unit_membership_sha256=membership,
            measurement_history=history,
            permitted_uses=("checked_observation", "admitted_research_inference"),
        )
        for role, kind, format_name, sha, history in artifacts
    ), roots
