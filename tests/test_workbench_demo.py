"""The current workbench can display an explicitly synthetic, offline population."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_generated_figure_counts_summaries_and_simulation_contract():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the presentation contracts')
    program = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const sandbox={window:{}};
for(const name of ['workbench-synthetic.js','workbench-summary-plots.js','workbench-demo.js','scientific-figure.js'])vm.runInNewContext(fs.readFileSync(process.argv[1]+'/'+name,'utf8'),sandbox);
const demo=sandbox.window.WorkbenchDemo,renderer=sandbox.window.ScientificFigure;
const data=sandbox.window.WorkbenchSynthetic.generate(20261029,demo.batchSizes),summary=demo.summarize(data.cells);
assert.ok(summary.read_gene_correlation>0 && summary.read_gene_correlation<=1);assert.equal(summary.mean_immune_by_group.length,6);
assert.equal(summary.n,287463);assert.equal(summary.groups.reduce((a,b)=>a+b,0),287463);
assert.deepEqual(Array.from(summary.batches,b=>b.n),[94817,101206,91440]);
for(const b of summary.batches){assert.equal(b.mean_reads,b.reads/b.n);assert.equal(b.mean_mito,b.mito/b.n);}
let full=demo.figure(data,'landscape','all');
assert.equal(full.record.analysis_n,287463);assert.equal(full.payload.population_ids.length,287463);
assert.equal(full.record.format,'devbench.simulated-figure/1');
assert.equal(full.payload.format,'devbench.simulated-figure-data/1');
assert.deepEqual(Array.from(full.record.provenance.batch_sizes),[94817,101206,91440]);assert.notEqual(full.record.provenance.batch_sizes,full.payload.provenance.batch_sizes);
assert.equal(full.record.provenance.kind,'synthetic');assert.equal(full.record.provenance.live_trials,0);
assert.throws(()=>renderer.prepare(full.record,full.payload));
assert.equal(renderer.prepareSimulation(full.record,full.payload).ids.length,287463);
full=null;
for(const view of Object.keys(demo.views).filter(view=>!Object.hasOwn(sandbox.window.WorkbenchSummaryPlots.titles,view))){
 const spec=demo.figure(data,view,'1');
 assert.equal(spec.payload.population_ids.length,101206);assert.ok(spec.payload.panels[0].title.includes('Batch 02'));assert.ok(spec.payload.panels[0].panel_id.endsWith('-batch-1'));
 assert.ok(spec.payload.panels[0].rows.every(r=>r.metadata.batch==='Batch 02'));
 assert.equal(renderer.prepareSimulation(spec.record,spec.payload).ids.length,101206);
}
assert.throws(()=>sandbox.window.WorkbenchSynthetic.generate(1,[1,2,300000]));
assert.throws(()=>sandbox.window.WorkbenchSynthetic.generate(1,[0,2,3]));
assert.throws(()=>sandbox.window.WorkbenchSynthetic.generate(1,[1,2]));
assert.throws(()=>demo.figure(data,'unknown','all'));assert.throws(()=>demo.figure(data,'landscape','42'));
console.log('287463 actual generated cells, computed summaries, explicit simulation contracts verified');
'''
    result = subprocess.run([node, '-e', program, str(ROOT / 'devbench/analysis/assets')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_demo_assets_are_shipped_and_connection_entry_is_explicit():
    from devbench.analysis.workbench_ui import render_page
    from scripts.build_colab_notebook import allowed_bundle_path

    page = render_page('simulation-test-nonce')
    assert 'data-workbench-demo' in page
    assert 'Open simulated project' in page
    assert 'mountSimulation(canvas' in page
    assert 'Scripted conversation prompts' in page
    assert 'data-synthetic-gallery' not in (ROOT / 'devbench/analysis/assets/workbench-ui.html').read_text()
    for suffix in ('js', 'css'):
        name = f'devbench/analysis/assets/workbench-demo.{suffix}'
        assert allowed_bundle_path(name)
        assert f'"analysis/assets/workbench-demo.{suffix}"' in (ROOT / 'pyproject.toml').read_text()


def test_six_project_catalog_generates_distinct_reproducible_populations():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the presentation contracts')
    program = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const sandbox={window:{}};
for(const name of ['workbench-synthetic.js','workbench-summary-plots.js','workbench-demo.js','scientific-figure.js'])vm.runInNewContext(fs.readFileSync(process.argv[1]+'/'+name,'utf8'),sandbox);
const demo=sandbox.window.WorkbenchDemo,renderer=sandbox.window.ScientificFigure,seen=new Set();
assert.equal(demo.projects.length,6);
for(const project of demo.projects){
 const data=demo.generateProject(project.id),n=project.batchSizes.reduce((a,b)=>a+b,0),stats=demo.summarize(data.cells);
 assert.equal(data.cells.length,n);assert.equal(new Set(data.cells.map(c=>c.id)).size,n);
 assert.deepEqual(Array.from(stats.batches,b=>b.n),Array.from(project.batchSizes));
 assert.equal(data.provenance.seed,project.seed);
 const fingerprint=JSON.stringify(data.cells.slice(0,3));assert.ok(!seen.has(fingerprint));seen.add(fingerprint);
 const spec=demo.figure(data,project.defaultView,'all');
 assert.ok(spec.record.figure_id.startsWith(project.id+'-'));assert.ok(spec.record.title.includes(project.title));
 assert.equal(spec.record.provenance.seed,project.seed);assert.deepEqual(Array.from(spec.record.provenance.batch_sizes),Array.from(project.batchSizes));
 assert.equal(renderer.prepareSimulation(spec.record,spec.payload).ids.length,n);
}
assert.throws(()=>demo.generateProject('missing'));
console.log('Six independently generated project populations validated');
'''
    result = subprocess.run([node, '-e', program, str(ROOT / 'devbench/analysis/assets')], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
