"""Investigation coordinator boundaries using an isolated native-DOM harness."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).resolve().parents[1]
    / "devbench/analysis/assets/workbench-investigations.js"
)


def run_js(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the isolated investigation UI checks")
    setup = r"""
const assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.handlers={};
    this.value='';this.hidden=false;this.disabled=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs[key]=String(value);}
  addEventListener(event,fn){(this.handlers[event]||(this.handlers[event]=[])).push(fn);
    }
  removeEventListener(event,fn){this.handlers[event]=(this.handlers[event]||[]).filter(
    item=>item!==fn);}
  focus(){this.focused=true;}
}
const document={createElement:tag=>new Element(tag)};
const walk=node=>[node,...node.children.flatMap(walk)];
const parent='a'.repeat(32),child='b'.repeat(32),source='/approved/cells.h5ad';
const profile={source,sha256:'f'.repeat(64),observed:{cells:80,genes:60,obs_columns:[
  'capture','batch'],layers:['counts'],obsm_keys:[]},declarations:{},unknowns:[
  'Experimental assay is unconfirmed.']};
function fixture(extra={}) {
  const calls=[],timers=new Map(),statuses=[],gates=[],jobs=[],views=[];
    let timerId=0,renders=0;
  const server={history:[],record:{id:parent,revision:1,status:'awaiting_information',
    question:'Inspect QC',source,profile,clarification_fields:[],turns:[],job_id:null}};
  const options={document,config:{model_access:true,proposal_selection:{mode:'codex',
    available:true}},questionInput:new Element('textarea'),datasetSelect:new Element(
    'select'),modeSelect:new Element('select'),conversationContainer:new Element('div'),
    historyContainer:new Element('div'),inputsContainer:new Element('div'),
    selectionHost:new Element('div'),submitButton:new Element('button'),
    setTimeout(fn){timers.set(++timerId,fn);return timerId;
      },clearTimeout(id){timers.delete(id);},
    onStatus(message,error){statuses.push({message,error});
      },onGate(gate){gates.push(gate);},onJob(id){jobs.push(id);
      },onCanvas(view){views.push(view);},
    conversation:{renderInvestigation(container,value){renders++;
      const p=new Element('p');p.textContent=value.investigation?.question||
      'Ask a question';container.replaceChildren(p);
      return {inlineGate:{kind:value.investigation?.status==='awaiting_information'?
      'clarification':value.investigation?.status==='awaiting_operator_review'?'review':
      value.investigation?.status==='approved'?'run':null}};}},
    async request(path,body){calls.push({path,body:body===undefined?undefined:
      JSON.parse(JSON.stringify(body))});
      if(server.request)return server.request(path,body);
      if(path==='/api/inputs')return {inputs:[{source,filename:'cells.h5ad',size_bytes:
        1024,hash_status:'pending'}]};
      if(path==='/api/inputs/inspect')return profile;
      if(path==='/api/investigations'&&body===undefined)return {investigations:
        server.history};
      if(path==='/api/investigations'&&body){server.record={...server.record,question:
        body.question,source:body.source};return JSON.parse(JSON.stringify(
        server.record));}
      if(path==='/api/investigations/'+parent||path==='/api/investigations/'+child){
        if(body?.action==='follow_up'){server.record={...server.record,id:child,
          revision:0,status:'awaiting_information',question:body.question,job_id:null};}
        else if(body?.action==='review')server.record={...server.record,revision:
          server.record.revision+1,status:'approved'};
        else if(body?.action==='run')server.record={...server.record,revision:
          server.record.revision+1,status:'running'};
        else if(body?.action==='clarify')server.record={...server.record,revision:
          server.record.revision+1,declarations:body.declarations,status:
          'awaiting_operator_review'};
        return JSON.parse(JSON.stringify(server.record));
      }
      throw Error('Unexpected route '+path);
    },...extra};
  const controller=globalThis.WorkbenchInvestigations.mount(options);
  return {controller,options,calls,timers,statuses,gates,jobs,views,server,renders:()=>
    renders};
}
function queueFixture(extra={}) {
  const questionInput=new Element('textarea'),composer=new Element('div');
  composer.append(questionInput);
  composer.insertBefore=(node,anchor)=>composer.children.splice(composer.children.indexOf(anchor),0,node);
  questionInput.parentNode=composer;
  return {...fixture({questionInput,...extra}), questionInput, tray:composer.children[0]};
}
"""
    code = "require(" + json.dumps(str(MODULE)) + ");\n" + setup
    code += (
        "\n(async()=>{\n" + scenario
        + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    )
    result = subprocess.run(
        [node, "-"], input=code, text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_discovery_inspection_and_clarification_are_read_only_until_explicit_submit():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.deepEqual(f.calls.map(call=>call.path).sort(),['/api/inputs',
  '/api/investigations']);
assert.equal(f.options.questionInput.value,'');
  assert.equal(f.controller.current(),null);
f.options.datasetSelect.value=source;await f.controller.inspect(source);
assert.deepEqual(f.calls.at(-1),{path:'/api/inputs/inspect',body:{source}});
assert.ok(walk(f.options.inputsContainer).some(node=>
  node.textContent==='capture, batch'));
f.options.questionInput.value='Inspect the saved QC';await f.controller.submit();
const create=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.deepEqual(create.body,{question:'Inspect the saved QC',source,proposal_mode:
  'agent'});
assert.equal(f.options.questionInput.value,'');
  assert.equal(f.controller.current().status,'awaiting_information');
assert.equal(f.gates.at(-1).kind,'clarification');
assert.ok(!f.calls.some(call=>['review','run'].includes(call.body?.action)));
""")


def test_adaptive_panel_pages_and_consumes_one_explicit_revision_bound_grant():
    run_js(r"""
const host=new Element('section'),calls=[];
const panel=globalThis.WorkbenchInvestigations.mountAdaptivePanel({host,document,
  async request(path,body){
  calls.push({path,body});
  if(path.includes('/replay'))return {verified:true,current_use:'unavailable',
    model_sessions:0,numerical_stage_launches:0};
  return {mode:'adaptive/1',session_id:parent,graph_status:'available',revision:7,
    qualification:'not_established',
    actions:{advance:true,model_required:true},replay_available:true,
    graph:{entries:[{entry_kind:'step',step_id:path.includes('?')?'graph-v2':
      'bounded-qc',status:'ready',generation:1}],
      total_entries:2,next_cursor:path.includes('?')?null:'e'.repeat(32)}};
}});
const byText=value=>walk(host).find(node=>node.textContent===value);
const data=()=>walk(host).find(node=>node.attrs[
  'aria-label']==='Allow data execution for this step');
const model=()=>walk(host).find(node=>node.attrs[
  'aria-label']==='Allow model selection for this step');
await panel.select(parent);
assert.equal(calls[0].path,'/api/scientific/adaptive/'+parent);
assert.equal(byText('Advance workflow').disabled,true);
data().checked=true;data().onchange();
assert.equal(byText('Advance workflow').disabled,true);
model().checked=true;model().onchange();
assert.equal(byText('Advance workflow').disabled,false);
await byText('Advance workflow').onclick();
assert.deepEqual(calls.find(call=>call.body)?.body,{action:'advance',expected_revision:
  7,allow_data_execution:true,allow_model_execution:true});
assert.equal(data().checked,false);assert.equal(model().checked,false);
await byText('Next evidence page').onclick();
assert.ok(walk(host).some(node=>node.textContent?.includes('graph-v2')));
assert.equal(calls.at(-1).path,'/api/scientific/adaptive/'+parent+'?cursor='+'e'.repeat(
  32));
await byText('Verify retained replay').onclick();
assert.equal(calls.at(-1).body,undefined);
assert.ok(walk(host).some(node=>node.textContent?.includes(
  'historical evidence verified')));
await panel.select(null);
assert.deepEqual(host.children,[]);
""")


def test_adaptive_cancel_uses_current_revision_while_advance_is_pending():
    run_js(r"""
const host=new Element('section'),calls=[];
let revision=7,cancelled=false,finish;
const advancing=new Promise(resolve=>{finish=resolve;});
const panel=globalThis.WorkbenchInvestigations.mountAdaptivePanel({host,document,
  async request(path,body){
  calls.push({path,body});
  if(body?.action==='advance'){revision=8;await advancing;return {steps_dispatched:0};}
  if(body?.action==='cancel'){cancelled=true;revision=9;
    return {status:'reconciliation_required',cleanup_status:'unverified'};}
  return {mode:'adaptive/1',session_id:parent,graph_status:'available',revision,
    actions:{advance:!cancelled,cancel:!cancelled,model_required:false},
    graph:{entries:[],execution_block:cancelled?'scientific_cancelled':null}};
}});
const byText=value=>walk(host).find(node=>node.textContent===value);
await panel.select(parent);
const data=walk(host).find(node=>node.attrs[
  'aria-label']==='Allow data execution for this step');
data.checked=true;data.onchange();
const pending=byText('Advance workflow').onclick();
await byText('Request cancellation').onclick();
finish();
await pending;
assert.deepEqual(calls.find(call=>call.body?.action==='cancel')?.body,{action:'cancel',
  expected_revision:8});
assert.ok(walk(host).some(node=>node.textContent?.includes(
  'Campaign cancellation requires reconciliation')));
assert.equal(byText('Advance workflow'),undefined);
""")


def test_bulk_composer_uses_advertised_analyst_without_changing_scrna_route():
    run_js(r"""
const f=fixture({config:{model_access:true,proposal_selection:{mode:'codex',available:
  true},investigations:{analyst_profile:'analyst_v1'}}});await f.controller.ready;
const csv='/approved/expression.csv';f.server.request=async(path,body)=>{
  if(path==='/api/inputs')return {inputs:[{source:csv}]};
  if(path==='/api/investigations'&&body)return {...f.server.record,source:csv,
    analyst_profile:body.analyst_profile};
  if(path==='/api/investigations')return {investigations:[]};throw Error(path);
};await f.controller.reloadInputs();f.controller.setSources([csv]);
  f.options.questionInput.value='Compare condition expression';
  await f.controller.submit();
const singleCsv=f.calls.find(call=>
  call.body?.question==='Compare condition expression');
assert.equal(singleCsv.body.analyst_profile,undefined);
const g=fixture({config:f.options.config});await g.controller.ready;
  g.options.datasetSelect.value=source;g.options.questionInput.value='Cell quality';
  await g.controller.submit();
assert.equal(g.calls.find(call=>
  call.body?.question==='Cell quality').body.analyst_profile,undefined);
""")


def test_analyst_episode_actions_use_exact_investigation_revision_and_permissions():
    run_js(r"""
const f=fixture();await f.controller.ready;
  f.server.record={...f.server.record,analyst_profile:'analyst_v1',revision:5,job_id:
  'c'.repeat(32),job_state:{operation_kind:'agentic_episode',status:'running',
  cancellable:true}};await f.controller.open(parent);
await f.controller.handleAction({type:'clarify_episode',response:{message:
  'Units confirmed from source records',preserves_frozen_inputs_and_settings:true}});
assert.deepEqual(f.calls.find(call=>call.body?.action==='clarify_episode').body,{action:
  'clarify_episode',expected_revision:5,response:{message:
  'Units confirmed from source records',preserves_frozen_inputs_and_settings:true},
  allow_data_execution:true,allow_model_execution:true});
await f.controller.handleAction({type:'cancel'});
  assert.equal(f.calls.at(-2).body.action,'cancel');
f.server.record.scientific_context={allow_model_execution:true};
  await f.controller.refresh();await f.controller.handleAction({type:'resume'});
assert.equal(f.calls.find(call=>call.body?.action==='resume').body.allow_data_execution,
  true);assert.equal(f.calls.find(call=>
  call.body?.action==='resume').body.allow_model_execution,true);
await f.controller.run({allow_data_execution:true,allow_model_execution:true});
  assert.equal(f.calls.find(call=>call.body?.action==='run').body.allow_model_execution,
  true);
""")


def test_old_inline_action_cannot_mutate_a_different_chat():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
  const count=f.calls.length;
await assert.rejects(f.controller.handleAction({type:'clarify_episode',investigationId:
  child,expectedRevision:1,response:{}}),/changed|chat/i);
assert.equal(f.calls.length,count);
""")


def test_active_analyst_evidence_poll_renders_changes_without_revision_change():
    run_js(r"""
const f=fixture();await f.controller.ready;
  f.server.record={...f.server.record,status:'running',job_state:{operation_kind:
  'agentic_episode',status:'running',agent_actions:[]}};await f.controller.open(parent);
  const before=f.renders();
f.server.record.job_state.agent_actions.push({kind:'query',query:{mode:'counts'}});
  await f.controller.refresh();assert.equal(f.renders(),before+1);
  assert.equal(f.timers.size,1);
""")


def test_bulk_composer_can_attach_optional_annotation_mapping():
    run_js(r"""
const paths=['/approved/expression.csv','/approved/metadata.csv',
  '/approved/annotations.json'],f=fixture({config:{model_access:true,proposal_selection:
  {mode:'codex',available:true},investigations:{analyst_profile:'analyst_v1'}}});
  await f.controller.ready;
f.server.request=async(path,body)=>path==='/api/inputs'?{inputs:paths.map(source=>({
  source}))}:body?{...f.server.record,sources:paths,source:paths[0]}:{investigations:[
  ]};await f.controller.reloadInputs();f.controller.setSources(paths);
  f.options.questionInput.value='Inspect bulk contrast';await f.controller.submit();
  assert.equal(f.calls.find(call=>
  call.body?.question==='Inspect bulk contrast').body.analyst_profile,'analyst_v1');
""")


def test_analyst_followup_inherits_profile_without_sending_new_record_fields():
    run_js(r"""
const csv='/approved/expression.csv',f=fixture({config:{model_access:true,
  proposal_selection:{mode:'codex',available:true},investigations:{analyst_profile:
  'analyst_v1'}}});await f.controller.ready;
f.server.record={...f.server.record,source:csv,analyst_profile:'analyst_v1'};
f.server.request=async(path,body)=>path==='/api/inputs'?{inputs:[{source:csv}]}:
  path==='/api/investigations'?{investigations:[]}:f.server.record;
await f.controller.reloadInputs();await f.controller.open(parent);
  f.options.questionInput.value='Compare another contrast';await f.controller.submit();
const body=f.calls.find(call=>call.body?.action==='follow_up').body;
  assert.equal(body.analyst_profile,undefined);assert.equal(body.expected_revision,1);
""")


def test_submission_preserves_a_draft_edited_during_the_request():
    run_js(r"""
const f=fixture();await f.controller.ready;f.options.datasetSelect.value=source;
  f.options.questionInput.value='Submitted question';
let release;const waiting=new Promise(resolve=>{release=resolve;});
f.server.request=async(path,body)=>{if(path==='/api/investigations'&&body){
  await waiting;return {...f.server.record,question:body.question};
  }if(path==='/api/investigations')return {investigations:[]};throw Error(path);};
const pending=f.controller.submit();assert.equal(f.options.submitButton.disabled,true);
  f.options.questionInput.value='My next unsent edit';release();await pending;
assert.equal(f.options.questionInput.value,'My next unsent edit');
  assert.equal(f.options.submitButton.disabled,false);
""")


def test_queue_drains_in_background_without_overwriting_a_new_draft():
    run_js(r"""
const f=queueFixture();await f.controller.ready;f.options.datasetSelect.value=source;
let release;const wait=new Promise(resolve=>{release=resolve;});
f.server.request=async(path,body)=>{
  if(path==='/api/investigations'&&body){await wait;return {...f.server.record,status:'completed',question:body.question};}
  if(path==='/api/investigations')return {investigations:[]};
  if(body?.action==='follow_up')return {...f.server.record,id:child,status:'completed',question:body.question};
  throw Error(path);
};
f.questionInput.value='One';
const sending=f.controller.submit();
assert.equal(f.questionInput.value,'');
f.questionInput.value='Two';await f.controller.submit();
f.questionInput.value='Three, still a draft';
release();await sending;await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(f.calls.filter(call=>call.body?.question).map(call=>call.body.question),['One','Two']);
assert.equal(f.questionInput.value,'Three, still a draft');
assert.equal(f.tray.hidden,true);
""")


def test_failed_queued_send_stays_available_for_explicit_retry():
    run_js(r"""
const f=queueFixture();await f.controller.ready;f.server.record.status='running';
await f.controller.open(parent);
f.questionInput.value='Queued follow-up';await f.controller.submit();
let rejected=true;
f.server.request=async(path,body)=>{
  if(path==='/api/investigations')return {investigations:[]};
  if(body?.action==='follow_up'&&rejected)throw Error('Connection interrupted');
  if(body?.action==='follow_up')return {...f.server.record,id:child,question:body.question};
  return {...f.server.record,status:'completed'};
};
await f.controller.refresh();await new Promise(resolve=>setImmediate(resolve));
assert.equal(f.tray.hidden,false);
assert.equal(walk(f.tray).find(node=>node.className==='wc-queue-text').textContent,'Queued follow-up');
assert.equal(f.calls.filter(call=>call.body?.action==='follow_up').length,1);
f.server.record.status='completed';await f.controller.refresh();
assert.equal(f.calls.filter(call=>call.body?.action==='follow_up').length,1);
rejected=false;
await walk(f.tray).find(node=>node.textContent==='Retry').onclick();
assert.equal(f.tray.hidden,true);
""")


def test_queue_keeps_its_chat_and_exact_selection_while_switching():
    run_js(r"""
const f=queueFixture();await f.controller.ready;
const unrelated='/approved/second.h5ad';
let settled=false;
f.server.request=async(path,body)=>{
  if(path==='/api/inputs')return {inputs:[{source},{source:unrelated}]};
  if(path==='/api/investigations')return {investigations:[]};
  if(body?.action==='follow_up')return {...f.server.record,id:child,status:'completed',question:body.question};
  if(path.endsWith(child))return {...f.server.record,id:child,source:unrelated,status:'completed'};
  return {...f.server.record,status:settled?'completed':'running'};
};
await f.controller.reloadInputs();await f.controller.open(parent);
f.controller.setSelection({figure_id:'qc',panel_id:'p',observation_ids:['cell2'],
  source_job:'c'.repeat(32),parent_root:'d'.repeat(64),display_only:true});
f.questionInput.value='Investigate the selected cell';await f.controller.submit();
await f.controller.open(child);assert.equal(f.tray.hidden,true);
assert.equal(f.calls.filter(call=>call.body?.action==='follow_up').length,0);
settled=true;await f.controller.open(parent);
await new Promise(resolve=>setImmediate(resolve));
const follow=f.calls.find(call=>call.body?.action==='follow_up');
assert.equal(follow.path,'/api/investigations/'+parent);
assert.deepEqual(follow.body.selection.observation_ids,['cell2']);
""")


def test_send_with_a_different_single_file_omits_the_previous_job():
    run_js(r"""
const f=fixture();await f.controller.ready;
const unrelated='/approved/second.h5ad';
f.server.request=async(path,body)=>{
  if(path==='/api/inputs')return {inputs:[{source},{source:unrelated}]};
  if(path==='/api/investigations'&&body)return {...f.server.record,id:child,source:unrelated,question:body.question};
  if(path==='/api/investigations')return {investigations:[]};
  return {...f.server.record,job_id:'c'.repeat(32),job_state:{source,status:'completed'},status:'completed'};
};
await f.controller.reloadInputs();await f.controller.open(parent);
f.controller.setSources([unrelated]);
f.options.questionInput.value='Inspect the new file';await f.controller.submit();
const sent=f.calls.find(call=>call.body?.question==='Inspect the new file');
assert.equal(sent.path,'/api/investigations');
assert.equal(sent.body.source,unrelated);
assert.equal(sent.body.parent_investigation,parent);
assert.equal(sent.body.parent_job,undefined);
""")


def test_queue_waits_at_human_gates_and_send_now_dispatches_the_selected_message():
    run_js(r"""
const questionInput=new Element('textarea'),composer=new Element('div');
composer.append(questionInput);
composer.insertBefore=(node,anchor)=>composer.children.splice(composer.children.indexOf(
  anchor),0,node);
questionInput.parentNode=composer;
const f=fixture({questionInput});
await f.controller.ready;
f.server.record.status='running';
await f.controller.open(parent);
for(const question of ['First queued question','Second queued question']){
  questionInput.value=question;await f.controller.submit();
}
const followups=()=>f.calls.filter(call=>call.body?.action==='follow_up');
const tray=composer.children[0];
for(const status of ['awaiting_operator_review','awaiting_information',
  'needs_information']){
  f.server.record.status=status;await f.controller.refresh();
  assert.equal(followups().length,0,status);
  assert.equal(tray.hidden,false,status);
  assert.equal(walk(tray).filter(node=>node.className==='wc-queue-text').length,2,
    status);
}
await walk(tray).filter(node=>node.textContent==='Send Now')[1].onclick();
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(followups().map(call=>call.body.question),['Second queued question']);
assert.equal(walk(tray).find(node=>node.className==='wc-queue-text').textContent,
  'First queued question');
f.server.record.status='completed';await f.controller.refresh();
await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(followups().map(call=>call.body.question),['Second queued question',
  'First queued question']);
assert.equal(tray.hidden,true);
""")


def test_followup_creates_a_separate_plan_with_source_context_and_fresh_review():
    run_js(r"""
const f=fixture();await f.controller.ready;
  f.server.record={...f.server.record,revision:4,status:'completed_review_passed',
  job_id:'c'.repeat(32),job_state:{status:'completed_review_passed'}};
await f.controller.open(parent);f.options.questionInput.value=
  'Compare the observed batch structure';await f.controller.submit();
const follow=f.calls.find(call=>call.body?.action==='follow_up');
  assert.equal(follow.path,'/api/investigations/'+parent);
assert.equal(follow.body.expected_revision,4);
  assert.equal(follow.body.question,'Compare the observed batch structure');
assert.equal(f.controller.current().id,child);
  assert.equal(f.controller.current().status,'awaiting_information');
assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
""")


def test_exact_selection_is_a_removable_chip_and_never_inserted_into_the_question():
    run_js(r"""
const f=fixture();await f.controller.ready;f.options.datasetSelect.value=source;
  f.options.questionInput.value='Inspect these cells';
const original={figure_id:'uncorrected',panel_id:'observed_batch',observation_ids:[
  'cell2','cell9'],context:{job_id:'c'.repeat(32),parent_root:'d'.repeat(64)},
  display_only:true};
const attached=f.controller.setSelection(original);
  original.observation_ids.push('mutated');
assert.deepEqual(attached.observation_ids,['cell2','cell9']);
  assert.equal(f.options.questionInput.value,'Inspect these cells');
assert.ok(walk(f.options.selectionHost).some(node=>
  node.textContent==='Selected cells (2)'));
await f.controller.submit();const create=f.calls.find(call=>
  call.path==='/api/investigations'&&call.body);
assert.deepEqual(create.body.selection.observation_ids,['cell2','cell9']);
  assert.equal(create.body.selection.source_job,'c'.repeat(32));
assert.equal(create.body.parent_job,'c'.repeat(32));
  assert.equal(f.options.selectionHost.hidden,true);
f.controller.setSelection({...original,observation_ids:['other']});
const remove=walk(f.options.selectionHost).find(node=>node.attrs[
  'aria-label']==='Remove selected-cell context');remove.handlers.click[0]();
assert.equal(f.options.selectionHost.hidden,true);
""")


def test_oversized_selection_is_refused_without_truncation_or_submitting():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.equal(f.controller.setSelection({figure_id:'qc',panel_id:'x',observation_ids:
  Array.from({length:1001},(_,i)=>'c'+i),context:{job_id:'c'.repeat(32),parent_root:
  'd'.repeat(64)},display_only:true}),null);
assert.match(f.statuses.at(-1).message,/1,000/);
  assert.equal(f.calls.filter(call=>call.body).length,0);
assert.throws(()=>globalThis.WorkbenchInvestigations.normalizeSelection({
  observation_ids:['a','a'],display_only:true}),/unique/);
""")


def test_only_explicit_review_and_run_actions_mutate_authority():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
await f.controller.handleAction({type:'clarify',declarations:{assay:'scrna',
  capture_unit_key:'capture',capture_units_confirmed:true}});
const clarification=f.calls.find(call=>call.body?.action==='clarify');
  assert.equal(clarification.body.expected_revision,1);
assert.equal(clarification.body.declarations.capture_unit_key,'capture');
  assert.equal(f.controller.current().status,'awaiting_operator_review');
await f.controller.review({reviewer:'Operator',decision:'approve',rationale:'Reviewed',
  confirmations:{scope:true}});
assert.equal(f.controller.current().status,'approved');
  assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
await assert.rejects(f.controller.run({allow_data_execution:false}),
  /explicit run action/);
await f.controller.run({allow_data_execution:true});
  const run=f.calls.find(call=>call.body?.action==='run');
assert.equal(run.body.allow_data_execution,true);
  assert.equal(run.body.expected_revision,3);
  assert.equal(f.controller.current().status,'running');
""")


def test_polling_preserves_unchanged_focus_and_does_not_reopen_same_job():
    run_js(r"""
const f=fixture();await f.controller.ready;
  f.server.record={...f.server.record,status:'running',job_id:'c'.repeat(32),job_state:{
  status:'running'}};
await f.controller.open(parent);const rendered=f.renders();
  assert.equal(f.jobs.length,1);assert.equal(f.timers.size,1);
await f.controller.refresh();assert.equal(f.renders(),rendered);
  assert.equal(f.jobs.length,1);
f.server.record={...f.server.record,revision:2,status:'completed_review_passed',
  job_state:{status:'completed_review_passed'}};
await f.controller.refresh();assert.ok(f.renders()>rendered);
  assert.equal(f.timers.size,0);assert.equal(f.jobs.length,1);
""")


def test_history_and_profile_text_are_safe_and_destruction_stops_polling():
    run_js(r"""
const f=fixture();await f.controller.ready;
  f.server.history=[{id:parent,source,question:'<script>unsafe</script>',status:
  'running'}];await f.controller.reloadHistory();
assert.ok(walk(f.options.historyContainer).some(node=>
  node.textContent==='<script>unsafe</script>'));
assert.ok(walk(f.options.historyContainer).every(node=>node.innerHTML===undefined));
f.server.record={...f.server.record,status:'running'};await f.controller.open(parent);
  assert.equal(f.timers.size,1);
f.controller.newThread();assert.equal(f.timers.size,0);
  assert.equal(f.controller.current(),null);
  assert.equal(f.options.questionInput.value,'');
await f.controller.open(parent);f.controller.destroy();assert.equal(f.timers.size,0);
assert.equal(f.options.datasetSelect.handlers.change.length,0);
""")


def test_explicitly_clearing_dataset_does_not_silently_reuse_active_source():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
f.options.datasetSelect.value='';f.options.questionInput.value=
  'A different dataset question';
await f.controller.submit();
const create=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.deepEqual(create.body,{
  question:'A different dataset question',proposal_mode:'agent',parent_investigation:
    parent
});
assert.equal(f.options.questionInput.value,'');
""")


def test_late_open_response_cannot_replace_a_new_thread_or_another_selected_record():
    run_js(r"""
const f=fixture();await f.controller.ready;let release;
const waiting=new Promise(resolve=>{release=resolve;});
f.server.request=async()=>{await waiting;return f.server.record;};
const opening=f.controller.open(parent);f.controller.newThread();release();
  await opening;
assert.equal(f.controller.current(),null);assert.equal(f.jobs.length,0);
""")


def test_error_preserves_draft_and_recovers_current_revision_without_retrying():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
let posts=0;f.server.request=async(path,body)=>{if(body){posts++;
  throw Error('Revision changed; review the current record.');
  }return {...f.server.record,revision:8};};
await assert.rejects(f.controller.review({reviewer:'Operator',decision:'approve',
  rationale:'Reviewed'}),/Revision changed/);
assert.equal(posts,1);assert.equal(f.controller.current().revision,8);
assert.equal(f.options.submitButton.disabled,false);
""")


def test_composer_distinguishes_model_access_and_allows_recorded_selection():
    run_js(r"""
const f=fixture({config:{model_access:false,proposal_selection:{mode:'codex',available:
  true}}});await f.controller.ready;
assert.equal(f.options.modeSelect.children.find(node=>node.value==='agent').disabled,
  true);
f.options.datasetSelect.value=source;f.options.modeSelect.value='agent';
  f.options.questionInput.value='Inspect QC';
const before=f.calls.length;await f.controller.submit();
  assert.equal(f.calls.length,before);
assert.match(f.statuses.at(-1).message,/Connect a model/);
const config={model_access:false,proposal_selection:{mode:'recorded_fixture',available:
  true}};
const recorded=fixture({config});await recorded.controller.ready;
assert.equal(recorded.options.modeSelect.children.find(node=>
  node.value==='agent').disabled,false);
recorded.options.datasetSelect.value=source;
recorded.options.questionInput.value='Use the recorded selector';
await recorded.controller.submit();
const create=recorded.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.equal(create.body.proposal_mode,'agent');
""")


def test_run_switch_keeps_an_active_drafts_dataset_instead_of_silently_rebinding_it():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
f.options.questionInput.value='Question still being edited for A';
f.server.record={...f.server.record,id:child,source:'/approved/dataset-b.h5ad'};
await f.controller.open(child);
assert.equal(f.controller.current().source,'/approved/dataset-b.h5ad');
assert.equal(f.options.datasetSelect.value,source);
  assert.equal(f.options.questionInput.value,'Question still being edited for A');
await f.controller.submit();const request=f.calls.find(call=>
  call.path==='/api/investigations'&&call.body);
assert.equal(request.body.source,source);
  assert.equal(request.body.parent_investigation,parent);
""")


def test_new_selection_added_while_submission_is_pending_is_not_discarded():
    run_js(r"""
const f=fixture();await f.controller.ready;f.options.datasetSelect.value=source;
  f.options.questionInput.value='Inspect cells';
let release;const waiting=new Promise(resolve=>{release=resolve;});
f.server.request=async(path,body)=>{if(path==='/api/investigations'&&body){
  await waiting;return {...f.server.record,question:body.question};
  }if(path==='/api/investigations')return {investigations:[]};throw Error(path);};
const pending=f.controller.submit();f.controller.setSelection({figure_id:'qc',panel_id:
  'x',observation_ids:['later-cell'],context:{job_id:'c'.repeat(32),parent_root:
  'd'.repeat(64)},display_only:true});
release();await pending;assert.equal(f.options.selectionHost.hidden,false);
assert.ok(walk(f.options.selectionHost).some(node=>
  node.textContent==='Selected cells (1)'));
""")


def test_thread_entry_uses_injected_view_transition_but_same_record_poll_does_not():
    run_js(r"""
let transitions=0;const f=fixture({transition:async callback=>{transitions++;
  return callback();}});await f.controller.ready;
await f.controller.open(parent);assert.equal(transitions,1);
await f.controller.refresh();assert.equal(transitions,1);
""")


def test_scientific_gate_preserves_nested_revision_and_explicit_operator_review():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
const request={action:'declare_trial',expected_revision:27,session_id:
  'session-retained',actor:'Operator',allow_data_execution:false};
const operatorReview={reviewer:'Operator',rationale:'Reviewed the retained trial plan',
  confirmations:{scope:true,limits:true}};
const before=JSON.stringify({request,operatorReview});
await f.controller.handleAction({type:'scientific',request,operatorReview});
const sent=f.calls.find(call=>call.body?.action==='scientific');
assert.equal(sent.path,'/api/investigations/'+parent);
  assert.equal(sent.body.expected_revision,1);
assert.deepEqual(sent.body.request,request);
  assert.deepEqual(sent.body.operator_review,operatorReview);
assert.equal(sent.body.request.expected_revision,27);
  assert.equal(sent.body.request.allow_data_execution,false);
assert.equal(JSON.stringify({request,operatorReview}),before);
assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
""")


def test_scientific_clarification_stays_separate_from_biological_declarations():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
await f.controller.handleAction({type:'clarify',expectedRevision:999,declarations:{
  capture_unit_key:'capture'},scientificContext:{data_classification:'synthetic',
  allow_model_execution:false,auto_advance:false}});
const sent=f.calls.find(call=>call.body?.action==='clarify');
  assert.equal(sent.body.expected_revision,1);
assert.deepEqual(sent.body.declarations,{capture_unit_key:'capture'});
assert.deepEqual(sent.body.scientific_context,{data_classification:'synthetic',
  allow_model_execution:false,auto_advance:false});
assert.ok(!Object.hasOwn(sent.body.declarations,'allow_model_execution'));
""")


def test_actual_poll_updates_history_badge_and_activity_callback_without_a_write():
    run_js(r"""
const observed=[];const f=fixture({onHistory:rows=>observed.push(rows)});
  await f.controller.ready;
f.server.history=[{id:parent,source,question:'Inspect QC',status:'preparing',job_id:
  'c'.repeat(32)}];
f.server.record={...f.server.record,status:'preparing',job_id:'c'.repeat(32),job_state:{
  status:'preparing'}};
await f.controller.reloadHistory();await f.controller.open(parent);
assert.ok(walk(f.options.historyContainer).some(node=>node.textContent==='Preparing'));
const writes=f.calls.filter(call=>call.body!==undefined).length;
f.server.record={...f.server.record,revision:2,status:'completed_review_passed',
  job_state:{status:'completed_review_passed'}};
await f.controller.refresh();
assert.ok(walk(f.options.historyContainer).some(node=>
  node.textContent==='Completed review passed'));
assert.ok(!walk(f.options.historyContainer).some(node=>node.textContent==='Preparing'));
assert.equal(observed.at(-1).find(row=>row.id===parent).status,
  'completed_review_passed');
assert.equal(observed.at(-1).find(row=>row.id===parent).job_id,'c'.repeat(32));
assert.equal(f.calls.filter(call=>call.body!==undefined).length,writes);
const callbacks=observed.length;await f.controller.refresh();
  assert.equal(observed.length,callbacks);
""")


def test_resume_of_episode_handoff_reopens_answer_without_an_empty_write():
    run_js(r"""
const f=fixture();await f.controller.ready;
f.server.record={...f.server.record,status:'cancelled',job_id:'c'.repeat(32),job_state:{
  operation_kind:'agentic_episode',status:'cancelled',handoff:{root_hash:'retained'}}};
const answer=new Element('textarea');f.options.conversationContainer.querySelector=
  selector=>selector==='[name="episode_answer"]'?answer:null;
await f.controller.open(parent);const before=f.calls.filter(call=>
  call.body!==undefined).length;await f.controller.handleAction({type:'resume'});
assert.equal(f.calls.filter(call=>call.body!==undefined).length,before);
  assert.equal(answer.focused,true);assert.match(f.statuses.at(-1).message,/answer/i);
""")
