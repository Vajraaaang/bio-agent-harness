"""No-data acceptance of the declared original airway object, never a trial."""
from copy import deepcopy
import importlib

import pytest


def _api():
    return importlib.import_module("devbench.analysis.airway_contract")


def _manifest():
    return {
        "format": "devbench.airway-input-declaration/1",
        "source": {
            "citation": "Love et al., F1000Research 2015, 4:1070",
            "doi": "10.12688/f1000research.7035.1",
            "article_version": 1,
            "data_package": "airway",
            "data_package_version": "0.102.0",
        },
        "measurement": "raw_counts",
        "shape": [64102, 8],
        "design": {
            "formula": "~ cell + dex",
            "biological_unit_column": "cell",
            "biological_unit_type": "donor_derived_cell_line",
            "blocking_column": "cell",
            "condition_column": "dex",
            "reference_condition": "untrt",
            "comparison_condition": "trt",
        },
        "samples": [
            {"sample_id": f"synthetic_library_{i}_{dex}", "cell": f"synthetic_line_{i}", "dex": dex}
            for i in range(4) for dex in ("untrt", "trt")
        ],
    }


def test_contract_names_exact_historical_object_without_counts():
    api = _api()
    contract = api.airway_input_contract()
    assert contract["source"] == _manifest()["source"]
    assert contract["shape"] == [64102, 8]
    assert contract["design"] == _manifest()["design"]
    assert contract["validation_scope"] == "declared_metadata_only"
    assert contract["counts_required_by_validator"] is False
    contract["shape"][0] = 63677
    assert api.airway_input_contract()["shape"] == [64102, 8]


def test_declared_four_pairs_have_rank_five_but_establish_no_trial():
    manifest = _manifest()
    retained = deepcopy(manifest)
    audit = _api().validate_airway_input_contract(manifest)
    assert manifest == retained
    assert audit["status"] == "declared_contract_satisfied"
    assert audit["biological_unit_count"] == 4
    assert audit["library_count"] == 8
    assert audit["design_rank"] == 5
    assert audit["residual_degrees_of_freedom"] == 3
    assert audit["counts_inspected"] is False
    assert audit["scientific_validity_established"] is False
    assert audit["trial_performed"] is False
    assert audit["biological_agreement"] == "not_run"
    assert audit["authorization_scope"] == "no_execution_or_model_authority_granted"
    assert "model_execution_authorized" not in audit


@pytest.mark.parametrize("shape", [[63677, 8], [64102, 7], [64102.0, 8], [64102, True]])
def test_substituted_shape_is_rejected_without_a_matrix(shape):
    manifest = _manifest()
    manifest["shape"] = shape
    with pytest.raises(ValueError, match="64,102 genes and eight libraries"):
        _api().validate_airway_input_contract(manifest)


@pytest.mark.parametrize("key,value", [("data_package_version", "1.0.0"),
    ("article_version", 2), ("article_version", True), ("doi", "10.12688/f1000research.7035.2"),
    ("data_package", "airway_subset")])
def test_wrong_source_pin_is_rejected(key, value):
    manifest = _manifest()
    manifest["source"][key] = value
    with pytest.raises(ValueError, match="original source"):
        _api().validate_airway_input_contract(manifest)


@pytest.mark.parametrize("field,value", [("biological_unit_column", "sample_id"),
    ("biological_unit_type", "library"), ("blocking_column", "sample_id"),
    ("formula", "~ dex"), ("reference_condition", "trt")])
def test_libraries_cannot_substitute_for_paired_units(field, value):
    manifest = _manifest()
    manifest["design"][field] = value
    with pytest.raises(ValueError, match="paired cell-line design"):
        _api().validate_airway_input_contract(manifest)


def test_wrong_measurement_is_rejected():
    manifest = _manifest()
    manifest["measurement"] = "log_counts"
    with pytest.raises(ValueError, match="raw_counts"):
        _api().validate_airway_input_contract(manifest)


@pytest.mark.parametrize("mutation", ["three_lines", "missing_pair", "unknown_unit", "duplicate_library", "wrong_condition"])
def test_invalid_membership_declarations_are_rejected(mutation):
    manifest = _manifest()
    rows = manifest["samples"]
    if mutation == "three_lines":
        for row in rows:
            if row["cell"] == "synthetic_line_3":
                row["cell"] = "synthetic_line_2"
    elif mutation == "missing_pair":
        rows[1]["dex"] = "untrt"
    elif mutation == "unknown_unit":
        rows[0]["cell"] = "unknown"
    elif mutation == "duplicate_library":
        rows[1]["sample_id"] = rows[0]["sample_id"]
    else:
        rows[0]["dex"] = "other"
    with pytest.raises(ValueError):
        _api().validate_airway_input_contract(manifest)


def test_count_payload_or_path_is_outside_metadata_contract():
    manifest = _manifest()
    manifest["counts_path"] = "/does/not/exist/airway-counts.csv"
    with pytest.raises(ValueError, match="metadata-only"):
        _api().validate_airway_input_contract(manifest)


def test_incomplete_declaration_rejects_without_defaulting():
    manifest = _manifest()
    del manifest["source"]["data_package_version"]
    with pytest.raises(ValueError, match="original source"):
        _api().validate_airway_input_contract(manifest)
