"""Bounded, saved bulk evidence and descriptive plotting projections.

The capture hook copies the counts and factors actually used by a bulk fit.
The workspace never fits a model or recalculates normalization, effects, p/q,
or dispersion shrinkage. PCA is an explicitly versioned descriptive transform.
Source custody and receipt verification belong to the calling artifact service.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from copy import deepcopy

from devbench.core.bulk_fit_evidence import (
    MAX_GENES,
    MAX_MATRIX_VALUES,
    MAX_SAMPLES,
    _text,
    _unpaired,
)
from devbench.core.bulk_fit_evidence import (
    capture_bulk_plot_evidence as capture_bulk_plot_evidence,  # noqa: F401
)

_KINDS = (
    "ma",
    "volcano",
    "dispersion",
    "pca",
    "paired_expression",
    "pvalue_histogram",
    "expression",
)
_PCA_PRODUCER = "devbench.log2-normalized-counts-pca/1"
_PCA_TRANSFORM = (
    "log2(raw_counts / saved_size_factor + 1); gene-centered across "
    "samples; no variance scaling"
)
_LIMITATIONS = [
    "Saved plotting evidence does not establish biological or clinical validity.",
    "Effects, intervals and p/q values are copied from the saved "
    "result; no statistical refitting or recalibration.",
    "PCA is descriptive log2 normalized-count geometry, not a VST or rlog transform.",
    "Recorded biological identities are supplied declarations; unique "
    "sample IDs do not establish pairing.",
]


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _json_value(value):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("bulk_plot_requires_finite_JSON") from error


def _closed(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError("bulk_plot_requires_declared_fields")


def _available(available, reason):
    return {
        "status": "available" if available else "unavailable",
        "reason": None if available else reason,
    }


def _pca(genes, samples, reason):
    pca = {
        "status": "unavailable",
        "reason": reason,
        "producer": _PCA_PRODUCER,
        "transform": _PCA_TRANSFORM,
        "scores": [],
        "explained_variance_ratio": [],
        "explained_variance": [],
    }
    if not samples or not genes or any(gene["raw_counts"] is None for gene in genes):
        return pca
    if len(samples) < 2 or len(genes) < 2:
        pca["reason"] = "pca_requires_at_least_two_samples_and_genes"
        return pca
    import numpy as np

    matrix = np.asarray([gene["raw_counts"] for gene in genes], dtype=float)
    factors = np.asarray([sample["size_factor"] for sample in samples], dtype=float)
    transformed = np.log2(matrix / factors[None, :] + 1.0)
    centered = transformed - transformed.mean(axis=1, keepdims=True)
    total = float(np.square(centered).sum())
    if total <= 0:
        pca["reason"] = "pca_has_no_between_sample_variation"
        return pca
    try:
        left, singular, right = np.linalg.svd(centered.T, full_matrices=False)
    except np.linalg.LinAlgError:
        pca["reason"] = "descriptive_pca_svd_failed"
        return pca
    scores = left[:, :2] * singular[None, :2]
    for axis in range(2):
        # Stable sign convention: first largest-magnitude gene loading is positive.
        if right[axis, int(np.argmax(np.abs(right[axis])))] < 0:
            scores[:, axis] *= -1
    variance = np.square(singular[:2]) / (len(samples) - 1)
    ratios = np.square(singular[:2]) / total
    pca.update(
        status="available",
        reason=None,
        scores=[
            {
                "sample_id": sample["sample_id"],
                "pc1": float(scores[index, 0]),
                "pc2": float(scores[index, 1]),
            }
            for index, sample in enumerate(samples)
        ],
        explained_variance=variance.tolist(),
        explained_variance_ratio=ratios.tolist(),
    )
    return pca


def _result_payload(result):
    if type(result) is not dict:
        raise ValueError("bulk_plot_saved_result_required")
    if result.get("kind") == "effects":
        return result, {}
    targets = result.get("target_results", [])
    payloads = [
        target["payload"]
        for target in targets
        if type(target) is dict
        and type(target.get("payload")) is dict
        and target["payload"].get("kind") == "effects"
    ]
    diagnostics = result.get("diagnostics", {})
    if not payloads:
        unavailable = dict(diagnostics.get("failed_gene_statistics", {}))
        unavailable.update(diagnostics.get("unavailable_target_records", {}))
        if len(unavailable) == 1:
            records = next(iter(unavailable.values()))
            return {
                "kind": "effects",
                "gene_statistics": records,
                "biological_unit": diagnostics.get("biological_units", {}).get(
                    "population_unit"
                ),
            }, diagnostics
    if len(payloads) != 1:
        raise ValueError("bulk_plot_requires_one_saved_effect_family")
    return payloads[0], diagnostics


def _dispersion(calibration, gene_id):
    saved = calibration.get("gene_estimates", {}).get(gene_id, {})
    final = saved.get("dispersion_alpha")
    boundary = None
    if final is not None and _finite(final):
        if _finite(calibration.get("min_disp")) and final <= calibration["min_disp"]:
            boundary = "lower"
        elif _finite(calibration.get("max_disp")) and final >= calibration["max_disp"]:
            boundary = "upper"
    return {
        "raw": saved.get("genewise_dispersion"),
        "trend": saved.get("trend_dispersion"),
        "final": final,
        "converged": saved.get("dispersion_converged"),
        "boundary": boundary,
        "outlier": saved.get("dispersion_outlier"),
    }


def _validate_count_alignment(evidence):
    for key, count in (
        ("gene_ids", evidence["gene_count"]),
        ("sample_ids", evidence["sample_count"]),
        ("raw_counts", evidence["gene_count"]),
        ("size_factors", evidence["sample_count"]),
        ("mean_normalized_counts", evidence["gene_count"]),
        ("samples", evidence["sample_count"]),
    ):
        if type(evidence[key]) is not list or len(evidence[key]) != count:
            raise ValueError("bulk_plot_saved_evidence_alignment_mismatch")
    if any(
        not _finite(value) or value <= 0 for value in evidence["size_factors"]
    ) or any(
        not _finite(value) or value < 0 for value in evidence["mean_normalized_counts"]
    ):
        raise ValueError("bulk_plot_invalid_saved_factor_or_mean")
    for row in evidence["raw_counts"]:
        if (
            type(row) is not list
            or len(row) != evidence["sample_count"]
            or any(
                not _finite(value)
                or value < 0
                or value > 2**53 - 1
                or value != int(value)
                for value in row
            )
            or any(
                not math.isfinite(value / factor)
                for value, factor in zip(row, evidence["size_factors"], strict=False)
            )
        ):
            raise ValueError("bulk_plot_saved_count_matrix_invalid")
    for sample in evidence["samples"]:
        _closed(sample, {"sample_id", "condition", "unit_id", "pair_id", "size_factor"})


def _validate_saved_evidence(evidence):
    if not evidence:
        return
    _closed(
        evidence,
        {
            "format",
            "status",
            "reason",
            "gene_count",
            "sample_count",
            "matrix_value_count",
            "truncated",
            "gene_ids",
            "sample_ids",
            "raw_counts",
            "size_factors",
            "mean_normalized_counts",
            "samples",
            "pairing",
        },
    )
    if (
        evidence["format"] != "devbench.bulk-plot-evidence/1"
        or evidence["truncated"] is not False
        or any(
            type(evidence[key]) is not int or evidence[key] < 0
            for key in ("gene_count", "sample_count", "matrix_value_count")
        )
        or evidence["matrix_value_count"]
        != evidence["gene_count"] * evidence["sample_count"]
    ):
        raise ValueError("bulk_plot_saved_evidence_extent_invalid")
    if evidence["status"] == "unavailable":
        if (
            not _text(evidence["reason"])
            or evidence["samples"]
            or any(
                evidence[key] is not None
                for key in (
                    "gene_ids",
                    "sample_ids",
                    "raw_counts",
                    "size_factors",
                    "mean_normalized_counts",
                )
            )
        ):
            raise ValueError(
                "bulk_plot_unavailable_evidence_cannot_carry_partial_matrix"
            )
        return
    if (
        evidence["status"] != "available"
        or evidence["reason"] is not None
        or not 1 <= evidence["gene_count"] <= MAX_GENES
        or not 1 <= evidence["sample_count"] <= MAX_SAMPLES
        or evidence["matrix_value_count"] > MAX_MATRIX_VALUES
    ):
        raise ValueError("bulk_plot_saved_evidence_exceeds_bounds")
    _validate_count_alignment(evidence)


def _availability(workspace, evidence_reason):
    genes, samples, contrast = (
        workspace["genes"],
        workspace["samples"],
        workspace["effect_definition"],
    )
    effect = any(
        gene["estimate_status"] == "estimated" and gene["effect"] is not None
        for gene in genes
    )
    scalar_reason = (
        "no_saved_scalar_effect_with_structured_contrast"
        if not effect or contrast is None
        else evidence_reason
    )
    conditions = {sample["condition"] for sample in samples}
    supported_conditions = (
        {contrast["reference_condition"], contrast["comparison_condition"]}
        if contrast
        else set()
    )
    expression = (
        bool(samples)
        and all(gene["raw_counts"] is not None for gene in genes)
        and contrast is not None
        and conditions == supported_conditions
    )
    expression_reason = (
        "recorded_conditions_missing_or_outside_computed_contrast"
        if samples and contrast is not None and conditions != supported_conditions
        else scalar_reason
    )
    return {
        "ma": _available(
            contrast is not None
            and any(
                gene["effect"] is not None and gene["mean_normalized_count"] is not None
                for gene in genes
            ),
            scalar_reason,
        ),
        "volcano": _available(
            contrast is not None
            and any(
                gene["effect"] is not None and gene["q_value"] is not None
                for gene in genes
            ),
            "saved_scalar_effect_or_q_value_or_contrast_unavailable",
        ),
        "dispersion": _available(
            any(
                gene["mean_normalized_count"] is not None
                and gene["mean_normalized_count"] > 0
                and gene["dispersion"]["final"] is not None
                and gene["dispersion"]["final"] > 0
                for gene in genes
            ),
            evidence_reason,
        ),
        "pca": _available(
            workspace["pca"]["status"] == "available", workspace["pca"]["reason"]
        ),
        "paired_expression": _available(
            expression and workspace["pairing"]["status"] == "paired",
            workspace["pairing"]["reason"] or expression_reason,
        ),
        "pvalue_histogram": _available(
            any(gene["p_value"] is not None for gene in genes),
            "saved_p_values_unavailable",
        ),
        "expression": _available(expression, expression_reason),
    }


def _add_count_warnings(counts, contrast, samples, warnings):
    if counts is not None:
        nonzero = sum(value > 0 for value in counts)
        if nonzero == 0:
            warnings.append("all_zero_counts")
        elif nonzero == 1:
            warnings.append("counts_in_one_sample_only")
        if contrast:
            for role in ("reference", "comparison"):
                positions = [
                    i
                    for i, sample in enumerate(samples)
                    if sample["condition"] == contrast[role + "_condition"]
                ]
                if positions and all(counts[i] == 0 for i in positions):
                    warnings.append("zero_counts_in_" + role + "_condition")


def _workspace_genes(
    rows,
    diagnostics,
    calibration_diagnostics,
    evidence_index,
    evidence,
    calibration,
    contrast,
    samples,
):
    genes = []
    for row in rows:
        gene_id = row.get("gene_id")
        values, intervals = row.get("values", []), row.get("intervals", [])
        effect = values[0] if len(values) == 1 else None
        warnings = list(row.get("warnings", [])) + list(
            diagnostics.get("model_warnings", {}).get(gene_id, [])
        )
        warnings.extend(
            item["code"]
            for item in calibration_diagnostics
            if gene_id in item.get("gene_ids", []) and _text(item.get("code"))
        )
        if len(values) > 1:
            warnings.append("non_scalar_effect_requires_coordinate_selection")
        interval = None
        if effect is not None and len(intervals) == 1 and intervals[0] is not None:
            saved_interval = intervals[0]
            interval = {
                "lower": saved_interval["lower"],
                "upper": saved_interval["upper"],
                "level": saved_interval["confidence_level"],
                "kind": "confidence",
            }
        index = evidence_index.get(gene_id)
        counts = deepcopy(evidence["raw_counts"][index]) if index is not None else None
        mean = evidence["mean_normalized_counts"][index] if index is not None else None
        dispersion = _dispersion(calibration, gene_id)
        if dispersion["boundary"]:
            warnings.append("dispersion_at_" + dispersion["boundary"] + "_boundary")
        if dispersion["converged"] is False:
            warnings.append("dispersion_not_converged")
        if dispersion["outlier"] is True:
            warnings.append("dispersion_outlier")
        _add_count_warnings(counts, contrast, samples, warnings)
        genes.append(
            {
                "gene_id": gene_id,
                "hypothesis_id": row.get("hypothesis_id"),
                "effect": effect,
                "interval": interval,
                "p_value": row.get("p_value"),
                "q_value": row.get("q_value"),
                "estimate_status": row.get("estimate_status"),
                "missing_reason": row.get("missing_reason"),
                "warnings": list(dict.fromkeys(warnings)),
                "mean_normalized_count": mean,
                "dispersion": dispersion,
                "raw_counts": counts,
            }
        )
    return genes


def build_bulk_plot_workspace(
    result: dict, *, source: dict, public=False, descriptor=None
) -> dict:
    """Create a full-family workspace from one saved bulk effects result.

    ``source`` must include caller-verified sha256, run_id and retained/synthetic
    kind; its other JSON metadata is copied unchanged. No files are opened.
    """
    payload, diagnostics = _result_payload(result)
    rows = payload.get("gene_statistics")
    if type(rows) is not list or not rows or len(rows) > MAX_GENES:
        raise ValueError("bulk_plot_gene_family_empty_or_exceeds_100000_limit")
    family = diagnostics.get("gene_test_adjustment_family", {})
    if "attempted_hypothesis_ids" in family and (
        set(family["attempted_hypothesis_ids"])
        != {row.get("hypothesis_id") for row in rows}
        or len(family["attempted_hypothesis_ids"]) != len(rows)
    ):
        raise ValueError("bulk_plot_saved_hypothesis_family_incomplete")
    evidence = diagnostics.get("plot_evidence", {})
    _validate_saved_evidence(evidence)
    _json_value((result, source))
    evidence_reason = (
        evidence.get("reason") or "saved_aligned_count_evidence_unavailable"
    )
    evidence_available = evidence.get("status") == "available"
    evidence_ids = evidence.get("gene_ids") if evidence_available else []
    if evidence_available:
        if (
            evidence.get("format") != "devbench.bulk-plot-evidence/1"
            or type(evidence_ids) is not list
            or len(evidence_ids) != len(rows)
            or len(set(evidence_ids)) != len(evidence_ids)
            or set(evidence_ids) != {row.get("gene_id") for row in rows}
            or len(evidence.get("raw_counts", [])) != len(evidence_ids)
            or len(evidence.get("mean_normalized_counts", [])) != len(evidence_ids)
            or evidence.get("sample_ids")
            != [sample.get("sample_id") for sample in evidence.get("samples", [])]
            or evidence.get("size_factors")
            != [sample.get("size_factor") for sample in evidence.get("samples", [])]
        ):
            raise ValueError("bulk_plot_saved_evidence_alignment_mismatch")
    evidence_index = {gene: index for index, gene in enumerate(evidence_ids)}
    calibration = diagnostics.get("calibration", {})
    calibration_diagnostics = diagnostics.get("calibration_diagnostics", [])
    contrast = deepcopy(payload.get("computed_contrast"))
    samples = deepcopy(evidence.get("samples", [])) if evidence_available else []
    genes = _workspace_genes(
        rows,
        diagnostics,
        calibration_diagnostics,
        evidence_index,
        evidence,
        calibration,
        contrast,
        samples,
    )
    pairing = (
        deepcopy(evidence["pairing"])
        if evidence_available
        else _unpaired(evidence_reason)
    )
    limitations = list(_LIMITATIONS)
    limitations.extend(
        "Saved calibration warning: " + warning
        for warning in diagnostics.get("calibration_warnings", [])
    )
    limitations.extend(
        "Saved calibration diagnostic: "
        + item["code"]
        + (". " + item["message"] if item.get("message") else "")
        for item in calibration_diagnostics
        if not item.get("gene_ids") and _text(item.get("code"))
    )
    if payload.get("selection_conditional_inference"):
        limitations.append(
            "Saved intervals and tests are conditional on model "
            "selection or empirical-Bayes plug-in estimates."
        )
    workspace = {
        "format": "devbench.bulk-plot-workspace/1",
        "source": deepcopy(source),
        "effect_definition": contrast,
        "effect_scale": contrast.get("effect_scale") if contrast else None,
        "biological_unit": payload.get("biological_unit"),
        "genes": genes,
        "samples": samples,
        "pca": _pca(genes, samples, evidence_reason),
        "pairing": pairing,
        "availability": {},
        "coverage": {
            "gene_count": len(genes),
            "sample_count": len(samples),
            "matrix_value_count": len(genes) * len(samples),
            "retained_gene_count": len(genes),
            "truncated": False,
            "evidence_gene_count": evidence.get("gene_count"),
            "evidence_sample_count": evidence.get("sample_count"),
            "evidence_matrix_value_count": evidence.get("matrix_value_count"),
        },
        "limitations": list(dict.fromkeys(limitations)),
    }
    if public:
        _publish_bulk_workspace(workspace, descriptor)
        evidence_reason = "not_admitted_for_public_presentation"
    workspace["availability"] = _availability(workspace, evidence_reason)
    validate_bulk_plot_workspace(workspace)
    if public:
        from .bulk_plot_service import _seal_public_geometry

        _seal_public_geometry(workspace)
    return workspace


def _publish_bulk_workspace(workspace, descriptor):
    """Preserve the schema and full family while withholding source measurements."""
    from .bulk_plot_service import _publication_label, _publication_source

    if descriptor is not None and (
        descriptor.source_sha256 != workspace["source"]["sha256"]
        or "publication_source_withheld" in descriptor.reasons
    ):
        raise PermissionError("publication_source_withheld")
    allow_effect = descriptor is None or descriptor.allow_descriptive
    allow_checked = descriptor is None or descriptor.allow_checked
    workspace["source"] = _publication_source(workspace["source"])
    workspace["limitations"] = [
        "Saved descriptive effects and coverage. Presentation does not rerun a fit."
    ]
    contrast = workspace["effect_definition"]
    if contrast is not None:
        for name in ("reference_condition", "comparison_condition"):
            _publication_label(contrast[name])
    if workspace["biological_unit"] is not None:
        _publication_label(workspace["biological_unit"])
    workspace["genes"].sort(key=lambda row: row["gene_id"])
    for gene in workspace["genes"]:
        _publication_label(gene["gene_id"])
        _publication_label(gene["hypothesis_id"])
        gene["raw_counts"] = None
        gene["warnings"] = []
        gene["missing_reason"] = (
            "saved_estimate_unavailable"
            if gene["estimate_status"] != "estimated"
            else None
        )
        if not allow_effect:
            gene["effect"] = None
        # Legacy rows have no per-family publication proof. The fixed typed
        # table owns inferential projection even if another family is eligible.
        gene.update(p_value=None, q_value=None, interval=None)
        if not allow_checked:
            gene["mean_normalized_count"] = None
        if not allow_effect:
            gene["dispersion"] = {key: None for key in gene["dispersion"]}
    workspace["samples"] = []
    workspace["pairing"] = _unpaired("source_measurements_require_separate_access")
    workspace["pca"] = _pca(
        workspace["genes"], [], "source_measurements_require_separate_access"
    )
    workspace["coverage"].update(sample_count=0, matrix_value_count=0)


def _validate_effect_definition(workspace):
    contrast = workspace["effect_definition"]
    if contrast is not None:
        _closed(
            contrast,
            {
                "operation",
                "reference_condition",
                "comparison_condition",
                "effect_scale",
                "normalization",
            },
        )
        if (
            contrast["operation"] != "comparison_minus_reference"
            or not all(
                _text(contrast[key])
                for key in ("reference_condition", "comparison_condition")
            )
            or contrast["reference_condition"] == contrast["comparison_condition"]
            or (contrast["effect_scale"], contrast["normalization"])
            not in {
                ("log2_relative_expression", "RLE_library_size_offsets"),
                ("unchanged_input_expression", "unchanged_input"),
            }
            or workspace["effect_scale"] != contrast["effect_scale"]
        ):
            raise ValueError("bulk_plot_computed_contrast_invalid")
    elif workspace["effect_scale"] is not None:
        raise ValueError("bulk_plot_effect_scale_requires_computed_contrast")
    if workspace["biological_unit"] is not None and not _text(
        workspace["biological_unit"]
    ):
        raise ValueError("bulk_plot_biological_unit_invalid")


def _validated_sample_ids(samples):
    sample_ids = []
    for sample in samples:
        _closed(sample, {"sample_id", "condition", "unit_id", "pair_id", "size_factor"})
        if (
            not _text(sample["sample_id"])
            or not _finite(sample["size_factor"])
            or sample["size_factor"] <= 0
            or any(
                sample[key] is not None and not _text(sample[key])
                for key in ("condition", "unit_id", "pair_id")
            )
            or (
                sample["pair_id"] is not None and sample["pair_id"] != sample["unit_id"]
            )
        ):
            raise ValueError("bulk_plot_sample_identity_or_factor_invalid")
        sample_ids.append(sample["sample_id"])
    return sample_ids


def _validate_gene_estimate(gene):
    if gene["estimate_status"] not in {
        "estimated",
        "failed",
        "unavailable",
        "refused",
    }:
        raise ValueError("bulk_plot_invalid_estimate_status")
    if gene["missing_reason"] is not None and not _text(gene["missing_reason"]):
        raise ValueError("bulk_plot_invalid_missing_reason")
    if type(gene["warnings"]) is not list or not all(
        _text(item) for item in gene["warnings"]
    ):
        raise ValueError("bulk_plot_invalid_warnings")
    for field in ("effect", "p_value", "q_value", "mean_normalized_count"):
        value = gene[field]
        if value is not None and (
            not _finite(value)
            or (field in {"p_value", "q_value"} and not 0 <= value <= 1)
            or (field == "mean_normalized_count" and value < 0)
        ):
            raise ValueError("bulk_plot_invalid_saved_number")
    if gene["estimate_status"] != "estimated" and any(
        gene[key] is not None for key in ("effect", "interval", "p_value", "q_value")
    ):
        raise ValueError("bulk_plot_failed_gene_cannot_carry_estimate")
    if gene["q_value"] is not None and (
        gene["p_value"] is None or gene["q_value"] < gene["p_value"]
    ):
        raise ValueError("bulk_plot_invalid_p_q_order")
    interval = gene["interval"]
    if interval is not None:
        _closed(interval, {"lower", "upper", "level", "kind"})
        if (
            not all(_finite(interval[key]) for key in ("lower", "upper", "level"))
            or interval["kind"] not in {"confidence", "credible", "range"}
            or not 0 < interval["level"] < 1
            or gene["effect"] is None
            or not interval["lower"] <= gene["effect"] <= interval["upper"]
        ):
            raise ValueError("bulk_plot_invalid_saved_interval")


def _validate_gene_counts(gene, samples):
    dispersion = gene["dispersion"]
    _closed(dispersion, {"raw", "trend", "final", "converged", "boundary", "outlier"})
    if (
        any(
            dispersion[key] is not None
            and (not _finite(dispersion[key]) or dispersion[key] < 0)
            for key in ("raw", "trend", "final")
        )
        or any(
            dispersion[key] is not None and type(dispersion[key]) is not bool
            for key in ("converged", "outlier")
        )
        or dispersion["boundary"] not in {None, "lower", "upper"}
    ):
        raise ValueError("bulk_plot_invalid_saved_dispersion")
    counts = gene["raw_counts"]
    if counts is not None and (
        type(counts) is not list
        or len(counts) != len(samples)
        or not samples
        or any(
            not _finite(value) or value < 0 or value > 2**53 - 1 or value != int(value)
            for value in counts
        )
    ):
        raise ValueError("bulk_plot_raw_count_alignment_or_value_invalid")
    if bool(samples) != (counts is not None) or (
        counts is not None and gene["mean_normalized_count"] is None
    ):
        raise ValueError("bulk_plot_partial_count_evidence_rejected")


def _validate_pairing(pairing, samples):
    _closed(pairing, {"status", "reason", "unit_column", "condition_column"})
    if pairing["status"] not in {"paired", "unpaired"}:
        raise ValueError("bulk_plot_invalid_pairing_state")
    if pairing["status"] == "paired":
        if (
            not samples
            or pairing["reason"] is not None
            or not all(
                _text(pairing[key]) for key in ("unit_column", "condition_column")
            )
        ):
            raise ValueError("bulk_plot_pairing_identity_required")
        groups: dict[str, list[str]] = {}
        for sample in samples:
            if sample["pair_id"] is None or sample["condition"] is None:
                raise ValueError("bulk_plot_pairing_identity_required")
            groups.setdefault(sample["pair_id"], []).append(sample["condition"])
        conditions = {sample["condition"] for sample in samples}
        if len(conditions) != 2 or any(
            len(group) != 2 or set(group) != conditions for group in groups.values()
        ):
            raise ValueError("bulk_plot_incomplete_pairing")
    elif not _text(pairing["reason"]) or any(
        sample["pair_id"] is not None for sample in samples
    ):
        raise ValueError("bulk_plot_unpaired_reason_required")


def _validate_saved_pca(pca, sample_ids):
    _closed(
        pca,
        {
            "status",
            "reason",
            "producer",
            "transform",
            "scores",
            "explained_variance",
            "explained_variance_ratio",
        },
    )
    if pca["producer"] != _PCA_PRODUCER or pca["transform"] != _PCA_TRANSFORM:
        raise ValueError("bulk_plot_pca_producer_or_transform_invalid")
    if pca["status"] == "available":
        if (
            pca["reason"] is not None
            or [row.get("sample_id") for row in pca["scores"]] != sample_ids
        ):
            raise ValueError("bulk_plot_pca_sample_alignment_invalid")
        for row in pca["scores"]:
            _closed(row, {"sample_id", "pc1", "pc2"})
            if not _finite(row["pc1"]) or not _finite(row["pc2"]):
                raise ValueError("bulk_plot_pca_nonfinite_scores")
        for field in ("explained_variance", "explained_variance_ratio"):
            if (
                type(pca[field]) is not list
                or len(pca[field]) != 2
                or any(not _finite(value) or value < 0 for value in pca[field])
            ):
                raise ValueError("bulk_plot_pca_invalid_variance")
        if sum(pca["explained_variance_ratio"]) > 1 + 1e-10:
            raise ValueError("bulk_plot_pca_invalid_variance_ratio")
    elif (
        pca["status"] != "unavailable"
        or not _text(pca["reason"])
        or pca["scores"]
        or pca["explained_variance"]
        or pca["explained_variance_ratio"]
    ):
        raise ValueError("bulk_plot_pca_unavailable_reason_required")


def _validate_availability(workspace):
    _closed(workspace["availability"], _KINDS)
    for value in workspace["availability"].values():
        _closed(value, {"status", "reason"})
        if value["status"] == "available":
            if value["reason"] is not None:
                raise ValueError("bulk_plot_available_panel_has_reason")
        elif value["status"] != "unavailable" or not _text(value["reason"]):
            raise ValueError("bulk_plot_unavailable_panel_requires_reason")
    expected_availability = _availability(workspace, "unavailable")
    if any(
        workspace["availability"][kind]["status"]
        != expected_availability[kind]["status"]
        for kind in _KINDS
    ):
        raise ValueError("bulk_plot_panel_availability_inconsistent")


def _validate_coverage(coverage, genes, samples):
    _closed(
        coverage,
        {
            "gene_count",
            "sample_count",
            "matrix_value_count",
            "retained_gene_count",
            "truncated",
            "evidence_gene_count",
            "evidence_sample_count",
            "evidence_matrix_value_count",
        },
    )
    if any(
        coverage[key] is not None
        and (type(coverage[key]) is not int or coverage[key] < 0)
        for key in (
            "evidence_gene_count",
            "evidence_sample_count",
            "evidence_matrix_value_count",
        )
    ):
        raise ValueError("bulk_plot_coverage_evidence_extent_invalid")
    extents = [
        coverage[key]
        for key in (
            "evidence_gene_count",
            "evidence_sample_count",
            "evidence_matrix_value_count",
        )
    ]
    if any(value is not None for value in extents) and (
        any(value is None for value in extents)
        or extents[0] != len(genes)
        or extents[2] != extents[0] * extents[1]
        or (samples and extents[1] != len(samples))
    ):
        raise ValueError("bulk_plot_coverage_evidence_extent_mismatch")
    if {
        key: coverage[key]
        for key in (
            "gene_count",
            "sample_count",
            "matrix_value_count",
            "retained_gene_count",
            "truncated",
        )
    } != {
        "gene_count": len(genes),
        "sample_count": len(samples),
        "matrix_value_count": len(genes) * len(samples),
        "retained_gene_count": len(genes),
        "truncated": False,
    }:
        raise ValueError("bulk_plot_coverage_mismatch")


def validate_bulk_plot_workspace(workspace: dict) -> dict:
    """Validate the closed workspace shape, full membership, and numeric states."""
    _json_value(workspace)
    _closed(
        workspace,
        {
            "format",
            "source",
            "effect_definition",
            "effect_scale",
            "biological_unit",
            "genes",
            "samples",
            "pca",
            "pairing",
            "availability",
            "coverage",
            "limitations",
        },
    )
    if workspace["format"] != "devbench.bulk-plot-workspace/1":
        raise ValueError("bulk_plot_unknown_workspace_format")
    source = workspace["source"]
    if (
        type(source) is not dict
        or not _text(source.get("run_id"))
        or source.get("kind") not in {"retained", "synthetic"}
        or type(source.get("sha256")) is not str
        or re.fullmatch("[0-9a-f]{64}", source["sha256"]) is None
    ):
        raise ValueError("bulk_plot_source_identity_required")
    _validate_effect_definition(workspace)
    genes, samples = workspace["genes"], workspace["samples"]
    if (
        type(genes) is not list
        or not 1 <= len(genes) <= MAX_GENES
        or type(samples) is not list
        or len(samples) > MAX_SAMPLES
        or len(genes) * len(samples) > MAX_MATRIX_VALUES
    ):
        raise ValueError("bulk_plot_workspace_exceeds_bounds")
    gene_ids, hypotheses = [], []
    sample_ids = _validated_sample_ids(samples)
    for gene in genes:
        _closed(
            gene,
            {
                "gene_id",
                "hypothesis_id",
                "effect",
                "interval",
                "p_value",
                "q_value",
                "estimate_status",
                "missing_reason",
                "warnings",
                "mean_normalized_count",
                "dispersion",
                "raw_counts",
            },
        )
        if not _text(gene["gene_id"]) or not _text(gene["hypothesis_id"]):
            raise ValueError("bulk_plot_gene_identity_required")
        gene_ids.append(gene["gene_id"])
        hypotheses.append(gene["hypothesis_id"])
        _validate_gene_estimate(gene)
        _validate_gene_counts(gene, samples)
    if any(len(ids) != len(set(ids)) for ids in (gene_ids, hypotheses, sample_ids)):
        raise ValueError("bulk_plot_duplicate_identity")
    _validate_pairing(workspace["pairing"], samples)
    _validate_saved_pca(workspace["pca"], sample_ids)
    _validate_availability(workspace)
    _validate_coverage(workspace["coverage"], genes, samples)
    if type(workspace["limitations"]) is not list or not all(
        _text(item) for item in workspace["limitations"]
    ):
        raise ValueError("bulk_plot_limitations_required")
    return workspace


def _axis(field, label, unit, transform="identity"):
    return {
        "field": field,
        "label": label,
        "unit": unit,
        "transform": transform,
        "direction": "increasing",
        "range_policy": "saved_full_range_no_clipping",
    }


def _value(value):
    return {
        "status": "missing" if value is None else "exact",
        "value": value,
        "censoring": None,
    }


def _gene_scatter_axes(kind, scale):
    if kind == "ma":
        axes = {
            "x": _axis(
                "mean_normalized_count",
                "ln(1 + mean normalized count)",
                "normalized_count",
                "natural_log1p",
            ),
            "y": _axis("effect", "Saved effect", scale),
        }
    elif kind == "volcano":
        axes = {
            "x": _axis("effect", "Saved effect", scale),
            "y": _axis("q_value", "-log10(saved q-value)", "q_value", "negative_log10"),
        }
    else:
        axes = {
            "x": _axis(
                "mean_normalized_count",
                "log10(mean normalized count)",
                "normalized_count",
                "log10",
            ),
            "y": _axis(
                "final_dispersion",
                "log10(saved final dispersion)",
                "negative_binomial_alpha",
                "log10",
            ),
        }
    return axes


def _append_gene_scatter(genes, kind, append):
    for gene in genes:
        reasons = []
        x = gene["effect"] if kind == "volcano" else gene["mean_normalized_count"]
        y = (
            gene["effect"]
            if kind == "ma"
            else gene["q_value"]
            if kind == "volcano"
            else gene["dispersion"]["final"]
        )
        if kind in {"ma", "volcano"} and gene["estimate_status"] != "estimated":
            reasons.append("estimate_" + gene["estimate_status"])
        if x is None:
            reasons.append(
                "effect_missing"
                if kind == "volcano"
                else "mean_normalized_count_missing"
            )
        if y is None:
            reasons.append(
                {
                    "ma": "effect_missing",
                    "volcano": "q_value_missing",
                    "dispersion": "final_dispersion_missing",
                }[kind]
            )
        if kind == "volcano" and y == 0:
            y = None
            reasons.append("q_value_zero_has_no_finite_negative_log10")
        if kind == "dispersion":
            if x is not None and x <= 0:
                x = None
                reasons.append("mean_normalized_count_nonpositive_for_log10")
            if y is not None and y <= 0:
                y = None
                reasons.append("final_dispersion_nonpositive_for_log10")
        append(
            gene["gene_id"],
            x,
            y,
            gene["estimate_status"],
            reasons,
            gene["interval"] if kind == "ma" else None,
        )


def _append_sample_expression(workspace, genes, samples, gene_id, append):
    gene = next((gene for gene in genes if gene["gene_id"] == gene_id), None)
    if gene is None:
        raise ValueError("bulk_plot_expression_requires_existing_gene_id")
    contrast = workspace["effect_definition"]
    if contrast is None:
        raise ValueError("bulk_plot_expression_requires_computed_contrast")
    axes = {
        "x": _axis(
            "condition",
            "0: "
            + contrast["reference_condition"]
            + "; 1: "
            + contrast["comparison_condition"],
            "condition_position",
        ),
        "y": _axis(
            "normalized_count",
            "ln(1 + normalized count): " + gene_id,
            "normalized_count",
            "natural_log1p",
        ),
    }
    positions = {
        contrast["reference_condition"]: 0,
        contrast["comparison_condition"]: 1,
    }
    for index, sample in enumerate(samples):
        x = positions.get(sample["condition"])
        y = (
            gene["raw_counts"][index] / sample["size_factor"]
            if gene["raw_counts"] is not None
            else None
        )
        reasons = ([] if x is not None else ["condition_not_in_computed_contrast"]) + (
            [] if y is not None else ["raw_count_unavailable"]
        )
        append(
            sample["sample_id"],
            x,
            y,
            sample["pair_id"] or sample["sample_id"],
            reasons,
        )
    return axes


def _append_p_histogram(genes, append):
    counts = [0] * 20
    for gene in genes:
        if gene["p_value"] is not None:
            counts[min(19, int(gene["p_value"] * 20))] += 1
    for index, count in enumerate(counts):
        append(
            f"bin_{index:02d}",
            (index + 0.5) / 20,
            count,
            "saved_p_value_histogram",
            [],
        )
    missing = [
        {"entity_id": gene["gene_id"], "reasons": ["p_value_missing"]}
        for gene in genes
        if gene["p_value"] is None
    ]
    return {
        "valid_p_values": sum(counts),
        "missing_p_values": len(missing),
        "excluded_gene_ids": [row["entity_id"] for row in missing],
        "bin_edges": [index / 20 for index in range(21)],
        "bin_policy": "left_closed_right_open_except_last_right_closed",
    }


def _projection(workspace, kind, gene_id):
    validate_bulk_plot_workspace(workspace)
    if kind not in _KINDS:
        raise ValueError("bulk_plot_unknown_kind")
    genes, samples = workspace["genes"], workspace["samples"]
    rows, undrawn = [], []
    extra_coverage = {}
    scale = workspace["effect_scale"] or "saved_effect_scale_unavailable"

    def append(entity_id, x, y, group_id, reasons, interval=None):
        rows.append(
            {
                "entity_id": entity_id,
                "coordinate_id": None,
                "group_id": group_id,
                "x": _value(x),
                "y": _value(y),
                "interval": interval if y is not None else None,
            }
        )
        if reasons:
            undrawn.append(
                {"entity_id": entity_id, "reasons": list(dict.fromkeys(reasons))}
            )

    if kind in {"ma", "volcano", "dispersion"}:
        plot_kind, entity_kind = "gene_scatter", "gene"
        axes = _gene_scatter_axes(kind, scale)
        _append_gene_scatter(genes, kind, append)
    elif kind == "pca":
        plot_kind, entity_kind = "sample_scatter", "sample"
        axes = {
            "x": _axis("pc1", "PC1", "log2_normalized_count_score"),
            "y": _axis("pc2", "PC2", "log2_normalized_count_score"),
        }
        by_sample = {row["sample_id"]: row for row in workspace["pca"]["scores"]}
        for sample in samples:
            score = by_sample.get(sample["sample_id"])
            append(
                sample["sample_id"],
                score["pc1"] if score else None,
                score["pc2"] if score else None,
                sample["condition"] or "condition_unavailable",
                [] if score else [workspace["pca"]["reason"]],
            )
    elif kind in {"paired_expression", "expression"}:
        plot_kind, entity_kind = "paired_expression", "sample"
        axes = _append_sample_expression(workspace, genes, samples, gene_id, append)
    else:
        plot_kind, entity_kind = "histogram", "bin"
        axes = {
            "x": _axis(
                "p_value_bin_midpoint", "Saved p-value (20 equal-width bins)", "p_value"
            ),
            "y": _axis("gene_count", "Genes with saved p-values", "gene_count"),
        }
        extra_coverage = _append_p_histogram(genes, append)
    limitations = list(workspace["limitations"])
    if workspace["availability"][kind]["status"] != "available":
        limitations.append(
            "Panel unavailable: " + workspace["availability"][kind]["reason"]
        )
    data = {
        "format": "devbench.numeric-plot-source/2",
        "plot_kind": plot_kind,
        "entity_kind": entity_kind,
        "axes": axes,
        "membership": [row["entity_id"] for row in rows],
        "rows": rows,
        "uncertainty_policy": "report_absence",
        "limitations": limitations,
    }
    coverage = {
        "kind": kind,
        "total_entities": len(rows),
        "drawn_entities": len(rows) - len(undrawn),
        "undrawn_entities": len(undrawn),
        "undrawn_reason_counts": dict(
            Counter(reason for item in undrawn for reason in item["reasons"])
        ),
        "undrawn": undrawn,
        "source_gene_count": len(genes),
        "source_sample_count": len(samples),
        "truncated": False,
        **extra_coverage,
    }
    return data, coverage


def numeric_plot_data(workspace: dict, kind: str, gene_id: str | None = None) -> dict:
    """Return a figure/2 numeric source; no writes or statistical inference."""
    return _projection(workspace, kind, gene_id)[0]


def numeric_plot_coverage(
    workspace: dict, kind: str, gene_id: str | None = None
) -> dict:
    """Describe retained membership and every absent geometry independently."""
    return _projection(workspace, kind, gene_id)[1]
