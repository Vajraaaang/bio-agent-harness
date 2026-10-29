"""Offline joined project/run binding; no deployed-schema qualification claim."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import json
import time
from uuid import uuid4

import pytest

from devbench.study import rna_langsmith_verify as verify
from devbench.study.contracts import canonical_json_bytes
from devbench.study.langsmith_outbox import digest
from devbench.study.rna_hosted_trace_contract import validate_reconciliation
from tests.test_rna_hosted_trace_contract import criteria
from tests.test_rna_langsmith_schema import candidate


SCHEMA = 'devbench.rna-joined-run-response/2'


def binding_module():
    name = 'devbench.study.rna_langsmith_binding'
    assert importlib.util.find_spec(name) is not None, 'Joined project binding module is missing'
    return importlib.import_module(name)


def joined(tmp_path):
    remote, target, manifest = candidate(tmp_path)
    remote.pop('tenant_id')
    now = datetime.now(timezone.utc)
    context = dict(format='devbench.rna-project-binding-context/2',
                   session_digest=manifest['live_session_digest'], packet_digest='d' * 64, action_digest='a' * 64,
                   source_digest=manifest['source_digest'], runtime_digest=manifest['runtime_digest'],
                   endpoint=manifest['endpoint'], region='us',
                   workspace_id=manifest['workspace_id'], project_id=manifest['project_id'],
                   project_name=manifest['project_name'],
                   expires_at=(now + timedelta(minutes=5)).isoformat(), max_age_seconds=60)
    project = target['project_attestation']
    return remote, target['expected_payload'], manifest, context, project, now


def issue(context, project, now, *, authority=None, nonce=None):
    authority = authority or binding_module().ProjectAttestationAuthority(expected_context=context)
    receipt = authority.issue_from_response(response={'status_code': 200, 'body': project},
                                            request_nonce=nonce or str(uuid4()), observed_at=now.isoformat())
    return receipt, authority


def decode(remote, expected, receipt, context, now, *, method='GET', schema=SCHEMA):
    decoder = getattr(verify, 'decode_joined_run_response', None)
    assert callable(decoder), 'Provider-compatible joined decoder is missing'
    return decoder(remote, schema_format=schema, method=method, expected_payload=expected,
                   project_receipt=receipt, expected_context=context, now=now)


def test_joined_get_uses_typed_project_receipt_without_inventing_run_tenant(tmp_path):
    remote, expected, _, context, project, now = joined(tmp_path)
    remote['discarded'] = 'CANARY'
    remote['extra']['metadata']['discarded'] = 'CANARY'
    original = deepcopy(remote)
    receipt, _ = issue(context, project, now)
    result, vector = decode(canonical_json_bytes(remote), expected, receipt, context, now)
    assert result == 'exact_revision_match'
    assert vector['extra']['metadata']['remote_proof'] == expected['extra']['metadata']['remote_proof']
    assert 'tenant_id' not in vector and 'CANARY' not in json.dumps(vector)
    assert remote == original
    record = receipt.to_record()
    assert record['format'] == 'devbench.rna-project-read-receipt/2'
    assert record['observed_project'] == project
    assert record['session_digest'] == context['session_digest']
    assert 'authenticated' not in record


@pytest.mark.parametrize('changed', [{'id': str(uuid4())}, {'tenant_id': str(uuid4())}, {'name': 'renamed'}])
def test_project_response_rejects_wrong_workspace_project_or_rename(tmp_path, changed):
    _, _, _, context, project, now = joined(tmp_path)
    with pytest.raises(ValueError):
        issue(context, {**project, **changed}, now)


@pytest.mark.parametrize('field', ['session_digest', 'packet_digest', 'action_digest', 'source_digest', 'runtime_digest',
                                  'workspace_id', 'project_id', 'project_name', 'endpoint', 'region'])
def test_receipt_requires_independently_matching_context(tmp_path, field):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    changed = {**context, field: str(uuid4()) if field.endswith('_id') else 'different'}
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, changed, now)


def test_serialized_receipt_and_authentication_flags_are_not_authority(tmp_path):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    for record in (receipt.to_record(), {**receipt.to_record(), 'authenticated': True}, project):
        with pytest.raises(ValueError):
            decode(remote, expected, record, context, now)
    record = receipt.to_record()
    record['observed_project']['tenant_id'] = str(uuid4())
    assert decode(remote, expected, receipt, context, now)[0] == 'exact_revision_match'


@pytest.mark.parametrize('delta', [-1, 61, 301])
def test_receipt_rejects_future_stale_or_expired_observations(tmp_path, delta):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now + timedelta(seconds=delta))


@pytest.mark.parametrize('stamp', ['not-a-time', '2026-10-08T00:00:00', '2026-10-08', False, None])
def test_project_receipt_rejects_invalid_timestamp(tmp_path, stamp):
    _, _, _, context, project, _ = joined(tmp_path)
    authority = binding_module().ProjectAttestationAuthority(expected_context=context)
    with pytest.raises(ValueError):
        authority.issue_from_response(response={'status_code': 200, 'body': project},
                                      request_nonce=str(uuid4()), observed_at=stamp)


def test_receipt_issuance_rejects_nonce_replay_and_bounded_invalid_response(tmp_path):
    _, _, _, context, project, now = joined(tmp_path)
    receipt, authority = issue(context, project, now)
    with pytest.raises(ValueError):
        issue(context, project, now, authority=authority, nonce=receipt.to_record()['request_nonce'])
    for response in ({'status_code': 302, 'body': project}, {'status_code': True, 'body': project},
                     {'status_code': 200, 'body': b'x' * 65537},
                     {'status_code': 200, 'body': canonical_json_bytes(project)[:-1] + b',"id":"duplicate"}'},
                     {'status_code': 200, 'body': canonical_json_bytes(project)[:-1] + b',"discarded":1e999}'}):
        with pytest.raises(ValueError):
            authority.issue_from_response(response=response, request_nonce=str(uuid4()), observed_at=now.isoformat())


@pytest.mark.parametrize('changed', [{'session_id': str(uuid4())}, {'tenant_id': str(uuid4())}])
def test_every_returned_run_requires_project_join_and_checks_optional_tenant(tmp_path, changed):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    remote.update(changed)
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)
    with pytest.raises(ValueError):
        decode({'runs': [remote], 'cursors': {}}, expected, receipt, context, now, method='POST')


def test_joined_query_requires_closed_envelope_and_at_most_one_run(tmp_path):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    assert decode({'runs': [remote], 'cursors': {}}, expected, receipt, context, now, method='POST')[0] == 'exact_revision_match'
    assert decode({'runs': [], 'cursors': {}}, expected, receipt, context, now, method='POST') == ('not_visible', None)
    for body in ({'runs': [remote]}, {'runs': [remote, remote], 'cursors': {}},
                 {'runs': [], 'cursors': {}, 'authenticated': True}):
        with pytest.raises(ValueError):
            decode(body, expected, receipt, context, now, method='POST')


@pytest.mark.parametrize('proof', [None, {'revision': False}, {'revision': 99}, {'unknown': True}])
def test_joined_malformed_proof_never_acknowledges(tmp_path, proof):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    if proof is None:
        remote['extra']['metadata']['remote_proof'] = None
    else:
        remote['extra']['metadata']['remote_proof'].update(proof)
    assert decode(remote, expected, receipt, context, now) == ('mismatch', None)


def test_joined_stripped_proof_is_state_only_and_nested_boolean_is_not_integer(tmp_path):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    remote['extra']['metadata'].pop('remote_proof')
    assert decode(remote, expected, receipt, context, now)[0] == 'state_match_only'
    expected['extra']['metadata']['values'] = [0]
    expected['extra']['metadata']['remote_proof']['semantic_payload_digest'] = verify.semantic_digest(expected)
    remote['extra']['metadata']['values'] = [False]
    assert decode(remote, expected, receipt, context, now) == ('mismatch', None)


@pytest.mark.parametrize('body', [b'{', b'[]', b'{"id":0,"id":0}', b'x' * 262145, b'{"discarded":1e999}'])
def test_joined_rejects_duplicate_nonfinite_or_oversized_json(tmp_path, body):
    _, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    with pytest.raises(ValueError, match='^Invalid bounded joined run response$'):
        decode(body, expected, receipt, context, now)


def test_joined_unknown_version_and_invalid_run_timestamp_are_rejected(tmp_path):
    remote, expected, _, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now, schema='devbench.rna-joined-run-response/999')
    remote['start_time'] = '2026-10-08T00:00:00'
    with pytest.raises(ValueError):
        decode(remote, expected, receipt, context, now)


def test_joined_verifier_returns_existing_reconciliation_and_consumes_receipt_once(tmp_path):
    remote, expected, manifest, context, project, now = joined(tmp_path)
    receipt, _ = issue(context, project, now)
    cls = getattr(verify, 'JoinedRunVerifier', None)
    assert callable(cls), 'Joined verifier is missing'
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return {'status_code': 200, 'body': remote}
    kwargs = dict(manifest=manifest, criteria=criteria(), api_key=None, read_transport=read,
                  project_receipt=receipt, expected_context=context)
    verifier = cls(**kwargs)
    proof = expected['extra']['metadata']['remote_proof']
    operation = dict(operation='start', operation_id=proof['operation_id'], span_id=expected['id'],
                     revision=proof['revision'], payload_sha256=digest(canonical_json_bytes(expected)))
    result = verifier.observe(operation=operation, expected_payload=expected, persisted_monotonic_ns=time.monotonic_ns())
    assert validate_reconciliation(result) == result
    assert result['verification_result'] == 'exact_revision_match' and len(calls) == 1
    with pytest.raises(ValueError):
        cls(**kwargs)
    assert verify.hosted_schema_qualified() is False
