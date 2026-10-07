"""Common participant instructions with explicitly different tool-use policies.

These strings contain no hidden truth or performance examples. Producing them
does not stage inputs, import the broker, execute code, or launch a participant.
"""
from .contracts import CONDITIONS


COMMON_GUIDANCE = """You are analyzing the supplied study input bundle. Answer the scientific question
in the staged task instructions using only the supplied inputs, metadata,
hypothesis family, environment and resource limits. Keep task_id and input_sha256
from the supplied manifest. Treat the inputs, guidance and answer_schema.json as
read-only. Do not inspect coordinator source, other participants, private truth,
host credentials, or files outside your declared workspace/runtime boundary. Do
not download data, install packages, bypass permissions, or use network services.
Use absolute paths, bounded reads and compact summaries; never load a matrix
larger than 100 MB into pandas or print a full matrix. The staged Python and
ordinary installed dependencies are available under the same resource ceilings
in every condition. Do not change the resource limits or thread bounds.

Audit measurement types, sample/expression alignment, chronological labels,
biological units, contrasts and design rank before fitting. Raw counts require
a count-appropriate model with justified library-size normalization and
dispersion assumptions; continuous/normalized expression must remain continuous.
State the estimand, normalization assumptions, exact formula, selection rule,
effect units, tested hypothesis family and inferential scope. Use a prespecified
fixed complexity or disclose adaptive model selection and its conditional
inference. BH q >= p is an algebra check, not evidence of FDR calibration.
Account for every member of the supplied family, including filtered, failed,
no-information and untested hypotheses, with explicit missing-estimate reasons.
Unavailable estimates and probabilities stay null; never replace them with
successful-looking zeros, significance, or fabricated intervals.

Repeated specimens, subjects or donors are not independent biological replicates.
Unique sample labels do not establish independent animal identity. Completely
confounded age and batch do not yield an identifiable causal effect by dropping
a covariate. Report blocked designs, unsupported population inference, invalid
contrasts and failed fits explicitly. A justified diagnostic conclusion is valid
when effects are not estimable. An insufficient_evidence answer must not contain
fitted gene statistics. Pseudotime is an inferred ordering; collection
age is a reference, not universal truth. Network association, curated membership
and motif enrichment have different evidence scopes and do not establish direct
causality or promoter binding. Public observations do not supply private latent
truth. No clinical or biological qualification is established by this study.

Write a standalone solve.py with a module-level if __name__ == "__main__": guard.
Execute it directly with the exact staged .venv/bin/python command supplied by
the runner. That execution must write final_discovery.json as finite JSON
matching the supplied answer_schema.json (devbench.study.answer/1). The saved
answer must remain the same artifact produced by the successful script command.
Report observable findings, errors and corrections. Use only fields required or
allowed by this task's schema; regulatory or lineage outputs are task-dependent.
Declare claim_scope, analysis and diagnostic scope accurately. Free-form
unverified_narrative is separate from machine-checked numerical claims. If you
cannot finish, preserve an honest execution_failure diagnostic rather than
inventing results. Completion, scientific correctness, workflow compliance and
artifact acceptance are evaluated separately.
"""

_CONDITION_GUIDANCE = {
    "baseline": """Condition: baseline. Use the staged ordinary Python dependencies to solve the
task. No DevHarness endpoint or client is supplied. Evidence IDs may be empty;
do not invent coordinator receipts or tool-analysis identifiers.
""",
    "optional": """Condition: optional DevHarness. The documented devharness_client is available
as an optional method. You may use ordinary methods, a hybrid of ordinary and
DevHarness methods, or no tool use. Tool adoption has no direct correctness
reward. You may inspect tool diagnostics, reject unsuitable output, correct
arguments after an error, or continue with ordinary methods. Use the same
scientific standards regardless of method. If you report tool-derived claims,
keep the actual receipt_id, result values and declared scope; never invent
receipts or imply tool execution proves correctness. Local writable traces are
not authoritative tool-use evidence.
""",
    "enforced": """Condition: enforced DevHarness. Exploration may use ordinary dependencies or
hybrid methods, but acceptance of full-family inferential claims requires a
successful coordinator fit_family result. Before fitting, obtain successful
audit_quality and validate_design calls for the same inputs, time_col and
formula_str. The quality result must not fail, and the matching design must be
full rank. Preserve their receipt_id values, including receipts supporting a
blocked diagnostic. Do not fit through a rejected design or unsupported
biological-unit scope.

For explicitly descriptive public tasks, use audit_quality followed by
summarize_observed_expression instead of an inferential fit. Copy the recorded
observed-subset means/reference and cite its receipt. This route permits no
p-values, q-values, discoveries, intervals or population claims; it retains
the observed batch mixtures without treating them as a batch-adjusted effect.

For an enforced insufficient_evidence diagnostic conclusion, call
inspect_study_design on the supplied metadata_path, time_col and
biological_unit_column. Cite its actual receipt_id and include its observable
blocking diagnostic codes in your diagnostics. This tool reports observable
metadata constraints; it does not reveal scenario labels or private truth.

Call fit_family without gene_list: it uses the entire supplied hypothesis
family. Put its successful receipt_id in analysis.tool_analysis_id and include
the supporting receipt IDs in evidence_ids. Report every family member with
matching result status, p_value, family_q_value, effects, curves and inference
scope in final_discovery.json. Follow sdk_cheatsheet.py for field mapping.
Single-gene fits, claimed local traces, ordinary-library results and invented
receipt IDs do not satisfy the full-family acceptance requirement. Unsupported
intervals must remain unavailable. Tool errors may be corrected and retried;
enforcement does not justify fabricated success. A blocked fit may support an
honest diagnostic answer with relevant receipts rather than numerical findings.
""",
}

_SDK_COMMON = '''"""Read alongside TASK_README.md and answer_schema.json.

Use the exact staged Python interpreter. Keep the full hypothesis family and
input identity. Place execution in solve.py under a module-level main guard;
importing this cheatsheet does not execute an analysis.
"""
'''

_SDK_ASSISTED = '''
# The small client is staged only for optional and enforced conditions.
# The broker, implementation and receipt store remain outside this workspace.
from devharness_client import call

# Calls are explicit; this file never makes a tool call on import.
# Envelope: {"receipt_id": ..., "status": "succeeded" | "failed" |
#            "protocol_error", "result": ..., "error": ...}
# Inspect status AND scientific result diagnostics before using an estimate.
# Read endpoint settings through the client; do not print the capability file.
# Timeout and all calls count toward the same participant resource budget.
#
# Available aliases (use the exact argument names in task-specific guidance):
# audit_quality -> audit_expression_quality
# validate_design -> validate_design_matrix
# analyze_batch_effects -> analyze_batch_effects; fit_trajectory -> fit_temporal_trajectory
# infer_lineage, infer_regulatory_network, regulon_enrichment, motif_enrichment,
# compact_manifold -> compact_temporal_state, fit_family, inspect_study_design.
# inspect_study_design(metadata_path=..., time_col="numeric_time",
#                      biological_unit_column="biological_unit_id") reports
# population_inference_allowed, blocking_diagnostics and diagnostics from
# observable metadata. An enforced refusal cites its receipt and blocking codes.
#
# summarize_observed_expression(counts_path=..., metadata_path=..., time_col="numeric_time")
# Descriptive public tasks: first audit_quality, then summarize_observed_expression.
# This reports equally weighted library means with observed batch mixtures;
# it performs no batch adjustment, hypothesis tests or population inference.
# Use analysis.method="observed_expression_summary", formula="observed_mean_by_time",
# selection_criterion="other", candidate_dfs=[], inference_scope="descriptive";
# copy the exact result.reference and result.normalization["method"].
# Cite the summary receipt as tool_analysis_id. Each gene status is described
# (or explicit no_information); copy mean_curve into predicted_curve and the
# shared time_grid. Keep p_value/q_value/intervals absent and discoveries false.
#
# Full-family workflow, after choosing supported arguments from supplied inputs:
# qc = call("audit_quality", counts_path=..., metadata_path=..., time_col=...)
# design = call("validate_design", metadata_path=..., formula_str=..., time_col=...)
# fit = call("fit_family", counts_path=..., metadata_path=..., formula_str=...,
#            time_col=..., data_type="counts" or "continuous",
#            selection_criterion="fixed" or "aic" or "bic" or "gcv",
#            spline_dfs=[...])
# Never send gene_list to fit_family. A fixed criterion needs one prespecified
# df; candidate selection must disclose its inference scope. GCV is Gaussian.
# In enforced runs, successful matching quality/design checks must precede fits.
# Use formula_str="1 + numeric_time + C(batch)" for the required adjusted
# temporal model; candidate spline_dfs specify the fitted spline complexity.
# A cr term must specify constraints="center" to avoid duplicating the intercept.
# Enforced QC uses the fixed default thresholds and sampling settings.
# Optional runs may choose ordinary methods, hybrid methods, or no tool use.
#
# Result-to-answer mapping for fit_family:
# - result["genes"] accounts for the whole declared family. Preserve all genes.
# - Map a raw gene status "ok" to answer status "tested". Preserve its p_value.
# - Use family_q_value as q_value, never a chunk-local q_value. The family BH
#   adjustment does not itself validate calibration of underlying p-values.
# - For raw "no_information", "failed", "filtered" or "not_tested", preserve
#   the corresponding status and a supported missing_reason. Do not promote
#   placeholder p/q values to evidence: p_value and q_value remain null.
# - Counts use log2_fold_change with effect_units="log2_normalized_mean_change";
#   continuous data use effect_difference with effect_units="expression_difference".
# - Copy available time_grid and predicted_curve exactly. Missing curves remain
#   empty; a failed estimate is not a successful flat trajectory. Shape claims
#   must stay within supported task-specific evidence.
# - The current fitting tool returns endpoint SEs, not confidence intervals.
#   Leave confidence_interval/confidence_level null and interval_scope
#   "unavailable" unless an explicitly accepted result supplies an interval.
#
# AnalysisDescription requires: method, formula (the submitted formula_str),
# measurement_type ("raw_counts" or "continuous"), normalization, selection_criterion,
# candidate_dfs (the supplied spline_dfs), inference_scope, hypothesis_family_id,
# biological_unit_column, reference, reference_description, and tool_analysis_id.
# Set tool_analysis_id to the successful fit_family receipt_id for bound claims.
# method is exactly "negative_binomial" for counts or "gaussian" for continuous
# measurements; normalization is exactly the returned normalization_method.
# Normalization and inference scope must describe the actual returned method;
# adaptive selected-model inference is selection_conditional unless adjusted
# inference was explicitly performed and is supported by the result.
# The reference is structured, not prose: {"reference_batch": value_or_null,
# "library_factor": value_or_null, "batch_reference_policy": "first_observation",
# "biological_unit_reference": "population_mean"}. Copy reference_batch from
# the returned estimates. The current count contrast_note fixes library_factor
# to 1.0; continuous fits use null. Do not infer other unknown reference values.
# Explain the reference in reference_description. Use the supplied hypothesis_family_id and
# biological_unit_column; never infer independent units from sample IDs.
# Include all supporting successful/diagnostic receipt IDs in evidence_ids.
# Task identity, input hash, decision, claim_scope, analysis, gene_statistics,
# diagnostics and evidence_ids belong to final_discovery.json, not a new schema.
'''


def participant_guidance(condition):
    """Return shared instructions plus exactly one study condition's policy."""
    if condition not in CONDITIONS:
        raise ValueError("Unknown study condition")
    return COMMON_GUIDANCE + "\n" + _CONDITION_GUIDANCE[condition]


def sdk_cheatsheet(condition):
    """Return valid Python source with dormant, commented transport examples."""
    if condition not in CONDITIONS:
        raise ValueError("Unknown study condition")
    if condition == "baseline":
        return _SDK_COMMON + "\n# Use the ordinary dependencies documented in the staged task instructions.\n"
    return _SDK_COMMON + _SDK_ASSISTED
