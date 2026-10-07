"""Only configuration and default-denial checks are authorized to execute.

Every observed-data, metadata, graph and numerical fixture below is skipped
unconditionally. Those specifications require separate execution permission.
"""
import ast
import builtins
import json
from pathlib import Path

import pytest


PUBLIC_CONFIG = {
    "measurement": "umi_counts",
    "population_unit_label": "donor_id",
    "observation_id_label": "sample_id",
    "context": {"biological_unit": "donor"},
    "questions": [{"target_id": "cell_state_effect", "estimand": "Within-type donor expression contrast",
                   "units": "log2 count-rate contrast", "contrast": "B minus A within public-marker type",
                   "reference_population": "Independent donors", "prediction_reference": "Observed donor population"}],
}
NO_DATA = "Set DEVBENCH_RUN_LOCAL_FIXTURES=1 for the authorized local software fixtures"


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true", [], {}])
def test_singlecell_default_denial_precedes_imports_reads_and_worker(monkeypatch, permission):
    from devbench.study import biology_tools_singlecell as module

    original_import = builtins.__import__

    def protected_import(name, *args, **kwargs):
        if name.split(".")[0] in {"numpy", "pandas", "scipy", "sklearn", "statsmodels", "zarr", "devharness_core"}:
            pytest.fail("Default denial must precede numerical imports and worker imports")
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("Default denial must precede data access")

    monkeypatch.setattr(builtins, "__import__", protected_import)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(module, "load_observed_data", forbidden)
    paths = dict(counts_path="/not-read/expression.csv", metadata_path="/not-read/metadata.csv",
                 annotations_path="/not-read/annotations.json", allow_data_execution=permission)
    with pytest.raises(PermissionError, match="literal True"):
        module.analyze_singlecell_tool("cell_type_state_analysis", PUBLIC_CONFIG, **paths)
    with pytest.raises(PermissionError, match="literal True"):
        module._analyze_singlecell_worker("cell_type_state_analysis", PUBLIC_CONFIG, **paths)


@pytest.mark.parametrize("option", ["case_id", "seed", "expected_scope", "latent_truth", "private_truth", "formula",
                                   "cell_type_col", "root_cell_id", "enrichment_query_gene_ids"])
def test_private_or_unbound_method_inputs_are_not_options(option):
    from devbench.study.biology_tools_singlecell import validate_singlecell_options

    with pytest.raises(ValueError, match="Unknown"):
        validate_singlecell_options({option: "unavailable"})


@pytest.mark.parametrize("options", [{"max_cells": True}, {"minimum_units": 1}, {"n_clusters": 100},
                                     {"timeout_sec": 500}, {"max_mem_mb": float("inf")},
                                     {"batch_cols": ["batch", "batch"]}, {"batch_cols": [{}]},
                                     {"condition_col": "C(condition)"}, {"correlation_threshold": float("nan")}])
def test_bounded_method_configuration_is_validated_without_observations(options):
    from devbench.study.biology_tools_singlecell import validate_singlecell_options

    with pytest.raises(ValueError):
        validate_singlecell_options(options)


def test_valid_options_are_copied_and_do_not_expand_scope():
    from devbench.study.biology_tools_singlecell import validate_singlecell_options

    supplied = {"condition_col": "condition", "batch_cols": ["capture_batch"], "max_cells": 800,
                "n_clusters": 6, "annotation_margin": 0.25}
    observed = validate_singlecell_options(supplied)
    assert observed == supplied
    assert observed is not supplied
    assert validate_singlecell_options() == {}


def test_registry_discloses_numerical_qualification_and_shared_design_policy():
    from devbench.study.biology_tools_singlecell import SINGLECELL_TOOL_NAMES, singlecell_tool_capabilities

    result = singlecell_tool_capabilities()
    assert set(result) == set(SINGLECELL_TOOL_NAMES)
    assert len(result) == 14
    assert result["pseudobulk_membership_audit"]["result_role"] == "support_audit"
    assert result["perturbation_response_analysis"]["unsupported_estimation_targets"] == []
    assert result["cell_type_state_analysis"]["design_condition_policy"]["condition_number_limit"] == 1000.0
    assert all(row["qualification"] == "unvalidated_numerical_implementation" for row in result.values())


def test_condition_policy_can_be_tightened_but_not_relaxed():
    from devbench.study.biology_tools_singlecell import validate_singlecell_options

    assert validate_singlecell_options({"condition_number_limit": 500})["condition_number_limit"] == 500
    with pytest.raises(ValueError, match="condition_number_limit"):
        validate_singlecell_options({"condition_number_limit": 1001})
    with pytest.raises(ValueError, match="condition_number_limit"):
        validate_singlecell_options({"condition_number_limit": True})


EFFICACY_CONTRACT = {
    "assay": "declared_independent_dose_assay",
    "target_to_column": {"public_target": "target_a_reduction"},
    "units": "achieved_intervention_dose_reduction", "reference_value": 0.0, "full_intervention_value": 1.0,
    "scale_definition": "Prescribed intervention dose; this is not percent transcript knockdown.",
    "assay_scope": "declared_assay_coordinate", "evidence_scope": "declared_conditional_assumptions",
    "assay_qualification": "not_empirically_qualified",
    "identification_assumptions": {"randomized_assignment": True, "randomization_unit": "donor",
        "consistency_assumed": True, "target_specificity_assumed": True,
        "exclusion_restriction_assumed": True, "no_between_donor_interference_assumed": True,
        "achieved_dose_observed_for_mapped_targets": True, "measurement_is_expression_estimated": False},
}


def test_achieved_contract_validation_uses_declared_mapping_without_reading_observations():
    from devbench.study.biology_tools_singlecell import validate_achieved_intervention_contract

    # This is schema/configuration only. There are no cell, donor or expression
    # observations, and the helper never inspects a records field.
    result = validate_achieved_intervention_contract(EFFICACY_CONTRACT, ["public_target"])
    assert result["target_to_column"] == {"public_target": "target_a_reduction"}
    assert result["full_intervention_value"] == 1.0
    assert result["assay_qualification"] == "not_empirically_qualified"


@pytest.mark.parametrize("key", ["randomized_assignment", "consistency_assumed", "target_specificity_assumed",
                               "exclusion_restriction_assumed", "no_between_donor_interference_assumed",
                               "achieved_dose_observed_for_mapped_targets"])
def test_achieved_contract_does_not_invent_identification_assumptions(key):
    from devbench.study.biology_tools_singlecell import validate_achieved_intervention_contract

    contract = dict(EFFICACY_CONTRACT, identification_assumptions={**EFFICACY_CONTRACT["identification_assumptions"], key: False})
    with pytest.raises(ValueError, match="assumptions_not_satisfied"):
        validate_achieved_intervention_contract(contract, ["public_target"])


def test_achieved_contract_rejects_expression_derived_efficacy_and_unbound_targets():
    from devbench.study.biology_tools_singlecell import validate_achieved_intervention_contract

    contract = dict(EFFICACY_CONTRACT, identification_assumptions={
        **EFFICACY_CONTRACT["identification_assumptions"], "measurement_is_expression_estimated": True})
    with pytest.raises(ValueError, match="independently_of_fitted_expression"):
        validate_achieved_intervention_contract(contract, ["public_target"])
    with pytest.raises(ValueError, match="match_public_assigned_target_universe"):
        validate_achieved_intervention_contract(EFFICACY_CONTRACT, ["unmapped_target"])


def test_empty_declared_target_mapping_does_not_assert_intervention_positivity():
    from devbench.study.biology_tools_singlecell import validate_achieved_intervention_contract

    contract = validate_achieved_intervention_contract(dict(EFFICACY_CONTRACT, target_to_column={}), [])
    assert contract["target_to_column"] == {}
    assert "assignment_and_achieved_estimands_coincide" not in contract


def test_method_limitations_and_missing_evidence_have_distinct_error_types():
    from devbench.study.biology_tools_singlecell import _MethodUnavailable, _Unavailable

    assert issubclass(_MethodUnavailable, RuntimeError)
    assert not issubclass(_MethodUnavailable, _Unavailable)


def test_entrypoint_source_has_first_statement_literal_gate():
    from devbench.study import biology_tools_singlecell as module

    tree = ast.parse(Path(module.__file__).read_text())
    names = {"analyze_singlecell_tool", "_analyze_singlecell_worker"}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            statements = [row for row in node.body if not (isinstance(row, ast.Expr) and isinstance(row.value, ast.Constant) and isinstance(row.value.value, str))]
            first = statements[0]
            assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
            assert isinstance(first.value.func, ast.Name) and first.value.func.id == "require_data_execution"


def test_import_graph_has_no_generator_or_private_scoring_dependency():
    from devbench.study import biology_tools_singlecell as module

    tree = ast.parse(Path(module.__file__).read_text())
    forbidden = {"biology_bulk", "biology_singlecell", "biology_scoring", "biology_datasets"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not forbidden.intersection((node.module or "").split("."))
        elif isinstance(node, ast.Import):
            assert not any(forbidden.intersection(alias.name.split(".")) for alias in node.names)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
            assert not any(name and name.split(".")[0] in {"numpy", "pandas", "scipy", "sklearn", "statsmodels"} for name in names)


def test_refusal_preserves_public_estimand_and_leaves_receipts_to_broker():
    from devbench.study.biology_answers import TargetAnswer
    from devbench.study.biology_tool_io import target_fragment

    target = PUBLIC_CONFIG["questions"][0]
    fragment = target_fragment(target, claim_type="refusal", evidence_scope="not_identified",
                               limitation_codes=["independent_donor_support_unavailable"])
    result = TargetAnswer.model_validate_json(json.dumps(fragment, allow_nan=False))
    assert result.estimand == target["estimand"]
    assert result.contrast == target["contrast"]
    assert result.units == target["units"]
    assert result.tool_evidence_ids == ()
    assert result.payload is None


def _observed_fixture(tmp_path, *, rows=None, annotations=None, questions=None, context=None):
    """Called only inside the unconditionally skipped execution specifications."""
    import csv
    import json

    metadata = rows or [
        {"sample_id": f"c{i}", "cell_id": f"c{i}", "donor_id": f"d{i // 4}", "specimen_id": f"s{i // 4}",
         "library_id": f"l{i // 4}", "condition": "A" if i < 16 else "B", "capture_batch": "batch0"}
        for i in range(32)]
    ids = [row["sample_id"] for row in metadata]
    with (tmp_path / "metadata.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metadata[0]))
        writer.writeheader()
        writer.writerows(metadata)
    with (tmp_path / "expression.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *ids])
        writer.writerow(["markerA", *[80 if i % 4 < 2 else 1 for i in range(len(ids))]])
        writer.writerow(["markerB", *[1 if i % 4 < 2 else 80 for i in range(len(ids))]])
        for number in range(12):
            writer.writerow([f"g{number}", *[10 + ((i + 3 * number) % 9) for i in range(len(ids))]])
    reference = {"cell_annotation_reference": {"reference_identity": "public_dictionary", "reference_scope": "broad-type marker sets",
        "markers": [{"label": "typeA", "gene_ids": ["markerA"]}, {"label": "typeB", "gene_ids": ["markerB"]}],
        "query_cell_labels_supplied": False}}
    reference.update(annotations or {})
    (tmp_path / "annotations.json").write_text(json.dumps(reference))
    config = dict(PUBLIC_CONFIG, context={"biological_unit": "donor", "condition_labels": ["A", "B"], **(context or {})})
    if questions:
        config["questions"] = [dict(PUBLIC_CONFIG["questions"][0], target_id=target) for target in questions]
    return config, dict(counts_path=str(tmp_path / "expression.csv"), metadata_path=str(tmp_path / "metadata.csv"),
                        annotations_path=str(tmp_path / "annotations.json"), allow_data_execution=True)


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_donor_type_pseudobulk_never_imputes_absent_groups(tmp_path):
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_singlecell import _pseudobulk

    config, paths = _observed_fixture(tmp_path)
    data = load_observed_data(config, **paths)
    labels = ["typeA" if i < 4 else ("typeA" if i % 4 < 2 else "typeB") for i in range(32)]
    aggregated, details = _pseudobulk(data, labels, {})
    assert len(aggregated.sample_ids) == 15
    assert {"donor_id": "d0", "condition": "A", "cell_type": "typeB"} in details["unavailable_donor_type_combinations"]
    assert details["missing_groups_filled_with_zeros"] is False


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_cells_and_batch_labels_do_not_create_donor_replication(tmp_path):
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker

    rows = [{"sample_id": f"c{i}", "cell_id": f"c{i}", "donor_id": "dA" if i < 16 else "dB",
             "specimen_id": "sA" if i < 16 else "sB", "library_id": "lA" if i < 16 else "lB",
             "condition": "A" if i < 16 else "B", "capture_batch": "bA" if i < 16 else "bB"} for i in range(32)]
    config, paths = _observed_fixture(tmp_path, rows=rows)
    result = _analyze_singlecell_worker("cell_type_state_analysis", config, **paths)
    assert result["target_results"][0]["estimate_status"] == "unavailable"
    assert result["target_results"][0]["payload"] is None


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_state_effects_use_count_family_and_explicit_global_test_family(tmp_path):
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker

    config, paths = _observed_fixture(tmp_path)
    result = _analyze_singlecell_worker("cell_type_state_analysis", config, **paths)
    target = result["target_results"][0]
    assert target["payload"]["analysis_layer"] == "umi_counts"
    rows = target["payload"]["gene_statistics"]
    tested = [row for row in rows if row.get("p_value") is not None]
    assert target["payload"]["multiplicity_scope"] == [row["hypothesis_id"] for row in tested]
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in tested)
    assert target["payload"]["biological_unit"] == "donor"


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_shared_process_graph_preserves_cells_counts_and_exact_marker_union(tmp_path):
    import numpy as np
    from devbench.study.biology_tool_io import load_observed_data
    from devbench.study.biology_tools_singlecell import _PROJECTION_CONTRACT, _geometry

    genes = ["markerA", "markerB", *[f"g{i}" for i in range(12)]]
    projection = dict(_PROJECTION_CONTRACT, excluded_feature_ids=genes[:2], retained_feature_universe=genes[2:])
    config, paths = _observed_fixture(tmp_path, questions=["state_topology"],
        context={"feature_universe": genes, "shared_process_projection": projection})
    data = load_observed_data(config, **paths)
    before = data.expression.copy()
    payload, _, _ = _geometry(data, data.targets[0], {})
    assert np.array_equal(data.expression, before)
    assert payload["excluded_feature_ids"] == genes[:2]
    assert payload["retained_feature_universe"] == genes[2:]
    assert {row["cell_id"] for row in payload["within_branch_order"]} == set(data.sample_ids)
    assert payload["orientation_uncertainty"] == "unanchored"


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_founder_records_do_not_orient_within_path_states(tmp_path):
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker

    history = {"measurement": "origin_record", "recording_scheme": "founder pulse", "founder_pulse_precedes_collection": True,
               "founder_expression_state_measured": False, "direction_anchor_available": False,
               "cell_records": [{"cell_id": f"c{i}", "founder_record": f"clone{i % 2}"} for i in range(32)]}
    config, paths = _observed_fixture(tmp_path, questions=["ancestry"], annotations={"history_markers": history})
    answer = _analyze_singlecell_worker("lineage_history_analysis", config, **paths)["target_results"][0]
    assert answer["evidence_scope"] == "clone_equivalence"
    assert answer["payload"]["direction_identified"] is False


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_fixed_query_enrichment_reports_conditional_annotation_probability(tmp_path):
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker

    background = ["markerA", "markerB", *[f"g{i}" for i in range(12)]]
    annotation = {"reference_kind": "curated_membership", "version": "v1", "complete_negative_reference": False,
                  "memberships": [{"tf_id": "g0", "target_id": gene} for gene in ["g1", "g2", "g3"]]}
    context = {"enrichment_query_gene_ids": ["g1", "g2"], "enrichment_background_gene_ids": background,
               "enrichment_query_selection": {"query_count": 2, "selection_independent_of_tested_expression": False}}
    config, paths = _observed_fixture(tmp_path, questions=["regulon_membership"],
                                      annotations={"regulon_annotation": annotation}, context=context)
    payload = _analyze_singlecell_worker("regulon_enrichment", config, **paths)["target_results"][0]["payload"]
    assert payload["feature_universe"] == background
    assert payload["probability_scope"] == "conditional_combinatorial_annotation_overlap"
    assert payload["selection_conditional_inference"] is True
    assert payload["unknown_edges_are_negatives"] is False


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_partial_motif_scans_are_unavailable_not_negative_calls(tmp_path):
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker

    background = ["markerA", "markerB", *[f"g{i}" for i in range(12)]]
    context = {"enrichment_query_gene_ids": ["g1"], "enrichment_background_gene_ids": background,
               "enrichment_query_selection": {"query_count": 1}}
    motif = {"reference_kind": "motif_occurrence", "genome_build": "public", "version": "v1", "binding_measured": False,
             "hits": [{"tf_id": "g0", "target_id": "g1", "hit_count": 1}]}
    config, paths = _observed_fixture(tmp_path, questions=["motif_occurrence"], context=context, annotations={"motif_hit_annotation": motif})
    answer = _analyze_singlecell_worker("motif_enrichment", config, **paths)["target_results"][0]
    assert answer["estimate_status"] == "unavailable"
    assert "motif_background_requires_explicit_scanned_hit_or_no_hit_calls" in answer["limitation_codes"]


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason=NO_DATA)
def test_control_normalized_factorial_response_is_not_log_interaction():
    import numpy as np
    from devbench.study.biology_tools_singlecell import _epistasis_estimate

    class Fit:
        params = np.log([10.0, 20.0, 30.0, 80.0])

        @staticmethod
        def cov_params():
            return np.eye(4) * 0.01

    values, intervals, p_value = _epistasis_estimate(Fit(), np.eye(4))
    assert np.isclose(values[0], (80 - 20 - 30 + 10) / 10)
    assert intervals[0]["lower"] < values[0] < intervals[0]["upper"]
    assert 0 <= p_value <= 1
