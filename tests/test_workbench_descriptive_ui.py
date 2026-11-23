"""Local descriptive answers use retained evidence, never scientific execution."""
import pytest

from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_investigation_asset import run_js as run_controller
from tests.test_workbench_project_http import IB, B
from tests.test_workbench_project_http import routing as _routing_fixture
from tests.test_workbench_workflow_accordion import SETUP

routing = _routing_fixture
DESCRIPTION = r"""
const binding={source:'/approved/cells.h5ad',source_sha256:'f'.repeat(64),source_binding_status:'verified',project_id:'project-A',question_id:'chat',operation_id:'op-1',result_ref:'answer-1',population:{kind:'all_observations',scope:'full_source_metadata'}};
const answer={format:'devbench.dataset-answer/1',message:'Stored labels\n\nThese are the observed values.',binding,tables:[{caption:'Stored cell_type labels',columns:['cell_type','count'],rows:[['<img src=x onerror=alert(1)>',3],[null,1]],denominator:4,missing_count:1,truncated:false}],facts:[{name:'cell_type',value:'Stored annotation',origin:'observed',source_ref:'obs/cell_type'}],limitations:['Stored labels do not establish validated cell identity.'],completion:'completed'};
const investigation={id:'chat',revision:2,question:'Which annotations are stored?',source:binding.source,status:'completed',request:{format:'devbench.workbench-request/1',category:'descriptive_retrieval',descriptive_topics:['annotations'],remaining_action:null,routing_hint:'local_question_classification'},description_operation:{operation_id:'op-1',status:'completed'},turns:[{id:'q',kind:'user_question',role:'user',step:2,content:{question:'Which annotations are stored?'}},{id:'a',kind:'dataset_answer',step:4,content:answer}]};
"""


def test_direct_answer_has_semantic_text_safe_tables_and_explicit_counts():
    run_js(SETUP + DESCRIPTION + r"""
const before=JSON.stringify(investigation);const model=render(investigation),nodes=walk(container);
const table=nodes.find(n=>n.tagName==='table');assert.ok(table,'Dataset answer must be a direct table');
assert.equal(walk(table).find(n=>n.tagName==='caption').textContent,'Stored cell_type labels');
assert.deepEqual(walk(table).filter(n=>n.tagName==='th').map(n=>[n.textContent,n.attrs.scope]),[['cell_type','col'],['count','col']]);
assert.ok(walk(table).some(n=>n.tagName==='td'&&n.textContent==='<img src=x onerror=alert(1)>'));
assert.ok(walk(table).some(n=>n.tagName==='td'&&n.textContent==='Missing'));
assert.ok(nodes.some(n=>n.textContent==='Denominator: 4 · Missing: 1'));
assert.ok(nodes.some(n=>n.textContent==='Stored labels\n\nThese are the observed values.'));
assert.ok(nodes.some(n=>n.textContent==='Stored labels do not establish validated cell identity.'));
assert.ok(nodes.some(n=>(n.textContent||"").includes('All observations · full-source metadata')));
assert.ok(nodes.some(n=>(n.textContent||"").includes('cells.h5ad')&&(n.textContent||"").includes('ffffffffffff')));
assert.ok(nodes.every(n=>n.innerHTML===undefined));assert.equal(JSON.stringify(investigation),before);
assert.equal(model.mode,'Local dataset retrieval');assert.equal(actions.length,0);
assert.ok(!nodes.some(n=>n.tagName==='form'));assert.equal(sections()[3].open,true);
""")


def test_matrix_answer_shows_pca_with_descriptive_scope():
    run_js(
        SETUP
        + DESCRIPTION
        + r"""
require(process.cwd()+'/src/devbench/analysis/assets/bulk-scientific-figure.js');
investigation.request={...investigation.request,category:'exploratory_computation',
  descriptive_topics:[],remaining_action:'exploratory_computation',matrix_computation_required:true};
delete investigation.description_operation;
investigation.descriptive_matrix_audit={audit_id:'audit-id'};
investigation.synthesis={supported_findings:{summary:'Legacy diagnostic summary'}};
Object.assign(answer,{matrix_audit_id:'audit-id',matrix_metrics:[{label:'Genes',value:8}],
  plot:{kind:'batch_pca',table_index:2,variance_ratio:[.8,.2],source_sample_count:2,gene_count:8},
  tables:[
    {caption:'Finite nonzero genes per observed profile',
      columns:['Profile','Nonzero genes'],rows:[['s1',5],['s2',4]]},
    {caption:'Per-gene batch diagnostics',
      columns:['Gene','Batch partial R²'],rows:[['g1',null]]},
    {caption:'PCA scores by batch',columns:['Profile','Batch','PC1','PC2'],
      rows:[['s1','batch <example>',-1,.2],['s2','other',1,-.2]]},
  ],
});
binding.population.scope='full_source_matrix';binding.result_ref='a'.repeat(64);
const model=render(investigation),nodes=walk(container);
assert.equal(model.mode,'Local matrix diagnostics');assert.equal(model.synthesis,null);
assert.ok(nodes.some(n=>n.textContent==='All observations · full-source matrix'));
assert.ok(nodes.some(n=>n.tagName==='dt'&&n.textContent==='Genes'));
assert.ok(nodes.some(n=>n.tagName==='td'&&n.textContent==='Not estimated'));
assert.ok(nodes.some(n=>n.tagName==='td'&&n.textContent==='-1'));
const plot=nodes.find(n=>n.tagName==='img'
  && n.attrs.alt==='Descriptive PCA of expression by batch');
assert.ok(plot);
const svg=decodeURIComponent(plot.attrs.src.split(',')[1]);
assert.match(svg,/PC1 \(80% variance\)/);assert.match(svg,/batch &lt;example&gt;/);
assert.match(svg,/scientific validity not established/);
assert.ok(!nodes.some(n=>n.textContent==='Legacy diagnostic summary'));
"""
    )


def test_table_truncation_discloses_server_and_display_limits_without_losing_zero():
    run_js(SETUP + DESCRIPTION + r"""
answer.tables=[{caption:'Empty categories',columns:['label','count'],rows:[],denominator:0,missing_count:0,truncated:true},{caption:'Many columns',columns:Array.from({length:18},(_,i)=>'col_'+i),rows:Array.from({length:53},(_,i)=>Array.from({length:18},(_,j)=>i+'-'+j)),denominator:100,missing_count:null,truncated:false}];
render(investigation);const nodes=walk(container),tables=nodes.filter(n=>n.tagName==='table');
assert.equal(tables.length,2);assert.ok(nodes.some(n=>n.textContent==='Denominator: 0 · Missing: 0'));
assert.ok(nodes.some(n=>/Source result is truncated/.test(n.textContent||'')));
assert.ok(nodes.some(n=>/Showing 50 of 53 rows and 16 of 18 columns/.test(n.textContent||'')));
assert.ok(nodes.some(n=>/Missing: not recorded/.test(n.textContent||'')));
assert.equal(walk(tables[1]).filter(n=>n.tagName==='th').length,16);
assert.equal(walk(tables[1]).find(n=>n.tagName==='tbody').children.length,50);
""")


def test_bounded_table_pages_expose_all_returned_categories_and_survive_polling():
    run_js(SETUP + DESCRIPTION + r"""
answer.tables=[{caption:'Stored labels',columns:['label','count'],rows:Array.from({length:64},(_,i)=>['label-'+i,1]),denominator:64,missing_count:0,truncated:false}];
render(investigation);let host=byClass(container,'wc-answer-table')[0];
assert.equal(walk(host).find(n=>n.tagName==='tbody').children.length,50);
let next=walk(host).find(n=>n.tagName==='button'&&n.textContent==='Next rows');assert.ok(next);next.click();
assert.equal(walk(host).find(n=>n.tagName==='tbody').children.length,14);assert.ok(walk(host).some(n=>n.textContent==='label-63'));
assert.ok(walk(host).some(n=>n.textContent==='Rows 51–64 of 64'));
render({...investigation,revision:3});host=byClass(container,'wc-answer-table')[0];assert.equal(walk(host).find(n=>n.tagName==='tbody').children.length,14);
assert.ok(walk(host).some(n=>n.textContent==='Denominator: 64 · Missing: 0'));
const previous=walk(host).find(n=>n.tagName==='button'&&n.textContent==='Previous rows');previous.click();assert.ok(walk(host).some(n=>n.textContent==='label-0'));assert.ok(!walk(host).some(n=>n.textContent==='label-63'));
""")


def test_source_set_digest_is_not_mislabeled_as_its_first_file_hash():
    run_js(SETUP + DESCRIPTION + r"""
answer.binding={...binding,input_kind:'source_set',input_set_sha256:'c'.repeat(64),source_sha256:null,sources:[{source:'/approved/one/cells.h5ad',sha256:'a'.repeat(64)},{source:'/approved/two/cells.h5ad',sha256:'b'.repeat(64)}]};
answer.tables[0].source='/approved/two/cells.h5ad';answer.tables[0].source_sha256='b'.repeat(64);render(investigation);const values=walk(container).map(n=>n.textContent||'');
assert.ok(values.includes('Source set: 2 files · SHA-256 cccccccccccc'));
assert.ok(!values.includes('Source: cells.h5ad · SHA-256 cccccccccccc'));
assert.ok(values.includes('Table source: /approved/two/cells.h5ad · SHA-256 bbbbbbbbbbbb'));
assert.ok(values.some(value=>value.includes('/approved/one/cells.h5ad')&&value.includes('aaaaaaaaaaaa')));
""")


def test_orphaned_description_recovery_dominates_stale_retained_running_state():
    run_js(SETUP + DESCRIPTION + r"""
investigation.status='recovery_required';investigation.worker_liveness='unknown';investigation.description_operation.status='running';investigation.turns=[];
render(investigation);assert.equal(byClass(container,'is-running').length,0);
assert.ok(!walk(container).some(n=>n.tagName==='button'&&n.textContent==='Cancel description'));
assert.ok(walk(container).some(n=>n.textContent==='Description recovery requires review'));
""")


def test_later_inferential_failure_does_not_relabel_completed_descriptive_evidence():
    run_js(SETUP + DESCRIPTION + r"""
investigation.status='operation_failed';investigation.request.category='ambiguous_or_mixed';investigation.request.remaining_action='inferential_analysis';
investigation.turns.push({id:'failure',kind:'error',step:4,content:{message:'Inferential operation failed'}});
render(investigation);const status=byClass(container,'wc-description-status')[0];
assert.ok(walk(status).some(n=>n.textContent==='Descriptive part available'));
assert.ok(!walk(status).some(n=>n.textContent==='Description failed'));
assert.ok(walk(container).some(n=>n.textContent==='Stored labels\n\nThese are the observed values.'));
""")


def test_orphaned_description_controller_stops_polling_without_live_operation():
    run_controller(r"""
const f=fixture();await f.controller.ready;f.server.record={...f.server.record,status:'recovery_required',operation_pending:false,description_operation:{status:'running'}};
await f.controller.open(parent);assert.equal(f.timers.size,0);
""")


def test_selection_context_never_claims_a_recomputed_subset():
    run_js(SETUP + DESCRIPTION + r"""
answer.binding.population={kind:'selection_context',scope:'full_source_metadata',selected_observation_n:2,selection_sha256:'e'.repeat(64)};
answer.limitations=['Selected rows were not recomputed.'];render(investigation);
const values=walk(container).map(n=>n.textContent||'').join('\n');
assert.match(values,/Selection context: 2 saved observations/);assert.match(values,/full-source metadata/);
assert.match(values,/Selected rows were not recomputed/);assert.ok(!values.includes('Selected subset results'));
""")


@pytest.mark.parametrize('status', ['queued', 'running', 'cancel_requested', 'failed', 'cancelled'])
def test_description_lifecycle_without_job_has_real_state_and_bounded_cancel(status):
    run_js(SETUP + DESCRIPTION + r"""
const status=STATUS;investigation.description_operation.status=status;
investigation.status=({queued:'queued',running:'inspecting',cancel_requested:'cancelling',failed:'operation_failed',cancelled:'cancelled'})[status];
investigation.turns=investigation.turns.slice(0,1);
investigation.turns.push({id:'life',kind:'lifecycle',step:1,content:{state:status,operation_id:'op-1',binding,stages:[{step:1,status:status==='running'?'running':status},{step:2,status:'available'},{step:3,status:'not_applicable'},{step:4,status:'not_run'},{step:5,status:'not_applicable'}]}});
const model=render(investigation),nodes=walk(container),statusCard=byClass(container,'wc-description-status')[0];
assert.ok(statusCard,'A description lifecycle must be visible without a job');assert.equal(statusCard.attrs.role,'status');
assert.equal(sections()[0].attrs['data-workflow-status'],status);
const cancel=nodes.find(n=>n.tagName==='button'&&n.textContent==='Cancel description');
if(status==='queued'||status==='running'){assert.ok(cancel);cancel.click();assert.deepEqual(actions[0],{type:'cancel',investigationId:'chat',expectedRevision:2});}
else assert.equal(cancel,undefined);
if(status==='failed'||status==='cancelled')assert.equal(byClass(container,'is-running').length,0);
assert.equal(model.inlineGate.jobId,null);
""".replace('STATUS', repr(status)))


def test_description_completion_and_mixed_partial_have_distinct_overview_states():
    run_js(SETUP + DESCRIPTION + r"""
render(investigation);
assert.equal(sections()[3].attrs['data-workflow-status'],'completed');
assert.ok(walk(sections()[3]).some(n=>n.textContent==='Description complete'));
assert.equal(sections()[4].attrs['data-workflow-status'],'not_applicable');
assert.ok(!walk(container).some(n=>n.textContent==='Scientific analysis completed'));
investigation.request.category='ambiguous_or_mixed';investigation.request.remaining_action='inferential_analysis';
investigation.status='awaiting_information';answer.completion='partial';
investigation.clarification_fields=[{field:'assay',type:'text',label:'Assay',required:true}];
render(investigation);
assert.equal(sections()[3].attrs['data-workflow-status'],'available');
assert.ok(walk(container).some(n=>n.textContent==='Descriptive part available'));
assert.equal(sections()[4].attrs['data-workflow-status'],'not_run');
assert.ok(walk(container).some(n=>/Remaining request: inferential analysis/.test(n.textContent||'')));
assert.equal(actions.length,0);
""")


def test_replayed_turn_ids_are_deduplicated_and_parent_evidence_retains_its_binding():
    run_js(SETUP + DESCRIPTION + r"""
const earlier={...investigation.turns[1],id:'prior-answer',investigation_id:'parent',content:{...answer,binding:{...binding,source:'/approved/earlier.h5ad',source_sha256:'b'.repeat(64),question_id:'parent'}}};
investigation.thread_turns=[earlier,...investigation.turns,investigation.turns[1],earlier];
const model=render(investigation);assert.equal(model.turns.length,3);
assert.equal(walk(container).filter(n=>n.attrs['data-turn-id']==='a').length,1);
const parent=walk(container).find(n=>n.attrs['data-parent-investigation']==='parent');
assert.ok(walk(parent).some(n=>(n.textContent||"").includes('earlier.h5ad')&&(n.textContent||"").includes('bbbbbbbbbbbb')));
assert.ok(!walk(parent).some(n=>(n.textContent||"").includes('ffffffffffff')));
render(investigation);assert.equal(walk(container).filter(n=>n.attrs['data-turn-id']==='a').length,1);
""")


def test_retained_description_summary_never_becomes_scientific_synthesis():
    run_js(SETUP + DESCRIPTION + r"""
investigation.synthesis={supported_findings:{summary:answer.message},uncertainties:answer.limitations};render(investigation);
assert.ok(!walk(container).some(n=>n.textContent==='Scientific synthesis'));
assert.ok(walk(container).some(n=>n.textContent==='Description summary'));
assert.equal(sections()[4].attrs['data-workflow-status'],'not_applicable');
""")


def test_unavailable_description_requests_attention_at_results_without_scientific_progress():
    run_js(SETUP + DESCRIPTION + r"""
answer.completion='unavailable';answer.tables=[];answer.message='No stored annotation was found.';investigation.status='awaiting_information';
render(investigation);assert.equal(byClass(container,'is-attention')[0].attrs['data-scientific-step'],'4');
assert.equal(sections()[2].attrs['data-workflow-status'],'not_applicable');
assert.equal(sections()[4].attrs['data-workflow-status'],'not_applicable');
assert.ok(!walk(container).some(n=>n.tagName==='form'));
""")


def test_failed_description_exposes_the_actual_error_without_traceback_in_status():
    run_js(SETUP + DESCRIPTION + r"""
investigation.status='operation_failed';investigation.description_operation.status='failed';investigation.turns=[{id:'error',kind:'error',step:4,content:{message:'PermissionError: input is no longer readable\nTraceback: retained details'}}];
render(investigation);const status=byClass(container,'wc-description-status')[0];
assert.ok(walk(status).some(n=>n.textContent==='PermissionError: input is no longer readable'));
assert.ok(!walk(status).some(n=>(n.textContent||'').includes('Traceback')));
""")


def test_description_repaint_preserves_focus_table_scroll_and_conversation_scroll():
    run_js(SETUP + DESCRIPTION + r"""
const matches=(node,selector)=>{const attr=selector.match(/^\[([^\]]+)\]$/);if(attr)return node.attrs[attr[1]]!==undefined;const [tag,...classes]=selector.split('.');return (!tag||node.tagName===tag)&&classes.every(c=>(node.className||'').split(' ').includes(c));};
Node.prototype.append=function(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}};
Node.prototype.replaceChildren=function(...nodes){this.children=[];this.append(...nodes);this.scrollTop=0;if(this.parentElement)this.parentElement.scrollTop=0;};
Node.prototype.querySelectorAll=function(selector){return walk(this).slice(1).filter(n=>selector.split(',').some(s=>matches(n,s)));};
Node.prototype.querySelector=function(selector){return this.querySelectorAll(selector)[0]||null;};
Node.prototype.closest=function(selector){let n=this;while(n){if(matches(n,selector))return n;n=n.parentElement;}return null;};
Node.prototype.contains=function(node){return walk(this).includes(node);};
Node.prototype.focus=function(){document.activeElement=this;};
document.getElementById=id=>walk(container).find(n=>n.attrs.id===id);
const scroller=new Node('div');scroller.append(container);render(investigation);
const region=byClass(container,'wc-answer-table')[0];assert.ok(region.attrs.id,'Table region needs a stable focus identity');region.scrollLeft=111;region.scrollTop=22;region.focus();container.scrollTop=144;scroller.scrollTop=289;
render({...investigation,revision:3});const updated=byClass(container,'wc-answer-table')[0];
assert.equal(document.activeElement,updated);assert.equal(updated.scrollLeft,111);assert.equal(updated.scrollTop,22);assert.equal(container.scrollTop,144);assert.equal(scroller.scrollTop,289);
""")


def test_selected_project_rejects_a_response_bound_to_another_project():
    run_controller(r"""
const f=fixture({config:{model_access:false,investigations:{descriptive_retrieval:true},project_catalog:{current_project_id:'c'.repeat(32)}}});await f.controller.ready;
f.server.record={...f.server.record,project_id:'d'.repeat(32)};
await assert.rejects(f.controller.open(parent),/project/i);assert.equal(f.controller.current(),null);
f.server.record={...f.server.record,project_id:'c'.repeat(32),turns:[{id:'answer',kind:'dataset_answer',content:{binding:{project_id:'d'.repeat(32)}}}]};
await assert.rejects(f.controller.open(parent),/project/i);assert.equal(f.controller.current(),null);
assert.equal(f.jobs.length,0);
""")


def test_history_labels_descriptive_completion_separately_from_mixed_pending_work():
    run_controller(r"""
const f=fixture();await f.controller.ready;
f.server.history=[{id:parent,source,question:'Describe',status:'completed',request:{category:'descriptive_retrieval'},description_operation:{status:'completed'}},{id:child,source,question:'Describe and compare',status:'awaiting_information',request:{category:'ambiguous_or_mixed',remaining_action:'inferential_analysis'},description_operation:{status:'completed'}}];
await f.controller.reloadHistory();assert.ok(walk(f.options.historyContainer).some(n=>n.textContent==='Description complete'));
assert.ok(walk(f.options.historyContainer).some(n=>n.textContent==='Awaiting information'));
assert.equal(walk(f.options.historyContainer).filter(n=>n.textContent==='Description complete').length,1);
""")


def test_description_submission_works_offline_only_with_advertised_server_capability():
    run_controller(r"""
const f=fixture({config:{model_access:false,proposal_selection:{mode:'disabled',available:false},investigations:{descriptive_retrieval:true}}});await f.controller.ready;
f.options.datasetSelect.value=source;f.options.questionInput.value='List the stored annotations';await f.controller.submit();
const sent=f.calls.find(call=>call.body?.question==='List the stored annotations');assert.ok(sent);
assert.equal(sent.body.scientific_context,undefined);assert.equal(sent.body.declarations,undefined);
assert.equal(sent.body.allow_model_execution,undefined);assert.equal(sent.body.allow_data_execution,undefined);
assert.equal(sent.body.proposal_mode,'agent');
""")


def test_description_polling_tracks_operation_changes_and_cancel_without_a_job():
    run_controller(r"""
const f=fixture();await f.controller.ready;
f.server.record={...f.server.record,status:'cancelling',description_operation:{operation_id:'op',status:'cancel_requested'}};
await f.controller.open(parent);assert.equal(f.timers.size,1);const before=f.renders();
f.server.record.description_operation={operation_id:'op',status:'running'};await f.controller.refresh();assert.equal(f.renders(),before+1);
await f.controller.handleAction({type:'cancel',investigationId:parent,expectedRevision:1});
assert.deepEqual(f.calls.find(call=>call.body?.action==='cancel').body,{action:'cancel',expected_revision:1});assert.equal(f.jobs.length,0);
f.server.record={...f.server.record,status:'cancelled',description_operation:{operation_id:'op',status:'cancelled'}};await f.controller.refresh();assert.equal(f.timers.size,0);
""")


def test_stale_description_cancel_cannot_apply_after_revision_change():
    run_controller(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);const before=f.calls.length;
await assert.rejects(f.controller.handleAction({type:'cancel',investigationId:parent,expectedRevision:0}),/changed|revision/i);
assert.equal(f.calls.length,before);
""")


def test_request_question_clarification_stays_outside_biological_declarations():
    run_js(SETUP + r"""
const investigation={id:'chat',revision:3,status:'awaiting_information',question:'How many genes?',declarations:{assay:'scrna'},clarification_fields:[{field:'question',scope:'request',type:'text',label:'Clarify the request',required:true}],turns:[]};
render(investigation);const field=walk(container).find(n=>n.attrs.name==='question');assert.equal(field.value,'How many genes?');
assert.ok(walk(sections()[1]).some(n=>n.tagName==='form'));
assert.equal(walk(container).find(n=>n.textContent==='Update question').disabled,true);
field.value='How many stored genes are in the file?';field.input();walk(container).find(n=>n.tagName==='form').submit({preventDefault(){}});
assert.equal(actions[0].question,'How many stored genes are in the file?');
assert.equal(Object.hasOwn(actions[0],'declarations'),false);
assert.deepEqual(investigation.declarations,{assay:'scrna'});
""")


def test_controller_transports_request_question_clarification_at_top_level():
    run_controller(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);
await f.controller.handleAction({type:'clarify',question:'How many stored genes are in the file?',declarations:{assay:'scrna'}});
const sent=f.calls.find(call=>call.body?.action==='clarify');assert.equal(sent.body.question,'How many stored genes are in the file?');assert.deepEqual(sent.body.declarations,{assay:'scrna'});
""")


def test_http_description_dispatch_uses_actual_selected_project_and_defers(routing):
    apps, _, request, _ = routing
    captured = []

    def create(body, **kwargs):
        captured.append((body, kwargs))
        return {'id': IB, 'status': 'queued'}

    def action(identity, body, **kwargs):
        captured.append((identity, body, kwargs))
        return {'id': IB, 'status': 'queued'}

    apps[B].investigations.create = create
    apps[B].investigations.action = action
    assert request('/api/investigations', project=B, method='POST', body={'question': 'List columns'})[0] == 202
    assert captured[-1][1] == {'defer_description': True, 'project_id': B}
    assert request('/api/investigations/' + IB, project=B, method='POST', body={'action': 'follow_up'})[0] == 202
    assert captured[-1][2] == {'defer_description': True, 'project_id': B}
