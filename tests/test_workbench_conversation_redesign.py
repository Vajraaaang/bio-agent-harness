"""Recorded conversation presentation; no provider or scientific execution."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "devbench/analysis/assets/workbench-conversation.js"


def run_js(source):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for browser-module behavior checks")
    assert MODULE.is_file(), "The recorded conversation renderer is missing"
    result = subprocess.run([node, "-e", "const assert=require('node:assert/strict');require(" + json.dumps(str(MODULE)) + ");const api=globalThis.WorkbenchConversation;" + source], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_empty_state_distinguishes_operator_and_live_agent_access():
    run_js("""
    const model=api.buildModel(null,{model_access:false});
    assert.equal(model.mode,'Operator-created plan');
    assert.match(model.scope,/No model conversation/);
    assert.equal(model.events.length,0);
    assert.equal(model.records.length,0);
    const live=api.buildModel(null,{model_access:true});
    assert.match(live.scope,/bounded proposal/);
    """)


def test_failure_summary_keeps_traceback_in_evidence_only():
    run_js(r"""
    const failure='ValueError: data_sha256_mismatch: expected 000\nTraceback (most recent call last):\n  private scientific implementation details';
    const state={id:'a'.repeat(32),status:'operation_failed',question:'QC',error:{message:failure}};
    const model=api.buildModel(state,{});
    assert.match(model.summary.body,/checksum/);
    assert.ok(!model.summary.body.includes('Traceback'));
    assert.equal(model.failureRecord.message,failure);
    assert.equal(state.error.message,failure);
    """)


def test_only_recorded_lifecycle_events_and_actual_planner_reason_are_present():
    run_js("""
    const state={id:'a'.repeat(32),status:'awaiting_information',question:'Inspect quality',plan_author:'codex_agent',planning_result:{status:'awaiting_information',decision:{kind:'request_information',message:'Which column identifies capture units?'}},state_history:[{from:'queued',to:'inspecting',at:'2026-10-28T14:00:00Z'},{from:'inspecting',to:'awaiting_information',at:'2026-10-28T14:01:00Z'}]};
    const before=JSON.stringify(state),model=api.buildModel(state,{model_access:true});
    assert.equal(model.events.length,2);
    assert.deepEqual(model.events.map(e=>e.status),['inspecting','awaiting_information']);
    assert.equal(model.events[0].at,'2026-10-28T14:00:00Z');
    assert.ok(model.records.some(r=>r.body==='Which column identifies capture units?'));
    assert.equal(JSON.stringify(state),before);
    assert.equal(model.primaryAction.view,'review');
    assert.ok(!model.events.some(e=>e.status==='running'));
    """)


def test_approval_and_recovery_actions_never_execute_or_restart():
    run_js("""
    for(const status of ['approved','awaiting_operator_review','recovery_required','cancel_requested','interrupted_service']){
      const model=api.buildModel({id:'a'.repeat(32),status,question:'QC',error:{message:'Retained failure'}},{execution_allowed:true});
      assert.equal(model.primaryAction.type,'navigate');
      assert.ok(['review','execute'].includes(model.primaryAction.view));
      assert.ok(!['run','resume','approve'].includes(model.primaryAction.type));
    }
    assert.match(api.buildModel({status:'recovery_required',question:'QC'},{}).summary.body,/Nothing restarts automatically/);
    """)


def test_draft_followup_retains_source_and_input_without_mutation_or_submission():
    run_js("""
    const state={id:'abc',source:'/allowed/cells.h5ad',status:'completed_review_failed',question:'Original question',completed_analysis:true,proposal:{external_input:{path:'/allowed/cells.h5ad',sha256:'f'.repeat(64)}}};
    const model=api.buildModel(state,{}),draft=model.suggestions[0].action;
    assert.equal(draft.type,'draft');assert.equal(draft.sourceRunId,'abc');assert.equal(draft.input.path,'/allowed/cells.h5ad');
    assert.match(draft.question,/Original question/);
    assert.match(model.scope,/No model conversation/);
    assert.match(model.summary.body,/Independent scientific review/);
    assert.equal(state.question,'Original question');
    """)


def test_unknown_status_is_displayed_without_claiming_completion_or_fabricating_events():
    run_js("""
    const model=api.buildModel({status:'future_backend_state',question:'QC'},{});
    assert.equal(model.events.length,0);
    assert.equal(model.summary.title,'Future backend state');
    assert.match(model.summary.body,/recorded status/);
    assert.ok(!model.summary.body.includes('completed'));
    """)


def test_render_uses_text_nodes_and_clicks_only_dispatch_explicit_actions():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag)};
    const container=new Node('div'),actions=[];
    api.render(container,{state:{id:'a',status:'awaiting_operator_review',question:'<script>malicious</script>',proposal:{}},config:{},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    assert.ok(nodes.some(n=>n.textContent==='<script>malicious</script>'));
    assert.ok(nodes.every(n=>n.innerHTML===undefined));
    const button=nodes.find(n=>n.tagName==='button'&&n.textContent==='Review analysis plan');
    assert.ok(button);assert.equal(actions.length,0);button.click();
    assert.deepEqual(actions,[{type:'navigate',view:'review'}]);
    """)


def test_historical_proposal_author_is_not_rewritten_by_current_service_access():
    run_js("""
    const operator=api.buildModel({plan_author:'operator_created_recorded_plan',status:'approved'}, {model_access:true});
    assert.match(operator.scope,/No model conversation/);
    const prior=api.buildModel({plan_author:'codex_agent',status:'approved',model_sessions_attempted:2},{model_access:false});
    assert.match(prior.scope,/retained bounded proposal/);
    assert.match(prior.scope,/2 model sessions/);
    assert.ok(!prior.scope.includes('No model conversation'));
    """)


def test_structured_disclosures_keep_raw_json_secondary_and_copy_fallback_accessible():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.open=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}focus(){this.focused=true;}select(){this.selected=true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>{const node=new Node(tag);node.namespace=ns;return node;}};
    const container=new Node('div');
    api.render(container,{state:{id:'a',status:'approved',question:'QC',proposal:{configuration:{parameters:{workflow:'<script>unsafe</script>',memory_mb:512}},preview:{cells:80,genes:60}},review:{decision:'approve',rationale:'Reviewed input'}},config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),byClass=c=>nodes.filter(n=>(n.className||'').split(' ').includes(c));
    assert.ok(byClass('wc-disclosure').length);
    assert.ok(byClass('wc-kv-grid').every(n=>n.tagName==='dl'));
    assert.ok(nodes.some(n=>n.tagName==='dt'&&n.textContent==='Memory mb'));
    assert.ok(nodes.some(n=>n.textContent==='<script>unsafe</script>'));
    assert.ok(nodes.some(n=>n.tagName==='svg'&&n.namespace==='http://www.w3.org/2000/svg'));
    assert.ok(!nodes.some(n=>n.tagName==='pre'));
    const raw=byClass('wc-raw-inspection')[0];assert.equal(raw.open,false);
    const textarea=walk(raw).find(n=>n.tagName==='textarea');assert.equal(textarea.readOnly,true);
    assert.equal(nodes.filter(n=>n.textContent==='Copy JSON').length,byClass('wc-disclosure').length);
    byClass('wc-copy-json')[0].click();
    assert.equal(raw.open,true);assert.equal(textarea.selected,true);
    assert.ok(byClass('wc-card-heading').some(n=>n.textContent==='Analysis plan'));
    assert.ok(byClass('wc-status-pill').some(n=>n.textContent==='Ready'));
    assert.ok(byClass('wc-status-pill').some(n=>n.textContent==='Approved'));
    assert.equal(byClass('message-label').length,0);
    assert.equal(api.renderConversationWorkspace,api.render);
    """)


def test_disclosure_structuring_is_bounded_and_does_not_modify_source_records():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    let nested={leaf:'truth'};for(let i=0;i<30;i++)nested={nested};
    const fields={};for(let i=0;i<500;i++)fields['field_'+i]=i;
    const state={id:'x',question:'QC',status:'planning',planning_result:{decision:{kind:'propose',rationale:'Recorded rationale'},nested,fields}},before=JSON.stringify(state),container=new Node('div');
    api.render(container,{state,config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    assert.ok(nodes.filter(n=>n.tagName==='dt').length<=160);
    assert.ok(nodes.some(n=>(n.textContent||'').includes('Additional fields')));
    assert.equal(JSON.stringify(state),before);
    """)


def test_card_motion_runs_only_when_run_identity_changes_and_respects_reduced_motion():
    run_js("""
    const animations=[];
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}animate(frames,options){animations.push({frames,options});} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    let reduced=false;globalThis.matchMedia=()=>({matches:reduced});
    const container=new Node('div'),state={id:'a',status:'awaiting_operator_review',question:'QC',proposal:{}};
    api.render(container,{state,config:{}});assert.ok(animations.length>0);
    assert.ok(animations.every(a=>a.options.duration===180&&a.frames[0].transform==='translateY(4px)'));
    const count=animations.length;api.render(container,{state:{...state,status:'approved'},config:{}});assert.equal(animations.length,count);
    api.render(container,{state:{...state,id:'b'},config:{}});assert.ok(animations.length>count);
    reduced=true;const next=animations.length;api.render(container,{state:{...state,id:'c'},config:{}});assert.equal(animations.length,next);
    """)


def test_inline_plan_summary_uses_only_recorded_input_parameters_and_limits():
    run_js("""
    const summary=api.planSummary({proposal:{preview:{cells:80,genes:60},configuration:{run_timeout_seconds:180,parameters:{workflow:'scrna_qc_integration_annotation/1',correction:'none',memory_mb:1024}}}});
    assert.equal(summary.length,4);
    assert.equal(summary[0].value,'80 cells × 60 genes');
    assert.equal(summary[1].value,'QC, integration, clustering and annotation');
    assert.equal(summary[2].value,'No correction');
    assert.equal(summary[3].value,'180 s · 1,024 MiB / worker');
    assert.ok(api.planSummary({}).every(metric=>metric.value==='Not recorded'));
    """)


def test_investigation_projection_uses_real_turns_and_requires_actual_review_run_states():
    run_js("""
    const investigation={id:'i',revision:3,status:'awaiting_information',job_id:null,question:'Inspect quality',profile:{observed:{cells:80,genes:60,obs_columns:['capture']},declarations:{},inferred:{candidate_columns:{capture_unit:['capture']}},unknowns:[{field:'assay',reason:'Experimental declaration required'}]},declarations:{},clarification_fields:[{field:'capture_unit_key',type:'select',options:[{value:'capture',label:'capture'}],required:true}],turns:[{id:'t1',step:1,kind:'user_question',role:'user',title:'Your question',content:{question:'Inspect quality'}},{id:'t2',step:1,kind:'data_audit',role:'workbench',title:'Input inspected',content:{}}]};
    const before=JSON.stringify(investigation),model=api.buildInvestigationModel(investigation,{});
    assert.equal(model.turns.length,2);assert.deepEqual(model.turns.map(t=>t.id),['t1','t2']);
    assert.equal(model.inlineGate.kind,'clarification');assert.equal(model.inlineGate.revision,3);
    assert.equal(model.profile.observed.cells,80);assert.equal(model.profile.declarations.assay,undefined);
    assert.equal(JSON.stringify(investigation),before);
    const approved={...investigation,status:'approved',job_id:'job',job_state:{status:'approved'},clarification_fields:[]};
    assert.equal(api.buildInvestigationModel(approved,{}).inlineGate.kind,'run');
    assert.equal(api.buildInvestigationModel({...approved,job_state:{status:'awaiting_operator_review'}},{}).inlineGate.kind,'review');
    assert.equal(api.buildInvestigationModel({...approved,job_state:{status:'running'}},{}).inlineGate.kind,null);
    """)


def test_investigation_renders_observed_declarations_inferences_unknowns_and_safe_clarification():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const container=new Node('div'),actions=[],investigation={id:'i',revision:2,status:'awaiting_information',question:'QC',declarations:{count_provenance:'Supplied protocol'},profile:{observed:{cells:80,genes:60,obs_columns:['actual_batch']},declarations:{count_provenance:'Supplied protocol'},inferred:{candidate_columns:{batch:['actual_batch']}},unknowns:[{field:'assay',reason:'Unknown assay'}]},clarification_fields:[{field:'capture_unit_key',label:'Capture unit',type:'select',required:true,options:[{value:'actual_batch',label:'actual_batch'}]}],turns:[{id:'audit',step:1,kind:'data_audit',title:'Data audit',content:{}}]};
    const model=api.renderInvestigation(container,{investigation,config:{},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    for(const heading of ['Observed','Supplied declarations','Inferred','Unknowns'])assert.ok(nodes.some(n=>n.textContent===heading));
    const select=nodes.find(n=>n.tagName==='select');assert.ok(select);
    assert.deepEqual(select.children.map(n=>n.value),['','actual_batch']);
    select.value='actual_batch';select.change();
    assert.equal(actions.length,0);
    const form=nodes.find(n=>n.tagName==='form');form.submit({preventDefault(){}});
    assert.equal(actions.length,1);assert.equal(actions[0].type,'clarify');assert.equal(actions[0].expectedRevision,2);
    assert.equal(actions[0].declarations.capture_unit_key,'actual_batch');
    assert.equal(actions[0].declarations.count_provenance,'Supplied protocol');
    assert.equal(model.inlineGate.kind,'clarification');
    assert.ok(!nodes.some(n=>n.attrs.id==='review-form'||n.attrs.id==='run-button'));
    """)


def test_investigation_run_gate_has_metrics_and_no_duplicate_execution_control():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const container=new Node('div'),actions=[];
    const investigation={id:'i',revision:4,status:'approved',job_id:'j',job_state:{status:'approved',proposal:{preview:{cells:80,genes:60},configuration:{parameters:{workflow:'scrna_qc_integration_annotation/1',correction:'none',memory_mb:1024},run_timeout_seconds:180}}},turns:[{id:'p',kind:'plan_review',step:3,title:'Plan prepared',content:{}}]};
    const model=api.renderInvestigation(container,{investigation,config:{execution_allowed:true},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    assert.equal(model.inlineGate.kind,'run');assert.equal(model.inlineGate.jobId,'j');
    assert.ok(nodes.some(n=>(n.className||'').split(' ').includes('wc-plan-metrics')));
    assert.ok(nodes.some(n=>n.textContent==='80 cells × 60 genes'));
    assert.equal(actions.length,0);
    assert.ok(!nodes.some(n=>n.tagName==='button'&&/run analysis|execute|approve/i.test(n.textContent||'')));
    """)


def test_investigation_thread_renders_ancestor_evidence_and_current_gate_without_rewriting_history():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const originalAudit={observed:{cells:80},declarations:{count_provenance:'Original declaration'},inferred:{},unknowns:[]};
    const originalPlan={preview:{cells:80,genes:60},configuration:{parameters:{workflow:'old_workflow'}}};
    const currentPlan={preview:{cells:70,genes:50},configuration:{parameters:{workflow:'current_workflow'}}};
    const investigation={id:'child',thread_id:'parent',revision:4,status:'approved',job_id:'current',proposal_mode:'operator',profile:{observed:{cells:70},declarations:{count_provenance:'Later declaration'}},job_state:{status:'approved',proposal:currentPlan},turns:[],thread_turns:[{id:'audit1',investigation_id:'parent',step:1,kind:'data_audit',content:originalAudit},{id:'plan1',investigation_id:'parent',step:3,kind:'plan_review',content:{job_id:'old',proposal:originalPlan}},{id:'plan2',investigation_id:'child',step:3,kind:'plan_review',content:{job_id:'current',proposal:currentPlan}}]};
    const container=new Node('div'),before=JSON.stringify(investigation),model=api.renderInvestigation(container,{investigation,config:{model_access:true}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    assert.equal(model.turns.length,3);assert.equal(model.inlineGate.jobId,'current');
    assert.ok(nodes.some(n=>n.textContent==='Original declaration'));
    const parentCard=nodes.find(n=>n.attrs['data-workflow-id']==='parent'),currentCard=nodes.find(n=>n.attrs['data-workflow-id']==='child');
    assert.ok(!walk(parentCard).some(n=>n.textContent==='Later declaration'));
    assert.ok(walk(currentCard).some(n=>n.textContent==='Later declaration'));
    assert.ok(nodes.some(n=>n.textContent==='old_workflow'));assert.ok(nodes.some(n=>n.textContent==='current_workflow'));
    assert.equal(model.mode,'Operator-created plan');assert.match(model.scope,/No model conversation/);
    assert.equal(JSON.stringify(investigation),before);
    assert.equal(api.buildInvestigationModel(null,{}).inlineGate,null);
    """)


def test_current_synthesis_and_gate_do_not_hide_or_relabel_ancestor_decisions():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const investigation={id:'current',revision:3,status:'completed_review_passed',job_id:'j',job_state:{status:'completed_review_passed'},synthesis:{supported_findings:{current:'Current finding'},method_evidence_assessment:{},uncertainties:[]},thread_turns:[{id:'oldplan',investigation_id:'old',kind:'plan_review',step:3,title:'Earlier plan',content:{status:'rejected',proposal:{}}},{id:'oldsyn',investigation_id:'old',kind:'synthesis',step:5,title:'Earlier synthesis',content:{supported_findings:{prior:'Prior retained finding'}}}]};
    const container=new Node('div');api.renderInvestigation(container,{investigation,config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    assert.ok(nodes.some(n=>n.textContent==='Prior retained finding'));
    assert.ok(nodes.some(n=>n.textContent==='Current finding'));
    const prior=nodes.find(n=>n.attrs['data-turn-id']==='oldplan');
    assert.ok(walk(prior).some(n=>n.textContent==='Plan rejected'));
    assert.ok(!walk(prior).some(n=>n.textContent==='Analysis completed'));
    """)


def test_scientific_context_clarification_stays_separate_and_requires_permitted_explicit_model_grant():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;this.disabled=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const fields=[{scope:'scientific_context',field:'data_classification',label:'Data classification',type:'select',required:true,options:['synthetic','public','private','unclassified'].map(value=>({value,label:value}))},{scope:'scientific_context',field:'allow_model_execution',label:'Allow planning model calls',type:'boolean',required:false},{scope:'scientific_context',field:'auto_advance',label:'Advance to the next review gate',type:'boolean',required:false}];
    const investigation={id:'i',revision:5,status:'awaiting_information',required_action:'scientific_context',scientific_context:{},declarations:{assay:'scrna'},clarification_fields:fields,turns:[]};
    const actions=[],container=new Node('div');
    api.renderInvestigation(container,{investigation,config:{scientific:{model_execution_allowed:true,scientific_workflow_configured:true}},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),byName=name=>nodes.find(n=>n.attrs.name===name);
    const classification=byName('data_classification'),grant=byName('allow_model_execution'),advance=byName('auto_advance');
    assert.equal(grant.disabled,true);assert.equal(grant.checked,false);
    classification.value='public';classification.change();assert.equal(grant.disabled,false);assert.equal(grant.checked,false);
    grant.checked=true;grant.change();advance.checked=true;advance.change();nodes.find(n=>n.tagName==='form').submit({preventDefault(){}});
    assert.equal(actions[0].scientificContext.data_classification,'public');assert.equal(actions[0].scientificContext.allow_model_execution,true);assert.equal(actions[0].scientificContext.auto_advance,true);
    assert.deepEqual(actions[0].declarations,{assay:'scrna'});assert.equal(actions[0].declarations.data_classification,undefined);
    classification.value='private';classification.change();assert.equal(grant.disabled,true);assert.equal(grant.checked,false);
    grant.checked=true;nodes.find(n=>n.tagName==='form').submit({preventDefault(){}});
    assert.equal(actions[1].scientificContext.allow_model_execution,false);
    """)


def test_startup_model_and_workflow_permissions_cannot_be_granted_by_clarification_controls():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const fields=[{scope:'scientific_context',field:'data_classification',type:'select',options:[{value:'public',label:'Public'}]},{scope:'scientific_context',field:'allow_model_execution',type:'boolean'},{scope:'scientific_context',field:'auto_advance',type:'boolean'}];
    const investigation={id:'i',revision:1,status:'awaiting_information',scientific_context:{data_classification:'public'},clarification_fields:fields,turns:[]},container=new Node('div'),actions=[];
    api.renderInvestigation(container,{investigation,config:{scientific:{model_execution_allowed:false,scientific_workflow_configured:false}},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),grant=nodes.find(n=>n.attrs.name==='allow_model_execution'),advance=nodes.find(n=>n.attrs.name==='auto_advance');
    assert.equal(grant.disabled,true);assert.equal(advance.disabled,true);grant.checked=true;advance.checked=true;
    nodes.find(n=>n.tagName==='form').submit({preventDefault(){}});
    assert.equal(actions[0].scientificContext.allow_model_execution,false);assert.equal(actions[0].scientificContext.auto_advance,false);
    """)


def test_explicit_synthetic_offline_auto_advance_never_implies_a_model_grant():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const fields=[{scope:'scientific_context',field:'data_classification',type:'select',options:[{value:'synthetic',label:'Synthetic'},{value:'public',label:'Public'}]},{scope:'scientific_context',field:'allow_model_execution',type:'boolean'},{scope:'scientific_context',field:'auto_advance',type:'boolean'}];
    const investigation={id:'i',revision:1,status:'awaiting_information',scientific_context:{data_classification:'synthetic'},clarification_fields:fields,turns:[]},container=new Node('div'),actions=[];
    api.renderInvestigation(container,{investigation,config:{scientific:{planning_mode:'recorded_fixture',model_execution_allowed:false,scientific_workflow_configured:true}},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),grant=nodes.find(n=>n.attrs.name==='allow_model_execution'),advance=nodes.find(n=>n.attrs.name==='auto_advance'),classification=nodes.find(n=>n.attrs.name==='data_classification');
    assert.equal(grant.disabled,true);assert.equal(grant.checked,false);assert.equal(advance.disabled,false);assert.equal(advance.checked,false);
    advance.checked=true;advance.change();nodes.find(n=>n.tagName==='form').submit({preventDefault(){}});
    assert.equal(actions[0].scientificContext.auto_advance,true);assert.equal(actions[0].scientificContext.allow_model_execution,false);
    classification.value='public';classification.change();assert.equal(advance.disabled,true);assert.equal(advance.checked,false);
    """)


def test_user_question_is_unnumbered_and_scientific_spine_starts_with_data_audit():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const container=new Node('div'),investigation={id:'i',revision:1,status:'awaiting_information',turns:[{id:'question',role:'user',kind:'user_question',step:2,content:{question:'Inspect quality'}},{id:'audit',role:'workbench',kind:'data_audit',step:1,content:{observed:{cells:80,genes:60},declarations:{},inferred:{},unknowns:[]}},{id:'scope',role:'workbench',kind:'question_scope',step:2,title:'Question and scope',content:{intent:'qc_and_integration'}}]};
    api.renderInvestigation(container,{investigation,config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),spine=nodes.filter(n=>n.attrs['data-scientific-step']);
    assert.deepEqual(spine.map(n=>n.attrs['data-scientific-step']),['1','2','3','4','5']);
    const user=nodes.find(n=>n.attrs['data-turn-id']==='question');assert.ok(user);
    assert.ok(walk(user).some(n=>n.textContent==='Your question'));
    assert.ok(!walk(user).some(n=>n.attrs['data-scientific-step']));
    assert.equal(nodes.find(n=>(n.className||'').includes('wc-workflow-card')).children[1],spine[0]);
    assert.ok(!nodes.some(n=>new RegExp('^0[1-5] / ').test(n.textContent||'')));
    """)


def test_data_audit_is_compact_with_four_closed_structured_scope_accordions():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.open=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const container=new Node('div'),investigation={id:'i',revision:1,status:'awaiting_information',turns:[{id:'audit',kind:'data_audit',step:1,content:{input:{source:'/allowed/actual.h5ad'},observed:{cells:80,genes:60,obs_columns:['batch']},declarations:{},inferred:{candidate_columns:{batch:['batch']}},unknowns:Array.from({length:6},(_,i)=>({field:'unknown_'+i,reason:'Supplied records needed',blocking:true}))}}]};
    api.renderInvestigation(container,{investigation,config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),grid=nodes.find(n=>(n.className||'').split(' ').includes('wc-audit-grid'));
    assert.equal(grid.children.length,4);
    assert.ok(grid.children.every(n=>n.tagName==='details'&&n.open===false&&(n.className||'').includes('wc-disclosure')));
    assert.ok(nodes.some(n=>n.textContent==='actual.h5ad · 80 cells × 60 genes'));
    assert.ok(walk(grid).some(n=>n.tagName==='dl'&&(n.className||'').includes('wc-kv-grid')));
    assert.ok(!walk(grid).some(n=>n.tagName==='pre'));
    """)


def test_clarification_deduplicates_identical_reasons_but_preserves_unique_constraints():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const shared='Confirm from experimental records; detected columns do not establish biological meaning.',unique='A batch label alone does not establish independent biological replication.';
    const fields=[{field:'assay',type:'text',reason:shared},{field:'measurement',type:'text',reason:shared},{field:'capture_unit_key',type:'text',reason:unique},{field:'count_provenance',type:'text',reason:shared}];
    const container=new Node('div');api.renderInvestigation(container,{investigation:{id:'i',revision:1,status:'awaiting_information',clarification_fields:fields,turns:[]},config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    assert.equal(nodes.filter(n=>n.textContent===shared).length,1);assert.equal(nodes.filter(n=>n.textContent===unique).length,1);
    const sharedNode=nodes.find(n=>n.textContent===shared);
    for(const name of ['assay','measurement','count_provenance'])assert.equal(nodes.find(n=>n.attrs.name===name).attrs['aria-describedby'],sharedNode.attrs.id);
    """)


def test_synthesis_followup_chips_only_draft_with_current_source_binding():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const actions=[],container=new Node('div');
    const investigation={id:'i',revision:4,status:'completed_review_passed',job_id:'current',parent_job:'earlier',source:'/allowed/cells.h5ad',selection:{observation_ids:['c1']},synthesis:{supported_findings:{cells_retained:80},method_evidence_assessment:{},uncertainties:[]},turns:[]};
    api.renderInvestigation(container,{investigation,config:{},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),chips=nodes.filter(n=>(n.className||'').split(' ').includes('wc-suggestion'));
    assert.ok(chips.length>=2&&chips.length<=3);assert.equal(actions.length,0);
    for(const chip of chips)chip.click();
    assert.ok(actions.every(a=>a.type==='draft'&&a.sourceRunId==='current'&&a.input.path==='/allowed/cells.h5ad'));
    assert.ok(actions.every(a=>!a.question.includes('identified')&&!a.question.includes('significant')));
    assert.deepEqual(investigation.selection.observation_ids,['c1']);
    """)


def test_native_model_permission_fallback_and_separate_synthesis_grant_remain_explicit():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const fields=[{scope:'scientific_context',field:'data_classification',type:'select',options:[{value:'synthetic',label:'Synthetic'}]},{scope:'scientific_context',field:'allow_model_execution',type:'boolean'},{scope:'scientific_context',field:'auto_advance',type:'boolean'},{scope:'scientific_context',field:'auto_synthesis',type:'boolean'}];
    const investigation={id:'i',revision:1,status:'awaiting_information',scientific_context:{data_classification:'synthetic'},clarification_fields:fields,turns:[]},container=new Node('div'),actions=[];
    api.renderInvestigation(container,{investigation,config:{model_access:true,scientific:{enabled:false}},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),byName=name=>nodes.find(n=>n.attrs.name===name);
    assert.equal(byName('allow_model_execution').disabled,false);
    assert.equal(byName('auto_synthesis').disabled,true);assert.equal(byName('auto_advance').disabled,true);
    api.renderInvestigation(container,{investigation,config:{scientific:{model_execution_allowed:true,scientific_workflow_configured:true}},onAction:a=>actions.push(a)});
    const next=walk(container),control=name=>next.find(n=>n.attrs.name===name);
    control('allow_model_execution').checked=true;control('allow_model_execution').change();
    assert.equal(control('auto_synthesis').disabled,false);assert.equal(control('auto_synthesis').checked,false);
    control('auto_advance').checked=true;control('auto_advance').change();next.find(n=>n.tagName==='form').submit({preventDefault(){}});
    assert.equal(actions[0].scientificContext.auto_advance,true);assert.equal(actions[0].scientificContext.auto_synthesis,false);
    control('auto_synthesis').checked=true;control('auto_synthesis').change();next.find(n=>n.tagName==='form').submit({preventDefault(){}});
    assert.equal(actions[1].scientificContext.auto_synthesis,true);
    """)


def test_scientific_review_gate_emits_exact_frozen_trial_and_explicit_operator_review():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;this.disabled=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const spec={name:'Frozen comparison',candidates:['observed_method'],resource_limits:{memory_mb:512}},confirmations=[{key:'question_and_scope',label:'I reviewed the question and scope.'},{key:'trial_criteria',label:'I reviewed the frozen comparison criteria.'}];
    const investigation={id:'i',revision:6,status:'awaiting_operator_review',scientific_session_id:'s',gate:'review',scientific_plan:{session_id:'s'},gate_options:{gate:'review',session_id:'s',workspace_revision:23,can_submit:true,blocked_reasons:[],proposals:[{draft_id:'d',spec,eligible:true,blocked_reasons:[]}],declared_trials:[],required_confirmations:confirmations,review_evidence:{design_review:{status:'passed'}}},turns:[]};
    const actions=[],container=new Node('div'),before=JSON.stringify(investigation),model=api.renderInvestigation(container,{investigation,config:{},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),control=name=>nodes.find(n=>n.attrs.name===name),form=nodes.find(n=>(n.className||'').includes('wc-scientific-gate-form'));
    assert.equal(model.inlineGate.kind,'scientific_review');assert.equal(actions.length,0);
    const select=control('trial');assert.equal(select.value,'');select.value='d';select.change();control('reviewer').value='Researcher';control('rationale').value='Reviewed frozen design and input context';
    form.submit({preventDefault(){}});assert.equal(actions.length,0);
    for(const key of ['question_and_scope','trial_criteria'])control('confirm-'+key).checked=true;
    form.submit({preventDefault(){}});assert.equal(actions.length,1);
    assert.equal(actions[0].type,'scientific');assert.equal(actions[0].request.action,'declare_trial');assert.equal(actions[0].request.expected_revision,23);assert.equal(actions[0].request.session_id,'s');assert.deepEqual(actions[0].request.spec,spec);
    assert.equal(actions[0].operatorReview.reviewer,'Researcher');assert.equal(actions[0].operatorReview.confirmations.trial_criteria,true);
    assert.equal(JSON.stringify(investigation),before);
    """)


def test_scientific_execution_gate_requires_data_grant_and_stable_idempotency_key():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;this.disabled=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const investigation={id:'i',revision:8,status:'awaiting_operator_review',scientific_session_id:'s',gate:'execution_review',gate_options:{gate:'execution_review',session_id:'s',workspace_revision:25,can_submit:true,startup_data_execution_allowed:true,declared_trials:[{experiment_id:'e',status:'declared',spec:{candidates:['actual']}}],required_confirmations:[]},turns:[]};
    const container=new Node('div'),actions=[];api.renderInvestigation(container,{investigation,config:{execution_allowed:true},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),control=name=>nodes.find(n=>n.attrs.name===name),form=nodes.find(n=>(n.className||'').includes('wc-scientific-gate-form'));
    control('trial').value='e';control('trial').change();control('reviewer').value='Researcher';control('rationale').value='Execute declared trial';form.submit({preventDefault(){}});assert.equal(actions.length,0);
    control('allow_data_execution').checked=true;form.submit({preventDefault(){}});assert.equal(actions.length,1);
    assert.equal(actions[0].request.action,'run_trial');assert.equal(actions[0].request.allow_data_execution,true);assert.equal(actions[0].request.experiment_id,'e');assert.ok(actions[0].request.idempotency_key);
    form.submit({preventDefault(){}});assert.equal(actions[1].request.idempotency_key,actions[0].request.idempotency_key);
    assert.ok(!nodes.some(n=>n.attrs.id==='run-button'||n.attrs.id==='review-form'));
    """)


def test_missing_or_failed_scientific_review_projection_never_exposes_an_enabled_gate_action():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.value='';this.checked=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}reportValidity(){return true;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const container=new Node('div'),actions=[],base={id:'i',revision:1,status:'awaiting_operator_review',gate:'review',scientific_session_id:'s',turns:[]};
    api.renderInvestigation(container,{investigation:{...base,gate_options:{gate:'review',session_id:'s',workspace_revision:3,can_submit:false,blocked_reasons:['Design review has not passed.'],proposals:[{draft_id:'d',eligible:false,spec:{},blocked_reasons:['Missing evidence']}] }},config:{},onAction:a=>actions.push(a)});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container),submit=nodes.find(n=>(n.className||'').includes('wc-scientific-submit'));
    assert.equal(submit.disabled,true);assert.ok(nodes.some(n=>n.textContent==='Design review has not passed.'));
    nodes.find(n=>(n.className||'').includes('wc-scientific-gate-form')).submit({preventDefault(){}});assert.equal(actions.length,0);
    api.renderInvestigation(container,{investigation:base,config:{},onAction:a=>actions.push(a)});
    assert.ok(!walk(container).some(n=>(n.className||'').includes('wc-scientific-submit')&&!n.disabled));assert.equal(actions.length,0);
    """)


def test_plan_review_receipts_show_actual_operator_and_no_invented_metrics():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const investigation={id:'i',revision:2,status:'approved',job_id:'j',job_state:{status:'approved',proposal:{preview:{cells:80,genes:60}}},turns:[{id:'native-review',kind:'plan_review',title:'Native operator decision recorded',content:{job_id:'j',review:{reviewer:'Native scientist',decision:'approve',rationale:'Reviewed native assumptions',confirmations:{question_and_scope:true}},result:{status:'approved'}}},{id:'science-review',kind:'plan_review',title:'Scientific declaration review recorded',content:{operator_review:{reviewer:'Scientific reviewer',rationale:'Reviewed scientific comparison criteria',confirmations:{trial_criteria:true}},result:{experiment:{status:'declared'}}}},{id:'unknown-plan',kind:'plan_review',title:'Earlier unknown plan',investigation_id:'earlier',content:{status:'rejected'}}]};
    const before=JSON.stringify(investigation),container=new Node('div');api.renderInvestigation(container,{investigation,config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    for(const [id,name,rationale] of [['native-review','Native scientist','Reviewed native assumptions'],['science-review','Scientific reviewer','Reviewed scientific comparison criteria']]){
      const card=nodes.find(n=>n.attrs['data-turn-id']===id),inside=walk(card);
      assert.ok(inside.some(n=>n.textContent==='Operator review'));assert.ok(inside.some(n=>n.textContent===name));assert.ok(inside.some(n=>n.textContent===rationale));
      assert.ok(!inside.some(n=>(n.className||'').includes('wc-plan-metrics')));
      assert.equal(card.attrs['data-turn-kind'],'plan_review');
      assert.ok(inside.some(n=>n.tagName==='textarea'&&(n.value||'').includes(name)));
    }
    assert.ok(!walk(nodes.find(n=>n.attrs['data-turn-id']==='unknown-plan')).some(n=>(n.className||'').includes('wc-plan-metrics')));
    assert.equal(JSON.stringify(investigation),before);
    """)


def test_execution_and_scientific_steps_are_closed_single_summary_disclosures():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.open=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const turns=[{id:'running',kind:'job_event',title:'running',step:4,content:{from:'preparing',to:'running',at:'2026-10-28T12:00:00Z'}},{id:'design',kind:'scientific_step',title:'design_review',step:3,content:{step:{node:'design_review',status:'completed',output:{accepted:true}}}},{id:'operation',kind:'scientific_operation',title:'advance_to_gate',step:3,content:{operation:{status:'running',action:'advance_to_gate'}}}];
    const container=new Node('div');api.renderInvestigation(container,{investigation:{id:'i',revision:3,status:'running',turns},config:{}});
    const walk=n=>[n,...n.children.flatMap(walk)],nodes=walk(container);
    for(const turn of turns){const card=nodes.find(n=>n.attrs['data-turn-id']===turn.id);assert.equal(card.tagName,'details');assert.equal(card.open,false);assert.equal(card.attrs['data-turn-kind'],turn.kind);assert.equal(card.children[0].tagName,'summary');assert.ok(!walk(card).some(n=>n.tagName==='h3'));assert.ok(!walk(card).some(n=>n.textContent==='Execution evidence'));}
    const running=nodes.find(n=>n.attrs['data-turn-id']==='running');assert.ok(walk(running.children[0]).some(n=>n.textContent==='Analysis running'));
    assert.ok(!nodes.some(n=>n.textContent==='Analysis completed'));
    """)


def test_scientific_planner_attribution_uses_immutable_dispatch_source_not_current_configuration():
    run_js("""
    const base={id:'i',revision:3,status:'awaiting_operator_review',proposal_mode:'operator',scientific_session_id:'s',turns:[]};
    const offline=api.buildInvestigationModel({...base,planning_source:{backend:'recorded_fixture',source:'configured scientific selector at dispatch'}},{model_access:true,scientific:{planning_mode:'codex-cli'}});
    assert.equal(offline.mode,'Offline scientific planning');assert.ok(!offline.scope.includes('operator-created'));assert.match(offline.scope,/recorded|offline/i);
    const codex=api.buildInvestigationModel({...base,planning_source:{backend:'codex-cli',source:'configured scientific selector at dispatch'}},{scientific:{planning_mode:'recorded_fixture'}});
    assert.equal(codex.mode,'Scientific planner');assert.ok(!/model calls were|model sessions were/.test(codex.scope));
    const historical=api.buildInvestigationModel(base,{model_access:true,scientific:{planning_mode:'recorded_fixture'}});
    assert.equal(historical.mode,'Scientific planning');assert.ok(!historical.scope.includes('operator-created'));assert.ok(!historical.scope.includes('Offline'));
    const native=api.buildInvestigationModel({id:'native',job_id:'j',proposal_mode:'agent',job_state:{plan_author:'operator_created_recorded_plan',status:'approved'}},{model_access:true});
    assert.equal(native.mode,'Operator-created plan');assert.match(native.scope,/No model conversation/);
    """)


def test_scientific_synthesis_prioritizes_actual_trial_metrics_and_keeps_ledgers_secondary():
    run_js("""
    class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.open=false;}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;} }
    globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
    const ledger={nodes:Array.from({length:21},(_,i)=>({node:'ledger_'+i,hash:'f'.repeat(64)}))};
    const trial={experiment_id:'e',candidate:'qc_candidate',status:'success',metric_values:{cells_retained:80,HVG_count:30},provenance:{receipt:'a'.repeat(64)}};
    const shapes=[
      {supported_findings:{workflow_status:'abstained',scientific_qualification:'not independently validated',trial_results:[trial]},method_evidence_assessment:{status:'ABSTAIN',evidence_floor:'Insufficient comparative evidence',comparison_evidence:{status:'insufficient',per_candidate:Array.from({length:25},(_,i)=>({name:'candidate_'+i}))}},uncertainties:['Independent biological validation remains absent.'],evidence:{governed_outputs:ledger}},
      {supported_findings:{governed_outputs:ledger,trial_results:[trial]},method_evidence_assessment:{source:'Existing governed scientific workflow',status:'ABSTAIN',comparison_evidence:{status:'insufficient',per_candidate:[{name:'other'}]}},uncertainties:['Comparison remains unresolved.']}
    ];
    for(const synthesis of shapes){
      const investigation={id:'i',revision:7,status:'abstained',scientific_session_id:'s',planning_source:{backend:'recorded_fixture'},synthesis,turns:[]},before=JSON.stringify(investigation),container=new Node('div');
      api.renderInvestigation(container,{investigation,config:{}});
      const all=n=>[n,...n.children.flatMap(all)],visible=n=>[n,...(n.tagName==='details'&&!n.open?n.children.slice(0,1):n.children).flatMap(visible)],nodes=all(container),card=nodes.find(n=>(n.className||'').split(' ').includes('wc-synthesis')),primary=visible(card);
      assert.ok(primary.some(n=>n.textContent==='80'));assert.ok(primary.some(n=>n.textContent==='30'));assert.ok(primary.some(n=>n.textContent==='ABSTAIN'));
      assert.ok(!primary.some(n=>n.textContent==='ledger_0'||n.textContent==='candidate_0'||n.textContent==='f'.repeat(64)));
      const evidence=all(card).find(n=>n.tagName==='details'&&all(n.children[0]).some(child=>child.textContent==='Workflow evidence'));
      assert.ok(evidence);assert.equal(evidence.open,false);assert.ok(all(evidence).some(n=>n.tagName==='textarea'&&(n.value||'').includes('ledger_0')));
      assert.ok(all(card).some(n=>n.tagName==='details'&&all(n.children[0]).some(child=>child.textContent==='Comparison evidence')));
      assert.equal(JSON.stringify(investigation),before);
    }
    """)
