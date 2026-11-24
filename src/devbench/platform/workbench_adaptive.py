"""Local Workbench actions over the authoritative adaptive workflow."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from devbench.analysis.evidence_access import EvidenceDelivery
from devbench.engine.workflow_operations import (
    LEDGER_NAME,
    OperationBlocked,
    OperationConflict,
    WorkflowOperationLedger,
)
from devbench.engine.workspace import read_bounded_file

from .adaptive_workflow import ADAPTIVE_MODE, RESEARCH_INFERENCE, AdaptiveWorkflow

_SESSION_ID = re.compile(r"[0-9a-f]{32}")
_SESSION_LIMIT = 32
_PLAN_LIMIT = 16
_PACKAGE_LIMIT = 4
_DOCUMENT_LIMIT = 64 * 1024
_PACKAGE_METADATA_LIMIT = 512 * 1024


def _basename(value: str) -> str:
    return Path(value.replace("\\", "/")).name[:180]


def _plugin_labels(plan: dict[str, Any]) -> dict[str, str]:
    profiles = plan["profiles"]
    dataset = next((p for p in profiles if p["role"] == "expression"), profiles[0])
    return {
        "objective": plan["objective"][:500],
        "dataset_label": _basename(dataset["path"]),
    }


def _plugin_plan_view(plan: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    # The hash identifies the full frozen plan. Expose only its design and runtime
    # projection, since the retained plan also contains absolute input paths.
    from .plugin_jobs import canonical

    design = plan["design"]
    # Retain parameter values that describe the analysis, with no input locators.
    design = {
        **{key: value for key, value in design.items() if key != "parameters"},
        "parameters": _plan_parameters(design.get("parameters", {})),
    }
    return {
        "plan_id": plan["plan_id"],
        "plan_sha256": row["plan_sha256"],
        "operation": plan["operation"],
        "created_at": datetime.fromtimestamp(row["created_at"], UTC).isoformat(),
        "design_summary": design if len(canonical(design)) <= 8192 else {},
        "runtime": _plan_parameters(plan.get("runtime") or {}),
        "inputs": [
            {
                "name": _basename(profile["path"]),
                "role": profile["role"],
                "file_type": profile["observed"].get("file_type"),
            }
            for profile in plan.get("profiles", ())
        ],
        "mounted_image_id": plan["mounted_image_id"],
    }


def _plan_parameters(values: dict[str, Any]) -> dict[str, Any]:
    def safe(value: Any, depth: int) -> bool:
        if isinstance(value, str):
            return len(value) <= 256 and not any(mark in value for mark in ("/", "\\"))
        if isinstance(value, list):
            return (
                depth < 6
                and len(value) <= 32
                and all(safe(item, depth + 1) for item in value)
            )
        return value is None or type(value) in {int, float, bool}

    def project(mapping: dict[str, Any], depth: int) -> dict[str, Any]:
        if depth >= 6:
            return {}
        result = {}
        for key, value in islice(mapping.items(), 64):
            if isinstance(value, dict):
                result[key] = project(value, depth + 1)
            elif safe(value, depth):
                result[key] = value
        return result

    return project(values, 0)


def _plugin_projection(app, binding: WorkbenchAdaptiveBinding) -> dict[str, Any] | None:
    from .plugin_jobs import JobStore
    from .plugin_mcp import (
        studio_job_readback,
        studio_plan_scope,
        studio_run_projection,
    )
    from .workbench_receipt_plots import receipt_plot_paths
    from .workbench_ui_projection import _publication_hashes

    sid = binding.session_id
    with studio_job_readback(app.workspace) as db:
        if db is None:
            return None
        plans = db.execute(
            "SELECT * FROM plugin_plans WHERE session_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (sid, _PLAN_LIMIT),
        ).fetchall()
        if not plans:
            return None
        latest = JobStore._plan(plans[0])
        if not studio_plan_scope(db, latest):
            return None
        blocked = _publication_hashes(app)
        plan_views, runs = [], []
        for row in plans:
            plan = JobStore._plan(row)
            if not studio_plan_scope(db, plan):
                continue
            plan_views.append(_plugin_plan_view(plan, row))
            run = db.execute(
                "SELECT * FROM plugin_runs "
                "WHERE session_id=? AND plan_id=? AND plan_sha256=?",
                (sid, row["plan_id"], row["plan_sha256"]),
            ).fetchone()
            if run is not None:
                projected = studio_run_projection(
                    app.workspace, binding.delivery.broker, plan, run, blocked,
                )
                if app.offline_only:
                    paths = receipt_plot_paths(sid, projected)
                    if paths:
                        projected["plotly_paths"] = paths
                runs.append(projected)
        return {
            **_plugin_labels(latest),
            "mounted_image_id": latest["mounted_image_id"],
            "plans": plan_views,
            "runs": runs,
        }


def _package_identity(
    app,
    flow: AdaptiveWorkflow,
    package_id: str,
    *,
    revision: int,
) -> tuple[Path, str, dict[str, Any], dict[str, Any]]:
    from devbench.analysis.thesis_synthesis import THESIS_RECEIPT
    from devbench.study.evidence import decode_json

    if _SESSION_ID.fullmatch(package_id) is None:
        raise KeyError("Unknown research package")
    root = app.workspace / "runs" / package_id / "research_package"
    if root.resolve() != root:
        raise PermissionError("Research package paths must stay inside the workspace")
    raw = read_bounded_file(root / THESIS_RECEIPT, _PACKAGE_METADATA_LIMIT)
    receipt = decode_json(raw)
    manifest_raw = read_bounded_file(
        root / "provenance_manifest.json", _PACKAGE_METADATA_LIMIT
    )
    files = receipt["files"]
    if hashlib.sha256(manifest_raw).hexdigest() != files["provenance_manifest.json"]:
        raise ValueError("Research package manifest changed")
    manifest = decode_json(manifest_raw)
    dag = manifest["execution_dag"]
    if dag["lineage_id"] != flow.lineage_id:
        raise KeyError("Research package belongs to another session")

    def current(db):
        _, head = flow._projection(db)
        if (
            not revision
            <= dag["revision"]
            <= flow.authority._revision(db, flow.lineage_id)
            or dag["adaptive_root_sha256"] != head.records_sha256
            or _graph_revision(flow, connection=db) != revision
        ):
            raise OperationBlocked("thesis_package_stale")

    flow._read_projection(current)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        sealed = read_bounded_file(root.parent / "research-package-root.sha256", 65)
    except FileNotFoundError as error:
        raise OperationBlocked("thesis_package_integrity_unverified") from error
    if sealed != (digest + "\n").encode("ascii"):
        raise ValueError("Research package differs from its retained root")
    return root, digest, manifest, files


def _check_package_scope(app, flow, checksum, manifest, files) -> set[str]:
    from devbench.analysis.scientific_publication import _visible_publication_ledgers
    from devbench.engine.partition_authority import embargoed
    from devbench.engine.scientific_authority import check_schema

    from .plugin_mcp import studio_evidence_digests

    identities = {checksum, *studio_evidence_digests((manifest, files))}

    # The scoped package route has its own use checks. Inspect partition custody
    # rather than the legacy metadata filter's broader inventory of hidden hashes.
    def protected(db):
        return check_schema(db) and any(
            embargoed(db, identity) for identity in identities
        )

    if flow._read_projection(protected):
        raise PermissionError("Research package references protected evidence")
    for path in _visible_publication_ledgers(app.scientific):
        if path != flow.ledger.path and WorkflowOperationLedger(
            path, readonly=True
        )._read(protected):
            raise PermissionError("Research package references protected evidence")
    return identities


def _package_preview(
    root: Path,
    name: str,
    files: dict[str, Any],
) -> str:
    value = read_bounded_file(root / name, _PACKAGE_METADATA_LIMIT)
    if hashlib.sha256(value).hexdigest() != files[name]:
        raise ValueError("Research package changed during readback")
    return value[:_DOCUMENT_LIMIT].decode("utf-8", errors="ignore")


def _package_projection(
    app,
    flow: AdaptiveWorkflow,
    package_id: str,
    revision: int,
) -> dict[str, Any]:
    root, checksum, manifest, files = _package_identity(
        app, flow, package_id, revision=revision
    )
    _check_package_scope(app, flow, checksum, manifest, files)
    return _verified_package_projection(
        root,
        package_id,
        checksum,
        manifest,
        files,
        f"/api/scientific/adaptive/{flow.lineage_id}/package/{package_id}/zip",
    )


def _verified_package_projection(
    root: Path,
    package_id: str,
    checksum: str,
    manifest: dict[str, Any],
    files: dict[str, Any],
    download_path: str,
) -> dict[str, Any]:
    from devbench.analysis.thesis_synthesis import verify_thesis_package

    verified = verify_thesis_package(root, checksum)
    if not (verified["valid"] and verified["evidence_verified"]):
        raise OperationBlocked("thesis_package_integrity_unverified")
    inventory = manifest["data_inventory"]["observed_inputs"]
    return {
        "package_id": package_id,
        "root_sha256": checksum,
        "outcome": manifest["outcome"],
        "created_at": datetime.fromtimestamp(root.stat().st_mtime, UTC).isoformat(),
        "summary_md": _package_preview(root, "summary.md", files),
        "methods_md": _package_preview(root, "methods.md", files),
        "replay_script": _package_preview(root, "replay_bundle/replay.py", files),
        "provenance_summary": {
            "original_inputs": inventory[:32],
            "runtime": manifest["runtime"].get("container_image_digests", [])[:16],
            "derived_artifacts": [
                name for name in files if name.startswith("figures/")
            ][:32],
            "adaptive_root_sha256": manifest["execution_dag"]["adaptive_root_sha256"],
            "answer_status": verified["answer_status"],
        },
        "download_path": download_path,
    }


def _local_package_members(app, flow, row):
    from devbench.analysis.thesis_synthesis import THESIS_RECEIPT
    from devbench.study.evidence import decode_json

    from .workbench_thesis import _workspace_path

    package_id = row["package_id"]
    if _SESSION_ID.fullmatch(package_id) is None:
        raise KeyError("Unknown research package")
    root = _workspace_path(
        app.workspace / "thesis-packages" / flow.lineage_id / package_id,
        (app.workspace,),
    )
    raw = read_bounded_file(root / THESIS_RECEIPT, _PACKAGE_METADATA_LIMIT)
    if hashlib.sha256(raw).hexdigest() != row["root_sha256"]:
        raise ValueError("Research package differs from its retained root")
    files = decode_json(raw)["files"]
    manifest_raw = read_bounded_file(
        root / "provenance_manifest.json", _PACKAGE_METADATA_LIMIT
    )
    if hashlib.sha256(manifest_raw).hexdigest() != files["provenance_manifest.json"]:
        raise ValueError("Research package manifest changed")
    manifest = decode_json(manifest_raw)
    _check_package_scope(app, flow, row["root_sha256"], manifest, files)
    return root, manifest, files


def _local_packages(app, binding, flow, revision: int) -> list[dict[str, Any]]:
    from .workbench_thesis import _checked_thesis_lineage
    from .workbench_thesis_actions import _capabilities
    from .workbench_thesis_export import thesis_export_inventory

    sid = flow.lineage_id
    if not (app.workspace / "thesis-package-index" / sid).is_dir():
        return []
    _, lifecycle, _ = _checked_thesis_lineage(app, sid)
    _, _, actions = flow._read_projection(
        lambda db: _capabilities(binding, lifecycle, flow, db)
    )
    if not actions["synthesize"]["enabled"]:
        return []
    packages = []
    inventory = thesis_export_inventory(app, sid, revision, allow_export=True)
    for row in islice((row for row in inventory if row["download"]), _PACKAGE_LIMIT):
        try:
            root, manifest, files = _local_package_members(app, flow, row)
            packages.append(
                _verified_package_projection(
                    root,
                    row["package_id"],
                    row["root_sha256"],
                    manifest,
                    files,
                    row["download"],
                )
            )
        except (OperationBlocked, OSError, KeyError, TypeError, ValueError):
            continue
    return packages


def guard_local_package_inventory(app, session_id: str, view: dict[str, Any]) -> None:
    from .workbench_thesis import _checked_thesis_lineage

    packages = view.get("packages")
    if not packages:
        return
    flow, _, _ = _checked_thesis_lineage(app, session_id)
    visible = []
    for row in packages:
        try:
            _local_package_members(app, flow, row)
        except (OSError, OperationBlocked, ValueError, KeyError, TypeError):
            continue
        visible.append(row)
    view["packages"] = visible


def _package_ids(workspace: Path) -> list[str]:
    runs = workspace / "runs"
    if not runs.is_dir() or runs.is_symlink():
        return []
    with os.scandir(runs) as entries:
        candidates = [
            (entry.stat(follow_symlinks=False).st_mtime_ns, entry.name)
            for entry in islice(entries, 1024)
            if _SESSION_ID.fullmatch(entry.name)
            and entry.is_dir(follow_symlinks=False)
            and (runs / entry.name / "research_package").is_dir()
        ]
    return [name for _, name in sorted(candidates, reverse=True)]


def _retained_packages(
    app,
    flow: AdaptiveWorkflow,
    revision: int,
    candidates: list[str],
) -> list[dict[str, Any]]:
    packages = []
    for package_id in candidates:
        try:
            packages.append(_package_projection(app, flow, package_id, revision))
        except (OperationBlocked, OSError, KeyError, TypeError, ValueError):
            continue
        if len(packages) == _PACKAGE_LIMIT:
            break
    return packages


def _studio_labels(scientific, plan: dict[str, Any]) -> dict[str, str]:
    context = plan.get("context", {})
    task = context.get("task", {})
    labels = {
        "objective": str(
            task.get("scientific_objective") or task.get("objective") or ""
        )[:500],
        "dataset_label": "",
    }
    refs = plan.get("scientific_anchors", {}).get("dataset_versions", ())
    if refs:
        store = scientific.store()
        dataset = store.get("dataset", store.resolve(refs[0])["dataset_id"])
        labels["dataset_label"] = _basename(dataset["name"])
    return labels


def _catalog_plugin_plans(workspace: Path) -> dict[str, dict[str, Any]]:
    from .plugin_jobs import JobStore
    from .plugin_mcp import studio_job_readback, studio_plan_scope

    sessions: dict[str, dict[str, Any]] = {}
    with studio_job_readback(workspace) as db:
        if db is None:
            return sessions
        rows = db.execute(
            "SELECT * FROM plugin_plans AS plan WHERE rowid = ("
            "SELECT rowid FROM plugin_plans WHERE session_id=plan.session_id "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1) "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (_SESSION_LIMIT,),
        )
        for row in rows:
            sid = row["session_id"]
            if not isinstance(sid, str) or _SESSION_ID.fullmatch(sid) is None:
                continue
            try:
                plan = JobStore._plan(row)
            except (OperationBlocked, KeyError, ValueError):
                continue
            statuses = db.execute(
                "SELECT status, count(*), max(updated_at) FROM plugin_runs "
                "WHERE session_id=? GROUP BY status",
                (sid,),
            ).fetchall()
            sessions[sid] = {
                "external_scope": studio_plan_scope(db, plan),
                "labels": _plugin_labels(plan),
                "runs": Counter({status: count for status, count, _ in statuses}),
                "updated_at": datetime.fromtimestamp(
                    max([row["created_at"], *(when for _, _, when in statuses)]),
                    UTC,
                ).isoformat(),
            }
    return sessions


def _catalog_status(steps: Counter, runs: Counter) -> str:
    if runs["queued"] or runs["running"] or steps["running"]:
        return "running"
    if runs["awaiting_review"] or steps["awaiting_review"] or steps["blocked"]:
        return "awaiting_review"
    if runs["failed"] or runs["interrupted"] or steps["failed"]:
        return "failed"
    if runs["completed"] or steps["succeeded"]:
        return "completed"
    return "planned"


def _catalog_runs(plugin, snapshot) -> Counter:
    from .workbench_interactive import _OPERATOR_BULK_STEP

    runs = Counter(plugin["runs"]) if plugin else Counter()
    if runs["awaiting_review"] and any(
        row["step_id"] == _OPERATOR_BULK_STEP
        and row["status"] == "succeeded"
        and not row.get("claim_hold")
        for row in snapshot["steps"]
    ):
        runs["completed"] += runs.pop("awaiting_review")
    return runs


def _catalog_entry(
    app, ledger, sid: str, plugin: dict[str, Any] | None, package_ids: list[str]
) -> dict[str, Any]:
    from .thesis_aims import ThesisAimLifecycle

    plan = app.scientific._plan(sid)
    flow = AdaptiveWorkflow(ledger, sid)
    snapshot = flow.projection_snapshot()
    steps = Counter(row["status"] for row in snapshot["steps"])
    failed_attempts = sum(
        row["status"] in {"failed", "interrupted"}
        for row in snapshot["attempt_history"]
    )
    lifecycle = ThesisAimLifecycle(ledger, sid)
    aim_count = ledger._read(lambda db: len(lifecycle._projection(db)[0].aims))
    classification = plan["data_classification"]
    if plugin is None and plan.get("created_by") == "plugin-operator":
        raise KeyError("Plugin job inventory is unavailable")
    binding = None
    if plugin or (app.workspace / "thesis-package-index" / sid).is_dir():
        from .workbench_interactive import ensure_adaptive_binding

        binding = ensure_adaptive_binding(app, sid)
        if binding is None:
            raise PermissionError("Plugin session disclosure is unavailable")
        binding.require_owner(app.scientific)
    display = (
        (plugin is None or plugin["external_scope"])
        and classification in {"public", "synthetic"}
    ) or (app.offline_only and classification in {"private", "private_clinical"})
    labels = (
        (plugin["labels"] if plugin else _studio_labels(app.scientific, plan))
        if display
        else {
            "objective": "Local-only investigation",
            "dataset_label": "",
        }
    )
    runs = _catalog_runs(plugin, snapshot)
    package_count = 0
    if binding is not None and plugin and plugin["external_scope"] and package_ids:
        _require_package_disclosure(binding, ledger)
        package_count = len(
            _retained_packages(app, flow, _graph_revision(flow), package_ids)
        )
    elif binding is not None and not plugin and display:
        package_count = len(_local_packages(app, binding, flow, _graph_revision(flow)))
    return {
        "session_id": sid,
        "origin": "plugin" if plugin else "studio",
        **labels,
        "status": _catalog_status(steps, runs),
        "step_count": max(sum(steps.values()), sum(runs.values())),
        "failed_step_count": max(
            steps["failed"],
            failed_attempts,
            runs["failed"] + runs["interrupted"],
        ),
        "aim_count": aim_count,
        "package_count": package_count,
        "data_classification": classification,
        "updated_at": plugin["updated_at"]
        if plugin
        else plan.get("updated_at", plan["created_at"]),
    }


def adaptive_session_catalog(app) -> dict[str, Any]:
    """List bounded local sessions that have an authoritative adaptive lineage."""
    ledger = app.scientific.open_workflow_ledger()
    if ledger is None or not app.scientific.initialized():
        return {"sessions": []}
    try:
        _ = ledger.scientific
    except OperationBlocked as error:
        if error.state != "scientific_authority_unavailable":
            raise
        return {"sessions": []}
    try:
        plugins = _catalog_plugin_plans(app.workspace)
    except (PermissionError, sqlite3.DatabaseError):
        plugins = {}
    studios = ledger._read(
        lambda db: db.execute(
            "SELECT lineage_id FROM scientific_records WHERE kind='adaptive_event' "
            "GROUP BY lineage_id ORDER BY max(rowid) DESC LIMIT ?",
            (_SESSION_LIMIT,),
        ).fetchall()
    )
    package_ids = _package_ids(app.workspace) if plugins else []
    sessions = []
    for sid in dict.fromkeys([*plugins, *(row[0] for row in studios)]):
        if _SESSION_ID.fullmatch(sid) is None:
            continue
        try:
            sessions.append(
                _catalog_entry(app, ledger, sid, plugins.get(sid), package_ids)
            )
        except (KeyError, OperationBlocked, PermissionError, ValueError):
            continue
    sessions.sort(key=lambda row: row["updated_at"], reverse=True)
    return {"sessions": sessions[:_SESSION_LIMIT]}


def _require_package_disclosure(binding: WorkbenchAdaptiveBinding, ledger) -> None:
    broker = binding.delivery.broker
    context_id = binding.delivery.context_id
    _, policy = ledger._read(lambda db: broker._context_policy(db, context_id))
    if not policy.allow_outcomes:
        raise PermissionError("Research package requires current outcome access")


def adaptive_package_archive(app, session_id: str, package_id: str) -> bytes:
    """Recheck current lineage, disclosure and package members before sending bytes."""
    from devbench.engine.partition_authority import embargoed

    from .plugin_jobs import JobStore
    from .plugin_mcp import studio_job_readback, studio_plan_scope
    from .workbench_interactive import ensure_adaptive_binding
    from .workbench_thesis_export import _archive

    with app._lock:
        binding = ensure_adaptive_binding(app, session_id)
        if (
            binding is None
            or binding.delivery.context_id != "plugin-scientist-" + session_id
        ):
            raise PermissionError(
                "Research package requires the authorized Plugin session"
            )
        plan, ledger = binding.require_owner(app.scientific)
        if plan["data_classification"] not in {"public", "synthetic"}:
            raise PermissionError("Research package is unavailable for this data scope")
        with studio_job_readback(app.workspace) as db:
            if db is None:
                raise PermissionError("Plugin job inventory is unavailable")
            latest = db.execute(
                "SELECT * FROM plugin_plans WHERE session_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if latest is None or not studio_plan_scope(db, JobStore._plan(latest)):
                raise PermissionError(
                    "Research package is unavailable for this data scope"
                )
        _require_package_disclosure(binding, ledger)
        flow = _flow(binding, ledger)
        revision = _graph_revision(flow)
        root, checksum, manifest, files = _package_identity(
            app, flow, package_id, revision=revision
        )
        identities = _check_package_scope(app, flow, checksum, manifest, files)
        content = _archive(root, checksum)

        def current(db):
            if _graph_revision(flow, connection=db) != revision:
                raise OperationBlocked("thesis_package_stale")
            if any(embargoed(db, identity) for identity in identities):
                raise PermissionError("Research package references protected evidence")

        binding.delivery.release(
            {
                "format": "devbench.research-package-download/1",
                "root_sha256": checksum,
                "content_sha256": hashlib.sha256(content).hexdigest(),
                "maximum_bytes": len(content),
            },
            _guard=current,
        )
        return content


def guarded_local_package_archive(app, session_id: str, package_id: str) -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    from devbench.analysis.thesis_synthesis import THESIS_RECEIPT
    from devbench.study.evidence import decode_json

    from .workbench_thesis import _checked_thesis_lineage
    from .workbench_thesis_export import _index, _record, export_thesis_package

    with app._lock:
        flow, _, _ = _checked_thesis_lineage(app, session_id)
        record = _record(app, _index(app, session_id) / (package_id + ".json"))
        _local_package_members(app, flow, {**record, "package_id": package_id})
        archive = export_thesis_package(app, session_id, package_id)
        with ZipFile(BytesIO(archive.content)) as zipped:
            raw = zipped.read(THESIS_RECEIPT)
            manifest = decode_json(zipped.read("provenance_manifest.json"))
        _check_package_scope(
            app,
            flow,
            hashlib.sha256(raw).hexdigest(),
            manifest,
            decode_json(raw)["files"],
        )
        return archive.content


@dataclass(frozen=True)
class WorkbenchAdaptiveBinding:
    """Coordinator capabilities supplied in-process for one admitted lineage."""

    session_id: str
    delivery: EvidenceDelivery
    executor: object | None = None
    controller: object | None = None
    narrative_delivery: EvidenceDelivery | None = None
    review_lifecycle: object | None = None
    review_binding: object | None = None
    diagnostic_qualification: object | None = None
    diagnostic_actions: dict | None = None
    replay_package: Path | None = None
    replay_root_sha256: str | None = None
    manual_local_oci: bool = False
    tier_a_executor: object | None = None
    thesis_lifecycle: object | None = None
    publication_registry: object | None = None
    campaign_mode: str | None = None

    def require_replay_path(self, scientific):
        if self.replay_package is None:
            if self.replay_root_sha256 is not None:
                raise ValueError(
                    "A replay root must identify an exact retained package"
                )
            return
        if (
            not isinstance(self.replay_package, Path)
            or not self.replay_package.is_absolute()
            or self.replay_package != self.replay_package.resolve()
            or not any(
                self.replay_package.is_relative_to(root)
                for root in (scientific.root, scientific.app.workspace)
            )
            or type(self.replay_root_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", self.replay_root_sha256)
        ):
            raise PermissionError(
                "Replay requires a bound package inside the project and its retained "
                "root"
            )

    def require_owner(self, scientific):
        from devbench.analysis.episode_agent import AdaptiveController
        from devbench.analysis.rna_diagnostic_policy import (
            DiagnosticQualificationAuthority,
        )
        from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

        if (
            type(self.session_id) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.session_id) is None
        ):
            raise ValueError("Use the exact planner session identity")
        plan = scientific._plan(self.session_id)
        if type(self.delivery) is not EvidenceDelivery:
            raise PermissionError(
                "Adaptive Workbench requires a current evidence delivery"
            )
        ledger = self.delivery.broker.authority.ledger
        if ledger.readonly or ledger.path != scientific.root / LEDGER_NAME:
            raise PermissionError(
                "Adaptive Workbench requires the exact writable disclosure ledger"
            )
        if (
            self.manual_local_oci or self.controller is not None
        ) and scientific.app.allow_data_execution is not True:
            raise PermissionError(
                "Adaptive execution requires current Workbench "
                "data execution permission"
            )
        self._require_disclosure_owner(ledger)
        if self.narrative_delivery is not None and (
            type(self.narrative_delivery) is not EvidenceDelivery
            or self.narrative_delivery.broker is not self.delivery.broker
            or self.narrative_delivery.context_id != self.delivery.context_id
        ):
            raise PermissionError(
                "Adaptive narrative requires the same scientist recipient and owner"
            )
        if self.controller is not None and (
            type(self.controller) is not AdaptiveController
            or self.controller.invoker.session_id != self.session_id
            or self.controller.invoker.planner.methods.root != scientific.root
            or self.controller.broker.authority.ledger.readonly
            or self.controller.broker.authority.ledger.path != ledger.path
        ):
            raise PermissionError(
                "Adaptive controller capability belongs to another lineage"
            )
        if self.review_lifecycle is not None and (
            type(self.review_lifecycle) is not ReviewHoldLifecycle
            or self.review_lifecycle.lineage_id != self.session_id
            or self.review_lifecycle.ledger is not ledger
            or not callable(self.review_binding)
        ):
            raise PermissionError(
                "Adaptive review capability belongs to another lineage"
            )
        if (
            self.diagnostic_qualification is not None
            and type(self.diagnostic_qualification)
            is not DiagnosticQualificationAuthority
        ):
            raise PermissionError(
                "Adaptive diagnostics require the registered qualification authority"
            )
        self._require_executor_binding(ledger)
        self._require_lineage_services(ledger)
        return plan, ledger

    def _require_lineage_services(self, ledger):
        from devbench.analysis.scientific_publication import PublicationRegistry

        from .thesis_workflow import ThesisWorkflow

        if self.thesis_lifecycle is not None and (
            type(self.thesis_lifecycle) is not ThesisWorkflow
            or self.thesis_lifecycle.lineage_id != self.session_id
            or self.thesis_lifecycle.ledger is not ledger
        ):
            raise PermissionError("Thesis lifecycle must use this exact lineage")
        if self.publication_registry is not None and (
            type(self.publication_registry) is not PublicationRegistry
            or self.publication_registry.workflow.lineage_id != self.session_id
            or self.publication_registry.ledger is not ledger
        ):
            raise PermissionError("Publication registry must use this exact lineage")

    def _require_disclosure_owner(self, ledger):
        with ledger._database() as db:
            db.execute("BEGIN")
            broker = self.delivery.broker
            recipient, policy = broker._context_policy(db, self.delivery.context_id)
            if (
                recipient["lineage_id"] != self.session_id
                or recipient["role"] != "scientist"
            ):
                raise PermissionError(
                    "Adaptive readback requires a scientist recipient for this lineage"
                )
            source = broker._source(db, self.delivery.source_id)
            broker._check(recipient, policy, source, set())
            if self.manual_local_oci:
                flow = AdaptiveWorkflow(ledger, self.session_id)
                if flow.controller_state(connection=db)["binding"] is not None:
                    raise PermissionError(
                        "The bound controller requires its own "
                        "current runtime capabilities"
                    )

    def _require_executor_binding(self, ledger):
        if self.tier_a_executor is not None:
            from devbench.engine.tier_a_executor import TierAExecutor

            if (
                type(self.tier_a_executor) is not TierAExecutor
                or self.tier_a_executor.ledger.path != ledger.path
                or self.manual_local_oci
            ):
                raise PermissionError(
                    "Tier A requires the exact current scientific ledger"
                )
        if self.manual_local_oci:
            from devbench.engine.exploratory_oci import ExploratoryOCI

            if (
                type(self.executor) is not ExploratoryOCI
                or self.executor.ledger is not ledger
                or self.controller is not None
            ):
                raise PermissionError(
                    "Manual adaptive execution requires its bound local OCI executor"
                )


def register_binding(app, binding):
    if app.scientific is None or type(binding) is not WorkbenchAdaptiveBinding:
        raise PermissionError(
            "Adaptive binding requires the scientific workspace and its coordinator"
        )
    with app._lock:
        _, ledger = binding.require_owner(app.scientific)
        previous = app._adaptive_bindings.get(binding.session_id)
        if previous is not None and previous is not binding:
            _require_local_controller_upgrade(previous, binding)
        binding.require_replay_path(app.scientific)
        if binding.controller is None:
            if _adaptive_ledger(app.scientific, binding.session_id) is None:
                raise PermissionError("Adaptive binding requires an admitted lineage")
        elif previous is None or previous.controller is None:
            flow = _flow(binding, ledger)
            with flow._read_connection() as db:
                if flow.controller_state(connection=db)["binding"] is None:
                    execution = flow.authority._load_state(db, binding.session_id)
                    flow.authority._active(db, binding.session_id, execution)
            binding.controller.bind(flow)
        app._adaptive_bindings[binding.session_id] = binding


def _require_local_controller_upgrade(previous, current):
    from dataclasses import replace

    if (
        previous.controller is not None
        or current.campaign_mode != "local_interactive_codex"
        or current.controller is None
        or replace(current, controller=None, campaign_mode=None) != previous
    ):
        raise PermissionError("The current lineage already has a coordinator binding")


def _adaptive_ledger(scientific, session_id):
    path = scientific.root / LEDGER_NAME
    if path.is_symlink():
        raise ValueError("Scientific workflow ledger cannot be a symlink")
    if not path.exists():
        return None
    ledger = WorkflowOperationLedger(path, readonly=True)
    try:
        _ = ledger.scientific
    except OperationBlocked as error:
        if error.state != "scientific_authority_unavailable":
            raise
        return None
    row = ledger._read(
        lambda db: db.execute(
            "SELECT 1 FROM scientific_records "
            "WHERE lineage_id=? AND kind='adaptive_event' LIMIT 1",
            (session_id,),
        ).fetchone()
    )
    return ledger if row is not None else None


def adaptive_cursor(scientific, session_id):
    """Recognize the adaptive owner before consulting a serial checkpoint."""
    if _adaptive_ledger(scientific, session_id) is not None:
        return {
            "mode": ADAPTIVE_MODE,
            "next": [],
            "phase": "adaptive",
            "status": "retained",
        }
    return None


def _startup_manifest(path):
    from devbench.core._files import read_bounded_json
    from devbench.engine.scientific_authority import _ID, _matches

    config = read_bounded_json(path, 65_536)
    required = {"format", "session_id", "context_id", "source_id"}
    formats = {
        "devbench.workbench-adaptive-startup/1",
        "devbench.workbench-adaptive-startup/2",
    }
    if (
        type(config) is not dict
        or not required <= config.keys()
        or config.keys() - {*required, "parameters", "mounted_image_id"}
        or config["format"] not in formats
        or type(config["session_id"]) is not str
        or not re.fullmatch("[a-f0-9]{32}", config["session_id"])
        or not all(_matches(_ID, config[key]) for key in ("context_id", "source_id"))
    ):
        raise ValueError(
            "Use a closed adaptive startup manifest with exact session and disclosure "
            "IDs"
        )
    mounted_image_id = config.get("mounted_image_id")
    if "mounted_image_id" in config:
        if (
            config["format"] != "devbench.workbench-adaptive-startup/2"
            or type(mounted_image_id) is not str
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", mounted_image_id)
        ):
            raise ValueError(
                "Mounted startup requires an exact local OCI image identity"
            )
    parameters = config.get("parameters")
    if "parameters" in config and (
        type(parameters) is not list
        or len(parameters) > 64
        or any(type(document) is not dict for document in parameters)
    ):
        raise ValueError("Provide at most 64 frozen OCI parameter objects")
    return config, mounted_image_id, parameters


def _mounted_startup(
    retained, session_id, image_id, controller, tier_a_executor, review, diagnostics
):
    from devbench.analysis.rna_diagnostic_policy import DiagnosticQualificationAuthority
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle
    from devbench.engine.tier_a_executor import TierAExecutor

    lifecycle, review_binding = review
    if (
        controller is None and tier_a_executor is None
        and lifecycle is None and review_binding is None and diagnostics is None
    ):
        ledger = WorkflowOperationLedger(retained.path)
        return ledger, TierAExecutor(
            ledger, allow_data_execution=True, mounted_image_id=image_id
        )
    if controller is None or tier_a_executor is not None:
        raise PermissionError(
            "Mounted startup requires the attached controller and one executor"
        )
    if (
        type(lifecycle) is not ReviewHoldLifecycle
        or lifecycle.lineage_id != session_id
        or lifecycle.ledger.path != retained.path
        or lifecycle.ledger.readonly
        or not callable(review_binding)
        or type(diagnostics) is not DiagnosticQualificationAuthority
    ):
        raise PermissionError(
            "Mounted startup requires current review and diagnostic capabilities"
        )
    ledger = lifecycle.ledger
    return ledger, TierAExecutor(
        ledger, allow_data_execution=True, mounted_image_id=image_id
    )


def _check_startup_attachment(retained, session_id, image, parameters, controller):
    if controller is not None or (image is None and parameters is None):
        return
    if AdaptiveWorkflow(retained, session_id).controller_state()["binding"] is not None:
        reason = "attached controller" if image else "bound controller"
        raise PermissionError(
            f"The {reason} requires its own current runtime capabilities"
        )
    if image is not None and parameters is not None:
        raise PermissionError("Mounted startup and manual OCI require separate routes")


def local_startup_binding(
    app,
    path,
    *,
    controller=None,
    tier_a_executor=None,
    review_lifecycle=None,
    review_binding=None,
    diagnostic_qualification=None,
):
    """Attach an entitled lineage using an optional in-process controller capability."""
    from devbench.analysis.evidence_access import EvidenceBroker

    scientific = app.scientific
    if scientific is None:
        raise ValueError("Adaptive startup requires the scientific workspace")
    if (
        not path.is_absolute()
        or path != path.resolve()
        or not any(
            path.is_relative_to(root) for root in (app.workspace, scientific.root)
        )
    ):
        raise ValueError(
            "Adaptive startup configuration must be an absolute in-project file"
        )
    config, mounted_image_id, parameters = _startup_manifest(path)
    execution_configured = (
        mounted_image_id is not None or parameters is not None
        or controller is not None or tier_a_executor is not None
    )
    if execution_configured and app.allow_data_execution is not True:
        raise PermissionError(
            "Local adaptive execution requires explicit startup "
            "data execution permission"
        )
    session_id = config["session_id"]
    scientific._plan(session_id)
    retained = _adaptive_ledger(scientific, session_id)
    if retained is None:
        raise PermissionError("Adaptive startup requires an existing admitted lineage")
    with retained._database() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='scientific_disclosure_schema'"
        ).fetchone():
            raise PermissionError(
                "Adaptive startup requires an existing disclosure authority"
            )
    broker = EvidenceBroker.open_existing(retained.scientific)
    broker.check_delivery(
        config["context_id"], config["source_id"], lineage_id=session_id,
        role="scientist",
    )
    _check_startup_attachment(
        retained, session_id, mounted_image_id, parameters, controller,
    )
    if mounted_image_id is not None:
        ledger, tier_a_executor = _mounted_startup(
            retained,
            session_id,
            mounted_image_id,
            controller,
            tier_a_executor,
            (review_lifecycle, review_binding),
            diagnostic_qualification,
        )
    else:
        ledger = (
            review_lifecycle.ledger
            if review_lifecycle is not None
            else WorkflowOperationLedger(retained.path)
        )
    delivery = EvidenceBroker.open_existing(ledger.scientific).delivery(
        config["context_id"], config["source_id"]
    )
    reviews = {
        "review_lifecycle": review_lifecycle,
        "review_binding": review_binding,
        "diagnostic_qualification": diagnostic_qualification,
    }
    from .workbench_interactive import local_thesis_lifecycle

    thesis_lifecycle = local_thesis_lifecycle(ledger, session_id)
    binding = WorkbenchAdaptiveBinding(
        session_id,
        delivery,
        controller=controller,
        tier_a_executor=tier_a_executor,
        thesis_lifecycle=thesis_lifecycle,
        **reviews,
    )
    binding.require_owner(scientific)
    if parameters is None:
        return binding
    executor = scientific.planner().exploratory_executor(session_id, ledger, parameters)
    return WorkbenchAdaptiveBinding(
        session_id,
        delivery,
        executor=executor,
        controller=controller,
        manual_local_oci=controller is None,
        tier_a_executor=tier_a_executor,
        thesis_lifecycle=thesis_lifecycle,
        **reviews,
    )


def _graph_revision(flow, *, connection=None):
    # Issuing a projection appends its own disclosure receipts. The browser
    # guards graph edits; dispatch takes a fresh full ledger revision below.
    return flow._read_projection(
        lambda db: db.execute(
            "SELECT coalesce(max(revision),0) FROM scientific_records "
            "WHERE lineage_id=? AND kind!='disclosure_event'",
            (flow.lineage_id,),
        ).fetchone()[0],
        connection=connection,
    )


def _model_allowed(app, plan):
    classification = plan.get("data_classification")
    return (
        app.allow_model_execution is True
        and classification in {"synthetic", "public"}
        and app.model_data_scope == classification
    )


def _can_open_local_model(app, plan, binding):
    from devbench.analysis.episode_codex import CodexActionSelector

    return (
        binding.controller is None
        and binding.thesis_lifecycle is not None
        and getattr(app, "interactive_adaptive", False) is True
        and app.allow_data_execution is True
        and _model_allowed(app, plan)
        and isinstance(app.scientific.selector, CodexActionSelector)
    )


def _flow(binding, ledger):
    from devbench.engine.partition_authority import PartitionAuthority

    partition = getattr(binding.executor, "partition", None)
    if partition is not None and (
        type(partition) is not PartitionAuthority
        or partition.authority.ledger.path != ledger.path
    ):
        raise PermissionError(
            "Adaptive Workbench requires the exact partition authority"
        )
    flow = AdaptiveWorkflow(ledger, binding.session_id, partition_check=partition)
    flow.manual_local_oci = binding.manual_local_oci
    return flow


def _readback_flow(scientific, binding, ledger):
    from devbench.analysis.scientific_review_holds import ReviewBinding

    from .method_planner import ScientificMethodPlanner
    from .workflow_actions import WorkflowActions

    flow = _flow(binding, ledger)
    if binding.review_lifecycle is None:
        return flow
    with flow._read_connection() as db:
        subjects = {
            step.step_id: step.purpose
            for step in flow._state(db).nodes.values()
            if step.primary
            or step.purpose
            in {
                "qualified_inference",
                RESEARCH_INFERENCE,
            }
        }
    current_bindings = {}
    for step_id in subjects:
        try:
            current_bindings[step_id] = ReviewBinding.model_validate(
                binding.review_binding(step_id)
            )
        except (ValueError, KeyError, PermissionError, OperationBlocked):
            continue

    def review_binding(step_id):
        if step_id not in current_bindings:
            raise OperationBlocked("completed_current_design_review_required")
        return current_bindings[step_id]

    planner = ScientificMethodPlanner(scientific.methods())
    actions = WorkflowActions(
        planner,
        ledger,
        actor="workbench-operator",
        selector=None,
        executor=binding.executor,
        review_lifecycle=binding.review_lifecycle,
        review_binding=review_binding,
        diagnostic_qualification=binding.diagnostic_qualification,
        controller=binding.controller,
        tier_a_executor=binding.tier_a_executor,
    )
    flow.review_admission = actions.adaptive_review_admission
    if binding.tier_a_executor is not None:
        for step_id, purpose in subjects.items():
            if purpose == RESEARCH_INFERENCE and step_id in current_bindings:
                try:
                    actions.prepare_research_admission(flow, step_id)
                except OperationBlocked:
                    continue
    return flow


def _diagnostic_readback(flow, entries):
    """Report retained check admission without calling external qualification."""
    checks = {}
    pending = [
        row["step_id"]
        for row in entries
        if row["entry_kind"] == "step"
        and row["role"] == "check"
        and row["status"] == "ready"
    ]
    if not pending:
        return checks
    with flow._read_connection() as db:
        state = flow._state(db)
        for step_id in pending:
            try:
                envelope = flow._envelope(state, step_id, "workbench-operator", db=db)
                _, acceptance, _ = flow._diagnostic_requirements(
                    db, state, envelope, admission_boundary=True
                )
            except OperationBlocked:
                checks[step_id] = "blocked"
            else:
                if acceptance is not None:
                    checks[step_id] = "dispatch_revalidation_required"
    return checks


def _publication_paths(app, session_id):
    binding = app._adaptive_bindings.get(session_id)
    registry = app.scientific.publication_registry
    delivery = app.evidence_delivery
    if binding is not None and binding.publication_registry is not None:
        registry = binding.publication_registry
        delivery = binding.delivery
    if (
        registry is None
        or registry.workflow.lineage_id != session_id
        or type(delivery) is not EvidenceDelivery
        or delivery.broker.authority.ledger.path != registry.ledger.path
    ):
        return None
    with registry.ledger._database() as db:
        db.execute("BEGIN")
        try:
            source = delivery.broker._source(db, delivery.source_id)
            subject = registry.subject_for_source(
                source["content_sha256"], connection=db, _lineage_only=True
            )
            if subject is None:
                return None
            descriptor = registry.descriptor(
                subject.subject_id, delivery=delivery, connection=db
            )
        except (PermissionError, OperationBlocked):
            return None
        if not (descriptor.allow_checked or descriptor.allow_descriptive):
            return None
        path = "/api/scientific/publications/" + subject.subject_id
        return {
            "step_id": subject.step_id,
            "generation": subject.generation,
            "summary": path + "?format=json",
            "figure": path + "?format=png"
            if descriptor.allow_descriptive and subject.check_id is None
            else None,
        }


class _StudioReadbackChanged(OperationConflict):
    """A worker committed a new graph revision during readback."""


def adaptive_view(app, session_id, *, cursor=None):
    # A fresh poll can retry if a worker commits between the graph and result
    # projections. A retained cursor still requires its original revision.
    for attempt in range(3):
        can_retry = cursor is None and attempt < 2
        try:
            return _adaptive_view_once(app, session_id, cursor=cursor)
        except _StudioReadbackChanged as error:
            if cursor is not None:
                raise OperationConflict(str(error)) from error
            if not can_retry:
                raise
        except OperationBlocked as error:
            if cursor is not None or error.state != "adaptive_projection_changed_before_delivery":
                raise
            if not can_retry:
                raise _StudioReadbackChanged(str(error)) from error


def _adaptive_view_once(app, session_id, *, cursor):
    from devbench.analysis.rna_investigation_projection import project_adaptive_dag

    scientific = app.scientific
    scientific._plan(session_id)
    view = {
        "session_id": session_id,
        "mode": "serial",
        "qualification": "not_established",
        "raw_matrix_rows": 0,
    }
    if _adaptive_ledger(scientific, session_id) is None:
        return view
    view.update(mode=ADAPTIVE_MODE, graph_status="current_delivery_required")
    from .workbench_interactive import ensure_adaptive_binding

    binding = ensure_adaptive_binding(app, session_id)
    if binding is None:
        return view
    plan, ledger = binding.require_owner(scientific)
    revision = _graph_revision(_flow(binding, ledger))
    flow = _readback_flow(scientific, binding, ledger)
    graph = project_adaptive_dag(
        flow,
        evidence_delivery=binding.delivery,
        cursor=cursor,
        narrative_delivery=binding.narrative_delivery,
    )
    narrative = graph.pop("controller_narrative", None)
    entitled_outcome = (narrative or {}).get("controller_outcome") or {}
    open_review_holds = [
        {"hold_id": hold["hold_id"], "scope": list(hold["scope"])}
        for hold in entitled_outcome.get("open_holds", ())
    ]
    controller = flow.controller_state()
    ready = bool(flow.ready()) if binding.controller is None else None
    diagnostic_readback = _diagnostic_readback(flow, graph["entries"])
    publication = _publication_paths(app, session_id)
    if _graph_revision(flow) != revision:
        raise _StudioReadbackChanged("Adaptive graph changed while preparing its view")
    model_required = binding.controller is not None or controller["binding"] is not None
    execution_block = graph["execution_block"]
    executable = binding.executor is not None or binding.tier_a_executor is not None
    runnable = (
        executable
        and app.allow_data_execution is True
        and (
            binding.controller is not None
            and _model_allowed(app, plan)
            and execution_block != "scientific_cancelled"
            if model_required
            else execution_block is None and ready
        )
    )
    check_counts = Counter(
        row["status"]
        for row in graph["entries"]
        if row["entry_kind"] == "step" and row["role"] == "check"
    )
    cleanup_pending = (
        execution_block == "scientific_cancelled"
        and binding.tier_a_executor is not None
        and bool(flow._unresolved_dispatches(binding.tier_a_executor))
    )
    from .workbench_interactive import operator_bulk_view

    view.update(
        status="available",
        operator_bulk=operator_bulk_view(app, binding, flow),
        plugin_linked=binding.delivery.context_id == "plugin-scientist-" + session_id,
        campaign_mode=binding.campaign_mode,
        graph_status="available",
        revision=revision,
        graph=graph,
        diagnostic_checks_on_page=dict(check_counts),
        replay_available=binding.replay_package is not None,
        diagnostic_admission=diagnostic_readback,
        publication=publication,
        open_review_holds=open_review_holds,
        actions={
            "advance": runnable and controller["outcome"] is None,
            "resume": runnable
            and controller["outcome"] is not None
            and binding.controller is not None,
            "cancel": executable
            and (execution_block != "scientific_cancelled" or cleanup_pending),
            "model_required": model_required,
            "model_available": (
                execution_block is None and _can_open_local_model(app, plan, binding)
            ),
            "run_checked_step": _approved_qc_candidate(app, binding, flow),
            "run_operator_steps": _operator_candidates(app, binding, flow),
        },
    )
    if cursor is None:
        view.update(_studio_readback(app, binding, flow, plan, revision))
    return view


def _studio_readback(app, binding, flow, plan, revision) -> dict[str, Any]:
    # The caller has already required the same owner and projected the guarded graph.
    if app._adaptive_bindings.get(flow.lineage_id) is not binding:
        raise PermissionError("Studio evidence requires the current session binding")
    extra: dict[str, Any] = {"packages": []}
    classification = plan["data_classification"]
    if classification not in {"public", "synthetic"} and not app.offline_only:
        return extra
    try:
        if (
            binding.delivery.context_id == "plugin-scientist-" + flow.lineage_id
            and classification in {"public", "synthetic"}
        ):
            plugin = _plugin_projection(app, binding)
            if plugin is None:
                return extra
            extra["plugin_session"] = plugin
            _require_package_disclosure(binding, flow.ledger)
            extra["packages"] = _retained_packages(
                app, flow, revision, _package_ids(app.workspace)
            )
        else:
            extra["session_summary"] = _studio_labels(app.scientific, plan)
            extra["packages"] = _local_packages(app, binding, flow, revision)
    except (OperationBlocked, OSError, sqlite3.DatabaseError):
        pass  # Keep the admitted graph usable if optional retained evidence is absent.
    if (
        app.offline_only and classification == "private"
        and binding.delivery.context_id == "plugin-scientist-" + flow.lineage_id
    ):
        extra.update(_offline_plugin_results(app, flow.lineage_id))
    if _graph_revision(flow) != revision:
        raise _StudioReadbackChanged(
            "Adaptive graph changed while preparing Studio evidence"
        )
    return extra


def _offline_plugin_results(app, session_id):
    from .plugin_graph_studio import studio_view as private_workflow_view
    from .plugin_motor_umap import studio_view
    from .workbench_local_analysis import local_analysis_view
    from .workbench_retained_comparison import local_comparison_view

    results = {}
    for key, reader in (
        ("retained_comparison", local_comparison_view),
        ("shared_motor_umap", studio_view),
        ("local_analysis", local_analysis_view),
        ("private_workflow", private_workflow_view),
    ):
        try:
            value = reader(app, session_id)
        except (OperationBlocked, OSError, ValueError, KeyError, sqlite3.DatabaseError):
            continue
        if value is not None:
            results[key] = value
    return results


def _require_fresh_execution(app, request):
    if (
        request.get("allow_data_execution") is not True
        or app.allow_data_execution is not True
    ):
        raise PermissionError(
            "A fresh data execution grant and startup access are required"
        )


def _adaptive_action_name(app, request):
    if type(request) is not dict or type(request.get("expected_revision")) is not int:
        raise ValueError("Adaptive actions require the current graph revision")
    name = request.get("action")
    base = {"action", "expected_revision"}
    if name == "cancel" and request.keys() == base:
        return name
    fields = base | {"allow_data_execution"}
    if name in {"advance", "resume"} and request.keys() <= fields | {
        "allow_model_execution"
    }:
        _require_fresh_execution(app, request)
        return name
    if name in {"queue_operator_bulk", "review_operator_bulk"}:
        if name == "review_operator_bulk":
            fields |= {"touchpoint", "assessment"}
            if type(request.get("assessment")) is not dict:
                raise ValueError("Supply explicit operator bulk review assessments")
    elif name in {"run_checked_step", "run_operator_step"}:
        fields |= {"step_id", "generation"}
        if (
            type(request.get("generation")) is not int
            or type(request.get("step_id")) is not str
        ):
            raise ValueError("Approve the exact queued step generation")
    else:
        raise ValueError("Unknown adaptive workflow action")
    if request.keys() != fields:
        raise ValueError("Use the declared adaptive action fields")
    _require_fresh_execution(app, request)
    return name


def _approved_qc_candidate(app, binding, flow):
    if binding.tier_a_executor is None or app.allow_data_execution is not True:
        return None
    with flow._read_connection() as db:
        controller = flow.controller_state(connection=db)
        if (
            (controller["binding"] or {}).get("scope") != "local_interactive_codex"
            or controller["pending"] is not None
        ):
            return None
        state = flow._state(db)
        selected = {
            row["result"].get("step_id") for row in controller["decisions"]
            if row["result"].get("kind") == "insert"
            and row["result"].get("status") == "applied"
        }
        ready = [
            name for name in flow._status(db, state)["ready"]
            if name in selected and state.generations[name] == 1
            and state.nodes[name].operation_id == "scrna_batch_qc"
            and state.nodes[name].purpose == "checked_observation"
        ]
    if len(ready) != 1:
        return None
    return {"step_id": ready[0], "generation": 1}


def _operator_candidates(app, binding, flow):
    from devbench.engine.tier_a_scrna import is_single_cell_operation

    from .thesis_workflow import has_thesis_scope
    from .workbench_interactive import _OPERATOR_BULK_STEP, operator_bulk_view

    if binding.tier_a_executor is None or app.allow_data_execution is not True:
        return []
    with flow._read_connection() as db:
        controller = flow.controller_state(connection=db)
        if controller["pending"] is not None:
            return []
        state = flow._state(db)
        selected = {
            row["result"].get("step_id")
            for row in controller["decisions"]
            if row["result"].get("kind") == "insert"
            and row["result"].get("status") == "applied"
        }
        ready = [
            {"step_id": name, "generation": state.generations[name]}
            for name in flow._status(db, state)["ready"]
            if name not in selected
            and is_single_cell_operation(state.nodes[name].operation_id)
            and state.nodes[name].purpose == "checked_observation"
            and not has_thesis_scope(state.nodes[name])
        ]
    bulk = operator_bulk_view(app, binding, flow)
    if bulk.get("run") is True:
        ready.append({"step_id": _OPERATOR_BULK_STEP, "generation": bulk["generation"]})
    return ready


def _run_operator_step(app, binding, flow, request, revision):
    from .workbench_interactive import _OPERATOR_BULK_STEP, operator_bulk_actions
    from .workflow_actions import WorkflowActions

    supplied = {key: request[key] for key in ("step_id", "generation")}
    if supplied not in _operator_candidates(app, binding, flow):
        raise OperationBlocked("current_operator_step_required")
    planner = app.scientific.planner()
    actions = (
        operator_bulk_actions(flow, binding.tier_a_executor, planner)
        if supplied["step_id"] == _OPERATOR_BULK_STEP
        else WorkflowActions(
            planner,
            flow.ledger,
            actor="workbench-operator",
            selector=None,
            allow_data_execution=True,
            tier_a_executor=binding.tier_a_executor,
        )
    )
    actions.perform_adaptive(flow, supplied["step_id"], expected_revision=revision)
    return {
        "mode": ADAPTIVE_MODE,
        "steps_dispatched": 1,
        "revision": _graph_revision(flow),
    }


def _run_approved_qc(app, binding, flow, request, revision):
    from .workflow_actions import WorkflowActions

    supplied = {key: request[key] for key in ("step_id", "generation")}
    if _approved_qc_candidate(app, binding, flow) != supplied:
        raise OperationBlocked("current_checked_observation_required")
    actions = WorkflowActions(
        app.scientific.planner(), flow.ledger, actor="workbench-operator",
        selector=None, allow_data_execution=True,
        tier_a_executor=binding.tier_a_executor,
    )
    actions.perform_adaptive(flow, supplied["step_id"], expected_revision=revision)
    return {
        "mode": "adaptive/1", "steps_dispatched": 1,
        "revision": _graph_revision(flow),
    }


def _cancel_adaptive_action(
    flow,
    ledger,
    session_id,
    ledger_revision,
    controller_state,
):
    result = flow.authority.cancel_execution(
        session_id,
        actor="workbench-operator",
        expected_revision=ledger_revision,
    )
    if controller_state["binding"] is not None:
        try:
            ledger.cancel_campaign(controller_state["binding"]["campaign_id"])
        except (RuntimeError, OSError, sqlite3.Error):
            return {
                "status": "reconciliation_required",
                "reason": "campaign_cancellation_unverified",
                "revision": result["revision"],
                "cleanup_status": "unverified",
            }
    return {
        "status": "cancel_requested",
        "revision": result["revision"],
        "cleanup_status": "unverified",
    }


def _reconcile_cancelled_cleanup(flow, tier_a_executor):
    if tier_a_executor is None:
        raise OperationBlocked("scientific_cancelled")
    flow.reconcile_pending(tier_a_executor)
    pending = flow._unresolved_dispatches(tier_a_executor)
    return {
        "status": "reconciliation_required" if pending else "cancel_requested",
        "reason": "worker_cleanup_unverified" if pending else None,
        "revision": _graph_revision(flow),
        "cleanup_status": "unverified" if pending else "confirmed",
    }


def _require_adaptive_controller_action(app, plan, request, name, controller, state):
    model_required = controller is not None or state["binding"] is not None
    if model_required and (
        controller is None
        or request.get("allow_model_execution") is not True
        or not _model_allowed(app, plan)
    ):
        raise PermissionError(
            "This adaptive step requires a fresh model grant for its exact data scope"
        )
    if (
        not model_required
        and "allow_model_execution" in request
        and request["allow_model_execution"] is not False
    ):
        raise PermissionError(
            "The configured adaptive route does not supply a model controller"
        )
    if name == "resume" and (controller is None or state["outcome"] is None):
        raise OperationBlocked("adaptive_controller_has_no_retained_stop_to_resume")
    if name == "advance" and state["outcome"] is not None:
        raise OperationBlocked("resume_requires_explicit_fresh_workbench_request")


def _open_requested_controller(app, plan, binding, flow, request):
    from .workbench_interactive import bind_local_controller

    if (
        request.get("action") != "advance"
        or request.get("allow_model_execution") is not True
        or not _can_open_local_model(app, plan, binding)
    ):
        return binding
    with app._lock:
        if (
            binding.session_id in app._adaptive_operations
            or _graph_revision(flow) != request["expected_revision"]
        ):
            raise OperationConflict("Adaptive revision changed before model enrolment")
        updated = bind_local_controller(app, binding)
        app.bind_adaptive_workflow(updated)
        return updated


def _authorize_adaptive_advance(
    app, plan, binding, flow, request, name, state, revision,
):
    updated = _open_requested_controller(app, plan, binding, flow, request)
    if updated is not binding:
        revision = _graph_revision(flow)
        state = flow.controller_state()
    _require_adaptive_controller_action(
        app, plan, request, name, updated.controller, state,
    )
    return updated, revision


def adaptive_action(app, session_id, request):
    name = _adaptive_action_name(app, request)
    scientific = app.scientific
    if _adaptive_ledger(scientific, session_id) is None:
        raise PermissionError("The selected session has no admitted adaptive workflow")
    from .workbench_interactive import ensure_adaptive_binding

    binding = ensure_adaptive_binding(app, session_id)
    if binding is None or binding.executor is None and binding.tier_a_executor is None:
        raise PermissionError(
            "Adaptive actions require a current coordinator runtime binding"
        )
    plan, ledger = binding.require_owner(scientific)
    flow = _flow(binding, ledger)
    revision = request["expected_revision"]
    with flow._read_connection() as db:
        if _graph_revision(flow, connection=db) != revision:
            raise OperationConflict(
                "Adaptive graph revision changed; refresh before retrying"
            )
        ledger_revision = flow.authority._revision(db, session_id)
        controller_state = flow.controller_state(connection=db)
        execution = flow.authority._load_state(db, session_id)
    if name == "cancel":
        if execution is not None and execution["status"] == "cancelled":
            return _reconcile_cancelled_cleanup(flow, binding.tier_a_executor)
        return _cancel_adaptive_action(
            flow,
            ledger,
            session_id,
            ledger_revision,
            controller_state,
        )
    if execution is not None and execution["status"] == "cancelled":
        raise OperationBlocked("scientific_cancelled")
    app._require_current_runtime()
    if name in {"advance", "resume"}:
        binding, revision = _authorize_adaptive_advance(
            app, plan, binding, flow, request, name, controller_state, revision,
        )
    with app._lock:
        with flow._read_connection() as db:
            if session_id in app._adaptive_operations or _graph_revision(
                flow, connection=db
            ) != revision:
                raise OperationConflict(
                    "Adaptive operation already active or graph revision changed"
                )
            ledger_revision = flow.authority._revision(db, session_id)
        app._adaptive_operations.add(session_id)
    try:
        return _dispatch_adaptive_action(
            app, binding, flow, name, request, ledger_revision,
        )
    finally:
        with app._lock:
            app._adaptive_operations.discard(session_id)


def _dispatch_adaptive_action(app, binding, flow, name, request, ledger_revision):
    from .workflow_entrypoints import advance_adaptive_workflow

    if name == "run_checked_step":
        return _run_approved_qc(app, binding, flow, request, ledger_revision)
    if name == "run_operator_step":
        return _run_operator_step(app, binding, flow, request, ledger_revision)
    if name in {"queue_operator_bulk", "review_operator_bulk"}:
        return _apply_operator_bulk(app, binding, flow, name, request)
    controller = binding.controller
    planner = (
        controller.invoker.planner
        if controller is not None
        else app.scientific.planner()
    )
    result = advance_adaptive_workflow(
        planner,
        flow.lineage_id,
        actor="workbench-operator",
        executor=binding.executor,
        ledger=flow.ledger,
        partition_check=flow.partition_check,
        max_steps=1,
        allow_data_execution=True,
        review_lifecycle=binding.review_lifecycle,
        review_binding=binding.review_binding,
        controller=controller,
        diagnostic_qualification=binding.diagnostic_qualification,
        diagnostic_actions=binding.diagnostic_actions,
        resume_controller=name == "resume",
        expected_revision=ledger_revision,
        manual_local_oci=binding.manual_local_oci,
        tier_a_executor=binding.tier_a_executor,
    )
    return {
        "mode": result["mode"],
        "steps_dispatched": result["steps_dispatched"],
        "revision": _graph_revision(flow),
    }


def _apply_operator_bulk(app, binding, flow, name, request):
    from .workbench_interactive import (
        _OPERATOR_BULK_STEP,
        operator_bulk_view,
        queue_operator_bulk,
        review_operator_bulk,
    )

    executor = binding.tier_a_executor
    view = operator_bulk_view(app, binding, flow)
    if name == "queue_operator_bulk" and view.get("queue") is True:
        queue_operator_bulk(flow, executor)
    elif name == "review_operator_bulk" and request["touchpoint"] in view.get(
        "review", []
    ):
        review_operator_bulk(
            flow,
            executor,
            _OPERATOR_BULK_STEP,
            request["touchpoint"],
            request["assessment"],
        )
    else:
        raise OperationBlocked("current_operator_bulk_review_required")
    return {"revision": _graph_revision(flow)}


def verify_adaptive_replay(app, session_id):
    """Verify a startup-bound public package without renewing publication use."""
    from devbench.analysis.bundles import verify_public_replay_package

    binding = app._adaptive_bindings.get(session_id)
    if binding is None or binding.replay_package is None:
        raise KeyError("No retained public replay package is bound to this session")
    app.scientific._plan(session_id)
    binding.require_replay_path(app.scientific)
    result = verify_public_replay_package(
        binding.replay_package, binding.replay_root_sha256
    )
    return {
        key: result[key]
        for key in (
            "verified",
            "root_sha256",
            "mode",
            "current_use",
            "scientific_acceptance",
            "numerical_stage_launches",
            "model_sessions",
        )
    }


def diagnostic_registry_view():
    from devbench.analysis.rna_diagnostic_policy import (
        registered_rna_diagnostic_policies,
    )

    policies = [
        policy.to_dict() for policy in registered_rna_diagnostic_policies().values()
    ]
    return {
        "policies": [
            {
                "family": policy["family"],
                "version": policy["version"],
                "qualification": policy["qualification"]["status"],
            }
            for policy in policies
        ]
    }


def release_evaluation_view():
    from devbench.analysis.rna_release_evaluation import (
        unratified_protocol,
        validate_release_protocol,
    )

    result = validate_release_protocol(unratified_protocol())
    return {
        "protocol_id": result["protocol_id"],
        "protocol_sha256": result["protocol_sha256"],
        "admission": result["admission"],
        "blocker_count": len(result["blockers"]),
        "gates": dict.fromkeys(("R0", "R1", "X8", "X9", "clinical_release"), "blocked"),
        "model_calls": result["model_calls"],
        "numerical_jobs": result["numerical_jobs"],
    }
