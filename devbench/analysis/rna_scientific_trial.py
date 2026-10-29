"""Local C4 readiness and trusted question-to-workflow orchestration.

The CLI can prepare/verify only. Live admission has no default implementation:
trusted in-process authority must authenticate approvals, verify the exact
engineering qualification and enforce aggregate allocation before dispatch.
This module adds neither a provider API nor an OS worker boundary. Fakes test
control flow without qualifying any real model, publication or scientific trial.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Protocol

from .checkpoints import digest
from .episode_agent import AgentLimits, Selection, selector_configuration
from .episode_proposals import _absolute, _load, _seal, require_disjoint_output
from .rna_input_contract import inspect_registered_bulk_rna_input
from .workflow_entrypoints import workflow_action, CHECKPOINT_NAME, LEDGER_NAME
from .rna_scientific_trial_contract import (
    FORMAT_V2, FORMAT_V3, ASSESSMENT_FORMATS, validate_trial_request, validate_approval, trial_binding, bounded_source,
)
from .rna_trial_authority import AuthenticatedTrialAuthority, trial_enforcement_source_digest, trial_enforcement_runtime_digest

SCOPE = "local_trial_readiness_not_engineering_or_scientific_qualification"


class TrialAuthority(Protocol):
    """Trusted coordinator dependency, never constructed from request JSON.

    authorize must independently authenticate operator evidence and verify
    exact source/runtime/backend/platform qualification, dataset access/privacy,
    preventive aggregate tokens (all requests), actual participant boundary,
    baseline dependency decisions, reviewer decisions and finite allocation.
    consume must atomically reserve the one-use binding before the dispatch and
    enforce its allocation. A post-hoc token receipt or self-asserted qualified
    flag is insufficient. Implementations are deliberately not guessed here.
    """
    def authorize(self, request: dict, *, purpose: str, binding_sha256: str) -> None: ...
    def consume(self, request: dict, *, purpose: str, binding_sha256: str) -> None: ...


def _ref_inside(reference, workspace):
    path = _absolute(reference["manifest_path"])
    if not path.resolve().is_relative_to(_absolute(workspace).resolve()) or path.is_symlink():
        raise ValueError("Trial evidence manifest lies outside the assigned workspace")


def inspect_trial_data(methods, request):
    """Read bounded registered JSON; no data fitting or model call occurs."""
    request = validate_trial_request(request)
    return inspect_registered_bulk_rna_input(methods, request["dataset"]["binding"])


def _design_audit(request, inspection):
    """Recompute necessary design warnings, without declaring scientific validity."""
    import numpy as np
    warnings = []
    rows = inspection["sample_metadata"]
    roles = inspection.get("metadata_roles", {})
    infer = request["objective"]["intent"] == "infer"
    unit = roles.get("biological_unit")
    units = [row.get(unit) for row in rows] if unit else []
    if infer and (not units or any(value is None for value in units) or len(set(units)) != len(rows)):
        warnings.append("independent_biological_units_unknown_or_repeated")
    if infer and (inspection["biological_independence"] is None or inspection["biological_independence"].get("independent_units") is not True):
        warnings.append("biological_independence_declaration_missing")
    if infer and inspection["representation"] is None:
        warnings.append("measurement_representation_unknown")
    design = request["design"]
    fields = {item["field"] for item in design["contrasts"]} | set(design["covariates"])
    if infer and not design["contrasts"] and design["chronological_field"] is None:
        warnings.append("inferential_contrast_or_chronological_estimand_missing")
    chronological = design["chronological_field"]
    # Audit declared batch alongside every inferential target, even if the
    # proposed design drops it. Removing a confounded covariate cannot identify
    # the biological effect. This checks rank only; it never fits expression.
    if infer and roles.get("batch") and (design["contrasts"] or chronological):
        fields.add(roles["batch"])
    if chronological is not None:
        fields.add(chronological)
        order = design["chronological_order"]
        actual = {str(row.get(chronological)) for row in rows}
        if len(order) < 2 or set(order) != actual:
            warnings.append("chronological_labels_require_complete_explicit_order")
    elif design["chronological_order"]:
        warnings.append("chronological_order_without_field")
    for contrast in design["contrasts"]:
        levels = {str(row.get(contrast["field"])) for row in rows}
        if contrast["reference"] == contrast["target"] or not {contrast["reference"], contrast["target"]} <= levels:
            warnings.append("invalid_contrast:" + contrast["field"])
    columns = [np.ones(len(rows))]
    for field in sorted(fields):
        values = [row.get(field) for row in rows]
        if any(value is None for value in values):
            warnings.append("design_covariate_missing:" + field)
            continue
        if field == chronological:
            order = design["chronological_order"]
            if all(str(value) in order for value in values):
                columns.append(np.array([order.index(str(value)) for value in values], dtype=float))
        elif all(type(value) in (int, float) for value in values):
            columns.append(np.array(values, dtype=float))
        else:
            levels = sorted({str(value) for value in values})
            columns.extend(np.array([float(str(value) == level) for value in values]) for level in levels[1:])
    rank = int(np.linalg.matrix_rank(np.column_stack(columns)))
    if rank != len(columns):
        warnings.append("design_rank_deficient_or_complete_confounding")
    if infer and len(rows) <= len(columns):
        warnings.append("design_has_no_residual_degrees_of_freedom")
    return {"rank": rank, "columns": len(columns), "sample_count": len(rows),
            "unit_count": len(set(units)) if units else None, "blockers": sorted(set(warnings)),
            "assurance": "necessary_recomputed_checks_not_independent_scientific_validation"}


def workflow_snapshot(planner, descriptor, *, workspace=None):
    """Freeze actual governed declarations, methods, dependencies and decisions."""
    if workspace is not None and not _absolute(planner.methods.root).is_relative_to(_absolute(workspace)):
        raise ValueError("Workflow service lies outside the current caller workspace")
    state = planner.get(descriptor["planner_session_id"])
    if "rna_execution_inputs" in state:
        planner._governed_execution_inputs(state, workspace=workspace)
    selected = [item for item in state["experiments"] if item["id"] in descriptor["experiment_ids"]]
    if len(selected) != len(descriptor["experiment_ids"]):
        raise ValueError("Exact workflow experiment is missing")
    experiments = []
    versions = set()
    for item in selected:
        frozen = deepcopy(item)
        expected = frozen.pop("declaration_sha256")
        frozen["status"] = "declared"  # Lifecycle is not part of the frozen declaration.
        if digest(frozen) != expected:
            raise ValueError("Frozen planner declaration drift")
        experiments.append(frozen)
        for name in item["candidates"]:
            matches = [row for row in state["candidates"] if row["name"] == name]
            if len(matches) != 1 or matches[0]["method_version_id"] is None:
                raise ValueError("Workflow candidate has no exact governed method")
            versions.add(matches[0]["method_version_id"])
    contracts = {version: planner.methods.method(version)["contract"] for version in sorted(versions)}
    snapshot = {"format": "devbench.rna-trial-workflow/1", "planner_session_id": descriptor["planner_session_id"],
            "task": state["context"]["task"], "dataset_summary": state["context"]["dataset_summary"],
            "requirements": state["user_parameter_requirements"], "scientific_candidates": state["scientific_candidates"],
            "candidates": state["candidates"], "methods": contracts, "experiments": experiments,
            "selection_history": state["selection_history"]}
    if "rna_execution_inputs" in state:
        snapshot["rna_execution_inputs"] = deepcopy(state["rna_execution_inputs"])
    return snapshot


def validate_assessment_workflow_snapshot(snapshot, *, methods, execution_inputs, dataset, workspace=None):
    """Resolve real governed steps and exact input identities before finalization.

    This validates retained service records. It does not authenticate when a
    caller authored a wrapper or establish scientific adequacy of the method.
    """
    if workspace is not None and not _absolute(methods.root).is_relative_to(_absolute(workspace)):
        raise ValueError("Assessment service lies outside the current caller workspace")
    if (type(dataset) is not dict or set(dataset) != {"format", "binding", "classification", "access_record", "inspection_sha256"}
            or dataset["format"] != "devbench.rna-trial-dataset/2"):
        raise ValueError("Assessment dataset must retain its actual closed original dataset descriptor")
    binding = dataset["binding"]
    from .rna_publication_profiles import read_reference_json
    transformation = read_reference_json(execution_inputs["transformation_receipt_ref"],
        workspace=workspace if workspace is not None else methods.root.parent, service=methods)
    if transformation["input_binding"] != binding:
        raise ValueError("Assessment dataset complete binding/declarations differ from execution input lineage")
    inspection = inspect_registered_bulk_rna_input(methods, binding)
    if digest(inspection) != dataset["inspection_sha256"]:
        raise ValueError("Assessment dataset actual inspection identity differs")
    for role in ("matrix", "metadata"):
        if execution_inputs["original_refs"][role]["artifact_id"] != binding[role + "_artifact_id"]:
            raise ValueError("Assessment dataset original identities differ from execution input lineage")
    expected_inputs = {port: reference["artifact_id"] for port, reference in execution_inputs["execution_refs"].items()}
    steps = []
    if type(snapshot) is dict and set(snapshot) == {"steps"}:
        if type(snapshot["steps"]) is not list or not 1 <= len(snapshot["steps"]) <= 32:
            raise ValueError("Assessment workflow requires bounded actual retained steps")
        for declared in snapshot["steps"]:
            if type(declared) is not dict or "step_id" not in declared:
                raise ValueError("Assessment workflow step is not an actual retained service record")
            actual = methods.get("step", declared["step_id"])
            if actual != declared:
                raise ValueError("Assessment workflow step identity, method, input or parameters differ")
            steps.append(actual)
    elif type(snapshot) is dict and snapshot.get("format") == "devbench.rna-trial-workflow/1":
        from .method_planner import ScientificMethodPlanner
        if type(snapshot["experiments"]) is not list or not 1 <= len(snapshot["experiments"]) <= 16:
            raise ValueError("Assessment workflow requires nonempty bounded actual planner experiments")
        descriptor = {"planner_session_id": snapshot["planner_session_id"],
            "experiment_ids": [item["id"] for item in snapshot["experiments"]]}
        if workflow_snapshot(ScientificMethodPlanner(methods), descriptor, workspace=workspace) != snapshot:
            raise ValueError("Assessment workflow differs from the actual frozen planner declarations")
        if "rna_execution_inputs" in snapshot:
            from .rna_execution_inputs import load_execution_inputs
            bridge = snapshot["rna_execution_inputs"]
            if (bridge["dataset_binding"] != binding or load_execution_inputs(bridge["record_ref"], artifact_service=methods,
                    dataset_binding=binding, workspace=workspace if workspace is not None else bridge.get("workspace")) != execution_inputs):
                raise ValueError("Assessment execution input lineage differs from the generated bridge")
        for experiment in snapshot["experiments"]:
            for candidate in experiment["candidates"]:
                step = methods.get("step", experiment["step_ids"][candidate])
                if step["inputs"] != experiment["inputs"][candidate] or step["parameters"] != experiment["parameters"][candidate]:
                    raise ValueError("Assessment workflow step differs from its governed trial declaration")
                steps.append(step)
    else:
        raise ValueError("Assessment workflow is not a recognized retained governed snapshot")
    if not 1 <= len(steps) <= 128:
        raise ValueError("Assessment workflow requires nonempty bounded actual retained steps")
    if len({step["step_id"] for step in steps}) != len(steps):
        raise ValueError("Assessment workflow steps must be unique")
    for step in steps:
        methods.method(step["method_version_id"])  # Recheck retained source/lock/dependency identity.
        from .rna_execution_inputs import validate_execution_method_binding
        validate_execution_method_binding(execution_inputs, step["method_version_id"], step["parameters"], artifact_service=methods)
        if step["inputs"] != expected_inputs:
            raise ValueError("Assessment workflow exact execution input ports or derived identities differ")
        for identity in step["inputs"].values():
            methods.artifact_path(identity)
    return deepcopy(snapshot)


def assert_assessment_workflow_not_started(snapshot, *, methods):
    """Refuse late finalization with a bounded metadata-only existence query."""
    if type(snapshot) is dict and set(snapshot) == {"steps"}:
        identities = [step["step_id"] for step in snapshot["steps"]]
    elif type(snapshot) is dict and snapshot.get("format") == "devbench.rna-trial-workflow/1":
        identities = [identity for experiment in snapshot["experiments"] for identity in experiment["step_ids"].values()]
    else:
        raise ValueError("Pre-run assessment requires an actual recognized workflow")
    if not 1 <= len(identities) <= 128 or len(set(identities)) != len(identities):
        raise ValueError("Pre-run assessment requires nonempty unique retained steps")
    for identity in identities:
        methods.get("step", identity)
    import sqlite3
    try:
        with methods._db() as db:
            progress = [0]
            def bound_query():
                progress[0] += 1
                return int(progress[0] > 1000)
            db.set_progress_handler(bound_query, 1000)
            try:
                found = db.execute("SELECT 1 FROM records WHERE kind='run' AND json_extract(payload,'$.step_id') IN ("
                    + ",".join("?" for _ in identities) + ") LIMIT 1", identities).fetchone()
            finally:
                db.set_progress_handler(None, 0)
    except sqlite3.DatabaseError as error:
        raise ValueError("Bounded pre-run workflow history cannot be verified; no finalization") from error
    if found is not None:
        raise ValueError("Assessment workflow already started; retain the attempt and use an explicit retrospective assessment")


def _assessment_bindings(request, *, methods, workspace, purpose, exposure, snapshot=None):
    """Verify additive intent/plan custody without touching reference findings."""
    if request["format"] not in ASSESSMENT_FORMATS:
        return None, []
    from .rna_publication_profiles import (
        assessment_rules_digest, load_assessment_intent, load_assessment_plan, load_reference_grounding_binding, read_reference_json,
    )
    intent = load_assessment_intent(request["assessment_intent"], workspace=workspace, service=methods)
    if intent["mode"] != request["objective"]["mode"] or intent["question_ref"] != request["question"]["source"]:
        raise ValueError("Assessment intent actual question or objective mode differs")
    dataset = read_reference_json(intent["dataset_ref"], workspace=workspace, service=methods)
    if dataset != {"format": "devbench.rna-trial-dataset/2", **request["dataset"]}:
        raise ValueError("Assessment intent exact original dataset binding differs")
    if read_reference_json(intent["exposure_ref"], workspace=workspace, service=methods) != exposure:
        raise ValueError("Assessment intent retained publication exposure differs")
    grounding = load_reference_grounding_binding(intent["reference_bundle_ref"], workspace=workspace, service=methods,
        expected_publication_ref=request["publication"], allow_evidence_read=True)
    record = {"intent_sha256": digest(intent), "assessment_rules_sha256": assessment_rules_digest(intent),
        "assessment_intent_ref": request["assessment_intent"], "assessment_plan_ref": request["assessment_plan"],
        "reference_publication_ref": grounding["publication_ref"], "reference_source_collection_ref": grounding["source_collection_ref"]}
    from .rna_execution_inputs import load_execution_inputs
    execution_ref = request.get("execution_inputs_ref")
    if execution_ref is not None:
        execution = load_execution_inputs(execution_ref, artifact_service=methods, dataset_binding=request["dataset"]["binding"],
            workspace=workspace)
        transformation = read_reference_json(execution["transformation_receipt_ref"], workspace=workspace, service=methods)
        if (transformation["method_ports"]["design"] != request["design"]
                or transformation["method_ports"]["objective"] != {"intent": request["objective"]["intent"]}):
            raise ValueError("Generation execution bridge design or objective differs from the actual trial")
        record.update(execution_inputs_ref=deepcopy(execution_ref), execution_inputs_sha256=digest(execution))
    if purpose == "workflow_generation":
        if request["assessment_plan"] is not None:
            raise ValueError("Generation cannot adopt or backfill an execution assessment plan")
        return record, []
    if request["assessment_plan"] is None:
        return record, ["finalized_assessment_plan_missing"]
    plan = load_assessment_plan(request["assessment_plan"], workspace=workspace, service=methods)
    if plan["intent_ref"] != request["assessment_intent"]:
        raise ValueError("Execution plan differs from the original generation intent")
    if execution_ref is not None and plan["execution_inputs_ref"] != execution_ref:
        raise ValueError("Execution plan differs from the generation-approved execution bridge")
    if request["workflow"] is None or plan["workflow_ref"] != request["workflow"]["workflow_ref"]:
        raise ValueError("Execution assessment plan exact generated workflow differs")
    wrapper = read_reference_json(plan["workflow_ref"], workspace=workspace, service=methods)
    if (wrapper["assessment_intent_ref"] != request["assessment_intent"]
            or wrapper["assessment_rules_sha256"] != assessment_rules_digest(intent)
            or wrapper["governed_workflow_sha256"] != request["workflow"]["sha256"]
            or snapshot is not None and wrapper["governed_workflow"] != snapshot):
        raise ValueError("Generated workflow, frozen rules or current governed declarations drifted")
    inputs = load_execution_inputs(plan["execution_inputs_ref"], artifact_service=methods, dataset_binding=request["dataset"]["binding"],
        workspace=workspace)
    if snapshot is not None:
        bridge = snapshot.get("rna_execution_inputs")
        expected_bridge = {"record_ref": execution_ref, "dataset_binding": request["dataset"]["binding"],
            "workspace": str(_absolute(workspace))}
        if ((execution_ref is None) != (bridge is None)
                or bridge is not None and bridge != expected_bridge):
            raise ValueError("Generated workflow differs from the generation-approved execution bridge")
        allowed = {ref["artifact_id"] for ref in inputs["execution_refs"].values()}
        actual = {identity for item in snapshot["experiments"] for bindings in item["inputs"].values() for identity in bindings.values()}
        if actual != allowed:
            raise ValueError("Workflow inputs differ from the bound original/derived execution input lineage")
    prerequisites = read_reference_json(plan["execution_prerequisites_ref"], workspace=workspace, service=methods)
    record.update(assessment_plan_sha256=digest(plan), execution_inputs_sha256=digest(inputs))
    return record, list(prerequisites["blockers"]) if prerequisites["computational_admission"] != "admitted" else []


def inspect_readiness(request, *, methods, workspace, planner=None, purpose="trial_execution", now=None,
                      execution_qualification=None, runtime_context=None):
    """Fail closed; inspection never consumes an approval or dispatches work."""
    from .rna_publication_evidence import load_publication_evidence
    request = validate_trial_request(request)
    workspace = _absolute(workspace)
    if not methods.root.resolve().is_relative_to(workspace.resolve()):
        raise ValueError("Method workspace must be inside the assigned workspace")
    question = bounded_source(request["question"]["source"], workspace).decode("utf-8")
    if request["question"]["source_excerpt"] not in question or request["question"]["text"] not in request["question"]["source_excerpt"]:
        raise ValueError("Question does not match retained source excerpt")
    if any(item["evidence"] not in question for item in request["requirements"]):
        raise ValueError("User requirement evidence must match actual retained question source")
    for source in [request["dataset"]["access_record"], *request["engineering"]["qualification_refs"],
                   *(item["evidence"] for item in request["engineering"]["baseline_decisions"])]:
        bounded_source(source, workspace)
    if request["format"] == FORMAT_V3 and request["engineering"]["qualified_boundary"] is not None:
        bounded_source(request["engineering"]["qualified_boundary"], workspace)
    inspection = inspect_trial_data(methods, request)
    if digest(inspection) != request["dataset"]["inspection_sha256"]:
        raise ValueError("Exact trial dataset identity or inspection changed")
    _ref_inside(request["publication"], workspace)
    evidence = load_publication_evidence(**_publication_args(request["publication"]), workspace=workspace, allow_evidence_read=True)
    if evidence["participant_id"] != request["roles"]["participant"] or evidence["curator_id"] != request["roles"]["evaluator"]:
        raise ValueError("Publication participant/independent curator mismatch")
    assessment, assessment_blockers = _assessment_bindings(request, methods=methods, workspace=workspace,
        purpose=purpose, exposure=evidence["exposure"])
    if request["format"] in ASSESSMENT_FORMATS:
        from .rna_execution_inputs import audit_execution_design
        audit = audit_execution_design(request, inspection)
    else:
        audit = _design_audit(request, inspection)
    blockers = list(audit["blockers"]) + assessment_blockers
    if request["dataset"]["classification"] not in {"synthetic", "public"}:
        blockers.append("dataset_model_access_not_authorized_by_supported_public_or_synthetic_scope")
    if any(item["status"] == "blocked" for item in request["engineering"]["baseline_decisions"]):
        blockers.append("retained_baseline_dependency_blocked")
    generation_controls = None
    if request["format"] == FORMAT_V3:
        generation_controls = {"policy": "generation_jobs", "jobs": "preventive_launch_intents",
            "concurrent_jobs": 1, "children": 0, "hard_total_tokens": "UNSUPPORTED",
            "internal_api_requests": "UNSUPPORTED", "credits": "UNSUPPORTED", "spend": "UNSUPPORTED",
            "reported_usage": "optional_evidence_not_authority"}
        backend = runtime_context.get("backend") if type(runtime_context) is dict else None
        from .codex_signedin import SignedInCodexInvestigationBackend
        accepted = False
        if type(backend) is SignedInCodexInvestigationBackend:
            try:
                capability = backend.require_admission("generation_jobs")
                accepted = (capability.get("scope") == "native" and capability.get("native_boundary_qualified") is True
                    and backend.qualification is not None
                    and backend.qualification.reference == request["engineering"]["qualified_boundary"]
                    and backend.runtime_identity() == {key: request["engineering"][key] for key in
                        ("route", "source_digest", "runtime_digest", "backend_digest", "model", "platform",
                         "client", "authentication", "budget_semantics", "qualified_boundary")})
            except (PermissionError, ValueError):
                pass
        if not accepted:
            blockers.append("signedin_native_boundary_unqualified")
        if purpose == "trial_execution" and request["allocation"]["numeric"] is None:
            blockers.append("separate_numeric_allocation_missing")
    try:
        approval = validate_approval(request, purpose, now=now)
        bounded_source(approval["evidence"], workspace)
    except PermissionError as error:
        blockers.append(str(error))
    snapshot = None
    if purpose == "trial_execution":
        if request["workflow"] is None or planner is None:
            blockers.append("frozen_governed_workflow_missing")
        else:
            snapshot = workflow_snapshot(planner, request["workflow"], workspace=workspace)
            if digest(snapshot) != request["workflow"]["sha256"]:
                raise ValueError("Workflow/decision/method/dependency digest mismatch")
            if snapshot["task"] != request["task"] or snapshot["requirements"] != request["requirements"]:
                raise ValueError("Workflow question or matching requirement record differs")
            if request["format"] in ASSESSMENT_FORMATS:
                assessment, _ = _assessment_bindings(request, methods=methods, workspace=workspace,
                    purpose=purpose, exposure=evidence["exposure"], snapshot=snapshot)
            else:
                inputs = {identity for item in snapshot["experiments"] for candidate in item["inputs"].values() for identity in candidate.values()}
                expected = {request["dataset"]["binding"][key] for key in ("matrix_artifact_id", "metadata_artifact_id")}
                if inputs != expected:
                    raise ValueError("Workflow inputs differ from the exact bulk dataset")
            runs = sum(len(item["candidates"]) for item in snapshot["experiments"])
            allocation = request["allocation"]["numeric"] if request["format"] == FORMAT_V3 else request["allocation"]
            if allocation is None:
                blockers.append("separate_numeric_allocation_missing")
            elif (runs > allocation["max_runs"] or sum(item["resource_limits"]["timeout_seconds"] * len(item["candidates"]) for item in snapshot["experiments"]) > allocation["seconds"]
                    or any(item["resource_limits"]["memory_mb"] > allocation["memory_mb"] for item in snapshot["experiments"])):
                blockers.append("workflow_resource_allocation_exceeded")
        if request["comparison"] is None:
            blockers.append("independently_frozen_publication_comparison_missing")
        else:
            from .rna_publication_comparison import load_publication_comparison
            _ref_inside(request["comparison"], workspace)
            comparison = load_publication_comparison(**_publication_args(request["comparison"]), allow_evidence_read=True,
                **({"workspace": workspace, "artifact_service": methods} if request["format"] in ASSESSMENT_FORMATS else {}))
            if request["format"] in ASSESSMENT_FORMATS:
                if comparison["format"] != "devbench.rna-publication-comparison/2" or comparison["assessment_plan_ref"] != request["assessment_plan"]:
                    raise ValueError("Version 2 comparison must bind the exact finalized assessment plan")
                protocol = None
            else:
                protocol = comparison["protocol"]
            for field, expected in ({"question_sha256": digest(request["question"]),
                                    "dataset_sha256": request["dataset"]["inspection_sha256"],
                                    "workflow_sha256": request["workflow"]["sha256"] if request["workflow"] else None,
                                    "participant_id": request["roles"]["participant"], "curator_id": request["roles"]["evaluator"],
                                    "mode": request["objective"]["mode"], "exposure_sha256": digest(evidence["exposure"])} if protocol is not None else {}).items():
                if protocol[field] != expected:
                    raise ValueError("Publication comparison exact-trial mismatch: " + field)
            if protocol is not None and comparison["publication_root"] != request["publication"]["root_hash"]:
                raise ValueError("Comparison publication evidence identity differs")
    original_blockers, qualification_receipt = list(blockers), None
    if execution_qualification is not None:
        from .rna_route_qualification import TrustedExecutionQualification
        if type(execution_qualification) is not TrustedExecutionQualification:
            raise TypeError("Use the trusted exact execution qualification resolver")
        qualification_receipt = execution_qualification.verify(request, methods=methods, planner=planner,
            purpose=purpose, runtime_context=runtime_context)
        blockers = [item for item in blockers if item not in qualification_receipt["satisfied_blockers"]]
    return {"format": "devbench.rna-trial-readiness/3" if request["format"] == FORMAT_V3 else
                "devbench.rna-trial-readiness/2" if assessment else "devbench.rna-trial-readiness/1", "scope": SCOPE, "trial_id": request["trial_id"],
            "request_sha256": digest(request), "binding_sha256": trial_binding(request, purpose), "purpose": purpose,
            "local_status": "blocked" if blockers else "locally_consistent",
            "blockers": blockers, "design_audit": audit, "dataset_sha256": digest(inspection),
            "workflow_sha256": digest(snapshot) if snapshot else None,
            "live_admission": "unverified_requires_trusted_authority", "scientific_review": "not_performed",
            "clinical_release": "held", "scope_limits": ["source_excerpt_verification_not_semantic_support", "declarations_not_authenticated_approval",
                    "no_live_model_or_data_execution", "no_engineering_qualification"],
            **({"assessment": assessment} if assessment else {}),
            **({"generation_controls": generation_controls} if generation_controls is not None else {}),
            **({"original_blockers": original_blockers, "execution_qualification": qualification_receipt}
               if qualification_receipt is not None else {})}


def _publication_args(reference):
    return {"manifest": reference["manifest_path"], "expected_root": reference["root_hash"]}


def prepare_trial(output, request, *, methods, workspace, planner=None, purpose="trial_execution", now=None):
    """Seal a reviewable local packet, including blocked status and exact invocation."""
    request = validate_trial_request(request)
    readiness = inspect_readiness(request, methods=methods, workspace=workspace, planner=planner, purpose=purpose, now=now)
    output = _absolute(output)
    if not output.resolve().is_relative_to(_absolute(workspace).resolve()):
        raise ValueError("Trial output must be inside the assigned workspace")
    protected = [request["question"]["source"]["path"], request["publication"]["manifest_path"], methods.root]
    output = require_disjoint_output(output, protected)
    record = {"format": "devbench.rna-trial-preparation/3" if request["format"] == FORMAT_V3 else
              "devbench.rna-trial-preparation/2" if request["format"] in ASSESSMENT_FORMATS else "devbench.rna-trial-preparation/1", "request": request, "readiness": readiness,
              "invocation": [str(Path(sys.executable).absolute()), "-I", "-B", "-m", "scripts.run_rna_scientific_trial", "--validate", "--workspace", str(workspace),
                             "--method-workspace", str(methods.root), "--manifest", str(output / "audit/manifest.json")],
              "scope": SCOPE}
    receipt = _seal(output, "trial-preparation.json", record, "analysis.rna_trial_readiness", scope=SCOPE)
    record["invocation"].extend(["--expected-root", receipt["root_hash"]])
    return {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"],
            "readiness": readiness, "invocation": record["invocation"]}


def load_trial(manifest, expected_root, *, methods, workspace, planner=None, now=None):
    _ref_inside({"manifest_path": manifest, "root_hash": expected_root}, workspace)
    _, record, _ = _load(_absolute(manifest), expected_root, "trial-preparation.json")
    readiness = inspect_readiness(record["request"], methods=methods, workspace=workspace, planner=planner,
                                  purpose=record["readiness"]["purpose"], now=now)
    if readiness != record["readiness"]:
        raise ValueError("Trial readiness changed; retain prior evidence and prepare a fresh packet")
    return record


def _admit(request, readiness, authority, purpose):
    if readiness["local_status"] != "locally_consistent":
        raise PermissionError("Trial admission blocked: " + "; ".join(readiness["blockers"]))
    if authority is None:
        raise PermissionError("No trusted engineering/approval authority; no dispatch")
    if request["engineering"]["route"] == "native":
        raise PermissionError("Native B4 qualification unavailable; native admission remains disabled")
    if request["format"] in ASSESSMENT_FORMATS and not isinstance(authority, AuthenticatedTrialAuthority):
        raise PermissionError("Version 2 admission requires the reviewed injected authenticated authority")
    authority.authorize(deepcopy(request), purpose=purpose, binding_sha256=readiness["binding_sha256"])
    authority.consume(deepcopy(request), purpose=purpose, binding_sha256=readiness["binding_sha256"])


class GroundedSelector:
    """Require verbatim method citations before accepting publication-derived choices.

    This is evidence anchoring, not independent semantic validation. The original
    envelope is retained in selection evidence. Evaluator-held outcomes and file
    paths are never added to participant context.
    """
    def __init__(self, selector, context, *, methods=None, dispatch_guard=None):
        self.selector, self.context, self.methods = selector, context, methods
        self.dispatch_guard = dispatch_guard

    def configuration(self):
        return selector_configuration(self.selector)

    def prepare_context(self, context):
        projected = deepcopy(context)
        if "publication_evidence" in projected and projected["publication_evidence"] != self.context:
            raise ValueError("Frozen publication projection differs from the trusted grounding context")
        projected["publication_evidence"] = deepcopy(self.context)
        return projected

    def select(self, context, output, **options):
        projected = self.prepare_context(context)
        if self.dispatch_guard is not None:
            options = self.dispatch_guard(deepcopy(projected), deepcopy(options))
        result = self.selector.select(projected, output, **options)
        reference = None
        if self.methods is not None:
            from .method_planner_contract import _copy_json
            from devharness_core.provenance import canonical_json
            # This is the canonical structured response, not unavailable raw
            # transport bytes. Retain failures through the existing artifact
            # authority before scientific/citation acceptance.
            body = canonical_json(_copy_json(result.action))
            if len(body) > 256_000:
                raise ValueError("Publication response exceeded its retention bound; response not retained")
            path = _absolute(output) / "publication-grounding-response.json"
            with path.open("xb") as handle:
                handle.write(body)
            retained = self.methods.import_artifact(path, artifact_type="json", actor="publication-grounding-capture")
            reference = {key: retained[key] for key in ("artifact_id", "sha256", "size_bytes", "artifact_type")}
        try:
            return self._validate_selection(result, context, reference)
        except (ValueError, KeyError, TypeError) as error:
            suffix = "; retained_response_artifact=" + reference["artifact_id"] if reference else ""
            raise ValueError(str(error)[:1000] + suffix) from error

    def _validate_selection(self, result, context, reference):
        envelope = result.action
        if type(envelope) is not dict or set(envelope) != {"answer", "citations"} or type(envelope["citations"]) is not list:
            raise ValueError("Workflow selector requires answer and exact publication citations")
        if len(envelope["citations"]) > 64:
            raise ValueError("Publication citation bound exceeded")
        excerpts = {item["excerpt_id"]: item for item in self.context["excerpts"] if item["purpose"] == "methods"}
        cited = set()
        for item in envelope["citations"]:
            if type(item) is not dict or set(item) != {"candidate", "excerpt_id", "quote"} or item["excerpt_id"] not in excerpts:
                raise ValueError("Workflow citation uses absent or evaluator-held method excerpt")
            quote = item["quote"]
            if type(quote) is not str or not quote.strip() or quote not in excerpts[item["excerpt_id"]]["text"]:
                raise ValueError("Workflow citation is not a verified verbatim excerpt")
            cited.add(item["candidate"])
        answer = envelope["answer"]
        if context["decision_kind"] == "method_scientific_planning":
            names = {item["name"] for item in answer.get("candidates", [])}
            if cited - names:
                raise ValueError("Publication citation names an unknown candidate")
            from .rna_publication_evidence import _identity
            publication = _identity(self.context["publication"])
            aliases = {publication.get("doi"), "doi:" + publication["doi"] if publication.get("doi") else None,
                       "https://doi.org/" + publication["doi"] if publication.get("doi") else None,
                       "PMID:" + publication["pmid"] if publication.get("pmid") else None,
                       publication.get("pmcid")}
            for candidate in answer.get("candidates", []):
                if candidate["source"]["type"] == "primary_paper":
                    if candidate["source"]["reference"] not in aliases or candidate["name"] not in cited:
                        raise ValueError("Publication-derived candidate lacks matched publication/excerpt grounding")
                if any(parameter["origin"] == "literature_default" for parameter in candidate["parameter_candidates"]) and candidate["name"] not in cited:
                    raise ValueError("Literature parameter needs a verified method excerpt")
        return Selection(answer, {**result.evidence, "publication_grounding": deepcopy(envelope["citations"]),
                                  "grounding_scope": "exact_quote_not_semantic_verification", "publication_response_ref": reference})


def generate_question_workflow(request, *, planner, workspace, selector, branch_id, version_ids, actor,
                               authority=None, backend=None, allow_model_execution=False, now=None, output=None, trace_observer=None, scientific_workflow=None):
    """Observe actual generation when requested; retain the default local path."""
    operation = lambda: _generate_question_workflow(request, planner=planner, workspace=workspace, selector=selector,
        branch_id=branch_id, version_ids=version_ids, actor=actor, authority=authority, backend=backend,
        allow_model_execution=allow_model_execution, now=now, output=output, trace_observer=trace_observer,
        scientific_workflow=scientific_workflow)
    if trace_observer is None:
        return operation()
    from .rna_trace_receipts import observe_boundary
    request = validate_trial_request(request)
    return observe_boundary(trace_observer, request["trial_id"], "generate", operation,
        binding_digest=trial_binding(request, "workflow_generation"))


def _generate_question_workflow(request, *, planner, workspace, selector, branch_id, version_ids, actor,
                                authority=None, backend=None, allow_model_execution=False, now=None, output=None, trace_observer=None, scientific_workflow=None):
    """Alternatives → capabilities → proposed governed declarations; no data run.

    Reuses the registered planner's charging, scientific prerequisites and trial
    declarations. Live selectors additionally require its actual campaign
    binding; offline fakes establish only orchestration software behavior.
    """
    from .rna_publication_evidence import participant_publication_context
    request = validate_trial_request(request)
    if scientific_workflow is None:
        raise PermissionError("RNA actions require a trusted scientific workflow runtime for discovery, implementation and independent reviews")
    if request["format"] in ASSESSMENT_FORMATS and actor != request["roles"]["participant"]:
        raise PermissionError("Version 2 workflow generation actor must match its authenticated participant role")
    if request["workflow"] is not None:
        raise ValueError("Workflow generation cannot replace a frozen workflow")
    if request["format"] in ASSESSMENT_FORMATS:
        if output is None:
            raise ValueError("Version 2 workflow generation requires an immutable output destination")
        output = _absolute(output)
        if not output.is_relative_to(_absolute(workspace)) or output.exists():
            raise ValueError("Version 2 workflow output must be fresh and inside the assigned workspace")
        output = require_disjoint_output(output, [planner.methods.root, request["question"]["source"]["path"],
            _absolute(request["assessment_intent"]["manifest_path"]).parent.parent,
            _absolute(request["publication"]["manifest_path"]).parent.parent])
    live = selector_configuration(selector).get("backend") != "recorded_fixture"
    signedin = request["format"] == FORMAT_V3
    runtime_context = {"planner": planner, "selector": selector, "actor": actor,
        **({"backend": backend} if live else {})}
    readiness_options = {"runtime_context": runtime_context} if signedin else {}
    readiness = inspect_readiness(request, methods=planner.methods, workspace=workspace,
                                  purpose="workflow_generation", now=now, **readiness_options)
    if live:
        if allow_model_execution is not True or backend is None:
            raise PermissionError("Separate model permission and qualified exact backend are required")
        if signedin:
            from .codex_signedin import SignedInCodexActionSelector, SignedInCodexInvestigationBackend
            if (type(backend) is not SignedInCodexInvestigationBackend or type(selector) is not SignedInCodexActionSelector
                    or backend.selector is not selector or backend.ledger is None
                    or request["engineering"]["route"] != "codex_chatgpt"
                    or type(authority) is not AuthenticatedTrialAuthority or authority.signedin_backend is not backend):
                raise PermissionError("Live question-workflow requires the exact registered signed-in campaign runtime")
        else:
            from .rna_mediated_backend import MediatedInvestigationBackend, MediatedActionSelector
            if (type(backend) is not MediatedInvestigationBackend or type(selector) is not MediatedActionSelector
                    or backend.selector is not selector or backend.ledger is None
                    or request["engineering"]["route"] != "mediated"
                    or type(authority) is not AuthenticatedTrialAuthority or authority.mediated_backend is not backend):
                raise PermissionError("Live question-workflow requires the exact accepted mediated campaign runtime")
        capability = backend.require_admission("generation_jobs" if signedin else "hard_total_tokens")
        if capability["backend_digest"] != request["engineering"]["backend_digest"] or capability["runtime_digest"] != request["engineering"]["runtime_digest"]:
            raise PermissionError("Actual model backend/runtime identity differs")
    elif request["engineering"]["route"] != "offline_fixture":
        raise PermissionError("Recorded selector requires an explicitly offline fixture scope")
    if request["format"] in ASSESSMENT_FORMATS and isinstance(authority, AuthenticatedTrialAuthority):
        authority.bind_runtime_context(runtime_context)
    from .rna_trace_receipts import observe_boundary
    observe_boundary(trace_observer, request["trial_id"], "permission_preflight",
        lambda: _admit(request, readiness, authority, "workflow_generation"),
        binding_digest=readiness["binding_sha256"], admission=True)
    if inspect_readiness(request, methods=planner.methods, workspace=workspace,
                         purpose="workflow_generation", now=now, **readiness_options) != readiness:
        raise PermissionError("Trial binding drift after generation admission")
    context = participant_publication_context(**_publication_args(request["publication"]), allow_evidence_read=True)
    guard = None
    if request["format"] in ASSESSMENT_FORMATS:
        def guard(projected, options):
            current = inspect_readiness(request, methods=planner.methods, workspace=workspace,
                purpose="workflow_generation", now=authority.clock(), **readiness_options)
            if current != readiness:
                raise PermissionError("Trial intent or actual input identity drift before generation dispatch")
            generation = request["allocation"]["generation"] if signedin else request["allocation"]
            seconds = min(options["timeout_seconds"], generation["seconds"] / (3 if signedin else generation["requests"]))
            if not live:
                authority.reserve_dispatch(request, purpose="workflow_generation", binding_sha256=readiness["binding_sha256"],
                    dispatch_id="generation:" + projected["decision_kind"] + ":" + digest(projected),
                    allocation={"total_tokens": 0, "requests": 1, "memory_mb": options["memory_mb"],
                        "seconds": seconds, "storage_bytes": 256_000, "max_runs": 0})
            return {**options, "timeout_seconds": seconds}
    grounded = GroundedSelector(selector, context, methods=planner.methods, dispatch_guard=guard)
    identifiers = [request["dataset"]["binding"][key] for key in ("matrix_artifact_id", "metadata_artifact_id")]
    summary = {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": identifiers}
    allocation = request["allocation"]["generation"] if signedin else request["allocation"]
    planner_limits = AgentLimits(max_selections=min(32, allocation["jobs"] if signedin else allocation["requests"]),
        timeout_seconds=allocation["seconds"], session_timeout_seconds=min(300, allocation["seconds"]),
        **({"max_context_bytes": allocation["context_bytes"]} if signedin else
           {"memory_mb": min(4096, allocation["memory_mb"])}))
    state = planner.create(request["task"], summary, actor=actor, data_classification=request["dataset"]["classification"],
        limits=planner_limits,
        evaluation_purpose="method_selection" if request["objective"]["mode"] == "independent_method_selection" else "execution_verification",
        user_parameter_requirements=request["requirements"], rna_input_contract=request["dataset"]["binding"],
        **({"rna_execution_inputs_ref": request["execution_inputs_ref"], "rna_execution_workspace": workspace}
           if "execution_inputs_ref" in request else {}))
    session = state["session_id"]
    if live:
        backend.bind_trial_authority(authority, request, purpose="workflow_generation")
        backend.trace_observer = trace_observer
        allocations = backend.generation_allocations({"session_timeout_seconds": min(300, allocation["seconds"]),
            "max_context_bytes": state["limits"]["max_context_bytes"], "memory_mb": state["limits"]["memory_mb"]})
        campaign_id = "trial:" + readiness["binding_sha256"]
        backend.ledger.open_campaign(campaign_id, session_id=session, policy=backend.policy, capability=capability,
            identity={"source_digest": capability["source_digest"], "runtime_digest": capability["runtime_digest"],
                "expected_plan_hash": digest(state), "parent_call_limits": allocations,
                "protected_parent_allocations": allocations,
                "trial_binding_sha256": readiness["binding_sha256"], "trial_purpose": "workflow_generation",
                "trial_id": request["trial_id"]}, backend=backend)
        planner.attach_model_campaign(session, backend.ledger, campaign_id, backend,
            expected_plan_hash=digest(state), actor=actor)
    from .workflow_actions import WorkflowActions, create_rna_workflow
    from .workflow_operations import WorkflowOperationLedger
    actions = WorkflowActions(planner, WorkflowOperationLedger(planner.methods.root / LEDGER_NAME),
        actor=actor, selector=grounded, version_ids=version_ids, allow_model_execution=live,
        scientific_workflow=scientific_workflow)
    with create_rna_workflow(actions=actions, checkpoint_path=planner.methods.root / CHECKPOINT_NAME) as workflow:
        paused = workflow.start(actions.create_state(session))
        if "__interrupt__" not in paused:
            raise ValueError((paused.get("failure") or {}).get("message")
                             or "Question workflow stopped before its required planning review pause")
        drafts = actions._drafts(planner.get(session))
        if not drafts:
            raise ValueError("No executable trial was proposed; abstain and retain planner evidence")
        numeric_allocation = request["allocation"]["numeric"] if signedin else allocation
        if numeric_allocation is not None and (sum(len(item["candidates"]) for item in drafts) > numeric_allocation["max_runs"]
                or sum(item["resource_limits"]["timeout_seconds"] * len(item["candidates"]) for item in drafts) > numeric_allocation["seconds"]
                or any(item["resource_limits"]["memory_mb"] > numeric_allocation["memory_mb"] for item in drafts)):
            raise PermissionError("Generated workflow exceeds accepted trial allocation")
        # The authenticated generation request authorizes these governed
        # proposals on its exact branch. This grants no scientific acceptance
        # and no numeric execution authority.
        actions.declarations = [{"draft_id": item["id"], "branch_id": branch_id} for item in drafts]
        declared = workflow.resume(session)
        if "__interrupt__" not in declared:
            raise ValueError((declared.get("failure") or {}).get("message")
                             or "Question workflow must stop at the separate execution authority gate")
        declarations = planner.get(session)["experiments"]
    descriptor = {"planner_session_id": session, "experiment_ids": [item["id"] for item in declarations]}
    snapshot = workflow_snapshot(planner, descriptor, workspace=workspace)
    descriptor["sha256"] = digest(snapshot)
    if request["format"] in ASSESSMENT_FORMATS:
        wrapper = {"format": "devbench.rna-assessment-workflow/2", "assessment_intent_ref": request["assessment_intent"],
            "assessment_rules_sha256": readiness["assessment"]["assessment_rules_sha256"],
            "governed_workflow": snapshot, "governed_workflow_sha256": descriptor["sha256"]}
        _seal(output, "assessment-workflow.json", wrapper, "analysis.assessment_workflow", scope=SCOPE)
        path = output / "assessment-workflow.json"
        from devbench.study.evidence import _read
        body = _read(path, 1_000_000)
        import hashlib
        descriptor["workflow_ref"] = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
        path.chmod(0o444)
    return descriptor


def execute_trial(request, *, planner, workspace, actor, authority=None, executor=None, now=None, trace_observer=None, scientific_workflow=None):
    """Observe the actual trial, including refusal, without granting execution."""
    operation = lambda: _execute_trial(request, planner=planner, workspace=workspace, actor=actor,
        authority=authority, executor=executor, now=now, trace_observer=trace_observer, scientific_workflow=scientific_workflow)
    if trace_observer is None:
        return operation()
    from .rna_trace_receipts import observe_boundary
    request = validate_trial_request(request)
    return observe_boundary(trace_observer, request["trial_id"], "run", operation,
        binding_digest=trial_binding(request, "trial_execution"), role="numerical", finalize_root=True)


def _execute_trial(request, *, planner, workspace, actor, authority=None, executor=None, now=None, trace_observer=None, scientific_workflow=None):
    """Dispatch only governed exact trials after separate one-use execution admission.

    No CLI exposes this API. A trusted authority/executor pair must exist; callers
    cannot turn local preparation into native/provider qualification.
    """
    request = validate_trial_request(request)
    if scientific_workflow is None:
        raise PermissionError("RNA actions require a trusted scientific workflow runtime for discovery, implementation and independent reviews")
    if request["format"] in ASSESSMENT_FORMATS and actor != request["roles"]["participant"]:
        raise PermissionError("Version 2 execution actor must match its authenticated participant role")
    if request["format"] == FORMAT_V3 and request["allocation"]["numeric"] is None:
        raise PermissionError("Separate explicit numeric allocation and execution approval are required")
    mediated = request["engineering"]["route"] == "mediated" and type(authority) is AuthenticatedTrialAuthority
    signedin = request["engineering"]["route"] == "codex_chatgpt" and type(authority) is AuthenticatedTrialAuthority
    runtime_context = {"planner": planner, "executor": executor, "actor": actor,
        "scientific_context": deepcopy(request.get("scientific_context")),
        **({"backend": authority.mediated_backend} if mediated else
           {"backend": authority.signedin_backend} if signedin else {})}
    readiness_options = {"execution_qualification": authority.execution_qualification,
        "runtime_context": runtime_context} if mediated or signedin else {}
    readiness = inspect_readiness(request, methods=planner.methods, workspace=workspace, planner=planner,
        now=authority.clock() if mediated or signedin else now, **readiness_options)
    snapshot = workflow_snapshot(planner, request["workflow"], workspace=workspace) if request["workflow"] else None
    if snapshot and any(item["status"] != "declared" for item in planner.get(request["workflow"]["planner_session_id"])["experiments"]
                        if item["id"] in request["workflow"]["experiment_ids"]):
        raise PermissionError("Exact trial already attempted; no replay dispatch")
    if request["format"] in ASSESSMENT_FORMATS and isinstance(authority, AuthenticatedTrialAuthority):
        authority.bind_runtime_context(runtime_context)
    from .rna_trace_receipts import observe_boundary
    observe_boundary(trace_observer, request["trial_id"], "permission_preflight",
        lambda: _admit(request, readiness, authority, "trial_execution"),
        binding_digest=readiness["binding_sha256"], admission=True)
    results = []
    guard = None
    if request["format"] in ASSESSMENT_FORMATS:
        def guard(descriptor):
            current = inspect_readiness(request, methods=planner.methods, workspace=workspace, planner=planner,
                now=authority.clock(), **readiness_options)
            if current != readiness:
                raise PermissionError("Trial binding, plan, input lineage or approval drift before candidate dispatch")
            contract = planner.methods.method(descriptor["method_version_id"])["contract"]
            from .rna_publication_profiles import load_assessment_plan
            from .rna_execution_inputs import load_execution_inputs, validate_execution_method_binding
            bound_plan = load_assessment_plan(request["assessment_plan"], workspace=workspace, service=planner.methods)
            bound_inputs = load_execution_inputs(bound_plan["execution_inputs_ref"], artifact_service=planner.methods,
                dataset_binding=request["dataset"]["binding"], workspace=workspace)
            validate_execution_method_binding(bound_inputs, descriptor["method_version_id"], descriptor["parameters"], artifact_service=planner.methods)
            authority.reserve_dispatch(request, purpose="trial_execution", binding_sha256=readiness["binding_sha256"],
                dispatch_id="candidate:" + digest([descriptor["session_id"], descriptor["experiment_id"], descriptor["candidate"]]),
                allocation={"kind": "numeric_execution", "numeric": {"memory_mb": contract["execution"]["memory_mb"],
                    "seconds": contract["execution"]["timeout_seconds"], "storage_bytes": contract["execution"]["max_output_bytes"], "max_runs": 1}}
                if request["format"] == FORMAT_V3 else {"total_tokens": 0, "requests": 0, "memory_mb": contract["execution"]["memory_mb"],
                    "seconds": contract["execution"]["timeout_seconds"], "storage_bytes": contract["execution"]["max_output_bytes"], "max_runs": 1})
    for identity in request["workflow"]["experiment_ids"]:
        # Recheck actual registered bytes/dependencies after admission, before dispatch.
        current = inspect_readiness(request, methods=planner.methods, workspace=workspace, planner=planner,
            now=authority.clock() if mediated or signedin else now, **readiness_options)
        if current != readiness:
            raise PermissionError("Trial binding drift after admission")
        execution_bindings = None
        if guard:
            from .rna_comparison_outputs import assessment_run_binding
            import uuid
            experiment = next(item for item in snapshot["experiments"] if item["id"] == identity)
            execution_bindings = {name: assessment_run_binding(request["assessment_plan"], artifact_service=planner.methods,
                **request["scientific_context"], controller_id=uuid.uuid4().hex, step_id=experiment["step_ids"][name])
                for name in experiment["candidates"]}
        results.extend(workflow_action(planner, request["workflow"]["planner_session_id"], "run", experiment_id=identity, actor=actor,
                                         allow_data_execution=True, executor=executor, scientific_workflow=scientific_workflow,
                                         **({"before_dispatch": guard, "execution_bindings": execution_bindings} if guard else {})))
        if any(row["status"] in {"failed", "resource_limit", "interrupted"} for row in results):
            break  # Retain the failed attempt and leave every later declaration unstarted.
    receipt = {"format": "devbench.rna-trial-execution/3" if request["format"] == FORMAT_V3 else
            "devbench.rna-trial-execution/2" if request["format"] in ASSESSMENT_FORMATS else "devbench.rna-trial-execution/1", "trial_id": request["trial_id"],
            "workflow_sha256": request["workflow"]["sha256"], "results": results, "outputs_sha256": digest(results),
            "status": "completed_with_failures" if any(row["status"] != "success" for row in results) else "execution_completed",
            "comparison": "not_performed", "scientific_review": "not_performed", "clinical_release": "held"}
    if request["format"] in ASSESSMENT_FORMATS:
        receipt["assessment_plan_ref"] = request["assessment_plan"]
        receipt["output_bundle_ref"] = None
        if any("trial_admission_denied_no_dispatch" in row["warnings"] for row in results):
            receipt.update(status="admission_denied", comparison="not_assessable_missing_complete_execution_evidence")
        else:
            from .rna_comparison_outputs import extract_comparison_outputs
            runs = [row["provenance"]["run_id"] for row in results if row["provenance"].get("run_id")]
            if runs and len(runs) == len(results):
                bundle = extract_comparison_outputs(runs, request["assessment_plan"], artifact_service=planner.methods)
                receipt["output_bundle_ref"] = bundle
                from .rna_publication_comparison import compare_assessment_outputs
                import uuid
                assessment = observe_boundary(trace_observer, request["trial_id"], "verify",
                    lambda: compare_assessment_outputs(**_publication_args(request["comparison"]), output_bundle_ref=bundle["record_ref"],
                        workspace=workspace, artifact_service=planner.methods, output=planner.methods.root / "trial-assessments" / uuid.uuid4().hex,
                        allow_evidence_read=True), binding_digest=readiness["binding_sha256"])
                receipt["comparison"] = {key: assessment[key] for key in ("manifest_path", "root_hash")}
                receipt["assessment_report_ref"] = assessment["report"]
            else:
                receipt["comparison"] = "not_assessable_missing_governed_run_receipts"
    return receipt
