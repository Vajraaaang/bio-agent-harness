"""Stage measured per-unit cell counts from a bound H5AD observation axis."""

from collections import Counter
from hashlib import sha256
from pathlib import Path

import h5py

from rna_kernels.canonical import canonical_json

from .population_data import _annotation, _node, bound_query_file
from .scrna_composition import (
    MAX_COMPOSITION_CELL_TYPES,
    MAX_COMPOSITION_PROFILES,
    CompositionInput,
    CompositionObservationSpec,
    CompositionSample,
)
from .scrna_qc_selection import AcceptedQCSelection

_MAX_H5AD_BYTES = 512 * 1024**2
_MAX_METADATA_BYTES = 64 * 1024**2
_MAX_CELLS = 250_000


def _observation_columns(
    group: h5py.Group, names: tuple[str, str, str]
) -> tuple[list[str], ...]:
    index_name = group.attrs.get("_index", "_index")
    index = _node(group, index_name)
    if (
        not isinstance(index, h5py.Dataset)
        or index.ndim != 1
        or not 1 <= len(index) <= _MAX_CELLS
        or index_name in names
    ):
        raise ValueError("Composition requires a bounded, separate observation ID axis")
    remaining = _MAX_METADATA_BYTES
    result: list[list[str]] = []
    for name in (index_name, *names):
        values, size = _annotation(_node(group, name), len(index), remaining)
        remaining -= size
        if any(
            not isinstance(value, str) or value.strip() != value for value in values
        ):
            raise ValueError(
                "Composition requires complete explicit observation labels"
            )
        result.append(list(values))
    if len(set(result[0])) != len(index):
        raise ValueError("Composition observation identifiers must be unique")
    return tuple(result)


def read_observed_composition(
    path: Path,
    declaration: CompositionObservationSpec,
    *,
    qc_selection: AcceptedQCSelection | None = None,
) -> CompositionInput:
    if path.suffix.casefold() != ".h5ad":
        raise ValueError("Expected a H5AD observation source")
    names = (
        declaration.biological_unit_column,
        declaration.condition_column,
        declaration.cell_type_column,
    )
    with (
        bound_query_file(path, declaration.source_sha256, _MAX_H5AD_BYTES) as stream,
        h5py.File(stream, "r") as handle,
    ):
        observation_group = _node(handle, "obs")
        if not isinstance(observation_group, h5py.Group):
            raise ValueError("Expected a complete H5AD observation metadata group")
        cells, donors, groups, types = _observation_columns(observation_group, names)
    columns = (cells, donors, groups, types)
    if qc_selection is not None:
        columns = _select_composition_cells(columns, declaration, qc_selection)
    return _aggregate(*columns, declaration, qc_selection=qc_selection)


def _select_composition_cells(
    columns: tuple[list[str], list[str], list[str], list[str]],
    declaration: CompositionObservationSpec,
    selection: AcceptedQCSelection,
) -> tuple[list[str], list[str], list[str], list[str]]:
    cells, donors, groups, _ = columns
    flags = selection.align(declaration.source_sha256, cells)
    retained = [
        (cell, donor, group, label)
        for cell, donor, group, label, keep in zip(*columns, flags, strict=True)
        if keep
    ]
    if {(donor, group) for _, donor, group, _ in retained} != set(
        zip(donors, groups, strict=True)
    ):
        raise ValueError("Accepted QC removed every cell from a composition profile")
    retained_cells, retained_donors, retained_groups, retained_types = zip(
        *retained, strict=True
    )
    return list(retained_cells), list(retained_donors), list(retained_groups), list(
        retained_types
    )


def _aggregate(
    cells: list[str],
    donors: list[str],
    groups: list[str],
    types: list[str],
    declaration: CompositionObservationSpec,
    *,
    qc_selection: AcceptedQCSelection | None = None,
) -> CompositionInput:
    cell_types = tuple(sorted(set(types)))
    if len(cell_types) > MAX_COMPOSITION_CELL_TYPES:
        raise ValueError("Observed cell-type count exceeds the composition axis bound")
    role_document = declaration.model_dump(mode="json")
    if qc_selection is not None:
        role_document["qc_selection"] = qc_selection.binding.model_dump(mode="json")
        role_document["qc_mask_sha256"] = qc_selection.mask_sha256()
    assignments = sha256(canonical_json({"kind": "cell_labels", **role_document}))
    units = sha256(canonical_json({"kind": "biological_units", **role_document}))
    counts: dict[tuple[str, str], Counter[str]] = {}
    for cell, donor, group, label in zip(cells, donors, groups, types, strict=True):
        counts.setdefault((donor, group), Counter())[label] += 1
        if len(counts) > MAX_COMPOSITION_PROFILES:
            raise ValueError(
                "Observed profile count exceeds the composition axis bound"
            )
        assignments.update(b"\n" + canonical_json([cell, label]))
        units.update(b"\n" + canonical_json([cell, donor, group]))
    rows = tuple(
        CompositionSample(
            biological_unit_id=donor,
            condition=group,
            cells_observed=by_type.total(),
            counts=tuple(by_type[label] for label in cell_types),
        )
        for (donor, group), by_type in sorted(counts.items())
    )
    return CompositionInput(
        source_sha256=declaration.source_sha256,
        cell_label_source_sha256=assignments.hexdigest(),
        biological_unit_mapping_sha256=units.hexdigest(),
        biological_unit=declaration.biological_unit,
        cell_types=cell_types,
        samples=rows,
    )
