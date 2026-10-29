"""Synthetic integration adapter for the required serial workflow.

This is an operator-specified SOFTWARE qualification, not RNA method selection.
Only hash/schema/metadata work occurs here. Scientific source, the independent
numerical validator and plots execute remotely. A completed graph records an
observed round trip; independent review is still required for acceptance.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import uuid

from devharness_core.provenance import verify_execution
from .workflow_actions import WorkflowActions, create_rna_workflow
from .workflow_operations import WorkflowOperationLedger
from .workflow_state import digest
from .colab_execution_contract import SCOPE, job_sha256, validate_job
from .workflow_protocol_qualification import (ProtocolQualificationProfile, ProtocolQualificationRuntime,
                                             verify_profile_job)


@dataclass(frozen=True)
class _QualificationRunContext:
    actions: object
    controller: object
    execution: object
    state: dict
    node_dispatch_token: object
    profile_digest: str
    job_digest: str


_active_qualification_run = ContextVar('active_colab_qualification_run', default=None)


def _check_graph_run(actions, controller, state):
    from .langgraph_workflow import SerialRNAWorkflow, _require_active_node_dispatch
    from .workflow_actions import _COLAB_FACTORY_ADMISSION
    if (type(actions) is not ColabQualificationActions or type(controller) is not SerialRNAWorkflow
            or controller.actions is not actions
            or getattr(controller, '_qualification_factory_token', None) is not _COLAB_FACTORY_ADMISSION):
        raise PermissionError('Qualification requires an active factory-admitted serial graph callback')
    admission = _require_active_node_dispatch(controller, 'run', state)
    snapshot = controller.graph.get_state(controller._config(state['workflow_id']))
    if tuple(snapshot.next) != ('run',) or snapshot.values != state:
        raise PermissionError('Qualification requires the exact active graph run checkpoint')
    if (state['session_id'] != actions.planner.session_id or len(state['trial_ids']) != 1
            or state['trial_index'] != 0 or state['trial_ids'] != [actions.profile.job_id]):
        raise PermissionError('Qualification requires the exact one-job graph cursor')
    actions.check_node_entry('run', state)
    actions.check(state)
    if actions.ledger.is_cancelled(state['workflow_id']):
        raise PermissionError('Cancelled qualification cannot dispatch')
    return admission


def _require_graph_run_context(execution):
    """An in-process active graph callback is necessary before any reservation."""
    context = _active_qualification_run.get()
    if type(context) is not _QualificationRunContext or context.execution is not execution:
        raise PermissionError('Qualification requires an active serial graph run context')
    actions, state = context.actions, context.state
    admission = _check_graph_run(actions, context.controller, state)
    if (admission is not context.node_dispatch_token
            or actions.planner.execution is not execution or actions.executor is not execution
            or execution.service is not actions.planner.methods
            or digest(actions.profile.model_dump(mode='json')) != context.profile_digest
            or job_sha256(execution.job) != context.job_digest
            or actions.allow_data_execution is not True):
        raise PermissionError('Qualification active graph execution identity or permission changed')
    verify_profile_job(actions.profile, execution.job)
    record = actions.ledger.get(state['workflow_id'], 'run', 0)
    expected = {'session_id': state['session_id'], 'expected_planner_hash': state['expected_planner_hash'],
        'actor': actions.actor, 'binding_digest': state['binding_digest'], 'artifact_refs': state['artifact_refs'],
        'version_ids': [], 'declarations': [], 'trial_ids': state['trial_ids'],
        'execution_binding_digest': digest(actions.execution_bindings),
        'selector_digest': digest(actions.selector.configuration()),
        'science_configuration_digest': actions._science_configuration_digest()}
    if record is None or record['status'] != 'active' or record['request'] != expected:
        raise PermissionError('Qualification requires its exact active outer graph operation reservation')
    return context


class FixedColabQualificationSelector:
    """Identity metadata only. There is deliberately no model-call API."""
    def configuration(self):
        return {"format": "devbench.colab-fixed-selection/1", "scope": SCOPE,
                "source": "operator_supplied_integration_protocol", "model_calls": 0}


class ColabQualificationExecution:
    """Exact approved one-job MethodService/official-transport integration."""
    def __init__(self, *, service, authority, transport, job, request, grant, export_directory):
        from .methods import MethodService
        from .rna_trial_authority import AuthenticatedTrialAuthority
        from .colab_transport import OfficialColabTransport
        if (type(service) is not MethodService or type(authority) is not AuthenticatedTrialAuthority
                or type(transport) is not OfficialColabTransport or transport.service is not service):
            raise TypeError("Exact MethodService, execution authority and official Colab transport required")
        self.service, self.authority, self.transport = service, authority, transport
        self.job = validate_job(job)
        self.request, self.grant = deepcopy(request), deepcopy(grant)
        self.export_directory = Path(export_directory)
        if not self.export_directory.is_absolute():
            raise ValueError("Absolute export directory required")
        self.used = False

    def execute_once(self):
        _require_graph_run_context(self)
        if self.used:
            raise PermissionError("Qualification dispatch is spent; automatic replay is forbidden")
        self.used = True
        job, service = validate_job(self.job), self.service
        try:
            service.reserve_remote_step(job, request=self.request, grant=self.grant,
                                        authority=self.authority, actor=job["actor"])
            service.export_remote_job(job["job_id"], self.export_directory, actor=job["actor"])
            dispatched = self.transport.dispatch(job["job_id"])
            service.record_remote_dispatch(job["job_id"], dispatched, actor=job["actor"])
            archive, returned = self.transport.collect()
            projection = returned.verify(job=job, stage="returned")
            return service.import_remote_result(job["job_id"], archive, projection["expected_root_sha256"],
                                                transport_receipt=returned, actor=job["actor"])
        except BaseException:
            # A failed reservation or a lost return stays charged/uncertain.
            # Never copy exception text (provider endpoints/credentials) into a
            # workflow checkpoint. Neither retry nor local execution exists.
            if not self.transport.cleanup["stop_requested"] and self.transport.state != "new":
                self.transport.stop()
            try:
                run = service.get("run", job["run_id"])
                if run["status"] in {"remote_pending", "remote_dispatched"}:
                    service.finalize_remote_failure(job["job_id"], category="transport_error",
                                                    uncertain=True, actor=job["actor"])
            except Exception:
                pass  # The outer workflow ledger retains the uncertain effect.
            raise RuntimeError("Colab qualification did not return verified evidence; no redispatch") from None


class ColabQualificationPlan:
    """Metadata-only plan, explicitly distinct from a scientific RNA planner.

    It supplies the fixed protocol to the existing WorkflowActions node contract;
    it never calls the scientific planner or claims biological observations.
    All snapshots are retained in the actual MethodService store.
    """
    def __init__(self, *, execution, actor, session_id=None):
        if type(execution) is not ColabQualificationExecution:
            raise TypeError("Exact qualification execution controller required")
        if actor != execution.job["actor"]:
            raise ValueError("Qualification actor differs from approved job")
        self.execution, self.methods, self.actor = execution, execution.service, actor
        self.session_id = session_id or uuid.uuid4().hex
        job = execution.job
        step = self.methods.get("step", job["step_id"])
        self.branch_id, self.draft_id = step["branch_id"], job["job_id"]
        refs = list(step["inputs"].values())
        if len(refs) != len(set(refs)):
            raise ValueError("Qualification input references must be distinct")
        plan = {"session_id": self.session_id, "context": {
            "dataset_summary": {"artifact_ids": refs, "observed": {},
                                "scope": "synthetic_software_fixture_only"},
            "task": {"scientific_objective": "Qualify a deterministic Colab integration round trip",
                     "objective_kind": "software_integration_qualification",
                     "requirement_source": "explicit_operator_request", "scope": SCOPE,
                     "job_sha256": job_sha256(job)}},
            "user_parameter_requirements": [], "data_classification": "public_synthetic",
            "limits": deepcopy(job["limits"]), "scientific_anchors": None,
            "scientific_candidates": [], "candidates": [], "registry": {},
            "trial_designs": [], "experiments": [], "results": [],
            "execution_correlations": {}, "recommendation": None,
            "selection_history": [], "selection_in_progress": None}
        # Validate the real cursor schema before writing a new session.
        from .workflow_state import initial_state
        initial_state(plan, [])
        with self.methods._db() as db:
            self.methods._put(db, "colab_qualification_plan", self.session_id, plan)
            self.methods._put(db, "method_plan_snapshot", digest(plan), plan)
            self.methods._audit(db, actor, "colab_qualification_plan_created",
                                {"session_id": self.session_id, "job_sha256": job_sha256(job)})

    def get(self, session_id):
        if session_id != self.session_id:
            raise ValueError("Qualification session differs")
        return self.methods.get("colab_qualification_plan", session_id)

    def _update(self, session_id, changes):
        before = self.get(session_id)
        after = {**before, **deepcopy(changes)}
        with self.methods._db() as db:
            if self.methods._get(db, "colab_qualification_plan", session_id) != before:
                raise ValueError("Qualification plan changed concurrently")
            self.methods._put(db, "colab_qualification_plan", session_id, after, update=True)
            if after != before:
                self.methods._put(db, "method_plan_snapshot", digest(after), after)
            self.methods._audit(db, self.actor, "colab_qualification_metadata_updated",
                                {"session_id": session_id, "before": digest(before), "after": digest(after)})
        return after

    @staticmethod
    def _fixed(selector, kwargs):
        if (type(selector) is not FixedColabQualificationSelector
                or kwargs.get("allow_model_execution") is not False):
            raise PermissionError("Only fixed operator selection with model execution disabled is admitted")

    def generate_candidates(self, session_id, selector, **kwargs):
        self._fixed(selector, kwargs)
        self._update(session_id, {"scientific_candidates": [{"name": "fixed_colab_software_qualification",
            "kind": "operator_supplied_integration_protocol", "scientific_comparison": False}]})

    def resolve_capabilities(self, session_id, selector, versions, **kwargs):
        self._fixed(selector, kwargs)
        version = self.execution.job["method_version_id"]
        if versions != [version]:
            raise ValueError("Qualification inventory must contain the exact approved method")
        self.methods.method(version)  # Schema/hash custody only.
        self._update(session_id, {"registry": {version: {"scope": SCOPE}}, "candidates": [
            {"name": "fixed_colab_software_qualification", "method_version_id": version,
             "applicability": "eligible", "eligibility_scope": "software_qualification_only"}]})

    def design_trials(self, session_id, selector, **kwargs):
        self._fixed(selector, kwargs)
        self._update(session_id, {"trial_designs": [{"proposals": [{"id": self.draft_id,
            "method_version_id": self.execution.job["method_version_id"],
            "scope": SCOPE, "job_sha256": job_sha256(self.execution.job),
            "parameter_source": "fixed_operator_integration_protocol"}]}]})

    def declare_trial(self, session_id, branch, draft, *, actor):
        plan = self.get(session_id)
        if (actor != self.actor or branch != self.branch_id or plan["experiments"]
                or draft != plan["trial_designs"][-1]["proposals"][0]):
            raise ValueError("Exact single frozen qualification declaration required")
        self._update(session_id, {"experiments": [{**draft, "branch_id": branch, "status": "declared"}]})

    def run_trial(self, session_id, experiment, *, actor, allow_data_execution, executor, execution_bindings):
        plan, job = self.get(session_id), self.execution.job
        if (actor != self.actor or allow_data_execution is not True or executor is not self.execution
                or execution_bindings != {"job_sha256": job_sha256(job)}
                or experiment != self.draft_id or len(plan["experiments"]) != 1
                or plan["experiments"][0]["status"] != "declared"):
            raise PermissionError("Exact remote execution permission and job binding required")
        run = self.execution.execute_once()
        rows = [{"experiment_id": experiment, "candidate": "fixed_colab_software_qualification",
                 "status": run["status"], "provenance": {"run_id": run["run_id"]}}]
        self._update(session_id, {"experiments": [{**plan["experiments"][0], "status": "completed"}],
            "results": rows, "execution_correlations": {experiment: deepcopy(execution_bindings)}})
        return rows

    def evidence_assessment(self, session_id):
        plan, job = self.get(session_id), self.execution.job
        if len(plan["results"]) != 1 or plan["results"][0]["provenance"]["run_id"] != job["run_id"]:
            raise ValueError("No exact retained qualification result exists")
        run = self.methods.get("run", job["run_id"])
        validation = run.get("validation") or {}
        if (run["status"] != "succeeded" or run.get("backend") != "google_colab"
                or run.get("job_sha256") != job_sha256(job)
                or validation.get("passed") is not True
                or validation.get("numerical_execution_tier") != "colab"
                or run.get("remote_transport_return", {}).get("stage") != "returned"
                or not run.get("outputs")):
            raise ValueError("Remote result and validation binding required")
        evidence = run["evidence"]
        if verify_execution(Path(evidence["manifest_path"]), evidence["root_hash"])["valid"] is not True:
            raise ValueError("Remote provenance integrity failed")
        for artifact_id in run["outputs"].values():
            self.methods.artifact_path(artifact_id)  # Hash only; no scientific replay.
        return {"round_trip_observed": True, "integration_accepted": False,
                "independent_verification_required": True, "scientific_admission": False}

    def recommend(self, session_id, selector, **kwargs):
        self._fixed(selector, kwargs)
        assessment = self.evidence_assessment(session_id)
        value = {"choice": "remote_round_trip_observed", "scope": SCOPE, **assessment}
        self._update(session_id, {"recommendation": value})
        return value


class ColabQualificationActions(WorkflowActions):
    """Same graph/operation contracts, with an explicit metadata-only audit."""
    def __init__(self, planner, ledger, *, actor, profile, allow_data_execution=False):
        if type(planner) is not ColabQualificationPlan or type(ledger) is not WorkflowOperationLedger:
            raise TypeError("Exact qualification plan and existing operation ledger required")
        if actor != planner.actor or type(allow_data_execution) is not bool:
            raise ValueError("Explicit qualification actor and execution permission required")
        if type(profile) is not ProtocolQualificationProfile:
            raise TypeError("Explicit nonbiological protocol qualification profile required")
        verify_profile_job(profile, planner.execution.job)
        runtime = ProtocolQualificationRuntime(profile=profile)
        super().__init__(planner, ledger, actor=actor, selector=FixedColabQualificationSelector(),
            version_ids=[planner.execution.job["method_version_id"]],
            declarations=[{"draft_id": planner.draft_id, "branch_id": planner.branch_id}],
            allow_model_execution=False, allow_data_execution=allow_data_execution,
            executor=planner.execution, execution_bindings={"job_sha256": job_sha256(planner.execution.job)},
            scientific_workflow=runtime)
        self.profile = profile

    @contextmanager
    def graph_run_scope(self, controller, state):
        """The serial controller supplies callback admission; callers cannot mint it."""
        if _active_qualification_run.get() is not None:
            raise PermissionError('Nested qualification graph execution is forbidden')
        admission = _check_graph_run(self, controller, state)
        context = _QualificationRunContext(actions=self, controller=controller, execution=self.planner.execution,
            state=deepcopy(state), node_dispatch_token=admission,
            profile_digest=digest(self.profile.model_dump(mode='json')),
            job_digest=job_sha256(self.planner.execution.job))
        token = _active_qualification_run.set(context)
        try:
            yield
        finally:
            _active_qualification_run.reset(token)

    def _audit(self, plan):
        job = self.planner.execution.job
        fresh = self.planner.methods.prepare_remote_step(job["step_id"], run_id=job["run_id"],
            job_id=job["job_id"], source_bundle_sha256=job["source_bundle_sha256"],
            validator_sha256=job["validator_sha256"], scope=job["scope"], limits=job["limits"],
            actor=job["actor"], correlation=job["correlation"])
        if (fresh != job or plan["context"]["task"]["job_sha256"] != job_sha256(job)
                or plan["context"]["dataset_summary"]["observed"] != {}):
            raise ValueError("Qualification metadata changed; no biological observation is admitted")

    def check(self, state, *, expected_hash=True):
        if self.allow_model_execution or type(self.selector) is not FixedColabQualificationSelector:
            raise PermissionError("Model execution is forbidden in Colab qualification")
        if (type(self.profile) is not ProtocolQualificationProfile
                or type(self.scientific_workflow) is not ProtocolQualificationRuntime
                or self.scientific_workflow.profile != self.profile):
            raise PermissionError("Typed nonbiological protocol runtime cannot change during qualification")
        verify_profile_job(self.profile, self.planner.execution.job)
        self.scientific_workflow.configuration()
        return super().check(state, expected_hash=expected_hash)

    def reconcile(self, cursor):
        raise PermissionError("Uncertain qualification requires independent review; no automatic replay or recovery")

    def recover_interrupted_plan(self, cursor):
        raise PermissionError("Scientific planner recovery is not a qualification operation")


def create_colab_qualification_workflow(*, actions, checkpoint_path):
    """Construction does not start, resume, approve or execute anything."""
    if type(actions) is not ColabQualificationActions:
        raise TypeError("Exact Colab qualification WorkflowActions required")
    return create_rna_workflow(mode="single_agent", actions=actions, checkpoint_path=checkpoint_path)
