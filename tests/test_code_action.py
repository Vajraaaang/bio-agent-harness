"""Actual local Python execution plus fake-CLI transport contracts; no inference."""
import json
from pathlib import Path
import shlex
import subprocess
import shutil
import sys

import pytest

from devbench.adapters.code_action import CodeActionAdapter, script_invocation
from devbench.staging import stage_python, write_task_guidance
from devbench.runner import CodeActionRunner
from devbench.tasks import curated_tasks
from devbench.graders import grade_design_matrix_gate, grade_fdr_invariant


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "task"
    path.mkdir()
    stage_python(path)
    (path / "answer_schema.json").write_text('{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}')
    return path


def fake_cli(tmp_path, body):
    file = tmp_path / "fake-codex"
    file.write_text("#!" + sys.executable + "\nimport sys,json,subprocess,shlex\nfrom pathlib import Path\n"
                    "if sys.argv[1:]==['login','status']:\n print('Logged in using ChatGPT');sys.exit(0)\n"
                    "work=Path.cwd(); prompt=sys.stdin.read()\n" + body)
    file.chmod(0o700)
    return str(file)


def test_staged_interpreter_excludes_coordinator_and_editable_hooks(workspace):
    site = next((workspace / ".venv/lib").glob("python*/site-packages"))
    assert not list(site.glob("*.pth"))
    assert not list(site.glob("__editable__*"))
    code = "import devharness,importlib.util; assert importlib.util.find_spec('devbench_engine') is None; assert importlib.util.find_spec('benchmarks') is None; assert importlib.util.find_spec('scripts') is None; print(devharness.__file__)"
    result = subprocess.run([str(workspace / ".venv/bin/python"), "-I", "-c", code], cwd=workspace,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert str(workspace) in result.stdout


def test_real_solve_script_runs_and_evidence_is_observed(tmp_path, workspace):
    executable = fake_cli(tmp_path, '''
assert '--approve-for-me' in sys.argv
assert '--sandbox' not in sys.argv  # --approve-for-me selects workspace-write itself
assert not any('mcp_servers' in arg or 'dangerously' in arg for arg in sys.argv)
(work/'solve.py').write_text("import devharness as dh\\nif __name__ == '__main__':\\n    dh.save_discovery(ok=True)\\n")
cmd=[str(work/'.venv/bin/python'),str(work/'solve.py')]
r=subprocess.run(cmd,capture_output=True,text=True)
print(json.dumps({'type':'thread.started','thread_id':'fake-session'}))
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':shlex.join(cmd),'status':'completed','exit_code':r.returncode,'aggregated_output':r.stdout+r.stderr}}))
print(json.dumps({'type':'turn.completed','model':'test-model','usage':{'input_tokens':12,'output_tokens':3}}))
''')
    adapter = CodeActionAdapter(executable, timeout_sec=20)
    assert adapter.execute("test", workspace) == {"ok": True}
    assert adapter.metadata["script_execution_observed"]
    assert adapter.metadata["tokens"] == 15
    assert adapter.metadata["actual_model"] == "test-model"
    assert adapter.metadata["mcp_attempts"] == 0


def test_file_existence_and_echo_do_not_prove_execution(tmp_path, workspace):
    executable = fake_cli(tmp_path, '''
(work/'solve.py').write_text('print("not executed")')
(work/'final_discovery.json').write_text('{"ok":true}')
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'echo python solve.py','status':'completed','exit_code':0}}))
''')
    adapter = CodeActionAdapter(executable, timeout_sec=20)
    with pytest.raises(RuntimeError, match="No successful direct"):
        adapter.execute("test", workspace)
    assert (workspace / "final_discovery.json").is_file(), "Failure artifacts must be retained"
    assert not adapter.metadata["script_execution_observed"]


def test_command_parser_requires_staged_interpreter_and_direct_script(tmp_path):
    python = tmp_path / ".venv/bin/python"
    command = shlex.join([str(python), str(tmp_path / "solve.py")])
    assert script_invocation(command, tmp_path, python)
    assert script_invocation(shlex.join(["/bin/zsh", "-lc", command]), tmp_path, python)
    assert script_invocation(shlex.join(["cd", str(tmp_path)]) + " && " + command, tmp_path, python)
    assert not script_invocation(command + " || true", tmp_path, python)
    assert not script_invocation("echo " + command, tmp_path, python)
    assert not script_invocation(shlex.join([sys.executable, str(tmp_path / "solve.py")]), tmp_path, python)


@pytest.mark.skipif(shutil.which("codex") is None, reason="Installed Codex CLI unavailable for parser-only check")
def test_installed_cli_accepts_flags_before_missing_cwd_rejection(tmp_path):
    adapter = CodeActionAdapter()
    adapter._schema_enforcement = "local"
    missing = tmp_path / "intentionally-missing"
    command = adapter._command(shutil.which("codex"), missing, missing / "schema.json", tmp_path / "last.txt", None)
    result = subprocess.run(command, input="", capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "cannot be used with" not in result.stderr
    assert "No such file or directory" in result.stderr
    assert "thread.started" not in result.stdout, "Parser test must never launch a model session"


def test_guidance_uses_real_budget_and_no_confounding_shortcut(workspace):
    write_task_guidance(workspace, "Analyze provided inputs", str(workspace / ".venv/bin/python"), assisted=True, timeout_sec=60)
    text = (workspace / "TASK_README.md").read_text()
    assert "60 seconds" in text and "90 seconds" not in text
    assert "remains unidentifiable" in text
    assert "private reference answers" in text


def test_catalog_is_twelve_unique_unchanged_definitions():
    tasks = curated_tasks()
    assert len(tasks) == len({path for _, path in tasks}) == 12
    assert {track for track, _ in curated_tasks(4)} == {"trajectory", "batch", "lineage", "adversarial"}
    assert all(path.exists() for _, path in tasks)
    with pytest.raises(ValueError):
        curated_tasks(0)


def test_explicit_mathematical_gates():
    assert not grade_design_matrix_gate({"condition_number":1001})["passed"]
    assert grade_design_matrix_gate({"condition_number":1001,"collinearity_diagnosed":True})["passed"]
    assert not grade_fdr_invariant({"gene_statistics":{"x":{"pvalue":.1,"qvalue":.01}}})["passed"]


def test_runner_retains_failed_artifacts_and_rejects_private_staging(tmp_path, monkeypatch):
    spec = {"task_id":"private", "instruction":"private task", "metadata":{"source_kind":"private"},
            "grader":{"type":"equals","field":"ok","expected":True}}
    file = tmp_path / "task.json"; file.write_text(json.dumps(spec))
    runner = CodeActionRunner(file)
    class Adapter:
        timeout_sec = 60
        model = None
        metadata = {}
        def execute(self, *args):
            pytest.fail("Private data reached model")
    monkeypatch.setattr(runner.legacy, "_stage", lambda work, **kwargs: {"metadata":{"source_kind":"private"}})
    result = runner.run(Adapter(), tmp_path / "result")
    assert not result["passed"]
    assert "Only declared synthetic/public" in result["metadata"]["error"]


def test_failed_script_remains_unaccepted(tmp_path, workspace):
    executable = fake_cli(tmp_path, '''
(work/'solve.py').write_text('raise RuntimeError("failure")')
cmd=[str(work/'.venv/bin/python'),str(work/'solve.py')]
r=subprocess.run(cmd,capture_output=True,text=True)
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':shlex.join(cmd),'status':'completed','exit_code':r.returncode,'aggregated_output':r.stderr}}))
''')
    adapter = CodeActionAdapter(executable, timeout_sec=20)
    with pytest.raises(RuntimeError, match="No successful direct"):
        adapter.execute("test", workspace)
    assert not adapter.metadata["commands"][0]["succeeded"]


def test_runner_grades_an_executed_script_and_retains_audited_exports(tmp_path):
    spec = {"task_id":"local_execution_control", "instruction":"Compute an increasing stage order", "metadata":{"source_kind":"synthetic"},
            "answer_schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]},
            "grader":{"type":"equals","field":"ok","expected":True}}
    path = tmp_path / "task.json"; path.write_text(json.dumps(spec))
    executable = fake_cli(tmp_path, '''
(work/'solve.py').write_text("import devharness as dh\\nif __name__ == '__main__':\\n    dh.save_discovery(ok=True)\\n")
cmd=[str(work/'.venv/bin/python'),str(work/'solve.py')]
r=subprocess.run(cmd,capture_output=True,text=True)
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':shlex.join(cmd),'status':'completed','exit_code':r.returncode,'aggregated_output':r.stdout+r.stderr}}))
''')
    result = CodeActionRunner(path).run(CodeActionAdapter(executable, timeout_sec=30), tmp_path / "result", tmp_path / "persistent-work")
    assert result["passed"] and result["metadata"]["accepted_final"]
    assert result["metadata"]["script_execution_observed"]
    assert (tmp_path / "result/solve.py").exists()
    assert (tmp_path / "persistent-work/counts.zarr").exists() is False
    assert result["metadata"]["audit"]["root_hash"]
    assert result["metadata"]["tool_calls"] == []


def test_declared_input_mutation_is_preserved_and_rejected(tmp_path):
    spec = {"task_id":"input_mutation_control", "instruction":"Preserve inputs", "metadata":{"source_kind":"synthetic"},
            "grader":{"type":"equals","field":"ok","expected":True}}
    path = tmp_path / "task.json";path.write_text(json.dumps(spec))
    executable = fake_cli(tmp_path, '''
(work/'solve.py').write_text("import json\\nfrom pathlib import Path\\nPath('answer_schema.json').write_text('{}')\\nPath('final_discovery.json').write_text('{\\\"ok\\\":true}')\\n")
cmd=[str(work/'.venv/bin/python'),str(work/'solve.py')]
r=subprocess.run(cmd,capture_output=True,text=True)
print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':shlex.join(cmd),'status':'completed','exit_code':r.returncode,'aggregated_output':r.stdout+r.stderr}}))
''')
    result = CodeActionRunner(path).run(CodeActionAdapter(executable, timeout_sec=30), tmp_path / "result")
    assert result["metadata"]["failure_type"] == "input_integrity_error"
    assert not result["passed"]
    assert (tmp_path / "result/final_discovery.json").is_file()
