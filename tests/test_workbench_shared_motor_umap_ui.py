"""Exercise the scoped companion routes and the shared UMAP Studio views."""

import json
import sys
from types import SimpleNamespace

import pytest

from devbench.analysis import workbench_ui
from tests.test_scientific_workspace import setup_workspace
from tests.test_scientific_workspace_http import call, running
from tests.test_workbench_studio_thesis import _run


@pytest.mark.wal
def test_shared_umap_routes_require_offline_studio(workspace_tmp_path, monkeypatch):
    app, *_ = setup_workspace(workspace_tmp_path)
    sid, plan_id, sha = "a" * 32, "b" * 32, "c" * 64
    calls = []
    img_bytes = b"verified raster"
    request = {"plan_id": plan_id, "plan_sha256": sha, "allow_data_execution": True}

    def launch(owner, session, body):
        calls.append(("launch", owner, session, body))
        return {"status": "queued"}

    def artifact(owner, session, kind, digest):
        calls.append(("artifact", owner, session, kind, digest))
        return img_bytes, "image/png", "shared_motor_umap.png"

    monkeypatch.setitem(
        sys.modules, "devbench.platform.plugin_motor_umap",
        SimpleNamespace(studio_launch=launch, studio_artifact=artifact),
    )
    route = f"/api/scientific/adaptive/{sid}/motor-umap"
    figure = f"{route}/umap/{sha}"
    try:
        with running(app) as port:
            origin = f"http://127.0.0.1:{port}"
            assert call(port, route, body=request, origin=origin)[0] == 403
            assert call(port, figure)[0] == 403
            assert calls == []
            app.offline_only = True
            assert call(port, figure, origin="https://example.invalid")[0] == 403
            assert call(port, figure + "?other=1")[0] == 404
            assert call(port, f"{route}/unlisted/{sha}")[0] == 404
            app.allow_data_execution = False
            assert call(port, route, body=request, origin=origin)[0] == 403
            app.allow_data_execution = True
            assert call(port, route, body=request)[0] == 403
            status, body, _ = call(
                port, route, body=request, origin=origin,
            )
            assert status == 202 and json.loads(body) == {"status": "queued"}
            assert call(port, figure) == (200, img_bytes, "image/png")
            assert calls == [
                ("launch", app, sid, request), ("artifact", app, sid, "umap", sha),
            ]
    finally:
        app.close()


@pytest.mark.wal
def test_plotly_frame_scope(workspace_tmp_path, monkeypatch):
    from urllib.request import urlopen

    app, *_ = setup_workspace(workspace_tmp_path)
    sid, sha = "a" * 32, "b" * 64
    seen = []

    def artifact(owner, session_id, kind, checksum):
        seen.append((owner, session_id, kind, checksum))
        return b"verified coordinates", "text/csv", "coordinates.csv"

    def render(figure, theme):
        assert figure == {"data": ["verified coordinates"]}
        return theme.encode(), "chart-test-nonce"

    monkeypatch.setitem(
        sys.modules, "devbench.platform.plugin_motor_umap",
        SimpleNamespace(studio_artifact=artifact),
    )
    monkeypatch.setitem(
        sys.modules, "devbench.platform.workbench_plotly",
        SimpleNamespace(
            motor_umap_figure=lambda value: {"data": [value.decode()]},
            render_plotly_frame=render,
        ),
    )
    path = f"/api/scientific/adaptive/{sid}/motor-umap/{sha}/plotly"
    try:
        with running(app) as port:
            assert call(port, path)[0] == 403
            assert not seen
            app.offline_only = True
            assert call(port, path + "?theme=dark&theme=light")[0] == 400
            assert call(port, path + "?project_id=" + sid)[0] == 400
            assert call(port, path, origin="https://example.invalid")[0] == 403
            chart_url = f"http://127.0.0.1:{port}{path}?theme=dark"
            with urlopen(chart_url, timeout=5) as reply:
                assert reply.read() == b"dark"
                assert reply.headers["X-Frame-Options"] == "SAMEORIGIN"
                policy = reply.headers["Content-Security-Policy"]
                assert "sandbox allow-scripts" in policy
                assert "script-src 'nonce-chart-test-nonce'" in policy
                assert "unsafe-eval" not in policy
                assert "connect-src 'self'" not in policy
            assert seen == [(app, sid, "coordinates", sha)]
    finally:
        app.close()


def test_shared_umap_figures_and_findings_use_only_same_session_artifacts():
    _run(r"""
const sha = 'b'.repeat(64);
const url = (kind, sid=sessionId) => '/api/scientific/adaptive/'+sid+'/motor-umap/'+
  kind+'/'+sha;
const model = {session_id:sessionId, plugin_linked:true, graph:{entries:[]},
  shared_motor_umap:{
  status:'completed', cells:254, features:250, plan_id:'a'.repeat(32), plan_sha256:sha,
  stages:[
    {stage:'e09',all_cells:130,motor_cells:110,neuroblast:110,neuron:0,neuron_share_of_motor:0},
    {stage:'e10',all_cells:100,motor_cells:80,neuroblast:40,neuron:40,neuron_share_of_motor:.5},
    {stage:'e11',all_cells:80,motor_cells:64,neuroblast:10,neuron:54,neuron_share_of_motor:.84375}
  ],
  artifacts:{
    umap:{url:url('umap'),sha256:sha},
    markers:{url:url('markers','c'.repeat(32)),sha256:sha},
    coordinates:{url:'https://example.invalid/a.csv',sha256:sha},
    composition:{url:url('composition'),sha256:sha},
    parameters:{url:url('parameters')+'?x=1',sha256:sha}
  }
}};
const requested = [];
const options = {artifact:async(path, type)=>{
  requested.push([path,type]);return new Blob(['f']);
}};
WorkbenchStudio.renderSession(container,'figures',model,options);
await flush();
assert.deepEqual(requested,[[url('umap'),'image/png']]);
assert.equal(walk(container).filter(item=>item.tagName==='img').length,1);
assert.ok(findButton(container,'Download stage composition'));
assert.ok(!findButton(container,'Download UMAP coordinates'));
WorkbenchStudio.renderSession(container,'findings',model,options);
assert.ok(textIn(container).includes('Motor-neuron composition by stage'));
assert.ok(textIn(container).includes('84.4%'));
assert.ok(textIn(container).includes('e11'));
assert.ok(textIn(container).includes('Changes between stages'));
assert.ok(textIn(container).includes('+50 pp'));
assert.ok(textIn(container).some(text=>text.includes('embryo-level')));
assert.equal(WorkbenchStudio.handoffView('workflows',model),'figures');

const custom = sid => '/api/scientific/adaptive/'+sid+'/comparison/'+sha+'.png';
model.retained_comparison = {title:'Custom plots', figures:[
  {title:'Verified custom plot',download_path:custom(sessionId)},
  {title:'Another session’s plot',download_path:custom('c'.repeat(32))},
]};
requested.length=0;
WorkbenchStudio.renderSession(container,'figures',model,options);
await flush();
assert.deepEqual(requested,[[custom(sessionId),'image/png'],[url('umap'),'image/png']]);
assert.deepEqual(walk(container).filter(item=>item.tagName==='img').map(item=>item.alt),
  ['Verified custom plot','Shared UMAP']);

const typed = sid => '/api/scientific/adaptive/'+sid+'/comparison/'+sha+'/plotly';
const offered = [];
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
options.plotlyFrame = path => { offered.push(path); return path; };
model.retained_comparison.figures[0].plotly_path = typed(sessionId);
requested.length=0;
WorkbenchStudio.renderSession(container,'figures',model,options);
await flush();
assert.deepEqual(offered,[typed(sessionId)]);
assert.equal(walk(container).find(item=>item.tagName==='iframe').title,
  'Interactive Verified custom plot');
assert.deepEqual(requested,[[url('umap'),'image/png']]);
WorkbenchStudio.renderSession(container,'code',model,options);

model.shared_motor_umap.inputs = [{name:'e09.h5ad',cells:130}];
model.shared_motor_umap.parameters = {counts_layer:'counts',label_column:'celltypes'};
model.shared_motor_umap.method = {normalization:'counts per 10000 per cell',
  common_symbols:400, pca_dimensions:20, pca_explained_variance_fraction:0.31,
  umap:{n_neighbors:15,min_dist:0.3,seed:42},
  limitations:['Independent embryo identities and lineage transitions are unverified.'],
};
WorkbenchStudio.motorWorkflow(container,model,{});
assert.ok(textIn(container).includes('Steps run'));
assert.ok(walk(container).some(item=>
  item.getAttribute('aria-label')==='Inputs and selection'));
assert.ok(textIn(container).some(s=>s.includes('20 components explaining 31%')));
assert.ok(textIn(container).includes('84.6%'));
assert.ok(textIn(container).includes('Align genes and normalize counts'));
assert.ok(textIn(container).includes('Verified outputs'));
WorkbenchStudio.renderSession(container,'overview',model,options);
assert.ok(textIn(container).includes('Run summary'));
assert.ok(textIn(container).some(s=>s.includes('Mapped 254 cells')));
assert.ok(textIn(container).some(s=>
  s.includes('0% at e09, 50% at e10, and 84.4% at e11')));
assert.ok(textIn(container).some(s=>s.includes('84.4 percentage points')));
assert.ok(textIn(container).includes('54 of 64'));
assert.ok(textIn(container).some(s=>s.includes('250 genes and 20 PCA components')));
assert.ok(textIn(container).some(s=>s.includes('lineage transitions are unverified')));
const meters = walk(container).filter(item=>item.tagName==='meter');
assert.deepEqual(meters.map(item=>item.getAttribute('aria-valuetext')),
  ['0%','50%','84.4%']);
assert.ok(findButton(container,'View workflow'));
""")


@pytest.mark.parametrize("outcome", ["ready", "error", "timeout"])
def test_studio_uses_interactive_plot_before_loading_its_fallback(outcome):
    _run(f"const outcome = {json.dumps(outcome)};\n" + r"""
const sha = 'b'.repeat(64);
const runId = 'c'.repeat(32);
const base = '/api/scientific/adaptive/' + sessionId;
const coordinates = base + '/motor-umap/coordinates/' + sha;
const png = base + '/motor-umap/umap/' + sha;
const receiptPlot = base + '/receipt-plot/' + runId + '/0/' + sha + '/plotly';
const umapView = {session_id:sessionId, shared_motor_umap:{
  status:'completed', artifacts:{
    umap:{url:png, sha256:sha}, coordinates:{url:coordinates, sha256:sha},
  },
}};
const receiptView = {session_id:sessionId, plugin_session:{runs:[{
  status:'completed', run_id:runId,
  summary_tables:[{caption:'QC retention by group',columns:['Group','Input','Retained'],
    rows:[['A',100,65]]}],
  plotly_paths:{0:receiptPlot},
}]}};
const listeners = new Set(), timers = new Map(), requests = [];
let nextTimer = 0;
globalThis.setTimeout = fn => { timers.set(++nextTimer, fn); return nextTimer; };
globalThis.clearTimeout = id => timers.delete(id);
globalThis.addEventListener = (type, fn, {signal}) => {
  assert.equal(type,'message');
  listeners.add(fn);
  signal.addEventListener('abort', () => listeners.delete(fn), {once:true});
};
globalThis.removeEventListener = (_type, fn) => listeners.delete(fn);
Node.prototype.remove = function() {
  const parent = walk(container).find(node=>node.children.includes(this));
  if (parent) parent.children = parent.children.filter(child=>child!==this);
};
const options = {
  plotlyFrame:path=>path+'?theme=dark',
  artifact:async path=>{ requests.push(path); return new Blob(['image']); },
};
for (const [view, source, fallbackTag] of [
  [umapView, base+'/motor-umap/'+sha+'/plotly', 'img'],
  [receiptView, receiptPlot, 'svg'],
]) {
  const get = tag => walk(container).find(node=>node.tagName===tag);
  WorkbenchStudio.renderSession(container,'figures',view,options);
  await flush();
  const frame = get('iframe');
  assert.equal(frame.src,source+'?theme=dark');
  assert.equal(frame.dataset.state,'loading');
  assert.equal(get(fallbackTag),undefined);
  assert.deepEqual(requests,[]);
  frame.contentWindow = {};
  const send = (source, type) => {
    for (const listener of [...listeners]) listener({source, data:{type}});
  };
  send({},'studio-plotly-error');
  assert.equal(get('iframe'),frame);
  if (outcome==='timeout') timers.values().next().value();
  else send(frame.contentWindow,'studio-plotly-'+outcome);
  await flush();
  assert.equal(timers.size,0);
  assert.equal(listeners.size,0);
  if (outcome==='ready') {
    assert.equal(frame.dataset.state,'ready');
    assert.equal(get('iframe'),frame);
    assert.equal(get(fallbackTag),undefined);
  } else {
    assert.equal(get('iframe'),undefined);
    assert.ok(get(fallbackTag));
  }
  assert.deepEqual(requests,outcome!=='ready' && fallbackTag==='img' ? [png] : []);
  requests.length = 0;
}
""")


def test_shared_umap_approval_posts_exact_plan_and_refreshes_while_active():
    module = workbench_ui.ASSET.parent / "workbench-investigations.js"
    _run(f"require({json.dumps(str(module))});\n" + r"""
globalThis.WorkbenchResearch = null;
const pending = new Map();
let tick = 0;
globalThis.setTimeout = callback=>{pending.set(++tick,callback);return tick;};
globalThis.clearTimeout = id=>pending.delete(id);
const plan = {status:'awaiting_review',can_run:true,plan_id:'b'.repeat(32),
  plan_sha256:'c'.repeat(64),inputs:[{name:'e09.h5ad',cells:130}],
  parameters:{stage_column:'age',label_column:'celltypes',counts_layer:'counts'}};
const view = {session_id:sessionId, mode:'adaptive/1',status:'available',
  shared_motor_umap:plan};
const path = '/api/scientific/adaptive/'+sessionId;
const calls = [], reached = [];
const controller = WorkbenchInvestigations.mountAdaptivePanel({
  host:container,
  request:async(route,body)=>{
    calls.push([route,body]);
    if (route === path+'/motor-umap') plan.status = 'queued';
    return {...view,shared_motor_umap:{...plan}};
  },
  onView:result=>reached.push(result.shared_motor_umap.status),
});
await controller.select(sessionId,{initialView:view,readOnly:true});
assert.ok(!findButton(container,'Approve and run shared UMAP'));
await controller.select(sessionId,{initialView:view});
assert.ok(textIn(container).includes('e09.h5ad'));
assert.ok(textIn(container).includes('celltypes'));
const consent = control(container,'Allow local execution for this plan');
const approve = findButton(container,'Approve and run shared UMAP');
assert.equal(approve.disabled,true);
await approve.onclick();
assert.equal(calls.length,0);
consent.checked = true;
consent.onchange();
await approve.onclick();
assert.deepEqual(calls,[[path+'/motor-umap',{
  plan_id:plan.plan_id,plan_sha256:plan.plan_sha256,allow_data_execution:true,
}],[path,undefined]]);
assert.deepEqual(reached,['queued']);
assert.equal(pending.size,1);
plan.status = 'completed';
await pending.get(tick)();
assert.deepEqual(reached,['queued','completed']);
assert.equal(pending.size,0);
""")
