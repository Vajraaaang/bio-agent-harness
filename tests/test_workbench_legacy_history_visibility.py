"""Retained jobs remain discoverable without a disclosure or top-three cutoff."""
from pathlib import Path
import re

from tests.test_workbench_ui_agent_asset import run_script


def test_history_is_an_open_section_and_empty_history_stays_out_of_the_way():
    page = (Path(__file__).resolve().parents[1] / "devbench/analysis/assets/workbench-ui.html").read_text()
    assert re.search(r'<section\b[^>]*id="legacy-history"[^>]*aria-labelledby="legacy-history-title"', page)
    assert not re.search(r'<details\b[^>]*id="legacy-history"', page)
    run_script(r"""
await elements.connect.onclick();
assert.equal(elements['legacy-history'].hidden,true);
assert.equal(elements.jobs.children.length,0);
assert.equal(posts().length,0);
""")


def test_all_retained_jobs_and_failure_states_are_visible_and_oldest_job_still_opens():
    run_script(r"""
server.jobs=[
 {id:'newest-job',question:'Newest question',status:'completed_review_passed',updated_at:'2026-10-28T15:00:00Z'},
 {id:'presentation-job',question:'Presentation attempt',operation_kind:'presentation',parent_job:'fixture-job',status:'cancelled',updated_at:'2026-10-28T14:00:00Z'},
 {id:'failed-job',question:'Failed attempt',status:'operation_failed',updated_at:'2026-10-28T13:00:00Z'},
 {id:'interrupted-job',question:'Interrupted attempt',status:'interrupted',updated_at:'2026-10-28T12:00:00Z'},
 {id:'fixture-job',question:'Oldest retained question',status:'operation_failed',updated_at:'2026-10-28T11:00:00Z'}
];
server.state.question='Oldest retained question';server.state.status='operation_failed';
await elements.connect.onclick();
assert.equal(elements['legacy-history'].hidden,false);
assert.equal(elements['legacy-history-title'].textContent,'Analysis history · 5');
assert.equal(elements.jobs.children.length,5);
assert.equal(elements.jobs.children[1].children[0].textContent,'Presentation');
assert.equal(elements.jobs.children[2].children[2].textContent,'operation failed');
assert.equal(elements.jobs.children[3].children[2].textContent,'interrupted');
assert.equal(elements.jobs.children[4].children[1].textContent,'Oldest retained question');
await elements.jobs.children[4].onclick();
assert.ok(calls.some(call=>call.path==='/api/jobs/fixture-job'));
assert.equal(JSON.parse(elements['execution-record'].textContent).id,'fixture-job');
assert.equal(posts().length,0);
""")
