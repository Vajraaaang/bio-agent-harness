"""Synthetic copies of native result structures; these are not native execution evidence."""
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest
from scipy.stats import chi2, f
from statsmodels.stats.multitest import multipletests

from devharness_controlled.native_contracts import NativeAnalysisPlan


def api():
    source = Path(__file__).resolve().parents[1] / "devharness_controlled" / "native_results.py"
    assert source.is_file(), "Native result validator is missing"
    return importlib.import_module("devharness_controlled.native_results")


def analysis_plan(engine="edger_qlf", **updates):
    values = dict(plan_id="synthetic-plan", revision=1, intended_use_ref="synthetic-verification-only",
        study_id="synthetic-study", data_classification="synthetic", engine=engine, normalization="rle",
        deseq2_fit_type="mean" if engine == "deseq2_lrt" else None,
        hypothesis_genes=("gB", "gA", "gC"), panel_genes=("gB", "gA", "gC", "gD", "gE", "gF", "gG", "gH"),
        sample_ids=tuple(f"S{i}" for i in range(6)), temporal_columns=(2, 3), time_unit="days",
        basis_definition_ref="synthetic-basis-protocol/1", runtime_image_id="sha256:" + "a" * 64,
        platform="linux/amd64", max_memory_mb=2048, max_cpus=1, timeout_sec=60,
        **{key: "b" * 64 for key in ("counts_sha256", "metadata_sha256", "full_design_sha256",
            "reduced_design_sha256", "driver_sha256", "package_lock_sha256", "implementation_sha256", "environment_sha256")})
    return NativeAnalysisPlan(**{**values, **updates})


def finite(value):
    return {"state": "finite", "value": value}


def refresh_probabilities(result):
    """Independent test reference: SciPy tails and statsmodels full-family BH."""
    for gene in result["genes"].values():
        if gene["status"] != "ok":
            continue
        distribution = f if result["engine"] == "edger_qlf" else chi2
        dfs = [gene["test_df_num"]] + ([gene["test_df_denom"]] if distribution is f else [])
        gene["native_p_value"] = gene["p_value"] = float(distribution.sf(gene["test_statistic"], *dfs))
    records = list(result["genes"].values())
    ps = [gene["p_value"] if gene["p_value"] is not None else 1.0 for gene in records]
    qs = multipletests(ps, method="fdr_bh")[1]
    for gene, q in zip(records, qs, strict=True):
        gene["q_value"] = float(q) if gene["p_value"] is not None else None


def fitted_result(engine="edger_qlf"):
    """Structure copied from retained smoke-02 records with explicit synthetic values."""
    plan = analysis_plan(engine)
    edger = engine == "edger_qlf"
    result = dict(schema_version="native_nb_result/1", engine=engine, clinical_release_allowed=False,
        qualification_status="candidate_unqualified", actual_versions={"R": "4.6.1",
            "platform": "x86_64-pc-linux-gnu", "edgeR" if edger else "DESeq2": "4.10.5" if edger else "1.52.0",
            "limma": "3.68.5", "BiocParallel": "1.46.0", "jsonlite": "2.0.0"},
        elapsed_seconds=0.5, status="ok", error=None, hypothesis_genes=list(plan.hypothesis_genes), genes={},
        fdr_scope="prespecified_hypothesis_genes_including_failed_positions",
        convergence_scope=("Native flags retained where exposed; oneway and some dispersion optimizers do not expose complete convergence telemetry"
            if edger else "Native full/reduced coefficient flags retained; final dispersion-optimizer flags are not exposed"),
        effects={"status": "not_supported_by_this_protocol",
            "scope": "Joint temporal-null test only; no endpoint prediction rows or effect contrast supplied"},
        evidence={}, conditions=[], conditions_truncated=False, memory_peak_mb=None,
        memory_measurement_scope="external_supervisor_required")
    for name, statistic in zip(plan.hypothesis_genes, (10.0, 3.0, 0.1), strict=True):
        diagnostics = ({"nb_dispersion": finite(0.2), "working_nb_dispersion": finite(0.1),
            "ql_posterior": finite(1.2), "ql_prior": finite(1.0), "ql_prior_df": finite(10.0),
            "adjusted_residual_df": finite(2.0), "adjusted_deviance": finite(2.4),
            "average_ql_dispersion": finite(2.0), "full_iterations": finite(5),
            "reduced_iterations": {"state": "not_exposed", "value": None}} if edger else {
            "nb_dispersion": finite(0.2), "gene_wise_dispersion": finite(0.3), "fitted_dispersion": finite(0.25),
            "map_dispersion": finite(0.2), "dispersion_outlier": False, "gene_dispersion_iterations": finite(6),
            "map_dispersion_iterations": finite(8), "full_iterations": finite(4), "max_cooks_distance": finite(0.04),
            "dispersion_convergence": {"converged": None, "scope": "native_final_optimizer_flags_not_exposed"}})
        result["genes"][name] = dict(status="ok", failure_code=None, p_value=None, q_value=None,
            native_p_value=None, test_statistic=statistic, test_distribution="quasi_F" if edger else "chi_square",
            test_df_num=2, test_df_denom=12.0 if edger else None,
            full_fit_convergence={"converged": True, "scope": "native_failed_flag" if edger else "native_fullBetaConv"},
            reduced_fit_convergence={"converged": None if edger else True,
                "scope": "not_exposed_by_native_oneway" if edger else "native_reducedBetaConv"}, diagnostics=diagnostics)
    result["evidence"]["input_validation"] = dict(panel_gene_ids=list(plan.panel_genes), sample_ids=list(plan.sample_ids),
        biological_unit_count=6, independence_verified=False,
        independence_scope="Unique declared IDs do not establish biological independence",
        full_design={"rank": 4, "columns": ["Intercept", "Time1", "Time2", "Batch"], "condition_number": 2.8, "residual_df": 2},
        reduced_design={"rank": 2, "columns": ["Intercept", "Batch"], "condition_number": 2.6, "residual_df": 4},
        temporal_columns=[2, 3], temporal_names=["Time1", "Time2"],
        design_nesting="exact_non_temporal_column_subset", design_selection="supplied_fixed_matrices")
    normalization = dict(method="edgeR_RLE" if edger else "DESeq2_ratio", scope="entire_input_panel",
        reference_gene_ids=list(plan.panel_genes))
    if edger:
        normalization.update(effective_library_sizes={s: 1000.0 for s in plan.sample_ids},
            normalization_factors={s: 1.0 for s in plan.sample_ids}, centered_size_factors={s: 1.0 for s in plan.sample_ids})
        settings = dict(legacy=False, robust=True, abundance_trend=True, winsor_tail_p=[0.05, 0.1], prior_count=0,
            top_proportion=0.3, low_count_filter="none",
            reduced_diagnostic_fit="additional_native_glmFit_at_identical_working_dispersion")
    else:
        normalization["size_factors"] = {s: 1.3 for s in plan.sample_ids}
        settings = dict(dispersion_fit_type="mean", dispersion_maxit=100, beta_maxit=100, beta_tolerance=1e-8,
            use_optim=True, use_QR=True, min_mu=0.5, independent_filtering=False,
            count_outlier_policy="diagnostic_only_no_replacement_no_filtering", coefficient_prior="none", low_count_filter="none")
        result["evidence"]["dispersion_fit_type"] = {"requested": "mean", "actual": "mean"}
    result["evidence"].update(normalization=normalization, native_settings=settings)
    refresh_probabilities(result)
    return plan, result


FITTED_FIELDS = {"hypothesis_genes", "genes", "fdr_scope", "convergence_scope", "effects"}


def global_failure(engine="edger_qlf", code="native_or_input_error", stage=None, early=False):
    plan, result = fitted_result(engine)
    for name in FITTED_FIELDS:
        del result[name]
    result.update(status="failed", error={"code": code, "stage": stage or ("edgeR_glmQLFTest" if engine == "edger_qlf" else "DESeq2_results"),
        "message": "Synthetic failure evidence"})
    if early:
        result["evidence"] = []
    return plan, result


def fail_gene(result, name="gA", failure_code="native_fit_or_test_failed"):
    result["genes"][name].update(status="failed", failure_code=failure_code, p_value=None, q_value=None)
    result["status"] = "partial_failure"
    refresh_probabilities(result)


def replace(record, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        record = record[key]
    record[keys[-1]] = value


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
def test_complete_synthetic_result_is_usable_only_with_qualification_limits(engine):
    plan, result = fitted_result(engine)
    original = deepcopy(result)
    validation = api().validate_native_result(plan, result)
    assert set(validation) == {"usable", "qualification_limits"}
    assert validation["usable"] is True
    limits = " ".join(validation["qualification_limits"]).lower()
    assert "clinical" in limits and "independence" in limits and "convergence" in limits
    assert "memory" in limits
    assert result == original


@pytest.mark.parametrize("path,value", [
    ("schema_version", "native_nb_result/2"), ("engine", "deseq2_lrt"),
    ("clinical_release_allowed", True), ("clinical_release_allowed", 0),
    ("qualification_status", "qualified"), ("status", "success"),
    ("actual_versions.R", "4.3.2"), ("actual_versions.edgeR", "4.10.4"),
    ("actual_versions.platform", "aarch64-unknown-linux-gnu"),
    ("actual_versions.jsonlite", False), ("elapsed_seconds", -1), ("elapsed_seconds", True),
    ("memory_peak_mb", 50), ("memory_measurement_scope", "measured"),
    ("fdr_scope", "successful_hypotheses_only"), ("convergence_scope", "fully_verified"),
    ("effects.status", "estimated"), ("conditions_truncated", True),
])
def test_result_rejects_wrong_identity_claims_and_scope(path, value):
    plan, result = fitted_result()
    replace(result, path, value)
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("path", ["extra", "actual_versions.extra", "genes.gA.extra", "genes.gA.diagnostics.extra",
    "evidence.extra", "evidence.input_validation.extra", "evidence.input_validation.full_design.extra",
    "evidence.normalization.extra", "evidence.native_settings.extra", "effects.extra"])
def test_result_rejects_unknown_structural_fields(path):
    plan, result = fitted_result()
    replace(result, path, "unknown")
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("field", ["actual_versions", "hypothesis_genes", "genes", "evidence", "conditions",
    "conditions_truncated", "error", "effects", "memory_peak_mb", "qualification_status"])
def test_result_requires_complete_success_fields(field):
    plan, result = fitted_result()
    del result[field]
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("path,value", [
    ("hypothesis_genes", ["gA", "gB", "gC"]),
    ("evidence.input_validation.panel_gene_ids", ["gB", "gA", "gC"]),
    ("evidence.input_validation.sample_ids", ["S1", "S0", "S2", "S3", "S4", "S5"]),
    ("evidence.input_validation.biological_unit_count", 5),
    ("evidence.input_validation.independence_verified", True),
    ("evidence.input_validation.temporal_columns", [2, 4]),
    ("evidence.input_validation.temporal_names", ["Time2", "Time1"]),
    ("evidence.input_validation.design_nesting", "approximately_nested"),
    ("evidence.input_validation.design_selection", "bic"),
    ("evidence.input_validation.full_design.rank", 3),
    ("evidence.input_validation.full_design.rank", True),
    ("evidence.input_validation.full_design.columns", ["Intercept", "Time1", "Time1", "Batch"]),
    ("evidence.input_validation.full_design.columns", ["Intercept", "Time1", "Time2", "bad()"]),
    ("evidence.input_validation.full_design.condition_number", 1001),
    ("evidence.input_validation.full_design.condition_number", 0),
    ("evidence.input_validation.full_design.residual_df", 3),
    ("evidence.input_validation.reduced_design.columns", ["Intercept", "Time1"]),
])
def test_result_validates_order_rank_nesting_and_design_scope(path, value):
    plan, result = fitted_result()
    replace(result, path, value)
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_gene_objects_have_exact_coverage_but_json_object_key_order_is_not_semantic():
    plan, result = fitted_result()
    result["genes"] = dict(reversed(list(result["genes"].items())))
    assert api().validate_native_result(plan, result)["usable"] is True
    canonical_copy = json.loads(json.dumps(result, sort_keys=True))
    assert api().validate_native_result(plan, canonical_copy)["usable"] is True
    del result["genes"]["gA"]
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("path,value", [
    ("method", "TMM"), ("scope", "hypothesis_only"), ("reference_gene_ids", []),
    ("reference_gene_ids", ["gA", "gB"]), ("reference_gene_ids", ["gA", "gA"]),
    ("reference_gene_ids", ["unapproved"]), ("normalization_factors.S0", 0),
    ("normalization_factors.S0", 2), ("effective_library_sizes.S0", -1),
    ("centered_size_factors.S0", 2), ("centered_size_factors.S0", True),
    ("effective_library_sizes.unknown", 1),
])
def test_native_normalization_requires_positive_factors_and_locked_coverage(path, value):
    plan, result = fitted_result()
    replace(result["evidence"]["normalization"], path, value)
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("engine,path,value", [
    ("edger_qlf", "legacy", True), ("edger_qlf", "robust", False),
    ("edger_qlf", "prior_count", 0.125), ("edger_qlf", "winsor_tail_p", [0.1, 0.1]),
    ("edger_qlf", "top_proportion", 1.1), ("deseq2_lrt", "independent_filtering", True),
    ("deseq2_lrt", "beta_maxit", 99), ("deseq2_lrt", "dispersion_fit_type", "parametric"),
    ("deseq2_lrt", "count_outlier_policy", "replace"),
])
def test_changed_native_procedure_is_rejected(engine, path, value):
    plan, result = fitted_result(engine)
    replace(result["evidence"]["native_settings"], path, value)
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
@pytest.mark.parametrize("field,value", [("p_value", -0.1), ("p_value", True), ("q_value", 1.1),
    ("q_value", 0), ("native_p_value", 0.123), ("test_statistic", -1), ("test_statistic", "10"),
    ("test_df_num", 1), ("test_df_num", True), ("test_distribution", "normal")])
def test_accepted_gene_tests_are_consistent_finite_and_native(engine, field, value):
    plan, result = fitted_result(engine)
    result["genes"]["gB"][field] = value
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
def test_p_value_must_match_locked_native_test_distribution(engine):
    plan, result = fitted_result(engine)
    gene = result["genes"]["gB"]
    gene["p_value"] = gene["native_p_value"] = 0.9
    gene["q_value"] = 0.9
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)
    # edgeR/R/glmQLFTest.R in edgeR_4.10.5.tar.gz disables poisson.bound
    # when legacy=False uses df.residual.adj. Other versions/configs are excluded.


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
def test_failed_hypotheses_remain_in_the_bh_denominator(engine):
    plan, result = fitted_result(engine)
    fail_gene(result, "gA")
    validation = api().validate_native_result(plan, result)
    assert validation["usable"] is False
    assert result["genes"]["gA"]["p_value"] is None and result["genes"]["gA"]["q_value"] is None
    survivors = [gene for gene in result["genes"].values() if gene["status"] == "ok"]
    for gene, q in zip(survivors, multipletests([gene["p_value"] for gene in survivors], method="fdr_bh")[1], strict=True):
        gene["q_value"] = float(q)
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_status_and_failure_fields_cannot_contradict_each_other():
    plan, original = fitted_result()
    variations = []
    result = deepcopy(original); result["status"] = "partial_failure"; variations.append(result)
    result = deepcopy(original); fail_gene(result); result["status"] = "ok"; variations.append(result)
    result = deepcopy(original); result["genes"]["gA"]["failure_code"] = "native_fit_or_test_failed"; variations.append(result)
    result = deepcopy(original); fail_gene(result); result["genes"]["gA"]["p_value"] = 1.0; variations.append(result)
    result = deepcopy(original); fail_gene(result); result["genes"]["gA"]["failure_code"] = None; variations.append(result)
    result = deepcopy(original); result["error"] = {"code": "failed"}; variations.append(result)
    for result in variations:
        with pytest.raises(ValueError):
            api().validate_native_result(plan, result)


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
def test_known_nonconvergence_cannot_be_accepted_but_can_be_preserved_as_failed(engine):
    plan, result = fitted_result(engine)
    result["genes"]["gA"]["full_fit_convergence"]["converged"] = False
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)
    fail_gene(result)
    assert api().validate_native_result(plan, result)["usable"] is False


def test_deseq2_requires_known_coefficient_convergence():
    plan, result = fitted_result("deseq2_lrt")
    result["genes"]["gA"]["full_fit_convergence"]["converged"] = None
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_unknown_edger_convergence_and_missing_cooks_remain_visible_limitations():
    plan, result = fitted_result()
    check = api().validate_native_result(plan, result)
    assert any("coefficient convergence" in item.lower() for item in check["qualification_limits"])
    plan, result = fitted_result("deseq2_lrt")
    result["genes"]["gA"]["diagnostics"]["max_cooks_distance"] = {"state": "missing_native_value", "value": None}
    check = api().validate_native_result(plan, result)
    assert check["usable"] is True
    assert any("cook" in item.lower() and "unavailable" in item.lower() for item in check["qualification_limits"])


def test_legitimate_positive_infinite_ql_prior_df_uses_native_capped_f_df():
    plan, result = fitted_result()
    for gene in result["genes"].values():
        gene["diagnostics"]["ql_prior_df"] = {"state": "positive_infinity", "value": None}
        gene["test_df_denom"] = len(plan.panel_genes) * 2
    refresh_probabilities(result)
    assert api().validate_native_result(plan, result)["usable"] is True
    result["genes"]["gA"]["test_df_denom"] -= 1
    refresh_probabilities(result)
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("field,value", [
    ("ql_prior_df", {"state": "positive_infinity", "value": 1}),
    ("nb_dispersion", {"state": "positive_infinity", "value": None}),
    ("nb_dispersion", {"state": "missing_native_value", "value": None}),
    ("nb_dispersion", {"state": "finite", "value": True}),
    ("full_iterations", {"state": "finite", "value": 1.5}),
    ("reduced_iterations", {"state": "not_exposed", "value": 1}),
    ("working_nb_dispersion", finite(0.9)), ("ql_prior_df", {"state": "invented", "value": None}),
])
def test_native_diagnostic_tags_and_equations_are_checked(field, value):
    plan, result = fitted_result()
    result["genes"]["gA"]["diagnostics"][field] = value
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
def test_all_zero_failure_records_retain_null_discoveries(engine):
    plan, result = fitted_result(engine)
    for name in result["genes"]:
        fail_gene(result, name, "all_zero_hypothesis")
        result["genes"][name]["native_p_value"] = None
        result["genes"][name]["test_statistic"] = None
    assert api().validate_native_result(plan, result)["usable"] is False


@pytest.mark.parametrize("engine", ["edger_qlf", "deseq2_lrt"])
def test_complete_engine_failure_is_valid_unusable_evidence(engine):
    plan, result = global_failure(engine)
    check = api().validate_native_result(plan, result)
    assert check["usable"] is False
    assert any("failed" in item.lower() for item in check["qualification_limits"])
    result["evidence"]["input_validation"]["sample_ids"].reverse()
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_explicit_native_version_refusal_is_not_relabelled_malformed_success():
    plan, result = global_failure(code="native_version_mismatch", stage="native_version_check", early=True)
    result["actual_versions"]["R"] = "4.3.2"
    assert api().validate_native_result(plan, result)["usable"] is False
    result["error"]["code"] = "native_or_input_error"
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_unapproved_deseq2_trend_is_valid_only_as_explicit_failed_evidence():
    plan, result = global_failure("deseq2_lrt", code="unapproved_native_fit_type_change", stage="DESeq2_dispersion_trend")
    plan = plan.model_copy(update={"deseq2_fit_type": "parametric"})
    result["evidence"]["dispersion_fit_type"] = {"requested": "parametric", "actual": "local"}
    del result["evidence"]["native_settings"]
    assert api().validate_native_result(plan, result)["usable"] is False
    result["error"]["code"] = "native_or_input_error"
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_restricted_classification_cannot_be_a_usable_native_result():
    plan, result = fitted_result()
    clinical = plan.model_copy(update={"data_classification": "restricted_clinical"})
    with pytest.raises(ValueError):
        api().validate_native_result(clinical, result)
    _, result = global_failure(code="clinical_admission_unconfigured", stage="request_validation", early=True)
    result["engine"] = None
    result["actual_versions"] = {"R": "4.6.1", "platform": "x86_64-pc-linux-gnu"}
    assert api().validate_native_result(clinical, result)["usable"] is False


def test_conditions_are_bounded_retained_and_cannot_be_truncated_successfully():
    plan, result = fitted_result()
    result["conditions"] = [{"stage": "edgeR_glmQLFit", "kind": "warning", "message": "Synthetic optimizer warning"}]
    check = api().validate_native_result(plan, result)
    assert any("condition" in item.lower() for item in check["qualification_limits"])
    result["conditions"][0]["message"] = "x" * 2001
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)
    plan, result = global_failure(code="native_condition_capture_limit_exceeded")
    result["conditions"] = [{"stage": "edgeR_glmQLFit", "kind": "warning", "message": "retained"}] * 256
    result["conditions_truncated"] = True
    assert api().validate_native_result(plan, result)["usable"] is False


@pytest.mark.parametrize("result", [None, [], {"schema_version": "native_nb_result/1"}, {1: "coerced key"}, {"nan": float("nan")}])
def test_malformed_json_values_raise_value_error(result):
    with pytest.raises(ValueError):
        api().validate_native_result(analysis_plan(), result)


def test_unvalidated_plan_copy_cannot_bypass_result_admission():
    plan, result = fitted_result()
    plan = plan.model_copy(update={"engine": "arbitrary-engine"})
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


@pytest.mark.parametrize("path", ["status", "genes.gA.status", "genes.gA.failure_code",
    "evidence.dispersion_fit_type.actual"])
def test_malformed_nested_types_raise_value_error_not_typeerror(path):
    plan, result = fitted_result("deseq2_lrt")
    replace(result, path, ["not", "a", "scalar"])
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)


def test_malformed_condition_stage_raises_value_error():
    plan, result = fitted_result()
    result["conditions"] = [{"stage": ["unhashable"], "kind": "warning", "message": "synthetic"}]
    with pytest.raises(ValueError):
        api().validate_native_result(plan, result)
