"""Bounded evidence capture from the inputs and factors used by a fit."""

import math
import re

MAX_GENES = 100_000
MAX_SAMPLES = 1_000
MAX_MATRIX_VALUES = 1_000_000


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 2000


def _unpaired(reason, unit_column=None, condition_column=None):
    return {
        "status": "unpaired",
        "reason": reason,
        "unit_column": unit_column,
        "condition_column": condition_column,
    }


def capture_bulk_plot_evidence(data, size_factors, calibration, *, design=None):
    """Copy bounded evidence at a completed fit; invalid evidence stays unavailable.

    ``data`` is the actual ObservedData analysis view, including any recorded
    technical aggregation. Factors are supplied by the existing calibration.
    This helper deliberately does not raise for unavailable plotting evidence.
    """
    gene_count, sample_count = len(data.gene_ids), len(data.sample_ids)
    evidence = {
        "format": "devbench.bulk-plot-evidence/1",
        "status": "unavailable",
        "reason": None,
        "gene_count": gene_count,
        "sample_count": sample_count,
        "matrix_value_count": gene_count * sample_count,
        "truncated": False,
        "gene_ids": None,
        "sample_ids": None,
        "raw_counts": None,
        "size_factors": None,
        "mean_normalized_counts": None,
        "samples": [],
        "pairing": _unpaired("plot_evidence_unavailable"),
    }
    try:
        if (
            gene_count > MAX_GENES
            or sample_count > MAX_SAMPLES
            or gene_count * sample_count > MAX_MATRIX_VALUES
        ):
            raise ValueError("plot_evidence_exceeds_bounded_capture_budget")
        if data.measurement == "continuous":
            raise ValueError("raw_count_plot_evidence_unavailable_for_continuous_input")
        if not gene_count or not sample_count:
            raise ValueError("empty_expression_evidence")
        values, factors, sample_ids, gene_ids, means = _capture_count_arrays(
            data, size_factors, design, gene_count, sample_count,
        )
        samples, pairing = _capture_memberships(data, design, sample_ids, factors)
        evidence.update(
            status="available",
            reason=None,
            gene_ids=gene_ids,
            sample_ids=sample_ids,
            raw_counts=values.tolist(),
            size_factors=factors.tolist(),
            mean_normalized_counts=means.tolist(),
            samples=samples,
            pairing=pairing,
        )
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as error:
        reason = str(error)
        evidence["reason"] = (
            reason
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,159}", reason)
            else "plot_evidence_capture_invalid"
        )
    return evidence


def _capture_count_arrays(data, size_factors, design, gene_count, sample_count):
    import numpy as np

    values, factors = (
        np.asarray(data.expression),
        np.asarray(size_factors, dtype=float),
    )
    sample_ids, gene_ids = list(data.sample_ids), list(data.gene_ids)
    if (
        len(set(sample_ids)) != sample_count
        or len(set(gene_ids)) != gene_count
        or not all(_text(item) for item in sample_ids + gene_ids)
    ):
        raise ValueError("plot_evidence_invalid_identity")
    if list(data.metadata.index) != sample_ids:
        raise ValueError("sample_metadata_alignment_mismatch")
    if design is not None and list(design.index) != sample_ids:
        raise ValueError("sample_design_alignment_mismatch")
    if values.shape != (gene_count, sample_count):
        raise ValueError("plot_evidence_matrix_shape_mismatch")
    if (
        not np.isfinite(values).all()
        or (values < 0).any()
        or (values > 2**53 - 1).any()
        or not (values == np.floor(values)).all()
    ):
        raise ValueError("plot_evidence_requires_exact_nonnegative_counts")
    if (
        factors.shape != (sample_count,)
        or not np.isfinite(factors).all()
        or (factors <= 0).any()
    ):
        raise ValueError("plot_evidence_requires_positive_saved_size_factors")
    normalized = values / factors[None, :]
    means = normalized.mean(axis=1)
    if not np.isfinite(normalized).all() or not np.isfinite(means).all():
        raise ValueError("normalized_plot_evidence_nonfinite")
    return values, factors, sample_ids, gene_ids, means


def _capture_memberships(data, design, sample_ids, factors):
    resolved = (
        design.attrs.get("bulk_plot_metadata") if design is not None else None
    )
    resolved = (
        resolved
        if isinstance(resolved, dict)
        and resolved.get("format") == "devbench.bulk-plot-design/1"
        else None
    )
    roles = (data.context.get("analysis_columns") or {}) if design is None else {}
    condition_column = (
        resolved.get("condition_column") if resolved else roles.get("condition")
    )
    blocks = (
        resolved.get("block_columns", []) if resolved else roles.get("blocking", [])
    )
    if condition_column not in data.metadata:
        condition_column = None
    unit_column = data.unit_hierarchy.get("population_unit_label")
    identity_declared = (
        unit_column in data.metadata
        and data.unit_hierarchy.get("identity_status")
        not in {"unknown", "intentionally_unknown", "unverified"}
        and unit_column != data.unit_hierarchy.get("library_label")
        and data.unit_hierarchy.get("population_unit")
        not in {"library", "observed_library", "cell"}
    )

    def identity(value):
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            return None
        value = str(value)
        return (
            value
            if _text(value)
            and value.lower()
            not in {"unknown", "na", "nan", "none", "unidentified"}
            else None
        )

    samples = [
        {
            "sample_id": sample_id,
            "condition": identity(data.metadata.iloc[index][condition_column])
            if condition_column
            else None,
            "unit_id": identity(data.metadata.iloc[index][unit_column])
            if identity_declared
            else None,
            "pair_id": None,
            "size_factor": float(factors[index]),
        }
        for index, sample_id in enumerate(sample_ids)
    ]
    declared_pair = isinstance(blocks, list) and unit_column in blocks
    pairing = _unpaired(
        "resolved_design_plot_metadata_unavailable"
        if design is not None and resolved is None
        else "pairing_not_explicitly_declared",
        unit_column,
        condition_column,
    )
    if declared_pair:
        conditions = {sample["condition"] for sample in samples}
        groups = {}
        for sample in samples:
            groups.setdefault(sample["unit_id"], []).append(sample["condition"])
        if not identity_declared or None in groups:
            pairing["reason"] = "declared_biological_unit_identity_unavailable"
        elif None in conditions or len(conditions) != 2:
            pairing["reason"] = "pairing_requires_two_recorded_conditions"
        elif any(
            len(group) != 2 or set(group) != conditions for group in groups.values()
        ):
            pairing["reason"] = "incomplete_or_repeated_unit_condition_membership"
        else:
            pairing.update(status="paired", reason=None)
            for sample in samples:
                sample["pair_id"] = sample["unit_id"]
    return samples, pairing
