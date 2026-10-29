"""Independent side-pane visibility without changing data or workflow state."""
import re
from pathlib import Path
from tests.test_workbench_ui_agent_asset import run_script

SETUP = r"""
let width=1200;const properties={},stored=new Map(),listeners={};
context.window={addEventListener(name,handler){listeners[name]=handler;},localStorage:{getItem(key){return stored.get(key)||null;},setItem(key,value){stored.set(key,value);}}};
elements.app.style={setProperty(name,value){properties[name]=value;}};
elements.app.getBoundingClientRect=()=>({width});
vm.runInContext(script,context);
"""

def test_side_panes_collapse_independently_and_restore_saved_widths():
    run_script(SETUP+r"""
const nav=elements['toggle-navigation'],canvas=elements['toggle-workbench'];
const original={rail:properties['--rail-width'],chat:properties['--investigation-width']};
nav.onclick();assert.equal(elements['workspace-rail'].hidden,true);assert.equal(elements['rail-divider'].hidden,true);
assert.equal(nav.getAttribute('aria-expanded'),'false');assert.equal(nav.getAttribute('aria-label'),'Show navigation');
assert.equal(elements['workspace-canvas'].hidden,false);assert.equal(canvas.getAttribute('aria-expanded'),'true');
canvas.onclick();assert.equal(elements['workspace-canvas'].hidden,true);assert.equal(elements['investigation-divider'].hidden,true);
assert.equal(canvas.getAttribute('aria-label'),'Show workbench');assert.equal(elements.inspector.hidden,false);
const saved=JSON.parse(stored.get('rna-workbench-panel-widths-v1'));
assert.deepEqual(saved.collapsed,{rail:true,canvas:true});
width=900;listeners.resize();assert.equal(properties['--rail-width'],original.rail);assert.equal(properties['--investigation-width'],original.chat);
width=1200;vm.runInContext(script,context);
assert.equal(elements['workspace-rail'].hidden,true);assert.equal(elements['workspace-canvas'].hidden,true);
nav.onclick();canvas.onclick();assert.equal(elements['workspace-rail'].hidden,false);assert.equal(elements['workspace-canvas'].hidden,false);
assert.equal(properties['--rail-width'],original.rail);assert.equal(properties['--investigation-width'],original.chat);
assert.equal(nav.getAttribute('aria-expanded'),'true');assert.equal(canvas.getAttribute('aria-expanded'),'true');
assert.equal(posts().length,0);
""")

def test_collapse_controls_remain_available_on_narrow_layouts():
    run_script(SETUP+r"""
width=500;listeners.resize();elements['toggle-navigation'].onclick();elements['toggle-workbench'].onclick();
assert.equal(elements['workspace-rail'].hidden,true);assert.equal(elements['workspace-canvas'].hidden,true);
assert.equal(elements['toggle-navigation'].hidden,false);assert.equal(elements['toggle-workbench'].hidden,false);
assert.equal(elements.inspector.hidden,false);
elements['toggle-navigation'].onclick();elements['toggle-workbench'].onclick();
assert.equal(elements['workspace-rail'].hidden,false);assert.equal(elements['workspace-canvas'].hidden,false);
assert.equal(posts().length,0);
""")

def test_rapid_pane_toggles_do_not_wait_for_document_view_transitions():
    run_script(SETUP+r"""
context.document.startViewTransition=()=>{throw new Error('Pane controls must respond immediately.');};
elements['toggle-navigation'].onclick();elements['toggle-workbench'].onclick();
assert.equal(elements['workspace-rail'].hidden,true);assert.equal(elements['workspace-canvas'].hidden,true);
elements['toggle-navigation'].onclick();elements['toggle-workbench'].onclick();
assert.equal(elements['workspace-rail'].hidden,false);assert.equal(elements['workspace-canvas'].hidden,false);
assert.equal(posts().length,0);
""")

def test_collapse_controls_and_zero_width_desktop_layout_are_explicit():
    root=Path(__file__).resolve().parents[1]
    page=(root/'devbench/analysis/assets/workbench-ui.html').read_text()
    theme=(root/'devbench/analysis/assets/workbench-theme.css').read_text()
    for name,target in [('toggle-navigation','workspace-rail'),('toggle-workbench','workspace-canvas')]:
        assert re.search(r'id="'+name+r'"[^>]+type="button"[^>]+aria-controls="'+target+r'"[^>]+aria-expanded="true"',page)
    assert '.layout[data-rail-collapsed=true][data-canvas-collapsed=true] { grid-template-columns: 0 0 minmax(0,1fr) 0 0; }' in theme
