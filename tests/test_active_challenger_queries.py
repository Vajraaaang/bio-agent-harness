"""Synthetic challenger entrypoints; procedural separation is not qualification."""
import json
from copy import deepcopy

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.episode_agent import Selection
from tests.test_evidence_access_policy import setup
from tests.test_evidence_query_receipts import family


def query_handle(tmp_path, *, allow_statistics=True, data=None):
    from devbench.analysis import scientific_review_agent as agent
    assert hasattr(agent, "EntitledReviewQuery"), "T17 entitled query adapter is missing"
    ledger, broker, policy = setup(tmp_path, allow_statistics=allow_statistics,
                                  role="challenger", phase="publication_initial")
    data = family() if data is None else data
    broker.register_source("family-source", content_sha256=digest(data), roots=("a" * 64,),
                           classification="target_outcome")
    handle = agent.EntitledReviewQuery("family-handle", data, {}, broker.delivery("ctx", "family-source"))
    return ledger, broker, policy, handle


@pytest.mark.wal
def test_entitled_challenger_can_find_counterevidence_after_preview(tmp_path):
    _, broker, _, handle = query_handle(tmp_path)
    first = handle.query({"target_id": "family", "limit": 20})
    second = handle.query({"target_id": "family", "limit": 20,
                           "cursor": first["query_receipt"]["next_cursor"]})
    assert next(row for row in second["rows"] if row["gene_id"] == "g039")["effect"] == -2
    assert second["query_receipt"]["total_matched"] == 46
    third = handle.query({"target_id": "family", "limit": 20,
                          "cursor": second["query_receipt"]["next_cursor"]})
    assert third["query_receipt"]["complete"]
    assert broker.verify_query_page(second)
    assert broker.exposure("ctx", "family-source")["classification"] == "target_outcome"


@pytest.mark.wal
def test_entitled_handle_cannot_rebind_source_or_hidden_statistics(tmp_path):
    from devbench.analysis.evidence_access import AccessDenied
    _, _, _, handle = query_handle(tmp_path, allow_statistics=False)
    with pytest.raises(AccessDenied):
        handle.query({"target_id": "family", "sort_by": "q_budget"})
    handle.result["families"][0]["genes"][0]["effect"] = 999
    with pytest.raises(ValueError, match="changed"):
        handle.query({"target_id": "family", "limit": 2})


@pytest.mark.wal
def test_challenger_can_retrieve_exact_donor_profiles_with_absence_preserved(tmp_path):
    from devbench.analysis.scientific_review_agent import EntitledReviewQuery
    from tests.test_evidence_sparse_queries import profiles
    data = family(2)
    panel = data["families"][0]
    panel["feature_ids"] = panel["eligible_features"] = ["a", "b"]
    for row, name in zip(panel["genes"], ["a", "b"], strict=True):
        row["gene_id"] = name
    _, broker, _, handle = query_handle(tmp_path, data=data)
    counts = EntitledReviewQuery("donor-counts", handle.result, profiles(tmp_path), handle.delivery)
    page = counts.query({"target_id": "family", "mode": "counts", "units": ["d1", "d2"]})
    assert page["rows"][0]["counts"] == {"p1": 2, "p2": 0, "p3": None}
    assert page["biological_units"] == {"p1": "d1", "p2": "d1", "p3": "d2"}
    assert broker.verify_query_page(page)


def packet(tmp_path, *, touchpoint="publication"):
    from devbench.analysis import scientific_result_contract as contracts
    from devbench.analysis import scientific_review as review
    assert hasattr(review, "ActiveReviewPacket"), "T17 revision-bound review packet is missing"
    assert hasattr(contracts, "ClaimValidationPlan"), "Typed claim validation plan is missing"
    from devbench.analysis.scientific_review_holds import (
        ReviewBinding,
        ReviewObligation,
        obligations_digest,
    )
    ledger, broker, policy, handle = query_handle(tmp_path)
    exposure = {"family-handle": broker.exposure("ctx", "family-source")}
    validation = contracts.ClaimValidationPlan(claim_ids=("claim",),
        competing_explanations=("Donor imbalance",), checks=("donor_sensitivity",),
        independence_dimension="Within-dataset sensitivity; no independent biological replication",
        required_evidence=("family-source",), stopping_conditions=("Withhold if unresolved",),
        limitations=("Synthetic fixture",))
    evidence = {"family_inventory": [{"family_id": "family", "total": 46}],
                "failed_checks": [], "weakening_checks": [], "branch_differences": []}
    narrative = "All genes increase, including g039."
    claim = contracts.ClaimSpec(claim_id="claim", revision=1,
        question="Does the complete family support a directional association?", population="Synthetic units",
        biological_unit="donor", estimand="Between-group relative expression", scale="log2",
        contrast="treated versus control", direction="positive", claim_class="biological_interpretation",
        effect_fields=("effect",), uncertainty_fields=(), family_id="family", exposure="unknown",
        evidence_ids=("family-source",), required_checks=("donor_sensitivity",))
    binding = ReviewBinding(**{key + "_sha256": digest(key) for key in
        ("facts", "inputs", "design", "procedure", "policy")}, evidence_sha256=digest(evidence),
        exposure_sha256=digest(exposure), obligations_sha256=obligations_digest((ReviewObligation(
            obligation_id="donor_sensitivity", entity_id="claim", stage="claim"),)),
        claim_sha256=digest({"controller_narrative": narrative, "validation_plan": validation.model_dump(mode="json"),
                            "claims": [claim.model_dump(mode="json")]}))
    value = review.ActiveReviewPacket(binding=binding, touchpoint=touchpoint,
        question="Does the complete family support a directional association?",
        evidence=evidence,
        prior_exposure=exposure, query_handles=(handle.descriptor(),),
        controller_narrative=narrative, validation_plan=validation, claims=(claim,))
    return ledger, broker, handle, value


@pytest.mark.wal
def test_evidence_first_packet_hides_narrative_and_binds_actual_exposure(tmp_path):
    from devbench.analysis import scientific_review as review
    _, _, _, value = packet(tmp_path)
    initial = review.active_review_context(value, phase="evidence_first")
    assert "controller_narrative" not in initial and "validation_plan" not in initial
    assert "blinded" not in initial
    assert initial["prior_exposure"] == value.prior_exposure
    reveal = review.active_review_context(value, phase="narrative_reveal")
    assert reveal["controller_narrative"] == value.controller_narrative
    assert reveal["validation_plan"]["checks"] == ["donor_sensitivity"]
    changed = value.model_dump(mode="json")
    changed["prior_exposure"]["family-handle"]["classification"] = "unknown"
    with pytest.raises(ValueError, match="exposure"):
        review.ActiveReviewPacket.model_validate_json(json.dumps(changed))


@pytest.mark.wal
@pytest.mark.parametrize("field", ["controller_narrative", "evidence"])
def test_material_change_cannot_keep_a_review_binding(tmp_path, field):
    from devbench.analysis.scientific_review import ActiveReviewPacket
    _, _, _, value = packet(tmp_path)
    changed = value.model_dump(mode="json")
    if field == "evidence":
        changed[field]["failed_checks"] = ["invalid design"]
    else:
        changed[field] = "Changed substantive interpretation"
    with pytest.raises(ValueError, match="revision"):
        ActiveReviewPacket.model_validate_json(json.dumps(changed))


@pytest.mark.wal
def test_narrative_cannot_be_smuggled_in_initial_evidence(tmp_path):
    from devbench.analysis.scientific_review import ActiveReviewPacket
    _, _, _, value = packet(tmp_path)
    changed = value.model_dump(mode="json")
    changed["evidence"]["nested"] = {"controller_narrative": "preferred explanation"}
    changed["binding"]["evidence_sha256"] = digest(changed["evidence"])
    with pytest.raises(ValueError, match="narrative"):
        ActiveReviewPacket.model_validate_json(json.dumps(changed))


def run_review(tmp_path, selector, *, limits=None, registered_checks=()):
    from devbench.analysis import scientific_review_agent as agent
    assert hasattr(agent, "request_active_review"), "Active reviewer orchestration is missing"
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    ledger, broker, handle, value = packet(tmp_path / "authority")
    policy_sha = handle.descriptor().policy_sha256
    broker.register_context("reveal", principal_id="scientist", lineage_id="study", role="challenger",
                            phase="narrative_reveal", policy_sha256=policy_sha, parents=("ctx",))
    broker.register_context("controller", principal_id="controller", lineage_id="study", role="controller",
                            phase="analysis", policy_sha256=policy_sha)
    lifecycle = ReviewHoldLifecycle(ledger.scientific, "study", registered_checks=registered_checks)
    from devbench.analysis.scientific_review_holds import ReviewEntity
    lifecycle.register_entities((ReviewEntity(entity_id="claim", kind="claim"),))
    from devbench.analysis.scientific_review_holds import ReviewObligation
    lifecycle.register_obligations((ReviewObligation(obligation_id="donor_sensitivity", entity_id="claim", stage="claim"),),
                                   binding=value.binding)
    result = agent.request_active_review(tmp_path / "review", value, lifecycle=lifecycle,
        cycle_id="cycle", reviewer_id="challenger", selector=selector, broker=broker,
        context_id="ctx", reveal_context_id="reveal", controller_context_id="controller",
        query_handles=(handle,), limits=limits)
    return result, lifecycle, broker, value


def assessment(outcome="completed"):
    return {"kind": "scientific_review", "outcome": outcome, "rationale": "Synthetic assessment",
            "covered_obligation_ids": [], "objections": []}


class ActiveSelector:
    def __init__(self):
        self.contexts = []

    def configuration(self):
        return {"backend": "recorded_scientific_review_fixture"}

    def select(self, context, output, **kwargs):
        self.contexts.append(deepcopy(context))
        action = assessment()
        action["covered_obligation_ids"] = [item["obligation_id"] for item in context["obligations"]]
        if len(self.contexts) == 1:
            action = {"kind": "query_evidence", "handle_id": "family-handle",
                      "query": {"target_id": "family", "mode": "family_summary"}}
        elif len(self.contexts) == 2:
            action = {"kind": "query_evidence", "handle_id": "family-handle",
                      "query": {"target_id": "family", "gene_ids": ["g039"]}}
        return Selection(action, {"model_sessions_attempted": 0, "tokens": 7, "actual_model": "offline_fixture"})


@pytest.mark.wal
def test_active_entrypoint_queries_before_reveal_and_meters_separately(
    workspace_tmp_path,
):
    selector = ActiveSelector()
    result, lifecycle, broker, value = run_review(workspace_tmp_path, selector)
    assert result["status"] == "completed"
    assert len(selector.contexts) == 4
    assert all("controller_narrative" not in context for context in selector.contexts[:-1])
    assert all("donor_sensitivity" not in json.dumps(context) for context in selector.contexts[:-1])
    assert selector.contexts[-1]["controller_narrative"] == value.controller_narrative
    assert selector.contexts[2]["query_results"][-1]["page"]["rows"][0]["gene_id"] == "g039"
    assert result["accounting"]["queries"] == 2
    assert result["accounting"]["invocations"] == 4
    assert result["observed_tokens"] == 28
    assert broker.exposure("controller", "review-return-cycle")["classification"] == "target_outcome"
    assert lifecycle.state()["cycles"]["cycle"]["phase"] == "completed"


@pytest.mark.wal
def test_query_exhaustion_withholds_and_never_reveals_narrative(workspace_tmp_path):
    from devbench.analysis.episode_agent import ReviewAgentLimits
    selector = ActiveSelector()
    result, _, _, _ = run_review(
        workspace_tmp_path, selector, limits=ReviewAgentLimits(max_queries=1)
    )
    assert result["status"] == "unavailable"
    assert all("controller_narrative" not in context for context in selector.contexts)


@pytest.mark.wal
def test_challenger_cannot_dispatch_or_publish(workspace_tmp_path):
    class Hostile(ActiveSelector):
        def select(self, context, output, **kwargs):
            return Selection({"kind": "run", "method": "refit"}, {"tokens": 1})
    result, _, _, _ = run_review(workspace_tmp_path, Hostile())
    assert result["status"] == "failed"
    assert result["analysis_executed"] is False


@pytest.mark.wal
def test_existing_workflow_adapter_uses_active_cycle_and_rejects_stale_binding(
    workspace_tmp_path,
):
    from devbench.analysis import workflow_scientific_reviews as workflow
    assert hasattr(workflow, "ActiveWorkflowReview"), "Existing adapter has no active review route"
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from tests.test_workflow_scientific_reviews import setup as workflow_setup
    planner, adapter, _, _ = workflow_setup(workspace_tmp_path)
    ledger, broker, handle, value = packet(workspace_tmp_path / "authority")
    policy = handle.descriptor().policy_sha256
    broker.register_context("reveal", principal_id="scientist", lineage_id="study", role="challenger",
        phase="narrative_reveal", policy_sha256=policy, parents=("ctx",))
    broker.register_context("controller", principal_id="controller", lineage_id="study", role="controller",
        phase="analysis", policy_sha256=policy)
    value = value.model_copy(update={"workflow_material_sha256": digest(workflow.review_material(
        planner, "synthetic-session", "results_conclusions"))})
    active = workflow.ActiveWorkflowReview(packet=value, lifecycle=ReviewHoldLifecycle(ledger.scientific, "study"),
        broker=broker, context_id="ctx", reveal_context_id="reveal", controller_context_id="controller",
        query_handles=(handle,))
    from devbench.analysis.scientific_review_holds import ReviewEntity
    active.lifecycle.register_entities((ReviewEntity(entity_id="claim", kind="claim"),))
    from devbench.analysis.scientific_review_holds import ReviewObligation
    active.lifecycle.register_obligations((ReviewObligation(obligation_id="donor_sensitivity", entity_id="claim", stage="claim"),),
                                          binding=value.binding)
    adapter.active_review_builder = lambda *_: active
    adapter.selector = ActiveSelector()
    first = adapter.review(planner, "synthetic-session", stage="results_conclusions", policy={}, actor="fixture")
    assert first["status"] == "blocked"  # A completed assessment does not perform the promised donor check.
    record = workflow.verify_review(planner, "synthetic-session", "results_conclusions", first, active_review=active)
    assert record["active_review"]["status"] == "completed"
    again = adapter.review(planner, "synthetic-session", stage="results_conclusions", policy={}, actor="fixture")
    assert again == first and len(adapter.selector.contexts) == 4
    active.lifecycle.open_cycle("newer-review", "publication", value.binding, reviewer_id="first", context_id="new-reviewer",
        narrative_sha256=digest(value.controller_narrative), validation_plan_sha256=digest(value.validation_plan.model_dump(mode="json")),
        entity_ids=("claim",))
    with pytest.raises(ValueError, match="latest"):
        workflow.verify_review(planner, "synthetic-session", "results_conclusions", first, active_review=active)
    assert workflow.verify_review(planner, "synthetic-session", "results_conclusions", first,
                                  current_relevance=False)["active_review"]["status"] == "completed"
    planner.state["preliminary_explanation"] = "Changed substantive interpretation"
    with pytest.raises(ValueError, match="material"):
        workflow.verify_review(planner, "synthetic-session", "results_conclusions", first)


def test_primary_adaptive_entrypoint_requires_current_design_review(tmp_path):
    from devbench.platform.workflow_actions import WorkflowActions
    assert hasattr(WorkflowActions, "require_primary_review"), "Primary dispatch lacks current review admission"
    from devbench.engine.workflow_operations import OperationBlocked
    actions = WorkflowActions(None, object(), actor="controller", selector=None)
    with pytest.raises(OperationBlocked, match="review"):
        actions.require_primary_review("fit")


def test_active_claim_spec_keeps_scientific_question_unit_and_evidence_typed():
    from devbench.analysis import scientific_result_contract as contracts
    assert hasattr(contracts, "ClaimSpec"), "Active substantive claims need the CLAIM-01 contract"
    with pytest.raises(ValueError):
        contracts.ClaimSpec(claim_id="claim", revision=1, question="Why?")


@pytest.mark.wal
def test_normal_adaptive_entrypoint_consumes_completed_design_review(tmp_path):
    from types import SimpleNamespace

    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.workflow_entrypoints import (
        LEDGER_NAME,
        advance_adaptive_workflow,
    )
    from tests.test_investigation_invalidation import Worker, make, step
    from tests.test_review_hold_lifecycle import binding, complete
    flow = make(tmp_path, name=LEDGER_NAME)
    node = step("fit", primary=True)
    node = node.model_copy(update={"profile": node.profile.model_copy(update={
        "permitted_claims": ("qualified_procedure_result",), "qualification_evidence": ("a" * 64,)})})
    flow.insert(node)
    lifecycle = ReviewHoldLifecycle(flow.ledger.scientific, "study", adaptive_workflow=flow)
    lifecycle.register_entities((ReviewEntity(entity_id="fit", kind="fit"),))
    complete(lifecycle)
    planner = object.__new__(ScientificMethodPlanner)
    planner.methods = SimpleNamespace(root=tmp_path)
    planner.get = lambda session_id: {"session_id": session_id}
    worker = Worker()
    outcome = advance_adaptive_workflow(planner, "study", ledger=flow.ledger, actor="controller", executor=worker,
        allow_data_execution=True, review_lifecycle=lifecycle, review_binding=lambda _: binding())
    assert outcome["steps_dispatched"] == 1 and worker.calls == ["fit"]


@pytest.mark.wal
def test_challenger_check_request_creates_a_real_scoped_branch(workspace_tmp_path):
    from devbench.analysis.scientific_review_holds import (
        CheckRequest,
        ResolutionPredicate,
        ReviewObjection,
    )
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_investigation_invalidation import artifact, link, step

    class Challenger(ActiveSelector):
        def select(self, context, output, **kwargs):
            selected = super().select(context, output, **kwargs)
            if len(self.contexts) == 3:
                predicate = ResolutionPredicate(kind="CounterevidenceResolved", owner_id="challenger",
                    evidence_required=("resolving-evidence",), resumption_condition="Assess whether the opposing gene changes the claim")
                request = CheckRequest(threat="Opposing evidence beyond the preview", affected_claim_ids=("claim",),
                    required_evidence=("resolving-evidence",), registered_check_id="synthetic", predicate=predicate,
                    input_evidence_ids=("family-source",))
                objection = ReviewObjection(category="COUNTEREVIDENCE", affected_entity_ids=("claim",),
                    evidence_ids=("family-source",), requested_resolution="Inspect the opposing evidence",
                    severity="material", necessity="The directional claim conflicts with g039", predicate=predicate,
                    check_request=request)
                selected.action["objections"] = [objection.model_dump(mode="json")]
            return selected

    result, lifecycle, _, value = run_review(
        workspace_tmp_path, Challenger(), registered_checks=("synthetic",)
    )
    hold_id = result["objections"][0]["hold_id"]
    assert result["blocked_entity_ids"] == ["claim"]
    flow = AdaptiveWorkflow(lifecycle.ledger, "study")
    frozen = artifact("frozen-family").model_copy(update={"sha256": value.query_handles[0].source_sha256})
    flow.register_artifact(frozen)
    actions = WorkflowActions(None, lifecycle.ledger, actor="controller", selector=None,
        review_lifecycle=lifecycle, review_binding=lambda _: value.binding)
    trace = actions.schedule_review_check(hold_id, flow,
        step("resolve-counterevidence", link("family", "frozen-family", immutable=True)))
    assert trace["status"] == "scheduled"
    with lifecycle.ledger._database() as db:
        assert "resolve-counterevidence" in flow._state(db).nodes
    assert lifecycle.state()["holds"][hold_id]["status"] == "open"
    assert result["analysis_executed"] is False


@pytest.mark.wal
def test_current_lifecycle_feeds_existing_publication_gate(tmp_path):
    from devbench.analysis.scientific_publication import PublicationAccess
    from devbench.analysis.scientific_result_contract import (
        reviewed_publication_descriptor,
    )
    from tests.test_review_hold_lifecycle import binding, complete, objection, review
    from tests.test_scientific_publication_policy import _allocation, profile
    lifecycle = review.__wrapped__(tmp_path)
    current = binding(claim_sha256="b" * 64)
    complete(lifecycle, current=current)
    complete(lifecycle, "publication", current=current, touchpoint="publication")
    kwargs = dict(source_sha256="c" * 64, allocation=_allocation(),
                  access=PublicationAccess(outcomes=True, statistics=True))
    p = profile("qualified_procedure_result", "exploratory_finding")
    descriptor = reviewed_publication_descriptor(lifecycle, current, "claim-a", p, **kwargs)
    assert descriptor.allow_statistics and descriptor.allow_interpretation
    lifecycle.admit_objection("publication", objection())
    held = reviewed_publication_descriptor(lifecycle, current, "claim-a", p, **kwargs)
    assert held.allow_statistics and not held.allow_interpretation
    table = reviewed_publication_descriptor(lifecycle, current, "table-b", p, **kwargs)
    assert table.allow_statistics


@pytest.mark.wal
def test_primary_review_is_rechecked_inside_dispatch_transaction(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.engine.scientific_authority import ScientificAuthority
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.workflow_entrypoints import (
        LEDGER_NAME,
        advance_adaptive_workflow,
    )
    from tests.test_investigation_invalidation import Worker, make, step
    from tests.test_review_hold_lifecycle import binding, complete, open_cycle
    flow = make(tmp_path, name=LEDGER_NAME)
    node = step("fit", primary=True)
    flow.insert(node.model_copy(update={"profile": node.profile.model_copy(update={
        "permitted_claims": ("qualified_procedure_result",), "qualification_evidence": ("a" * 64,)})}))
    flow.insert(step("independent"))
    lifecycle = ReviewHoldLifecycle(flow.authority, "study", adaptive_workflow=flow)
    lifecycle.register_entities((ReviewEntity(entity_id="fit", kind="fit"),))
    complete(lifecycle)
    planner = object.__new__(ScientificMethodPlanner)
    planner.methods = SimpleNamespace(root=tmp_path)
    planner.get = lambda session_id: {"session_id": session_id}
    original = ScientificAuthority.dispatch_attempt
    def interrupt(authority, *args, **kwargs):
        open_cycle(lifecycle, "newer-pending")
        return original(authority, *args, **kwargs)
    monkeypatch.setattr(ScientificAuthority, "dispatch_attempt", interrupt)
    worker = Worker()
    with pytest.raises(OperationBlocked):
        advance_adaptive_workflow(planner, "study", ledger=flow.ledger, actor="controller", executor=worker,
            allow_data_execution=True, review_lifecycle=lifecycle, review_binding=lambda _: binding())
    assert worker.calls == []


@pytest.mark.wal
def test_claim_cannot_review_a_different_entitled_family(tmp_path):
    from devbench.analysis.scientific_review import ActiveReviewPacket
    _, _, _, value = packet(tmp_path)
    changed = value.model_dump(mode="json")
    changed["claims"][0]["family_id"] = "other-family"
    changed["binding"]["claim_sha256"] = digest({key: changed[key] for key in
        ("controller_narrative", "validation_plan", "claims")})
    with pytest.raises(ValueError, match="family"):
        ActiveReviewPacket.model_validate_json(json.dumps(changed))


@pytest.mark.wal
def test_whole_family_hold_survives_export_from_an_unaffected_claim(tmp_path):
    from devbench.analysis.scientific_publication import (
        PublicationAccess,
        fixed_capsule,
    )
    from devbench.analysis.scientific_result_contract import (
        reviewed_publication_descriptor,
    )
    from devbench.analysis.scientific_review_holds import ReviewEntity
    from tests.test_review_hold_lifecycle import binding, complete, objection, review
    from tests.test_scientific_publication_policy import _allocation, _observed, profile
    lifecycle = review.__wrapped__(tmp_path)
    lifecycle.register_entities((ReviewEntity(entity_id="first-table", kind="table", dependencies=("fit",), family_id="first"),
        ReviewEntity(entity_id="second-table", kind="table", dependencies=("fit",), family_id="second")))
    current = binding(claim_sha256="b" * 64)
    complete(lifecycle, current=current)
    complete(lifecycle, "publication", current=current, touchpoint="publication")
    lifecycle.admit_objection("publication", objection(entity="first-table", category="FAMILY_ACCOUNTING"))
    allocation = _allocation()
    descriptor = reviewed_publication_descriptor(lifecycle, current, "second-table",
        profile("qualified_procedure_result", "exploratory_finding"), source_sha256="c" * 64,
        allocation=allocation, access=PublicationAccess(outcomes=True, statistics=True))
    export = fixed_capsule(descriptor, allocation, (_observed(allocation, "first"), _observed(allocation, "second")))
    first = [row for row in export["table"] if row["family_id"] == "first"]
    second = [row for row in export["table"] if row["family_id"] == "second"]
    assert all("p" not in row and "q_budget" not in row for row in first)
    assert any("p" in row for row in second)


@pytest.mark.wal
def test_v3_packet_cannot_enter_unscoped_legacy_dispatch(workspace_tmp_path):
    from devbench.analysis.scientific_review import prepare_active_packet
    from devbench.analysis.scientific_review_agent import request_review
    _, _, _, value = packet(workspace_tmp_path / "authority")
    receipt = prepare_active_packet(workspace_tmp_path / "packet", value)
    selector = ActiveSelector()
    with pytest.raises(ValueError, match="active review lifecycle"):
        request_review(
            workspace_tmp_path / "review",
            receipt["manifest_path"],
            expected_root=receipt["root_hash"],
            reviewer_label="challenger",
            selector=selector,
            allow_evidence_read=True,
            allow_review=True,
        )
    assert selector.contexts == []


@pytest.mark.wal
def test_failed_provider_retains_unknown_token_reservation(workspace_tmp_path):
    class Unavailable(ActiveSelector):
        def select(self, context, output, **kwargs):
            raise TimeoutError("provider unavailable after entry")
    result, lifecycle, _, _ = run_review(workspace_tmp_path, Unavailable())
    assert result["status"] == "unavailable"
    assert result["unknown_token_invocations"] == 1
    assert not result["observed_tokens_complete"]
    assert lifecycle.state()["accounting"]["tokens"] > 0


@pytest.mark.wal
def test_final_narrative_return_cannot_hide_reviewer_implementation_drift(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import scientific_review_agent as agent
    original = agent._review_sources
    class ChangedDuringReveal(ActiveSelector):
        def select(self, context, output, **kwargs):
            choice = super().select(context, output, **kwargs)
            if context["phase"] == "narrative_reveal":
                monkeypatch.setattr(agent, "_review_sources", lambda: {**original(), "changed": "a" * 64})
            return choice
    result, lifecycle, _, _ = run_review(workspace_tmp_path, ChangedDuringReveal())
    assert result["status"] == "failed"
    assert lifecycle.state()["cycles"]["cycle"]["phase"] == "failed"


@pytest.mark.wal
def test_bound_rereview_can_spend_the_reserved_resolution_budget(workspace_tmp_path):
    from devbench.analysis.scientific_review import ActiveReviewPacket
    from devbench.analysis.scientific_review_agent import (
        EntitledReviewQuery,
        request_active_review,
    )
    from devbench.analysis.scientific_review_holds import (
        ResolutionPredicate,
        ReviewObjection,
        ReviewObligation,
    )
    _, lifecycle, broker, value = run_review(workspace_tmp_path, ActiveSelector())
    predicate = ResolutionPredicate(kind="CounterevidenceResolved", owner_id="challenger",
        evidence_required=("new-evidence",), resumption_condition="Review the new relevant evidence")
    hold = lifecycle.admit_objection("cycle", ReviewObjection(category="COUNTEREVIDENCE",
        affected_entity_ids=("claim",), evidence_ids=("family-source",), requested_resolution="Inspect the new evidence",
        severity="material", necessity="Opposing effects may limit direction", predicate=predicate))
    content = {"counterevidence": "Additional synthetic sensitivity evidence"}
    lifecycle.register_evidence("new-evidence", digest(content), relevant_entity_ids=("claim",))
    lifecycle.controller_response(hold["hold_id"], response_sha256=digest("response"), relevant_evidence_ids=("new-evidence",))
    lifecycle.record_usage("cycle", invocations=16)  # Exhaust ordinary review's 20-invocation allowance.
    policy = value.query_handles[0].policy_sha256
    # A fresh, separately isolated challenger still shares the durable review
    # allowance. Reusing the old principal retains its narrative exposure.
    broker.register_context("rereview", principal_id="fresh-challenger", lineage_id="study", role="challenger",
        phase="publication_initial", policy_sha256=policy)
    broker.register_context("rereveal", principal_id="fresh-challenger", lineage_id="study", role="challenger",
        phase="narrative_reveal", policy_sha256=policy, parents=("rereview",))
    handle = EntitledReviewQuery("family-handle", family(), {}, broker.delivery("rereview", "family-source"))
    changed = value.model_dump(mode="json")
    changed["query_handles"] = [handle.descriptor().model_dump(mode="json")]
    changed["prior_exposure"] = {"family-handle": broker.exposure("rereview", "family-source")}
    changed["evidence"]["artifacts"] = {"new-evidence": content}
    changed["claims"][0]["evidence_ids"].append("new-evidence")
    changed["binding"].update(evidence_sha256=digest(changed["evidence"]), exposure_sha256=digest(changed["prior_exposure"]),
        claim_sha256=digest({key: changed[key] for key in ("controller_narrative", "validation_plan", "claims")}))
    revised = ActiveReviewPacket.model_validate_json(json.dumps(changed))
    lifecycle.register_obligations((ReviewObligation(obligation_id="donor_sensitivity", entity_id="claim", stage="claim"),),
                                  binding=revised.binding)
    result = request_active_review(
        workspace_tmp_path / "rereview", revised, lifecycle=lifecycle,
        cycle_id="response-cycle", reviewer_id="challenger",
        selector=ActiveSelector(), broker=broker, context_id="rereview",
        reveal_context_id="rereveal", controller_context_id="controller",
        query_handles=(handle,),
    )
    assert result["status"] == "completed" and result["budget_purpose"] == "resolution"
    assert result["accounting"]["invocations"] == 24
    assert lifecycle.state()["holds"][hold["hold_id"]]["status"] == "open"
