"""The investigation stage tabs keep failures away from admitted findings."""

import json
import re

from devbench.analysis import workbench_ui
from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_workflow_accordion import SETUP

RESEARCH_SETUP = SETUP + r"""
Node.prototype.removeAttribute = function(key) { delete this.attrs[key]; };
Node.prototype.classList = {toggle(){}};
Node.prototype.dataset = {};
const sessionId = 'a'.repeat(32);
const aim = {aim_id:'aim',version:2,approved:true,question:'Compare groups'};
const proof = (step_id, status, missing_measurements = []) => ({
  aim_id:aim.aim_id,aim_version:aim.version,step_id,status,missing_measurements,
  reason:missing_measurements.length ? 'missing_measurements' : null,
});
const completedAim = {
  ...aim,status:'supported',blinded_review_required:true,blinded_review:{status:'passed'},
  outcome:{status:'supported',evidence:[{step_id:'fit',receipt_sha256:'b'.repeat(64)}]},
};
const thesisView = {status:'available',session_id:sessionId,revision:8,
  aims:[],answerability:[],actions:{}};
const textIn = node => walk(node).map(item=>item.textContent).filter(Boolean);
const missingIn = node =>
  textIn(node).filter(text=>text.startsWith('Missing measurements'));
const findButton = (node, label) =>
  walk(node).find(item=>item.tagName==='button' && item.textContent===label);
const project = {projectId:'local-project',readOnly:false};
const flush = () => new Promise(resolve=>setImmediate(resolve));
"""


def _run_research(source):
    assets = workbench_ui.ASSET.parent
    modules = "\n".join(
        f"require({json.dumps(str(assets / module))});"
        for module in ("workbench-research.js", "workbench-investigations.js")
    )
    run_js(
        modules + RESEARCH_SETUP + "(async()=>{\n" + source
        + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    )


def test_failed_run_and_findings_stay_in_their_own_stage():
    run_js(SETUP + r"""
const bar = new Node('nav');
const tabs = Array.from({length:3}, (_, i) => Object.assign(new Node('button'), {
  id:'wc-stage-pill-'+(i+1), closest:()=>bar, focus(){}, click(){this.onclick();},
}));
const historyContainer = new Node('div');
const record = {id:'example',revision:1,status:'failed',question:'Compare groups',
  turns:[{id:'failure',kind:'error',step:4,content:{message:'Recorded worker error'}}]};
const renderStage = investigation => api.renderInvestigation(container, {
  investigation,stageButtons:tabs,historyContainer,onAction:value=>actions.push(value),
});
const panel = stage => walk(container).find(n=>n.attrs.id==='wc-stage-content-'+stage);
renderStage(record);
assert.equal(tabs[1].attrs['aria-selected'],'true');
assert.ok(walk(panel(2)).some(n=>n.attrs['data-turn-id']==='failure'));
assert.ok(!walk(panel(3)).some(n=>n.attrs['data-turn-id']==='failure'));
tabs[2].click();
assert.deepEqual(actions.at(-1),{type:'navigate',view:'findings',workflowStage:3});
assert.equal(panel(2).hidden,true);
assert.equal(panel(3).hidden,false);
""")


def test_project_thesis_never_requests_a_null_session():
    _run_research(r"""
const calls = [];
const panel = WorkbenchResearch.mountProject({host:container,request:async path=>{
  calls.push(path);
  return {plans:[]};
}});
await panel.select(project);
await panel.select({...project,projectId:'second-project'});
assert.deepEqual(calls,['/api/scientific','/api/scientific']);
""")


def test_project_uses_latest_proofs_and_current_verified_terminal_outcome():
    _run_research(r"""
const outcomes = ['supported','no_significant_finding'];
for (const outcome of outcomes) {
  const host = new Node('div');
  let currentAim = {...completedAim,status:outcome,
    outcome:{...completedAim.outcome,status:outcome}};
  const missing = [['spliced_counts','unspliced_counts'],['donor_metadata']];
  const abandoned = proof('abandoned','awaiting_information',missing);
  let proofs = [
    proof('superseded','awaiting_information',[['old_measurement']]),
    proof('superseded','ready'),abandoned,
    {...proof('old-version','awaiting_information',[['wrong_version']]),aim_version:1},
  ];
  const panel = WorkbenchResearch.mountProject({host,request:async path=>
    path==='/api/scientific' ? {plans:[{session_id:sessionId}]} :
      {...thesisView,aims:[currentAim],answerability:proofs}
  });
  const group = name => byClass(host,'thesis-aim-group')
    .find(node=>node.children[0].textContent===name);
  await panel.select(project);
  assert.ok(findButton(group('Ready to include'),'Open aim aim'));
  assert.deepEqual(missingIn(group('Ready to include')),[]);
  currentAim = {...currentAim,outcome:{status:outcome,evidence:[]}};
  await panel.select(project);
  const unresolved = group('Needs information');
  assert.ok(findButton(unresolved,'Open aim aim'));
  assert.deepEqual(missingIn(unresolved),[
    'Missing measurements · spliced counts or unspliced counts, donor metadata',
  ]);
  findButton(unresolved,'Open aim aim').click();
  await flush();
  const selected = byClass(host,'thesis-current-session')[0];
  assert.deepEqual(missingIn(selected),[
    'Missing measurements: spliced counts or unspliced counts, donor metadata',
  ]);
  currentAim = {...currentAim,blinded_review:{status:'awaiting_reviews'},
    outcome:{...completedAim.outcome,status:outcome}};
  proofs = proofs.filter(item=>item!==abandoned);
  await panel.select(project);
  assert.ok(findButton(group('Needs review'),'Open aim aim'));
  assert.deepEqual(missingIn(group('Needs review')),[]);
}
""")


def test_project_refresh_to_zero_sessions_clears_the_selected_thesis_panel():
    _run_research(r"""
let plans = [{session_id:sessionId}];
const calls = [];
const panel = WorkbenchResearch.mountProject({host:container,request:async path=>{
  calls.push(path);
  return path==='/api/scientific' ? {plans} : {...thesisView,aims:[completedAim]};
}});
await panel.select(project);
findButton(container,'Open aim aim').click();
await flush();
const selected = byClass(container,'thesis-current-session')[0];
assert.equal(selected.hidden,false);
assert.ok(textIn(selected).includes(aim.question));
plans = [];
await panel.select(project);
await flush();
assert.equal(selected.hidden,true);
assert.ok(!textIn(selected).includes(aim.question));
assert.ok(!calls.includes('/api/scientific/thesis/null'));
""")


def test_inline_adaptive_thesis_offers_current_package_export():
    page = workbench_ui.ASSET.read_text()
    mount = re.search(
        r"WorkbenchInvestigations\.mountAdaptivePanel\(\{(.*?)\}\)\)",
        page,
        re.S,
    )
    _run_research(r"""
const exports = [];
const path = '/api/scientific/thesis/'+sessionId+'/package/'+'c'.repeat(32);
const adaptivePanel = WorkbenchInvestigations.mountAdaptivePanel({
  host:container,download:async value=>{exports.push(value);},
  request:async route=>{
    if (route==='/api/scientific/adaptive/'+sessionId) {
      return {session_id:sessionId,revision:thesisView.revision,
        graph_status:'available',graph:{entries:[]},actions:{}};
    }
    if (route==='/api/scientific/single-cell/'+sessionId) return thesisView;
    assert.equal(route,'/api/scientific/thesis/'+sessionId);
    return {...thesisView,packages:[{
      status:'current',outcome:'supported',root_sha256:'d'.repeat(64),download:path,
    }]};
  },
});
await adaptivePanel.select(sessionId,{scope:project.projectId,readOnly:true});
const research = byClass(container,'research-workflows')[0];
await flush();
const download = findButton(research,'Export research package (.zip)');
assert.ok(download);
await download.click();
assert.deepEqual(exports,[path]);
""")
    assert mount is not None and "download:downloadThesisPackage" in mount.group(1)
