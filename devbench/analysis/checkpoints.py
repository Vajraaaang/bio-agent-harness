"""Verified, dependency-bound stage checkpoints for one sequential writer.

The manifest is tamper-evident only against a separately retained root. It is
neither a signature nor a claim that a cloud filesystem is an immutable store.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def digest(value):
    # Same finite canonical JSON/hash contract used by the coordinator broker;
    # workbench stages remain a distinct receipt type, not invented MCP events.
    from devbench.study.contracts import content_sha256
    return content_sha256(value)


def atomic_json(path, value):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


class CheckpointStore:
    """Resume completed stages only when input, code, config and outputs match."""

    def __init__(self, root, identity):
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError("Checkpoint root must be absolute")
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity = digest(identity)
        path = self.root / "identity.json"
        record = {"identity_sha256": self.identity, "identity": identity}
        if path.exists():
            if json.loads(path.read_text()) != record:
                raise ValueError("checkpoint_identity_mismatch: use a fresh output directory")
        else:
            atomic_json(path, record)

    def path(self, name):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError("Stage names must be simple path components")
        path = self.root / name
        if path.is_symlink():
            raise ValueError("Checkpoint directories cannot be symlinks")
        return path

    def verify(self, name, dependencies=()):
        path = self.path(name)
        receipt = path / "receipt.json"
        if not receipt.exists():
            return None
        record = json.loads(receipt.read_text())
        dependency_roots = {dep: sha256(self.path(dep) / "receipt.json") for dep in dependencies}
        if (record["identity_sha256"] != self.identity or record["stage"] != name
                or record["dependencies"] != dependency_roots):
            raise ValueError("checkpoint_dependency_mismatch")
        if not record["artifacts"]:
            raise ValueError("checkpoint_has_no_artifacts")
        for relative, expected in record["artifacts"].items():
            artifact = path / relative
            if (Path(relative).is_absolute() or ".." in Path(relative).parts
                    or artifact.is_symlink() or not artifact.resolve().is_relative_to(path.resolve())
                    or not artifact.is_file() or sha256(artifact) != expected):
                raise ValueError(f"checkpoint_artifact_mismatch:{name}:{relative}")
        return record

    def run(self, name, operation, *, dependencies=()):
        from .resources import StageMeasurement

        for dep in dependencies:
            dep_path = self.path(dep) / "receipt.json"
            if not dep_path.is_file():
                raise ValueError(f"missing_dependency:{dep}")
            dep_record = json.loads(dep_path.read_text())
            if self.verify(dep, tuple(dep_record["dependencies"])) is None:
                raise ValueError(f"missing_dependency:{dep}")
        record = self.verify(name, dependencies)
        if record is not None:
            print(json.dumps({"stage": name, "status": "resumed_verified"}), flush=True)
            return self.path(name)
        path = self.path(name)
        path.mkdir(exist_ok=True)
        phase = StageMeasurement(name)
        try:
            with phase:
                metadata = operation(path) or {}
            files = [p for p in path.rglob("*") if p.is_file()
                     and not p.name.startswith(".") and p.name != "receipt.json"]
            if not files or any(p.is_symlink() for p in files):
                raise ValueError("Stages must emit regular artifacts")
            record = {"format": "devbench.analysis.checkpoint/1", "stage": name,
                      "identity_sha256": self.identity,
                      "dependencies": {dep: sha256(self.path(dep)/"receipt.json") for dep in dependencies},
                      "artifacts": {p.relative_to(path).as_posix(): sha256(p) for p in sorted(files)},
                      "measurement": phase.result, "metadata": metadata}
            atomic_json(path / "receipt.json", record)
        except BaseException as error:
            failures = self.root / "failures"
            failures.mkdir(exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            atomic_json(failures / f"{name}-{stamp}.json", {"stage": name,
                "type": type(error).__name__, "message": str(error)[:2000], "measurement": phase.result})
            raise
        print(json.dumps({"stage": name, "status": "completed", **phase.result}), flush=True)
        return path
