"""Project navigation preserves active evidence until the investigation changes."""

import pytest

from tests.test_workbench_project_structure import SETUP
from tests.test_workbench_ui_agent_asset import run_script
from tests.test_workbench_ui_workspace_asset import FIGURE_SETUP

RETAINED_RESULT = (
    FIGURE_SETUP
    + SETUP
    + """
currentChat.job_id=server.state.id;
hooks.onGate({investigation:currentChat,jobState:server.state,workflowStage:3});
await new Promise(resolve=>setImmediate(resolve));
assert.equal(elements['result-view'].hidden,false);
assert.equal(elements['canvas-display'].hidden,false);
"""
)

PANEL_FOR_CANVAS = {
    "code": "panel-code",
    "evidence": "panel-provenance",
    "display": "panel-display",
}


@pytest.mark.parametrize("canvas", ["code", "evidence", "display"])
@pytest.mark.parametrize("destination", ["runs", "library", "files"])
def test_project_tool_navigation_preserves_selected_canvas(canvas, destination):
    run_script(RETAINED_RESULT + r"""
const canvas = CANVAS, destination = DESTINATION, panel = PANEL;
await elements['canvas-' + canvas].onclick();
assert.equal(elements['canvas-detail'].hidden, false);
assert.equal(elements[panel].hidden, false);
const selectedQuestion = elements['inspector-title'].textContent;
await elements['nav-' + destination].onclick();
assert.equal(elements[destination].hidden, false);
assert.equal(elements['project-dialog'].open, true);
assert.equal(elements['canvas-detail'].hidden, false);
assert.equal(elements[panel].hidden, false);
for (const other of ['runs', 'library', 'files']) {
  if (other !== destination) assert.equal(elements[other].hidden, true);
}
assert.equal(elements.results.hidden, true);
assert.equal(elements['inspector-title'].textContent, selectedQuestion);
await elements['close-project-dialog'].onclick();
await elements['canvas-figures'].onclick();
assert.equal(elements.results.hidden, false);
assert.equal(elements['canvas-detail'].hidden, true);
assert.equal(elements[panel].hidden, true);
assert.equal(elements['canvas-figures'].getAttribute('aria-current'), 'page');
assert.equal(posts().length, 0, 'navigation must not submit analysis or review work');
""".replace("CANVAS", repr(canvas))
        .replace("DESTINATION", repr(destination))
        .replace("PANEL", repr(PANEL_FOR_CANVAS[canvas])))


@pytest.mark.parametrize("canvas", ["code", "evidence", "display"])
def test_new_chat_after_details_starts_with_a_clean_figures_canvas(canvas):
    run_script(RETAINED_RESULT + r"""
await elements['canvas-' + CANVAS].onclick();
assert.equal(elements['canvas-detail'].hidden, false);
assert.equal(elements[PANEL].hidden, false);
await elements['nav-register'].onclick();
assert.equal(elements['inspector-title'].textContent, 'New chat');
assert.equal(elements.results.hidden, false);
assert.equal(elements['canvas-detail'].hidden, true);
assert.equal(elements[PANEL].hidden, true);
assert.equal(elements['result-view'].hidden, true);
assert.equal(elements['canvas-display'].hidden, true);
assert.equal(elements['canvas-figures'].getAttribute('aria-current'), 'page');
assert.equal(posts().length, 0);
""".replace("CANVAS", repr(canvas)).replace("PANEL", repr(PANEL_FOR_CANVAS[canvas])))


def test_investigation_without_a_job_hides_legacy_evidence_and_style():
    run_script(SETUP + r"""
assert.equal(elements['canvas-display'].hidden, true);
await elements['canvas-evidence'].onclick();
assert.equal(elements['canvas-detail'].hidden, true);
for (const panel of ['provenance', 'review', 'execution', 'environment']) {
  assert.equal(elements['panel-' + panel].hidden, true);
}
await elements['canvas-figures'].onclick();
assert.equal(elements.results.hidden, false);
assert.equal(posts().length, 0);
""")


def test_project_details_counts_discovered_files_and_chats_before_any_job():
    run_script(SETUP + r"""
assert.equal(server.jobs.length, 0);
hooks.onHistory([{
  id:'saved-chat',question:'A retained biological question',
  status:'awaiting_information',job_id:null
}]);
// Input discovery can finish after history. Opening Project refreshes the counts.
const placeholder = new Element('option'), input = new Element('option');
placeholder.value = ''; input.value = '/approved/synthetic-cells.h5ad';
input.textContent = 'synthetic-cells.h5ad';
elements['investigation-dataset'].replaceChildren(placeholder, input);
await elements['nav-overview'].onclick();
const stats = elements['overview-stats'].children.map(card => ({
  count: card.children[0].textContent,
  label: card.children[1].textContent.toLowerCase()
}));
assert.equal(stats.find(row => row.label.includes('file')).count, '1');
assert.equal(stats.find(row => row.label.includes('chat')).count, '1');
assert.equal(elements['project-dialog'].open, true);
assert.equal(posts().length, 0);
""")
