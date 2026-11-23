"""Recorded scientific review feedback is public, scoped, and non-authorizing."""
from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_workflow_accordion import SETUP


REVIEW = r"""
const binding={source:'/approved/cells.h5ad',source_sha256:'a'.repeat(64),project_id:'project',question_id:'chat',operation_id:'review-operation',result_ref:'b'.repeat(64)};
const feedback={format:'devbench.public-scientific-review/1',review_kind:'candidate_suitability',status:'insufficient_evidence',workflow_status:'blocked',rationale:'The stage and replicate support tables were not included in the review packet.',missing_evidence:['Retained embryo × population count table.','Stage-specific paired-unit support.'],action_consequences:'Method review cannot advance on the supplied packet.',competing_explanations:['Observed counts alone do not establish biological independence.'],evidence_refs:[{artifact_id:'c'.repeat(64),sha256:'c'.repeat(64),artifact_type:'result',size_bytes:1234}],evidence_ids:['claim-stage-support'],next_action:'Review the missing evidence and add material evidence before requesting another review.',binding,event_key:'review-key',raw_cli:'PRIVATE CLI TRACE',selection_history:[{reasoning:'PRIVATE REASONING'}]};
const investigation={id:'chat',question_id:'chat',revision:5,status:'awaiting_information',scientific_session_id:'session',scientific_plan:{scientific_candidates:[{name:'Proposed count model',method_family:'A count model with embryo effects.',assumptions:[],parameter_candidates:[]}]},artifacts:[{artifact_id:'c'.repeat(64)}],turns:[{id:'review-feedback',kind:'scientific_review_feedback',step:3,content:feedback,binding}]};
"""


def test_blocking_review_rationale_and_missing_evidence_are_the_visible_current_response():
    run_js(SETUP + REVIEW + r"""
render(investigation);const host=byClass(container,'wc-public-answer')[0],nodes=walk(host),card=byClass(host,'wc-scientific-review-feedback')[0];assert.ok(card);
assert.ok(nodes.some(n=>n.textContent==='Scientific review needs evidence'));
for(const value of [feedback.rationale,...feedback.missing_evidence,feedback.action_consequences,...feedback.competing_explanations,feedback.next_action])assert.ok(nodes.some(n=>n.textContent===value),value);
assert.ok(!nodes.some(n=>n.textContent==='Proposed count model'));
const methods=sections()[2];assert.ok(walk(methods).some(n=>n.textContent==='Proposed count model'));assert.ok(walk(methods).some(n=>n.textContent==='Current review feedback is shown above the overview.'));
assert.equal(walk(container).filter(n=>n.attrs['data-turn-id']==='review-feedback').length,1);
const raw=walk(card).flatMap(n=>[n.textContent,n.value]).filter(v=>typeof v==='string').join('\n');assert.ok(!raw.includes('PRIVATE CLI TRACE'));assert.ok(!raw.includes('PRIVATE REASONING'));
assert.equal(actions.length,0);nodes.find(n=>n.tagName==='button'&&n.textContent==='Inspect saved evidence').click();assert.deepEqual(actions[0],{type:'navigate',view:'evidence'});
""")


def test_review_text_is_literal_and_does_not_claim_approval_or_issue_grants():
    run_js(SETUP + REVIEW + r"""
feedback.rationale='<img src=x onerror=alert(1)>';feedback.missing_evidence=['<script>missing</script>'];investigation.artifacts=[];
render(investigation);const card=byClass(container,'wc-scientific-review-feedback')[0];assert.ok(card);const nodes=walk(card);
assert.ok(nodes.some(n=>n.textContent===feedback.rationale));assert.ok(nodes.some(n=>n.textContent===feedback.missing_evidence[0]));assert.ok(nodes.every(n=>n.innerHTML===undefined));
assert.equal(nodes.filter(n=>n.tagName==='button'&&!n.className?.includes('wc-copy-json')).length,0);assert.equal(actions.length,0);
assert.ok(!nodes.some(n=>n.textContent==='Approved'));
""")


def test_review_stays_in_history_when_its_exact_question_binding_is_older():
    run_js(SETUP + REVIEW + r"""
investigation.question_id='revised-question';render(investigation);
assert.equal(byClass(byClass(container,'wc-public-answer')[0],'wc-scientific-review-feedback').length,0);
assert.equal(byClass(byClass(container,'wc-conversation-trail')[0],'wc-scientific-review-feedback').length,1);
""")


def test_blocked_results_review_is_visible_ahead_of_retained_synthesis():
    run_js(SETUP + REVIEW + r"""
feedback.review_kind='results_conclusions';investigation.turns[0].step=5;investigation.synthesis={supported_findings:{summary:'Retained output summary'},method_evidence_assessment:{},uncertainties:[]};
render(investigation);const host=byClass(container,'wc-public-answer')[0];assert.equal(byClass(host,'wc-scientific-review-feedback').length,1);assert.equal(byClass(host,'wc-synthesis').length,0);
assert.equal(byClass(sections()[4],'wc-synthesis').length,1);assert.equal(investigation.synthesis.supported_findings.summary,'Retained output summary');
""")
