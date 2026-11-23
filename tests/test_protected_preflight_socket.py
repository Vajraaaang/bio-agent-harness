"""Exercise the preflight listener through the real local Unix socket API."""

import os
import stat
import tempfile
from pathlib import Path
from socket import AF_UNIX, SOCK_STREAM, socket

from devbench.engine.workspace import (
    UNIX_SOCKET_PATH_MAX,
    bounded_unix_socket,
    validate_relocated_socket,
)
from devbench.study.exec_preflight import run_exec_preflight
from devbench.study.permissions import PermissionBoundary
from tests import test_exec_preflight
from tests.test_exec_preflight import fake_process

staged = test_exec_preflight.staged


def _long_workspace(staged, monkeypatch, *, oversized_root=False):
    adapter, output = staged
    root = adapter.workspace.parent
    actor = root / ("nested-" + "é" * 40)
    control = actor / "control"
    control.mkdir(mode=0o700, parents=True)
    adapter.boundary = PermissionBoundary(
        workspace=actor,
        private_roots=adapter.boundary.private_roots,
        protected_paths=(control,),
    )
    # Keep the coordinator's lease root out of deeply nested pytest workspaces.
    root_parent = root / ("r" * 100) if oversized_root else Path(__file__).resolve().parents[1]
    runs = root_parent / ".runs"
    monkeypatch.setenv("DEVBENCH_RUNS_ROOT", str(runs))
    monkeypatch.setattr("socket.socket", socket)

    mkdtemp = tempfile.mkdtemp

    def protect_temporary_storage(*args, **kwargs):
        if kwargs.get("prefix") == "dh-u-":
            raise ValueError("preflight_attempted_platform_scratch")
        return mkdtemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkdtemp", protect_temporary_storage)
    assert len(os.fsencode(control / "u-01234567")) > 105
    return adapter, output, runs


def test_long_exec_preflight_canary_binds_in_protected_storage(staged, monkeypatch):
    adapter, output, runs = _long_workspace(staged, monkeypatch)
    seen = []

    def connect_to_canary(_events, _script, config):
        path = Path(config["unassigned_socket"])
        preferred = adapter.boundary.workspace / "control" / (
            "u-" + config["nonce"][:8]
        )
        assert path == bounded_unix_socket(preferred)
        assert path.parent.parent == runs / "s"
        assert len(os.fsencode(path)) <= UNIX_SOCKET_PATH_MAX
        assert stat.S_ISSOCK(path.stat().st_mode)
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        with socket(AF_UNIX, SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(path))
        seen.append(path)

    calls = fake_process(monkeypatch, mutate=connect_to_canary)
    result = run_exec_preflight(adapter, output, allow_model_execution=True)
    assert result["verified"], result["validation_errors"]
    assert len(calls) == len(seen) == 1
    assert not seen[0].exists()
    assert not seen[0].parent.exists()


def test_long_exec_preflight_refuses_oversized_protected_root(staged, monkeypatch):
    adapter, output, runs = _long_workspace(staged, monkeypatch, oversized_root=True)
    calls = fake_process(monkeypatch)
    result = run_exec_preflight(adapter, output, allow_model_execution=True)
    assert not result["verified"]
    assert result["validation_errors"] == [
        "ValueError: unassigned_socket_path_too_long"
    ]
    assert not result["inference_dispatch_attempted"]
    assert not calls
    assert not runs.exists()


def test_long_exec_preflight_preserves_an_existing_socket_lease(staged, monkeypatch):
    adapter, output, _ = _long_workspace(staged, monkeypatch)
    nonce = "a" * 24
    monkeypatch.setattr("secrets.token_hex", lambda _: nonce)
    preferred = adapter.boundary.workspace / "control" / ("u-" + nonce[:8])
    path = bounded_unix_socket(preferred)
    validate_relocated_socket(path, create=True)
    calls = fake_process(monkeypatch)

    with socket(AF_UNIX, SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)
        try:
            result = run_exec_preflight(adapter, output, allow_model_execution=True)
            assert not result["verified"]
            error, = result["validation_errors"]
            assert error.startswith("FileExistsError:")
            assert not calls
            with socket(AF_UNIX, SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(path))
        finally:
            path.unlink()
            path.parent.rmdir()
