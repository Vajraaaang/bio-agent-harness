"""Exercise exact cross-aim family inventories and prospective ledger custody."""

import pytest
from pydantic import ValidationError

from devbench.core._hashing import canonical_json, canonical_sha256
from devbench.core.joint_bh import (
    JointBHMember,
    JointBHPlan,
    adjust_joint_bh,
)
from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    RuntimeEvidence,
)
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.thesis_aims import ExplanationAssessment, ThesisAimLifecycle
from devbench.platform.thesis_joint_bh import ThesisJointBHRegistry
from devbench.platform.thesis_workflow import ThesisWorkflow, _check_confirmatory
from tests.test_investigation_invalidation import digest
from tests.test_thesis_aims import _aim, _approve, _lifecycle
from tests.test_thesis_bulk_acceptance import _research_actions
from tests.test_tier_a_executor import _fixture, _submit

pytestmark = pytest.mark.wal


@pytest.fixture
def declared_pool(workspace_tmp_path):
    _, executor, envelope = _fixture(workspace_tmp_path / "cohort")
    _, first, _ = executor.validate(envelope)
    flow, aims = _lifecycle(workspace_tmp_path / "aims")
    plan, second = _propose_pool(aims, first, "bulk-fit")
    return flow, aims, plan, first, second


def _propose_pool(aims, first, step_id, *, second=None, comparison=False):
    if second is None:
        second = first.model_copy(update={"expression_sha256": digest("other-cohort")})
    plan = JointBHPlan(
        family_id="cohorts",
        fdr=0.05,
        members=(
            JointBHMember.from_pipeline("second", second, step_id="fit-second"),
            JointBHMember.from_pipeline("first", first, step_id=step_id),
        ),
        dependence="independence",
        dependence_rationale="Synthetic declaration for the custody fixture.",
    )
    alternatives = (
        {
            "explanation_id": "composition",
            "explanation": "Composition could account for the difference",
            "discriminating_check": "Compare the locked synthetic cohorts",
        },
    )
    for member, spec in (("first", first), ("second", second)):
        aims.propose(
            _aim(
                member,
                kind="confirmatory",
                input_sha256s=[spec.expression_sha256, spec.metadata_sha256],
                evaluation_sha256s=[spec.expression_sha256],
                competing_explanations=(
                    alternatives if comparison and member == "first" else ()
                ),
                multiplicity={
                    "family_id": plan.family_id,
                    "aim_ids": plan.aim_ids,
                    "hypotheses_sha256": plan.sha256(),
                    "fdr": plan.fdr,
                },
            )
        )
    return plan, second


def test_complete_family_is_adjusted_in_one_denominator(declared_pool):
    _, _, plan, first, second = declared_pool
    projected = adjust_joint_bh(
        plan,
        specifications={"first": first, "second": second},
        measurements={
            "first": (("g1", 0.01), ("g2", 0.04)),
            "second": (("g1", None), ("g2", 0.03)),
        },
    )
    assert [row.q_value for row in projected["first"]] == pytest.approx(
        [0.04, 0.04 * 4 / 3]
    )
    assert projected["second"][0].q_value is None
    assert projected["second"][1].q_value == pytest.approx(0.04 * 4 / 3)
    assert [row.p_value for row in projected["first"]] == [0.01, 0.04]


@pytest.mark.parametrize("defect", ["missing_aim", "wrong_model", "reordered", "nan"])
def test_incomplete_or_unbound_family_is_refused(declared_pool, defect):
    _, _, plan, first, second = declared_pool
    specifications = {"first": first, "second": second}
    measurements = {
        "first": (("g1", 0.01), ("g2", 0.04)),
        "second": (("g1", None), ("g2", 0.03)),
    }
    if defect == "missing_aim":
        measurements.pop("second")
    elif defect == "wrong_model":
        specifications["second"] = first
    elif defect == "reordered":
        measurements["first"] = tuple(reversed(measurements["first"]))
    else:
        measurements["first"] = (("g1", float("nan")), ("g2", 0.04))
    with pytest.raises(ValueError):
        adjust_joint_bh(plan, specifications=specifications, measurements=measurements)


def test_legacy_plan_identity_is_preserved_and_duplicate_steps_fail(declared_pool):
    _, _, plan, _, _ = declared_pool
    historical = plan.model_dump(mode="json")
    for member in historical["members"]:
        member.pop("step_id")
    previous = JointBHPlan.model_validate_json(canonical_json(historical))
    assert previous.sha256() == canonical_sha256(historical)
    revised = plan.model_dump(mode="json")
    revised["members"][0]["step_id"] = revised["members"][1]["step_id"]
    with pytest.raises(ValidationError, match="distinct frozen step"):
        JointBHPlan.model_validate_json(canonical_json(revised))


def _freeze(registry, plan, actor="researcher"):
    return registry.freeze(
        plan, actor=actor, rationale="Review both planned member inventories."
    )


def test_prospective_pool_reopens_with_operator_attribution(declared_pool):
    flow, aims, plan, _, _ = declared_pool
    registry = ThesisJointBHRegistry(aims)
    with pytest.raises(PermissionError):
        _freeze(registry, plan, actor="unauthorized")
    with pytest.raises(OperationBlocked, match="joint_bh_plan_missing"):
        registry.read(plan.family_id)
    revision = _freeze(registry, plan)
    assert _freeze(registry, plan) == revision
    _approve(aims, "first")
    _approve(aims, "second")
    reopened = ThesisJointBHRegistry(type(aims)(flow.ledger, flow.lineage_id))
    retained = reopened.read("cohorts")
    assert retained.plan == plan
    assert retained.actor == "researcher"
    assert retained.aim_versions == {"first": 1, "second": 1}


def test_scope_cannot_be_added_after_approval(declared_pool):
    _, aims, plan, _, _ = declared_pool
    _approve(aims, "first")
    with pytest.raises(OperationConflict, match="before the first aim approval"):
        _freeze(ThesisJointBHRegistry(aims), plan)


def test_joint_scope_cannot_be_added_after_a_local_fit(workspace_tmp_path):
    flow, executor, envelope = _fixture(workspace_tmp_path)
    step, first, _ = executor.validate(envelope)
    aims = ThesisAimLifecycle(
        flow.ledger,
        flow.lineage_id,
        authorize_operator=lambda actor: actor == "researcher",
    )
    _, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded"
    plan, _ = _propose_pool(aims, first, step.step_id)
    with pytest.raises(OperationConflict, match="before a member fit is dispatched"):
        _freeze(ThesisJointBHRegistry(aims), plan)


def test_joint_plan_requires_its_ledger_outbox_binding(declared_pool):
    flow, aims, plan, _, _ = declared_pool
    registry = ThesisJointBHRegistry(aims)
    revision = _freeze(registry, plan)
    with flow.ledger._database() as db:
        db.execute(
            "UPDATE scientific_outbox SET sha256=? WHERE lineage_id=? AND revision=?",
            (digest("corrupt"), flow.lineage_id, revision),
        )
    with pytest.raises(OperationConflict, match="outbox binding"):
        registry.read(plan.family_id)


def test_revising_a_member_invalidates_its_retained_plan(declared_pool):
    _, aims, plan, _, _ = declared_pool
    registry = ThesisJointBHRegistry(aims)
    _freeze(registry, plan)
    definition = aims.ledger._read(
        lambda db: aims._projection(db)[0].aims["first"].definition
    )
    aims.revise(
        definition.model_copy(update={"estimand": "Updated donor-level estimand"}),
        actor="researcher",
        rationale="New information changed the first aim.",
    )
    with pytest.raises(OperationBlocked, match="joint_bh_aim_versions_changed"):
        registry.read(plan.family_id)


def test_joint_gate_checks_the_frozen_step_and_parameters(
    declared_pool,
    workspace_tmp_path,
):
    flow, aims, plan, _, _ = declared_pool
    _, executor, envelope = _fixture(workspace_tmp_path / "gate")
    step, _, _ = executor.validate(envelope)
    registry = ThesisJointBHRegistry(aims)
    _freeze(registry, plan)
    _approve(aims, "first")
    _approve(aims, "second")
    binding = aims.require_ready("first")
    with flow._read_connection() as db:
        scope = _check_confirmatory(binding, step, registry, db)
        assert scope is not None and scope.plan_sha256 == plan.sha256()
        changed = step.model_copy(update={"parameters_sha256": digest("other-plan")})
        with pytest.raises(OperationBlocked, match="joint_bh_parameters_unbound"):
            _check_confirmatory(binding, changed, registry, db)
        relabeled = step.model_copy(update={"step_id": "another-fit"})
        with pytest.raises(OperationBlocked, match="joint_bh_step_unbound"):
            _check_confirmatory(binding, relabeled, registry, db)


def test_comparison_references_exact_joint_member_without_using_primary_identity(
    declared_pool, workspace_tmp_path,
):
    from devbench.platform.thesis_workflow import check_thesis_backend

    flow, aims, plan, first, _ = declared_pool
    _, executor, envelope = _fixture(workspace_tmp_path / "comparison")
    step, _, _ = executor.validate(envelope)
    registry = ThesisJointBHRegistry(aims)
    _freeze(registry, plan)
    _approve(aims, "first")
    _approve(aims, "second")
    family = first.family.model_copy(update={"primary": False})
    specification = first.model_copy(update={"family": family})
    comparison = step.model_copy(update={
        "step_id": "fit-sensitivity", "primary": False, "comparison": True,
        "parameters_sha256": canonical_sha256(specification.model_dump(mode="json")),
    })
    aim = aims.require_ready("first")
    with flow._read_connection() as db:
        reference = _check_confirmatory(aim, comparison, registry, db)
        assert reference is not None and reference.plan_sha256 == plan.sha256()
        assert reference.member.aim_id == "first"
        for member in plan.members:
            for changes in ({"step_id": member.step_id},
                            {"parameters_sha256": member.parameters_sha256}):
                collision = comparison.model_copy(update=changes)
                with pytest.raises(OperationBlocked, match="comparison_reuses_primary"):
                    _check_confirmatory(aim, collision, registry, db)
    envelope["step"] = comparison.model_dump(mode="json")
    scope = {
        "aim_id": "first", "backend_id": executor.backend_id, "route": "tier_a",
        "hypotheses_sha256": plan.sha256(),
        "joint_bh_reference": reference.model_dump(mode="json"),
    }
    envelope["thesis_scope"] = scope
    check_thesis_backend(
        envelope, executor.backend_id, validated_specification=specification,
    )
    changed_family = specification.model_copy(update={"family": family.model_copy(
        update={"feature_ids": tuple(reversed(family.feature_ids))},
    )})
    with pytest.raises(OperationBlocked, match="confirmatory_family_unbound"):
        check_thesis_backend(
            envelope, executor.backend_id, validated_specification=changed_family,
        )
    scope["aim_id"] = "second"
    with pytest.raises(OperationBlocked, match="confirmatory_family_unbound"):
        check_thesis_backend(
            envelope, executor.backend_id, validated_specification=specification,
        )
    scope["aim_id"] = "first"
    scope["joint_bh"] = scope["joint_bh_reference"]
    with pytest.raises(OperationBlocked, match="confirmatory_family_unbound"):
        check_thesis_backend(
            envelope, executor.backend_id, validated_specification=specification,
        )


def test_tier_a_retains_joint_fit_in_a_non_consumer_scope(workspace_tmp_path):
    from devbench.platform.thesis_workflow import check_thesis_backend

    flow, executor, envelope = _fixture(workspace_tmp_path)
    step, specification, _ = executor.validate(envelope)
    member = JointBHMember.from_pipeline("first", specification, step_id=step.step_id)
    envelope["thesis_scope"] = {
        "aim_id": member.aim_id,
        "hypotheses_sha256": digest("joint scope"),
        "backend_id": executor.backend_id,
        "route": "tier_a",
        "joint_bh": {
            "family_id": "cohorts",
            "plan_sha256": digest("joint scope"),
            "member": member.model_dump(mode="json"),
        },
    }
    check_thesis_backend(
        envelope,
        executor.backend_id,
        validated_specification=specification,
    )
    _, receipt = _submit(flow, executor, envelope)
    artifact = receipt.outputs["result"]
    assert artifact.representation == "joint_bh_unadjusted_parquet"
    assert artifact.permitted_uses == ()
    assert executor.verify(envelope, receipt)
    assert executor.load_receipt(envelope, attempt_id=receipt.job_id) == receipt
    with pytest.raises(ValueError, match="typed Parquet"):
        executor.load_output(artifact)


def _bind_fit(flow, step, executor, spec, *, aim_id="first"):
    axis = digest("synthetic donor axis")
    expression = AttestedMeasurement(
        kind="normalized_expression",
        artifact_sha256=spec.expression_sha256,
        receipt_sha256=digest("synthetic expression receipt"),
        observation_axis_sha256=axis,
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=digest("synthetic preflight"),
        observation_axis_sha256=axis,
        estimability="estimable",
        independent_units_per_group=(4, 4),
    )
    runtime = RuntimeEvidence(
        question="differential_expression",
        tier="tier_a",
        operation_id=step.operation_id,
        runtime_sha256=executor.environment_sha256,
    )
    thesis = ThesisWorkflow(
        flow.ledger,
        flow.lineage_id,
        verify_attestation=lambda _db, record: record in (expression, design, runtime),
    )
    bound, decision = thesis.bind_step(
        step,
        aim_id=aim_id,
        request=AnswerabilityRequest(
            family_id=1,
            assay="bulk_rna_seq",
            question="differential_expression",
            entry_point="normalized_expression",
        ),
        measurements=(expression,),
        design=design,
        runtime=runtime,
        backend_id=executor.backend_id,
    )
    assert decision.status == "ready"
    flow.insert(bound)
    return design.receipt_sha256


def test_joint_fit_dispatches_but_controller_and_claim_paths_hold(workspace_tmp_path):
    flow, executor, envelope = _fixture(workspace_tmp_path, step_id="fit", insert=False)
    step, specification, inputs = executor.validate(envelope)
    aims = ThesisAimLifecycle(
        flow.ledger,
        flow.lineage_id,
        authorize_operator=lambda actor: actor == "researcher",
    )
    plan, _ = _propose_pool(aims, specification, step.step_id)
    _freeze(ThesisJointBHRegistry(aims), plan)
    _approve(aims, "first")
    _approve(aims, "second")
    design_digest = _bind_fit(flow, step, executor, specification)
    actions = _research_actions(
        flow,
        executor,
        inputs,
        {
            "sha256": design_digest,
            "metrics": {"estimable": True},
            "scientific_validity_established": False,
        },
    )
    outcome = actions.perform_adaptive(flow, step.step_id)
    assert outcome["status"] == "succeeded", outcome["reason"]
    assert outcome["accepted_record_id"]
    for purpose in ("cache", "query", "export", "replay-current-use"):
        for use in ("compute", "claim"):
            with pytest.raises(OperationBlocked, match="joint_bh_member_unpublished"):
                flow.current_use(step.step_id, "result", purpose=purpose, use=use)
    with pytest.raises(OperationBlocked, match="aim_evidence_artifact_unadmitted"):
        aims.record_outcome(
            "first",
            version=1,
            status="no_significant_finding",
            rationale="Local fits cannot establish a pooled finding.",
            uncertainty="Joint inference is still pending.",
            evidence=(
                {
                    "step_id": step.step_id,
                    "artifact_sha256": outcome["outputs"]["result"]["sha256"],
                    "receipt_sha256": outcome["receipt_sha256"],
                },
            ),
        )


def _second_cohort(flow, executor, step, specification, inputs, *, peer_signal):
    from devbench.platform.adaptive_workflow_state import AdaptiveStep

    artifacts = {}
    for port, original in inputs.items():
        with executor.training.open(original.sha256) as stream:
            source = stream.read(256 * 1024 + 1)
        assert len(source) <= 256 * 1024
        if port == "metadata":
            header, newline, data = source.partition(b"\n")
            source = header + newline + data.replace(b",d", b",other-d")
        elif original.representation == "continuous_h5":
            import io

            import h5py

            buffer = io.BytesIO(source)
            with h5py.File(buffer, "r+") as file:
                file["expression"][0, 0] = 2
            source = buffer.getvalue()
        else:
            source = source.replace(b"g1,1,3,2", b"g1,2,3,2")
            if peer_signal:
                source = source.replace(
                    b"g1,2,3,2,4,1,3,2,4", b"g1,2,3,2,4,25,27,24,28"
                )
        identity = executor.training.put(source)
        artifacts[port] = original.model_copy(
            update={
                "version_id": "other-" + original.version_id,
                "sha256": identity,
                "unit_membership_sha256": digest("other cohort unit membership"),
            }
        )
        flow.register_artifact(artifacts[port])
    spec = specification.model_copy(
        update={
            "expression_sha256": artifacts["expression"].sha256,
            "metadata_sha256": artifacts["metadata"].sha256,
        }
    )
    other = AdaptiveStep.model_validate(
        {
            **step.model_dump(),
            "step_id": "fit-second",
            "branch_id": "other-bulk",
            "parameters_sha256": executor.register_pipeline(spec),
            "inputs": tuple(
                link.model_copy(
                    update={"source_id": artifacts[link.port.name].version_id}
                )
                for link in step.inputs
            ),
        }
    )
    return other, spec, artifacts


@pytest.mark.parametrize(
    "scenario", (
        "compact", "mounted", "contradictory-negative", "negative-with-peer",
        "supported-omitted", "comparison-only",
    )
)
def test_joint_release_requires_every_receipt_and_revokes_as_one(
    workspace_tmp_path, scenario, monkeypatch
):
    import pyarrow.parquet as pq

    from devbench.core.publication_families import budgeted_bh
    from tests.test_tier_a_executor import _require_mounted_image

    mounted = scenario == "mounted"
    case_values = {
        "mounted": (25, 27, 24, 28),
        "contradictory-negative": (25, 27, 24, 28),
        "supported-omitted": (25, 27, 24, 28),
        "negative-with-peer": (1, 3, 2, 4),
    }
    flow, executor, envelope = _fixture(
        workspace_tmp_path,
        step_id="fit",
        insert=False,
        input_format="h5" if mounted else None,
        case_expression_g1=case_values.get(scenario, (4, 5, 3, 7)),
        mounted_image_id=_require_mounted_image() if mounted else None,
    )
    step, first, inputs = executor.validate(envelope)
    other, second, other_inputs = _second_cohort(
        flow, executor, step, first, inputs,
        peer_signal=scenario == "negative-with-peer",
    )
    aims = ThesisAimLifecycle(
        flow.ledger, flow.lineage_id, authorize_operator=lambda a: a == "researcher"
    )
    plan, _ = _propose_pool(
        aims, first, step.step_id, second=second,
        comparison=scenario == "comparison-only",
    )
    _freeze(ThesisJointBHRegistry(aims), plan)
    for aim_id in plan.aim_ids:
        _approve(aims, aim_id)
    design = _bind_fit(flow, step, executor, first)
    _bind_fit(flow, other, executor, second, aim_id="second")
    actions = _research_actions(
        flow,
        executor,
        {**inputs, **{"other-" + name: item for name, item in other_inputs.items()}},
        {
            "sha256": design,
            "metrics": {"estimable": True},
            "scientific_validity_established": False,
        },
        step_ids=(step.step_id, other.step_id),
    )
    first_run = actions.perform_adaptive(flow, step.step_id)
    if not mounted:
        _configure_controller(actions, monkeypatch)
        context = actions.adaptive_controller_context(flow)
        assert not context.get("joint_publication_choices")
        assert not context["query_handles"][0].get("handle_id")
    with pytest.raises(OperationBlocked, match="joint_bh_member_pending"):
        actions.publish_joint_bh(flow, plan.family_id)
    second_run = actions.perform_adaptive(flow, other.step_id)
    retained = executor.runs / second_run["attempt_id"] / "receipt.json"
    saved = retained.read_bytes()
    retained.chmod(0o600)
    try:
        retained.write_bytes(saved.replace(b'"succeeded"', b'"failed"'))
        with pytest.raises(OperationBlocked, match="receipt_unverified"):
            actions.publish_joint_bh(flow, plan.family_id)
    finally:
        retained.write_bytes(saved)
        retained.chmod(0o400)
    decision = (
        None if mounted else _publish_using_controller(actions, flow, monkeypatch)
    )
    release = actions.publish_joint_bh(flow, plan.family_id)
    if decision is not None:
        assert decision["receipt_sha256"] == release.receipt_sha256()
    assert actions.publish_joint_bh(flow, plan.family_id) == release
    assert release.plan_sha256 == plan.sha256()
    assert [item.receipt_sha256 for item in release.sources] == [
        first_run["receipt_sha256"],
        second_run["receipt_sha256"],
    ]
    rows = []
    for step_id in (step.step_id, other.step_id):
        artifact = flow.current_use(step_id, "result", purpose="query", use="claim")
        with executor.open_output(artifact) as reader:
            rows.extend(
                pq.read_table(reader, columns=["p_value", "q_value"]).to_pylist()
            )
    expected = budgeted_bh([row["p_value"] for row in rows], weight="1")
    assert [row["q_value"] for row in rows] == pytest.approx(
        [row["q_local"] for row in expected]
    )
    provenance = flow.disclosure_provenance(step.step_id, "result")
    assert {first.expression_sha256, second.expression_sha256} <= set(
        provenance["dependencies"]
    )
    pooled = flow.current_use(step.step_id, "result", purpose="export")
    with pytest.raises(PermissionError, match="require their retained receipts"):
        flow.register_artifact(pooled)
    _assert_release_reopens(flow, pooled)
    if mounted:
        _assert_pooled_ora(flow, executor, actions, release, pooled)
    reference = {
        "step_id": step.step_id,
        "artifact_sha256": pooled.sha256,
        "receipt_sha256": release.receipt_sha256(),
    }
    conclusion = _record_first_joint_evidence(aims, scenario, rows, plan.fdr, reference)
    assert aims.scientific_context()["research_aims"][0]["status"] == conclusion
    _assert_joint_synthesis(
        workspace_tmp_path,
        flow,
        executor,
        aims,
        release,
        scenario=scenario,
        monkeypatch=monkeypatch,
    )
    flow.retire_branch(
        other.branch_id,
        reason="scope_change",
        evidence_sha256=second_run["receipt_sha256"],
    )
    current = aims.scientific_context()["research_aims"][0]
    if scenario == "comparison-only":
        assert current["status"] == "ready"
        assert "composition" in current["stale_explanation_evidence"]
        _assert_retired_comparison_is_audit_only(workspace_tmp_path, flow, executor)
    else:
        assert current["status"] == "invalidated"
    with pytest.raises(OperationBlocked):
        flow.current_use(step.step_id, "result", purpose="export")
    with pytest.raises(OperationBlocked):
        executor.load_output(pooled)
    if mounted:
        with pytest.raises(OperationBlocked):
            flow.current_use("pooled-ora", "pathway_result", purpose="query")


def _assert_pooled_ora(flow, executor, actions, release, pooled):
    import pyarrow.parquet as pq

    from devbench.core.rna_pathway_analysis import (
        GeneSet,
        PathwayPlan,
        PathwayResult,
    )
    from devbench.core.rna_pathway_analysis import (
        TestedGeneUniverse as GeneUniverse,
    )
    from devbench.platform.adaptive_workflow_state import ArtifactVersion, BasisUse

    with executor.open_output(pooled) as source:
        rows = pq.read_table(
            source, columns=["gene_id", "estimate_status", "q_value"]
        ).to_pylist()
    estimated = [row for row in rows if row["estimate_status"] == "estimated"]
    tested = tuple(row["gene_id"] for row in estimated)
    selected = tuple(
        row["gene_id"] for row in estimated if row["q_value"] <= release.fdr
    )
    assert selected and len(selected) < len(tested)
    universe = GeneUniverse(
        result_receipt_sha256=release.receipt_sha256(),
        gene_statistics_sha256=pooled.sha256,
        filter_receipt_sha256=digest("pooled filter"),
        genes=tested,
        selected=selected,
    )
    plan = PathwayPlan(
        method="ora",
        collection_id="synthetic",
        collection_version="1",
        min_set_size=2,
        gene_sets=(GeneSet(set_id="tested", genes=tested),),
    )
    flow.record_basis("pooled-filter", "review_basis", universe.filter_receipt_sha256)

    def register(declared):
        flow.record_basis("pooled-release", "source", declared.result_receipt_sha256)
        return executor.register_pathway_step(
            flow,
            plan,
            step_id="pooled-ora",
            branch_id="pathways",
            biological_unit="donor",
            sources={"pathway_statistics": (pooled.version_id, None)},
            basis=(
                BasisUse(basis_id="pooled-release"),
                BasisUse(basis_id="pooled-filter"),
            ),
            universe=declared,
            verify_universe=lambda candidate: candidate == declared,
            foreground=declared.selected,
        )

    for change, message in (
        ({"result_receipt_sha256": digest("wrong")}, "execution receipt"),
        ({"gene_statistics_sha256": digest("wrong")}, "retained release"),
        (
            {"genes": tuple(reversed(tested))},
            "exact ordered filtered tested background",
        ),
    ):
        with pytest.raises(PermissionError, match=message):
            register(universe.model_copy(update=change))
        assert pooled.version_id not in flow._read_projection(
            lambda db: flow._state(db).artifacts
        )
    step = register(universe)
    flow.insert(step)
    outcome = actions.perform_adaptive(flow, step.step_id)
    assert outcome["status"] == "succeeded", outcome
    artifact = ArtifactVersion.model_validate(outcome["outputs"]["pathway_result"])
    with executor.open_output(artifact) as stream:
        result = PathwayResult.model_validate_json(stream.read())
    assert result.tested_gene_count == len(tested)
    assert result.clinical_release_allowed is False


def _record_first_joint_evidence(aims, scenario, rows, fdr, reference):
    if scenario == "comparison-only":
        aims.assess_explanation(
            "first",
            ExplanationAssessment.model_validate(
                {
                    "explanation_id": "composition",
                    "conclusion": "inconclusive",
                    "evidence": reference,
                    "rationale": "The synthetic comparison leaves composition open.",
                }
            ),
            version=1,
        )
        return "ready"
    conclusion = (
        "supported"
        if any(row["q_value"] <= fdr for row in rows[:2])
        else "no_significant_finding"
    )
    if scenario in {"contradictory-negative", "negative-with-peer"}:
        assert conclusion == {
            "contradictory-negative": "supported",
            "negative-with-peer": "no_significant_finding",
        }[scenario]
        assert any(row["q_value"] <= fdr for row in rows[2:])
        conclusion = "no_significant_finding"
    if scenario == "supported-omitted":
        assert conclusion == "supported"
    aims.record_outcome(
        "first",
        version=1,
        status=conclusion,
        rationale="Synthetic test.",
        uncertainty="Real-cohort performance is unmeasured.",
        evidence=(reference,),
    )
    return conclusion


def _configure_controller(actions, monkeypatch):
    from types import SimpleNamespace

    controller = actions.controller
    controller.action_catalog = {}
    controller.literature_handles = ()
    controller.max_decisions = 6
    controller._review_capabilities = {}
    controller.invoker = SimpleNamespace(
        limits=SimpleNamespace(max_context_bytes=65_536),
        _runtime=lambda: None,
    )
    actions.planner = SimpleNamespace(get=lambda _: {"context": {}})
    monkeypatch.setattr(actions, "_controller_pipeline_inputs", lambda *_: [])


def _publish_using_controller(actions, flow, monkeypatch):
    flow.bind_controller({"max_decisions": 6})
    monkeypatch.setattr(flow, "_controller_admission", lambda *_: None)
    context = actions.adaptive_controller_context(flow)
    (choice,) = context["joint_publication_choices"]
    different_family = {**choice, "publication_sha256": digest("unoffered receipts")}
    unoffered = _select_joint_publication(flow, context, different_family)
    assert actions.apply_adaptive_decision(flow, unoffered) == {
        "status": "rejected",
        "kind": "publish_joint_bh",
        "reason": "joint_publication_choice_not_currently_offered",
    }
    current = actions.adaptive_controller_context(flow)
    pending = _select_joint_publication(flow, current, choice)

    original = flow._event

    def interrupted(db, event, record, **kwargs):
        if event == "controller":
            raise RuntimeError("synthetic interrupted completion")
        return original(db, event, record, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(flow, "_event", interrupted)
        with pytest.raises(RuntimeError, match="synthetic interrupted completion"):
            actions.apply_adaptive_decision(flow, pending)
    with pytest.raises(OperationBlocked, match="member_unpublished"):
        flow.current_use("fit", "result", purpose="query")
    result = actions.apply_adaptive_decision(flow, pending)
    assert result["status"] == "applied"
    assert actions.apply_adaptive_decision(flow, pending)["reconciled"] is True
    assert len(flow.authority.attempts(flow.lineage_id)) == 2
    assert not actions.adaptive_controller_context(flow).get(
        "joint_publication_choices"
    )
    flow.stop_controller({"status": "partial", "reason": "synthetic fixture completed"})
    return result


def _select_joint_publication(flow, context, choice):
    from devbench.analysis.episode_agent import AdaptiveController

    pending = flow.begin_controller_decision(
        context, expected_revision=context["revision"]
    )
    action = {
        "kind": "publish_joint_bh",
        **choice,
        "rationale": "Every frozen member fit has a receipt.",
        "evidence_ids": [],
    }
    validated = AdaptiveController._validate_controller_selection(
        {"action": action}, context
    )
    return flow.select_controller_decision(
        pending["sequence"],
        validated,
        {"controller_disclosure_source_id": "input-expression"},
    )


def _assert_joint_synthesis(
    root, flow, executor, aims, release, *, scenario, monkeypatch
):
    import json

    from devbench.analysis import thesis_synthesis
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )

    schema = root / "answer_schema.json"
    declaration = {"type": "object", "required": ["gene_statistics"]}
    schema.write_bytes(canonical_json(declaration))
    spec = ThesisSynthesisSpec.model_validate(
        {
            "question": "Do the two synthetic cohorts support a condition effect?",
            "outcome": "partial",
            "expected_revision": flow.status()["revision"],
            "answer_schema_sha256": canonical_sha256(declaration),
            "discovery_threshold": release.fdr,
            "fields": {
                "gene_statistics": {
                    "step_id": "fit",
                    "port": "result",
                    "projection": "tier_a_gene_statistics",
                }
            },
        }
    )

    def write(destination, selected=spec):
        return write_thesis_package(
            destination,
            flow,
            selected,
            answer_schema=schema,
            open_artifact=executor.open_output,
            aim_lifecycle=aims,
            tier_a_executor=executor,
        )

    peer_ref = spec.fields["gene_statistics"].model_copy(
        update={"step_id": "fit-second"}
    )
    peer_spec = spec.model_copy(update={"fields": {"gene_statistics": peer_ref}})
    omitted_spec = spec.model_copy(update={"fields": {}})
    if scenario == "contradictory-negative":
        for name, selection in (
            ("peer-only", peer_spec), ("primary", spec), ("omitted", omitted_spec)
        ):
            with pytest.raises(ValueError, match="Aim first has a significant q-value"):
                write(root / name, selection)
            assert not (root / name).exists()

        # The verifier also checks summaries when an untrusted writer skips validation.
        older = root / "inconsistent"
        with monkeypatch.context() as patch:
            patch.setattr(thesis_synthesis, "validate_negative_aims", lambda *_: None)
            older_seal = write(older, peer_spec)
        with pytest.raises(ValueError, match="Aim first has a significant q-value"):
            verify_thesis_package(older, older_seal["root_sha256"])
        return

    root = root / "thesis"
    if scenario in {"supported-omitted", "comparison-only"}:
        seal = write(root, omitted_spec)
        _assert_cited_release_replays(root, seal, executor, release)
        return
    seal = write(root, peer_spec if scenario == "negative-with-peer" else spec)
    verify_thesis_package(root, seal["root_sha256"])
    manifest = json.loads((root / "provenance_manifest.json").read_bytes())
    identity = release.receipt_sha256()
    assert manifest["fields"]["gene_statistics"]["receipt_sha256"] == identity
    assert manifest["cross_aim_bh"][identity] == release.model_dump(mode="json")
    if scenario == "negative-with-peer":
        summary, = manifest["negative_aim_families"]["first"]
        assert summary["artifact_sha256"] == release.outputs["first"].sha256
        assert summary["receipt_sha256"] == identity
        assert summary["step_id"] == "fit"
        assert summary["tested_hypotheses"] == 2
        assert summary["minimum_q"] > release.fdr
        root = root.parent / "omitted"
        seal = write(root, omitted_spec)
        assert seal["answer_status"] == "withheld"
        verify_thesis_package(root, seal["root_sha256"])
        manifest = json.loads((root / "provenance_manifest.json").read_bytes())
        assert manifest["negative_aim_families"]["first"] == [summary]
        assert "2 tested hypotheses at q ≤ 0.05" in (root / "summary.md").read_text()
        assert manifest["cross_aim_bh"][identity] == release.model_dump(mode="json")

    assert manifest["runtime"]["numerical_replay_available"] is True
    assert manifest["runtime"]["cross_aim_bh_replay"] == "available"
    if executor.mounted_image_id is None:
        assert manifest["runtime"]["compact_fit_replay"] is True
    _assert_pooled_parity(root, seal, executor, release)


def _assert_cited_release_replays(root, seal, executor, release):
    import json

    from devbench.analysis.thesis_synthesis import verify_thesis_package

    assert seal["answer_status"] == "withheld"
    verify_thesis_package(root, seal["root_sha256"])
    manifest = json.loads((root / "provenance_manifest.json").read_bytes())
    assert not manifest["fields"]
    assert manifest["cross_aim_bh"][release.receipt_sha256()] == release.model_dump(
        mode="json"
    )
    _assert_pooled_parity(root, seal, executor, release)


def _assert_retired_comparison_is_audit_only(root, flow, executor):
    import json

    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )

    schema = root / "answer_schema.json"
    spec = ThesisSynthesisSpec(
        question="What evidence remains available for the competing explanation?",
        outcome="partial",
        expected_revision=flow.status()["revision"],
        answer_schema_sha256=canonical_sha256(json.loads(schema.read_bytes())),
    )
    package = root / "after-retirement"
    seal = write_thesis_package(
        package, flow, spec, answer_schema=schema, open_artifact=executor.open_output,
        tier_a_executor=executor,
    )
    verify_thesis_package(package, seal["root_sha256"])
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    index = json.loads((package / "replay_bundle/index.json").read_bytes())
    assert "cross_aim_bh" not in manifest
    assert "cross_aim_bh" not in index
    (aim, _) = manifest["aim_lifecycle"]["research_aims"]
    assert aim["assessed_explanations"][0]["explanation_id"] == "composition"
    assert "composition" in aim["stale_explanation_evidence"]


def _assert_pooled_parity(root, seal, executor, release):
    import json
    import subprocess
    import sys

    from devbench.analysis.thesis_joint_replay import bind_mounted_releases

    index = json.loads((root / "replay_bundle/index.json").read_bytes())
    recipes = {item["step_id"]: item for item in index["steps"]}
    assert set(recipes) == {source.step_id for source in release.sources}
    publications = {release.receipt_sha256(): release.model_dump(mode="json")}
    incomplete = dict(recipes)
    incomplete.pop(release.sources[1].step_id)
    assert bind_mounted_releases(publications, incomplete) == {}
    wrong_receipt = {
        **recipes,
        "fit": {**recipes["fit"], "receipt_sha256": digest("different fit")},
    }
    with pytest.raises(ValueError, match="differs from its source receipt"):
        bind_mounted_releases(publications, wrong_receipt)
    if executor.mounted_image_id is None:
        from devbench.analysis.thesis_compact_replay import (
            CompactReplayRequest,
            _runtime,
        )

        request = CompactReplayRequest.model_validate_json(
            (root / recipes["fit"]["request_path"]).read_bytes()
        )
        assert request.environment_sha256 == executor.environment_sha256
        changed = request.model_copy(update={"code_sha256": digest("modified runtime")})
        with pytest.raises(ValueError, match="recorded host runtime"):
            _runtime(changed)

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "replay_bundle/replay.py"),
            "--expected-root",
            seal["root_sha256"],
            "--input-root",
            str(executor.training.root),
            "--output",
            str(root.parent / "replayed"),
            "--allow-data-execution",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )
    replay = json.loads(completed.stdout)
    assert replay["exact_genes_parquet_match"] is True
    assert replay["exact_pooled_parquet_match"] is True
    assert replay["cross_aim_bh"] == {
        release.receipt_sha256(): {
            aim_id: artifact.sha256 for aim_id, artifact in release.outputs.items()
        }
    }


def _assert_release_reopens(flow, pooled):
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    ledger = WorkflowOperationLedger(flow.ledger.path, scientific=True, readonly=True)
    reopened = AdaptiveWorkflow(ledger, flow.lineage_id)
    artifact = reopened.current_use("fit", "result", purpose="query")
    assert artifact == pooled
    assert TierAExecutor(ledger).load_output(artifact).startswith(b"PAR1")
