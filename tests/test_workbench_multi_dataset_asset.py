"""Ordered dataset attachments are explicit and never collapse into a first-file plan."""
from tests.test_workbench_investigation_asset import run_js
from tests.test_workbench_conversation_redesign import run_js as run_conversation

MULTI = r"""
const second='/approved/metadata.csv';
const profileFor=path=>({source:path,filename:path.split('/').pop(),sha256:(path===source?'a':'b').repeat(64),observed:path===source?{file_type:'h5ad',cells:80,genes:60,obs_columns:['donor_a'],layers:['counts']}:{file_type:'csv',columns:['sample','condition']},unknowns:[]});
async function multiFixture(){
 const f=fixture({inputTrigger:new Element('button'),inputPickerList:new Element('div'),inputChips:new Element('div')});await f.controller.ready;
 f.server.request=async(path,body)=>{
  if(path==='/api/inputs')return {inputs:[source,second].map(path=>({source:path,filename:path.split('/').pop(),sha256:profileFor(path).sha256}))};
  if(path==='/api/inputs/inspect')return profileFor(body.source);
  if(path==='/api/investigations'&&body===undefined)return {investigations:[]};
  if(path==='/api/investigations'&&body){const paths=body.sources||[body.source];f.server.record={id:parent,revision:1,status:'awaiting_information',question:body.question,source:paths[0],sources:paths,source_profiles:paths.map(profileFor),source_declarations:paths.map(()=>({})),profile:paths.length>1?{filename:paths.length+' datasets',observed:{file_type:'input_set',sources:paths.map(path=>profileFor(path))}}:profileFor(paths[0]),turns:[],job_id:null};return JSON.parse(JSON.stringify(f.server.record));}
  if(path==='/api/investigations/'+parent||path==='/api/investigations/'+child){
   if(body?.action==='follow_up')f.server.record={...f.server.record,id:child,revision:0,question:body.question};
   if(body?.action==='clarify')f.server.record={...f.server.record,revision:f.server.record.revision+1};
   return JSON.parse(JSON.stringify(f.server.record));
  }
  throw Error(path);
 };
 await f.controller.reloadInputs();return f;
}
"""


def test_multiple_attachments_submit_the_exact_ordered_set_without_first_file_fallback():
    run_js(MULTI+r"""
const f=await multiFixture();f.controller.setSources([source,second]);
assert.deepEqual(f.controller.selectedSources(),[source,second]);assert.equal(f.options.datasetSelect.value,'');
assert.equal(f.options.inputTrigger.textContent,'2 datasets');
assert.equal(f.options.inputChips.children.length,2);
f.options.questionInput.value='Compare these data sources';await f.controller.submit();
const request=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.deepEqual(request.body.sources,[source,second]);assert.deepEqual(request.body.source_declarations,[{},{}]);
assert.ok(!('source' in request.body));assert.equal(request.body.proposal_mode,'agent');
assert.deepEqual(f.controller.selectedSources(),[source,second]);assert.equal(f.options.datasetSelect.value,'');
assert.ok(!f.calls.some(call=>['run','review'].includes(call.body?.action)));
""")


def test_picker_checkbox_adds_and_removable_chip_removes_only_the_named_file():
    run_js(MULTI+r"""
const f=await multiFixture();const checks=walk(f.options.inputPickerList).filter(node=>node.type==='checkbox');
checks[0].checked=true;checks[0].onchange();checks[1].checked=true;checks[1].onchange();
assert.deepEqual(f.controller.selectedSources(),[source,second]);
const remove=walk(f.options.inputChips).find(node=>node.attrs['aria-label']==='Remove cells.h5ad');remove.onclick();
assert.deepEqual(f.controller.selectedSources(),[second]);assert.equal(f.options.datasetSelect.value,second);
assert.equal(f.options.inputTrigger.focused,true);
assert.ok(!f.calls.some(call=>call.path==='/api/investigations'&&call.body));
""")


def test_maximum_and_duplicate_attachments_are_rejected_without_silent_truncation():
    run_js(MULTI+r"""
const f=await multiFixture();const paths=Array.from({length:8},(_,i)=>'/approved/'+i+'.h5ad');
f.controller.setSources(paths);assert.throws(()=>f.controller.setSources([...paths,'/approved/ninth.h5ad']),/eight distinct/);
assert.deepEqual(f.controller.selectedSources(),paths);
assert.throws(()=>f.controller.setSources([source,source]),/distinct/);assert.deepEqual(f.controller.selectedSources(),paths);
""")


def test_each_inspection_is_independent_and_an_older_response_cannot_replace_the_focused_file():
    run_js(MULTI+r"""
const f=await multiFixture();f.controller.setSources([source,second]);const original=f.server.request;let release;
f.server.request=async(path,body)=>{if(path==='/api/inputs/inspect'&&body.source===source){await new Promise(resolve=>release=resolve);return profileFor(source);}return original(path,body);};
const a=f.controller.inspect(source);await Promise.resolve();await f.controller.inspect(second);release();await a;
assert.deepEqual(f.controller.selectedSources(),[source,second]);assert.equal(f.options.datasetSelect.value,'');
assert.equal(f.calls.filter(call=>call.path==='/api/inputs/inspect').length,2);
const profiles=walk(f.options.inputsContainer).filter(node=>node.className==='dataset-profile');assert.equal(profiles.length,1);
assert.ok(walk(profiles[0]).some(node=>node.textContent==='metadata.csv'));
assert.ok(!walk(profiles[0]).some(node=>node.textContent===80));
const third='/approved/reference.json';f.server.request=async(path,body)=>path==='/api/inputs'?{inputs:[source,second,third].map(source=>({source}))}:original(path,body);
await f.controller.reloadInputs();const preview=walk(f.options.inputsContainer).find(node=>node.tagName==='button'&&node.attrs.title===third);
preview.handlers.click[0]();await new Promise(resolve=>setImmediate(resolve));
assert.deepEqual(f.controller.selectedSources(),[source,second]);assert.equal(f.options.datasetSelect.value,'');
""")


def test_followups_keep_all_sources_and_source_declarations_remain_independent():
    run_js(MULTI+r"""
const f=await multiFixture();f.controller.setSources([source,second]);f.options.questionInput.value='Initial question';await f.controller.submit();
await f.controller.handleAction({type:'clarify',source_declarations:[{role:'expression',assay:'scrna'},{role:'metadata'}]});
const clarify=f.calls.find(call=>call.body?.action==='clarify');assert.deepEqual(clarify.body.source_declarations,[{role:'expression',assay:'scrna'},{role:'metadata'}]);assert.ok(!('declarations' in clarify.body));
f.options.questionInput.value='Ask about correspondence';await f.controller.submit();
const follow=f.calls.find(call=>call.body?.action==='follow_up');assert.equal(follow.path,'/api/investigations/'+parent);
assert.ok(!('source' in follow.body));assert.ok(!('sources' in follow.body));
assert.deepEqual(f.controller.selectedSources(),[source,second]);
""")


def test_opening_another_chat_keeps_an_unsent_multi_file_draft_bound_to_its_original_set():
    run_js(MULTI+r"""
const f=await multiFixture();f.controller.setSources([source,second]);f.options.questionInput.value='Initial';await f.controller.submit();
f.options.questionInput.value='Unsent comparison';f.server.record={...f.server.record,id:child,source:'/approved/other.h5ad',sources:['/approved/other.h5ad']};
await f.controller.open(child);assert.deepEqual(f.controller.selectedSources(),[source,second]);assert.equal(f.options.questionInput.value,'Unsent comparison');
await f.controller.submit();const requests=f.calls.filter(call=>call.path==='/api/investigations'&&call.body);const last=requests.at(-1);
assert.deepEqual(last.body.sources,[source,second]);assert.equal(last.body.parent_investigation,parent);
""")


def test_cell_ids_are_not_assumed_to_span_multiple_attached_datasets():
    run_js(MULTI+r"""
const f=await multiFixture();f.controller.setSources([source,second]);
assert.equal(f.controller.setSelection({figure_id:'qc',panel_id:'x',observation_ids:['cell1'],source_job:'c'.repeat(32),parent_root:'d'.repeat(64),display_only:true}),null);
assert.equal(f.options.selectionHost.hidden,true);assert.ok(f.statuses.some(row=>row.error&&/one analysis/.test(row.message)));
assert.equal(f.calls.filter(call=>call.body?.action==='run').length,0);
""")


def test_restored_multi_file_draft_keeps_missing_chips_and_cannot_fall_back_to_one_file():
    run_js(MULTI+r"""
const f=fixture({initialSources:[source,second],inputTrigger:new Element('button'),inputPickerList:new Element('div'),inputChips:new Element('div')});
f.options.questionInput.value='Saved comparison';await f.controller.ready;
assert.deepEqual(f.controller.selectedSources(),[source,second]);assert.equal(f.options.datasetSelect.value,'');
assert.ok(walk(f.options.inputChips).some(node=>node.textContent==='metadata.csv · Unavailable'));
const before=f.calls.length;await f.controller.submit();assert.equal(f.calls.length,before);
assert.equal(f.options.questionInput.value,'Saved comparison');assert.ok(f.statuses.some(row=>row.error&&/unavailable in this project/.test(row.message)));
f.server.request=async(path)=>{if(path==='/api/inputs')return {inputs:[source,second].map(source=>({source}))};throw Error(path);};
await f.controller.reloadInputs();assert.deepEqual(f.controller.selectedSources(),[source,second]);
assert.ok(!walk(f.options.inputChips).some(node=>node.className?.includes('is-unavailable')));
""")


def test_multi_submit_requests_planning_to_review_without_model_or_execution_grants():
    run_js(MULTI+r"""
const f=await multiFixture();f.controller.setSources([source,second]);f.options.questionInput.value='Prepare a comparison plan';await f.controller.submit();
let request=f.calls.find(call=>call.path==='/api/investigations'&&call.body);
assert.deepEqual(request.body.scientific_context,{auto_advance:true});
assert.ok(!('allow_model_execution' in request.body.scientific_context));assert.ok(!('allow_data_execution' in request.body));
assert.ok(!('auto_synthesis' in request.body.scientific_context));
const explicit=await multiFixture();explicit.options.scientificContextProvider=()=>({auto_advance:false});explicit.controller.setSources([source,second]);explicit.options.questionInput.value='Keep manual planning';await explicit.controller.submit();
request=explicit.calls.find(call=>call.path==='/api/investigations'&&call.body);assert.deepEqual(request.body.scientific_context,{auto_advance:false});
""")


def test_changed_multi_set_does_not_claim_an_unrelated_parent_runs_dataset():
    run_js(MULTI+r"""
const f=await multiFixture(),old='/approved/previous.h5ad';
f.server.record={id:parent,revision:1,status:'completed_review_passed',question:'Older analysis',source:old,sources:[old],profile:profileFor(old),job_id:'c'.repeat(32),turns:[]};
await f.controller.open(parent);f.controller.setSources([source,second]);f.options.questionInput.value='Compare the newly attached files';await f.controller.submit();
const request=f.calls.find(call=>call.path==='/api/investigations'&&call.body);assert.equal(request.body.parent_investigation,parent);assert.ok(!('parent_job' in request.body));assert.deepEqual(request.body.sources,[source,second]);
""")


def test_read_only_project_explains_archive_without_posting_or_dropping_the_draft():
    run_js(r"""
const f=fixture({config:{project_catalog:{read_only:true},model_access:false,proposal_selection:{mode:'disabled',available:false}}});await f.controller.ready;
f.options.questionInput.value='Question to carry into a working project';f.options.datasetSelect.value=source;
const before=f.calls.length;await f.controller.submit();assert.equal(f.calls.length,before);
assert.equal(f.options.questionInput.value,'Question to carry into a working project');
assert.equal(f.statuses.at(-1).message,'Read-only project. Create or select a working project to start a chat.');
""")


NODES = r"""
class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;this.disabled=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
const walk=node=>[node,...node.children.flatMap(walk)];
const a='/approved/cells.h5ad',b='/approved/metadata.csv';
"""


def test_per_source_clarification_keeps_indexes_and_drafts_separate_without_global_declarations():
    run_conversation(NODES+r"""
const container=new Node('div'),actions=[];
const investigation={id:'i',revision:1,status:'awaiting_information',source:a,sources:[a,b],declarations:{assay:'must-not-copy'},source_declarations:[{},{}],turns:[],clarification_fields:[0,1].map(source_index=>({scope:'source_declarations',source_index,field:'role',label:(source_index===0?'cells.h5ad':'metadata.csv')+' · Role',type:'select',options:[{value:'expression',label:'Expression'},{value:'metadata',label:'Metadata'}]}))};
api.renderInvestigation(container,{investigation,config:{},onAction:action=>actions.push(action)});
let fields=walk(container).filter(node=>node.tagName==='select');assert.equal(fields.length,2);assert.notEqual(fields[0].attrs.id,fields[1].attrs.id);
fields[0].value='expression';fields[0].change();
api.renderInvestigation(container,{investigation,config:{},onAction:action=>actions.push(action)});
fields=walk(container).filter(node=>node.tagName==='select');assert.equal(fields[0].value,'expression');assert.equal(fields[1].value,'');
fields[1].value='metadata';walk(container).find(node=>node.tagName==='form').submit({preventDefault(){}});
assert.deepEqual(actions[0].source_declarations,[{role:'expression'},{role:'metadata'}]);assert.ok(!('declarations' in actions[0]));
assert.deepEqual(investigation.source_declarations,[{},{}]);
""")


def test_multi_file_audit_displays_each_observed_shape_without_inventing_a_combined_matrix():
    run_conversation(NODES+r"""
const sources=[{source:a,filename:'cells.h5ad',observed:{file_type:'h5ad',cells:80,genes:60}},{source:b,filename:'metadata.csv',observed:{file_type:'csv',columns:['sample','condition']}}];
const investigation={id:'i',revision:1,status:'awaiting_information',source:a,sources:[a,b],source_profiles:sources,profile:{filename:'2 datasets',observed:{file_type:'input_set',sources},unknowns:['Cross-file correspondence is not yet declared.']},turns:[]};
const before=JSON.stringify(investigation),container=new Node('div');api.renderInvestigation(container,{investigation,config:{}});const nodes=walk(container);
assert.ok(nodes.some(node=>node.textContent==='2 attached datasets · inspected separately'));
assert.ok(nodes.some(node=>node.textContent==='80 cells × 60 genes'));assert.ok(nodes.some(node=>node.textContent==='Csv file'));
assert.equal(nodes.filter(node=>node.className==='wc-source-audit').length,2);assert.equal(JSON.stringify(investigation),before);
assert.equal(investigation.profile.observed.cells,undefined);assert.equal(investigation.profile.observed.genes,undefined);
""")


def test_multi_source_followup_suggestions_attach_the_complete_set_not_legacy_first_path():
    run_conversation(NODES+r"""
const actions=[],container=new Node('div');
api.renderInvestigation(container,{investigation:{id:'i',revision:1,status:'abstained',source:a,sources:[a,b],turns:[],synthesis:{supported_findings:{},method_evidence_assessment:{},uncertainties:[]}},config:{},onAction:action=>actions.push(action)});
const button=walk(container).find(node=>node.tagName==='button'&&node.className?.includes('wc-suggestion'));button.click();
assert.deepEqual(actions[0].sources,[a,b]);assert.ok(!('input' in actions[0]));
""")


def test_requested_multi_planning_survives_fresh_sharing_consent_but_respects_unchecking():
    run_conversation(NODES+r"""
const container=new Node('div'),actions=[],investigation={id:'i',revision:2,status:'awaiting_information',source:a,sources:[a,b],source_declarations:[{},{}],turns:[],scientific_context:{auto_advance:true,data_classification:'unclassified',allow_model_execution:false},clarification_fields:[
{scope:'scientific_context',field:'data_classification',type:'select',options:[{value:'synthetic',label:'Synthetic'},{value:'private',label:'Private'}]},
{scope:'scientific_context',field:'allow_model_execution',type:'boolean'},
{scope:'scientific_context',field:'auto_advance',type:'boolean'}]};
api.renderInvestigation(container,{investigation,config:{scientific:{model_execution_allowed:true,scientific_workflow_configured:true}},onAction:action=>actions.push(action)});
const nodes=walk(container),classification=nodes.find(node=>node.attrs.name==='data_classification'),grant=nodes.find(node=>node.attrs.name==='allow_model_execution'),advance=nodes.find(node=>node.attrs.name==='auto_advance'),form=nodes.find(node=>node.tagName==='form');
assert.equal(grant.checked,false);assert.equal(advance.disabled,true);assert.equal(advance.checked,true);
form.submit({preventDefault(){}});assert.equal(actions[0].scientificContext.allow_model_execution,false);assert.equal(actions[0].scientificContext.auto_advance,true);
classification.value='synthetic';classification.change();assert.equal(grant.checked,false);grant.checked=true;grant.change();
assert.equal(advance.disabled,false);assert.equal(advance.checked,true);
form.submit({preventDefault(){}});assert.equal(actions[1].scientificContext.allow_model_execution,true);assert.equal(actions[1].scientificContext.auto_advance,true);assert.ok(!('allow_data_execution' in actions[1]));
advance.checked=false;advance.change();form.submit({preventDefault(){}});assert.equal(actions[2].scientificContext.auto_advance,false);
""")
