"""Public inline activity comes only from retained events, never model traces."""
from pathlib import Path
import re

import pytest
import tinycss2

from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_workflow_accordion import SETUP
from tests.test_workbench_descriptive_ui import DESCRIPTION
from tests.test_workbench_ui_agent_asset import run_script
from tests.test_workbench_project_structure import SETUP as PAGE_SETUP
from tests.test_workbench_investigation_asset import run_js as run_controller


ACTIVITY = r"""
const at=seconds=>'2026-11-02T12:00:'+String(seconds).padStart(2,'0')+'Z';
const lifecycle=(id,state,second)=>({id,investigation_id:'chat',kind:'lifecycle',at:at(second),step:1,content:{operation_id:'description-1',state}});
const operation=(id,status,second)=>({id,investigation_id:'chat',kind:'scientific_operation',at:at(second),step:3,content:{operation:{operation_id:'planning-1',action:'advance_to_gate',status,created_at:at(6),private_reasoning:'PRIVATE PROVIDER TRACE'}}});
const investigation={id:'chat',revision:4,status:'running',question:'Compare supplied populations',scientific_session_id:'session',operation_pending:true,planning_source:{backend:'codex-cli'},turns:[
 {id:'parent-event',investigation_id:'parent',kind:'error',at:at(0),content:{message:'Other question failure'}},
 lifecycle('queued','queued',0),lifecycle('start','running',1),lifecycle('done','completed',5),
 operation('plan-queued','queued',6),operation('plan-start','running',8),
 {id:'design-start',investigation_id:'chat',kind:'scientific_step',at:at(9),step:3,content:{step:{session_id:'session',node:'design',attempt:1,status:'active',evidence:{hidden_reasoning:'PRIVATE DESIGN TRACE'}}}}
],artifacts:[{artifact_id:'a'.repeat(64)},{artifact_id:'a'.repeat(64)},{artifact_id:'b'.repeat(64)}]};
"""


def test_activity_model_uses_real_steps_deduplicates_replays_and_never_claims_provider_activity():
    run_js(SETUP + ACTIVITY + r"""
investigation.turns.push(investigation.turns[1],{...investigation.turns[5],id:'repeated-running'});
const before=JSON.stringify(investigation),model=api.buildInvestigationModel(investigation,{}),activity=api.buildActivityModel(model,Date.parse(at(20)));
assert.equal(activity.state,'working');assert.equal(activity.label,'Checking design');assert.equal(activity.working,true);
assert.equal(activity.steps,3);assert.equal(activity.started,3);assert.equal(activity.completed,1);assert.equal(activity.artifacts,2);
assert.equal(activity.events.length,6);assert.equal(new Set(activity.events.map(row=>row.id)).size,6);
assert.ok(activity.events.every(row=>!row.label.includes('PRIVATE')&&!row.label.includes('Other question')));
assert.ok(!activity.label.includes('Codex'));assert.equal(activity.elapsed,'11 s elapsed');assert.equal(JSON.stringify(investigation),before);
""")


@pytest.mark.parametrize('status,state', [('queued', 'queued'), ('awaiting_operator_review', 'waiting'),
    ('awaiting_information', 'waiting'), ('approved', 'waiting'), ('cancel_requested', 'stopping'),
    ('cancelling', 'stopping'), ('cancelled', 'cancelled'), ('operation_failed', 'failed'),
    ('recovery_required', 'recovery'), ('completed', 'complete')])
def test_nonworking_states_never_animate_or_inherit_old_running_events(status, state):
    run_js(SETUP + ACTIVITY + r"""
investigation.status=STATUS;
const activity=api.buildActivityModel(api.buildInvestigationModel(investigation,{}),Date.parse(at(20)));
assert.equal(activity.state,STATE);assert.equal(activity.working,false);assert.equal(activity.elapsed,'');
render(investigation);assert.equal(byClass(container,'wc-activity-working').length,0);
""".replace('STATUS', repr(status)).replace('STATE', repr(state)))


def test_unknown_liveness_and_queued_child_never_show_thinking():
    run_js(SETUP + ACTIVITY + r"""
investigation.worker_liveness='unknown';let activity=api.buildActivityModel(api.buildInvestigationModel(investigation,{}));assert.equal(activity.state,'recovery');assert.equal(activity.working,false);
delete investigation.worker_liveness;investigation.status='planning';investigation.turns=[operation('only-queued','queued',6)];
activity=api.buildActivityModel(api.buildInvestigationModel(investigation,{}));assert.equal(activity.state,'queued');assert.equal(activity.started,0);assert.equal(activity.working,false);
render(investigation);assert.equal(byClass(container,'is-running').length,0);assert.equal(sections()[2].attrs['data-workflow-state'],'queued');
""")


def test_bound_error_does_not_invent_an_extra_operation_or_failed_step():
    run_js(SETUP + ACTIVITY + r"""
investigation.status='operation_failed';investigation.turns=[operation('start','running',1),operation('failed','failed',3),{id:'public-error',kind:'error',at:at(4),content:{operation_id:'planning-1',message:'The review workspace requires repair.'}}];
const activity=api.buildActivityModel(api.buildInvestigationModel(investigation,{}));assert.equal(activity.steps,1);assert.equal(activity.events.filter(row=>row.state==='failed').length,1);assert.equal(activity.started,1);
render(investigation);assert.ok(byClass(container,'wc-public-answer').some(host=>walk(host).some(n=>n.textContent==='The review workspace requires repair.')));
""")


def test_elapsed_time_requires_an_observed_running_start_and_creates_no_events():
    run_js(SETUP + ACTIVITY + r"""
investigation.turns=[{id:'unknown-time',kind:'scientific_operation',content:{operation:{operation_id:'op',action:'run_trial',status:'running',created_at:at(1)}}}];
const model=api.buildInvestigationModel(investigation,{}),first=api.buildActivityModel(model,Date.parse(at(20))),later=api.buildActivityModel(model,Date.parse(at(40)));
assert.equal(first.elapsed,'');assert.deepEqual(first.events,later.events);assert.equal(first.steps,1);
""")


def test_inline_feed_is_persistent_and_unchanged_poll_does_not_repeat_live_announcements():
    run_js(SETUP + ACTIVITY + r"""
render(investigation);const feed=byClass(container,'wc-inline-activity')[0],live=byClass(container,'wc-activity-announcement')[0],log=byClass(container,'wc-activity-log')[0];
assert.ok(feed);assert.equal(live.attrs['aria-live'],'polite');assert.equal(log.open,true);assert.equal(byClass(feed,'wc-activity-working').length,1);
let writes=0,saved=live.textContent;Object.defineProperty(live,'textContent',{get(){return saved;},set(value){saved=value;writes++;}});
log.open=false;render({...investigation,revision:5});
assert.equal(byClass(container,'wc-inline-activity')[0],feed);assert.equal(byClass(container,'wc-activity-announcement')[0],live);assert.equal(writes,0);assert.equal(byClass(container,'wc-activity-log')[0].open,false);
investigation.turns.push({id:'design-done',kind:'scientific_step',at:at(21),step:3,content:{step:{session_id:'session',node:'design',attempt:1,status:'completed'}}});render(investigation);assert.equal(writes,1);
assert.ok(!walk(feed).some(n=>(n.textContent||'').includes('PRIVATE')));
""")


def test_completed_activity_folds_to_actual_step_and_artifact_counts():
    run_js(SETUP + ACTIVITY + r"""
render(investigation);investigation.status='completed';investigation.operation_pending=false;
investigation.turns.push(operation('plan-done','completed',23),{id:'design-done',kind:'scientific_step',at:at(22),step:3,content:{step:{session_id:'session',node:'design',attempt:1,status:'completed'}}});
render(investigation);const feed=byClass(container,'wc-inline-activity')[0];assert.ok(feed);const log=byClass(feed,'wc-activity-log')[0];assert.equal(log.open,false);
assert.ok(walk(log).some(n=>(n.textContent||'').includes('3 steps')&&(n.textContent||'').includes('2 artifacts')));
assert.equal(byClass(feed,'wc-activity-working').length,0);assert.equal(byClass(feed,'wc-activity-row').length,8);
""")


def test_current_answer_is_visible_outside_the_five_question_trail_without_duplicate_tables():
    run_js(SETUP + DESCRIPTION + r"""
render(investigation);const publicAnswer=byClass(container,'wc-public-answer')[0],trail=byClass(container,'wc-conversation-trail')[0];
assert.ok(publicAnswer);assert.ok(walk(publicAnswer).some(n=>n.textContent==='Stored labels\n\nThese are the observed values.'));
assert.equal(walk(publicAnswer).filter(n=>n.tagName==='table').length,1);assert.equal(walk(trail).filter(n=>n.tagName==='table').length,0);
assert.equal(sections().length,5);assert.equal(walk(container).filter(n=>n.attrs['data-turn-id']==='a').length,1);
assert.ok(walk(sections()[3]).some(n=>n.textContent==='Current answer is shown above the overview.'));
""")


def test_actual_public_planning_proposal_is_visible_and_keeps_assumptions_and_origins():
    run_js(SETUP + r"""
const investigation={id:'chat',revision:4,status:'operation_failed',question:'Compare populations',scientific_session_id:'s',scientific_plan:{scientific_candidates:[{name:'Embryo-aware NB contrast',method_family:'A count model with embryo effects.',dataset_fit:'plausible',objective_fit:'strong',assumptions:[{statement:'Embryos are independent biological units.',status:'unknown',evidence:'Supplied source declaration'}],parameter_candidates:[{name:'procedure',value:'nb_v1',origin:'assistant',rationale:'A proposed fixed procedure.'}],known_failure_modes:['Sparse stages may not be estimable.'],source:{type:'paper',reference:'Retained source'}}],selection_history:[{raw_reasoning:'PRIVATE TRACE'}]},turns:[]};
render(investigation);const answer=byClass(container,'wc-public-answer')[0];assert.ok(answer);const values=walk(answer).map(n=>n.textContent||'').join('\n');
assert.match(values,/Proposed approach/);assert.match(values,/Embryo-aware NB contrast/);assert.match(values,/A count model with embryo effects/);assert.match(values,/Embryos are independent biological units/);assert.match(values,/Unknown/);assert.match(values,/Assistant/);assert.ok(!values.includes('PRIVATE TRACE'));assert.ok(!values.includes('Approved'));
""")


def test_duplicate_conclusion_does_not_repeat_the_same_visible_answer():
    run_js(SETUP + DESCRIPTION + r"""
investigation.turns.push({id:'conclusion',kind:'conclusion',step:5,content:{message:answer.message}});render(investigation);
const host=byClass(container,'wc-public-answer')[0];assert.equal(byClass(host,'wc-message-body').filter(n=>n.textContent===answer.message).length,1);
""")


def test_later_verified_results_take_precedence_over_historical_method_proposals():
    run_js(SETUP + DESCRIPTION + r"""
investigation.request.category='inferential_analysis';investigation.scientific_plan={scientific_candidates:[{name:'Earlier proposal',method_family:'Earlier method',assumptions:[],parameter_candidates:[]}],experiments:[{id:'reviewed-trial'}]};
investigation.turns=[{id:'result',kind:'singlecell_evidence',step:4,content:{tool_name:'scrna_population_contrast',status:'completed',message:'The recorded contrast is available.',binding,tables:[],summary:{estimated_genes:8},limitations:[]}}];render(investigation);
const host=byClass(container,'wc-public-answer')[0];assert.ok(walk(host).some(n=>n.textContent==='The recorded contrast is available.'));assert.ok(!walk(host).some(n=>n.textContent==='Earlier proposal'));
""")


def test_public_proposal_projection_refreshes_even_without_revision_or_turn_changes():
    run_controller(r"""
const f=fixture();await f.controller.ready;await f.controller.open(parent);const before=f.renders();
f.server.record.scientific_plan={scientific_candidates:[{name:'Retained public proposal'}]};await f.controller.refresh();assert.equal(f.renders(),before+1);
""")


def test_proposal_disclosure_and_summary_focus_survive_a_poll():
    run_js(SETUP + r"""
const matches=(node,selector)=>{const attr=selector.match(/^\[([^\]]+)\]$/);if(attr)return node.attrs[attr[1]]!==undefined;const [tag,...classes]=selector.split('.');return (!tag||node.tagName===tag)&&classes.every(c=>(node.className||'').split(' ').includes(c));};
Node.prototype.append=function(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}};
Node.prototype.replaceChildren=function(...nodes){this.children=[];this.append(...nodes);};
Node.prototype.querySelectorAll=function(selector){return walk(this).slice(1).filter(n=>selector.split(',').some(s=>matches(n,s)));};
Node.prototype.querySelector=function(selector){return this.querySelectorAll(selector)[0]||null;};
Node.prototype.closest=function(selector){let n=this;while(n){if(matches(n,selector))return n;n=n.parentElement;}return null;};
Node.prototype.contains=function(node){return walk(this).includes(node);};Node.prototype.focus=function(){document.activeElement=this;};document.getElementById=id=>walk(container).find(n=>n.attrs.id===id);
const investigation={id:'chat',revision:1,status:'awaiting_operator_review',scientific_plan:{scientific_candidates:[{name:'Retained proposal',method_family:'Proposed model',assumptions:[],parameter_candidates:[]}]},turns:[]};
render(investigation);let details=byClass(container,'wc-public-assumptions')[0];details.open=true;const summary=details.children[0];assert.ok(summary.attrs.id);summary.focus();container.scrollTop=95;
render({...investigation,revision:2});details=byClass(container,'wc-public-assumptions')[0];assert.equal(details.open,true);assert.equal(document.activeElement,details.children[0]);assert.equal(container.scrollTop,95);
""")


def test_new_chat_answer_is_revealed_once_and_unchanged_poll_does_not_move_scroll():
    run_js(SETUP + DESCRIPTION + r"""
const reveals=[];let scrollTop=0;
const body={get scrollTop(){return scrollTop;},set scrollTop(value){scrollTop=value;reveals.push(value);},
  getBoundingClientRect:()=>({top:80})};
container.closest=selector=>selector==='.inspector-body'?body:null;
Node.prototype.getBoundingClientRect=function(){return {top:120-scrollTop};};
Node.prototype.scrollIntoView=()=>assert.fail('Revealing the answer must not scroll the chat header');
render(investigation);assert.deepEqual(reveals,[40]);
render({...investigation,revision:3});assert.deepEqual(reveals,[40]);
render({...investigation,id:'followup',revision:1});assert.deepEqual(reveals,[40,40]);
""")


def test_preliminary_explanation_is_a_public_hypothesis_with_uncertainty_not_final_fact():
    run_js(SETUP + r"""
render({id:'chat',revision:4,status:'awaiting_operator_review',scientific_plan:{preliminary_explanation:{response:{hypotheses:[{id:'h1',statement:'The observed contrast may reflect composition.',limitations:['Alternative biological context remains possible.'],evidence_refs:[]}],ambiguity_items:[{id:'a1',discriminating_criterion:'Independent cell-type evidence is needed.'}],uncertainty:['Labels are supplied.'],absent_assessments:['Independent biological review']},raw_reasoning:'PRIVATE EXPLANATION'}},turns:[]});
const answer=byClass(container,'wc-public-answer')[0];assert.ok(answer);const values=walk(answer).map(n=>n.textContent||'').join('\n');
assert.match(values,/Preliminary explanation/);assert.match(values,/may reflect composition/);assert.match(values,/Labels are supplied/);assert.match(values,/Independent biological review/);assert.ok(!values.includes('PRIVATE EXPLANATION'));
""")


def test_header_dimensions_are_observations_and_features_without_assay_confirmation():
    run_js(SETUP + r"""
render({id:'chat',revision:1,status:'awaiting_information',profile:{filename:'input.h5ad',observed:{cells:80,genes:60},declarations:{}},turns:[]});
assert.ok(walk(container).some(n=>n.textContent==='input.h5ad · 80 observations × 60 features'));
""")


@pytest.mark.parametrize('status', ['failed', 'scientific_refusal', 'recovery_required'])
def test_stopped_synthesis_points_to_diagnostics_without_claiming_a_saved_fit(status):
    run_js(SETUP + r"""
render({id:'chat',revision:1,status:'awaiting_information',turns:[],
  synthesis_continuation:{status:STATUS,next_node:'recommend',eligible:false,steps:[]}});
const card=byClass(container,'wc-synthesis-continuation')[0];
const nodes=walk(card),copy=nodes.map(node=>node.textContent||'').join(' ');
assert.doesNotMatch(copy,/The fit is saved|Verified results/);
assert.match(copy,/trial stopped/i);
nodes.find(node=>node.textContent==='Inspect diagnostic receipts').click();
assert.equal(actions.at(-1).view,'evidence');
""".replace('STATUS', repr(status)))


def test_canvas_headline_uses_header_dimensions_without_assay_claims():
    run_script(PAGE_SETUP + r"""
currentChat={id:'saved-chat',question:'Inspect stored metadata',source:'/approved/input.h5ad',profile:{observed:{cells:80,genes:60}}};
hooks.onGate({investigation:currentChat,jobState:null,kind:null});await Promise.resolve();await Promise.resolve();
assert.match(elements['workspace-dataset'].textContent,/80 observations × 60 features/);
""")


def test_working_text_has_white_left_to_right_sweep_and_accessible_static_fallback():
    theme = (Path(__file__).resolve().parents[1] / 'src/devbench/analysis/assets/workbench-theme.css').read_text()
    rules = tinycss2.parse_stylesheet(theme, skip_comments=True, skip_whitespace=True)
    assert not [rule for rule in rules if rule.type == 'error']
    sweeps = [tinycss2.serialize(rule.content) for rule in rules if rule.type == 'at-rule' and rule.lower_at_keyword == 'keyframes' and 'wc-working-sweep' in tinycss2.serialize(rule.prelude)]
    assert len(sweeps) == 1
    assert '-100%' in sweeps[0] and '200%' in sweeps[0]
    assert 'linear-gradient(90deg' in theme and '#ffffff' in theme
    reduced = [tinycss2.serialize(rule.content) for rule in rules if rule.type == 'at-rule' and rule.lower_at_keyword == 'media' and 'prefers-reduced-motion:reduce' in tinycss2.serialize(rule.prelude)]
    assert any('wc-activity-working' in rule and 'animation:none' in rule for rule in reduced)


def test_white_sweep_keeps_small_working_text_readable_on_the_light_surface():
    theme = (Path(__file__).resolve().parents[1] / 'src/devbench/analysis/assets/workbench-theme.css').read_text()
    text_rule = re.search(r'\.wc-activity-text\s*\{([^}]+)\}', theme).group(1)
    sweep_rule = re.search(r'\.wc-activity-working \.wc-activity-text::after\s*\{([^}]+)\}', theme).group(1)
    color_token = re.search(r'color:var\((--[^)]+)\)', text_rule).group(1)
    root = re.search(r':root\s*\{([^}]+)\}', theme).group(1)
    base = re.search(re.escape(color_token) + r':\s*(#[0-9a-fA-F]{6})', root).group(1)
    opacity_match = re.search(r'opacity:([0-9.]+)', sweep_rule)
    assert opacity_match, 'Opaque white on a white surface would erase the working text'
    opacity = float(opacity_match.group(1))
    channels = [(int(base[index:index + 2], 16) * (1 - opacity) + 255 * opacity) / 255 for index in (1, 3, 5)]
    luminance = sum(weight * (value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4) for weight, value in zip((.2126, .7152, .0722), channels))
    assert 1.05 / (luminance + .05) >= 4.5
