"""Durable investigation context without new scientific or execution authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from devbench.analysis.workbench_investigations import InvestigationService, classify_question


DECLARATIONS = {"assay": "scrna", "measurement": "umi_counts", "count_provenance": "Synthetic integer counts",
    "capture_unit_key": "batch", "capture_units_confirmed": True, "annotation_provenance": "Unavailable in this synthetic fixture"}


class Inputs:
    def __init__(self, source):
        self.source = source
        self.calls = []

    def source_path(self, source):
        path = Path(source)
        if not path.is_absolute() or path != path.resolve() or not path.is_file() or not path.is_relative_to(self.source.parent):
            raise PermissionError("Not an allowed regular input")
        return path

    def inspect(self, source):
        self.calls.append(source)
        return {"format": "devbench.dataset-profile/1", "source": source, "path": source,
            "filename": Path(source).name, "size_bytes": self.source.stat().st_size,
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "observed": {"file_type": "h5ad", "cells": 2, "genes": 3, "obs_columns": ["batch", "donor"],
                "var_columns": ["gene_symbol"], "layers": [], "obsm_keys": [], "expression_values_read": False},
            "declarations": {}, "inferred": {"candidate_columns": {"batch": ["batch"], "capture_unit": ["batch"], "donor": ["donor"]}},
            "unknowns": [{"field": "biological_units", "reason": "Experimental identity is unknown", "blocking": True}]}


class App:
    def __init__(self, base):
        self.workspace = base / "service"
        self.workspace.mkdir()
        self.input_roots = (base / "inputs",)
        self.input_roots[0].mkdir()
        self.source = self.input_roots[0] / "synthetic.h5ad"
        self.source.write_text("synthetic header fixture only")
        self.parameters = {"batch_key": "batch", "n_pcs": 8}
        self.allow_data_execution = False
        self.allow_model_execution = False
        self.model_data_scope = "local_only"
        self.selector_config = None
        self.scientific = None
        self.registered, self.reviewed, self.started = [], [], []
        self.records = {}
        self.figure_inventory = None

    def _path(self, *parts):
        path = self.workspace.joinpath(*parts)
        if path != path.resolve() or not path.is_relative_to(self.workspace):
            raise ValueError("Output escaped protected workspace")
        return path

    def _input(self, value):
        path = Path(value)
        if path != path.resolve() or not path.is_file() or not path.is_relative_to(self.input_roots[0]):
            raise PermissionError("Not an allowed regular input")
        return path

    def register(self, request, *, investigation_id):
        self.registered.append((deepcopy(request), investigation_id))
        # The actual WorkbenchService similarly namespaces request idempotency.
        identity = hashlib.md5(investigation_id.encode()).hexdigest()
        if identity not in self.records:
            self.records[identity] = {"id": identity, "source": request["source"], "question": request["question"],
                "status": "awaiting_operator_review", "proposal": {"configuration": {"parameters": {"workflow": "registered_fixture"},
                    "declarations": request["declarations"]}, "external_input": {"path": request["source"], "sha256": request["expected_sha256"]}},
                "state_history": [{"from": "inspecting", "to": "awaiting_operator_review", "at": "2026-10-28T08:00:00Z"}],
                "model_sessions_attempted": 0}
        return {"id": identity, "status": self.records[identity]["status"]}

    def status(self, identity):
        return deepcopy(self.records[identity])

    def review(self, identity, request):
        self.reviewed.append((identity, deepcopy(request)))
        if not request["confirmations"] or request["decision"] != "approve":
            raise ValueError("Explicit confirmations are required")
        self.records[identity].update(status="approved", review=deepcopy(request))
        return {"id": identity, "status": "approved"}

    def run(self, identity):
        if not self.allow_data_execution:
            raise PermissionError("Startup execution permission is disabled")
        if self.records[identity]["status"] != "approved":
            raise RuntimeError("An approved plan is required")
        self.started.append(identity)
        self.records[identity]["status"] = "running"
        self.records[identity]["state_history"].append({"from": "approved", "to": "running", "at": "2026-10-28T08:01:00Z"})
        return {"id": identity, "status": "running"}

    def results(self, identity):
        if not self.records[identity]["status"].startswith("completed_review_"):
            raise RuntimeError("No verified complete result")
        return {"report": {"cells_input": 2, "cells_retained": 2, "HVG_count": 3},
            "deterministic_review": {"passed": True, "checks": {"fixture": True}}, "artifacts": [{"name": "notebook.ipynb"}]}

    def figures(self, identity):
        return deepcopy(self.figure_inventory or {"parent_root": "b" * 64, "figures": []})

    def download(self, identity, name):
        return self.payload, "application/json", name


@pytest.fixture
def setup(tmp_path):
    app = App(tmp_path)
    inputs = Inputs(app.source)
    return app, inputs, InvestigationService(app, inputs)


def create(service, app, **extra):
    return service.create({"question": "Inspect cell quality", "source": str(app.source), **extra})


def action(service, record, name, **extra):
    return service.action(record["id"], {"action": name, "expected_revision": record["revision"], **extra})


def test_missing_context_is_a_durable_observed_profile_not_a_guessed_declaration(setup):
    app, inputs, service = setup
    record = create(service, app)
    assert record["status"] == "awaiting_information" and record["job_id"] is None
    assert inputs.calls == [str(app.source)] and not app.registered and record["declarations"] == {}
    audit = next(turn for turn in record["turns"] if turn["kind"] == "data_audit")
    assert audit["content"]["observed"]["expression_values_read"] is False
    capture = next(field for field in record["clarification_fields"] if field["field"] == "capture_unit_key")
    assert [row["value"] for row in capture["options"]] == ["batch", "donor"]
    reopened = InvestigationService(app, inputs).get(record["id"])
    assert reopened["turns"] == record["turns"] and reopened["revision"] == record["revision"]


def test_partial_clarification_and_unknown_column_cannot_prepare_a_plan(setup):
    app, _, service = setup
    record = create(service, app)
    with pytest.raises(ValueError, match="observed metadata column"):
        action(service, record, "clarify", declarations={"capture_unit_key": "invented"})
    record = action(service, record, "clarify", declarations={"assay": "scrna"})
    assert record["status"] == "awaiting_information" and not app.registered
    record = action(service, record, "clarify", declarations=DECLARATIONS)
    assert record["status"] == "awaiting_operator_review" and record["job_state"]["proposal"]
    request, namespace = app.registered[0]
    assert namespace == record["id"] and request["declarations"] == DECLARATIONS
    assert request["expected_sha256"] == record["profile"]["sha256"]


def test_revision_conflict_and_unknown_action_fail_without_mutation(setup):
    app, _, service = setup
    record = create(service, app)
    with pytest.raises(RuntimeError, match="changed"):
        service.action(record["id"], {"action": "clarify", "expected_revision": 0, "declarations": DECLARATIONS})
    with pytest.raises(ValueError, match="declared fields"):
        action(service, record, "approve_without_review")
    assert service.get(record["id"])["revision"] == record["revision"]


def test_approval_and_run_use_original_controllers_and_separate_explicit_permissions(setup):
    app, _, service = setup
    record = create(service, app, declarations=DECLARATIONS)
    with pytest.raises(PermissionError, match="per-run"):
        action(service, record, "run")
    with pytest.raises(PermissionError, match="Startup"):
        action(service, record, "run", allow_data_execution=True)
    app.allow_data_execution = True
    with pytest.raises(RuntimeError, match="approved"):
        action(service, record, "run", allow_data_execution=True)
    record = action(service, record, "review", reviewer="Fixture operator", decision="approve", rationale="Software check", confirmations={"all": True})
    assert app.reviewed and record["status"] == "approved" and not app.started
    record = action(service, record, "run", allow_data_execution=True)
    assert app.started == [record["job_id"]] and record["status"] == "running"
    assert any(turn["kind"] == "execution_request" for turn in record["turns"])


def test_identical_followup_creates_a_new_approval_scope_and_retains_thread_history(setup):
    app, _, service = setup
    parent = create(service, app, declarations=DECLARATIONS)
    parent = action(service, parent, "review", reviewer="Fixture operator", decision="approve", rationale="Software check", confirmations={"all": True})
    child = action(service, parent, "follow_up", question=parent["question"])
    assert child["thread_id"] == parent["thread_id"] and child["parent_investigation"] == parent["id"]
    assert child["parent_job"] == parent["job_id"] and child["job_id"] != parent["job_id"]
    assert child["status"] == "awaiting_operator_review" and "review" not in child["job_state"]
    assert len(child["thread_turns"]) > len(child["turns"])
    assert service.get(parent["id"])["children"] == [child["id"]]


def test_actual_job_events_deduplicate_and_verified_results_create_scoped_synthesis(setup):
    app, _, service = setup
    record = create(service, app, declarations=DECLARATIONS)
    first = service.get(record["id"])
    assert len(first["turns"]) == len(record["turns"])
    app.records[record["job_id"]]["status"] = "completed_review_passed"
    app.records[record["job_id"]]["state_history"].append({"from": "running", "to": "completed_review_passed", "at": "2026-10-28T08:02:00Z"})
    complete = service.get(record["id"])
    assert complete["synthesis"]["supported_findings"]["metrics"] == {"cells_input": 2, "cells_retained": 2, "HVG_count": 3}
    assert complete["artifacts"][0]["job_id"] == record["job_id"]
    assert "no comparative" in complete["synthesis"]["method_evidence_assessment"]["selection_basis"]
    assert service.get(record["id"])["revision"] == complete["revision"]
    assert len([turn for turn in complete["turns"] if turn["kind"] == "job_event"]) == 2


@pytest.mark.parametrize("question,intent", [("Find a differential contrast", "differential_contrast"),
    ("Infer lineage and regulons", "trajectory_or_regulon"), ("Which method is best?", "method_selection"),
    ("Compare neuronal populations between day 10 and day 12, identify meaningful changes in cell composition and gene expression, and show whether those differences remain after accounting for batch effects.", "differential_contrast"),
    ("Does batch correction cause changes in clustering?", "method_selection")])
def test_unsupported_intents_never_silently_execute_qc(setup, question, intent):
    app, _, service = setup
    record = service.create({"question": question, "source": str(app.source), "declarations": DECLARATIONS})
    assert record["intent"]["kind"] == intent and record["intent"]["inferred"] is True
    assert record["status"] == "awaiting_information" and record["required_action"] == "configure_scientific_workspace"
    assert not app.registered and not app.started


def test_explicit_parameters_are_retained_separately_and_profile_conflicts_do_not_mutate_it(setup):
    app, _, service = setup
    requirement = {"name": "n_pcs", "value": 9, "evidence": "n_pcs=9"}
    record = service.create({"question": "Inspect cell quality with n_pcs=9", "source": str(app.source),
        "declarations": DECLARATIONS, "user_parameter_requirements": [requirement]})
    assert record["user_parameter_requirements"] == [requirement] and not app.registered
    assert record["status"] == "awaiting_information" and app.parameters["n_pcs"] == 8


def test_selection_is_exact_and_receipt_bound_not_a_population_execution_grant(setup):
    app, inputs, service = setup
    parent = create(service, app, declarations=DECLARATIONS)
    sha = parent["profile"]["sha256"]
    app.payload = app.workspace / "saved-geometry.json"
    app.payload.write_text(json.dumps({"panels": [{"panel_id": "batch", "rows": [
        {"observation_id": "c0", "status": "rendered"}, {"observation_id": "c1", "status": "rendered"},
        {"observation_id": "excluded", "status": "excluded"}]}]}))
    app.figure_inventory = {"parent_root": "b" * 64, "figures": [{"figure_id": "umap", "analysis_binding": {"input_sha256": sha}, "plot_data": {"path": "saved-geometry.json"}}]}
    selection = {"figure_id": "umap", "panel_id": "batch", "observation_ids": ["c0"], "source_job": parent["job_id"], "parent_root": "b" * 64, "display_only": True}
    child = action(service, parent, "follow_up", question="Inspect quality for this selection", selection=selection)
    assert child["selection"]["observation_ids"] == ["c0"] and "not_a_population_analysis_grant" in child["selection"]["scope"]
    assert child["turns"][0]["content"]["selection"] == child["selection"]
    assert child["status"] == "awaiting_information" and child["required_action"] == "selection_scope" and child["job_id"] is None
    assert len(app.registered) == 1
    child = action(service, child, "clarify", scientific_context={"selection_scope": "saved_evidence_only"})
    assert child["status"] == "completed_saved_evidence" and child["synthesis"]["supported_findings"]["observation_ids"] == ["c0"]
    assert child["synthesis"]["method_evidence_assessment"]["new_analysis_executed"] is False and len(app.registered) == 1
    full = service.create({"question": "Inspect quality with the selection as context", "source": str(app.source), "declarations": DECLARATIONS,
        "parent_job": parent["job_id"], "selection": selection, "scientific_context": {"selection_scope": "context_only_full_dataset"}})
    assert full["job_id"] is not None and full["population_scope"]["analysis_population"] == "full_registered_dataset"
    assert app.registered[-1][0]["source"] == str(app.source) and "selection" not in app.registered[-1][0]
    parent = service.get(parent["id"])
    bad = action(service, parent, "follow_up", question="Inspect other cells", selection={**selection, "observation_ids": ["excluded"]})
    assert bad["status"] == "operation_failed" and bad["job_id"] is None
    assert "absent" in bad["error"]["message"]


def test_symlink_records_and_tampered_ancestry_fail_closed(setup, tmp_path):
    app, _, service = setup
    record = create(service, app)
    path = service._path(record["id"])
    copied = tmp_path / "copy.json"
    copied.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(copied)
    with pytest.raises(ValueError, match="escaped"):
        service.get(record["id"])
    path.unlink()
    copied.replace(path)
    value = json.loads(path.read_text())
    value["parent_investigation"] = record["id"]
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="cyclic"):
        service.get(record["id"])


def test_inspection_failure_is_preserved_without_registration(setup):
    app, inputs, service = setup
    def fail(_):
        raise ValueError("Actual malformed header")
    inputs.inspect = fail
    record = create(service, app)
    assert record["status"] == "operation_failed" and record["profile"] is None
    assert record["error"]["message"] == "Actual malformed header" and not app.registered


def test_list_uses_actual_job_status_and_no_unrecorded_events(setup):
    app, _, service = setup
    record = create(service, app, declarations=DECLARATIONS)
    app.records[record["job_id"]]["status"] = "running"
    rows = service.list()["investigations"]
    assert rows[0]["status"] == "running" and rows[0]["dataset"] == app.source.name
    assert len(service._load(record["id"])["turns"]) == len(record["turns"])


def test_literal_parameter_assignments_require_matching_source_and_are_not_defaults(setup):
    app, _, service = setup
    record = service.create({"question": "Inspect quality with n_pcs=9", "source": str(app.source), "declarations": DECLARATIONS})
    assert record["user_parameter_requirements"] == [{"name": "n_pcs", "value": 9, "evidence": "n_pcs=9"}]
    assert record["status"] == "awaiting_information" and not app.registered
    spaced = service.create({"question": "Inspect quality with n_pcs 9", "source": str(app.source), "declarations": DECLARATIONS})
    assert spaced["user_parameter_requirements"] == [{"name": "n_pcs", "value": 9, "evidence": "n_pcs 9"}]
    with pytest.raises(ValueError, match="match the submitted question"):
        create(service, app, user_parameter_requirements=[{"name": "n_pcs", "value": 9, "evidence": "a source the user did not supply"}])


def test_live_native_proposal_requires_matching_explicit_data_scope_and_campaign_grant(setup):
    app, _, service = setup
    app.selector_config = {"backend": "codex-cli"}
    app.allow_model_execution = True
    app.model_data_scope = "synthetic"
    record = create(service, app, declarations=DECLARATIONS, proposal_mode="agent")
    assert record["required_action"] == "authorize_model_planning" and not app.registered
    record = action(service, record, "clarify", scientific_context={"data_classification": "private", "allow_model_execution": True})
    assert record["status"] == "awaiting_information" and not app.registered
    record = action(service, record, "clarify", scientific_context={"data_classification": "public", "allow_model_execution": True})
    assert record["status"] == "awaiting_information" and not app.registered  # wrong server data scope
    record = action(service, record, "clarify", scientific_context={"data_classification": "synthetic", "allow_model_execution": True})
    assert record["status"] == "awaiting_operator_review" and len(app.registered) == 1


class Scientific:
    """Protocol fixture: real schema validation, no model or numerical execution."""
    def __init__(self, app):
        self.app, self.calls, self.revision, self.initialized = app, [], 0, False
        self.root = app.workspace / "scientific"
        self.versions, self.plans, self.operations, self.steps = {}, {}, [], []
        self.logical_datasets, self.logical_analyses = {}, {}
        self.mode, self.configured = "disabled", False
        self.scientific_workflow, self.selector = None, None
        self.artifact_records = {}

    def configuration(self):
        return {"planning_mode": self.mode, "scientific_workflow_configured": self.configured,
                "model_execution_allowed": self.app.allow_model_execution}

    @property
    def selector_config(self):
        return None if self.mode == "disabled" else {"backend": self.mode, "model_execution": self.mode != "recorded_fixture"}

    def catalog(self):
        return {"revision": self.revision, "initialized": self.initialized, "studies": [], "datasets": [], "analyses": [],
                "operations": deepcopy(self.operations), "workflow_steps": deepcopy(self.steps)}

    def store(self):
        return self

    def methods(self):
        return self

    def planner(self):
        return self

    def resolve(self, ref):
        return deepcopy(self.versions[ref["version_id"]])

    def input_bindings(self, ids):
        return {"dataset": self.versions[ids[0]]["manifest"]["members"]["dataset"]["artifact_id"]}

    def get(self, kind, identity):
        if kind == "dataset":
            return deepcopy(self.logical_datasets[identity])
        assert kind == "artifact"
        if identity in self.artifact_records:
            return deepcopy(self.artifact_records[identity])
        return {"sha256": hashlib.sha256(self.app.source.read_bytes()).hexdigest()}

    def download(self, identity):
        if identity not in self.artifact_records:
            raise KeyError("Artifact is not linked to a scientific record in this project")
        return self.app.workspace / "scientific-output.json", "application/json", identity + ".json"

    def evidence_assessment(self, session_id):
        assert session_id in self.plans
        return {"scope": "protocol_fixture_only", "independent_scientific_validation": False, "candidates": []}

    def _plan(self, identity):
        return deepcopy(self.plans[identity])

    def action(self, body):
        from devbench.analysis.method_planner_contract import scientific_context
        assert body["expected_revision"] == self.revision
        self.calls.append(deepcopy(body))
        self.revision += 1
        name = body["action"]
        identity, version = uuid.uuid4().hex, hashlib.sha256(str(self.revision).encode()).hexdigest()
        if name == "initialize":
            self.initialized = True
            return {"initialized": True}
        if name == "create_study":
            return {"study_id": identity, "name": body["name"]}
        if name == "register_dataset":
            ref = {"object_type": "dataset", "object_id": body.get("dataset_id", identity), "version_id": version}
            self.logical_datasets[ref["object_id"]] = {"dataset_id": ref["object_id"], "study_id": body["study_id"]}
            self.versions[version] = {"dataset_id": ref["object_id"], "version_id": version, "ref": ref, "manifest": {"metadata": body["metadata"],
                "members": {"dataset": {"artifact_id": "c" * 64}}}}
            return {"dataset_id": ref["object_id"], "ref": ref}
        if name == "create_analysis":
            ref = {"object_type": "analysis", "object_id": identity, "version_id": version}
            self.logical_analyses[identity] = {"analysis_id": identity, "study_id": body["study_id"]}
            self.versions[version] = {"version_id": version, "ref": ref, "definition": {"task": {
                "scientific_objective": body["objective"], "computational_task": body["computational_task"]}}}
            return {"analysis_id": identity, "ref": ref}
        if name == "create_plan":
            metadata = self.resolve(body["dataset_ref"])["manifest"]["metadata"]
            summary = {key: metadata[key] for key in ("observed", "inferred", "unknowns")}
            summary["artifact_ids"] = ["c" * 64]
            context = scientific_context(self.resolve(body["analysis_ref"])["definition"]["task"], summary)
            # This fixture represents the trusted typed-adapter response; the
            # adapter itself has separate actual AnnData header/hash tests.
            if body.get("rna_input_contract", {}).get("format") == "devbench.scrna-input-binding/1":
                context["dataset_summary"]["observed"]["scrna_input"] = {"scope": "synthetic protocol fixture"}
            self.plans[identity] = {"session_id": identity, "context": context,
                "scientific_anchors": {"study_id": self.logical_analyses[body["analysis_ref"]["object_id"]]["study_id"],
                    "analysis_version": body["analysis_ref"], "dataset_versions": [body["dataset_ref"]]}}
            return deepcopy(self.plans[identity])
        if name == "advance_to_gate":
            operation = {"operation_id": identity, "action": name, "status": "queued"}
            self.operations.append(operation)
            return deepcopy(operation)
        if name == "declare_trial":
            plan = self.plans[body["session_id"]]
            experiment = {**deepcopy(body["spec"]), "status": "declared"}
            plan.setdefault("experiments", []).append(experiment)
            previous = self.analysis_ref
            ref = {**previous, "version_id": version}
            self.versions[version] = {**deepcopy(self.versions[previous["version_id"]]), "version_id": version, "ref": ref}
            plan.setdefault("scientific_trial_definitions", {})[experiment["id"]] = ref
            return {"experiment": experiment, "analysis_ref": ref}
        raise AssertionError(name)


def test_scientific_route_bootstraps_typed_metadata_then_asks_for_layer_and_grants(setup):
    app, _, service = setup
    app.scientific = Scientific(app)
    record = service.create({"question": "Which method is best for this assay?", "source": str(app.source), "declarations": DECLARATIONS})
    assert record["status"] == "awaiting_information" and record["required_action"] == "measurement_layer"
    assert [row["action"] for row in app.scientific.calls] == ["initialize", "create_study", "register_dataset", "create_analysis"]
    assert record["scientific_context"]["dataset_ref"] and not app.registered
    field = record["clarification_fields"][0]
    assert field["field"] == "counts_layer" and field["scope"] == "scientific_context"
    record = action(service, record, "clarify", scientific_context={"counts_layer": "X"})
    assert record["scientific_session_id"] and record["required_action"] == "configure_scientific_workflow"
    contract = next(row for row in app.scientific.calls if row["action"] == "create_plan")["rna_input_contract"]
    assert contract["format"] == "devbench.scrna-input-binding/1" and contract["declarations"]["biological_independence"] is None
    assert "bulk_rna_input" not in app.scientific.plans[record["scientific_session_id"]]["context"]["dataset_summary"]["observed"]
    assert not app.started


def test_explicit_offline_campaign_syncs_actual_steps_and_stops_at_review(setup):
    app, _, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True}})
    assert record["scientific_operations"] and not app.started
    sent = next(row for row in app.scientific.calls if row["action"] == "advance_to_gate")
    assert sent["allow_model_execution"] is False
    identity = record["scientific_operations"][0]
    app.scientific.steps = [{"session_id": record["scientific_session_id"], "node": "audit", "attempt": 0, "status": "active"}]
    app.scientific.operations[0]["status"] = "running"
    active = service.get(record["id"])
    assert active["operation_pending"] is True
    assert any(turn["kind"] == "scientific_step" and turn["content"]["step"]["status"] == "active" for turn in active["turns"])
    app.scientific.steps[0]["status"] = "completed"
    app.scientific.operations[0].update(status="completed", value={"status": "awaiting_operator_review", "gate": "review", "phase": "design_review", "requested_phase": "planning"})
    complete = service.get(record["id"])
    assert complete["gate"] == "review" and complete["status"] == "awaiting_operator_review" and complete["operation_pending"] is False
    assert not app.reviewed and not app.started


@pytest.mark.parametrize("stage", ["discovery", "candidate_review", "implementation", "design_review", "science", "resolve", "design_trials", "draft_method", "declare_trial"])
def test_every_scientific_session_action_rejects_an_unrelated_session(setup, stage):
    app, _, service = setup
    app.scientific = Scientific(app)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X"}})
    count = len(app.scientific.calls)
    with pytest.raises(ValueError, match="exact planner session"):
        action(service, record, "scientific", request={"action": stage, "session_id": "f" * 32, "expected_revision": app.scientific.revision})
    assert len(app.scientific.calls) == count


def test_scientific_model_grant_cannot_override_private_input_scope(setup):
    app, _, service = setup
    app.scientific = Scientific(app)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "private", "allow_model_execution": True}})
    with pytest.raises(PermissionError, match="public or synthetic"):
        action(service, record, "scientific", request={"action": "science", "session_id": record["scientific_session_id"],
            "expected_revision": app.scientific.revision, "allow_model_execution": True})


def test_post_execution_synthesis_requires_its_own_current_campaign_grant_and_readiness(setup, monkeypatch):
    from devbench.analysis import workflow_entrypoints
    app, _, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    ready = [False]
    monkeypatch.setattr(workflow_entrypoints, "synthesis_ready", lambda *args, **kwargs: ready[0], raising=False)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True, "auto_synthesis": True}})
    app.scientific.operations[0].update(status="completed", value={"status": "awaiting_operator_review", "gate": "review", "requested_phase": "planning"})
    first = service.get(record["id"])
    assert not first.get("auto_synthesis_requested")
    assert len(app.scientific.operations) == 1
    ready[0] = True  # The separately tested read-only validator reports complete verified trials.
    advanced = service.get(record["id"])
    assert advanced["auto_synthesis_requested"] is True
    synthesis_calls = [row for row in app.scientific.calls if row.get("phase") == "synthesis"]
    assert len(synthesis_calls) == 1 and synthesis_calls[0]["allow_model_execution"] is False
    service.get(record["id"])
    assert len([row for row in app.scientific.calls if row.get("phase") == "synthesis"]) == 1


def test_restart_does_not_renew_a_retained_synthesis_model_or_campaign_grant(setup, monkeypatch):
    from devbench.analysis import workflow_entrypoints
    app, inputs, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    monkeypatch.setattr(workflow_entrypoints, "synthesis_ready", lambda *args, **kwargs: True, raising=False)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True, "auto_synthesis": True}})
    app.scientific.operations[0].update(status="completed", value={"status": "awaiting_operator_review", "gate": "review", "requested_phase": "planning"})
    reopened = InvestigationService(app, inputs).get(record["id"])
    assert reopened["required_action"] == "renew_synthesis_authorization"
    assert not reopened.get("auto_synthesis_requested") and len(app.scientific.operations) == 1


def test_planning_only_campaign_does_not_authorize_automatic_synthesis(setup, monkeypatch):
    from devbench.analysis import workflow_entrypoints
    app, _, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    def unexpected(*args, **kwargs):
        raise AssertionError("Planning-only grant cannot even request synthesis readiness")
    monkeypatch.setattr(workflow_entrypoints, "synthesis_ready", unexpected, raising=False)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True}})
    app.scientific.operations[0].update(status="completed", value={"status": "awaiting_operator_review", "gate": "review", "requested_phase": "planning"})
    service.get(record["id"])
    assert len(app.scientific.operations) == 1


def test_scientific_published_artifacts_are_bound_and_unavailable_links_are_explicit(setup):
    app, _, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True}})
    session = record["scientific_session_id"]
    ref = {"object_type": "result", "object_id": "e" * 32, "version_id": "e" * 64}
    output = app.workspace / "scientific-output.json"
    output.write_text('{"retained":2}')
    identity, absent = hashlib.sha256(output.read_bytes()).hexdigest(), "d" * 64
    app.scientific.artifact_records[identity] = {"sha256": identity, "size_bytes": output.stat().st_size, "artifact_type": "json", "suffix": ".json"}
    run_id = "c" * 32
    app.scientific.versions[ref["version_id"]] = {"run_id": run_id, "attempt_id": "b" * 32, "validation_id": "a" * 32,
        "artifact_ids": [identity, absent], "review": {"review_status": "candidate"},
        "run": {"trigger": {"kind": "run_trial", "request": {"session_id": session}}}}
    app.scientific.operations[0].update(status="completed", value={"run_id": run_id, "results": [{"ref": ref}], "unavailable": []})
    value = service.get(record["id"])
    assert value["artifacts"][0]["artifact_id"] == identity
    assert value["artifacts"][0]["download_path"] == "/api/scientific/artifacts/" + identity
    assert value["artifacts"][0]["scientific_session_id"] == session
    assert value["scientific_results"][0]["ref"] == ref
    assert value["artifact_unavailable"][0]["artifact_id"] == absent
    assert "not linked" in value["artifact_unavailable"][0]["reason"]


def test_scientific_published_result_cannot_cross_thread_sessions(setup):
    app, _, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True}})
    ref = {"object_type": "result", "object_id": "e" * 32, "version_id": "e" * 64}
    app.scientific.versions[ref["version_id"]] = {"run": {"trigger": {"kind": "run_trial", "request": {"session_id": "f" * 32}}}}
    app.scientific.operations[0].update(status="completed", value={"run_id": "c" * 32, "results": [{"ref": ref}]})
    value = service.get(record["id"])
    assert not value["artifacts"] and "another scientific planner" in value["artifact_unavailable"][0]["reason"]


def test_scientific_gate_requires_verified_cursor_and_explicit_exact_trial_operator_review(setup, monkeypatch):
    from devbench.analysis.workbench_investigations import SCIENTIFIC_CONFIRMATIONS
    app, _, service = setup
    app.scientific = Scientific(app)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X"}})
    session = record["scientific_session_id"]
    spec = {"id": "frozen-trial", "candidates": ["Fixture method"], "parameters": {"Fixture method": {"setting": 1}},
        "resource_limits": {"timeout_seconds": 30, "memory_mb": 256}, "metrics": [{"name": "test_metric"}],
        "metric_sources": {"test_metric": {"kind": "validation"}}, "subset_validity": {"preserves_preconditions": True}}
    app.scientific.plans[session]["trial_designs"] = [{"proposals": [spec]}]
    cursor = {"next": ["review"], "phase": "design_review", "status": "ready", "trial_ids": [], "trial_index": 0, "failure": None}
    monkeypatch.setattr(service, "_scientific_cursor", lambda *args: deepcopy(cursor))
    internal = service._load(record["id"])
    service._project_scientific_plan(internal)
    assert internal["gate_options"]["can_submit"] is True
    assert internal["gate_options"]["proposals"][0]["spec"] == spec
    assert len(internal["gate_options"]["required_confirmations"]) == 5
    request = {"action": "declare_trial", "session_id": session, "spec": spec, "expected_revision": app.scientific.revision}
    before = len(app.scientific.calls)
    with pytest.raises(ValueError, match="operator review"):
        action(service, record, "scientific", request=request)
    assert len(app.scientific.calls) == before
    review = {"reviewer": "Explicit test operator", "rationale": "Reviewed software fixture criteria", "confirmations": {key: True for key in SCIENTIFIC_CONFIRMATIONS}}
    with pytest.raises(ValueError, match="exact eligible frozen"):
        action(service, record, "scientific", request={**request, "spec": {**spec, "id": "invented"}}, operator_review=review)
    app.scientific.analysis_ref = record["scientific_context"]["analysis_ref"]
    declared = action(service, record, "scientific", request=request, operator_review=review)
    assert app.scientific.calls[-1]["actor"] == review["reviewer"]
    assert "operator_review" not in app.scientific.calls[-1]
    assert declared["scientific_context"]["analysis_ref"] == app.scientific.plans[session]["scientific_trial_definitions"][spec["id"]]
    assert any(turn["kind"] == "plan_review" and turn["content"].get("operator_review") == review for turn in declared["turns"])
    app.allow_data_execution = True
    cursor.update(next=["execution_review"], phase="declare", trial_ids=[spec["id"]])
    internal = service._load(record["id"])
    service._project_scientific_plan(internal)
    assert internal["gate_options"]["gate"] == "execution_review" and internal["gate_options"]["can_submit"] is True
    assert internal["gate_options"]["declared_trials"][0]["experiment_id"] == spec["id"]
    assert internal["gate_options"]["required_confirmations"] == []


def test_unknown_or_failed_scientific_cursor_cannot_offer_a_submittable_gate(setup, monkeypatch):
    app, _, service = setup
    app.scientific = Scientific(app)
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X"}})
    internal = service._load(record["id"])
    service._project_scientific_plan(internal)
    assert internal["gate_options"]["can_submit"] is False
    monkeypatch.setattr(service, "_scientific_cursor", lambda *args: {"next": ["review"], "status": "failed", "phase": "design_review", "trial_ids": [], "trial_index": 0})
    service._project_scientific_plan(internal)
    assert internal["gate_options"]["can_submit"] is False


@pytest.mark.parametrize("startup_permission,startup_scope,declared_scope", [
    (True, "synthetic", "public"), (True, "public", "synthetic"), (False, "public", "public")])
def test_scientific_model_campaign_and_direct_actions_require_exact_current_startup_scope(
        setup, startup_permission, startup_scope, declared_scope):
    app, _, service = setup
    app.allow_model_execution = startup_permission
    app.model_data_scope = startup_scope
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "codex-cli", True
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": declared_scope,
            "allow_model_execution": True, "auto_advance": True}})
    assert not any(row["action"] == "advance_to_gate" for row in app.scientific.calls)
    assert record["status"] == "awaiting_information"
    before = len(app.scientific.calls)
    with pytest.raises(PermissionError, match="startup permission|server data scope"):
        action(service, record, "scientific", request={"action": "advance_to_gate",
            "session_id": record["scientific_session_id"], "expected_revision": app.scientific.revision,
            "actor": "Fixture operator", "idempotency_key": "must-not-dispatch", "allow_model_execution": True})
    assert len(app.scientific.calls) == before


def test_scientific_model_campaign_with_exact_current_startup_scope_can_advance(setup):
    app, _, service = setup
    app.allow_model_execution = True
    app.model_data_scope = "public"
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "codex-cli", True
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "public",
            "allow_model_execution": True, "auto_advance": True}})
    sent = [row for row in app.scientific.calls if row["action"] == "advance_to_gate"]
    assert len(sent) == 1 and sent[0]["allow_model_execution"] is True
    assert record["scientific_operations"] and not app.started


@pytest.mark.parametrize("suffix,contents", [
    (".json", '{"samples":["s1","s2"],"metadata":{"assay":"unknown"}}'),
    (".csv", "sample,condition\ns1,A\ns2,B\n")])
def test_real_service_accepts_discovered_generic_scientific_inputs_without_weakening_native_h5ad_boundary(suffix, contents):
    from devbench.analysis.workbench_ui import WorkbenchService
    from devbench.analysis.workbench_inputs import InputDiscovery
    base = Path(__file__).resolve().parents[1] / "reports/investigation-input-admission" / uuid.uuid4().hex
    inputs = base / "inputs"
    inputs.mkdir(parents=True)
    source = inputs / ("scientific-input" + suffix)
    source.write_text(contents)
    app = WorkbenchService(base / "service", [inputs])
    try:
        discovery = InputDiscovery(app.input_roots)
        service = InvestigationService(app, discovery)
        with pytest.raises(ValueError, match="H5AD|h5ad"):
            app._input(str(source))
        record = service.create({"question": "Which method is appropriate for these data?", "source": str(source)})
        assert record["status"] == "awaiting_information"
        assert record["profile"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert record["profile"]["observed"]["file_type"] == suffix[1:]
        assert record["job_id"] is None and app.jobs() == []
        assert service.get(record["id"])["source"] == str(source)
    finally:
        app.close()


def test_scientific_synthesis_keeps_metrics_primary_and_complete_receipts_secondary(setup):
    app, _, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "auto_advance": True}})
    session = record["scientific_session_id"]
    plan = app.scientific.plans[session]
    plan["context"]["dataset_summary"]["unknowns"] = []
    result = {"experiment_id": "actual-trial", "candidate": "Fixture method", "status": "success",
        "metric_values": {"retained": 2}, "warnings": ["Software-only fixture"], "failures": [],
        "provenance": {"run_id": "a" * 32, "declaration_sha256": "b" * 64}}
    plan["results"] = [result]
    assessment = {"scope": "comparative_pilot_only", "independent_scientific_validation": False,
        "limitations": ["No joint comparative evidence is available."],
        "candidates": [{"candidate": "Fixture method", "reasons": ["At least two comparable successful candidates are required."]}]}
    app.scientific.evidence_assessment = lambda _: deepcopy(assessment)
    receipt = {"status": "abstained", "scientific_qualification": "not_established", "requested_phase": "synthesis",
        "steps": [{"node": "verify", "request_sha256": "c" * 64, "result_sha256": "d" * 64}], "operation_refs": ["verify:0", "recommend:0", "conclusion_review:0"]}
    app.scientific.operations[0].update(status="completed", value=deepcopy(receipt))
    value = service.get(record["id"])
    synthesis = value["synthesis"]
    primary = synthesis["supported_findings"]
    assert primary["workflow_status"] == "abstained"
    assert primary["trial_results"][0]["metric_values"] == {"retained": 2}
    assert "governed_outputs" not in primary and "provenance" not in primary["trial_results"][0]
    assert "request_sha256" not in json.dumps(primary)
    assert synthesis["evidence"]["governed_outputs"] == receipt
    assert synthesis["evidence"]["trial_results"][0]["provenance"] == result["provenance"]
    reasons = [row.get("reason", row.get("impact")) for row in synthesis["uncertainties"]]
    assert "No joint comparative evidence is available." in reasons
    assert any("two comparable" in reason for reason in reasons)
    assert any(row["field"] == "independent_scientific_validation" for row in synthesis["uncertainties"])


def test_scientific_planning_source_is_captured_from_bound_selector_and_never_backfilled_on_get(setup):
    app, inputs, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode = "recorded_fixture"
    record = service.create({"question": "Which method is best?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X"}})
    source = deepcopy(record["planning_source"])
    assert source["backend"] == "recorded_fixture"
    assert source["source"] == "configured_scientific_planner_at_dispatch"
    assert source["capture_event"] == "session_created"
    assert source["session_id"] == record["scientific_session_id"]
    assert "model_sessions_attempted" not in source
    app.scientific.mode = "codex-cli"
    assert service.get(record["id"])["planning_source"] == source
    retained = service._load(record["id"])
    retained.pop("planning_source")
    retained.pop("planning_source_history", None)
    service._save(retained)
    assert "planning_source" not in InvestigationService(app, inputs).get(record["id"])


def test_scientific_followup_inherits_only_bound_data_and_creates_a_fresh_question_scope(setup):
    app, inputs, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    parent = service.create({"question": "Which method is best with n_pcs=8?", "source": str(app.source), "declarations": DECLARATIONS,
        "scientific_context": {"counts_layer": "X", "data_classification": "synthetic", "allow_model_execution": False,
            "auto_advance": True, "auto_synthesis": True, "selection_scope": "context_only_full_dataset"}})
    context_before = deepcopy(parent["scientific_context"])
    calls_before = deepcopy(app.scientific.calls)
    child = action(service, parent, "follow_up", question="Which method best preserves this dataset's structure?")
    assert child["scientific_context"]["study_id"] == parent["scientific_context"]["study_id"]
    assert child["scientific_context"]["dataset_ref"] == parent["scientific_context"]["dataset_ref"]
    assert child["scientific_context"]["rna_input_contract"] == parent["scientific_context"]["rna_input_contract"]
    assert child["scientific_context"]["counts_layer"] == "X" and child["scientific_context"]["data_classification"] == "synthetic"
    assert child["scientific_session_id"] != parent["scientific_session_id"]
    assert child["scientific_context"]["analysis_ref"]["object_id"] != parent["scientific_context"]["analysis_ref"]["object_id"]
    assert app.scientific.plans[child["scientific_session_id"]]["context"]["task"]["scientific_objective"] == child["question"]
    for key in ("allow_model_execution", "auto_advance", "auto_synthesis", "selection_scope", "plan_request", "advance_request"):
        assert key not in child["scientific_context"]
    assert child["scientific_operations"] == [] and child["user_parameter_requirements"] == []
    assert not any(row["action"] == "advance_to_gate" for row in app.scientific.calls[len(calls_before):])
    assert service.get(parent["id"])["scientific_context"] == context_before
    assert child["context_inheritance"]["parent_investigation"] == parent["id"]
    assert child["context_inheritance"]["source"] == "retained_parent_data_bindings"
    assert "data_classification" in child["context_inheritance"]["inherited_fields"]


def test_scientific_followup_recovers_binding_from_parent_plan_request_without_reusing_old_objective_or_grants(setup):
    app, inputs, service = setup
    app.scientific = Scientific(app)
    app.scientific.mode, app.scientific.configured = "recorded_fixture", True
    original_inspect = inputs.inspect
    def bulk_inspect(source):
        profile = original_inspect(source)
        profile["observed"].update(file_type="json", cells=None, genes=None, obs_columns=[])
        return profile
    inputs.inspect = bulk_inspect
    declarations = {"assay": "bulk", "measurement": "non_umi_counts", "count_provenance": "Synthetic bulk fixture"}
    parent = service.create({"question": "Which method best fits these bulk data?", "source": str(app.source), "declarations": declarations,
        "scientific_context": {"data_classification": "synthetic"}})
    retained = service._load(parent["id"])
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": "c" * 64,
        "metadata_artifact_id": "d" * 64, "declarations": {"metadata_roles": {"condition": "condition", "biological_unit": "donor"}}}
    retained["scientific_context"] = {"plan_request": {"action": "create_plan", "dataset_ref": parent["scientific_context"]["dataset_ref"],
        "analysis_ref": parent["scientific_context"]["analysis_ref"], "rna_input_contract": binding,
        "expected_revision": 0, "actor": "Old operator"}, "data_classification": "synthetic",
        "allow_model_execution": True, "auto_advance": True, "auto_synthesis": True,
        "advance_request": {"idempotency_key": "old-campaign"}}
    service._save(retained)
    child = action(service, retained, "follow_up", question="Which alternative method should be compared next?",
        scientific_context={"data_classification": "public"})
    assert child["scientific_context"]["rna_input_contract"] == binding
    assert child["scientific_context"]["dataset_ref"] == parent["scientific_context"]["dataset_ref"]
    assert child["scientific_context"]["data_classification"] == "public"
    assert child["scientific_session_id"] != parent["scientific_session_id"] and not child["scientific_operations"]
    assert "plan_request" not in child["scientific_context"] and "advance_request" not in child["scientific_context"]
    assert "data_classification" in child["context_inheritance"]["overridden_fields"]
    assert "data_classification" not in child["context_inheritance"]["inherited_fields"]
