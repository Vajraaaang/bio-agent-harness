"""Project-bound scientific metadata over the existing method SQLite store.

This layer stores references and review decisions. It never initializes a method
workspace, reconciles workers, launches computation, or changes sealed receipts.
Legacy accepted revisions remain MethodService records with their original IDs.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from devharness_core.provenance import canonical_json
from devharness_core.methods import MethodContract
from .method_executor import _path
from .methods import MethodService, _digest, _label, _now, _reason


SCHEMA_VERSION = 2
KINDS = frozenset({"study", "dataset", "dataset_version", "analysis", "analysis_version",
                   "run", "attempt", "result", "result_version", "validation", "review",
                   "selection", "selection_event", "import", "import_event"})
VERSIONED = frozenset({"dataset", "analysis", "result"})


def _json(value, *, maximum=200_000):
    try:
        encoded = canonical_json(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Scientific metadata must be finite JSON") from error
    if len(encoded) > maximum:
        raise ValueError("Scientific metadata exceeds the bounded record limit")
    return json.loads(encoded)


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise ValueError("A bounded nonempty identity is required")
    return value


def _expected(value, expected):
    if type(expected) is not int or expected < 0:
        raise ValueError("An integer expected_revision is required")
    if value["revision"] != expected:
        raise ValueError("Scientific record is stale; reload its current revision")


def _ref(kind, identity, version=None):
    return {"object_type": kind, "object_id": identity, "version_id": version}


def _legacy_fingerprint(db):
    digest = hashlib.sha256()
    for table, order in (("records", "kind,id"), ("heads", "analysis_id"),
                         ("audit", "sequence"), ("schema_version", "version")):
        digest.update(table.encode())
        for row in db.execute("SELECT * FROM " + table + " ORDER BY " + order):
            digest.update(canonical_json(list(row)))
    return digest.hexdigest()


def _file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_records(db):
    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("Method database integrity check failed")
    for encoded, expected in db.execute("SELECT payload,sha256 FROM records"):
        if _digest(json.loads(encoded)) != expected:
            raise ValueError("Method record digest mismatch")
    previous, count = None, 0
    for sequence, encoded, expected in db.execute("SELECT sequence,payload,sha256 FROM audit ORDER BY sequence"):
        event = json.loads(encoded)
        if (sequence != count + 1 or event.get("sequence") != sequence
                or event.get("previous") != previous or _digest(event) != expected):
            raise ValueError("Method audit chain mismatch")
        count, previous = sequence, expected


class ScientificObjectService:
    """Local metadata; actor labels are caller assertions, not authentication.

    Opening is read-only by default and requires explicit prior migration.
    Mutable objects have their own revision; every mutation also advances the
    project snapshot revision. MethodService remains authoritative for execution.
    """

    def __init__(self, workspace, project_id, *, readonly=True):
        self.root = _path(workspace)
        self.database = _path(self.root / "methods.sqlite3")
        self.project_id = _identity(project_id)
        self.readonly = bool(readonly)
        if not self.database.is_file():
            raise ValueError("Scientific store requires an existing initialized method database")
        with self._db() as db:
            self._binding(db)

    @classmethod
    def migrate(cls, workspace, project_id, *, actor):
        """Explicit v1 → v2 upgrade; retain a verified pre-migration SQLite backup."""
        root, project_id, actor = _path(workspace), _identity(project_id), _label(actor)
        database = _path(root / "methods.sqlite3")
        if not database.is_file():
            raise ValueError("Migration requires an existing initialized method database")
        db = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=30)
        backup_path = None
        try:
            db.execute("BEGIN IMMEDIATE")
            versions = db.execute("SELECT version FROM schema_version").fetchall()
            if versions == [(SCHEMA_VERSION,)]:
                row = db.execute("SELECT project_id,revision,backup_path FROM scientific_metadata WHERE singleton=1").fetchone()
                if row is None or row[0] != project_id:
                    raise ValueError("Scientific store project identity mismatch")
                db.rollback()
                return {"schema_version": SCHEMA_VERSION, "project_id": project_id,
                        "backup_path": row[2], "migrated": False}
            if versions != [(1,)]:
                raise ValueError("Unsupported method workspace schema")
            _validate_records(db)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='scientific_metadata'").fetchone():
                raise ValueError("Unexpected scientific metadata in legacy schema")
            before = _legacy_fingerprint(db)
            backup_path = _path(root / ("methods.before-scientific-v2-" + uuid.uuid4().hex + ".sqlite3"))
            # A second read connection can back up the committed v1 snapshot
            # while this reserved writer lock prevents concurrent mutations.
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as backup:
                    source.backup(backup)
                    if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)] or _legacy_fingerprint(backup) != before:
                        raise ValueError("Migration backup verification failed")
            backup_path.chmod(0o600)
            with backup_path.open("rb") as retained:
                os.fsync(retained.fileno())
            backup_manifest = {"format": "devbench.scientific-backup/1", "project_id": project_id,
                               "source_database": str(database), "backup_filename": backup_path.name,
                               "schema_version": 1, "sha256": _file_digest(backup_path),
                               "legacy_fingerprint": before}
            with _path(Path(str(backup_path) + ".manifest.json")).open("xb") as retained:
                retained.write(canonical_json(backup_manifest))
                retained.flush()
                os.fsync(retained.fileno())
            _sync_directory(root)
            db.execute("CREATE TABLE scientific_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                       "project_id TEXT NOT NULL, revision INTEGER NOT NULL, backup_path TEXT NOT NULL)")
            db.execute("INSERT INTO scientific_metadata VALUES(1,?,0,?)", (project_id, str(backup_path)))
            db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            MethodService._audit(db, actor, "scientific_store_migrated", {
                "project_id": project_id, "from_schema": 1, "to_schema": SCHEMA_VERSION,
                "backup_path": str(backup_path), "legacy_fingerprint": before})
            db.commit()
        except sqlite3.DatabaseError as error:
            db.rollback()
            raise ValueError("Unsupported or invalid method workspace schema") from error
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
        return {"schema_version": SCHEMA_VERSION, "project_id": project_id,
                "backup_path": str(backup_path), "migrated": True}

    @classmethod
    def restore_backup(cls, workspace, backup_path, project_id, *, actor):
        """Explicit offline recovery of corrupt/missing metadata, never valid rollback.

        The migration snapshot predates scientific records. Recovery discloses
        that boundary and retains invalid primary bytes; no jobs are restarted.
        """
        root, backup_path = _path(workspace), _path(backup_path)
        project_id, actor = _identity(project_id), _label(actor)
        database = _path(root / "methods.sqlite3")
        if backup_path.parent != root or re.fullmatch(r"methods\.before-scientific-v2-[0-9a-f]{32}\.sqlite3", backup_path.name) is None:
            raise ValueError("Use a retained migration backup from this workspace")
        manifest_path = _path(Path(str(backup_path) + ".manifest.json"))
        if not backup_path.is_file() or not manifest_path.is_file() or manifest_path.stat().st_size > 8192:
            raise ValueError("A retained verified migration backup and manifest are required")
        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (ValueError, UnicodeError) as error:
            raise ValueError("Invalid migration backup manifest") from error
        if (not isinstance(manifest, dict) or manifest.get("format") != "devbench.scientific-backup/1"
                or manifest.get("project_id") != project_id or manifest.get("source_database") != str(database)
                or manifest.get("backup_filename") != backup_path.name or manifest.get("schema_version") != 1):
            raise ValueError("Migration backup project identity or schema mismatch")
        if _file_digest(backup_path) != manifest.get("sha256"):
            raise ValueError("Migration backup hash mismatch")
        try:
            with closing(sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("SELECT version FROM schema_version").fetchall() != [(1,)]:
                    raise ValueError("Unsupported migration backup schema")
                _validate_records(db)
                if _legacy_fingerprint(db) != manifest["legacy_fingerprint"]:
                    raise ValueError("Migration backup fingerprint mismatch")
        except sqlite3.DatabaseError as error:
            raise ValueError("Invalid migration backup database") from error
        if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Resolve active or interrupted database journals before explicit recovery")
        # Holding every existing method lease prevents recovery over a running
        # controller. The loopback service must also be stopped by its operator.
        leases = []
        try:
            for path in sorted((root / "runs").glob("*/lease")):
                lease = _path(path).open("rb")
                leases.append(lease)
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError("Stop the active method controller before metadata recovery") from error
            if database.exists():
                corrupt = False
                try:
                    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                        versions = db.execute("SELECT version FROM schema_version").fetchall()
                        if versions not in ([(1,)], [(SCHEMA_VERSION,)]):
                            raise ValueError("Recovery refuses an unsupported schema downgrade")
                        if versions == [(SCHEMA_VERSION,)]:
                            row = db.execute("SELECT project_id,revision FROM scientific_metadata WHERE singleton=1").fetchone()
                            if row is not None and row[0] != project_id:
                                raise ValueError("Primary scientific project identity mismatch")
                            if row is None or type(row[1]) is not int or row[1] < 0:
                                corrupt = True
                        try:
                            _validate_records(db)
                        except (ValueError, TypeError, KeyError):
                            corrupt = True
                except sqlite3.DatabaseError:
                    corrupt = True
                if not corrupt:
                    raise ValueError("Recovery refuses to roll back a valid primary database")
            temporary = _path(root / ("scientific-recovery-" + uuid.uuid4().hex + ".sqlite3"))
            shutil.copyfile(backup_path, temporary)
            temporary.chmod(0o600)
            with closing(sqlite3.connect(temporary)) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("CREATE TABLE scientific_metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                           "project_id TEXT NOT NULL, revision INTEGER NOT NULL, backup_path TEXT NOT NULL)")
                db.execute("INSERT INTO scientific_metadata VALUES(1,?,0,?)", (project_id, str(backup_path)))
                db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
                MethodService._audit(db, actor, "scientific_store_recovered", {
                    "project_id": project_id, "backup_path": str(backup_path), "backup_sha256": manifest["sha256"],
                    "restored_scientific_revision": 0, "restores_execution_grants": False})
                db.commit()
                _validate_records(db)
            with temporary.open("rb") as retained:
                os.fsync(retained.fileno())
            quarantine = None
            if database.exists():
                quarantine = _path(root / ("methods.invalid-" + uuid.uuid4().hex + ".sqlite3"))
                os.link(database, quarantine)
                _sync_directory(root)
            os.replace(temporary, database)
            _sync_directory(root)
            return {"recovered": True, "project_id": project_id, "revision": 0,
                    "backup_path": str(backup_path), "quarantine_path": str(quarantine) if quarantine else None,
                    "restored_schema_version": SCHEMA_VERSION, "restores_execution_grants": False,
                    "disclosure": "Restored the pre-migration method snapshot; later method and scientific metadata is unavailable. Retained execution files were left in place; no workers or execution permissions were restored."}
        finally:
            for lease in leases:
                lease.close()

    @contextmanager
    def _db(self, *, write=False, connection=None):
        if write and self.readonly:
            raise PermissionError("Scientific store is read-only")
        if connection is not None:
            locations = connection.execute("PRAGMA database_list").fetchall()
            if not connection.in_transaction or not any(row[1] == "main" and row[2] == str(self.database) for row in locations):
                raise ValueError("Scientific mutation requires the existing method database transaction")
            self._binding(connection)
            yield connection
            return
        _path(self.database)
        db = sqlite3.connect(self.database.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=30)
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
                db.execute("BEGIN")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _binding(self, db):
        try:
            if db.execute("SELECT version FROM schema_version").fetchall() != [(SCHEMA_VERSION,)]:
                raise ValueError("Scientific schema is not initialized; explicit migration is required")
            row = db.execute("SELECT project_id,revision FROM scientific_metadata WHERE singleton=1").fetchone()
        except sqlite3.DatabaseError as error:
            raise ValueError("Unsupported or uninitialized scientific schema") from error
        if row is None or row[0] != self.project_id:
            raise ValueError("Scientific store project identity mismatch")
        if type(row[1]) is not int or row[1] < 0:
            raise ValueError("Invalid scientific project revision")
        return {"project_id": row[0], "revision": row[1]}

    def _get(self, db, kind, identity):
        if kind not in KINDS:
            raise ValueError("Unknown scientific object type")
        value = MethodService._get(db, "scientific_" + kind, _identity(identity))
        if value.get("project_id") != self.project_id:
            raise ValueError("Scientific record project identity mismatch")
        if kind in {"dataset_version", "analysis_version"}:
            logical = kind.removesuffix("_version")
            field = "manifest" if logical == "dataset" else "definition"
            if value.get("version_id") != identity or _digest({logical + "_id": value[logical + "_id"], field: value[field]}) != identity:
                raise ValueError("Scientific version content identity mismatch")
        elif kind == "result_version":
            fields = ("result_id", "interpretation", "run_id", "attempt_id", "analysis_version_id", "artifact_ids", "validation_id")
            if value.get("version_id") != identity or _digest({key: value[key] for key in fields}) != identity:
                raise ValueError("Result version content identity mismatch")
        elif kind == "run":
            fields = ("analysis_id", "analysis_version_id", "dataset_version_ids", "environment", "trigger", "actor")
            if value.get("run_id") != identity or _digest({key: value[key] for key in fields}) != value["request_fingerprint"]:
                raise ValueError("Scientific invocation fingerprint mismatch")
        return value

    def _put(self, db, kind, identity, value, *, update=False):
        if kind not in KINDS or value.get("project_id") != self.project_id:
            raise ValueError("Invalid scientific record type or project identity")
        MethodService._put(db, "scientific_" + kind, identity, _json(value), update=update)

    def _event(self, db, actor, action, payload):
        self._binding(db)
        MethodService._audit(db, actor, "scientific_" + action, {"project_id": self.project_id, **payload})
        db.execute("UPDATE scientific_metadata SET revision=revision+1 WHERE singleton=1")

    def _new(self, *, actor):
        return {"project_id": self.project_id, "created_by": _label(actor), "created_at": _now()}

    def _list(self, db, kind):
        if kind not in KINDS:
            raise ValueError("Unknown scientific object type")
        return [self._get(db, kind, row[0]) for row in db.execute(
            "SELECT id FROM records WHERE kind=? ORDER BY rowid", ("scientific_" + kind,)).fetchall()]

    def get(self, kind, identity):
        with self._db() as db:
            self._binding(db)
            return self._get(db, kind, identity)

    def list(self, kind):
        with self._db() as db:
            self._binding(db)
            return self._list(db, kind)

    def snapshot(self):
        with self._db() as db:
            value = self._binding(db)
            for plural, kind in (("studies", "study"), ("datasets", "dataset"), ("analyses", "analysis"),
                                 ("runs", "run"), ("attempts", "attempt"), ("results", "result"),
                                 ("selections", "selection")):
                value[plural] = self._list(db, kind)
            return value

    catalog = snapshot

    def touch(self, *, actor, action, payload, expected_revision=None, db=None):
        """Audit associated adapter metadata in the same project transaction."""
        action, payload = _identity(action), _json(payload)
        if not isinstance(payload, dict):
            raise ValueError("Scientific audit payload must be an object")
        with self._db(write=True, connection=db) as transaction:
            if expected_revision is not None:
                _expected(self._binding(transaction), expected_revision)
            self._event(transaction, actor, action, payload)
            return self._binding(transaction)["revision"]

    def record_import(self, source_kind, source_id, mappings, status, *, actor, expected_revision, evidence=None):
        """Record an explicit mapping; this never groups or backfills executions."""
        source_kind, source_id, mappings = _identity(source_kind), _identity(source_id), _json(mappings)
        supplied_evidence = _json(evidence or {})
        if status not in {"unresolved", "imported", "linked"} or not isinstance(mappings, dict) or not isinstance(supplied_evidence, dict):
            raise ValueError("Import requires mappings and an unresolved, linked or imported status")
        identity = _digest({"project_id": self.project_id, "source_kind": source_kind, "source_id": source_id})
        with self._db(write=True) as db:
            exists = db.execute("SELECT 1 FROM records WHERE kind='scientific_import' AND id=?", (identity,)).fetchone()
            prior = self._get(db, "import", identity) if exists else None
            unchanged = (prior is not None and prior["mappings"] == mappings and prior["status"] == status
                         and prior["evidence"] == supplied_evidence)
            if not unchanged:
                _expected(self._binding(db), expected_revision)
            evidence = None
            if status in {"imported", "linked"}:
                required = {"study_id", "analysis_id", "analysis_version_id", "dataset_version_ids"}
                resolved = mappings
                if {"study_id", "dataset_ref", "analysis_ref"} <= set(mappings):
                    dataset_ref, analysis_ref = mappings["dataset_ref"], mappings["analysis_ref"]
                    dataset, analysis_version = self.resolve(dataset_ref), self.resolve(analysis_ref)
                    if dataset_ref["object_type"] != "dataset" or analysis_ref["object_type"] != "analysis":
                        raise ValueError("Use exact dataset and analysis import mappings")
                    resolved = {"study_id": mappings["study_id"], "analysis_id": analysis_version["analysis_id"],
                                "analysis_version_id": analysis_version["version_id"], "dataset_version_ids": [dataset["version_id"]]}
                if source_kind not in {"method", "method_run", "workbench_job"} or not required <= set(resolved):
                    raise ValueError("Complete imports require a supported controller and explicit scientific mappings")
                analysis = self._get(db, "analysis", resolved["analysis_id"])
                version = self._get(db, "analysis_version", resolved["analysis_version_id"])
                if analysis["study_id"] != resolved["study_id"] or version["analysis_id"] != analysis["analysis_id"]:
                    raise ValueError("Import mapping belongs to another Study or analysis")
                inputs = self._input_bindings(db, resolved["dataset_version_ids"], study_id=resolved["study_id"])
                if source_kind == "workbench_job":
                    # This reference-only link is supplied by the existing
                    # authenticated Workbench controller after results() checks.
                    # It creates no scientific Run/attempt or validation claim.
                    if (status != "linked" or supplied_evidence.get("job_id") != source_id
                            or supplied_evidence.get("execution_grouping_inferred") is not False
                            or supplied_evidence.get("missing_mappings") != []):
                        raise ValueError("Workbench links require explicit controller evidence and complete mappings")
                else:
                    native = MethodService._get(db, "run", source_id)
                    if native["status"] not in {"succeeded", "failed"} or not MethodService(self.root, readonly=True).provenance(source_id)["valid"]:
                        raise ValueError("Legacy controller evidence is unavailable or invalid")
                    definition = version["definition"]
                    steps = definition.get("steps") or [{"method_version_id": item, "parameters": definition["parameters"]}
                                                          for item in definition["method_version_ids"]]
                    if not any(step["method_version_id"] == native["method_version_id"]
                            and step["parameters"] == native["parameters"]
                            and native["inputs"] == step.get("inputs", inputs)
                            and set(step.get("inputs", inputs).values()) <= set(inputs.values()) for step in steps):
                        raise ValueError("Import mapping does not match the original controller invocation")
                    for artifact in native["outputs"].values():
                        self._artifact(db, artifact)
                    evidence = native["evidence"]
                    if supplied_evidence.get("receipt", evidence) != evidence:
                        raise ValueError("Supplied legacy receipt differs from retained controller evidence")
            if unchanged:
                return prior
            value = {**self._new(actor=actor), "import_id": identity, "source_kind": source_kind, "source_id": source_id,
                     "mappings": mappings, "status": status, "source_evidence": evidence, "evidence": supplied_evidence,
                     "revision": prior["revision"] + 1 if prior else 1, "ref": _ref("import", identity)}
            event_id = uuid.uuid4().hex
            self._put(db, "import_event", event_id, {**value, "import_event_id": event_id,
                      "previous_revision": prior["revision"] if prior else None})
            self._put(db, "import", identity, value, update=bool(prior))
            self._event(db, actor, "legacy_import_recorded", {"import_id": identity, "import_event_id": event_id})
        return value

    def _artifact(self, db, identity):
        value = MethodService._get(db, "artifact", _identity(identity))
        # This read-only service does not initialize, recover, or reconcile.
        MethodService(self.root, readonly=True).artifact_path(identity)
        return value

    def resolve(self, reference):
        if not isinstance(reference, dict) or set(reference) != {"object_type", "object_id", "version_id"}:
            raise ValueError("An exact typed object/version reference is required")
        kind, identity, version = reference["object_type"], reference["object_id"], reference["version_id"]
        with self._db() as db:
            self._binding(db)
            if kind == "artifact":
                if version != identity:
                    raise ValueError("Artifact version must match the retained artifact identity")
                return {**self._artifact(db, identity), "version_id": identity, "ref": reference}
            logical = self._get(db, kind, identity)
            if kind not in VERSIONED:
                if version is not None:
                    raise ValueError("This scientific object has no version identity")
                return logical
            if not version:
                raise ValueError("An exact version is required; latest is not an exact reference")
            value = self._get(db, kind + "_version", version)
            if value[kind + "_id"] != identity:
                raise ValueError("Version belongs to a different logical object")
            artifacts = value["artifact_ids"] if kind == "result" else []
            if kind == "dataset":
                artifacts = [member["artifact_id"] for member in value["manifest"]["members"].values()]
            for artifact in artifacts:
                self._artifact(db, artifact)
            return value

    def create_study(self, name, *, aim, actor, expected_revision):
        identity = uuid.uuid4().hex
        value = {**self._new(actor=actor), "study_id": identity, "name": _label(name),
                 "aim": _reason(aim), "revision": 1, "ref": _ref("study", identity)}
        with self._db(write=True) as db:
            _expected(self._binding(db), expected_revision)
            self._put(db, "study", identity, value)
            self._event(db, actor, "study_created", {"study_id": identity})
        return value

    def update_study(self, study_id, *, name, aim, actor, expected_revision):
        with self._db(write=True) as db:
            value = self._get(db, "study", study_id)
            _expected(value, expected_revision)
            value.update(name=_label(name), aim=_reason(aim), revision=value["revision"] + 1)
            self._put(db, "study", study_id, value, update=True)
            self._event(db, actor, "study_updated", {"study_id": study_id})
        return value

    def _manifest(self, db, manifest):
        value = _json(manifest)
        if (not isinstance(value, dict) or set(value) != {"members", "metadata"}
                or not isinstance(value["metadata"], dict) or not isinstance(value["members"], dict)
                or not 1 <= len(value["members"]) <= 64):
            raise ValueError("Dataset manifest requires bounded members and scientific metadata")
        members = {}
        for name, member in value["members"].items():
            _identity(name)
            if not isinstance(member, dict) or "artifact_id" not in member or set(member) - {
                    "artifact_id", "sha256", "size_bytes", "artifact_type"}:
                raise ValueError("Dataset members require retained artifact identities, with locations stored separately")
            artifact = self._artifact(db, member["artifact_id"])
            canonical = {key: artifact[key] for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")}
            if any(member[key] != canonical[key] for key in member):
                raise ValueError("Dataset member hash or manifest does not match the retained artifact")
            members[name] = canonical
        return {"members": members, "metadata": value["metadata"]}

    def _definition(self, db, definition):
        value = _json(definition)
        required = {"method_version_ids", "code_identity", "parameters", "validation_policy"}
        allowed = required | {"task", "objective", "steps", "environment"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - allowed:
            raise ValueError("Analysis definition requires exact methods, code, parameters and validation policy")
        if (not isinstance(value["method_version_ids"], list) or len(value["method_version_ids"]) > 64
                or any(not isinstance(value[key], dict) for key in required - {"method_version_ids"})):
            raise ValueError("Invalid scientific execution definition")
        if len(set(value["method_version_ids"])) != len(value["method_version_ids"]):
            raise ValueError("Duplicate method version identity")
        contracts = {}
        for identity in value["method_version_ids"]:
            method = MethodService._get(db, "method", _identity(identity))
            contract = MethodContract.from_dict(method["contract"])
            if contract.version_id != identity:
                raise ValueError("Retained MethodContract identity mismatch")
            contracts[identity] = contract
            self._artifact(db, method["source_artifact"])
            if method["environment_artifact"]:
                self._artifact(db, method["environment_artifact"])
        if not contracts:
            if (value["code_identity"] or value["parameters"] or value["validation_policy"] != {"status": "unplanned"}
                    or value.get("environment") or value.get("steps")):
                raise ValueError("A draft definition must remain explicitly unplanned without execution claims")
            return value
        actual_code = {identity: contract.to_dict()["implementation"] for identity, contract in contracts.items()}
        actual_policy = {identity: contract.to_dict()["validation"] for identity, contract in contracts.items()}
        actual_environment = {identity: contract.to_dict()["environment"] for identity, contract in contracts.items()}
        if (value["code_identity"] != actual_code or value["validation_policy"] != actual_policy
                or value.get("environment", actual_environment) != actual_environment):
            raise ValueError("Execution code, environment and validation claims must match retained MethodContract identities")
        value["environment"] = actual_environment
        if "steps" in value:
            if not isinstance(value["steps"], list) or not 1 <= len(value["steps"]) <= 64:
                raise ValueError("Execution steps must be a bounded nonempty list")
            keys = set()
            for step in value["steps"]:
                if (not isinstance(step, dict) or not {"step_key", "method_version_id", "parameters"} <= set(step)
                        or set(step) - {"step_key", "method_version_id", "parameters", "inputs"}
                        or step["method_version_id"] not in value["method_version_ids"]
                        or not isinstance(step["parameters"], dict)):
                    raise ValueError("Execution step requires an exact method version, parameters and step_key")
                key = _identity(step["step_key"])
                if key in keys:
                    raise ValueError("Duplicate execution step_key")
                keys.add(key)
                step["parameters"] = contracts[step["method_version_id"]].validate_parameters(step["parameters"])
                if "inputs" in step:
                    if not isinstance(step["inputs"], dict):
                        raise ValueError("Execution step input bindings must be a mapping")
                    for identity in step["inputs"].values():
                        self._artifact(db, identity)
            expected_parameters = {step["step_key"]: step["parameters"] for step in value["steps"]}
            if value["parameters"] != expected_parameters:
                raise ValueError("Definition parameters must match its exact execution step parameters")
        else:
            if len(contracts) != 1:
                raise ValueError("Multiple methods require explicit execution steps")
            value["parameters"] = next(iter(contracts.values())).validate_parameters(value["parameters"])
        return value

    def _version(self, db, kind, logical, content, *, actor):
        field = "manifest" if kind == "dataset" else "definition"
        identity = _digest({kind + "_id": logical[kind + "_id"], field: content})
        version = {**self._new(actor=actor), kind + "_id": logical[kind + "_id"],
                   "version_id": identity, field: content,
                   "ref": _ref(kind, logical[kind + "_id"], identity)}
        existing = db.execute("SELECT 1 FROM records WHERE kind=? AND id=?", ("scientific_" + kind + "_version", identity)).fetchone()
        if existing:
            version = self._get(db, kind + "_version", identity)
        else:
            self._put(db, kind + "_version", identity, version)
        logical.update(current_version_id=identity, ref=version["ref"])
        return logical

    def create_dataset(self, study_id, name, manifest, *, actor, expected_revision, locations=None):
        with self._db(write=True) as db:
            _expected(self._get(db, "study", study_id), expected_revision)
            identity = uuid.uuid4().hex
            value = {**self._new(actor=actor), "dataset_id": identity, "study_id": study_id,
                     "name": _label(name), "revision": 1, "locations": self._locations(locations or {})}
            self._version(db, "dataset", value, self._manifest(db, manifest), actor=actor)
            self._put(db, "dataset", identity, value)
            self._event(db, actor, "dataset_created", {"dataset_id": identity, "version_id": value["current_version_id"]})
        return value

    @staticmethod
    def _locations(locations):
        if not isinstance(locations, dict) or len(locations) > 64:
            raise ValueError("Dataset locations must be a bounded mapping")
        for name, location in locations.items():
            _identity(name)
            if not isinstance(location, str) or len(location) > 4096:
                raise ValueError("Invalid dataset location")
            _path(location)
        return dict(locations)

    def add_dataset_version(self, dataset_id, manifest, *, actor, expected_revision):
        with self._db(write=True) as db:
            value = self._get(db, "dataset", dataset_id)
            _expected(value, expected_revision)
            original = value["current_version_id"]
            self._version(db, "dataset", value, self._manifest(db, manifest), actor=actor)
            if value["current_version_id"] != original:
                value["revision"] += 1
                self._put(db, "dataset", dataset_id, value, update=True)
                self._event(db, actor, "dataset_version_added", {"dataset_id": dataset_id, "version_id": value["current_version_id"]})
        return value

    def rename_dataset(self, dataset_id, name, *, actor, expected_revision):
        return self._edit_logical("dataset", dataset_id, {"name": _label(name)}, actor, expected_revision)

    def rename_analysis(self, analysis_id, name, *, actor, expected_revision):
        return self._edit_logical("analysis", analysis_id, {"name": _label(name)}, actor, expected_revision)

    def set_dataset_locations(self, dataset_id, locations, *, actor, expected_revision):
        return self._edit_logical("dataset", dataset_id, {"locations": self._locations(locations)}, actor, expected_revision)

    def _edit_logical(self, kind, identity, fields, actor, expected_revision):
        with self._db(write=True) as db:
            value = self._get(db, kind, identity)
            _expected(value, expected_revision)
            value.update(fields, revision=value["revision"] + 1)
            self._put(db, kind, identity, value, update=True)
            self._event(db, actor, kind + "_updated", {kind + "_id": identity, **fields})
        return value

    def create_analysis(self, study_id, name, definition, *, actor, expected_revision, legacy_analysis_id=None):
        with self._db(write=True) as db:
            _expected(self._get(db, "study", study_id), expected_revision)
            if legacy_analysis_id is not None and not db.execute("SELECT 1 FROM heads WHERE analysis_id=?", (_identity(legacy_analysis_id),)).fetchone():
                raise ValueError("Unknown legacy method analysis")
            identity = uuid.uuid4().hex
            value = {**self._new(actor=actor), "analysis_id": identity, "study_id": study_id, "name": _label(name),
                     "revision": 1, "legacy_analysis_id": legacy_analysis_id}
            self._version(db, "analysis", value, self._definition(db, definition), actor=actor)
            self._put(db, "analysis", identity, value)
            self._event(db, actor, "analysis_created", {"analysis_id": identity, "version_id": value["current_version_id"]})
        return value

    def add_analysis_version(self, analysis_id, definition, *, actor, expected_revision, db=None):
        with self._db(write=True, connection=db) as transaction:
            value = self._get(transaction, "analysis", analysis_id)
            _expected(value, expected_revision)
            original = value["current_version_id"]
            self._version(transaction, "analysis", value, self._definition(transaction, definition), actor=actor)
            if value["current_version_id"] != original:
                value["revision"] += 1
                self._put(transaction, "analysis", analysis_id, value, update=True)
                self._event(transaction, actor, "analysis_version_added", {"analysis_id": analysis_id, "version_id": value["current_version_id"]})
        return value

    def input_bindings(self, dataset_version_ids):
        with self._db() as db:
            self._binding(db)
            return self._input_bindings(db, dataset_version_ids)

    def _input_bindings(self, db, versions, *, study_id=None):
        if not isinstance(versions, list) or not 1 <= len(versions) <= 64 or len(set(versions)) != len(versions):
            raise ValueError("Exact distinct dataset versions are required")
        bindings = {}
        for identity in versions:
            version = self._get(db, "dataset_version", identity)
            dataset = self._get(db, "dataset", version["dataset_id"])
            if study_id is not None and dataset["study_id"] != study_id:
                raise ValueError("Dataset belongs to a different Study")
            for name, member in version["manifest"]["members"].items():
                if name in bindings:
                    raise ValueError("Dataset member names collide across selected versions")
                self._artifact(db, member["artifact_id"])
                bindings[name] = member["artifact_id"]
        return bindings

    def request_run(self, analysis_id, analysis_version_id, dataset_version_ids, *, environment,
                    trigger, actor, expected_revision, idempotency_key):
        idempotency_key = _identity(idempotency_key)
        request = _json({"analysis_id": analysis_id, "analysis_version_id": analysis_version_id,
                         "dataset_version_ids": dataset_version_ids, "environment": environment,
                         "trigger": trigger, "actor": _label(actor)})
        if not isinstance(environment, dict) or not environment or not isinstance(trigger, (str, dict)) or not trigger:
            raise ValueError("Invocation requires an explicit environment and trigger")
        fingerprint = _digest(request)
        with self._db(write=True) as db:
            self._binding(db)
            for item in self._list(db, "run"):
                if item["idempotency_key"] == idempotency_key:
                    if item["request_fingerprint"] != fingerprint:
                        raise ValueError("Conflicting idempotency key payload")
                    return item
            analysis = self._get(db, "analysis", analysis_id)
            _expected(analysis, expected_revision)
            version = self._get(db, "analysis_version", analysis_version_id)
            if version["analysis_id"] != analysis_id:
                raise ValueError("Analysis version belongs to another logical analysis")
            definition = version["definition"]
            if not definition["method_version_ids"] or definition["validation_policy"].get("status") == "unplanned":
                raise ValueError("An unplanned analysis definition cannot be executed")
            if request["environment"] != definition["environment"]:
                raise ValueError("Invocation environment differs from the exact analysis definition")
            bindings = self._input_bindings(db, dataset_version_ids, study_id=analysis["study_id"])
            for step in definition.get("steps", []):
                if "inputs" in step and any(identity not in bindings.values() for identity in step["inputs"].values()):
                    raise ValueError("Execution step inputs are not supplied by selected dataset versions")
            identity = uuid.uuid4().hex
            value = {**self._new(actor=actor), **request, "run_id": identity, "study_id": analysis["study_id"],
                     "idempotency_key": idempotency_key, "request_fingerprint": fingerprint,
                     "correlation_id": uuid.uuid4().hex, "inputs": bindings, "status": "requested",
                     "revision": 1, "ref": _ref("run", identity)}
            self._put(db, "run", identity, value)
            self._event(db, actor, "run_requested", {"run_id": identity, "request_fingerprint": fingerprint})
        return value

    def attach_attempt(self, run_id, *, controller_kind, controller_id, correlation_id,
                       actor, expected_revision, step_key="default"):
        if controller_kind != "method":
            raise ValueError("Unsupported execution controller")
        controller_id, step_key = _identity(controller_id), _identity(step_key)
        with self._db(write=True) as db:
            run = self._get(db, "run", run_id)
            if run["correlation_id"] != correlation_id:
                raise ValueError("Invocation correlation identity mismatch")
            attempts = self._list(db, "attempt")
            for item in attempts:
                if item["controller_kind"] == controller_kind and item["controller_id"] == controller_id:
                    if item["run_id"] != run_id or item["step_key"] != step_key:
                        raise ValueError("Controller execution already belongs to another invocation/step")
                    return item
            _expected(run, expected_revision)
            version = self._get(db, "analysis_version", run["analysis_version_id"])
            allowed = {step["step_key"] for step in version["definition"].get("steps", [])} or {"default"}
            if step_key not in allowed:
                raise ValueError("Attempt step_key is absent from the immutable execution definition")
            if any(item["run_id"] == run_id and item["step_key"] == step_key for item in attempts):
                raise ValueError("The method controller is one-shot for each invocation step; no automatic redispatch")
            identity = uuid.uuid4().hex
            value = {**self._new(actor=actor), "attempt_id": identity, "run_id": run_id, "step_key": step_key,
                     "controller_kind": controller_kind, "controller_id": controller_id, "correlation_id": correlation_id,
                     "status": "reserved", "revision": 1, "ref": _ref("attempt", identity)}
            self._put(db, "attempt", identity, value)
            run.update(status="dispatched", revision=run["revision"] + 1)
            self._put(db, "run", run_id, run, update=True)
            self._event(db, actor, "attempt_attached", {"run_id": run_id, "attempt_id": identity,
                        "controller_kind": controller_kind, "controller_id": controller_id, "step_key": step_key})
        return value

    def _native(self, db, attempt):
        native = MethodService._get(db, "run", attempt["controller_id"])
        service = MethodService(self.root, readonly=True)
        if native["status"] not in {"succeeded", "failed"} or not service.provenance(native["run_id"])["valid"]:
            raise ValueError("Execution has no verified terminal controller evidence")
        run = self._get(db, "run", attempt["run_id"])
        definition = self._get(db, "analysis_version", run["analysis_version_id"])["definition"]
        if run["environment"] != definition["environment"]:
            raise ValueError("Invocation environment differs from the retained analysis definition")
        declared = next((step for step in definition.get("steps", []) if step["step_key"] == attempt["step_key"]), None)
        expected_method = declared["method_version_id"] if declared else definition["method_version_ids"][0]
        parameters = declared["parameters"] if declared else definition["parameters"]
        inputs = declared.get("inputs", run["inputs"]) if declared else run["inputs"]
        if native["method_version_id"] != expected_method or native["parameters"] != parameters or native["inputs"] != inputs:
            raise ValueError("Controller execution differs from the immutable invocation definition")
        expected_correlation = {"project_id": self.project_id, "scientific_run_id": run["run_id"],
                                "attempt_id": attempt["attempt_id"], "analysis_version_id": run["analysis_version_id"],
                                "dataset_version_ids": run["dataset_version_ids"]}
        legacy = (isinstance(run["trigger"], dict) and run["trigger"].get("kind") == "legacy_import"
                  and run["trigger"].get("source_kind") == "method"
                  and run["trigger"].get("source_id") == native["run_id"])
        if native.get("correlation") != expected_correlation and not (legacy and native.get("correlation") is None):
            raise ValueError("Controller correlation differs from the explicit invocation ownership")
        for identity in (*native["inputs"].values(), *native["outputs"].values()):
            self._artifact(db, identity)
        return native

    def finish_attempt(self, attempt_id, *, actor, expected_revision):
        with self._db(write=True) as db:
            value = self._get(db, "attempt", attempt_id)
            _expected(value, expected_revision)
            native = self._native(db, value)
            if value["status"] != "reserved":
                if value["evidence"] != native["evidence"]:
                    raise ValueError("Terminal execution attempt is immutable")
                return value
            value.update(status=native["status"], evidence=native["evidence"], validation=native["validation"],
                         outputs=native["outputs"], started_at=native["started_at"], finished_at=native["finished_at"],
                         error=native.get("error"), revision=value["revision"] + 1,
                         validation_id=self._validation(db, value, native, actor)["validation_id"])
            self._put(db, "attempt", attempt_id, value, update=True)
            run = self._get(db, "run", value["run_id"])
            attempts = [item for item in self._list(db, "attempt") if item["run_id"] == run["run_id"]]
            definition = self._get(db, "analysis_version", run["analysis_version_id"])["definition"]
            planned_count = len(definition.get("steps", [])) or 1
            status = "dispatched"
            if len(attempts) == planned_count and all(item["status"] in {"succeeded", "failed"} for item in attempts):
                status = "succeeded" if all(item["status"] == "succeeded" for item in attempts) else "failed"
            run.update(status=status, revision=run["revision"] + 1)
            self._put(db, "run", run["run_id"], run, update=True)
            self._event(db, actor, "attempt_observed", {"attempt_id": attempt_id, "evidence": native["evidence"]})
        return value

    def _validation(self, db, attempt, native, actor):
        validation_id = _digest({"controller_id": native["run_id"], "evidence": native["evidence"]})
        if db.execute("SELECT 1 FROM records WHERE kind='scientific_validation' AND id=?", (validation_id,)).fetchone():
            return self._get(db, "validation", validation_id)
        run = self._get(db, "run", attempt["run_id"])
        method = MethodService._get(db, "method", native["method_version_id"])
        validation = {**self._new(actor=actor), "attempt_id": attempt["attempt_id"], "run_id": run["run_id"],
                      "method_version_id": native["method_version_id"], "inputs": native["inputs"],
                      "dataset_version_ids": run["dataset_version_ids"], "environment": method["contract"]["environment"],
                      "checks": method["contract"]["validation"], "outputs": native["outputs"],
                      "evidence": native["evidence"], "executor": native.get("execution"), "reviewer": None,
                      "observations": native["validation"], "error": native.get("error"),
                      "status": "passed" if native["status"] == "succeeded" and native["validation"] and native["validation"]["passed"] else "failed",
                      "validation_id": validation_id, "ref": _ref("validation", validation_id)}
        self._put(db, "validation", validation_id, validation)
        return validation

    def publish_result(self, attempt_id, *, interpretation, actor, expected_revision,
                       result_id=None, expected_result_revision=None):
        with self._db(write=True) as db:
            attempt = self._get(db, "attempt", attempt_id)
            _expected(attempt, expected_revision)
            if attempt["status"] not in {"succeeded", "failed"}:
                raise ValueError("Result publication requires a finished execution attempt")
            native = self._native(db, attempt)
            run = self._get(db, "run", attempt["run_id"])
            validation_id = self._validation(db, attempt, native, actor)["validation_id"]
            if result_id is None:
                # Repeated publication of the same receipt/interpretation is idempotent.
                for existing in self._list(db, "result_version"):
                    if existing["attempt_id"] == attempt_id and existing["interpretation"] == _reason(interpretation):
                        logical = self._get(db, "result", existing["result_id"])
                        if logical["current_version_id"] != existing["version_id"]:
                            reviews = [item for item in self._list(db, "review") if item["result_version_id"] == existing["version_id"]]
                            historical = {**logical, "current_ref": logical["ref"], "ref": existing["ref"],
                                          "version_id": existing["version_id"], "is_current": False,
                                          "review_status": reviews[-1]["decision"] if reviews else "candidate"}
                            historical.pop("review_id", None)
                            if reviews:
                                historical["review_id"] = reviews[-1]["review_id"]
                            return historical
                        return logical
                result_id = uuid.uuid4().hex
                result = {**self._new(actor=actor), "result_id": result_id, "study_id": run["study_id"],
                          "analysis_id": run["analysis_id"], "revision": 1, "review_status": "candidate"}
                update = False
            else:
                result = self._get(db, "result", result_id)
                _expected(result, expected_result_revision)
                if result["study_id"] != run["study_id"] or result["analysis_id"] != run["analysis_id"]:
                    raise ValueError("Result belongs to another analysis or Study")
                result.update(revision=result["revision"] + 1, review_status="candidate")
                result.pop("review_id", None)
                update = True
            content = {"result_id": result_id, "interpretation": _reason(interpretation), "run_id": run["run_id"],
                       "attempt_id": attempt_id, "analysis_version_id": run["analysis_version_id"],
                       "artifact_ids": list(native["outputs"].values()), "validation_id": validation_id}
            version_id = _digest(content)
            version = {**self._new(actor=actor), **content, "version_id": version_id, "ref": _ref("result", result_id, version_id)}
            if not db.execute("SELECT 1 FROM records WHERE kind='scientific_result_version' AND id=?", (version_id,)).fetchone():
                self._put(db, "result_version", version_id, version)
            result.update(current_version_id=version_id, ref=version["ref"])
            self._put(db, "result", result_id, result, update=update)
            self._event(db, actor, "result_published", {"result_id": result_id, "version_id": version_id, "validation_id": validation_id})
        return result

    def review_result(self, result_id, result_version_id, decision, *, actor, rationale, expected_revision, db=None):
        if decision not in {"accepted", "rejected"}:
            raise ValueError("A result review decision must be accepted or rejected")
        with self._db(write=True, connection=db) as transaction:
            result = self._get(transaction, "result", result_id)
            _expected(result, expected_revision)
            version = self._get(transaction, "result_version", result_version_id)
            if version["result_id"] != result_id or result["current_version_id"] != result_version_id:
                raise ValueError("Result proposal version is stale or belongs to another result")
            if decision == "accepted":
                validation = self._get(transaction, "validation", version["validation_id"])
                if validation["status"] != "passed":
                    raise ValueError("Failed validation cannot become an accepted result")
                native = self._native(transaction, self._get(transaction, "attempt", version["attempt_id"]))
                if native["status"] != "succeeded" or not native.get("validation", {}).get("passed"):
                    raise ValueError("Failed native execution or validation cannot be accepted")
            identity = uuid.uuid4().hex
            review = {**self._new(actor=actor), "review_id": identity, "result_id": result_id,
                      "result_version_id": result_version_id, "decision": decision, "rationale": _reason(rationale),
                      "base_revision": result["revision"], "ref": _ref("review", identity)}
            self._put(transaction, "review", identity, review)
            result.update(revision=result["revision"] + 1, review_status=decision, review_id=identity)
            self._put(transaction, "result", result_id, result, update=True)
            self._event(transaction, actor, "result_reviewed", {"result_id": result_id, "review_id": identity})
        return result

    def select_result(self, study_id, role, result_version_id, *, actor, rationale, expected_revision, db=None):
        role = _label(role)
        identity = _digest({"study_id": study_id, "role": role})
        with self._db(write=True, connection=db) as transaction:
            self._get(transaction, "study", study_id)
            version = self._get(transaction, "result_version", result_version_id)
            result = self._get(transaction, "result", version["result_id"])
            if result["study_id"] != study_id:
                raise ValueError("Result selection belongs to a different Study")
            reviews = [item for item in self._list(transaction, "review") if item["result_version_id"] == result_version_id]
            if not reviews or reviews[-1]["decision"] != "accepted":
                raise ValueError("Only an explicitly accepted result version can be selected")
            native = self._native(transaction, self._get(transaction, "attempt", version["attempt_id"]))
            if native["status"] != "succeeded" or not native.get("validation", {}).get("passed"):
                raise ValueError("Failed native execution or validation cannot be selected")
            exists = transaction.execute("SELECT 1 FROM records WHERE kind='scientific_selection' AND id=?", (identity,)).fetchone()
            old = self._get(transaction, "selection", identity) if exists else {"revision": 0, "result_version_id": None}
            _expected(old, expected_revision)
            value = {**self._new(actor=actor), "selection_id": identity, "study_id": study_id, "role": role,
                     "result_version_id": result_version_id, "review_id": reviews[-1]["review_id"],
                     "revision": old["revision"] + 1, "rationale": _reason(rationale), "ref": _ref("selection", identity)}
            event_id = uuid.uuid4().hex
            event = {**value, "selection_event_id": event_id, "superseded_result_version_id": old["result_version_id"]}
            self._put(transaction, "selection_event", event_id, event)
            self._put(transaction, "selection", identity, value, update=bool(exists))
            self._event(transaction, actor, "result_selected", {"selection_id": identity, "selection_event_id": event_id})
        return value
