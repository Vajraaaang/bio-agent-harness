"""Pure configuration/guard checks; process and numerical paths remain unrun."""
from pathlib import Path

import pytest

from devbench.study.contracts import RunBudget


@pytest.mark.parametrize("allowed", [False, None, 0, 1, "true"])
def test_supervision_rejects_before_arguments_or_filesystem(allowed, monkeypatch):
    from devbench.study.supervision import supervise_coordinator
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("Guard reached filesystem"))
    with pytest.raises(PermissionError, match="disabled"):
        supervise_coordinator(object(), object(), object(), object(), allow_data_execution=allowed)


def test_writer_and_child_entry_are_default_guarded():
    from devbench.study.supervision import _coordinator_entry, write_coordinator_json
    with pytest.raises(PermissionError, match="disabled"):
        write_coordinator_json(None, None)
    with pytest.raises(PermissionError, match="disabled"):
        _coordinator_entry(None, None, None, None, None, None, None)


def test_existing_top_level_coordinator_worker_is_valid_without_calling_it():
    from devbench.study.execution import _attempt_worker
    from devbench.study.supervision import validate_supervision_configuration
    budget, output = validate_supervision_configuration(
        _attempt_worker, (), Path("/opt/devbench/private/one-attempt"), RunBudget(),
        result_name="attempt_result.json", checkpoint_names=("dispatch_checkpoint.json", "staging_checkpoint.json"))
    assert budget == RunBudget()
    assert output == Path("/opt/devbench/private/one-attempt")


@pytest.mark.parametrize("worker", ["devbench.study.execution._attempt_worker", None, str, Path, lambda: None])
def test_restored_names_and_non_coordinator_callables_are_not_worker_configuration(worker):
    from devbench.study.supervision import validate_supervision_configuration
    with pytest.raises((ValueError, TypeError)):
        validate_supervision_configuration(worker, (), Path("/opt/devbench/private/a"), RunBudget())


@pytest.mark.parametrize("changes", [
    {"args": []}, {"output_dir": Path("relative")}, {"output_dir": Path("/tmp/private-run")},
    {"budget": None}, {"budget": RunBudget(token_budget=10)},
    {"result_name": "../result.json"}, {"result_name": "worker_error.json"},
    {"checkpoint_names": ["dispatch.json"]}, {"checkpoint_names": ("../escape.json",)},
    {"checkpoint_names": ("repeated.json", "repeated.json")},
    {"checkpoint_names": tuple(f"checkpoint-{index}.json" for index in range(9))},
])
def test_invalid_supervision_configuration_has_no_execution_side_effects(changes):
    from devbench.study.execution import _attempt_worker
    from devbench.study.supervision import validate_supervision_configuration
    arguments = {"worker": _attempt_worker, "args": (), "output_dir": Path("/opt/devbench/private/a"),
                 "budget": RunBudget(), **changes}
    with pytest.raises((ValueError, TypeError)):
        validate_supervision_configuration(**arguments)


def test_supervisor_source_has_no_scientific_contract_dependency():
    """Inspect only module source, never data or a staged result."""
    import ast
    import inspect
    from devbench.study import supervision
    tree = ast.parse(inspect.getsource(supervision))
    forbidden = {"StudyPlan", "AttemptRecord", "StudyAnswer", "score_answer", "evaluate_workflow"}
    assert not {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} & forbidden
    assert not any(isinstance(node, ast.ImportFrom) and node.module in {"execution", "records", "scoring", "answers"}
                   for node in ast.walk(tree))


def test_empty_supervision_result_is_immutable_and_json_safe():
    from dataclasses import FrozenInstanceError
    from devbench.study.supervision import SupervisionResult
    result = SupervisionResult(None, None, None, False, False, 0.0, None,
                               "unavailable", False, (), ())
    assert result.as_dict()["artifacts"] == []
    assert result.as_dict()["parent_artifact_capture_RSS_MB"] is None
    with pytest.raises(FrozenInstanceError):
        result.result_present = True


@pytest.mark.parametrize("changes", [
    {"elapsed_sec": float("nan")}, {"RSS_MB": True}, {"exitcode": False},
    {"rss_coverage": "complete"}, {"error": "x" * 2001}, {"artifacts": []},
    {"artifact_errors": ("x" * 2001,)}, {"failure_kind": "guessed"},
])
def test_supervision_result_rejects_unbounded_or_invented_control_states(changes):
    from devbench.study.supervision import SupervisionResult
    values = dict(exitcode=None, failure_kind=None, error=None, interrupted=False,
                  resource_failure=False, elapsed_sec=0.0, RSS_MB=None, rss_coverage="unavailable",
                  result_present=False, artifacts=(), artifact_errors=())
    with pytest.raises(ValueError):
        SupervisionResult(**{**values, **changes})


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local storage/process fixture requires opt-in")
def test_actual_owned_writer_reports_process_and_raw_artifact_evidence():
    """An actual empty JSON writer, never a model or numerical-result mock."""
    import hashlib
    import tempfile
    from devbench.study.supervision import supervise_coordinator, write_coordinator_json
    repository_tmp = Path(__file__).resolve().parents[1] / "tmp"
    repository_tmp.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="supervision-empty-", dir=repository_tmp) as directory:
        output = Path(directory).resolve()
        result = supervise_coordinator(write_coordinator_json, (output / "attempt_result.json", {}),
                                       output, RunBudget(timeout_sec=30), allow_data_execution=True)
        assert result.exitcode == 0 and result.failure_kind is None and result.result_present
        receipt = next(item for item in result.artifacts if item.kind == "result")
        assert receipt.sha256 == hashlib.sha256(b"{}\n").hexdigest()
        assert receipt.bytes == 3 and result.elapsed_sec > 0
