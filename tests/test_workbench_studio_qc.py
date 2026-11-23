"""Exercise the single-cell cockpit using the bounded Workbench API contract."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ASSETS = Path(__file__).resolve().parents[1] / "src/devbench/analysis/assets"
_SETUP = r"""
const assert = require('node:assert/strict');
class Element {
  constructor(tag) {
    Object.assign(this, {tag, children:[], attrs:{}, dataset:{}, value:'',
      textContent:'', classList:{toggle(){}}, open:false});
  }
  append(...items) {
    this.children.push(...items);
    items.forEach(item=>item.parentElement=this);
    if (this.tag==='select' && this.children.length===items.length && items[0]) {
      this.value=items[0].value;
    }
  }
  insertBefore(item,next) {this.children.splice(this.children.indexOf(next),0,item);}
  replaceChildren(...items) {this.children=items;}
  setAttribute(key,value) {this.attrs[key]=String(value);}
  removeAttribute(key) {delete this.attrs[key];}
  addEventListener(key,fn) {(this.events??={})[key]=fn;}
  fire(key) {
    const result=this.events?.[key]?.({preventDefault(){}});
    if (key==='input') this.parentElement?.fire(key);
    return result;
  }
}
globalThis.document={createElement:tag=>new Element(tag)};
const inspected=[], context=[];
globalThis.WorkbenchStudio={inspectReceipt:value=>inspected.push(value),
  addContext:value=>context.push(value)};
const walk = el => [el,...el.children.flatMap(walk)];
const text = (el,caption) => walk(el).find(item=>item.textContent===caption);
const field = (el,caption) => walk(el).find(item=>item.attrs['aria-label']===caption);
const klass = (el,caption) => walk(el).find(item=>
  (item.className||'').split(' ').includes(caption));
const flush = () => new Promise(resolve=>setImmediate(resolve));
const receipt = '1'.repeat(64);
const specimen = {session_id:'s',step_id:'qc',generation:2,receipt_sha256:receipt,
  artifact_sha256:'2'.repeat(64),
  parameters:{min_genes:10},revision_impact:{total:2,steps:[
    {step_id:'downstream',generation:3,effect:'superseded'},
    {step_id:'review',generation:1,effect:'claim_stale'}]},
  report:{cells_input:100,cells_retained:78,cells_excluded:22,method:'scanpy_QC_log1p_MAD/1',
    min_genes:10,mad_limit:5,max_mito_percent:15,qc_group_key:'capture',
    mitochondrial_features:24,thresholds:[
      {batch:'A',metric:'pct_counts_mt',lower:null,upper:11},
      {batch:'B',metric:'log1p_n_genes_by_counts',lower:2,upper:5}],
    group_retention:{group_key:'capture',groups:[
      {group:'A',cells_input:50,cells_retained:30},
      {group:'B',cells_input:50,cells_retained:48}]}}};
const steps = [{id:'qc',operation:'scrna_batch_qc',status:'succeeded',generation:2,
  dependencies:[],qc_report_available:true,can_revise_qc:true}];
const spec = {id:'scrna_batch_qc',available:true,inputs:[
  {name:'expression',kind:'counts',representation:'raw_counts_h5ad'}],
  parameters_schema:{properties:{min_genes:{type:'integer',minimum:1}}}};
const view = {status:'available',session_id:'s',revision:8,steps,
  stage_steps:[{id:'pathway',operation:'rna_pathway_tested',status:'succeeded'}],
  actions:{revise_qc:true,queue:true},operations:[spec,
    {id:'scrna_droplets',available:false,reason:'pinned_runtime_required'}],
  sources:[{id:'normalized',kind:'counts',representation:'normalized_expression'},
    {id:'raw',kind:'counts',representation:'raw_counts_h5ad',biological_unit:'donor'}]};
async function mount(readOnly, detail=specimen) {
  const host=new Element('main'), requests=[];
  const handle=WorkbenchResearch.mount({host,request:async (url,payload)=>{
    requests.push({url,payload});
    return payload ? {revision:9} : url.includes('?qc_step=') ? detail : view;
  }});
  handle.select('s',{scope:'p',revision:8,readOnly});
  const disclosure=host.children[0];
  disclosure.open=true;
  await disclosure.fire('toggle');
  await flush();
  await text(host,'Review QC').fire('click');
  return {host,requests};
}
"""


def _run(body):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for Workbench cockpit behavior checks")
    modules = "\n".join(
        f"require({json.dumps(str(ASSETS / name))});"
        for name in ("workbench-qc.js", "workbench-research.js")
    )
    source = _SETUP + modules + "(async()=>{\n" + body + (
        "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    )
    result = subprocess.run(
        [node, "-e", source], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_qc_cockpit_displays_exact_group_retention_and_receipts():
    _run(r"""
const {host}=await mount(false);
const rail=klass(host,'studio-qc-stage-rail');
assert.ok(text(rail,'Pathways'));
assert.ok(text(rail,'pinned runtime required'));
const succeeded = text(rail,'succeeded');
assert.ok(succeeded.className.split(' ').includes('studio-status'));
assert.equal(succeeded.attrs['data-status'],'succeeded');
assert.equal(text(rail,'Prerequisites pending').attrs['data-status'],'blocked');
const kpis=klass(host,'studio-qc-kpis');
assert.ok(text(kpis,'78 · 78%'));
const shortDigest=text(kpis,receipt.slice(0,12));
assert.equal(shortDigest.tag,'code');
assert.equal(shortDigest.attrs.title,receipt);
assert.equal(text(kpis,receipt),undefined);
const table=klass(host,'studio-qc-table');
assert.ok(text(table,'Per-capture QC thresholds'));
assert.ok(text(table,'A'));
assert.ok(text(table,'50 → 30'));
assert.ok(text(table,'50 → 48'));
assert.deepEqual(walk(table).filter(n=>n.tag==='meter').map(n=>n.value),[0.6,0.96]);
const audit=text(host,'QC diagnostics and thresholds');
assert.equal(walk(host).find(n=>n.tag==='details' && n.children[0]===audit).open,false);
text(host,'Inspect QC receipt').fire('click');
assert.equal(inspected[0].receipt_sha256,receipt);
assert.equal(inspected[0].step_id,'qc');
assert.equal(inspected[0].artifact_sha256,specimen.artifact_sha256);

const other=await mount(true,{...specimen,report:{...specimen.report,
  group_retention:{...specimen.report.group_retention,group_key:'donor'}}});
assert.equal(walk(other.host).filter(n=>n.tag==='meter').length,0);
assert.ok(text(klass(other.host,'studio-qc-table'),'Retention unavailable'));
assert.equal(text(other.host,'Queue QC reanalysis'),undefined);
assert.equal(text(other.host,'Queue single-cell step'),undefined);
""")


def test_legacy_qc_keeps_receipt_and_thresholds_without_group_retention():
    _run(r"""
const report = {...specimen.report};
delete report.group_retention;
const {host} = await mount(true,{...specimen,report});
assert.ok(text(klass(host,'studio-qc-table'),'A'));
assert.equal(walk(host).filter(n=>n.tag==='meter').length,0);
text(host,'Inspect QC receipt').fire('click');
assert.equal(inspected[0].receipt_sha256,receipt);
""")


def test_qc_reanalysis_diffs_exact_settings_and_requires_a_fresh_grant():
    _run(r"""
const {host,requests}=await mount(false);
const input=field(host,'min genes');
input.value='15';
input.fire('input');
assert.ok(text(klass(host,'studio-qc-diff'),'10 → 15'));
assert.ok(text(klass(host,'studio-qc-impact'),'downstream · Generation 3 superseded'));
assert.ok(text(klass(host,'studio-qc-impact'),
  'review · Claim evidence will become stale'));
const form=walk(host).find(n=>n.tag==='form' && text(n,'Queue QC reanalysis'));
const consent=walk(form).find(n=>n.type==='checkbox');
assert.ok(!consent.checked);
form.fire('submit');
await flush();
assert.ok(!requests.some(r=>r.payload));
consent.checked=true;
form.fire('submit');
await flush();
assert.deepEqual(requests.find(r=>r.payload).payload,{
  action:'revise_qc',step_id:'qc',generation:2,parameters:{min_genes:15},
  allow_data_execution:true,expected_revision:8,
});
""")


def test_qc_composer_shows_only_eligible_typed_port_bindings():
    _run(r"""
const {host}=await mount(false);
const composer=klass(host,'studio-qc-composer');
const source=field(composer,'expression source');
assert.deepEqual(source.children.map(n=>n.textContent),
  ['Choose current evidence','raw']);
assert.ok(text(klass(composer,'studio-qc-port'),'counts · COUNT-1 raw'));
assert.ok(text(composer,'Parameters and contract'));
assert.ok(!walk(composer).find(node=>node.tag==='details').open);
""")


def test_qc_impact_uses_authoritative_generation_and_claim_edges():
    from devbench.platform.adaptive_workflow_state import _State
    from devbench.platform.workbench_curated_actions import _qc_revision_impact

    def step(source=None, use="compute"):
        return SimpleNamespace(
            role="analysis",
            inputs=[] if source is None else [
                SimpleNamespace(source_id=source, source_port="result", use=use)
            ],
        )

    state = _State(
        nodes={
            "qc": step(),
            "fit": step("qc"),
            "pathway": step("fit"),
            "interpretation": step("fit", use="claim"),
            "unrelated": step(),
        },
        generations={
            "qc": 2,
            "fit": 3,
            "pathway": 1,
            "interpretation": 2,
            "unrelated": 2,
        },
    )
    assert _qc_revision_impact(state, "qc") == {
        "steps": [
            {"step_id": "fit", "generation": 3, "effect": "superseded"},
            {"step_id": "interpretation", "generation": 2, "effect": "claim_stale"},
            {"step_id": "pathway", "generation": 1, "effect": "superseded"},
        ],
        "total": 3,
    }
    assert state.generations["qc"] == 2
    assert state.generations["fit"] == 3
    assert not state.tombstones and not state.claim_stale
