"""Local partition custody. Only the trusted coordinator opens vault objects."""

import fcntl
import os
import re
import tempfile
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from rna_kernels.canonical import canonical_json

from .input_files import _open_file, _signature
from .population_data import bound_query_file

_MAX_PARTITION = 16_000_000
_SHA = re.compile(r"[a-f0-9]{64}")


def _path(value):
    path = Path(value)
    if (
        not path.is_absolute()
        or path != path.resolve()
        or any(parent.is_symlink() for parent in (path, *path.parents))
    ):
        raise ValueError("Canonical absolute paths without symlinks are required")
    if any(character in str(path) for character in (",", "\n", "\r")):
        raise ValueError("Invalid protected storage path")
    return path


def digest(value):
    return sha256(canonical_json(value)).hexdigest()


class PartitionVault:
    def __init__(self, root, *, max_bytes: int = _MAX_PARTITION):
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValueError("Partition vault requires a positive byte bound")
        self.max_bytes = max_bytes
        self.root = _path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise PermissionError("Partition vault must be private")

    def put(self, raw):
        if type(raw) is not bytes or len(raw) > self.max_bytes:
            raise ValueError("Partition object exceeds its byte bound")
        identity = sha256(raw).hexdigest()
        with tempfile.NamedTemporaryFile(dir=self.root) as pending:
            pending.write(raw)
            self._publish(pending, identity)
        with self.open(identity):
            pass
        return identity

    def put_file(self, source, *, expected_sha256=None):
        """Copy and bind a stable, singly linked staged file under the byte bound."""
        source = _path(source)
        root = source.parent.stat()
        digest = sha256()
        total = 0
        with tempfile.NamedTemporaryFile(dir=self.root) as pending:
            with _open_file(
                source.parent, (source.name,), (root.st_dev, root.st_ino)
            ) as fd:
                before = os.fstat(fd)
                if before.st_nlink != 1:
                    raise ValueError(
                        "Partition staging source requires exactly one link"
                    )
                if before.st_size > self.max_bytes:
                    raise ValueError("Partition object exceeds its byte bound")
                while block := os.read(
                    fd, min(1024 * 1024, self.max_bytes - total + 1)
                ):
                    total += len(block)
                    if total > self.max_bytes:
                        raise ValueError("Partition object exceeds its byte bound")
                    digest.update(block)
                    pending.write(block)
                after = os.fstat(fd)
                current = source.stat(follow_symlinks=False)
                if (
                    total != before.st_size
                    or after.st_nlink != 1
                    or current.st_nlink != 1
                    or _signature(before) != _signature(after)
                    or _signature(after) != _signature(current)
                ):
                    raise ValueError("Partition staging source changed during copy")
            identity = digest.hexdigest()
            if expected_sha256 is not None and identity != expected_sha256:
                raise ValueError("Partition staging differs from the frozen digest")

            self._publish(pending, identity)
        with self.open(identity):
            pass
        return identity

    def _publish(self, pending, identity):
        destination = _path(self.root / identity)
        pending.flush()
        os.fchmod(pending.fileno(), 0o400)
        os.fsync(pending.fileno())
        directory = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # A competing publisher must wait until the winning temporary link
            # disappears, since unlink updates the retained inode's ctime.
            fcntl.flock(directory, fcntl.LOCK_EX)
            try:
                os.link(
                    Path(pending.name).name,
                    destination.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            pending.close()
            os.fsync(directory)
        finally:
            os.close(directory)

    @contextmanager
    def open(self, identity):
        if type(identity) is not str or _SHA.fullmatch(identity) is None:
            raise ValueError("Partition objects require an exact content digest")
        with bound_query_file(
            _path(self.root / identity), identity, self.max_bytes
        ) as stream:
            yield stream
