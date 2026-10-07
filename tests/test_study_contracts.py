"""Pure study configuration tests; no datasets, analysis or participants are run."""

from collections import Counter, defaultdict
import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError


def api():
    source = Path(__file__).resolve().parents[1] / "devbench" / "study" / "contracts.py"
    assert source.is_file(), "Study contracts are not implemented"
    return importlib.import_module("devbench.study")


def changed(model, **updates):
    return type(model)(**{**model.model_dump(), **updates})


@pytest.mark.parametrize("stage, count", [("smoke", 6), ("diagnostic", 36), ("exploratory", 216)])
def test_stage_counts_include_every_attempt_without_execution(stage, count):
    module = api()
    plan = module.default_study_plan(stage=stage)
    attempts = module.schedule_attempts(plan)
    assert len(attempts) == count
    assert plan.status == "not_run"
    assert plan.calibration_status == "uncalibrated"
    assert plan.conditions == ("baseline", "optional", "enforced")
    assert plan.primary_comparison == ("enforced", "baseline")
    assert plan.scripted_controls == ()
    assert module.schedule_controls(plan) == []
    assert all(recipe.status == "planned" for recipe in plan.dataset_recipes)
    assert all(cohort.generation_seed is None for cohort in plan.cohorts)
    assert len({attempt.attempt_id for attempt in attempts}) == count
    assert [attempt.execution_order for attempt in attempts] == list(range(1, count + 1))


def test_triplets_share_task_identity_and_balance_condition_positions():
    module = api()
    plan = module.default_study_plan()
    attempts = module.schedule_attempts(plan)
    assert attempts == module.schedule_attempts(plan)
    groups = defaultdict(list)
    positions = Counter()
    for attempt in attempts:
        groups[(attempt.cohort_id, attempt.repetition)].append(attempt)
    for triplet in groups.values():
        assert {attempt.condition for attempt in triplet} == set(plan.conditions)
        assert len({attempt.task_identity for attempt in triplet}) == 1
        for position, attempt in enumerate(triplet):
            positions[(position, attempt.condition)] += 1
    assert len(set(positions.values())) == 1
    assert len({attempt.plan_identity for attempt in attempts}) == 1
    for cohort_id in {attempt.cohort_id for attempt in attempts}:
        assert len({attempt.task_identity for attempt in attempts if attempt.cohort_id == cohort_id}) == 1


def test_schedule_seed_changes_order_but_preserves_scientific_tasks():
    module = api()
    plan = module.default_study_plan()
    first = module.schedule_attempts(plan)
    second = module.schedule_attempts(changed(plan, seed=plan.seed + 1))
    assert {a.cohort_id: a.task_identity for a in first} == {a.cohort_id: a.task_identity for a in second}
    assert [(a.cohort_id, a.condition) for a in first] != [(a.cohort_id, a.condition) for a in second]


@pytest.mark.parametrize("field,value", [
    ("seed", True), ("seed", -1), ("seed", "3"), ("cohorts_per_family", 0),
    ("repetitions", 0), ("conditions", ("baseline", "baseline", "enforced")),
    ("scenario_families", ("unknown_scenario",)),
    ("scenario_families", ("balanced_temporal", "balanced_temporal")),
    ("primary_comparison", ("optional", "baseline")), ("study_id", "../private"),
])
def test_plan_rejects_invalid_configuration(field, value):
    with pytest.raises(ValidationError):
        changed(api().default_study_plan(), **{field: value})


def test_plan_rejects_duplicate_cohorts_and_invalid_recipe_links():
    plan = api().default_study_plan()
    with pytest.raises(ValidationError, match="cohort"):
        changed(plan, cohorts=(plan.cohorts[0],) * len(plan.cohorts))
    invalid = changed(plan.cohorts[0], recipe_id="missing")
    with pytest.raises(ValidationError, match="recipe"):
        changed(plan, cohorts=(invalid, *plan.cohorts[1:]))
    training = next(recipe for recipe in plan.dataset_recipes if recipe.split == "training")
    invalid = changed(plan.cohorts[0], recipe_id=training.recipe_id)
    with pytest.raises(ValidationError, match="evaluation"):
        changed(plan, cohorts=(invalid, *plan.cohorts[1:]))


def test_requested_model_is_explicit_configuration_not_observed_identity():
    module = api()
    plan = module.default_study_plan(stage="smoke")
    assert plan.requested_model is None
    assert plan.reasoning_effort is None
    configured = changed(plan, requested_model="provider/model-v1", reasoning_effort="high")
    assert configured.requested_model == "provider/model-v1"
    assert configured.reasoning_effort == "high"
    assert configured.status == "not_run"
    assert "observed_model" not in configured.model_dump()
    assert module.StudyPlan.model_validate_json(configured.model_dump_json()) == configured


@pytest.mark.parametrize("field,value", [
    ("requested_model", ""), ("requested_model", " "), ("requested_model", True),
    ("requested_model", "m" * 201), ("requested_model", "model\n--unsafe"),
    ("reasoning_effort", "unlimited"), ("reasoning_effort", True),
])
def test_requested_runtime_settings_reject_invalid_values(field, value):
    with pytest.raises(ValidationError):
        changed(api().default_study_plan(stage="smoke"), **{field: value})


@pytest.mark.parametrize("generation_seed", [0, 47])
def test_assigned_generation_seeds_must_be_unique_without_assigning_defaults(generation_seed):
    plan = api().default_study_plan(stage="smoke")
    assert [c.generation_seed for c in plan.cohorts] == [None, None]
    duplicate = tuple(changed(c, generation_seed=generation_seed) for c in plan.cohorts)
    with pytest.raises(ValidationError, match="generation seeds must be distinct"):
        changed(plan, cohorts=duplicate)
    distinct = tuple(changed(c, generation_seed=generation_seed + i) for i, c in enumerate(plan.cohorts))
    assert changed(plan, cohorts=distinct).cohorts == distinct


@pytest.mark.parametrize("stage,count", [("smoke", 6), ("diagnostic", 36), ("exploratory", 216)])
def test_registered_controls_use_separate_ids_and_do_not_consume_model_slots(stage, count):
    module = api()
    plan = module.default_study_plan(stage=stage)
    control = module.ScriptedControlPlan(control_id="control-1", cohort_id=plan.cohorts[0].cohort_id,
                                         procedure_id="prespecified-procedure-v1")
    plan = changed(plan, scripted_controls=(control,))
    controls = module.schedule_controls(plan)
    attempts = module.schedule_attempts(plan)
    assert len(attempts) == count
    assert len(controls) == 1
    assert controls[0]["attempt_id"] == module.control_attempt_id(plan, control)
    assert controls[0]["attempt_id"].startswith("control-attempt-")
    assert controls[0]["attempt_id"] not in {attempt.attempt_id for attempt in attempts}
    assert controls[0]["actor_type"] == "scripted_control"
    assert controls[0]["condition"] == "baseline"
    assert controls[0]["repetition"] == 1
    assert controls[0]["status"] == "scheduled_not_run"
    assert controls[0]["task_identity"] == module.task_identity(plan, plan.cohorts[0])
    restored = module.StudyPlan.model_validate_json(plan.model_dump_json())
    assert module.schedule_controls(restored) == controls
    different_nonce = changed(plan, blinding_nonce="33" * 32)
    assert module.control_attempt_id(different_nonce, control) != controls[0]["attempt_id"]


def test_controls_require_explicit_registration_and_known_cohorts():
    module = api()
    plan = module.default_study_plan(stage="smoke")
    control = module.ScriptedControlPlan(control_id="control-1", cohort_id=plan.cohorts[0].cohort_id,
                                         procedure_id="prespecified-procedure-v1")
    with pytest.raises(ValueError, match="registered"):
        module.control_attempt_id(plan, control)
    with pytest.raises(ValidationError, match="control"):
        changed(plan, scripted_controls=(control, control))
    with pytest.raises(ValidationError, match="cohort"):
        changed(plan, scripted_controls=(changed(control, cohort_id="unknown"),))
    with pytest.raises(ValidationError):
        changed(control, actor_type="model_participant")
    with pytest.raises(ValidationError):
        changed(control, repetition=0)
    with pytest.raises(ValidationError):
        changed(control, procedure_id="../script.py")


@pytest.mark.parametrize("field,value", [
    ("timeout_sec", 0), ("cpu_threads", True), ("max_memory_mb", -1),
    ("token_budget", 0), ("timeout_sec", float("inf")),
])
def test_budget_rejects_invalid_or_nonfinite_limits(field, value):
    with pytest.raises(ValidationError):
        changed(api().RunBudget(), **{field: value})


@pytest.mark.parametrize("field,value", [
    ("alpha", 0.0), ("alpha", float("nan")), ("maximum_fdp", 1.01),
    ("minimum_signed_recall", -0.1), ("minimum_shape_macro_f1", True),
    ("eligible_amplitude_log2", float("inf")), ("eligible_mean", -1.0),
    ("confidence_level", 1.0), ("minimum_independent_cohorts", 9),
])
def test_thresholds_are_finite_bounded_proposals(field, value):
    with pytest.raises(ValidationError):
        changed(api().MetricThresholds(), **{field: value})


def test_recipe_contract_validates_source_measurement_and_limits():
    module = api()
    recipe = module.default_study_plan().dataset_recipes[0]
    assert {r.generator_family for r in module.default_study_plan().dataset_recipes} == {
        "negative_binomial", "poisson_lognormal"}
    assert {r.split for r in module.default_study_plan().dataset_recipes} == {"training", "evaluation"}
    for updates in [
        {"source_kind": "private_clinical"}, {"measurement_type": "continuous"},
        {"source_sha256": "wrong"}, {"num_genes": True}, {"num_genes": 0},
        {"max_matrix_bytes": 100_000_001}, {"num_genes": 100_000, "max_matrix_bytes": 10_000_000},
        {"timepoint_labels": ("t1",) * 6}, {"generator_family": None},
    ]:
        with pytest.raises(ValidationError):
            changed(recipe, **updates)
    public = changed(recipe, source_kind="public_processed", generator_family=None,
                     public_accession="GSE12345", status="not_acquired", latent_truth_scope="none",
                     biological_unit_policy="require_verified_biological_unit_identifiers",
                     independent_unit_count=None)
    assert public.source_sha256 is None
    assert public.independent_unit_count is None
    with pytest.raises(ValidationError):
        changed(public, public_accession="/private/clinical.csv")


def test_technical_replicates_are_one_series_and_seventy_two_libraries():
    recipes = [r for r in api().default_study_plan().dataset_recipes
               if r.scenario == "technical_replicates"]
    assert len(recipes) == 4
    for recipe in recipes:
        assert recipe.biological_unit_policy == "single_biological_series"
        assert recipe.biological_units_per_cell == 1
        assert recipe.technical_replicates_per_unit == 4
        assert recipe.num_timepoints == 6
        assert recipe.num_batches == 3
        assert recipe.sample_count == 72
        assert recipe.independent_unit_count == 1
        assert recipe.model_dump()["independent_unit_count"] == 1
        minimum_bytes = 72 * recipe.num_genes * 8
        assert changed(recipe, max_matrix_bytes=minimum_bytes).sample_count == 72
        with pytest.raises(ValidationError, match="upper bound"):
            changed(recipe, max_matrix_bytes=minimum_bytes - 1)


def test_other_five_default_families_retain_seventy_two_independent_units():
    recipes = [r for r in api().default_study_plan().dataset_recipes
               if r.scenario != "technical_replicates"]
    assert len({r.scenario for r in recipes}) == 5
    for recipe in recipes:
        assert recipe.biological_unit_policy == "independent_biological_units"
        assert recipe.sample_count == 72
        assert recipe.independent_unit_count == 72


def test_complete_aliasing_counts_only_six_occupied_time_batch_cells():
    recipes = [r for r in api().default_study_plan().dataset_recipes
               if r.scenario == "complete_aliasing"]
    for recipe in recipes:
        assert recipe.layout == "time_aliased_batches"
        assert recipe.num_timepoints == recipe.num_batches == 6
        assert len(recipe.batch_labels) == 6
        assert recipe.biological_units_per_cell == 12
        assert recipe.occupied_cell_count == 6
        assert recipe.sample_count == 72
        assert recipe.independent_unit_count == 72
        assert recipe.model_dump()["layout"] == "time_aliased_batches"
        assert changed(recipe, max_matrix_bytes=72 * recipe.num_genes * 8).sample_count == 72
        with pytest.raises(ValidationError, match="same number"):
            changed(recipe, num_batches=3, batch_labels=("batch1", "batch2", "batch3"))
        with pytest.raises(ValidationError, match="layout"):
            changed(recipe, layout="crossed")


def test_crossed_and_single_series_layouts_keep_their_declared_occupied_cells():
    recipes = api().default_study_plan().dataset_recipes
    balanced = next(r for r in recipes if r.scenario == "balanced_temporal")
    single = next(r for r in recipes if r.scenario == "technical_replicates")
    assert balanced.layout == "crossed"
    assert single.layout == "single_series"
    assert balanced.occupied_cell_count == single.occupied_cell_count == 18
    assert balanced.sample_count == single.sample_count == 72
    assert balanced.independent_unit_count == 72
    assert single.independent_unit_count == 1


@pytest.mark.parametrize("updates", [
    {"independent_unit_count": 72}, {"independent_unit_count": True},
    {"biological_units_per_cell": 4},
    {"biological_unit_policy": "technical_replicates_nested_within_unit"},
])
def test_single_series_rejects_inflated_replication_claims(updates):
    recipe = next(r for r in api().default_study_plan().dataset_recipes
                  if r.scenario == "technical_replicates")
    with pytest.raises(ValidationError):
        changed(recipe, **updates)


def test_nested_technical_libraries_do_not_erase_multiple_independent_units():
    recipe = api().default_study_plan().dataset_recipes[0]
    nested = changed(recipe, biological_unit_policy="technical_replicates_nested_within_unit",
                     technical_replicates_per_unit=4)
    assert nested.scenario == "balanced_temporal"
    assert nested.sample_count == 288
    assert nested.independent_unit_count == 72
    assert "nonidentifiable" not in nested.model_dump_json()


def test_models_are_frozen_and_json_roundtrips_preserve_identity():
    module = api()
    plan = module.default_study_plan()
    with pytest.raises(ValidationError):
        plan.seed = 1
    restored = module.StudyPlan.model_validate_json(plan.model_dump_json())
    assert restored == plan
    assert module.schedule_attempts(restored) == module.schedule_attempts(plan)


def test_new_default_plans_get_fresh_private_blinding_metadata():
    module = api()
    first = module.default_study_plan(stage="smoke")
    second = module.default_study_plan(stage="smoke")
    assert first.blinding_nonce != second.blinding_nonce
    assert len(bytes.fromhex(first.blinding_nonce)) == 32
    assert first.sha256() != second.sha256()
    assert first.model_dump(exclude={"blinding_nonce"}) == second.model_dump(exclude={"blinding_nonce"})
    assert first.blinding_nonce not in repr(first)
    replay = module.StudyPlan.model_validate_json(first.model_dump_json())
    assert module.schedule_attempts(replay) == module.schedule_attempts(first)
    original_attempt = module.schedule_attempts(first)[0]
    replay_attempt = module.schedule_attempts(replay)[0]
    assert original_attempt.participant_projection(first) == replay_attempt.participant_projection(replay)


@pytest.mark.parametrize("nonce", [True, "short", "G" * 64, "A" * 64])
def test_blinding_nonce_requires_canonical_32_byte_hex(nonce):
    with pytest.raises(ValidationError):
        changed(api().default_study_plan(stage="smoke"), blinding_nonce=nonce)


def test_saved_plan_without_blinding_metadata_cannot_silently_create_new_ids():
    module = api()
    record = module.default_study_plan(stage="smoke").model_dump(mode="json")
    record.pop("blinding_nonce")
    with pytest.raises(ValidationError, match="blinding_nonce"):
        module.StudyPlan.model_validate_json(json.dumps(record))


def test_private_nonce_blinds_both_identifiers_even_for_enumerable_cohorts():
    module = api()
    base = module.default_study_plan(stage="smoke")
    first = changed(base, blinding_nonce="11" * 32)
    second = changed(base, blinding_nonce="22" * 32)
    first_attempt = module.schedule_attempts(first)[0]
    second_attempt = module.schedule_attempts(second)[0]
    assert first_attempt.cohort_id == second_attempt.cohort_id
    first_public = first_attempt.participant_projection(first)
    second_public = second_attempt.participant_projection(second)
    assert first_public["task_id"] != second_public["task_id"]
    assert first_public["cohort_id"] != second_public["cohort_id"]
    enumerated_unsalted_id = module.content_sha256(
        {"cohort_id": first_attempt.cohort_id, "study_id": first.study_id})
    assert first_public["cohort_id"] != enumerated_unsalted_id
    for plan, attempt, public in [(first, first_attempt, first_public), (second, second_attempt, second_public)]:
        encoded = json.dumps(public)
        assert plan.blinding_nonce not in encoded
        assert "blinding_nonce" not in encoded
        assert attempt.cohort_id not in encoded
        assert attempt.recipe_id not in encoded
        assert attempt.scenario not in encoded


def test_participant_projection_contains_only_shared_public_contract():
    module = api()
    plan = module.default_study_plan()
    attempt = module.schedule_attempts(plan)[0]
    projection = attempt.participant_projection(plan)
    assert set(projection) == {"record_format", "task_id", "cohort_id", "question_ref", "answer_schema_ref", "budget"}
    text = json.dumps(projection)
    for secret in ["negative_binomial", "poisson_lognormal", attempt.scenario, "generation_seed", "thresholds"]:
        assert secret not in text
    assert projection["budget"] == plan.budgets.model_dump(mode="json")
    assert all(attempt.participant_projection(plan) == other.participant_projection(plan)
               for other in module.schedule_attempts(plan) if other.cohort_id == attempt.cohort_id)


def test_participant_projection_rejects_a_different_plan_or_task_identity():
    module = api()
    plan = module.default_study_plan(stage="smoke")
    attempt = module.schedule_attempts(plan)[0]
    with pytest.raises(ValueError, match="different study plan"):
        attempt.participant_projection(changed(plan, seed=plan.seed + 1))
    with pytest.raises(ValueError, match="task identity"):
        changed(attempt, task_identity="0" * 64).participant_projection(plan)


def test_unknown_public_assets_remain_unacquired_not_zero_performance(tmp_path):
    module = api()
    plan = module.default_study_plan(stage="smoke")
    recipe = next(r for r in plan.dataset_recipes if r.recipe_id == plan.cohorts[0].recipe_id)
    public = changed(recipe, source_kind="public_processed", generator_family=None,
                     public_accession="GSE12345", status="not_acquired", latent_truth_scope="none",
                     biological_unit_policy="require_verified_biological_unit_identifiers",
                     independent_unit_count=None)
    plan = changed(plan, dataset_recipes=tuple(public if r.recipe_id == recipe.recipe_id else r
                                               for r in plan.dataset_recipes))
    receipt = module.prepare_plan(plan, (tmp_path / "public-plan").resolve())
    assert receipt["execution_performed"] is False
    assert receipt["unavailable_public_assets"] == ["GSE12345"]
    assert "performance" not in receipt


def test_prepare_writes_two_configuration_files_with_stable_hash(tmp_path):
    module = api()
    plan = module.default_study_plan(stage="smoke")
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    receipt = module.prepare_plan(plan, first)
    other = module.prepare_plan(plan, second)
    assert receipt["bundle_sha256"] == other["bundle_sha256"]
    assert receipt["attempt_count"] == 6
    assert receipt["planned_control_count"] == 0
    assert receipt["execution_performed"] is False
    assert receipt["status"] == "not_run"
    assert {p.name for p in first.iterdir()} == {"plan.json", "schedule.json"}
    assert (first / "plan.json").read_bytes() == (second / "plan.json").read_bytes()
    assert len(json.loads((first / "schedule.json").read_text())) == 6
    assert module.StudyPlan.model_validate_json((first / "plan.json").read_bytes()) == plan


def test_prepare_writes_control_schedule_only_for_explicit_registered_controls(tmp_path):
    module = api()
    plan = module.default_study_plan(stage="smoke")
    control = module.ScriptedControlPlan(control_id="control-1", cohort_id=plan.cohorts[0].cohort_id,
                                         procedure_id="prespecified-procedure-v1")
    plan = changed(plan, scripted_controls=(control,))
    output = (tmp_path / "with-control").resolve()
    receipt = module.prepare_plan(plan, output)
    assert receipt["attempt_count"] == 6
    assert receipt["planned_control_count"] == 1
    assert receipt["execution_performed"] is False
    assert {p.name for p in output.iterdir()} == {"plan.json", "schedule.json", "control_schedule.json"}
    assert "control_schedule.json" in receipt["file_sha256"]
    assert json.loads((output / "control_schedule.json").read_text()) == module.schedule_controls(plan)


def test_prepare_serializes_the_same_normalized_plan_as_its_schedule(tmp_path):
    module = api()
    plan = module.default_study_plan(stage="smoke")
    unchecked_recipe = plan.dataset_recipes[0].model_copy(
        update={"layout": None, "independent_unit_count": None})
    unchecked_plan = plan.model_copy(update={"dataset_recipes": (unchecked_recipe, *plan.dataset_recipes[1:])})
    output = (tmp_path / "normalized-plan").resolve()
    module.prepare_plan(unchecked_plan, output)
    saved = json.loads((output / "plan.json").read_text())
    assert saved["dataset_recipes"][0]["layout"] == "crossed"
    assert saved["dataset_recipes"][0]["independent_unit_count"] == 72
    saved_identity = module.content_sha256(saved)
    schedule = json.loads((output / "schedule.json").read_text())
    assert {attempt["plan_identity"] for attempt in schedule} == {saved_identity}
    assert unchecked_plan.dataset_recipes[0].layout is None
    assert unchecked_plan.dataset_recipes[0].independent_unit_count is None


def test_prepare_rejects_existing_relative_and_symlink_directories(tmp_path):
    module = api()
    plan = module.default_study_plan(stage="smoke")
    existing = tmp_path.resolve()
    with pytest.raises(FileExistsError):
        module.prepare_plan(plan, existing)
    with pytest.raises(ValueError, match="absolute"):
        module.prepare_plan(plan, Path("relative-output"))
    link = existing / "link"
    link.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        module.prepare_plan(plan, link / "new-plan")
    assert not (existing / "new-plan").exists()
