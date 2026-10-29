"""Metadata/hash tests only: staged numerical source is never imported or run."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.methods import MethodService


ASSETS = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-colab-integration/2026-10-22-v1/candidate/qualification')


@pytest.fixture(autouse=True)
def engineering_candidate_validator(monkeypatch):
    """Isolate node controls; this fixture establishes NO candidate provenance."""
    import devbench.analysis.workflow_candidate_freeze as freeze
    def validate_inert_test_manifest(ref, *, expected_sha256=None):
        raw = Path(ref['path']).read_bytes()
        if (hashlib.sha256(raw).hexdigest() != ref['sha256']
                or len(raw) != ref['size_bytes'] or expected_sha256 != ref['sha256']):
            raise ValueError('Inert engineering fixture manifest bytes changed')
        assert json.loads(raw) == {'format': 'test.inert-candidate-manifest/1'}
        return json.loads(raw)
    monkeypatch.setattr(freeze, 'validate_candidate_manifest', validate_inert_test_manifest)


def setup(tmp_path):
    from devbench.analysis.workflow_protocol_qualification import (
        ProtocolQualificationProfile, ProtocolQualificationRuntime, EXECUTION_POLICY)
    names = {'method': 'method.py', 'fixture': 'fixture.json', 'verifier': 'verifier.py',
             'expectations': 'expected_values.json', 'environment_lock': 'environment-lock.json'}
    refs = {}
    for role, name in names.items():
        source = tmp_path / name
        source.write_bytes((ASSETS / name).read_bytes())
        raw = source.read_bytes()
        refs[role] = {'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
    manifest = tmp_path / 'candidate-manifest.json'
    manifest.write_text('{"format":"test.inert-candidate-manifest/1"}')
    manifest_raw = manifest.read_bytes()
    job = {'job_id': 'a' * 32, 'source_bundle_sha256': 'b' * 64,
           'scope': 'synthetic_colab_qualification', 'policy': deepcopy(EXECUTION_POLICY),
           'source': {k: refs['method'][k] for k in ('sha256', 'size_bytes')},
           'environment_lock': {k: refs['environment_lock'][k] for k in ('sha256', 'size_bytes')},
           'validator_sha256': refs['verifier']['sha256'],
           'inputs': {'fixture': {**{k: refs['fixture'][k] for k in ('sha256', 'size_bytes')}, 'artifact_type': 'json'}}}
    profile = ProtocolQualificationProfile(job_id=job['job_id'], job_sha256=digest(job),
        source_bundle_sha256=job['source_bundle_sha256'], sources=refs,
        candidate_manifest_ref={'path': str(manifest), 'sha256': hashlib.sha256(manifest_raw).hexdigest(),
                                'size_bytes': len(manifest_raw)},
        candidate_sha256=hashlib.sha256(manifest_raw).hexdigest())
    runtime = ProtocolQualificationRuntime(profile=profile)
    state = {'session_id': 'synthetic-session', 'context': {
        'task': {'scope': profile.scope, 'objective_kind': 'software_integration_qualification',
                 'job_sha256': digest(job)},
        'dataset_summary': {'scope': 'synthetic_software_fixture_only', 'observed': {}}},
        'recommendation': None, 'results': []}
    service = MethodService(tmp_path / 'store')
    with service._db() as db:
        service._put(db, 'method_plan_snapshot', digest(state), state)
    from devbench.analysis.colab_workflow import ColabQualificationPlan
    # An inert typed shell isolates this unit test from authority/transport setup.
    planner = object.__new__(ColabQualificationPlan)
    planner.methods = service
    planner.get = lambda session: deepcopy(state)
    planner.execution = SimpleNamespace(job=job)
    return profile, runtime, planner, state


@pytest.mark.parametrize('node', ['discovery', 'candidate_review', 'implementation', 'design_review'])
def test_retained_protocol_records_are_nonbiological_and_replay_is_read_only(tmp_path, node):
    _, runtime, planner, state = setup(tmp_path)
    selector = SimpleNamespace(select=lambda *a, **k: pytest.fail('No model execution authorized'))
    evidence = runtime.perform(node, planner, state['session_id'], actor='operator', selector=selector)
    record = runtime.verify(node, planner, state['session_id'], evidence)
    assert record['record_type'] == 'nonbiological_protocol_check'
    assert record['qualification_status'] == 'protocol_only'
    assert record['biological_review_performed'] is False
    assert record['scientifically_qualified'] is False
    assert record['numerical_verification_performed'] is False
    assert record['oci_validation_performed'] is False
    assert record['execution_granted'] is False
    assert evidence['status'] in {'completed', 'descriptive'}
    assert runtime.perform(node, planner, state['session_id'], actor='operator', selector=selector) == evidence
    assert runtime.reconcile(node, planner, state['session_id'], before_planner_hash=digest(state)) == evidence
    assert planner.get(state['session_id']) == state
    assert planner.methods.list_records('run') == []


@pytest.mark.parametrize('change', ['scope', 'local', 'model', 'airway', 'extra', 'boolean_max_runs'])
def test_closed_profile_refuses_broader_authority(tmp_path, change):
    from devbench.analysis.workflow_protocol_qualification import ProtocolQualificationProfile
    profile, _, _, _ = setup(tmp_path)
    raw = profile.model_dump(mode='json')
    if change == 'scope': raw['scope'] = 'airway'
    elif change == 'local': raw['execution_policy']['fallback'] = 'local'
    elif change == 'model': raw['allow_model_execution'] = True
    elif change == 'airway': raw['airway'] = True
    elif change == 'extra': raw['hidden_executor'] = 'local'
    else: raw['execution_policy']['max_runs'] = True
    with pytest.raises(ValueError):
        ProtocolQualificationProfile.model_validate(raw)


def test_source_mutation_and_wrong_job_are_rejected(tmp_path):
    profile, runtime, planner, state = setup(tmp_path)
    evidence = runtime.perform('design_review', planner, state['session_id'], actor='operator', selector=None)
    Path(profile.sources.method.path).write_bytes(b'# changed source\n')
    with pytest.raises(ValueError, match='source|bytes'):
        runtime.verify('design_review', planner, state['session_id'], evidence)
    with pytest.raises(ValueError, match='source|bytes'):
        runtime.perform('candidate_review', planner, state['session_id'], actor='operator', selector=None)


def test_candidate_job_and_nonbiological_context_are_bound(tmp_path):
    _, runtime, planner, state = setup(tmp_path)
    planner.execution.job['job_id'] = 'c' * 32
    with pytest.raises(ValueError, match='job'):
        runtime.perform('candidate_review', planner, state['session_id'], actor='operator', selector=None)
    planner.execution.job['job_id'] = 'a' * 32
    state['context']['dataset_summary']['observed'] = {'rna': 'not authorized'}
    with pytest.raises(ValueError, match='biological|scope|synthetic'):
        runtime.perform('candidate_review', planner, state['session_id'], actor='operator', selector=None)


def test_unknown_and_uncertain_operations_cannot_dispatch(tmp_path):
    _, runtime, planner, state = setup(tmp_path)
    with pytest.raises(ValueError, match='node'):
        runtime.perform('run', planner, state['session_id'], actor='operator', selector=None)
    with pytest.raises(ValueError, match='uncertain|retained'):
        runtime.reconcile('discovery', planner, state['session_id'], before_planner_hash=digest(state))


def test_conclusion_requires_actual_retained_remote_result(tmp_path):
    _, runtime, planner, state = setup(tmp_path)
    with pytest.raises(ValueError, match='conclusion|remote|recommendation'):
        runtime.perform('conclusion_review', planner, state['session_id'], actor='operator', selector=None)


def test_exact_after_snapshot_and_envelope_are_required(tmp_path):
    _, runtime, planner, state = setup(tmp_path)
    evidence = runtime.perform('implementation', planner, state['session_id'], actor='operator', selector=None)
    altered = deepcopy(state)
    altered['unexpected'] = True
    with pytest.raises(ValueError, match='snapshot'):
        runtime.verify('implementation', planner, state['session_id'], evidence, plan_snapshot=altered)
    altered_evidence = {**evidence, 'scientifically_qualified': True}
    with pytest.raises(ValueError, match='envelope'):
        runtime.verify('implementation', planner, state['session_id'], altered_evidence)


def test_schema_and_configuration_do_not_grant_execution(tmp_path):
    from devbench.analysis.workflow_protocol_qualification import protocol_qualification_profile_schema
    profile, runtime, _, _ = setup(tmp_path)
    assert protocol_qualification_profile_schema()['additionalProperties'] is False
    assert runtime.configuration()['profile'] == profile.model_dump(mode='json')
    assert runtime.configuration()['execution_granted'] is False


def test_candidate_manifest_hash_and_exact_staged_assets_are_required(tmp_path):
    from devbench.analysis.workflow_protocol_qualification import ProtocolQualificationProfile, ProtocolQualificationRuntime
    profile, _, _, _ = setup(tmp_path)
    raw = profile.model_dump(mode='json')
    raw['candidate_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Candidate SHA'):
        ProtocolQualificationProfile.model_validate(raw)
    raw = profile.model_dump(mode='json')
    altered = Path(raw['sources']['fixture']['path'])
    # A schema-correct different dataset cannot be relabeled as the fixed fixture.
    altered.write_text('{"format":"devbench.colab-linear-fixture/1","x":[0,1,2,3],"y":[0,0,0,0]}')
    content = altered.read_bytes()
    raw['sources']['fixture'].update(sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content))
    with pytest.raises(ValueError, match='explicitly staged'):
        ProtocolQualificationRuntime(profile=ProtocolQualificationProfile.model_validate(raw))


def test_unsigned_binding_retains_exact_profile_and_never_approves(tmp_path):
    from devbench.analysis.workflow_protocol_qualification import qualification_execution_binding
    profile, _, planner, _ = setup(tmp_path)
    target = tmp_path / 'profile.json'
    target.write_text(profile.model_dump_json())
    raw = target.read_bytes()
    ref = {'path': str(target), 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}
    binding = qualification_execution_binding(profile=profile, job=planner.execution.job, profile_ref=ref)
    assert binding['workflow_ref'] == ref
    assert binding['approval_required'] is True and binding['execution_granted'] is False
    assert binding['candidate_sha256'] == profile.candidate_sha256
    assert planner.methods.list_records('run') == []
    target.write_text(profile.model_dump_json() + '\n')
    with pytest.raises(ValueError, match='bytes'):
        qualification_execution_binding(profile=profile, job=planner.execution.job, profile_ref=ref)
