from types import SimpleNamespace

import pytest


def review(**updates):
    value={'attempt_id':'attempt-1','input_sha256':'a'*64,'answer_sha256':'b'*64,
        'reviewer_id':'fixture-reviewer','rating_stage':'independent','blinded_to_condition':True,'critical_fabrication':False,
        'criteria':{name:{'score':2,'justification':'Fixture adjudication with supporting evidence.',
                         'evidence_reference':'fixture:observed-result'} for name in
                    ('factual_accuracy','evidence_grounding','causal_restraint','uncertainty','alternative_explanations')}}
    value.update(updates);return value


def batch(*ratings):
    return {'record_format':'devbench.reasoning-review-batch/2',
        'reviewer_ids':['fixture-reviewer','second-reviewer'],
        'reviews':list(ratings) if ratings else [review(),review(reviewer_id='second-reviewer')]}


def inputs():
    record=SimpleNamespace(attempt_id='attempt-1',input_sha256='a'*64,answer_sha256='b'*64,
        accepted_final=True,integrity_status='verified',condition='enforced',scientific_success=True)
    summary={'run_rows':[{'attempt_id':'attempt-1','primary_task_success':1.,'condition':'enforced'}]}
    return summary,[record]


def test_correct_numbers_with_fabricated_reasoning_fail_joint_outcome():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    summary,records=inputs()
    good=apply_reasoning_reviews(summary,records,batch(),retained_root_verified=True)
    bad=apply_reasoning_reviews(summary,records,batch(review(critical_fabrication=True),review(reviewer_id='second-reviewer')),retained_root_verified=True)
    assert good['run_rows'][0]['reviewed_insight_success'] is True
    assert bad['run_rows'][0]['reviewed_insight_success'] is False
    assert summary['run_rows'][0]['primary_task_success']==1.


def test_missing_review_is_unknown_not_a_reasoning_pass():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    result=apply_reasoning_reviews(*inputs(),[],retained_root_verified=True)
    assert result['run_rows'][0]['reviewed_insight_success'] is None
    assert result['reasoning_evaluation']['reviewed_count']==0


@pytest.mark.parametrize('updates',[{'answer_sha256':'c'*64},{'input_sha256':'c'*64},{'attempt_id':'foreign'}])
def test_reviews_cannot_transfer_between_answers_or_tasks(updates):
    from devbench.study.reasoning_review import apply_reasoning_reviews
    with pytest.raises(ValueError):apply_reasoning_reviews(*inputs(),[review(**updates)],retained_root_verified=True)


def test_duplicate_reviewer_is_rejected_but_unblinding_does_not_remove_primary_ratings():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    with pytest.raises(ValueError):apply_reasoning_reviews(*inputs(),[review(),review()],retained_root_verified=True)
    with pytest.raises(PermissionError):apply_reasoning_reviews(*inputs(),[review()],retained_root_verified=False)
    result=apply_reasoning_reviews(*inputs(),batch(review(blinded_to_condition=False),review(reviewer_id='second-reviewer')),retained_root_verified=True)
    assert result['run_rows'][0]['reviewed_insight_success'] is True
    assert result['run_rows'][0]['blinding_compromised'] is True
    assert result['run_rows'][0]['blinded_only_reasoning_score'] is None


def test_human_review_condition_rates_require_complete_joint_outcomes():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    summary,records=inputs()
    context={'source_kind':'synthetic','actual_model':'fixture-model','model_identity_sha256':'c'*64,
             'model_identity_verified':True,'actor_type':'model_participant','condition':'enforced'}
    for key,value in context.items():setattr(records[0],key,value)
    records[0].cohort_id='cohort';records[0].case_id='case';records[0].template_id='T'
    summary['group_summaries']=[context]
    completed=apply_reasoning_reviews(summary,records,batch(),retained_root_verified=True)
    assert completed['group_summaries'][0]['reviewed_insight_success_rate']==1.
    absent=apply_reasoning_reviews(summary,records,[],retained_root_verified=True)
    assert absent['group_summaries'][0]['reviewed_insight_success_rate'] is None


def test_grader_record_projection_and_blind_packet_bind_prose():
    from devbench.study.biology_answers import BiologyAnswer
    from devbench.study.biology_scoring import BiologyReference, grade_biology_answer
    from devbench.study.biology_records import biology_grade_record_fields
    from devbench.study.reasoning_review import reasoning_review_template
    from devbench.study.contracts import content_sha256
    reference=BiologyReference(task_id='task',input_sha256='a'*64,
        gene_universe_sha256=content_sha256(('gene',)),gene_ids=('gene',),targets=())
    answer=BiologyAnswer(task_id='task',input_sha256='a'*64,gene_universe_sha256=reference.gene_universe_sha256,
        target_results=(),narrative_for_human_review='The supplied evidence does not establish causation.')
    grade=grade_biology_answer(answer,reference=reference,allow_result_analysis=True)
    fields=biology_grade_record_fields(grade,input_sha256='a'*64,expected_task_identity='task',allow_result_analysis=True)
    packet=reasoning_review_template(answer.model_dump(mode='json'),'attempt-1')
    assert fields['answer_sha256']==packet['review']['answer_sha256']
    changed=answer.model_copy(update={'narrative_for_human_review':'These numbers prove a cure.'})
    assert grade_biology_answer(changed,reference=reference,allow_result_analysis=True).answer_sha256!=grade.answer_sha256
    assert packet['review']['reviewer_id'] is None


def test_independent_ratings_are_retained_and_cutoff_sensitivity_is_reported():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    first=review();second=review(reviewer_id='second-reviewer')
    for value in (first,second):
        value['criteria']['uncertainty']['score']=1
        value['criteria']['alternative_explanations']['score']=1
    result=apply_reasoning_reviews(*inputs(),batch(first,second),retained_root_verified=True)
    row=result['run_rows'][0]
    assert row['reasoning_score']==.8 and row['reasoning_pass'] is True
    assert row['reasoning_pass_at_7'] is True and row['reasoning_pass_at_9'] is False
    assert row['one_point_sensitive'] is True
    assert len(result['_reasoning_review_evidence']['normalized_ratings'])==2


def test_missing_rater_has_explicit_bounds_and_no_invented_primary_score():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    result=apply_reasoning_reviews(*inputs(),batch(review()),retained_root_verified=True)
    row=result['run_rows'][0]
    assert row['reasoning_score'] is None
    assert row['reasoning_score_lower_bound']==.5 and row['reasoning_score_upper_bound']==1.


def test_kappa_matches_established_implementation_and_handles_degenerate_marginals():
    import numpy as np
    from statsmodels.stats.inter_rater import cohens_kappa
    from devbench.study.reasoning_review import rating_agreement
    pairs=[(0,0),(1,1),(2,1),(2,2),(1,2),(0,1)]
    table=np.zeros((3,3),dtype=int)
    for a,b in pairs:table[a,b]+=1
    assert rating_agreement(pairs)['kappa']==pytest.approx(cohens_kappa(table).kappa)
    assert rating_agreement([(2,2),(2,2)])['kappa'] is None


def test_disagreement_and_adjudication_do_not_replace_initial_ratings():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    summary,records=inputs()
    other=SimpleNamespace(**{**records[0].__dict__,'attempt_id':'attempt-2'})
    records.append(other);summary['run_rows'].append(dict(summary['run_rows'][0],attempt_id='attempt-2'))
    low=review();low['criteria']['factual_accuracy']['score']=0
    ratings=[low,review(reviewer_id='second-reviewer'),review(attempt_id='attempt-2'),
             review(attempt_id='attempt-2',reviewer_id='second-reviewer'),
             review(rating_stage='adjudication')]
    result=apply_reasoning_reviews(summary,records,batch(*ratings),retained_root_verified=True)
    assert result['reasoning_evaluation']['raw_rating_count']==5
    assert result['reasoning_evaluation']['paired_rating_answer_count']==2
    metric=next(x for x in result['reasoning_evaluation']['rating_agreement'] if x['criterion']=='factual_accuracy')
    assert metric['exact_agreement']==.5
    assert len(result['_reasoning_review_evidence']['normalized_ratings'])==5


def test_raw_rating_audit_is_private_fresh_and_hash_bound():
    from pathlib import Path
    import hashlib,json,tempfile
    from devbench.study.reasoning_review import apply_reasoning_reviews,write_reasoning_review_audit
    root=Path(__file__).resolve().parents[1]/'tmp'
    root.mkdir(exist_ok=True)
    result=apply_reasoning_reviews(*inputs(),batch(),retained_root_verified=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        target=Path(directory)/'private-review-audit'
        receipt=write_reasoning_review_audit(result,target)
        raw=target/'raw-ratings.json'
        assert hashlib.sha256(raw.read_bytes()).hexdigest()==receipt['raw_ratings_sha256']
        assert len(json.loads(raw.read_text())['normalized_ratings'])==2
        assert raw.stat().st_mode & 0o777 == 0o600
        with pytest.raises(FileExistsError):write_reasoning_review_audit(result,target)


def test_blinding_changes_sensitivity_not_primary_availability_across_arms():
    from devbench.study.reasoning_review import apply_reasoning_reviews
    from devbench.study.biology_dashboard import _public_analysis
    import json
    summary,records=inputs();base=records[0]
    context={'source_kind':'synthetic','actual_model':'fixture-model','model_identity_sha256':'c'*64,
             'model_identity_verified':True,'actor_type':'model_participant'}
    records=[];groups=[];rows=[];ratings=[]
    for condition in ('baseline','enforced'):
        record=SimpleNamespace(**{**base.__dict__,**context,'attempt_id':condition,'condition':condition,
            'cohort_id':'cohort','case_id':'case','template_id':'T'})
        records.append(record);groups.append({**context,'condition':condition})
        rows.append({'attempt_id':condition,'condition':condition,'primary_task_success':1.})
        for reviewer in ('fixture-reviewer','second-reviewer'):
            ratings.append(review(attempt_id=condition,reviewer_id=reviewer,blinded_to_condition=condition=='baseline'))
    result=apply_reasoning_reviews({'group_summaries':groups,'run_rows':rows},records,batch(*ratings),retained_root_verified=True)
    assert [g['reasoning_review_coverage'] for g in result['group_summaries']]==[1.,1.]
    assert [g['reasoning_score_mean'] for g in result['group_summaries']]==[1.,1.]
    assert [g['blinded_only_review_coverage'] for g in result['group_summaries']]==[1.,0.]
    public=_public_analysis(result)
    assert len(public['reasoning_evaluation']['rating_agreement'])==6
    assert 'normalized_ratings' not in json.dumps(public)
    assert 'Fixture adjudication' not in json.dumps(public)
