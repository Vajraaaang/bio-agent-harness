"""No observations: variation, blinding and seed configuration only."""
import pytest
from devbench.study.biology_planning import (default_biology_study, add_biology_stress_variants,
    assign_fresh_biology_seeds, schedule_biology_attempts)


def test_full_stress_plan_preserves_cases_and_pairs_all_arms():
    plan = add_biology_stress_variants(default_biology_study(coverage="full"))
    assert len({r.case_id for r in plan.recipes}) == 97
    assert len(plan.recipes) > 97
    assert {r.parameters.dispersion for r in plan.recipes} >= {.05, .2, .6}
    assert {r.parameters.nuisance_intensity for r in plan.recipes} >= {0., .25, 1.5}
    assert {"T06-balanced", "T06-partial", "T06-aliased"} <= {r.case_id for r in plan.recipes}
    assert {.35, 2.} <= {e.amplitude for r in plan.recipes for e in r.parameters.effect_programs}
    assert all(r.generation_seed is None for r in plan.recipes)
    attempts = schedule_biology_attempts(plan)
    assert len(attempts) == len(plan.recipes)*3
    assert all("T0" not in row.task_identity for row in attempts)


def test_fresh_seeds_are_distinct_retained_and_cannot_overwrite():
    plan = default_biology_study()
    a, b = assign_fresh_biology_seeds(plan), assign_fresh_biology_seeds(plan)
    assert len({r.generation_seed for r in a.recipes}) == len(a.recipes)
    assert {r.generation_seed for r in a.recipes}.isdisjoint(r.generation_seed for r in b.recipes)
    assert a.model_validate_json(a.model_dump_json()) == a
    with pytest.raises(ValueError, match="already assigned"):
        assign_fresh_biology_seeds(a)


def test_blinded_task_and_storage_identifiers_do_not_encode_case_or_stress_names():
    from devbench.study.biology_planning import biology_task_identity
    plan = add_biology_stress_variants(default_biology_study())
    names = ["cohort-" + biology_task_identity(plan, recipe)[:24] for recipe in plan.recipes]
    assert len(names) == len(set(names))
    assert all(not any(label in name for label in ("T01", "T20", "signal", "dispersion", "null", "aliased")) for name in names)
