"""Visible project/chat hierarchy preserves the governed investigation contracts."""
import re

from devbench.analysis import workbench_ui
from tests.test_workbench_investigation_asset import run_js


def test_project_data_chats_and_workbench_have_distinct_visible_names():
    page = workbench_ui.ASSET.read_text()
    assert re.search(r'id="nav-register"[^>]*aria-label="New chat"[^>]*title="New chat"', page)
    data = re.search(r'<button\b[^>]*id="nav-data"[^>]*>(.*?)</button>', page, re.S).group(1)
    assert re.sub(r'<[^>]+>', '', data).strip() == 'Data'
    assert 'id="inspector-title"' in page
    assert 'id="chat-section"' in page and 'id="investigation-history"' in page
    assert "el('workspace-title').textContent='Workbench'" in page
    assert 'aria-label="Resize chat and workbench"' in page
    assert '+ New investigation' not in page


def test_chat_history_labels_keep_retained_investigation_routes_read_only():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.ok(walk(f.options.historyContainer).some(node=>node.textContent==='Your chats will appear here.'));
f.server.history=[{id:parent,source,status:'awaiting_information',question:''}];
await f.controller.reloadHistory();
assert.ok(walk(f.options.historyContainer).some(node=>node.textContent==='Untitled chat'));
await f.controller.open(parent);
assert.ok(f.statuses.some(row=>row.message==='Opening chat…'));
assert.ok(f.calls.some(row=>row.path==='/api/investigations/'+parent));
assert.equal(f.calls.filter(row=>row.body!==undefined).length,0);
""")
