"""Metadata-only contract for the later original Love et al. airway trial.

This module neither accepts a count payload nor opens a file. Its successful
result means a declaration matches the prepared-object precondition. It does
not establish that a count matrix is present, intact, authentic, scientifically
valid, or authorized for execution. The eventual reader must still verify the
actual object's complete gene/sample identities, counts and metadata.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json

import numpy as np


_SOURCE = {
    "citation": "Love et al., F1000Research 2015, 4:1070",
    "doi": "10.12688/f1000research.7035.1",
    "article_version": 1,
    "data_package": "airway",
    "data_package_version": "0.102.0",
}
_DESIGN = {
    "formula": "~ cell + dex",
    "biological_unit_column": "cell",
    "biological_unit_type": "donor_derived_cell_line",
    "blocking_column": "cell",
    "condition_column": "dex",
    "reference_condition": "untrt",
    "comparison_condition": "trt",
}
_MANIFEST_FIELDS = {"format", "source", "measurement", "shape", "design", "samples"}
_UNKNOWN_IDENTITIES = {"", "unknown", "missing", "not_known", "not_available", "none", "null", "na", "n/a", "nan"}


def airway_input_contract() -> dict:
    """Return an independent copy of the required declared object contract.

    The source and design are historical reproduction requirements, not a
    claim that a user prespecified the statistically best method.
    """
    return {
        "format": "devbench.airway-input-contract/1",
        "declaration_format": "devbench.airway-input-declaration/1",
        "source": deepcopy(_SOURCE),
        "measurement": "raw_counts",
        "shape": [64102, 8],
        "design": deepcopy(_DESIGN),
        "biological_unit_count": 4,
        "libraries_per_biological_unit": 2,
        "expected_design_rank": 5,
        "expected_residual_degrees_of_freedom": 3,
        "validation_scope": "declared_metadata_only",
        "counts_required_by_validator": False,
        "requirement_origin": "historical_prepared_object_contract",
        "later_reader_obligations": [
            "verify_actual_source_and_prepared_object_identity",
            "verify_complete_gene_and_sample_identities_and_order",
            "verify_finite_nonnegative_integer_counts",
            "verify_actual_metadata_pairing_rank_and_residual_degrees_of_freedom",
            "obtain_separate_matching_execution_authority",
        ],
    }


def _exact_mapping(value: object, expected: dict) -> bool:
    return (type(value) is dict and set(value) == set(expected)
            and all(type(value[key]) is type(item) and value[key] == item
                    for key, item in expected.items()))


def _identifier(value: object) -> bool:
    return (type(value) is str and 1 <= len(value) <= 160
            and value == value.strip() and value.lower() not in _UNKNOWN_IDENTITIES)


def validate_airway_input_contract(manifest: dict) -> dict:
    """Check only a supplied, bounded metadata declaration of the original input.

    ``manifest`` has exactly ``format``, ``source``, ``measurement``, ``shape``,
    ``design`` and ``samples``. Source/design values match
    :func:`airway_input_contract`; each of the eight sample rows contains only
    ``sample_id``, ``cell`` and ``dex``. ``shape`` is ``[64102, 8]``. No count
    matrix, path, source inspection, model launch or statistical fit is involved.

    A 63,677-row substitute is rejected even if its paired metadata otherwise
    agrees. A matching shape is not evidence of the actual gene universe.
    """
    if (type(manifest) is not dict or set(manifest) != _MANIFEST_FIELDS
            or manifest["format"] != "devbench.airway-input-declaration/1"):
        raise ValueError("Use the closed metadata-only airway input declaration; no count payload or path is accepted")
    if not _exact_mapping(manifest["source"], _SOURCE):
        raise ValueError("Declare the exact original source: Love 2015 version 1 and airway 0.102.0")
    if type(manifest["measurement"]) is not str or manifest["measurement"] != "raw_counts":
        raise ValueError("The original airway object requires declared raw_counts")
    shape = manifest["shape"]
    if (type(shape) is not list or len(shape) != 2
            or any(type(value) is not int for value in shape) or shape != [64102, 8]):
        raise ValueError("The original airway contract requires exactly 64,102 genes and eight libraries")
    if not _exact_mapping(manifest["design"], _DESIGN):
        raise ValueError("Declare the original paired cell-line design: ~ cell + dex, with cell as the biological unit")
    rows = manifest["samples"]
    if type(rows) is not list or len(rows) != 8:
        raise ValueError("Declare exactly eight library memberships")
    for row in rows:
        if (type(row) is not dict or set(row) != {"sample_id", "cell", "dex"}
                or any(not _identifier(value) for value in row.values())):
            raise ValueError("Each library needs explicit bounded sample_id, cell and dex identities")
        if row["dex"] not in {"untrt", "trt"}:
            raise ValueError("The original airway conditions are untrt and trt")
    if len({row["sample_id"] for row in rows}) != 8:
        raise ValueError("Each library sample_id must be distinct; memberships do not create independent donors")
    cells = sorted({row["cell"] for row in rows})
    if len(cells) != 4:
        raise ValueError("The original airway contract has four paired donor-derived cell lines")
    pairs = Counter((row["cell"], row["dex"]) for row in rows)
    if any(pairs[(cell, condition)] != 1 for cell in cells for condition in ("untrt", "trt")):
        raise ValueError("Each donor-derived cell line must have exactly one library in each condition")
    # This is a tiny metadata design check, not a fit or a count matrix read.
    design = np.asarray([[1, int(row["dex"] == "trt"),
                          *[int(row["cell"] == cell) for cell in cells[1:]]]
                         for row in rows], dtype=float)
    rank = int(np.linalg.matrix_rank(design))
    residual_df = len(rows) - rank
    if rank != 5 or residual_df != 3:
        raise ValueError("The declared paired design must have rank five and residual degrees of freedom three")
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "format": "devbench.airway-input-declaration-audit/1",
        "status": "declared_contract_satisfied",
        "declaration_sha256": hashlib.sha256(canonical).hexdigest(),
        "source": deepcopy(_SOURCE),
        "family_gene_count": shape[0],
        "library_count": len(rows),
        "biological_unit_count": len(cells),
        "biological_unit_type": "donor_derived_cell_line",
        "design_formula": _DESIGN["formula"],
        "design_columns": ["Intercept", "dex_trt", *["cell_" + cell for cell in cells[1:]]],
        "design_rank": rank,
        "residual_degrees_of_freedom": residual_df,
        "validation_scope": "declared_metadata_only",
        "counts_inspected": False,
        "actual_source_identity_verified": False,
        "scientific_validity_established": False,
        "trial_performed": False,
        "biological_agreement": "not_run",
        "authorization_scope": "no_execution_or_model_authority_granted",
    }
