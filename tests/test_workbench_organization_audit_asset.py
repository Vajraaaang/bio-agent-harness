"""Honest local labels and explicit restoration notices from the design audit."""
from tests.test_workbench_ui_agent_asset import run_script as run
from tests.test_workbench_ui_workspace_asset import FIGURE_SETUP


def test_recent_work_has_real_type_badges_and_opens_the_exact_presentation():
    run(FIGURE_SETUP+"""
server.jobs=[
 {id:'revision-fixture',parent_job:'fixture-job',question:'QC styling',figure_id:'qc',operation_kind:'presentation',status:'completed_presentation',updated_at:'2026-09-27T11:00:00Z'},
 {id:'fixture-job',question:'Original question',operation_kind:'analysis',status:'completed_review_failed',updated_at:'2026-09-27T10:00:00Z'}
];
server.revision={id:'revision-fixture',figure_id:'qc',status:'completed_presentation',request:{display_options:{point_size:12}}};
server.figures.revisions=[server.revision];
await elements.connect.onclick();
assert.equal(elements.jobs.children.length,2);
assert.equal(elements.jobs.children[0].children[0].textContent,'Presentation');
assert.equal(elements.jobs.children[1].children[0].textContent,'Analysis request');
await elements.jobs.children[0].onclick();
assert.equal(elements['figure-select'].value,'qc');
assert.equal(elements['version-select'].value,'revision-fixture');
assert.equal(posts().length,0);
""")


def test_content_groups_preserve_locations_and_do_not_verify_uninspected_inputs():
    run("""
server.jobs=[
 {id:'one',question:'First registration',status:'approved',input:{path:'/inputs/first.h5ad',sha256:'a'.repeat(64)}},
 {id:'two',question:'Copied content',status:'approved',input:{path:'/inputs/copy.h5ad',sha256:'a'.repeat(64)}},
 {id:'three',question:'Replacement content',status:'approved',input:{path:'/inputs/first.h5ad',sha256:'b'.repeat(64)}},
 {id:'four',question:'Inspection pending',status:'awaiting_information',input:{path:'/inputs/uninspected.h5ad'}}
];
await elements.connect.onclick();elements['nav-data'].onclick();
assert.equal(elements['workspace-title'].textContent,'Inputs');
assert.equal(elements['data-list'].children.length,3);
const content=elements['data-list'].children.find(row=>row.textContent.includes('a'.repeat(64)));
assert.match(content.textContent,/first.h5ad/);assert.match(content.textContent,/copy.h5ad/);
assert.ok(elements['data-list'].children.some(row=>row.textContent.includes('Identity not yet verified')));
assert.equal(calls.filter(call=>call.path.includes('/artifact')).length,0);
assert.equal(posts().length,0);
""")


def test_recovery_notice_is_visible_and_does_not_turn_state_into_authorization():
    run("""
server.workspace.recovery_notice='Recovered the previous saved location and question draft.';
server.workspace.ui={view:'overview',question_draft:'Recovered question'};
await elements.connect.onclick();
assert.match(elements['workspace-message'].textContent,/Recovered/);
assert.equal(elements.question.value,'Recovered question');
assert.equal(elements['run-button'].disabled,true);
assert.equal(posts().length,0);
""")
