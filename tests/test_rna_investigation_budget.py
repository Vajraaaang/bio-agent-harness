"""Offline budget proofs. No provider, native CLI or scientific fit is launched."""

import hashlib
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time
from threading import Event

import pytest

from devbench.analysis.workflow_operations import (
    OperationBlocked, OperationConflict, WorkflowOperationLedger,
)


def _policy(kind='hard_total_tokens', **updates):
    value = {'format': 'devbench.model-budget-policy/1', 'kind': kind,
             'max_total_tokens': 120 if kind == 'hard_total_tokens' else None,
             'soft_token_target': 120 if kind == 'bounded_observed_tokens' else None,
             'max_calls': 12, 'max_total_seconds': 120, 'max_context_bytes': 65536,
             'memory_mb': 512, 'max_children': 2,
             'reconciliation': {'tokens': 20, 'calls': 1, 'seconds': 10, 'context_bytes': 4096}}
    return {**value, **updates}


def _capability(**updates):
    return {'backend': 'enforced-offline-fixture', 'backend_digest': 'b' * 64,
            'runtime_digest': 'c' * 64, 'source_digest': 'd' * 64,
            'capability_status': 'offline_qualified', 'enforced_total_token_cap': True,
            'usage_capture': 'verified_complete', 'context_bytes_enforced': True,
            'outer_timeout_enforced': True, 'memory_enforced': True,
            'request_count_enforced': True, 'all_attributable_requests_accounted': True,
            'parallel_boundary_qualified': True,
            'child_command_or_numerical_tools_allowed': False, **updates}


def _open(tmp_path, *, policy=None, capability=None, identity=None):
    ledger = WorkflowOperationLedger(tmp_path / 'operations.sqlite')
    ledger.open_campaign('campaign', session_id='parent', policy=policy or _policy(),
                         capability=capability or _capability(), identity=identity or {'analysis': 'fixture-v1'})
    return ledger


def _request(node, **updates):
    return {'format': 'devbench.model-call-request/1', 'parent_session_id': 'parent',
            'session_id': 'session-' + node, 'invocation_id': 'invocation-' + node,
            'backend_digest': 'b' * 64, 'runtime_digest': 'c' * 64,
            'prompt_digest': 'e' * 64, 'schema_digest': 'f' * 64,
            'context_digest': 'a' * 64, 'input': {'task': node}, **updates}


def _reserve(ledger, node='parent-generation', *, tokens=30, calls=1, seconds=10,
             purpose='parent_generation', child_id=None, attempt=0, request=None, **limits):
    return ledger.reserve_model_call('campaign', node, attempt, request or _request(node),
        limits={'tokens': tokens, 'calls': calls, 'seconds': seconds,
                'context_bytes': 4096, 'memory_mb': 512, **limits},
        purpose=purpose, child_id=child_id)


def _capture(tmp_path, reservation, *, tokens=12, status='completed', calls=1,
             seconds=2, launch_confirmed=True, **updates):
    from devbench.study.model_telemetry import normalize_usage
    request = reservation.request
    native = {'input_tokens': 8, 'output_tokens': 4,
              'cached_input_tokens': 3, 'reasoning_tokens': 2}
    if tokens is None:
        native = {}
    elif tokens != 12:
        native = {'input_tokens': tokens, 'output_tokens': 0}
    usage = normalize_usage(native)
    payload = {'format': 'devbench.model-call-capture/1',
               'reservation_ref': reservation.ref, 'request_digest': reservation.request_digest,
               **{key: request[key] for key in ('session_id', 'invocation_id', 'backend_digest',
                   'runtime_digest', 'prompt_digest', 'schema_digest', 'context_digest')},
               'status': status, 'usage': usage, 'actual_calls': calls,
               'elapsed_seconds': seconds, 'launch_confirmed': launch_confirmed,
               'native_session_ids': ['native-' + request['invocation_id']] if launch_confirmed else [], **updates}
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    filename = 'capture-' + reservation.ref.split(':')[-1] + '-' + hashlib.sha256(encoded).hexdigest()[:16] + '.json'
    path = tmp_path / filename
    path.write_bytes(encoded)
    reference = {'artifact_id': filename, 'sha256': hashlib.sha256(encoded).hexdigest(),
                 'artifact_type': 'model_capture', 'size_bytes': len(encoded)}

    def verify(ref, retained_reservation):
        actual = (tmp_path / ref['artifact_id']).read_bytes()
        if hashlib.sha256(actual).hexdigest() != ref['sha256'] or len(actual) != ref['size_bytes']:
            raise OperationConflict('Capture bytes changed')
        return {**json.loads(actual), 'capture_ref': ref}
    return reference, verify


def _settle(tmp_path, ledger, reservation, **updates):
    capture, verify = _capture(tmp_path, reservation, **updates)
    return ledger.settle_model_call(reservation, capture=capture, verify_capture=verify)


def _fork(ledger, child_ids=('child-1', 'child-2'), stage='pre_fit_methods', **updates):
    return ledger.reserve_investigation_fork('campaign', fork_id='fork-1', stage=stage,
        child_ids=list(child_ids), identity={'packet': 'fixture-v1', **updates})


def test_shared_campaign_charges_parent_only_and_all_attributable_attempts(tmp_path):
    ledger = _open(tmp_path, policy=_policy(max_total_tokens=300))
    purposes = ['parent_generation', 'preflight', 'capability_resolution', 'trial_design',
                'reviewer', 'failed_attempt', 'retry', 'synthesis', 'parent_reconciliation']
    for purpose in purposes:
        reservation = _reserve(ledger, purpose, purpose=purpose)
        ledger.activate_model_call(reservation)
        _settle(tmp_path, ledger, reservation, status='failed' if purpose == 'failed_attempt' else 'completed')
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_spent_tokens'] == 12 * len(purposes)
    assert campaign['verified_calls'] == len(purposes)
    assert campaign['outstanding_reserved_tokens'] == 0
    assert campaign['qualification'] == 'complete'
    assert len(ledger.replay('campaign')) == len(purposes)


def test_reservation_and_v1_operation_are_atomic_and_exact_key_is_idempotent(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    assert reservation.status == 'reserved'
    assert ledger.get('campaign', 'parent-generation', 0)['request'] == reservation.request
    assert _reserve(ledger) == reservation
    assert len(ledger.campaign_reservations('campaign')) == 1
    with pytest.raises(OperationConflict):
        _reserve(ledger, request=_request('parent-generation', prompt_digest='1' * 64))
    with pytest.raises(OperationConflict):
        _reserve(ledger, tokens=31)


def test_reconciliation_capacity_cannot_be_consumed_by_children(tmp_path):
    ledger = _open(tmp_path, policy=_policy(max_total_tokens=70))
    _fork(ledger)
    first = _reserve(ledger, 'one', tokens=30, purpose='investigation', child_id='child-1')
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, 'two', tokens=30, purpose='investigation', child_id='child-2')
    assert error.value.state == 'budget_exhausted'
    assert ledger.get('campaign', 'two', 0) is None
    final = _reserve(ledger, 'final', tokens=20, purpose='parent_reconciliation')
    assert final.status == 'reserved'
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == first.limits['tokens'] + 20


@pytest.mark.parametrize('field,value', [('enforced_total_token_cap', False),
    ('usage_capture', 'posthoc_optional'), ('parallel_boundary_qualified', False),
    ('request_count_enforced', False), ('all_attributable_requests_accounted', False)])
def test_unsupported_strict_backend_blocks_before_any_operation_or_launch(tmp_path, field, value):
    ledger = WorkflowOperationLedger(tmp_path / 'operations.sqlite')
    with pytest.raises(OperationBlocked) as error:
        ledger.open_campaign('campaign', session_id='parent', policy=_policy(),
            capability=_capability(**{field: value}), identity={'analysis': 'v1'})
    assert error.value.state == 'blocked_backend_capability'
    assert ledger.replay('campaign') == []


def test_bounded_policy_is_explicit_and_never_claims_a_hard_guarantee(tmp_path):
    ledger = _open(tmp_path, policy=_policy('bounded_observed_tokens'),
                   capability=_capability(enforced_total_token_cap=False))
    assert ledger.get_campaign('campaign')['hard_token_guarantee'] is False
    assert ledger.get_campaign('campaign')['policy']['kind'] == 'bounded_observed_tokens'
    with pytest.raises(OperationConflict):
        ledger.open_campaign('campaign', session_id='parent', policy=_policy(),
                             capability=_capability(), identity={'analysis': 'fixture-v1'})


@pytest.mark.parametrize('limits', [{'calls': 13}, {'seconds': 121},
    {'context_bytes': 65537}, {'memory_mb': 513}])
def test_each_operational_limit_blocks_without_grant(tmp_path, limits):
    ledger = _open(tmp_path)
    with pytest.raises((OperationBlocked, ValueError)):
        _reserve(ledger, **limits)
    assert ledger.campaign_reservations('campaign') == []
    assert ledger.replay('campaign') == []


def test_fork_is_one_stage_and_coordinator_allocates_at_most_two_children(tmp_path):
    ledger = _open(tmp_path)
    first = _fork(ledger)
    assert _fork(ledger) == first
    with pytest.raises(OperationConflict):
        _fork(ledger, stage='post_fit_explanation')
    with pytest.raises(OperationConflict):
        ledger.reserve_investigation_fork('campaign', fork_id='another', stage='pre_fit_methods',
                                          child_ids=['child-3'], identity={})
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'third', child_id='child-3')


def test_child_without_coordinator_fork_cannot_grant_itself_calls(tmp_path):
    ledger = _open(tmp_path)
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, child_id='self-granted')
    assert error.value.state == 'child_not_allocated'


def test_verified_usage_counts_cached_and_reasoning_subsets_once(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    record = _settle(tmp_path, ledger, reservation)
    assert record['status'] == 'completed'
    assert record['capture']['usage']['normalized_total_tokens'] == 12
    assert ledger.get_campaign('campaign')['verified_spent_tokens'] == 12
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 0
    assert _settle(tmp_path, ledger, reservation) == record


def test_failed_call_charges_actual_usage(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    assert _settle(tmp_path, ledger, reservation, status='failed')['status'] == 'failed'
    assert ledger.get_campaign('campaign')['verified_spent_tokens'] == 12


@pytest.mark.parametrize('state', ['active', 'uncertain'])
def test_missing_usage_retains_worst_case_and_blocks_further_dispatch(tmp_path, state):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    if state == 'uncertain':
        ledger.mark_model_call_uncertain(reservation)
    receipt = _settle(tmp_path, ledger, reservation, tokens=None)
    assert receipt['status'] == 'uncertain'
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_spent_tokens'] == 0
    assert campaign['outstanding_reserved_tokens'] == 30
    assert campaign['unresolved_count'] == 1
    assert campaign['qualification'] == 'incomplete'
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, 'next')
    assert error.value.state == 'unresolved_usage'


def test_unverified_capture_keeps_uncertainty_and_never_refunds(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    capture, verify = _capture(tmp_path, reservation)
    (tmp_path / capture['artifact_id']).write_text('{}')
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=capture, verify_capture=verify)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 30


@pytest.mark.parametrize('changed', [{'session_id': 'wrong-session'}, {'runtime_digest': '1' * 64},
    {'request_digest': '2' * 64}, {'invocation_id': 'wrong-invocation'},
    {'schema_digest': '3' * 64}, {'context_digest': '4' * 64}])
def test_capture_identity_mismatch_is_not_success(tmp_path, changed):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    capture, verify = _capture(tmp_path, reservation, **changed)
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=capture, verify_capture=verify)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'


def test_overcap_backend_violation_is_preserved_and_stops_campaign(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger, tokens=20)
    ledger.activate_model_call(reservation)
    result = _settle(tmp_path, ledger, reservation, tokens=21)
    assert result['capture']['usage']['normalized_total_tokens'] == 21
    campaign = ledger.get_campaign('campaign')
    assert campaign['qualification'] == 'violated'
    assert campaign['reason'] == 'backend_limit_violation'
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'next')


def test_bounded_target_overrun_is_retained_not_an_enforced_cap_pass(tmp_path):
    ledger = _open(tmp_path, policy=_policy('bounded_observed_tokens', soft_token_target=35),
                   capability=_capability(enforced_total_token_cap=False))
    reservation = _reserve(ledger, tokens=10)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, tokens=36)
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_spent_tokens'] == 36
    assert campaign['hard_token_guarantee'] is False
    assert campaign['reason'] == 'soft_token_target_exceeded'
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'next')


def test_cancellation_retains_active_tails_but_releases_only_unlaunched_calls(tmp_path):
    ledger = _open(tmp_path)
    active = _reserve(ledger, 'active')
    reserved = _reserve(ledger, 'reserved')
    ledger.activate_model_call(active)
    ledger.cancel_campaign('campaign')
    assert ledger.get_model_reservation(active)['status'] == 'active'
    assert ledger.get_model_reservation(reserved)['status'] == 'cancelled'
    assert ledger.get_model_reservation(reserved)['charge_basis'] == 'verified_unlaunched'
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 30
    assert _settle(tmp_path, ledger, active)['status'] == 'completed'
    assert ledger.get_campaign('campaign')['verified_spent_tokens'] == 12
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'late')


def test_activation_is_a_single_owner_transition_and_recovery_cannot_redispatch(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    assert ledger.activate_model_call(reservation).status == 'active'
    with pytest.raises(OperationBlocked) as error:
        WorkflowOperationLedger(ledger.path).activate_model_call(reservation)
    assert error.value.state == 'uncertain'
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'
    capture, verify = _capture(tmp_path, reservation)
    record = ledger.reconcile_model_call(reservation, capture=capture, verify_capture=verify)
    assert record['status'] == 'completed'


def _process_reserve(path, start, output, number):
    # Actual independent OS processes, each with its own SQLite connection.
    start.wait(10)
    for _ in range(100):
        try:
            ledger = WorkflowOperationLedger(path)
            _reserve(ledger, 'process-' + str(number), tokens=30)
            output.put('reserved')
            return
        except OperationBlocked as error:
            if error.state == 'ledger_busy':
                time.sleep(.005)
                continue
            output.put(error.state)
            return
    output.put('ledger_busy')


def test_independent_processes_cannot_reserve_past_authorized_aggregate(tmp_path):
    ledger = _open(tmp_path, policy=_policy(max_total_tokens=80))
    context = multiprocessing.get_context('spawn')
    start, output = context.Event(), context.Queue()
    workers = [context.Process(target=_process_reserve,
               args=(str(ledger.path), start, output, number)) for number in range(6)]
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    results = [output.get(timeout=5) for _ in workers]
    assert results.count('reserved') == 2
    assert results.count('budget_exhausted') == 4
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 60
    assert len(ledger.replay('campaign')) == 2


def test_campaign_and_reservation_replay_are_readonly(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in tmp_path.rglob('*') if path.is_file()}
    reader = WorkflowOperationLedger(ledger.path, readonly=True)
    assert reader.get_campaign('campaign')['policy']['kind'] == 'hard_total_tokens'
    assert reader.get_model_reservation(reservation)['status'] == 'reserved'
    assert len(reader.campaign_reservations('campaign')) == 1
    for mutation in (lambda: reader.activate_model_call(reservation),
                     lambda: reader.cancel_campaign('campaign'),
                     lambda: _reserve(reader, 'next'),
                     lambda: _fork(reader)):
        with pytest.raises(OperationBlocked) as error:
            mutation()
        assert error.value.state == 'readonly'
    after = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
             for path in tmp_path.rglob('*') if path.is_file()}
    assert before == after


@pytest.mark.parametrize('changed', [{'runtime_digest': '1' * 64}, {'input': 'x' * 70000},
    {'context_digest': float('nan')}])
def test_invalid_or_changed_request_cannot_be_reserved(tmp_path, changed):
    ledger = _open(tmp_path)
    with pytest.raises((OperationConflict, ValueError)):
        _reserve(ledger, request=_request('parent-generation', **changed))
    assert ledger.campaign_reservations('campaign') == []


def test_failed_prelaunch_is_verified_zero_use_and_does_not_create_unknown_spend(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    record = _settle(tmp_path, ledger, reservation, tokens=0, calls=0,
                     launch_confirmed=False, status='failed')
    assert record['status'] == 'failed'
    assert ledger.get_campaign('campaign')['verified_spent_tokens'] == 0
    assert ledger.get_campaign('campaign')['unresolved_count'] == 0
    assert _reserve(ledger, 'next').status == 'reserved'


@pytest.mark.parametrize('mutation', [
    {'normalized_total_tokens': 17}, {'cached_input_tokens': 9}, {'reasoning_tokens': 5},
    {'native_usage': {'input_tokens': 7, 'output_tokens': 4}},
])
def test_forged_or_double_counted_usage_is_rejected(tmp_path, mutation):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    ref, verify = _capture(tmp_path, reservation)
    def changed(ref, retained):
        value = verify(ref, retained)
        value['usage'].update(mutation)
        return value
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=ref, verify_capture=changed)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'


def test_two_distinct_invocations_cannot_reuse_captured_native_session(tmp_path):
    ledger = _open(tmp_path)
    first = _reserve(ledger, 'first')
    ledger.activate_model_call(first)
    _settle(tmp_path, ledger, first, native_session_ids=['actual-native-session'])
    second = _reserve(ledger, 'second')
    ledger.activate_model_call(second)
    ref, verify = _capture(tmp_path, second, native_session_ids=['actual-native-session'])
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(second, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(second)['status'] == 'uncertain'


def test_campaign_total_wall_time_cannot_restart_after_idle_or_reopen(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _open(tmp_path, policy=_policy(max_total_seconds=20))
    monkeypatch.setattr(operations.time, 'time', lambda: 1021)
    with pytest.raises(OperationBlocked) as error:
        _reserve(WorkflowOperationLedger(ledger.path))
    assert error.value.state == 'campaign_deadline_exhausted'
    assert ledger.campaign_reservations('campaign') == []


@pytest.mark.parametrize('commit_happened', [False, True])
def test_settlement_commit_loss_never_launches_or_refunds_unknown_work(tmp_path, monkeypatch, commit_happened):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    original = ledger._database
    ref, verify = _capture(tmp_path, reservation)
    triggered = []

    @contextmanager
    def fail_at_commit():
        settled = False
        with original() as connection:
            yield connection
            row = connection.execute('SELECT status FROM model_reservations WHERE ref=?', (reservation.ref,)).fetchone()
            settled = row and row[0] == 'completed' and not triggered
            if settled and not commit_happened:
                triggered.append('before_commit')
                raise OSError('forced settlement commit loss')
        if settled and commit_happened:
            triggered.append('after_commit')
            raise OSError('forced acknowledgement loss after commit')

    monkeypatch.setattr(ledger, '_database', fail_at_commit)
    with pytest.raises(OSError):
        ledger.settle_model_call(reservation, capture=ref, verify_capture=verify)
    monkeypatch.setattr(ledger, '_database', original)
    current = ledger.get_model_reservation(reservation)
    assert current['status'] == ('completed' if commit_happened else 'uncertain')
    assert ledger.get_campaign('campaign')['verified_spent_tokens'] == (12 if commit_happened else 0)
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == (0 if commit_happened else 30)
    assert ledger.reconcile_model_call(reservation, capture=ref, verify_capture=verify)['status'] == 'completed'
    assert len(ledger.campaign_reservations('campaign')) == 1


def test_repeated_frozen_child_call_is_replayed_without_new_budget_grant(tmp_path):
    ledger = _open(tmp_path)
    _fork(ledger, ('child-1',))
    first = _reserve(ledger, 'child', child_id='child-1', purpose='investigation')
    ledger.activate_model_call(first)
    _settle(tmp_path, ledger, first)
    reused = _reserve(ledger, 'child', child_id='child-1', purpose='investigation')
    assert reused.status == 'completed'
    assert ledger.get_campaign('campaign')['verified_spent_tokens'] == 12
    assert len(ledger.campaign_reservations('campaign')) == 1


def test_malformed_model_reservation_capture_cannot_be_replayed_as_spend(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation)
    with sqlite3.connect(str(ledger.path)) as connection:
        connection.execute("UPDATE model_reservations SET capture_json='{}'")
    with pytest.raises(OperationConflict):
        ledger.get_campaign('campaign')


@pytest.mark.parametrize('identity_key', ['session_id', 'invocation_id'])
def test_invocation_identity_is_one_to_one_with_its_reservation(tmp_path, identity_key):
    ledger = _open(tmp_path)
    first = _reserve(ledger, 'first')
    assert _reserve(ledger, 'first') == first
    with pytest.raises(OperationConflict):
        _reserve(ledger, 'second', request=_request('second', **{identity_key: first.request[identity_key]}))
    assert len(ledger.campaign_reservations('campaign')) == 1
    assert ledger.get('campaign', 'second', 0) is None


def test_new_attempt_must_have_a_distinct_captured_invocation_identity(tmp_path):
    ledger = _open(tmp_path)
    first = _reserve(ledger, 'parent')
    with pytest.raises(OperationConflict):
        _reserve(ledger, 'parent', attempt=1)
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 30


def test_a_verifier_claim_without_an_immutable_capture_reference_is_rejected(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    actual, verify = _capture(tmp_path, reservation)
    claim = {'claimed_verified': True}
    def fabricated(ref, retained):
        return {**verify(actual, retained), 'capture_ref': claim}
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=claim, verify_capture=fabricated)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'


@pytest.mark.parametrize('calls,sessions', [(0, ['native-one']),
    (0, ['native-one', 'native-two']), (1, ['native-one', 'native-two'])])
def test_launched_capture_cannot_understate_distinct_session_call_counts(tmp_path, calls, sessions):
    ledger = _open(tmp_path, policy=_policy(max_calls=3))
    reservation = _reserve(ledger, calls=2)
    ledger.activate_model_call(reservation)
    ref, verify = _capture(tmp_path, reservation, calls=calls, native_session_ids=sessions)
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_calls'] == 0
    assert campaign['outstanding_reserved_calls'] == 2
    assert campaign['outstanding_reserved_tokens'] == 30
    assert campaign['qualification'] == 'incomplete'
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, 'next')
    assert error.value.state == 'unresolved_usage'


@pytest.mark.parametrize('calls', [2, 3])
def test_preflight_action_and_observed_retries_charge_all_actual_calls(tmp_path, calls):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger, calls=calls)
    ledger.activate_model_call(reservation)
    receipt = _settle(tmp_path, ledger, reservation, calls=calls,
                      native_session_ids=['fixture-native-preflight', 'fixture-native-action'])
    assert receipt['status'] == 'completed'
    assert ledger.get_campaign('campaign')['verified_calls'] == calls
    assert ledger.get_campaign('campaign')['outstanding_reserved_calls'] == 0


def test_zero_use_prelaunch_cancellation_is_explicit_and_refundable(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    receipt = _settle(tmp_path, ledger, reservation, tokens=0, calls=0, seconds=0,
                      launch_confirmed=False, native_session_ids=[], status='cancelled')
    assert receipt['status'] == 'cancelled'
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_calls'] == 0
    assert campaign['verified_spent_tokens'] == 0
    assert campaign['outstanding_reserved_calls'] == 0
    assert campaign['outstanding_reserved_tokens'] == 0
    assert campaign['unresolved_count'] == 0
    assert _reserve(ledger, 'next').status == 'reserved'


@pytest.mark.parametrize('updates', [{'calls': 1}, {'tokens': 1}, {'native_session_ids': ['native-one']}])
def test_unlaunched_receipt_cannot_claim_session_tokens_or_calls(tmp_path, updates):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ref, verify = _capture(tmp_path, reservation, **{
        'tokens': 0, 'calls': 0, 'seconds': 0, 'launch_confirmed': False,
        'native_session_ids': [], 'status': 'cancelled', **updates})
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'
    assert ledger.get_campaign('campaign')['outstanding_reserved_calls'] == 1


def test_new_reservation_duration_must_fit_absolute_campaign_remainder(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _open(tmp_path, policy=_policy(max_total_seconds=20))
    monkeypatch.setattr(operations.time, 'time', lambda: 1015)
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, seconds=10)
    assert error.value.state == 'call_exceeds_campaign_deadline'
    assert ledger.campaign_reservations('campaign') == []
    assert ledger.replay('campaign') == []


def test_launch_deadline_is_readonly_absolute_and_cannot_restart_on_reopen(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _open(tmp_path, policy=_policy(max_total_seconds=20))
    reservation = _reserve(ledger, seconds=10, purpose='parent_reconciliation')
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
              for path in tmp_path.rglob('*') if path.is_file()}
    monkeypatch.setattr(operations.time, 'time', lambda: 1015)
    reader = WorkflowOperationLedger(ledger.path, readonly=True)
    assert reader.get_campaign('campaign')['deadline_epoch'] == 1020
    assert reader.model_call_deadline(reservation) == 1020
    assert reader.model_call_remaining(reservation) == 5
    assert reader.model_call_remaining('campaign', 'parent-generation', 0) == 5
    after = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
             for path in tmp_path.rglob('*') if path.is_file()}
    assert before == after
    active = ledger.activate_model_call(reservation)
    assert active.status == 'active'
    assert ledger.model_call_remaining(active) == 5
    monkeypatch.setattr(operations.time, 'time', lambda: 1021)
    assert reader.model_call_remaining(active) == 0


def test_remaining_launch_time_is_zero_for_cancelled_and_uncertain_calls(tmp_path):
    ledger = _open(tmp_path)
    reserved = _reserve(ledger, 'reserved')
    active = _reserve(ledger, 'active')
    ledger.activate_model_call(active)
    ledger.mark_model_call_uncertain(active)
    assert ledger.model_call_remaining(active) == 0
    ledger.cancel_campaign('campaign')
    assert ledger.model_call_remaining(reserved) == 0


def test_actual_native_session_cannot_be_reused_by_another_campaign(tmp_path):
    ledger = _open(tmp_path)
    first = _reserve(ledger, 'first')
    ledger.activate_model_call(first)
    _settle(tmp_path, ledger, first, native_session_ids=['one-real-native-session'])
    ledger.open_campaign('other-campaign', session_id='other-parent', policy=_policy(),
                         capability=_capability(), identity={'analysis': 'other-fixture'})
    second = ledger.reserve_model_call('other-campaign', 'second', 0,
        _request('second', parent_session_id='other-parent'), limits=first.limits,
        purpose='parent_generation')
    ledger.activate_model_call(second)
    ref, verify = _capture(tmp_path, second, native_session_ids=['one-real-native-session'])
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(second, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(second)['status'] == 'uncertain'
    assert ledger.get_campaign('other-campaign')['outstanding_reserved_calls'] == 1


def test_blocked_native_admission_cannot_advertise_hard_guarantee(tmp_path):
    ledger = WorkflowOperationLedger(tmp_path / 'operations.sqlite')
    with pytest.raises(OperationBlocked):
        ledger.open_campaign('campaign', session_id='parent', policy=_policy(),
            capability=_capability(enforced_total_token_cap=False,
                                   capability_status='source_inspected_not_multiagent_qualified'),
            identity={'analysis': 'fixture-v1'})
    assert ledger.get_campaign('campaign')['hard_token_guarantee'] is False


@pytest.mark.parametrize('kind,reason', [('hard_total_tokens', 'backend_limit_violation'),
    ('bounded_observed_tokens', 'soft_token_target_exceeded')])
def test_reopen_blocked_campaign_preserves_its_exact_blocking_reason(tmp_path, kind, reason):
    policy = _policy(kind, soft_token_target=35) if kind == 'bounded_observed_tokens' else _policy()
    capability = _capability(enforced_total_token_cap=kind == 'hard_total_tokens')
    ledger = _open(tmp_path, policy=policy, capability=capability)
    reservation = _reserve(ledger, tokens=10)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, tokens=36)
    with pytest.raises(OperationBlocked) as error:
        ledger.open_campaign('campaign', session_id='parent', policy=policy,
                             capability=capability, identity={'analysis': 'fixture-v1'})
    assert error.value.state == reason


def test_late_first_settlement_preserves_actual_charges_and_marks_policy_violation(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _open(tmp_path)
    reservation = _reserve(ledger, seconds=10, purpose='parent_reconciliation')
    monkeypatch.setattr(operations.time, 'time', lambda: 1119)
    ledger.activate_model_call(reservation)
    assert ledger.model_call_remaining(reservation) == 1
    monkeypatch.setattr(operations.time, 'time', lambda: 1121)
    receipt = _settle(tmp_path, ledger, reservation, tokens=12, calls=1, seconds=2)
    assert receipt['status'] == 'completed'
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_spent_tokens'] == 12
    assert campaign['verified_calls'] == 1
    assert campaign['verified_seconds'] == 2
    assert campaign['reason'] == 'backend_limit_violation'
    assert campaign['qualification'] == 'violated'
    assert campaign['hard_token_guarantee'] is False
    assert _settle(tmp_path, ledger, reservation, tokens=12, calls=1, seconds=2) == receipt


def test_shared_ledger_object_serializes_only_bookkeeping_connections(tmp_path):
    ledger = _open(tmp_path)
    ready, release, attempting = Event(), Event(), Event()
    def hold_bookkeeping_transaction():
        with ledger._database() as connection:
            connection.execute('BEGIN EXCLUSIVE')
            ready.set()
            assert release.wait(5)
    def read_on_other_thread():
        attempting.set()
        return ledger.get_campaign('campaign')
    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(hold_bookkeeping_transaction)
        try:
            assert ready.wait(5)
            reader = pool.submit(read_on_other_thread)
            assert attempting.wait(5)
            time.sleep(.02)
            assert not reader.done(), 'same-object connection raced its bookkeeping transaction'
        finally:
            release.set()
        writer.result(5)
        assert reader.result(5)['status'] == 'open'


def test_other_ledger_object_preserves_explicit_busy_state(tmp_path):
    ledger = _open(tmp_path)
    other = WorkflowOperationLedger(ledger.path)
    with ledger._database() as connection:
        connection.execute('BEGIN EXCLUSIVE')
        with pytest.raises(OperationBlocked) as error:
            other.get_campaign('campaign')
        assert error.value.state == 'ledger_busy'


@pytest.mark.parametrize('updates', [
    {'launch_confirmed': False, 'native_session_ids': [], 'calls': 0, 'tokens': 0, 'status': 'failed'},
    {'native_session_ids': ['replacement-native-session']},
    {'calls': 1}, {'seconds': 1},
])
def test_uncertain_reconciliation_cannot_erase_observed_dispatch(tmp_path, updates):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger, calls=3)
    ledger.activate_model_call(reservation)
    first = _settle(tmp_path, ledger, reservation, tokens=None, calls=2,
                    native_session_ids=['observed-native-session'])
    ref, verify = _capture(tmp_path, reservation, **{
        'tokens': 12, 'calls': 2, 'seconds': 2,
        'native_session_ids': ['observed-native-session'], **updates})
    with pytest.raises(OperationConflict):
        ledger.reconcile_model_call(reservation, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(reservation) == first
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_calls'] == 2
    assert campaign['outstanding_reserved_tokens'] == 30
    assert campaign['unresolved_count'] == 1
    assert campaign['qualification'] == 'incomplete'


def test_uncertain_capture_can_be_enriched_without_losing_prior_observations(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger, calls=3)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, tokens=None, calls=2,
            native_session_ids=['observed-native-session'])
    ref, verify = _capture(tmp_path, reservation, tokens=12, calls=3, seconds=3,
                           native_session_ids=['observed-native-session', 'observed-native-tail'])
    assert ledger.reconcile_model_call(reservation, capture=ref, verify_capture=verify)['status'] == 'completed'
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_calls'] == 3
    assert campaign['verified_spent_tokens'] == 12
    assert campaign['outstanding_reserved_tokens'] == 0


@pytest.mark.parametrize('native', [{}, {'input_tokens': 8},
    {'input_tokens': 8, 'output_tokens': 4, 'reasoning_output_tokens': 5}])
def test_claimed_exact_usage_must_equal_canonical_native_normalization(tmp_path, native):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    claim = {'normalization': 'input_output_subsets/1', 'native_usage': native,
             'normalized_total_tokens': 12, 'input_tokens': 8, 'output_tokens': 4,
             'cached_input_tokens': None, 'reasoning_tokens': None}
    ref, verify = _capture(tmp_path, reservation, usage=claim)
    with pytest.raises(OperationConflict):
        ledger.settle_model_call(reservation, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(reservation)['status'] == 'uncertain'
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 30


@pytest.mark.parametrize('native', [{'input_tokens': 101},
    {'phase_usage': [{'input_tokens': 101, 'output_tokens': 4}, {}]}])
def test_partial_verified_usage_floor_exceeding_lease_is_a_retained_violation(tmp_path, native):
    from devbench.study.model_telemetry import normalize_usage
    ledger = _open(tmp_path, policy=_policy(max_total_tokens=100))
    reservation = _reserve(ledger, tokens=25)
    ledger.activate_model_call(reservation)
    normalized = normalize_usage(native)
    assert normalized['normalized_total_tokens'] is None
    receipt = _settle(tmp_path, ledger, reservation, usage=normalized)
    assert receipt['status'] == 'uncertain'
    campaign = ledger.get_campaign('campaign')
    assert campaign['verified_spent_tokens'] == 0
    assert campaign['verified_token_lower_bound'] >= 101
    assert campaign['outstanding_reserved_tokens'] >= 101
    assert campaign['reason'] == 'backend_limit_violation'
    assert campaign['qualification'] == 'violated'
    assert campaign['hard_token_guarantee'] is False


def test_partial_usage_floor_cannot_be_erased_by_later_zero_usage(tmp_path):
    from devbench.study.model_telemetry import normalize_usage
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    first = _settle(tmp_path, ledger, reservation, usage=normalize_usage({'input_tokens': 8}))
    ref, verify = _capture(tmp_path, reservation, tokens=0)
    with pytest.raises(OperationConflict):
        ledger.reconcile_model_call(reservation, capture=ref, verify_capture=verify)
    assert ledger.get_model_reservation(reservation) == first
    assert ledger.get_campaign('campaign')['verified_token_lower_bound'] >= 8


def test_legacy_multi_parent_reconciliation_hold_remains_dimensionwise(tmp_path):
    reconciliation = {'tokens': 80, 'calls': 4, 'seconds': 40, 'context_bytes': 8192}
    ledger = _open(tmp_path, policy=_policy(max_total_tokens=160, reconciliation=reconciliation))
    reservation = _reserve(ledger, 'reassess', tokens=20, calls=1,
                           purpose='parent_reconciliation')
    campaign = ledger.get_campaign('campaign')
    assert campaign['reconciliation_held']['tokens'] == 60
    assert campaign['reconciliation_held']['calls'] == 3
    assert campaign['reconciliation_held']['seconds'] == 30
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'ordinary', tokens=81)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation)
    assert ledger.get_campaign('campaign')['reconciliation_held']['tokens'] == 60


def _protected_campaign(tmp_path):
    phases = ['method_candidates_reassessment', 'method_capability_resolution',
              'method_trial_design', 'method_recommendation']
    allocations = {phase: {'tokens': 20, 'calls': 1, 'seconds': 10,
                           'context_bytes': 4096, 'memory_mb': 512} for phase in phases}
    policy = _policy(max_total_tokens=160,
        reconciliation={'tokens': 80, 'calls': 4, 'seconds': 40, 'context_bytes': 4096})
    return _open(tmp_path, policy=policy,
        identity={'analysis': 'fixture-v1', 'protected_parent_allocations': allocations})


def test_future_parent_stages_have_distinct_protected_slots(tmp_path):
    ledger = _protected_campaign(tmp_path)
    reassess = _reserve(ledger, 'reassess', tokens=20, purpose='parent_reconciliation',
        request=_request('reassess', purpose='method_candidates_reassessment'))
    assert ledger.get_campaign('campaign')['reconciliation_held']['tokens'] == 60
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'ordinary', tokens=81)
    ledger.activate_model_call(reassess)
    _settle(tmp_path, ledger, reassess)
    resolve = _reserve(ledger, 'resolve', tokens=20, purpose='method_capability_resolution',
        request=_request('resolve', purpose='method_capability_resolution'))
    assert ledger.get_campaign('campaign')['reconciliation_held']['tokens'] == 40
    assert ledger.get_campaign('campaign')['reconciliation_held']['calls'] == 2
    assert resolve.status == 'reserved'
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'reassess-again', tokens=20, purpose='parent_reconciliation',
            request=_request('reassess-again', purpose='method_candidates_reassessment'))


def test_protected_stage_cannot_borrow_another_stage_or_be_claimed_by_a_child(tmp_path):
    ledger = _protected_campaign(tmp_path)
    _fork(ledger, ('child-1',))
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'oversized', tokens=40, purpose='parent_reconciliation',
            request=_request('oversized', purpose='method_candidates_reassessment'))
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'child', tokens=20, child_id='child-1', purpose='investigation',
            request=_request('child', purpose='method_recommendation'))
    assert ledger.campaign_reservations('campaign') == []


def test_unknown_sibling_usage_stops_next_launch_of_an_already_active_slot(tmp_path):
    ledger = _open(tmp_path)
    first = _reserve(ledger, 'first')
    sibling = _reserve(ledger, 'sibling')
    ledger.activate_model_call(first)
    ledger.activate_model_call(sibling)
    assert ledger.model_call_remaining(sibling) > 0
    _settle(tmp_path, ledger, first, tokens=None)
    assert ledger.model_call_remaining(sibling) == 0


def test_unused_protected_stage_capacity_releases_only_after_verified_completion(tmp_path):
    allocation = {'tokens': 20, 'calls': 1, 'seconds': 10, 'context_bytes': 4096, 'memory_mb': 512}
    ledger = _open(tmp_path, policy=_policy(max_total_tokens=100),
        identity={'analysis': 'fixture-v1', 'protected_parent_allocations': {'method_recommendation': allocation}})
    reservation = _reserve(ledger, 'recommend', tokens=10, seconds=5, purpose='parent_reconciliation',
        request=_request('recommend', purpose='method_recommendation'))
    assert ledger.get_campaign('campaign')['reconciliation_held']['tokens'] == 10
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'ordinary-before', tokens=81)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, tokens=8)
    assert ledger.get_campaign('campaign')['reconciliation_held']['tokens'] == 0
    assert _reserve(ledger, 'ordinary-after', tokens=90).status == 'reserved'


def test_all_four_frozen_parent_stages_consume_only_their_own_slots(tmp_path):
    ledger = _protected_campaign(tmp_path)
    stages = list(ledger.get_campaign('campaign')['identity']['protected_parent_allocations'])
    for index, stage in enumerate(stages):
        reservation = _reserve(ledger, stage, tokens=20, purpose='parent_reconciliation',
            request=_request(stage, purpose=stage))
        assert ledger.get_campaign('campaign')['reconciliation_held']['tokens'] == (3 - index) * 20
        ledger.activate_model_call(reservation)
        _settle(tmp_path, ledger, reservation)
    campaign = ledger.get_campaign('campaign')
    assert campaign['reconciliation_held']['tokens'] == 0
    assert campaign['verified_spent_tokens'] == 48
    assert campaign['verified_calls'] == 4


def test_protected_manifest_cannot_exceed_frozen_parent_reserve(tmp_path):
    allocations = {'method_recommendation': {'tokens': 21, 'calls': 1, 'seconds': 10,
                                            'context_bytes': 4096, 'memory_mb': 512}}
    with pytest.raises(ValueError):
        _open(tmp_path, identity={'protected_parent_allocations': allocations})
    ledger = WorkflowOperationLedger(tmp_path / 'operations.sqlite')
    assert ledger.get_campaign('campaign') is None


def test_publication_guard_blocks_cancelled_campaign_before_any_cas(tmp_path):
    ledger = _open(tmp_path)
    ledger.cancel_campaign('campaign')
    accepted = []
    with pytest.raises(OperationBlocked) as error:
        with ledger.publication_guard('campaign'):
            accepted.append('scientific-state')
    assert error.value.state == 'cancelled'
    assert accepted == []


def test_publication_guard_linearizes_bounded_cas_before_overlapping_cancel(tmp_path):
    ledger = _open(tmp_path)
    entered, finish, cancelling = Event(), Event(), Event()
    accepted = []
    def publish():
        with ledger.publication_guard('campaign'):
            assert ledger.get_campaign('campaign')['status'] == 'open'
            entered.set()
            assert finish.wait(5)
            accepted.append('scientific-state')
    def cancel():
        cancelling.set()
        ledger.cancel_campaign('campaign')
    with ThreadPoolExecutor(2) as pool:
        publisher = pool.submit(publish)
        try:
            assert entered.wait(2)
            canceller = pool.submit(cancel)
            assert cancelling.wait(2)
            time.sleep(.02)
            assert not canceller.done()
        finally:
            finish.set()
        publisher.result(5)
        canceller.result(5)
    assert accepted == ['scientific-state']
    assert ledger.get_campaign('campaign')['status'] == 'cancelled'


def test_publication_guard_refuses_unknown_usage_and_preserves_charges(tmp_path):
    ledger = _open(tmp_path)
    reservation = _reserve(ledger)
    ledger.activate_model_call(reservation)
    _settle(tmp_path, ledger, reservation, tokens=None)
    with pytest.raises(OperationBlocked) as error:
        with ledger.publication_guard('campaign'):
            pytest.fail('unknown captured spend granted scientific publication')
    assert error.value.state == 'unresolved_usage'
    assert ledger.get_campaign('campaign')['outstanding_reserved_tokens'] == 30


def test_readonly_publication_guard_is_not_an_authority_grant(tmp_path):
    ledger = _open(tmp_path)
    before = ledger.path.read_bytes()
    reader = WorkflowOperationLedger(ledger.path, readonly=True)
    with pytest.raises(OperationBlocked) as error:
        with reader.publication_guard('campaign'):
            pytest.fail('readonly replay granted publication')
    assert error.value.state == 'readonly'
    assert ledger.path.read_bytes() == before


def test_child_cannot_consume_absolute_wall_window_reserved_for_parent_stages(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    _fork(ledger, ('child-1',))
    monkeypatch.setattr(operations.time, 'time', lambda: 1110)
    assert ledger.get_campaign('campaign')['reconciliation_held']['seconds'] == 40
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, 'child', tokens=20, seconds=10, child_id='child-1', purpose='investigation')
    assert error.value.state == 'call_exceeds_parent_headroom'
    assert ledger.campaign_reservations('campaign') == []


def test_stale_child_slot_cannot_activate_or_launch_through_parent_wall_hold(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    _fork(ledger, ('child-1',))
    reservation = _reserve(ledger, 'child', tokens=20, seconds=10, child_id='child-1', purpose='investigation')
    monkeypatch.setattr(operations.time, 'time', lambda: 1110)
    assert ledger.model_call_remaining(reservation) == 0
    with pytest.raises(OperationBlocked) as error:
        ledger.activate_model_call(reservation)
    assert error.value.state == 'protected_parent_headroom_exhausted'
    assert ledger.get_model_reservation(reservation)['status'] == 'reserved'


def test_protected_stage_consumes_own_wall_share_and_preserves_other_stages(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    monkeypatch.setattr(operations.time, 'time', lambda: 1080)
    reservation = _reserve(ledger, 'reassess', tokens=20, seconds=10, purpose='parent_reconciliation',
        request=_request('reassess', purpose='method_candidates_reassessment'))
    assert ledger.model_call_remaining(reservation) == 10
    ledger.activate_model_call(reservation)
    monkeypatch.setattr(operations.time, 'time', lambda: 1088)
    _settle(tmp_path, ledger, reservation, seconds=8)
    assert ledger.get_campaign('campaign')['reconciliation_held']['seconds'] == 30
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'ordinary', seconds=3)
    ordinary = _reserve(ledger, 'ordinary-small', seconds=2)
    ledger.activate_model_call(ordinary)
    assert ledger.model_call_remaining(ordinary) == 2
    monkeypatch.setattr(operations.time, 'time', lambda: 1089)
    assert ledger.model_call_remaining(ordinary) == 1


def test_current_stage_unused_wall_allocation_is_not_mistaken_for_future_stage(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    allocation = {'tokens': 20, 'calls': 1, 'seconds': 10, 'context_bytes': 4096, 'memory_mb': 512}
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _open(tmp_path, policy=_policy(max_total_seconds=20),
        identity={'protected_parent_allocations': {'method_recommendation': allocation}})
    monkeypatch.setattr(operations.time, 'time', lambda: 1015)
    reservation = _reserve(ledger, 'recommend', tokens=20, seconds=5, purpose='parent_reconciliation',
        request=_request('recommend', purpose='method_recommendation'))
    assert ledger.get_campaign('campaign')['reconciliation_held']['seconds'] == 5
    assert ledger.model_call_remaining(reservation) == 5
    assert ledger.activate_model_call(reservation).status == 'active'


@pytest.mark.parametrize('bad_state', ['deadline', 'violation'])
def test_publication_guard_rejects_current_deadline_and_violation_without_mutation(tmp_path, monkeypatch, bad_state):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _open(tmp_path)
    if bad_state == 'deadline':
        monkeypatch.setattr(operations.time, 'time', lambda: 1121)
    else:
        reservation = _reserve(ledger, tokens=10)
        ledger.activate_model_call(reservation)
        _settle(tmp_path, ledger, reservation, tokens=11)
    before = ledger.path.read_bytes()
    with pytest.raises(OperationBlocked):
        with ledger.publication_guard('campaign'):
            pytest.fail('invalid campaign published scientific state')
    assert ledger.path.read_bytes() == before


def _reserve_all_parent_stages(ledger):
    reservations = []
    for stage in ledger.get_campaign('campaign')['identity']['protected_parent_allocations']:
        reservations.append(_reserve(ledger, stage, tokens=20, seconds=10,
            purpose='parent_reconciliation', request=_request(stage, purpose=stage)))
    return reservations


def test_reserved_unlaunched_parent_leases_still_protect_their_wall_window(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    _fork(ledger, ('child-1',))
    parents = _reserve_all_parent_stages(ledger)
    assert ledger.get_campaign('campaign')['reconciliation_held']['seconds'] == 0
    assert ledger.get_campaign('campaign')['outstanding_reserved_seconds'] == 40
    monkeypatch.setattr(operations.time, 'time', lambda: 1110)
    with pytest.raises(OperationBlocked) as error:
        _reserve(ledger, 'child', tokens=20, seconds=10, child_id='child-1', purpose='investigation')
    assert error.value.state == 'call_exceeds_parent_headroom'
    assert len(ledger.campaign_reservations('campaign')) == len(parents)


def test_legacy_reserved_parent_lease_cannot_be_consumed_by_a_child(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    policy = _policy(max_total_tokens=200,
        reconciliation={'tokens': 80, 'calls': 4, 'seconds': 40, 'context_bytes': 4096})
    ledger = _open(tmp_path, policy=policy)
    _fork(ledger, ('child-1',))
    _reserve(ledger, 'final-parent', tokens=80, calls=4, seconds=40, purpose='parent_reconciliation')
    assert ledger.get_campaign('campaign')['reconciliation_held']['seconds'] == 0
    monkeypatch.setattr(operations.time, 'time', lambda: 1110)
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'child', tokens=20, seconds=10, child_id='child-1', purpose='investigation')


def test_stale_child_cannot_launch_when_parent_time_is_in_concrete_leases(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    _fork(ledger, ('child-1',))
    child = _reserve(ledger, 'child', tokens=20, seconds=10, child_id='child-1', purpose='investigation')
    _reserve_all_parent_stages(ledger)
    monkeypatch.setattr(operations.time, 'time', lambda: 1110)
    assert ledger.model_call_remaining(child) == 0
    with pytest.raises(OperationBlocked):
        ledger.activate_model_call(child)


def test_current_parent_excludes_only_own_concrete_lease_and_completed_parent_time(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    parents = _reserve_all_parent_stages(ledger)
    monkeypatch.setattr(operations.time, 'time', lambda: 1080)
    assert ledger.model_call_remaining(parents[0]) == 10
    ledger.activate_model_call(parents[0])
    monkeypatch.setattr(operations.time, 'time', lambda: 1088)
    _settle(tmp_path, ledger, parents[0], seconds=8)
    assert ledger.model_call_remaining(parents[1]) == 10
    ordinary = _reserve(ledger, 'ordinary', tokens=20, seconds=2)
    assert ledger.model_call_remaining(ordinary) == 2
    with pytest.raises(OperationBlocked):
        _reserve(ledger, 'oversized-ordinary', tokens=20, seconds=3)


def test_parallel_active_parent_bounds_are_not_summed_as_serial_wall_time(tmp_path, monkeypatch):
    import devbench.analysis.workflow_operations as operations
    monkeypatch.setattr(operations.time, 'time', lambda: 1000)
    ledger = _protected_campaign(tmp_path)
    _fork(ledger, ('child-1',))
    parents = _reserve_all_parent_stages(ledger)
    for parent in parents[:3]:
        ledger.activate_model_call(parent)
    monkeypatch.setattr(operations.time, 'time', lambda: 1090)
    assert ledger.model_call_remaining(parents[0]) == 10
    child = _reserve(ledger, 'child', tokens=20, seconds=10, child_id='child-1', purpose='investigation')
    assert ledger.model_call_remaining(child) == 10


if __name__ == '__main__':
    # Keeps standalone process test helper imports from starting a test runner.
    raise SystemExit(0)
