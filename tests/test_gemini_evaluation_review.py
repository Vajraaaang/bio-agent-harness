"""Fresh public trusted-callback regressions; no provider or scientific efficacy claim."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from tests.test_rna_workflow_evaluation import (
    ARMS, api, authorization, cases, digest, identities, protocol, response,
)


def gemini_analyst(request):
    mode = request['case']['public_data'].get('review_mode', 'repair')
    if mode in {'failed', 'timeout'}:
        return response(None, status=mode, tokens=2)
    if mode in {'unknown_usage', 'budget_exhausted'}:
        result = response({'estimate': 3}, tokens=2, cost=2 if mode == 'budget_exhausted' else 0)
        if mode == 'unknown_usage':
            result['usage']['cost_usd'] = None
        return result
    if request['phase'] == 'initial' and mode in {'repair', 'retain'}:
        return response({'abstention': 'insufficient declared data'}, status='abstained', tokens=2)
    if request['phase'] == 'revision':
        if mode in {'retain', 'introduce_refusal'}:
            return response({'abstention': 'insufficient declared data'}, status='abstained', tokens=3)
        if mode in {'revision_failure', 'revision_retry_exhaustion'}:
            result = response(None, status='failed', tokens=3)
            result.update(error_type='DeliberateRevisionFailure', retryable=mode.endswith('exhaustion'))
            return result
        if mode == 'revision_timeout':
            return response(None, status='timeout', tokens=3)
    return response({'estimate': 3}, tokens=2)


def gemini_reviewer(request):
    mode = request['case']['public_data'].get('review_mode', 'repair')
    if mode == 'review_failure':
        return response(None, status='failed', tokens=4)
    if mode == 'review_abstention':
        return response({'reason': 'reviewer declines'}, status='abstained', tokens=4)
    if mode == 'review_retry_budget' and request['driver_attempt'] == 1:
        result = response(None, status='failed', tokens=4)
        result.update(retryable=True, error_type='DeliberateReviewRetry')
        return result
    return response({'suggested_estimate': 3, 'initial_status_seen': request.get('initial_status')}, tokens=4)


def gemini_checks(receipt):
    records = []
    for attempt in receipt['attempts']:
        endpoints = {name: {'status': 'not_assessed', 'value': None, 'reason': 'Fixture does not assess this endpoint.'}
            for name in api().ENDPOINTS}
        initial, final = attempt['initial_output'], attempt['final_output']
        def labels(output):
            return None if output is None else (['false_refusal'] if 'abstention' in output else [])
        if final is not None and attempt['status'] in {'completed', 'abstained'}:
            for name in ('design_validity', 'appropriate_refusal'):
                endpoints[name] = {'status': 'assessed', 'value': 'abstention' not in final,
                    'evidence_sha256': digest(final)}
            if attempt['status'] == 'completed':
                endpoints['numeric_correctness'] = {'status': 'assessed', 'value': final.get('estimate') == 3,
                    'evidence_sha256': digest(final)}
        records.append({'attempt_id': attempt['attempt_id'], 'attempt_sha256': attempt['attempt_sha256'],
            'scope': api().SCOPE, 'endpoints': endpoints, 'initial_errors': labels(initial), 'final_errors': labels(final),
            'check_source_sha256': api().driver_identity(gemini_checks)['source_sha256']})
    return records


def campaign(tmp_path, mode='repair', *, limit=4):
    module = api()
    data, spec = cases(), protocol()
    # Four public cases, two unrelated locked-test clusters. Inputs freeze before any callbacks.
    extra = deepcopy(data[-1])
    for key in ('case_id', 'dataset_id', 'family_id'):
        extra[key] += '-independent'
    for key in ('cohort_ids', 'donor_ids', 'provenance_ids'):
        extra[key] = [extra[key][0] + '-independent']
    data.append(extra)
    for index, case in enumerate(data):
        case['public_data']['review_mode'] = mode[index] if isinstance(mode, list) else mode
    spec['identities']['resources']['max_calls_per_attempt'] = limit
    spec['coordinator_checks'] = {name: module.driver_identity(gemini_checks) for name in module.ENDPOINTS}
    drivers = (gemini_analyst, gemini_analyst, gemini_reviewer)
    root = tmp_path / 'campaign'
    prepared = module.prepare_comparison(root, protocol=spec, cases=data, driver_identities=identities(module, drivers))
    receipt = module.run_comparison(prepared, deterministic_driver=drivers[0], analyst_driver=drivers[1],
        reviewer_driver=drivers[2], authorization=authorization())
    return module, prepared, receipt


@pytest.mark.parametrize('endpoint', ['design_validity', 'appropriate_refusal'])
@pytest.mark.parametrize('status', ['blocked', 'failed', 'timeout', 'budget_exhausted', 'unknown_usage'])
def test_ineligible_no_output_cannot_pass_design_or_refusal(tmp_path, endpoint, status):
    module, prepared, receipt = campaign(tmp_path, 'unknown_usage' if status == 'blocked' else status)
    attempt = next(a for a in receipt['attempts'] if a['status'] == status)
    assert attempt['initial_output'] is None and attempt['final_output'] is None
    outcome = gemini_checks({'attempts': [attempt]})[0]
    outcome['endpoints'][endpoint] = {'status': 'assessed', 'value': True, 'evidence_sha256': 'a' * 64}
    with pytest.raises(ValueError, match='output|eligible'):
        module._outcomes([outcome], [attempt], module._manifest(prepared)['protocol'])


def test_false_initial_abstention_is_reviewed_and_repaired(tmp_path):
    module, prepared, receipt = campaign(tmp_path)
    reviewed = [a for a in receipt['attempts'] if a['arm'] == ARMS[2]]
    assert all([c['phase'] for c in a['calls']] == ['initial', 'review', 'revision'] for a in reviewed)
    assert all(a['initial_status'] == 'abstained' and a['status'] == 'completed' for a in reviewed)
    assert all(a['feedback']['initial_status_seen'] == 'abstained' for a in reviewed)
    assert all(a['initial_output'] == {'abstention': 'insufficient declared data'} and a['final_output'] == {'estimate': 3}
        for a in reviewed)
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    assert report['arms'][ARMS[2]]['errors']['corrected'] == 8
    paired = report['paired_effects']['appropriate_refusal']
    assert paired['independent_clusters'] == 2 and paired['paired_units'] == 4
    assert paired['status'] == 'descriptive_cluster_bootstrap' and paired['interval'] == [1, 1]
    assert report['scientific_improvement_claim'] is False
    before = {str(p): p.read_bytes() for p in prepared.root.rglob('*') if p.is_file()}
    assert module.replay_comparison(prepared.root, report['report_seal_sha256'])['valid'] is True
    assert before == {str(p): p.read_bytes() for p in prepared.root.rglob('*') if p.is_file()}


def test_terminal_analyst_abstention_remains_assessable_without_numeric_score(tmp_path):
    module, prepared, receipt = campaign(tmp_path, 'retain')
    reviewed = [a for a in receipt['attempts'] if a['arm'] == ARMS[2]]
    assert all(len(a['calls']) == 3 and a['status'] == 'abstained' and a['final_output'] is not None for a in reviewed)
    records = gemini_checks(receipt)
    # Declared fixture checker independently accepts these refusals; no scientific endorsement.
    for record in records:
        record['endpoints']['appropriate_refusal']['value'] = True
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=records)
    assert report['arms'][ARMS[2]]['endpoints']['appropriate_refusal']['passed'] == 8
    assert report['arms'][ARMS[2]]['endpoints']['numeric_correctness']['assessed'] == 0


@pytest.mark.parametrize('mode,status,calls', [
    ('revision_failure', 'failed', 3), ('revision_retry_exhaustion', 'failed', 4),
    ('revision_timeout', 'timeout', 3), ('review_failure', 'failed', 2),
    ('review_retry_budget', 'budget_exhausted', 3),
])
def test_review_introduced_execution_failure_is_typed_without_inventing_numeric_error(tmp_path, mode, status, calls):
    module, prepared, receipt = campaign(tmp_path, mode, limit=3 if mode == 'review_retry_budget' else 4)
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    arm = report['arms'][ARMS[2]]
    assert arm['completion'][status] == 8
    assert arm['execution_outcomes']['introduced_after_clean_initial'] == 8
    assert arm['execution_outcomes']['failure_after_initial'] == 8
    assert arm['execution_outcomes']['denominator'] == 8
    assert arm['errors']['introduced'] == 0
    assert arm['endpoints']['numeric_correctness']['failed'] == 0
    assert arm['endpoints']['numeric_correctness']['not_assessed'] == 8
    paired = report['paired_execution_completion']
    assert paired['paired_units'] == 4 and paired['unassessed_pairs'] == 0
    assert paired['estimate'] == -1 and paired['interval'] == [-1, -1]
    assert paired['independent_clusters'] == 2
    attempts = [a for a in receipt['attempts'] if a['arm'] == ARMS[2]]
    assert all(len(a['calls']) == calls and a['initial_output'] == {'estimate': 3} for a in attempts)
    assert all(a['final_output'] is None for a in attempts)
    assert arm['usage']['tokens'] == sum(c['usage']['tokens'] for a in attempts for c in a['calls'])


def test_reviewer_abstention_is_separate_from_execution_failure_and_final_analyst_output(tmp_path):
    module, prepared, receipt = campaign(tmp_path, 'review_abstention')
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    arm = report['arms'][ARMS[2]]
    assert arm['completion']['abstained'] == 8
    assert arm['execution_outcomes']['abstained_without_final_output'] == 8
    assert arm['execution_outcomes']['failure_after_initial'] == 0
    assert arm['endpoints']['appropriate_refusal']['not_assessed'] == 8


def test_two_cluster_bootstrap_uses_distinct_real_paired_outcomes(tmp_path):
    module, prepared, receipt = campaign(tmp_path, ['repair', 'repair', 'repair', 'retain'])
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    paired = report['paired_effects']['appropriate_refusal']
    assert paired['independent_clusters'] == 2 and paired['paired_units'] == 4
    assert paired['estimate'] == 0.5 and paired['interval'] == [0, 1]
    assert paired['unassessed_pairs'] == 0


def test_reviewer_can_introduce_false_abstention_as_checked_harm(tmp_path):
    module, prepared, receipt = campaign(tmp_path, 'introduce_refusal')
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    reviewed = report['arms'][ARMS[2]]
    assert reviewed['errors']['introduced'] == 8
    assert reviewed['errors']['not_assessed'] == 0
    assert reviewed['completion']['abstained'] == 8
    assert reviewed['execution_outcomes']['failure_after_initial'] == 0
    assert report['paired_effects']['appropriate_refusal']['estimate'] == -1
    assert report['paired_effects']['numeric_correctness']['unassessed_pairs'] == 4


@pytest.mark.parametrize('mode', ['unknown_usage', 'budget_exhausted'])
def test_campaign_stop_preserves_denominator_and_unknown_telemetry(tmp_path, mode):
    module, prepared, receipt = campaign(tmp_path, mode)
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    assert report['denominator'] == 24
    assert report['statuses']['blocked'] == 23 and report['statuses'][mode] == 1
    assert report['usage']['request_attempts'] == 1
    assert report['usage']['tokens'] == 2
    assert report['usage']['cost_usd'] == (None if mode == 'unknown_usage' else 2)
    assert report['paired_execution_completion']['paired_units'] == 4
    assert report['paired_execution_completion']['unassessed_pairs'] == 0
    assert all(a['execution_outcomes']['failure_before_initial'] == a['denominator'] for a in report['arms'].values())
    called = next(a for a in receipt['attempts'] if a['calls'])
    assert called['calls'][0]['raw_response']['output'] == {'estimate': 3}


def test_new_synthetic_legacy_v1_custody_fixture_is_readonly_and_never_rescored(tmp_path, monkeypatch):
    module, prepared, receipt = campaign(tmp_path)
    report = module.summarize_comparison(prepared, receipt, checked_outcomes=gemini_checks(receipt))
    root = prepared.root
    def store(path, value):
        data = module._encoded(value) + b'\n'
        path.write_bytes(data)
        return module._hash_bytes(data)
    # Construct a fresh synthetic legacy custody fixture. No retained campaign
    # or held-out qualification evidence is read or retuned.
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['protocol']['format'] = 'devbench.rna-workflow-comparison-protocol/1'
    manifest['protocol'].pop('coordinator_checks')
    manifest_hash = store(root / 'manifest.json', manifest)
    store(root / 'preparation-seal.json', {'format': module.FORMAT, 'manifest_sha256': manifest_hash})
    campaign_seal = json.loads((root / 'campaign-seal.json').read_bytes())
    attempt_hashes = {}
    for binding in campaign_seal['attempts']:
        path = Path(binding['path'])
        attempt = json.loads(path.read_bytes())
        attempt['manifest_sha256'] = manifest_hash
        attempt.pop('attempt_sha256')
        attempt.pop('initial_status')  # Legacy attempts predate this additive field.
        attempt['attempt_sha256'] = module._digest(attempt)
        attempt_hashes[attempt['attempt_id']] = attempt['attempt_sha256']
        binding['sha256'] = store(path, attempt)
    campaign_seal['manifest_sha256'] = manifest_hash
    campaign_hash = store(root / 'campaign-seal.json', campaign_seal)
    outcomes = json.loads((root / 'checked-outcomes.json').read_bytes())
    for outcome in outcomes:
        outcome['attempt_sha256'] = attempt_hashes[outcome['attempt_id']]
    outcomes_hash = store(root / 'checked-outcomes.json', outcomes)
    seal = json.loads((root / 'report-seal.json').read_bytes())
    seal.update(manifest_sha256=manifest_hash, campaign_seal_sha256=campaign_hash,
        checked_outcomes_sha256=outcomes_hash)
    seal_hash = store(root / 'report-seal.json', seal)
    before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail('Custody replay dispatched or rescored a synthetic legacy campaign')
    monkeypatch.setattr(module, '_invoke', forbidden)
    monkeypatch.setattr(module, '_report', forbidden)
    replay = module.replay_comparison(root, seal_hash)
    assert replay['valid'] is True and replay['legacy_unbound_checks'] is True
    assert replay['report']['paired_effects'] == report['paired_effects']
    assert before == {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='custody replay only'):
        module._validate(manifest['protocol'], manifest['cases'], manifest['drivers'])
