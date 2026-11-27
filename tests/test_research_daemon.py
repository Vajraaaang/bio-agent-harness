"""Exercise the local daemon across its CLI, socket, HTTP and worker boundaries."""

import asyncio
import concurrent.futures
import fcntl
import hashlib
import http.client
import json
import os
import re
import shlex
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, asynccontextmanager, closing, contextmanager, suppress
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from uuid import uuid4

import psutil
import pytest

pytestmark = pytest.mark.wal


def _until(observe, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result := observe():
            return result
        time.sleep(0.04)
    raise AssertionError(f"Timed out waiting for {observe.__name__}")


def _alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _json(path):
    return json.loads(path.read_text())


def _stop_when_idle(workspace):
    from devbench.daemon import ipc

    def stopped():
        try:
            return ipc.stop_daemon(workspace)["status"] == "stopped"
        except ipc.DaemonError as error:
            if str(error) != "daemon_busy":
                raise
            return False

    # A worker may have published its receipt just before its group exits.
    _until(stopped, timeout=60)


@contextmanager
def _daemon(root, *, offline_only=True, **options):
    from devbench.daemon import ipc

    inputs = root / "inputs"
    inputs.mkdir(mode=0o700)
    workspace = root / "workspace"
    try:
        metadata = ipc.start_daemon(
            workspace, input_root=inputs, offline_only=offline_only, **options
        )
        yield workspace, inputs, metadata
    finally:
        _stop_when_idle(workspace)


def _freeze(store, classification="synthetic", **options):
    plan = {
        "session_id": uuid4().hex,
        "plan_id": uuid4().hex,
        "data_classification": classification,
        **options,
    }
    plan_id, sha = store.freeze(plan)
    return {"session_id": plan["session_id"], "plan_id": plan_id, "plan_sha256": sha}


def _authority(workspace):
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger

    return WorkflowOperationLedger(
        workspace / "methods" / LEDGER_NAME, scientific=True
    ).scientific


def _http(url, path, *, headers=None, method="GET", body=None):
    parsed = urlsplit(url)
    assert parsed.hostname == "127.0.0.1"
    conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def _cli(*arguments, check=True):
    result = subprocess.run(
        [sys.executable, "-m", "devbench.platform.cli", *map(str, arguments)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def test_concurrent_bootstrap_and_hashed_socket(workspace_tmp_path):
    from devbench.daemon import ipc

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    workspace = workspace_tmp_path / ("long-workspace-" * 7)
    state = workspace / ".devbench-daemon"
    assert len(os.fsencode(state / "daemon.sock")) > 96

    def start():
        return json.loads(
            _cli(
                "daemon",
                "start",
                "--workspace",
                workspace,
                "--input-root",
                inputs,
                "--http-port",
                "0",
                "--offline-only",
            ).stdout
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            launches = [executor.submit(start) for _ in range(3)]
            results = [future.result() for future in launches]
        metadata = json.loads((state / "daemon.json").read_text())
        assert {result["pid"] for result in results} == {metadata["pid"]}
        assert psutil.Process(metadata["pid"]).create_time() == pytest.approx(
            metadata["create_time"], rel=0, abs=0.01
        )
        assert metadata["started_at_utc"] and metadata["idle_timeout_sec"] == 1800
        sock = Path(metadata["socket_path"])
        expected_hash = hashlib.sha256(os.fsencode(workspace.resolve())).hexdigest()[
            :16
        ]
        assert sock.name == f"s-{expected_hash}.sock"
        assert sock.parent.name == f"devbench-ipc-{os.getuid()}"
        assert len(os.fsencode(sock)) < 104
        assert stat.S_ISSOCK(sock.lstat().st_mode)
        for directory in (state, sock.parent):
            assert stat.S_ISDIR(directory.lstat().st_mode)
            assert directory.stat().st_uid == os.getuid()
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        for path in (
            sock,
            state / "daemon.lock",
            state / "daemon.token",
            state / "daemon.json",
        ):
            assert stat.S_IMODE(path.lstat().st_mode) == 0o600
            assert path.stat().st_uid == os.getuid()
        secret = (state / "daemon.token").read_text().strip()
        assert re.fullmatch(r"[0-9a-f]{64}", secret)
        assert all(secret not in json.dumps(item) for item in [metadata, *results])
        status = json.loads(_cli("daemon", "status", "--workspace", workspace).stdout)
        assert status["pid"] == metadata["pid"]
    finally:
        ipc.stop_daemon(workspace)


def test_reap_stale_socket_after_crash_and_pid_reuse(workspace_tmp_path):
    from devbench.daemon import ipc

    with _daemon(workspace_tmp_path) as (workspace, inputs, first):
        state = workspace / ".devbench-daemon"
        first_token = (state / "daemon.token").read_bytes()
        process = psutil.Process(first["pid"])
        process.kill()
        _until(lambda: not _alive(process))
        assert Path(first["socket_path"]).is_socket()
        with subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        ) as bystander:
            try:
                # Model a recycled PID without ever choosing an unrelated host process.
                marker = _json(state / "daemon.json")
                marker.update(
                    pid=bystander.pid,
                    create_time=psutil.Process(bystander.pid).create_time() - 100,
                )
                (state / "daemon.json").write_text(json.dumps(marker))
                second = ipc.start_daemon(
                    workspace, input_root=inputs, offline_only=True
                )
                assert second["pid"] != bystander.pid
                assert bystander.poll() is None
                assert second["socket_path"] == first["socket_path"]
                assert (state / "daemon.token").read_bytes() != first_token
                assert ipc.request(workspace, "status")["pid"] == second["pid"]
            finally:
                bystander.terminate()


@contextmanager
def _listener_lock(workspace):
    from devbench.daemon import auth

    with auth.state_directory(workspace, create=True) as directory:
        fd = os.open("daemon.lock", os.O_CREAT | os.O_RDWR, 0o600, dir_fd=directory)
        try:
            yield fd
        finally:
            os.close(fd)


def test_listener_binds_owner_only_without_nofollow_chmod(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import ipc

    workspace = workspace_tmp_path / "workspace"
    bind, chmod = socket.socket.bind, os.chmod
    modes = []
    address = ipc.socket_path(workspace)

    def observe_bind(listener, path):
        bind(listener, path)
        modes.append(stat.S_IMODE(os.lstat(path).st_mode))

    def linux_chmod(path, mode, **options):
        if not options.get("follow_symlinks", True):
            raise NotImplementedError("chmod does not support follow_symlinks=False")
        return chmod(path, mode, **options)

    monkeypatch.setattr(socket.socket, "bind", observe_bind)
    monkeypatch.setattr(
        os, "supports_follow_symlinks", os.supports_follow_symlinks - {chmod}
    )
    monkeypatch.setattr(os, "chmod", linux_chmod)
    mask = os.umask(0o022)
    try:
        with (
            _listener_lock(workspace) as fd,
            ipc.daemon_listener(workspace, fd) as listener,
        ):
            _, address, _ = listener
            assert modes == [0o600] and stat.S_ISSOCK(address.lstat().st_mode)
            assert os.umask(0o022) == 0o022
        assert not address.exists()
    finally:
        os.umask(mask)
        address.unlink(missing_ok=True)


@pytest.mark.parametrize("replaced", [False, True], ids=["bound-inode", "replacement"])
def test_listener_initialization_failure_only_unlinks_its_bound_inode(
    workspace_tmp_path, monkeypatch, replaced
):
    from devbench.daemon import auth, ipc

    workspace = workspace_tmp_path / "workspace"
    with _listener_lock(workspace) as fd, socket.socket(socket.AF_UNIX) as substitute:
        address = ipc.socket_path(workspace)

        def fail_publication(*_args):
            if replaced:
                address.unlink()
                substitute.bind(str(address))
                address.chmod(0o600)
            else:
                # The listener still owns this inode after an incomplete setup.
                address.chmod(0o700)
            raise OSError("interrupted token publication")

        monkeypatch.setattr(auth, "write_file", fail_publication)
        try:
            with pytest.raises(OSError, match="interrupted token publication"):
                with ipc.daemon_listener(workspace, fd):
                    pytest.fail("listener unexpectedly advertised itself")
            assert address.is_socket() is replaced
        finally:
            address.unlink(missing_ok=True)


@pytest.mark.parametrize("recover", ["stop", "start"])
def test_inaccessible_recycled_pid_is_reaped_only_without_a_lifetime_owner(
    workspace_tmp_path, monkeypatch, recover
):
    from devbench.daemon import auth, ipc

    workspace = workspace_tmp_path / "workspace"
    children = []
    spawn = ipc._spawn_daemon

    def launch(*args):
        child = spawn(*args)
        children.append(child)
        return child

    monkeypatch.setattr(ipc, "_spawn_daemon", launch)
    with (
        _listener_lock(workspace) as fd,
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"]
        ) as bystander,
    ):
        address = ipc.socket_path(workspace)
        try:
            with socket.socket(socket.AF_UNIX) as stale:
                stale.bind(str(address))
                address.chmod(0o700)  # An incomplete earlier bind.
            with auth.state_directory(workspace) as directory:
                marker = {
                    "pid": bystander.pid,
                    "create_time": psutil.Process(bystander.pid).create_time() - 10,
                    "workspace": str(workspace),
                    "http_url": "http://127.0.0.1:12345/",
                    "socket_path": str(address),
                }
                auth.write_file(directory, "daemon.json", json.dumps(marker).encode())
                auth.write_file(directory, "daemon.token", b"a" * 64)
            inspect = psutil.Process

            def protected(pid=None):
                if pid == bystander.pid:
                    raise psutil.AccessDenied(pid)
                return inspect(pid)

            monkeypatch.setattr(psutil, "Process", protected)
            assert ipc.daemon_status(workspace)["status"] == "stopped"
            fcntl.flock(fd, fcntl.LOCK_EX)
            with pytest.raises(ipc.DaemonError, match="daemon_identity_unavailable"):
                ipc.daemon_status(workspace)
            assert address.is_socket()
            fcntl.flock(fd, fcntl.LOCK_UN)
            if recover == "start":
                fresh = ipc.start_daemon(workspace, idle_timeout_sec=10)
                assert fresh["pid"] == children[0].pid != bystander.pid
                assert ipc.request(workspace, "status")["pid"] == fresh["pid"]
            assert ipc.stop_daemon(workspace)["status"] == "stopped"
            for name in ("daemon.json", "daemon.token"):
                assert not (workspace / auth.STATE_DIR / name).exists()
            assert not address.exists() and bystander.poll() is None
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
            address.unlink(missing_ok=True)
            bystander.terminate()


@pytest.mark.parametrize("link", ["directory", "lock"])
def test_rejects_symlinks_in_daemon_control_state(workspace_tmp_path, link):
    from devbench.daemon import ipc

    workspace = workspace_tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    state = workspace / ".devbench-daemon"
    target = workspace_tmp_path / "unrelated"
    target.mkdir(mode=0o700)
    marker = target / "marker"
    marker.write_text("keep")
    marker.chmod(0o600)
    if link == "directory":
        state.symlink_to(target, target_is_directory=True)
    else:
        state.mkdir(mode=0o700)
        (state / "daemon.lock").symlink_to(marker)
    with pytest.raises((OSError, ValueError, RuntimeError)):
        ipc.start_daemon(workspace)
    assert sorted(target.iterdir()) == [marker]
    assert marker.read_text() == "keep"


def test_runtime_fallback_requires_owned_nofollow_directory(workspace_tmp_path):
    from devbench.daemon.ipc import _runtime_directory

    runtime = workspace_tmp_path / "runtime"
    runtime.mkdir(mode=0o700)
    protected = runtime / f"devbench-ipc-{os.getuid()}"
    target = workspace_tmp_path / "unrelated"
    target.mkdir(mode=0o700)
    protected.symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        _runtime_directory(runtime)
    assert not list(target.iterdir())
    protected.unlink()
    protected.mkdir(mode=0o700)
    protected.chmod(0o755)
    with pytest.raises(PermissionError):
        _runtime_directory(runtime)
    protected.chmod(0o700)
    assert _runtime_directory(runtime) == protected


def test_macos_sqlite_bootstrap_preserves_caller_environment(monkeypatch):
    from devbench.daemon import ipc
    from devbench.engine import scientific_authority
    from devbench.engine.workflow_operations import OperationConflict

    library = Path("/opt/homebrew/opt/sqlite/lib/libsqlite3.dylib")
    monkeypatch.setattr(ipc, "sys", SimpleNamespace(platform="darwin"))
    check = Mock()
    monkeypatch.setattr(scientific_authority, "require_patched_wal", check)
    monkeypatch.setattr(Path, "is_file", lambda path: path == library)
    inherited = os.environ.get("DYLD_LIBRARY_PATH")
    child = {}
    ipc._macos_sqlite_environment(child)
    assert child == {}
    check.side_effect = OperationConflict("unpatched SQLite")
    monkeypatch.setattr(ipc.sysconfig, "get_config_var", lambda _key: 1)
    with pytest.raises(OperationConflict, match="uv python install"):
        ipc._macos_sqlite_environment(child)
    assert not child
    monkeypatch.setattr(ipc.sysconfig, "get_config_var", lambda _key: None)
    ipc._macos_sqlite_environment(child)
    assert child == {"DYLD_LIBRARY_PATH": str(library.parent)}
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        choice = {key: "/configured/sqlite/lib"}
        ipc._macos_sqlite_environment(choice)
        assert choice == {key: "/configured/sqlite/lib"}
    assert check.call_count == 3 and os.environ.get("DYLD_LIBRARY_PATH") == inherited
    monkeypatch.setattr(Path, "is_file", lambda path: False)
    with pytest.raises(OperationConflict):
        ipc._macos_sqlite_environment({})


def test_idle_shutdown_starts_after_last_client_disconnects(workspace_tmp_path):
    from devbench.daemon import ipc

    with _daemon(workspace_tmp_path, idle_timeout_sec=2) as (workspace, _, metadata):
        process = psutil.Process(metadata["pid"])
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(metadata["socket_path"])
            client.sendall(b'{"op":"mcp"}\n')
            with client.makefile("rb") as reader:
                assert json.loads(reader.readline())["ok"]
                time.sleep(2.3)
                assert _alive(process)
        # Poll the OS; a new IPC connection would legitimately reset this timer.
        _until(lambda: not _alive(process), timeout=15)
        assert ipc.daemon_status(workspace)["status"] == "stopped"


def test_explicit_stop_disconnects_idle_mcp_client(workspace_tmp_path):
    from devbench.daemon import ipc

    with _daemon(workspace_tmp_path) as (workspace, _, metadata):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(metadata["socket_path"])
            client.sendall(b'{"op":"mcp"}\n')
            with client.makefile("rb") as reader:
                assert json.loads(reader.readline())["ok"]
                assert ipc.stop_daemon(workspace)["status"] == "stopped"
                assert reader.readline() == b""


def _serve_with_gated_description(workspace, config, fd):
    from devbench.daemon.server import serve
    from devbench.platform.workbench_investigations import InvestigationService

    original = InvestigationService._describe_async
    root = workspace.parent

    def wait_for_release(self, *args):
        (root / "description-started").touch()
        try:
            _until((root / "release-description").is_file, timeout=45)
            return original(self, *args)
        finally:
            (root / "description-finished").touch()

    InvestigationService._describe_async = wait_for_release
    os.umask(0o077)
    serve(workspace, config, fd)


def test_deferred_workbench_http_job_holds_idle_and_stop_barriers(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import ipc
    from devbench.daemon.auth import read_token

    def spawn(workspace, config, lock_fd):
        # Only the description's completion is gated. Use the normal daemon and lock.
        return subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                str(workspace),
                json.dumps(config),
                "gated-workbench",
                str(lock_fd),
            ],
            cwd=workspace,
            start_new_session=True,
            pass_fds=(lock_fd,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    monkeypatch.setattr(ipc, "_spawn_daemon", spawn)
    release = workspace_tmp_path / "release-description"
    with _daemon(workspace_tmp_path, idle_timeout_sec=2) as (
        workspace,
        inputs,
        metadata,
    ):
        try:
            matrix = inputs / "counts.csv"
            matrix.write_text("gene,s1,s2\nA,1,2\nB,3,4\n")
            url = metadata["http_url"]
            code, _, data = _http(
                url,
                "/api/investigations",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": url.rstrip("/"),
                    "Authorization": f"Bearer {read_token(workspace)}",
                },
                body=json.dumps({"source": str(matrix), "question": "How many genes?"}),
            )
            assert code == 202, data
            _until((workspace_tmp_path / "description-started").is_file)
            state = ipc.request(workspace, "status")
            assert state["active_jobs"] >= 1 and state["attached_clients"] == 0
            with pytest.raises(ipc.DaemonError, match="daemon_busy"):
                ipc.stop_daemon(workspace)
            time.sleep(2.3)
            assert ipc.request(workspace, "status")["pid"] == metadata["pid"]
            owner = psutil.Process(metadata["pid"])
            release.touch()
            _until((workspace_tmp_path / "description-finished").is_file, timeout=30)
            _until(lambda: not _alive(owner), timeout=12)
        finally:
            release.touch()


def test_management_only_bootstrap_can_attach_first_input_root(workspace_tmp_path):
    from devbench.daemon import ipc

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    try:
        first = json.loads(
            _cli("daemon", "start", "--workspace", workspace, "--offline-only").stdout
        )
        attached = ipc.start_daemon(workspace, input_root=inputs, offline_only=True)
        assert (
            attached["pid"] == first["pid"]
            and first["socket_path"] == attached["socket_path"]
        )
        assert _json(workspace / "service.json")["input_roots"] == [str(inputs)]
        _cli("daemon", "stop", "--workspace", workspace)
        assert (
            json.loads(_cli("daemon", "status", "--workspace", workspace).stdout)[
                "status"
            ]
            == "stopped"
        )
    finally:
        ipc.stop_daemon(workspace)


def test_cli_approval_exact_immutable_identity(workspace_tmp_path):
    from devbench.platform.plugin_jobs import JobStore

    with _daemon(workspace_tmp_path) as (workspace, _, _):
        store = JobStore(workspace)
        identity = _freeze(store, "private", clinical_release_allowed=False)
        authority = _authority(workspace)

        def approve(**updates):
            args = {**identity, **updates}
            return _cli(
                "approve",
                "--workspace",
                workspace,
                "--actor",
                "operator-one",
                "--rationale",
                "Reviewed donor consent",
                "--session-id",
                args["session_id"],
                "--plan-id",
                args["plan_id"],
                "--plan-sha256",
                args["plan_sha256"],
                check=False,
            )

        assert authority.plan_approval(**identity) is None
        assert approve(plan_sha256="0" * 64).returncode != 0
        assert approve(session_id=uuid4().hex).returncode != 0
        assert approve(plan_id=uuid4().hex).returncode != 0
        assert authority.plan_approval(**identity) is None
        granted = approve()
        assert granted.returncode == 0, granted.stderr
        row = authority.plan_approval(**identity)
        assert {name: row[name] for name in identity} == identity
        assert row["actor_id"] == "operator-one" and row["surface"] == "cli"
        assert row["rationale"] == "Reviewed donor consent" and row["auth_method"]
        assert (
            datetime.fromisoformat(row["granted_at_utc"]).utcoffset().total_seconds()
            == 0
        )
        assert approve().returncode == 0
        assert (
            authority.plan_approval(**identity)["granted_at_utc"]
            == row["granted_at_utc"]
        )
        override = _freeze(store, "clinical", policy={"clinical_release_allowed": True})
        assert approve(**override).returncode != 0
        assert authority.plan_approval(**override) is None


def test_stopped_approve_coexists_with_standalone_workbench(workspace_tmp_path):
    from devbench.daemon import ipc
    from devbench.platform.plugin_jobs import JobStore
    from devbench.platform.workbench_ui import WorkbenchService

    workspace, inputs = workspace_tmp_path / "workspace", workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    app = WorkbenchService(
        workspace, [inputs], offline_only=True, run_timeout_seconds=71
    )
    try:
        identity = _freeze(JobStore(workspace), "private")
        assert ipc.daemon_status(workspace)["status"] == "stopped"
        lock = workspace / ".service.lock"
        with lock.open("rb") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            args = [
                "approve",
                "--workspace",
                workspace,
                "--session-id",
                identity["session_id"],
                "--plan-id",
                identity["plan_id"],
                "--actor",
                "headless-reviewer",
                "--rationale",
                "Reviewed the local frozen plan",
                "--plan-sha256",
            ]
            assert _cli(*args, "0" * 64, check=False).returncode != 0
            assert _authority(workspace).plan_approval(**identity) is None
            granted = json.loads(_cli(*args, identity["plan_sha256"]).stdout)
            assert granted == _authority(workspace).plan_approval(**identity)
            assert (
                granted["actor_id"] == "headless-reviewer"
                and granted["surface"] == "cli"
            )
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert ipc.daemon_status(workspace)["status"] == "stopped"
    finally:
        ipc.stop_daemon(workspace)
        app.close()


@pytest.mark.parametrize(
    ("scope", "expected"),
    [("sticky-input", 403), ("fastq-private", 403), ("public", 201)],
)
def test_online_studio_rechecks_protected_scope_before_approving(
    workspace_tmp_path, scope, expected
):
    from devbench.daemon.auth import read_token
    from devbench.platform.plugin_jobs import JobStore

    with _daemon(workspace_tmp_path, offline_only=False) as (workspace, _, metadata):
        jobs = JobStore(workspace)
        if scope == "sticky-input":
            profiles = [{"sha256": "f" * 64}]
            identity = _freeze(jobs, "public", profiles=profiles)
            _freeze(jobs, "private", profiles=profiles)
        elif scope == "fastq-private":
            identity = _freeze(jobs, "synthetic", fastq={"private": True})
        else:
            identity = _freeze(jobs, "public")
        origin = metadata["http_url"].rstrip("/")
        status, _, _ = _http(
            origin,
            "/api/approve",
            method="POST",
            headers={
                "Origin": origin,
                "Content-Type": "application/json",
                "Authorization": f"Bearer {read_token(workspace)}",
            },
            body=json.dumps(identity),
        )
        assert status == expected
        grant = _authority(workspace).plan_approval(**identity)
        if expected == 201:
            assert (
                grant["surface"] == "studio"
                and grant["plan_sha256"] == identity["plan_sha256"]
            )
        else:
            assert grant is None


def test_legacy_http_client_preserves_explicit_authorization(
    workspace_tmp_path, authenticated_workbench_client, monkeypatch
):
    from devbench.platform.workbench_ui import make_daemon_workbench, make_server

    app = make_daemon_workbench(workspace_tmp_path, None, offline_only=True)
    with make_server(app, port=0) as server:
        seen = []
        handler = server.RequestHandlerClass
        check = handler._bearer

        def observe(request, target):
            seen.append(request.headers.get_all("Authorization"))
            return check(request, target)

        monkeypatch.setattr(handler, "_bearer", observe)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            bearer = f"Bearer {server.daemon_token}"
            assert _http(origin, "/api/config")[0] == 200
            assert (
                _http(origin, "/api/config", headers={"authorization": bearer})[0]
                == 200
            )
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                connection.putrequest("GET", "/api/config")
                connection.putheader(b"aUtHoRiZaTiOn", "Bearer incorrect")
                connection.endheaders()
                response = connection.getresponse()
                assert response.status == 401
                response.read()
            finally:
                connection.close()
            assert seen == [[bearer], [bearer], ["Bearer incorrect"]]
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_loopback_boundaries_session_exchange_and_studio_approval(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon.auth import read_token
    from devbench.platform import workbench_ui
    from devbench.platform.plugin_jobs import JobStore

    with _daemon(workspace_tmp_path) as (workspace, inputs, metadata):
        url = metadata["http_url"]
        origin = url.rstrip("/")
        opener = Mock()
        monkeypatch.setattr(workbench_ui, "_open_browser_async", opener)
        flags = [
            "--workspace",
            str(workspace),
            "--input-root",
            str(inputs),
            "--offline-only",
        ]
        workbench_ui.main(flags, pin_execution=True, interactive_adaptive=True)
        opener.assert_not_called()
        workbench_ui.main(
            [*flags, "--open-browser"], pin_execution=True, interactive_adaptive=True
        )
        opener.assert_called_once_with(url)
        token = read_token(workspace)
        bearer = {"Authorization": f"Bearer {token}"}
        code, landing_headers, landing = _http(url, "/")
        assert code == 401 and token.encode() not in landing
        policy = landing_headers["Content-Security-Policy"]
        nonce = re.search(r"script-src 'nonce-([^']+)'", policy)
        assert nonce and nonce[1].encode() in landing
        status, _, body = _http(url, "/api/config")
        assert status == 401 and token.encode() not in body
        for forbidden in (
            {"Host": "other.invalid"},
            {"Origin": "https://other.invalid"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            status, _, body = _http(url, "/api/config", headers={**bearer, **forbidden})
            assert status == 403 and token.encode() not in body
        status, _, body = _http(url, "/api/config", headers=bearer)
        assert status == 200 and json.loads(body)["offline_only"] is True
        assert _http(url, "/mcp", headers=bearer)[0] == 404
        headers = {**bearer, "Origin": origin, "Content-Type": "application/json"}
        code, response_headers, body = _http(
            url, "/api/studio/session", method="POST", headers=headers, body=b""
        )
        assert code == 200
        cookie = response_headers["Set-Cookie"]
        assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
        assert token not in cookie and token.encode() not in body
        cookie_header = {"Cookie": cookie.partition(";")[0]}
        assert _http(url, "/api/config", headers=cookie_header)[0] in {401, 403}
        session = {
            **cookie_header,
            "X-Workbench-Session": json.loads(body)["session_key"],
            "Origin": origin,
            "Content-Type": "application/json",
        }
        assert _http(url, "/api/config", headers=session)[0] == 200
        identity = _freeze(JobStore(workspace), "private")
        payload = {
            **identity,
            "actor_id": "local-reviewer",
            "rationale": "Reviewed locally",
        }
        denied = {**headers, "Origin": "https://other.invalid"}
        assert (
            _http(
                url,
                "/api/approve",
                headers=denied,
                method="POST",
                body=json.dumps(payload),
            )[0]
            == 403
        )
        assert _authority(workspace).plan_approval(**identity) is None
        assert (
            _http(
                url,
                "/api/approve",
                headers=session,
                method="POST",
                body=json.dumps(payload),
            )[0]
            == 201
        )
        grant = _authority(workspace).plan_approval(**identity)
        assert grant["actor_id"] == "local-reviewer" and grant["surface"] == "studio"


def test_opt_in_streamable_http_serves_existing_mcp_tools(workspace_tmp_path):
    from devbench.daemon.auth import read_mcp_token, read_token

    with _daemon(workspace_tmp_path, enable_streamable_http=True) as (
        workspace,
        _,
        metadata,
    ):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        origin = metadata["http_url"].rstrip("/")
        headers = {"Authorization": f"Bearer {read_token(workspace)}"}
        for method in ("GET", "POST", "DELETE"):
            assert _http(origin, "/mcp", method=method)[0] == 401
        for invalid in (
            {"Origin": "https://other.invalid"},
            {"Host": "other.invalid"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            assert (
                _http(origin, "/mcp", method="POST", headers={**headers, **invalid})[0]
                == 403
            )
        with closing(
            http.client.HTTPConnection("127.0.0.1", urlsplit(origin).port, timeout=5)
        ) as client:
            client.putrequest("POST", "/mcp", skip_host=True)
            client.putheader("Authorization", headers["Authorization"])
            client.endheaders()
            response = client.getresponse()
            assert response.status == 403
            response.read()
        status, response_headers, response_body = _http(
            origin,
            "/api/studio/session",
            method="POST",
            headers={**headers, "Origin": origin},
        )
        assert status == 200
        browser = {
            "Cookie": response_headers["Set-Cookie"].partition(";")[0],
            "X-Workbench-Session": json.loads(response_body)["session_key"],
        }
        assert _http(origin, "/api/config", headers=browser)[0] == 200
        assert (
            _http(origin, "/mcp", method="POST", headers={**browser, "Origin": origin})[
                0
            ]
            == 401
        )
        assert (
            _http(origin, "/api/studio/session", method="POST", headers=headers)[0]
            == 403
        )
        sentinel = "LOCAL_INPUT_VALUE_MUST_NOT_APPEAR"
        headers = {"Authorization": f"Bearer {read_mcp_token(workspace)}"}
        invalid = {"jsonrpc": sentinel, "id": 17, "method": "initialize"}
        status, _, diagnostic = _http(
            origin,
            "/mcp",
            method="POST",
            headers={
                **headers,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            body=json.dumps(invalid),
        )
        assert status == 400 and sentinel.encode() not in diagnostic

        async def tools():
            async with httpx.AsyncClient(headers=headers, trust_env=False) as http:
                async with streamable_http_client(
                    origin + "/mcp", http_client=http
                ) as (rx, tx, _):
                    async with ClientSession(rx, tx) as session:
                        await session.initialize()
                        return [
                            tool.name for tool in (await session.list_tools()).tools
                        ]

        names = asyncio.run(tools())
        from devbench.mcp.schemas import INPUTS

        assert set(names) == set(INPUTS) and len(names) == 13


def test_control_frame_requires_newline_before_dispatch(workspace_tmp_path):
    from devbench.daemon import ipc

    with _daemon(workspace_tmp_path) as (workspace, _, daemon):
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(5)
            client.connect(daemon["socket_path"])
            client.sendall(b'{"op":"status"}')
            client.shutdown(socket.SHUT_WR)
            with client.makefile("rb") as stream:
                assert json.loads(stream.readline()) == {
                    "ok": False,
                    "error": "invalid_request",
                }
        assert ipc.request(workspace, "status")["pid"] == daemon["pid"]


def test_mcp_outbound_byte_limit_includes_delimiter(monkeypatch):
    from mcp import types
    from mcp.shared.message import SessionMessage

    from devbench.daemon import server

    message = types.JSONRPCMessage.model_validate(
        {"jsonrpc": "2.0", "id": 1, "result": {"text": "µ"}}
    )
    frame = message.model_dump_json(by_alias=True, exclude_none=True).encode() + b"\n"

    async def exercise(limit):
        drained, packets = asyncio.Event(), []
        reader = asyncio.StreamReader()
        reader.feed_eof()

        async def drain():
            drained.set()

        async def run(_reader, writer, _options):
            await writer.send(SessionMessage(message))
            await drained.wait()

        endpoint = SimpleNamespace(create_initialization_options=lambda: None, run=run)
        writer = SimpleNamespace(write=packets.append, drain=drain)
        monkeypatch.setattr(server, "_FRAME_BYTES", limit)
        if limit == len(frame):
            await server._serve_mcp(endpoint, reader, writer)
            assert packets == [frame]
        else:
            with pytest.raises(ExceptionGroup) as failure:
                await server._serve_mcp(endpoint, reader, writer)
            assert failure.value.subgroup(ValueError)
            assert not packets

    asyncio.run(asyncio.wait_for(exercise(len(frame)), 5))
    asyncio.run(asyncio.wait_for(exercise(len(frame) - 1), 5))


async def _rpc(process, method, params, *, notify=False):
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if not notify:
        message["id"] = 1
    process.stdin.write(json.dumps(message).encode() + b"\n")
    await process.stdin.drain()
    if notify:
        return None
    async with asyncio.timeout(30):
        while line := await process.stdout.readline():
            response = json.loads(line)
            if response.get("id") == 1:
                assert "error" not in response, response
                return response["result"]
    raise AssertionError("MCP shim disconnected before replying")


@asynccontextmanager
async def _stdio_session(
    workspace,
    inputs,
    errors,
    *,
    legacy_browser_log=None,
    example=False,
    capabilities=None,
    allow_external_input_files=False,
):
    if legacy_browser_log is None:
        command = ["-m", "devbench.platform.cli", "mcp", "--no-auto-browser"]
        if allow_external_input_files:
            command.append("--allow-external-input-files")
    else:
        entry = (
            "from pathlib import Path; "
            "from devbench.platform import cli, workbench_ui; "
            f"log=Path({str(legacy_browser_log)!r}); "
            "workbench_ui._open_browser_async=lambda *_: log.touch(); "
            "raise SystemExit(cli.main())"
        )
        if example:
            entry = entry.replace(
                "raise SystemExit(cli.main())",
                "from examples.motor_programs.server import main; "
                "raise SystemExit(main())",
            )
        command = ["-c", entry, *([] if example else ["plugin-mcp"])]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *command,
        "--workspace",
        str(workspace),
        "--input-root",
        str(inputs),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=errors,
        limit=1024 * 1024,
    )
    try:
        initialized = await _rpc(
            process,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": capabilities or {},
                "clientInfo": {"name": "daemon-test", "version": "1.0"},
            },
        )
        assert initialized["serverInfo"]["name"] == "devharness-rna"
        await _rpc(process, "notifications/initialized", {}, notify=True)
        yield process
    finally:
        # Exercise loss of the actual stdio process, without an MCP shutdown handshake.
        if process.returncode is None:
            process.kill()
        await process.wait()


async def _tool(process, name, arguments, *, expect_error=False):
    result = await _rpc(process, "tools/call", {"name": name, "arguments": arguments})
    assert bool(result.get("isError")) == expect_error, result
    return result["structuredContent"]


@pytest.mark.parametrize("legacy", [False, True], ids=["mcp-shim", "plugin-mcp"])
def test_cold_mcp_private_plan_executes_only_after_offline_studio_consent(
    workspace_tmp_path, legacy
):
    import h5py

    from devbench.daemon import ipc
    from devbench.daemon.auth import read_token
    from tests.test_installed_plugin_private_graph import _fixture_days

    workspace, inputs = workspace_tmp_path / "workspace", workspace_tmp_path / "inputs"
    sentinel = "private_marker_must_stay_in_studio"
    private = inputs / sentinel
    private.mkdir(parents=True)
    paths = _fixture_days(private)
    cell_sentinel = "SYNTHETIC_PRIVATE_CELL_IDENTIFIER"
    with h5py.File(paths[0], "r+") as h5:
        h5["obs/_index"][0] = cell_sentinel
    declarations = [{"path": str(p), "data_classification": "private"} for p in paths]
    errors = workspace_tmp_path / "stderr"
    opened = workspace_tmp_path / "browser.log"

    async def request(rid=None):
        with errors.open("ab") as stderr:
            async with _stdio_session(
                workspace,
                inputs,
                stderr,
                legacy_browser_log=opened if legacy else None,
                example=legacy,
            ) as process:

                async def call(name, arguments, *, denied=False):
                    packet = await _rpc(
                        process, "tools/call", {"name": name, "arguments": arguments}
                    )
                    assert bool(packet.get("isError")) is denied
                    for secret in (
                        sentinel,
                        cell_sentinel,
                        str(paths[0]),
                        read_token(workspace),
                    ):
                        assert (
                            secret not in json.dumps(packet)
                            and secret not in errors.read_text()
                        )
                    return packet["structuredContent"]

                if rid is not None:
                    receipt = await call(
                        "get_execution_receipt" if legacy else "get_run_receipt",
                        {"session_id": sid, "run_id": rid},
                        denied=True,
                    )
                    assert receipt["reason"] == "private_data_local_studio_only"
                    return
                if legacy:
                    planned = await call(
                        "plan_rna_workflow",
                        {
                            "operation": "motor_program_comparison",
                            "objective": "Review the synthetic marker counts locally",
                            "inputs": declarations,
                        },
                    )
                else:
                    selected = await call(
                        "register_investigation_input", _admission(paths[0], "private")
                    )
                    planned = await call("freeze_analysis_plan", _audit_plan(selected))
                assert planned["status"] == "local_studio_only"
                frozen = {
                    key: planned[key]
                    for key in ("session_id", "plan_id", "plan_sha256")
                }
                denial = await call(
                    "execute_reviewed_plan" if legacy else "submit_reviewed_run",
                    frozen,
                    denied=True,
                )
                assert denial["reason"] == "private_data_local_studio_only"
                return planned, frozen

    try:
        assert ipc.daemon_status(workspace)["status"] == "stopped"
        plan, identity = asyncio.run(request())
        daemon = ipc.daemon_status(workspace)
        origin = daemon["http_url"].rstrip("/")
        sid = identity["session_id"]
        assert plan["studio_url"] == f"{origin}/?session_id={sid}&view=workflows"
        assert daemon["offline_only"] and daemon["allow_data_execution"]
        assert not opened.exists()
        launch = [
            str({"<input-root>": inputs, "<workspace>": workspace}.get(word, word))
            for word in shlex.split(plan["launch_hint"])
        ]
        assert launch[:2] == ["devbench", "workbench"]
        assert {
            "--offline-only",
            "--enable-scientific",
            "--allow-data-execution",
        } <= set(launch)
        entry = (
            "from unittest.mock import patch; from devbench.platform.cli import main; "
            "opener=patch('devbench.platform.workbench_ui._open_browser_async')"
            ".start(); "
            "code=main(); opener.assert_not_called(); raise SystemExit(code)"
        )
        attached = subprocess.run(
            [sys.executable, "-c", entry, *launch[1:]],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert attached.returncode == 0, attached.stderr
        assert (
            daemon["http_url"] in attached.stdout
            and ipc.daemon_status(workspace)["pid"] == daemon["pid"]
        )
        route = "/api/scientific/adaptive/" + sid + "/plugin-execute"
        payload = {
            "plan_id": identity["plan_id"],
            "plan_sha256": identity["plan_sha256"],
            "allow_data_execution": True,
        }
        headers = {"Origin": origin, "Content-Type": "application/json"}
        assert (
            _http(
                origin, route, method="POST", headers=headers, body=json.dumps(payload)
            )[0]
            == 401
        )
        assert _authority(workspace).plan_approval(**identity) is None
        headers["Authorization"] = f"Bearer {read_token(workspace)}"
        assert (
            _http(
                origin,
                route,
                method="POST",
                headers=headers,
                body=json.dumps({**payload, "allow_data_execution": False}),
            )[0]
            == 403
        )
        assert _authority(workspace).plan_approval(**identity) is None
        status, _, response = _http(
            origin, route, method="POST", headers=headers, body=json.dumps(payload)
        )
        assert status == 202, response
        rid = json.loads(response)["run_id"]

        def completed():
            row = ipc.request(workspace, "run_status", run_id=rid)
            return row if row["cleanup_state"] == "confirmed" else None

        row = _until(completed, timeout=45)
        assert row["status"] == "completed", row["error_text"]
        grant = _authority(workspace).plan_approval(**identity)
        assert grant["surface"] == "studio" and grant["auth_method"] == "bearer"
        asyncio.run(request(rid))
        assert not opened.exists()
    finally:
        _stop_when_idle(workspace)


@pytest.mark.parametrize("legacy", [True, False], ids=["plugin-mcp", "mcp-shim"])
def test_default_legacy_plugin_mcp_uses_online_daemon_without_popup(
    workspace_tmp_path, legacy
):
    from devbench.daemon import ipc
    from devbench.platform.plugin_jobs import JobStore, studio_handoff

    with _daemon(workspace_tmp_path, offline_only=False) as (workspace, inputs, daemon):
        counts = inputs / "public.csv"
        counts.write_text("gene,s1,s2\nA,1,2\nB,3,4\n")
        browser_log = workspace_tmp_path / "browser.log"

        async def inspect():
            with (workspace_tmp_path / "stderr").open("wb") as errors:
                async with _stdio_session(
                    workspace,
                    inputs,
                    errors,
                    legacy_browser_log=browser_log if legacy else None,
                ) as process:
                    assert len((await _rpc(process, "tools/list", {}))["tools"]) == (
                        7 if legacy else 13
                    )
                    declaration = {"path": str(counts), "data_classification": "public"}
                    if not legacy:
                        declaration["file_kind"] = "expression"
                    inspected = await _tool(
                        process,
                        "inspect_rna_inputs" if legacy else "inspect_inputs",
                        {"inputs": [declaration]},
                    )
                    item = inspected["inputs"][0]
                    assert (
                        item["observed"]["file_type"] if legacy else item["format"]
                    ) == "csv"

        asyncio.run(inspect())
        current = ipc.daemon_status(workspace)
        assert current["pid"] == daemon["pid"] and current["offline_only"] is False
        assert not current.get("allow_data_execution")
        assert not browser_log.exists()
        private = _freeze(JobStore(workspace), "private")
        assert (
            studio_handoff(workspace, inputs, private["session_id"], "overview")[
                "studio_url"
            ]
            is None
        )


def _admission(path, classification="synthetic"):
    return {
        "input": {
            "path": str(path),
            "file_kind": "expression",
            "data_classification": classification,
            "investigation_role": "primary_cohort",
        },
        "representation_class": "count_1_raw_counts",
        "provenance_declaration": {
            "origin": "user_declaration",
            "source": "synthetic fixture",
            "count_provenance": "raw counts",
        },
    }


def _audit_plan(admitted):
    ref = admitted["artifact"] or admitted["opaque_artifact"]
    return {
        "session_id": admitted["session_id"],
        "plan_kind": "tier1_kernel",
        "branch_id": "primary",
        "epistemic_purpose": "qc_diagnostic",
        "objective": "Audit the registered synthetic matrix",
        "kernel": {"kernel_id": "matrix_audit", "version": "1", "parameters": {}},
        "input_bindings": {
            "expression": {k: ref[k] for k in ("artifact_id", "sha256")}
        },
    }


def test_restart_revokes_external_file_plan(workspace_tmp_path):
    external = workspace_tmp_path / "external.csv"
    external.write_text("gene,A,B\nred,1,2\nblue,3,4\n")
    with (
        _daemon(workspace_tmp_path, allow_external_input_files=True) as (
            workspace,
            inputs,
            _,
        ),
        (workspace_tmp_path / "stderr").open("wb") as errors,
    ):

        async def freeze():
            async with _stdio_session(
                workspace, inputs, errors, allow_external_input_files=True
            ) as client:
                admitted = await _tool(
                    client, "register_investigation_input", _admission(external)
                )
                plan = await _tool(
                    client, "freeze_analysis_plan", _audit_plan(admitted)
                )
                return {k: plan[k] for k in ("session_id", "plan_id", "plan_sha256")}

        async def check_revocation(identity):
            async with _stdio_session(workspace, inputs, errors) as client:
                refusal = await _tool(
                    client, "submit_reviewed_run", identity, expect_error=True
                )
                assert refusal["category"] == "input_scope_violation"
                state = await _tool(
                    client,
                    "list_investigation_state",
                    {"session_id": identity["session_id"]},
                )
                assert state["runs"] == []

        identity = asyncio.run(freeze())
        _stop_when_idle(workspace)
        asyncio.run(check_revocation(identity))


def _controlled_worker(workspace, rid):
    """Run two synthetic units, pausing at a real, persisted checkpoint."""
    from devbench.platform.plugin_jobs import JobStore

    store = JobStore(workspace)
    plan = store.start(rid)
    root = workspace / "runs" / rid
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = {}
    if plan.get("needs_cleanup"):
        stage = root / "container" / "stage"
        stage.mkdir(mode=0o700, parents=True)
        metadata["mounted_container"] = {
            "stage": str(stage),
            "request_sha": "a" * 64,
            "image_id": "sha256:" + "b" * 64,
            "attempt_id": "c" * 32,
        }
    if plan.get("resist_term"):

        def keep_running(_signum, _frame):
            (root / "saw-term").touch()

        signal.signal(signal.SIGTERM, keep_running)
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,signal,sys,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(90)",
                str(root / "child.pid"),
            ]
        )
        _until(lambda: (root / "child.pid").exists() or child.poll() is not None)
        assert child.poll() is None
    restored = store.resume_checkpoint(rid)
    executed = [] if restored else [1]
    if restored:
        assert restored["state"]["finished_units"] == [1]
    else:
        store.checkpoint(rid, step="first_unit", state={"finished_units": [1]})
    store.stage(rid, step="waiting_for_release", percent=50, **metadata)
    (root / "ready").touch()
    _until(lambda: (root / "release").is_file(), timeout=60)
    executed.append(2)
    store.finish(
        rid, receipt={"executed_units": executed, "clinical_release_allowed": False}
    )


def _detached_engine_worker(workspace, rid):
    from devbench.platform.plugin_jobs import JobStore

    store = JobStore(workspace)
    plan = store.start(rid)
    root = workspace / "runs" / rid
    attempt = root / "nextflow" / ("fastq-" + rid)
    attempt.mkdir(mode=0o700, parents=True)
    store.stage(
        rid,
        step="nextflow",
        nextflow_attempt=str(attempt),
        nextflow_docker={
            "endpoint": "unix:///test/isolated.sock",
            "daemon_id": "original",
        },
    )
    engine = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(80)"],
        cwd=attempt,
        start_new_session=True,
    )
    created = psutil.Process(engine.pid).create_time()
    record = {
        "format": "devbench.fastq-attempt/1",
        "attempt_dir": str(attempt),
        "engine_launch_attempt_count": 1,
        "engine_launch_count": 1,
        "engine_execution": "started",
        "engine_process_id": engine.pid,
        "engine_process_create_time": created - 60
        if plan["wrong_engine_birth"]
        else created,
    }
    (attempt / "result.json").write_text(json.dumps(record))
    (root / "ready").touch()
    engine.wait(timeout=90)


@contextmanager
def _supervisor(root, *, worker_mode="controlled", heartbeat_sec=0.2, **options):
    from devbench.daemon.supervisor import Supervisor
    from devbench.platform.plugin_jobs import JobStore

    workspace = root / "workspace"
    store = JobStore(workspace)
    workers = []

    def spawn(directory, rid):
        # Interrupt only the original worker. Recovery uses the installed worker entry.
        mode = (
            "native"
            if workers and worker_mode == "crash_at_publication"
            else worker_mode
        )
        if workers and mode == "fastq-preflight":
            mode = "controlled"
        proc = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                str(directory),
                rid,
                mode,
            ],
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        workers.append(proc)
        return proc

    supervisor = Supervisor(
        workspace,
        grace_sec=0.2,
        poll_sec=0.05,
        heartbeat_sec=heartbeat_sec,
        worker_factory=spawn,
        **options,
    )
    try:
        supervisor.start()
        yield supervisor, store, workspace / "runs"
    finally:
        try:
            supervisor.close()
        finally:
            for proc in workers:
                if proc.poll() is None and os.getpgid(proc.pid) == proc.pid:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)


def _ready(runs, rid):
    _until(lambda: (runs / rid / "ready").is_file())
    return runs / rid


def test_fast_exit_worker_records_birth_before_reaper_collects_child(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import supervisor

    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE,
        start_new_session=True,
    ) as child:
        try:
            birth = psutil.Process(child.pid).create_time()
            owner = supervisor.Supervisor(
                workspace_tmp_path / "workspace", worker_factory=lambda *_: child
            )
            row = owner.submit(**_freeze(owner.jobs))

            def immediate_reaper(*, target, **_):
                def start():
                    # Force the earliest legal reap once the reaper is started.
                    child.stdin.close()
                    target()

                return SimpleNamespace(start=start)

            monkeypatch.setattr(
                supervisor, "threading", SimpleNamespace(Thread=immediate_reaper)
            )
            assert owner._dispatch()
            dispatched = owner.status(row["run_id"])
            assert child.returncode == 0
            assert dispatched["status"] == "running"
            assert dispatched["pid"] == child.pid and dispatched["create_time"] == birth
        finally:
            if child.poll() is None:
                child.kill()


def test_atomic_heartbeat_read_reopens_superseded_inode(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import auth

    name = "worker-heartbeat.json"
    with auth.owned_directory(workspace_tmp_path) as directory:
        auth.write_file(directory, name, b"first")
        original = os.stat(name, dir_fd=directory)
        fstat = os.fstat

        def publish_before_check(fd):
            info = fstat(fd)
            if info.st_nlink == 1 and os.path.samestat(original, info):
                auth.write_file(directory, name, b"second")
                info = fstat(fd)
                assert info.st_nlink == 0
            return info

        monkeypatch.setattr(os, "fstat", publish_before_check)
        assert auth.read_file(directory, name, 2048) == b"second"
        os.link(name, "other-name", src_dir_fd=directory, dst_dir_fd=directory)
        with pytest.raises(PermissionError):
            auth.read_file(directory, name, 2048)


def _state(supervisor, rid, status):
    def observe():
        row = supervisor.status(rid)
        if row["status"] == status:
            return row
        assert row["status"] not in {
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        }, (row["status"], row["error_text"])

    return _until(observe)


def test_memory_and_gpu_admission(workspace_tmp_path):
    with _supervisor(
        workspace_tmp_path,
        cpu_slots=3,
        gpu_slots=1,
        memory_budget_mb=4096,
        max_concurrent_runs=4,
    ) as (sup, store, runs):
        identities = [_freeze(store) for _ in range(4)]
        with pytest.raises((ValueError, RuntimeError)):
            sup.submit(**identities[0], memory_mb=4097)
        a = sup.submit(**identities[1], pool="gpu", memory_mb=3000)["run_id"]
        _ready(runs, a)
        b = sup.submit(**identities[2], pool="gpu", memory_mb=1024)["run_id"]
        c = sup.submit(**identities[3], pool="cpu", memory_mb=3000)["run_id"]
        assert sup.status(b)["status"] == sup.status(c)["status"] == "queued"
        (runs / a / "release").touch()
        _ready(runs, b)
        _ready(runs, c)
        assert sup.status(b)["status"] == sup.status(c)["status"] == "running"
        for rid in (b, c):
            (runs / rid / "release").touch()
            _state(sup, rid, "completed")


def test_cancel_escalates_group_and_confirms_oci_cleanup(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import supervisor

    with _supervisor(workspace_tmp_path) as (sup, store, runs):
        rid = sup.submit(**_freeze(store, resist_term=True, needs_cleanup=True))[
            "run_id"
        ]
        root = _ready(runs, rid)
        parent = psutil.Process(sup.status(rid)["pid"])
        child = psutil.Process(int((root / "child.pid").read_text()))
        assert _alive(child) and os.getpgid(child.pid) == parent.pid

        def recover(stage, *, request_sha, image_id, attempt_id):
            assert not _alive(parent) and not _alive(child)
            assert Path(stage) == root / "container" / "stage"
            assert request_sha == "a" * 64 and image_id == "sha256:" + "b" * 64
            assert attempt_id == "c" * 32
            return {"cleanup_confirmed": True, "disposition": "removed"}

        hook = Mock(side_effect=recover)
        monkeypatch.setattr(supervisor, "recover_mounted_container", hook)
        cancelled = sup.cancel_run(rid)
        assert cancelled["status"] == "cancelled"
        assert cancelled["cleanup_state"] == "confirmed"
        assert (root / "saw-term").is_file()
        assert sup.cancel_run(rid)["cleanup_state"] == "confirmed"
        assert hook.call_count == 1
        assert sup.status(rid)["cleanup_state"] == "confirmed"


def test_uncertain_container_cleanup_remains_retryable(workspace_tmp_path, monkeypatch):
    from devbench.daemon import supervisor

    with _supervisor(workspace_tmp_path) as (sup, store, runs):
        rid = sup.submit(**_freeze(store, needs_cleanup=True))["run_id"]
        _ready(runs, rid)
        hook = Mock(side_effect=RuntimeError("Docker daemon identity changed"))
        monkeypatch.setattr(supervisor, "recover_mounted_container", hook)
        with suppress(RuntimeError):
            sup.cancel_run(rid)
        assert hook.called and sup.status(rid)["cleanup_state"] != "confirmed"
        hook.side_effect = None
        hook.return_value = {
            "cleanup_confirmed": True,
            "disposition": "already_removed",
        }
        assert sup.cancel_run(rid)["cleanup_state"] == "confirmed"


@pytest.mark.parametrize(
    ("trigger", "wrong_birth", "changed_daemon"),
    [
        ("cancel", False, False),
        ("crash", False, False),
        ("crash", True, False),
        ("crash", False, True),
    ],
)
def test_recover_detached_nextflow_before_first_descendant_heartbeat(
    workspace_tmp_path,
    monkeypatch,
    trigger,
    wrong_birth,
    changed_daemon,
):
    from devbench.daemon import supervisor

    with _supervisor(workspace_tmp_path, worker_mode="nextflow", heartbeat_sec=60) as (
        sup,
        store,
        runs,
    ):
        rid = sup.submit(
            **_freeze(store, operation="qc-salmon", wrong_engine_birth=wrong_birth)
        )["run_id"]
        root = _ready(runs, rid)
        attempt = root / "nextflow" / ("fastq-" + rid)
        record = _json(attempt / "result.json")
        engine = psutil.Process(record["engine_process_id"])
        inventory = []

        def respond(command):
            inventory.append(command)
            if command == ["info", "--format", "{{.ID}}"]:
                return "replacement" if changed_daemon else "original"
            assert command == [
                "container",
                "ls",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=devbench.fastq.attempt=" + attempt.name,
            ]
            return ""

        @contextmanager
        def local_client(directory, *, endpoint):
            assert directory == attempt and endpoint == "unix:///test/isolated.sock"
            yield SimpleNamespace(endpoint=endpoint, _call=respond)

        monkeypatch.setattr(supervisor, "_private_runtime", local_client)
        try:
            assert Path(engine.cwd()) == attempt and os.getsid(engine.pid) == engine.pid
            row = sup.status(rid)
            assert engine.pid not in dict(
                json.loads(row["stage_json"]).get("_children", [])
            )
            if trigger == "cancel":
                sup.cancel_run(rid)
            else:
                psutil.Process(row["pid"]).kill()

            def outcome():
                state = sup.status(rid)["cleanup_state"]
                if state == "confirmed":
                    assert not _alive(engine), (
                        "confirmed cleanup left the detached engine alive"
                    )
                return state if state in {"confirmed", "unconfirmed"} else None

            if wrong_birth:
                assert _until(outcome) == "unconfirmed" and _alive(engine)
                assert not inventory
            elif changed_daemon:
                assert _until(outcome) == "unconfirmed" and not _alive(engine)
                assert inventory and all(command[0] == "info" for command in inventory)
            else:
                assert _until(outcome) == "confirmed"
                assert any(command[0] == "container" for command in inventory)
        finally:
            if _alive(engine):
                engine.kill()
                engine.wait(timeout=5)


def test_killed_worker_resumes_only_its_verified_checkpoint(workspace_tmp_path):
    with _supervisor(workspace_tmp_path) as (sup, store, runs):
        identity = _freeze(store)
        original = sup.submit(**identity)["run_id"]
        _ready(runs, original)
        manifest = (
            store.workspace
            / ".devbench-daemon"
            / "runs"
            / original
            / "checkpoint_manifest.json"
        )
        sealed = manifest.read_bytes()
        first = sup.status(original)
        assert re.fullmatch("[0-9a-f]{64}", first["checkpoint_sha256"])
        psutil.Process(first["pid"]).kill()
        interrupted = _state(sup, original, "interrupted")
        assert interrupted["cleanup_state"] == "confirmed"
    # A fresh supervisor has no in-memory worker or queue state from before the crash.
    with _supervisor(workspace_tmp_path) as (sup, store, runs):
        with pytest.raises((ValueError, RuntimeError)):
            sup.submit(**_freeze(store), resume_from_run_id=original)
        manifest.write_text("{}")
        with pytest.raises((ValueError, RuntimeError)):
            sup.submit(**identity, resume_from_run_id=original)
        manifest.write_bytes(sealed)
        resumed = sup.submit(**identity, resume_from_run_id=original)
        second = resumed["run_id"]
        assert second != original and resumed["resume_from_run_id"] == original
        second_root = _ready(runs, second)
        (second_root / "release").touch()
        completed = _state(sup, second, "completed")
        assert json.loads(completed["receipt_json"])["executed_units"] == [2]
        assert sup.submit(**identity, resume_from_run_id=original)["run_id"] == second
        assert sup.status(original)["status"] == "interrupted"


def test_unapproved_private_and_clinical_jobs_never_spawn(workspace_tmp_path):
    with _supervisor(workspace_tmp_path) as (sup, store, _runs):
        for classification in ("private", "clinical", "unclassified"):
            identity = _freeze(store, classification)
            with pytest.raises((ValueError, RuntimeError, PermissionError)):
                sup.submit(**identity)
            assert _authority(store.workspace).plan_approval(**identity) is None
        assert sup.active_count() == 0


def test_online_daemon_refuses_previously_approved_private_run(workspace_tmp_path):
    from devbench.daemon import ipc
    from devbench.platform.plugin_jobs import JobStore

    with _daemon(workspace_tmp_path) as (workspace, inputs, _):
        identity = _freeze(JobStore(workspace), "private")
        ipc.request(
            workspace,
            "approve",
            **identity,
            actor_id="local-operator",
            rationale="offline review",
        )
        assert _authority(workspace).plan_approval(**identity)
    try:
        online = ipc.start_daemon(workspace, input_root=inputs, offline_only=False)
        assert online["offline_only"] is False
        with pytest.raises(ipc.DaemonError, match="private_data_local_studio_only"):
            ipc.request(workspace, "submit", **identity)
        assert ipc.request(workspace, "status")["active_jobs"] == 0
    finally:
        ipc.stop_daemon(workspace)


def _grant(store, identity):
    return store.approve(
        identity["session_id"],
        identity["plan_id"],
        identity["plan_sha256"],
        actor_id="local-operator",
        surface="cli",
        auth_method="local_uid",
        rationale="Reviewed the exact frozen plan offline",
    )


def _held(lookup, rid, expected):
    def observe():
        record = lookup(rid)
        assert record["pid"] is None, "a restricted worker was dispatched"
        if record["status"] == expected:
            return record
        assert record["status"] == "queued", record["status"]

    return _until(observe, timeout=8)


def test_restart_defers_previously_queued_private_work_until_offline(
    workspace_tmp_path,
):
    from devbench.daemon import ipc
    from devbench.daemon.supervisor import Supervisor
    from devbench.platform.plugin_jobs import JobStore

    with _daemon(workspace_tmp_path) as (workspace, inputs, _):
        store = JobStore(workspace)
        identity = _freeze(store, "private")
        _grant(store, identity)
    # A successfully committed request can outlive the process before dispatch.
    stopped = Supervisor(workspace, offline_only=True)
    try:
        rid = stopped.submit(**identity)["run_id"]
        assert stopped.status(rid)["status"] == "queued"
    finally:
        stopped.close()
    try:
        ipc.start_daemon(workspace, input_root=inputs, offline_only=False)
        _held(
            lambda run_id: ipc.request(workspace, "run_status", run_id=run_id),
            rid,
            "awaiting_offline",
        )
        assert ipc.request(workspace, "status")["active_jobs"] == 0
    except Exception:
        # Release this test's own worker if a regression launched it prematurely.
        with suppress(ipc.DaemonError):
            ipc.request(workspace, "cancel_run", run_id=rid)
        raise
    finally:
        # A held job must allow an operator to restart with the required policy.
        ipc.stop_daemon(workspace)
    with _supervisor(workspace_tmp_path, offline_only=True) as (offline, _, runs):
        root = _ready(runs, rid)
        (root / "release").touch()
        assert (
            _state(offline, rid, "completed")["plan_sha256"] == identity["plan_sha256"]
        )


def test_dispatch_rechecks_sticky_taint_and_exact_grant(workspace_tmp_path):
    with _supervisor(
        workspace_tmp_path, offline_only=True, cpu_slots=1, max_concurrent_runs=1
    ) as (sup, store, runs):
        blocker = sup.submit(**_freeze(store))["run_id"]
        _ready(runs, blocker)
        profiles = [{"sha256": "d" * 64}]
        identity = _freeze(store, "public", profiles=profiles)
        restricted = sup.submit(**identity)["run_id"]
        assert sup.status(restricted)["status"] == "queued"
        # This later declaration covers the same content as the frozen plan.
        other = _freeze(store, "private", profiles=profiles)
        _grant(store, other)
        unrelated = sup.submit(**_freeze(store))["run_id"]
        (runs / blocker / "release").touch()
        _held(sup.status, restricted, "awaiting_approval")
        unrelated_root = _ready(runs, unrelated)
        assert not (runs / restricted / "ready").exists()
        _grant(store, identity)
        (unrelated_root / "release").touch()
        resumed = _ready(runs, restricted)
        (resumed / "release").touch()
        assert _state(sup, restricted, "completed")["plan_id"] == identity["plan_id"]


def test_online_start_refuses_running_private_generation(workspace_tmp_path):
    from devbench.daemon.supervisor import Supervisor
    from devbench.engine.workflow_operations import OperationBlocked

    with _supervisor(workspace_tmp_path, offline_only=True) as (old, store, runs):
        identity = _freeze(store, "private")
        _grant(store, identity)
        rid = old.submit(**identity)["run_id"]
        run = _ready(runs, rid)
        live = psutil.Process(old.status(rid)["pid"])
        old.close()
        online = Supervisor(store.workspace, offline_only=False)
        try:
            with pytest.raises(
                OperationBlocked, match="private_data_local_studio_only"
            ):
                online.start()
            assert _alive(live)
        finally:
            online.close()
            (run / "release").touch()
        with _supervisor(workspace_tmp_path, offline_only=True) as (restored, _, _runs):
            assert _state(restored, rid, "completed")["pid"] == live.pid


def _legacy_writer(workspace, rid):
    # Use only the pre-daemon worker's storage interface.
    gate = workspace / "runs" / rid / "release-legacy"
    _until(gate.is_file, timeout=40)
    receipt = json.dumps({"run_id": rid, "clinical_release_allowed": False})
    with sqlite3.connect(workspace / "plugin-jobs.sqlite3") as db:
        now = time.time()
        updated = db.execute(
            "UPDATE plugin_runs SET status='completed',receipt_json=?,"
            "finished_at=?,updated_at=? "
            "WHERE run_id=? AND pid=? AND status='running'",
            (receipt, now, now, rid, os.getpid()),
        )
        assert updated.rowcount == 1


@pytest.mark.parametrize("outcome", ["completed", "interrupted"])
def test_drain_pre_upgrade_worker_before_dispatch(
    workspace_tmp_path, monkeypatch, outcome
):
    from devbench.daemon import supervisor
    from devbench.platform.plugin_jobs import JobStore

    workspace = workspace_tmp_path / "workspace"
    store = JobStore(workspace)
    identity = _freeze(store)
    rid = uuid4().hex
    root = workspace / "runs" / rid
    stage = root / "container" / "stage"
    root.mkdir(mode=0o700, parents=True)
    stage.parent.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    binding = {
        "request_sha": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "attempt_id": "c" * 32,
    }
    recover = Mock(return_value={"disposition": "removed", "cleanup_confirmed": True})
    monkeypatch.setattr(supervisor, "recover_mounted_container", recover)
    with subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(workspace), rid, "legacy"],
        start_new_session=True,
    ) as old:
        try:
            metadata = {
                "process_created_at": psutil.Process(old.pid).create_time(),
                "mounted_container": {"stage": str(stage), **binding},
            }
            with sqlite3.connect(store.path) as db:
                now = time.time()
                db.execute(
                    "INSERT INTO plugin_runs VALUES "
                    "(?,?,?,?,'running',?,?,NULL,NULL,?,?,NULL)",
                    (
                        rid,
                        identity["session_id"],
                        identity["plan_id"],
                        identity["plan_sha256"],
                        old.pid,
                        json.dumps(metadata),
                        now,
                        now,
                    ),
                )
            with _supervisor(workspace_tmp_path, cpu_slots=2) as (sup, current, runs):
                assert sup.status(rid)["status"] == "running"
                new = sup.submit(**_freeze(current))["run_id"]
                # Let the scheduler consider work while legacy resource use is unknown.
                time.sleep(5 * sup.poll_sec)
                assert sup.status(new)["status"] == "queued" and old.poll() is None
                recover.assert_not_called()
                if outcome == "completed":
                    (root / "release-legacy").touch()
                    assert old.wait(timeout=10) == 0
                else:
                    old.kill()
                    old.wait(timeout=10)
                _ready(runs, new)
                historical = _state(sup, rid, outcome)
                assert historical["cleanup_state"] == "confirmed"
                recover.assert_called_with(stage, **binding)
                if outcome == "completed":
                    assert json.loads(historical["receipt_json"])["run_id"] == rid
                (runs / new / "release").touch()
                _state(sup, new, "completed")
        finally:
            if old.poll() is None:
                old.terminate()
                old.wait(timeout=5)


def test_profile_deadline_terminates_worker_and_releases_slot(workspace_tmp_path):
    with _supervisor(workspace_tmp_path, deadlines={"qc": 2}) as (sup, store, runs):
        rid = sup.submit(**_freeze(store), profile="qc")["run_id"]
        _ready(runs, rid)
        proc = psutil.Process(sup.status(rid)["pid"])
        expired = _state(sup, rid, "failed")
        assert expired["error_text"] == "plugin_worker_deadline_exceeded"
        assert expired["cleanup_state"] == "confirmed" and not _alive(proc)
        _until(lambda: sup.active_count() == 0)


def _check_fastq_deadline(workspace, rid):
    from devbench.analysis import fastq_execution
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform import plugin_mcp
    from devbench.platform.plugin_jobs import JobStore

    jobs = JobStore(workspace)
    plan = jobs.start(rid)
    proof = workspace / "runs" / rid / "reviewed-timeout"

    admit_resources = fastq_execution._resources

    def stop_before_launch(resources, timeout):
        # run_fastq_plan has already validated its transport and native time limit here.
        proof.write_text(json.dumps(admit_resources(resources, timeout)))
        raise ValueError("fixture stopped at reviewed FASTQ resource admission")

    with (
        patch.object(plugin_mcp, "_freeze_fastq", return_value=plan["fastq"]),
        patch.object(fastq_execution, "_resources", stop_before_launch),
        suppress(OperationBlocked),
    ):
        plugin_mcp._run_fastq(jobs, rid, plan)
    jobs.finish(rid, error="fixture_stopped_before_engine_launch")


def _gated_fastq_preflight(workspace, rid):
    """Pause the native FASTQ runner at a known preflight boundary."""
    from devbench.analysis import fastq_execution
    from devbench.daemon import supervisor
    from devbench.platform import plugin_mcp
    from devbench.platform.plugin_jobs import JobStore

    jobs = JobStore(workspace)
    plan = jobs.start(rid)
    root = workspace / "runs" / rid
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    phase = plan["fixture_preflight"]

    def pause():
        (root / "ready").touch()
        _until((root / "release-preflight").exists, timeout=60)

    if phase == "legacy_missing":
        attempt = root / "nextflow" / ("fastq-" + rid)
        attempt.mkdir(mode=0o700, parents=True)
        jobs.stage(rid, nextflow_attempt=str(attempt))
        pause()
        return

    def admit(*_args):
        if phase == "input_check":
            pause()
        if phase == "stale_prelaunch":
            stage = json.loads(jobs.managed_run(rid)["stage_json"])
            receipt = Path(stage["nextflow_attempt"]) / "result.json"
            (root / "before-probe.json").write_bytes(receipt.read_bytes())
        return {
            "design": {"formula": "~condition", "contrast": ["case", "control"]},
            "input_files": [],
        }

    def verify(_frozen):
        if phase == "after_probe":
            pause()

    command = fastq_execution._execute_command

    def run_command(argv, **kwargs):
        if phase in {"main_intent", "stale_prelaunch"} and "-log" in argv:
            pause()
        return command(argv, **kwargs)

    executable = root / "nextflow-fixture-bin" / "nextflow"
    executable.parent.mkdir(mode=0o700)
    executable.write_text(
        f"#!{Path(sys.executable).resolve()}\n"
        "import os, sys, time\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['-version']\n"
        f"root=Path({str(root)!r})\n(root/'probe-pid').write_text(str(os.getpid()))\n"
        + (
            "(root/'ready').touch()\ntime.sleep(50)\n"
            if phase in {"live_probe", "altered_probe"}
            else ""
        )
        + f"print('Nextflow version {fastq_execution.NEXTFLOW_VERSION}')\n"
    )
    executable.chmod(0o700)
    binding = {"endpoint": "unix:///test/isolated.sock", "daemon_id": "original"}
    with (
        patch.dict(
            os.environ, {"PATH": str(executable.parent) + os.pathsep + os.defpath}
        ),
        patch.object(plugin_mcp, "_freeze_fastq", return_value=plan["fastq"]),
        patch.object(fastq_execution, "_admit_fastq_inputs", admit),
        patch.object(fastq_execution, "_verify_input_files", verify),
        patch.object(
            fastq_execution, "_prepare_engine", return_value=root / "quantification"
        ),
        patch.object(supervisor, "nextflow_docker_binding", return_value=binding),
        patch.object(fastq_execution, "_execute_command", run_command),
    ):
        plugin_mcp._run_fastq(jobs, rid, plan)


def _check_preflight_evidence(root, stage, phase, probe):
    from devbench.platform.plugin_jobs import digest

    result = Path(stage["nextflow_attempt"]) / "result.json"
    if phase == "legacy_missing":
        assert not result.exists()
    else:
        record = _json(result)
        assert record["daemon_prelaunch"]["attempt_dir"] == str(result.parent)
        assert digest(record["daemon_prelaunch"]) == stage["nextflow_prelaunch_sha256"]
        if phase in {"main_intent", "stale_prelaunch"}:
            assert stage["nextflow_engine_launch_intent"] is True
            assert record["engine_execution"] == "launch_intent" and not record.get(
                "engine_process_id"
            )
        else:
            assert (
                record["engine_launch_attempt_count"]
                == record["engine_launch_count"]
                == 0
            )
        if probe is not None:
            assert _alive(probe) and os.getsid(probe.pid) == probe.pid
            assert stage["nextflow_version_probe"] == record["version_probe"]
        if phase == "after_probe":
            assert stage["nextflow_version_probe_finished"] is True
        if phase == "stale_prelaunch":
            result.write_bytes((root / "before-probe.json").read_bytes())
        if phase == "altered_probe":
            record["version_probe"]["create_time"] -= 60
            result.write_text(json.dumps(record))


@pytest.mark.parametrize(
    ("phase", "trigger"),
    [
        ("input_check", "cancel"),
        ("input_check", "crash"),
        ("live_probe", "crash"),
        ("altered_probe", "crash"),
        ("after_probe", "cancel"),
        ("main_intent", "crash"),
        ("stale_prelaunch", "crash"),
        ("legacy_missing", "cancel"),
    ],
)
def test_fastq_prelaunch_interrupt_requires_affirmative_custody(
    workspace_tmp_path,
    monkeypatch,
    phase,
    trigger,
):
    from devbench.daemon import supervisor

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    docker = Mock(
        side_effect=AssertionError("no engine or labeled container is proven")
    )
    monkeypatch.setattr(supervisor, "_nextflow_containers", docker)
    with _supervisor(
        workspace_tmp_path,
        worker_mode="fastq-preflight",
        cpu_slots=1,
        max_concurrent_runs=1,
        heartbeat_sec=60,
    ) as (sup, store, runs):
        plan = _freeze(
            store,
            operation="qc-salmon",
            input_root=str(inputs),
            profiles=[],
            fastq={"source": {}},
            fixture_preflight=phase,
            design={
                "parameters": {"tx2gene": "fixture.tsv", "quantification_only": True}
            },
            mounted_image_id=None,
        )
        rid = sup.submit(**plan)["run_id"]
        root = _ready(runs, rid)
        followup = sup.submit(**_freeze(store))["run_id"]
        assert sup.status(followup)["status"] == "queued"
        row = sup.status(rid)
        stage = json.loads(row["stage_json"])
        probe = (
            psutil.Process(int((root / "probe-pid").read_text()))
            if phase in {"live_probe", "altered_probe"}
            else None
        )
        try:
            _check_preflight_evidence(root, stage, phase, probe)
            if trigger == "cancel":
                sup.cancel_run(rid)
            else:
                psutil.Process(row["pid"]).kill()

            def disposition():
                cleanup = sup.status(rid)["cleanup_state"]
                return cleanup if cleanup in {"confirmed", "unconfirmed"} else None

            if phase in {
                "main_intent",
                "legacy_missing",
                "stale_prelaunch",
                "altered_probe",
            }:
                assert _until(disposition) == "unconfirmed"
                assert sup.status(followup)["status"] == "queued"
                if probe is not None:
                    assert _alive(probe)
                sup.cancel_run(followup)
            else:
                assert _until(disposition) == "confirmed"
                if probe is not None:
                    assert not _alive(probe)
                following = _ready(runs, followup)
                (following / "release").touch()
                _state(sup, followup, "completed")
            docker.assert_not_called()
        finally:
            if probe is not None and _alive(probe):
                probe.kill()
                probe.wait(timeout=5)


@pytest.mark.parametrize(("quant_only", "cpus"), [(True, 1), (False, 2)])
def test_multiday_profile_reaches_reviewed_fastq_resource_admission(
    workspace_tmp_path,
    quant_only,
    cpus,
):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    with _supervisor(
        workspace_tmp_path,
        worker_mode="reviewed-timeout",
        cpu_slots=cpus,
        memory_budget_mb=3072,
    ) as (
        sup,
        store,
        runs,
    ):
        identity = _freeze(
            store,
            operation="qc-salmon",
            input_root=str(inputs),
            profiles=[],
            fastq={"source": {}},
            design={
                "parameters": {
                    "tx2gene": "fixture.tsv",
                    "quantification_only": quant_only,
                }
            },
            mounted_image_id=None,
        )
        with pytest.raises((ValueError, RuntimeError)):
            sup.submit(**identity, profile="long", memory_mb=1023)
        with pytest.raises(ValueError):
            sup.submit(**identity, pool="gpu", memory_mb=2048)
        if cpus == 1:
            full_de = _freeze(
                store,
                operation="qc-salmon",
                design={"parameters": {"quantification_only": False}},
            )
            with pytest.raises(ValueError, match="pool capacity"):
                sup.submit(**full_de, memory_mb=2048)
        assert sup.active_count() == 0
        rid = sup.submit(**identity, profile="long", memory_mb=2560)["run_id"]
        row = _state(sup, rid, "failed")
        assert row["error_text"] == "fixture_stopped_before_engine_launch"
        limits = _json(runs / rid / "reviewed-timeout")
        assert 86400 < limits["timeout_sec"] <= row["deadline_sec"] == 259200
        assert limits["cpus"] == json.loads(row["stage_json"])["_cpu_slots"] == cpus
        assert limits["memory_gb"] * 1024 == row["memory_mb"] == 2048


def test_paused_worker_heartbeat_becomes_stale_and_recovers(workspace_tmp_path):
    with _supervisor(workspace_tmp_path) as (sup, store, runs):
        rid = sup.submit(**_freeze(store))["run_id"]
        root = _ready(runs, rid)
        path = store.workspace / ".devbench-daemon" / "runs" / rid / "progress.json"
        worker = psutil.Process(sup.status(rid)["pid"])

        def snapshot(predicate):
            def observe():
                progress = _json(path)
                return progress if predicate(progress) else None

            return _until(observe, timeout=8)

        initial = snapshot(
            lambda p: p["worker_heartbeat_utc"] and not p["worker_heartbeat_stale"]
        )
        try:
            worker.suspend()
            try:
                stale = snapshot(lambda p: p["worker_heartbeat_stale"])
                last_pulse = datetime.fromisoformat(stale["worker_heartbeat_utc"])
                reported = datetime.fromisoformat(stale["updated_at_utc"])
                assert (reported - last_pulse).total_seconds() > 3 * sup.heartbeat_sec
                assert last_pulse >= datetime.fromisoformat(
                    initial["worker_heartbeat_utc"]
                )
                # The supervisor keeps sampling RSS while the worker cannot report.
                later = snapshot(
                    lambda p: p["updated_at_utc"] != stale["updated_at_utc"]
                )
                assert later["worker_heartbeat_utc"] == stale["worker_heartbeat_utc"]
                assert later["worker_heartbeat_stale"] and later["rss_mb"] > 0
                assert _alive(worker) and sup.status(rid)["status"] == "running"
            finally:
                worker.resume()
            snapshot(
                lambda p: not p["worker_heartbeat_stale"]
                and datetime.fromisoformat(p["worker_heartbeat_utc"]) > last_pulse
            )
        finally:
            (root / "release").touch()
        _state(sup, rid, "completed")


def _crash_at_publication():
    from devbench.platform.plugin_jobs import JobStore, worker_main

    original_finish = JobStore.finish

    def finish(self, rid, *, receipt=None, error=None):
        if receipt is not None and error is None:
            os.kill(os.getpid(), signal.SIGKILL)
        return original_finish(self, rid, receipt=receipt, error=error)

    JobStore.finish = finish
    worker_main()


def _fixture_qc_metrics(data, masks, *, percent_top):
    """Compute the fixture's count reductions independently of optional Scanpy."""
    import numpy as np
    from scipy import sparse

    counts = data.X.toarray() if sparse.issparse(data.X) else np.asarray(data.X)
    totals = counts.sum(axis=1)
    assert (totals > 0).all()
    fields = {"total_counts": totals, "n_genes_by_counts": (counts > 0).sum(axis=1)}
    for name, mask in masks.items():
        fields["total_counts_" + name] = counts[:, mask].sum(axis=1)
        data.obs["pct_counts_" + name] = 100 * fields["total_counts_" + name] / totals
    for name, values in fields.items():
        data.obs[name] = values
        data.obs["log1p_" + name] = np.log1p(values)
    for n in percent_top or []:
        data.obs[f"pct_counts_in_top_{n}_genes"] = (
            100 * np.sort(counts)[:, -n:].sum(axis=1) / totals
        )


def _fixture_hvg(counts, means, m2, feature_names, n_top_genes):
    """Select fixture features by pooled within-batch variance."""
    import numpy as np
    import pandas as pd

    pooled_variance = m2.sum(axis=0) / (counts.sum() - len(means))
    selected = np.argsort(-pooled_variance, kind="stable")[:n_top_genes]
    return pd.DataFrame(
        {
            "fixture_variance": pooled_variance,
            "highly_variable": np.isin(np.arange(len(feature_names)), selected),
        },
        index=feature_names,
    )


def _host_single_cell_kernel(stage, request, identity):
    """Drive native I/O with host transport and deterministic synthetic reductions."""
    from devbench.analysis import streaming
    from devbench.engine import mounted_kernel_oci, tier_a_scrna
    from rna_kernels.mounted_scrna import run_mounted_scrna

    def run_local(directory, *, request_sha, **_options):
        run_mounted_scrna(directory / "inputs", directory / "outputs", request_sha)
        return {
            "error_type": None,
            "cleanup_confirmed": True,
            "test_transport": "host_fixture",
        }

    with (
        patch.object(mounted_kernel_oci, "run_mounted_container", run_local),
        patch.object(streaming, "calculate_cell_qc", _fixture_qc_metrics),
        patch.object(streaming, "hvg_from_moments", _fixture_hvg),
    ):
        return tier_a_scrna.execute_mounted_scrna(stage, request, identity)


def _interrupt_single_cell_chain():
    from devbench.engine import tier_a_executor
    from devbench.platform.plugin_jobs import JobStore, worker_main

    save = JobStore.checkpoint

    def checkpoint(self, rid, *, step, state):
        manifest = save(self, rid, step=step, state=state)
        if state.get("phase") == "native_steps" and len(state["steps"]) == 1:
            control = self.workspace / ".devbench-daemon" / "runs" / rid
            if self.managed_run(rid)["resume_from_run_id"] is None:
                (control / "first-step-accepted").touch()
                _until((control / "continue-test").is_file, timeout=90)
        return manifest

    with (
        patch.object(
            tier_a_executor, "execute_mounted_scrna", _host_single_cell_kernel
        ),
        patch.object(JobStore, "checkpoint", checkpoint),
    ):
        worker_main()


@pytest.mark.parametrize(
    "tamper", [False, True], ids=["intact", "changed-intermediate"]
)
def test_native_single_cell_chain_resumes_only_after_verified_accepted_step(
    workspace_tmp_path, tamper
):
    from devbench.daemon.native_steps import accepted_step
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.plugin_mcp import Plan, Plugin, Run
    from devharness_controlled.native_contracts import single_cell_kernel_images
    from tests.test_tier_a_scrna import _source

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    image = sorted(single_cell_kernel_images())[0]
    source = _source(inputs)
    plugin = Plugin(workspace, inputs, image)
    expression = {"id": "input", "port": None}
    request = {
        "operation": "single_cell",
        "inputs": [{"path": str(source), "data_classification": "synthetic"}],
        "objective": "Check QC and preparation on synthetic cells.",
        "measurement": "umi_counts",
        "counts_layer": "counts",
        "batch_column": "batch",
        "count_provenance": "Simulated raw count layer",
        "parameters": {
            "capture_units_confirmed": True,
            "steps": [
                {
                    "operation": "scrna_batch_qc",
                    "parameters": {
                        "batch_key": "batch",
                        "qc_group_key": "batch",
                        "min_genes": 1,
                    },
                    "sources": {"expression": expression},
                },
                {
                    "operation": "scrna_prepare",
                    "parameters": {
                        "batch_key": "batch",
                        "n_top_genes": 24,
                        "normalization": "shifted_log",
                    },
                    "sources": {
                        "expression": expression,
                        "qc_metrics": {"id": "plugin-step-1", "port": "qc_metrics"},
                        "qc_features": {"id": "plugin-step-1", "port": "qc_features"},
                    },
                },
            ],
        },
    }
    frozen = plugin.plan(Plan.model_validate(request))
    identity = {key: frozen[key] for key in ("session_id", "plan_id", "plan_sha256")}
    with _supervisor(workspace_tmp_path, worker_mode="single-cell-chain") as (
        sup,
        store,
        runs,
    ):
        rid = sup.submit(**identity)["run_id"]
        marker = workspace / ".devbench-daemon" / "runs" / rid / "first-step-accepted"

        def reached_boundary():
            row = sup.status(rid)
            if row["status"] not in {"queued", "running"}:
                diag = runs / rid / "worker-error.log"
                detail = (
                    diag.read_text()[-1300:] if diag.is_file() else row["error_text"]
                )
                pytest.fail(f"Worker stopped before checkpoint: {detail}")
            return marker.is_file()

        _until(reached_boundary, timeout=60)
        flow = AdaptiveWorkflow(store.authority.ledger, identity["session_id"])
        verifier = TierAExecutor(flow.ledger, mounted_image_id=image)
        step1, step2 = [
            f"plugin-step-{identity['plan_id']}-{number}" for number in (1, 2)
        ]
        outcome, evidence = accepted_step(flow, verifier, step1)
        metrics = outcome["outputs"]["qc_metrics"]
        output = verifier._outputs.root / metrics["sha256"]
        assert hashlib.sha256(output.read_bytes()).hexdigest() == metrics["sha256"]
        psutil.Process(sup.status(rid)["pid"]).kill()
        assert _state(sup, rid, "interrupted")["cleanup_state"] == "confirmed"
        if tamper:
            output.chmod(0o600)
            output.write_bytes(b"changed fixture intermediate")
        recovered = sup.submit(**identity, resume_from_run_id=rid)
        assert recovered["run_id"] == rid
        final = _state(sup, rid, "failed" if tamper else "completed")
        dispatches = flow._read_projection(
            lambda db: [
                value["envelope"]["step"]["step_id"]
                for value in flow._state(db).dispatches.values()
            ]
        )
        assert dispatches.count(step1) == 1
        if tamper:
            assert final["error_text"] and step2 not in dispatches
        else:
            assert (
                dispatches.count(step2) == 1
                and accepted_step(flow, verifier, step1)[1] == evidence
            )
            receipt = plugin.receipt(Run(session_id=identity["session_id"], run_id=rid))
            assert receipt["metrics"]["steps_completed"] == 2


def _plan_matrix_audit(plugin, counts):
    from devbench.platform.plugin_mcp import Plan

    frozen = plugin.plan(
        Plan.model_validate(
            {
                "operation": "matrix_audit",
                "measurement": "non_umi_counts",
                "objective": "Audit these synthetic counts.",
                "inputs": [{"path": str(counts), "data_classification": "synthetic"}],
            }
        )
    )
    return {k: frozen[k] for k in ("session_id", "plan_id", "plan_sha256")}


@pytest.mark.parametrize("tamper", [False, True], ids=["verified", "changed-evidence"])
def test_native_audit_resumes_receipt_publication_without_recomputing(
    workspace_tmp_path,
    tamper,
):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.plugin_mcp import Plugin, Run

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    counts = inputs / "counts.csv"
    counts.write_text("gene,s1,s2\nA,1,2\nB,3,4\n")
    plugin = Plugin(workspace, inputs, None)
    identity = _plan_matrix_audit(plugin, counts)
    with _supervisor(workspace_tmp_path, worker_mode="crash_at_publication") as (
        sup,
        _,
        runs,
    ):
        rid = sup.submit(**identity)["run_id"]
        interrupted = _state(sup, rid, "interrupted")
        assert (
            interrupted["checkpoint_sha256"]
            and interrupted["cleanup_state"] == "confirmed"
        )
        manifest = runs / rid / "replay_bundle" / "manifest.json"
        original = manifest.read_bytes(), manifest.stat().st_mtime_ns
        if tamper:
            manifest.write_text("{}")
        # The verified outputs and the new receipt refer to the original scientific run.
        recovered = sup.submit(**identity, resume_from_run_id=rid)
        assert recovered["run_id"] == rid
        key = Run(session_id=identity["session_id"], run_id=rid)
        if tamper:
            assert _state(sup, rid, "failed")["error_text"]
            with pytest.raises(OperationBlocked):
                plugin.receipt(key)
            return
        _state(sup, rid, "completed")
        receipt = plugin.receipt(key)
        assert receipt["metrics"]["genes"] == 2 and receipt["run_id"] == rid
        assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == original


def test_same_run_receipt_resume_rebinds_capacity_after_restart(workspace_tmp_path):
    from devbench.platform.plugin_mcp import Plugin, Run

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    matrix = inputs / "matrix.csv"
    matrix.write_text("gene,s1,s2\nA,7,3\nB,4,6\n")
    plugin = Plugin(workspace, inputs, None)
    identity = _plan_matrix_audit(plugin, matrix)
    independent = _plan_matrix_audit(plugin, matrix)
    assert independent["plan_id"] != identity["plan_id"]

    with _supervisor(
        workspace_tmp_path,
        worker_mode="crash_at_publication",
        cpu_slots=2,
        memory_budget_mb=4096,
    ) as (first, _, _runs):
        rid = first.submit(**identity, pool="cpu", memory_mb=3072, profile="long")[
            "run_id"
        ]
        source = _state(first, rid, "interrupted")
        assert source["checkpoint_sha256"] and source["cleanup_state"] == "confirmed"
        assert source["memory_mb"] == 3072

    with _supervisor(
        workspace_tmp_path,
        worker_mode="native",
        cpu_slots=1,
        gpu_slots=1,
        memory_budget_mb=1024,
        max_concurrent_runs=1,
        deadlines={"qc": 75},
    ) as (small, _, _runs):

        def reservation(row):
            return (
                row["pool"],
                row["memory_mb"],
                row["profile"],
                row["deadline_sec"],
                json.loads(row["stage_json"])["_cpu_slots"],
            )

        current = small.submit(
            **identity, resume_from_run_id=rid, pool="gpu", memory_mb=512, profile="qc"
        )
        assert current["run_id"] == current["resume_from_run_id"] == rid
        expected = ("gpu", 512, "qc", 75, 1)
        assert reservation(small.status(rid)) == expected
        next_id = small.submit(**independent, pool="cpu", memory_mb=512)["run_id"]
        retry = dict(
            resume_from_run_id=rid, pool="cpu", memory_mb=768, profile="standard"
        )
        assert reservation(small.submit(**identity, **retry)) == expected
        for plan, run_id in ((identity, rid), (independent, next_id)):
            assert _state(small, run_id, "completed")["plan_id"] == plan["plan_id"]
            receipt = plugin.receipt(Run(session_id=plan["session_id"], run_id=run_id))
            assert receipt["run_id"] == run_id and receipt["metrics"]["genes"] == 2
        assert reservation(small.submit(**identity, **retry)) == expected


def test_legacy_studio_attaches_to_offline_management_owner_without_escalation(
    workspace_tmp_path,
):
    from devbench.daemon import ipc
    from devbench.daemon.auth import read_token
    from devbench.platform.plugin_jobs import (
        JobStore,
        ensure_offline_studio,
        studio_handoff,
    )

    workspace, inputs = workspace_tmp_path / "workspace", workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    try:
        owner = ipc.start_daemon(
            workspace, offline_only=True, allow_data_execution=False
        )
        assert owner["input_root"] is None
        token = read_token(workspace)
        assert ensure_offline_studio(workspace, inputs, None) is None
        live = ipc.daemon_status(workspace)
        assert live["input_root"] == str(inputs) and live["pid"] == owner["pid"]
        assert live["offline_only"] and not live.get("allow_data_execution")
        assert read_token(workspace) == token
        origin = live["http_url"]
        assert _http(origin, "/api/config")[0] == 401
        headers = {
            "Authorization": f"Bearer {token}",
            "Origin": origin.rstrip("/"),
            "Content-Type": "application/json",
        }
        status, _, body = _http(origin, "/api/config", headers=headers)
        config = json.loads(body)
        assert status == 200 and config["offline_only"] is True
        assert config.get("status") != "awaiting_input_root"
        assert _json(workspace / "studio-server.json")["pid"] == owner["pid"]
        frozen = _freeze(JobStore(workspace), "private")
        session = frozen.pop("session_id")
        handoff = studio_handoff(
            workspace, inputs, session, "workflows", allow_data_execution=True
        )
        assert handoff["studio_url"] == f"{origin}?session_id={session}&view=workflows"
        assert (
            _http(
                origin,
                f"/api/scientific/adaptive/{session}/plugin-execute",
                method="POST",
                headers=headers,
                body=json.dumps({**frozen, "allow_data_execution": True}),
            )[0]
            == 403
        )
        assert _authority(workspace).plan_approval(session_id=session, **frozen) is None
    finally:
        _stop_when_idle(workspace)


@pytest.mark.parametrize("owner", ["cold", "pinned", "racing-unpinned"])
def test_scientific_workbench_selects_local_image_only_for_new_daemon(
    workspace_tmp_path, monkeypatch, owner
):
    from devbench.daemon import ipc
    from devbench.engine import execution_snapshot
    from devbench.platform import workbench_ui
    from devharness_controlled.native_contracts import limma_voom_kernel_images

    workspace, inputs = workspace_tmp_path / "workspace", workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    registered = sorted(limma_voom_kernel_images())
    detect = Mock(return_value=registered[0])
    monkeypatch.setattr(workbench_ui, "_detect_mounted_image", detect)
    monkeypatch.setattr(
        execution_snapshot, "launch", lambda *a, **k: pytest.fail("Standalone fallback")
    )
    flags = [
        "--workspace",
        str(workspace),
        "--input-root",
        str(inputs),
        "--offline-only",
        "--enable-scientific",
        "--allow-data-execution",
        "--port",
        "0",
    ]
    try:
        current = None
        if owner != "cold":
            options = {"mounted_image_id": registered[-1]} if owner == "pinned" else {}
            current = ipc.start_daemon(
                workspace,
                input_root=inputs,
                offline_only=True,
                allow_data_execution=True,
                **options,
            )
        with monkeypatch.context() as attach:
            if owner == "racing-unpinned":
                # The daemon can become visible after the caller's first observation.
                status = ipc.daemon_status
                first = iter([{"status": "stopped", "workspace": str(workspace)}])
                attach.setattr(
                    ipc, "daemon_status", lambda path: next(first, None) or status(path)
                )
            workbench_ui.main(flags, pin_execution=True, interactive_adaptive=True)
        live = ipc.request(workspace, "status")
        if current is None:
            detect.assert_called_once_with()
            assert live["mounted_image_id"] == registered[0]
        else:
            detect.assert_not_called()
            assert live["pid"] == current["pid"]
            assert live.get("mounted_image_id") == current.get("mounted_image_id")
    finally:
        _stop_when_idle(workspace)


def test_slow_optional_image_discovery_leaves_time_for_daemon_readiness(
    workspace_tmp_path, monkeypatch
):
    from devbench.daemon import ipc
    from devbench.platform import workbench_ui

    workspace, inputs = workspace_tmp_path / "workspace", workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    clock = SimpleNamespace(monotonic=time.monotonic, sleep=time.sleep)

    def no_image():
        clock.monotonic = lambda: time.monotonic() + ipc._START_TIMEOUT + 1
        return None

    detect = Mock(side_effect=no_image)
    monkeypatch.setattr(ipc, "time", clock)
    monkeypatch.setattr(workbench_ui, "_detect_mounted_image", detect)
    spawn = ipc._spawn_daemon
    with ExitStack() as cleanup:

        def spawn_owned(*args):
            child = spawn(*args)
            # A broken starter can time out after forking but before discovery.
            cleanup.callback(child.wait, timeout=10)
            cleanup.callback(child.terminate)
            return child

        monkeypatch.setattr(ipc, "_spawn_daemon", spawn_owned)
        workbench_ui.main(
            [
                "--workspace",
                str(workspace),
                "--input-root",
                str(inputs),
                "--port",
                "0",
                "--offline-only",
                "--enable-scientific",
                "--allow-data-execution",
            ],
            pin_execution=True,
            interactive_adaptive=True,
        )
        detect.assert_called_once_with()
        live = ipc.request(workspace, "status")
        assert live["status"] == "running" and live.get("mounted_image_id") is None
        assert ipc.stop_daemon(workspace)["status"] == "stopped"


if __name__ == "__main__":
    if sys.argv[3] == "crash_at_publication":
        _crash_at_publication()
    elif sys.argv[3] == "legacy":
        _legacy_writer(Path(sys.argv[1]), sys.argv[2])
    elif sys.argv[3] == "nextflow":
        _detached_engine_worker(Path(sys.argv[1]), sys.argv[2])
    elif sys.argv[3] == "gated-workbench":
        _serve_with_gated_description(
            Path(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[4])
        )
    elif sys.argv[3] == "single-cell-chain":
        _interrupt_single_cell_chain()
    elif sys.argv[3] == "reviewed-timeout":
        _check_fastq_deadline(Path(sys.argv[1]), sys.argv[2])
    elif sys.argv[3] == "fastq-preflight":
        _gated_fastq_preflight(Path(sys.argv[1]), sys.argv[2])
    elif sys.argv[3] == "native":
        from devbench.platform.plugin_jobs import worker_main

        worker_main()
    else:
        _controlled_worker(Path(sys.argv[1]), sys.argv[2])
