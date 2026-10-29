"""Durable local projects with isolated workspaces and explicit input copies.

This catalog does not copy analyses, scientific approvals or model grants. A
trusted factory creates each service; HTTP authentication and project routing
remain the responsibility of ``make_server``.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import uuid

from devbench.study.permissions import require_protected_storage
from .workbench_inputs import InputDiscovery, _directory, _open_file, _signature, _unchanged, _hash_fd


FORMAT = "devbench.workbench-projects/1"
MAX_CATALOG_BYTES = 128 * 1024
MAX_SOURCES = 20
MAX_PROJECT_INPUTS = 64
_ID = re.compile(r"[0-9a-f]{32}\Z")
_ALIAS = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _identity(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Use an exact project identifier")
    return value


def _name(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 120
            or any(ord(char) < 32 for char in value)):
        raise ValueError("Use a project name of 1 to 120 characters without control characters")
    return value.strip()


def _protected_directory(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path != path.resolve():
        raise ValueError("Project storage must use absolute canonical paths without symlinks")
    require_protected_storage(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = _directory(path)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid():
            raise PermissionError("Project storage must be owned by the current user")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return path


def _decode_catalog(body):
    def pairs(items):
        if len({key for key, _ in items}) != len(items):
            raise ValueError("Project catalog contains duplicate fields")
        return dict(items)
    def invalid(_value):
        raise ValueError("Project catalog must contain finite JSON")
    return json.loads(body, object_pairs_hook=pairs, parse_constant=invalid)


class WorkbenchProjectHub:
    """One local catalog, one execution queue, separate services per project.

    ``service_factory(workspace, data, actors, executor)`` must honor the supplied
    shared executor and arrange actor denials for ``denied_roots``. Existing
    services are adopted only through the trusted startup mapping and are always
    read-only in the catalog. The HTTP layer must enforce that read-only flag.
    """

    def __init__(self, root, service_factory, *, library_roots=(), existing=None,
                 max_projects=20, max_import_bytes=128 * 1024 * 1024):
        if not callable(service_factory):
            raise TypeError("A trusted project service factory is required")
        if type(max_projects) is not int or not 1 <= max_projects <= 100:
            raise ValueError("Use a bounded project count from 1 to 100")
        if type(max_import_bytes) is not int or not 1 <= max_import_bytes <= 1024 * 1024 * 1024:
            raise ValueError("Use a bounded project input-copy limit")
        self.root = _protected_directory(root)
        self.private_root = _protected_directory(self.root / "private")
        self.data_root = _protected_directory(self.root / "data")
        self.actors_root = _protected_directory(self.root / "actors")
        self.metadata_path = self.root / "catalog.json"
        self.denied_roots = (self.private_root, self.data_root, self.metadata_path)
        self.token = secrets.token_urlsafe(32)
        self.max_projects, self.max_import_bytes = max_projects, max_import_bytes
        self._factory, self._lock = service_factory, threading.RLock()
        self._root_fd = _directory(self.root)
        self._root_identity = (os.fstat(self._root_fd).st_dev, os.fstat(self._root_fd).st_ino)
        self._lease = None
        self._closed = False
        self._catalog_fault = False
        self._catalog_identity = None
        self._apps = {}
        self._data_identities = {}
        self._existing = dict(existing or {})
        try:
            library_roots = tuple(Path(value) for value in library_roots)
            for library in library_roots:
                if not library.is_absolute() or library.is_relative_to(self.root) or self.root.is_relative_to(library):
                    raise ValueError("The approved input library must be disjoint from project state")
            self._discovery = InputDiscovery(library_roots, max_file_bytes=max_import_bytes) if library_roots else None
        except BaseException:
            self._release()
            raise
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="project-workbench")
        try:
            for alias in self._existing:
                if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
                    raise ValueError("Existing project aliases must be simple startup identifiers")
            self._lease = os.open(".catalog.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  0o600, dir_fd=self._root_fd)
            if not stat.S_ISREG(os.fstat(self._lease).st_mode):
                raise ValueError("Project catalog lease must be a regular file")
            try:
                fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Project catalog is already open by another service") from error
            self._catalog = self._read_catalog() or {"format": FORMAT, "revision": 0,
                                                     "default_project_id": None, "projects": []}
            self._validate(self._catalog)
            for row in self._catalog["projects"]:
                if row["kind"] == "existing":
                    app = self._existing.get(row["alias"])
                    if app is None or self._metadata(app)["id"] != row["id"]:
                        raise ValueError("A retained existing project is not bound to its reviewed startup service")
                    self._apps[row["id"]] = app
            for alias, app in self._existing.items():
                self._adopt(alias, app)
            if self._catalog_identity is None:
                self._commit(self._catalog)
        except BaseException:
            self.executor.shutdown(wait=True, cancel_futures=False)
            self._release()
            raise

    @property
    def default_project_id(self):
        return self._catalog["default_project_id"]

    def _check(self, *, mutation=False):
        if self._closed:
            raise RuntimeError("Project catalog is closed")
        descriptor = _directory(self.root)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != self._root_identity:
                raise RuntimeError("Project catalog directory identity changed")
        finally:
            os.close(descriptor)
        if mutation and self._catalog_fault:
            raise RuntimeError("Project catalog save was interrupted; restart before further mutations")
        if mutation and self._disk_identity() != self._catalog_identity:
            raise RuntimeError("Project catalog changed on disk; restart before mutating projects")

    def _validate(self, value):
        if (not isinstance(value, dict) or set(value) != {"format", "revision", "default_project_id", "projects"}
                or value["format"] != FORMAT or type(value["revision"]) is not int or value["revision"] < 0
                or not isinstance(value["projects"], list) or len(value["projects"]) > self.max_projects):
            raise ValueError("Invalid or excessive project catalog")
        ids, slots, aliases = set(), set(), set()
        for row in value["projects"]:
            if not isinstance(row, dict) or row.get("kind") not in {"owned", "existing"}:
                raise ValueError("Invalid project catalog entry")
            required = {"id", "kind", "created_at", "slot"} if row["kind"] == "owned" else {"id", "kind", "created_at", "alias"}
            if set(row) != required or not isinstance(row["created_at"], str) or len(row["created_at"]) > 40:
                raise ValueError("Invalid project catalog fields")
            _identity(row["id"])
            if row["id"] in ids:
                raise ValueError("Project catalog contains duplicate identities")
            ids.add(row["id"])
            if row["kind"] == "owned":
                _identity(row["slot"])
                if row["slot"] in slots:
                    raise ValueError("Project catalog contains duplicate storage slots")
                slots.add(row["slot"])
            else:
                if not isinstance(row["alias"], str) or not _ALIAS.fullmatch(row["alias"]) or row["alias"] in aliases:
                    raise ValueError("Invalid or duplicate existing project alias")
                aliases.add(row["alias"])
        default = value["default_project_id"]
        if default is not None and default not in ids:
            raise ValueError("Default project is not registered")

    def _read_catalog(self):
        try:
            descriptor = os.open("catalog.json", _FILE_FLAGS, dir_fd=self._root_fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CATALOG_BYTES:
                raise ValueError("Project catalog must be a bounded regular file")
            body = bytearray()
            while len(body) <= MAX_CATALOG_BYTES:
                block = os.read(descriptor, min(65536, MAX_CATALOG_BYTES + 1 - len(body)))
                if not block:
                    break
                body.extend(block)
            if len(body) > MAX_CATALOG_BYTES or _signature(os.fstat(descriptor)) != _signature(info):
                raise ValueError("Project catalog changed while reading")
            self._catalog_identity = (_signature(info), hashlib.sha256(body).hexdigest())
            return _decode_catalog(body)
        finally:
            os.close(descriptor)

    def _disk_identity(self):
        try:
            descriptor = os.open("catalog.json", _FILE_FLAGS, dir_fd=self._root_fd)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CATALOG_BYTES:
                raise ValueError("Project catalog was replaced by an unsupported file")
            digest = hashlib.sha256()
            remaining = MAX_CATALOG_BYTES + 1
            while remaining:
                block = os.read(descriptor, min(65536, remaining))
                if not block:
                    break
                digest.update(block)
                remaining -= len(block)
            if remaining == 0 or _signature(info) != _signature(os.fstat(descriptor)):
                raise ValueError("Project catalog changed while checking identity")
            return (_signature(info), digest.hexdigest())
        finally:
            os.close(descriptor)

    def _commit(self, value):
        self._check(mutation=True)
        self._validate(value)
        if self._disk_identity() != self._catalog_identity:
            raise RuntimeError("Project catalog changed on disk; restart before saving")
        body = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
        if len(body) > MAX_CATALOG_BYTES:
            raise ValueError("Project catalog exceeds its byte bound")
        temporary = ".pending-projects-" + secrets.token_hex(12)
        descriptor = None
        replaced = False
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=self._root_fd)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = None
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "catalog.json", src_dir_fd=self._root_fd, dst_dir_fd=self._root_fd)
            replaced = True
            os.fsync(self._root_fd)
            written_identity = self._disk_identity()
            if written_identity is None or written_identity[1] != hashlib.sha256(body).hexdigest():
                raise RuntimeError("Project catalog changed during publication; restart before further mutations")
            self._catalog_identity = written_identity
            self._catalog = _copy(value)
        except BaseException:
            if replaced:
                self._catalog_fault = True
            raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=self._root_fd)
            except FileNotFoundError:
                pass

    @staticmethod
    def _metadata(app):
        value = app.workspace_state()["project"]
        _identity(value.get("id"))
        _name(value.get("name"))
        return value

    def _entry(self, project_id=None):
        self._check()
        project_id = self.default_project_id if project_id is None else _identity(project_id)
        if project_id is None:
            raise KeyError("Create or select a project first")
        for row in self._catalog["projects"]:
            if row["id"] == project_id:
                return row
        raise KeyError("Unknown project")

    def _adopt(self, alias, app):
        metadata = self._metadata(app)
        prior = next((row for row in self._catalog["projects"] if row["id"] == metadata["id"]), None)
        if prior:
            if prior["kind"] != "existing" or prior["alias"] != alias:
                raise ValueError("Existing project identity is already registered under another source")
            self._apps[prior["id"]] = app
            return
        if len(self._catalog["projects"]) >= self.max_projects:
            raise ValueError("Project catalog has reached its project limit")
        updated = _copy(self._catalog)
        updated["projects"].append({"id": metadata["id"], "kind": "existing", "alias": alias,
                                    "created_at": datetime.now(timezone.utc).isoformat()})
        updated["revision"] += 1
        self._commit(updated)
        self._apps[metadata["id"]] = app

    def resolve(self, project_id=None):
        with self._lock:
            row = self._entry(project_id)
            if row["id"] not in self._apps:
                if row["kind"] != "owned":
                    raise KeyError("Existing project is unavailable")
                workspace, data, actors = self._storage(row["slot"])
                data_identity = self._directory_identity(data)
                app = self._factory(workspace, data, actors, self.executor)
                if self._metadata(app)["id"] != row["id"] or self._directory_identity(data) != data_identity:
                    app.close()
                    raise ValueError("Project workspace identity no longer matches its catalog")
                self._apps[row["id"]] = app
                self._data_identities[row["id"]] = data_identity
            return self._apps[row["id"]]

    def _storage(self, slot):
        _identity(slot)
        return tuple(_protected_directory(base / slot) for base in
                     (self.private_root, self.data_root, self.actors_root))

    @staticmethod
    def _directory_identity(path):
        descriptor = _directory(path)
        try:
            info = os.fstat(descriptor)
            return (info.st_dev, info.st_ino)
        finally:
            os.close(descriptor)

    def read_only(self, project_id=None):
        with self._lock:
            return self._entry(project_id)["kind"] == "existing"

    def _summary(self, row):
        metadata = self._metadata(self.resolve(row["id"]))
        return {"id": row["id"], "name": metadata["name"], "read_only": row["kind"] == "existing",
                "default": row["id"] == self.default_project_id, "created_at": row["created_at"]}

    def list_projects(self, active_id=None):
        with self._lock:
            self._check()
            active = self.default_project_id if active_id is None else self._entry(active_id)["id"]
            return {"format": FORMAT, "revision": self._catalog["revision"],
                    "default_project_id": self.default_project_id, "active_project_id": active,
                    "projects": [self._summary(row) for row in self._catalog["projects"]]}

    def set_default(self, project_id):
        with self._lock:
            row = self._entry(project_id)
            updated = _copy(self._catalog)
            updated["default_project_id"] = row["id"]
            updated["revision"] += 1
            self._commit(updated)
            return self._summary(row)

    def create(self, body):
        if not isinstance(body, dict) or set(body) - {"name", "sources"} or "name" not in body:
            raise ValueError("Use a project name and optional approved source list")
        name = _name(body["name"])
        sources = body.get("sources", [])
        if (not isinstance(sources, list) or len(sources) > MAX_SOURCES
                or any(not isinstance(source, str) for source in sources) or len(set(sources)) != len(sources)):
            raise ValueError("Use a bounded list of distinct approved input paths")
        with self._lock:
            self._check(mutation=True)
            if len(self._catalog["projects"]) >= self.max_projects:
                raise ValueError("Project catalog has reached its project limit")
            # Retained failed-creation state is diagnostic evidence, but cannot
            # become an unlimited disk-allocation path outside the catalog cap.
            staging = 0
            directory = _directory(self.private_root)
            try:
                with os.scandir(directory) as entries:
                    for _entry in entries:
                        staging += 1
                        if staging >= self.max_projects:
                            raise ValueError("Project storage staging limit reached; inspect retained creation state")
            finally:
                os.close(directory)
            # Validate every requested source before allocating a new project.
            for source in sources:
                self._library().source_path(source)
            slot = uuid.uuid4().hex
            workspace, data, actors = self._storage(slot)
            data_identity = self._directory_identity(data)
            app = None
            try:
                app = self._factory(workspace, data, actors, self.executor)
                metadata = self._metadata(app)
                if any(row["id"] == metadata["id"] for row in self._catalog["projects"]):
                    raise ValueError("New project identity collides with a retained project")
                current = app.workspace_state()
                app.workspace_state({"expected_revision": current["revision"], "name": name})
                for source in sources:
                    self._copy_source(data, source, expected_destination=data_identity)
                if self._directory_identity(data) != data_identity:
                    raise ValueError("Project data directory changed during creation")
                row = {"id": metadata["id"], "kind": "owned", "slot": slot,
                       "created_at": datetime.now(timezone.utc).isoformat()}
                updated = _copy(self._catalog)
                updated["projects"].append(row)
                if updated["default_project_id"] is None:
                    updated["default_project_id"] = row["id"]
                updated["revision"] += 1
                self._commit(updated)
                self._apps[row["id"]] = app
                self._data_identities[row["id"]] = data_identity
                return self._summary(row)
            except BaseException:
                # Failed creation is not published. Its private staging state is
                # retained for diagnosis, rather than deleting possible evidence.
                if app is not None:
                    app.close()
                raise

    def _library(self):
        if self._discovery is None:
            raise PermissionError("No approved project input library is configured")
        return self._discovery

    def list_sources(self):
        with self._lock:
            self._check()
            if self._discovery is None:
                return {"format": "devbench.workbench-inputs/1", "inputs": [], "truncated": False}
            return self._discovery.list()

    def import_source(self, project_id, body):
        if not isinstance(body, dict) or set(body) != {"source"} or not isinstance(body["source"], str):
            raise ValueError("Import requires exactly one approved source path")
        with self._lock:
            self._check(mutation=True)
            row = self._entry(project_id)
            if row["kind"] != "owned":
                raise PermissionError("Retained projects are read-only; import data into a new project")
            self.resolve(row["id"])
            data = self.data_root / row["slot"]
            result = self._copy_source(data, body["source"],
                                       expected_destination=self._data_identities[row["id"]])
            self._check(mutation=True)
            return result

    def _copy_source(self, destination, source, *, expected_destination=None):
        library = self._library()
        path, root, parts, root_identity = library._source(source)
        if len(path.name) > 180 or any(ord(char) < 32 for char in path.name):
            raise ValueError("Input filename exceeds the project import bounds")
        temporary = ".pending-input-" + secrets.token_hex(12)
        output = _directory(destination)
        output_identity = (os.fstat(output).st_dev, os.fstat(output).st_ino)
        descriptor = None
        try:
            if expected_destination is not None and output_identity != expected_destination:
                raise ValueError("Project data directory identity changed; restart with reviewed storage")
            entries = 0
            with os.scandir(output) as listing:
                for _entry in listing:
                    entries += 1
                    if entries >= MAX_PROJECT_INPUTS:
                        raise ValueError("Project input file limit reached")
            with _open_file(root, parts, root_identity) as incoming:
                before = os.fstat(incoming)
                if before.st_size > self.max_import_bytes:
                    raise ValueError("Input exceeds the project copy byte budget")
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=output)
                digest, total = hashlib.sha256(), 0
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = None
                    while True:
                        block = os.read(incoming, min(1024 * 1024, self.max_import_bytes - total + 1))
                        if not block:
                            break
                        total += len(block)
                        if total > self.max_import_bytes:
                            raise ValueError("Input changed or exceeds the project copy byte budget")
                        digest.update(block)
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
                if total != before.st_size or _signature(os.fstat(incoming)) != _signature(before):
                    raise ValueError("Input changed while copying; no project input was published")
            _unchanged(root, parts, root_identity, _signature(before))
            check = _directory(destination)
            try:
                if (os.fstat(check).st_dev, os.fstat(check).st_ino) != output_identity:
                    raise ValueError("Project data directory changed while copying")
            finally:
                os.close(check)
            identity = digest.hexdigest()
            stem, suffix = path.stem, path.suffix
            candidates = [path.name] + [stem + "-" + identity[:12] + ("-" + str(index) if index else "") + suffix for index in range(20)]
            published = None
            for filename in candidates:
                try:
                    os.link(temporary, filename, src_dir_fd=output, dst_dir_fd=output, follow_symlinks=False)
                    published = filename
                    break
                except FileExistsError:
                    prior = os.open(filename, _FILE_FLAGS, dir_fd=output)
                    try:
                        info = os.fstat(prior)
                        if not stat.S_ISREG(info.st_mode) or info.st_size > self.max_import_bytes:
                            raise ValueError("Project destination is not an admitted regular input")
                        prior_digest = hashlib.sha256()
                        count = 0
                        while count <= self.max_import_bytes:
                            block = os.read(prior, min(1024 * 1024, self.max_import_bytes - count + 1))
                            if not block:
                                break
                            count += len(block)
                            prior_digest.update(block)
                        if _signature(info) != _signature(os.fstat(prior)) or count > self.max_import_bytes:
                            raise ValueError("Existing project input changed during duplicate checking")
                        if count == total and prior_digest.hexdigest() == identity:
                            published = filename
                            break
                    finally:
                        os.close(prior)
            if published is None:
                raise ValueError("Project input filename collision limit reached")
            os.fsync(output)
            target = destination / published
            with _open_file(destination, (published,), output_identity) as verified:
                expected = _signature(os.fstat(verified))
                if _hash_fd(verified, self.max_import_bytes) != identity:
                    raise ValueError("Published project input no longer matches the approved source copy")
            _unchanged(destination, (published,), output_identity, expected)
            _unchanged(root, parts, root_identity, _signature(before))
            return {"input": {"source": str(target), "path": str(target), "filename": published,
                              "size_bytes": total, "sha256": identity},
                    "source": {"path": str(path), "sha256": identity}, "analysis_executed": False}
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=output)
            except FileNotFoundError:
                pass
            os.close(output)

    def _release(self):
        if self._lease is not None:
            os.close(self._lease)
            self._lease = None
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        # Work can finish while all its service locks remain held. Services using
        # the shared executor must not shut it down themselves.
        self.executor.shutdown(wait=True, cancel_futures=False)
        failure = None
        for app in dict.fromkeys(self._apps.values()):
            try:
                app.close()
            except BaseException as error:
                failure = failure or error
        self._release()
        if failure is not None:
            raise failure
