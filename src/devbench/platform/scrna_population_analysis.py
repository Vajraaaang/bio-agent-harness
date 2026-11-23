"""Bounded embryo-level inference for two explicitly supplied cell populations.

No cell-level replication, clustering, annotation inference, executable formulas,
feature selection or independently identifiable stage main effect is introduced.
The registered wrapper runs this importable kernel in the existing supervisor.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from devbench.core.population_contract import (
    PROCEDURE,
    content_sha256,
    validate_population_spec,
)
from devbench.core.population_data import _source, _validate_sparse_storage
from devbench.core.population_statistics import _statistics
from devharness_core.provenance import hash_artifact

_MAX_COUNT = 2**53 - 1
_BINARY_PANEL_BYTES = 256 * 1024**2
_BINARY_SUMMARY_BYTES = 1024**2
_BINARY_MAX_PROFILES = 4096
MAX_HYPOTHESES = 200_000
# Reserve output space for supervisor diagnostics and the small summary artifact.
MAX_RESULT_BYTES = 95_000_000
_POPULATION_AGGREGATION = (
    "sum_raw_umi_counts_within_observed_biological_unit_and_supplied_population"
)
_LIMITATIONS = [
    "Experimental registered procedure; independent calibration and biological "
    "validation are absent.",
    "Embryo identities and independence are supplied declarations, not authenticated "
    "animal identities.",
    "Annotation membership is supplied; annotation accuracy and population purity are "
    "unverified.",
    "Population contrasts are conditional on recorded stage and embryo fixed effects; "
    "no causal stage effect is identified.",
    "RLE normalization assumes most reference genes are stable or changes are "
    "balanced between the supplied populations.",
    "Wald intervals and p-values condition on estimated size factors and "
    "empirical-Bayes dispersions; their uncertainty is not fully propagated.",
    "Differences between these supplied populations are candidate markers, not "
    "independent marker validation.",
]


def bounded_result(value, maximum=None):
    """Reject oversized finite output in the worker before transport to its parent."""
    maximum = MAX_RESULT_BYTES if maximum is None else maximum
    total = 0
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode("utf-8"))
        if total > maximum:
            raise ValueError("Population result exceeds its byte bound")
    return value


def _audit(source, specification):
    import numpy as np

    obs = source.obs
    memberships = np.full(len(obs), -1, dtype=np.int8)
    for index, predicate in enumerate(specification["populations"]):
        selected = obs[predicate["column"]].isin(predicate["values"]).to_numpy()
        if np.any(selected & (memberships != -1)):
            raise ValueError("Supplied population predicates overlap")
        memberships[selected] = index
    selected = memberships >= 0
    required = [
        specification["biological_unit_column"],
        specification["stage_column"],
        *specification["technical_alias_columns"],
    ]
    if obs.loc[selected, required].isna().any().any():
        raise ValueError(
            "Selected cells require complete biological-unit, stage and technical "
            "labels"
        )
    stages = obs.loc[selected, specification["stage_column"]]
    if not set(stages) <= set(specification["stage_levels"]):
        raise ValueError(
            "Selected population contains stages outside declared stage_levels"
        )
    unit_column = specification["biological_unit_column"]
    selected_obs = obs.loc[selected]
    if any(
        len(set(rows[specification["stage_column"]])) != 1
        for _, rows in selected_obs.groupby(unit_column, observed=True)
    ):
        raise ValueError(
            "One declared biological unit occurs at multiple collection stages"
        )
    membership_records = [
        [str(cell), int(group)]
        for cell, group in zip(obs.index, memberships, strict=True)
    ]
    labels = [population["label"] for population in specification["populations"]]
    audit = {
        "observation_count": len(obs),
        "feature_count": source.shape[1],
        "selected_cell_count": int(selected.sum()),
        "excluded_cell_count": int((~selected).sum()),
        "population_cell_counts": {
            label: int(np.sum(memberships == index))
            for index, label in enumerate(labels)
        },
        "declared_biological_unit_count": int(selected_obs[unit_column].nunique()),
        "observation_ids_unique": True,
        "feature_ids_unique": True,
        "populations_disjoint": True,
        "independence_verified": False,
        "biological_unit_identity_status": "declared",
        "membership_sha256": content_sha256(membership_records),
        "observation_ids_sha256": content_sha256(list(obs.index)),
        "feature_ids_sha256": content_sha256(list(source.var.index)),
        "feature_universe": "all_stored_features_in_original_order",
        "counts_layer": specification["counts_layer"],
        "measurement_declaration": "umi_counts",
        "expression_values_read": False,
        "counts_valid": None,
        "observation_ids": list(obs.index),
        "population_membership": memberships.tolist(),
    }
    return audit, memberships


def _profile_assignment(obs, specification, memberships, *, max_profiles=None):
    import numpy as np

    labels = [p["label"] for p in specification["populations"]]
    unit_column, stage_column = (
        specification["biological_unit_column"],
        specification["stage_column"],
    )
    units = sorted(set(obs.loc[memberships >= 0, unit_column]))
    profiles, missing, profile_indices = [], [], np.full(len(obs), -1, dtype=int)
    stages_by_unit = {}
    for unit in units:
        unit_cells = (obs[unit_column] == unit).to_numpy() & (memberships >= 0)
        stage = str(obs.loc[unit_cells, stage_column].iloc[0])
        stages_by_unit[unit] = stage
        for population, label in enumerate(labels):
            cells = unit_cells & (memberships == population)
            if not cells.any():
                missing.append(
                    {"biological_unit": unit, "population": label, "stage": stage}
                )
                continue
            profile_indices[cells] = len(profiles)
            profiles.append(
                {
                    "profile_id": "profile_" + str(len(profiles)),
                    "biological_unit": unit,
                    "population": label,
                    "stage": stage,
                    "cell_count": int(cells.sum()),
                }
            )
    if max_profiles is None:
        max_profiles = specification["limits"]["max_profiles"]
    if len(profiles) > max_profiles:
        raise ValueError("Embryo-population profiles exceed the fixed profile bound")
    return units, profiles, missing, profile_indices, stages_by_unit


def _design(source, specification, memberships):
    import numpy as np
    import pandas as pd

    from devbench.study.biology_tool_io import design_matrix_diagnostics

    obs, labels = source.obs, [p["label"] for p in specification["populations"]]
    unit_column = specification["biological_unit_column"]
    units, profiles, missing, profile_indices, stages_by_unit = _profile_assignment(
        obs,
        specification,
        memberships,
    )
    columns = (
        ["intercept"]
        + ["biological_unit_" + str(i) for i in range(1, len(units))]
        + [
            "population_at_stage_" + str(i)
            for i in range(len(specification["stage_levels"]))
        ]
    )
    values = [
        [1.0]
        + [float(profile["biological_unit"] == unit) for unit in units[1:]]
        + [
            float(profile["population"] == labels[1] and profile["stage"] == stage)
            for stage in specification["stage_levels"]
        ]
        for profile in profiles
    ]
    matrix = pd.DataFrame(
        values, columns=columns, index=[p["profile_id"] for p in profiles], dtype=float
    )
    matrix.attrs["condition_number_limit"] = 1000.0
    diagnostics = (
        design_matrix_diagnostics(matrix)
        if len(profiles)
        else {
            "rank": 0,
            "column_count": len(columns),
            "residual_df": 0,
            "condition_number": None,
            "condition_number_validated": False,
        }
    )
    refusal = []
    if not diagnostics["condition_number_validated"]:
        refusal.append("rank_deficient_or_ill_conditioned_design")
    if diagnostics["residual_df"] < 2:
        refusal.append("insufficient_independent_residual_degrees_of_freedom")
    aliases = []
    alias_diagnostics = {}
    selected_obs = obs.loc[memberships >= 0]
    for alias in specification["technical_alias_columns"]:
        mapping = selected_obs[[unit_column, alias]].drop_duplicates()
        valid = bool(
            len(mapping) == len(units) and mapping[alias].nunique() == len(units)
        )
        alias_diagnostics[alias] = {
            "one_to_one_biological_unit_alias": valid,
            "distinct_value_count": int(mapping[alias].nunique()),
        }
        if valid:
            aliases.append(alias)
        else:
            refusal.append("technical_column_is_not_one_to_one_biological_unit_alias")
    contrasts = []
    projection = (
        np.linalg.pinv(matrix.to_numpy()) @ matrix.to_numpy()
        if len(profiles)
        else np.zeros((len(columns), len(columns)))
    )
    for index, stage in enumerate(specification["stage_levels"]):
        vector = np.zeros(len(columns))
        vector[len(columns) - len(specification["stage_levels"]) + index] = 1
        paired = sum(
            all(
                any(
                    p["biological_unit"] == unit and p["population"] == label
                    for p in profiles
                )
                for label in labels
            )
            for unit in units
            if stages_by_unit[unit] == stage
        )
        estimable = bool(
            np.allclose(vector @ projection, vector, rtol=1e-8, atol=1e-10)
        )
        contrasts.append(
            {
                "contrast_id": "population_at_stage_" + str(index),
                "stage": stage,
                "numerator": labels[1],
                "denominator": labels[0],
                "vector": vector.tolist(),
                "paired_unit_count": int(paired),
                "minimum_paired_units": 2,
                "estimable": estimable,
                "supported": bool(estimable and paired >= 2),
                "refusal_code": None
                if estimable and paired >= 2
                else "insufficient_paired_biological_units_or_nonestimable_contrast",
            }
        )
    result = {
        "profiles": profiles,
        "missing_combinations": missing,
        "columns": columns,
        "matrix": values,
        "contrasts": contrasts,
        "diagnostics": diagnostics,
        "identifiable": not refusal,
        "refusal_codes": list(dict.fromkeys(refusal)),
        "biological_unit_reference": units[0] if units else None,
        "biological_unit_levels": units,
        "biological_unit_column": unit_column,
        "stage_main_effects": "absorbed_by_biological_unit_fixed_effects",
        "alias_columns_absorbed_by_biological_unit": aliases,
        "technical_alias_diagnostics": alias_diagnostics,
        "coding": (
            "intercept_plus_reference_coded_embryos_plus_"
            "stage_specific_numerator_indicators"
        ),
        "parameter_origin": "assistant_proposed_procedure",
        "dropped_protected_terms": [],
        "warnings": [],
    }
    low_cells = [profile for profile in profiles if profile["cell_count"] < 20]
    if low_cells:
        result["warnings"].append(
            {
                "code": "low_cell_count_population_profiles",
                "severity": "warning",
                "message": "Very small embryo-population groups can yield sparse or "
                "unstable pseudobulk estimates.",
                "cell_count_threshold": 20,
                "threshold_origin": "assistant_proposed_diagnostic",
                "profile_count": len(low_cells),
                "profiles": low_cells,
                "exclusions_applied": False,
            }
        )
    result["design_sha256"] = content_sha256(result)
    return result, matrix, profile_indices


def _raw_count_blocks(source, limits):
    import numpy as np
    from scipy import sparse

    _validate_sparse_storage(source, limits["block_bytes"])
    offset = 0
    for block in source.iter_rows(
        block_rows=limits["block_rows"], max_bytes=limits["block_bytes"]
    ):
        matrix = block.X
        entries = matrix.data if sparse.issparse(matrix) else np.asarray(matrix)
        if (
            entries.dtype.kind not in "iuf"
            or not np.isfinite(entries).all()
            or (entries < 0).any()
            or (entries > _MAX_COUNT).any()
            or not (entries == np.floor(entries)).all()
        ):
            raise ValueError(
                "Raw counts must be finite nonnegative integers <= 2**53-1"
            )
        if sparse.issparse(matrix) and not matrix.has_canonical_format:
            raise ValueError("Sparse counts must contain unique sorted coordinates")
        # Bounding each cell sum before any int64 reduction prevents both
        # integer overflow and later loss of exact count representation.
        cell_totals = np.asarray(matrix.sum(axis=1, dtype=np.float64)).reshape(-1)
        if not np.isfinite(cell_totals).all() or (cell_totals > _MAX_COUNT).any():
            raise ValueError("Raw counts exceed exact per-cell count-sum bounds")
        yield offset, matrix
        offset += matrix.shape[0]
    if offset != source.shape[0]:
        raise ValueError(
            "Count scan did not cover the complete source observation axis"
        )


def _pseudobulk(source, specification, design, profile_indices):
    import numpy as np
    from scipy import sparse

    limits, genes, profiles = (
        specification["limits"],
        source.shape[1],
        len(design["profiles"]),
    )
    if genes * profiles * 8 > limits["max_panel_bytes"]:
        raise ValueError(
            "Complete feature pseudobulk panel exceeds its byte budget; feature "
            "selection is not permitted"
        )
    counts = np.zeros((genes, profiles), dtype=np.int64)
    offset, blocks, largest = 0, 0, 0
    for offset, matrix in _raw_count_blocks(source, limits):
        assignment = profile_indices[offset : offset + matrix.shape[0]]
        for index in range(matrix.shape[0]):
            profile = int(assignment[index])
            if profile < 0:
                continue
            row = (
                matrix.getrow(index).toarray().reshape(-1)
                if sparse.issparse(matrix)
                else matrix[index]
            )
            row = np.asarray(row, dtype=np.int64)
            if (row > _MAX_COUNT - counts[:, profile]).any():
                raise ValueError("Aggregated counts exceed exact integer bounds")
            counts[:, profile] += row
        offset += matrix.shape[0]
        blocks += 1
        largest = max(largest, int(matrix.shape[0]))
    library_sizes = counts.sum(axis=0, dtype=np.float64)
    if (library_sizes > _MAX_COUNT).any():
        raise ValueError("Aggregated counts exceed exact library-sum bounds")
    payload = {
        "feature_ids": list(source.var.index),
        "profile_ids": [p["profile_id"] for p in design["profiles"]],
        "counts": counts.tolist(),
        "orientation": "feature_by_embryo_population",
        "aggregation": _POPULATION_AGGREGATION,
        "missing_combinations": "absent_profiles_never_zero_filled",
        "panel_bytes": int(counts.nbytes),
        "profile_library_sizes": library_sizes.astype(np.int64).tolist(),
        "count_validation": {
            "all_source_entries_validated": True,
            "finite": True,
            "integer_valued": True,
            "nonnegative": True,
            "sparse_storage_structure_validated": True,
            "source_cells_scanned": offset,
            "blocks": blocks,
            "largest_block_rows": largest,
        },
    }
    payload["pseudobulk_sha256"] = content_sha256(payload)
    return payload, counts


def _exact_axis_sum(values):
    """Sum in chunks small enough for uint64, retaining overflow beyond 2**53."""
    import numpy as np

    return sum(
        int(values[start : start + 2048].sum(dtype=np.uint64))
        for start in range(0, len(values), 2048)
    )


def _write_binary_counts(handle, source, limits, profiles, indices):
    import numpy as np
    from scipy import sparse

    genes = source.shape[1]
    shape = (genes, len(profiles))
    if (
        not 0 < len(profiles) <= _BINARY_MAX_PROFILES
        or genes * len(profiles) * 8 > _BINARY_PANEL_BYTES
    ):
        raise ValueError("Observed pseudobulk profiles exceed the binary panel bounds")
    counts = handle.create_dataset(
        "expression",
        shape=shape,
        dtype="<i8",
        chunks=(min(genes, 2048), 1),
        compression="gzip",
        compression_opts=3,
    )
    totals = [0] * len(profiles)
    for offset, matrix in _raw_count_blocks(source, limits):
        assignment = indices[offset : offset + matrix.shape[0]]
        is_sparse = sparse.issparse(matrix)
        for profile in np.unique(assignment):
            if profile < 0:
                continue
            aggregate = counts[:, profile]
            for index in np.flatnonzero(assignment == profile):
                row = (
                    matrix[index : index + 1].toarray().reshape(-1)
                    if is_sparse
                    else matrix[index]
                )
                row = np.asarray(row, dtype=np.int64)
                # The library ceiling also bounds every individual count and addition.
                totals[profile] += _exact_axis_sum(row)
                if totals[profile] > _MAX_COUNT:
                    raise ValueError(
                        "Aggregated counts exceed exact library-sum bounds"
                    )
                aggregate += row
            counts[:, profile] = aggregate
    return shape, totals


def _binary_profile_metadata(handle, source, profiles, missing):
    import h5py

    string = h5py.string_dtype("utf-8")
    handle.create_dataset("gene_names", data=list(source.var.index), dtype=string)
    handle.create_dataset(
        "sample_names", data=[p["profile_id"] for p in profiles], dtype=string
    )
    profile_group = handle.create_group("profiles")
    absent_group = handle.create_group("missing_profiles")
    for field in ("biological_unit", "stage", "population"):
        profile_group.create_dataset(
            field, data=[p[field] for p in profiles], dtype=string
        )
        absent_group.create_dataset(
            field, data=[p[field] for p in missing], dtype=string
        )
    profile_group.create_dataset(
        "cell_count", data=[p["cell_count"] for p in profiles], dtype="<i8"
    )


def stage_population_pseudobulk_hdf5(
    anndata_path, specification, destination, *, population=None
):
    """Stage a verified HDF5 count capsule in a trusted private output directory.

    The existing population declaration supplies the cohort and aggregation
    roles. The distinct binary output contract has its own fixed panel bound.
    An authorization decision and custody of the returned digest belong to
    the calling coordinator.
    """
    import h5py

    specification = validate_population_spec(specification)
    output = Path(destination)
    if (
        not output.is_absolute()
        or output.parent.resolve() != output.parent
        or output.suffix != ".h5"
        or not output.parent.is_dir()
    ):
        raise ValueError(
            "Use an absolute HDF5 capsule path in a canonical staging directory"
        )
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".pseudobulk-"
    ) as directory:
        staged = Path(directory) / "pseudobulk.h5"
        with _source(anndata_path, specification) as source:
            audit, memberships = _audit(source, specification)
            _, profiles, missing, indices, _ = _profile_assignment(
                source.obs,
                specification,
                memberships,
                max_profiles=_BINARY_MAX_PROFILES * len(specification["populations"]),
            )
            if population is not None:
                profiles, missing, indices = _population_capsule_scope(
                    profiles,
                    missing,
                    indices,
                    population,
                )
            with h5py.File(staged, "x") as handle:
                shape, totals = _write_binary_counts(
                    handle, source, specification["limits"], profiles, indices
                )
                _binary_profile_metadata(handle, source, profiles, missing)
        artifact = hash_artifact(staged, max_bytes=_BINARY_PANEL_BYTES, max_entries=1)
        summary = {
            "format": "devbench.scrna-population-pseudobulk-hdf5/1",
            "source_sha256": specification["source_sha256"],
            "specification_sha256": content_sha256(specification),
            "feature_ids_sha256": audit["feature_ids_sha256"],
            "membership_sha256": audit["membership_sha256"],
            "observation_ids_sha256": audit["observation_ids_sha256"],
            "independence_verified": False,
            "aggregation": _POPULATION_AGGREGATION,
            "missing_combinations": "absent_profiles_never_zero_filled",
            "missing_profile_count": len(missing),
            "population": population,
            "profile_library_sizes": totals,
            "profile_ids": [profile["profile_id"] for profile in profiles],
            "count_validation": dict.fromkeys(
                (
                    "all_source_entries_validated",
                    "sparse_storage_structure_validated",
                    "finite",
                    "integer_valued",
                    "nonnegative",
                ),
                True,
            ),
            "matrix": {
                "name": output.name,
                "shape": list(shape),
                "dtype": "int64",
                "orientation": "feature_by_embryo_population",
                "decoded_bytes": shape[0] * shape[1] * 8,
                "size_bytes": artifact["size_bytes"],
                "sha256": artifact["sha256"],
            },
        }
        bounded_result(summary, _BINARY_SUMMARY_BYTES)
        staged.chmod(0o400)
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        os.link(staged, output)
    return summary


def _population_capsule_scope(profiles, missing, indices, population):
    import numpy as np

    selected = [
        index for index, row in enumerate(profiles) if row["population"] == population
    ]
    if not selected:
        raise ValueError(
            "The mounted pseudobulk capsule requires an observed supplied population"
        )
    scoped = np.full(indices.shape, -1, dtype=indices.dtype)
    remap = np.full(len(profiles), -1, dtype=indices.dtype)
    remap[selected] = np.arange(len(selected))
    observed = indices >= 0
    scoped[observed] = remap[indices[observed]]
    return (
        [profiles[index] for index in selected],
        [row for row in missing if row["population"] == population],
        scoped,
    )


def _retain_population_contrast(
    source,
    specification,
    result,
    matrix,
    counts,
    binary_destination,
    event,
):
    result["testing_family"] = {
        "method": "Benjamini-Hochberg",
        "scope": "all_stored_features_by_all_declared_stages",
        "attempted_n": source.shape[1] * len(specification["stage_levels"]),
        "feature_ids_sha256": result["audit"]["feature_ids_sha256"],
        "stages": specification["stage_levels"],
        "frozen_before_fit": True,
        "missing_hypothesis_policy": "internal_p_one_public_p_q_null",
    }
    started = time.monotonic()
    rows = _statistics(
        source,
        specification,
        result["design"],
        matrix,
        counts,
        result["diagnostics"],
    )
    if binary_destination is None:
        result["gene_statistics"] = rows
        event("contrast", started, rows)
    else:
        from .population_parquet import write_population_family

        result["result_artifact"] = write_population_family(
            binary_destination, rows, result
        )
        event("contrast", started, result["result_artifact"])
    failed = sum(row["estimate_status"] == "failed" for row in rows)
    refused = sum(row["estimate_status"] == "refused" for row in rows)
    untestable = sum(row["estimate_status"] == "not_estimable" for row in rows)
    estimated = len(rows) - failed - refused - untestable
    if failed:
        result["status"] = "scientific_failure"
    elif refused or not estimated:
        result["status"] = "scientific_refusal"
    stage_summaries = []
    for stage_label in specification["stage_levels"]:
        stage_rows = [row for row in rows if row["stage"] == stage_label]
        successful = sorted(
            (row for row in stage_rows if row["p_value"] is not None),
            key=lambda r: (r["q_value"], r["p_value"], r["gene_id"]),
        )
        stage_summaries.append(
            {
                "stage": stage_label,
                "estimated_n": len(successful),
                "q_below_0_05_n": sum(row["q_value"] < 0.05 for row in successful),
                "display_threshold_origin": ("assistant_proposed_exploratory_summary"),
                "top_genes": successful[:20],
            }
        )
    result["summary"] = {
        "audit_passed": True,
        "design_identifiable": result["design"]["identifiable"],
        "full_feature_count": source.shape[1],
        "attempted_hypothesis_count": len(rows),
        "estimated_hypothesis_count": estimated,
        "failed_hypothesis_count": failed,
        "refused_hypothesis_count": refused,
        "not_estimable_hypothesis_count": untestable,
        "population_cell_counts": result["audit"]["population_cell_counts"],
        "stage_summaries": stage_summaries,
        "analysis_scope": ("experimental_stage_specific_supplied_population_contrasts"),
    }


def _retain_population_counts(
    source,
    specification,
    result,
    indices,
    binary_destination,
    event,
):
    started = time.monotonic()
    if binary_destination is None:
        result["pseudobulk"], counts = _pseudobulk(
            source, specification, result["design"], indices
        )
    else:
        result["pseudobulk"], counts = _capsule_for_fit(
            source, specification, result["design"], indices, binary_destination
        )
    event(
        "pseudobulk",
        started,
        {key: value for key, value in result["pseudobulk"].items() if key != "counts"},
    )
    return counts


def run_population_analysis(
    anndata_path, specification, stage, *, binary_destination=None
):
    """Importable worker kernel; authorization is owned by the native caller."""
    if stage not in {"audit", "design", "pseudobulk", "contrast"}:
        raise ValueError("Unknown supplied-population analysis stage")
    if binary_destination is not None and stage != "contrast":
        raise ValueError("Binary output is only supported for population contrasts")
    specification = validate_population_spec(specification)
    result = {
        "format": "devbench.scrna-population-"
        + stage
        + ("/2" if binary_destination is not None else "/1"),
        "status": "completed",
        "source_sha256": specification["source_sha256"],
        "specification_sha256": content_sha256(specification),
        "specification": specification,
        "procedure": PROCEDURE,
        "runtime_events": [],
        "limitations": list(_LIMITATIONS),
        "diagnostics": {},
    }

    def event(phase, started, evidence):
        result["runtime_events"].append(
            {
                "phase": phase,
                "status": "completed",
                "elapsed_seconds": time.monotonic() - started,
                "evidence_sha256": content_sha256(evidence),
            }
        )

    with _source(anndata_path, specification) as source:
        if (
            binary_destination is None
            and stage == "contrast"
            and source.shape[1] * len(specification["stage_levels"]) > MAX_HYPOTHESES
        ):
            raise ValueError(
                "Complete gene-by-stage family exceeds the bounded result-row budget; "
                "no feature selection is permitted"
            )
        started = time.monotonic()
        result["audit"], memberships = _audit(source, specification)
        event("audit", started, result["audit"])
        if stage == "audit":
            return bounded_result(result)
        started = time.monotonic()
        result["design"], matrix, indices = _design(source, specification, memberships)
        event("design", started, result["design"])
        if not result["design"]["identifiable"]:
            result["status"] = "scientific_refusal"
        if stage == "design":
            return bounded_result(result)
        # Refused contrasts still emit a complete family of null statistics.
        # Only the explicit pseudobulk stage materializes counts for a refused design.
        counts = None
        if result["design"]["identifiable"] or stage == "pseudobulk":
            counts = _retain_population_counts(
                source,
                specification,
                result,
                indices,
                binary_destination,
                event,
            )
        else:
            result["pseudobulk"] = None
        if stage == "pseudobulk":
            return bounded_result(result)
        _retain_population_contrast(
            source,
            specification,
            result,
            matrix,
            counts,
            binary_destination,
            event,
        )
    return (
        _bounded_contrast_summary(result)
        if binary_destination is not None
        else bounded_result(result)
    )


def run_population_contrast_parquet(anndata_path, specification, destination):
    return run_population_analysis(
        anndata_path, specification, "contrast", binary_destination=destination
    )


def _capsule_for_fit(source, specification, design, indices, destination):
    import h5py

    with tempfile.TemporaryDirectory(
        dir=Path(destination).parent, prefix=".population-fit-"
    ) as directory:
        path = Path(directory) / "counts.h5"
        profiles = design["profiles"]
        with h5py.File(path, "x") as handle:
            shape, totals = _write_binary_counts(
                handle, source, specification["limits"], profiles, indices
            )
            _binary_profile_metadata(
                handle, source, profiles, design["missing_combinations"]
            )
            counts = handle["expression"][:]
        artifact = hash_artifact(path, max_bytes=_BINARY_PANEL_BYTES, max_entries=1)
        return {
            "pseudobulk_sha256": artifact["sha256"],
            "panel_bytes": shape[0] * shape[1] * 8,
            "orientation": "feature_by_embryo_population",
            "aggregation": _POPULATION_AGGREGATION,
            "missing_combinations": "absent_profiles_never_zero_filled",
            "profile_ids": [profile["profile_id"] for profile in profiles],
            "profile_library_sizes": totals,
            "count_validation": dict.fromkeys(
                (
                    "all_source_entries_validated",
                    "sparse_storage_structure_validated",
                    "finite",
                    "integer_valued",
                    "nonnegative",
                ),
                True,
            ),
        }, counts


def _bounded_contrast_summary(result):
    result.pop("specification")
    for key in ("observation_ids", "population_membership"):
        result["audit"].pop(key, None)
    result["design"].pop("matrix", None)
    diagnostics = result["diagnostics"]
    for key in ("gene_fit_failures", "model_warnings"):
        diagnostics[key + "_count"] = len(diagnostics.pop(key, {}))
    calibration = diagnostics.get("calibration", {})
    calibration.pop("gene_estimates", None)
    calibration.pop("optimizer_retries", None)
    for key in ("calibration_diagnostics", "independent_count_filter", "plot_evidence"):
        diagnostics.pop(key, None)
    normalization = diagnostics.get("normalization", {})
    normalization.pop("reference_gene_ids", None)
    return bounded_result(result, maximum=_BINARY_SUMMARY_BYTES)
