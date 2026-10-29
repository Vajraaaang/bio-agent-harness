"""Opaque /3 failure display; proof v1 excludes error, full payload SHA includes it."""
from copy import deepcopy

import pytest

from devbench.analysis.rna_trace_contract import trace_digest
from devbench.analysis.rna_trace_receipts import LocalTraceObserver, observe_boundary
from devbench.study import rna_langsmith_live as live
from devbench.study import rna_langsmith_verify as verify
from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest
from devbench.study.rna_hosted_workflow import WorkflowReceiptBridge
from tests.test_rna_hosted_workflow_projection import (
    CAPABILITY, Observer, SCHEMA, decoder_scope, remote, workflow_scope,
)


ERRORS = {
    'failed': 'devbench.workflow_failed',
    'blocked': 'devbench.workflow_blocked',
    'cancelled': 'devbench.workflow_cancelled',
    'incomplete': 'devbench.workflow_incomplete',
}
REASONS = dict(failed='failed', blocked='admission_blocked', cancelled='cancelled',
               incomplete='incomplete', abstained='diagnostic_refusal')


def error_code(status):
    value = getattr(live, 'workflow_error_code', None)
    assert callable(value), 'Closed /3 workflow error mapping is missing'
    return value(status)


def terminal_projection(tmp_path, status):
    session, receipts, now = workflow_scope()
    retained = next(row for row in receipts if row['phase'] == 'run' and row['kind'] == 'completion')
    retained['source_status'] = status
    retained['kind'] = 'cancel' if status == 'cancelled' else 'failure' if status in {'failed', 'blocked'} else 'completion'
    retained['reason_code'] = REASONS.get(status, 'none')
    bridge = live.ReceiptOperationBridge(authorized_session_manifest=session,
        output_path=tmp_path / 'operations.jsonl', capability=CAPABILITY, allocation=0, actual_children=0)
    operations = bridge.emit_new(observer=Observer(receipts), workflow_id='synthetic-error-fixture')
    starts = {item['payload']['id']: item['payload'] for item in operations if item['operation']['operation'] == 'start'}
    terminal = next(item for item in operations if item['payload']['id'] == retained['span_id']
                    and item['operation']['operation'] == 'finish')
    return session, now, operations, starts, terminal


def rebound(item):
    item['payload'] = verify.bind_remote_proof(item['payload'], item['operation'])
    item['operation']['payload_sha256'] = digest(canonical_json_bytes(item['payload']))
    return item


def decode(observed, item, session, starts, now):
    context, receipt = decoder_scope(session, now)
    return verify.decode_joined_workflow_run_response(observed, schema_format=SCHEMA,
        method='GET', expected_payload=item['payload'], project_receipt=receipt,
        expected_context=context, expected_topology=starts, now=now)


@pytest.mark.parametrize('status, expected', [*ERRORS.items(), ('active', None), ('completed', None), ('abstained', None), ('closed', None), ('skipped', None)])
def test_workflow_error_mapping_is_closed_fixed_and_distinguishes_terminal_states(status, expected):
    assert error_code(status) == expected


@pytest.mark.parametrize('status', ['PRIVATE_EXCEPTION_CANARY', None, True])
def test_workflow_error_mapping_refuses_unknown_values_without_echoing_them(status):
    with pytest.raises(ValueError) as caught:
        error_code(status)
    assert 'PRIVATE_EXCEPTION_CANARY' not in str(caught.value)


def test_real_observed_failure_emits_only_opaque_errors_without_exception_text(tmp_path):
    session, _, _ = workflow_scope()
    workflow_id = 'PRIVATE_WORKFLOW_CANARY'
    session['workflow_binding_digest'] = trace_digest(workflow_id)
    observer = LocalTraceObserver(tmp_path / 'observer.sqlite', source_digest=session['source_digest'],
        runtime_digest=session['runtime_digest'], policy_digest=session['policy_digest'])
    def fail():
        raise RuntimeError('PRIVATE_EXCEPTION_CANARY')
    with pytest.raises(RuntimeError, match='PRIVATE_EXCEPTION_CANARY'):
        observe_boundary(observer, workflow_id, 'audit', fail,
            binding_digest=trace_digest([workflow_id, 'audit']), role='investigator', finalize_root=True)
    original = deepcopy(observer.read_events(workflow_id))
    assert len(original) == 4
    assert sum(row['source_status'] == 'failed' for row in original) == 2
    bridge = WorkflowReceiptBridge(authorized_session_manifest=session, output_path=tmp_path / 'sanitized.jsonl')
    operations = bridge.emit_new(observer=observer, workflow_id=workflow_id)
    assert len(operations) == 4
    for item in operations:
        payload = item['payload']
        assert 'status' not in payload  # Provider status is a read field, never a write.
        if item['operation']['operation'] == 'finish':
            assert payload['error'] == 'devbench.workflow_failed'
            assert payload['extra']['metadata']['source_status'] == 'failed'
            assert payload['extra']['metadata']['reason_code'] == 'failed'
        else:
            assert 'error' not in payload
    raw = (tmp_path / 'sanitized.jsonl').read_bytes()
    assert b'PRIVATE_' not in raw and str(tmp_path).encode() not in raw
    assert observer.read_events(workflow_id) == original


@pytest.mark.parametrize('status, expected', ERRORS.items())
def test_terminal_receipts_emit_exact_opaque_error_on_finish_and_never_status(tmp_path, status, expected):
    _, _, operations, _, item = terminal_projection(tmp_path, status)
    assert item['payload']['error'] == expected
    assert item['payload']['extra']['metadata']['source_status'] == status
    assert item['payload']['extra']['metadata']['reason_code'] == REASONS[status]
    for operation in operations:
        assert 'status' not in operation['payload']
        if operation['operation']['operation'] == 'start':
            assert 'error' not in operation['payload']
        assert operation['operation']['payload_sha256'] == digest(canonical_json_bytes(operation['payload']))


@pytest.mark.parametrize('operation', ['start', 'success'])
@pytest.mark.parametrize('error', ['devbench.workflow_failed', None])
def test_error_field_on_start_or_success_is_refused(tmp_path, operation, error):
    _, _, operations, _, terminal = terminal_projection(tmp_path, 'completed')
    item = deepcopy(operations[4] if operation == 'start' else terminal)
    item['payload']['error'] = error
    rebound(item)
    with pytest.raises(ValueError):
        live.validate_live_run(item['payload'], item['operation']['operation'], session_format=live.SESSION_FORMAT_V3)


@pytest.mark.parametrize('error', ['PRIVATE_EXCEPTION_CANARY', 'devbench.workflow_cancelled', None, False, {'message': 'PRIVATE_EXCEPTION_CANARY'}])
def test_failed_write_refuses_arbitrary_or_wrong_error_values(tmp_path, error):
    _, _, _, _, item = terminal_projection(tmp_path, 'failed')
    item['payload']['error'] = error
    rebound(item)
    with pytest.raises(ValueError) as caught:
        live.validate_live_run(item['payload'], 'finish', session_format=live.SESSION_FORMAT_V3)
    assert 'PRIVATE_EXCEPTION_CANARY' not in str(caught.value)


@pytest.mark.parametrize('status, expected', ERRORS.items())
def test_decoder_matches_failed_source_only_with_provider_error_status_and_exact_code(tmp_path, status, expected):
    session, now, _, starts, item = terminal_projection(tmp_path, status)
    assert item['payload']['error'] == expected
    observed = remote(item['payload'])
    observed['status'] = 'error'
    result, vector = decode(observed, item, session, starts, now)
    assert result == 'exact_revision_match'
    assert vector['extra']['metadata']['source_status'] == status
    assert vector['extra']['metadata']['remote_proof'] == item['payload']['extra']['metadata']['remote_proof']


@pytest.mark.parametrize('mutation', ['missing_error', 'null_error', 'wrong_error', 'raw_error', 'success_green'])
def test_decoder_refuses_missing_changed_or_success_green_failure(tmp_path, mutation):
    session, now, _, starts, item = terminal_projection(tmp_path, 'failed')
    item['payload']['error'] = ERRORS['failed']
    rebound(item)
    observed = remote(item['payload'])
    observed['status'] = 'error'
    if mutation == 'missing_error': observed.pop('error')
    elif mutation == 'null_error': observed['error'] = None
    elif mutation == 'wrong_error': observed['error'] = ERRORS['cancelled']
    elif mutation == 'raw_error': observed['error'] = 'PRIVATE_EXCEPTION_CANARY'
    else: observed['status'] = 'success'
    assert decode(observed, item, session, starts, now) == ('mismatch', None)


@pytest.mark.parametrize('observed_error', ['absent', None])
def test_decoder_allows_absent_or_null_error_only_for_successful_expected_source(tmp_path, observed_error):
    session, now, _, starts, item = terminal_projection(tmp_path, 'completed')
    assert 'error' not in item['payload']
    observed = remote(item['payload'])
    if observed_error is None: observed['error'] = None
    assert decode(observed, item, session, starts, now)[0] == 'exact_revision_match'


def test_decoder_refuses_injected_error_for_successful_source(tmp_path):
    session, now, _, starts, item = terminal_projection(tmp_path, 'completed')
    observed = remote(item['payload'])
    observed['error'] = ERRORS['failed']
    assert decode(observed, item, session, starts, now) == ('mismatch', None)


@pytest.mark.parametrize('status', ['abstained', 'skipped', 'closed'])
def test_nonerror_terminal_outcomes_preserve_explicit_source_metadata(tmp_path, status):
    session, now, _, starts, item = terminal_projection(tmp_path, status)
    assert 'error' not in item['payload']
    assert item['payload']['extra']['metadata']['source_status'] == status
    observed = remote(item['payload'])
    result, vector = decode(observed, item, session, starts, now)
    assert result == 'exact_revision_match'
    assert vector['extra']['metadata']['source_status'] == status
    # Provider terminal success denotes transport state, not scientific success.


@pytest.mark.parametrize('version', [None, live.SESSION_FORMAT, live.SESSION_FORMAT_V2])
def test_legacy_run_validation_does_not_accept_v3_error_payload(tmp_path, version):
    _, _, operations, _, _ = terminal_projection(tmp_path, 'failed')
    item = deepcopy(operations[-1])  # A root avoids incidental legacy child-order rejection.
    item['payload']['extra']['metadata']['source_status'] = 'failed'
    item['payload']['extra']['metadata']['kind'] = 'failure'
    item['payload']['extra']['metadata']['reason_code'] = 'failed'
    item['payload']['error'] = ERRORS['failed']
    rebound(item)
    with pytest.raises(ValueError):
        live.validate_live_run(item['payload'], 'finish', session_format=version)


def test_error_is_checked_separately_from_frozen_v1_semantics_and_full_wire_sha(tmp_path):
    _, _, _, _, item = terminal_projection(tmp_path, 'failed')
    assert item['payload']['error'] == ERRORS['failed']
    original = deepcopy(item['payload'])
    changed = deepcopy(original)
    changed['error'] = ERRORS['cancelled']
    assert verify.semantic_digest(changed) == verify.semantic_digest(original)
    assert digest(canonical_json_bytes(changed)) != item['operation']['payload_sha256']
    assert verify.validate_remote_proof(changed) == original['extra']['metadata']['remote_proof']
    with pytest.raises(ValueError):
        live.validate_live_run(changed, 'finish', session_format=live.SESSION_FORMAT_V3)


# /4 context-observation additions. All earlier /3 test bytes remain intact.
def test_v4_context_failed_terminal_error_and_closed_readback_diagnostic(tmp_path):
    from tests.test_rna_hosted_workflow_projection import context_projected
    scope = context_projected(tmp_path, fail_lead=True)
    decoder = getattr(verify, 'decode_joined_context_run_response', None)
    diagnostic = getattr(verify, 'context_response_diagnostic', None)
    assert callable(decoder) and callable(diagnostic), 'Explicit /4 context readback support is missing'
    failed = next(item for item in scope['operations'] if item['operation']['operation'] == 'finish'
                  and item['payload']['extra']['metadata']['source_status'] == 'failed')
    assert failed['payload']['error'] == 'devbench.workflow_failed'
    observed = remote(failed['payload']); observed['status'] = 'error'
    context, receipt = decoder_scope(scope['session'], scope['now'])
    kwargs = dict(schema_format='devbench.rna-joined-context-run-response/4', method='GET',
        expected_payload=failed['payload'], project_receipt=receipt, expected_context=context,
        expected_topology=scope['starts'], expected_session=scope['session'], now=scope['now'])
    assert decoder(observed, **kwargs)[0] == 'exact_revision_match'
    observed['status'] = 'success'; observed['private_unknown'] = 'PRIVATE_CONTEXT_CANARY'
    assert decoder(observed, **kwargs) == ('mismatch', None)
    body = canonical_json_bytes(observed)
    closed = diagnostic(body, failed['payload'], decoder_result='mismatch')
    assert set(closed) == {'span_id', 'body_length', 'body_sha256', 'mismatch_category'}
    assert closed['body_length'] == len(body) and closed['body_sha256'] == digest(body)
    assert closed['mismatch_category'] == 'provider_status_mismatch'
    assert 'PRIVATE_CONTEXT_CANARY' not in str(closed)


def test_v4_context_readback_never_hashes_an_oversize_or_incomplete_prefix(tmp_path):
    from tests.test_rna_hosted_workflow_projection import context_projected
    scope = context_projected(tmp_path)
    diagnostic = getattr(verify, 'context_response_diagnostic', None)
    assert callable(diagnostic), 'Explicit /4 closed body diagnostic is missing'
    expected = scope['operations'][0]['payload']
    for body, complete, category in ((b'x' * 131073, True, 'body_oversized'),
            (canonical_json_bytes(remote(expected)), False, 'body_incomplete')):
        row = diagnostic(body, expected, decoder_result='invalid_response', body_complete=complete)
        assert row['body_sha256'] is None and row['body_length'] is None
        assert row['mismatch_category'] == category


def test_v4_context_diagnostic_row_validator_is_closed_and_never_exposes_values():
    validator = getattr(verify, 'validate_context_response_diagnostic', None)
    assert callable(validator), 'Explicit /4 closed diagnostic row validator is missing'
    valid = dict(span_id='b5b7372f-6e46-4a31-bfea-b5b2c77f3668', body_length=0,
                 body_sha256=digest(b''), mismatch_category='schema_mismatch')
    assert validator(valid) == valid
    for changes in ({'private_unknown': 'PRIVATE_DIAGNOSTIC_CANARY'},
                    {'body_length': 131073}, {'body_sha256': None},
                    {'mismatch_category': 'PRIVATE_DIAGNOSTIC_CANARY'}):
        with pytest.raises(ValueError) as caught:
            validator({**valid, **changes})
        assert 'PRIVATE_DIAGNOSTIC_CANARY' not in str(caught.value)


def test_v4_context_complete_json_prefix_cannot_claim_verified_complete_body(tmp_path):
    from tests.test_rna_hosted_workflow_projection import context_projected
    scope = context_projected(tmp_path)
    expected = scope['operations'][0]['payload']
    body = canonical_json_bytes(remote(expected))
    diagnostic = getattr(verify, 'context_response_diagnostic', None)
    validator = getattr(verify, 'validate_context_response_diagnostic', None)
    decoder = getattr(verify, 'decode_joined_context_run_response', None)
    assert callable(diagnostic) and callable(validator) and callable(decoder)
    row = diagnostic(body, expected, decoder_result='exact_revision_match', body_complete=False)
    assert row['mismatch_category'] == 'body_incomplete'
    assert row['body_length'] is None and row['body_sha256'] is None
    with pytest.raises(ValueError):
        validator({**row, 'body_length': len(body), 'body_sha256': digest(body)})
    context, receipt = decoder_scope(scope['session'], scope['now'])
    with pytest.raises(ValueError):
        decoder(body, schema_format='devbench.rna-joined-context-run-response/4', method='GET',
            expected_payload=expected, project_receipt=receipt, expected_context=context,
            expected_topology=scope['starts'], expected_session=scope['session'],
            body_complete=False, now=scope['now'])
