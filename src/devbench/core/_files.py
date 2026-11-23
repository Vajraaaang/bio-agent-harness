"""Bounded reads and hashes of regular files under absolute custody paths."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from ._hashing import decode_receipt_json


@contextmanager
def _open_bounded(path: Path, limit: int) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    if (
        not path.is_absolute()
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError("Evidence paths must be absolute and contain no symlinks")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Evidence is not a bounded regular file")
        yield stream, info


def read_bounded_file(path: Path, limit: int) -> bytes:
    with _open_bounded(path, limit) as (stream, _):
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Evidence exceeded its byte budget while reading")
    return content


def hash_bounded_file(path: Path, limit: int) -> str:
    """Hash evidence with the same file admission and a one MiB read size."""
    digest, total = hashlib.sha256(), 0
    with _open_bounded(path, limit) as (stream, before):
        while chunk := stream.read(min(1_048_576, limit - total + 1)):
            total += len(chunk)
            if total > limit:
                raise ValueError("Evidence exceeded its byte budget while hashing")
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        before_identity = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_identity = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if total != before.st_size or before_identity != after_identity:
            raise ValueError("Evidence changed while hashing")
    return digest.hexdigest()


def read_bounded_json(path: Path, limit: int) -> Any:
    return decode_receipt_json(read_bounded_file(path, limit))
