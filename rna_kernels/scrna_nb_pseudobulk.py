"""Build paired donor profiles from the sealed inference side of an NB split."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from scipy import sparse

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.core._hashing import decode_receipt_json
from devbench.core.expression_io import BackedExpression, open_h5ad
from devbench.core.scientific_types import content_sha256

from .canonical import canonical_json
from .mounted_model import compile_mounted_model
from .scrna_upstream_contract import (
    MAX_TABLE_BYTES,
    NB_FOLD_AGGREGATE,
    SCRNA_OPERATIONS,
    CountSplitPseudobulkParameters,
    MarkerCohortColumns,
    MountedSingleCellRequest,
)

_ROSTER_FORMAT = "devbench.nb-split-donor-roster/1"
_MAX_COUNT = 2**53 - 1
_MIN_CELL_WARNING = 20


def _identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 200
        and (value == value.strip() and not any(char in value for char in "\0\r\n"))
    )


def write_donor_roster(
    source: BackedExpression,
    target: Path,
    columns: MarkerCohortColumns,
    source_sha256: str,
) -> None:
    fields = {
        "donor": columns.donor_key,
        "specimen": columns.specimen_key,
        "condition": columns.condition_key,
    }
    if not set(fields.values()) <= set(source.obs.columns):
        raise ValueError("Split donor roster requires declared observation columns")
    data = source.obs[list(fields.values())].itertuples(index=False, name=None)
    cells = tuple(source.obs.index)
    rows = [
        {"cell_id": cell, **dict(zip(fields, values, strict=True))}
        for cell, values in zip(cells, data, strict=True)
    ]
    if any(not _identifier(value) for row in rows for value in row.values()):
        raise ValueError("Split donor roster requires exact biological identifiers")
    raw = canonical_json(
        {
            "format": _ROSTER_FORMAT,
            "source_sha256": source_sha256,
            "cell_ids_sha256": content_sha256(cells),
            "columns": columns.model_dump(mode="json"),
            "rows": rows,
        }
    )
    if len(raw) > MAX_TABLE_BYTES:
        raise ValueError("Split donor roster exceeds its metadata budget")
    target.write_bytes(raw)


def _document(path: Path, expected_format: str) -> dict[str, Any]:
    raw = read_bounded_file(path, MAX_TABLE_BYTES)
    doc = decode_receipt_json(raw)
    if (
        not isinstance(doc, dict)
        or doc.get("format") != expected_format
        or canonical_json(doc) != raw
    ):
        raise ValueError("Expected a canonical NB fold evidence document")
    return doc


def _documents(request: MountedSingleCellRequest, paths: Mapping[str, Path]):
    labels = _document(paths["cluster_labels"], "devbench.nb-fold-cluster-labels/1")
    split = _document(paths["split_report"], "devbench.nb-countsplit/1")
    roster = _document(paths["donor_roster"], _ROSTER_FORMAT)
    if (
        split.get("inference_counts_sha256") != request.inputs["inference_counts"]
        or split.get("donor_roster_sha256") != request.inputs["donor_roster"]
        or roster.get("source_sha256") != split.get("counts_sha256")
        or labels.get("split_report_sha256") != request.inputs["split_report"]
        or labels.get("clustering_counts_sha256")
        != split.get("clustering_counts_sha256")
        or labels.get("inference_performed") is not False
    ):
        raise ValueError(
            "NB aggregation requires counts, labels and roster from one split"
        )
    return labels, split, roster


def _axis_rows(source: BackedExpression, labels, split, roster):
    cells = tuple(source.obs.index)
    features = tuple(source.var.index)
    axis = content_sha256(cells)
    if (
        source._handle.attrs.get("devbench_fold_role") != "inference"
        or source.shape != (split.get("cells"), split.get("genes"))
        or content_sha256(features) != split["dispersion"]["feature_ids_sha256"]
        or any(item.get("cell_ids_sha256") != axis for item in (labels, split, roster))
    ):
        raise ValueError("NB inference counts do not match the declared split axes")
    if any(
        tuple(row.get("cell_id") for row in rows) != cells
        for rows in (labels["labels"], roster["rows"])
    ):
        raise ValueError("NB aggregation requires the complete ordered cell axis")
    return features, zip(labels["labels"], roster["rows"], strict=True)


def _profile_inventory(rows, parameters: CountSplitPseudobulkParameters):
    groups = (parameters.denominator, parameters.numerator)
    bins = []
    by_donor: dict[str, set[str]] = defaultdict(set)
    by_specimen: dict[str, set[str]] = defaultdict(set)
    for label, row in rows:
        donor, specimen, condition = (
            row[key] for key in ("donor", "specimen", "condition")
        )
        if not all(map(_identifier, (donor, specimen, condition))):
            raise ValueError("NB roster contains unresolved biological identifiers")
        cluster = label["cluster_id"]
        if cluster is not None and not _identifier(cluster):
            raise ValueError("NB label contains an invalid cluster identifier")
        key = None
        if condition == parameters.condition:
            by_donor[donor].add(specimen)
            by_specimen[specimen].add(donor)
            if cluster in groups:
                key = (donor, cluster)
        bins.append(key)
    if any(len(value) != 1 for value in (*by_donor.values(), *by_specimen.values())):
        raise ValueError("NB paired marker profiles require one specimen per donor")
    counts = Counter(key for key in bins if key is not None)
    if {cluster for _, cluster in counts} != set(groups):
        raise ValueError(
            "NB marker contrast requires observations in both declared groups"
        )
    profiles: list[dict[str, Any]] = []
    for donor, cluster in sorted(counts):
        sample = f"profile-{len(profiles) + 1}"
        profiles.append(
            {
                "sample_id": sample,
                "donor": donor,
                "specimen": next(iter(by_donor[donor])),
                "condition": cluster,
                "library": sample,
                "cell_count": counts[donor, cluster],
            }
        )
    lookup = {(row["donor"], row["condition"]): i for i, row in enumerate(profiles)}
    indices = np.fromiter(
        (lookup.get(key, -1) if key is not None else -1 for key in bins), dtype=np.int64
    )
    pairs = sum(all((donor, group) in counts for group in groups) for donor in by_donor)
    return profiles, indices, pairs, 2 * len(by_donor) - len(profiles)


def _persist_counts(target, source, features, profiles, indices):
    genes, width = len(features), len(profiles)
    if width > 4096 or genes * width * 8 > 256 * 1024**2:
        raise ValueError("Observed NB donor profiles exceed the binary panel budget")
    totals = [0] * width
    with h5py.File(target, "x") as handle:
        strings = h5py.string_dtype("utf-8")
        handle.create_dataset("gene_names", data=features, dtype=strings)
        handle.create_dataset(
            "sample_names", data=[row["sample_id"] for row in profiles], dtype=strings
        )
        counts = handle.create_dataset(
            "expression",
            (genes, width),
            dtype="<i8",
            chunks=(min(genes, 2048), 1),
            compression="gzip",
        )
        position = 0
        for block in source.iter_rows(block_rows=128, max_bytes=16 * 1024**2):
            values = sparse.csr_matrix(block.X)
            if (
                not values.has_canonical_format
                or values.dtype.kind not in "iu"
                or np.any(values.data < 0)
                or np.any(values.data > 2**31 - 1)
            ):
                raise ValueError(
                    "NB inference folds require canonical raw integer storage"
                )
            end = position + values.shape[0]
            assignment = indices[position:end]
            for index in np.unique(assignment[assignment >= 0]):
                delta = np.asarray(
                    values[assignment == index].sum(axis=0, dtype=np.int64)
                )
                delta = delta.reshape(-1)
                totals[index] += int(delta.sum(dtype=np.int64))
                if totals[index] > _MAX_COUNT:
                    raise ValueError("NB donor libraries exceed the exact count bound")
                counts[:, index] = counts[:, index] + delta
            position = end
    return totals


def aggregate_mounted_fold(
    request: MountedSingleCellRequest,
    paths: Mapping[str, Path],
    root: Path,
    parameters: CountSplitPseudobulkParameters,
) -> dict[str, Path]:
    labels, split, roster = _documents(request, paths)
    target, metadata = root / "pseudobulk.h5", root / "metadata.csv"
    with open_h5ad(paths["inference_counts"]) as source:
        features, rows = _axis_rows(source, labels, split, roster)
        profiles, indices, pairs, missing = _profile_inventory(rows, parameters)
        totals = _persist_counts(target, source, features, profiles, indices)
    with metadata.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(profiles[0]))
        writer.writeheader()
        writer.writerows(profiles)
    refusals = []
    if missing:
        refusals.append("incomplete_donor_pair")
    if pairs < 3:
        refusals.append("insufficient_paired_donors")
    if not all(totals):
        refusals.append("zero_inference_library")
    design = None if refusals else _paired_design(profiles, parameters)
    report = root / "aggregation_report.json"
    limit = SCRNA_OPERATIONS[NB_FOLD_AGGREGATE].outputs["pseudobulk_counts"].max_bytes
    report.write_bytes(
        canonical_json(
            {
                "format": "devbench.nb-fold-pseudobulk/1",
                "input_sha256": request.inputs,
                "contrast": parameters.model_dump(mode="json"),
                "pseudobulk_counts_sha256": hash_bounded_file(target, limit),
                "pseudobulk_metadata_sha256": hash_bounded_file(
                    metadata, MAX_TABLE_BYTES
                ),
                "feature_ids_sha256": content_sha256(features),
                "observed_profiles": len(profiles),
                "source_cells": len(indices),
                "included_cells": int(np.count_nonzero(indices >= 0)),
                "missing_profiles": missing,
                "paired_donors": pairs,
                "low_cell_profiles": sum(
                    row["cell_count"] < _MIN_CELL_WARNING for row in profiles
                ),
                "exclusions_applied_to_low_cell_profiles": False,
                "design_status": "held" if refusals else "ready",
                "design": design,
                "refusal_codes": refusals,
                "marker_inference_performed": False,
            }
        )
    )
    return {
        "pseudobulk_counts": target,
        "pseudobulk_metadata": metadata,
        "aggregation_report": report,
    }


def _paired_design(profiles, parameters: CountSplitPseudobulkParameters) -> dict:
    compiled = compile_mounted_model(
        profiles,
        {
            "condition_column": "condition",
            "unit_column": "donor",
            "technical_factors": [],
            "continuous_covariates": [],
            "numerator": parameters.numerator,
            "denominator": parameters.denominator,
            "model": {"comparison": "coefficient", "units": "paired_fixed"},
        },
    )
    samples, rank = compiled.design.shape
    matrix = np.ascontiguousarray(compiled.design, dtype="<f8")
    return {
        "rank": rank,
        "residual_df": samples - rank,
        "condition_number": compiled.condition_number,
        "design_sha256": content_sha256(
            {
                "sample_ids": [row["sample_id"] for row in profiles],
                "matrix_sha256": sha256(memoryview(matrix).cast("B")).hexdigest(),
                "shape": (samples, rank),
                "contrast": compiled.contrast.tolist(),
            }
        ),
    }
