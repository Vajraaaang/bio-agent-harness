"""Scientific anchors stay separate from the public six-field planner contract."""
import uuid

from tests.test_extensible_methods import FixtureExecutor
from tests.test_method_planner import resolved, trial_spec


def test_planner_exact_anchors_are_retained_outside_export(tmp_path):
    planner, state, _, _, _, _ = resolved(tmp_path)
    anchors = {"project_id": "a" * 32, "study_id": "b" * 32,
               "dataset_versions": [{"object_type": "dataset", "object_id": "c" * 32, "version_id": "d" * 64}],
               "analysis_version": {"object_type": "analysis", "object_id": "e" * 32, "version_id": "f" * 64}}
    linked = planner.create(state["context"]["task"], state["context"]["dataset_summary"],
                            actor="researcher", data_classification="synthetic", anchors=anchors)
    anchors["project_id"] = "0" * 32
    assert planner.get(linked["session_id"])["scientific_anchors"]["project_id"] == "a" * 32
    assert set(planner.export(linked["session_id"])) == {
        "task", "dataset_summary", "candidates", "experiments", "results", "recommendation"}


def test_trial_dispatch_carries_reserved_scientific_controller_links(tmp_path):
    planner, state, _, _, branch, step = resolved(tmp_path)
    experiment = planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="researcher")
    controller_id = uuid.uuid4().hex
    correlation = {"project_id": "a" * 32, "scientific_run_id": "b" * 32,
                   "attempt_id": "c" * 32, "analysis_version_id": "d" * 64, "dataset_version_ids": ["e" * 64]}
    bindings = {"Unknown QC": {"controller_id": controller_id, "correlation": correlation}}
    rows = planner.run_trial(state["session_id"], experiment["id"], actor="researcher", allow_data_execution=True,
                             executor=FixtureExecutor(), execution_bindings=bindings)
    assert rows[0]["provenance"]["run_id"] == controller_id
    assert planner.methods.get("run", controller_id)["correlation"] == correlation
    stored = planner.get(state["session_id"])
    assert stored["execution_correlations"][experiment["id"]] == bindings
    assert "execution_correlations" not in planner.export(state["session_id"])
