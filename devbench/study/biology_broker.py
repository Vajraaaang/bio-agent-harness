"""Observed-input biology tools using the coordinator's existing receipt boundary.

Neither method dispatch nor prerequisite decisions receive a case ID, private
target scope, seed, or reference answer. Numerical method gaps stay visible;
registration does not claim that a method solves or qualifies every target.
"""
from pathlib import Path

from .broker import ToolBroker
from .contracts import canonical_json_bytes, content_sha256
from .evidence import VerifiedReceipts
from .biology_input_limits import MINIMUM_BIOLOGICAL_UNITS

COMMON_CHECKS = ("audit_expression_quality", "validate_biological_units")
AUDIT_TOOLS = frozenset((*COMMON_CHECKS, "validate_design_matrix", "pseudobulk_membership_audit",
                         "perturbation_design_audit", "regulatory_evidence_audit", "lineage_history_analysis"))
PREREQUISITES = {
    "fit_temporal_trajectory": ("validate_design_matrix",),
    "temporal_event_analysis": ("validate_design_matrix",),
    "condition_time_analysis": ("validate_design_matrix",),
    "mean_condition_analysis": ("validate_design_matrix",),
    "covariate_response_analysis": ("validate_design_matrix",),
    "cell_type_state_analysis": ("pseudobulk_membership_audit",),
    "perturbation_response_analysis": ("perturbation_design_audit",),
}


def biology_tool_menu(public_task):
    """Construct public method metadata without reading data or running a tool."""
    from .biology_tool_io import validate_public_task
    from .biology_tools_bulk import bulk_tool_capabilities
    from .biology_tools_singlecell import singlecell_tool_capabilities
    validate_public_task(public_task)
    question_ids = {item["target_id"] for item in public_task["questions"]}
    context = public_task.get("context", {})
    cellular = public_task["measurement"] in {"umi_counts", "non_umi_counts"} or "cell" in context.get("nested_observation_roles", ())
    bulk, cell = bulk_tool_capabilities(), singlecell_tool_capabilities()
    menu = {name: {**bulk[name], "driver": "bulk"} for name in COMMON_CHECKS}
    source = cell if cellular else bulk
    for name, details in source.items():
        if question_ids & set(details["target_ids"]) or name in COMMON_CHECKS:
            menu[name] = {**details, "driver": "singlecell" if cellular else "bulk"}
    for name in tuple(menu):
        for prerequisite in PREREQUISITES.get(name, ()):
            if prerequisite in bulk:
                menu[prerequisite] = {**bulk[prerequisite], "driver": "bulk"}
            elif prerequisite in cell:
                menu[prerequisite] = {**cell[prerequisite], "driver": "singlecell"}
    return menu


def validate_biology_request(tool, arguments, public_task, *, enforced):
    """Check public request shape and protect the assigned question's meaning."""
    if not isinstance(arguments, dict) or set(arguments) - {"options"}:
        raise ValueError("Biology tools accept only method options; input paths and task context are coordinator-bound")
    options = arguments.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("Biology method options must be an object")
    menu = biology_tool_menu(public_task)
    if tool not in menu:
        raise ValueError("Method is not registered for the public task")
    for name in ("minimum_units", "minimum_donors", "min_donors"):
        if name in options and (type(options[name]) is not int or options[name] < MINIMUM_BIOLOGICAL_UNITS):
            raise ValueError("All harness conditions share the minimum biological-unit threshold")
    if not enforced:
        return options
    roles = public_task.get("analysis_columns")
    if not isinstance(roles, dict):
        raise ValueError("Enforced analysis requires coordinator-bound column roles")
    for option, role in {"condition_col": "condition", "time_col": "time", "dose_col": "dose",
                         "covariate_col": "covariate", "donor_col": "population_unit",
                         "unit_col": "population_unit"}.items():
        if option in options and options[option] != roles.get(role):
            raise ValueError("Method option changes the assigned column role: " + option)
    if "batch_cols" in options and list(options["batch_cols"]) != roles.get("batches", []):
        raise ValueError("Required batch terms cannot be removed or changed")
    context = public_task.get("context", {})
    design = context.get("design", {})
    levels = design.get("condition_levels", context.get("condition_labels", []))
    explicit = context.get("requested_condition_contrast", {})
    expected = {"reference_condition": explicit.get("denominator", levels[0] if levels else None),
                "comparison_condition": explicit.get("numerator", levels[1] if len(levels) > 1 else None)}
    for name, value in expected.items():
        if name in options and options[name] != value:
            raise ValueError("Method option changes the assigned contrast: " + name)
    if "reference_time" in options and options["reference_time"] != public_task.get("analysis_reference_time"):
        raise ValueError("The assigned effect reference time cannot change")
    domain = context.get("rhythm_analysis_domain", {}).get("frequency_cycles_per_time_unit")
    if "frequency_domain" in options and list(options["frequency_domain"]) != list(domain or ()):
        raise ValueError("The prespecified frequency domain cannot change")
    if tool in COMMON_CHECKS and options:
        raise ValueError("Common enforced checks use the fixed public policy")
    return options


def _audit_completed(event, results):
    if event.get("status") != "succeeded":
        return False
    result = results.get(event["receipt_id"], {})
    diagnostics = result.get("diagnostics", {})
    return (isinstance(diagnostics, dict) and not diagnostics.get("analysis_error")
            and diagnostics.get("status") not in {"analysis_failed", "support_unavailable", "failed"})


class BiologyBroker(ToolBroker):
    def __init__(self, policy, input_aliases, output_dir, *, public_task, allow_data_execution=False, defer_start=False):
        if allow_data_execution is not True:
            raise PermissionError("Biological tool execution is disabled")
        from .evidence import decode_json
        self.public_task = decode_json(canonical_json_bytes(public_task))
        if self.public_task.get("input_sha256") != policy.input_sha256:
            raise ValueError("Public task must match the broker's retained input identity")
        self.menu = biology_tool_menu(self.public_task)
        super().__init__(policy, input_aliases, output_dir, allow_data_execution=True, defer_start=defer_start)

    @property
    def allowed_tools(self):
        return frozenset(self.menu)

    def _invoke_tool(self, tool, arguments):
        options = validate_biology_request(tool, arguments, self.public_task,
                                           enforced=self.policy.condition == "enforced")
        if self.policy.condition == "enforced" and tool not in AUDIT_TOOLS:
            for check in (*COMMON_CHECKS, *PREREQUISITES.get(tool, ())):
                matches = [event for event in self.events if event["tool"] == check
                           and _audit_completed(event, self.results)
                           and (check in COMMON_CHECKS or event["arguments"].get("options", {}) == options)]
                if not matches:
                    raise ValueError("Matching prerequisite was not executed successfully: " + check)
        if self.menu[tool]["driver"] == "singlecell":
            from .biology_tools_singlecell import analyze_singlecell_tool as analyze
        else:
            from .biology_tools_bulk import analyze_bulk_tool as analyze
        paths = {}
        for key in ("expression_path", "metadata_path", "public_annotations_path"):
            public = str(Path(self.public_task[key]).resolve())
            if public not in self.aliases:
                raise ValueError("Task asset has no protected input alias")
            paths[key] = str(self.aliases[public])
        return analyze(tool, self.public_task, counts_path=paths["expression_path"],
                       metadata_path=paths["metadata_path"], annotations_path=paths["public_annotations_path"],
                       options=options, allow_data_execution=True)


def verify_biology_workflow(answer, evidence, public_task, *, allow_evidence_read=False):
    """Check workflow and exact typed numerical outputs; do not grade truth."""
    if allow_evidence_read is not True:
        raise PermissionError("Biological answer evidence reading is disabled")
    from .biology_answers import BiologyAnswer, TargetAnswer
    parsed = BiologyAnswer.model_validate_json(canonical_json_bytes(
        answer.model_dump(mode="json") if isinstance(answer, BiologyAnswer) else answer))
    if not isinstance(evidence, VerifiedReceipts):
        raise TypeError("Coordinator-verified tool receipts are required")
    errors = []
    if (parsed.task_id != public_task["task_id"] or parsed.input_sha256 != evidence.input_sha256
            or evidence.input_sha256 != public_task["input_sha256"]
            or parsed.gene_universe_sha256 != public_task["gene_universe_sha256"]):
        errors.append("answer_task_or_input_identity_mismatch")
    expected = {row["target_id"] for row in public_task["questions"]}
    if {row.target_id for row in parsed.target_results} != expected:
        errors.append("requested_targets_incomplete")
    events = {row["receipt_id"]: row for row in evidence.events}
    for check in COMMON_CHECKS:
        if not any(row["tool"] == check and _audit_completed(row, evidence.results) for row in evidence.events):
            errors.append("missing_common_check:" + check)
    matched = {}
    for target in parsed.target_results:
        if target.claim_type == "failed":
            errors.append("failed_target_not_accepted:" + target.target_id)
        wanted = target.model_dump(mode="json", exclude={"tool_evidence_ids"})
        candidate_events = []
        for receipt_id in target.tool_evidence_ids:
            event = events.get(receipt_id)
            if event is None:
                errors.append("unknown_evidence_receipt:" + target.target_id)
                continue
            if event["status"] != "succeeded":
                continue
            for value in evidence.results.get(receipt_id, {}).get("target_results", []):
                recorded = TargetAnswer.model_validate_json(canonical_json_bytes(value))
                if recorded.target_id == target.target_id and recorded.model_dump(mode="json", exclude={"tool_evidence_ids"}) == wanted:
                    candidate_events.append(event)
        if not candidate_events:
            errors.append("target_has_no_matching_recorded_result:" + target.target_id)
            continue
        event = candidate_events[-1]
        options = event["arguments"].get("options", {})
        validate_biology_request(event["tool"], event["arguments"], public_task, enforced=True)
        checks = () if event["tool"] in AUDIT_TOOLS else (*COMMON_CHECKS, *PREREQUISITES.get(event["tool"], ()))
        for check in checks:
            if not any(row["tool"] == check and row["sequence"] < event["sequence"]
                       and _audit_completed(row, evidence.results)
                       and (check in COMMON_CHECKS or row["arguments"].get("options", {}) == options)
                       for row in evidence.events):
                errors.append("missing_matching_prior_check:" + target.target_id + ":" + check)
        matched[target.target_id] = event["receipt_id"]
    return {"workflow_passed": not errors, "errors": sorted(set(errors)), "matched_targets": matched,
            "scope": "structured_claim_execution_binding", "scientific_correctness": "graded_separately",
            "narrative_status": "human_review_only", "clinical_qualification": False}
