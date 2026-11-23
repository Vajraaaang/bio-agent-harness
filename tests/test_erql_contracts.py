"""Synthetic registered-input and design binding for ERQL T06."""
import csv
from dataclasses import replace
import hashlib
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest

from devbench.analysis.methods import MethodService
from devbench.analysis.rna_scrna_input_contract import INVENTORY_BINDING_FORMAT, inspect_registered_scrna_input
from devbench.core.contrast_design import DesignSpec
from devbench.core.count_profiles import CountComponent, CountObservation, ProfileKey

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "reports/agentic-rna-v24/erql-build/smoke"


def prepare(tmp_path, *, reference=False, changed_group=False, profile="ERQL-1.1", add_filtered=False, stress=False,
            paired=False, population_pair=False, label_origin="independent_assignment", batch_binding=None):
    from devharness_controlled.erql_contracts import prepare_erql

    if reference:
        source = SMOKE / "synthetic-counts.tsv"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == "a02e4b9bd66430b6c27d97981e3d5c0d4b5dbafd73454a7dc5e6acc1dd4ec8cf"
        with source.open(newline="") as stream:
            reader = csv.reader(stream, delimiter="\t")
            ids = next(reader)[1:]
            rows = list(reader)
        genes = [row[0] for row in rows]
        matrix = np.array([row[1:] for row in rows], dtype=np.int64).T
        if stress:
            matrix, genes = matrix[:, :60].copy(), genes[:60]
            matrix[:, 0] = [0] * 4 + [100000, 110000, 120000, 130000]
        if add_filtered:
            genes.append("all_zero_control")
            matrix = np.column_stack([matrix, np.zeros(len(ids), dtype=np.int64)])
    else:
        ids = [f"synthetic_unit_{i}" for i in range(1, 9)]
        genes = ["g1", "g2", "filtered"]
        matrix = np.array([[14+i*2, 20+i, 0] for i in range(8)])
    groups = ["reference"] * 4 + ["target"] * 4
    donors = ids[:4] * 2 if paired or population_pair else ids
    conditions = ["fixed-condition"] * len(ids) if population_pair else groups
    populations = groups if population_pair else ["fixed"] * len(ids)
    batches = ["batch-a", "batch-b"] * (len(ids) // 2)
    path = tmp_path / "source.h5ad"
    metadata = {"donor": donors, "condition": conditions, "population": populations}
    if batch_binding is not None:
        metadata["batch"] = batches
    ann = ad.AnnData(np.log1p(matrix), obs=pd.DataFrame(metadata, index=ids),
                     var=pd.DataFrame(index=genes))
    if stress:
        from scipy.sparse import csr_matrix
        matrix = csr_matrix(matrix)
    ann.raw = ad.AnnData(matrix, obs=ann.obs.copy(), var=ann.var.copy())
    ann.write_h5ad(path)
    methods = MethodService(tmp_path / "methods")
    artifact = methods.import_artifact(path, artifact_type="anndata", actor="synthetic-fixture")
    inspection = inspect_registered_scrna_input(methods, {
        "format": INVENTORY_BINDING_FORMAT, "anndata_artifact_id": artifact["artifact_id"],
        "declarations": {"assay": "scrna", "measurement": "umi_counts", "counts_layer": "raw/X",
            "capture_unit_key": "donor", "capture_units_confirmed": True,
            "count_provenance": "Original integer software fixture", "annotation_provenance": "Fixed synthetic population",
            "biological_independence": {"independent": True, "unit_column": "donor", "evidence": "Fixture-defined units"}},
        "inherited_provenance": [{"path": "raw/X", "artifact_sha256": artifact["sha256"],
            "source": "Original integer software fixture", "representation": "original_counts"},
            {"path": "obs/population", "artifact_sha256": artifact["sha256"],
             "source": "Fixed synthetic population", "label_origin": label_origin}],
    })
    keys = tuple(ProfileKey(unit, specimen, condition, pop) for unit, specimen, condition, pop in
                 zip(donors, ids, conditions, populations, strict=True))
    components = tuple(CountComponent(unit, unit, unit, "synthetic-features", unit, 0, 1,
        hashlib.sha256(unit.encode()).hexdigest(), "umi_counts", () if batch_binding is None else
        (("batch", batch_binding(batch)),)) for unit, batch in zip(ids, batches, strict=True))
    observations = tuple(CountObservation(unit, key, unit, cell_id=unit) for unit, key in zip(ids, keys))
    if changed_group:
        observations = (CountObservation(ids[-1], keys[0], ids[0], cell_id=ids[-1]), *observations[1:])
    design = (DesignSpec("P-P", "target", "reference", condition="fixed-condition") if population_pair else
              DesignSpec("S-P" if paired else "S-I", "target", "reference", population="fixed"))
    if batch_binding is not None:
        design = replace(design, technical_factors=("batch",))
    return prepare_erql(methods, inspection, components=components, observations=observations,
        expected_profiles=keys, design_spec=design,
        condition_column="condition", population_column="population", study_id="synthetic-t06",
        data_classification="synthetic", profile=profile)


def test_erql_binds_raw_counts_and_exact_profile_order(tmp_path):
    plan, request = prepare(tmp_path)
    assert plan.profile == "ERQL-1.1"
    assert request["counts"][0] == [14, 20, 0]
    assert request["contrast"] == [0, 1]
    assert request["design"] == [[1, 0]] * 4 + [[1, 1]] * 4
    assert request["feature_ids"] == ["g1", "g2", "filtered"]


def test_erql_rejects_non_bijective_observation_mapping(tmp_path):
    with pytest.raises(ValueError, match="observation"):
        prepare(tmp_path, changed_group=True)


def prepare_design_aware(regime, *, label_origin=None, technical=None, missing=None, profile="ERQL-1.2"):
    from devharness_controlled.erql_contracts import prepare_erql_count_profiles
    from tests.test_erql_designs import profiles, spec

    design = spec(regime)
    if technical is not None:
        design = replace(design, technical_factors=("batch",))
    return prepare_erql_count_profiles(
        profiles(regime, technical=technical, missing=missing), design_spec=design,
        study_id="synthetic-design-aware", data_classification="synthetic",
        source_sha256="a" * 64, inspection_sha256="b" * 64, profile=profile,
        population_label_origin=label_origin,
    )


@pytest.mark.parametrize("regime", ["B-I", "B-P", "S-I", "S-P", "P-P"])
def test_erql_1_2_binds_full_design_and_target_regime(regime):
    from devharness_controlled.erql_contracts import driver_for_profile, parse_erql_plan

    def batches(key):
        reference = key.population == "A" if regime == "P-P" else key.condition == "base"
        return {"batch": "a" if (key.unit_id == "unit-0") == reference else "b"}

    plan, request = prepare_design_aware(
        regime, label_origin="independent_assignment" if regime == "P-P" else None,
        technical=batches,
    )
    assert plan == parse_erql_plan(plan)
    assert plan.profile == "ERQL-1.2" and request["schema_version"] == "erql_request/2"
    assert plan.allocation.filtering_policy == "erql-design-filter-by-expr/1"
    assert request["regime"] == regime
    assert np.linalg.matrix_rank(request["design"]) == len(request["contrast"])
    assert len(request["contrast"]) > 2
    assert request["contrast"][-1] == 1
    if regime == "P-P":
        assert request["condition"] == "fixed"
        assert request["population_label_basis"] == {"origin": "independent_assignment", "source_sha256": "a" * 64}
    else:
        assert request["condition"] is request["population_label_basis"] is None
    driver = driver_for_profile(plan.profile)
    assert isinstance(driver, Path) and driver.name == "erql_1_2.R"
    assert plan.driver_sha256 == hashlib.sha256(driver.read_bytes()).hexdigest()


@pytest.mark.parametrize("label_origin", [None, "external_reference_classification", "expression_based_study_annotation"])
def test_erql_1_2_requires_pp_labels_independent_of_tested_rna(label_origin):
    with pytest.raises(ValueError, match="P-P.*independent"):
        prepare_design_aware("P-P", label_origin=label_origin)


def test_erql_1_2_rejects_missing_pair_and_confounded_design():
    with pytest.raises(ValueError, match="incomplete_pair"):
        prepare_design_aware("B-P", missing=ProfileKey("unit-0", "unit-0-base", "base", None))
    with pytest.raises(ValueError, match="contrast_not_estimable"):
        prepare_design_aware("B-I", technical=lambda key: {"batch": key.condition})


def test_erql_1_2_requires_pairs_in_revalidated_signed_request():
    from devharness_controlled.erql_contracts import ERQLPlan, digest, validate_erql_request

    plan, request = prepare_design_aware("B-P")
    request["unit_ids"][-1] = "extra-unpaired-unit"
    signed_fields = plan.model_dump(exclude={"schema_version", "allocation", "family_id"})
    signed_fields["request_sha256"] = digest(request)
    with pytest.raises(ValueError, match="B-P.*complete pairs"):
        validate_erql_request(request, ERQLPlan(**signed_fields))


def test_erql_1_2_pp_registered_input_binds_observed_label_axis(tmp_path):
    plan, request = prepare(tmp_path, profile="ERQL-1.2", population_pair=True)
    assert request["condition"] == "fixed-condition"
    assert set(request["groups"]) == {"reference", "target"}
    assert request["population_label_basis"]["source_sha256"] == plan.source_sha256
    assert all(request["unit_ids"].count(unit) == 2 for unit in set(request["unit_ids"]))


def test_erql_1_2_pp_registered_input_refuses_classifier_labels(tmp_path):
    with pytest.raises(ValueError, match="independent label assignment"):
        prepare(tmp_path, profile="ERQL-1.2", population_pair=True,
                label_origin="external_reference_classification")


def test_erql_1_2_signed_pp_label_provenance_cannot_name_another_source():
    from devharness_controlled.erql_contracts import ERQLPlan, digest, validate_erql_request

    plan, request = prepare_design_aware("P-P", label_origin="orthogonal_measurement")
    request["population_label_basis"]["source_sha256"] = "c" * 64
    signed_fields = plan.model_dump(exclude={"schema_version", "allocation", "family_id"})
    signed_fields["request_sha256"] = digest(request)
    with pytest.raises(ValueError, match="source-bound independent labels"):
        validate_erql_request(request, ERQLPlan(**signed_fields))


def test_erql_1_2_binds_registered_batch_labels(tmp_path):
    _, request = prepare(tmp_path, profile="ERQL-1.2", batch_binding=lambda batch: batch)
    assert len(request["design"][0]) == 3


def test_erql_1_2_rejects_substituted_registered_batch_labels(tmp_path):
    with pytest.raises(ValueError, match="technical factor contradicts registered observation metadata"):
        prepare(tmp_path, profile="ERQL-1.2", batch_binding=lambda batch: batch + "-changed")


@pytest.mark.parametrize("regime", ["B-I", "B-P", "S-I", "S-P", "P-P"])
def test_erql_1_3_binds_distinct_filter_with_same_fit_design(regime):
    from devharness_controlled.erql_contracts import driver_for_profile, parse_erql_plan

    def batches(key):
        reference = key.population == "A" if regime == "P-P" else key.condition == "base"
        return {"batch": "a" if (key.unit_id == "unit-0") == reference else "b"}

    options = {
        "label_origin": "independent_assignment" if regime == "P-P" else None,
        "technical": batches,
    }
    historical, old_request = prepare_design_aware(regime, **options)
    successor, request = prepare_design_aware(regime, profile="ERQL-1.3", **options)
    assert successor == parse_erql_plan(successor)
    assert request == {**old_request, "profile": "ERQL-1.3", "schema_version": "erql_request/3"}
    assert historical.design_sha256 == successor.design_sha256
    assert historical.count_profiles_sha256 == successor.count_profiles_sha256
    assert historical.request_sha256 != successor.request_sha256
    assert historical.driver_sha256 != successor.driver_sha256
    assert successor.allocation.filtering_policy == "erql-group-filter-by-expr/1"
    driver = driver_for_profile(successor.profile)
    assert driver.name == "erql_1_3.R"
    assert successor.driver_sha256 == hashlib.sha256(driver.read_bytes()).hexdigest()


def test_erql_1_3_retains_source_bound_independent_population_labels():
    with pytest.raises(ValueError, match="P-P.*independent"):
        prepare_design_aware("P-P", profile="ERQL-1.3", label_origin="expression_based_study_annotation")


def test_erql_1_3_requires_its_signed_family_allocation():
    from devharness_controlled.erql_contracts import ERQLPlan, parse_erql_plan, validate_erql_request

    family_plan, request = prepare_design_aware("B-P", profile="ERQL-1.3")
    legacy_plan = ERQLPlan(**family_plan.model_dump(exclude={"schema_version", "allocation", "family_id"}))
    for validate in (lambda: parse_erql_plan(legacy_plan), lambda: validate_erql_request(request, legacy_plan)):
        with pytest.raises(ValueError, match="requires a signed family allocation"):
            validate()
