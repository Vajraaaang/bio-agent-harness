"""Keep Studio displays concise while preserving receipt semantics."""

from tests.test_workbench_studio_thesis import _run


def test_sccoda_scale_uses_only_supported_interval_data():
    _run(r"""
document.createElementNS = (_, tag) => new Node(tag);
const model = {session_id:sessionId, graph:{entries:[]}, plugin_session:{
  runs:[{status:'completed', operation:'single_cell', summary_tables:[]}],
}};
const table = {caption:'Composition effects',
  columns:['Cell type','Covariate','Posterior inclusion probability','Selected','Coefficient'],
  rows:[['State A','treated',.95,true,Math.log(2)]],
};
const render = value => {
  model.plugin_session.runs[0].summary_tables = [value];
  WorkbenchStudio.renderSession(container,'figures',model,{});
};
render(table);
assert.ok(!findButton(container,'Fold ratio'));
assert.ok(control(container,'Composition effects'));
assert.ok(walk(container).some(el=>el.tagName==='th' && el.scope==='row'));
const withBounds = {
  ...table, columns:[...table.columns,'Credible interval lower','Credible interval upper'],
  rows:[['State A','treated',.95,true,Math.log(2),Math.log(1.5),Math.log(3)]],
};
render(withBounds);
const ratio = findButton(container,'Fold ratio');
assert.ok(ratio);
ratio.onclick();
assert.equal(ratio.getAttribute('aria-pressed'),'true');
const mark = walk(container).find(el=>el.tagName==='g' && el.getAttribute('tabindex')==='0');
assert.match(mark.getAttribute('aria-label'),/2×.*1\.5×.*3×.*PIP 0\.95/);
const tip = walk(container).find(el=>el.getAttribute('role')==='tooltip');
assert.equal(tip.hidden,true);
mark.focus();
assert.equal(tip.hidden,false);
assert.equal(tip.textContent,mark.getAttribute('aria-label'));
mark.keydown({key:'Escape'});
assert.equal(tip.hidden,true);
assert.ok(textIn(control(container,'Composition effects')).includes('0.6931472'));
render({...withBounds,rows:[['State A','treated',.95,true,700,690,800]]});
assert.ok(!findButton(container,'Fold ratio'));
""")


def test_session_rail_uses_the_question_and_one_status():
    _run(r"""
const selected=[];
const session = {origin:'plugin',session_id:sessionId,dataset_label:'cells.h5ad',
  objective:'Compare motor-neuron states',status:'completed',step_count:1,aim_count:0};
const earlier = {...session,session_id:'b'.repeat(32),status:'failed'};
const different = {...session,session_id:'c'.repeat(32),objective:'Inspect capture quality'};
WorkbenchStudio.renderSessionRail(container,[session,earlier,different],sessionId,
  id=>selected.push(id));
const entry = walk(container).find(el=>el.tagName==='button');
assert.deepEqual(textIn(entry),['Compare motor-neuron states','Completed']);
assert.equal(entry.getAttribute('aria-current'),'true');
entry.onclick();
assert.deepEqual(selected,[sessionId]);
const history = walk(container).find(el=>el.tagName==='details');
assert.ok(textIn(history).includes('Failed'));
assert.equal(history.open,false);
WorkbenchStudio.renderSessionRail(container,[session,earlier,different],earlier.session_id,
  id=>selected.push(id));
assert.ok(container.children.some(el=>el.getAttribute('aria-current')==='true' &&
  textIn(el).includes('Failed')));
""")
