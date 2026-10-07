"""Configuration and denial tests; no biological generation or workers."""
import importlib

import pytest


def test_preparation_denies_before_inspecting_arguments():
    module = importlib.import_module("devbench.study.biology_preparation")
    with pytest.raises(PermissionError, match="disabled"):
        module.prepare_biology_study_data(object(), object())


def test_preparation_requires_explicit_seeds_without_creating_output(tmp_path):
    from devbench.study.biology_planning import default_biology_study
    module = importlib.import_module("devbench.study.biology_preparation")
    with pytest.raises(ValueError, match="generation seeds"):
        module.validate_biology_preparation(default_biology_study())
    assert not tuple(tmp_path.iterdir())


def test_seed_assignment_is_configuration_only_and_never_overwrites():
    from devbench.study.biology_planning import default_biology_study, assign_biology_seeds
    plan = default_biology_study()
    assigned = assign_biology_seeds(plan, 100)
    assert [r.generation_seed for r in assigned.recipes] == list(range(100, 120))
    assert all(r.generation_seed is None for r in plan.recipes)
    with pytest.raises(ValueError, match="already assigned"):
        assign_biology_seeds(assigned, 200)
    with pytest.raises(ValueError, match="seed"):
        assign_biology_seeds(plan, True)
    with pytest.raises(ValueError, match="seed"):
        assign_biology_seeds(plan, 2**63-1)


def test_registered_coverage_is_not_qualification(monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy"}
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    from devbench.study.biology import load_biology_catalog, check_biology_capabilities
    from devbench.study.biology_planning import default_biology_study
    from devbench.study.biology_registry import biology_runtime_registry, biology_method_metadata
    catalog = load_biology_catalog()
    registry = biology_runtime_registry(catalog)
    plan = default_biology_study(coverage="full")
    for recipe in plan.recipes:
        for target in check_biology_capabilities(recipe, catalog, registry):
            assert target.executable_handlers_registered
            assert target.qualification_status == "not_verified_by_configuration"
    assert any(row.get("estimation_limits") for row in biology_method_metadata())

