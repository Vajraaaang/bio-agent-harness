"""Content-addressed custody for mounted matrices and local read-only source pins."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil

from devbench.core._files import read_bounded_json
from devbench.core.input_files import _hash_fd, _open_file, _signature
from devbench.core.population_data import bound_query_file
from devbench.core.protected_partitions import PartitionVault
from devharness_core.provenance import _artifact, canonical_json

from .method_executor import _path

ZERO_COPY_THRESHOLD = 256 * 1024**2
MAX_ZARR_ENTRIES = 100_000


def _local_filesystem(path):
    mounts = [
        m for m in psutil.disk_partitions(all=True) if path.is_relative_to(m.mountpoint)
    ]
    if not mounts:
        return False
    fs = max(mounts, key=lambda m: len(Path(m.mountpoint).parts)).fstype.lower()
    return fs in {
        "apfs",
        "hfs",
        "ext2",
        "ext3",
        "ext4",
        "xfs",
        "btrfs",
        "tmpfs",
        "overlay",
        "zfs",
        "f2fs",
    }


def _member_paths(source, artifact):
    yield source
    for entry in artifact.get("entries", ()):
        yield source / entry["name"]


def _read_only(source, artifact):
    return all(
        not path.lstat().st_mode & 0o222 for path in _member_paths(source, artifact)
    )


def _record(path, artifact, mode):
    info = path.lstat()
    return {
        "canonical_path": str(path),
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "size_bytes": artifact["size_bytes"],
        "full_or_merkle_sha256": artifact["sha256"],
        "storage_mode": mode,
    }


def _hash(path):
    path = _path(path)
    root = path.parent
    info = root.stat()

    def read(member):
        with _open_file(
            root, member.relative_to(root).parts, (info.st_dev, info.st_ino)
        ) as fd:
            size = os.fstat(fd).st_size
            return _hash_fd(fd, size), size

    # Preserve the existing directory Merkle encoding and stable inventories,
    # while opening every member relative to directory descriptors with NOFOLLOW.
    return _artifact(path, read, max_entries=MAX_ZARR_ENTRIES)


def _copy_file(source, target, expected):
    parent = source.parent.stat()
    with _open_file(
        source.parent, (source.name,), (parent.st_dev, parent.st_ino)
    ) as fd:
        before = os.fstat(fd)
        if before.st_size != expected["size_bytes"]:
            raise ValueError("Matrix changed before immutable copy")
        digest = hashlib.sha256()
        with target.open("xb") as output:
            remaining = before.st_size
            while chunk := os.read(fd, min(1024**2, remaining + 1)):
                if len(chunk) > remaining:
                    raise ValueError("Matrix grew during immutable copy")
                digest.update(chunk)
                output.write(chunk)
                remaining -= len(chunk)
            if (
                remaining
                or digest.hexdigest() != expected["sha256"]
                or _signature(before) != _signature(os.fstat(fd))
            ):
                raise ValueError("Matrix changed during immutable copy")
            output.flush()
            os.fchmod(output.fileno(), 0o400)
            os.fsync(output.fileno())


def _copy(source, target, artifact):
    if artifact["kind"] == "file":
        _copy_file(source, target, artifact)
        return
    target.mkdir(mode=0o700)
    for entry in artifact["entries"]:
        destination = target / entry["name"]
        if entry["kind"] == "directory":
            destination.mkdir(mode=0o700)
        else:
            _copy_file(source / entry["name"], destination, entry)


def _stage_links(source, target, artifact):
    for path in _member_paths(source, artifact):
        destination = target / path.relative_to(source)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            destination.mkdir(mode=0o700)
        elif stat.S_ISREG(info.st_mode):
            if os.geteuid() == 0 and info.st_uid != 65532 and not info.st_mode & 0o004:
                # The remapped worker needs a separately chmod-able staged inode.
                _copy(path, destination, _hash(path))
            else:
                os.link(path, destination, follow_symlinks=False)
        else:
            raise ValueError("Matrix staging requires regular file members")


class MountedZarrVault:
    """Old immutable directory objects and new H5AD/Zarr pins share hash identity."""

    def __init__(self, root):
        self.root = _path(root)

    def _object(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("Mounted matrix custody requires an exact content digest")
        return _path(self.root / identity)

    def contains(self, identity):
        target = self._object(identity)
        return target.exists() or self._pin(identity).exists()

    def _pin(self, identity):
        return _path(self._object(identity).with_suffix(".json"))

    def put(
        self, directory, *, immutable_copy=False, mutable=False, expected_sha256=None
    ):
        source = _path(directory)
        if not (
            source.is_dir()
            and source.suffix == ".zarr"
            or source.is_file()
            and source.suffix in {".h5ad", ".h5", ".hdf5"}
        ):
            raise ValueError("Mounted custody requires a canonical Zarr or HDF5 matrix")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise PermissionError("Mounted matrix vault must be private")
        before = source.lstat()
        artifact = _hash(source)
        if expected_sha256 is not None and artifact["sha256"] != expected_sha256:
            raise ValueError("Matrix digest differs from the expected input")
        record = _record(source, artifact, "zero_copy")
        if (before.st_dev, before.st_ino, before.st_mtime_ns) != (
            record["device"],
            record["inode"],
            record["mtime_ns"],
        ):
            raise ValueError("Source matrix identity changed during admission")
        zero_copy = (
            not immutable_copy
            and not mutable
            and artifact["size_bytes"] > ZERO_COPY_THRESHOLD
            and _local_filesystem(source)
            and _read_only(source, artifact)
        )
        identity = artifact["sha256"]
        target, pin = self._object(identity), self._pin(identity)
        fd = os.open(self.root, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if not target.exists() and (not zero_copy or not pin.exists()):
                with TemporaryDirectory(dir=self.root) as temp:
                    pending = Path(temp) / "object"
                    if zero_copy:
                        with pending.open("xb") as proof:
                            proof.write(canonical_json(record))
                            proof.flush()
                            os.fchmod(proof.fileno(), 0o400)
                            os.fsync(proof.fileno())
                        os.rename(pending, pin)
                    else:
                        _copy(source, pending, artifact)
                        os.rename(pending, target)
                    os.fsync(fd)
            self.verify(identity)
        finally:
            os.close(fd)
        return identity

    def _inspect(self, identity):
        target, pin = self._object(identity), self._pin(identity)
        if target.exists():
            artifact = _hash(target)
            record = _record(target, artifact, "immutable_copy")
        else:
            retained = read_bounded_json(pin, 16 * 1024)
            source = _path(retained["canonical_path"])
            artifact = _hash(source)
            record = _record(source, artifact, "zero_copy")
            if retained != record or not _read_only(source, artifact):
                raise ValueError("Mounted matrix source attestation changed")
        if artifact["sha256"] != identity:
            raise ValueError(
                "Mounted matrix content does not match the recorded digest"
            )
        return record, artifact

    def attestation(self, identity):
        return self._inspect(identity)[0]

    def verify(self, identity):
        self._inspect(identity)

    def stage(self, identity, destination, *, record=None):
        if record is None:
            record, artifact = self._inspect(identity)
        else:
            artifact = self.verify_attestation(identity, record)
        source, target = Path(record["canonical_path"]), _path(destination)
        if source.is_relative_to(target) or target.is_relative_to(source):
            raise ValueError("Matrix stage and source must not overlap")
        if target.exists():
            raise FileExistsError(target)
        with TemporaryDirectory(dir=target.parent) as temp:
            pending = Path(temp) / "matrix"
            if source.stat().st_dev == target.parent.stat().st_dev:
                try:
                    _stage_links(source, pending, artifact)
                except OSError as exc:
                    if exc.errno not in {errno.EXDEV, errno.EPERM, errno.ENOTSUP}:
                        raise
                    pending = Path(temp) / "snapshot"
                    _copy(source, pending, artifact)
            else:
                _copy(source, pending, artifact)
            self.verify_attestation(identity, record)
            if _hash(pending)["sha256"] != identity:
                raise ValueError("Staged matrix differs from the admitted source")
            os.rename(pending, target)
        return record

    def verify_attestation(self, identity, record):
        # A later operator can also retain a copy of these same bytes. Replay
        # verifies the source actually used on this attempt.
        if record["storage_mode"] not in {"zero_copy", "immutable_copy"}:
            raise ValueError("Unknown matrix custody mode")
        if record["full_or_merkle_sha256"] != identity:
            raise ValueError("Mounted attestation differs from the input pin")
        if record["storage_mode"] == "zero_copy":
            if record != read_bounded_json(self._pin(identity), 16 * 1024):
                raise ValueError(
                    "Mounted matrix attestation differs from the retained pin"
                )
            path = _path(record["canonical_path"])
            artifact = _hash(path)
            if not _read_only(path, artifact):
                raise ValueError("Mounted matrix source became writable")
        else:
            path = self._object(identity)
            artifact = _hash(path)
        if record != _record(path, artifact, record["storage_mode"]):
            raise ValueError("Mounted matrix source attestation changed")
        return artifact

    def member_digests(self, identity, record=None):
        """Identity and regular member digests of the exact, verified object."""
        artifact = (
            self._inspect(identity)[1]
            if record is None
            else self.verify_attestation(identity, record)
        )
        members = {
            entry["sha256"]
            for entry in artifact.get("entries", ())
            if entry["kind"] == "file"
        }
        return (identity, *sorted(members - {identity}))

    def verify_staged(self, identity, destination, record):
        self.verify_attestation(identity, record)
        if _hash(destination)["sha256"] != identity:
            raise ValueError("Mounted matrix changed during execution")


class MountedTrainingVault(PartitionVault):
    """Preserve the training vault interface while pinning large matrix files."""

    def __init__(self, root, matrices, **limits):
        super().__init__(root, **limits)
        self.matrices = matrices

    def put_file(self, source):
        source = _path(source)
        if source.suffix == ".zarr" or (
            source.suffix in {".h5", ".h5ad", ".hdf5"}
            and source.stat().st_size > ZERO_COPY_THRESHOLD
        ):
            return self.matrices.put(source)
        return super().put_file(source)

    @contextmanager
    def open(self, identity):
        if self.matrices.contains(identity):
            record = self.matrices.attestation(identity)
            with bound_query_file(
                Path(record["canonical_path"]), identity, record["size_bytes"]
            ) as stream:
                yield stream
            self.matrices.verify(identity)
        else:
            with super().open(identity) as stream:
                yield stream
