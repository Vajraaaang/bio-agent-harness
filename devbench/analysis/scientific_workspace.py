"""Single-project browser adapter over scientific metadata and existing controllers.

The service owns admission and exact references. MethodService and the planner
remain the execution authorities; operation rows are UI activity, never a lease
or permission to repeat work after restart.
"""
from copy import deepcopy
from pathlib import Path
import mimetypes
import re
import sqlite3
import uuid

from .episode_agent import selector_configuration
from .method_planner import ScientificMethodPlanner
from .methods import MethodService, _digest, _label, _reason, _now
from .scientific_objects import ScientificObjectService
from .workbench_ui import _canonical, _copy
from .workflow_entrypoints import (workflow_action, refresh_workspace_context, is_rna_plan,
    require_completed_parent, governed_followup, advance_workflow_to_gate)


class ScientificWorkspace:
    def __init__(self, app, workspace, *, selector=None, executor=None, restore_backup=None, scientific_workflow=None):
        self.app = app
        self.root = _canonical(workspace)
        if self.root == app.workspace or any(self.root.is_relative_to(root) or root.is_relative_to(self.root)
                                               for root in app.input_roots):
            raise ValueError("Scientific storage must be separate from input roots and the service root")
        self.project_id = app.workspace_state()["project"]["id"]
        self.selector, self.executor, self.futures = selector, executor, {}
        self.scientific_workflow = scientific_workflow
        self.recovery_notice = None
        if restore_backup is not None:
            self.recovery_notice = ScientificObjectService.restore_backup(self.root, _canonical(restore_backup),
                self.project_id, actor="local-explicit-recovery")
        self.selector_config = selector_configuration(selector) if selector is not None else None
        if self.selector_config and self.selector_config.get("backend") not in {"codex-cli", "recorded_fixture"}:
            raise ValueError("Use the protected Codex selector or explicit recorded method decisions")
        if self.selector_config and self.selector_config["backend"] == "codex-cli":
            private = getattr(selector, "private_root", None)
            if private is None or not self.root.is_relative_to(private):
                raise ValueError("Method selector must protect the entire scientific store")
        self.initialized()  # Validate existing identity/schema without initialization or recovery.

    def initialized(self):
        database = _canonical(self.root / "methods.sqlite3")
        if not database.exists():
            return False
        try:
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                version = db.execute("SELECT version FROM schema_version").fetchall()
            if version == [(1,)]:
                return False
            if version != [(2,)]:
                raise ValueError("Unsupported scientific store schema")
            self.store().snapshot()
            return True
        except sqlite3.DatabaseError as error:
            raise ValueError("Scientific metadata is unavailable; preserve it and explicitly recover a validated backup") from error

    def store(self, *, write=False):
        return ScientificObjectService(self.root, self.project_id, readonly=not write)

    def methods(self, *, write=False):
        return MethodService(self.root, readonly=not write, initialize=False, reconcile=False)

    def planner(self):
        return ScientificMethodPlanner(self.methods(write=True))

    def configuration(self):
        mode = self.selector_config.get("backend") if self.selector_config else "disabled"
        return {"enabled": True, "initialized": self.initialized(), "planning_mode": mode,
                "model_execution_allowed": self.app.allow_model_execution,
                "data_execution_allowed": self.app.allow_data_execution,
                "scientific_workflow_configured": self.scientific_workflow is not None,
                "actor_identity": "caller-asserted within the authenticated local session", "recovery_notice": self.recovery_notice}

    def catalog(self):
        if not self.initialized():
            return {"project_id": self.project_id, "revision": 0, "initialized": False, "recovery_notice": self.recovery_notice,
                    **{key: [] for key in ("studies", "datasets", "analyses", "runs", "attempts", "results",
                                           "selections", "plans", "methods", "operations", "workflow_steps", "imports", "legacy_method_runs")}}
        store, methods = self.store(), self.methods()
        value = store.snapshot()
        value["initialized"] = True
        value["recovery_notice"] = self.recovery_notice
        value["methods"] = methods.list_records("method")
        method_names = {item["version_id"]: item["contract"]["name"] for item in value["methods"]}
        value["legacy_method_runs"] = []
        for native in methods.list_records("run"):
            if native.get("correlation") is not None:
                continue
            branch = methods.get("branch", native["branch_id"])
            value["legacy_method_runs"].append({"run_id": native["run_id"], "analysis_id": branch["analysis_id"],
                "method_version_id": native["method_version_id"], "method_name": method_names.get(native["method_version_id"], "Unavailable method"),
                "status": native["status"]})
        value["dataset_versions"] = store.list("dataset_version")
        value["analysis_versions"] = store.list("analysis_version")
        value["result_versions"] = store.list("result_version")
        value["validations"] = store.list("validation")
        value["reviews"] = store.list("review")
        value["plans"] = [state for state in methods.list_records("method_plan")
                          if state.get("scientific_anchors", {}).get("project_id") == self.project_id]
        value["workflow_steps"] = self._workflow_activity(value["plans"])
        value["operations"] = []
        for operation in methods.list_records("workspace_operation"):
            if operation["project_id"] != self.project_id:
                continue
            row = deepcopy(operation)
            if row["status"] in {"queued", "running"} and row["operation_id"] not in self.futures:
                row.update(status="recovery_required", worker_liveness="unknown",
                    notice="A previous service owned this operation. Inspect its retained controller evidence; no work was restarted.")
            value["operations"].append(row)
        value["imports"] = methods.list_records("scientific_import")
        for attempt in value["attempts"]:
            if attempt["status"] == "reserved":
                try:
                    controller = methods.get("run", attempt["controller_id"])
                    attempt["controller_status"] = controller["status"]
                except ValueError:
                    attempt["controller_status"] = "unavailable"
                attempt["worker_liveness"] = "unknown"
        return value

    def _workflow_activity(self, plans):
        """Read current per-node receipts without initializing or resuming work."""
        from .workflow_entrypoints import LEDGER_NAME
        from .workflow_operations import WorkflowOperationLedger
        from .workflow_state import SERIAL_ROUTE
        path = self.root / LEDGER_NAME
        if path.is_symlink():
            raise ValueError("Scientific workflow ledger cannot be a symlink")
        if not path.exists():
            return []
        ledger = WorkflowOperationLedger(path, readonly=True)
        rows = []
        for plan in plans:
            if not is_rna_plan(plan):
                continue
            for record in ledger.replay(plan["session_id"]):
                if record["node"] not in SERIAL_ROUTE:
                    continue
                update = (record.get("result") or {}).get("update", {})
                rows.append({"session_id": plan["session_id"], "node": record["node"],
                    "attempt": record["attempt"], "status": record["status"],
                    "controller_status": update.get("status"), "error_type": record["error_type"],
                    "request_sha256": record["request_digest"], "result_sha256": record["result_digest"],
                    "evidence": deepcopy((record.get("result") or {}).get("evidence"))})
        order = {node: index for index, node in enumerate(SERIAL_ROUTE)}
        return sorted(rows, key=lambda row: (row["session_id"], order[row["node"]], row["attempt"]))

    def resolve(self, reference):
        try:
            store = self.store()
            value = store.resolve(reference)
            if reference["object_type"] == "result":
                value = {**value, "review": store.get("result", value["result_id"]),
                    "validation": store.get("validation", value["validation_id"]),
                    "attempt": store.get("attempt", value["attempt_id"]), "run": store.get("run", value["run_id"])}
            return value
        except (ValueError, OSError) as error:
            raise KeyError("Exact scientific version unavailable or changed: " + str(error)) from error

    def download(self, artifact_id):
        store = self.store()
        linked = {member["artifact_id"] for row in store.list("dataset_version") for member in row["manifest"]["members"].values()}
        linked.update(identity for row in store.list("result_version") for identity in row["artifact_ids"])
        if artifact_id not in linked:
            raise KeyError("Artifact is not linked to a scientific record in this project")
        methods = self.methods()
        record = methods.get("artifact", artifact_id)
        path = methods.artifact_path(artifact_id)
        name = artifact_id + record["suffix"]
        return path, mimetypes.guess_type(name)[0] or "application/octet-stream", name

    def method_preview(self, version_id):
        if not self.initialized():
            raise KeyError("Scientific methods are not bound to this project")
        methods = self.methods()
        method = methods.method(version_id)
        source = methods.artifact_path(method["source_artifact"])
        with source.open("rb") as stream:
            content = stream.read(200001)
        return {**method, "source_text": content[:200000].decode("utf-8", errors="replace" if len(content) > 200000 else "strict"), "source_truncated": len(content) > 200000,
                "source_sha256": methods.get("artifact", method["source_artifact"])["sha256"],
                "execution_started": False, "source_scope": "Retained implementation text; viewing does not execute or approve it"}

    @staticmethod
    def _fields(body, required=(), optional=()):
        common = {"action", "expected_revision", "actor"}
        if set(body) - (common | set(required) | set(optional)) or not set(required) <= set(body):
            raise ValueError("Use only the declared fields for this scientific action")
        for key in set(required) | set(optional):
            if key not in body:
                continue
            if (key.endswith("_id") or key in {"name", "aim", "objective", "computational_task", "rationale", "candidate", "idempotency_key", "source_kind", "justification"}):
                if not isinstance(body[key], str) or not body[key].strip():
                    raise ValueError("Use a nonempty string for " + key)
            if key.endswith("_ref") and not isinstance(body[key], dict):
                raise ValueError("Use an exact typed reference for " + key)

    def _plan(self, identity):
        state = self.methods().get("method_plan", identity)
        if state.get("scientific_anchors", {}).get("project_id") != self.project_id:
            raise ValueError("Planner session is not anchored to this project")
        return state

    def _save_plan_context(self, state, **fields):
        updated = deepcopy(state)
        updated.update(fields)
        saved = self.planner()._save(state, updated, actor="workspace-controller", action="scientific_context_linked")
        refresh_workspace_context(self.planner(), state, saved, scientific_workflow=self.scientific_workflow)
        return saved

    def _operation(self, operation, *, new=False):
        methods, store = self.methods(write=True), self.store(write=True)
        with methods._db() as db:
            methods._put(db, "workspace_operation", operation["operation_id"], operation, update=not new)
            store.touch(actor=operation["actor"], action="workspace_operation", payload={
                "operation_id": operation["operation_id"], "status": operation["status"], "action": operation["action"]}, db=db)

    def _queue(self, name, actor, function, *, run_id=None, request=None):
        operation = {"operation_id": uuid.uuid4().hex, "project_id": self.project_id, "action": name,
                     "actor": actor, "status": "queued", "created_at": _now(), "run_id": run_id}
        if request is not None:
            operation["request"] = deepcopy(request)
        self._operation(operation, new=True)

        def perform():
            try:
                self.app._require_current_runtime()
                operation.update(status="running")
                self._operation(operation)
                value = function()
                operation.update(status="completed", value=value)
            except Exception as error:
                operation.update(status="failed", error={"type": type(error).__name__, "message": str(error)[:2000]})
            finally:
                operation["finished_at"] = _now()
                self._operation(operation)
            return _copy(operation)

        self.futures[operation["operation_id"]] = self.app._executor.submit(perform)
        return {"operation_id": operation["operation_id"], "run_id": run_id, "status": "queued"}

    def _advance_descriptor(self, body, actor):
        self._fields(body, ("session_id", "idempotency_key"),
            ("allow_model_execution", "method_version_ids", "phase"))
        if type(body.get("allow_model_execution", False)) is not bool:
            raise ValueError("Use an explicit boolean model-execution permission")
        phase = body.get("phase", "planning")
        if type(phase) is not str or phase not in {"planning", "synthesis"}:
            raise ValueError("Use planning or synthesis advancement")
        versions = body.get("method_version_ids")
        if versions is not None and (type(versions) is not list or len(versions) > 64
                or any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None for value in versions)
                or len(set(versions)) != len(versions)):
            raise ValueError("Use at most 64 distinct exact method-version identities")
        return {"session_id": body["session_id"], "idempotency_key": _label(body["idempotency_key"]),
            "actor": actor, "phase": phase, "method_version_ids": deepcopy(versions),
            "allow_model_execution": body.get("allow_model_execution", False)}

    def _advance_to_gate(self, body, actor):
        request = self._advance_descriptor(body, actor)
        state = self._plan(request["session_id"])
        if not is_rna_plan(state):
            raise ValueError("Automatic graph advancement requires an RNA scientific plan")
        versions = request["method_version_ids"]
        if versions is None:
            versions = [row["version_id"] for row in self.methods().list_records("method")]
        planner = self.planner()
        return self._queue("advance_to_gate", actor, lambda: advance_workflow_to_gate(
            planner, state["session_id"], actor=actor, selector=self.selector, version_ids=versions,
            scientific_workflow=self.scientific_workflow, phase=request["phase"],
            allow_model_execution=self.app.allow_model_execution and request["allow_model_execution"],
            before_advance=self.app._require_current_runtime), request=request)

    def _scientific_stage(self, action, body, actor):
        self._fields(body, ("session_id",), ("allow_model_execution",))
        state = self._plan(body["session_id"])
        if self.scientific_workflow is None:
            raise PermissionError("RNA actions require a trusted scientific workflow runtime for discovery, implementation and independent reviews")
        planner = self.planner()
        return self._queue(action, actor, lambda: workflow_action(planner, state["session_id"], action,
            actor=actor, selector=self.selector, scientific_workflow=self.scientific_workflow,
            allow_model_execution=self.app.allow_model_execution and body.get("allow_model_execution") is True))

    def _model_action(self, action, body, actor):
        self._fields(body, ("session_id",), ("allow_model_execution", "method_version_ids", "candidate", "method_version_id", "justification"))
        state = self._plan(body["session_id"])
        if action == "draft_method" and is_rna_plan(state):
            raise ValueError("RNA implementation must use the explicit implementation graph stage and its frozen trusted requests")
        if self.selector is None:
            raise PermissionError("No scientific planning selector is configured")
        authorized = self.app.allow_model_execution and body.get("allow_model_execution") is True
        if self.selector_config["backend"] != "recorded_fixture" and not authorized:
            raise PermissionError("This operation requires separately enabled and explicitly requested model execution")
        planner = self.planner()
        options = {"actor": actor, "allow_model_execution": authorized}
        def select():
            if action == "science":
                return workflow_action(planner, state["session_id"], "generate", selector=self.selector, scientific_workflow=self.scientific_workflow, **options)
            if action == "resolve":
                versions = body.get("method_version_ids")
                if versions is None:
                    versions = [row["version_id"] for row in self.methods().list_records("method")]
                return workflow_action(planner, state["session_id"], "resolve", selector=self.selector, version_ids=versions, scientific_workflow=self.scientific_workflow, **options)
            if action == "design_trials":
                return workflow_action(planner, state["session_id"], "design", selector=self.selector, scientific_workflow=self.scientific_workflow, **options)
            if action == "recommend":
                return workflow_action(planner, state["session_id"], "recommend", selector=self.selector, scientific_workflow=self.scientific_workflow, **options)
            if not {"candidate", "method_version_id", "justification"} <= set(body):
                raise ValueError("Source drafting requires a candidate, exact interface method and justification")
            methods = self.methods()
            method = methods.method(body["method_version_id"])
            if method["environment_artifact"] is None:
                raise ValueError("A custom source draft requires an existing pinned environment lock")
            return planner.draft_implementation(state["session_id"], body["candidate"], self.selector,
                method["contract"], methods.artifact_path(method["environment_artifact"]),
                justification=body["justification"], **options)
        return self._queue(action, actor, select)

    def action(self, body):
        if not isinstance(body, dict) or type(body.get("expected_revision")) is not int:
            raise ValueError("Scientific mutations require expected_revision")
        name, actor = body.get("action"), _label(body.get("actor", "local-researcher"))
        with self.app._lock:
            self.app._require_current_runtime()
            catalog = self.catalog()
            if name == "advance_to_gate":
                requested = self._advance_descriptor(body, actor)
                for previous in catalog["operations"]:
                    if (previous["action"] == name and previous.get("request", {}).get("idempotency_key")
                            == requested["idempotency_key"]):
                        if previous["request"] != requested:
                            raise ValueError("Conflicting idempotency key payload")
                        return {"operation_id": previous["operation_id"], "run_id": previous.get("run_id"),
                            "status": previous["status"]}
            # A replay of the same execution request returns its retained intent;
            # it never renews a permission or dispatches an abandoned attempt.
            if name in {"run_trial", "rerun"} and body.get("idempotency_key"):
                for run in catalog["runs"]:
                    if run["idempotency_key"] == body["idempotency_key"]:
                        expected = self._request_descriptor(name, body, actor)
                        if run["trigger"].get("request") != expected:
                            raise ValueError("Conflicting idempotency key payload")
                        operation = next((row for row in catalog["operations"] if row.get("run_id") == run["run_id"]), None)
                        return {"run_id": run["run_id"], "operation_id": operation["operation_id"] if operation else None,
                                "status": operation["status"] if operation else "recovery_required"}
            if name == "import_legacy":
                for previous in catalog["imports"]:
                    if (previous["source_kind"] == body.get("source_kind") and previous["source_id"] == body.get("source_id")
                            and previous["mappings"] == body.get("mappings", {}) and previous["status"] in {"linked", "imported"}):
                        # Revalidate the original pinned import without republishing
                        # its result or changing its exact historical references.
                        self._fields(body, ("source_kind", "source_id"), ("mappings",))
                        if previous["source_kind"] == "workbench_job":
                            self.app.results(previous["source_id"])
                        return self.store(write=True).record_import(previous["source_kind"], previous["source_id"],
                            previous["mappings"], previous["status"], actor=actor, expected_revision=catalog["revision"],
                            evidence=previous.get("evidence"))
            if body["expected_revision"] != catalog["revision"]:
                raise RuntimeError("Scientific workspace revision changed; refresh before applying this action")
            if name == "initialize":
                self._fields(body)
                MethodService(self.root, reconcile=False)
                result = ScientificObjectService.migrate(self.root, self.project_id, actor=actor)
                self.app.upgrade_scientific_view()
                return result
            if not catalog["initialized"]:
                raise ValueError("Initialize or explicitly migrate the scientific store before writing records")
            store = self.store(write=True)
            if name == "create_study":
                self._fields(body, ("name", "aim"))
                return store.create_study(body["name"], aim=body["aim"], actor=actor, expected_revision=catalog["revision"])
            if name == "update_study":
                self._fields(body, ("study_id", "name", "aim"))
                record = store.get("study", body["study_id"])
                return store.update_study(record["study_id"], name=body["name"], aim=body["aim"], actor=actor, expected_revision=record["revision"])
            if name == "register_dataset":
                return self._register_dataset(body, actor)
            if name in {"rename_dataset", "rename_analysis", "set_dataset_locations"}:
                kind = "analysis" if name == "rename_analysis" else "dataset"
                field = "locations" if name == "set_dataset_locations" else "name"
                self._fields(body, (kind + "_id", field))
                record = store.get(kind, body[kind + "_id"])
                return getattr(store, name)(record[kind + "_id"], body[field], actor=actor, expected_revision=record["revision"])
            if name == "create_analysis":
                self._fields(body, ("study_id", "name", "objective", "computational_task"), ("legacy_analysis_id", "execution_source_run_id"))
                study = store.get("study", body["study_id"])
                definition = {"method_version_ids": [], "code_identity": {}, "parameters": {},
                    "validation_policy": {"status": "unplanned"}, "task": {
                        "scientific_objective": _reason(body["objective"]), "computational_task": _label(body["computational_task"])}}
                methods = self.methods(write=True)
                legacy = methods.analysis(body["legacy_analysis_id"]) if body.get("legacy_analysis_id") else None
                if body.get("execution_source_run_id"):
                    native = methods.get("run", body["execution_source_run_id"])
                    branch = methods.get("branch", native["branch_id"])
                    if legacy is None or branch["analysis_id"] != legacy["analysis_id"] or not methods.provenance(native["run_id"])["valid"]:
                        raise ValueError("Explicit source invocation must match the selected retained analysis and verified evidence")
                    method_id = native["method_version_id"]
                    contract = methods.method(method_id)["contract"]
                    definition.update(method_version_ids=[method_id], code_identity={method_id: contract["implementation"]},
                        parameters={"legacy": native["parameters"]}, validation_policy={method_id: contract["validation"]},
                        environment={method_id: contract["environment"]},
                        steps=[{"step_key": "legacy", "method_version_id": method_id, "parameters": native["parameters"], "inputs": native["inputs"]}])
                if legacy is None:
                    legacy = methods.create_analysis(body["name"], actor=actor)
                return store.create_analysis(study["study_id"], body["name"], definition, actor=actor,
                    expected_revision=study["revision"], legacy_analysis_id=legacy["analysis_id"])
            if name == "create_plan":
                return self._create_plan(body, actor)
            if name == "advance_to_gate":
                return self._advance_to_gate(body, actor)
            if name in {"discovery", "candidate_review", "implementation", "design_review", "conclusion_review"}:
                return self._scientific_stage(name, body, actor)
            if name in {"science", "resolve", "design_trials", "draft_method", "recommend"}:
                return self._model_action(name, body, actor)
            if name == "declare_trial":
                return self._declare_trial(body, actor)
            if name in {"run_trial", "rerun"}:
                return self._execute_request(name, body, actor)
            if name in {"accept_result", "reject_result", "select_result"}:
                return self._review_result(name, body, actor)
            if name == "recover_run":
                self._fields(body, ("run_id",))
                return self._publish(body["run_id"], actor)
            if name == "import_legacy":
                return self._import_legacy(body, actor)
            raise ValueError("Unknown scientific action")

    def _register_dataset(self, body, actor):
        self._fields(body, ("study_id", "name", "members", "metadata"), ("dataset_id",))
        store, methods = self.store(write=True), self.methods(write=True)
        study = store.get("study", body["study_id"])
        if not isinstance(body["members"], dict) or not 1 <= len(body["members"]) <= 32:
            raise ValueError("Use one to thirty-two named dataset members")
        members, locations = {}, {}
        for name, item in body["members"].items():
            if isinstance(item, dict) and set(item) == {"artifact_id"}:
                methods.artifact_path(item["artifact_id"])
                members[name] = item
            elif isinstance(item, dict) and set(item) == {"path", "artifact_type"}:
                path = _canonical(item["path"], exists=True)
                if not any(path.is_relative_to(root) for root in self.app.input_roots):
                    raise ValueError("Dataset member is outside the configured input roots")
                artifact = methods.import_artifact(path, artifact_type=item["artifact_type"], actor=actor)
                members[name] = {"artifact_id": artifact["artifact_id"]}
                locations[name] = str(path)
            else:
                raise ValueError("A dataset member needs an explicit retained artifact or allowed file/type")
        manifest = {"members": members, "metadata": body["metadata"]}
        if body.get("dataset_id"):
            previous = store.get("dataset", body["dataset_id"])
            if previous["study_id"] != study["study_id"]:
                raise ValueError("Dataset belongs to another Study")
            result = store.add_dataset_version(previous["dataset_id"], manifest, actor=actor, expected_revision=previous["revision"])
            if locations:
                result = store.set_dataset_locations(result["dataset_id"], locations, actor=actor, expected_revision=result["revision"])
            return result
        return store.create_dataset(study["study_id"], body["name"], manifest, actor=actor,
                                    expected_revision=study["revision"], locations=locations)

    def _create_plan(self, body, actor):
        self._fields(body, ("analysis_ref", "dataset_ref"), ("evaluation_purpose", "user_parameter_requirements",
            "rna_input_contract", "rna_execution_inputs_ref", "rna_execution_workspace", "scientific_contract"))
        store = self.store(write=True)
        analysis_version, dataset_version = store.resolve(body["analysis_ref"]), store.resolve(body["dataset_ref"])
        analysis = store.get("analysis", analysis_version["analysis_id"])
        dataset = store.get("dataset", dataset_version["dataset_id"])
        if analysis["study_id"] != dataset["study_id"]:
            raise ValueError("Analysis and Dataset belong to different Studies")
        metadata = dataset_version["manifest"]["metadata"]
        summary = {key: deepcopy(metadata.get(key, default)) for key, default in (("observed", {}), ("inferred", {}), ("unknowns", []))}
        summary["artifact_ids"] = list(store.input_bindings([dataset_version["version_id"]]).values())
        task = analysis_version["definition"].get("task")
        if not task:
            raise ValueError("This AnalysisVersion needs an explicit scientific task before planning")
        anchors = {"project_id": self.project_id, "study_id": analysis["study_id"],
                   "dataset_versions": [dataset_version["ref"]], "analysis_version": analysis_version["ref"]}
        planner = self.planner()
        plan = planner.create(task, summary, actor=actor, data_classification=metadata.get("data_classification", "unclassified"),
            anchors=anchors, evaluation_purpose=body.get("evaluation_purpose", "method_selection"),
            user_parameter_requirements=body.get("user_parameter_requirements"),
            rna_input_contract=body.get("rna_input_contract"),
            rna_execution_inputs_ref=body.get("rna_execution_inputs_ref"),
            rna_execution_workspace=body.get("rna_execution_workspace"),
            scientific_contract=body.get("scientific_contract"))
        base = self.methods().analysis(analysis["legacy_analysis_id"])
        plan = self._save_plan_context(plan, scientific_base_revision=base["revision_id"])
        store.touch(actor=actor, action="plan_anchored", payload={"session_id": plan["session_id"], "anchors": anchors})
        return plan

    def _declare_trial(self, body, actor):
        self._fields(body, ("session_id", "spec"))
        plan = self._plan(body["session_id"])
        store, methods, planner = self.store(write=True), self.methods(write=True), self.planner()
        anchors = plan["scientific_anchors"]
        analysis = store.get("analysis", anchors["analysis_version"]["object_id"])
        base = methods.analysis(analysis["legacy_analysis_id"])
        if base["revision_id"] != plan["scientific_base_revision"]:
            raise RuntimeError("Approved method plan changed; create a new planning session against its current revision")
        spec = deepcopy(body["spec"])
        if not isinstance(spec, dict):
            raise ValueError("Use a declared scientific trial specification object")
        bindings = store.input_bindings([item["version_id"] for item in anchors["dataset_versions"]])
        for name in spec.get("candidates", []):
            if spec.get("inputs", {}).get(name) != bindings:
                raise ValueError("Trial input bindings must match the selected immutable dataset manifest")
        branch = methods.create_branch(analysis["legacy_analysis_id"], base["revision_id"], actor=actor)
        experiment = workflow_action(planner, plan["session_id"], "declare", branch_id=branch["branch_id"], spec=spec, actor=actor, scientific_workflow=self.scientific_workflow)
        steps = []
        contracts = {}
        for name in experiment["candidates"]:
            step = methods.get("step", experiment["step_ids"][name])
            contracts[step["method_version_id"]] = methods.method(step["method_version_id"])["contract"]
            steps.append({"step_key": name, "method_version_id": step["method_version_id"], "parameters": step["parameters"]})
        definition = {"method_version_ids": sorted(contracts),
            "code_identity": {identity: contract["implementation"] for identity, contract in contracts.items()},
            "environment": {identity: contract["environment"] for identity, contract in contracts.items()},
            "parameters": experiment["parameters"], "validation_policy": {identity: contract["validation"] for identity, contract in contracts.items()},
            "steps": steps, "task": plan["context"]["task"]}
        updated = store.add_analysis_version(analysis["analysis_id"], definition, actor=actor, expected_revision=analysis["revision"])
        plan = planner.get(plan["session_id"])
        definitions = deepcopy(plan.get("scientific_trial_definitions", {}))
        definitions[experiment["id"]] = updated["ref"]
        self._save_plan_context(plan, scientific_trial_definitions=definitions)
        return {"experiment": experiment, "analysis_ref": updated["ref"]}

    @staticmethod
    def _request_descriptor(name, body, actor):
        fields = ("session_id", "experiment_id") if name == "run_trial" else ("analysis_ref", "dataset_ref")
        return {"action": name, "actor": actor, **{key: body.get(key) for key in fields}}

    def _execute_request(self, name, body, actor):
        required = ("session_id", "experiment_id", "idempotency_key") if name == "run_trial" else ("analysis_ref", "dataset_ref", "idempotency_key")
        self._fields(body, required, ("allow_data_execution",))
        if not self.app.allow_data_execution or body.get("allow_data_execution") is not True:
            raise PermissionError("Trial execution requires current service permission and explicit authorization for this request")
        store, methods = self.store(write=True), self.methods(write=True)
        if name == "run_trial":
            plan = self._plan(body["session_id"])
            experiment = next((row for row in plan["experiments"] if row["id"] == body["experiment_id"]), None)
            if experiment is None or experiment["status"] != "declared":
                raise ValueError("Trial is unavailable or already attempted; unchanged analysis reruns are separate invocations")
            reference = plan.get("scientific_trial_definitions", {}).get(experiment["id"])
            if reference is None:
                raise ValueError("Trial has no exact scientific execution definition")
            dataset_refs = plan["scientific_anchors"]["dataset_versions"]
        else:
            reference, dataset_refs = body["analysis_ref"], [body["dataset_ref"]]
            experiment = None
        version = store.resolve(reference)
        analysis = store.get("analysis", version["analysis_id"])
        for ref in dataset_refs:
            store.resolve(ref)
        definition = version["definition"]
        rna_session = None
        if name == "rerun":
            matches = []
            for candidate in methods.list_records("method_plan"):
                anchors = candidate.get("scientific_anchors", {})
                references = [anchors.get("analysis_version"), *candidate.get("scientific_trial_definitions", {}).values()]
                if (is_rna_plan(candidate) and reference in references
                        and dataset_refs == anchors.get("dataset_versions")):
                    matches.append(candidate["session_id"])
            known_rna = any("bulk_rna_input" in store.resolve(ref)["manifest"].get("metadata", {}).get("observed", {})
                            for ref in dataset_refs)
            if len(matches) > 1 or known_rna and not matches:
                raise ValueError("RNA rerun requires an exact completed parent graph for these analysis and dataset versions")
            if matches:
                rna_session = matches[0]
                require_completed_parent(self.planner(), rna_session, scientific_workflow=self.scientific_workflow)
        request = self._request_descriptor(name, body, actor)
        run = store.request_run(analysis["analysis_id"], version["version_id"], [ref["version_id"] for ref in dataset_refs],
            environment=definition.get("environment") or {identity: methods.method(identity)["contract"]["environment"] for identity in definition["method_version_ids"]},
            trigger={"kind": name, "request": request}, actor=actor, expected_revision=analysis["revision"], idempotency_key=body["idempotency_key"])
        executions = {}
        steps = definition.get("steps") or [{"step_key": "default", "method_version_id": definition["method_version_ids"][0], "parameters": definition["parameters"]}]
        branch = None
        if name == "rerun":
            base = methods.analysis(analysis["legacy_analysis_id"])
            branch = methods.create_branch(analysis["legacy_analysis_id"], base["revision_id"], actor=actor)
        for step in steps:
            controller_id = uuid.uuid4().hex
            current = store.get("run", run["run_id"])
            attempt = store.attach_attempt(run["run_id"], controller_kind="method", controller_id=controller_id,
                correlation_id=run["correlation_id"], actor=actor, expected_revision=current["revision"], step_key=step["step_key"])
            correlation = {"project_id": self.project_id, "scientific_run_id": run["run_id"], "attempt_id": attempt["attempt_id"],
                           "analysis_version_id": version["version_id"], "dataset_version_ids": run["dataset_version_ids"]}
            executions[step["step_key"]] = {"controller_id": controller_id, "correlation": correlation}
            if branch is not None:
                created = methods.add_step(branch["branch_id"], step["method_version_id"], step.get("inputs", run["inputs"]), step["parameters"], actor=actor)
                executions[step["step_key"]]["step_id"] = created["step_id"]
        def execute_existing():
            if name == "run_trial":
                workflow_action(self.planner(), plan["session_id"], "run", experiment_id=experiment["id"], actor=actor,
                    allow_data_execution=True, executor=self.executor, execution_bindings=executions,
                    scientific_workflow=self.scientific_workflow)
            else:
                for binding in executions.values():
                    methods.run_step(binding["step_id"], actor=actor, executor=self.executor,
                        run_id=binding["controller_id"], correlation=binding["correlation"])
            return self._publish(run["run_id"], actor)
        def verify_followup(value):
            if value["run_id"] != run["run_id"]:
                raise ValueError("RNA rerun output belongs to another scientific invocation")
            for binding in executions.values():
                native = methods.get("run", binding["controller_id"])
                if native["step_id"] != binding["step_id"] or not methods.provenance(native["run_id"])["valid"]:
                    raise ValueError("RNA rerun differs from its retained exact method execution evidence")
            for published in value["results"]:
                if store.resolve(published["ref"])["run_id"] != run["run_id"]:
                    raise ValueError("RNA rerun published result differs from its exact scientific invocation")
        def execute():
            if rna_session is not None:
                return governed_followup(self.planner(), rna_session, kind="rerun", request_id=run["run_id"],
                    actor=actor, operation=execute_existing, verify=verify_followup, scientific_workflow=self.scientific_workflow)
            return execute_existing()
        return self._queue(name, actor, execute, run_id=run["run_id"])

    def _publish(self, run_id, actor):
        store = self.store(write=True)
        run = store.get("run", run_id)
        results, unavailable = [], []
        for attempt in store.list("attempt"):
            if attempt["run_id"] != run_id:
                continue
            try:
                finished = store.finish_attempt(attempt["attempt_id"], actor=actor, expected_revision=attempt["revision"])
                trial = self._trial_observation(run, attempt)
                if trial is not None and trial["status"] != "success":
                    unavailable.append({"attempt_id": attempt["attempt_id"], "trial_status": trial["status"],
                        "message": "Trial evidence is not eligible for acceptance", "failures": trial.get("failures", []),
                        "warnings": trial.get("warnings", []), "worker_liveness": "terminal_controller_record"})
                    continue
                result = store.publish_result(finished["attempt_id"], interpretation="Retained execution evidence for " + finished["step_key"],
                    actor=actor, expected_revision=finished["revision"])
                results.append(result)
            except (ValueError, OSError) as error:
                unavailable.append({"attempt_id": attempt["attempt_id"], "message": str(error), "worker_liveness": "unknown"})
        return {"run_id": run_id, "results": results, "unavailable": unavailable, "execution_restarted": False}

    def _trial_observation(self, run, attempt):
        if run["trigger"].get("kind") != "run_trial":
            return None
        request = run["trigger"]["request"]
        state = self._plan(request["session_id"])
        ScientificMethodPlanner(self.methods())._verified_results(state)
        rows = [row for row in state["results"] if row["experiment_id"] == request["experiment_id"]
                and row["candidate"] == attempt["step_key"]
                and row.get("provenance", {}).get("run_id") == attempt["controller_id"]]
        if len(rows) != 1:
            raise ValueError("Trial has no exact retained outcome for this controller execution")
        return rows[0]

    def _review_result(self, name, body, actor):
        self._fields(body, ("result_ref", "rationale"), ("acknowledge_warnings", "select_role"))
        store, methods = self.store(write=True), self.methods(write=True)
        version = store.resolve(body["result_ref"])
        result = store.get("result", version["result_id"])
        if version["version_id"] != result["current_version_id"]:
            raise RuntimeError("Result changed; review the exact current interpretation before accepting")
        role = body.get("select_role")
        if name == "select_result":
            if not role:
                raise ValueError("A scoped result role is required")
            prior = next((item for item in store.list("selection") if item["study_id"] == result["study_id"] and item["role"] == role), None)
            return store.select_result(result["study_id"], role, version["version_id"], actor=actor,
                rationale=body["rationale"], expected_revision=prior["revision"] if prior else 0)
        if name == "reject_result":
            return store.review_result(result["result_id"], version["version_id"], "rejected", actor=actor,
                rationale=body["rationale"], expected_revision=result["revision"])
        attempt = store.get("attempt", version["attempt_id"])
        trial = self._trial_observation(store.get("run", version["run_id"]), attempt)
        if trial is not None and trial["status"] != "success":
            raise ValueError("Failed, partial or over-budget trial evidence cannot be accepted")
        if result["review_status"] == "accepted":
            raise ValueError("This result is already accepted; select its reviewed version explicitly if needed")
        analysis = store.get("analysis", result["analysis_id"])
        definition = store.get("analysis_version", version["analysis_version_id"])["definition"]
        # A newer definition is a stale scientific target, even if the legacy
        # method head has not advanced yet.
        if analysis["current_version_id"] != version["analysis_version_id"]:
            raise RuntimeError("Analysis definition changed; create an explicit revised proposal before acceptance")
        selection = next((item for item in store.list("selection") if item["study_id"] == result["study_id"] and item["role"] == role), None)
        def accept(db, revision, selected_proposal, selected_run):
            store.add_analysis_version(analysis["analysis_id"], definition, actor=actor, expected_revision=analysis["revision"], db=db)
            store.review_result(result["result_id"], version["version_id"], "accepted", actor=actor,
                rationale=body["rationale"], expected_revision=result["revision"], db=db)
            if role:
                store.select_result(result["study_id"], role, version["version_id"], actor=actor,
                    rationale=body["rationale"], expected_revision=selection["revision"] if selection else 0, db=db)
        native = methods.get("run", attempt["controller_id"])
        if (native.get("validation") or {}).get("warnings") and body.get("acknowledge_warnings") is not True:
            raise ValueError("Scientific review must acknowledge validation warnings")
        head = methods.analysis(analysis["legacy_analysis_id"])
        already_accepted = any(item["run_id"] == native["run_id"] and item["evidence_root"] == native["evidence"]["root_hash"]
                               and item["outputs"] == native["outputs"] for item in head["steps"])
        if already_accepted:
            # An explicit import can add scoped scientific meaning to an already
            # accepted method run. Verify its current head atomically; preserve
            # the accepted method history instead of adding the same step again.
            with methods._db() as db:
                current = db.execute("SELECT revision_id FROM heads WHERE analysis_id=?", (analysis["legacy_analysis_id"],)).fetchone()
                if current is None or current[0] != head["revision_id"]:
                    raise RuntimeError("Accepted method history changed during scientific review")
                accept(db, head, None, native)
                methods._audit(db, actor, "scientific_review_of_accepted_history", {"run_id": native["run_id"],
                    "method_revision_id": head["revision_id"], "result_version_id": version["version_id"]})
        else:
            branch = methods.get("branch", native["branch_id"])
            if branch["analysis_id"] != analysis["legacy_analysis_id"] or branch["base_revision"] != head["revision_id"]:
                raise RuntimeError("Approved method plan changed; explicitly rebase before accepting this proposal")
            proposal = methods.propose(attempt["controller_id"], actor=actor, rationale=body["rationale"])
            methods.approve(proposal["proposal_id"], actor=actor, rationale=body["rationale"],
                acknowledge_warnings=body.get("acknowledge_warnings") is True, _on_approval=accept)
        return store.get("result", result["result_id"])

    def _import_legacy(self, body, actor):
        self._fields(body, ("source_kind", "source_id"), ("mappings",))
        store, methods = self.store(write=True), self.methods()
        mappings = body.get("mappings", {})
        if not isinstance(mappings, dict) or set(mappings) - {"study_id", "dataset_ref", "analysis_ref"}:
            raise ValueError("Use explicit Study, DatasetVersion and AnalysisVersion import mappings")
        if body["source_kind"] == "workbench_job":
            job = self.app.status(body["source_id"])
            evidence = self.app.results(job["id"]) if job.get("completed_analysis") else None
            status = "linked" if evidence and {"study_id", "dataset_ref", "analysis_ref"} <= set(mappings) else "unresolved"
            for key in ("dataset_ref", "analysis_ref"):
                if key in mappings:
                    store.resolve(mappings[key])
            if "study_id" in mappings:
                store.get("study", mappings["study_id"])
            payload = {"job_id": job["id"], "existing_route": "/api/jobs/" + job["id"],
                       "execution": job.get("execution"), "review": job.get("scientific_review"),
                       "verification": evidence["verification"] if evidence else None,
                       "missing_mappings": sorted({"study_id", "dataset_ref", "analysis_ref"} - set(mappings)),
                       "execution_grouping_inferred": False}
            return store.record_import("workbench_job", job["id"], mappings, status, actor=actor,
                expected_revision=store.snapshot()["revision"], evidence=payload)
        if body["source_kind"] != "method_run":
            raise ValueError("Use an explicitly selected retained method run or existing workbench job")
        native = methods.get("run", body["source_id"])
        if not methods.provenance(native["run_id"])["valid"]:
            raise ValueError("Legacy controller evidence failed verification")
        required = {"study_id", "dataset_ref", "analysis_ref"}
        if not required <= set(mappings):
            return store.record_import("method_run", native["run_id"], mappings, "unresolved", actor=actor,
                expected_revision=store.snapshot()["revision"], evidence={"receipt": native["evidence"], "missing_mappings": sorted(required-set(mappings))})
        dataset, analysis_version = store.resolve(mappings["dataset_ref"]), store.resolve(mappings["analysis_ref"])
        analysis = store.get("analysis", analysis_version["analysis_id"])
        original_branch = methods.get("branch", native["branch_id"])
        if analysis["legacy_analysis_id"] != original_branch["analysis_id"]:
            raise ValueError("Import requires an explicit mapping to the original method analysis")
        if analysis["study_id"] != mappings["study_id"] or store.get("dataset", dataset["dataset_id"])["study_id"] != mappings["study_id"]:
            raise ValueError("Import mappings belong to different Studies")
        run = store.request_run(analysis["analysis_id"], analysis_version["version_id"], [dataset["version_id"]],
            environment=analysis_version["definition"]["environment"],
            trigger={"kind": "legacy_import", "source_kind": "method", "source_id": native["run_id"]},
            actor=actor, expected_revision=analysis["revision"], idempotency_key="legacy:" + native["run_id"])
        steps = analysis_version["definition"].get("steps") or [{"step_key": "default"}]
        if len(steps) != 1:
            raise ValueError("Import one explicit method execution into a matching one-step definition")
        store.attach_attempt(run["run_id"], controller_kind="method", controller_id=native["run_id"],
            correlation_id=run["correlation_id"], actor=actor, expected_revision=run["revision"], step_key=steps[0]["step_key"])
        published = self._publish(run["run_id"], actor)
        return store.record_import("method_run", native["run_id"], mappings,
            "linked" if not published["unavailable"] else "unresolved", actor=actor,
            expected_revision=store.snapshot()["revision"], evidence={"receipt": native["evidence"], "run_id": run["run_id"],
                "result_refs": [item["ref"] for item in published["results"]], "unavailable": published["unavailable"]})
