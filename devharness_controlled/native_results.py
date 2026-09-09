"""Read-only semantic validation of the pinned ``native_nb_result/1`` protocol.

This checks result structure and exposed numerical invariants; it cannot prove
that R ran, that preserved CSVs produced the reported matrices, or that subjects
are independent. The coordinator must establish those links separately. ``usable``
means the complete declared family is numerically usable under this candidate
protocol, never that it is clinically qualified.

F-tail equality is deliberately restricted to edgeR 4.10.5, legacy=False. In the
pinned package's R/glmQLFTest.R, the df.residual.adj branch disables poisson.bound;
its manual likewise limits that bound to legacy=True. Other configurations and
versions require a different contract. Native positive-infinite QL prior df is
valid, while the test denominator is the finite native capped df.
"""
import math
import re

from scipy.stats import chi2, f

from .contracts import canonical_json_bytes
from .native_contracts import EXPECTED_DESEQ2_VERSION, EXPECTED_EDGER_VERSION, EXPECTED_R_VERSION, NativeAnalysisPlan, NativeEffectPlan, GovernedNativePlan


BASE_FIELDS = set("""schema_version engine clinical_release_allowed qualification_status actual_versions
elapsed_seconds status error evidence conditions conditions_truncated memory_peak_mb memory_measurement_scope""".split())
FITTED_FIELDS = {"hypothesis_genes", "genes", "fdr_scope", "convergence_scope", "effects"}
GENE_FIELDS = set("""status failure_code p_value q_value native_p_value test_statistic test_distribution
test_df_num test_df_denom full_fit_convergence reduced_fit_convergence diagnostics""".split())
INPUT_FIELDS = set("""panel_gene_ids sample_ids biological_unit_count independence_verified independence_scope
full_design reduced_design temporal_columns temporal_names design_nesting design_selection""".split())
STAGES = set("""startup request_validation native_version_check input_validation output_publication
edgeR_normalization edgeR_glmQLFit edgeR_reduced_diagnostic_fit edgeR_glmQLFTest DESeq2_dataset
DESeq2_normalization DESeq2_gene_dispersions DESeq2_dispersion_trend DESeq2_MAP_dispersions
DESeq2_nbinomLRT DESeq2_results""".split())
EDGE_DIAGNOSTICS = set("""nb_dispersion working_nb_dispersion ql_posterior ql_prior ql_prior_df
adjusted_residual_df adjusted_deviance average_ql_dispersion full_iterations reduced_iterations""".split())
DESEQ_DIAGNOSTICS = set("""nb_dispersion gene_wise_dispersion fitted_dispersion map_dispersion
dispersion_outlier gene_dispersion_iterations map_dispersion_iterations full_iterations
max_cooks_distance dispersion_convergence""".split())
CONVERGENCE_SCOPE = {
    "edger_qlf": "Native flags retained where exposed; oneway and some dispersion optimizers do not expose complete convergence telemetry",
    "deseq2_lrt": "Native full/reduced coefficient flags retained; final dispersion-optimizer flags are not exposed",
}
EFFECT_SCOPE = "Joint temporal-null test only; no endpoint prediction rows or effect contrast supplied"
INDEPENDENCE_SCOPE = "Unique declared IDs do not establish biological independence"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _fields(record, fields, label):
    _require(type(record) is dict and set(record) == fields, f"Unknown or missing {label} fields")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _close(left, right):
    return _number(left) and _number(right) and math.isclose(left, right, rel_tol=5e-10, abs_tol=0.0)


def _text(value, maximum=2048, blank=False):
    return type(value) is str and len(value) <= maximum and (blank or bool(value.strip()))


def _array(value, expected, label):
    _require(type(value) is list and value == list(expected), f"Changed {label}")


def _limit(limits, message):
    if message not in limits:
        limits.append(message)


def _versions(plan, versions, failed, code, stage, limits):
    selected = "edgeR" if plan.engine == "edger_qlf" else "DESeq2"
    expected = EXPECTED_EDGER_VERSION if plan.engine == "edger_qlf" else EXPECTED_DESEQ2_VERSION
    full_keys = {"R", "platform", selected, "limma", "BiocParallel", "jsonlite"}
    _require(type(versions) is dict and set(versions) in ({"R", "platform"}, full_keys), "Invalid native version structure")
    _require(_text(versions["R"], 128) and versions["platform"] == "x86_64-pc-linux-gnu", "Invalid R version or Linux/amd64 platform")
    if set(versions) != full_keys:
        _require(failed and stage in {"startup", "request_validation", "native_version_check"}, "Missing fitted native package versions")
    else:
        for name in (selected, "limma", "BiocParallel", "jsonlite"):
            value = versions[name]
            _require(value is None or _text(value, 128) and re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value), "Invalid native package version")
            if value is None:
                _limit(limits, f"Native package version unavailable: {name}.")
    mismatch = versions["R"] != EXPECTED_R_VERSION or selected in versions and versions[selected] != expected
    if mismatch:
        _require(failed and code == "native_version_mismatch" and stage == "native_version_check", "Native version mismatch")
        _limit(limits, "Pinned native versions were not available; the native procedure failed before fitting.")
    if code == "native_version_mismatch":
        _require(mismatch, "Version-refusal record reports no pinned version mismatch")


def _conditions(result, code, limits):
    conditions = result["conditions"]
    _require(type(conditions) is list and len(conditions) <= 256, "Invalid native condition count")
    for condition in conditions:
        _fields(condition, {"stage", "kind", "message"}, "condition")
        _require(condition["stage"] in STAGES and condition["kind"] in {"warning", "message"}
                 and _text(condition["message"], 2000, blank=True), "Malformed native condition")
    _require(type(result["conditions_truncated"]) is bool, "Invalid condition truncation flag")
    if result["conditions_truncated"]:
        _require(result["status"] == "failed" and len(conditions) == 256, "Truncated conditions cannot accompany usable results")
        _limit(limits, "Native conditions were truncated at the declared limit; the run failed.")
    if code == "native_condition_capture_limit_exceeded":
        _require(result["conditions_truncated"], "Condition-limit failure has no truncation evidence")
    if conditions:
        _limit(limits, "Captured native warning/message conditions require review; they are not discarded or proof of recovery.")


def _design(record, samples):
    _fields(record, {"rank", "columns", "condition_number", "residual_df"}, "design")
    columns = record["columns"]
    _require(type(columns) is list and 1 <= len(columns) <= 32
             and all(type(name) is str and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in columns)
             and len(set(columns)) == len(columns) and columns[0] == "Intercept", "Invalid design columns")
    _require(type(record["rank"]) is int and record["rank"] == len(columns) < samples, "Invalid full-rank design")
    _require(type(record["residual_df"]) is int and record["residual_df"] == samples - len(columns), "Inconsistent design residual df")
    condition = record["condition_number"]
    _require(_number(condition) and 1 - 1e-12 <= condition <= 1000, "Invalid design condition number")
    return columns


def _inputs(plan, record):
    _fields(record, INPUT_FIELDS, "input validation")
    _array(record["panel_gene_ids"], plan.panel_genes, "panel gene IDs/order")
    _array(record["sample_ids"], plan.sample_ids, "sample IDs/order")
    _require(type(record["biological_unit_count"]) is int and record["biological_unit_count"] == len(plan.sample_ids), "Inconsistent biological-unit count")
    _require(record["independence_verified"] is False and record["independence_scope"] == INDEPENDENCE_SCOPE, "Unsupported biological-independence claim")
    full = _design(record["full_design"], len(plan.sample_ids))
    reduced = _design(record["reduced_design"], len(plan.sample_ids))
    _array(record["temporal_columns"], plan.temporal_columns, "temporal column indices")
    _require(all(type(column) is int and 2 <= column <= len(full) for column in record["temporal_columns"]), "Invalid temporal column indices")
    _array(record["temporal_names"], [full[column - 1] for column in plan.temporal_columns], "temporal column names")
    _require(reduced == [name for index, name in enumerate(full, 1) if index not in plan.temporal_columns]
             and len(full) - len(reduced) == len(plan.temporal_columns), "Reduced design is not the exact nontemporal subset")
    _require(record["design_nesting"] == "exact_non_temporal_column_subset"
             and record["design_selection"] == "supplied_fixed_matrices", "Unapproved design nesting or selection")


def _sample_values(plan, values):
    _require(type(values) is dict and set(values) == set(plan.sample_ids)
             and all(_number(value) and value > 0 for value in values.values()), "Invalid native normalization factors or sample coverage")


def _normalization(plan, record):
    from .native_contracts import is_reference_plan
    reference_plan = is_reference_plan(plan)
    edger = plan.engine == "edger_qlf"
    value_fields = {"effective_library_sizes", "normalization_factors", "centered_size_factors"} if edger else {"size_factors"}
    _fields(record, {"method", "scope", "reference_gene_ids"} | value_fields
            | ({"reference_specification_sha256"} if reference_plan else set()), "normalization")
    _require(record["method"] == ("edgeR_RLE" if edger else "DESeq2_ratio")
             and record["scope"] == ("prespecified_reference_genes" if reference_plan else "entire_input_panel"),
             "Unapproved normalization method/scope")
    references = record["reference_gene_ids"]
    _require(type(references) is list and references and all(type(name) is str for name in references)
             and len(set(references)) == len(references) and set(references) <= set(plan.panel_genes), "Invalid RLE reference genes")
    _array(references, [name for name in plan.panel_genes if name in set(references)], "RLE reference order")
    if reference_plan:
        from .normalization_reference import reference_genes_for_panel
        _array(references, reference_genes_for_panel(plan.normalization_reference, study_id=plan.study_id,
                                                    panel_genes=plan.panel_genes), "declared reference genes")
        _require(record["reference_specification_sha256"] == plan.normalization_reference.sha256(),
                 "Native normalization does not bind the declared reference specification")
    for key in value_fields:
        _sample_values(plan, record[key])
    if edger:
        _require(abs(math.fsum(math.log(value) for value in record["normalization_factors"].values()) / len(plan.sample_ids)) < 1e-10,
                 "Native edgeR normalization factors are not geometrically centered")
        logs = {sample: math.log(value) for sample, value in record["effective_library_sizes"].items()}
        mean_log = math.fsum(logs.values()) / len(logs)
        _require(all(_close(record["centered_size_factors"][sample], math.exp(value - mean_log)) for sample, value in logs.items()),
                 "Inconsistent centered effective library sizes")
    # DESeq2 ratio size factors are intentionally NOT required to have geometric mean 1.


def _settings(plan, record, limits):
    if plan.engine == "edger_qlf":
        expected = dict(legacy=False, robust=True, abundance_trend=True, winsor_tail_p=[0.05, 0.1], prior_count=0,
            low_count_filter="none", reduced_diagnostic_fit="additional_native_glmFit_at_identical_working_dispersion")
        _fields(record, set(expected) | {"top_proportion"}, "edgeR settings")
        value = record["top_proportion"]
        _require(value is None or _number(value) and 0 < value <= 1, "Invalid native top proportion")
        if value is None:
            _limit(limits, "Native common-dispersion top proportion is unavailable.")
    else:
        expected = dict(dispersion_fit_type=plan.deseq2_fit_type, dispersion_maxit=100, beta_maxit=100,
            beta_tolerance=1e-8, use_optim=True, use_QR=True, min_mu=0.5, independent_filtering=False,
            count_outlier_policy="diagnostic_only_no_replacement_no_filtering", coefficient_prior="none", low_count_filter="none")
        _fields(record, set(expected), "DESeq2 settings")
    # Canonical JSON distinguishes booleans from integers and preserves these pinned values.
    for key, value in expected.items():
        _require(canonical_json_bytes(record[key]) == canonical_json_bytes(value), f"Changed native setting: {key}")


def _evidence(plan, evidence, failed, code, limits):
    if type(evidence) is list and not evidence and failed:
        evidence = {}  # jsonlite represents the driver's initially empty R list as [].
    allowed = {"input_validation", "normalization", "native_settings"}
    if plan.engine == "deseq2_lrt":
        allowed.add("dispersion_fit_type")
    _require(type(evidence) is dict and set(evidence) <= allowed, "Unknown native evidence fields")
    if not failed:
        _require(set(evidence) == allowed, "Incomplete fitted native evidence")
    if "input_validation" in evidence:
        _inputs(plan, evidence["input_validation"])
    _require(not (set(evidence) - {"input_validation"}) or "input_validation" in evidence, "Native evidence is missing input validation")
    if "normalization" in evidence:
        _normalization(plan, evidence["normalization"])
    _require(not (set(evidence) & {"native_settings", "dispersion_fit_type"}) or "normalization" in evidence,
             "Native fit evidence is missing normalization")
    if "dispersion_fit_type" in evidence:
        fit_type = evidence["dispersion_fit_type"]
        _fields(fit_type, {"requested", "actual"}, "dispersion fit type")
        _require(fit_type["requested"] == plan.deseq2_fit_type and fit_type["actual"] in {"parametric", "mean", "local"}, "Unapproved dispersion fit type declaration")
        if fit_type["actual"] != fit_type["requested"]:
            _require(failed and code == "unapproved_native_fit_type_change" and "native_settings" not in evidence,
                     "Unapproved native trend substitution")
            _limit(limits, "DESeq2 changed the requested dispersion trend; the procedure refused that substitution.")
        elif code == "unapproved_native_fit_type_change":
            raise ValueError("Trend-refusal record reports no trend mismatch")
    if code == "unapproved_native_fit_type_change":
        _require(plan.engine == "deseq2_lrt" and "dispersion_fit_type" in evidence, "Missing native trend-refusal evidence")
    if "native_settings" in evidence:
        _require(plan.engine != "deseq2_lrt" or "dispersion_fit_type" in evidence, "Missing DESeq2 trend evidence")
        _settings(plan, evidence["native_settings"], limits)
    return evidence


def _tag(record, name, limits, infinity=False):
    _fields(record, {"state", "value"}, f"{name} diagnostic")
    state, value = record["state"], record["value"]
    if state == "finite":
        _require(_number(value) and value >= 0, f"Invalid finite diagnostic: {name}")
        if "iterations" in name:
            _require(value == math.floor(value), f"Noninteger iteration diagnostic: {name}")
        return value
    _require(value is None, f"Unavailable diagnostic contains an estimate: {name}")
    if state == "positive_infinity":
        _require(infinity, f"Unsupported infinite diagnostic: {name}")
        return math.inf
    _require(state in {"missing_native_value", "not_exposed"}, f"Unknown diagnostic state: {name}")
    _limit(limits, f"Native diagnostic unavailable: {name} ({state}).")
    if name == "max_cooks_distance":
        _limit(limits, "Cook's influence diagnostic is unavailable; influence safety has not been established.")
    return None


def _convergence(record, engine, full, accepted, limits):
    _fields(record, {"converged", "scope"}, "coefficient convergence")
    value = record["converged"]
    _require(value is None or type(value) is bool, "Invalid coefficient convergence flag")
    if engine == "edger_qlf":
        if record["scope"] == "native_failed_flag":
            _require(type(value) is bool, "Native failure flag is missing")
        else:
            _require(record["scope"] in {"not_exposed_by_native_oneway", "not_exposed_by_native_API"} and value is None,
                     "Contradictory edgeR convergence scope")
    else:
        _require(record["scope"] == ("native_fullBetaConv" if full else "native_reducedBetaConv"), "Unapproved DESeq2 coefficient convergence scope")
    if accepted:
        _require(value is not False and (engine == "edger_qlf" or value is True), "Accepted coefficients did not verifiably converge as required")
    if value is None:
        _limit(limits, "Some coefficient convergence flags are unknown; finite estimates do not establish convergence.")


def _diagnostics(plan, record, accepted, denominator, evidence, limits):
    edger = plan.engine == "edger_qlf"
    _fields(record, EDGE_DIAGNOSTICS if edger else DESEQ_DIAGNOSTICS, "native diagnostics")
    values = {}
    for name, value in record.items():
        if name not in {"dispersion_outlier", "dispersion_convergence"}:
            values[name] = _tag(value, name, limits, infinity=edger and name == "ql_prior_df")
    if accepted:
        _require(values["nb_dispersion"] is not None and (values["nb_dispersion"] >= 0 if edger else values["nb_dispersion"] > 0),
                 "Missing or invalid accepted NB dispersion")
    if edger:
        for key in ("nb_dispersion", "working_nb_dispersion", "average_ql_dispersion"):
            _require(values[key] is not None, "Missing required edgeR dispersion evidence")
        _require(values["average_ql_dispersion"] > 0 and _close(values["working_nb_dispersion"], values["nb_dispersion"] / values["average_ql_dispersion"]),
                 "Inconsistent native working dispersion")
        if accepted:
            _require(values["ql_posterior"] is not None and values["ql_posterior"] > 0, "Invalid accepted QL posterior")
        cap = len(plan.panel_genes) * evidence["input_validation"]["full_design"]["residual_df"]
        if denominator is not None:
            _require(denominator <= cap or _close(denominator, cap), "Native QL denominator exceeds its panel residual-df cap")
        if accepted and values["ql_prior_df"] is not None and values["adjusted_residual_df"] is not None:
            expected = min(values["ql_prior_df"] + values["adjusted_residual_df"], cap)
            _require(_close(denominator, expected), "Inconsistent native capped QL denominator df")
    else:
        outlier = record["dispersion_outlier"]
        _require(outlier is None or type(outlier) is bool, "Invalid dispersion outlier flag")
        if outlier is None:
            _limit(limits, "Native dispersion-outlier status is unavailable.")
        _fields(record["dispersion_convergence"], {"converged", "scope"}, "dispersion convergence")
        _require(record["dispersion_convergence"]["converged"] is None
                 and record["dispersion_convergence"]["scope"] == "native_final_optimizer_flags_not_exposed", "Unsupported dispersion-convergence claim")
        if accepted and outlier is not None:
            selected = values["gene_wise_dispersion"] if outlier else values["map_dispersion"]
            if selected is not None:
                _require(_close(values["nb_dispersion"], selected), "Native final dispersion disagrees with its outlier/MAP selection")


def _gene(plan, gene, evidence, limits):
    _fields(gene, GENE_FIELDS, "gene result")
    _require(gene["status"] in {"ok", "failed"}, "Unknown gene status")
    accepted = gene["status"] == "ok"
    _require(gene["failure_code"] is None if accepted else gene["failure_code"] in {"all_zero_hypothesis", "native_fit_or_test_failed"}, "Contradictory gene failure code")
    _require(gene["test_distribution"] == ("quasi_F" if plan.engine == "edger_qlf" else "chi_square"), "Unapproved native test distribution")
    for key in ("p_value", "q_value", "native_p_value"):
        value = gene[key]
        _require(value is None or _number(value) and 0 <= value <= 1, f"Invalid native probability: {key}")
    statistic, numerator, denominator = gene["test_statistic"], gene["test_df_num"], gene["test_df_denom"]
    _require(statistic is None or _number(statistic) and statistic >= 0, "Invalid native test statistic")
    _require(numerator is None or _number(numerator) and numerator == len(plan.temporal_columns), "Changed joint temporal test df")
    if plan.engine == "deseq2_lrt":
        _require(denominator is None, "DESeq2 LRT has an unexpected denominator df")
    else:
        _require(denominator is None or _number(denominator) and denominator >= 0, "Invalid native denominator df")
    _convergence(gene["full_fit_convergence"], plan.engine, True, accepted, limits)
    _convergence(gene["reduced_fit_convergence"], plan.engine, False, accepted, limits)
    _diagnostics(plan, gene["diagnostics"], accepted, denominator, evidence, limits)
    if not accepted:
        _require(gene["p_value"] is None and gene["q_value"] is None, "Failed gene contains usable discovery probabilities")
        return None
    _require(all(_number(gene[key]) for key in ("p_value", "q_value", "native_p_value", "test_statistic", "test_df_num"))
             and gene["q_value"] >= gene["p_value"] and _close(gene["p_value"], gene["native_p_value"]), "Missing or inconsistent accepted native test")
    if plan.engine == "edger_qlf":
        _require(_number(denominator) and denominator > 0, "Accepted QL test has no positive denominator df")
        expected = float(f.sf(statistic, numerator, denominator))
    else:
        expected = float(chi2.sf(statistic, numerator))
    _require(_close(gene["p_value"], expected), "Native p-value does not match the approved test statistic/df")
    return gene["p_value"]


def _validate_native_result(plan: NativeAnalysisPlan, result: dict) -> dict:
    try:
        from .native_contracts import is_reference_plan
        if is_reference_plan(plan):
            plan = type(plan).model_validate_json(plan.canonical_bytes())
        else:
            if isinstance(plan, GovernedNativePlan):
                plan = plan.primary_plan()
            plan = NativeAnalysisPlan.model_validate(plan)
        _require(type(result) is dict, "Native result must be a JSON object")
        _require(len(canonical_json_bytes(result)) <= 8 * 1024 * 1024, "Native result exceeds the 8 MB validation bound")
    except (TypeError, UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError("Native result contains invalid JSON values") from error
    failed = result.get("status") == "failed"
    _fields(result, BASE_FIELDS if failed else BASE_FIELDS | FITTED_FIELDS, "native result")
    _require(result["schema_version"] == "native_nb_result/1" and result["status"] in {"ok", "partial_failure", "failed"}, "Unapproved native result schema/status")
    _require(result["clinical_release_allowed"] is False and result["qualification_status"] == "candidate_unqualified", "Unsupported clinical qualification claim")
    _require(_number(result["elapsed_seconds"]) and result["elapsed_seconds"] >= 0, "Invalid native elapsed time")
    _require(result["memory_peak_mb"] is None and result["memory_measurement_scope"] == "external_supervisor_required", "Unsubstantiated native memory measurement")
    limits = [
        "Candidate native procedure remains unqualified for clinical release.",
        "Biological independence is not established by unique declared sample or unit IDs.",
        "Native output does not independently attest input classification; the coordinator must bind the approved plan and preserved inputs.",
        "Runtime image, driver, complete dependency lock and authentic execution require coordinator verification.",
        "Peak memory is unavailable in native output and requires external supervisor measurement.",
        "Final dispersion optimizer convergence is not fully exposed by the pinned native APIs.",
        "This joint temporal-null protocol establishes no clinical biomarker, endpoint effect or interval.",
    ]
    if is_reference_plan(plan):
        limits.append("Prespecified normalization references are bound and checked structurally; biological stability and clinical suitability are not established.")
    code, stage = None, None
    if failed:
        _fields(result["error"], {"code", "stage", "message"}, "native failure")
        code, stage = result["error"]["code"], result["error"]["stage"]
        _require(_text(code, 256) and re.fullmatch(r"[a-z][a-z0-9_]*", code)
                 and stage in STAGES and _text(result["error"]["message"], 6000, blank=True), "Malformed native failure")
        _limit(limits, f"Native procedure failed: {code} at {stage}; retained evidence is not a usable analysis.")
    else:
        _require(result["error"] is None and plan.data_classification != "restricted_clinical", "Unexpected error or unconfigured clinical admission")
    _require(result["engine"] == plan.engine or failed and result["engine"] is None
             and stage in {"startup", "request_validation"}, "Native engine differs from approved plan")
    _versions(plan, result["actual_versions"], failed, code, stage, limits)
    _conditions(result, code, limits)
    evidence = _evidence(plan, result["evidence"], failed, code, limits)
    if code == "clinical_admission_unconfigured":
        _require(plan.data_classification == "restricted_clinical" and stage == "request_validation" and not evidence,
                 "Clinical-admission refusal contradicts approved classification or execution stage")
    if code == "native_version_mismatch":
        _require(not evidence, "Version-refusal record contains later fit evidence")
    if code == "unapproved_native_fit_type_change":
        _require(stage == "DESeq2_dispersion_trend", "Trend refusal has the wrong native stage")
    if failed:
        return {"usable": False, "qualification_limits": limits}
    _array(result["hypothesis_genes"], plan.hypothesis_genes, "ordered hypothesis family")
    _require(type(result["genes"]) is dict and set(result["genes"]) == set(plan.hypothesis_genes), "Changed hypothesis gene coverage")
    _require(result["fdr_scope"] == "prespecified_hypothesis_genes_including_failed_positions"
             and result["convergence_scope"] == CONVERGENCE_SCOPE[plan.engine], "Unapproved FDR/convergence scope")
    _fields(result["effects"], {"status", "scope"}, "effect scope")
    _require(result["effects"] == {"status": "not_supported_by_this_protocol", "scope": EFFECT_SCOPE}, "Unapproved effect estimate/claim")
    ps = [_gene(plan, result["genes"][name], evidence, limits) for name in plan.hypothesis_genes]
    ordered = sorted((p, index) for index, p in enumerate(ps) if p is not None)
    expected_qs = [None] * len(ps)
    running = 1.0
    for rank in range(len(ordered) - 1, -1, -1):
        p, index = ordered[rank]
        running = min(running, len(ps) * p / (rank + 1))
        expected_qs[index] = running
    for name, expected in zip(plan.hypothesis_genes, expected_qs, strict=True):
        actual = result["genes"][name]["q_value"]
        _require(actual is None if expected is None else _close(actual, expected), "BH adjustment differs from the complete approved family")
    all_usable = all(p is not None for p in ps)
    _require(result["status"] == ("ok" if all_usable else "partial_failure"), "Native aggregate status contradicts gene outcomes")
    if not all_usable:
        _limit(limits, "The declared hypothesis family contains failed fits; all failed positions remain in its FDR denominator.")
    return {"usable": all_usable, "qualification_limits": limits}


def validate_native_result(plan: NativeAnalysisPlan, result: dict) -> dict:
    """Validate exposed evidence without changing it; malformed evidence raises ValueError."""
    try:
        if isinstance(plan, NativeEffectPlan):
            from .native_effects import validate_effect_result
            return validate_effect_result(plan, result)
        return _validate_native_result(plan, result)
    except (TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError) as error:
        raise ValueError("Malformed native result evidence") from error
