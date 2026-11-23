"""Coordinator-supplied human reasoning adjudication; never an LLM judge.

Human authorship and blinding are declarations, not authenticated signatures.
Reviews supplement immutable numeric records and must bind the entire answer,
including prose. Schema validity does not establish an adjudication's truth.
"""
from typing import Annotated, Literal
import json
from collections import Counter, defaultdict

from pydantic import Field, model_validator

from .contracts import FrozenContract, Identifier, Sha256, content_sha256

CRITERIA=('factual_accuracy','evidence_grounding','causal_restraint','uncertainty','alternative_explanations')


class CriterionReview(FrozenContract):
    score: Annotated[int,Field(ge=0,le=2)]
    justification: Annotated[str,Field(min_length=10,max_length=3000)]
    evidence_reference: Annotated[str,Field(min_length=1,max_length=1000)]


class ReasoningReview(FrozenContract):
    record_format: Literal['devbench.human-reasoning-review/1']='devbench.human-reasoning-review/1'
    attempt_id: Identifier
    input_sha256: Sha256
    answer_sha256: Sha256
    reviewer_id: Identifier
    rating_stage: Literal['independent','adjudication','unspecified']='unspecified'
    blinded_to_condition: bool
    guessed_condition: Literal['baseline','optional','enforced','unknown'] | None = None
    critical_fabrication: bool
    criteria: dict[str,CriterionReview]

    @model_validator(mode='after')
    def complete_rubric(self):
        if set(self.criteria)!=set(CRITERIA):raise ValueError('Every prespecified reasoning criterion is required')
        return self


def rating_agreement(pairs):
    """Descriptive, unweighted Cohen kappa; no independent-answer CI assumption."""
    n=len(pairs)
    result={'paired_answer_count':n,'exact_agreement':None,'kappa':None,'confidence_interval':None,
            'reason':'fewer_than_two_paired_answers' if n<2 else None}
    if not n:return result
    left=Counter(a for a,_ in pairs);right=Counter(b for _,b in pairs)
    observed=sum(a==b for a,b in pairs)/n
    expected=sum(left[k]*right[k] for k in left.keys()|right.keys())/n**2
    result['exact_agreement']=observed
    if n>=2:
        if expected==1:result['reason']='degenerate_marginals'
        else:result['kappa']=(observed-expected)/(1-expected)
    return result


def alternative_aware_judgment(packet, ratings, *, policy, expected_policy_sha256,
                               primary_score, adjudication=None):
    """Additive claim endpoint; legacy reviewed_insight_success is unchanged.

    Coordinator checks and a separately frozen policy bind the endpoint. Review
    records are not authenticated human evidence or scientific qualification.
    """
    from devbench.analysis.scientific_judgment import evaluate_judgments
    return evaluate_judgments(packet,ratings,policy=policy,
        expected_policy_sha256=expected_policy_sha256,primary_score=primary_score,adjudication=adjudication)


def _pass(ratings, threshold=8):
    return (bool(ratings) and not any(r.critical_fabrication for r in ratings)
        and all(c.score>0 for r in ratings for c in r.criteria.values())
        and sum(c.score for r in ratings for c in r.criteria.values())/len(ratings)>=threshold)


def _one_point_sensitive(ratings):
    original=_pass(ratings)
    for index,rating in enumerate(ratings):
        for name,criterion in rating.criteria.items():
            for change in (-1,1):
                if 0<=criterion.score+change<=2:
                    criteria={**rating.criteria,name:criterion.model_copy(update={'score':criterion.score+change})}
                    changed=[*ratings];changed[index]=rating.model_copy(update={'criteria':criteria})
                    if _pass(changed)!=original:return True
    return False


def _answer_rating(ratings, panel):
    independent={r.reviewer_id:r for r in ratings if r.rating_stage=='independent' and r.reviewer_id in panel}
    chosen=[independent[name] for name in panel if name in independent]
    complete=len(chosen)==2 and len(panel)==2
    points=sum(c.score for r in chosen for c in r.criteria.values())
    score=points/20 if complete else None
    blind=complete and all(r.blinded_to_condition for r in chosen)
    result={'reasoning_review_status':'independent_panel_complete' if complete else 'independent_panel_incomplete',
        'independent_rating_count':len(chosen),'reasoning_score':score,
        'reasoning_score_lower_bound':points/20,'reasoning_score_upper_bound':(points+10*(2-len(chosen)))/20,
        'reasoning_pass':_pass(chosen) if complete else None,
        'reasoning_pass_at_7':_pass(chosen,7) if complete else None,
        'reasoning_pass_at_9':_pass(chosen,9) if complete else None,
        'one_point_sensitive':_one_point_sensitive(chosen) if complete else None,
        'blinding_compromised':any(not r.blinded_to_condition for r in chosen),
        'blinded_only_reasoning_score':score if blind else None,
        'critical_fabrication_reported':any(r.critical_fabrication for r in chosen),
        'reasoning_review_sha256':content_sha256([r.model_dump(mode='json') for r in ratings]) if ratings else None}
    return result


def apply_reasoning_reviews(summary, records, reviews, *, retained_root_verified=False):
    if retained_root_verified is not True:
        raise PermissionError('Reasoning adjudication requires the independently retained execution root')
    supplied=json.loads(json.dumps(reviews))
    panel=()
    if isinstance(reviews,dict):
        if (set(reviews)-{'record_format','reviewer_ids','reviews','protocol'} or
                reviews.get('record_format')!='devbench.reasoning-review-batch/2'):
            raise ValueError('Unknown review batch protocol')
        panel=reviews.get('reviewer_ids')
        if not isinstance(panel,list) or len(panel)!=2 or len(set(panel))!=2:
            raise ValueError('Prespecify exactly two distinct independent reviewers')
        from pydantic import TypeAdapter
        for name in panel:TypeAdapter(Identifier).validate_python(name)
        panel=tuple(panel);reviews=reviews.get('reviews')
    if not isinstance(reviews,list) or len(reviews)>1000:raise ValueError('Expected at most 1000 ratings')
    exposed={row['attempt_id'] for row in summary.get('run_rows',[])
             if row.get('binding_status','verified_schedule_binding')=='verified_schedule_binding'}
    lookup={record.attempt_id:record for record in records if record.attempt_id in exposed}
    by_answer=defaultdict(list);seen=set();normalized=[]
    for value in reviews:
        review=ReasoningReview.model_validate_json(json.dumps(value))
        key=(review.attempt_id,review.reviewer_id,review.rating_stage)
        if key in seen:raise ValueError('Duplicate rating by the same reviewer in the same stage')
        seen.add(key)
        if panel and review.rating_stage=='independent' and review.reviewer_id not in panel:
            raise ValueError('Independent reviewer is outside the prespecified panel')
        record=lookup.get(review.attempt_id)
        if (record is None or not record.accepted_final or record.integrity_status!='verified'
                or record.answer_sha256 is None or record.answer_sha256!=review.answer_sha256
                or record.input_sha256!=review.input_sha256):
            raise ValueError('Review does not bind a verified retained answer and input')
        by_answer[review.attempt_id].append(review);normalized.append(review.model_dump(mode='json'))
    rated={key:_answer_rating(values,panel) for key,values in by_answer.items()}
    output=dict(summary);rows=[]
    for row in summary.get('run_rows',[]):
        evaluation=rated.get(row['attempt_id'],_answer_rating([],panel))
        numeric=row.get('primary_task_success')
        joint=False if numeric==0 else evaluation['reasoning_pass'] if numeric==1 else None
        score=(0. if numeric==0 else (0. if evaluation['critical_fabrication_reported'] else evaluation['reasoning_score'])
               if numeric==1 else None)
        rows.append({**row,**evaluation,'reviewed_insight_success':joint,'reviewed_insight_score':score})
    output['run_rows']=rows
    from .biology_metrics import _hierarchical, _key, _labels
    row_index={row['attempt_id']:row for row in rows}
    groups=[]
    for group in summary.get('group_summaries',[]):
        selected=[record for record in lookup.values() if record.condition==group['condition']
                  and all(group.get(k)==v for k,v in _labels(_key(record)).items())]
        reviewed=[record for record in selected if rated.get(record.attempt_id,{}).get('reasoning_pass') is not None]
        blinded=[record for record in selected if row_index[record.attempt_id]['blinded_only_reasoning_score'] is not None]
        complete=bool(selected) and all(row_index[r.attempt_id]['reviewed_insight_success'] is not None for r in selected)
        all_scored=bool(selected) and all(row_index[r.attempt_id]['reasoning_score'] is not None for r in selected)
        all_joint_scored=bool(selected) and all(row_index[r.attempt_id]['reviewed_insight_score'] is not None for r in selected)
        group_ratings=[value for record in selected for value in by_answer[record.attempt_id]
                       if value.rating_stage=='independent' and value.reviewer_id in panel]
        guesses=[value for value in group_ratings if value.guessed_condition not in (None,'unknown')]
        mean=lambda key:_hierarchical(selected,lambda r:row_index[r.attempt_id][key]) if selected else None
        groups.append({**group,'reasoning_reviewed_count':len(reviewed),
            'reasoning_review_coverage':len(reviewed)/len(selected) if selected else None,
            'reasoning_score_mean':mean('reasoning_score') if all_scored else None,
            'reasoning_score_lower_bound':mean('reasoning_score_lower_bound'),
            'reasoning_score_upper_bound':mean('reasoning_score_upper_bound'),
            'reviewed_insight_score_mean':mean('reviewed_insight_score') if all_joint_scored else None,
            'blinded_only_review_coverage':sum(row_index[r.attempt_id]['blinded_only_reasoning_score'] is not None for r in selected)/len(selected) if selected else None,
            'blinded_only_reasoning_score_mean':_hierarchical(blinded,lambda r:row_index[r.attempt_id]['blinded_only_reasoning_score']) if blinded else None,
            'unblinded_rating_fraction':sum(not value.blinded_to_condition for value in group_ratings)/len(group_ratings) if group_ratings else None,
            'condition_guess_count':len(guesses),'condition_guess_accuracy':sum(value.guessed_condition==group['condition'] for value in guesses)/len(guesses) if guesses else None,
            'one_point_sensitive_fraction':_hierarchical(reviewed,lambda r:float(row_index[r.attempt_id]['one_point_sensitive'])) if reviewed else None,
            'reasoning_pass_rate_at_7':mean('reasoning_pass_at_7') if all_scored else None,
            'reasoning_pass_rate_at_8':mean('reasoning_pass') if all_scored else None,
            'reasoning_pass_rate_at_9':mean('reasoning_pass_at_9') if all_scored else None,
            'reviewed_insight_success_rate':_hierarchical(selected,lambda r:float(row_index[r.attempt_id]['reviewed_insight_success'])) if complete else None,
            'reasoning_comparison_status':'descriptive_unblinding_detected' if any(not value.blinded_to_condition for value in group_ratings) else 'review_measurement_unqualified'})
    output['group_summaries']=groups
    paired=[]
    if len(panel)==2:
        for attempt,values in by_answer.items():
            initial={value.reviewer_id:value for value in values if value.rating_stage=='independent'}
            if all(name in initial for name in panel):paired.append((attempt,initial[panel[0]],initial[panel[1]]))
    agreement=[{'criterion':name,**rating_agreement([(a.criteria[name].score,b.criteria[name].score) for _,a,b in paired])} for name in CRITERIA]
    agreement.append({'criterion':'secondary_pass_at_8',**rating_agreement([(_pass([a]),_pass([b])) for _,a,b in paired])})
    output['_reasoning_review_evidence']={'record_format':'devbench.raw-reasoning-evidence/2',
        'supplied_batch':supplied,'normalized_ratings':normalized,'prespecified_reviewer_ids':panel}
    output['reasoning_evaluation']={'status':'ratings_attached' if rated else 'human_review_pending',
        'protocol_version':2,'primary_human_endpoint':'mean_continuous_score','binary_endpoint_role':'secondary_sensitivity_7_8_9',
        'raw_rating_count':len(normalized),'reviewed_count':len(rated),
        'primary_blinded_review_count':sum(x['blinded_only_reasoning_score'] is not None for x in rated.values()),
        'available_answer_count':sum(r.accepted_final for r in records),
        'holistic_insight_success_rate':None,
        'rating_agreement':agreement,'paired_rating_answer_count':len(paired),
        'declared_cohort_count':len({getattr(lookup[key],'cohort_id',None) for key,_,_ in paired}-{None}) or None,
        'agreement_uncertainty':'not_estimated; answers sharing a cohort are not independent',
        'raw_ratings_sha256':content_sha256(output['_reasoning_review_evidence']),
        'scope':'all independent panel ratings retained regardless of blinding; blinding diagnostics and blind-only sensitivity remain separate',
        'rubric_calibrated':False,'human_authorship_authenticated':False}
    return output


def write_reasoning_review_audit(summary, directory):
    """Keep raw ratings in a separate private directory, never the public export."""
    from pathlib import Path
    from .contracts import canonical_json_bytes
    from .dashboard import _write_fresh
    from .permissions import require_protected_storage
    evidence=summary.get('_reasoning_review_evidence')
    expected=summary.get('reasoning_evaluation',{}).get('raw_ratings_sha256')
    if not isinstance(evidence,dict) or content_sha256(evidence)!=expected:
        raise ValueError('Raw rating evidence does not match the recorded hash')
    payload=canonical_json_bytes(evidence)
    if len(payload)>12_000_000:raise ValueError('Raw rating audit exceeds its bound')
    destination=require_protected_storage(Path(directory))
    receipt={'record_format':'devbench.reasoning-rating-receipt/2','raw_ratings_sha256':expected,
             'rating_count':len(evidence['normalized_ratings']),'scope':'local raw-rating retention; not authenticated human identity'}
    _write_fresh(destination,{'raw-ratings.json':payload,'receipt.json':canonical_json_bytes(receipt)})
    return {'directory':str(destination),**receipt}


def reasoning_review_template(answer, attempt_id):
    from .biology_answers import BiologyAnswer
    from pydantic import TypeAdapter
    TypeAdapter(Identifier).validate_python(attempt_id)
    parsed=BiologyAnswer.model_validate_json(json.dumps(answer))
    canonical=parsed.model_dump(mode='json')
    display=parsed.model_dump(mode='json')
    for target in display['target_results']:
        target.pop('tool_evidence_ids',None)
        for check in target.get('support_checks',[]):check.pop('evidence_ids',None)
    return {'record_format':'devbench.reasoning-review-packet/1',
        'answer_to_review':display,
        'blinding_scope':'model/arm metadata and tool receipt identifiers omitted; prose may still reveal condition',
        'rubric':{'0':'Incorrect or unsupported','1':'Partly adequate; material omission','2':'Adequate and evidence-grounded',
                  'primary_policy':'Two prespecified independent reviewers; continuous mean score is primary; retain disagreements.',
                  'passing_policy':'Secondary thresholds 7, 8 and 9/10, no zero criterion, no critical fabrication; uncalibrated.',
                  'rating_procedure':'Rate independently before seeing other ratings or arm identity. Preserve initial ratings; any adjudication is a separate record.'},
        'review':{'record_format':'devbench.human-reasoning-review/1','attempt_id':attempt_id,
            'input_sha256':parsed.input_sha256,'answer_sha256':content_sha256(canonical),
            'reviewer_id':None,'rating_stage':'independent','blinded_to_condition':None,'guessed_condition':None,'critical_fabrication':None,
            'criteria':{name:{'score':None,'justification':'','evidence_reference':''} for name in CRITERIA}}}
