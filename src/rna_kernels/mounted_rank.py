"""Automatic rank sensitivity and bounded evidence for large mounted cohorts."""

import itertools

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.stats import spearmanr

from devharness_core.provenance import hash_artifact

_MIN_UNITS = 100
_MIN_CONCORDANCE = 0.5
_PRIMARY_DISCOVERY_RATIO = 2
_MIN_EXCESS_DISCOVERIES = 10
_FDR = 0.05


def assess_rank_agreement(primary_effects, primary_q, effects, q, *, permutations=None):
    """Apply fixed, method-default comparison thresholds to matched hypotheses."""
    comparable = np.isfinite(primary_effects) & np.isfinite(effects)
    matched = int(np.count_nonzero(comparable))
    concordance = None
    if (
        matched >= 20
        and np.ptp(primary_effects[comparable]) > 0
        and np.ptp(effects[comparable]) > 0
    ):
        correlation = float(
            spearmanr(primary_effects[comparable], effects[comparable]).statistic
        )
        if np.isfinite(correlation):
            concordance = correlation
    first = int(np.count_nonzero(comparable & (primary_q <= _FDR)))
    second = int(np.count_nonzero(comparable & (q <= _FDR)))
    resolution = (
        None if not permutations else len(q) / ((permutations + 1) * max(1, first))
    )
    resolved = resolution is None or resolution <= _FDR
    excess = (
        resolved
        and first > _PRIMARY_DISCOVERY_RATIO * max(1, second)
        and first - second >= _MIN_EXCESS_DISCOVERIES
    )
    diverges = concordance is not None and concordance < _MIN_CONCORDANCE or excess
    return {
        "paired_estimates": matched,
        "effect_spearman": concordance,
        "effect_comparison": "assessed"
        if concordance is not None
        else "insufficient_comparable_effects",
        "primary_discoveries": first,
        "sensitivity_discoveries": second,
        "discovery_comparison_resolved": resolved,
        "best_permutation_q_at_primary_discovery_count": resolution,
        "thresholds": {
            "effect_spearman_min": _MIN_CONCORDANCE,
            "fdr": _FDR,
            "primary_discovery_ratio": _PRIMARY_DISCOVERY_RATIO,
            "min_excess_discoveries": _MIN_EXCESS_DISCOVERIES,
        },
        "hold": "Q-DESIGN" if diverges or not matched else None,
    }


def _rank_rows(genes, effects, probabilities, adjusted, codes):
    for gene, effect, p, q, code in zip(
        genes, effects, probabilities, adjusted, codes, strict=True
    ):
        available = code == 0
        yield {
            "gene_id": gene,
            "estimate_status": "estimated" if available else "unavailable",
            "effect": effect if available else None,
            "p_value": p if available else None,
            "q_value": q if available else None,
        }


def write_rank_sensitivity(
    source,
    destination,
    request,
    cohort,
    primary,
    factors=None,
    *,
    normalization_method="RLE",
):
    """Run the frozen comparison once and emit a private columnar evidence artifact."""
    design, unit_ids, strata, covariates = cohort
    if len(unit_ids) < _MIN_UNITS:
        return None
    from .rank_sensitivity import (
        prepare_rank_sensitivity,
        rank_family_bh,
        test_rank_block,
    )

    conditions = np.where(
        design[:, 1] == 1, request["numerator"], request["denominator"]
    ).tolist()
    try:
        plan = prepare_rank_sensitivity(
            conditions,
            unit_ids,
            numerator=request["numerator"],
            denominator=request["denominator"],
            strata=strata,
            covariates=covariates,
        )
    except ValueError as error:
        return {"status": "unavailable", "reason": str(error)[:300], "hold": "Q-DESIGN"}

    effects = np.empty(source.shape[0])
    probabilities = np.empty(source.shape[0])
    codes = np.empty(source.shape[0], dtype=np.int8)
    start = 0
    for _, values in source.iter_gene_chunks(128):
        data = np.log1p(values / factors) if factors is not None else values
        stop = start + len(data)
        effects[start:stop], probabilities[start:stop], codes[start:stop] = (
            test_rank_block(plan, data)
        )
        start = stop
    if start != source.shape[0]:
        raise ValueError("Rank sensitivity did not cover the complete measured family")
    adjusted = rank_family_bh(probabilities, codes)
    assessment = assess_rank_agreement(
        *primary, effects, adjusted, permutations=plan.permutations
    )
    schema = pa.schema(
        [
            pa.field("gene_id", pa.string(), nullable=False),
            pa.field("estimate_status", pa.string(), nullable=False),
            pa.field("effect", pa.float64()),
            pa.field("p_value", pa.float64()),
            pa.field("q_value", pa.float64()),
        ]
    )
    rows = _rank_rows(source.gene_names, effects, probabilities, adjusted, codes)
    with pq.ParquetWriter(destination, schema, compression="snappy") as writer:
        while batch := list(itertools.islice(rows, 2048)):
            writer.write_batch(pa.RecordBatch.from_pylist(batch, schema=schema))
    return {
        "status": "completed",
        "method": plan.method,
        "n_units": len(unit_ids),
        "permutations": plan.permutations,
        "seed": plan.seed,
        "input_scale": f"log1p_{normalization_method}"
        if factors is not None
        else "declared_continuous",
        "tested_count": int(np.count_nonzero(codes == 0)),
        "artifact_sha256": hash_artifact(destination, max_bytes=256 * 1024**2)[
            "sha256"
        ],
        **assessment,
    }
