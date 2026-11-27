"""Benchmark protocol parity, source ownership and general-research skill separation."""

import inspect
import re
from pathlib import Path

import pytest

from devharness_server import dispatcher, tools_network, tools_rna_pathways
from devharness_server import tools_scrna_composition as legacy_composition
from rna_kernels import network, pathways, scrna_composition

ROOT = Path(__file__).resolve().parents[1]
# The participant contract is frozen by ADR-18. Pin the names independently.
BENCHMARK_TOOLS = {
    "validate_design_matrix",
    "fit_temporal_trajectory",
    "calculate_temporal_monotonicity",
    "compact_temporal_state",
    "analyze_batch_effects",
    "infer_lineage",
    "infer_regulatory_network",
    "regulon_enrichment",
    "motif_enrichment",
    "audit_expression_quality",
}


def _general_research_docs():
    yield ROOT / "AGENTS.md"
    for parent in (ROOT / ".agents/skills", ROOT / "integrations/codex"):
        for path in sorted(parent.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".rst"}:
                yield path


def test_general_research_guidance_excludes_benchmark_tools():
    assert set(dispatcher._PARTICIPANT_TOOL_CONTRACT) == BENCHMARK_TOOLS
    # Leading delimiters include MCP namespace separators, e.g. mcp__server__<name>.
    legacy = re.compile(
        r"(?<![a-zA-Z0-9])(?:"
        + "|".join(sorted(BENCHMARK_TOOLS))
        + r")(?![a-zA-Z0-9_])"
    )
    coordinator = set(dispatcher._COORDINATOR_TOOL_CONTRACT) | set(
        dispatcher._ARTIFACT_WRITE_TOOL_CONTRACT
    )
    # The unqualified bulk_condition_contrast is also a native Tier A operation.
    # A qualified dispatcher citation unambiguously refers to the benchmark.
    qualified = re.compile(
        r"(?:mcp__devharness__|devharness_server(?:\.tools_\w+)?[.:/])"
        + "(?:"
        + "|".join(sorted(coordinator))
        + r")(?![a-zA-Z0-9_])"
    )
    collisions = [
        f"{path.relative_to(ROOT)}:{line}: {found[0]}"
        for path in _general_research_docs()
        for line, text in enumerate(path.read_text().splitlines(), 1)
        if (found := legacy.search(text) or qualified.search(text))
    ]
    assert not collisions, "\n".join(collisions)


def test_historical_worker_entrypoints_and_frozen_r_resources():
    assert tools_rna_pathways.run_mounted_pathways is pathways.run_mounted_pathways
    assert (
        legacy_composition.run_mounted_composition
        is scrna_composition.run_mounted_composition
    )
    assert tools_network._enrichment_worker is network._enrichment_worker
    assert list(inspect.signature(tools_network.motif_enrichment).parameters) == [
        "foreground_genes",
        "background_genes",
        "motif_hits_path",
        "max_results",
    ]
    historical = ROOT / "src/devharness_server/r"
    canonical = ROOT / "src/rna_kernels/r"
    assert {p.name for p in historical.glob("*.R")} == {
        p.name for p in canonical.glob("*.R")
    }
    for old in historical.glob("*.R"):
        # The paired driver sources sibling scripts through its installed R path.
        expected = old.read_bytes().replace(
            b"/opt/rna/runtime/devharness_server/r", b"/opt/rna/runtime/rna_kernels/r"
        )
        assert (canonical / old.name).read_bytes() == expected


def test_regulon_and_motif_require_trusted_tested_universe_before_opening_reference(
    tmp_path,
):
    from devbench.core.rna_pathway_analysis import TestedGeneUniverse

    universe = TestedGeneUniverse(
        result_receipt_sha256="1" * 64,
        gene_statistics_sha256="2" * 64,
        filter_receipt_sha256="3" * 64,
        genes=("a", "b", "c", "d"),
        selected=("a", "b"),
    )
    args = {
        "reference": tmp_path / "withheld.tsv",
        "kind": "regulon",
        "collection_id": "ref",
        "collection_version": "1",
        "verify_universe": lambda _value: False,
    }
    with pytest.raises(PermissionError, match="unverified"):
        network.admit_verified_enrichment(universe, **args)
    args["reference"].write_text("tf\ttarget\nx\ta\nx\tb\n")
    args["verify_universe"] = lambda value: value == universe
    assert network.admit_verified_enrichment(universe, **args).foreground == ("a", "b")
    args["kind"] = "motif"
    args["reference"].write_text("gene\tmotif\thit\na\tX\t1\nb\tX\t0\n")
    with pytest.raises(ValueError, match="explicit scanned"):
        network.admit_verified_enrichment(universe, **args)
