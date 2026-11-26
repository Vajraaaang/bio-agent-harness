"""Guard the repository instructions and the standalone copy used by the evaluator."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERAL = ROOT / "AGENTS.md"
OVERLAY = Path("src/devbench_engine/AGENTS.md")
QUESTIONS = (
    "What data do I have?",
    "What question am I investigating?",
    "Which methods are appropriate, and why?",
    "What happened?",
    "What can I conclude, and what remains uncertain?",
)
TEMPORAL_TOOLS = (
    "audit_expression_quality",
    "validate_design_matrix",
    "analyze_batch_effects",
    "compact_temporal_state",
    "fit_temporal_trajectory",
    "infer_lineage",
    "infer_regulatory_network",
    "regulon_enrichment",
    "motif_enrichment",
    "calculate_temporal_monotonicity",
)
STAGED_OUTPUTS = (
    "answer_schema.json",
    "final_discovery.json",
    "stage_kinetics",
    "master_regulators",
    "condition_number_validated",
    "biological_rationale",
    "gene_statistics",
)
STAGED_LIMITS = (
    r"(?:do not|must not|never) download (?:bam/fastq|fastq/bam) archives",
    r"(?:do not|must not|never)[^.]*load[^.]*(?:larger than|over|>)\s*100\s*mi?b"
    r"\b[^.]*\bpandas\b",
)


def instructions(path):
    return " ".join(path.read_text(encoding="utf-8").casefold().split())


def assert_in_order(text, terms):
    start = 0
    for term in terms:
        found = text.find(term.casefold(), start)
        assert found >= 0, f"Missing or out of order: {term}"
        start = found + len(term)


def test_general_guidance_fits_budget_and_routes_staged_work_to_overlay():
    assert GENERAL.stat().st_size < 8 * 1024
    text = instructions(GENERAL)
    assert OVERLAY.as_posix().casefold() in text
    assert "out-of-core" in text and "compact summaries" in text
    forbidden = (*STAGED_OUTPUTS, *TEMPORAL_TOOLS, "tooldispatcher")
    assert not [term for term in forbidden if term in text]
    for limit in STAGED_LIMITS:
        assert not re.search(limit, text), limit


@pytest.mark.parametrize("path", [GENERAL, ROOT / OVERLAY], ids=["general", "packaged"])
def test_scientific_and_custody_contract_is_standalone_in_both_modes(path):
    text = instructions(path)
    assert_in_order(text, QUESTIONS)
    assert_in_order(
        text,
        (
            "L1",
            "execution",
            "L2",
            "comparison",
            "L3",
            "independent",
            "L4",
            "clinical validation",
        ),
    )
    required = (
        "requirement record",
        "comparison criteria",
        "operator interventions",
        "count-1",
        "design rank",
        "negative-binomial",
        "continuous",
        "finite json",
        "0 <= p <= q <= 1",
        "clinical_release_allowed = false",
        "read-only",
        "sealed evidence",
        "externally retained root",
    )
    assert not [term for term in required if term not in text]
    non_independence = r"repeat[^.]*(?:cannot|must not|do not|are not)[^.]*independent"
    assert re.search(non_independence, text)
    assert re.search(r"(?:reject|refuse)[^.]*non.estimable", text)
    assert re.search(r"(?:entire|full|whole|complete) (?:measurement )?ancestry", text)
    refused = re.search(r"(?:refuse|reject) count[^.]*", text)
    assert refused
    assert all(term in refused[0] for term in ("normalized", "transformed", "round"))
    assert re.search(r"(?:never|do not) fabricate", text)
    private_inputs = r"(?:never|no|do not)[^.]*private[^.]*clinical[^.]*external model"
    assert re.search(private_inputs, text)


def test_packaged_guidance_retains_staged_tools_limits_phases_and_output():
    text = instructions(ROOT / OVERLAY)
    required = (
        *TEMPORAL_TOOLS,
        *STAGED_OUTPUTS,
        "devharness mcp",
        "tooldispatcher",
        "offline",
        "four distinct master_regulators",
        "diagnostic/refusal",
    )
    assert not [term for term in required if term not in text]
    for limit in STAGED_LIMITS:
        assert re.search(limit, text), limit
    assert_in_order(text, ("exploration", "diagnostics", "modeling", "synthesis"))
    assert re.search(r"intermediate phases[^.]*schema", text)
    assert re.search(r"(?:during|at) synthesis[^.]*final_discovery\.json", text)
