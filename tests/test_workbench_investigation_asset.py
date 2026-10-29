"""Investigation coordinator boundaries using an isolated native-DOM harness."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest


MODULE = Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-investigations.js"


def run_js(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the isolated investigation UI checks")
    setup = r"""
const assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.handlers={};this.value='';this.hidden=false;this.disabled=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs[key]=String(value);}
  addEventListener(event,fn){(this.handlers[event]||(this.handlers[event]=[])).push(fn);}
  removeEventListener(event,fn){this.handlers[event]=(this.handlers[event]||[]).filter(item=>item!==fn);}
  focus(){this.focused=true;}
}
const document={createElement:tag=>new Element(tag)};
const walk=node=>[node,...node.children.flatMap(walk)];
const parent='a'.repeat(32),child='b'.repeat(32),source='/approved/cells.h5ad';
const profile={source,sha256:'f'.repeat(64),observed:{cells:80,genes:60,obs_columns:['capture','batch'],layers:['counts'],obsm_keys:[]},declarations:{},unknowns:['Experimental assay is unconfirmed.']};
function fixture(extra={}) {
  const calls=[],timers=new Map(),statuses=[],gates=[],jobs=[],views=[];let timerId=0,renders=0;
  const server={history:[],record:{id:parent,revision:1,status:'awaiting_information',question:'Inspect QC',source,profile,clarification_fields:[],turns:[],job_id:null}};
  const options={document,config:{model_access:true,proposal_selection:{mode:'codex',available:true}},questionInput:new Element('textarea'),datasetSelect:new Element('select'),modeSelect:new Element('select'),conversationContainer:new Element('div'),historyContainer:new Element('div'),inputsContainer:new Element('div'),selectionHost:new Element('div'),submitButton:new Element('button'),
    setTimeout(fn){timers.set(++timerId,fn);return timerId;},clearTimeout(id){timers.delete(id);},
    onStatus(message,error){statuses.push({message,error});},onGate(gate){gates.push(gate);},onJob(id){jobs.push(id);},onCanvas(view){views.push(view);},
    conversation:{renderInvestigation(container,value){renders++;const p=new Element('p');p.textContent=value.investigation?.question||'Ask a question';container.replaceChildren(p);return {inlineGate:{kind:value.investigation?.status==='awaiting_information'?'clarification':value.investigation?.status==='awaiting_operator_review'?'review':value.investigation?.status==='approved'?'run':null}};}},
    async request(path,body){calls.push({path,body:body===undefined?undefined:JSON.parse(JSON.stringify(body))});
      if(server.request)return server.request(path,body);
      if(path==='/api/inputs')return {inputs:[{source,filename:'cells.h5ad',size_bytes:1024,hash_status:'pending'}]};
      if(path==='/api/inputs/inspect')return profile;
      if(path==='/api/investigations'&&body===undefined)return {investigations:server.history};
      if(path==='/api/investigations'&&body){server.record={...server.record,question:body.question,source:body.source};return JSON.parse(JSON.stringify(server.record));}
      if(path==='/api/investigations/'+parent||path==='/api/investigations/'+child){
        if(body?.action==='follow_up'){server.record={...server.record,id:child,revision:0,status:'awaiting_information',question:body.question,job_id:null};}
        else if(body?.action==='review')server.record={...server.record,revision:server.record.revision+1,status:'approved'};
        else if(body?.action==='run')server.record={...server.record,revision:server.record.revision+1,status:'running'};
        else if(body?.action==='clarify')server.record={...server.record,revision:server.record.revision+1,declarations:body.declarations,status:'awaiting_operator_review'};
        return JSON.parse(JSON.stringify(server.record));
      }
      throw Error('Unexpected route '+path);
    },...extra};
  const controller=globalThis.WorkbenchInvestigations.mount(options);
  return {controller,options,calls,timers,statuses,gates,jobs,views,server,renders:()=>renders};
}
"""
    code = "require(" + json.dumps(str(MODULE)) + ");\n" + setup
    code += "\n(async()=>{\n" + scenario + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    result = subprocess.run([node, "-"], input=code, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_discovery_inspection_and_clarification_are_read_only_until_explicit_submit():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.deepEqual(f.calls.map(call=>call.path).sort(),['/api/inputs','/api/investigations']);
assert.equal(f.options.questionInput.value,'');assert.equal(f.controller.current(),null);
f.options.datasetSelect.value=source;await f.controller.inspect(source);
assert.deepEqual(f.calls.at(-1),{path:'/api/inputs/inspect',body:{source}});
assert.ok(walk(f.options.inputsContainer).some(node=>node.textContent==='capture, batch'));
f.options.questionInput.value='Inspect the saved QC';await f.controller.submit();
const create=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.deepEqual(create.body,{question:'Inspect the saved QC',source,proposal_mode:'agent'});
assert.equal(f.options.questionInput.value,'');assert.equal(f.controller.current().status,'awaiting_information');
assert.equal(f.gates.at(-1).kind,'clarification');
assert.ok(!f.calls.some(call=>['review','run'].includes(call.body?.action)));
""")


def test_submission_preserves_a_draft_edited_during_the_request():
    run_js(r"""
const f=fixture();await f.controller.ready;f.options.datasetSelect.value=source;f.options.questionInput.value='Submitted question';
let release;const waiting=new Promise(resolve=>{release=resolve;});
f.server.request=async(path,body)=>{if(path==='/api/investigations'&&body){await waiting;return {...f.server.record,question:body.question};}if(path==='/api/investigations')return {investigations:[]};throw Error(path);};
const pending=f.controller.submit();assert.equal(f.options.submitButton.disabled,true);f.options.questionInput.value='My next unsent edit';release();await pending;
assert.equal(f.options.questionInput.value,'My next unsent edit');assert.equal(f.options.submitButton.disabled,false);
""")


def test_followup_creates_a_separate_plan_with_source_context_and_fresh_review():
    run_js(r"""
const f=fixture();await f.controller.ready;f.server.record={...f.server.record,revision:4,status:'completed_review_passed',job_id:'c'.repeat(32),job_state:{status:'completed_review_passed'}};
await f.controller.open(parent);f.options.questionInput.value='Compare the observed batch structure';await f.controller.submit();
const follow=f.calls.find(call=>call.body?.action==='follow_up');assert.equal(follow.path,'/api/investigations/'+parent);
assert.equal(follow.body.expected_revision,4);assert.equal(follow.body.question,'Compare the observed batch structure');
assert.equal(f.controller.current().id,child);assert.equal(f.controller.current().status,'awaiting_information');
assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
""")


def test_exact_selection_is_a_removable_chip_and_never_inserted_into_the_question():
    run_js(r"""
const f=fixture();await f.controller.ready;f.options.datasetSelect.value=source;f.options.questionInput.value='Inspect these cells';
const original={figure_id:'uncorrected',panel_id:'observed_batch',observation_ids:['cell2','cell9'],context:{job_id:'c'.repeat(32),parent_root:'d'.repeat(64)},display_only:true};
const attached=f.controller.setSelection(original);original.observation_ids.push('mutated');
assert.deepEqual(attached.observation_ids,['cell2','cell9']);assert.equal(f.options.questionInput.value,'Inspect these cells');
assert.ok(walk(f.options.selectionHost).some(node=>node.textContent==='Selected cells (2)'));
await f.controller.submit();const create=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.deepEqual(create.body.selection.observation_ids,['cell2','cell9']);assert.equal(create.body.selection.source_job,'c'.repeat(32));
assert.equal(create.body.parent_job,'c'.repeat(32));assert.equal(f.options.selectionHost.hidden,true);
f.controller.setSelection({...original,observation_ids:['other']});
const remove=walk(f.options.selectionHost).find(node=>node.attrs['aria-label']==='Remove selected-cell context');remove.handlers.click[0]();
assert.equal(f.options.selectionHost.hidden,true);
""")


def test_oversized_selection_is_refused_without_truncation_or_submitting():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.equal(f.controller.setSelection({figure_id:'qc',panel_id:'x',observation_ids:Array.from({length:1001},(_,i)=>'c'+i),context:{job_id:'c'.repeat(32),parent_root:'d'.repeat(64)},display_only:true}),null);
assert.match(f.statuses.at(-1).message,/1,000/);assert.equal(f.calls.filter(call=>call.body).length,0);
assert.throws(()=>globalThis.WorkbenchInvestigations.normalizeSelection({observation_ids:['a','a'],display_only:true}),/unique/);
""")


def test_only_explicit_review_and_run_actions_mutate_authority():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
await f.controller.handleAction({type:'clarify',declarations:{assay:'scrna',capture_unit_key:'capture',capture_units_confirmed:true}});
const clarification=f.calls.find(call=>call.body?.action==='clarify');assert.equal(clarification.body.expected_revision,1);
assert.equal(clarification.body.declarations.capture_unit_key,'capture');assert.equal(f.controller.current().status,'awaiting_operator_review');
await f.controller.review({reviewer:'Operator',decision:'approve',rationale:'Reviewed',confirmations:{scope:true}});
assert.equal(f.controller.current().status,'approved');assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
await assert.rejects(f.controller.run({allow_data_execution:false}),/explicit run action/);
await f.controller.run({allow_data_execution:true});const run=f.calls.find(call=>call.body?.action==='run');
assert.equal(run.body.allow_data_execution,true);assert.equal(run.body.expected_revision,3);assert.equal(f.controller.current().status,'running');
""")


def test_polling_preserves_unchanged_focus_and_does_not_reopen_same_job():
    run_js(r"""
const f=fixture();await f.controller.ready;f.server.record={...f.server.record,status:'running',job_id:'c'.repeat(32),job_state:{status:'running'}};
await f.controller.open(parent);const rendered=f.renders();assert.equal(f.jobs.length,1);assert.equal(f.timers.size,1);
await f.controller.refresh();assert.equal(f.renders(),rendered);assert.equal(f.jobs.length,1);
f.server.record={...f.server.record,revision:2,status:'completed_review_passed',job_state:{status:'completed_review_passed'}};
await f.controller.refresh();assert.ok(f.renders()>rendered);assert.equal(f.timers.size,0);assert.equal(f.jobs.length,1);
""")


def test_history_and_profile_text_are_safe_and_destruction_stops_polling():
    run_js(r"""
const f=fixture();await f.controller.ready;f.server.history=[{id:parent,source,question:'<script>unsafe</script>',status:'running'}];await f.controller.reloadHistory();
assert.ok(walk(f.options.historyContainer).some(node=>node.textContent==='<script>unsafe</script>'));
assert.ok(walk(f.options.historyContainer).every(node=>node.innerHTML===undefined));
f.server.record={...f.server.record,status:'running'};await f.controller.open(parent);assert.equal(f.timers.size,1);
f.controller.newThread();assert.equal(f.timers.size,0);assert.equal(f.controller.current(),null);assert.equal(f.options.questionInput.value,'');
await f.controller.open(parent);f.controller.destroy();assert.equal(f.timers.size,0);
assert.equal(f.options.datasetSelect.handlers.change.length,0);
""")


def test_explicitly_clearing_dataset_does_not_silently_reuse_active_source():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
f.options.datasetSelect.value='';f.options.questionInput.value='A different dataset question';
const before=f.calls.length;await f.controller.submit();assert.equal(f.calls.length,before);
assert.match(f.statuses.at(-1).message,/Choose a dataset/);
assert.equal(f.options.questionInput.value,'A different dataset question');
""")


def test_late_open_response_cannot_replace_a_new_thread_or_another_selected_record():
    run_js(r"""
const f=fixture();await f.controller.ready;let release;
const waiting=new Promise(resolve=>{release=resolve;});
f.server.request=async()=>{await waiting;return f.server.record;};
const opening=f.controller.open(parent);f.controller.newThread();release();await opening;
assert.equal(f.controller.current(),null);assert.equal(f.jobs.length,0);
""")


def test_error_does_not_clear_draft_and_recovers_current_revision_without_retrying_action():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
let posts=0;f.server.request=async(path,body)=>{if(body){posts++;throw Error('Revision changed; review the current record.');}return {...f.server.record,revision:8};};
await assert.rejects(f.controller.review({reviewer:'Operator',decision:'approve',rationale:'Reviewed'}),/Revision changed/);
assert.equal(posts,1);assert.equal(f.controller.current().revision,8);
assert.equal(f.options.submitButton.disabled,false);
""")


def test_new_chat_requires_a_live_model_and_never_treats_recorded_selector_as_live():
    run_js(r"""
const f=fixture({config:{model_access:false,proposal_selection:{mode:'codex',available:true}}});await f.controller.ready;
assert.equal(f.options.modeSelect.children.find(node=>node.value==='agent').disabled,true);
f.options.datasetSelect.value=source;f.options.modeSelect.value='agent';f.options.questionInput.value='Inspect QC';
const before=f.calls.length;await f.controller.submit();assert.equal(f.calls.length,before);
assert.match(f.statuses.at(-1).message,/Connect a model/);
const recorded=fixture({config:{model_access:false,proposal_selection:{mode:'recorded_fixture',available:true}}});await recorded.controller.ready;
assert.equal(recorded.options.modeSelect.children.find(node=>node.value==='agent').disabled,true);
recorded.options.datasetSelect.value=source;recorded.options.questionInput.value='Ask a real model';
const recordedBefore=recorded.calls.length;await recorded.controller.submit();assert.equal(recorded.calls.length,recordedBefore);
""")


def test_run_switch_keeps_an_active_drafts_dataset_instead_of_silently_rebinding_it():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
f.options.questionInput.value='Question still being edited for A';
f.server.record={...f.server.record,id:child,source:'/approved/dataset-b.h5ad'};
await f.controller.open(child);
assert.equal(f.controller.current().source,'/approved/dataset-b.h5ad');
assert.equal(f.options.datasetSelect.value,source);assert.equal(f.options.questionInput.value,'Question still being edited for A');
await f.controller.submit();const request=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.equal(request.body.source,source);assert.equal(request.body.parent_investigation,parent);
""")


def test_new_selection_added_while_submission_is_pending_is_not_discarded():
    run_js(r"""
const f=fixture();await f.controller.ready;f.options.datasetSelect.value=source;f.options.questionInput.value='Inspect cells';
let release;const waiting=new Promise(resolve=>{release=resolve;});
f.server.request=async(path,body)=>{if(path==='/api/investigations'&&body){await waiting;return {...f.server.record,question:body.question};}if(path==='/api/investigations')return {investigations:[]};throw Error(path);};
const pending=f.controller.submit();f.controller.setSelection({figure_id:'qc',panel_id:'x',observation_ids:['later-cell'],context:{job_id:'c'.repeat(32),parent_root:'d'.repeat(64)},display_only:true});
release();await pending;assert.equal(f.options.selectionHost.hidden,false);
assert.ok(walk(f.options.selectionHost).some(node=>node.textContent==='Selected cells (1)'));
""")


def test_thread_entry_uses_injected_view_transition_but_same_record_poll_does_not():
    run_js(r"""
let transitions=0;const f=fixture({transition:async callback=>{transitions++;return callback();}});await f.controller.ready;
await f.controller.open(parent);assert.equal(transitions,1);
await f.controller.refresh();assert.equal(transitions,1);
""")


def test_scientific_gate_dispatch_preserves_nested_revision_and_explicit_operator_review():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
const request={action:'declare_trial',expected_revision:27,session_id:'session-retained',actor:'Operator',allow_data_execution:false};
const operatorReview={reviewer:'Operator',rationale:'Reviewed the retained trial plan',confirmations:{scope:true,limits:true}};
const before=JSON.stringify({request,operatorReview});
await f.controller.handleAction({type:'scientific',request,operatorReview});
const sent=f.calls.find(call=>call.body?.action==='scientific');
assert.equal(sent.path,'/api/investigations/'+parent);assert.equal(sent.body.expected_revision,1);
assert.deepEqual(sent.body.request,request);assert.deepEqual(sent.body.operator_review,operatorReview);
assert.equal(sent.body.request.expected_revision,27);assert.equal(sent.body.request.allow_data_execution,false);
assert.equal(JSON.stringify({request,operatorReview}),before);
assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
""")


def test_scientific_clarification_stays_separate_from_biological_declarations():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
await f.controller.handleAction({type:'clarify',expectedRevision:999,declarations:{capture_unit_key:'capture'},scientificContext:{data_classification:'synthetic',allow_model_execution:false,auto_advance:false}});
const sent=f.calls.find(call=>call.body?.action==='clarify');assert.equal(sent.body.expected_revision,1);
assert.deepEqual(sent.body.declarations,{capture_unit_key:'capture'});
assert.deepEqual(sent.body.scientific_context,{data_classification:'synthetic',allow_model_execution:false,auto_advance:false});
assert.ok(!Object.hasOwn(sent.body.declarations,'allow_model_execution'));
""")


def test_actual_poll_updates_history_badge_and_activity_callback_without_a_write():
    run_js(r"""
const observed=[];const f=fixture({onHistory:rows=>observed.push(rows)});await f.controller.ready;
f.server.history=[{id:parent,source,question:'Inspect QC',status:'preparing',job_id:'c'.repeat(32)}];
f.server.record={...f.server.record,status:'preparing',job_id:'c'.repeat(32),job_state:{status:'preparing'}};
await f.controller.reloadHistory();await f.controller.open(parent);
assert.ok(walk(f.options.historyContainer).some(node=>node.textContent==='Preparing'));
const writes=f.calls.filter(call=>call.body!==undefined).length;
f.server.record={...f.server.record,revision:2,status:'completed_review_passed',job_state:{status:'completed_review_passed'}};
await f.controller.refresh();
assert.ok(walk(f.options.historyContainer).some(node=>node.textContent==='Completed review passed'));
assert.ok(!walk(f.options.historyContainer).some(node=>node.textContent==='Preparing'));
assert.equal(observed.at(-1).find(row=>row.id===parent).status,'completed_review_passed');
assert.equal(observed.at(-1).find(row=>row.id===parent).job_id,'c'.repeat(32));
assert.equal(f.calls.filter(call=>call.body!==undefined).length,writes);
const callbacks=observed.length;await f.controller.refresh();assert.equal(observed.length,callbacks);
""")
