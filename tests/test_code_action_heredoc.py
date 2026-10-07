"""Real local shell execution and evidence checks; no provider/model calls."""
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from devbench.adapters.code_action import CodeActionAdapter, script_invocation


def write_and_run(workspace, *, delimiter="'PY'", target=None, invocation=None):
    python = workspace / ".venv/bin/python"
    script = workspace / "solve.py"
    body = ("import json\nfrom pathlib import Path\n"
            "if __name__ == '__main__':\n"
            "    Path(__file__).with_name('final_discovery.json').write_text(json.dumps({'ok': True}))\n")
    return (f"cat > {shlex.quote(str(target or script))} <<{delimiter}\n"
            f"{body}PY\n{invocation or shlex.join([str(python), str(script)])}")


@pytest.mark.parametrize("delimiter", ["'PY'", '"PY"'])
@pytest.mark.parametrize("wrapped", [False, True])
def test_literal_write_and_run_captures_actual_execution_hashes(tmp_path, delimiter, wrapped):
    workspace = tmp_path / "task with spaces"
    workspace.mkdir()
    python = workspace / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    command = write_and_run(workspace, delimiter=delimiter)
    execution = subprocess.run(["/bin/sh", "-c", command], cwd=workspace,
                               capture_output=True, text=True, timeout=10)
    assert execution.returncode == 0, execution.stderr
    assert json.loads((workspace / "final_discovery.json").read_text()) == {"ok": True}
    recorded = shlex.join(["/bin/sh", "-c", command]) if wrapped else command
    adapter = CodeActionAdapter()
    adapter.workspace, adapter.python = workspace, python
    adapter._event({"type": "item.completed", "item": {
        "type": "command_execution", "command": recorded,
        "status": "completed", "exit_code": execution.returncode,
        "aggregated_output": execution.stdout}})
    adapter._observe_script_execution()
    assert adapter.metadata["script_execution_observed"]
    assert adapter.command_events[0]["script_sha256_at_completion"]
    assert adapter.command_events[0]["answer_sha256_at_completion"]


@pytest.mark.parametrize("artifact", ["solve.py", "final_discovery.json"])
def test_write_and_run_still_rejects_artifacts_changed_after_completion(tmp_path, artifact):
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    command = write_and_run(tmp_path)
    result = subprocess.run(["/bin/sh", "-c", command], cwd=tmp_path,
                            capture_output=True, timeout=10)
    assert result.returncode == 0
    adapter = CodeActionAdapter()
    adapter.workspace, adapter.python = tmp_path, python
    adapter._event({"type": "item.completed", "item": {
        "type": "command_execution", "command": command,
        "status": "completed", "exit_code": 0}})
    adapter._observe_script_execution()
    assert adapter.metadata["script_execution_observed"]
    (tmp_path / artifact).write_text("changed after execution")
    adapter._observe_script_execution()
    assert not adapter.metadata["script_execution_observed"]


@pytest.mark.parametrize("variant", [
    "echo", "inside_body", "unquoted", "missing_terminator", "wrong_target",
    "wrong_interpreter", "trailing_command", "masked_failure", "conditional",
    "background", "early_exit", "pipeline", "extra_prefix", "oversized",
])
def test_unproven_compound_forms_remain_rejected(tmp_path, variant):
    python = tmp_path / ".venv/bin/python"
    direct = shlex.join([str(python), str(tmp_path / "solve.py")])
    command = write_and_run(tmp_path)
    candidates = {
        "echo": "echo " + direct,
        "inside_body": f"cat > {shlex.quote(str(tmp_path / 'solve.py'))} <<'PY'\n{direct}\nPY",
        "unquoted": write_and_run(tmp_path, delimiter="PY"),
        "missing_terminator": command.replace("\nPY\n", "\n"),
        "wrong_target": write_and_run(tmp_path, target=tmp_path / "other.py"),
        "wrong_interpreter": write_and_run(tmp_path, invocation=shlex.join([sys.executable, str(tmp_path / "solve.py")])),
        "trailing_command": command + "; true",
        "masked_failure": command + " || true",
        "conditional": "if false; then\n" + command + "\nfi",
        "background": command + " &",
        "early_exit": "exit 0\n" + command,
        "pipeline": command + " | cat",
        "extra_prefix": "true; " + command,
        "oversized": " " * 16_001 + command,
    }
    assert not script_invocation(candidates[variant], tmp_path, python)


def test_python_body_is_literal_and_not_parsed_as_shell(tmp_path):
    command = write_and_run(tmp_path).replace("import json\n", "# an unmatched apostrophe ' and shell-looking $(false)\nimport json\n")
    assert script_invocation(command, tmp_path, tmp_path / ".venv/bin/python")


@pytest.mark.parametrize("false_terminator", ["\vPY", "\fPY", "\u2028PY", "PY\r", "PY\t"])
def test_only_exact_lf_delimited_heredoc_terminators_count(tmp_path, false_terminator):
    command = write_and_run(tmp_path).replace("\nPY\n", "\n" + false_terminator + "\n")
    assert not script_invocation(command, tmp_path, tmp_path / ".venv/bin/python")


@pytest.mark.parametrize("extra", ["; true", " && true", " > /dev/null", " <<'OTHER'"])
def test_additional_opener_syntax_is_not_treated_as_literal_write(tmp_path, extra):
    command = write_and_run(tmp_path).replace("<<'PY'\n", "<<'PY'" + extra + "\n")
    assert not script_invocation(command, tmp_path, tmp_path / ".venv/bin/python")


def test_nonzero_combined_execution_does_not_capture_success(tmp_path):
    (tmp_path / "solve.py").write_text("raise RuntimeError('failure')")
    (tmp_path / "final_discovery.json").write_text('{"stale":true}')
    adapter = CodeActionAdapter()
    adapter.workspace, adapter.python = tmp_path, tmp_path / ".venv/bin/python"
    adapter._event({"type": "item.completed", "item": {
        "type": "command_execution", "command": write_and_run(tmp_path),
        "status": "completed", "exit_code": 1}})
    adapter._observe_script_execution()
    assert not adapter.metadata["script_execution_observed"]
    assert adapter.command_events[0]["answer_sha256_at_completion"] is None
