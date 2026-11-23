"""Exercise thesis comparison with offline retained publication responses."""

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime

import pytest

from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from rna_kernels.canonical import canonical_json
from tests.test_literature_claim_support import PUBMED, api, assess, claim, service

pytestmark = pytest.mark.wal


def _capture(instance, binding, identity="search-source"):
    request = api().LiteratureRequest(
        operation="fetch",
        provider="pubmed",
        identifier="123",
    )
    return instance.execute(
        request, request_id=identity, context_id="ctx", binding=binding
    )


def _comparison(root, monkeypatch, *, freeze=True, forge=False):
    from devbench.analysis.thesis_literature import (
        LiteratureComparisonPlan,
        LiteratureQueryReference,
        ThesisLiteratureComparison,
    )

    instance, binding, calls, _ = service(root, monkeypatch)
    retained = _capture(instance, binding)
    assessment = assess(instance, binding, retained["snapshot_id"])
    assert assessment["eligible"]
    receipt = retained["query_receipt"]
    if forge:
        receipt = {**receipt, "policy_sha256": "f" * 64}
    plan = LiteratureComparisonPlan(
        claim=claim(),
        scope_description="The retrieved human airway expression contrast.",
        selection_rule="Use the specified archived publication passage.",
        comparison_dimensions=("Tissue", "Intervention", "Direction"),
        corpus=(
            LiteratureQueryReference(operation_id="search-source", receipt=receipt),
        ),
    )
    flow = AdaptiveWorkflow(instance.ledger, instance.lineage_id)
    if freeze:
        flow.record_basis("corpus-a", "review_basis", plan.sha256())
    comparison = ThesisLiteratureComparison(
        plan=plan,
        plan_basis_id="corpus-a",
        review_binding_sha256=binding.review_binding_sha256,
        assessment_id=assessment["assessment_id"],
    )
    return instance, binding, calls, flow, comparison, retained


@contextmanager
def _unexpected_artifact(_):
    raise AssertionError("Publication metadata must not open any expression artifact")
    yield


def _package(root, flow, comparison):
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )

    schema = root / "answer_schema.json"
    raw = canonical_json({"type": "object"})
    schema.write_bytes(raw)
    spec = ThesisSynthesisSpec(
        question="Does the contrast agree with the specified published observation?",
        outcome="partial",
        expected_revision=flow.status()["revision"],
        answer_schema_sha256=hashlib.sha256(raw).hexdigest(),
        literature=comparison,
    )
    package = root / "package"
    sealed = write_thesis_package(
        package,
        flow,
        spec,
        answer_schema=schema,
        open_artifact=_unexpected_artifact,
    )
    assert verify_thesis_package(package, sealed["root_sha256"])["valid"]
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    return manifest["literature_comparison"], (package / "summary.md").read_text()


def test_retained_paper_and_assessment_support_only_the_frozen_corpus(
    workspace_tmp_path,
    monkeypatch,
):
    root = workspace_tmp_path
    _, _, calls, flow, binding, retained = _comparison(root, monkeypatch)
    record, summary = _package(root, flow, binding)
    assert len(calls) == 1
    assert record["status"] == "supported_in_retrieved_corpus"
    assert record["assessment"]["assessment_id"] == binding.assessment_id
    assert record["plan_sha256"] == binding.plan.sha256()
    (source,) = record["sources"]
    assert source["query_receipt"] == retained["query_receipt"]
    assert source["publication"]["identifiers"]["pmid"] == "123"
    assert source["publication"]["retraction"] == "unknown"
    assert "coverage" in source["publication"] and "correction" in source["publication"]
    observed = source["responses"][0]["retrieved_at"]
    assert observed == retained["responses"][0]["retrieved_at"]
    assert datetime.fromisoformat(observed).utcoffset() is not None
    assert "retrieved corpus" in summary
    assert "limited publication notice coverage" in summary


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("unfrozen", "literature_comparison_plan_not_frozen"),
        ("forgery", "literature_query_receipt_unverified"),
        ("retraction", "literature_claim_assessment_unavailable_or_stale"),
        ("correction", "literature_claim_assessment_unavailable_or_stale"),
    ],
)
def test_thesis_withholds_unverified_or_stale_comparisons(
    workspace_tmp_path,
    monkeypatch,
    change,
    reason,
):
    root = workspace_tmp_path
    instance, binding, calls, flow, comparison, _ = _comparison(
        root,
        monkeypatch,
        freeze=change != "unfrozen",
        forge=change == "forgery",
    )
    if change in {"retraction", "correction"}:
        from devbench.analysis import method_sources

        relation = "RetractionIn" if change == "retraction" else "ErratumIn"
        notice = (
            '<CommentsCorrectionsList><CommentsCorrections RefType="'
            + relation
            + '"><PMID>999</PMID></CommentsCorrections>'
            "</CommentsCorrectionsList>"
        ).encode()
        body = PUBMED.replace(b"</MedlineCitation>", notice + b"</MedlineCitation>")
        monkeypatch.setattr(
            method_sources,
            "_fetch_hop",
            lambda *_, **__: (None, body, "application/xml"),
        )
        _capture(instance, binding, "status-refresh")
    count = len(calls)
    record, summary = _package(root, flow, comparison)
    assert len(calls) == count
    assert record["status"] == "withheld" and record["reason"] == reason
    assert "sources" not in record and "assessment" not in record
    assert "comparison was withheld" in summary


def test_a_frozen_subset_cannot_replace_the_complete_assessed_corpus(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis import method_sources

    root = workspace_tmp_path
    instance, binding, _, flow, comparison, primary = _comparison(root, monkeypatch)
    another = PUBMED.replace(b">123<", b">456<").replace(b"/test", b"/second")
    monkeypatch.setattr(
        method_sources,
        "_fetch_hop",
        lambda *_, **__: (None, another, "application/xml"),
    )
    instance.execute(
        api().LiteratureRequest(operation="fetch", provider="pubmed", identifier="456"),
        request_id="second-source",
        context_id="ctx",
        binding=binding,
    )
    updated = assess(instance, binding, primary["snapshot_id"])
    comparison = comparison.model_copy(
        update={"assessment_id": updated["assessment_id"]}
    )
    record, _ = _package(root, flow, comparison)
    assert record["status"] == "withheld"
    assert record["reason"] == "literature_corpus_differs_from_assessed_dependencies"
