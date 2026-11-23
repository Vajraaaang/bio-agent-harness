"""Offline control fixtures; no claim of reviewer authentication or qualification."""
from copy import deepcopy
import hashlib
import json

import pytest

from tests.test_analysis_episodes import workspace
from devbench.analysis.checkpoints import digest
from devbench.analysis.episode_agent import Selection
from devbench.analysis.methods import MethodService
from devbench.analysis.scientific_judgment import (
    ArtifactEvidence, ClaimJudgment, ClaimPacket, EvaluationPolicy, NumericalAssertion,
    ObjectiveCheck, packet_sha256, policy_sha256,
)


def test_active_workflow_review_type_hints_resolve():
    from typing import get_type_hints

    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.analysis.scientific_review import ActiveReviewPacket
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from devbench.analysis.workflow_scientific_reviews import ActiveWorkflowReview

    hints = get_type_hints(ActiveWorkflowReview)
    assert hints["packet"] is ActiveReviewPacket
    assert hints["lifecycle"] is ReviewHoldLifecycle
    assert hints["broker"] is EvidenceBroker


class Planner:
    def __init__(self, root):
        self.methods = MethodService(root)
        self.state = {"session_id": "synthetic-session", "context": {
            "task": {"scientific_objective": "Compare synthetic procedures", "computational_task": "Synthetic diagnostic"},
            "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": []}, "prior_state": None},
            "sources": [], "scientific_candidates": [{"name": "synthetic candidate"}], "candidates": [],
            "registry": {"must_not_enter_candidate_review": True}, "trial_designs": [], "experiments": [], "results": [],
            "recommendation": {"choice": None}, "evaluation_purpose": "method_selection"}

    def get(self, session):
        assert session == self.state["session_id"]
        return deepcopy(self.state)


class Selector:
    def __init__(self, *, status="supported", disposition="accept", forged=False):
        self.calls = 0
        self.status, self.disposition, self.forged = status, disposition, forged
        self.context = None

    def configuration(self):
        return {"backend": "recorded_fixture"}

    def select(self, context, output, **kwargs):
        self.calls += 1
        self.context = deepcopy(context)
        claim = context["claim_review"]
        judgment = ClaimJudgment(packet_sha256=claim["packet_sha256"], claim_id=claim["claim_id"], reviewer_id="first",
            stage="independent", scientific_status=self.status, evidence_ids=("diagnostic",),
            rationale="Synthetic bounded review response.", evidence_quality="verified_artifact",
            action_consequences="Advisory workflow gate only.", competing_explanations=(),
            missing_evidence="More evidence required." if self.status == "insufficient_evidence" else None,
            numerical_assertions=(NumericalAssertion(evidence_id="diagnostic", pointer="/value", value=.9 if self.forged else .2),))
        return Selection({"kind": "scientific_review", "disposition": self.disposition, "rationale": "Synthetic advisory response.",
            "findings": [], "questions": [], "limitations": ["Offline fixture; independence unverified."],
            "claim_judgment": judgment.model_dump(mode="json")}, {"model_sessions_attempted": 0, "tokens": None,
            "cost_usd": None, "actual_model": "offline_fixture"})


def setup(workspace, **selector_kwargs):
    from devbench.analysis.workflow_scientific_reviews import WorkflowScientificReviewAdapter
    root = workspace / "root"
    root.mkdir(mode=0o700)
    planner = Planner(root / "methods")
    diagnostic = root / "diagnostic.json"
    diagnostic.write_text('{"value": 0.2}')
    panel = EvaluationPolicy(policy_id="synthetic-policy", version="1", independent_reviewer_ids=("first", "second"), adjudicator_id="third")
    selector = Selector(**selector_kwargs)
    def builder(planner, session, stage, material):
        return ClaimPacket(packet_id="synthetic-packet", input_root=digest(material), claim_id="synthetic-claim",
            claim_text="Assess this exact synthetic workflow material.", objective="Compare synthetic procedures",
            assay="synthetic", measurement="continuous", assay_semantics="known", units_design="Declared synthetic units",
            intent="diagnostic_flagging", assumptions=(), observations=(), declarations=(), inferences=(), unknowns=(),
            evidence=(ArtifactEvidence(evidence_id="diagnostic", path=str(diagnostic), sha256=hashlib.sha256(diagnostic.read_bytes()).hexdigest(), kind="diagnostic"),),
            objective_checks=(), execution="not_run", objective_validity="not_assessed", operational_feasibility="available")
    adapter = WorkflowScientificReviewAdapter(output_root=root / "reviews", selector=selector, claim_packet_builder=builder,
        reviewer_label="first", evaluation_policy=panel, expected_policy_sha256=policy_sha256(panel))
    return planner, adapter, selector, diagnostic


def test_stage_material_stays_independent_of_registry_and_later_execution(workspace):
    from devbench.analysis.workflow_scientific_reviews import review_material
    planner, _, _, _ = setup(workspace)
    material = review_material(planner, "synthetic-session", "candidate_suitability")
    assert "registry" not in json.dumps(material)
    planner.state["results"] = [{"observed": "later"}]
    assert material == review_material(planner, "synthetic-session", "candidate_suitability")


def test_one_actual_combined_dispatch_retains_advisory_and_claim_record(workspace):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, selector, _ = setup(workspace)
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert selector.calls == 1
    assert result["status"] == "accepted"
    record = verify_review(planner, "synthetic-session", "candidate_suitability", result, policy={})
    assert record["claim_judgment"]["scientific_status"] == "supported"
    assert record["panel_evaluation"]["alternative_aware_success"] is False
    assert record["panel_evaluation"]["reason"] == "execution_did_not_complete"
    assert record["independence_verified"] is False and record["qualification_status"] == "not_established"
    assert not any(record[key] for key in ("model_execution_granted", "data_execution_granted", "canonical_change_granted", "promotion_granted"))
    assert "claim_review" in selector.context


def test_real_codex_selector_gets_fresh_review_scope_without_mutating_planner(workspace, monkeypatch):
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, scripted, _ = setup(workspace)
    planner.state['data_classification'] = 'synthetic'
    private = planner.methods.root.parent
    previous_actor = workspace / 'previous-analyst-actor'
    previous_actor.mkdir()
    denied = workspace / 'original-denied-root'
    denied.mkdir()
    prototype = CodexActionSelector(requested_model='dispatch-stub-no-provider', workspace_parent=workspace / 'actors',
        private_root=private, denied_roots=(denied,), memory_mb=512, max_context_bytes=120000)
    prototype.workspaces = [previous_actor]
    adapter.selector = prototype
    calls = []
    def dispatch_stub(selector, context, output, **kwargs):
        assert selector is not prototype
        assert selector.private_root == output.parent
        assert selector.private_root.name == 'request'
        assert private in selector.denied_roots and denied in selector.denied_roots
        assert previous_actor in selector.workspaces
        assert selector.workspace_parent == prototype.workspace_parent
        assert selector.requested_model == prototype.requested_model
        assert selector.memory_mb == prototype.memory_mb
        assert selector.max_context_bytes == prototype.max_context_bytes
        assert kwargs['allow_model_execution'] is True
        calls.append(selector.private_root)
        return scripted.select(context, output, **kwargs)
    monkeypatch.setattr(CodexActionSelector, 'select', dispatch_stub)
    for stage in ('candidate_suitability', 'comparison_design'):
        evidence = review(planner, 'synthetic-session', stage=stage, reviewer=adapter, policy={}, actor='fixture',
            allow_model_execution=True)
        assert evidence['status'] == 'accepted'
        assert verify_review(planner, 'synthetic-session', stage, evidence, policy={})['status'] == 'accepted'
    assert len(set(calls)) == 2
    assert prototype.private_root == private and prototype.workspaces == [previous_actor]


def test_numeric_forgery_cannot_become_acceptance(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, selector, _ = setup(workspace, forged=True)
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert selector.calls == 1 and result["status"] == "blocked"


def test_stale_claim_bytes_and_material_are_rejected(workspace):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, _, diagnostic = setup(workspace)
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    planner.state["scientific_candidates"][0]["name"] = "altered"
    with pytest.raises(ValueError, match="material"):
        verify_review(planner, "synthetic-session", "candidate_suitability", result, policy={})
    planner.state["scientific_candidates"][0]["name"] = "synthetic candidate"
    diagnostic.write_text('{"value": 0.8}')
    with pytest.raises(ValueError, match="changed"):
        verify_review(planner, "synthetic-session", "candidate_suitability", result, policy={})


def test_missing_adapter_blocks_and_missing_information_requires_scoped_route(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, _, _ = setup(workspace, status="insufficient_evidence", disposition="needs_information")
    assert review(planner, "synthetic-session", stage="candidate_suitability", reviewer=None, policy={}, actor="fixture")["status"] == "blocked"
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter,
        policy={"allow_descriptive": True, "descriptive_scope": "Retain observations; no inference or exclusion."}, actor="fixture")
    assert result["status"] == "descriptive"


def test_accept_cannot_override_deterministic_claim_failures(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, _, _ = setup(workspace)
    builder = adapter.claim_packet_builder
    def failed(*args):
        packet = builder(*args)
        return packet.model_copy(update={"objective_checks": (ObjectiveCheck(check="statistics", status="failed", evidence_ids=("diagnostic",), reason="Synthetic numerical negative control."),)})
    adapter.claim_packet_builder = failed
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert result["status"] == "blocked"


@pytest.mark.parametrize("stage", ["candidate_suitability", "comparison_design", "results_conclusions"])
def test_three_distinct_stage_contracts_dispatch_actual_material(workspace, stage):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, selector, _ = setup(workspace)
    result = review(planner, "synthetic-session", stage=stage, reviewer=adapter, policy={}, actor="fixture")
    assert result["status"] == "accepted"
    assert selector.context["sections"]["limitations"]["workflow_stage"] == stage
    assert verify_review(planner, "synthetic-session", stage, result)["stage"] == stage
    with pytest.raises(ValueError, match="material"):
        verify_review(planner, "synthetic-session", "results_conclusions" if stage != "results_conclusions" else "comparison_design", result)


def test_complete_record_is_reused_after_outer_ledger_receipt_loss(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, selector, _ = setup(workspace)
    first = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    # A fresh adapter has no in-memory operation status or model response.
    from devbench.analysis.workflow_scientific_reviews import WorkflowScientificReviewAdapter
    resumed = WorkflowScientificReviewAdapter(output_root=adapter.output_root, selector=selector,
        claim_packet_builder=adapter.claim_packet_builder, reviewer_label=adapter.reviewer_label,
        evaluation_policy=adapter.evaluation_policy, expected_policy_sha256=adapter.expected_policy_sha256)
    second = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=resumed, policy={}, actor="fixture")
    assert first == second and selector.calls == 1


def test_uncertain_review_never_redispatches(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, selector, _ = setup(workspace)
    review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    next(adapter.output_root.glob("*/review-evidence.json")).unlink()
    with pytest.raises(ValueError, match="uncertain"):
        review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert selector.calls == 1


def test_late_source_retrieval_invalidates_earlier_candidate_review(workspace):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, _, _ = setup(workspace)
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    source = workspace / "synthetic-source.txt"
    source.write_text("Synthetic source supplied after the frozen candidate assessment.")
    ref = planner.methods.import_artifact(source, artifact_type="file", actor="fixture")
    planner.state["sources"].append({"artifact_id": ref["artifact_id"], "content_sha256": ref["sha256"], "excerpt": source.read_text()})
    with pytest.raises(ValueError, match="material"):
        verify_review(planner, "synthetic-session", "candidate_suitability", result)


def test_forged_source_excerpt_is_rejected_before_provider(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, selector, _ = setup(workspace)
    source = workspace / "synthetic-source.txt"
    source.write_text("Actual synthetic source bytes.")
    ref = planner.methods.import_artifact(source, artifact_type="file", actor="fixture")
    planner.state["sources"].append({"artifact_id": ref["artifact_id"], "content_sha256": ref["sha256"], "excerpt": "Fabricated citation excerpt."})
    with pytest.raises(ValueError, match="excerpt"):
        review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert selector.calls == 0


def test_snapshot_verification_allows_later_execution_but_not_rewritten_candidates(workspace):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, _, _ = setup(workspace)
    before = planner.get("synthetic-session")
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    planner.state["results"] = [{"observed": "later, irrelevant to candidate assessment"}]
    assert verify_review(planner, "synthetic-session", "candidate_suitability", result, plan_snapshot=before)["status"] == "accepted"
    before["scientific_candidates"] = []
    with pytest.raises(ValueError, match="material"):
        verify_review(planner, "synthetic-session", "candidate_suitability", result, plan_snapshot=before)


def test_candidate_claim_context_cannot_smuggle_implementation_inventory(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    planner, adapter, selector, diagnostic = setup(workspace)
    diagnostic.write_text('{"value": 0.2, "registry": ["available implementation"]}')
    with pytest.raises(ValueError, match="registry"):
        review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert selector.calls == 0


def test_claim_citation_requires_actual_matching_workflow_acquisition(workspace):
    from devbench.analysis.workflow_scientific_reviews import review
    from devbench.analysis.scientific_judgment import SourceExcerpt
    planner, adapter, selector, diagnostic = setup(workspace)
    source = workspace / "fabricated-reference.txt"
    source.write_text("An actual supplied synthetic artifact is not a retrieved paper.")
    builder = adapter.claim_packet_builder
    def unacquired(*args):
        packet = builder(*args)
        evidence = ArtifactEvidence(evidence_id="unacquired", path=str(source), kind="source",
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(), source=SourceExcerpt(
                title="Synthetic citation", url="https://example.invalid/synthetic", version="fixture", access="abstract",
                accessed_on="2026-10-22", actual_text=source.read_text(), applicability="Synthetic test only.", limitations="No acquisition evidence."))
        return packet.model_copy(update={"evidence": (*packet.evidence, evidence)})
    adapter.claim_packet_builder = unacquired
    with pytest.raises(ValueError, match="acquisition"):
        review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    assert selector.calls == 0


def test_configured_stage_packet_input_is_verified_without_reinvoking_builder(workspace):
    from devbench.analysis.workflow_scientific_reviews import review, verify_review
    planner, adapter, selector, _ = setup(workspace)
    original_builder = adapter.claim_packet_builder
    supplied = workspace / "stage-packet.json"
    calls = []
    def builder(*args):
        calls.append(True)
        packet = original_builder(*args)
        supplied.write_text(packet.model_dump_json())
        return packet
    builder.input_reference = lambda stage: {"path": str(supplied), "sha256": hashlib.sha256(supplied.read_bytes()).hexdigest(), "size_bytes": supplied.stat().st_size}
    adapter.claim_packet_builder = builder
    result = review(planner, "synthetic-session", stage="candidate_suitability", reviewer=adapter, policy={}, actor="fixture")
    record = verify_review(planner, "synthetic-session", "candidate_suitability", result)
    assert record["claim_packet_input_ref"]["path"] == str(supplied)
    assert len(calls) == 1 and selector.calls == 1
    # Even whitespace-only mutation changes the exact configured source bytes.
    supplied.write_text(supplied.read_text() + "\n")
    with pytest.raises(ValueError, match="input"):
        verify_review(planner, "synthetic-session", "candidate_suitability", result)
    assert len(calls) == 1 and selector.calls == 1


def test_design_material_ignores_only_recomputed_candidate_eligibility(workspace):
    from devbench.analysis.workflow_scientific_reviews import stage_material
    planner, _, _, _ = setup(workspace)
    planner.state["candidates"] = [{"name": "stable", "method_version_id": "frozen-version",
        "applicability": "eligible", "blockers": [], "prerequisite_inspections": ["earlier"],
        "assumptions": [{"statement": "Replicated", "field": "replication", "operator": "equals",
            "expected": True, "hard_precondition": True, "status": "satisfied", "evidence": "earlier"}]}]
    before = stage_material(planner.state, "comparison_design")
    candidate = planner.state["candidates"][0]
    candidate.update(applicability="blocked_unknown", blockers=["later uncertainty"], prerequisite_inspections=["later"])
    candidate["assumptions"][0].update(status="unknown", evidence="later")
    assert stage_material(planner.state, "comparison_design") == before
    candidate["method_version_id"] = "different-version"
    assert stage_material(planner.state, "comparison_design") != before
    candidate["method_version_id"] = "frozen-version"
    candidate["assumptions"][0]["expected"] = False
    assert stage_material(planner.state, "comparison_design") != before


@pytest.mark.parametrize("relation,reassess", [("RetractionOf", False), ("ErratumFor", False), ("ErratumFor", True)])
def test_current_active_review_rechecks_cross_article_notice_basis(workspace, monkeypatch, relation, reassess):
    from devbench.analysis import workflow_scientific_reviews as workflow
    from tests.test_literature_claim_support import active_fixture, LiteratureSelector, retrieve_cross_article_notice, api
    planner, previous, _, _ = setup(workspace)
    lifecycle, broker, numerical, packet, handles, _ = active_fixture(workspace / "active", monkeypatch,
        scientific_obligations=True)
    packet = packet.model_copy(update={"workflow_material_sha256": digest(workflow.review_material(
        planner, "synthetic-session", "results_conclusions"))})
    active = workflow.ActiveWorkflowReview(packet=packet, lifecycle=lifecycle, broker=broker,
        context_id="ctx", reveal_context_id="reveal", controller_context_id="controller",
        query_handles=(numerical,), literature_handles=handles)
    policy = EvaluationPolicy(policy_id="synthetic-policy", version="1",
        independent_reviewer_ids=("challenger", "second"), adjudicator_id="third")
    adapter = workflow.WorkflowScientificReviewAdapter(output_root=previous.output_root,
        selector=LiteratureSelector(handles[0].bindings[0]), claim_packet_builder=previous.claim_packet_builder,
        reviewer_label="challenger", evaluation_policy=policy, expected_policy_sha256=policy_sha256(policy),
        active_review_builder=lambda *_: active)
    evidence = adapter.review(planner, "synthetic-session", stage="results_conclusions", policy={}, actor="fixture")
    assert evidence["status"] == "accepted"
    current = workflow.verify_review(planner, "synthetic-session", "results_conclusions", evidence, active_review=active)
    snapshots = [item["snapshot_id"] for item in current["active_review"]["literature_receipts"]]
    retrieve_cross_article_notice(handles[0].service, handles[0].bindings[0], lifecycle, monkeypatch, relation=relation)
    # The original query and article bytes remain current; the cross-article notice invalidates the claim use.
    for snapshot_id in snapshots:
        handles[0].service.replay(snapshot_id, require_current=True)
    assert not api().literature_use_current(lifecycle, "claim", packet.binding.sha256())
    if reassess:
        handles[0].assess(current["active_review"]["literature_assessments"][0]["assessment"],
            claim=packet.claims[0], context_id="reveal")
        assert api().literature_use_current(lifecycle, "claim", packet.binding.sha256())
    with pytest.raises(ValueError, match="Literature"):
        workflow.verify_review(planner, "synthetic-session", "results_conclusions", evidence, active_review=active)
    assert workflow.verify_review(planner, "synthetic-session", "results_conclusions", evidence,
        current_relevance=False)["status"] == "accepted"
