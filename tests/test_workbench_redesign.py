"""Integration boundaries for trusted local workbench presentation assets."""
import re
import subprocess
import shutil

from devbench.analysis import workbench_ui
from scripts.build_colab_notebook import allowed_bundle_path


def test_composed_page_retains_nonce_and_trusted_modules():
    page = workbench_ui.render_page("redesign-test-nonce")
    assert "__NONCE__" not in page
    assert "/*__WORKBENCH_MODULES__*/" not in page
    assert "/*__WORKBENCH_THEME__*/" not in page
    assert "WorkbenchConversation" in page and "ScientificFigure" in page
    assert 'nonce="redesign-test-nonce"' in page
    assert "https://" not in re.search(r"<style.*?>(.*?)</style>", page, re.S).group(1)
    node = shutil.which("node")
    if node:
        script = re.search(r"<script.*?>(.*?)</script>", page, re.S).group(1)
        result = subprocess.run([node, "--check"], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


def test_redesign_assets_are_exactly_packaged_and_allowlisted():
    import tomllib
    from pathlib import Path
    root = Path(workbench_ui.__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    packaged = config["tool"]["setuptools"]["package-data"]["devbench"]
    for name in ["workbench-theme.css", "workbench-conversation.js", "workbench-investigations.js", "workbench-artifacts.js", "scientific-figure.js"]:
        assert allowed_bundle_path("devbench/analysis/assets/" + name)
        assert "analysis/assets/" + name in packaged
        assert (workbench_ui.ASSET.parent / name).is_file()
    assert not allowed_bundle_path("devbench/analysis/assets/arbitrary.js")


def test_primary_conversation_and_figure_context_remain_explicit():
    page = workbench_ui.ASSET.read_text()
    assert 'id="draft-context"' in page
    assert 'id="figure-mode"' in page
    assert 'id="theme-select"' in page
    assert "record.plot_data.path" in page
    assert "display_only" in page


def test_followup_binds_source_context_and_clears_unknown_context():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
let options;
context.window={WorkbenchConversation:{render(_container,value){options=value;}}};
vm.runInContext(script,context);
server.state.proposal={external_input:{path:'/approved/source-a.h5ad',sha256:'a'.repeat(64)},
  configuration:{declarations:{assay:'scrna',measurement:'umi_counts',count_provenance:'A counts',
    capture_unit_key:'donor_a',capture_units_confirmed:true,annotation_provenance:'A annotations'}}};
await elements.connect.onclick();await submit();
elements.source.value='/approved/unrelated-b.h5ad';elements.digest.value='b'.repeat(64);
options.onAction({type:'draft',question:'Inspect source A',sourceRunId:'fixture-job'});
assert.equal(elements.source.value,'/approved/source-a.h5ad');
assert.equal(elements.digest.value,'a'.repeat(64));
assert.equal(elements.capture.value,'donor_a');
assert.equal(elements['capture-confirmed'].checked,true);
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,0);
options.onAction({type:'draft',question:'Unknown source',sourceRunId:'missing-run'});
assert.equal(elements.source.value,'');assert.equal(elements.digest.value,'');
assert.equal(elements.counts.value,'');assert.equal(elements['capture-confirmed'].checked,false);
""")


def test_interactive_figure_returns_after_navigation_without_analysis_mutation():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
let mounts=0,destroys=0;
context.window={ScientificFigure:{mount(){mounts++;return {destroy(){destroys++;},redraw(){}};}}};
vm.runInContext(script,context);
server.state.status='completed_review_passed';
server.results={report:{cells_input:2,cells_retained:2,HVG_count:1},deterministic_review:{passed:true},artifacts:[]};
server.figures={parent_root:'a'.repeat(64),parent_status:'completed_review_passed',revisions:[],figures:[{
  figure_id:'uncorrected',analysis_n:2,rendered_n:2,labels:[],image:{path:'figures/uncorrected.png'},
  plot_data:{path:'figures/uncorrected-data.json'},display_options:{point_size:4}}]};
await elements.connect.onclick();await submit();
await elements['figure-mode'].onchange();
assert.ok(mounts>=1);assert.equal(elements['interactive-figure'].hidden,false);
await elements['nav-review'].onclick();assert.ok(destroys>=1);assert.equal(elements.figures.hidden,false);
await elements['nav-results'].onclick();await elements['figure-mode'].onchange();
await Promise.resolve();await Promise.resolve();
assert.ok(mounts>=2);assert.equal(elements['interactive-figure'].hidden,false);
assert.equal(posts().filter(call=>call.path.endsWith('/run')||call.path.endsWith('/review')).length,0);
""")


def test_workbench_chrome_uses_requested_monochrome_palette():
    theme = (workbench_ui.ASSET.parent / "workbench-theme.css").read_text()
    for value in re.findall(r"--[\w-]+\s*:\s*(#[0-9a-f]{6})\b", theme, re.I):
        assert value[1:3].lower() == value[3:5].lower() == value[5:7].lower(), value
    for red, green, blue in re.findall(r"rgba?\((\d+),(\d+),(\d+),", theme):
        assert red == green == blue
    assert "--accent: #242424" in theme
    assert "--accent: #ededed" in theme


def test_panel_dividers_resize_with_pointer_keyboard_and_local_persistence():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
let width=1200;const properties={},stored=new Map(),listeners={};
context.window={addEventListener(name,handler){listeners[name]=handler;},localStorage:{getItem(key){return stored.get(key)||null;},setItem(key,value){stored.set(key,value);}}};
elements.app.style={setProperty(name,value){properties[name]=value;}};
elements.app.getBoundingClientRect=()=>({width});
for(const id of ['rail-divider','investigation-divider']){
  const node=elements[id];node.setPointerCapture=pointer=>{node.pointer=pointer;};node.hasPointerCapture=pointer=>node.pointer===pointer;node.releasePointerCapture=()=>{node.pointer=null;};
}
vm.runInContext(script,context);
const rail=elements['rail-divider'],investigation=elements['investigation-divider'];
let prevented=0;const event=extra=>({button:0,isPrimary:true,pointerId:1,clientX:200,preventDefault(){prevented++;},...extra});
assert.equal(properties['--rail-width'],'196px');
rail.onpointerdown(event());rail.onpointermove(event({clientX:260}));rail.onpointerup(event({clientX:260}));
assert.equal(properties['--rail-width'],'256px');assert.equal(rail.pointer,null);
assert.equal(elements.app.dataset.resizing,undefined);
assert.equal(JSON.parse(stored.get('rna-workbench-panel-widths-v1')).rail,256);
const before=parseFloat(properties['--investigation-width']);
investigation.onkeydown(event({key:'ArrowRight',shiftKey:true}));
assert.equal(parseFloat(properties['--investigation-width']),before+50);
investigation.onkeydown(event({key:'End'}));assert.equal(properties['--investigation-width'],'608px');
width=900;listeners.resize();assert.equal(properties['--investigation-width'],'308px');
rail.onkeydown(event({key:'Home'}));assert.equal(properties['--rail-width'],'156px');
vm.runInContext(script,context);assert.equal(properties['--rail-width'],'156px');
assert.ok(prevented>=4);assert.equal(posts().length,0);
""")


def test_panel_separators_and_redundant_copy_have_responsive_boundaries():
    page=workbench_ui.ASSET.read_text()
    for name in ('rail-divider','investigation-divider'):
        assert re.search(r'id="'+name+r'"[^>]*role="separator"[^>]*aria-orientation="vertical"[^>]*tabindex="0"',page)
    assert 'class="secondary project-context-button"' in page
    assert '<strong id="project-name">' in page
    assert '<footer class="footer" hidden>' in page
    theme=(workbench_ui.ASSET.parent/'workbench-theme.css').read_text()
    assert re.search(r'@media\(max-width:860px\)\s*{\s*\.panel-divider\s*{\s*display:\s*none',theme)


def test_accepted_question_clears_composer_but_inflight_edit_survives():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
await elements.connect.onclick();elements.question.value='Inspect recorded quality';
await submit();assert.equal(elements.question.value,'');
const originalFetch=context.fetch;let release;
context.fetch=async(path,options)=>{if(path==='/api/jobs'&&options.method==='POST')await new Promise(resolve=>{release=resolve;});return originalFetch(path,options);};
elements.question.value='First submitted question';const pending=submit();
await Promise.resolve();elements.question.value='An actively edited follow-up';release();await pending;
assert.equal(elements.question.value,'An actively edited follow-up');
""")


def test_stage_transitions_are_native_guarded_and_never_dispatch_science():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
let transitions=0;
context.document.startViewTransition=update=>{transitions++;const done=Promise.resolve(update());return {updateCallbackDone:done,finished:done,skipTransition(){}};};
await elements.connect.onclick();await elements['nav-review'].onclick();
assert.equal(transitions,1);assert.equal(elements.review.hidden,false);
context.matchMedia=()=>({matches:true});await elements['nav-results'].onclick();
assert.equal(transitions,1);assert.equal(elements.results.hidden,false);
assert.equal(posts().length,0);
""")


def test_investigation_status_persists_a_cleared_submitted_draft():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
let host;
context.window={WorkbenchInvestigations:{mount(options){host=options;return {ready:Promise.resolve(),current(){return null;},render(){}};}}};
vm.runInContext(script,context);
server.config.investigations={enabled:true};
server.workspace.ui.question_draft='Previously submitted question';
await elements.connect.onclick();assert.ok(host);
elements.question.value='';host.onStatus('Question recorded.',false);
for(let tick=0;tick<12;tick++)await Promise.resolve();
assert.equal(server.workspace.ui.question_draft,'');
assert.ok(calls.some(call=>call.path==='/api/workspace'&&call.method==='POST'&&call.body.ui.question_draft===''));
assert.equal(posts().length,0);
""")


def test_new_job_requires_fresh_operator_confirmations_without_resetting_same_job_edits():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
await elements.connect.onclick();await submit();
elements.reviewer.value='Same local operator';elements.decision.value='approve';elements.rationale.value='Earlier question rationale';
for(const input of elements.confirmations.querySelectorAll('input'))input.checked=true;
server.state={...server.state,id:'second-job',question:'A separate question'};server.registration={...server.registration,id:'second-job'};
await submit();
assert.equal(elements.reviewer.value,'Same local operator');
assert.equal(elements.decision.value,'needs_information');assert.equal(elements.rationale.value,'');
assert.ok(elements.confirmations.querySelectorAll('input').every(input=>!input.checked));
elements.rationale.value='An active review draft';for(const input of elements.confirmations.querySelectorAll('input'))input.checked=true;
await elements.refresh.onclick();assert.equal(elements.rationale.value,'An active review draft');
assert.ok(elements.confirmations.querySelectorAll('input').every(input=>input.checked));
assert.equal(posts().filter(call=>call.path.endsWith('/run')||call.path.endsWith('/review')).length,0);
""")


def test_composer_from_a_legacy_job_uses_a_fresh_investigation_with_source_context():
    from tests.test_workbench_ui_agent_asset import run_script
    run_script(r"""
let host,submitted=0,sourceAction;
context.window={WorkbenchInvestigations:{mount(options){host=options;return {ready:Promise.resolve(),current(){return null;},render(){},
  newThread(){elements.question.value='';host.onNewThread();},
  async handleAction(action){sourceAction=action;elements.question.value=action.question;},
  async submit(){submitted++;}};}}};
vm.runInContext(script,context);server.config.investigations={enabled:true};
server.state.source='/approved/retained.h5ad';server.jobs=[{id:'fixture-job',question:'Retained question',status:'awaiting_operator_review'}];
await elements.connect.onclick();await elements.jobs.children[0].onclick();
elements['investigation-dataset'].value='/approved/retained.h5ad';elements.question.value='A new question about this retained input';
await elements['composer-submit'].onclick();
assert.equal(submitted,1);assert.equal(sourceAction.sourceRunId,'fixture-job');
assert.equal(sourceAction.input.path,'/approved/retained.h5ad');
assert.equal(elements.question.value,'A new question about this retained input');
assert.equal(posts().filter(call=>call.path==='/api/jobs').length,0);
""")
