"""Verify thesis scope through real adaptive dispatch and receipt acceptance."""

import pytest

from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    RuntimeEvidence,
)
from devbench.engine.workflow_operations import OperationBlocked
from tests.test_investigation_invalidation import Worker, artifact, digest, link, make
from tests.test_investigation_invalidation import step as make_step

pytestmark = pytest.mark.wal


def receipt_bound_time_course(flow, node, binding, input_sha256, *, extra=None):
    """Install deterministic synthetic attestations using an independent inventory."""
    from devbench.platform.thesis_workflow import ThesisWorkflow

    node_body = node.model_dump()
    node_body["operation_id"] = "fit_temporal_trajectory"
    node_body["profile"]["operations"][0]["operation_id"] = "fit_temporal_trajectory"
    node = type(node).model_validate(node_body)
    request = AnswerabilityRequest(
        family_id=7,
        question="observed_time_course",
        assay="bulk_rna_seq",
        entry_point="count_matrix",
    )
    axis = digest("synthetic matched observations")
    measurements = (
        AttestedMeasurement(
            kind="gene_counts",
            slot="counts",
            artifact_sha256=input_sha256,
            receipt_sha256=digest("counts receipt"),
            observation_axis_sha256=axis,
        ),
        AttestedMeasurement(
            kind="sampled_time",
            slot="collection_time",
            artifact_sha256=input_sha256,
            receipt_sha256=digest("time receipt"),
            observation_axis_sha256=axis,
        ),
        *((extra,) if extra is not None else ()),
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=digest("verified preflight"),
        observation_axis_sha256=axis,
        estimability="estimable",
        independent_units_per_group=(4, 4),
    )
    runtime = RuntimeEvidence(
        question=request.question,
        tier="tier_a",
        operation_id=node.operation_id,
        runtime_sha256=node.environment_sha256,
    )
    inventory = (*measurements, design, runtime)
    lifecycle = ThesisWorkflow(
        flow.ledger,
        flow.lineage_id,
        verify_attestation=lambda _db, record: record in inventory,
    )
    return lifecycle.bind_step(
        node,
        aim_id=binding.aim_id,
        request=request,
        measurements=measurements,
        design=design,
        runtime=runtime,
        backend_id=Worker.backend_id,
    )


def _aims(flow):
    from devbench.platform.thesis_aims import ThesisAimLifecycle

    return ThesisAimLifecycle(
        flow.ledger,
        flow.lineage_id,
        authorize_operator=lambda name: name == "operator",
    )


def _approve(
    aims, identifier, *, upstream=(), question="What changes with elapsed time?"
):
    from devbench.platform.thesis_aims import AimDefinition

    definition = AimDefinition(
        aim_id=identifier,
        kind="exploratory",
        question=question,
        estimand="Difference in donor-level expression trajectories",
        depends_on=upstream,
        input_sha256s=(digest("cohort"),),
    )
    aims.propose(definition)
    aims.approve(identifier, actor="operator", rationale="Verified source and estimand")
    return definition, aims.require_ready(identifier)


def _outcome(aims, binding, node, result):
    aims.record_outcome(
        binding.aim_id,
        version=binding.version,
        status="no_significant_finding",
        rationale="The locked synthetic check found insufficient support.",
        uncertainty="Whether the trajectory generalizes to independent observations.",
        evidence=(
            {
                "step_id": node.step_id,
                "artifact_sha256": result["outputs"]["out"]["sha256"],
                "receipt_sha256": result["receipt_sha256"],
            },
        ),
    )


def test_connected_aims_bind_upstream_receipts_and_selectively_invalidate(
    workspace_tmp_path,
):
    flow = make(workspace_tmp_path)
    cohort = artifact("cohort")
    flow.register_artifact(cohort)
    aims = _aims(flow)
    first, first_binding = _approve(aims, "primary")
    candidate = make_step(
        "primary-fit", link("data", cohort.version_id, immutable=True)
    )
    primary, decision = receipt_bound_time_course(
        flow, candidate, first_binding, cohort.sha256
    )
    assert decision.status == "ready"
    flow.insert(primary)
    worker = Worker()
    first_run = flow.dispatch(primary.step_id, worker, actor="operator")
    _outcome(aims, first_binding, primary, first_run)

    _, second = _approve(
        aims,
        "conditional",
        upstream=("primary",),
        question="Since the first aim found no support, does recovery vary by donor?",
    )
    candidate = make_step(
        "conditional-fit",
        link("data", cohort.version_id, immutable=True),
        link("prior", primary.step_id),
    )
    followup, decision = receipt_bound_time_course(
        flow, candidate, second, cohort.sha256
    )
    assert decision.status == "ready"
    flow.insert(followup)
    assert (
        flow.dispatch(followup.step_id, worker, actor="operator")["status"]
        == "succeeded"
    )
    flow.insert(make_step("independent"))
    assert (
        flow.dispatch("independent", worker, actor="operator")["status"] == "succeeded"
    )

    aims.revise(
        first.model_copy(update={"question": "Estimate the within-donor trajectory"}),
        actor="operator",
        rationale="The next experiment changes the target comparison",
    )
    snapshot = flow.status()
    assert snapshot["outcomes"] == {"independent": "succeeded"}
    assert snapshot["blocked"]["conditional-fit"] == "adaptive_dependency_pending"
    assert (
        flow.history(primary.step_id)[0]["receipt_sha256"]
        == first_run["receipt_sha256"]
    )
    assert worker.calls == ["primary-fit", "conditional-fit", "independent"]


def test_counts_only_velocity_is_retained_as_refusal_before_any_attempt(
    workspace_tmp_path,
):
    from devbench.platform.thesis_workflow import ThesisWorkflow

    flow = make(workspace_tmp_path)
    co = artifact("cohort")
    flow.register_artifact(co)
    _, binding = _approve(_aims(flow), "velocity")
    counts = AttestedMeasurement(
        kind="gene_counts",
        artifact_sha256=co.sha256,
        receipt_sha256=digest("counts"),
    )
    workflow = ThesisWorkflow(
        flow.ledger,
        flow.lineage_id,
        verify_attestation=lambda _db, item: item == counts,
    )
    node, result = workflow.bind_step(
        make_step("velocity-fit", link("expression", co.version_id, immutable=True)),
        aim_id=binding.aim_id,
        request=AnswerabilityRequest(
            family_id=15,
            question="rna_velocity",
            assay="single_cell_rna_seq",
            entry_point="annotated_object",
        ),
        measurements=(counts,),
        backend_id=Worker.backend_id,
    )
    assert result.status == "awaiting_information"
    assert result.missing_measurements == (("spliced_counts",), ("unspliced_counts",))
    flow.insert(node)
    worker = Worker()
    with pytest.raises(
        OperationBlocked, match="thesis_answerability_missing_measurements"
    ):
        flow.dispatch(node.step_id, worker, actor="operator")
    assert not worker.calls and not flow.authority.attempts(flow.lineage_id)
    state = workflow.scientific_context()
    assert state["raw_matrix_rows"] == 0
    assert state["answerability"][0]["missing_measurements"] == [
        ["spliced_counts"],
        ["unspliced_counts"],
    ]


def test_unscoped_or_changed_proof_cannot_run_an_approved_aim(workspace_tmp_path):
    flow = make(workspace_tmp_path)
    co = artifact("cohort")
    flow.register_artifact(co)
    _, binding = _approve(_aims(flow), "test")
    candidate = make_step("fit", link("data", co.version_id, immutable=True))
    naked = candidate.model_dump()
    naked["basis"] = [{"basis_id": binding.basis_id}]
    flow.insert(naked)
    assert flow.status()["blocked"]["fit"] == "thesis_answerability_required"
    bound, decision = receipt_bound_time_course(flow, candidate, binding, co.sha256)
    assert decision.status == "ready"
    flow.revise(bound, reason="design_change", evidence_sha256=digest("bound protocol"))
    wrong = Worker()
    wrong.backend_id = "different-runtime"
    with pytest.raises(OperationBlocked, match="thesis_answerability_backend_changed"):
        flow.dispatch("fit", wrong, actor="operator")
    with pytest.raises(OperationBlocked, match="thesis_scope_revision_cannot_unbind"):
        flow.revise(
            candidate,
            reason="design_change",
            evidence_sha256=digest("omitted authority"),
        )
    assert not flow.authority.attempts(flow.lineage_id)


def test_aim_revision_during_execution_keeps_result_audit_only(workspace_tmp_path):
    flow = make(workspace_tmp_path)
    co = artifact("cohort")
    flow.register_artifact(co)
    aims = _aims(flow)
    definition, binding = _approve(aims, "changes")
    step = make_step("scope-fit", link("data", co.version_id, immutable=True))
    step, _ = receipt_bound_time_course(flow, step, binding, co.sha256)
    flow.insert(step)

    class RevisingWorker(Worker):
        def run(self, envelope):
            receipt = super().run(envelope)
            aims.revise(
                definition.model_copy(update={"question": "Evaluate paired donors"}),
                actor="operator",
                rationale="The assumed comparison changed",
            )
            return receipt

    worker = RevisingWorker()
    result = flow.dispatch(step.step_id, worker, actor="operator")
    assert result["status"] == "audit_only"
    assert result["reason"].endswith("adaptive_dispatch_basis_invalidated")
    assert result["outputs"] == {}
    assert result["staged_outputs"]["out"]["sha256"] == digest("scope-fit-v1")
    assert worker.calls == ["scope-fit"]
    assert len(flow.authority.attempts(flow.lineage_id)) == 1


@pytest.mark.parametrize("fits_complete_family", [True, False])
def test_confirmatory_execution_checks_the_actual_tier_a_family(
    workspace_tmp_path,
    fits_complete_family,
):
    from devbench.platform.adaptive_workflow import AdaptiveStep
    from devbench.platform.thesis_aims import AimDefinition, AimMultiplicity
    from devbench.platform.thesis_workflow import (
        ThesisWorkflow,
        compiled_hypotheses_sha256,
    )
    from tests.test_tier_a_executor import _fixture

    flow, executor, envelope = _fixture(workspace_tmp_path)
    step, compiled, inputs = executor.validate(envelope)
    planned_family = (
        compiled.family
        if fits_complete_family
        else compiled.family.model_copy(
            update={"feature_ids": compiled.family.feature_ids[:1]},
        )
    )
    family_hash = compiled_hypotheses_sha256(
        compiled.model_copy(update={"family": planned_family}),
    )
    aims = _aims(flow)
    aims.propose(
        AimDefinition(
            aim_id="confirmatory",
            kind="confirmatory",
            question="Compare conditions in the declared cohort.",
            estimand="Donor-level change",
            input_sha256s=(compiled.expression_sha256,),
            evaluation_sha256s=(compiled.expression_sha256,),
            multiplicity=AimMultiplicity(
                family_id="donors",
                aim_ids=("confirmatory",),
                hypotheses_sha256=family_hash,
                fdr=0.05,
            ),
        )
    )
    aims.approve("confirmatory", actor="operator", rationale="Freeze before fitting")
    observations = digest("original observations")
    expression = AttestedMeasurement(
        kind="normalized_expression",
        artifact_sha256=compiled.expression_sha256,
        receipt_sha256=digest("expression provenance"),
        observation_axis_sha256=observations,
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=digest("preflight"),
        observation_axis_sha256=observations,
        estimability="estimable",
        independent_units_per_group=(4, 4),
    )
    runtime = RuntimeEvidence(
        question="differential_expression",
        tier="tier_a",
        operation_id="bulk_condition_contrast",
        runtime_sha256=executor.environment_sha256,
    )
    accepted = {expression, design, runtime}
    thesis = ThesisWorkflow(
        flow.ledger,
        flow.lineage_id,
        verify_attestation=lambda _db, item: item in accepted,
    )
    step, decision = thesis.bind_step(
        AdaptiveStep.model_validate(step),
        aim_id="confirmatory",
        request=AnswerabilityRequest(
            family_id=1,
            question="differential_expression",
            assay="bulk_rna_seq",
            entry_point="normalized_expression",
        ),
        measurements=(expression,),
        design=design,
        runtime=runtime,
        backend_id=executor.backend_id,
    )
    assert decision.status == "ready"
    with flow.ledger._database() as db:
        db.execute("BEGIN")
        state = flow._state(db)
        basis = flow._computation_basis(db, state, step, inputs)
        envelope["thesis_scope"] = thesis.require_step(
            db,
            step,
            inputs=inputs,
            basis=basis,
            environment=executor.environment_sha256,
        )
        envelope["basis"] = basis
        envelope["step"] = step.model_dump(
            mode="json", exclude={"oci_method_version_id"}
        )
    if fits_complete_family:
        assert flow._dispatch_code(executor, envelope) == executor.code_sha256
    else:
        with pytest.raises(OperationBlocked, match="confirmatory_family_unbound"):
            flow._dispatch_code(executor, envelope)
    assert not flow.authority.attempts(flow.lineage_id)


def test_compact_adaptive_context_reopens_without_exposing_narrative(
    workspace_tmp_path,
):
    from devbench.analysis.episode_agent import (
        adaptive_authoritative_scientific_context,
    )
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.thesis_workflow import ThesisWorkflow

    flow = make(workspace_tmp_path)
    co = artifact("cohort")
    flow.register_artifact(co)
    confidential_question = "Synthetic confidential narrative"
    _, binding = _approve(_aims(flow), "local", question=confidential_question)
    step = make_step("fit", link("data", co.version_id, immutable=True))
    step, _ = receipt_bound_time_course(flow, step, binding, co.sha256)

    broker = EvidenceBroker(flow.authority)
    policy = broker.register_policy(AccessPolicy("structural", 1, allow_outcomes=False))
    broker.register_context(
        "context",
        principal_id="analyst",
        lineage_id=flow.lineage_id,
        role="controller",
        phase="analysis",
        policy_sha256=policy,
    )
    lifecycle = ThesisWorkflow(flow.ledger, flow.lineage_id)
    with flow.ledger._database() as db:
        db.execute("BEGIN")
        projected = adaptive_authoritative_scientific_context(
            flow,
            connection=db,
            state=flow._state(db),
            revision=flow.authority._revision(db, flow.lineage_id),
            review_state={"holds": {}},
            evidence={},
            broker=broker,
            context_id="context",
            byte_budget=32_768,
            thesis_lifecycle=lifecycle,
        )
    assert projected["thesis_state"]["raw_matrix_rows"] == 0
    assert projected["thesis_state"]["clinical_release_allowed"] is False
    assert projected["thesis_state"]["research_aims"]["records"][0]["aim_id"] == "local"
    assert projected["thesis_state"]["answerability"]["records"][0]["status"] == "ready"
    assert confidential_question not in str(projected)

    readonly = WorkflowOperationLedger(flow.ledger.path, readonly=True, inert=True)
    reopened = ThesisWorkflow(readonly, flow.lineage_id)
    assert reopened.scientific_context() == lifecycle.scientific_context()
