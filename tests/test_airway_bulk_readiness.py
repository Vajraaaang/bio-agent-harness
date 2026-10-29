"""Synthetic paired-count and identity checks; never load the airway object."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from devbench.study.biology_tool_io import ObservedData
from devbench.study import biology_tools_bulk as core


def paired_data(*, genes=80, biological_unit="cell", declared_unit="cell", block=True):
    rng = np.random.default_rng(9172)
    metadata = pd.DataFrame({
        "sample_id": [f"sample{i}" for i in range(8)],
        "cell": [f"line{i // 2}" for i in range(8)],
        "specimen_id": [f"specimen{i}" for i in range(8)],
        "library_id": [f"library{i}" for i in range(8)],
        "condition": ["A", "B"] * 4,
    }).set_index("sample_id", drop=False)
    baselines = rng.uniform(30, 300, size=(genes, 1))
    effects = np.ones((genes, 8))
    effects[:max(1, genes // 5), 1::2] = 3
    values = rng.negative_binomial(25, 25 / (25 + baselines * effects)).astype(float)
    values[-1] = 0
    roles = {"condition": "condition", "population_unit": declared_unit,
             "batches": [], "time": None, "dose": None, "covariate": None}
    if block:
        roles["blocking"] = [declared_unit]
    target = {"target_id": "mean_contrast", "estimand": "Condition contrast",
              "measurement_units": "log2 count-rate contrast", "contrast": "B minus A",
              "reference_population": "observed biological units",
              "prediction_reference": "observed biological units"}
    return ObservedData(values, tuple(f"gene{i}" for i in range(genes)),
        tuple(metadata.index), metadata, {}, "raw_counts",
        {"population_unit": "donor_derived_cell_line" if biological_unit == "cell" else biological_unit,
         "population_unit_label": declared_unit, "specimen_label": "specimen_id",
         "library_label": "library_id", "identity_status": "declared"},
        (target,), {}, {"condition_labels": ["A", "B"], "analysis_columns": roles})


def test_empty_declared_batch_refuses_in_public_binding_and_direct_design():
    data = paired_data(block=False, declared_unit="library_id")
    metadata = data.metadata.assign(batch=["x", "y", "y", "x"] * 2)
    data = replace(data, metadata=metadata)
    with pytest.raises(ValueError, match="batch.*explicitly_declared"):
        core._public_settings(data, {})
    with pytest.raises(ValueError, match="batch.*explicitly_declared"):
        core._design(data, "condition", {})


def test_library_ids_do_not_hide_repeated_cell_lines():
    data = paired_data(block=False, declared_unit="library_id")
    with pytest.raises(ValueError, match="repeated_biological_unit_cell"):
        core._independent_view(data, ["condition"])


def test_unpaired_repeated_units_remain_refused():
    data = paired_data(block=False)
    with pytest.raises(ValueError, match="repeated_units_across_design_coordinates"):
        core._independent_view(data, ["condition"])


def test_paired_fixed_block_is_rank_five_and_four_biological_units():
    data = paired_data()
    settings = core._public_settings(data, {})
    view, audit = core._independent_view(data, ["condition"], settings=settings)
    design, details = core._design(view, "condition", settings)
    assert design.shape == (8, 5)
    assert details["rank"] == 5 and details["residual_df"] == 3
    assert details["block_columns"] == ["cell"]
    assert len([name for name in design if name.startswith("fixed_block_cell_")]) == 3
    assert audit["biological_unit_count"] == audit["analysis_unit_count"] == 4
    assert audit["analysis_observation_count"] == 8
    assert audit["paired_block_column"] == "cell"
    core._require_design(design, details)
    core._check_replication(view, details, settings)


def test_paired_model_preserves_existing_nb_calibration_and_failed_bh_family():
    from statsmodels.stats.multitest import multipletests
    data = paired_data()
    settings = core._public_settings(data, {})
    diagnostics = {}
    fragments = core._fit_effect_targets(data, list(data.targets), "condition", settings, diagnostics)
    rows = fragments[0]["payload"]["gene_statistics"]
    assert [row["gene_id"] for row in rows] == list(data.gene_ids)
    assert diagnostics["model_family_attempted"] == "negative_binomial"
    assert diagnostics["calibration"]["design_columns"] == diagnostics["designs"][0]["columns"]
    assert diagnostics["designs"][0]["rank"] == 5
    assert diagnostics["normalization"]["method"]
    assert diagnostics["gene_test_adjustment_family"]["attempted_count"] == len(data.gene_ids)
    assert rows[-1]["p_value"] is None and rows[-1]["q_value"] is None
    estimated = [row for row in rows if row["estimate_status"] == "estimated"]
    assert len(estimated) > len(rows) // 2
    assert all(row["q_value"] >= row["p_value"] for row in estimated)
    reference_q = multipletests([row["p_value"] if row["p_value"] is not None else 1.0 for row in rows], method="fdr_bh")[1]
    for row, expected in zip(rows, reference_q):
        if row["p_value"] is not None:
            assert row["q_value"] == pytest.approx(expected)
    assert fragments[0]["payload"]["biological_unit"] == "donor_derived_cell_line"


def test_block_is_not_a_way_to_drop_a_confounded_batch():
    data = paired_data()
    metadata = data.metadata.assign(batch=data.metadata.condition)
    data = replace(data, metadata=metadata,
        context={**data.context, "analysis_columns": {**data.context["analysis_columns"], "batches": ["batch"]}})
    settings = core._public_settings(data, {})
    view, _ = core._independent_view(data, ["condition", "batch"], settings=settings)
    design, details = core._design(view, "condition", settings)
    with pytest.raises(ValueError, match="rank_deficient"):
        core._require_design(design, details)


def test_technical_libraries_do_not_increase_paired_replication():
    data = paired_data()
    positions = np.repeat(np.arange(8), 2)
    metadata = data.metadata.iloc[positions].copy()
    metadata["sample_id"] = [f"technical{i}" for i in range(16)]
    metadata["library_id"] = metadata.sample_id
    metadata.index = metadata.sample_id
    data = replace(data, expression=data.expression[:, positions], metadata=metadata,
                   sample_ids=tuple(metadata.index))
    settings = core._public_settings(data, {})
    view, audit = core._independent_view(data, ["condition"], settings=settings)
    assert view.expression.shape == (80, 8)
    assert audit["biological_unit_count"] == audit["analysis_unit_count"] == 4
    assert audit["library_count"] == 16
    np.testing.assert_array_equal(view.expression, paired_data().expression * 2)


def test_explicit_block_cannot_be_used_for_continuous_or_incomplete_pairs():
    data = paired_data()
    with pytest.raises(ValueError, match="raw_counts"):
        core._independent_view(replace(data, measurement="continuous"), ["condition"], settings={"block_cols": ["cell"]})
    metadata = data.metadata.copy()
    metadata.loc["sample1", "condition"] = "A"
    with pytest.raises(ValueError, match="complete_pairs"):
        core._independent_view(replace(data, metadata=metadata), ["condition"], settings={"block_cols": ["cell"]})


def test_cells_and_unknown_recognizable_identity_cannot_prove_independence():
    data = paired_data(block=False, biological_unit="single_cell", declared_unit="library_id")
    with pytest.raises(ValueError, match="biological_unit_is_membership"):
        core._independent_view(data, ["condition"])
    data = paired_data(block=False, declared_unit="library_id")
    data.metadata.loc["sample0", "cell"] = None
    with pytest.raises(ValueError, match="biological_unit_identity_unknown_cell"):
        core._independent_view(data, ["condition"])


def test_false_independence_refusal_has_observed_identity_evidence():
    from devbench.study.biology_tool_io import target_fragment
    data = paired_data(block=False, declared_unit="library_id")
    fragment = target_fragment(data.targets[0], claim_type="refusal", evidence_scope="not_identified",
                               limitation_codes=["repeated_biological_unit_cell"])
    diagnostics = {}
    core._attach_refusal_evidence(data, [fragment], diagnostics)
    assert fragment["claim_type"] == "refusal"
    assert fragment["support_checks"][0]["code"] == "independent_biological_units"
    assert fragment["support_checks"][0]["evidence_ids"] == ["metadata"]


@pytest.mark.parametrize("alias", ["Cell", "Cell_Line", "DONOR_ID"])
def test_recognizable_identity_alias_case_cannot_hide_repeated_units(alias):
    data = paired_data(block=False, declared_unit="library_id")
    data = replace(data, metadata=data.metadata.rename(columns={"cell": alias}))
    with pytest.raises(ValueError, match="repeated_biological_unit_" + alias):
        core._independent_view(data, ["condition"])


def test_physical_library_cannot_reappear_under_two_paired_conditions():
    data = paired_data()
    metadata = data.metadata.copy()
    metadata["specimen_id"] = [f"physical_specimen{i // 2}" for i in range(8)]
    metadata["library_id"] = [f"physical_library{i // 2}" for i in range(8)]
    data = replace(data, metadata=metadata)
    with pytest.raises(ValueError, match="library_crosses_design_coordinates"):
        core._independent_view(data, ["condition"])


def test_split_specimen_with_distinct_libraries_remains_a_valid_pair():
    data = paired_data()
    data = replace(data, metadata=data.metadata.assign(
        specimen_id=[f"split_specimen{i // 2}" for i in range(8)]))
    settings = core._public_settings(data, {})
    view, audit = core._independent_view(data, ["condition"], settings=settings)
    design, details = core._design(view, "condition", settings)
    core._require_design(design, details)
    assert audit["biological_unit_count"] == 4 and audit["library_count"] == 8
    assert details["rank"] == 5 and details["residual_df"] == 3


def test_library_repeated_only_within_one_coordinate_can_be_aggregated():
    data = paired_data()
    positions = np.repeat(np.arange(8), 2)
    metadata = data.metadata.iloc[positions].copy()
    metadata["sample_id"] = [f"lane{i}" for i in range(16)]
    metadata.index = metadata.sample_id
    expanded = replace(data, expression=data.expression[:, positions], metadata=metadata,
                       sample_ids=tuple(metadata.index))
    view, audit = core._independent_view(expanded, ["condition"])
    assert audit["biological_unit_count"] == 4 and audit["library_count"] == 8
    assert audit["analysis_observation_count"] == 8
    np.testing.assert_array_equal(view.expression, data.expression * 2)


@pytest.mark.parametrize("marker", ["not_known", "Not Known", "missing"])
def test_recognizable_missing_identity_sentinels_refuse(marker):
    data = paired_data()
    data.metadata.loc[["sample0", "sample1"], "cell"] = marker
    with pytest.raises(ValueError, match="biological_unit_identity_unknown_cell"):
        core._independent_view(data, ["condition"])
