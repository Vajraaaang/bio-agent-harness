"""Owner-only daemon state and bearer credentials, anchored to open directories."""

from __future__ import annotations

import hmac
import os
import pwd
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from devbench.engine.permissions import require_protected_storage

STATE_DIR = ".devbench-daemon"
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def local_actor() -> str:
    """Identify the local operator even on machines without a passwd entry."""
    uid = os.getuid()
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def workspace_path(workspace: Path) -> Path:
    path = Path(workspace)
    if not path.is_absolute() or path.parent == path or path.resolve() != path:
        raise ValueError("Use a canonical absolute daemon workspace")
    return require_protected_storage(path)


def verify_owner(info: os.stat_result, kind: int, mode: int) -> None:
    if (
        info.st_uid != os.getuid()
        or stat.S_IFMT(info.st_mode) != kind
        or stat.S_IMODE(info.st_mode) != mode
        or (kind in {stat.S_IFREG, stat.S_IFSOCK} and info.st_nlink != 1)
    ):
        raise PermissionError("Daemon state must be owner-only and unlinked elsewhere")


@contextmanager
def owned_directory(path: Path):
    fd = os.open(path, DIR_FLAGS)
    try:
        verify_owner(os.fstat(fd), stat.S_IFDIR, 0o700)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def state_directory(workspace: Path, *, create: bool = False):
    workspace = workspace_path(workspace)
    if create:
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = os.open(workspace, DIR_FLAGS)
    try:
        info = os.fstat(parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise PermissionError("Daemon workspace requires an owner-controlled root")
        if create:
            try:
                os.mkdir(STATE_DIR, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        fd = os.open(STATE_DIR, DIR_FLAGS, dir_fd=parent)
        try:
            verify_owner(os.fstat(fd), stat.S_IFDIR, 0o700)
            yield fd
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _linked_private_generation(fd: int) -> bool:
    info = os.fstat(fd)
    if (
        info.st_nlink == 0
        and info.st_uid == os.getuid()
        and stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o600
    ):
        # An atomic publication removed this generation from the directory.
        return False
    verify_owner(info, stat.S_IFREG, 0o600)
    return True


def read_file(directory_fd: int, name: str, limit: int) -> bytes:
    for _ in range(3):
        fd = os.open(name, FILE_FLAGS | os.O_RDONLY, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as stream:
            if not _linked_private_generation(fd):
                continue
            data = stream.read(limit + 1)
            if not _linked_private_generation(fd):
                continue
            if len(data) > limit:
                raise ValueError("Daemon state exceeds its size limit")
            return data
    raise PermissionError("Daemon state was repeatedly replaced during read")


def _existing_regular(directory_fd: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    verify_owner(info, stat.S_IFREG, 0o600)
    return True


def remove_file(directory_fd: int, name: str) -> None:
    if _existing_regular(directory_fd, name):
        os.unlink(name, dir_fd=directory_fd)


def write_file(directory_fd: int, name: str, data: bytes) -> None:
    # Keep the prior generation readable until the entire replacement is durable.
    _existing_regular(directory_fd, name)
    tmp = f".{name}-{secrets.token_hex(12)}"
    fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | FILE_FLAGS,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        os.replace(tmp, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(tmp, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


def read_token(workspace: Path) -> str:
    with state_directory(workspace) as fd:
        token = read_file(fd, "daemon.token", 65).decode("ascii").rstrip("\n")
    if not _TOKEN.fullmatch(token):
        raise ValueError("Invalid daemon credential")
    return token


def valid_bearer(header_value: str | None, token: str) -> bool:
    if not header_value or not _TOKEN.fullmatch(token):
        return False
    scheme, sep, value = header_value.partition(" ")
    return bool(
        sep
        and scheme.lower() == "bearer"
        and _TOKEN.fullmatch(value)
        and hmac.compare_digest(value, token)
    )
