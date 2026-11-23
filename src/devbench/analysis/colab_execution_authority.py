"""Trusted exact-job custody for the one-use synthetic Colab bootstrap."""
from dataclasses import dataclass
from .checkpoints import digest
from devharness_core.provenance import canonical_json
from .colab_execution_contract import validate_job
from .colab_qualification_contract import validate_colab_qualification_request, _hex


@dataclass(frozen=True, slots=True)
class ColabQualificationContext:
    service: object
    approved_request_sha256: str
    approved_job_sha256: str

    def __post_init__(self):
        from .methods import MethodService
        if type(self.service) is not MethodService:
            raise TypeError('Actual registered MethodService is required')
        _hex(self.approved_request_sha256)
        _hex(self.approved_job_sha256)

    def verify(self, request, *, workspace, job, now):
        from .methods import MethodService
        if type(self.service) is not MethodService:
            raise PermissionError('Colab MethodService identity changed')
        job = validate_job(job)
        request = validate_colab_qualification_request(request, workspace=workspace, job=job, now=now)
        if digest(request) != self.approved_request_sha256 or digest(job) != self.approved_job_sha256:
            raise PermissionError('Bootstrap lacks exact trusted coordinator approval')
        # The separately signed workflow_ref must describe this exact typed
        # nonbiological protocol and frozen combined candidate, never opaque
        # caller-supplied workflow labels or a three-file worker bundle alone.
        from .rna_scientific_trial_contract import bounded_source
        from devbench.study.evidence import decode_json
        from .workflow_protocol_qualification import (ProtocolQualificationProfile,
            verify_profile_sources, verify_profile_job)
        profile = ProtocolQualificationProfile.model_validate(
            decode_json(bounded_source(request['workflow_ref'], workspace)))
        verify_profile_job(profile, job)
        for ref in [*profile.sources.model_dump(mode='json').values(),
                    profile.candidate_manifest_ref.model_dump(mode='json')]:
            bounded_source(ref, workspace)
        for role, key in (('verifier', 'validator_ref'), ('expectations', 'expected_values_ref')):
            if getattr(profile.sources, role).model_dump(mode='json') != request['bindings'][key]:
                raise PermissionError('Protocol qualification source differs from signed request bindings')
        # This concrete verifier also validates the full candidate manifest,
        # installed RECORDs, source closure and frozen origin bindings.
        verify_profile_sources(profile)
        step = self.service.get('step', job['step_id'])
        method = self.service.method(job['method_version_id'])
        if (step['method_version_id'] != job['method_version_id'] or method['contract'] != job['method_contract']
                or canonical_json(step['parameters']) != canonical_json(job['parameters']) or set(step['inputs']) != set(job['inputs'])):
            raise PermissionError('Registered method or step differs from frozen Colab job')
        for artifact_key, expected in (('source_artifact',job['source']), ('environment_artifact',job['environment_lock'])):
            self.service.artifact_path(method[artifact_key])
            record = self.service.get('artifact',method[artifact_key])
            if {key:record[key] for key in ('sha256','size_bytes')} != expected:
                raise PermissionError('Current registered Colab source or lock custody differs')
        for name, identity in step['inputs'].items():
            self.service.artifact_path(identity)
            record = self.service.get('artifact',identity)
            if {key:record[key] for key in ('sha256','size_bytes','artifact_type')} != job['inputs'][name]:
                raise PermissionError('Current registered Colab input custody differs')
        return request, job
