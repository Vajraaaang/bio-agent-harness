"""Synthetic adaptive graph scheduling through the workflow entrypoint."""
from types import SimpleNamespace

import pytest

from tests.test_investigation_invalidation import (
    Worker,
    artifact,
    digest,
    link,
    make,
    run,
    step,
    wal,
)


@wal
def test_research_step_cannot_dispatch_without_a_current_admission(tmp_path):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_admitted_research_inference import research_step

    flow = make(tmp_path)
    flow.insert(research_step().model_copy(update={"step_id": "candidate"}))
    executor = Worker()
    actions = WorkflowActions(
        SimpleNamespace(), flow.ledger, actor="tester", selector=None,
        executor=executor, allow_data_execution=True,
    )

    with pytest.raises(OperationBlocked, match="research|review"):
        actions.perform_adaptive(flow, "candidate")

    assert not executor.calls
    assert flow.ledger.scientific.attempts("study") == []


def _tier_a_admitted_runtime(path):
    from devbench.analysis.episode_agent import AdaptiveController
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
    from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority
    from devbench.analysis.scientific_review_holds import (
        ReviewEntity,
        ReviewHoldLifecycle,
    )
    from devbench.platform.workflow_actions import WorkflowActions
    from tests.test_admitted_research_inference import _accept
    from tests.test_review_hold_lifecycle import binding
    from tests.test_tier_a_executor import _fixture

    flow, executor, envelope = _fixture(path)
    identity = envelope["step"]["step_id"]
    broker = EvidenceBroker(flow.authority)
    policy = broker.register_policy(AccessPolicy("synthetic-tier-a", 1))
    broker.register_context(
        "controller", principal_id="controller", lineage_id=flow.lineage_id,
        role="controller", phase="analysis", policy_sha256=policy,
    )
    for role, item in envelope["inputs"].items():
        source_id = "input-" + role
        broker.register_source(
            source_id, content_sha256=item["sha256"], roots=(item["sha256"],),
            classification="structural",
        )
        broker.delivery("controller", source_id).release({"input_role": role})

    lifecycle = ReviewHoldLifecycle(flow.authority, flow.lineage_id)
    lifecycle.register_entities((ReviewEntity(entity_id=identity, kind="fit"),))
    lifecycle.register_evidence(
        "checked-design", digest("rank-check"), relevant_entity_ids=(identity,),
    )
    review = binding()
    narrative, plan = digest("controller-narrative"), digest("review-plan")
    for touchpoint in ("design", "publication"):
        reviewer = touchpoint + "-challenger"
        broker.register_context(
            reviewer, principal_id=reviewer, lineage_id=flow.lineage_id,
            role="challenger", phase=touchpoint + "_initial", policy_sha256=policy,
        )
        cycle = touchpoint + "-review"
        lifecycle.open_cycle(
            cycle, touchpoint, review, reviewer_id=reviewer, context_id=reviewer,
            narrative_sha256=narrative, validation_plan_sha256=plan,
            exposure="structural", evidence_ids=("checked-design",),
            entity_ids=(identity,),
        )
        lifecycle.record_initial_assessment(
            cycle, assessment_sha256=digest(cycle + "-initial"),
        )
        lifecycle.reveal_narrative(
            cycle, narrative_sha256=narrative, validation_plan_sha256=plan,
        )
        lifecycle.complete_cycle(cycle, assessment_sha256=digest(cycle + "-final"))

    controller = object.__new__(AdaptiveController)
    controller.broker = broker
    controller.context_id = "controller"
    actions = WorkflowActions(
        None, flow.ledger, actor="fixture", selector=None, controller=controller,
        allow_data_execution=True, tier_a_executor=executor,
        review_lifecycle=lifecycle, review_binding=lambda _: review,
        diagnostic_qualification=DiagnosticQualificationAuthority(_accept),
    )
    return flow, executor, actions, identity


@wal
def test_reviewed_research_dispatch_executes_registered_tier_a_and_retains_parquet(
    workspace_tmp_path,
):
    import pyarrow.parquet as pq

    from devbench.analysis.evidence_access import AccessPolicy

    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)
    outcome = actions.perform_adaptive(flow, identity)

    assert outcome["status"] == "succeeded", outcome
    artifact = flow.current_use(identity, "result", purpose="query", use="claim")
    with executor.open_output(artifact) as stream:
        rows = pq.read_table(stream).to_pylist()
    assert [row["gene_id"] for row in rows] == ["g1", "g2"]
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in rows)
    broker = actions.controller.broker
    policy = broker.register_policy(AccessPolicy(
        "synthetic-research-readback", 1, allow_outcomes=True, allow_statistics=True,
    ))
    broker.register_context(
        "scientist", principal_id="scientist", lineage_id=flow.lineage_id,
        role="scientist", phase="readback", policy_sha256=policy,
    )
    broker.register_source(
        "fit-result", content_sha256=artifact.sha256, roots=(artifact.sha256,),
        classification="target_outcome", parents=("input-expression", "input-metadata"),
    )
    delivery = broker.delivery("scientist", "fit-result")
    readback = {
        "load": executor.load_output, "open_artifact": executor.open_output,
        "evidence_delivery": delivery,
    }
    first = flow.query_current(identity, "result", {"limit": 1}, **readback)
    second = flow.query_current(identity, "result", {
        "limit": 1, "cursor": first["query_receipt"]["next_cursor"],
    }, **readback)
    rows = [row for page in (first, second) for row in page["rows"]]
    assert [row["gene_id"] for row in rows] == ["g1", "g2"]
    assert second["query_receipt"]["complete"]
    assert broker.verify_query_page(first) and broker.verify_query_page(second)


@wal
def test_mid_graph_crash_reconciles_original_tier_a_receipt_without_redispatch(
    workspace_tmp_path, monkeypatch,
):
    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import (
        AdaptiveWorkflow,
        StepReceipt,
        request_digest,
    )
    from devbench.platform.langgraph_workflow import AdaptiveRNAWorkflow
    from devbench.platform.workflow_actions import WorkflowActions

    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)
    flow.insert(step("prior-failure"))
    flow.insert(step(
        "after-fit", link("fit", identity, output="result"),
        representations=["parquet"], purpose="checked_observation",
    ))

    class FailedWorker(Worker):
        def run(self, envelope):
            document = super().run(envelope)
            return StepReceipt.model_validate(document).model_copy(update={
                "status": "failed", "reason": "fixture_failure", "outputs": {},
            })

    prior = flow.dispatch("prior-failure", FailedWorker(), actor="fixture")
    flow.hold("prior-failure", "Q-DESIGN", evidence_sha256=digest("earlier-hold"))
    dispatched = []
    run_once = executor.run

    def interrupt_after_receipt(envelope):
        receipt = run_once(envelope)
        dispatched.append(receipt.job_id)
        raise RuntimeError("fixture crash before job reconciliation")

    with monkeypatch.context() as interrupted:
        interrupted.setattr(executor, "run", interrupt_after_receipt)
        with pytest.raises(RuntimeError, match="fixture crash"):
            actions.perform_adaptive(flow, identity)

    reader = WorkflowOperationLedger(flow.ledger.path)
    reopened = AdaptiveWorkflow(reader, flow.lineage_id)
    reopened_executor = TierAExecutor(reader, allow_data_execution=True)
    controller = actions.controller
    controller.broker = EvidenceBroker.open_existing(reader.scientific)
    resumed_actions = WorkflowActions(
        None, reader, actor="fixture", selector=None, controller=controller,
        allow_data_execution=True, tier_a_executor=reopened_executor,
        review_lifecycle=ReviewHoldLifecycle(reader.scientific, flow.lineage_id),
        review_binding=actions.review_binding,
        diagnostic_qualification=actions.diagnostic_qualification,
    )
    monkeypatch.setattr(
        reopened_executor, "run", lambda _: pytest.fail("unexpected redispatch"),
    )
    status = reopened.status()
    assert status["blocked"][identity] == "adaptive_attempt_requires_reconciliation"
    assert not reopened.history(identity)

    # Resume the stateless scheduler with only its registered recovery and
    # dispatch capabilities. No model call is needed for this recovery.
    downstream = Worker()

    def dispatch(workflow, target, expected_revision):
        return workflow.dispatch(
            target, downstream, actor="fixture", expected_revision=expected_revision,
        )

    local_actions = SimpleNamespace(
        reconcile_adaptive=resumed_actions.reconcile_adaptive,
        perform_adaptive=dispatch,
    )
    graph = AdaptiveRNAWorkflow(local_actions, reopened)
    resumed = graph.advance(
        expected_revision=status["revision"],
    )
    assert resumed["reconciled_attempts"] == 1
    assert resumed["steps_dispatched"] == 0
    (result,) = reopened.history(identity)
    assert result["status"] == "succeeded"
    assert result["attempt_id"] == dispatched[0]
    assert len(dispatched) == 1
    assert reopened.history("prior-failure") == (prior,)
    assert reopened.status()["blocked"]["prior-failure"] == "Q-DESIGN"
    continued = graph.advance(expected_revision=resumed["revision"])
    assert continued["steps_dispatched"] == 1
    assert downstream.calls == ["after-fit"]
    assert continued["outcomes"]["after-fit"] == "succeeded"
    assert sum(
        attempt["token"]["backend_id"] == reopened_executor.backend_id
        for attempt in reopened.authority.attempts(reopened.lineage_id)
    ) == 1
    dispatch = reopened._read_projection(
        lambda db: reopened._state(db).dispatches[result["attempt_id"]],
    )
    envelope = dispatch["envelope"]
    assert dispatch["idempotency_key"] == request_digest({
        "step_id": identity,
        "code_digest": reopened_executor.code_sha256,
        "inputs_sha256": request_digest(envelope["pins"]),
        "params_sha256": envelope["step"]["parameters_sha256"],
    })


@wal
def test_tier_a_crash_before_launch_is_recovered_for_bounded_retry(
    workspace_tmp_path, monkeypatch,
):
    from devbench.engine import tier_a_executor
    from devbench.engine.workflow_operations import OperationBlocked

    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)

    def interrupt_before_launch(*args, **kwargs):
        raise RuntimeError("fixture crash before worker launch")

    monkeypatch.setattr(tier_a_executor, "run_sandboxed", interrupt_before_launch)
    for _ in range(3):
        with pytest.raises(RuntimeError, match="before worker launch"):
            actions.perform_adaptive(flow, identity)
        status = flow.status()
        assert status["blocked"][identity] == "adaptive_attempt_requires_reconciliation"
        (recovered,) = actions.reconcile_adaptive(flow)
        assert recovered["execution_state"] == "noncompletion_confirmed"
        assert recovered["cleanup_state"] == "confirmed"
        assert recovered["external_job_id"] is None
        assert identity in flow.ready()

    with pytest.raises(OperationBlocked, match="infrastructure_retry_budget_exhausted"):
        actions.perform_adaptive(flow, identity)
    assert len(flow.authority.attempts(flow.lineage_id)) == 3
    assert actions.reconcile_adaptive(flow) == []


@wal
@pytest.mark.parametrize("change", ["hold", "revoke"])
def test_tier_a_rechecks_authority_after_staging(
    workspace_tmp_path, monkeypatch, change,
):
    from devbench.engine import tier_a_executor
    from devbench.engine.workflow_operations import OperationBlocked

    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)
    stage_inputs = executor._stage_inputs

    def stage_and_change(inputs, directory):
        paths = stage_inputs(inputs, directory)
        if change == "hold":
            flow.hold(identity, "Q-DESIGN", evidence_sha256=digest("new hold"))
        else:
            flow.authority.cancel_execution(flow.lineage_id, actor="fixture")
        return paths

    def launch(*args, _before_start, **kwargs):
        _before_start()
        pytest.fail("Worker start was authorized under stale scientific authority")

    monkeypatch.setattr(executor, "_stage_inputs", stage_and_change)
    monkeypatch.setattr(tier_a_executor, "run_sandboxed", launch)
    expected = "current step" if change == "hold" else "scientific_epoch_revoked"
    with pytest.raises((OperationBlocked, PermissionError), match=expected):
        actions.perform_adaptive(flow, identity)

    (attempt,) = flow.authority.attempts(flow.lineage_id)
    assert attempt["execution_state"] == "submission_uncertain"
    prepared, launched, stopped = executor._read_custody(attempt["token"])
    assert prepared["token"] == attempt["token"]
    assert launched is None and stopped is None
    (recovered,) = flow.reconcile_pending(executor)
    assert recovered["execution_state"] == "noncompletion_confirmed"
    assert recovered["cleanup_state"] == "confirmed"
    assert recovered["external_job_id"] is None
    assert not flow.history(identity)
    assert not flow.ready()


@wal
def test_tier_a_crash_after_verified_cleanup_retains_interruption_and_allows_retry(
    workspace_tmp_path, monkeypatch,
):
    from devbench.engine import tier_a_executor

    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)
    publish = tier_a_executor._write_record

    def interrupt_before_receipt(path, value):
        if path.name == "receipt.json":
            raise RuntimeError("fixture crash before receipt publication")
        publish(path, value)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(tier_a_executor, "_write_record", interrupt_before_receipt)
        with pytest.raises(RuntimeError, match="before receipt publication"):
            actions.perform_adaptive(flow, identity)

    with monkeypatch.context() as recovery:
        recovery.setattr(
            executor, "run", lambda _: pytest.fail("Recovery launched a worker"),
        )
        (failed,) = actions.reconcile_adaptive(flow)
    assert failed["status"] == "failed"
    assert failed["reason"] == "tier_a_coordinator_interrupted"
    assert failed["outputs"] == {}
    (attempt,) = flow.authority.attempts(flow.lineage_id)
    assert attempt["execution_state"] == "failed"
    assert attempt["cleanup_state"] == "confirmed"
    assert attempt["observation_sha256"] == failed["receipt_sha256"]
    assert actions.reconcile_adaptive(flow) == []

    node = flow._read_projection(lambda db: flow._state(db).nodes[identity])
    flow.revise(node, reason="retry", evidence_sha256=failed["receipt_sha256"])
    result = actions.perform_adaptive(flow, identity)
    assert result["status"] == "succeeded"
    assert result["attempt_id"] != failed["attempt_id"]
    assert flow.history(identity) == (failed, result)


@wal
@pytest.mark.parametrize(("reason", "limit", "error"), [
    ("design_change", 3, "scientific_revision_budget_exhausted"),
    ("retry", 2, "unchanged_infrastructure_retry_budget_exhausted"),
])
def test_step_revision_budgets_are_durable(tmp_path, reason, limit, error):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow

    flow = make(tmp_path)
    node = step("bounded")
    flow.insert(node)
    for n in range(limit):
        proposed = node if reason == "retry" else node.model_copy(
            update={"parameters_sha256": digest(str(n))},
        )
        flow.revise(
            proposed, reason=reason, evidence_sha256=digest("revision evidence"),
        )
    reopened = AdaptiveWorkflow(flow.ledger, flow.lineage_id)
    with pytest.raises(OperationBlocked, match=error):
        reopened.revise(node, reason=reason, evidence_sha256=digest("another revision"))


@wal
@pytest.mark.parametrize("run_directory", ["retained", "replaced"])
def test_tier_a_crash_with_unknown_worker_liveness_fences_retries(
    workspace_tmp_path, monkeypatch, run_directory,
):
    from devbench.engine import tier_a_executor
    from devbench.engine.workflow_operations import OperationBlocked

    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)
    flow.insert(step("independent"))

    def interrupt(*args, _before_start, **kwargs):
        _before_start()
        raise RuntimeError("fixture interrupted before a receipt")

    monkeypatch.setattr(tier_a_executor, "run_sandboxed", interrupt)
    with pytest.raises(RuntimeError, match="before a receipt"):
        actions.perform_adaptive(flow, identity)

    (interrupted,) = flow.authority.attempts(flow.lineage_id)
    directory = executor._directory(interrupted["token"]["attempt_id"])
    retained_directory = directory
    if run_directory == "replaced":
        retained_directory = directory.with_name(directory.name + "-displaced")
        directory.rename(retained_directory)
        directory.mkdir(mode=0o700)

    (failure,) = actions.reconcile_adaptive(flow)
    assert failure["reason"] == "adaptive_tier_a_interrupted_cleanup_unverified"
    assert failure["status"] == "failed"
    assert failure["outputs"] == {}
    (attempt,) = flow.authority.attempts(flow.lineage_id)
    assert attempt["execution_state"] == "failed"
    assert attempt["cleanup_state"] == "unknown"
    assert (retained_directory / ".work").is_dir()
    assert actions.reconcile_adaptive(flow) == []
    with pytest.raises(OperationBlocked, match="adaptive_step_not_ready"):
        flow.dispatch(identity, executor, actor="fixture")

    node = flow._read_projection(lambda db: flow._state(db).nodes[identity])
    replacement = node.model_copy(update={
        "parameters_sha256": digest("new parameters"),
    })
    flow.revise(
        replacement, reason="method_change", evidence_sha256=digest("changed scope"),
    )

    awaiting_recovery = "adaptive_attempt_requires_reconciliation"
    assert flow.status()["blocked"][identity] == awaiting_recovery
    with pytest.raises(OperationBlocked, match=awaiting_recovery):
        flow.dispatch(identity, executor, actor="fixture")
    assert len(flow.authority.attempts(flow.lineage_id)) == 1
    independent = Worker()
    assert flow.dispatch("independent", independent, actor="fixture")["status"] == (
        "succeeded"
    )
    assert independent.calls == ["independent"]
    assert flow.history(identity) == (failure,)


@wal
def test_partial_receipt_keeps_its_step_pending_while_independent_work_runs(
    workspace_tmp_path, monkeypatch,
):
    flow, executor, actions, identity = _tier_a_admitted_runtime(workspace_tmp_path)
    flow.insert(step("independent"))

    def interrupt(_):
        (attempt,) = flow.authority.attempts(flow.lineage_id)
        folder = executor._directory(attempt["token"]["attempt_id"])
        folder.mkdir(mode=0o700)
        (folder / "receipt.json").write_bytes(b'{"truncated":')
        raise RuntimeError("fixture interruption during legacy receipt write")

    monkeypatch.setattr(executor, "run", interrupt)
    with pytest.raises(RuntimeError, match="legacy receipt"):
        actions.perform_adaptive(flow, identity)

    assert actions.reconcile_adaptive(flow) == []
    status = flow.status()
    assert status["blocked"][identity] == "adaptive_attempt_requires_reconciliation"
    independent = Worker()
    assert flow.dispatch(
        "independent", independent, actor="fixture",
    )["status"] == "succeeded"
    assert independent.calls == ["independent"]
    assert not flow.history(identity)


@wal
def test_adaptive_entrypoint_inserts_step_and_keeps_independent_work_ready(tmp_path):
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.platform.workflow_entrypoints import (
        LEDGER_NAME,
        advance_adaptive_workflow,
    )
    flow = make(tmp_path, name=LEDGER_NAME)
    worker = Worker()
    flow.insert(step("qc"))
    flow.insert(step("inference", link("qc", "qc")))
    flow.hold("inference", "unresolved_design", evidence_sha256=digest("review"))
    planner = object.__new__(ScientificMethodPlanner)
    planner.methods = SimpleNamespace(root=tmp_path)
    planner.get = lambda session_id: {"session_id": session_id}
    first = advance_adaptive_workflow(planner, "study", ledger=flow.ledger, executor=worker,
                                      actor="tester", allow_data_execution=True)
    assert first["steps_dispatched"] == 1
    assert first["blocked"]["inference"] == "unresolved_design"
    evidence = flow.current_use("qc", "out", purpose="query")
    flow.insert(step("explore", link("qc", "qc")), evidence_sha256=evidence.sha256)
    final = advance_adaptive_workflow(planner, "study", ledger=flow.ledger, executor=worker,
                                      actor="tester", allow_data_execution=True)
    assert final["steps_dispatched"] == 1 and worker.calls == ["qc", "explore"]


@wal
def test_diagnostic_revisit_pins_refined_graph_and_retains_baseline(tmp_path):
    from devbench.engine.workflow_operations import OperationBlocked

    flow = make(tmp_path)
    worker = Worker()
    flow.insert(step("qc"))
    flow.insert(step("graph", link("qc", "qc"), kind="graph"))
    flow.insert(step("cluster", link("neighbors", "graph", kind="graph")))
    run(flow, worker, "qc", "graph", "cluster")
    qc = flow.current_use("qc", "out", purpose="cache")
    qc_bytes = worker.blobs[qc.version_id]
    old = flow.current_use("graph", "out", purpose="query")
    flow.insert(step("baseline", link("neighbors", old.version_id, kind="graph", immutable=True),
                     comparison=True))
    revised = step("graph", link("qc", "qc"), kind="graph").model_copy(
        update={"parameters_sha256": digest("refined parameters")})
    flow.revise(revised, reason="diagnostic_revisit", evidence_sha256=digest("diagnostic"))
    assert flow.current_use("qc", "out", purpose="cache") == qc
    assert set(flow.ready()) == {"baseline", "graph"}
    run(flow, worker, "baseline", "graph")
    with pytest.raises(ValueError, match="pinned"):
        flow.dispatch("cluster", Worker(stale_graph=old.graph_sha256), actor="tester")
    assert flow.current_use("qc", "out", purpose="cache") == qc
    flow.revise(step("cluster", link("neighbors", "graph", kind="graph")),
                reason="retry", evidence_sha256=digest("verified receipt mismatch"))
    run(flow, worker, "cluster")
    assert flow.current_use("qc", "out", purpose="cache") == qc
    assert flow.current_use("baseline", "out", purpose="cache").sha256 == digest("baseline-v1")
    assert worker.calls.count("qc") == 1 and worker.blobs[qc.version_id] == qc_bytes
    assert [record["status"] for record in flow.history("cluster")] == ["succeeded", "failed", "succeeded"]
    flow.retire_branch("graph", reason="scope_change", evidence_sha256=digest("scope correction"))
    with pytest.raises(OperationBlocked):
        flow.current_use("baseline", "out", purpose="query")
    assert flow.current_use("qc", "out", purpose="cache") == qc


@wal
def test_count_label_and_protected_admission_are_checked_before_dispatch(tmp_path):
    flow = make(tmp_path)
    flow.register_artifact(artifact("continuous", kind="counts", representation="embedding",
                                    measurement_history=("integrated",)))
    flow.insert(step("counts", link("input", "continuous", kind="counts", immutable=True),
                     representations=["COUNT-1"]))
    flow.register_artifact(artifact("labels", kind="membership_view", label_origin="rna_derived"))
    flow.insert(step("primary", link("labels", "labels", kind="membership_view", immutable=True),
                     regime="P-P", primary=True))
    flow.register_artifact(artifact("learned", learned_scope="full_cohort"))
    flow.insert(step("heldout", link("model", "learned", immutable=True), held_out=True))
    blocked = flow.status()["blocked"]
    assert "representation" in blocked["counts"]
    assert "RNA" in blocked["primary"]
    assert "full_cohort" in blocked["heldout"]
    assert flow.ready() == ()
    flow.register_artifact(artifact("integer-counts", kind="counts", representation="COUNT-1",
                                    measurement_history=("raw_counts",)))
    flow.insert(step("verified-counts", link("input", "integer-counts", kind="counts", immutable=True),
                     representations=["COUNT-1"]))
    run(flow, Worker(), "verified-counts")


@wal
def test_partition_hook_is_required_for_held_out_state(tmp_path):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    flow = make(tmp_path)
    flow.register_artifact(artifact("train-model", learned_scope="partition_only"))
    flow.insert(step("evaluate", link("model", "train-model", immutable=True), held_out=True))
    assert "partition" in flow.status()["blocked"]["evaluate"]
    seen = []

    def partition_check(node, inputs, basis):
        seen.append((node.step_id, inputs["model"].version_id))
        return inputs["model"].learned_scope == "partition_only"

    admitted = AdaptiveWorkflow(flow.ledger, "study", partition_check=partition_check)
    run(admitted, Worker(), "evaluate")
    assert seen and set(seen) == {("evaluate", "train-model")}


@wal
def test_long_wide_projection_retains_failed_omitted_and_retired_evidence(tmp_path):
    import json

    from devbench.analysis.evidence_access import EvidenceBroker
    from devbench.analysis.rna_investigation_projection import project_adaptive_dag
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from tests.test_investigation_invalidation import delivery_for

    flow = make(tmp_path)
    flow.insert(step("root"))
    flow.insert(step("failing", link("root", "root")))
    run(flow, Worker(), "root")
    failure = flow.dispatch("failing", Worker(fail={"failing"}), actor="tester")
    assert failure["status"] == "failed"
    for i in range(24):
        parent = "root" if i == 0 else f"deep-{i-1:02}"
        flow.insert(step(f"deep-{i:02}", link("prev", parent)))
        flow.insert(step(f"wide-{i:02}", link("root", "root")))
    flow.hold("wide-03", "unresolved_design", evidence_sha256=digest("counterevidence"))
    flow.retire_branch("wide-09", reason="irrelevant", evidence_sha256=digest("retirement evidence"))
    flow.declare_sweep("sweep", {"failing": None, "omitted": "budget", "wide-03": None})
    delivery = delivery_for(flow)
    page = project_adaptive_dag(flow, evidence_delivery=delivery, max_bytes=1700)
    items = list(page["entries"])
    assert page["next_cursor"] is not None
    from tests.test_investigation_publication_epochs import authorize
    flow.authority.cancel_execution("study", actor="tester")
    state = flow.authority.execution("study")
    flow.authority.resume_execution("study", expected_revision=state["revision"],
        binding_id=state["binding_id"], grant_id="grant-a", max_jobs=64, authorize=authorize)
    reopened = WorkflowOperationLedger(flow.ledger.path)
    flow = AdaptiveWorkflow(reopened, "study")
    broker = EvidenceBroker(reopened.scientific)
    delivery = broker.delivery(delivery.context_id, delivery.source_id)
    while page["next_cursor"] is not None:
        page = project_adaptive_dag(flow, evidence_delivery=delivery, max_bytes=1700, cursor=page["next_cursor"])
        assert len(json.dumps(page).encode()) <= 1700
        items.extend(page["entries"])
    assert len(items) == page["total_entries"]
    assert page["sweep_status_counts"] == {"failed": 1, "omitted": 1, "blocked": 1}
    assert any(row["entry_kind"] == "attempt" and row["status"] == "failed" for row in items)
    assert any(row["entry_kind"] == "tombstone" and row["step_id"] == "wide-09" for row in items)
    retired = project_adaptive_dag(flow, evidence_delivery=delivery, max_bytes=1700, branch_id="wide-09")
    assert {row["entry_kind"] for row in retired["entries"]} == {"step", "tombstone"}


def test_workflow_cli_requires_fresh_execution_authority(tmp_path):
    from devbench.platform.cli import run_workflow

    workspace = tmp_path / "missing"
    with pytest.raises(SystemExit) as error:
        run_workflow(["advance", "--workspace", str(workspace), "--session-id", "study",
                      "--actor", "tester", "--parameters", str(tmp_path / "parameters.json")])
    assert error.value.code == 2 and not workspace.exists()


@wal
def test_workflow_cli_reads_retained_lineage_and_refuses_non_oci_dispatch(tmp_path, capsys, monkeypatch):
    import json
    import sys

    from devbench.engine.methods import MethodService
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform import cli
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.langgraph_workflow import AdaptiveRNAWorkflow
    from devbench.platform.workflow_entrypoints import LEDGER_NAME
    from tests.test_exploratory_execution_profile import bound_step, register

    methods = MethodService(tmp_path)
    with methods._db() as db:
        methods._put(db, "method_plan", "study", {"session_id": "study"})
    flow = make(tmp_path, name=LEDGER_NAME)
    flow.insert(step("qc"))
    args = ["--workspace", str(tmp_path), "--session-id", "study"]
    monkeypatch.setattr(sys, "argv", ["devbench", "workflow", "status", *args])
    assert cli.main() == 0
    current = json.loads(capsys.readouterr().out)
    assert current["ready"] == ["qc"] and current["revision"] == flow.status()["revision"]
    assert not (tmp_path / "adaptive-oci").exists()

    params = tmp_path / "parameters.json"
    params.write_text("[{}]")
    execute = ["advance", *args, "--actor", "tester", "--parameters", str(params), "--allow-data-execution"]
    assert cli.run_workflow(execute) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["steps_dispatched"] == 0
    assert result["blocked"]["qc"] == "manual_local_oci_step_required"
    assert flow.authority.attempts("study") == []

    method = register(methods, tmp_path / "method", "bounded", (), ("out",),
                      "from pathlib import Path\nPath('/outputs/out.json').write_text('{}')\n")
    flow.insert(bound_step(method, step("bounded")))
    assert cli.run_workflow(execute) == 1
    assert "No exact frozen parameter document" in capsys.readouterr().err
    assert flow.authority.attempts("study") == []

    flow.manual_local_oci = True
    reopened = AdaptiveWorkflow(flow.ledger, "study")
    with flow._read_connection() as db:
        envelope = flow._envelope(flow._state(db), "bounded", "tester", db=db)
        assert envelope["manual_local_oci"] is True
        reopened._guard(db, envelope)

    params.write_text('[{"threshold":1}]')
    original = AdaptiveRNAWorkflow.advance

    def bind_before_dispatch(graph, *, max_steps):
        flow.bind_controller({"campaign_id": "controller", "max_decisions": 1})
        return original(graph, max_steps=max_steps)

    monkeypatch.setattr(AdaptiveRNAWorkflow, "advance", bind_before_dispatch)
    assert cli.run_workflow(execute) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["blocked"]["bounded"] == "manual_local_oci_requires_unbound_controller"
    assert result["steps_dispatched"] == 0
    with reopened._read_connection() as db:
        with pytest.raises(OperationBlocked, match="adaptive_dispatch_basis_invalidated"):
            reopened._guard(db, envelope)

    assert cli.run_workflow(execute) == 1
    assert "bound controller" in capsys.readouterr().err
    assert flow.authority.attempts("study") == []
