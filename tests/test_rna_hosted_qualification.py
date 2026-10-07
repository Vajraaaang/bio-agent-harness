"""Default-off driver input binding and bounded secret-free channel."""
from datetime import datetime, timezone
import pytest
from devbench.study.rna_hosted_qualification import QualificationDriver, main
from tests.support.rna_hosted_trace_fixtures import fake_inputs


class Channel:
    def __init__(self): self.calls=[]
    def request(self, value):
        self.calls.append(value)
        return {key: 0 for key in ('prepared','acknowledged','uncertain','rejected','blocked','mutation_requests','read_requests')}


def test_invalid_manifest_rejected_before_fixture_or_channel(tmp_path):
    manifest,criteria,_=fake_inputs(tmp_path/'out')
    manifest['prompt']='private'
    channel=Channel()
    with pytest.raises(ValueError):
        QualificationDriver(manifest=manifest,criteria=criteria,exporter_channel=channel,
                            fixture_factory=lambda **kwargs: pytest.fail('fixture ran'))
    assert channel.calls==[]


def test_fixture_manifest_drift_rejected_before_dispatch(tmp_path):
    manifest,criteria,_=fake_inputs(tmp_path/'out')
    manifest['fixture_manifest_digest']='e'*64
    with pytest.raises(ValueError):
        QualificationDriver(manifest=manifest,criteria=criteria,exporter_channel=Channel(),
                            fixture_factory=lambda **kwargs: pytest.fail('fixture ran'))


def test_hosted_cannot_run_without_allow_upload_and_channel(tmp_path):
    manifest,criteria,_=fake_inputs(tmp_path/'out')
    manifest['mode']='hosted'
    with pytest.raises((PermissionError,ValueError)):
        QualificationDriver(manifest=manifest,criteria=criteria,exporter_channel=Channel(),
                            fixture_factory=lambda **kwargs: pytest.fail('fixture ran'))


def test_cli_relative_manifest_fails_before_reads():
    with pytest.raises(ValueError):
        main(['--manifest','relative','--criteria','relative','--output','relative'])


def _fault_controls_snapshot(root):
    from hashlib import sha256
    return {str(path.relative_to(root)): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@pytest.mark.parametrize('phase', ['before_persist', 'after_persist_before_send'])
def test_fault_controls_persistence_crash_reuses_actual_fixture(tmp_path, phase):
    import json
    from devbench.study.rna_hosted_qualification import (
        FakeExporterChannel, isolated_fixture_factory, prepare_fake_inputs)

    output = tmp_path / 'driver'
    manifest, criteria = prepare_fake_inputs(output)
    calls = []
    def factory(**kwargs):
        calls.append(kwargs['sample']['fixture_id'])
        return isolated_fixture_factory(**kwargs)
    exporter = tmp_path / 'exporter'
    with FakeExporterChannel(root=exporter) as channel:
        driver = QualificationDriver(manifest=manifest, criteria=criteria,
                                     exporter_channel=channel, fixture_factory=factory)
        channel.request({'command': 'fault', 'kind': phase})
        with pytest.raises(ValueError):
            driver.drain()
        assert calls == ['pre_fit_methods-0']
        assert not list(exporter.glob('mutation-*.json'))
        fixture = json.loads((output / 'fixture-0' / 'fixture-result.json').read_text())
        assert fixture['process_environment'] == {
            'credential_present':False, 'tracing_enabled':False, 'inherited_context':False}
        assert fixture['process_pid'] != channel.process.pid
        before = _fault_controls_snapshot(output / 'fixture-0')
        assert before
    with FakeExporterChannel(root=exporter, restart=True) as resumed:
        resumed.request({'command': 'initialize', 'manifest': manifest, 'criteria': criteria})
        driver.channel = resumed
        state = driver.drain()
        assert state['mutation_requests'] > 0
        assert state['prepared'] == state['uncertain'] == state['rejected'] == 0
        assert calls == ['pre_fit_methods-0']
        assert _fault_controls_snapshot(output / 'fixture-0') == before
        mutations = state['mutation_requests']
        driver.drain()
        assert driver.status()['mutation_requests'] == mutations


@pytest.mark.parametrize('phase', ['accept_then_timeout', 'after_response_before_ack'])
def test_fault_controls_accepted_crash_never_blindly_resends_or_reexecutes(tmp_path, phase):
    import sqlite3
    import time
    from devbench.study.rna_hosted_qualification import (
        FakeExporterChannel, isolated_fixture_factory, prepare_fake_inputs)

    output = tmp_path / 'driver'
    manifest, criteria = prepare_fake_inputs(output)
    calls = []
    def factory(**kwargs):
        calls.append(kwargs['sample']['fixture_id'])
        return isolated_fixture_factory(**kwargs)
    exporter = tmp_path / 'exporter'
    with FakeExporterChannel(root=exporter) as channel:
        driver = QualificationDriver(manifest=manifest, criteria=criteria,
                                     exporter_channel=channel, fixture_factory=factory)
        channel.request({'command': 'fault', 'kind': phase})
        if phase == 'after_response_before_ack':
            with pytest.raises(ValueError):
                driver.drain()
        else:
            assert driver.drain()['uncertain'] == 1
        before = _fault_controls_snapshot(output / 'fixture-0')
        assert len(list(exporter.glob('mutation-*.json'))) == 1
    with FakeExporterChannel(root=exporter, restart=True) as resumed:
        resumed.request({'command': 'initialize', 'manifest': manifest, 'criteria': criteria})
        driver.channel = resumed
        assert driver.status()['mutation_requests'] == 1
        if phase == 'after_response_before_ack':
            # A real crash leaves the owner's actual bounded lease intact.
            # Observe it and prove a competing driver cannot acquire it.
            journal = exporter / 'sample-0' / 'outbox' / 'journal.sqlite'
            with sqlite3.connect(journal) as database:
                expiry = database.execute('SELECT max(expires_at) FROM leases').fetchone()[0]
            assert expiry > time.time()
            with pytest.raises(ValueError):
                driver.drain()
            assert driver.status()['mutation_requests'] == 1
            time.sleep(max(0, expiry - time.time()) + .05)
        for _ in range(2):
            assert driver.drain()['uncertain'] == 1
            assert driver.status()['mutation_requests'] == 1
        assert calls == ['pre_fit_methods-0']
        assert _fault_controls_snapshot(output / 'fixture-0') == before
        # Root is unacknowledged; every descendant and later revision stays held.
        state = driver.status()
        assert state['prepared'] > 0
        assert state['acknowledged'] == 0
        candidate = driver.verify_remote()
        assert candidate['verification_result'] == 'exact_revision_match'
        assert candidate['reviewer_approval_digest'] is None
        assert driver.status()['uncertain'] == 1
        assert calls == ['pre_fit_methods-0']
        assert _fault_controls_snapshot(output / 'fixture-0') == before


def _fault_controls_sanitized_operations(manifest, *, lifecycle=False, child=False):
    """Bounded software-only receipts; no scientific/provider execution."""
    from datetime import datetime, timezone
    from hashlib import sha256
    import time
    from uuid import uuid4
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.rna_langsmith_live import sanitize_live_event
    from devbench.study.rna_langsmith_verify import bind_remote_proof
    from tests.test_rna_trace_contract import event

    root = str(uuid4())
    session = dict(format='devbench.rna-live-trace-session/2', workflow_alias=root,
        workflow_binding_digest='d'*64, data_scope='synthetic',
        source_digest=manifest['source_digest'], runtime_digest=manifest['runtime_digest'],
        policy_digest=manifest['policy_digest'], endpoint='https://api.smith.langchain.com',
        project='fake-qualification', workspace_id=str(uuid4()), project_id=str(uuid4()),
        redaction_version='rna-allowlist/1', event_schema='devbench.rna-trace-event/1',
        allowed_event_fields=['event_id', 'trace_id', 'span_id', 'parent_span_id', 'sequence',
            'attempt', 'role', 'phase', 'kind', 'source_status', 'dispatch_status',
            'capture_status', 'clock', 'metrics', 'reason_code', 'origin'],
        max_spans=manifest['limits']['max_spans'], max_bytes=manifest['limits']['max_bytes'],
        max_delivery_attempts=3, expires_at=manifest['expires_at'], authorize_upload=True)
    receipt = event(trace_id=root, span_id=root, parent_span_id=None, role='coordinator',
        phase='workflow', kind='reservation', source_status='active', dispatch_status='prepared',
        bindings={'workflow':'d'*64, 'source':session['source_digest'],
                  'runtime':session['runtime_digest'], 'policy':session['policy_digest']},
        clock={'utc':datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'monotonic_ns':time.monotonic_ns(), 'process_alias':str(uuid4()), 'timing_basis':'observed'})
    item = sanitize_live_event(receipt, authorized_session_manifest=session,
        capability={'backend_kind':'offline_recorded', 'qualification_status':'offline_qualified',
                    'admitted':True}, allocation=2 if child else 0, actual_children=int(child), operation='start')
    item['payload'] = bind_remote_proof(item['payload'], item['operation'])
    item['operation']['payload_sha256'] = sha256(canonical_json_bytes(item['payload'])).hexdigest()
    operations = [item]
    if child:
        child_receipt = {**receipt, 'event_id':str(uuid4()), 'span_id':str(uuid4()),
                         'parent_span_id':root, 'role':'investigator', 'phase':'invocation'}
        child_item = sanitize_live_event(child_receipt, authorized_session_manifest=session,
            capability={'backend_kind':'offline_recorded', 'qualification_status':'offline_qualified',
                        'admitted':True}, allocation=2, actual_children=1, operation='start')
        child_item['payload'] = bind_remote_proof(child_item['payload'], child_item['operation'])
        child_item['operation']['payload_sha256'] = sha256(canonical_json_bytes(child_item['payload'])).hexdigest()
        operations.append(child_item)
    if lifecycle:
        for revision, status, operation in ((1, 'cancelled', 'annotation'),
                                             (2, 'completed', 'finish')):
            revision_receipt = {**receipt, 'event_id':str(uuid4()), 'sequence':revision,
                'kind':'completion', 'source_status':status, 'dispatch_status':'attempted',
                'clock':{**receipt['clock'], 'utc':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                         'monotonic_ns':time.monotonic_ns()}}
            update = sanitize_live_event(revision_receipt, authorized_session_manifest=session,
                capability={'backend_kind':'offline_recorded', 'qualification_status':'offline_qualified',
                            'admitted':True}, allocation=2 if child else 0, actual_children=int(child), operation=operation,
                start_time=item['payload']['start_time'])
            update['payload'] = bind_remote_proof(update['payload'], update['operation'])
            update['operation']['payload_sha256'] = sha256(canonical_json_bytes(update['payload'])).hexdigest()
            operations.append(update)
    return session, operations


@pytest.mark.parametrize('cap', ['max_spans', 'max_bytes'])
def test_fault_controls_campaign_caps_reject_before_persistence(tmp_path, cap):
    from copy import deepcopy
    from hashlib import sha256
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.rna_hosted_qualification import FakeExporterChannel

    manifest, criteria, _ = fake_inputs(tmp_path / 'output')
    session, operations = _fault_controls_sanitized_operations(manifest, child=True)
    with FakeExporterChannel(root=tmp_path / 'valid-baseline') as baseline:
        baseline.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        assert baseline.request({'command':'prepare', 'session':session, 'operations':operations})['prepared'] == 2
    manifest, criteria, session = deepcopy(manifest), deepcopy(criteria), deepcopy(session)
    manifest['limits'][cap] = criteria['limits'][cap] = session[cap] = 1
    manifest['criteria_digest'] = sha256(canonical_json_bytes(criteria)).hexdigest()
    exporter = tmp_path / 'limited-exporter'
    with FakeExporterChannel(root=exporter) as channel:
        channel.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        with pytest.raises(ValueError):
            channel.request({'command':'prepare', 'session':session, 'operations':operations})
        assert channel.request({'command':'status'})['mutation_requests'] == 0
        assert not list(exporter.glob('sample-*'))


def test_fault_controls_mutation_cap_retains_unfinished_terminal(tmp_path):
    import json
    import sqlite3
    from hashlib import sha256
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.rna_hosted_qualification import FakeExporterChannel

    manifest, criteria, _ = fake_inputs(tmp_path / 'output')
    manifest['limits']['max_mutation_requests'] = criteria['limits']['max_mutation_requests'] = 1
    manifest['criteria_digest'] = sha256(canonical_json_bytes(criteria)).hexdigest()
    session, operations = _fault_controls_sanitized_operations(manifest, lifecycle=True)
    exporter = tmp_path / 'exporter'
    with FakeExporterChannel(root=exporter) as channel:
        channel.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        channel.request({'command':'prepare', 'session':session, 'operations':operations})
        state = channel.request({'command':'drain'})
        assert state['mutation_requests'] == state['acknowledged'] == 1
        assert state['uncertain'] == state['prepared'] == 1
        assert channel.request({'command':'drain'}) == state
    with sqlite3.connect(exporter / 'sample-0' / 'outbox' / 'journal.sqlite') as database:
        terminal = database.execute('SELECT payload,state FROM operations WHERE revision=2').fetchone()
    assert json.loads(terminal[0])['extra']['metadata']['source_status'] == 'completed'
    assert terminal[1] == 'prepared'


def test_fault_controls_expired_delivery_preserves_prepared_terminal(tmp_path):
    from datetime import timedelta
    import sqlite3
    import time
    from devbench.study.rna_hosted_qualification import FakeExporterChannel

    manifest, criteria, _ = fake_inputs(tmp_path / 'output')
    deadline = datetime.now(timezone.utc) + timedelta(seconds=3)
    manifest['expires_at'] = deadline.isoformat()
    session, operations = _fault_controls_sanitized_operations(manifest, lifecycle=True)
    exporter = tmp_path / 'exporter'
    with FakeExporterChannel(root=exporter) as channel:
        channel.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        assert channel.request({'command':'prepare', 'session':session, 'operations':operations})['prepared'] == 3
        time.sleep(max(0, (deadline-datetime.now(timezone.utc)).total_seconds()) + .05)
        with pytest.raises(ValueError):
            channel.request({'command':'drain'})
    with sqlite3.connect(exporter / 'sample-0' / 'outbox' / 'journal.sqlite') as database:
        states = database.execute('SELECT state FROM operations').fetchall()
    assert states == [('prepared',)] * 3
    assert not list(exporter.glob('mutation-*.json'))


def test_fault_controls_fifo_input_rejected_without_blocking(tmp_path):
    import os
    import subprocess
    import sys
    import time
    from devbench.study import rna_hosted_qualification as qualification

    fifo = tmp_path / 'untrusted-manifest.fifo'
    os.mkfifo(fifo)
    started = time.monotonic()
    process = subprocess.run(
        [sys.executable, '-I', '-B', qualification.__file__, '--manifest', str(fifo),
         '--criteria', str(tmp_path / 'unused-criteria.json'), '--output', str(tmp_path / 'output')],
        cwd=tmp_path, env=qualification.minimal_environment(), close_fds=True,
        capture_output=True, text=True, timeout=1)
    assert time.monotonic() - started < 1
    assert process.returncode != 0
    assert 'Bounded regular JSON file required' in process.stderr


def test_fault_controls_duplicate_stale_and_late_terminal_revisions_are_retained(tmp_path):
    from copy import deepcopy
    import json
    import sqlite3
    from devbench.study.rna_hosted_qualification import FakeExporterChannel

    manifest, criteria, _ = fake_inputs(tmp_path / 'output')
    session, operations = _fault_controls_sanitized_operations(manifest, lifecycle=True)
    exporter = tmp_path / 'exporter'
    with FakeExporterChannel(root=exporter) as channel:
        channel.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        request = {'command':'prepare', 'session':session, 'operations':operations}
        prepared = channel.request(request)
        assert prepared['prepared'] == 3
        assert channel.request(deepcopy(request)) == prepared
        conflict = deepcopy(request)
        conflict['operations'][1]['operation']['revision'] = 0
        with pytest.raises(ValueError):
            channel.request(conflict)
        assert channel.request({'command':'status'}) == prepared
        state = channel.request({'command':'drain'})
        assert state['acknowledged'] == 3
        assert state['mutation_requests'] == 3
        channel.request(deepcopy(request))
        assert channel.request({'command':'drain'})['mutation_requests'] == 3
    with sqlite3.connect(exporter / 'sample-0' / 'outbox' / 'journal.sqlite') as database:
        rows = database.execute('SELECT revision,payload,state FROM operations ORDER BY revision').fetchall()
    assert [revision for revision, _, _ in rows] == [0, 1, 2]
    assert [json.loads(payload)['extra']['metadata']['source_status']
            for _, payload, _ in rows] == ['active', 'cancelled', 'completed']
    assert all(state == 'acknowledged' for _, _, state in rows)


@pytest.mark.parametrize('field', ['objective', 'free_text', 'errors', 'prompt', 'response',
    'gene_ids', 'sample_ids', 'donor_ids', 'paths', 'grants', 'headers', 'matrix', 'identity_hash'])
def test_fault_controls_payload_canaries_rejected_before_persistence(tmp_path, field):
    from hashlib import sha256
    from devbench.study.contracts import canonical_json_bytes
    from devbench.study.rna_hosted_qualification import FakeExporterChannel

    manifest, criteria, _ = fake_inputs(tmp_path / 'output')
    session, operations = _fault_controls_sanitized_operations(manifest)
    # A missing installed module must fail this control, rather than making an
    # unrelated import error look like successful privacy rejection.
    with FakeExporterChannel(root=tmp_path / 'valid-baseline') as baseline:
        baseline.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        accepted = baseline.request({'command':'prepare', 'session':session, 'operations':operations})
        assert accepted['prepared'] == 1
        assert accepted['mutation_requests'] == 0
    canary = 'UNAUTHORIZED-SYNTHETIC-CANARY'
    operations[0]['payload']['extra']['metadata'][field] = (
        sha256(canary.encode()).hexdigest() if field == 'identity_hash' else canary)
    operations[0]['operation']['payload_sha256'] = sha256(
        canonical_json_bytes(operations[0]['payload'])).hexdigest()
    exporter = tmp_path / 'exporter'
    with FakeExporterChannel(root=exporter) as channel:
        channel.request({'command':'initialize', 'manifest':manifest, 'criteria':criteria})
        with pytest.raises(ValueError):
            channel.request({'command':'prepare', 'session':session, 'operations':operations})
        assert channel.request({'command':'status'})['mutation_requests'] == 0
        assert not list(exporter.glob('sample-*'))
        assert not list(exporter.glob('mutation-*.json'))


@pytest.mark.parametrize('field',['source_digest','runtime_digest','policy_digest'])
def test_manifest_bound_to_independently_observed_identity(tmp_path,monkeypatch,field):
    from devbench.study import rna_hosted_qualification as module
    manifest,criteria,identity=fake_inputs(tmp_path/'out')
    identity={key:identity[key] for key in module.IDENTITY_KEYS if key not in ('criteria_digest','fixture_manifest_digest')}
    identity['protected_paths']=[]
    monkeypatch.setattr(module,'installed_identity',lambda:dict(identity))
    manifest[field]='e'*64
    with pytest.raises(ValueError):
        module.QualificationDriver(manifest=manifest,criteria=criteria,exporter_channel=Channel(),
            fixture_factory=lambda **kwargs:pytest.fail('Identity drift launched fixture'))
    assert not (tmp_path/'out').exists()


def test_expiry_checked_before_recovering_or_producing_fixture(tmp_path,monkeypatch):
    from devbench.study import rna_hosted_qualification as module
    manifest,criteria,identity=fake_inputs(tmp_path/'out')
    identity={key:identity[key] for key in module.IDENTITY_KEYS if key not in ('criteria_digest','fixture_manifest_digest')}
    identity['protected_paths']=[]
    monkeypatch.setattr(module,'installed_identity',lambda:dict(identity))
    driver=module.QualificationDriver(manifest=manifest,criteria=criteria,exporter_channel=Channel(),
        fixture_factory=lambda **kwargs:pytest.fail('Expired authorization launched fixture'))
    driver.started-=901
    with pytest.raises(PermissionError):driver.drain()
