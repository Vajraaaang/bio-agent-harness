"""Guard/configuration coverage only: no datasets, tools or participant launches."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from devbench.study.contracts import StudyPlan
from devbench.study.planning import default_study_plan, schedule_attempts


def executable_plan():
    plan = default_study_plan(stage="smoke")
    payload = plan.model_dump(mode="json")
    payload["requested_model"] = "pinned-model-2026-09-11"
    for index, cohort in enumerate(payload["cohorts"]):
        cohort["generation_seed"] = 100 + index
    return StudyPlan.model_validate_json(json.dumps(payload))


def prepared(plan):
    from devbench.study.execution import PreparedCohortBundle
    return {cohort.cohort_id: PreparedCohortBundle(
        root=Path("/opt/study/bundles") / cohort.cohort_id,
        input_sha256=f"{index + 1:064x}", truth_sha256=f"{index + 10:064x}",
        recipe_id=cohort.recipe_id, generation_seed=cohort.generation_seed,
        independence_id=f"independent-cohort-{index}", unit_provenance_verified=True,
    ) for index, cohort in enumerate(plan.cohorts)}


@pytest.mark.parametrize("authorization", [False, None, 0, 1, "true"])
def test_execution_guard_precedes_plan_paths_and_files(authorization, monkeypatch):
    from devbench.study.execution import execute_study
    def forbidden(*args, **kwargs):
        pytest.fail("Configuration/filesystem was reached before authorization")
    monkeypatch.setattr(Path, "resolve", forbidden)
    with pytest.raises(PermissionError, match="disabled"):
        execute_study(object(), object(), object(), workspace_parent=object(),
                      allow_data_execution=authorization)


def test_default_execution_authorization_is_off():
    from devbench.study.execution import execute_study
    with pytest.raises(PermissionError, match="disabled"):
        execute_study(None, {}, Path("/opt/output"), workspace_parent=Path("/opt/workspaces"))


def test_private_attempt_and_materialization_entry_points_are_also_guarded():
    from devbench.study.execution import _attempt, _attempt_worker, _run_owned_attempt, _stage_inputs
    with pytest.raises(PermissionError, match="disabled"):
        _stage_inputs(*([None] * 6))
    with pytest.raises(PermissionError, match="disabled"):
        _attempt(*([None] * 11))
    with pytest.raises(PermissionError, match="disabled"):
        _run_owned_attempt(*([None] * 11))
    with pytest.raises(PermissionError, match="disabled"):
        _attempt_worker(None, None, None)


def test_prepared_bundle_contract_is_immutable_and_explicit():
    bundle = next(iter(prepared(executable_plan()).values()))
    with pytest.raises(FrozenInstanceError):
        bundle.input_sha256 = "0" * 64


def test_prepared_bundle_json_parser_is_exact():
    from devbench.study.execution import PreparedCohortBundle
    bundle = next(iter(prepared(executable_plan()).values()))
    payload = {**vars(bundle), "root": str(bundle.root)}
    assert PreparedCohortBundle.from_mapping(payload) == bundle
    with pytest.raises(ValueError, match="fields"):
        PreparedCohortBundle.from_mapping({**payload, "scenario": "private-label"})
    with pytest.raises(TypeError, match="JSON path"):
        PreparedCohortBundle.from_mapping(vars(bundle))


@pytest.mark.parametrize("updates", [
    {"root": Path("relative")}, {"root": Path("/tmp/study-private")},
    {"input_sha256": "missing"}, {"truth_sha256": "missing"},
    {"unit_provenance_verified": 1}, {"generation_seed": True},
    {"independence_id": ""},
])
def test_invalid_prepared_bundle_configuration_is_rejected(updates):
    from dataclasses import replace
    bundle = next(iter(prepared(executable_plan()).values()))
    with pytest.raises((ValueError, TypeError)):
        replace(bundle, **updates)


def test_execution_configuration_requires_pinned_model_before_bundle_access():
    from devbench.study.execution import validate_execution_configuration
    with pytest.raises(ValueError, match="requested_model"):
        validate_execution_configuration(default_study_plan(stage="smoke"), {})


def test_configuration_validates_all_cohort_bindings_without_opening_files(monkeypatch):
    from devbench.study.execution import validate_execution_configuration
    plan = executable_plan()
    bundles = prepared(plan)
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("Opened data during validation"))
    actual, ordered = validate_execution_configuration(plan, bundles)
    assert actual.sha256() == plan.sha256()
    assert set(ordered) == {cohort.cohort_id for cohort in plan.cohorts}
    assert schedule_attempts(actual) == schedule_attempts(plan)


@pytest.mark.parametrize("case", ["missing", "extra", "path_only", "wrong_recipe", "wrong_seed", "unassigned_seed", "same_input", "same_independence"])
def test_invalid_cohort_mapping_never_becomes_a_schedule(case):
    from dataclasses import replace
    from devbench.study.execution import validate_execution_configuration
    plan = executable_plan()
    bundles = prepared(plan)
    first, second = list(bundles)
    if case == "missing":
        bundles.pop(first)
    elif case == "extra":
        bundles["unexpected"] = bundles[first]
    elif case == "path_only":
        bundles[first] = bundles[first].root
    elif case == "wrong_recipe":
        bundles[first] = replace(bundles[first], recipe_id="different-recipe")
    elif case == "wrong_seed":
        bundles[first] = replace(bundles[first], generation_seed=998)
    elif case == "unassigned_seed":
        plan = plan.model_copy(update={"cohorts": tuple(cohort.model_copy(update={"generation_seed": None})
                                                       for cohort in plan.cohorts)})
    elif case == "same_input":
        bundles[second] = replace(bundles[second], input_sha256=bundles[first].input_sha256)
    else:
        bundles[second] = replace(bundles[second], independence_id=bundles[first].independence_id)
    with pytest.raises((ValueError, TypeError)):
        validate_execution_configuration(plan, bundles)


def test_empty_records_preserve_every_planned_attempt_and_unknown_evidence():
    from devbench.study.execution import initial_attempt_records
    plan = executable_plan()
    records = initial_attempt_records(plan, prepared(plan))
    assert len(records) == 6
    assert [row.attempt_id for row in records] == [row.attempt_id for row in schedule_attempts(plan)]
    assert {row.condition for row in records} == {"baseline", "optional", "enforced"}
    for row in records:
        assert row.completion_status == "not_started" and row.attempted is False
        assert row.input_sha256 is None and row.shared_environment_sha256 is None
        assert row.actual_model is None and row.model_identity_verified is False
        assert row.accepted_final is False and row.report_accepted is None
        assert row.scientific_success is None and row.workflow_passed is None
        assert row.trusted_tool_calls is None and row.trusted_tool_capture_complete is False
        assert row.accounting_completeness.model_dump() == dict.fromkeys(
            ("elapsed_sec", "RSS_MB", "tokens", "cost"), "unavailable")


@pytest.mark.parametrize("stage,attempted,metadata,error,expected", [
    ("execution_capture_validation", True, {}, RuntimeError("artifact capture missing"),
     ("tool_error", "execution_capture_incomplete")),
    ("script_provenance_validation", True, {}, RuntimeError("direct execution absent"),
     ("invalid_output", "script_execution_unverified")),
    ("participant_execution", True, {"event_capture_complete": False}, ValueError("answer schema"),
     ("tool_error", "execution_capture_incomplete")),
    ("participant_execution", True, {}, RuntimeError("provider infrastructure mentions schema artifact deadline"),
     ("provider_error", "participant_execution_failure")),
    ("participant_execution", True, {}, TimeoutError("typed deadline"),
     ("timeout", "participant_deadline")),
    ("participant_execution", False, {"dispatch_stage": "checkpoint_failed_before_launch"}, RuntimeError("write"),
     ("blocked", "dispatch_checkpoint_failed")),
    ("preparation", False, {}, RuntimeError("artifact"), ("blocked", "preparation_failure")),
    ("participant_execution", True, {"event_capture_complete": True, "returncode": 0,
                                     "script_execution_observed": False}, RuntimeError("proof missing"),
     ("invalid_output", "script_execution_unverified")),
    ("participant_execution", True, {"event_capture_complete": True, "returncode": 0,
                                     "script_execution_observed": True}, ValueError("invalid output"),
     ("invalid_output", "answer_validation_failed")),
])
def test_failure_taxonomy_uses_control_state_not_error_text(stage, attempted, metadata, error, expected):
    """Configuration states only; no result artifact or numerical output exists."""
    from devbench.study.execution import _failure_outcome
    assert _failure_outcome(error, stage=stage, attempted=attempted, metadata=metadata) == expected
