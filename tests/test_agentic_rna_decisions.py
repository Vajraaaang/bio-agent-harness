"""Codex chooses the RNA-seq method. These checks do not call a model."""
import hashlib
from pathlib import Path

import pytest

from tests.test_analysis_episodes import create, workspace


def test_prompt_states_the_scientific_choice_and_drops_repeated_policy():
    from devbench.analysis.episode_codex import prompt_context, selection_instruction
    policy = {"limit": 1, "scope": "repeated-boilerplate"}
    context = {"observation": {"allowed_tools": ["mean_condition_analysis"], "question": "B minus A",
        "tool_catalog": {"mean_condition_analysis": {"capability": {
            "target_ids": ["mean_contrast"], "design_condition_policy": policy,
            "qualification": "unvalidated_numerical_implementation",
            "estimation_limits": {"mean_contrast": "visible"}}}}}}
    instruction = selection_instruction(context)
    assert "No tool sequence is prescribed" in instruction
    assert "method parameters are fixed" not in instruction
    assert instruction.count("repeated-boilerplate") == 1
    compact = prompt_context(context)
    capability = compact["observation"]["tool_catalog"]["mean_condition_analysis"]["capability"]
    assert "design_condition_policy" not in capability
    assert compact["observation"]["design_condition_policy"] == policy
    assert "Required JSON response schema:" in instruction


def test_compatible_cli_accepts_0_147_and_0_163(tmp_path, monkeypatch):
    from devbench.study import cli_identity
    from devbench_engine.adapters import CodexCLIAdapter
    binary = tmp_path / "codex"
    binary.write_bytes(b"fixture executable")
    seen = {}

    def run(self, command, workspace, environment, instruction, timeout_sec, parse_events):
        self._last_process_measurements = {"stdout_bytes": 20}
        return 0, seen["version"] + "\n", "WARNING: proceeding, even though we could not create PATH aliases\n"

    monkeypatch.setattr(CodexCLIAdapter, "_run_process", run)
    for version in ("codex-cli 0.147.0", "codex-cli 0.163.0"):
        seen["version"] = version
        assert cli_identity.read_codex_identity(binary)["version"] == version
    seen["version"] = "codex-cli 0.164.0"
    with pytest.raises(ValueError, match="0.147.0 through 0.163.0"):
        cli_identity.read_codex_identity(binary)


def test_runtime_alias_drift_refreshes_without_accepting_a_new_executable(tmp_path, monkeypatch):
    from tests.test_cli_identity import _package_identity_fixture
    from devbench.study import cli_identity
    executable, metadata, runtime = _package_identity_fixture(tmp_path, monkeypatch)
    identity = cli_identity.read_codex_identity(executable)
    (runtime / "rg").write_bytes(b"changed helper")
    (runtime / "python").write_bytes(b"new path alias")
    updated, note = cli_identity.reconcile_codex_identity(identity)
    assert note["executable_unchanged"] is True
    assert note["inventory_changed"] is True
    assert updated["component_sha256"][str(runtime / "rg")] == hashlib.sha256(b"changed helper").hexdigest()
    assert str(runtime / "python") in updated["component_sha256"]
    with pytest.raises(ValueError, match="component changed|runtime inventory changed"):
        cli_identity.verify_codex_identity(identity)
    executable.write_bytes(b"replaced executable")
    with pytest.raises(ValueError, match="Pinned Codex executable component changed"):
        cli_identity.reconcile_codex_identity(identity)


def test_long_socket_path_is_shortened():
    from devbench.study.transport import UNIX_SOCKET_PATH_MAX, bounded_unix_socket
    preferred = Path("/" + ("workspace/" * 30) + "control/tools.sock")
    assert len(str(preferred).encode()) > UNIX_SOCKET_PATH_MAX
    path = bounded_unix_socket(preferred)
    assert len(str(path).encode()) <= UNIX_SOCKET_PATH_MAX
    assert path.parent.parent == Path(__file__).resolve().parents[1] / ".runs" / "s"


def test_socket_root_must_fit_without_platform_scratch_fallback(monkeypatch):
    from devbench.study.transport import bounded_unix_socket

    oversized = Path("/" + ("workspace/" * 30) + ".runs")
    monkeypatch.setenv("DEVBENCH_RUNS_ROOT", str(oversized))
    with pytest.raises(ValueError, match="unassigned_socket_path_too_long"):
        bounded_unix_socket(oversized / "control" / "tools.sock")


def test_agent_method_choice_is_executed_and_bound_roles_stay_bound(workspace):
    episode, _ = create(workspace)
    bound = episode.config["fixed_options"]["mean_condition_analysis"]["condition_col"]
    decision = {"method": "gaussian", "why": "Continuous input uses the fixed Gaussian mean contrast; BIC transport does not compare models.",
                "parameters": {"selection_criterion": "bic"}}
    rejected = episode.step({"kind": "tool", "tool": "mean_condition_analysis", "decision": {
        "method": decision["method"], "why": decision["why"],
        "parameters": {"condition_col": "not_the_declared_column", "selection_criterion": "bic"}}})
    assert rejected["status"] == "rejected"
    assert rejected["diagnostic"].startswith("bound_column_role_is_not_a_method_choice")
    episode.step({"kind": "revise", "reason": "Keep the declared condition column"})
    for tool in ("audit_expression_quality", "validate_biological_units"):
        assert episode.step({"kind": "tool", "tool": tool})["status"] == "succeeded"
    designed = episode.step({"kind": "tool", "tool": "validate_design_matrix", "decision": decision})
    assert designed["status"] == "succeeded", designed
    observed = episode.step({"kind": "tool", "tool": "mean_condition_analysis", "decision": decision})
    assert observed["status"] == "succeeded", observed
    fit = next(event for event in episode.broker.events if event["tool"] == "mean_condition_analysis")
    assert fit["arguments"]["options"]["selection_criterion"] == "bic"
    assert fit["arguments"]["options"]["condition_col"] == bound
