"""Project settings keep useful controls without empty or technical filler."""
from pathlib import Path
import re

from tests.test_workbench_project_structure import SETUP
from tests.test_workbench_ui_agent_asset import run_script


def test_project_settings_are_compact_and_keep_rename_controls():
    root = Path(__file__).resolve().parents[1]
    page = (root / "devbench/analysis/assets/workbench-ui.html").read_text()
    overview = re.search(r'<section id="overview".*?(?=<section id="investigation-inputs")', page, re.S).group(0)
    for identity in ["overview-name", "overview-stats", "project-name-input", "save-project-name", "project-identity", "overview-recent"]:
        assert 'id="' + identity + '"' in overview
    assert 'id="project-identity" class="field-note" hidden' in overview
    assert '<details class="project-settings">' not in overview
    for filler in ["Continue your research", "Recent work", "single-user workspace", "canonical", "Save name"]:
        assert filler not in overview


def test_empty_recent_activity_is_hidden_but_saved_activity_remains_available():
    run_script(SETUP + r"""
server.jobs=[];await elements['refresh-project'].onclick();
assert.equal(elements['overview-recent-section'].hidden,true);
assert.equal(elements['overview-recent'].children.length,0);
server.jobs=[{id:'fixture-job',question:'A saved analysis',status:'awaiting_operator_review',operation_kind:'analysis',execution_history:[]}];
await elements['refresh-project'].onclick();
assert.equal(elements['overview-recent-section'].hidden,false);
assert.equal(elements['overview-recent'].children.length,1);
assert.ok(elements['overview-recent'].children[0].textContent.includes('A saved analysis'));
assert.equal(posts().length,0);
""")


def test_name_save_and_error_controls_remain_functional_without_visible_identity_noise():
    run_script(SETUP + r"""
assert.equal(elements['project-identity'].hidden,true);
assert.ok(!elements['project-identity'].textContent.includes('single-user'));
elements['project-name-input'].value='Renamed project';
await elements['save-project-name'].onclick();
const saves=calls.filter(call=>call.method==='POST'&&call.path==='/api/workspace');
assert.ok(saves.some(call=>call.body.name==='Renamed project'));
assert.equal(elements['overview-name'].textContent,'Renamed project');
assert.equal(elements['project-identity'].hidden,true);
elements['project-name-input'].value='';elements['save-project-name'].onclick();
assert.ok(elements['workspace-message'].textContent.includes('Enter a project name'));
assert.equal(posts().filter(call=>call.path.endsWith('/run')||call.path.endsWith('/review')).length,0);
""")
