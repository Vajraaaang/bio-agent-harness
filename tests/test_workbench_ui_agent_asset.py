"""Isolated browser-script controls; no service, scientific execution or model calls."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ASSET = Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-ui.html"
NODE = shutil.which("node")


def run_script(scenario):
    if NODE is None:
        pytest.skip("Node is required for the isolated UI script checks")
    page = ASSET.read_text()
    script = re.search(r'<script nonce="__NONCE__">(.*?)</script>', page, re.S).group(1)
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Element {
  constructor(tag, attrs = '') {
    this.tagName = tag; this.children = []; this.value = '';
    this.textContent = ''; this.checked = false; this.dataset = {};
    this.hidden = /\bhidden\b/.test(attrs); this.disabled = /\bdisabled\b/.test(attrs);
    this.classList = {toggle() {}}; this.elements = [];
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key,value) {this.attrs=this.attrs||{};this.attrs[key]=String(value);}
  getAttribute(key) {return this.attrs&&this.attrs[key];}
  focus() {this.focused=true;}
  scrollIntoView() {}
  querySelectorAll(tag) {
    return this.children.flatMap(child => [
      ...(child.tagName === tag ? [child] : []), ...child.querySelectorAll(tag)]);
  }
}
const elements = {};
for (const match of page.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
  elements[match[3]] = new Element(match[1], match[2]);
}
for (const match of page.matchAll(/<select\b[^>]*id="([^"]+)"[^>]*>\s*<option value="([^"]*)"/g)) {
  elements[match[1]].value = match[2];
}
elements['review-form'].elements = ['reviewer', 'decision', 'rationale', 'review-button'].map(id => elements[id]);
const calls = [], timers = new Map(); let nextTimer = 0;
const server = {
  workspace: {format:'devbench.workspace/1', revision:0, project:{id:'project-fixture',name:'RNA research',scope:'single_user_local'},ui:{view:'overview',tab:'analyst',zoom:'fit'}},
  jobs: [],
  config: {execution_allowed: true, model_access: false,
    proposal_selection: {mode: 'disabled', available: false, label: 'Agent selection unavailable'}},
  registration: {id: 'fixture-job', status: 'planning', cached: false},
  state: {id: 'fixture-job', question: 'Inspect the registered input', status: 'planning',
    plan_author: 'recorded_fixture', planning_result: null, proposal: null,
    cancellable: false, resumable: false, completed_analysis: false}
};
const context = vm.createContext({
  document: {getElementById: id => elements[id], createElement: tag => new Element(tag), body: new Element('body')},
  location: {hash: '', pathname: '/'}, history: {replaceState() {}},
  URL: {revokeObjectURL() {}, createObjectURL() {return 'blob:fixture';}},
  setTimeout: fn => {timers.set(++nextTimer, fn); return nextTimer;},
  clearTimeout: id => timers.delete(id),
  fetch: async (path, options) => {
    calls.push({path, ...options, body: options.body === undefined ? undefined : JSON.parse(options.body)});
    let data;
    if (path.endsWith('/artifact?name=notebook.ipynb')) data = server.notebook;
    else if (path.includes('/artifact?name=figures%2F') || path.endsWith('/image')) data = {};
    else if (path === '/api/config') data = server.config;
    else if (path === '/api/workspace') {
      if(options.method!=='POST'&&server.beforeWorkspaceRead)await server.beforeWorkspaceRead();
      if (options.method === 'POST') {
        const update=JSON.parse(options.body);
        if(server.workspaceConflict)return {ok:false,status:409,json:async()=>({message:'Workspace changed in another window'})};
        assert.equal(update.expected_revision,server.workspace.revision);
        server.workspace={...server.workspace,revision:server.workspace.revision+1,
          project:{...server.workspace.project,...(update.name?{name:update.name}:{})},
          ui:{...server.workspace.ui,...update.ui}};
      }
      data=server.workspace;
    }
    else if (path === '/api/jobs' && options.method === 'POST') data = server.registration;
    else if (path === '/api/jobs') data = {jobs: server.jobs};
    else if (path === '/api/jobs/' + server.state.id) {
      if (server.beforeJobRead) await server.beforeJobRead();
      data = server.state;
    }
    else if (path === '/api/jobs/fixture-job/cancel' || path === '/api/jobs/fixture-job/resume') data = {};
    else if (path === '/api/jobs/fixture-job/run') {
      if(server.beforeRun)await server.beforeRun();
      data={};
    }
    else if (path === '/api/jobs/fixture-job/results') data = server.results;
    else if (path === '/api/jobs/fixture-job/figures') data = server.figures;
    else if (path === '/api/jobs/fixture-job/figure-revisions' && options.method === 'POST') data = server.revision;
    else if (path === '/api/jobs/fixture-job/figure-revisions/revision-fixture') data = server.revision;
    else throw Error('Unexpected request: ' + path);
    return {ok: true, json: async () => JSON.parse(JSON.stringify(data)), blob: async () => 'fixture'};
  }
});
vm.runInContext(script, context);
const submit = () => elements.registration.onsubmit({preventDefault() {}});
// Existing assertions count scientific workflow mutations; metadata has separate tests.
const posts = () => calls.filter(call => call.method === 'POST' && call.path !== '/api/workspace');
"""
    code = "const page = " + json.dumps(page) + "; const script = " + json.dumps(script) + ";\n" + harness
    code += "\n(async () => {\n" + scenario + "\n})().catch(error => {console.error(error); process.exitCode = 1;});"
    result = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_m2a_presentation_controls_exist_without_scientific_edit_fields():
    run_script("""
for (const id of ['figure-select','figure-evidence','presentation-form','presentation-author',
  'point-size','figure-opacity','figure-font','legend-position','figure-width','figure-height',
  'figure-label','label-x','label-y','presentation-submit','presentation-versions']) {
  assert.ok(elements[id], 'Missing presentation control: '+id);
}
assert.equal(elements['presentation-submit'].disabled,true);
""")


def test_presentation_form_sends_bound_style_request_without_analysis_launch():
    run_script("""
server.state.status='completed_review_failed';
server.results={report:{cells_input:2,cells_retained:2,HVG_count:1},deterministic_review:{passed:false},artifacts:[]};
server.figures={parent_root:'a'.repeat(64),parent_status:'completed_review_failed',revisions:[],figures:[{
  figure_id:'qc',analysis_n:2,rendered_n:2,labels:[],
  display_options:{width:770,height:550,point_size:4,opacity:0.65,font_size:9,legend_position:'best',category_colors:{},label_positions:{}}
}]};
server.revision={id:'revision-fixture',status:'queued',cached:false};
await elements.connect.onclick();await submit();
assert.equal(elements['presentation-submit'].disabled,false);
assert.match(elements['figure-context'].textContent,/completed review failed/);
elements['point-size'].value='12';elements['presentation-author'].value='Fixture operator';
await elements['presentation-form'].onsubmit({preventDefault(){}});
const sent=posts().find(call=>call.path.endsWith('/figure-revisions'));
assert.ok(sent);
assert.deepEqual(Object.keys(sent.body).sort(),['author_label','display_options','expected_parent_root','figure_id']);
assert.equal(sent.body.expected_parent_root,'a'.repeat(64));
assert.equal(sent.body.display_options.point_size,12);
assert.equal(server.figures.figures[0].display_options.point_size,4);
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,0);
assert.match(elements['presentation-message'].textContent,/scientific analysis remains unchanged/);
""")


@pytest.mark.parametrize("mode,label,model_access", [
    ("disabled", "Agent selection unavailable", False),
    ("recorded_fixture", "Recorded fixture selector", False),
    ("codex", "Live Codex selector", True),
])
def test_proposal_mode_is_configured_and_sent_without_changing_the_operator_default(mode, label, model_access):
    run_script(f"""
assert.ok(elements['proposal-mode'], 'The proposal-mode control is missing');
server.config.proposal_selection = {{mode: {json.dumps(mode)}, label: {json.dumps(label)}, available: {str(mode != 'disabled').lower()}}};
server.config.model_access = {str(model_access).lower()};
await elements.connect.onclick();
assert.equal(elements['proposal-mode'].value, 'operator');
assert.equal(elements['agent-proposal'].disabled, {str(mode == 'disabled').lower()});
assert.equal(elements['agent-proposal'].textContent, {json.dumps(label)});
await submit();
assert.equal(posts()[0].body.proposal_mode, 'operator');
if ({json.dumps(mode)} !== 'disabled') {{
  elements['proposal-mode'].value = 'agent';
  elements['proposal-mode'].onchange();
  assert.equal(elements['planning-mode'].textContent, {json.dumps(label)});
  await submit();
  assert.equal(posts()[1].body.proposal_mode, 'agent');
}}
""")


@pytest.mark.parametrize("status", ["awaiting_information", "refusal", "invalid_proposal"])
def test_planner_outcomes_are_visible_without_exposing_a_review_form(status):
    run_script(f"""
assert.ok(elements['planning-record'], 'The planner outcome record is missing');
server.state.status = {json.dumps(status)};
server.state.planning_result = {{status: {json.dumps(status)}, decision: {{questions: ['<script>Missing capture-unit provenance</script>']}}}};
await elements.connect.onclick();
await submit();
assert.equal(elements.plan.hidden, false);
assert.equal(elements['planning-result'].hidden, false);
assert.ok(elements['planning-record'].textContent.includes('<script>Missing capture-unit provenance</script>'));
assert.equal(elements['review-form'].hidden, true);
assert.equal(elements['run-button'].disabled, true);
assert.match(elements['plan-author'].textContent, /recorded fixture/i);
assert.equal(timers.size, 0);
""")


def test_cached_registration_does_not_claim_a_fresh_plan_or_retry():
    run_script("""
server.registration.cached = true;
server.state.status = 'refusal';
await elements.connect.onclick();
await submit();
assert.match(elements['register-message'].textContent, /reus|cached/i);
assert.equal(posts().length, 1);
assert.equal(posts()[0].path, '/api/jobs');
""")


def test_review_requires_a_proposal_and_execution_still_requires_approval():
    run_script("""
assert.ok(elements['plan-author'], 'The recorded plan-author label is missing');
await elements.connect.onclick();
server.state.status = 'awaiting_operator_review';
server.state.proposal = {configuration: {parameters: {workflow: 'existing-profile'}}};
server.state.plan_author = 'codex_agent';
await submit();
assert.equal(elements['review-form'].hidden, false);
assert.equal(elements['review-button'].disabled, false);
assert.equal(elements['run-button'].disabled, true);
assert.match(elements['plan-author'].textContent, /live.*codex/i);
server.state.status = 'approved';
await elements.refresh.onclick();
assert.equal(elements['review-button'].disabled, true);
assert.equal(elements['run-button'].disabled, false);
server.state.proposal = null;
server.state.status = 'awaiting_operator_review';
await elements.refresh.onclick();
assert.equal(elements['review-form'].hidden, true);
assert.equal(elements['review-button'].disabled, true);
""")


def test_cancel_and_resume_follow_server_eligibility_and_do_not_auto_recover():
    run_script("""
assert.ok(elements['cancel-button'] && elements['resume-button'], 'Recovery controls are missing');
await elements.connect.onclick();
await submit();
assert.equal(timers.size, 1, 'Planning should poll the recorded job');
assert.equal(elements['cancel-button'].disabled, true);
assert.equal(elements['resume-button'].disabled, true);
await elements['cancel-button'].onclick();
await elements['resume-button'].onclick();
assert.equal(posts().length, 1, 'Ineligible controls must not send mutations');
server.state.cancellable = true;
await elements.refresh.onclick();
assert.equal(elements['cancel-button'].disabled, false);
await elements['cancel-button'].onclick();
assert.equal(posts().at(-1).path, '/api/jobs/fixture-job/cancel');
assert.deepEqual(posts().at(-1).body, {});
server.state.status = 'cancel_requested';
server.state.cancellable = false;
await elements.refresh.onclick();
assert.equal(timers.size, 1, 'Cancellation should poll until settled');
server.state.status = 'recovery_required';
server.state.worker_liveness = 'unknown';
await elements.refresh.onclick();
assert.equal(elements['resume-button'].disabled, true);
assert.equal(elements['run-button'].disabled, true);
assert.equal(timers.size, 0, 'Unknown recovery must not poll or auto-run');
assert.equal(posts().length, 2);
server.state.status = 'cancelled';
server.state.resumable = true;
await elements.refresh.onclick();
assert.equal(elements['resume-button'].disabled, false);
await elements['resume-button'].onclick();
assert.equal(posts().at(-1).path, '/api/jobs/fixture-job/resume');
assert.deepEqual(posts().at(-1).body, {});
""")


def test_asset_retains_local_token_nonce_and_safe_text_rendering():
    page = ASSET.read_text()
    assert '<style nonce="__NONCE__">' in page
    assert '<script nonce="__NONCE__">' in page
    assert "X-Workbench-Token" in page and "history.replaceState" in page
    assert "credentials:'omit'" in page
    assert "innerHTML" not in page and "http://" not in page and "https://" not in page


def test_switching_jobs_clears_previous_action_eligibility_until_state_arrives():
    run_script("""
await elements.connect.onclick();
server.state.cancellable = true;
await submit();
assert.equal(elements['cancel-button'].disabled, false);
server.state = {...server.state, id: 'other-job', cancellable: false};
server.registration.id = 'other-job';
let release;
server.beforeJobRead = () => new Promise(resolve => {release = resolve;});
const pending = submit();
await new Promise(resolve => setImmediate(resolve));
assert.equal(elements['cancel-button'].disabled, true, 'Old job eligibility must not apply to the new job');
assert.equal(elements['resume-button'].disabled, true);
assert.equal(elements['run-button'].disabled, true);
assert.equal(elements['review-form'].hidden, true);
await elements['cancel-button'].onclick();
assert.equal(posts().length, 2);
release();
await pending;
""")
