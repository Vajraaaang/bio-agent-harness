"""Synthetic decoder candidates do not attest a deployed LangSmith schema."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from devbench.study import rna_langsmith_verify as verify
from devbench.study.contracts import canonical_json_bytes
from tests.test_rna_langsmith_verify import prepared


SCHEMA_FORMAT = 'devbench.rna-run-response-candidate/1'


def candidate(tmp_path):
    _, _, item, manifest = prepared(tmp_path)
    # The public RunSchema requires these provider fields. tenant_id is an
    # explicitly synthetic extension: the documented response lacks it.
    remote = {**deepcopy(item['payload']), 'tenant_id': manifest['workspace_id'],
              'dotted_order': 'synthetic-order', 'status': 'pending',
              'app_path': '/synthetic-run'}
    target = dict(schema_format=SCHEMA_FORMAT, method='GET',
                  expected_payload=item['payload'], workspace_id=manifest['workspace_id'],
                  project_id=manifest['project_id'], project_name=manifest['project_name'],
                  project_attestation=dict(id=manifest['project_id'],
                                           tenant_id=manifest['workspace_id'],
                                           name=manifest['project_name']))
    return remote, target, manifest


def decode(body, **target):
    decoder = getattr(verify, 'decode_run_response', None)
    assert callable(decoder), 'A2b closed run-response decoder is missing'
    return decoder(body, **target)


def test_candidate_get_preserves_proof_and_projects_provider_additions(tmp_path):
    remote, target, _ = candidate(tmp_path)
    remote['provider_secret'] = 'CANARY'
    remote['extra']['metadata']['provider_secret'] = 'CANARY'
    original = deepcopy(remote)
    result, vector = decode(canonical_json_bytes(remote), **target)
    assert result == 'exact_revision_match'
    assert vector['extra']['metadata']['remote_proof'] == target['expected_payload']['extra']['metadata']['remote_proof']
    assert vector['parent_run_id'] == remote['parent_run_id']
    assert vector['start_time'].endswith('+00:00')
    assert 'CANARY' not in json.dumps(vector)
    assert remote == original


def test_candidate_query_checks_each_object_and_exact_cardinality(tmp_path):
    remote, target, _ = candidate(tmp_path)
    target['method'] = 'POST'
    assert decode({'runs': [remote], 'cursors': {}}, **target)[0] == 'exact_revision_match'
    assert decode({'runs': [], 'cursors': {}}, **target) == ('not_visible', None)
    with pytest.raises(ValueError):
        decode({'runs': [remote, {**remote, 'tenant_id': str(uuid4())}], 'cursors': {}}, **target)
    with pytest.raises(ValueError):
        decode({'runs': [remote]}, **target)


@pytest.mark.parametrize('field,value', [('parsed_query',0),('parsed_query',{}),('search_cursors',False),('search_cursors',[])])
def test_candidate_query_rejects_invalid_known_optional_field_types(tmp_path,field,value):
    remote, target, _ = candidate(tmp_path)
    target['method'] = 'POST'
    with pytest.raises(ValueError):
        decode({'runs':[remote],'cursors':{},field:value},**target)


def test_candidate_rejects_duplicate_identity_in_otherwise_valid_json(tmp_path):
    remote, target, _ = candidate(tmp_path)
    body = canonical_json_bytes(remote)[:-1] + b',"session_id":' + canonical_json_bytes(remote['session_id']) + b'}'
    with pytest.raises(ValueError):
        decode(body,**target)


@pytest.mark.parametrize('field', ['tenant_id', 'session_id'])
def test_candidate_rejects_wrong_or_metadata_only_target(tmp_path, field):
    remote, target, _ = candidate(tmp_path)
    remote[field] = str(uuid4())
    with pytest.raises(ValueError):
        decode(remote, **target)
    remote.pop(field)
    remote['extra']['metadata'][field] = target['workspace_id' if field == 'tenant_id' else 'project_id']
    with pytest.raises(ValueError):
        decode(remote, **target)


def test_candidate_project_attestation_rejects_rename_and_cross_workspace(tmp_path):
    remote, target, _ = candidate(tmp_path)
    for changed in ({'name': 'renamed'}, {'tenant_id': str(uuid4())}, {'id': str(uuid4())}, {'workspace_binding_qualified': True}):
        invalid = {**target, 'project_attestation': {**target['project_attestation'], **changed}}
        with pytest.raises(ValueError):
            decode(remote, **invalid)


@pytest.mark.parametrize('field', ['id', 'trace_id', 'parent_run_id', 'session_id', 'tenant_id', 'name', 'run_type', 'tags', 'start_time', 'inputs', 'outputs', 'extra', 'dotted_order', 'status', 'app_path'])
def test_candidate_requires_every_identity_and_semantic_field(tmp_path, field):
    remote, target, _ = candidate(tmp_path)
    remote.pop(field)
    with pytest.raises(ValueError):
        decode(remote, **target)


@pytest.mark.parametrize('stamp', ['not-a-time', '2026-10-08T07:30:00', '2026-10-08', 0, None])
def test_candidate_rejects_invalid_or_timezone_free_timestamp(tmp_path, stamp):
    remote, target, _ = candidate(tmp_path)
    remote['start_time'] = stamp
    with pytest.raises(ValueError):
        decode(remote, **target)


def test_candidate_does_not_invent_completion_or_acknowledge_stripped_proof(tmp_path):
    remote, target, _ = candidate(tmp_path)
    remote['end_time'] = None
    remote['extra']['metadata'].pop('remote_proof')
    result, vector = decode(remote, **target)
    assert result == 'state_match_only'
    assert 'end_time' not in vector
    assert 'remote_proof' not in vector['extra']['metadata']


def test_candidate_stripped_proof_does_not_equate_nested_boolean_and_integer(tmp_path):
    remote, target, _ = candidate(tmp_path)
    expected = target['expected_payload']
    # Public metadata accepts JSON lists. Recompute the trusted expected proof
    # after adding a synthetic field, then strip the observed proof entirely.
    expected['extra']['metadata']['synthetic_values'] = [0]
    expected['extra']['metadata']['remote_proof']['semantic_payload_digest'] = verify.semantic_digest(expected)
    remote['extra']['metadata']['synthetic_values'] = [False]
    remote['extra']['metadata'].pop('remote_proof')
    assert decode(remote,**target) == ('mismatch',None)


@pytest.mark.parametrize('changed', [{'revision': 42}, {'revision': False}, {'operation_id': str(uuid4())}, {'semantic_payload_digest': 'f' * 64}, {'unreviewed': True}])
def test_candidate_malformed_or_later_proof_never_acknowledges(tmp_path, changed):
    remote, target, _ = candidate(tmp_path)
    remote['extra']['metadata']['remote_proof'].update(changed)
    result, _ = decode(remote, **target)
    assert result == 'mismatch'


@pytest.mark.parametrize('field,value', [('parent_run_id', None), ('trace_id', str(uuid4())), ('name', 'wrong'), ('inputs', {'prompt': 'CANARY'})])
def test_candidate_semantic_mismatch_retains_no_untrusted_content(tmp_path, field, value):
    remote, target, _ = candidate(tmp_path)
    remote[field] = value
    assert decode(remote, **target) == ('mismatch', None)


@pytest.mark.parametrize('body', [b'{', b'[]', b'{"runs": [], "runs": []}', b'x' * 262145])
def test_candidate_rejects_malformed_duplicate_or_oversized_json(tmp_path, body):
    _, target, _ = candidate(tmp_path)
    with pytest.raises(ValueError):
        decode(body, **target)


@pytest.mark.parametrize('value', [b'NaN', b'Infinity', b'-Infinity', b'1e999'])
def test_candidate_rejects_nonfinite_discarded_provider_fields(tmp_path, value):
    remote, target, _ = candidate(tmp_path)
    body = canonical_json_bytes(remote)[:-1] + b',"discarded":{"value":' + value + b'}}'
    with pytest.raises(ValueError, match='^Invalid bounded candidate run response$'):
        decode(body, **target)


def test_candidate_rejects_proof_null_and_does_not_leak_error_content(tmp_path):
    remote, target, _ = candidate(tmp_path)
    remote['extra']['metadata']['remote_proof'] = None
    assert decode(remote, **target) == ('mismatch', None)
    remote['start_time'] = 'CANARY'
    with pytest.raises(ValueError, match='^Invalid bounded candidate run response$'):
        decode(remote, **target)


def test_candidate_rejects_unknown_version_and_unsupported_route(tmp_path):
    remote, target, _ = candidate(tmp_path)
    for changed in ({'schema_format': 'devbench.rna-run-response-candidate/999'}, {'method': 'PATCH'}):
        with pytest.raises(ValueError):
            decode(remote, **{**target, **changed})


def test_documented_run_without_workspace_binding_and_project_metadata_are_inadmissible(tmp_path):
    remote, target, _ = candidate(tmp_path)
    remote.pop('tenant_id')
    with pytest.raises(ValueError):
        decode(remote, **target)
    metadata = {**target['project_attestation'], 'deployment_qualified': True,
                'workspace_binding_qualified': True}
    with pytest.raises(ValueError):
        decode(metadata, **target)


def test_hosted_schema_readiness_cannot_be_promoted_by_mutable_flags(monkeypatch):
    readiness = getattr(verify, 'hosted_schema_qualified', None)
    assert callable(readiness), 'A2b non-self-asserted hosted readiness gate is missing'
    monkeypatch.setitem(verify.READ_SCHEMA_SNAPSHOT, 'deployment_qualified', True)
    monkeypatch.setitem(verify.READ_SCHEMA_SNAPSHOT, 'workspace_binding_qualified', True)
    assert readiness() is False
