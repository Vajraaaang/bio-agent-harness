"""Question intake renders public diagnostics and supports source-free drafts."""

import json

import pytest

from tests.test_workbench_conversation_redesign import run_js
from tests.test_workbench_descriptive_ui import DESCRIPTION
from tests.test_workbench_investigation_asset import run_js as run_controller
from tests.test_workbench_workflow_accordion import SETUP


def test_no_dataset_question_is_sent_even_without_a_model_selector():
    run_controller(r"""
const f = fixture({config:{model_access:false}});
await f.controller.ready;
assert.match(f.statuses.at(-1).message,/No model selector.*local.*matrix diagnostics/i);
assert.match(f.statuses.at(-1).message,/Model-planned analyses require/);
const question = 'What data would a donor-level contrast need?';
f.options.questionInput.value = question;
await f.controller.submit();
const create = f.calls.find(call => call.path === '/api/investigations' && call.body);
assert.deepEqual(create.body,{question,proposal_mode:'agent'});
""")


def test_analyst_profile_requires_two_csv_files():
    run_controller(r"""
const csv = '/approved/expression.csv', tsv = '/approved/metadata.tsv';
const config = {model_access:true,proposal_selection:{mode:'codex',available:true},
  investigations:{analyst_profile:'analyst_v1'}};
for (const paths of [[tsv],[csv,tsv]]) {
  const f = fixture({config});
  await f.controller.ready;
  f.server.request = async (path,body) => path === '/api/inputs'
    ? {inputs:paths.map(source => ({source}))}
    : body ? {...f.server.record,source:paths[0],sources:paths} : {investigations:[]};
  await f.controller.reloadInputs();
  f.controller.setSources(paths);
  f.options.questionInput.value = 'Compare condition expression';
  await f.controller.submit();
  const create=f.calls.find(call => call.path === '/api/investigations' && call.body);
  assert.equal(create.body.analyst_profile,undefined);
}
""")


def test_public_table_title_does_not_require_an_unredacted_caption():
    run_js(SETUP + DESCRIPTION + r"""
delete answer.tables[0].caption;
answer.tables[0].table_title = 'Stored cell type labels';
render(investigation);
const caption = walk(container).find(node => node.tagName === 'caption');
assert.equal(caption.textContent,'Stored cell type labels');
""")


def test_grouped_stage_conclusion_link_selects_and_focuses_last_tab():
    run_js(SETUP + r"""
const stageBar = new Node('nav');
const stageButtons = Array.from({length:3},(_,index) =>
  Object.assign(new Node('button'),{
    id:'wc-stage-pill-'+(index+1),closest:() => stageBar,
    focus(){this.focused = true;},click(){this.onclick();},
  })
);
const investigation = {
  id:'chat',revision:1,status:'awaiting_information',
  question:'Compare groups',turns:[{
    id:'result',kind:'singlecell_evidence',step:4,
    content:{tool_name:'scrna_population_contrast',status:'completed'},
  }],
  synthesis_continuation:{status:'review_required',eligible:false,
    population_evidence:{direct_answer:'Retained answer',trials:[]}},
};
const historyContainer = new Node('div');
api.renderInvestigation(container,{investigation,stageButtons,historyContainer});
const button = walk(container)
  .find(node => node.textContent === 'Read the conclusion →');
button.click();
assert.equal(stageButtons.at(-1).focused,true);
assert.equal(stageButtons.at(-1).attrs['aria-selected'],'true');
""")


def test_disclosure_withheld_synthesis_does_not_render_empty_card():
    run_js(SETUP + r"""
render({id:'chat',status:'completed',synthesis:{publication_status:'withheld'}});
assert.ok(!walk(container).some(node => node.textContent === 'Scientific synthesis'));
""")


@pytest.mark.parametrize(
    ("code", "explanation"),
    [
        ("sqlite_wal_upgrade_required", "DYLD_LIBRARY_PATH"),
        ("model_selector_not_configured", "requires a configured selector"),
        ("scientific_workspace_not_enabled", "--enable-scientific"),
        ("data_execution_disabled", "--allow-data-execution"),
        (
            "scientific_workflow_not_configured",
            "needs a configured scientific workflow",
        ),
        ("adaptive_runtime_not_bound", "has no bound adaptive runtime"),
    ],
)
def test_guidance_and_closed_diagnostic_are_readable(code, explanation):
    run_js(
        SETUP + f"\nconst code = {json.dumps(code)};\n"
        f"const explanation = {json.dumps(explanation)};\n" + r"""
render({id:'chat',status:'awaiting_information',diagnostic_code:code,
  clarification_fields:[{field:'condition',type:'text',
    guidance:'Use the study design records.',reason:'operation_unavailable'}],
});
const copy = walk(container).map(node => node.textContent).join(' ');
assert.ok(copy.includes('Use the study design records.'));
assert.ok(copy.includes(explanation));
assert.ok(!copy.includes('operation_unavailable'));
"""
    )
