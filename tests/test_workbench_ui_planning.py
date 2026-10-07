"""M1 uses the real planner boundary and small inputs; no provider is called."""
import inspect
import json
import os
from pathlib import Path
import tempfile
import time

import pytest

from devbench.analysis.checkpoints import digest, sha256
from devbench.analysis.episode_agent import Selection

BASE = Path(__file__).resolve().parents[1] / 'reports/rna-agent-readiness/2026-09-24-m1.i_ibb4sy'
DECISION = {'kind':'propose', 'workflow':'scrna_qc_integration_annotation/1',
            'rationale':'Inspect the supplied fixed QC and annotation workflow.'}


class RecordedSelector:
    def __init__(self, decision=None):
        self.decision = decision or DECISION
        self.calls = []

    def configuration(self):
        return {'backend':'recorded_planning_answer','answer_sha256':digest(self.decision)}

    def select(self, context, output, **options):
        assert options['allow_model_execution'] is False
        self.calls.append(context)
        return Selection(self.decision, {'backend':'recorded_planning_answer',
            'model_sessions_attempted':0,'actual_model':None,'tokens':None,'cost_usd':None,
            'scope':'recorded offline fixture decision'})


@pytest.fixture
def root():
    BASE.mkdir(parents=True,exist_ok=True)
    # Keep execution and failure artifacts for the milestone's completion audit.
    yield Path(tempfile.mkdtemp(prefix='planning-',dir=BASE))


def setup(root, selector=None, **options):
    from devbench.analysis.workbench_ui import WorkbenchService
    assert 'proposal_selector' in inspect.signature(WorkbenchService).parameters, 'UI is not connected to the proposal selector'
    from tests.test_workbench_proposals import fixture
    inputs=root/'inputs';inputs.mkdir()
    source, parameters, declarations=fixture(inputs)
    excluded={'workflow','prompt','execution_tier','data_sha256','parent_root','model_path','model_sha256'}
    app=WorkbenchService(root/'service',[inputs],parameter_overrides={k:v for k,v in parameters.items() if k not in excluded},
        run_timeout_seconds=180,proposal_selector=selector,**options)
    request={'source':str(source),'expected_sha256':sha256(source),
        'question':'Inspect cell quality and available annotation','declarations':declarations,'proposal_mode':'agent'}
    return app,request


def wait(app, job):
    from devbench.analysis.workbench_ui import ACTIVE
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        record=app.status(job)
        if record['status'] not in ACTIVE:return record
        time.sleep(.02)
    pytest.fail('bounded planner fixture did not finish')


def approval():
    from devbench.analysis.workbench_ui import CONFIRMATIONS
    return {'reviewer':'fixture operator','decision':'approve','rationale':'Inspect this synthetic fixture only',
            'confirmations':{key:True for key in CONFIRMATIONS}}


def test_recorded_selector_configuration_is_not_live_model_access(root):
    app,_=setup(root,RecordedSelector())
    try:
        config=app.configuration()
        assert config['proposal_selection']['mode']=='recorded_fixture'
        assert config['proposal_selection']['available'] is True
        assert config['model_access'] is False
    finally:app.close()


def test_default_service_cannot_launch_an_agent(root):
    app,request=setup(root)
    try:
        with pytest.raises(PermissionError,match='proposal|selector|Agent'):
            app.register(request)
        assert app.jobs()==[]
    finally:app.close()


def test_codex_is_wired_but_disabled_before_inspection(root,monkeypatch):
    from devbench.analysis.episode_codex import CodexActionSelector
    monkeypatch.setattr(CodexActionSelector,'select',lambda *a,**kw:pytest.fail('unauthorized provider dispatch'))
    app,request=setup(root,codex_model='test-model-not-dispatched',agent_workspaces=root/'actors')
    try:
        assert isinstance(app.proposal_selector,CodexActionSelector)
        assert app.configuration()['proposal_selection']['mode']=='codex'
        assert app.configuration()['proposal_selection']['available'] is False
        with pytest.raises(PermissionError):app.register(request)
        assert app.jobs()==[]
    finally:app.close()


def test_real_planner_proposal_reaches_operator_review_with_bound_origin(root):
    selector=RecordedSelector();app,request=setup(root,selector)
    try:
        job=app.register(request)['id'];record=wait(app,job)
        assert record['status']=='awaiting_operator_review',record
        assert record['plan_author']=='recorded_fixture'
        assert record['planning_result']['status']=='proposal_ready_for_review'
        assert record['model_sessions_attempted']==0
        assert record['proposal']['authoring']['root_hash']==record['planning_result']['authoring_receipt']['root_hash']
        assert len(selector.calls)==1 and selector.calls[0]['workflow_family']=='backed_workbench'
        assert str(root) not in json.dumps(selector.calls[0])
        app.review(job,approval())
        assert wait(app,job)['status']=='approved'
    finally:app.close()


@pytest.mark.parametrize('decision,status',[
    ({'kind':'request_information','message':'Confirm capture-unit provenance.'},'awaiting_information'),
    ({'kind':'refuse','reason':'The requested causal comparison is unsupported.'},'refusal'),
])
def test_nonproposals_and_duplicate_requests_remain_terminal(root,decision,status):
    selector=RecordedSelector(decision);app,request=setup(root,selector)
    if decision['kind']=='request_information':
        request['declarations']['capture_units_confirmed']=False
    else:
        request['declarations']['assay']='snrna'
        request['question']='Use this workflow for nuclei despite its declared scRNA-only support'
    try:
        job=app.register(request)['id'];record=wait(app,job)
        assert record['status']==status and not record.get('proposal_receipt'),record
        again=app.register(request)
        assert again['id']==job and again['cached'] is True
        assert len(selector.calls)==1
        assert selector.calls[0]['allowed_workflows']==[]
        with pytest.raises(RuntimeError):app.review(job,approval())
        with pytest.raises((RuntimeError,PermissionError)):app.run(job)
    finally:app.close()


def test_actor_cannot_expand_settings_or_claim_a_different_workflow(root):
    selector=RecordedSelector({**DECISION,'memory_mb':999999})
    app,request=setup(root,selector)
    try:
        record=wait(app,app.register(request)['id'])
        assert record['status']=='invalid_proposal' and not record.get('proposal_receipt'),record
    finally:app.close()


def test_changed_request_gets_new_identity_and_no_approval(root):
    app,request=setup(root,RecordedSelector())
    try:
        first=app.register(request)['id'];assert wait(app,first)['status']=='awaiting_operator_review'
        app.review(first,approval());assert wait(app,first)['status']=='approved'
        second=app.register({**request,'question':'Inspect a different descriptive question'})['id']
        assert second!=first
        assert wait(app,second)['status']=='awaiting_operator_review'
        assert 'review_receipt' not in app.status(second)
    finally:app.close()


def test_tampered_agent_authoring_cannot_be_approved(root):
    app,request=setup(root,RecordedSelector())
    try:
        job=app.register(request)['id'];assert wait(app,job)['status']=='awaiting_operator_review'
        context=app.workspace/'jobs'/job/'planning/context.json'
        context.write_bytes(context.read_bytes()+b'\n')
        app.review(job,approval())
        record=wait(app,job)
        assert record['status']=='operation_failed' and 'review_receipt' not in record
    finally:app.close()


def test_stopped_request_is_reused_after_service_restart(root):
    selector=RecordedSelector({'kind':'refuse','reason':'Unsupported estimand'})
    app,request=setup(root,selector)
    job=app.register(request)['id'];assert wait(app,job)['status']=='refusal'
    parameters={key:value for key,value in app.parameters.items() if key not in
        {'workflow','prompt','execution_tier','data_sha256','parent_root','model_path','model_sha256'}}
    app.close()
    from devbench.analysis.workbench_ui import WorkbenchService
    reopened=WorkbenchService(root/'service',[root/'inputs'],parameter_overrides=parameters,
        run_timeout_seconds=180,proposal_selector=selector)
    try:
        assert reopened.register(request)=={'id':job,'status':'refusal','cached':True}
        assert len(selector.calls)==1
    finally:reopened.close()


def test_cached_request_is_not_reused_after_source_drift(root,monkeypatch):
    from devbench.analysis import workbench_proposals
    app,request=setup(root,RecordedSelector({'kind':'refuse','reason':'Unsupported question'}))
    try:
        job=app.register(request)['id'];assert wait(app,job)['status']=='refusal'
        monkeypatch.setattr(workbench_proposals,'_source_identity',lambda:'f'*64)
        with pytest.raises(RuntimeError,match='runtime|source|restart'):
            app.register(request)
        assert app.status(job)['status']=='refusal'
    finally:app.close()


def test_mutated_selector_cannot_reuse_a_frozen_service_configuration(root):
    selector=RecordedSelector();app,request=setup(root,selector)
    try:
        selector.decision={'kind':'refuse','reason':'Changed after service construction'}
        with pytest.raises(ValueError,match='configuration changed'):app.register(request)
        assert app.jobs()==[]
    finally:app.close()
