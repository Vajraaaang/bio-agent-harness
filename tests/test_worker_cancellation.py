"""Real, small process controls for coordinator-requested cancellation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest


def waiting_process(path):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    Path(path).write_text(json.dumps({'worker':os.getpid(),'child':child.pid}))
    time.sleep(30)


def identity(value):
    return value


def cancellation_api():
    from devharness_core import watchdog
    assert hasattr(watchdog,'cancel_workers_on'), 'Worker cancellation is not connected to the existing supervisor'
    assert hasattr(watchdog,'WorkerCancelled')
    return watchdog


def test_cancel_stops_observed_process_tree_and_records_cleanup(tmp_path):
    watchdog = cancellation_api()
    path=tmp_path/'pids.json';event=threading.Event();diagnostics={}
    def stop():
        end=time.monotonic()+8
        while not path.exists() and time.monotonic()<end:time.sleep(.02)
        time.sleep(.2)
        event.set()
    thread=threading.Thread(target=stop);thread.start()
    try:
        with watchdog.cancel_workers_on(event), pytest.raises(watchdog.WorkerCancelled):
            watchdog.run_sandboxed(waiting_process,(str(path),),timeout_sec=10,max_mem_mb=512,diagnostics=diagnostics)
    finally:thread.join(timeout=10)
    assert diagnostics['supervisor_status']=='cancelled'
    assert diagnostics['cleanup']['verified_stopped'] is True,diagnostics
    assert diagnostics['cleanup']['coverage']=='observed_process_tree'
    for pid in json.loads(path.read_text()).values():
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_nested_scope_cannot_override_a_stop_and_scope_does_not_leak():
    watchdog=cancellation_api();stop=threading.Event();stop.set()
    with watchdog.cancel_workers_on(stop),watchdog.cancel_workers_on(threading.Event()):
        with pytest.raises(watchdog.WorkerCancelled):
            watchdog.run_sandboxed(identity,(3,),timeout_sec=5,max_mem_mb=512)
    assert watchdog.run_sandboxed(identity,(4,),timeout_sec=5,max_mem_mb=512)==4


def test_cancel_scope_requires_a_coordinator_event():
    watchdog=cancellation_api()
    with pytest.raises(TypeError):
        with watchdog.cancel_workers_on(True):pass


def test_scope_records_cleanup_for_its_own_workers():
    watchdog=cancellation_api()
    with watchdog.cancel_workers_on(threading.Event()) as control:
        assert watchdog.run_sandboxed(identity,(9,),timeout_sec=5,max_mem_mb=512)==9
    assert isinstance(control,dict),'Owning controller has no worker cleanup evidence'
    assert len(control['workers'])==1
    assert control['workers'][0]['cleanup']['verified_stopped'] is True


def test_cleanup_cannot_claim_success_when_inventory_is_denied(monkeypatch):
    watchdog=cancellation_api()
    monitor=watchdog.MemoryWatchdog(os.getpid(),512)
    def denied():raise psutil.AccessDenied(os.getpid())
    monkeypatch.setattr(monitor,'sample_rss_mb',denied)
    monkeypatch.setattr(watchdog,'_terminate_processes',lambda *a:None)
    result=monitor.terminate_tree()
    assert result['verified_stopped'] is False
    assert os.getpid() in result['unknown_pids']


def test_join_reconciles_stale_root_liveness_but_not_descendant_uncertainty(monkeypatch):
    watchdog=cancellation_api();original=watchdog.MemoryWatchdog.terminate_tree
    uncertain=False
    def stale_root(monitor):
        result=original(monitor)
        result['remaining_pids'].append(monitor.target_pid)
        if uncertain:result['unknown_pids'].append(os.getpid())
        result['verified_stopped']=False
        return result
    monkeypatch.setattr(watchdog.MemoryWatchdog,'terminate_tree',stale_root)
    with watchdog.cancel_workers_on(threading.Event()) as control:
        assert watchdog.run_sandboxed(identity,(7,),timeout_sec=5,max_mem_mb=512)==7
    assert control['workers'][0]['cleanup']['verified_stopped'] is True
    uncertain=True
    with watchdog.cancel_workers_on(threading.Event()) as control:
        assert watchdog.run_sandboxed(identity,(8,),timeout_sec=5,max_mem_mb=512)==8
    assert control['workers'][0]['cleanup']['verified_stopped'] is False
