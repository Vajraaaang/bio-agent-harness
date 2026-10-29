"""Normal chat is free-form and model-first; historical authorship remains evidence."""
from tests.test_workbench_conversation_redesign import run_js as conversation_js
from tests.test_workbench_investigation_asset import run_js as coordinator_js


DOM = """
class Node {constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}append(...nodes){this.children.push(...nodes);}replaceChildren(...nodes){this.children=nodes;}setAttribute(k,v){this.attrs[k]=v;}addEventListener(k,f){this[k]=f;}}
globalThis.document={createElement:tag=>new Node(tag),createElementNS:(ns,tag)=>new Node(tag)};
const walk=n=>[n,...n.children.flatMap(walk)];
"""


def test_new_chat_welcome_has_no_canned_questions_or_operator_mode_copy():
    conversation_js(DOM + """
const container=new Node('div');api.render(container,{config:{model_access:false}});
const nodes=walk(container),copy=nodes.map(n=>n.textContent||'').join(' ');
assert.match(copy,/Choose project data and ask a question/);
assert.equal(nodes.filter(n=>n.tagName==='button').length,0);
assert.ok(!copy.includes('Inspect cell quality')&&!copy.includes('Explore batch structure'));
assert.ok(!copy.includes('Operator-created')&&!copy.includes('No model conversation'));
""")


def test_historical_operator_authorship_is_secondary_evidence_not_primary_chrome():
    conversation_js(DOM + """
const state={id:'old',status:'approved',plan_author:'operator_created_recorded_plan',question:'Retained question'};
const container=new Node('div'),model=api.render(container,{state,config:{model_access:true}});
assert.equal(model.mode,'Operator-created plan');assert.match(model.scope,/No model conversation/);
const nodes=walk(container),source=nodes.find(n=>n.tagName==='details'&&walk(n).some(child=>child.textContent==='Planning source'));
assert.ok(source);assert.ok(walk(source).some(n=>n.textContent==='Operator-created plan'));
assert.ok(!nodes.some(n=>(n.className||'').split(' ').includes('wc-mode')));
assert.ok(!nodes.some(n=>n.tagName==='footer'));
""")


def test_new_chat_and_submit_use_agent_even_if_a_hidden_legacy_control_was_changed():
    coordinator_js("""
const f=fixture();await f.controller.ready;
assert.equal(f.options.modeSelect.value,'agent');
f.options.modeSelect.value='operator';f.controller.newThread();assert.equal(f.options.modeSelect.value,'agent');
f.options.modeSelect.value='operator';f.options.datasetSelect.value=source;f.options.questionInput.value='My own scientific question';
await f.controller.submit();
const create=f.calls.find(row=>row.path==='/api/investigations'&&row.body);
assert.equal(create.body.proposal_mode,'agent');assert.equal(create.body.question,'My own scientific question');
""")
