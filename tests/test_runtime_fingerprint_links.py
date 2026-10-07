"""Runtime links may target already-admitted files, never broaden read access."""
import pytest
from devbench.study.execution import _tree_digest


def test_admitted_runtime_file_link_binds_link_and_target_bytes(tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    target = tmp_path / "Python"
    target.write_bytes(b"runtime-v1")
    link = root / "libpython.dylib"
    link.symlink_to(target)
    allowed = (root, target)
    first = _tree_digest(root, allowed_symlink_targets=allowed)
    target.write_bytes(b"runtime-v2")
    second = _tree_digest(root, allowed_symlink_targets=allowed)
    assert second != first
    link.unlink()
    link.symlink_to("../Python")
    assert _tree_digest(root, allowed_symlink_targets=allowed) != second


@pytest.mark.parametrize("kind", ["outside", "directory", "source", "default"])
def test_runtime_link_exceptions_do_not_weaken_other_fingerprints(tmp_path, kind):
    root = tmp_path / "config"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir() if kind == "directory" else target.write_bytes(b"runtime")
    (root / "link.py").symlink_to(target)
    kwargs = {} if kind == "default" else {"allowed_symlink_targets": (root,) if kind == "outside" else (root, target)}
    if kind == "source":
        kwargs["sources_only"] = True
    with pytest.raises(ValueError, match="symlink"):
        _tree_digest(root, **kwargs)
