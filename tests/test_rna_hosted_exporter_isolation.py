"""Real process/env separation; this is not native OS worker qualification."""
import os
import pytest
from devbench.study.rna_hosted_qualification import FakeExporterChannel, minimal_environment


def _hosted_exporter_module():
    import importlib.util
    from unittest.mock import patch
    spec = importlib.util.find_spec('devbench.study.rna_hosted_exporter')
    assert spec is not None, 'Dedicated child-only hosted exporter is not implemented'
    # No import-time subprocess credential path is allowed in synthetic tests.
    with patch('subprocess.Popen', side_effect=AssertionError('unexpected import subprocess')):
        from devbench.study import rna_hosted_exporter
    return rna_hosted_exporter


def _guard_hosted_child(exporter, tmp_path, monkeypatch):
    """Run the actual entry point behind test-owned deny-by-default guards."""
    import sys
    marker = tmp_path / 'forbidden-child-secret-attempt'
    script = '''
import ctypes, os, pathlib, subprocess, sys
marker = pathlib.Path(sys.argv[1])
def denied(*args, **kwargs):
    marker.write_text('attempted', encoding='utf-8')
    raise AssertionError('Synthetic child cannot launch credential subprocesses')
subprocess.Popen = denied
subprocess.run = denied
ctypes.CDLL = denied
os.system = denied
os.popen = denied
from devbench.study import rna_hosted_qualification as driver
raise SystemExit(driver.main(['--hosted-exporter-child']))
'''
    monkeypatch.setattr(exporter, '_child_command', lambda: [
        sys.executable, '-I', '-B', '-c', script, str(marker)])
    return marker


def _hosted_descriptor(tmp_path, *, approved=False):
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from devbench.study import rna_hosted_preflight as preflight
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    now = datetime.now(timezone.utc)
    proposal = preflight.prepare_hosted_action(action='target_read',
        binding={key: 'a' * 64 for key in preflight.BINDING_KEYS},
        target=dict(endpoint='https://api.smith.langchain.com',
                    workspace_id=str(uuid4()), project_id=str(uuid4()),
                    project_name='synthetic-channel-test'), run_ids=[str(uuid4())],
        limits=dict(max_spans=4, max_bytes=32768, max_mutation_requests=1,
                    max_read_requests=1, max_delivery_attempts=1, total_deadline_seconds=10),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        evidence_path=str(tmp_path / 'evidence'), now=now)
    record = None if not approved else dict(format='devbench.rna-hosted-action-approval/1',
        proposal_digest=digest(canonical_json_bytes(proposal)), action='target_read',
        approved=True, operator_id=str(uuid4()), implementation_owner_id=str(uuid4()),
        expires_at=proposal['expires_at'])
    return dict(format='devbench.rna-hosted-exporter-session/1',
                proposal=proposal, approval_record=record)


def test_a2b_opaque_handle_selects_one_exact_item_without_credentials():
    exporter = _hosted_exporter_module()
    handle = exporter.keychain_credential_handle()
    assert handle == {'format': 'devbench.rna-exporter-credential-handle/1',
                      'handle': 'langsmith-bio-harness'}
    handle['handle'] = 'changed'
    assert exporter.keychain_credential_handle()['handle'] == 'langsmith-bio-harness'


@pytest.mark.parametrize('approved', [False, True])
def test_a2b_blocked_child_startup_is_zero_secret_and_zero_authority(tmp_path, approved, monkeypatch):
    exporter = _hosted_exporter_module()
    marker = _guard_hosted_child(exporter, tmp_path, monkeypatch)
    with exporter.HostedExporterChannel(root=tmp_path / 'exporter',
            descriptor=_hosted_descriptor(tmp_path, approved=approved),
            credential_handle=exporter.keychain_credential_handle()) as channel:
        state = channel.request({'command': 'status'})
        boundary = channel.request({'command': 'boundary'})
        assert state['status'] == 'blocked'
        assert 'unsupported_schema' in state['blockers']
        assert state['credential_resolutions'] == boundary['credential_resolutions'] == 0
        assert state['authorize_upload'] is state['authorize_remote_read'] is False
        assert boundary['pid'] != os.getpid()
        assert boundary['tracing_enabled'] is boundary['inherited_context'] is False
    assert not list((tmp_path / 'exporter').iterdir())
    assert not marker.exists()


@pytest.mark.parametrize('location', ['descriptor', 'proposal', 'handle'])
def test_a2b_raw_credential_rejected_before_spawn_or_storage(tmp_path, monkeypatch, location):
    exporter = _hosted_exporter_module()
    import subprocess
    descriptor = _hosted_descriptor(tmp_path)
    handle = exporter.keychain_credential_handle()
    target = {'descriptor': descriptor, 'proposal': descriptor['proposal'], 'handle': handle}[location]
    target['api_key'] = 'DUMMY-RAW-CREDENTIAL-NEVER-RESOLVED'
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('child spawned'))
    with pytest.raises(ValueError) as error:
        exporter.HostedExporterChannel(root=tmp_path / 'exporter',
            descriptor=descriptor, credential_handle=handle)
    assert 'DUMMY-' not in str(error.value)
    assert not (tmp_path / 'exporter').exists()


def test_a2b_fake_channel_never_requests_secret_resolution(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    monkeypatch.setattr(exporter, '_resolve_keychain_credential',
                        lambda: pytest.fail('fake requested a secret'))
    with FakeExporterChannel(root=tmp_path / 'fake') as channel:
        assert channel.request({'command': 'boundary'})['credential_present'] is False


def test_a2b_child_launch_has_clean_environment_closed_fds_and_no_handle_in_argv(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    marker = _guard_hosted_child(exporter, tmp_path, monkeypatch)
    import subprocess
    original = subprocess.Popen
    launches = []
    def observed(*args, **kwargs):
        launches.append((args, kwargs))
        return original(*args, **kwargs)
    class HostileEnvironment(dict):
        def __iter__(self): raise AssertionError('ambient enumeration')
        def items(self): raise AssertionError('ambient enumeration')
        def get(self, *args): raise AssertionError('ambient lookup')
        def __getitem__(self, key): raise AssertionError('ambient lookup')
    monkeypatch.setattr(subprocess, 'Popen', observed)
    monkeypatch.setattr(os, 'environ', HostileEnvironment())
    with exporter.HostedExporterChannel(root=tmp_path / 'exporter',
            descriptor=_hosted_descriptor(tmp_path),
            credential_handle=exporter.keychain_credential_handle()) as channel:
        assert channel.request({'command': 'status'})['credential_resolutions'] == 0
    args, kwargs = launches[0]
    assert kwargs['env'] == minimal_environment()
    assert kwargs['close_fds'] is True
    assert kwargs['stderr'] == subprocess.DEVNULL
    assert 'langsmith-bio-harness' not in repr(args)
    assert 'synthetic-channel-test' not in repr(args)
    assert not marker.exists()


@pytest.mark.parametrize('raw', [b'{"api_key":"DUMMY-LEAK"}\n',
    b'{"error":"DUMMY-LEAK"}\n', b'[]\n', b'not-json\n',
    b'{"status":"blocked","extra":"DUMMY-LEAK"}\n'])
def test_a2b_bad_child_response_and_exception_text_never_cross_boundary(raw):
    exporter = _hosted_exporter_module()
    with pytest.raises(ValueError) as error:
        exporter._decode_response(raw, command='status')
    assert 'DUMMY-LEAK' not in str(error.value)
    import json
    try:
        value = json.loads(raw)
    except ValueError:
        value = {'error': 'DUMMY-LEAK'}
    emitted = exporter._encoded_response(value, command='status')
    assert emitted == b'{"error":"Exporter operation rejected"}\n'
    assert b'DUMMY-LEAK' not in emitted


def test_a2b_child_rejects_raw_secret_command_and_restart_does_not_resolve(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    marker = _guard_hosted_child(exporter, tmp_path, monkeypatch)
    root = tmp_path / 'exporter'
    descriptor = _hosted_descriptor(tmp_path)
    for restart in (False, True):
        with exporter.HostedExporterChannel(root=root, descriptor=descriptor,
                credential_handle=exporter.keychain_credential_handle(), restart=restart) as channel:
            with pytest.raises(ValueError):
                channel.request({'command': 'status', 'api_key': 'DUMMY-REJECT'})
            with pytest.raises(ValueError):
                channel.request({'command': 'prepare', 'session': {}, 'operations': []})
            assert channel.request({'command': 'status'})['credential_resolutions'] == 0
    assert not list(root.iterdir())
    assert not marker.exists()


def test_a2b_dummy_child_resolver_and_fixture_prove_process_separation_only(tmp_path):
    """Inject only an owned dummy inside a trusted test child; no Keychain call.

    The sibling fixture has a clean environment and closed FDs. This demonstrates
    process separation and deliberately makes no claim of OS access denial.
    """
    exporter = _hosted_exporter_module()
    assert hasattr(exporter,'_native_credential_command'), 'Fixed bounded native helper command absent'
    import json
    import subprocess
    import sys
    with pytest.raises(PermissionError):
        exporter._resolve_keychain_credential()
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    script = '''
import ctypes, json, os, subprocess, sys
original_popen = subprocess.Popen
def denied(*args, **kwargs):
    raise AssertionError('Synthetic canary cannot access host credential APIs')
subprocess.Popen = denied
subprocess.run = denied
ctypes.CDLL = denied
os.system = denied
os.popen = denied
from devbench.study import rna_hosted_exporter as module
module._CHILD_PROCESS_PID = os.getpid()
calls = []
def fake_popen(command, **kwargs):
    assert command == module._native_credential_command()
    assert kwargs['env'] == module._native_credential_environment()
    assert kwargs['close_fds'] is True
    assert kwargs['stderr'] == subprocess.DEVNULL
    assert kwargs['stdin'] == subprocess.DEVNULL
    calls.append(True)
    # Sole permitted subprocess: fixed synthetic producer, dummy made there.
    # No secret is placed in argv or written to disk.
    return original_popen([sys.executable, '-I', '-B', '-c',
        "import os,sys; sys.stdout.buffer.write(b'dummy-child-only-' + "
        "os.urandom(24).hex().encode() + b'\\\\n')"], **kwargs)
module.subprocess.Popen = fake_popen
resolved = module._resolve_keychain_credential()
try:
    os.fstat(int(sys.argv[1])); inherited_fd = True
except OSError:
    inherited_fd = False
print(json.dumps({'pid': os.getpid(), 'dummy_resolved': resolved.startswith('dummy-child-only-'),
                  'resolver_calls': len(calls), 'inherited_fd': inherited_fd,
                  'environment_credential': 'LANGSMITH_API_KEY' in os.environ,
                  'native_isolation_qualified': False}))
'''
    try:
        child = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(read_fd)],
            cwd=tmp_path, env=minimal_environment(), close_fds=True,
            capture_output=True, check=True, text=True, timeout=10)
    finally:
        os.close(read_fd)
        os.close(write_fd)
    result = json.loads(child.stdout)
    assert result == dict(pid=result['pid'], dummy_resolved=True, resolver_calls=1,
        inherited_fd=False, environment_credential=False, native_isolation_qualified=False)
    assert result['pid'] != os.getpid()
    assert 'dummy-child-only-' not in child.stdout + child.stderr
    fixture = subprocess.run([sys.executable, '-I', '-B', '-c',
        'import os; assert "LANGSMITH_API_KEY" not in os.environ; '
        'assert not any("dummy-child-only" in v for v in os.environ.values()); print("clean")'],
        cwd=tmp_path, env=minimal_environment(), close_fds=True,
        capture_output=True, check=True, text=True, timeout=10)
    assert fixture.stdout == 'clean\n'


def test_a2b_blocked_admission_cannot_invoke_injected_dummy_resolver(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    monkeypatch.setattr(exporter, '_resolve_keychain_credential',
                        lambda: pytest.fail('blocked startup resolved credential'))
    state = exporter._initialize(_hosted_descriptor(tmp_path, approved=True),
                                 exporter.keychain_credential_handle())
    assert state['status'] == 'blocked'
    assert state['credential_resolutions'] == 0


def test_a2b_descriptor_byte_cap_rejected_before_spawn(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    import subprocess
    descriptor = _hosted_descriptor(tmp_path)
    descriptor['padding'] = 'DUMMY-' * 50000
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('child spawned'))
    with pytest.raises(ValueError):
        exporter.HostedExporterChannel(root=tmp_path / 'exporter', descriptor=descriptor,
            credential_handle=exporter.keychain_credential_handle())
    assert not (tmp_path / 'exporter').exists()


@pytest.mark.parametrize('field', ['status', 'format', 'credential_resolutions'])
def test_a2b_duplicate_known_response_keys_rejected_without_marker(tmp_path, field):
    exporter = _hosted_exporter_module()
    import json
    state = exporter._initialize(_hosted_descriptor(tmp_path), exporter.keychain_credential_handle())
    raw = ('{"' + field + '":"DUMMY-DUPLICATE-MARKER",' + json.dumps(state)[1:] + '\n').encode()
    with pytest.raises(ValueError) as error:
        exporter._decode_response(raw, command='status')
    assert 'DUMMY-DUPLICATE' not in str(error.value)


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_a2b_nonfinite_wire_constants_rejected(constant):
    exporter = _hosted_exporter_module()
    with pytest.raises(ValueError):
        exporter._parse_json(('{"value":' + constant + '}').encode())


def test_a2b_duplicate_initialize_command_rejected_inside_real_child_entry(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    import json
    import subprocess
    marker = _guard_hosted_child(exporter, tmp_path, monkeypatch)
    message = dict(command='initialize', descriptor=_hosted_descriptor(tmp_path),
                   credential_handle=exporter.keychain_credential_handle())
    raw = '{"command":"DUMMY-DUPLICATE-MARKER",' + json.dumps(message)[1:] + '\n'
    result = subprocess.run(exporter._child_command(), input=raw, cwd=tmp_path,
        env=minimal_environment(), close_fds=True, capture_output=True,
        check=True, text=True, timeout=10)
    assert result.stdout == '{"error":"Exporter operation rejected"}\n'
    assert 'DUMMY-DUPLICATE' not in result.stdout + result.stderr
    assert not marker.exists()


def test_a2b_launch_failure_sanitized_and_removes_fresh_empty_root(tmp_path, monkeypatch):
    exporter = _hosted_exporter_module()
    import subprocess
    def failed(*args, **kwargs):
        raise OSError('DUMMY-LAUNCH-ERROR-MARKER')
    monkeypatch.setattr(subprocess, 'Popen', failed)
    with pytest.raises(ValueError, match='Exporter process unavailable') as error:
        exporter.HostedExporterChannel(root=tmp_path / 'exporter',
            descriptor=_hosted_descriptor(tmp_path),
            credential_handle=exporter.keychain_credential_handle())
    assert 'DUMMY-LAUNCH' not in str(error.value)
    assert not (tmp_path / 'exporter').exists()


@pytest.mark.parametrize('scenario', ['blank', 'multiline', 'crlf', 'control',
                                    'oversize', 'nonzero', 'exception', 'timeout'])
def test_a2b_dummy_resolver_rejects_invalid_output_and_sanitizes_errors(tmp_path, scenario):
    """Only fixed owned producers are executable; real Keychain is unreachable."""
    assert hasattr(_hosted_exporter_module(),'_native_credential_command'), 'Fixed bounded native helper command absent'
    import json
    import subprocess
    import sys
    script = '''
import ctypes, json, os, subprocess, sys
original_popen = subprocess.Popen
def denied(*args, **kwargs):
    raise AssertionError('No host credential API in synthetic test')
subprocess.Popen = denied
subprocess.run = denied
ctypes.CDLL = denied
os.system = denied
os.popen = denied
from devbench.study import rna_hosted_exporter as module
module._CHILD_PROCESS_PID = os.getpid()
scenario = sys.argv[1]
producers = {
 'blank': 'import sys; sys.stdout.buffer.write(bytes([10]))',
 'multiline': 'import sys; sys.stdout.buffer.write(b"dummy" + bytes([10]) + b"bad")',
 'crlf': 'import sys; sys.stdout.buffer.write(b"dummy" + bytes([13,10]))',
 'control': 'import sys; sys.stdout.buffer.write(b"dummy" + bytes([127]))',
 'oversize': 'import sys; sys.stdout.buffer.write(b"x" * 4097)',
 'nonzero': 'import sys; print("dummy"); sys.exit(2)',
 'timeout': 'import time; time.sleep(2)',
}
def fake_popen(command, **kwargs):
    assert command == module._native_credential_command()
    assert kwargs['env'] == module._native_credential_environment()
    if scenario == 'exception':
        raise RuntimeError('DUMMY-PROVIDER-ERROR-MARKER')
    return original_popen([sys.executable, '-I', '-B', '-c', producers[scenario]], **kwargs)
module.subprocess.Popen = fake_popen
if scenario == 'timeout':
    module._SECRET_TIMEOUT_SECONDS = .05
try:
    module._resolve_keychain_credential()
    result = {'rejected': False}
except ValueError as error:
    result = {'rejected': True, 'message': str(error), 'cause_absent': error.__cause__ is None,
              'context_suppressed': error.__suppress_context__}
print(json.dumps(result))
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', script, scenario],
        cwd=tmp_path, env=minimal_environment(), close_fds=True,
        capture_output=True, check=True, text=True, timeout=10)
    assert json.loads(result.stdout) == dict(rejected=True, message='Exporter credential unavailable',
                                           cause_absent=True, context_suppressed=True)
    assert 'DUMMY-PROVIDER' not in result.stdout + result.stderr


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


def _readback_api():
    import importlib
    import importlib.util
    name = 'devbench.study.rna_hosted_context_readback'
    assert importlib.util.find_spec(name) is not None, 'GET-only historical context readback engine absent'
    return importlib.import_module(name)


def _readback_fixture(tmp_path, monkeypatch, *, mode='exact', lifetime_seconds=None, count=3,
        seal_format='devbench.rna-context-history-seal/1', checkpoint_bytes=None,
        unacknowledged_terminal=False, duplicate_slots=False):
    """Real current contract, archived outbox, and original child entry point.

    Only credential production and external HTTPS are test owned. Admission,
    installed identity, custody, durable intent and comparison stay production.
    """
    import hashlib
    import importlib
    import importlib.util
    import json
    from pathlib import Path
    import sys
    from types import SimpleNamespace
    from tests.test_rna_hosted_workflow_contract import make_readback_scope, readback_handles
    api = _readback_api()
    contract_name = 'devbench.study.rna_hosted_context_readback_contract'
    assert importlib.util.find_spec(contract_name) is not None, 'Historical readback contract absent'
    contract = importlib.import_module(contract_name)
    scope = make_readback_scope(tmp_path, count=count, seal_format=seal_format,
        checkpoint_bytes=checkpoint_bytes, unacknowledged_terminal=unacknowledged_terminal,
        duplicate_slots=duplicate_slots)
    # This is retained by the trusted fixture launcher before packet assembly.
    # Packet/history fields are untrusted inputs to the eventual child.
    trusted_root = scope['producer_binding']['failure_seal_digest']
    scope['installed_identity'] = contract.readback_installed_identity()
    if lifetime_seconds is not None:
        from datetime import datetime, timedelta, timezone
        scope['now'] = datetime.now(timezone.utc)
        scope['expires_at'] = (scope['now'] + timedelta(seconds=lifetime_seconds)).isoformat()
    packet = contract.prepare_context_readback_packet(**scope)
    handles = readback_handles(tmp_path, contract, packet)
    descriptor = dict(format='devbench.rna-hosted-context-readback-session/1',
        packet=packet, approval_handles=handles,
        expected_historical_root=trusted_root)
    import sqlite3
    remote = {}
    with sqlite3.connect(Path(packet['history']['outbox']['path']).as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        for raw, in db.execute("SELECT payload FROM operations WHERE state='acknowledged' ORDER BY revision LIMIT 33"):
            payload = json.loads(raw)
            remote[payload['id']] = payload
    config = tmp_path / 'readback-fixture.json'
    config.write_text(json.dumps(dict(target=packet['target'], remote=remote,
        mode=mode, output=packet['evidence_path'], reads=packet['read_descriptor'],
        packet_digest=contract.packet_digest(packet),
        watched_members=[ref['path'] for key, ref in packet['history'].items()
            if key not in {'root', 'failure_seal'}])))
    audit = tmp_path / 'readback-audit.jsonl'
    exporter = _hosted_exporter_module()
    monkeypatch.setattr(exporter, '_child_command', lambda: [sys.executable, '-I', '-B', '-c',
        READBACK_TRUSTED_CHILD, str(config), str(audit)])
    def snapshot():
        root = Path(packet['history']['root'])
        return {str(path.relative_to(root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ctime_ns,
            path.stat().st_ino, path.stat().st_mode) for path in root.rglob('*') if path.is_file()}
    def rows():
        return [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
    def channel(*, restart=False):
        return exporter.HostedExporterChannel(root=Path(packet['evidence_path']), descriptor=descriptor,
            credential_handle=exporter.keychain_credential_handle(), restart=restart)
    return SimpleNamespace(api=api, contract=contract, scope=scope, packet=packet, handles=handles,
        descriptor=descriptor, exporter=exporter, config=config, audit=audit, rows=rows,
        snapshot=snapshot, channel=channel, remote=remote, trusted_root=trusted_root)


def _readback_initialize_original_child_directly(fixture):
    """Bypass only parent IPC validation, retaining the original child handler."""
    import json
    from pathlib import Path
    import subprocess
    request = dict(command='initialize', descriptor=fixture.descriptor,
        credential_handle=fixture.exporter.keychain_credential_handle())
    completed = subprocess.run(fixture.exporter._child_command(),
        input=(json.dumps(request) + '\n').encode(), stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, close_fds=True, env=minimal_environment(),
        cwd=Path(fixture.packet['evidence_path']).parent, timeout=10, check=True)
    assert len(completed.stdout) <= 262144
    value = json.loads(completed.stdout)
    assert value == {'error': 'Exporter operation rejected'} or (
        value['status'] == 'blocked' and value['credential_resolutions'] == 0
        and value['authorize_upload'] is value['authorize_remote_read'] is False)


READBACK_TRUSTED_CHILD = r'''
import ctypes, io, json, os, pathlib, socket, subprocess, sys, urllib.request
real_popen = subprocess.Popen
config = json.loads(pathlib.Path(sys.argv[1]).read_text())
audit = pathlib.Path(sys.argv[2])
def log(kind, **fields):
    with audit.open('a') as stream: stream.write(json.dumps(dict(kind=kind, **fields))+'\n')
watched_members = frozenset(config['watched_members'])
def observe_file_access(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.fsdecode(args[0]) in watched_members:
            log('history_member_read')
sys.addaudithook(observe_file_access)
def denied(*args, **kwargs):
    log('forbidden')
    raise AssertionError('offline readback native/network denied')
subprocess.Popen = denied
subprocess.run = denied
ctypes.CDLL = denied
os.system = denied
os.popen = denied
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.create_connection = denied
urllib.request.urlopen = denied
from devbench.study import rna_hosted_qualification as driver
from devbench.study import rna_hosted_exporter as exporter
from devbench.study import rna_hosted_context_readback as readback
def producer(command, **kwargs):
    assert command == exporter._native_credential_command()
    assert kwargs['env'] == exporter._native_credential_environment() and kwargs['close_fds']
    output = pathlib.Path(config['output'])
    assert output.is_dir() and any(output.iterdir()), 'durable one-use intent must precede secret resolution'
    log('credential')
    if config['mode'] == 'credential_crash':
        os._exit(71)
    return real_popen([sys.executable,'-I','-B','-c',
        "import sys;sys.stdout.write('DUMMY-READBACK-ONLY-CREDENTIAL\\n')"], **kwargs)
exporter.subprocess.Popen = producer
class Response:
    def __init__(self, status, raw, *, incomplete=False):
        self.status=status; self.stream=io.BytesIO(raw)
        self.length=len(raw) + int(incomplete); self.chunked=False
    def isclosed(self): return self.length == 0
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def close(self): self.stream.close()
    def read(self, size):
        assert 0 <= size <= 131073
        value=self.stream.read(size); self.length-=len(value); return value
class Opener:
    def open(self, request, timeout):
        assert request.get_method() == 'GET' and request.data is None
        assert request.get_header('X-api-key') == 'DUMMY-READBACK-ONLY-CREDENTIAL'
        assert 0 < timeout <= 15
        path=request.full_url.split('langchain.com',1)[1]
        # The production intent must already be durably materialized before
        # external transport is entered, including the project GET.
        previous=[json.loads(line) for line in audit.read_text().splitlines()]
        request_index=1+sum(row['kind']=='http' for row in previous)
        intent=pathlib.Path(config['output']) / ('request-' + str(request_index).zfill(4) + '-intent.json')
        assert intent.is_file(), 'GET entered transport before durable request intent'
        retained=json.loads(intent.read_text())
        assert set(retained)=={'format','packet_digest','request_index','method','path'}
        assert retained['format']=='devbench.rna-context-readback-request-intent/1'
        assert retained['packet_digest']==config['packet_digest']
        assert retained['request_index']==request_index and retained['method']=='GET' and retained['path']==path
        log('http', method=request.get_method(), path=path, timeout=timeout)
        target=config['target']
        if path == config['reads']['project_route']:
            if config['mode'] == 'project_deadline':
                import time
                time.sleep(4)
            return Response(200,json.dumps(dict(id=target['project_id'],tenant_id=target['workspace_id'],
                name=target['project_name'])).encode())
        assert path.startswith('/api/v1/runs/') and path.rsplit('/',1)[1] in config['remote']
        mode=config['mode']
        if mode == 'transport_timeout': raise TimeoutError('PRIVATE_READBACK_PROVIDER_CANARY')
        body=dict(config['remote'][path.rsplit('/',1)[1]])
        body.pop('session_name',None)
        body.update(status='error' if 'error' in body else 'success' if 'end_time' in body else 'pending',
            app_path='/offline-historical-context')
        if mode == 'status_mismatch': body['status']='PRIVATE_READBACK_PROVIDER_CANARY'
        raw=json.dumps(body).encode()
        if mode == 'oversized': raw=b'PRIVATE_READBACK_BODY_CANARY' * 7000
        if mode in {'http_error', 'http_error_incomplete', 'http_error_ambiguous'}:
            from urllib.error import HTTPError
            response=Response(404,raw,incomplete=mode == 'http_error_incomplete')
            if mode == 'http_error_ambiguous': response.length=None
            raise HTTPError(request.full_url,404,'PRIVATE_READBACK_PROVIDER_CANARY',{},response)
        if mode == 'partial_error':
            response=Response(200,raw)
            original=response.read
            def partial(size):
                if response.stream.tell(): raise TimeoutError('PRIVATE_READBACK_PROVIDER_CANARY')
                return original(min(size,7))
            response.read=partial
            return response
        return Response(200,raw,incomplete=mode == 'incomplete')
def opener(*handlers):
    import ssl
    from urllib.request import HTTPSHandler,ProxyHandler,HTTPRedirectHandler
    assert any(isinstance(h,ProxyHandler) and h.proxies == {} for h in handlers)
    tls=[h for h in handlers if isinstance(h,HTTPSHandler)]
    assert len(tls)==1 and tls[0]._context.verify_mode==ssl.CERT_REQUIRED and tls[0]._context.check_hostname
    assert any(isinstance(h,HTTPRedirectHandler) and h.redirect_request(None,None,302,'',{},'https://invalid.example') is None
        for h in handlers)
    return Opener()
readback.build_opener=opener
raise SystemExit(driver.main(['--hosted-exporter-child']))
'''


@pytest.mark.parametrize('count', [3, 9])
def test_readback_original_child_descriptor_runs_only_approved_gets(tmp_path, monkeypatch, count):
    fixture = _readback_fixture(tmp_path, monkeypatch, count=count)
    before = fixture.snapshot()
    with fixture.channel() as channel:
        state = channel.request({'command': 'status'})
        assert state['status'] == 'admitted' and state['authorize_upload'] is False
        boundary = channel.request({'command': 'boundary'})
        assert boundary['pid'] == channel.process.pid != os.getpid()
        assert boundary['credential_resolutions'] == 1
        assert boundary['tracing_enabled'] is boundary['inherited_context'] is False
        result = channel.request({'command': 'verify'})
        assert result['verification_result'] == 'exact_context_match'
    assert fixture.snapshot() == before
    rows = fixture.rows()
    assert sum(row['kind'] == 'credential' for row in rows) == 1
    http = [row for row in rows if row['kind'] == 'http']
    assert len(http) == count + 1 and all(row['method'] == 'GET' for row in http)
    assert {row['path'] for row in http} == {fixture.packet['read_descriptor']['project_route'],
        *fixture.packet['read_descriptor']['run_routes']}
    assert not any(row['kind'] == 'forbidden' for row in rows)


@pytest.mark.parametrize('wire_request', [
    {'command':'emit','operation':{}}, {'command':'reconcile','receipt':{},'approval_handle':{}},
    {'command':'prepare'}, {'command':'drain'}, {'command':'verify','route':'/api/v1/runs/query'},
    {'command':'verify','method':'POST'}, {'command':'verify','model':'PRIVATE_CANARY'}])
def test_readback_parent_rejects_mutation_query_and_model_commands_before_ipc(tmp_path, monkeypatch, wire_request):
    fixture = _readback_fixture(tmp_path, monkeypatch)
    with fixture.channel() as channel:
        before = fixture.rows()
        with pytest.raises(ValueError): channel.request(wire_request)
        assert fixture.rows() == before


@pytest.mark.parametrize('wire_request', [
    {'command':'emit','operation':{}}, {'command':'reconcile','receipt':{},'approval_handle':{}},
    {'command':'prepare'}, {'command':'drain'}, {'command':'verify','route':'/api/v1/runs/query'},
    {'command':'verify','method':'POST'}])
def test_readback_original_child_independently_denies_parent_bypass(tmp_path, monkeypatch, wire_request):
    fixture = _readback_fixture(tmp_path, monkeypatch)
    with fixture.channel() as channel:
        before = fixture.rows()
        with pytest.raises(ValueError): channel._send(wire_request)
        assert fixture.rows() == before


@pytest.mark.parametrize('mutation', ['approval', 'history', 'root', 'reader'])
def test_readback_bad_approval_custody_or_reader_never_resolves_secret(tmp_path, monkeypatch, mutation):
    from pathlib import Path
    fixture = _readback_fixture(tmp_path, monkeypatch)
    before = fixture.snapshot()
    if mutation == 'approval': fixture.descriptor['approval_handles'] = {}
    elif mutation == 'history':
        fixture.descriptor['packet']['history']['outbox']['sha256'] = '0' * 64
    elif mutation == 'root': fixture.descriptor['expected_historical_root'] = '0' * 64
    else: fixture.descriptor['packet']['reader_binding']['source_digest'] = '0' * 64
    try:
        with fixture.channel() as channel:
            state = channel.request({'command':'status'})
            assert state['status'] == 'blocked' and state['credential_resolutions'] == 0
    except (ValueError, PermissionError):
        pass
    assert not any(row['kind'] in {'credential', 'http', 'forbidden'} for row in fixture.rows())
    if mutation == 'root':
        _readback_initialize_original_child_directly(fixture)
        assert not any(row['kind'] == 'history_member_read' for row in fixture.rows())
        assert not any(row['kind'] in {'credential', 'http', 'forbidden'} for row in fixture.rows())
    assert fixture.snapshot() == before
    assert not Path(fixture.packet['evidence_path']).exists()


def test_readback_incomplete_existing_output_cannot_be_reset_or_reused(tmp_path, monkeypatch):
    from pathlib import Path
    fixture = _readback_fixture(tmp_path, monkeypatch)
    output = Path(fixture.packet['evidence_path'])
    output.mkdir(mode=0o700)
    (output / 'incomplete-intent').write_bytes(b'')
    try:
        with fixture.channel(restart=True) as channel:
            status = channel.request({'command':'status'})
            assert status['status'] == 'blocked' and status['credential_resolutions'] == 0
    except (ValueError, PermissionError):
        pass
    assert fixture.rows() == []
    assert (output / 'incomplete-intent').read_bytes() == b''


def test_readback_descriptor_never_accepts_extra_secret_or_route_before_spawn(tmp_path, monkeypatch):
    fixture = _readback_fixture(tmp_path, monkeypatch)
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('untrusted descriptor spawned'))
    fixture.descriptor['route'] = '/api/v1/runs/query'
    fixture.descriptor['api_key'] = 'PRIVATE_READBACK_DESCRIPTOR_CANARY'
    with pytest.raises(ValueError) as error:
        fixture.channel()
    assert 'PRIVATE_' not in str(error.value)
    assert fixture.rows() == []


def test_readback_rebased_history_cannot_replace_trusted_root_or_protected_approval(tmp_path, monkeypatch):
    from copy import deepcopy
    from pathlib import Path
    import json
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.langsmith_outbox import digest
    fixture = _readback_fixture(tmp_path, monkeypatch)
    forged_scope = deepcopy(fixture.scope)
    seal = json.loads(Path(forged_scope['history']['failure_seal']['path']).read_bytes())
    seal['original_api_exact_revision_count'] = seal['original_api_total_count']
    seal_path = Path(forged_scope['history']['root']) / 'self-endorsed-seal.json'
    seal_path.write_bytes(canonical_json_bytes(seal))
    forged_root = digest(seal_path.read_bytes())
    assert forged_root != fixture.trusted_root
    forged_scope['history']['failure_seal'] = dict(path=str(seal_path), sha256=forged_root)
    forged_scope['producer_binding']['failure_seal_digest'] = forged_root
    fixture.descriptor['packet'] = fixture.contract.prepare_context_readback_packet(**forged_scope)
    before = fixture.snapshot()
    # Attacker controls packet/archive references, not the trusted launcher or
    # private current review/action records, which still bind the original seal.
    assert fixture.descriptor['expected_historical_root'] == fixture.trusted_root
    config = json.loads(fixture.config.read_text())
    config['watched_members'] = [ref['path'] for key, ref in forged_scope['history'].items()
        if key not in {'root', 'failure_seal'}]
    fixture.config.write_text(json.dumps(config))
    _readback_initialize_original_child_directly(fixture)
    assert fixture.rows() == []
    assert fixture.snapshot() == before
