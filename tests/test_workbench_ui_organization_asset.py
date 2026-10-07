"""Research navigation and restoration use existing records without executing work."""
from tests.test_workbench_ui_agent_asset import run_script as run
from tests.test_workbench_ui_workspace_asset import FIGURE_SETUP


def test_stable_navigation_and_search_separate_analysis_and_presentation_records():
    run("""
server.jobs=[
 {id:'fixture-job',question:'Native analysis',status:'approved',operation_kind:'analysis',execution_history:[]},
 {id:'render-job',parent_job:'fixture-job',question:'Style revision',status:'operation_failed',operation_kind:'presentation',figure_id:'qc',execution_history:[{attempt:1,elapsed_sec:1}]}
];
await elements.connect.onclick();
assert.equal(elements.overview.hidden,false);
elements['nav-analyses'].onclick();
assert.equal(elements.analyses.hidden,false);
assert.equal(elements['analysis-list'].children.length,1);
assert.match(elements['analysis-list'].children[0].textContent,/Native analysis/);
elements['nav-runs'].onclick();
assert.equal(elements.runs.hidden,false);
assert.equal(elements['run-list'].children.length,1);
assert.match(elements['run-list'].children[0].textContent,/Presentation render/);
elements['workspace-search'].value='not present';elements['workspace-search'].oninput();
assert.match(elements['run-list'].children[0].textContent,/No matching/);
assert.equal(elements.search.hidden,false);
assert.match(elements['search-list'].children[0].textContent,/No matching/);
assert.equal(posts().length,0);
""")


def test_background_status_change_does_not_take_over_project_navigation():
    run("""
await elements.connect.onclick();await submit();
elements['nav-data'].onclick();server.state.status='approved';
await elements.refresh.onclick();
assert.equal(elements.data.hidden,false);
assert.equal(elements.execute.hidden,true);
assert.equal(elements['run-button'].disabled,false);
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,0);
""")


def test_missing_saved_version_is_disclosed_without_replacing_it_with_latest():
    run(FIGURE_SETUP+"""
server.jobs=[{id:'fixture-job',question:'Recorded question',status:'completed_review_failed',operation_kind:'analysis'}];
server.workspace.ui={selected_job:'fixture-job',view:'results',figure:'qc',selected_revision:'missing-version',parent_root:'a'.repeat(64)};
await elements.connect.onclick();
assert.equal(elements['result-view'].hidden,true);
assert.match(elements['workspace-message'].textContent,/saved artifact version is unavailable/);
assert.equal(posts().length,0);
""")


def test_saved_draft_location_and_exact_version_restore_without_workflow_posts():
    run(FIGURE_SETUP+"""
server.jobs=[{id:'fixture-job',question:'Recorded question',status:'completed_review_failed',operation_kind:'analysis',execution_history:[{attempt:1}]}];
server.revision={id:'revision-fixture',figure_id:'qc',status:'completed_presentation',request:{display_options:{point_size:12}}};
server.figures.revisions=[server.revision];
server.workspace.ui={selected_job:'fixture-job',view:'results',figure:'qc',selected_revision:'revision-fixture',parent_root:'a'.repeat(64),tab:'provenance',zoom:'native',question_draft:'Unsubmitted next question',search:'retained'};
await elements.connect.onclick();
assert.equal(elements.results.hidden,false);
assert.equal(elements['figure-select'].value,'qc');
assert.equal(elements['version-select'].value,'revision-fixture');
assert.equal(elements['panel-provenance'].hidden,false);
assert.equal(elements['figure-stage'].dataset.scale,'native');
assert.equal(elements.question.value,'Unsubmitted next question');
assert.match(elements['provenance-record'].textContent,/revision-fixture/);
assert.equal(posts().length,0);
assert.equal(calls.filter(c=>c.path==='/api/workspace'&&c.method==='POST').length,0);
""")


def test_saved_artifact_with_changed_root_is_not_silently_replaced():
    run(FIGURE_SETUP+"""
server.jobs=[{id:'fixture-job',question:'Recorded question',status:'completed_review_failed',operation_kind:'analysis'}];
server.workspace.ui={selected_job:'fixture-job',view:'results',figure:'qc',parent_root:'b'.repeat(64)};
await elements.connect.onclick();
assert.equal(elements['result-view'].hidden,true);
assert.match(elements['workspace-message'].textContent,/saved artifact.*changed|changed.*saved artifact/i);
assert.equal(posts().length,0);
""")


def test_draft_save_is_whitelisted_and_conflict_keeps_local_text():
    run("""
elements.token.value='session-secret';
await elements.connect.onclick();
elements.question.value='Save this unsubmitted draft';
await elements['save-workspace'].onclick();
const writes=calls.filter(c=>c.path==='/api/workspace'&&c.method==='POST');
assert.equal(writes.length,1);
assert.equal(writes[0].body.ui.question_draft,'Save this unsubmitted draft');
assert.ok(!JSON.stringify(writes[0].body).includes('session-secret'));
server.workspaceConflict=true;elements.question.value='Keep the local edit';
await elements['save-workspace'].onclick();
assert.match(elements['workspace-message'].textContent,/another window/);
assert.equal(elements.question.value,'Keep the local edit');
assert.equal(posts().length,0);
""")


def test_data_versions_use_hashes_and_unknown_activity_remains_visible():
    run("""
server.jobs=[
 {id:'one',question:'First question',status:'recovery_required',worker_liveness:'unknown',operation_kind:'analysis',input:{path:'/inputs/data.h5ad',sha256:'a'.repeat(64)},execution_history:[]},
 {id:'two',question:'Second question',status:'approved',operation_kind:'analysis',input:{path:'/moved/data.h5ad',sha256:'a'.repeat(64)},execution_history:[]},
 {id:'three',question:'Third question',status:'approved',operation_kind:'analysis',input:{path:'/inputs/data.h5ad',sha256:'b'.repeat(64)},execution_history:[]}
];
await elements.connect.onclick();elements['nav-data'].onclick();
assert.equal(elements['data-list'].children.length,2);
assert.match(elements['activity-control'].textContent,/1.*attention/);
assert.equal(posts().length,0);
""")


def test_pending_record_selection_cannot_overwrite_later_navigation_or_saved_view():
    run("""
server.jobs=[{id:'fixture-job',question:'Recorded question',status:'approved',operation_kind:'analysis',execution_history:[]}];
server.state.status='approved';
await elements.connect.onclick();elements['nav-analyses'].onclick();
let release;server.beforeJobRead=()=>new Promise(resolve=>release=resolve);
const pending=elements['analysis-list'].children[0].onclick();
await new Promise(resolve=>setImmediate(resolve));
await elements['nav-data'].onclick();release();await pending;
assert.equal(elements.data.hidden,false);
assert.equal(elements.review.hidden,true);
assert.equal(server.workspace.ui.view,'data');
""")


def test_pending_run_response_cannot_take_over_later_navigation():
    run("""
server.state.status='approved';await elements.connect.onclick();await submit();
let release;server.beforeRun=()=>new Promise(resolve=>release=resolve);
const pending=elements['run-button'].onclick();await new Promise(resolve=>setImmediate(resolve));
await elements['nav-data'].onclick();server.state.status='running';release();await pending;
assert.equal(elements.data.hidden,false);
assert.equal(elements.execute.hidden,true);
assert.equal(server.workspace.ui.view,'data');
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,1);
""")


def test_workspace_is_not_interactive_until_restore_finishes():
    run("""
let release;server.beforeWorkspaceRead=()=>new Promise(resolve=>release=resolve);
server.workspace.ui.question_draft='Previously retained draft';
const pending=elements.connect.onclick();await new Promise(resolve=>setImmediate(resolve));
assert.equal(elements.app.hidden,true);
assert.equal(elements.connect.disabled,true);
release();await pending;
assert.equal(elements.app.hidden,false);
assert.equal(elements.connect.disabled,false);
assert.equal(elements.question.value,'Previously retained draft');
""")


def test_approval_requests_are_in_the_attention_count_without_becoming_runs():
    run("""
server.jobs=[{id:'fixture-job',question:'Review required',status:'awaiting_operator_review',operation_kind:'analysis',execution_history:[]}];
await elements.connect.onclick();elements['nav-runs'].onclick();
assert.match(elements['activity-control'].textContent,/1.*attention/);
assert.match(elements['activity-list'].children[0].textContent,/Review required/);
assert.match(elements['run-list'].children[0].textContent,/No matching execution attempts/);
assert.equal(posts().length,0);
""")
