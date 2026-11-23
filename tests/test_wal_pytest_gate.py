"""Exercise the scientific WAL gate through direct pytest collection."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "version,skip,selection,exit_code,summary",
    [
        (
            (3, 45, 3), None, (), pytest.ExitCode.USAGE_ERROR,
            "Scientific WAL requires patched SQLite",
        ),
        ((3, 45, 3), "1", (), pytest.ExitCode.OK, "1 passed, 1 skipped"),
        (
            (3, 45, 3), None, ("-m", "not wal"), pytest.ExitCode.OK,
            "1 passed, 1 deselected",
        ),
        pytest.param(
            (3, 50, 7), "1", (), pytest.ExitCode.OK, "2 passed", marks=pytest.mark.wal,
        ),
    ],
)
def test_direct_pytest_wal_gate(tmp_path, version, skip, selection, exit_code, summary):
    repository = Path(__file__).resolve().parents[1]
    entrypoint = repository / "tests" / "test_adaptive_entrypoint_execution.py"
    wal_test = "test_adaptive_entrypoint_inserts_step_and_keeps_independent_work_ready"
    non_wal_test = "test_workflow_cli_requires_fresh_execution_authority"
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    skip_flag = "DEVBENCH_SKIP_UNPATCHED_WAL_TESTS"
    environment.pop(skip_flag, None)
    if skip is not None:
        environment[skip_flag] = skip
    script = f"""
import sqlite3
import sys
import pytest

sqlite3.sqlite_version_info = {version!r}
sqlite3.sqlite_version = {'.'.join(map(str, version))!r}
sys.exit(pytest.main(sys.argv[1:]))
"""
    result = subprocess.run(
        [
            sys.executable, "-c", script, "-q", "-p", "no:cacheprovider",
            "--import-mode=importlib", "--basetemp", str(tmp_path / "pytest"),
            *selection, f"{entrypoint}::{wal_test}", f"{entrypoint}::{non_wal_test}",
        ],
        check=False, cwd=repository, env=environment,
        capture_output=True, text=True, timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == exit_code, output
    assert summary in output, output
