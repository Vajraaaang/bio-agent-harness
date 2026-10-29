"""Real process controls and policy transport; no scientific comparator runs."""
import json
from pathlib import Path
import sys
import threading
import time
import subprocess

import pytest

from devbench.analysis import fastq_execution as execution
from devharness_core.watchdog import WorkerCancelled
from tests.test_fastq_execution import patch_preflight, read_result


def test_cancelled_request_never_reads_inputs_or_starts_process(tmp_path, monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(execution, 'preflight_fastq_plan', lambda *a: pytest.fail('cancelled input read'))
    result = execution.run_fastq_plan({}, tmp_path / 'attempts', cancellation_event=stop)
    record = read_result(result)
    assert record['status'] == 'cancelled'
    assert record['outcome_category'] == 'cancelled'
    assert record['engine_launch_count'] == 0
    assert record['artifacts'] == {}


@pytest.mark.parametrize('close_stdout', [False, True])
def test_live_process_is_cancelled_even_after_stdout_closes(tmp_path, close_stdout):
    stop = threading.Event()
    code = 'import os,time; ' + ('os.close(1); os.close(2); ' if close_stdout else '') + 'time.sleep(30)'
    timer = threading.Timer(.2, stop.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(WorkerCancelled):
            execution._execute_command([sys.executable, '-c', code], cwd=tmp_path,
                log_path=tmp_path / 'process.log', timeout_sec=10, cancellation_event=stop)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3


def test_preflight_observer_can_stop_before_runtime(tmp_path, monkeypatch):
    patch_preflight(monkeypatch, execution, tmp_path)
    monkeypatch.setattr(execution, '_nextflow_identity', lambda *a: pytest.fail('runtime entered'))
    events = []
    def observer(event):
        events.append(event)
        return event['event'] != 'attempt_created'
    record = read_result(execution.run_fastq_plan({}, tmp_path / 'runs', execution_observer=observer))
    assert record['status'] == 'cancelled'
    assert record['engine_launch_count'] == 0
    assert events[0]['event'] == 'attempt_created'
    assert Path(events[0]['attempt_dir']).is_absolute()
    assert events[-1]['event'] == 'terminal'


def test_pre_cancelled_count_worker_stays_unstarted(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(WorkerCancelled):
        execution._run_count_model(tmp_path / 'absent.tsv', {}, tmp_path / 'model', alpha=.05,
            deadline=time.monotonic()+10, resources={'memory_gb': 1}, diagnostics={}, cancellation_event=stop)


def test_frozen_policy_reaches_count_fit(monkeypatch, tmp_path):
    policy = {'format': 'policy-transport-fixture'}
    seen = []
    def fit(*args, **kwargs):
        seen.append(kwargs)
        return {'fixture': True}
    monkeypatch.setattr(execution, 'fit_count_matrix', fit)
    execution._count_model_worker(str(tmp_path/'counts.tsv'), {}, str(tmp_path/'model'), .05, policy)
    assert seen == [{'alpha': .05, 'analysis_policy': policy}]


def test_expected_runtime_hash_refuses_before_version_execution(tmp_path, monkeypatch):
    executable = tmp_path / 'untrusted-runtime'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o700)
    monkeypatch.setattr(execution, '_execute_command', lambda *a, **k: pytest.fail('unverified runtime executed'))
    with pytest.raises(ValueError, match='digest|hash'):
        execution._nextflow_identity(executable, tmp_path, expected_sha256='a'*64)


def test_reconcile_only_terminates_verified_owned_process(tmp_path, monkeypatch):
    import psutil
    root = tmp_path / 'fastq-workflow'
    attempt = root / ('fastq-' + 'a'*32)
    attempt.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'],
                             cwd=attempt, start_new_session=True)
    events = [{'event': 'attempt_created', 'attempt_dir': str(attempt)},
              {'event': 'engine_started', 'attempt_dir': str(attempt), 'pid': child.pid,
               'process_create_time': psutil.Process(child.pid).create_time()}]
    monkeypatch.setattr(execution, '_cleanup_containers', lambda p: {'status': 'complete'})
    try:
        result = execution.reconcile_fastq_runtime(events, root)
        assert result['status'] == 'reconciled', result
        child.wait(timeout=3)
        assert result['process_stopped'] is True
    finally:
        if child.poll() is None:
            child.kill(); child.wait()


def test_reconcile_refuses_unowned_paths_without_signalling(tmp_path, monkeypatch):
    monkeypatch.setattr(execution.os, 'killpg', lambda *a: pytest.fail('unowned process signalled'))
    with pytest.raises(ValueError, match='owned|binding'):
        execution.reconcile_fastq_runtime([{'event': 'engine_started',
            'attempt_dir': str(tmp_path / 'elsewhere'), 'pid': 1, 'process_create_time': 0}],
            tmp_path / 'fastq-workflow')


def test_reconcile_missing_launch_identity_remains_uncertain(tmp_path, monkeypatch):
    root = tmp_path / 'fastq-workflow'
    attempt = root / ('fastq-' + 'b'*32)
    attempt.mkdir(parents=True)
    monkeypatch.setattr(execution, '_cleanup_containers', lambda p: {'status': 'complete'})
    result = execution.reconcile_fastq_runtime([
        {'event': 'before_engine_start', 'attempt_dir': str(attempt)}], root)
    assert result['status'] == 'uncertain'
    assert result['process_stopped'] is None


@pytest.mark.parametrize('category,status', [('needs_information','needs_information'),
    ('unsupported_analysis','unsupported'),('invalid_design','refused'),('input_error','input_error')])
def test_typed_admission_status_is_not_flattened_to_invalid_design(tmp_path, monkeypatch, category, status):
    from devbench.analysis.fastq_analysis_policy import FastqAdmissionError
    def reject(plan):
        raise FastqAdmissionError('fixture reason', category=category)
    monkeypatch.setattr(execution, 'preflight_fastq_plan', reject)
    record = read_result(execution.run_fastq_plan({}, tmp_path / 'runs'))
    assert record['status'] == status
    assert record['outcome_category'] == category
    assert record['engine_launch_count'] == 0


def test_reconcile_reused_pid_is_not_signalled(tmp_path, monkeypatch):
    import psutil
    root = tmp_path / 'workflow';attempt = root / ('fastq-'+'c'*32);attempt.mkdir(parents=True)
    class Reused:
        def create_time(self): return 2.0
    monkeypatch.setattr(psutil, 'Process', lambda pid: Reused())
    monkeypatch.setattr(execution.os, 'killpg', lambda *a: pytest.fail('reused PID signalled'))
    monkeypatch.setattr(execution, '_cleanup_containers', lambda p: {'status':'complete'})
    result = execution.reconcile_fastq_runtime([{'event':'engine_started','attempt_dir':str(attempt),
        'pid':2345,'process_create_time':1.0}],root)
    assert result['status']=='uncertain' and result['process_stopped'] is None


def test_missing_leader_with_live_owned_group_is_not_declared_stopped(tmp_path, monkeypatch):
    import os
    import signal
    import psutil
    root=tmp_path/'workflow';attempt=root/('fastq-'+'d'*32);attempt.mkdir(parents=True)
    code='import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True); time.sleep(.2)'
    leader=subprocess.Popen([sys.executable,'-c',code],cwd=attempt,start_new_session=True,
                            stdout=subprocess.PIPE,text=True)
    birth=psutil.Process(leader.pid).create_time()
    child=int(leader.stdout.readline().strip())
    leader.wait(timeout=3)
    monkeypatch.setattr(execution,'_cleanup_containers',lambda p:{'status':'complete'})
    try:
        result=execution.reconcile_fastq_runtime([{'event':'engine_started','attempt_dir':str(attempt),
            'pid':leader.pid,'process_create_time':birth}],root)
        assert result['status']=='uncertain',result
        assert result['process_stopped'] is not True
        assert os.getpgid(child)==leader.pid
    finally:
        try: os.killpg(leader.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        leader.stdout.close()


def test_cleanup_retries_retain_separate_logs(tmp_path,monkeypatch):
    monkeypatch.setattr(execution.shutil,'which',lambda name:'/usr/bin/true')
    first=execution._cleanup_containers(tmp_path)
    second=execution._cleanup_containers(tmp_path)
    assert first['status']==second['status']=='complete'
    assert len(list(tmp_path.glob('container-list*.log')))==2
