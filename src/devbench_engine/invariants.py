"""Positive veto predicates for explicit, mathematically invalid statistical claims.

Missing narrative evidence is not invented. These gates complement task schemas,
known references and qualified review; they do not semantically judge free text.
"""
import math
from typing import List


def _probability(value) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 1
    except OverflowError:
        return False


def statistical_failure_veto(answer: dict, tool_events: List[dict]) -> dict:
    violations = []
    statistics = answer.get("gene_statistics", {})
    if isinstance(statistics, dict):
        for gene, record in statistics.items():
            if not isinstance(record, dict):
                violations.append(f"invalid_gene_statistics:{gene}")
                continue
            p = record.get("pvalue", record.get("p_value"))
            q = record.get("qvalue", record.get("q_value", record.get("padj")))
            if not _probability(p) or not _probability(q) or q < p:
                violations.append(f"invalid_p_q_invariant:{gene}")
    if answer.get("rank_deficient") is True and answer.get("causal_batch_adjusted_identifiable") is True:
        violations.append("causal_claim_from_rank_deficient_design")
    if answer.get("recommended_action") == "fit_validated_model":
        if answer.get("contrast_valid") is False:
            violations.append("invalid_contrast_ignored")
        if answer.get("embedding_valid") is False:
            violations.append("invalid_embedding_ignored")
    observed = {}
    for event in tool_events:
        if event.get("tool_name") == "fit_temporal_trajectory" and event.get("succeeded"):
            observed.update(event.get("result", {}).get("genes", {}))
    claimed = answer.get("claimed_significant_genes", [])
    if isinstance(claimed, list):
        for gene in claimed:
            if not isinstance(gene, str) or gene not in observed:
                continue
            record = observed[gene]
            q = record.get("padj", record.get("q_value"))
            if _probability(q) and q > .05:
                violations.append(f"nonsignificant_gene_claim:{gene}")
    return {"passed": bool(violations), "score": float(bool(violations)), "score_max": 1.0,
            "role": "hard_fail", "violations": violations,
            "reasoning": "; ".join(violations) if violations else "No explicit statistical invariant violation observed"}
