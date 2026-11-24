"""Exercise exact local approval and verified graph result presentation."""

import json

from devbench.analysis import workbench_ui
from tests.test_workbench_studio_thesis import _run


def test_offline_plan_approval_posts_exact_review_and_resumes_polling():
    module = workbench_ui.ASSET.parent / "workbench-investigations.js"
    _run(f"require({json.dumps(str(module))});\n" + r"""
globalThis.WorkbenchResearch = null;
const pending = new Map();
let tick = 0;
globalThis.setTimeout = callback=>{pending.set(++tick,callback);return tick;};
globalThis.clearTimeout = id=>pending.delete(id);
const plan = {plan_id:'b'.repeat(32),plan_sha256:'c'.repeat(64),
  operation:'motor_program_comparison',status:'awaiting_review',can_run:true,
  objective:'Compare provisional motor-neuron programs by stage',
  inputs:[{name:'e10.h5ad',role:'expression'}],parameters:{counts_layer:'counts'},
  dependencies:[{plan_id:'1'.repeat(32),plan_sha256:'2'.repeat(64)}]};
const blocked = {plan_id:'e'.repeat(32),plan_sha256:'f'.repeat(64),
  operation:'composition_summary',status:'blocked',error:'adaptive_dependency_held'};
const view = () => ({session_id:sessionId,mode:'adaptive/1',status:'available',
  private_workflow:{plans:[{...plan},blocked]}});
const path = '/api/scientific/adaptive/'+sessionId;
const calls = [], reached = [];
const controller = WorkbenchInvestigations.mountAdaptivePanel({
  host:container,
  request:async(route,body)=>{
    calls.push([route,body]);
    if (route === path+'/plugin-execute') plan.status = 'queued';
    return view();
  },
  onView:result=>reached.push(result.private_workflow.plans[0].status),
});
await controller.select(sessionId,{initialView:view(),readOnly:true});
assert.ok(!findButton(container,'Approve and run'));
assert.ok(textIn(container).includes('adaptive dependency held'));
await controller.select(sessionId,{
  initialView:view(),readOnly:true,allowOfflineApproval:true,
});
const consent = control(container,'Allow local execution for this plan');
const approve = findButton(container,'Approve and run');
assert.ok(textIn(container).includes(plan.plan_sha256));
for (const value of [plan.objective,'e10.h5ad','counts','2'.repeat(64)])
  assert.ok(textIn(container).includes(value));
assert.equal(approve.disabled,true);
await approve.onclick();
assert.deepEqual(calls,[]);
consent.checked = true;
consent.onchange();
await approve.onclick();
assert.deepEqual(calls,[[path+'/plugin-execute',{
  plan_id:plan.plan_id,plan_sha256:plan.plan_sha256,allow_data_execution:true,
}],[path,undefined]]);
assert.deepEqual(reached,['queued']);
assert.equal(pending.size,1);
plan.status = 'completed';
plan.graph_execution = {receipt_sha256:'a'.repeat(64)};
blocked.status = 'awaiting_review';
blocked.can_run = true;
delete blocked.error;
await pending.get(tick)();
assert.deepEqual(reached,['queued','completed']);
assert.equal(pending.size,0);
assert.ok(findButton(container,'Approve and run'));
assert.ok(walk(container).some(item=>
  item.tagName==='button' && item.title===plan.graph_execution.receipt_sha256));
const nextConsent = control(container,'Allow local execution for this plan');
const nextApprove = findButton(container,'Approve and run');
nextConsent.checked = true;
nextConsent.onchange();
blocked.plan_sha256 = '3'.repeat(64);
await controller.refresh();
await nextApprove.onclick();
assert.equal(calls.filter(([route])=>route.endsWith('/plugin-execute')).length,1);
""")


def test_private_graph_findings_require_verified_completion_and_keep_umap_figures():
    _run(r"""
const sha = 'b'.repeat(64);
const motorPlan = 'c'.repeat(32), markerPlan = 'd'.repeat(32);
const image = '/api/scientific/adaptive/'+sessionId+'/motor-umap/umap/'+sha;
const model = {session_id:sessionId,plugin_linked:true,shared_motor_umap:{
  status:'completed',plan_id:motorPlan,artifacts:{umap:{url:image,sha256:sha}},
},private_workflow:{plans:[{
  plan_id:motorPlan,plan_sha256:sha,operation:'shared_motor_umap',status:'completed',
  graph_execution:{receipt_sha256:sha},
  summary_tables:[{caption:'Duplicate UMAP',columns:['Cells'],rows:[[42]]}],
},{
  plan_id:markerPlan,plan_sha256:sha,operation:'motor_program_comparison',
  status:'completed',graph_execution:{receipt_sha256:sha},
  summary_tables:[{caption:'Verified markers',columns:['Stage','Cells'],
    rows:[['e10',42]]}],
},{
  plan_id:'e'.repeat(32),plan_sha256:sha,operation:'composition_summary',status:'completed',
  summary_tables:[{caption:'Unverified counts',columns:['Count'],rows:[[7]]}],
}]}};
WorkbenchStudio.renderSession(container,'findings',model,{});
assert.ok(textIn(container).includes('Motor-neuron composition by stage'));
assert.ok(textIn(container).includes('Verified markers'));
assert.ok(textIn(container).includes('e10'));
assert.ok(!textIn(container).includes('Unverified counts'));
assert.ok(!textIn(container).includes('Duplicate UMAP'));
const requested = [];
WorkbenchStudio.renderSession(container,'figures',model,{artifact:async path=>{
  requested.push(path);return new Blob(['figure']);
}});
await flush();
assert.deepEqual(requested,[image]);
assert.equal(walk(container).filter(node=>node.tagName==='img').length,1);
model.private_workflow.plans[1].status = 'awaiting_review';
assert.equal(WorkbenchStudio.handoffView('workflows',model),'workflows');
""")
