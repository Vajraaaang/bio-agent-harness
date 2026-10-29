"""Collection navigation must not retain the selected chat's detail panels."""
import pytest

from tests.test_workbench_project_structure import SETUP
from tests.test_workbench_ui_agent_asset import run_script


@pytest.mark.parametrize("canvas", ["code", "evidence", "display"])
@pytest.mark.parametrize("destination", ["runs", "library", "files"])
def test_project_tool_navigation_closes_previous_canvas_details(canvas, destination):
    run_script(SETUP + r"""
const canvas = CANVAS, destination = DESTINATION;
await elements['canvas-' + canvas].onclick();
await Promise.resolve(); await Promise.resolve();
assert.equal(elements['canvas-detail'].hidden, false);
const selectedQuestion = elements['inspector-title'].textContent;
await elements['nav-' + destination].onclick();
await Promise.resolve(); await Promise.resolve();
assert.equal(elements[destination].hidden, false);
assert.equal(elements['project-dialog'].open, true);
assert.equal(elements['canvas-detail'].hidden, false, 'project browsing preserves the underlying selected chat canvas');
for (const other of ['runs', 'library', 'files']) {
  if (other !== destination) assert.equal(elements[other].hidden, true);
}
assert.equal(elements.results.hidden, true);
assert.equal(elements['inspector-title'].textContent, selectedQuestion);
await elements['close-project-dialog'].onclick();
await elements['canvas-figures'].onclick();
await Promise.resolve(); await Promise.resolve();
assert.equal(elements.results.hidden, false, 'returning to Figures restores the chat output view');
assert.equal(elements['canvas-detail'].hidden, true);
assert.equal(elements['canvas-figures'].getAttribute('aria-current'), 'page');
assert.equal(posts().length, 0, 'navigation must not submit analysis or review work');
""".replace("CANVAS", repr(canvas)).replace("DESTINATION", repr(destination)))


@pytest.mark.parametrize("canvas", ["code", "evidence", "display"])
def test_new_chat_after_details_starts_with_a_clean_figures_canvas(canvas):
    run_script(SETUP + r"""
await elements['canvas-' + CANVAS].onclick();
await Promise.resolve(); await Promise.resolve();
assert.equal(elements['canvas-detail'].hidden, false);
await elements['nav-register'].onclick();
await Promise.resolve(); await Promise.resolve();
assert.equal(elements['inspector-title'].textContent, 'New chat');
assert.equal(elements.results.hidden, false);
assert.equal(elements['canvas-detail'].hidden, true);
assert.equal(elements['result-view'].hidden, true, 'a new chat must not inherit previous outputs');
assert.equal(elements['canvas-figures'].getAttribute('aria-current'), 'page');
assert.equal(posts().length, 0);
""".replace("CANVAS", repr(canvas)))


def test_project_details_counts_discovered_files_and_chats_before_any_job():
    run_script(SETUP + r"""
assert.equal(server.jobs.length, 0);
hooks.onHistory([{id:'saved-chat',question:'A retained biological question',status:'awaiting_information',job_id:null}]);
// Input discovery can finish after the history callback; opening Project must refresh.
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
