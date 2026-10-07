"""Synthetic interface controls; these fixtures do not qualify scientific judges."""
import hashlib
import json

import pytest

from tests.test_analysis_episodes import workspace


def contracts():
    from devbench.analysis import scientific_judgment as api
    return api


def packet(tmp_path, *, check=None, semantics='known', intent='diagnostic_flagging', execution='completed'):
    api = contracts()
    artifact = tmp_path / 'diagnostics.json'
    artifact.write_text('{"p": 0.1, "q": 0.2, "correlation": 0.8}')
    evidence = api.ArtifactEvidence(evidence_id='diagnostic', path=str(artifact),
        sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), kind='diagnostic')
    return api.ClaimPacket(packet_id='packet', claim_id='claim', input_root='a'*64,
        claim_text='The observations warrant review.', objective='Assess descriptive flags.',
        assay='synthetic expression', measurement='continuous', assay_semantics=semantics,
        units_design='Independent unit identity is unknown.', intent=intent,
        observations=('Observed atypicality.',), declarations=(), inferences=(), unknowns=('Technical cause.',),
        assumptions=('No biological causation inferred.',), evidence=(evidence,),
        objective_checks=(() if check is None else (api.ObjectiveCheck(check=check, status='failed',
            evidence_ids=('diagnostic',), reason='Independent synthetic negative control.'),)),
        execution=execution, objective_validity='verified', operational_feasibility='available')


def policy():
    return contracts().EvaluationPolicy(policy_id='frozen-policy', version='1',
        independent_reviewer_ids=('first', 'second'), adjudicator_id='third')


def judgment(p, reviewer='first', status='supported', stage='independent', **changes):
    api = contracts()
    fields=dict(packet_sha256=api.packet_sha256(p), claim_id=p.claim_id,
        reviewer_id=reviewer, stage=stage, scientific_status=status, evidence_ids=('diagnostic',),
        rationale='The diagnostic supports only descriptive review.', evidence_quality='verified_artifact',
        action_consequences='Retain all observations pending further evidence.',
        competing_explanations=('Biological heterogeneity remains possible.',),
        missing_evidence=('Independent technical QC validation.' if status=='insufficient_evidence' else None))
    return api.ClaimJudgment(**{**fields,**changes})


def endpoint(p, ratings, adjudication=None, *, bind=True):
    api=contracts(); frozen=policy()
    if bind and adjudication is not None:
        adjudication=adjudication.model_copy(update={'independent_rating_sha256':tuple(api.judgment_sha256(r) for r in ratings)})
    return api.evaluate_judgments(p, tuple(ratings), adjudication=adjudication, policy=frozen,
        expected_policy_sha256=api.policy_sha256(frozen), primary_score=0.0)


def test_contract_is_available():
    assert contracts().ClaimJudgment.model_fields['record_format'].default == 'devbench.scientific-judgment/1'


def test_claim_packet_verifies_resolvable_hash_bound_artifacts_and_blinds_paths(tmp_path):
    p=packet(tmp_path)
    context=contracts().judgment_context(p)
    assert context['diagnostics'][0]['content']['q']==.2
    assert str(tmp_path) not in json.dumps(context)
    assert not {'final_reference','model_identity','prior_verdicts'} & context.keys()
    with pytest.raises(Exception):p.claim_text='Changed'
    (tmp_path/'diagnostics.json').write_text('{}')
    with pytest.raises(ValueError,match='changed'):contracts().judgment_context(p)


def test_unknown_reference_and_forged_statistical_assertion_are_rejected(tmp_path):
    api=contracts();p=packet(tmp_path)
    with pytest.raises(ValueError,match='reference'):
        api.validate_judgment(p, judgment(p,evidence_ids=('fabricated',)))
    with pytest.raises(ValueError,match='Source excerpt'):
        api.validate_judgment(p,judgment(p,evidence_quality='source_excerpt'))
    with pytest.raises(ValueError,match='numerical'):
        api.validate_judgment(p,judgment(p,numerical_assertions=(api.NumericalAssertion(
            evidence_id='diagnostic',pointer='/p',value=.001),)))
    assert api.validate_judgment(p,judgment(p,numerical_assertions=(api.NumericalAssertion(
        evidence_id='diagnostic',pointer='/q',value=.2),)))['scientific_judgment']=='supported'


@pytest.mark.parametrize('check',['input_integrity','statistics','contrast','causality','replication','citation'])
def test_all_accept_judge_cannot_override_coordinator_hard_failure(tmp_path,check):
    p=packet(tmp_path,check=check)
    result=endpoint(p,[judgment(p),judgment(p,'second')],judgment(p,'third',stage='adjudication'))
    assert result['alternative_aware_success'] is False
    assert result['objective_validity']=='failed'
    assert result['scientific_judgment']=='demonstrable_error'
    assert len(result['independent_ratings'])==2 and result['adjudication']['scientific_status']=='supported'


def test_reference_disagreement_requires_verified_objective_and_explicit_adjudication(tmp_path):
    p=packet(tmp_path,check='reference_label')
    ratings=[judgment(p,status='defensible_alternative'),judgment(p,'second',status='defensible_alternative')]
    assert endpoint(p,ratings)['alternative_aware_success'] is None
    adjudication=judgment(p,'third',status='defensible_alternative',stage='adjudication')
    assert endpoint(p,ratings,adjudication)['alternative_aware_success'] is True
    unknown=p.model_copy(update={'objective_validity':'not_assessed'})
    assert endpoint(unknown,[judgment(unknown,status='defensible_alternative'),
        judgment(unknown,'second',status='defensible_alternative')],
        judgment(unknown,'third',status='defensible_alternative',stage='adjudication'))['alternative_aware_success'] is None


def test_two_defensible_resolutions_preserve_disagreement_and_primary_score(tmp_path):
    p=packet(tmp_path,check='resolution')
    result=endpoint(p,[judgment(p,status='supported'),judgment(p,'second',status='defensible_alternative')],
        judgment(p,'third',status='defensible_alternative',stage='adjudication'))
    assert result['alternative_aware_success'] is True
    assert result['primary_score']==0.
    assert [x['scientific_status'] for x in result['independent_ratings']]==['supported','defensible_alternative']
    assert result['expert_validation']=='unmet'


@pytest.mark.parametrize('adjudicated_status,success',[(None,None),
    ('defensible_alternative',True),('supported',True),('demonstrable_error',False),
    ('insufficient_evidence',None)])
def test_initial_error_label_remains_disputed_until_policy_bound_adjudication(tmp_path,adjudicated_status,success):
    p=packet(tmp_path,check='resolution')
    ratings=[judgment(p,status='demonstrable_error'),judgment(p,'second',status='defensible_alternative')]
    adjudication=(None if adjudicated_status is None else
        judgment(p,'third',status=adjudicated_status,stage='adjudication'))
    result=endpoint(p,ratings,adjudication)
    assert result['alternative_aware_success'] is success
    assert result['objective_validity']=='verified'
    assert result['primary_score']==0.
    assert [r['scientific_status'] for r in result['independent_ratings']]==['demonstrable_error','defensible_alternative']
    if success is None:assert result['reason']=='review_disagreement_requires_adjudication'


@pytest.mark.parametrize('first_status,second_status',[
    ('supported','defensible_alternative'),('supported','supported'),
    ('defensible_alternative','defensible_alternative')])
def test_adverse_adjudication_resolves_initial_favorable_votes_without_objective_failure(tmp_path,first_status,second_status):
    p=packet(tmp_path,check='resolution')
    ratings=[judgment(p,status=first_status),judgment(p,'second',status=second_status)]
    result=endpoint(p,ratings,judgment(p,'third',status='demonstrable_error',stage='adjudication'))
    assert result['alternative_aware_success'] is False
    assert result['scientific_judgment']=='demonstrable_error'
    assert result['reason']=='adjudicator_error_reported'
    assert result['objective_validity']=='verified'
    assert result['primary_score']==0.
    assert [r['scientific_status'] for r in result['independent_ratings']]==[first_status,second_status]


@pytest.mark.parametrize('execution',['timed_out','failed','not_run'])
def test_operational_failure_never_gets_scientific_answer_credit(tmp_path,execution):
    p=packet(tmp_path,execution=execution)
    assert endpoint(p,[judgment(p),judgment(p,'second')],judgment(p,'third',stage='adjudication'))['alternative_aware_success'] is False


def test_indeterminate_semantics_and_all_abstain_are_unavailable(tmp_path):
    p=packet(tmp_path,semantics='unknown',intent='infer')
    assert endpoint(p,[judgment(p),judgment(p,'second')])['alternative_aware_success'] is None
    p=packet(tmp_path)
    assert endpoint(p,[judgment(p,status='insufficient_evidence'),
        judgment(p,'second',status='insufficient_evidence')])['alternative_aware_success'] is None


def test_source_requires_actual_accessible_text_and_retains_applicability(tmp_path):
    api=contracts();p=packet(tmp_path)
    text='Correlation describes association and does not establish causation.'
    path=tmp_path/'source.txt';path.write_text(text)
    source=api.SourceExcerpt(title='Synthetic source control',url='https://example.org/control',
        version='fixture-1',access='full_text',accessed_on='2026-10-01',actual_text=text,
        applicability='Association claims only.',limitations='Synthetic text, not biological validation.')
    evidence=api.ArtifactEvidence(evidence_id='source',path=str(path),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),kind='source',source=source)
    p=p.model_copy(update={'evidence':(*p.evidence,evidence)})
    context=api.judgment_context(p)
    assert context['sources'][0]['actual_text']==text
    assert context['sources'][0]['version']=='fixture-1'
    forged=evidence.model_copy(update={'source':source.model_copy(update={'actual_text':'Invented conclusion.'})})
    with pytest.raises(ValueError,match='source text'):api.judgment_context(p.model_copy(update={'evidence':(forged,)}))


def test_finite_json_policy_digest_and_panel_binding_fail_closed(tmp_path):
    api=contracts();p=packet(tmp_path)
    with pytest.raises(ValueError):api.NumericalAssertion(evidence_id='diagnostic',pointer='/p',value=float('nan'))
    with pytest.raises(ValueError,match='policy'):
        api.evaluate_judgments(p,(judgment(p),),policy=policy(),expected_policy_sha256='f'*64,primary_score=0.)
    with pytest.raises(ValueError,match='reviewer'):endpoint(p,[judgment(p,'unregistered')])
    with pytest.raises(ValueError,match='missing evidence'):judgment(p,status='insufficient_evidence',missing_evidence=None)


def test_sensitivity_is_evidence_bound_and_unassessed_remains_explicit(tmp_path):
    api=contracts();p=packet(tmp_path)
    check=api.SensitivityCheck(check_id='resolution-check',kind='resolution',
        status='not_assessed',conclusion_changed=None,evidence_ids=(),reason='No matched resolution comparison available.')
    p=p.model_copy(update={'sensitivity_checks':(check,)})
    context=api.judgment_context(p)
    assert context['sensitivity_checks'][0]['conclusion_changed'] is None
    with pytest.raises(ValueError,match='sensitivity'):api.validate_judgment(p,judgment(p,sensitivity_ids=('invented',)))


def test_adjudication_binds_both_original_ratings_not_just_agreement(tmp_path):
    api=contracts();p=packet(tmp_path,check='reference_label')
    ratings=(judgment(p,status='defensible_alternative'),judgment(p,'second',status='defensible_alternative'))
    unbound=judgment(p,'third',status='defensible_alternative',stage='adjudication')
    with pytest.raises(ValueError,match='ratings'):endpoint(p,ratings,unbound,bind=False)
    bound=unbound.model_copy(update={'independent_rating_sha256':tuple(api.judgment_sha256(r) for r in ratings)})
    assert endpoint(p,ratings,bound)['alternative_aware_success'] is True
    changed=ratings[0].model_copy(update={'rationale':'A different independently retained rationale.'})
    with pytest.raises(ValueError,match='ratings'):endpoint(p,(changed,ratings[1]),bound,bind=False)


def test_blinded_context_rejects_answer_encoding_diagnostic_fields(tmp_path):
    p=packet(tmp_path);path=tmp_path/'diagnostics.json'
    path.write_text('{"final_reference": "secret answer", "p": 0.1}')
    evidence=p.evidence[0].model_copy(update={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    with pytest.raises(ValueError,match='blind'):contracts().judgment_context(p.model_copy(update={'evidence':(evidence,)}))


def test_numeric_pointer_cannot_use_negative_array_indices(tmp_path):
    p=packet(tmp_path);path=tmp_path/'diagnostics.json';path.write_text('{"values": [0.1, 0.2]}')
    evidence=p.evidence[0].model_copy(update={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    p=p.model_copy(update={'evidence':(evidence,)})
    assertion=contracts().NumericalAssertion(evidence_id='diagnostic',pointer='/values/-1',value=.2)
    with pytest.raises(ValueError,match='numerical'):contracts().validate_judgment(p,judgment(p,numerical_assertions=(assertion,)))


def test_companion_packet_and_judgment_seal_separately_and_reverify_artifacts(workspace):
    from devbench.analysis.scientific_review import prepare_claim_packet, load_claim_packet, record_claim_judgment
    tmp_path=workspace
    p=packet(tmp_path)
    receipt=prepare_claim_packet(tmp_path/'companion',p,allow_evidence_read=True)
    manifest=tmp_path/'companion/audit/manifest.json'
    verified=load_claim_packet(manifest,receipt['root_hash'],allow_evidence_read=True)
    assert verified==p
    second=record_claim_judgment(tmp_path/'rating',manifest,expected_root=receipt['root_hash'],
        judgment=judgment(p),allow_evidence_read=True,allow_review=True)
    assert second['root_hash']!=receipt['root_hash']
    with pytest.raises(ValueError,match='disjoint'):
        prepare_claim_packet(tmp_path/'companion/nested',p,allow_evidence_read=True,read_only_paths=(tmp_path/'companion',))
    (tmp_path/'diagnostics.json').write_text('{}')
    with pytest.raises(ValueError,match='changed'):load_claim_packet(manifest,receipt['root_hash'],allow_evidence_read=True)


def test_agent_claim_schema_cannot_set_coordinator_dimensions(tmp_path):
    from devbench.analysis.scientific_review_agent import claim_review_context, claim_review_schema
    p=packet(tmp_path,check='causality')
    context=claim_review_context(p)
    schema=claim_review_schema(context,reviewer_id='first')
    assert schema['additionalProperties'] is False
    assert not {'objective_checks','objective_validity','oversight','operational_feasibility','failure_taxonomy'} & schema['properties'].keys()
    assert schema['properties']['packet_sha256']=={'const':contracts().packet_sha256(p)}
    from jsonschema import Draft202012Validator
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(judgment(p).model_dump(mode='json'))


def test_adjudication_context_is_separate_and_retains_original_votes(tmp_path):
    from devbench.analysis.scientific_review_agent import claim_adjudication_context,claim_review_context
    p=packet(tmp_path,check='resolution');ratings=(judgment(p),judgment(p,'second',status='defensible_alternative'))
    frozen=policy()
    context=claim_adjudication_context(p,ratings,policy=frozen,expected_policy_sha256=contracts().policy_sha256(frozen))
    assert context['independent_ratings'][1]['scientific_status']=='defensible_alternative'
    assert context['objective_checks'][0]['check']=='resolution'
    assert 'independent_ratings' not in claim_review_context(p)


@pytest.mark.parametrize('check,text',[
    ('causality','Strong correlation proves direct regulation and promoter binding.'),
    ('replication','Each unique specimen label establishes an independent biological animal.'),
    ('statistics','A fitted p-value of 0.1 and q-value of 0.001 demonstrate significance.'),
    ('citation','A citation title establishes that this method has been independently validated.')])
def test_plausible_prose_cannot_override_negative_control_evidence(tmp_path,check,text):
    p=packet(tmp_path,check=check).model_copy(update={'claim_text':text})
    result=endpoint(p,[judgment(p),judgment(p,'second')],judgment(p,'third',stage='adjudication'))
    assert result['alternative_aware_success'] is False
    assert result['scientific_judgment']=='demonstrable_error'


def test_supported_endpoint_requires_adjudication_and_leaves_legacy_semantics_alone(tmp_path):
    from devbench.study.reasoning_review import alternative_aware_judgment
    p=packet(tmp_path);ratings=(judgment(p),judgment(p,'second'));frozen=policy()
    result=alternative_aware_judgment(p,ratings,policy=frozen,
        expected_policy_sha256=contracts().policy_sha256(frozen),primary_score=1.)
    assert result['alternative_aware_success'] is None
    assert 'reviewed_insight_success' not in result
    bound=judgment(p,'third',stage='adjudication',independent_rating_sha256=tuple(contracts().judgment_sha256(r) for r in ratings))
    result=alternative_aware_judgment(p,ratings,policy=frozen,
        expected_policy_sha256=contracts().policy_sha256(frozen),primary_score=1.,adjudication=bound)
    assert result['alternative_aware_success'] is True


def test_claim_review_load_requires_original_packet_and_immutable_receipt(workspace):
    from devbench.analysis.scientific_review import prepare_claim_packet,record_claim_judgment,load_claim_judgment
    p=packet(workspace);receipt=prepare_claim_packet(workspace/'packet',p,allow_evidence_read=True)
    manifest=workspace/'packet/audit/manifest.json';rating=judgment(p)
    review=record_claim_judgment(workspace/'review',manifest,expected_root=receipt['root_hash'],
        judgment=rating,allow_evidence_read=True,allow_review=True)
    kwargs=dict(packet_manifest=manifest,packet_root=receipt['root_hash'],allow_evidence_read=True)
    assert load_claim_judgment(workspace/'review/audit/manifest.json',review['root_hash'],**kwargs)==rating
    (workspace/'review/claim-judgment.json').write_text('{}')
    with pytest.raises(ValueError):load_claim_judgment(workspace/'review/audit/manifest.json',review['root_hash'],**kwargs)
