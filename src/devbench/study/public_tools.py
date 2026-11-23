"""Public observed-data tools. No private reference or generator imports."""
from pathlib import Path

from .broker import ToolBroker
from .contracts import canonical_json_bytes
from .evidence import VerifiedReceipts, decode_json
from .public_answers import PublicAnswer
from .public_io import load_public_inputs

AUDIT = "audit_public_inputs"
SUMMARY = "summarize_developmental_observations"
NORMALIZE = "normalize_seqc_libraries"


def public_tool_menu(task):
    if task.get("kind") not in {"developmental_observation", "seqc_normalization"}:
        raise ValueError("Unknown public task")
    analysis = SUMMARY if task["kind"] == "developmental_observation" else NORMALIZE
    return {
        AUDIT: {"description": "Check measurement, axes, biological units and limits", "options": {}},
        analysis: {"description": "Observed library means" if analysis == SUMMARY else "Relative library factors; counts are divided by factors",
                   "options": {} if analysis == SUMMARY else {"method": ["calibration", "rle", "library_total"]}},
    }


def analyze_public_tool(tool, input_root, *, options=None, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Public numerical tools are disabled")
    task, values, rows, calibration, input_hash = load_public_inputs(input_root, allow_data_execution=True)
    if tool not in public_tool_menu(task):
        raise ValueError("Public tool is unavailable for this task")
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - ({"method"} if tool == NORMALIZE else set()):
        raise ValueError("Unknown public method option")
    import numpy as np
    if task["kind"] == "developmental_observation":
        stages = task.get("stage_labels")
        if (not isinstance(stages, list) or not 3 <= len(stages) <= 100 or len(set(stages)) != len(stages)
                or {row.get("stage") for row in rows} != set(stages)
                or any(row.get("independent_animal_id") for row in rows)):
            raise ValueError("Developmental stages or unverified animal identities violate the task scope")
        limitations = ["Observed library summaries only; independent animal identities are unverified.",
                      "Collection ages are not ground truth for inferred pseudotime; no population inference."]
    else:
        if (set(row.get("mixture") for row in rows) != set("ABCD")
                or any(row.get("sample_id") != row.get("library_id") or row.get("replicate_kind") != "technical"
                       or row.get("independent_patient_id") for row in rows)):
            raise ValueError("SEQC units must remain A-D technical libraries with no patient identity")
        limitations = ["Technical libraries and their lanes are not independent patients.",
                      "Nominal ERCC inputs do not establish every endogenous gene's truth or equal mRNA content.",
                      "Recovering held-out ERCC ratios does not validate normalization of endogenous RNA; control-specific assay bias may remain."]
    result = {"input_sha256": input_hash, "task_id": task["task_id"], "status": "completed", "limitations": limitations}
    if tool == AUDIT:
        result.update(measurement=task["measurement"], genes=len(task["gene_ids"]), libraries=len(rows),
                      population_inference_allowed=False, complete_biological_truth=False)
    elif tool == SUMMARY:
        means = np.column_stack([values[:, [i for i, row in enumerate(rows) if row["stage"] == stage]].mean(axis=1)
                                 for stage in stages])
        result["payload"] = {"kind": "developmental_observation", "stage_labels": stages,
                             "curves": [{"gene_id": gene, "means": means[i].tolist()} for i, gene in enumerate(task["gene_ids"])]}
    else:
        method = options.get("method", "calibration")
        if method == "library_total":
            factors = values.sum(axis=0, dtype=float)
            limitations.append("Library-total factors use the selected panel and can be biased by composition.")
        elif method == "rle":
            positive = values[(values > 0).all(axis=1)].astype(float)
            if len(positive) < 2:
                raise ValueError("RLE requires at least two all-positive features in this bounded panel")
            factors = np.median(positive / np.exp(np.log(positive).mean(axis=1))[:, None], axis=0)
            limitations.append("RLE assumes a stable majority/reference distribution in the selected panel.")
        elif method == "calibration":
            amounts = calibration.get("expected_inputs", {})
            if not isinstance(amounts, dict) or len(amounts) < 2 or not set(amounts) <= set(task["gene_ids"]):
                raise ValueError("At least two public calibration features are required")
            gene_index = {gene: i for i, gene in enumerate(task["gene_ids"])}
            adjusted = []
            for gene, expected in sorted(amounts.items()):
                if not isinstance(expected, dict) or set(expected) != set(task["sample_ids"]):
                    raise ValueError("Calibration amounts must align with every library")
                nominal = np.asarray([expected[sample] for sample in task["sample_ids"]], dtype=float)
                if not np.isfinite(nominal).all() or np.any(nominal <= 0):
                    raise ValueError("Calibration amounts must be finite and positive")
                counts = values[gene_index[gene]].astype(float)
                if np.all(counts > 0):
                    relative = np.log(counts) - np.log(nominal)
                    adjusted.append(relative - relative.mean())
            if len(adjusted) < 2:
                raise ValueError("Fewer than two all-positive calibration controls; no factors fabricated")
            factors = np.exp(np.median(np.asarray(adjusted), axis=0))
            limitations.append("Calibration assumes stable control-specific recovery across mixtures; only public calibration controls were used.")
        else:
            raise ValueError("Unknown SEQC normalization method")
        if not np.isfinite(factors).all() or np.any(factors <= 0):
            raise ValueError("Normalization produced nonfinite or nonpositive factors")
        factors = factors / np.exp(np.log(factors).mean())
        result["payload"] = {"kind": "seqc_normalization", "method": method,
                             "factors": [{"library_id": sample, "factor": float(factor)}
                                         for sample, factor in zip(task["sample_ids"], factors)]}
    if "payload" in result:
        # Validate the identical answer payload type used for participant acceptance.
        result["payload"] = PublicAnswer.model_validate_json(canonical_json_bytes({
            "task_id": task["task_id"], "input_sha256": input_hash, "payload": result["payload"],
            "limitations": limitations})).payload.model_dump(mode="json")
    return result


class PublicToolBroker(ToolBroker):
    def __init__(self, policy, aliases, output_dir, *, public_task, public_input_root, allow_data_execution=False, defer_start=False):
        if allow_data_execution is not True:
            raise PermissionError("Public tool execution is disabled")
        self.public_task = decode_json(canonical_json_bytes(public_task))
        self.public_input_root = Path(public_input_root)
        from .public_io import input_inventory
        from .contracts import content_sha256
        if (self.public_task.get("input_sha256") != policy.input_sha256
                or content_sha256(input_inventory(self.public_input_root)) != policy.input_sha256):
            raise ValueError("Public task and broker input identities disagree")
        self.menu = public_tool_menu(self.public_task)
        super().__init__(policy, aliases, output_dir, allow_data_execution=True, defer_start=defer_start)

    @property
    def allowed_tools(self):
        return frozenset(self.menu)

    def _check_inputs(self):
        super()._check_inputs()
        from .public_io import input_inventory
        from .contracts import content_sha256
        if content_sha256(input_inventory(self.public_input_root)) != self.policy.input_sha256:
            raise ValueError("Protected public input bundle changed")

    def _invoke_tool(self, tool, arguments):
        if not isinstance(arguments, dict) or set(arguments) - {"options"}:
            raise ValueError("Public tool paths and task identity are coordinator-bound")
        if self.policy.condition == "enforced" and tool != AUDIT:
            if not any(event["tool"] == AUDIT and event["status"] == "succeeded"
                       and self.results[event["receipt_id"]].get("status") == "completed" for event in self.events):
                raise ValueError("A completed public-input audit is required before analysis")
        return analyze_public_tool(tool, self.public_input_root, options=arguments.get("options", {}), allow_data_execution=True)


def verify_public_workflow(answer, evidence, public_task, *, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Public workflow verification is disabled")
    parsed = PublicAnswer.model_validate_json(canonical_json_bytes(
        answer.model_dump(mode="json") if isinstance(answer, PublicAnswer) else answer))
    if not isinstance(evidence, VerifiedReceipts):
        raise TypeError("Coordinator-verified receipts are required")
    errors = []
    if (parsed.task_id != public_task["task_id"] or parsed.input_sha256 != public_task["input_sha256"]
            or evidence.input_sha256 != parsed.input_sha256):
        errors.append("answer_task_or_input_identity_mismatch")
    expected_tool = SUMMARY if parsed.payload.kind == "developmental_observation" else NORMALIZE
    events = {row["receipt_id"]: row for row in evidence.events}
    matching = []
    for receipt in parsed.tool_evidence_ids:
        event = events.get(receipt)
        if event is None:
            errors.append("unknown_evidence_receipt")
            continue
        result = evidence.results.get(receipt, {})
        if (event["tool"] == expected_tool and event["status"] == "succeeded"
                and result.get("payload") == parsed.payload.model_dump(mode="json")
                and result.get("task_id") == parsed.task_id and result.get("input_sha256") == parsed.input_sha256):
            matching.append(event)
    if not matching:
        errors.append("answer_has_no_matching_recorded_result")
    elif not any(event["tool"] == AUDIT and event["status"] == "succeeded"
                 and event["sequence"] < matching[-1]["sequence"]
                 and evidence.results[event["receipt_id"]].get("status") == "completed" for event in evidence.events):
        errors.append("missing_prior_public_input_audit")
    return {"workflow_passed": not errors, "errors": sorted(set(errors)),
            "scope": "structured_execution_binding_only", "narrative_status": "human_review_only"}
