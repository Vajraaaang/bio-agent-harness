"""Bounded, descriptor-based reads of local input files."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat


_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _directory(path):
    """Open every absolute directory component without following symlinks."""
    descriptor = os.open("/", _DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _open_file(root, parts, root_identity):
    directory = _directory(Path(root))
    descriptor = None
    try:
        actual = os.fstat(directory)
        if (actual.st_dev, actual.st_ino) != tuple(root_identity):
            raise ValueError("Configured input root identity changed; restart with reviewed roots")
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], _FILE, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Input must be a regular file, not a link, device or pipe")
        yield descriptor
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _hash_fd(descriptor, maximum):
    before = os.fstat(descriptor)
    if before.st_size > maximum:
        raise ValueError("Input exceeds the configured hashing byte budget")
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        block = os.read(descriptor, min(1024 * 1024, maximum - total + 1))
        if not block:
            break
        total += len(block)
        if total > maximum:
            raise ValueError("Input changed or exceeds its hashing byte budget")
        digest.update(block)
    if total != before.st_size or _signature(os.fstat(descriptor)) != _signature(before):
        raise ValueError("Input changed during identity inspection")
    return digest.hexdigest()


def _unchanged(root, parts, root_identity, expected):
    with _open_file(root, parts, root_identity) as descriptor:
        if _signature(os.fstat(descriptor)) != tuple(expected):
            raise ValueError("Input changed during inspection; refresh before using this profile")
