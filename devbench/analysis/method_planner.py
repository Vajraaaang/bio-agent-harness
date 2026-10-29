"""Open-world scientific planning over the existing method execution service.

Selectors propose decisions; this coordinator binds versions, checks observed
preconditions, freezes trials, measures results and records scoped approvals.
Caller-supplied observations and actor labels are not independent validation or
authenticated identities. No model, download or trial runs on construction.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import ast
import math
import re
import time
import uuid
import unicodedata

from devharness_core.methods import MethodContract, validate_action_scope
from devharness_core.provenance import canonical_json, hash_artifact
from .episode_agent import AgentLimits, Selection, selector_configuration
from .method_planner_contract import (scientific_context, validate_scientific_candidates,
    validate_resolution, validate_recommendation, validate_plan, validate_metrics,
    validate_planning_metadata, validate_parameter_origins, validate_preliminary_explanation)
from .method_validation import coordinator_measurement_provenance, finite_json, observation_prerequisite_inspection
from .method_executor import _path
from .method_planner_recovery import plan_operation
from .methods import MethodService, _digest, _label, _reason, _now


_SCOPES = ("approve_recommendation", "approve_full_execution", "approve_analysis_plan_change",
           "save_experimental_recipe", "promote_to_registry")
_SCIENTIFIC_PUBLICATIONS = {"scientific_candidates_frozen", "scientific_candidates_reconciled",
    "method_capabilities_resolved", "scientific_trial_designs_proposed", "experimental_implementation_generated",
    "preliminary_explanation_nominated", "scientific_recommendation_proposed"}
_ABSENT = object()
ACTION_PREREQUISITE_POLICIES = {
    "retain": ("input_integrity_valid", "observation_ids_unique", "metrics_finite"),
    "diagnostic_flagging": ("input_integrity_valid", "observation_ids_unique", "metrics_finite"),
    "exclude": ("input_integrity_valid", "observation_ids_unique", "metrics_finite",
                "assay_semantics_known", "exclusion_evidence_supported", "exclusion_authorized"),
    "infer": ("input_integrity_valid", "observation_ids_unique", "metrics_finite",
              "assay_semantics_known", "biological_units_independent", "design_identifiable"),
}
BULK_RNA_NUMERIC_PREREQUISITES = ("bulk_rna_input.input_integrity_valid", "bulk_rna_input.gene_ids_unique",
    "bulk_rna_input.sample_ids_unique", "bulk_rna_input.alignment_valid", "bulk_rna_input.measurements_finite")
ACTION_PREREQUISITE_POLICY = {"format": "devbench.action-prerequisites/1",
    "minimum_requirements": {intent: list(fields) for intent, fields in ACTION_PREREQUISITE_POLICIES.items()},
    "interpretation_requirements": {"qc_review": ["assay_semantics_known"]},
    "exclusions_requirements": list(ACTION_PREREQUISITE_POLICIES["exclude"]),
    "bulk_rna_numeric_requirements": list(BULK_RNA_NUMERIC_PREREQUISITES)}
OBSERVATION_FACT_CATALOG = {
    "input_integrity_valid": "Bounded immutable input hash, format, structure and type checks passed; not scientific validity.",
    "observation_ids_unique": "Observation identifiers are bounded strings and unique; not independent biological units.",
    "metrics_finite": "Measurement values are numeric typed and finite under the explicitly declared input roles; not assay semantics.",
}
# Only named action-specific unknowns can cease blocking a less consequential
# action. Unrecognized unknowns and every model hard precondition remain binding.
_ACTION_SPECIFIC_FACTS = frozenset().union(*map(set, ACTION_PREREQUISITE_POLICIES.values())) - set(ACTION_PREREQUISITE_POLICIES["retain"])
_ACTION_SPECIFIC_FACTS = _ACTION_SPECIFIC_FACTS | {"technical_damage_validated", "biological_causal_validation", "failure_labels"}


def _name(value):
    normalized = "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())
    if not normalized:
        raise ValueError("Scientific method names must contain letters or digits")
    return normalized


def _observed(summary, field):
    value = summary["observed"]
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return _ABSENT
        value = value[part]
    return value


def _applicability(candidate, summary):
    candidate = deepcopy(candidate)
    blocked, violated = [], []
    scope = validate_action_scope(candidate["action_scope"]) if "action_scope" in candidate else None
    required = ACTION_PREREQUISITE_POLICIES[scope["intent"]] if scope else ()
    bulk = "bulk_rna_input" in summary["observed"]
    if bulk and scope:
        required = (*BULK_RNA_NUMERIC_PREREQUISITES, *(field for field in required
                     if field not in ACTION_PREREQUISITE_POLICIES["retain"]))
    if scope and scope["interpretation"] == "qc_review":
        required = tuple(dict.fromkeys((*required, "assay_semantics_known")))
    if scope and scope["exclusions"]:
        exclusion_required = ACTION_PREREQUISITE_POLICIES["exclude"]
        if bulk:
            exclusion_required = tuple(field for field in exclusion_required if field not in ACTION_PREREQUISITE_POLICIES["retain"])
        required = tuple(dict.fromkeys((*required, *exclusion_required)))
    for field in required:
        value = _observed(summary, field)
        if value is _ABSENT or value is None:
            blocked.append("Mandatory action prerequisite unknown: " + field)
        elif value is not True:
            violated.append("Mandatory action prerequisite violated: " + field)
    for assumption in candidate["assumptions"]:
        value = _observed(summary, assumption["field"])
        expected, operator = assumption["expected"], assumption["operator"]
        if value is _ABSENT or (value is None and {"scrna_input", "rna_input_set"} & summary["observed"].keys()):
            status = "unknown"
        else:
            if operator == "present":
                passed = value is not None
            elif operator == "equals":
                passed = type(value) is type(expected) and value == expected
            elif operator == "at_least":
                passed = type(value) in (int, float) and type(expected) in (int, float) and value >= expected
            else:
                passed = ((isinstance(value, list) and expected in value)
                          or (isinstance(value, str) and isinstance(expected, str) and expected in value))
            status = "satisfied" if passed else "violated"
        assumption["status"] = status
        assumption["evidence"] = "Coordinator-supplied observed metadata: " + assumption["field"] if value is not _ABSENT else None
        if assumption["hard_precondition"] and status != "satisfied":
            (blocked if status == "unknown" else violated).append(assumption["statement"])
    for unknown in summary["unknowns"]:
        if scope and unknown["field"] in _ACTION_SPECIFIC_FACTS and unknown["field"] not in required:
            continue
        if unknown.get("blocking", True):
            blocked.append(unknown["field"] + ": " + unknown["impact"])
    if candidate["dataset_fit"] == "inapplicable" or candidate["objective_fit"] == "inapplicable":
        violated.append("Candidate does not fit the dataset or objective")
    candidate["applicability"] = "inapplicable" if violated else "blocked_unknown" if blocked else "eligible"
    candidate["blockers"] = violated + blocked
    return candidate


def _empty_recommendation():
    return {"choice": None, "confidence": "no_recommendation", "scientific_confidence": "no_recommendation",
            "implementation_confidence": "unknown", "rationale": "No candidate has sufficient reviewed evidence yet",
            "params": {}, "approval_actions": [
                {"action": action, "required": True, "status": "not_requested"} for action in _SCOPES]}


class ScientificMethodPlanner:
    def __init__(self, methods: MethodService):
        self.methods = methods
        # Runtime objects are not serialized permissions. Restoring references
        # requires a fresh exact binding to trusted coordinator objects.
        self._model_campaigns = {}

    def create(self, task, dataset_summary, *, actor, prior_state=None, data_classification="unclassified",
               limits=None, anchors=None, evaluation_purpose="method_selection", user_parameter_requirements=None,
               rna_input_contract=None, rna_execution_inputs_ref=None, rna_execution_workspace=None,
               scientific_contract=None, rna_design=None):
        if rna_execution_workspace is not None:
            if rna_execution_inputs_ref is None:
                raise ValueError("An explicit execution workspace requires a governed RNA bridge")
            if not self.methods.root.is_relative_to(_path(rna_execution_workspace)):
                raise ValueError("Artifact service lies outside the assigned execution-input workspace")
        dataset_summary = deepcopy(dataset_summary)
        reserved = {"bulk_rna_input", "scrna_input", "rna_input_set", "fastq_input"} & set(dataset_summary.get("observed", {}))
        if rna_input_contract is None and reserved:
            raise ValueError("RNA inspection requires an explicit registered artifact binding")
        if rna_input_contract is not None:
            if type(rna_input_contract) is dict and rna_input_contract.get("format") == "devbench.fastq-input-binding/1":
                from .fastq_planner_binding import inspect_registered_fastq_input
                if reserved - {"fastq_input"}:
                    raise ValueError("FASTQ and matrix inspections cannot be mixed")
                inspection = inspect_registered_fastq_input(self.methods, rna_input_contract)
                inspection_key = "fastq_input"
            elif type(rna_input_contract) is dict and rna_input_contract.get("format") == "devbench.rna-input-set-binding/1":
                from .rna_input_set_contract import inspect_registered_input_set, header_only_facts
                if reserved - {"rna_input_set"}:
                    raise ValueError("An input set cannot be replaced by a singular RNA inspection")
                inspection = inspect_registered_input_set(self.methods, rna_input_contract, actor=actor)
                inspection_key = "rna_input_set"
                facts = header_only_facts()
                prior = {key: deepcopy(dataset_summary.get("observed", {})[key]) for key in facts
                         if key in dataset_summary.get("observed", {})}
                if prior:
                    dataset_summary.setdefault("inferred", {})["unverified_supplied_observed_claims"] = {
                        "origin": "supplied_dataset_summary", "values": prior,
                        "status": "Not verified by source-set header admission"}
                dataset_summary.setdefault("observed", {}).update(facts)
            elif type(rna_input_contract) is dict and rna_input_contract.get("format") == "devbench.scrna-input-binding/1":
                from .rna_scrna_input_contract import inspect_registered_scrna_input, header_only_facts
                if reserved - {"scrna_input"}:
                    raise ValueError("AnnData and bulk RNA bindings cannot be mixed in one input inspection")
                inspection = inspect_registered_scrna_input(self.methods, rna_input_contract, actor=actor)
                inspection_key = "scrna_input"
                facts = header_only_facts()
                prior = {key: deepcopy(dataset_summary.get("observed", {})[key]) for key in facts
                         if key in dataset_summary.get("observed", {})}
                if prior:
                    dataset_summary.setdefault("inferred", {})["unverified_supplied_observed_claims"] = {
                        "origin": "supplied_dataset_summary", "values": prior,
                        "status": "Not verified by the AnnData header-only audit"}
                dataset_summary.setdefault("observed", {}).update(facts)
            else:
                if reserved - {"bulk_rna_input"}:
                    raise ValueError("AnnData and bulk RNA bindings cannot be mixed in one input inspection")
                from .rna_input_contract import inspect_registered_bulk_rna_input
                inspection = inspect_registered_bulk_rna_input(self.methods, rna_input_contract, actor=actor)
                inspection_key = "bulk_rna_input"
            identities = {ref["artifact_id"] for ref in inspection["input_artifact_refs"].values()}
            if identities != set(dataset_summary.get("artifact_ids", [])):
                raise ValueError("RNA binding must match the declared dataset artifacts exactly")
            dataset_summary.setdefault("observed", {})[inspection_key] = inspection
        execution_binding = None
        if rna_execution_inputs_ref is not None:
            if rna_input_contract is None:
                raise ValueError("A governed execution bridge requires the exact original RNA binding")
            from .rna_execution_inputs import load_execution_inputs
            load_execution_inputs(rna_execution_inputs_ref, artifact_service=self.methods, dataset_binding=rna_input_contract,
                workspace=rna_execution_workspace)
            execution_binding = {"record_ref": deepcopy(rna_execution_inputs_ref), "dataset_binding": deepcopy(rna_input_contract)}
            if rna_execution_workspace is not None:
                execution_binding["workspace"] = str(_path(rna_execution_workspace))
        if "fastq_input" in dataset_summary.get("observed", {}) and rna_design is None:
            raise ValueError("FASTQ planning requires an explicit RNA design and durable audit")
        if rna_design is not None:
            from .rna_design_audit import validate_rna_design
            rna_design = validate_rna_design(rna_design)
            if not {"bulk_rna_input", "fastq_input"} & dataset_summary.get("observed", {}).keys():
                raise ValueError("An RNA design declaration requires a registered bulk or FASTQ input")
        context = scientific_context(task, dataset_summary, prior_state)
        metadata = validate_planning_metadata(evaluation_purpose, user_parameter_requirements)
        companion = None
        if scientific_contract is not None:
            if "rna_input_set" in context["dataset_summary"].get("observed", {}):
                raise ValueError("A singular assay companion cannot silently describe a multi-source input set")
            from .rna_scientific_contract import validate_scientific_contract
            companion = validate_scientific_contract(scientific_contract, methods=self.methods,
                artifact_ids=context["dataset_summary"].get("artifact_ids", []), task=context["task"],
                user_parameter_requirements=metadata["user_parameter_requirements"],
                rna_input_inspection=context["dataset_summary"].get("observed", {}).get("bulk_rna_input"))
            if "scrna_input" in context["dataset_summary"].get("observed", {}):
                from .rna_scrna_input_contract import match_scrna_scientific_contract
                match_scrna_scientific_contract(companion, context["dataset_summary"]["observed"]["scrna_input"])
        if rna_design is not None:
            from .rna_design_audit import match_scientific_design
            match_scientific_design(rna_design, companion)
        if data_classification not in {"synthetic", "public", "private_clinical", "private", "unclassified"}:
            raise ValueError("Declare a supported data classification")
        for identity in context["dataset_summary"].get("artifact_ids", []):
            self.methods.artifact_path(identity)
        limits = limits or AgentLimits()
        if not isinstance(limits, AgentLimits):
            raise TypeError("Use AgentLimits for model-selection budgets")
        record = {"session_id": uuid.uuid4().hex, "context": context, "data_classification": data_classification, **metadata,
                  "action_prerequisite_policy": deepcopy(ACTION_PREREQUISITE_POLICY),
                  "limits": asdict(limits), "scientific_candidates": None, "registry": {}, "sources": [],
                  "candidates": [], "experiments": [], "results": [], "recommendation": _empty_recommendation(),
                  "selection_history": [], "selection_in_progress": None, "approvals": {}, "analysis_proposal_id": None,
                  "created_by": _label(actor), "created_at": _now()}
        if rna_design is not None:
            record.update(rna_design=rna_design, rna_design_sha256=_digest(rna_design))
        if execution_binding is not None:
            record["rna_execution_inputs"] = execution_binding
        if companion is not None:
            record["scientific_contract"] = companion
            record["scientific_contract_sha256"] = _digest(companion)
        if anchors is not None:
            if not isinstance(anchors, dict) or set(anchors) != {"project_id", "study_id", "dataset_versions", "analysis_version"}:
                raise ValueError("Use exact project, study, dataset and analysis anchors")
            for key in ("project_id", "study_id"):
                if not isinstance(anchors[key], str) or re.fullmatch(r"[0-9a-f]{32}", anchors[key]) is None:
                    raise ValueError("Invalid scientific anchor identity")
            datasets = anchors["dataset_versions"]
            if not isinstance(datasets, list) or not 1 <= len(datasets) <= 32:
                raise ValueError("Use bounded exact dataset anchors")
            for kind, ref in [("dataset", item) for item in datasets] + [("analysis", anchors["analysis_version"])]:
                if (not isinstance(ref, dict) or set(ref) != {"object_type", "object_id", "version_id"}
                        or ref["object_type"] != kind or not isinstance(ref["object_id"], str)
                        or re.fullmatch(r"[0-9a-f]{32}", ref["object_id"]) is None
                        or not isinstance(ref["version_id"], str) or re.fullmatch(r"[0-9a-f]{64}", ref["version_id"]) is None):
                    raise ValueError("Use typed exact-version scientific anchors")
            record["scientific_anchors"] = deepcopy(anchors)
        with self.methods._db() as db:
            self.methods._put(db, "method_plan", record["session_id"], record)
            self.methods._put(db, "method_plan_snapshot", _digest(record), record)
            self.methods._audit(db, actor, "method_plan_created", record)
        return record

    def get(self, session_id):
        state = self.methods.get("method_plan", session_id)
        from .rna_design_audit import retained_rna_design
        retained_rna_design(state)
        if "scientific_contract" in state:
            from .rna_scientific_contract import retained_scientific_contract
            retained_scientific_contract(state, self.methods)
        return state

    def export_scientific_contract(self, session_id):
        """Separate optional companion export; the legacy six-field export stays fixed."""
        from .rna_scientific_contract import retained_scientific_contract
        state = self.get(session_id)
        companion = retained_scientific_contract(state, self.methods)
        if companion is None:
            return None
        return {"record_format": "devbench.rna-scientific-contract-export/1", "session_id": session_id,
                "scientific_contract": companion, "sha256": state["scientific_contract_sha256"]}

    def _governed_execution_inputs(self, state, *, inputs=None, method_version_id=None, parameters=None, subset_spec=None, workspace=None):
        """Revalidate exact /2 lineage; scientific preconditions keep original IDs."""
        binding = state.get("rna_execution_inputs")
        if binding is None:
            return None
        if workspace is not None:
            workspace = _path(workspace)
            if not self.methods.root.is_relative_to(workspace):
                raise ValueError("Artifact service lies outside the current caller workspace")
            if binding.get("workspace") is not None and binding["workspace"] != str(workspace):
                raise ValueError("Retained execution bridge workspace differs from the current caller workspace")
        if "fastq_input" in state["context"]["dataset_summary"].get("observed", {}):
            from .fastq_planner_binding import require_fastq_execution
            return require_fastq_execution(state, self.methods, inputs=inputs, method_version_id=method_version_id,
                parameters=parameters, subset_spec=subset_spec)
        from .rna_execution_inputs import load_execution_inputs, validate_execution_method_binding
        record = load_execution_inputs(binding["record_ref"], artifact_service=self.methods, dataset_binding=binding["dataset_binding"],
            workspace=workspace if workspace is not None else binding.get("workspace"))
        original_ids = {ref["artifact_id"] for ref in record["original_refs"].values()}
        if original_ids != set(state["context"]["dataset_summary"].get("artifact_ids", [])):
            raise ValueError("Governed execution bridge differs from the original scientific dataset")
        if inputs is not None:
            if subset_spec.get("kind") != "full_dataset":
                raise ValueError("Governed execution bridge requires the exact full-dataset snapshot")
            expected = {port: ref["artifact_id"] for port, ref in record["execution_refs"].items()}
            if inputs != expected:
                raise ValueError("Trial execution ports or artifacts differ from the sealed bridge")
            validate_execution_method_binding(record, method_version_id, parameters, artifact_service=self.methods)
        return record

    @staticmethod
    def _original_rna_binding(state):
        fastq = state["context"]["dataset_summary"].get("observed", {}).get("fastq_input")
        if fastq is not None:
            return {"format": "devbench.fastq-input-binding/1",
                "manifest_artifact_id": fastq["input_artifact_refs"]["manifest"]["artifact_id"]}
        inspection = state["context"]["dataset_summary"].get("observed", {}).get("bulk_rna_input")
        if inspection is None:
            raise ValueError("Late execution inputs require the original registered bulk RNA binding")
        refs = inspection["input_artifact_refs"]
        declarations = {key: deepcopy(inspection[key]) for key in
                        ("format", "representation", "axes", "provenance", "biological_independence")}
        if "metadata_roles" in inspection:
            declarations["metadata_roles"] = deepcopy(inspection["metadata_roles"])
        return {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
                "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": declarations}

    def prepare_execution_inputs(self, session_id, method_ports, *, output, allow_data_execution=False):
        """Serialize a selected method's inputs; never fit or mutate the plan."""
        if allow_data_execution is not True:
            raise PermissionError("Input preparation requires explicit data-execution authorization")
        if type(method_ports) is not dict:
            raise ValueError("Input preparation requires explicit method-port declarations")
        output = _path(output)
        if not output.is_relative_to(self.methods.root.parent):
            raise ValueError("Prepared inputs must stay inside the planner workspace")
        state = self.get(session_id)
        if state.get("rna_design") is None:
            raise ValueError("Input preparation requires an explicit audited rna_design")
        from .rna_design_audit import require_rna_design_audit, require_rna_design_execution
        require_rna_design_audit(state, self.methods)
        if (state.get("rna_execution_inputs") is not None or state.get("trial_designs") or state["experiments"]
                or state["results"] or state["selection_in_progress"] or state["recommendation"]["choice"] is not None):
            raise ValueError("Execution inputs or trial design are already frozen")
        if not any(row.get("method_version_id") == method_ports.get("method_version_id")
                   and row.get("method_version_id") is not None and row.get("applicability") == "eligible"
                   for row in state.get("candidates", [])):
            raise ValueError("Resolve an eligible method before preparing its execution inputs")
        declaration = state["rna_design"]
        if (_digest(method_ports.get("objective")) != _digest(declaration["objective"])
                or _digest(method_ports.get("design")) != _digest(declaration["design"])):
            raise ValueError("Execution ports must preserve the exact audited objective and design")
        if "fastq_input" in state["context"]["dataset_summary"].get("observed", {}):
            from .fastq_planner_binding import prepare_fastq_execution
            return prepare_fastq_execution(state, self.methods, method_ports, output)
        from .rna_execution_inputs import prepare_execution_inputs
        result = prepare_execution_inputs(self._original_rna_binding(state), method_ports,
            artifact_service=self.methods, output=output)
        prospective = {**state, "rna_execution_inputs": {"record_ref": result,
            "dataset_binding": self._original_rna_binding(state)}}
        require_rna_design_execution(prospective, self.methods)
        if _digest(self.get(session_id)) != _digest(state):
            raise ValueError("The plan changed during input preparation; retained transport was not attached")
        return result

    def _late_execution_binding(self, state, reference, workspace):
        """Validate prospective transport without publishing a partial plan."""
        if reference is None and workspace is None:
            return None
        if reference is None or workspace is None:
            raise ValueError("Late execution inputs require both a sealed reference and an explicit absolute workspace")
        if (state.get("rna_execution_inputs") is not None or state.get("trial_designs")
                or state["experiments"] or state["results"]):
            raise ValueError("Execution inputs are already bound or trial design is frozen")
        if state.get("rna_design") is None:
            raise ValueError("Late execution inputs require an explicit audited rna_design")
        from .rna_design_audit import require_rna_design_audit
        require_rna_design_audit(state, self.methods)
        workspace = _path(workspace)
        binding = {"record_ref": deepcopy(reference), "workspace": str(workspace),
                   "dataset_binding": self._original_rna_binding(state)}
        prospective = {**state, "rna_execution_inputs": binding}
        self._governed_execution_inputs(prospective, workspace=workspace)
        return binding

    @plan_operation
    def attach_model_campaign(self, session_id, ledger, campaign_id, backend, *, expected_plan_hash, actor):
        """Bind an admitted campaign before its first parent model invocation."""
        state = self.get(session_id)
        if _digest(state) != expected_plan_hash:
            raise ValueError("Campaign attachment requires the exact current plan hash")
        if "model_campaign" not in state and (state["selection_history"] or state["selection_in_progress"]):
            raise ValueError("Attach the model campaign before the first parent selection; history cannot be reset")
        from .workflow_operations import WorkflowOperationLedger, _limits
        from .rna_investigation_backend import AuditedInvestigationBackend
        from .rna_mediated_backend import MediatedInvestigationBackend
        from .codex_signedin import SignedInCodexInvestigationBackend
        if not isinstance(ledger, WorkflowOperationLedger) or (not isinstance(backend, (AuditedInvestigationBackend, MediatedInvestigationBackend))
                and type(backend) is not SignedInCodexInvestigationBackend):
            raise TypeError("Use the existing ledger and a trusted protected investigation backend")
        if ledger.readonly or backend.methods.root != self.methods.root:
            raise ValueError("Campaign runtime requires the bound writable ledger and protected method store")
        campaign = ledger.get_campaign(campaign_id)
        if campaign["policy"]["kind"] == "generation_jobs" and type(backend) is not SignedInCodexInvestigationBackend:
            raise TypeError("Generation jobs require the exact registered signed-in backend")
        capability = backend.require_admission(campaign["policy"]["kind"])
        identity = campaign["identity"]
        if (campaign["session_id"] != session_id or campaign["status"] != "open"
                or campaign["capability"] != capability
                or identity.get("source_digest") != capability["source_digest"]
                or identity.get("runtime_digest") != capability["runtime_digest"]):
            raise ValueError("Campaign session, source, runtime or backend capability binding differs")
        allocations = identity.get("parent_call_limits")
        if type(allocations) is not dict or not allocations or len(allocations) > 16:
            raise ValueError("Campaign requires retained bounded parent call allocations")
        for limits in allocations.values():
            _limits(limits)
        backend.bind_ledger(ledger)
        binding = {"format": "devbench.planner-model-campaign/1", "campaign_id": campaign_id,
            "ledger_path": str(ledger.path), "policy_digest": campaign["policy_digest"],
            "capability_digest": campaign["capability_digest"], "identity_digest": campaign["identity_digest"],
            "backend_digest": capability["backend_digest"], "source_digest": capability["source_digest"],
            "runtime_digest": capability["runtime_digest"]}
        if "model_campaign" in state:
            if state["model_campaign"] != binding:
                raise ValueError("A planner campaign cannot be replaced or regain authority from new labels")
            updated = state
        else:
            if identity.get("expected_plan_hash") != expected_plan_hash:
                raise ValueError("Campaign must retain its original exact parent plan hash")
            updated = deepcopy(state)
            updated["model_campaign"] = binding
            self._save(state, updated, actor=actor, action="planner_model_campaign_attached")
        self._model_campaigns[session_id] = (ledger, backend)
        return updated

    def _campaign_runtime(self, state):
        retained = state.get("model_campaign")
        runtime = self._model_campaigns.get(state["session_id"])
        if retained is None or runtime is None:
            raise ValueError("A current protected campaign backend is required; stored references grant no authority")
        ledger, backend = runtime
        campaign = ledger.get_campaign(retained["campaign_id"])
        capability = backend.require_admission(campaign["policy"]["kind"])
        if (ledger.readonly or str(ledger.path) != retained["ledger_path"]
                or campaign["session_id"] != state["session_id"] or campaign["status"] != "open"
                or any(campaign[key] != retained[key] for key in ("policy_digest", "capability_digest", "identity_digest"))
                or capability != campaign["capability"]
                or any(capability[key] != retained[key] for key in ("backend_digest", "source_digest", "runtime_digest"))):
            raise ValueError("Planner campaign, input runtime or backend identity changed")
        return ledger, backend, campaign

    def _investigation_child_verifier(self, state):
        _, backend, _ = self._campaign_runtime(state)
        def verify(packet_ref, result_ref, *, dispatch_ref=None):
            if result_ref is not None:
                return backend.verify_child_result(packet_ref, result_ref)
            return backend.verify_incomplete_child(packet_ref, dispatch_ref)
        return verify

    def _investigation_projection(self, state):
        from .rna_investigations import investigation_evidence_projection
        return investigation_evidence_projection(self.methods, state,
            verify_child=self._investigation_child_verifier(state))

    @plan_operation
    def attach_investigation_evidence(self, session_id, bundle_ref, *, expected_plan_hash, actor):
        """Retain verified links only; candidates, results and approvals stay governed."""
        from .rna_investigations import verify_investigation_bundle
        state = self.get(session_id)
        _, _, campaign = self._campaign_runtime(state)
        existing = state.get("investigation_evidence", [])
        if existing:
            if (len(existing) != 1 or existing[0]["bundle_ref"] != bundle_ref
                    or expected_plan_hash not in {_digest(state), existing[0]["attachment_before_hash"]}):
                raise ValueError("Conflicting investigation evidence or stale expected plan hash")
            self._investigation_projection(state)
            return state
        if _digest(state) != expected_plan_hash:
            raise ValueError("Investigation attachment requires the exact current plan hash")
        if state["selection_in_progress"] or state["approvals"] or state.get("full_execution"):
            raise ValueError("Investigation evidence cannot alter a running or approved planning cycle")
        for identity in state["context"]["dataset_summary"].get("artifact_ids", []):
            self.methods.artifact_path(identity)
        self._verified_results(state)
        bundle = verify_investigation_bundle(self.methods, bundle_ref, plan=state,
            verify_child=self._investigation_child_verifier(state))
        if bundle["policy_digest"] != campaign["identity"].get("investigation_policy_digest"):
            raise ValueError("Investigation route policy differs from the retained campaign identity")
        if bundle["stage"] == "pre_fit_methods":
            if state["scientific_candidates"] is None or state["registry"] or state["candidates"] or state["experiments"]:
                raise ValueError("Pre-fit evidence belongs after nomination and before capability or trial freeze")
        elif not state["results"] or state.get("preliminary_explanation") is None:
            raise ValueError("Post-fit evidence requires verified outputs and a retained preliminary explanation")
        updated = deepcopy(state)
        updated["investigation_evidence"] = [{"bundle_ref": deepcopy(bundle_ref), "stage": bundle["stage"],
            "scope_binding": bundle["binding_digest"], "attachment_before_hash": bundle["plan_hash"]}]
        self._save(state, updated, actor=actor, action="investigation_evidence_attached")
        return updated

    @staticmethod
    def _check_action_binding(candidate, contract):
        if candidate.get("action_scope") != contract.get("action_scope"):
            raise ValueError("Scientific candidate and method action scope must match exactly")
        implemented = contract.get("validation", {}).get("diagnostic_policy", {}).get("profile")
        if candidate.get("diagnostic_profile") != implemented:
            raise ValueError("Scientific candidate and implemented diagnostic profile must match exactly")

    def _observation_inspection(self, summary, artifact_ids, requested, *, actor, contract=None, require_roles=False):
        """Retain shared bounded evidence and return a detached observed overlay."""
        inspected = deepcopy(summary)
        identities = sorted(set(artifact_ids))
        if "fastq_input" in summary.get("observed", {}):
            from .fastq_planner_binding import load_fastq_manifest
            fastq = summary["observed"]["fastq_input"]
            load_fastq_manifest(self.methods, fastq)
            if set(identities) != {ref["artifact_id"] for ref in fastq["input_artifact_refs"].values()}:
                raise ValueError("FASTQ prerequisite inspection requires the original manifest")
            record = {"origin": "devbench.fastq-input/1", "inspection_kind": "fastq_input",
                "input_artifact_ids": identities, "observed": {}, "requested_fields": sorted(requested),
                "failures": [], "retention": fastq["retention"]}
            identity = _digest(record)
            with self.methods._db() as db:
                if db.execute("SELECT 1 FROM records WHERE kind='method_prerequisite_inspection' AND id=?", (identity,)).fetchone() is None:
                    self.methods._put(db, "method_prerequisite_inspection", identity, record)
                self.methods._audit(db, actor, "fastq_prerequisites_inspected", {"inspection_id": identity})
            return inspected, identity, record
        if "rna_input_set" in summary.get("observed", {}):
            from .rna_input_set_contract import verify_input_set_inspection, header_only_facts
            actual = verify_input_set_inspection(self.methods, summary["observed"]["rna_input_set"])
            if set(identities) != {ref["artifact_id"] for ref in actual["input_artifact_refs"].values()}:
                raise ValueError("RNA input-set inspection must bind every selected original artifact exactly")
            facts = header_only_facts()
            inspected["observed"].update(facts)
            record = {"origin": "devbench.rna-input-set-inspection/1", "inspection_kind": "rna_input_set",
                "input_artifact_ids": identities, "input_set_sha256": actual["input_set_sha256"],
                "observed": facts, "requested_fields": sorted(requested), "failures": [],
                "expression_values_read": False, "observation_ids_read": False,
                "interpretation": "Original source/header integrity only; no joint matrix or cross-file scientific compatibility is established."}
            identity = _digest(record)
            with self.methods._db() as db:
                if db.execute("SELECT 1 FROM records WHERE kind='method_prerequisite_inspection' AND id=?", (identity,)).fetchone() is None:
                    self.methods._put(db, "method_prerequisite_inspection", identity, record)
                self.methods._audit(db, actor, "rna_input_set_prerequisites_inspected", {"inspection_id": identity})
            return inspected, identity, record
        if "scrna_input" in summary.get("observed", {}):
            from .rna_scrna_input_contract import verify_scrna_inspection, header_only_facts
            actual = verify_scrna_inspection(self.methods, summary["observed"]["scrna_input"])
            if set(identities) != {ref["artifact_id"] for ref in actual["input_artifact_refs"].values()}:
                raise ValueError("AnnData RNA binding must match the exact selected dataset artifacts")
            # Header admission cannot establish numeric/identity/design facts or
            # turn caller declarations into independently observed prerequisites.
            facts = header_only_facts()
            inspected["observed"].update(facts)
            record = {"origin": "devbench.scrna-inspection/1", "inspection_kind": "scrna_input",
                "input_artifact_ids": identities, "observed": facts,
                "requested_fields": sorted(requested), "failures": [],
                "expression_values_read": False, "observation_ids_read": False,
                "interpretation": "Registered AnnData byte/header integrity only; scientific prerequisites remain unassessed."}
            identity = _digest(record)
            with self.methods._db() as db:
                if db.execute("SELECT 1 FROM records WHERE kind='method_prerequisite_inspection' AND id=?", (identity,)).fetchone() is None:
                    self.methods._put(db, "method_prerequisite_inspection", identity, record)
                self.methods._audit(db, actor, "scrna_header_prerequisites_inspected", {"inspection_id": identity})
            return inspected, identity, record
        if "bulk_rna_input" in summary.get("observed", {}):
            # Bulk matrices have gene/sample axes, not observation/QC rows.
            # Recheck registered bytes; never reuse the observation adapter.
            bulk = summary["observed"]["bulk_rna_input"]
            refs = bulk["input_artifact_refs"]
            from .rna_input_contract import inspect_registered_bulk_rna_input
            declaration_source = bulk
            if "inspection_artifact_ref" in bulk:
                from .rna_input_contract import _read_json, MAX_MATRIX_BYTES
                declaration_source = _read_json(self.methods.artifact_path(bulk["inspection_artifact_ref"]["artifact_id"]), MAX_MATRIX_BYTES)
            binding = {"format": "devbench.bulk-rna-binding/1",
                "matrix_artifact_id": refs["matrix"]["artifact_id"],
                "metadata_artifact_id": refs["metadata"]["artifact_id"],
                "declarations": {key: declaration_source[key] for key in ("format", "representation", "axes", "provenance", "biological_independence")}}
            if "metadata_roles" in declaration_source:
                binding["declarations"]["metadata_roles"] = declaration_source["metadata_roles"]
            actual = inspect_registered_bulk_rna_input(self.methods, binding, actor=actor if "inspection_artifact_ref" in bulk else None)
            if actual != bulk or set(identities) != {ref["artifact_id"] for ref in refs.values()}:
                raise ValueError("Bulk RNA inspection or input binding changed")
            record = {"origin": "devbench.bulk-rna-inspection/1", "inspection_kind": "bulk_rna_input",
                      "input_artifact_ids": identities, "observed": {},
                      "bulk_rna_inspection_artifact_ref": actual.get("inspection_artifact_ref"),
                      "requested_fields": sorted(requested), "failures": []}
            identity = _digest(record)
            with self.methods._db() as db:
                if db.execute("SELECT 1 FROM records WHERE kind='method_prerequisite_inspection' AND id=?", (identity,)).fetchone() is None:
                    self.methods._put(db, "method_prerequisite_inspection", identity, record)
                self.methods._audit(db, actor, "bulk_rna_prerequisites_inspected", {"inspection_id": identity})
            return inspected, identity, record
        record = {"origin": "devharness.coordinator-prerequisites/1", "input_artifact_ids": identities,
                  "requested_fields": sorted(requested), "observed": {}, "failures": []}
        inspection_applicable = not require_roles
        try:
            if len(identities) != 1:
                raise ValueError("Bounded observation inspection requires one immutable dataset artifact")
            if contract is not None:
                source_policy = contract["validation"]
            else:
                # Before an implementation is bound, only explicit coordinator
                # declarations supply metadata roles; never infer from names.
                source_policy = summary["observed"].get("observation_input_policy", {})
            if not isinstance(source_policy, dict):
                raise ValueError("Coordinator observation input policy must be an object")
            input_policy = {key: deepcopy(source_policy[key]) for key in ("sample_key", "qc_metrics", "counts_layer") if key in source_policy}
            artifact = self.methods.get("artifact", identities[0])
            artifact_type = artifact["artifact_type"]
            record["declared_input_sha256"] = artifact["sha256"]
            if require_roles:
                if artifact_type not in {"json", "anndata"}:
                    raise ValueError("Unsupported artifact type for bounded observation inspection")
                if not input_policy or (artifact_type == "json" and not input_policy.get("qc_metrics")):
                    raise ValueError("Pre-science inspection requires explicitly declared observation measurement roles")
                if artifact_type == "anndata" and not input_policy.get("counts_layer"):
                    raise ValueError("Pre-science AnnData inspection requires a declared count representation")
                inspection_applicable = True
            if contract is not None and contract["inputs"].get("dataset", {}).get("artifact_type") != artifact_type:
                raise ValueError("Inspection artifact type must match the declared dataset contract")
            record.update(observation_prerequisite_inspection(self.methods.artifact_path(identities[0]), sorted(requested),
                policy=input_policy, artifact_type=artifact_type))
        except (OSError, ValueError, RecursionError) as error:
            record["failures"].append(f"{type(error).__name__}: {error}"[:1000])
        identity = _digest(record)
        with self.methods._db() as db:
            if db.execute("SELECT 1 FROM records WHERE kind='method_prerequisite_inspection' AND id=?", (identity,)).fetchone() is None:
                self.methods._put(db, "method_prerequisite_inspection", identity, record)
            self.methods._audit(db, actor, "method_prerequisites_inspected", {"inspection_id": identity})
        if not inspection_applicable:
            # Observation inspection is unavailable for this general context,
            # not a mandatory failed prerequisite for unrelated objectives.
            return inspected, identity, record
        facts = record["observed"]
        inspected["observed"].update(facts)
        inspected["unknowns"] = [row for row in inspected["unknowns"] if row["field"] not in facts]
        if record["failures"]:
            # A failed inspection cannot inherit a caller's successful assertion.
            for field in requested:
                inspected["observed"].pop(field, None)
            inspected["unknowns"].append({"field": "coordinator_numeric_inspection", "impact": "; ".join(record["failures"]),
                                          "blocking": True})
        return inspected, identity, record

    def _inspect_applicability(self, candidate, summary, artifact_ids, *, actor, contract=None):
        """Reinspect bound inputs; never backfill the supplied context."""
        if "action_scope" not in candidate:
            return _applicability(candidate, summary)
        numeric = set(ACTION_PREREQUISITE_POLICIES["retain"])
        computable = numeric | {"measurement_rank", "measurement_variance_positive"}
        requested = numeric | {row["field"] for row in candidate["assumptions"] if row["field"] in computable}
        requested |= {row["field"] for row in summary["unknowns"] if row["field"] in computable}
        if contract is None and candidate.get("method_version_id"):
            contract = self.methods.method(candidate["method_version_id"])["contract"]
        inspected, identity, record = self._observation_inspection(summary, artifact_ids, requested,
                                                                  actor=actor, contract=contract)
        facts = record["observed"]
        result = _applicability(candidate, inspected)
        result["prerequisite_inspections"] = [identity]
        for assumption in result["assumptions"]:
            if assumption["field"] in facts and not record["failures"]:
                assumption["evidence"] = "Retained coordinator prerequisite inspection: " + identity
        return result

    def _current_candidate(self, state, name, *, actor, summary=None, artifact_ids=None):
        candidate = self._candidate(state, name)
        if candidate.get("method_version_id"):
            self._check_action_binding(candidate, self.methods.method(candidate["method_version_id"])["contract"])
        return self._inspect_applicability(candidate, summary or state["context"]["dataset_summary"],
            artifact_ids if artifact_ids is not None else state["context"]["dataset_summary"].get("artifact_ids", []), actor=actor)

    def _rechecked_state(self, state, *, actor):
        checked = deepcopy(state)
        checked["candidates"] = [self._current_candidate(state, item["name"], actor=actor) for item in state["candidates"]]
        return checked

    def _save(self, original, updated, *, actor, action):
        canonical_json(updated)
        history, previous = updated.get("selection_history", []), original.get("selection_history", [])
        accepting = action in _SCIENTIFIC_PUBLICATIONS or bool(history and history[-1].get("status") == "accepted"
            and (not previous or previous[-1].get("attempt") != history[-1].get("attempt")
                 or previous[-1].get("status") != "accepted"))
        guard = nullcontext()
        if accepting and "model_campaign" in original:
            runtime = self._model_campaigns.get(original["session_id"])
            if runtime is None:
                raise ValueError("Scientific publication requires a current protected campaign runtime")
            guard = runtime[0].publication_guard(original["model_campaign"]["campaign_id"])
        # This short guard serializes cancellation with the scientific CAS. It
        # never surrounds selection, validation, numerical work or model calls.
        with guard:
            if accepting and "model_campaign" in original:
                self._campaign_runtime(original)
            with self.methods._db() as db:
                if self.methods._get(db, "method_plan", original["session_id"]) != original:
                    raise ValueError("Planner state changed; reload before retrying")
                self.methods._put(db, "method_plan", original["session_id"], updated, update=True)
                snapshot_id = _digest(updated)
                if db.execute("SELECT 1 FROM records WHERE kind='method_plan_snapshot' AND id=?", (snapshot_id,)).fetchone() is None:
                    self.methods._put(db, "method_plan_snapshot", snapshot_id, updated)
                self.methods._audit(db, actor, action, {"session_id": original["session_id"], "state_sha256": _digest(updated)})
        return updated

    def _select(self, state, selector, context, *, actor, allow_model_execution):
        from .rna_design_audit import require_rna_design_audit
        design_audit = require_rna_design_audit(state, self.methods)
        if design_audit is not None:
            context = {**context, "rna_design": deepcopy(state["rna_design"]), "rna_design_audit": design_audit}
        context = {**context, **validate_planning_metadata(state.get("evaluation_purpose", "method_selection"),
                                                          state.get("user_parameter_requirements", [])),
                   "parameter_requirement_assurance": "caller-declared; not authenticated human authorship",
                   "planning_metadata_recorded": "evaluation_purpose" in state and "user_parameter_requirements" in state}
        if "scientific_contract" in state:
            from .rna_scientific_contract import retained_scientific_contract, scientific_projection
            context["scientific_contract"] = scientific_projection(retained_scientific_contract(state, self.methods))
        context["action_prerequisite_policy"] = deepcopy(ACTION_PREREQUISITE_POLICY)
        from .rna_mediated_backend import MediatedActionSelector, MediatedInvestigationBackend
        from .codex_signedin import SignedInCodexActionSelector, SignedInCodexInvestigationBackend
        from .rna_scientific_trial import GroundedSelector
        if type(selector) is GroundedSelector and type(selector.selector) in {MediatedActionSelector, SignedInCodexActionSelector}:
            context = selector.prepare_context(context)
        if state.get("investigation_evidence"):
            context["investigation_evidence"] = self._investigation_projection(state)
        config = selector_configuration(selector)
        if config.get("backend") != "recorded_fixture" and (
                allow_model_execution is not True or state["data_classification"] not in {"synthetic", "public"}):
            raise PermissionError("Live planning requires explicit model authorization and public or synthetic context")
        if state["selection_in_progress"]:
            raise ValueError("Another planner selection is in progress")
        limits = state["limits"]
        # An orphaned attempt has no measured duration. Charge its whole
        # permitted window, so restarting a coordinator cannot reset spend.
        spent = sum(item.get("elapsed_seconds", limits["session_timeout_seconds"])
                    for item in state["selection_history"])
        if len(state["selection_history"]) >= limits["max_selections"] or spent >= limits["timeout_seconds"]:
            raise ValueError("Planner selection budget exhausted")
        if len(canonical_json(context)) > limits["max_context_bytes"]:
            raise ValueError("Planner context exceeds its declared budget")
        attempt = uuid.uuid4().hex
        timeout = min(limits["session_timeout_seconds"], limits["timeout_seconds"] - spent)
        reservation = bindings = ledger = backend = None
        if "model_campaign" in state:
            from .workflow_operations import _limits
            from .episode_codex import _METHOD_PLANNER_CONTRACTS
            if context["decision_kind"] not in {*_METHOD_PLANNER_CONTRACTS, "scientific_review"}:
                raise ValueError("A campaign parent call requires a registered parent selection purpose")
            ledger, backend, campaign = self._campaign_runtime(state)
            bindings = backend.parent_selection_binding(selector, context,
                parent_session_id=state["session_id"], session_id=attempt, invocation_id=attempt)
            if (bindings["context_digest"] != _digest(context)
                    or bindings["backend_digest"] != campaign["capability"]["backend_digest"]
                    or bindings["runtime_digest"] != campaign["capability"]["runtime_digest"]):
                raise ValueError("Protected parent selection context or backend binding differs")
            allocation = _limits(backend.parent_call_limits(context, campaign,
                {**limits, "session_timeout_seconds": timeout}))
            if (len(canonical_json(context)) > allocation["context_bytes"]
                    or allocation["context_bytes"] > limits["max_context_bytes"]
                    or allocation.get("memory_mb", limits["memory_mb"]) > limits["memory_mb"] or allocation["seconds"] > timeout):
                raise ValueError("Parent call allocation exceeds its existing AgentLimits")
            timeout = allocation["seconds"]
            purpose = ("parent_reconciliation" if context["decision_kind"] in
                {"method_recommendation", "method_candidates_reassessment"} else context["decision_kind"])
            bindings = {"format": "devbench.model-call-request/1", **bindings,
                "parent_session_id": state["session_id"], "session_id": attempt, "invocation_id": attempt,
                "plan_hash": _digest(state), "purpose": context["decision_kind"]}
            reservation = ledger.reserve_model_call(campaign["campaign_id"],
                "parent:" + context["decision_kind"], len(state["selection_history"]),
                bindings,
                limits=allocation, purpose=purpose)
        directory = self.methods.root / "planner-selections" / state["session_id"] / attempt
        reserved = deepcopy(state)
        reserved["selection_in_progress"] = attempt
        reserved["selection_history"].append({"attempt": attempt, "phase": context["decision_kind"],
            "context_sha256": _digest(context), "selector": config, "status": "running", "output": str(directory)})
        if reservation is not None:
            reserved["selection_history"][-1].update(model_reservation_ref=reservation.ref,
                model_call_allocation=reservation.limits)
        try:
            directory.mkdir(mode=0o700, parents=True)
            self._save(state, reserved, actor=actor, action="planner_selection_started")
        except BaseException as error:
            if reservation is not None:
                ledger.mark_model_call_uncertain(reservation, error_type=type(error).__name__)
            raise
        started = time.monotonic()
        completed = deepcopy(reserved)
        capture_settled = False
        trace_reference = None
        def observe_mediated_settlement(capture_ref=None):
            if type(backend) is not MediatedInvestigationBackend:
                return
            from .rna_mediated_observability import settle_mediated_observation
            settle_mediated_observation(backend, reservation, trace_reference, capture_ref=capture_ref)
            if getattr(backend, "trace_observer", None) is not None:
                backend.trace_observer.complete_invocation(trace_reference, reservation=reservation,
                    status=ledger.get_model_reservation(reservation)["status"],
                    capture_verified=capture_ref is not None, db_timeout=0)
        def settle_capture():
            nonlocal capture_settled
            capture_ref, receipt = backend.verify_selection_capture(directory,
                bindings=bindings, reservation=reservation)
            job_capture = receipt.get("format") == "devbench.codex-job-capture/1"
            if type(backend) is SignedInCodexInvestigationBackend:
                from .codex_job_trace import complete_job_trace
                complete_job_trace(backend.trace_observer, trace_reference, reservation=reservation,
                    capture=receipt, status=receipt["status"])
            elif type(backend) is not MediatedInvestigationBackend and getattr(backend, "trace_observer", None) is not None:
                backend.trace_observer.complete_invocation(trace_reference,reservation=reservation,
                    status=receipt["status"],capture_verified=True)
            completed["selection_history"][-1].update(model_capture_ref=capture_ref,
                native_session_ids=receipt["native_session_ids"])
            if "usage" in receipt:
                completed["selection_history"][-1]["model_usage"] = deepcopy(receipt["usage"])
            ledger.settle_model_call(reservation, capture=capture_ref, verify_capture=backend.verify_capture)
            capture_settled = True
            observe_mediated_settlement(capture_ref)
            from .rna_investigations import retain_investigation_json
            usage_ref = retain_investigation_json(self.methods,
                {"format": "devbench.rna-generation-job-receipt/1" if job_capture else "devbench.rna-investigation-usage/1", "capture_ref": capture_ref,
                 "receipt": backend.verify_capture(capture_ref, reservation)}, actor=actor,
                artifact_type="json" if job_capture else "rna_investigation_usage", max_bytes=24_000)
            completed["selection_history"][-1]["model_job_receipt_ref" if job_capture else "model_usage_ref"] = usage_ref
            if not job_capture and receipt["usage"]["normalized_total_tokens"] is None:
                raise ValueError("Unresolved parent model usage blocks further calls")
            return receipt
        try:
            if reservation is not None:
                ledger.activate_model_call(reservation)
                timeout = min(timeout, ledger.model_call_remaining(reservation))
                if timeout <= 0:
                    raise ValueError("Campaign deadline ended before parent dispatch")
            transport_limits = {}
            if reservation is not None:
                from .episode_codex import CodexActionSelector
                underlying = selector.selector if type(selector) is GroundedSelector else selector
                if isinstance(selector, CodexActionSelector) or type(underlying) in {MediatedActionSelector, SignedInCodexActionSelector}:
                    transport_limits["max_context_bytes"] = reservation.limits["context_bytes"]
                    transport_limits["protected_model_gate"] = backend.model_dispatch_gate(reservation)
                    if getattr(backend, "trace_observer", None) is not None:
                        workflow_id = (campaign["identity"]["trial_id"]
                            if type(backend) in {MediatedInvestigationBackend, SignedInCodexInvestigationBackend}
                            else state["session_id"])
                        if type(backend) is SignedInCodexInvestigationBackend:
                            from .codex_job_trace import prepare_job_trace
                            reference = prepare_job_trace(backend.trace_observer, workflow_id, bindings,
                                reservation, role="lead")
                        else:
                            reference = backend.trace_observer.prepare_invocation(workflow_id, bindings,
                                reservation, role="review" if context["decision_kind"] == "scientific_review" else "lead")
                        if reference is not None:
                            trace_reference = reference
                            transport_limits["trace_sidecar"] = reference
            answer = selector.select(deepcopy(context), directory,
                timeout_seconds=timeout,
                memory_mb=reservation.limits.get("memory_mb") if reservation is not None else limits["memory_mb"],
                allow_model_execution=allow_model_execution, **transport_limits)
            if not isinstance(answer, Selection):
                raise ValueError("Planner selectors must return a Selection with actual evidence")
            canonical_json({"action": answer.action, "evidence": answer.evidence})
            completed["selection_history"][-1].update(status="returned_unvalidated", response=answer.action, evidence=answer.evidence)
            if reservation is not None:
                receipt = settle_capture()
                job_capture = receipt.get("format") == "devbench.codex-job-capture/1"
                response_digest = receipt.get("response_sha256") if job_capture else receipt.get("response_digest")
                if response_digest != _digest(answer.action) or receipt["status"] != "completed":
                    raise ValueError("Parent selection response differs from actual protected capture")
                # A late completion remains charged and inspectable, but cannot
                # authorize a new scientific decision after campaign invalidation.
                from .workflow_operations import OperationBlocked
                current = ledger.get_campaign(reservation.campaign_id)
                if current["status"] != "open":
                    raise OperationBlocked(current.get("reason") or current["status"])
                if current["qualification"] == "violated":
                    raise OperationBlocked("budget_policy_violation")
                if current["unresolved_count"]:
                    raise OperationBlocked("unresolved_usage")
                if time.time() >= current["deadline_epoch"]:
                    raise OperationBlocked("campaign_deadline")
        except BaseException as error:
            completed["selection_history"][-1].update(status="failed", error=f"{type(error).__name__}: {error}"[:2000])
            if reservation is not None and not capture_settled:
                try:
                    settle_capture()
                except BaseException as capture_error:
                    completed["selection_history"][-1]["capture_error"] = f"{type(capture_error).__name__}: {capture_error}"[:1000]
                    if ledger.get_model_reservation(reservation)["status"] in {"reserved", "active"}:
                        ledger.mark_model_call_uncertain(reservation, error_type=type(capture_error).__name__)
                    if type(backend) is SignedInCodexInvestigationBackend:
                        from .codex_job_trace import complete_job_trace
                        complete_job_trace(backend.trace_observer, trace_reference, reservation=reservation,
                            capture=None, status="uncertain")
                    observe_mediated_settlement()
            raise
        finally:
            completed["selection_in_progress"] = None
            completed["selection_history"][-1]["elapsed_seconds"] = time.monotonic() - started
            self._save(reserved, completed, actor=actor, action="planner_selection_returned")
        return answer.action, completed

    @plan_operation
    def retrieve_evidence(self, session_id, url, *, allowed_hosts, actor, allow_network=False):
        from .method_sources import retrieve_source
        state = self.get(session_id)
        if state["experiments"] or state["selection_in_progress"]:
            raise ValueError("Source evidence is frozen during selection and after trial declaration")
        if state["scientific_candidates"] is not None:
            raise ValueError("Source evidence is frozen with scientific candidates; late evidence requires a new planning cycle")
        if allow_network is not True:
            raise PermissionError("Source retrieval requires explicit network authorization")
        if len(state["sources"]) >= 3:
            raise ValueError("Source retrieval budget exhausted; start a separately scoped plan")
        target = self.methods.root / "planner-sources" / (uuid.uuid4().hex + ".txt")
        _path(target.parent).mkdir(mode=0o700, exist_ok=True)
        evidence = retrieve_source(url, target, allowed_hosts=allowed_hosts, allow_network=allow_network)
        artifact = self.methods.import_artifact(target, artifact_type="file", actor=actor)
        updated = deepcopy(state)
        updated["sources"].append({**evidence, "excerpt": evidence["excerpt"][:8000],
                                   "artifact_id": artifact["artifact_id"]})
        self._save(state, updated, actor=actor, action="planner_source_retrieved")
        return updated["sources"][-1]

    @plan_operation
    def generate_candidates(self, session_id, selector, *, actor, allow_model_execution=False, discovery_context=None):
        state = self.get(session_id)
        if state["scientific_candidates"] is not None:
            raise ValueError("Scientific candidates are frozen; start a new plan to revise them")
        # No registry read occurs in this method. Live selectors use fresh,
        # tool-less actors with the method workspace in protected private roots.
        context = {"decision_kind": "method_scientific_planning", **deepcopy(state["context"]), "sources": state["sources"]}
        if discovery_context is not None:
            from .workflow_discovery import load_discovery
            discovered = load_discovery(self, discovery_context, session_id=session_id)
            if discovered["status"] not in {"completed", "descriptive"}:
                raise ValueError("Unresolved discovery evidence cannot authorize scientific generation")
            if discovered["after_planner_hash"] != _digest(state):
                raise ValueError("Discovery evidence does not bind the current scientific planning baseline")
            context["discovery_evidence"] = {"record_ref": deepcopy(discovery_context["record_ref"]),
                "query": discovered["request"]["query"], "provider": deepcopy(discovered["provider"]),
                "sources": [deepcopy(source) for source in discovered["sources"]
                            if source["kind"] != "author_repository"], "status": discovered["status"],
                "search_performed": discovered["request"].get("mode") != "supplied_only",
                "scope": discovered["scope"],
                "limitations": ["Provider observations are distinct from inspected full text and verified scientific support.",
                                "No implementation registry or availability information is supplied."]}
            from .method_planner_contract import _reject_capabilities
            _reject_capabilities(context["discovery_evidence"])
        summary = state["context"]["dataset_summary"]
        inspected, identity, record = self._observation_inspection(summary, summary.get("artifact_ids", []),
            set(ACTION_PREREQUISITE_POLICIES["retain"]), actor=actor, require_roles=True)
        context["dataset_summary"] = inspected
        context["observation_fact_catalog"] = deepcopy(OBSERVATION_FACT_CATALOG)
        context["prerequisite_inspection"] = {"inspection_id": identity, **deepcopy(record)}
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        candidates = validate_scientific_candidates(answer)
        validate_parameter_origins(candidates, state.get("user_parameter_requirements", []))
        updated = deepcopy(state)
        updated["scientific_candidates"] = candidates
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="scientific_candidates_frozen")
        return candidates

    @plan_operation
    def reassess_candidates(self, session_id, selector, *, actor, allow_model_execution=False):
        """One charged scientific reconciliation before capability resolution."""
        state = self.get(session_id)
        links = state.get("investigation_evidence", [])
        if (state["scientific_candidates"] is None or len(links) != 1
                or links[0]["stage"] != "pre_fit_methods"):
            raise ValueError("Candidate reassessment requires verified pre-fit investigation evidence")
        if (state["registry"] or state["candidates"] or state["experiments"] or state["results"]
                or state["approvals"] or state.get("candidate_reassessment")):
            raise ValueError("Candidate reconciliation is frozen after capability, trial or prior reassessment")
        summary = state["context"]["dataset_summary"]
        inspected, identity, record = self._observation_inspection(summary, summary.get("artifact_ids", []),
            set(ACTION_PREREQUISITE_POLICIES["retain"]), actor=actor, require_roles=True)
        context = {"decision_kind": "method_candidates_reassessment", **deepcopy(state["context"]),
            "dataset_summary": inspected, "sources": state["sources"],
            "previous_scientific_candidates": deepcopy(state["scientific_candidates"]),
            "observation_fact_catalog": deepcopy(OBSERVATION_FACT_CATALOG),
            "prerequisite_inspection": {"inspection_id": identity, **deepcopy(record)}}
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        candidates = validate_scientific_candidates(answer)
        validate_parameter_origins(candidates, state.get("user_parameter_requirements", []))
        updated = deepcopy(state)
        updated["candidate_reassessment"] = {"previous_scientific_candidates": deepcopy(state["scientific_candidates"]),
            "bundle_ref": deepcopy(links[0]["bundle_ref"]), "selection_attempt": state["selection_history"][-1]["attempt"],
            "scope": "scientific_alternatives_before_capability_and_results"}
        updated["scientific_candidates"] = candidates
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="scientific_candidates_reconciled")
        return candidates

    @plan_operation
    def resolve_capabilities(self, session_id, selector, version_ids, *, actor, allow_model_execution=False,
                             discovery_context=None):
        state = self.get(session_id)
        if state["scientific_candidates"] is None:
            raise ValueError("Scientific generation must precede capability resolution")
        if state["experiments"]:
            raise ValueError("Method bindings are frozen after trial declaration")
        if (any(link["stage"] == "pre_fit_methods" for link in state.get("investigation_evidence", []))
                and not state.get("candidate_reassessment")):
            raise ValueError("Reconcile the verified pre-fit scientific evidence before capability resolution")
        if not isinstance(version_ids, list) or len(version_ids) > 64 or len(set(version_ids)) != len(version_ids):
            raise ValueError("Use a bounded unique method-version inventory")
        registry = {identity: self.methods.method(identity)["contract"] for identity in version_ids}
        context = {"decision_kind": "method_capability_resolution", **state["context"],
                   "candidates": state["scientific_candidates"], "registry": registry, "sources": state["sources"]}
        if discovery_context is not None:
            from .workflow_discovery import load_discovery
            discovered = load_discovery(self, discovery_context, session_id=session_id)
            if discovered["status"] not in {"completed", "descriptive"}:
                raise ValueError("Unresolved discovery cannot become verified implementation availability")
            context["implementation_discovery"] = {"record_ref": deepcopy(discovery_context["record_ref"]),
                "sources": [deepcopy(source) for source in discovered["sources"]
                            if source["kind"] == "author_repository"],
                "scope": "Unverified external implementation leads; no repository inspection, correspondence or numerical validation is established."}
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        bindings = validate_resolution(answer)["bindings"]
        names = [row["name"] for row in state["scientific_candidates"]]
        if len(bindings) != len(names) or {item["candidate"] for item in bindings} != set(names):
            raise ValueError("Resolution must preserve every scientific candidate")
        by_name = {row["candidate"]: row for row in bindings}
        candidates = []
        for scientific in state["scientific_candidates"]:
            binding = by_name[scientific["name"]]
            identity = binding["method_version_id"]
            if identity is not None:
                if identity not in registry or _name(registry[identity]["name"]) != _name(scientific["name"]):
                    raise ValueError("Binding does not match the scientific candidate and supplied registry")
                custom = registry[identity]["lifecycle"] == "custom"
                availability, confidence = ("workspace_custom", "low") if custom else ("registered", "high")
                if scientific["source"]["type"] == "generated":
                    confidence = "low"
                self._check_action_binding(scientific, registry[identity])
            else:
                availability = "externally_available" if binding["discovery"] else (
                    "implementation_required" if scientific["source"]["type"] == "generated" else "unknown")
                confidence = "unknown"
            candidate = self._inspect_applicability(scientific, state["context"]["dataset_summary"],
                state["context"]["dataset_summary"].get("artifact_ids", []), actor=actor,
                contract=registry[identity] if identity is not None else None)
            candidate.update(method_version_id=identity, availability=availability, implementation_confidence=confidence,
                             discovery=binding["discovery"])
            candidates.append(candidate)
        updated = deepcopy(state)
        updated.update(registry=registry, candidates=candidates)
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="method_capabilities_resolved")
        return candidates

    @plan_operation
    def design_trials(self, session_id, selector, *, actor, allow_model_execution=False,
                      execution_inputs_ref=None, execution_workspace=None):
        """Ask for trial specifications; declaration and execution remain separate."""
        from .method_authoring_contract import validate_trial_design
        state = self.get(session_id)
        if state["results"]:
            raise ValueError("Trial criteria are frozen after observing results; use a new planning cycle")
        if not state["candidates"] or state["recommendation"]["choice"] is not None:
            raise ValueError("Resolve candidates before proposing trials, and freeze designs before recommendation")
        self._verified_results(state)
        attachment = self._late_execution_binding(state, execution_inputs_ref, execution_workspace)
        prospective = {**state, "rna_execution_inputs": attachment} if attachment is not None else state
        execution = self._governed_execution_inputs(prospective)
        available_ids = ([ref["artifact_id"] for ref in execution["execution_refs"].values()] if execution is not None
                         else state["context"]["dataset_summary"].get("artifact_ids", []))
        descriptors = {identity: self.methods.get("artifact", identity) for identity in available_ids}
        references = {identity: self._trial_input_reference(identity) for identity in descriptors}
        context = {"decision_kind": "method_trial_design", "plan": self.export(session_id),
                   "registry": state["registry"], "available_artifacts": descriptors,
                   "available_input_artifact_refs": references, "sources": state["sources"]}
        if prospective.get("rna_design", {}).get("objective", {}).get("intent") == "infer" and execution is not None:
            from .rna_design_audit import rna_design_execution_parameters
            context["frozen_execution_binding"] = rna_design_execution_parameters(prospective, self.methods)
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        prospective = {**state, "rna_execution_inputs": attachment} if attachment is not None else state
        proposals = validate_trial_design(answer)
        for proposal in proposals:
            self._bind_trial_inputs(proposal, allowed_ids=set(descriptors))
            for name in proposal["candidates"]:
                candidate = self._current_candidate(state, name, actor=actor)
                if candidate["applicability"] != "eligible" or candidate["method_version_id"] is None:
                    raise ValueError("A proposed trial requires eligible candidates with real implementations")
                ports = self.methods.method(candidate["method_version_id"])["contract"]["inputs"]
                if set(proposal["inputs"][name]) != set(ports):
                    raise ValueError("Trial input ports must match the exact method contract")
                self._governed_execution_inputs(prospective, inputs=proposal["inputs"][name], method_version_id=candidate["method_version_id"],
                    parameters=proposal["parameters"][name], subset_spec=proposal["subset_spec"])
            if any(row["id"] == proposal["id"] for row in state["experiments"]):
                raise ValueError("A proposed trial cannot overwrite a frozen experiment")
        updated = deepcopy(state)
        if attachment is not None:
            updated["rna_execution_inputs"] = attachment
        updated.setdefault("trial_designs", []).append({"proposals": proposals, "status": "unexecuted_drafts",
                                                         "selection_attempt": state["selection_history"][-1]["attempt"]})
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="scientific_trial_designs_proposed")
        return proposals

    @plan_operation
    def draft_implementation(self, session_id, candidate_name, selector, interface, environment_lock, *, actor,
                             justification, allow_model_execution=False):
        """Materialize model-returned source; never execute it in the coordinator."""
        from .method_authoring_contract import validate_implementation
        state = self.get(session_id)
        if state["experiments"] or state["recommendation"]["choice"] is not None:
            raise ValueError("Implementation bindings are frozen once a trial is declared")
        candidate = self._candidate(state, candidate_name)
        justification = _reason(justification)
        contract = deepcopy(interface)
        if contract.get("implementation", {}).get("kind") != "oci":
            raise ValueError("Generated implementations require the OCI execution boundary")
        if _name(contract.get("name", "")) != _name(candidate_name):
            raise ValueError("Generated interface must correspond to the selected scientific candidate")
        contract["lifecycle"], contract["implementation_trust"] = "custom", "unreviewed"
        contract["implementation"]["source_sha256"] = "0" * 64
        contract = MethodContract.from_dict(contract).to_dict()
        self._check_action_binding(candidate, contract)
        environment_lock = _path(environment_lock)
        if hash_artifact(environment_lock, max_bytes=1_000_000)["sha256"] != contract["environment"]["lock_sha256"]:
            raise ValueError("Environment lock differs from the authorized interface")
        context = {"decision_kind": "method_implementation_draft", **state["context"], "candidate": candidate,
                   "interface": contract, "sources": state["sources"], "justification": justification}
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        draft = validate_implementation(answer)
        supplied_references = {source[key] for source in state["sources"] for key in ("requested_url", "final_url")}
        if not set(draft["source_references"]) <= supplied_references:
            raise ValueError("Generated source cites evidence that was not retrieved and supplied")
        interpreter = Path(contract["implementation"]["entrypoint"][0]).name.lower()
        if interpreter.startswith("python"):
            # Syntax checking is parsing only; no imports, compilation execution,
            # tests or arbitrary generated code run in this host process.
            ast.parse(draft["source_code"])
        directory = _path(self.methods.root / "generated-methods" / uuid.uuid4().hex)
        directory.mkdir(mode=0o700, parents=True)
        source = directory / "main.py"
        source.write_text(draft["source_code"], encoding="utf-8")
        contract["implementation"]["source_sha256"] = hash_artifact(source, max_bytes=1_000_000)["sha256"]
        contract["provenance"] = {"origin": "generated", "source_plan_revision": _digest(state),
                                   "citations": draft["source_references"]}
        method = self.methods.register_method(contract, source=source, environment_lock=environment_lock, actor=actor)
        record = {"candidate": candidate_name, "method_version_id": method["version_id"],
                  "status": "generated_unexecuted", "justification": justification,
                  "algorithm_summary": draft["algorithm_summary"], "introduced_choices": draft["introduced_choices"],
                  "source_references": draft["source_references"], "source_artifact": method["source_artifact"],
                  "interface_sha256": _digest(interface), "implementation_confidence": "low"}
        updated = deepcopy(state)
        updated.setdefault("implementation_drafts", []).append(record)
        updated["registry"][method["version_id"]] = method["contract"]
        selected = self._candidate(updated, candidate_name)
        selected.update(method_version_id=method["version_id"], availability="workspace_custom", implementation_confidence="low")
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="experimental_implementation_generated")
        return record

    @staticmethod
    def _candidate(state, name):
        matches = [item for item in state["candidates"] if item["name"] == name]
        if len(matches) != 1:
            raise ValueError("Unknown scientific candidate")
        return matches[0]

    def _trial_input_reference(self, identity):
        try:
            self.methods.artifact_path(identity)
            artifact = self.methods.get("artifact", identity)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Trial input reference is not an exact registered artifact") from error
        return {"format": "devbench.trial-input/1", **{key: artifact[key] for key in
                ("artifact_id", "sha256", "size_bytes", "artifact_type")}}

    def _bind_trial_inputs(self, spec, *, allowed_ids=None):
        retain_references = "input_artifact_refs" in spec or any(type(value) is dict for ports in spec.get("inputs", {}).values() for value in ports.values())
        references = {}
        for name, ports in spec.get("inputs", {}).items():
            references[name] = {}
            for port, value in ports.items():
                identity = value.get("artifact_id") if type(value) is dict else value
                if allowed_ids is not None and identity not in allowed_ids:
                    raise ValueError("Trial input must use an exact artifact supplied in the prompt")
                reference = self._trial_input_reference(identity)
                if type(value) is dict and value != reference:
                    raise ValueError("Structured trial input differs from the immutable artifact")
                references[name][port] = reference
                ports[port] = identity
        if "input_artifact_refs" in spec and spec["input_artifact_refs"] != references:
            raise ValueError("Frozen trial input references changed")
        if retain_references:
            spec["input_artifact_refs"] = references

    def terminal_outcome(self, session_id, *, refusal=None):
        from .method_authoring_contract import trial_terminal_outcome
        state = self.get(session_id)
        self._verified_results(state)
        if refusal is not None:
            for identity in refusal.get("evidence_artifact_ids", []):
                self.methods.artifact_path(identity)
        return trial_terminal_outcome(state["experiments"], state["results"], refusal=refusal)

    @plan_operation
    def declare_trial(self, session_id, branch_id, spec, *, actor):
        state = self.get(session_id)
        if state["results"]:
            raise ValueError("Trial criteria are frozen after observing results; use a new planning cycle")
        spec = deepcopy(spec)
        self._bind_trial_inputs(spec)
        if any(item["id"] == spec.get("id") for item in state["experiments"]):
            raise ValueError("Trial declarations are immutable and the experiment already exists")
        if state["recommendation"]["choice"] is not None or state["selection_in_progress"] or any(
                item["status"] == "running" for item in state["experiments"]):
            raise ValueError("Trials are frozen during selection and after a recommendation")
        if len(state["experiments"]) >= 64:
            raise ValueError("Trial declaration budget exhausted")
        preview_plan = self.export(session_id)
        preview_plan["experiments"].append(spec)
        validate_plan(preview_plan)
        validity = spec["subset_validity"]
        if validity["preserves_preconditions"] is not True or not validity["rationale"].strip():
            raise ValueError("A scientifically valid subset preserving preconditions is required")
        validate_metrics(spec["metrics"])
        names = spec["candidates"]
        if len(set(names)) != len(names) or len(names) > 16:
            raise ValueError("Trials require unique bounded candidate names")
        if set(spec.get("inputs", {})) != set(names) or set(spec.get("parameters", {})) != set(names):
            raise ValueError("Each trial candidate requires explicit inputs and parameters")
        limits = spec["resource_limits"]
        if (set(limits) != {"memory_mb", "timeout_seconds", "max_runs"}
                or type(limits["memory_mb"]) is not int or not 1 <= limits["memory_mb"] <= 65536
                or type(limits["max_runs"]) is not int or not len(names) <= limits["max_runs"] <= 16
                or type(limits["timeout_seconds"]) not in (int, float) or not 0 < limits["timeout_seconds"] <= 3600):
            raise ValueError("Invalid trial resource budget")
        selectors = spec.get("metric_sources", {})
        if set(selectors) != {item["name"] for item in spec["metrics"]}:
            raise ValueError("Each predeclared metric requires a runner-owned source")
        for metric in spec["metrics"]:
            source = selectors[metric["name"]]
            if not isinstance(source, dict) or source.get("kind") not in {"runtime", "validation", "output_json"}:
                raise ValueError("Unsupported metric source")
            if set(source) != ({"kind", "field", "output"} if source["kind"] == "output_json" else {"kind", "field"}):
                raise ValueError("Metric sources accept selectors, not executable expressions")
            if not isinstance(source["field"], str) or len(source["field"]) > 300:
                raise ValueError("Invalid metric field")
            if source["kind"] == "runtime" and source["field"] not in {"elapsed_seconds", "output_bytes"}:
                raise ValueError("Unknown runtime metric")
            if source["kind"] != "runtime":
                if not source["field"].startswith("/"):
                    raise ValueError("Use a JSON pointer for metric extraction")
                for token in source["field"].split("/")[1:]:
                    decoded = token.replace("~1", "/").replace("~0", "~")
                    validate_metrics([{**metric, "name": decoded}])
        population = None
        observed = state["context"]["dataset_summary"]
        original_ids = set(observed.get("artifact_ids", []))
        commands, admitted, reserved_seconds = [], {}, 0
        for name in names:
            candidate = self._candidate(state, name)
            identity = candidate["method_version_id"]
            if identity is None:
                raise ValueError("Candidate implementation is unavailable; resolve a real method version first")
            contract = self.methods.method(identity)["contract"]
            self._check_action_binding(candidate, contract)
            parameters = MethodContract.from_dict(contract).validate_parameters(spec["parameters"][name])
            inputs = spec["inputs"][name]
            execution = self._governed_execution_inputs(state, inputs=inputs, method_version_id=identity,
                parameters=parameters, subset_spec=spec["subset_spec"])
            ids = set(inputs.values())
            if population is not None and ids != population:
                raise ValueError("Candidate trials must use comparable immutable input states")
            population = ids
            for artifact in ids:
                self.methods.artifact_path(artifact)
            if spec["subset_spec"].get("kind") == "full_dataset":
                expected_ids = {ref["artifact_id"] for ref in execution["execution_refs"].values()} if execution is not None else original_ids
                if ids != expected_ids:
                    raise ValueError("Full-dataset trial inputs differ from the declared dataset")
                subset_summary = observed
            else:
                inspection_id = spec["subset_spec"].get("inspection_artifact_id")
                if not inspection_id:
                    raise ValueError("A subset requires a retained coordinator inspection artifact")
                inspection = finite_json(self.methods.artifact_path(inspection_id))
                if set(inspection.get("input_artifact_ids", [])) != ids or set(inspection.get("parent_artifact_ids", [])) != original_ids:
                    raise ValueError("Subset inspection does not bind the trial and source artifacts")
                subset_summary = inspection["dataset_summary"]
            candidate = self._current_candidate(state, name, actor=actor, summary=subset_summary,
                artifact_ids=original_ids if execution is not None else ids)
            if candidate["applicability"] != "eligible":
                raise ValueError("Trial subset does not preserve hard preconditions")
            if contract["execution"]["memory_mb"] > limits["memory_mb"]:
                raise ValueError("Method resource requirements exceed the trial budget")
            reserved_seconds += contract["execution"]["timeout_seconds"]
            admitted[name] = (identity, inputs, parameters)
            implementation = contract["implementation"]
            commands.append(str(implementation.get("entrypoint", "ToolDispatcher.execute:" + implementation.get("tool_name", ""))))
        if reserved_seconds > limits["timeout_seconds"]:
            raise ValueError("Declared method timeouts exceed the trial budget")
        # All policies are checked before creating branch steps. These are fresh
        # exploratory steps, never edits to a canonical revision.
        step_ids = {name: self.methods.add_step(branch_id, identity, inputs, params, actor=actor)["step_id"]
                    for name, (identity, inputs, params) in admitted.items()}
        spec.update(commands=commands, step_ids=step_ids, branch_id=branch_id, status="declared")
        spec["declaration_sha256"] = _digest(spec)
        updated = deepcopy(state)
        updated["experiments"].append(spec)
        self._save(state, updated, actor=actor, action="scientific_trial_declared")
        return spec

    def _metric(self, run, source):
        if source["kind"] == "runtime":
            if source["field"] == "elapsed_seconds":
                return (datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
            return sum(self.methods.get("artifact", value)["size_bytes"] for value in run["outputs"].values())
        value = run["validation"] if source["kind"] == "validation" else finite_json(
            self.methods.artifact_path(run["outputs"][source["output"]]))
        for part in source["field"].split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) and part.isdigit() else value[part]
        if type(value) not in (int, float, bool) or not math.isfinite(value):
            raise ValueError("Metric is not an observed finite scalar")
        return value

    @plan_operation
    def run_trial(self, session_id, experiment_id, *, actor, allow_data_execution=False, executor=None, execution_bindings=None,
                  before_dispatch=None, cancellation_event=None):
        if allow_data_execution is not True:
            raise PermissionError("Trial data execution requires explicit authorization")
        if before_dispatch is not None and not callable(before_dispatch):
            raise TypeError("Use an injected trusted candidate dispatch guard")
        state = self.get(session_id)
        from .rna_design_audit import require_rna_design_audit
        require_rna_design_audit(state, self.methods)
        from .rna_design_audit import require_rna_design_execution
        require_rna_design_execution(state, self.methods)
        if state["selection_in_progress"] or state["recommendation"]["choice"] is not None or any(
                item["status"] == "running" for item in state["experiments"]):
            raise ValueError("Trials cannot run during model selection or after a recommendation")
        matches = [item for item in state["experiments"] if item["id"] == experiment_id]
        if len(matches) != 1 or matches[0]["status"] != "declared":
            raise ValueError("Trial is unknown or already attempted; declarations execute once")
        experiment = deepcopy(matches[0])
        expected = dict(experiment)
        declaration_hash = expected.pop("declaration_sha256")
        if _digest(expected) != declaration_hash:
            raise ValueError("Frozen trial declaration changed")
        if state.get("rna_design", {}).get("objective", {}).get("intent") == "infer":
            for name in experiment["candidates"]:
                candidate = self._candidate(state, name)
                self._governed_execution_inputs(state, inputs=experiment["inputs"][name],
                    method_version_id=candidate["method_version_id"], parameters=experiment["parameters"][name],
                    subset_spec=experiment["subset_spec"])
        reserved = deepcopy(state)
        index = state["experiments"].index(matches[0])
        if execution_bindings is not None:
            if (not isinstance(execution_bindings, dict) or set(execution_bindings) != set(experiment["candidates"])
                    or any(not isinstance(item, dict) or set(item) != {"controller_id", "correlation"}
                           for item in execution_bindings.values())):
                raise ValueError("Every trial execution needs its reserved controller and scientific correlation")
            canonical_json(execution_bindings)
            reserved.setdefault("execution_correlations", {})[experiment_id] = deepcopy(execution_bindings)
        reserved["experiments"][index]["status"] = "running"
        self._save(state, reserved, actor=actor, action="scientific_trial_started")
        deadline = time.monotonic() + experiment["resource_limits"]["timeout_seconds"]
        results = []
        for name in experiment["candidates"]:
            candidate = self._candidate(state, name)
            row = {"experiment_id": experiment_id, "candidate": name, "metric_values": {},
                   "artifacts": [], "warnings": [], "failures": [], "provenance": {
                       "declaration_sha256": declaration_hash, "method_version": candidate["method_version_id"],
                       "input_artifact_ids": sorted(experiment["inputs"][name].values()),
                       "parameters": experiment["parameters"][name]}}
            try:
                summary = state["context"]["dataset_summary"]
                execution = self._governed_execution_inputs(state, inputs=experiment["inputs"][name],
                    method_version_id=candidate["method_version_id"], parameters=experiment["parameters"][name],
                    subset_spec=experiment["subset_spec"])
                if experiment["subset_spec"].get("kind") != "full_dataset":
                    inspection = finite_json(self.methods.artifact_path(experiment["subset_spec"]["inspection_artifact_id"]))
                    summary = inspection["dataset_summary"]
                current = self._current_candidate(state, name, actor=actor, summary=summary,
                    artifact_ids=summary.get("artifact_ids", []) if execution is not None else experiment["inputs"][name].values())
                if current["applicability"] != "eligible":
                    raise ValueError("Current trial preconditions are not eligible: " + "; ".join(current["blockers"]))
                if time.monotonic() >= deadline:
                    raise TimeoutError("Trial budget exhausted before the next candidate")
                binding = execution_bindings[name] if execution_bindings is not None else None
                if before_dispatch is not None:
                    descriptor = {"session_id": session_id, "experiment_id": experiment_id, "candidate": name,
                        "step_id": experiment["step_ids"][name], "method_version_id": candidate["method_version_id"],
                        "input_artifact_ids": sorted(experiment["inputs"][name].values()),
                        "parameters": deepcopy(experiment["parameters"][name]), "declaration_sha256": declaration_hash,
                        "resource_limits": deepcopy(experiment["resource_limits"])}
                    try:
                        before_dispatch(deepcopy(descriptor))
                    except Exception as error:
                        row.update(status="failed", failures=[f"{type(error).__name__}: {error}"[:2000]],
                            warnings=["trial_admission_denied_no_dispatch"])
                        results.append(row)
                        break  # Retain the refusal and prior results; no later candidate dispatch.
                run = self.methods.run_step(experiment["step_ids"][name], actor=actor, executor=executor,
                    **({"data_execution_authorized": allow_data_execution, "cancellation_event": cancellation_event}
                       if "fastq_input" in summary.get("observed", {}) else {}),
                    **({"run_id": binding["controller_id"], "correlation": binding["correlation"]} if binding else {}))
                row["provenance"].update(run_id=run["run_id"], evidence_root=run["evidence"]["root_hash"],
                    environment=state["registry"][candidate["method_version_id"]]["environment"],
                    started_at=run["started_at"], finished_at=run["finished_at"])
                if run["status"] != "succeeded":
                    row.update(status="failed", failures=[run.get("error", "Method execution failed")])
                elif time.monotonic() > deadline:
                    row.update(status="resource_limit", failures=["Trial exceeded its declared elapsed-time budget"])
                else:
                    row.update(status="success", warnings=run["validation"]["warnings"],
                        artifacts=[{"artifact_id": value, "role": key, "immutable": True} for key, value in run["outputs"].items()])
                    for metric in experiment["metrics"]:
                        try:
                            row["metric_values"][metric["name"]] = self._metric(run, experiment["metric_sources"][metric["name"]])
                        except (KeyError, IndexError, TypeError, ValueError) as error:
                            row["metric_values"][metric["name"]] = None
                            row["warnings"].append("Metric unavailable: " + metric["name"] + ": " + str(error)[:200])
                            row["status"] = "partial"
            except Exception as error:
                row.update(status="resource_limit" if isinstance(error, TimeoutError) else "failed",
                           failures=[f"{type(error).__name__}: {error}"[:2000]])
            results.append(row)
        completed = deepcopy(reserved)
        completed["experiments"][index]["status"] = "completed"
        completed["results"].extend(results)
        self._save(reserved, completed, actor=actor, action="scientific_trial_finished")
        return results

    def _verified_results(self, state):
        experiments = {item["id"]: item for item in state["experiments"]}
        for experiment in experiments.values():
            declared = dict(experiment)
            expected_hash = declared.pop("declaration_sha256")
            declared["status"] = "declared"
            if _digest(declared) != expected_hash:
                raise ValueError("Frozen scientific trial declaration changed")
        for result in state["results"]:
            identity = result["provenance"].get("run_id")
            if identity:
                run = self.methods.get("run", identity)
                if (not self.methods.provenance(identity)["valid"]
                        or run["evidence"]["root_hash"] != result["provenance"]["evidence_root"]):
                    raise ValueError("Trial execution evidence changed")
                experiment = experiments[result["experiment_id"]]
                name = result["candidate"]
                if (run["step_id"] != experiment["step_ids"][name]
                        or run["parameters"] != experiment["parameters"][name]
                        or run["inputs"] != experiment["inputs"][name]
                        or result["provenance"]["parameters"] != run["parameters"]):
                    raise ValueError("Trial result does not match its declared execution")
                if result["status"] in {"success", "partial"}:
                    if run["status"] != "succeeded":
                        raise ValueError("Failed execution cannot become successful trial evidence")
                    expected_metrics = {}
                    for metric in experiment["metrics"]:
                        try:
                            expected_metrics[metric["name"]] = self._metric(run, experiment["metric_sources"][metric["name"]])
                        except (KeyError, IndexError, TypeError, ValueError):
                            expected_metrics[metric["name"]] = None
                    if result["metric_values"] != expected_metrics or (
                            result["status"] == "success" and any(value is None for value in expected_metrics.values())):
                        raise ValueError("Trial metrics differ from actual execution evidence")
                    if {item["role"]: item["artifact_id"] for item in result["artifacts"]} != run["outputs"]:
                        raise ValueError("Trial artifact bindings differ from actual execution evidence")
                for artifact in result["artifacts"]:
                    self.methods.artifact_path(artifact["artifact_id"])

    def _evidence_assessment(self, state):
        """A necessary comparison floor over verified receipts, not a ranking oracle."""
        purpose = validate_planning_metadata(state.get("evaluation_purpose", "method_selection"),
                                             state.get("user_parameter_requirements", []))["evaluation_purpose"]
        metadata_recorded = "evaluation_purpose" in state and "user_parameter_requirements" in state
        eligible = {item["name"] for item in state["candidates"]
                    if item["applicability"] == "eligible" and item["method_version_id"] is not None}
        unavailable = [item["name"] for item in state["candidates"]
                       if item["applicability"] == "eligible" and item["method_version_id"] is None]
        limitations = ["Necessary comparative-pilot evidence floor only; not independent scientific or clinical validation.",
                       "No overall superiority claim is supported; unmeasured methods and scientific assumptions remain outside this comparison."]
        if unavailable:
            limitations.append("Eligible candidates with unavailable implementations remain untested: " + ", ".join(unavailable))
        if not metadata_recorded:
            limitations.append("Legacy plan has no recorded evaluation purpose or requirement attribution.")
        diagnostic_next_steps = []
        for result in state["results"]:
            identity = result["provenance"].get("run_id")
            if result["status"] not in {"success", "partial"} or not identity:
                continue
            run = self.methods.get("run", identity)
            validation = run["validation"]
            if (run["status"] != "succeeded" or validation.get("passed") is not True
                    or validation.get("comparator") != "observation_diagnostic"):
                continue
            next_step = {"candidate": result["candidate"], "run_id": identity,
                "evidence_scope": validation["evidence_scope"], "flagged_count": len(validation["flagged_ids"]),
                "unavailable_count": len(validation["unavailable_ids"]), "exclusions_authorized": False,
                "next_step": ("Obtain missing evidence for unavailable diagnostics and review descriptive flags"
                              if validation["unavailable_ids"] else
                              "Review descriptive flags alongside immutable measurements and assay semantics"),
                "limitations": ["Candidate flags are predictions, not technical-damage or biological truth",
                                "This diagnostic next step does not establish a comparative winner"]}
            if validation.get("diagnostic_output_format") == "devbench.observation-diagnostics/2":
                next_step.update(score_available_count=len(validation["score_available_ids"]),
                                 deferred_flag_count=len(validation["deferred_flag_ids"]),
                                 profile_metrics=validation["profile_metrics"],
                                 arithmetic_fidelity=validation["arithmetic_fidelity"],
                                 next_step="Inspect computed numerical profiles; resolve categorical rules separately from assay interpretation")
            diagnostic_next_steps.append(next_step)
        successful = {item["candidate"] for item in state["results"]
                      if item["status"] == "success" and item["provenance"].get("run_id")}
        missing_success = sorted(eligible - successful)
        if missing_success:
            limitations.append("Eligible implemented candidates without successful trial evidence: " + ", ".join(missing_success))
        comparable = []
        scientific_categories = {"accuracy_proxy", "signal_preservation", "artifact_removal", "stability", "calibration", "failure_mode"}
        structural_roots = {"passed", "invariants", "shape", "samples", "parameters", "inputs", "outputs",
                            "removed_ids", "added_ids", "metadata", "variables", "layers", "embeddings"}
        for experiment in state["experiments"]:
            names = set(experiment["candidates"])
            if len(eligible) < 2 or not eligible <= names or experiment["status"] != "completed":
                continue
            results = {item["candidate"]: item for item in state["results"]
                       if item["experiment_id"] == experiment["id"] and item["status"] == "success"
                       and item["provenance"].get("run_id")}
            if not eligible <= set(results):
                continue
            populations = {tuple(sorted(experiment["inputs"][name].values())) for name in eligible}
            if len(populations) != 1:
                continue
            metrics = []
            validations = {name: self.methods.get("run", results[name]["provenance"]["run_id"])["validation"]
                           for name in eligible}
            for metric in experiment["metrics"]:
                source = experiment["metric_sources"][metric["name"]]
                tokens = [part.replace("~1", "/").replace("~0", "~") for part in source["field"].split("/")[1:]]
                names_normalized = [_name(value) for value in [metric["name"], *tokens] if any(char.isalnum() for char in value)]
                if (source["kind"] != "validation" or metric["category"] not in scientific_categories
                        or metric["direction"] == "descriptive" or not tokens or tokens[0] in structural_roots
                        or any("retention" in value or "retained" in value or value in {"removed", "removalrate", "cellcount", "ncells", "nobs"}
                               for value in names_normalized)):
                    continue
                provenances = [coordinator_measurement_provenance(validations[name], source["field"])
                               for name in sorted(eligible)]
                if any(record is None for record in provenances) or any(record != provenances[0] for record in provenances[1:]):
                    continue
                values = {name: results[name]["metric_values"].get(metric["name"]) for name in sorted(eligible)}
                if all(type(value) in (int, float) and math.isfinite(value) for value in values.values()):
                    metrics.append({"name": metric["name"], "category": metric["category"], "direction": metric["direction"],
                                    "source": deepcopy(source), "values": values,
                                    "measurement_provenance": deepcopy(provenances[0])})
            if metrics:
                comparable.append({"experiment_id": experiment["id"], "candidates": sorted(eligible), "metrics": metrics})
        rows = []
        for candidate in state["candidates"]:
            reasons = []
            if not metadata_recorded:
                reasons.append("Legacy evaluation purpose and requirement attribution are unverified; create an explicitly scoped plan for a new scientific recommendation.")
            if purpose == "execution_verification":
                reasons.append("Execution verification cannot produce a scientific recommendation.")
            if candidate["applicability"] != "eligible":
                reasons.append("Candidate scientific preconditions are not eligible.")
            if candidate["method_version_id"] is None:
                reasons.append("Candidate implementation is unavailable.")
            if len(eligible) < 2:
                reasons.append("At least two eligible implemented candidates need a comparable successful joint experiment.")
            if not comparable:
                reasons.append("No predeclared joint experiment supplies successful matching-input evidence for all eligible implemented alternatives and shared scientifically directed numeric coordinator metrics.")
            rows.append({"candidate": candidate["name"], "meets_evidence_floor": not reasons,
                         "comparable_experiments": deepcopy(comparable) if candidate["name"] in eligible else [],
                         "reasons": reasons, "limitations": list(limitations)})
        return {"evaluation_purpose": purpose, "scope": "comparative_pilot_only",
                "independent_scientific_validation": False, "eligible_implemented_candidates": sorted(eligible),
                "unavailable_candidates": unavailable, "candidates": rows, "limitations": limitations,
                "diagnostic_next_steps": diagnostic_next_steps}

    def evidence_assessment(self, session_id):
        """Explicit receipt verification; ordinary listing never calls this helper."""
        state = self.get(session_id)
        self._verified_results(state)
        return self._evidence_assessment(state)

    @staticmethod
    def _check_evidence_floor(state, recommendation, assessment):
        if recommendation["choice"] is None:
            return
        if assessment["evaluation_purpose"] == "execution_verification":
            raise ValueError("Execution verification cannot produce a non-null scientific recommendation")
        row = next((item for item in assessment["candidates"] if item["candidate"] == recommendation["choice"]), None)
        tests = set(recommendation.get("tests_run", []))
        qualifying = [] if row is None else [item for item in row["comparable_experiments"]
            if item["experiment_id"] in tests and any(result["experiment_id"] == item["experiment_id"]
                and result["candidate"] == recommendation["choice"] and result["status"] == "success"
                and result["provenance"]["parameters"] == recommendation["params"] for result in state["results"])]
        if row is None or not row["meets_evidence_floor"] or not qualifying:
            raise ValueError("Scientific recommendation lacks the necessary comparable coordinator-validation evidence floor")

    @plan_operation
    def nominate_explanation(self, session_id, selector, *, actor, allow_model_execution=False,
                            verified_evidence=None):
        """Retain a normal charged preliminary explanation before a post-fit fork."""
        state = self.get(session_id)
        if (state["selection_in_progress"] or state["approvals"]
                or any(item["status"] == "running" for item in state["experiments"])):
            raise ValueError("Preliminary explanation cannot change a running or approved cycle")
        if state.get("preliminary_explanation") is not None:
            raise ValueError("The preliminary explanation nomination is frozen")
        self._verified_results(state)
        if not any(row["status"] == "success" for row in state["results"]):
            raise ValueError("A preliminary explanation requires actual verified successful outputs")
        output_view = None
        if verified_evidence is not None:
            from .rna_investigation_projection import verify_packet_projection
            output_view = verify_packet_projection(self.methods, verified_evidence, state)
            if output_view["stage"] != "post_fit_explanation":
                raise ValueError("A preliminary explanation requires a verified post-fit output view")
            if "model_campaign" in state:
                _, _, campaign = self._campaign_runtime(state)
                if (any(output_view["bindings"][key] != campaign["capability"][key]
                        for key in ("source_digest", "runtime_digest"))
                        or output_view["bindings"]["policy_digest"] != campaign["identity"].get("investigation_policy_digest")):
                    raise ValueError("Verified output view source, runtime or policy binding differs from its campaign")
        public_plan = self.export(session_id)
        for result in public_plan["results"]:
            for artifact in result["artifacts"]:
                artifact["output_name"] = artifact.pop("role")
        context = {"decision_kind": "method_preliminary_explanation", "plan": public_plan,
            "sources": state["sources"], "visual_content_supplied": False,
            "assessment_limits": ["Validated plot pixels are absent", "Qualified independent scientific assessment is absent"],
            "recommendation_evidence_assessment": self._evidence_assessment(self._rechecked_state(state, actor=actor))}
        if output_view is not None:
            context["verified_output_view"] = output_view
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        response = validate_preliminary_explanation(answer)
        identities = set(state["context"]["dataset_summary"].get("artifact_ids", []))
        identities.update(source["artifact_id"] for source in state["sources"])
        identities.update(artifact["artifact_id"] for row in state["results"] for artifact in row["artifacts"])
        allowed = {_digest({key: value for key, value in self._trial_input_reference(identity).items() if key != "format"})
            for identity in identities}
        if output_view is not None:
            allowed.update(_digest(ref) for ref in output_view["evidence_refs"])
        for item in (*response["hypotheses"], *response["ambiguity_items"]):
            if any(_digest(ref) not in allowed for ref in item["evidence_refs"]):
                raise ValueError("Preliminary explanation cites evidence outside the exact verified input/output bindings")
        updated = deepcopy(state)
        updated["preliminary_explanation"] = {"response": response,
            "selection_attempt": state["selection_history"][-1]["attempt"],
            "visual_content_supplied": False, "assessment_limits": context["assessment_limits"]}
        if output_view is not None:
            updated["preliminary_explanation"]["verified_output_packet_sha256"] = _digest(output_view)
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="preliminary_explanation_nominated")
        return response

    @plan_operation
    def recommend(self, session_id, selector, *, actor, allow_model_execution=False):
        state = self.get(session_id)
        if state["selection_in_progress"]:
            raise ValueError("Wait for the current planner selection before review")
        if state["approvals"] or any(item["status"] == "running" for item in state["experiments"]):
            raise ValueError("Cannot replace a reviewed recommendation or recommend during a trial")
        self._verified_results(state)
        checked = self._rechecked_state(state, actor=actor)
        assessment = self._evidence_assessment(checked)
        assessed = deepcopy(state)
        assessed["candidates"] = checked["candidates"]
        assessed["recommendation_evidence_assessment"] = assessment
        state = self._save(state, assessed, actor=actor, action="recommendation_evidence_assessed")
        public_plan = self.export(session_id)
        # Artifact output labels are public evidence, not coordinator roles.
        # Project only this known field; preserve the retained export/receipts.
        for result in public_plan["results"]:
            for artifact in result["artifacts"]:
                artifact["output_name"] = artifact.pop("role")
        context = {"decision_kind": "method_recommendation", "plan": public_plan, "sources": state["sources"],
                   "recommendation_evidence_assessment": assessment}
        answer, state = self._select(state, selector, context, actor=actor, allow_model_execution=allow_model_execution)
        recommendation = validate_recommendation(answer)
        choice = recommendation["choice"]
        if choice is not None and assessment["evaluation_purpose"] == "execution_verification":
            self._check_evidence_floor(state, recommendation, assessment)
        if choice is not None:
            candidate = self._candidate(state, choice)
            successful = [item for item in state["results"] if item["candidate"] == choice and item["status"] == "success"]
            if candidate["applicability"] != "eligible" or not successful:
                raise ValueError("A chosen method requires eligible preconditions and successful trial evidence")
            if candidate["implementation_confidence"] == "low" and recommendation["implementation_confidence"] != "low":
                raise ValueError("Custom or generated implementation confidence remains low")
            tested = set(recommendation.get("tests_run", []))
            matching = [item for item in successful if item["experiment_id"] in tested and
                        item["provenance"]["parameters"] == recommendation["params"]]
            if not matching or not tested <= {item["experiment_id"] for item in state["results"]}:
                raise ValueError("Recommendation parameters and tests must match recorded successful trials")
            untested = [item for item in state["candidates"] if item["applicability"] == "eligible" and
                        not any(row["candidate"] == item["name"] and row["status"] == "success" for row in state["results"])]
            if untested and (recommendation["confidence"] == "high" or recommendation["scientific_confidence"] == "high"):
                raise ValueError("High scientific confidence is unsupported while plausible candidates remain untested")
            self._check_evidence_floor(state, recommendation, assessment)
        updated = deepcopy(state)
        updated["recommendation"] = recommendation
        updated["selection_history"][-1]["status"] = "accepted"
        self._save(state, updated, actor=actor, action="scientific_recommendation_proposed")
        return recommendation

    @plan_operation
    def approve_recommendation(self, session_id, *, actor, rationale):
        state = self.get(session_id)
        if state["selection_in_progress"]:
            raise ValueError("Wait for the current planner selection before review")
        if state["recommendation"]["choice"] is None:
            raise ValueError("There is no selected recommendation to approve")
        self._verified_results(state)
        checked = self._rechecked_state(state, actor=actor)
        self._check_evidence_floor(checked, state["recommendation"], self._evidence_assessment(checked))
        updated = deepcopy(state)
        updated["approvals"]["approve_recommendation"] = {"status": "approved", "actor": _label(actor),
            "rationale": _reason(rationale), "recommendation_sha256": _digest(state["recommendation"]), "at": _now(),
            "identity_assurance": "caller-asserted"}
        self._save(state, updated, actor=actor, action="scientific_recommendation_approved")
        return updated["approvals"]["approve_recommendation"]

    def _approved_choice(self, state, *, actor="coordinator-action-recheck"):
        if state["selection_in_progress"]:
            raise ValueError("Wait for the current planner selection before changing approval scope")
        approval = state["approvals"].get("approve_recommendation")
        if not approval or approval["status"] != "approved" or approval["recommendation_sha256"] != _digest(state["recommendation"]):
            raise ValueError("Explicit approval of this exact recommendation is required")
        self._verified_results(state)
        checked = self._rechecked_state(state, actor=actor)
        self._check_evidence_floor(checked, state["recommendation"], self._evidence_assessment(checked))
        return self._candidate(checked, state["recommendation"]["choice"])

    @plan_operation
    def save_recipe(self, session_id, name, *, actor, rationale):
        state = self.get(session_id)
        choice = self._approved_choice(state)
        recipe = {"recipe_id": uuid.uuid4().hex, "name": _label(name), "status": "experimental",
                  "method_version_id": choice["method_version_id"], "parameters": state["recommendation"]["params"],
                  "source_plan_id": session_id, "recommendation_sha256": _digest(state["recommendation"]),
                  "created_by": _label(actor), "rationale": _reason(rationale)}
        updated = deepcopy(state)
        updated["approvals"]["save_experimental_recipe"] = {"status": "approved", "recipe_id": recipe["recipe_id"]}
        with self.methods._db() as db:
            if self.methods._get(db, "method_plan", session_id) != state:
                raise ValueError("Planner state changed")
            self.methods._put(db, "recipe", recipe["recipe_id"], recipe)
            self.methods._put(db, "method_plan", session_id, updated, update=True)
            self.methods._put(db, "method_plan_snapshot", _digest(updated), updated)
            self.methods._audit(db, actor, "experimental_recipe_saved", recipe)
        return recipe

    @plan_operation
    def propose_selected(self, session_id, *, actor, rationale):
        state = self.get(session_id)
        choice = self._approved_choice(state)
        results = [item for item in state["results"] if item["candidate"] == choice["name"] and item["status"] == "success"
                   and item["experiment_id"] in state["recommendation"].get("tests_run", [])
                   and item["provenance"]["parameters"] == state["recommendation"]["params"]]
        full = state.get("full_execution")
        if full:
            if full["status"] != "succeeded" or full["recommendation_sha256"] != _digest(state["recommendation"]):
                raise ValueError("An approved successful full execution is required before proposal")
            run_id = full["run_id"]
        else:
            selected = results[-1]
            experiment = next(item for item in state["experiments"] if item["id"] == selected["experiment_id"])
            if experiment["subset_spec"].get("kind") != "full_dataset":
                raise ValueError("A subset trial requires an approved full execution before proposal")
            run_id = selected["provenance"]["run_id"]
        proposal = self.methods.propose(run_id, actor=actor, rationale=rationale)
        updated = deepcopy(state)
        updated["analysis_proposal_id"] = proposal["proposal_id"]
        self._save(state, updated, actor=actor, action="recommended_result_proposed")
        return proposal

    @plan_operation
    def prepare_full_execution(self, session_id, branch_id, inputs, *, actor):
        state = self.get(session_id)
        choice = self._approved_choice(state)
        if state.get("full_execution", {}).get("status") == "running":
            raise ValueError("Full execution is already running")
        if set(inputs.values()) != set(state["context"]["dataset_summary"].get("artifact_ids", [])):
            raise ValueError("Full execution must bind the original declared dataset")
        step = self.methods.add_step(branch_id, choice["method_version_id"], inputs, state["recommendation"]["params"], actor=actor)
        updated = deepcopy(state)
        updated["full_execution"] = {"step_id": step["step_id"], "recommendation_sha256": _digest(state["recommendation"]),
                                     "status": "pending"}
        updated["approvals"].pop("approve_full_execution", None)
        self._save(state, updated, actor=actor, action="full_execution_proposed")
        return updated["full_execution"]

    @plan_operation
    def approve_full_execution(self, session_id, *, actor, rationale):
        state = self.get(session_id)
        self._approved_choice(state)
        full = state.get("full_execution")
        if not full or full["status"] != "pending":
            raise ValueError("A concrete pending full-execution step is required")
        updated = deepcopy(state)
        updated["full_execution"].update(status="approved", actor=_label(actor), rationale=_reason(rationale))
        updated["approvals"]["approve_full_execution"] = {"status": "approved", "step_id": full["step_id"]}
        self._save(state, updated, actor=actor, action="full_execution_approved")
        return updated["full_execution"]

    @plan_operation
    def run_full(self, session_id, *, actor, allow_data_execution=False, executor=None):
        if allow_data_execution is not True:
            raise PermissionError("Full data execution requires explicit authorization")
        state = self.get(session_id)
        self._approved_choice(state)
        full = state.get("full_execution")
        if not full or full["status"] != "approved" or full["recommendation_sha256"] != _digest(state["recommendation"]):
            raise ValueError("This full-execution step has not been approved")
        reserved = deepcopy(state)
        reserved["full_execution"]["status"] = "running"
        self._save(state, reserved, actor=actor, action="full_execution_started")
        updated = deepcopy(reserved)
        try:
            run = self.methods.run_step(full["step_id"], actor=actor, executor=executor)
            updated["full_execution"].update(status=run["status"], run_id=run["run_id"])
        except Exception as error:
            updated["full_execution"].update(status="failed", error=f"{type(error).__name__}: {error}"[:2000])
            raise
        finally:
            self._save(reserved, updated, actor=actor, action="full_execution_finished")
        return run

    def export(self, session_id):
        state = self.get(session_id)
        recommendation = deepcopy(state["recommendation"])
        actions = {item["action"]: item for item in recommendation["approval_actions"]}
        for scope in _SCOPES:
            actions.setdefault(scope, {"action": scope, "required": True, "status": "not_requested"})
            actions[scope]["required"] = True
            if scope in state["approvals"]:
                actions[scope]["status"] = state["approvals"][scope]["status"]
        if state["analysis_proposal_id"]:
            actions["approve_analysis_plan_change"]["status"] = self.methods.get("proposal", state["analysis_proposal_id"])["status"]
        if state.get("full_execution", {}).get("status") == "pending":
            actions["approve_full_execution"]["status"] = "pending"
        recommendation["approval_actions"] = list(actions.values())
        return validate_plan({"task": state["context"]["task"], "dataset_summary": state["context"]["dataset_summary"],
            "candidates": state["candidates"], "experiments": state["experiments"], "results": state["results"],
            "recommendation": recommendation})
