"""Biology study configuration only; no generator, numerical import or model."""
import builtins
import importlib

import pytest


def api():
    return importlib.import_module("devbench.study.biology_planning")


def test_full_coverage_plan_assigns_no_data_seed_and_has_three_equal_arms(monkeypatch):
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy"}
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    plan = api().default_biology_study(coverage="full")
    assert len(plan.recipes) == 97
    assert all(recipe.generation_seed is None for recipe in plan.recipes)
    attempts = api().schedule_biology_attempts(plan)
    assert len(attempts) == 291
    assert {condition: sum(row.condition == condition for row in attempts)
            for condition in plan.conditions} == {"baseline": 97, "optional": 97, "enforced": 97}


def test_each_default_case_satisfies_actual_generator_configuration_only():
    from devbench.study.biology_bulk import validate_biology_bulk_recipe
    from devbench.study.biology_singlecell import validate_biology_singlecell_recipe
    plan = api().default_biology_study(coverage="full")
    for recipe in plan.recipes:
        # A configuration-only seed satisfies the existing admission API;
        # it is never retained in the plan or supplied to a generator.
        admitted = recipe.model_copy(update={"generation_seed": 0})
        validate = validate_biology_bulk_recipe if int(recipe.template_id[1:]) <= 13 else validate_biology_singlecell_recipe
        assert validate(admitted).observation_upper_bound > 0


def test_saved_plan_schedule_is_stable_but_fresh_nonce_changes_blinded_ids():
    module = api()
    plan = module.default_biology_study(coverage="representative")
    restored = module.BiologyStudyPlan.model_validate_json(plan.model_dump_json())
    assert module.schedule_biology_attempts(plan) == module.schedule_biology_attempts(restored)
    other = module.default_biology_study(coverage="representative")
    assert module.schedule_biology_attempts(plan)[0].task_identity != module.schedule_biology_attempts(other)[0].task_identity


def test_role_separation_does_not_schedule_calibration_as_model_performance():
    from devbench.study.contracts import canonical_json_bytes
    module = api()
    plan = module.default_biology_study(coverage="representative")
    payload = plan.model_dump(mode="json")
    payload["recipes"][0]["role"] = "calibration"
    changed = module.BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload))
    assert len(module.schedule_biology_attempts(changed)) == 19 * 3


def test_duplicate_assigned_seeds_cannot_disguise_reused_cohorts():
    from devbench.study.contracts import canonical_json_bytes
    module = api()
    payload = module.default_biology_study().model_dump(mode="json")
    payload["recipes"][0]["generation_seed"] = 0
    payload["recipes"][1]["generation_seed"] = 0
    with pytest.raises(ValueError, match="generation seeds"):
        module.BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload))
