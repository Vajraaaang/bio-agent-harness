"""Descriptive aggregates must conserve generated records and expose their meaning."""
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def test_exact_aggregates_and_quantiles():
    node=shutil.which('node')
    assert node, 'Node required for plot aggregation tests'
    source=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');const window={};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{window});const api=window.WorkbenchSummaryPlots;
const data={project:{id:'fixture'},provenance:{kind:'synthetic'},cells:[1,2,3,4].map((v,i)=>({batch:i%2,group:i%2,reads:v,genes:v*10,mito:v,expression:Array(8).fill(v)}))};
const s=api.summarize(data);assert.equal(s.n,4);assert.equal(s.batches.reduce((n,b)=>n+b.n,0),4);
assert.equal(s.batches.reduce((n,b)=>n+b.depthBins.reduce((a,c)=>a+c,0),0),4);
assert.equal(s.batches.reduce((n,b)=>n+b.mitoBins.reduce((a,c)=>a+c,0),0),4);
assert.deepEqual(Array.from(s.batches[0].quantiles),[1,1.5,2,2.5,3]);
assert.equal(s.groups[0].means[0],2);assert.equal(s.groups[0].fractions[0],.5);
assert.ok(Math.abs(s.correlation[1][2]-1)<1e-12);assert.equal(api.summarize(data,'0').n,2);
assert.equal(api.quantile([1,2,3,4],.5),2.5);assert.deepEqual(Array.from(api.ecdfKnots([1,1,2,3])[0]),[1,.5]);assert.deepEqual(Array.from(api.ecdfKnots([1,1,2,3]).at(-1)),[3,1]);assert.equal(Object.keys(api.titles).length,8);
assert.throws(()=>api.summarize(data,'bad'));console.log('ok');
'''
    completed=subprocess.run([node,'-e',source,str(ROOT/'devbench/analysis/assets/workbench-summary-plots.js')],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr

def test_packaged_and_composed_summary_renderer():
    from devbench.analysis.workbench_ui import render_page
    from scripts.build_colab_notebook import allowed_bundle_path
    path='devbench/analysis/assets/workbench-summary-plots.js'
    assert allowed_bundle_path(path)
    assert 'WorkbenchSummaryPlots' in render_page('summary-check')
    assert 'analysis/assets/workbench-summary-plots.js' in (ROOT/'pyproject.toml').read_text()
