"""Configuration tests execute no observations; numerical specifications stay skipped.

The unconditional skips reflect the current no-data-execution authorization.
Removing them is a separate, explicit execution decision.
"""
from pathlib import Path

import pytest


PUBLIC_CONFIG = {
    "measurement": "continuous",
    "unit_hierarchy": {
        "population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id",
    },
    "targets": [{
        "target_id": "mean_contrast", "estimand": "Observed condition contrast",
        "measurement_units": "input units", "contrast": "B minus A",
        "reference_population": "independent donors",
        "prediction_reference": "observed conditions",
    }],
}


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true", [], {}])
def test_default_denial_precedes_any_data_access(monkeypatch, permission):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_bulk import analyze_bulk_tool

    def forbidden_read(*args, **kwargs):
        pytest.fail("default denial must precede data access")

    monkeypatch.setattr(Path, "open", forbidden_read)
    arguments = dict(counts_path="/nonexistent/counts.zarr",
                     metadata_path="/nonexistent/metadata.csv",
                     annotations_path="/nonexistent/annotations.json",
                     allow_data_execution=permission)
    with pytest.raises(PermissionError, match="literal True"):
        load_observed_data(PUBLIC_CONFIG, **arguments)
    with pytest.raises(PermissionError, match="literal True"):
        analyze_bulk_tool("mean_condition_analysis", PUBLIC_CONFIG, **arguments)


def test_public_configuration_preserves_estimand_and_omits_coordinator_ids():
    from devbench.study.biology_tool_io import validate_public_task

    validated = validate_public_task(dict(PUBLIC_CONFIG, task_id="opaque-task"))
    assert validated["targets"][0]["contrast"] == "B minus A"
    assert "task_id" not in validated


@pytest.mark.parametrize("field", ["expected_scope", "private_truth", "seed", "case_id"])
def test_coordinator_fields_cannot_reach_analysis_configuration(field):
    from devbench.study.biology_tool_io import validate_public_task

    with pytest.raises(ValueError, match="private|coordinator"):
        validate_public_task(dict(PUBLIC_CONFIG, **{field: "forbidden"}))


@pytest.mark.parametrize("field", ["generation_seed", "template_id", "recipe_id", "cohort_id",
    "plan_identity", "role", "expected_refusal", "numerical_scoring", "private_targets",
    "latent_simulation_truth", "rng_stream_roles", "structural_zero_gene_mask", "nonnull",
    "hypothesis_testable", "numerical_reference_available", "unidentifiable_simulation_effect"])
def test_nested_coordinator_keys_never_reach_public_analysis(field):
    from devbench.study.biology_tool_io import validate_public_task

    with pytest.raises(ValueError, match="private|coordinator"):
        validate_public_task(dict(PUBLIC_CONFIG, context={"nested": {field: "forbidden"}}))


def test_shared_public_limits_are_conservative_and_private_truth_independent():
    from devbench.study import biology_input_limits as limits
    from devbench.study import biology_tool_io

    assert limits.PUBLIC_MAX_METADATA_BYTES == 5_000_000
    assert limits.PUBLIC_MAX_ANNOTATION_BYTES == 5_000_000
    assert limits.PUBLIC_MAX_JSON_NODES == 200_000
    assert limits.PUBLIC_MAX_JSON_DEPTH == 24
    assert limits.PUBLIC_MAX_FEATURES == 5000
    assert biology_tool_io.MAX_METADATA_BYTES == limits.PUBLIC_MAX_METADATA_BYTES
    assert biology_tool_io.MAX_ANNOTATION_BYTES == limits.PUBLIC_MAX_ANNOTATION_BYTES
    assert "scope" not in limits.PUBLIC_DENIED_KEYS
    assert "target_id" not in limits.PUBLIC_DENIED_KEYS


def test_public_structure_counts_values_and_preserves_unknown_fields():
    from devbench.study.biology_input_limits import validate_public_structure

    budget = [3]
    validate_public_structure({"ordinary_key": {"optional_key": None}}, budget=budget)
    assert budget == [0]
    validate_public_structure({"ordinary_key": "", "optional_key": None})
    with pytest.raises(ValueError, match="structural bound"):
        validate_public_structure({"ordinary_key": {"optional_key": None}}, budget=[2])
    with pytest.raises(ValueError, match="structural bound"):
        validate_public_structure(None, depth=25)


@pytest.mark.parametrize("storage_dtype", ["<i8", "<u4", "|i1", "|u1"])
def test_continuous_zarr_requires_float_source_dtype_metadata(storage_dtype):
    from devbench.study.biology_input_limits import validate_expression_source_dtype

    with pytest.raises(ValueError, match="floating"):
        validate_expression_source_dtype(storage_dtype, "continuous")
    assert validate_expression_source_dtype(storage_dtype, "raw_counts") in {"i", "u"}


def test_float_source_dtype_check_does_not_require_a_numerical_runtime():
    from devbench.study.biology_input_limits import validate_expression_source_dtype

    assert validate_expression_source_dtype("<f8", "continuous") == "f"
    with pytest.raises(ValueError, match="integer"):
        validate_expression_source_dtype("<f8", "umi_counts")
    with pytest.raises(ValueError, match="fixed"):
        validate_expression_source_dtype("object", "continuous")


def test_public_json_byte_bound_checks_configuration_text_only():
    from devbench.study.biology_input_limits import public_json_size

    assert public_json_size({"k": "x"}, max_bytes=9) == 9
    with pytest.raises(ValueError, match="byte bound"):
        public_json_size({"k": "x"}, max_bytes=8)
    with pytest.raises(ValueError, match="ceiling"):
        public_json_size({}, max_bytes=5_000_001)


def test_options_are_structured_and_cannot_accept_formula_grammar():
    from devbench.study.biology_tools_bulk import validate_bulk_options

    with pytest.raises(ValueError, match="Unknown"):
        validate_bulk_options({"formula": "__import__('os').system('anything')"})
    with pytest.raises(ValueError, match="spline_df"):
        validate_bulk_options({"spline_df": True})
    with pytest.raises(ValueError, match="frequency"):
        validate_bulk_options({"frequency_domain": [0.2, 0.1]})


def test_registry_describes_real_dispatch_and_never_claims_qualification():
    from devbench.study.biology_tools_bulk import BULK_TOOL_NAMES, bulk_tool_capabilities

    capabilities = bulk_tool_capabilities()
    assert set(capabilities) == set(BULK_TOOL_NAMES)
    assert capabilities["mean_condition_analysis"]["target_ids"]
    assert all(value["qualification"] == "unvalidated_numerical_implementation"
               for value in capabilities.values())


def test_robustness_target_coverage_includes_observed_sampled_mean():
    from devbench.study.biology_tools_bulk import bulk_tool_capabilities

    capability = bulk_tool_capabilities()["observation_robustness_analysis"]
    assert set(capability["target_ids"]) == {"sampled_mean", "clean_population_effect"}
    assert "observed" in capability["estimation_limits"]["sampled_mean"]


def test_target_fragment_copies_canonical_public_definition():
    import json
    from devbench.study.biology_tool_io import target_fragment
    from devbench.study.biology_answers import TargetAnswer

    fragment = target_fragment(PUBLIC_CONFIG["targets"][0], claim_type="refusal",
                               evidence_scope="not_identified", limitation_codes=["missing_unit_identity"])
    result = TargetAnswer.model_validate_json(json.dumps(fragment))
    assert result.units == "input units"
    assert result.contrast == "B minus A"
    assert result.tool_evidence_ids == ()


def test_worker_entry_itself_denies_data_execution_by_default():
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker

    with pytest.raises(PermissionError, match="literal True"):
        _analyze_bulk_worker("mean_condition_analysis", PUBLIC_CONFIG,
                             "/unread/expression.csv", "/unread/metadata.csv", "/unread/annotations.json")


def test_canonical_public_transport_fields_and_layer_provenance():
    from devbench.study.biology_tool_io import validate_public_task

    question = dict(PUBLIC_CONFIG["targets"][0])
    question["units"] = question.pop("measurement_units")
    question["projection_contract"] = {"scope": "shared_process_quotient"}
    canonical = {
        "measurement": "continuous", "questions": [question],
        "population_unit_label": "donor_id", "observation_id_label": "sample_id",
        "context": {"biological_unit": "donor", "feature_universe": ["declared_gene"],
                    "transformation_history": ["continuous input supplied without count conversion"]},
    }
    result = validate_public_task(canonical)
    assert result["targets"][0]["measurement_units"] == "input units"
    assert result["targets"][0]["shared_process_projection"] == question["projection_contract"]
    assert result["gene_ids"] == ["declared_gene"]
    assert result["transformation_history"] == canonical["context"]["transformation_history"]


def test_conflicting_measurement_declarations_fail_without_reading_inputs():
    from devbench.study.biology_tool_io import validate_public_task

    conflicting = dict(PUBLIC_CONFIG, context={"measurement": {"measurement": "raw_counts",
                                                              "analysis_route": "count_likelihood"}})
    with pytest.raises(ValueError, match="measurement disagrees"):
        validate_public_task(conflicting)


def test_answer_fragment_schema_validation_uses_json_semantics():
    from devbench.study.biology_tool_io import json_safe_result, target_fragment

    result = json_safe_result({"target_results": [target_fragment(PUBLIC_CONFIG["targets"][0],
        claim_type="refusal", evidence_scope="not_identified", limitation_codes=["no_observation_permission"])],
        "diagnostics": {}, "method": {"qualification": "unvalidated_numerical_implementation"}})
    assert result["target_results"][0]["tool_evidence_ids"] == []


def test_observed_tool_modules_do_not_import_generators_or_scoring():
    import ast

    package = Path(__file__).resolve().parents[1] / "devbench" / "study"
    for filename in ("biology_tool_io.py", "biology_tools_bulk.py"):
        tree = ast.parse((package / filename).read_text())
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert not any(name.endswith(("biology_bulk", "biology_singlecell", "biology_scoring", "biology_datasets"))
                       for name in imports)


def test_public_condition_order_defines_canonical_reference_without_observations():
    from types import SimpleNamespace
    from devbench.study.biology_tools_bulk import _public_settings

    public_context = SimpleNamespace(context={"design": {"condition_levels": ["Z_reference", "A_comparison"]}})
    settings = _public_settings(public_context, {})
    assert settings["reference_condition"] == "Z_reference"
    assert settings["comparison_condition"] == "A_comparison"
    with pytest.raises(ValueError, match="canonical"):
        _public_settings(public_context, {"reference_condition": "A_comparison"})


def test_missing_estimators_are_method_failures_not_scientific_refusals():
    from devbench.study.biology_tools_bulk import _failure_kind, _failure_limitations, bulk_tool_capabilities

    code = "matched_mean_count_variance_estimator_not_implemented"
    assert _failure_kind(code) == "failed"
    assert "method_not_implemented" in _failure_limitations(code)
    assert _failure_kind("external_control_inputs_not_independently_verified") == "refusal"
    assert "variability" in bulk_tool_capabilities()["differential_variability_analysis"]["unsupported_estimation_targets"]


def test_shared_design_policy_cannot_relax_the_thousand_condition_limit():
    from devbench.study.biology_tool_io import DESIGN_CONDITION_NUMBER_LIMIT, design_condition_policy
    from devbench.study.biology_tools_bulk import bulk_tool_capabilities, validate_bulk_options

    assert DESIGN_CONDITION_NUMBER_LIMIT == 1000.0
    policy = design_condition_policy()
    assert policy["condition_number_limit"] == 1000.0
    assert policy["gate"] == "both_original_and_standardized_full_rank_and_within_limit"
    assert policy["fitting_parameterization"] == "unchanged_declared_design_with_its_full_covariance"
    assert design_condition_policy(500)["condition_number_limit"] == 500
    for invalid in (True, 0, 1001, 1e8, float("inf"), "1000"):
        with pytest.raises(ValueError, match="condition_number_limit"):
            design_condition_policy(invalid)
    with pytest.raises(ValueError, match="condition_number_limit"):
        validate_bulk_options({"condition_number_limit": 1e8})
    assert all(capability["design_condition_policy"] == policy
               for capability in bulk_tool_capabilities().values())


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_continuous_condition_contrast_uses_declared_scale_and_independent_units(tmp_path):
    import json
    import numpy as np
    import pandas as pd
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker

    samples = [f"s{i}" for i in range(8)]
    expression = pd.DataFrame([[1, 2, 1, 2, 5, 6, 5, 6], [3, 5, 4, 6, 2, 4, 3, 5]],
                              index=["g1", "g2"], columns=samples)
    expression.to_csv(tmp_path / "expression.csv", index_label="gene_id")
    metadata = pd.DataFrame({"sample_id": samples, "donor_id": samples,
                             "specimen_id": samples, "library_id": samples,
                             "condition": ["A"] * 4 + ["B"] * 4})
    metadata.to_csv(tmp_path / "metadata.csv", index=False)
    (tmp_path / "annotations.json").write_text(json.dumps({}))
    result = _analyze_bulk_worker("mean_condition_analysis", PUBLIC_CONFIG,
        str(tmp_path / "expression.csv"), str(tmp_path / "metadata.csv"),
        str(tmp_path / "annotations.json"), {}, True)
    target = result["target_results"][0]
    rows = target["payload"]["gene_statistics"]
    assert np.isclose(rows[0]["values"][0], 4)
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in rows)
    assert target["payload"]["analysis_layer"] == "continuous"


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_complete_confounding_rejects_adjusted_effect_but_retains_observed_summary(tmp_path):
    import json
    import pandas as pd
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker

    samples = [f"s{i}" for i in range(6)]
    pd.DataFrame([[1, 2, 3, 7, 8, 9]], index=["g1"], columns=samples).to_csv(
        tmp_path / "expression.csv", index_label="gene_id")
    pd.DataFrame({"sample_id": samples, "donor_id": samples, "specimen_id": samples,
                  "library_id": samples, "condition": ["A"] * 3 + ["B"] * 3,
                  "batch": ["x"] * 3 + ["y"] * 3}).to_csv(tmp_path / "metadata.csv", index=False)
    (tmp_path / "annotations.json").write_text(json.dumps({}))
    config = dict(PUBLIC_CONFIG, targets=[dict(PUBLIC_CONFIG["targets"][0], target_id="adjusted_effect"),
                                        dict(PUBLIC_CONFIG["targets"][0], target_id="observed_difference")])
    args = (config, str(tmp_path / "expression.csv"), str(tmp_path / "metadata.csv"),
            str(tmp_path / "annotations.json"), {}, True)
    adjusted = _analyze_bulk_worker("mean_condition_analysis", *args)
    descriptive = _analyze_bulk_worker("descriptive_expression_summary", *args)
    assert adjusted["target_results"][0]["claim_type"] == "refusal"
    assert descriptive["target_results"][0]["evidence_scope"] == "observed_difference"


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_raw_counts_use_rle_empirical_bayes_dispersion_and_never_count_conversion(tmp_path):
    """Full integration specification; counts originate in an explicit raw layer."""
    import json
    import pandas as pd
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker

    samples = [f"s{i}" for i in range(12)]
    values = [[20 + i + (5 * (j % 3)) + (20 if j >= 6 and i < 4 else 0)
               for j in range(12)] for i in range(40)]
    pd.DataFrame(values, index=[f"g{i}" for i in range(40)], columns=samples).to_csv(
        tmp_path / "expression.csv", index_label="gene_id")
    pd.DataFrame({"sample_id": samples, "donor_id": samples, "specimen_id": samples,
                  "library_id": samples, "condition": ["A"] * 6 + ["B"] * 6}).to_csv(
        tmp_path / "metadata.csv", index=False)
    (tmp_path / "annotations.json").write_text(json.dumps({}))
    config = dict(PUBLIC_CONFIG, measurement="raw_counts")
    result = _analyze_bulk_worker("mean_condition_analysis", config, str(tmp_path / "expression.csv"),
        str(tmp_path / "metadata.csv"), str(tmp_path / "annotations.json"), {}, True)
    assert result["diagnostics"]["calibration"]["method"]
    assert result["diagnostics"]["normalization"]["method"].startswith("median_of_ratios")
    assert result["method"]["count_model"] == "negative_binomial_empirical_bayes_dispersion"


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_failed_hypotheses_remain_in_attempted_bh_family_without_fabricated_pvalues():
    from devbench.study.biology_tools_bulk import _bh

    rows = [{"hypothesis_id": "g1", "p_value": 0.01, "q_value": 0.01},
            {"hypothesis_id": "g2", "estimate_status": "failed", "p_value": None, "q_value": None}]
    tested = _bh(rows)
    assert tested == ["g1"]
    assert rows[0]["q_value"] == pytest.approx(0.02)
    assert rows[1]["p_value"] is None and rows[1]["q_value"] is None


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_spline_width_and_reference_time_contrasts_preserve_observed_grid_ids():
    import numpy as np
    from devbench.study.biology_tools_bulk import _basis, _contrasts

    grid = [0., 1., 2., 3., 4., 5.]
    basis, specification = _basis(np.asarray(grid), 4)
    assert basis.shape == (6, 4)
    details = {"grid": grid, "reference_time": 2., "spline": specification,
               "blocks": {"time": [1, 2, 3, 4]}}
    contrasts, coordinates = _contrasts("sampled_mean", details, 5)
    assert "grid_0" in coordinates and "grid_2" in coordinates
    assert np.allclose(contrasts[coordinates.index("grid_2")], 0.0)
    at_reference, _ = _basis([2.], 4, specification)
    assert np.allclose(contrasts[coordinates.index("grid_0")][1:], basis[0]-at_reference[0])


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_library_relative_summary_uses_complete_observed_count_denominator():
    import math
    import numpy as np
    import pandas as pd
    from devbench.study.biology_tool_io import ObservedData
    from devbench.study.biology_tools_bulk import _library_relative_targets

    samples = ("s1", "s2", "s3", "s4")
    metadata = pd.DataFrame({"sample_id": samples, "donor_id": samples,
        "specimen_id": samples, "library_id": samples, "condition": ["A", "A", "B", "B"]}, index=samples)
    target = dict(PUBLIC_CONFIG["targets"][0], target_id="relative_expression")
    data = ObservedData(np.array([[10., 10., 10., 10.], [10., 10., 90., 90.]]),
        ("stable_count_gene", "changing_denominator_gene"), samples, metadata, {}, "raw_counts",
        PUBLIC_CONFIG["unit_hierarchy"], (target,), {})
    diagnostics = {}
    result = _library_relative_targets(data, [target], {}, diagnostics)
    row = result[0]["payload"]["gene_statistics"][0]
    assert row["values"][0] == pytest.approx(math.log2(0.1/0.5))
    assert row["p_value"] is None and row["q_value"] is None
    assert diagnostics["relative_expression_denominator"]["reference_centering"] == "none"


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Numerical data execution was explicitly not authorized")
def test_standardizing_diagnostic_cannot_rescue_an_unstable_actual_fitting_matrix():
    import numpy as np
    from devbench.study.biology_tool_io import design_matrix_diagnostics, require_design_condition

    matrix = np.column_stack([np.ones(8), np.arange(8, dtype=float)*1e8])
    original = matrix.copy()
    diagnostics = design_matrix_diagnostics(matrix)
    assert diagnostics["raw_condition_number"] > 1000
    assert diagnostics["standardized_condition_number"] < 1000
    assert diagnostics["condition_number_validated"] is False
    assert np.array_equal(matrix, original)
    with pytest.raises(ValueError, match="design_condition_policy_rejected"):
        require_design_condition(matrix)
