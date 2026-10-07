"""Bounded genuine NB software verification, not an agent evaluation campaign."""
import importlib.util

import numpy as np
import pandas as pd
import pytest


def _run(counts, metadata, *, arm="design_aware", provenance=None):
    return _wrapper(counts, metadata, arm=arm, provenance=provenance)["payload"]


def _wrapper(counts, metadata, *, arm="design_aware", provenance=None):
    assert importlib.util.find_spec("benchmarks.discovery_baselines") is not None, "discovery baseline API is absent"
    from benchmarks.discovery_baselines import run_baseline
    return run_baseline(counts, metadata, arm=arm, provenance=provenance or _provenance(metadata))


def _provenance(metadata):
    return {
        "representation": "raw_counts",
        "metadata_roles": {role: role for role in ("condition", "sex", "batch") if role in metadata},
        "biological_independence": {"status": "assumed_independent", "unit": "specimen",
                                    "source_kind": "assistant_fixture_declaration"},
        "kind": "synthetic_bulk_count_generation", "scope": "software_verification",
    }


def _fixture(*, sex=False, batch=False, null=False):
    rng = np.random.default_rng(881)
    n = 24
    metadata = pd.DataFrame({"sample_id": [f"s{i}" for i in range(n)],
                             "condition": ["control", "treated"] * 12})
    if sex:
        metadata["sex"] = ["female"] * 12 + ["male"] * 12
    if batch:
        metadata["batch"] = ["batch_1"] * 6 + ["batch_2"] * 12 + ["batch_1"] * 6
    means = np.full((60, n), 200.0)
    treated = (metadata.condition == "treated").to_numpy()
    if not null:
        if sex:
            female = (metadata.sex == "female").to_numpy()
            means[:3, treated & female] *= 8
            means[:3, treated & ~female] /= 8
        else:
            means[:3, treated] *= 8
    if batch:
        means[3:6, (metadata.batch == "batch_2").to_numpy()] *= 8
    counts = rng.negative_binomial(50, 50 / (50 + means))
    return pd.DataFrame(counts, index=[f"g{i}" for i in range(60)], columns=metadata.sample_id), metadata


@pytest.mark.parametrize("bad", [-1.0, 1.5, np.nan, np.inf])
def test_rejects_invalid_counts_before_fit(bad):
    counts, metadata = _fixture()
    counts = counts.astype(float)
    counts.iloc[0, 0] = bad
    with pytest.raises(ValueError, match="count"):
        _run(counts, metadata)


def test_whole_float_raw_counts_are_explicitly_accepted_and_inputs_immutable():
    counts, metadata = _fixture()
    counts = counts.astype(float)
    old_counts, old_meta = counts.copy(deep=True), metadata.copy(deep=True)
    result = _run(counts, metadata)
    assert result["status"] == "analysis_complete"
    pd.testing.assert_frame_equal(counts, old_counts)
    pd.testing.assert_frame_equal(metadata, old_meta)
    assert len(result["gene_statistics"]) == 60
    assert all(0 <= row["p"] <= row["q"] <= 1 for row in result["gene_statistics"] if row["status"] == "fitted")


def test_sample_metadata_order_is_explicitly_aligned():
    counts, metadata = _fixture()
    a = _run(counts, metadata)
    b = _run(counts, metadata.iloc[::-1].reset_index(drop=True))
    numeric = lambda result: [{k: v for k, v in row.items() if k != "evidence_refs"} for row in result["gene_statistics"]]
    assert numeric(a) == numeric(b)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_rejects_nonmatching_metadata_ids(change):
    counts, metadata = _fixture()
    if change == "missing":
        metadata = metadata.iloc[:-1]
    elif change == "extra":
        metadata = pd.concat([metadata, pd.DataFrame({"sample_id": ["extra"], "condition": ["control"]})])
    else:
        metadata.loc[1, "sample_id"] = metadata.loc[0, "sample_id"]
    with pytest.raises(ValueError, match="sample"):
        _run(counts, metadata)


def test_real_interaction_has_opposite_stratum_estimates_and_full_family_bh():
    counts, metadata = _fixture(sex=True)
    result = _run(counts, metadata)
    rows = result["gene_statistics"]
    assert len(rows) == 180
    by_key = {(row["gene_id"], row["contrast_id"]): row for row in rows}
    for gene in ("g0", "g1", "g2"):
        assert by_key[gene, "female_treated_vs_control"]["effect"] > 0
        assert by_key[gene, "male_treated_vs_control"]["effect"] < 0
        assert by_key[gene, "male_minus_female"]["effect"] < 0
    assert all(row["q"] >= row["p"] for row in rows if row["status"] == "fitted")


def test_real_joint_batch_model_returns_both_prespecified_contrasts():
    counts, metadata = _fixture(batch=True)
    result = _run(counts, metadata)
    assert len(result["gene_statistics"]) == 120
    assert {row["contrast_id"] for row in result["gene_statistics"]} == {"treated_vs_control", "batch_2_vs_batch_1"}


def test_confounded_batch_has_evidence_backed_refusal():
    counts, metadata = _fixture()
    metadata["batch"] = metadata.condition.map({"control": "batch_1", "treated": "batch_2"})
    result = _run(counts, metadata)
    assert result["status"] == "scientific_refusal"
    assert result["gene_statistics"] == []
    assert any(claim["issue_code"] == "non_identifiable_design" for claim in result["claims"])
    assert all(claim["evidence_refs"] for claim in result["claims"])
    wrapper = _wrapper(counts, metadata)
    from benchmarks.discovery_baselines import _hash
    for receipt in wrapper["evidence_lookup"].values():
        assert _hash(receipt["diagnostic_result"]) == receipt["result_sha256"]


def test_missing_independence_provenance_withholds_biological_statistics():
    counts, metadata = _fixture()
    provenance = _provenance(metadata)
    provenance["biological_independence"] = None
    result = _run(counts, metadata, provenance=provenance)
    assert result["status"] == "scientific_refusal"
    assert result["gene_statistics"] == []
    assert any(claim["issue_code"] == "missing_independence_provenance" for claim in result["claims"])


def test_same_library_runs_aggregate_to_eight_declared_donors():
    counts, metadata = _fixture()
    metadata["donor_id"] = [f"d{i // 3}" for i in range(24)]
    metadata["library_id"] = [f"l{i // 3}" for i in range(24)]
    metadata["run_id"] = [f"r{i}" for i in range(24)]
    metadata["condition"] = ["control"] * 12 + ["treated"] * 12
    provenance = _provenance(metadata)
    provenance["biological_independence"] = {"status": "repeated_same_library", "biological_unit_column": "donor_id",
                                            "library_column": "library_id", "run_column": "run_id"}
    result = _run(counts, metadata, provenance=provenance)
    assert result["status"] in {"analysis_complete", "null_result"}
    assert any("eight" in limitation.lower() or "8 biological" in limitation.lower() for limitation in result["limitations"])


def test_no_positive_reference_gene_does_not_silently_change_normalization():
    counts, metadata = _fixture()
    counts.iloc[:, ::2] = 0
    result = _run(counts, metadata)
    assert result["status"] == "operational_failure"
    assert result["gene_statistics"] == []
    assert "ratio" in result["error"]["message"].lower()


def test_baseline_has_no_private_truth_family_seed_or_campaign_argument():
    assert importlib.util.find_spec("benchmarks.discovery_baselines") is not None, "discovery baseline API is absent"
    import inspect
    from benchmarks.discovery_baselines import run_baseline
    assert list(inspect.signature(run_baseline).parameters) == ["counts", "metadata", "arm", "provenance"]


def test_full_panel_bh_retains_unavailable_zero_gene_positions():
    counts, metadata = _fixture(sex=True)
    counts.iloc[-1] = 0
    result = _run(counts, metadata)
    rows = result["gene_statistics"]
    assert len(rows) == 180
    failures = [row for row in rows if row["gene_id"] == "g59"]
    assert len(failures) == 3
    assert all(row["status"] == "failed" and row["p"] is None and row["q"] is None and row["effect"] is None and row["reason"] for row in failures)
    usable = sorted((row for row in rows if row["status"] == "fitted"), key=lambda row: row["p"])
    expected = np.minimum.accumulate((180 * np.array([row["p"] for row in usable]) / np.arange(1, len(usable) + 1))[::-1])[::-1].clip(0, 1)
    assert np.allclose([row["q"] for row in usable], expected)


def test_executed_receipts_and_diagnostics_are_serializable_and_content_bound():
    import json
    counts, metadata = _fixture()
    result = _wrapper(counts, metadata)
    json.dumps(result, allow_nan=False)
    assert set(result) == {"payload", "evidence_lookup", "diagnostics"}
    assert result["evidence_lookup"]
    for ref, receipt in result["evidence_lookup"].items():
        assert receipt["status"] == "executed"
        assert receipt["method"] == "pydeseq2_wald"
        assert receipt["biological_unit_count"] == 24
        assert all(len(receipt[key]) == 64 for key in ("input_id", "model_id", "result_id"))
        assert ref.startswith("sha256:")
        from benchmarks.discovery_baselines import _hash
        record = result["diagnostics"]["execution_records"][ref]
        assert _hash(record["model"]) == receipt["model_sha256"]
        assert _hash(record["result"]) == receipt["result_sha256"]
        assert "sha256:" + _hash(receipt) == ref
    diagnostics = result["diagnostics"]
    assert diagnostics["hypothesis_family"] == {"gene_ids": list(counts.index), "contrast_ids": ["treated_vs_control"], "test_count": 60}
    assert diagnostics["runtime"]["pydeseq2_version"] == "0.5.4"
    assert diagnostics["parameters"]["n_cpus"] == 1
    assert diagnostics["parameters"]["independent_filter"] is False
    assert diagnostics["parameters"]["refit_cooks"] is False
    assert diagnostics["parameters"]["normalization"] == "ratio"


def test_naive_unknown_units_is_provisional_and_emits_no_biological_discovery():
    counts, metadata = _fixture()
    provenance = _provenance(metadata)
    provenance["biological_independence"] = None
    result = _run(counts, metadata, arm="naive", provenance=provenance)
    assert result["gene_statistics"]
    assert all(claim["assertion_status"] == "hypothesis" or claim["type"] in {"design_issue", "abstention"} for claim in result["claims"])
    assert any("provisional" in item.lower() for item in result["limitations"])


def test_null_fixture_keeps_actual_executed_evidence():
    counts, metadata = _fixture(null=True)
    result = _wrapper(counts, metadata)
    assert result["payload"]["status"] == "null_result"
    assert result["payload"]["claims"] == []
    assert result["payload"]["gene_statistics"]
    assert result["evidence_lookup"]


def test_public_role_mapping_uses_actual_column_names():
    counts, metadata = _fixture(sex=True)
    metadata = metadata.rename(columns={"condition": "treatment", "sex": "recorded_sex"})
    provenance = _provenance(metadata)
    provenance["metadata_roles"] = {"condition": "treatment", "sex": "recorded_sex"}
    result = _run(counts, metadata, provenance=provenance)
    assert len(result["gene_statistics"]) == 180
    assert any(claim["factor"] == "recorded_sex" for claim in result["claims"])


def test_normalized_representation_is_not_converted_to_counts():
    counts, metadata = _fixture()
    provenance = _provenance(metadata)
    provenance["representation"] = "normalized_expression"
    with pytest.raises(ValueError, match="raw_counts"):
        _run(counts, metadata, provenance=provenance)


def test_same_library_declaration_does_not_allow_cross_condition_library():
    counts, metadata = _fixture()
    metadata["donor_id"] = [f"d{i // 3}" for i in range(24)]
    metadata["library_id"] = [f"l{i // 3}" for i in range(24)]
    metadata["run_id"] = [f"r{i}" for i in range(24)]
    provenance = _provenance(metadata)
    provenance["biological_independence"] = {"status": "repeated_same_library", "biological_unit_column": "donor_id",
                                            "library_column": "library_id", "run_column": "run_id"}
    with pytest.raises(ValueError, match="library"):
        _run(counts, metadata, provenance=provenance)


@pytest.mark.parametrize("column", ["condition label", "__import__('os').system('touch baseline_injection_marker')"])
def test_public_column_names_are_data_not_executable_formulas(column):
    from pathlib import Path
    marker = Path("baseline_injection_marker")
    assert not marker.exists()
    counts, metadata = _fixture()
    metadata = metadata.rename(columns={"condition": column})
    provenance = _provenance(metadata)
    provenance["metadata_roles"] = {"condition": column}
    result = _wrapper(counts, metadata, provenance=provenance)
    assert result["payload"]["status"] == "analysis_complete"
    assert result["diagnostics"]["design"]["columns"] == ["Intercept", "Condition"]
    assert all(claim["factor"] == column for claim in result["payload"]["claims"])
    assert not marker.exists()


def test_same_library_sum_receipt_and_nested_design_rank_are_actual():
    from benchmarks.discovery_baselines import _frame, _hash
    counts, metadata = _fixture()
    metadata["donor_id"] = [f"d{i // 3}" for i in range(24)]
    metadata["library_id"] = [f"l{i // 3}" for i in range(24)]
    metadata["run_id"] = [f"r{i}" for i in range(24)]
    metadata["condition"] = ["control"] * 12 + ["treated"] * 12
    provenance = _provenance(metadata)
    provenance["biological_independence"] = {"status": "repeated_same_library", "biological_unit_column": "donor_id",
                                            "library_column": "library_id", "run_column": "run_id"}
    result = _wrapper(counts, metadata, provenance=provenance)
    aggregation = result["diagnostics"]["aggregation"]
    expected = pd.DataFrame({f"l{i}": counts.iloc[:, i * 3:(i + 1) * 3].sum(axis=1) for i in range(8)})
    assert aggregation["aggregated_counts_sha256"] == _hash(_frame(expected))
    assert aggregation["nested_donor_condition_rank"] < aggregation["nested_donor_condition_columns"]
    assert len(result["diagnostics"]["design"]["row_ids"]) == 8
    assert all(receipt["biological_unit_count"] == 8 for receipt in result["evidence_lookup"].values())


def test_baseline_payload_passes_shared_schema():
    from benchmarks.discovery_claims import validate_claims
    counts, metadata = _fixture(sex=True)
    result = _wrapper(counts, metadata)
    assert validate_claims(result["payload"], gene_ids=list(counts.index), sample_ids=list(counts.columns)) == result["payload"]


def test_unavailable_empirical_bayes_prior_is_not_reported_as_success():
    import json
    counts, metadata = _fixture()
    counts.iloc[:, :] = 100
    result = _wrapper(counts, metadata)
    assert result["payload"]["status"] == "operational_failure"
    assert result["payload"]["gene_statistics"] == []
    json.dumps(result, allow_nan=False)
    if result["diagnostics"].get("dispersion", {}).get("actual_fit_type") == "mean":
        assert result["diagnostics"]["fallback_records"]


@pytest.mark.parametrize("private_key", ["family", "seed", "truth", "evaluator_path", "planted_membership"])
def test_provenance_cannot_smuggle_evaluator_arguments(private_key):
    counts, metadata = _fixture()
    provenance = _provenance(metadata)
    provenance[private_key] = "private evaluator value"
    with pytest.raises(ValueError, match="private"):
        _run(counts, metadata, provenance=provenance)


def test_run_labels_need_be_unique_within_library_not_globally():
    counts, metadata = _fixture()
    metadata["donor_id"] = [f"d{i // 3}" for i in range(24)]
    metadata["library_id"] = [f"l{i // 3}" for i in range(24)]
    metadata["run_id"] = [f"run_{i % 3}" for i in range(24)]
    metadata["condition"] = ["control"] * 12 + ["treated"] * 12
    provenance = _provenance(metadata)
    provenance["biological_independence"] = {"status": "repeated_same_library", "biological_unit_column": "donor_id",
                                            "library_column": "library_id", "run_column": "run_id"}
    result = _wrapper(counts, metadata, provenance=provenance)
    assert result["payload"]["status"] in {"analysis_complete", "null_result"}
    metadata.loc[1, "run_id"] = metadata.loc[0, "run_id"]
    with pytest.raises(ValueError, match="library"):
        _wrapper(counts, metadata, provenance=provenance)


def test_all_unavailable_estimates_are_operational_failure_not_null(monkeypatch):
    from pydeseq2.dds import DeseqDataSet
    original = DeseqDataSet.fit_LFC

    def force_nonconvergence_status(self):
        original(self)  # Execute genuine NB fit, then simulate the worker failure flag.
        self.var["_LFC_converged"] = False

    monkeypatch.setattr(DeseqDataSet, "fit_LFC", force_nonconvergence_status)
    counts, metadata = _fixture()
    result = _wrapper(counts, metadata)
    assert result["payload"]["status"] == "operational_failure"
    assert result["payload"]["gene_statistics"] == []
    assert len(result["diagnostics"]["unavailable_gene_statistics"]) == 60
    assert all(row["p"] is None and row["q"] is None and row["reason"] for row in result["diagnostics"]["unavailable_gene_statistics"])


@pytest.mark.parametrize("role,levels", [("sex", ["female", "male"]), ("batch", ["batch_1", "batch_2"])])
def test_same_library_modeled_covariates_cannot_be_silently_selected_from_one_run(role, levels):
    counts, metadata = _fixture()
    metadata["donor_id"] = [f"d{i // 3}" for i in range(24)]
    metadata["library_id"] = [f"l{i // 3}" for i in range(24)]
    metadata["run_id"] = [f"run_{i % 3}" for i in range(24)]
    metadata["condition"] = ["control"] * 12 + ["treated"] * 12
    metadata[role] = [levels[(i // 3) % 2] if i % 3 != 1 else levels[1 - (i // 3) % 2] for i in range(24)]
    provenance = _provenance(metadata)
    provenance["biological_independence"] = {"status": "repeated_same_library", "biological_unit_column": "donor_id",
                                            "library_column": "library_id", "run_column": "run_id"}
    result = _wrapper(counts, metadata, provenance=provenance)
    assert result["payload"]["status"] == "scientific_refusal"
    assert result["payload"]["gene_statistics"] == []
    assert result["diagnostics"]["aggregation"] is None
    assert any(claim["issue_code"] == "invalid_design" for claim in result["payload"]["claims"])
    diagnostic = next(iter(result["evidence_lookup"].values()))["diagnostic_result"]
    assert diagnostic["observations"]["inconsistent_library_covariates"]
    assert role in diagnostic["observations"]["modeled_roles"]


def test_identifiable_sex_plus_batch_has_real_joint_nb_fit_and_four_contrasts():
    counts, metadata = _fixture(sex=True, batch=True)
    result = _wrapper(counts, metadata)
    rows = result["payload"]["gene_statistics"]
    assert len(rows) == 240
    assert result["diagnostics"]["hypothesis_family"]["test_count"] == 240
    assert result["diagnostics"]["design"]["columns"] == ["Intercept", "Batch2", "Condition", "Male", "ConditionMale"]
    assert result["diagnostics"]["design"]["rank"] == 5
    assert set(result["diagnostics"]["design"]["contrasts"]) == {"female_treated_vs_control", "male_treated_vs_control", "male_minus_female", "batch_2_vs_batch_1"}
    by_key = {(row["gene_id"], row["contrast_id"]): row for row in rows}
    assert by_key["g0", "female_treated_vs_control"]["effect"] > 0
    assert by_key["g0", "male_treated_vs_control"]["effect"] < 0
    assert by_key["g0", "male_minus_female"]["effect"] < 0
    assert by_key["g3", "batch_2_vs_batch_1"]["effect"] > 0
    fitted = sorted((r for r in rows if r["status"] == "fitted"), key=lambda r: r["p"])
    expected = np.minimum.accumulate((240 * np.array([r["p"] for r in fitted]) / np.arange(1, len(fitted) + 1))[::-1])[::-1].clip(0, 1)
    assert np.allclose([r["q"] for r in fitted], expected)
    for receipt in result["evidence_lookup"].values():
        assert set(receipt["adjustment_factors"]) == ({"condition", "sex"} if receipt["factor"] == "batch" else {"sex", "batch"})


def test_confounded_sex_plus_batch_has_rank_diagnostic_and_no_fit():
    counts, metadata = _fixture(sex=True, batch=True)
    metadata["batch"] = metadata.condition.map({"control": "batch_1", "treated": "batch_2"})
    result = _wrapper(counts, metadata)
    assert result["payload"]["status"] == "scientific_refusal"
    assert result["payload"]["gene_statistics"] == []
    assert result["diagnostics"]["design"]["rank"] < 5


def test_receipt_contains_actual_fit_and_model_records_without_circular_refs():
    from benchmarks.discovery_baselines import _hash
    counts, metadata = _fixture()
    result = _wrapper(counts, metadata)
    for ref, receipt in result["evidence_lookup"].items():
        assert _hash(receipt["model_record"]) == receipt["model_sha256"]
        assert _hash(receipt["fit_result"]) == receipt["result_sha256"]
        assert all(row["evidence_refs"] == [] for row in receipt["fit_result"])
        assert _hash(receipt) == ref.removeprefix("sha256:")


def test_unsupported_int64_count_is_rejected_before_float_rounding_at_constructor(monkeypatch):
    import pydeseq2.dds
    counts, metadata = _fixture()
    counts.iloc[0, 0] = 9007199254740993
    observed = []

    def capture_constructor(**kwargs):
        observed.append(int(kwargs["counts"].iloc[0, 0]))
        print(f"constructor_observed_count={observed[-1]}; supplied_count={int(counts.iloc[0, 0])}")
        raise RuntimeError("constructor capture only; no fit")

    monkeypatch.setattr(pydeseq2.dds, "DeseqDataSet", capture_constructor)
    with pytest.raises(ValueError, match="exactly representable"):
        _wrapper(counts, metadata)
    assert observed == []


def test_supported_int64_boundary_reaches_constructor_unchanged_without_fit(monkeypatch):
    import pydeseq2.dds
    counts, metadata = _fixture()
    counts.iloc[0, 0] = 2**53 - 1
    observed = []

    def capture_constructor(**kwargs):
        observed.append(kwargs["counts"].copy(deep=True))
        raise RuntimeError("constructor capture only; no fit")

    monkeypatch.setattr(pydeseq2.dds, "DeseqDataSet", capture_constructor)
    result = _wrapper(counts, metadata)
    assert result["payload"]["status"] == "operational_failure"
    assert len(observed) == 1
    pd.testing.assert_frame_equal(observed[0], counts.T)


def test_same_library_sum_outside_engine_range_is_rejected_before_constructor(monkeypatch):
    import pydeseq2.dds
    counts, metadata = _fixture()
    counts.iloc[0, :3] = 2**53 - 1
    metadata["donor_id"] = [f"d{i // 3}" for i in range(24)]
    metadata["library_id"] = [f"l{i // 3}" for i in range(24)]
    metadata["run_id"] = [f"run_{i % 3}" for i in range(24)]
    metadata["condition"] = ["control"] * 12 + ["treated"] * 12
    provenance = _provenance(metadata)
    provenance["biological_independence"] = {"status": "repeated_same_library", "biological_unit_column": "donor_id",
                                            "library_column": "library_id", "run_column": "run_id"}
    observed = []

    def capture_constructor(**kwargs):
        observed.append(kwargs["counts"])
        raise RuntimeError("constructor capture only; no fit")

    monkeypatch.setattr(pydeseq2.dds, "DeseqDataSet", capture_constructor)
    with pytest.raises(ValueError, match="exactly representable"):
        _wrapper(counts, metadata, provenance=provenance)
    assert observed == []
