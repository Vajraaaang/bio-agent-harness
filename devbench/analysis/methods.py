"""Contract-first methods and explicit scientific acceptance for local research.

This service is a single-user coordinator, not an authentication service. Actor
labels are assertions. As with ExecutionDAG, externally retained roots are needed
to detect a workspace owner replacing an entire history.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from devharness_core.methods import MethodContract
from devharness_core.provenance import (ExecutionDAG, canonical_json, copy_artifact,
                                       hash_artifact, verify_execution)
from .method_executor import LocalDockerExecutor, _path
from .method_validation import MAX_DATASET_BYTES, validate_outputs


_INVESTIGATION_ARTIFACT_LIMITS = {
    "rna_investigation_packet": 48_000,
    "rna_investigation_result": 24_000,
    "rna_investigation_capture": 64_000,
    "rna_investigation_usage": 24_000,
    "rna_investigation_reconciliation": 48_000,
    "rna_mediated_capture": 1_000_000,
}


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("A bounded nonempty actor/name label is required")
    return value.strip()


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError("A nonempty scientific review rationale is required")
    return value.strip()


class MethodService:
    """Both registered and custom methods use these Step, Run and Proposal APIs."""

    def __init__(self, workspace, *, readonly=False, initialize=None, reconcile=None):
        if type(readonly) is not bool:
            raise ValueError("readonly must be a boolean")
        initialize = not readonly if initialize is None else initialize
        reconcile = not readonly if reconcile is None else reconcile
        if type(initialize) is not bool or type(reconcile) is not bool or (readonly and (initialize or reconcile)):
            raise ValueError("Read-only access cannot initialize or reconcile workers")
        self.readonly = readonly
        self._remote_authorities = {}  # Trusted live objects are never reconstructed from JSON.
        self.root = _path(workspace)
        self.database = _path(self.root / "methods.sqlite3")
        exists = self.database.is_file()
        if exists:
            # Validate before any schema write, even when the caller is a writer.
            with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True) as db:
                if db.execute("SELECT version FROM schema_version").fetchall() not in ([(1,)], [(2,)]):
                    raise ValueError("Unsupported method workspace schema")
        elif not initialize:
            raise ValueError("Method workspace is unavailable; explicit initialization is required")
        if initialize:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in ("objects", "runs"):
                _path(self.root / name).mkdir(mode=0o700, exist_ok=True)
        if not exists:
            with self._db() as db:
                db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(kind, id));
                CREATE TABLE IF NOT EXISTS heads (analysis_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
                INSERT OR IGNORE INTO schema_version VALUES (1);
                """)
        if reconcile:
            self.reconcile()

    def _require_writable(self):
        if self.readonly:
            raise PermissionError("Method workspace is read-only")

    @contextmanager
    def _db(self):
        _path(self.database)
        db = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=30) if self.readonly else sqlite3.connect(self.database, timeout=30)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            if self.readonly:
                db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN" if self.readonly else "BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, kind, identity):
        row = db.execute("SELECT payload,sha256 FROM records WHERE kind=? AND id=?", (kind, identity)).fetchone()
        if row is None:
            raise ValueError("Unknown " + kind + " identity")
        value = json.loads(row[0])
        if _digest(value) != row[1]:
            raise ValueError("Method workspace record digest mismatch")
        return value

    @staticmethod
    def _put(db, kind, identity, value, *, update=False):
        encoded = canonical_json(value).decode()
        if update:
            cursor = db.execute("UPDATE records SET payload=?,sha256=? WHERE kind=? AND id=?",
                                (encoded, _digest(value), kind, identity))
            if cursor.rowcount != 1:
                raise ValueError("Missing record to update")
        else:
            db.execute("INSERT INTO records VALUES(?,?,?,?)", (kind, identity, encoded, _digest(value)))

    @staticmethod
    def _audit(db, actor, action, payload):
        previous = db.execute("SELECT sequence,sha256 FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        sequence = previous[0] + 1 if previous else 1
        event = {"sequence": sequence, "previous": previous[1] if previous else None,
                 "actor": _label(actor), "identity_assurance": "caller-asserted", "action": action,
                 "at": _now(), "payload": payload}
        db.execute("INSERT INTO audit VALUES(?,?,?)", (sequence, canonical_json(event).decode(), _digest(event)))

    def get(self, kind, identity):
        with self._db() as db:
            return self._get(db, kind, identity)

    def list_records(self, kind):
        with self._db() as db:
            return [self._get(db, kind, row[0]) for row in db.execute(
                "SELECT id FROM records WHERE kind=? ORDER BY id", (kind,)).fetchall()]

    def import_artifact(self, source, *, artifact_type, actor):
        return self._import_artifact(source, artifact_type=artifact_type, actor=actor)

    def _import_artifact(self, source, *, artifact_type, actor, generated_by=None, _connection=None):
        self._require_writable()
        source = _path(source)
        if not source.is_file() or artifact_type not in {"file", "json", "table", "anndata", *_INVESTIGATION_ARTIFACT_LIMITS}:
            raise ValueError("A supported single regular-file artifact is required")
        temporary = self.root / "objects" / ("pending-" + uuid.uuid4().hex)
        try:
            maximum = _INVESTIGATION_ARTIFACT_LIMITS.get(artifact_type, MAX_DATASET_BYTES)
            snapshot = copy_artifact(source, temporary, max_bytes=maximum, max_entries=1)
            if artifact_type in _INVESTIGATION_ARTIFACT_LIMITS:
                # These are bounded reasoning/evidence records, not matrices or
                # executable inputs. Validate copied bytes before retaining them.
                from .method_planner_contract import _copy_json
                from .method_validation import finite_json
                try:
                    document = finite_json(temporary)
                    # Wire captures contain independently bounded request and
                    # base64 response strings, larger than planner prose limits.
                    # The single-file byte cap above still applies to all bytes.
                    if artifact_type != "rna_mediated_capture":
                        document = _copy_json(document)
                except (TypeError, ValueError, UnicodeError, RecursionError) as error:
                    raise ValueError("Investigation artifacts require finite bounded JSON") from error
                if not isinstance(document, dict):
                    raise ValueError("Investigation artifacts require a JSON object")
            target = self.root / "objects" / snapshot["sha256"]
            _path(target)
            temporary.chmod(0o444)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if hash_artifact(target, max_bytes=MAX_DATASET_BYTES)["sha256"] != snapshot["sha256"]:
                    raise ValueError("Retained artifact hash mismatch")
            suffix = source.suffix
            if suffix and re.fullmatch(r"\.[A-Za-z0-9_-]{1,20}", suffix) is None:
                raise ValueError("Unsupported artifact filename suffix")
            value = {"sha256": snapshot["sha256"], "size_bytes": snapshot["size_bytes"],
                     "artifact_type": artifact_type, "suffix": suffix, "generated_by": generated_by}
            value["artifact_id"] = _digest(value)
            with (self._db() if _connection is None else nullcontext(_connection)) as db:
                if db.execute("SELECT 1 FROM records WHERE kind='artifact' AND id=?", (value["artifact_id"],)).fetchone() is None:
                    self._put(db, "artifact", value["artifact_id"], value)
                    self._audit(db, actor, "artifact_imported", value)
            return value
        finally:
            temporary.unlink(missing_ok=True)

    def artifact_path(self, artifact_id):
        value = self.get("artifact", artifact_id)
        expected = {key: value[key] for key in ("sha256", "size_bytes", "artifact_type", "suffix", "generated_by")}
        if _digest(expected) != artifact_id:
            raise ValueError("Artifact manifest digest mismatch")
        path = _path(self.root / "objects" / value["sha256"])
        actual = hash_artifact(path, max_bytes=MAX_DATASET_BYTES, max_entries=1)
        if actual["sha256"] != value["sha256"] or actual["size_bytes"] != value["size_bytes"]:
            raise ValueError("Retained artifact hash mismatch")
        return path

    def register_method(self, value, *, source, environment_lock=None, actor):
        contract = MethodContract.from_dict(value)
        value = contract.to_dict()
        if value["implementation"]["kind"] == "dispatcher":
            from devharness_server.method_adapter import dispatcher_contract
            if dispatcher_contract(value["implementation"]["tool_name"]).version_id != contract.version_id:
                raise ValueError("Registered method does not match the live trusted adapter")
        elif value["implementation"]["kind"] == "trusted_fastq":
            from .fastq_method_adapter import fastq_execution_contract
            if environment_lock is not None:
                raise ValueError("Trusted FASTQ runtime cannot accept a caller-supplied environment")
            if fastq_execution_contract(value["implementation"]["execution_binding"]).version_id != contract.version_id:
                raise ValueError("Registered FASTQ method does not match its exact live trusted adapter")
        elif value["lifecycle"] != "custom" or value["implementation_trust"] != "unreviewed":
            raise ValueError("Custom methods cannot grant themselves registered implementation trust")
        if hash_artifact(_path(source), max_bytes=1_000_000)["sha256"] != value["implementation"]["source_sha256"]:
            raise ValueError("Method source digest mismatch")
        if value["implementation"]["kind"] in {"oci", "colab"}:
            if environment_lock is None or hash_artifact(_path(environment_lock), max_bytes=1_000_000)["sha256"] != value["environment"]["lock_sha256"]:
                raise ValueError("Environment lock digest mismatch")
        source_ref = self.import_artifact(source, artifact_type="file", actor=actor)
        lock_ref = self.import_artifact(environment_lock, artifact_type="file", actor=actor) if environment_lock else None
        if source_ref["sha256"] != value["implementation"]["source_sha256"] or (
                lock_ref and value["implementation"]["kind"] in {"oci", "colab"} and lock_ref["sha256"] != value["environment"]["lock_sha256"]):
            raise ValueError("Method source or environment changed during import")
        record = {"version_id": contract.version_id, "contract": value, "source_artifact": source_ref["artifact_id"],
                  "environment_artifact": lock_ref["artifact_id"] if lock_ref else None}
        with self._db() as db:
            existing = db.execute("SELECT 1 FROM records WHERE kind='method' AND id=?", (contract.version_id,)).fetchone()
            if existing:
                if self._get(db, "method", contract.version_id) != record:
                    raise ValueError("Method version is immutable")
            else:
                self._put(db, "method", contract.version_id, record)
                self._audit(db, actor, "method_created", record)
        return record

    def register_dispatcher(self, tool_name, *, actor):
        from devharness_server.method_adapter import dispatcher_contract, dispatcher_source
        return self.register_method(dispatcher_contract(tool_name).to_dict(), source=dispatcher_source(tool_name), actor=actor)

    def register_fastq(self, manifest, *, actor):
        from .fastq_method_adapter import fastq_contract, fastq_source
        return self.register_method(fastq_contract(manifest).to_dict(), source=fastq_source(), actor=actor)

    def method(self, version_id):
        record = self.get("method", version_id)
        if MethodContract.from_dict(record["contract"]).version_id != version_id:
            raise ValueError("Method version digest mismatch")
        return record

    def create_analysis(self, name, *, actor):
        analysis_id = uuid.uuid4().hex
        revision = {"analysis_id": analysis_id, "parent_revision": None, "steps": [], "name": _label(name)}
        revision["revision_id"] = _digest(revision)
        with self._db() as db:
            self._put(db, "revision", revision["revision_id"], revision)
            db.execute("INSERT INTO heads VALUES(?,?)", (analysis_id, revision["revision_id"]))
            self._audit(db, actor, "analysis_created", revision)
        return revision

    def analysis(self, analysis_id):
        with self._db() as db:
            row = db.execute("SELECT revision_id FROM heads WHERE analysis_id=?", (analysis_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown analysis")
            return self._get(db, "revision", row[0])

    def create_branch(self, analysis_id, revision_id, *, actor):
        with self._db() as db:
            revision = self._get(db, "revision", revision_id)
            if revision["analysis_id"] != analysis_id:
                raise ValueError("Revision belongs to a different analysis")
            value = {"branch_id": uuid.uuid4().hex, "analysis_id": analysis_id, "base_revision": revision_id,
                     "created_by": _label(actor)}
            self._put(db, "branch", value["branch_id"], value)
            self._audit(db, actor, "branch_created", value)
        return value

    def add_step(self, branch_id, version_id, inputs, parameters, *, actor):
        method = self.method(version_id)
        contract = MethodContract.from_dict(method["contract"])
        parameters = contract.validate_parameters(parameters)
        declarations = method["contract"]["inputs"]
        if not isinstance(inputs, dict) or set(inputs) - set(declarations) or any(
                spec["required"] and key not in inputs for key, spec in declarations.items()):
            raise ValueError("Input bindings do not match the method contract")
        for name, identity in inputs.items():
            self.artifact_path(identity)
            if self.get("artifact", identity)["artifact_type"] != declarations[name]["artifact_type"]:
                raise ValueError("Input artifact type does not match the method contract")
        with self._db() as db:
            branch = self._get(db, "branch", branch_id)
            approved = {item["run_id"] for item in self._get(db, "revision", branch["base_revision"])["steps"]}
            for identity in inputs.values():
                origin = self._get(db, "artifact", identity)["generated_by"]
                if origin and origin not in approved:
                    upstream = self._get(db, "run", origin)
                    if upstream["branch_id"] != branch_id or upstream["status"] != "succeeded":
                        raise ValueError("Unapproved upstream results must belong to this branch")
            step = {"step_id": uuid.uuid4().hex, "branch_id": branch_id, "method_version_id": version_id,
                    "inputs": inputs, "parameters": parameters, "created_by": _label(actor)}
            self._put(db, "step", step["step_id"], step)
            self._audit(db, actor, "step_created", step)
        return step

    def run_step(self, step_id, *, actor, executor=None, reproduces=None, run_id=None, correlation=None,
                 data_execution_authorized=False, cancellation_event=None):
        self._require_writable()
        if run_id is not None and (not isinstance(run_id, str) or re.fullmatch(r"[0-9a-f]{32}", run_id) is None):
            raise ValueError("Use a preallocated controller run identity")
        if correlation is not None:
            fields = {"project_id", "scientific_run_id", "attempt_id", "analysis_version_id", "dataset_version_ids"}
            if not isinstance(correlation, dict) or set(correlation) != fields:
                raise ValueError("Use exact scientific correlation fields")
            for key in fields - {"dataset_version_ids"}:
                if not isinstance(correlation[key], str) or re.fullmatch(r"[0-9a-f]{32,64}", correlation[key]) is None:
                    raise ValueError("Invalid scientific correlation identity")
            versions = correlation["dataset_version_ids"]
            if not isinstance(versions, list) or not 1 <= len(versions) <= 32 or any(
                    not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in versions):
                raise ValueError("Invalid scientific dataset versions")
            correlation = json.loads(canonical_json(correlation))
        if reproduces is not None:
            original = self.get("run", reproduces)
            if original["step_id"] != step_id or original["status"] != "succeeded" or not self.provenance(reproduces)["valid"]:
                raise ValueError("Reproduction must bind a verified successful execution of this step")
        step = self.get("step", step_id)
        method = self.method(step["method_version_id"])
        contract = MethodContract.from_dict(method["contract"])
        if method["contract"]["implementation"]["kind"] == "trusted_fastq":
            import threading
            if data_execution_authorized is not True:
                raise PermissionError("Trusted FASTQ requires fresh explicit data execution authority")
            if executor is not None:
                raise ValueError("Trusted FASTQ cannot accept an injected executor")
            if cancellation_event is not None and not isinstance(cancellation_event, threading.Event):
                raise TypeError("Trusted FASTQ cancellation requires a coordinator threading.Event")
            if cancellation_event is not None and cancellation_event.is_set():
                raise PermissionError("Cancelled FASTQ execution cannot dispatch")
        if method["contract"]["implementation"]["kind"] == "colab":
            raise PermissionError("Colab methods require the approved remote job interface; local execution is forbidden")
        parameters = contract.validate_parameters(step["parameters"])
        inputs = {name: self.artifact_path(identity) for name, identity in step["inputs"].items()}
        source = self.artifact_path(method["source_artifact"])
        lock = self.artifact_path(method["environment_artifact"]) if method["environment_artifact"] else None
        run_id = run_id or uuid.uuid4().hex
        directory = self.root / "runs" / run_id
        directory.mkdir(mode=0o700)
        output_dir = directory / "outputs"
        output_dir.mkdir()
        record = {"run_id": run_id, "step_id": step_id, "branch_id": step["branch_id"],
                  "method_version_id": method["version_id"], "inputs": step["inputs"], "parameters": parameters,
                  "status": "running", "outputs": {}, "validation": None, "reproduces": reproduces,
                  "started_at": _now(), "actor": _label(actor),
                  "coordinator_sha256": _digest({name: hash_artifact(Path(__file__).resolve().parent / name)["sha256"]
                      for name in ("methods.py", "method_executor.py", "method_validation.py")}),
                  "backend": ("trusted_fastq" if method["contract"]["implementation"]["kind"] == "trusted_fastq" else
                              "local_docker" if executor is None and method["contract"]["implementation"]["kind"] == "oci" else "trusted_coordinator")}
        if correlation is not None:
            record["correlation"] = correlation
        with (directory / "lease").open("xb") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            with self._db() as db:
                if method["contract"]["implementation"]["kind"] == "trusted_fastq":
                    # The write transaction linearizes competing dispatches.
                    for row in db.execute("SELECT id FROM records WHERE kind='run'").fetchall():
                        previous = self._get(db, "run", row[0])
                        if previous["step_id"] == step_id and (previous["status"] in {"running", "interrupted"}
                                                               or previous.get("cleanup_required") is True):
                            raise PermissionError("An active or uncertain FASTQ attempt requires reconciliation; no redispatch")
                self._put(db, "run", run_id, record)
                self._audit(db, actor, "run_started", record)
            dag = ExecutionDAG(directory / "evidence", actor_id=actor)
            finalized=False
            backend=None
            try:
                retained_inputs = {name: dag.add_artifact(path, max_bytes=MAX_DATASET_BYTES, max_entries=1)
                                   for name, path in inputs.items()}
                dag.append("method_started", {"run": record, "method": method,
                    "source": dag.add_artifact(source, max_bytes=1_000_000),
                    "environment_lock": dag.add_artifact(lock, max_bytes=1_000_000) if lock else None,
                    "inputs": retained_inputs})
                bindings_dir = directory / "input-bindings"
                bindings_dir.mkdir()
                snapshots = {}
                for name, item in retained_inputs.items():
                    suffix = self.get("artifact", step["inputs"][name])["suffix"]
                    destination = bindings_dir / (name + suffix)
                    copy_artifact(dag.root_dir / "objects" / item["sha256"], destination,
                                  max_bytes=MAX_DATASET_BYTES, max_entries=1)
                    destination.chmod(0o444)
                    snapshots[name] = destination
                if method["contract"]["implementation"]["kind"] == "dispatcher":
                    if executor is not None:
                        raise ValueError("Registered methods must use their trusted dispatcher adapter")
                    from devharness_server.method_adapter import execute_dispatcher
                    result = execute_dispatcher(contract, snapshots, parameters, output_dir)
                elif method["contract"]["implementation"]["kind"] == "trusted_fastq":
                    from .fastq_method_adapter import execute_fastq
                    def observe_fastq(event):
                        # These are facts from the closed backend, not caller code
                        # or a second controller. The existing run lease is held.
                        encoded = canonical_json(event)
                        if len(encoded) > 32_000:
                            raise ValueError("FASTQ runtime event exceeds its receipt bound")
                        observed = json.loads(encoded)
                        record.setdefault("runtime_events", []).append(observed)
                        if len(record["runtime_events"]) > 64:
                            raise ValueError("FASTQ runtime emitted too many lifecycle events")
                        with self._db() as db:
                            self._put(db, "run", run_id, record, update=True)
                            self._audit(db, actor, "fastq_runtime_observed", {"run_id": run_id, "event": observed})
                        return cancellation_event is None or not cancellation_event.is_set()
                    result = execute_fastq(contract, snapshots, parameters, output_dir,
                                           execution_authorized=data_execution_authorized,
                                           cancellation_event=cancellation_event, execution_observer=observe_fastq)
                else:
                    backend = executor or LocalDockerExecutor()
                    result = backend.execute(method["contract"], snapshots, parameters,
                        dag.root_dir / "objects" / method["contract"]["implementation"]["source_sha256"],
                        dag.root_dir / "objects" / method["contract"]["environment"]["lock_sha256"], output_dir)
                canonical_json(result)
                record["execution"] = result
                if method["contract"]["implementation"]["kind"] == "trusted_fastq":
                    record["cleanup_required"] = result.get("cleanup_required") is True
                    record["runtime_uncertain"] = result.get("runtime_uncertain") is True
                if result.get("exit_code") != 0:
                    raise ValueError("Method execution failed: " + str(result.get("error", result.get("stderr", "nonzero exit")))[:2000])
                outputs, total = {}, 0
                for path in output_dir.iterdir():
                    _path(path)
                    if not path.is_file():
                        raise ValueError("Method outputs must be regular files")
                    total += hash_artifact(path, max_bytes=method["contract"]["execution"]["max_output_bytes"])["size_bytes"]
                if total > method["contract"]["execution"]["max_output_bytes"]:
                    raise ValueError("Method output byte budget exceeded")
                declared = {Path(spec["path"]).name for spec in method["contract"]["outputs"].values()}
                if {path.name for path in output_dir.iterdir()} - declared:
                    raise ValueError("Method produced undeclared outputs")
                for name, spec in method["contract"]["outputs"].items():
                    path = output_dir / Path(spec["path"]).name
                    if path.exists():
                        outputs[name] = path
                    elif spec["required"]:
                        raise ValueError("Required method output is missing: " + name)
                if not outputs:
                    raise ValueError("Method emitted no output artifacts")
                if method["contract"]["validation"]["comparator"] == "single_cell_qc":
                    from devharness_core.watchdog import run_sandboxed
                    validation = run_sandboxed(validate_outputs, (method["contract"], snapshots, outputs),
                                               max_mem_mb=1024, timeout_sec=60)
                else:
                    validation = validate_outputs(method["contract"], snapshots, outputs)
                validation["parameters"] = parameters
                record["validation"] = validation
                if not validation["passed"]:
                    raise ValueError("Required scientific invariants failed")
                # Recheck all frozen inputs and evidence before publishing any output references.
                for name, path in snapshots.items():
                    if hash_artifact(path, max_bytes=MAX_DATASET_BYTES)["sha256"] != retained_inputs[name]["sha256"]:
                        raise ValueError("Method mutated an input artifact")
                guard=getattr(backend, "publication_context", None)
                with (guard(record) if callable(guard) else nullcontext(None)) as publication_db:
                    if (method["contract"]["implementation"]["kind"] == "trusted_fastq"
                            and cancellation_event is not None and cancellation_event.is_set()):
                        raise ValueError("Cancelled FASTQ execution cannot publish outputs")
                    published = {}
                    for name, path in outputs.items():
                        dag.add_artifact(path, max_bytes=MAX_DATASET_BYTES, max_entries=1)
                        published[name] = self._import_artifact(path,
                            artifact_type=method["contract"]["outputs"][name]["artifact_type"],
                            actor=actor, generated_by=run_id, **({"_connection":publication_db} if publication_db is not None else {}))["artifact_id"]
                    record.update(status="succeeded", outputs=published)
                    if reproduces is not None:
                        actual_hashes = {name: (self._get(publication_db,"artifact",identity) if publication_db is not None else self.get("artifact",identity))["sha256"] for name, identity in published.items()}
                        expected_hashes = {name: self.get("artifact", identity)["sha256"] for name, identity in original["outputs"].items()}
                        record["reproduction"] = {"outputs_identical": actual_hashes == expected_hashes,
                                                   "output_sha256": actual_hashes, "original_output_sha256": expected_hashes}
                    if publication_db is not None:
                        record["finished_at"] = _now()
                        dag.append("method_finished", record)
                        record["evidence"] = dag.finalize(analysis_fingerprint=_digest({"method": method, "inputs": step["inputs"],
                                                                                     "parameters": parameters}))
                        self._put(publication_db, "run", run_id, record, update=True)
                        self._audit(publication_db, actor, "run_finished", record)
                        finalized=True
            except Exception as error:
                if hasattr(error, "execution"):
                    record["execution"] = error.execution
                record.update(status="failed", outputs={}, error=f"{type(error).__name__}: {error}"[:3000])
                if (method["contract"]["implementation"]["kind"] == "trusted_fastq"
                        and "execution" not in record and any(event.get("event") in {"before_engine_start", "engine_started"}
                            for event in record.get("runtime_events", []))):
                    record.update(cleanup_required=True, runtime_uncertain=True)
            if not finalized:
                record["finished_at"] = _now()
                dag.append("method_finished", record)
                record["evidence"] = dag.finalize(analysis_fingerprint=_digest({"method": method, "inputs": step["inputs"],
                                                                                "parameters": parameters}))
                with self._db() as db:
                    self._put(db, "run", run_id, record, update=True)
                    self._audit(db, actor, "run_finished", record)
        return record

    def prepare_remote_step(self, step_id, *, run_id, job_id, source_bundle_sha256,
                            validator_sha256, scope, limits, actor, correlation=None):
        """Freeze metadata only; no execution, grant consumption, or numerical read."""
        from .colab_execution_contract import JOB_FORMAT, POLICY, validate_job
        self._require_writable()
        step = self.get('step', step_id)
        method = self.method(step['method_version_id'])
        contract = MethodContract.from_dict(method['contract'])
        def ref(artifact_id, *, input_ref=False):
            self.artifact_path(artifact_id)
            artifact = self.get('artifact', artifact_id)
            return {key: artifact[key] for key in (('sha256','size_bytes','artifact_type') if input_ref else ('sha256','size_bytes'))}
        return validate_job({'format': JOB_FORMAT, 'job_id': job_id, 'run_id': run_id,
            'step_id': step_id, 'method_version_id': method['version_id'],
            'method_contract': contract.to_dict(), 'source': ref(method['source_artifact']),
            'environment_lock': ref(method['environment_artifact']),
            'inputs': {name: ref(identity, input_ref=True) for name, identity in step['inputs'].items()},
            'parameters': contract.validate_parameters(step['parameters']),
            'source_bundle_sha256': source_bundle_sha256, 'validator_sha256': validator_sha256,
            'scope': scope, 'limits': limits, 'actor': _label(actor), 'correlation': correlation,
            'policy': dict(POLICY)})

    def reserve_remote_step(self, job, *, request, grant, authority, actor):
        """Consume the one-job approval before creating any transport capability.

        A failure after the external authority commits stays charged. There is no
        refund, automatic dispatch, fallback or replay operation here.
        """
        from .colab_execution_contract import validate_job, job_sha256
        from .rna_trial_authority import AuthenticatedTrialAuthority
        self._require_writable()
        job = validate_job(job)
        if type(authority) is not AuthenticatedTrialAuthority:
            raise PermissionError('Remote reservation requires the exact execution grant authority')
        fresh = self.prepare_remote_step(job['step_id'], run_id=job['run_id'], job_id=job['job_id'],
            source_bundle_sha256=job['source_bundle_sha256'], validator_sha256=job['validator_sha256'],
            scope=job['scope'], limits=job['limits'], actor=job['actor'], correlation=job['correlation'])
        if fresh != job:
            raise ValueError('Remote step changed before reservation')
        step = self.get('step', job['step_id'])
        method = self.method(step['method_version_id'])
        directory = self.root / 'runs' / job['run_id']
        # The database transaction serializes duplicate job/run identities. The
        # authority separately guarantees single-use grant reservation.
        with (self.root / 'remote-reserve-lease').open('ab') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self._db() as db:
                if db.execute("SELECT 1 FROM records WHERE (kind='remote_job' AND id=?) OR (kind='run' AND id=?)",
                              (job['job_id'], job['run_id'])).fetchone():
                    raise PermissionError('Remote job or run identity already reserved')
            approval = authority.export_colab_public_approval(request, grant, job=job)
            reservation = authority.consume_and_reserve_colab_qualification(request, grant,
                dispatch_id=job['job_id'], allocation=job['limits'], job=job)
            canonical_json(reservation)
            directory.mkdir(mode=0o700)
            (directory / 'outputs').mkdir(mode=0o700)
            (directory / 'job.json').write_bytes(canonical_json(job))
            (directory / 'job.json').chmod(0o444)
            record = {'run_id': job['run_id'], 'step_id': job['step_id'], 'branch_id': step['branch_id'],
                'method_version_id': job['method_version_id'], 'inputs': step['inputs'],
                'parameters': job['parameters'], 'status': 'remote_pending', 'backend': 'google_colab',
                'outputs': {}, 'validation': None, 'reproduces': None, 'started_at': _now(),
                'actor': _label(actor), 'job_id': job['job_id'], 'job_sha256': job_sha256(job),
                'reservation': reservation, 'remote_approval': approval}
            if job['correlation'] is not None:
                record['correlation'] = job['correlation']
            dag = ExecutionDAG(directory / 'reservation-evidence', actor_id=actor)
            inputs = {name: dag.add_artifact(self.artifact_path(identity), max_bytes=MAX_DATASET_BYTES, max_entries=1)
                      for name, identity in step['inputs'].items()}
            dag.append('remote_method_reserved', {'run': record, 'job': job, 'method': method,
                'source': dag.add_artifact(self.artifact_path(method['source_artifact']), max_bytes=1_000_000),
                'environment_lock': dag.add_artifact(self.artifact_path(method['environment_artifact']), max_bytes=1_000_000),
                'inputs': inputs})
            record['reservation_evidence'] = dag.finalize(analysis_fingerprint=record['job_sha256'])
            with self._db() as db:
                self._put(db, 'remote_job', job['job_id'], job)
                self._put(db, 'run', job['run_id'], record)
                self._audit(db, actor, 'remote_method_reserved', record)
        self._remote_authorities[job['job_id']] = authority
        return record

    def _remote_job(self, job_id):
        from .colab_execution_contract import validate_job, job_sha256
        job = validate_job(self.get('remote_job', job_id))
        record = self.get('run', job['run_id'])
        if record.get('job_id') != job_id or record.get('job_sha256') != job_sha256(job) or record.get('backend') != 'google_colab':
            raise ValueError('Remote run binding changed')
        path = self.root / 'runs' / job['run_id'] / 'job.json'
        if hash_artifact(_path(path), max_bytes=1_000_000)['sha256'] != job_sha256(job):
            raise ValueError('Retained remote job changed')
        return job, record

    def export_remote_job(self, job_id, destination, *, actor):
        self._require_writable()
        job, _ = self._remote_job(job_id)
        with (self.root / 'runs' / job['run_id'] / 'remote-operation-lease').open('ab') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PermissionError('Another remote operation owns this run') from error
            return self._export_remote_job_once(job_id, destination, actor=actor)

    def _export_remote_job_once(self, job_id, destination, *, actor):
        """Package immutable artifacts using the shared notebook/bundle builder."""
        from .colab_job import export_method_job
        self._require_writable()
        job, record = self._remote_job(job_id)
        if record['status'] != 'remote_pending' or record.get('export') is not None:
            raise PermissionError('A reserved remote job can be exported once only')
        step = self.get('step', job['step_id'])
        method = self.method(job['method_version_id'])
        receipt = export_method_job(job, destination=_path(destination),
            source=self.artifact_path(method['source_artifact']),
            environment_lock=self.artifact_path(method['environment_artifact']),
            inputs={name: self.artifact_path(identity) for name, identity in step['inputs'].items()},
            request=record['remote_approval']['request'], grant=record['remote_approval']['grant'],
            issuer=record['remote_approval']['issuer'])
        canonical_json(receipt)
        with self._db() as db:
            current = self._get(db, 'run', job['run_id'])
            if current['status'] != 'remote_pending' or current.get('export') is not None:
                raise PermissionError('Remote export state changed')
            current['export'] = receipt
            self._put(db, 'run', job['run_id'], current, update=True)
            self._audit(db, actor, 'remote_job_exported', current)
        return receipt

    @staticmethod
    def _remote_transport_projection(observation, job, stage):
        from .colab_transport import ColabTransportObservation
        if type(observation) is not ColabTransportObservation:
            raise PermissionError('An authenticated Colab transport observation is required')
        projection = observation.verify(job=job, stage=stage)
        canonical_json(projection)
        return projection

    def revalidate_remote_dispatch(self, job_id):
        """Recheck exact approval and current custody immediately before remote exec."""
        from .rna_trial_authority import AuthenticatedTrialAuthority
        job, record = self._remote_job(job_id)
        authority = self._remote_authorities.get(job_id)
        if type(authority) is not AuthenticatedTrialAuthority:
            raise PermissionError('The live trusted reservation authority is unavailable; no resume or replay')
        if record['status'] not in {'remote_pending', 'remote_dispatched'} or record.get('transport_claim') is None:
            raise PermissionError('Remote transport has no outstanding one-use claim')
        approval = record['remote_approval']
        actual = authority.export_colab_public_approval(approval['request'], approval['grant'], job=job)
        if canonical_json(actual) != canonical_json(approval):
            raise PermissionError('Remote approval changed before dispatch')
        fresh = self.prepare_remote_step(job['step_id'], run_id=job['run_id'], job_id=job['job_id'],
            source_bundle_sha256=job['source_bundle_sha256'], validator_sha256=job['validator_sha256'],
            scope=job['scope'], limits=job['limits'], actor=job['actor'], correlation=job['correlation'])
        if canonical_json(fresh) != canonical_json(job):
            raise PermissionError('Remote job custody changed before dispatch')
        return record['transport_claim']

    def claim_remote_transport(self, job_id, dispatch_id, remote_session_id):
        """Durably spend the sole transport claim before any remote provisioning."""
        from .colab_execution_contract import identity
        self._require_writable()
        identity(dispatch_id, 32); identity(remote_session_id)
        job, record = self._remote_job(job_id)
        claim = {'format':'devbench.colab-transport-claim/1', 'job_id':job_id,
            'job_sha256':record['job_sha256'], 'dispatch_id':dispatch_id,
            'remote_session_id':remote_session_id}
        with self._db() as db:
            current = self._get(db, 'run', job['run_id'])
            if current['status'] != 'remote_pending' or current.get('export') is None or current.get('transport_claim') is not None:
                raise PermissionError('The remote transport claim is unavailable or already spent')
            current['transport_claim'] = claim
            self._put(db, 'run', job['run_id'], current, update=True)
            self._audit(db, job['actor'], 'remote_transport_claimed', claim)
        return claim

    def record_remote_dispatch(self, job_id, transport_receipt, *, actor):
        self._require_writable()
        job, record = self._remote_job(job_id)
        projection = self._remote_transport_projection(transport_receipt, job, 'dispatched')
        with self._db() as db:
            current = self._get(db, 'run', job['run_id'])
            if current['status'] != 'remote_pending' or current.get('export') is None:
                raise PermissionError('Remote job is not ready for a single dispatch')
            claim = current.get('transport_claim')
            if claim is None or any(projection[key] != claim[key] for key in ('job_id','job_sha256','dispatch_id','remote_session_id')):
                raise PermissionError('Remote dispatch does not match the spent transport claim')
            current.update(status='remote_dispatched', transport=projection)
            self._put(db, 'run', job['run_id'], current, update=True)
            self._audit(db, actor, 'remote_job_dispatched', current)
        return current

    def import_remote_result(self, job_id, archive, expected_root, *, transport_receipt, actor):
        """Serialize one import; retain any rejected return as a charged failure."""
        self._require_writable()
        job, record = self._remote_job(job_id)
        if record['status'] != 'remote_dispatched':
            raise PermissionError('Only an outstanding remote dispatch can return')
        directory = self.root / 'runs' / job['run_id']
        with (directory / 'remote-operation-lease').open('ab') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PermissionError('Another remote import owns this run') from error
            try:
                return self._import_remote_result_once(job_id, archive, expected_root,
                    transport_receipt=transport_receipt, actor=actor)
            except Exception:
                # Rejected remote bytes must not trigger another computation or
                # refund. A failed seal remains visibly unresolved for operators.
                current = self.get('run', job['run_id'])
                if current['status'] == 'remote_dispatched':
                    self._finalize_remote_failure_once(job_id, category='result_rejected', uncertain=True, actor=actor)
                raise

    def _import_remote_result_once(self, job_id, archive, expected_root, *, transport_receipt, actor):
        """Import bytes and structural evidence only; scientific validation is remote."""
        from .colab_result_import import verify_result_archive
        self._require_writable()
        job, record = self._remote_job(job_id)
        if record['status'] != 'remote_dispatched':
            raise PermissionError('Only an outstanding single remote dispatch can return')
        projection = self._remote_transport_projection(transport_receipt, job, 'returned')
        directory = self.root / 'runs' / job['run_id']
        # The importer uses a fresh directory and rejects duplicate imports. Any
        # invalid archive remains an explicit charged failure, never re-executed.
        imported = verify_result_archive(job, _path(archive), expected_root,
            destination=directory / 'remote-return', transport_projection=projection,
            dispatch_projection=record['transport'])
        receipt = record['reservation_evidence']
        if not verify_execution(_path(receipt['manifest_path']), receipt['root_hash'])['valid']:
            raise ValueError('Remote reservation evidence changed')
        dag = ExecutionDAG(directory / 'evidence', actor_id=actor)
        dag.append('remote_reservation_verified', {'receipt': receipt, 'job_sha256': record['job_sha256']})
        dag.add_artifact(directory / 'reservation-evidence', max_bytes=200_000_000, max_entries=256)
        published = {}
        for name, path in imported['outputs'].items():
            dag.add_artifact(path, max_bytes=job['limits']['max_output_bytes'], max_entries=1)
            published[name] = self._import_artifact(path,
                artifact_type=job['method_contract']['outputs'][name]['artifact_type'], actor=actor,
                generated_by=job['run_id'])['artifact_id']
        validation = {'passed': imported['validation']['passed'], 'remote': imported['validation'],
            'warnings': ['Synthetic Colab qualification only; network confinement is not proven; no airway admission'],
            'parameters': job['parameters'], 'numerical_execution_tier': 'colab'}
        record.update(status='succeeded', outputs=published, validation=validation,
            execution=imported['execution'], remote_transport_return=projection, finished_at=_now())
        for path in imported['evidence_paths']:
            dag.add_artifact(path, max_bytes=job['limits']['max_output_bytes'], max_entries=1)
        dag.append('method_finished', record)
        record['evidence'] = dag.finalize(analysis_fingerprint=record['job_sha256'])
        with self._db() as db:
            current = self._get(db, 'run', job['run_id'])
            if current['status'] != 'remote_dispatched':
                raise PermissionError('Remote import state changed')
            self._put(db, 'run', job['run_id'], record, update=True)
            self._audit(db, actor, 'remote_result_imported', record)
        return record

    def finalize_remote_failure(self, job_id, *, category, uncertain, actor):
        self._require_writable()
        job, _ = self._remote_job(job_id)
        with (self.root / 'runs' / job['run_id'] / 'remote-operation-lease').open('ab') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise PermissionError('Another remote operation owns this run') from error
            return self._finalize_remote_failure_once(job_id, category=category, uncertain=uncertain, actor=actor)

    def _finalize_remote_failure_once(self, job_id, *, category, uncertain, actor):
        self._require_writable()
        if category not in {'remote_error','transport_error','result_rejected','timeout','operator_stop'} or type(uncertain) is not bool:
            raise ValueError('Invalid remote failure projection')
        job, record = self._remote_job(job_id)
        with self._db() as db:
            current = self._get(db, 'run', job['run_id'])
            if current['status'] not in {'remote_pending','remote_dispatched'}:
                raise PermissionError('Remote job is already terminal')
            current.update(status='remote_uncertain' if uncertain else 'failed', outputs={},
                error=category, finished_at=_now(), approval_charge_retained=True,
                automatic_replay=False, local_fallback=False)
            dag = ExecutionDAG(self.root / 'runs' / job['run_id'] / 'failure-evidence', actor_id=actor)
            receipt = current['reservation_evidence']
            if not verify_execution(_path(receipt['manifest_path']), receipt['root_hash'])['valid']:
                raise ValueError('Remote reservation evidence changed')
            dag.append('remote_reservation_verified', {'receipt': receipt, 'job_sha256': current['job_sha256']})
            dag.append('method_finished', current)
            current['evidence'] = dag.finalize(analysis_fingerprint=current['job_sha256'])
            self._put(db, 'run', job['run_id'], current, update=True)
            self._audit(db, actor, 'remote_job_failed', current)
        return current

    def provenance(self, run_id):
        run = self.get("run", run_id)
        if "evidence" not in run:
            return {"valid": False, "errors": ["No completed execution evidence"]}
        receipt = run["evidence"]
        report = verify_execution(_path(receipt["manifest_path"]), receipt["root_hash"])
        if report["valid"]:
            manifest = json.loads(Path(receipt["manifest_path"]).read_bytes())
            node = Path(receipt["manifest_path"]).parent / "nodes" / (manifest["node_ids"][-1] + ".json")
            actual = json.loads(node.read_bytes())["payload"]
            if actual != {key: value for key, value in run.items() if key != "evidence"}:
                report.update(valid=False, errors=["Stored run differs from its execution evidence"])
        return report

    def reproduce(self, run_id, *, actor, executor=None):
        run = self.get("run", run_id)
        if run["status"] != "succeeded" or not self.provenance(run_id)["valid"]:
            raise ValueError("Reproduction requires a verified successful run")
        return self.run_step(run["step_id"], actor=actor, executor=executor, reproduces=run_id)

    def propose(self, run_id, *, actor, rationale):
        run = self.get("run", run_id)
        if run["status"] != "succeeded" or not run["validation"]["passed"] or not self.provenance(run_id)["valid"]:
            raise ValueError("A verified successful run is required for a proposal")
        for artifact_id in run["outputs"].values():
            self.artifact_path(artifact_id)
        branch = self.get("branch", run["branch_id"])
        changes = self._proposal_changes(run, branch)
        proposal = {"proposal_id": uuid.uuid4().hex, "analysis_id": branch["analysis_id"],
                    "base_revision": branch["base_revision"], "run_id": run_id, "step_id": run["step_id"],
                    "outputs": run["outputs"], "preview": run["validation"], "evidence_root": run["evidence"]["root_hash"],
                    "changes": changes,
                    "dependency_previews": [{"run_id": item["run_id"], "preview": self.get("run", item["run_id"])["validation"]}
                                            for item in changes if item["run_id"] != run_id],
                    "status": "pending", "rationale": _reason(rationale), "created_by": _label(actor)}
        with self._db() as db:
            self._put(db, "proposal", proposal["proposal_id"], proposal)
            self._audit(db, actor, "proposal_created", proposal)
        return proposal

    def _proposal_changes(self, run, branch):
        base = self.get("revision", branch["base_revision"])
        approved = {item["run_id"] for item in base["steps"]}
        changes, visiting, visited = [], set(), set()
        def collect(item):
            identity = item["run_id"]
            if identity in approved or identity in visited:
                return
            if identity in visiting:
                raise ValueError("Cyclic exploratory dependencies")
            if (item["branch_id"] != branch["branch_id"] or item["status"] != "succeeded"
                    or not item["validation"]["passed"] or not self.provenance(identity)["valid"]):
                raise ValueError("Unapproved dependency lacks valid branch evidence")
            visiting.add(identity)
            for artifact_id in item["inputs"].values():
                self.artifact_path(artifact_id)
                origin = self.get("artifact", artifact_id)["generated_by"]
                if origin:
                    collect(self.get("run", origin))
            for artifact_id in item["outputs"].values():
                self.artifact_path(artifact_id)
            changes.append({"run_id": identity, "step_id": item["step_id"], "outputs": item["outputs"],
                            "method_version_id": item["method_version_id"], "evidence_root": item["evidence"]["root_hash"]})
            visiting.remove(identity)
            visited.add(identity)
        collect(run)
        return changes

    def approve(self, proposal_id, *, actor, rationale, acknowledge_warnings=False, _on_approval=None):
        rationale, actor = _reason(rationale), _label(actor)
        proposal = self.get("proposal", proposal_id)
        run = self.get("run", proposal["run_id"])
        if (not self.provenance(run["run_id"])["valid"] or run["status"] != "succeeded"
                or proposal["evidence_root"] != run["evidence"]["root_hash"]
                or proposal["preview"] != run["validation"] or proposal["outputs"] != run["outputs"]):
            raise ValueError("Proposal scientific evidence changed")
        branch = self.get("branch", run["branch_id"])
        changes = self._proposal_changes(run, branch)
        dependency_previews = [{"run_id": item["run_id"], "preview": self.get("run", item["run_id"])["validation"]}
                               for item in changes if item["run_id"] != run["run_id"]]
        if changes != proposal["changes"] or dependency_previews != proposal["dependency_previews"]:
            raise ValueError("Proposal dependency evidence changed")
        if (proposal["preview"]["warnings"] or any(item["preview"]["warnings"] for item in dependency_previews)) and acknowledge_warnings is not True:
            raise ValueError("Scientific review must acknowledge validation warnings")
        for artifact_id in proposal["outputs"].values():
            self.artifact_path(artifact_id)
        with self._db() as db:
            current_proposal = self._get(db, "proposal", proposal_id)
            if current_proposal != proposal or proposal["status"] != "pending":
                raise ValueError("Proposal is no longer pending")
            head = db.execute("SELECT revision_id FROM heads WHERE analysis_id=?", (proposal["analysis_id"],)).fetchone()
            if head is None or head[0] != proposal["base_revision"]:
                raise ValueError("stale proposal: the approved plan revision changed")
            parent = self._get(db, "revision", head[0])
            revision = {"analysis_id": proposal["analysis_id"], "name": parent["name"], "parent_revision": head[0],
                        "steps": [*parent["steps"], *changes],
                        "approved_by": actor, "review_rationale": rationale, "proposal_id": proposal_id,
                        "identity_assurance": "caller-asserted", "approved_at": _now()}
            revision["revision_id"] = _digest(revision)
            self._put(db, "revision", revision["revision_id"], revision)
            db.execute("UPDATE heads SET revision_id=? WHERE analysis_id=?", (revision["revision_id"], proposal["analysis_id"]))
            self._put(db, "proposal", proposal_id, dict(proposal, status="approved", revision_id=revision["revision_id"]), update=True)
            if _on_approval is not None:
                _on_approval(db, revision, proposal, run)
            self._audit(db, actor, "proposal_approved", revision)
        return revision

    def reject(self, proposal_id, *, actor, rationale):
        with self._db() as db:
            proposal = self._get(db, "proposal", proposal_id)
            if proposal["status"] != "pending":
                raise ValueError("Proposal is no longer pending")
            proposal.update(status="rejected", reviewed_by=_label(actor), review_rationale=_reason(rationale))
            self._put(db, "proposal", proposal_id, proposal, update=True)
            self._audit(db, actor, "proposal_rejected", proposal)
        return proposal

    def lineage(self, artifact_id):
        self.artifact_path(artifact_id)
        edges = []
        with self._db() as db:
            identities = [row[0] for row in db.execute("SELECT id FROM records WHERE kind='run'")]
            for identity in identities:
                run = self._get(db, "run", identity)
                if run["status"] == "succeeded" and artifact_id in run["outputs"].values():
                    edges.append({"run_id": identity, "parents": run["inputs"], "method_version_id": run["method_version_id"]})
        return {"artifact_id": artifact_id, "generated_by": edges}

    def audit(self):
        previous, count = None, 0
        with self._db() as db:
            for sequence, encoded, identity in db.execute("SELECT sequence,payload,sha256 FROM audit ORDER BY sequence"):
                event = json.loads(encoded)
                if sequence != count + 1 or event["sequence"] != sequence or event["previous"] != previous or _digest(event) != identity:
                    raise ValueError("Audit chain mismatch")
                count, previous = sequence, identity
        return {"valid": True, "event_count": count, "root_hash": previous, "identity_assurance": "caller-asserted"}

    def reconcile(self):
        """Identify dead coordinator leases without interrupting another live process."""
        self._require_writable()
        recovered = []
        with self._db() as db:
            running = [self._get(db, "run", row[0]) for row in db.execute("SELECT id FROM records WHERE kind='run'")]
        def needs_reconciliation(value):
            return (value["status"] == "running" or value.get("backend") == "trusted_fastq"
                    and value["status"] in {"failed", "interrupted"} and value.get("cleanup_required") is True)
        for record in running:
            if not needs_reconciliation(record):
                continue
            path = _path(self.root / "runs" / record["run_id"] / "lease")
            with path.open("ab") as lease:
                try:
                    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                fastq_recovery = None
                if record.get("backend") == "trusted_fastq":
                    with self._db() as db:
                        current = self._get(db, "run", record["run_id"])
                    if not needs_reconciliation(current):
                        continue
                    try:
                        from .fastq_execution import reconcile_fastq_runtime
                        fastq_recovery = reconcile_fastq_runtime(current.get("runtime_events", []),
                            self.root / "runs" / record["run_id"] / "fastq-workflow")
                    except Exception as error:
                        fastq_recovery = {"status": "uncertain", "execution_restarted": False,
                                          "reason": f"{type(error).__name__}: {error}"[:2000]}
                with self._db() as db:
                    current = self._get(db, "run", record["run_id"])
                    if not needs_reconciliation(current):
                        continue
                    prior = json.loads(canonical_json(current))
                    if current["status"] == "running":
                        current.update(status="interrupted", outputs={}, finished_at=_now(),
                                       error="Coordinator exited before a sealed run result was published")
                    if fastq_recovery is not None:
                        current["runtime_reconciliation"] = fastq_recovery
                        current["runtime_reconciled_at"] = _now()
                        current["cleanup_required"] = fastq_recovery["status"] != "reconciled"
                        current["runtime_uncertain"] = current["cleanup_required"]
                        # Preserve sealed failure evidence while sealing each later
                        # cleanup update. This is the same run and abandoned lease,
                        # not a new execution/controller or rewritten old receipt.
                        previous_evidence = current.pop("evidence", None)
                        if previous_evidence is not None:
                            current["previous_execution_evidence"] = previous_evidence
                        evidence_dir = self.root / "runs" / record["run_id"] / ("reconciliation-evidence-" + uuid.uuid4().hex)
                        evidence = ExecutionDAG(evidence_dir, actor_id="restart-reconciliation")
                        evidence.append("previous_run_record", prior)
                        evidence.append("runtime_reconciled", current)
                        current["evidence"] = evidence.finalize(analysis_fingerprint=_digest({
                            "run_id": record["run_id"], "previous_evidence": previous_evidence,
                            "runtime_reconciliation": fastq_recovery}))
                    if record.get("backend") == "local_docker":
                        try:
                            from devharness_controlled.native_runtime import LocalDocker
                            LocalDocker()._call(["rm", "--force", "--volumes", "devharness-method-" + record["run_id"]])
                        except Exception:
                            current["cleanup_required"] = True
                    self._put(db, "run", record["run_id"], current, update=True)
                    self._audit(db, "restart-reconciliation",
                                "run_interrupted" if prior["status"] == "running" else "runtime_reconciled", current)
                    recovered.append(record["run_id"])
        return recovered
