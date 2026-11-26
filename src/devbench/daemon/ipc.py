"""Local daemon discovery, lifetime lock and newline-framed Unix transport."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from devbench.core._files import read_bounded_file

from . import auth

MAX_FRAME = 1024 * 1024
_MAX_RECORD = 16 * 1024
_START_TIMEOUT = 30
_LOOPBACK = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})/\Z")
_RECORD = "daemon.json"
_LOCK = "daemon.lock"
_SOCKET = "daemon.sock"


class DaemonError(RuntimeError):
    """A bounded daemon or control protocol failure."""


def _encode(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def _socket_name(workspace: Path) -> str:
    digest = hashlib.sha256(os.fsencode(workspace)).hexdigest()[:16]
    return f"s-{digest}.sock"


def _runtime_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise PermissionError("Runtime socket directory must be absolute")
    # macOS aliases these system roots. Never resolve a user-controlled symlink.
    if sys.platform == "darwin" and path.parts[1:2] in {("var",), ("tmp",)}:
        alias = Path("/", path.parts[1])
        real = Path("/private", path.parts[1])
        if alias.resolve() == real:
            path = real.joinpath(*path.parts[2:])
    if path.resolve() != path:
        raise PermissionError("Runtime socket directory must have no symlinks")
    return path


def _runtime_directory(base: Path) -> Path:
    fd = os.open(base, auth.DIR_FLAGS)
    try:
        info = os.fstat(fd)
        shared_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if info.st_uid not in {0, os.getuid()} or (
            info.st_mode & 0o022 and not shared_sticky
        ):
            raise PermissionError("Untrusted runtime socket directory")
        name = f"devbench-ipc-{os.getuid()}"
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
        except FileExistsError:
            pass
        child = os.open(name, auth.DIR_FLAGS, dir_fd=fd)
        try:
            auth.verify_owner(os.fstat(child), stat.S_IFDIR, 0o700)
        finally:
            os.close(child)
    finally:
        os.close(fd)
    return base / name


def socket_path(workspace: Path) -> Path:
    """Select a byte-length-safe address; create a protected fallback if needed."""
    workspace = auth.workspace_path(workspace)
    direct = workspace / auth.STATE_DIR / _SOCKET
    if len(os.fsencode(direct)) <= 96:
        return direct
    name = _socket_name(workspace)
    for value in (os.environ.get("XDG_RUNTIME_DIR"), os.environ.get("TMPDIR"), "/tmp"):
        if not value:
            continue
        base = _runtime_path(value)
        path = base / f"devbench-ipc-{os.getuid()}" / name
        if len(os.fsencode(path)) <= 96:
            try:
                _runtime_directory(base)
            except FileNotFoundError:
                continue
            return path
    raise ValueError("No short daemon socket address is available")


def _socket_address(workspace: Path, value: str) -> Path:
    if not isinstance(value, str):
        raise DaemonError("invalid_daemon_metadata")
    path = Path(value)
    direct = workspace / auth.STATE_DIR / _SOCKET
    if not path.is_absolute() or path.resolve() != path or len(os.fsencode(path)) > 96:
        raise PermissionError("Invalid daemon socket address")
    if path != direct and (
        path.parent.name != f"devbench-ipc-{os.getuid()}"
        or path.name != _socket_name(workspace)
    ):
        raise PermissionError("Daemon socket does not belong to this workspace")
    return path


def _check_socket(workspace: Path, value: str, *, remove: bool = False) -> Path:
    path = _socket_address(workspace, value)
    with auth.owned_directory(path.parent) as directory:
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        auth.verify_owner(info, stat.S_IFSOCK, 0o600)
        if remove:
            os.unlink(path.name, dir_fd=directory)
    return path


def _record(directory: int) -> dict:
    data = json.loads(auth.read_file(directory, _RECORD, _MAX_RECORD))
    if not isinstance(data, dict):
        raise DaemonError("invalid_daemon_metadata")
    return data


def _remove_stale_socket(workspace: Path, value: str) -> None:
    address = _check_socket(workspace, value)
    before = address.lstat()
    # Metadata can be absent after a crash during startup. Prove that the
    # recorded address no longer has a listener before unlinking its inode.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        try:
            probe.connect(str(address))
        except ConnectionRefusedError:
            pass
        else:
            raise DaemonError("daemon_socket_in_use")
    with auth.owned_directory(address.parent) as directory:
        after = os.stat(address.name, dir_fd=directory, follow_symlinks=False)
        if not os.path.samestat(before, after):
            raise DaemonError("daemon_socket_changed")
        os.unlink(address.name, dir_fd=directory)


def _is_live(record: dict) -> bool:
    pid, created = record.get("pid"), record.get("create_time")
    if (
        type(pid) is not int
        or pid <= 0
        or type(created) not in (int, float)
        or not math.isfinite(created)
    ):
        raise DaemonError("invalid_daemon_identity")
    try:
        process = psutil.Process(pid)
        return (
            process.create_time() == created
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as error:
        raise DaemonError("daemon_identity_unavailable") from error


def _origin(value: Any) -> bool:
    match = _LOOPBACK.fullmatch(value) if isinstance(value, str) else None
    return bool(match and 0 < int(match.group(1)) <= 65535)


def _locked(directory: int) -> bool:
    fd = os.open(_LOCK, auth.FILE_FLAGS | os.O_RDONLY, dir_fd=directory)
    try:
        auth.verify_owner(os.fstat(fd), stat.S_IFREG, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


def daemon_status(workspace: Path) -> dict:
    """Discover a running daemon without creating a directory, lock, or socket."""
    workspace = auth.workspace_path(workspace)
    stopped = {"status": "stopped", "workspace": str(workspace)}
    try:
        with auth.state_directory(workspace) as directory:
            record = _record(directory)
            if not _is_live(record):
                return stopped
            if not _locked(directory):
                raise DaemonError("daemon_lifetime_lock_missing")
            if record.get("workspace") != str(workspace) or not _origin(
                record.get("http_url")
            ):
                raise DaemonError("invalid_daemon_metadata")
            _check_socket(workspace, record.get("socket_path"))
            return {**record, "status": "running"}
    except FileNotFoundError:
        return stopped


def publish_daemon(
    workspace: Path, socket_path: Path, http_url: str, config: dict
) -> dict:
    if not http_url.endswith("/"):
        http_url += "/"
    if not _origin(http_url):
        raise ValueError("Expected a canonical loopback HTTP URL")
    record = {
        **config,
        "workspace": str(workspace),
        "pid": os.getpid(),
        "create_time": psutil.Process().create_time(),
        "socket_path": str(socket_path),
        "http_url": http_url,
        "started_at_utc": datetime.now(UTC).isoformat(),
    }
    encoded = _encode(record)
    if len(encoded) > _MAX_RECORD:
        raise ValueError("Daemon configuration exceeds its size limit")
    with auth.state_directory(workspace) as directory:
        try:
            old = _record(directory)
        except FileNotFoundError:
            pass
        else:
            if (
                old.get("pid") == record["pid"]
                and old.get("create_time") == record["create_time"]
            ):
                record["started_at_utc"] = old["started_at_utc"]
                encoded = _encode(record)
        auth.write_file(directory, _RECORD, encoded)
    return {**record, "status": "running"}


def _reap(workspace: Path, directory: int, selected: Path) -> None:
    previous = None
    try:
        previous = _record(directory)
    except FileNotFoundError:
        pass
    paths = {str(selected)}
    if previous is not None:
        if _is_live(previous):
            raise DaemonError("daemon_already_running")
        if "socket_path" in previous:
            paths.add(str(_socket_address(workspace, previous["socket_path"])))
    for address in paths:
        try:
            _remove_stale_socket(workspace, address)
        except FileNotFoundError:
            pass
    auth.remove_file(directory, _RECORD)
    auth.remove_file(directory, "daemon.token")


@contextmanager
def daemon_listener(workspace: Path, lock_fd: int):
    """Own the inherited flock until the listener, state and credentials are retired."""
    with auth.state_directory(workspace) as directory:
        info = os.fstat(lock_fd)
        os.set_inheritable(lock_fd, False)
        auth.verify_owner(info, stat.S_IFREG, 0o600)
        if not os.path.samestat(
            info, os.stat(_LOCK, dir_fd=directory, follow_symlinks=False)
        ):
            raise DaemonError("invalid_daemon_lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        address = socket_path(workspace)
        _reap(workspace, directory, address)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(address))
            try:
                os.chmod(address, 0o600, follow_symlinks=False)
                token = secrets.token_hex(32)
                auth.write_file(directory, "daemon.token", token.encode())
                listener.listen(64)
                yield listener, address, token
            finally:
                # No subsequent generation can bind until this descriptor closes.
                auth.remove_file(directory, _RECORD)
                auth.remove_file(directory, "daemon.token")
                _check_socket(workspace, str(address), remove=True)


def _connect(workspace: Path, record: dict, *, timeout: float) -> socket.socket:
    addr = _check_socket(workspace, record["socket_path"])
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(timeout)
        client.connect(str(addr))
        # A concurrent restart can reuse the same socket pathname with a new PID.
        live = daemon_status(workspace)
        if (live.get("pid"), live.get("create_time")) != (
            record["pid"],
            record["create_time"],
        ):
            raise DaemonError("daemon_generation_changed")
    except BaseException:
        client.close()
        raise
    return client


def _send(client: socket.socket, op: str, fields: dict) -> None:
    data = _encode({"op": op, **fields})
    if len(data) > MAX_FRAME:
        raise ValueError("Daemon request exceeds its byte budget")
    client.sendall(data)


def _response(stream) -> dict:
    raw = stream.readline(MAX_FRAME + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise DaemonError("invalid_daemon_response")
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise DaemonError("invalid_daemon_response")
    if envelope.get("ok") is not True:
        error = envelope.get("error")
        if not isinstance(error, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,95}", error
        ):
            error = "daemon_request_refused"
        raise DaemonError(error)
    if not isinstance(envelope.get("result"), dict):
        raise DaemonError("invalid_daemon_response")
    return envelope["result"]


def _request(workspace: Path, record: dict, op: str, fields: dict) -> dict:
    if record["status"] != "running":
        raise DaemonError("daemon_not_running")
    timeout = 60
    if op in {"cancel", "cancel_run"}:
        timeout += record.get("grace_sec", 15)
    with (
        _connect(workspace, record, timeout=timeout) as client,
        client.makefile("rb") as stream,
    ):
        _send(client, op, fields)
        return _response(stream)


def request(workspace: Path, op: str, **fields) -> dict:
    return _request(workspace, daemon_status(workspace), op, fields)


def _input_root(workspace: Path, value: Path | None) -> str | None:
    if value is None:
        try:
            service = json.loads(
                read_bounded_file(workspace / "service.json", 512 * 1024)
            )
        except FileNotFoundError:
            return None
        roots = service.get("input_roots")
        if not isinstance(roots, list) or len(roots) != 1:
            raise ValueError("Choose an explicit input root for this workspace")
        value = Path(roots[0])
    root = Path(value)
    if (
        not root.is_absolute()
        or root.resolve(strict=True) != root
        or not root.is_dir()
        or root.is_relative_to(workspace)
        or workspace.is_relative_to(root)
    ):
        raise ValueError("Input root must be a disjoint canonical directory")
    return str(root)


def _config(
    workspace: Path,
    input_root: Path | None,
    http_port: int,
    idle_timeout_sec: float,
    offline_only: bool,
    enable_streamable_http: bool,
    defer_studio: bool,
    options: dict,
) -> dict:
    allowed = {
        "cpu_slots",
        "gpu_slots",
        "memory_budget_mb",
        "max_concurrent_runs",
        "grace_sec",
        "deadlines",
        "mounted_image_id",
        "allow_data_execution",
    }
    if set(options) - allowed:
        raise ValueError("Unknown daemon configuration option")
    if type(http_port) is not int or not 0 <= http_port <= 65535:
        raise ValueError("Expected a loopback port between 0 and 65535")
    if (
        type(idle_timeout_sec) not in (int, float)
        or not math.isfinite(idle_timeout_sec)
        or idle_timeout_sec < 0
    ):
        raise ValueError("Expected a nonnegative idle shutdown interval")
    if any(
        type(v) is not bool
        for v in (offline_only, enable_streamable_http, defer_studio)
    ):
        raise ValueError("Invalid daemon transport options")
    root = (
        None
        if defer_studio and input_root is None
        else _input_root(workspace, input_root)
    )
    return {
        "input_root": root,
        "http_port": http_port,
        "idle_timeout_sec": idle_timeout_sec,
        "offline_only": offline_only,
        "enable_streamable_http": enable_streamable_http,
        **{name: value for name, value in options.items() if value is not None},
    }


def _attach(workspace: Path, record: dict, config: dict, options: dict) -> dict:
    # A default-valued setting permits discovery of an already configured daemon.
    # An explicit capability request cannot silently weaken that daemon's policy.
    fields = {name for name, value in options.items() if value is not None}
    fields.update(k for k in ("offline_only", "enable_streamable_http") if config[k])
    if config["idle_timeout_sec"] != 1800:
        fields.add("idle_timeout_sec")
    if any(config.get(key) != record.get(key) for key in fields):
        raise DaemonError("daemon_configuration_mismatch")
    if config["http_port"] and config["http_port"] != int(
        _LOOPBACK.fullmatch(record["http_url"]).group(1)
    ):
        raise DaemonError("daemon_configuration_mismatch")
    root = config["input_root"]
    if root and record.get("input_root") != root:
        if record.get("input_root") is not None:
            raise DaemonError("daemon_input_root_mismatch")
        return request(workspace, "attach_input", input_root=root)
    return record


def _macos_sqlite_environment(env: dict[str, str]) -> None:
    if (
        sys.platform != "darwin"
        or env.get("DYLD_LIBRARY_PATH")
        or env.get("LD_LIBRARY_PATH")
    ):
        return
    from devbench.engine.scientific_authority import require_patched_wal
    from devbench.engine.workflow_operations import OperationConflict

    try:
        require_patched_wal()
    except OperationConflict:
        # macOS strips DYLD_* when a long console-script shebang goes through
        # /bin/sh. The detached interpreter performs its own WAL check on startup.
        prefixes = ("/opt/homebrew", "/usr/local")
        if os.uname().machine == "x86_64":
            prefixes = tuple(reversed(prefixes))
        for prefix in prefixes:
            lib = Path(prefix) / "opt/sqlite/lib"
            if (lib / "libsqlite3.dylib").is_file():
                env["DYLD_LIBRARY_PATH"] = str(lib)
                return
        raise


def _spawn_daemon(workspace: Path, config: dict, fd: int):
    source = Path(__file__).resolve().parents[2]
    roots = [str(source)]
    if source.name == "src" and (source.parent / "pyproject.toml").is_file():
        roots.append(str(source.parent))
    script = (
        "import json, os, sys; from pathlib import Path; "
        f"sys.path[:0] = {roots!r}; os.umask(0o077); "
        "from devbench.daemon.server import serve; "
        "serve(Path(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]))"
    )
    env = {
        key: os.environ[key]
        for key in (
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "XDG_RUNTIME_DIR",
            "DYLD_LIBRARY_PATH",
            "LD_LIBRARY_PATH",
            "VECLIB_MAXIMUM_THREADS",
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
        )
        if key in os.environ
    }
    _macos_sqlite_environment(env)
    return subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            script,
            str(workspace),
            _encode(config).decode(),
            str(fd),
        ],
        cwd=workspace,
        env=env,
        pass_fds=(fd,),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _try_start(workspace: Path, directory: int, config: dict):
    flags = os.O_RDWR | auth.FILE_FLAGS
    try:
        fd = os.open(_LOCK, flags, dir_fd=directory)
    except FileNotFoundError:
        try:
            fd = os.open(_LOCK, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        except (FileExistsError, FileNotFoundError):
            # On Darwin competing first openat(O_CREAT | O_NOFOLLOW) calls can
            # transiently report ENOENT. Retry against the registered directory.
            if not os.path.samestat(
                os.fstat(directory), (workspace / auth.STATE_DIR).lstat()
            ):
                raise
            return None
    try:
        auth.verify_owner(os.fstat(fd), stat.S_IFREG, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        # pass_fds shares the open file description and its flock across exec.
        # Closing this process's copy is safe; explicitly unlocking it is not.
        return _spawn_daemon(workspace, config, fd)
    finally:
        os.close(fd)


def start_daemon(
    workspace: Path,
    *,
    input_root: Path | None = None,
    http_port: int = 0,
    idle_timeout_sec: float = 1800,
    offline_only: bool = False,
    enable_streamable_http: bool = False,
    defer_studio: bool = False,
    **options,
) -> dict:
    workspace = auth.workspace_path(workspace)
    config = _config(
        workspace,
        input_root,
        http_port,
        idle_timeout_sec,
        offline_only,
        enable_streamable_http,
        defer_studio,
        options,
    )
    deadline = time.monotonic() + _START_TIMEOUT
    process = None
    with auth.state_directory(workspace, create=True) as directory:
        while time.monotonic() < deadline:
            record = daemon_status(workspace)
            if record["status"] == "running":
                return _attach(workspace, record, config, options)
            if process is None:
                process = _try_start(workspace, directory, config)
                if process is not None:
                    threading.Thread(target=process.wait, daemon=True).start()
            elif process.poll() is not None:
                raise DaemonError("daemon_start_failed")
            time.sleep(0.05)
    raise DaemonError("daemon_start_timeout")


def _reap_stopped(workspace: Path) -> None:
    # Status is side-effect free. An explicit stop may also retire a dead
    # generation when no live process or concurrent starter owns its lock.
    try:
        with auth.state_directory(workspace) as directory:
            fd = os.open(_LOCK, os.O_RDWR | auth.FILE_FLAGS, dir_fd=directory)
            try:
                auth.verify_owner(os.fstat(fd), stat.S_IFREG, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                previous = _record(directory)
                if not _is_live(previous):
                    selected = _socket_address(workspace, previous.get("socket_path"))
                    _reap(workspace, directory, selected)
            finally:
                os.close(fd)
    except (FileNotFoundError, BlockingIOError):
        pass


def stop_daemon(workspace: Path) -> dict:
    record = daemon_status(workspace)
    if record["status"] == "stopped":
        _reap_stopped(workspace)
        return record
    try:
        _request(workspace, record, "stop", {})
    except DaemonError as error:
        if str(error) != "daemon_stopping":
            raise
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError):
        if daemon_status(workspace)["status"] == "running":
            raise
    deadline = time.monotonic() + _START_TIMEOUT
    while _is_live(record):
        if time.monotonic() >= deadline:
            raise DaemonError("daemon_stop_timeout")
        time.sleep(0.05)
    return {"status": "stopped", "workspace": str(workspace)}


def _copy_stdin(client: socket.socket, source: int) -> None:
    try:
        while data := os.read(source, 65536):
            client.sendall(data)
        client.shutdown(socket.SHUT_WR)
    except OSError:
        # The peer can end its MCP session before the input pipe closes.
        pass


@contextmanager
def _mcp_connection(workspace: Path, input_root: Path, options: dict):
    deadline = time.monotonic() + _START_TIMEOUT
    while True:
        record = start_daemon(workspace, input_root=input_root, **options)
        with ExitStack() as stack:
            try:
                client = stack.enter_context(_connect(workspace, record, timeout=30))
                stream = stack.enter_context(client.makefile("rb"))
                _send(client, "mcp", {})
                _response(stream)
            except (ConnectionError, FileNotFoundError):
                pass
            except DaemonError as error:
                if str(error) not in {"daemon_stopping", "daemon_generation_changed"}:
                    raise
            else:
                # No application request has been forwarded before this point.
                yield client, stream
                return
        if time.monotonic() >= deadline:
            raise DaemonError("daemon_connect_timeout")
        time.sleep(0.05)


def stdio_shim(workspace: Path, input_root: Path, **options) -> int:
    """Bridge MCP bytes concurrently; closing this client only closes its session."""
    with _mcp_connection(workspace, input_root, options) as (client, stream):
        client.settimeout(None)
        threading.Thread(
            target=_copy_stdin,
            args=(client, sys.stdin.fileno()),
            daemon=True,
        ).start()
        while data := stream.read1(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    return 0
