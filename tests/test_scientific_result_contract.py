"""Synthetic result-evidence controls; no estimator or scientific qualification."""
import copy
import hashlib
import json

import pytest


def api():
    from devbench.analysis import scientific_judgment
    assert hasattr(scientific_judgment, 'parse_claim_packet'), 'P1 versioned packet dispatch is not implemented'
    return scientific_judgment


def result_record():
    return {
        'record_format': 'devbench.scientific-result-diagnostic/1',
        'result_id': 'result', 'input_root': 'a' * 64,
        'cohort': {'cohort_id': 'cohort', 'sample_ids': ['s1', 's2', 's3', 's4'],
                   'biological_unit_ids': ['u1', 'u2', 'u3', 'u4']},
        'biological_unit_count': 4,
        'contrast': {'contrast_id': 'treated-versus-control', 'reference': 'control',
                     'comparison': 'treated', 'operation': 'comparison_minus_reference'},
        'method': {'method_id': 'synthetic-nb', 'version': 'fixture-1',
                   'normalization': 'RLE', 'test': 'Wald', 'effect_scale': 'log2_relative_expression',
                   'effect_kind': 'unshrunken'},
        'family': {'family_id': 'family', 'eligible_ids': ['g1', 'g2'], 'complete': True,
                   'adjustment': 'benjamini_hochberg',
                   'failed_hypothesis_policy': 'retain_null_use_one_for_bh_denominator'},
        'rows': [
            {'feature_id': 'g1', 'status': 'ok', 'effect': 1.23456, 'p_value': 1e-50,
             'adjusted_p_value': 2e-50, 'standard_error': .1, 'missing_reasons': {}},
            {'feature_id': 'g2', 'status': 'ok', 'effect': -.2, 'p_value': .4,
             'adjusted_p_value': .4, 'standard_error': .2, 'missing_reasons': {}},
        ],
    }


def write_artifact(root, name, record, evidence_id=None, kind='diagnostic'):
    path = root / name
    path.write_text(json.dumps(record, allow_nan=False), encoding='utf-8')
    return {'evidence_id': evidence_id or name.split('.')[0], 'path': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'kind': kind}


def packet_record(root, result=None):
    from devbench.study.contracts import content_sha256
    result = result_record() if result is None else result
    evidence = write_artifact(root, 'result.json', result, evidence_id='result')
    return {
        'record_format': 'devbench.scientific-claim-packet/2',
        'packet_id': 'packet', 'input_root': 'a' * 64, 'claim_id': 'claim',
        'claim_text': 'The synthetic g1 coefficient is positive; its rounded value is 1.23.',
        'objective': 'Check a frozen synthetic result.', 'assay': 'synthetic expression',
        'measurement': 'raw_counts', 'assay_semantics': 'known',
        'units_design': 'Four fixture units; no biological inference claimed.', 'intent': 'infer',
        'assumptions': [], 'observations': [], 'declarations': [], 'inferences': [], 'unknowns': [],
        'evidence': [evidence], 'execution': 'completed', 'objective_validity': 'verified',
        'operational_feasibility': 'available',
        'result_bindings': [{'evidence_id': 'result', 'result_id': 'result',
                             'cohort_sha256': content_sha256(result['cohort']),
                             'contrast_id': 'treated-versus-control', 'method_id': 'synthetic-nb',
                             'method_version': 'fixture-1', 'effect_scale': 'log2_relative_expression'}],
        'presentation_rules': [
            {'rule_id': 'effect', 'evidence_id': 'result', 'pointer': '/rows/0/effect',
             'metric_kind': 'rounded', 'decimal_places': 2},
            {'rule_id': 'probability', 'evidence_id': 'result', 'pointer': '/rows/0/p_value',
             'metric_kind': 'probability', 'relative_tolerance': 1e-8, 'decision_threshold': .05},
            {'rule_id': 'direction', 'evidence_id': 'result', 'pointer': '/rows/0/effect',
             'metric_kind': 'direction'},
            {'rule_id': 'units', 'evidence_id': 'result', 'pointer': '/biological_unit_count',
             'metric_kind': 'count'},
        ],
        'qc_policies': [], 'plot_bindings': [],
    }


def packet(root, result=None):
    return api().parse_claim_packet(packet_record(root, result))


def judgment_record(p, assertions=()):
    return {
        'record_format': 'devbench.scientific-judgment/2',
        'packet_sha256': api().packet_sha256(p), 'claim_id': p.claim_id,
        'reviewer_id': 'first', 'stage': 'independent', 'scientific_status': 'supported',
        'evidence_ids': ['result'], 'rationale': 'Fixture numbers agree with the retained record.',
        'evidence_quality': 'verified_artifact', 'action_consequences': 'No execution authority.',
        'competing_explanations': [], 'missing_evidence': None,
        'presentation_assertions': list(assertions),
    }


def judgment(p, assertions=()):
    return api().parse_claim_judgment(judgment_record(p, assertions))


def assert_valid(root, result):
    p = packet(root, result)
    return api().verified_artifacts(p)['result']


def test_v1_fixed_digest_and_shape_remain_unchanged():
    from devbench.analysis import scientific_judgment as legacy
    p = legacy.ClaimPacket(packet_id='frozen-v1', input_root='a'*64, claim_id='claim',
        claim_text='Synthetic descriptive fixture.', objective='Preserve the legacy contract.',
        assay='synthetic', measurement='continuous', assay_semantics='known',
        units_design='Fixture units only.', intent='diagnostic_flagging', assumptions=(),
        observations=(), declarations=(), inferences=(), unknowns=(),
        evidence=({'evidence_id':'diagnostic', 'path':'/synthetic-fixture/diagnostic.json',
                   'sha256':'b'*64, 'kind':'diagnostic'},),
        execution='completed', objective_validity='verified', operational_feasibility='available')
    j = legacy.ClaimJudgment(packet_sha256=legacy.packet_sha256(p), claim_id='claim', reviewer_id='first',
        stage='independent', scientific_status='supported', evidence_ids=('diagnostic',),
        rationale='Synthetic fixture only.', evidence_quality='verified_artifact',
        action_consequences='No execution authority.', competing_explanations=(), missing_evidence=None)
    assert legacy.packet_sha256(p) == 'c83086ac692da77ffc692f891de93cbd453789e0a72e9dc7e9f148e3c885de0f'
    assert legacy.judgment_sha256(j) == 'fefd61aa1c11fde2ac6bc33c1887e4095a45ca65729c3e8cd84dedbb1edc3c3f'
    assert not {'result_bindings', 'presentation_rules', 'qc_policies'} & p.model_dump().keys()
    assert not {'presentation_assertions'} & j.model_dump().keys()


def test_v2_result_verification_recomputes_bh_and_accepts_finite_tiny_probabilities(tmp_path):
    content = assert_valid(tmp_path, result_record())
    assert content['rows'][0]['p_value'] == 1e-50


@pytest.mark.parametrize('change', ['wrong_family', 'tiny_q_error', 'duplicate_feature', 'missing_row'])
def test_result_family_corruption_is_rejected(tmp_path, change):
    result = result_record()
    if change == 'wrong_family': result['family']['eligible_ids'].append('g3')
    elif change == 'tiny_q_error': result['rows'][0]['adjusted_p_value'] = 2e-30
    elif change == 'duplicate_feature': result['rows'][1]['feature_id'] = 'g1'
    else: result['rows'].pop()
    with pytest.raises(ValueError): assert_valid(tmp_path, result)


def test_legitimate_independent_filtering_preserves_effect_and_raw_p(tmp_path):
    result = result_record()
    result['rows'].append({'feature_id': 'g3', 'status': 'independent_filtered', 'effect': .3,
        'p_value': .8, 'adjusted_p_value': None, 'standard_error': .4,
        'missing_reasons': {'adjusted_p_value': 'independent_filtering'}})
    actual = assert_valid(tmp_path, result)
    assert actual['rows'][2]['effect'] == .3 and actual['rows'][2]['p_value'] == .8
    assert actual['rows'][2]['adjusted_p_value'] is None


def test_failed_hypothesis_retains_nulls_and_declared_bh_denominator(tmp_path):
    result = result_record()
    result['family']['eligible_ids'].append('g3')
    result['rows'][0]['adjusted_p_value'] = 3e-50
    result['rows'][1]['adjusted_p_value'] = .6
    result['rows'].append({'feature_id':'g3','status':'fit_failed','effect':None,
        'p_value':None,'adjusted_p_value':None,'standard_error':None,
        'missing_reasons':{name:'fit_failed' for name in ('effect','p_value','adjusted_p_value','standard_error')}})
    actual = assert_valid(tmp_path, result)
    assert actual['rows'][2]['p_value'] is None
    assert actual['rows'][1]['adjusted_p_value'] == .6


@pytest.mark.parametrize('missing', ['reason_absent', 'reason_with_number', 'fabricated_zero'])
def test_missing_statistics_require_consistent_per_metric_reasons(tmp_path, missing):
    result = result_record()
    row = result['rows'][0]
    if missing == 'reason_absent': row['standard_error'] = None
    elif missing == 'reason_with_number': row['missing_reasons']['p_value'] = 'fit_failed'
    else:
        row.update(status='fit_failed', effect=0., p_value=0., adjusted_p_value=0., standard_error=0.)
    with pytest.raises(ValueError): assert_valid(tmp_path, result)


@pytest.mark.parametrize('field,value', [('contrast_id','control-versus-treated'),
    ('method_version','fixture-2'), ('effect_scale','normalized_expression'), ('cohort_sha256','f'*64)])
def test_result_binding_rejects_wrong_cohort_method_contrast_or_scale(tmp_path, field, value):
    data = packet_record(tmp_path)
    data['result_bindings'][0][field] = value
    with pytest.raises(ValueError):
        api().verified_artifacts(api().parse_claim_packet(data))


def test_repeated_sample_labels_do_not_inflate_observed_unit_count(tmp_path):
    result = result_record()
    result['cohort']['biological_unit_ids'] = ['u1', 'u1', 'u2', 'u2']
    with pytest.raises(ValueError): assert_valid(tmp_path, result)


def test_result_input_identity_must_match_the_packet(tmp_path):
    data = packet_record(tmp_path)
    data['input_root'] = 'd'*64
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_result_evidence_remains_bounded_before_reviewer_projection(tmp_path):
    result = result_record()
    result['family']['complete'] = False
    result['rows'] = [{**result['rows'][0], 'feature_id':'g'+str(i)} for i in range(2000)]
    with pytest.raises(ValueError): assert_valid(tmp_path, result)


@pytest.mark.parametrize('rule,value', [('effect',1.24), ('probability',1e-40),
    ('probability',0.), ('direction','negative'), ('units',4.1), ('units',True)])
def test_metric_specific_rules_reject_wrong_claims(tmp_path, rule, value):
    p = packet(tmp_path)
    with pytest.raises(ValueError):
        api().validate_judgment(p, judgment(p, ({'rule_id':rule, 'value':value},)))


def test_coordinator_rules_allow_rounding_probability_sign_and_exact_count(tmp_path):
    p = packet(tmp_path)
    assertions = ({'rule_id':'effect','value':1.23}, {'rule_id':'probability','value':1e-50},
                  {'rule_id':'direction','value':'positive'}, {'rule_id':'units','value':4})
    assert api().validate_judgment(p, judgment(p, assertions))['scientific_judgment'] == 'supported'


def test_v2_does_not_relax_exact_numerical_assertions(tmp_path):
    p = packet(tmp_path)
    record = judgment_record(p)
    record['numerical_assertions'] = [{'evidence_id':'result','pointer':'/rows/0/effect','value':1.23}]
    with pytest.raises(ValueError):
        api().validate_judgment(p,api().parse_claim_judgment(record))


@pytest.mark.parametrize('attack', ['tolerance', 'unknown_rule', 'changed_significance'])
def test_reviewer_cannot_select_tolerance_or_cross_a_significance_boundary(tmp_path, attack):
    data = packet_record(tmp_path)
    if attack == 'changed_significance':
        result = result_record()
        result['rows'][0]['p_value'] = .05
        result['rows'][0]['adjusted_p_value'] = .1
        data = packet_record(tmp_path, result)
    p = api().parse_claim_packet(data)
    assertion = {'rule_id':'probability', 'value':.0499999999 if attack == 'changed_significance' else 1e-30}
    if attack == 'tolerance': assertion['absolute_tolerance'] = .01
    if attack == 'unknown_rule': assertion['rule_id'] = 'self-chosen'
    with pytest.raises(ValueError): api().validate_judgment(p, judgment(p,(assertion,)))


def test_incomplete_family_is_explicitly_unassessed_without_blocking_other_evidence(tmp_path):
    result = result_record()
    result['family']['complete'] = False
    p = packet(tmp_path, result)
    context = api().judgment_context(p)
    assert context['result_checks'][0]['multiplicity']['status'] == 'not_assessed'
    assert api().validate_judgment(p, judgment(p,({'rule_id':'effect','value':1.23},)))['scientific_judgment'] == 'supported'


def qc_policy():
    return {'policy_id':'contextual-depth', 'version':'fixture-1', 'metric':'mapped_fraction',
        'unit':'fraction', 'denominator':'mapped_reads_plus_unmapped_reads',
        'applicability':'Synthetic capture protocol only.',
        'provenance':{'origin':'protocol', 'record_id':'synthetic-protocol', 'record_sha256':'c'*64,
                      'rationale':'A contextual review threshold, not a scientific validity certificate.'},
        'operator':'less_than', 'threshold':.7, 'observed_value':.5,
        'consequence':'warning', 'limitations':'Fixture policy has no biological calibration.'}


def test_contextual_qc_warning_is_distinct_from_fatal_eligibility(tmp_path):
    data = packet_record(tmp_path)
    data['qc_policies'] = [qc_policy()]
    p = api().parse_claim_packet(data)
    context = api().judgment_context(p)
    assert context['qc_checks'][0]['status'] == 'warning'
    assert api().validate_judgment(p, judgment(p))['scientific_judgment'] == 'supported'
    fatal = copy.deepcopy(data)
    fatal['objective_checks'] = [{'check':'replication', 'status':'failed', 'evidence_ids':['result'],
                                 'reason':'A separate fixture biological-unit eligibility failure.'}]
    p = api().parse_claim_packet(fatal)
    assert api().validate_judgment(p, judgment(p))['scientific_judgment'] == 'demonstrable_error'


def test_qc_policy_cannot_claim_user_origin_without_a_requirement_record(tmp_path):
    data = packet_record(tmp_path)
    policy = qc_policy()
    policy['provenance']['origin'] = 'user'
    data['qc_policies'] = [policy]
    with pytest.raises(ValueError): api().parse_claim_packet(data)


def test_v1_exact_numerical_assertion_is_not_relaxed_by_v2_rules(tmp_path):
    from tests.test_scientific_judgment_contract import packet as legacy_packet, judgment as legacy_judgment
    from devbench.analysis.scientific_judgment import NumericalAssertion, validate_judgment
    p = legacy_packet(tmp_path)
    with pytest.raises(ValueError, match='numerical'):
        validate_judgment(p,legacy_judgment(p,numerical_assertions=(NumericalAssertion(
            evidence_id='diagnostic',pointer='/p',value=.10000000001),)))


def test_method_specific_missing_standard_error_does_not_destroy_other_metrics(tmp_path):
    result = result_record()
    result['rows'][0]['standard_error'] = None
    result['rows'][0]['missing_reasons'] = {'standard_error':'not_reported_by_method'}
    actual = assert_valid(tmp_path, result)
    assert actual['rows'][0]['p_value'] == 1e-50


def test_unsupported_adjustment_is_honestly_unassessed(tmp_path):
    result = result_record()
    result['family']['adjustment'] = 'storey_q'
    p = packet(tmp_path, result)
    assert api().judgment_context(p)['result_checks'][0]['multiplicity']['status'] == 'not_assessed'


def test_shrunken_effect_requires_declared_test_estimate_source(tmp_path):
    result = result_record()
    result['method']['effect_kind'] = 'shrunken'
    with pytest.raises(ValueError): assert_valid(tmp_path, result)
    result['method']['test_effect_source'] = 'Unshrunken Wald coefficient from the same fit.'
    assert assert_valid(tmp_path, result)['method']['effect_kind'] == 'shrunken'


@pytest.mark.parametrize('field,value', [('family_id','other'),('family_sha256','e'*64)])
def test_optional_explicit_family_identity_cannot_be_spoofed(tmp_path,field,value):
    data = packet_record(tmp_path)
    data['result_bindings'][0][field] = value
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


@pytest.mark.parametrize('change', ['wrong_metric','wrong_feature','wrong_unit'])
def test_coordinator_presentation_rule_must_match_typed_metric_identity(tmp_path,change):
    data = packet_record(tmp_path)
    rule = data['presentation_rules'][0]
    if change == 'wrong_metric':
        rule.update(pointer='/rows/0/p_value')
    elif change == 'wrong_feature': rule['feature_id'] = 'g2'
    else: rule['unit'] = 'normalized_expression'
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_log_probability_rule_cannot_change_orders_of_magnitude(tmp_path):
    data = packet_record(tmp_path)
    data['presentation_rules'][1].update(metric_kind='log_probability',log10_tolerance=.001)
    data['presentation_rules'][1].pop('relative_tolerance')
    p = api().parse_claim_packet(data)
    api().validate_judgment(p,judgment(p,({'rule_id':'probability','value':1.0001e-50},)))
    with pytest.raises(ValueError):
        api().validate_judgment(p,judgment(p,({'rule_id':'probability','value':1e-49},)))


def test_missing_presentation_preserves_null_and_its_reason(tmp_path):
    data = packet_record(tmp_path)
    result = result_record()
    result['rows'].append({'feature_id':'g3','status':'independent_filtered','effect':.1,
        'p_value':.8,'adjusted_p_value':None,'standard_error':.4,
        'missing_reasons':{'adjusted_p_value':'independent_filtering'}})
    data = packet_record(tmp_path,result)
    data['presentation_rules'].append({'rule_id':'missing-q','evidence_id':'result',
        'pointer':'/rows/2/adjusted_p_value','metric_kind':'missing','missing_reason':'independent_filtering'})
    p = api().parse_claim_packet(data)
    api().validate_judgment(p,judgment(p,({'rule_id':'missing-q','value':None,'missing_reason':'independent_filtering'},)))
    with pytest.raises(ValueError):
        api().validate_judgment(p,judgment(p,({'rule_id':'missing-q','value':0.},)))


def test_relative_effect_tolerance_cannot_erase_a_direction(tmp_path):
    data = packet_record(tmp_path)
    data['presentation_rules'][0] = {'rule_id':'effect','evidence_id':'result',
        'pointer':'/rows/0/effect','metric_kind':'relative','relative_tolerance':1.}
    p = api().parse_claim_packet(data)
    with pytest.raises(ValueError): api().validate_judgment(p,judgment(p,({'rule_id':'effect','value':0.},)))


def test_unknown_typed_result_version_cannot_become_unchecked_generic_json(tmp_path):
    data = packet_record(tmp_path)
    result = result_record()
    result['record_format'] = 'devbench.scientific-result-diagnostic/2'
    data['evidence'].append(write_artifact(tmp_path,'unbound.json',result,'unbound'))
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_unknown_judgment_version_fails_closed(tmp_path):
    p = packet(tmp_path)
    data = judgment_record(p)
    data['record_format'] = 'devbench.scientific-judgment/3'
    with pytest.raises(ValueError): api().parse_claim_judgment(data)


def test_fatal_contextual_eligibility_reaches_independent_endpoint(tmp_path):
    from devbench.analysis.scientific_judgment import EvaluationPolicy, evaluate_judgments, policy_sha256
    data = packet_record(tmp_path)
    policy = qc_policy()
    policy['consequence'] = 'fatal_eligibility'
    data['qc_policies'] = [policy]
    p = api().parse_claim_packet(data)
    frozen = EvaluationPolicy(policy_id='panel',version='1',independent_reviewer_ids=('first','second'),adjudicator_id='third')
    ratings = []
    for reviewer in ('first','second'):
        record = judgment_record(p)
        record['reviewer_id'] = reviewer
        ratings.append(api().parse_claim_judgment(record))
    result = evaluate_judgments(p,ratings,policy=frozen,expected_policy_sha256=policy_sha256(frozen),primary_score=1)
    assert result['reason'] == 'coordinator_hard_failure'
    assert result['objective_validity'] == 'failed'


def test_qc_unavailable_observation_stays_unassessed(tmp_path):
    data = packet_record(tmp_path)
    policy = qc_policy()
    policy['observed_value'] = None
    data['qc_policies'] = [policy]
    p = api().parse_claim_packet(data)
    assert api().judgment_context(p)['qc_checks'][0]['status'] == 'not_assessed'


def test_qc_artifact_observation_is_bound_to_captured_number(tmp_path):
    data = packet_record(tmp_path)
    policy = qc_policy()
    policy.update(observed_evidence_id='result',observed_pointer='/biological_unit_count')
    data['qc_policies'] = [policy]
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def gene_set_packet_record(root,kind='ranked_enrichment'):
    data = packet_record(root)
    library = {'record_format':'devbench.scientific-gene-set-library/1','database_id':'fixture-db',
        'version':'1','sets':[{'set_id':'set1','feature_ids':['g1','g2']}]}
    library_evidence = write_artifact(root,'library.json',library,'library')
    data['evidence'].append(library_evidence)
    metadata = {'kind':kind,'database':{'evidence_id':'library','sha256':library_evidence['sha256'],
        'database_id':'fixture-db','version':'1'},'source_result_evidence_id':'result',
        'source_result_sha256':data['evidence'][0]['sha256'],'contrast_id':'treated-versus-control'}
    if kind == 'ranked_enrichment':
        ranking = {'record_format':'devbench.scientific-ranking/1','source_result_sha256':data['evidence'][0]['sha256'],
            'contrast_id':'treated-versus-control','statistic':'effect','direction':'descending',
            'feature_ids':['g1','g2'],'values':[1.23456,-.2]}
        ranking_evidence = write_artifact(root,'ranking.json',ranking,'ranking')
        data['evidence'].append(ranking_evidence)
        metadata['ranking'] = {'evidence_id':'ranking','sha256':ranking_evidence['sha256'],
                               'statistic':'effect','direction':'descending'}
    else:
        mapping = {'record_format':'devbench.scientific-gene-mapping/1','mapping_id':'mapping',
            'version':'1','source_namespace':'fixture','target_namespace':'fixture',
            'provenance':'Frozen fixture mapping, no biological qualification.',
            'records':[{'source_id':'g1','target_id':'g1','status':'mapped'},
                       {'source_id':'g2','target_id':None,'status':'unmapped'}]}
        mapping_evidence = write_artifact(root,'mapping.json',mapping,'mapping')
        universe = {'record_format':'devbench.scientific-gene-set-universe/1',
            'source_result_sha256':data['evidence'][0]['sha256'],'contrast_id':'treated-versus-control',
            'eligible_ids':['g1','g2'],'mapped_ids':['g1'],
            'mapping':{'evidence_id':'mapping','sha256':mapping_evidence['sha256']}}
        universe_evidence = write_artifact(root,'universe.json',universe,'universe')
        selection = {'record_format':'devbench.scientific-gene-set-selection/1',
            'source_result_sha256':data['evidence'][0]['sha256'],'contrast_id':'treated-versus-control',
            'feature_ids':['g1'],'provenance':'Frozen selected fixture genes; no optimal threshold claim.'}
        selection_evidence = write_artifact(root,'selection.json',selection,'selection')
        data['evidence'].extend([mapping_evidence,universe_evidence,selection_evidence])
        metadata['universe'] = {'evidence_id':'universe','sha256':universe_evidence['sha256']}
        metadata['selection'] = {'evidence_id':'selection','sha256':selection_evidence['sha256']}
    result = result_record()
    result.update(result_id='gene-sets',result_kind=kind,gene_set=metadata)
    result['method'].update(method_id='fixture-enrichment',normalization='supplied-ranking' if kind=='ranked_enrichment' else 'none',
        test='fixture-only',effect_scale='normalized_enrichment_score' if kind=='ranked_enrichment' else 'odds_ratio')
    result['family'].update(family_id='gene-set-family',eligible_ids=['set1'])
    result['rows'] = [{'feature_id':'set1','status':'ok','effect':-1.5 if kind=='ranked_enrichment' else 2.,
        'p_value':.2,'adjusted_p_value':.2,'standard_error':None,
        'missing_reasons':{'standard_error':'not_reported_by_method'}}]
    evidence = write_artifact(root,'gene-sets.json',result,'gene-sets')
    data['evidence'].append(evidence)
    data['result_bindings'].append({**data['result_bindings'][0],'evidence_id':'gene-sets',
        'result_id':'gene-sets','result_kind':kind,'method_id':'fixture-enrichment',
        'effect_scale':result['method']['effect_scale']})
    if kind == 'ranked_enrichment':
        data['presentation_rules'].append({'rule_id':'rank-direction','evidence_id':'gene-sets',
            'pointer':'/rows/0/effect','metric_kind':'rank_direction','feature_id':'set1',
            'unit':'normalized_enrichment_score'})
    return data


def test_negative_nes_supports_bottom_of_declared_rank_without_mechanistic_claim(tmp_path):
    p = api().parse_claim_packet(gene_set_packet_record(tmp_path))
    record = judgment_record(p,({'rule_id':'rank-direction','value':'enriched_toward_bottom_of_rank'},))
    record['evidence_ids'].append('gene-sets')
    assert api().validate_judgment(p,api().parse_claim_judgment(record))['scientific_judgment'] == 'supported'
    checks = api().judgment_context(p)['gene_set_checks']
    assert checks[0]['ranking_direction'] == 'descending'
    assert checks[0]['mechanistic_interpretation'] == 'not_assessed'


@pytest.mark.parametrize('claim',['pathway_inhibition','pathway_activation','enriched_toward_top_of_rank'])
def test_nes_cannot_establish_activation_inhibition_or_opposite_rank(tmp_path,claim):
    p = api().parse_claim_packet(gene_set_packet_record(tmp_path))
    record = judgment_record(p,({'rule_id':'rank-direction','value':claim},))
    record['evidence_ids'].append('gene-sets')
    with pytest.raises(ValueError): api().validate_judgment(p,api().parse_claim_judgment(record))


@pytest.mark.parametrize('change',['opposite_ranking','wrong_contrast','stale_database','gene_kind_spoof','wrong_set_id','same_gene_family'])
def test_ranked_enrichment_binds_ranking_contrast_library_and_result_kind(tmp_path,change):
    data = gene_set_packet_record(tmp_path)
    result = json.loads((tmp_path/'gene-sets.json').read_text())
    if change=='opposite_ranking': result['gene_set']['ranking']['direction']='ascending'
    elif change=='wrong_contrast': result['gene_set']['contrast_id']='reverse'
    elif change=='stale_database': result['gene_set']['database']['version']='2'
    elif change=='gene_kind_spoof': result['result_kind']='gene_effect'
    elif change=='wrong_set_id':
        result['rows'][0]['feature_id']='not-in-database'
        result['family']['eligible_ids']=['not-in-database']
    else: result['family']['family_id']='family'
    data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


def test_ora_retains_distinct_family_and_verified_eligible_mapped_universe(tmp_path):
    p = api().parse_claim_packet(gene_set_packet_record(tmp_path,'overrepresentation'))
    checks = api().judgment_context(p)['gene_set_checks']
    assert checks[0]['kind']=='overrepresentation'
    assert checks[0]['eligible_count']==2 and checks[0]['mapped_count']==1
    assert checks[0]['selected_count']==1
    assert checks[0]['mechanistic_interpretation']=='not_assessed'


@pytest.mark.parametrize('change',['wrong_eligible','wrong_mapped','missing_mapping_provenance','wrong_selection','missing_universe'])
def test_ora_rejects_universe_mismatch_or_missing_provenance(tmp_path,change):
    data = gene_set_packet_record(tmp_path,'overrepresentation')
    universe = json.loads((tmp_path/'universe.json').read_text())
    if change in {'wrong_eligible','wrong_mapped'}:
        universe['eligible_ids' if change=='wrong_eligible' else 'mapped_ids']=['g1','fabricated']
        updated=write_artifact(tmp_path,'universe.json',universe,'universe')
        data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']=='universe')]=updated
        result=json.loads((tmp_path/'gene-sets.json').read_text())
        result['gene_set']['universe']['sha256']=updated['sha256']
        data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    elif change=='missing_mapping_provenance':
        mapping=json.loads((tmp_path/'mapping.json').read_text())
        mapping.pop('provenance')
        updated=write_artifact(tmp_path,'mapping.json',mapping,'mapping')
        data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']=='mapping')]=updated
        universe['mapping']['sha256']=updated['sha256']
        updated=write_artifact(tmp_path,'universe.json',universe,'universe')
        data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']=='universe')]=updated
        result=json.loads((tmp_path/'gene-sets.json').read_text())
        result['gene_set']['universe']['sha256']=updated['sha256']
        data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    elif change=='wrong_selection':
        selection=json.loads((tmp_path/'selection.json').read_text())
        selection['feature_ids']=['g2']
        updated=write_artifact(tmp_path,'selection.json',selection,'selection')
        data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']=='selection')]=updated
        result=json.loads((tmp_path/'gene-sets.json').read_text())
        result['gene_set']['selection']['sha256']=updated['sha256']
        data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    else:
        result=json.loads((tmp_path/'gene-sets.json').read_text())
        result['gene_set'].pop('universe')
        data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    with pytest.raises(ValueError): api().verified_artifacts(api().parse_claim_packet(data))


@pytest.mark.parametrize('claimed_overlap',[.5,2.,1e100])
def test_ora_overlap_count_must_equal_retained_selected_mapped_set_intersection(tmp_path,claimed_overlap):
    data=gene_set_packet_record(tmp_path,'overrepresentation')
    result=json.loads((tmp_path/'gene-sets.json').read_text())
    result['method']['effect_scale']='overlap_count'
    result['rows'][0]['effect']=claimed_overlap
    data['result_bindings'][1]['effect_scale']='overlap_count'
    data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    p=api().parse_claim_packet(data)
    with pytest.raises(ValueError):
        record=judgment_record(p)
        record['evidence_ids'].append('gene-sets')
        record['numerical_assertions']=[{'evidence_id':'gene-sets','pointer':'/rows/0/effect','value':claimed_overlap}]
        api().validate_judgment(p,api().parse_claim_judgment(record))


@pytest.mark.parametrize('overlap',[0,1])
def test_ora_legitimate_overlap_count_preserves_integer_presentation(tmp_path,overlap):
    data=gene_set_packet_record(tmp_path,'overrepresentation')
    result=json.loads((tmp_path/'gene-sets.json').read_text())
    result['method']['effect_scale']='overlap_count'
    result['rows'][0]['effect']=overlap
    data['result_bindings'][1]['effect_scale']='overlap_count'
    if overlap==0:
        library=json.loads((tmp_path/'library.json').read_text())
        library['sets'][0]['feature_ids']=['g2']
        updated=write_artifact(tmp_path,'library.json',library,'library')
        data['evidence'][next(i for i,e in enumerate(data['evidence']) if e['evidence_id']=='library')]=updated
        result['gene_set']['database']['sha256']=updated['sha256']
    data['evidence'][-1]=write_artifact(tmp_path,'gene-sets.json',result,'gene-sets')
    data['presentation_rules'].append({'rule_id':'overlap','evidence_id':'gene-sets','pointer':'/rows/0/effect',
        'metric_kind':'count','unit':'overlap_count'})
    p=api().parse_claim_packet(data)
    record=judgment_record(p,({'rule_id':'overlap','value':overlap},))
    record['evidence_ids'].append('gene-sets')
    assert api().validate_judgment(p,api().parse_claim_judgment(record))['scientific_judgment']=='supported'


@pytest.mark.parametrize('raw_p,captured_q,threshold',[(.025,.049999999999,.05),
    (.0249999999995,.05,.05),(.05,.099999999999,.1)])
def test_bh_recomputed_decision_cannot_cross_frozen_adjusted_probability_boundary(tmp_path,raw_p,captured_q,threshold):
    result=result_record()
    result['rows'][0].update(p_value=raw_p,adjusted_p_value=captured_q)
    data=packet_record(tmp_path,result)
    # Arithmetic closeness alone remains a valid scoped artifact audit.
    p=api().parse_claim_packet(data)
    assert api().verified_artifacts(p)['result']['rows'][0]['adjusted_p_value']==captured_q
    data['presentation_rules'][1].update(pointer='/rows/0/adjusted_p_value',decision_threshold=threshold)
    p=api().parse_claim_packet(data)
    with pytest.raises(ValueError):
        api().validate_judgment(p,judgment(p,({'rule_id':'probability','value':captured_q},)))
    # The source statistic is preserved, never silently corrected to recomputed BH.
    assert json.loads((tmp_path/'result.json').read_text())['rows'][0]['adjusted_p_value']==captured_q
