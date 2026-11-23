"""Publication authority discovery and current-use workspace guards."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import re

from devbench.engine.provenance import canonical_json

from ._publication_policy import _ERROR_FIELDS, _SHA, _digest, _key, public_metadata


_LOCATION_KEY = "publication_authorities"
_LOCATION_LOCK = ".publication-authorities.lock"
_LOCATION_COMMITTED = b"devbench.publication-authorities/committed/1\n"


def _owned_location_path(path, *, directory=False):
    import os
    import stat
    path = Path(path)
    observed = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or observed.st_uid != os.geteuid()
            or observed.st_mode & 0o022
            or not (stat.S_ISDIR(observed.st_mode) if directory else stat.S_ISREG(observed.st_mode))):
        raise PermissionError("publication_authority_locator_unavailable")
    return observed


@contextmanager
def _publication_location_lock(root, *, write=False):
    import fcntl
    import os
    import stat
    path = root / _LOCATION_LOCK
    flags = os.O_NOFOLLOW | (os.O_RDWR if write else os.O_RDONLY)
    descriptor = os.open(path, flags | os.O_CREAT, 0o600) if write else os.open(path, flags)
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.geteuid() or observed.st_mode & 0o022:
            raise PermissionError("publication_authority_locator_unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
        yield descriptor
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def publication_service_settings(value):
    """Recognition metadata never restores the service's runtime grants."""
    if type(value) is not dict:
        raise ValueError("Invalid retained service settings")
    if _LOCATION_KEY in value:
        record = value[_LOCATION_KEY]
        if (type(record) is not dict or set(record) != {"format", "project_id", "scientific_root", "locations", "sha256"}
                or record["format"] != "devbench.publication-authority-locations/1"
                or record["sha256"] != _digest({key: item for key, item in record.items() if key != "sha256"})):
            raise PermissionError("publication_authority_locator_unavailable")
    return {key: item for key, item in value.items() if key != _LOCATION_KEY}


def _publication_location_record(root, service, *, expected_project_id=None, expected_scientific_root=None, inert=False):
    from devbench.engine.scientific_authority import check_schema
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.study.evidence import _read
    publication_service_settings(service)
    record = service[_LOCATION_KEY]
    _owned_location_path(root, directory=True)
    _owned_location_path(root / "workspace.json")
    project = json.loads(_read(root / "workspace.json", 1_000_000))["project"]["id"]
    scientific_root = Path(record["scientific_root"])
    if (type(project) is not str or not re.fullmatch(r"[a-f0-9]{32}", project)
            or record["project_id"] != project or expected_project_id not in (None, project)
            or not scientific_root.is_absolute() or scientific_root.resolve() != scientific_root
            or expected_scientific_root is not None and scientific_root != expected_scientific_root
            or type(record["locations"]) is not list or not 1 <= len(record["locations"]) <= 64):
        raise PermissionError("publication_authority_locator_unavailable")
    paths = []
    for location in record["locations"]:
        if (type(location) is not dict or set(location) != {"root", "path", "device", "inode"}
                or location["root"] not in {"workspace", "scientific"} or type(location["path"]) is not str
                or not 1 <= len(location["path"]) <= 4096 or "\\" in location["path"]
                or any(part in {"", ".", ".."} for part in location["path"].split("/"))
                or any(type(location[key]) is not int or location[key] < 0 for key in ("device", "inode"))):
            raise PermissionError("publication_authority_locator_unavailable")
        relative = Path(location["path"])
        base = root if location["root"] == "workspace" else scientific_root
        if relative.is_absolute() or len(relative.parts) > 32:
            raise PermissionError("publication_authority_locator_unavailable")
        _owned_location_path(base, directory=True)
        path = base / relative
        observed = _owned_location_path(path)
        if (path in paths or (observed.st_dev, observed.st_ino) != (location["device"], location["inode"])):
            raise PermissionError("publication_authority_locator_unavailable")
        ledger = WorkflowOperationLedger(path, readonly=True, inert=inert)
        with ledger._database() as db:
            db.execute("BEGIN")
            if not check_schema(db):
                raise PermissionError("publication_authority_locator_unavailable")
        paths.append(path)
    return record, tuple(paths)


def recorded_publication_ledgers(root, *, expected_project_id=None, expected_scientific_root=None, inert=False):
    """Read bounded recognition locators; never recreate live authority objects."""
    from devbench.study.evidence import _read
    from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
    import sqlite3
    import os
    root = Path(root)
    marker, witness = root / "service.json", root / _LOCATION_LOCK
    try:
        if not marker.exists() and not marker.is_symlink():
            if witness.exists() or witness.is_symlink():
                raise PermissionError("publication_authority_locator_unavailable")
            return ()
        _owned_location_path(marker)
        service = json.loads(_read(marker, 1_000_000))
        if _LOCATION_KEY not in service and not witness.exists() and not witness.is_symlink():
            return ()
        if inert:
            before = _read(marker, 1_000_000)
            committed = _read(witness, len(_LOCATION_COMMITTED) + 1)
            if committed != _LOCATION_COMMITTED:
                raise PermissionError("publication_authority_locator_unavailable")
            result = _publication_location_record(root, json.loads(before), expected_project_id=expected_project_id,
                expected_scientific_root=expected_scientific_root, inert=True)[1]
            if _read(marker, 1_000_000) != before or _read(witness, len(_LOCATION_COMMITTED) + 1) != committed:
                raise PermissionError("publication_authority_locator_unavailable")
            return result
        with _publication_location_lock(root) as lock_descriptor:
            if os.pread(lock_descriptor, len(_LOCATION_COMMITTED) + 1, 0) != _LOCATION_COMMITTED:
                raise PermissionError("publication_authority_locator_unavailable")
            service = json.loads(_read(marker, 1_000_000))
            return _publication_location_record(root, service, expected_project_id=expected_project_id,
                expected_scientific_root=expected_scientific_root)[1]
    except (OSError, sqlite3.Error, OperationBlocked, OperationConflict, ValueError, KeyError, TypeError) as error:
        raise PermissionError("publication_authority_locator_unavailable") from error


def record_publication_authority(owner, registry):
    """Locked append to service recognition; no execution, data, or review grant."""
    from .checkpoints import atomic_json
    from devbench.study.evidence import _read
    from devbench.engine.scientific_authority import check_schema
    import os
    root, scientific_root = owner.app.workspace, owner.root
    marker = root / "service.json"
    _owned_location_path(root, directory=True)
    _owned_location_path(marker)
    path = registry.ledger.path
    if not any(path.is_relative_to(base) for base in (root, scientific_root)):
        raise PermissionError("publication_authority_locator_unavailable")
    _owned_location_path(path)
    with registry.ledger._database() as db:
        db.execute("BEGIN")
        if not check_schema(db):
            raise PermissionError("publication_authority_locator_unavailable")
    with _publication_location_lock(root, write=True) as lock_descriptor:
        service = json.loads(_read(marker, 1_000_000))
        witness = os.pread(lock_descriptor, len(_LOCATION_COMMITTED) + 1, 0)
        if _LOCATION_KEY in service:
            if witness != _LOCATION_COMMITTED:
                raise PermissionError("publication_authority_locator_unavailable")
            previous, paths = _publication_location_record(root, service, expected_project_id=owner.project_id,
                expected_scientific_root=scientific_root)
            if path in paths:
                return
            locations = list(previous["locations"])
        else:
            # First-use ownership is decided under flock. The O_CREAT winner
            # may arrive second; an empty uncommitted witness grants nothing.
            if witness:
                raise PermissionError("publication_authority_locator_unavailable")
            publication_service_settings(service)
            locations = []
        observed = _owned_location_path(path)
        kind = "scientific" if path.is_relative_to(scientific_root) else "workspace"
        base = scientific_root if kind == "scientific" else root
        locations.append({"root": kind, "path": path.relative_to(base).as_posix(),
                          "device": observed.st_dev, "inode": observed.st_ino})
        body = {"format": "devbench.publication-authority-locations/1", "project_id": owner.project_id,
                "scientific_root": str(scientific_root), "locations": sorted(locations, key=lambda item: (item["root"], item["path"]))}
        changed = {**service, _LOCATION_KEY: {**body, "sha256": _digest(body)}}
        if len(canonical_json(changed)) > 1_000_000:
            raise ValueError("publication_authority_locator_unavailable")
        _publication_location_record(root, changed, expected_project_id=owner.project_id,
                                     expected_scientific_root=scientific_root)
        atomic_json(marker, changed)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if os.pwrite(lock_descriptor, _LOCATION_COMMITTED, 0) != len(_LOCATION_COMMITTED):
            raise OSError("publication_authority_locator_unavailable")
        os.ftruncate(lock_descriptor, len(_LOCATION_COMMITTED))
        os.fsync(lock_descriptor)


def _visible_publication_ledgers(owner):
    app = getattr(owner, "app", owner)
    paths = set()
    project_id = getattr(owner, "project_id", None) or getattr(app, "_workspace_state", {}).get("project", {}).get("id")
    owner_root = getattr(owner, "root", None)
    if owner_root is None and callable(getattr(owner, "methods", None)):
        owner_root = getattr(owner.methods(), "root", None)
    scientific_root = getattr(getattr(app, "scientific", None), "root", None)
    if scientific_root is None and hasattr(owner, "project_id") and hasattr(owner, "methods"):
        scientific_root = owner_root
    for root in (owner_root, getattr(app, "workspace", None),
                 getattr(getattr(app, "scientific", None), "root", None)):
        if root is not None:
            root = Path(root).resolve()
            for parent in (root, *root.parents):
                path = parent / "rna-workflow-operations.sqlite"
                if path.is_symlink():
                    raise PermissionError("publication_source_withheld")
                if path.exists():
                    paths.add(path)
                paths.update(recorded_publication_ledgers(parent,
                    expected_project_id=project_id if parent == getattr(app, "workspace", None) else None,
                    expected_scientific_root=scientific_root if parent == getattr(app, "workspace", None) else None))
    return tuple(sorted(paths))


def _known_adaptive_identity(ledger, db, identity, *, exclude_lineage=None):
    from ._publication_registry import PublicationRegistry
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    clause = " AND lineage_id<>?" if exclude_lineage is not None else ""
    lineages = db.execute("SELECT DISTINCT lineage_id FROM scientific_records WHERE kind='adaptive_event'" +
        clause + " LIMIT 513", (exclude_lineage,) if exclude_lineage is not None else ()).fetchall()
    if len(lineages) > 512:
        raise PermissionError("publication_source_withheld")
    for (lineage,) in lineages:
        registry = PublicationRegistry(AdaptiveWorkflow(ledger, lineage))
        subjects, _ = registry._state(db)
        state = registry.workflow._state(db)
        if (identity in subjects or any(subject.source_sha256 == identity for subject in subjects.values())
                or any(item["sha256"] == identity for outcome in state.outcomes.values()
                    for item in {**outcome.get("staged_outputs", {}), **outcome["outputs"]}.values())):
            return True
    return False


def _reject_other_visible_ledger_identity(owner, registry, identity):
    """Recognize foreign authority without importing its metadata or permission."""
    from devbench.engine.workflow_operations import WorkflowOperationLedger
    from devbench.engine.scientific_authority import check_schema
    for path in _visible_publication_ledgers(owner):
        if path == registry.ledger.path.resolve():
            continue
        ledger = WorkflowOperationLedger(path, readonly=True)
        with ledger._database() as db:
            db.execute("BEGIN")
            if check_schema(db) and _known_adaptive_identity(ledger, db, identity):
                raise PermissionError("publication_source_withheld")


def publication_descriptor_for_source(owner, source_sha256):
    """Resolve content identity through the startup-bound coordinator, including aliases."""

    from ._publication_registry import PublicationRegistry

    registry = getattr(owner, "publication_registry", None)
    app = getattr(owner, "app", owner)
    if registry is None:
        registry = getattr(getattr(app, "scientific", None), "publication_registry", None)
    if registry is None:
        # A restored service must not treat a recognized adaptive output as a
        # legacy result merely because no coordinator has yet been attached.
        from devbench.engine.workflow_operations import WorkflowOperationLedger
        from devbench.engine.scientific_authority import check_schema
        from devbench.platform.adaptive_workflow import AdaptiveWorkflow
        found = []
        for path in _visible_publication_ledgers(owner):
            ledger = WorkflowOperationLedger(path, readonly=True)
            with ledger._database() as db:
                db.execute("BEGIN")
                if not check_schema(db):
                    continue
                lineages = db.execute("SELECT DISTINCT lineage_id FROM scientific_records WHERE kind='adaptive_event' LIMIT 513").fetchall()
                if len(lineages) > 512:
                    raise PermissionError("publication_source_withheld")
                for (lineage,) in lineages:
                    candidate = PublicationRegistry(AdaptiveWorkflow(ledger, lineage))
                    subject = candidate.subject_for_source(source_sha256, connection=db, _lineage_only=True)
                    if subject is not None:
                        found.append(candidate.descriptor(subject.subject_id, delivery=getattr(app, "evidence_delivery", None), connection=db))
        if len(found) > 1:
            raise PermissionError("publication_requires_per_subject_projection")
        return found[0] if found else None
    if type(registry) is not PublicationRegistry:
        raise PermissionError("publication_subject_required")
    subject = registry.subject_for_source(source_sha256)
    if subject is None:
        _reject_other_visible_ledger_identity(owner, registry, source_sha256)
        return None
    return registry.descriptor(subject.subject_id, delivery=getattr(app, "evidence_delivery", None))


def project_subject_metadata(owner, value, *, aliases=None, blocked_hashes=()):
    """Use verified per-subject content, never caller-supplied numerical payloads."""

    from ._publication_registry import PublicationRegistry

    app = getattr(owner, "app", owner)
    registry = getattr(owner, "publication_registry", None) or getattr(getattr(app, "scientific", None), "publication_registry", None)
    aliases = aliases or {}
    blocked_hashes = frozenset(blocked_hashes)

    def legacy_node(item, recurse, depth):
        # Resolve child subjects before applying a legacy filter to them. Error
        # payloads remain closed diagnostics rather than traversable evidence.
        if any(type(child) is str and child in blocked_hashes for child in item.values()):
            return {"status": "withheld", "reason": "publication_source_withheld"}
        nested, local = {}, {}
        for key, child in item.items():
            if type(key) is not str:
                continue
            if isinstance(child, (dict, list, tuple)) and _key(key) not in _ERROR_FIELDS:
                nested[key], local[key] = child, None
            else:
                local[key] = child
        return {key: recurse(nested[key], depth + 1) if key in nested else child
                for key, child in public_metadata(local, blocked_hashes=blocked_hashes).items()}

    if registry is None:
        def unmanaged(item, depth=0):
            if depth > 32:
                return None
            if isinstance(item, (list, tuple)):
                return [unmanaged(child, depth + 1) for child in item]
            if not isinstance(item, dict):
                return public_metadata(item, blocked_hashes=blocked_hashes)
            identities = [child for child in item.values() if type(child) is str and _SHA.fullmatch(child)]
            identities.extend(child for child in item.get("artifact_ids", ()) if type(child) is str)
            try:
                if any(publication_descriptor_for_source(owner, aliases.get(identity, identity)) is not None for identity in identities):
                    raise PermissionError("publication_subject_required")
            except PermissionError:
                return {"status": "withheld", "reason": "publication_source_withheld"}
            return legacy_node(item, unmanaged, depth)
        return unmanaged(value)
    if type(registry) is not PublicationRegistry:
        raise PermissionError("publication_subject_required")
    delivery = getattr(app, "evidence_delivery", None)
    with registry.ledger._database() as db:
        db.execute("BEGIN")
        subjects, _ = registry._state(db)

        def visit(item, depth=0):
            if depth > 32:
                return None
            if isinstance(item, (tuple, list)):
                return [visit(child, depth + 1) for child in item]
            if not isinstance(item, dict):
                return public_metadata(item, blocked_hashes=blocked_hashes)
            identities = set()
            for key, child in item.items():
                if type(child) is str and _SHA.fullmatch(child):
                    identities.add(aliases.get(child, child))
                elif key in {"artifact_ids", "source_sha256s"} and isinstance(child, list):
                    identities.update(aliases.get(identity, identity) for identity in child if type(identity) is str)
            matched = set()
            try:
                for identity in identities:
                    if identity in subjects:
                        registry._subject(db, identity)
                        matched.add(identity)
                    else:
                        subject = registry.subject_for_source(identity, connection=db)
                        if subject is not None:
                            matched.add(subject.subject_id)
                        else:
                            _reject_other_visible_ledger_identity(owner, registry, identity)
                if len(matched) > 1:
                    raise PermissionError("publication_requires_per_subject_projection")
                if matched:
                    subject_id = next(iter(matched))
                    projection = registry.projection(subject_id, delivery=delivery, connection=db)
                    if item.get("format") == "devbench.adaptive-publication/1":
                        return projection
                    identity_fields = {key: child for key, child in item.items() if key in {
                        "id", "ref", "result_id", "version_id", "artifact_id", "artifact_ids", "run_id",
                        "source_sha256", "subject_id", "status", "operation_kind", "updated_at"}}
                    return {**public_metadata(identity_fields), "publication": projection}
            except (PermissionError, ValueError, KeyError, OSError):
                return {"status": "withheld", "reason": "publication_source_withheld"}
            return legacy_node(item, visit, depth)

        return visit(value)


@dataclass(frozen=True)
class PublicationGuard:
    blocked_hashes: frozenset[str] = frozenset()
    available: bool = True
    roots: tuple[Path, ...] = ()
    registered_subjects: frozenset[str] = frozenset()
    inert: bool = False

    def require_subject(self, subject_id, source_sha256):
        if subject_id not in self.registered_subjects:
            raise PermissionError("publication_subject_required")
        self.require(subject_id, source_sha256)

    def require(self, *digests):
        if not self.available or any(digest in self.blocked_hashes for digest in digests if isinstance(digest, str)):
            raise PermissionError("publication_source_withheld")
        for root in self.roots:
            workspace_guard(root, _snapshot=True, inert=self.inert).require(*digests)

    def project(self, value, *, descriptor=None):
        if not self.available:
            return {"status": "withheld", "reason": "publication_source_withheld"}
        blocked = set(self.blocked_hashes)
        for root in self.roots:
            current = workspace_guard(root, _snapshot=True, inert=self.inert)
            if not current.available:
                return {"status": "withheld", "reason": "publication_source_withheld"}
            blocked.update(current.blocked_hashes)
        return public_metadata(value, descriptor=descriptor, blocked_hashes=blocked)


def workspace_guard(root, *, _snapshot=False, inert=False):
    """Read current T14/T15 state by content identity, independent of a route alias."""

    from ._publication_registry import PublicationRegistry
    from devbench.engine.partition_authority import embargoed
    from devbench.engine.scientific_authority import _row, check_schema
    from devbench.engine.workflow_operations import OperationBlocked, OperationConflict, WorkflowOperationLedger
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    import sqlite3

    root = Path(root).resolve()
    blocked = set()
    subjects = set()
    try:
        ledgers = {parent / "rna-workflow-operations.sqlite" for parent in (root, *root.parents)}
        for parent in (root, *root.parents):
            ledgers.update(recorded_publication_ledgers(parent, inert=inert))
        for path in sorted(ledgers):
            if path.is_symlink():
                raise OperationConflict("Unsafe publication authority path")
            if not path.exists():
                continue
            ledger = WorkflowOperationLedger(path, readonly=True, inert=inert)
            with ledger._database() as db:
                db.execute("BEGIN")
                if not check_schema(db):
                    continue
                embargoes = db.execute("SELECT * FROM scientific_records WHERE kind='execution_event' "
                                       "AND record_id GLOB 'embargo-*' LIMIT 8193").fetchall()
                lineages = db.execute("SELECT DISTINCT lineage_id FROM scientific_records WHERE kind='adaptive_event' "
                                      "LIMIT 513").fetchall()
                if len(embargoes) > 8192 or len(lineages) > 512:
                    raise OperationConflict("Publication authority inventory exceeds its bound")
                for row in embargoes:
                    record = _row(row)["record"]
                    identity = record["state"].get("sha256")
                    if type(identity) is str and _SHA.fullmatch(identity) and embargoed(db, identity):
                        blocked.add(identity)
                for (lineage,) in lineages:
                    workflow = AdaptiveWorkflow(ledger, lineage)
                    registry = PublicationRegistry(workflow)
                    blocked.update(registry.blocked_sources(db))
                    subjects.update(registry._state(db)[0])
                    state = workflow._state(db)
                    blocked.update(state.artifacts[version][0]["sha256"] for version in state.stale_versions)
                    for attempt, result in state.outcomes.items():
                        dispatch = state.dispatches[attempt]["envelope"]
                        for port, artifact in {**result.get("staged_outputs", {}), **result["outputs"]}.items():
                            try:
                                current = workflow._current_use(db, dispatch["step"]["step_id"], port, "compute",
                                                                dispatch["generation"])
                            except OperationBlocked:
                                blocked.add(artifact["sha256"])
                            else:
                                if current.sha256 != artifact["sha256"]:
                                    blocked.add(artifact["sha256"])
        return PublicationGuard(frozenset(blocked), roots=() if _snapshot else (root,), registered_subjects=frozenset(subjects), inert=inert)
    except (OSError, sqlite3.Error, OperationBlocked, OperationConflict, ValueError, KeyError, TypeError):
        return PublicationGuard(available=False)
