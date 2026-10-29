"""Synthetic presentation never becomes an analysis record."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "devbench/analysis/assets/workbench-synthetic.js"


def test_deterministic_cells_filters_and_aggregations():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for presentation generator checks")
    program = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sandbox = {window:{}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const api = sandbox.window.WorkbenchSynthetic;
const data = api.generate(20261029);
assert.equal(data.cells.length, 300000);
assert.equal(new Set(data.cells.map(x=>x.id)).size, 300000);
const checksum = data => data.cells.reduce((sum,c) => (sum + Math.round(c.x*1e6) + c.reads + c.genes) % 2147483647, 0);
const expected = checksum(data);
assert.equal(expected, checksum(api.generate(20261029)));
assert.notEqual(expected, checksum(api.generate(42)));
assert.equal(data.provenance.kind, 'synthetic');
assert.equal(data.provenance.live_trials, 0);
assert.equal(data.provenance.coordinate_method, 'generated latent coordinates; not computed UMAP');
const original = checksum(data);
for (let batch=0;batch<3;batch++) {
 const selected = api.select(data.cells, String(batch));
 assert.equal(selected.length, 100000);
 const totals = api.composition(selected);
 assert.equal(totals.flat().reduce((a,b)=>a+b,0),100000);
 assert.equal(totals[batch].reduce((a,b)=>a+b,0),100000);
}
assert.equal(api.select(data.cells,'all').length, 300000);
assert.equal(checksum(data), original);
for(const c of data.cells) {
 for(const key of ['x','y','reads','genes','mito']) assert.ok(Number.isFinite(c[key]));
 assert.ok(c.mito>=0 && c.mito<=100);
 assert.equal(c.expression.length, 8);
}
console.log('deterministic synthetic gallery contracts pass');
'''
    result = subprocess.run([node, "-e", program, str(ASSET)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_gallery_is_packaged_and_disclosed_in_composed_page():
    from devbench.analysis.workbench_ui import render_page
    from scripts.build_colab_notebook import allowed_bundle_path
    page = render_page("gallery-test-nonce")
    assert "WorkbenchSynthetic" in page
    assert "Synthetic atlas" in page
    assert "not computed UMAP" in page
    assert "data-synthetic-gallery" in page
    for suffix in ("js", "css"):
        name = f"devbench/analysis/assets/workbench-synthetic.{suffix}"
        assert allowed_bundle_path(name)
        assert f'"analysis/assets/workbench-synthetic.{suffix}"' in (ROOT / "pyproject.toml").read_text()
