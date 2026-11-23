"""Contracts and staged preflight for the local Python RNA routes."""

import hashlib
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any, Literal, NamedTuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from devbench.core import bulk_deconvolution, bulk_dtu, phenotype_prediction, scrna_ccc
from devbench.core._rna_solver_inputs import (
    MAX_INPUT_BYTES,
    donor_design,
    indexed_rows,
    numeric_matrix,
    read_count_and_metadata,
    read_solver_csv,
    require_labels,
)
from devbench.core.perturbation_analysis import (
    PerturbationSpec,
    _pseudobulk_input,
    fit_perturbation_response,
)
from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    Assay,
    EntryPoint,
    Measurement,
    ReferencePin,
)
from devbench.core.scrna_neighborhood_da import NeighborhoodDASpec, fit_neighborhood_da
from devbench.core.spatial_analysis import SpatialPatternSpec, fit_spatial_pattern
from devbench.engine.workflow_operations import OperationBlocked

from .plugin_jobs import digest


class _SolverChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CCCChoice(_SolverChoice):
    operation: Literal["scrna_ccc"]
    parameters: scrna_ccc.CellCommunicationSpec


class NeighborhoodChoice(_SolverChoice):
    operation: Literal["scrna_neighborhood_da"]
    parameters: NeighborhoodDASpec


class DeconvolutionChoice(_SolverChoice):
    operation: Literal["bulk_deconvolution"]
    parameters: bulk_deconvolution.BulkDeconvolutionSpec


class DTUChoice(_SolverChoice):
    operation: Literal["bulk_dtu"]
    parameters: bulk_dtu.BulkDTUSpec
    transcript_reference: ReferencePin


class SpatialChoice(_SolverChoice):
    operation: Literal["spatial_pattern"]
    parameters: SpatialPatternSpec


class PerturbationChoice(_SolverChoice):
    operation: Literal["perturbation_response"]
    parameters: PerturbationSpec


class PredictionChoice(_SolverChoice):
    operation: Literal["phenotype_prediction"]
    parameters: phenotype_prediction.PhenotypePredictionSpec


SolverChoice = Annotated[
    CCCChoice
    | NeighborhoodChoice
    | DeconvolutionChoice
    | DTUChoice
    | SpatialChoice
    | PerturbationChoice
    | PredictionChoice,
    Field(discriminator="operation"),
]

_ROUTES: dict[str, tuple[int, str, Assay, EntryPoint]] = {
    "scrna_ccc": (
        10,
        "cell_communication",
        "single_cell_rna_seq",
        "normalized_expression",
    ),
    "scrna_neighborhood_da": (
        3,
        "compositional_abundance",
        "single_cell_rna_seq",
        "annotated_object",
    ),
    "bulk_deconvolution": (3, "bulk_deconvolution", "bulk_rna_seq", "count_matrix"),
    "bulk_dtu": (4, "transcript_usage", "bulk_rna_seq", "transcript_quantification"),
    "spatial_pattern": (
        9,
        "spatial_pattern",
        "spatial_transcriptomics",
        "count_matrix",
    ),
    "perturbation_response": (
        11,
        "perturbation_response",
        "single_cell_rna_seq",
        "count_matrix",
    ),
    "phenotype_prediction": (
        18,
        "phenotype_prediction",
        "bulk_rna_seq",
        "count_matrix",
    ),
}
LOCAL_OPERATIONS = frozenset(_ROUTES)
_INPUT_BINDINGS = {
    "scrna_ccc": {
        "expression": "expression",
        "metadata": "metadata",
        "ligand_receptor": "reference",
    },
    "scrna_neighborhood_da": {"embedding": "expression", "metadata": "metadata"},
    "bulk_deconvolution": {
        "bulk": "expression",
        "reference": "reference",
        "metadata": "metadata",
    },
    "bulk_dtu": {
        "counts": "expression",
        "metadata": "metadata",
        "mapping": "reference",
        "effective_lengths": "reference",
    },
    "spatial_pattern": {"counts": "expression", "metadata": "metadata"},
    "perturbation_response": {"counts": "expression", "metadata": "metadata"},
    "phenotype_prediction": {
        "counts": "expression",
        "metadata": "metadata",
        "independent_evaluation_partition": "reference",
    },
}


def local_request(choice: SolverChoice) -> AnswerabilityRequest:
    family, question, assay, entry = _ROUTES[choice.operation]
    if (
        isinstance(choice, DeconvolutionChoice)
        and choice.parameters.bulk_measurement == "cpm"
    ):
        entry = "normalized_expression"
    return AnswerabilityRequest(
        family_id=family, question=question, assay=assay, entry_point=entry
    )


def runtime_info() -> dict[str, Any]:
    packages = (
        "devharness",
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "scikit-learn",
        "pydeseq2",
    )
    try:
        versions = {dist: version(dist) for dist in packages}
    except PackageNotFoundError as error:
        raise OperationBlocked("local_python_runtime_unavailable") from error
    # Source checkouts carry uv.lock. Wheel installs attest the installed versions.
    lock = Path(__file__).resolve().parents[3] / "uv.lock"
    lock_sha = None
    if lock.is_file():
        with lock.open("rb") as source:
            lock_sha = hashlib.file_digest(source, "sha256").hexdigest()
    facts = {
        "target": "local_worker",
        "engine": "python",
        "implementation": sys.implementation.name,
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "architecture": platform.machine(),
        "package_versions": versions,
        "lockfile_sha256": lock_sha,
        "oci_digest": None,
        "network_policy": "offline",
    }
    return {**facts, "runtime_sha256": digest(facts)}


def bind_sources(
    choice: SolverChoice,
    profiles: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    spec = choice.parameters.model_dump(mode="json")
    selected = {}
    for field, role in _INPUT_BINDINGS[choice.operation].items():
        expected = spec[field + "_sha256"]
        if expected is None:
            continue
        candidates = [
            profile
            for profile in profiles
            if profile["sha256"] == expected and profile["role"] == role
        ]
        if len(candidates) != 1:
            raise OperationBlocked("solver_input_digest_or_role_mismatch")
        profile = candidates[0]
        expected_type = "json" if field == "independent_evaluation_partition" else "csv"
        if profile["observed"]["file_type"] != expected_type:
            raise OperationBlocked("solver_input_file_type_unsupported")
        selected[field] = profile
    if len({profile["source"] for profile in selected.values()}) != len(
        profiles
    ) or len(selected) != len(profiles):
        raise OperationBlocked("solver_input_scope_mismatch")
    if sum(profile["size_bytes"] for profile in profiles) > MAX_INPUT_BYTES:
        raise OperationBlocked("solver_input_byte_budget_exceeded")
    return selected


class ObservedMeasurement(NamedTuple):
    source: dict[str, Any]
    kind: Measurement
    slot: str
    observations: tuple[str, ...] = ()
    features: tuple[str, ...] = ()


class SolverEvidence(NamedTuple):
    measurements: list[ObservedMeasurement]
    design_units: tuple[int, ...] = ()


def _csv(profile: dict[str, Any], identity: str) -> pd.DataFrame:
    return indexed_rows(
        read_solver_csv(Path(profile["source"]), profile["sha256"]), identity
    )


def _matched(
    sources: dict[str, dict[str, Any]], key: str, identity: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = _csv(sources[key], identity)
    metadata = _csv(sources["metadata"], identity)
    if set(frame.index) != set(metadata.index):
        raise ValueError("expression_and_metadata_observation_ids_must_match")
    return frame, metadata.loc[frame.index]


def _ccc_evidence(
    choice: CCCChoice, sources: dict[str, dict[str, Any]]
) -> SolverEvidence:
    spec = choice.parameters
    frame, metadata = _matched(sources, "expression", "cell_id")
    require_labels(metadata, ("donor_id", "cell_type"))
    metadata = scrna_ccc._select_condition(metadata, spec)
    if (numeric_matrix(frame) < 0).any():
        raise ValueError("nonnegative_normalized_expression_required")
    reference = scrna_ccc._read_reference(
        Path(sources["ligand_receptor"]["source"]), spec.ligand_receptor_sha256
    )
    if not any(
        set(ligand + receptor) <= set(frame.columns)
        for ligand, receptor in reference.values()
    ):
        raise ValueError("at_least_one_measured_ligand_receptor_pair_required")
    if spec.contrast is not None:
        donor_design(metadata, spec.contrast)
    else:
        counts = metadata.groupby(["donor_id", "cell_type"]).size()
        eligible = (counts >= spec.min_cells_per_donor_type).groupby(level=0).sum()
        if (eligible < 2).any():
            raise ValueError("donors_require_two_eligible_cell_types_for_specificity")
    return SolverEvidence(
        [
            ObservedMeasurement(
                sources["expression"],
                "cell_type_expression",
                "cell_rows",
                tuple(frame.index),
                tuple(frame.columns),
            ),
            ObservedMeasurement(
                sources["ligand_receptor"],
                "ligand_receptor_reference",
                "ligand_and_receptor",
            ),
        ]
    )


def _deconvolution_evidence(
    choice: DeconvolutionChoice,
    sources: dict[str, dict[str, Any]],
) -> SolverEvidence:
    spec = choice.parameters
    bulk = bulk_deconvolution._read_expression(
        Path(sources["bulk"]["source"]),
        spec.bulk_sha256,
        measurement=spec.bulk_measurement,
    )
    reference = bulk_deconvolution._read_expression(
        Path(sources["reference"]["source"]),
        spec.reference_sha256,
        measurement=spec.reference_measurement,
    )
    metadata = read_solver_csv(
        Path(sources["metadata"]["source"]), spec.metadata_sha256
    )
    groups = bulk_deconvolution._reference_groups(metadata, reference.observations)
    excluded = bulk_deconvolution._outlier_mask(
        bulk, reference, exclude_ribosomal=spec.exclude_ribosomal
    )
    shared, _, _ = bulk_deconvolution._shared_axis(
        bulk, reference, excluded, max(spec.min_shared_genes, len(groups) + 1)
    )
    primary: Measurement = (
        "gene_counts" if spec.bulk_measurement == "counts" else "normalized_expression"
    )
    return SolverEvidence(
        [
            ObservedMeasurement(
                sources["bulk"],
                primary,
                "matched_bulk_features",
                bulk.observations,
                tuple(shared),
            ),
            ObservedMeasurement(
                sources["reference"],
                "cell_type_reference",
                "matched_reference_features",
                reference.observations,
                tuple(shared),
            ),
        ]
    )


def _dtu_evidence(
    choice: DTUChoice, sources: dict[str, dict[str, Any]]
) -> SolverEvidence:
    spec = choice.parameters
    if choice.transcript_reference.reference_sha256 != spec.mapping_sha256:
        raise OperationBlocked("transcript_reference_digest_mismatch")
    frame, metadata = _matched(sources, "counts", "sample_id")
    _, axis = bulk_dtu._read_mapping(
        Path(sources["mapping"]["source"]), spec, frame.columns
    )
    lengths = sources.get("effective_lengths")
    bulk_dtu._lengths(
        Path(lengths["source"]) if lengths else None, spec, axis, frame.columns
    )
    bulk_dtu._usage_counts(frame, metadata, spec.count_scale)
    _, _, diagnostics = donor_design(metadata, spec.contrast, min_units_per_group=2)
    return SolverEvidence(
        [
            ObservedMeasurement(
                sources["counts"],
                spec.measurement,
                axis,
                tuple(frame.index),
                tuple(frame.columns),
            ),
        ],
        tuple(diagnostics["donors_per_condition"].values()),
    )


def _count_evidence(
    choice: SpatialChoice | PerturbationChoice | PredictionChoice,
    sources: dict[str, dict[str, Any]],
) -> SolverEvidence:
    expression = sources["counts"]
    annotation = sources["metadata"]
    identity = (
        "spot_id"
        if isinstance(choice, SpatialChoice)
        else "cell_id"
        if isinstance(choice, PerturbationChoice)
        else "sample_id"
    )
    counts, metadata, genes = read_count_and_metadata(
        Path(expression["source"]),
        Path(annotation["source"]),
        expression["sha256"],
        annotation["sha256"],
        identity,
    )
    ids, features = tuple(metadata.index), tuple(genes)
    measured = [ObservedMeasurement(expression, "gene_counts", identity, ids, features)]
    if isinstance(choice, SpatialChoice):
        require_labels(metadata, ("donor_id", "section_id", "cell_type"))
        if not {"x", "y"} <= set(metadata.columns):
            raise ValueError("spatial_coordinates_require_physical_x_and_y_columns")
        numeric_matrix(metadata[["x", "y"]])
        measured.append(
            ObservedMeasurement(annotation, "spatial_coordinates", "x,y", ids)
        )
        return SolverEvidence(measured)
    if isinstance(choice, PerturbationChoice):
        metadata = metadata.assign(batch=metadata.get("batch", "unspecified"))
        require_labels(metadata, ("donor_id", "perturbation", "batch"))
        spec = choice.parameters
        control, assignments = spec.non_targeting_control, set(metadata["perturbation"])
        if control not in assignments or len(assignments) < 2:
            raise ValueError("declared_control_and_perturbation_required")
        groups = []
        for label in sorted(assignments - {control}):
            mask = metadata["perturbation"].isin([control, label]).to_numpy()
            _, _, diagnostics = _pseudobulk_input(
                counts[mask], metadata.loc[mask], control, label
            )
            groups.append(tuple(diagnostics["biological_units_per_group"].values()))
        measured.extend(
            (
                ObservedMeasurement(
                    annotation, "perturbation_assignments", "perturbation", ids
                ),
                ObservedMeasurement(
                    annotation, "control_assignments", "perturbation/control", ids
                ),
            )
        )
        return SolverEvidence(
            measured, tuple(min(arm) for arm in zip(*groups, strict=True))
        )
    prediction = choice.parameters
    evaluation = sources.get("independent_evaluation_partition")
    if evaluation is None:
        raise OperationBlocked("independent_evaluation_partition_required")
    donors, group = phenotype_prediction._profiles(metadata)
    for train, _ in phenotype_prediction._folds(
        metadata, prediction.n_splits, prediction.seed
    ):
        phenotype_prediction._folds(
            metadata.iloc[train], prediction.inner_splits, prediction.seed
        )
    _, held = phenotype_prediction._locked_inputs(
        Path(evaluation["source"]), evaluation["sha256"], genes
    )
    phenotype_prediction._profiles(held)
    phenotype_prediction._check_disjoint(metadata, held)
    if set(ids) & set(held.index):
        raise ValueError("observation_leakage_across_training_and_evaluation")
    measured.extend(
        (
            ObservedMeasurement(annotation, "phenotype_outcomes", "label", ids),
            # The partition attestation is joined to the training axis it excludes.
            ObservedMeasurement(
                evaluation,
                "independent_evaluation_partition",
                "holdout_bound_to_training",
                ids,
                features,
            ),
        )
    )
    return SolverEvidence(
        measured, tuple(donors.groupby("label")[group].nunique().tolist())
    )


def check_inputs(
    choice: SolverChoice, sources: dict[str, dict[str, Any]]
) -> SolverEvidence:
    if isinstance(choice, CCCChoice):
        return _ccc_evidence(choice, sources)
    if isinstance(choice, NeighborhoodChoice):
        frame, metadata = _matched(sources, "embedding", "cell_id")
        coordinates = numeric_matrix(frame)
        if len(frame) <= choice.parameters.k or np.all(coordinates == coordinates[0]):
            raise ValueError("neighborhood_geometry_unusable")
        _, _, diagnostics = donor_design(metadata, choice.parameters.contrast)
        return SolverEvidence(
            [
                ObservedMeasurement(
                    sources["embedding"],
                    "latent_embedding",
                    "cell_rows",
                    tuple(frame.index),
                    tuple(frame.columns),
                ),
            ],
            tuple(diagnostics["donors_per_condition"].values()),
        )
    if isinstance(choice, DeconvolutionChoice):
        return _deconvolution_evidence(choice, sources)
    if isinstance(choice, DTUChoice):
        return _dtu_evidence(choice, sources)
    return _count_evidence(choice, sources)


def execute_solver(
    choice: SolverChoice, sources: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    paths = {key: Path(profile["source"]) for key, profile in sources.items()}
    if isinstance(choice, CCCChoice):
        return scrna_ccc.fit_cell_communication(
            paths["expression"],
            paths["metadata"],
            paths["ligand_receptor"],
            choice.parameters,
        )
    if isinstance(choice, NeighborhoodChoice):
        return fit_neighborhood_da(
            paths["embedding"], paths["metadata"], choice.parameters
        ).model_dump(mode="json")
    if isinstance(choice, DeconvolutionChoice):
        return dict(
            bulk_deconvolution.fit_bulk_deconvolution(
                paths["bulk"], paths["reference"], paths["metadata"], choice.parameters
            )
        )
    counts, metadata = paths["counts"], paths["metadata"]
    if isinstance(choice, DTUChoice):
        return bulk_dtu.fit_bulk_dtu(
            counts,
            metadata,
            paths["mapping"],
            choice.parameters,
            effective_lengths_path=paths.get("effective_lengths"),
        )
    if isinstance(choice, SpatialChoice):
        return fit_spatial_pattern(counts, metadata, choice.parameters)
    if isinstance(choice, PerturbationChoice):
        return fit_perturbation_response(counts, metadata, choice.parameters)
    return phenotype_prediction.fit_phenotype_prediction(
        counts,
        metadata,
        choice.parameters,
        independent_evaluation_partition=paths["independent_evaluation_partition"],
    )
