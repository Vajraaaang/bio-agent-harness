"""Governed STDIO tools for local, explicitly scoped RNA-seq research."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager, suppress
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from errno import EEXIST, ENOTEMPTY
from pathlib import Path
from sqlite3 import Connection
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Annotated, Any, Literal
from uuid import uuid4

import anyio
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Resource, TextContent, Tool, ToolAnnotations
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from devbench.analysis.evidence_access import AccessPolicy, EvidenceBroker
from devbench.analysis.literature_adapter import LiteratureRequest
from devbench.analysis.thesis_synthesis import ThesisSynthesisSpec
from devbench.analysis.workbench_inputs import (
    InputDiscovery,
    _h5ad_sparse_layout,
)
from devbench.core.count_forensics import (
    CONFLICTING_PROVENANCE,
    SUSPECTED_TRANSFORM,
    CountDistribution,
    declares_count_transform,
)
from devbench.core.procedure_profile import ProcedureProfile
from devbench.core.protected_partitions import PartitionVault
from devbench.core.rna_answerability import (
    RNA_FAMILIES,
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    Measurement,
    RuntimeEvidence,
    assess_answerability,
)
from devbench.core.rna_pathway_analysis import PathwayPlan
from devbench.core.scrna_composition import CompositionObservationSpec, CompositionSpec
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.engine.workspace import read_bounded_file
from devbench.study.permissions import require_protected_storage
from devharness_core.watchdog import RemoteWorkerError
from rna_kernels.scrna_upstream_contract import SingleCellStepSpec

from .plugin_jobs import (
    MAX_DOCUMENT_BYTES,
    JobStore,
    canonical,
    digest,
    directory,
    ensure_offline_studio,
    studio_handoff,
)
from .plugin_mcp_output import PRIVATE_LAUNCH_HINT, output_schema, output_validator
from .plugin_rna_solvers import (
    LOCAL_OPERATIONS,
    CCCChoice,
    DeconvolutionChoice,
    DTUChoice,
    NeighborhoodChoice,
    PerturbationChoice,
    PredictionChoice,
    SolverChoice,
    SolverEvidence,
    SpatialChoice,
    bind_sources,
    check_inputs,
    execute_solver,
    local_request,
)
from .plugin_rna_solvers import runtime_info as _local_python_runtime
from .scrna_general_pseudobulk import GeneralPseudobulkSpec
from .thesis_aims import AimDefinition, AimOutcome, ExplanationAssessment

_PRIVATE = "private_data_local_studio_only"
_ACTOR = "plugin-operator"
_ID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
_SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_PATH = Annotated[str, Field(min_length=1, max_length=4096)]
_TEXT = Annotated[str, Field(min_length=1, max_length=1600)]
_CLINICAL = re.compile(
    r"(^|[^a-z])(clinical|patient|mrn|medical_record|pheno_clinical|"
    r"date_of_birth|birth_date|dob|social_security|diagnosis|phi|email|"
    r"telephone|phone_number)([^a-z]|$)",
    re.IGNORECASE,
)


def _clinical_name(value: str) -> bool:
    import unicodedata

    name = unicodedata.normalize("NFKC", value)
    if _CLINICAL.search(name):
        return True
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return _CLINICAL.search(re.sub(r"[^a-zA-Z0-9]+", "_", name)) is not None


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Input(_Args):
    path: _PATH
    data_classification: Literal[
        "synthetic", "public", "private", "private_clinical", "clinical", "unclassified"
    ] = "unclassified"
    clinical_metadata: bool = False
    role: Literal["expression", "metadata", "manifest", "reference"] = "expression"


class Inspect(_Args):
    inputs: list[Input] = Field(min_length=1, max_length=16)


class _LocalContrast(_Args):
    factor: _TEXT
    numerator: _TEXT
    denominator: _TEXT


class LocalDesign(_Args):
    metadata: Input
    formula: _TEXT
    contrast: _LocalContrast
    biological_unit: _TEXT
    batch_columns: list[_TEXT] = Field(default_factory=list, max_length=16)


class _Count1Confirmation(_Args):
    source_sha256: _SHA
    slot: _TEXT
    evidence: Annotated[str, Field(min_length=1, max_length=1600, pattern=r"\S")]


class Answerability(_Args):
    measurement: Literal["umi_counts", "non_umi_counts"] | None = None
    count_provenance: _TEXT | None = None
    counts_layer: _TEXT | None = None
    count1_confirmation: _Count1Confirmation | None = None
    batch_column: _TEXT | None = None
    capture_column: _TEXT | None = None
    capture_units_confirmed: bool = False
    raw_droplet_provenance: _DropletProvenance | None = None
    session_id: _ID | None = None
    composition: _CompositionStage | None = None
    pseudobulk: _PseudobulkStage | None = None
    pathways: _PathwayBinding | None = None
    salmon: _FastqParameters | None = None
    request: AnswerabilityRequest
    inputs: list[Input] = Field(default_factory=list, max_length=16)
    measurements: list[AttestedMeasurement] = Field(
        default_factory=list, max_length=128
    )
    design: DesignEvidence | None = None
    local_design: LocalDesign | None = None
    local_solver: SolverChoice | None = None
    normalized_layer: _TEXT | None = None
    normalization: Literal["library_size_normalized", "library_size_log1p"] | None = (
        None
    )
    coordinate_frame: _TEXT | None = None
    coordinate_units: _TEXT | None = None
    non_targeting_control: _TEXT | None = None


class _PlanDependency(_Args):
    plan_id: _ID
    plan_sha256: _SHA


class _ScopedPlan(_Args):
    session_id: _ID | None = None
    inputs: list[Input] = Field(default_factory=list, max_length=16)
    objective: _TEXT
    depends_on: list[_PlanDependency] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _validate_dependencies(self):
        if self.depends_on and self.session_id is None:
            raise ValueError("graph dependencies require an existing session")
        if len({item.plan_id for item in self.depends_on}) != len(self.depends_on):
            raise ValueError("duplicate graph dependency")
        return self


class _Plan(_ScopedPlan):
    measurement: (
        Literal["raw_counts", "normalized_expression", "umi_counts", "non_umi_counts"]
        | None
    ) = None
    count_provenance: _TEXT | None = Field(
        default=None, description="Source declaration required for governed bulk DE."
    )
    counts_layer: _TEXT | None = None
    observation_column: _TEXT | None = None
    batch_column: _TEXT | None = None
    condition_column: _TEXT | None = None
    references: list[_TEXT] = Field(default_factory=list, max_length=16)
    answerability: Answerability | None = None


class _MatrixPlan(_Plan):
    operation: Literal["matrix_audit"]
    parameters: _Args = Field(default_factory=_Args)


class _SharedMotorUMAPParameters(_Args):
    stage_column: _TEXT = "age"
    label_column: _TEXT = "celltypes"
    counts_layer: _TEXT = "counts"


class _SharedMotorUMAPPlan(_ScopedPlan):
    operation: Literal["shared_motor_umap"]
    objective: _TEXT
    inputs: list[Input] = Field(
        min_length=3,
        max_length=3,
        description="Three H5AD inputs in e09, e10, e11 order.",
    )
    parameters: _SharedMotorUMAPParameters = Field(
        default_factory=_SharedMotorUMAPParameters
    )


class _MotorProgramComparisonPlan(_ScopedPlan):
    operation: Literal["motor_program_comparison"]
    parameters: _Args = Field(default_factory=_Args)


class _RegisteredExploratoryPlan(_ScopedPlan):
    operation: Literal["registered_exploratory"]
    session_id: _ID
    method_version_id: _SHA
    step_name: Annotated[str, Field(min_length=1, max_length=200)]
    parameters: dict[str, Any] = Field(max_length=64)
    input_versions: dict[str, str] = Field(min_length=1, max_length=16)


_DeclarationOrigin = Literal[
    "user_declaration", "metadata_declaration", "assistant_suggestion", "unknown"
]
_Column = Annotated[str, Field(max_length=200)]
_Provenance = Annotated[str, Field(min_length=1, max_length=4000)]


class _Attribution(_Args):
    origin: _DeclarationOrigin
    source: _Provenance


class _AttributedText(_Attribution):
    value: _Column


class _AttributedProvenance(_Attribution):
    value: _Provenance


_BatchList = Annotated[
    list[Annotated[_Column, Field(min_length=1)]], Field(max_length=8)
]


class _AttributedBatch(_Attribution):
    value: _Column | _BatchList


_Declaration = _Column | _AttributedText


class _BulkDeclarations(_Args):
    biological_unit: _Declaration | None = None
    population_unit_column: _Declaration | None = None
    specimen_column: _Declaration | None = None
    library_column: _Declaration | None = None
    observation_column: _Declaration | None = None
    condition_column: _Declaration | None = None
    identity_status: _Declaration | None = None
    dependence: _Declaration | None = None
    batch_columns: _Column | _BatchList | _AttributedBatch | None = None
    contrast_numerator: _Declaration | None = None
    contrast_denominator: _Declaration | None = None
    declaration_provenance: _Provenance | _AttributedProvenance | None = None


class _BulkParameters(_Args):
    bulk_declarations: _BulkDeclarations = Field(default_factory=_BulkDeclarations)


class _BulkPlan(_Plan):
    operation: Literal["bulk_de"]
    parameters: _BulkParameters = Field(default_factory=_BulkParameters)


class _StepSource(_Args):
    id: _TEXT
    port: _TEXT | None = None


class _CellStep(SingleCellStepSpec):
    sources: dict[str, _StepSource] = Field(max_length=16)

    @model_validator(mode="after")
    def _registered_ports(self) -> _CellStep:
        from rna_kernels.scrna_upstream_contract import SCRNA_OPERATIONS

        if self.sources.keys() != SCRNA_OPERATIONS[self.operation].inputs.keys():
            raise ValueError("Choose the exact registered input ports")
        return self


class _IndependentUnits(_Args):
    independent: bool
    unit_column: _TEXT
    evidence: _Provenance


class _DropletProvenance(_Args):
    source_sha256: _SHA
    capture_id: _TEXT
    origin_receipt_sha256: _SHA
    source_kind: _TEXT
    filter_state: _TEXT


class _CompositionStage(_Args):
    qc_step_id: _TEXT
    observations: CompositionObservationSpec
    model: CompositionSpec


class _PseudobulkStage(_Args):
    qc_step_id: _TEXT
    aim_id: _TEXT
    profile: ProcedureProfile
    specification: GeneralPseudobulkSpec


class _CellParameters(_Args):
    capture_units_confirmed: bool
    steps: list[_CellStep] = Field(min_length=1, max_length=16)
    biological_independence: _IndependentUnits | None = None
    raw_droplet_provenance: _DropletProvenance | None = None
    composition: _CompositionStage | None = None


class _PseudobulkParameters(_Args):
    capture_units_confirmed: Literal[True]
    pseudobulk: _PseudobulkStage


class _CellPlan(_Plan):
    operation: Literal["single_cell"]
    parameters: _CellParameters | _PseudobulkParameters


class _PathwayBinding(_Args):
    source_session_id: _ID | None = None
    step_id: _TEXT
    pathway_plan: PathwayPlan | None = None
    foreground_q_max: Annotated[float, Field(gt=0, le=1)] | None = None


class _PathwayPlan(_Plan):
    operation: Literal["pathways"]
    parameters: _PathwayBinding


class _Tx2Gene(_Args):
    path: _PATH
    sha256: _SHA


class _FastqParameters(_Args):
    tx2gene: _Tx2Gene
    quantification_only: bool = False


class _FastqPlan(_Plan):
    operation: Literal["qc-salmon"]
    parameters: _FastqParameters


class _CCCPlan(CCCChoice, _Plan):
    pass


class _NeighborhoodPlan(NeighborhoodChoice, _Plan):
    pass


class _DeconvolutionPlan(DeconvolutionChoice, _Plan):
    pass


class _DTUPlan(DTUChoice, _Plan):
    pass


class _SpatialPlan(SpatialChoice, _Plan):
    pass


class _PerturbationPlan(PerturbationChoice, _Plan):
    pass


class _PredictionPlan(PredictionChoice, _Plan):
    pass


_LOCAL_PLAN_TYPES = (
    _CCCPlan,
    _NeighborhoodPlan,
    _DeconvolutionPlan,
    _DTUPlan,
    _SpatialPlan,
    _PerturbationPlan,
    _PredictionPlan,
)
_SOLVER_FIELDS = {"operation", "parameters", "transcript_reference"}
_SOLVER_ADAPTER: TypeAdapter[SolverChoice] = TypeAdapter(SolverChoice)

_StandardPlan = (
    _MatrixPlan
    | _BulkPlan
    | _CellPlan
    | _PathwayPlan
    | _FastqPlan
    | _CCCPlan
    | _NeighborhoodPlan
    | _DeconvolutionPlan
    | _DTUPlan
    | _SpatialPlan
    | _PerturbationPlan
    | _PredictionPlan
)


class Plan(
    RootModel[
        Annotated[
            _StandardPlan
            | _SharedMotorUMAPPlan
            | _MotorProgramComparisonPlan
            | _RegisteredExploratoryPlan,
            Field(discriminator="operation"),
        ]
    ]
):
    model_config = ConfigDict(json_schema_extra={"type": "object"})


class Session(_Args):
    session_id: _ID


class Execute(Session):
    plan_id: _ID
    plan_sha256: _SHA


class Run(Session):
    run_id: _ID


class _ListAims(Session):
    action: Literal["list"]
    request: _Args = Field(default_factory=_Args)


class _Revision(_Args):
    expected_revision: int = Field(ge=0)


class _ProposeRequest(_Revision):
    definition: AimDefinition


class _ReviseRequest(_ProposeRequest):
    rationale: _TEXT


class _ApproveRequest(_Revision):
    aim_id: _TEXT
    rationale: _TEXT


class _VersionedAim(_Revision):
    aim_id: _TEXT
    version: int = Field(ge=1)


class _AssessmentRequest(_VersionedAim):
    assessment: ExplanationAssessment


class _OutcomeRequest(_VersionedAim):
    outcome: AimOutcome


class _LiteratureQuery(_Args):
    run_id: _ID
    context_id: _TEXT
    binding_sha256: _SHA
    request_id: _TEXT
    query: LiteratureRequest


class _PackageSpec(ThesisSynthesisSpec):
    answer_schema_sha256: _SHA | None = None  # type: ignore[assignment]


class _PackageRequest(_Args):
    spec: _PackageSpec
    answer_schema: _PATH | None = None


class _ProposeAim(Session):
    action: Literal["propose_aim"]
    request: _ProposeRequest


class _ReviseAim(Session):
    action: Literal["revise_aim"]
    request: _ReviseRequest


class _ApproveAim(Session):
    action: Literal["approve_aim"]
    request: _ApproveRequest


class _AssessExplanation(Session):
    action: Literal["assess_explanation"]
    request: _AssessmentRequest


class _RecordOutcome(Session):
    action: Literal["record_outcome"]
    request: _OutcomeRequest


class _QueryLiterature(Session):
    action: Literal["query_literature_post_evidence"]
    request: _LiteratureQuery


class _BuildPackage(Session):
    action: Literal["build_research_package"]
    request: _PackageRequest


class ResearchAim(
    RootModel[
        Annotated[
            _ListAims
            | _ProposeAim
            | _ReviseAim
            | _ApproveAim
            | _AssessExplanation
            | _RecordOutcome
            | _QueryLiterature
            | _BuildPackage,
            Field(discriminator="action"),
        ]
    ]
):
    model_config = ConfigDict(json_schema_extra={"type": "object"})


def _canonical_input(root: Path, value: str, *, allow_external_files=False) -> Path:
    path = Path(value)
    in_root = path.is_relative_to(root)
    if (
        not path.is_absolute()
        or path == root
        or (not in_root and not allow_external_files)
        or path.resolve(strict=True) != path
    ):
        raise ValueError("Input must be canonical and inside the configured input root")
    if not in_root and not path.is_file():
        raise ValueError("An external input must be an existing regular file")
    return path


def _zarr_profile(path: Path) -> dict[str, Any]:
    metadata = {}
    for name in (".zmetadata", "zarr.json", ".zgroup"):
        marker = path / name
        if marker.exists():
            metadata = json.loads(read_bounded_file(marker, 256 * 1024))
            break
    if not isinstance(metadata, dict) or not metadata:
        raise ValueError("Zarr requires a bounded local group metadata header")
    # Metadata inventory only. The directory and chunk data are not attested.
    return {
        "observed": {"file_type": "zarr", "matrix_chunks_inspected": False},
        "header_sha256": digest(metadata),
        "sha256": None,
    }


def _profile(discovery: InputDiscovery, root: Path, item: Input) -> dict[str, Any]:
    path = _canonical_input(
        root, item.path, allow_external_files=discovery.allow_external_files
    )
    parts = path.relative_to(root).parts if path.is_relative_to(root) else path.parts
    declared_private = (
        item.clinical_metadata
        or item.data_classification not in {"synthetic", "public"}
        or any(_clinical_name(name) for name in parts)
    )
    if path.is_dir() and path.suffix == ".zarr":
        profile = _zarr_profile(path)
    else:
        if not path.is_file():
            raise ValueError("Input requires a supported regular file or Zarr group")
        try:
            profile = discovery.inspect(path, stream_selected_h5ad=True)
        except RemoteWorkerError as error:
            if path.suffix.lower() != ".h5ad":
                raise
            # Worker tracebacks may include private filenames, fields or values.
            raise OperationBlocked(
                _PRIVATE if declared_private else "h5ad_header_inspection_failed"
            ) from error
    obs = profile["observed"]
    names = (
        name
        for field in ("obs_columns", "var_columns", "columns", "layers", "obsm_keys")
        for name in obs.get(field, ())
    )
    private = declared_private or any(_clinical_name(name) for name in names)
    return {
        **profile,
        "source": str(path),
        "path": str(path),
        "data_classification": "private" if private else item.data_classification,
        "role": item.role,
    }


_TABLE_TYPES = {"csv", "tsv", "csv.gz", "tsv.gz"}
_AXIS_BUDGET = 4 * 1024 * 1024
_H5_BLOCK = 1024 * 1024
_H5_MAX_DECODED_CHUNK = 64 * 1024 * 1024
_H5_LAYOUTS = {"10x_cellranger"}
_INTEGER_COUNT_KINDS = {"gene_counts", "spliced_counts", "unspliced_counts"}
_FEATURE_LABELS = {"gene_id", "gene", "gene_name", ""}
_METADATA_IDENTITIES = {
    "sample_id",
    "sample",
    "donor_id",
    "donor",
    "specimen_id",
    "specimen",
    "library_id",
    "library",
    "observation_id",
    "subject_id",
    "participant_id",
    "cell_id",
    "barcode",
}


def _metadata_header(columns: list[str]) -> bool:
    # A role declaration alone cannot attest a flat header as metadata. Require
    # an observation-identity field at the conventional row-label position.
    return (
        1 < len(columns) <= 128
        and columns[0].casefold() in _METADATA_IDENTITIES
        and len(set(columns)) == len(columns)
    )


def _valid_numeric_table_row(row: list[str], measurement: str) -> bool:
    try:
        values = tuple(map(float, row))
    except ValueError:
        return False
    return (
        all(map(math.isfinite, values))
        and (measurement != "estimated_gene_counts" or min(values) >= 0)
        and (measurement != "effective_lengths" or min(values) > 0)
    )


def _observe_table_counts(values: list[str], distribution: CountDistribution) -> bool:
    counts = []
    for text in values:
        # Do not round a fractional decimal through binary floating point.
        try:
            value = Decimal(text)
        except InvalidOperation:
            return False
        if (
            not value.is_finite()
            or not 0 <= value < 2**53
            or value != value.to_integral_value()
        ):
            return False
        counts.append(int(value))
    distribution.observe(counts)
    return True


def _table_axes(
    profile: dict[str, Any],
    *,
    verify_counts: bool = False,
    count1_confirmed: bool = False,
    maximum: int = _AXIS_BUDGET,
    numeric_measurement: Literal[
        "normalized_expression", "estimated_gene_counts", "effective_lengths"
    ]
    | None = None,
    verified_axes: tuple[tuple[str, ...], tuple[str, ...]] | None = None,
) -> dict[str, str]:
    if verified_axes is not None:
        observed_ids, feature_ids = verified_axes
        return {
            **(
                {"observation_axis_sha256": digest(sorted(observed_ids))}
                if observed_ids
                else {}
            ),
            **({"feature_axis_sha256": digest(feature_ids)} if feature_ids else {}),
        }
    from devbench.analysis.rna_input_contract import MAX_GENES
    from devbench.analysis.workbench_episode_intake import _expression_rows

    with _expression_rows(profile, maximum=maximum) as rows:
        header: list[str] = next(rows, [])
        if len(header) < 2 or header[0] not in _FEATURE_LABELS:
            return {}
        observations = header[1:]
        if len(set(observations)) != len(observations):
            return {}
        features = []
        distribution = (
            CountDistribution(major_width=len(observations), libraries_in_rows=False)
            if verify_counts
            else None
        )
        for row in rows:
            if (
                maximum > _AXIS_BUDGET
                and len(features) >= MAX_GENES
                or len(row) != len(header)
                or not row[0]
                or len(row[0]) > 200
            ):
                return {}
            if distribution is not None and not _observe_table_counts(
                row[1:], distribution
            ):
                return {}
            if numeric_measurement and not _valid_numeric_table_row(
                row[1:],
                numeric_measurement,
            ):
                return {}
            features.append(row[0])
    if not features or len(set(features)) != len(features):
        return {}
    if (
        distribution is not None
        and distribution.suspected_transform
        and not count1_confirmed
    ):
        raise OperationBlocked(SUSPECTED_TRANSFORM)
    return {
        "observation_axis_sha256": digest(sorted(observations)),
        "feature_axis_sha256": digest(features),
    }


def _validate_h5_dataset(node) -> None:
    import h5py

    if node.external or node.is_virtual:
        raise ValueError("HDF5 inspection requires internal dataset storage")
    if (
        node.chunks
        and math.prod(node.chunks) * node.dtype.itemsize > _H5_MAX_DECODED_CHUNK
    ):
        raise ValueError("HDF5 decoded chunk exceeds the inspection limit")
    filters = node.id.get_create_plist()
    allowed = {
        h5py.h5z.FILTER_DEFLATE,
        h5py.h5z.FILTER_SHUFFLE,
        h5py.h5z.FILTER_FLETCHER32,
        h5py.h5z.FILTER_LZF,
    }
    if any(
        filters.get_filter(i)[0] not in allowed for i in range(filters.get_nfilters())
    ):
        raise ValueError("HDF5 uses an unsupported inspection filter")


def _h5_matrix_storage(element, shape, layout):
    """Return validated, backed dense or sparse components without materialization."""
    import h5py

    from devbench.core.population_data import _node, _sparse_components

    if layout in {"csr", "csc"}:
        for key in ("data", "indices", "indptr"):
            node = _node(element, key)
            if not isinstance(node, h5py.Dataset):
                return None
            _validate_h5_dataset(node)
        values, indices, pointers = _sparse_components(
            element, shape, layout, _H5_BLOCK
        )
        return (
            (values, indices, pointers)
            if _canonical_sparse_indices(indices, pointers)
            else None
        )
    if layout is None and isinstance(element, h5py.Dataset) and element.shape == shape:
        _validate_h5_dataset(element)
        return element, None, None
    return None


def _h5_declares_count_transform(element) -> bool:
    import h5py
    import numpy as np

    def text(key):
        if key not in element.attrs:
            return None
        with closing(element.attrs.get_id(key)) as attr:
            encoding = h5py.check_string_dtype(attr.dtype)
            if attr.shape != () or encoding is None:
                return None
            target = np.empty(
                (), dtype=h5py.string_dtype(encoding.encoding, length=129)
            )
            with closing(h5py.h5t.py_create(target.dtype, logical=True)) as bounded:
                attr.read(target, mtype=bounded)
        raw = target.item()
        if len(raw) > 128:
            raise ValueError("HDF5 processing label exceeds the metadata bound")
        return raw.decode(encoding.encoding)

    return declares_count_transform(text("representation"), text("transformation"))


def _verify_h5_counts(
    element,
    shape: tuple[int, int],
    layout: str | None,
    *,
    integer: bool = True,
    nonnegative: bool = True,
    count1_flags: set[str] | None = None,
    libraries_in_rows: bool = True,
) -> bool:
    import numpy as np

    storage = _h5_matrix_storage(element, shape, layout)
    if storage is None or min(shape) < 1:
        return False
    values, indices, pointers = storage
    if values.dtype.kind not in "iuf":
        return False
    check_raw_counts = integer and nonnegative
    if check_raw_counts and _h5_declares_count_transform(element):
        if count1_flags is not None:
            count1_flags.add(CONFLICTING_PROVENANCE)
        return False
    libraries_in_major_axis = libraries_in_rows != (layout == "csc")
    distribution = (
        CountDistribution(
            total_values=math.prod(shape),
            major_width=shape[0] if layout == "csc" else shape[1],
            libraries_in_rows=libraries_in_major_axis,
        )
        if check_raw_counts
        else None
    )
    blocks = (
        _sparse_library_blocks(values, indices, pointers, libraries_in_major_axis)
        if check_raw_counts and indices is not None
        else ((chunk, None) for chunk in _h5_value_blocks(values))
    )
    for chunk, libraries in blocks:
        if (
            not np.isfinite(chunk).all()
            or (nonnegative and np.any(chunk < 0))
            or (nonnegative and np.any(chunk >= 2**53))
            or (
                integer
                and values.dtype.kind == "f"
                and np.any(chunk != np.floor(chunk))
            )
        ):
            return False
        if distribution is not None:
            distribution.observe(chunk, library_indices=libraries)
    if distribution is not None and distribution.suspected_transform:
        if count1_flags is not None:
            count1_flags.add(SUSPECTED_TRANSFORM)
        return False
    return True


def _h5_value_blocks(values):
    """Read in storage order, splitting even a single very wide dense row."""
    elements = max(1, _H5_BLOCK // values.dtype.itemsize)
    if values.ndim == 1:
        for start in range(0, values.size, elements):
            yield values[start : start + elements]
        return
    width = min(values.shape[1], elements)
    height = max(1, elements // width)
    for row in range(0, values.shape[0], height):
        for col in range(0, values.shape[1], width):
            yield values[row : row + height, col : col + width]


def _sparse_library_blocks(values, indices, pointers, libraries_in_major_axis):
    """Map stored entries to their logical library without expanding implicit zeros."""
    import numpy as np

    stride = max(1, _H5_BLOCK // max(8, values.dtype.itemsize, indices.dtype.itemsize))
    if not libraries_in_major_axis:
        for start in range(0, len(values), stride):
            span = slice(start, start + stride)
            yield values[span], indices[span]
        return
    # Iterate the compressed axis a page at a time. Repeated indptr offsets
    # represent empty libraries; side="right" skips those at block boundaries.
    for major in range(0, len(pointers) - 1, 4096):
        offsets = pointers[major : major + 4097]
        end = int(offsets[-1])
        for start in range(int(offsets[0]), end, stride):
            stop = min(start + stride, end)
            ids = major + np.searchsorted(
                offsets[1:], np.arange(start, stop), side="right"
            )
            yield values[start:stop], ids


def _canonical_sparse_indices(indices, pointers) -> bool:
    import numpy as np

    # Bound both the number of pointers and the coordinate window. A boundary
    # at a different row/column permits the index to reset.
    stride = _H5_BLOCK // max(indices.dtype.itemsize, 8)
    for pos in range(0, len(pointers) - 1, 4096):
        offsets = pointers[pos : pos + 4097]
        start, end = int(offsets[0]), int(offsets[-1])
        for left in range(start, end - 1, stride):
            actual = indices[left : min(left + stride + 1, end)]
            breaks = left + np.flatnonzero(actual[1:] <= actual[:-1]) + 1
            if breaks.size and not np.isin(breaks, offsets).all():
                return False
    return True


def _bounded_hdf5_probe(probe: Callable[..., Any], *args: Any) -> Any:
    from devharness_core.watchdog import HarnessResourceInterrupt, run_sandboxed

    try:
        return run_sandboxed(probe, args, max_mem_mb=384, timeout_sec=30)
    except HarnessResourceInterrupt as exc:
        raise OperationBlocked("hdf5_inspection_resource_limit") from exc
    except RemoteWorkerError as exc:
        raise OperationBlocked("hdf5_evidence_inspection_failed") from exc


def _annotation_relationships(db, columns, readers, batch, capture, control):
    """Reduce selected columns together; retain only digests for cross-block joins."""
    pairs = [
        (child, parent)
        for child, parent in ((capture, batch), ("donor_id", "label"))
        if child != parent and child in columns and parent in columns
    ]
    db.execute(
        "CREATE TABLE pairs (child TEXT, parent TEXT, lhs BLOB, rhs BLOB, "
        "PRIMARY KEY (child, parent, lhs, rhs)) WITHOUT ROWID"
    )
    outcomes, controls = set(), set()
    for blocks in zip(*readers, strict=True):
        values = dict(zip(columns, blocks, strict=True))
        outcomes.update(
            v if v in ("0", "1") else "other" for v in values.get("label", ())
        )
        controls.update(v == control for v in values.get("perturbation", ()))
        for child, parent in pairs:
            unique = set(zip(values[child], values[parent], strict=True))
            db.executemany(
                "INSERT OR IGNORE INTO pairs VALUES (?, ?, ?, ?)",
                (
                    (
                        child,
                        parent,
                        hashlib.sha256(left.encode()).digest(),
                        hashlib.sha256(right.encode()).digest(),
                    )
                    for left, right in unique
                ),
            )
    ambiguous = set(
        db.execute(
            "SELECT child, parent FROM pairs "
            "GROUP BY child, parent, lhs HAVING count(*) > 1"
        )
    )
    return set(pairs) - ambiguous, outcomes == {"0", "1"}, controls == {False, True}


def _h5ad_annotation_measurements(
    obs, expected, columns, batch, capture, control
) -> list[tuple[Measurement, str]]:
    from .plugin_h5ad_axes import h5ad_label_blocks

    if not columns:
        return []
    readers = [
        h5ad_label_blocks(obs, column, expected, _validate_h5_dataset, annotation=True)
        for column in columns
    ]
    # An unnamed SQLite database spills to disk as needed and is deleted on close.
    with closing(sqlite3.connect("")) as db:
        links, binary, has_control = _annotation_relationships(
            db, columns, readers, batch, capture, control
        )
    selected: list[tuple[Measurement, str]] = []
    if "perturbation" in columns:
        selected.append(("perturbation_assignments", "obs/perturbation"))
        if has_control:
            selected.append(("control_assignments", "obs/perturbation/control"))
    if binary and ("donor_id", "label") in links:
        selected.append(("phenotype_outcomes", "obs/label"))
    if batch in columns:
        selected.append(("batch_membership", "obs/" + batch))
    if (capture, batch) in links:
        selected.append(("batch_membership", "obs/" + capture))
    return selected


def _h5ad_evidence(
    profile: dict[str, Any],
    check_slots: tuple[str, ...] = (),
    annotation_columns: tuple[str, ...] = (),
    continuous_slot: str | None = None,
    physical_spatial: bool = False,
    confirmed_slot: str | None = None,
    annotation_roles: tuple[str | None, str | None, str | None] = (None, None, None),
) -> tuple[
    dict[str, str], tuple[str, ...], list[tuple[Measurement, str]], dict[str, str]
]:
    import h5py

    from devbench.analysis.rna_scrna_input_contract import _closed_storage
    from devbench.core.population_data import _node, bound_query_file

    from .plugin_h5ad_axes import h5ad_index_digest

    observed = profile["observed"]
    shape = (observed["cells"], observed["genes"])
    with (
        bound_query_file(
            profile["source"], profile["sha256"], profile["size_bytes"]
        ) as source,
        h5py.File(source, "r", rdcc_nbytes=_H5_BLOCK) as handle,
    ):
        _closed_storage(handle)
        obs = handle["obs"]
        axes = {
            "observation_axis_sha256": h5ad_index_digest(
                obs, shape[0], _validate_h5_dataset
            ),
            "feature_axis_sha256": h5ad_index_digest(
                handle["var"], shape[1], _validate_h5_dataset
            ),
        }
        columns = tuple(
            col for col in annotation_columns if col in observed["obs_columns"]
        )
        annotations = _h5ad_annotation_measurements(
            obs, shape[0], columns, *annotation_roles
        )
        verified = []
        additional = {}
        for slot in check_slots:
            flags: set[str] = set()
            if _valid_h5ad_slot(
                handle, slot, observed["layers"], shape, count1_flags=flags
            ):
                verified.append(slot)
            elif CONFLICTING_PROVENANCE in flags:
                additional["count1_reason"] = CONFLICTING_PROVENANCE
            elif SUSPECTED_TRANSFORM in flags:
                if confirmed_slot == ("X" if slot == "X" else "layers/" + slot):
                    verified.append(slot)
                else:
                    additional["count1_reason"] = SUSPECTED_TRANSFORM
        if continuous_slot and _valid_h5ad_slot(
            handle, continuous_slot, observed["layers"], shape, integer=False
        ):
            additional["cell_type_expression"] = (
                continuous_slot
                if continuous_slot == "X"
                else "layers/" + continuous_slot
            )
        if (
            physical_spatial
            and "spatial" in observed["obsm_keys"]
            and isinstance(handle["obsm"], h5py.Group)
            and _verify_h5_counts(
                _node(handle["obsm"], "spatial"),
                (shape[0], 2),
                None,
                integer=False,
                nonnegative=False,
            )
        ):
            additional["spatial_coordinates"] = "obsm/spatial"
    return axes, tuple(verified), annotations, additional


def _valid_h5ad_slot(
    handle,
    slot: str,
    layers: list[str],
    shape: tuple[int, int],
    *,
    integer: bool = True,
    count1_flags: set[str] | None = None,
) -> bool:
    import h5py

    if slot == "X":
        element = handle["X"]
    elif slot in layers:
        element = handle["layers"][slot]
    else:
        return False
    if isinstance(element, h5py.Dataset):
        return _verify_h5_counts(
            element, shape, None, integer=integer, count1_flags=count1_flags
        )
    layout, sparse_shape = _h5ad_sparse_layout(element)
    return (
        layout in ("csr", "csc")
        and sparse_shape == shape
        and _verify_h5_counts(
            element, shape, layout, integer=integer, count1_flags=count1_flags
        )
    )


def _tenx_evidence(
    profile: dict[str, Any], verify_counts: bool, confirmed: bool = False
) -> dict[str, str]:
    import h5py

    from devbench.analysis.rna_scrna_input_contract import _closed_storage
    from devbench.core.population_data import bound_query_file
    from rna_kernels.scrna_droplets import _text_axis

    obs = profile["observed"]
    genes, cells = obs["genes"], obs["cells"]
    with (
        bound_query_file(
            profile["source"], profile["sha256"], profile["size_bytes"]
        ) as source,
        h5py.File(source, "r", rdcc_nbytes=_H5_BLOCK) as handle,
    ):
        _closed_storage(handle)
        matrix = handle["matrix"]
        features = matrix["features"]
        for dataset in (matrix["barcodes"], features["id"], features["feature_type"]):
            _validate_h5_dataset(dataset)
            if dataset.size * max(dataset.dtype.itemsize, 24) > 8 * _H5_BLOCK:
                return {}
        barcodes = _text_axis(matrix, "barcodes", cells)
        labels = _text_axis(features, "id", genes)
        feature_types = _text_axis(features, "feature_type", genes)
        if len(set(barcodes)) != cells or len(set(labels)) != genes:
            return {}
        rna_features = [
            label
            for label, kind in zip(labels, feature_types, strict=True)
            if kind == "Gene Expression"
        ]
        if not rna_features:
            return {}
        flags: set[str] = set()
        if verify_counts and not _verify_h5_counts(
            matrix, (genes, cells), "csc", libraries_in_rows=False, count1_flags=flags
        ):
            if not flags:
                return {}
            reason = flags.pop()
            if reason != SUSPECTED_TRANSFORM or not confirmed:
                return {"count1_reason": reason}
    return {
        "observation_axis_sha256": digest(barcodes),
        "feature_axis_sha256": digest(rna_features),
    }


def _inside_file_budget(profile: dict[str, Any]) -> bool:
    observed = profile["observed"]
    is_hdf5 = (
        observed["file_type"] == "h5ad" or observed.get("matrix_layout") in _H5_LAYOUTS
    )
    size = profile.get("size_bytes") or 0
    return size > 0 and (is_hdf5 or size <= _AXIS_BUDGET)


def _inspection_axes(profile: dict[str, Any]) -> dict[str, str]:
    observed = profile["observed"]
    file_type = observed["file_type"]
    if not _inside_file_budget(profile):
        return {}
    try:
        if file_type == "h5ad":
            return _bounded_hdf5_probe(_h5ad_evidence, profile)[0]
        if observed.get("matrix_layout") in _H5_LAYOUTS:
            return _bounded_hdf5_probe(_tenx_evidence, profile, False)
        if file_type in _TABLE_TYPES and profile["role"] == "expression":
            return _table_axes(profile)
    except (OSError, ValueError, RuntimeError, csv.Error):
        if file_type == "h5ad" or observed.get("matrix_layout") in _H5_LAYOUTS:
            return {"axis_verification_reason": "hdf5_axis_verification_failed"}
        return {}  # The bounded header is still available for discovery.
    return {}


def _inspect_projection(
    profile: dict[str, Any], *, axes: dict[str, str] | None = None
) -> dict[str, Any]:
    if profile["data_classification"] not in {"public", "synthetic"}:
        raise OperationBlocked(_PRIVATE)
    observed = profile["observed"]
    fields = ("file_type", "cells", "genes", "matrix_layout", "matrix_chunks_inspected")
    preview = {key: observed[key] for key in fields if key in observed}
    if observed["file_type"] == "h5ad":
        preview.update(
            layers=observed["layers"],
            obsm_keys=observed["obsm_keys"],
            obs_columns=observed["obs_columns"][:64],
            obs_columns_truncated=len(observed["obs_columns"]) > 64,
        )
    columns = observed.get("columns")
    if (
        profile["role"] == "metadata"
        and observed["file_type"] in _TABLE_TYPES
        and columns
        and _metadata_header(columns)
    ):
        preview["columns"] = columns[:64]
        preview["columns_truncated"] = len(columns) > 64
    projection = {
        "data_classification": profile["data_classification"],
        "observed": preview,
        "size_bytes": profile.get("size_bytes"),
        "sha256": profile.get("sha256"),
        "header_sha256": profile.get("header_sha256"),
        **(_inspection_axes(profile) if axes is None else axes),
    }
    return {**projection, "inspection_receipt_sha256": digest(projection)}


def _h5ad_measurements(
    args: Answerability,
    profile: dict[str, Any],
) -> tuple[dict[str, str], list[tuple[Measurement, str]]]:
    slots: tuple[str, ...]
    continuous_bulk = (
        args.request.assay == "bulk_rna_seq"
        and args.request.entry_point == "normalized_expression"
    )
    if continuous_bulk:
        slots = ()
    elif args.request.question == "rna_velocity":
        slots = ("spliced", "unspliced")
    else:
        slot = args.counts_layer or (
            "counts" if "counts" in profile["observed"]["layers"] else "X"
        )
        slots = (slot,)
    from devbench.core.scientific_types import PHYSICAL_UNITS

    extra_columns = {
        "cell_communication": ("cell_type", "donor_id"),
        "perturbation_response": ("perturbation", "donor_id"),
        "phenotype_prediction": ("label", "donor_id"),
    }.get(args.request.question, ())
    physical_spatial = (
        args.request.question in {"spatial_pattern", "spatial_domains"}
        and args.coordinate_units in PHYSICAL_UNITS
        and args.coordinate_frame is not None
        and args.coordinate_frame.strip() == args.coordinate_frame
        and not re.search(
            r"umap|(?:^|[^a-z])pca|t[\W_]*sne|latent|embedding",
            args.coordinate_frame,
            re.I,
        )
    )
    columns = tuple(
        dict.fromkeys(
            col
            for col in (args.batch_column, args.capture_column, *extra_columns)
            if col is not None
        )
    )
    axes, verified, annotations, additional = _bounded_hdf5_probe(
        _h5ad_evidence,
        profile,
        slots,
        columns,
        (args.normalized_layer or "X")
        if continuous_bulk
        else (
            args.normalized_layer
            if args.normalization and args.request.question == "cell_communication"
            else None
        ),
        physical_spatial,
        args.count1_confirmation.slot
        if args.count1_confirmation
        and args.count1_confirmation.source_sha256 == profile["sha256"]
        else None,
        (args.batch_column, args.capture_column, args.non_targeting_control),
    )
    if "count1_reason" in additional:
        raise OperationBlocked(additional["count1_reason"])
    kinds: dict[str, Measurement] = {
        "X": "gene_counts",
        "counts": "gene_counts",
        "spliced": "spliced_counts",
        "unspliced": "unspliced_counts",
    }
    selected = [
        (kinds.get(slot, "gene_counts"), slot if slot == "X" else "layers/" + slot)
        for slot in verified
        if (slot if slot == "X" else "layers/" + slot)
        != additional.get("cell_type_expression")
    ]
    if continuous_bulk and "cell_type_expression" in additional:
        selected.append(("normalized_expression", additional["cell_type_expression"]))
    elif {"cell_type", "donor_id"} <= set(
        profile["observed"]["obs_columns"]
    ) and "cell_type_expression" in additional:
        selected.append(("cell_type_expression", additional["cell_type_expression"]))
    if "spatial_coordinates" in additional:
        selected.append(("spatial_coordinates", additional["spatial_coordinates"]))
    selected.extend(annotations)
    return axes, selected


def _attested_solver_measurements(
    args: Answerability,
    solver_evidence: SolverEvidence,
    *,
    offline_private: bool = False,
) -> tuple[AttestedMeasurement, ...]:
    attestations = []
    choice = args.local_solver
    for measured in solver_evidence.measurements:
        axes = _table_axes(
            measured.source,
            verified_axes=(measured.observations, measured.features),
        )
        spatial = (
            choice.parameters
            if isinstance(choice, SpatialChoice)
            and measured.kind == "spatial_coordinates"
            else None
        )
        if offline_private and measured.source["data_classification"] == "private":
            # Input inspection stays in the sealed private plan.
            inspection = digest(
                {
                    "format": "devbench.offline-rna-solver-inspection/1",
                    "input_sha256": measured.source["sha256"],
                    "size_bytes": measured.source["size_bytes"],
                    "axes": axes,
                }
            )
        else:
            inspection = _inspect_projection(measured.source, axes=axes)[
                "inspection_receipt_sha256"
            ]
        attestations.append(
            AttestedMeasurement(
                kind=measured.kind,
                slot=measured.slot,
                artifact_sha256=measured.source["sha256"],
                receipt_sha256=digest(
                    {
                        "inspection": inspection,
                        "kind": measured.kind,
                        "slot": measured.slot,
                        "solver": choice.model_dump(mode="json") if choice else None,
                    }
                ),
                observation_axis_sha256=axes.get("observation_axis_sha256"),
                feature_axis_sha256=axes.get("feature_axis_sha256"),
                reference=choice.transcript_reference
                if isinstance(choice, DTUChoice)
                else None,
                coordinate_frame=spatial.coordinate_frame if spatial else None,
                coordinate_units=spatial.coordinate_units if spatial else None,
            )
        )
    return tuple(attestations)


def _profile_expression_measurements(
    args: Answerability,
    profile: dict[str, Any],
    tximport: bool,
) -> tuple[dict[str, str], list[tuple[Measurement, str]]]:
    request = args.request
    role = profile["role"]
    observed = profile["observed"]
    numeric_measurement: (
        Literal["normalized_expression", "estimated_gene_counts", "effective_lengths"]
        | None
    ) = None
    if tximport:
        numeric_measurement = (
            "effective_lengths" if role == "reference" else "estimated_gene_counts"
        )
    elif (
        request.assay == "bulk_rna_seq"
        and request.entry_point == "normalized_expression"
    ):
        numeric_measurement = "normalized_expression"
    if numeric_measurement and observed["file_type"] in _TABLE_TYPES:
        axes = _table_axes(profile, numeric_measurement=numeric_measurement)
        return axes, [(numeric_measurement, "feature_rows")] if axes else []
    if tximport:
        return {}, []
    if observed["file_type"] in _TABLE_TYPES and request.entry_point == "count_matrix":
        review = args.count1_confirmation
        axes = _table_axes(
            profile,
            verify_counts=True,
            count1_confirmed=bool(
                review
                and review.source_sha256 == profile["sha256"]
                and review.slot == "feature_rows"
            ),
        )
        return axes, [("gene_counts", "feature_rows")] if axes else []
    if observed["file_type"] == "h5ad":
        return _h5ad_measurements(args, profile)
    if not numeric_measurement and observed.get("matrix_layout") in _H5_LAYOUTS:
        review = args.count1_confirmation
        confirmed = bool(
            review
            and review.source_sha256 == profile["sha256"]
            and review.slot == "matrix"
        )
        axes = _bounded_hdf5_probe(_tenx_evidence, profile, True, confirmed)
        if "count1_reason" in axes:
            raise OperationBlocked(axes["count1_reason"])
        return axes, [("gene_counts", "matrix")] if axes else []
    return {}, []


def _attested_measurements(
    args: Answerability,
    profiles: list[dict[str, Any]],
    *,
    solver_evidence: SolverEvidence | None = None,
) -> tuple[AttestedMeasurement, ...]:
    if solver_evidence is not None:
        return _attested_solver_measurements(args, solver_evidence)
    request = args.request
    measurements: list[AttestedMeasurement] = []
    tximport = (
        request.assay == "bulk_rna_seq"
        and request.question == "differential_expression"
        and request.entry_point == "transcript_quantification"
    )
    for profile in profiles:
        role = profile["role"]
        if (
            role != "expression" and not (tximport and role == "reference")
        ) or not _inside_file_budget(profile):
            continue
        axes, selected = _profile_expression_measurements(args, profile, tximport)
        if not selected:
            continue
        projection = _inspect_projection(profile, axes=axes)
        measurements.extend(
            AttestedMeasurement(
                kind=kind,
                slot=slot,
                artifact_sha256=profile["sha256"],
                receipt_sha256=digest(
                    {
                        "inspection": projection["inspection_receipt_sha256"],
                        "verified_representation": kind,
                        "slot": slot,
                        **(
                            {
                                "count1_confirmation_sha256": digest(
                                    args.count1_confirmation.model_dump()
                                )
                            }
                            if kind in _INTEGER_COUNT_KINDS
                            and args.count1_confirmation
                            and args.count1_confirmation.source_sha256
                            == profile["sha256"]
                            and args.count1_confirmation.slot == slot
                            else {}
                        ),
                        **(
                            {
                                "coordinate_frame": args.coordinate_frame,
                                "coordinate_units": args.coordinate_units,
                            }
                            if kind == "spatial_coordinates"
                            else {}
                        ),
                        **(
                            {"declared_control": args.non_targeting_control}
                            if kind == "control_assignments"
                            else {}
                        ),
                        **(
                            {"declared_normalization": args.normalization}
                            if kind == "cell_type_expression"
                            else {}
                        ),
                    }
                ),
                observation_axis_sha256=axes.get("observation_axis_sha256"),
                feature_axis_sha256=axes.get("feature_axis_sha256"),
                coordinate_frame=args.coordinate_frame
                if kind == "spatial_coordinates"
                else None,
                coordinate_units=args.coordinate_units
                if kind == "spatial_coordinates"
                else None,
            )
            for kind, slot in selected
        )
    return tuple(measurements)


_CELL_ROUTES = {
    "single_cell_qc": (2, "scrna_batch_qc"),
    "single_cell_doublets": (2, "scrna_lane_doublets"),
    "single_cell_preparation": (2, "scrna_prepare"),
    "droplet_qc": (2, "scrna_droplets"),
    "atlas_integration": (8, "scrna_integrate"),
}
_CELL_ASSAYS = {"single_cell_rna_seq", "single_nucleus_rna_seq"}


def _answer_runtime(
    request: AnswerabilityRequest,
    image_id: str | None,
    *,
    local_solver: SolverChoice | None = None,
    local_runtime: dict[str, Any] | None = None,
) -> RuntimeEvidence | None:
    if local_solver is not None:
        declared = local_request(local_solver)
        if (declared.family_id, declared.question) != (
            request.family_id,
            request.question,
        ):
            return None
        info = local_runtime if local_runtime is not None else _local_python_runtime()
        return RuntimeEvidence(
            question=request.question,
            tier="tier_a",
            operation_id=local_solver.operation,
            runtime_sha256=info["runtime_sha256"],
        )
    from devharness_controlled.native_contracts import (
        limma_voom_kernel_images,
        pathway_kernel_images,
        sccoda_kernel_images,
        single_cell_kernel_images,
    )

    routes = {
        **{
            question: (route, single_cell_kernel_images())
            for question, (_, route) in _CELL_ROUTES.items()
        },
        "differential_expression": (
            "bulk_condition_contrast",
            limma_voom_kernel_images(),
        ),
        "differential_cell_state": (
            "bulk_condition_contrast",
            limma_voom_kernel_images(),
        ),
        "compositional_abundance": ("scrna_sccoda", sccoda_kernel_images()),
        "pathway_enrichment": ("rna_pathway_tested", pathway_kernel_images()),
        "overrepresentation": ("rna_pathway_tested", pathway_kernel_images()),
        "regulon_activity": ("rna_pathway_tested", pathway_kernel_images()),
        "qc_salmon": ("qc-salmon", limma_voom_kernel_images()),
    }
    entry = routes.get(request.question)
    if image_id is None or entry is None or image_id not in entry[1]:
        return None
    if request.question == "differential_expression" and (
        request.assay != "bulk_rna_seq"
        or request.entry_point
        not in {"count_matrix", "transcript_quantification", "normalized_expression"}
    ):
        return None
    return RuntimeEvidence(
        question=request.question,
        tier="tier_a",
        operation_id=entry[0],
        runtime_sha256=digest(_runtime_info(image_id)),
    )


def _operational_answer(
    runtime: RuntimeEvidence | None,
    *,
    claim_scope: str = "checked_observation",
) -> dict[str, Any]:
    if runtime is None:
        return {
            "status": "unavailable",
            "reason": "runtime_unavailable",
            "execution_route": None,
            "claim_scope": None,
        }
    return {
        "status": "ready",
        "reason": "prerequisites_satisfied",
        "execution_route": "tier_a",
        "claim_scope": claim_scope,
        "missing_measurements": (),
        "runtime": runtime.model_dump(mode="json"),
    }


def _missing_route(reason: str) -> dict[str, Any]:
    return {
        "status": "awaiting_information",
        "reason": reason,
        "execution_route": None,
        "claim_scope": None,
    }


def _verify_droplet_subject(source: str, declaration: dict[str, Any]) -> None:
    from rna_kernels.scrna_droplets import (
        RawDropletProvenance,
        verify_raw_droplet_input,
    )

    provenance = RawDropletProvenance(**declaration)
    verify_raw_droplet_input(
        Path(source),
        provenance,
        expected_provenance_sha256=provenance.sha256(),
    )


def _cell_answer(args, profiles, measurements, runtime):
    request = args.request
    family, route = _CELL_ROUTES[request.question]
    if (
        request.family_id != family
        or request.assay not in _CELL_ASSAYS
        or request.entry_point not in {"annotated_object", "count_matrix"}
        or request.requires_read_archive_download
    ):
        return None
    counts = [item for item in measurements if item.kind == "gene_counts"]
    if len(counts) != 1:
        return _missing_route("exact_verified_count_source_required")
    count = counts[0]
    if args.measurement is None or not (args.count_provenance or "").strip():
        return _missing_route("original_count_provenance_required")
    if not args.capture_units_confirmed:
        return _missing_route("capture_units_confirmation_required")
    if route == "scrna_droplets":
        if count.slot != "matrix" or args.raw_droplet_provenance is None:
            return _missing_route("verified_raw_droplet_provenance_required")
        declaration = args.raw_droplet_provenance
        if declaration.source_sha256 != count.artifact_sha256:
            return _missing_route("raw_droplet_provenance_source_mismatch")
        profile = next(
            item for item in profiles if item["sha256"] == count.artifact_sha256
        )
        _bounded_hdf5_probe(
            _verify_droplet_subject, profile["source"], declaration.model_dump()
        )
    else:
        required = [args.batch_column]
        if route == "scrna_lane_doublets":
            required.append(args.capture_column)
        verified = {
            item.slot
            for item in measurements
            if item.kind == "batch_membership"
            and item.artifact_sha256 == count.artifact_sha256
        }
        if count.slot == "matrix" or any(
            not col or f"obs/{col}" not in verified for col in required
        ):
            return _missing_route("verified_batch_or_capture_annotations_required")
    return {
        **_operational_answer(runtime),
        "measurement_basis": (
            "verified_droplet_storage_and_caller_declared_origin"
            if route == "scrna_droplets"
            else "verified_integer_storage_and_declared_count_provenance"
        ),
    }


def _answer(
    args: Answerability,
    source: dict[str, Any] | None = None,
    *,
    profiles: list[dict[str, Any]] | None = None,
    image_id: str | None = None,
) -> dict[str, Any]:
    if any(
        row["data_classification"] == "private"
        for row in [*(profiles or []), *([source] if source is not None else [])]
    ):
        raise OperationBlocked(_PRIVATE)
    observed = (
        _local_design(args.local_design, Path(source["source"]), source["sha256"])
        if args.local_design is not None and source is not None
        else None
    )
    design = args.design
    measurements = tuple(args.measurements)
    if profiles:
        measurements = _attested_measurements(args, profiles)
        design = None
        if observed and observed["status"] == "estimable" and args.local_design:
            design = DesignEvidence(
                biological_unit=args.local_design.biological_unit,
                receipt_sha256=digest(observed),
                observation_axis_sha256=observed["observation_axis_sha256"],
                estimability="estimable",
                independent_units_per_group=observed["independent_units_per_group"],
            )
    runtime = _answer_runtime(args.request, image_id) if profiles else None
    cell_route = (
        _cell_answer(args, profiles, measurements, runtime)
        if profiles and args.request.question in _CELL_ROUTES
        else None
    )
    result = assess_answerability(
        args.request,
        measurements=measurements,
        design=design,
        runtime=runtime,
    )
    answer: dict[str, Any] = {
        "status": result.status,
        "reason": result.reason,
        "missing_measurements": result.missing_measurements,
        "execution_route": result.execution_route,
        "claim_scope": result.claim_scope,
        "design_evidence_status": design.estimability if design else "not_supplied",
        "evidence_provenance": (
            "server_attested_local_inputs" if profiles else "unverified_caller_evidence"
        ),
        "registered_family_count": len(RNA_FAMILIES),
        **(
            {"runtime": result.runtime.model_dump(mode="json")}
            if result.runtime
            else {}
        ),
        **(
            {
                "attested_measurements": [
                    item.model_dump(
                        mode="json",
                        include={
                            "kind",
                            "slot",
                            "coordinate_frame",
                            "coordinate_units",
                        },
                        exclude_none=True,
                    )
                    for item in measurements
                ]
            }
            if profiles
            else {}
        ),
        **(
            {"measurement_basis": "verified_storage_and_declared_entry_point"}
            if profiles
            else {}
        ),
        **(cell_route or {}),
        "clinical_release_allowed": False,
    }
    if observed is not None:
        answer["local_design"] = observed
        if observed["status"] != "estimable" and result.status != "refused":
            answer.update(
                status="refused", reason=observed["reason"], execution_route=None
            )
    review = args.count1_confirmation
    if review and any(
        item.kind in _INTEGER_COUNT_KINDS
        and item.artifact_sha256 == review.source_sha256
        and item.slot == review.slot
        for item in measurements
    ):
        answer["count1_confirmation_sha256"] = digest(review.model_dump())
    return answer


def _fence_salmon_inputs(
    plugin, manifest: dict[str, Any], mapper: Path
) -> dict[str, str]:
    from devbench.analysis.fastq_contract import (
        MAX_FILE_BYTES,
        MAX_INPUT_BYTES,
        MAX_SAMPLES,
        MAX_TABLE_BYTES,
        _file_identity,
        _read_csv,
    )

    def resolve(values: Iterable[str]) -> list[Path]:
        paths = [_canonical_input(plugin.input_root, value) for value in values]
        if any(
            _clinical_name(part)
            for path in paths
            for part in path.relative_to(plugin.input_root).parts
        ):
            raise OperationBlocked(_PRIVATE)
        return paths

    def identify(paths: list[Path]) -> dict[str, str]:
        identities = {
            str(path): _file_identity(path, "salmon_preflight_input")["sha256"]
            for path in paths
        }
        hashes = set(identities.values())
        plugin._reject_embargoed(hashes)
        if plugin.jobs.has_private_input(hashes):
            raise OperationBlocked(_PRIVATE)
        return identities

    sheet, metadata = manifest["samplesheet"], manifest["metadata"]
    declared = resolve((sheet, metadata, *manifest["reference"].values(), str(mapper)))
    if any(path.stat().st_size > MAX_TABLE_BYTES for path in (*declared[:2], mapper)):
        raise ValueError("A Salmon metadata table exceeds its byte budget")
    declared_sizes = {path: path.stat().st_size for path in declared}
    if len(declared_sizes) != len(declared) or any(
        size > MAX_FILE_BYTES for size in declared_sizes.values()
    ):
        raise ValueError(
            "FASTQ inputs must be distinct and stay within the per-file budget"
        )
    initial = identify(declared[:1])
    samples, file_sha = _read_csv(Path(sheet), "samplesheet")
    if initial[sheet] != file_sha:
        raise OperationBlocked("input_snapshot_changed")
    reads = resolve(
        row[key] for row in samples for key in ("fastq_1", "fastq_2") if row[key]
    )
    paths = [*declared, *reads]
    sizes = {path: path.stat().st_size for path in paths}
    if (
        len(sizes) != len(paths)
        or len({row["sample"] for row in samples}) > MAX_SAMPLES
        or any(size > MAX_FILE_BYTES for size in sizes.values())
        or sum(sizes.values()) > MAX_INPUT_BYTES
    ):
        raise ValueError("FASTQ inputs exceed the checksum preflight budget")
    identities = {**initial, **identify(paths[1:])}
    _profile_metadata_identity(plugin, metadata, identities[metadata])
    return identities


def _profile_metadata_identity(plugin, path, expected_sha) -> None:
    profile = _profile(
        plugin.discovery,
        plugin.input_root,
        Input(
            path=path,
            data_classification="public",
            role="metadata",
        ),
    )
    if profile["data_classification"] == "private":
        raise OperationBlocked(_PRIVATE)
    if profile["sha256"] != expected_sha:
        raise OperationBlocked("input_snapshot_changed")


def _salmon_answer(plugin, args, profiles, runtime):
    from devbench.analysis.fastq_execution import _check_salmon_model, _salmon_inputs

    if args.salmon is None:
        return _missing_route("prestaged_salmon_manifest_and_map_required")
    parameters = args.salmon.model_dump(mode="json")
    frozen = _freeze_fastq(
        plugin.input_root,
        profiles,
        parameters,
        verify_members=lambda plan, mapper: _fence_salmon_inputs(plugin, plan, mapper),
    )
    if frozen["private"]:
        raise OperationBlocked(_PRIVATE)
    identities = {entry["sha256"] for entry in frozen["inventory"]}
    plugin._reject_embargoed(identities)
    if plugin.jobs.has_private_input(identities):
        raise OperationBlocked(_PRIVATE)
    _salmon_inputs(plugin.input_root, parameters["tx2gene"])
    if runtime is None:
        return _operational_answer(None)
    if not args.salmon.quantification_only:
        _check_salmon_model(frozen["preflight"], plugin.mounted_image_id, {"cpus": 2})
    result = _operational_answer(runtime)
    result["workflow_evidence_sha256"] = digest(
        {
            "inventory": frozen["inventory"],
            "manifest": frozen["source"],
            "parameters": parameters,
        }
    )
    return result


def _pathway_answer(plugin, args, profiles, runtime):
    from devbench.core.rna_pathway_analysis import (
        admit_pathway_request,
        eligible_members,
        testable_set,
    )
    from devbench.engine.tier_a_executor import TierAExecutor

    choice = args.pathways
    if not args.session_id or choice is None or choice.pathway_plan is None:
        return _missing_route("verified_differential_expression_and_gene_sets_required")
    method = choice.pathway_plan.method
    expected = {
        "pathway_enrichment": {"ora", "fgsea", "decoupleR"},
        "overrepresentation": {"ora"},
        "regulon_activity": {"decoupleR"},
    }
    if method not in expected[args.request.question] or (method == "ora") != (
        choice.foreground_q_max is not None
    ):
        return _missing_route("pathway_requires_explicit_method_and_foreground_rule")
    frozen = {
        "session_id": args.session_id,
        "profiles": profiles,
        "design": {"parameters": choice.model_dump(mode="json")},
    }
    flow, binding = _pathway_source(plugin.jobs, frozen)
    if runtime is None:
        return _operational_answer(None)
    executor = TierAExecutor(
        flow.ledger,
        allow_data_execution=False,
        mounted_image_id=plugin.mounted_image_id,
    )
    universe, scores = _pathway_universe(
        executor,
        binding["de_source"],
        q_max=choice.foreground_q_max,
        ranked=method != "ora",
    )
    requested = admit_pathway_request(
        choice.pathway_plan,
        universe=universe,
        verify_universe=lambda candidate: candidate == universe,
        foreground=_pathway_foreground(
            universe, scores, choice.pathway_plan.foreground_direction
        ),
        ranked_scores=scores if method != "ora" else None,
    )
    if not any(
        testable_set(requested, members) for members in eligible_members(requested)
    ):
        return _missing_route("no_testable_gene_sets_in_verified_background")
    result = _operational_answer(runtime, claim_scope="requires_scientific_review")
    if _pathway_source(plugin.jobs, frozen)[1] != binding:
        raise OperationBlocked("pathway_binding_changed")
    result["workflow_evidence_sha256"] = digest(
        {
            "binding": binding,
            "request": requested.model_dump(mode="json"),
        }
    )
    return result


def _stage_readiness_digest(
    stage: _CompositionStage | _PseudobulkStage,
    source: Path,
    selection,
    image_id: str | None,
) -> str:
    from devbench.core.scrna_composition import (
        _audit_composition,
        composition_from_h5ad,
    )
    from devbench.engine.mounted_kernel_oci import require_mounted_pipeline_runtime

    from .scrna_general_pseudobulk import stage_general_pseudobulk

    if isinstance(stage, _CompositionStage):
        data = composition_from_h5ad(source, stage.observations, qc_selection=selection)
        support = _audit_composition(data, stage.model)
        if support.reason:
            raise OperationBlocked(support.reason)
        return digest(data.model_dump(mode="json"))
    staged = stage_general_pseudobulk(
        source,
        stage.specification,
        source.parent / "capsule",
        qc_selection=selection,
    )
    if staged.pipeline is None:
        raise OperationBlocked("pseudobulk_capsule_held")
    require_mounted_pipeline_runtime(
        staged.pipeline,
        image_id,
        representation="raw_counts_h5",
    )
    return staged.provenance_sha256


def _retained_cell_answer(
    plugin,
    args: Answerability,
    runtime: RuntimeEvidence | None,
) -> dict[str, Any]:
    from devbench.engine.tier_a_executor import TierAExecutor

    from .adaptive_workflow import AdaptiveWorkflow
    from .scrna_qc_handoff import accepted_qc_selection, require_current_qc_selection
    from .thesis_workflow import ThesisWorkflow

    stage = (
        args.composition
        if args.request.question == "compositional_abundance"
        else args.pseudobulk
    )
    if args.session_id is None or stage is None:
        return _missing_route("current_qc_and_confirmed_biological_units_required")
    if runtime is None:
        return _operational_answer(None)
    ledger = _ledger(plugin.jobs.workspace)
    flow = AdaptiveWorkflow(ledger, args.session_id)
    executor = TierAExecutor(
        ledger,
        allow_data_execution=False,
        mounted_image_id=plugin.mounted_image_id,
    )
    aim = None
    if isinstance(stage, _CompositionStage):
        sha = stage.observations.source_sha256
        unit = stage.observations.biological_unit_column
    else:
        sha = stage.specification.source_sha256
        unit = stage.specification.unit_column
        defined_assay = {
            "scrna": "single_cell_rna_seq",
            "snrna": "single_nucleus_rna_seq",
        }
        if defined_assay[stage.specification.assay] != args.request.assay:
            return _missing_route("pseudobulk_assay_scope_mismatch")
        lifecycle = ThesisWorkflow(ledger, args.session_id).aims
        aim = lifecycle.require_ready(stage.aim_id)
    selection = accepted_qc_selection(
        flow, executor, stage.qc_step_id, source_sha256=sha
    )
    source_artifact = flow._read_projection(
        lambda db: next(
            (
                item[0]
                for key, item in flow._state(db).artifacts.items()
                if key.startswith("workbench-counts-") and item[0]["sha256"] == sha
            ),
            None,
        )
    )
    if (
        source_artifact is None
        or not _confirmed_unit(unit)
        or source_artifact["biological_unit"] != unit
        or source_artifact["representation"] != "raw_counts_h5ad"
    ):
        return _missing_route("confirmed_biological_units_required")
    executor._require_unprotected((sha,))
    with TemporaryDirectory(
        dir=plugin.jobs.workspace, prefix=".answerability-"
    ) as folder:
        source = Path(folder) / "counts.h5ad"
        with executor.training.open(sha) as retained, source.open("xb") as target:
            shutil.copyfileobj(retained, target, length=_H5_BLOCK)
        evidence = _bounded_hdf5_probe(
            _stage_readiness_digest,
            stage,
            source,
            selection,
            plugin.mounted_image_id,
        )
    require_current_qc_selection(flow, selection.binding)
    if aim is not None and isinstance(stage, _PseudobulkStage):
        if lifecycle.require_ready(stage.aim_id) != aim:
            raise OperationBlocked("pseudobulk_aim_binding_changed")
    result = _operational_answer(runtime, claim_scope="requires_scientific_review")
    result["workflow_evidence_sha256"] = digest(
        {
            "selection": selection.binding.model_dump(mode="json"),
            "preflight": evidence,
            "stage": stage.model_dump(mode="json"),
            "aim": aim.basis_sha256 if aim is not None else None,
        }
    )
    return result


def _answer_staged(
    plugin, args: Answerability, profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    request = args.request
    if not profiles or request.requires_read_archive_download:
        return {}
    if args.local_solver is not None:
        return _local_solver_answer(args, profiles)
    runtime = _answer_runtime(request, plugin.mounted_image_id)
    if request.question == "qc_salmon" and (
        request.family_id == 1
        and request.assay == "bulk_rna_seq"
        and request.entry_point == "reads"
    ):
        return _salmon_answer(plugin, args, profiles, runtime)
    if request.question in {
        "pathway_enrichment",
        "overrepresentation",
        "regulon_activity",
    } and (request.family_id == 6 and request.entry_point == "prior_results"):
        return _pathway_answer(plugin, args, profiles, runtime)
    if (
        {"compositional_abundance": 3, "differential_cell_state": 1}.get(
            request.question
        )
        == request.family_id
        and request.assay in _CELL_ASSAYS
        and request.entry_point in {"count_matrix", "annotated_object"}
    ):
        return _retained_cell_answer(plugin, args, runtime)
    return {}


def _local_solver_answer(
    args: Answerability,
    profiles: list[dict[str, Any]],
    runtime_info=None,
    *,
    offline_private: bool = False,
) -> dict[str, Any]:
    choice = args.local_solver
    if choice is None or args.request.requires_read_archive_download:
        raise OperationBlocked("local_solver_requires_staged_inputs")
    if not offline_private and any(
        profile["data_classification"] not in {"synthetic", "public"}
        for profile in profiles
    ):
        raise OperationBlocked(_PRIVATE)
    declared = local_request(choice)
    if (args.request.family_id, args.request.question) != (
        declared.family_id,
        declared.question,
    ):
        raise OperationBlocked("local_solver_question_mismatch")
    sources = bind_sources(choice, profiles)
    try:
        evidence = check_inputs(choice, sources)
    except ValueError as error:
        # Report a controlled category rather than arbitrary values from inputs.
        raise OperationBlocked("local_solver_input_or_design_invalid") from error
    measurements = _attested_solver_measurements(
        args, evidence, offline_private=offline_private
    )
    design = None
    if evidence.design_units:
        axis = measurements[0].observation_axis_sha256
        assert axis is not None
        design = DesignEvidence(
            biological_unit="donor_id",
            receipt_sha256=digest(
                {
                    "solver": choice.model_dump(mode="json"),
                    "observations": axis,
                    "independent_units_per_group": evidence.design_units,
                }
            ),
            observation_axis_sha256=axis,
            estimability="estimable",
            independent_units_per_group=evidence.design_units,
        )
    runtime = _answer_runtime(
        args.request, None, local_solver=choice, local_runtime=runtime_info
    )
    result = assess_answerability(
        args.request,
        measurements=measurements,
        design=design,
        runtime=runtime,
    ).model_dump(mode="json")
    return {
        **result,
        "evidence_provenance": "server_attested_local_inputs",
        "design_evidence_status": design.estimability
        if design
        else "not_required_by_registry",
        "attested_measurements": [
            item.model_dump(mode="json", exclude_none=True) for item in measurements
        ],
        "registered_family_count": len(RNA_FAMILIES),
    }


def _local_design(design: LocalDesign, source: Path, source_sha: str) -> dict[str, Any]:
    from devbench.analysis.fastq_contract import (
        FastqAdmissionError,
        _read_csv,
        validate_design,
    )

    rows, actual = _read_csv(source, "design metadata")
    if actual != source_sha:
        raise OperationBlocked("input_snapshot_changed")
    try:
        checked = validate_design(
            rows,
            formula=design.formula,
            contrast=design.contrast.model_dump(),
            biological_unit=design.biological_unit,
            batch_columns=design.batch_columns,
            analysis_scope="bulk",
        )
    except FastqAdmissionError as error:
        message = str(error).lower()
        if "rank deficiency" in message or "confounding" in message:
            reason = "rank_deficient_or_confounded_design"
        elif "biological" in message:
            reason = "biological_unit_or_replication_check_failed"
        else:
            reason = "design_validation_failed"
        return {"status": "refused", "reason": reason}
    return {
        "status": "estimable",
        "rank": checked["rank"],
        "columns": len(checked["design_columns"]),
        "residual_df": checked["residual_df"],
        "declared_units": checked["unit_count"],
        "metadata_sha256": source_sha,
        "observation_axis_sha256": digest(sorted(checked["sample_ids"])),
        "independent_units_per_group": tuple(checked["replication"].values()),
        "independence": "sample_labels_checked_against_declared_biological_units",
    }


def _runtime_info(image_id: str | None) -> dict[str, Any]:
    from devharness_controlled.native_contracts import KERNEL_LOCK, kernel_test_platform

    return {
        "target": "local_worker",
        "oci_digest": image_id if image_id and kernel_test_platform(image_id) else None,
        "architecture": kernel_test_platform(image_id) if image_id else None,
        "oci_runtime_verification": "required_at_launch" if image_id else None,
        "lockfile_sha256": hashlib.sha256(KERNEL_LOCK.read_bytes()).hexdigest(),
        "network_policy": "offline",
    }


def _bootstrap_workspace(workspace: Path, input_root: Path) -> None:
    from .workbench_ui import WorkbenchService

    if not (workspace / "service.json").exists():
        # Only the empty workspace bootstrap takes the Studio service lock.
        # Curated planner writes below use the same DB transaction authorities.
        app = WorkbenchService(workspace, [input_root], enable_scientific=True)
        app.close()
    config = json.loads(read_bounded_file(workspace / "service.json", 512 * 1024))
    if str(input_root) not in config.get("input_roots", []):
        raise ValueError("Input root does not match the existing Studio workspace")


def _ledger(workspace: Path, *, readonly: bool = False):
    from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger

    return WorkflowOperationLedger(
        workspace / "methods" / LEDGER_NAME, scientific=True, readonly=readonly
    )


def _roots(plan: dict[str, Any]) -> tuple[str, ...]:
    if "session_roots" in plan:
        return tuple(plan["session_roots"])
    inventory = plan.get("fastq", {}).get("inventory", [])
    return tuple(
        dict.fromkeys(row["sha256"] for row in [*plan["profiles"], *inventory])
    )


def _retain_local_h5ad_reference(
    methods, discovery: InputDiscovery, profile: dict[str, Any]
) -> dict[str, Any]:
    fresh = discovery.inspect(profile["source"], stream_selected_h5ad=True)
    if any(fresh[key] != profile[key] for key in ("sha256", "size_bytes")):
        raise OperationBlocked("input_snapshot_changed")
    methods._check_partition_access(profile["sha256"])
    reference = {
        "format": "devbench.local-input-reference/1",
        "file_type": "h5ad",
        "sha256": profile["sha256"],
        "size_bytes": profile["size_bytes"],
    }
    with TemporaryDirectory(dir=methods.root) as temporary:
        path = Path(temporary) / "source-reference.json"
        path.write_bytes(canonical(reference))
        return methods.import_artifact(
            path, artifact_type="rna_local_input_reference", actor=_ACTOR
        )


def _admit_session(
    workspace: Path, plan: dict[str, Any], discovery: InputDiscovery
) -> str:
    from devbench.analysis.method_validation import MAX_DATASET_BYTES
    from devbench.analysis.scientific_objects import ScientificObjectService
    from devbench.engine.methods import MethodService

    from .adaptive_workflow import AdaptiveWorkflow
    from .method_planner import ScientificMethodPlanner
    from .workbench_interactive import _retain_admission

    project = json.loads(read_bounded_file(workspace / "workspace.json", 512 * 1024))
    project_id = project["project"]["id"]
    methods = MethodService(workspace / "methods", reconcile=False)
    ScientificObjectService.migrate(methods.root, project_id, actor=_ACTOR)
    store = ScientificObjectService(methods.root, project_id, readonly=False)
    study = store.create_study(
        "Plugin RNA investigation",
        aim=plan["objective"],
        actor=_ACTOR,
        expected_revision=store.snapshot()["revision"],
    )
    members, locations = {}, {}
    for index, item in enumerate(plan["profiles"]):
        name = f"{item['role']}-{index}"
        if (
            item["observed"]["file_type"] == "h5ad"
            and item["size_bytes"] > MAX_DATASET_BYTES
        ):
            artifact = _retain_local_h5ad_reference(methods, discovery, item)
        else:
            artifact = methods.import_artifact(
                Path(item["source"]), artifact_type="file", actor=_ACTOR
            )
            if artifact["sha256"] != item["sha256"]:
                raise OperationBlocked("input_snapshot_changed")
        members[name] = {"artifact_id": artifact["artifact_id"]}
        locations[name] = item["source"]
    dataset = store.create_dataset(
        study["study_id"],
        "RNA inputs",
        {
            "members": members,
            "metadata": {
                "data_classification": plan["data_classification"],
            },
        },
        actor=_ACTOR,
        expected_revision=study["revision"],
        locations=locations,
    )
    native = methods.create_analysis("RNA workflow", actor=_ACTOR)
    task = {
        "scientific_objective": plan["objective"],
        "computational_task": "rna_workflow",
    }
    analysis = store.create_analysis(
        study["study_id"],
        "RNA workflow",
        {
            "task": task,
            "method_version_ids": [],
            "code_identity": {},
            "parameters": {},
            "validation_policy": {"status": "unplanned"},
        },
        actor=_ACTOR,
        expected_revision=study["revision"],
        legacy_analysis_id=native["analysis_id"],
    )
    planner = ScientificMethodPlanner(methods)
    state = planner.create(
        task,
        {
            "artifact_ids": [member["artifact_id"] for member in members.values()],
            "observed": {
                "input_inventory": (
                    []
                    if plan["data_classification"] == "private"
                    else [_inspect_projection(p) for p in plan["profiles"]]
                )
            },
            "inferred": {},
            "unknowns": [],
        },
        actor=_ACTOR,
        data_classification=plan["data_classification"],
        anchors={
            "project_id": project_id,
            "study_id": study["study_id"],
            "dataset_versions": [dataset["ref"]],
            "analysis_version": analysis["ref"],
        },
    )
    sid = state["session_id"]
    ledger = _ledger(workspace)
    flow = AdaptiveWorkflow(ledger, sid)
    broker = EvidenceBroker(ledger.scientific)
    roots = _roots(plan)
    policy = broker.register_policy(
        AccessPolicy(
            "plugin-session-" + sid,
            1,
            allowed_roots=roots,
            allow_outcomes=plan["data_classification"] != "private",
        )
    )
    lineage = _lineage(sid, plan, policy)
    _retain_admission(flow, {"adaptive_lineage": lineage}, policy)
    broker.register_context(
        lineage["context_id"],
        principal_id=_ACTOR,
        lineage_id=sid,
        role="scientist",
        phase="readback",
        policy_sha256=policy,
    )
    broker.register_source(
        lineage["source_id"],
        content_sha256=lineage["intake_sha256"],
        roots=roots,
        classification="structural",
    )
    return sid


def _lineage(
    sid: str, plan: dict[str, Any], policy: str | None = None
) -> dict[str, Any]:
    intake = plan.get("session_intake") or {
        "source_sha256": plan["profiles"][0]["sha256"],
        "design_sha256": digest(plan["design"]),
        "plan_id": plan["plan_id"],
    }
    intake_sha = digest(intake)
    return {
        "lineage_id": sid,
        "context_id": f"plugin-scientist-{sid}",
        "source_id": f"plugin-session-{sid}",
        "intake": intake,
        "intake_sha256": intake_sha,
        "exposure_basis_sha256": digest(
            {
                "intake_sha256": intake_sha,
                "policy_sha256": policy,
            }
        ),
    }


def _assessed_count_inputs(args: _Plan) -> set[str]:
    assessment = args.answerability
    if (
        assessment
        and assessment.request.entry_point in {"count_matrix", "annotated_object"}
        and (
            assessment.counts_layer == args.counts_layer
            or isinstance(args, _CellPlan)
            and assessment.request.question in _CELL_ROUTES
        )
        and assessment.request.question != "rna_velocity"
    ):
        return {item.path for item in assessment.inputs}
    return set()


def _check_plan_count_source(
    args: _Plan, profile: dict[str, Any], confirmed: str | None
) -> str | None:
    """Verify the selected count slot and return its canonical name."""
    from devbench.analysis.rna_input_set_contract import MAX_SOURCE_BYTES

    observed = profile["observed"]
    if observed["file_type"] == "h5ad":
        slot = args.counts_layer or (
            "counts" if "counts" in observed["layers"] else "X"
        )
        _, verified, _, evidence = _bounded_hdf5_probe(
            _h5ad_evidence, profile, (slot,), (), None, False, confirmed
        )
        if not verified:
            raise OperationBlocked(
                evidence.get("count1_reason", "raw_count_slot_unverified")
            )
        return slot if slot == "X" else "layers/" + slot
    if observed["file_type"] in _TABLE_TYPES:
        if not _table_axes(
            profile,
            verify_counts=True,
            count1_confirmed=confirmed == "feature_rows",
            maximum=MAX_SOURCE_BYTES,
        ):
            raise OperationBlocked("raw_count_slot_unverified")
        return "feature_rows"
    if observed.get("matrix_layout") in _H5_LAYOUTS:
        axes = _bounded_hdf5_probe(_tenx_evidence, profile, True, confirmed == "matrix")
        if not axes or "count1_reason" in axes:
            raise OperationBlocked(
                axes.get("count1_reason", "raw_count_slot_unverified")
            )
        return "matrix"
    return None


def _check_unassessed_plan_counts(
    args: _Plan, profiles: list[dict[str, Any]]
) -> dict[str, str]:
    """Check declared counts omitted from an optional question assessment."""
    if not isinstance(
        args, (_MatrixPlan, _CellPlan, _BulkPlan)
    ) or args.measurement not in {"raw_counts", "umi_counts", "non_umi_counts"}:
        return {}
    checked = _assessed_count_inputs(args)
    review = args.answerability.count1_confirmation if args.answerability else None
    confirmation = {}
    for profile in profiles:
        if profile["role"] != "expression" or (
            profile["source"] in checked and _inside_file_budget(profile)
        ):
            continue
        selected = (
            review.slot
            if review and review.source_sha256 == profile["sha256"]
            else None
        )
        verified = _check_plan_count_source(args, profile, selected)
        if review and selected is not None and verified == selected:
            confirmation["count1_confirmation_sha256"] = digest(review.model_dump())
    return confirmation


def _plan_answer(
    args: _Plan,
    profiles: list[dict[str, Any]],
    image_id: str | None,
) -> dict[str, Any] | None:
    confirmation = _check_unassessed_plan_counts(args, profiles)
    answerability = args.answerability
    if answerability is None:
        return None
    if (
        answerability.inputs
        and answerability.request.entry_point == "count_matrix"
        and args.measurement == "normalized_expression"
    ):
        raise OperationBlocked("answerability_measurement_declaration_conflict")
    if isinstance(args, _CellPlan) and answerability.request.question in _CELL_ROUTES:
        if args.counts_layer is None and any(
            row["observed"]["file_type"] == "h5ad" for row in profiles
        ):
            raise OperationBlocked("single_cell_explicit_counts_layer_required")
        for field in ("counts_layer", "measurement", "count_provenance"):
            nested = getattr(answerability, field)
            selected = getattr(args, field)
            if nested is not None and nested != selected:
                raise OperationBlocked("answerability_measurement_declaration_conflict")
        if args.measurement not in {"umi_counts", "non_umi_counts"}:
            raise OperationBlocked("single_cell_counts_measurement_required")
        provenance = getattr(args.parameters, "raw_droplet_provenance", None)
        if (
            answerability.raw_droplet_provenance is not None
            and answerability.raw_droplet_provenance != provenance
        ):
            raise OperationBlocked("single_cell_droplet_provenance_changed")
        answerability = answerability.model_copy(
            update={
                "counts_layer": args.counts_layer,
                "measurement": args.measurement,
                "count_provenance": args.count_provenance,
                "capture_units_confirmed": args.parameters.capture_units_confirmed,
                "raw_droplet_provenance": provenance,
            }
        )
    frozen = dict(zip((item.path for item in args.inputs), profiles, strict=True))
    metadata = (
        answerability.local_design.metadata if answerability.local_design else None
    )
    required = [*answerability.inputs, *([metadata] if metadata else [])]
    if any(item not in args.inputs for item in required):
        raise ValueError("Answerability evidence must be within the frozen input scope")
    result = _answer(
        answerability,
        frozen[metadata.path] if metadata else None,
        profiles=[frozen[item.path] for item in answerability.inputs],
        image_id=image_id,
    )
    return {**result, **confirmation}


def _plan_runtime_and_answer(
    args: _Plan,
    profiles: list[dict[str, Any]],
    classification: str,
    mounted_image_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, SolverChoice | None]:
    if classification == "private" and not isinstance(args, _LOCAL_PLAN_TYPES):
        return _runtime_info(mounted_image_id), None, None
    if not isinstance(args, _LOCAL_PLAN_TYPES):
        return (
            _runtime_info(mounted_image_id),
            _plan_answer(args, profiles, mounted_image_id),
            None,
        )
    choice = _SOLVER_ADAPTER.validate_json(
        canonical(args.model_dump(mode="json", include=_SOLVER_FIELDS))
    )
    supplied = args.answerability
    if supplied is not None and (
        supplied.design is not None
        or supplied.local_design is not None
        or supplied.measurements
        or (supplied.inputs and supplied.inputs != args.inputs)
        or (supplied.local_solver is not None and supplied.local_solver != choice)
    ):
        raise OperationBlocked("local_solver_answerability_scope_mismatch")
    runtime = _local_python_runtime()
    answer = _local_solver_answer(
        Answerability(
            request=supplied.request if supplied else local_request(choice),
            local_solver=choice,
        ),
        profiles,
        runtime,
        offline_private=classification == "private",
    )
    if answer["status"] != "ready":
        raise OperationBlocked(answer["reason"])
    return runtime, answer, choice


def _plan_input_pins(declarations, profiles):
    return [
        {
            "declaration": declaration,
            "sha256": profile["sha256"],
            "size_bytes": profile["size_bytes"],
        }
        for declaration, profile in zip(declarations, profiles, strict=True)
    ]


def _freeze_route_parameters(
    plan: dict[str, Any], args: _StandardPlan, choice: SolverChoice | None
) -> None:
    parameters = args.parameters.model_dump(mode="json", exclude_unset=True)
    if isinstance(args, _BulkPlan) and args.parameters.bulk_declarations:
        parameters = {
            "bulk_declarations": args.parameters.bulk_declarations.model_dump(
                mode="json", exclude_none=True
            )
        }
    if args.answerability and args.answerability.count1_confirmation:
        review = args.answerability.count1_confirmation.model_dump()
        if plan["data_classification"] != "private" and (
            plan.get("answerability") or {}
        ).get("count1_confirmation_sha256") != digest(review):
            raise OperationBlocked("count1_confirmation_scope_mismatch")
        plan["count1_confirmation"] = review
    plan["design"]["parameters"] = parameters
    if choice is not None:
        plan["local_solver"] = choice.model_dump(mode="json")


class Plugin:
    def __init__(
        self,
        workspace: Path,
        input_root: Path,
        mounted_image_id: str | None,
        literature_root: Path | None = None,
        *,
        allow_external_files: bool = False,
    ):
        self.input_root = directory(input_root)
        workspace = directory(workspace, exists=False)
        if workspace.is_relative_to(self.input_root) or self.input_root.is_relative_to(
            workspace
        ):
            raise ValueError("Input and output roots must be disjoint")
        _bootstrap_workspace(workspace, self.input_root)
        self.jobs = JobStore(workspace)
        self.discovery = InputDiscovery(
            [self.input_root], allow_external_files=allow_external_files
        )
        self.mounted_image_id = mounted_image_id
        self.literature_root = directory(literature_root) if literature_root else None
        from .plugin_bulk_receipts import publish_reviewed_bulk_receipts

        publish_reviewed_bulk_receipts(workspace)

    def _handoff(
        self,
        sid: str,
        view: str,
        *,
        mounted_image_id: str | None = None,
        allow_data_execution: bool = False,
    ) -> dict[str, Any]:
        return studio_handoff(
            self.jobs.workspace,
            self.input_root,
            sid,
            view,
            mounted_image_id=mounted_image_id,
            allow_data_execution=allow_data_execution,
        )

    def _reject_embargoed(self, identities: Iterable[str | None]) -> None:
        from devbench.engine.partition_authority import embargoed
        from devbench.engine.workflow_operations import (
            LEDGER_NAME,
            WorkflowOperationLedger,
        )

        path = self.jobs.workspace / "methods" / LEDGER_NAME
        if not path.exists() and not path.is_symlink():
            return
        ledger = WorkflowOperationLedger(path, readonly=True)

        def protected(db: Connection) -> bool:
            if (
                db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='scientific_records'"
                ).fetchone()
                is None
            ):
                return False
            return any(
                identity is not None and embargoed(db, identity)
                for identity in identities
            )

        if ledger._read(protected):
            raise OperationBlocked("adaptive_protected_partition_delivery_denied")

    def _profiles(
        self, inputs: list[Input], *, allow_retained_private: bool = False
    ) -> list[dict[str, Any]]:
        if len({item.path for item in inputs}) != len(inputs):
            raise ValueError("Each input may be supplied only once")
        profiles = [_profile(self.discovery, self.input_root, item) for item in inputs]
        self._reject_embargoed(item["sha256"] for item in profiles)
        if not allow_retained_private and self.jobs.has_private_input(
            {item["sha256"] for item in profiles}
        ):
            raise OperationBlocked(_PRIVATE)
        return profiles

    def inspect(self, args: Inspect) -> dict[str, Any]:
        profiles = self._profiles(args.inputs)
        if any(item["data_classification"] == "private" for item in profiles):
            raise OperationBlocked(_PRIVATE)
        return {"inputs": [_inspect_projection(item) for item in profiles]}

    def answer(self, args: Answerability) -> dict[str, Any]:
        if args.local_solver is not None:
            if (
                args.design is not None
                or args.local_design is not None
                or args.measurements
            ):
                raise OperationBlocked("local_solver_requires_server_attested_design")
            profiles = self._answer_profiles(args)
            if not profiles:
                raise OperationBlocked("local_solver_requires_staged_inputs")
            return _answer_staged(self, args, profiles) or _local_solver_answer(
                args, profiles
            )
        design = args.local_design
        source = (
            _profile(self.discovery, self.input_root, design.metadata)
            if design
            else None
        )
        if source is not None:
            self._reject_embargoed((source["sha256"],))
            if self.jobs.has_private_input({source["sha256"]}):
                raise OperationBlocked(_PRIVATE)
        profiles = self._answer_profiles(args)
        result = _answer(
            args, source, profiles=profiles, image_id=self.mounted_image_id
        )
        if (
            result["status"] == "refused"
            and result["reason"] != "unregistered_question"
        ):
            return result
        return {**result, **_answer_staged(self, args, profiles)}

    def _answer_profiles(self, args: Answerability) -> list[dict[str, Any]]:
        if args.session_id is None:
            return self._profiles(args.inputs)
        plan = self._allow_external(args.session_id)
        admitted = [Input.model_validate(row) for row in plan["input_declarations"]]
        if args.inputs and args.inputs != admitted:
            raise OperationBlocked("session_input_scope_mismatch")
        profiles = self._profiles(admitted)
        if [row["sha256"] for row in profiles] != [
            row["sha256"] for row in plan["profiles"]
        ]:
            raise OperationBlocked("input_snapshot_changed")
        return profiles

    def _plan_inputs(
        self,
        args: _ScopedPlan,
        *,
        private_only: bool = False,
        allow_offline_native: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        previous = self.jobs.plan(args.session_id) if args.session_id else None
        if previous is not None:
            previous_private = previous["data_classification"] == "private"
            if previous_private and not (private_only or allow_offline_native):
                raise OperationBlocked(_PRIVATE)
            if private_only and not previous_private:
                raise OperationBlocked("session_input_scope_mismatch")
            private_only = previous_private
            if not args.inputs:
                args.inputs = [
                    Input.model_validate(row) for row in previous["input_declarations"]
                ]
        if not args.inputs:
            raise OperationBlocked("plan_input_required")
        inspected = (
            [
                item.model_copy(update={"data_classification": "private"})
                for item in args.inputs
            ]
            if private_only
            else args.inputs
        )
        declared_for_private_execution = allow_offline_native and any(
            item.data_classification not in {"public", "synthetic"}
            or item.clinical_metadata
            for item in inspected
        )
        profiles = self._profiles(
            inspected,
            allow_retained_private=private_only or declared_for_private_execution,
        )
        if any(row["sha256"] is None for row in profiles):
            raise OperationBlocked("input_snapshot_unavailable")
        if previous is not None:
            if not private_only and any(
                row["data_classification"] == "private" for row in profiles
            ):
                raise OperationBlocked(_PRIVATE)
            pins = _plan_input_pins(
                [item.model_dump(mode="json") for item in args.inputs], profiles
            )
            previous_pins = _plan_input_pins(
                previous["input_declarations"], previous["profiles"]
            )
            allowed = previous.get("session_inputs", previous_pins)
            if previous["input_root"] != str(self.input_root) or (
                any(pin not in allowed for pin in pins)
                if args.depends_on
                else pins != previous_pins
            ):
                raise OperationBlocked("session_input_scope_mismatch")
        return profiles, previous

    def _retain_plan_scope(self, plan, args, previous):
        if previous is not None:
            plan["session_id"] = previous["session_id"]
            plan["session_intake"] = _lineage(previous["session_id"], previous)[
                "intake"
            ]
            if not set(_roots(plan)) <= set(_roots(previous)):
                raise OperationBlocked("session_input_scope_mismatch")
            plan["session_roots"] = list(_roots(previous))
            plan["session_inputs"] = previous.get("session_inputs") or _plan_input_pins(
                previous["input_declarations"], previous["profiles"]
            )
        for dependency in args.depends_on:
            prior = self.jobs.plan(args.session_id, dependency.plan_id)
            if prior.get("graph_runtime") is None:
                raise OperationBlocked("graph_dependency_not_registered")
            if dependency.plan_sha256 != digest(prior):
                raise OperationBlocked("graph_dependency_plan_identity_mismatch")
        if args.depends_on:
            plan["dependencies"] = [item.model_dump() for item in args.depends_on]

    def plan(self, request: Plan) -> dict[str, Any]:
        args = request.root
        if isinstance(args, _SharedMotorUMAPPlan):
            from .plugin_motor_umap import plan_shared_motor_umap

            return plan_shared_motor_umap(self, args)
        if isinstance(args, _MotorProgramComparisonPlan):
            return self._plan_motor_programs(args)
        if isinstance(args, _RegisteredExploratoryPlan):
            return self._plan_registered_method(args)
        return self._plan_standard(args)

    def _plan_registered_method(
        self, args: _RegisteredExploratoryPlan
    ) -> dict[str, Any]:
        from .plugin_registered_method import freeze_registered_method

        if args.depends_on:
            raise OperationBlocked("registered_method_requires_verified_input_versions")
        profiles, previous = self._plan_inputs(args)
        if previous is None or previous["data_classification"] not in {
            "public",
            "synthetic",
        }:
            raise OperationBlocked(
                "registered_method_public_or_synthetic_scope_required"
            )
        self._allow_external(args.session_id)
        plan_id = uuid4().hex
        binding = freeze_registered_method(
            self.jobs.workspace,
            args.session_id,
            plan_id=plan_id,
            method_version_id=args.method_version_id,
            step_name=args.step_name,
            parameters=args.parameters,
            input_versions=args.input_versions,
        )
        frozen = {
            "format": "devbench.plugin-plan/1",
            "plan_id": plan_id,
            "operation": args.operation,
            "objective": args.objective,
            "input_root": str(self.input_root),
            "allow_external_input_files": self.discovery.allow_external_files,
            "data_classification": previous["data_classification"],
            "profiles": profiles,
            "input_declarations": [
                item.model_dump(mode="json") for item in args.inputs
            ],
            "design": {"parameters": args.parameters},
            "runtime": _runtime_info(None),
            "mounted_image_id": None,
            "registered_method": binding,
        }
        self._retain_plan_scope(frozen, args, previous)
        pid, sha = self.jobs.freeze(frozen)
        return {
            "status": "planned",
            "execution_route": "mcp",
            "session_id": args.session_id,
            "plan_id": pid,
            "plan_sha256": sha,
            **self._handoff(args.session_id, "workflows"),
        }

    def _plan_motor_programs(self, args: _MotorProgramComparisonPlan) -> dict[str, Any]:
        from .plugin_graph import registered_plugin_runtime
        from .plugin_motor_programs import (
            motor_program_runtime,
            validate_motor_program_profiles,
        )

        profiles, previous = self._plan_inputs(args, private_only=True)
        validate_motor_program_profiles(profiles)
        frozen = {
            "format": "devbench.plugin-plan/1",
            "plan_id": uuid4().hex,
            "operation": args.operation,
            "objective": args.objective,
            "input_root": str(self.input_root),
            "allow_external_input_files": self.discovery.allow_external_files,
            "data_classification": "private",
            "profiles": profiles,
            "input_declarations": [
                item.model_dump(mode="json") for item in args.inputs
            ],
            "design": {"parameters": {}},
            "runtime": motor_program_runtime(),
            "mounted_image_id": None,
        }
        self._retain_plan_scope(frozen, args, previous)
        frozen["graph_runtime"] = registered_plugin_runtime(frozen)
        sid = args.session_id or _admit_session(
            self.jobs.workspace, frozen, self.discovery
        )
        frozen["session_id"] = sid
        pid, sha = self.jobs.freeze(frozen)
        return {
            "status": "local_studio_only",
            "execution_route": "offline_studio",
            "session_id": sid,
            "plan_id": pid,
            "plan_sha256": sha,
            **self._handoff(sid, "workflows", allow_data_execution=True),
        }

    def _plan_standard(self, args: _StandardPlan) -> dict[str, Any]:
        offline_native = isinstance(args, _LOCAL_PLAN_TYPES)
        profiles, previous = self._plan_inputs(
            args, allow_offline_native=offline_native
        )
        classes = {profile["data_classification"] for profile in profiles}
        classification = (
            "private"
            if "private" in classes
            else ("public" if "public" in classes else "synthetic")
        )
        runtime_info, answer, local_choice = _plan_runtime_and_answer(
            args,
            profiles,
            classification,
            self.mounted_image_id,
        )
        frozen: dict[str, Any] = {
            "format": "devbench.plugin-plan/1",
            "plan_id": uuid4().hex,
            "operation": args.operation,
            "objective": args.objective,
            "input_root": str(self.input_root),
            "allow_external_input_files": self.discovery.allow_external_files,
            "data_classification": classification,
            "profiles": profiles,
            "input_declarations": [
                item.model_dump(mode="json") for item in args.inputs
            ],
            "design": args.model_dump(
                mode="json",
                include={
                    "measurement",
                    "count_provenance",
                    "counts_layer",
                    "observation_column",
                    "batch_column",
                    "condition_column",
                },
            ),
            "references": [
                {"citation": citation, "provenance": "unverified_caller_citation"}
                for citation in args.references
            ],
            "answerability": answer,
            "runtime": runtime_info,
            "mounted_image_id": self.mounted_image_id,
        }
        _freeze_route_parameters(frozen, args, local_choice)
        if args.operation == "qc-salmon" and classification != "private":
            frozen["runtime"]["network_policy"] = (
                "Local data only; analysis containers offline; pinned engine bootstrap "
                "may use network"
            )
            frozen["fastq"] = _freeze_fastq(
                self.input_root, profiles, frozen["design"]["parameters"]
            )
            if frozen["fastq"]["private"]:
                frozen["data_classification"] = classification = "private"
        if (
            previous is not None
            and classification == "private"
            and (not offline_native or previous["data_classification"] != "private")
        ):
            raise OperationBlocked(_PRIVATE)
        self._retain_plan_scope(frozen, args, previous)
        if (
            offline_native
            or classification != "private"
            and args.operation in {"matrix_audit", "qc-salmon"}
        ):
            from .plugin_graph import registered_plugin_runtime

            frozen["graph_runtime"] = registered_plugin_runtime(frozen)
        elif args.depends_on:
            raise OperationBlocked("graph_dependency_executor_unavailable")
        if args.operation == "pathways" and classification != "private":
            _, frozen["pathway_binding"] = _pathway_source(self.jobs, frozen)
        if len(canonical(frozen)) > MAX_DOCUMENT_BYTES - 128:
            raise ValueError("Plan exceeds its immutable document budget")
        sid = args.session_id or _admit_session(
            self.jobs.workspace, frozen, self.discovery
        )
        frozen["session_id"] = sid
        plan_id, sha = self.jobs.freeze(frozen)
        identity = {"session_id": sid, "plan_id": plan_id, "plan_sha256": sha}
        handoff = self._handoff(sid, "workflows")
        if classification == "private":
            if offline_native:
                return {
                    "status": "local_studio_only",
                    "execution_route": "offline_studio",
                    **identity,
                    **self._handoff(sid, "workflows", allow_data_execution=True),
                }
            return {
                "status": "local_studio_only",
                "execution_route": None,
                "execution_blocker": "private_plan_execution_unavailable",
                **identity,
                **handoff,
            }
        local_review = isinstance(args, _BulkPlan) or (
            isinstance(args, _CellPlan)
            and isinstance(args.parameters, _PseudobulkParameters)
        )
        return {
            "status": "planned",
            "execution_route": "mcp_then_offline_studio" if local_review else "mcp",
            **identity,
            "runtime": frozen["runtime"],
            "answerability": frozen["answerability"],
            "references": frozen["references"],
            "inputs": [_inspect_projection(item) for item in profiles],
            "steps": [{"name": args.operation, "state": "planned"}],
            "clinical_release_allowed": False,
            **handoff,
        }

    def execute(self, args: Execute) -> dict[str, Any]:
        plan = self._allow_external(args.session_id, args.plan_id)
        if plan.get("dependencies"):
            from .plugin_graph import require_plugin_dependencies

            if digest(plan) != args.plan_sha256:
                raise OperationBlocked("plan_identity_mismatch")
            require_plugin_dependencies(self.jobs, plan)
        row = self.jobs.launch(args.session_id, args.plan_id, args.plan_sha256)
        return self._status(row, plan)

    def _allow_external(
        self,
        sid: str,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        plan = self.jobs.plan(sid, plan_id)
        if plan["data_classification"] not in {
            "public",
            "synthetic",
        } or self.jobs.has_private_input(set(_roots(plan))):
            raise OperationBlocked(_PRIVATE)
        return plan

    def _status(self, row: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        stage = json.loads(row["stage_json"])
        completed = row["status"] == "completed"
        recorded_tasks = stage.get("tasks", [])
        elapsed = (row["finished_at"] or time.time()) - row["started_at"]
        projection: dict[str, Any] = {
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "status": row["status"],
            "compute_target": "local_worker",
            "progress": {"completed": int(completed), "total": 1},
            "tasks": [
                {
                    "step": stage.get("step", "local_worker"),
                    "target": "local_worker",
                    "duration_seconds": max(0, round(elapsed, 2)),
                    "status": row["status"],
                }
            ],
            "warnings": [row["error_text"]] if row["error_text"] else [],
            "generation": 1,
            "stale": row["status"] == "interrupted",
            **self._handoff(
                row["session_id"], "overview" if completed else "workflows"
            ),
        }
        if recorded_tasks:
            projection["tasks"] = recorded_tasks
            projection["progress"] = {
                "completed": sum(
                    task["status"] == "succeeded" for task in recorded_tasks
                ),
                "total": stage["step_total"],
            }
        if row["status"] == "awaiting_review":
            projection.update(_bulk_waiting(self.jobs.workspace, row["session_id"]))
            if projection["status"] == "awaiting_review":
                projection.update(
                    self._handoff(
                        row["session_id"],
                        "workflows",
                        mounted_image_id=plan["runtime"]["oci_digest"],
                    )
                )
        trace = stage.get("nextflow_attempt")
        if trace:
            from devbench.analysis import fastq_execution

            parse_trace = getattr(fastq_execution, "read_nextflow_trace", None)
            if parse_trace is not None:
                trace_path = Path(trace) / "nextflow-trace.tsv"
                run_root = self.jobs.workspace / "runs" / row["run_id"]
                if trace_path.is_relative_to(run_root) and trace_path.is_file():
                    try:
                        trace_values = parse_trace(trace_path)
                    except (OSError, ValueError):
                        projection["warnings"].append("nextflow_trace_unavailable")
                        return projection
                    projection["progress"] = {
                        key: trace_values[key] for key in ("completed", "total")
                    }
                    projection["tasks"] = [
                        {
                            "step": entry["process"],
                            "target": entry["tag"] or "local_nextflow",
                            "duration": entry["realtime"],
                            "status": entry["status"],
                        }
                        for entry in trace_values["tasks"]
                    ]
        return projection

    def status(self, args: Run) -> dict[str, Any]:
        self._allow_external(args.session_id)
        row = self.jobs.read_run(args.session_id, args.run_id)
        return self._status(row, self._allow_external(args.session_id, row["plan_id"]))

    def receipt(self, args: Run) -> dict[str, Any]:
        self._allow_external(args.session_id)
        row = self.jobs.read_run(args.session_id, args.run_id)
        plan = self._allow_external(args.session_id, row["plan_id"])
        ledger = _ledger(self.jobs.workspace)
        broker = EvidenceBroker.open_existing(ledger.scientific)
        lineage = _lineage(args.session_id, plan)
        source = f"plugin-receipt-{args.run_id}"
        current = None
        if row["status"] == "awaiting_review" and plan["operation"] == "bulk_de":
            receipt, source, current = _read_reviewed_bulk_receipt(
                self.jobs.workspace, plan, row
            )
        elif row["status"] == "completed":
            receipt = json.loads(row["receipt_json"])
            _verify_replay(
                self.jobs.workspace / "runs" / args.run_id / "replay_bundle",
                row["plan_sha256"],
                receipt,
            )
            _verify_receipt_results(self.jobs.workspace, args.run_id, plan, receipt)
            current = _adaptive_receipt_guard(ledger, plan, receipt)
        else:
            raise OperationBlocked("run_receipt_unavailable")
        retained = ledger._read(lambda db: broker._source(db, source))
        if retained["content_sha256"] != digest(receipt):
            raise OperationBlocked("receipt_identity_mismatch")
        broker.check_delivery(
            lineage["context_id"],
            lineage["source_id"],
            lineage_id=args.session_id,
            role="scientist",
        )
        context_id = _receipt_context(
            broker, plan, lineage, retained["tags"], lookup_only=True
        )
        broker.check_delivery(
            context_id,
            source,
            lineage_id=args.session_id,
            role="scientist",
        )
        released = broker.delivery(context_id, source).release(
            receipt,
            _guard=current,
        )
        return _bounded_receipt(
            {**released, **self._handoff(args.session_id, "overview")}
        )

    def aims(self, args: ResearchAim) -> dict[str, Any]:
        self._allow_external(args.root.session_id)
        return _manage_aim(
            self,
            SimpleNamespace(
                session_id=args.root.session_id,
                action=args.root.action,
                request=args.root.request.model_dump(mode="json", exclude_unset=True),
            ),
        )


def _adaptive_receipt_guard(ledger, plan, receipt):
    from devbench.engine.partition_authority import embargoed

    from .adaptive_workflow import AdaptiveWorkflow

    if plan["operation"] not in {"single_cell", "pathways"}:
        return None
    parameters = plan["design"]["parameters"]
    sid = (
        parameters.get("source_session_id") if plan["operation"] == "pathways" else None
    ) or plan["session_id"]
    flow = AdaptiveWorkflow(ledger, sid)

    def require_current(db: Connection) -> None:
        state = flow._state(db)
        for retained in receipt.get("tool_receipts", ()):
            step_id = retained["step"]
            if step_id == "pseudobulk_staging":
                if embargoed(db, retained["receipt_sha256"]):
                    raise OperationBlocked(
                        "adaptive_protected_partition_delivery_denied"
                    )
                continue
            step = state.nodes.get(step_id)
            outcome = state.outcome(step_id) if step is not None else None
            if (
                step is None
                or step.branch_id in state.retired
                or not outcome
                or outcome["status"] != "succeeded"
                or outcome["receipt_sha256"] != retained["receipt_sha256"]
                or flow._held_ancestry(state, step_id)
            ):
                raise OperationBlocked("adaptive_receipt_no_longer_current")
            for artifact in outcome["outputs"].values():
                if artifact["version_id"] in state.stale_versions:
                    raise OperationBlocked("adaptive_receipt_no_longer_current")
                if embargoed(db, artifact["sha256"]):
                    raise OperationBlocked(
                        "adaptive_protected_partition_delivery_denied"
                    )
                flow._check_derived_delivery(db, state, artifact["version_id"])

    return require_current


@contextmanager
def studio_job_readback(workspace: Path) -> Iterator[Connection | None]:
    """Open committed JobStore rows without initializing or reconciling jobs."""
    path = workspace / "plugin-jobs.sqlite3"
    for member in (
        path,
        path.with_suffix(".sqlite3-wal"),
        path.with_suffix(".sqlite3-shm"),
    ):
        try:
            info = member.lstat()
        except FileNotFoundError:
            if member == path:
                yield None
                return
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise PermissionError(
                "Plugin job storage requires owner-only regular files"
            )
    require_protected_storage(workspace)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        yield db


def studio_plan_scope(db: Connection, plan: dict[str, Any]) -> bool:
    """Apply the Plugin's private-input lineage restriction to Studio readback."""
    if plan["data_classification"] not in {"public", "synthetic"}:
        return False
    return (
        db.execute(
            """
        SELECT 1 FROM plugin_plans AS plan,
                      json_each(plan.plan_json, '$.profiles') AS item
        WHERE json_extract(plan.plan_json, '$.data_classification') = 'private'
          AND json_extract(item.value, '$.sha256') IN (SELECT value FROM json_each(?))
        LIMIT 1
        """,
            (canonical(_roots(plan)).decode(),),
        ).fetchone()
        is None
    )


def studio_evidence_digests(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from studio_evidence_digests(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from studio_evidence_digests(item)
    elif isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        yield value


def _verify_receipt_results(workspace, rid, plan, receipt) -> bool:
    graph_verified = False
    if plan["operation"] in LOCAL_OPERATIONS:
        _verify_solver_result(workspace, rid, receipt)
    if plan["operation"] == "registered_exploratory":
        from .plugin_registered_method import verify_registered_method_result

        verify_registered_method_result(workspace, rid, plan, receipt)
        graph_verified = True
    if plan.get("graph_runtime") is not None:
        from .plugin_graph import verify_registered_plugin_result

        verify_registered_plugin_result(workspace, rid, plan, receipt)
        graph_verified = True
    return graph_verified


def _studio_receipt(
    workspace: Path,
    broker: EvidenceBroker,
    plan: dict[str, Any],
    row: sqlite3.Row,
    blocked_hashes: frozenset[str],
) -> dict[str, Any]:
    from devbench.engine.partition_authority import embargoed

    if plan["data_classification"] not in {"public", "synthetic"}:
        raise OperationBlocked(_PRIVATE)
    sid, rid = plan["session_id"], row["run_id"]
    if not isinstance(rid, str) or re.fullmatch(r"[0-9a-f]{32}", rid) is None:
        raise OperationBlocked("unknown_run")
    source = f"plugin-receipt-{rid}"
    ledger = broker.authority.ledger
    lineage = _lineage(sid, plan)
    graph_verified = False
    if row["status"] == "awaiting_review" and plan["operation"] == "bulk_de":
        receipt, source, current = _read_reviewed_bulk_receipt(workspace, plan, row)
    else:
        receipt = json.loads(row["receipt_json"])
        _verify_replay(
            workspace / "runs" / rid / "replay_bundle",
            row["plan_sha256"],
            receipt,
        )
        graph_verified = _verify_receipt_results(workspace, rid, plan, receipt)
        current = _adaptive_receipt_guard(ledger, plan, receipt)
    identities = set(studio_evidence_digests(receipt))
    exposure = receipt
    if graph_verified:
        # Studio's broker-scoped research projection carries structural receipt
        # identities, but never publishes the raw graph output artifacts.
        exposure = {
            key: value
            for key, value in receipt.items()
            if key
            not in {"result_artifact", "registered_method_outputs", "graph_execution"}
        }
    if not blocked_hashes.isdisjoint(studio_evidence_digests(exposure)):
        raise PermissionError("Plugin receipt references protected evidence")
    retained = ledger._read(lambda db: broker._source(db, source))
    if retained["content_sha256"] != digest(receipt) or set(retained["roots"]) != set(
        _roots(plan)
    ):
        raise OperationBlocked("receipt_identity_mismatch")
    broker.check_delivery(
        lineage["context_id"],
        lineage["source_id"],
        lineage_id=sid,
        role="scientist",
    )
    context_id = _receipt_context(
        broker, plan, lineage, retained["tags"], lookup_only=True
    )
    broker.check_delivery(context_id, source, lineage_id=sid, role="scientist")
    fields = {
        "metrics",
        "summary_tables",
        "tool_receipts",
        "limitations",
        "warnings",
        "replay_bundle_sha256",
        "count1_confirmation_sha256",
        "figure_preview",
        "probability_bounds_status",
        "nextflow_trace",
        "graph_execution",
    }
    projection = _bounded_receipt(
        {key: receipt[key] for key in fields if key in receipt}
    )

    def guard(db: Connection) -> None:
        if any(embargoed(db, identity) for identity in identities):
            raise PermissionError("Plugin receipt references protected evidence")
        if current is not None:
            current(db)

    return broker.delivery(context_id, source).release(projection, _guard=guard)


def studio_run_projection(
    workspace: Path,
    broker: EvidenceBroker,
    plan: dict[str, Any],
    row: sqlite3.Row,
    blocked_hashes: frozenset[str],
) -> dict[str, Any]:
    from devbench.analysis.scientific_publication import safe_diagnostic

    result = {
        "run_id": row["run_id"],
        "plan_id": row["plan_id"],
        "status": row["status"],
        "operation": plan["operation"],
    }
    if row["error_text"]:
        result["warnings"] = [safe_diagnostic(row["error_text"])]
    bulk = row["status"] == "awaiting_review" and plan["operation"] == "bulk_de"
    if row["status"] == "completed" or bulk:
        try:
            result.update(_studio_receipt(workspace, broker, plan, row, blocked_hashes))
        except (
            OperationBlocked,
            PermissionError,
            OSError,
            KeyError,
            TypeError,
            ValueError,
        ):
            result["warnings"] = ["verified_receipt_unavailable"]
        else:
            result["status"] = "completed"
    return result


def _retained_nextflow_trace(
    workspace: Path, rid: str, attempt: str | None
) -> str | None:
    from devbench.analysis.fastq_execution import read_nextflow_trace

    if attempt is None:
        return None
    path = Path(attempt) / "nextflow-trace.tsv"
    if not path.is_relative_to(workspace / "runs" / rid) or path.resolve() != path:
        return None
    try:
        tasks = read_nextflow_trace(path)["tasks"]
    except (OSError, ValueError):
        return None
    if not tasks:
        return None
    columns = ("process", "status", "realtime")
    return "\n".join(
        [
            "\t".join(columns),
            *(
                "\t".join(str(task[key])[:120] for key in columns)
                for task in tasks[:25]
            ),
        ]
    )


def _receipt_tags(receipt: dict[str, Any]) -> tuple[str, ...]:
    if receipt["probability_bounds_status"] == "verified" or any(
        table["caption"] == "Composition effects"
        for table in receipt.get("summary_tables", ())
    ):
        return ("inferential_statistics",)
    return ()


def _receipt_context(broker, plan, lineage, tags, *, lookup_only: bool = False) -> str:
    if "inferential_statistics" not in tags:
        return lineage["context_id"]
    if plan["data_classification"] not in {"public", "synthetic"}:
        raise OperationBlocked(_PRIVATE)
    sid = lineage["lineage_id"]
    parent, original = broker.authority.ledger._read(
        lambda db: broker._context_policy(db, lineage["context_id"]),
    )
    context_id = f"plugin-statistics-v2-{sid}-{parent['policy_sha256'][:12]}"
    if not original.allow_outcomes or set(original.allowed_roots) != set(_roots(plan)):
        raise OperationBlocked("receipt_statistics_scope_unavailable")
    policy = replace(original, policy_id=context_id, allow_statistics=True)
    if lookup_only:
        context, retained = broker.authority.ledger._read(
            lambda db: broker._context_policy(db, context_id)
        )
        if retained != policy or context["parents"] != [lineage["context_id"]]:
            raise OperationBlocked("receipt_statistics_scope_unavailable")
        return context_id
    policy_sha = broker.register_policy(policy)
    return broker.register_context(
        context_id,
        principal_id="plugin-verified-receipt-reader",
        lineage_id=sid,
        role="scientist",
        phase="readback",
        policy_sha256=policy_sha,
        parents=(lineage["context_id"],),
    )


class _RetainedParameters(PartitionVault):
    """Recomputing a bulk preflight on read must match an existing vault object."""

    def put(self, raw: bytes) -> str:
        identity = hashlib.sha256(raw).hexdigest()
        with self.open(identity):
            return identity


def _bulk_verified_fit(
    ledger,
    plan,
    run_id: str,
    step_sha256: str | None = None,
) -> tuple[dict[str, Any], Callable[[Connection], None]]:
    import pyarrow.parquet as pq

    from devbench.core.contrast_design import TIER_A_BULK_OUTPUT
    from devbench.engine.tier_a_executor import TierAExecutor

    from .adaptive_workflow import AdaptiveWorkflow, request_digest
    from .workbench_interactive import _OPERATOR_BULK_STEP, operator_bulk_actions

    sid = plan["session_id"]
    flow = AdaptiveWorkflow(ledger, sid)
    step = _OPERATOR_BULK_STEP
    with flow._read_connection() as db:
        state = flow._state(db)
        if step_sha256 is not None and (
            step not in state.nodes
            or digest(state.nodes[step].model_dump(mode="json")) != step_sha256
        ):
            raise OperationBlocked("bulk_fit_subject_changed")
        outcome = state.outcome(step)
        if not outcome or outcome["status"] != "succeeded":
            raise OperationBlocked("bulk_fit_requires_current_studio_review_and_run")
        generation = state.generations[step]
        attempt = outcome["attempt_id"]
        envelope = state.dispatches[attempt]["envelope"]
    executor = TierAExecutor(
        ledger, allow_data_execution=False, mounted_image_id=plan["mounted_image_id"]
    )
    if ledger.readonly:
        executor._parameters = _RetainedParameters(executor._parameters.root)
    actions = operator_bulk_actions(flow, executor, planner=None, step_id=step)
    actions.prepare_research_admission(flow, step)
    flow.review_admission = actions.adaptive_review_admission
    artifact = flow.current_use(
        step, TIER_A_BULK_OUTPUT, purpose="query", use="claim", generation=generation
    )
    retained = executor.load_receipt(envelope, attempt_id=attempt)
    if (
        retained is None
        or request_digest(retained.model_dump(mode="json")) != outcome["receipt_sha256"]
    ):
        raise OperationBlocked("bulk_retained_receipt_identity_mismatch")
    with executor.open_output(artifact) as handle:
        parquet = pq.ParquetFile(handle)
        summaries, tested = _statistic_summary(
            _parquet_rows(parquet),
            identifier="gene_id",
            effect="effect",
            caption="Bulk differential expression",
        )
        metrics = {
            "feature_rows": parquet.metadata.num_rows,
            "tested_feature_rows": tested,
        }

    def require_current(db: Connection) -> None:
        if (
            flow._state(db).outcome(step) != outcome
            or flow._current_use(db, step, TIER_A_BULK_OUTPUT, "claim", generation)
            != artifact
        ):
            raise OperationBlocked("bulk_fit_generation_changed")

    ledger._read(require_current)
    receipt = {
        "session_id": sid,
        "run_id": run_id,
        "operation": "bulk_de",
        "metrics": metrics,
        "summary_tables": summaries,
        "probability_bounds_status": "verified" if tested else "not_reported",
        "tool_receipts": [
            {
                "step": step,
                "generation": generation,
                "receipt_sha256": outcome["receipt_sha256"],
            }
        ],
        "clinical_release_allowed": False,
        "limitations": [
            "Provisional retained bulk fit. Publication requires separate "
            "review and release authority.",
        ],
    }
    return _record_count1_confirmation(plan, receipt), require_current


def _bulk_waiting(workspace: Path, sid: str) -> dict[str, Any]:
    from devbench.analysis.scientific_review_holds import ReviewHoldLifecycle

    from .adaptive_workflow import AdaptiveWorkflow

    ledger = _ledger(workspace, readonly=True)
    flow = AdaptiveWorkflow(ledger, sid)
    lifecycle = ReviewHoldLifecycle(ledger.scientific, sid)
    step = "operator-bulk-de"
    with flow._read_connection() as db:
        state = flow._state(db)
        reviews = lifecycle._state(db)
        completed = {
            row["touchpoint"]
            for row in reviews["cycles"].values()
            if row["phase"] == "completed" and step in row["subject_entity_ids"]
        }
        outcome = state.outcome(step)
    if outcome and outcome["status"] == "succeeded":
        return {
            "status": "fit_completed",
            "warnings": ["bulk_fit_retained_publication_requires_separate_review"],
            "continuation": "Open Studio Findings to inspect the retained fit.",
        }
    elif {"design", "publication"} <= completed:
        reason = "bulk_fit_requires_fresh_studio_run_authorization"
    else:
        reason = "bulk_independent_structural_review_required"
    return {
        "warnings": [reason],
        "continuation": "Open Studio Workflows to review the frozen design. If "
        "review controls are unavailable, restart Studio using the launch command.",
    }


def _workbench_record(plan: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    profiles = plan["profiles"]
    design = plan["design"]
    parameters = design["parameters"]
    return {
        "id": plan["session_id"],
        "plugin_plan_id": plan["plan_id"],
        "adaptive_lineage": lineage,
        "question": plan["objective"],
        "profile": profiles[0],
        "source": profiles[0]["source"],
        "sources": [row["source"] for row in profiles],
        "source_profiles": profiles,
        "declarations": {"measurement": design["measurement"]},
        "source_declarations": [
            {
                "role": row["role"],
                **(
                    {
                        "assay": "bulk",
                        "measurement": design["measurement"],
                        **(
                            {"count_provenance": design["count_provenance"]}
                            if design.get("count_provenance")
                            else {}
                        ),
                    }
                    if row["role"] == "expression"
                    else {}
                ),
            }
            for row in profiles
        ],
        "scientific_context": {
            "counts_layer": design["counts_layer"],
            "batch_col": design["batch_column"],
            "condition_col": design["condition_column"],
            "bulk_episode_declarations": parameters.get("bulk_declarations", {}),
        },
    }


def _run_matrix(
    jobs: JobStore, rid: str, plan: dict[str, Any], ledger, lineage, verify
) -> dict[str, Any]:
    from .workbench_matrix_audit import run_matrix_audit, verify_matrix_audit_disclosure

    design = plan["design"]
    if design["measurement"] is None:
        raise OperationBlocked("measurement_declaration_required")
    record = _workbench_record(plan, lineage)
    if design["observation_column"]:
        record["scientific_context"]["bulk_episode_declarations"] = {
            "observation_column": design["observation_column"],
        }
    if len(plan["profiles"]) == 1:
        for key in ("sources", "source_profiles", "source_declarations"):
            record.pop(key)
    intents = ("batch_association",) if design["batch_column"] else ("nonzero_genes",)
    run_root = jobs.workspace / "runs" / rid
    run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    result = run_matrix_audit(
        record,
        intents=intents,
        ledger=ledger,
        workspace=run_root,
        verify_sources=verify,
        maximum_bytes=1_000_000,
    )
    verify_matrix_audit_disclosure(
        EvidenceBroker.open_existing(ledger.scientific),
        lineage,
        result,
    )
    receipt = {
        "metrics": {
            "genes": result["gene_count"],
            "profiles": result["sample_count"],
            "quality_status": result["quality_status"],
        },
        "tool_receipts": result["tool_receipts"],
        "summary_tables": _matrix_summary(result),
        "limitations": [
            "Descriptive matrix audit. Independent biological units and "
            "scientific qualification require separate evidence."
        ],
    }
    preview = _matrix_preview(run_root, result)
    if preview is not None:
        receipt["figure_preview"] = preview
    return receipt


def _bounded_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if len(canonical(receipt)) <= 64 * 1024:
        return receipt
    trimmed = {key: value for key, value in receipt.items() if key != "figure_preview"}
    if len(canonical(trimmed)) > 64 * 1024:
        raise OperationBlocked("receipt_byte_budget_exceeded")
    return trimmed


def _summary_table(
    caption: str,
    columns: list[str],
    rows: list[list[Any]],
) -> dict[str, Any]:
    return {"caption": caption, "columns": columns, "rows": rows[:20]}


def _parquet_rows(parquet) -> Iterable[dict[str, Any]]:
    for batch in parquet.iter_batches(
        batch_size=1024,
        columns=["gene_id", "effect", "p_value", "q_value", "estimate_status"],
    ):
        yield from batch.to_pylist()


def _statistic_summary(
    rows: Iterable[dict[str, Any]],
    *,
    identifier: str,
    effect: str,
    caption: str,
) -> tuple[list[dict[str, Any]], int]:
    tested = significant = 0
    top: list[tuple[float, float, str, float | None]] = []
    for row in rows:
        p, q = row["p_value"], row["q_value"]
        if p is None and q is None:
            continue
        if p is None or q is None or not 0 <= p <= q <= 1:
            raise OperationBlocked("retained_probability_bounds_failed")
        tested += 1
        if q > 0.05:
            continue
        significant += 1
        label, value = row[identifier], row[effect]
        if (
            isinstance(label, str)
            and 1 <= len(label) <= 128
            and not _clinical_name(label)
            and (value is None or math.isfinite(value))
        ):
            top.append((q, p, label, value))
            top.sort(key=lambda entry: entry[:3])
            del top[20:]
    return [
        _summary_table(
            f"{caption} summary",
            ["Metric", "Value"],
            [["tested", tested], ["q_at_most_0.05", significant]],
        ),
        _summary_table(
            f"{caption} with q ≤ 0.05",
            ["ID", "Effect or score", "p", "q"],
            [[label, value, p, q] for q, p, label, value in top],
        ),
    ], tested


def _solver_label(*parts: str) -> str:
    safe = [_receipt_label(part, "") for part in parts]
    return " / ".join(safe) if all(safe) else ""


def _solver_statistics(
    rows: Iterable[dict[str, Any]],
    *,
    axes: tuple[str, ...],
    effect: str,
    p: str = "p_value",
    q: str = "q_value",
    unconfirmed: bool = False,
) -> Iterator[dict[str, Any]]:
    for row in rows:
        nominal, adjusted = row[p], row[q]
        if unconfirmed and adjusted is None and nominal is not None:
            if not 0 <= nominal <= 1:
                raise OperationBlocked("retained_probability_bounds_failed")
            nominal = (
                None  # Confirmation was not performed for the retained nominal test.
            )
        yield {
            "label": _solver_label(*(str(row[key]) for key in axes)),
            "effect": row.get(effect),
            "p_value": nominal,
            "q_value": adjusted,
        }


def _descriptive_solver_tables(
    operation: str, result: dict[str, Any], metrics: dict[str, Any]
) -> list[dict[str, Any]]:
    tables = []
    if operation == "bulk_deconvolution":
        samples = result["samples"]
        metrics["samples"] = len(samples)
        metrics["estimated_samples"] = sum(
            sample["status"] == "estimated" for sample in samples
        )
        tables.append(
            _summary_table(
                "Reconstruction diagnostics",
                ["Sample", "Status", "RMSE", "Pearson", "Spearman"],
                [
                    [
                        _receipt_label(row["sample_id"], f"Sample {index}"),
                        row["status"],
                        row["rmse"],
                        row["pearson_r"],
                        row["spearman_r"],
                    ]
                    for index, row in enumerate(samples[:20], start=1)
                ],
            )
        )
        proportions = (
            [sample["sample_id"], cell_type, proportion]
            for sample in samples
            for cell_type, proportion in (sample["proportions"] or {}).items()
        )
        from itertools import islice

        tables.append(
            _summary_table(
                "Relative cell-type proportions",
                ["Sample", "Cell type", "Proportion"],
                [
                    [
                        _receipt_label(sample, "Withheld"),
                        _receipt_label(cell_type, "Withheld"),
                        value,
                    ]
                    for sample, cell_type, value in islice(proportions, 20)
                ],
            )
        )
    else:
        oof = result["out_of_fold_metrics"]
        held = result["independent_evaluation"]["metrics"]
        keys = (
            "n_donors",
            "roc_auc",
            "pr_auc",
            "brier_score",
            "calibration_error",
            "calibration_slope",
        )
        metrics.update({f"out_of_fold_{key}": oof[key] for key in keys})
        tables.append(
            _summary_table(
                "Donor-level predictive performance",
                ["Metric", "Out of fold", "Independent holdout"],
                [[key, oof[key], held[key]] for key in keys],
            )
        )
    return tables


def _solver_summary(
    operation: str, result: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    metrics = {"solver_status": result["status"]}
    for key in (
        "n_cells",
        "n_donors",
        "n_spots",
        "n_donor_sections",
        "n_undirected_edges",
        "tested_genes",
        "screened_genes",
        "target_ofdr",
        "pca_components_used",
        "reference_condition_number",
        "feature_selection_stability",
    ):
        metrics[key] = result.get(key)
    metrics = {key: value for key, value in metrics.items() if value is not None}
    if operation in {"bulk_deconvolution", "phenotype_prediction"}:
        return metrics, _descriptive_solver_tables(operation, result, metrics), 0
    tables: list[dict[str, Any]] = []
    tested = 0

    def statistics(caption: str, rows: Iterable[dict[str, Any]]) -> int:
        preview, n_tests = _statistic_summary(
            rows,
            identifier="label",
            effect="effect",
            caption=caption,
        )
        tables.extend(preview)
        return n_tests

    if operation == "scrna_ccc":
        effect = (
            "effect"
            if result["specification"]["mode"] == "differential_condition"
            else "interaction_magnitude"
        )
        tested += statistics(
            "Cell–cell communication",
            _solver_statistics(
                result["interaction_statistics"],
                axes=("interaction_id", "sender", "receiver"),
                effect=effect,
            ),
        )
    elif operation == "scrna_neighborhood_da":
        rows = result["neighborhoods"]
        tested += statistics(
            "Neighborhood differential abundance",
            _solver_statistics(
                rows,
                axes=("center_cell_id",),
                effect="log2_fold_change",
            ),
        )
        statistics(
            "Neighborhoods (spatial FDR)",
            _solver_statistics(
                rows,
                axes=("center_cell_id",),
                effect="log2_fold_change",
                q="spatial_fdr",
            ),
        )
    elif operation == "bulk_dtu":
        genes = result["gene_statistics"]
        tested += statistics(
            "Within-gene usage screen",
            _solver_statistics(
                genes,
                axes=("gene_id",),
                effect="unused",
                p="gene_p",
                q="gene_q",
            ),
        )
        tested += statistics(
            "Feature usage confirmation",
            _solver_statistics(
                (row for gene in genes for row in gene["transcript_statistics"]),
                axes=("feature_id",),
                effect="log_odds_ratio",
                p="tx_p",
                q="tx_q",
                unconfirmed=True,
            ),
        )
    elif operation == "spatial_pattern":
        for label, key, effect in (
            ("Moran’s I", "moran", "moran_i"),
            ("Geary’s C", "geary", "geary_c"),
        ):
            tested += statistics(
                label,
                _solver_statistics(
                    result["gene_statistics"],
                    axes=("gene_id",),
                    effect=effect,
                    p=key + "_p",
                    q=key + "_q",
                ),
            )
        tested += statistics(
            "Cell-type neighborhood enrichment",
            _solver_statistics(
                (
                    {**row, "label": _solver_label(*row["cell_types"])}
                    for row in result["neighborhood_enrichment"]
                ),
                axes=("label",),
                effect="z_score",
            ),
        )
        tested += statistics(
            "Library size Moran’s I",
            _solver_statistics(
                [result["library_size_diagnostic"]],
                axes=("measurement",),
                effect="moran_i",
            ),
        )
    elif operation == "perturbation_response":
        perturbations = result["perturbations"]
        tested += statistics(
            "Perturbation energy distance",
            _solver_statistics(
                (
                    {**row["energy_test"], "perturbation": row["perturbation"]}
                    for row in perturbations
                ),
                axes=("perturbation",),
                effect="distance",
            ),
        )
        tested += statistics(
            "Donor pseudobulk differential expression",
            _solver_statistics(
                (
                    {**gene, "perturbation": row["perturbation"]}
                    for row in perturbations
                    for gene in row["pseudobulk_de"]["gene_statistics"]
                ),
                axes=("perturbation", "gene_id"),
                effect="log2_fold_change",
            ),
        )
    return metrics, tables, tested


_SOLVER_RESULT_FILE = "solver-result.json"


def _verify_solver_result(
    workspace: Path,
    rid: str,
    receipt: dict[str, Any],
) -> None:
    from devbench.core.population_data import bound_query_file

    relative = Path("runs") / rid / _SOLVER_RESULT_FILE
    artifact = receipt.get("result_artifact", {})
    if artifact.get("path") != relative.as_posix():
        raise OperationBlocked("solver_result_artifact_changed")
    try:
        with bound_query_file(
            workspace / relative, artifact["sha256"], artifact["size_bytes"]
        ) as source:
            if os.fstat(source.fileno()).st_size != artifact["size_bytes"]:
                raise ValueError("Result size mismatch")
    except (OSError, ValueError, KeyError) as error:
        raise OperationBlocked("solver_result_artifact_changed") from error


def _run_local_solver(jobs: JobStore, rid: str, plan: dict[str, Any]) -> dict[str, Any]:
    from devbench.core._rna_solver_inputs import MAX_INPUT_BYTES
    from devbench.core.population_data import bound_query_file

    from .workbench_matrix_audit import _snapshot_sources

    runtime = _local_python_runtime()
    if runtime != plan["runtime"]:
        raise OperationBlocked("local_python_runtime_changed")
    choice = _SOLVER_ADAPTER.validate_json(canonical(plan["local_solver"]))
    if choice.operation != plan["operation"]:
        raise OperationBlocked("solver_plan_operation_mismatch")
    run_root = jobs.workspace / "runs" / rid
    run_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    profiles = plan["profiles"]
    with TemporaryDirectory(dir=run_root, prefix=".inputs-") as staging:
        staged = _snapshot_sources(
            {
                "source_profiles": profiles,
                "sources": [row["source"] for row in profiles],
            },
            Path(staging),
            MAX_INPUT_BYTES,
        )["source_profiles"]
        sources = bind_sources(choice, staged)
        try:
            result = execute_solver(choice, sources)
        finally:
            for profile in staged:
                with bound_query_file(
                    profile["source"], profile["sha256"], profile["size_bytes"]
                ):
                    pass
    metrics, tables, tested = _solver_summary(choice.operation, result)
    raw = canonical(result)
    path = run_root / _SOLVER_RESULT_FILE
    with path.open("xb") as target:
        target.write(raw)
    path.chmod(0o400)
    return {
        "claim_scope": result["claim_scope"],
        "runtime_sha256": runtime["runtime_sha256"],
        "probability_bounds_status": "verified" if tested else "not_evaluated",
        "metrics": metrics,
        "summary_tables": tables,
        "result_artifact": {
            "path": path.relative_to(jobs.workspace).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        },
        "limitations": [
            "Scope is limited to the supplied measurements and design and the "
            "declared method. "
            "Scientific review and independent qualification remain separate."
        ],
    }


def _matrix_summary(result: dict[str, Any]) -> list[dict[str, Any]]:
    from statistics import median, quantiles

    quality = result["quality_metrics"]
    rows = [
        [name, quality[name]]
        for name in ("zero_fraction", "missing_value_count", "nonfinite_value_count")
    ]
    rows.append(["zero_expression_profiles", result["zero_expression_samples"]])
    for name, value in (quality["library_size_summary"] or {}).items():
        rows.append([f"library_size_{name}", value])
    counts = [row["nonzero_genes"] for row in result["nonzero_per_sample"]]
    rows.append(["nonzero_features_median", median(counts)])
    if len(counts) > 1:
        lower, _, upper = quantiles(counts, method="inclusive")
        rows.extend([["nonzero_features_p25", lower], ["nonzero_features_p75", upper]])
    diagnostics = {row["code"] for row in result["quality_diagnostics"]}
    rows.extend(
        [
            [
                "no_varying_expression_geometry",
                "expression_geometry_collapse" in diagnostics,
            ],
            ["zero_depth_profile_flag", bool(result["zero_expression_samples"])],
        ]
    )
    return [_summary_table("Expression audit", ["Metric", "Value"], rows)]


def _matrix_preview(workspace: Path, result: dict[str, Any]) -> str | None:
    from .workbench_matrix_audit_plot import read_matrix_plot_blob

    artifact = result["plot_artifacts"]["figure"]
    if artifact is None:
        return None
    blob = read_matrix_plot_blob(workspace, artifact["sha256"])
    if len(blob) > 36_000 or not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return "data:image/png;base64," + base64.b64encode(blob).decode("ascii")


def run_frozen_plan(jobs: JobStore, rid: str, plan: dict[str, Any]) -> dict[str, Any]:
    if (
        plan["operation"]
        in {
            "matrix_audit",
            "qc-salmon",
            "shared_motor_umap",
            "motor_program_comparison",
            *LOCAL_OPERATIONS,
        }
        and plan.get("graph_runtime") is None
    ):
        # A legacy approval never authorized the registered graph implementation.
        raise OperationBlocked("plugin_graph_replan_required")
    if plan["operation"] == "shared_motor_umap":
        from .plugin_graph import run_registered_plugin_step
        from .plugin_motor_umap import _check_scope, run_shared_motor_umap_plan

        result = run_registered_plugin_step(
            jobs,
            rid,
            plan,
            execute=lambda: run_shared_motor_umap_plan(jobs, rid, plan),
            verify_sources=lambda: _check_scope(jobs.workspace, plan),
        )
        result.pop("receipt_sha256", None)
        return {**result, "receipt_sha256": digest(result)}
    if plan["data_classification"] == "private" and plan["operation"] in {
        "motor_program_comparison",
        *LOCAL_OPERATIONS,
    }:
        from .plugin_graph import run_registered_plugin_step
        from .plugin_motor_programs import run_motor_program_comparison

        lineage = _lineage(plan["session_id"], plan)
        ledger = _ledger(jobs.workspace)
        broker = EvidenceBroker.open_existing(ledger.scientific)

        def verify_private_scope():
            broker.check_delivery(
                lineage["context_id"],
                lineage["source_id"],
                lineage_id=plan["session_id"],
                role="scientist",
            )
            ledger._read(lambda db: broker._query_dependencies(db, _roots(plan)))

        def execute_private():
            if plan["operation"] == "motor_program_comparison":
                return run_motor_program_comparison(jobs, rid, plan)
            return _run_local_solver(jobs, rid, plan)

        return run_registered_plugin_step(
            jobs,
            rid,
            plan,
            execute=execute_private,
            verify_sources=verify_private_scope,
        )
    return _run_external_plan(jobs, rid, plan)


def _verify_execution_inputs(discovery, root, plan) -> None:
    profiles = [
        _profile(discovery, root, Input.model_validate(row))
        for row in plan["input_declarations"]
    ]
    if any(row["data_classification"] == "private" for row in profiles):
        raise OperationBlocked(_PRIVATE)
    if [item["sha256"] for item in profiles] != [
        item["sha256"] for item in plan["profiles"]
    ]:
        raise OperationBlocked("input_snapshot_changed")


def _record_count1_confirmation(plan, result):
    review = plan.get("count1_confirmation")
    if review is None:
        return result
    return {
        **result,
        "count1_confirmation_sha256": digest(review),
        "limitations": [
            *result.get("limitations", ()),
            "Caller confirmed suspected integer-transformed count distribution "
            "against external provenance.",
        ],
    }


def _run_external_plan(
    jobs: JobStore, rid: str, plan: dict[str, Any]
) -> dict[str, Any]:
    root = directory(Path(plan["input_root"]))
    sid = plan["session_id"]
    if plan["data_classification"] not in {"synthetic", "public"}:
        raise OperationBlocked(_PRIVATE)
    discovery = InputDiscovery(
        [root], allow_external_files=plan.get("allow_external_input_files") is True
    )

    def verify():
        _verify_execution_inputs(discovery, root, plan)

    verify()
    operation = plan["operation"]
    jobs.stage(rid, step=operation)
    ledger = _ledger(jobs.workspace)
    broker = EvidenceBroker.open_existing(ledger.scientific)
    lineage = _lineage(sid, plan)
    broker.check_delivery(
        lineage["context_id"], lineage["source_id"], lineage_id=sid, role="scientist"
    )
    policy = ledger._read(
        lambda db: broker._context_policy(db, lineage["context_id"])[0]["policy_sha256"]
    )
    lineage = _lineage(sid, plan, policy)

    def execute_native():
        if operation == "matrix_audit":
            return _run_matrix(jobs, rid, plan, ledger, lineage, verify)
        if operation == "qc-salmon":
            return _run_fastq(jobs, rid, plan)
        return _run_local_solver(jobs, rid, plan)

    if plan.get("graph_runtime") is not None:
        from .plugin_graph import run_registered_plugin_step

        result = run_registered_plugin_step(
            jobs,
            rid,
            plan,
            execute=lambda: _record_count1_confirmation(plan, execute_native()),
            verify_sources=verify,
        )
    else:
        if operation == "registered_exploratory":
            from .plugin_registered_method import run_registered_method

            result = run_registered_method(jobs, rid, plan, verify_sources=verify)
        else:
            result = _run_adaptive(jobs, rid, plan, ledger, broker, lineage)
        result = _record_count1_confirmation(plan, result)
    verify()
    if result.get("_terminal_status"):
        return result
    receipt = {
        "session_id": sid,
        "run_id": rid,
        "operation": operation,
        "clinical_release_allowed": False,
        "probability_bounds_status": "not_evaluated",
        **result,
    }
    require_current = _adaptive_receipt_guard(ledger, plan, receipt)
    if require_current is not None:
        ledger._read(require_current)
    receipt = _bounded_receipt(receipt)
    destination = jobs.workspace / "runs" / rid / "replay_bundle"
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = _replay_manifest(plan, receipt)
    _write_replay(destination, plan, receipt, manifest)
    receipt["replay_bundle_sha256"] = digest(manifest)
    tags = _receipt_tags(receipt)
    source = broker.register_source(
        f"plugin-receipt-{rid}",
        content_sha256=digest(receipt),
        roots=_roots(plan),
        parents=(lineage["source_id"],),
        classification="target_outcome",
        tags=tags,
    )
    context_id = _receipt_context(broker, plan, lineage, tags)
    broker.check_delivery(context_id, source, lineage_id=sid, role="scientist")
    return receipt


def _replay_manifest(plan, receipt):
    return {
        "format": "devbench.plugin-replay/1",
        "plan_sha256": digest(plan),
        "receipt_sha256": digest(receipt),
        "input_sha256": _roots(plan),
    }


def _write_replay(root, plan, receipt, manifest):
    for name, value in (
        ("plan.json", plan),
        ("receipt.json", receipt),
        ("manifest.json", manifest),
    ):
        with (root / name).open("xb") as target:
            target.write(canonical(value))


def _read_reviewed_bulk_receipt(workspace, plan, row):
    from .plugin_bulk_receipts import reviewed_bulk_source_id

    rid = row["run_id"]
    subject = json.loads(row["stage_json"]).get("bulk_step_sha256")
    receipt, current = _bulk_verified_fit(
        _ledger(workspace, readonly=True), plan, rid, subject
    )
    manifest = _replay_manifest(plan, receipt)
    released = {**receipt, "replay_bundle_sha256": digest(manifest)}
    root = workspace / "runs" / rid / "replay_bundle" / manifest["receipt_sha256"]
    _verify_replay(root, row["plan_sha256"], released)
    return released, reviewed_bulk_source_id(rid, released), current


def _seal_bulk_replay(
    workspace: Path,
    rid: str,
    plan: dict[str, Any],
    plan_sha: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Seal the verified fit once per receipt without replacing prior evidence."""
    manifest = _replay_manifest(plan, receipt)
    if manifest["plan_sha256"] != plan_sha:
        raise OperationBlocked("replay_bundle_identity_mismatch")
    archive = directory(workspace / "runs" / rid / "replay_bundle", exists=False)
    archive.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory(archive / manifest["receipt_sha256"], exists=False)
    if not destination.exists():
        with TemporaryDirectory(prefix=".replay-", dir=archive) as temporary:
            staged = Path(temporary)
            _write_replay(staged, plan, receipt, manifest)
            try:
                staged.rename(destination)
            except OSError as error:
                if error.errno not in {EEXIST, ENOTEMPTY}:
                    raise
    released = {**receipt, "replay_bundle_sha256": digest(manifest)}
    _verify_replay(destination, plan_sha, released)
    return released


def _verify_replay(root: Path, plan_sha: str, receipt: dict[str, Any]) -> None:
    manifest = read_bounded_file(root / "manifest.json", MAX_DOCUMENT_BYTES)
    plan = read_bounded_file(root / "plan.json", MAX_DOCUMENT_BYTES)
    retained_receipt = read_bounded_file(root / "receipt.json", 768 * 1024)
    expected = {
        key: value for key, value in receipt.items() if key != "replay_bundle_sha256"
    }
    identity = json.loads(manifest)
    if (
        hashlib.sha256(manifest).hexdigest() != receipt["replay_bundle_sha256"]
        or hashlib.sha256(plan).hexdigest() != plan_sha
        or identity["plan_sha256"] != plan_sha
        or hashlib.sha256(retained_receipt).hexdigest() != identity["receipt_sha256"]
        or retained_receipt != canonical(expected)
    ):
        raise OperationBlocked("replay_bundle_identity_mismatch")


def _freeze_fastq(
    root: Path,
    profiles: list[dict[str, Any]],
    parameters: dict[str, Any],
    *,
    verify_members: Callable[[dict[str, Any], Path], dict[str, str]] | None = None,
) -> dict[str, Any]:
    from devbench.analysis.fastq_contract import _file_identity, preflight_fastq_plan

    manifests = [row for row in profiles if row["role"] == "manifest"]
    if len(manifests) != 1 or set(parameters) - {"tx2gene", "quantification_only"}:
        raise ValueError("Use one local FASTQ manifest and exact qc-salmon parameters")
    raw = read_bounded_file(Path(manifests[0]["source"]), 64 * 1024)
    if hashlib.sha256(raw).hexdigest() != manifests[0]["sha256"]:
        raise OperationBlocked("input_snapshot_changed")
    source = json.loads(raw)
    mapper = parameters.get("tx2gene")
    if not isinstance(mapper, dict) or set(mapper) != {"path", "sha256"}:
        raise ValueError("Provide exact local tx2gene path and SHA-256")
    tx2gene = _canonical_input(root, mapper["path"])
    if type(parameters.get("quantification_only", False)) is not bool:
        raise ValueError("Declare whether this request stops after quantification")
    expected = verify_members(source, tx2gene) if verify_members is not None else None
    preflight = preflight_fastq_plan(source, input_root=root)
    inventory = preflight["input_files"]
    clinical_names = [
        *preflight["design"]["metadata_rows"][0],
        *(
            part
            for row in inventory
            for part in Path(row["path"]).relative_to(root).parts
        ),
        *tx2gene.relative_to(root).parts,
    ]
    if any(_clinical_name(name) for name in clinical_names):
        return {"private": True}
    inventory.append(_file_identity(tx2gene, "tx2gene"))
    if inventory[-1]["sha256"] != mapper["sha256"]:
        raise OperationBlocked("input_snapshot_changed")
    result = {"source": source, "inventory": inventory, "private": False}
    if expected is not None:
        if {item["path"]: item["sha256"] for item in inventory} != expected:
            raise OperationBlocked("input_snapshot_changed")
        result["preflight"] = preflight
    return result


def _fastq_worker_options(jobs, rid, cleanup):
    options = {}
    if callable(getattr(jobs, "bind_nextflow_runtime", None)):
        options["container_cleanup"] = cleanup
    if remaining := getattr(jobs, "remaining_deadline_sec", None):
        options["timeout_sec"] = remaining(rid)
    if resources := getattr(jobs, "nextflow_resources", None):
        options["resources"] = resources(rid)
    return options


def _fastq_custody_stage(event):
    kind = event["event"]
    if kind == "attempt_created":
        fields = {"nextflow_attempt": event["attempt_dir"]}
        if "prelaunch_sha256" in event:
            fields["nextflow_prelaunch_sha256"] = event["prelaunch_sha256"]
        return fields
    if kind in {"version_probe_started", "engine_process_bound"}:
        field = (
            "nextflow_version_probe"
            if kind == "version_probe_started"
            else "nextflow_engine_process"
        )
        return {field: {"pid": event["pid"], "create_time": event["create_time"]}}
    if kind == "version_probe_finished":
        return {"nextflow_version_probe_finished": True}
    if kind == "engine_launch_intent":
        return {"nextflow_engine_launch_intent": True}
    return {}


def _run_fastq(jobs: JobStore, rid: str, plan: dict[str, Any]) -> dict[str, Any]:
    from inspect import signature

    from devbench.analysis.fastq_execution import run_fastq_plan

    if "input_root" not in signature(run_fastq_plan).parameters:
        raise OperationBlocked("local_qc_salmon_runtime_unavailable")
    root = Path(plan["input_root"])
    design = plan["design"]["parameters"]
    if _freeze_fastq(root, plan["profiles"], design) != plan["fastq"]:
        raise OperationBlocked("input_snapshot_changed")
    destination = jobs.workspace / "runs" / rid / "nextflow"
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    trace_attempt = None
    docker_binding = None
    bind_runtime = getattr(jobs, "bind_nextflow_runtime", None)

    def observe(event):
        nonlocal trace_attempt, docker_binding
        if event["event"] == "attempt_created":
            trace_attempt = event["attempt_dir"]
        elif event["event"] == "before_engine_start" and bind_runtime is not None:
            docker_binding = bind_runtime(rid, Path(event["attempt_dir"]))
            return docker_binding
        if stage := _fastq_custody_stage(event):
            jobs.stage(rid, **stage)

    def cleanup(attempt):
        from devbench.daemon.supervisor import _nextflow_containers

        return _nextflow_containers(attempt, docker_binding)

    runtime: Any = run_fastq_plan
    result = runtime(
        plan["fastq"]["source"],
        destination,
        execution_profile="qc-salmon",
        input_root=root,
        tx2gene=design["tx2gene"],
        quantification_only=design.get(
            "quantification_only",
            False,
        ),
        mounted_image_id=plan["mounted_image_id"],
        execution_observer=observe,
        **_fastq_worker_options(jobs, rid, cleanup),
    )
    if result["status"] != "completed":
        raise OperationBlocked("qc_salmon_execution_incomplete")
    if _freeze_fastq(root, plan["profiles"], design) != plan["fastq"]:
        raise OperationBlocked("input_snapshot_changed")
    summaries, receipts, tested = _salmon_summaries(destination, result)
    trace = _retained_nextflow_trace(jobs.workspace, rid, trace_attempt)
    return {
        **({"nextflow_trace": trace} if trace is not None else {}),
        "summary_tables": summaries,
        "tool_receipts": receipts,
        "probability_bounds_status": "verified" if tested else "not_reported",
        "metrics": {
            "engine_launch_count": result["engine_launch_count"],
            "engine_execution": result["engine_execution"],
        },
        "limitations": [
            "Local workflow execution evidence only. Independent scientific "
            "qualification requires separate evidence."
        ],
    }


def _salmon_rows(raw: bytes) -> Iterable[dict[str, Any]]:
    from io import StringIO

    reader = csv.DictReader(StringIO(raw.decode()), delimiter="\t")
    for row in reader:
        yield {
            **row,
            **{
                key: float(row[key]) if row[key] else None
                for key in ("effect", "p_value", "q_value")
            },
        }


def _salmon_summaries(
    destination: Path,
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    def retained(name: str, *, maximum: int) -> bytes:
        artifact = result["artifacts"][name]
        path = _canonical_input(destination, artifact["path"])
        raw = read_bounded_file(path, maximum)
        if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
            raise OperationBlocked("qc_salmon_receipt_identity_mismatch")
        return raw

    provenance = json.loads(retained("salmon_quant_provenance", maximum=8 * 1024**2))
    rows = [
        ["transcripts_quantified", provenance["transcript_count"]],
        ["genes_quantified", provenance["gene_count"]],
        ["profiles_quantified", len(provenance["sample_ids"])],
    ]
    mapping = _salmon_mapping_metrics(destination, provenance["quant_files"])
    rows.extend(mapping)
    tables = [_summary_table("Salmon quantification", ["Metric", "Value"], rows)]
    names = ["salmon_quant_provenance"]
    tested = 0
    if "differential_expression" in result["artifacts"]:
        name = "differential_expression"
        raw = retained(name, maximum=32 * 1024**2)
        summary, tested = _statistic_summary(
            _salmon_rows(raw),
            identifier="gene_id",
            effect="effect",
            caption="Salmon differential expression",
        )
        tables.extend(summary)
        names.append(name)
    return (
        tables,
        [
            {"step": name, "receipt_sha256": result["artifacts"][name]["sha256"]}
            for name in names
        ],
        tested,
    )


def _salmon_mapping_metrics(
    destination: Path,
    files: list[dict[str, Any]],
) -> list[list[Any]]:
    from io import TextIOWrapper
    from statistics import median

    from devbench.core.population_data import bound_query_file

    maximum = 64 * 1024**2
    if not files or sum(entry["size_bytes"] for entry in files) > maximum:
        return []
    totals = []
    for entry in files:
        quant = _canonical_input(destination, entry["path"])
        with bound_query_file(quant, entry["sha256"], maximum) as stream:
            decoded = TextIOWrapper(stream, encoding="utf-8")
            try:
                numbers = csv.DictReader(decoded, delimiter="\t")
                total = math.fsum(float(row["NumReads"]) for row in numbers)
            finally:
                decoded.detach()
            if not math.isfinite(total) or total < 0:
                raise OperationBlocked("qc_salmon_quantification_invalid")
            totals.append(total)
    return [
        ["estimated_fragments_assigned_total", math.fsum(totals)],
        ["estimated_fragments_assigned_median_per_profile", median(totals)],
    ]


def _run_adaptive(jobs, rid, plan, ledger, broker, lineage):
    from devbench.engine.tier_a_executor import TierAExecutor

    from .adaptive_workflow import AdaptiveWorkflow
    from .workbench_interactive import (
        _activate_session,
        _register_bulk_inputs,
    )

    if plan["runtime"]["oci_digest"] is None:
        raise OperationBlocked("pinned_runtime_required")
    executor = TierAExecutor(
        ledger, allow_data_execution=True, mounted_image_id=plan["mounted_image_id"]
    )
    sid = plan["session_id"]
    flow = AdaptiveWorkflow(ledger, sid)
    parameters = plan["design"]["parameters"]
    record = _workbench_record(plan, lineage)
    operation = plan["operation"]
    if operation == "bulk_de":
        if not (plan["design"].get("count_provenance") or "").strip():
            raise OperationBlocked("bulk_count_provenance_required")
        from . import workbench_interactive

        bulk_step = workbench_interactive._OPERATOR_BULK_STEP
        if flow._read_projection(lambda db: bulk_step in flow._state(db).nodes):
            raise OperationBlocked("bulk_session_already_has_reviewed_design")
        queue_bulk = getattr(workbench_interactive, "queue_operator_bulk", None)
        if queue_bulk is None:
            raise OperationBlocked("governed_bulk_review_runtime_unavailable")
        _register_bulk_inputs(flow, broker, record, executor)
        _activate_session(
            flow,
            SimpleNamespace(allow_data_execution=True),
            "workbench-session-input",
            record,
        )
        queue_bulk(flow, executor)
        subject = flow._read_projection(
            lambda db: digest(flow._state(db).nodes[bulk_step].model_dump(mode="json"))
        )
        jobs.stage(
            rid,
            bulk_step_sha256=subject,
            step_total=3,
            tasks=[
                {
                    "step": "Mounted design preflight",
                    "target": "local_tier_a",
                    "status": "succeeded",
                },
                {
                    "step": "Independent scientific review",
                    "target": "research_studio",
                    "status": "awaiting_review",
                },
            ],
        )
        return {"_terminal_status": "awaiting_review"}
    if operation == "pathways":
        return _run_pathway(jobs, plan, ledger, broker, executor)
    if "pseudobulk" in parameters:
        return _run_pseudobulk(jobs, rid, plan, flow, broker, executor, parameters)
    return _run_singlecell(jobs, rid, flow, record, broker, executor, parameters)


def _confirmed_unit(unit: str) -> bool:
    from devbench.analysis.fastq_contract import MEMBERSHIP_ALIASES

    normalized = re.sub(r"[^a-z0-9]+", "_", unit.casefold()).strip("_")
    technical = MEMBERSHIP_ALIASES | {
        "unverified_exploratory",
        "capture",
        "capture_id",
        "lane",
        "lane_id",
        "cell_barcode",
        "nucleus_id",
        "barcode_id",
    }
    return bool(normalized) and normalized not in technical


def _check_cell_input_scope(flow, record, parameters) -> None:
    def check(db):
        state = flow._state(db)
        counts = next(
            (
                value[0]
                for key, value in state.artifacts.items()
                if key.startswith("workbench-counts-")
            ),
            None,
        )
        if counts is None:
            return
        layer = record["scientific_context"]["counts_layer"]
        if layer is not None and (
            state.basis.get("workbench-counts-view", {}).get("sha256")
            != digest(
                {
                    "original_sha256": record["profile"]["sha256"],
                    "counts_view_sha256": counts["sha256"],
                    "declared_layer": layer,
                }
            )
        ):
            raise OperationBlocked("single_cell_count_layer_changed")
        if "biological_independence" in parameters:
            declaration = parameters["biological_independence"] or {}
            expected = digest(
                {
                    "input_sha256": record["profile"]["sha256"],
                    "obs_column": counts["biological_unit"],
                    "declaration": declaration,
                }
            )
            if counts["unit_membership_sha256"] != expected:
                raise OperationBlocked("single_cell_biological_units_changed")
        if provenance := parameters.get("raw_droplet_provenance"):
            declared = digest(
                {"format": "devbench.raw-droplet-provenance/1", **provenance}
            )
            retained = state.basis.get("workbench-droplet-provenance")
            if retained is not None and retained["sha256"] != declared:
                raise OperationBlocked("single_cell_droplet_provenance_changed")

    flow._read_projection(check)


def _cell_inputs(flow, record, broker, executor, parameters):
    from .workbench_interactive import _activate_session, _register_cell_counts
    from .workbench_investigations import _validate_context_bindings

    required = {"capture_units_confirmed", "steps"}
    context_fields = {"biological_independence", "raw_droplet_provenance"}
    if not required <= parameters.keys() <= required | context_fields | {"composition"}:
        raise OperationBlocked("single_cell_intake_declarations_required")
    if parameters["capture_units_confirmed"] is not True:
        raise OperationBlocked("single_cell_capture_units_unconfirmed")
    measurement = record["declarations"].get("measurement")
    if measurement is not None and measurement not in {"umi_counts", "non_umi_counts"}:
        raise OperationBlocked("single_cell_counts_measurement_required")
    independence = parameters.get("biological_independence")
    if (
        independence
        and independence["independent"]
        and not _confirmed_unit(independence["unit_column"])
    ):
        raise OperationBlocked("confirmed_biological_units_required")
    _check_cell_input_scope(flow, record, parameters)
    context = record["scientific_context"]
    record["declarations"].update(
        assay="scrna",
        capture_units_confirmed=parameters["capture_units_confirmed"],
    )
    declared_context = {
        key: parameters[key] for key in context_fields & parameters.keys()
    }
    _validate_context_bindings(declared_context)
    context.update(declared_context)
    _register_cell_counts(flow, broker, record, executor)
    artifacts = flow._read_projection(lambda db: flow._state(db).artifacts)
    source = next(
        (key for key in artifacts if key.startswith("workbench-counts-")), None
    )
    if source is None:
        raise OperationBlocked("single_cell_verified_counts_layer_and_units_required")
    biological_unit = artifacts[source][0]["biological_unit"]
    if parameters.get("composition") and not _confirmed_unit(biological_unit):
        raise OperationBlocked("confirmed_biological_units_required")
    aliases = {"input": source}
    provenance = next(
        (key for key in artifacts if key.startswith("workbench-droplet-provenance-")),
        None,
    )
    if provenance is not None:
        aliases["droplet_provenance"] = provenance
    _activate_session(
        flow,
        SimpleNamespace(allow_data_execution=True),
        "workbench-session-input",
        record,
    )
    return artifacts[source][0], aliases


_CHAIN_PORTS = {
    "counts",
    "filtered_counts",
    "ambient_corrected_counts",
    "qc_metrics",
    "qc_features",
    "predictions",
    "selected",
    "embedding",
}


def _run_singlecell(jobs, rid, flow, record, broker, executor, parameters):
    from devbench.daemon.native_steps import NativeSteps

    counts, aliases = _cell_inputs(flow, record, broker, executor, parameters)
    biological_unit = counts["biological_unit"]
    if composition := parameters.get("composition"):
        aliases.update(
            _stage_composition(jobs.workspace, rid, flow, executor, counts, composition)
        )
    task_rows, outcomes, summaries = [], [], []
    steps = parameters["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 16:
        raise ValueError("Use one to sixteen ordered registered single-cell steps")
    checkpoints = NativeSteps(
        jobs,
        rid,
        flow,
        executor,
        record["plugin_plan_id"],
        enabled=not composition,
        actor=_ACTOR,
    )
    if len(checkpoints.entries) > len(steps):
        raise OperationBlocked("native_step_checkpoint_mismatch")
    for index, step in enumerate(steps, 1):
        if set(step) != {"operation", "parameters", "sources"}:
            raise ValueError(
                "Single-cell step requires typed parameters and input ports"
            )
        identity = f"plugin-step-{record['plugin_plan_id']}-{index}"
        if step["operation"] == "scrna_sccoda" and not _confirmed_unit(biological_unit):
            raise OperationBlocked("confirmed_biological_units_required")
        links = {
            port: {"id": aliases.get(link["id"], link["id"]), "port": link.get("port")}
            for port, link in step["sources"].items()
        }
        outcome = checkpoints.prepare(
            {
                **step,
                "step_id": identity,
                "branch_id": "plugin-single-cell",
                "biological_unit": biological_unit,
                "sources": links,
            },
            index,
        )
        task = {"step": step["operation"], "target": "local_tier_a"}
        task_rows.append(task)
        if outcome is None:
            task["status"] = "running"
            jobs.stage(rid, tasks=task_rows, step_total=len(steps))
            started = time.monotonic()
            outcome = flow.dispatch(identity, executor, actor=_ACTOR)
            task["duration_seconds"] = round(time.monotonic() - started, 2)
            if outcome["status"] == "succeeded" and checkpoints.enabled:
                outcome = checkpoints.accept(identity, index)
        else:
            task["resumed_from_checkpoint"] = True
        task["status"] = outcome["status"]
        jobs.stage(rid, tasks=task_rows, step_total=len(steps))
        if outcome["status"] != "succeeded":
            raise OperationBlocked("single_cell_step_did_not_succeed")
        summaries.extend(_cell_summaries(flow, executor, identity, outcome))
        aliases[f"plugin-step-{index}"] = identity
        outcomes.append(
            {
                "step": identity,
                "receipt_sha256": outcome["receipt_sha256"],
                "outputs": {
                    port: {
                        "artifact_id": artifact["version_id"],
                        "sha256": artifact["sha256"],
                    }
                    for port, artifact in outcome["outputs"].items()
                    if port in _CHAIN_PORTS
                },
            }
        )
    return {
        "metrics": {"steps_completed": len(outcomes)},
        "source_bindings": {
            "input": {"artifact_id": counts["version_id"], "sha256": counts["sha256"]},
        },
        "summary_tables": summaries,
        "tool_receipts": outcomes,
        "limitations": [
            "Checked Tier A execution evidence. Independent scientific "
            "qualification requires separate evidence."
        ],
    }


def _retained_cell_source(workspace, rid, executor, artifact, expected_sha):
    import shutil

    if (
        artifact["sha256"] != expected_sha
        or artifact["representation"] != "raw_counts_h5ad"
    ):
        raise OperationBlocked("current_qc_counts_source_required")
    executor._require_unprotected((expected_sha,))
    root = workspace / "runs" / rid
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "source-counts.h5ad"
    with (
        executor.training.open(expected_sha) as retained,
        path.open("xb") as destination,
    ):
        shutil.copyfileobj(retained, destination)
    return path


def _stage_composition(workspace, rid, flow, executor, counts, spec):
    from devbench.platform.adaptive_workflow_state import ArtifactVersion
    from devbench.platform.scrna_qc_handoff import register_composition_after_qc

    observations = CompositionObservationSpec.model_validate(spec["observations"])
    if observations.biological_unit_column != counts["biological_unit"]:
        raise OperationBlocked("confirmed_biological_units_required")
    source = _retained_cell_source(
        workspace, rid, executor, counts, observations.source_sha256
    )
    data, artifact = register_composition_after_qc(
        flow,
        executor,
        spec["qc_step_id"],
        source,
        observations,
        version_id=f"plugin-composition-input-{rid}",
    )
    model = CompositionSpec.model_validate(spec["model"])
    model_artifact = ArtifactVersion(
        version_id=f"plugin-composition-spec-{rid}",
        sha256=executor.training.put(canonical(model.model_dump(mode="json"))),
        kind="table",
        representation="scrna_composition_spec_json",
        biological_unit=data.biological_unit,
        unit_membership_sha256=data.biological_unit_mapping_sha256,
    )
    flow.register_artifact(model_artifact, parents=(artifact.version_id,))
    return {
        "composition_input": artifact.version_id,
        "composition_spec": model_artifact.version_id,
    }


def _pseudobulk_records(staged):
    from devbench.core._hashing import canonical_sha256

    plan = staged.pipeline
    if plan is None:
        raise OperationBlocked("pseudobulk_capsule_held")
    summary = staged.summary
    raw = read_bounded_file(staged.directory / "metadata.csv", 5_000_000)
    if hashlib.sha256(raw).hexdigest() != plan.metadata_sha256:
        raise OperationBlocked("pseudobulk_metadata_identity_mismatch")
    from io import StringIO

    rows = list(csv.DictReader(StringIO(raw.decode())))
    observations = canonical_sha256([row["sample_id"] for row in rows])
    members: dict[str, set[str]] = {}
    for row in rows:
        members.setdefault(row["condition"], set()).add(row["donor"])
    measurement = AttestedMeasurement(
        kind="donor_pseudobulk_counts",
        artifact_sha256=plan.expression_sha256,
        receipt_sha256=staged.provenance_sha256,
        observation_axis_sha256=observations,
        feature_axis_sha256=summary["feature_ids_sha256"],
    )
    design = DesignEvidence(
        biological_unit=plan.biological_unit,
        receipt_sha256=canonical_sha256(
            {
                "capsule": staged.provenance_sha256,
                "design": summary["design"],
                "metadata_sha256": plan.metadata_sha256,
            }
        ),
        observation_axis_sha256=observations,
        estimability="estimable",
        independent_units_per_group=tuple(len(units) for units in members.values()),
    )
    return measurement, design


def _bind_pseudobulk(jobs, plan, flow, broker, executor, request, staged, profile):
    from .thesis_workflow import ThesisWorkflow
    from .workbench_adaptive import _graph_revision
    from .workbench_thesis import _bind_pseudobulk_capsule

    sid = flow.lineage_id
    measurement, design = _pseudobulk_records(staged)
    query = AnswerabilityRequest(
        family_id=1,
        question="differential_cell_state",
        entry_point="count_matrix",
        assay=(
            "single_cell_rna_seq"
            if request.specification.assay == "scrna"
            else "single_nucleus_rna_seq"
        ),
    )
    runtime = RuntimeEvidence(
        question=query.question,
        tier="tier_a",
        operation_id="bulk_condition_contrast",
        runtime_sha256=executor.environment_sha256,
    )
    lifecycle = ThesisWorkflow(
        flow.ledger,
        sid,
        authorize_operator=lambda actor: actor == _ACTOR,
        verify_attestation=lambda _db, record: record in (measurement, design, runtime),
    )
    broker.check_delivery(
        f"plugin-scientist-{sid}",
        f"plugin-session-{sid}",
        lineage_id=sid,
        role="scientist",
    )
    return _bind_pseudobulk_capsule(
        flow,
        lifecycle,
        executor,
        (jobs.workspace, flow.ledger.path.parent),
        staged=staged,
        step_id=f"plugin-step-{plan['plan_id']}-pseudobulk",
        branch_id="plugin-pseudobulk",
        profile=profile,
        aim_id=request.aim_id,
        request=query,
        measurements=(measurement,),
        design=design,
        expected_revision=_graph_revision(flow),
    )


def _run_pseudobulk(jobs, rid, plan, flow, broker, executor, parameters):
    from .scrna_general_pseudobulk import stage_general_pseudobulk
    from .scrna_qc_handoff import accepted_qc_selection

    request = _PseudobulkStage.model_validate_json(canonical(parameters["pseudobulk"]))
    specification = request.specification
    counts = flow._read_projection(
        lambda db: next(
            (
                item[0]
                for key, item in flow._state(db).artifacts.items()
                if key.startswith("workbench-counts-")
                and item[0]["sha256"] == specification.source_sha256
            ),
            None,
        )
    )
    if (
        counts is None
        or not _confirmed_unit(counts["biological_unit"])
        or counts["biological_unit"] != specification.unit_column
    ):
        raise OperationBlocked("confirmed_biological_units_required")
    source = _retained_cell_source(
        jobs.workspace, rid, executor, counts, specification.source_sha256
    )
    selection = accepted_qc_selection(
        flow,
        executor,
        request.qc_step_id,
        source_sha256=specification.source_sha256,
    )
    staged = stage_general_pseudobulk(
        source,
        specification,
        source.parent / "pseudobulk",
        qc_selection=selection,
    )
    bound = _bind_pseudobulk(
        jobs, plan, flow, broker, executor, request, staged, request.profile
    )
    summary = _numeric_rows(
        staged.summary,
        (
            "profile_count",
            "missing_profile_count",
            "low_cell_profile_count",
        ),
    )
    return {
        "metrics": {"review_required": True, "step_id": bound["step_id"]},
        "summary_tables": [
            _summary_table("Pseudobulk staging", ["Metric", "Value"], summary)
        ],
        "tool_receipts": [
            {"step": "pseudobulk_staging", "receipt_sha256": staged.provenance_sha256},
            {
                "step": selection.binding.step_id,
                "receipt_sha256": selection.binding.receipt_sha256,
            },
        ],
        "limitations": [
            "Staged donor profiles and design check. Fit requires research review."
        ],
    }


def _step_report(flow, executor, step_id, port) -> dict[str, Any]:
    from devbench.core._hashing import decode_receipt_json

    artifact = flow.current_use(step_id, port, purpose="query")
    with executor.open_output(artifact) as handle:
        raw = handle.read(8 * 1024**2 + 1)
    if len(raw) > 8 * 1024**2:
        return {}
    report = decode_receipt_json(raw)
    if isinstance(report, dict):
        return report
    raise OperationBlocked("invalid_numeric_summary_artifact")


def _numeric_rows(report: dict[str, Any], keys: tuple[str, ...]) -> list[list[Any]]:
    return [
        [key, value]
        for key in keys
        if isinstance(value := report.get(key), (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ]


_CONTACT_LABEL = re.compile(
    r"@|https?://|(?<![0-9])[0-9]{3}-[0-9]{2}-[0-9]{4}(?![0-9])|"
    r"(?<![0-9])(?:\+?1[ .-]?)?(?:\([0-9]{3}\)|[0-9]{3})[ .-]?"
    r"[0-9]{3}[ .-]?[0-9]{4}(?![0-9])",
    re.IGNORECASE,
)


def _receipt_label(value: object, fallback: str) -> str:
    import unicodedata

    if (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value.isprintable()
        and value.strip()
        and not _clinical_name(value)
        and not _CONTACT_LABEL.search(unicodedata.normalize("NFKC", value))
    ):
        return value
    return fallback


def _composition_tables(model: dict[str, Any]) -> list[dict[str, Any]]:
    units = model.get("condition_biological_units", {})
    conditions = {
        label: _receipt_label(label, f"Condition {index}")
        for index, label in enumerate(units, 1)
    }
    return [
        _summary_table(
            "Composition",
            ["Condition", "Biological units"],
            [[conditions[label], count] for label, count in list(units.items())[:20]],
        ),
        _summary_table(
            "Composition effects",
            [
                "Cell type",
                "Covariate",
                "Posterior inclusion probability",
                "Selected",
                "Coefficient",
            ],
            [
                [
                    _receipt_label(row.get("cell_type"), "Withheld"),
                    conditions.get(row.get("condition"), "Withheld"),
                    row["posterior_inclusion_probability"],
                    row["credible_effect"],
                    row["relative_log_coefficient"],
                ]
                for row in model.get("effects", [])[:20]
            ],
        ),
    ]


def _cell_qc_tables(flow, executor, step_id, report):
    rows = _numeric_rows(
        report,
        (
            "cells_input",
            "cells_retained",
            "cells_excluded",
            "mitochondrial_features",
        ),
    )
    rows.extend(_cell_qc_quantiles(flow, executor, step_id))
    groups = report.get("group_retention", {}).get("groups", [])
    return [
        _summary_table("Single-cell QC", ["Metric", "Value"], rows),
        _summary_table(
            "QC retention by group",
            ["Group", "Input", "Retained", "Excluded"],
            [
                [
                    _receipt_label(row.get("group"), f"Group {index}"),
                    row["cells_input"],
                    row["cells_retained"],
                    row["cells_input"] - row["cells_retained"],
                ]
                for index, row in enumerate(groups[:20], 1)
            ],
        ),
    ]


def _cell_qc_quantiles(flow, executor, step_id):
    from io import StringIO
    from statistics import quantiles

    artifact = flow.current_use(step_id, "qc_metrics", purpose="query")
    with executor.open_output(artifact) as handle:
        raw = handle.read(_AXIS_BUDGET + 1)
    if len(raw) > _AXIS_BUDGET:
        return []
    columns = ("total_counts", "n_genes_by_counts", "pct_counts_mt")
    values: dict[str, list[float]] = {key: [] for key in columns}
    for row in csv.DictReader(StringIO(raw.decode())):
        for key in columns:
            if row.get(key) and math.isfinite(value := float(row[key])):
                values[key].append(value)
    return [
        [f"{label}_{metric}", value]
        for label, column in values.items()
        if len(column) > 1
        for metric, value in zip(
            ("p25", "p50", "p75"),
            quantiles(column, method="inclusive"),
            strict=True,
        )
    ]


def _cell_summaries(flow, executor, step_id, outcome) -> list[dict[str, Any]]:
    ports = outcome["outputs"]

    def report(port):
        return _step_report(flow, executor, step_id, port)

    tables = []
    if "qc_report" in ports:
        tables.extend(_cell_qc_tables(flow, executor, step_id, report("qc_report")))
    if "doublet_report" in ports:
        rows = []
        for index, row in enumerate(
            report("doublet_report").get("batches", [])[:20], 1
        ):
            cells, calls = row["cells"], row.get("predicted_doublets")
            rows.append(
                [
                    _receipt_label(row.get("batch"), f"Capture {index}"),
                    cells,
                    calls,
                    calls / cells if cells and calls is not None else None,
                ]
            )
        tables.append(
            _summary_table(
                "Doublet predictions",
                ["Capture", "Cells", "Predictions", "Rate"],
                rows,
            )
        )
    if "integration_report" in ports:
        payload = report("integration_report")
        candidate = payload["conditions"].get(payload["published_condition"], {})
        rows = _numeric_rows(
            candidate.get("batch_mixing", {}),
            (
                "cross_batch_edge_fraction",
                "observed_batches",
            ),
        ) + _numeric_rows(
            candidate.get("biological_conservation", {}),
            (
                "silhouette_label",
                "cells_evaluated",
            ),
        )
        tables.append(
            _summary_table("Integration assessment", ["Metric", "Value"], rows)
        )
    if "composition_result" in ports:
        tables.extend(_composition_tables(report("composition_result")))
    return tables


def _pathway_source(jobs: JobStore, plan: dict[str, Any]):
    from devbench.engine.tier_a_pathways import PATHWAY_OPERATIONS

    from .adaptive_workflow import AdaptiveWorkflow
    from .workbench_adaptive import _graph_revision

    parameters = plan["design"]["parameters"]
    sid = parameters.get("source_session_id") or plan.get("session_id")
    step_id = parameters["step_id"]
    if sid is None:
        raise OperationBlocked("current_verified_pathway_step_required")
    source = jobs.plan(sid)
    if source["data_classification"] not in {"public", "synthetic"}:
        raise OperationBlocked(_PRIVATE)
    roots = {row["sha256"] for row in plan["profiles"]}
    if {row["sha256"] for row in source["profiles"]} != roots:
        raise OperationBlocked("pathway_input_scope_mismatch")
    flow = AdaptiveWorkflow(_ledger(jobs.workspace), sid)
    queue = parameters.get("pathway_plan") is not None
    if queue and sid != plan.get("session_id"):
        raise OperationBlocked("pathway_requires_same_session")
    with flow._read_connection() as db:
        state = flow._state(db)
        step = state.nodes.get(step_id)
        if step is None:
            raise OperationBlocked("current_verified_pathway_step_required")
        if flow.controller_state(connection=db)["binding"] is not None:
            raise OperationBlocked("pathway_controller_owns_dispatch")
        binding = {
            "step_id": step_id,
            "generation": state.generations[step_id],
            "revision": _graph_revision(flow, connection=db),
            "step_sha256": digest(step.model_dump(mode="json")),
        }
        if queue:
            binding["de_source"] = _pathway_de_source(flow, db, state, step_id)
        elif step.operation_id not in PATHWAY_OPERATIONS:
            raise OperationBlocked("current_verified_pathway_step_required")
    return flow, binding


def _pathway_de_source(flow, db, state, step_id):
    from devbench.core.contrast_design import TIER_A_BULK_OPERATION, TIER_A_BULK_OUTPUT
    from devbench.core.joint_bh import JOINT_BH_OUTPUT_PREFIX

    if state.nodes[step_id].operation_id != TIER_A_BULK_OPERATION:
        raise OperationBlocked("pathway_requires_verified_differential_expression")
    outcome = state.outcome(step_id)
    artifact = flow._current_use(db, step_id, TIER_A_BULK_OUTPUT, "compute", None)
    receipt_sha = outcome["receipt_sha256"]
    if artifact.version_id.startswith(JOINT_BH_OUTPUT_PREFIX):
        from .thesis_joint_bh_release import current_joint_bh_release

        release, _ = current_joint_bh_release(flow, db, state, outcome, claim=False)
        receipt_sha = release.receipt_sha256()
    return {
        "artifact": artifact.model_dump(mode="json"),
        "attempt_id": outcome["attempt_id"],
        "receipt_sha256": receipt_sha,
    }


def _pathway_universe(executor, source, *, q_max, ranked):
    import pyarrow.parquet as pq

    from devbench.core.rna_pathway_analysis import (
        TestedGeneUniverse,
        ranked_scores_sha256,
    )
    from devbench.platform.adaptive_workflow_state import ArtifactVersion

    artifact = ArtifactVersion.model_validate(source["artifact"])
    scores, foreground = {}, []
    with executor.open_output(artifact) as stream:
        parquet = pq.ParquetFile(stream)
        if parquet.metadata.num_rows > 100_000:
            raise OperationBlocked("pathway_tested_family_exceeds_row_budget")
        for row in _parquet_rows(parquet):
            if row["estimate_status"] != "estimated":
                continue
            p, q, label = row["p_value"], row["q_value"], row["gene_id"]
            if p is None or q is None or not 0 <= p <= q <= 1:
                raise OperationBlocked("retained_probability_bounds_failed")
            if label in scores:
                raise OperationBlocked("pathway_tested_family_duplicate_gene")
            effect = row["effect"]
            if ranked and (effect is None or not math.isfinite(effect)):
                raise OperationBlocked("pathway_requires_finite_ranked_effects")
            scores[label] = (
                (math.copysign(-math.log10(max(p, 1e-300)), effect) if effect else 0.0)
                if ranked
                else effect
            )
            if not ranked and q <= q_max:
                foreground.append(label)
    genes = tuple(scores)
    return TestedGeneUniverse(
        result_receipt_sha256=source["receipt_sha256"],
        gene_statistics_sha256=artifact.sha256,
        filter_receipt_sha256=digest(
            {
                "artifact": artifact.sha256,
                "ordered_tested_genes": genes,
                "foreground_q_max": q_max,
                "selected_genes": foreground,
            }
        ),
        genes=genes,
        selected=tuple(foreground),
        ranked_scores_sha256=ranked_scores_sha256(genes, tuple(scores.values()))
        if ranked
        else None,
    ), scores


def _pathway_foreground(universe, effects, direction):
    from devbench.core.rna_pathway_analysis import matches_foreground_direction

    return tuple(
        gene
        for gene in universe.selected
        if matches_foreground_direction(effects[gene], direction)
    )


def _queue_reviewed_pathway(flow, executor, plan, binding):
    from devbench.core.contrast_design import TIER_A_BULK_OUTPUT
    from devbench.core.joint_bh import JOINT_BH_OUTPUT_PREFIX
    from devbench.platform.adaptive_workflow_state import BasisUse

    parameters = plan["design"]["parameters"]
    pathway = PathwayPlan.model_validate_json(canonical(parameters["pathway_plan"]))
    q_max = parameters.get("foreground_q_max")
    switch = pathway.method
    if switch not in {"ora", "fgsea", "decoupleR"} or (switch == "ora") != (
        q_max is not None
    ):
        raise OperationBlocked("pathway_requires_explicit_method_and_foreground_rule")

    def require_source(db):
        state = flow._state(db)
        if (
            binding["generation"] != state.generations[binding["step_id"]]
            or _pathway_de_source(flow, db, state, binding["step_id"])
            != binding["de_source"]
            or flow.controller_state(connection=db)["binding"] is not None
        ):
            raise OperationBlocked("pathway_binding_changed")

    source = binding["de_source"]
    universe, scores = _pathway_universe(
        executor, source, q_max=q_max, ranked=switch != "ora"
    )
    artifact = source["artifact"]
    output_id = artifact["version_id"]
    step_id = f"plugin-step-{plan['plan_id']}-pathways"
    proofs = {
        f"{step_id}-source": universe.result_receipt_sha256,
        f"{step_id}-filter": universe.filter_receipt_sha256,
    }
    with flow.authority._execution_tx(flow.lineage_id) as db:
        require_source(db)
        for identity, sha in proofs.items():
            flow.record_basis(identity, "source", sha, connection=db)
    source_link = (
        (output_id, None)
        if output_id.startswith(JOINT_BH_OUTPUT_PREFIX)
        else (binding["step_id"], TIER_A_BULK_OUTPUT)
    )
    step = executor.register_pathway_step(
        flow,
        pathway,
        step_id=step_id,
        branch_id="plugin-pathways",
        biological_unit=artifact["biological_unit"],
        sources={"pathway_statistics": source_link},
        basis=tuple(BasisUse(basis_id=identity) for identity in proofs),
        universe=universe,
        verify_universe=lambda candidate: candidate == universe,
        foreground=_pathway_foreground(universe, scores, pathway.foreground_direction),
        ranked_scores=scores if switch != "ora" else None,
    )
    with flow.authority._execution_tx(flow.lineage_id) as db:
        require_source(db)
        flow.insert(step, connection=db)
    return step_id


def _run_pathway(jobs, plan, ledger, broker, executor):
    from devbench.core.rna_pathway_analysis import PathwayResult
    from devharness_server.tools_rna_pathways import MAX_RESULT_BYTES

    flow, binding = _pathway_source(jobs, plan)
    sid, step_id = flow.lineage_id, binding["step_id"]
    if binding != plan["pathway_binding"]:
        raise OperationBlocked("pathway_binding_changed")
    broker.check_delivery(
        f"plugin-scientist-{sid}",
        f"plugin-session-{sid}",
        lineage_id=sid,
        role="scientist",
    )
    from .workbench_adaptive import _graph_revision

    queued = "de_source" in binding
    if queued:
        step_id = _queue_reviewed_pathway(flow, executor, plan, binding)
    with flow._read_connection() as db:
        if not queued and _graph_revision(flow, connection=db) != binding["revision"]:
            raise OperationBlocked("pathway_binding_changed")
        revision = ledger.scientific._revision(db, sid)
    outcome = flow.dispatch(step_id, executor, actor=_ACTOR, expected_revision=revision)
    if outcome["status"] != "succeeded":
        raise OperationBlocked("pathway_step_did_not_succeed")
    artifact = flow.current_use(step_id, "pathway_result", purpose="query")
    with executor.open_output(artifact) as handle:
        raw = handle.read(MAX_RESULT_BYTES + 1)
    if len(raw) > MAX_RESULT_BYTES:
        raise OperationBlocked("pathway_result_exceeds_custody_budget")
    result = PathwayResult.model_validate_json(raw)
    if result.status != "completed":
        raise OperationBlocked("pathway_result_unavailable")
    summaries, tested = _statistic_summary(
        (row.model_dump() for row in result.results),
        identifier="set_id",
        effect="score",
        caption="Pathway analysis",
    )
    return {
        "metrics": {"method": result.method, "result_rows": len(result.results)},
        "probability_bounds_status": "verified" if tested else "not_reported",
        "summary_tables": summaries,
        "tool_receipts": [
            {"step": step_id, "receipt_sha256": outcome["receipt_sha256"]}
        ],
        "limitations": [
            "Method output from an admitted pathway evidence lineage. "
            "Biological interpretation requires independent review."
        ],
    }


def _manage_aim(plugin, args):
    from .thesis_workflow import ThesisWorkflow
    from .workbench_adaptive import _graph_revision
    from .workbench_thesis_actions import _ACTION_FIELDS, _aim_action

    ledger = _ledger(plugin.jobs.workspace)
    lifecycle = ThesisWorkflow(
        ledger, args.session_id, authorize_operator=lambda a: a == _ACTOR
    )
    flow = lifecycle.flow
    broker = EvidenceBroker.open_existing(ledger.scientific)
    delivery = broker.delivery(
        f"plugin-scientist-{args.session_id}", f"plugin-session-{args.session_id}"
    )
    broker.check_delivery(
        delivery.context_id,
        delivery.source_id,
        lineage_id=args.session_id,
        role="scientist",
    )
    action, request = args.action, args.request
    if action == "list":
        if request:
            raise ValueError("List takes no research aim arguments")
        with flow._read_connection() as db:
            projection, _ = lifecycle.aims._projection(db)
            listing = {
                "session_id": args.session_id,
                "revision": _graph_revision(flow, connection=db),
                "aims": [
                    {
                        "aim_id": row.definition.aim_id,
                        "version": row.version,
                        "status": _aim_status(row),
                    }
                    for row in list(projection.aims.values())[:40]
                ],
            }
        return delivery.release(listing)
    if action == "query_literature_post_evidence":
        return _post_evidence_literature(plugin, args, ledger, broker)
    if action == "build_research_package":
        return _build_research_package(plugin, args, lifecycle)
    if request.keys() != {"expected_revision", *_ACTION_FIELDS[action]}:
        raise ValueError("Use the declared fields for the selected research aim action")
    with flow.authority._execution_tx(args.session_id) as db:
        if type(request["expected_revision"]) is not int or request[
            "expected_revision"
        ] != _graph_revision(flow, connection=db):
            raise OperationConflict("Research aim change requires the current revision")
        _aim_action(
            lifecycle.aims,
            action,
            request,
            _ACTOR,
            {
                "expected_revision": flow.authority._revision(db, args.session_id),
                "connection": db,
            },
        )
        return {
            "session_id": args.session_id,
            "revision": _graph_revision(flow, connection=db),
            "status": "recorded",
        }


def _aim_status(row) -> str:
    if row.invalidated_by:
        return "invalidated"
    if row.outcome:
        return row.outcome.status
    return "approved" if row.approval else "proposed"


def _post_evidence_literature(plugin, args, ledger, broker):
    if plugin.literature_root is None:
        raise OperationBlocked("current_post_evidence_literature_capability_required")
    request = args.request
    if set(request) != {
        "run_id",
        "context_id",
        "binding_sha256",
        "request_id",
        "query",
    }:
        raise ValueError(
            "Use an exact retained evidence run and Literature claim context"
        )
    run = Run.model_validate(
        {"session_id": args.session_id, "run_id": request["run_id"]}
    )
    receipt = plugin.receipt(run)
    if not receipt.get("tool_receipts"):
        raise OperationBlocked("verified_numerical_evidence_required")

    from devbench.analysis.literature_adapter import (
        LiteratureCapability,
        LiteratureRequest,
        LiteratureService,
        _state,
    )

    state = ledger._read(lambda db: _state(db, args.session_id))
    binding = next(
        (
            row["binding"]
            for row in state["claims"].values()
            if digest(row["binding"]) == request["binding_sha256"]
        ),
        None,
    )
    if state["grant"] is None or binding is None:
        raise OperationBlocked("registered_post_evidence_claim_required")
    root = directory(Path(state["grant"]["root"]))
    if not root.is_relative_to(plugin.jobs.workspace):
        raise OperationBlocked("literature_custody_outside_current_workspace")
    selected = LiteratureRequest.model_validate_json(canonical(request["query"]))
    if selected.query and _clinical_name(selected.query):
        raise OperationBlocked(_PRIVATE)
    service = LiteratureService(
        ledger.scientific,
        args.session_id,
        root,
        broker=broker,
        capability=LiteratureCapability(plugin_root=str(plugin.literature_root)),
        limits=state["grant"]["limits"],
        allow_network=True,
        data_scope=plugin.jobs.plan(args.session_id)["data_classification"],
    )
    result = service.execute(
        selected,
        request_id=request["request_id"],
        context_id=request["context_id"],
        binding=binding,
    )
    return {
        key: result[key]
        for key in (
            "status",
            "snapshot_id",
            "query_receipt",
            "scope",
            "failure",
        )
    }


def _apply_package_review(flow, executor, spec) -> None:
    from .workbench_interactive import _OPERATOR_BULK_STEP, operator_bulk_actions

    refs = [*spec.fields.values(), *spec.figures]
    if not any(item.step_id == _OPERATOR_BULK_STEP for item in refs):
        return
    actions = operator_bulk_actions(
        flow, executor, planner=None, step_id=_OPERATOR_BULK_STEP
    )
    actions.prepare_research_admission(flow, _OPERATOR_BULK_STEP)
    flow.review_admission = actions.adaptive_review_admission


def _default_package_schema(spec: dict[str, Any]) -> bytes:
    typed = {
        "failure_reason": {"type": "string", "minLength": 1},
        "tier_a_gene_statistics": {"type": "array", "items": {"type": "object"}},
        "json": {"type": ["object", "array", "string", "number", "boolean", "null"]},
    }
    fields = {
        name: typed[binding.get("projection", "json")]
        for name, binding in spec.get("fields", {}).items()
    }
    return canonical(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": fields,
            "required": sorted(fields),
            "additionalProperties": False,
        }
    )


def _build_research_package(plugin, args, lifecycle):
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )
    from devbench.engine.tier_a_executor import TierAExecutor

    plan = plugin.jobs.plan(args.session_id)
    request = args.request
    from .workbench_adaptive import _graph_revision

    workspace = plugin.jobs.workspace
    destination = workspace / "runs" / uuid4().hex / "research_package"
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    schema_path = request.get("answer_schema")
    spec_document = dict(request["spec"])
    if schema_path is None:
        schema = destination.parent / "answer_schema.json"
        raw_schema = _default_package_schema(spec_document)
        schema.write_bytes(raw_schema)
        schema_sha = hashlib.sha256(raw_schema).hexdigest()
        if spec_document.get("answer_schema_sha256") not in {None, schema_sha}:
            raise OperationBlocked("answer_schema_identity_mismatch")
        spec_document["answer_schema_sha256"] = schema_sha
    else:
        schema = _canonical_input(workspace, schema_path)
    with lifecycle.flow._read_connection() as db:
        if (
            _graph_revision(lifecycle.flow, connection=db)
            != spec_document["expected_revision"]
        ):
            raise OperationConflict(
                "Thesis package requires the current graph revision"
            )
        spec_document["expected_revision"] = lifecycle.flow.authority._revision(
            db,
            args.session_id,
        )
    spec = ThesisSynthesisSpec.model_validate_json(canonical(spec_document))
    executor = TierAExecutor(
        lifecycle.ledger,
        allow_data_execution=False,
        mounted_image_id=plan["mounted_image_id"],
    )
    _apply_package_review(lifecycle.flow, executor, spec)
    package = write_thesis_package(
        destination,
        lifecycle.flow,
        spec,
        answer_schema=schema,
        aim_lifecycle=lifecycle.aims,
        open_artifact=executor.open_output,
        tier_a_executor=executor,
    )
    verified = verify_thesis_package(destination, package["root_sha256"])
    with (destination.parent / "research-package-root.sha256").open("x") as seal:
        seal.write(verified["root_sha256"] + "\n")
    return {
        "status": "verified",
        "package_root_sha256": verified["root_sha256"],
        "clinical_release_allowed": False,
        **plugin._handoff(args.session_id, "evidence"),
    }


_TOOLS: dict[str, tuple[type[BaseModel], str, str, bool, str | None]] = {
    "inspect_rna_inputs": (
        Inspect,
        "inspect",
        "Inspect local RNA input headers and return aggregate metadata.",
        True,
        None,
    ),
    "check_answerability": (
        Answerability,
        "answer",
        "Evaluate the RNA question registry and supplied prerequisite evidence.",
        True,
        None,
    ),
    "plan_rna_workflow": (
        Plan,
        "plan",
        "Freeze inputs and the objective in a scientific session. Starts no run.",
        False,
        "plan-card",
    ),
    "execute_reviewed_plan": (
        Execute,
        "execute",
        "Start the exact locally reviewed plan by session ID, plan ID and SHA-256.",
        False,
        "run-card",
    ),
    "get_run_status": (
        Run,
        "status",
        "Read the local background run, task progress and Studio link.",
        True,
        "run-card",
    ),
    "get_execution_receipt": (
        Run,
        "receipt",
        "Read a verified non-private run receipt. The broker records each disclosure.",
        True,
        "receipt-card",
    ),
    "manage_research_aim": (
        ResearchAim,
        "aims",
        "Work with the bound investigation's research aims and evidence.",
        False,
        "receipt-card",
    ),
}


def _schema_locations(value: Any) -> set[str]:
    if isinstance(value, dict):
        fields = set(value.get("properties", {}))
        mapping = value.get("discriminator", {}).get("mapping", {})
        return fields.union(mapping, *(_schema_locations(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_schema_locations(v) for v in value))
    return set()


def _validation_diagnostics(
    model: type[BaseModel], error: ValidationError
) -> list[dict[str, str]]:
    allowed = _schema_locations(model.model_json_schema())
    dynamic_keys = {"fields", "sources"}
    diagnostics = []
    for detail in error.errors(
        include_url=False, include_context=False, include_input=False
    )[:16]:
        location = detail["loc"]
        kind = detail["type"]
        parts = [
            str(part)
            if (
                isinstance(part, int)
                or (
                    part in allowed
                    and not (i > 0 and location[i - 1] in dynamic_keys)
                    and not (kind == "extra_forbidden" and i == len(location) - 1)
                )
            )
            else "<field>"
            for i, part in enumerate(location)
        ]
        diagnostics.append({"loc": ".".join(parts), "type": kind})
    return diagnostics


def _tool_error(
    plugin: Plugin, name: str, arguments: dict[str, Any], error: Exception
) -> CallToolResult:
    if isinstance(error, OperationBlocked):
        reason = error.state
    elif isinstance(error, (ValueError, KeyError)):
        reason = "invalid_request"
    elif isinstance(
        error, (OSError, OperationConflict, RuntimeError, TypeError, csv.Error)
    ):
        reason = "local_workflow_unavailable"
    else:
        reason = "internal_tool_error"
    result = {"error": "OperationBlocked", "reason": reason}
    if isinstance(error, ValidationError):
        result["validation_errors"] = _validation_diagnostics(_TOOLS[name][0], error)
    sid = arguments.get("session_id") if isinstance(arguments, dict) else None
    if (
        reason == _PRIVATE
        and isinstance(sid, str)
        and re.fullmatch(r"[0-9a-f]{32}", sid)
    ):
        result["studio_url"] = None
        with suppress(OSError, ValueError, RuntimeError, KeyError):
            result["studio_url"] = plugin._handoff(sid, "overview")["studio_url"]
        if result["studio_url"] is None:
            # Configured folders may contain private identifiers. The full local
            # command is also available in the caller's original plan handoff.
            result["launch_hint"] = PRIVATE_LAUNCH_HINT
    if name in _TOOLS and not output_validator(name).is_valid(result):
        result = {"error": "OperationBlocked", "reason": "internal_tool_error"}
    return CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=canonical(result).decode())],
        structuredContent=result,
    )


def _open_studio_handoff(
    workspace: Path, result: dict[str, Any], previous: str | None
) -> str | None:
    from .plugin_jobs import _studio_origin
    from .workbench_ui import _open_browser_async

    link = result.get("studio_url")
    if not isinstance(link, str):
        return previous
    origin = _studio_origin(workspace, offline_only=True)
    if origin is None or previous == origin or not link.startswith(origin + "?"):
        return previous
    try:
        _open_browser_async(link)
    except (OSError, RuntimeError):
        return previous
    return origin


def _checked_tool_success(name: str, result: dict[str, Any]) -> CallToolResult:
    # Validate the actual JSON that crosses the wire. The SDK can include the
    # offending instance in error text if handed an invalid tuple response.
    try:
        payload = canonical(result).decode()
        structured = json.loads(payload)
        if (
            not isinstance(structured, dict)
            or "error" in structured
            or not output_validator(name).is_valid(structured)
        ):
            raise ValueError("Invalid tool result")
        return CallToolResult(
            content=[TextContent(type="text", text=payload)],
            structuredContent=structured,
        )
    except Exception:
        raise OperationBlocked("internal_tool_error") from None


def build_server(
    plugin: Plugin, *, auto_studio: bool = True, open_browser: bool = False
) -> Server:
    from .plugin_mcp_ui import CARD_NAMES, MIME, RESOURCE_META, card_html

    server: Server = Server("devharness-rna", version="0.2.0")
    opened_studio = None

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name=name,
                description=description,
                inputSchema=model.model_json_schema(),
                outputSchema=output_schema(name),
                annotations=ToolAnnotations(
                    readOnlyHint=readonly,
                    destructiveHint=False,
                    openWorldHint=name == "execute_reviewed_plan"
                    or (
                        name == "manage_research_aim"
                        and plugin.literature_root is not None
                    ),
                    idempotentHint=readonly or name == "execute_reviewed_plan",
                ),
                _meta={"ui": {"resourceUri": f"ui://devharness/{card}"}}
                if card
                else None,
            )
            for name, (model, _, description, readonly, card) in _TOOLS.items()
        ]

    @server.list_resources()
    async def list_resources():
        return [
            Resource(
                uri=f"ui://devharness/{card}",
                name=title,
                mimeType=MIME,
                _meta=RESOURCE_META,
            )
            for card, title in CARD_NAMES.items()
        ]

    @server.read_resource()
    async def read_resource(uri):
        address = str(uri)
        card = address.removeprefix("ui://devharness/")
        if address == card or card not in CARD_NAMES:
            raise ValueError("Unknown RNA card")
        return [
            ReadResourceContents(
                content=card_html(card), mime_type=MIME, meta=RESOURCE_META
            )
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        nonlocal opened_studio
        result: dict[str, Any]
        try:
            model, handler, _, readonly, _ = _TOOLS[name]
            if len(canonical(arguments)) > MAX_DOCUMENT_BYTES:
                raise ValueError("Request exceeds the byte budget")
            args = model.model_validate_json(canonical(arguments), strict=True)
            if auto_studio and not readonly:
                # A failed optional startup still leaves a manual Studio handoff.
                with suppress(OSError, ValueError):
                    await anyio.to_thread.run_sync(
                        ensure_offline_studio,
                        plugin.jobs.workspace,
                        plugin.input_root,
                        plugin.mounted_image_id,
                    )
            result = await anyio.to_thread.run_sync(getattr(plugin, handler), args)
            response = _checked_tool_success(name, result)
        except Exception as error:
            # The SDK otherwise echoes unexpected exception text over STDIO.
            return _tool_error(plugin, name, arguments, error)
        if open_browser:
            opened_studio = _open_studio_handoff(
                plugin.jobs.workspace, result, opened_studio
            )
        return response

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="devbench plugin-mcp", description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument(
        "--allow-external-input-files",
        action="store_true",
        help="Allow explicitly named files outside the configured input root.",
    )
    parser.add_argument("--mounted-image-id")
    parser.add_argument("--allow-literature-network", action="store_true")
    parser.add_argument("--literature-plugin-root", type=Path)
    parser.add_argument(
        "--no-auto-studio",
        action="store_true",
        help="Leave Companion Studio startup to the operator.",
    )
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open an offline Studio handoff in the browser.",
    )
    browser.add_argument(
        "--no-auto-browser",
        action="store_false",
        dest="open_browser",
        help="Keep Studio links passive (default).",
    )
    parser.set_defaults(open_browser=False)
    args = parser.parse_args(argv)
    if args.allow_literature_network != (args.literature_plugin_root is not None):
        parser.error(
            "Literature requires explicit network permission and a pinned client root"
        )
    try:
        plugin = Plugin(
            args.workspace,
            args.input_root,
            args.mounted_image_id,
            args.literature_plugin_root,
            allow_external_files=args.allow_external_input_files,
        )
        server = build_server(
            plugin, auto_studio=not args.no_auto_studio, open_browser=args.open_browser
        )

        async def serve():
            async with stdio_server() as (reader, writer):
                await server.run(reader, writer, server.create_initialization_options())

        anyio.run(serve)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"devbench plugin-mcp: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
