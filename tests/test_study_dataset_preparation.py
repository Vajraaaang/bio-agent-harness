"""Preparation contract checks. Data-producing checks are explicitly skipped."""

import builtins
import importlib
from pathlib import Path

import pytest

from devbench.study.contracts import CohortPlan, DatasetRecipe, StudyPlan, content_sha256


def api():
    source = Path(__file__).resolve().parents[1] / "devbench" / "study" / "datasets.py"
    assert source.is_file(), "Gated cohort preparation is not implemented"
    return importlib.import_module("devbench.study.datasets")


def plan_with_seeds(seeds=(None,), *, num_genes=2000):
    recipe = DatasetRecipe(recipe_id="recipe", scenario="balanced_temporal", num_genes=num_genes)
    return StudyPlan(study_id="preparation-contract", blinding_nonce="a" * 64,
                     scenario_families=("balanced_temporal",), cohorts_per_family=len(seeds), repetitions=1,
                     dataset_recipes=(recipe,), cohorts=tuple(
                         CohortPlan(cohort_id=f"cohort-{index}", scenario="balanced_temporal",
                                    recipe_id="recipe", generation_seed=seed)
                         for index, seed in enumerate(seeds)))


@pytest.mark.parametrize("permission", [None, False, 1, "true"])
def test_preparation_default_denial_precedes_argument_path_or_data_access(monkeypatch, permission):
    module = api()
    original_import = builtins.__import__

    def forbidden(*args, **kwargs):
        pytest.fail("Denied preparation attempted filesystem or data access")

    def guard(name, *args, **kwargs):
        if name.split(".")[0] in {"numpy", "pandas", "zarr", "devharness_core"}:
            forbidden()
        return original_import(name, *args, **kwargs)

    options = {} if permission is None else {"allow_data_execution": permission}
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "__import__", guard)
        blocked.setattr(builtins, "open", forbidden)
        for name in ("resolve", "open", "stat", "mkdir", "exists", "is_symlink"):
            blocked.setattr(Path, name, forbidden)
        with pytest.raises(PermissionError, match="allow_data_execution=True"):
            module.prepare_study_data(object(), object(), **options)


def test_all_missing_seeds_are_rejected_before_any_path_access(monkeypatch):
    module = api()
    plan = plan_with_seeds((None, 17, None))

    def forbidden(*args, **kwargs):
        pytest.fail("Unassigned generation seeds reached filesystem access")

    with monkeypatch.context() as blocked:
        for name in ("resolve", "open", "stat", "mkdir", "exists", "is_symlink"):
            blocked.setattr(Path, name, forbidden)
        with pytest.raises(ValueError, match="cohort-0.*cohort-2"):
            module.prepare_study_data(plan, object(), allow_data_execution=True)


def test_pure_preparation_validation_freezes_configuration_without_execution():
    module = api()
    plan = plan_with_seeds((17, 18))
    checked = module.validate_data_preparation(plan)
    assert checked.sha256() == plan.sha256()
    assert checked is not plan
    assert [cohort.generation_seed for cohort in checked.cohorts] == [17, 18]


@pytest.mark.parametrize("destination,error", [
    ("relative-output", ValueError),
    ("/private/tmp/devbench-denied-preparation", ValueError),
    (str(Path(__file__).resolve().parent), FileExistsError),
])
def test_preparation_rejects_unsafe_or_existing_storage_before_output(monkeypatch, destination, error):
    module = api()

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid storage reached data generation or output")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(module, "_generator_implementation_sha256", forbidden)
    monkeypatch.setattr(module.data, "prepare_dataset", forbidden)
    with pytest.raises(error):
        module.prepare_study_data(plan_with_seeds((17,)), Path(destination), allow_data_execution=True)


def test_preparation_rejects_public_plans_without_verified_import_path():
    module = api()
    plan = plan_with_seeds()
    recipe = DatasetRecipe(recipe_id="recipe", scenario="balanced_temporal", source_kind="public_processed",
        generator_family=None, public_accession="E-MTAB-6798", measurement_type="continuous",
        biological_unit_policy="require_verified_biological_unit_identifiers", latent_truth_scope="none", status="not_acquired")
    public = StudyPlan(**{**plan.model_dump(), "dataset_recipes": (recipe,)})
    with pytest.raises(ValueError, match="public.*verified"):
        module.validate_data_preparation(public)


def test_independence_identity_depends_on_generator_and_seed_not_cohort_name():
    module = api()
    first = module.synthetic_independence_id("a" * 64, 17)
    assert first == module.synthetic_independence_id("a" * 64, 17)
    assert first != module.synthetic_independence_id("a" * 64, 18)
    assert first != module.synthetic_independence_id("b" * 64, 17)
    assert len(first) <= 160


@pytest.mark.parametrize("seed", [None, True, -1, "17", 2**63])
def test_independence_identity_rejects_unassigned_or_invalid_seeds(seed):
    with pytest.raises(ValueError):
        api().synthetic_independence_id("a" * 64, seed)


def test_manifest_contract_keeps_executor_mapping_separate_from_private_receipts():
    schema = api().CoordinatorManifest.model_json_schema()
    fields = schema["$defs"]["PreparedCohortRecord"]["properties"]
    assert set(fields) == {"root", "input_sha256", "truth_sha256", "recipe_id", "generation_seed",
                           "independence_id", "unit_provenance_verified"}
    assert {"plan_identity", "cohorts", "receipts"} <= set(schema["required"])
    assert schema["additionalProperties"] is False


def input_manifest():
    files = {name: {"sha256": "a" * 64, "bytes": 1} for name in (
        "inputs/counts.zarr/.zgroup", "inputs/metadata.csv", "inputs/task_inputs.json")}
    return {"record_format": "devbench.study.input_manifest/1", "files": files,
            "input_sha256": content_sha256(files), "hash_scope": "input-relative-paths-bytes-and-sha256",
            "measurement_type": "raw_counts", "reference_batch": "batch1", "library_factor": 1.0,
            "batch_reference_policy": "first_observation", "biological_unit_reference": "population_mean"}


@pytest.mark.parametrize("path", ["../private/truth.json", "/private/truth.json", "inputs/../private/truth.json",
                                 "inputs//bad", "inputs/./bad", "inputs\\bad", "other/file"])
def test_prepared_manifest_rejects_escape_and_noncanonical_paths_without_reads(path):
    manifest = input_manifest()
    manifest["files"][path] = {"sha256": "b" * 64, "bytes": 1}
    manifest["input_sha256"] = content_sha256(manifest["files"])
    with pytest.raises(ValueError, match="paths"):
        api().PreparedInputManifest.model_validate(manifest)


def test_prepared_manifest_bounds_bytes_and_requires_matching_receipt_identity():
    module = api()
    assert module.PreparedInputManifest.model_validate(input_manifest()).measurement_type == "raw_counts"
    manifest = input_manifest()
    manifest["input_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="identity"):
        module.PreparedInputManifest.model_validate(manifest)
    manifest = input_manifest()
    for record in manifest["files"].values():
        record["bytes"] = 100_000_000
    manifest["input_sha256"] = content_sha256(manifest["files"])
    with pytest.raises(ValueError, match="byte budget"):
        module.PreparedInputManifest.model_validate(manifest)


def test_prepared_unit_verification_is_a_strict_boolean_claim():
    record = {"root": "/coordinator/cohort", "input_sha256": "a" * 64, "truth_sha256": "b" * 64,
              "recipe_id": "recipe", "generation_seed": 17, "independence_id": "synthetic-test",
              "unit_provenance_verified": 1}
    with pytest.raises(ValueError):
        api().PreparedCohortRecord.model_validate(record)


def public_recipe():
    return DatasetRecipe(recipe_id="public-recipe", scenario="balanced_temporal", source_kind="public_processed",
        generator_family=None, public_accession="E-MTAB-6798", measurement_type="continuous",
        biological_unit_policy="require_verified_biological_unit_identifiers", latent_truth_scope="none", status="not_acquired")


def public_provenance():
    return {
        "record_format": "devbench.study.public-import-provenance/1", "retained_input_sha256": "a" * 64,
        "selection_description": "Curator-prepared bounded subset; no biological identity adjudication",
        "public_manifest": {"record_format": "devbench.study.public_manifest/1", "source_id": "E-MTAB-6798",
            "classification": "public_research", "primary_url": "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6798",
            "input_sha256": "a" * 64, "measurement_type": "continuous", "expression_units": "RPKM",
            "biological_unit_scope": "unverified", "reference_scope": "observational_expression",
            "complete_causal_truth": False, "complete_pseudotime_truth": False},
    }


@pytest.mark.parametrize("permission", [None, False, 1, "true"])
def test_public_import_default_denial_precedes_provenance_and_paths(monkeypatch, permission):
    module = api()
    assert hasattr(module, "import_public_subset"), "Public subset import is not implemented"

    def forbidden(*args, **kwargs):
        pytest.fail("Denied import attempted filesystem access")

    options = {} if permission is None else {"allow_data_execution": permission}
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "open", forbidden)
        for name in ("resolve", "open", "stat", "mkdir", "exists", "is_symlink"):
            blocked.setattr(Path, name, forbidden)
        with pytest.raises(PermissionError, match="allow_data_execution=True"):
            module.import_public_subset(object(), object(), object(), object(), object(), **options)


def test_public_import_configuration_keeps_declared_and_verified_provenance_separate(monkeypatch):
    module = api()
    assert hasattr(module, "validate_public_import"), "Public import contract is not implemented"

    def forbidden(*args, **kwargs):
        pytest.fail("Public import configuration accessed files")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    provenance = module.validate_public_import(public_recipe(), public_provenance())
    assert provenance.public_manifest.input_hash_verified is False
    assert provenance.public_manifest.biological_units_verified is False


@pytest.mark.parametrize("change", ["missing_hash", "mismatched_hash", "reference_hash_missing", "reference_scope_missing"])
def test_public_import_requires_retained_matching_input_and_reference_hashes(change):
    module = api()
    assert hasattr(module, "validate_public_import"), "Public import contract is not implemented"
    provenance = public_provenance()
    if change == "missing_hash":
        del provenance["retained_input_sha256"]
    elif change == "mismatched_hash":
        provenance["retained_input_sha256"] = "b" * 64
    elif change == "reference_hash_missing":
        provenance["curator_reference_scope"] = "observational_expression"
    else:
        provenance["retained_reference_sha256"] = "b" * 64
    with pytest.raises(ValueError):
        module.validate_public_import(public_recipe(), provenance)


def test_public_observation_truth_has_no_latent_or_adjudicated_diagnostic_truth():
    module = api()
    assert hasattr(module, "public_observation_truth"), "Public observation truth contract is not implemented"
    truth = module.public_observation_truth(public_recipe(), ["public-gene"], "c" * 64,
                                             module.validate_public_import(public_recipe(), public_provenance()))
    assert truth["source_kind"] == "public_processed"
    assert truth["latent_truth_scope"] == "none"
    assert truth["diagnostic_truth_adjudicated"] is False
    assert truth["population_identifiability"]["identifiable"] is None
    assert truth["population_inference_permitted"] is False
    assert truth["genes"] == {}
    assert truth["available_reference_metrics"] == []


def test_limited_public_reference_schema_excludes_complete_causal_and_pseudotime_truth():
    module = api()
    assert hasattr(module, "LimitedPublicReference"), "Limited public reference contract is not implemented"
    base = {"record_format": "devbench.study.public-reference/1", "reference_scope": "observational_expression",
            "available_reference_metrics": [], "genes": {}}
    for field in ("complete_causal_truth", "complete_pseudotime_truth", "diagnostic_truth_adjudicated"):
        with pytest.raises(ValueError):
            module.LimitedPublicReference(**{**base, field: True})


def test_public_subset_manifest_rejects_sidecars_that_could_expose_private_references():
    manifest = input_manifest()
    manifest.update(measurement_type="continuous", library_factor=None, reference_batch=None,
                    batch_reference_policy="observed_batch_mix", biological_unit_reference="observed_subset")
    manifest["files"]["inputs/reference.json"] = {"sha256": "b" * 64, "bytes": 1}
    manifest["input_sha256"] = content_sha256(manifest["files"])
    with pytest.raises(ValueError, match="sidecar"):
        api().PublicSubsetManifest.model_validate(manifest)


def test_public_prepared_mapping_keeps_unknown_unit_provenance_and_no_seed():
    module = api()
    record = module.PreparedPublicCohortRecord(root="/coordinator/public", input_sha256="a" * 64,
        truth_sha256="b" * 64, recipe_id="public", independence_id="public-" + "a" * 64)
    assert record.generation_seed is None
    assert record.unit_provenance_verified is False
    with pytest.raises(ValueError):
        module.PreparedPublicCohortRecord(**{**record.model_dump(), "unit_provenance_verified": True})


@pytest.mark.parametrize("sidecar", ["inputs/counts.zarr/reference.json", "inputs/counts.zarr/private/truth.json",
                                    "inputs/counts.zarr/expression/truth.json"])
def test_public_manifest_rejects_arbitrary_descendants_inside_zarr(sidecar):
    manifest = input_manifest()
    manifest.update(measurement_type="continuous", library_factor=None, reference_batch=None,
                    batch_reference_policy="observed_batch_mix", biological_unit_reference="observed_subset")
    manifest["files"][sidecar] = {"sha256": "b" * 64, "bytes": 1}
    manifest["input_sha256"] = content_sha256(manifest["files"])
    with pytest.raises(ValueError, match="sidecar"):
        api().PublicSubsetManifest.model_validate(manifest)


def test_preparation_and_public_import_reject_gene_universes_above_executor_cap():
    with pytest.raises(ValueError, match="5000"):
        api().validate_data_preparation(plan_with_seeds((17,), num_genes=5001))
    recipe = DatasetRecipe(**{**public_recipe().model_dump(), "num_genes": 5001})
    with pytest.raises(ValueError, match="5000"):
        api().validate_public_import(recipe, public_provenance())


def test_public_curve_reference_describes_the_actual_observed_batch_mix():
    reference = api().PublicObservationCurveReference(library_factor=None)
    assert reference.model_dump() == {"reference_batch": None, "library_factor": None,
        "batch_reference_policy": "observed_batch_mix", "biological_unit_reference": "observed_subset"}
    with pytest.raises(ValueError):
        api().PublicObservationCurveReference(reference_batch="batch1", library_factor=None,
                                              batch_reference_policy="first_observation")


@pytest.mark.parametrize("kind,units,nonnegative", [("raw_counts", "read_counts", True),
    ("continuous", "RPKM", True), ("continuous", "TPM", True), ("continuous", "normalized_expression", False)])
def test_expression_domain_policy_uses_declared_units(kind, units, nonnegative):
    assert api().requires_nonnegative_expression(kind, units) is nonnegative


@pytest.mark.parametrize("content", [b"key,key\na,b\n", b"key,\na,b\n", b"key,value\na,b,c\n",
    b"key,value\na\n", b"key,value\n,b\n", b"key,value\na,b\na,c\n",
    b'key,value\nalpha,"unterminated', b'key,"unterminated\n'])
def test_generic_csv_text_rejects_ambiguous_headers_ragged_rows_and_duplicate_keys(content):
    with pytest.raises(ValueError):
        api().parse_bounded_csv_records(content, required_columns={"key", "value"}, identifier_column="key", max_rows=10)


def test_generic_csv_text_retains_an_unambiguous_exact_identifier_order():
    rows = api().parse_bounded_csv_records(b"key,value\nalpha,beta\ngamma,delta\n",
        required_columns={"key", "value"}, identifier_column="key", max_rows=10)
    assert [row["key"] for row in rows] == ["alpha", "gamma"]


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/storage fixture requires opt-in")
def test_authorized_tiny_preparation_roundtrip_deferred():
    import hashlib
    import json
    import tempfile

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix=".study-data-test-", dir=root) as directory:
        result = api().prepare_study_data(plan_with_seeds((17,), num_genes=10),
                                         Path(directory) / "prepared", allow_data_execution=True)
        manifest = json.loads(Path(result["coordinator_manifest_path"]).read_text())
        record = manifest["cohorts"]["cohort-0"]
        bundle = Path(record["root"])
        assert record["truth_sha256"] == hashlib.sha256((bundle / "private" / "truth.json").read_bytes()).hexdigest()
        assert record["unit_provenance_verified"] is True
        assert manifest["receipts"]["cohort-0"]["input_bytes"] > 0
        assert (bundle / "inputs" / "counts.zarr").is_dir()
