"""Chat-list visibility is independent of chat creation and retained run history."""
import re
from pathlib import Path

from tests.test_workbench_ui_agent_asset import run_script


def test_new_chat_is_an_accessible_icon_outside_the_collapsible_body():
    page = (Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-ui.html").read_text()
    section = re.search(r'<details id="chat-section" open>(.*?)</details>', page, re.S).group(1)
    assert 'id="investigation-history"' in section
    assert 'id="nav-register"' not in section and 'id="legacy-history"' not in section
    button = re.search(r'<button id="nav-register"([^>]*)>(.*?)</button>', page, re.S)
    assert 'aria-label="New chat"' in button.group(1) and 'title="New chat"' in button.group(1)
    assert '<svg class="ui-icon"' in button.group(2)
    assert not re.sub(r'<.*?>', '', button.group(2)).strip()


def test_chat_list_default_persistence_and_new_chat_do_not_hide_history():
    run_script(r"""
const stored=new Map();
context.window={localStorage:{getItem:key=>stored.get(key)||null,setItem:(key,value)=>stored.set(key,value)}};
vm.runInContext(script,context);
const section=elements['chat-section'];
assert.equal(section.open,true);
section.open=false;section.ontoggle();
assert.equal(stored.get('rna-workbench-chat-list-collapsed-v1'),'true');
vm.runInContext(script,context);
assert.equal(section.open,false);
server.jobs=[{id:'fixture-job',question:'Retained run',status:'interrupted',updated_at:'2026-10-28T12:00:00Z'}];
await elements.connect.onclick();
assert.equal(elements['legacy-history'].hidden,false);
assert.equal(elements.jobs.children.length,1);
await elements['nav-register'].onclick();
assert.equal(section.open,false);
assert.equal(elements['legacy-history'].hidden,false);
section.open=true;section.ontoggle();
assert.equal(stored.get('rna-workbench-chat-list-collapsed-v1'),'false');
assert.equal(posts().length,0);
""")


def test_chat_list_works_when_preference_storage_is_unavailable():
    run_script(r"""
context.window={localStorage:{getItem(){throw new Error('Unavailable');},setItem(){throw new Error('Unavailable');}}};
vm.runInContext(script,context);
assert.equal(elements['chat-section'].open,true);
elements['chat-section'].open=false;elements['chat-section'].ontoggle();
assert.equal(elements['chat-section'].open,false);
assert.equal(elements['nav-register'].hidden,false);
context.window={};Object.defineProperty(context.window,'localStorage',{get(){throw new Error('Denied storage');}});
vm.runInContext(script,context);
assert.equal(elements['chat-section'].open,true);
""")
