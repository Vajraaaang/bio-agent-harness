"""Authenticated one-use local control, with every live route still disabled.

The issuer keys, clock, current-runtime verifier and fixed protected ledger are
trusted coordinator dependencies. They are never reconstructed from request
JSON. This control does not establish an OS participant boundary or qualify a
model backend. Uncertain dispatches retain their preventive reservations.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import base64
import csv
import hashlib
import importlib.metadata
import io
from email.parser import BytesParser
import math
from pathlib import Path
import sqlite3
import stat
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.requirements import Requirement
from devharness_core.provenance import canonical_json
from devbench.study.evidence import _read, decode_json
from devbench.study.permissions import require_protected_storage

from .checkpoints import digest
from .episode_proposals import _absolute
from .rna_scientific_trial_contract import (
    FORMAT_V3, bounded_source, trial_binding, trial_purpose_allocation, validate_approval, validate_trial_request,
)

_IDENTITY = ("route", "source_digest", "runtime_digest", "backend_digest", "model", "platform")
_SIGNEDIN_IDENTITY = _IDENTITY + ("client", "authentication", "budget_semantics", "qualified_boundary")
_ALLOCATION = {"total_tokens", "requests", "memory_mb", "seconds", "storage_bytes", "max_runs"}
_SUMMED = _ALLOCATION - {"memory_mb"}
_GRANT_FIELDS = {"format", "issuer_id", "grant_id", "trial_id", "purpose", "binding_sha256", "approver_id",
    "roles_sha256", "allocation_sha256", "engineering_sha256", "expires_at", "signature"}
_CUSTODY_PATHS = (
    'devbench/analysis/rna_trial_authority.py',
    'devbench/analysis/rna_scientific_trial.py',
    'devbench/analysis/rna_scientific_trial_contract.py',
    'devbench/analysis/method_planner.py',
    'devbench/analysis/method_planner_contract.py',
    'devbench/analysis/method_authoring_contract.py',
    'devbench/analysis/methods.py',
    'devbench/analysis/method_executor.py',
    'devbench/analysis/method_validation.py',
    'devbench/analysis/workflow_operations.py',
    'devbench/analysis/workflow_entrypoints.py',
    'devbench/analysis/workflow_actions.py',
    'devbench/analysis/workflow_state.py',
    'devbench/analysis/langgraph_workflow.py',
    'devbench/analysis/langgraph_investigations.py',
    'devbench/analysis/scientific_workspace.py',
    'devbench/analysis/method_planner_cli.py',
    'devbench/analysis/workflow_science.py',
    'devbench/analysis/workflow_discovery.py',
    'devbench/analysis/workflow_scientific_reviews.py',
    'devbench/analysis/workflow_implementation.py',
    'devbench/analysis/scientific_review_agent.py',
    'devbench/analysis/scientific_review.py',
    'devbench/analysis/scientific_judgment.py',
    'devbench/analysis/method_sources.py',
    'devbench/analysis/rna_investigation_backend.py',
    'devbench/analysis/checkpoints.py',
    'devbench/analysis/episode_proposals.py',
    'devbench/analysis/episode_agent.py',
    'devbench/analysis/rna_input_contract.py',
    'devbench/analysis/rna_publication_profiles.py',
    'devbench/analysis/rna_execution_inputs.py',
    'devbench/analysis/rna_comparison_outputs.py',
    'devbench/analysis/rna_publication_comparison.py',
    'devbench/analysis/rna_publication_metrics.py',
    'devbench/analysis/rna_publication_evidence.py',
    'devbench/analysis/rna_reference_extraction.py',
    'devbench/analysis/rna_publication_report.py',
    'devbench/analysis/rna_mediated_backend.py',
    'devbench/analysis/rna_route_qualification.py',
    'devbench/analysis/episode_codex.py',
    'devbench/analysis/figure_artifacts.py',
    'devbench/analysis/rna_mediated_observability.py',
    'devbench/analysis/rna_trace_receipts.py',
    'devbench/analysis/rna_trace_contract.py',
    'devbench/analysis/rna_trace_projection.py',
    'devbench/analysis/codex_signedin.py',
    'devbench/analysis/codex_job_policy.py',
    'devbench/analysis/codex_job_trace.py',
    'devbench/study/evidence.py',
    'devbench/study/evidence_keys.py',
    'devbench/study/permissions.py',
    'devbench/study/contracts.py',
    'devbench/study/rna_hosted_tls.py',
    'devharness_core/provenance.py',
    'devharness_core/methods.py',
    'devharness_core/storage.py',
    'devharness_core/watchdog.py',
    'devharness_server/method_adapter.py',
    'devharness_server/dispatcher.py',
    'devbench/analysis/codex_isolated_binding.py',
    'devbench/analysis/codex_isolated_launch.py',
    'devbench/study/cli_identity.py',
    'devbench/analysis/rna_public_execution_qualification.py',
    'devbench/analysis/rna_retained_figures.py',
    'devbench/analysis/rna_airway_native_outputs.py',
    'devbench/analysis/rna_grant_signing.py',
    'devbench/analysis/rna_approval_setup.py',
    'devharness_controlled/native_runtime.py',
    'devbench/analysis/colab_qualification_contract.py',
    'devbench/analysis/colab_execution_authority.py',
    'devbench/analysis/colab_execution_contract.py',
    'devbench/analysis/colab_result_import.py',
    'devbench/analysis/colab_job.py',
    'devbench/analysis/colab_transport.py',
    'devbench/analysis/colab_remote_worker.py',
    'devbench/analysis/colab_bundle_helpers.py',
    'devbench/analysis/colab_workflow.py',
    'devbench/analysis/workflow_protocol_qualification.py',
    'devbench/analysis/workflow_candidate_freeze.py',
    'devbench/analysis/workbench.py',
    'devbench/analysis/workbench_proposals.py',
    'devbench/analysis/scientific_objects.py',
    'devbench/analysis/resources.py',
    'devbench/analysis/method_planner_recovery.py',
)
_SCHEMA_SQL = {
    "grants": "CREATE TABLE grants (issuer_id TEXT NOT NULL, grant_id TEXT NOT NULL, purpose TEXT NOT NULL, "
        "binding_sha256 TEXT NOT NULL, grant_json TEXT NOT NULL, grant_sha256 TEXT NOT NULL, "
        "PRIMARY KEY(issuer_id,grant_id), UNIQUE(purpose,binding_sha256))",
    "dispatches": "CREATE TABLE dispatches (issuer_id TEXT NOT NULL, grant_id TEXT NOT NULL, dispatch_id TEXT NOT NULL, "
        "allocation_json TEXT NOT NULL, allocation_sha256 TEXT NOT NULL, PRIMARY KEY(issuer_id,grant_id,dispatch_id), "
        "FOREIGN KEY(issuer_id,grant_id) REFERENCES grants(issuer_id,grant_id))",
}


def verify_installed_distribution_custody(distribution, *, runtime_root, required_paths=None):
    """Verify ordinary RECORD rows against actual files, never editable source labels."""
    runtime_root = _absolute(runtime_root)
    record_files = [value for value in distribution.files or () if str(value).endswith(".dist-info/RECORD")]
    if len(record_files) != 1:
        raise PermissionError("Installed RECORD custody is unavailable")
    record_path = _absolute(distribution.locate_file(record_files[0]))
    if not record_path.is_relative_to(runtime_root):
        raise PermissionError("Installed distribution RECORD lies outside its actual runtime")
    record_body = _read(record_path, 4_000_000)
    direct_path = record_path.parent / "direct_url.json"
    if direct_path.exists() and decode_json(_read(_absolute(direct_path), 100_000)).get("dir_info", {}).get("editable"):
        raise PermissionError("Editable installed distributions cannot qualify runtime custody")
    rows, actual, names = list(csv.reader(io.StringIO(record_body.decode()))), {}, set()
    if len(rows) > 100_000:
        raise PermissionError("Installed RECORD exceeds its custody bound")
    required = None if required_paths is None else set(required_paths)
    for row in rows:
        if len(row) != 3 or row[0] in names:
            raise PermissionError("Installed RECORD contains malformed or duplicate custody rows")
        relative, encoded, size = row
        names.add(relative)
        if not encoded:
            if not relative.endswith((".dist-info/RECORD", ".pyc")):
                raise PermissionError("Installed RECORD contains an unverified file")
            continue
        if not encoded.startswith("sha256="):
            raise PermissionError("Installed RECORD uses unsupported digest custody")
        located = Path(distribution.locate_file(relative))
        if Path(relative).is_absolute() or any(part.is_symlink() for part in (located, *located.parents)):
            raise PermissionError("Installed RECORD custody cannot escape through an absolute path or symlink")
        path = _absolute(located.resolve())
        if not path.is_relative_to(runtime_root):
            raise PermissionError("Installed RECORD file lies outside the actual runtime")
        try:
            expected = base64.urlsafe_b64decode(encoded.split("=", 1)[1] + "===").hex()
            content = _read(path, 200_000_000)
            length = int(size)
        except (ValueError, OSError) as error:
            raise PermissionError("Installed RECORD byte custody is unavailable") from error
        if len(content) != length or hashlib.sha256(content).hexdigest() != expected:
            raise PermissionError("Actual installed bytes differ from their RECORD digest custody")
        actual[relative] = expected
    if required is not None and not required <= set(actual):
        raise PermissionError("Installed RECORD does not cover every required enforcement helper")
    metadata = BytesParser().parsebytes(_read(_absolute(record_path.parent / "METADATA"), 1_000_000))
    return {"name": metadata["Name"], "version": metadata["Version"],
        "record_sha256": hashlib.sha256(record_body).hexdigest(), "file_count": len(actual), "files_sha256": digest(actual),
        "requires": metadata.get_all("Requires-Dist", [])}


def verify_trial_runtime_dependencies(seeds, *, runtime_root, distribution_resolver=None):
    """Verify the active installed dependency closure, including separate wheels."""
    resolver = distribution_resolver or importlib.metadata.distribution
    pending, verified, extras = list(seeds), {}, {}
    while pending:
        requested = pending.pop()
        name = requested.lower().replace("_", "-").replace(".", "-")
        if name in verified:
            continue
        if len(verified) >= 128:
            raise PermissionError("Runtime dependency closure exceeds its bound")
        try:
            distribution = resolver(name)
            custody = verify_installed_distribution_custody(distribution, runtime_root=runtime_root)
        except (importlib.metadata.PackageNotFoundError, KeyError) as error:
            raise PermissionError("An active installed runtime dependency is unavailable") from error
        verified[name] = custody
        for text in custody["requires"]:
            requirement = Requirement(text)
            active = requirement.marker is None or any(requirement.marker.evaluate({"extra": value})
                for value in ({""} | extras.get(name, set())))
            if not active:
                continue
            child = requirement.name.lower().replace("_", "-").replace(".", "-")
            if requirement.extras:
                added = set(requirement.extras) - extras.get(child, set())
                extras.setdefault(child, set()).update(requirement.extras)
                if added and child in verified:
                    verified.pop(child)  # Re-evaluate newly requested optional runtime edges.
            pending.append(child)
    return verified


def trial_enforcement_source_digest():
    """Bind the actual installed enforcement bytes, including this control."""
    root = Path(__file__).resolve().parents[2]
    actual = {name: hashlib.sha256(_read(root / name, 1_000_000)).hexdigest() for name in _CUSTODY_PATHS}
    distribution = importlib.metadata.distribution("devharness")
    custody = verify_installed_distribution_custody(distribution, runtime_root=Path(sys.prefix).resolve(), required_paths=_CUSTODY_PATHS)
    if any(Path(distribution.locate_file(name)).resolve() != root / name for name in _CUSTODY_PATHS):
        raise PermissionError("Actual enforcement helper origin differs from the installed distribution")
    return digest({"control_files": actual, "ordinary_distribution_custody": custody})


def trial_enforcement_runtime_digest():
    """Interpreter and active base product dependencies plus explicit control custody.

    Optional scientific/renderer extras require their own selected-path
    qualification; this digest never admits an unavailable live route.
    """
    executable = Path(sys.executable).resolve()
    custody = verify_trial_runtime_dependencies(("devharness", "cryptography", "jsonschema", "referencing", "numpy", "packaging"),
        runtime_root=Path(sys.prefix).resolve())
    return digest({"executable": str(executable), "executable_sha256": hashlib.sha256(_read(executable, 64_000_000)).hexdigest(),
        "version": sys.version, "prefix": str(Path(sys.prefix).resolve()), "dependencies": custody})


class AuthenticatedTrialAuthority:
    """Trusted injected authority for offline fixtures; no provider/native default.

    `runtime_verifier(context)` must inspect current trusted runtime objects and return
    the six exact identity fields. Returning request declarations is not a
    verifier. Offline fixtures make no model-isolation or scientific claim.
    Signed issuer grants authenticate their exact subject and operator, while
    an atomic durable ledger enforces global issuer/grant and binding one-use.
    """
    def __init__(self, path, *, workspace, issuer_keys, runtime_verifier, clock=None,
                 mediated_backend=None, execution_qualification=None, signedin_backend=None,
                 colab_qualification_context=None):
        self.workspace, self.path = _absolute(workspace), _absolute(path)
        if not self.path.is_relative_to(self.workspace):
            raise ValueError("Trusted trial ledger must be inside the assigned workspace")
        require_protected_storage(self.path)
        if (type(issuer_keys) is not dict or not issuer_keys or len(issuer_keys) > 16
                or any(type(key) is not str or not key.strip() or len(key) > 200
                       or not isinstance(value, Ed25519PublicKey) for key, value in issuer_keys.items())):
            raise TypeError("Trusted issuer public keys are required")
        if not callable(runtime_verifier) or clock is not None and not callable(clock):
            raise TypeError("Trusted current runtime verifier and clock are required")
        self.issuer_keys = dict(issuer_keys)
        self.runtime_verifier = runtime_verifier
        self.mediated_backend = mediated_backend
        self.signedin_backend = signedin_backend
        self.execution_qualification = execution_qualification
        self.colab_qualification_context = colab_qualification_context
        self._runtime_context = None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._inode = None
        self._schema_ready = False
        existing = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if existing:
            self._check_file()
            if self.path.stat().st_size == 0:
                raise PermissionError("Trusted trial ledger is empty or invalid")
        with self._database() as db:
            if existing:
                self._check_schema(db)
            else:
                db.execute("BEGIN IMMEDIATE")
                for sql in _SCHEMA_SQL.values():
                    db.execute(sql)
                db.execute("PRAGMA user_version=1")
                self._check_schema(db)
        self._schema_ready = True
        self.path.chmod(0o600)

    def bind_runtime_context(self, context):
        """Supply actual current coordinator objects, never a JSON projection."""
        if type(context) is not dict or set(context) - {"planner", "selector", "executor", "actor", "scientific_context", "backend"} or "planner" not in context or "actor" not in context:
            raise ValueError("Use the actual bounded coordinator runtime context")
        self._runtime_context = dict(context)  # Keep object identity; do not serialize/deepcopy the objects.

    def _check_schema(self, db):
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise PermissionError("Unsupported trusted trial ledger schema")
        tables = {name: sql for name, sql in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'")}
        if tables != _SCHEMA_SQL or db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view')").fetchone() is not None:
            raise PermissionError("Trusted trial ledger exact schema/constraints differ")
        for table, expected in {"grants": {("issuer_id", "grant_id"), ("purpose", "binding_sha256")},
                "dispatches": {("issuer_id", "grant_id", "dispatch_id")}}.items():
            indexes = db.execute("PRAGMA index_list(" + table + ")").fetchall()
            if any(not row[2] or row[4] or not row[1].startswith("sqlite_autoindex_" + table + "_") for row in indexes):
                raise PermissionError("Trusted trial ledger unique constraints differ")
            actual = {tuple(row[2] for row in db.execute("PRAGMA index_info(" + index[1] + ")")) for index in indexes}
            if actual != expected:
                raise PermissionError("Trusted trial ledger replay constraints differ")
        if db.execute("PRAGMA foreign_key_check").fetchall() or db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise PermissionError("Trusted trial ledger integrity differs")

    def _check_file(self):
        _absolute(self.path)
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("Trusted trial ledger must be a single-linked regular file")
        identity = (info.st_dev, info.st_ino)
        if self._inode is not None and identity != self._inode:
            raise PermissionError("Trusted trial ledger resource identity changed")
        self._inode = identity

    @contextmanager
    def _database(self):
        db = None
        try:
            if self._inode is not None:
                self._check_file()
            db = sqlite3.connect(str(self.path), timeout=0)
            self._check_file()
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                if self._schema_ready:
                    self._check_schema(db)
                yield db
                if self._schema_ready:
                    self._check_schema(db)
        except sqlite3.IntegrityError as error:
            raise PermissionError("Trusted trial grant or dispatch already consumed; replay denied") from error
        except sqlite3.DatabaseError as error:
            raise PermissionError("Trusted trial ledger unavailable; no dispatch") from error
        finally:
            if db is not None:
                db.close()

    def _validated(self, request, *, purpose, binding_sha256):
        request = validate_trial_request(request)
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise PermissionError("Trusted current approval time is unavailable")
        approval = validate_approval(request, purpose, now=now)
        if binding_sha256 != trial_binding(request, purpose):
            raise PermissionError("Authenticated grant subject binding differs")
        route = request["engineering"]["route"]
        if route == "mediated":
            from .rna_mediated_backend import MediatedInvestigationBackend
            if (type(self.mediated_backend) is not MediatedInvestigationBackend or self._runtime_context is None
                    or self._runtime_context.get("backend") is not self.mediated_backend
                    or self._runtime_context["planner"].methods is not self.mediated_backend.methods
                    or request["format"] != "devbench.rna-scientific-trial/2"
                    or request["dataset"]["classification"] != "synthetic"):
                raise PermissionError("Mediated route is unavailable without the exact accepted synthetic runtime")
            backend = self.mediated_backend
            capability = backend.require_admission("hard_total_tokens")
            if backend.qualification.reference not in request["engineering"]["qualification_refs"]:
                raise PermissionError("Accepted backend qualification differs from the signed trial evidence")
            policy, allocation = backend.policy, request["allocation"]
            if (policy["max_total_tokens"] > allocation["total_tokens"] or policy["max_calls"] > allocation["requests"]
                    or policy["max_total_seconds"] > allocation["seconds"] or policy["memory_mb"] > allocation["memory_mb"]):
                raise PermissionError("Mediated campaign exceeds the authenticated aggregate allocation")
            from decimal import Decimal
            ceiling = allocation["cost_ceiling"]
            if ceiling is None or capability["accepted_qualification"]["max_total_cost_microusd"] > Decimal(str(ceiling)) * 1_000_000:
                raise PermissionError("Mediated admission requires the authenticated conservative cost ceiling")
            if backend.runtime_identity() != {key: request["engineering"][key] for key in _IDENTITY}:
                raise PermissionError("Current mediated runtime differs from the signed subject")
            if purpose == "trial_execution":
                from .rna_route_qualification import TrustedExecutionQualification
                if type(self.execution_qualification) is not TrustedExecutionQualification:
                    raise PermissionError("Mediated execution requires accepted exact scientific adapter qualification")
                self.execution_qualification.verify(request, methods=backend.methods,
                    planner=self._runtime_context["planner"], purpose=purpose, runtime_context=self._runtime_context)
        elif route == "codex_chatgpt":
            from .codex_signedin import SignedInCodexActionSelector, SignedInCodexInvestigationBackend
            backend = self.signedin_backend
            if (type(backend) is not SignedInCodexInvestigationBackend or self._runtime_context is None
                    or self._runtime_context.get("backend") is not backend
                    or self._runtime_context["planner"].methods is not backend.methods
                    or request["format"] != FORMAT_V3
                    or request["dataset"]["classification"] not in {"synthetic", "public"}):
                raise PermissionError("Signed-in route requires the exact registered qualified runtime")
            if purpose == "workflow_generation" and (type(self._runtime_context.get("selector")) is not SignedInCodexActionSelector
                    or self._runtime_context["selector"] is not backend.selector):
                raise PermissionError("Signed-in route requires the exact registered selector")
            capability = backend.require_admission("generation_jobs")
            boundary = request["engineering"]["qualified_boundary"]
            if (capability.get("scope") != "native" or capability.get("native_boundary_qualified") is not True
                    or boundary is None or backend.qualification is None
                    or backend.qualification.reference != boundary
                    or boundary not in request["engineering"]["qualification_refs"]):
                raise PermissionError("Signed-in native boundary qualification is unavailable")
            bounded_source(boundary, self.workspace)
            policy, allocation = backend.policy, request["allocation"]["generation"]
            if (policy["max_jobs"] > allocation["jobs"] or policy["max_total_seconds"] > allocation["seconds"]
                    or policy["max_context_bytes"] > allocation["context_bytes"]
                    or policy["max_output_bytes"] > allocation["output_bytes"]
                    or policy["max_concurrent_jobs"] != 1 or policy["max_children"] != 0):
                raise PermissionError("Signed-in campaign exceeds its authenticated generation-job allocation")
            if backend.runtime_identity() != {key: request["engineering"][key] for key in _SIGNEDIN_IDENTITY}:
                raise PermissionError("Current signed-in runtime differs from the signed subject")
            if purpose == "trial_execution":
                from .rna_route_qualification import TrustedExecutionQualification
                if type(self.execution_qualification) is not TrustedExecutionQualification:
                    raise PermissionError("Signed-in route execution requires separate exact scientific adapter qualification")
                self.execution_qualification.verify(request, methods=backend.methods,
                    planner=self._runtime_context["planner"], purpose=purpose, runtime_context=self._runtime_context)
        elif route != "offline_fixture":
            raise PermissionError("Live route admission remains disabled; only offline fixture control is supported")
        current = self.runtime_verifier(self._runtime_context)
        fields = _SIGNEDIN_IDENTITY if request["format"] == FORMAT_V3 else _IDENTITY
        if type(current) is not dict or set(current) != set(fields):
            raise PermissionError("Trusted actual runtime identity is unavailable")
        expected = {key: request["engineering"][key] for key in fields}
        if (current != expected or expected["source_digest"] != trial_enforcement_source_digest()
                or expected["runtime_digest"] != trial_enforcement_runtime_digest()):
            raise PermissionError("Actual source/runtime/backend identity drift; no dispatch")
        grant = decode_json(bounded_source(approval["evidence"], self.workspace))
        if type(grant) is not dict or set(grant) != _GRANT_FIELDS or grant["format"] != "devbench.rna-trial-authority-grant/1":
            raise PermissionError("An authenticated closed issuer grant is required")
        if type(grant["issuer_id"]) is not str or len(grant["issuer_id"]) > 200:
            raise PermissionError("Authenticated grant issuer is invalid")
        key = self.issuer_keys.get(grant["issuer_id"])
        if key is None:
            raise PermissionError("Authenticated grant issuer is unknown")
        if type(grant["grant_id"]) is not str or not grant["grant_id"].strip() or len(grant["grant_id"]) > 200:
            raise PermissionError("Authenticated grant identity is invalid")
        signed = {key: value for key, value in grant.items() if key != "signature"}
        try:
            if type(grant["signature"]) is not str or len(grant["signature"]) != 128:
                raise ValueError("Wrong signature length")
            signature = bytes.fromhex(grant["signature"])
            if len(signature) != 64:
                raise ValueError("Wrong signature length")
            key.verify(signature, canonical_json(signed))
        except (InvalidSignature, ValueError, TypeError) as error:
            raise PermissionError("Authenticated grant signature is invalid") from error
        for field, value in {"trial_id": request["trial_id"], "purpose": purpose, "binding_sha256": binding_sha256,
                "approver_id": request["roles"]["operator"], "roles_sha256": digest(request["roles"]),
                "allocation_sha256": digest(trial_purpose_allocation(request, purpose)), "engineering_sha256": digest(request["engineering"]),
                "expires_at": approval["expires_at"]}.items():
            if grant[field] != value:
                raise PermissionError("Authenticated grant exact subject or purpose differs: " + field)
        return request, grant

    def authorize(self, request, *, purpose, binding_sha256):
        self._validated(request, purpose=purpose, binding_sha256=binding_sha256)

    def consume(self, request, *, purpose, binding_sha256):
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            _, grant = self._validated(request, purpose=purpose, binding_sha256=binding_sha256)
            encoded = canonical_json(grant).decode()
            db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?)", (grant["issuer_id"], grant["grant_id"], purpose,
                binding_sha256, encoded, digest(grant)))

    def reserve_dispatch(self, request, *, purpose, binding_sha256, dispatch_id, allocation):
        """Reserve finite worst-case resources before dispatch; never refund uncertainty."""
        if type(dispatch_id) is not str or not dispatch_id.strip() or len(dispatch_id) > 256:
            raise ValueError("Use a bounded exact dispatch identity")
        request = validate_trial_request(request)
        fields, summed, ceiling = _ALLOCATION, _SUMMED, request["allocation"]
        def resources(value):
            if type(value) is not dict or set(value) != fields:
                raise ValueError("Use the closed preventive dispatch allocation")
            for field, item in value.items():
                if (type(item) not in (int, float) or not math.isfinite(item) or item < 0
                        or field != "seconds" and type(item) is not int):
                    raise ValueError("Dispatch allocations must be finite nonnegative resources")
            return value
        if request["format"] == FORMAT_V3:
            if purpose == "workflow_generation":
                from .codex_job_policy import validate_job_limits
                fields, summed, ceiling = {"jobs", "seconds", "context_bytes", "output_bytes"}, {"jobs", "seconds"}, request["allocation"]["generation"]
                def resources(value):
                    if (type(value) is not dict or set(value) != {"kind", "generation", "numeric"}
                            or value["kind"] != "generation_jobs" or value["numeric"] is not None):
                        raise ValueError("Use the closed generation-job dispatch allocation")
                    return validate_job_limits(value["generation"])
            elif purpose == "trial_execution":
                fields = {"memory_mb", "seconds", "storage_bytes", "max_runs"}
                summed, ceiling = fields - {"memory_mb"}, request["allocation"]["numeric"]
                if ceiling is None:
                    raise PermissionError("Separate explicit numeric allocation is required")
                numeric_resources = resources
                def resources(value):
                    if (type(value) is not dict or set(value) != {"kind", "numeric"}
                            or value["kind"] != "numeric_execution"):
                        raise ValueError("Use the closed numeric-execution dispatch allocation")
                    return numeric_resources(value["numeric"])
            else:
                raise ValueError("Unsupported trial authority purpose")
        dispatch_resources = resources(allocation)
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            request, grant = self._validated(request, purpose=purpose, binding_sha256=binding_sha256)
            identity = (grant["issuer_id"], grant["grant_id"])
            retained = db.execute("SELECT purpose,binding_sha256,grant_json,grant_sha256 FROM grants WHERE issuer_id=? AND grant_id=?", identity).fetchone()
            if (retained is None or retained[0] != purpose or retained[1] != binding_sha256
                    or retained[2] != canonical_json(grant).decode() or retained[3] != digest(grant)):
                raise PermissionError("Dispatch lacks its exact consumed authenticated grant")
            spent = {key: 0 for key in summed}
            for encoded, expected in db.execute("SELECT allocation_json,allocation_sha256 FROM dispatches WHERE issuer_id=? AND grant_id=?", identity):
                prior = decode_json(encoded.encode())
                if digest(prior) != expected:
                    raise PermissionError("Retained dispatch allocation integrity differs")
                retained_resources = resources(prior)
                for key in summed:
                    spent[key] += retained_resources[key]
            if (any(dispatch_resources[key] > ceiling[key] for key in fields - summed) or any(
                    spent[key] + dispatch_resources[key] > ceiling[key] for key in summed)):
                raise PermissionError("Preventive trial allocation cap exhausted")
            db.execute("INSERT INTO dispatches VALUES(?,?,?,?,?)", (*identity, dispatch_id,
                canonical_json(deepcopy(allocation)).decode(), digest(allocation)))
        return {"purpose": purpose, "binding_sha256": binding_sha256, "dispatch_id": dispatch_id,
            "allocation_sha256": digest(allocation), "status": "reserved_no_refund_on_uncertainty"}


    def _validated_colab_qualification(self, request, grant, *, job):
        """Separate synthetic bootstrap; never enters native/offline trial admission."""
        from .colab_execution_authority import ColabQualificationContext
        from .colab_qualification_contract import unsigned_colab_qualification_grant_subject
        from .rna_grant_signing import verify_authority_signature
        if type(self.colab_qualification_context) is not ColabQualificationContext:
            raise PermissionError("Exact trusted Colab bootstrap context is required")
        request, job = self.colab_qualification_context.verify(request, workspace=self.workspace,
            job=job, now=self.clock())
        if type(grant) is not dict or set(grant) != _GRANT_FIELDS:
            raise PermissionError("Authenticated closed Colab issuer grant is required")
        from .rna_approval_setup import _name
        _name(grant["issuer_id"])
        _name(grant["grant_id"])
        key = self.issuer_keys.get(grant["issuer_id"])
        if not isinstance(key, Ed25519PublicKey):
            raise PermissionError("Colab grant issuer is not an explicitly trusted public root")
        expected = unsigned_colab_qualification_grant_subject(request, issuer_id=grant["issuer_id"],
            grant_id=grant["grant_id"], now=self.clock())
        payload = {key:value for key,value in grant.items() if key != "signature"}
        if payload != expected:
            raise PermissionError("Colab authenticated grant exact subject differs")
        verified = verify_authority_signature(payload, grant["signature"], public_key=key)
        return request, job, verified


    def export_colab_public_approval(self, request, grant, *, job):
        """Public material only; this neither reserves nor dispatches computation."""
        from cryptography.hazmat.primitives import serialization
        request, job, grant = self._validated_colab_qualification(request, grant, job=job)
        key = self.issuer_keys[grant["issuer_id"]]
        return {"request":request, "grant":grant, "issuer":{"issuer_id":grant["issuer_id"],
            "public_key_hex":key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()}}


    def consume_and_reserve_colab_qualification(self, request, grant, *, dispatch_id, allocation, job):
        """Atomically spend one authenticated bootstrap grant and one full dispatch.

        There is no refund, replay, partial budget reuse, local fallback or retry
        after this durable transaction commits, including uncertain dispatch.
        """
        request, job, grant = self._validated_colab_qualification(request, grant, job=job)
        if dispatch_id != job["job_id"] or canonical_json(allocation) != canonical_json(job["limits"]):
            raise PermissionError("Colab dispatch identity or complete preventive allocation differs")
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            request, job, grant = self._validated_colab_qualification(request, grant, job=job)
            db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?)", (grant["issuer_id"], grant["grant_id"],
                "trial_execution", grant["binding_sha256"], canonical_json(grant).decode(), digest(grant)))
            reserved = deepcopy(request["allocation"])
            db.execute("INSERT INTO dispatches VALUES(?,?,?,?,?)", (grant["issuer_id"], grant["grant_id"], dispatch_id,
                canonical_json(reserved).decode(), digest(reserved)))
            self._validated_colab_qualification(request, grant, job=job)
        return {"format":"devbench.colab-execution-reservation/1", "job_id":job["job_id"],
            "job_sha256":digest(job), "grant_id":grant["grant_id"], "dispatch_id":dispatch_id,
            "request_sha256":digest(request), "allocation":deepcopy(allocation)}


    @contextmanager
    def colab_qualification_signing_preflight(self, request, *, payload, job):
        """Lock one exact unspent subject while the dedicated existing signer acts."""
        from .colab_execution_authority import ColabQualificationContext
        from .colab_qualification_contract import unsigned_colab_qualification_grant_subject
        from .rna_approval_setup import _UNSIGNED_FIELDS
        if type(self.colab_qualification_context) is not ColabQualificationContext:
            raise PermissionError("Exact trusted Colab bootstrap context is required")
        if type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS:
            raise PermissionError("Closed unsigned Colab grant subject is required")
        with self._database() as db:
            db.execute("BEGIN IMMEDIATE")
            request, job = self.colab_qualification_context.verify(request, workspace=self.workspace, job=job, now=self.clock())
            expected = unsigned_colab_qualification_grant_subject(request, issuer_id=payload["issuer_id"],
                grant_id=payload["grant_id"], now=self.clock())
            if payload != expected:
                raise PermissionError("Colab signing subject differs")
            key = self.issuer_keys.get(payload["issuer_id"])
            if not isinstance(key, Ed25519PublicKey):
                raise PermissionError("Signing issuer lacks an explicitly injected public root")
            if db.execute("SELECT 1 FROM grants WHERE (issuer_id=? AND grant_id=?) OR (purpose=? AND binding_sha256=?)",
                    (payload["issuer_id"],payload["grant_id"],"trial_execution",payload["binding_sha256"])).fetchone() is not None:
                raise PermissionError("Colab grant or binding already consumed; replay denied")
            yield key
            self.colab_qualification_context.verify(request, workspace=self.workspace, job=job, now=self.clock())
