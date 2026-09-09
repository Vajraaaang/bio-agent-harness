"""Durable execution evidence with content hashes and explicit trust boundaries.

This local store is append-only through this API, not WORM storage. Its owner can
replace the entire history. Only a separately retained, trusted root hash detects
that replacement. Actor labels are caller assertions, not authenticated identities.
"""

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Union

PathLike = Union[str, Path]
SCHEMA_VERSION = "devharness.execution-dag/1"
CANONICALIZATION = "devharness.canonical-json/1"
ARTIFACT_VERSION = "devharness.directory-artifact/1"
MAX_JSON_BYTES = 16_000_000
CHUNK_BYTES = 1_048_576
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ArtifactLimitError(ValueError):
    """A configured artifact payload or inventory bound was exceeded."""


def _limit(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(name + " must be a nonnegative integer or None")
    return value


def canonical_json(value) -> bytes:
    """Encode strict, finite JSON deterministically; this is not RFC 8785 JCS.

    The versioned contract uses Python's JSON number representation, sorted string
    keys, compact separators and UTF-8 without Unicode normalization. Lists retain
    their order. Non-string keys and implicit Python-to-JSON coercions are rejected.
    """
    def check(item):
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Nonfinite JSON numbers are not permitted")
        elif item is None or type(item) in (str, int, bool):
            return
        elif type(item) is list:
            for child in item:
                check(child)
        elif type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError("JSON object keys must be strings")
                check(child)
        else:
            raise TypeError(f"Unsupported JSON value: {type(item).__name__}")

    try:
        check(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except RecursionError as error:
        raise ValueError("Cyclic or excessively nested JSON") from error
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("JSON evidence exceeds the 16 MB bound")
    return encoded


def _digest(value) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _copy_json(value):
    return json.loads(canonical_json(value))


def _absolute(value: PathLike) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Filesystem paths must be absolute")
    if path.is_symlink():
        raise ValueError("Symlink paths are not permitted")
    return path.resolve()


def _signature(info) -> tuple:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _open_regular(path: Path):
    if path.is_symlink():
        raise ValueError("Symlink evidence is not permitted")
    # A FIFO must reach the descriptor type check without waiting for a writer.
    # O_NONBLOCK does not bound ordinary regular-file I/O.
    descriptor = os.open(
        str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Evidence must be a regular file")
    return os.fdopen(descriptor, "rb")


def _stream_digest(path: Path, sink=None, *, max_bytes=None) -> tuple:
    _limit(max_bytes, "max_bytes")
    digest, size = hashlib.sha256(), 0
    with _open_regular(path) as source:
        before = _signature(os.fstat(source.fileno()))
        if max_bytes is not None and before[2] > max_bytes:
            raise ArtifactLimitError("Artifact byte limit exceeded")
        while True:
            # Read the descriptor directly to avoid buffered read-ahead.
            # One lookahead byte detects growth; it is never written to a sink.
            chunk = (source.read(CHUNK_BYTES) if max_bytes is None else
                     os.read(source.fileno(), min(CHUNK_BYTES, max_bytes - size + 1)))
            if not chunk:
                break
            if max_bytes is not None and size + len(chunk) > max_bytes:
                raise ArtifactLimitError("Artifact byte limit exceeded")
            digest.update(chunk)
            size += len(chunk)
            if sink is not None:
                sink.write(chunk)
        if _signature(os.fstat(source.fileno())) != before:
            raise ValueError("Artifact changed while being read")
    return digest.hexdigest(), size


def _sync_directory(path: Path) -> None:
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish(temporary: Path, destination: Path, digest: str, allow_existing=True) -> None:
    """Atomically publish complete bytes without replacing an existing name."""
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Invalid evidence storage directory")
    try:
        os.chmod(temporary, 0o444)  # Advisory protection; explicitly not WORM.
        try:
            os.link(str(temporary), str(destination))
        except FileExistsError:
            if not allow_existing:
                raise
            if _stream_digest(destination)[0] != digest:
                raise ValueError("Corrupt existing evidence: hash mismatch")
        _sync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(destination: Path, data: bytes, allow_existing=True) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=str(destination.parent))
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        _publish(temporary, destination, hashlib.sha256(data).hexdigest(), allow_existing)
    finally:
        temporary.unlink(missing_ok=True)


def _member_name(name: str) -> bool:
    if not isinstance(name, str) or not name or name == "." or "\\" in name or "\x00" in name:
        return False
    member = PurePosixPath(name)
    return (not member.is_absolute() and name == member.as_posix()
            and all(part not in (".", "..") for part in member.parts))


def _inventory(source: Path, *, max_entries=None) -> list:
    _limit(max_entries, "max_entries")
    entries = []
    if max_entries is not None:
        # os.walk materializes each directory before yielding it. Use scandir
        # incrementally so a single wide directory cannot evade the entry cap.
        pending = [source]
        while pending:
            base = pending.pop()
            if base.is_symlink():
                raise ValueError("Symlink artifacts are not permitted")
            with os.scandir(base) as children:
                for child in children:
                    if len(entries) >= max_entries:
                        raise ArtifactLimitError("Artifact entry limit exceeded")
                    path = base / child.name
                    if child.is_symlink():
                        raise ValueError("Symlink artifacts are not permitted")
                    info = child.stat(follow_symlinks=False)
                    directory = stat.S_ISDIR(info.st_mode)
                    if not directory and not stat.S_ISREG(info.st_mode):
                        raise ValueError("Artifacts must contain only regular files and directories")
                    member = path.relative_to(source).as_posix()
                    if not _member_name(member):
                        raise ValueError("Unsafe artifact member name")
                    entries.append((member, path, "directory" if directory else "file", _signature(info)))
                    if directory:
                        pending.append(path)
        return sorted(entries, key=lambda item: item[0])

    def unreadable(error):
        raise error
    for directory, directories, files in os.walk(str(source), followlinks=False, onerror=unreadable):
        base = Path(directory)
        for name in sorted(directories + files):
            path = base / name
            if path.is_symlink():
                raise ValueError("Symlink artifacts are not permitted")
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                raise ValueError("Artifacts must contain only regular files and directories")
            member = path.relative_to(source).as_posix()
            if not _member_name(member):
                raise ValueError("Unsafe artifact member name")
            entries.append((member, path, "directory" if stat.S_ISDIR(info.st_mode) else "file",
                            _signature(info)))
    return sorted(entries, key=lambda item: item[0])


def _artifact(source: Path, read_file: Callable, save_directory: Optional[Callable] = None,
              *, max_bytes=None, max_entries=None, prepare_directory: Optional[Callable] = None) -> dict:
    info = source.stat()
    if stat.S_ISREG(info.st_mode):
        if max_entries is not None and max_entries < 1:
            raise ArtifactLimitError("Artifact entry limit exceeded")
        if max_bytes is not None and info.st_size > max_bytes:
            raise ArtifactLimitError("Artifact byte limit exceeded")
        digest, size = read_file(source)
        return {"kind": "file", "name": source.name, "sha256": digest, "size_bytes": size}
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Artifact must be a regular file or directory")
    before = _signature(info)
    inventory = _inventory(source) if max_entries is None else _inventory(source, max_entries=max_entries)
    if max_bytes is not None and sum(signature[2] for _, _, kind, signature in inventory if kind == "file") > max_bytes:
        raise ArtifactLimitError("Artifact byte limit exceeded")
    if prepare_directory is not None:
        prepare_directory()
    entries, size = [], 0
    for name, path, kind, _ in inventory:
        entry = {"name": name, "kind": kind}
        if kind == "file":
            digest, count = read_file(path)
            entry.update(sha256=digest, size_bytes=count)
            size += count
        entries.append(entry)
    if _signature(source.stat()) != before:
        raise ValueError("Artifact directory changed while being read")
    after = _inventory(source) if max_entries is None else _inventory(source, max_entries=max_entries)
    if after != inventory:
        raise ValueError("Artifact directory changed while being read")
    descriptor = {"schema_version": ARTIFACT_VERSION, "entries": entries}
    digest = _digest(descriptor)
    if save_directory is not None:
        save_directory(digest, canonical_json(descriptor))
    return {"kind": "directory", "name": source.name, "sha256": digest,
            "size_bytes": size, "entries": entries}


def hash_artifact(abs_path: PathLike, *, max_bytes=None, max_entries=None) -> dict:
    """Hash an artifact, optionally limiting total payload and member count.

    Limits do not change identity encoding. With a byte limit, at most one extra
    payload byte is read to detect growth. Filesystem-operation latency is not
    bounded. A root file counts as one entry; a root directory is not counted.
    """
    _limit(max_bytes, "max_bytes")
    _limit(max_entries, "max_entries")
    used = 0
    def read_file(path):
        nonlocal used
        digest, size = _stream_digest(path, max_bytes=None if max_bytes is None else max_bytes-used)
        used += size
        return digest, size
    return _artifact(_absolute(abs_path), read_file, max_bytes=max_bytes, max_entries=max_entries)


def copy_artifact(source: PathLike, destination: PathLike, *, max_bytes: int, max_entries: int) -> dict:
    """Copy a stable artifact to a new path with cumulative streaming limits.

    Partial output on failure remains within the configured payload/inventory
    limits. Callers must retain the failure and verify the destination before
    using it. Source and destination parents are a caller-owned filesystem
    boundary, not isolation from a hostile host administrator.
    """
    for value, name in ((max_bytes, "max_bytes"), (max_entries, "max_entries")):
        if _limit(value, name) is None:
            raise ValueError(name + " is required for artifact copying")
    source, destination = _absolute(source), _absolute(destination)
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Source and destination must not overlap")
    if destination.exists():
        raise FileExistsError("Artifact destination already exists")
    if not destination.parent.is_dir():
        raise ValueError("Artifact destination parent must already exist")
    used = 0
    def copy_file(path):
        nonlocal used
        target = destination if path == source else destination / path.relative_to(source)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with target.open("xb") as sink:
            os.chmod(target, 0o600)
            digest, size = _stream_digest(path, sink, max_bytes=max_bytes-used)
            sink.flush()
            os.fsync(sink.fileno())
        used += size
        return digest, size
    # Reserve a directory with one exclusive operation before copying any of its
    # members. A prior exists() check alone permits concurrent copies to merge.
    reference = _artifact(source, copy_file, max_bytes=max_bytes, max_entries=max_entries,
                          prepare_directory=lambda: destination.mkdir(mode=0o700))
    if reference["kind"] == "directory":
        for member in reference["entries"]:
            if member["kind"] == "directory":
                (destination / member["name"]).mkdir(mode=0o700, parents=True, exist_ok=True)
    return dict(reference, name=destination.name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class ExecutionDAG:
    """One runner-owned ledger per absolute, initially empty directory.

    Thread-safe within one process. Separate runs require separate directories.
    Directory ownership/ACLs, externally retained checkpoints, authentication and
    retention enforcement remain deployment responsibilities. Copy artifacts before
    executing and make the executor consume those snapshots to bind data to a run.
    """

    def __init__(self, root_dir: PathLike, actor_id: str = "local-unverified") -> None:
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 200:
            raise ValueError("actor_id must be a nonempty label of at most 200 characters")
        self.root_dir = _absolute(root_dir)
        self.root_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        if any(self.root_dir.iterdir()):
            raise FileExistsError("Execution ledger directory must be empty")
        self._lock = threading.RLock()
        self._node_ids, self._artifacts = [], {}
        self._receipt = None
        self._identity = {"run_id": str(uuid.uuid4()), "actor_id": actor_id,
                          "identity_assurance": "caller-asserted", "created_at_utc": _now()}
        _atomic_bytes(self.root_dir / "ledger.json", canonical_json(self._identity), allow_existing=False)
        (self.root_dir / "objects").mkdir(mode=0o700)
        (self.root_dir / "nodes").mkdir(mode=0o700)
        _sync_directory(self.root_dir)

    def _check_open(self):
        if self._receipt is not None:
            raise RuntimeError("Execution ledger has been finalized")
        for name in ("objects", "nodes"):
            if (self.root_dir / name).is_symlink() or not (self.root_dir / name).is_dir():
                raise ValueError("Invalid evidence storage directory")

    def _copy_file(self, source: Path, *, max_bytes=None) -> tuple:
        descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=str(self.root_dir / "objects"))
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                digest, size = (_stream_digest(source, output) if max_bytes is None else
                                _stream_digest(source, output, max_bytes=max_bytes))
                output.flush()
                os.fsync(output.fileno())
            _publish(temporary, self.root_dir / "objects" / digest, digest)
            return digest, size
        finally:
            temporary.unlink(missing_ok=True)

    def add_artifact(self, abs_path: PathLike, *, max_bytes=None, max_entries=None) -> dict:
        """Preserve bytes, with optional cumulative payload and member limits.

        Limits have the same meaning as hash_artifact and do not change identity.
        Metadata descriptors are separately bounded by the JSON contract.
        """
        _limit(max_bytes, "max_bytes")
        _limit(max_entries, "max_entries")
        with self._lock:
            self._check_open()
            source = _absolute(abs_path)
            if source.is_relative_to(self.root_dir) or self.root_dir.is_relative_to(source):
                raise ValueError("Source artifact and execution ledger must not overlap")
            used = 0
            def copy_file(path):
                nonlocal used
                digest, size = (self._copy_file(path) if max_bytes is None else
                                self._copy_file(path, max_bytes=max_bytes-used))
                used += size
                return digest, size
            reference = _artifact(source, copy_file, lambda digest, data:
                                  _atomic_bytes(self.root_dir / "objects" / digest, data),
                                  max_bytes=max_bytes, max_entries=max_entries)
            self._artifacts[_digest(reference)] = _copy_json(reference)
            return _copy_json(reference)

    def append(self, kind: str, payload: dict, parents=()) -> str:
        """Persist a new node whose parents must already exist; never update a node."""
        with self._lock:
            self._check_open()
            if not isinstance(kind, str) or not kind.strip() or len(kind) > 200:
                raise ValueError("Node kind must be a nonempty label")
            if type(payload) is not dict:
                raise TypeError("Node payload must be a JSON object")
            parents = list(parents)
            if (any(not isinstance(parent, str) or parent not in self._node_ids for parent in parents)
                    or len(set(parents)) != len(parents)):
                raise ValueError("Every parent must be a distinct existing node")
            node = {"schema_version": SCHEMA_VERSION, "run_id": self._identity["run_id"],
                    "sequence": len(self._node_ids) + 1, "kind": kind,
                    "payload": _copy_json(payload), "parents": parents,
                    "previous": self._node_ids[-1] if self._node_ids else None,
                    "actor_id": self._identity["actor_id"], "identity_assurance": "caller-asserted",
                    "timestamp_utc": _now()}
            node_id = _digest(node)
            node["node_id"] = node_id
            _atomic_bytes(self.root_dir / "nodes" / (node_id + ".json"), canonical_json(node),
                          allow_existing=False)
            self._node_ids.append(node_id)
            return node_id

    def finalize(self, *, analysis_fingerprint: Optional[str] = None) -> dict:
        """Seal a manifest and return a receipt; retain its root outside this store."""
        with self._lock:
            if self._receipt is not None:
                report = verify_execution(self._receipt["manifest_path"], self._receipt["root_hash"])
                if not report["valid"]:
                    raise ValueError("Finalized evidence has been altered")
                return _copy_json(self._receipt)
            self._check_open()
            if analysis_fingerprint is not None and not _is_digest(analysis_fingerprint):
                raise ValueError("Invalid analysis fingerprint")
            manifest = {"schema_version": SCHEMA_VERSION, "canonicalization": CANONICALIZATION,
                        **self._identity, "finalized_at_utc": _now(), "node_ids": list(self._node_ids),
                        "node_count": len(self._node_ids), "artifacts": list(self._artifacts.values()),
                        "analysis_fingerprint": analysis_fingerprint}
            root_hash = _digest(manifest)
            manifest["root_hash"] = root_hash
            manifest_path = self.root_dir / "manifest.json"
            _atomic_bytes(manifest_path, canonical_json(manifest))
            _atomic_bytes(self.root_dir / "root.sha256", (root_hash + "\n").encode("ascii"))
            report = verify_execution(manifest_path, root_hash)
            if not report["valid"]:
                raise ValueError("Cannot finalize invalid evidence: " + "; ".join(report["errors"]))
            self._receipt = {"schema_version": SCHEMA_VERSION, "manifest_path": str(manifest_path),
                             "root_hash": root_hash, "node_count": len(self._node_ids),
                             "artifact_count": len(self._artifacts),
                             "analysis_fingerprint": analysis_fingerprint}
            return _copy_json(self._receipt)


def _is_digest(value) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _read_json(path: Path):
    with _open_regular(path) as source:
        data = source.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError("JSON evidence exceeds the 16 MB bound")
    value = json.loads(data, object_pairs_hook=_unique_keys)
    canonical_json(value)
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _valid_time(value) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").utcoffset().total_seconds() == 0
    except (ValueError, AttributeError):
        return False


def _verify_artifact(reference: dict, root: Path, checked: dict) -> None:
    def blob(digest, size=None):
        _require(_is_digest(digest), "Invalid artifact SHA-256")
        if digest not in checked:
            actual, count = _stream_digest(root / "objects" / digest)
            _require(actual == digest, "Artifact hash mismatch: " + digest)
            checked[digest] = count
        if size is not None:
            _require(type(size) is int and size >= 0 and checked[digest] == size,
                     "Artifact size mismatch: " + digest)

    _require(type(reference) is dict, "Invalid artifact reference")
    kind = reference.get("kind")
    _require(kind in ("file", "directory"), "Unknown artifact kind")
    expected = {"kind", "name", "sha256", "size_bytes"} | ({"entries"} if kind == "directory" else set())
    _require(set(reference) == expected, "Invalid artifact fields")
    _require(_member_name(reference["name"]) and "/" not in reference["name"], "Unsafe artifact name")
    _require(type(reference["size_bytes"]) is int and reference["size_bytes"] >= 0,
             "Invalid artifact size")
    if kind == "file":
        blob(reference["sha256"], reference["size_bytes"])
        return
    blob(reference["sha256"])
    entries = reference["entries"]
    _require(type(entries) is list, "Invalid directory entries")
    names, directories, total = [], set(), 0
    for entry in entries:
        _require(type(entry) is dict and _member_name(entry.get("name")), "Unsafe artifact member name")
        name, entry_kind = entry["name"], entry.get("kind")
        _require(name not in names, "Duplicate artifact member")
        parent = PurePosixPath(name).parent.as_posix()
        _require(parent == "." or parent in directories, "Missing artifact parent directory")
        names.append(name)
        if entry_kind == "directory":
            _require(set(entry) == {"name", "kind"}, "Invalid directory member")
            directories.add(name)
        else:
            _require(entry_kind == "file" and set(entry) == {"name", "kind", "sha256", "size_bytes"},
                     "Invalid file member")
            blob(entry["sha256"], entry["size_bytes"])
            total += entry["size_bytes"]
    _require(names == sorted(names), "Directory members are not canonically ordered")
    _require(type(reference["size_bytes"]) is int and reference["size_bytes"] == total,
             "Directory size mismatch")
    descriptor = {"schema_version": ARTIFACT_VERSION, "entries": entries}
    _require(_read_json(root / "objects" / reference["sha256"]) == descriptor,
             "Directory descriptor mismatch")


def verify_execution(manifest_path: PathLike, expected_root_hash: Optional[str] = None) -> dict:
    """Fail closed on malformed or changed evidence; anchors must come from a trusted source.

    Without an independently retained root this checks internal consistency only.
    No record in this local directory can authenticate the directory against a full
    rewrite by its owner. The return value never certifies regulatory compliance.
    """
    report = {"valid": False, "errors": [], "anchored": expected_root_hash is not None,
              "root_hash": None, "compliance_certified": False}
    errors = report["errors"]
    try:
        path = _absolute(manifest_path)
        root = path.parent
        for name in ("nodes", "objects"):
            _require(not (root / name).is_symlink() and (root / name).is_dir(), "Invalid evidence storage directory")
        manifest = _read_json(path)
        _require(type(manifest) is dict, "Manifest must be a JSON object")
        _require(manifest.get("schema_version") == SCHEMA_VERSION, "Unknown execution schema version")
        _require(manifest.get("canonicalization") == CANONICALIZATION, "Unknown canonicalization version")
        root_hash = manifest.get("root_hash")
        report["root_hash"] = root_hash
        unsigned = {key: value for key, value in manifest.items() if key != "root_hash"}
        if not _is_digest(root_hash) or root_hash != _digest(unsigned):
            errors.append("Manifest root hash mismatch")
        if expected_root_hash is not None and (not _is_digest(expected_root_hash) or root_hash != expected_root_hash):
            errors.append("Manifest does not match the trusted root")
        with _open_regular(root / "root.sha256") as root_file:
            if root_file.read(100) != (str(root_hash) + "\n").encode("ascii"):
                errors.append("Local root receipt mismatch")
        identity = _read_json(root / "ledger.json")
        _require(identity == {key: manifest.get(key) for key in
                              ("run_id", "actor_id", "identity_assurance", "created_at_utc")},
                 "Ledger identity mismatch")
        _require(manifest.get("identity_assurance") == "caller-asserted", "Unsupported identity assurance claim")
        _require(_valid_time(manifest.get("created_at_utc")) and _valid_time(manifest.get("finalized_at_utc")),
                 "Invalid UTC execution timestamps")
        _require(manifest.get("analysis_fingerprint") is None or _is_digest(manifest["analysis_fingerprint"]),
                 "Invalid analysis fingerprint")
        ids = manifest.get("node_ids")
        _require(type(ids) is list and type(manifest.get("node_count")) is int
                 and manifest["node_count"] == len(ids), "Node count mismatch")
        if not all(_is_digest(node_id) for node_id in ids):
            errors.append("Invalid node ID; filesystem traversal is forbidden")
            return report
        _require(len(set(ids)) == len(ids), "Duplicate node IDs")
        expected_files = {node_id + ".json" for node_id in ids}
        actual_files = {item.name for item in (root / "nodes").iterdir()}
        if actual_files != expected_files:
            errors.append("Missing or unreferenced node files")
        seen = set()
        for index, node_id in enumerate(ids):
            try:
                node = _read_json(root / "nodes" / (node_id + ".json"))
                _require(type(node) is dict and node.get("node_id") == node_id, "Node identity mismatch")
                content = {key: value for key, value in node.items() if key != "node_id"}
                _require(_digest(content) == node_id, "Node hash mismatch")
                _require(node.get("schema_version") == SCHEMA_VERSION and node.get("run_id") == manifest["run_id"],
                         "Node execution identity mismatch")
                _require(node.get("actor_id") == manifest["actor_id"] and node.get("identity_assurance") == "caller-asserted",
                         "Node actor identity mismatch")
                _require(type(node.get("sequence")) is int and node["sequence"] == index + 1, "Node sequence mismatch")
                _require(node.get("previous") == (ids[index - 1] if index else None), "Broken previous-node link")
                parents = node.get("parents")
                _require(type(parents) is list and all(_is_digest(parent) and parent in seen for parent in parents)
                         and len(set(parents)) == len(parents), "Missing, duplicate or cyclic parent links")
                _require(_valid_time(node.get("timestamp_utc")), "Invalid UTC node timestamp")
                _require(isinstance(node.get("kind"), str) and bool(node["kind"].strip())
                         and type(node.get("payload")) is dict, "Invalid node kind or payload")
            except (OSError, ValueError, TypeError, KeyError) as error:
                errors.append(f"Node {node_id}: {error}")
            seen.add(node_id)
        references = manifest.get("artifacts")
        _require(type(references) is list, "Artifact references must be a list")
        checked = {}
        for reference in references:
            try:
                _verify_artifact(reference, root, checked)
            except (OSError, ValueError, TypeError, KeyError) as error:
                errors.append(f"Artifact verification: {error}")
    except (OSError, ValueError, TypeError, KeyError) as error:
        errors.append(str(error))
    report["valid"] = not errors
    return report


def analysis_fingerprint(*, inputs: dict, code: dict, environment: dict,
                         parameters: dict, seeds: dict, method: dict) -> str:
    """Hash the scientific specification separately from run IDs and timestamps.

    Supply content digests and version identifiers, not temporary paths, timing,
    actors or secrets. Matching fingerprints identify matching specifications;
    reproducibility still requires replay and numerical comparison. Remote model
    inference is not made deterministic by recording its seed or model label.
    """
    return _digest({"schema_version": "devharness.analysis-fingerprint/1",
                    "inputs": inputs, "code": code, "environment": environment,
                    "parameters": parameters, "seeds": seeds, "method": method})


REQUIRED_CONTROLS = (
    "intended_use_validation", "qualified_identity", "validated_configuration",
    "security_access_control", "retention_policy", "backup_restore_validation",
    "audit_trail_review", "change_control", "electronic_signature_validation",
    "external_trust_anchor", "clinical_biomarker_validation", "reference_data_licensing", "release_approval",
)


def evaluate_controls(evidence: dict) -> dict:
    """Inventory caller-supplied qualification references, never authorize patient use.

    A control is evidenced only by an object with status='verified' and a nonempty
    controlled-document reference. These remain self-attestations until reviewed by
    the responsible organization. This software does not implement authentication,
    electronic signatures or a regulated clinical-release workflow.
    """
    if type(evidence) is not dict:
        raise TypeError("Control evidence must be a JSON object")
    canonical_json(evidence)
    def documented(key):
        record = evidence.get(key)
        return (type(record) is dict and record.get("status") == "verified"
                and isinstance(record.get("reference"), str) and bool(record["reference"].strip()))
    missing = [key for key in REQUIRED_CONTROLS if not documented(key)]
    return {"schema_version": "devharness.control-readiness/1",
            "status": "not_qualified" if missing else "ready_for_independent_review",
            "required_controls": list(REQUIRED_CONTROLS), "missing_controls": missing,
            "evidenced_controls": [key for key in REQUIRED_CONTROLS if key not in missing],
            "evidence_assurance": "caller-attested; independent qualification required",
            "clinical_release_allowed": False, "compliance_certified": False}
