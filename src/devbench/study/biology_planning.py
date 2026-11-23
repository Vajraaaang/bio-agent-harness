"""Frozen plans for broad biological cases; no data or model work on import."""
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import secrets
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .biology import BiologyRecipe, GenerationParameters, load_biology_catalog, validate_role_separation
from .contracts import (CONDITIONS, Condition, FrozenContract, Identifier, ReasoningEffort,
                        RunBudget, Sha256, canonical_json_bytes, content_sha256)
from .biology_scoring import BiologyScoringThresholds


class BiologyStudyPlan(FrozenContract):
    """One independent generated cohort per recipe; repeats share that cohort.

    Training/calibration roles reserve data for method development; they never
    become evaluation participants. Case-disjoint identifiers do not establish
    disjoint biological mechanisms or empirical out-of-distribution validity.
    """
    record_format: Literal["devbench.biology.study/1"] = "devbench.biology.study/1"
    study_id: Identifier
    blinding_nonce: Sha256 = Field(repr=False)
    catalog_sha256: Sha256
    recipes: tuple[BiologyRecipe, ...] = Field(min_length=1, max_length=1000)
    conditions: tuple[Literal["baseline"], Literal["optional"], Literal["enforced"]] = CONDITIONS
    repetitions: Annotated[int, Field(ge=1, le=100)] = 1
    requested_model: Annotated[str, Field(min_length=1, max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]*$")] | None = None
    reasoning_effort: ReasoningEffort | None = None
    budget: RunBudget = Field(default_factory=lambda: RunBudget(cpu_threads=1))
    thresholds: BiologyScoringThresholds = Field(default_factory=BiologyScoringThresholds)
    primary_outcome: Literal["raw_scientific_success"] = "raw_scientific_success"
    primary_contrast: tuple[Literal["enforced"], Literal["baseline"]] = ("enforced", "baseline")
    role_split: Literal["independent_cohorts", "case_disjoint"] = "independent_cohorts"
    status: Literal["planned_not_run"] = "planned_not_run"
    calibration_status: Literal["uncalibrated"] = "uncalibrated"

    def sha256(self):
        return content_sha256(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def coherent_plan(self):
        validate_role_separation(self.recipes)
        evaluation = {row.case_id for row in self.recipes if row.role == "evaluation"}
        if not evaluation:
            raise ValueError("A benchmark plan requires at least one evaluation recipe")
        if self.role_split == "case_disjoint":
            development = {row.case_id for row in self.recipes if row.role != "evaluation"}
            if evaluation & development:
                raise ValueError("Case-disjoint role assignment cannot reuse an evaluation case")
        if len([row for row in self.recipes if row.role == "evaluation"]) * self.repetitions * 3 > 100_000:
            raise ValueError("Planned model attempts exceed the bounded study budget")
        return self


class BiologyAttemptPlan(FrozenContract):
    attempt_id: Identifier
    study_id: Identifier
    plan_identity: Sha256
    task_identity: Sha256
    cohort_id: Identifier
    recipe_id: Identifier
    template_id: Identifier
    case_id: Identifier
    condition: Condition
    repetition: Annotated[int, Field(ge=1)]
    execution_order: Annotated[int, Field(ge=1)]
    status: Literal["scheduled_not_run"] = "scheduled_not_run"


def default_biology_recipe(case_id, *, cohort_index=1, num_genes=2000, role="evaluation"):
    """Choose declared design values, not observations or random effect values."""
    template = str(case_id).split("-", 1)[0]
    p = {"num_genes": num_genes, "independent_units_per_group": 4,
         "condition_labels": ("control", "treated"),
         "time_grid": tuple(float(index) for index in range(13)), "time_units": "hours"}
    if int(template[1:]) <= 13:
        if case_id in {"T01-sparse", "T01-unsampled-pulse", "T02-aliased"}:
            p["time_grid"] = tuple(float(index) for index in range(6))
        if template == "T04":
            p.update(time_grid=(), time_units=None, dose_grid=(0.0, 1.0, 3.0, 10.0, 30.0, 100.0), dose_units="nM")
        if template == "T06":
            p["num_batches"] = 2
        if case_id in {"T07-repeated-donors", "T07-single-unit"}:
            p["repeated_specimens_per_unit"] = 13
        if case_id == "T07-single-unit":
            p["independent_units_per_group"] = 1
        if case_id == "T07-technical":
            p["technical_libraries_per_specimen"] = 2
        if case_id == "T08-genotype-balanced":
            p["independent_units_per_group"] = 6
        if template == "T11":
            p["cell_type_count"] = 3
        if case_id == "T12-mar":
            # Reserve replication before MAR loss; the generator still rejects
            # any realized under-supported cohort without resampling it.
            p["independent_units_per_group"] = 8
    else:
        p.update(independent_units_per_group=3, time_grid=(), time_units=None,
                 cells_per_specimen=40, cell_type_count=3)
        p["condition_labels"] = ("control", "treated") if template in {"T14", "T20"} else ("observed",)
        if case_id == "T20-double-perturbation":
            p["condition_labels"] = ("control", "a", "b", "a_b")
        if case_id == "T14-one-donor":
            p["independent_units_per_group"] = 1
        if case_id == "T16-age-mismatch":
            p.update(time_grid=(0.0, 1.0, 2.0), time_units="days")
    return BiologyRecipe(recipe_id=f"{case_id}.{role}.{cohort_index:04d}", template_id=template,
                         case_id=case_id, role=role, generation_seed=None,
                         parameters=GenerationParameters(**p), mechanism_holdout_group=template)


def default_biology_study(study_id="biology-three-arm-v1", *, coverage="representative",
                          cohorts_per_case=1, repetitions=1, num_genes=2000, requested_model=None):
    if coverage not in {"representative", "full"}:
        raise ValueError("Coverage must be representative or full")
    if type(cohorts_per_case) is not int or not 1 <= cohorts_per_case <= 10:
        raise ValueError("Default preparation supports one to ten cohorts per case")
    catalog = load_biology_catalog()
    cases = [case for template in catalog.templates for case in
             (template.cases if coverage == "full" else template.cases[:1])]
    recipes = tuple(default_biology_recipe(case.case_id, cohort_index=cohort, num_genes=num_genes)
                    for case in cases for cohort in range(1, cohorts_per_case + 1))
    plan = BiologyStudyPlan(study_id=study_id, blinding_nonce=secrets.token_hex(32),
        catalog_sha256=content_sha256(catalog.model_dump(mode="json")), recipes=recipes,
        repetitions=repetitions, requested_model=requested_model)
    validate_biology_plan(plan)
    return plan


def validate_biology_plan(plan):
    """Validate catalog and generator admission without invoking a generator."""
    plan = BiologyStudyPlan.model_validate_json(plan.model_dump_json())
    catalog = load_biology_catalog()
    if plan.catalog_sha256 != content_sha256(catalog.model_dump(mode="json")):
        raise ValueError("Study plan does not match the installed frozen biology catalog")
    from .biology_bulk import validate_biology_bulk_recipe
    from .biology_singlecell import validate_biology_singlecell_recipe
    for recipe in plan.recipes:
        recipe.validate_against(catalog)
        # The existing admission API requires an assigned seed. This temporary
        # integer validates only nonrandom constraints; it is never retained or
        # supplied to a generator and does not authorize dataset preparation.
        check = recipe if recipe.generation_seed is not None else recipe.model_copy(update={"generation_seed": 0})
        validator = validate_biology_bulk_recipe if int(recipe.template_id[1:]) <= 13 else validate_biology_singlecell_recipe
        validator(check)
    return plan


def assign_biology_seeds(plan, seed_base):
    """Freeze distinct future seeds without drawing random data or changing a saved plan."""
    plan = validate_biology_plan(plan)
    if type(seed_base) is not int or not 0 <= seed_base <= 2**63 - len(plan.recipes):
        raise ValueError("The seed range must fit the declared nonnegative 63-bit seed domain")
    if any(recipe.generation_seed is not None for recipe in plan.recipes):
        raise ValueError("Generation seeds are already assigned; create a separate explicit plan revision")
    payload = plan.model_dump(mode="json")
    for index, recipe in enumerate(payload["recipes"]):
        recipe["generation_seed"] = seed_base + index
    return validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload)))


def assign_fresh_biology_seeds(plan):
    """Assign and retain new seeds without generating expression or running agents."""
    plan = validate_biology_plan(plan)
    if any(recipe.generation_seed is not None for recipe in plan.recipes):
        raise ValueError("Generation seeds are already assigned; create a new plan for a new study")
    payload = plan.model_dump(mode="json")
    used = set()
    for recipe in payload["recipes"]:
        seed = secrets.randbits(63)
        while seed in used:
            seed = secrets.randbits(63)
        used.add(seed)
        recipe["generation_seed"] = seed
    return validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload)))


def add_biology_stress_variants(plan):
    """Prespecified one-factor changes; does not choose settings from outcomes.

    Signal amplitude is varied only where the implemented generator exposes a
    log2-effect control. Cellular mechanisms retain their original equations.
    Confounding allocation remains the balanced/partial/aliased case ladder;
    nuisance-strength changes do not themselves change identifiability.
    """
    plan = validate_biology_plan(plan)
    if any(recipe.generation_seed is not None for recipe in plan.recipes):
        raise ValueError("Add stress variants before assigning generation seeds")
    programs = {"T01": (("E01", .16), ("E02", .16), ("E03", .16), ("E04", .16)),
                "T02": (("E05", .5),), "T03": (("E07", .5),), "T04": (("E06", .5),)}
    payload = plan.model_dump(mode="json")
    expanded = []
    for recipe in payload["recipes"]:
        expanded.append(recipe)
        variants = [("dispersion-low", "dispersion", .05), ("dispersion-high", "dispersion", .6),
                    ("nuisance-low", "nuisance_intensity", .25), ("nuisance-high", "nuisance_intensity", 1.5)]
        for name, field, value in variants:
            if recipe["parameters"][field] == value:
                continue
            changed = {**recipe, "recipe_id": recipe["recipe_id"] + "." + name,
                       "parameters": {**recipe["parameters"], field: value}}
            expanded.append(changed)
        if recipe["template_id"] in programs and not recipe["parameters"]["effect_programs"]:
            for name, amplitude in (("signal-weak", .35), ("signal-strong", 2.0)):
                effects = [{"effect_id": effect, "gene_fraction": fraction, "amplitude": amplitude,
                            "amplitude_units": "log2_fold_change"}
                           for effect, fraction in programs[recipe["template_id"]]]
                expanded.append({**recipe, "recipe_id": recipe["recipe_id"] + "." + name,
                    "parameters": {**recipe["parameters"], "effect_programs": effects}})
    if len(expanded) > 1000:
        raise ValueError("Stress expansion exceeds 1000 cohorts; reduce the configured cohort count")
    payload["recipes"] = expanded
    return validate_biology_plan(BiologyStudyPlan.model_validate_json(canonical_json_bytes(payload)))


def biology_task_identity(plan, recipe):
    return _task_identity(plan, recipe, plan.sha256())


def _task_identity(plan, recipe, plan_identity):
    return hmac.new(bytes.fromhex(plan.blinding_nonce), canonical_json_bytes({
        "domain": "devbench.biology.task/1", "plan": plan_identity, "recipe": recipe.recipe_id}), hashlib.sha256).hexdigest()


def schedule_biology_attempts(plan):
    plan = validate_biology_plan(plan)
    identity = plan.sha256()
    rows, block = [], 0
    for recipe in plan.recipes:
        if recipe.role != "evaluation":
            continue
        task_identity = _task_identity(plan, recipe, identity)
        for repetition in range(1, plan.repetitions + 1):
            order = list(plan.conditions)
            if (block // 3) % 2:
                order.reverse()
            shift = block % 3
            order = order[shift:] + order[:shift]
            for condition in order:
                digest = content_sha256({"plan": identity, "recipe": recipe.recipe_id,
                                         "condition": condition, "repetition": repetition})
                rows.append(BiologyAttemptPlan(attempt_id="bio-attempt-" + digest[:24],
                    study_id=plan.study_id, plan_identity=identity,
                    task_identity=task_identity, cohort_id=recipe.recipe_id,
                    recipe_id=recipe.recipe_id, template_id=recipe.template_id, case_id=recipe.case_id,
                    condition=condition, repetition=repetition, execution_order=len(rows) + 1))
            block += 1
    return rows


def prepare_biology_plan(plan, output_dir):
    """Write private configuration only to a new protected absolute directory."""
    from .permissions import require_protected_storage
    path = Path(output_dir)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Private plan paths must not contain symlinks")
    path = require_protected_storage(path)
    plan = validate_biology_plan(plan)
    payloads = {"plan.json": canonical_json_bytes(plan.model_dump(mode="json")),
                "schedule.json": canonical_json_bytes([row.model_dump(mode="json") for row in schedule_biology_attempts(plan)])}
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = {}
    for name, payload in payloads.items():
        with (path / name).open("xb") as handle:
            handle.write(payload)
        (path / name).chmod(0o400)
        hashes[name] = hashlib.sha256(payload).hexdigest()
    receipt = {"record_format": "devbench.biology.plan-preparation/1", "plan_identity": plan.sha256(),
               "files": hashes, "planned_attempts": len(schedule_biology_attempts(plan)),
               "data_executed": False, "model_participants_launched": 0}
    with (path / "receipt.json").open("xb") as handle:
        handle.write(canonical_json_bytes(receipt))
    return receipt
