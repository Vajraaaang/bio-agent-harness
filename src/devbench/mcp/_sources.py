"""Descriptor-pinned streaming inspection of files and directory manifests."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from devbench.core.input_files import _directory, _open_file, _signature, _unchanged
from devbench.engine.workflow_operations import OperationBlocked

BLOCK = 1 << 20
MAX_HEADER_BYTES = 4 * BLOCK
MAX_MEMBERS = 200_000
PRIVATE = "private_data_local_studio_only"
QUOTA = "resource_quota_exceeded"
SCOPE = "input_scope_violation"


def clinical(name):
    from devbench.platform.plugin_mcp import _clinical_name

    return _clinical_name(name)


def deadline():
    return time.monotonic() + 4.25


def check_time(until):
    from .server import check_deadline

    check_deadline()
    if time.monotonic() > until:
        raise OperationBlocked(QUOTA)


def scoped_path(root, value):
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if not path.is_relative_to(root) or path == root or ".." in path.parts:
        raise OperationBlocked(SCOPE)
    if path.resolve(strict=True) != path:
        raise OperationBlocked(SCOPE)
    return path


@contextmanager
def pinned_file(path, until, *, expected=None):
    """Only parse bytes from the verified descriptor; recheck its path at release."""
    parent = path.parent
    parent_info = parent.stat()
    parent_id = parent_info.st_dev, parent_info.st_ino
    with _open_file(parent, (path.name,), parent_id) as fd:
        info = os.fstat(fd)
        if info.st_nlink != 1:
            raise OperationBlocked(SCOPE)
        signature = _signature(info)
        with os.fdopen(os.dup(fd), "rb") as stream:
            hasher = hashlib.sha256()
            while block := stream.read(BLOCK):
                hasher.update(block)
                check_time(until)
            digest = hasher.hexdigest()
            if expected is not None and digest != expected:
                raise OperationBlocked("plan_identity_mismatch")
            stream.seek(0)
            yield stream, digest, signature[3]
        if _signature(os.fstat(fd)) != signature:
            raise OperationBlocked("plan_identity_mismatch")
        _unchanged(parent, (path.name,), parent_id, signature)


def canonical(document):
    return json.dumps(
        document, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode()


class DirectoryInventory:
    """Stream a deterministic directory tree and bounded metadata."""

    def __init__(self, path, until):
        self.path = path
        self.until = until
        self.size = 0
        self.members = 0
        self.metadata = {}
        self.mtx_members = {}
        self.top_names = set()
        self.array_chunks = {}
        self.member_sha256s = set()
        self.clinical = False
        self.metadata_bytes = 0
        descriptor = _directory(path)
        try:
            self.sha256 = self._branch(descriptor, (), 0)
        finally:
            os.close(descriptor)

    def _branch(self, fd, parts, depth):
        check_time(self.until)
        if depth > 32:
            raise OperationBlocked(QUOTA)
        before = _signature(os.fstat(fd))
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                self.members += 1
                if self.members % 512 == 0:
                    check_time(self.until)
                if self.members > MAX_MEMBERS:
                    raise OperationBlocked(QUOTA)
                names.append(entry.name)
        hasher = hashlib.sha256(b"devbench.input-tree/1\0")
        for name in sorted(names):
            check_time(self.until)
            self.clinical |= clinical(name)
            if not parts:
                self.top_names.add(name)
            branch = (*parts, name)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            kind, digest = self._child(fd, name, branch, info, depth)
            hasher.update(canonical([kind, name, digest]))
            hasher.update(b"\n")
        if _signature(os.fstat(fd)) != before:
            raise OperationBlocked("plan_identity_mismatch")
        return hasher.hexdigest()

    def _child(self, fd, name, parts, info, depth):
        if stat.S_ISDIR(info.st_mode):
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                return "dir", self._branch(child, parts, depth + 1)
            finally:
                os.close(child)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OperationBlocked(SCOPE)
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        hasher = hashlib.sha256()
        collect = name in {".zarray", ".zattrs", "zarr.json", ".zgroup", ".zmetadata"}
        data = bytearray()
        with os.fdopen(child, "rb") as stream:
            if _signature(os.fstat(child)) != _signature(info):
                raise OperationBlocked("plan_identity_mismatch")
            while block := stream.read(BLOCK):
                check_time(self.until)
                hasher.update(block)
                if collect:
                    self.metadata_bytes += len(block)
                    if self.metadata_bytes > MAX_HEADER_BYTES:
                        raise OperationBlocked(QUOTA)
                    data.extend(block)
            if _signature(os.fstat(child)) != _signature(info):
                raise OperationBlocked("plan_identity_mismatch")
        if "X" in parts[:-1] or parts[0] in {"obs", "var"}:
            self.array_chunks["/".join(parts)] = hasher.hexdigest()
        self._retain(parts, name, data, collect, hasher.hexdigest())
        self.size += info.st_size
        return "file", hasher.hexdigest()

    def _retain(self, parts, name, data, collect, digest):
        self.member_sha256s.add(digest)
        if collect:
            self.metadata["/".join(parts)] = json.loads(data)
        if len(parts) == 1 and name in {"matrix.mtx", "matrix.mtx.gz"}:
            self.mtx_members[name] = digest


@contextmanager
def text_input(stream, compressed):
    reader = gzip.GzipFile(fileobj=stream) if compressed else stream
    wrapper = io.TextIOWrapper(reader, encoding="utf-8-sig", newline="")
    try:
        yield wrapper
    finally:
        wrapper.detach()
        if compressed:
            reader.close()
