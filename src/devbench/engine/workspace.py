"""Protected directories and staged Python read grants for owned execution."""

import hashlib
import os
import re
import stat
import sys
import sysconfig
import tempfile
import uuid
from pathlib import Path

from devbench._layout import PROJECT_ROOT as _REPOSITORY
from devbench.core._files import read_bounded_file as read_bounded_file

from .permissions import require_protected_storage

UNIX_SOCKET_PATH_MAX = 100


def protected_path(value):
    """Check the spelling before resolving a protected workspace path."""
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Protected paths must be absolute without traversal")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Protected paths must not contain symlinks")
    return require_protected_storage(path)


def runs_root():
    """Resolve the protected .runs directory for actors and tool sockets.

    Set DEVBENCH_RUNS_ROOT to a short absolute .runs path when the default
    checkout or home .runs path leaves insufficient space for relocated
    sockets. Existing ancestors must be owned by root or the coordinator
    and satisfy ``mode & 0o022 == 0``. The coordinator owns .runs, and the
    socket subdirectories must be private.
    """
    configured = os.environ.get("DEVBENCH_RUNS_ROOT")
    checkout = (_REPOSITORY / "pyproject.toml").is_file()
    default = (_REPOSITORY if checkout else Path.home().resolve()) / ".runs"
    root = protected_path(configured if configured else default)
    if root.name != ".runs":
        raise ValueError("Runtime root must be an absolute .runs directory")
    return root


def workspace_parent(parent):
    """Resolve a caller's actor root without creating it."""
    parent = Path(parent)
    checkout = _REPOSITORY
    admitted_checkout = os.environ.get("DEVBENCH_SNAPSHOT_CHECKOUT")
    if (
        admitted_checkout
        and _REPOSITORY.name == "code_snapshot"
        and re.fullmatch(
            r"[0-9a-f]{64}", os.environ.get("DEVBENCH_EXECUTION_SNAPSHOT_ID", "")
        )
    ):
        checkout = Path(admitted_checkout)
        if not checkout.is_absolute() or ".." in checkout.parts:
            raise ValueError(
                "Pinned checkout identity must be absolute without traversal"
            )
    if parent in {_REPOSITORY, checkout}:
        return protected_path(runs_root() / "actors")
    return protected_path(parent)


def _prepare_workspace_parent(parent):
    parent = workspace_parent(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return parent


def allocate_workspace(parent, *, prefix):
    if re.fullmatch(r"[a-z]-", prefix) is None:
        raise ValueError("Invalid actor workspace prefix")
    return Path(tempfile.mkdtemp(prefix=prefix, dir=_prepare_workspace_parent(parent)))


def allocate_actor_pair(parent):
    """Give the permission probe a fresh actor and disjoint denied sibling."""
    parent = _prepare_workspace_parent(parent)
    nonce = uuid.uuid4().hex[:10]
    workspace, sibling = parent / ("a-" + nonce), parent / ("d-" + nonce)
    workspace.mkdir(mode=0o700)
    try:
        sibling.mkdir(mode=0o700)
    except OSError:
        workspace.rmdir()
        raise
    return workspace, sibling


def short_socket_root():
    return protected_path(runs_root() / "s")


def _socket_size(path):
    return len(os.fsencode(path))


def _private_directory(
    path: Path, *, create: bool = False, exclusive: bool = False
) -> None:
    protected_path(path)
    if create:
        path.mkdir(mode=0o700, exist_ok=not exclusive)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if create:
            raise
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError(
            "Tool socket directories must be private and owned by the coordinator"
        )


def validate_relocated_socket(
    path: Path, *, create: bool = False, exclusive: bool = False
) -> None:
    """Validate relocated socket custody and optionally reserve its lease directory."""
    root = short_socket_root()
    if (
        path.parent.parent != root
        or path.name != "s"
        or re.fullmatch(r"[0-9a-f]{12}", path.parent.name) is None
    ):
        raise ValueError("Relocated tool socket must live under the short socket root")
    # .runs may also contain local reports. It must be owned and protected
    # against replacement, while the socket subtree itself must be private.
    runs = root.parent
    for ancestor in runs.parents:
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ValueError("Tool socket root ancestry must be protected from writes")
    if create:
        runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runs.exists():
        info = runs.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ValueError(
                "Tool socket run root must be owned and protected from writes"
            )
    _private_directory(root, create=create)
    _private_directory(path.parent, create=create, exclusive=exclusive)


def validate_tool_socket(value):
    path = protected_path(value)
    if _socket_size(path) > UNIX_SOCKET_PATH_MAX:
        raise ValueError("A short absolute nonsymlink Unix socket path is required")
    return path


def bounded_unix_socket(preferred):
    """Return an admitted short path without creating a socket or directory."""
    preferred = protected_path(preferred)
    if _socket_size(preferred) <= UNIX_SOCKET_PATH_MAX:
        return preferred
    digest = hashlib.sha256(os.fsencode(preferred)).hexdigest()[:12]
    candidate = short_socket_root() / digest / "s"
    if _socket_size(candidate) > UNIX_SOCKET_PATH_MAX:
        raise ValueError("unassigned_socket_path_too_long")
    validate_relocated_socket(candidate)
    return candidate


def validate_socket_directory(value, *, create=False):
    path = validate_tool_socket(value)
    if path.is_relative_to(short_socket_root()):
        validate_relocated_socket(path, create=create)
    else:
        if create:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_directory(path.parent)
    return path


def runtime_read_roots(workspace, environment):
    """Grant individual third-party entries and Python runtime components only."""
    if environment.get("runtime_source_hashes"):
        raise ValueError(
            "Study participants must not receive harness implementation source"
        )
    site = (
        workspace
        / ".venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    roots = {Path(sys.executable).resolve()}
    for name in environment["linked_dependency_entries"]:
        path = site / name
        if not path.is_symlink():
            raise ValueError("Expected individually linked ordinary dependency")
        roots.add(path.resolve())
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    # Grant stdlib entries individually: its site-packages sibling may contain
    # editable coordinator hooks and must never receive a broad read grant.
    roots.update(
        path.resolve()
        for path in stdlib.iterdir()
        if path.name not in {"site-packages", "dist-packages", "__pycache__"}
    )
    library = sysconfig.get_config_var("LDLIBRARY")
    library_dir = sysconfig.get_config_var("LIBDIR")
    if library and library_dir:
        path = (Path(library_dir) / library).resolve()
        if path.is_file():
            roots.add(path)
    if sys.platform == "darwin":
        framework = Path(sys.base_prefix) / "Python"
        if framework.is_file():
            roots.add(framework.resolve())
    return tuple(sorted(roots))
