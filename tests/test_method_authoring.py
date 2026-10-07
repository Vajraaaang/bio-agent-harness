"""Proposed trials and generated source are artifacts, never implicit execution."""
import json

import pytest

from devharness_core.provenance import hash_artifact
from tests.test_method_planner import resolved, Selector, trial_spec
from tests.test_extensible_methods import FixtureExecutor


def implementation():
    return {"source_code": "from pathlib import Path\nPath('/outputs/result.json').write_text('{}')\n",
            "algorithm_summary": "Synthetic output fixture only", "introduced_choices": ["No biological claims"],
            "source_references": []}


def test_model_can_propose_trials_without_executing_or_declaring_them(tmp_path):
    planner, state, _, analysis, branch, step = resolved(tmp_path)
    selector = Selector({"experiments": [trial_spec(step)]})
    proposals = planner.design_trials(state["session_id"], selector, actor="scientist")
    assert proposals[0]["id"] == "qc-pilot"
    assert selector.contexts[0]["decision_kind"] == "method_trial_design"
    assert planner.export(state["session_id"])["experiments"] == []
    assert planner.export(state["session_id"])["results"] == []
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]
    planner.declare_trial(state["session_id"], branch["branch_id"], proposals[0], actor="scientist")


def test_generated_source_is_hashed_and_registered_as_unreviewed_without_running(tmp_path):
    planner, state, _, analysis, _, step = resolved(tmp_path)
    interface = planner.methods.method(step["method_version_id"])["contract"]
    lock = tmp_path / "environment.lock"
    result = planner.draft_implementation(state["session_id"], "Unknown QC", Selector(implementation()),
        interface, lock, actor="scientist", justification="Test a clearly specified alternative implementation")
    registered = planner.methods.method(result["method_version_id"])
    assert registered["contract"]["implementation_trust"] == "unreviewed"
    assert registered["contract"]["lifecycle"] == "custom"
    source = planner.methods.artifact_path(registered["source_artifact"])
    assert source.read_text() == implementation()["source_code"]
    assert registered["contract"]["implementation"]["source_sha256"] == hash_artifact(source)["sha256"]
    assert result["status"] == "generated_unexecuted"
    assert planner.export(state["session_id"])["results"] == []
    assert planner.methods.analysis(analysis["analysis_id"])["revision_id"] == analysis["revision_id"]


@pytest.mark.parametrize("case", ["no_justification", "unseen_reference", "invalid_python", "changed_lock", "host_execution"])
def test_invalid_generated_method_is_rejected(tmp_path, case):
    planner, state, _, _, _, step = resolved(tmp_path)
    interface = planner.methods.method(step["method_version_id"])["contract"]
    lock = tmp_path / "environment.lock"
    response, reason = implementation(), "Explicit fixture adaptation"
    if case == "no_justification":
        reason = ""
    elif case == "unseen_reference":
        response["source_references"] = ["https://example.invalid/not-retrieved"]
    elif case == "invalid_python":
        response["source_code"] = "def invalid(:\n"
    elif case == "changed_lock":
        lock.write_text("Different environment")
    else:
        interface["implementation"]["kind"] = "dispatcher"
    with pytest.raises((ValueError, SyntaxError)):
        planner.draft_implementation(state["session_id"], "Unknown QC", Selector(response), interface, lock,
                                     actor="scientist", justification=reason)


def test_implementation_cannot_change_after_trial_declaration(tmp_path):
    planner, state, _, _, branch, step = resolved(tmp_path)
    planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    interface = planner.methods.method(step["method_version_id"])["contract"]
    with pytest.raises(ValueError, match="frozen|trial"):
        planner.draft_implementation(state["session_id"], "Unknown QC", Selector(implementation()), interface,
            tmp_path / "environment.lock", actor="scientist", justification="Too late to rewrite this trial")


def test_generated_source_is_never_executed_on_host(tmp_path):
    planner, state, _, _, _, step = resolved(tmp_path)
    marker = tmp_path / "must-not-exist"
    response = implementation()
    response["source_code"] = "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('unsafe')\n"
    interface = planner.methods.method(step["method_version_id"])["contract"]
    result = planner.draft_implementation(state["session_id"], "Unknown QC", Selector(response), interface,
        tmp_path / "environment.lock", actor="scientist", justification="Verify source remains inert text")
    assert result["status"] == "generated_unexecuted"
    assert not marker.exists()


def test_trial_criteria_cannot_be_redesigned_after_observing_results(tmp_path):
    planner, state, _, _, branch, step = resolved(tmp_path)
    spec = trial_spec(step)
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
    planner.run_trial(state["session_id"], spec["id"], actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    spec["id"] = "post-hoc-criteria"
    selector = Selector({"experiments": [spec]})
    with pytest.raises(ValueError, match="evidence|results|frozen"):
        planner.design_trials(state["session_id"], selector, actor="scientist")
    assert selector.contexts == []
    with pytest.raises(ValueError, match="evidence|results|frozen"):
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="scientist")
