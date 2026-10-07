"""Hashing large runtime files must not allocate the complete file in memory."""
import hashlib
import os
import tracemalloc

import pytest


@pytest.mark.parametrize("owner", ["execution", "cli_identity"])
def test_runtime_digests_use_bounded_memory(tmp_path, owner):
    from devbench.study.execution import _file_digest
    from devbench.study.cli_identity import _digest
    path = tmp_path / "native-payload"
    block = b"x" * 1_048_576
    expected = hashlib.sha256()
    with path.open("wb") as stream:
        for _ in range(12):
            stream.write(block)
            expected.update(block)
    tracemalloc.start()
    try:
        result = (_file_digest if owner == "execution" else _digest)(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == expected.hexdigest()
    assert peak < 4_000_000


@pytest.mark.parametrize("kind", ["oversized", "symlink", "fifo"])
def test_streaming_keeps_existing_file_safety_guards(tmp_path, kind):
    from devbench.study.execution import _file_digest
    target = tmp_path / "file"
    target.write_bytes(b"12345")
    path = target
    if kind == "symlink":
        path = tmp_path / "link"
        path.symlink_to(target)
    elif kind == "fifo":
        path = tmp_path / "fifo"
        os.mkfifo(path)
    with pytest.raises(ValueError):
        _file_digest(path, limit=4 if kind == "oversized" else 100)
