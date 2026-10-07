"""Scientific browser controls with an isolated DOM and recorded API responses."""
from pathlib import Path

import pytest

from tests.test_workbench_ui_agent_asset import run_script


SETUP = r"""
context.URLSearchParams=URLSearchParams;
context.crypto={randomUUID:()=> 'request-'+(++server.keyNumber)};
server.keyNumber=0;
const study='1'.repeat(32),dataset='2'.repeat(32),analysis='3'.repeat(32),result='4'.repeat(32),session='5'.repeat(32),run='6'.repeat(32);
const ref=(kind,id,version)=>({object_type:kind,object_id:id,version_id:version.repeat(64)});
const datasetRef=ref('dataset',dataset,'a'),analysisRef=ref('analysis',analysis,'b'),resultRef=ref('result',result,'c');
server.config.scientific={enabled:true,initialized:true,planning_mode:'recorded_fixture',data_execution_allowed:true,model_execution_allowed:false};
server.workspace={...server.workspace,schema_version:3,ui:{view:'scientific',tab:'analyst',study_id:study,dataset_ref:datasetRef,analysis_ref:analysisRef,result_ref:null,scientific_run_id:null,planner_session_id:session}};
server.scientific={initialized:true,revision:7,studies:[{study_id:study,name:'Synthetic QC',aim:'Inspect bounded synthetic counts'}],
 datasets:[{dataset_id:dataset,study_id:study,name:'Counts',ref:datasetRef}],
 dataset_versions:[{dataset_id:dataset,version_id:datasetRef.version_id,ref:datasetRef,manifest:{members:{dataset:{artifact_id:'d'.repeat(64)}},metadata:{data_classification:'synthetic'}}}],
 analyses:[{analysis_id:analysis,study_id:study,name:'QC analysis',ref:analysisRef}],
 analysis_versions:[{analysis_id:analysis,version_id:analysisRef.version_id,ref:analysisRef,definition:{method_version_ids:['method-v1'],parameters:{},task:{scientific_objective:'Inspect synthetic counts'}}}],
 results:[],result_versions:[],runs:[],attempts:[],validations:[],reviews:[],selections:[],operations:[],imports:[],
 methods:[{version_id:'method-v1',contract:{name:'Synthetic QC',provenance:{origin:'registered'},lifecycle:'registered',implementation_trust:'reviewed',implementation:{kind:'builtin'},environment:{}}}],
 plans:[{session_id:session,scientific_anchors:{study_id:study,dataset_versions:[datasetRef],analysis_version:analysisRef},scientific_candidates:[{name:'Synthetic QC'}],candidates:[{name:'Synthetic QC',method_version_id:'method-v1',applicability:'eligible'}],trial_designs:[{proposals:[{id:'qc-trial',candidates:['Synthetic QC'],inputs:{'Synthetic QC':{dataset:'d'.repeat(64)}},parameters:{}}]}],experiments:[{id:'qc-declared',status:'declared'}],results:[]}]};
const originalFetch=context.fetch;
context.fetch=async(path,options)=>{
 if(path==='/api/workspace'&&options.method==='POST'&&server.workspace.schema_version===3){
   const ui=JSON.parse(options.body).ui;
   for(const key of ['study_id','scientific_run_id','planner_session_id'])assert.ok(typeof ui[key]==='string'&&(!ui[key]||/^[0-9a-f]{32}$/.test(ui[key])),key+' must be an empty string or exact identity');
 }
 if(!path.startsWith('/api/scientific'))return originalFetch(path,options);
 const body=options.body===undefined?undefined:JSON.parse(options.body);calls.push({path,...options,body});
 let value=server.scientific;
 if(path.startsWith('/api/scientific/methods/'))value=server.source;
 if(path.startsWith('/api/scientific/resolve?')){
   const query=new URLSearchParams(path.split('?')[1]);
   value=(server.scientific[query.get('object_type')+'_versions']||[]).find(row=>row.version_id===query.get('version_id')&&row.ref.object_id===query.get('object_id'));
   if(!value)return {ok:false,status:404,json:async()=>({message:'Exact scientific version unavailable'})};
 }
 if(options.method==='POST'){
   if(server.actionFailure){const failure=server.actionFailure;return {ok:false,status:failure.status,json:async()=>({message:failure.message})};}
   assert.equal(body.expected_revision,server.scientific.revision);
   server.scientific.revision++;
   if(server.onAction)value=await server.onAction(body);
   else value={operation_id:'operation-1',run_id:run,status:'queued'};
 }
 return {ok:true,json:async()=>JSON.parse(JSON.stringify(value)),blob:async()=> 'fixture'};
};
const sciPosts=()=>calls.filter(call=>call.path==='/api/scientific'&&call.method==='POST');
const connectScience=async()=>{elements.token.value='local-token';await elements.connect.onclick();assert.equal(elements['login-error'].textContent,'Restoring your local workspace…');};
"""


def test_scientific_restore_keeps_exact_versions_without_permissions_or_execution():
    run_script(SETUP + r"""
server.scientific.dataset_versions.push({...server.scientific.dataset_versions[0],version_id:'e'.repeat(64),ref:ref('dataset',dataset,'e')});
server.scientific.datasets[0].ref=ref('dataset',dataset,'e');
await connectScience();
assert.equal(elements.scientific.hidden,false);
assert.ok(elements['scientific-dataset'].value.includes(datasetRef.version_id));
assert.equal(elements['scientific-data-grant'].checked,false);
assert.equal(elements['scientific-model-grant'].checked,false);
assert.equal(sciPosts().length,0);
await elements['save-workspace'].onclick();
assert.deepEqual(server.workspace.ui.dataset_ref,datasetRef);
assert.ok(!Object.keys(server.workspace.ui).some(key=>/grant|idempotency|allow_/.test(key)));
""")


def test_missing_version_remains_selected_and_cannot_plan():
    run_script(SETUP + r"""
server.workspace.ui.dataset_ref=ref('dataset',dataset,'f');
await connectScience();
assert.ok(elements['scientific-dataset'].value.includes('f'.repeat(64)));
assert.match(elements['scientific-reference-message'].textContent,/unavailable/i);
assert.equal(elements['scientific-create-plan'].disabled,true);
await elements['save-workspace'].onclick();
assert.equal(server.workspace.ui.dataset_ref.version_id,'f'.repeat(64));
assert.equal(sciPosts().length,0);
""")


def test_fixture_planning_is_labeled_and_does_not_grant_model_execution():
    run_script(SETUP + r"""
await connectScience();
assert.match(elements['scientific-mode'].textContent,/Recorded fixture/);
await elements['scientific-resolve'].onclick();
assert.equal(sciPosts().length,1);
assert.equal(sciPosts()[0].body.action,'resolve');
assert.equal(sciPosts()[0].body.actor,'local-researcher');
assert.ok(!sciPosts()[0].body.allow_model_execution);
assert.match(elements['scientific-methods'].textContent,/registered/);
assert.match(elements['scientific-methods'].textContent,/reviewed/);
""")


def test_execution_requires_fresh_grant_and_failed_retry_reuses_request_identity():
    run_script(SETUP + r"""
await connectScience();
elements['scientific-experiment'].value='qc-declared';await elements['scientific-experiment'].onchange();
await elements['scientific-run'].onclick();assert.equal(sciPosts().length,0);
elements['scientific-data-grant'].checked=true;elements['scientific-data-grant'].onchange();
server.actionFailure={status:503,message:'Response was lost; refresh evidence before retrying'};
await elements['scientific-run'].onclick();
assert.equal(elements['scientific-data-grant'].checked,false);
const key=sciPosts()[0].body.idempotency_key;
elements['scientific-data-grant'].checked=true;elements['scientific-data-grant'].onchange();
await elements['scientific-run'].onclick();
assert.equal(sciPosts().length,2);assert.equal(sciPosts()[1].body.idempotency_key,key);
assert.equal(sciPosts()[1].body.allow_data_execution,true);
assert.match(elements['scientific-message'].textContent,/Response was lost/);
""")


def test_stale_mutation_keeps_trial_draft_and_visible_error_through_refresh():
    run_script(SETUP + r"""
await connectScience();
elements['scientific-trial-json'].value='{"id":"researcher-draft","parameters":{"threshold":7}}';
server.actionFailure={status:409,message:'Scientific workspace revision changed; refresh before applying this action'};
await elements['scientific-declare'].onclick();
assert.match(elements['scientific-message'].textContent,/revision changed/);
await elements['scientific-refresh'].onclick();
assert.match(elements['scientific-message'].textContent,/revision changed/);
assert.equal(JSON.parse(elements['scientific-trial-json'].value).parameters.threshold,7);
assert.equal(sciPosts().length,1);
""")


def test_acceptance_records_review_only_and_requires_warning_acknowledgment():
    run_script(SETUP + r"""
server.scientific.results=[{result_id:result,analysis_id:analysis,study_id:study,ref:resultRef,review_status:'candidate'}];
server.scientific.result_versions=[{result_id:result,version_id:resultRef.version_id,ref:resultRef,run_id:run,attempt_id:'attempt',validation_id:'validation',artifact_ids:[],interpretation:'Synthetic result'}];
server.scientific.validations=[{validation_id:'validation',status:'passed',observations:{passed:true,warnings:['Synthetic only']}}];
server.workspace.ui.result_ref=resultRef;
await connectScience();
elements['scientific-review-rationale'].value='Checked the retained synthetic validation';
await elements['scientific-accept'].onclick();assert.equal(sciPosts().length,0);
elements['scientific-warning-ack'].checked=true;elements['scientific-warning-ack'].onchange();
elements['scientific-select-role'].value='primary-qc';
await elements['scientific-accept'].onclick();
assert.deepEqual(sciPosts().map(call=>call.body.action),['accept_result']);
assert.deepEqual(sciPosts()[0].body.result_ref,resultRef);
assert.equal(sciPosts()[0].body.acknowledge_warnings,true);
assert.equal(sciPosts()[0].body.select_role,'primary-qc');
assert.ok(!sciPosts()[0].body.allow_data_execution);
""")


def test_schema_two_does_not_save_new_fields_until_explicit_initialization():
    run_script(SETUP + r"""
server.workspace.schema_version=2;server.workspace.ui={view:'analyses',tab:'analyst'};
server.scientific.initialized=false;server.scientific.revision=0;
server.onAction=async body=>{assert.equal(body.action,'initialize');server.scientific.initialized=true;server.workspace.schema_version=3;server.workspace.revision++;return {initialized:true};};
await connectScience();await elements['open-scientific'].onclick();
await elements['save-workspace'].onclick();
assert.equal(server.workspace.ui.dataset_ref,undefined);
assert.equal(server.workspace.ui.view,'analyses');
await elements['scientific-initialize'].onclick();
assert.equal(sciPosts().length,1);assert.equal(sciPosts()[0].body.action,'initialize');
await elements['save-workspace'].onclick();
assert.equal(server.workspace.schema_version,3);assert.equal(server.workspace.ui.view,'scientific');
""")


def test_live_planning_consumes_only_an_explicit_fresh_model_grant():
    run_script(SETUP + r"""
server.config.scientific.planning_mode='codex-cli';server.config.scientific.model_execution_allowed=true;
await connectScience();
await elements['scientific-resolve'].onclick();assert.equal(sciPosts().length,0);
elements['scientific-model-grant'].checked=true;elements['scientific-model-grant'].onchange();
await elements['scientific-resolve'].onclick();
assert.equal(sciPosts().length,1);assert.equal(sciPosts()[0].body.allow_model_execution,true);
assert.equal(elements['scientific-model-grant'].checked,false);
await elements['scientific-resolve'].onclick();assert.equal(sciPosts().length,1);
""")


def test_exact_deep_link_overrides_saved_context_and_never_contains_session_token():
    run_script(SETUP + r"""
const linked=ref('dataset',dataset,'e');server.scientific.dataset_versions.push({...server.scientific.dataset_versions[0],version_id:linked.version_id,ref:linked});
context.location.search='?'+new URLSearchParams(linked).toString();
await connectScience();
assert.ok(elements['scientific-dataset'].value.includes(linked.version_id));
assert.equal(elements['scientific-analysis'].value,'');assert.equal(elements['scientific-plan'].value,'');
const link=elements['scientific-exact-links'].children[0];
assert.ok(link.href.includes(linked.version_id));assert.ok(!link.href.includes('local-token'));assert.ok(!link.href.includes('#'));
await elements['scientific-refresh'].onclick();
assert.ok(elements['scientific-dataset'].value.includes(linked.version_id));assert.equal(sciPosts().length,0);
""")


def test_completed_operation_with_failed_trial_shows_retained_failure_without_success_claim():
    run_script(SETUP + r"""
server.scientific.operations=[{action:'run_trial',operation_id:'operation-1',run_id:run,status:'completed',value:{results:[],unavailable:[{trial_status:'resource_limit',message:'Trial evidence is not eligible for acceptance',failures:['Budget exceeded']}]}}];
server.scientific.recovery_notice='Validated backup restored; inspect retained evidence.';
await connectScience();
assert.match(elements['scientific-operations'].children[0].textContent,/resource_limit/);
assert.match(elements['scientific-operations'].children[0].textContent,/Budget exceeded/);
assert.match(elements['scientific-operations'].children[0].textContent,/Inspect the run and validation/);
assert.equal(elements['scientific-accept'].disabled,true);
assert.match(elements['scientific-recovery'].textContent,/Validated backup/);
assert.equal(sciPosts().length,0);
""")


def test_verified_source_preview_is_rendered_as_inert_text():
    run_script(SETUP + r"""
server.source={source_text:'<script>globalThis.executed=true</script>\nprint("draft")',source_sha256:'f'.repeat(64),source_scope:'Generated unexecuted source',source_truncated:false,execution_started:false};
await connectScience();elements['scientific-method-source'].value='method-v1';elements['scientific-method-source'].onchange();
await elements['scientific-source-preview'].onclick();
assert.equal(elements['scientific-source-code'].textContent,server.source.source_text);
assert.equal(context.executed,undefined);assert.equal(sciPosts().length,0);
assert.match(elements['scientific-source-message'].textContent,/No source execution was requested/);
""")


def test_declaration_uses_researcher_draft_without_silent_input_rebinding():
    run_script(SETUP + r"""
await connectScience();elements['scientific-trial-proposal'].value='0:0';elements['scientific-trial-proposal'].onchange();
const draft=JSON.parse(elements['scientific-trial-json'].value);draft.parameters={threshold:7};draft.inputs['Synthetic QC'].dataset='explicit-researcher-binding';
elements['scientific-trial-json'].value=JSON.stringify(draft);
server.onAction=async body=>{assert.equal(body.action,'declare_trial');const experiment={...body.spec,status:'declared'};server.scientific.plans[0].experiments.push(experiment);return {experiment,analysis_ref:analysisRef};};
await elements['scientific-declare'].onclick();
assert.deepEqual(sciPosts()[0].body.spec,draft);
assert.ok(elements['scientific-frozen-trial'].textContent.includes('explicit-researcher-binding'));
elements['scientific-trial-json'].value='{"id":"unrelated-edit"}';
await elements['scientific-refresh'].onclick();
assert.ok(elements['scientific-frozen-trial'].textContent.includes('explicit-researcher-binding'));
assert.equal(sciPosts().length,1);
""")


def test_explicit_legacy_import_keeps_unresolved_evidence_visible():
    run_script(SETUP + r"""
await connectScience();elements['scientific-import-id'].value='retained-method-run';
server.onAction=async body=>{const record={source_id:body.source_id,status:'unresolved',reason:'Explicit dataset and analysis mappings required'};server.scientific.imports.push(record);return record;};
await elements['scientific-import-form'].onsubmit({preventDefault(){}});
assert.equal(sciPosts()[0].body.mappings,undefined);assert.match(elements['scientific-imports'].textContent,/unresolved/);
elements['scientific-import-mappings'].checked=true;
await elements['scientific-import-form'].onsubmit({preventDefault(){}});
assert.deepEqual(sciPosts()[1].body.mappings,{study_id:study,dataset_ref:datasetRef,analysis_ref:analysisRef});
assert.deepEqual(sciPosts().map(call=>call.body.action),['import_legacy','import_legacy']);
""")


def test_inflight_double_submission_does_not_send_a_second_execution_request():
    run_script(SETUP + r"""
await connectScience();elements['scientific-experiment'].value='qc-declared';await elements['scientific-experiment'].onchange();
let release;server.onAction=async()=>{await new Promise(resolve=>release=resolve);return {operation_id:'operation-1',run_id:run,status:'queued'};};
elements['scientific-data-grant'].checked=true;elements['scientific-data-grant'].onchange();
const pending=elements['scientific-run'].onclick();await Promise.resolve();
await elements['scientific-run'].onclick();assert.equal(sciPosts().length,1);
release();await pending;
assert.equal(elements['scientific-data-grant'].checked,false);
assert.equal(server.workspace.ui.scientific_run_id,run);
""")


def test_separate_rerun_requires_another_explicit_request_and_keeps_versions():
    run_script(SETUP + r"""
await connectScience();elements['scientific-data-grant'].checked=true;elements['scientific-data-grant'].onchange();
await elements['scientific-rerun'].onclick();
const first=sciPosts()[0].body;
assert.equal(elements['scientific-new-rerun'].hidden,false);
elements['scientific-new-rerun'].onclick();assert.equal(elements['scientific-data-grant'].checked,false);
await elements['scientific-rerun'].onclick();assert.equal(sciPosts().length,1);
elements['scientific-data-grant'].checked=true;elements['scientific-data-grant'].onchange();
await elements['scientific-rerun'].onclick();
assert.equal(sciPosts().length,2);assert.notEqual(sciPosts()[1].body.idempotency_key,first.idempotency_key);
assert.deepEqual(sciPosts()[1].body.analysis_ref,first.analysis_ref);assert.deepEqual(sciPosts()[1].body.dataset_ref,first.dataset_ref);
""")


def test_disabled_scientific_service_preserves_saved_references_on_legacy_navigation():
    run_script(SETUP + r"""
server.config.scientific.enabled=false;
await connectScience();assert.equal(elements['scientific-entry'].hidden,true);assert.equal(elements.analyses.hidden,false);
await elements['nav-overview'].onclick();
assert.deepEqual(server.workspace.ui.dataset_ref,datasetRef);assert.deepEqual(server.workspace.ui.analysis_ref,analysisRef);
assert.equal(sciPosts().length,0);
""")


def test_historical_result_does_not_inherit_current_version_acceptance():
    run_script(SETUP + r"""
server.scientific.results=[{result_id:result,analysis_id:analysis,study_id:study,ref:ref('result',result,'e'),review_status:'accepted'}];
server.scientific.result_versions=[{result_id:result,version_id:resultRef.version_id,ref:resultRef,run_id:run,attempt_id:'attempt',validation_id:'validation',artifact_ids:[],interpretation:'Older interpretation'}];
server.scientific.validations=[{validation_id:'validation',status:'failed',observations:{passed:false}}];
server.workspace.ui.result_ref=resultRef;
await connectScience();elements['scientific-warning-ack'].checked=true;elements['scientific-warning-ack'].onchange();
assert.match(elements['scientific-result-summary'].textContent,/unreviewed historical version/);
assert.match(elements['scientific-result-summary'].textContent,/validation: failed/);
assert.equal(elements['scientific-accept'].disabled,true);assert.equal(elements['scientific-reject'].disabled,true);
assert.equal(sciPosts().length,0);
""")


def test_initialized_store_with_old_view_schema_offers_explicit_completion():
    run_script(SETUP + r"""
server.workspace.schema_version=2;server.workspace.ui={view:'analyses',tab:'analyst'};
server.onAction=async body=>{assert.equal(body.action,'initialize');server.workspace.schema_version=3;server.workspace.revision++;return {initialized:true};};
await connectScience();await elements['open-scientific'].onclick();
assert.equal(elements['scientific-setup'].hidden,false);
assert.match(elements['scientific-initialize'].textContent,/Enable exact version restoration/);
assert.equal(elements['scientific-create-study'].disabled,true);assert.equal(sciPosts().length,0);
await elements['scientific-initialize'].onclick();
assert.equal(elements['scientific-setup'].hidden,true);assert.equal(elements['scientific-create-study'].disabled,false);
assert.equal(server.workspace.schema_version,3);
""")


def test_analysis_uses_an_earlier_invocation_only_after_explicit_selection():
    run_script(SETUP + r"""
const earlier={run_id:'7'.repeat(32),analysis_id:'8'.repeat(32),method_version_id:'method-v1',method_name:'Earlier QC',status:'succeeded'};
server.scientific.legacy_method_runs=[earlier];
server.onAction=async body=>{assert.equal(body.action,'create_analysis');return {ref:analysisRef};};
await connectScience();elements['scientific-analysis-name'].value='New QC question';elements['scientific-objective'].value='Inspect the selected dataset';
assert.equal(elements['scientific-earlier-invocation'].value,'');
await elements['scientific-analysis-form'].onsubmit({preventDefault(){}});
assert.equal(sciPosts()[0].body.legacy_analysis_id,undefined);assert.equal(sciPosts()[0].body.execution_source_run_id,undefined);
elements['scientific-earlier-invocation'].value=earlier.run_id;elements['scientific-earlier-invocation'].onchange();
assert.equal(sciPosts().length,1);
await elements['scientific-analysis-form'].onsubmit({preventDefault(){}});
assert.equal(sciPosts()[1].body.legacy_analysis_id,earlier.analysis_id);
assert.equal(sciPosts()[1].body.execution_source_run_id,earlier.run_id);
assert.deepEqual(sciPosts().map(call=>call.body.action),['create_analysis','create_analysis']);
elements['scientific-use-earlier-import'].onclick();
assert.equal(elements['scientific-import-id'].value,earlier.run_id);assert.equal(elements['scientific-import-kind'].value,'method_run');
assert.equal(elements['scientific-import-mappings'].checked,false);assert.equal(sciPosts().length,2);
elements['scientific-earlier-invocation'].value='';elements['scientific-earlier-invocation'].onchange();
await elements['scientific-analysis-form'].onsubmit({preventDefault(){}});
assert.equal(sciPosts()[2].body.legacy_analysis_id,undefined);assert.equal(sciPosts()[2].body.execution_source_run_id,undefined);
""")


def test_five_scientific_questions_are_visible_in_order_with_controls_in_context():
    page = (Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-ui.html").read_text()
    questions = [
        "What data do I have?",
        "What question am I investigating?",
        "Which methods are appropriate, and why?",
        "What happened?",
        "What can I conclude—and what remains uncertain?",
    ]
    positions = [page.index("<h3>" + question + "</h3>") for question in questions]
    assert positions == sorted(positions)
    assert positions[2] < page.index('id="scientific-run"') < positions[3]
    assert positions[3] < page.index('id="scientific-result"') < positions[4]
    assert positions[4] < page.index('id="scientific-recommendation"') < page.index('id="scientific-review-form"')


def test_missing_dataset_findings_remain_explicit_without_inferred_replication():
    run_script(SETUP + r"""
await connectScience();
assert.match(elements['scientific-data-context'].textContent,/Biological units.*Not provided/);
assert.match(elements['scientific-data-context'].textContent,/Groups.*Not provided/);
assert.match(elements['scientific-data-context'].textContent,/Measurements.*Not provided/);
assert.match(elements['scientific-data-context'].textContent,/QC concerns.*Not provided/);
assert.match(elements['scientific-data-observed'].textContent,/No observations supplied/);
assert.match(elements['scientific-data-inferred'].textContent,/Not provided/);
assert.match(elements['scientific-data-unknowns'].textContent,/completeness.*not established/);
assert.match(elements['scientific-data-context'].textContent,/Sample labels and row counts do not establish independent biological replication/);
assert.match(elements['scientific-data-context'].textContent,/Content hashes identify bytes/);
assert.equal(sciPosts().length,0);
""")


def test_provided_dataset_metadata_keeps_observation_inference_and_unknowns_separate():
    run_script(SETUP + r"""
server.scientific.dataset_versions[0].manifest.metadata={data_classification:'public',observed:{sample_labels:['S1','S2'],row_count:2000,groups:['control','treated'],measurement:'normalized expression',qc_concerns:['Possible low library sizes']},inferred:{batch_confounding:'Not established'},unknowns:[{field:'Donor identity',impact:'Replication remains unresolved',blocking:true}]};
await connectScience();
assert.match(elements['scientific-data-context'].textContent,/S1/);assert.match(elements['scientific-data-context'].textContent,/Biological units.*Not provided/);
assert.match(elements['scientific-data-context'].textContent,/normalized expression/);assert.match(elements['scientific-data-context'].textContent,/Possible low library sizes/);
assert.match(elements['scientific-data-observed'].textContent,/2000/);
assert.match(elements['scientific-data-inferred'].textContent,/Not established/);
assert.match(elements['scientific-data-unknowns'].textContent,/Replication remains unresolved/);
assert.equal(sciPosts().length,0);
""")


def test_new_plan_requires_explicit_confirmation_for_user_parameter_requirements():
    run_script(SETUP + r"""
server.onAction=async body=>({session_id:session,evaluation_purpose:body.evaluation_purpose,user_parameter_requirements:body.user_parameter_requirements});
await connectScience();await elements['scientific-create-plan'].onclick();
assert.equal(sciPosts()[0].body.evaluation_purpose,'method_selection');assert.deepEqual(sciPosts()[0].body.user_parameter_requirements,[]);
const requirements=[{name:'threshold',value:4,evidence:'Explicit user instruction: use threshold 4 for verification.'}];
elements['scientific-evaluation-purpose'].value='execution_verification';elements['scientific-parameter-requirements'].value=JSON.stringify(requirements);
await elements['scientific-create-plan'].onclick();assert.equal(sciPosts().length,1);
assert.match(elements['scientific-message'].textContent,/confirm/i);
elements['scientific-parameter-confirmation'].checked=true;
await elements['scientific-create-plan'].onclick();assert.equal(sciPosts().length,2);
assert.equal(sciPosts()[1].body.evaluation_purpose,'execution_verification');assert.deepEqual(sciPosts()[1].body.user_parameter_requirements,requirements);
assert.equal(elements['scientific-parameter-confirmation'].checked,false);
assert.ok(!sciPosts()[1].body.allow_model_execution);assert.ok(!sciPosts()[1].body.allow_data_execution);
""")


def test_candidate_explanation_and_user_attribution_include_retained_evidence():
    run_script(SETUP + r"""
const first={name:'Synthetic QC',method_family:'Descriptive quality control',source:{type:'primary_paper',reference:'doi:synthetic-source'},assumptions:[{statement:'Independent specimens are required',status:'unknown',evidence:null,hard_precondition:true}],dataset_fit:'plausible',objective_fit:'strong',expected_inputs:['raw count matrix'],known_failure_modes:['Confounding can obscure group effects'],parameter_candidates:[{name:'threshold',value:4,origin:'user',rationale:'Use the confirmed threshold'},{name:'seed',value:17,origin:'user',rationale:'Historical user attribution'}]};
server.scientific.plans[0].scientific_candidates=[first,{...first,name:'Unavailable alternative',parameter_candidates:[]}];
server.scientific.plans[0].candidates=[{...first,method_version_id:'method-v1',availability:'registered',applicability:'blocked_unknown',blockers:['Independent specimen identity is missing']}];
server.scientific.plans[0].user_parameter_requirements=[{name:'threshold',value:4,evidence:'Caller explicitly required threshold 4.'}];
await connectScience();const body=elements['scientific-candidates'].children.map(row=>row.textContent).join('\n');
for(const text of ['Descriptive quality control','doi:synthetic-source','Independent specimens are required','plausible','strong','raw count matrix','Confounding can obscure group effects','Independent specimen identity is missing','Unavailable alternative','Caller explicitly required threshold 4.'])assert.ok(body.includes(text),text);
assert.match(body,/caller-declared/i);assert.match(body,/Unverified user attribution/);
assert.ok(body.indexOf('Dataset fit')<body.indexOf('Availability'));
assert.equal(sciPosts().length,0);
""")


def test_abstention_stays_visible_with_uncertainty_and_legacy_qualification_after_refresh():
    run_script(SETUP + r"""
server.scientific.plans[0].recommendation={choice:null,confidence:'no_recommendation',scientific_confidence:'no_recommendation',implementation_confidence:'unknown',rationale:'No comparable scientific evidence exists',confidence_basis:'Only one software check is retained',tradeoffs:['The preferred alternative is unavailable'],alternatives:['Collect independent diagnostics'],params:{},approval_actions:[]};
await connectScience();assert.equal(elements['scientific-recommend'].disabled,false);
await elements['scientific-refresh'].onclick();const text=elements['scientific-recommendation'].textContent;
for(const expected of ['No recommendation','No comparable scientific evidence exists','Only one software check is retained','The preferred alternative is unavailable','Collect independent diagnostics','no_recommendation'])assert.ok(text.includes(expected),expected);
assert.match(elements['scientific-plan-purpose-summary'].textContent,/unverified/i);
assert.match(elements['scientific-evidence-assessment'].textContent,/unqualified/i);
assert.equal(sciPosts().length,0);
""")


def test_observed_metrics_label_method_claims_and_controller_diagnostics_without_winner():
    run_script(SETUP + r"""
server.workspace.ui.scientific_run_id=run;
server.scientific.runs=[{run_id:run,study_id:study,status:'finished',analysis_version_id:analysisRef.version_id,trigger:{request:{session_id:session}}}];
server.scientific.attempts=[{run_id:run,controller_id:'controller-1',status:'succeeded'}];
server.scientific.plans[0].experiments=[{id:'qc-declared',status:'completed',metrics:[{name:'runtime',category:'runtime',direction:'lower_better',interpretation:'Elapsed execution time'},{name:'signal',category:'signal_preservation',direction:'higher_better',interpretation:'Method-reported score'}],metric_sources:{runtime:{kind:'runtime',field:'elapsed_seconds'},signal:{kind:'output_json',output:'metrics',field:'/signal'}}}];
server.scientific.plans[0].results=[{experiment_id:'qc-declared',candidate:'Synthetic QC',status:'partial',metric_values:{runtime:0.25,signal:null},warnings:['Signal missing'],failures:[],provenance:{run_id:'controller-1'}}];
server.scientific.plans[0].recommendation_evidence_assessment={evaluation_purpose:'method_selection',scope:'comparative_pilot_only',independent_scientific_validation:false,eligible_implemented_candidates:['Synthetic QC'],unavailable_candidates:['Alternative'],candidates:[{candidate:'Synthetic QC',meets_evidence_floor:false,comparable_experiments:[],reasons:['No comparative scientific diagnostics'],limitations:['Runtime cannot establish scientific fit']}],limitations:['Independent scientific review remains absent']};
await connectScience();const text=elements['scientific-observed-metrics'].textContent;
assert.match(text,/0.25/);assert.match(text,/Controller-measured/);assert.match(text,/Method-reported/);assert.match(text,/unavailable/i);assert.match(text,/partial/);
assert.match(elements['scientific-evidence-assessment'].textContent,/No comparative scientific diagnostics/);
assert.match(elements['scientific-evidence-assessment'].textContent,/Independent scientific review remains absent/);
assert.match(elements['scientific-evidence-assessment'].textContent,/not.*proof.*best/i);
assert.equal(sciPosts().length,0);
""")


def test_no_verified_plot_is_explicit_and_does_not_scan_artifacts_on_refresh():
    run_script(SETUP + r"""
server.scientific.results=[{result_id:result,analysis_id:analysis,study_id:study,ref:resultRef,review_status:'candidate'}];
server.scientific.result_versions=[{result_id:result,version_id:resultRef.version_id,ref:resultRef,run_id:run,attempt_id:'attempt',validation_id:'validation',artifact_ids:['d'.repeat(64)],interpretation:'Retained output'}];
server.workspace.ui.result_ref=resultRef;
await connectScience();await elements['scientific-refresh'].onclick();
assert.match(elements['scientific-plot-message'].textContent,/No verified plot available in this view/);
assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.ok(!calls.some(call=>call.path.startsWith('/api/scientific/artifacts/')));
assert.equal(sciPosts().length,0);
""")


RASTER_RESULT = r"""
context.Blob=Blob;
server.scientific.results=[{result_id:result,analysis_id:analysis,study_id:study,ref:resultRef,review_status:'candidate'}];
server.scientific.result_versions=[{result_id:result,version_id:resultRef.version_id,ref:resultRef,run_id:run,attempt_id:'attempt',validation_id:'validation',artifact_ids:['d'.repeat(64)],interpretation:'Retained output'}];
server.workspace.ui.result_ref=resultRef;
let artifactReads=0,cancelled=0;
const beforeArtifactFetch=context.fetch;
context.fetch=async(path,options)=>{
 if(!path.startsWith('/api/scientific/artifacts/'))return beforeArtifactFetch(path,options);
 artifactReads++;assert.equal(options.headers['X-Workbench-Token'],'local-token');
 let consumed=false;
 return {ok:true,headers:{get:()=>server.previewType},body:{cancel:async()=>cancelled++,getReader:()=>({read:async()=>consumed?{done:true}:(consumed=true,{done:false,value:server.previewBytes}),cancel:async()=>cancelled++})}};
};
"""


def test_linked_raster_preview_is_explicit_authenticated_and_cleared_on_version_change():
    run_script(SETUP + RASTER_RESULT + r"""
server.previewType='image/png';server.previewBytes=new Uint8Array([137,80,78,71]);
await connectScience();assert.equal(artifactReads,0);
await elements['scientific-artifacts'].children[0].onclick();
assert.equal(artifactReads,1);const figure=elements['scientific-plot-preview'].children[0],img=figure.children[0];
assert.equal(img.tagName,'img');assert.equal(img.src,'blob:fixture');
assert.match(figure.children[1].textContent,/no scientific interpretation added/);
img.onload();assert.match(elements['scientific-plot-message'].textContent,/Verified linked raster output/);
await elements['scientific-refresh'].onclick();assert.equal(artifactReads,1);
elements['scientific-result'].value='';await elements['scientific-result'].onchange();
assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.match(elements['scientific-plot-message'].textContent,/No verified plot available/);
assert.equal(sciPosts().length,0);
""")


def test_active_image_format_is_not_rendered_as_a_plot():
    run_script(SETUP + RASTER_RESULT + r"""
server.previewType='image/svg+xml';server.previewBytes=new Uint8Array([60,115,118,103]);
await connectScience();await elements['scientific-artifacts'].children[0].onclick();
assert.equal(cancelled,1);assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.match(elements['scientific-artifact-message'].textContent,/active image\/document formats are not displayed/);
assert.equal(sciPosts().length,0);
""")


def test_raster_preview_rejects_bytes_beyond_its_bound():
    run_script(SETUP + RASTER_RESULT + r"""
server.previewType='image/png';server.previewBytes=new Uint8Array(8*1024*1024+1);
await connectScience();await elements['scientific-artifacts'].children[0].onclick();
assert.equal(cancelled,1);assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.match(elements['scientific-artifact-message'].textContent,/exceeds the bounded preview limit/);
assert.equal(sciPosts().length,0);
""")


def test_loaded_plot_is_removed_when_the_exact_result_becomes_unavailable():
    run_script(SETUP + RASTER_RESULT + r"""
server.previewType='image/png';server.previewBytes=new Uint8Array([137,80,78,71]);
await connectScience();await elements['scientific-artifacts'].children[0].onclick();
assert.equal(elements['scientific-plot-preview'].children.length,1);
server.scientific.result_versions=[];await elements['scientific-refresh'].onclick();
assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.match(elements['scientific-reference-message'].textContent,/unavailable/i);
assert.ok(elements['scientific-result'].value.includes(resultRef.version_id));
assert.equal(artifactReads,1);assert.equal(sciPosts().length,0);
""")


@pytest.mark.parametrize("content_type", ["image/png", "text/plain"])
def test_pending_preview_is_discarded_when_the_exact_result_becomes_unavailable(content_type):
    run_script(SETUP + RASTER_RESULT + f"server.previewType={content_type!r};" + r"""
server.previewBytes=new Uint8Array([137,80,78,71]);
let release,started;
const pendingResponse=new Promise(resolve=>started=resolve),fetchBeforeHold=context.fetch;
context.fetch=async(path,options)=>{
 const response=await fetchBeforeHold(path,options);
 if(path.startsWith('/api/scientific/artifacts/'))await new Promise(resolve=>{release=resolve;started();});
 return response;
};
await connectScience();const preview=elements['scientific-artifacts'].children[0].onclick();await pendingResponse;
server.scientific.result_versions=[];await elements['scientific-refresh'].onclick();
release();await preview;
assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.equal(elements['scientific-output-preview'].hidden,true);
assert.equal(elements['scientific-output-preview'].textContent,'');
assert.match(elements['scientific-plot-message'].textContent,/No verified plot available/);
assert.match(elements['scientific-reference-message'].textContent,/unavailable/i);
assert.ok(elements['scientific-result'].value.includes(resultRef.version_id));
assert.equal(elements['scientific-artifact-message'].textContent,'');
assert.equal(artifactReads,1);assert.equal(sciPosts().length,0);
""")


@pytest.mark.parametrize("event", ["onload", "onerror"])
def test_pending_raster_callback_cannot_change_unavailable_result_or_new_preview(event):
    run_script(SETUP + RASTER_RESULT + f"const callback={event!r};" + r"""
server.previewType='image/png';server.previewBytes=new Uint8Array([137,80,78,71]);
await connectScience();await elements['scientific-artifacts'].children[0].onclick();
const firstImage=elements['scientific-plot-preview'].children[0].children[0],retainedVersions=server.scientific.result_versions;
server.scientific.result_versions=[];await elements['scientific-refresh'].onclick();
firstImage[callback]();
assert.equal(elements['scientific-plot-preview'].children.length,0);
assert.match(elements['scientific-plot-message'].textContent,/No verified plot available/);
assert.equal(elements['scientific-artifact-message'].textContent,'');
assert.match(elements['scientific-reference-message'].textContent,/unavailable/i);
server.scientific.result_versions=retainedVersions;await elements['scientific-refresh'].onclick();
await elements['scientific-artifacts'].children[0].onclick();
const secondFigure=elements['scientific-plot-preview'].children[0],secondImage=secondFigure.children[0];
secondImage.onload();firstImage[callback]();
assert.equal(elements['scientific-plot-preview'].children[0],secondFigure);
assert.match(elements['scientific-plot-message'].textContent,/Verified linked raster output/);
assert.match(elements['scientific-artifact-message'].textContent,/Verified raster bytes loaded/);
assert.equal(artifactReads,2);assert.equal(sciPosts().length,0);
""")


def test_text_conversion_cannot_finish_into_an_unavailable_result():
    run_script(SETUP + RASTER_RESULT + r"""
server.previewType='text/plain';server.previewBytes=new Uint8Array([81,67]);
let release,started;
const converting=new Promise(resolve=>started=resolve);
context.Blob=class extends Blob{async text(){await new Promise(resolve=>{release=resolve;started();});return super.text();}};
await connectScience();const preview=elements['scientific-artifacts'].children[0].onclick();await converting;
server.scientific.result_versions=[];await elements['scientific-refresh'].onclick();
release();await preview;
assert.equal(elements['scientific-output-preview'].hidden,true);
assert.equal(elements['scientific-output-preview'].textContent,'');
assert.match(elements['scientific-reference-message'].textContent,/unavailable/i);
assert.equal(elements['scientific-artifact-message'].textContent,'');
assert.equal(artifactReads,1);assert.equal(sciPosts().length,0);
""")


def test_completed_empty_capability_resolution_allows_abstention_without_invented_candidates():
    run_script(SETUP + r"""
server.scientific.plans[0].scientific_candidates=[];server.scientific.plans[0].candidates=[];
server.scientific.plans[0].selection_history=[{phase:'method_capability_resolution',status:'accepted'}];
await connectScience();assert.equal(elements['scientific-recommend'].disabled,false);
assert.match(elements['scientific-candidates'].children[0].textContent,/No scientifically appropriate candidates/);
assert.match(elements['scientific-recommendation'].textContent,/No recommendation recorded/);
assert.equal(sciPosts().length,0);
""")


def test_fixture_and_assistant_parameter_origins_are_never_recast_as_user_requirements():
    run_script(SETUP + r"""
server.scientific.plans[0].scientific_candidates=[{name:'Synthetic QC',parameter_candidates:[{name:'seed',value:17,origin:'fixture',rationale:'Recorded fixture choice'},{name:'threshold',value:4,origin:'assistant',rationale:'Assistant suggestion'},{name:'limit',value:4,origin:'user',rationale:'Historical assertion'}]}];
server.scientific.plans[0].user_parameter_requirements=[{name:'seed',value:17,evidence:'Caller mentioned this separately'},{name:'limit',value:'4',evidence:'A string value was explicitly supplied'}];
await connectScience();const text=elements['scientific-candidates'].children[0].textContent;
assert.match(text,/Origin: fixture/);assert.match(text,/Origin: assistant/);assert.match(text,/Unverified user attribution/);
assert.ok(!text.includes('recorded user requirement (caller-declared)'));
assert.equal(sciPosts().length,0);
""")
