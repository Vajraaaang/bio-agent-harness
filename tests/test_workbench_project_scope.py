"""Project-wide collections are separate from selected-chat outputs."""

import pytest

from tests.test_workbench_investigation_asset import run_js
from tests.test_workbench_navigation_visibility import RETAINED_RESULT
from tests.test_workbench_project_structure import SETUP
from tests.test_workbench_ui_agent_asset import run_script


@pytest.mark.parametrize(
    "destination,title",
    [
        ("analyses", "Project plans"),
        ("runs", "Project runs"),
        ("library", "Project results"),
        ("files", "Project exports"),
    ],
)
def test_project_collection_preserves_chat_and_scope(destination, title):
    run_script(RETAINED_RESULT + r"""
server.config.scientific={enabled:true};
await elements['canvas-evidence'].onclick();
const before=elements['inspector-title'].textContent;
await elements['nav-'+DESTINATION].onclick();
assert.equal(elements['project-dialog'].open,true);
assert.equal(elements['project-dialog-title'].textContent,'RNA research / '+TITLE);
assert.equal(
  elements['project-dialog-scope'].textContent,'Across chats in this project'
);
assert.equal(elements['canvas-detail'].hidden,false);
assert.equal(elements['inspector-title'].textContent,before);
for(const name of ['analyses','runs','library','files']){
  assert.equal(elements[name].hidden,name!==DESTINATION);
}
assert.equal(elements.scientific.hidden,true);
assert.equal(posts().length,0);
""".replace("DESTINATION", repr(destination)).replace("TITLE", repr(title)))


def test_project_rows_include_all_project_chats_and_open_the_exact_owner():
    run_script(SETUP+r"""
const opened=[];
controller.open=async id=>{
  opened.push(id);
  currentChat={id,question:'Second source chat'};
};
server.jobs=[
  {id:'owner-job',question:'Second saved plan',status:'completed_review_passed',
    operation_kind:'analysis',
    execution_history:[{attempt:1,status:'completed_review_passed'}]},
  {id:'other-job',question:'Another saved plan',status:'awaiting_operator_review',
    operation_kind:'analysis',execution_history:[]}
];
await elements['refresh-project'].onclick();
hooks.onHistory([
  {id:'second-chat',question:'Second source chat',job_id:'owner-job'},
  {id:'third-chat',question:'Third source chat',job_id:'other-job'},
]);
await elements['nav-analyses'].onclick();
const plans=elements['analysis-list'].children;
assert.equal(plans.length,2);
const ownedRow=plans.find(row=>row.textContent.includes('Second saved plan'));
assert.ok(ownedRow.textContent.includes('Source chat: Second source chat'));
await ownedRow.onclick();
assert.deepEqual(opened,['second-chat']);
assert.equal(elements['project-dialog'].open,false);
assert.equal(elements['inspector-title'].textContent,'Second source chat');
assert.equal(posts().length,0);
""")


def test_legacy_project_row_does_not_invent_a_source_chat():
    run_script(SETUP+r"""
const opened=[];controller.open=async id=>opened.push(id);
server.jobs=[{id:'fixture-job',question:'Legacy saved plan',
  status:'awaiting_operator_review',operation_kind:'analysis',execution_history:[]}];
await elements['refresh-project'].onclick();hooks.onHistory([]);
await elements['nav-analyses'].onclick();
const row=elements['analysis-list'].children[0];
assert.ok(row.textContent.includes('Source analysis: fixture-'));
assert.ok(!row.textContent.includes('Source chat:'));
await row.onclick();assert.deepEqual(opened,[]);
assert.equal(elements['project-dialog'].open,false);
assert.equal(posts().length,0);
""")


def test_reopening_same_chat_restores_its_job_after_legacy_navigation():
    run_js(r"""
let displayedJob='',bindings=0;
const f=fixture({onJob(id){displayedJob=id;bindings++;}});await f.controller.ready;
const ownedJob='c'.repeat(32);
f.server.record={...f.server.record,job_id:ownedJob,status:'completed_review_passed'};
await f.controller.open(parent);assert.equal(displayedJob,ownedJob);
displayedJob='legacy-analysis';
await f.controller.open(parent);
assert.equal(displayedJob,ownedJob);
assert.equal(bindings,2);
await f.controller.refresh();
assert.equal(bindings,2,'background polling retains the binding cache');
assert.ok(!f.calls.some(call=>call.body!==undefined));
""")


def test_project_navigation_marks_only_the_open_project_collection():
    run_script(SETUP+r"""
const controls=['analyses','runs','library','files'].map(name=>elements['nav-'+name]);
const activeTabs=()=>controls.filter(tab=>tab.getAttribute('aria-selected')==='true');
assert.deepEqual(activeTabs(),[]);
elements['nav-analyses'].setAttribute('aria-selected','true');
elements['nav-library'].setAttribute('aria-selected','true');
await elements['nav-runs'].onclick();
assert.deepEqual(activeTabs(),[elements['nav-runs']]);
assert.equal(elements['nav-runs'].tabIndex,0);
await elements['canvas-figures'].onclick();
assert.deepEqual(activeTabs(),[elements['nav-runs']]);
assert.equal(elements['project-dialog'].open,true);
await elements['close-project-dialog'].onclick();
assert.deepEqual(activeTabs(),[]);
await elements['canvas-figures'].onclick();
assert.deepEqual(activeTabs(),[]);
assert.equal(posts().length,0);
""")


def test_legacy_collection_navigation_keeps_one_matching_highlight():
    run_script(r"""
vm.runInContext(script,context);await startWorkspace();
const controls=['analyses','runs','library','files'].map(name=>elements['nav-'+name]);
const activeTabs=()=>controls.filter(tab=>tab.getAttribute('aria-selected')==='true');
await elements['activity-control'].onclick();
assert.deepEqual(activeTabs(),[elements['nav-runs']]);
await elements['nav-library'].onclick();
assert.deepEqual(activeTabs(),[elements['nav-library']]);
await elements['close-project-dialog'].onclick();
assert.deepEqual(activeTabs(),[]);
assert.equal(posts().length,0);
""")
