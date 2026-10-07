"""Plan-only scheduling and serialization; no generators or runtime adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
import random
import secrets
from typing import Any

from .contracts import (
    SCENARIO_FAMILIES, AttemptPlan, CohortPlan, DatasetRecipe, StudyPlan, StudyStage,
    canonical_json_bytes, content_sha256, control_attempt_id, task_identity,
)


def default_study_plan(study_id: str = "inference-three-arm-v1",
                       stage: StudyStage = "exploratory") -> StudyPlan:
    """Propose 6 smoke, 36 diagnostic or 216 exploratory attempts without running.

    Training/evaluation recipes and two generator families are configuration
    only. Generation seeds are unassigned and independent cohorts unverified.
    Each new plan gets private random blinding metadata; reload its serialized
    record to reproduce identifiers instead of creating another default plan.
    """
    if stage not in ("smoke", "diagnostic", "exploratory"):
        raise ValueError("stage must be smoke, diagnostic or exploratory")
    scenarios = SCENARIO_FAMILIES[:1] if stage == "smoke" else SCENARIO_FAMILIES
    count, repetitions = {"smoke": (2, 1), "diagnostic": (1, 2), "exploratory": (4, 3)}[stage]
    recipes = []
    cohorts = []
    for scenario in scenarios:
        evaluation_recipes = []
        for family in ("negative_binomial", "poisson_lognormal"):
            for split in ("training", "evaluation"):
                recipe = DatasetRecipe(
                    recipe_id=f"{scenario}-{family}-{split}-v1", scenario=scenario,
                    generator_family=family, split=split,
                    biological_unit_policy=("single_biological_series"
                                            if scenario == "technical_replicates"
                                            else "independent_biological_units"),
                    num_batches=6 if scenario == "complete_aliasing" else 3,
                    batch_labels=tuple(f"batch{index}" for index in
                                       range(1, 7 if scenario == "complete_aliasing" else 4)),
                    biological_units_per_cell=(1 if scenario == "technical_replicates" else
                                               12 if scenario == "complete_aliasing" else 4),
                    technical_replicates_per_unit=4 if scenario == "technical_replicates" else 1,
                )
                recipes.append(recipe)
                if split == "evaluation":
                    evaluation_recipes.append(recipe)
        for index in range(count):
            recipe = evaluation_recipes[index % len(evaluation_recipes)]
            cohort_identity = content_sha256({"study_id": study_id, "stage": stage,
                                               "scenario": scenario, "cohort_index": index})
            cohorts.append(CohortPlan(cohort_id=f"cohort-{cohort_identity[:24]}",
                                      scenario=scenario, recipe_id=recipe.recipe_id))
    return StudyPlan(study_id=study_id, blinding_nonce=secrets.token_hex(32),
                     stage=stage, scenario_families=scenarios,
                     cohorts_per_family=count, repetitions=repetitions,
                     dataset_recipes=tuple(recipes), cohorts=tuple(cohorts))


def schedule_attempts(plan: StudyPlan) -> list[AttemptPlan]:
    """Schedule every cohort/repetition triplet using a seeded six-order cycle.

    Complete six-block cycles balance arm positions and directed within-block
    order. Remainders use rotations, with no outcome-dependent rescheduling.
    """
    # Revalidate even if a caller used Pydantic's unchecked model_copy/update API.
    plan = StudyPlan.model_validate_json(plan.model_dump_json())
    return _schedule_validated_plan(plan)


def _schedule_validated_plan(plan: StudyPlan) -> list[AttemptPlan]:
    """Schedule the exact already-normalized instance used for serialization."""
    rng = random.Random(plan.seed)
    cohorts = list(plan.cohorts)
    rng.shuffle(cohorts)
    base_order = list(plan.conditions)
    rng.shuffle(base_order)
    attempts = []
    plan_hash = plan.sha256()
    block_index = 0
    for cohort in cohorts:
        scientific_identity = task_identity(plan, cohort)
        for repetition in range(1, plan.repetitions + 1):
            order = base_order if (block_index // 3) % 2 == 0 else list(reversed(base_order))
            shift = block_index % 3
            order = order[shift:] + order[:shift]
            for condition in order:
                attempt_hash = content_sha256({"plan_identity": plan_hash, "cohort_id": cohort.cohort_id,
                                                "repetition": repetition, "condition": condition})
                attempts.append(AttemptPlan(
                    attempt_id=f"attempt-{attempt_hash[:24]}", cohort_id=cohort.cohort_id,
                    scenario=cohort.scenario, condition=condition, repetition=repetition,
                    execution_order=len(attempts) + 1, recipe_id=cohort.recipe_id,
                    plan_identity=plan_hash, task_identity=scientific_identity,
                ))
            block_index += 1
    return attempts


def schedule_controls(plan: StudyPlan) -> list[dict[str, Any]]:
    """Serialize registered scripted-control slots without executing procedures."""
    plan = StudyPlan.model_validate_json(plan.model_dump_json())
    return _schedule_validated_controls(plan)


def _schedule_validated_controls(plan: StudyPlan) -> list[dict[str, Any]]:
    """Coordinator-only schedule, separate from the model attempt sequence."""
    cohorts = {cohort.cohort_id: cohort for cohort in plan.cohorts}
    plan_identity = plan.sha256()
    return [{"record_format": "devbench.study.control-attempt/1",
             **control.model_dump(mode="json"),
             "attempt_id": control_attempt_id(plan, control),
             "plan_identity": plan_identity,
             "task_identity": task_identity(plan, cohorts[control.cohort_id]),
             "execution_order": index, "status": "scheduled_not_run"}
            for index, control in enumerate(plan.scripted_controls, start=1)]


def prepare_plan(plan: StudyPlan, output_dir: Path) -> dict[str, Any]:
    """Write plan.json and schedule.json into a fresh absolute directory.

    Add control_schedule.json only when scripted controls are explicitly
    registered; those slots never change the model attempt count.
    Refuses symlink components and existing destinations. The receipt's hash
    binds exact file bytes and filenames, independent of time and destination.
    This operation neither acquires data nor grants execution authorization.
    """
    plan = StudyPlan.model_validate_json(plan.model_dump_json())
    if not isinstance(output_dir, Path) or not output_dir.is_absolute():
        raise ValueError("output_dir must be an absolute Path")
    for component in (output_dir, *output_dir.parents):
        if component.is_symlink():
            raise ValueError("output_dir must not contain symlink components")
    destination = output_dir.resolve(strict=False)
    attempts = _schedule_validated_plan(plan)
    controls = _schedule_validated_controls(plan)
    payloads = {
        "plan.json": canonical_json_bytes(plan.model_dump(mode="json")) + b"\n",
        "schedule.json": canonical_json_bytes([item.model_dump(mode="json") for item in attempts]) + b"\n",
    }
    if controls:
        payloads["control_schedule.json"] = canonical_json_bytes(controls) + b"\n"
    file_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, data in payloads.items():
        path = (destination / name).resolve(strict=False)
        with path.open("xb") as handle:
            handle.write(data)
    return {"record_format": "devbench.study.preparation/1", "study_id": plan.study_id,
            "status": "not_run", "execution_performed": False,
            "calibration_status": "uncalibrated", "attempt_count": len(attempts),
            "planned_control_count": len(controls),
            "bundle_sha256": content_sha256(file_hashes), "file_sha256": file_hashes,
            "output_dir": str(destination), "blocked_reasons": list(plan.blocked_reasons),
            "unavailable_public_assets": sorted({recipe.public_accession for recipe in plan.dataset_recipes
                                                   if recipe.source_kind == "public_processed"})}
