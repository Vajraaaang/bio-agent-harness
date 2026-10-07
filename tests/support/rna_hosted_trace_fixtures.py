"""Source-bound thin test wrappers around the installed software controls."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4
from devbench.study.contracts import canonical_json_bytes
from devbench.study.rna_hosted_fixtures import fixture_manifest


def fake_inputs(output, *, matrix=None):
    limits = dict(max_spans=64, max_bytes=262144, max_mutation_requests=1200,
        max_read_requests=2400, max_delivery_attempts=3, total_deadline_seconds=900)
    samples = matrix or [{'fixture_id': 'pre_fit_methods-0', 'stage': 'pre_fit_methods', 'allocation': 0, 'repeat': 1}]
    criteria = dict(format='devbench.rna-hosted-criteria/1', sample_matrix=samples,
        api_p95_upper_seconds=5, visibility_deadline_seconds=30, poll_interval_seconds=.5,
        dashboard_window_seconds=30, max_extra_model_calls=0, max_extra_fits=0,
        max_extra_retrievals=0, max_source_writes=0, max_privacy_leaks=0,
        required_topology_fraction=1, censored_samples_fail=True, limits=limits)
    identities = dict(source_digest='a'*64, runtime_digest='b'*64, policy_digest='c'*64,
        criteria_digest=sha256(canonical_json_bytes(criteria)).hexdigest(),
        fixture_manifest_digest=sha256(canonical_json_bytes(fixture_manifest())).hexdigest(),
        live_session_digest=None, authorization_record_digest=None)
    try:
        from devbench.study.rna_hosted_qualification import installed_identity
        actual=installed_identity();actual.pop('protected_paths')
        identities.update(actual)
    except ValueError:
        pass  # Source-only unit rejection tests; not retained qualification identities.
    manifest = dict(format='devbench.rna-hosted-qualification/1', qualification_id=str(uuid4()),
        mode='fake', data_scope='synthetic', output_path=str(output), endpoint=None,
        workspace_id=None, project_id=None, project_name=None,
        expires_at=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(),
        allowed_operations=['create_run','update_run','read_run'], authorize_upload=False,
        authorize_remote_read=False, limits=limits, **identities)
    return manifest, criteria, identities
