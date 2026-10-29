"""Project catalog UI uses authenticated host requests and real project identities."""
from pathlib import Path
import json
import shutil
import subprocess

import pytest

MODULE = Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-projects.js"


def run_js(scenario):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for project controls")
    setup = r"""
const assert=require('node:assert/strict');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attrs={};this.handlers={};this.value='';this.hidden=false;this.disabled=false;this.textContent='';this.open=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(key,value){this.attrs[key]=String(value);}
  getAttribute(key){return this.attrs[key]??null;}
  addEventListener(event,fn){(this.handlers[event]||(this.handlers[event]=[])).push(fn);}
  removeEventListener(event,fn){this.handlers[event]=(this.handlers[event]||[]).filter(item=>item!==fn);}
  focus(){this.focused=true;}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
const document={createElement:tag=>new Element(tag)},walk=node=>[node,...node.children.flatMap(walk)];
const projectA='a'.repeat(32),projectB='b'.repeat(32),projectC='c'.repeat(32),source='/approved/synthetic-cells.h5ad';
const event={preventDefault(){this.prevented=true;}};
function fixture(extra={}){
  const calls=[],switches=[],updates=[],statuses=[];
  const server={projects:[{id:projectA,name:'Research project',read_only:false},{id:projectB,name:'Saved samples',read_only:true}],handler:null};
  const options={document,currentProjectId:projectA,switcher:new Element('button'),nameElement:new Element('strong'),dialog:new Element('dialog'),title:new Element('h2'),list:new Element('div'),createPanel:new Element('section'),createForm:new Element('form'),nameInput:new Element('input'),seedSelect:new Element('select'),createButton:new Element('button'),createOpen:new Element('button'),createBack:new Element('button'),closeButton:new Element('button'),detailsButton:new Element('button'),status:new Element('p'),importHost:new Element('div'),
  onSwitch:async id=>{switches.push(id);},onDataChanged:async value=>{updates.push(value);},onStatus:(message,error)=>statuses.push({message,error}),
  request:async(path,body)=>{calls.push({path,body});if(server.handler)return server.handler(path,body);
    if(path==='/api/projects'&&body===undefined)return {projects:server.projects,active_project_id:projectA,default_project_id:projectA};
    if(path==='/api/project-sources')return {inputs:[{source,filename:'synthetic-cells.h5ad'}]};
    if(path==='/api/projects'){const project={id:projectC,name:body.name,read_only:false};server.projects.push(project);return {project};}
    if(path==='/api/project-inputs')return {source:'/project/data/synthetic-cells.h5ad',sha256:'f'.repeat(64)};
    throw new Error('Unexpected route '+path);
  },...extra};
  const controller=globalThis.WorkbenchProjects.mount(options);return {options,controller,calls,switches,updates,statuses,server};
}
"""
    code = "require(" + json.dumps(str(MODULE)) + ");\n" + setup
    code += "\n(async()=>{\n" + scenario + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
    result = subprocess.run([node, "-"], input=code, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_catalog_is_read_only_and_switch_uses_an_exact_available_identity():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.deepEqual(f.calls,[{path:'/api/projects',body:undefined}]);
assert.equal(f.options.list.children.length,2);assert.equal(f.options.nameElement.textContent,'Research project');
assert.ok(walk(f.options.list).some(node=>node.textContent==='Read only'));
await f.controller.switchProject(projectB);assert.deepEqual(f.switches,[projectB]);
assert.equal(f.controller.current(),projectA); // host must replace the page; do not relabel old caches
await assert.rejects(()=>f.controller.switchProject('x'.repeat(32)),/available project/);
await assert.rejects(()=>f.controller.switchProject('d'.repeat(32)),/available project/);
assert.equal(f.calls.filter(call=>call.body).length,0);
""")


def test_creation_requires_an_explicit_name_and_import_choice():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.loadSources();
await f.controller.create(event);assert.equal(f.calls.filter(call=>call.body).length,0);
f.options.nameInput.value=' New experiment ';f.options.seedSelect.value=source;await f.controller.create(event);
assert.deepEqual(f.calls.find(call=>call.path==='/api/projects'&&call.body).body,{name:'New experiment',sources:[source]});
assert.deepEqual(f.switches,[projectC]);assert.equal(f.options.dialog.open,false);
assert.equal(f.controller.current(),projectA);
""")


def test_creation_failure_preserves_draft_and_never_switches_or_falls_back():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.loadSources();
f.options.nameInput.value='Preserve this';f.options.seedSelect.value=source;
f.server.handler=async()=>{throw new Error('Creation is unavailable');};
await f.controller.create(event);
assert.equal(f.options.nameInput.value,'Preserve this');assert.equal(f.options.seedSelect.value,source);
assert.deepEqual(f.switches,[]);assert.equal(f.controller.current(),projectA);
assert.match(f.options.status.textContent,/Creation is unavailable/);assert.equal(f.options.createButton.disabled,false);
""")


def test_double_submit_does_not_create_two_projects():
    run_js(r"""
const f=fixture();await f.controller.ready;let release;
f.server.handler=async(path,body)=>{if(path==='/api/projects'&&body){await new Promise(resolve=>release=resolve);return {id:projectC,name:body.name};}throw Error(path);};
f.options.nameInput.value='One project';const pending=f.controller.create(event);await Promise.resolve();
assert.equal(f.options.createButton.disabled,true);await f.controller.create(event);
release();await pending;
assert.equal(f.calls.filter(call=>call.path==='/api/projects'&&call.body).length,1);assert.deepEqual(f.switches,[projectC]);
""")


def test_failed_navigation_retries_open_without_creating_duplicate_project():
    run_js(r"""
let fail=true;const f=fixture({onSwitch:async id=>{if(fail)throw new Error('Navigation blocked');}});await f.controller.ready;
f.options.nameInput.value='Created already';await f.controller.create(event);
assert.match(f.options.status.textContent,/Project created/);assert.equal(f.options.createButton.textContent,'Open project');
fail=false;await f.controller.create(event);
assert.equal(f.calls.filter(call=>call.path==='/api/projects'&&call.body).length,1);
""")


def test_import_is_explicit_project_scoped_and_read_only_projects_cannot_import():
    run_js(r"""
const f=fixture();await f.controller.ready;await f.controller.loadSources();
const select=walk(f.options.importHost).find(node=>node.tagName==='select');select.value=source;
await f.controller.importSource(event);
assert.deepEqual(f.calls.find(call=>call.path==='/api/project-inputs').body,{source});assert.equal(f.updates.length,1);
assert.ok(!f.calls.some(call=>/review|run|investigations/.test(call.path)));
const readonly=fixture({currentProjectId:projectB});await readonly.controller.ready;assert.equal(readonly.options.importHost.hidden,true);
await readonly.controller.importSource(event);assert.equal(readonly.calls.filter(call=>call.body).length,0);
""")


def test_project_names_are_text_and_disposal_prevents_late_switching():
    run_js(r"""
const f=fixture();f.server.projects[0].name='<img src=x onerror=evil()>';await f.controller.ready;
assert.ok(walk(f.options.list).some(node=>node.textContent==='<img src=x onerror=evil()>'));
assert.ok(walk(f.options.list).every(node=>node.tagName!=='img'));
let release;f.server.handler=async()=>{await new Promise(resolve=>release=resolve);return {id:projectC,name:'Late'};};
f.options.nameInput.value='Late';const pending=f.controller.create(event);await Promise.resolve();f.controller.destroy();release();await pending;
assert.deepEqual(f.switches,[]);
""")
