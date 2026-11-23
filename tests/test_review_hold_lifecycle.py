"""Synthetic adversarial controls for coordinator-owned scoped review holds."""
import importlib
import importlib.util

import pytest

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict, WorkflowOperationLedger


def api():
    assert importlib.util.find_spec("devbench.analysis.scientific_review_holds") is not None, (
        "T17 needs the shared authoritative scoped review lifecycle"
    )
    return importlib.import_module("devbench.analysis.scientific_review_holds")


def digest(letter="a"):
    return letter * 64


def binding(**changes):
    m = api()
    fields = dict.fromkeys(("facts_sha256", "inputs_sha256", "exposure_sha256", "design_sha256",
        "procedure_sha256", "policy_sha256", "evidence_sha256"), digest())
    return m.ReviewBinding(**{**fields, "obligations_sha256": m.obligations_digest(()), **changes})


@pytest.fixture
def review(tmp_path):
    m = api()
    ledger = WorkflowOperationLedger(tmp_path / "review.sqlite", scientific=True)
    ledger.scientific.commit("study", expected_revision=0, record={
        "kind": "binding", "record_id": "binding-1", **dict.fromkeys(("cohort_sha256",
        "design_sha256", "procedure_sha256", "family_allocation_sha256",
        "exposure_basis_sha256", "policy_sha256"), digest())})
    lifecycle = m.ReviewHoldLifecycle(ledger.scientific, "study",
        registered_verifiers={"alias-verifier": lambda proof: proof["proof_sha256"] == digest("b")},
        registered_checks=("read-evidence",))
    lifecycle.register_entities((
        m.ReviewEntity(entity_id="fit", kind="fit"),
        m.ReviewEntity(entity_id="table-a", kind="table", dependencies=("fit",), family_id="family"),
        m.ReviewEntity(entity_id="table-b", kind="table", dependencies=("fit",), family_id="family"),
        m.ReviewEntity(entity_id="claim-a", kind="claim", dependencies=("table-a",)),
        m.ReviewEntity(entity_id="claim-b", kind="claim", dependencies=("table-b",)),
        m.ReviewEntity(entity_id="independent", kind="fit"),
    ))
    lifecycle.register_evidence("source", digest(), relevant_entity_ids=("fit",))
    return lifecycle


def open_cycle(review, cycle_id="design-1", *, current=None, touchpoint="design", **kwargs):
    return review.open_cycle(cycle_id, touchpoint, current or binding(), reviewer_id="reviewer",
        context_id="ctx-" + cycle_id, narrative_sha256=digest("c"), validation_plan_sha256=digest("d"),
        **kwargs)


def complete(review, cycle_id="design-1", *, current=None, touchpoint="design", covered=(), decisions=(), entities=()):
    open_cycle(review, cycle_id, current=current, touchpoint=touchpoint, entity_ids=entities)
    review.record_initial_assessment(cycle_id, assessment_sha256=digest("e"), covered_obligation_ids=covered)
    review.reveal_narrative(cycle_id, narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    if decisions:
        content = {"resolution_assessments": [item.model_dump(mode="json") for item in decisions]}
        from devbench.engine.workflow_operations import _digest, _json
        return review.complete_cycle(cycle_id, assessment_sha256=_digest(_json(content)),
            covered_obligation_ids=covered, assessment_content=content)
    return review.complete_cycle(cycle_id, assessment_sha256=digest("f"), covered_obligation_ids=covered)


def objection(*, entity="claim-a", kind="CounterevidenceResolved", **changes):
    m = api()
    return m.ReviewObjection(**{
        "category": "COUNTEREVIDENCE", "affected_entity_ids": (entity,), "evidence_ids": ("source",),
        "missing_obligation_ids": (), "requested_resolution": "Examine the donor evidence",
        "severity": "material", "necessity": "The interpretation depends on the contradicted assumption",
        "predicate": m.ResolutionPredicate(kind=kind,
            owner_id="alias-verifier" if kind == "AliasProved" else "reviewer",
            evidence_required=("source",), resumption_condition="The exact objection is resolved"),
        **changes,
    })


def test_shared_lifecycle_is_available():
    assert api().ReviewHoldLifecycle


@pytest.mark.wal
def test_primary_requires_completed_current_design_review(review):
    with pytest.raises(OperationBlocked, match="design_review"):
        review.require_primary_fit("fit", binding())
    open_cycle(review)
    with pytest.raises(OperationBlocked, match="design_review"):
        review.require_primary_fit("fit", binding())
    review.record_initial_assessment("design-1", assessment_sha256=digest("e"))
    with pytest.raises(OperationBlocked, match="design_review"):
        review.require_primary_fit("fit", binding())
    review.reveal_narrative("design-1", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    review.complete_cycle("design-1", assessment_sha256=digest("f"))
    assert review.require_primary_fit("fit", binding()) is None


@pytest.mark.wal
@pytest.mark.parametrize("field", ["facts_sha256", "inputs_sha256", "exposure_sha256", "design_sha256",
    "procedure_sha256", "policy_sha256", "evidence_sha256", "obligations_sha256"])
def test_every_review_basis_change_invalidates_admission(review, field):
    complete(review)
    with pytest.raises(OperationBlocked, match="design_review"):
        review.require_primary_fit("fit", binding(**{field: digest("b")}))


@pytest.mark.wal
def test_reveal_requires_assessment_and_exact_controller_content(review):
    open_cycle(review)
    with pytest.raises(OperationConflict, match="initial"):
        review.reveal_narrative("design-1", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    review.record_initial_assessment("design-1", assessment_sha256=digest("e"))
    with pytest.raises(OperationConflict, match="narrative"):
        review.reveal_narrative("design-1", narrative_sha256=digest("b"), validation_plan_sha256=digest("d"))
    with pytest.raises(OperationConflict, match="narrative"):
        review.complete_cycle("design-1", assessment_sha256=digest("f"))


@pytest.mark.wal
def test_claim_hold_does_not_block_fit_or_numerical_table(review):
    complete(review)
    held = review.admit_objection("design-1", objection())
    assert held["status"] == "held"
    assert review.state()["holds"][held["hold_id"]]["scope"] == ["claim-a"]
    review.require_primary_fit("fit", binding())
    eligible = review.review_eligibility(binding(), digest("f"), entity_id="table-a")
    assert eligible.design == eligible.numerical == "current"


@pytest.mark.wal
def test_table_hold_expands_to_complete_family_and_dependents(review):
    complete(review)
    held = review.admit_objection("design-1", objection(entity="table-a"))
    assert set(review.state()["holds"][held["hold_id"]]["scope"]) == {
        "table-a", "table-b", "claim-a", "claim-b"}
    review.require_primary_fit("fit", binding())
    assert review.review_eligibility(binding(), digest("f"), entity_id="table-b").numerical == "held"


@pytest.mark.wal
def test_speculative_missing_covariate_cannot_hold_primary_fit(review):
    complete(review)
    answer = review.admit_objection("design-1", objection(entity="fit", category="MISSING_CHECK",
        necessity="RIN might matter", evidence_ids=(), missing_obligation_ids=()))
    assert answer["status"] == "clarification_required"
    assert not review.state()["holds"]
    review.require_primary_fit("fit", binding())


@pytest.mark.wal
def test_demonstrated_target_invalidity_blocks_only_dependents(review):
    complete(review)
    held = review.admit_objection("design-1", objection(entity="fit",
        category="DESIGN_ESTIMABILITY", target_invalidity_evidence_ids=("source",)))
    assert held["status"] == "held"
    with pytest.raises(OperationBlocked, match="review_hold"):
        review.require_primary_fit("fit", binding())
    review.require_primary_fit("independent", binding())


@pytest.mark.wal
@pytest.mark.parametrize("mutation", [
    {"category": "I_DISLIKE_THIS"}, {"severity": "absolute_truth"},
    {"affected_entity_ids": ["absent"]}, {"evidence_ids": ["fabricated"]},
    {"necessity": ""}, {"resolved": True},
])
def test_invalid_accusations_return_clarification_without_hold(review, mutation):
    complete(review)
    result = review.admit_objection("design-1", {**objection().model_dump(), **mutation})
    assert result["status"] == "clarification_required"
    assert not review.state()["holds"]


@pytest.mark.wal
def test_controller_json_cannot_resolve_and_check_completion_is_not_scientific_resolution(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    with pytest.raises(PermissionError):
        review.resolve_mechanical(hold_id, verifier_id="reviewer", proof_sha256=digest("b"), evidence_ids=("source",))
    with pytest.raises(OperationConflict):
        review.record_check_result(hold_id, action_id="invented", result_sha256=digest("b"), evidence_ids=("source",))
    assert review.state()["holds"][hold_id]["status"] == "open"


@pytest.mark.wal
def test_exact_registered_mechanical_proof_retains_original_objection(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection(kind="AliasProved"))["hold_id"]
    original = review.state()["holds"][hold_id]["objection"]
    with pytest.raises(OperationBlocked, match="predicate"):
        review.resolve_mechanical(hold_id, verifier_id="alias-verifier", proof_sha256=digest("c"), evidence_ids=("source",))
    review.resolve_mechanical(hold_id, verifier_id="alias-verifier", proof_sha256=digest("b"), evidence_ids=("source",))
    retained = review.state()["holds"][hold_id]
    assert retained["status"] == "resolved" and retained["objection"] == original


@pytest.mark.wal
def test_rhetoric_and_new_session_do_not_reset_response_limit(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.controller_response(hold_id, response_sha256=digest("a"))
    with pytest.raises(OperationBlocked, match="response_cycle"):
        review.controller_response(hold_id, response_sha256=digest("b"))
    complete(review, "design-2")
    repeated = review.admit_objection("design-2", objection(requested_resolution="Please reconsider in another way"))
    assert repeated["hold_id"] == hold_id
    with pytest.raises(OperationBlocked, match="response_cycle"):
        review.controller_response(hold_id, response_sha256=digest("c"))


@pytest.mark.wal
def test_only_new_relevant_evidence_allows_another_bounded_cycle(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.controller_response(hold_id, response_sha256=digest("a"))
    review.register_evidence("irrelevant", digest("b"), relevant_entity_ids=("independent",))
    with pytest.raises(OperationBlocked, match="relevant"):
        review.controller_response(hold_id, response_sha256=digest("b"), relevant_evidence_ids=("irrelevant",))
    review.register_evidence("new", digest("c"), relevant_entity_ids=("claim-a",))
    review.controller_response(hold_id, response_sha256=digest("b"), relevant_evidence_ids=("new",))
    with pytest.raises(OperationBlocked, match="response_cycle"):
        review.controller_response(hold_id, response_sha256=digest("c"), relevant_evidence_ids=("new",))


@pytest.mark.wal
def test_persistent_reopen_preserves_holds_cycles_budget_and_original_evidence(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.record_usage("design-1", invocations=2, turns=3, queries=4, tokens=125)
    reopened = api().ReviewHoldLifecycle(WorkflowOperationLedger(review.ledger.path).scientific, "study")
    assert reopened.state() == review.state()
    assert reopened.state()["holds"][hold_id]["status"] == "open"


@pytest.mark.wal
def test_publication_requires_fresh_evidence_then_actual_narrative(review):
    complete(review)
    first = review.review_eligibility(binding(), digest("f"), entity_id="claim-a")
    assert first.design == "current" and first.narrative == "unavailable"
    open_cycle(review, "publication", touchpoint="publication")
    review.record_initial_assessment("publication", assessment_sha256=digest("e"))
    second = review.review_eligibility(binding(), digest("f"), entity_id="claim-a")
    assert second.evidence_first == "current" and second.narrative == "pending"
    review.reveal_narrative("publication", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    review.complete_cycle("publication", assessment_sha256=digest("f"))
    third = review.review_eligibility(binding(), digest("f"), entity_id="claim-a")
    assert third.evidence_first == third.narrative == "current"


@pytest.mark.wal
def test_pre_fit_obligation_needs_verified_bound_proof_post_fit_does_not_block_fit(review):
    m = api()
    obligations = (
        m.ReviewObligation(obligation_id="identity", entity_id="fit", stage="pre_fit", evidence_ids=("source",)),
        m.ReviewObligation(obligation_id="sensitivity", entity_id="claim-a", stage="post_fit", evidence_ids=("source",)),
    )
    current = binding(obligations_sha256=m.obligations_digest(obligations))
    review.register_obligations(obligations, binding=current)
    complete(review, current=current, covered=("identity", "sensitivity"))
    with pytest.raises(OperationBlocked, match="pre_fit_obligation"):
        review.require_primary_fit("fit", current)
    review.satisfy_obligation("identity", current, verifier_id="alias-verifier",
        proof_sha256=digest("b"), evidence_ids=("source",))
    review.require_primary_fit("fit", current)
    assert review.review_eligibility(current, digest("f"), entity_id="claim-a").narrative != "current"


@pytest.mark.wal
def test_assessment_cannot_complete_without_registered_obligation_coverage(review):
    m = api()
    obligation = m.ReviewObligation(obligation_id="identity", entity_id="fit", stage="pre_fit", evidence_ids=("source",))
    current = binding(obligations_sha256=m.obligations_digest((obligation,)))
    with pytest.raises(OperationConflict, match="obligation"):
        open_cycle(review, current=current)
    review.register_obligations((obligation,), binding=current)
    open_cycle(review, current=current)
    review.record_initial_assessment("design-1", assessment_sha256=digest("e"))
    review.reveal_narrative("design-1", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    with pytest.raises(OperationBlocked, match="obligation"):
        review.complete_cycle("design-1", assessment_sha256=digest("f"))


@pytest.mark.wal
def test_budget_dimensions_and_resolution_reserves_cannot_reset_on_reopen(review):
    open_cycle(review)
    budget = review.budget
    review.record_usage("design-1", invocations=budget.invocations-budget.resolution_invocations,
        queries=budget.queries-budget.resolution_queries, tokens=budget.tokens-budget.resolution_tokens)
    for dimension in ("invocations", "queries", "tokens"):
        with pytest.raises(OperationBlocked, match="budget"):
            review.record_usage("design-1", **{dimension: 1})
    review.record_usage("design-1", invocations=1, queries=1, tokens=1, purpose="resolution")
    reopened = api().ReviewHoldLifecycle(review.authority, "study")
    with pytest.raises(OperationBlocked, match="budget"):
        reopened.record_usage("design-1", invocations=1)


@pytest.mark.wal
def test_current_context_handles_and_prior_exposure_are_retained(review):
    cycle = open_cycle(review, exposure="target_outcome", exposure_receipt_ids=("receipt-1",),
        entitled_handle_ids=("family-query",))
    assert cycle["exposure"] == "target_outcome"
    assert cycle["exposure_receipt_ids"] == ["receipt-1"]
    assert cycle["entitled_handle_ids"] == ["family-query"]
    with pytest.raises(OperationConflict, match="fresh"):
        review.open_cycle("publication", "publication", binding(), reviewer_id="reviewer",
            context_id="ctx-design-1", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))


@pytest.mark.wal
def test_stale_revision_cannot_mutate_hold_and_graph_cycles_are_rejected(review):
    complete(review)
    with pytest.raises(OperationConflict, match="revision"):
        review.admit_objection("design-1", objection(), expected_revision=0)
    m = api()
    with pytest.raises(ValueError, match="cycle"):
        review.register_entities((m.ReviewEntity(entity_id="x", kind="claim", dependencies=("y",)),
            m.ReviewEntity(entity_id="y", kind="claim", dependencies=("x",))))


@pytest.mark.wal
def test_withdrawing_claim_withholds_it_without_declaring_scientific_resolution(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.withdraw_use("claim-a", reason="Remove the unsupported interpretation")
    assert review.state()["holds"][hold_id]["status"] == "open"
    assert review.state()["withdrawn"]["claim-a"]
    review.require_primary_fit("fit", binding())


@pytest.mark.wal
def test_invocation_tokens_remain_reserved_after_crash_and_actual_usage_is_separate(review):
    open_cycle(review)
    rid = review.reserve_invocation("design-1", maximum_tokens=2048)
    reopened = api().ReviewHoldLifecycle(review.authority, "study")
    assert reopened.state()["accounting"]["tokens"] == 2048
    assert reopened.state()["reservations"][rid]["status"] == "reserved"
    reopened.settle_invocation("design-1", rid, tokens=132)
    assert reopened.state()["accounting"]["tokens"] == 2048
    assert reopened.state()["observed_tokens"] == 132
    with pytest.raises(OperationConflict, match="settled"):
        reopened.settle_invocation("design-1", rid, tokens=1)


@pytest.mark.wal
def test_token_reservation_cannot_be_settled_by_another_cycle_and_accounts_overrun(review):
    open_cycle(review)
    rid = review.reserve_invocation("design-1", maximum_tokens=2048)
    open_cycle(review, "design-2")
    with pytest.raises(OperationConflict, match="reservation"):
        review.settle_invocation("design-2", rid, tokens=20)
    result = review.settle_invocation("design-1", rid, tokens=2049)
    assert result["status"] == "overrun"
    assert review.state()["accounting"]["tokens"] == review.state()["observed_tokens"] == 2049
    with pytest.raises(OperationBlocked, match="overrun"):
        review.record_usage("design-2", invocations=1)


@pytest.mark.wal
def test_scientific_obligation_cannot_be_satisfied_by_mechanical_verifier(review):
    m = api()
    obligation = m.ReviewObligation(obligation_id="adequacy", entity_id="fit", stage="pre_fit",
        evidence_ids=("source",), predicate=objection().predicate)
    current = binding(obligations_sha256=m.obligations_digest((obligation,)))
    review.register_obligations((obligation,), binding=current)
    complete(review, current=current, covered=("adequacy",))
    with pytest.raises(PermissionError, match="scientific"):
        review.satisfy_obligation("adequacy", current, verifier_id="alias-verifier",
            proof_sha256=digest("b"), evidence_ids=("source",))


@pytest.mark.wal
def test_post_fit_obligation_cannot_be_used_as_fit_prerequisite(review):
    m = api()
    obligation = m.ReviewObligation(obligation_id="sensitivity", entity_id="fit", stage="post_fit", evidence_ids=("source",))
    current = binding(obligations_sha256=m.obligations_digest((obligation,)))
    review.register_obligations((obligation,), binding=current)
    complete(review, current=current, covered=("sensitivity",))
    result = review.admit_objection("design-1", objection(entity="fit", fit_prerequisite_id="sensitivity"))
    assert result["status"] == "clarification_required"
    review.require_primary_fit("fit", current)


@pytest.mark.wal
def test_publication_new_evidence_keeps_explicit_design_review_but_not_changed_facts(review):
    complete(review)
    publication = binding(evidence_sha256=digest("b"), claim_sha256=digest("c"))
    complete(review, "publication", touchpoint="publication", current=publication)
    current = review.review_eligibility(publication, digest("f"), entity_id="claim-a", design_binding=binding())
    assert current.design == current.numerical == current.narrative == "current"
    stale = review.review_eligibility(publication.model_copy(update={"facts_sha256": digest("d")}),
        digest("f"), entity_id="claim-a", design_binding=binding())
    assert stale.design == "stale" and stale.numerical == "stale"


@pytest.mark.wal
def test_adaptive_fit_hold_is_atomic_and_other_step_holds_survive_resolution(review):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import step

    flow = AdaptiveWorkflow(review.ledger, "study")
    flow.insert(step("fit"))
    coupled = api().ReviewHoldLifecycle(review.authority, "study", adaptive_workflow=flow,
        registered_verifiers=review.registered_verifiers)
    complete(coupled)
    hold_id = coupled.admit_objection("design-1", objection(entity="fit", kind="AliasProved",
        target_invalidity_evidence_ids=("source",)))["hold_id"]
    with review.ledger._database() as db:
        assert flow._state(db).holds["fit"]["reason"] == "t17_review"
    flow.hold("fit", "deterministic_failure", evidence_sha256=digest("d"))
    coupled.resolve_mechanical(hold_id, verifier_id="alias-verifier", proof_sha256=digest("b"), evidence_ids=("source",))
    with review.ledger._database() as db:
        assert flow._state(db).holds["fit"]["reason"] == "deterministic_failure"


@pytest.mark.wal
def test_check_request_schedules_existing_dag_action_and_cannot_claim_missing_result(review):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import artifact, link, step

    m = api()
    flow = AdaptiveWorkflow(review.ledger, "study")
    frozen = artifact("frozen-input")
    flow.register_artifact(frozen)
    runner = m.ReviewHoldLifecycle(review.authority, "study", registered_checks=("synthetic",))
    runner.register_evidence("check-input", frozen.sha256, relevant_entity_ids=("claim-a",))
    complete(runner)
    request = m.CheckRequest(threat="A donor-specific effect may explain the selected interpretation",
        affected_claim_ids=("claim-a",), required_evidence=("source",), registered_check_id="synthetic",
        predicate=objection().predicate, input_evidence_ids=("check-input",))
    hold_id = runner.admit_objection("design-1", objection(check_request=request))["hold_id"]
    trace = runner.schedule_check(hold_id, flow, step("resolving-check", link("counts", "frozen-input", immutable=True)))
    assert trace["action_id"] == "resolving-check" and trace["status"] == "scheduled"
    with review.ledger._database() as db:
        assert "resolving-check" in flow._state(db).nodes
    with pytest.raises(OperationConflict, match="result"):
        runner.record_check_result(hold_id, action_id="resolving-check", result_sha256=digest("b"), evidence_ids=("source",))
    assert runner.state()["holds"][hold_id]["status"] == "open"


@pytest.mark.wal
def test_new_session_cannot_repeat_unchanged_rereview_twice(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    with pytest.raises(OperationBlocked, match="rereview"):
        open_cycle(review, "renamed")
    review.controller_response(hold_id, response_sha256=digest("b"))
    complete(review, "rereview")
    with pytest.raises(OperationBlocked, match="rereview"):
        open_cycle(review, "renamed-again")


@pytest.mark.wal
def test_scientific_resolution_cannot_use_another_reviewer_or_unrelated_evidence(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.register_evidence("irrelevant", digest("b"), relevant_entity_ids=("independent",))
    newer = binding(evidence_sha256=digest("b"), claim_sha256=digest("c"))
    decision = api().ResolutionAssessment(hold_id=hold_id, binding_sha256=newer.sha256(),
        predicate_sha256=objection().predicate.sha256(), satisfied=True, rationale="Reviewed the exact counterevidence",
        evidence_ids=("source", "irrelevant"))
    reviewed = complete(review, "publication", current=newer, touchpoint="publication", decisions=(decision,))
    with pytest.raises(OperationBlocked, match="relevant"):
        review.resolve_scientific(hold_id, cycle_id="publication", evidence_ids=("source", "irrelevant"),
                                 assessment_sha256=reviewed["assessment_sha256"])


@pytest.mark.wal
def test_read_only_check_cannot_schedule_registered_new_fit_or_missing_input(review):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import step

    m = api()
    flow = AdaptiveWorkflow(review.ledger, "study")
    runner = m.ReviewHoldLifecycle(review.authority, "study", registered_checks={"synthetic": "new_fit"})
    complete(runner)
    request = m.CheckRequest(threat="Threat to the interpretation", affected_claim_ids=("claim-a",),
        required_evidence=("source",), registered_check_id="synthetic", predicate=objection().predicate)
    invalid = runner.admit_objection("design-1", objection(check_request=request))
    assert invalid["status"] == "clarification_required"
    runner = m.ReviewHoldLifecycle(review.authority, "study", registered_checks={"synthetic": "read_only"})
    hold_id = runner.admit_objection("design-1", objection(check_request=request))["hold_id"]
    with pytest.raises(OperationBlocked, match="input"):
        runner.schedule_check(hold_id, flow, step("unbound-check"))


@pytest.mark.wal
def test_generic_completed_review_cannot_satisfy_scientific_hold(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.register_evidence("resolution", digest("b"), relevant_entity_ids=("claim-a",))
    newer = binding(evidence_sha256=digest("b"), claim_sha256=digest("c"))
    complete(review, "publication", current=newer, touchpoint="publication")
    with pytest.raises(OperationBlocked, match="explicit"):
        review.resolve_scientific(hold_id, cycle_id="publication", evidence_ids=("source", "resolution"),
                                 assessment_sha256=digest("f"))


@pytest.mark.wal
def test_scientific_hold_needs_explicit_exact_bound_decision(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.register_evidence("resolution", digest("b"), relevant_entity_ids=("claim-a",))
    newer = binding(evidence_sha256=digest("b"), claim_sha256=digest("c"))
    decision = api().ResolutionAssessment(hold_id=hold_id, binding_sha256=newer.sha256(),
        predicate_sha256=objection().predicate.sha256(), satisfied=True, rationale="The new donor evidence resolves this objection",
        evidence_ids=("source", "resolution"))
    completed = complete(review, "publication", current=newer, touchpoint="publication", decisions=(decision,))
    review.resolve_scientific(hold_id, cycle_id="publication", evidence_ids=("source", "resolution"),
                             assessment_sha256=completed["assessment_sha256"])
    assert review.state()["holds"][hold_id]["status"] == "resolved"


@pytest.mark.wal
def test_detached_or_false_resolution_decision_cannot_close_hold(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.register_evidence("resolution", digest("b"), relevant_entity_ids=("claim-a",))
    newer = binding(evidence_sha256=digest("b"), claim_sha256=digest("c"))
    decision = api().ResolutionAssessment(hold_id=hold_id, binding_sha256=newer.sha256(),
        predicate_sha256=objection().predicate.sha256(), satisfied=False, rationale="Still unresolved",
        evidence_ids=("source", "resolution"))
    open_cycle(review, "publication", current=newer, touchpoint="publication")
    review.record_initial_assessment("publication", assessment_sha256=digest("e"))
    review.reveal_narrative("publication", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    with pytest.raises(OperationConflict, match="content"):
        review.complete_cycle("publication", assessment_sha256=digest("f"),
            assessment_content={"resolution_assessments": [decision.model_dump(mode="json")]})
    from devbench.engine.workflow_operations import _digest, _json
    content = {"resolution_assessments": [decision.model_dump(mode="json")]}
    reviewed = review.complete_cycle("publication", assessment_sha256=_digest(_json(content)), assessment_content=content)
    with pytest.raises(OperationBlocked, match="explicit"):
        review.resolve_scientific(hold_id, cycle_id="publication", evidence_ids=("source", "resolution"),
                                 assessment_sha256=reviewed["assessment_sha256"])


@pytest.mark.wal
def test_corrected_design_closure_requires_registered_dependency_revalidation(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.register_evidence("correction", digest("b"), relevant_entity_ids=("claim-a",))
    newer = binding(evidence_sha256=digest("b"), design_sha256=digest("c"))
    decision = api().ResolutionAssessment(hold_id=hold_id, binding_sha256=newer.sha256(),
        predicate_sha256=objection().predicate.sha256(), satisfied=True, rationale="The corrected design addresses the exact defect",
        evidence_ids=("source", "correction"))
    reviewed = complete(review, "publication", current=newer, touchpoint="publication", decisions=(decision,))
    with pytest.raises(OperationConflict, match="revalidation"):
        review.resolve_scientific(hold_id, cycle_id="publication", evidence_ids=("source", "correction"),
                                 assessment_sha256=reviewed["assessment_sha256"])
    review.revalidate_hold(hold_id, newer, verifier_id="alias-verifier", proof_sha256=digest("b"), evidence_ids=("correction",))
    review.resolve_scientific(hold_id, cycle_id="publication", evidence_ids=("source", "correction"),
                             assessment_sha256=reviewed["assessment_sha256"])
    assert review.state()["holds"][hold_id]["objection"]["evidence_ids"] == ["source"]


@pytest.mark.wal
def test_other_reviewer_cannot_assert_explicit_hold_withdrawal(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.register_evidence("resolution", digest("b"), relevant_entity_ids=("claim-a",))
    current = binding(evidence_sha256=digest("b"))
    review.open_cycle("other", "publication", current, reviewer_id="unassigned", context_id="other",
        narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    review.record_initial_assessment("other", assessment_sha256=digest("e"))
    review.reveal_narrative("other", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    decision = api().ResolutionAssessment(hold_id=hold_id, binding_sha256=current.sha256(),
        predicate_sha256=objection().predicate.sha256(), satisfied=True, rationale="Attempted reassignment",
        evidence_ids=("source", "resolution"))
    from devbench.engine.workflow_operations import _digest, _json
    content = {"resolution_assessments": [decision.model_dump(mode="json")]}
    with pytest.raises(OperationConflict, match="assigned"):
        review.complete_cycle("other", assessment_sha256=_digest(_json(content)), assessment_content=content)


@pytest.mark.wal
def test_scientific_obligation_requires_explicit_predicate_decision(review):
    m = api()
    obligation = m.ReviewObligation(obligation_id="adequacy", entity_id="fit", stage="pre_fit",
        evidence_ids=("source",), predicate=objection().predicate)
    current = binding(obligations_sha256=m.obligations_digest((obligation,)))
    review.register_obligations((obligation,), binding=current)
    complete(review, current=current, covered=("adequacy",))
    with pytest.raises(OperationBlocked, match="explicit"):
        review.assess_obligation("adequacy", current, cycle_id="design-1", evidence_ids=("source",),
                                 assessment_sha256=digest("f"))
    decision = m.ResolutionAssessment(obligation_id="adequacy", binding_sha256=current.sha256(),
        predicate_sha256=obligation.predicate.sha256(), satisfied=True, rationale="Assumption reviewed against the cited data",
        evidence_ids=("source",))
    reviewed = complete(review, "new-review", current=current, covered=("adequacy",), decisions=(decision,))
    review.assess_obligation("adequacy", current, cycle_id="new-review", evidence_ids=("source",),
                             assessment_sha256=reviewed["assessment_sha256"])
    review.require_primary_fit("fit", current)


@pytest.mark.wal
def test_adaptive_attachment_survives_reopen_before_new_hold(review):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import step

    flow = AdaptiveWorkflow(review.ledger, "study")
    flow.insert(step("fit"))
    coupled = api().ReviewHoldLifecycle(review.authority, "study", adaptive_workflow=flow)
    complete(coupled)
    reopened = api().ReviewHoldLifecycle(review.authority, "study")
    reopened.admit_objection("design-1", objection(entity="fit", target_invalidity_evidence_ids=("source",)))
    with review.ledger._database() as db:
        assert flow._state(db).holds["fit"]["reason"] == "t17_review"


@pytest.mark.wal
def test_equal_byte_evidence_alias_cannot_reset_objection_or_response(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.controller_response(hold_id, response_sha256=digest("a"))
    review.register_evidence("renamed-source", digest(), relevant_entity_ids=("fit",))
    with pytest.raises(OperationBlocked, match="response_cycle"):
        review.controller_response(hold_id, response_sha256=digest("b"), relevant_evidence_ids=("renamed-source",))
    complete(review, "rereview")
    repeated = review.admit_objection("rereview", objection(evidence_ids=("renamed-source",)))
    assert repeated["hold_id"] == hold_id
    with pytest.raises(OperationBlocked, match="rereview"):
        open_cycle(review, "repeat")


@pytest.mark.wal
def test_query_receipt_can_ground_same_cycle_without_rewriting_packet_binding(review):
    open_cycle(review, entitled_handle_ids=("family",))
    original = review.state()["cycles"]["design-1"]["binding_sha256"]
    review.record_query_evidence("design-1", "query-receipt", digest("b"), relevant_entity_ids=("claim-a",))
    current = review.state()["cycles"]["design-1"]
    assert current["observed_evidence_ids"] == ["query-receipt"]
    assert current["binding_sha256"] == original
    result = review.admit_objection("design-1", objection(evidence_ids=("query-receipt",)))
    assert result["status"] == "held"


@pytest.mark.wal
def test_query_evidence_rejects_unentitled_source_and_completed_cycle(review):
    open_cycle(review, entitled_handle_ids=("family",))
    with pytest.raises(PermissionError, match="entitled"):
        review.record_query_evidence("design-1", "bad-receipt", digest("b"),
            source_handle_id="another", relevant_entity_ids=("claim-a",))
    review.record_initial_assessment("design-1", assessment_sha256=digest("e"))
    review.reveal_narrative("design-1", narrative_sha256=digest("c"), validation_plan_sha256=digest("d"))
    review.complete_cycle("design-1", assessment_sha256=digest("f"))
    with pytest.raises(OperationConflict, match="completed"):
        review.record_query_evidence("design-1", "late-receipt", digest("b"), relevant_entity_ids=("claim-a",))


@pytest.mark.wal
def test_primary_admission_accepts_only_same_ledger_transaction(review, tmp_path):
    complete(review)
    with review.ledger._database() as db:
        db.execute("BEGIN")
        review.require_primary_fit("fit", binding(), connection=db)
    other = WorkflowOperationLedger(tmp_path / "unrelated.sqlite", scientific=True)
    with other._database() as db:
        with pytest.raises(PermissionError, match="ledger"):
            review.require_primary_fit("fit", binding(), connection=db)


@pytest.mark.wal
@pytest.mark.parametrize("invalidate_before_record", [False, True])
def test_completed_registered_check_retains_result_but_scientific_hold_stays_open(review, monkeypatch, invalidate_before_record):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import artifact, link, step, Worker
    from tests.test_investigation_publication_epochs import authorize

    authority = review.authority
    with review.ledger._database() as db:
        revision = authority._revision(db, "study")
    authority.commit("study", expected_revision=revision, record={"kind": "grant", "record_id": "grant",
        "binding_id": "binding-1", "grant_sha256": digest("b")})
    authority.activate_execution("study", expected_revision=revision + 1, binding_id="binding-1",
        grant_id="grant", max_jobs=4, authorize=authorize)
    flow = AdaptiveWorkflow(review.ledger, "study")
    source = artifact("frozen-input")
    flow.register_artifact(source)
    runner = api().ReviewHoldLifecycle(authority, "study", registered_checks=("synthetic",))
    runner.register_evidence("check-input", source.sha256, relevant_entity_ids=("claim-a",))
    complete(runner)
    request = api().CheckRequest(threat="A single donor may explain this claim", affected_claim_ids=("claim-a",),
        required_evidence=("check-output",), registered_check_id="synthetic", predicate=objection().predicate,
        input_evidence_ids=("check-input",))
    hold_id = runner.admit_objection("design-1", objection(check_request=request))["hold_id"]
    runner.schedule_check(hold_id, flow, step("resolving-check", link("counts", "frozen-input", immutable=True)))
    outcome = flow.dispatch("resolving-check", Worker(), actor="synthetic-coordinator")
    output_sha = outcome["outputs"]["out"]["sha256"]
    runner.register_evidence("check-output", output_sha, relevant_entity_ids=("claim-a",))
    if invalidate_before_record:
        original_write = runner._write

        def concurrent_revision(action, build, **kwargs):
            if action == "record_check_result":
                flow.revise(step("resolving-check", link("counts", "frozen-input", immutable=True)),
                    reason="design_change", evidence_sha256=digest("f"))
            return original_write(action, build, **kwargs)

        monkeypatch.setattr(runner, "_write", concurrent_revision)
        with pytest.raises(OperationConflict, match="result"):
            runner.record_check_result(hold_id, action_id="resolving-check", result_sha256=output_sha,
                                       evidence_ids=("check-output",))
        assert runner.state()["holds"][hold_id]["status"] == "open"
        return
    trace = runner.record_check_result(hold_id, action_id="resolving-check", result_sha256=output_sha,
                                      evidence_ids=("check-output",))
    assert trace["status"] == "completed" and trace["result"]["attempt_id"] == outcome["attempt_id"]
    assert runner.state()["holds"][hold_id]["status"] == "open"


@pytest.mark.wal
def test_failed_review_commit_rolls_back_adaptive_hold_in_same_transaction(review):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import step

    flow = AdaptiveWorkflow(review.ledger, "study")
    flow.insert(step("fit"))
    complete(review)
    with review.ledger._database() as db:
        db.execute("""CREATE TRIGGER fail_review BEFORE INSERT ON scientific_records
            WHEN NEW.record_id LIKE 'review-%'
            BEGIN SELECT RAISE(ABORT,'synthetic review write failure'); END""")
    with pytest.raises(OperationConflict):
        review.admit_objection("design-1", objection(entity="fit", target_invalidity_evidence_ids=("source",)))
    assert not review.state()["holds"]
    with review.ledger._database() as db:
        assert not flow._state(db).holds


@pytest.mark.wal
def test_new_family_dependents_cannot_escape_existing_table_hold(review):
    complete(review)
    hold_id = review.admit_objection("design-1", objection(entity="table-a"))["hold_id"]
    m = api()
    review.register_entities((m.ReviewEntity(entity_id="table-c", kind="table", family_id="family", dependencies=("fit",)),
        m.ReviewEntity(entity_id="claim-c", kind="claim", dependencies=("table-c",))))
    assert {"table-c", "claim-c"} <= set(review.state()["holds"][hold_id]["scope"])
    assert review.review_eligibility(binding(), digest("b"), entity_id="claim-c").numerical == "held"


@pytest.mark.wal
def test_unrelated_claim_cycle_is_not_blocked_by_another_claim_response_limit(review):
    complete(review)
    complete(review, "publication-a", touchpoint="publication", entities=("claim-a",))
    review.admit_objection("publication-a", objection())
    opened = open_cycle(review, "publication-b", touchpoint="publication", entity_ids=("claim-b",))
    assert opened["subject_entity_ids"] == ["claim-b"]
    result = review.admit_objection("publication-b", objection())
    assert result["status"] == "clarification_required"


@pytest.mark.wal
def test_review_of_one_claim_or_fit_cannot_approve_an_unreviewed_independent_subject(review):
    complete(review, entities=("fit",))
    review.require_primary_fit("fit", binding())
    with pytest.raises(OperationBlocked, match="design_review"):
        review.require_primary_fit("independent", binding())
    complete(review, "publication-a", touchpoint="publication", entities=("claim-a",))
    assert review.review_eligibility(binding(), digest("b"), entity_id="claim-a").narrative == "current"
    assert review.review_eligibility(binding(), digest("b"), entity_id="claim-b").narrative == "unavailable"


@pytest.mark.wal
def test_fresh_query_receipts_for_same_scientific_content_do_not_reset_response(review):
    open_cycle(review, entitled_handle_ids=("family",))
    review.record_query_evidence("design-1", "receipt-one", digest("b"),
        relevant_entity_ids=("claim-a",), content_identity_sha256=digest("d"))
    review.record_query_evidence("design-1", "receipt-two", digest("c"),
        relevant_entity_ids=("claim-a",), content_identity_sha256=digest("d"))
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    review.controller_response(hold_id, response_sha256=digest("b"))
    review.controller_response(hold_id, response_sha256=digest("c"), relevant_evidence_ids=("receipt-one",))
    with pytest.raises(OperationBlocked, match="response_cycle"):
        review.controller_response(hold_id, response_sha256=digest("d"), relevant_evidence_ids=("receipt-two",))


@pytest.mark.wal
def test_claim_only_dependency_cannot_implicitly_become_fit_blocking(review):
    m = api()
    review.register_entities((m.ReviewEntity(entity_id="followup-fit", kind="fit", dependencies=("claim-a",)),))
    complete(review)
    hold_id = review.admit_objection("design-1", objection())["hold_id"]
    assert "followup-fit" not in review.state()["holds"][hold_id]["scope"]
    review.require_primary_fit("followup-fit", binding())


@pytest.mark.wal
def test_shared_blocking_projection_keeps_claim_only_design_and_family_rules(review):
    complete(review)
    review.admit_objection("design-1", objection())
    assert review.blocking_entities(binding(), touchpoint="design", entity_ids=("fit", "claim-a")) == ()
    assert review.blocking_entities(binding(), touchpoint="publication", entity_ids=("claim-a", "claim-b")) == ("claim-a",)
    review.admit_objection("design-1", objection(entity="table-a"))
    assert review.blocking_entities(binding(), touchpoint="publication", entity_ids=("table-b", "claim-b")) == ("claim-b", "table-b")


@pytest.mark.wal
def test_resolved_hold_cannot_supply_proof_for_a_different_predicate_or_check(review):
    m = api()
    review.register_evidence("alias-one", digest("b"), relevant_entity_ids=("claim-a",))
    review.register_evidence("same-proof-new-name", digest("b"), relevant_entity_ids=("claim-a",))
    complete(review)
    first_predicate = m.ResolutionPredicate(kind="AliasProved", owner_id="alias-verifier",
        evidence_required=("alias-one",), resumption_condition="Verify the named alias")
    first = review.admit_objection("design-1", objection(category="UNIT_FACT", predicate=first_predicate))["hold_id"]
    review.resolve_mechanical(first, verifier_id="alias-verifier", proof_sha256=digest("b"), evidence_ids=("alias-one",))
    second_predicate = first_predicate.model_copy(update={"evidence_required": ("alias-two",)})
    second = review.admit_objection("design-1", objection(category="UNIT_FACT", predicate=second_predicate))
    assert second["status"] == "held" and second["hold_id"] != first
    with pytest.raises(OperationBlocked, match="evidence_incomplete"):
        review.resolve_mechanical(second["hold_id"], verifier_id="alias-verifier", proof_sha256=digest("b"), evidence_ids=("alias-one",))
    renamed = first_predicate.model_copy(update={"evidence_required": ("same-proof-new-name",),
        "resumption_condition": "Please examine the same exact alias again"})
    assert review.admit_objection("design-1", objection(category="UNIT_FACT", predicate=renamed))["hold_id"] == first
    request = m.CheckRequest(threat="Verify the relevant alias", affected_claim_ids=("claim-a",),
        required_evidence=("check-output-one",), registered_check_id="read-evidence",
        predicate=first_predicate, input_evidence_ids=("source",))
    checked = review.admit_objection("design-1", objection(category="UNIT_FACT", predicate=first_predicate,
        check_request=request))
    assert checked["status"] == "held" and checked["hold_id"] != first
    changed_request = request.model_copy(update={"required_evidence": ("check-output-two",)})
    changed = review.admit_objection("design-1", objection(category="UNIT_FACT", predicate=first_predicate,
        check_request=changed_request))
    assert changed["status"] == "held" and changed["hold_id"] != checked["hold_id"]
