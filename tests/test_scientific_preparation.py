"""Independent small numerical/contract controls; no participant or cloud calls."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("outside_count", [2, 20])
def test_enrichment_effect_and_probability_share_measured_universe(outside_count):
    from scipy.stats import fisher_exact
    from devbench.study.biology_tools_singlecell import _enrichment

    genes = tuple(f"g{i}" for i in range(16))
    # Measured table [[8, 2], [1, 5]] has sample OR=20. Adding unmeasured
    # annotation members must change neither that table nor its probability.
    members = [*genes[:8], genes[10], *[f"unmeasured_{i}" for i in range(outside_count)]]
    data = SimpleNamespace(gene_ids=genes, context={"enrichment_query_gene_ids": list(genes[:10]),
        "enrichment_background_gene_ids": list(genes), "enrichment_query_selection": {"query_count": 10}},
        annotations={"regulon_annotation": {"version": "fixture-1", "memberships": [
            {"tf_id": "TF", "target_id": gene} for gene in members]}})
    target = {"target_id": "regulon_membership", "estimand": "Fixed query annotation overlap",
        "contrast": "Query versus measured background", "units": "sample odds ratio",
        "reference_population": "Measured genes", "prediction_reference": "Fixed query"}
    diagnostics = {}
    result = _enrichment(data, target, {}, diagnostics)["payload"]["enrichment_results"][0]
    reference = fisher_exact([[8, 2], [1, 5]], alternative="greater")
    assert result["odds_ratio"] == pytest.approx(reference.statistic)
    assert result["p_value"] == pytest.approx(reference.pvalue)
    assert result["odds_ratio_status"] == "finite"
    assert diagnostics["reference_genes_outside_background"] == outside_count


def effect_inputs(*, intervals=(), values=(1.0,), fixed=()):
    from devbench.study.biology_answers import EffectsPayload, HypothesisEstimate
    from devbench.study.biology_scoring import HypothesisReference, TargetReference

    coordinates = tuple(f"grid_{i}" for i in range(len(values)))
    target = TargetReference(target_id="sampled_mean", estimand="Temporal differences", units="expression units",
        contrast="At time minus reference time", reference_population="Independent donors", prediction_reference="Observed grid",
        scope="supported", evidence_scope="population_mean", numerical_reference_available=True,
        metric_ids=("interval_coverage",), measurement="continuous", hypotheses=(HypothesisReference(
            hypothesis_id="g", gene_id="g", coordinate_ids=coordinates, values=values, nonnull=True),))
    if fixed:
        target = target.model_copy(update={"fixed_effect_coordinate_ids": fixed})
    row = HypothesisEstimate(hypothesis_id="g", gene_id="g", estimate_status="estimated",
        coordinate_ids=coordinates, values=values, p_value=.01, q_value=.01, intervals=intervals)
    payload = EffectsPayload(analysis_performed=True, gene_statistics=(row,), multiplicity_scope=("g",),
        selection_conditional_inference=False, biological_unit="donor", analysis_layer="continuous",
        effect_definition=target.contrast)
    return target, payload


@pytest.mark.parametrize("intervals", [(), (None,), ({"lower": .5, "upper": 1.5},),
    ({"lower": .5, "upper": 1.5, "confidence_level": .5},)])
def test_required_uncertainty_cannot_be_omitted_or_lowered(intervals):
    from devbench.study.biology_scoring import _effect_metrics, BiologyScoringThresholds
    target, payload = effect_inputs(intervals=intervals)
    metrics, errors, _, success = _effect_metrics(target, payload, BiologyScoringThresholds())
    assert success is False
    assert metrics["interval_availability"] == 0
    assert "required_pointwise_intervals_missing_or_wrong_level" in errors


def test_fixed_reference_differences_do_not_inflate_interval_coverage():
    from devbench.study.biology_scoring import _effect_metrics, BiologyScoringThresholds
    target, payload = effect_inputs(values=(0.0, 1.0), fixed=("grid_0",), intervals=(
        {"lower": 0.0, "upper": 0.0, "confidence_level": .95},
        {"lower": 2.0, "upper": 3.0, "confidence_level": .95}))
    metrics, _, _, success = _effect_metrics(target, payload, BiologyScoringThresholds())
    assert metrics["interval_coverage"] == 0.0
    assert metrics["interval_expected_coordinate_count"] == 1
    assert metrics["interval_fixed_coordinates_excluded"] == 1
    assert success is False


def test_full_pointwise_uncertainty_remains_eligible():
    from devbench.study.biology_scoring import _effect_metrics, BiologyScoringThresholds
    target, payload = effect_inputs(intervals=({"lower": .5, "upper": 1.5, "confidence_level": .95},))
    metrics, errors, _, success = _effect_metrics(target, payload, BiologyScoringThresholds())
    assert metrics["interval_availability"] == 1.0 and metrics["interval_coverage"] == 1.0
    assert not errors and success is True


def test_bh_reserves_failed_family_positions_without_inventing_estimates():
    from statsmodels.stats.multitest import multipletests
    from devbench.study.biology_answers import HypothesisEstimate
    from devbench.study.biology_scoring import _effect_metrics, BiologyScoringThresholds, HypothesisReference

    target, payload = effect_inputs(intervals=({"lower": .5, "upper": 1.5, "confidence_level": .95},))
    reference_q = multipletests([.01, 1.0], method="fdr_bh")[1][0]
    target = target.model_copy(update={"hypotheses": (*target.hypotheses,
        HypothesisReference(hypothesis_id="failed", gene_id="failed", coordinate_ids=("grid_0",), values=(0.,), nonnull=False))})
    payload = payload.model_copy(update={"gene_statistics": (
        payload.gene_statistics[0].model_copy(update={"q_value": float(reference_q)}),
        HypothesisEstimate(hypothesis_id="failed", gene_id="failed", estimate_status="failed", missing_reason="nonconvergence"))})
    metrics, errors, _, success = _effect_metrics(target, payload, BiologyScoringThresholds())
    assert metrics["bh_arithmetic_valid"] is True
    assert "bh_adjustment_inconsistent" not in errors
    assert payload.gene_statistics[1].p_value is None
    assert success is False  # Missing scientific estimates still count against the task.


def test_count_gcv_is_rejected_before_any_model_fit(monkeypatch):
    from devbench.study import biology_tools_bulk as methods
    from devbench.study.biology_tool_io import ObservedData
    import numpy as np
    import pandas as pd

    data = ObservedData(expression=np.ones((2, 8), dtype=int), gene_ids=("a", "b"),
        sample_ids=tuple(f"s{i}" for i in range(8)), metadata=pd.DataFrame(), annotations={},
        measurement="raw_counts", unit_hierarchy={}, targets=(), provenance={})
    monkeypatch.setattr(methods, "_independent_view", lambda *a, **kw: (data, {}))
    monkeypatch.setattr(methods, "_mode_columns", lambda *a: [])
    monkeypatch.setattr(methods, "_multiplicity_provenance", lambda *a: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported criterion must be refused before fitting")
    monkeypatch.setattr(methods, "_design", forbidden)
    with pytest.raises(ValueError, match="gcv_not_available_for_negative_binomial_likelihood"):
        methods._fit_effect_targets(data, [], "temporal", {"selection_criterion": "gcv"}, {})


def test_no_actual_tests_does_not_report_valid_bh_arithmetic():
    from devbench.study.biology_answers import HypothesisEstimate
    from devbench.study.biology_scoring import _effect_metrics, BiologyScoringThresholds
    target, payload = effect_inputs()
    payload = payload.model_copy(update={"multiplicity_scope": (), "gene_statistics": (
        HypothesisEstimate(hypothesis_id="g", gene_id="g", estimate_status="failed", missing_reason="nonconvergence"),)})
    metrics, _, _, success = _effect_metrics(target, payload, BiologyScoringThresholds())
    assert metrics["bh_arithmetic_valid"] is None
    assert metrics["interval_availability"] == 0
    assert success is False


def test_public_uncertainty_contract_uses_only_declared_design():
    from devbench.study.biology_answers import public_output_layout
    task = {"measurement": "continuous", "analysis_reference_time": 1.0,
        "context": {"design": {"time_grid": [0., 1., 2.], "condition_levels": ["A", "B"]}},
        "questions": [{"target_id": "sampled_mean"}]}
    result = public_output_layout(task)["sampled_mean"]["uncertainty_contract"]
    assert result["confidence_level"] == .95
    assert result["fixed_effect_coordinate_ids"] == ["grid_1", "grid_4"]
    assert result["interval_scope"] == "pointwise"
    assert public_output_layout(task)["sampled_mean"]["multiplicity_contract"]["family"] == "all_declared_hypothesis_rows"


def test_actual_gaussian_effect_agrees_with_independent_two_sample_test():
    import numpy as np
    import pandas as pd
    from scipy import linalg, stats
    from devbench.study.biology_tool_io import ObservedData
    from devbench.study.biology_tools_bulk import _fit_effect_targets

    # One small fixture: two independent groups on a continuous declared scale.
    # No synthetic generator, private reference, or grading result supplies truth.
    values = np.array([2, 3, 1, 5, 2, 4, 3, 1, 6, 4, 5, 7, 8, 4, 6, 5], dtype=float)
    group = np.repeat([0., 1.], 8)
    ids = tuple(f"s{i}" for i in range(16))
    metadata = pd.DataFrame({"condition": np.where(group, "B", "A"), "donor_id": ids,
        "specimen_id": ids, "library_id": ids}, index=ids)
    target = {"target_id": "mean_contrast", "estimand": "Difference of means", "measurement_units": "declared units",
        "contrast": "B minus A", "reference_population": "Independent donors", "prediction_reference": "Group means"}
    data = ObservedData(expression=values[None, :], gene_ids=("gene",), sample_ids=ids, metadata=metadata,
        annotations={}, measurement="continuous", targets=(target,), provenance={},
        unit_hierarchy={"population_unit": "donor", "population_unit_label": "donor_id", "specimen_label": "specimen_id",
                        "library_label": "library_id", "identity_status": "declared_synthetic"})
    diagnostics = {}
    result = _fit_effect_targets(data, [target], "condition", {}, diagnostics)[0]["payload"]["gene_statistics"][0]
    independent_test = stats.ttest_ind(values[8:], values[:8], equal_var=True)
    assert result["values"][0] == pytest.approx(values[8:].mean() - values[:8].mean())
    assert result["p_value"] == pytest.approx(independent_test.pvalue)
    interval = result["intervals"][0]
    independent_interval = independent_test.confidence_interval(confidence_level=.95)
    assert (interval["lower"], interval["upper"]) == pytest.approx((independent_interval.low, independent_interval.high))
    # Independent LAPACK least squares and the Gaussian ML likelihood verify
    # the recorded criterion, including the fitted residual-variance parameter.
    design = np.column_stack([np.ones(16), group])
    coefficients = linalg.lstsq(design, values)[0]
    variance_ml = np.mean((values - design @ coefficients) ** 2)
    likelihood = np.sum(stats.norm.logpdf(values, loc=design @ coefficients, scale=np.sqrt(variance_ml)))
    scores = diagnostics["model_selection"]["candidate_scores"]["gene"][0]
    assert scores["aic"] == pytest.approx(-2 * likelihood + 2 * 3)
    assert scores["bic"] == pytest.approx(-2 * likelihood + np.log(16) * 3)


@pytest.mark.parametrize("missing_scan", [False, True])
def test_motif_reference_distinguishes_positive_negative_and_unscanned_genes(missing_scan):
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_scoring import build_biology_reference
    genes = ("a", "b", "c", "d")
    hits = [{"tf_id": "TF", "target_id": gene, "hit_count": int(gene in {"a", "b"})} for gene in genes]
    hits.append({"tf_id": "TF", "target_id": "outside", "hit_count": 1})
    if missing_scan:
        hits = [row for row in hits if row["target_id"] != "d"]
    reference = build_biology_reference(template=load_biology_catalog().template("T19"),
        private_truth={"targets": {"motif_occurrence": {"scope": "supported", "numerical_reference_available": True}}},
        public_context={"measurement": "umi_counts", "biological_unit": "donor",
                        "enrichment_background_gene_ids": genes, "enrichment_query_gene_ids": ("a",)},
        public_annotations={"motif_hit_annotation": {"hits": hits}}, task_id="test", input_sha256="0"*64,
        gene_ids=genes, allow_result_analysis=True)
    target = next(target for target in reference.targets if target.target_id == "motif_occurrence")
    if missing_scan:
        assert target.numerical_reference_available is False
        assert target.unsupported_reference_reason == "incomplete_motif_background_scan"
    else:
        assert target.memberships[0].gene_ids == ("a", "b")
