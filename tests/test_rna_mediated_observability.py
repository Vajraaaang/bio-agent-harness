"""Mediated observability controls with simulated provider usage only.

These fixtures exercise the real planner, ledger and observer. Their replaced
provider seam is not evidence of API calls, hosted delivery or scientific merit.
"""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
import hashlib
import json
from pathlib import Path

import pytest


def api():
    from devbench.analysis import rna_mediated_observability
    return rna_mediated_observability


def observed_case(tmp_path, monkeypatch, *, failure=None):
    from tests.test_rna_mediated_backend import mediated_generation_fixture
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    request, planner, selector, backend, authority, case, calls = mediated_generation_fixture(
        tmp_path, monkeypatch, failure=failure)
    identity = backend.runtime_identity()
    from devbench.analysis.workflow_operations import _digest, _json
    observer = LocalTraceObserver(tmp_path / "mediated-observer.sqlite",
        source_digest=identity["source_digest"], runtime_digest=identity["runtime_digest"],
        policy_digest=_digest(_json(backend.policy)))
    return request, planner, selector, backend, authority, case, calls, observer


def generate(tmp_path, values, *, allowed=True):
    from devbench.analysis.rna_scientific_trial import generate_question_workflow
    from tests.test_rna_scientific_trial import NOW
    request, planner, selector, backend, authority, case, _, observer = values
    return generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
        branch_id=case["branch"]["branch_id"], version_ids=[case["mediated_version_id"]],
        actor="participant", authority=authority, backend=backend, allow_model_execution=allowed,
        now=NOW, output=tmp_path / "observed-generated", trace_observer=observer, scientific_workflow=RecordingScientificRuntime())


def readonly_backend(backend):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.rna_mediated_backend import MediatedActionSelector, MediatedInvestigationBackend
    methods = MethodService(backend.methods.root, readonly=True)
    selector = MediatedActionSelector(backend.selector.limits, private_root=methods.root,
        transport=backend.selector.transport)
    return MediatedInvestigationBackend(methods, selector, policy=backend.policy,
        qualification=backend.qualification, ledger=WorkflowOperationLedger(backend.ledger.path, readonly=True))


def snapshot(values, *, campaign_id=None, workflow_id=None, backend=None):
    request, _, _, actual, _, _, calls, observer = values
    campaign_id = campaign_id or calls[0]["reservation"].campaign_id
    return api().read_mediated_rna_trace_snapshot(backend=backend or readonly_backend(actual),
        campaign_id=campaign_id, workflow_id=workflow_id or request["trial_id"],
        observer_path=observer.path, allow_evidence_read=True)


def test_unicode_request_uses_ledger_canonical_binding_without_changing_trace_v1(tmp_path):
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver, InvocationTraceReceipt
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.analysis.workflow_operations import ModelCallReservation, _digest, _json
    identities = dict(source_digest="a" * 64, runtime_digest="b" * 64, policy_digest="c" * 64)
    binding = {"source_digest": identities["source_digest"], "runtime_digest": identities["runtime_digest"],
        "purpose": "method_scientific_planning", "provider_request": {"input": "β cells — fixture canary"}}
    encoded = _json(binding)
    reservation = ModelCallReservation("fixture-campaign", "parent:planning", 0, "model:" + "d" * 64,
        "active", binding["purpose"], None, _digest(encoded), identities["policy_digest"], "e" * 64,
        encoded, _json({"calls": 1}))
    assert reservation.request_digest != trace_digest(binding)
    observer = LocalTraceObserver(tmp_path / "unicode.sqlite", **identities)
    reference = observer.prepare_invocation("fixture-workflow", binding, reservation)
    assert reference is not None, "UTF-8 ledger bindings must survive observer preparation"
    receipt = InvocationTraceReceipt(reference, binding_digest=reservation.request_digest,
        private_root=tmp_path, **identities)
    assert receipt.context["invocation_binding_digest"] == reservation.request_digest
    assert trace_digest({"β": "—"}) == hashlib.sha256(b'{"\\u03b2":"\\u2014"}').hexdigest()


def test_three_simulated_provider_stages_join_one_trial_root_and_actual_usage(tmp_path, monkeypatch):
    api()
    values = observed_case(tmp_path, monkeypatch)
    descriptor = generate(tmp_path, values)
    request, planner, _, backend, _, _, calls, observer = values
    result = snapshot(values)
    assert result["format"] == "devbench.rna-mediated-trace-snapshot/1"
    assert len(calls) == 3 and result["allocation"] == result["actual_children"] == 0
    events = result["events"]
    assert len({row["trace_id"] for row in events}) == 1
    roots = {row["span_id"] for row in events if row["parent_span_id"] is None}
    assert len(roots) == 1
    captures = [row for row in events if row["phase"] == "selection" and row["kind"] == "capture"]
    assert len(captures) == 3
    assert all(row["metrics"]["total_tokens"] == 70 for row in captures)
    assert all(row["metrics"]["provider_cost"]["value"] is None for row in captures)
    assert result["ledger_accounting"]["verified_calls"] == 3
    assert result["ledger_accounting"]["verified_spent_tokens"] == 210
    observed = observer.read_events(request["trial_id"])
    assert sum(row["kind"] == "capture" and row["phase"] == "selection" for row in observed) == 3
    assert observer.read_events(descriptor["planner_session_id"]) == []
    assert all("model_usage_ref" in row for row in planner.get(descriptor["planner_session_id"])["selection_history"])
    exported = json.dumps(result)
    for forbidden in ("publication_evidence", "Fixture output 4.0", "fixture-opaque-reference",
                      str(backend.methods.root), "Synthetic submission acknowledgement lost"):
        assert forbidden not in exported


@pytest.mark.parametrize("failure", ["incomplete", "unknown_usage", "ambiguous_submission"])
def test_failed_or_unknown_simulated_calls_preserve_status_and_reservations(tmp_path, monkeypatch, failure):
    api()
    values = observed_case(tmp_path, monkeypatch, failure=failure)
    with pytest.raises((ValueError, TimeoutError)):
        generate(tmp_path, values)
    result = snapshot(values)
    _, _, _, backend, _, _, calls, _ = values
    assert len(calls) == 1
    record = backend.ledger.get_model_reservation(calls[0]["reservation"])
    calls_in_trace = [row for row in result["events"] if row["phase"] == "selection"]
    assert calls_in_trace
    if failure == "incomplete":
        assert record["status"] == "failed"
        captures = [row for row in calls_in_trace if row["kind"] == "capture"]
        assert captures[-1]["source_status"] == "failed" and captures[-1]["metrics"]["total_tokens"] == 70
        assert result["ledger_accounting"]["verified_spent_tokens"] == 70
    else:
        assert record["status"] == "uncertain"
        assert result["ledger_accounting"]["unresolved_count"] == 1
        assert result["ledger_accounting"]["unknown_usage_count"] == 1
        assert all(row["metrics"]["total_tokens"] is None for row in calls_in_trace)
        assert result["complete"] is False
    assert all(row["metrics"]["provider_cost"]["value"] is None for row in calls_in_trace)


def test_denied_generation_has_no_dispatch_or_credential_lookup(tmp_path, monkeypatch):
    values = observed_case(tmp_path, monkeypatch)
    with pytest.raises(PermissionError):
        generate(tmp_path, values, allowed=False)
    request, _, _, _, _, _, calls, observer = values
    assert calls == []
    assert not any(row["kind"] == "dispatch" for row in observer.read_events(request["trial_id"]))


def test_optional_observer_failure_does_not_change_simulated_generation(tmp_path, monkeypatch):
    api()
    values = observed_case(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError("private-observer-canary")
    monkeypatch.setattr(values[-1], "_emit", fail)
    descriptor = generate(tmp_path, values)
    assert descriptor["planner_session_id"] and len(values[-2]) == 3


@pytest.mark.parametrize("change", ["workflow", "campaign", "writable", "json_backend", "runtime"])
def test_typed_snapshot_refuses_mismatched_custody(tmp_path, monkeypatch, change):
    api()
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    backend = readonly_backend(values[3])
    kwargs = {}
    if change == "workflow":
        kwargs["workflow_id"] = "unrelated-trial"
    elif change == "campaign":
        kwargs["campaign_id"] = "trial:" + "f" * 64
    elif change == "writable":
        backend = values[3]
    elif change == "json_backend":
        backend = backend.capabilities()
    else:
        from devbench.analysis import rna_trial_authority
        monkeypatch.setattr(rna_trial_authority, "trial_enforcement_runtime_digest", lambda: "f" * 64)
    with pytest.raises((ValueError, PermissionError, TypeError)):
        snapshot(values, backend=backend, **kwargs)


def test_retained_capture_corruption_is_refused_without_trusting_model_report(tmp_path, monkeypatch):
    api()
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    backend = values[3]
    row = backend.ledger.get_model_reservation(values[-2][0]["reservation"])
    artifact = backend.methods.artifact_path(row["capture"]["capture_ref"]["artifact_id"])
    raw = Path(artifact).read_bytes()
    Path(artifact).chmod(0o600)
    Path(artifact).write_bytes(raw.replace(b'"input_tokens": 50', b'"input_tokens": 51'))
    if Path(artifact).read_bytes() == raw:
        Path(artifact).write_bytes(raw + b" ")
    assert Path(artifact).read_bytes() != raw
    with pytest.raises((ValueError, PermissionError)):
        snapshot(values)


def test_explicit_read_authorization_precedes_store_access():
    with pytest.raises(PermissionError):
        api().read_mediated_rna_trace_snapshot(backend=None, campaign_id="unused", workflow_id="unused",
            allow_evidence_read=False)


@pytest.mark.parametrize("expired_after_connect", [False, True])
def test_actual_https_worker_observes_post_intent_only_at_submission_boundary(tmp_path, monkeypatch,
                                                                           expired_after_connect):
    """The actual worker uses inert HTTPS doubles; no provider or credentials are accessed."""
    from devbench.analysis import rna_mediated_backend as transport_api
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    from devbench.analysis.workflow_operations import ModelCallReservation, _digest, _json
    identities = dict(source_digest="a" * 64, runtime_digest="b" * 64, policy_digest="c" * 64)
    binding = {"source_digest": identities["source_digest"], "runtime_digest": identities["runtime_digest"],
               "purpose": "method_scientific_planning"}
    encoded = _json(binding)
    reservation = ModelCallReservation("fixture-campaign", "parent:planning", 0, "model:" + "d" * 64,
        "active", binding["purpose"], None, _digest(encoded), identities["policy_digest"], "e" * 64,
        encoded, _json({"calls": 1}))
    observer = LocalTraceObserver(tmp_path / "worker.sqlite", **identities)
    reference = observer.prepare_invocation("fixture-workflow", binding, reservation)
    worker_trace_context = dict(reference=reference, binding_digest=reservation.request_digest,
                                private_root=str(tmp_path), **identities)
    clock, requests = [100.0], []
    class Response:
        status = 200
        def getheader(self, name, default=None):
            return default
        def read1(self, size):
            return b""
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            assert not any(row["kind"] == "dispatch" for row in observer.read_events("fixture-workflow"))
            if expired_after_connect:
                clock[0] = 300.0
        def request(self, method, path, *, body, headers):
            events = observer.read_events("fixture-workflow")
            dispatches = [row for row in events if row["kind"] == "dispatch"]
            assert len(dispatches) == 1
            assert dispatches[0]["phase"] == "selection"
            assert dispatches[0]["source_status"] == "active"
            assert dispatches[0]["dispatch_status"] == "attempted"
            assert body == b'{"fixture":"inert"}'
            requests.append((method, path))
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(transport_api.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(transport_api, "verified_tls_context", lambda: None)
    monkeypatch.setattr(transport_api.time, "time", lambda: clock[0])
    monkeypatch.setattr(transport_api.time, "monotonic", lambda: clock[0])
    args = (b'{"fixture":"inert"}', "synthetic-private-token-canary", 50, 1024, 200, 150,
            worker_trace_context)
    if expired_after_connect:
        with pytest.raises(PermissionError):
            transport_api._https_worker(*args)
        assert requests == []
        assert not any(row["kind"] == "dispatch" for row in observer.read_events("fixture-workflow"))
    else:
        assert transport_api._https_worker(*args)["http_status"] == 200
        assert requests == [("POST", "/v1/responses")]
        assert not any(row["kind"] in {"completion", "failure"}
                       for row in observer.read_events("fixture-workflow"))
    exported = json.dumps(observer.read_events("fixture-workflow"))
    assert "synthetic-private-token-canary" not in exported and "inert" not in exported


def test_typed_mediated_projection_retains_every_observed_context(tmp_path, monkeypatch):
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    result = snapshot(values)
    projected = api().project_mediated_rna_trace(backend=readonly_backend(values[3]),
        campaign_id=values[-2][0]["reservation"].campaign_id, workflow_id=values[0]["trial_id"],
        observer_path=values[-1].path, allow_evidence_read=True,
        export_namespace=result["events"][0]["trace_id"], limits={})
    assert {row["span_id"] for row in result["events"]} == {row["id"] for row in projected["spans"]}
    assert projected["accounting"]["total_tokens"] == 210
    assert all(row["extra"]["metadata"]["capability"]["backend_kind"] == "openai-responses-mediated"
               for row in projected["spans"])


def test_typed_projection_refuses_a_forged_backend_before_evidence_io():
    import uuid
    with pytest.raises((TypeError, PermissionError)):
        api().project_mediated_rna_trace(backend={"backend": "openai-responses-mediated", "qualified": True},
            campaign_id="forged", workflow_id="forged", observer_path=Path("/not-an-observer"),
            allow_evidence_read=True, export_namespace=str(uuid.uuid4()), limits={})


def test_optional_sidecar_validation_rejects_unrelated_path_before_read(tmp_path, monkeypatch):
    from devbench.analysis import rna_trace_receipts
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    backend, observer = values[3], values[-1]
    backend.trace_observer = observer
    reservation = values[-2][0]["reservation"]
    opened = []
    def forbidden(*args, **kwargs):
        opened.append(True)
        raise AssertionError("Unrelated reference reached sidecar reader")
    monkeypatch.setattr(rna_trace_receipts, "InvocationTraceReceipt", forbidden)
    reference = dict(path=str(tmp_path / "unrelated-private-file"), sha256="a" * 64,
        binding_digest=reservation.request_digest, **observer.identities)
    assert api().validated_mediated_trace_receipt(backend, reservation, reference) is None
    assert opened == []


def test_prelaunch_save_failure_counts_reservation_without_claiming_provider_activation(tmp_path, monkeypatch):
    """The fixture sees zero submissions; retained uncertain accounting stays conservative."""
    values = observed_case(tmp_path, monkeypatch)
    planner, backend, calls, observer = values[1], values[3], values[-2], values[-1]
    reservations = []
    reserve, save = backend.ledger.reserve_model_call, planner._save
    def retain_reservation(*args, **kwargs):
        reservation = reserve(*args, **kwargs)
        reservations.append(reservation)
        return reservation
    def fail_before_activation(*args, **kwargs):
        if kwargs.get("action") == "planner_selection_started":
            raise OSError("synthetic-prelaunch-save-failure")
        return save(*args, **kwargs)
    monkeypatch.setattr(backend.ledger, "reserve_model_call", retain_reservation)
    monkeypatch.setattr(planner, "_save", fail_before_activation)
    with pytest.raises(OSError, match="synthetic-prelaunch-save-failure"):
        generate(tmp_path, values)
    assert calls == [] and len(reservations) == 1
    row = backend.ledger.get_model_reservation(reservations[0])
    assert row["status"] == "uncertain" and row["capture"] is None
    result = api().read_mediated_rna_trace_snapshot(backend=readonly_backend(backend),
        campaign_id=reservations[0].campaign_id, workflow_id=values[0]["trial_id"],
        observer_path=observer.path, allow_evidence_read=True)
    assert result["submission_accounting"] == dict(ledger_accounted_reservations=1,
        confirmed_provider_responses=0, unknown_submission_reservations=1, observed_post_attempts=0)
    assert result["ledger_accounting"]["verified_calls"] == 0
    assert result["ledger_accounting"]["unknown_usage_count"] == 1
    assert result["complete"] is False


def test_observer_does_not_repeat_mandatory_authorization_or_capture_validation(tmp_path, monkeypatch):
    observations = []
    for enabled in (False, True):
        with monkeypatch.context() as patch:
            directory = tmp_path / ("enabled" if enabled else "disabled")
            directory.mkdir()
            values = observed_case(directory, patch)
            backend, planner, calls = values[3], values[1], values[-2]
            counts = dict(authorization=0, capture_validation=0)
            authorize, verify = backend.require_admission, backend.verify_capture
            def counted_authorization(*args, **kwargs):
                counts["authorization"] += 1
                return authorize(*args, **kwargs)
            def counted_capture(*args, **kwargs):
                counts["capture_validation"] += 1
                return verify(*args, **kwargs)
            patch.setattr(backend, "require_admission", counted_authorization)
            patch.setattr(backend, "verify_capture", counted_capture)
            descriptor = generate(directory, values if enabled else (*values[:-1], None))
            assert len(calls) == 3
            assert all(row["status"] == "accepted" for row in
                       planner.get(descriptor["planner_session_id"])["selection_history"])
            campaign = backend.ledger.get_campaign(calls[0]["reservation"].campaign_id)
            assert campaign["verified_spent_tokens"] == 210 and campaign["verified_calls"] == 3
            observations.append(counts)
    # These are actual mandatory-call counts, not a claim about live latency.
    assert observations[0] == observations[1]
    assert observations[0]["authorization"] > 0 and observations[0]["capture_validation"] > 0


def test_generation_seal_failure_survives_three_successful_simulated_calls(tmp_path, monkeypatch):
    from devbench.analysis import rna_scientific_trial
    values = observed_case(tmp_path, monkeypatch)
    def failed_seal(*args, **kwargs):
        raise OSError("synthetic-seal-failure-after-three-responses")
    monkeypatch.setattr(rna_scientific_trial, "_seal", failed_seal)
    with pytest.raises(OSError, match="synthetic-seal-failure-after-three-responses"):
        generate(tmp_path, values)
    assert len(values[-2]) == 3
    result = snapshot(values)
    root = [row for row in result["events"] if row["parent_span_id"] is None][-1]
    # Generation failure belongs to its child; the outer workflow was not finalized.
    assert root["source_status"] == "active" and root["kind"] == "reservation"
    generation = [row for row in result["events"] if row["role"] == "coordinator"
                  and row["phase"] == "generate"]
    assert generation[-1]["source_status"] == "failed"
    assert result["ledger_accounting"]["verified_calls"] == 3
    assert result["ledger_accounting"]["verified_spent_tokens"] == 210
    assert result["complete"] is False
    assert "synthetic-seal-failure-after-three-responses" not in json.dumps(result)


@pytest.mark.parametrize("observation", ["absent", "wrong_trial_binding"])
def test_completed_call_ledger_cannot_supply_missing_exact_generation_terminal(tmp_path, monkeypatch, observation):
    from devbench.analysis import rna_trace_receipts
    values = observed_case(tmp_path, monkeypatch)
    if observation == "wrong_trial_binding":
        observe = rna_trace_receipts.observe_boundary
        def wrong_generation_binding(observer, workflow_id, phase, operation, **kwargs):
            if phase == "generate":
                kwargs["binding_digest"] = "f" * 64
            return observe(observer, workflow_id, phase, operation, **kwargs)
        monkeypatch.setattr(rna_trace_receipts, "observe_boundary", wrong_generation_binding)
    generate(tmp_path, values if observation != "absent" else (*values[:-1], None))
    result = api().read_mediated_rna_trace_snapshot(backend=readonly_backend(values[3]),
        campaign_id=values[-2][0]["reservation"].campaign_id, workflow_id=values[0]["trial_id"],
        observer_path=values[-1].path if observation != "absent" else None, allow_evidence_read=True)
    root = [row for row in result["events"] if row["parent_span_id"] is None][-1]
    # Missing generation evidence never changes the independently observed root.
    assert root["source_status"] == ("incomplete" if observation == "absent" else "active")
    if observation == "absent":
        assert root["clock"]["timing_basis"] == "unavailable"
    else:
        assert root["kind"] == "reservation"
    assert result["complete"] is False
    assert "result_missing" in result["gaps"]
    assert result["ledger_accounting"]["verified_calls"] == 3
    assert result["ledger_accounting"]["verified_spent_tokens"] == 210


@pytest.mark.parametrize("lock_at", ["initialization", "after_response"])
def test_worker_observer_database_locks_never_hold_received_raw_response(tmp_path, monkeypatch, lock_at):
    """Real SQLite locks and an inert HTTPS peer exercise the actual worker."""
    import sqlite3
    from devbench.analysis import rna_mediated_backend as transport_api
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    from devbench.analysis.workflow_operations import ModelCallReservation, _digest, _json
    identities = dict(source_digest="a" * 64, runtime_digest="b" * 64, policy_digest="c" * 64)
    binding = dict(source_digest=identities["source_digest"], runtime_digest=identities["runtime_digest"],
                   purpose="method_scientific_planning")
    encoded = _json(binding)
    reservation = ModelCallReservation("fixture-campaign", "parent:planning", 0, "model:" + "d" * 64,
        "active", binding["purpose"], None, _digest(encoded), identities["policy_digest"], "e" * 64,
        encoded, _json({"calls": 1}))
    observer = LocalTraceObserver(tmp_path / "locked-worker.sqlite", **identities)
    reference = observer.prepare_invocation("fixture-workflow", binding, reservation)
    context = dict(reference=reference, binding_digest=reservation.request_digest,
                   private_root=str(tmp_path), **identities)
    clock, finished, timeouts, post_response_queries = [100.0], [False], [], []
    connect = sqlite3.connect
    lock = connect(observer.path)
    if lock_at == "initialization":
        lock.execute("BEGIN EXCLUSIVE")
    def observed_connect(*args, **kwargs):
        timeout = kwargs.get("timeout")
        timeouts.append(timeout)
        if finished[0]:
            post_response_queries.append(True)
        if timeout != 0:
            # Deterministically model a lock consuming the call deadline;
            # never actually wait five seconds in this synthetic test.
            clock[0] = 200.0
            raise sqlite3.OperationalError("synthetic-observer-lock-deadline")
        return connect(*args, **kwargs)
    class Response:
        status = 200
        chunks = 0
        def getheader(self, name, default=None):
            return default
        def read1(self, size):
            self.chunks += 1
            if self.chunks == 1:
                return b"synthetic-inert-wire"
            if lock_at == "after_response":
                lock.execute("BEGIN EXCLUSIVE")
            finished[0] = True
            return b""
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    monkeypatch.setattr(transport_api.http.client, "HTTPSConnection", Connection)
    monkeypatch.setattr(transport_api, "verified_tls_context", lambda: None)
    monkeypatch.setattr(transport_api.time, "time", lambda: clock[0])
    monkeypatch.setattr(transport_api.time, "monotonic", lambda: clock[0])
    try:
        result = transport_api._https_worker(b"{}", "synthetic-inert-token", 2, 1024, 102, 102, context)
    finally:
        lock.rollback()
        lock.close()
    assert result["body"] == b"synthetic-inert-wire" and result["http_status"] == 200
    assert timeouts and set(timeouts) == {0}
    assert post_response_queries == []


def test_oversize_observer_is_refused_before_settings_queries_or_content_reads(tmp_path, monkeypatch):
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    oversized = tmp_path / "oversized-observer.sqlite"
    with oversized.open("wb") as handle:
        handle.truncate(17 * 1024 * 1024)
    opened = []
    def forbidden_open(*args, **kwargs):
        opened.append(True)
        raise AssertionError("Oversize observer reached settings queries")
    read_bytes = Path.read_bytes
    def bounded_read(path):
        if path == oversized:
            raise AssertionError("Oversize observer reached a content read")
        return read_bytes(path)
    monkeypatch.setattr(LocalTraceObserver, "open_existing", forbidden_open)
    monkeypatch.setattr(Path, "read_bytes", bounded_read)
    with pytest.raises(ValueError, match="aggregate read bound"):
        api().read_mediated_rna_trace_snapshot(backend=readonly_backend(values[3]),
            campaign_id=values[-2][0]["reservation"].campaign_id, workflow_id=values[0]["trial_id"],
            observer_path=oversized, allow_evidence_read=True)
    assert opened == []


def test_successful_generation_preserves_actual_active_workflow_root(tmp_path, monkeypatch):
    """Generation finishes a child phase; it does not finish the whole workflow."""
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    result = snapshot(values)
    assert len(values[-2]) == 3
    assert result["ledger_accounting"]["verified_spent_tokens"] == 210
    actual = [row for row in values[-1].read_events(values[0]["trial_id"])
              if row["parent_span_id"] is None]
    roots = [row for row in result["events"] if row["parent_span_id"] is None]
    assert actual[-1]["source_status"] == "active" and actual[-1]["kind"] == "reservation"
    assert roots[-1]["source_status"] == "active"
    assert [(row["event_id"], row["kind"], row["source_status"]) for row in roots] == [
        (row["event_id"], row["kind"], row["source_status"]) for row in actual]
    generate_events = [row for row in result["events"] if row["role"] == "coordinator"
                       and row["phase"] == "generate"]
    assert generate_events[-1]["source_status"] == "completed"
    assert result["complete"] is False


def test_explicit_observer_finalization_preserves_actual_completed_root_history(tmp_path, monkeypatch):
    """An actual observer finalizer can close this synthetic control's root."""
    values = observed_case(tmp_path, monkeypatch)
    generate(tmp_path, values)
    observer, workflow_id = values[-1], values[0]["trial_id"]
    interval = observer.begin_interval(workflow_id, "synthetic-finalizer-control")
    assert interval is not None
    observer.finish_interval(workflow_id, interval, "completed")
    actual = [row for row in observer.read_events(workflow_id) if row["parent_span_id"] is None]
    assert actual[-1]["source_status"] == "completed" and actual[-1]["kind"] == "completion"
    result = snapshot(values)
    roots = [row for row in result["events"] if row["parent_span_id"] is None]
    assert [(row["event_id"], row["kind"], row["source_status"]) for row in roots] == [
        (row["event_id"], row["kind"], row["source_status"]) for row in actual]
    assert roots[-1]["source_status"] == "completed"
    assert len(values[-2]) == 3
    assert result["ledger_accounting"]["verified_spent_tokens"] == 210
