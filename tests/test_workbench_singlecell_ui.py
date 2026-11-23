"""Explicit native single-cell startup binding, with no providers or analysis."""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from devbench.analysis import workbench_ui
from devbench.analysis.checkpoints import digest
from tests.test_workbench_ui import root
from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_workflow_accordion import SETUP
from tests.test_workbench_investigation_asset import run_js as run_controller


@pytest.fixture
def trial_helpers(monkeypatch):
    calls = []

    def validate(raw):
        calls.append(('validate', copy.deepcopy(raw)))
        if not isinstance(raw, dict) or not isinstance(raw.get('question'), str):
            raise ValueError('Invalid single-cell trial request')
        return copy.deepcopy(raw)

    def load(path):
        calls.append(('load', path))
        return validate(json.loads(Path(path).read_text()))

    def bind(raw, *, project_id, input_roots):
        calls.append(('bind', copy.deepcopy(raw), project_id, input_roots))
        if raw.get('project_id') not in (None, project_id):
            return None
        return {**copy.deepcopy(raw), 'project_id': project_id, 'request_sha256': digest(raw)}

    ns = SimpleNamespace(
        validate_singlecell_trial_request=validate, load_singlecell_trial_request=load,
        bind_singlecell_trial=bind)
    monkeypatch.setitem(sys.modules, 'devbench.analysis.workbench_singlecell', ns)
    monkeypatch.setitem(sys.modules, 'devbench.platform.workbench_singlecell', ns)
    monkeypatch.setattr(workbench_ui.WorkbenchService, '_runtime_identity', staticmethod(lambda: {'synthetic_test': True}))
    return calls


def launch_args(root):
    return ['--input-root', str(root / 'inputs'), '--workspace', str(root / 'workspace'), '--port', '0', '--open-browser']


def fake_launcher(monkeypatch):
    captured = []

    class App:
        token = 'synthetic-unused-launcher-key'

        def __init__(self, workspace, *_args, **kwargs):
            self.workspace = workspace
            self.workspace.mkdir(mode=0o700, exist_ok=True)
            self.offline_only = kwargs["offline_only"]
            captured.append(kwargs)

        def configuration(self):
            return {'proposal_selection': {'label': 'disabled'}}

        def close(self):
            pass

    class Server:
        server_address = ('127.0.0.1', 43210)

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(workbench_ui, 'WorkbenchService', App)
    monkeypatch.setattr(workbench_ui, 'make_server', lambda *args, **kwargs: Server())
    monkeypatch.setattr(workbench_ui, '_open_browser_async', lambda _: None)
    return captured


def test_explicit_cli_request_is_loaded_without_granting_model_or_data_authority(root, monkeypatch, trial_helpers):
    raw = {'question': 'Exact user question', 'data_classification': 'public'}
    request = root / 'request.json'
    request.write_text(json.dumps(raw))
    captured = fake_launcher(monkeypatch)
    workbench_ui.main(launch_args(root) + ['--enable-scientific', '--singlecell-trial-request', str(request)])
    assert ('load', request) in trial_helpers
    assert captured[0]['singlecell_trial_binding'] == raw
    assert captured[0]['allow_data_execution'] is False
    assert captured[0]['allow_model_execution'] is False
    assert captured[0]['model_data_scope'] == 'local_only'


def test_cli_requires_absolute_explicit_request_path(root, monkeypatch, trial_helpers, capsys):
    captured = fake_launcher(monkeypatch)
    with pytest.raises(SystemExit):
        workbench_ui.main(launch_args(root) + ['--enable-scientific', '--singlecell-trial-request', 'relative.json'])
    assert 'absolute' in capsys.readouterr().err.lower()
    assert captured == []
    assert not any(call[0] == 'load' for call in trial_helpers)


def test_service_trial_binding_requires_scientific_mode_before_creating_workspace(root, trial_helpers):
    inputs = root / 'inputs'
    inputs.mkdir()
    with pytest.raises(ValueError, match='scientific'):
        workbench_ui.WorkbenchService(root / 'workspace', [inputs], singlecell_trial_binding={'question': 'Exact'})
    assert not (root / 'workspace' / 'service.json').exists()


def test_service_validates_trial_request_before_persisting_settings(root, trial_helpers):
    inputs = root / 'inputs'
    inputs.mkdir()
    with pytest.raises(ValueError, match='Invalid single-cell'):
        workbench_ui.WorkbenchService(root / 'workspace', [inputs], enable_scientific=True, singlecell_trial_binding={})
    assert not (root / 'workspace' / 'service.json').exists()


def test_service_binds_exact_workspace_identity_and_freezes_independent_request_copy(root, trial_helpers):
    inputs = root / 'inputs'
    inputs.mkdir()
    raw = {'question': 'Exact', 'source': str(inputs / 'source.h5ad'), 'source_sha256': 'a' * 64}
    original = copy.deepcopy(raw)
    app = workbench_ui.WorkbenchService(root / 'workspace', [inputs], enable_scientific=True, singlecell_trial_binding=raw)
    try:
        project = app.workspace_state()['project']['id']
        bound = [call for call in trial_helpers if call[0] == 'bind'][-1]
        assert bound[1] == original
        assert bound[2] == project
        assert bound[3] == (inputs.resolve(),)
        assert app.singlecell_trial_binding['project_id'] == project
        raw['question'] = 'Mutated caller'
        assert app.singlecell_trial_binding['question'] == 'Exact'
        assert app.allow_data_execution is False and app.allow_model_execution is False
        configuration = json.loads((root / 'workspace' / 'service.json').read_text())
        assert configuration['singlecell_trial_request_sha256'] == digest(original)
        assert app.configuration()['singlecell_trial']['enabled'] is True
        assert app.configuration()['singlecell_trial']['scope'] == 'source_question_project_bound_facts_only'
        assert app.configuration()['singlecell_trial']['source'] == original['source']
        assert app.configuration()['singlecell_trial']['source_sha256'] == original['source_sha256']
        assert app.configuration()['singlecell_trial']['question'] == original['question']
        assert app.configuration()['singlecell_trial']['project_id'] == project
        assert app.configuration()['singlecell_trial']['request_sha256'] == digest(original)
    finally:
        app.close()


def test_nonmatching_project_disables_binding_without_import_or_execution(root, trial_helpers):
    inputs = root / 'inputs'
    inputs.mkdir()
    app = workbench_ui.WorkbenchService(root / 'workspace', [inputs], enable_scientific=True,
        singlecell_trial_binding={'question': 'Exact', 'project_id': 'f' * 32})
    try:
        assert app.singlecell_trial_binding is None
        assert app.configuration()['singlecell_trial']['enabled'] is False
        assert app.jobs() == []
        assert list(inputs.iterdir()) == []
    finally:
        app.close()


def test_reopening_workspace_cannot_replace_frozen_trial_facts(root, trial_helpers):
    inputs = root / 'inputs'
    inputs.mkdir()
    app = workbench_ui.WorkbenchService(root / 'workspace', [inputs], enable_scientific=True,
        singlecell_trial_binding={'question': 'Original'})
    app.close()
    with pytest.raises(ValueError, match='different frozen settings'):
        workbench_ui.WorkbenchService(root / 'workspace', [inputs], enable_scientific=True,
            singlecell_trial_binding={'question': 'Changed'})


def test_bound_trial_can_configure_its_registered_runtime_without_starting_work(root, trial_helpers):
    inputs = root / 'inputs'
    inputs.mkdir()
    configured = []
    runtime = object()

    class Selector:
        def configuration(self):
            return {'backend': 'recorded_fixture'}

        def select(self, *args, **kwargs):
            raise AssertionError('Startup must not request model decisions')

    selector = Selector()

    def factory(binding, **options):
        configured.append((binding, options))
        return runtime

    sys.modules['devbench.analysis.workbench_singlecell'].make_singlecell_scientific_runtime = factory
    app = workbench_ui.WorkbenchService(root / 'workspace', [inputs], enable_scientific=True,
        singlecell_trial_binding={'question': 'Exact'}, method_selector=selector)
    try:
        assert len(configured) == 1
        assert configured[0][0] == app.singlecell_trial_binding
        assert configured[0][1] == {'methods_root': (root / 'workspace' / 'methods').resolve(),
            'selector': selector, 'allow_model_execution': False}
        assert app.scientific.scientific_workflow is runtime
        assert app.jobs() == []
        assert app.allow_data_execution is False and app.allow_model_execution is False
    finally:
        app.close()


def test_data_preflight_permission_is_a_fresh_explicit_control_gated_by_service_capability():
    run_js(SETUP + r"""
const investigation={id:'chat',revision:1,status:'awaiting_information',question:'Exact bound question',scientific_context:{data_classification:'public'},declarations:{assay:'scrna'},clarification_fields:[{scope:'scientific_context',field:'allow_data_execution',type:'boolean',label:'Authorize local audit, design checks and pseudobulk preparation'},{scope:'scientific_context',field:'allow_model_execution',type:'boolean',label:'Allow configured model planning'},{scope:'scientific_context',field:'auto_advance',type:'boolean',label:'Advance planning to review'}],turns:[]};
const config={execution_allowed:true,scientific:{enabled:true,model_execution_allowed:true,scientific_workflow_configured:true}};
api.renderInvestigation(container,{investigation,config,onAction:a=>actions.push(a)});let fields=walk(container),data=fields.find(n=>n.attrs.name==='allow_data_execution');
assert.equal(data.checked,false);assert.equal(data.disabled,false);assert.equal(actions.length,0);assert.ok(!fields.some(n=>n.attrs.name==='assay'||n.attrs.name==='biological_unit_column'));
data.checked=true;data.change();const model=fields.find(n=>n.attrs.name==='allow_model_execution');model.checked=true;model.change();const advance=fields.find(n=>n.attrs.name==='auto_advance');advance.checked=true;advance.change();fields.find(n=>n.tagName==='form').submit({preventDefault(){}});
assert.equal(actions[0].scientificContext.allow_data_execution,true);assert.equal(actions[0].scientificContext.allow_model_execution,true);assert.equal(actions[0].scientificContext.auto_advance,true);
api.renderInvestigation(container,{investigation,config:{...config,execution_allowed:false},onAction:a=>actions.push(a)});fields=walk(container);data=fields.find(n=>n.attrs.name==='allow_data_execution');assert.equal(data.disabled,true);assert.equal(data.checked,false);
fields.find(n=>n.tagName==='form').submit({preventDefault(){}});assert.equal(actions[1].scientificContext.allow_data_execution,false);
""")


def test_singlecell_retained_tool_evidence_is_direct_bounded_and_never_claims_synthesis():
    run_js(SETUP + r"""
const binding={source:'/approved/cells.h5ad',source_sha256:'a'.repeat(64),project_id:'p',question_id:'chat',specification_sha256:'b'.repeat(64),result_ref:'c'.repeat(64),population:{kind:'supplied_populations',scope:'stage_specific_declared_unit_contrasts',biological_unit_column:'embryo_id',independence_verified:false}};
const tools=[['scrna_population_audit',1,'Population audit'],['scrna_population_design',3,'Study design'],['scrna_population_pseudobulk',4,'Pseudobulk preparation'],['scrna_population_contrast',4,'Population contrast']];
const investigation={id:'chat',revision:4,status:'completed',question:'Compare declared populations',analyst_profile:'scientific_singlecell_v1',artifacts:[{artifact_id:'d'.repeat(64)}],turns:tools.map(([tool_name,step],index)=>({id:'tool-'+index,kind:'singlecell_evidence',step,content:{format:'devbench.workbench-singlecell-evidence/1',tool_name,status:'completed',message:'Actual retained '+tool_name,summary:{biological_units:12,cell_independence:false},tables:tool_name.endsWith('contrast')?[{caption:'Exploratory display preview',columns:['gene','p_value','q_value'],rows:[['<script>G1</script>',0,null]],denominator:100,missing_count:null,truncated:true}]:[],binding,limitations:['Declared embryo identity is not independently authenticated.'],artifact_refs:[{artifact_id:'d'.repeat(64),sha256:'e'.repeat(64),size_bytes:96000000,artifact_type:'result'}]}}))};
const before=JSON.stringify(investigation);render(investigation);const nodes=walk(container),cards=byClass(container,'wc-singlecell-evidence');
assert.equal(cards.length,4);for(const [,,title] of tools)assert.ok(nodes.some(n=>n.textContent===title));
assert.ok(nodes.some(n=>n.tagName==='caption'&&n.textContent==='Exploratory display preview'));assert.ok(nodes.some(n=>n.tagName==='td'&&n.textContent==='<script>G1</script>'));assert.ok(nodes.some(n=>n.tagName==='td'&&n.textContent==='Not estimated'));
assert.ok(nodes.some(n=>n.textContent==='Biological units'));assert.ok(nodes.some(n=>n.textContent==='Declared embryo identity is not independently authenticated.'));
assert.ok(nodes.some(n=>n.textContent==='Population scope: stage specific declared unit contrasts · Unit column: embryo_id'));assert.ok(!nodes.some(n=>(n.textContent||'').includes('full-source metadata')));
assert.equal(sections()[0].attrs['data-workflow-status'],'observed');assert.equal(sections()[2].attrs['data-workflow-status'],'available');assert.equal(sections()[3].attrs['data-workflow-status'],'completed');assert.equal(sections()[4].attrs['data-workflow-status'],'not_run');
assert.equal(sections()[3].open,true);assert.ok(!nodes.some(n=>n.textContent==='Scientific synthesis'));
const open=nodes.find(n=>n.tagName==='button'&&n.textContent==='Open full result artifacts');assert.ok(open);open.click();assert.deepEqual(actions[0],{type:'navigate',view:'evidence'});
assert.ok(nodes.every(n=>n.innerHTML===undefined));assert.equal(JSON.stringify(investigation),before);
""")


def test_scoped_scientific_stop_uses_current_operation_without_an_analysis_job():
    run_js(SETUP + r"""
const investigation={id:'chat',revision:5,status:'planning',operation_pending:true,singlecell_trial:{request_sha256:'a'.repeat(64)},scientific_session_id:'session',turns:[{id:'op',kind:'scientific_operation',step:3,content:{operation:{action:'advance_to_gate',status:'running'}}}]};
const model=render(investigation);let card=byClass(container,'wc-scientific-status')[0];assert.ok(card);assert.equal(card.attrs.role,'status');assert.equal(model.inlineGate.jobId,null);
let stop=walk(card).find(n=>n.tagName==='button'&&n.textContent==='Stop scientific operation');assert.ok(stop);stop.click();assert.deepEqual(actions[0],{type:'cancel',investigationId:'chat',expectedRevision:5});
investigation.status='cancelling';render(investigation);card=byClass(container,'wc-scientific-status')[0];assert.ok(walk(card).some(n=>n.textContent==='Stopping scientific operation'));assert.ok(!walk(card).some(n=>n.tagName==='button'));
investigation.status='cancelled';investigation.operation_pending=false;render(investigation);card=byClass(container,'wc-scientific-status')[0];assert.ok(walk(card).some(n=>n.textContent==='Scientific operation cancelled'));assert.ok(!walk(card).some(n=>n.tagName==='button'));assert.equal(byClass(container,'is-running').length,0);
investigation.status='operation_failed';investigation.operation_pending=true;render(investigation);assert.ok(!walk(container).some(n=>n.tagName==='button'&&n.textContent==='Stop scientific operation'));
delete investigation.singlecell_trial;investigation.status='planning';render(investigation);assert.equal(byClass(container,'wc-scientific-status').length,0);
""")


def test_scoped_operation_pending_changes_repaint_and_cancel_without_new_grants():
    run_controller(r"""
const f=fixture();await f.controller.ready;f.server.record={...f.server.record,singlecell_trial:{request_sha256:'a'.repeat(64)},scientific_session_id:'session',status:'awaiting_information',operation_pending:true};
await f.controller.open(parent);const before=f.renders();f.server.record.operation_pending=false;await f.controller.refresh();assert.equal(f.renders(),before+1);
await f.controller.handleAction({type:'cancel',investigationId:parent,expectedRevision:1});
const sent=f.calls.find(call=>call.body?.action==='cancel');assert.deepEqual(sent.body,{action:'cancel',expected_revision:1});assert.equal(f.jobs.length,0);
""")


def test_offline_only_reopen_preserves_frozen_selector_settings(
    workspace_tmp_path, trial_helpers,
):
    from devbench.analysis.episode_planning import RecordedPlanningSelector

    workspace = workspace_tmp_path / "workspace"
    inputs = [workspace_tmp_path / "inputs"]
    inputs[0].mkdir(mode=0o700)
    selector = RecordedPlanningSelector({})
    configured = workbench_ui.WorkbenchService(
        workspace, inputs, proposal_selector=selector,
    )
    configured.close()
    marker = workspace / "service.json"
    before = marker.read_bytes()
    local = workbench_ui.WorkbenchService(workspace, inputs, offline_only=True)
    try:
        assert local.proposal_selector is None
        assert local.allow_model_execution is False
        assert local.configuration()["offline_only"] is True
        assert marker.read_bytes() == before
    finally:
        local.close()


def test_workspace_adoption_preserves_plugin_files_and_refuses_unknown_entries(
    workspace_tmp_path, trial_helpers,
):
    workspace = workspace_tmp_path / "workspace"
    inputs = [workspace_tmp_path / "inputs"]
    inputs[0].mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    for name in ("methods", "runs"):
        (workspace / name).mkdir(mode=0o700)
    standalone = workspace / "plugin-jobs.sqlite3"
    standalone.touch(mode=0o600)
    orphan = workspace / "unknown.json"
    orphan.touch()
    with pytest.raises(ValueError, match="dedicated empty workspace"):
        workbench_ui.WorkbenchService(workspace, inputs, offline_only=True)
    orphan.unlink()
    local = workbench_ui.WorkbenchService(workspace, inputs, offline_only=True)
    local.close()
    assert standalone.stat().st_size == 0
    assert (workspace / "service.json").is_file()
