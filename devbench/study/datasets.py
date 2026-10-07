"""Coordinator-only cohort preparation, with no data or model work on import.

Only explicitly authorized calls may generate synthetic data or import bounded
curator-prepared public subsets. Public hash verification proves byte identity,
not source authenticity, biological independence or complete scientific truth.
"""

from __future__ import annotations

import hashlib
import json
import csv
import io
import math
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from . import data
from .contracts import DatasetRecipe, FrozenContract, Identifier, Seed, Sha256, StudyPlan, canonical_json_bytes, content_sha256
from .permissions import require_protected_storage
from .zarr_bounds import MAX_GENES, is_prepared_zarr_member, validate_prepared_zarr


MAX_COHORTS = 1000
MAX_STUDY_INPUT_BYTES = 2_000_000_000
MAX_MANIFEST_BYTES = 10_000_000
MAX_SOURCE_BYTES = 2_000_000


def validate_data_preparation(plan: StudyPlan) -> StudyPlan:
    """Freeze and validate all cohort assignments without touching data or paths."""
    if not isinstance(plan, StudyPlan):
        raise TypeError("plan must be a StudyPlan")
    frozen = StudyPlan.model_validate(plan.model_dump())
    recipes = {recipe.recipe_id: recipe for recipe in frozen.dataset_recipes}
    public = [cohort.cohort_id for cohort in frozen.cohorts if recipes[cohort.recipe_id].source_kind != "synthetic"]
    if public:
        raise ValueError("public cohorts require the separate retained-hash-verified import_public_subset path: " + ", ".join(public))
    missing = [cohort.cohort_id for cohort in frozen.cohorts if cohort.generation_seed is None]
    if missing:
        raise ValueError("Missing explicit generation seeds for cohorts: " + ", ".join(missing))
    if len(frozen.cohorts) > MAX_COHORTS:
        raise ValueError("Study preparation exceeds the cohort-count budget")
    total_matrix_bytes = 0
    for cohort in frozen.cohorts:
        recipe = data.validate_synthetic_recipe(recipes[cohort.recipe_id])
        if recipe.num_genes > MAX_GENES:
            raise ValueError("Prepared study cohorts must fit the executor's 5000-gene limit")
        total_matrix_bytes += recipe.num_genes * recipe.sample_count * 8
    if total_matrix_bytes > MAX_STUDY_INPUT_BYTES:
        raise ValueError("Planned expression matrices exceed the study input-byte budget")
    if len(canonical_json_bytes(frozen.model_dump(mode="json"))) > MAX_MANIFEST_BYTES:
        raise ValueError("Plan exceeds its bounded coordinator JSON budget")
    return frozen


def synthetic_independence_id(generator_implementation_sha256: str, generation_seed: int) -> str:
    """Pure cluster identity; names and locations cannot disguise seed reuse."""
    if (not isinstance(generator_implementation_sha256, str) or len(generator_implementation_sha256) != 64
            or any(character not in "0123456789abcdef" for character in generator_implementation_sha256)):
        raise ValueError("Generator implementation identity must be a SHA-256 digest")
    if type(generation_seed) is not int or not 0 <= generation_seed <= 2**63 - 1:
        raise ValueError("Independence identity requires an explicit valid generation seed")
    return "synthetic-" + content_sha256({"generator_implementation_sha256": generator_implementation_sha256,
                                         "generation_seed": generation_seed})


class PreparedCohortRecord(FrozenContract):
    """Exact JSON projection of the executor's PreparedCohortBundle fields."""

    root: Annotated[str, Field(min_length=1, max_length=4096)]
    input_sha256: Sha256
    truth_sha256: Sha256
    recipe_id: Identifier
    generation_seed: Seed
    independence_id: Identifier
    unit_provenance_verified: Literal[True]

    @field_validator("unit_provenance_verified", mode="before")
    @classmethod
    def strict_verified_flag(cls, value: Any) -> bool:
        if value is not True:
            raise ValueError("Fresh synthetic unit provenance requires an explicit boolean true")
        return value

    @model_validator(mode="after")
    def absolute_root(self) -> PreparedCohortRecord:
        path = PurePosixPath(self.root)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Prepared cohort root must be absolute and traversal-free")
        return self


class CohortPreparationReceipt(FrozenContract):
    input_manifest_sha256: Sha256
    input_bytes: Annotated[int, Field(ge=1, le=data.MAX_OUTPUT_BYTES)]
    input_file_count: Annotated[int, Field(ge=1, le=20_000)]
    generator_implementation_sha256: Sha256
    unit_provenance_scope: Literal["fresh_synthetic_generator_identity"]
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def consistent_hash(self) -> CohortPreparationReceipt:
        if self.receipt_sha256 != content_sha256(self.model_dump(mode="json", exclude={"receipt_sha256"})):
            raise ValueError("Cohort receipt hash does not match its fields")
        return self


class CoordinatorManifest(FrozenContract):
    """Private preparation receipt. Never include this file in participant inputs."""

    record_format: Literal["devbench.study.prepared-data/1"]
    plan_identity: Sha256
    plan_path: Literal["study_plan.json"]
    status: Literal["data_prepared_models_not_run"]
    cohorts: Annotated[dict[Identifier, PreparedCohortRecord], Field(min_length=1, max_length=MAX_COHORTS)]
    receipts: Annotated[dict[Identifier, CohortPreparationReceipt], Field(min_length=1, max_length=MAX_COHORTS)]

    @model_validator(mode="after")
    def complete_distinct_receipts(self) -> CoordinatorManifest:
        if self.cohorts.keys() != self.receipts.keys():
            raise ValueError("Every prepared cohort must have exactly one private receipt")
        for attribute in ("root", "input_sha256", "independence_id"):
            if len({getattr(record, attribute) for record in self.cohorts.values()}) != len(self.cohorts):
                raise ValueError("Prepared cohorts must have distinct " + attribute)
        if sum(receipt.input_bytes for receipt in self.receipts.values()) > MAX_STUDY_INPUT_BYTES:
            raise ValueError("Prepared study exceeds its input-byte budget")
        for cohort_id, cohort in self.cohorts.items():
            expected = synthetic_independence_id(self.receipts[cohort_id].generator_implementation_sha256,
                                                 cohort.generation_seed)
            if cohort.independence_id != expected:
                raise ValueError("Cohort independence identity does not match its generator and seed")
        return self


class InputFileReceipt(FrozenContract):
    sha256: Sha256
    bytes: Annotated[int, Field(ge=0, le=data.MAX_OUTPUT_BYTES)]


class PreparedInputManifest(data.SyntheticCurveReference):
    record_format: Literal["devbench.study.input_manifest/1"]
    files: Annotated[dict[str, InputFileReceipt], Field(min_length=1, max_length=20_000)]
    input_sha256: Sha256
    hash_scope: Literal["input-relative-paths-bytes-and-sha256"]
    measurement_type: Literal["raw_counts"]

    @model_validator(mode="after")
    def bounded_input_paths(self) -> PreparedInputManifest:
        for name in self.files:
            path = PurePosixPath(name)
            if (len(name) > 512 or path.is_absolute() or ".." in path.parts or "\\" in name
                    or not path.parts or path.parts[0] != "inputs" or path.as_posix() != name):
                raise ValueError("Manifest file paths must be canonical relative paths beneath inputs/")
        required = {"inputs/metadata.csv", "inputs/task_inputs.json"}
        if not required.issubset(self.files) or not any(name.startswith("inputs/counts.zarr/") for name in self.files):
            raise ValueError("Prepared input manifest must include metadata, task inputs and a counts store")
        if sum(record.bytes for record in self.files.values()) > data.MAX_OUTPUT_BYTES:
            raise ValueError("Prepared inputs exceed the cohort-byte budget")
        files = {name: record.model_dump(mode="json") for name, record in self.files.items()}
        if self.input_sha256 != content_sha256(files):
            raise ValueError("Input manifest identity does not match the declared file receipts")
        return self


def _new_root(output_dir: Path) -> Path:
    path = Path(output_dir)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("output_dir must be an absolute path without traversal")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("Prepared data paths cannot contain symlinks")
    root = require_protected_storage(path)
    if root.exists():
        raise FileExistsError("Prepared study destination must be a new directory")
    if not root.parent.is_dir():
        raise ValueError("Prepared study parent must already exist and be coordinator-controlled")
    return root


def _hash_file(path: Path, limit: int) -> tuple[str, int]:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Receipt inputs must be absolute regular files without symlinks")
    path = path.resolve()
    if not path.is_file():
        raise ValueError("Receipt input is not a regular file")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while block := handle.read(min(1_048_576, limit - total + 1)):
            total += len(block)
            if total > limit:
                raise ValueError("Receipt input exceeds the bounded read budget")
            digest.update(block)
    return digest.hexdigest(), total


def _read_json(path: Path, limit: int) -> tuple[dict[str, Any], str]:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Coordinator JSON must use an absolute nonsymlink path")
    with path.resolve().open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Coordinator JSON exceeds its bounded read budget")

    def invalid_constant(value: str) -> None:
        raise ValueError("Coordinator JSON cannot contain nonfinite constants")

    value = json.loads(content, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("Coordinator JSON must be an object")
    return value, hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    content = canonical_json_bytes(value)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("Coordinator JSON exceeds its output-byte budget")
    with path.resolve().open("xb") as handle:
        handle.write(content)


def _generator_implementation_sha256() -> str:
    study = Path(__file__).resolve().parent
    source_paths = {"generator": study / "data.py", "contracts": study / "contracts.py",
                    "storage": study.parent.parent / "devharness_core" / "storage.py"}
    return content_sha256({name: _hash_file(path.resolve(), MAX_SOURCE_BYTES)[0]
                           for name, path in source_paths.items()})


def _verify_fresh_bundle(root: Path, recipe: Any, generation_seed: int,
                         implementation_sha256: str) -> tuple[PreparedCohortRecord, CohortPreparationReceipt]:
    inputs_root = root / "inputs"
    private_root = root / "private"
    if any(path.is_symlink() or not path.is_dir() for path in (root, inputs_root, private_root)):
        raise ValueError("Fresh coordinator bundle directories must be nonsymlink directories")
    manifest_document, manifest_sha256 = _read_json(root / "input_manifest.json", MAX_MANIFEST_BYTES)
    manifest = PreparedInputManifest.model_validate(manifest_document)
    actual_files = set()
    for path in inputs_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Generated inputs contain an unexpected symlink")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
        if len(actual_files) > 20_000:
            raise ValueError("Generated inputs exceed the file-count budget")
    if actual_files != manifest.files.keys():
        raise ValueError("Generated input files disagree with the declared manifest")
    for name, expected in manifest.files.items():
        actual_hash, actual_bytes = _hash_file(root / name, expected.bytes)
        if actual_hash != expected.sha256 or actual_bytes != expected.bytes:
            raise ValueError("Generated input file does not match its manifest receipt")
    truth, truth_sha256 = _read_json(private_root / "truth.json", data.MAX_TRUTH_BYTES)
    if (truth.get("record_format") != "devbench.study.truth/1" or truth.get("generation_seed") != generation_seed
            or truth.get("recipe") != recipe.model_dump(mode="json")
            or truth.get("population_identifiability", {}).get("independent_unit_count") != recipe.independent_unit_count):
        raise ValueError("Fresh synthetic truth does not match the assigned recipe, seed and known unit structure")
    record = PreparedCohortRecord(root=str(root), input_sha256=manifest.input_sha256, truth_sha256=truth_sha256,
        recipe_id=recipe.recipe_id, generation_seed=generation_seed,
        independence_id=synthetic_independence_id(implementation_sha256, generation_seed), unit_provenance_verified=True)
    fields = {"input_manifest_sha256": manifest_sha256,
              "input_bytes": sum(item.bytes for item in manifest.files.values()),
              "input_file_count": len(manifest.files), "generator_implementation_sha256": implementation_sha256,
              "unit_provenance_scope": "fresh_synthetic_generator_identity"}
    receipt = CohortPreparationReceipt(**fields, receipt_sha256=content_sha256(fields))
    return record, receipt


def prepare_study_data(plan: StudyPlan, output_dir: Path, *, allow_data_execution: bool = False) -> dict[str, Any]:
    """Materialize fresh synthetic cohorts only after literal explicit opt-in.

All cohort seeds and recipes are checked before any filesystem operation. The
new root, study_plan.json, coordinator_manifest.json and private truth remain
coordinator-only. Failed partial directories are retained and never reused.
Storage must be outside platform scratch and under a coordinator-controlled
parent; these checks do not defeat adversarial concurrent ancestor replacement.
No model, tool adapter, download or public-to-synthetic fallback is invoked.
"""
    if allow_data_execution is not True:
        raise data.DataExecutionDisabled("Study data preparation requires explicit allow_data_execution=True")
    frozen = validate_data_preparation(plan)
    plan_identity = frozen.sha256()
    root = _new_root(output_dir)
    implementation_sha256 = _generator_implementation_sha256()
    root.mkdir(mode=0o700)
    cohorts_root = (root / "cohorts").resolve()
    cohorts_root.mkdir(mode=0o700)
    _write_json((root / "study_plan.json").resolve(), frozen.model_dump(mode="json"))
    recipes = {recipe.recipe_id: recipe for recipe in frozen.dataset_recipes}
    cohorts: dict[str, PreparedCohortRecord] = {}
    receipts: dict[str, CohortPreparationReceipt] = {}
    input_bytes = 0
    for cohort in frozen.cohorts:
        recipe = recipes[cohort.recipe_id]
        cohort_root = (cohorts_root / cohort.cohort_id).resolve()
        data.prepare_dataset(recipe, cohort_root, cohort.generation_seed, allow_data_execution=True)
        record, receipt = _verify_fresh_bundle(cohort_root, recipe, cohort.generation_seed, implementation_sha256)
        input_bytes += receipt.input_bytes
        if input_bytes > MAX_STUDY_INPUT_BYTES:
            raise ValueError("Prepared study exceeds the input-byte budget; partial outputs retained")
        cohorts[cohort.cohort_id] = record
        receipts[cohort.cohort_id] = receipt
        _write_json((cohort_root / "private" / "preparation_receipt.json").resolve(), {
            "record_format": "devbench.study.cohort-preparation/1", "plan_identity": plan_identity,
            "cohort_id": cohort.cohort_id, "bundle": record.model_dump(mode="json"),
            "receipt": receipt.model_dump(mode="json")})
    if _generator_implementation_sha256() != implementation_sha256:
        raise ValueError("Generator implementation changed during preparation; partial outputs retained")
    manifest = CoordinatorManifest(record_format="devbench.study.prepared-data/1", plan_identity=plan_identity,
        plan_path="study_plan.json", status="data_prepared_models_not_run", cohorts=cohorts, receipts=receipts)
    manifest_path = (root / "coordinator_manifest.json").resolve()
    _write_json(manifest_path, manifest.model_dump(mode="json"))
    return {"coordinator_manifest_path": str(manifest_path),
            "coordinator_manifest_sha256": content_sha256(manifest.model_dump(mode="json")), "plan_identity": plan_identity,
            "cohorts": {key: value.model_dump(mode="json") for key, value in cohorts.items()}}


PublicReferenceScope = Literal["observational_expression", "mixture_and_spikein_controls", "perturbation_response"]
PublicLabel = Annotated[str, Field(min_length=1, max_length=200)]
FiniteReferenceNumber = Annotated[float, Field(allow_inf_nan=False)]


class PublicImportProvenance(FrozenContract):
    """Retained digests must come from the coordinator, never from this import's reads.

The source hash scope is the curator-prepared subset's input-file manifest;
it does not identify or authenticate the original full accession download.
"""

    record_format: Literal["devbench.study.public-import-provenance/1"]
    public_manifest: data.PublicManifest
    retained_input_sha256: Sha256
    selection_description: Annotated[str, Field(min_length=1, max_length=2000)]
    retained_reference_sha256: Sha256 | None = None
    curator_reference_scope: PublicReferenceScope | None = None

    @model_validator(mode="after")
    def independently_retained_hashes(self) -> PublicImportProvenance:
        if self.public_manifest.input_sha256 != self.retained_input_sha256:
            raise ValueError("Declared subset hash must match the independently retained input digest")
        if (self.retained_reference_sha256 is None) != (self.curator_reference_scope is None):
            raise ValueError("A curator reference requires both a retained hash and a declared limited scope")
        if self.curator_reference_scope is not None and self.curator_reference_scope != self.public_manifest.reference_scope:
            raise ValueError("Curator reference scope must match the declared public source scope")
        return self


class PublicSubsetManifest(PreparedInputManifest):
    measurement_type: Literal["raw_counts", "continuous"]
    reference_batch: None = None
    library_factor: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None
    batch_reference_policy: Literal["observed_batch_mix"] = "observed_batch_mix"
    biological_unit_reference: Literal["observed_subset"] = "observed_subset"

    @model_validator(mode="after")
    def participant_file_scope(self) -> PublicSubsetManifest:
        prefix = "inputs/counts.zarr/"
        if any(name not in {"inputs/metadata.csv", "inputs/task_inputs.json"}
               and (not name.startswith(prefix) or not is_prepared_zarr_member(name[len(prefix):])) for name in self.files):
            raise ValueError("Public input sidecars are unsupported; curator references must remain outside inputs/")
        return self


class PublicSubsetInputs(FrozenContract):
    """Whitelisted source metadata. Supplied prompts/instructions are not reused."""

    measurement_type: Literal["raw_counts", "continuous"]
    time_unit: PublicLabel
    time_col: PublicLabel = "numeric_time"
    biological_unit_column: PublicLabel = "biological_unit_id"
    gene_ids: Annotated[list[PublicLabel], Field(min_length=1, max_length=MAX_GENES)]
    reference_genes: Annotated[list[PublicLabel], Field(max_length=MAX_GENES)] = []

    @model_validator(mode="after")
    def fixed_universe(self) -> PublicSubsetInputs:
        if len(set(self.gene_ids)) != len(self.gene_ids):
            raise ValueError("Public subset gene universe must be distinct")
        if not set(self.reference_genes) <= set(self.gene_ids) or len(set(self.reference_genes)) != len(self.reference_genes):
            raise ValueError("Declared public positive controls must be distinct members of the public gene universe")
        return self


class PublicGeneReference(FrozenContract):
    """Optional curator-provided descriptive estimates, never latent null labels."""

    effect: FiniteReferenceNumber | None = None
    effect_units: Literal["log2_normalized_mean_change", "expression_difference"] | None = None
    predicted_curve: Annotated[list[FiniteReferenceNumber], Field(max_length=100)] = []

    @model_validator(mode="after")
    def paired_effect_units(self) -> PublicGeneReference:
        if (self.effect is None) != (self.effect_units is None):
            raise ValueError("Curator effect and units must be supplied together")
        return self


class PublicObservationCurveReference(FrozenContract):
    reference_batch: None = None
    library_factor: Annotated[float, Field(gt=0.0, allow_inf_nan=False)] | None
    batch_reference_policy: Literal["observed_batch_mix"] = "observed_batch_mix"
    biological_unit_reference: Literal["observed_subset"] = "observed_subset"


class LimitedPublicReference(FrozenContract):
    """Limited retained public reference; unsupported scientific targets are absent."""

    record_format: Literal["devbench.study.public-reference/1"]
    reference_scope: PublicReferenceScope
    available_reference_metrics: list[Literal["effect_mae", "effect_rmse", "curve_rmse"]]
    genes: Annotated[dict[PublicLabel, PublicGeneReference], Field(max_length=100_000)]
    time_grid: Annotated[list[FiniteReferenceNumber], Field(max_length=100)] = []
    curve_reference: PublicObservationCurveReference | None = None
    complete_causal_truth: Literal[False] = False
    complete_pseudotime_truth: Literal[False] = False
    diagnostic_truth_adjudicated: Literal[False] = False

    @field_validator("complete_causal_truth", "complete_pseudotime_truth", "diagnostic_truth_adjudicated", mode="before")
    @classmethod
    def no_adjudicated_truth(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("Public references cannot claim complete or adjudicated latent truth")
        return value

    @model_validator(mode="after")
    def descriptive_reference_only(self) -> LimitedPublicReference:
        if self.time_grid and (len(self.time_grid) < 3 or any(b <= a for a, b in zip(self.time_grid, self.time_grid[1:]))):
            raise ValueError("Reference coordinates must be bounded, increasing and explicit")
        if len(set(self.available_reference_metrics)) != len(self.available_reference_metrics):
            raise ValueError("Reference metric declarations must be distinct")
        if any(gene.predicted_curve and len(gene.predicted_curve) != len(self.time_grid) for gene in self.genes.values()):
            raise ValueError("Curator curves must match the declared reference grid")
        if self.available_reference_metrics and (not self.genes or self.curve_reference is None):
            raise ValueError("Descriptive reference metrics require supplied estimates and their observation reference")
        if ({"effect_mae", "effect_rmse"} & set(self.available_reference_metrics)
                and not any(gene.effect is not None for gene in self.genes.values())):
            raise ValueError("Effect metrics require at least one supplied curator effect")
        if "curve_rmse" in self.available_reference_metrics and not any(gene.predicted_curve for gene in self.genes.values()):
            raise ValueError("Curve metrics require at least one supplied curator curve")
        return self


class PreparedPublicCohortRecord(FrozenContract):
    """Executor-compatible public record, without biological-unit certification."""

    root: Annotated[str, Field(min_length=1, max_length=4096)]
    input_sha256: Sha256
    truth_sha256: Sha256
    recipe_id: Identifier
    generation_seed: None = None
    independence_id: Identifier
    unit_provenance_verified: Literal[False] = False

    @field_validator("unit_provenance_verified", mode="before")
    @classmethod
    def unverified_units(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("Imported public unit identifiers are not independently verified")
        return value

    @model_validator(mode="after")
    def absolute_root(self) -> PreparedPublicCohortRecord:
        path = PurePosixPath(self.root)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Prepared public root must be absolute and traversal-free")
        return self


def validate_public_import(recipe: DatasetRecipe, retained_provenance: PublicImportProvenance | dict[str, Any]) -> PublicImportProvenance:
    """Pure admission check; supplied provenance is not self-certified authenticity."""
    recipe = DatasetRecipe.model_validate(recipe.model_dump())
    if recipe.source_kind != "public_processed":
        raise ValueError("Public import requires a public_processed recipe and never substitutes synthetic data")
    if recipe.num_genes > MAX_GENES:
        raise ValueError("Public import must fit the executor's 5000-gene limit")
    if recipe.sample_count * 1024 > data.MAX_METADATA_BYTES:
        raise ValueError("Public subset exceeds the bounded metadata/sample budget")
    payload = retained_provenance.model_dump() if isinstance(retained_provenance, PublicImportProvenance) else retained_provenance
    provenance = PublicImportProvenance.model_validate(payload)
    if (provenance.public_manifest.source_id != recipe.public_accession
            or provenance.public_manifest.measurement_type != recipe.measurement_type):
        raise ValueError("Public source and measurement declarations must match the assigned recipe")
    if recipe.source_sha256 is not None and recipe.source_sha256 != provenance.retained_input_sha256:
        raise ValueError("Recipe source hash must identify the same retained prepared-subset input manifest")
    return provenance


def public_observation_truth(recipe: DatasetRecipe, gene_ids: list[str], hypothesis_family_id: str,
                             provenance: PublicImportProvenance, reference: LimitedPublicReference | None = None) -> dict[str, Any]:
    """Build a public reference record, keeping absent latent truth explicitly unknown."""
    if not gene_ids or len(gene_ids) > 100_000 or len(set(gene_ids)) != len(gene_ids):
        raise ValueError("Public observation reference requires a bounded distinct gene universe")
    truth = {
        "record_format": "devbench.study.truth/1", "source_kind": "public_processed",
        "recipe": recipe.model_dump(mode="json"), "gene_ids": gene_ids, "genes": {},
        "target": "descriptive_public_observation", "hypothesis_family_id": hypothesis_family_id,
        "latent_truth_scope": "none", "diagnostic_truth_adjudicated": False, "expected_diagnostics": [],
        "population_identifiability": {"identifiable": None, "reasons": ["Biological-unit provenance is unverified"]},
        "population_inference_permitted": False, "permitted_claim_scopes": ["descriptive"],
        "unit_provenance_verified": False, "available_reference_metrics": [],
        "observation_estimand": "Arithmetic library means within each supplied coordinate, retaining the observed batch mixture",
        "source_reference_scope": provenance.public_manifest.reference_scope,
        "unknown_truth_scopes": ["latent_gene_programs", "population_independence", "diagnostic_adjudication",
                                 "complete_causal_truth", "complete_pseudotime_truth"],
    }
    if reference is not None:
        if provenance.retained_reference_sha256 is None or reference.reference_scope != provenance.curator_reference_scope:
            raise ValueError("Limited reference needs independently retained hash and matching declared scope")
        if set(reference.genes) - set(gene_ids):
            raise ValueError("Curator reference genes must belong to the public input universe")
        truth.update(genes={gene: row.model_dump(mode="json", exclude_none=True) for gene, row in reference.genes.items()},
                     available_reference_metrics=reference.available_reference_metrics,
                     time_grid=reference.time_grid,
                     curve_reference=reference.curve_reference.model_dump(mode="json") if reference.curve_reference is not None else None,
                     retained_reference_sha256=provenance.retained_reference_sha256)
    return truth


class PublicImportReceipt(FrozenContract):
    """Actual byte checks coexist with explicitly unverified scientific provenance."""

    record_format: Literal["devbench.study.public-import-receipt/1"]
    source_kind: Literal["public_processed"]
    retained_input_sha256: Sha256
    source_input_manifest_sha256: Sha256
    imported_input_sha256: Sha256
    imported_input_manifest_sha256: Sha256
    input_bytes: Annotated[int, Field(ge=1, le=data.MAX_OUTPUT_BYTES)]
    prepared_subset_bytes_verified: Literal[True]
    stored_measurement_values_checked: Literal[True]
    source_authenticity_verified: Literal[False]
    original_accession_bytes_verified: Literal[False]
    biological_units_verified: Literal[False]
    population_inference_permitted: Literal[False]
    source_hash_scope: Literal["curator_prepared_subset_input_manifest"]
    public_manifest: data.PublicManifest
    selection_description: Annotated[str, Field(min_length=1, max_length=2000)]
    retained_reference_sha256: Sha256 | None
    curator_reference_bytes_verified: bool
    curator_reference_scope: PublicReferenceScope | None

    @model_validator(mode="after")
    def limited_verification(self) -> PublicImportReceipt:
        if self.public_manifest.input_sha256 != self.retained_input_sha256:
            raise ValueError("Receipt must identify the same externally retained prepared subset")
        if self.curator_reference_bytes_verified != (self.retained_reference_sha256 is not None):
            raise ValueError("Reference byte verification requires its separately retained digest")
        return self


def _existing_public_root(input_bundle: Path) -> Path:
    path = Path(input_bundle)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Prepared public input bundle must use an absolute traversal-free path")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("Prepared public input path cannot contain symlinks")
    root = require_protected_storage(path)
    if not root.is_dir() or (root / "inputs").is_symlink() or not (root / "inputs").is_dir():
        raise ValueError("Prepared public bundle requires an existing nonsymlink inputs directory")
    return root


def _verify_public_files(root: Path, manifest: PublicSubsetManifest) -> None:
    actual_files = set()
    for path in (root / "inputs").rglob("*"):
        if path.is_symlink():
            raise ValueError("Prepared public inputs must not contain symlinks")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
        if len(actual_files) > 20_000:
            raise ValueError("Prepared public input file count exceeds its budget")
    if actual_files != manifest.files.keys():
        raise ValueError("Prepared public files disagree with the retained manifest")
    for name, expected in manifest.files.items():
        actual_hash, actual_bytes = _hash_file(root / name, expected.bytes)
        if (actual_hash, actual_bytes) != (expected.sha256, expected.bytes):
            raise ValueError("Prepared public input bytes do not match the externally retained identity")


def requires_nonnegative_expression(measurement_type: str, expression_units: str) -> bool:
    """Pure measurement-domain policy; generic normalized expression may be signed."""
    return measurement_type == "raw_counts" or expression_units in {"RPKM", "TPM"}


def parse_bounded_csv_records(content: bytes, *, required_columns: set[str], identifier_column: str,
                               max_rows: int) -> list[dict[str, str]]:
    """Parse text without duplicate-header or ragged-row ambiguity."""
    if not isinstance(content, bytes) or len(content) > 5_000_000 or type(max_rows) is not int or max_rows < 1:
        raise ValueError("CSV text exceeds its bounded contract")
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")), strict=True)
    try:
        fields = reader.fieldnames or []
    except csv.Error as error:
        raise ValueError("Malformed CSV header quoting") from error
    if (not fields or len(set(fields)) != len(fields) or any(not name.strip() for name in fields)
            or not (set(required_columns) | {identifier_column}) <= set(fields)):
        raise ValueError("CSV headers must be distinct, nonempty and include required columns")
    rows = []
    identifiers = set()
    try:
        for row in reader:
            if (len(rows) >= max_rows or None in row or any(value is None for value in row.values())
                    or any(not row[name].strip() for name in required_columns | {identifier_column})):
                raise ValueError("CSV rows must have exact field counts and nonempty required values")
            identifier = row[identifier_column]
            if identifier in identifiers:
                raise ValueError("CSV record identifiers must be distinct")
            identifiers.add(identifier)
            rows.append(row)
    except csv.Error as error:
        raise ValueError("Malformed CSV record quoting") from error
    return rows


def _validate_public_observations(root: Path, recipe: DatasetRecipe, source_inputs: PublicSubsetInputs,
                                  expression_units: str) -> list[float]:
    """Validate only a hash-verified, freshly copied coordinator-owned snapshot."""
    expression_root = root / "inputs" / "counts.zarr"
    validate_prepared_zarr(expression_root, expected_shape=(recipe.num_genes, recipe.sample_count),
        measurement_type=recipe.measurement_type, max_chunk_bytes=4 * 1024 * 1024,
        max_metadata_bytes=5_000_000, max_matrix_bytes=recipe.max_matrix_bytes)
    metadata_path = root / "inputs" / "metadata.csv"
    with metadata_path.open("rb") as handle:
        content = handle.read(5_000_001)
    required = {"sample_id", "batch", source_inputs.time_col, source_inputs.biological_unit_column}
    rows = parse_bounded_csv_records(content, required_columns=required, identifier_column="sample_id",
                                    max_rows=recipe.sample_count)
    coordinates = set()
    for row in rows:
        coordinate = float(row[source_inputs.time_col])
        if not math.isfinite(coordinate):
            raise ValueError("Public coordinate declarations must be finite")
        coordinates.add(coordinate)
    if len(rows) != recipe.sample_count or len(coordinates) != recipe.num_timepoints:
        raise ValueError("Public sample and coordinate counts must match the configured bounded subset")
    import numpy as np
    from devharness_core.storage import ChunkedExpressionStore

    with ChunkedExpressionStore(expression_root) as store:
        if store.gene_names != source_inputs.gene_ids or store.sample_names != [row["sample_id"] for row in rows]:
            raise ValueError("Stored public labels must match the declared gene universe and metadata order")
        rows_per_chunk = max(1, 1_000_000 // (8 * recipe.sample_count))
        for _, values in store.iter_gene_chunks(chunk_size=rows_per_chunk):
            if not np.isfinite(values).all() or (requires_nonnegative_expression(recipe.measurement_type, expression_units) and np.any(values < 0)):
                raise ValueError("Public expression contains invalid values for its declared measurement type")
    return sorted(coordinates)


def _copy_verified_public_file(source: Path, destination: Path, receipt: InputFileReceipt) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    with source.open("rb") as reader, destination.open("xb") as writer:
        while block := reader.read(min(1_048_576, receipt.bytes - count + 1)):
            count += len(block)
            if count > receipt.bytes:
                raise ValueError("Source bytes changed during public import; partial output retained")
            digest.update(block)
            writer.write(block)
    if count != receipt.bytes or digest.hexdigest() != receipt.sha256:
        raise ValueError("Source bytes changed during public import; partial output retained")


def import_public_subset(plan: StudyPlan, cohort_id: str, input_bundle: Path, output_dir: Path,
                         retained_provenance: PublicImportProvenance | dict[str, Any], *,
                         allow_data_execution: bool = False) -> dict[str, Any]:
    """Import one already prepared local public Zarr-v2 subset; never download.

The retained input digest must be supplied independently of this import. It
identifies the prepared subset, not authenticated original accession bytes.
An optional coordinator-only reference.json needs its own retained digest and
declared limited scope. No public biological-unit identifiers are certified.
The returned mapping exactly matches PreparedCohortBundle fields. Private
provenance/observation-reference receipts stay outside participant inputs.
Sources and fresh destinations must be protected, disjoint and coordinator-
controlled; concurrent hostile ancestor replacement is outside this boundary.
"""
    if allow_data_execution is not True:
        raise data.DataExecutionDisabled("Public import requires explicit allow_data_execution=True")
    frozen = StudyPlan.model_validate(plan.model_dump())
    cohort = next((item for item in frozen.cohorts if item.cohort_id == cohort_id), None)
    if cohort is None or cohort.generation_seed is not None:
        raise ValueError("Public import requires a registered cohort without a generation seed")
    recipe = next(item for item in frozen.dataset_recipes if item.recipe_id == cohort.recipe_id)
    provenance = validate_public_import(recipe, retained_provenance)
    source = _existing_public_root(input_bundle)
    root = _new_root(output_dir)
    if root.is_relative_to(source) or source.is_relative_to(root):
        raise ValueError("Public input and destination directories must be disjoint")
    source_document, source_manifest_sha256 = _read_json(source / "input_manifest.json", MAX_MANIFEST_BYTES)
    source_manifest = PublicSubsetManifest.model_validate(source_document)
    if (source_manifest.input_sha256 != provenance.retained_input_sha256
            or source_manifest.measurement_type != recipe.measurement_type):
        raise ValueError("Prepared subset identity/measurement does not match the retained public provenance")
    _verify_public_files(source, source_manifest)
    # Decode only owned immutable copies, never a source checked earlier that
    # could change between frame preflight and the numerical store's open.
    root.mkdir(mode=0o700)
    (root / "private").mkdir(mode=0o700)
    for name, receipt in source_manifest.files.items():
        destination = root / "private" / "source_task_inputs.json" if name == "inputs/task_inputs.json" else root / name
        _copy_verified_public_file(source / name, destination, receipt)
    supplied_inputs, _ = _read_json(root / "private" / "source_task_inputs.json", MAX_MANIFEST_BYTES)
    source_inputs = PublicSubsetInputs.model_validate({key: value for key, value in supplied_inputs.items()
                                                      if key in PublicSubsetInputs.model_fields})
    if source_inputs.measurement_type != recipe.measurement_type or len(source_inputs.gene_ids) != recipe.num_genes:
        raise ValueError("Public task metadata does not match the assigned measurement and gene count")
    time_grid = _validate_public_observations(root, recipe, source_inputs, provenance.public_manifest.expression_units)
    curve_reference = PublicObservationCurveReference(
        library_factor=1.0 if recipe.measurement_type == "raw_counts" else None).model_dump(mode="json")
    reference = None
    if provenance.retained_reference_sha256 is not None:
        reference_hash, reference_bytes = _hash_file(source / "reference.json", MAX_MANIFEST_BYTES)
        if reference_hash != provenance.retained_reference_sha256:
            raise ValueError("Curator reference bytes do not match the independently retained reference digest")
        _copy_verified_public_file(source / "reference.json", root / "private" / "curator_reference.json",
            InputFileReceipt(sha256=reference_hash, bytes=reference_bytes))
        reference_document, _ = _read_json(root / "private" / "curator_reference.json", MAX_MANIFEST_BYTES)
        reference = LimitedPublicReference.model_validate(reference_document)
        if (reference.curve_reference is not None and reference.curve_reference.model_dump(mode="json") != curve_reference
                or reference.time_grid and reference.time_grid != time_grid):
            raise ValueError("Curator reference must match the imported observation coordinate and reference")
        expected_units = "log2_normalized_mean_change" if recipe.measurement_type == "raw_counts" else "expression_difference"
        if any(row.effect_units not in (None, expected_units) for row in reference.genes.values()):
            raise ValueError("Curator reference effect units contradict the public measurement layer")
    hypothesis_family = content_sha256({"gene_ids": source_inputs.gene_ids, "target": "descriptive_public_observation"})
    truth = public_observation_truth(recipe, source_inputs.gene_ids, hypothesis_family, provenance, reference)
    truth.update(time_grid=time_grid, curve_reference=curve_reference)
    task_inputs = {
        "record_format": "devbench.study.task_inputs/1", "measurement_type": recipe.measurement_type,
        "expression_path": "inputs/counts.zarr", "metadata_path": "inputs/metadata.csv", "path_base": "bundle_root",
        "gene_ids": source_inputs.gene_ids, "hypothesis_family_id": hypothesis_family,
        "target": "descriptive_public_observation", "time_grid": time_grid, "time_unit": source_inputs.time_unit,
        "time_col": source_inputs.time_col, "biological_unit_column": source_inputs.biological_unit_column,
        "reference_genes": source_inputs.reference_genes, **curve_reference,
        "source_kind": "public_processed", "source_accession": recipe.public_accession,
        "source_sha256": provenance.retained_input_sha256, "measurement_layer": "public_processed_subset",
        "expression_units": provenance.public_manifest.expression_units,
        "biological_unit_scope": provenance.public_manifest.biological_unit_scope,
        "reference_scope": provenance.public_manifest.reference_scope,
        "available_truth_scopes": ["public_observations"] + (["limited_" + reference.reference_scope] if reference is not None else []),
        "unknown_truth_scopes": truth["unknown_truth_scopes"], "population_inference_permitted": False,
        "unit_provenance_verified": False, "permitted_claim_scopes": ["descriptive"],
        "required_scientific_question": (
            "Describe the supplied public observations across the declared numeric coordinate and measurement units. "
            "This is a curator-prepared subset with verified retained byte identity; original accession authenticity and "
            "biological-unit provenance remain unverified. Restrict claims to descriptive observations of this subset. "
            "Do not claim population inference, p-values, q-values, significance, causal edges, verified pseudotime, "
            "or independent donors based on unique sample or unit labels. Preserve repeated/unit labels as supplied. "
            "For each coordinate, report the arithmetic mean across the observed libraries, retaining that coordinate's "
            "actual observed batch mixture. These means are not batch-adjusted or population effects. "
            "Keep continuous expression on its supplied scale; for raw counts use whole-family RLE size factors with "
            "geometric mean one and disclose the estimator. Do not substitute a fixed-batch or donor-adjusted curve. "
            "Report missing provenance and other observable limitations without treating unknown truth as a negative label."
        ),
    }
    _write_json(root / "inputs" / "task_inputs.json", task_inputs)
    files = {name: receipt.model_dump(mode="json") for name, receipt in source_manifest.files.items()}
    task_hash, task_bytes = _hash_file(root / "inputs" / "task_inputs.json", MAX_MANIFEST_BYTES)
    files["inputs/task_inputs.json"] = {"sha256": task_hash, "bytes": task_bytes}
    output_manifest = PublicSubsetManifest(record_format="devbench.study.input_manifest/1", files=files,
        input_sha256=content_sha256(files), hash_scope="input-relative-paths-bytes-and-sha256",
        measurement_type=recipe.measurement_type, **curve_reference)
    output_document = output_manifest.model_dump(mode="json")
    _write_json(root / "input_manifest.json", output_document)
    _write_json(root / "private" / "truth.json", truth)
    record = PreparedPublicCohortRecord(root=str(root), input_sha256=output_manifest.input_sha256,
        truth_sha256=content_sha256(truth), recipe_id=recipe.recipe_id,
        independence_id="public-" + provenance.retained_input_sha256)
    receipt = PublicImportReceipt(record_format="devbench.study.public-import-receipt/1", source_kind="public_processed",
        retained_input_sha256=provenance.retained_input_sha256, source_input_manifest_sha256=source_manifest_sha256,
        imported_input_sha256=record.input_sha256, imported_input_manifest_sha256=content_sha256(output_document),
        input_bytes=sum(item["bytes"] for item in files.values()), prepared_subset_bytes_verified=True,
        stored_measurement_values_checked=True, source_authenticity_verified=False, original_accession_bytes_verified=False,
        biological_units_verified=False, population_inference_permitted=False,
        source_hash_scope="curator_prepared_subset_input_manifest", public_manifest=provenance.public_manifest,
        selection_description=provenance.selection_description, retained_reference_sha256=provenance.retained_reference_sha256,
        curator_reference_bytes_verified=reference is not None, curator_reference_scope=provenance.curator_reference_scope)
    _write_json(root / "private" / "preparation_receipt.json", {
        "record_format": "devbench.study.cohort-preparation/1", "plan_identity": frozen.sha256(), "cohort_id": cohort_id,
        "bundle": record.model_dump(mode="json"), "receipt": receipt.model_dump(mode="json")})
    return record.model_dump(mode="json")
