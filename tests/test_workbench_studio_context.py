"""The composer frames a bounded draft context without changing source authority."""

import json

from devbench.analysis import workbench_ui
from tests.test_workbench_conversation_redesign import run_js

_MODULE = workbench_ui.ASSET.parent / "workbench-context.js"
_SETUP = f"require({json.dumps(str(_MODULE))});" + r"""
class Node {
  constructor(tag) {this.tagName=tag; this.children=[]; this.attrs={};}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;}
  setAttribute(key,value) {this.attrs[key]=value;}
  addEventListener(event,handler) {this[event]=handler;}
}
globalThis.document = {createElement:tag=>new Node(tag)};
const root = new Node('div'), studio = WorkbenchContext;
const walk = node => [node, ...node.children.flatMap(walk)];
const has = (node, pattern) =>
  walk(node).some(item=>pattern.test(item.textContent||''));
"""


def test_wayfinders_use_declared_assay_and_keep_unknown_inputs_unqualified():
    run_js(_SETUP + r"""
let draft, data = 0;
const render = datasets => studio.renderWayfinders(root,{
  datasets,onDraft:question=>draft=question,onChooseData:()=>data++,
});
render([]);
assert.ok(has(root,/Choose project data to see answerable questions/));
walk(root).find(row=>row.tagName==='button').click();
assert.equal(data,1);
render([{filename:'raw_feature_bc_matrix.h5',representation:'raw_feature_bc_matrix'}]);
assert.ok(has(root,/EmptyDrops/));
walk(root).find(row=>row.tagName==='button').click();
assert.match(draft,/barcode/);
render([{filename:'expression.h5ad',profile:{observed:{file_type:'h5ad'}}}]);
assert.ok(has(root,/pseudobulk/));
assert.ok(!has(root,/COUNT-1/));
render([{filename:'bulk.csv',profile:{declarations:{assay:'bulk_rna_seq'}}}]);
assert.ok(has(root,/CAMERA/));
render([{filename:'bulk.csv'}]);
assert.ok(!has(root,/CAMERA|EmptyDrops/));
const entry=studio.entryForDataset({source:'/private/reads.h5ad',filename:'reads.h5ad'},
  {observed:{file_type:'h5ad',layers:['counts']}});
assert.equal(entry.detail,'AnnData');
assert.equal(entry.label,'reads.h5ad');
""")


def test_scoped_draft_chips_remove_and_cannot_follow_different_datasets():
    run_js(_SETUP + r"""
const removed = [], selections = [];
const context = studio.mount(root,{
  onDatasetRemove:id=>removed.push(id),onSelectionRemove:()=>selections.push(true),
});
const dataset={kind:'dataset',id:'/private/read.h5ad ',label:'read.h5ad',
  detail:'AnnData'};
const selection={kind:'selection',id:'saved',label:'Selected cells (4)'};
context.sync({scope:'first',datasets:[dataset],selection});
assert.ok(context.add({kind:'step',id:'qc',label:'Review QC',generation:2}));
assert.ok(context.add({kind:'aim',id:'aim-a',label:'<script>draft target</script>'}));
const question=context.formatDraft('Check the original contrast.');
assert.match(question,/@step qc.*generation 2/);
assert.match(question,/@aim aim-a/);
assert.doesNotMatch(question,/\/private\/|Selected cells|<script>/);
assert.ok(has(root,/<script>draft target<\/script>/));
assert.ok(walk(root).every(node=>node.innerHTML===undefined));
walk(root).find(node=>node.attrs['aria-label']==='Remove read.h5ad').click();
walk(root).find(node=>node.attrs['aria-label']==='Remove Selected cells (4)').click();
assert.deepEqual(removed,[dataset.id]);
assert.equal(selections.length,1);
context.sync({scope:'first',datasets:[{...dataset,id:'/private/other.h5ad'}]});
assert.equal(context.formatDraft('Original question'),'Original question');
context.add({kind:'step',id:'old',label:'Old step'});
context.sync({scope:'second',datasets:[dataset],readOnly:true});
assert.equal(context.formatDraft('Original question'),'Original question');
assert.equal(context.add({kind:'step',id:'no',label:'Read-only step'}),false);
assert.ok(walk(root).every(node=>node.tagName!=='button'));
""")


def test_synthesis_badges_and_exact_citation_drawer_stay_grounded():
    run_js(_SETUP + r"""
const receipt = 'b'.repeat(64), artifact = 'c'.repeat(64);
const shown=[];
globalThis.WorkbenchStudio={inspectReceipt:value=>shown.push(value)};
const model={id:'investigation',revision:1,status:'completed',question:'Compare groups',
  declarations:{assay:'bulk'},profile:{observed:{file_type:'csv'}},turns:[],
  synthesis:{findings:[{target_id:'t',claim_scope:'admitted_research_inference',
    source:{step_id:'fit',receipt_sha256:receipt,artifact_sha256:artifact}}],
    uncertainties:['Small cohort'],evidence:{status:'verified'}}};
api.renderInvestigation(root,{investigation:model});
assert.ok(has(root,/^Observed$/));
assert.ok(has(root,/^Supplied declaration$/));
assert.ok(has(root,/^Confirmatory inference$/));
assert.ok(has(root,/^Open uncertainties/));
const button=walk(root).find(node=>
  node.tagName==='button' && node.textContent==='fit · bbbbbbbb');
button.click();
assert.deepEqual(shown[0],{receipt_sha256:receipt,artifact_sha256:artifact,
  step_id:'fit',claim_scope:'admitted_research_inference'});
""")


def test_investigation_submission_keeps_exact_sources_and_scopes_read_only():
    from tests.test_workbench_investigation_asset import run_js as run_investigation

    run_investigation(f"require({json.dumps(str(_MODULE))});" + r"""
const contexts=[];
const suffix='\n\nPinned draft context:\n• @step qc';
let formatted = 0;
const f=fixture({
  onContext:value=>contexts.push(value),
  formatContextDraft:question=>{formatted++; return question+suffix;},
});
await f.controller.ready;
f.controller.setSources([source]);
const data = contexts.at(-1).datasets[0];
assert.equal(data.id,source);
assert.equal(data.label,'cells.h5ad');
assert.equal(formatted,0);
f.options.questionInput.value='Inspect the raw measurements';
await f.controller.submit();
assert.equal(formatted,1);
const submitted=f.calls.find(call=>call.path==='/api/investigations' && call.body);
assert.equal(submitted.body.source,source);
assert.equal(submitted.body.question,'Inspect the raw measurements'+suffix);
assert.equal(contexts.at(-1).scope,parent);
assert.deepEqual(Object.keys(submitted.body).sort(),['proposal_mode','question','source']);

const g=fixture({config:{project_catalog:{read_only:true}}});
await g.controller.ready;
await g.controller.open(parent);
g.options.questionInput.value='Unsaved draft';
const count=g.calls.length;
assert.equal(await g.controller.submit(),null);
await assert.rejects(g.controller.handleAction({type:'cancel'}),/read-only/);
await assert.rejects(g.controller.review({decision:'approve'}),/read-only/);
assert.equal(g.calls.length,count);
assert.equal(g.options.questionInput.value,'Unsaved draft');
""")
