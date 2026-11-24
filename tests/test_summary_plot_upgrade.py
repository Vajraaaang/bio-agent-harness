"""Numerical and vector fidelity of generated summaries and retained dispatch."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/devbench/analysis/assets"
SUMMARY = ASSETS / "workbench-summary-plots.js"
ATLAS = ASSETS / "workbench-synthetic.js"
PANELS = ASSETS / "scientific-plot-panels.js"

BASE = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const window={};const sandbox={window};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
const api=window.WorkbenchSummaryPlots;
const data={project:{id:'numerical-fixture',title:'Declared generated fixture',seed:42,batchSizes:[8,0,0]},
 provenance:{kind:'synthetic'},cells:[1,2,3,4,5,6,7,100].map((reads,i)=>({id:'cell-'+i,batch:0,group:0,x:i/2,y:Math.sin(i),reads,genes:reads*10,mito:i*5,expression:Array(8).fill(i%5)}))};
"""


def run_js(program):
    node = shutil.which("node")
    assert node, "Node is required for scientific geometry verification"
    result = subprocess.run([node, "-e", BASE + program, str(SUMMARY), str(ATLAS), str(PANELS)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_tukey_whiskers_exact_observations_and_full_source_range():
    run_js(r"""
const s=api.summarize(data),b=s.batches[0];
assert.equal(b.whiskers.low,1);assert.equal(b.whiskers.high,7);
assert.deepEqual(Array.from(b.whiskers.outliers),[100]);
const scene=api.buildScene(s,'batch_boxplot');
const observations=scene.marks.filter(m=>m.role==='observation');
assert.equal(observations.length,8);assert.equal(new Set(observations.map(m=>m.key)).size,8);
assert.ok(scene.marks.some(m=>m.type==='text'&&m.text==='n=8'));
assert.ok(observations.every(m=>m.y>=scene.plotBounds.top&&m.y<=scene.plotBounds.bottom));
assert.notEqual(observations[0].x,observations[1].x);
const ticks=api.niceTicks(0,5118);const step=ticks[1]-ticks[0];
assert.ok([1,2,5,10].some(v=>Math.abs(step/10**Math.floor(Math.log10(step))-v)<1e-10));
assert.ok(ticks[0]<=0&&ticks.at(-1)>=5118);
""")


@pytest.mark.parametrize("view", ["composition", "depth_histogram", "mito_ridges", "batch_boxplot", "depth_ecdf", "module_heatmap", "module_dotplot", "correlation"])
def test_summary_svg_is_vector_with_complete_geometry_and_membership(view):
    run_js("const view=" + repr(view) + r""";
const scene=api.buildScene(api.summarize(data),view),config={provenance:{kind:'synthetic'},source_member_ids:data.cells.map(c=>c.id)};
const svg=api.toSVG(scene,config);
assert.ok(svg.startsWith('<svg'));assert.ok(svg.includes('<metadata>'));assert.ok(svg.includes('<text'));
assert.ok(!svg.includes('<image'));assert.ok(!svg.includes('data:image'));assert.ok(svg.includes('cell-7'));
assert.ok(!svg.includes('NaN'));assert.ok(!svg.includes('Infinity'));
if(['module_heatmap','module_dotplot','correlation'].includes(view))assert.ok(svg.includes('<linearGradient'));
if(view==='module_dotplot'){
 const legend=scene.marks.filter(m=>m.role==='fraction-legend');
 assert.equal(legend.length,3);assert.ok(Math.abs(legend[1].radius**2/legend[2].radius**2-.5)<1e-12);
}
if(view==='correlation'){
 assert.equal(api.divergingColor(0),'#f7f7f7');
 assert.ok(scene.marks.some(m=>m.type==='text'&&m.text==='−1'));
 assert.ok(scene.marks.some(m=>m.type==='text'&&m.text==='+1'));
}
""")


def test_histogram_count_density_and_cutoff_semantics_are_explicit():
    run_js(r"""
const s=api.summarize(data);
const count=api.buildScene(s,'depth_histogram');
assert.ok(count.marks.some(m=>m.type==='text'&&m.text.includes('Records per bin')));
assert.ok(count.marks.some(m=>m.role==='median'));
assert.equal(count.marks.filter(m=>m.role==='cutoff').length,0);
const density=api.buildScene(s,'depth_histogram',{histogramScale:'density',cutoffs:{reads:10}});
assert.ok(density.marks.some(m=>m.type==='text'&&m.text.includes('Density per log10 unit')));
assert.equal(density.marks.filter(m=>m.role==='cutoff').length,1);
const bars=density.marks.filter(m=>m.role==='histogram-bin'&&m.batch===0);
assert.ok(Math.abs(bars.reduce((sum,m)=>sum+m.value*m.binWidth,0)-1)<1e-12);
assert.throws(()=>api.buildScene(s,'depth_histogram',{cutoffs:{reads:-1}}));
""")


def test_summary_and_atlas_use_the_shared_exact_palette_when_composed():
    run_js(r"""
window.ScientificPalettes=require(process.argv[3].replace('scientific-plot-panels.js','scientific-palettes.js'));
assert.equal(api.continuousColor(.371),window.ScientificPalettes.sample('viridis',.371));
const scene=api.buildScene(api.summarize(data),'module_heatmap');
assert.equal(scene.marks.find(m=>m.type==='colorbar').stops.length,256);
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
assert.equal((window.WorkbenchSynthetic.heatmap(data.cells).match(/<stop /g)||[]).length,256);
""")


def test_explicit_retained_dispatch_validates_and_keeps_typed_panel_identity():
    run_js(r"""
const capability={composition:'batch_composition',depth_histogram:'depth_histogram',mito_ridges:'qc_ridges',batch_boxplot:'batch_boxplot',depth_ecdf:'depth_ecdf',module_heatmap:'module_heatmap',module_dotplot:'marker_dotplot',correlation:'measurement_correlation'};
let validated=0,seen=null;
window.ScientificPlotPanels={prepareWorkspace(w){validated++;assert.equal(w.format,'devbench.scientific-plot-workspace/1');if(w.invalid)throw Error('Invalid saved source');return w;}};
window.BulkScientificFigure={mount(container,options){seen=options;return {shared:true};}};
for(const view of Object.keys(capability)){
 const panel={id:'retained-'+view,capability:capability[view],title:view==='mito_ridges'?'Mitochondrial percentage — pre-filter':'Saved '+view};
 const w={format:'devbench.scientific-plot-workspace/1',source:{sha256:'saved'},panels:[panel],entities:[]};
 assert.equal(api.mount({}, {scientificWorkspace:w,view}).shared,true);
 assert.equal(seen.scientificWorkspace,w);assert.equal(seen.panelId,panel.id);
 assert.equal(api.mount({}, {data:w,view}).shared,true);
}
assert.equal(validated,16);
assert.throws(()=>api.mount({}, {scientificWorkspace:{format:'devbench.scientific-plot-workspace/1',invalid:true},view:'composition'}));
assert.throws(()=>api.mount({}, {data:{...data,provenance:{kind:'retained'}},view:'composition'}));
assert.throws(()=>api.mount({}, {scientificWorkspace:{format:'devbench.scientific-plot-workspace/1',panels:[]},view:'correlation'}));
""")


def test_atlas_vector_export_exact_points_area_legend_and_unclipped_mito_bins():
    run_js(r"""
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
const atlas=window.WorkbenchSynthetic;
const svg=atlas.scatterSVG(data.cells,'group',false,{kind:'synthetic',seed:42});
assert.ok(!svg.includes('<image'));assert.ok(!svg.includes('data:image'));
assert.equal((svg.match(/data-record-id=/g)||[]).length,data.cells.length);
assert.ok(svg.includes('cell-7'));assert.ok(svg.includes('SYNTHETIC'));assert.ok(svg.includes('<metadata>'));
const heatmap=atlas.heatmap(data.cells);
assert.ok(heatmap.includes('<linearGradient'));assert.ok(heatmap.includes('Fraction (score &gt; 1)'));
assert.ok(heatmap.includes('data-fraction="0.5"'));assert.ok(heatmap.includes('data-fraction="1"'));
const ridge=atlas.density(data.cells);
assert.ok(ridge.includes('Histogram outlines'));assert.ok(ridge.includes('data-bin-upper="35"'));assert.ok(!ridge.includes('NaN'));
""")


def test_canvas_and_vector_share_points_and_complete_current_exports_keep_membership():
    run_js(r"""
const arcs=[],texts=[];
const ctx=new Proxy({createLinearGradient(){return {addColorStop(){}};}},{get(target,key){if(key in target)return target[key];return (...args)=>{if(key==='arc')arcs.push(args);if(key==='fillText')texts.push(args[0]);};}});
const doc={createElement(tag){return {tag,ownerDocument:doc,dataset:{},style:{},children:[],setAttribute(){},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},getContext(){return ctx;}};}};
const host=doc.createElement('div');data.cells.forEach((c,i)=>c.batch=i%2);data.project.batchSizes=[4,4,0];
const controller=api.mount(host,{data,view:'batch_boxplot',batch:'0'});
const current=controller.exportSVG('current'),complete=controller.exportSVG('complete');
assert.equal((current.match(/data-record-id=/g)||[]).length,4);
assert.equal((complete.match(/data-record-id=/g)||[]).length,8);
const unescape=s=>s.replace(/&quot;/g,'"').replace(/&apos;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const currentMetadata=JSON.parse(unescape(current.match(/<metadata>(.*?)<\/metadata>/)[1]));
const completeMetadata=JSON.parse(unescape(complete.match(/<metadata>(.*?)<\/metadata>/)[1]));
assert.equal(currentMetadata.source_member_ids.length,8);assert.equal(currentMetadata.displayed_member_ids.length,4);
assert.equal(completeMetadata.source_member_ids.length,8);assert.equal(completeMetadata.displayed_member_ids.length,8);
assert.equal(currentMetadata.scope,'current');assert.equal(completeMetadata.scope,'complete');
assert.equal(currentMetadata.provenance.kind,'synthetic');assert.equal(completeMetadata.provenance.live_trials,0);
assert.equal(arcs.length,4);assert.equal(texts.filter(s=>s.startsWith('SIMULATED DATA')).length,1);
const circles=[...current.matchAll(/<circle cx="([^"]+)" cy="([^"]+)" r="([^"]+)"/g)];
circles.forEach((m,i)=>assert.deepEqual(arcs[i].slice(0,3),m.slice(1).map(Number)));
assert.throws(()=>controller.exportSVG('mislabeled'));
controller.destroy();assert.equal(host.children.length,0);
""")


def test_atlas_vector_geometry_retains_the_full_300000_point_presentation_budget():
    run_js(r"""
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
const cells=Array.from({length:300000},(_,i)=>({id:'generated-large-'+i,batch:i%3,group:i%6,x:i%1000,y:Math.floor(i/1000),reads:i+1,genes:100+i%500}));
const scene=window.WorkbenchSynthetic.scatterScene(cells,'batch');
const points=scene.marks.filter(m=>m.role==='observation');
assert.equal(points.length,300000);assert.equal(points.at(-1).key,'generated-large-299999');
const svg=api.toSVG(scene,{kind:'synthetic',point_count:300000});
assert.equal((svg.match(/data-record-id=/g)||[]).length,300000);
assert.ok(!svg.includes('<image'));assert.ok(!svg.includes('NaN'));assert.ok(!svg.includes('Infinity'));
""")


def test_actual_saved_adapters_dispatch_through_shared_validation_without_fixture_shapes():
    from devbench.analysis import plot_adapters_bulk, plot_adapters_expression, plot_adapters_diagnostics
    from tests.test_plot_adapters_bulk import fixture_packets as bulk_packets
    from tests.test_plot_adapters_expression import fixture_packets as expression_packets
    from tests.test_plot_adapters_diagnostics import fixture_packets as diagnostics_packets

    aliases = {
        "composition": "batch_composition", "depth_histogram": "depth_histogram",
        "mito_ridges": "qc_ridges", "batch_boxplot": "batch_boxplot",
        "depth_ecdf": "depth_ecdf", "module_heatmap": "module_heatmap",
        "module_dotplot": "marker_dotplot", "correlation": "measurement_correlation",
    }
    packets = {p["capability"]: p for p in [*expression_packets(), *bulk_packets(), *diagnostics_packets()]}
    fixtures = []
    for view, capability in aliases.items():
        packet = packets[capability]
        adapter = (plot_adapters_diagnostics if capability == "measurement_correlation"
                   else plot_adapters_bulk if capability.startswith("bulk_") else plot_adapters_expression)
        panels = adapter.build_panels(capability, packet["data"])
        fixtures.append({"view": view, "workspace": {
            "format": "devbench.scientific-plot-workspace/1", "title": "Saved synthetic validation fixture",
            "source": {"sha256": "a" * 64, "run_id": "synthetic-renderer-test", "kind": "synthetic"},
            "panels": panels, "entities": [], "limitations": ["Synthetic software verification only"],
        }})
    run_js("const fixtures=" + json.dumps(fixtures) + r""";
window.ScientificPlotPanels=require(process.argv[3]);
window.BulkScientificFigure={mount(container,options){return window.ScientificPlotPanels.project(options.scientificWorkspace,options.panelId);}};
for(const {view,workspace} of fixtures){
 const before=JSON.stringify(workspace),model=api.mount({}, {scientificWorkspace:workspace,view});
 assert.ok(model.scientific);assert.ok(model.rows.length);assert.ok(model.scientificPanel.marks.length);
 assert.equal(JSON.stringify(workspace),before);assert.equal(model.source.run_id,'synthetic-renderer-test');
 if(view==='mito_ridges')assert.match(model.title,/Mitochondrial.*pre-filter/);
 if(view==='module_heatmap')assert.notEqual(model.scientificPanel.x_categories.length,8);
 if(view==='composition')assert.notEqual(model.scientificPanel.x_categories.length,3);
 if(view==='correlation')assert.deepEqual(Array.from(model.scientificPanel.color_scale.domain),[-1,1]);
 const invalid=JSON.parse(JSON.stringify(workspace));invalid.source.sha256='unverified';
 assert.throws(()=>api.mount({}, {scientificWorkspace:invalid,view}));
 invalid.source.sha256='a'.repeat(64);invalid.panels[0].marks[0].keys=['not-a-source-member'];
 assert.throws(()=>api.mount({}, {scientificWorkspace:invalid,view}));
}
""")
