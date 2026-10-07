"""Live software export qualification; no provider or actual network."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from devbench.study.rna_langsmith_live import validate_live_session, LiveTraceExporter, sanitize_live_event, ReceiptOperationBridge, SanitizedOperationFile
from devbench.study.langsmith_outbox import ExportOutbox
from tests.test_rna_trace_contract import event


def session():
    return dict(format="devbench.rna-live-trace-session/1", workflow_alias=str(uuid4()),
                workflow_binding_digest="d"*64,
                data_scope="synthetic", source_digest="a"*64, runtime_digest="b"*64,
                policy_digest="c"*64, endpoint="https://api.smith.langchain.com",
                project="pilot", workspace_id=None, redaction_version="rna-allowlist/1",
                event_schema="devbench.rna-trace-event/1", allowed_event_fields=["event_id", "trace_id", "span_id", "parent_span_id", "sequence", "attempt", "role", "phase", "kind", "source_status", "dispatch_status", "capture_status", "clock", "metrics", "reason_code", "origin"],
                max_spans=2000, max_bytes=8_000_000, max_delivery_attempts=3,
                expires_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(),
                authorize_upload=True)


def live_fixture(tmp_path,**session_changes):
    manifest = session()
    manifest.update(session_changes)
    root = str(uuid4())
    def receipt(identity, parent, sequence, status, phase="invocation"):
        return event(event_id=str(uuid4()), trace_id=root, span_id=identity,
                     parent_span_id=parent, sequence=sequence,
                     role="coordinator" if parent is None else "investigator", phase=phase,
                     kind="reservation" if sequence == 0 else "completion", source_status=status,
                     dispatch_status="prepared" if sequence == 0 else "attempted",
                     clock={"utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "monotonic_ns": time.monotonic_ns(), "process_alias": str(uuid4()), "timing_basis": "observed"},
                     bindings={"workflow": "d"*64, "source": manifest["source_digest"],
                               "runtime": manifest["runtime_digest"], "policy": manifest["policy_digest"]})
    def sanitize(value, operation, start_time=None):
        return sanitize_live_event(value, authorized_session_manifest=manifest,
                                   capability={"backend_kind": "offline_recorded", "qualification_status": "offline_qualified", "admitted": True},
                                   allocation=2, actual_children=0, operation=operation, start_time=start_time)
    root_start = sanitize(receipt(root, None, 0, "active", "workflow"), "start")
    source = tmp_path / "seed" / "payload.json"
    source.parent.mkdir()
    raw = json.dumps(dict(record_format="devbench.rna-langsmith-export/1", privacy_policy="rna-allowlist/1", data_scope="synthetic", runs=[root_start["payload"]])).encode()
    source.write_bytes(raw)
    box = ExportOutbox.create_live(tmp_path / "journal" / "outbox.sqlite", payload_path=source,
                                  expected_sha256=hashlib.sha256(raw).hexdigest(),
                                  authorized_session_manifest=manifest,initial_operations=[root_start])
    return box, manifest, root, receipt, sanitize


def test_session_is_closed_expiring_and_no_clinical_scope(tmp_path):
    value = session()
    assert validate_live_session(value) == value
    for key, bad in (("data_scope", "clinical"), ("max_spans", True),
                     ("expires_at", "2020-01-01T00:00:00+00:00"),
                     ("endpoint", "https://attacker.invalid"), ("prompt", "secret")):
        with pytest.raises((ValueError, PermissionError)):
            validate_live_session({**value, key: bad})


def test_separate_exporter_process_observed(tmp_path):
    box, manifest, root, receipt, sanitize = live_fixture(tmp_path)
    manifest_path = tmp_path / "session.json"
    manifest_path.write_text(json.dumps(manifest))
    receipts = tmp_path / "fake-receipts.jsonl"
    source = tmp_path / "operations.jsonl"
    source.write_bytes(b"")
    process = subprocess.Popen([sys.executable, "-I", "-B", "-m", "devbench.study.rna_langsmith_live",
                             "--session", str(manifest_path), "--outbox", str(box.path),
                             "--fake-transport-receipts", str(receipts), "--operations", str(source), "--poll-seconds", "3"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    children = [str(uuid4()), str(uuid4())]
    starts = [sanitize(receipt(child, root, 0, "active"), "start") for child in children]
    with source.open("ab") as stream:
        for start in starts:
            stream.write(json.dumps(start).encode()+b"\n")
    deadline = time.monotonic()+2
    while time.monotonic() < deadline:
        if receipts.exists() and len(receipts.read_text().splitlines()) >= 3:
            break
        time.sleep(.02)
    assert process.poll() is None
    assert len(receipts.read_text().splitlines()) == 3
    with source.open("ab") as stream:
        for child, start in zip(children, starts):
            finish = sanitize(receipt(child, root, 1, "completed"), "finish", start["payload"]["start_time"])
            stream.write(json.dumps(finish).encode()+b"\n")
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    rows = [json.loads(row) for row in receipts.read_text().splitlines()]
    assert [row["span_id"] for row in rows[:3]] == [root, *children]
    assert [row["method"] for row in rows] == ["POST", "POST", "POST", "PATCH", "PATCH"]
    assert all(row["exporter_pid"] != os.getpid() for row in rows)
    assert box.status()["counts"]["acknowledged"] == 5


def test_failure_does_not_retry_or_call_science(tmp_path):
    box, manifest, _, _, _ = live_fixture(tmp_path)
    calls = []
    def fail(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("private details")
    exporter = LiveTraceExporter.open(authorized_session_manifest=manifest, outbox=box,
                                     sanitized_event_source=(), transport=fail)
    exporter.drain_once()
    exporter.drain_once()
    assert len(calls) == 1
    assert exporter.shutdown()["counts"]["uncertain"] == 1


def test_start_required_revision_immutable_and_parent_unknown_withheld(tmp_path):
    box, manifest, root, receipt, sanitize=live_fixture(tmp_path)
    child=str(uuid4())
    finish=sanitize(receipt(child,root,1,"completed"),"finish")
    with pytest.raises(ValueError):
        box.enqueue_operation(finish["operation"],finish["payload"])
    start=sanitize(receipt(child,root,0,"active"),"start")
    box.enqueue_operation(start["operation"],start["payload"])
    assert box.enqueue_operation(start["operation"],start["payload"])["status"]=="existing"
    with pytest.raises(ValueError):
        box.enqueue_operation({**start["operation"],"event_digest":"e"*64},start["payload"])
    calls=[]
    def fail(**kwargs):
        calls.append(kwargs)
        raise TimeoutError()
    exporter=LiveTraceExporter.open(authorized_session_manifest=manifest,outbox=box,
                                     sanitized_event_source=(),transport=fail)
    exporter.drain_once()
    exporter.drain_once()
    assert [call["payload"]["id"] for call in calls]==[root]
    assert box.status()["counts"]["prepared"]==1


def test_cancel_then_late_finish_is_new_bound_revision(tmp_path):
    box, manifest, root, receipt, sanitize=live_fixture(tmp_path)
    child=str(uuid4())
    start=sanitize(receipt(child,root,0,"active"),"start")
    cancel=sanitize(receipt(child,root,1,"cancelled"),"annotation",start["payload"]["start_time"])
    finish=sanitize(receipt(child,root,2,"completed"),"finish",start["payload"]["start_time"])
    exporter=LiveTraceExporter.open(authorized_session_manifest=manifest,outbox=box,
                                     sanitized_event_source=(start,cancel,finish),transport=lambda **kwargs:{"status_code":200})
    exporter.drain_once()
    with box._db() as db:
        history=[json.loads(row[0])["extra"]["metadata"]["source_status"] for row in db.execute("SELECT payload FROM operations WHERE span_id=? ORDER BY revision",(child,))]
    assert history==["active","cancelled","completed"]


def test_session_mismatch_refused_before_transport(tmp_path):
    box, manifest, _, _, _=live_fixture(tmp_path)
    with pytest.raises(ValueError):
        LiveTraceExporter.open(authorized_session_manifest={**manifest,"workflow_alias":str(uuid4())},
                               outbox=box,sanitized_event_source=(),transport=lambda **kwargs:pytest.fail("session mismatch"))


def test_clock_skew_and_missing_clock_remain_open_annotations(tmp_path):
    _, _, root, receipt, sanitize=live_fixture(tmp_path)
    child=str(uuid4())
    start=sanitize(receipt(child,root,0,"active"),"start")
    value=receipt(child,root,1,"completed")
    value["clock"]["utc"]="2020-01-01T00:00:00Z"
    skew=sanitize(value,"finish",start["payload"]["start_time"])
    assert skew["operation"]["operation"]=="annotation"
    assert "end_time" not in skew["payload"]
    assert skew["payload"]["extra"]["metadata"]["reason_code"]=="clock_disagreement"
    value["clock"].update(utc=None,monotonic_ns=None,timing_basis="unavailable")
    missing=sanitize(value,"finish",start["payload"]["start_time"])
    assert missing["operation"]["operation"]=="annotation"
    assert "end_time" not in missing["payload"]


def test_actual_observer_bridge_structural_gap_and_offline_work(tmp_path):
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    manifest=session()
    directory=tmp_path/"science"
    directory.mkdir()
    observer=LocalTraceObserver(directory/"observer.sqlite",source_digest=manifest["source_digest"],
                                runtime_digest=manifest["runtime_digest"],policy_digest=manifest["policy_digest"])
    observer.begin_interval("work","start")
    contexts=observer.read_contexts("work")
    root=next(context for context in contexts if context["parent_span_id"] is None)
    manifest.update(workflow_alias=root["workflow_alias"],workflow_binding_digest=trace_digest("work"))
    bridge=ReceiptOperationBridge(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",
                                  capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=2,actual_children=0)
    initial=bridge.emit_new(observer=observer,workflow_id="work")
    raw=json.dumps(dict(record_format="devbench.rna-langsmith-export/1",privacy_policy="rna-allowlist/1",data_scope="synthetic",runs=[initial[0]["payload"]])).encode()
    seed=tmp_path/"seed"/"payload.json"
    seed.parent.mkdir()
    seed.write_bytes(raw)
    box=ExportOutbox.create_live(tmp_path/"journal"/"outbox.sqlite",payload_path=seed,
                                expected_sha256=hashlib.sha256(raw).hexdigest(),authorized_session_manifest=manifest,
                                initial_operations=[initial[0]])
    calls=[]
    def offline(**kwargs):
        calls.append(kwargs)
        raise TimeoutError()
    exporter=LiveTraceExporter.open(authorized_session_manifest=manifest,outbox=box,
                                     sanitized_event_source=SanitizedOperationFile(bridge.path),transport=offline)
    exporter.drain_once()
    ledger=WorkflowOperationLedger(directory/"ledger.sqlite")
    ledger.trace_observer=observer
    scientific_calls=[]
    assert ledger.run("work","run",0,{"x":1},lambda:scientific_calls.append(1) or {"result":42})=={"result":42}
    observer.observe_operation("work","investigation_barrier",0,trace_digest("barrier"),"verification","completed")
    additions=bridge.emit_new(observer=observer,workflow_id="work")
    assert any(item["payload"]["extra"]["metadata"]["reason_code"]=="observer_absent" for item in additions)
    exporter.drain_once()
    assert scientific_calls==[1] and len(calls)==1
    assert box.status()["counts"]["uncertain"]==1
    assert box.status()["counts"]["prepared"]>=3
    assert bridge.emit_new(observer=observer,workflow_id="work")==[]


def test_actual_receipt_to_fake_ack_latency_target(tmp_path):
    import statistics
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    manifest=session()
    observer=LocalTraceObserver(tmp_path/"observer.sqlite",source_digest=manifest["source_digest"],runtime_digest=manifest["runtime_digest"],policy_digest=manifest["policy_digest"])
    observer.begin_interval("latency","start")
    root=next(context for context in observer.read_contexts("latency") if context["parent_span_id"] is None)
    manifest.update(workflow_alias=root["workflow_alias"],workflow_binding_digest=trace_digest("latency"))
    bridge=ReceiptOperationBridge(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=0,actual_children=0)
    initial=bridge.emit_new(observer=observer,workflow_id="latency")
    raw=json.dumps(dict(record_format="devbench.rna-langsmith-export/1",privacy_policy="rna-allowlist/1",data_scope="synthetic",runs=[initial[0]["payload"]])).encode()
    seed=tmp_path/"seed"/"payload.json"
    seed.parent.mkdir()
    seed.write_bytes(raw)
    box=ExportOutbox.create_live(tmp_path/"journal"/"outbox.sqlite",payload_path=seed,expected_sha256=hashlib.sha256(raw).hexdigest(),authorized_session_manifest=manifest,initial_operations=[initial[0]])
    exporter=LiveTraceExporter.open(authorized_session_manifest=manifest,outbox=box,sanitized_event_source=SanitizedOperationFile(bridge.path),transport=lambda **kwargs:{"status_code":200})
    exporter.drain_once()
    durations=[]
    for attempt in range(30):
        observer.observe_operation("latency","audit",attempt,trace_digest(attempt),"reservation","active")
        receipt=observer.read_events("latency")[-1]
        bridge.emit_new(observer=observer,workflow_id="latency")
        result=exporter.drain_once()
        assert result["counts"]["prepared"]==0
        durations.append((time.monotonic_ns()-receipt["clock"]["monotonic_ns"])/1e9)
    p95=sorted(durations)[28]
    (tmp_path/"live-latency-summary.json").write_text(json.dumps({"repetitions":30,"basis":"observed_same_process_receipt_to_fake_ack","median_seconds":statistics.median(durations),"p95_seconds":p95,"target_seconds":2,"provider_calls":0}))
    assert p95<=2


def test_rest_start_and_patch_guarded_fake_opener(tmp_path,monkeypatch):
    from devbench.study import rna_langsmith_live as live
    _, manifest, root, receipt, sanitize=live_fixture(tmp_path)
    child=str(uuid4())
    start=sanitize(receipt(child,root,0,"active"),"start")
    finish=sanitize(receipt(child,root,1,"completed"),"finish",start["payload"]["start_time"])
    calls=[]
    class Response:
        status=202
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):assert size==65536;return b""
    class Opener:
        def open(self,request,*,timeout):
            calls.append(request)
            return Response()
    monkeypatch.setattr(live,"build_opener",lambda *handlers:Opener())
    transport=live.RESTLiveTransport(authorized_session_manifest=manifest,api_key="fake-local-only-key")
    assert transport(method="POST",path="/runs",payload=start["payload"],timeout=1)=={"status_code":202}
    assert transport(method="PATCH",path="/runs/"+child,payload=finish["payload"],timeout=1)=={"status_code":202}
    assert [request.get_method() for request in calls]==["POST","PATCH"]
    assert "end_time" not in json.loads(calls[0].data)
    with pytest.raises(ValueError):
        transport(method="POST",path="/runs",payload={**start["payload"],"prompt":"PRIVACY_CANARY"},timeout=1)
    assert len(calls)==2


def test_expired_frozen_session_rejects_delivery(tmp_path,monkeypatch):
    from devbench.study import rna_langsmith_live as live
    box,manifest,_,_,_=live_fixture(tmp_path)
    class FutureDatetime(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.fromisoformat(manifest["expires_at"])+timedelta(seconds=1)
    monkeypatch.setattr(live,"datetime",FutureDatetime)
    with pytest.raises(ValueError):
        box.deliver(transport=lambda **kwargs:pytest.fail("expired delivery"),allow_upload=True)


def test_bridge_resume_and_changed_event_fail_closed(tmp_path):
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    manifest=session()
    observer=LocalTraceObserver(tmp_path/"observer.sqlite",source_digest=manifest["source_digest"],runtime_digest=manifest["runtime_digest"],policy_digest=manifest["policy_digest"])
    observer.begin_interval("resume","start")
    root=next(context for context in observer.read_contexts("resume") if context["parent_span_id"] is None)
    manifest.update(workflow_alias=root["workflow_alias"],workflow_binding_digest=trace_digest("resume"))
    kwargs=dict(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=0,actual_children=0)
    bridge=ReceiptOperationBridge(**kwargs)
    assert bridge.emit_new(observer=observer,workflow_id="resume")
    reopened=ReceiptOperationBridge(**kwargs)
    assert reopened.emit_new(observer=observer,workflow_id="resume")==[]
    changed=observer.read_events("resume")
    changed[0]["source_status"]="failed"
    class ChangedObserver:
        def read_events(self,workflow_id):return changed
    with pytest.raises(ValueError):
        reopened.emit_new(observer=ChangedObserver(),workflow_id="resume")


def test_actual_observer_two_children_overlap_in_separate_exporter(tmp_path):
    from types import SimpleNamespace
    from devbench.analysis.rna_trace_contract import trace_digest
    from devbench.analysis.rna_trace_receipts import LocalTraceObserver
    manifest=session()
    science=tmp_path/"science";science.mkdir()
    observer=LocalTraceObserver(science/"observer.sqlite",source_digest=manifest["source_digest"],runtime_digest=manifest["runtime_digest"],policy_digest=manifest["policy_digest"])
    interval=observer.begin_interval("observed","start")
    root=next(context for context in observer.read_contexts("observed") if context["parent_span_id"] is None)
    manifest.update(workflow_alias=root["workflow_alias"],workflow_binding_digest=trace_digest("observed"))
    bridge=ReceiptOperationBridge(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=2,actual_children=0)
    initial=bridge.emit_new(observer=observer,workflow_id="observed")
    raw=json.dumps(dict(record_format="devbench.rna-langsmith-export/1",privacy_policy="rna-allowlist/1",data_scope="synthetic",runs=[initial[0]["payload"]])).encode()
    seed=tmp_path/"seed"/"payload.json";seed.parent.mkdir();seed.write_bytes(raw)
    box=ExportOutbox.create_live(tmp_path/"journal"/"outbox.sqlite",payload_path=seed,expected_sha256=hashlib.sha256(raw).hexdigest(),authorized_session_manifest=manifest,initial_operations=[initial[0]])
    manifest_path=tmp_path/"session.json";manifest_path.write_text(json.dumps(manifest))
    acknowledgements=tmp_path/"acknowledgements.jsonl"
    process=subprocess.Popen([sys.executable,"-I","-B","-m","devbench.study.rna_langsmith_live","--session",str(manifest_path),"--outbox",str(box.path),"--operations",str(bridge.path),"--fake-transport-receipts",str(acknowledgements),"--poll-seconds","3"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    reservations=[]
    for index in range(2):
        binding=dict(source_digest=manifest["source_digest"],runtime_digest=manifest["runtime_digest"],invocation_id=str(index))
        reservation=SimpleNamespace(request=binding,request_digest=trace_digest(binding),attempt=index,policy_digest=manifest["policy_digest"])
        reference=observer.prepare_invocation("observed",binding,reservation,role="investigator")
        assert reference is not None
        reservations.append((reference,reservation))
    operations=bridge.emit_new(observer=observer,workflow_id="observed")
    child_ids=[item["payload"]["id"] for item in operations]
    until=time.monotonic()+2
    while time.monotonic()<until:
        rows=[json.loads(line) for line in acknowledgements.read_text().splitlines()] if acknowledgements.exists() else []
        if all(any(row["span_id"]==child for row in rows) for child in child_ids):break
        time.sleep(.02)
    assert process.poll() is None
    assert all(any(row["span_id"]==child and row["method"]=="POST" for row in rows) for child in child_ids)
    assert not any(row["method"]=="PATCH" for row in rows)
    for reference,reservation in reservations:
        observer.complete_invocation(reference,reservation=reservation,status="completed")
    observer.observe_operation("observed","investigation_barrier",0,trace_digest("barrier"),"verification","completed")
    observer.finish_interval("observed",interval,"completed")
    bridge.emit_new(observer=observer,workflow_id="observed")
    stdout,stderr=process.communicate(timeout=10)
    assert process.returncode==0,stderr
    rows=[json.loads(line) for line in acknowledgements.read_text().splitlines()]
    assert all(any(row["span_id"]==child and row["method"]=="PATCH" for row in rows) for child in child_ids)
    assert rows[0]["span_id"]==root["span_id"]
    assert box.status()["counts"]["prepared"]==0


def test_child_trace_mismatch_and_second_root_rejected(tmp_path):
    box,_,root,receipt,sanitize=live_fixture(tmp_path)
    child=receipt(str(uuid4()),root,0,"active")
    child["trace_id"]=str(uuid4())
    item=sanitize(child,"start")
    with pytest.raises(ValueError):box.enqueue_operation(item["operation"],item["payload"])
    item=sanitize(receipt(str(uuid4()),None,0,"active","workflow"),"start")
    with pytest.raises(ValueError):box.enqueue_operation(item["operation"],item["payload"])


def test_late_structural_gap_preserves_terminal_parent(tmp_path):
    _,manifest,root,receipt,sanitize=live_fixture(tmp_path)
    events=[receipt(root,None,0,"active","workflow"),receipt(root,None,1,"completed","workflow")]
    events.append(receipt(str(uuid4()),root,0,"completed","investigation_barrier"))
    events[-1].update(kind="verification",dispatch_status="not_dispatched")
    class Observer:
        def read_events(self,workflow_id):return events
    bridge=ReceiptOperationBridge(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=0,actual_children=0)
    operations=bridge.emit_new(observer=Observer(),workflow_id="work")
    assert operations[-1]["payload"]["extra"]["metadata"]["source_status"]=="completed"
    assert operations[-1]["operation"]["operation"]=="annotation"
    assert operations[-1]["payload"]["id"]==root


def test_bridge_reopens_after_multiple_structural_gaps_then_parent_progress(tmp_path):
    _,manifest,root,receipt,_=live_fixture(tmp_path)
    child=str(uuid4())
    events=[receipt(root,None,0,"active","workflow")]
    for sequence in range(3):
        point=receipt(child,root,sequence,"completed","investigation_barrier")
        point.update(kind="verification",dispatch_status="not_dispatched")
        events.append(point)
    class Observer:
        def read_events(self,workflow_id):return events
    kwargs=dict(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=0,actual_children=0)
    bridge=ReceiptOperationBridge(**kwargs)
    source_before=json.dumps(events,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    assert len(bridge.emit_new(observer=Observer(),workflow_id="work"))==4
    assert json.dumps(events,sort_keys=True,separators=(",",":"),allow_nan=False).encode()==source_before
    reopened=ReceiptOperationBridge(**kwargs)
    events.append(receipt(root,None,1,"completed","workflow"))
    added=reopened.emit_new(observer=Observer(),workflow_id="work")
    assert len(added)==1 and added[0]["operation"]["operation"]=="finish"
    assert added[0]["payload"]["extra"]["metadata"]["sequence"]==1


def test_negative_live_transport_epoch_rejected(tmp_path):
    from devbench.study.rna_langsmith_live import validate_live_run
    _,_,root,receipt,sanitize=live_fixture(tmp_path)
    item=sanitize(receipt(str(uuid4()),root,0,"active"),"start")
    item["payload"]["start_time"]="1960-01-01T00:00:00+00:00"
    with pytest.raises(ValueError):validate_live_run(item["payload"],"start")


def test_session_span_cap_blocks_child_before_transport(tmp_path):
    box,manifest,root,receipt,sanitize=live_fixture(tmp_path,max_spans=1)
    child=sanitize(receipt(str(uuid4()),root,0,"active"),"start")
    exporter=LiveTraceExporter.open(authorized_session_manifest=manifest,outbox=box,
                                     sanitized_event_source=(child,),transport=lambda **kwargs:pytest.fail("cap bypass"))
    with pytest.raises(ValueError):exporter.drain_once()
    assert box.status()["counts"]["prepared"]==1
    assert len(box.status()["spans"])==1


def test_repeated_parent_gap_preserves_sanitized_cost_receipt_alias(tmp_path):
    _,manifest,root,receipt,_=live_fixture(tmp_path)
    parent=receipt(root,None,0,"active","workflow")
    raw_cost_alias=str(uuid4())
    parent["metrics"]["provider_cost"]={"value":1,"currency":"USD","basis":"verified_recorded_charge","source_alias":raw_cost_alias}
    events=[parent]
    child=str(uuid4())
    for sequence in range(2):
        point=receipt(child,root,sequence,"completed","investigation_barrier")
        point.update(kind="verification",dispatch_status="not_dispatched")
        events.append(point)
    class Observer:
        def read_events(self,workflow_id):return events
    kwargs=dict(authorized_session_manifest=manifest,output_path=tmp_path/"operations.jsonl",capability={"backend_kind":"offline_recorded","qualification_status":"offline_qualified","admitted":True},allocation=0,actual_children=0)
    bridge=ReceiptOperationBridge(**kwargs)
    source_before=json.dumps(events,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    operations=bridge.emit_new(observer=Observer(),workflow_id="work")
    assert json.dumps(events,sort_keys=True,separators=(",",":"),allow_nan=False).encode()==source_before
    aliases=[item["payload"]["extra"]["metadata"]["metrics"]["provider_cost"]["source_alias"] for item in operations]
    assert len(set(aliases))==1 and aliases[0]!=raw_cost_alias
    assert ReceiptOperationBridge(**kwargs).emit_new(observer=Observer(),workflow_id="work")==[]
    for item in operations:
        assert item["operation"]["payload_sha256"]==hashlib.sha256(json.dumps(item["payload"],sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
