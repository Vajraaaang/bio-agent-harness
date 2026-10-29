"""Presentation history has an explicit empty state and retains real attempts."""

from tests.test_workbench_ui_agent_asset import run_script


SETUP = r"""
server.state.status='completed_review_passed';
server.results={report:{cells_input:2,cells_retained:2,HVG_count:1},deterministic_review:{passed:true},artifacts:[]};
server.figures={parent_root:'a'.repeat(64),parent_status:'completed_review_passed',revisions:[],figures:[{
  figure_id:'qc',analysis_n:2,rendered_n:2,labels:[],
  display_options:{width:770,height:550,point_size:4,opacity:0.65,font_size:9,legend_position:'best',category_colors:{},label_positions:{}}
}]};
"""


def test_presentation_history_explains_zero_attempts_without_starting_work():
    run_script(SETUP + r"""
await elements.connect.onclick();await submit();
const history=elements['presentation-versions'];
assert.equal(history.children.length,1);
assert.equal(history.children[0].textContent,'No presentation versions yet.');
assert.equal(history.querySelectorAll('button').length,0);
assert.equal(posts().filter(call=>call.path.endsWith('/run')||call.path.endsWith('/figure-revisions')).length,0);
""")


def test_presentation_history_opens_real_attempt_and_replaces_empty_state():
    run_script(SETUP + r"""
server.figures.revisions=[{id:'revision-fixture',figure_id:'qc',status:'completed_presentation',cancellable:false}];
server.revision={id:'revision-fixture',figure_id:'qc',status:'completed_presentation',figures:[]};
await elements.connect.onclick();await submit();
const history=elements['presentation-versions'];
assert.equal(history.children.length,1);
assert.equal(history.children[0].tagName,'button');
assert.match(history.children[0].textContent,/completed presentation/);
await history.children[0].onclick();
assert.ok(calls.some(call=>call.path==='/api/jobs/fixture-job/figure-revisions/revision-fixture'));
assert.equal(elements['version-select'].value,'revision-fixture');
assert.equal(posts().filter(call=>call.path.endsWith('/run')||call.path.endsWith('/figure-revisions')).length,0);
""")
