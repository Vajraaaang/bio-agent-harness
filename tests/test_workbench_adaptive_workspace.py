"""Investigation views lead with task state and checked findings."""

from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_investigation_asset import run_js as run_panel

DOM = """
class Node {
  constructor(tag) {this.tagName=tag;this.children=[];this.attrs={};}
  append(...nodes) {this.children.push(...nodes);}
  replaceChildren(...nodes) {this.children=nodes;}
  setAttribute(k,v) {this.attrs[k]=v;}
  addEventListener(k,f) {this[k]=f;}
}
globalThis.document={createElement:t=>new Node(t)};
const walk=node=>[node,...node.children.flatMap(walk)];
"""


def test_adaptive_views_show_execution_and_answer_with_receipts_secondary():
    run_js(DOM + """
const host=new Node('section');
const research={
  plan:{title:'Investigate mitochondrial signal',summary:'Compare profiles by depth.'},
  steps:[{title:'Capture QC',status:'succeeded',generation:1}],
  activity:{
    current:{title:'Codex is selecting a step',status:'running'},
    last:{title:'Capture QC succeeded',status:'succeeded'}
  },
  next_action:{label:'Approve follow-up',detail:'Confirm the exclusion rule.'},
  findings:{status:'available',answer:'Fixture fallback'},
  checked_observation:{status:'available',
    answer:'Mitochondrial signal varies across profiles.',
    metrics:[{label:'Profiles reviewed',value:10}],
    evidence:[{label:'QC receipt',sha256:'a'.repeat(64)}]},
  limitations:['One observed donor.']
};
api.renderAdaptiveResearch(host,research,{view:'plan'});
let nodes=walk(host);
for(const value of ['Codex is selecting a step','Capture QC succeeded',
  'Investigate mitochondrial signal','Capture QC']) {
  assert.ok(nodes.some(n=>n.textContent===value),value);
}
assert.ok(!nodes.some(n=>n.textContent==='Approve follow-up'));
delete research.activity.current;
api.renderAdaptiveResearch(host,research,{view:'plan'});
assert.ok(walk(host).some(n=>n.textContent==='Approve follow-up'));
api.renderAdaptiveResearch(host,research,{view:'findings'});
nodes=walk(host);
assert.ok(nodes.some(n=>
    n.textContent==='Mitochondrial signal varies across profiles.'));
assert.ok(!nodes.some(n=>n.textContent==='Fixture fallback'));
assert.ok(nodes.some(n=>n.textContent==='One observed donor.'));
assert.ok(nodes.find(n=>n.tagName==='details').children.some(n=>
  n.tagName==='summary' && n.textContent==='Supporting evidence'));
""")


def test_adaptive_intake_history_groups_confirmations_without_losing_evidence():
    run_js(DOM + """
const host=new Node('div');
const investigation={id:'i',workflow_mode:'adaptive/1',status:'adaptive_workflow',
  question:'Check profiles',turns:[{id:'a',kind:'clarification_answer',
    title:'Dataset context confirmed',content:{measurement:'raw counts'}},
    {id:'b',kind:'clarification_answer',title:'Dataset context confirmed',
      content:{unit:'donor'}},
    {id:'c',kind:'clarification_answer',title:'Dataset context confirmed',
      content:{layer:'counts'}}]};
api.renderInvestigation(host,{investigation});
const nodes=walk(host);
assert.equal(nodes.filter(n=>n.attrs['data-turn-kind']==='clarification_answer')
  .length,1);
for(const value of ['raw counts','donor','counts']) {
  assert.ok(nodes.some(n=>n.textContent===value),value);
}
""")


def test_adaptive_workflow_keeps_technical_lineage_collapsed_below_live_consent():
    run_panel("""
const host=new Element('section'),activity=[],calls=[];
const panel=globalThis.WorkbenchInvestigations.mountAdaptivePanel({
  host,document,taskCentered:true,onActivity:(id,change)=>activity.push({id,change}),
  async request(path,body){
    calls.push(body);
    if(body)return {steps_dispatched:0};
    return {mode:'adaptive/1',session_id:parent,graph_status:'available',revision:8,
      actions:{model_available:true,model_required:false},graph:{entries:[{
        entry_kind:'step',step_id:'capture-qc',status:'succeeded'}]}};
}});
await panel.select(parent);
const byText=value=>walk(host).find(node=>node.textContent===value);
const technical=host.children[0].children.find(node=>node.tagName==='details');
assert.equal(technical.open,false);
assert.equal(technical.children[0].textContent,
  'Inspect run lineage and technical records');
assert.equal(walk(technical).includes(byText('Ask Codex for the next step')),false);
let grant=walk(host).filter(node=>node.type==='checkbox');
grant[0].checked=true;grant[0].onchange();
assert.equal(byText('Ask Codex for the next step').disabled,true);
grant[1].checked=true;grant[1].onchange();
await byText('Ask Codex for the next step').onclick();
assert.deepEqual(activity.map(row=>row.change.status),['running','recorded']);
assert.ok(activity.every(row=>row.id===parent));
assert.equal(calls.filter(Boolean).length,1);
assert.equal(calls.find(Boolean).allow_model_execution,true);
grant=walk(host).filter(node=>node.type==='checkbox');
assert.ok(grant.every(node=>node.checked===false));
""")


def test_plan_activity_feed_shows_current_work_and_bounded_events_as_plain_text():
    run_js(DOM + """
const host=new Node('section');
const title='<img src=x onerror=alert(1)>';
const events=Array.from({length:15},(_,i)=>({title:'Run '+i,status:'succeeded'}));
const activity={events,current:{title,status:'running',detail:'Awaiting a response'}};
api.renderAdaptiveResearch(host,{activity},{view:'plan',activityFeed:true});
const feed=walk(host).find(node=>node.className?.includes('wc-research-feed'));
assert.equal(feed.open,false);
const [summary,list]=feed.children;
assert.equal(summary.tagName,'summary');
assert.ok(walk(summary).some(node=>node.textContent===title));
assert.equal(feed.attrs['data-activity-state'],'running');
assert.equal(list.children.length,12);
assert.equal(list.children[0].children[0].textContent,'Run 4');
assert.equal(list.children.at(-1).children[0].textContent,title);
assert.ok(!walk(feed).some(node=>node.tagName==='img'));
activity.current={title:'Awaiting review',status:'ready'};
api.renderAdaptiveResearch(host,{activity},{view:'plan',activityFeed:true});
assert.equal(walk(host).find(node=>node.className?.includes('wc-research-feed'))
  .attrs['data-activity-state'],'settled');
""")
