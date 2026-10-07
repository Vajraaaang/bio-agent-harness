"""CLI integration and opt-in smoke guards; no live model or Docker in these tests."""

import importlib
import json
from pathlib import Path

import pytest

from devbench.analysis.episode_agent import Selection
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.methods import MethodService
from devharness_core.provenance import canonical_json


IMAGE = "fixture.invalid/python@sha256:" + "a" * 64


def _write(path, value):
    path.write_bytes(canonical_json(value))
    return str(path)


def _request(classification="synthetic"):
    return {"task": {"scientific_objective": "Explore synthetic QC alternatives",
                     "computational_task": "quality_control"},
            "dataset_summary": {"observed": {"synthetic_data": True}, "inferred": {}, "unknowns": []},
            "data_classification": classification}


def _candidate(name):
    return {"name": name, "method_family": "quality_control",
            "source": {"type": "generated", "reference": "Recorded synthetic test fixture"},
            "assumptions": [], "expected_inputs": ["dataset"], "dataset_fit": "plausible",
            "objective_fit": "strong", "known_failure_modes": ["Unvalidated fixture"],
            "parameter_candidates": []}


def _call(capsys, workspace, *args):
    from devbench.analysis.workbench import main
    status = main(["methods", "planner", "--workspace", str(workspace), "--actor", "synthetic-cli-test", *args])
    assert status == 0
    return json.loads(capsys.readouterr().out)


def _create(capsys, tmp_path, classification="synthetic"):
    workspace = tmp_path / "workspace"
    state = _call(capsys, workspace, "create", "--request", _write(tmp_path / "request.json", _request(classification)))
    return workspace, state["session_id"]


def test_workbench_routes_offline_science_resolution_show_and_state(capsys, tmp_path):
    workspace, session = _create(capsys, tmp_path)
    names = ["Unavailable scientific-first fixture", "Second scientific fixture"]
    decisions = _write(tmp_path / "science.json", {"candidates": [_candidate(name) for name in names]})
    science = _call(capsys, workspace, "--decisions", decisions, "science", "--plan", session)
    assert [item["name"] for item in science] == names
    bindings = _write(tmp_path / "resolution.json", {"bindings": [
        {"candidate": name, "method_version_id": None, "discovery": None} for name in reversed(names)]})
    resolved = _call(capsys, workspace, "--decisions", bindings, "resolve", "--plan", session,
                     "--versions", _write(tmp_path / "versions.json", []))
    assert [item["name"] for item in resolved] == names
    assert all(item["method_version_id"] is None for item in resolved)
    exported = _call(capsys, workspace, "show", "--plan", session)
    assert set(exported) == {"task", "dataset_summary", "candidates", "experiments", "results", "recommendation"}
    assert exported["recommendation"]["choice"] is None
    state = _call(capsys, workspace, "state", "--plan", session)
    assert len(state["selection_history"]) == 2
    assert all(item["status"] == "accepted" and item["evidence"]["model_execution"] is False
               for item in state["selection_history"])


@pytest.mark.parametrize("classification,authorize", [("synthetic", False), ("private_clinical", True),
                                                       ("private", True), ("unclassified", True)])
def test_live_science_requires_model_authorization_and_allowed_data_classification(
        capsys, tmp_path, monkeypatch, classification, authorize):
    workspace, session = _create(capsys, tmp_path, classification)
    module = importlib.import_module("devbench.analysis.episode_codex")

    class ForbiddenSelector:
        def __init__(self, **kwargs):
            pass

        def configuration(self):
            return {"backend": "codex-cli"}

        def select(self, *args, **kwargs):
            pytest.fail("Unauthorized context reached the model selector")

    monkeypatch.setattr(module, "CodexActionSelector", ForbiddenSelector)
    flags = ["--allow-model-execution"] if authorize else []
    with pytest.raises(PermissionError):
        _call(capsys, workspace, "--model", "unit-test-model", "--actor-workspaces", str(tmp_path / "actors"),
              *flags, "science", "--plan", session)
    state = ScientificMethodPlanner(MethodService(workspace)).get(session)
    assert state["selection_history"] == []
    assert state["scientific_candidates"] is None


def test_explicit_model_authorization_reaches_only_the_unit_stub(capsys, tmp_path, monkeypatch):
    workspace, session = _create(capsys, tmp_path)
    module = importlib.import_module("devbench.analysis.episode_codex")
    calls = []

    class StubSelector:
        def __init__(self, **kwargs):
            pass

        def configuration(self):
            return {"backend": "codex-cli"}

        def select(self, context, output, **kwargs):
            calls.append((context, kwargs))
            return Selection({"candidates": []}, {"backend": "unit_stub", "model_execution": False})

    monkeypatch.setattr(module, "CodexActionSelector", StubSelector)
    assert _call(capsys, workspace, "--model", "unit-test-model", "--actor-workspaces", str(tmp_path / "actors"),
                 "--allow-model-execution", "science", "--plan", session) == []
    assert len(calls) == 1 and calls[0][1]["allow_model_execution"] is True
    assert "registry" not in calls[0][0]


def test_trial_cli_requires_explicit_data_authorization(capsys, tmp_path, monkeypatch):
    from tests.test_method_planner import resolved, trial_spec
    planner, state, _, _, branch, step = resolved(tmp_path)
    planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="synthetic-cli-test")

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized CLI command attempted data execution")

    monkeypatch.setattr(MethodService, "run_step", forbidden)
    with pytest.raises(PermissionError):
        _call(capsys, planner.methods.root, "run-trial", "--plan", state["session_id"], "--experiment", "qc-pilot")
    current = planner.get(state["session_id"])
    assert current["experiments"][0]["status"] == "declared" and current["results"] == []


def test_source_cli_requires_explicit_network_authorization(capsys, tmp_path, monkeypatch):
    workspace, session = _create(capsys, tmp_path)
    module = importlib.import_module("devbench.analysis.method_sources")

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized CLI command attempted source retrieval")

    monkeypatch.setattr(module, "retrieve_source", forbidden)
    with pytest.raises(PermissionError):
        _call(capsys, workspace, "source", "--plan", session,
              "--url", "https://example.org/source", "--allow-host", "example.org")


def test_authoring_cli_registers_and_designs_without_execution_or_canonical_change(capsys, tmp_path, monkeypatch):
    from tests.test_method_planner import resolved, trial_spec
    planner, state, _, analysis, _, step = resolved(tmp_path)
    service, session = planner.methods, state["session_id"]
    before = canonical_json(service.analysis(analysis["analysis_id"]))
    original = service.method(step["method_version_id"])

    def forbidden(*args, **kwargs):
        pytest.fail("Authoring must not execute a method")

    monkeypatch.setattr(MethodService, "run_step", forbidden)
    draft = {"source_code": "raise RuntimeError('A retained draft must not execute on the host')\n",
             "algorithm_summary": "Synthetic inert draft fixture", "introduced_choices": ["Fail on any execution"],
             "source_references": []}
    record = _call(capsys, service.root, "--decisions", _write(tmp_path / "draft.json", draft),
        "draft-method", "--plan", session, "--candidate", "Unknown QC",
        "--interface", _write(tmp_path / "interface.json", original["contract"]),
        "--environment-lock", str(service.artifact_path(original["environment_artifact"])),
        "--justification", "Exercise inert registration of an explicitly authorized synthetic draft")
    assert record["status"] == "generated_unexecuted"
    assert record["method_version_id"] != original["version_id"]
    generated = service.method(record["method_version_id"])
    assert generated["contract"]["lifecycle"] == "custom"
    assert generated["contract"]["implementation_trust"] == "unreviewed"
    assert service.artifact_path(generated["source_artifact"]).read_text() == draft["source_code"]
    for key in ("parameters", "inputs", "outputs", "execution", "environment"):
        assert generated["contract"][key] == original["contract"][key]
    spec = trial_spec(step)
    proposals = _call(capsys, service.root, "--decisions", _write(tmp_path / "design.json", {"experiments": [spec]}),
                      "design-trials", "--plan", session)
    assert proposals == [spec]
    current = planner.get(session)
    assert current["trial_designs"][-1]["status"] == "unexecuted_drafts"
    assert current["experiments"] == [] and current["results"] == []
    assert current["candidates"][0]["method_version_id"] is None
    assert current["candidates"][1]["method_version_id"] == record["method_version_id"]
    assert canonical_json(service.analysis(analysis["analysis_id"])) == before


@pytest.mark.parametrize("command", ["design-trials", "draft-method"])
def test_authoring_cli_live_selection_requires_explicit_model_authorization(capsys, tmp_path, monkeypatch, command):
    from tests.test_method_planner import resolved
    planner, state, _, _, _, step = resolved(tmp_path)
    module = importlib.import_module("devbench.analysis.episode_codex")

    class ForbiddenSelector:
        def __init__(self, **kwargs):
            pass

        def configuration(self):
            return {"backend": "codex-cli"}

        def select(self, *args, **kwargs):
            pytest.fail("Authoring reached a live selector without authorization")

    monkeypatch.setattr(module, "CodexActionSelector", ForbiddenSelector)
    extra = []
    if command == "draft-method":
        method = planner.methods.method(step["method_version_id"])
        extra = ["--candidate", "Unknown QC", "--interface", _write(tmp_path / "interface.json", method["contract"]),
                 "--environment-lock", str(planner.methods.artifact_path(method["environment_artifact"])),
                 "--justification", "Synthetic draft authorization-gate test"]
    before = planner.get(state["session_id"])
    with pytest.raises(PermissionError):
        _call(capsys, planner.methods.root, "--model", "unit-test-model", "--actor-workspaces", str(tmp_path / "actors"),
              command, "--plan", state["session_id"], *extra)
    after = planner.get(state["session_id"])
    assert after["selection_history"] == before["selection_history"]
    assert after["experiments"] == [] and after["results"] == []


def _script():
    return importlib.import_module("scripts.verify_method_planner")


def test_smoke_implementation_response_only_adds_an_identifying_comment():
    source = "raise RuntimeError('This test must not execute source')\n"
    response = _script()._implementation_response(source, "synthetic-draft-1")
    assert response["source_code"].startswith(source)
    assert response["source_code"][len(source):] == "\n# devharness recorded implementation draft synthetic-draft-1\n"
    assert response["source_references"] == []
    assert response["introduced_choices"]


def test_smoke_implementation_comment_cannot_introduce_code():
    with pytest.raises(ValueError):
        _script()._implementation_response("# Source\n", "marker\nraise RuntimeError('injected')")


def test_oci_smoke_requires_opt_in_before_creating_output(monkeypatch, tmp_path):
    script = _script()
    monkeypatch.setattr(script, "verify_execution", lambda *args: pytest.fail("Unapproved OCI run"))
    output = tmp_path / "smoke"
    with pytest.raises(PermissionError):
        script.verify(output, IMAGE)
    assert not output.exists()


@pytest.mark.parametrize("output_kind,image", [("relative", IMAGE), ("fresh", "fixture.invalid/python:latest")])
def test_oci_smoke_requires_absolute_output_and_immutable_image(monkeypatch, tmp_path, output_kind, image):
    script = _script()
    monkeypatch.setattr(script, "verify_execution", lambda *args: pytest.fail("Invalid OCI run"))
    output = Path("invalid-relative-smoke") if output_kind == "relative" else tmp_path / "fresh"
    with pytest.raises(ValueError):
        script.verify(output, image, allow_data_execution=True)


def test_oci_smoke_preserves_failed_attempt_and_refuses_reuse(monkeypatch, tmp_path):
    script = _script()

    def fail(output, image):
        output.mkdir()
        (output / "retained-failure.json").write_text('{"status":"failed"}')
        raise RuntimeError("Synthetic bootstrap failure; no Docker was used")

    monkeypatch.setattr(script, "verify_execution", fail)
    output = tmp_path / "smoke"
    with pytest.raises(RuntimeError, match="bootstrap failure"):
        script.verify(output, IMAGE, allow_data_execution=True)
    assert (output / "execution" / "retained-failure.json").is_file()
    assert (output / "attempt.json").is_file()
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed" and summary["model_execution"] is False
    original = (output / "summary.json").read_bytes()
    with pytest.raises(FileExistsError):
        script.verify(output, IMAGE, allow_data_execution=True)
    assert (output / "summary.json").read_bytes() == original
