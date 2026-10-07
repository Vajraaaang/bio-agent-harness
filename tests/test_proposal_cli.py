"""Review-route CLI controls; no actual model participant is launched."""
import json
import subprocess
import sys

from tests.test_analysis_episodes import workspace
from tests.test_episode_proposals import prepared, reviewed


def test_cli_starts_reviewed_episode_with_explicit_data_permission(workspace):
    from devbench.analysis.episodes import verify_episode
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    actions = [{"kind": "tool", "tool": name} for name in (
        "audit_expression_quality", "validate_biological_units", "validate_design_matrix", "mean_condition_analysis")]
    actions.append({"kind": "finish"})
    action_path = workspace / "actions.json"
    action_path.write_text(json.dumps(actions))
    output = workspace / "episode"
    command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--proposal", proposal["manifest_path"],
               "--proposal-root", proposal["root_hash"], "--review", review["manifest_path"], "--review-root", review["root_hash"],
               "--actions", str(action_path), "--output", str(output), "--allow-data-execution"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    result = json.loads(result.stdout)
    assert result["observation"]["review_status"] == "recorded_operator_research_review"
    assert result["observation"]["outcome"] == "completed"
    assert verify_episode(output, result["receipt"]["root_hash"])["valid"]


def test_reviewed_cli_requires_model_permission_before_reading_proposal(workspace):
    result = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--proposal", str(workspace / "missing"), "--proposal-root", "a" * 64, "--review", str(workspace / "missing-review"),
        "--review-root", "b" * 64, "--output", str(workspace / "denied"), "--allow-data-execution",
        "--codex-model", "unused-model", "--agent-workspaces", str(workspace / "actors")],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "requires --allow-model-execution" in result.stderr
    assert not (workspace / "denied").exists()


def test_reviewed_cli_cannot_replace_the_approved_spec(workspace):
    result = subprocess.run([sys.executable, "-m", "devbench.analysis.episode_cli",
        "--proposal", str(workspace / "missing"), "--spec", str(workspace / "replacement"),
        "--output", str(workspace / "denied"), "--allow-data-execution"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "proposal" in result.stderr and "excludes" in result.stderr
    assert not (workspace / "denied").exists()


def test_reviewed_selector_setup_failure_is_sealed_without_a_provider_call(workspace, monkeypatch, capsys):
    from devbench.analysis import episode_cli, episode_codex
    from devbench.analysis.episodes import verify_episode
    proposal, _ = prepared(workspace)
    review = reviewed(workspace, proposal)
    def fail(**kwargs):
        raise RuntimeError("injected selector setup failure")
    monkeypatch.setattr(episode_codex, "CodexActionSelector", fail)
    output = workspace / "failed-setup"
    code = episode_cli.main(["--proposal", proposal["manifest_path"], "--proposal-root", proposal["root_hash"],
        "--review", review["manifest_path"], "--review-root", review["root_hash"], "--output", str(output),
        "--allow-data-execution", "--allow-model-execution", "--codex-model", "offline-fixture",
        "--agent-workspaces", str(workspace / "actors")])
    assert code == 4
    assert json.loads(capsys.readouterr().out)["observation"]["outcome"] == "infrastructure_failure"
    final = json.loads((output / "episode.json").read_text())
    assert final["outcome"] == "infrastructure_failure" and not final["harness_accepted"]
    assert final["agent_execution"]["model_sessions_attempted"] == 0
    assert "injected selector setup failure" in final["reason"]
    receipt = json.loads((output / "root-receipt.json").read_text())
    assert verify_episode(output, receipt["root_hash"])["valid"]
