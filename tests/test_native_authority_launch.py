"""Authority/daemon dispatch ordering; no Docker or numerical performance claims."""
from contextlib import contextmanager
from types import SimpleNamespace

from tests.test_native_lifecycle import harness, lifecycle
from devharness_controlled import native_runtime as runtime


def test_authority_refusal_prevents_start_and_still_cleans_created_container(harness):
    @contextmanager
    def refuse():
        raise ValueError("authority tip changed")
        yield
    result=harness.run(authority_guard=refuse)
    assert result["status"]=="failed" and "authority tip changed" in result["error"]
    assert not harness.start_calls and result["cleanup"]["removed"] is True


def test_authority_guard_spans_observed_daemon_start_not_only_client_spawn(harness,monkeypatch):
    state={"held":False,"spawned":False,"observations":0,"started":False}
    decision={"infrastructure_test_only":True}
    @contextmanager
    def guard():
        state["held"]=True
        try:yield decision
        finally:state["held"]=False
    process=SimpleNamespace(pid=12345,returncode=None)
    def poll():
        if state["started"]:
            process.returncode=0
            harness.runtime.record["State"].update(Running=False,Status="exited",ExitCode=0)
            (harness.outputs/"result.json").write_text("{}")
        return process.returncode
    process.poll=poll
    process.terminate=lambda:None
    process.wait=lambda timeout:None
    process.kill=lambda:None
    def start(*args,**kwargs):
        assert state["held"]
        state["spawned"]=True
        harness.runtime.record["State"]["StartedAt"]="0001-01-01T00:00:00Z"
        return process
    inspect=harness.runtime.inspect
    def observed(container):
        if state["spawned"] and not state["started"]:
            assert state["held"]
            state["observations"]+=1
            if state["observations"]==3:
                state["started"]=True
                harness.runtime.record["State"].update(Running=True,Status="running",StartedAt="2026-09-07T12:00:00Z")
        return inspect(container)
    monkeypatch.setattr(runtime.subprocess,"Popen",start)
    harness.runtime.inspect=observed
    result=harness.run(authority_guard=guard,authorize_dispatch=lambda at:None)
    assert result["status"]=="ok" and state["observations"]==3 and not state["held"]
    assert result["launch_authority"]==decision
    assert result["dispatch_observed"]["State"]["StartedAt"]=="2026-09-07T12:00:00Z"
    events=[e["event"] for e in lifecycle(harness)]
    assert events.index("start_requested")<events.index("attach_started")<events.index("authority_dispatch_observed")<events.index("finished")


def test_dispatch_is_rechecked_after_durable_callbacks(harness):
    @contextmanager
    def guard(): yield {"infrastructure_test_only":True}
    state={"requested":False}
    def checkpoint(entry):
        if entry["event"]=="start_requested":state["requested"]=True
    def dispatch(at):
        assert state["requested"]
        raise ValueError("Synthetic authority expired during durable callback")
    result=harness.run(authority_guard=guard,authorize_dispatch=dispatch,on_checkpoint=checkpoint)
    assert result["status"]=="failed" and "expired" in result["error"]
    assert not harness.start_calls and result["cleanup"]["removed"] is True
