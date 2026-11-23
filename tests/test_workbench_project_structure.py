"""Project data stays shared and investigation selection owns review context."""

import re
from pathlib import Path

from tests.test_workbench_ui_agent_asset import run_script

SETUP = r"""
let hooks,currentChat={id:'saved-chat',question:'A retained biological question'};
const controller={
  ready:Promise.resolve(),render(){},current(){return currentChat;},
  newThread(){currentChat=null;hooks.onNewThread();},submit(){},
};
server.config.investigations={enabled:true};
context.window={
  WorkbenchConversation:{render(){},renderInvestigation(){}},
  WorkbenchInvestigations:{mount(options){hooks=options;return controller;}},
};
vm.runInContext(script,context);await startWorkspace();
"""


def test_project_details_does_not_replace_selected_chat_or_workbench():
    run_script(
        SETUP
        + r"""
assert.equal(elements.results.hidden,false);
assert.equal(elements.overview.hidden,true);
assert.equal(elements['inspector-title'].textContent,'A retained biological question');
elements['nav-overview'].onclick();
assert.equal(elements['project-dialog'].open,true);
assert.equal(elements.overview.hidden,false);
assert.equal(elements['project-dialog-title'].textContent,'Project details');
assert.equal(elements.results.hidden,false);
assert.equal(elements['inspector-title'].textContent,'A retained biological question');
elements['close-project-dialog'].onclick();
assert.equal(elements['project-dialog'].open,false);
assert.equal(elements.overview.hidden,true);
assert.equal(elements.results.hidden,false);
assert.equal(posts().length,0);
"""
    )


def test_project_data_discovery_stays_available_with_workbench_collapsed():
    run_script(
        SETUP
        + r"""
elements['workspace-canvas'].hidden=true;
elements['nav-data'].onclick();
assert.equal(elements['project-dialog'].open,true);
assert.equal(elements['investigation-inputs'].hidden,false);
assert.equal(elements['project-dialog-title'].textContent,'Project data');
assert.equal(elements['workspace-canvas'].hidden,true);
assert.equal(elements['inspector-title'].textContent,'A retained biological question');
elements['close-investigation-inputs'].onclick();
assert.equal(elements['project-dialog'].open,false);
assert.equal(elements['investigation-inputs'].hidden,true);
assert.equal(posts().length,0);
"""
    )


def test_new_investigation_closes_project_panel_and_clears_old_outputs():
    run_script(
        SETUP
        + r"""
elements['nav-overview'].onclick();
elements['nav-register'].onclick();
assert.equal(elements['project-dialog'].open,false);
assert.equal(elements['inspector-title'].textContent,'New chat');
assert.equal(elements['result-view'].hidden,true);
assert.equal(elements.results.hidden,false);
assert.equal(posts().filter(
  call=>call.path.endsWith('/run')||call.path.endsWith('/review')||call.path==='/api/jobs'
).length,0);
"""
    )


def test_project_activity_is_secondary_and_motion_cannot_intercept_clicks():
    assets = Path(__file__).resolve().parents[1] / "devbench/analysis/assets"
    page = (assets / "workbench-ui.html").read_text()
    theme = (assets / "workbench-theme.css").read_text()
    tools = re.search(r'<nav id="project-activity-tabs".*?</nav>', page, re.S)
    assert tools is not None
    markup = tools.group(0)
    assert 'id="project-tools"' not in page
    assert page.index('id="chat-section"') < tools.start()
    for identity in ("nav-analyses", "nav-runs", "nav-library", "nav-files"):
        assert f'id="{identity}"' in markup
    assert '<dialog id="project-dialog"' in page
    header = re.search(
        r'<header class="inspector-head">(.*?)</header>',
        page,
        re.S,
    ).group(1)
    assert "toggle-workbench" not in header and "toggle-navigation" not in header
    assert ".inspector-head #evidence-toggle { margin-left:0; }" in theme
    assert "::view-transition { pointer-events:none; }" in theme


def test_activity_opens_the_four_project_views_without_disrupting_chat():
    run_script(
        SETUP
        + r"""
elements['activity-control'].onclick();
assert.equal(elements['project-dialog'].open,true);
assert.equal(elements['project-activity-tabs'].hidden,false);
assert.equal(elements.runs.hidden,false);
elements['nav-files'].onclick();
assert.equal(elements.runs.hidden,true);
assert.equal(elements.files.hidden,false);
assert.equal(elements.results.hidden,false);
assert.equal(elements['inspector-title'].textContent,'A retained biological question');
"""
    )


def test_descriptive_findings_hide_empty_legacy_evidence_panels():
    run_script(
        SETUP
        + r"""
await elements['canvas-findings'].onclick();
assert.equal(elements.results.hidden,false);
assert.equal(elements['canvas-detail'].hidden,true);
assert.equal(elements['panel-provenance'].hidden,true);
assert.equal(elements['panel-review'].hidden,true);
assert.equal(elements['canvas-display'].hidden,true);
assert.equal(elements['evidence-toggle'].hidden,true);
"""
    )


def test_scientific_navigation_refreshes_and_hides_the_investigation_stage_bar():
    run_script(
        SETUP.replace(
            "server.config.investigations={enabled:true};",
            "server.config.investigations={enabled:true};"
            "server.config.scientific={enabled:true};"
            "server.scientific={initialized:false};",
        )
        + r"""
const prior=calls.filter(call=>call.path==='/api/scientific').length;
await elements['nav-scientific'].onclick();
assert.equal(elements.scientific.hidden,false);
assert.equal(elements['scientific-inspector'].hidden,false);
assert.equal(elements['wc-stage-bar'].hidden,true);
assert.equal(elements['scientific-attempts'].hidden,true);
assert.equal(elements['scientific-attempts-empty'].hidden,false);
assert.equal(calls.filter(call=>call.path==='/api/scientific').length,prior+1);
hooks.onGate({investigation:currentChat,workflowStage:3});
assert.equal(elements.scientific.hidden,false);
const history=elements['investigation-history'], button={};
history.listeners.click({target:{closest:selector=>
  selector==='.investigation-thread'?button:null}});
assert.equal(elements.scientific.hidden,true);
assert.equal(elements.results.hidden,false);
assert.equal(elements['wc-stage-bar'].hidden,false);
"""
    )


def test_saved_thesis_redirects_to_project_aims_without_a_workflow():
    run_script(
        SETUP.replace(
            "await startWorkspace();",
            "server.workspace.ui.view='thesis';await startWorkspace();",
        )
        + r"""
assert.equal(elements.thesis,undefined);
assert.equal(elements['project-dialog'].open,true);
assert.equal(elements['project-aims'].hidden,false);
assert.equal(elements.results.hidden,false);
elements['open-command-palette-btn'].onclick();
elements['workspace-search'].value='record';elements['workspace-search'].oninput();
elements['workspace-search'].value='';elements['workspace-search'].oninput();
elements['close-command-palette'].onclick();
assert.equal(elements['project-dialog'].open,true);
assert.equal(elements['project-aims'].hidden,false);
await elements['save-workspace'].onclick();
assert.equal(server.workspace.ui.view,'results');
"""
    )
