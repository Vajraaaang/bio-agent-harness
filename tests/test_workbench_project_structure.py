"""Project data stays shared; chat selection owns its workbench and review context."""
import re
from pathlib import Path
from tests.test_workbench_ui_agent_asset import run_script

SETUP = r"""
let hooks,currentChat={id:'saved-chat',question:'A retained biological question'};
const controller={ready:Promise.resolve(),render(){},current(){return currentChat;},newThread(){currentChat=null;hooks.onNewThread();},submit(){}};
server.config.investigations={enabled:true};
context.window={WorkbenchConversation:{render(){},renderInvestigation(){}},WorkbenchInvestigations:{mount(options){hooks=options;return controller;}}};
vm.runInContext(script,context);await elements.connect.onclick();
"""


def test_project_details_does_not_replace_selected_chat_or_workbench():
    run_script(SETUP+r"""
assert.equal(elements.results.hidden,false);assert.equal(elements.overview.hidden,true);
assert.equal(elements['inspector-title'].textContent,'A retained biological question');
elements['nav-overview'].onclick();
assert.equal(elements['project-dialog'].open,true);assert.equal(elements.overview.hidden,false);
assert.equal(elements['project-dialog-title'].textContent,'Project details');
assert.equal(elements.results.hidden,false);assert.equal(elements['inspector-title'].textContent,'A retained biological question');
elements['close-project-dialog'].onclick();
assert.equal(elements['project-dialog'].open,false);assert.equal(elements.overview.hidden,true);
assert.equal(elements.results.hidden,false);assert.equal(posts().length,0);
""")


def test_project_data_discovery_stays_available_with_workbench_collapsed():
    run_script(SETUP+r"""
elements['workspace-canvas'].hidden=true;
elements['nav-data'].onclick();
assert.equal(elements['project-dialog'].open,true);assert.equal(elements['investigation-inputs'].hidden,false);
assert.equal(elements['project-dialog-title'].textContent,'Project data');
assert.equal(elements['workspace-canvas'].hidden,true);assert.equal(elements['inspector-title'].textContent,'A retained biological question');
elements['close-investigation-inputs'].onclick();
assert.equal(elements['project-dialog'].open,false);assert.equal(elements['investigation-inputs'].hidden,true);
assert.equal(posts().length,0);
""")


def test_new_chat_closes_project_panel_and_clears_old_outputs():
    run_script(SETUP+r"""
elements['nav-overview'].onclick();elements['nav-register'].onclick();
assert.equal(elements['project-dialog'].open,false);
assert.equal(elements['inspector-title'].textContent,'New chat');
assert.equal(elements['result-view'].hidden,true);assert.equal(elements.results.hidden,false);
assert.equal(posts().filter(call=>call.path.endsWith('/run')||call.path.endsWith('/review')||call.path==='/api/jobs').length,0);
""")


def test_project_tools_are_secondary_and_motion_cannot_intercept_clicks():
    root=Path(__file__).resolve().parents[1]
    page=(root/'devbench/analysis/assets/workbench-ui.html').read_text()
    theme=(root/'devbench/analysis/assets/workbench-theme.css').read_text()
    tools=re.search(r'<details id="project-tools".*?</details>',page,re.S).group(0)
    assert '<summary>Project tools</summary>' in tools and ' open' not in tools.split('>')[0]
    for identity in ['nav-analyses','nav-runs','nav-library','nav-files']:
        assert 'id="'+identity+'"' in tools
    assert '<dialog id="project-dialog"' in page
    header=re.search(r'<header class="inspector-head">(.*?)</header>',page,re.S).group(1)
    assert 'toggle-workbench' not in header and 'toggle-navigation' not in header
    assert '.inspector-head #evidence-toggle { margin-left:0; }' in theme
    assert '::view-transition { pointer-events:none; }' in theme
