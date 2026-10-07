"""Immutable coordinator contracts for a proposed, unexecuted three-arm study.

These records define research recipes and scheduling limits. They are not data,
evidence of independent cohorts, calibrated decision rules, or permission to run.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import hmac
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Condition = Literal["baseline", "optional", "enforced"]
ActorType = Literal["model_participant", "scripted_control"]
Scenario = Literal[
    "balanced_temporal", "partial_confounding", "complete_aliasing",
    "temporal_null", "library_composition", "technical_replicates",
]
GeneratorFamily = Literal["negative_binomial", "poisson_lognormal"]
StudyStage = Literal["smoke", "diagnostic", "exploratory"]
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
CONDITIONS: tuple[Condition, ...] = ("baseline", "optional", "enforced")
SCENARIO_FAMILIES: tuple[Scenario, ...] = (
    "balanced_temporal", "partial_confounding", "complete_aliasing",
    "temporal_null", "library_composition", "technical_replicates",
)
Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Probability = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
PositiveFinite = Annotated[float, Field(strict=True, gt=0.0, allow_inf_nan=False)]


def canonical_json_bytes(value: Any) -> bytes:
    """Encode finite JSON deterministically, without timestamps or machine paths."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def content_sha256(value: Any) -> str:
    """Hash a JSON-compatible coordinator contract."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              validate_default=True, allow_inf_nan=False)


class RunBudget(FrozenContract):
    """Identical resource ceilings for every study condition."""

    timeout_sec: Annotated[int, Field(ge=1, le=86_400)] = 600
    cpu_threads: Annotated[int, Field(ge=1, le=128)] = 2
    max_memory_mb: Annotated[int, Field(ge=64, le=1_048_576)] = 4096
    token_budget: Annotated[int, Field(ge=1, le=1_000_000_000)] | None = None


class MetricThresholds(FrozenContract):
    """Provisional research targets; the display cutoff is not a power guarantee."""

    status: Literal["provisional_uncalibrated"] = "provisional_uncalibrated"
    alpha: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.05
    minimum_signed_recall: Probability = 0.70
    maximum_fdp: Probability = 0.10
    minimum_shape_macro_f1: Probability = 0.70
    eligible_amplitude_log2: PositiveFinite = 0.5
    eligible_mean: PositiveFinite = 20.0
    confidence_level: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.95
    minimum_independent_cohorts: Annotated[int, Field(ge=10, le=100_000)] = 10


class DatasetRecipe(FrozenContract):
    """A bounded data specification; constructing one never acquires data.

    Generation families are development proposals. Public processed sources
    carry no latent truth, and biological independence must be verified later.
    """

    recipe_id: Identifier
    scenario: Scenario
    layout: Literal["crossed", "partial", "time_aliased_batches", "single_series"] | None = None
    source_kind: Literal["synthetic", "public_processed"] = "synthetic"
    measurement_type: Literal["raw_counts", "continuous"] = "raw_counts"
    generator_family: GeneratorFamily | None = "negative_binomial"
    public_accession: Annotated[str, Field(pattern=r"^(GSE[0-9]+|E-MTAB-[0-9]+|E-GEOD-[0-9]+)$")] | None = None
    split: Literal["training", "evaluation"] = "evaluation"
    biological_unit_policy: Literal[
        "independent_biological_units", "technical_replicates_nested_within_unit",
        "single_biological_series", "require_verified_biological_unit_identifiers",
    ] = "independent_biological_units"
    latent_truth_scope: Literal["synthetic_generator", "none"] = "synthetic_generator"
    status: Literal["planned", "not_acquired"] = "planned"
    source_sha256: Sha256 | None = None
    num_timepoints: Annotated[int, Field(ge=3, le=100)] = 6
    num_batches: Annotated[int, Field(ge=1, le=100)] = 3
    biological_units_per_cell: Annotated[int, Field(ge=1, le=1000)] = 4
    independent_unit_count: Annotated[int, Field(ge=1, le=10_000_000)] | None = None
    technical_replicates_per_unit: Annotated[int, Field(ge=1, le=100)] = 1
    num_genes: Annotated[int, Field(ge=1, le=100_000)] = 2000
    max_matrix_bytes: Annotated[int, Field(ge=1, le=100_000_000)] = 100_000_000
    timepoint_labels: tuple[Identifier, ...] = ("t1", "t2", "t3", "t4", "t5", "t6")
    batch_labels: tuple[Identifier, ...] = ("batch1", "batch2", "batch3")

    @property
    def occupied_cell_count(self) -> int:
        """Declared occupied time/batch cells; empty crossed cells are not observations.

        time_aliased_batches pairs equally indexed time and batch labels, so
        every time is observed in exactly one unique batch. A partial layout
        retains the crossed budget; its allocation remains to be calibrated.
        """
        if self.layout == "time_aliased_batches":
            return self.num_timepoints
        return self.num_timepoints * self.num_batches

    @property
    def sample_count(self) -> int:
        """Planned library rows, including technical libraries and repeated samples.

        Under single_biological_series the same one series occupies every
        timepoint/batch cell. Library rows never establish biological identity.
        """
        return (self.occupied_cell_count * self.biological_units_per_cell * self.technical_replicates_per_unit)

    @model_validator(mode="after")
    def validate_recipe(self) -> DatasetRecipe:
        expected_layout = {"complete_aliasing": "time_aliased_batches",
                           "partial_confounding": "partial",
                           "technical_replicates": "single_series"}.get(self.scenario, "crossed")
        if self.layout is None:
            object.__setattr__(self, "layout", expected_layout)
        elif self.layout != expected_layout:
            raise ValueError("layout must match the declared scenario")
        if self.layout == "time_aliased_batches" and self.num_batches != self.num_timepoints:
            raise ValueError("time_aliased_batches requires the same number of batches and timepoints")
        for labels, expected, name in (
            (self.timepoint_labels, self.num_timepoints, "timepoint_labels"),
            (self.batch_labels, self.num_batches, "batch_labels"),
        ):
            if len(labels) != expected or len(set(labels)) != expected:
                raise ValueError(f"{name} must have exactly {expected} distinct labels")
        if self.source_kind == "synthetic":
            if self.generator_family is None or self.public_accession is not None:
                raise ValueError("synthetic recipes require a generator family and no public accession")
            if self.measurement_type != "raw_counts":
                raise ValueError("the registered synthetic families specify raw_counts")
            if self.latent_truth_scope != "synthetic_generator" or self.status != "planned":
                raise ValueError("synthetic recipes must be planned with synthetic_generator truth scope")
            if self.biological_unit_policy == "require_verified_biological_unit_identifiers":
                raise ValueError("synthetic recipes must specify the intended biological unit structure")
        else:
            if self.generator_family is not None or self.public_accession is None:
                raise ValueError("public_processed recipes require an accession and no generator family")
            if self.latent_truth_scope != "none" or self.status != "not_acquired":
                raise ValueError("public recipes must be not_acquired and have no latent truth")
            if self.biological_unit_policy != "require_verified_biological_unit_identifiers":
                raise ValueError("public recipes require verified biological unit identifiers before use")
            if self.independent_unit_count is not None:
                raise ValueError("unacquired public recipes have no verified independent_unit_count")
        if self.source_kind == "synthetic":
            single_series = self.biological_unit_policy == "single_biological_series"
            nested = self.biological_unit_policy == "technical_replicates_nested_within_unit"
            if (nested or single_series) != (self.technical_replicates_per_unit > 1):
                raise ValueError("technical replicate limits must match the biological unit policy")
            if self.scenario == "technical_replicates" and not single_series:
                raise ValueError("technical_replicates scenario requires a single_biological_series")
            if single_series and self.biological_units_per_cell != 1:
                raise ValueError("single_biological_series requires the same one biological unit in every cell")
            expected_units = (1 if single_series else
                              self.occupied_cell_count * self.biological_units_per_cell)
            if self.independent_unit_count is None:
                # Normalize the declared design, not an observation of biological independence.
                object.__setattr__(self, "independent_unit_count", expected_units)
            elif self.independent_unit_count != expected_units:
                raise ValueError("independent_unit_count must match the declared biological unit structure")
        upper_bound_bytes = self.sample_count * self.num_genes * 8
        if upper_bound_bytes > self.max_matrix_bytes:
            raise ValueError("planned dense matrix upper bound exceeds max_matrix_bytes")
        return self


class CohortPlan(FrozenContract):
    """Coordinator-only proposed cohort; no observed independence is implied."""

    cohort_id: Identifier
    scenario: Scenario
    recipe_id: Identifier
    generation_seed: Seed | None = None
    independence_status: Literal["planned_not_verified"] = "planned_not_verified"


class ScriptedControlPlan(FrozenContract):
    """Explicit procedure registration, separate from every model participant slot."""

    control_id: Identifier
    cohort_id: Identifier
    procedure_id: Identifier
    condition: Condition = "baseline"
    repetition: Annotated[int, Field(ge=1, le=100)] = 1
    actor_type: Literal["scripted_control"] = "scripted_control"


class StudyPlan(FrozenContract):
    """Versioned coordinator plan. Never copy this record to a participant.

    blinding_nonce is private security metadata, not a dataset generation seed.
    It must be retained for deterministic replay of the saved study plan.
    requested_model and reasoning_effort express intent only. A future executor
    must require requested_model and record observed runtime identity separately.
    """

    record_format: Literal["devbench.study/1"] = "devbench.study/1"
    study_id: Identifier
    blinding_nonce: Sha256 = Field(repr=False)
    stage: StudyStage = "exploratory"
    status: Literal["not_run"] = "not_run"
    calibration_status: Literal["uncalibrated"] = "uncalibrated"
    conditions: tuple[Literal["baseline"], Literal["optional"], Literal["enforced"]] = (
        "baseline", "optional", "enforced")
    seed: Seed = 42
    seed_scope: Literal["schedule_randomization_only"] = "schedule_randomization_only"
    requested_model: Annotated[str, Field(min_length=1, max_length=200,
                                         pattern=r"^[A-Za-z0-9][A-Za-z0-9_./:-]*$")] | None = None
    reasoning_effort: ReasoningEffort | None = None
    cohorts_per_family: Annotated[int, Field(ge=1, le=1000)] = 4
    repetitions: Annotated[int, Field(ge=1, le=100)] = 3
    scenario_families: tuple[Scenario, ...] = SCENARIO_FAMILIES
    budgets: RunBudget = Field(default_factory=RunBudget)
    primary_comparison: tuple[Literal["enforced"], Literal["baseline"]] = ("enforced", "baseline")
    exploratory_comparisons: tuple[
        tuple[Literal["optional"], Literal["baseline"]],
        tuple[Literal["enforced"], Literal["optional"]],
    ] = (("optional", "baseline"), ("enforced", "optional"))
    scientific_question_ref: Identifier = "devbench-study-temporal-question-v1"
    answer_schema_ref: Identifier = "devbench-study-answer-v1"
    dataset_recipes: tuple[DatasetRecipe, ...]
    cohorts: tuple[CohortPlan, ...]
    scripted_controls: tuple[ScriptedControlPlan, ...] = ()
    thresholds: MetricThresholds = Field(default_factory=MetricThresholds)
    blocked_reasons: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = (
        "Study execution has not been authorized or performed.",
        "Development recipes and research thresholds are uncalibrated.",
        "Datasets are unacquired; cohort independence is not verified.",
    )

    @model_validator(mode="after")
    def validate_design(self) -> StudyPlan:
        if not self.scenario_families or len(set(self.scenario_families)) != len(self.scenario_families):
            raise ValueError("scenario_families must be nonempty and distinct")
        recipes = {recipe.recipe_id: recipe for recipe in self.dataset_recipes}
        if not recipes or len(recipes) != len(self.dataset_recipes):
            raise ValueError("dataset recipe IDs must be nonempty and distinct")
        if any(recipe.scenario not in self.scenario_families for recipe in self.dataset_recipes):
            raise ValueError("dataset recipe scenarios must belong to scenario_families")
        if len({cohort.cohort_id for cohort in self.cohorts}) != len(self.cohorts):
            raise ValueError("duplicate cohort IDs are not permitted")
        if len({control.control_id for control in self.scripted_controls}) != len(self.scripted_controls):
            raise ValueError("duplicate scripted control IDs are not permitted")
        cohort_ids = {cohort.cohort_id for cohort in self.cohorts}
        if any(control.cohort_id not in cohort_ids for control in self.scripted_controls):
            raise ValueError("every scripted control must reference a known cohort")
        counts = Counter(cohort.scenario for cohort in self.cohorts)
        if counts != Counter({scenario: self.cohorts_per_family for scenario in self.scenario_families}):
            raise ValueError("cohorts must match cohorts_per_family for every scenario")
        assigned_seeds: set[int] = set()
        for cohort in self.cohorts:
            recipe = recipes.get(cohort.recipe_id)
            if recipe is None or recipe.scenario != cohort.scenario:
                raise ValueError("each cohort must link to a recipe for its scenario")
            if recipe.split != "evaluation":
                raise ValueError("scheduled cohorts must use evaluation recipes")
            if cohort.generation_seed is not None:
                if recipe.source_kind != "synthetic":
                    raise ValueError("public cohorts cannot have generation seeds")
                if cohort.generation_seed in assigned_seeds:
                    raise ValueError("generation seeds must be distinct across proposed cohorts")
                assigned_seeds.add(cohort.generation_seed)
        return self

    def sha256(self) -> str:
        """Identity of the entire immutable plan, including scheduling choices."""
        return content_sha256(self.model_dump(mode="json"))


def _blinded_identifier(plan: StudyPlan, namespace: str, value: Any) -> str:
    """Domain-separated opaque ID requiring coordinator-only secret metadata."""
    return hmac.new(bytes.fromhex(plan.blinding_nonce),
                    canonical_json_bytes({"namespace": namespace, "value": value}),
                    hashlib.sha256).hexdigest()


def task_identity(plan: StudyPlan, cohort: CohortPlan) -> str:
    """Blind the common proposed task independently of arm and schedule seed."""
    recipe = next(item for item in plan.dataset_recipes if item.recipe_id == cohort.recipe_id)
    return _blinded_identifier(plan, "task", {
        "record_format": "devbench.study.task/1", "study_id": plan.study_id,
        "cohort": cohort.model_dump(mode="json"), "recipe": recipe.model_dump(mode="json"),
        "question_ref": plan.scientific_question_ref, "answer_schema_ref": plan.answer_schema_ref})


def control_attempt_id(plan: StudyPlan, control: ScriptedControlPlan) -> str:
    """Bind an explicitly registered control to an ID outside model attempt slots."""
    if control not in plan.scripted_controls:
        raise ValueError("scripted control must be explicitly registered in this plan")
    identity = _blinded_identifier(plan, "scripted-control-attempt", {
        "plan_identity": plan.sha256(), "control": control.model_dump(mode="json")})
    return f"control-attempt-{identity}"


class AttemptPlan(FrozenContract):
    """One scheduled participant attempt; contains coordinator-only scenario labels."""

    attempt_id: Identifier
    cohort_id: Identifier
    scenario: Scenario
    condition: Condition
    actor_type: Literal["model_participant"] = "model_participant"
    repetition: Annotated[int, Field(ge=1, le=100)]
    execution_order: Annotated[int, Field(ge=1)]
    recipe_id: Identifier
    plan_identity: Sha256
    task_identity: Sha256
    status: Literal["scheduled_not_run"] = "scheduled_not_run"

    def participant_projection(self, plan: StudyPlan) -> dict[str, Any]:
        """Project only shared public inputs; no seeds, truth, thresholds or arm labels."""
        if self.plan_identity != plan.sha256():
            raise ValueError("attempt belongs to a different study plan")
        cohort = next((item for item in plan.cohorts if item.cohort_id == self.cohort_id), None)
        if cohort is None or cohort.recipe_id != self.recipe_id or cohort.scenario != self.scenario:
            raise ValueError("attempt has an invalid cohort binding")
        if self.task_identity != task_identity(plan, cohort):
            raise ValueError("attempt has an invalid task identity")
        return {
            "record_format": "devbench.study.participant/1",
            "task_id": self.task_identity,
            "cohort_id": _blinded_identifier(plan, "cohort", {
                "cohort_id": cohort.cohort_id, "study_id": plan.study_id}),
            "question_ref": plan.scientific_question_ref,
            "answer_schema_ref": plan.answer_schema_ref,
            "budget": plan.budgets.model_dump(mode="json"),
        }
