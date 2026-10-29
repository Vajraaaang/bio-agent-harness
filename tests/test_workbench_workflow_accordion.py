"""Five scientific questions remain ordered, real, and locally expandable."""
from tests.test_workbench_conversation_redesign import run_js


SETUP = r"""
class Node {
  constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.open=false;this.value='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this.attrs[key]=String(value);}
  getAttribute(key){return this.attrs[key];}
  addEventListener(key,fn){this[key]=fn;}
}
globalThis.document={createElement:tag=>new Node(tag),createElementNS:(_ns,tag)=>new Node(tag)};
const walk=n=>[n,...n.children.flatMap(walk)];
const byClass=(node,name)=>walk(node).filter(n=>(n.className||'').split(' ').includes(name));
const container=new Node('div'),actions=[];
const sections=()=>byClass(container,'wc-workflow-section');
const render=investigation=>api.renderInvestigation(container,{investigation,config:{execution_allowed:true},onAction:value=>actions.push(value)});
"""


def test_one_compact_card_has_five_ordered_questions_without_fabricated_steps():
    run_js(SETUP+r"""
const investigation={id:'chat',revision:1,status:'awaiting_information',question:'Inspect the input',turns:[{id:'q',kind:'user_question',role:'user',step:2,content:{question:'Inspect the input'}},{id:'a',kind:'data_audit',step:1,content:{observed:{cells:80},declarations:{},inferred:{},unknowns:[]}}]};
const before=JSON.stringify(investigation);render(investigation);
assert.equal(byClass(container,'wc-workflow-card').length,1);
assert.deepEqual(sections().map(n=>n.attrs['data-scientific-step']),['1','2','3','4','5']);
assert.deepEqual(byClass(container,'wc-workflow-label').map(n=>n.textContent),['Data','Question','Methods','Results','Conclusion']);
assert.ok(sections()[0].children[0].attrs['aria-label'].includes('What data do I have?'));
assert.ok(!walk(container).some(n=>new RegExp('^0[1-5] / ').test(n.textContent||'')));
assert.ok(!walk(container).some(n=>n.textContent==='Completed'));
assert.equal(JSON.stringify(investigation),before);assert.equal(actions.length,0);
""")


def test_gate_section_opens_once_and_manual_expansion_survives_polling():
    run_js(SETUP+r"""
const investigation={id:'chat',revision:1,status:'awaiting_operator_review',job_id:'job',job_state:{status:'awaiting_operator_review',proposal:{}},turns:[]};
render(investigation);assert.deepEqual(sections().map(n=>n.open),[false,false,true,false,false]);
assert.ok(walk(sections()[2]).some(n=>n.attrs['data-inline-gate']==='review'));
sections()[2].open=false;sections()[0].open=true;
render({...investigation,revision:2});
assert.deepEqual(sections().map(n=>n.open),[true,false,false,false,false]);
render({...investigation,revision:3,status:'approved',job_state:{status:'approved',proposal:{}}});
assert.equal(sections()[3].open,true);
assert.ok(walk(sections()[3]).some(n=>n.attrs['data-inline-gate']==='run'));
assert.equal(actions.length,0);
""")


def test_clarification_draft_survives_section_changes_and_new_fields_reveal_gate():
    run_js(SETUP+r"""
const field={field:'count_provenance',type:'text',label:'Provenance',required:true};
const investigation={id:'chat',revision:1,status:'awaiting_information',question:'QC',clarification_fields:[field],turns:[]};
render(investigation);let input=walk(container).find(n=>n.attrs.id==='clarify-chat-count_provenance');
input.value='A retained experimental declaration';input.input();sections()[0].open=false;sections()[1].open=true;
render({...investigation,revision:2});
input=walk(container).find(n=>n.attrs.id==='clarify-chat-count_provenance');
assert.equal(input.value,'A retained experimental declaration');assert.equal(sections()[0].open,false);assert.equal(sections()[1].open,true);
render({...investigation,revision:3,clarification_fields:[field,{field:'assay',type:'text',required:true}]});
assert.equal(sections()[0].open,true);assert.equal(actions.length,0);
""")


def test_ancestor_context_stays_collapsed_and_never_receives_current_profile():
    run_js(SETUP+r"""
render({id:'child',revision:1,status:'awaiting_operator_review',question:'Current question',job_id:'j',job_state:{status:'awaiting_operator_review'},profile:{observed:{cells:90},declarations:{count_provenance:'Current declaration'}},thread_turns:[{id:'parent-question',investigation_id:'parent',role:'user',kind:'user_question',step:2,content:{question:'Earlier question text'}},{id:'parent-audit',investigation_id:'parent',kind:'data_audit',step:1,content:{observed:{cells:20},declarations:{count_provenance:'Earlier declaration'},inferred:{},unknowns:[]}},{id:'current-question',investigation_id:'child',role:'user',kind:'user_question',step:2,content:{question:'Current question'}}]});
const parent=walk(container).find(n=>n.attrs['data-parent-investigation']==='parent');assert.equal(parent.open,false);
assert.ok(walk(parent).some(n=>n.textContent==='Earlier declaration'));assert.ok(!walk(parent).some(n=>n.textContent==='Current declaration'));
const current=walk(container).find(n=>n.attrs['data-workflow-id']==='child');
assert.ok(walk(current).some(n=>n.textContent==='Current declaration'));assert.ok(!walk(current).some(n=>n.textContent==='Earlier declaration'));
assert.equal(actions.length,0);
""")


def test_legacy_record_uses_the_same_shape_and_actions_remain_navigation_only():
    run_js(SETUP+r"""
api.render(container,{state:{id:'legacy',question:'Saved analysis',status:'approved',proposal:{external_input:{path:'/approved/a.h5ad'},preview:{cells:80,genes:60},configuration:{parameters:{workflow:'scrna_qc_integration_annotation/1'}}}},config:{execution_allowed:true},onAction:value=>actions.push(value)});
assert.equal(byClass(container,'wc-workflow-card').length,1);assert.equal(sections().length,5);assert.equal(sections()[3].open,true);
assert.ok(walk(container).some(n=>n.textContent==='Saved analysis'));
const action=walk(sections()[3]).find(n=>n.tagName==='button'&&typeof n.click==='function');assert.ok(action);action.click();
assert.equal(actions.length,1);assert.equal(actions[0].type,'navigate');assert.equal(actions[0].view,'execute');
""")


def test_only_the_actual_active_lifecycle_section_spins_and_terminal_states_stop():
    run_js(SETUP+r"""
const active = {queued:1,inspecting:1,planning:3,recording_review:3,preparing:4,running:4,cancel_requested:4,finalizing:4};
for (const [status,step] of Object.entries(active)) {
  render({id:'chat',revision:1,status,turns:[{id:'old',kind:'data_audit',step:1,content:{observed:{cells:80}}}]});
  const busy=byClass(container,'is-running');assert.equal(busy.length,1,status);
  assert.equal(busy[0].attrs['data-scientific-step'],String(step),status);
  assert.equal(busy[0].attrs['data-workflow-state'],'running');
  assert.ok(busy[0].children[0].attrs['aria-label'].includes(api.buildModel({status},{}).summary.title));
  assert.equal(byClass(container,'is-attention').length,0);
}
for(const status of ['completed_review_passed','completed_presentation','completed','abstained','', 'ready']) {
  render({id:'chat',revision:2,status,...(status.startsWith('completed') || status==='abstained' ? {scientific_session_id:'s',gate:'review'} : {}),turns:[{id:'done',kind:'scientific_operation',step:4,content:{operation:{status:'running',action:'run_trial'}}}]});
  assert.equal(byClass(container,'is-running').length,0,status);assert.equal(byClass(container,'is-attention').length,0,status);
}
assert.equal(actions.length,0);
""")


def test_attention_is_distinct_from_running_for_required_input_reviews_and_failure():
    run_js(SETUP+r"""
for(const status of ['awaiting_information','needs_information','awaiting_operator_review','approved','recovery_required','operation_failed','failed','refusal','invalid_proposal','rejected','completed_review_failed']) {
  render({id:'chat',revision:1,status,job_id:'job',clarification_fields:[{field:'assay',required:true}],job_state:{status},turns:[{id:'plan',kind:'plan_review',step:3,content:{}}]});
  const attention=byClass(container,'is-attention');assert.equal(attention.length,1,status);
  assert.match(attention[0].children[0].attrs['aria-label'],/Needs attention:/);
  assert.equal(byClass(container,'is-running').length,0,status);
  if(status==='operation_failed') assert.equal(attention[0].attrs['data-scientific-step'],'3');
}
assert.equal(actions.length,0);
""")


def test_scientific_operation_request_locates_synthesis_and_old_parent_never_animates():
    run_js(SETUP+r"""
for(const [action,phase,step] of [['advance_to_gate','planning',3],['run_trial',undefined,4],['advance_to_gate','synthesis',5]]) {
 const investigation={id:'child',revision:1,status:'running',scientific_session_id:'s',gate:'review',thread_turns:[
  {id:'old-active',investigation_id:'parent',kind:'scientific_operation',step:4,content:{operation:{status:'running',action:'run_trial'}}},
  {id:'current',investigation_id:'child',kind:'scientific_operation',step:3,content:{operation:{status:'running',action,request:{phase}}}}
 ]};
 render(investigation);const busy=byClass(container,'is-running');assert.equal(busy.length,1);
 assert.equal(busy[0].attrs['data-scientific-step'],String(step));
 const parent=walk(container).find(n=>n.attrs['data-parent-investigation']==='parent');
 assert.equal(byClass(parent,'is-running').length,0);assert.equal(byClass(parent,'is-attention').length,0);
 assert.equal(sections().filter(n=>n.open).at(-1).attrs['data-scientific-step'],String(step));
}
render({id:'child',revision:2,status:'recovery_required',turns:[{id:'op',kind:'scientific_operation',step:5,content:{operation:{status:'running',request:{phase:'synthesis'}}}}]});
assert.equal(byClass(container,'is-running').length,0);assert.equal(byClass(container,'is-attention')[0].attrs['data-scientific-step'],'5');
""")


def test_legacy_lifecycle_uses_identical_status_marker_without_executing():
    run_js(SETUP+r"""
for(const [status,kind] of [['running','is-running'],['awaiting_operator_review','is-attention'],['approved','is-attention']]) {
 api.render(container,{state:{id:'legacy',question:'Saved analysis',status},onAction:value=>actions.push(value)});
 assert.equal(byClass(container,kind).length,1);
}
api.render(container,{state:{id:'legacy',question:'Saved analysis',status:'completed_review_passed'}});
assert.equal(byClass(container,'is-running').length,0);assert.equal(byClass(container,'is-attention').length,0);assert.equal(actions.length,0);
""")


def test_monochrome_activity_motion_is_disabled_for_reduced_motion():
    from pathlib import Path
    import tinycss2

    theme = (Path(__file__).resolve().parents[1] / 'devbench/analysis/assets/workbench-theme.css').read_text()
    rules = tinycss2.parse_stylesheet(theme, skip_comments=True, skip_whitespace=True)
    assert not [rule for rule in rules if rule.type == 'error']
    reduced = [tinycss2.serialize(rule.content) for rule in rules if rule.type == 'at-rule' and rule.lower_at_keyword == 'media' and 'prefers-reduced-motion:reduce' in tinycss2.serialize(rule.prelude)]
    assert any('.wc-workflow-section .wc-workflow-marker' in rule and 'animation:none!important' in rule for rule in reduced)
    assert 'wc-workflow-spin 1.6s linear infinite' in theme
    assert 'wc-workflow-attention 2.4s ease-in-out infinite' in theme


def test_unknown_worker_liveness_never_claims_to_be_running():
    run_js(SETUP+r"""
render({id:'chat',revision:1,status:'running',job_state:{status:'running',worker_liveness:'unknown'},turns:[]});
assert.equal(byClass(container,'is-running').length,0);
assert.equal(byClass(container,'is-attention').length,1);
assert.match(byClass(container,'is-attention')[0].children[0].attrs['aria-label'],/Worker liveness unknown/);
api.render(container,{state:{id:'legacy',status:'planning',worker_liveness:'unknown'}});
assert.equal(byClass(container,'is-running').length,0);assert.equal(byClass(container,'is-attention').length,1);
""")
