"""Bounded prefit aggregation for count blocks from a trusted intake adapter.

The caller binds original measurement and reference provenance before streaming
blocks. This module checks their structural and numerical consistency. It does
not open files, authenticate receipt assertions, or admit a scientific fit.
"""
from dataclasses import dataclass, field, replace
import re
from typing import Iterable, Literal, TypeGuard

import numpy as np
from scipy import sparse  # type: ignore[import-untyped]

from rna_kernels.units import UNKNOWN_UNIT_MARKERS

from .population_contract import content_sha256


_MAX_COUNT = 2**53 - 1
_SHA256 = re.compile(r"[0-9a-f]{64}")
CountMeasurement = Literal["non_umi_counts", "umi_counts"]
ProfileStatus = Literal["observed", "observed_zero", "absent", "excluded"]


@dataclass(frozen=True)
class ProfileKey:
    unit_id: str
    specimen_id: str
    condition: str
    population: str | None


@dataclass(frozen=True)
class CountComponent:
    """One contribution and its half-open partition on a shared source axis.

    The axis names a common original read or capture inventory. Different
    axes cannot establish disjointness. Technical factors here are the
    retained factors that must survive aggregation.
    """

    component_id: str
    material_id: str
    library_id: str
    reference: str
    partition_axis: str
    partition_start: int
    partition_stop: int
    contribution_sha256: str
    measurement: CountMeasurement
    technical_factors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CountObservation:
    observation_id: str
    profile: ProfileKey
    component_id: str
    cell_id: str | None = None


@dataclass(frozen=True)
class CountBlock:
    observations: tuple[CountObservation, ...]
    counts: np.ndarray | sparse.csr_matrix | sparse.csr_array


@dataclass(frozen=True)
class CountLimits:
    max_observations: int = 2_000_000
    max_features: int = 100_000
    max_profiles: int = 2_048
    max_components: int = 65_536
    max_block_bytes: int = 16 * 1024**2
    max_panel_bytes: int = 16 * 1024**2


@dataclass(frozen=True)
class CountProfile:
    """An excluded profile retains its measured library size for audit only."""

    profile_id: str
    key: ProfileKey
    status: ProfileStatus
    observation_count: int
    cell_count: int | None
    counts: tuple[int, ...] | None
    library_size: int | None
    component_ids: tuple[str, ...]
    technical_factors: tuple[tuple[str, str], ...]
    exclusion_reason: str | None


@dataclass(frozen=True)
class CountProfiles:
    feature_ids: tuple[str, ...]
    reference: str | None
    measurement: CountMeasurement | None
    components: tuple[CountComponent, ...]
    profiles: tuple[CountProfile, ...]
    format: str = field(default="devbench.count-profiles/1", init=False)


def _label(value: object) -> TypeGuard[str]:
    return (type(value) is str and bool(value.strip()) and value == value.strip()
            and len(value) <= 200 and "\x00" not in value)


def _key_values(key):
    return key.unit_id, key.specimen_id, key.condition, key.population


def _validate_key(key):
    if (type(key) is not ProfileKey or any(not _label(item) for item in _key_values(key)[:3])
            or key.unit_id.lower() in UNKNOWN_UNIT_MARKERS or key.specimen_id.lower() in UNKNOWN_UNIT_MARKERS
            or (key.population is not None and not _label(key.population))):
        raise ValueError("Profile key needs a declared biological unit, specimen, condition and optional population")


def _component_map(components):
    result, digests = {}, set()
    for item in components:
        if (type(item) is not CountComponent or not all(_label(value) for value in (
                item.component_id, item.material_id, item.library_id, item.reference, item.partition_axis))
                or type(item.partition_start) is not int or type(item.partition_stop) is not int
                or not 0 <= item.partition_start < item.partition_stop <= 2**63 - 1
                or type(item.contribution_sha256) is not str
                or not _SHA256.fullmatch(item.contribution_sha256)
                or item.measurement not in ("non_umi_counts", "umi_counts")
                or type(item.technical_factors) is not tuple or len(item.technical_factors) > 16
                or any(type(factor) is not tuple or len(factor) != 2 or not all(map(_label, factor))
                       for factor in item.technical_factors)):
            raise ValueError("Technical contribution requires a bounded partition, digest and factor record")
        if len(dict(item.technical_factors)) != len(item.technical_factors):
            raise ValueError("Technical contribution repeats a factor")
        if item.component_id in result or item.contribution_sha256 in digests:
            raise ValueError("Duplicate component identity or duplicate contribution digest")
        result[item.component_id] = replace(item, technical_factors=tuple(sorted(item.technical_factors)))
        digests.add(item.contribution_sha256)
    return result


def _check_block(block, nfeatures, byte_limit):
    if type(block) is not CountBlock or type(block.observations) is not tuple:
        raise ValueError("A count block requires an ordered tuple of observation records")
    matrix = block.counts
    if type(matrix) is np.ndarray:
        entries = matrix
        size = entries.nbytes
        indices = None
    elif type(matrix) in (sparse.csr_matrix, sparse.csr_array):
        entries, indices = matrix.data, matrix.indices
        size = entries.nbytes + indices.nbytes + matrix.indptr.nbytes
    else:
        raise ValueError("Count blocks require a dense array or CSR matrix")
    if matrix.shape != (len(block.observations), nfeatures) or size > byte_limit:
        raise ValueError("Count block shape or byte limit is invalid")
    if indices is not None:
        pointers = matrix.indptr
        if (entries.ndim != 1 or indices.ndim != 1 or pointers.shape != (matrix.shape[0] + 1,)
                or entries.size != indices.size or indices.dtype.kind not in "iu"
                or pointers.dtype.kind not in "iu" or pointers[0] != 0 or pointers[-1] != entries.size
                or np.any(pointers[1:] < pointers[:-1]) or np.any(indices < 0)
                or np.any(indices >= nfeatures)):
            raise ValueError("Counts require valid canonical CSR coordinates")
        for start, stop in zip(pointers[:-1], pointers[1:]):
            row = indices[start:stop]
            if np.any(row[1:] <= row[:-1]):
                raise ValueError("Counts require unique, sorted canonical CSR coordinates")
    if (entries.dtype.kind not in "iuf" or not np.isfinite(entries).all() or np.any(entries < 0)
            or np.any(entries >= 2**53) or (entries.dtype.kind == "f" and np.any(entries != np.floor(entries)))):
        raise ValueError("Counts must be finite nonnegative integers within the exact count limit")
    return matrix, entries, indices


def _verify_merge(items):
    first = items[0]
    identity = (first.material_id, first.library_id, first.reference, first.measurement,
                first.partition_axis, first.technical_factors)
    if any((item.material_id, item.library_id, item.reference, item.measurement,
            item.partition_axis, item.technical_factors) != identity for item in items[1:]):
        raise ValueError("Profile contains incompatible technical contributions")


def compile_count_profiles(
    feature_ids: tuple[str, ...],
    components: tuple[CountComponent, ...],
    blocks: Iterable[CountBlock],
    *,
    expected_profiles: tuple[ProfileKey, ...],
    exclusions: tuple[tuple[ProfileKey, str], ...] = (),
    limits: CountLimits = CountLimits(),
) -> CountProfiles:
    """Aggregate by declared specimen/condition/population, retaining coverage.

    The expected inventory is frozen independently of counts. Sparse absence
    in a matrix is a zero entry. Absence in the inventory's observation
    coverage is an absent profile. Only the latter has no count vector.
    """
    if (type(limits) is not CountLimits
            or any(type(value) is not int or not 1 <= value <= getattr(CountLimits(), name)
                   for name, value in vars(limits).items())):
        raise ValueError("Count limits must be positive integers within the prefit bounds")
    if (type(feature_ids) is not tuple or not 1 <= len(feature_ids) <= limits.max_features
            or not all(map(_label, feature_ids)) or len(set(feature_ids)) != len(feature_ids)):
        raise ValueError("Counts require a complete ordered axis of unique feature IDs")
    if (type(expected_profiles) is not tuple or not 1 <= len(expected_profiles) <= limits.max_profiles
            or len(expected_profiles) * len(feature_ids) * 8 > limits.max_panel_bytes):
        raise ValueError("Original profile inventory exceeds the bounded count panel")
    specimens: dict[str, str] = {}
    for key in expected_profiles:
        _validate_key(key)
        if specimens.setdefault(key.specimen_id, key.unit_id) != key.unit_id:
            raise ValueError("A specimen cannot belong to multiple biological units")
    if len(set(expected_profiles)) != len(expected_profiles):
        raise ValueError("Original coverage contains duplicate profile keys")
    expected = set(expected_profiles)
    if type(components) is not tuple or len(components) > limits.max_components:
        raise ValueError("Technical contribution inventory exceeds its bound")
    by_component = _component_map(components)

    excluded: dict[ProfileKey, str] = {}
    if type(exclusions) is not tuple or len(exclusions) > len(expected):
        raise ValueError("Exclusions must be a bounded part of the original inventory")
    for key, reason in exclusions:
        if (type(key) is not ProfileKey or key not in expected or key in excluded
                or type(reason) is not str or not reason.strip() or len(reason) > 2_000 or "\x00" in reason):
            raise ValueError("Exclusion requires a unique original profile and a retained reason")
        excluded[key] = reason

    seen_observations: set[str] = set()
    component_scope: dict[str, tuple[str, str]] = {}
    seen_cell_contributions: set[tuple[str, str]] = set()
    cell_scope: dict[tuple[str, str], ProfileKey] = {}
    profile_components: dict[ProfileKey, set[str]] = {}
    profile_counts: dict[ProfileKey, np.ndarray] = {}
    profile_sizes: dict[ProfileKey, int] = {}
    profile_cells: dict[ProfileKey, int] = {}
    for block in blocks:
        if (type(block) is not CountBlock or type(block.observations) is not tuple
                or not 1 <= len(block.observations) <= limits.max_observations - len(seen_observations)):
            raise ValueError("Count block observation scope exceeds its bound or is empty")
        matrix, entries, indices = _check_block(block, len(feature_ids), limits.max_block_bytes)
        for index, observation in enumerate(block.observations):
            if (type(observation) is not CountObservation or not _label(observation.observation_id)
                    or not _label(observation.component_id)):
                raise ValueError("Counts require bounded observation and component IDs")
            key = observation.profile
            _validate_key(key)
            if key not in expected:
                raise ValueError("Observation is outside the original coverage")
            if observation.observation_id in seen_observations:
                raise ValueError("Counts contain a duplicate observation ID")
            seen_observations.add(observation.observation_id)
            if observation.component_id not in by_component:
                raise ValueError("Observation is missing its technical contribution receipt")
            if key.population is not None:
                if not _label(observation.cell_id):
                    raise ValueError("Population observations require explicit cell IDs")
                component_cell = (observation.component_id, observation.cell_id)
                if component_cell in seen_cell_contributions:
                    raise ValueError("Duplicate cell contribution within a technical component")
                seen_cell_contributions.add(component_cell)
                component = by_component[observation.component_id]
                cell = (component.library_id, observation.cell_id)
                if cell not in cell_scope:
                    cell_scope[cell] = key
                    profile_cells[key] = profile_cells.get(key, 0) + 1
                elif cell_scope[cell] != key:
                    raise ValueError("A cell identity crosses profiles within an originating library")
            elif observation.cell_id is not None:
                raise ValueError("Bulk count contributions cannot declare cell IDs")
            scope = (key.specimen_id, key.condition)
            if component_scope.setdefault(observation.component_id, scope) != scope:
                raise ValueError("A technical contribution cannot span specimens or conditions")
            profile_components.setdefault(key, set()).add(observation.component_id)
            profile_sizes[key] = profile_sizes.get(key, 0) + 1
            if key not in profile_counts:
                profile_counts[key] = np.zeros(len(feature_ids), dtype=np.int64)
            total = profile_counts[key]
            columns: slice | np.ndarray
            if indices is None:
                row, columns = entries[index], slice(None)
            else:
                start, stop = matrix.indptr[index:index + 2]
                row, columns = entries[start:stop], indices[start:stop]
            row = row.astype(np.int64, copy=False)
            if np.any(row > _MAX_COUNT - total[columns]):
                raise ValueError("Count aggregate exceeds the exact per-feature count limit")
            total[columns] += row

    for component_ids in profile_components.values():
        _verify_merge([by_component[item] for item in sorted(component_ids)])
    partitions: dict[str, list[CountComponent]] = {}
    library_scope: dict[str, tuple[str, str, str]] = {}
    library_axes: dict[str, str] = {}
    for item in by_component.values():
        if item.component_id not in component_scope:
            raise ValueError("Technical contribution receipt has no source observation")
        library_identity = (item.material_id, *component_scope[item.component_id])
        if library_scope.setdefault(item.library_id, library_identity) != library_identity:
            raise ValueError("An originating library cannot span materials, specimens or conditions")
        if library_axes.setdefault(item.library_id, item.partition_axis) != item.partition_axis:
            raise ValueError("Incompatible technical contributions lack a common partition axis")
        partitions.setdefault(item.partition_axis, []).append(item)
    for items in partitions.values():
        ordered = sorted(items, key=lambda item: item.partition_start)
        if any(left.partition_stop > right.partition_start for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Count input has overlapping technical contributions")
    references = {item.reference for item in by_component.values()}
    if len(references) > 1:
        raise ValueError("Count profiles must share one feature reference")
    measurements = {item.measurement for item in by_component.values()}
    if len(measurements) > 1:
        raise ValueError("Count profiles must share one measurement representation")

    output = []
    for key in sorted(expected, key=lambda item: (*_key_values(item)[:3], item.population or "")):
        identity = "profile-" + content_sha256(_key_values(key))
        ids = tuple(sorted(profile_components.get(key, ())))
        if not ids:
            if key in excluded:
                raise ValueError("Absent profile cannot carry an observed-profile exclusion")
            output.append(CountProfile(identity, key, "absent", 0, 0 if key.population else None,
                                       None, None, (), (), None))
            continue
        counts = tuple(map(int, profile_counts[key]))
        library_size = sum(counts)
        if library_size > _MAX_COUNT:
            raise ValueError("Count aggregate exceeds the exact library size limit")
        exclusion_reason = excluded.get(key)
        status: ProfileStatus = "excluded" if exclusion_reason else ("observed" if library_size else "observed_zero")
        output.append(CountProfile(identity, key, status, profile_sizes[key], profile_cells.get(key),
                                   None if exclusion_reason else counts,
                                   library_size, ids, by_component[ids[0]].technical_factors, exclusion_reason))
    return CountProfiles(feature_ids, next(iter(references), None), next(iter(measurements), None),
                         tuple(by_component[item] for item in sorted(by_component)), tuple(output))
