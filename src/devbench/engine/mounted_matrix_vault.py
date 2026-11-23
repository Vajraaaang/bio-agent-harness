"""Private content custody for a mounted Zarr directory."""

import errno
import os
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from devharness_core.provenance import copy_artifact, hash_artifact

from .method_executor import _path

MAX_MOUNTED_MATRIX_BYTES = 512 * 1024**2
MAX_ZARR_ENTRIES = 100_000


class MountedZarrVault:
    def __init__(self, root):
        self.root = _path(root)

    def _object(self, identity):
        if len(identity) != sha256().digest_size * 2 or any(
            char not in "0123456789abcdef" for char in identity
        ):
            raise ValueError("Mounted Zarr custody requires an exact content digest")
        return _path(self.root / identity)

    def put(self, directory):
        source = _path(directory)
        if not source.is_dir() or source.suffix != ".zarr":
            raise ValueError("Mounted Zarr custody requires a canonical Zarr directory")
        self.root.mkdir(mode=0o700, exist_ok=True)
        if self.root.stat().st_mode & 0o077:
            raise PermissionError("Mounted Zarr vault must be private")
        with TemporaryDirectory(dir=self.root) as temporary:
            pending = Path(temporary) / "matrix"
            copied = copy_artifact(
                source,
                pending,
                max_bytes=MAX_MOUNTED_MATRIX_BYTES,
                max_entries=MAX_ZARR_ENTRIES,
            )
            identity = copied["sha256"]
            target = self._object(identity)
            try:
                os.rename(pending, target)
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
            self.verify(identity)
        return identity

    def verify(self, identity):
        artifact = hash_artifact(
            self._object(identity),
            max_bytes=MAX_MOUNTED_MATRIX_BYTES,
            max_entries=MAX_ZARR_ENTRIES,
        )
        if artifact["kind"] != "directory" or artifact["sha256"] != identity:
            raise ValueError("Mounted Zarr content does not match the recorded digest")

    def stage(self, identity, destination):
        artifact = copy_artifact(
            self._object(identity),
            destination,
            max_bytes=MAX_MOUNTED_MATRIX_BYTES,
            max_entries=MAX_ZARR_ENTRIES,
        )
        if artifact["kind"] != "directory" or artifact["sha256"] != identity:
            raise ValueError("Staged Zarr content does not match the recorded digest")
