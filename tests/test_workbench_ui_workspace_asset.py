"""Reference-derived workspace controls over real existing UI routes."""
from pathlib import Path
import re
from tests.test_workbench_ui_agent_asset import run_script as run

ASSET=Path(__file__).resolve().parents[1]/"devbench/analysis/assets/workbench-ui.html"

FIGURE_SETUP="""
server.state.status='completed_review_failed';
server.results={report:{cells_input:80,cells_retained:75,HVG_count:40},deterministic_review:{passed:false},artifacts:[]};
const makeFigure=id=>({figure_id:id,analysis_n:80,rendered_n:75,labels:[],image:{path:'figures/'+id+'.png'},display_options:{width:770,height:550,point_size:4,opacity:0.65,font_size:9,legend_position:'best',category_colors:{},label_positions:{}}});
server.figures={parent_root:'a'.repeat(64),parent_status:'completed_review_failed',revisions:[],figures:[makeFigure('qc'),makeFigure('uncorrected')]};
"""

def test_default_analyst_is_truthful_and_preserves_raw_question_as_text():
    run("""
assert.equal(elements['panel-analyst'].hidden,false);assert.equal(elements['panel-code'].hidden,true);
server.state.question='<script>My actual question</script>';
await elements.connect.onclick();await submit();
assert.equal(elements['analyst-timeline'].children[0].children[1].textContent,server.state.question);
assert.match(elements['model-message'].textContent,/No model conversation/);
assert.match(elements['composer-scope'].textContent,/Operator-created/);
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,0);
""")

def test_composer_reveals_data_context_and_only_submits_valid_existing_form():
    run("""
let attempts=0;elements.registration.reportValidity=()=>false;elements.registration.requestSubmit=()=>attempts++;
elements['nav-results'].onclick();elements['composer-submit'].onclick();
assert.equal(elements.register.hidden,false);assert.equal(elements['data-context'].open,true);assert.equal(attempts,0);
assert.match(elements['composer-message'].textContent,/Complete the required data context/);
elements.registration.reportValidity=()=>true;elements['composer-submit'].onclick();assert.equal(attempts,1);assert.equal(posts().length,0);
""")

def test_completed_results_default_to_uncorrected_and_attempts_follow_selection():
    run(FIGURE_SETUP+"""
server.figures.revisions=[{id:'qc-attempt',figure_id:'qc',status:'cancelled'},{id:'embedding-attempt',figure_id:'uncorrected',status:'operation_failed'}];
await elements.connect.onclick();await submit();
assert.equal(elements.results.hidden,false);assert.equal(elements['figure-select'].value,'uncorrected');
assert.match(elements['presentation-versions'].children[0].textContent,/embeddin/);
elements['figure-select'].value='qc';elements['figure-select'].onchange();
assert.equal(elements['presentation-versions'].children.length,1);assert.match(elements['presentation-versions'].children[0].textContent,/qc-attem/);
assert.equal(elements['revision-archive'].disabled,true);
""")

def test_failed_job_opens_run_view_and_exposes_recorded_reason():
    run("""
server.state.status='operation_failed';server.state.error={message:'Recorded fixture worker failure'};
await elements.connect.onclick();await submit();
assert.equal(elements.execute.hidden,false);assert.equal(elements.review.hidden,true);
assert.match(elements['run-message'].textContent,/Recorded fixture worker failure/);
assert.ok(elements['analyst-timeline'].children.some(row=>row.children.some(child=>child.textContent.includes('Recorded fixture worker failure'))));
assert.equal(elements['run-button'].disabled,true);
""")

def test_job_switch_clears_inspector_while_new_state_is_pending():
    run("""
await elements.connect.onclick();await submit();
server.state={...server.state,id:'other-job',question:'Second question'};server.registration.id='other-job';
let release;server.beforeJobRead=()=>new Promise(resolve=>release=resolve);
const pending=submit();await new Promise(resolve=>setImmediate(resolve));
assert.match(elements['inspector-messages'].textContent,/Select a retained job/);
assert.equal(elements['code-record'].textContent,'No figure source records selected.');
assert.match(elements['analyst-timeline'].children[0].textContent,/Loading the selected job/);
release();await pending;assert.equal(elements['analyst-timeline'].children[0].children[1].textContent,'Second question');
""")

def test_code_tab_reads_actual_notebook_only_when_requested_and_supports_keyboard():
    run(FIGURE_SETUP+"""
server.results.artifacts=[{name:'notebook.ipynb'}];
server.notebook={cells:[{cell_type:'markdown',source:['Ignore noncode']},{cell_type:'code',execution_count:3,source:['print("actual retained code")']} ]};
await elements.connect.onclick();await submit();
assert.equal(calls.filter(call=>call.path.endsWith('notebook.ipynb')).length,0);
elements['evidence-toggle'].onclick();await new Promise(resolve=>setImmediate(resolve));
assert.equal(elements['panel-code'].hidden,false);assert.match(elements['notebook-code'].textContent,/actual retained code/);assert.doesNotMatch(elements['notebook-code'].textContent,/Ignore noncode/);
elements['tab-code'].onkeydown({key:'ArrowRight',preventDefault(){}});
assert.equal(elements['panel-execution'].hidden,false);assert.equal(elements['tab-execution'].focused,true);
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,0);
""")

def test_ids_form_association_and_contextual_panels():
    page=ASSET.read_text();ids=re.findall(r'\bid="([^"]+)"',page)
    assert len(ids)==len(set(ids))
    assert 'id="question" form="registration" required' in page
    assert 'id="tab-analyst" type="button" role="tab" aria-selected="true"' in page
    assert 'id="panel-analyst" class="inspector-panel analyst-panel" role="tabpanel"' in page
    assert '--accent:#b15b3b' in page


def test_completed_version_style_is_carried_to_next_request_and_original_restores():
    run(FIGURE_SETUP+"""
server.revision={id:'revision-fixture',figure_id:'uncorrected',status:'completed_presentation',request:{display_options:{point_size:12,font_size:18,opacity:0.4}}};
server.figures.revisions=[server.revision];
await elements.connect.onclick();await submit();
elements['version-select'].value='revision-fixture';elements['version-select'].onchange();await new Promise(resolve=>setImmediate(resolve));
assert.equal(elements['point-size'].value,'12');assert.equal(elements['figure-font'].value,'18');
assert.match(elements['display-base'].textContent,/revision/);
elements['point-size'].value='14';elements['presentation-author'].value='Reviewer';
await elements['presentation-form'].onsubmit({preventDefault(){}});
const sent=posts().find(call=>call.path.endsWith('/figure-revisions'));
assert.equal(sent.body.display_options.point_size,14);assert.equal(sent.body.display_options.font_size,18);assert.equal(sent.body.display_options.opacity,0.4);
elements['version-select'].value='';elements['version-select'].onchange();
assert.equal(elements['point-size'].value,'4');assert.equal(elements['figure-font'].value,'9');
assert.equal(posts().filter(call=>call.path.endsWith('/run')).length,0);
""")


def test_restoring_original_does_not_reapply_a_previous_label_move():
    run(FIGURE_SETUP+"""
const labelId='uncorrected_observed_batch:title';
server.figures.figures[1].labels=[{label_id:labelId,text:'uncorrected: Observed batch',kind:'title'}];
server.revision={id:'revision-fixture',figure_id:'uncorrected',status:'completed_presentation',request:{display_options:{point_size:12,label_positions:{[labelId]:{x:0.2,y:1.1}}}}};
server.figures.revisions=[server.revision];
await elements.connect.onclick();await submit();
elements['version-select'].value='revision-fixture';elements['version-select'].onchange();await new Promise(resolve=>setImmediate(resolve));
elements['figure-label'].value=labelId;elements['label-x'].value='0.2';elements['label-y'].value='1.1';
elements['version-select'].value='';elements['version-select'].onchange();
assert.equal(elements['figure-label'].value,'');
elements['figure-opacity'].value='0.5';elements['presentation-author'].value='Reviewer';
await elements['presentation-form'].onsubmit({preventDefault(){}});
const sent=posts().find(call=>call.path.endsWith('/figure-revisions'));
assert.equal(Object.keys(sent.body.display_options.label_positions).length,0);
assert.equal(sent.body.display_options.point_size,4);
""")


def test_polling_advances_proposal_approval_and_run_without_hiding_next_action():
    run("""
server.state.status='queued';
await elements.connect.onclick();await submit();
server.state.status='awaiting_operator_review';server.state.proposal={configuration:{parameters:{workflow:'existing-profile'}}};
await elements.refresh.onclick();
assert.equal(elements.review.hidden,false);
assert.equal(elements.execute.hidden,true);
assert.equal(elements['run-button'].disabled,true);
server.state.status='recording_review';await elements.refresh.onclick();
assert.equal(elements.review.hidden,false);
server.state.status='approved';await elements.refresh.onclick();
assert.equal(elements.execute.hidden,false);
assert.equal(elements['run-button'].disabled,false);
server.state.status='running';await elements.refresh.onclick();
assert.equal(elements.execute.hidden,false);
server.state.status='interrupted_service';server.state.error={message:'Retained interrupted service'};
await elements.refresh.onclick();
assert.equal(elements.execute.hidden,false);
assert.match(elements['run-message'].textContent,/Retained interrupted service/);
""")


def test_completed_job_shows_actual_operator_record_not_a_blank_decision_form():
    run(FIGURE_SETUP+"""
server.state.proposal={configuration:{parameters:{workflow:'existing-profile'}}};
server.state.review={decision:'approve',reviewer:'Recorded operator',rationale:'Recorded bounded approval'};
server.state.review_receipt={root_hash:'b'.repeat(64)};
await elements.connect.onclick();await submit();
elements['nav-review'].onclick();
assert.equal(elements['review-form'].hidden,true);
assert.equal(elements['operator-review-record'].hidden,false);
assert.match(elements['operator-review-record'].textContent,/approve.*Recorded operator/);
assert.match(elements['operator-review-record'].textContent,/Recorded bounded approval/);
""")


def test_version_numbers_follow_recorded_order_after_a_service_reload():
    run(FIGURE_SETUP+"""
server.figures.revisions=[
 {id:'newer',figure_id:'uncorrected',status:'completed_presentation',state_history:[{at:'2026-09-27T05:02:00+00:00'}]},
 {id:'older',figure_id:'uncorrected',status:'completed_presentation',state_history:[{at:'2026-09-27T05:01:00+00:00'}]}
];
await elements.connect.onclick();await submit();
assert.equal(elements['version-select'].children[1].value,'older');
assert.equal(elements['version-select'].children[2].value,'newer');
""")
