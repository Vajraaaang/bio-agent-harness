"""Local question/proposal interface for the existing reviewed scRNA workbench.

Reuses the existing, explicitly authorized proposal selector. No input upload,
arbitrary command endpoint or additional scientific engine is provided.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from .checkpoints import atomic_json, sha256, digest


ASSET = Path(__file__).parent / "assets/workbench-ui.html"
MAX_BODY = 65536
ACTIVE = {"queued", "inspecting", "planning", "recording_review", "preparing", "running", "cancel_requested", "finalizing"}
CONFIRMATIONS = {"question_and_scope", "input_provenance", "capture_units", "settings_and_limits", "interpretation_limits"}
RESOURCE_CAPS = {"memory_mb", "stage_timeout_seconds", "scvi_timeout_seconds", "max_block_bytes", "max_batch_bytes", "max_selected_bytes", "block_rows"}
WORKSPACE_VIEWS = {"overview", "data", "analyses", "runs", "library", "files", "search", "register", "review", "execute", "results", "scientific"}
WORKSPACE_TABS = {"analyst", "code", "execution", "messages", "provenance", "environment", "review", "display"}
DOWNLOADS = {"report.json", "summary.md", "citations.json", "measurements.json", "parameters.json",
             "reviewer-report.json", "notebook.html", "notebook.ipynb", "html-export.json"}


def decode_json(body, *, max_bytes=MAX_BODY):
    if not isinstance(body, bytes) or len(body) > max_bytes:
        raise ValueError("JSON size exceeds the permitted bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("JSON numbers must be finite")
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
        json.dumps(value, allow_nan=False)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as error:
        raise ValueError("Invalid finite JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Use a JSON object")
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 16 or count > 20000:
            raise ValueError("JSON depth or item count exceeds the bound")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def _canonical(value, *, exists=False):
    if not isinstance(value, (str, Path)):
        raise ValueError("Use an absolute canonical path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path != path.resolve(strict=exists):
        raise ValueError("Use an absolute canonical path without symlinks or traversal")
    return path


def _read(path, maximum=1024 * 1024):
    path = _canonical(path, exists=True)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Read only regular retained records")
    with path.open("rb") as handle:
        return decode_json(handle.read(maximum + 1), max_bytes=maximum)


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _validate_ui_state(value, *, scientific=False):
    """Only location and an unsubmitted question; never tokens or execution settings."""
    limits = {"selected_job": 32, "selected_revision": 32, "parent_root": 64,
              "figure": 150, "search": 500, "question_draft": 8000}
    choices = {"view": WORKSPACE_VIEWS, "tab": WORKSPACE_TABS, "zoom": {"fit", "native"},
               "run_filter": {"all", "active", "attention", "analysis", "presentation"}}
    flags = {"inspector_open", "evidence_open"}
    refs = {"dataset_ref": "dataset", "analysis_ref": "analysis", "result_ref": "result"} if scientific else {}
    if scientific:
        limits.update(study_id=32, scientific_run_id=32, planner_session_id=32)
    if not isinstance(value, dict) or set(value) - (limits.keys() | choices.keys() | flags | refs.keys()):
        raise ValueError("Use only the declared workspace UI fields")
    for key, item in value.items():
        if key in refs:
            valid = item is None or (isinstance(item, dict) and set(item) == {"object_type", "object_id", "version_id"}
                and item["object_type"] == refs[key] and isinstance(item["object_id"], str)
                and re.fullmatch(r"[0-9a-f]{32}", item["object_id"]) is not None
                and isinstance(item["version_id"], str) and re.fullmatch(r"[0-9a-f]{64}", item["version_id"]) is not None)
        elif key in flags:
            valid = type(item) is bool
        elif key in choices:
            valid = isinstance(item, str) and item in choices[key]
        else:
            valid = isinstance(item, str) and len(item) <= limits[key] and "\x00" not in item
            if valid and item and key in {"selected_job", "selected_revision", "parent_root", "study_id", "scientific_run_id", "planner_session_id"}:
                valid = re.fullmatch(r"[0-9a-f]{" + str(limits[key]) + "}", item) is not None
        if not valid:
            raise ValueError("Invalid workspace UI field: " + key)
    return _copy(value)


class WorkspaceSchemaError(ValueError):
    """An unknown schema must not be downgraded through backup recovery."""


def _migrate_workspace_state(value):
    """Validate known envelopes; preserve identity and view state when upgrading v1."""
    if not isinstance(value, dict):
        raise ValueError("Invalid retained workspace metadata")
    if not isinstance(value.get("format"), str) or value["format"] not in {"devbench.workspace/1", "devbench.workspace/2", "devbench.workspace/3"}:
        if isinstance(value.get("format"), str) and re.fullmatch(r"devbench\.workspace/[0-9]+", value["format"]):
            raise WorkspaceSchemaError("Unsupported workspace metadata schema; use its compatible service version")
        raise ValueError("Invalid retained workspace metadata envelope")
    legacy = value["format"] == "devbench.workspace/1"
    keys = {"format", "revision", "project", "ui"} | (set() if legacy else {"schema_version"})
    if not legacy:
        if type(value.get("schema_version")) is not int:
            raise ValueError("Workspace schema_version must be an integer")
        if value["schema_version"] != int(value["format"].rsplit("/", 1)[1]):
            raise WorkspaceSchemaError("Unsupported workspace metadata schema version")
    project = value.get("project")
    if (set(value) != keys or type(value.get("revision")) is not int
            or not 0 <= value["revision"] <= 2**53 - 1
            or not isinstance(project, dict) or set(project) != {"id", "name", "scope"}
            or not isinstance(project["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", project["id"])
            or project["scope"] != "single_user_local"
            or not isinstance(project["name"], str) or not 1 <= len(project["name"].strip()) <= 120
            or "\x00" in project["name"]):
        raise ValueError("Invalid retained workspace metadata")
    _validate_ui_state(value.get("ui"), scientific=value["format"] == "devbench.workspace/3")
    result = _copy(value)
    if legacy:
        result.update(format="devbench.workspace/2", schema_version=2)
    return result


class WorkbenchService:
    """One sequential writer; native APIs retain scientific and execution control."""

    def __init__(self, workspace, input_roots, *, parameter_overrides=None,
                 run_timeout_seconds=3600, allow_data_execution=False,
                 proposal_selector=None, codex_model=None, agent_workspaces=None,
                 allow_model_execution=False, model_data_scope="local_only", restore_view_state_backup=False,
                 enable_scientific=False, scientific_workspace=None, method_selector=None, method_executor=None,
                 restore_scientific_backup=None):
        from devbench.study.permissions import require_protected_storage
        from .workbench import default_parameters
        self.workspace = _canonical(workspace)
        if restore_scientific_backup is not None and not enable_scientific:
            raise ValueError("Explicit scientific recovery requires the scientific workspace to be enabled")
        require_protected_storage(self.workspace)
        self.input_roots = tuple(_canonical(path, exists=True) for path in input_roots)
        if not self.input_roots or any(not path.is_dir() or path == Path("/") for path in self.input_roots):
            raise ValueError("Configure explicit existing input directories")
        if any(self.workspace.is_relative_to(path) or path.is_relative_to(self.workspace) for path in self.input_roots):
            raise ValueError("Input roots and the dedicated output workspace must be disjoint")
        defaults = default_parameters(data_sha256="0" * 64)
        overrides = _copy(parameter_overrides or {})
        if not isinstance(overrides, dict) or set(overrides) - (set(defaults) - {
                "workflow", "prompt", "execution_tier", "data_sha256", "parent_root", "model_path", "model_sha256"}):
            raise ValueError("Use existing scientific settings; input, source, model and tier bindings are not profile overrides")
        self.parameters = default_parameters(data_sha256="0" * 64, **overrides)
        if any(self.parameters[key] > defaults[key] for key in RESOURCE_CAPS):
            raise ValueError("A UI profile cannot widen an existing resource ceiling")
        if type(run_timeout_seconds) is not int or not 1 <= run_timeout_seconds <= 3600:
            raise ValueError("Run deadline must be within the existing 3600-second ceiling")
        self.run_timeout_seconds = run_timeout_seconds
        self.allow_data_execution = allow_data_execution is True
        self.runtime_identity = self._runtime_identity()
        self.allow_model_execution = allow_model_execution is True
        self.model_data_scope = model_data_scope
        if model_data_scope not in {"local_only", "synthetic", "public"}:
            raise ValueError("Use an explicit supported model context scope")
        if proposal_selector is not None and codex_model is not None:
            raise ValueError("Choose one proposal selector")
        if codex_model is not None:
            from .episode_codex import CodexActionSelector
            if agent_workspaces is None:
                raise ValueError("Codex selection requires an explicit protected actor workspace parent")
            actors = _canonical(agent_workspaces)
            require_protected_storage(actors)
            if (actors.is_relative_to(self.workspace) or self.workspace.is_relative_to(actors)
                    or any(actors.is_relative_to(p) or p.is_relative_to(actors) for p in self.input_roots)):
                raise ValueError("Actor workspace, inputs and private service state must be disjoint")
            proposal_selector = CodexActionSelector(requested_model=codex_model, workspace_parent=actors,
                private_root=self.workspace, memory_mb=min(2048, self.parameters["memory_mb"]))
        from .episode_agent import selector_configuration
        self.proposal_selector = proposal_selector
        self.selector_config = selector_configuration(proposal_selector) if proposal_selector is not None else None
        if self.selector_config and self.selector_config.get("backend") not in {"codex-cli", "recorded_planning_answer"}:
            raise ValueError("Use the registered Codex or explicit recorded-answer proposal selector")
        if self.allow_model_execution and (not self.selector_config or self.selector_config["backend"] != "codex-cli"
                                          or model_data_scope not in {"synthetic", "public"}):
            raise ValueError("Model permission requires the Codex selector and synthetic/public context scope")
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._workspace_lock = None
        self._executor = None
        self._jobs = {}
        self._cancellations = {}
        self._futures = {}
        self._workspace_notice = None
        self._workspace_metadata_fault = False
        configuration = {"format": "devbench.workbench-ui/1", "input_roots": [str(p) for p in self.input_roots],
                         "parameters": self.parameters, "run_timeout_seconds": run_timeout_seconds}
        if self.selector_config is not None:
            configuration.update(proposal_selector=self.selector_config, model_data_scope=model_data_scope)
        marker = self.workspace / "service.json"
        if self.workspace.exists():
            if not self.workspace.is_dir() or (any(path.name != ".service.lock" for path in self.workspace.iterdir()) and not marker.is_file()):
                raise ValueError("Use a dedicated empty workspace or this service's existing workspace")
        self.workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.workspace.chmod(0o700)
        self._acquire_lock()
        try:
            fresh_workspace = not marker.exists()
            if marker.exists() and _read(marker) != configuration:
                raise ValueError("Existing workspace has different frozen settings; use a fresh workspace")
            if not marker.exists():
                atomic_json(marker, configuration)
            self._workspace_state = self._load_workspace_state(restore_view_state_backup, fresh_workspace=fresh_workspace)
            self._path("jobs").mkdir(exist_ok=True, mode=0o700)
            for path in sorted(self._path("jobs").iterdir()):
                if len(self._jobs) >= 100 or not re.fullmatch(r"[0-9a-f]{32}", path.name):
                    raise ValueError("Invalid or excessive job directories in service workspace")
                record = _read(self._path("jobs", path.name, "state.json"))
                if record.get("id") != path.name:
                    raise ValueError("Job state identity mismatch")
                self._jobs[path.name] = record
                if record.get("cache_validation_pending"):
                    self._update(path.name,cache_validation_pending=False,cache_worker_liveness="unknown")
                if record["status"] in ACTIVE or record.get("operation_pending"):
                    self._update(path.name, status="recovery_required", worker_liveness="unknown",
                        interrupted_status=record["status"], operation_pending=False,
                        error={"type": "ServiceRestart", "message": "Prior attempt has no completed service record. Native evidence is retained; inspect and recover it before further execution. Worker liveness is unknown."})
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reviewed-scrna")
            self.scientific = None
            if enable_scientific:
                from .scientific_workspace import ScientificWorkspace
                self.scientific = ScientificWorkspace(self, scientific_workspace or self._path("methods"),
                    selector=method_selector or (self.proposal_selector if codex_model is not None else None),
                    executor=method_executor, restore_backup=restore_scientific_backup)
        except BaseException:
            self._release_lock()
            raise

    def _sync_workspace_directory(self):
        descriptor = os.open(self.workspace, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_workspace_state(self, value, *, previous=None):
        # Reuse the existing fsynced temp-write/replace. Only UI metadata adds
        # a previous valid snapshot and directory fsync; scientific writers stay unchanged.
        if previous is not None:
            atomic_json(self._path("workspace.previous.json"), previous)
            self._sync_workspace_directory()
        atomic_json(self._path("workspace.json"), value)
        self._sync_workspace_directory()

    def _load_workspace_state(self, restore_backup, *, fresh_workspace=False):
        if type(restore_backup) is not bool:
            raise ValueError("View-state recovery requires an explicit boolean")
        primary, backup = self._path("workspace.json"), self._path("workspace.previous.json")
        for path in (primary, backup):
            if path.exists() and not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("Workspace metadata and backups must be regular files")
        if not primary.exists() and not backup.exists():
            if not fresh_workspace:
                raise ValueError("Workspace metadata and its backup are missing. Preserve this existing workspace and recover a retained metadata snapshot; its project identity will not be reset.")
            if restore_backup:
                raise ValueError("No retained view-state backup is available")
            value = {"format": "devbench.workspace/2", "schema_version": 2, "revision": 0,
                     "project": {"id": uuid.uuid4().hex, "name": "RNA research", "scope": "single_user_local"},
                     "ui": {"view": "overview", "tab": "analyst", "zoom": "fit"}}
            self._write_workspace_state(value, previous=value)
            return value
        try:
            original = _read(primary)
            value = _migrate_workspace_state(original)
        except WorkspaceSchemaError:
            raise
        except (ValueError, FileNotFoundError) as error:
            if not restore_backup:
                raise ValueError("Workspace view state is missing or invalid. Preserve it; restart with --restore-view-state-backup only to restore the previous saved location and draft.") from error
            # Validate the backup before preserving the invalid primary or writing anything.
            value = _migrate_workspace_state(_read(backup))
            recovered_revision = value["revision"]
            value["revision"] += 1
            _migrate_workspace_state(value)
            if primary.exists():
                primary.replace(self._path("workspace.invalid-" + uuid.uuid4().hex + ".json"))
                self._sync_workspace_directory()
            self._write_workspace_state(value)
            self._workspace_notice = f"Recovered the saved location and question draft from backup revision {recovered_revision}. Review this restored selection; no scientific records or execution permissions were restored or changed."
            return value
        if restore_backup:
            raise ValueError("Current view state is valid; refusing to roll it back from backup")
        if value != original:
            self._write_workspace_state(value, previous=original)
        return value

    def _acquire_lock(self):
        path = self._path(".service.lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Workspace lock must be a regular file")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Workspace is already owned by another service") from error
            self._workspace_lock = descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _release_lock(self):
        with self._lock:
            if self._workspace_lock is not None:
                fcntl.flock(self._workspace_lock, fcntl.LOCK_UN)
                os.close(self._workspace_lock)
                self._workspace_lock = None

    def _path(self, *parts):
        path = _canonical(self.workspace.joinpath(*parts))
        if not path.is_relative_to(self.workspace):
            raise ValueError("Output escaped the dedicated workspace")
        return path

    def _job(self, job):
        if not isinstance(job, str) or not re.fullmatch(r"[0-9a-f]{32}", job):
            raise ValueError("Invalid job identifier")
        if job not in self._jobs:
            raise KeyError("Unknown job")
        self._path("jobs", job)
        return self._jobs[job]

    def _input(self, value):
        source = _canonical(value, exists=True)
        if not any(source.is_relative_to(root) for root in self.input_roots):
            raise ValueError("Input is outside the configured roots")
        if source.suffix.lower() != ".h5ad" or not stat.S_ISREG(source.stat().st_mode):
            raise ValueError("Register an existing regular H5AD file")
        return source

    @staticmethod
    def _runtime_identity():
        from .workbench_proposals import _source_identity
        from .runtime import observe_runtime
        runtime = observe_runtime("local_supervised",detect_gpu=False)["runtime"]
        return {"source_sha256":_source_identity(), "python":runtime["python"], "packages":runtime["packages"]}

    def _require_current_runtime(self):
        if self._runtime_identity() != self.runtime_identity:
            raise RuntimeError("Service source/runtime changed; restart before submitting or resuming an operation")

    def _manifest(self, job, kind, receipt):
        path = _canonical(receipt["manifest_path"], exists=True)
        directory = "planning/proposal" if kind == "proposal" and self._job(job).get("proposal_mode") == "agent" else kind
        if not path.is_relative_to(self._path("jobs", job, directory)):
            raise ValueError("Evidence path escaped the job's protected records")
        return path

    def _update(self, job, **fields):
        with self._lock:
            record = self._job(job)
            if fields.get("status",record.get("status")) != record.get("status"):
                history = record.setdefault("state_history", [])
                if len(history) >= 200:
                    raise RuntimeError("Job transition limit reached; preserve this attempt and use a reviewed revision")
                history.append({"from":record.get("status"), "to":fields["status"],
                                "at":datetime.now(timezone.utc).isoformat()})
            record.update(fields, updated_at=datetime.now(timezone.utc).isoformat())
            atomic_json(self._path("jobs", job, "state.json"), record)

    def configuration(self):
        mode = ("codex" if self.selector_config["backend"] == "codex-cli" else "recorded_fixture") if self.selector_config else "disabled"
        available = self.proposal_selector is not None and (mode != "codex" or self.allow_model_execution)
        return _copy({"input_roots": [str(p) for p in self.input_roots],
            "parameters": {key: value for key, value in self.parameters.items() if key != "data_sha256"},
            "run_timeout_seconds": self.run_timeout_seconds, "execution_allowed": self.allow_data_execution,
            "model_access": mode == "codex" and available, "plan_author": "operator_created_recorded_plan",
            "proposal_selection": {"mode": mode, "available": available,
                "configuration":self.selector_config,"model_data_scope":self.model_data_scope,
                "label": {"disabled":"Agent proposal selection is disabled", "codex":"Codex agent proposals",
                          "recorded_fixture":"Recorded offline proposal fixture; no live model"}[mode]},
            "support": "scRNA UMI counts; QC, integration, clustering and available annotation",
            "scope": "Research use; operator declarations are not independent experimental verification",
            "scientific": self.scientific.configuration() if self.scientific else {"enabled": False}})

    def jobs(self):
        with self._lock:
            summaries = []
            for job in reversed(self._jobs):
                row = self.status(job)
                summary = {key: row.get(key) for key in ("id", "question", "status", "updated_at",
                    "parent_job", "figure_id", "worker_liveness", "interrupted_status", "cancellable", "resumable")}
                summary["operation_kind"] = row.get("operation_kind", "analysis")
                prior = next((item.get("from") for item in reversed(row.get("state_history", []))
                              if item.get("to") == "cancel_requested"), None)
                interrupted = row.get("interrupted_status") if row["status"] == "recovery_required" else row["status"]
                summary["pending_execution"] = (interrupted in {"preparing", "running"}
                    or interrupted == "cancel_requested" and prior in {"preparing", "running"}
                    or summary["operation_kind"] == "presentation" and interrupted in {"queued", "preparing", "running", "cancel_requested"})
                summary["execution_history"] = [{key: item[key] for key in ("attempt", "status", "elapsed_sec") if key in item}
                                                  for item in row.get("execution_history", [])]
                source = (row.get("proposal") or {}).get("external_input") or {}
                summary["input"] = {key: source[key] for key in ("path", "sha256", "size_bytes") if key in source}
                if not summary["input"] and row.get("source"):
                    summary["input"] = {"path": row["source"]}
                summary["proposal_root"] = (row.get("proposal_receipt") or {}).get("root_hash")
                summary["review_decision"] = (row.get("review") or {}).get("decision")
                summary["bundle_root"] = (row.get("execution") or {}).get("bundle_root")
                summaries.append(summary)
            return summaries

    def workspace_state(self, changes=None):
        """Local project metadata, deliberately outside immutable scientific bundles."""
        with self._lock:
            if self._workspace_metadata_fault:
                raise RuntimeError("Workspace metadata is unreadable after an interrupted save; preserve it and recover view state before saving again")
            if changes is None:
                result = _copy(self._workspace_state)
                if self._workspace_notice:
                    result["recovery_notice"] = self._workspace_notice
                return result
            if (not isinstance(changes, dict) or set(changes) - {"expected_revision", "name", "ui"}
                    or type(changes.get("expected_revision")) is not int):
                raise ValueError("Use the declared workspace fields and expected revision")
            if changes["expected_revision"] != self._workspace_state["revision"]:
                raise RuntimeError("Workspace changed in another window. Reconnect before saving; your local draft is still visible.")
            updated = _copy(self._workspace_state)
            if "name" in changes:
                name = changes["name"]
                if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or "\x00" in name:
                    raise ValueError("Use a project name of 1 to 120 characters")
                updated["project"]["name"] = name.strip()
            if "ui" in changes:
                updated["ui"].update(_validate_ui_state(changes["ui"], scientific=updated["schema_version"] == 3))
            updated["revision"] += 1
            _migrate_workspace_state(updated)
            try:
                self._write_workspace_state(updated, previous=self._workspace_state)
            except OSError as error:
                # Replacement may already have happened. Never continue accepting
                # writes against a stale in-memory revision after a durability error.
                try:
                    self._workspace_state = _migrate_workspace_state(_read(self._path("workspace.json")))
                except (ValueError, OSError):
                    self._workspace_metadata_fault = True
                raise RuntimeError("Workspace save was interrupted and may already be stored. Reconnect to verify the retained revision; keep your local draft.") from error
            self._workspace_state = updated
            return self.workspace_state()

    def upgrade_scientific_view(self):
        """Explicit compatible view upgrade; scientific records and grants stay separate."""
        with self._lock:
            if self._workspace_state["schema_version"] == 3:
                return
            updated = _copy(self._workspace_state)
            updated.update(format="devbench.workspace/3", schema_version=3)
            _migrate_workspace_state(updated)
            try:
                self._write_workspace_state(updated, previous=self._workspace_state)
            except OSError as error:
                try:
                    self._workspace_state = _migrate_workspace_state(_read(self._path("workspace.json")))
                except (ValueError, OSError):
                    self._workspace_metadata_fault = True
                raise RuntimeError("Scientific view upgrade was interrupted; reconnect to verify the retained schema and revision") from error
            self._workspace_state = updated

    def status(self, job):
        with self._lock:
            record = _copy(self._job(job))
            if record.get("cache_worker_liveness") == "unknown":
                record["retained_outcome"] = record["status"]
                record["status"] = "recovery_required"
            if record.get("operation_pending") and record["status"] not in ACTIVE:
                record["status"] = "finalizing"
            record["cancellable"] = record["status"] in (ACTIVE - {"cancel_requested"}) | {"approved", "awaiting_operator_review", "awaiting_information"}
            if record["status"] == "finalizing":
                record["cancellable"] = False
            record["remaining_execution_seconds"] = self._remaining(record)
            record["resumable"] = self._can_resume(record)
            return record

    def _remaining(self, record):
        elapsed = 0.0
        for attempt in record.get("execution_history", []):
            value = attempt.get("elapsed_sec")
            if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
                raise ValueError("Retained execution budget accounting is invalid")
            elapsed += value
        return max(0.0,self.run_timeout_seconds-elapsed)

    def _can_resume(self, record):
        if record.get("operation_kind") == "presentation":
            return False
        bundle = self._path("jobs",record["id"],"bundle")
        return (self.allow_data_execution and record["status"] == "cancelled"
                and not record.get("operation_pending")
                and record.get("cache_worker_liveness") != "unknown"
                and record.get("worker_liveness") == "stopped" and self._remaining(record) > 0
                and bool(record.get("execution_history")) and (bundle/"review-binding.json").is_file()
                and not (bundle/"receipts.json").exists() and not (bundle/".reviewed-execution.json").exists())

    def _require_known_workers(self):
        self._require_current_runtime()
        if any((record["status"] == "recovery_required" and record.get("worker_liveness") == "unknown")
               or record.get("cache_worker_liveness") == "unknown"
               for record in self._jobs.values()):
            raise RuntimeError("Reconcile unknown worker liveness before launching further operations")

    def _submit(self, job, function):
        self._cancellations[job] = threading.Event()
        self._update(job,operation_pending=True)
        self._futures[job] = self._executor.submit(self._task,job,function)

    def _task(self, job, function):
        from devharness_core.watchdog import cancel_workers_on, WorkerCancelled
        event = self._cancellations[job]
        control = {"workers": []}
        try:
            with cancel_workers_on(event) as control:
                if event.is_set():
                    raise WorkerCancelled("Operation cancelled before dispatch")
                self._require_current_runtime()
                function()
        except BaseException as error:
            self._update(job, status="cancelled" if isinstance(error, WorkerCancelled) else
                "interrupted_service" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "operation_failed",
                error={"type": type(error).__name__, "message": str(error)[:2000]}, completed_analysis=False)
        finally:
            stopped = all(worker["cleanup"].get("verified_stopped") is True for worker in control["workers"])
            with self._lock:
                record = self._job(job)
                fields = {"worker_liveness":"stopped" if stopped else "unknown", "worker_control":control,
                          "operation_pending":False}
                if not stopped:
                    fields.update(status="recovery_required", completed_analysis=False)
                elif event.is_set() and not (record["status"].startswith("completed_review_") or record["status"] == "completed_presentation"):
                    fields.update(status="cancelled", completed_analysis=False)
                elif event.is_set():
                    fields["cancellation_resolution"] = "completed_before_stop"
                self._update(job, **fields)

    def register(self, request):
        from .workbench_proposals import _configuration
        if (not isinstance(request, dict) or set(request) - {"source", "expected_sha256", "question", "declarations", "proposal_mode"}
                or not {"source", "expected_sha256", "question", "declarations"} <= request.keys()):
            raise ValueError("Registration fields are source, expected_sha256, question and declarations")
        mode = request.get("proposal_mode", "operator")
        if mode not in {"operator", "agent"}:
            raise ValueError("Use operator or agent proposal mode")
        if mode == "agent" and not self.configuration()["proposal_selection"]["available"]:
            raise PermissionError("Agent proposal selector is unavailable or model permission is disabled")
        self._require_current_runtime()
        if mode == "agent":
            from .episode_agent import selector_configuration
            if selector_configuration(self.proposal_selector) != self.selector_config:
                raise ValueError("Proposal selector configuration changed; use a fresh reviewed service configuration")
        source = self._input(request["source"])
        params = {**self.parameters, "data_sha256": request["expected_sha256"]}
        config, _ = _configuration(request["question"], params, request["declarations"], self.run_timeout_seconds)
        request_identity = digest({"source":str(source), "configuration":config, "proposal_mode":mode,
                                   "runtime_identity":self.runtime_identity,
                                   "selector":self.selector_config if mode == "agent" else None})
        with self._lock:
            self._require_known_workers()
            prior = next((row for row in self._jobs.values() if row.get("request_identity") == request_identity),None)
            if prior is None:
                return self._register_new(source,params,config,mode,request_identity)
            if prior.get("operation_pending"):
                # The existing operation owns input verification. Deduplicate
                # in-flight requests without starting a second reader or job.
                return {"id":prior["id"],"status":self.status(prior["id"])["status"],"cached":True}
            if any(row.get("operation_pending") for row in self._jobs.values()):
                raise RuntimeError("Cached input verification waits for the active operation; inspect retained status meanwhile")
            future = self._executor.submit(self._verify_cached_input,prior["id"],source,params["data_sha256"])
        # Never hold the state lock while waiting on the single writer.
        future.result(timeout=min(self.parameters["stage_timeout_seconds"],self.run_timeout_seconds)+10)
        return {"id":prior["id"],"status":self.status(prior["id"])["status"],"cached":True}

    def _verify_cached_input(self, job, source, expected):
        from .runtime import verify_data_identity
        from devharness_core.watchdog import run_sandboxed, cancel_workers_on
        with self._lock:
            self._require_known_workers()
            self._update(job,cache_validation_pending=True)
        control = {"workers":[]}
        try:
            with cancel_workers_on(threading.Event()) as control:
                run_sandboxed(verify_data_identity,(self._input(source),expected),
                    timeout_sec=min(self.parameters["stage_timeout_seconds"],self.run_timeout_seconds),
                    max_mem_mb=self.parameters["memory_mb"],growth_window_seconds=self.parameters.get("growth_window_seconds",0))
        finally:
            stopped = all(row["cleanup"].get("verified_stopped") is True for row in control["workers"])
            self._update(job,cache_validation_pending=False,cache_worker_control=control,
                         cache_worker_liveness="stopped" if stopped else "unknown")
        if not stopped:
            raise RuntimeError("Cached-input worker liveness needs reconciliation")

    def _register_new(self, source, params, config, mode, request_identity):
        # Called while the owning controller holds its state lock.
        if len(self._jobs) >= 100:
            raise ValueError("Workspace job limit reached; use a fresh workspace")
        self._require_known_workers()
        job = uuid.uuid4().hex
        self._path("jobs", job).mkdir(mode=0o700)
        self._jobs[job] = {"id": job, "question": config["question"], "source": str(source),
            "status": "queued", "proposal_mode":mode, "request_identity":request_identity,
            "runtime_identity":_copy(self.runtime_identity),
            "plan_author": ("codex_agent" if self.selector_config["backend"] == "codex-cli" else "recorded_fixture")
                           if mode == "agent" else "operator_created_recorded_plan", "model_sessions_attempted": 0,
            "scientific_review": "not_performed_by_this_service", "completed_analysis": False}
        self._update(job)
        def prepare():
            from .workbench_proposals import prepare_workbench_proposal
            self._update(job, status="inspecting")
            # Recheck containment immediately before opening the external input.
            self._input(source)
            if mode == "agent":
                from .episode_planning import propose_analysis
                self._update(job, status="planning")
                result = propose_analysis(self._path("jobs", job, "planning"), question=config["question"],
                    dataset={"transport":"backed_workbench", "source":str(source), "parameters":params,
                             "declarations":config["declarations"], "run_timeout_seconds":self.run_timeout_seconds},
                    selector=self.proposal_selector, allow_data_read=True,
                    allow_model_execution=self.allow_model_execution, model_data_scope=self.model_data_scope)
                self._update(job, planning_result=result, model_sessions_attempted=result.get("model_sessions_attempted"),
                    tokens=result.get("tokens"), cost_usd=result.get("cost_usd"), actual_model=result.get("actual_model"))
                if result["status"] != "proposal_ready_for_review":
                    self._update(job, status=result["status"])
                    return
                receipt = result["proposal_receipt"]
                proposal = _read(self._path("jobs", job, "planning", "proposal", "proposal.json"))
            else:
                receipt = prepare_workbench_proposal(self._path("jobs", job, "proposal"), question=config["question"],
                    source=source, parameters=params, declarations=config["declarations"],
                    run_timeout_seconds=self.run_timeout_seconds, allow_data_read=True)
                proposal = _read(self._path("jobs", job, "proposal", "proposal.json"))
            self._update(job, status=proposal["status"], proposal_receipt=receipt, proposal=proposal)
        self._submit(job, prepare)
        return {"id": job, "status": "queued", "cached":False}

    def review(self, job, request):
        from .episode_proposals import _review_decision
        if not isinstance(request, dict) or set(request) != {"reviewer", "decision", "rationale", "confirmations"}:
            raise ValueError("Review fields are reviewer, decision, rationale and confirmations")
        _review_decision(request["decision"], request["reviewer"], request["rationale"], request["confirmations"], required_confirmations=CONFIRMATIONS)
        request = _copy(request)
        with self._lock:
            record = self._job(job)
            self._require_known_workers()
            if (record["status"] not in {"awaiting_operator_review", "needs_information"}
                    or "review_receipt" in record or not record.get("proposal_receipt") or record.get("operation_pending")):
                raise RuntimeError("This plan is not awaiting a first operator review; create a fresh plan for revisions")
            receipt = record["proposal_receipt"]
            self._update(job, status="recording_review")
            def review():
                from .workbench_proposals import record_workbench_review
                self._input(record["source"])
                result = record_workbench_review(self._path("jobs", job, "review"), self._manifest(job, "proposal", receipt),
                    expected_root=receipt["root_hash"], **request, allow_review=True)
                self._update(job, review_receipt=result, review=request,
                    status={"approve": "approved", "reject": "rejected", "needs_information": "needs_information"}[request["decision"]])
            self._submit(job, review)
            return {"id": job, "status": "recording_review"}

    def run(self, job):
        if not self.allow_data_execution:
            raise PermissionError("Analysis requires explicit server startup --allow-data-execution permission")
        with self._lock:
            record = self._job(job)
            self._require_known_workers()
            if record["status"] != "approved" or record.get("operation_pending"):
                raise RuntimeError("Execution requires an approved frozen plan and a fresh attempt")
            return self._execute(job, resume=False)

    def _execute(self, job, *, resume):
        with self._lock:
            record = self._job(job)
            proposal, review = record["proposal_receipt"], record["review_receipt"]
            remaining = self._remaining(record)
            if remaining <= 0:
                raise RuntimeError("Reviewed execution budget exhausted")
            attempt_number = len(record.get("execution_history", [])) + 1
            self._update(job, status="preparing")
            def execute():
                from devharness_core.watchdog import worker_limits
                from .workbench_proposals import prepare_reviewed_workbench, execute_reviewed_workbench
                self._input(record["source"])
                bundle = self._path("jobs", job, "bundle")
                output = self._path("jobs",job,"execution" if attempt_number==1 else f"execution-{attempt_number:04d}")
                start, result = time.monotonic(), None
                try:
                    with worker_limits(deadline=start+remaining, max_mem_mb=self.parameters["memory_mb"],
                        scratch_dir=self._path("jobs",job), growth_window_seconds=self.parameters.get("growth_window_seconds",0)):
                        if not resume:
                            prepare_reviewed_workbench(bundle, self._manifest(job, "proposal", proposal), self._manifest(job, "review", review),
                                expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
                        self._validate_bundle_input(job,bundle)
                        self._update(job, status="running")
                        result = execute_reviewed_workbench(bundle, output, allow_data_execution=True)
                        self._update(job, status=result["status"], execution=result,
                            completed_analysis=result["status"] in {"completed_review_passed", "completed_review_failed"})
                finally:
                    history = [*self._job(job).get("execution_history", []),
                        {"attempt":attempt_number, "path":str(output), "budget_seconds":remaining,
                         "elapsed_sec":time.monotonic()-start, "status":result["status"] if result else "interrupted_preparation",
                         "receipt":result.get("receipt") if result else None}]
                    self._update(job, execution_history=history)
            self._submit(job, execute)
            return {"id": job, "status": "preparing"}

    def cancel(self, job):
        with self._lock:
            record = self._job(job)
            if not self.status(job)["cancellable"]:
                raise RuntimeError("This job has no cancellable operation")
            event = self._cancellations.setdefault(job, threading.Event())
            event.set()
            future = self._futures.get(job)
            if future is None or future.cancel() or future.done():
                self._update(job,status="cancelled",worker_liveness="stopped",completed_analysis=False,operation_pending=False)
            else:
                self._update(job,status="cancel_requested")
            return {"id":job,"status":self._job(job)["status"]}

    def resume(self, job):
        if not self.allow_data_execution:
            raise PermissionError("Resume requires explicit server data-execution permission")
        with self._lock:
            self._require_known_workers()
            record = self._job(job)
            if not self._can_resume(record):
                raise RuntimeError("Resume requires a controlled stopped attempt, verified review binding and remaining budget")
            from .workbench_proposals import _approved, verify_workbench_review
            from .workbench import _frozen_configuration
            proposal, review = record["proposal_receipt"], record["review_receipt"]
            self._input(record["source"])
            _approved(self._manifest(job,"proposal",proposal),self._manifest(job,"review",review),
                      proposal["root_hash"],review["root_hash"])
            parameters,_ = _frozen_configuration(self._path("jobs",job,"bundle"))
            self._validate_bundle_input(job,self._path("jobs",job,"bundle"))
            binding = verify_workbench_review(self._path("jobs",job,"bundle"),parameters)
            if binding["proposal_root"] != proposal["root_hash"] or binding["review_root"] != review["root_hash"]:
                raise ValueError("Recovered bundle does not match this job's approved proposal")
            return self._execute(job,resume=True)

    def _validate_bundle_input(self, job, bundle):
        binding = _read(bundle/"bindings.json")
        if set(binding) != {"data"}:
            raise ValueError("Unexpected bundle input binding")
        actual = self._input(binding["data"])
        approved = self._input(self._job(job)["source"])
        if actual != approved:
            raise ValueError("Bundle data binding differs from the approved source")

    def _verified(self, job):
        from .bundles import verify_bundle
        record = self.status(job)
        if record["status"] not in {"completed_review_passed", "completed_review_failed"}:
            raise RuntimeError("Verified complete results are unavailable; inspect retained status/failure evidence")
        bundle = self._path("jobs", job, "bundle")
        verification = verify_bundle(bundle, record["execution"]["bundle_root"])
        return record, bundle, verification

    def figures(self, job):
        from .figure_catalog import figure_catalog
        from .figure_artifacts import load_figure
        record, bundle, _ = self._verified(job)
        manifest = bundle / "figures/manifest.json"
        figures = []
        if manifest.is_file():
            for entry in _read(manifest)["figures"]:
                figure, payload = load_figure(bundle, entry["figure_id"])
                figures.append({**figure, "labels": [label for panel in payload["panels"] for label in panel["labels"]]})
        with self._lock:
            revisions = [self.status(row["id"]) for row in self._jobs.values()
                         if row.get("operation_kind") == "presentation" and row.get("parent_job") == job]
        return {"parent_root": record["execution"]["bundle_root"], "parent_status": record["status"],
                "figures": figures, "revisions": revisions,
                "catalog": figure_catalog(available_families={row["family"] for row in figures}),
                "reason": None if figures else "presentation_revision_unavailable: legacy figure semantics are absent"}

    def revise_figure(self, job, request):
        from .figure_revisions import revision_request, prepare_figure_revision, execute_figure_revision
        from .bundles import pack_bundle
        from devharness_core.watchdog import worker_limits, _raise_if_cancelled
        if not isinstance(request, dict) or set(request) != {"figure_id", "expected_parent_root", "display_options", "author_label"}:
            raise ValueError("Use only the declared presentation request fields")
        with self._lock:
            self._require_known_workers()
            parent, bundle, _ = self._verified(job)
            if request["expected_parent_root"] != parent["execution"]["bundle_root"]:
                raise ValueError("presentation_parent_root_mismatch")
            normalized = revision_request(bundle, request["expected_parent_root"], request["figure_id"],
                                          request["display_options"], author_label=request["author_label"])
            for prior in self._jobs.values():
                if (prior.get("operation_kind") == "presentation" and prior.get("parent_job") == job
                        and prior.get("request_identity") == normalized["request_identity"]):
                    return {"id": prior["id"], "status": self.status(prior["id"])["status"], "cached": True}
            if len(self._jobs) >= 100:
                raise ValueError("Workspace job limit reached; use a fresh workspace")
            child = uuid.uuid4().hex
            self._path("jobs", child).mkdir(mode=0o700)
            self._jobs[child] = {"id": child, "question": "Presentation: " + normalized["figure_id"],
                "operation_kind": "presentation", "parent_job": job, "parent_root": normalized["parent_bundle_root"],
                "parent_status": parent["status"], "figure_id": normalized["figure_id"],
                "request_identity": normalized["request_identity"], "request": _copy(request),
                "runtime_identity": _copy(self.runtime_identity), "status": "queued", "completed_analysis": False,
                "model_sessions_attempted": 0, "scientific_review": "inherited_parent_context_not_new_review"}
            self._update(child)
            def render():
                started = time.monotonic()
                output = self._path("jobs", child, "bundle")
                try:
                    with worker_limits(deadline=started + min(120, self.run_timeout_seconds),
                                       max_mem_mb=min(1024, self.parameters["memory_mb"]),
                                       scratch_dir=self._path("jobs", child),
                                       growth_window_seconds=self.parameters.get("growth_window_seconds", 0)):
                        self._verified(job)
                        prepare_figure_revision(bundle, normalized["parent_bundle_root"], normalized["figure_id"],
                            normalized["display_options"], output, author_label=normalized["author_label"])
                        self._update(child, status="running")
                        result = execute_figure_revision(output)
                        _raise_if_cancelled()
                        archive = pack_bundle(output, self._path("jobs", child, "presentation.zip"), result["manifest_sha256"])
                        _raise_if_cancelled()
                        self._update(child, status="completed_presentation", execution={**result, **archive},
                                     completed_analysis=False)
                finally:
                    self._update(child, execution_history=[{"attempt": 1, "elapsed_sec": time.monotonic()-started,
                                                          "operation_kind": "presentation"}])
            self._submit(child, render)
            return {"id": child, "status": "queued", "cached": False}

    def figure_revision(self, job, revision):
        self._verified(job)
        record = self.status(revision)
        if record.get("operation_kind") != "presentation" or record.get("parent_job") != job:
            raise ValueError("Revision does not belong to this analysis")
        return record

    def download_figure_revision(self, job, revision, name):
        from .bundles import verify_bundle
        from .figure_artifacts import load_figure
        record = self.figure_revision(job, revision)
        if record["status"] != "completed_presentation":
            raise RuntimeError("Completed presentation is unavailable; inspect retained failure/cancellation")
        bundle = self._path("jobs", revision, "bundle")
        verify_bundle(bundle, record["execution"]["manifest_sha256"])
        if name == "archive":
            path = self._path("jobs", revision, "presentation.zip")
            if sha256(path) != record["execution"]["archive_sha256"]:
                raise ValueError("Presentation archive identity mismatch")
            return path, "application/zip", "presentation-evidence.zip"
        if name == "image":
            figure, _ = load_figure(bundle, record["figure_id"])
            path = self._path("jobs", revision, "bundle", figure["image"]["path"])
            return path, "image/png", path.name
        raise ValueError("Unknown presentation download")

    @staticmethod
    def _allowed_artifact(name):
        return name in DOWNLOADS or bool(re.fullmatch(r"figures/[A-Za-z0-9_-]+\.(png|svg|html|json)", name))

    def results(self, job):
        _, bundle, verification = self._verified(job)
        manifest = _read(bundle / "receipts.json")
        names = [name for name in manifest["artifacts"] if self._allowed_artifact(name)]
        return {"verification": verification, "report": _read(bundle / "report.json"),
            "deterministic_review": _read(bundle / "reviewer-report.json"),
            "scientific_review": "not_performed_by_this_service",
            "artifacts": [{"name": name, "bytes": (bundle / name).stat().st_size} for name in sorted(names)],
            "archive_available": True}

    def download(self, job, name):
        record, bundle, _ = self._verified(job)
        if name == "archive":
            path = self._path("jobs", job, "bundle.bundle.zip")
            if str(path) != record["execution"]["archive"] or sha256(path) != record["execution"]["native_result"]["archive_sha256"]:
                raise ValueError("Export archive identity mismatch")
            return path, "application/zip", "scRNA-evidence.bundle.zip"
        manifest = _read(bundle / "receipts.json")
        if not self._allowed_artifact(name) or name not in manifest["artifacts"]:
            raise ValueError("Artifact is not in the permitted verified result routes")
        path = self._path("jobs", job, "bundle", name)
        return path, mimetypes.guess_type(name)[0] or "application/octet-stream", path.name

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=False)
        self._release_lock()


def make_server(app, *, port=8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ResearchWorkbench"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass  # Requests and session tokens are never logged.

        def _headers(self, code, content_type, size, *, nonce=None, filename=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            csp = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            if nonce:
                csp += f"; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; img-src 'self' blob: data:"
            self.send_header("Content-Security-Policy", csp)
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="' + filename + '"')
            self.end_headers()

        def _json(self, value, code=200):
            body = json.dumps(value, allow_nan=False).encode()
            self._headers(code, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _boundary(self, *, api=False, mutation=False):
            host = "127.0.0.1:" + str(self.server.server_address[1])
            origin = "http://" + host
            if self.headers.get_all("Host") != [host]:
                raise PermissionError("Invalid local Host")
            origins = self.headers.get_all("Origin") or []
            if (origins and origins != [origin]) or (mutation and origins != [origin]):
                raise PermissionError("Invalid or missing same-origin request")
            if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
                raise PermissionError("Cross-site requests are unavailable")
            tokens = self.headers.get_all("X-Workbench-Token") or []
            if api and (len(tokens) != 1 or not hmac.compare_digest(tokens[0], app.token)):
                raise PermissionError("A valid service session token is required")

        def _dispatch(self, mutation=False):
            try:
                self._boundary(api=urlsplit(self.path).path != "/", mutation=mutation)
                target = urlsplit(self.path)
                if target.scheme or target.netloc or target.fragment:
                    raise ValueError("Use an allowed local route")
                if not mutation and target.path == "/":
                    if target.query:
                        query = parse_qs(target.query, strict_parsing=True)
                        if set(query) != {"object_type", "object_id", "version_id"} or any(len(items) != 1 for items in query.values()):
                            raise ValueError("Use an exact scientific-version page link")
                    nonce = secrets.token_urlsafe(24)
                    body = ASSET.read_text().replace("__NONCE__", nonce).encode()
                    self._headers(200, "text/html; charset=utf-8", len(body), nonce=nonce)
                    self.wfile.write(body)
                    return
                body = None
                if mutation:
                    lengths = self.headers.get_all("Content-Length") or []
                    if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
                        raise ValueError("Use one bounded Content-Length, without transfer encoding")
                    size = int(lengths[0])
                    if size > MAX_BODY:
                        raise ValueError("JSON size exceeds the request bound")
                    if self.headers.get("Content-Type") != "application/json":
                        raise ValueError("Use application/json")
                    body = decode_json(self.rfile.read(size))
                if not target.query and not mutation and target.path == "/api/config":
                    return self._json(app.configuration())
                if not target.query and target.path == "/api/workspace":
                    return self._json(app.workspace_state(body) if mutation else app.workspace_state())
                if target.path.startswith("/api/scientific"):
                    if app.scientific is None:
                        raise KeyError("Scientific workspace is not enabled for this service")
                    if target.path == "/api/scientific" and not target.query:
                        return self._json(app.scientific.action(body) if mutation else app.scientific.catalog())
                    if target.path == "/api/scientific/resolve" and not mutation:
                        query = parse_qs(target.query, strict_parsing=True)
                        if set(query) != {"object_type", "object_id", "version_id"} or any(len(items) != 1 for items in query.values()):
                            raise ValueError("Use an exact typed scientific reference")
                        return self._json(app.scientific.resolve({key: items[0] for key, items in query.items()}))
                    artifact_route = re.fullmatch(r"/api/scientific/artifacts/([0-9a-f]{64})", target.path)
                    method_route = re.fullmatch(r"/api/scientific/methods/([0-9a-f]{64})", target.path)
                    if method_route and not mutation and not target.query:
                        return self._json(app.scientific.method_preview(method_route[1]))
                    if artifact_route and not mutation and not target.query:
                        path, content_type, filename = app.scientific.download(artifact_route[1])
                        with path.open("rb") as source:
                            self._headers(200, content_type, path.stat().st_size, filename=filename)
                            shutil.copyfileobj(source, self.wfile, 64 * 1024)
                        return
                    raise KeyError("Unknown scientific resource or action")
                if not target.query and target.path == "/api/jobs":
                    return self._json(app.register(body), 202) if mutation else self._json({"jobs": app.jobs()})
                figure_route = re.fullmatch(r"/api/jobs/([0-9a-f]{32})/(figures|figure-revisions)(?:/([0-9a-f]{32})(?:/(archive|image|cancel))?)?", target.path)
                if figure_route and not target.query:
                    job, action, revision, artifact = figure_route.groups()
                    if action == "figures" and not mutation and revision is None:
                        return self._json(app.figures(job))
                    if action == "figure-revisions":
                        if mutation and revision is None:
                            return self._json(app.revise_figure(job, body), 202)
                        if revision is not None:
                            if mutation and artifact == "cancel" and body == {}:
                                app.figure_revision(job, revision)
                                return self._json(app.cancel(revision), 202)
                            if not mutation and artifact is None:
                                return self._json(app.figure_revision(job, revision))
                            if not mutation and artifact in {"archive", "image"}:
                                path, content_type, filename = app.download_figure_revision(job, revision, artifact)
                                with path.open("rb") as source:
                                    self._headers(200, content_type, path.stat().st_size, filename=filename)
                                    shutil.copyfileobj(source, self.wfile, 64 * 1024)
                                return
                match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})(?:/(review|run|cancel|resume|results|artifact|archive))?", target.path)
                if match:
                    job, action = match.groups()
                    if mutation and not target.query and action == "review":
                        return self._json(app.review(job, body), 202)
                    if mutation and not target.query and action == "run" and body == {}:
                        return self._json(app.run(job), 202)
                    if mutation and not target.query and action in {"cancel","resume"} and body == {}:
                        return self._json(getattr(app,action)(job),202)
                    if not mutation and not target.query and action in {None, "results"}:
                        return self._json(app.status(job) if action is None else app.results(job))
                    if not mutation and action in {"artifact", "archive"}:
                        query = parse_qs(target.query, strict_parsing=True)
                        if (action == "archive" and query) or (action == "artifact" and
                                (set(query) != {"name"} or len(query["name"]) != 1 or len(query["name"][0]) > 150)):
                            raise ValueError("Invalid artifact request")
                        name = "archive" if action == "archive" else query["name"][0]
                        path, content_type, filename = app.download(job, name)
                        with path.open("rb") as source:
                            self._headers(200, content_type, path.stat().st_size, filename=filename)
                            shutil.copyfileobj(source, self.wfile, 64 * 1024)
                        return
                raise KeyError("Unknown or unavailable route")
            except (ValueError, PermissionError, KeyError, RuntimeError, OSError) as error:
                code = 403 if isinstance(error, PermissionError) else 404 if isinstance(error, KeyError) else 409 if isinstance(error, RuntimeError) else 400
                self.close_connection = True
                self._json({"error": type(error).__name__, "message": str(error)[:2000]}, code)

        def do_GET(self):
            self._dispatch()

        def do_POST(self):
            self._dispatch(mutation=True)

    return HTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, help="Existing workbench parameter overrides, fixed for this workspace")
    parser.add_argument("--run-timeout-seconds", type=int, default=3600)
    parser.add_argument("--allow-data-execution", action="store_true")
    parser.add_argument("--restore-view-state-backup", action="store_true",
        help="Explicitly recover missing/invalid local view metadata from its previous valid snapshot; does not restore jobs or permissions")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--codex-model", help="Configure the existing Codex proposal selector; does not authorize model calls")
    selection.add_argument("--recorded-plan-answer", type=Path, help="Explicit offline fixture decision, visibly labeled; never live model evidence")
    parser.add_argument("--agent-workspaces", type=Path, help="Disjoint protected actor workspace parent for Codex")
    parser.add_argument("--allow-model-execution", action="store_true", help="Explicitly permit the configured Codex selector and its mandatory preflight")
    parser.add_argument("--model-data-scope", choices=("local_only", "synthetic", "public"), default="local_only")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--enable-scientific", action="store_true", help="Expose shared scientific metadata; initialization remains an explicit authenticated action")
    parser.add_argument("--scientific-workspace", type=Path, help="Existing method workspace to explicitly bind to this local project")
    parser.add_argument("--recorded-method-decisions", type=Path, help="Explicit offline method-planner responses; never live model evidence")
    parser.add_argument("--restore-scientific-backup", type=Path,
        help="Explicitly recover invalid scientific metadata from its validated retained backup; never restores execution grants or launches work")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    from .episode_planning import RecordedPlanningSelector
    selector = RecordedPlanningSelector(_read(args.recorded_plan_answer)) if args.recorded_plan_answer else None
    method_selector = None
    if args.recorded_method_decisions:
        from .method_planner_cli import RecordedMethodSelector
        method_selector = RecordedMethodSelector(_read(args.recorded_method_decisions)["responses"])
    app = WorkbenchService(args.workspace, args.input_root,
        parameter_overrides=_read(args.parameters) if args.parameters else None,
        run_timeout_seconds=args.run_timeout_seconds, allow_data_execution=args.allow_data_execution,
        proposal_selector=selector, codex_model=args.codex_model, agent_workspaces=args.agent_workspaces,
        allow_model_execution=args.allow_model_execution, model_data_scope=args.model_data_scope,
        restore_view_state_backup=args.restore_view_state_backup, enable_scientific=args.enable_scientific,
        scientific_workspace=args.scientific_workspace, method_selector=method_selector,
        restore_scientific_backup=args.restore_scientific_backup)
    server = make_server(app, port=args.port)
    print(f"Open http://127.0.0.1:{server.server_address[1]}/#{app.token}", flush=True)
    print("Local scRNA research interface; " + app.configuration()["proposal_selection"]["label"]
          + ". Ctrl+C stops accepting requests.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Closing local interface; any active supervised attempt retains its existing deadline.", flush=True)
    finally:
        server.server_close()
        app.close()


if __name__ == "__main__":
    main()
