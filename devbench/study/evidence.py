"""Verify coordinator receipts and bind structured claims to recorded results.

Hashes establish consistency against the caller's independently retained receipt.
They do not authenticate an operator or establish scientific correctness. No
expression matrices, graders or model services are accessed by this module.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from .contracts import canonical_json_bytes, content_sha256
from .evidence_keys import evidence_key


def decode_json(content):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate receipt JSON keys")
            result[key] = value
        return result
    value = json.loads(content, object_pairs_hook=unique)
    canonical_json_bytes(value)  # Reject NaN, Infinity and overflowed exponents.
    return value


def _read(path, limit):
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Evidence paths must be absolute and contain no symlinks")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Evidence is not a bounded regular file")
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Evidence exceeded its byte budget while reading")
    return content


def _file_sha256(path, limit):
    """Same bounded-file admission as _read, with at most two 1 MiB chunks live."""
    if not path.is_absolute() or any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Evidence paths must be absolute and contain no symlinks")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    digest, total = hashlib.sha256(), 0
    with os.fdopen(descriptor, "rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("Evidence is not a bounded regular file")
        while chunk := handle.read(min(1_048_576, limit - total + 1)):
            total += len(chunk)
            if total > limit:
                raise ValueError("Evidence exceeded its byte budget while hashing")
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        if (total != before.st_size or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError("Evidence changed while hashing")
    return digest.hexdigest()


@dataclass(frozen=True)
class VerifiedReceipts:
    """Trusted coordinator object; never deserialize one from participant output."""
    input_sha256: str
    implementation_sha256: str
    condition: str
    root_receipt: str | None
    events: tuple[dict, ...]
    results: dict[str, dict]
    executions: tuple[dict, ...] = ()
    execution_scoping: bool = False


def _verify_executions(directory, root):
    manifest = root.get("execution_manifest")
    if (not isinstance(manifest, dict) or manifest.get("file") != "executions.json"
            or type(manifest.get("count")) is not int or not 0 <= manifest["count"] <= 128
            or root.get("execution_scope") != "coordinator_observed_sequential_script_command_windows"
            or root.get("numerical_replay") != "out_of_scope"):
        raise ValueError("Invalid execution manifest contract")
    content = _read(directory / "executions.json", 200_000)
    if hashlib.sha256(content).hexdigest() != manifest.get("sha256"):
        raise ValueError("Execution manifest hash mismatch")
    executions = decode_json(content)
    if not isinstance(executions, list) or len(executions) != manifest["count"]:
        raise ValueError("Execution manifest count mismatch")
    previous, seen = 0, set()
    for entry in executions:
        if not isinstance(entry, dict):
            raise ValueError("Execution record must be an object")
        seq, identifier = entry.get("sequence"), entry.get("execution_id")
        if (type(seq) is not int or not previous < seq <= 128
                or identifier != f"execution-{seq:04d}" or identifier in seen
                or entry.get("start_observed") is not True or entry.get("completed") is not True
                or type(entry.get("direct_script_invocation")) is not bool
                or type(entry.get("succeeded")) is not bool):
            raise ValueError("Invalid execution identity, order or completion")
        code = entry.get("exit_code")
        if (code is not None and type(code) is not int) or entry["succeeded"] != (type(code) is int and code == 0):
            raise ValueError("Execution success differs from the recorded exit code")
        for field in ("command_sha256", "script_sha256_at_completion", "answer_sha256_at_completion"):
            value = entry.get(field)
            if value is None and field != "command_sha256":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Invalid execution artifact hash")
        previous = seq
        seen.add(identifier)
    return tuple(executions)


def verify_receipts(directory, *, expected_receipt, expected_input_sha256,
                    expected_implementation_sha256, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Evidence reading is disabled until explicitly requested")
    directory = Path(directory)
    root = decode_json(_read(directory / "root.json", 100_000))
    if root != expected_receipt:
        raise ValueError("Saved root does not match the independently retained receipt")
    if (root.get("record_format") not in {"devbench.study.tool-receipts/1", "devbench.study.tool-receipts/2"}
            or root.get("input_sha256") != expected_input_sha256
            or root.get("implementation_sha256") != expected_implementation_sha256
            or root.get("condition") not in {"optional", "enforced"}
            or root.get("capture_complete") is not True or root.get("capture_error") is not None):
        raise ValueError("Incomplete capture or mismatched receipt identity")
    scoped = root["record_format"] == "devbench.study.tool-receipts/2"
    executions = _verify_executions(directory, root) if scoped else ()
    execution_order = {entry["execution_id"]: entry["sequence"] for entry in executions}
    previous_execution = 0
    count = root.get("event_count")
    if type(count) is not int or not 0 <= count <= 1000:
        raise ValueError("Invalid tool event count")
    event_path = directory / "events.jsonl"
    content = _read(event_path, 32_000_000) if count or event_path.exists() else b""
    lines = content.splitlines()
    if len(lines) != count or (content and not content.endswith(b"\n")):
        raise ValueError("Tool event stream is incomplete or count mismatched")
    events, results, previous, total_bytes = [], {}, None, len(content)
    for sequence, line in enumerate(lines, start=1):
        event = decode_json(line)
        if not isinstance(event, dict):
            raise ValueError("Tool event must be an object")
        receipt_id = event.get("receipt_id")
        if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt_id):
            raise ValueError("Invalid event receipt identifier")
        unsigned = {key: value for key, value in event.items() if key != "receipt_id"}
        if (content_sha256(unsigned) != receipt_id or event.get("previous_receipt") != previous
                or type(event.get("sequence")) is not int or event["sequence"] != sequence
                or event.get("input_sha256") != expected_input_sha256
                or event.get("implementation_sha256") != expected_implementation_sha256
                or event.get("status") not in {"succeeded", "failed"}):
            raise ValueError("Tool event identity, chain or sequence failed verification")
        if scoped:
            order = execution_order.get(event.get("execution_id"))
            if order is None or order < previous_execution:
                raise ValueError("Tool receipt has an unknown or out-of-order execution")
            previous_execution = order
        references = []
        if event["status"] == "succeeded":
            references.append((receipt_id + ".json", event.get("result_sha256"), 8_000_000, True))
        if "backend_evidence" in event:
            backend = event["backend_evidence"]
            if not isinstance(backend, dict) or backend.get("file") != f"backend-{sequence:04d}.json":
                raise ValueError("Unexpected backend evidence filename")
            references.append((backend["file"], backend.get("sha256"), 32_000_000, False))
        for name, expected_hash, bound, is_result in references:
            payload = _read(directory / name, min(bound, 512_000_000 - total_bytes))
            total_bytes += len(payload)
            if hashlib.sha256(payload).hexdigest() != expected_hash:
                raise ValueError("Referenced tool artifact hash mismatch")
            value = decode_json(payload)
            if is_result:
                if not isinstance(value, dict):
                    raise ValueError("Numerical tool result must be an object")
                results[receipt_id] = value
            elif (not isinstance(value, list) or type(backend.get("calls")) is not int
                  or backend["calls"] != len(value)):
                raise ValueError("Backend call count mismatch")
        events.append(event)
        previous = receipt_id
    if previous != root.get("root_receipt"):
        raise ValueError("Tool event chain does not reach the retained root")
    return VerifiedReceipts(expected_input_sha256, expected_implementation_sha256,
                            root["condition"], previous, tuple(events), results, executions, scoped)


def _same_number(left, right):
    return (type(left) in (float, int) and type(right) in (float, int)
            and math.isfinite(left) and math.isfinite(right)
            and math.isclose(left, right, rel_tol=1e-8, abs_tol=1e-10))


def _descriptive_binding(answer, evidence, task_context, by_id, quality):
    """Bind an observed-subset summary, without implying population inference."""
    errors = []
    analysis = answer.analysis
    event = by_id.get(analysis.tool_analysis_id) if analysis is not None else None
    if event is None or event["status"] != "succeeded" or event["tool"] != "summarize_observed_expression":
        return ["missing_successful_observed_summary"]
    result = evidence.results.get(event["receipt_id"], {})
    arguments = event["arguments"]
    reference = {key: task_context.get(key) for key in
        ("reference_batch", "library_factor", "batch_reference_policy", "biological_unit_reference")}
    if (answer.claim_scope != "descriptive" or analysis.method != "observed_expression_summary"
            or analysis.inference_scope != "descriptive" or analysis.selection_criterion != "other"
            or analysis.candidate_dfs or analysis.formula != "observed_mean_by_time"
            or analysis.measurement_type != task_context["measurement_type"]
            or analysis.normalization != result.get("normalization", {}).get("method")
            or analysis.hypothesis_family_id != task_context["hypothesis_family_id"]
            or analysis.biological_unit_column != task_context["biological_unit_column"]
            or analysis.reference.model_dump(mode="json") != reference
            or result.get("reference") != reference
            or arguments.get("metadata_path") != task_context["metadata_path"]
            or arguments.get("counts_path") != task_context["expression_path"]
            or arguments.get("time_col") != task_context.get("time_col", "numeric_time")):
        errors.append("descriptive_summary_contract_mismatch")
    quality_key = evidence_key("quality", evidence.input_sha256, arguments)
    if not any(row.get("quality_key") == quality_key and row["sequence"] < event["sequence"]
               and evidence.results[row["receipt_id"]].get("status") in {"pass", "warning"} for row in quality):
        errors.append("missing_applicable_presummary_quality_audit")
    universe = set(task_context["gene_ids"])
    if set(result.get("hypothesis_family", [])) != universe or set(row.gene_id for row in answer.gene_statistics) != universe:
        errors.append("descriptive_family_incomplete")
    estimates = result.get("genes", {})
    for row in answer.gene_statistics:
        observed = estimates.get(row.gene_id, {})
        if (row.status != observed.get("status") or row.p_value is not None or row.q_value is not None
                or row.claimed_discovery or row.confidence_interval is not None):
            errors.append("descriptive_gene_status_or_inference_mismatch:" + row.gene_id)
        if row.effect is not None:
            if row.effect_units != observed.get("effect_units") or not _same_number(row.effect, observed.get("effect")):
                errors.append("descriptive_effect_mismatch:" + row.gene_id)
        elif observed.get("effect") is not None:
            errors.append("descriptive_effect_omitted:" + row.gene_id)
        for declared, actual in ((row.predicted_curve, observed.get("mean_curve", [])),
                                 (row.time_grid, result.get("time_grid", []) if observed.get("mean_curve") else [])):
            if len(declared) != len(actual) or not all(_same_number(a, b) for a, b in zip(declared, actual)):
                errors.append("descriptive_curve_mismatch:" + row.gene_id)
                break
    return errors


def evaluate_workflow(answer, evidence, *, task_context, allow_evidence_read=False):
    """Check temporal-study acceptance evidence independently of hidden truth.

Only coordinator-verified receipts are accepted. Shape labels and free text are
not tool-certified; the independent scientific grader evaluates those claims.
Optional use has no acceptance requirement imposed by this function's result.
"""
    if allow_evidence_read is not True:
        raise PermissionError("Answer evidence evaluation is disabled")
    from .answers import StudyAnswer
    answer = StudyAnswer.model_validate_json(canonical_json_bytes(
        answer.model_dump(mode="json") if isinstance(answer, StudyAnswer) else answer))
    if not isinstance(evidence, VerifiedReceipts):
        raise TypeError("A coordinator-verified receipt set is required")
    errors, checked = [], []
    by_id = {event["receipt_id"]: event for event in evidence.events}
    if answer.input_sha256 != evidence.input_sha256:
        errors.append("answer_input_identity_mismatch")
    if answer.claim_scope not in {"association", "descriptive"}:
        errors.append("claim_scope_outside_temporal_association_contract")
    references = set(answer.evidence_ids)
    for diagnostic in answer.diagnostics:
        references.update(diagnostic.evidence_ids)
    if answer.analysis is not None and answer.analysis.tool_analysis_id:
        references.add(answer.analysis.tool_analysis_id)
    if any(ref not in by_id for ref in references):
        errors.append("unknown_tool_receipt")
    quality = [event for event in evidence.events if event["tool"] == "audit_quality"
               and event["status"] == "succeeded"]
    if not quality:
        errors.append("missing_quality_audit")
    if answer.decision == "execution_failure":
        errors.append("execution_failure_cannot_be_accepted_report")
    elif answer.decision == "insufficient_evidence":
        # Observable design/replication checks, never private generator labels.
        observations = [event for event in evidence.events if event["tool"] == "inspect_study_design"
                        and event["status"] == "succeeded" and event["receipt_id"] in references]
        observed_codes = set()
        for event in observations:
            result = evidence.results[event["receipt_id"]]
            if (result.get("population_inference_allowed") is False
                    and result.get("time_col") == task_context.get("time_col", "numeric_time")
                    and result.get("biological_unit_column") == task_context["biological_unit_column"]
                    and event["arguments"].get("metadata_path") == task_context["metadata_path"]):
                observed_codes.update(result.get("blocking_diagnostics", []))
        claimed = {item.code for item in answer.diagnostics}
        if not observed_codes or not observed_codes <= claimed or answer.gene_statistics:
            errors.append("unsupported_or_contradictory_refusal_evidence")
        else:
            checked.append("observable_refusal_basis")
    elif answer.analysis is not None and answer.analysis.inference_scope == "descriptive":
        errors.extend(_descriptive_binding(answer, evidence, task_context, by_id, quality))
        checked.append("observed_subset_summary")
    elif answer.analysis is None:
        errors.append("missing_recorded_analysis")
    else:
        analysis = answer.analysis
        event = by_id.get(analysis.tool_analysis_id)
        if (event is None or event["status"] != "succeeded" or event["tool"] != "fit_family"
                or analysis.tool_analysis_id not in evidence.results):
            errors.append("missing_successful_full_family_fit")
        else:
            result = evidence.results[analysis.tool_analysis_id]
            arguments = event["arguments"]
            chunks = result.get("chunks", [])
            if (not chunks or result.get("adjustment") != "BH_full_declared_family"
                    or set(result.get("hypothesis_family", [])) != set(task_context["gene_ids"])):
                errors.append("incomplete_hypothesis_family_receipt")
            expected_scope = ("selection_adjusted_omnibus" if arguments.get("inference_mode") == "gaussian_selection_mc"
                              else "fixed_design" if arguments.get("selection_criterion") == "fixed"
                              else "selection_conditional")
            expected_method = "negative_binomial" if task_context["measurement_type"] == "raw_counts" else "gaussian"
            if (analysis.method != expected_method or analysis.formula != arguments.get("formula_str")
                    or arguments.get("metadata_path") != task_context["metadata_path"]
                    or arguments.get("counts_path") != task_context["expression_path"]
                    or arguments.get("time_col") != task_context.get("time_col", "numeric_time")
                    or analysis.selection_criterion != arguments.get("selection_criterion")
                    or sorted(analysis.candidate_dfs) != sorted(arguments.get("spline_dfs", []))
                    or analysis.inference_scope != expected_scope
                    or analysis.hypothesis_family_id != task_context["hypothesis_family_id"]
                    or analysis.biological_unit_column != task_context["biological_unit_column"]
                    or analysis.measurement_type != task_context["measurement_type"]
                    or any(analysis.normalization != chunk.get("normalization_method") for chunk in chunks)):
                errors.append("analysis_contract_disagrees_with_recorded_fit")
            expected_reference = {key: task_context.get(key) for key in
                ("reference_batch", "library_factor", "batch_reference_policy", "biological_unit_reference")}
            if analysis.reference.model_dump(mode="json") != expected_reference:
                errors.append("prediction_reference_mismatch")
            design_key = evidence_key("design", evidence.input_sha256, arguments)
            quality_key = evidence_key("quality", evidence.input_sha256, arguments)
            if not any(row.get("quality_key") == quality_key and row["sequence"] < event["sequence"]
                       and evidence.results[row["receipt_id"]].get("status") in {"pass", "warning"} for row in quality):
                errors.append("missing_applicable_prefit_quality_audit")
            if not any(row["tool"] == "validate_design" and row["status"] == "succeeded"
                       and row.get("design_key") == design_key and row["sequence"] < event["sequence"]
                       and evidence.results[row["receipt_id"]].get("is_full_rank") is True
                       for row in evidence.events):
                errors.append("missing_applicable_prefit_design_check")
            if set(row.gene_id for row in answer.gene_statistics) != set(task_context["gene_ids"]):
                errors.append("answer_does_not_account_for_complete_family")
            estimates = result.get("genes", {})
            for row in answer.gene_statistics:
                estimate = estimates.get(row.gene_id, {})
                expected_status = {"ok": "tested", "no_information": "no_information", "failed": "failed"}.get(estimate.get("status"))
                if row.status != expected_status:
                    errors.append("gene_status_mismatch:" + row.gene_id)
                    continue
                if row.status == "tested":
                    if not _same_number(row.p_value, estimate.get("p_value")) or not _same_number(row.q_value, estimate.get("family_q_value")):
                        errors.append("gene_probability_mismatch:" + row.gene_id)
                elif row.p_value is not None or row.q_value is not None:
                    errors.append("unavailable_gene_has_probability:" + row.gene_id)
                if row.effect is not None:
                    key = "log2_fold_change" if row.effect_units == "log2_normalized_mean_change" else "effect_difference"
                    if not _same_number(row.effect, estimate.get(key)):
                        errors.append("gene_effect_mismatch:" + row.gene_id)
                if row.effect is not None or row.predicted_curve:
                    if estimate.get("reference_batch") != analysis.reference.reference_batch:
                        errors.append("gene_prediction_reference_mismatch:" + row.gene_id)
                if row.predicted_curve:
                    for field in ("predicted_curve", "time_grid"):
                        declared, observed = getattr(row, field), estimate.get(field, [])
                        if len(declared) != len(observed) or not all(_same_number(a, b) for a, b in zip(declared, observed)):
                            errors.append("gene_curve_mismatch:" + row.gene_id)
                            break
                if row.confidence_interval is not None:
                    errors.append("interval_not_supplied_by_recorded_tool:" + row.gene_id)
            checked.extend(["analysis_contract", "gene_probabilities", "reported_effects_and_curves"])
    if answer.lineage is not None or answer.regulatory_edges or answer.tf_activation_order:
        errors.append("supplemental_claims_outside_current_temporal_acceptance_contract")
    return {"workflow_passed": not errors, "required_for_acceptance": evidence.condition == "enforced",
            "errors": sorted(set(errors)), "checked_fields": checked,
            "unverified_fields": ["unverified_narrative", "shape", "biological_interpretation"],
            "scientific_correctness": "requires_independent_grading", "clinical_qualification": False}
