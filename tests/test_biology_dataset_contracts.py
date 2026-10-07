"""Pure contracts and generic byte plumbing; dataset creation stays skipped."""

from __future__ import annotations

import builtins
import hashlib
import importlib
import json

import pytest


def datasets():
    return importlib.import_module("devbench.study.biology_datasets")


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true", [], {}])
def test_preparation_denies_before_touching_arguments(permission):
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("argument inspected before authorization")

    with pytest.raises(PermissionError, match="allow_data_execution=True"):
        datasets().prepare_biology_dataset(Unreadable(), Unreadable(),
                                           plan_identity=Unreadable(), cohort_id=Unreadable(),
                                           allow_data_execution=permission)


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true"])
def test_verification_denies_before_reading_receipt_or_paths(permission):
    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("argument inspected before authorization")

    with pytest.raises(PermissionError, match="allow_data_execution=True"):
        datasets().verify_prepared_biology_bundle(Unreadable(), Unreadable(), Unreadable(),
                                                  allow_data_execution=permission)


def test_import_has_no_scientific_runtime(monkeypatch):
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "zarr", "scipy", "h5py"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)
    importlib.reload(datasets())


def test_public_member_contract_excludes_private_and_undeclared_sidecars():
    allowed = datasets().is_biology_public_member
    assert allowed("inputs/counts.zarr/expression/0.0")
    assert allowed("inputs/task_inputs.json")
    assert allowed("inputs/public_annotations.json")
    assert not allowed("private/truth.json")
    assert not allowed("inputs/../private/truth.json")
    assert not allowed("inputs/counts.zarr/source.py")
    assert not allowed("inputs/freeform.txt")


def test_exact_generator_registry_is_configuration_only():
    from devbench.study.biology import load_biology_catalog
    catalog = load_biology_catalog()
    registry = datasets().biology_generator_registry(catalog)
    implemented = {case for registration in registry.registrations for case in registration.case_ids}
    configured = {case.case_id for template in catalog.templates for case in template.cases}
    assert implemented == configured
    assert all(registration.kind == "generator" for registration in registry.registrations)


def test_generic_byte_receipts_are_bounded_and_detect_changes(tmp_path):
    path = tmp_path / "sentinel.bin"
    path.write_bytes(b"abc")
    assert datasets()._hash_file(path, 3) == (hashlib.sha256(b"abc").hexdigest(), 3)
    with pytest.raises(ValueError, match="bound"):
        datasets()._hash_file(path, 2)
    path.write_bytes(b"abd")
    assert datasets()._hash_file(path, 3)[0] != hashlib.sha256(b"abc").hexdigest()


def test_json_byte_writer_preserves_null_and_rejects_nonfinite_without_substitution(tmp_path):
    path = tmp_path / "generic.json"
    datasets()._write_json(path, {"nullable": None, "value": .25}, 1000)
    assert json.loads(path.read_text()) == {"nullable": None, "value": .25}
    with pytest.raises(ValueError, match="finite"):
        datasets()._write_json(tmp_path / "invalid.json", {"value": float("nan")}, 1000)


def test_json_byte_writer_retains_bounded_partial_output_on_failure(tmp_path):
    path = tmp_path / "partial.json"
    with pytest.raises(ValueError, match="bound"):
        datasets()._write_json(path, {"text": "x" * 500}, 40)
    assert path.exists() and path.stat().st_size <= 40


def test_strict_json_parser_rejects_repeated_and_nonfinite_keys():
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}'):
        with pytest.raises(ValueError):
            datasets()._decode_json(raw)


def test_public_transport_limits_are_the_same_as_the_tool_consumer():
    from devbench.study import biology_input_limits as limits
    assert datasets().MAX_METADATA_BYTES == limits.PUBLIC_MAX_METADATA_BYTES
    assert datasets().MAX_MATRIX_BYTES == limits.PUBLIC_MAX_MATRIX_BYTES
    assert datasets().MAX_INPUT_BYTES == limits.PUBLIC_MAX_AGGREGATE_BYTES
    assert datasets().MAX_TRUTH_BYTES > limits.PUBLIC_MAX_ANNOTATION_BYTES


def test_public_streaming_json_matches_shared_node_budget_and_private_key_policy():
    from devbench.study.biology_input_limits import validate_public_structure
    payload = {"first": None, "second": False}
    validate_public_structure(payload, budget=[3])
    assert json.loads("".join(datasets()._json_parts(payload, public=True, budget=[3]))) == payload
    with pytest.raises(ValueError, match="bound"):
        "".join(datasets()._json_parts(payload, public=True, budget=[2]))
    with pytest.raises(ValueError, match="private"):
        "".join(datasets()._json_parts({"generation_seed": None}, public=True))


def test_public_json_string_limit_does_not_shrink_the_private_truth_contract(tmp_path):
    from devbench.study.biology_input_limits import PUBLIC_MAX_JSON_STRING_CHARS
    payload = {"text": "x" * (PUBLIC_MAX_JSON_STRING_CHARS + 1)}
    with pytest.raises(ValueError, match="string.*bound"):
        datasets()._write_json(tmp_path / "public.json", payload, 100_000, public=True)
    datasets()._write_json(tmp_path / "private.json", payload, 100_000)


def test_public_transport_configuration_rejects_predictably_oversized_cell_metadata():
    from devbench.study.biology import BiologyRecipe, GenerationParameters, load_biology_catalog
    recipe = BiologyRecipe(recipe_id="transport-config", template_id="T14", case_id="T14-state-only",
                           role="calibration", mechanism_holdout_group="transport-config", generation_seed=1,
                           parameters=GenerationParameters(num_genes=64, independent_units_per_group=3,
                               condition_labels=("A", "B"), cells_per_specimen=6000, cell_type_count=3))
    with pytest.raises(ValueError, match="metadata.*bound"):
        datasets().validate_biology_public_transport(recipe, load_biology_catalog())


def test_unknown_population_unit_status_remains_admissible_without_data():
    from devbench.study.biology import BiologyRecipe, GenerationParameters, load_biology_catalog
    recipe = BiologyRecipe(recipe_id="unknown-unit-config", template_id="T07", case_id="T07-unknown-identity",
                           role="calibration", mechanism_holdout_group="unknown-unit-config", generation_seed=1,
                           parameters=GenerationParameters(num_genes=120, independent_units_per_group=4,
                               condition_labels=("A", "B"), time_grid=tuple(range(13)), time_units="hours"))
    admission = datasets().validate_biology_public_transport(recipe, load_biology_catalog())
    assert admission["unit_identity_status"] == "intentionally_unknown"


@pytest.mark.parametrize("identity", ["human-plan-name", "A" * 64, "a" * 63, b"a" * 64, None])
def test_plan_identity_contract_is_strict_lowercase_sha256(identity):
    with pytest.raises(ValueError, match="SHA-256"):
        datasets()._identity(identity, "cohort-config")


def test_recipe_question_contracts_never_contain_case_or_template_labels():
    from devbench.study.biology import BiologyRecipe, GenerationParameters, load_biology_catalog
    catalog = load_biology_catalog()
    for template in catalog.templates:
        # This constructs selection configuration only; no generator is admitted
        # or invoked, and no biological metadata records are constructed.
        recipe = BiologyRecipe(recipe_id="question-contract", template_id=template.template_id,
                               case_id=template.cases[0].case_id, role="calibration",
                               mechanism_holdout_group="question-contract", generation_seed=1,
                               parameters=GenerationParameters(num_genes=64, independent_units_per_group=3,
                                                               condition_labels=("A", "B")))
        datasets()._public_value(datasets()._questions(catalog, recipe), recipe)


def _question_recipe(template_id):
    from devbench.study.biology import BiologyRecipe, GenerationParameters, load_biology_catalog
    catalog = load_biology_catalog()
    template = catalog.template(template_id)
    recipe = BiologyRecipe(recipe_id="question-contract", template_id=template_id,
                           case_id=template.cases[0].case_id, role="calibration",
                           mechanism_holdout_group="question-contract", generation_seed=1,
                           parameters=GenerationParameters(num_genes=64, independent_units_per_group=3,
                                                           condition_labels=("A", "B")))
    return catalog, recipe


def test_questions_include_the_exact_typed_projection_contract():
    catalog, recipe = _question_recipe("T15")
    targets = {target.target_id: target for target in catalog.template("T15").targets}
    questions = datasets()._questions(catalog, recipe)
    for question in questions:
        projection = targets[question["target_id"]].shared_process_projection
        if projection is not None:
            assert question["projection_contract"] == projection.model_dump(mode="json")
        else:
            assert "projection_contract" not in question


def test_public_reference_override_updates_the_canonical_question_fields():
    catalog, recipe = _question_recipe("T14")
    override = {"estimand": "Descriptive captured composition", "contrast": "Condition contrast within captured observations",
                "units": "captured fraction", "reference_population": "observed captured subset",
                "prediction_reference": "observed support only"}
    context = {"target_populations": {"captured_abundance": "captured observations by condition"},
               "target_references": {"captured_abundance": override}}
    questions = datasets()._questions(catalog, recipe, context)
    question = next(item for item in questions if item["target_id"] == "captured_abundance")
    assert all(question[field] == value for field, value in override.items())
    assert "expected_scope" not in question and "expected_refusal" not in question


@pytest.mark.parametrize("override", [
    {"captured_abundance": {"expected_refusal": True}},
    {"private_unknown_target": {"estimand": "unsupported reference"}},
    {"captured_abundance": {"reference_population": "x" * 3001}},
])
def test_reference_overrides_reject_private_unknown_and_unbounded_fields(override):
    catalog, recipe = _question_recipe("T14")
    with pytest.raises(ValueError):
        datasets()._questions(catalog, recipe, {"target_references": override})


def test_latest_cell_context_keys_are_explicitly_allowed_without_private_scopes():
    fields = datasets()._CELL_CONTEXT
    assert {"target_references", "target_populations", "enrichment_query_gene_ids",
            "enrichment_background_gene_ids", "enrichment_query_selection",
            "shared_process_projection", "projection_interpretation",
            "multiplet_identity_scope", "artifact_detection_calibration",
            "captured_abundance_reference_scope"} <= fields
    assert not {"expected_scope", "expected_refusal", "case_id", "template_id", "private_truth"} & fields


def _projection_schema_configuration():
    catalog, recipe = _question_recipe("T15")
    target = next(target for target in catalog.template("T15").targets if target.shared_process_projection is not None)
    projection = target.shared_process_projection.model_dump(mode="json")
    universe = tuple(f"feature_{index}" for index in range(64))
    projection.update(excluded_feature_ids=universe[:1], retained_feature_universe=universe[1:])
    context = {"feature_universe": universe, "shared_process_projection": projection}
    annotations = {"cell_annotation_reference": {"markers": ({"label": "declared_type", "gene_ids": universe[:1]},)}}
    return catalog, recipe, context, annotations


def test_projection_configuration_validation_is_pure_and_uses_public_marker_union(monkeypatch):
    catalog, recipe, context, annotations = _projection_schema_configuration()
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy", "zarr"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked)
    datasets()._cell_extension_contract(context, annotations, recipe, catalog)


@pytest.mark.parametrize("field,value", [
    ("private_truth", {}), ("full_counts_preserved", 1),
    ("excluded_feature_ids", ("unmeasured_feature",)),
    ("retained_feature_universe", ("feature_0", "feature_1")),
])
def test_projection_rejects_private_fields_wrong_typed_flags_and_invalid_partitions(field, value):
    catalog, recipe, context, annotations = _projection_schema_configuration()
    context["shared_process_projection"][field] = value
    with pytest.raises(ValueError):
        datasets()._cell_extension_contract(context, annotations, recipe, catalog)


def _enrichment_schema_configuration():
    catalog, recipe = _question_recipe("T19")
    universe = tuple(f"feature_{index}" for index in range(64))
    selection = {key: "declared public selection description" for key in datasets()._ENRICHMENT_TEXT}
    selection.update({key: False for key in datasets()._ENRICHMENT_FLAGS})
    selection.update(query_count=16, nonempty_library_count=3, positive_variance_gene_count=64,
                     working_bytes_upper_bound=1024)
    context = {"feature_universe": universe, "enrichment_query_gene_ids": universe[:16],
               "enrichment_background_gene_ids": universe, "enrichment_query_selection": selection}
    return catalog, recipe, context


def test_enrichment_schema_preserves_full_background_and_explicit_selection_limits():
    catalog, recipe, context = _enrichment_schema_configuration()
    datasets()._cell_extension_contract(context, {}, recipe, catalog)
    context["enrichment_background_gene_ids"] = context["enrichment_background_gene_ids"][:-1]
    with pytest.raises(ValueError, match="complete measured"):
        datasets()._cell_extension_contract(context, {}, recipe, catalog)


def test_enrichment_selection_rejects_private_scope_and_unbounded_metadata():
    catalog, recipe, context = _enrichment_schema_configuration()
    context["enrichment_query_selection"]["expected_scope"] = "private"
    with pytest.raises(ValueError, match="private"):
        datasets()._cell_extension_contract(context, {}, recipe, catalog)
    del context["enrichment_query_selection"]["expected_scope"]
    context["enrichment_query_selection"]["ranking"] = "x" * 3001
    with pytest.raises(ValueError, match="bounded"):
        datasets()._cell_extension_contract(context, {}, recipe, catalog)


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="User hold: generating or writing biological datasets/stores is not authorized")
class TestAuthorizedDatasetIntegration:
    def test_prepared_store_binds_all_public_inputs_and_verifies_without_truth_return(self):
        from devbench.study.biology import BiologyRecipe, GenerationParameters
        from pathlib import Path
        import tempfile
        parent = Path(tempfile.mkdtemp(prefix="biology-authorized-", dir=Path(__file__).resolve().parents[1]))
        recipe = BiologyRecipe(recipe_id="future-bulk", template_id="T01", case_id="T01-dense",
                               role="calibration", generation_seed=41, mechanism_holdout_group="future-bulk",
                               parameters=GenerationParameters(num_genes=120, independent_units_per_group=4,
                                   condition_labels=("A", "B"), time_grid=tuple(range(13)), time_units="hours"))
        prepared = datasets().prepare_biology_dataset(recipe, parent / "cohort", plan_identity="a" * 64,
                                                      cohort_id="future-cohort", allow_data_execution=True)
        verified = datasets().verify_prepared_biology_bundle(prepared, recipe, "a" * 64, allow_data_execution=True)
        assert verified.input_sha256 == prepared.input_sha256
        assert not hasattr(verified, "private_truth")
        assert prepared.input_manifest_sha256 != prepared.input_sha256
        assert (Path(prepared.root) / "inputs" / "counts.zarr").is_dir()

    def test_single_cell_sample_alias_preserves_cell_and_donor_hierarchy(self):
        from devbench.study.biology import BiologyRecipe, GenerationParameters
        from pathlib import Path
        import csv
        import tempfile
        parent = Path(tempfile.mkdtemp(prefix="biology-authorized-", dir=Path(__file__).resolve().parents[1]))
        recipe = BiologyRecipe(recipe_id="future-cell", template_id="T14", case_id="T14-state-only",
                               role="calibration", generation_seed=42, mechanism_holdout_group="future-cell",
                               parameters=GenerationParameters(num_genes=120, independent_units_per_group=3,
                                   condition_labels=("A", "B"), cells_per_specimen=24, cell_type_count=3))
        prepared = datasets().prepare_biology_dataset(recipe, parent / "cohort", plan_identity="a" * 64,
                                                      cohort_id="future-cell-cohort", allow_data_execution=True)
        with (Path(prepared.root) / "inputs/metadata.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 144
        assert all(row["sample_id"] == row["cell_id"] for row in rows)
        assert len({row["donor_id"] for row in rows}) == 6
        assert all(row["specimen_id"] and row["library_id"] for row in rows)
        datasets().verify_prepared_biology_bundle(prepared, recipe, "a" * 64, allow_data_execution=True)
