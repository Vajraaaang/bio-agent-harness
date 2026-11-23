"""Exercise donor-pseudobulk admission through the Workbench owner."""

import json
import os

import pytest

from devbench.analysis.checkpoints import digest as document_digest
from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority
from devbench.analysis.scientific_review_holds import ReviewEntity, ReviewHoldLifecycle
from devbench.analysis.workbench_ui import WorkbenchService
from devbench.core.joint_bh import JointBHMember, JointBHPlan
from devbench.core.procedure_profile import ProcedureProfile
from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    RuntimeEvidence,
)
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)
from devbench.platform.adaptive_workflow import ArtifactVersion
from devbench.platform.scrna_general_pseudobulk import (
    GeneralPseudobulkSpec,
    stage_general_pseudobulk,
)
from devbench.platform.thesis_joint_bh import ThesisJointBHRegistry
from devbench.platform.thesis_workflow import (
    ThesisWorkflow,
    check_thesis_backend,
    compiled_hypotheses_sha256,
)
from devbench.platform.workbench_adaptive import (
    WorkbenchAdaptiveBinding,
    _graph_revision,
)
from devbench.platform.workflow_actions import WorkflowActions
from devharness_controlled.native_contracts import (
    LIMMA_VOOM_KERNEL_IMAGES,
    limma_voom_kernel_images,
)
from tests.test_admitted_research_inference import _cycle
from tests.test_investigation_invalidation import digest
from tests.test_investigation_publication_epochs import authorize
from tests.test_p3_general_pseudobulk import _source, _voom_spec
from tests.test_review_hold_lifecycle import binding as review_binding
from tests.test_scientific_authority_migration import _binding
from tests.test_scientific_workspace import action, scientific_context, setup_workspace
from tests.test_scientific_workspace_http import (
    _fixture_adaptive_controller,
    call,
    running,
)
from tests.test_thesis_aims import _aim, _approve
from tests.test_tier_a_scrna import _qc_step
from tests.test_tier_a_scrna import _source as qc_source

pytestmark = pytest.mark.wal


def _open_workbench(root):
    app, methods, _, method_step, inputs = setup_workspace(root)
    selector = app.scientific.selector
    app.close()
    actors = root / "actors"
    actors.mkdir(mode=0o700)
    app = WorkbenchService(
        root / "pseudobulk-browser",
        [inputs],
        enable_scientific=True,
        scientific_workspace=methods.root,
        allow_data_execution=True,
        codex_model="fixture-only-model",
        method_selector=selector,
        agent_workspaces=actors,
        allow_model_execution=True,
        model_data_scope="synthetic",
    )
    _, dataset, analysis = scientific_context(app, method_step)
    plan = action(
        app,
        "create_plan",
        dataset_ref=dataset["ref"],
        analysis_ref=analysis["ref"],
    )
    lineage = plan["session_id"]
    ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME, scientific=True)
    authority = ledger.scientific
    authority.commit(lineage, expected_revision=0, record=_binding())
    authority.commit(
        lineage,
        expected_revision=1,
        record={
            "kind": "grant",
            "record_id": "grant-a",
            "binding_id": "binding-a",
            "grant_sha256": digest("pseudobulk engineering grant"),
        },
    )
    authority.activate_execution(
        lineage,
        expected_revision=2,
        binding_id="binding-a",
        grant_id="grant-a",
        max_jobs=5,
        authorize=authorize,
    )
    return app, ledger, lineage


def _install_controller(app, ledger, lineage, thesis, executor):
    broker = EvidenceBroker(ledger.scientific)
    policy = AccessPolicy("pseudobulk-readback", 1, allow_outcomes=True)
    broker.register_policy(policy)
    broker.register_context(
        "viewer",
        principal_id="scientist",
        lineage_id=lineage,
        role="scientist",
        phase="readback",
        policy_sha256=policy.sha256(),
    )
    broker.register_source(
        "graph",
        content_sha256=digest("pseudobulk graph"),
        roots=(digest("root"),),
        classification="structural",
    )
    controller, selector = _fixture_adaptive_controller(
        app.scientific.planner(),
        ledger,
        lineage,
        broker,
        step_id="voom-sensitivity",
        thesis_lifecycle=thesis,
    )
    review = ReviewHoldLifecycle(ledger.scientific, lineage)
    certified = {}

    def verify(subject):
        if "design" not in certified:
            return None
        return {
            "format": "devbench.rna-research-admission/1",
            "subject": subject,
            "acceptance": "independently_accepted",
            "checks": {
                "Q-DESIGN": {
                    "outcome": "pass",
                    "evidence_sha256": certified["design"],
                }
            },
        }

    app.bind_adaptive_workflow(
        WorkbenchAdaptiveBinding(
            lineage,
            broker.delivery("viewer", "graph"),
            controller=controller,
            tier_a_executor=executor,
            review_lifecycle=review,
            review_binding=lambda _: review_binding(),
            diagnostic_qualification=DiagnosticQualificationAuthority(verify),
        )
    )
    return selector, certified


def _profile():
    return ProcedureProfile.model_validate(
        {
            "profile_id": "pseudobulk",
            "version": 1,
            "biological_units": ["donor"],
            "design_scope": ["B-I"],
            "permitted_claims": ["admitted_research_inference"],
            "input_representations": ["raw_counts_h5", "metadata_csv"],
            "operations": [
                {
                    "operation_id": "bulk_condition_contrast",
                    "output_kinds": ["table"],
                    "max_memory_mb": 2048,
                    "timeout_seconds": 60,
                }
            ],
        }
    )


def _prepared_comparison(
    app,
    ledger,
    lineage,
    *,
    image=None,
    primary_anchor=False,
    blinded=False,
    joint=False,
):
    root = app.workspace
    source, _, _, _, groups = _source(root)
    capsule = stage_general_pseudobulk(
        source,
        _voom_spec(source, groups, False),
        root / "capsule",
    )
    plan = capsule.pipeline
    assert plan is not None
    executor = TierAExecutor(
        ledger,
        allow_data_execution=True,
        mounted_image_id=image or LIMMA_VOOM_KERNEL_IMAGES["linux/arm64"],
    )
    observations = digest("verified ordered donor profiles")
    measurement = AttestedMeasurement(
        kind="donor_pseudobulk_counts",
        artifact_sha256=plan.expression_sha256,
        receipt_sha256=capsule.provenance_sha256,
        observation_axis_sha256=observations,
        feature_axis_sha256=capsule.summary["feature_ids_sha256"],
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=digest("independent design review"),
        observation_axis_sha256=observations,
        estimability="estimable",
        independent_units_per_group=(4, 4, 4),
    )
    runtime = RuntimeEvidence(
        question="differential_cell_state",
        tier="tier_a",
        operation_id="bulk_condition_contrast",
        runtime_sha256=executor.environment_sha256,
    )
    swapped = measurement.model_copy(update={"artifact_sha256": plan.metadata_sha256})
    thesis = ThesisWorkflow(
        ledger,
        lineage,
        authorize_operator=lambda who: who == "researcher",
        verify_attestation=lambda _db, item: item
        in (measurement, design, runtime, swapped),
    )
    _declare_pseudobulk_aims(thesis, plan, primary_anchor, blinded, joint)
    selector, certified = _install_controller(app, ledger, lineage, thesis, executor)
    args = {
        "staged": capsule,
        "step_id": "voom-sensitivity",
        "branch_id": "sensitivity",
        "profile": _profile(),
        "aim_id": "pseudobulk",
        "request": AnswerabilityRequest(
            family_id=1,
            question="differential_cell_state",
            assay="single_cell_rna_seq",
            entry_point="count_matrix",
        ),
        "measurements": (measurement,),
        "design": design,
    }
    return thesis.flow, selector, swapped, args, certified


def _declare_pseudobulk_aims(thesis, plan, primary_anchor, blinded, joint):
    hypotheses = compiled_hypotheses_sha256(plan)
    members = {"pseudobulk": plan}
    frozen = None
    if primary_anchor or joint:
        family = plan.family.model_copy(update={"primary": True})
        hypotheses = document_digest(family.model_dump(mode="json"))
    if joint:
        primary = plan.model_copy(
            update={
                "family": family,
                "family_sha256": hypotheses,
                "mounted_model": plan.mounted_model.model_copy(
                    update={"sensitivity_method": None}
                ),
            }
        )
        peer = primary.model_copy(
            update={"expression_sha256": digest("planned peer counts")}
        )
        members["peer"] = peer
        frozen = JointBHPlan(
            family_id="pseudobulk-testing",
            fdr=0.05,
            dependence="independence",
            dependence_rationale="The synthetic aims declare disjoint donor cohorts.",
            members=(
                JointBHMember.from_pipeline(
                    "pseudobulk", primary, step_id="edgeR-primary"
                ),
                JointBHMember.from_pipeline("peer", peer, step_id="peer-primary"),
            ),
        )
        hypotheses = frozen.sha256()
    for aim_id, specification in members.items():
        thesis.aims.propose(
            _aim(
                aim_id,
                kind="confirmatory",
                blinded_review_required=blinded,
                input_sha256s=(
                    specification.expression_sha256,
                    specification.metadata_sha256,
                ),
                evaluation_sha256s=[specification.expression_sha256],
                multiplicity={
                    "family_id": "pseudobulk-testing",
                    "aim_ids": sorted(members),
                    "hypotheses_sha256": hypotheses,
                    "fdr": 0.05,
                },
            )
        )
    if frozen is not None:
        ThesisJointBHRegistry(thesis.aims).freeze(
            frozen,
            actor="researcher",
            rationale="Freeze both primary analysis identities.",
        )
    for aim_id in members:
        _approve(thesis.aims, aim_id)


def test_owner_binds_exact_capsule_without_granting_research_review(workspace_tmp_path):
    from devbench.platform.workbench_thesis import bind_workbench_pseudobulk

    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        flow, selector, swapped, args, _ = _prepared_comparison(app, ledger, lineage)
        revision = _graph_revision(flow)
        with pytest.raises(OperationConflict, match="revision changed"):
            bind_workbench_pseudobulk(
                app, lineage, **args, expected_revision=revision - 1
            )
        with pytest.raises(
            OperationBlocked, match="pseudobulk_measurement_scope_changed"
        ):
            bind_workbench_pseudobulk(
                app,
                lineage,
                **{**args, "measurements": (swapped,)},
                expected_revision=revision,
            )
        unverified_design = args["design"].model_copy(
            update={"receipt_sha256": digest("untrusted design record")},
        )
        with pytest.raises(
            OperationBlocked, match="thesis_input_attestation_unverified"
        ):
            bind_workbench_pseudobulk(
                app,
                lineage,
                **{**args, "design": unverified_design},
                expected_revision=revision,
            )
        assert _graph_revision(flow) == revision
        with flow._read_connection() as db:
            assert not flow._state(db).artifacts
        held = bind_workbench_pseudobulk(
            app,
            lineage,
            **{**args, "design": None},
            expected_revision=revision,
        )
        assert held["step_id"] is None
        assert held["decision"]["reason"] == "design_evidence_required"
        accepted = bind_workbench_pseudobulk(
            app,
            lineage,
            **args,
            expected_revision=held["revision"],
        )
        assert accepted["step_id"] == "voom-sensitivity"
        assert accepted["decision"]["status"] == "ready"
        assert accepted["decision"]["claim_scope"] == "requires_scientific_review"
        assert accepted["decision"]["clinical_release_allowed"] is False
        assert accepted["capsule_sha256"] == args["staged"].provenance_sha256
        with flow._read_connection() as db:
            state = flow._state(db)
            registered = state.nodes[accepted["step_id"]]
            assert len(state.artifacts) == 2
        assert registered.comparison and not registered.primary
        assert selector.controller_turns == 0
        assert flow.history(registered.step_id) == ()
        with pytest.raises(OperationBlocked, match="completed_current_design_review"):
            flow.candidate_envelope(registered, actor="researcher")
    finally:
        app.close()


def _complete_reviews(app, lineage, flow, admission, *, suffix=""):
    owner = app._adaptive_bindings[lineage]
    step_id = admission["step_id"]
    with flow._read_connection() as db:
        state = flow._state(db)
        artifacts = flow._step_inputs(state, state.nodes[step_id])
    broker = owner.controller.broker
    for port, artifact in artifacts.items():
        source = "pseudobulk-" + port + suffix
        broker.register_source(
            source,
            content_sha256=artifact.sha256,
            roots=(artifact.sha256,),
            classification="structural",
        )
        broker.delivery(owner.controller.context_id, source).release(
            {"input_role": port}
        )
    lifecycle = owner.review_lifecycle
    lifecycle.register_entities((ReviewEntity(entity_id=step_id, kind="fit"),))
    lifecycle.register_evidence(
        "checked-design" + suffix,
        admission["structural_design_sha256"],
        relevant_entity_ids=(step_id,),
    )
    policy = broker.register_policy(AccessPolicy("pseudobulk-blinded-review", 1))
    for touchpoint in ("design", "publication"):
        _cycle(
            lifecycle, broker, touchpoint, policy, entity_ids=(step_id,), suffix=suffix
        )


def _trusted_actions(app, ledger, owner):
    return WorkflowActions(
        app.scientific.planner(),
        ledger,
        actor="workbench-operator",
        selector=None,
        executor=None,
        allow_data_execution=True,
        review_lifecycle=owner.review_lifecycle,
        review_binding=owner.review_binding,
        controller=owner.controller,
        diagnostic_qualification=owner.diagnostic_qualification,
        tier_a_executor=owner.tier_a_executor,
    )


@pytest.mark.parametrize(
    ("model_dispatch", "blinded"),
    [(False, False), (True, False), (False, True)],
)
def test_reviewed_workbench_comparison_uses_the_authoritative_scheduler(
    workspace_tmp_path,
    model_dispatch,
    blinded,
):
    from devbench.platform.workbench_thesis import bind_workbench_pseudobulk

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in limma_voom_kernel_images():
        pytest.skip("Select the locked limma-voom image for the Workbench dispatch")
    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        flow, selector, _, args, certified = _prepared_comparison(
            app,
            ledger,
            lineage,
            image=image,
            blinded=blinded,
        )
        admission = bind_workbench_pseudobulk(
            app,
            lineage,
            **args,
            expected_revision=_graph_revision(flow),
        )
        _complete_reviews(app, lineage, flow, admission)
        certified["design"] = admission["structural_design_sha256"]
        owner = app._adaptive_bindings[lineage]
        actions = _trusted_actions(app, ledger, owner)
        if model_dispatch:
            _advance_reviewed_comparison(app, flow, lineage, selector)
        else:
            outcome = actions.perform_adaptive(flow, admission["step_id"])
            assert outcome["status"] == "succeeded", outcome
        (receipt,) = flow.history(admission["step_id"])
        assert receipt["status"] == "succeeded", receipt
        if blinded:
            _assert_sensitivity_cannot_open_panel(owner, admission["step_id"], receipt)
        else:
            _assert_sensitivity_cannot_close_aim(owner, admission["step_id"], receipt)
        executor = app._adaptive_bindings[lineage].tier_a_executor
        record = executor.runs / receipt["attempt_id"] / "io.json"
        method = json.loads(record.read_bytes())["tool"]["method"]
        assert (method["solver"], method["analysis_role"]) == (
            "limma_voom",
            "nonprimary_sensitivity",
        )
    finally:
        app.close()


def _aim_evidence(step_id, receipt):
    return {
        "step_id": step_id,
        "receipt_sha256": receipt["receipt_sha256"],
        "artifact_sha256": receipt["outputs"]["result"]["sha256"],
    }


def _assert_sensitivity_cannot_close_aim(owner, step_id, receipt):
    aims = owner.controller.thesis_lifecycle.aims
    evidence = _aim_evidence(step_id, receipt)
    for status in ("supported", "no_significant_finding"):
        with pytest.raises(
            OperationBlocked, match="thesis_aim_primary_evidence_required"
        ):
            aims.record_outcome(
                "pseudobulk",
                version=1,
                status=status,
                rationale="Attempted closure on the sensitivity result.",
                uncertainty="The primary fit is outstanding.",
                evidence=(evidence,),
            )


def _assert_sensitivity_cannot_open_panel(owner, step_id, receipt):
    from devbench.platform.thesis_aims import AimOutcome

    aims = owner.controller.thesis_lifecycle.aims
    proposal = AimOutcome.model_validate(
        {
            "status": "supported",
            "rationale": "Sensitivity evidence is available.",
            "uncertainty": "The primary fit is outstanding.",
            "evidence": [_aim_evidence(step_id, receipt)],
        }
    )
    with pytest.raises(OperationBlocked, match="thesis_aim_primary_evidence_required"):
        aims.open_blinded_review(
            "pseudobulk",
            version=1,
            outcome=proposal,
            masked_packet_sha256=digest("synthetic masked evidence"),
            reviewers=("reviewer-a", "reviewer-b"),
            actor="researcher",
            rationale="Submit the proposed outcome for independent review.",
        )


@pytest.mark.parametrize("joint", [False, True])
def test_confirmatory_aim_keeps_sensitivity_with_its_exact_primary_fit(
    workspace_tmp_path,
    joint,
):
    import pyarrow.parquet as pq

    from devbench.platform.workbench_thesis import bind_workbench_pseudobulk

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in limma_voom_kernel_images():
        pytest.skip("Select the locked limma-voom image for companion-fit custody")
    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        flow, _, _, args, certified = _prepared_comparison(
            app,
            ledger,
            lineage,
            image=image,
            primary_anchor=True,
            joint=joint,
        )
        comparison = bind_workbench_pseudobulk(
            app,
            lineage,
            **args,
            expected_revision=_graph_revision(flow),
        )
        capsule = args["staged"]
        declaration = GeneralPseudobulkSpec.model_validate_json(
            json.dumps(capsule.summary["specification"]),
        )
        primary = stage_general_pseudobulk(
            app.workspace / "source.h5ad",
            declaration.model_copy(
                update={
                    "model": declaration.model.model_copy(
                        update={"sensitivity_method": None},
                    )
                }
            ),
            app.workspace / "primary-capsule",
        )
        assert primary.pipeline is not None and capsule.pipeline is not None
        assert primary.pipeline.expression_sha256 == capsule.pipeline.expression_sha256
        assert primary.pipeline.metadata_sha256 == capsule.pipeline.metadata_sha256
        _assert_companion_keeps_ordered_family(capsule.pipeline, primary.pipeline)
        primary_args = {
            **args,
            "staged": primary,
            "step_id": "edgeR-primary",
            "branch_id": "primary",
        }
        main = bind_workbench_pseudobulk(
            app,
            lineage,
            **primary_args,
            expected_revision=comparison["revision"],
        )
        owner = app._adaptive_bindings[lineage]
        actions = _trusted_actions(app, ledger, owner)
        retained = []
        for name, decision in (("comparison", comparison), ("primary", main)):
            _complete_reviews(app, lineage, flow, decision, suffix="-" + name)
            certified["design"] = decision["structural_design_sha256"]
            fit = actions.perform_adaptive(flow, decision["step_id"])
            assert fit["status"] == "succeeded", fit
            retained.append(fit)
            if name == "comparison":
                _assert_sensitivity_cannot_close_aim(owner, decision["step_id"], fit)

        if joint:
            _assert_outside_joint_pool(flow, owner, comparison, main, retained)
            return
        adopted = flow.current_use(main["step_id"], "result", purpose="query")
        with owner.tier_a_executor.open_output(adopted) as stream:
            rows = pq.read_table(stream, columns=["q_value"])["q_value"].to_pylist()
        status = (
            "supported"
            if any(q is not None and q <= 0.05 for q in rows)
            else ("no_significant_finding")
        )
        aims = owner.controller.thesis_lifecycle.aims
        aims.record_outcome(
            "pseudobulk",
            version=1,
            status=status,
            rationale="Read the prespecified fit and its sensitivity check.",
            uncertainty="Independent donor cohorts would test generalization.",
            evidence=(
                _aim_evidence(comparison["step_id"], retained[0]),
                _aim_evidence(main["step_id"], retained[1]),
            ),
        )
        assert aims.scientific_context()["research_aims"][0]["status"] == status
        flow.retire_branch(
            "primary",
            reason="scope_change",
            evidence_sha256=retained[1]["receipt_sha256"],
        )
        assert aims.scientific_context()["research_aims"][0]["status"] == "invalidated"
    finally:
        app.close()


def _assert_outside_joint_pool(flow, owner, comparison, primary, retained):
    import numpy as np
    import pyarrow.parquet as pq
    from scipy.stats import false_discovery_control

    from devbench.platform.thesis_joint_bh_release import prepare_joint_bh_release

    with flow._read_connection() as db:
        attempts = flow._state(db).dispatches
        scopes = [
            attempts[receipt["attempt_id"]]["envelope"]["thesis_scope"]
            for receipt in retained
        ]
    assert "joint_bh" not in scopes[0] and "joint_bh_reference" not in scopes[1]
    assert scopes[0]["joint_bh_reference"] == scopes[1]["joint_bh"]
    assert scopes[0]["joint_bh_reference"]["member"]["step_id"] != comparison["step_id"]
    artifact = flow.current_use(comparison["step_id"], "result", purpose="query")
    executor = owner.tier_a_executor
    with executor.open_output(artifact) as stream:
        family = pq.read_table(stream, columns=["p_value", "q_value"])
    assert family.num_rows == 48
    np.testing.assert_allclose(
        family["q_value"].to_numpy(),
        false_discovery_control(family["p_value"].to_numpy()),
    )
    with pytest.raises(OperationBlocked, match="thesis_joint_bh_member_unpublished"):
        flow.current_use(primary["step_id"], "result", purpose="query")
    with pytest.raises(OperationBlocked, match="thesis_joint_bh_member_pending"):
        prepare_joint_bh_release(flow, executor, "pseudobulk-testing")
    aims = owner.controller.thesis_lifecycle.aims
    with flow._read_connection() as db:
        state, _ = aims._projection(db)
        peer = state.aims["peer"].definition
    aims.revise(
        peer.model_copy(update={"question": "What changes in the revised peer aim?"}),
        actor="researcher",
        rationale="Revise the question for the other pooled aim.",
    )
    with pytest.raises(OperationBlocked, match="adaptive_current_use_unavailable"):
        flow.current_use(comparison["step_id"], "result", purpose="query")
    assert (
        flow.history(comparison["step_id"])[0]["receipt_sha256"]
        == (retained[0]["receipt_sha256"])
    )


def _assert_companion_keeps_ordered_family(comparison, primary):
    envelope = {
        "step": {
            "purpose": "admitted_research_inference",
            "primary": False,
            "comparison": True,
        },
        "thesis_scope": {
            "backend_id": "fixture",
            "route": "tier_a",
            "hypotheses_sha256": compiled_hypotheses_sha256(primary),
        },
    }
    reordered = comparison.family.model_copy(
        update={
            "feature_ids": tuple(reversed(comparison.family.feature_ids)),
        }
    )
    wrong = comparison.model_copy(update={"family": reordered})
    with pytest.raises(
        OperationBlocked, match="thesis_aim_confirmatory_family_unbound"
    ):
        check_thesis_backend(envelope, "fixture", validated_specification=wrong)


def _advance_reviewed_comparison(app, flow, lineage, selector):
    from tests.test_two_agent_investigation_v24 import decision

    briefings = []

    def choose(context):
        briefings.append(context)
        return decision("dispatch", step_id=context["ready"][0])

    selector.script[0] = choose
    with running(app) as port:
        status, body, _ = call(
            port,
            "/api/scientific/adaptive/" + lineage,
            origin=f"http://127.0.0.1:{port}",
            body={
                "action": "advance",
                "expected_revision": _graph_revision(flow),
                "allow_data_execution": True,
                "allow_model_execution": True,
            },
        )
    assert status == 200, body.decode()
    result = json.loads(body)
    assert result["steps_dispatched"] == 1, (
        result,
        (flow.controller_state()["outcome"] or {}).get("reason"),
    )
    (briefing,) = briefings
    assert set(briefing) == {
        "decision_kind",
        "brief",
        "brief_id",
        "ready",
        "jobs",
        "termination_required",
        "selection_rules",
    }
    assert briefing["brief"] == "reviewed_research_dispatch"
    assert briefing["jobs"] == [
        {"job_id": "research-fit-1", "fit_kind": "comparison", "regime": "B-I"},
    ]
    (decision_record,) = flow.controller_state()["decisions"]
    assert decision_record["decision"]["step_id"] == "voom-sensitivity"
    broker = app._adaptive_bindings[lineage].controller.broker
    disclosure = broker.reservation(
        decision_record["evidence"]["controller_disclosure_reservation_id"]
    )
    assert disclosure["classification"] == "structural"
    assert disclosure["content_sha256"] == document_digest(briefing)


def _add_mounted_qc(app, flow, lineage):
    executor = app._adaptive_bindings[lineage].tier_a_executor
    source = qc_source(app.workspace)
    flow.register_artifact(
        ArtifactVersion(
            version_id="count-source",
            sha256=executor.training.put_file(source),
            kind="counts",
            representation="raw_counts_h5ad",
            biological_unit="donor",
            unit_membership_sha256=digest("QC donor inventory"),
            measurement_history=("raw_counts",),
        )
    )
    flow.insert(_qc_step(executor))


@pytest.mark.parametrize("qc_first", [True, False])
def test_mixed_frontier_offers_qc_and_reviewed_fit_without_outcomes(
    workspace_tmp_path,
    qc_first,
):
    from devbench.platform.workbench_thesis import bind_workbench_pseudobulk
    from tests.test_two_agent_investigation_v24 import decision

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in limma_voom_kernel_images():
        pytest.skip("Select the locked limma-voom image for mixed Workbench dispatch")
    app, ledger, lineage = _open_workbench(workspace_tmp_path)
    try:
        flow, selector, _, args, certified = _prepared_comparison(
            app,
            ledger,
            lineage,
            image=image,
        )
        admission = bind_workbench_pseudobulk(
            app,
            lineage,
            **args,
            expected_revision=_graph_revision(flow),
        )
        _complete_reviews(app, lineage, flow, admission)
        certified["design"] = admission["structural_design_sha256"]
        _add_mounted_qc(app, flow, lineage)

        briefings = []

        def choose_qc(context):
            briefings.append(context)
            job = next(
                (
                    row["job_id"]
                    for row in context.get("jobs", [])
                    if row.get("operation") == "scrna_batch_qc"
                ),
                "qc-lanes",
            )
            return decision("dispatch", step_id=job)

        def choose_fit(context):
            briefings.append(context)
            job = next(
                (
                    row["job_id"]
                    for row in context.get("jobs", [])
                    if row["fit_kind"] == "comparison"
                ),
                admission["step_id"],
            )
            return decision("dispatch", step_id=job)

        selector.script[:] = [choose_qc, choose_fit] if qc_first else [choose_fit]
        expected_steps = (
            ("qc-lanes", admission["step_id"]) if qc_first else (admission["step_id"],)
        )
        with running(app) as port:
            for _ in expected_steps:
                status, body, _ = call(
                    port,
                    "/api/scientific/adaptive/" + lineage,
                    origin=f"http://127.0.0.1:{port}",
                    timeout=90,
                    body={
                        "action": "advance",
                        "expected_revision": _graph_revision(flow),
                        "allow_data_execution": True,
                        "allow_model_execution": True,
                    },
                )
                assert status == 200, body.decode()
                assert json.loads(body)["steps_dispatched"] == 1, body.decode()
        for step in expected_steps:
            (receipt,) = flow.history(step)
            assert receipt["status"] == "succeeded", receipt
        assert {row["fit_kind"] for row in briefings[0]["jobs"]} == {
            "registered_step",
            "comparison",
        }
        if qc_first:
            assert [row["fit_kind"] for row in briefings[1]["jobs"]] == ["comparison"]
        else:
            assert flow.history("qc-lanes") == ()
        broker = app._adaptive_bindings[lineage].controller.broker
        records = flow.controller_state()["decisions"]
        for entry, briefing in zip(records, briefings, strict=True):
            assert "graph" not in briefing and "scientific_state" not in briefing
            receipt = broker.reservation(
                entry["evidence"]["controller_disclosure_reservation_id"]
            )
            assert receipt["classification"] == "structural"
            assert receipt["content_sha256"] == document_digest(briefing)
    finally:
        app.close()


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "dispatch", "step_id": "retained-private-step", "evidence_ids": []},
        {"kind": "dispatch", "step_id": "research-fit-1", "evidence_ids": ["outcome"]},
        {"kind": "revise", "step_id": "research-fit-1", "evidence_ids": []},
        {"kind": "stop", "disposition": "completed_exploratory", "evidence_ids": []},
    ],
)
def test_dispatch_brief_refuses_actions_outside_its_scheduling_scope(action):
    from devbench.analysis.controller_research_dispatch import (
        ResearchDispatchBrief,
        _Job,
    )

    brief = ResearchDispatchBrief(
        (
            _Job(
                "research-fit-1",
                "retained-private-step",
                "comparison",
                "B-I",
                digest("parameters"),
                (digest("reference counts"),),
            ),
        ),
        False,
    )
    with pytest.raises(ValueError):
        brief.resolve_action(action)
