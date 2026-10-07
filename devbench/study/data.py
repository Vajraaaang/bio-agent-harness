"""Explicitly gated, uncalibrated study data proposals.

Importing this module, validating recipes, and reading the small shipped source
registry do not acquire expression data or import the scientific runtime.
Generation and bundle writing require a literal ``allow_data_execution=True``.
These generators have not been empirically qualified for this study.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from .contracts import DatasetRecipe, FrozenContract, Identifier, Sha256, canonical_json_bytes


MAX_METADATA_BYTES = 32_000_000
MAX_TRUTH_BYTES = 50_000_000
MAX_REGISTRY_BYTES = 100_000
MAX_OUTPUT_BYTES = 250_000_000
SYNTHETIC_RNG_STREAMS = (
    "layout", "identifiers", "programs", "nuisance", "observations", "reference_controls",
)
METADATA_COLUMNS = (
    "sample_id", "numeric_time", "timepoint_label", "batch", "biological_unit_id",
    "specimen_id", "technical_replicate_id", "measurement_type",
)


class DataExecutionDisabled(PermissionError):
    """No data-side effects are authorized by a plan or an omitted option."""


class SyntheticCurveReference(FrozenContract):
    """Shared prediction reference; biological-unit frailty is marginalized."""

    reference_batch: Identifier
    library_factor: Literal[1.0] = 1.0
    batch_reference_policy: Literal["first_observation"] = "first_observation"
    biological_unit_reference: Literal["population_mean"] = "population_mean"


def reference_control_count(num_genes: int) -> int:
    """Pure control-budget policy depending only on the public gene universe."""
    if type(num_genes) is not int or num_genes < 10:
        raise ValueError("Control selection requires an integer universe of at least 10 genes")
    return min(20, max(1, num_genes // 100))


def _require_data_execution(allowed: bool) -> None:
    if allowed is not True:
        raise DataExecutionDisabled("Data execution requires explicit allow_data_execution=True")


def validate_synthetic_recipe(recipe: DatasetRecipe) -> DatasetRecipe:
    """Pure support/budget check; does not choose a seed or create a dataset.

Partial allocation preserves the recipe's occupied cells and total units. Its
per-cell unit count is the mean allocation, with one unit moved from each
nonfavored batch to a time-associated favored batch, preserving overlap.
"""
    if not isinstance(recipe, DatasetRecipe):
        raise TypeError("recipe must be a DatasetRecipe")
    DatasetRecipe.model_validate(recipe.model_dump())
    if recipe.source_kind != "synthetic":
        raise ValueError("Public sources require separately verified local ingestion; no fallback is provided")
    if recipe.source_sha256 is not None:
        raise ValueError("Unmaterialized synthetic recipes cannot claim source_sha256")
    if recipe.num_genes < 10:
        raise ValueError("These program proportions require at least 10 genes")
    if recipe.layout == "partial" and (recipe.num_batches < 2 or recipe.biological_units_per_cell < 2):
        raise ValueError("partial requires at least two batches and two mean biological units per cell")
    if recipe.sample_count * 1024 > MAX_METADATA_BYTES:
        raise ValueError("Planned metadata exceeds the bounded metadata budget")
    truth_upper_bound = (recipe.num_genes * ((recipe.num_timepoints + recipe.num_batches) * 24 + 1500)
                         + recipe.sample_count * 32)
    if truth_upper_bound > MAX_TRUTH_BYTES:
        raise ValueError("Planned private truth exceeds the bounded JSON budget")
    return recipe


def _validate_seed(generation_seed: int) -> None:
    if type(generation_seed) is not int or not 0 <= generation_seed <= 2**63 - 1:
        raise ValueError("generation_seed must be an explicit integer in [0, 2**63 - 1]")


def _new_ids(rng: Any, prefix: str, count: int) -> list[str]:
    identifiers: list[str] = []
    seen: set[str] = set()
    while len(identifiers) < count:
        value = prefix + rng.bytes(12).hex()
        if value not in seen:
            seen.add(value)
            identifiers.append(value)
    return identifiers


def _layout(recipe: DatasetRecipe, rng: Any) -> tuple[list[dict[str, Any]], list[int], list[int]]:
    rows: list[dict[str, Any]] = []
    time_indices: list[int] = []
    batch_indices: list[int] = []
    unit_number = 0
    single_series = recipe.biological_unit_policy == "single_biological_series"
    for time_index in range(recipe.num_timepoints):
        batches = [time_index] if recipe.layout == "time_aliased_batches" else range(recipe.num_batches)
        favored_batch = time_index * recipe.num_batches // recipe.num_timepoints
        for batch_index in batches:
            units = recipe.biological_units_per_cell
            if recipe.layout == "partial":
                units += recipe.num_batches - 1 if batch_index == favored_batch else -1
            for _ in range(units):
                unit_id = "unit-000001" if single_series else f"unit-{unit_number + 1:06d}"
                # Multiple libraries of the same time-specific specimen are technical repeats.
                specimen_id = f"{unit_id}-time-{time_index + 1:03d}"
                for library in range(recipe.technical_replicates_per_unit):
                    rows.append({
                        "sample_id": "", "numeric_time": float(time_index),
                        "timepoint_label": recipe.timepoint_labels[time_index],
                        "batch": recipe.batch_labels[batch_index],
                        "biological_unit_id": unit_id, "specimen_id": specimen_id,
                        "technical_replicate_id": f"library-{batch_index + 1:03d}-{library + 1:03d}",
                        "measurement_type": "raw_counts",
                    })
                    time_indices.append(time_index)
                    batch_indices.append(batch_index)
                unit_number += 1
    if len(rows) != recipe.sample_count or len({row["biological_unit_id"] for row in rows}) != recipe.independent_unit_count:
        raise RuntimeError("Constructed allocation contradicts the recipe's declared counts")
    permutation = rng.permutation(len(rows)).tolist()
    return ([rows[index] for index in permutation], [time_indices[index] for index in permutation],
            [batch_indices[index] for index in permutation])


def generate_synthetic(recipe: DatasetRecipe, generation_seed: int, *,
                       allow_data_execution: bool = False) -> dict[str, Any]:
    """Generate bounded raw counts in memory only after explicit authorization.

The returned ``truth`` is coordinator-private. Curves are expected counts at
library factor one in the first observation's batch; latent biological-unit
effects are integrated out. ``effect`` is the end-minus-start
log2 normalized mean; amplitude is separately the full curve's log2 range.
Independent RNG streams separate allocation, IDs, programs, nuisance effects
observations and positive-control sampling. Reproducibility is within a pinned NumPy version.
Per-gene ``eligible`` is a default-recipe convenience; scoring must recompute
eligibility from raw amplitude/mean and the locked StudyPlan thresholds.
"""
    _require_data_execution(allow_data_execution)
    validate_synthetic_recipe(recipe)
    _validate_seed(generation_seed)
    import numpy as np

    streams = [np.random.default_rng(seed)
               for seed in np.random.SeedSequence(generation_seed).spawn(len(SYNTHETIC_RNG_STREAMS))]
    layout_rng, id_rng, program_rng, nuisance_rng, observation_rng, reference_rng = streams
    metadata, time_indices, batch_indices = _layout(recipe, layout_rng)
    gene_ids = _new_ids(id_rng, "g-", recipe.num_genes)
    sample_ids = _new_ids(id_rng, "s-", recipe.sample_count)
    for row, sample_id in zip(metadata, sample_ids):
        row["sample_id"] = sample_id
    times = np.asarray(time_indices, dtype=int)
    batches = np.asarray(batch_indices, dtype=int)
    grid = np.arange(recipe.num_timepoints, dtype=float)
    x = grid / (recipe.num_timepoints - 1)
    shapes = np.full(recipe.num_genes, "flat", dtype="U12")
    permutation = program_rng.permutation(recipe.num_genes)
    program_size = recipe.num_genes // 10
    if recipe.scenario != "temporal_null":
        for index, name in enumerate(("increasing", "decreasing", "transient", "delayed")):
            shapes[permutation[index * program_size:(index + 1) * program_size]] = name
    means = np.exp(program_rng.uniform(np.log(10.0), np.log(1000.0), recipe.num_genes))
    dispersions = np.exp(nuisance_rng.uniform(np.log(0.04), np.log(0.40), recipe.num_genes))
    curves = np.empty((recipe.num_genes, recipe.num_timepoints), dtype=float)
    for index, shape in enumerate(shapes):
        amplitude = float(program_rng.uniform(0.6, 2.5))
        if shape == "flat":
            profile = np.zeros(recipe.num_timepoints)
        elif shape in {"increasing", "decreasing"}:
            profile = x ** float(program_rng.uniform(0.7, 1.8))
            if shape == "decreasing":
                profile = -profile
        elif shape == "transient":
            center = float(program_rng.uniform(0.3, 0.7))
            width = float(program_rng.uniform(0.12, 0.25))
            profile = np.exp(-0.5 * ((x - center) / width) ** 2)
            profile = (profile - profile[0]) / np.ptp(profile)
        else:
            midpoint = float(program_rng.uniform(0.5, 0.8))
            steepness = float(program_rng.uniform(10.0, 18.0))
            profile = 1.0 / (1.0 + np.exp(-steepness * (x - midpoint)))
            profile = (profile - profile[0]) / np.ptp(profile)
        if recipe.scenario == "library_composition" and shape != "flat":
            means[index] *= 8.0
            amplitude *= 1.5
        curves[index] = means[index] * np.exp2(amplitude * profile)

    null_indices = np.flatnonzero(shapes == "flat")
    batch_only = nuisance_rng.permutation(null_indices)[:recipe.num_genes // 20]
    batch_log_effects = np.zeros((recipe.num_genes, recipe.num_batches))
    # Stable references have neither temporal nor batch-specific mean changes.
    batch_affected = np.concatenate((np.flatnonzero(shapes != "flat"), batch_only))
    batch_log_effects[batch_affected] = nuisance_rng.normal(0.0, 0.35, (len(batch_affected), recipe.num_batches))
    batch_factors = np.exp(batch_log_effects)
    batch_factors /= batch_factors.mean(axis=1, keepdims=True)
    library_sd = 0.8 if recipe.scenario == "library_composition" else 0.35
    library_factors = np.exp(nuisance_rng.normal(-0.5 * library_sd**2, library_sd, recipe.sample_count))
    library_factors /= np.exp(np.log(library_factors).mean())
    unit_ids = sorted({row["biological_unit_id"] for row in metadata})
    unit_lookup = {unit: index for index, unit in enumerate(unit_ids)}
    unit_indices = np.asarray([unit_lookup[row["biological_unit_id"]] for row in metadata])
    matrix = np.empty((recipe.num_genes, recipe.sample_count), dtype=np.int64)
    for start in range(0, recipe.num_genes, 128):
        stop = min(start + 128, recipe.num_genes)
        phi = dispersions[start:stop, None]
        if recipe.generator_family == "negative_binomial":
            # Shared gamma frailty gives Var(Y)=mu+phi*mu^2 per library.
            latent = observation_rng.gamma(1.0 / phi, phi, size=(stop - start, len(unit_ids)))
        else:
            # E[exp(Z)] = 1 because Z has mean -sigma^2/2, not zero.
            variance = np.log1p(phi)
            latent = np.exp(observation_rng.normal(-0.5 * variance, np.sqrt(variance),
                                                   size=(stop - start, len(unit_ids))))
        expected = curves[start:stop, times] * batch_factors[start:stop, batches] * library_factors
        intensities = expected * latent[:, unit_indices]
        if not np.isfinite(intensities).all() or np.any(intensities > 1e12):
            raise ValueError("Realized intensities exceed the finite count-generation safety bound")
        matrix[start:stop] = observation_rng.poisson(intensities)
    if matrix.nbytes > recipe.max_matrix_bytes:
        raise RuntimeError("Generated matrix exceeded the recipe budget")

    curve_reference = SyntheticCurveReference(reference_batch=metadata[0]["batch"]).model_dump(mode="json")
    # Toolkit predictions hold nuisance covariates at the first observation.
    # Integrate unit frailty (whose expectation is one), not its realized value.
    reference_curves = curves * batch_factors[:, batch_indices[0], None]
    reasons: list[str] = []
    diagnostics: list[str] = []
    if recipe.layout == "time_aliased_batches":
        reasons.append("Time and batch are completely aliased; adjusted temporal effects are not identifiable")
        diagnostics.append("time_batch_complete_aliasing")
    if len(unit_ids) == 1:
        reasons.append("One biological unit cannot establish population replication from repeated technical libraries")
        diagnostics.append("insufficient_independent_biological_units")
    if recipe.technical_replicates_per_unit > 1:
        diagnostics.append("technical_replicates_not_independent")
    if recipe.layout == "partial":
        diagnostics.append("partial_time_batch_confounding")
    if recipe.scenario == "library_composition":
        diagnostics.append("library_composition_requires_robust_normalization")
    identifiable = not reasons
    genes = {}
    for index, gene_id in enumerate(gene_ids):
        curve = reference_curves[index]
        amplitude = float(np.log2(curve.max() / curve.min()))
        mean_reference = float(curve.mean())
        is_null = bool(shapes[index] == "flat")
        genes[gene_id] = {
            "null": is_null, "eligible": bool(identifiable and not is_null and amplitude >= 0.5 and mean_reference >= 20),
            "shape": str(shapes[index]), "amplitude_log2": amplitude,
            "mean_reference": mean_reference, "effect": float(np.log2(curve[-1] / curve[0])),
            "effect_units": "log2_normalized_mean_change", "predicted_curve": curve.tolist(),
        }
    batch_only_indices = set(batch_only.tolist())
    stable = [gene_ids[index] for index in null_indices if index not in batch_only_indices]
    reference_genes = []
    if recipe.scenario == "library_composition":
        desired = reference_control_count(recipe.num_genes)
        if len(stable) < desired:
            raise ValueError("Insufficient stable genes for the declared positive-control budget")
        # Uniform positive membership only: omissions and public ordering carry no negative labels.
        reference_genes = sorted(reference_rng.choice(stable, size=desired, replace=False).tolist())
    truth = {
        "record_format": "devbench.study.truth/1", "target": "temporal_omnibus",
        "gene_ids": gene_ids, "time_grid": grid.tolist(), "genes": genes,
        "curve_reference": curve_reference,
        "generation_seed": generation_seed, "recipe": recipe.model_dump(mode="json"),
        "generator_family": recipe.generator_family, "calibration_status": "uncalibrated",
        "expected_diagnostics": diagnostics,
        "population_identifiability": {"identifiable": identifiable, "reasons": reasons,
                                       "independent_unit_count": len(unit_ids)},
        "eligibility_rule": {"amplitude_log2_at_least": 0.5, "mean_reference_at_least": 20.0,
                             "status": "provisional_uncalibrated", "requires_population_identifiability": True,
                             "authority": "default_recipe_convenience_only",
                             "scoring_requirement": "Recompute from amplitude_log2 and mean_reference using locked StudyPlan thresholds"},
        "estimand": {"curve": "Population expected counts at library factor one in the first observation's batch",
                     **curve_reference,
                     "effect": "log2 mean at final time minus log2 mean at initial time",
                     "size_factor_reference": "geometric_mean_one_across_libraries",
                     "time_unit": "arbitrary_units", "causal_interpretation": False},
        "simulator_assumptions": [
            "Development generator; no biological or clinical calibration is established",
            "Opaque randomized gene labels carry no curated regulatory membership",
            "At least 60 percent of genes are time-null; a subset has batch-only effects",
            "Gene-specific dispersion is positive; library scale varies independently of biological-unit frailty",
            "Technical libraries share gene-specific unit frailty; their number does not increase biological replication",
            "Count family is gamma-Poisson or mean-corrected Poisson-lognormal without a learned reference distribution",
            "Complete aliasing retains a private latent curve but no identifiable adjusted population temporal target",
        ],
        "private_nuisance": {"dispersion": dispersions.tolist(), "library_factors": library_factors.tolist(),
                             "batch_factors": batch_factors.tolist(), "batch_only_genes": [gene_ids[i] for i in batch_only],
                             "time_null_fraction": len(null_indices) / recipe.num_genes},
        "software": {"numpy_version": np.__version__,
                     "rng": f"numpy.default_rng.SeedSequence.spawn({len(SYNTHETIC_RNG_STREAMS)})",
                     "rng_streams": list(SYNTHETIC_RNG_STREAMS)},
    }
    if len(canonical_json_bytes(truth)) > MAX_TRUTH_BYTES:
        raise ValueError("Private truth exceeds the bounded JSON budget")
    return {"matrix": matrix, "gene_ids": gene_ids, "metadata": metadata,
            "reference_genes": reference_genes, "truth": truth}


def _new_absolute_directory(output_dir: Path) -> Path:
    path = Path(output_dir)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("output_dir must be an absolute path without traversal")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValueError("output_dir and all ancestors must be free of symlinks")
    resolved = path.resolve()
    if resolved.exists():
        raise FileExistsError("Dataset destination must be a new directory")
    if not resolved.parent.is_dir():
        raise ValueError("Dataset destination parent must already exist")
    return resolved


def _write_json(path: Path, value: Any, limit: int) -> None:
    content = canonical_json_bytes(value)
    if len(content) > limit:
        raise ValueError("JSON exceeds its bounded output budget")
    with path.resolve().open("xb") as handle:
        handle.write(content)


def _input_files(root: Path, private_bytes: int) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    total_bytes = private_bytes
    for path in sorted((root / "inputs").rglob("*")):
        if path.is_symlink():
            raise ValueError("Input bundle cannot contain symlinks")
        if not path.is_file():
            continue
        if len(records) >= 20_000:
            raise ValueError("Input bundle exceeds the file-count budget")
        byte_count = path.stat().st_size
        total_bytes += byte_count
        if total_bytes > MAX_OUTPUT_BYTES:
            raise ValueError("Coordinator bundle exceeds the output byte budget")
        digest = hashlib.sha256()
        with path.resolve().open("rb") as handle:
            for block in iter(lambda: handle.read(1_048_576), b""):
                digest.update(block)
        records[path.relative_to(root).as_posix()] = {"sha256": digest.hexdigest(), "bytes": byte_count}
    return records


def prepare_dataset(recipe: DatasetRecipe, output_dir: Path, generation_seed: int, *,
                    allow_data_execution: bool = False) -> dict[str, Any]:
    """Create a new coordinator-only bundle, never a participant workspace.

Only inputs/ and input_manifest.json may later be projected to a participant.
The private/ sibling must remain outside the participant read boundary. Failure
may leave a private incomplete directory; existing destinations are never reused.
No download or public-source fallback exists in this API.
Output parents must be coordinator-controlled: path checks do not claim
protection against a concurrent adversary replacing filesystem ancestors.
"""
    _require_data_execution(allow_data_execution)
    validate_synthetic_recipe(recipe)
    _validate_seed(generation_seed)
    root = _new_absolute_directory(output_dir)
    root.mkdir(mode=0o700)
    inputs = (root / "inputs").resolve()
    private = (root / "private").resolve()
    inputs.mkdir(mode=0o700)
    private.mkdir(mode=0o700)
    bundle = generate_synthetic(recipe, generation_seed, allow_data_execution=True)
    import pandas as pd
    from devharness_core.storage import ChunkedExpressionStore

    counts_path = (inputs / "counts.zarr").resolve()
    if counts_path.exists() or counts_path.is_symlink():
        raise FileExistsError("Expression store destination already exists")
    frame = pd.DataFrame(bundle["matrix"], index=bundle["gene_ids"],
                         columns=[row["sample_id"] for row in bundle["metadata"]], copy=False)
    with ChunkedExpressionStore.from_dataframe_to_zarr(frame, counts_path):
        pass
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=METADATA_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(bundle["metadata"])
    metadata_bytes = buffer.getvalue().encode("utf-8")
    if len(metadata_bytes) > MAX_METADATA_BYTES:
        raise ValueError("Metadata exceeds the bounded output budget")
    with (inputs / "metadata.csv").resolve().open("xb") as handle:
        handle.write(metadata_bytes)
    hypothesis_family = hashlib.sha256(canonical_json_bytes({
        "gene_ids": bundle["gene_ids"], "target": "temporal_omnibus"})).hexdigest()
    curve_reference = bundle["truth"]["curve_reference"]
    task_inputs = {
        "record_format": "devbench.study.task_inputs/1", "measurement_type": "raw_counts",
        "time_unit": "arbitrary_units", "expression_path": "inputs/counts.zarr",
        "metadata_path": "inputs/metadata.csv", "path_base": "bundle_root",
        "gene_ids": bundle["gene_ids"], "hypothesis_family_id": hypothesis_family,
        "target": "temporal_omnibus", "time_grid": bundle["truth"]["time_grid"],
        "biological_unit_column": "biological_unit_id", "reference_genes": bundle["reference_genes"],
        **curve_reference,
        "required_scientific_question": (
            "Determine whether any genes have supported temporal associations after accounting for batch, "
            "library scale, and biological-unit identity. Assess identifiability and replication before fitting. "
            "If supported, report per-gene omnibus p-values and BH q-values over the declared gene family, "
            "curve shapes and normalized mean curves, and final-minus-initial log2 normalized mean changes. "
            "The curve reference holds batch at the first metadata observation's batch (reference_batch), "
            "at library factor one with biological-unit effects averaged over the population; "
            "normalize size factors to geometric mean one across libraries. "
            "Use robust size factors or supplied reference genes; do not treat technical libraries as biological replication. "
            "Report unsupported inferences and explicit diagnostics when the design cannot answer the question."
        ),
    }
    _write_json((inputs / "task_inputs.json").resolve(), task_inputs, MAX_METADATA_BYTES)
    _write_json((private / "truth.json").resolve(), bundle["truth"], MAX_TRUTH_BYTES)
    files = _input_files(root, (private / "truth.json").resolve().stat().st_size)
    manifest = {"record_format": "devbench.study.input_manifest/1", "files": files,
                "input_sha256": hashlib.sha256(canonical_json_bytes(files)).hexdigest(),
                "hash_scope": "input-relative-paths-bytes-and-sha256", "measurement_type": "raw_counts",
                **curve_reference}
    if sum(item["bytes"] for item in files.values()) + (private / "truth.json").resolve().stat().st_size + len(canonical_json_bytes(manifest)) > MAX_OUTPUT_BYTES:
        raise ValueError("Coordinator bundle including manifest exceeds the output byte budget")
    _write_json((root / "input_manifest.json").resolve(), manifest, MAX_METADATA_BYTES)
    return {"bundle_root": str(root), "input_manifest": manifest,
            "private_truth_path": str((private / "truth.json").resolve())}


class PublicManifest(FrozenContract):
    """Declared provenance only: this model never reads or authenticates data."""

    record_format: Literal["devbench.study.public_manifest/1"]
    source_id: Identifier
    classification: Literal["public_research"]
    primary_url: Annotated[str, Field(min_length=1, max_length=2000)]
    input_sha256: Sha256
    measurement_type: Literal["raw_counts", "continuous"]
    expression_units: Literal["read_counts", "UMI_counts", "RPKM", "TPM", "normalized_expression"]
    biological_unit_scope: Literal["unverified", "pooled_reference_rna", "biological_units", "cells_nested_within_biological_units"]
    reference_scope: Literal["observational_expression", "mixture_and_spikein_controls", "perturbation_response"]
    complete_causal_truth: Literal[False] = False
    complete_pseudotime_truth: Literal[False] = False
    validation_status: Literal["contract_only"] = "contract_only"
    input_hash_verified: Literal[False] = False
    biological_units_verified: Literal[False] = False

    @model_validator(mode="after")
    def coherent_provenance(self) -> PublicManifest:
        url = urlparse(self.primary_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("primary_url must be a public HTTPS reference without credentials")
        count_units = self.expression_units in {"read_counts", "UMI_counts"}
        if count_units != (self.measurement_type == "raw_counts"):
            raise ValueError("measurement_type must agree with expression_units")
        if self.source_id == "E-MTAB-6798" and (
            self.measurement_type != "continuous" or self.expression_units != "RPKM"
            or self.reference_scope != "observational_expression"
        ):
            raise ValueError("E-MTAB-6798 registry scope is observational continuous RPKM")
        if self.source_id == "GSE47774" and (
            self.measurement_type != "raw_counts" or self.biological_unit_scope != "pooled_reference_rna"
            or self.expression_units != "read_counts"
            or self.reference_scope != "mixture_and_spikein_controls"
        ):
            raise ValueError("SEQC processed counts describe pooled reference RNA and limited control truth")
        return self


def validate_public_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate claims, without verifying hashes, units, source files or licenses."""
    return PublicManifest.model_validate(manifest).model_dump(mode="json")


RegistryText = Annotated[str, Field(min_length=1, max_length=2000)]
RegistrySourceId = Literal["E-MTAB-6798", "GSE47774", "dyngen", "scDesign3", "Perturb-seq"]


def _false_registry_flag(value: Any) -> bool:
    if type(value) is not bool or value:
        raise ValueError("Registry policy and validation flags must be the boolean false")
    return value


class HistoricalCacheNote(FrozenContract):
    """Historical counts are an unverified description, never a current receipt."""

    reported_gene_count: Annotated[int, Field(ge=1, le=100_000)]
    reported_sample_count: Annotated[int, Field(ge=1, le=100_000)]
    biological_unit_provenance: Literal["unknown"]
    revalidated: Literal[False]
    scope: RegistryText

    @field_validator("revalidated", mode="before")
    @classmethod
    def unverified_flag(cls, value: Any) -> bool:
        return _false_registry_flag(value)


class PublicSourceRecord(FrozenContract):
    """Closed metadata proposal; source, license and unit authenticity are pending."""

    source_id: RegistrySourceId
    title: RegistryText
    source_kind: Literal["public_processed", "future_simulator_reference", "future_public_perturbation_reference"]
    status: Literal["not_acquired", "reference_only"]
    primary_urls: Annotated[list[RegistryText], Field(min_length=1, max_length=10)]
    measurement_type: Literal["raw_counts", "continuous", "modality_dependent"]
    expression_units: Literal["RPKM", "read_counts", "simulator_dependent_counts", "configuration_dependent", "UMI_counts_pending_file_selection"]
    candidate_processed_file: Literal["Mouse.RPKM.txt"] | None = None
    reference_scope: RegistryText
    biological_unit_scope: Literal[
        "unverified", "pooled_reference_rna", "simulated_cells_not_independent_donors",
        "reference_dependent_unverified", "cells_nested_in_experimental_replicates",
    ]
    complete_causal_truth: Literal[False]
    complete_pseudotime_truth: Literal[False]
    metadata_requirements: Annotated[list[RegistryText], Field(min_length=1, max_length=30)]
    historical_cache_note: HistoricalCacheNote | None = None
    selection_status: Literal["not_selected"]
    license_status: Literal["pending_verification"]
    input_sha256: None
    metadata_sha256: None
    acquisition_status: Literal["not_authorized_or_performed"]

    @field_validator("complete_causal_truth", "complete_pseudotime_truth", mode="before")
    @classmethod
    def unsupported_truth_flags(cls, value: Any) -> bool:
        return _false_registry_flag(value)

    @model_validator(mode="after")
    def consistent_source_description(self) -> PublicSourceRecord:
        expected = {
            "E-MTAB-6798": ("public_processed", "not_acquired", "continuous", "RPKM", "unverified"),
            "GSE47774": ("public_processed", "not_acquired", "raw_counts", "read_counts", "pooled_reference_rna"),
            "dyngen": ("future_simulator_reference", "reference_only", "raw_counts", "simulator_dependent_counts",
                       "simulated_cells_not_independent_donors"),
            "scDesign3": ("future_simulator_reference", "reference_only", "modality_dependent", "configuration_dependent",
                          "reference_dependent_unverified"),
            "Perturb-seq": ("future_public_perturbation_reference", "reference_only", "raw_counts",
                            "UMI_counts_pending_file_selection", "cells_nested_in_experimental_replicates"),
        }
        if (self.source_kind, self.status, self.measurement_type, self.expression_units,
                self.biological_unit_scope) != expected[self.source_id]:
            raise ValueError("Source kind, acquisition status, measurement units and biological-unit scope must match the registered reference")
        for reference in self.primary_urls:
            url = urlparse(reference)
            if url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError("Primary references must be HTTPS URLs without credentials")
        if len(set(self.primary_urls)) != len(self.primary_urls):
            raise ValueError("Primary reference URLs must be distinct")
        if self.source_id == "E-MTAB-6798":
            if self.candidate_processed_file is None or self.historical_cache_note is None:
                raise ValueError("Mouse reference requires the candidate file and explicit unverified historical-cache note")
        elif self.candidate_processed_file is not None or self.historical_cache_note is not None:
            raise ValueError("Mouse-specific file/cache descriptions cannot be assigned to other sources")
        return self


class PublicSourceRegistry(FrozenContract):
    """Metadata-only catalog with acquisition and fallback disabled."""

    record_format: Literal["devbench.study.public_sources/1"]
    registry_scope: RegistryText
    automatic_downloads: Literal[False]
    fallback_to_synthetic: Literal[False]
    raw_archive_downloads_permitted: Literal[False]
    sources: Annotated[list[PublicSourceRecord], Field(min_length=5, max_length=5)]

    @field_validator("automatic_downloads", "fallback_to_synthetic", "raw_archive_downloads_permitted", mode="before")
    @classmethod
    def denied_policies(cls, value: Any) -> bool:
        return _false_registry_flag(value)

    @model_validator(mode="after")
    def distinct_registered_sources(self) -> PublicSourceRegistry:
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("Registry must contain each declared source once")
        return self


def validate_public_sources(registry: dict[str, Any]) -> dict[str, Any]:
    """Pure schema check; no source authentication, acquisition or license review."""
    return PublicSourceRegistry.model_validate(registry).model_dump(mode="json", exclude_unset=True)


def load_public_sources() -> dict[str, Any]:
    """Read only the shipped bounded metadata registry; no expression or network I/O."""
    path = Path(__file__).resolve().parent / "public_sources.json"
    if path.is_symlink():
        raise ValueError("Shipped public source registry must not be a symlink")
    path = path.resolve()
    with path.open("rb") as handle:
        content = handle.read(MAX_REGISTRY_BYTES + 1)
    if len(content) > MAX_REGISTRY_BYTES:
        raise ValueError("Shipped public source registry exceeds its metadata budget")
    return validate_public_sources(json.loads(content))
