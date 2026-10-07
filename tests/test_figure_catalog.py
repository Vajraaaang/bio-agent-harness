"""Catalog claims are gated by implementation and verified figure availability."""
import json

import pytest


def test_catalog_registers_all_twenty_two_families_without_claiming_support():
    from devbench.analysis.figure_catalog import figure_catalog

    records = figure_catalog()
    assert [row["id"] for row in records] == [f"G{number:02d}" for number in range(1, 23)]
    assert json.loads(json.dumps(records, allow_nan=False)) == records
    for row in records:
        assert row["name"] and row["required_inputs"] and row["statistical_units"]
        assert row["planned_stage"] in {"M2a", "M2b", "M2c", "E1", "E2"}
        assert row["producer_status"] and row["renderer_status"] and row["verification_level"]
        assert row["eligibility"]["eligible"] is False
        assert row["eligibility"]["reason"]


def test_only_verified_current_qc_and_umap_families_become_eligible():
    from devbench.analysis.figure_catalog import figure_catalog

    records = figure_catalog(available_families=[f"G{number:02d}" for number in range(1, 23)])
    assert {row["id"] for row in records if row["eligibility"]["eligible"]} == {"G02", "G08"}
    for row in records:
        if row["id"] in {"G02", "G08"}:
            assert row["eligibility"] == {"eligible": True, "reason": None, "missing_inputs": []}
            assert row["verification_level"] == "verified_figure_artifact"
            assert "presentation_revision" in row["allowed_operations"]
        else:
            assert row["allowed_operations"] == []


def test_generic_capabilities_do_not_unlock_current_renderers():
    from devbench.analysis.figure_catalog import figure_catalog

    capabilities = {item for row in figure_catalog() for item in row["required_inputs"]}
    records = figure_catalog(capabilities=capabilities)
    assert not any(row["eligibility"]["eligible"] for row in records)
    for row in records:
        expected = "verified_figure_artifact_required" if row["id"] in {"G02", "G08"} else "renderer_not_implemented"
        assert row["eligibility"]["reason"] == expected


@pytest.mark.parametrize("family,missing", [
    ("G19", {"spliced_expression", "unspliced_expression", "fitted_velocity_kinetics"}),
    ("G20", {"junction_or_transcript_evidence", "genomic_coordinates"}),
    ("G21", {"spatial_coordinates", "coordinate_transforms"}),
])
def test_counts_alone_do_not_establish_specialized_prerequisites(family, missing):
    from devbench.analysis.figure_catalog import figure_catalog

    row = next(row for row in figure_catalog(capabilities=("gene_counts", "cell_ids")) if row["id"] == family)
    assert row["eligibility"]["eligible"] is False
    assert row["eligibility"]["reason"] == "missing_required_inputs"
    assert missing.issubset(row["eligibility"]["missing_inputs"])


@pytest.mark.parametrize("family", ["G04", "G06", "G13", "G14", "G15", "G16"])
def test_sample_inference_requires_independent_biological_units(family):
    from devbench.analysis.figure_catalog import figure_catalog

    row = next(row for row in figure_catalog() if row["id"] == family)
    capabilities = set(row["required_inputs"]) - {"independent_biological_units"}
    checked = next(row for row in figure_catalog(capabilities=capabilities) if row["id"] == family)
    assert checked["eligibility"] == {
        "eligible": False, "reason": "missing_required_inputs",
        "missing_inputs": ["independent_biological_units"],
    }


def test_claimed_future_inputs_never_promote_unimplemented_renderers():
    from devbench.analysis.figure_catalog import figure_catalog

    all_inputs = {item for row in figure_catalog() for item in row["required_inputs"]}
    records = figure_catalog(available_families=("G19", "G20", "G21"), capabilities=all_inputs)
    for row in records:
        if row["id"] not in {"G02", "G08"}:
            assert row["eligibility"] == {
                "eligible": False, "reason": "renderer_not_implemented", "missing_inputs": [],
            }
            assert row["verification_level"] == "requirements_only"


def test_umap_availability_does_not_claim_tsne_or_scientific_validation():
    from devbench.analysis.figure_catalog import figure_catalog

    row = next(row for row in figure_catalog(available_families=("G08",)) if row["id"] == "G08")
    assert row["supported_subtypes"] == ["umap"]
    assert "t-SNE" in row["limitations"]
    assert "biological validation" in row["limitations"]


def test_returned_nested_records_are_fresh_and_cannot_change_later_calls():
    from devbench.analysis.figure_catalog import figure_catalog

    original = figure_catalog(available_families=("G02",))
    changed = figure_catalog(available_families=("G02",))
    changed[0]["required_inputs"].clear()
    changed[0]["statistical_units"].append("invented_unit")
    changed[0]["eligibility"]["missing_inputs"].clear()
    changed[1]["allowed_operations"].append("numerical_stage")
    assert figure_catalog(available_families=("G02",)) == original


def test_t14_remains_visible_without_declaring_its_route_supported():
    from devbench.analysis.figure_catalog import figure_catalog

    volcano = next(row for row in figure_catalog() if row["id"] == "G14")
    assert "T14" in volcano["limitations"]
    assert "effect_definition_mismatch" in volcano["limitations"]
    assert volcano["eligibility"]["eligible"] is False
