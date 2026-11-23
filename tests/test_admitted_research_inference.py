"""Synthetic custody checks for the provisional research claim lane."""

import pytest
from pydantic import ValidationError

from tests.test_investigation_invalidation import artifact, digest, link, make, step
from tests.test_review_hold_lifecycle import binding

RESEARCH = "admitted_research_inference"


def research_step(**changes):
    from devbench.platform.adaptive_workflow_state import AdaptiveStep

    value = step("fit").model_dump()
    value.update(purpose=RESEARCH, primary=True, regime="B-I", **changes)
    value["profile"]["permitted_claims"] = (RESEARCH,)
    return AdaptiveStep.model_validate(value)


def test_adaptive_research_scope_does_not_require_external_qualification():
    node = research_step()
    assert node.profile.permitted_claims == (RESEARCH,)
    assert node.profile.qualification_evidence == ()
    with pytest.raises(ValidationError, match="qualification evidence"):
        type(node.profile).model_validate(
            {
                **node.profile.model_dump(),
                "permitted_claims": ("qualified_procedure_result",),
            }
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"oci_method_version_id": "a" * 64},
        {"held_out": True},
    ],
)
def test_adaptive_research_scope_excludes_oci_and_held_out_uses(changes):
    with pytest.raises(ValidationError, match="Tier A"):
        research_step(**changes)


def test_publication_does_not_promote_a_research_claim():
    from devbench.analysis._publication_policy import (
        PublicationAccess,
        build_publication_descriptor,
    )

    descriptor = build_publication_descriptor(
        research_step().profile,
        source_sha256=digest("research-result"),
        scope="adaptive",
        access=PublicationAccess(outcomes=True, statistics=True),
    )
    assert RESEARCH not in descriptor.allowed_claims
    assert not descriptor.allow_statistics


def _cycle(lifecycle, broker, touchpoint, policy, *, entity_ids=("fit",), suffix=""):
    context = touchpoint + "-challenger" + suffix
    broker.register_context(
        context,
        principal_id=context,
        lineage_id=lifecycle.lineage_id,
        role="challenger",
        phase=f"{touchpoint}_initial",
        policy_sha256=policy,
    )
    cycle = touchpoint + "-review" + suffix
    narrative, plan = digest("narrative"), digest("plan")
    lifecycle.open_cycle(
        cycle,
        touchpoint,
        binding(),
        reviewer_id=context,
        context_id=context,
        narrative_sha256=narrative,
        validation_plan_sha256=plan,
        exposure="structural",
        evidence_ids=("checked-design" + suffix,),
        entity_ids=entity_ids,
    )
    lifecycle.record_initial_assessment(
        cycle, assessment_sha256=digest(cycle + "-initial")
    )
    lifecycle.reveal_narrative(
        cycle, narrative_sha256=narrative, validation_plan_sha256=plan
    )
    lifecycle.complete_cycle(cycle, assessment_sha256=digest(cycle + "-final"))


def _accept(subject):
    return {
        "format": "devbench.rna-research-admission/1",
        "subject": subject,
        "acceptance": "independently_accepted",
        "checks": {
            "Q-DESIGN": {"outcome": "pass", "evidence_sha256": digest("rank-check")}
        },
    }


def _research_fixture(
    path, *, publication=True, exposed=True, verifier=_accept, allow_outcomes=False,
):
    from devbench.analysis.episode_agent import AdaptiveController
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.platform.workflow_actions import WorkflowActions

    flow = make(path)
    sample = artifact("recorded-input", permitted_uses=(RESEARCH,))
    flow.register_artifact(sample)
    node = research_step(
        inputs=(link("expression", sample.version_id, immutable=True),)
    )
    flow.insert(node)
    broker = EvidenceBroker(flow.authority)
    policy = broker.register_policy(
        AccessPolicy("research", 1, allow_outcomes=allow_outcomes)
    )
    broker.register_context(
        "controller",
        principal_id="controller",
        lineage_id="study",
        role="controller",
        phase="analysis",
        policy_sha256=policy,
    )
    broker.register_source(
        "input",
        content_sha256=sample.sha256,
        roots=(sample.sha256,),
        classification="structural",
    )
    if exposed:
        broker.delivery("controller", "input").release({"shape": [10, 8]})
    lifecycle = ReviewHoldLifecycle(flow.authority, "study")
    lifecycle.register_entities((ReviewEntity(entity_id="fit", kind="fit"),))
    lifecycle.register_evidence(
        "checked-design", digest("rank-check"), relevant_entity_ids=("fit",)
    )
    _cycle(lifecycle, broker, "design", policy)
    if publication:
        _cycle(lifecycle, broker, "publication", policy)
    # Use the real typed coordinator, with no model invoker or provider in this
    # preflight fixture. The qualification callback represents synthetic proof.
    controller = object.__new__(AdaptiveController)
    controller.broker = broker
    controller.context_id = "controller"
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="fixture",
        selector=None,
        controller=controller,
        review_lifecycle=lifecycle,
        review_binding=lambda _: binding(),
        diagnostic_qualification=DiagnosticQualificationAuthority(verifier),
    )
    return flow, node, actions, broker


@pytest.mark.wal
def test_research_dispatch_requires_both_reviews_and_live_exact_exposure(
    workspace_tmp_path,
):
    from devbench.engine.workflow_operations import OperationBlocked

    flow, node, actions, broker = _research_fixture(workspace_tmp_path)
    actions.prepare_research_admission(flow, node.step_id)
    admitted = flow.ledger._read(lambda db: actions.adaptive_review_admission(db, node))
    assert admitted["research_admission"]["checks"] == {
        "Q-DESIGN": digest("rank-check")
    }

    broker.delivery("controller", "input").release({"another": "observed preview"})
    with pytest.raises(OperationBlocked, match="research_admission_scope_changed"):
        flow.ledger._read(lambda db: actions.adaptive_review_admission(db, node))


@pytest.mark.wal
def test_research_refresh_still_refuses_unknown_input_exposure(workspace_tmp_path):
    from devbench.engine.workflow_operations import OperationBlocked

    flow, node, actions, broker = _research_fixture(
        workspace_tmp_path, allow_outcomes=True,
    )
    actions.prepare_research_admission(flow, node.step_id)
    broker.register_source(
        "unclassified", content_sha256=digest("unclassified context"),
        roots=(artifact("recorded-input").sha256,), classification="unknown",
    )
    broker.delivery("controller", "unclassified").release({"note": "Unclassified"})
    with pytest.raises(OperationBlocked, match="current_research_exposure_required"):
        actions.prepare_research_admission(flow, node.step_id)


@pytest.mark.wal
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"publication": False}, "completed_current_publication_review_required"),
        ({"exposed": False}, "current_research_exposure_required"),
        (
            {"verifier": lambda _: None},
            "current_research_diagnostic_acceptance_required",
        ),
    ],
)
def test_missing_research_receipts_fail_closed(workspace_tmp_path, changes, reason):
    from devbench.engine.workflow_operations import OperationBlocked

    flow, node, actions, _ = _research_fixture(workspace_tmp_path, **changes)
    with pytest.raises(OperationBlocked, match=reason):
        actions.prepare_research_admission(flow, node.step_id)


@pytest.mark.wal
def test_revoked_research_acceptance_cannot_reuse_preflight(workspace_tmp_path):
    from devbench.engine.workflow_operations import OperationBlocked

    accepted = True

    def verify(subject):
        return _accept(subject) if accepted else None

    flow, node, actions, _ = _research_fixture(workspace_tmp_path, verifier=verify)
    actions.prepare_research_admission(flow, node.step_id)
    accepted = False
    with pytest.raises(
        OperationBlocked, match="current_research_diagnostic_acceptance_required"
    ):
        actions.prepare_research_admission(flow, node.step_id)
    with pytest.raises(
        OperationBlocked, match="current_research_diagnostic_acceptance_required"
    ):
        flow.ledger._read(lambda db: actions.adaptive_review_admission(db, node))


@pytest.mark.wal
def test_diagnostic_approval_must_name_current_shared_evidence(workspace_tmp_path):
    def verify(subject):
        malformed = _accept(subject)
        malformed["checks"]["Q-DESIGN"]["evidence_sha256"] = digest("unreviewed-check")
        return malformed

    flow, node, actions, _ = _research_fixture(workspace_tmp_path, verifier=verify)
    with pytest.raises(PermissionError, match="scoped passing receipt"):
        actions.prepare_research_admission(flow, node.step_id)
