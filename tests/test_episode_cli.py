"""Runnable coordinator entry point; does not invoke Codex/model participants."""
import json
import subprocess
import sys

from tests.test_analysis_episodes import api, create, inputs, run_contrast, workspace as _workspace

workspace = _workspace


def test_cli_denies_before_reading_configuration(workspace):
    output = workspace / "denied"
    result = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--spec", str(workspace / "missing.json"), "--actions", str(workspace / "missing-actions.json"),
        "--output", str(output)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "--allow-data-execution" in result.stderr
    assert not output.exists()


def test_cli_executes_real_bulk_flow_without_model_backend(workspace, monkeypatch):
    runs = workspace / ".runs"
    monkeypatch.setenv("DEVBENCH_RUNS_ROOT", str(runs))
    task, paths = inputs(workspace)
    spec = {"question": "Estimate B minus A", "assay": "bulk", "public_task": task,
            "inputs": {name: str(path.relative_to(workspace)) for name, path in paths.items()}}
    (workspace / "spec.json").write_text(json.dumps(spec))
    actions = [{"kind": "tool", "tool": name} for name in (
        "audit_expression_quality", "validate_biological_units", "validate_design_matrix", "mean_condition_analysis")]
    actions.append({"kind": "finish"})
    (workspace / "actions.json").write_text(json.dumps(actions))
    output = workspace / "cli-episode"
    result = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--spec", str(workspace / "spec.json"), "--actions", str(workspace / "actions.json"),
        "--output", str(output), "--allow-data-execution"], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["observation"]["outcome"] == "completed"
    assert record["observation"]["usage"]["model"] is None
    assert (output / "summary.md").is_file()
    _, _, verify = api()
    assert verify(output, record["receipt"]["root_hash"])["valid"]
    binding = json.loads((output / "execution-code.json").read_text())
    assert (runs / binding["run_id"] / "code_snapshot" / "snapshot-manifest.json").is_file()
    replay = workspace / "retained-replay"
    replayed = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--replay", str(output), "--expected-root", record["receipt"]["root_hash"],
        "--output", str(replay), "--allow-data-execution"], capture_output=True, text=True, timeout=90)
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads((replay / "execution-code.json").read_text()) == binding
    bundle_output = workspace / "bundle-replay"
    bundle_replay = subprocess.run(
        [sys.executable, "-P", str(output / "replay_bundle/replay.py"),
         "--expected-root", record["receipt"]["root_hash"],
         "--output", str(bundle_output), "--allow-data-execution"],
        capture_output=True, text=True, timeout=90,
    )
    assert bundle_replay.returncode == 0, bundle_replay.stderr
    assert json.loads(bundle_replay.stdout)["exact_target_hash_match"] is True
    assert json.loads((bundle_output / "execution-code.json").read_text()) == binding


def test_replay_can_start_from_saved_artifacts_without_original_object(workspace):
    import devbench.analysis.episodes as module
    assert hasattr(module, "replay_episode"), "A saved episode needs a stateless replay entry point"
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    episode.step({"kind": "finish"})
    root, receipt = episode.root, episode.receipt["root_hash"]
    del episode
    replay = module.replay_episode(root, receipt, workspace / "fresh-replay", allow_data_execution=True)
    assert replay.outcome == "completed"
    assert replay.last_result["target_results"] == observed["result"]["target_results"]


def test_cli_codex_mode_requires_separate_authorization_before_spec_access(workspace):
    output = workspace / "denied-codex"
    result = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--spec", str(workspace / "missing.json"), "--codex-model", "fixture-model",
        "--agent-workspaces", str(workspace / "actors"), "--output", str(output),
        "--allow-data-execution"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "requires --allow-model-execution" in result.stderr
    assert not output.exists() and not (workspace / "actors").exists()


def test_cli_rejects_actions_and_codex_mode_together(workspace):
    result = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--spec", str(workspace / "missing.json"), "--actions", str(workspace / "missing-actions.json"),
        "--codex-model", "fixture-model", "--agent-workspaces", str(workspace / "actors"),
        "--output", str(workspace / "denied"), "--allow-data-execution", "--allow-model-execution"],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "Choose exactly one" in result.stderr
    assert not (workspace / "denied").exists()


def test_cli_codex_route_connects_selector_to_real_numerical_tools(workspace, monkeypatch, capsys):
    from devbench.analysis import episode_cli, episode_codex
    from tests.test_episode_agent import ScriptedSelector, sequence
    class FixtureSelector(ScriptedSelector):
        def __init__(self, **kwargs):
            super().__init__(sequence())
        def episode_lease(self):
            from contextlib import nullcontext
            return nullcontext()

        def select(self, context, output, **kwargs):
            assert kwargs.pop("allow_model_execution") is False
            return super().select(context, output, **kwargs)
    monkeypatch.setattr(episode_codex, "CodexActionSelector", FixtureSelector)
    task, paths = inputs(workspace)
    spec = {"question": "Fixture donor contrast", "assay": "bulk", "public_task": task,
        "inputs": {key: str(path) for key, path in paths.items()}, "agent_limits": {"max_selections": 5}}
    spec_path = workspace / "spec.json"
    spec_path.write_text(json.dumps(spec))
    output = workspace / "codex-route"
    code = episode_cli.main(["--spec", str(spec_path), "--output", str(output), "--codex-model", "offline-fixture",
        "--agent-workspaces", str(workspace / "actors"), "--allow-data-execution", "--allow-model-execution"])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["observation"]["outcome"] == "completed"
    assert result["observation"]["agent_execution"]["model_sessions_attempted"] == 0
    assert api()[2](output, result["receipt"]["root_hash"])["valid"]


def test_cli_resumes_authenticated_snapshot_handoff(workspace, monkeypatch):
    monkeypatch.setenv("DEVBENCH_RUNS_ROOT", str(workspace / ".runs"))
    task, paths = inputs(workspace)
    spec = workspace / "spec.json"
    spec.write_text(json.dumps({"question": "Estimate B minus A", "assay": "bulk",
                                "public_task": task, "inputs": {name: str(path) for name, path in paths.items()}}))
    actions = workspace / "actions.json"
    actions.write_text(json.dumps([{"kind": "tool", "tool": "audit_expression_quality"},
                                   {"kind": "request_information", "message": "Confirm interpretation"}]))
    output = workspace / "cli-handoff"
    command = [sys.executable, "-m", "devbench.analysis.episode_cli",
               "--output", str(output), "--allow-data-execution"]
    paused = subprocess.run([*command, "--spec", str(spec), "--actions", str(actions)],
                            capture_output=True, text=True, timeout=90)
    assert paused.returncode == 5, paused.stderr
    receipt = json.loads(paused.stdout)["handoff"]
    binding = (output / "execution-code.json").read_bytes()
    response = workspace / "response.json"
    response.write_text(json.dumps({"message": "Continue with the declared design.",
                                    "preserves_frozen_inputs_and_settings": True}))
    actions.write_text(json.dumps([{"kind": "tool", "tool": name} for name in
                                   ("validate_biological_units", "validate_design_matrix", "mean_condition_analysis")]
                                  + [{"kind": "finish"}]))
    resumed = subprocess.run([*command, "--resume", receipt["manifest_path"], "--expected-root", receipt["root_hash"],
                              "--response", str(response), "--actions", str(actions)],
                             capture_output=True, text=True, timeout=90)
    assert resumed.returncode == 0, resumed.stderr
    assert (output / "execution-code.json").read_bytes() == binding
    assert json.loads(resumed.stdout)["observation"]["outcome"] == "completed"
