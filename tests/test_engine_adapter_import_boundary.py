"""Scientific adapter imports must not load benchmark evaluation machinery."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def isolated(code):
    process = subprocess.run([sys.executable, '-I', '-c',
        'import sys; sys.path.insert(0, ' + repr(str(ROOT)) + ');\n' + code],
        capture_output=True, text=True, timeout=20, cwd=ROOT)
    assert process.returncode == 0, process.stderr


def test_adapter_import_does_not_import_evaluation_runner():
    isolated('''
import importlib.abc
class NoEvaluation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'devbench_engine.runner' or fullname.startswith('devbench_engine.graders'):
            raise AssertionError('Scientific adapter imported evaluation machinery')
sys.meta_path.insert(0, NoEvaluation())
import devbench_engine.adapters
assert 'devbench_engine.runner' not in sys.modules
''')


def test_explicit_evaluation_exports_remain_compatible_without_inspecting_graders():
    isolated('''
import types
runner = types.ModuleType('devbench_engine.runner')
runner.EvalRunner, runner.EvalResult = object(), object()
sys.modules['devbench_engine.runner'] = runner
from devbench_engine import EvalRunner, EvalResult
assert EvalRunner is runner.EvalRunner and EvalResult is runner.EvalResult
''')
