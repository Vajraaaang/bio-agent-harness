"""Biology coordinator guard/configuration checks; no biological data exists."""
from pathlib import Path

import pytest


@pytest.mark.parametrize("allowed", [False, None, 0, 1, "true"])
def test_biology_execution_denies_before_plan_paths_or_runtime_imports(allowed, monkeypatch):
    from devbench.study.biology_execution import execute_biology_study
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: pytest.fail("Guard reached paths"))
    with pytest.raises(PermissionError, match="disabled"):
        execute_biology_study(None, None, None, workspace_root=None, allow_data_execution=allowed)


def test_biology_projection_and_workers_are_default_denied():
    from devbench.study.biology_execution import (
        project_biology_task, _biology_reference_worker, _biology_attempt_worker,
    )
    with pytest.raises(PermissionError, match="disabled"):
        project_biology_task(None, None, None, None)
    with pytest.raises(PermissionError, match="disabled"):
        _biology_reference_worker(None)
    with pytest.raises(PermissionError, match="disabled"):
        _biology_attempt_worker(None)


def plan():
    from devbench.study.biology_planning import BiologyStudyPlan, default_biology_study
    proposed = default_biology_study(requested_model="pinned-model-2026-09-11")
    payload = proposed.model_dump(mode="json")
    payload["recipes"] = payload["recipes"][:1]
    payload["recipes"][0]["generation_seed"] = 123
    from devbench.study.contracts import canonical_json_bytes
    return BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload))


def bundle_for(study):
    from devbench.study.biology_datasets import PreparedBiologyBundle, _BASE_SOURCES
    from devbench.study.contracts import content_sha256
    recipe = study.recipes[0]
    sources = {path: "0" * 64 for path in (*_BASE_SOURCES, "devbench/study/biology_bulk.py")}
    return PreparedBiologyBundle(root="/opt/devbench/prepared/cohort", input_sha256="1" * 64,
        input_manifest_sha256="2" * 64, truth_sha256="3" * 64, preparation_receipt_sha256="4" * 64,
        recipe_id=recipe.recipe_id, cohort_id=recipe.recipe_id, plan_identity=study.sha256(),
        recipe_sha256=content_sha256(recipe.model_dump(mode="json")), catalog_sha256=study.catalog_sha256,
        generator_sha256=content_sha256(sources), generator_source_sha256="0" * 64, source_hashes=sources,
        measurement="raw_counts", population_unit_label="donor_id", unit_identity_status="declared_synthetic",
        num_genes=recipe.parameters.num_genes, num_observations=recipe.parameters.observation_count_upper_bound)


def test_configuration_requires_biology_plan_and_exact_prepared_mapping():
    from devbench.study.biology_execution import validate_biology_execution_configuration
    study = plan()
    bundle = bundle_for(study)
    actual, prepared = validate_biology_execution_configuration(study, {bundle.cohort_id: bundle})
    assert actual.sha256() == study.sha256() and prepared[bundle.cohort_id] == bundle
    with pytest.raises(TypeError):
        validate_biology_execution_configuration(object(), {})
    with pytest.raises(ValueError):
        validate_biology_execution_configuration(study, {})


def test_missing_model_or_seed_remains_unexecutable_configuration():
    from devbench.study.biology_execution import validate_biology_execution_configuration
    study = plan()
    bundle = bundle_for(study)
    with pytest.raises(ValueError, match="model"):
        validate_biology_execution_configuration(study.model_copy(update={"requested_model": None}), {})
    no_seed = study.model_copy(update={"recipes": (study.recipes[0].model_copy(update={"generation_seed": None}),)})
    with pytest.raises(ValueError, match="seed"):
        validate_biology_execution_configuration(no_seed, {bundle.cohort_id: bundle})


def test_initial_records_preserve_three_scheduled_arms_without_eligibility_or_results():
    from devbench.study.biology_execution import initial_biology_attempt_records
    study = plan()
    bundle = bundle_for(study)
    records = initial_biology_attempt_records(study, {bundle.cohort_id: bundle})
    assert len(records) == 3 and {row.condition for row in records} == {"baseline", "optional", "enforced"}
    for row in records:
        assert row.attempted is False and row.completion_status == "not_started"
        assert not row.target_reference_availability and row.reference_availability_sha256 is None
        assert row.actual_model is None and row.scientific_success is None and row.report_accepted is None
        assert row.trusted_tool_calls is None and row.safe_rows == () and row.per_target_results == ()


def test_live_attempt_cap_blocks_before_any_workspace_or_data_access():
    from devbench.study.biology_execution import execute_biology_study
    study = plan().model_copy(update={"repetitions": 3})
    bundle = bundle_for(study)
    with pytest.raises(ValueError, match="max_live_attempts"):
        execute_biology_study(study, {bundle.cohort_id: bundle}, None, workspace_root=None,
                             allow_data_execution=True)


@pytest.mark.parametrize("condition", ["baseline", "optional", "enforced"])
def test_condition_guidance_is_protocol_only_and_does_not_reveal_cases(condition):
    from devbench.study.biology_execution import biology_participant_guidance
    text = biology_participant_guidance(condition)
    assert "solve.py" in text and "answer_schema.json" in text
    assert "case_id" not in text and "T20-" not in text
    if condition == "optional":
        assert "no tool use" in text
    if condition == "enforced":
        assert "receipt_id" in text
