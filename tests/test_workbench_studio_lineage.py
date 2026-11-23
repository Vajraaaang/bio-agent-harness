"""Verify the local lineage controls against retained graph entries."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

LINEAGE = (
    Path(__file__).resolve().parents[1]
    / "src/devbench/analysis/assets/workbench-lineage.js"
)


def test_lineage_keeps_edges_generation_and_drawer_evidence_tied_to_the_page():
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node is required for the offline lineage UI check")
    source = r"""
const assert = require('node:assert/strict');
require(MODULE);
class Node {
  constructor(tag) { this.tagName = tag; this.children = []; this.attrs = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(name, handler) { this[name] = handler; }
}
const document = {
  createElement: tag => new Node(tag),
  createElementNS: (_, tag) => new Node(tag)
};
const walk = node => [node, ...node.children.flatMap(walk)];
const button = (tag, label) => walk(tag).find(
  node => node.tagName === 'button' && node.textContent === label
);
const field = (tag, name, value) => walk(tag).find(node => node.attrs[name] === value);
const branch = (step, deps, status, group = 'input') => ({
  entry_kind:'step', step_id:step, status, branch_id:group, dependencies:deps,
  generation:step === 'qc-lanes' ? 2 : 1, operation_id:'scrna_batch_qc',
  parameters_sha256:'c'.repeat(64),
  input_ports:[{port:'matrix', kind:'counts', representation:'raw_counts'}]
});
const view = {
  graph:{entries:[
    branch('prepare', ['qc-lanes'], 'blocked', 'analysis'),
    branch('qc-lanes', ['source'], 'succeeded'),
    branch('source', [], 'succeeded'),
    branch('fork', ['source'], 'ready', 'exploratory'),
    {entry_kind:'tombstone', step_id:'qc-lanes', generation:1, reason:'qc_revision'},
    {entry_kind:'attempt', step_id:'qc-lanes', generation:2,
      status:'succeeded', receipt_sha256:'d'.repeat(64)}
  ]},
  revision:12, publication:{step_id:'qc-lanes',generation:1}
};
const host = new Node('section'), state = {};
const inspected = [], context = [], actions = [];
globalThis.WorkbenchStudio = {
  inspectReceipt: item => inspected.push(item),
  addContext: item => context.push(item)
};
const openPublication = () => actions.push('publication');
const verifyReplay = () => actions.push('replay');
const options = {document, host, view, state, openPublication, verifyReplay};
assert.equal(WorkbenchLineage.mount(options),6);
assert.ok(walk(host).some(node=>node.textContent?.includes('Superseded result')));
button(host, 'Graph').onclick();
const graph = field(host,'data-lineage-graph','');
assert.ok(graph);
const edges = walk(graph).filter(node=>node.tagName === 'path');
assert.equal(edges.length,3);
const qc = field(graph,'data-lineage-step','qc-lanes');
const qcCell = walk(graph).find(node=>node.children.includes(qc));
assert.ok(Number(qcCell.attrs.width)>182);
const edgeStart = Number(qcCell.attrs.x) + Number(qcCell.attrs.width);
assert.ok(edges.some(edge=>edge.attrs.d.startsWith('M '+edgeStart+' ')));
assert.ok(walk(qc).some(node=>node.className==='lineage-generation' &&
  node.textContent==='gen 2'));
assert.ok(walk(qc).some(node=>node.className==='studio-status' &&
  node.attrs['data-status']==='succeeded' && node.textContent==='succeeded'));
assert.match(qc.attrs['aria-label'],/succeeded · Revision 2/);
qc.onclick();
assert.equal(field(graph,'data-lineage-step','source')
  .attrs['data-lineage-relation'],'upstream');
assert.equal(field(graph,'data-lineage-step','prepare')
  .attrs['data-lineage-relation'],'downstream');
assert.equal(field(graph,'data-lineage-step','fork')
  .attrs['data-lineage-relation'],'unrelated');
const receipt = button(host,'Inspect receipt · dddddddd');
assert.ok(receipt);
receipt.onclick();
assert.equal(inspected[0].generation,2);
assert.equal(inspected[0].step_id,'qc-lanes');
button(host,'Send step to chat').onclick();
assert.equal(context[0].kind,'step');
assert.equal(context[0].id,'qc-lanes');
assert.equal(button(host, 'Open admitted output for this step'), undefined);
button(host,'Verify workflow replay').onclick();
assert.deepEqual(actions,['replay']);
button(host,'List').onclick();
assert.ok(field(host,'data-lineage-list',''));
assert.equal(state.selected,'qc-lanes');
view.publication.generation = 2;
host.replaceChildren();
WorkbenchLineage.mount(options);
button(host,'Open admitted output for this step').onclick();
assert.deepEqual(actions,['replay','publication']);
"""
    result = subprocess.run(
        [executable, "-"],
        input=source.replace("MODULE", json.dumps(str(LINEAGE)), 1),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.wal
def test_lineage_metadata_uses_authorized_structural_projection(workspace_tmp_path):
    from devbench.analysis.rna_investigation_projection import project_adaptive_dag
    from tests.test_investigation_invalidation import (
        Worker,
        delivery_for,
        digest,
        link,
        make,
        run,
        step,
    )

    flow = make(workspace_tmp_path)
    flow.insert(step("qc", branch_id="primary"))
    flow.insert(step("prepare", link("qc_input", "qc"), branch_id="downstream"))
    run(flow, Worker(), "qc")
    page = project_adaptive_dag(flow, evidence_delivery=delivery_for(flow))
    downstream = next(
        row for row in page["entries"] if row["step_id"] == "prepare"
    )
    assert downstream["dependencies"] == ["qc"]
    assert downstream["branch_id"] == "downstream"
    assert downstream["generation"] == 1
    assert downstream["operation_id"] == "synthetic"
    assert downstream["parameters_sha256"] == digest("prepare")
    assert downstream["input_ports"] == [
        {
            "port": "qc_input",
            "kind": "table",
            "source_id": "qc",
            "source_port": "out",
            "representation": "table",
        }
    ]
