"""Catalog registration is distinct from dataset and software verification."""
from copy import deepcopy
import json

import pytest


def _api():
    from devbench.analysis import figure_catalog
    assert hasattr(figure_catalog, "scientific_plot_catalog"), "scientific subtype catalog is absent"
    assert hasattr(figure_catalog, "scientific_plot_workspace_ref"), "workspace binding is absent"
    return figure_catalog


def _workspace(capability="feature_expression"):
    from devbench.analysis.scientific_plot_suite import prepare_scientific_plot_workspace
    from tests.test_scientific_plot_suite import fixtures
    request = next(row for row in fixtures() if row["capability"] == capability)
    return prepare_scientific_plot_workspace(
        {"format": "devbench.scientific-plot-input/1", "plots": [request]},
        source={"sha256": "a" * 64, "run_id": "synthetic-catalog", "kind": "synthetic"},
    )


def _evidence(capability, workspace_ref):
    return {
        "capability": capability, "scope": "software_rendering", "status": "passed",
        "tested_workspace_sha256": workspace_ref["sha256"], "source_kind": "synthetic",
        "implementation_sha256": "d" * 64,
        "checks": {name: "passed" for name in (
            "source_binding", "axis_units", "full_range", "browser_rendering", "exports")},
        "evidence_refs": [{"path": "synthetic-catalog-checks.json", "sha256": "b" * 64}],
    }


def _by_id(records):
    return {row["id"]: row for row in records}


def test_every_actual_builder_has_an_explicit_honest_mapping():
    from devbench.analysis.scientific_plot_suite import capability_catalog
    api = _api()
    actual = {row["id"] for row in capability_catalog()}
    records = api.scientific_plot_catalog()
    implemented = {row["id"] for row in records if row["implementation_status"] == "implemented"}
    assert implemented == actual
    for row in records:
        assert row["canonical_ids"] or row["extension_ids"]
        assert row["scientific_validity_established"] is False
        assert not row["eligibility"]["eligible"]
        assert row["verification_status"] == "not_verified"
    assert json.loads(json.dumps(records, allow_nan=False)) == records


def test_current_builders_are_implemented_but_do_not_claim_verified_data():
    from devbench.analysis.scientific_plot_suite import capability_catalog

    api = _api()
    records = _by_id(api.figure_catalog())
    for name in ("G01", "G03", "G04", "G19", "G20", "G21", "G22"):
        assert records[name]["renderer_status"] == "implemented"
        assert records[name]["supported_subtypes"]
        assert not records[name]["eligibility"]["eligible"]
    assert records["G19"]["supported_subtypes"] == ["rna_velocity"]
    assert records["G20"]["supported_subtypes"] == ["genomic_effect", "splice_tracks"]
    registered = {row["id"] for row in capability_catalog()}
    assert set(records["G21"]["supported_subtypes"]) == registered & {
        "spatial_expression", "spatial_tissue_overlay", "spatial_segmentation"}
    assert records["G22"]["supported_subtypes"] == ["orthology_comparison", "perturbation_control"]


def test_repertoire_multimodal_and_bulk_extensions_do_not_reassign_families():
    records = _by_id(_api().scientific_plot_catalog())
    for name in ("repertoire_abundance", "repertoire_overlap", "repertoire_diversity"):
        assert records[name]["canonical_ids"] == []
        assert records[name]["extension_ids"] == ["repertoire_vdj"]
    for name in ("multimodal_features", "multimodal_heatmap"):
        assert records[name]["canonical_ids"] == []
        assert records[name]["extension_ids"] == ["multimodal"]
    for name in ("bulk_library_qc", "bulk_pvalue_histogram", "bulk_independent_filtering"):
        assert records[name]["canonical_ids"] == []
        assert records[name]["extension_ids"]


def test_partial_families_keep_unimplemented_named_subtypes_visible():
    api = _api()
    rows = _by_id(api.figure_catalog())
    for family in ("G03", "G06"):
        assert rows[family]["implementation_scope"] == "listed_subtypes_only"
    subtypes = _by_id(api.scientific_plot_catalog())
    if "ambient_rna_diagnostics" not in rows["G03"]["supported_subtypes"]:
        assert "ambient_rna" in rows["G03"]["unimplemented_subtypes"]
        assert subtypes["ambient_rna"]["implementation_status"] == "not_yet_implemented"
    else:
        assert "ambient_rna" not in subtypes
    if not all(name in rows["G06"]["supported_subtypes"] for name in
               ("bulk_residual_diagnostics", "bulk_design_diagnostics")):
        assert "residual_design" in rows["G06"]["unimplemented_subtypes"]
        assert subtypes["residual_design"]["eligibility"]["reason"] == "renderer_not_implemented"


def test_named_residual_and_design_subtypes_retain_the_audit_requirement():
    names = ["bulk_residual_diagnostics", "bulk_design_diagnostics"]
    rows = _by_id(_api().scientific_plot_catalog(requested_capabilities=names))
    for name in names:
        assert rows[name]["canonical_ids"] == ["G06"]
        assert rows[name]["audit_requirement_ids"] == ["S29"]
    if all(rows[name]["implementation_status"] == "implemented" for name in names):
        assert "residual_design" not in rows


def test_named_ambient_subtype_does_not_alias_doublet_evidence():
    rows = _by_id(_api().scientific_plot_catalog(requested_capabilities=["ambient_rna_diagnostics"]))
    assert rows["ambient_rna_diagnostics"]["canonical_ids"] == ["G03"]
    assert rows["ambient_rna_diagnostics"]["audit_requirement_ids"] == ["S09"]
    assert "S09" not in rows["doublet_diagnostics"]["audit_requirement_ids"]


def test_mean_variance_is_distinct_from_fitted_dispersion():
    rows = _by_id(_api().scientific_plot_catalog(requested_capabilities=["bulk_mean_variance"]))
    assert rows["bulk_mean_variance"]["canonical_ids"] == ["G05"]
    assert rows["bulk_mean_variance"]["audit_requirement_ids"] == ["B10"]
    assert "B10" not in rows["bulk_dispersion"]["audit_requirement_ids"]


@pytest.mark.parametrize("name,audit_ids", [
    ("scatter_marginals", ["F18"]),
    ("measurement_correlation", ["Q10"]),
])
def test_generic_diagnostics_do_not_claim_sample_correlation(name, audit_ids):
    api = _api()
    rows = _by_id(api.scientific_plot_catalog(requested_capabilities=[name]))
    assert rows[name]["canonical_ids"] == []
    assert rows[name]["extension_ids"] == [name]
    assert rows[name]["audit_requirement_ids"] == audit_ids
    assert rows[name]["extensions"][0]["name"]
    assert name not in _by_id(api.figure_catalog())["G04"]["supported_subtypes"]
    assert rows[name]["verification_status"] == "not_verified"


@pytest.mark.parametrize("name", ["spatial_tissue_overlay", "spatial_segmentation"])
def test_named_spatial_subtypes_stay_unimplemented_until_registered(name):
    from devbench.analysis.scientific_plot_suite import capability_catalog

    api = _api()
    rows = _by_id(api.scientific_plot_catalog(requested_capabilities=[name]))
    assert rows[name]["canonical_ids"] == ["G21"]
    assert rows[name]["audit_requirement_ids"] == ["D21"]
    registered = {row["id"] for row in capability_catalog()}
    expected = "implemented" if name in registered else "not_yet_implemented"
    assert rows[name]["implementation_status"] == expected
    assert rows[name]["verification_status"] == "not_verified"
    assert rows[name]["eligibility"]["eligible"] is False
    spatial = _by_id(api.figure_catalog())["G21"]
    assert (name in spatial["supported_subtypes"]) == (name in registered)
    assert (name in spatial["unimplemented_subtypes"]) == (name not in registered)


def test_bare_claims_never_promote_new_subtypes():
    api = _api()
    all_inputs = {name for row in api.figure_catalog() for name in row["required_inputs"]}
    rows = api.figure_catalog(available_families=[f"G{i:02d}" for i in range(1, 23)], capabilities=all_inputs)
    assert {row["id"] for row in rows if row["eligibility"]["eligible"]} == {"G02", "G08"}
    with pytest.raises(ValueError, match="verification"):
        api.scientific_plot_catalog(verification_evidence=[{"capability": "feature_expression", "verified": True}])


def test_unknown_renderer_and_absent_dataset_are_distinct():
    api = _api()
    records = _by_id(api.scientific_plot_catalog(requested_capabilities=["not_a_renderer"]))
    assert records["feature_expression"]["implementation_status"] == "implemented"
    assert records["feature_expression"]["eligibility"]["reason"] == "required_saved_inputs_absent"
    assert records["feature_expression"]["data_availability"]["missing_inputs"]
    unknown = records["not_a_renderer"]
    assert unknown["implementation_status"] == "not_yet_implemented"
    assert unknown["eligibility"]["reason"] == "renderer_not_implemented"


def test_bound_workspace_does_not_manufacture_software_verification():
    api = _api()
    workspace = _workspace()
    reference = api.scientific_plot_workspace_ref(workspace)
    records = _by_id(api.scientific_plot_catalog(workspace, workspace_ref=reference))
    feature = records["feature_expression"]
    assert feature["data_availability"]["available"] is True
    assert feature["verification_status"] == "not_verified"
    assert feature["eligibility"] == {
        "eligible": False, "reason": "software_verification_required", "missing_inputs": [],
    }
    assert not records["rna_velocity"]["data_availability"]["available"]


def test_source_bound_verified_subtype_enables_only_its_actual_family():
    api = _api()
    workspace = _workspace()
    reference = api.scientific_plot_workspace_ref(workspace)
    evidence = [_evidence("feature_expression", reference)]
    subtypes = _by_id(api.scientific_plot_catalog(workspace, workspace_ref=reference,
                     implementation_sha256="d" * 64, verification_evidence=evidence))
    assert subtypes["feature_expression"]["verification_status"] == "verified"
    assert subtypes["feature_expression"]["eligibility"]["eligible"] is True
    assert subtypes["feature_expression"]["status"] == "implemented_and_verified"
    records = api.figure_catalog(scientific_workspace=workspace, scientific_workspace_ref=reference,
                                 implementation_sha256="d" * 64, verification_evidence=evidence)
    assert {row["id"] for row in records if row["eligibility"]["eligible"]} == {"G09"}
    feature = _by_id(records)["G09"]
    assert feature["eligible_subtypes"] == ["feature_expression"]
    assert feature["verification_level"] == "verified_scientific_plot_workspace"


@pytest.mark.parametrize("change", ["coordinates", "source", "capability", "marks"])
def test_tampered_workspace_is_rejected_against_prior_binding(change):
    api = _api()
    workspace = _workspace()
    reference = api.scientific_plot_workspace_ref(workspace)
    changed = deepcopy(workspace)
    if change == "coordinates":
        changed["panels"][0]["rows"][0]["x"] += 1
    elif change == "source":
        changed["source"]["sha256"] = "f" * 64
    elif change == "capability":
        changed["panels"][0]["capability"] = "rna_velocity"
    else:
        changed["panels"][0]["marks"][0]["radius"] += 1
    with pytest.raises(ValueError, match="binding"):
        api.scientific_plot_catalog(changed, workspace_ref=reference)


def test_workspace_requires_independent_binding_and_valid_schema():
    api = _api()
    workspace = _workspace()
    with pytest.raises(ValueError, match="binding"):
        api.scientific_plot_catalog(workspace)
    changed = deepcopy(workspace)
    changed["panels"][0]["axes"]["x"]["unit"] = ""
    with pytest.raises(ValueError, match="unit"):
        api.scientific_plot_workspace_ref(changed)


@pytest.mark.parametrize("change", ["implementation", "failed_check", "missing_check", "missing_ref", "scientific_claim"])
def test_invalid_or_unbound_verification_cannot_promote(change):
    api = _api()
    workspace = _workspace()
    reference = api.scientific_plot_workspace_ref(workspace)
    evidence = _evidence("feature_expression", reference)
    if change == "implementation":
        evidence["implementation_sha256"] = "c" * 64
    elif change == "failed_check":
        evidence["checks"]["exports"] = "failed"
    elif change == "missing_check":
        evidence["checks"].pop("axis_units")
    elif change == "missing_ref":
        evidence["evidence_refs"] = []
    else:
        evidence["scope"] = "scientific_validation"
    with pytest.raises(ValueError, match="verification"):
        api.scientific_plot_catalog(workspace, workspace_ref=reference,
                                   implementation_sha256="d" * 64, verification_evidence=[evidence])


def test_software_verification_and_current_dataset_binding_are_independent():
    api = _api()
    tested = _workspace()
    evidence = [_evidence("feature_expression", api.scientific_plot_workspace_ref(tested))]
    current = deepcopy(tested)
    current["source"] = {"sha256": "e" * 64, "run_id": "different-retained-dataset", "kind": "retained"}
    current_ref = api.scientific_plot_workspace_ref(current)
    assert current_ref["sha256"] != evidence[0]["tested_workspace_sha256"]
    rows = _by_id(api.scientific_plot_catalog(current, workspace_ref=current_ref,
                    implementation_sha256="d" * 64, verification_evidence=evidence))
    feature = rows["feature_expression"]
    assert feature["eligibility"]["eligible"] is True
    assert feature["implementation_verification"]["status"] == "verified"
    assert feature["implementation_verification"]["implementation_sha256"] == "d" * 64
    assert feature["scientific_validity_established"] is False
    absent = _by_id(api.scientific_plot_catalog(implementation_sha256="d" * 64,
                                              verification_evidence=evidence))["feature_expression"]
    assert absent["implementation_verification"]["status"] == "verified"
    assert not absent["data_availability"]["available"]
    assert absent["status"] == "implemented_but_unavailable"


def test_software_evidence_needs_an_independent_current_implementation_binding():
    api = _api()
    workspace = _workspace()
    evidence = [_evidence("feature_expression", api.scientific_plot_workspace_ref(workspace))]
    with pytest.raises(ValueError, match="verification"):
        api.scientific_plot_catalog(verification_evidence=evidence)


def test_all_saved_adapter_fixtures_bind_only_their_actual_capabilities():
    from devbench.analysis.scientific_plot_suite import prepare_scientific_plot_workspace
    from tests.test_scientific_plot_suite import fixtures

    api = _api()
    requests = fixtures()
    for start in range(0, len(requests), 32):
        batch = requests[start:start + 32]
        workspace = prepare_scientific_plot_workspace(
            {"format": "devbench.scientific-plot-input/1", "plots": batch},
            source={"sha256": "a" * 64, "run_id": "synthetic-catalog-all", "kind": "synthetic"},
        )
        reference = api.scientific_plot_workspace_ref(workspace)
        names = {request["capability"] for request in batch}
        records = api.scientific_plot_catalog(workspace, workspace_ref=reference,
            implementation_sha256="d" * 64,
            verification_evidence=[_evidence(name, reference) for name in sorted(names)])
        assert {r["id"] for r in records if r["eligibility"]["eligible"]} == names
        assert all(r["data_availability"]["workspace_sha256"] == reference["sha256"]
                   for r in records if r["eligibility"]["eligible"])


def test_unknown_panel_capability_cannot_gain_eligibility_from_valid_geometry():
    api = _api()
    workspace = _workspace()
    workspace["panels"][0]["capability"] = "not_a_renderer"
    reference = api.scientific_plot_workspace_ref(workspace)
    with pytest.raises(ValueError, match="renderer_not_implemented"):
        api.scientific_plot_catalog(workspace, workspace_ref=reference)


def test_workspace_with_only_saved_failures_stays_unavailable_even_for_verified_code():
    from devbench.analysis.scientific_plot_contract import panel, row

    api = _api()
    workspace = _workspace()
    previous = workspace["panels"][0]
    workspace["panels"] = [panel("feature_expression", "All saved values unavailable",
        [row("failed-cell", entity_kind="cell", reason="saved_measurement_failed")],
        axes=previous["axes"], marks=[])]
    workspace["entities"] = [{"kind": "cell", "id": "failed-cell", "label": "failed-cell",
                              "details": {}, "warnings": []}]
    reference = api.scientific_plot_workspace_ref(workspace)
    records = _by_id(api.scientific_plot_catalog(workspace, workspace_ref=reference,
        implementation_sha256="d" * 64,
        verification_evidence=[_evidence("feature_expression", reference)]))
    feature = records["feature_expression"]
    assert feature["verification_status"] == "verified"
    assert feature["eligibility"] == {"eligible": False, "reason": "no_renderable_saved_values", "missing_inputs": []}
    assert feature["data_availability"]["panel_ids"]
    assert feature["data_availability"]["available_panel_ids"] == []


def test_catalog_return_values_cannot_mutate_input_or_later_records():
    api = _api()
    workspace = _workspace()
    reference = api.scientific_plot_workspace_ref(workspace)
    original = deepcopy(workspace)
    first = api.scientific_plot_catalog(workspace, workspace_ref=reference)
    altered = deepcopy(first)
    altered[0]["required_inputs"].clear()
    altered[0]["canonical_ids"].append("G99")
    assert api.scientific_plot_catalog(workspace, workspace_ref=reference) == first
    assert workspace == original
