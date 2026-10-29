"""Offline provider readback compatibility, bound to independent expectations.

Observed shape: reports/langsmith-readback/2026-10-08-v1/evidence/shape.json.
Provider reference: https://raw.githubusercontent.com/langchain-ai/langsmith-sdk/main/python/langsmith/schemas.py
Unlike SDK defaults, null IO and timezone-free times require exact expected data.
"""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from devbench.study import rna_langsmith_verify as verify
from devbench.study.contracts import canonical_json_bytes
from tests.test_rna_langsmith_binding import decode, issue, joined


def provider_readback(tmp_path, *, start='2026-10-08T20:00:00.123456+00:00',
                      end='2026-10-08T20:00:01.654321+00:00'):
    _, expected, _, context, project, now = joined(tmp_path)
    expected = deepcopy(expected)
    expected['trace_id'] = expected['id']
    expected.pop('parent_run_id', None)
    expected.update(start_time=start, end_time=end, inputs={}, outputs={})
    expected['dotted_order'] = (datetime.fromisoformat(start).astimezone(timezone.utc)
                                .strftime('%Y%m%dT%H%M%S%fZ') + expected['id'])
    expected['extra']['metadata']['remote_proof']['semantic_payload_digest'] = verify.semantic_digest(expected)
    remote = deepcopy(expected)
    remote.update(parent_run_id=None, inputs=None, outputs=None,
                  status='success', app_path='/synthetic-run')
    for key in ('start_time', 'end_time'):
        remote[key] = datetime.fromisoformat(expected[key]).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    receipt, _ = issue(context, project, now)
    return remote, expected, receipt, context, now


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('representation', ['combined', 'null_only', 'naive_only'])
@pytest.mark.parametrize('times', [
    ('2026-10-08T20:00:00.123456+00:00', '2026-10-08T20:00:01.654321+00:00'),
    ('2026-10-08T20:00:00+00:00', '2026-10-08T20:00:01+00:00'),
    ('2026-10-08T13:00:00.123456-07:00', '2026-10-08T13:00:01.654321-07:00'),
])
def test_provider_null_empty_io_and_expected_utc_naive_times_match(tmp_path, method, representation, times):
    remote, expected, receipt, context, now = provider_readback(tmp_path, start=times[0], end=times[1])
    if representation == 'null_only':
        remote.update(start_time=expected['start_time'], end_time=expected['end_time'])
    elif representation == 'naive_only':
        remote.update(inputs={}, outputs={})
    remote['extra']['metadata']['discarded'] = 'CANARY'
    original_remote, original_expected = deepcopy(remote), deepcopy(expected)
    body = remote if method == 'GET' else {'runs': [remote], 'cursors': {}}
    result, vector = decode(canonical_json_bytes(body), expected, receipt, context, now, method=method)
    assert result == 'exact_revision_match'
    assert vector['inputs'] == {} and vector['outputs'] == {}
    assert vector['start_time'] == datetime.fromisoformat(times[0]).astimezone(timezone.utc).isoformat(timespec='microseconds')
    assert vector['end_time'] == datetime.fromisoformat(times[1]).astimezone(timezone.utc).isoformat(timespec='microseconds')
    assert vector['extra']['metadata']['remote_proof'] == expected['extra']['metadata']['remote_proof']
    assert 'discarded' not in vector['extra']['metadata']
    assert remote == original_remote and expected == original_expected


@pytest.mark.parametrize('field', ['inputs', 'outputs'])
def test_null_io_cannot_replace_expected_nonempty_object(tmp_path, field):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    expected[field] = {'count': 1}
    expected['extra']['metadata']['remote_proof']['semantic_payload_digest'] = verify.semantic_digest(expected)
    remote['extra']['metadata']['remote_proof'] = deepcopy(expected['extra']['metadata']['remote_proof'])
    with pytest.raises(ValueError, match='^Invalid bounded joined run response$'):
        decode(remote, expected, receipt, context, now)


@pytest.mark.parametrize('field', ['inputs', 'outputs', 'extra', 'status', 'app_path'])
@pytest.mark.parametrize('value', [[], False, 0])
def test_provider_compatibility_rejects_wrong_field_types(tmp_path, field, value):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote[field] = value
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)


@pytest.mark.parametrize('field', ['inputs', 'outputs', 'extra', 'start_time', 'status', 'app_path'])
def test_provider_compatibility_rejects_missing_required_fields(tmp_path, field):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote.pop(field)
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)


@pytest.mark.parametrize('field', ['start_time', 'end_time'])
@pytest.mark.parametrize('value', ['2026-10-08T20:00:02.123456', '2026-10-08T13:00:00.123456',
                                 '2026-10-08T20:00:00.1234567', '2026-02-30T20:00:00',
                                 '2026-10-08', 'not-a-time', False])
def test_naive_timestamp_requires_valid_exact_expected_utc_components(tmp_path, field, value):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote[field] = value
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)


@pytest.mark.parametrize('value', [None, 'missing'])
def test_missing_terminal_time_cannot_invent_expected_completion(tmp_path, value):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    if value == 'missing':
        remote.pop('end_time')
    else:
        remote['end_time'] = value
    assert decode(remote, expected, receipt, context, now) == ('mismatch', None)


def test_qualified_equivalent_offsets_preserve_existing_behavior(tmp_path):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote.update(start_time='2026-10-08T13:00:00.123456-07:00',
                  end_time='2026-10-08T20:00:01.654321Z')
    assert decode(remote, expected, receipt, context, now)[0] == 'exact_revision_match'
    remote['end_time'] = '2026-10-08T20:00:02.654321Z'
    assert decode(remote, expected, receipt, context, now) == ('mismatch', None)


def test_chronology_still_rejected_even_when_expected_naive_components_match(tmp_path):
    remote, expected, receipt, context, now = provider_readback(
        tmp_path, end='2026-10-08T19:59:59.654321+00:00')
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)


@pytest.mark.parametrize('field', ['id', 'trace_id', 'parent_run_id', 'session_id', 'tenant_id'])
def test_provider_shape_does_not_relax_identity_project_or_workspace(tmp_path, field):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote[field] = str(uuid4())
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)


@pytest.mark.parametrize('field', ['dotted_order', 'name'])
def test_provider_shape_preserves_order_and_semantic_content_comparison(tmp_path, field):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote[field] = 'changed'
    assert decode(remote, expected, receipt, context, now) == ('mismatch', None)


@pytest.mark.parametrize('change', [{'revision': 99}, {'revision': False},
                                  {'operation_id': str(uuid4())}, {'semantic_payload_digest': '0' * 64}])
def test_provider_shape_does_not_relax_exact_revision_proof(tmp_path, change):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote['extra']['metadata']['remote_proof'].update(change)
    assert decode(remote, expected, receipt, context, now) == ('mismatch', None)


def test_provider_shape_without_proof_is_state_match_only(tmp_path):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    remote['extra']['metadata'].pop('remote_proof')
    assert decode(remote, expected, receipt, context, now)[0] == 'state_match_only'


def test_provider_shape_requires_real_receipt_and_independent_reader_context(tmp_path):
    remote, expected, receipt, context, now = provider_readback(tmp_path)
    with pytest.raises(ValueError):
        decode(remote, expected, receipt.to_record(), context, now)
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, {**context, 'runtime_digest': '0' * 64}, now)
