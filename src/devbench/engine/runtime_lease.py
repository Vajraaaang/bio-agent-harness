"""Coordinator-owned, invocation-local reusable selector setup."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import time


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def environment_identity():
    from .watchdog import THREAD_ENVIRONMENT_KEYS

    keys = (*THREAD_ENVIRONMENT_KEYS, "PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE",
            "VIRTUAL_ENV", "CODEX_HOME", "CODEX_PERMISSION_PROFILE")
    return _digest({key: os.environ.get(key) for key in keys})


def _path_identity(path, *, allow_link=False):
    info = path.lstat()
    if info.st_uid not in {0, os.geteuid()}:
        raise ValueError("runtime_lease_untrusted_owner")
    fingerprint = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
    if stat.S_ISLNK(info.st_mode) and allow_link:
        target = path.resolve(strict=True)
        return (*fingerprint, os.readlink(path), str(target), _path_identity(target))
    if stat.S_ISDIR(info.st_mode):
        return fingerprint
    if stat.S_ISREG(info.st_mode):
        return (*fingerprint, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    raise ValueError("runtime_lease_unexpected_path_type")


def workspace_identity(workspace, sibling, roots):
    directories = (workspace, sibling, workspace / "control", workspace / ".venv")
    paths = {str(path): _path_identity(path) for path in directories}
    if any(not stat.S_ISDIR(paths[str(path)][2]) for path in directories):
        raise ValueError("runtime_lease_expected_owned_directories")
    if set(path.name for path in workspace.iterdir()) - {"control", "answer_schema.json", ".venv", ".tmp"}:
        raise ValueError("runtime_lease_unexpected_actor_state")
    for path in (workspace / "control", workspace / "control" / "context.json", workspace / "answer_schema.json"):
        paths[str(path)] = _path_identity(path)
    if set(path.name for path in (workspace / "control").iterdir()) != {"context.json"}:
        raise ValueError("runtime_lease_unexpected_control_state")
    for base, directories, files in os.walk(workspace / ".venv", followlinks=False):
        for path in (Path(base), *(Path(base) / name for name in directories + files)):
            if len(paths) >= 10_000:
                raise ValueError("runtime_lease_inventory_limit")
            paths[str(path)] = _path_identity(path, allow_link=True)
    for path in roots:
        paths[str(path)] = _path_identity(path)
    return _digest(paths)


@dataclass
class RuntimeLease:
    """State travels only in the trusted supervisor request/result, never actor output."""
    lease_id: str
    opened_at: float
    setup: dict | None = None
    turns: int = 0

    def validate(self, boundary, sibling, environment, executable, policy):
        state = self.setup
        if (time.monotonic() - self.opened_at > 3600 or state["policy"] != policy
                or state["environment_identity"] != environment_identity()
                or state["executable"] != str(executable)
                or state["boundary"] != _digest(boundary.config_overrides())
                or state["workspace_identity"] != workspace_identity(boundary.workspace, sibling, boundary.runtime_roots)):
            raise ValueError("runtime_lease_setup_changed")
        if environment != state["environment"]:
            raise ValueError("runtime_lease_environment_changed")
        from .workspace import read_bounded_file
        retained = read_bounded_file(Path(state["probe_path"]), 1_000_000)
        if hashlib.sha256(retained).hexdigest() != state["probe_sha256"]:
            raise ValueError("runtime_lease_probe_changed")
        scratch = boundary.workspace / ".tmp"
        if scratch.exists() or scratch.is_symlink():
            _path_identity(scratch)
            if not scratch.is_dir():
                raise ValueError("runtime_lease_invalid_scratch")
            if not shutil.rmtree.avoids_symlink_attacks:
                raise ValueError("runtime_lease_safe_scratch_cleanup_unavailable")
            shutil.rmtree(scratch)

    def remember(self, boundary, sibling, environment, executable, identity, probe, probe_path, policy):
        from .workspace import read_bounded_file

        retained = read_bounded_file(probe_path, 1_000_000)
        if json.loads(retained) != probe:
            raise ValueError("runtime_lease_probe_changed")
        self.setup = {"workspace": boundary.workspace, "sibling": sibling, "environment": environment,
            "environment_identity": environment_identity(), "executable": str(executable), "identity": identity,
            "boundary": _digest(boundary.config_overrides()), "probe": probe,
            "probe_sha256": hashlib.sha256(retained).hexdigest(), "probe_path": str(probe_path), "policy": policy,
            "workspace_identity": workspace_identity(boundary.workspace, sibling, boundary.runtime_roots)}
        self.turns += 1
