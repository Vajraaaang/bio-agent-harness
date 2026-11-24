"""Typed parameters and fixed artifact ports for mounted single-cell stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_serializer,
    model_validator,
)

from devbench.core.trajectory_evidence import DonorTrajectoryEvidence

from .canonical import canonical_json

REQUEST_FORMAT = "devbench.mounted-scrna/1"
SUMMARY_FORMAT = "devbench.mounted-scrna-summary/1"
MAX_REQUEST_BYTES = 32 * 1024
MAX_SUMMARY_BYTES = 1024**2
MAX_TABLE_BYTES = 64 * 1024**2
MAX_MATRIX_BYTES = 512 * 1024**2
COUNT_LAYER = "counts"
NB_INFERENCE_FOLD = "nb_inference_fold_h5ad"
NB_DONOR_ROSTER = "nb_marker_donor_roster_json"
NB_FOLD_AGGREGATE: Final = "scrna_nb_fold_pseudobulk"
NB_MARKER_FIT: Final = "scrna_nb_marker_fit"
DONOR_TRAJECTORY: Final = "fit_donor_trajectory"
NB_PSEUDOBULK_COUNTS = "nb_fold_pseudobulk_h5"
NB_PSEUDOBULK_METADATA = "nb_fold_metadata_csv"
NB_MARKER_FIT_PARQUET = "nb_sealed_marker_fit_parquet"
NB_MARKER_SOLVER_RECEIPT = "nb_sealed_marker_solver_receipt_json"
NB_SEALED_REPRESENTATIONS = frozenset({
    NB_INFERENCE_FOLD, NB_DONOR_ROSTER, NB_PSEUDOBULK_COUNTS,
    NB_PSEUDOBULK_METADATA, NB_MARKER_FIT_PARQUET, NB_MARKER_SOLVER_RECEIPT,
})


_SEALED_NB_PORTS = {
    NB_FOLD_AGGREGATE: {
        "inference_counts": NB_INFERENCE_FOLD, "donor_roster": NB_DONOR_ROSTER,
    },
    NB_MARKER_FIT: {
        "pseudobulk_counts": NB_PSEUDOBULK_COUNTS,
        "pseudobulk_metadata": NB_PSEUDOBULK_METADATA,
    },
}


def is_sealed_nb_input_port(operation: str, port: str, representation: str) -> bool:
    return _SEALED_NB_PORTS.get(operation, {}).get(port) == representation


def marker_fit_basis_sha256(parameters_sha256: str, aggregate_sha256: str) -> str:
    raw = canonical_json({
        "format": "devbench.nb-marker-fit-review/1",
        "parameters_sha256": parameters_sha256,
        "aggregation_report_sha256": aggregate_sha256,
    })
    return sha256(raw).hexdigest()

OperationID = Literal[
    "scrna_batch_qc", "scrna_lane_doublets", "scrna_prepare", "scrna_integrate",
    "scrna_integrate_qualified", "scrna_droplets", "scrna_sccoda",
    "scrna_nb_countsplit", "scrna_nb_fold_cluster", "scrna_nb_fold_pseudobulk",
    "scrna_nb_marker_fit", "fit_donor_trajectory",
]
PortKind = Literal[
    "counts", "continuous_matrix", "droplet_matrix", "embedding", "graph", "table",
    "figure",
]


@dataclass(frozen=True)
class SingleCellPort:
    filename: str
    representation: str
    kind: PortKind = "table"
    max_bytes: int = MAX_TABLE_BYTES
    required: bool = True


@dataclass(frozen=True)
class SingleCellOperation:
    inputs: Mapping[str, SingleCellPort]
    outputs: Mapping[str, SingleCellPort]


_EXPRESSION = SingleCellPort(
    "expression.h5ad", "raw_counts_h5ad", "counts", MAX_MATRIX_BYTES,
)
_QC_METRICS = SingleCellPort("qc_metrics.csv", "scrna_qc_metrics_csv")
_QC_FEATURES = SingleCellPort("qc_features.csv", "scrna_qc_features_csv")
_SELECTED = SingleCellPort(
    "selected.h5ad", "scrna_prepared_h5ad", "continuous_matrix", MAX_MATRIX_BYTES,
)
_NB_CLUSTERING_FOLD = SingleCellPort(
    "clustering.h5ad", "nb_clustering_fold_h5ad", max_bytes=MAX_MATRIX_BYTES,
)
_NB_SPLIT_REPORT = SingleCellPort("split_report.json", "nb_split_report_json")
_NB_CLUSTER_LABELS = SingleCellPort(
    "cluster_labels.json", "nb_fold_cluster_labels_json",
)
_NB_INFERENCE_COUNTS = SingleCellPort(
    "inference.h5ad", NB_INFERENCE_FOLD, max_bytes=MAX_MATRIX_BYTES,
)
_NB_ROSTER = SingleCellPort("donor_roster.json", NB_DONOR_ROSTER, required=False)
_NB_BULK_COUNTS = SingleCellPort(
    "pseudobulk.h5", NB_PSEUDOBULK_COUNTS, max_bytes=256 * 1024**2,
)
_NB_BULK_METADATA = SingleCellPort("metadata.csv", NB_PSEUDOBULK_METADATA)
_NB_AGGREGATION_REPORT = SingleCellPort(
    "aggregation_report.json", "nb_fold_aggregation_report_json",
)
_INTEGRATION_OUTPUTS = MappingProxyType({
    "embedding": SingleCellPort(
        "embedding.csv", "scrna_embedding_csv", "embedding", required=False,
    ),
    "graph": SingleCellPort(
        "graph.npz", "scrna_neighbor_graph_npz", "graph", required=False,
    ),
    "figure": SingleCellPort(
        "embedding.png", "scrna_embedding_png", "figure", required=False,
    ),
    "integration_report": SingleCellPort(
        "integration_report.json", "scrna_integration_report_json",
    ),
})

SCRNA_OPERATIONS: Mapping[OperationID, SingleCellOperation] = MappingProxyType({
    DONOR_TRAJECTORY: SingleCellOperation(
        MappingProxyType({
            "trajectory_expression": SingleCellPort(
                "trajectory_expression.h5ad", "scrna_prepared_h5ad",
                "continuous_matrix", MAX_MATRIX_BYTES,
            ),
            "trajectory_coordinates": SingleCellPort(
                "trajectory_coordinates.csv", "scrna_trajectory_coordinates_csv",
                max_bytes=16 * 1024**2,
            ),
            "trajectory_spec": SingleCellPort(
                "trajectory_spec.json", "scrna_trajectory_spec_json",
                max_bytes=8192,
            ),
        }),
        MappingProxyType({
            "trajectory_result": SingleCellPort(
                "trajectory_result.json", "scrna_donor_trajectory_result_json",
            ),
        }),
    ),
    "scrna_nb_countsplit": SingleCellOperation(
        MappingProxyType({
            "expression": _EXPRESSION,
            "split_dispersion": SingleCellPort(
                "dispersion.json", "nb_countsplit_dispersion_json",
                max_bytes=32 * 1024**2,
            ),
        }),
        MappingProxyType({
            "clustering_counts": _NB_CLUSTERING_FOLD,
            "inference_counts": _NB_INFERENCE_COUNTS,
            "split_report": _NB_SPLIT_REPORT,
            "donor_roster": _NB_ROSTER,
        }),
    ),
    "scrna_nb_fold_cluster": SingleCellOperation(
        MappingProxyType({
            "clustering_counts": _NB_CLUSTERING_FOLD,
            "split_report": _NB_SPLIT_REPORT,
        }),
        MappingProxyType({
            "cluster_labels": _NB_CLUSTER_LABELS,
            "clustering_report": SingleCellPort(
                "clustering_report.json", "nb_fold_clustering_report_json",
            ),
            "graph_assessment": SingleCellPort(
                "graph_assessment.json", "nb_fold_graph_assessment_json",
            ),
        }),
    ),
    NB_FOLD_AGGREGATE: SingleCellOperation(
        MappingProxyType({
            "inference_counts": _NB_INFERENCE_COUNTS,
            "split_report": _NB_SPLIT_REPORT,
            "donor_roster": SingleCellPort("donor_roster.json", NB_DONOR_ROSTER),
            "cluster_labels": _NB_CLUSTER_LABELS,
        }),
        MappingProxyType({
            "pseudobulk_counts": _NB_BULK_COUNTS,
            "pseudobulk_metadata": _NB_BULK_METADATA,
            "aggregation_report": _NB_AGGREGATION_REPORT,
        }),
    ),
    NB_MARKER_FIT: SingleCellOperation(
        MappingProxyType({
            "pseudobulk_counts": _NB_BULK_COUNTS,
            "pseudobulk_metadata": _NB_BULK_METADATA,
            "aggregation_report": _NB_AGGREGATION_REPORT,
        }),
        MappingProxyType({
            "marker_fit": SingleCellPort(
                "genes.parquet", NB_MARKER_FIT_PARQUET, max_bytes=256 * 1024**2,
            ),
            "marker_fit_report": SingleCellPort(
                "marker_fit_report.json", "nb_marker_fit_report_json",
            ),
            "marker_solver_receipt": SingleCellPort(
                "marker_solver_receipt.json", NB_MARKER_SOLVER_RECEIPT,
            ),
        }),
    ),
    "scrna_sccoda": SingleCellOperation(
        MappingProxyType({
            "composition_input": SingleCellPort(
                "composition_input.json", "scrna_composition_input_json",
            ),
            "composition_spec": SingleCellPort(
                "composition_spec.json", "scrna_composition_spec_json",
            ),
        }),
        MappingProxyType({
            "composition_result": SingleCellPort(
                "composition_result.json", "scrna_composition_result_json",
            ),
        }),
    ),
    "scrna_batch_qc": SingleCellOperation(
        MappingProxyType({"expression": _EXPRESSION}),
        MappingProxyType({
            "qc_metrics": _QC_METRICS,
            "qc_features": _QC_FEATURES,
            "qc_report": SingleCellPort("qc_report.json", "scrna_qc_report_json"),
        }),
    ),
    "scrna_lane_doublets": SingleCellOperation(
        MappingProxyType({"expression": _EXPRESSION, "qc_metrics": _QC_METRICS}),
        MappingProxyType({
            "predictions": SingleCellPort(
                "predictions.csv", "scrna_doublet_predictions_csv",
            ),
            "doublet_report": SingleCellPort(
                "doublet_report.json", "scrna_doublet_report_json",
            ),
        }),
    ),
    "scrna_prepare": SingleCellOperation(
        MappingProxyType({
            "expression": _EXPRESSION,
            "qc_metrics": _QC_METRICS,
            "qc_features": _QC_FEATURES,
        }),
        MappingProxyType({
            "selected": _SELECTED,
            "hvg": SingleCellPort("hvg.csv", "scrna_hvg_csv"),
            "normalization_report": SingleCellPort(
                "normalization_report.json", "scrna_normalization_report_json",
                required=False,
            ),
        }),
    ),
    "scrna_integrate": SingleCellOperation(
        MappingProxyType({"selected": _SELECTED}),
        _INTEGRATION_OUTPUTS,
    ),
    "scrna_integrate_qualified": SingleCellOperation(
        MappingProxyType({
            "selected": _SELECTED,
            "labels": SingleCellPort(
                "labels.json", "scrna_external_labels_json", max_bytes=16_000_000,
            ),
            "label_qualification": SingleCellPort(
                "label_qualification.json", "scrna_label_qualification_json",
                max_bytes=16_384,
            ),
        }),
        _INTEGRATION_OUTPUTS,
    ),
    "scrna_droplets": SingleCellOperation(
        MappingProxyType({
            "droplets": SingleCellPort(
                "droplets.h5", "raw_cellranger_v3_h5", "droplet_matrix",
                MAX_MATRIX_BYTES,
            ),
            "droplet_provenance": SingleCellPort(
                "droplet_provenance.json", "raw_droplet_provenance_json",
                max_bytes=16_384,
            ),
        }),
        MappingProxyType({
            "droplet_report": SingleCellPort(
                "droplet_report.json", "scrna_droplet_report_json",
            ),
            "cell_calls": SingleCellPort(
                "cell_calls.csv", "emptydrops_calls_csv", required=False,
            ),
            "corrected_expression": SingleCellPort(
                "corrected.h5", "cellbender_corrected_tenx_h5", "continuous_matrix",
                MAX_MATRIX_BYTES, required=False,
            ),
        }),
    ),
})


Column = Annotated[str, StringConstraints(
    min_length=1, max_length=200, pattern=r"^[^\s\x00](?:[^\x00\r\n]*[^\s\x00])?$",
)]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ImageID = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Seed = Annotated[int, Field(ge=0, lt=2**32)]


class _FrozenParameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, allow_inf_nan=False,
    )


class BatchQCParameters(_FrozenParameters):
    batch_key: Column
    qc_group_key: Column | None = None
    retention_group_key: Column | None = None
    mad_definition: Literal["absolute_mad", "scaled_mad"] = "absolute_mad"
    min_genes: Annotated[int, Field(ge=1, le=100_000)] = 200
    mad_limit: Annotated[float, Field(gt=0)] | None = 5.0
    max_mito_percent: Annotated[float, Field(ge=0, le=100)] | None = None
    symbol_column: Column | None = None


class LaneDoubletParameters(_FrozenParameters):
    batch_key: Column
    capture_key: Column | None = None
    method: Literal["scrublet", "scdblfinder"] = "scrublet"
    seed: Seed = 0
    expected_rate: Annotated[float, Field(gt=0, lt=1)] = 0.05


class MarkerCohortColumns(_FrozenParameters):
    donor_key: Column
    specimen_key: Column
    condition_key: Column

    @model_validator(mode="after")
    def distinct_roles(self) -> MarkerCohortColumns:
        if len({self.donor_key, self.specimen_key, self.condition_key}) != 3:
            raise ValueError("NB marker cohort columns must identify distinct roles")
        return self


class CountSplitParameters(_FrozenParameters):
    seed: Seed
    clustering_fraction: Annotated[float, Field(gt=0, lt=1)]
    marker_cohort_columns: MarkerCohortColumns | None = None

    @model_serializer(mode="wrap")
    def preserve_original_parameters(self, serialize):
        parameters = serialize(self)
        if self.marker_cohort_columns is None:
            parameters.pop("marker_cohort_columns", None)
        return parameters


class CountSplitClusteringParameters(_FrozenParameters):
    seed: Seed
    n_top_genes: Annotated[int, Field(ge=3, le=20_000)] = 2_000
    n_pcs: Annotated[int, Field(ge=2, le=128)] = 30
    resolution: Annotated[float, Field(gt=0, le=20)] = 1.0


class CountSplitPseudobulkParameters(_FrozenParameters):
    condition: Column
    numerator: Column
    denominator: Column

    @model_validator(mode="after")
    def distinct_groups(self) -> CountSplitPseudobulkParameters:
        if self.numerator == self.denominator:
            raise ValueError("NB marker contrast requires distinct cluster labels")
        return self


class CountSplitMarkerFitParameters(CountSplitPseudobulkParameters):
    fdr_threshold: Annotated[float, Field(gt=0, lt=1)]


class PreparationParameters(_FrozenParameters):
    batch_key: Column
    n_top_genes: Annotated[int, Field(ge=3, le=20_000)] = 2_000
    normalization: Literal["shifted_log", "scran"] = "shifted_log"


class IntegrationParameters(_FrozenParameters):
    batch_key: Column
    correction: Literal["none", "harmony", "scvi"] = "harmony"
    n_pcs: Annotated[int, Field(ge=2, le=128)] = 30
    seed: Seed = 0
    resolution: Annotated[float, Field(gt=0, le=20)] = 1.0
    scvi_epochs: Annotated[int, Field(ge=1, le=200)] = 100


class EmptyDropsParameters(_FrozenParameters):
    method: Literal["emptydrops"]
    lower: Annotated[int, Field(ge=1, lt=2**31)] = 100
    niters: Annotated[int, Field(ge=1, le=100_000)] = 10_000
    fdr: Annotated[float, Field(gt=0, lt=1)] = 0.001
    seed: Annotated[int, Field(ge=0, lt=2**31)] = 0


class CellBenderParameters(_FrozenParameters):
    method: Literal["cellbender"]
    expected_cells: Annotated[int, Field(ge=1, lt=1_000_000)]
    total_droplets_included: Annotated[int, Field(ge=2, le=1_000_000)]
    fpr: Annotated[float, Field(gt=0, lt=1)] = 0.01

    @model_validator(mode="after")
    def _scope(self) -> CellBenderParameters:
        if self.total_droplets_included <= self.expected_cells:
            raise ValueError("Expected cells must be fewer than the included droplets")
        return self


DropletParameters = EmptyDropsParameters | CellBenderParameters
_DROPLET_PARAMETERS: TypeAdapter[DropletParameters] = TypeAdapter(
    Annotated[DropletParameters, Field(discriminator="method")],
)
class CompositionParameters(_FrozenParameters):
    """Bayesian parameters arrive through the pinned specification port."""


class DonorTrajectoryParameters(_FrozenParameters):
    """The fixed donor spline specification is a separate pinned artifact."""


_Parameter = (
    BatchQCParameters | LaneDoubletParameters | PreparationParameters
    | IntegrationParameters | DropletParameters | CompositionParameters
    | CountSplitParameters | CountSplitClusteringParameters
    | CountSplitPseudobulkParameters | CountSplitMarkerFitParameters
    | DonorTrajectoryParameters
)
_PARAMETERS: dict[str, type[_Parameter]] = {
    "scrna_batch_qc": BatchQCParameters,
    "scrna_lane_doublets": LaneDoubletParameters,
    "scrna_prepare": PreparationParameters,
    "scrna_integrate": IntegrationParameters,
    "scrna_integrate_qualified": IntegrationParameters,
    "scrna_sccoda": CompositionParameters,
    DONOR_TRAJECTORY: DonorTrajectoryParameters,
    "scrna_nb_countsplit": CountSplitParameters,
    "scrna_nb_fold_cluster": CountSplitClusteringParameters,
    NB_FOLD_AGGREGATE: CountSplitPseudobulkParameters,
    NB_MARKER_FIT: CountSplitMarkerFitParameters,
}


class SingleCellStepSpec(_FrozenParameters):
    """The operation determines the only accepted parameter schema."""

    operation: OperationID
    parameters: _Parameter

    @model_validator(mode="before")
    @classmethod
    def _bind_parameters(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("operation"), str):
            return value
        if "parameters" not in value:
            return value
        if value["operation"] == "scrna_droplets":
            params = _DROPLET_PARAMETERS.validate_python(value["parameters"])
            return {**value, "parameters": params}
        schema = _PARAMETERS.get(value["operation"])
        if schema is None:
            return value
        return {**value, "parameters": schema.model_validate(value["parameters"])}


class MountedSingleCellRequest(SingleCellStepSpec):
    format: Literal["devbench.mounted-scrna/1"]
    inputs: dict[str, Digest]
    runtime_image_id: ImageID | None = None
    trajectory_evidence: DonorTrajectoryEvidence | None = None

    @model_validator(mode="after")
    def _exact_ports(self) -> MountedSingleCellRequest:
        if self.inputs.keys() != SCRNA_OPERATIONS[self.operation].inputs.keys():
            raise ValueError("Mounted single-cell request requires exact input ports")
        if self.trajectory_evidence is not None and self.operation != DONOR_TRAJECTORY:
            raise ValueError("Trajectory evidence is specific to the donor fit")
        return self
