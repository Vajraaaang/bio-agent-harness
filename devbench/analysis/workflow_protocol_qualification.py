"""Nonbiological, metadata-only qualification services for the serial RNA graph.

These checks verify retained protocol/source/review records. They perform no
discovery, biological review, numerical verification, plot validation or OCI
validation. Configuration never grants execution; the separate Colab authority
must approve the exact frozen candidate and job before the graph's run node.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from devbench.study.evidence import _read, decode_json
from devharness_core.provenance import canonical_json
from .checkpoints import atomic_json, digest


EXECUTION_POLICY = {'scientific_execution_tier': 'colab', 'local_scientific_execution': 'forbidden',
    'fallback': 'forbidden', 'automatic_replay': 'forbidden', 'max_runs': 1,
    'network_enforcement': 'not_proven'}
# The operator named these staged assets explicitly. Byte-identical frozen
# copies are admitted; a different dataset or verifier needs a new profile API.
QUALIFICATION_ASSET_SHA256 = {
    'method': '7ee61b57cc762e1e1e35fda55ca628ec9252b4dab0e6461bcd43d9497de71991',
    'verifier': 'ab951453442598aba42b08340e565eb5804b1eab01be4279295ac2dedb0d7984',
    'fixture': '0a82e852d862115df52d18dd4492b174d2383ac44577c6cd3330a3630bf1c57c',
    'expectations': 'c00cfab078efd3ae9ef6aea26cfea67105aa554371b993f3052ce01b44015fa3',
    'environment_lock': 'e0fc92f7c52d3d99da40e2e67958d8e07c568600d99bd26aefef6b3f7a036a37',
}
_NODES = {'discovery', 'candidate_review', 'implementation', 'design_review', 'conclusion_review'}
_FORMAT = 'devbench.nonbiological-protocol-check/1'
_MAX_BYTES = 4_000_000
_REF_KEYS = ('artifact_id', 'sha256', 'artifact_type', 'size_bytes')
_ENVELOPE_KEYS = {'record_ref', 'status', 'stage', 'material_sha256', 'before_planner_hash', 'after_planner_hash'}
_CHECKS = {
    'discovery': 'supplied_protocol_catalog_and_asset_custody',
    'candidate_review': 'fixed_job_identity_matches_retained_sources',
    'implementation': 'retained_method_and_environment_bytes_without_oci_validation',
    'design_review': 'single_job_remote_only_policy_and_fixture_schema',
    'conclusion_review': 'retained_remote_result_metadata_with_independent_acceptance_pending',
}


class _Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class QualificationSourceReference(_Closed):
    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes: int = Field(ge=1, le=_MAX_BYTES)

    @field_validator('path')
    @classmethod
    def absolute_path(cls, value):
        if not Path(value).is_absolute():
            raise ValueError('Protocol source requires an absolute path')
        return value


class QualificationSources(_Closed):
    method: QualificationSourceReference
    fixture: QualificationSourceReference
    verifier: QualificationSourceReference
    expectations: QualificationSourceReference
    environment_lock: QualificationSourceReference


class QualificationExecutionPolicy(_Closed):
    scientific_execution_tier: Literal['colab'] = 'colab'
    local_scientific_execution: Literal['forbidden'] = 'forbidden'
    fallback: Literal['forbidden'] = 'forbidden'
    automatic_replay: Literal['forbidden'] = 'forbidden'
    max_runs: int = Field(default=1, ge=1, le=1)
    network_enforcement: Literal['not_proven'] = 'not_proven'


class ProtocolQualificationProfile(_Closed):
    """One public synthetic job, without scientific or execution authority.

    The candidate manifest binds the combined source/wheel/dependency closure;
    it is distinct from the remote job's narrower source-bundle hash. The actual
    execution request must retain and bind this exact profile as workflow_ref.
    """
    format: Literal['devbench.protocol-qualification-profile/1'] = 'devbench.protocol-qualification-profile/1'
    scope: Literal['synthetic_colab_qualification'] = 'synthetic_colab_qualification'
    job_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    job_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_bundle_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_manifest_ref: QualificationSourceReference
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    sources: QualificationSources
    execution_policy: QualificationExecutionPolicy = QualificationExecutionPolicy()
    allow_model_execution: Literal[False] = False
    airway: Literal[False] = False

    @field_validator('allow_model_execution', 'airway', mode='before')
    @classmethod
    def exact_false(cls, value):
        if value is not False:
            raise ValueError('Model execution and airway inputs must be explicitly false')
        return value

    @model_validator(mode='after')
    def candidate_identity(self):
        if self.candidate_sha256 != self.candidate_manifest_ref.sha256:
            raise ValueError('Candidate SHA must bind the exact frozen manifest bytes')
        return self


def protocol_qualification_profile_schema():
    return ProtocolQualificationProfile.model_json_schema()


def _source(ref):
    path = Path(ref.path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Retained protocol source must be a regular file')
    raw = _read(path, _MAX_BYTES)
    if len(raw) != ref.size_bytes or hashlib.sha256(raw).hexdigest() != ref.sha256:
        raise ValueError('Retained protocol source bytes changed')
    return raw


def _sources(profile):
    """Bounded byte/schema validation only; no numerical or plot checks."""
    raw = {role: _source(getattr(profile.sources, role)) for role in QualificationSources.model_fields}
    if any(getattr(profile.sources, role).sha256 != expected for role, expected in QUALIFICATION_ASSET_SHA256.items()):
        raise ValueError('Protocol source differs from the explicitly staged qualification assets')
    fixture = decode_json(raw['fixture'])
    if (type(fixture) is not dict or set(fixture) != {'format', 'x', 'y'}
            or fixture['format'] != 'devbench.colab-linear-fixture/1'
            or any(type(fixture[key]) is not list or len(fixture[key]) != 4
                   or any(type(value) not in (int, float) for value in fixture[key]) for key in ('x', 'y'))):
        raise ValueError('Only the bounded synthetic fixture schema is admitted')
    expectations = decode_json(raw['expectations'])
    if (type(expectations) is not dict
            or set(expectations) != {'format', 'fixture', 'numerical', 'plot', 'negative_cases'}
            or expectations['format'] != 'devbench.colab-linear-expectations/1'):
        raise ValueError('Retained protocol expectations schema differs')
    environment = decode_json(raw['environment_lock'])
    if (type(environment) is not dict or environment.get('format') != 'devbench.colab-synthetic-environment/1'
            or environment.get('runtime') != 'google_colab'
            or environment.get('scope') != 'synthetic_colab_qualification_only'
            or environment.get('network_enforcement') != 'not_proven'
            or environment.get('airway_r_runtime') != 'not_qualified'
            or environment.get('dependency_installation') != 'forbidden'):
        raise ValueError('Retained synthetic Colab environment schema differs')
    from .workflow_candidate_freeze import validate_candidate_manifest
    validate_candidate_manifest(profile.candidate_manifest_ref.model_dump(mode='json'),
                                expected_sha256=profile.candidate_sha256)
    if not raw['method'].strip() or not raw['verifier'].strip():
        raise ValueError('Retained method/verifier source is absent')
    return profile.sources.model_dump(mode='json')


def verify_profile_job(profile, job):
    """Bind metadata to the exact separate-authority job; never approve it."""
    if type(profile) is not ProtocolQualificationProfile or type(job) is not dict:
        raise TypeError('Use an exact protocol qualification profile and job')
    refs = profile.sources
    if (digest(job) != profile.job_sha256 or job.get('job_id') != profile.job_id
            or job.get('scope') != profile.scope or job.get('source_bundle_sha256') != profile.source_bundle_sha256
            or canonical_json(job.get('policy')) != canonical_json(profile.execution_policy.model_dump(mode='json'))
            or job.get('source') != {key: getattr(refs.method, key) for key in ('sha256', 'size_bytes')}
            or job.get('environment_lock') != {key: getattr(refs.environment_lock, key) for key in ('sha256', 'size_bytes')}
            or job.get('validator_sha256') != refs.verifier.sha256
            or job.get('inputs') != {'fixture': {**{key: getattr(refs.fixture, key) for key in ('sha256', 'size_bytes')},
                                              'artifact_type': 'json'}}):
        raise ValueError('Protocol qualification job differs from the exact frozen profile')
    return profile


def verify_profile_sources(profile):
    """Validate typed source/profile/frozen-candidate custody without authority."""
    if type(profile) is not ProtocolQualificationProfile:
        raise TypeError('Use the exact typed protocol qualification profile')
    _sources(profile)
    return profile


def prepare_qualification_profile(*, job, candidate_ref, assets):
    """Prepare one typed profile, without creating approval or dispatch authority.

    ``assets`` maps the five fixed roles to absolute files or exact source refs.
    The caller retains the resulting JSON as the execution request workflow_ref.
    """
    from .colab_execution_contract import validate_job
    job = validate_job(job)
    candidate = QualificationSourceReference.model_validate(candidate_ref)
    if type(assets) is not dict or set(assets) != set(QUALIFICATION_ASSET_SHA256):
        raise ValueError('Use exactly the five explicitly staged qualification asset roles')
    refs = {}
    for role, value in assets.items():
        if type(value) is dict:
            ref = QualificationSourceReference.model_validate(value)
        else:
            path = Path(value)
            if not path.is_absolute():
                raise ValueError('Qualification assets require absolute paths')
            raw = _read(path, _MAX_BYTES)
            ref = QualificationSourceReference(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))
        refs[role] = ref.model_dump(mode='json')
    profile = ProtocolQualificationProfile(job_id=job['job_id'], job_sha256=digest(job),
        source_bundle_sha256=job['source_bundle_sha256'], candidate_manifest_ref=candidate,
        candidate_sha256=candidate.sha256, sources=refs)
    verify_profile_job(profile, job)
    verify_profile_sources(profile)
    return profile


def prepare_profile(*, job, candidate_manifest_ref, assets):
    """Explicit candidate-manifest spelling for the public planning API."""
    return prepare_qualification_profile(job=job, candidate_ref=candidate_manifest_ref, assets=assets)


def qualification_execution_binding(*, profile, job, profile_ref):
    """Unsigned approval subject; no grant, reservation, run or signature."""
    verify_profile_job(profile, job)
    verify_profile_sources(profile)
    ref = QualificationSourceReference.model_validate(profile_ref)
    retained = ProtocolQualificationProfile.model_validate(decode_json(_source(ref)))
    if retained != profile:
        raise ValueError('Retained protocol profile differs from the exact execution binding')
    return {'format': 'devbench.colab-qualification-execution-binding/1', 'scope': profile.scope,
        'job_id': profile.job_id, 'job_sha256': profile.job_sha256,
        'candidate_sha256': profile.candidate_sha256,
        'candidate_manifest_ref': profile.candidate_manifest_ref.model_dump(mode='json'),
        'workflow_ref': ref.model_dump(mode='json'),
        'execution_policy': profile.execution_policy.model_dump(mode='json'),
        'approval_required': True, 'execution_granted': False, 'qualification_status': 'protocol_only'}


def _snapshot(planner, identity, session):
    value = planner.methods.get('method_plan_snapshot', identity)
    if digest(value) != identity or value.get('session_id') != session:
        raise ValueError('Protocol qualification retained planner snapshot differs')
    return value


def _material(node, planner, session, plan, profile):
    from .colab_workflow import ColabQualificationPlan
    if type(planner) is not ColabQualificationPlan:
        raise TypeError('Nonbiological checks require the exact Colab qualification plan')
    if node not in _NODES:
        raise ValueError('Unknown protocol qualification node')
    context = plan.get('context', {})
    task, dataset = context.get('task', {}), context.get('dataset_summary', {})
    if (plan.get('session_id') != session or task.get('scope') != profile.scope
            or task.get('objective_kind') != 'software_integration_qualification'
            or task.get('job_sha256') != profile.job_sha256
            or dataset.get('scope') != 'synthetic_software_fixture_only' or dataset.get('observed') != {}):
        raise ValueError('Only explicit nonbiological synthetic qualification scope is admitted')
    verify_profile_job(profile, planner.execution.job)
    refs = _sources(profile)
    if node == 'conclusion_review':
        recommendation = plan.get('recommendation') or {}
        if (recommendation.get('scope') != profile.scope
                or recommendation.get('round_trip_observed') is not True
                or recommendation.get('integration_accepted') is not False
                or recommendation.get('independent_verification_required') is not True
                or recommendation.get('scientific_admission') is not False
                or len(plan.get('results', [])) != 1):
            raise ValueError('Protocol conclusion requires an actual retained remote result and provisional recommendation')
        # The planner independently checks retained remote hashes/provenance.
        # It must never invoke the remote numerical verifier locally.
        assessment = planner.evidence_assessment(session)
        if assessment != {key: recommendation[key] for key in (
                'round_trip_observed', 'integration_accepted', 'independent_verification_required', 'scientific_admission')}:
            raise ValueError('Protocol conclusion differs from retained remote evidence')
    return {'node': node, 'session_id': session, 'plan_sha256': digest(plan),
        'profile_sha256': digest(profile.model_dump(mode='json')), 'source_refs': refs,
        'candidate_manifest_ref': profile.candidate_manifest_ref.model_dump(mode='json'),
        'candidate_sha256': profile.candidate_sha256,
        'check': _CHECKS[node],
        'check_scope': 'retained_protocol_source_and_review_record_integrity_only',
        'qualification_status': 'protocol_only', 'scientific_execution_tier': 'colab'}


def verify_protocol_qualification_evidence(node, planner, session, evidence, *, plan_snapshot):
    """Independent, read-only verifier; no runtime callbacks or model authority."""
    if type(evidence) is not dict or set(evidence) != _ENVELOPE_KEYS:
        raise ValueError('Use the exact protocol qualification envelope')
    ref = evidence['record_ref']
    if type(ref) is not dict or set(ref) != set(_REF_KEYS):
        raise ValueError('Use the exact immutable protocol artifact reference')
    actual = planner.methods.get('artifact', ref['artifact_id'])
    if ({key: actual[key] for key in _REF_KEYS} != ref or type(ref['size_bytes']) is not int
            or not 0 < ref['size_bytes'] <= _MAX_BYTES):
        raise ValueError('Protocol qualification artifact identity changed')
    raw = _read(planner.methods.artifact_path(ref['artifact_id']), _MAX_BYTES)
    if len(raw) != ref['size_bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Protocol qualification artifact bytes changed')
    record = decode_json(raw)
    record_keys = {'format', 'record_type', 'node', 'stage', 'session_id', 'status', 'qualification_status',
        'biological_review_performed', 'scientific_discovery_performed', 'scientifically_qualified',
        'numerical_verification_performed', 'plot_verification_performed', 'oci_validation_performed',
        'execution_granted', 'model_execution_performed', 'integration_accepted', 'material', 'material_sha256',
        'before_planner_hash', 'after_planner_hash', 'configuration', 'configuration_sha256'}
    if (type(record) is not dict or set(record) != record_keys
            or record.get('format') != _FORMAT or record.get('record_type') != 'nonbiological_protocol_check'
            or record.get('qualification_status') != 'protocol_only'
            or any(record.get(key) is not False for key in ('biological_review_performed', 'scientifically_qualified',
                'scientific_discovery_performed',
                'numerical_verification_performed', 'plot_verification_performed', 'oci_validation_performed',
                'execution_granted', 'model_execution_performed', 'integration_accepted'))):
        raise ValueError('Protocol checks cannot claim scientific or execution authority')
    config = record['configuration']
    profile = ProtocolQualificationProfile.model_validate(config['profile'])
    if config != _configuration(profile) or record['configuration_sha256'] != digest(config):
        raise ValueError('Protocol qualification frozen configuration changed')
    if any(record.get(key) != evidence[key] for key in _ENVELOPE_KEYS - {'record_ref'}):
        raise ValueError('Protocol qualification envelope binding changed')
    if (record.get('node') != node or record.get('session_id') != session
            or record['before_planner_hash'] != record['after_planner_hash']
            or digest(plan_snapshot) != record['after_planner_hash']
            or _snapshot(planner, record['after_planner_hash'], session) != plan_snapshot):
        raise ValueError('Protocol qualification exact planner snapshot binding differs')
    material = _material(node, planner, session, plan_snapshot, profile)
    expected_status = 'descriptive' if node == 'discovery' else 'completed'
    if (record['stage'] != node or record['material'] != material or record['material_sha256'] != digest(material)
            or record['status'] != expected_status):
        raise ValueError('Protocol qualification retained review material differs')
    return record


def _configuration(profile):
    return {'format': 'devbench.protocol-qualification-runtime/1', 'profile': profile.model_dump(mode='json'),
        'qualification_status': 'protocol_only', 'execution_granted': False}


class ProtocolQualificationRuntime:
    """Typed nonbiological services; all sixteen graph nodes remain mandatory."""
    def __init__(self, *, profile):
        if type(profile) is not ProtocolQualificationProfile:
            raise TypeError('Use the exact typed nonbiological protocol qualification profile')
        _sources(profile)
        self.profile = profile
        self._configuration = _configuration(profile)

    def configuration(self):
        if _configuration(self.profile) != self._configuration:
            raise ValueError('Protocol qualification frozen configuration changed')
        _sources(self.profile)
        return deepcopy(self._configuration)

    def _operation(self, node, planner, session, before_hash):
        if node not in _NODES:
            raise ValueError('Unknown protocol qualification node')
        identity = digest({'session_id': session, 'node': node, 'before_planner_hash': before_hash,
                           'configuration_sha256': digest(self.configuration())})
        return Path(planner.methods.root).resolve() / 'workflow-protocol-qualification' / identity

    def verify(self, node, planner, session, evidence, *, plan_snapshot=None):
        config = self.configuration()
        record = verify_protocol_qualification_evidence(node, planner, session, evidence,
            plan_snapshot=planner.get(session) if plan_snapshot is None else plan_snapshot)
        if record['configuration'] != config:
            raise ValueError('Protocol qualification runtime identity differs')
        return record

    def reconcile(self, node, planner, session, *, before_planner_hash):
        """Only reuse a completed sealed record; uncertainty never redispatches."""
        index = self._operation(node, planner, session, before_planner_hash) / 'completed.json'
        if not index.is_file():
            raise ValueError('Protocol operation uncertain: retained completion required; no automatic replay')
        evidence = decode_json(_read(index, 128_000))
        snapshot = _snapshot(planner, before_planner_hash, session)
        self.verify(node, planner, session, evidence, plan_snapshot=snapshot)
        return evidence

    def perform(self, node, planner, session, *, actor, selector):
        config, plan = self.configuration(), planner.get(session)
        material = _material(node, planner, session, plan, self.profile)
        before_hash = digest(plan)
        _snapshot(planner, before_hash, session)
        root = self._operation(node, planner, session, before_hash)
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            return self.reconcile(node, planner, session, before_planner_hash=before_hash)
        atomic_json(root / 'started.json', {'node': node, 'session_id': session,
            'before_planner_hash': before_hash, 'configuration_sha256': digest(config), 'actor': actor})
        record = {'format': _FORMAT, 'record_type': 'nonbiological_protocol_check',
            'node': node, 'stage': node, 'session_id': session,
            'status': 'descriptive' if node == 'discovery' else 'completed',
            'qualification_status': 'protocol_only', 'biological_review_performed': False,
            'scientific_discovery_performed': False,
            'scientifically_qualified': False, 'numerical_verification_performed': False,
            'plot_verification_performed': False, 'oci_validation_performed': False,
            'execution_granted': False, 'model_execution_performed': False, 'integration_accepted': False,
            'material': material, 'material_sha256': digest(material),
            'before_planner_hash': before_hash, 'after_planner_hash': before_hash,
            'configuration': config, 'configuration_sha256': digest(config)}
        path = root / 'evidence.json'
        with path.open('xb') as stream:
            stream.write(canonical_json(record))
            stream.flush()
            os.fsync(stream.fileno())
        artifact = planner.methods.import_artifact(path, artifact_type='json', actor=actor)
        evidence = {'record_ref': {key: artifact[key] for key in _REF_KEYS},
            **{key: record[key] for key in _ENVELOPE_KEYS - {'record_ref'}}}
        self.verify(node, planner, session, evidence, plan_snapshot=plan)
        atomic_json(root / 'completed.json', evidence)
        return evidence
