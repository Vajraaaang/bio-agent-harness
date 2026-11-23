"""The receipt inspector displays exact evidence and verifies bounded previews."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src/devbench/analysis/assets"


def test_receipt_drawer_only_requests_an_exact_preview_descriptor():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise the browser presentation module")
    script = r"""
const assert = require('node:assert/strict');
const {webcrypto, createHash} = require('node:crypto');
class Element {
  constructor(tag, text='') {
    this.tagName=tag; this.textContent=text; this.children=[];
    this.hidden=false; this.open=false; this.isConnected=true; this.events={};
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  setAttribute() {}
  querySelector(id) { return selectors.get(id); }
  addEventListener(key,fn) { this.events[key]=fn; }
  focus() { active=this; }
  matches() { return this.open; }
  showPopover() { this.open=true; }
  hidePopover() { this.open=false; }
}
const content=new Element('div'), close=new Element('button');
const dialog=new Element('dialog');
const opener=new Element('button');
let active=opener;
const selectors=new Map([
  ['[data-studio-receipt-content]',content],
  ['[data-studio-receipt-close]',close],
]);
const walk=el=>[el,...el.children.flatMap(walk)];
global.window=global;
Object.defineProperty(global,'crypto',{value:webcrypto});
global.document={
  createElement:tag=>new Element(tag),
  get activeElement(){return active;},
};
require(process.argv[1]);
require(process.argv[2]);
let requests=[];
const data=Buffer.from('{"method":"edgeR"}');
const sha=createHash('sha256').update(data).digest('hex');
const receipt='2'.repeat(64);
const artifact={
  artifact_id:'3'.repeat(64),sha256:sha,name:'summary.json',
  content_type:'application/json',artifact_type:'json',size_bytes:data.length,
  download_path:'/api/scientific/artifacts/'+'3'.repeat(64),
};
const preview=()=>walk(content).find(x=>x.textContent==='Preview verified artifact');
const inspect=record=>window.WorkbenchStudio.inspectReceipt(record);
dialog.hidden=true;
WorkbenchStudio.mountDrawer(dialog,async(path,options)=>{
  requests.push(path);
  assert.equal(options.signal.aborted,false);
  return new Response(data, {headers:{'Content-Length':String(data.length)}});
});
(async()=>{
  inspect({receipt_sha256:receipt});
  assert.equal(preview(),undefined);
  assert.equal(requests.length,0);
  assert(walk(content).some(x=>x.textContent===receipt));
  close.events.click();
  assert.equal(active,opener);
  for (const record of [
    {artifact_sha256:'4'.repeat(64),artifact},
    {artifact_sha256:sha,artifact,embargoed:true},
    {artifact_sha256:sha,artifact,representation:'expression_matrix'},
    {artifact_sha256:sha,artifact:{...artifact,download_path:'/api/inputs'}},
  ]) {
    inspect(record);
    assert.equal(preview(),undefined);
  }
  inspect({receipt_sha256:receipt,artifact_sha256:sha,artifact});
  assert.equal(requests.length,0);
  await preview().onclick();
  assert.deepEqual(requests,[artifact.download_path]);
  assert(walk(content).some(x=>x.textContent==='SHA-256 verified.'));
  assert(walk(content).some(x=>x.tagName==='pre'&&x.textContent.includes('edgeR')));
  WorkbenchStudio.closeReceipt();
  assert.equal(dialog.hidden,true);
  assert.deepEqual(content.children,[]);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    command = [
        node,
        "-e",
        script,
        str(ASSETS / "workbench-artifacts.js"),
        str(ASSETS / "workbench-studio.js"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
