"""Coordinator-only limited public reference grading, never full biological truth."""
from .contracts import canonical_json_bytes
from .public_answers import PublicAnswer
from .public_io import load_public_inputs


def grade_public_answer(answer, *, input_root, reference, plan, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Public answer grading is disabled")
    import numpy as np
    task, values, rows, _, input_hash = load_public_inputs(input_root, allow_data_execution=True)
    if (reference.get("record_format") != "devbench.public-reference/1"
            or reference.get("task_id") != task["task_id"] or reference.get("input_sha256") != input_hash
            or reference.get("kind") != task["kind"] or reference.get("complete_biological_truth") is not False):
        raise ValueError("Public reference does not bind these inputs and limited scope")
    result = {"reference_task_success": None, "complete_biological_correctness": None,
              "metrics": {}, "errors": [], "metric_missingness": {}, "calibration_status": plan.calibration_status}
    eligible = reference.get("eligible_evaluation_controls", []) if task["kind"] == "seqc_normalization" else task["gene_ids"]
    if task["kind"] == "seqc_normalization":
        ratios = reference["evaluation_ratios_A_over_B"]
        if len(set(eligible)) != len(eligible) or not set(eligible) <= set(ratios) & set(task["gene_ids"]):
            raise ValueError("Invalid fixed evaluation control family")
        result["metrics"]["eligible_evaluation_controls"] = len(eligible)
    if not eligible:
        result["errors"].append("no_eligible_reference_controls")
    else:
        # Fixed eligibility is established before reading a participant answer.
        result["reference_task_success"] = False
    try:
        parsed = PublicAnswer.model_validate_json(canonical_json_bytes(
            answer.model_dump(mode="json") if isinstance(answer, PublicAnswer) else answer))
    except (ValueError, TypeError):
        result["errors"].append("invalid_or_missing_answer")
        return result
    if parsed.task_id != task["task_id"] or parsed.input_sha256 != input_hash or parsed.payload.kind != task["kind"]:
        result["errors"].append("answer_task_or_input_identity_mismatch")
        return result
    if task["kind"] == "developmental_observation":
        if list(parsed.payload.stage_labels) != reference["stage_labels"] or {row.gene_id for row in parsed.payload.curves} != set(reference["gene_ids"]):
            result["errors"].append("observed_summary_axes_incomplete_or_reordered")
            return result
        by_gene = {row.gene_id: row.means for row in parsed.payload.curves}
        predicted = np.asarray([by_gene[gene] for gene in reference["gene_ids"]])
        expected = np.asarray(reference["means"], dtype=float)
        if predicted.shape != expected.shape or not np.isfinite(expected).all():
            raise ValueError("Invalid retained observation reference")
        error = float(np.max(np.abs(predicted - expected)))
        result["metrics"].update(maximum_absolute_observed_mean_error=error, observed_genes=len(expected), independent_animal_count=None)
        result["metric_missingness"]["independent_animal_count"] = "animal_identity_unverified"
        result["reference_task_success"] = error <= plan.developmental_absolute_tolerance
        if not result["reference_task_success"]:
            result["errors"].append("observed_mean_mismatch")
        return result
    factors = {row.library_id: row.factor for row in parsed.payload.factors}
    if set(factors) != set(task["sample_ids"]):
        result["errors"].append("normalization_library_family_incomplete")
        return result
    scale = np.asarray([factors[sample] for sample in task["sample_ids"]])
    scale /= np.exp(np.log(scale).mean())
    normalized = values / scale
    groups = {group: np.asarray([i for i, row in enumerate(rows) if row["mixture"] == group]) for group in "ABCD"}
    if any(len(indices) == 0 for indices in groups.values()):
        raise ValueError("SEQC reference task lacks a mixture")
    means = {group: normalized[:, indices].mean(axis=1) for group, indices in groups.items()}
    positions = {gene: i for i, gene in enumerate(task["gene_ids"])}
    if eligible:
        indices = [positions[gene] for gene in eligible]
        expected = np.asarray([ratios[gene] for gene in eligible], dtype=float)
        if np.any(expected <= 0) or not np.isfinite(expected).all():
            raise ValueError("Nominal control ratios must be finite and positive")
        error = np.abs(np.log2(means["A"][indices] / means["B"][indices]) - np.log2(expected))
        if not np.isfinite(error).all():
            result["errors"].append("eligible_control_ratio_unavailable")
        else:
            median = float(np.median(error))
            result["metrics"].update(median_absolute_control_log2_error=median,
                                      maximum_absolute_control_log2_error=float(error.max()))
            result["reference_task_success"] = median <= plan.maximum_control_log2_error
            if not result["reference_task_success"]:
                result["errors"].append("held_out_control_ratio_error")
    # Secondary descriptive metrics are never a claim of clinical or gene-wide truth.
    endogenous = [positions[gene] for gene in reference.get("endogenous_gene_ids", [])]
    cvs = []
    for group, indices in groups.items():
        if len(indices) > 1:
            chosen = [i for i in endogenous if means[group][i] > 0]
            cvs.extend((normalized[chosen][:, indices].std(axis=1, ddof=1) / means[group][chosen]).tolist())
    result["metrics"]["median_within_mixture_technical_cv"] = float(np.median(cvs)) if cvs else None
    if not cvs:
        result["metric_missingness"]["median_within_mixture_technical_cv"] = "insufficient_positive_technical_replicates"
    informative = [i for i in endogenous if means["A"][i] != means["B"][i]]
    ordered = []
    for i in informative:
        values_in_order = [means[group][i] for group in "ACDB"]
        differences = np.diff(values_in_order)
        ordered.append(bool(np.all(differences >= 0) or np.all(differences <= 0)))
    result["metrics"]["observed_endogenous_titration_order_fraction"] = float(np.mean(ordered)) if ordered else None
    if not ordered:
        result["metric_missingness"]["observed_endogenous_titration_order_fraction"] = "no_distinct_observed_A_B_means"
    result["metrics"]["independent_patient_count"] = 0
    return result
