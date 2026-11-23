"""Shared runtime checks and protected test workspaces."""

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    if config.getoption("collectonly"):
        return
    wal_tests = [item for item in items if item.get_closest_marker("wal") is not None]
    if not wal_tests:
        return

    from devbench.engine.scientific_authority import require_patched_wal
    from devbench.engine.workflow_operations import OperationConflict

    try:
        require_patched_wal()
    except OperationConflict as error:
        reason = f"{error}. Loaded SQLite {sqlite3.sqlite_version}."
        skip_flag = "DEVBENCH_SKIP_UNPATCHED_WAL_TESTS"
        if os.environ.get(skip_flag) != "1":
            raise pytest.UsageError(
                f"{reason}\nRun `make test` to use the verified runtime. "
                f"For a non-WAL run, set {skip_flag}=1 to skip @wal tests explicitly."
            ) from error
        skip = pytest.mark.skip(reason=f"{skip_flag}=1. {reason}")
        for item in wal_tests:
            item.add_marker(skip)


@contextmanager
def _workspace(config):
    from devbench.engine.permissions import require_protected_storage

    # An evidence-collection plugin may explicitly allocate and retain fixtures.
    # Ordinary pytest runs always clean up their own workspace.
    allocate = getattr(config, "_devbench_retained_workspace_factory", None)
    if allocate is not None:
        with allocate() as path:
            yield require_protected_storage(path)
        return

    parent = Path(__file__).resolve().parents[1] / ".pytest-workspaces"
    if parent.is_symlink():
        raise ValueError("Protected test workspace root cannot be a symlink")
    parent.mkdir(mode=0o700, exist_ok=True)
    parent = require_protected_storage(parent)
    with tempfile.TemporaryDirectory(prefix="", dir=parent) as directory:
        yield Path(directory)


@pytest.fixture
def workspace_tmp_path(pytestconfig):
    """An isolated, auto-cleaned path outside shared OS scratch."""
    with _workspace(pytestconfig) as path:
        yield path


@pytest.fixture(scope="module")
def module_workspace_tmp_path(pytestconfig):
    with _workspace(pytestconfig) as path:
        yield path


@pytest.fixture
def installed_hosted_wheel():
    """Require installed module origin for tests whose product path checks the wheel RECORD."""
    from devbench.study import rna_hosted_qualification

    module_path = Path(rna_hosted_qualification.__file__).resolve()
    if not module_path.is_relative_to(Path(sys.prefix).resolve()):
        pytest.skip("Requires a non-editable installed devharness wheel for hosted identity verification")
