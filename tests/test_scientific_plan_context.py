"""Plan-request metadata forwarding, without workers, models or data execution."""
from copy import deepcopy
from types import SimpleNamespace

from devbench.analysis.scientific_workspace import ScientificWorkspace


def workspace_context():
    dataset_ref = {"object_type": "dataset", "object_id": "d" * 32, "version_id": "d" * 64}
    analysis_ref = {"object_type": "analysis", "object_id": "a" * 32, "version_id": "a" * 64}
    task = {"scientific_objective": "Assess quality; an assistant's earlier MAD/4 suggestion is not a requirement.",
            "computational_task": "quality_control"}
    versions = {
        "dataset": {"dataset_id": dataset_ref["object_id"], "version_id": dataset_ref["version_id"],
                    "ref": dataset_ref, "manifest": {"metadata": {"observed": {}, "inferred": {},
                    "unknowns": [], "data_classification": "synthetic"}}},
        "analysis": {"analysis_id": analysis_ref["object_id"], "ref": analysis_ref, "definition": {"task": task}},
    }
    calls = []

    def create(task, summary, **options):
        calls.append(deepcopy({"task": task, "summary": summary, **options}))
        return {"session_id": "f" * 32, **options}

    store = SimpleNamespace(
        resolve=lambda ref: deepcopy(versions[ref["object_type"]]),
        get=lambda kind, identity: {"study_id": "b" * 32, "legacy_analysis_id": "c" * 32},
        input_bindings=lambda versions: {},
        touch=lambda **fields: None,
    )
    workspace = object.__new__(ScientificWorkspace)
    workspace.project_id = "e" * 32
    workspace.store = lambda **options: store
    workspace.methods = lambda **options: SimpleNamespace(analysis=lambda identity: {"revision_id": "frozen-base"})
    workspace.planner = lambda: SimpleNamespace(create=create)
    workspace._save_plan_context = lambda state, **fields: {**state, **fields}
    body = {"analysis_ref": analysis_ref, "dataset_ref": dataset_ref}
    return workspace, body, calls, task


def test_plan_request_forwards_explicit_scope_and_requirement_records():
    workspace, body, calls, task = workspace_context()
    requirements = [{"name": "alpha", "value": 0.05,
                     "evidence": "Explicit requirement recorded in the researcher's request: alpha=0.05."}]
    result = workspace._create_plan({**body, "evaluation_purpose": "execution_verification",
                                    "user_parameter_requirements": requirements}, "local-researcher")
    assert calls[0]["evaluation_purpose"] == "execution_verification"
    assert calls[0]["user_parameter_requirements"] == requirements
    assert calls[0]["task"] == task
    assert result["scientific_base_revision"] == "frozen-base"
    assert calls[0]["anchors"]["analysis_version"] == body["analysis_ref"]
    assert calls[0]["anchors"]["dataset_versions"] == [body["dataset_ref"]]


def test_plan_request_does_not_infer_requirements_from_prior_method_mentions():
    workspace, body, calls, task = workspace_context()
    workspace._create_plan(body, "local-researcher")
    assert calls[0]["evaluation_purpose"] == "method_selection"
    assert calls[0]["user_parameter_requirements"] is None
    assert calls[0]["task"] == task


def test_planner_cli_forwards_explicit_scope_and_requirements(tmp_path, monkeypatch, capsys):
    import json
    from devbench.analysis import method_planner_cli

    calls = []
    requirement = {"name": "alpha", "value": 0.05, "evidence": "Explicit request recorded for this test."}
    request = {"task": {"scientific_objective": "Assess the data", "computational_task": "quality_control"},
               "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": []},
               "evaluation_purpose": "execution_verification", "user_parameter_requirements": [requirement]}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))

    def create(task, summary, **options):
        calls.append(options)
        return {"session_id": "f" * 32}

    monkeypatch.setattr(method_planner_cli, "MethodService", lambda *args, **kwargs: object())
    monkeypatch.setattr(method_planner_cli, "ScientificMethodPlanner", lambda *args: SimpleNamespace(create=create))
    method_planner_cli.main(["--workspace", str(tmp_path / "store"), "create", "--request", str(path)])
    assert calls[0]["evaluation_purpose"] == "execution_verification"
    assert calls[0]["user_parameter_requirements"] == [requirement]
    assert json.loads(capsys.readouterr().out)["session_id"] == "f" * 32
