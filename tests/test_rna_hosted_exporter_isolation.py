"""Real process/env separation; this is not native OS worker qualification."""
import os
import pytest
from devbench.study.rna_hosted_qualification import FakeExporterChannel, minimal_environment


def test_fresh_environment_does_not_inspect_ambient_secrets(monkeypatch):
    class HostileEnvironment(dict):
        def __iter__(self): raise AssertionError('ambient enumeration')
        def items(self): raise AssertionError('ambient lookup')
        def get(self, *args): raise AssertionError('ambient lookup')
        def __getitem__(self, key): raise AssertionError('ambient lookup')
    monkeypatch.setattr(os,'environ',HostileEnvironment())
    environment=minimal_environment()
    assert environment['LANGSMITH_TRACING']=='false'
    assert not any('KEY' in key or 'TOKEN' in key for key in environment)


def test_exporter_separate_process_and_bounded_protocol(tmp_path,monkeypatch):
    monkeypatch.setenv('LANGSMITH_API_KEY','AMBIENT-SECRET-CANARY')
    monkeypatch.setenv('LANGSMITH_TRACING','true')
    with FakeExporterChannel(root=tmp_path/'exporter') as channel:
        state=channel.request({'command':'boundary'})
        assert state['pid']!=os.getpid()
        assert state['credential_present'] is False
        assert state['tracing_enabled'] is False
        assert state['inherited_context'] is False
        with pytest.raises(ValueError):
            channel.request({'command':'boundary','prompt':'CANARY'})
        with pytest.raises(ValueError):
            channel.request({'command':'prepare','operations':'x'*300000})


def test_fault_controls_dummy_credential_is_exporter_only(tmp_path, monkeypatch):
    """A dummy is created in the child; its value never crosses the pipe."""
    import json
    import subprocess
    import sys
    from tests.support.rna_hosted_trace_fixtures import fake_inputs

    monkeypatch.setenv('LANGSMITH_API_KEY', 'UNREAD-AMBIENT-CANARY')
    monkeypatch.setenv('LANGSMITH_PARENT', 'UNREAD-PARENT-CANARY')
    manifest, criteria, _ = fake_inputs(tmp_path / 'output')
    with FakeExporterChannel(root=tmp_path / 'exporter') as channel:
        channel.request({'command': 'initialize', 'manifest': manifest, 'criteria': criteria})
        boundary = channel.request({'command': 'boundary'})
        assert boundary['credential_present'] is True
        assert boundary['pid'] != os.getpid()
        assert boundary['tracing_enabled'] is False
        assert boundary['inherited_context'] is False
        # Asking the bounded pipe to disclose the child's environment is denied.
        with pytest.raises(ValueError):
            channel.request({'command': 'boundary', 'environment': True})
        probe = subprocess.run(
            [sys.executable, '-I', '-B', '-c',
             'import json, os; print(json.dumps({"pid": os.getpid(), '
             '"credential_present": any(k in os.environ for k in '
             '("DEVBENCH_FAKE_EXPORTER_CANARY", "LANGSMITH_API_KEY")), '
             '"tracing_enabled": os.environ.get("LANGSMITH_TRACING") == "true", '
             '"inherited_context": "LANGSMITH_PARENT" in os.environ}))'],
            cwd=tmp_path, env=minimal_environment(), close_fds=True,
            capture_output=True, text=True, check=True, timeout=10)
        fixture = json.loads(probe.stdout)
        assert fixture['pid'] not in (os.getpid(), boundary['pid'])
        assert fixture['credential_present'] is False
        assert fixture['tracing_enabled'] is False
        assert fixture['inherited_context'] is False
        assert 'fake-only-' not in json.dumps(boundary)
        assert 'UNREAD-' not in json.dumps(boundary) + probe.stdout


def test_fault_controls_owned_adversarial_process_probe_retains_boundary_limits(tmp_path):
    """Exercise only owned dummies and loopback, and report actual OS access.

    A's environment allowlist is not a file/network sandbox. Either permission
    outcome is retained; neither outcome qualifies a native participant boundary.
    """
    import json
    import socket
    import subprocess
    import sys

    canaries = []
    for name in ('dummy-home', 'dummy-sibling', 'dummy-coordinator'):
        path = tmp_path / name / 'canary.txt'
        path.parent.mkdir()
        path.write_text('OWNED-FILE-CANARY', encoding='utf-8')
        canaries.append(str(path.resolve()))
    listener = None
    listener_status = 'available'
    port = 9
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
    except OSError as error:
        listener_status = type(error).__name__
        if listener is not None:
            listener.close()
            listener = None
    script = '''
import json, pathlib, socket, sys
findings = []
for value in json.loads(sys.argv[1]):
    try:
        content = pathlib.Path(value).read_text(encoding="utf-8")
        findings.append({"attempted": True, "permitted": content == "OWNED-FILE-CANARY"})
    except PermissionError:
        findings.append({"attempted": True, "permitted": False})
try:
    with socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=2):
        network = {"attempted": True, "permitted": True}
except OSError as error:
    network = {"attempted": True, "permitted": False, "error_type": type(error).__name__}
print(json.dumps({"files": findings, "loopback": network, "native_isolation_qualified": False}))
'''
    try:
        probe = subprocess.run(
            [sys.executable, '-I', '-B', '-c', script, json.dumps(canaries),
             str(port)],
            cwd=tmp_path, env=minimal_environment(), close_fds=True,
            capture_output=True, text=True, check=True, timeout=10)
    finally:
        if listener is not None:
            listener.close()
    findings = json.loads(probe.stdout)
    findings['listener_status'] = listener_status
    findings['controlled_listener_available'] = listener_status == 'available'
    findings['network_control_status'] = ('observed' if listener_status == 'available'
                                          else 'unavailable')
    assert len(findings['files']) == 3
    assert all(row['attempted'] for row in findings['files'])
    assert findings['loopback']['attempted'] is True
    assert findings['native_isolation_qualified'] is False
    assert 'OWNED-FILE-CANARY' not in probe.stdout
    (tmp_path / 'actual-boundary-observations.json').write_text(
        json.dumps(findings, sort_keys=True), encoding='utf-8')


def test_fault_controls_stopped_exporter_pipe_backpressure_has_deadline(tmp_path, monkeypatch):
    import signal
    import time
    from devbench.study import rna_hosted_qualification as qualification

    channel = FakeExporterChannel(root=tmp_path / 'exporter')
    try:
        assert channel.request({'command':'boundary'})['pid'] == channel.process.pid
        os.kill(channel.process.pid, signal.SIGSTOP)
        _, status = os.waitpid(channel.process.pid, os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        monkeypatch.setattr(qualification, '_IPC_DEADLINE_SECONDS', .05)
        monkeypatch.setattr(qualification, '_IPC_CLOSE_TIMEOUT_SECONDS', .05)
        # Larger than the platform's real pipe capacity, within the wire cap.
        # No mocked writes/selectors/clock can make this stopped reader progress.
        request = {'command':'prepare', 'session':{}, 'operations':'x'*240000}
        started = time.monotonic()
        with pytest.raises(TimeoutError, match='IPC deadline'):
            channel.request(request)
        assert time.monotonic() - started < 2
        assert channel.process.poll() is not None
    finally:
        if channel.process.poll() is None:
            os.kill(channel.process.pid, signal.SIGKILL)
            channel.process.wait(timeout=1)
        channel.close()
