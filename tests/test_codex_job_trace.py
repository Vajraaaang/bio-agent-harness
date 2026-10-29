"""Synthetic job observations retain lifecycle without inventing provider usage."""
from copy import deepcopy
import importlib
import importlib.util
from types import SimpleNamespace
import json
import uuid

import pytest

from devbench.analysis.rna_trace_contract import canonical_bytes, trace_digest
from devbench.analysis.rna_trace_projection import project_rna_trace
from devbench.analysis.rna_trace_receipts import LocalTraceObserver


IDENTITIES = dict(source_digest="1" * 64, runtime_digest="2" * 64,
                  policy_digest="3" * 64)
NAMESPACE = str(uuid.UUID(int=100))


def helper():
    assert importlib.util.find_spec("devbench.analysis.codex_job_trace") is not None, \
        "The generation job observer integration is missing"
    return importlib.import_module("devbench.analysis.codex_job_trace")


def reservation():
    binding = dict(**{key: IDENTITIES[key] for key in ("source_digest", "runtime_digest")},
        session_id="synthetic-session", invocation_id="synthetic-invocation",
        backend_digest="4" * 64, prompt_digest="5" * 64, schema_digest="6" * 64,
        context_digest="7" * 64, purpose="method_scientific_planning")
    return SimpleNamespace(request=binding, request_digest=trace_digest(binding),
        ref="model:" + "8" * 64, policy_digest=IDENTITIES["policy_digest"], attempt=0)


def capture(*, status="completed", usage=None):
    admitted = reservation()
    value = dict(format="devbench.codex-job-capture/1", reservation_ref=admitted.ref,
        request_digest=admitted.request_digest,
        capture_ref=dict(artifact_id="synthetic-capture", artifact_type="rna_investigation_capture",
                         sha256="9" * 64, size_bytes=1024),
        launch_intent=True, launch_confirmed=True, status=status, process_reaped=status != "uncertain",
        job_count=1, elapsed_seconds=0.5, native_session_ids=["synthetic-native-id"],
        provenance={key: admitted.request[key] for key in ("session_id", "invocation_id",
            "backend_digest", "runtime_digest", "prompt_digest", "schema_digest", "context_digest")},
        response_sha256="a" * 64)
    if usage is not None:
        value["usage"] = usage
    return value


def root_event():
    root = str(uuid.UUID(int=1))
    return dict(format="devbench.rna-trace-event/1", event_id=str(uuid.UUID(int=2)),
        trace_id=root, span_id=root, parent_span_id=None, sequence=0, attempt=1,
        role="coordinator", phase="workflow", kind="completion", source_status="completed",
        dispatch_status="not_dispatched", capture_status="unavailable",
        clock=dict(utc=None, monotonic_ns=None, process_alias=str(uuid.UUID(int=3)), timing_basis="unavailable"),
        bindings=dict(workflow="a" * 64),
        metrics=dict(input_tokens=None, output_tokens=None, total_tokens=None,
            elapsed_seconds=None, peak_rss_mb=None, usage_basis="unavailable",
            provider_cost=dict(value=None, currency=None, basis="unavailable", source_alias=None)),
        reason_code="none", origin="retained_authoritative_evidence")


def job_snapshot(*, reported=None, unknown=1, complete=True):
    return dict(format="devbench.rna-trace-snapshot/2", accounting_kind="generation_jobs",
        workflow_binding="a" * 64, events=[root_event()], allocation=0, actual_children=0,
        complete=complete, gaps=[],
        capability=dict(backend_kind="codex-chatgpt-cli", qualification_status="unqualified", admitted=True),
        ledger_accounting=dict(format="devbench.codex-job-trace-accounting/1", policy_kind="generation_jobs",
            verified_jobs=1, outstanding_reserved_jobs=0, unresolved_count=0,
            qualification="complete", reported_token_subtotal=reported, reported_usage_jobs=int(reported is not None),
            unknown_usage_jobs=unknown, verified_seconds=0.5))


def test_prepare_launch_complete_preserve_one_job_without_fake_phase_calls(tmp_path):
    api = helper()
    observer = LocalTraceObserver(tmp_path / "observer.sqlite", **IDENTITIES)
    admitted = reservation()
    reference = api.prepare_job_trace(observer, "workflow", admitted.request, admitted)
    receipt = api.job_trace_receipt(reference, reservation=admitted, private_root=tmp_path)
    api.observe_job_launch(receipt)
    api.complete_job_trace(observer, reference, reservation=admitted, capture=capture(), status="completed")
    events = observer.read_events("workflow")
    assert [row["kind"] for row in events] == ["reservation", "reservation", "dispatch", "capture", "completion"]
    assert {row["phase"] for row in events} == {"workflow", "method_scientific_planning"}
    assert events[-1]["source_status"] == "completed"
    assert all(row["metrics"]["total_tokens"] is None for row in events)
    assert observer.failure_count == 0


def test_reported_tokens_never_become_preventive_or_complete_accounting(tmp_path):
    api = helper()
    observer = LocalTraceObserver(tmp_path / "observer.sqlite", **IDENTITIES)
    admitted = reservation()
    reference = api.prepare_job_trace(observer, "workflow", admitted.request, admitted)
    api.complete_job_trace(observer, reference, reservation=admitted,
        capture=capture(usage=dict(input_tokens=5, output_tokens=3, total_tokens=8)), status="completed")
    events = observer.read_events("workflow")
    assert events[-1]["metrics"]["total_tokens"] is None
    assert all(row["metrics"]["usage_basis"] != "verified_complete" for row in events)
    source = job_snapshot(reported=8, unknown=0)
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["format"] == "devbench.rna-trace-projection/2"
    assert result["complete"] is True
    assert result["accounting"]["observed_token_subtotal"] == 8
    assert result["accounting"]["total_tokens"] is None
    assert result["accounting"]["usage_basis"] == "reported_optional"
    assert result["accounting"]["internal_model_requests"] is None
    assert result["accounting"]["credit_usage"] is None
    assert result["accounting"]["provider_cost_total"] is None


def test_known_prelaunch_failure_never_becomes_an_observed_dispatch(tmp_path):
    api = helper()
    observer = LocalTraceObserver(tmp_path / "observer.sqlite", **IDENTITIES)
    admitted = reservation()
    reference = api.prepare_job_trace(observer, "workflow", admitted.request, admitted)
    failed = capture(status="not_started")
    failed.update(launch_confirmed=False, process_reaped=True, native_session_ids=[])
    api.complete_job_trace(observer, reference, reservation=admitted, capture=failed, status="not_started")
    events = observer.read_events("workflow")
    assert not any(row["dispatch_status"] == "attempted" for row in events)
    assert events[-1]["source_status"] == "failed"
    assert events[-1]["capture_status"] == "verified"
    assert events[-1]["dispatch_status"] == "not_dispatched"


def test_completed_job_trace_can_have_unknown_tokens_and_native_qualification():
    result = project_rna_trace(job_snapshot(), export_namespace=NAMESPACE, limits={})
    assert result["complete"] is True
    assert result["accounting"]["job_lifecycle_complete"] is True
    assert result["accounting"]["verified_jobs"] == 1
    assert result["accounting"]["unknown_usage_jobs"] == 1
    assert result["accounting"]["total_tokens"] is None
    assert result["accounting"]["verified_calls"] is None
    assert result["spans"][0]["extra"]["metadata"]["capability"]["qualification_status"] == "unqualified"


@pytest.mark.parametrize("changed", ["format", "policy_kind", "extra", "legacy"])
def test_job_accounting_has_an_exact_new_discriminator(changed):
    source = job_snapshot()
    if changed == "format":
        source["format"] = "devbench.rna-trace-snapshot/3"
    elif changed == "policy_kind":
        source["ledger_accounting"]["policy_kind"] = "hard_total_tokens"
    elif changed == "extra":
        source["ledger_accounting"]["native_auth"] = "PRIVACY_CANARY"
    else:
        source["format"] = "devbench.rna-trace-snapshot/1"
    with pytest.raises(ValueError):
        project_rna_trace(source, export_namespace=NAMESPACE, limits={})


def test_uncertain_job_lifecycle_is_retained():
    source = job_snapshot(complete=False)
    source["events"][0]["source_status"] = "uncertain"
    source["ledger_accounting"].update(unresolved_count=1, qualification="incomplete")
    result = project_rna_trace(source, export_namespace=NAMESPACE, limits={})
    assert result["complete"] is False
    assert result["accounting"]["job_lifecycle_complete"] is False
    assert result["spans"][0]["extra"]["metadata"]["source_status"] == "uncertain"


def test_prepare_hosted_observation_preserves_dispatch_with_disabled_authority(tmp_path):
    api = helper()
    observer = LocalTraceObserver(tmp_path / "observer.sqlite", **IDENTITIES)
    admitted = reservation()
    reference = api.prepare_job_trace(observer, "workflow", admitted.request, admitted)
    api.observe_job_launch(api.job_trace_receipt(reference, reservation=admitted, private_root=tmp_path))
    before = canonical_bytes(observer.read_events("workflow"))
    prepared = api.prepare_job_observation(observer=observer, workflow_id="workflow")
    assert prepared["authorize_upload"] is False
    assert prepared["authorize_remote_read"] is False
    assert prepared["blockers"] == []
    assert prepared["profile"]["format"] == "devbench.rna-context-observation-profile/5"
    assert prepared["profile"]["dispatch_event_count"] == 1
    assert prepared["dispatch_event_count"] == 1
    assert prepared["source_event_count"] == 3
    assert canonical_bytes(observer.read_events("workflow")) == before
    assert "synthetic-native-id" not in canonical_bytes(prepared).decode()


def test_observer_failure_does_not_change_execution():
    api = helper()
    class Broken:
        def prepare_invocation(self, *args, **kwargs):
            raise OSError("PRIVACY_CANARY")
        def complete_invocation(self, *args, **kwargs):
            raise OSError("PRIVACY_CANARY")
    admitted = reservation()
    assert api.prepare_job_trace(Broken(), "workflow", admitted.request, admitted) is None
    assert api.complete_job_trace(Broken(), None, reservation=admitted, capture=None, status="uncertain") is None


def test_job_snapshot_authorization_precedes_all_source_access():
    api = helper()
    with pytest.raises(PermissionError):
        api.read_codex_job_trace_snapshot(backend=None, campaign_id="unread", workflow_id="unread",
            allow_evidence_read=False)


def test_job_accounting_counts_optional_reports_without_fabricating_missing_usage():
    api = helper()
    campaign = dict(policy=dict(kind="generation_jobs"), verified_jobs=2,
        outstanding_reserved_jobs=1, unresolved_count=1, qualification="incomplete", verified_seconds=1.0)
    rows = [dict(capture=capture(), status="completed"),
        dict(capture=capture(usage=dict(input_tokens=5, output_tokens=3)), status="completed"),
        dict(capture=None, status="uncertain")]
    result = api.job_trace_accounting(campaign, rows)
    assert result["verified_jobs"] == 2
    assert result["outstanding_reserved_jobs"] == 1
    assert result["reported_token_subtotal"] == 8
    assert result["reported_usage_jobs"] == 1
    assert result["unknown_usage_jobs"] == 2
    assert api.job_trace_metrics(rows[1]["capture"])["usage_basis"] == "observed_partial"
    assert api.job_trace_metrics(rows[1]["capture"])["total_tokens"] is None


def test_private_usage_fields_are_not_exported():
    api = helper()
    value = capture(usage=dict(input_tokens=5, output_tokens=3,
                              prompt="PRIVACY_CANARY", email="private@example.test"))
    projected = api.job_trace_metrics(value)
    assert projected["input_tokens"] == 5
    assert b"PRIVACY_CANARY" not in canonical_bytes(projected)
    assert b"private@example.test" not in canonical_bytes(projected)


def test_generation_job_lifecycle_cannot_claim_complete_while_unresolved():
    source = job_snapshot()
    source["ledger_accounting"].update(outstanding_reserved_jobs=1, unknown_usage_jobs=2)
    with pytest.raises(ValueError, match="Unresolved"):
        project_rna_trace(source, export_namespace=NAMESPACE, limits={})


@pytest.mark.parametrize("lose_dispatch", [False, True])
def test_actual_offline_selector_child_has_same_workflow_capture_and_readonly_trace(tmp_path, monkeypatch, lose_dispatch):
    """Real bounded child and stores; authority is an explicit offline test stub."""
    api = helper()
    from devbench.analysis import codex_signedin, rna_trial_authority
    from devbench.analysis.methods import MethodService
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    monkeypatch.setattr(rna_trial_authority, "trial_enforcement_source_digest", lambda: "1" * 64)
    monkeypatch.setattr(rna_trial_authority, "trial_enforcement_runtime_digest", lambda: "2" * 64)
    stage = "method_scientific_planning"
    schema = dict(type="object", required=["ok"], properties=dict(ok=dict(const=True)), additionalProperties=False)
    protocol = [dict(type="thread.started", thread_id="synthetic-actual-thread"), dict(type="turn.started"),
        dict(type="item.completed", item=dict(type="agent_message", text='{"ok":true}')),
        dict(type="turn.completed", model="fixture-model")]
    raw = b"".join(json.dumps(row).encode() + b"\n" for row in protocol)
    methods = MethodService(tmp_path / "private")
    ledger = WorkflowOperationLedger(methods.root / "ledger.sqlite")
    selector = codex_signedin.SignedInCodexActionSelector(requested_model="fixture-model",
        private_root=methods.root, _test_process=codex_signedin._OfflineProcessBinding(raw))
    limits = dict(jobs=1, seconds=2, context_bytes=48000, output_bytes=100000)
    policy = dict(format="devbench.model-job-budget-policy/1", kind="generation_jobs", max_jobs=3,
        max_concurrent_jobs=1, max_total_seconds=30, max_context_bytes=48000, max_output_bytes=100000,
        max_children=0, reconciliation=dict(jobs=3, seconds=24, context_bytes=48000))
    backend = codex_signedin.SignedInCodexInvestigationBackend(methods, selector, policy=policy, ledger=ledger)
    workflow_id, campaign_id = "synthetic-workflow", "trial:" + "a" * 64
    campaign = ledger.open_campaign(campaign_id, session_id="parent", policy=policy, capability=backend.capabilities(),
        identity=dict(trial_id=workflow_id, trial_purpose="workflow_generation", trial_binding_sha256="a" * 64,
            protected_parent_allocations={stage: limits}), backend=backend)
    context = dict(decision_kind=stage)
    monkeypatch.setattr(codex_signedin, "request_material", lambda context: dict(prompt="synthetic fixed prompt", schema=schema))
    request = backend.parent_selection_binding(selector, context, parent_session_id="parent",
        session_id="synthetic-session", invocation_id="synthetic-invocation")
    request.update(format="devbench.model-call-request/1", plan_hash="b" * 64)
    admitted = ledger.reserve_model_call(campaign_id, "parent:" + stage, 0, request, limits=limits, purpose=stage)
    observer = LocalTraceObserver(methods.root / "observer.sqlite", source_digest="1" * 64,
        runtime_digest="2" * 64, policy_digest=campaign["policy_digest"])
    if lose_dispatch:
        original_emit = LocalTraceObserver._emit
        def lose_one_receipt(self, context, workflow_digest, kind, *args, **kwargs):
            if kind == "dispatch":
                raise OSError("synthetic observer write loss")
            return original_emit(self, context, workflow_digest, kind, *args, **kwargs)
        monkeypatch.setattr(LocalTraceObserver, "_emit", lose_one_receipt)
    reference = api.prepare_job_trace(observer, workflow_id, request, admitted)
    ledger.activate_model_call(admitted)
    # Real grants and native credentials are deliberately absent in this fixture.
    monkeypatch.setattr(backend, "_authorize_dispatch", lambda reservation: None)
    output = methods.root / "capture"
    output.mkdir()
    selected = selector.select(context, output, timeout_seconds=2, allow_model_execution=True,
        protected_model_gate=backend.model_dispatch_gate(admitted), trace_sidecar=reference)
    ref, receipt = backend.verify_selection_capture(output, bindings=request, reservation=admitted)
    ledger.settle_model_call(admitted, capture=ref, verify_capture=backend.verify_capture)
    api.complete_job_trace(observer, reference, reservation=admitted, capture=receipt, status=receipt["status"])
    assert selected.action == {"ok": True}
    actual_events = observer.read_events(workflow_id)
    assert sum(row["kind"] == "dispatch" for row in actual_events) == (0 if lose_dispatch else 1)
    assert receipt["native_session_ids"] == ["synthetic-actual-thread"]
    assert receipt["status"] == "completed" and "usage" not in receipt
    backend.methods = MethodService(methods.root, readonly=True)
    backend.ledger = WorkflowOperationLedger(ledger.path, readonly=True)
    snapshot = api.read_codex_job_trace_snapshot(backend=backend, campaign_id=campaign_id,
        workflow_id=workflow_id, observer_path=observer.path, allow_evidence_read=True)
    assert snapshot["observer_events"] == actual_events
    projection = project_rna_trace(snapshot, export_namespace=actual_events[0]["trace_id"], limits={})
    assert projection["complete"] is (not lose_dispatch)
    assert projection["accounting"]["verified_jobs"] == 1
    assert projection["accounting"]["job_lifecycle_complete"] is True
    if lose_dispatch:
        assert "observer_failure" in projection["local_evidence"]["gaps"]
    assert projection["accounting"]["total_tokens"] is None
    assert projection["accounting"]["internal_model_requests"] is None
    assert all(row["inputs"] == row["outputs"] == {} for row in projection["spans"])
    assert b"synthetic-actual-thread" not in canonical_bytes(projection["spans"])
    assert b"synthetic fixed prompt" not in canonical_bytes(projection["spans"])

