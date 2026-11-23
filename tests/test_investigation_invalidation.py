"""Synthetic tests of durable adaptive computation and claim use."""
import hashlib

import pytest

from devbench.engine.workflow_operations import OperationBlocked, WorkflowOperationLedger
from tests.test_investigation_publication_epochs import authorize
from tests.test_scientific_authority_migration import _binding

wal = pytest.mark.wal


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def make(tmp_path, *, name="operations.sqlite"):
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    ledger = WorkflowOperationLedger(tmp_path / name, scientific=True)
    authority = ledger.scientific
    authority.commit("study", expected_revision=0, record=_binding())
    authority.commit("study", expected_revision=1, record={
        "kind": "grant", "record_id": "grant-a", "binding_id": "binding-a", "grant_sha256": digest("grant")})
    authority.activate_execution("study", expected_revision=2, binding_id="binding-a",
                                 grant_id="grant-a", max_jobs=64, authorize=authorize)
    return AdaptiveWorkflow(ledger, "study")


def artifact(name, *, kind="table", representation=None, **fields):
    from devbench.platform.adaptive_workflow import ArtifactVersion
    return ArtifactVersion(version_id=name, sha256=digest(name), kind=kind,
                           representation=representation or kind, biological_unit="donor",
                           unit_membership_sha256=digest("units"),
                           graph_sha256=digest(name + "-graph") if kind == "graph" else None,
                           **fields)


def link(name, source, *, kind="table", output="out", use="compute", immutable=False):
    from devbench.platform.adaptive_workflow import InputLink
    return InputLink(port={"name": name, "artifact_kind": kind}, source_id=source, use=use,
                     source_port=None if immutable else output)


def step(name, *inputs, kind="table", representations=None, role="compute", basis=(), **fields):
    from devbench.platform.adaptive_workflow import AdaptiveStep
    return AdaptiveStep(step_id=name, branch_id=fields.pop("branch_id", name), operation_id="synthetic",
        profile={"profile_id": "fixture", "version": 1, "operations": [
            {"operation_id": "synthetic", "output_kinds": [kind], "max_memory_mb": 256, "timeout_seconds": 30}],
            "input_representations": representations or [item.port.artifact_kind for item in inputs] or ["table"],
            "biological_units": ["donor"], "permitted_claims": ["checked_observation", "exploratory_finding"]},
        environment_sha256=digest("runtime"), parameters_sha256=digest(name), inputs=inputs,
        outputs=({"name": "out", "artifact_kind": kind},), role=role, basis=basis, **fields)


class Worker:
    backend_id = "synthetic-worker"

    def __init__(self, *, fail=(), stale_graph=None):
        self.calls = []
        self.blobs = {}
        self.fail = fail
        self.stale_graph = stale_graph

    def run(self, envelope):
        from devbench.platform.adaptive_workflow import StepReceipt, request_digest
        node = envelope["step"]
        name = node["step_id"]
        self.calls.append(name)
        generation = envelope["generation"]
        job = f"job-{name}-{generation}"
        consumed = envelope["pins"]
        if self.stale_graph is not None and consumed:
            consumed = [{**pin, "graph_sha256": self.stale_graph} for pin in consumed]
        failed = name in self.fail
        version = f"{name}-v{generation}"
        if not failed:
            self.blobs[version] = version.encode()
        return StepReceipt(job_id=job, request_sha256=request_digest(envelope), consumed=consumed,
            status="failed" if failed else "succeeded",
            reason="synthetic_failure" if failed else None,
            outputs={} if failed else {"out": artifact(version, kind=node["outputs"][0]["artifact_kind"])})

    def verify(self, envelope, receipt):
        return True  # Trusted synthetic test adapter. No real provider.


def run(flow, worker, *names):
    for name in names:
        assert flow.dispatch(name, worker, actor="tester")["status"] == "succeeded"


@wal
def test_donor_and_annotation_corrections_invalidate_transitive_uses(tmp_path):
    flow = make(tmp_path)
    worker = Worker()
    flow.record_basis("donors", "fact", digest("old donors"))
    flow.record_basis("labels", "membership", digest("old annotation"))
    for node in (step("qc"), step("aggregate", link("qc", "qc"), basis=({"basis_id": "donors"}, {"basis_id": "labels"})),
                 step("design", link("groups", "aggregate")), step("fit", link("design", "design")),
                 step("plot", link("fit", "fit")), step("review", link("fit", "fit", use="claim"), role="claim")):
        flow.insert(node)
    run(flow, worker, "qc", "aggregate", "design", "fit", "plot", "review")
    before = flow.current_use("qc", "out", purpose="cache")
    flow.record_basis("donors", "fact", digest("corrected donors"))
    flow.record_basis("labels", "membership", digest("corrected annotation"))
    assert flow.ready() == ("aggregate",)
    assert flow.current_use("qc", "out", purpose="cache") == before
    for node in ("aggregate", "design", "fit", "plot", "review"):
        for use in ("cache", "query", "export", "replay-current-use"):
            with pytest.raises(OperationBlocked):
                flow.current_use(node, "out", purpose=use)
    assert flow.history("fit")[0]["status"] == "succeeded"
    assert flow.history("fit")[0]["outputs"]["out"]["sha256"] == digest("fit-v1")
    run(flow, worker, "aggregate", "design", "fit", "plot", "review")
    assert flow.current_use("fit", "out", purpose="cache").sha256 != digest("fit-v1")
    assert worker.calls.count("qc") == 1


@wal
def test_claim_only_contradiction_leaves_computational_cache_valid(tmp_path):
    flow = make(tmp_path)
    worker = Worker()
    flow.record_basis("basis", "review_basis", digest("old"))
    flow.insert(step("fit", basis=({"basis_id": "basis", "use": "claim"},)))
    flow.insert(step("review", link("fit", "fit", use="claim"), role="claim"))
    run(flow, worker, "fit", "review")
    previous = flow.current_use("fit", "out", purpose="cache")
    flow.record_basis("basis", "review_basis", digest("contradiction"))
    assert flow.current_use("fit", "out", purpose="cache") == previous
    assert flow.ready() == ("review",)
    with pytest.raises(OperationBlocked, match="claim"):
        flow.current_use("fit", "out", purpose="export", use="claim")


def test_adaptive_shape_and_cycle_validation():
    from devbench.platform.adaptive_workflow import validate_dag
    with pytest.raises(ValueError, match="cycle"):
        validate_dag({"a": step("a", link("source", "b")), "b": step("b", link("source", "a"))})


def delivery_for(flow, *, allow_statistics=False):
    from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker

    broker = EvidenceBroker(flow.authority)
    policy = AccessPolicy("adaptive-read", 1, allow_outcomes=True, allow_statistics=allow_statistics)
    broker.register_policy(policy)
    broker.register_context("adaptive-context", principal_id="scientist", lineage_id="study",
                            role="scientist", phase="readback", policy_sha256=policy.sha256())
    broker.register_source("adaptive-source", content_sha256=digest("source"), roots=(digest("root"),),
                           classification="structural")
    return broker.delivery("adaptive-context", "adaptive-source")


@wal
def test_ordinary_view_and_confirmation_preserve_fit_but_stale_export_is_fenced(tmp_path):
    flow = make(tmp_path)
    flow.record_basis("design", "fact", digest("old"))
    flow.insert(step("fit", basis=({"basis_id": "design"},)))
    run(flow, Worker(), "fit")
    before = flow.current_use("fit", "out", purpose="cache")
    delivery = delivery_for(flow, allow_statistics=True)
    broker = delivery.broker
    raw = flow.export_current("fit", "out", load=lambda _: b"fit-v1", evidence_delivery=delivery)
    assert raw == b"fit-v1"
    exposure = broker.exposure(delivery.context_id, delivery.source_id)
    rid = exposure["reservation_ids"][0]
    broker.confirm_receipt(rid, receipt_sha256=digest("confirmed"), confirmed_bytes=len(raw),
                           verify_receipt=lambda _: True)
    assert flow.current_use("fit", "out", purpose="cache") == before

    def change_while_reading(_):
        flow.record_basis("design", "fact", digest("new"))
        return b"fit-v1"

    with pytest.raises(OperationBlocked, match="current_use|invalidated"):
        flow.export_current("fit", "out", load=change_while_reading, evidence_delivery=delivery)
    assert broker.exposure(delivery.context_id, delivery.source_id)["possible_reservations"] == 1


@wal
def test_correction_during_execution_keeps_late_output_audit_only(tmp_path):
    flow = make(tmp_path)
    flow.record_basis("donors", "fact", digest("old"))
    flow.insert(step("fit", basis=({"basis_id": "donors"},)))

    class ChangingWorker(Worker):
        def run(self, envelope):
            receipt = super().run(envelope)
            flow.record_basis("donors", "fact", digest("new"))
            return receipt

    result = flow.dispatch("fit", ChangingWorker(), actor="tester")
    assert result["status"] == "audit_only"
    assert flow.ready() == ("fit",)
    run(flow, Worker(), "fit")
    assert flow.current_use("fit", "out", purpose="replay-current-use").sha256 == digest("fit-v2")


@wal
def test_current_query_rechecks_invalidation_before_reserving_disclosure(tmp_path):
    from devbench.platform.adaptive_workflow import StepReceipt

    flow = make(tmp_path)
    flow.record_basis("input", "source", digest("old"))
    flow.insert(step("result", basis=({"basis_id": "input"},)))
    raw = b'{"targets": []}'

    class JSONWorker(Worker):
        def run(self, envelope):
            receipt = super().run(envelope)
            result = receipt.model_dump()
            result["outputs"]["out"]["sha256"] = hashlib.sha256(raw).hexdigest()
            return StepReceipt.model_validate(result)

    run(flow, JSONWorker(), "result")
    delivery = delivery_for(flow)
    page = flow.query_current("result", "out", {"mode": "annotations"}, load=lambda _: raw,
                              evidence_delivery=delivery)
    assert delivery.broker.verify_query_page(page)

    def change_while_reading(_):
        flow.record_basis("input", "source", digest("new"))
        return raw

    with pytest.raises(OperationBlocked):
        flow.query_current("result", "out", {"mode": "annotations"}, load=change_while_reading,
                           evidence_delivery=delivery)
    assert delivery.broker.exposure(delivery.context_id, delivery.source_id)["possible_reservations"] == 1
