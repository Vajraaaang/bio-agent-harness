"""Exercise the thesis studio against the local Workbench contracts."""

import json
import re

from devbench.analysis import workbench_ui
from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_review_findings import RESEARCH_SETUP


def _run(source):
    assets = workbench_ui.ASSET.parent
    modules = (
        "workbench-studio.js", "workbench-thesis-studio.js", "workbench-research.js",
    )
    imports = "\n".join(
        f"require({json.dumps(str(assets / filename))});" for filename in modules
    )
    helpers = r"""
const control = (host, name) => walk(host).find(node=>node.attrs['aria-label']===name);
const set = (host, name, value) => { control(host, name).value = value; };
const press = (host, name) => findButton(host, name).click();
"""
    run_js(
        "globalThis.window=globalThis;\n" + imports + RESEARCH_SETUP + helpers
        + "(async()=>{\n" + source
        + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    )


def test_matrix_keeps_latest_proofs_and_scoped_receipt_links():
    _run(r"""
const seen = [];
globalThis.WorkbenchStudio = {inspectReceipt: receipt => seen.push(receipt)};
const done = {...completedAim,
  question:'Which donors respond?', estimand:'Mean contrast',
  outcome:{status:'supported',evidence:[{step_id:'fit',receipt_sha256:'b'.repeat(64),
    artifact_sha256:'c'.repeat(64)}]}};
const open = {...aim, aim_id:'review-aim', status:'ready', approved:true,
  competing_explanations:[{explanation_id:'batch',status:'stale'}],
  blinded_review_required:true, blinded_review:{status:'needs_evidence',
    assigned_reviewers:3,submitted_reviews:2,receipt_sha256s:['e'.repeat(64)]}};
const request = async path => path==='/api/scientific'
  ? {plans:[{session_id:sessionId}]}
  : {...thesisView, aims:[done,open], answerability:[
      {...proof('fit','awaiting_information',[['old_missing']]),aim_id:open.aim_id},
      {...proof('fit','ready'),aim_id:open.aim_id,proof_sha256:'d'.repeat(64)},
      {...proof('qc','awaiting_information',[['raw_counts','umi_counts']]),
        aim_id:open.aim_id}],
      packages:[{status:'current'},{status:'stale'}]};
const studio = WorkbenchResearch.mountProject({host:container,request});
await studio.select(project);
assert.ok(textIn(container).includes('Total aims'));
press(container,'Evidence Matrix');
const table = control(container,'Aim Evidence Matrix');
assert.ok(table);
assert.ok(textIn(table).includes('Which donors respond?'));
assert.ok(textIn(table).includes('needs evidence'));
assert.ok(textIn(table).includes('2 of 3 assessments'));
assert.ok(textIn(table).includes('raw counts or umi counts'));
assert.ok(!textIn(table).includes('old missing'));
const receipt = walk(table).find(node => node.tagName==='button' &&
  node.textContent.includes('bbbbbbbb'));
assert.ok(receipt);
assert.ok(byClass(table,'studio-receipt-token').includes(receipt));
assert.equal(receipt.getAttribute('title'),'b'.repeat(64));
receipt.click();
assert.equal(seen[0].receipt_sha256,'b'.repeat(64));
assert.equal(seen[0].artifact_sha256,'c'.repeat(64));
press(table,'Open aim aim');
await flush();
assert.ok(textIn(container).includes('Mean contrast'));
press(container,'Board');
assert.ok(byClass(container,'thesis-aim-group').some(group=>!group.hidden));
""")


def test_project_opens_a_sole_thesis_session_and_keeps_ready_reason_neutral():
    _run(r"""
const second = 'f'.repeat(32);
let plans = [{session_id:sessionId}, {session_id:second}];
const view = {...thesisView, aims:[{...aim, status:'ready'}], answerability:[
  {...proof('fit','ready'), reason:'prerequisites_satisfied'},
  proof('qc','awaiting_information',[['donor_metadata']]),
]};
const request = async path => path==='/api/scientific' ? {plans} :
  {...view, session_id:path.split('/').at(-1)};
const studio = WorkbenchResearch.mountProject({host:container,request});
const current = byClass(container,'thesis-current-session')[0];
await studio.select(project);
assert.ok(current.hidden);
plans = plans.slice(0,1);
await studio.select(project);
await flush();
assert.equal(current.hidden,false);
assert.equal(control(container,'Investigation').value,sessionId);
assert.ok(byClass(current,'research-answerability-ready').some(node=>
  node.textContent==='prerequisites satisfied'));
assert.ok(byClass(current,'studio-status').some(node=>
  node.getAttribute('data-status')==='ready' && node.textContent==='ready'));
assert.ok(!byClass(current,'research-hold').some(node=>
  node.textContent==='prerequisites satisfied'));
assert.ok(byClass(current,'research-hold').some(node=>
  node.textContent==='missing measurements'));
""")


def test_package_build_banner_shows_digest_prefix_and_id_without_host_path():
    _run(r"""
const sha256 = '123456789abc' + 'a'.repeat(52);
const packageId = 'c'.repeat(32);
const view = {...thesisView, actions:{synthesize:{enabled:true}}};
const packageResult = {root_sha256:sha256, package_id:packageId,
  directory:'/Users/example/private-thesis-packages/'+packageId};
const request = async (path, action) => action
  ? {package:packageResult,revision:9}
  : view;
const panel = WorkbenchResearch.mount({host:container,request});
panel.select(sessionId,{scope:project.projectId,revision:8,readOnly:false});
await flush();
set(container,'Answer schema path','/project/schema.json');
set(container,'Synthesis specification (JSON)','{"question":"Compare groups"}');
walk(container).find(node=>node.tagName==='form').submit({preventDefault(){}});
await flush();
const status = byClass(container,'message')[0].textContent;
assert.equal(status, 'Research package built · SHA-256 123456789abc · package ' +
  packageId);
assert.ok(!textIn(container).some(value=>value.includes('/Users/example')));
""")


def test_structured_forms_keep_exact_backend_fields():
    _run(r"""
const source = 'a'.repeat(64);
const hypothesis = 'd'.repeat(64);
const host = new Node('form');
const declaration = WorkbenchThesisStudio.aimDefinition(host,{aims:[]});
set(host,'Aim ID','primary');
set(host,'Aim kind','confirmatory');
set(host,'Research question','How does the condition affect these donors?');
set(host,'Estimand','Adjusted condition contrast');
set(host,'Input artifact SHA-256',source);
set(host,'Evaluation artifact SHA-256',source);
set(host,'Multiplicity family ID','primary-family');
set(host,'Hypotheses SHA-256',hypothesis);
set(host,'FDR','0.05');
press(host,'Add competing explanation');
set(host,'Explanation ID','batch');
set(host,'Explanation','One batch drives the result');
set(host,'Discriminating check','Compare batch-stratified estimates');
assert.deepEqual(declaration(),{aim_id:'primary',kind:'confirmatory',
  question:'How does the condition affect these donors?',
  estimand:'Adjusted condition contrast',
  input_sha256s:[source],evaluation_sha256s:[source],blinded_review_required:false,
  competing_explanations:[{explanation_id:'batch',
    explanation:'One batch drives the result',
    discriminating_check:'Compare batch-stratified estimates'}],
  multiplicity:{family_id:'primary-family',aim_ids:['primary'],
    hypotheses_sha256:hypothesis,fdr:0.05}});
const synthesis = new Node('form');
const specification = WorkbenchThesisStudio.synthesisSpecification(
  synthesis,thesisView);
set(synthesis,'Answer schema path','/project/schema.json');
set(synthesis,'Answer schema SHA-256',source);
set(synthesis,'Synthesis question','What does the analysis establish?');
set(synthesis,'Package outcome','partial');
press(synthesis,'Add field binding');
set(synthesis,'Answer field','cells_input');
set(synthesis,'Source step ID','qc');
set(synthesis,'Output port','qc_report');
set(synthesis,'JSON field path','cells.0.total');
set(synthesis,'Observations','Twelve input samples');
press(synthesis,'Add follow-up');
set(synthesis,'Experiment','Collect a second cohort');
set(synthesis,'Uncertainty','Whether the effect replicates');
set(synthesis,'Observable','Direction of the effect');
assert.deepEqual(specification(),{answer_schema:'/project/schema.json', spec:{
  answer_schema_sha256:source,question:'What does the analysis establish?',
  outcome:'partial',
  fields:{cells_input:{step_id:'qc',port:'qc_report',path:['cells',0,'total'],
    projection:'json'}},
  data_notes:{observations:['Twelve input samples']},follow_up:[{
    experiment:'Collect a second cohort',uncertainty:'Whether the effect replicates',
    observable:'Direction of the effect'}]}});
""")


def test_revision_conflict_keeps_draft_and_requires_explicit_revision_reload():
    _run(r"""
const posts = [];
let revision = 8;
const view = {...thesisView, actions:{propose_aim:{enabled:true}}};
const request = async (path, body) => {
  if (!body) return {...view, revision};
  posts.push(body);
  if (body.expected_revision !== revision) {
    throw Object.assign(new Error('operation_unavailable'), {status:409});
  }
  return {revision:++revision};
};
const panel = WorkbenchResearch.mount({host:container,request});
panel.select(sessionId,{scope:'local-project',revision:8,readOnly:false});
await flush();
const submit = async () => {
  set(container,'Aim ID','new-aim');
  set(container,'Research question','Retain my proposed question');
  set(container,'Estimand','Mean donor-level condition effect');
  const form = walk(container).find(node=>node.tagName==='form');
  form.submit({preventDefault(){}});
  await flush();
};
revision = 9;
await submit();
assert.equal(posts[0].expected_revision,8);
assert.equal(control(container,'Research question').value,
  'Retain my proposed question');
press(container,'Reload current evidence');
await flush();
const draft = control(container,'Retained unsaved draft (JSON)');
assert.ok(draft.readOnly);
assert.ok(draft.value.includes('Retain my proposed question'));
assert.ok(!draft.value.includes('expected_revision'));
await submit();
assert.equal(posts[1].expected_revision,9);
assert.equal(control(container,'Retained unsaved draft (JSON)'),undefined);
""")


def test_approved_declaration_projection_honors_embargo(monkeypatch):
    from devbench.platform import workbench_thesis_actions as actions

    approval, external = ("a" * 64, "b" * 64)
    row = {
        "approval": {"aim_sha256": approval},
        "status": "ready",
        "question": "What is the donor-level effect?",
        "estimand": "Adjusted paired contrast",
        "input_sha256s": [external],
        "competing_explanations": [],
    }
    monkeypatch.setattr(actions, "embargoed", lambda _db, _sha: False)
    projected = actions._public_declaration(row, object())
    assert projected["question"] == row["question"]
    assert projected["declaration"]["input_sha256s"] == [external]
    assert actions._public_declaration({**row, "approval": None}, object()) == {}
    monkeypatch.setattr(actions, "embargoed", lambda _db, sha: sha == external)
    assert actions._public_declaration(row, object()) == {}


def test_research_bands_render_together_for_the_exact_session():
    _run(r"""
const calls = [];
const panel = WorkbenchResearch.mount({host:container,request:async path=>{
  calls.push(path);
  return thesisView;
}});
await panel.select(sessionId,{scope:'local-project',revision:8,readOnly:false});
assert.deepEqual(calls,[
  '/api/scientific/thesis/'+sessionId,'/api/scientific/single-cell/'+sessionId,
]);
assert.deepEqual(walk(container).map(node=>node.attrs['data-workflow-band']).filter(Boolean),
  ['design','execution','package']);
assert.ok(!findButton(container,'Thesis'));
assert.ok(!findButton(container,'Single-cell'));
assert.ok(textIn(container).includes('Clinical release is disabled.'));
""")


def test_stale_package_stays_visible_and_has_no_download_action():
    _run(r"""
const downloads = [];
const parent = new Node('section');
WorkbenchThesisStudio.packageTable(parent,[
  {outcome:'partial',status:'current',root_sha256:'a'.repeat(64),download:'/export/a'},
  {outcome:'completed',status:'stale',root_sha256:'b'.repeat(64),download:'/export/b'},
],sessionId,{download: async path=>downloads.push(path)});
const stale = walk(parent).find(node=>node.getAttribute('data-status')==='stale');
assert.ok(textIn(stale).includes('completed research package'));
assert.ok(textIn(stale).includes(
  'Build from current evidence'));
assert.ok(!findButton(stale,'Export research package (.zip)'));
await findButton(parent,'Export research package (.zip)').click();
assert.deepEqual(downloads,['/export/a']);
""")


def test_studio_views_render_bounded_receipts_and_treat_markdown_as_text():
    _run(r"""
document.createElementNS = (_, tag) => new Node(tag);
document.createTextNode = value =>
  Object.assign(new Node('#text'), {textContent:value});
const model = {session_id:sessionId, graph:{entries:[]}, plugin_session:{
  objective:'Compare synthetic groups',
  plans:[{plan_id:sessionId,plan_sha256:'a'.repeat(64)}],
  runs:[{status:'completed', operation:'single_cell',
    summary_tables:[{caption:'QC retention by group',
      columns:['Group','Input','Retained'],
      rows:[['A',80,60]]}],tool_receipts:[{step:'qc',receipt_sha256:'b'.repeat(64)}]}],
}, packages:[{status:'verified',root_sha256:'c'.repeat(64),
  summary_md:'# Summary\n\n- `<script>`\n- <img src=x>',methods_md:'## Methods',
  replay_script:'print(42)'}]};
for (const [tab, content] of Object.entries({overview:'Summary',
  findings:'QC retention by group',
  figures:'Derived from verified numeric receipt table',code:'Frozen plans',
  evidence:'Verified Research Packages'})) {
  const host = new Node('section');
  WorkbenchStudio.renderSession(host, tab, model, {});
  assert.ok(textIn(host).includes(content),tab);
  if (tab==='overview') {
    assert.ok(textIn(host).includes('<script>'));
    assert.ok(!walk(host).some(item=>['img','script'].includes(item.tagName)));
  }
  if (tab==='figures') assert.ok(walk(host).some(item=>item.tagName==='svg'));
}
""")


def test_composed_studio_uses_no_html_assignment_or_external_style_urls():
    page = workbench_ui.render_page("studio-verification")
    script, = re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.S)
    assert "innerHTML" not in script
    styles = re.findall(r"<style\b[^>]*>(.*?)</style>", page, re.S)
    assert styles and all("https://" not in style for style in styles)


def test_studio_presents_retained_comparison_on_its_session_tabs():
    _run(r"""
const title = 'Matched developmental stages';
const path = '/api/scientific/adaptive/'+sessionId+'/comparison/'+'b'.repeat(64)+'.png';
const model = {session_id:sessionId, plugin_linked:true, graph:{entries:[]},
  retained_comparison:{title,
    stages:[{label:'E09',dataset:'e09.h5ad',cells:80},{label:'E10',dataset:'e10.h5ad',cells:40}],
    tables:[{caption:'Motor neuron programs · all cells',
      columns:['Program','E09','E10'],rows:[['Somatic motor','9 (11%)','10 (25%)']]},
    {caption:'Cell states · screened cells',
      columns:['State','E09','E10'],rows:[['Motor-like','12 (20%)','15 (75%)']]}],
    figures:[{title:'Shared UMAP',download_path:path},
      {title:'Other session',download_path:path.replace(sessionId,'c'.repeat(32))},
      {title:'External path',download_path:'https://example.com/out.png'},
      {title:'Unexpected query',download_path:path+'?other=1'}],
    limitations:['Biological replicates remain unverified.']}};
const navigation=[];
const render = tab => WorkbenchStudio.renderSession(container,tab,model,{navigate:tab=>navigation.push(tab)});
render('overview');
assert.ok(textIn(container).includes(title));
assert.ok(textIn(container).includes('e09.h5ad'));
assert.ok(!textIn(container).includes('Completed / failed steps'));
findButton(container,'View findings').onclick();
findButton(container,'View figures').onclick();
assert.deepEqual(navigation,['findings','figures']);
render('findings');
assert.ok(textIn(container).includes('Motor neuron programs · all cells'));
assert.ok(textIn(container).includes('Cell states · screened cells'));
assert.ok(textIn(container).includes('Biological replicates remain unverified.'));
assert.ok(!textIn(container).includes('No current numerical tables are available. Review the recorded workflow and its evidence holds.'));
render('figures');
assert.deepEqual(walk(container).filter(item=>item.tagName==='img')
  .map(item=>[item.src,item.alt]),[[path,'Shared UMAP']]);
assert.ok(textIn(container).includes('Cell states · screened cells'));
assert.equal(WorkbenchStudio.handoffView('workflows',model),'figures');
assert.equal(WorkbenchStudio.handoffView('overview',model),'overview');
assert.equal(WorkbenchStudio.handoffView('findings',model),'findings');
assert.equal(WorkbenchStudio.handoffView('workflows',{...model,plugin_linked:false}),'workflows');
assert.equal(WorkbenchStudio.handoffView('workflows',{...model,retained_comparison:null}),'workflows');
""")
