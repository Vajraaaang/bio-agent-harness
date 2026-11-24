"""The permission profile and transport share the engine's workspace checks."""
import os
from pathlib import Path
import stat
import tempfile

import pytest

from devbench.engine import workspace
from devbench.engine.permissions import PermissionBoundary


@pytest.fixture
def runtime_root(monkeypatch):
    checkout = Path(__file__).resolve().parents[1]
    runs = checkout / ".runs"
    sample_socket = runs / "s-abcdefgh" / ".runs" / "s" / "0123456789ab" / "s"
    if (
        len(os.fsencode(sample_socket)) > workspace.UNIX_SOCKET_PATH_MAX
        and (checkout / ".git").is_file()
    ):
        # A nested worktree can exhaust the socket budget before the lease checks.
        enclosing_checkout = next(
            (parent for parent in checkout.parents if (parent / ".git").is_dir()),
            checkout,
        )
        runs = enclosing_checkout / ".runs"
    workspace.protected_path(runs)
    runs.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="s-", dir=runs) as directory:
        root = Path(directory) / ".runs"
        monkeypatch.setenv("DEVBENCH_RUNS_ROOT", str(root))
        yield root


def test_socket_admission_and_transport_reject_shared_lease(runtime_root):
    actor = runtime_root.parent / ("a" * 80)
    path = workspace.bounded_unix_socket(actor / "control" / "tools.sock")
    assert len(os.fsencode(path)) <= workspace.UNIX_SOCKET_PATH_MAX
    assert not path.parent.exists()
    boundary = PermissionBoundary(workspace=actor, socket_path=path)
    assert boundary.socket_path == path
    assert workspace.validate_socket_directory(path, create=True) == path
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    path.parent.chmod(0o755)
    with pytest.raises(ValueError, match="private and owned"):
        PermissionBoundary(workspace=actor, socket_path=path)
    with pytest.raises(ValueError, match="private and owned"):
        workspace.validate_socket_directory(path, create=True)


def test_relocated_socket_rejects_symlink_ancestors(runtime_root):
    runtime_root.mkdir(mode=0o700)
    target = runtime_root.parent / "other"
    target.mkdir(mode=0o700)
    (runtime_root / "s").symlink_to(target, target_is_directory=True)
    preferred = runtime_root.parent / ("a" * 80) / "control" / "tools.sock"
    with pytest.raises(ValueError, match="symlinks"):
        workspace.bounded_unix_socket(preferred)


def test_relocated_socket_rejects_writable_ancestors(runtime_root):
    parent = runtime_root.parent
    parent.chmod(0o770)
    preferred = parent / ("a" * 80) / "control" / "tools.sock"
    with pytest.raises(ValueError, match="ancestry"):
        workspace.bounded_unix_socket(preferred)


def test_relocated_socket_cannot_override_private_root(runtime_root):
    actor = runtime_root.parent / ("a" * 80)
    socket = workspace.bounded_unix_socket(actor / "control" / "tools.sock")
    with pytest.raises(ValueError, match="intersects private"):
        PermissionBoundary(workspace=actor, private_roots=(runtime_root,), socket_path=socket)


def test_relocated_socket_cannot_live_under_actor_write_grant(runtime_root):
    actor = runtime_root.parent
    socket = workspace.bounded_unix_socket(actor / ("a" * 80) / "tools.sock")
    with pytest.raises(ValueError, match="intersects the participant workspace"):
        PermissionBoundary(workspace=actor, socket_path=socket)


def test_checkout_actor_allocations_use_runs_and_keep_denied_sibling(runtime_root, monkeypatch):
    checkout = runtime_root.parent
    monkeypatch.setattr(workspace, "_REPOSITORY", checkout)
    actor, denied = workspace.allocate_actor_pair(checkout)
    assert actor.parent == denied.parent == runtime_root / "actors"
    assert actor.name.startswith("a-") and denied.name == "d-" + actor.name[2:]
    assert stat.S_IMODE(actor.stat().st_mode) == stat.S_IMODE(denied.stat().st_mode) == 0o700
    other = workspace.allocate_workspace(checkout, prefix="p-")
    assert other.parent == actor.parent and other not in {actor, denied}


def test_pinned_process_keeps_checkout_actor_redirection(runtime_root, monkeypatch):
    checkout = runtime_root.parent
    monkeypatch.setattr(workspace, "_REPOSITORY", runtime_root / "fixture" / "code_snapshot")
    monkeypatch.setenv("DEVBENCH_EXECUTION_SNAPSHOT_ID", "a" * 64)
    monkeypatch.setenv("DEVBENCH_SNAPSHOT_CHECKOUT", str(checkout))

    assert workspace.workspace_parent(checkout) == runtime_root / "actors"


def test_supervisor_keeps_exact_compatibility_writer_after_move(runtime_root):
    from devbench.study.contracts import RunBudget
    from devbench.study.supervision import validate_supervision_configuration, write_coordinator_json

    output = runtime_root / "output"
    _, admitted = validate_supervision_configuration(
        write_coordinator_json, (output / "attempt_result.json", {}), output, RunBudget())
    assert admitted == output
    assert not output.exists()
    with pytest.raises(TypeError, match="trusted coordinator"):
        validate_supervision_configuration(workspace.allocate_actor_pair, (), output, RunBudget())
