"""Exercise aim approval, prospective families, and replay in the real ledger."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from devbench.core._hashing import canonical_sha256
from devbench.engine.workflow_operations import (
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)
from tests.test_investigation_invalidation import (
    Worker,
    artifact,
    digest,
    link,
    make,
    step,
)

pytestmark = pytest.mark.wal
_QUALIFIED_OPERATORS = {"researcher", "night-operator"}


def _lifecycle(root: Path, **kwargs):
    from devbench.platform.thesis_aims import ThesisAimLifecycle

    flow = make(root)
    lifecycle = ThesisAimLifecycle(
        flow.ledger,
        flow.lineage_id,
        authorize_operator=_QUALIFIED_OPERATORS.__contains__,
        **kwargs,
    )
    return flow, lifecycle


def _aim(aim_id, **kwargs):
    from devbench.platform.thesis_aims import AimDefinition

    return AimDefinition.model_validate(
        {
            "aim_id": aim_id,
            "kind": "exploratory",
            "question": "Does recovery time differ between observed conditions?",
            "estimand": "Difference in donor-level recovery between A and B",
            "input_sha256s": [digest("cohort")],
            **kwargs,
        }
    )


def _approve(lifecycle, aim_id, actor="researcher"):
    return lifecycle.approve(
        aim_id,
        actor=actor,
        rationale="Checked the stated measurement scope",
    )


def _run_evidence(flow, lifecycle, aim_id, *, step_id=None, actor="runner"):
    from tests.test_thesis_lifecycle_acceptance import receipt_bound_time_course

    binding = lifecycle.require_ready(aim_id)
    source = artifact("cohort")
    if source.version_id not in flow._read_projection(
        lambda db: flow._state(db).artifacts
    ):
        flow.register_artifact(source)
    node = step(
        step_id or "fit-" + aim_id,
        link("expression", source.version_id, immutable=True),
    )
    node, _ = receipt_bound_time_course(flow, node, binding, source.sha256)
    flow.insert(node)
    run = flow.dispatch(node.step_id, Worker(), actor=actor)
    assert run["status"] == "succeeded"
    return {
        "step_id": node.step_id,
        "receipt_sha256": run["receipt_sha256"],
        "artifact_sha256": run["outputs"]["out"]["sha256"],
    }


def _run_outcome(flow, lifecycle, aim_id, *, result="no_significant_finding"):
    binding = lifecycle.require_ready(aim_id)
    evidence = _run_evidence(flow, lifecycle, aim_id)
    lifecycle.record_outcome(
        aim_id,
        version=binding.version,
        status=result,
        rationale="Checked the retained synthetic result",
        evidence=(evidence,),
        uncertainty="Replication across donor cohorts",
    )
    return evidence


def test_attributed_approval_supports_a_negative_aim_and_reopens(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.engine import scientific_authority
    from devbench.platform.thesis_aims import ThesisAimLifecycle

    monkeypatch.setattr(scientific_authority, "_CHECKPOINT_INTERVAL", 2)
    flow, aims = _lifecycle(workspace_tmp_path)
    aims.propose(_aim("discovery"))
    aims.propose(_aim("follow-up", depends_on=("discovery",)))
    with pytest.raises(PermissionError, match="authorized"):
        _approve(aims, "discovery", actor="model")
    _approve(aims, "discovery", actor="night-operator")
    _approve(aims, "follow-up")
    with pytest.raises(OperationBlocked, match="dependency"):
        aims.require_ready("follow-up")
    evidence = _run_outcome(flow, aims, "discovery")
    with pytest.raises(OperationBlocked, match="outcome_requires_revision"):
        aims.require_ready("discovery")

    retained = WorkflowOperationLedger(flow.ledger.path, readonly=True, inert=True)
    reopened = ThesisAimLifecycle(retained, flow.lineage_id)
    assert reopened.history() == aims.history()
    status = reopened.scientific_context()
    assert status["raw_matrix_rows"] == 0
    assert status["clinical_release_allowed"] is False
    aims_by_id = {row["aim_id"]: row for row in status["research_aims"]}
    first = aims_by_id["discovery"]
    assert first["approval"]["actor"] == "night-operator"
    assert first["approval"]["rationale"] == "Checked the stated measurement scope"
    assert first["approval"]["approved_at"] > 0
    assert first["status"] == "no_significant_finding"
    assert first["outcome"]["evidence"][0] == evidence
    assert reopened.require_ready("follow-up").version == 1


def test_revision_invalidates_linked_outputs_without_erasing_prior_attempts(
    workspace_tmp_path,
):
    flow, aims = _lifecycle(workspace_tmp_path)
    aims.propose(_aim("upstream"))
    aims.propose(_aim("dependent", depends_on=("upstream",)))
    aims.propose(_aim("independent"))
    for name in ("upstream", "dependent", "independent"):
        _approve(aims, name)
        _run_outcome(flow, aims, name)
    prior = aims.scientific_context()
    with pytest.raises(ValueError, match="acyclic"):
        aims.revise(
            _aim("upstream", depends_on=("dependent",)),
            actor="researcher",
            rationale="This would create a cycle.",
        )
    new = _aim("upstream", question="Does recovery vary within paired donors?")
    aims.revise(new, actor="night-operator", rationale="Clarified the donor comparison")
    status = aims.scientific_context()
    rows = {row["aim_id"]: row for row in status["research_aims"]}
    assert rows["upstream"]["version"] == 2
    assert rows["dependent"]["status"] == "invalidated"
    assert rows["independent"]["status"] == "no_significant_finding"
    assert "fit-upstream" not in flow.status()["outcomes"]
    assert "fit-dependent" not in flow.status()["outcomes"]
    assert flow.status()["outcomes"]["fit-independent"] == "succeeded"
    assert prior["research_aims"][0]["outcome"] is not None
    assert len(flow.ledger.scientific.attempts(flow.lineage_id)) == 3
    revisions = [row for row in aims.history() if row["action"] == "revise"]
    assert len(revisions) == 1
    assert revisions[0]["actor"] == "night-operator"
    assert revisions[0]["aim_version"] == 2
    assert revisions[0]["invalidated"] == ["dependent"]
    assert sum("outcome" in row for row in aims.history()) == 3
    with pytest.raises(OperationBlocked, match="approval|invalidat"):
        aims.require_ready("dependent")


def test_shared_confirmatory_family_is_frozen_before_any_fitting(
    workspace_tmp_path,
):
    _, aims = _lifecycle(workspace_tmp_path)
    family = {
        "family_id": "frozen-family",
        "aim_ids": ("a", "b"),
        "hypotheses_sha256": digest("complete BH denominator"),
        "fdr": 0.05,
    }
    spec = {"kind": "confirmatory", "evaluation_sha256s": (digest("cohort"),)}
    aims.propose(_aim("a", multiplicity=family, **spec))
    with pytest.raises(OperationBlocked, match="family"):
        _approve(aims, "a")
    aims.propose(_aim("b", multiplicity=family, **spec))
    _approve(aims, "a")
    with pytest.raises(OperationBlocked, match="family"):
        aims.require_ready("a")
    _approve(aims, "b")
    assert aims.require_ready("a").multiplicity == aims.require_ready("b").multiplicity
    with pytest.raises(OperationConflict, match="frozen family"):
        aims.revise(
            _aim("a", multiplicity={**family, "fdr": 0.1}, **spec),
            actor="researcher",
            rationale="A post-approval change",
        )
    with aims.ledger._database() as db:
        db.execute("BEGIN")
        binding = aims.require_ready("a", connection=db)
        basis = {binding.basis_id: {"sha256": binding.basis_sha256}}
        with pytest.raises(OperationBlocked, match="family_unbound"):
            aims.require_step_basis(
                basis,
                input_sha256s={digest("cohort")},
                confirmatory_fit=True,
                hypotheses_sha256=digest("gene subset"),
                connection=db,
            )
        assert aims.require_step_basis(
            basis,
            input_sha256s={digest("cohort")},
            confirmatory_fit=True,
            hypotheses_sha256=family["hypotheses_sha256"],
            connection=db,
        ) == (binding,)
    for name in ("a", "b"):
        aims.revise(
            _aim(name, multiplicity=family, **spec),
            actor="researcher",
            rationale="Pre-exposure scope clarification",
        )
    _approve(aims, "a")
    _approve(aims, "b")
    assert aims.require_ready("a").version == aims.require_ready("b").version == 2


def test_discriminating_check_requires_receipt_bound_to_exact_aim(workspace_tmp_path):
    from devbench.platform.thesis_aims import ExplanationAssessment

    flow, aims = _lifecycle(workspace_tmp_path)
    aims.propose(
        _aim(
            "evidence-owner",
            competing_explanations=(
                {
                    "explanation_id": "compositional",
                    "explanation": "Differences reflect cell-type composition",
                    "discriminating_check": "Compare within-type donor expression",
                },
            ),
        )
    )
    aims.propose(_aim("other-branch"))
    _approve(aims, "evidence-owner")
    _approve(aims, "other-branch")
    other = _run_evidence(flow, aims, "other-branch")
    assessment = {
        "explanation_id": "compositional",
        "conclusion": "weakened",
        "rationale": "The measured within-type effect was retained",
    }
    with pytest.raises(OperationBlocked, match="wrong_approval_basis"):
        aims.assess_explanation(
            "evidence-owner",
            ExplanationAssessment.model_validate(
                {
                    **assessment,
                    "evidence": other,
                }
            ),
            version=1,
        )
    evidence = _run_evidence(flow, aims, "evidence-owner")
    aims.assess_explanation(
        "evidence-owner",
        ExplanationAssessment.model_validate(
            {
                **assessment,
                "evidence": evidence,
            }
        ),
        version=1,
    )
    assert aims.scientific_context()["research_aims"][0]["assessed_explanations"] == [
        {**assessment, "evidence": evidence}
    ]


@pytest.mark.parametrize("finding", ("supported", "no_significant_finding"))
def test_positive_aim_rechecks_its_discriminating_evidence(
    workspace_tmp_path, finding
):
    from devbench.platform.thesis_aims import ExplanationAssessment

    flow, aims = _lifecycle(workspace_tmp_path)
    aim_id = "condition-effect"
    aims.propose(
        _aim(
            aim_id,
            competing_explanations=(
                {
                    "explanation_id": "composition",
                    "explanation": "Composition may explain the aggregate change",
                    "discriminating_check": "Measure within-type donor expression",
                },
            ),
        )
    )
    _approve(aims, aim_id)
    primary = _run_evidence(flow, aims, aim_id)
    outcome = {
        "version": 1,
        "status": finding,
        "rationale": "Considered the synthetic result and the alternative.",
        "uncertainty": "An independent cohort could narrow the remaining uncertainty.",
        "evidence": (primary,),
    }
    with pytest.raises(OperationBlocked, match="competing_explanations_unassessed"):
        aims.record_outcome(aim_id, **outcome)
    alternative = _run_evidence(flow, aims, aim_id, step_id="comparison")
    aims.assess_explanation(
        aim_id,
        ExplanationAssessment(
            explanation_id="composition",
            conclusion="inconclusive",
            rationale="The comparison could not distinguish the two explanations.",
            evidence=alternative,
        ),
        version=1,
    )
    aims.record_outcome(aim_id, **outcome)
    assert aims.scientific_context()["research_aims"][0]["status"] == finding

    aims.propose(_aim("dependent", depends_on=(aim_id,)))
    _approve(aims, "dependent")
    assert aims.require_ready("dependent").version == 1
    flow.retire_branch(
        alternative["step_id"],
        reason="superseded",
        evidence_sha256=alternative["receipt_sha256"],
    )
    assert flow.current_use(primary["step_id"], "out", purpose="cache")
    view = aims.scientific_context()["research_aims"][0]
    assert view["status"] == "invalidated"
    assert view["stale_explanation_evidence"] == {
        "composition": "thesis_aim_evidence_not_current"
    }
    assert view["assessed_explanations"][0]["evidence"] == alternative
    with pytest.raises(OperationBlocked, match="dependency_not_current"):
        aims.require_ready("dependent")
    _assert_partial_discloses_stale_comparison(workspace_tmp_path, flow)


def _assert_partial_discloses_stale_comparison(root, flow):
    from hashlib import sha256

    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )

    schema = root / "comparison-answer-schema.json"
    wire = b'{"type":"object"}'
    schema.write_bytes(wire)
    spec = ThesisSynthesisSpec(
        question="Does the comparison distinguish the explanations?",
        outcome="partial",
        expected_revision=flow.status()["revision"],
        answer_schema_sha256=sha256(wire).hexdigest(),
    )
    package = root / "partial-comparison"
    receipt = write_thesis_package(
        package, flow, spec, answer_schema=schema,
        open_artifact=lambda _: pytest.fail("Unexpected answer artifact"),
    )
    assert verify_thesis_package(package, receipt["root_sha256"])["valid"]
    assert "is stale with reason `thesis_aim_evidence_not_current`" in (
        package / "summary.md"
    ).read_text()


def test_confirmatory_approval_refuses_previously_seen_outcome_inputs(
    workspace_tmp_path,
):
    flow, aims = _lifecycle(workspace_tmp_path)
    aims.propose(_aim("pilot"))
    _approve(aims, "pilot")
    _run_outcome(flow, aims, "pilot")
    aims.propose(
        _aim(
            "confirm",
            kind="confirmatory",
            evaluation_sha256s=(digest("cohort"),),
            multiplicity={
                "family_id": "prospective",
                "aim_ids": ("confirm",),
                "hypotheses_sha256": digest("test universe"),
                "fdr": 0.05,
            },
        )
    )
    with pytest.raises(OperationBlocked, match="previously_exposed"):
        _approve(aims, "confirm")


def test_requires_exact_user_requirement_source(workspace_tmp_path):
    source = {"name": "target", "value": "paired", "evidence": "Compare paired donors"}
    _, aims = _lifecycle(workspace_tmp_path, user_requirements=[source])
    requirement = {
        "name": "target",
        "origin": "user_prespecified",
        "value_sha256": canonical_sha256("different"),
        "source_sha256": canonical_sha256(source),
    }
    with pytest.raises(ValueError, match="retained user requirement"):
        aims.propose(_aim("paired", requirement_sources=(requirement,)))
    requirement["value_sha256"] = canonical_sha256("paired")
    aims.propose(_aim("paired", requirement_sources=(requirement,)))
    with pytest.raises(ValidationError, match="modality|multiplicity"):
        _aim("confirm", kind="confirmatory")


def test_confirmatory_rejects_outcome_disclosure_through_an_aliased_source(
    workspace_tmp_path,
):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker

    _, aims = _lifecycle(workspace_tmp_path)
    broker = EvidenceBroker(aims.authority)
    policy = AccessPolicy("pilot", 1, allow_outcomes=True, allow_statistics=True)
    policy_sha = broker.register_policy(policy)
    broker.register_context(
        "pilot",
        principal_id="pilot-actor",
        lineage_id=aims.lineage_id,
        role="controller",
        phase="analysis",
        policy_sha256=policy_sha,
    )
    broker.register_source(
        "derived",
        content_sha256=digest("derived"),
        roots=(digest("cohort"),),
        classification="target_outcome",
    )
    broker.delivery("pilot", "derived").release({"finding": "synthetic effect"})
    aims.propose(
        _aim(
            "confirm",
            kind="confirmatory",
            evaluation_sha256s=(digest("cohort"),),
            multiplicity={
                "family_id": "prospective",
                "aim_ids": ("confirm",),
                "hypotheses_sha256": digest("test universe"),
                "fdr": 0.05,
            },
        )
    )
    with pytest.raises(OperationBlocked, match="previously_exposed"):
        _approve(aims, "confirm")


def test_approval_and_adaptive_basis_are_atomic_in_the_callers_transaction(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.platform import thesis_aims

    flow, aims = _lifecycle(workspace_tmp_path)
    aims.propose(_aim("prospective"))
    before = flow.status()["revision"]

    def fail(*args, **kwargs):
        raise OperationConflict("Simulated append failure")

    monkeypatch.setattr(thesis_aims, "append_scientific_projection_event", fail)
    with aims.authority._execution_tx(flow.lineage_id) as db:
        with pytest.raises(OperationConflict, match="Simulated append failure"):
            aims.approve(
                "prospective",
                actor="researcher",
                rationale="Attempted approval",
                connection=db,
            )
        assert flow._state(db).basis == {}
        assert aims.scientific_context(connection=db)["research_aims"][0]["status"] == (
            "operator_review"
        )
    assert flow.status()["revision"] == before


def _blinded_subject(root, *, assess=True):
    from devbench.platform.thesis_aims import AimOutcome
    from devbench.platform.thesis_workflow import ThesisWorkflow

    verified = set()

    def verify(_subject, review):
        return review.receipt_sha256 in verified

    flow = make(root)
    thesis = ThesisWorkflow(
        flow.ledger, flow.lineage_id,
        authorize_operator=_QUALIFIED_OPERATORS.__contains__,
        verify_blinded_review=verify,
    )
    aims = thesis.aims
    aims.propose(
        _aim(
            "paired-study",
            blinded_review_required=True,
            competing_explanations=(
                {
                    "explanation_id": "measurement",
                    "explanation": "Measurement differences explain the result",
                    "discriminating_check": "Compare donor-level controls",
                },
            ),
        )
    )
    _approve(aims, "paired-study")
    evidence = _run_evidence(flow, aims, "paired-study")
    outcome = AimOutcome.model_validate({
        "status": "supported",
        "rationale": "The exact comparison supports the scoped observation.",
        "uncertainty": "Agreement has only been measured on this cohort.",
        "evidence": [evidence],
    })
    if assess:
        _assess_measurement(aims, evidence)
    return flow, aims, outcome, verified


def _assess_measurement(aims, evidence):
    from devbench.platform.thesis_aims import ExplanationAssessment

    aims.assess_explanation(
        "paired-study",
        ExplanationAssessment(
            explanation_id="measurement",
            conclusion="weakened",
            rationale="The donor-level control retained the observed difference.",
            evidence=evidence,
        ),
        version=1,
    )


def _open_blinded(aims, outcome, reviewers=("reviewer-a", "reviewer-b")):
    return aims.open_blinded_review(
        "paired-study", version=1, outcome=outcome,
        masked_packet_sha256=digest("masked packet"), reviewers=reviewers,
        actor="night-operator", rationale="Bound the masked diagnostic output.",
    )


def _record_blinded(aims, outcome):
    return aims.record_outcome(
        "paired-study", version=1, **outcome.model_dump(mode="python")
    )


def _submit_blinded(
    aims, verified, reviewer, subject, *, decision="supports", sign=True,
):
    from devbench.core.thesis_blinded_review import BlindedAimAssessment

    receipt = digest(f"assessment::{reviewer}::{decision}")
    if sign:
        verified.add(receipt)
    ratings = dict.fromkeys(
        ("units", "design", "provenance", "claim_scope", "alternatives", "follow_up"),
        "supports",
    )
    ratings["claim_scope"] = decision
    record = BlindedAimAssessment(
        reviewer=reviewer, subject_sha256=subject, receipt_sha256=receipt,
        ratings=ratings, rationale="Assessed the masked subject independently.",
        veto_codes=("unsupported_causal_claim",) if decision == "veto" else (),
    )
    return aims.submit_blinded_review("paired-study", version=1, assessment=record)


def test_blinded_panel_requires_two_attributed_exact_subject_reviews(
    workspace_tmp_path, monkeypatch,
):
    from devbench.engine import scientific_authority
    from devbench.platform.thesis_aims import ExplanationAssessment

    monkeypatch.setattr(scientific_authority, "_CHECKPOINT_INTERVAL", 2)
    flow, aims, outcome, verified = _blinded_subject(workspace_tmp_path, assess=False)
    with pytest.raises(OperationBlocked, match="thesis_blinded_review_required"):
        _record_blinded(aims, outcome)
    with pytest.raises(OperationBlocked, match="competing_explanations_unassessed"):
        _open_blinded(aims, outcome)
    measurement = _run_evidence(
        flow, aims, "paired-study", step_id="measurement-check", actor="control-runner",
    )
    _assess_measurement(aims, measurement)
    with pytest.raises(PermissionError, match="independent"):
        _open_blinded(aims, outcome, reviewers=("runner", "reviewer-b"))
    with pytest.raises(PermissionError, match="independent"):
        _open_blinded(aims, outcome, reviewers=("control-runner", "reviewer-b"))

    _open_blinded(aims, outcome)
    view = aims.scientific_context()
    panel = view["research_aims"][0]["blinded_review"]
    subject = panel["subject_sha256"]
    assert panel["submitted_reviews"] == 0
    assert "reviewer-a" not in str(view)
    with pytest.raises(OperationBlocked, match="blinded_review_explanations_locked"):
        aims.assess_explanation(
            "paired-study",
            ExplanationAssessment(
                explanation_id="measurement",
                conclusion="weakened",
                rationale="This interpretation was added after masking.",
                evidence=outcome.evidence[0],
            ),
            version=1,
        )
    with pytest.raises(OperationBlocked, match="required_review_aim_not_closed"):
        _verify_blinded_package(workspace_tmp_path, flow, outcome="completed")
    with pytest.raises(PermissionError, match="authorized.*review"):
        _submit_blinded(aims, verified, "reviewer-a", subject, sign=False)
    with pytest.raises(OperationBlocked, match="subject_changed"):
        _submit_blinded(aims, verified, "reviewer-a", digest("different subject"))
    _submit_blinded(aims, verified, "reviewer-a", subject)
    with pytest.raises(OperationConflict, match="already recorded"):
        _submit_blinded(aims, verified, "reviewer-a", subject)
    with pytest.raises(OperationBlocked, match="awaiting_independent_reviews"):
        _record_blinded(aims, outcome)
    _submit_blinded(aims, verified, "reviewer-b", subject)
    with pytest.raises(OperationBlocked, match="reviewed_outcome_changed"):
        _record_blinded(aims, outcome.model_copy(update={"uncertainty": "Different."}))
    _record_blinded(aims, outcome)

    from devbench.platform.thesis_aims import ThesisAimLifecycle

    reader = WorkflowOperationLedger(flow.ledger.path, readonly=True, inert=True)
    reopened = ThesisAimLifecycle(reader, flow.lineage_id)
    summary = reopened.scientific_context()
    assert summary["research_aims"][0]["status"] == "supported"
    assert summary["research_aims"][0]["blinded_review"]["status"] == "passed"
    audit = reopened.blinded_review_history("paired-study", version=1)
    assert {row["reviewer"] for row in audit["assessments"]} == {
        "reviewer-a", "reviewer-b",
    }
    assert summary["blinded_review_metrics"]["adjudicated_pass_rate"] == 1
    assert summary["raw_matrix_rows"] == 0
    assert summary["clinical_release_allowed"] is False
    package_summary = _verify_blinded_package(workspace_tmp_path, flow)
    assert "1 of 1 adjudicated blinded panels passed" in package_summary

    flow.retire_branch(
        measurement["step_id"], reason="superseded", evidence_sha256=digest("refit"),
    )
    assert flow.current_use("fit-paired-study", "out", purpose="cache")
    stale = aims.scientific_context()
    assert stale["research_aims"][0]["status"] == "invalidated"
    assert stale["research_aims"][0]["blinded_review"]["status"] == "stale_evidence"
    assert stale["blinded_review_metrics"]["stale_panels"] == 1
    assert stale["blinded_review_metrics"]["adjudicated_pass_rate"] is None
    assert aims.blinded_review_history("paired-study", version=1) == audit


def _verify_blinded_package(root, flow, *, outcome="partial", panel_status="passed"):
    from hashlib import sha256

    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )

    schema = root / "blinded-answer-schema.json"
    wire = b'{"type":"object"}'
    schema.write_bytes(wire)
    spec = ThesisSynthesisSpec(
        question="Does recovery time differ between observed conditions?",
        outcome=outcome,
        expected_revision=flow.status()["revision"],
        answer_schema_sha256=sha256(wire).hexdigest(),
    )
    package = root / "blinded-package"
    receipt = write_thesis_package(
        package, flow, spec, answer_schema=schema,
        open_artifact=lambda _: pytest.fail("Unexpected answer artifact"),
    )
    verify_thesis_package(package, receipt["root_sha256"])
    summary = (package / "summary.md").read_text()
    assert f"Blinded review recorded `{panel_status}` with 2 of 2" in summary
    assert "reviewer-a" not in summary
    assert "reviewer-a" not in (package / "provenance_manifest.json").read_text()
    return summary


@pytest.mark.parametrize(
    ("second", "blocked"),
    [("needs_evidence", "unresolved_disagreement"), ("veto", "vetoed")],
)
def test_blinded_panel_keeps_disagreements_and_vetoes(
    workspace_tmp_path, second, blocked,
):
    _, aims, outcome, verified = _blinded_subject(workspace_tmp_path)
    _open_blinded(aims, outcome)
    panel = aims.scientific_context()["research_aims"][0]["blinded_review"]
    _submit_blinded(aims, verified, "reviewer-a", panel["subject_sha256"])
    _submit_blinded(
        aims, verified, "reviewer-b", panel["subject_sha256"], decision=second
    )
    with pytest.raises(OperationBlocked, match=blocked):
        _record_blinded(aims, outcome)
    metrics = aims.scientific_context()["blinded_review_metrics"]
    assert metrics["unresolved_disagreements"] == 1
    if second == "veto":
        assert metrics["veto_codes"] == {"unsupported_causal_claim": 1}
        assert metrics["adjudicated_pass_rate"] == 0
    else:
        assert metrics["adjudicated_pass_rate"] is None
    with pytest.raises(OperationBlocked, match="blinded_review_requirement_locked"):
        aims.revise(
            _aim("paired-study"), actor="night-operator", rationale="Change scope.",
        )
    aims.revise(
        _aim("paired-study", blinded_review_required=True),
        actor="night-operator",
        rationale="Address the exact concern with new evidence.",
    )
    prior = aims.blinded_review_history("paired-study", version=1)
    assert prior["assessments"][1]["ratings"]["claim_scope"] == second


def test_unanimous_blinded_panel_surfaces_need_for_evidence(workspace_tmp_path):
    flow, aims, outcome, verified = _blinded_subject(workspace_tmp_path)
    _open_blinded(aims, outcome)
    panel = aims.scientific_context()["research_aims"][0]["blinded_review"]
    for reviewer in ("reviewer-a", "reviewer-b"):
        _submit_blinded(
            aims, verified, reviewer, panel["subject_sha256"],
            decision="needs_evidence",
        )
    with pytest.raises(OperationBlocked, match="needs_evidence"):
        _record_blinded(aims, outcome)
    metrics = aims.scientific_context()["blinded_review_metrics"]
    assert metrics["needs_evidence_panels"] == 1
    assert metrics["unresolved_disagreements"] == 0
    assert metrics["adjudicated_pass_rate"] is None
    summary = _verify_blinded_package(
        workspace_tmp_path, flow, panel_status="needs_evidence",
    )
    assert "Blinded review recorded `needs_evidence` in 1 of 1 panels." in summary
