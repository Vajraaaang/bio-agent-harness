"""Claim-level evidence and coordinator-owned judgment policy.

Artifact identity and synthetic controls do not qualify biological judgment.
These companion records grant no execution, promotion or clinical authority.
The coordinator supplies checks; evaluated reviewers cannot change their taxonomy.
"""
from datetime import date
import hashlib
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from devbench.study.contracts import FrozenContract, Identifier, Sha256, content_sha256
from devbench.study.evidence import _read, decode_json
from .episode_proposals import _absolute

Text = Annotated[str, Field(min_length=1, max_length=4000)]
Texts = Annotated[tuple[Text, ...], Field(max_length=64)]
Refs = Annotated[tuple[Identifier, ...], Field(max_length=64)]
ScientificStatus = Literal['supported', 'defensible_alternative', 'demonstrable_error',
                           'insufficient_evidence', 'not_assessed']
CheckName = Literal['input_integrity', 'statistics', 'contrast', 'causality', 'replication',
                    'citation', 'reference_label', 'resolution', 'execution']
HARD_FAILURES = frozenset({'input_integrity', 'statistics', 'contrast', 'causality', 'replication', 'citation'})
DISPUTABLE_FAILURES = frozenset({'reference_label', 'resolution'})


class SourceExcerpt(FrozenContract):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    version: Annotated[str, Field(min_length=1, max_length=200)]
    access: Literal['documentation', 'full_text', 'abstract']
    accessed_on: Annotated[str,Field(min_length=10,max_length=10)]
    actual_text: Annotated[str, Field(min_length=1, max_length=32000)]
    applicability: Text
    limitations: Text

    @model_validator(mode='after')
    def source_identity(self):
        url=urlsplit(self.url)
        if url.scheme!='https' or not url.hostname or url.username or url.password:
            raise ValueError('Use credential-free HTTPS source references')
        date.fromisoformat(self.accessed_on)
        if not self.actual_text.strip():raise ValueError('Actual source text is required')
        return self


class ArtifactEvidence(FrozenContract):
    evidence_id: Identifier
    path: Annotated[str, Field(min_length=1, max_length=2000)]
    sha256: Sha256
    kind: Literal['diagnostic', 'source', 'plot']
    source: SourceExcerpt | None = None
    plot_verified: bool = False

    @model_validator(mode='after')
    def artifact_kind(self):
        _absolute(self.path)
        if (self.kind=='source') != (self.source is not None):
            raise ValueError('Sources require retrieved text and applicability; other artifacts cannot claim retrieval')
        if self.plot_verified and self.kind!='plot':raise ValueError('Only plot evidence can declare plot verification')
        return self


class ObjectiveCheck(FrozenContract):
    check: CheckName
    status: Literal['verified', 'failed', 'not_assessed']
    evidence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=64)]
    reason: Text


class SensitivityCheck(FrozenContract):
    check_id: Identifier
    kind: Literal['preprocessing', 'resolution', 'reference', 'prior', 'counterfactual']
    status: Literal['assessed', 'not_assessed']
    conclusion_changed: bool | None
    evidence_ids: Refs
    reason: Text

    @model_validator(mode='after')
    def assessed_evidence(self):
        if self.status=='assessed' and (self.conclusion_changed is None or not self.evidence_ids):
            raise ValueError('Assessed sensitivity requires actual evidence and observed conclusion change')
        if self.status=='not_assessed' and self.conclusion_changed is not None:
            raise ValueError('Unassessed sensitivity cannot invent an outcome')
        return self


class ClaimPacket(FrozenContract):
    record_format: Literal['devbench.scientific-claim-packet/1']='devbench.scientific-claim-packet/1'
    packet_id: Identifier
    input_root: Sha256
    claim_id: Identifier
    claim_text: Text
    objective: Text
    assay: Text
    measurement: Text
    assay_semantics: Literal['known', 'unknown']
    units_design: Text
    intent: Literal['retain', 'diagnostic_flagging', 'exclude', 'infer']
    assumptions: Texts
    observations: Texts
    declarations: Texts
    inferences: Texts
    unknowns: Texts
    evidence: Annotated[tuple[ArtifactEvidence, ...], Field(min_length=1, max_length=64)]
    objective_checks: Annotated[tuple[ObjectiveCheck, ...], Field(max_length=64)]=()
    sensitivity_checks: Annotated[tuple[SensitivityCheck, ...], Field(max_length=64)]=()
    execution: Literal['completed', 'failed', 'timed_out', 'not_run']
    objective_validity: Literal['verified', 'failed', 'not_assessed']
    operational_feasibility: Literal['available', 'unavailable', 'resource_blocked']
    oversight: Literal['pending', 'accepted', 'qualified_acceptance', 'rejected']='pending'

    @model_validator(mode='after')
    def references(self):
        ids={item.evidence_id for item in self.evidence}
        if len(ids)!=len(self.evidence):raise ValueError('Duplicate evidence reference')
        checks=[item.check for item in self.objective_checks]
        sensitivity=[item.check_id for item in self.sensitivity_checks]
        if len(checks)!=len(set(checks)) or len(sensitivity)!=len(set(sensitivity)):
            raise ValueError('Duplicate objective/sensitivity checks')
        for item in (*self.objective_checks,*self.sensitivity_checks):
            if not set(item.evidence_ids)<=ids:raise ValueError('Unknown evidence reference in coordinator checks')
        return self


class NumericalAssertion(FrozenContract):
    evidence_id: Identifier
    pointer: Annotated[str, Field(min_length=1, max_length=1000, pattern=r'^/')]
    value: Annotated[float, Field(allow_inf_nan=False)]


class ClaimJudgment(FrozenContract):
    record_format: Literal['devbench.scientific-judgment/1']='devbench.scientific-judgment/1'
    packet_sha256: Sha256
    claim_id: Identifier
    reviewer_id: Identifier
    stage: Literal['independent', 'adjudication']
    blinded: bool = True
    scientific_status: ScientificStatus
    evidence_ids: Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=64)]
    rationale: Text
    evidence_quality: Literal['verified_artifact', 'source_excerpt', 'limited']
    action_consequences: Text
    competing_explanations: Texts
    missing_evidence: Text | None
    sensitivity_ids: Refs=()
    numerical_assertions: Annotated[tuple[NumericalAssertion, ...], Field(max_length=128)]=()
    confidence: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None=None
    confidence_calibration: Literal['uncalibrated']='uncalibrated'
    independent_rating_sha256: Annotated[tuple[Sha256, ...], Field(max_length=2)]=()

    @model_validator(mode='after')
    def missing_information(self):
        if self.scientific_status in {'insufficient_evidence','not_assessed'} and self.missing_evidence is None:
            raise ValueError('Unavailable judgment requires a concrete missing evidence request')
        if self.scientific_status=='defensible_alternative' and not self.competing_explanations:
            raise ValueError('A defensible alternative requires an explanation')
        if len(set(self.evidence_ids))!=len(self.evidence_ids):raise ValueError('Duplicate judgment evidence reference')
        if self.stage=='independent' and self.independent_rating_sha256:
            raise ValueError('Independent initial ratings cannot refer to prior ratings')
        return self


class EvaluationPolicy(FrozenContract):
    record_format: Literal['devbench.scientific-evaluation-policy/1']='devbench.scientific-evaluation-policy/1'
    policy_id: Identifier
    version: Identifier
    owner: Literal['coordinator']='coordinator'
    frozen_before_answers: Literal[True]=True
    failure_taxonomy: Literal['devbench.scientific-failure-taxonomy/1']='devbench.scientific-failure-taxonomy/1'
    adjudication_policy: Literal['two_blinded_ratings_and_evidence_bound_adjudication/1']='two_blinded_ratings_and_evidence_bound_adjudication/1'
    independent_reviewer_ids: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=2)]
    adjudicator_id: Identifier
    disputable_failures: tuple[Literal['reference_label','resolution'], ...]=('reference_label','resolution')

    @model_validator(mode='after')
    def panel(self):
        if len(set(self.independent_reviewer_ids))!=2 or self.adjudicator_id in self.independent_reviewer_ids:
            raise ValueError('Require two independent reviewer identifiers and a separate adjudicator')
        if len(set(self.disputable_failures))!=len(self.disputable_failures):raise ValueError('Duplicate policy codes')
        return self


def packet_sha256(packet):
    return content_sha256(packet.model_dump(mode='json'))


def policy_sha256(policy):
    return content_sha256(policy.model_dump(mode='json'))


def judgment_sha256(judgment):
    return content_sha256(judgment.model_dump(mode='json'))


def _blinded_diagnostics(value):
    """Reject obvious protected metadata; this is not a text-semantics firewall."""
    forbidden={'final_reference','final_references','model_identity','model_id',
        'prior_verdict','prior_verdicts','golden','private_golden','grader','answer_key'}
    if isinstance(value,dict):
        if any(str(key).lower() in forbidden for key in value):
            raise ValueError('Protected fields cannot enter a blinded diagnostic context')
        for item in value.values():_blinded_diagnostics(item)
    elif isinstance(value,list):
        for item in value:_blinded_diagnostics(item)


def verified_artifacts(packet):
    """Resolve exact bytes locally; no citation title or URL counts as retrieval."""
    packet=ClaimPacket.model_validate_json(packet.model_dump_json())
    result={}
    for evidence in packet.evidence:
        content=_read(_absolute(evidence.path),128_000)
        if hashlib.sha256(content).hexdigest()!=evidence.sha256:
            raise ValueError('Evidence artifact changed: '+evidence.evidence_id)
        if evidence.kind=='diagnostic':
            parsed=decode_json(content)
            # decode_json rejects constants; nested huge numeric exponents also
            # must fail finite JSON instead of silently becoming infinity.
            content_sha256(parsed)
            result[evidence.evidence_id]=parsed
        elif evidence.kind=='source':
            if evidence.source.actual_text not in content.decode('utf-8'):
                raise ValueError('Retrieved source text is absent from the verified artifact')
            result[evidence.evidence_id]=evidence.source.actual_text
        else:result[evidence.evidence_id]=None
    return result


def judgment_context(packet):
    """Allowlist initial reviewer evidence; source text can still carry style cues."""
    artifacts=verified_artifacts(packet)
    for item in packet.evidence:
        if item.kind=='diagnostic':_blinded_diagnostics(artifacts[item.evidence_id])
    fields=('record_format','packet_id','claim_id','claim_text','objective','assay','measurement',
        'assay_semantics','units_design','intent','assumptions','observations','declarations','inferences','unknowns')
    values=packet.model_dump(mode='json')
    context={key:values[key] for key in fields}
    context['packet_sha256']=packet_sha256(packet)
    context['diagnostics']=[{'evidence_id':e.evidence_id,'sha256':e.sha256,'content':artifacts[e.evidence_id]}
        for e in packet.evidence if e.kind=='diagnostic']
    context['sources']=[{'evidence_id':e.evidence_id,'sha256':e.sha256,**e.source.model_dump(mode='json')}
        for e in packet.evidence if e.kind=='source']
    context['plots']=[{'evidence_id':e.evidence_id,'sha256':e.sha256,'verified':e.plot_verified,
        'visual_content_supplied':False}
        for e in packet.evidence if e.kind=='plot']
    context['sensitivity_checks']=values['sensitivity_checks']
    context['execution']=packet.execution
    context['limits']=['Exact local artifact identity is verified; scientific correctness and source entailment are not.',
        'No final references, competing model identities or prior review verdicts are supplied.',
        'A missing plot or sensitivity check remains absent. Confidence is uncalibrated.',
        'Plot references verify byte identity only; this JSON context does not display plot pixels.',
        'Numerical assertions must cite exact diagnostic JSON pointers; free prose is not numerically verified.']
    return context


def validate_judgment(packet, judgment):
    artifacts=verified_artifacts(packet)
    judgment=ClaimJudgment.model_validate_json(judgment.model_dump_json())
    if judgment.packet_sha256!=packet_sha256(packet) or judgment.claim_id!=packet.claim_id:
        raise ValueError('Judgment does not bind the immutable claim packet')
    if not set(judgment.evidence_ids)<=artifacts.keys():raise ValueError('Unknown judgment evidence reference')
    if judgment.evidence_quality=='source_excerpt' and not any(
            item.kind=='source' and item.evidence_id in judgment.evidence_ids for item in packet.evidence):
        raise ValueError('Source excerpt quality requires a cited verified source artifact')
    if not set(judgment.sensitivity_ids)<={item.check_id for item in packet.sensitivity_checks}:
        raise ValueError('Unknown sensitivity reference')
    for assertion in judgment.numerical_assertions:
        if assertion.evidence_id not in judgment.evidence_ids:
            raise ValueError('Numerical assertion must cite a judgment evidence reference')
        try:
            value=artifacts[assertion.evidence_id]
            for part in assertion.pointer[1:].split('/'):
                key=part.replace('~1','/').replace('~0','~')
                if re.search(r'~(?![01])',part):raise ValueError()
                if isinstance(value,list):
                    if re.fullmatch(r'0|[1-9][0-9]*',key) is None:raise ValueError()
                    value=value[int(key)]
                else:value=value[key]
            if type(value) not in {int,float} or value!=assertion.value:raise ValueError()
        except (KeyError,IndexError,TypeError,ValueError) as error:
            raise ValueError('Unsupported numerical assertion in verified diagnostic evidence') from error
    failed={check.check for check in packet.objective_checks if check.status=='failed'}
    hard=bool(failed&HARD_FAILURES) or packet.objective_validity=='failed'
    scientific='demonstrable_error' if hard else judgment.scientific_status
    if not hard and packet.assay_semantics=='unknown' and packet.intent in {'exclude','infer'}:
        scientific='insufficient_evidence'
    return {'execution':packet.execution,'objective_validity':'failed' if hard else packet.objective_validity,
        'scientific_judgment':scientific,'operational_feasibility':packet.operational_feasibility,
        'oversight':packet.oversight,'reviewer_scientific_status':judgment.scientific_status,
        'hard_failures':sorted(failed&HARD_FAILURES),'failed_checks':sorted(failed)}


def evaluate_judgments(packet, ratings, *, policy, expected_policy_sha256, primary_score, adjudication=None):
    """Additive endpoint: never rewrite primary scores or infer expert qualification."""
    ratings=tuple(ratings)
    if len(ratings)>2:raise ValueError('At most two independent ratings are allowed')
    policy=EvaluationPolicy.model_validate_json(policy.model_dump_json())
    if policy_sha256(policy)!=expected_policy_sha256:raise ValueError('Frozen evaluation policy digest mismatch')
    content_sha256(primary_score)
    if primary_score is not None and (type(primary_score) not in {int,float} or not 0<=primary_score<=1):
        raise ValueError('Primary score must be finite in [0,1] or unavailable')
    verified_artifacts(packet)
    seen=set();effective=[]
    for rating in ratings:
        if (rating.stage!='independent' or not rating.blinded or
                rating.reviewer_id not in policy.independent_reviewer_ids or rating.reviewer_id in seen):
            raise ValueError('Independent reviewer does not bind the frozen panel')
        seen.add(rating.reviewer_id);effective.append(validate_judgment(packet,rating))
    if adjudication is not None:
        if adjudication.stage!='adjudication' or adjudication.reviewer_id!=policy.adjudicator_id:
            raise ValueError('Adjudication reviewer does not bind the frozen policy')
        if (seen!=set(policy.independent_reviewer_ids) or len(adjudication.independent_rating_sha256)!=2
                or set(adjudication.independent_rating_sha256)!={judgment_sha256(r) for r in ratings}):
            raise ValueError('Adjudication must bind both immutable independent ratings')
        adjudicated=validate_judgment(packet,adjudication)
    else:adjudicated=None
    failed={item.check for item in packet.objective_checks if item.status=='failed'}
    hard=bool(failed&HARD_FAILURES) or packet.objective_validity=='failed'
    operational=packet.execution!='completed' or 'execution' in failed
    success=None;status='not_assessed';reason='independent_review_incomplete'
    if hard:
        success=False;status='demonstrable_error';reason='coordinator_hard_failure'
    elif operational:
        success=False;reason='execution_did_not_complete'
    elif primary_score is None or packet.objective_validity!='verified':reason='objective_evidence_unavailable'
    elif packet.assay_semantics=='unknown' and packet.intent in {'exclude','infer'}:
        status='insufficient_evidence';reason='assay_semantics_unknown'
    elif seen==set(policy.independent_reviewer_ids) and all(r.blinded for r in ratings):
        states={e['scientific_judgment'] for e in effective}
        dispute_resolved=(bool(failed) and failed<=set(policy.disputable_failures)
            and adjudicated is not None
            and adjudicated['scientific_judgment'] in {'supported','defensible_alternative'})
        if adjudicated is not None and adjudicated['scientific_judgment']=='demonstrable_error':
            success=False;status='demonstrable_error';reason='adjudicator_error_reported'
        elif states&{'insufficient_evidence','not_assessed'}:
            status='insufficient_evidence';reason='review_unresolved'
        elif 'demonstrable_error' in states:
            # An initial reviewer label is a retained vote, not an objective veto.
            # Coordinator-proven hard errors already terminate above this branch.
            if dispute_resolved:
                success=True;status='defensible_alternative';reason='policy_scoped_evidence_bound_adjudication'
            else:reason='review_disagreement_requires_adjudication'
        elif primary_score==1 and not failed and adjudicated is not None and adjudicated['scientific_judgment'] in {'supported','defensible_alternative'}:
            success=True;status='defensible_alternative' if 'defensible_alternative' in states else 'supported';reason='objective_and_independent_reviews_supported'
        elif dispute_resolved:
            success=True;status='defensible_alternative';reason='policy_scoped_evidence_bound_adjudication'
        else:reason='primary_mismatch_requires_policy_and_adjudication'
    return {'record_format':'devbench.alternative-aware-judgment-endpoint/1',
        'packet_sha256':packet_sha256(packet),'policy_sha256':expected_policy_sha256,
        'primary_score':primary_score,'alternative_aware_success':success,'reason':reason,
        'execution':packet.execution,'objective_validity':'failed' if hard else packet.objective_validity,
        'scientific_judgment':status,'operational_feasibility':packet.operational_feasibility,'oversight':packet.oversight,
        'independent_ratings':[r.model_dump(mode='json') for r in ratings],
        'adjudication':adjudication.model_dump(mode='json') if adjudication is not None else None,
        'expert_validation':'unmet','human_authorship_authenticated':False,'confidence_calibrated':False,
        'clinical_release_allowed':False,'data_execution_granted':False,'model_execution_granted':False}
