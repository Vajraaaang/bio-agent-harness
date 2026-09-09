"""Statistical acceptance tests use synthetic data and supervised spawned fits."""

import json

import numpy as np
import pandas as pd
import pytest


def _dataset(tmp_path, values, stages, batch=None, suffix="csv"):
    from devharness_core.storage import ChunkedExpressionStore

    samples = [f"s{i}" for i in range(len(stages))]
    genes = [f"g{i}" for i in range(len(values))]
    counts = tmp_path / f"counts.{suffix}"
    frame = pd.DataFrame(values, index=genes, columns=samples)
    writer = (ChunkedExpressionStore.from_dataframe_to_csv if suffix == "csv"
              else ChunkedExpressionStore.from_dataframe_to_hdf5)
    writer(frame, counts).close()
    metadata = tmp_path / "metadata.csv"
    pd.DataFrame({"sample_id": samples, "embryonic_age": stages,
                  "batch": batch if batch is not None else np.tile(["a", "b"], len(stages) // 2)
                  }).to_csv(metadata, index=False)
    return str(counts), str(metadata), genes


def _fit(paths, genes=None, **kwargs):
    from devharness_server.tools_stat import fit_temporal_trajectory

    counts, metadata, names = paths
    return fit_temporal_trajectory(counts, metadata, "1 + numeric_time + C(batch)",
                                   "embryonic_age", genes or names[:2], **kwargs)


def test_depth_offsets_remove_spurious_temporal_fold_change(tmp_path):
    rng = np.random.default_rng(412)
    stages = np.repeat(np.arange(6), 8)
    depth = np.repeat([0.5, 0.7, 1, 1.4, 2, 4], 8)
    means = np.exp(rng.uniform(5, 7, (300, 1))) * depth
    alpha = 0.04
    counts = rng.negative_binomial(1 / alpha, 1 / (1 + alpha * means))
    result = _fit(_dataset(tmp_path, counts, stages), spline_dfs=[2, 3])
    factors = np.array(list(result["size_factors"].values()))
    expected = depth / np.exp(np.log(depth).mean())
    assert factors == pytest.approx(expected, rel=0.12)
    assert result["normalization_method"] == "median_of_ratios"
    assert abs(result["genes"]["g0"]["log2_fold_change"]) < 0.6
    assert abs(np.log2(counts[0, -8:].mean() / counts[0, :8].mean())) > 2
    assert result["normalization"]["reference_gene_count"] == 300
    assert result["dispersion_calibration"]["n_cpus"] == 1
    assert result["dispersion_calibration"]["low_memory"] is True
    json.dumps(result, allow_nan=False)


def test_empirical_bayes_dispersions_are_data_dependent_and_shrink(tmp_path):
    rng = np.random.default_rng(822)
    stages = np.repeat(np.arange(4), 8)
    truth = np.exp(rng.normal(np.log(0.18), 0.5, 250))
    means = np.exp(rng.uniform(4, 7, (250, 1))) * np.ones((1, len(stages)))
    counts = rng.negative_binomial(1 / truth[:, None], 1 / (1 + truth[:, None] * means))
    paths = _dataset(tmp_path, counts, stages)
    result = _fit(paths, paths[2][:80], spline_dfs=[2])
    raw = np.array([g["genewise_dispersion"] for g in result["genes"].values()])
    final = np.array([g["dispersion_alpha"] for g in result["genes"].values()])
    trend = np.array([g["trend_dispersion"] for g in result["genes"].values()])
    assert np.std(final) > 0.02
    assert np.corrcoef(final, truth[:80])[0, 1] > 0.5
    assert np.mean(abs(np.log(final / trend)) < abs(np.log(raw / trend))) > 0.7
    assert np.mean(np.log(final / truth[:80]) ** 2) < np.mean(np.log(raw / truth[:80]) ** 2)
    assert result["dispersion_calibration"]["method"] == "pydeseq2_empirical_bayes"
    assert result["fdr_scope"] == "requested_genes"
    assert all(0 <= g["p_value"] <= g["padj"] <= 1 for g in result["genes"].values())


@pytest.mark.parametrize("suffix", ["csv", "h5"])
def test_integral_float_expression_is_not_automatically_count_normalized(tmp_path, suffix):
    rng = np.random.default_rng(42)
    stages = np.repeat(np.arange(3), 4)
    values = rng.poisson(30, (3, len(stages))).astype(float)
    result = _fit(_dataset(tmp_path, values, stages, suffix=suffix))
    assert result["family"] == "gaussian"
    assert result["normalization_method"] == "none"
    assert result["size_factors"] is None
    assert result["dispersion_calibration"] is None


def test_three_stage_data_uses_feasible_two_df_spline(tmp_path):
    rng = np.random.default_rng(93)
    stages = np.repeat(np.arange(3), 6)
    result = _fit(_dataset(tmp_path, stages[None, :] ** 2 + rng.normal(0, 0.1, (2, 18)), stages))
    for gene in result["genes"].values():
        assert gene["selected_df"] == 2
        assert gene["degrees_of_freedom"] == 2
        assert {score["df"] for score in gene["candidate_scores"]} == {2, 3, 4, 5}
        assert all(score["status"] == "infeasible" for score in gene["candidate_scores"] if score["df"] > 2)


@pytest.mark.parametrize("criterion", ["aic", "bic", "gcv"])
def test_nonlinear_continuous_curve_selects_flexible_spline(tmp_path, criterion):
    rng = np.random.default_rng(109)
    stages = np.repeat(np.arange(8), 8)
    values = 10 + 5 * np.sin(2 * np.pi * stages[None, :] / 7) + rng.normal(0, .2, (2, 64))
    result = _fit(_dataset(tmp_path, values, stages), selection_criterion=criterion)
    for gene in result["genes"].values():
        assert gene["selected_df"] >= 3
        candidates = [x for x in gene["candidate_scores"] if x["status"] == "ok"]
        assert gene["selected_df"] == min(candidates, key=lambda x: (x[criterion], x["df"]))["df"]
        assert gene["p_value"] < 1e-8
    assert "selection" in result["inference_scope"].lower()


def test_fractional_values_rejected_when_explicitly_claimed_counts(tmp_path):
    rng = np.random.default_rng(42)
    paths = _dataset(tmp_path, rng.uniform(1, 20, (2, 18)), np.repeat(np.arange(3), 6))
    with pytest.raises(Exception, match="integer"):
        _fit(paths, data_type="counts")


def test_calibration_panel_is_bounded_and_includes_requested_genes(tmp_path):
    rng = np.random.default_rng(789)
    stages = np.repeat(np.arange(4), 6)
    counts = rng.negative_binomial(5, .04, size=(350, len(stages)))
    paths = _dataset(tmp_path, counts, stages, suffix="h5")
    result = _fit(paths, ["g349"], calibration_max_genes=80, spline_dfs=[2])
    calibration = result["dispersion_calibration"]
    assert len(calibration["gene_ids"]) == 80
    assert "g349" in calibration["gene_ids"]
    assert calibration["panel_selection"] == "evenly_spaced_plus_requested"
    assert result["normalization"]["reference_gene_count"] <= 80
    assert any("panel" in warning.lower() for warning in result["warnings"])


def test_batch_partial_r_squared_and_confounding_diagnostics(tmp_path):
    from devharness_server.tools_stat import analyze_batch_effects

    rng = np.random.default_rng(51)
    stages = np.repeat(np.arange(4), 8)
    batch = np.tile(["a", "b"], 16)
    values = 10 + 20 * (batch[None, :] == "b") + rng.normal(0, 1, (3, 32))
    counts, metadata, genes = _dataset(tmp_path, values, stages, batch=batch)
    result = analyze_batch_effects(counts, metadata, gene_list=genes)
    assert result["identifiable"] is True
    assert all(x["batch_partial_r2"] > .95 for x in result["genes"].values())
    assert all(x["time_partial_r2"] < .5 for x in result["genes"].values())
    frame = pd.read_csv(metadata)
    frame["batch"] = frame["embryonic_age"].astype(str)
    frame.to_csv(metadata, index=False)
    confounded = analyze_batch_effects(counts, metadata, gene_list=genes)
    assert confounded["identifiable"] is False
    assert confounded["design"]["is_full_rank"] is False
    assert all(x["batch_partial_r2"] is None for x in confounded["genes"].values())
    assert "causal" in confounded["inference_scope"].lower()


def test_nonfinite_input_and_confounding_fail_explicitly(tmp_path):
    rng = np.random.default_rng(44)
    stages = np.repeat(np.arange(3), 6)
    values = rng.normal(10, 1, (2, 18))
    paths = _dataset(tmp_path, values, stages, batch=stages.astype(str))
    with pytest.raises(Exception, match="Rank-deficient"):
        _fit(paths)
    values[0, 1] = np.nan
    paths = _dataset(tmp_path, values, stages)
    with pytest.raises(Exception, match="finite"):
        _fit(paths)


def _continuous_without_count_backend_worker(counts, metadata):
    import sys
    import devharness_server.normalization as normalization
    from devharness_server.tools_stat import _fit_worker

    def forbidden(*args, **kwargs):
        raise AssertionError("Continuous data entered the count calibration backend")
    normalization.calibrate_counts = forbidden
    result = _fit_worker(counts, metadata, "1 + numeric_time + C(batch)", "embryonic_age", ["g0"])
    return result["family"], any(name.startswith("pydeseq2") for name in sys.modules)


def test_continuous_worker_never_imports_or_calls_pydeseq2(tmp_path):
    from devharness_core.watchdog import run_sandboxed

    rng = np.random.default_rng(53)
    paths = _dataset(tmp_path, rng.uniform(1, 10, (2, 18)), np.repeat(np.arange(3), 6))
    assert run_sandboxed(_continuous_without_count_backend_worker, paths[:2]) == ("gaussian", False)


def _dispersion_retry_worker():
    from pydeseq2.utils import grid_fit_alpha
    from devharness_server.normalization import _retry_dispersion

    rng = np.random.default_rng(567)
    counts = rng.negative_binomial(1.25, 1 / 81, size=40)
    mu = np.repeat(100.0, 40)
    design = np.ones((40, 1))
    raw, _ = _retry_dispersion(counts, design, mu, .1, 1e-8, 40)
    shrunk, record = _retry_dispersion(counts, design, mu, .03, 1e-8, 40,
                                      prior_disp_var=.05, prior_reg=True)
    reference = np.exp(grid_fit_alpha(counts, design, mu, .03, 1e-8, 40,
                                      prior_disp_var=.05, prior_reg=True, grid_length=300))
    return raw, shrunk, reference, record


def test_scalar_retry_preserves_map_prior_and_matches_library_objective():
    from devharness_core.watchdog import run_sandboxed

    raw, shrunk, reference, record = run_sandboxed(_dispersion_retry_worker)
    assert shrunk < raw / 2
    assert shrunk == pytest.approx(reference, rel=.005)
    assert record["prior_penalty_included"] is True
    assert record["objective"] <= record["grid_minimum_objective"]


def _candidate_failure_worker(counts, metadata, strict, fail_all):
    import devharness_server.tools_stat as stats

    original = stats._fit_candidate
    def injected(values, design, alpha, offset):
        if fail_all or design.shape[1] == 4:
            raise ValueError("glm_nonconvergence: injected reproducible optimizer failure")
        return original(values, design, alpha, offset)
    stats._fit_candidate = injected
    return stats._fit_worker(counts, metadata, "1 + numeric_time + C(batch)", "embryonic_age", ["g0"], strict=strict)


def test_candidate_failures_are_explicit_and_strict_mode_rejects(tmp_path):
    from devharness_core.watchdog import run_sandboxed

    rng = np.random.default_rng(582)
    paths = _dataset(tmp_path, rng.uniform(1, 10, (2, 24)), np.repeat(np.arange(4), 6))
    with pytest.raises(Exception, match="candidate_fit_failed"):
        run_sandboxed(_candidate_failure_worker, (*paths[:2], True, False))
    partial = run_sandboxed(_candidate_failure_worker, (*paths[:2], False, False))
    assert partial["status"] == "partial_failure"
    assert partial["genes"]["g0"]["candidate_scores"][0]["status"] == "failed"
    assert any(d["code"] == "candidate_fit_failed" and d["severity"] == "error" for d in partial["diagnostics"])
    failed = run_sandboxed(_candidate_failure_worker, (*paths[:2], False, True))
    assert failed["genes"]["g0"]["status"] == "failed"
    assert failed["genes"]["g0"]["p_value"] is None
    assert failed["genes"]["g0"]["padj"] is None


def test_no_information_gene_is_distinguished_from_fit_failure(tmp_path):
    values = np.full((2, 18), 12.5)
    result = _fit(_dataset(tmp_path, values, np.repeat(np.arange(3), 6)))
    assert result["genes"]["g0"]["status"] == "no_information"
    assert result["genes"]["g0"]["p_value"] == 1
    assert result["genes"]["g0"]["diagnostics"][0]["code"] == "no_expression_information"
    json.dumps(result, allow_nan=False)


def test_clinical_time_column_is_consistent_in_validation_and_fit(tmp_path):
    from devharness_server.tools_stat import fit_temporal_trajectory, validate_design_matrix

    rng = np.random.default_rng(914)
    stages = np.repeat([0, 7, 14, 21], 6)
    counts, metadata, _ = _dataset(tmp_path, rng.normal(20, 2, (2, 24)), stages)
    frame = pd.read_csv(metadata).rename(columns={"embryonic_age": "days_since_enrollment"})
    frame["baseline_score"] = rng.normal(0, 1, len(frame))
    frame.to_csv(metadata, index=False)
    formula = "1 + numeric_time + C(batch) + baseline_score"
    assert validate_design_matrix(metadata, formula, time_col="days_since_enrollment")["is_full_rank"]
    result = fit_temporal_trajectory(counts, metadata, formula, "days_since_enrollment", ["g0"],
                                     selection_criterion="fixed", spline_dfs=[2])
    assert "baseline_score" in result["genes"]["g0"]["coefficients"]
    assert result["genes"]["g0"]["time_start"] == 0
    assert result["genes"]["g0"]["time_end"] == 21


@pytest.mark.parametrize("expression", ["__import__('os').system('true')", "numeric_time.__class__",
                                        "numeric_time[0]", "(lambda: 1)()", "sum(numeric_time)"])
def test_formula_rejects_executable_or_unapproved_python(tmp_path, expression):
    from devharness_server.tools_stat import validate_design_matrix

    metadata = tmp_path / "metadata.csv"
    pd.DataFrame({"embryonic_age": [0, 1, 2, 3], "batch": ["a", "b", "a", "b"]}).to_csv(metadata, index=False)
    with pytest.raises(Exception, match="unsafe_formula"):
        validate_design_matrix(str(metadata), "1 + numeric_time + I(" + expression + ")")


def test_formula_cannot_create_a_file_and_clinical_arithmetic_still_works(tmp_path):
    from devharness_server.tools_stat import validate_design_matrix

    marker = tmp_path / "forbidden.txt"
    metadata = tmp_path / "metadata.csv"
    pd.DataFrame({"embryonic_age": [0, 1, 2, 3, 4, 5], "age": [40, 32, 35, 61, 44, 21],
                  "treatment": ["a", "b", "a", "b", "a", "b"]}).to_csv(metadata, index=False)
    expression = "1 + numeric_time + I(__import__('pathlib').Path(" + repr(str(marker)) + ").write_text('unsafe'))"
    with pytest.raises(Exception, match="unsafe_formula"):
        validate_design_matrix(str(metadata), expression)
    assert not marker.exists()
    result = validate_design_matrix(str(metadata), "1 + numeric_time + age + C(treatment) + I(age ** 2)")
    assert result["is_full_rank"]


def _calibration_mean_failure_worker(counts, metadata, strict):
    from pydeseq2.default_inference import DefaultInference
    from devharness_server.tools_stat import _fit_worker

    original = DefaultInference.irls
    def injected(self, *args, **kwargs):
        beta, mu, leverage, converged = original(self, *args, **kwargs)
        return beta, mu, leverage, np.zeros_like(converged, dtype=bool)
    DefaultInference.irls = injected
    return _fit_worker(counts, metadata, "1 + numeric_time + C(batch)", "embryonic_age", ["g0"],
                       spline_dfs=[2], strict=strict)


def test_dispersion_calibration_mean_nonconvergence_is_not_silently_accepted(tmp_path):
    from devharness_core.watchdog import run_sandboxed

    rng = np.random.default_rng(165)
    paths = _dataset(tmp_path, rng.negative_binomial(5, .08, (30, 24)), np.repeat(np.arange(4), 6))
    with pytest.raises(Exception, match="dispersion_mean_nonconvergence"):
        run_sandboxed(_calibration_mean_failure_worker, (*paths[:2], True))
    partial = run_sandboxed(_calibration_mean_failure_worker, (*paths[:2], False))
    assert partial["status"] == "partial_failure"
    assert partial["genes"]["g0"]["status"] == "failed"
    assert partial["genes"]["g0"]["p_value"] is None
    assert partial["genes"]["g0"]["padj"] is None


def test_batch_numerical_overflow_never_returns_nonfinite_success(tmp_path):
    from devharness_server.tools_stat import analyze_batch_effects

    stages = np.repeat(np.arange(4), 6)
    values = np.tile([1e308, -1e308], (2, 12))
    counts, metadata, genes = _dataset(tmp_path, values, stages, suffix="h5")
    with pytest.raises(Exception, match="nonfinite_statistical_output"):
        analyze_batch_effects(counts, metadata, gene_list=genes)


def _pseudoreplication_guard_worker(counts, metadata, strict):
    import devharness_server.tools_stat as stats
    import devharness_server.normalization as normalization

    def forbidden(*args, **kwargs):
        raise AssertionError("Model fitting began before the biological-unit guard")
    normalization.calibrate_counts = forbidden
    stats._fit_candidate = forbidden
    return stats._fit_worker(counts, metadata, "1 + numeric_time + C(batch)", "embryonic_age",
                              ["g0", "g1"], strict=strict)


@pytest.mark.parametrize("unit_column", ["specimen_id", "subject_id", "donor_id"])
@pytest.mark.parametrize("discrete", [False, True])
def test_repeated_biological_units_block_all_inference_before_fitting(tmp_path, unit_column, discrete):
    from devharness_core.watchdog import run_sandboxed

    rng = np.random.default_rng(266)
    values = rng.poisson(30, (2, 18)) if discrete else rng.normal(30, 2, (2, 18))
    counts, metadata, _ = _dataset(tmp_path, values, np.repeat(np.arange(3), 6))
    frame = pd.read_csv(metadata)
    frame[unit_column] = np.tile([f"unit{i}" for i in range(6)], 3)
    frame.to_csv(metadata, index=False)
    with pytest.raises(Exception, match="pseudoreplication_requires_aggregation_or_repeated_measures_model"):
        run_sandboxed(_pseudoreplication_guard_worker, (counts, metadata, True))
    result = run_sandboxed(_pseudoreplication_guard_worker, (counts, metadata, False))
    assert result["status"] == "failed"
    assert result["model_fitting_performed"] is False
    assert result["independence_assumption"]["repeated_unit_columns"] == [unit_column]
    assert result["dispersion_calibration"] is None
    for gene in result["genes"].values():
        assert gene["status"] == "failed"
        assert gene["p_value"] is gene["padj"] is gene["q_value"] is None
    assert any(d["code"] == "pseudoreplication_requires_aggregation_or_repeated_measures_model"
               and d["severity"] == "error" for d in result["diagnostics"])


def test_unique_specimens_are_accepted_and_undeclared_units_are_disclosed(tmp_path):
    rng = np.random.default_rng(267)
    paths = _dataset(tmp_path, rng.normal(30, 2, (2, 18)), np.repeat(np.arange(3), 6))
    unknown = _fit(paths)
    assert unknown["status"] == "ok"
    assert unknown["independence_assumption"]["verification_status"] == "unknown_unit_provenance"
    assert any(d["code"] == "unknown_biological_unit_provenance" for d in unknown["diagnostics"])
    frame = pd.read_csv(paths[1])
    frame["specimen_id"] = [f"specimen{i}" for i in range(len(frame))]
    frame.to_csv(paths[1], index=False)
    known = _fit(paths)
    assert known["status"] == "ok"
    assert known["independence_assumption"]["verification_status"] == "declared_unique_units"
    assert known["independence_assumption"]["declared_unit_columns"] == ["specimen_id"]
    assert not any(d["code"] == "unknown_biological_unit_provenance" for d in known["diagnostics"])


def test_unique_specimen_does_not_override_a_repeated_subject(tmp_path):
    rng = np.random.default_rng(268)
    paths = _dataset(tmp_path, rng.normal(30, 2, (2, 18)), np.repeat(np.arange(3), 6))
    frame = pd.read_csv(paths[1])
    frame["specimen_id"] = [f"specimen{i}" for i in range(len(frame))]
    frame["subject_id"] = np.tile([f"subject{i}" for i in range(6)], 3)
    frame.to_csv(paths[1], index=False)
    with pytest.raises(Exception, match="pseudoreplication_requires_aggregation_or_repeated_measures_model"):
        _fit(paths)


def test_unknown_unit_sentinel_and_unverified_annotation_are_not_verified_animals():
    from devharness_server.tools_stat import _biological_unit_diagnostics
    unknown = pd.DataFrame({'specimen_id': ['unknown', 'unknown']})
    assumption, diagnostics = _biological_unit_diagnostics(unknown)
    assert assumption['repeated_unit_columns'] == []
    assert assumption['verification_status'] == 'unknown_unit_provenance'
    labeled = pd.DataFrame({'specimen_id':['s1','s2'], 'specimen_provenance':['independent identity unverified']*2})
    assumption, diagnostics = _biological_unit_diagnostics(labeled)
    assert assumption['verification_status'] == 'unknown_unit_provenance'
    assert any(d['code']=='unknown_biological_unit_provenance' for d in diagnostics)
