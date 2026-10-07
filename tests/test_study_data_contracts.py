"""Configuration and refusal checks only; no expression data is created or read."""

import builtins
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from devbench.study.contracts import DatasetRecipe


def api():
    source = Path(__file__).resolve().parents[1] / "devbench" / "study" / "data.py"
    assert source.is_file(), "Opt-in study data API is not implemented"
    return importlib.import_module("devbench.study.data")


def test_import_does_not_import_scientific_dependencies(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "devbench" / "study" / "data.py"
    assert source.is_file(), "Opt-in study data API is not implemented"
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "zarr", "devharness_core"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(importlib.import_module("devbench.study.data"))


@pytest.mark.parametrize("entrypoint", ["generate_synthetic", "prepare_dataset"])
@pytest.mark.parametrize("permission", [None, False, 1, "true"])
def test_default_denial_precedes_import_path_access_or_argument_validation(monkeypatch, entrypoint, permission):
    module = api()

    def forbidden(*args, **kwargs):
        pytest.fail("Default-denied call touched an import or filesystem path")

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"numpy", "pandas", "zarr", "devharness_core"}:
            forbidden()
        return original_import(name, *args, **kwargs)

    kwargs = {} if permission is None else {"allow_data_execution": permission}
    arguments = (object(), object()) if entrypoint == "generate_synthetic" else (object(), object(), object())
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "__import__", guarded_import)
        blocked.setattr(builtins, "open", forbidden)
        for name in ("resolve", "open", "stat", "mkdir", "exists", "is_symlink"):
            blocked.setattr(Path, name, forbidden)
        with pytest.raises(module.DataExecutionDisabled, match="allow_data_execution=True"):
            getattr(module, entrypoint)(*arguments, **kwargs)


def test_recipe_support_check_is_configuration_only_and_does_not_assign_seed(monkeypatch):
    module = api()
    recipe = DatasetRecipe(recipe_id="proposal", scenario="balanced_temporal")
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "zarr", "devharness_core"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    checked = module.validate_synthetic_recipe(recipe)
    assert checked is recipe
    assert "generation_seed" not in checked.model_dump()
    assert checked.sample_count == 72
    assert checked.independent_unit_count == 72


@pytest.mark.parametrize("updates,reason", [
    ({"scenario": "partial_confounding", "num_batches": 1, "batch_labels": ("b1",)}, "partial"),
    ({"scenario": "partial_confounding", "biological_units_per_cell": 1}, "partial"),
    ({"num_genes": 1}, "at least 10"),
    ({"source_sha256": "a" * 64}, "source_sha256"),
])
def test_unsupported_generation_recipes_are_rejected_without_execution(updates, reason):
    recipe = DatasetRecipe(**{"recipe_id": "proposal", "scenario": "balanced_temporal", **updates})
    with pytest.raises(ValueError, match=reason):
        api().validate_synthetic_recipe(recipe)


def test_registry_is_metadata_only_and_preserves_unverified_source_limits():
    registry = api().load_public_sources()
    assert registry["record_format"] == "devbench.study.public_sources/1"
    assert registry["automatic_downloads"] is False
    assert registry["fallback_to_synthetic"] is False
    sources = {source["source_id"]: source for source in registry["sources"]}
    assert set(sources) == {"E-MTAB-6798", "GSE47774", "dyngen", "scDesign3", "Perturb-seq"}
    for source in sources.values():
        assert source["status"] in {"not_acquired", "reference_only"}
        assert source["input_sha256"] is None
        assert source["license_status"] == "pending_verification"
        assert source["selection_status"] == "not_selected"
        assert source["metadata_requirements"]
        assert all(url.startswith("https://") for url in source["primary_urls"])
    mouse = sources["E-MTAB-6798"]
    assert mouse["measurement_type"] == "continuous"
    assert mouse["expression_units"] == "RPKM"
    assert mouse["historical_cache_note"]["biological_unit_provenance"] == "unknown"
    assert mouse["historical_cache_note"]["revalidated"] is False
    assert mouse["complete_causal_truth"] is False
    assert sources["GSE47774"]["biological_unit_scope"] == "pooled_reference_rna"
    assert len(json.dumps(registry).encode()) < 100_000


def public_manifest(**updates):
    return {
        "record_format": "devbench.study.public_manifest/1",
        "source_id": "E-MTAB-6798",
        "classification": "public_research",
        "primary_url": "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6798",
        "input_sha256": "a" * 64,
        "measurement_type": "continuous",
        "expression_units": "RPKM",
        "biological_unit_scope": "unverified",
        "reference_scope": "observational_expression",
        "complete_causal_truth": False,
        "complete_pseudotime_truth": False,
        **updates,
    }


def test_public_manifest_validation_never_claims_file_verification(monkeypatch):
    module = api()

    def forbidden(*args, **kwargs):
        pytest.fail("Pure manifest validation accessed files")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    result = module.validate_public_manifest(public_manifest())
    assert result["validation_status"] == "contract_only"
    assert result["input_hash_verified"] is False
    assert result["biological_units_verified"] is False


@pytest.mark.parametrize("updates", [
    {"classification": "private_clinical"}, {"classification": "private"},
    {"input_sha256": None}, {"input_sha256": "unverified"},
    {"measurement_type": None}, {"biological_unit_scope": None},
    {"complete_causal_truth": True}, {"complete_pseudotime_truth": True},
    {"measurement_type": "raw_counts"}, {"expression_units": "counts"},
    {"primary_url": "file:///private/input"}, {"input_hash_verified": True},
    {"biological_units_verified": True}, {"reference_scope": "complete_causal_truth"},
])
def test_manifest_rejects_unsafe_or_inconsistent_declarations(updates):
    with pytest.raises(ValueError):
        api().validate_public_manifest(public_manifest(**updates))


@pytest.mark.parametrize("missing", ["input_sha256", "measurement_type", "biological_unit_scope"])
def test_manifest_requires_hash_measurement_and_unit_scope(missing):
    manifest = public_manifest()
    del manifest[missing]
    with pytest.raises(ValueError):
        api().validate_public_manifest(manifest)


def test_public_manifest_schema_has_closed_provenance_contract():
    schema = api().PublicManifest.model_json_schema()
    assert schema["additionalProperties"] is False
    assert {"input_sha256", "measurement_type", "biological_unit_scope"} <= set(schema["required"])
    assert schema["properties"]["classification"]["const"] == "public_research"
    assert schema["properties"]["input_hash_verified"]["const"] is False
    assert schema["properties"]["biological_units_verified"]["const"] is False


@pytest.mark.parametrize("family", ["negative_binomial", "poisson_lognormal"])
@pytest.mark.parametrize("scenario", [
    "balanced_temporal", "partial_confounding", "complete_aliasing",
    "temporal_null", "library_composition", "technical_replicates",
])
def test_all_scenario_proposals_have_supported_configuration_without_generation(family, scenario):
    fields = {"recipe_id": "proposal", "scenario": scenario, "generator_family": family}
    if scenario == "complete_aliasing":
        fields.update(num_batches=6, batch_labels=("b1", "b2", "b3", "b4", "b5", "b6"), biological_units_per_cell=12)
    if scenario == "technical_replicates":
        fields.update(biological_unit_policy="single_biological_series", biological_units_per_cell=1,
                      technical_replicates_per_unit=4)
    recipe = api().validate_synthetic_recipe(DatasetRecipe(**fields))
    assert recipe.sample_count == 72
    assert recipe.independent_unit_count == (1 if scenario == "technical_replicates" else 72)


def test_synthetic_curve_reference_contract_fixes_batch_and_population_scope():
    module = api()
    assert hasattr(module, "SyntheticCurveReference"), "Shared curve-reference contract is not implemented"
    reference = module.SyntheticCurveReference(reference_batch="batch2")
    assert reference.model_dump() == {
        "reference_batch": "batch2", "library_factor": 1.0,
        "batch_reference_policy": "first_observation", "biological_unit_reference": "population_mean",
    }
    schema = module.SyntheticCurveReference.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "reference_batch" in schema["required"]
    assert schema["properties"]["library_factor"]["const"] == 1.0


@pytest.mark.parametrize("updates", [
    {"reference_batch": ""}, {"library_factor": 2.0},
    {"batch_reference_policy": "equal_batch_average"},
    {"biological_unit_reference": "first_observed_unit"},
])
def test_synthetic_curve_reference_rejects_conflicting_estimands(updates):
    module = api()
    assert hasattr(module, "SyntheticCurveReference"), "Shared curve-reference contract is not implemented"
    with pytest.raises(ValueError):
        module.SyntheticCurveReference(**{"reference_batch": "batch2", **updates})


def test_seqc_manifest_rejects_umi_units_for_registered_read_counts():
    manifest = public_manifest(
        source_id="GSE47774", primary_url="https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE47774",
        measurement_type="raw_counts", expression_units="read_counts", biological_unit_scope="pooled_reference_rna",
        reference_scope="mixture_and_spikein_controls",
    )
    assert api().validate_public_manifest(manifest)["expression_units"] == "read_counts"
    with pytest.raises(ValueError):
        api().validate_public_manifest({**manifest, "expression_units": "UMI_counts"})


@pytest.mark.parametrize("num_genes,count", [(10, 1), (100, 1), (1000, 10), (2000, 20), (100_000, 20)])
def test_reference_control_budget_depends_only_on_public_gene_universe(num_genes, count):
    module = api()
    assert hasattr(module, "reference_control_count"), "Public-universe control budget is not implemented"
    assert module.reference_control_count(num_genes) == count
    assert count < num_genes // 2


def test_reference_control_stream_has_its_own_declared_rng_namespace():
    module = api()
    assert hasattr(module, "SYNTHETIC_RNG_STREAMS"), "Independent control RNG stream is not declared"
    assert module.SYNTHETIC_RNG_STREAMS == (
        "layout", "identifiers", "programs", "nuisance", "observations", "reference_controls",
    )


@pytest.mark.parametrize("num_genes", [True, 9, 0, "100", 10.0])
def test_reference_control_budget_rejects_invalid_universe_configuration(num_genes):
    module = api()
    assert hasattr(module, "reference_control_count"), "Public-universe control budget is not implemented"
    with pytest.raises(ValueError):
        module.reference_control_count(num_genes)


def test_shipped_registry_matches_closed_metadata_schema():
    module = api()
    assert hasattr(module, "validate_public_sources"), "Complete registry schema validation is not implemented"
    registry = module.load_public_sources()
    assert module.validate_public_sources(registry) == registry


@pytest.mark.parametrize("field,value", [
    ("source_id", "unknown"), ("source_kind", "private_clinical"),
    ("measurement_type", "unknown"), ("expression_units", "unknown"),
    ("status", "verified"), ("license_status", "verified"), ("selection_status", "selected"),
    ("input_sha256", "a" * 64), ("metadata_sha256", "a" * 64),
    ("acquisition_status", "acquired"), ("primary_urls", ["http://example.org/study"]),
    ("primary_urls", ["https://secret@example.org/study"]), ("primary_urls", []),
    ("metadata_requirements", []), ("metadata_requirements", "sample_id"),
    ("complete_causal_truth", True), ("complete_pseudotime_truth", True),
    ("biological_unit_scope", "independence_verified"),
])
def test_registry_rejects_incomplete_or_overclaimed_source_metadata(field, value):
    module = api()
    assert hasattr(module, "validate_public_sources"), "Complete registry schema validation is not implemented"
    registry = deepcopy(module.load_public_sources())
    registry["sources"][0][field] = value
    with pytest.raises(ValueError):
        module.validate_public_sources(registry)


@pytest.mark.parametrize("field", ["measurement_type", "input_sha256", "metadata_requirements", "primary_urls"])
def test_registry_requires_explicit_measurement_provenance_and_reference_fields(field):
    module = api()
    assert hasattr(module, "validate_public_sources"), "Complete registry schema validation is not implemented"
    registry = deepcopy(module.load_public_sources())
    del registry["sources"][0][field]
    with pytest.raises(ValueError):
        module.validate_public_sources(registry)


@pytest.mark.parametrize("field,value", [
    ("automatic_downloads", True), ("automatic_downloads", 0),
    ("fallback_to_synthetic", True), ("raw_archive_downloads_permitted", True),
    ("record_format", "unknown"), ("sources", []),
])
def test_registry_rejects_wrong_top_level_policy_types_and_status(field, value):
    module = api()
    assert hasattr(module, "validate_public_sources"), "Complete registry schema validation is not implemented"
    registry = deepcopy(module.load_public_sources())
    registry[field] = value
    with pytest.raises(ValueError):
        module.validate_public_sources(registry)


def test_registry_rejects_duplicate_sources_wrong_units_and_claimed_cache_revalidation():
    module = api()
    assert hasattr(module, "validate_public_sources"), "Complete registry schema validation is not implemented"
    for mutation in ("duplicate", "wrong_units", "historical_revalidated"):
        registry = deepcopy(module.load_public_sources())
        if mutation == "duplicate":
            registry["sources"][1] = deepcopy(registry["sources"][0])
        elif mutation == "wrong_units":
            registry["sources"][1]["expression_units"] = "RPKM"
        else:
            registry["sources"][0]["historical_cache_note"]["revalidated"] = True
        with pytest.raises(ValueError):
            module.validate_public_sources(registry)
