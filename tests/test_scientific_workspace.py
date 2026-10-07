"""Browser service workflow over actual scientific and method SQLite records."""
from copy import deepcopy
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from devbench.analysis.workbench_ui import WorkbenchService
from tests.test_extensible_methods import prepared, FixtureExecutor
from tests.test_method_planner import Selector, candidate, trial_spec


@pytest.fixture
def tmp_path():
    # Exercise real storage admission; platform scratch is deliberately denied.
    root = Path(__file__).resolve().parents[1] / "reports" / "scientific-workspace-fixtures" / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def setup_workspace(tmp_path):
    source = tmp_path / "author"
    source.mkdir()
    methods, legacy, _, step, *_ = prepared(source)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    selector = Selector({"candidates": [candidate()]}, {"bindings": [
        {"candidate": "Unknown QC", "method_version_id": step["method_version_id"], "discovery": None}]})
    app = WorkbenchService(tmp_path / "browser", [inputs], enable_scientific=True,
        scientific_workspace=methods.root, method_selector=selector,
        method_executor=FixtureExecutor(), allow_data_execution=True)
    return app, methods, legacy, step, inputs


def action(app, action_name, **fields):
    return app.scientific.action({"action": action_name, "expected_revision": app.scientific.catalog()["revision"], **fields})


def settled(app, response):
    app.scientific.futures[response["operation_id"]].result(timeout=30)
    record = next(row for row in app.scientific.catalog()["operations"] if row["operation_id"] == response["operation_id"])
    assert record["status"] == "completed", record
    return record["value"]


def scientific_context(app, step):
    action(app, "initialize")
    study = action(app, "create_study", name="QC study", aim="Preserve sample-aware inputs")
    dataset = action(app, "register_dataset", study_id=study["study_id"], name="Synthetic input",
        members={"dataset": {"artifact_id": step["inputs"]["dataset"]}},
        metadata={"observed": {"independent_samples": True}, "inferred": {}, "unknowns": [], "data_classification": "synthetic"})
    analysis = action(app, "create_analysis", study_id=study["study_id"], name="QC alternatives",
        objective="Compare sensible synthetic QC choices", computational_task="quality_control")
    return study, dataset, analysis


def scientific_plan(app, step):
    study, dataset, analysis = scientific_context(app, step)
    plan = action(app, "create_plan", analysis_ref=analysis["ref"], dataset_ref=dataset["ref"])
    for name in ("science", "resolve"):
        settled(app, action(app, name, session_id=plan["session_id"]))
    declared = action(app, "declare_trial", session_id=plan["session_id"], spec=trial_spec(step))
    return study, dataset, analysis, plan, declared


def test_explicit_plan_intent_and_user_requirements_are_retained_without_execution(tmp_path, monkeypatch):
    from devbench.analysis.method_planner import ScientificMethodPlanner

    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, step)
        requirement = {"name": "threshold", "value": 4,
                       "evidence": "Explicit researcher requirement in this test request: threshold=4."}
        request = {"analysis_ref": analysis["ref"], "dataset_ref": dataset["ref"],
                   "evaluation_purpose": "execution_verification", "user_parameter_requirements": [requirement]}
        plan = action(app, "create_plan", **request)
        assert plan["evaluation_purpose"] == "execution_verification"
        assert plan["user_parameter_requirements"] == [requirement]
        requirement["value"] = 999
        retained = methods.get("method_plan", plan["session_id"])
        assert retained["user_parameter_requirements"][0]["value"] == 4
        assert retained["selection_history"] == []
        assert not methods.list_records("run")
        assert not app.scientific.futures
        monkeypatch.setattr(ScientificMethodPlanner, "evidence_assessment",
                            lambda *args, **kwargs: pytest.fail("Listing recomputed execution evidence"))
        before = methods.database.read_bytes()
        listed = app.scientific.catalog()["plans"]
        assert next(row for row in listed if row["session_id"] == plan["session_id"]) == retained
        assert methods.database.read_bytes() == before
    finally:
        app.close()


@pytest.mark.parametrize("extra", [
    {"evaluation_purpose": "choose-my-favorite"},
    {"user_parameter_requirements": [{"name": "threshold", "value": 4, "evidence": ""}]},
])
def test_invalid_plan_attribution_is_rejected_before_creating_a_session(tmp_path, extra):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, step)
        with pytest.raises(ValueError):
            action(app, "create_plan", analysis_ref=analysis["ref"], dataset_ref=dataset["ref"], **extra)
        assert not methods.list_records("method_plan")
        assert not methods.list_records("run")
        assert not app.scientific.futures
    finally:
        app.close()


def test_listing_does_not_initialize_or_reconcile_legacy_records(tmp_path, monkeypatch):
    app, methods, legacy, _, _ = setup_workspace(tmp_path)
    try:
        before = methods.database.read_bytes()
        monkeypatch.setattr(type(methods), "reconcile", lambda _: pytest.fail("listing reconciled workers"))
        catalog = app.scientific.catalog()
        assert catalog["initialized"] is False
        assert methods.database.read_bytes() == before
        action(app, "initialize")
        assert app.scientific.catalog()["initialized"]
        assert methods.analysis(legacy["analysis_id"]) == legacy
        assert app.workspace_state()["schema_version"] == 3
    finally:
        app.close()


def test_trial_acceptance_restart_preserves_exact_versions_and_no_grants(tmp_path):
    app, methods, legacy, step, inputs = setup_workspace(tmp_path)
    try:
        study, dataset, analysis, plan, declared = scientific_plan(app, step)
        request = {"action": "run_trial", "expected_revision": app.scientific.catalog()["revision"],
            "session_id": plan["session_id"], "experiment_id": "qc-pilot", "allow_data_execution": True,
            "idempotency_key": uuid.uuid4().hex}
        first = app.scientific.action(request)
        duplicate = app.scientific.action(deepcopy(request))
        assert first["run_id"] == duplicate["run_id"]
        settled(app, first)
        catalog = app.scientific.catalog()
        assert len(catalog["runs"]) == 1
        assert len(catalog["results"]) == 1
        result = catalog["results"][0]
        assert result["review_status"] == "candidate"
        accepted_definition = declared["analysis_ref"]
        runs_before = methods.list_records("run")
        accepted = action(app, "accept_result", result_ref=result["ref"], rationale="Reviewed synthetic evidence",
                          acknowledge_warnings=True, select_role="qc")
        assert accepted["review_status"] == "accepted"
        assert methods.list_records("run") == runs_before
        assert methods.analysis(legacy["analysis_id"]) == legacy
        logical = next(row for row in app.scientific.catalog()["analyses"] if row["analysis_id"] == analysis["analysis_id"])
        assert logical["ref"] == accepted_definition
        saved = app.workspace_state()
        ui = {**saved["ui"], "view": "scientific", "study_id": study["study_id"],
              "dataset_ref": dataset["ref"], "analysis_ref": accepted_definition, "result_ref": result["ref"],
              "planner_session_id": plan["session_id"]}
        app.workspace_state({"expected_revision": saved["revision"], "ui": ui})
        root, method_root = app.workspace, methods.root
    finally:
        app.close()
    restored = WorkbenchService(root, [inputs], enable_scientific=True, scientific_workspace=method_root)
    try:
        assert restored.workspace_state()["ui"] == ui
        assert restored.scientific.resolve(result["ref"])["ref"] == result["ref"]
        assert restored.allow_data_execution is False
        assert restored.allow_model_execution is False
        assert restored.scientific.futures == {}
        assert methods.list_records("run") == runs_before
    finally:
        restored.close()


def test_api_mutation_requires_revision_and_trial_requires_fresh_permission(tmp_path):
    app, _, _, step, _ = setup_workspace(tmp_path)
    try:
        _, _, _, plan, _ = scientific_plan(app, step)
        with pytest.raises((ValueError, RuntimeError), match="revision"):
            app.scientific.action({"action": "create_study", "name": "stale", "aim": "stale", "expected_revision": 0})
        with pytest.raises(PermissionError):
            action(app, "run_trial", session_id=plan["session_id"], experiment_id="qc-pilot",
                   idempotency_key=uuid.uuid4().hex)
        assert not app.scientific.catalog()["runs"]
    finally:
        app.close()


def test_rerun_has_new_invocation_and_keeps_the_same_analysis_version(tmp_path):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis, plan, declared = scientific_plan(app, step)
        first = action(app, "run_trial", session_id=plan["session_id"], experiment_id="qc-pilot",
                       allow_data_execution=True, idempotency_key=uuid.uuid4().hex)
        settled(app, first)
        second = action(app, "rerun", analysis_ref=declared["analysis_ref"], dataset_ref=dataset["ref"],
                        allow_data_execution=True, idempotency_key=uuid.uuid4().hex)
        settled(app, second)
        assert first["run_id"] != second["run_id"]
        catalog = app.scientific.catalog()
        assert len(catalog["runs"]) == len(methods.list_records("run")) == 2
        assert {run["analysis_version_id"] for run in catalog["runs"]} == {declared["analysis_ref"]["version_id"]}
        assert len([row for row in catalog["analysis_versions"] if row["analysis_id"] == analysis["analysis_id"]]) == 2  # draft + execution definition
    finally:
        app.close()


def test_unknown_dispatch_liveness_is_not_retried_after_restart(tmp_path, monkeypatch):
    app, methods, _, step, inputs = setup_workspace(tmp_path)
    try:
        _, _, _, plan, _ = scientific_plan(app, step)
        monkeypatch.setattr(app._executor, "submit", lambda *args, **kwargs: Future())
        request = {"action": "run_trial", "expected_revision": app.scientific.catalog()["revision"],
                   "session_id": plan["session_id"], "experiment_id": "qc-pilot", "allow_data_execution": True,
                   "idempotency_key": uuid.uuid4().hex}
        first = app.scientific.action(request)
        root = app.workspace
        assert methods.list_records("run") == []
    finally:
        app.close()
    restored = WorkbenchService(root, [inputs], enable_scientific=True, scientific_workspace=methods.root)
    try:
        replay = restored.scientific.action(request)
        assert replay["run_id"] == first["run_id"]
        assert replay["status"] == "recovery_required"
        assert restored.scientific.catalog()["attempts"][0]["worker_liveness"] == "unknown"
        assert restored.scientific.futures == {}
        assert methods.list_records("run") == []
    finally:
        restored.close()


def test_view_schema_upgrade_reconciles_after_directory_sync_failure(tmp_path, monkeypatch):
    app, _, _, _, _ = setup_workspace(tmp_path)
    try:
        original = app._sync_workspace_directory
        calls = 0
        def fail_after_replace():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated directory sync failure")
            original()
        monkeypatch.setattr(app, "_sync_workspace_directory", fail_after_replace)
        with pytest.raises(RuntimeError, match="interrupted"):
            action(app, "initialize")
        assert app.workspace_state()["schema_version"] == 3
        assert app.allow_model_execution is False
    finally:
        app.close()


def test_successful_method_receipt_cannot_bypass_failed_trial_budget(tmp_path, monkeypatch):
    import devbench.analysis.method_planner as planning
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, _, _, plan, _ = scientific_plan(app, step)
        ticks = iter([0, 1, 40])
        monkeypatch.setattr(planning, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        response = action(app, "run_trial", session_id=plan["session_id"], experiment_id="qc-pilot",
                          allow_data_execution=True, idempotency_key=uuid.uuid4().hex)
        published = settled(app, response)
        assert methods.list_records("run")[0]["status"] == "succeeded"
        assert published["results"] == []
        assert published["unavailable"][0]["trial_status"] == "resource_limit"
        catalog = app.scientific.catalog()
        assert catalog["validations"]
        assert catalog["results"] == []
    finally:
        app.close()


@pytest.mark.parametrize("already_accepted", [False, True])
def test_explicit_legacy_method_import_is_verified_and_idempotent(tmp_path, already_accepted):
    app, methods, legacy, step, _ = setup_workspace(tmp_path)
    native = methods.run_step(step["step_id"], actor="earlier-researcher", executor=FixtureExecutor())
    if already_accepted:
        proposal = methods.propose(native["run_id"], actor="earlier-researcher", rationale="Original review")
        legacy = methods.approve(proposal["proposal_id"], actor="earlier-researcher", rationale="Original accepted history", acknowledge_warnings=True)
    try:
        action(app, "initialize")
        retained = next(row for row in app.scientific.catalog()["legacy_method_runs"] if row["run_id"] == native["run_id"])
        assert retained["analysis_id"] == legacy["analysis_id"]
        study = action(app, "create_study", name="Imported study", aim="Retain selected legacy evidence")
        dataset = action(app, "register_dataset", study_id=study["study_id"], name="Retained data",
            members={"dataset": {"artifact_id": step["inputs"]["dataset"]}}, metadata={})
        analysis = action(app, "create_analysis", study_id=study["study_id"], name="Explicit imported definition",
            objective="Review the original invocation", computational_task="quality_control",
            legacy_analysis_id=legacy["analysis_id"], execution_source_run_id=native["run_id"])
        request = {"action": "import_legacy", "expected_revision": app.scientific.catalog()["revision"],
            "source_kind": "method_run", "source_id": native["run_id"],
            "mappings": {"study_id": study["study_id"], "dataset_ref": dataset["ref"], "analysis_ref": analysis["ref"]}}
        imported = app.scientific.action(request)
        again = app.scientific.action(request)
        assert imported == again
        assert imported["status"] == "linked"
        assert len(app.scientific.catalog()["runs"]) == 1
        assert len(app.scientific.catalog()["results"]) == 1
        assert methods.list_records("run") == [native]
        assert methods.analysis(legacy["analysis_id"]) == legacy
        assert methods.provenance(native["run_id"])["valid"]
        if already_accepted:
            result = app.scientific.catalog()["results"][0]
            accepted = action(app, "accept_result", result_ref=result["ref"], rationale="Review imported scientific meaning", acknowledge_warnings=True)
            assert accepted["review_status"] == "accepted"
            assert methods.analysis(legacy["analysis_id"]) == legacy
            assert methods.list_records("run") == [native]
    finally:
        app.close()


def test_rerun_honors_explicit_step_subset_from_selected_manifest(tmp_path):
    app, methods, legacy, step, inputs = setup_workspace(tmp_path)
    native = methods.run_step(step["step_id"], actor="earlier-researcher", executor=FixtureExecutor())
    annotation = inputs / "annotation.json"
    annotation.write_text('{"sample":"synthetic annotation"}')
    try:
        action(app, "initialize")
        study = action(app, "create_study", name="Input-role study", aim="Preserve declared execution bindings")
        dataset = action(app, "register_dataset", study_id=study["study_id"], name="Data and annotation",
            members={"dataset": {"artifact_id": step["inputs"]["dataset"]},
                     "annotation": {"path": str(annotation), "artifact_type": "json"}}, metadata={})
        analysis = action(app, "create_analysis", study_id=study["study_id"], name="Explicit earlier invocation",
            objective="Repeat the original declared input roles", computational_task="quality_control",
            legacy_analysis_id=legacy["analysis_id"], execution_source_run_id=native["run_id"])
        response = action(app, "rerun", analysis_ref=analysis["ref"], dataset_ref=dataset["ref"],
            idempotency_key=uuid.uuid4().hex, allow_data_execution=True)
        value = settled(app, response)
        assert value["results"] and not value["unavailable"]
        runs = methods.list_records("run")
        assert len(runs) == 2
        assert all(run["inputs"] == step["inputs"] and run["status"] == "succeeded" for run in runs)
    finally:
        app.close()
