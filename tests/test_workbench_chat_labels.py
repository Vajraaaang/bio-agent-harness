"""Visible project and investigation labels preserve the governed chat routes."""

import re

from devbench.analysis import workbench_ui
from tests.test_workbench_investigation_asset import run_js


def _caption(page, control_id):
    match = re.search(
        rf'<button\b[^>]*id="{control_id}"[^>]*>(.*?)</button>',
        page,
        re.S,
    )
    assert match is not None
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def test_project_data_investigations_and_workbench_have_distinct_names():
    page = workbench_ui.ASSET.read_text()
    assert _caption(page, "nav-register") == "New investigation"
    assert _caption(page, "nav-data") == "Data"
    assert 'id="nav-thesis"' not in page
    assert _caption(page, "project-aims-button") == "Aims &amp; packages"
    assert 'aria-label="Investigations"' in page
    assert 'id="inspector-title"' in page
    assert 'id="chat-section"' in page and 'id="investigation-history"' in page
    assert _caption(page, "canvas-findings") == "Findings"
    assert _caption(page, "canvas-workflows") == "Workflows"
    assert _caption(page, "canvas-figures") == "Figures"
    assert _caption(page, "canvas-code") == "Code"
    assert _caption(page, "canvas-evidence") == "Evidence"
    assert re.search(r'<span id="workspace-title">[^<]+</span>', page)
    assert "el('workspace-title').textContent='Workbench'" not in page
    assert 'aria-label="Resize chat and workbench"' in page


def test_chat_history_labels_keep_retained_investigation_routes_read_only():
    run_js(r"""
const f=fixture();await f.controller.ready;
assert.ok(walk(f.options.historyContainer).some(
  node=>node.textContent==='Your chats will appear here.'
));
f.server.history=[{id:parent,source,status:'awaiting_information',question:''}];
await f.controller.reloadHistory();
assert.ok(walk(f.options.historyContainer).some(
  node=>node.textContent==='Untitled chat'
));
await f.controller.open(parent);
assert.ok(f.statuses.some(row=>row.message==='Opening chat…'));
assert.ok(f.calls.some(row=>row.path==='/api/investigations/'+parent));
assert.equal(f.calls.filter(row=>row.body!==undefined).length,0);
""")
