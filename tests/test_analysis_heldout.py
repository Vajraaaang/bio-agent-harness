import json
from pathlib import Path

import pytest


def test_plan_is_fresh_balanced_and_preserves_development_parameters(tmp_path):
    from devbench.analysis.heldout import build_holdout_plan
    from devbench.analysis.validation import PROTOCOL
    path = Path('/Users/vajraang/Documents/ChatGPT/bio-agent-harness/results/scientific-preparation-2026-09-14/proposed-pilot/private/plan.json')
    prior = json.loads(path.read_text())
    plan = build_holdout_plan(prior, templates=("T01", "T06"))
    assert len(plan["recipes"]) == 6
    seeds = [r["generation_seed"] for r in plan["recipes"]]
    assert len(set(seeds)) == 6
    assert not set(seeds) & {r["generation_seed"] for r in prior["recipes"]}
    old = {r["template_id"]: r for r in prior["recipes"] if r["role"] == "evaluation"}
    for recipe in plan["recipes"]:
        assert recipe["parameters"] == old[recipe["template_id"]]["parameters"]
        assert recipe["case_id"] == old[recipe["template_id"]]["case_id"]
    assert plan["workbench_gates"] == PROTOCOL
    assert plan["tuning_after_generation_allowed"] is False


def test_default_plan_contains_only_the_two_requested_templates():
    from collections import Counter
    from devbench.analysis.heldout import build_holdout_plan
    plan = build_holdout_plan({"recipes": []})
    assert Counter(r["template_id"] for r in plan["recipes"]) == {"T01": 3, "T06": 3}


def test_source_change_aborts_before_further_measurement(tmp_path):
    from devbench.analysis.heldout import verify_source_freeze
    import hashlib
    path = tmp_path / "method.py"
    path.write_text("fixed method\n")
    frozen = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}
    verify_source_freeze(frozen)
    path.write_text("changed method\n")
    with pytest.raises(RuntimeError, match="changed"):
        verify_source_freeze(frozen)


def test_real_method_and_generator_sources_are_in_the_freeze():
    from devbench.analysis.heldout import source_freeze, verify_source_freeze
    frozen = source_freeze()
    assert any(name.endswith("devbench/analysis/inference.py") for name in frozen)
    assert any(name.endswith("devbench/study/biology_bulk.py") for name in frozen)
    assert any(name.endswith("devbench/study/biology_catalog.json") for name in frozen)
    verify_source_freeze(frozen)
