"""Actual CLI process round trips using reviewed local actions, never a model."""
import json
from pathlib import Path
import subprocess
import sys

from tests.test_analysis_episodes import inputs, workspace
from tests.test_episode_handoff import REPLY


def invoke(*args):
    return subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli", *map(str, args)],
        capture_output=True, text=True, timeout=90)


def test_real_cli_exit_and_resume_preserves_diagnostics_and_finishes(workspace):
    task, paths = inputs(workspace)
    spec = workspace / "spec.json"
    spec.write_text(json.dumps({"question": "Estimate the declared donor contrast", "assay": "bulk",
        "public_task": task, "inputs": {key: str(path) for key, path in paths.items()}}))
    first = workspace / "first.json"
    first.write_text(json.dumps([{"kind": "tool", "tool": "audit_expression_quality"},
        {"kind": "tool", "tool": "validate_biological_units"},
        {"kind": "request_information", "message": "Confirm the intended interpretation"}]))
    root = workspace / "cli-episode"
    result = invoke("--spec", spec, "--actions", first, "--output", root, "--allow-data-execution")
    assert result.returncode == 5, result.stderr
    pending = json.loads(result.stdout)
    assert pending["information_request"] == "Confirm the intended interpretation"
    assert pending["receipt"] is None
    handoff = pending["handoff"]
    old = (root / "tool-evidence/root.json").read_bytes()
    reply = workspace / "reply.json"
    reply.write_text(json.dumps(REPLY))
    rest = workspace / "rest.json"
    rest.write_text(json.dumps([{"kind": "tool", "tool": "validate_design_matrix"},
        {"kind": "tool", "tool": "mean_condition_analysis"}, {"kind": "finish"}]))
    result = invoke("--resume", handoff["manifest_path"], "--expected-root", handoff["root_hash"],
        "--response", reply, "--actions", rest, "--output", root, "--allow-data-execution")
    assert result.returncode == 0, result.stderr
    final = json.loads(result.stdout)
    assert final["observation"]["outcome"] == "completed"
    assert final["observation"]["steps"] == 6
    assert final["observation"]["agent_execution"]["model_sessions_attempted"] == 0
    assert (root / "tool-evidence/root.json").read_bytes() == old
    from devbench.analysis.episodes import verify_episode
    assert verify_episode(root, final["receipt"]["root_hash"])["valid"]


def test_cli_resume_denies_model_execution_before_reading_handoff(workspace):
    result = invoke("--resume", workspace / "missing.json", "--expected-root", "0" * 64,
        "--response", workspace / "missing-reply.json", "--codex-model", "fixture-model",
        "--agent-workspaces", workspace / "actors", "--output", workspace / "missing-episode",
        "--allow-data-execution")
    assert result.returncode == 2
    assert "requires --allow-model-execution" in result.stderr
    assert not (workspace / "actors").exists()


def test_cli_resume_cannot_replace_frozen_specification(workspace):
    result = invoke("--resume", workspace / "missing.json", "--expected-root", "0" * 64,
        "--response", workspace / "missing-reply.json", "--spec", workspace / "replacement.json",
        "--actions", workspace / "actions.json", "--output", workspace / "missing-episode", "--allow-data-execution")
    assert result.returncode == 2
    assert "excludes --spec" in result.stderr


def test_cli_resumes_frozen_nondefault_selector_settings(workspace, monkeypatch, capsys):
    from devbench.analysis import episode_cli, episode_codex
    from devbench.analysis.episode_agent import AgentLimits, drive_episode
    from tests.test_analysis_episodes import create
    from tests.test_episode_agent import ScriptedSelector, sequence
    class FakeCodexSelector(ScriptedSelector):
        def __init__(self, *, requested_model, memory_mb=2048, reasoning_effort=None, executable="codex", **kwargs):
            super().__init__([{"kind": "finish"}])
            self.model, self.memory, self.effort, self.executable = requested_model, memory_mb, reasoning_effort, executable
        def configuration(self):
            return {"backend": "codex-cli", "requested_model": self.model, "memory_mb": self.memory,
                "reasoning_effort": self.effort, "executable": self.executable}
        def select(self, context, output, **kwargs):
            kwargs.pop("allow_model_execution", None)
            return super().select(context, output, **kwargs)
    first = FakeCodexSelector(requested_model="fixture-model", memory_mb=128, reasoning_effort="low")
    first.actions = iter(sequence()[:-1] + [{"kind": "request_information", "message": "Confirm interpretation"}])
    episode, _ = create(workspace)
    pending = drive_episode(episode, first, limits=AgentLimits(memory_mb=128), allow_data_execution=True)
    handoff = pending["handoff"]
    reply = workspace / "reply.json"
    reply.write_text(json.dumps(REPLY))
    monkeypatch.setattr(episode_codex, "CodexActionSelector", FakeCodexSelector)
    capsys.readouterr()
    code = episode_cli.main(["--resume", handoff["manifest_path"], "--expected-root", handoff["root_hash"],
        "--response", str(reply), "--output", str(episode.root), "--codex-model", "fixture-model",
        "--agent-workspaces", str(workspace / "actors"), "--allow-data-execution", "--allow-model-execution"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["observation"]["outcome"] == "completed"
