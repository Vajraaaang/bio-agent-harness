"""Real local request/store integration; no selector or numerical execution."""
import json

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from tests.test_rna_scientific_contract import TASK, prepared_contract, scientific_contract
from tests.test_scientific_workspace import action, scientific_context, setup_workspace, tmp_path


def test_cli_persists_scientific_companion_in_real_method_store(tmp_path, capsys):
    from devbench.analysis.method_planner_cli import main

    planner, summary, companion = prepared_contract(tmp_path)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"task": TASK, "dataset_summary": summary,
                                  "scientific_contract": companion}))
    main(["--workspace", str(planner.methods.root), "create", "--request", str(request)])
    created = json.loads(capsys.readouterr().out)
    reopened = ScientificMethodPlanner(planner.methods)
    retained = reopened.get(created["session_id"])
    assert retained["scientific_contract"] == companion
    assert reopened.export_scientific_contract(created["session_id"])["sha256"] == retained["scientific_contract_sha256"]
    assert not planner.methods.list_records("run")
    assert retained["selection_history"] == []


def _browser_companion(app, methods, step, analysis):
    companion = scientific_contract(methods, list(step["inputs"].values()))
    objective = app.scientific.store().resolve(analysis["ref"])["definition"]["task"]["scientific_objective"]
    companion["sources"][0]["text"] = objective
    companion["objective"].update(statement=objective, source_excerpt=objective)
    return companion


def test_browser_action_persists_and_lists_companion_in_real_scientific_store(tmp_path):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, step)
        companion = _browser_companion(app, methods, step, analysis)
        plan = action(app, "create_plan", analysis_ref=analysis["ref"],
                      dataset_ref=dataset["ref"], scientific_contract=companion)
        reopened = app.scientific.planner()
        retained = reopened.get(plan["session_id"])
        assert retained["scientific_contract"] == companion
        assert retained["scientific_anchors"]["analysis_version"] == analysis["ref"]
        assert retained["scientific_anchors"]["dataset_versions"] == [dataset["ref"]]
        listed = next(row for row in app.scientific.catalog()["plans"] if row["session_id"] == plan["session_id"])
        assert listed["scientific_contract_sha256"] == retained["scientific_contract_sha256"]
        assert len(reopened.export(plan["session_id"])) == 6
        assert not methods.list_records("run") and not app.scientific.futures
        assert retained["selection_history"] == []
    finally:
        app.close()


def test_browser_rejects_contradictory_companion_before_creating_plan(tmp_path):
    app, methods, _, step, _ = setup_workspace(tmp_path)
    try:
        _, dataset, analysis = scientific_context(app, step)
        companion = _browser_companion(app, methods, step, analysis)
        companion["objective"]["statement"] = "A different unrequested objective"
        with pytest.raises(ValueError):
            action(app, "create_plan", analysis_ref=analysis["ref"],
                   dataset_ref=dataset["ref"], scientific_contract=companion)
        assert not methods.list_records("method_plan")
        assert not methods.list_records("run") and not app.scientific.futures
    finally:
        app.close()
