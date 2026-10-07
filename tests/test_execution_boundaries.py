"""Execution-boundary regressions with real local shells and no provider calls."""
import hashlib
import json
from pathlib import Path
import shlex
import sys

import pytest

from devbench.adapters.code_action import CodeActionAdapter, script_invocation


def fixture_workspace(path):
    path.mkdir()
    python = path / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (path / 'answer_schema.json').write_text(json.dumps({
        'type': 'object', 'properties': {'version': {'type': 'integer'}},
        'required': ['version'], 'additionalProperties': False}))
    return python


def fixture_cli(path, commands):
    path.write_text('#!' + sys.executable + '\n' +
        "import json,subprocess,sys\n"
        "if sys.argv[1:]==['login','status']:\n print('Logged in using ChatGPT');sys.exit(0)\n"
        "sys.stdin.read()\n" +
        'commands=' + repr(commands) + '\n' +
        "for i,cmd in enumerate(commands):\n"
        " item={'id':f'fixture-{i}','type':'command_execution','command':cmd,'status':'in_progress'}\n"
        " print(json.dumps({'type':'item.started','item':item}),flush=True)\n"
        " r=subprocess.run(cmd,shell=True,executable='/bin/sh',capture_output=True,text=True)\n"
        " item.update(status='completed',exit_code=r.returncode,aggregated_output=r.stdout+r.stderr)\n"
        " print(json.dumps({'type':'item.completed','item':item}),flush=True)\n"
        "print(json.dumps({'type':'turn.completed','model':'offline-fixture','usage':{'input_tokens':0,'output_tokens':0}}),flush=True)\n")
    path.chmod(0o700)
    return str(path)


def test_actual_relative_heredoc_then_python_edit_and_rerun(tmp_path):
    work = tmp_path / 'work'
    python = fixture_workspace(work)
    run = shlex.join([str(python), str(work / 'solve.py')])
    body = "from pathlib import Path\nPath('final_discovery.json').write_text('{\"version\":1}')\n"
    first = "cat > solve.py <<'PY'\n" + body + 'PY\n' + run
    edit = (shlex.quote(str(python)) + " - <<'PY'\nfrom pathlib import Path\n"
            "p=Path('solve.py');p.write_text(p.read_text().replace('version\\\":1','version\\\":2'))\nPY\n" + run)
    cli = fixture_cli(tmp_path / 'fixture-codex', [first, edit])
    adapter = CodeActionAdapter(cli, timeout_sec=20)
    assert adapter.execute('offline boundary fixture', work) == {'version': 2}
    assert adapter.metadata['script_execution_observed']
    assert len(adapter.metadata['script_executions']) == 2
    assert adapter.metadata['final_execution_id'] == adapter.metadata['script_executions'][-1]['execution_id']


def test_binding_never_falls_back_to_an_older_success(tmp_path):
    python = fixture_workspace(tmp_path / 'work')
    work = python.parents[2]
    (work / 'solve.py').write_text('first')
    (work / 'final_discovery.json').write_text('{"version":1}')
    hash_file = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    first = {'direct_script_invocation': True, 'succeeded': True,
             'script_sha256_at_completion': hash_file(work / 'solve.py'),
             'answer_sha256_at_completion': hash_file(work / 'final_discovery.json')}
    second = {**first, 'script_sha256_at_completion': 'b' * 64,
              'answer_sha256_at_completion': 'c' * 64}
    adapter = CodeActionAdapter()
    adapter.workspace, adapter.python = work, python
    adapter.command_events = [first, second]
    adapter._observe_script_execution()
    assert not adapter.metadata['script_execution_observed']


@pytest.mark.parametrize('prefix', [
    "cat > solve.py <<'PY'\nprint('literal')\nPY\n",
    "sed -i '' 's/first/second/' solve.py\n",
    "{python} - <<'PY'\nprint('literal source, not shell')\nPY\n",
])
def test_observed_editing_forms_end_in_direct_execution(tmp_path, prefix):
    python = tmp_path / '.venv/bin/python'
    command = prefix.format(python=shlex.quote(str(python))) + shlex.join([str(python), str(tmp_path / 'solve.py')])
    assert script_invocation(shlex.join(['/bin/zsh', '-lc', command]), tmp_path, python)


@pytest.mark.parametrize('pattern', [
    'exit 0; {run}', 'false && {run}', '{run} || true', '{run} &',
    'if false; then {run}; fi', 'echo "{run}"', '{run} | cat',
    'trap "exit 0" EXIT; {run}', 'eval "{run}"',
])
def test_control_flow_never_supplies_execution_proof(tmp_path, pattern):
    python = tmp_path / '.venv/bin/python'
    command = pattern.format(run=shlex.join([str(python), str(tmp_path / 'solve.py')]))
    assert not script_invocation(command, tmp_path, python)


def test_cli_command_cannot_change_after_its_observed_start(tmp_path):
    work = tmp_path / 'work'
    python = fixture_workspace(work)
    adapter = CodeActionAdapter()
    adapter.workspace, adapter.python = work, python
    item = {'id':'same-item','type':'command_execution','status':'in_progress',
            'command':shlex.join([str(python),str(work/'solve.py')])}
    adapter._event({'type':'item.started','item':item})
    changed = {**item, 'command':'echo fabricated', 'status':'completed','exit_code':0}
    with pytest.raises(RuntimeError, match='changed'):
        adapter._event({'type':'item.completed','item':changed})
