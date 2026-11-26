"""Shared runtime checks and protected test workspaces."""

import http.client
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


@pytest.fixture(autouse=True)
def _legacy_workbench_clients(request):
    """Enable authenticated clients for the existing authorized route suites."""
    route_suites = {
        "test_bulk_plot_workbench",
        "test_evidence_disclosure_reservations",
        "test_plugin_graph_studio",
        "test_plugin_mcp_server",
        "test_scientific_plot_http_closeout",
        "test_scientific_publication_policy",
        "test_scientific_workspace_http",
        "test_scientific_workspace_ui",
        "test_thesis_workbench_acceptance",
        "test_workbench_descriptive_ui",
        "test_workbench_input_import",
        "test_workbench_local_analysis",
        "test_workbench_local_analyst",
        "test_workbench_operator_runtime",
        "test_workbench_project_http",
        "test_workbench_pseudobulk",
        "test_workbench_question_intake",
        "test_workbench_shared_motor_umap_ui",
        "test_workbench_ui",
        "test_workbench_ui_organization",
    }
    if request.node.path.stem in route_suites:
        request.getfixturevalue("authenticated_workbench_client")


@pytest.fixture
def authenticated_workbench_client(monkeypatch):
    """Supply a bearer for a test-owned listener when the caller omits one."""
    from devbench.platform import workbench_http

    listeners = {}
    explicit_auth = set()
    make_server = workbench_http.make_workbench_server
    putrequest = http.client.HTTPConnection.putrequest
    putheader = http.client.HTTPConnection.putheader
    endheaders = http.client.HTTPConnection.endheaders

    def bind(*args, **kwargs):
        server = make_server(*args, **kwargs)
        listeners[server.server_address] = server
        return server

    def begin(connection, *args, **kwargs):
        explicit_auth.discard(connection)
        return putrequest(connection, *args, **kwargs)

    def header(connection, name, *values):
        putheader(connection, name, *values)
        if name.lower() in ("authorization", b"authorization"):
            explicit_auth.add(connection)

    def finish(connection, *args, **kwargs):
        server = listeners.get((connection.host, connection.port))
        if (
            connection not in explicit_auth
            and server is not None
            and server.fileno() >= 0
        ):
            putheader(connection, "Authorization", f"Bearer {server.daemon_token}")
        explicit_auth.discard(connection)
        return endheaders(connection, *args, **kwargs)

    monkeypatch.setattr(workbench_http, "make_workbench_server", bind)
    monkeypatch.setattr(http.client.HTTPConnection, "putrequest", begin)
    monkeypatch.setattr(http.client.HTTPConnection, "putheader", header)
    monkeypatch.setattr(http.client.HTTPConnection, "endheaders", finish)


@pytest.fixture
def installed_hosted_wheel():
    """Require installed module origin when the product checks its wheel RECORD."""
    from devbench.analysis import rna_trace_contract

    module_path = Path(rna_trace_contract.__file__).resolve()
    if not module_path.is_relative_to(Path(sys.prefix).resolve()):
        pytest.skip(
            "Runtime identity verification requires a non-editable devharness wheel"
        )
