"""Retrieved observations stay separate from implementations and frozen decisions."""
import pytest

from devbench.analysis.workflow_discovery import discover
from tests.test_langgraph_workflow import rna_plan
from tests.test_method_planner import Selector, candidate


def test_scientific_generation_receives_verified_discovery_without_registry(tmp_path):
    planner, plan, *_ = rna_plan(tmp_path)
    evidence = discover(planner, plan["session_id"], provider=None, actor="scientist",
        request={"mode": "supplied_only", "reason": "Explicit descriptive synthetic control",
                 "query": None, "allowed_hosts": [], "acquire_indices": []})
    selector = Selector({"candidates": [candidate()]})
    planner.generate_candidates(plan["session_id"], selector, actor="scientist", discovery_context=evidence)
    context = selector.contexts[0]
    assert context["discovery_evidence"]["status"] == "descriptive"
    assert context["discovery_evidence"]["search_performed"] is False
    assert "registry" not in context and "available_methods" not in context
    assert context["discovery_evidence"]["sources"] == []


def test_new_sources_after_candidate_freeze_require_new_cycle_before_network(tmp_path, monkeypatch):
    planner, plan, *_ = rna_plan(tmp_path)
    planner.generate_candidates(plan["session_id"], Selector({"candidates": [candidate()]}), actor="scientist")
    import devbench.analysis.method_sources as source_module
    monkeypatch.setattr(source_module, "retrieve_source", lambda *args, **kwargs: pytest.fail("Late retrieval reached network"))
    with pytest.raises(ValueError, match="new planning cycle|frozen"):
        planner.retrieve_evidence(plan["session_id"], "https://example.org/method", allowed_hosts=["example.org"],
                                  actor="scientist", allow_network=True)
    assert planner.get(plan["session_id"])["sources"] == []


def test_repository_availability_is_withheld_until_capability_resolution(tmp_path):
    planner, plan, *_ = rna_plan(tmp_path)
    class Provider:
        def configuration(self):
            return {"provider": "recorded_engineering_observations"}
        def search(self, context, request, **kwargs):
            assert "registry" not in context
            return {"raw_response": {"fixture": True}, "consumption": {"requests": 0}, "candidates": [{
                "identity": {"repository": "author/method"}, "title": "Executable implementation available",
                "url": "https://github.com/author/method", "kind": "author_repository", "access": "metadata",
                "excerpt": "A readily available implementation; this must not influence scientific selection."}]}
    evidence = discover(planner, plan["session_id"], provider=Provider(), actor="scientist", allow_network=True,
        request={"query": "method evidence", "allowed_hosts": ["github.com"], "acquire_indices": []})
    selector = Selector({"candidates": [candidate()]})
    planner.generate_candidates(plan["session_id"], selector, actor="scientist", discovery_context=evidence)
    assert selector.contexts[0]["discovery_evidence"]["sources"] == []
    assert "Executable implementation" not in str(selector.contexts[0])
    resolver = Selector({"bindings": [{"candidate": "Unknown QC", "method_version_id": None,
        "discovery": {"status": "unavailable", "references": [], "implementation_confidence": "unknown",
                      "reason": "Recorded repository is only an unverified lead"}}]})
    # A failed/invalid response may be rejected by the resolution schema; the
    # projection itself must still retain the actual unverified repository lead.
    try:
        planner.resolve_capabilities(plan["session_id"], resolver, [], actor="scientist", discovery_context=evidence)
    except ValueError:
        pass
    assert resolver.contexts[0]["implementation_discovery"]["sources"][0]["kind"] == "author_repository"
