"""Project identity is fixed before requests and changes only through navigation."""
from tests.test_workbench_ui_agent_asset import run_script


SETUP = r"""
const projectA='a'.repeat(32),projectB='b'.repeat(32);
context.URLSearchParams=URLSearchParams;
context.location.search='?project_id='+projectA;
server.workspace.project.id=projectA;
server.config.project_catalog={enabled:true,current_project_id:projectA,read_only:false};
let projectOptions,navigation;
context.location.assign=value=>{navigation=value;};
context.window={WorkbenchProjects:{mount(options){projectOptions=options;return {ready:Promise.resolve(),destroy(){}};}}};
vm.runInContext(script,context);
await elements.connect.onclick();
"""


def test_project_header_is_present_from_the_first_configuration_request():
    run_script(SETUP+r"""
assert.ok(projectOptions);
assert.equal(projectOptions.currentProjectId,projectA);
assert.ok(calls.length>2);
for(const call of calls)assert.equal(call.headers['X-Workbench-Project'],projectA,call.path);
assert.equal(posts().length,0);
""")


def test_switch_saves_the_old_project_draft_then_reboots_without_relabeling_requests():
    run_script(SETUP+r"""
elements.question.value='Unsent question belonging to A';
await projectOptions.onSwitch(projectB);
assert.equal(server.workspace.ui.question_draft,'Unsent question belonging to A');
assert.equal(navigation,'/?project_id='+projectB+'#');
for(const call of calls)assert.equal(call.headers['X-Workbench-Project'],projectA,call.path);
assert.equal(posts().length,0);
""")


def test_project_switch_does_not_discard_a_draft_when_save_conflicts():
    run_script(SETUP+r"""
elements.question.value='Keep this draft';server.workspaceConflict=true;
await assert.rejects(()=>projectOptions.onSwitch(projectB),/Workspace changed/);
assert.equal(navigation,undefined);assert.equal(elements.question.value,'Keep this draft');
assert.equal(posts().length,0);
""")


def test_scientific_navigation_keeps_the_project_query():
    run_script(SETUP+r"""
server.config.scientific={enabled:true};server.workspace.schema_version=3;
const originalFetch=context.fetch;
context.fetch=async(path,options)=>path==='/api/scientific'?{ok:true,json:async()=>({initialized:false,studies:[],datasets:[],analyses:[],plans:[],operations:[],runs:[]})}:originalFetch(path,options);
const urls=[];context.history.replaceState=(_a,_b,value)=>urls.push(value);
await elements.connect.onclick();
elements['nav-data'].onclick();
assert.ok(urls.some(value=>value==='/?project_id='+projectA));
assert.ok(urls.every(value=>!value.includes('?')||value.includes('project_id='+projectA)));
assert.equal(posts().length,0);
""")
