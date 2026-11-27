"""Run a fixed single-cell stage over checksum-bound, read-only input mounts."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from importlib.util import find_spec
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

from devbench.core._files import hash_bounded_file, read_bounded_file, read_bounded_json
from devbench.core._hashing import decode_receipt_json

from .canonical import canonical_json
from .scrna_upstream_contract import (
    COUNT_LAYER,
    DONOR_TRAJECTORY,
    MAX_MATRIX_BYTES,
    MAX_REQUEST_BYTES,
    MAX_SUMMARY_BYTES,
    MAX_TABLE_BYTES,
    SCRNA_OPERATIONS,
    SUMMARY_FORMAT,
    BatchQCParameters,
    CellBenderParameters,
    CountSplitClusteringParameters,
    CountSplitMarkerFitParameters,
    CountSplitParameters,
    CountSplitPseudobulkParameters,
    DropletParameters,
    EmptyDropsParameters,
    IntegrationParameters,
    LaneDoubletParameters,
    MountedSingleCellRequest,
    PreparationParameters,
)
from .scrna_upstream_contract import (
    SingleCellStepSpec as SingleCellStepSpec,
)

if TYPE_CHECKING:
    import pandas as pd

    from devbench.analysis.checkpoints import CheckpointStore
    from devbench.analysis.integration_metrics import ExternalLabelEvidence

_SHA256 = re.compile(r"[0-9a-f]{64}")
_BLOCK_BYTES = 64 * 1024**2
_MAX_H5AD_NODES = 4_096


@dataclass(frozen=True)
class _StageResult:
    artifacts: dict[str, Path]
    status: str = "completed"
    reason: str | None = None


def _file_state(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Mounted files must be unlinked regular files")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _required_inputs(
    inputs: Path,
    request_sha256: str,
) -> tuple[MountedSingleCellRequest, dict[str, Path], dict[Path, tuple[int, ...]]]:
    request_path = inputs / "request.json"
    states = {request_path: _file_state(request_path)}
    raw = read_bounded_file(request_path, MAX_REQUEST_BYTES)
    if sha256(raw).hexdigest() != request_sha256:
        raise ValueError("Mounted single-cell request hash mismatch")
    decoded = decode_receipt_json(raw)
    if canonical_json(decoded) != raw:
        raise ValueError("Mounted single-cell request must be canonical JSON")
    request = MountedSingleCellRequest.model_validate(decoded)
    ports = SCRNA_OPERATIONS[request.operation].inputs
    paths = {port: inputs / descriptor.filename for port, descriptor in ports.items()}
    expected = {request_path, *paths.values()}
    if set(islice(inputs.iterdir(), len(expected) + 1)) != expected:
        raise ValueError("Mounted single-cell inputs require exact static filenames")
    for port, path in paths.items():
        states[path] = _file_state(path)
        if hash_bounded_file(path, ports[port].max_bytes) != request.inputs[port]:
            raise ValueError(f"Mounted single-cell input hash mismatch: {port}")
    return request, paths, states


def _check_hdf5_storage(
    path: Path,
    *,
    prepared: bool,
    matrix_path: str = f"layers/{COUNT_LAYER}",
) -> None:
    import h5py

    with h5py.File(path, "r") as handle:
        if handle.attrs.get("encoding-type") != "anndata":
            raise ValueError("Mounted single-cell expression must be AnnData H5AD")
        totals = _hdf5_storage_totals(handle)
        if totals["obs"] + totals["var"] > MAX_TABLE_BYTES:
            raise ValueError("Mounted H5AD axes exceed the metadata budget")
        if prepared and sum(totals.values()) > MAX_MATRIX_BYTES:
            raise ValueError("Mounted selected H5AD exceeds the materialization budget")
        required = ("obs", "var", matrix_path)
        if not all(key in handle for key in required) or prepared and "X" not in handle:
            raise ValueError("Mounted H5AD requires axes and a raw counts layer")


def _hdf5_storage_totals(handle) -> dict[str, int]:
    import h5py

    pending = [handle]
    seen: set[int] = {h5py.h5o.get_info(handle.id).addr}
    totals: dict[str, int] = {"obs": 0, "var": 0}
    while pending:
        group = pending.pop()
        for name in group:
            if not isinstance(group.get(name, getlink=True), h5py.HardLink):
                raise ValueError("Mounted H5AD must use internal storage")
            node = group[name]
            address = h5py.h5o.get_info(node.id).addr
            if address in seen or len(seen) >= _MAX_H5AD_NODES:
                raise ValueError("Mounted H5AD exceeds its supported object inventory")
            seen.add(address)
            if isinstance(node, h5py.Group):
                pending.append(node)
            else:
                root = node.name.split("/", 2)[1]
                totals[root] = totals.get(root, 0) + _hdf5_dataset_bytes(node)
    return totals


def _hdf5_dataset_bytes(node) -> int:
    from math import prod

    import h5py

    if not isinstance(node, h5py.Dataset) or node.external or node.is_virtual:
        raise ValueError("Mounted H5AD must use internal array storage")
    item_size = node.dtype.itemsize
    if node.dtype.hasobject:
        item_size = max(32, item_size)
    if node.chunks and prod(node.chunks) * item_size > _BLOCK_BYTES:
        raise ValueError("Mounted H5AD array chunk exceeds its read budget")
    return int(node.size * item_size)


def _batch_qc(
    paths: dict[str, Path],
    store: CheckpointStore,
    parameters: BatchQCParameters,
) -> _StageResult:
    from devbench.analysis.streaming import stream_qc

    settings = parameters.model_dump()
    settings["qc_group_key"] = parameters.qc_group_key or parameters.batch_key
    folder = stream_qc(paths["expression"], store, layer=COUNT_LAYER, **settings)
    return _StageResult(
        {
            "qc_metrics": folder / "metrics.csv",
            "qc_features": folder / "features.csv",
            "qc_report": folder / "report.json",
        }
    )


def _bind_qc_stage(
    paths: dict[str, Path],
    store: CheckpointStore,
    batch_key: str,
) -> pd.Index:
    import pandas as pd

    from devbench.analysis.scrna_qc import declared_groups
    from devbench.analysis.streaming import read_table
    from devbench.core.expression_io import open_h5ad

    metrics = read_table(paths["qc_metrics"], label_columns=(batch_key,))
    if "qc_pass" not in metrics or not pd.api.types.is_bool_dtype(
        metrics.qc_pass.dtype
    ):
        raise ValueError("Mounted QC metrics require exact boolean QC pass flags")
    with open_h5ad(paths["expression"], layer=COUNT_LAYER) as source:
        labels = declared_groups(
            source.obs,
            batch_key,
            "Declared batch identities required",
        )
        if not metrics.index.equals(source.obs.index) or batch_key not in metrics:
            raise ValueError("Mounted QC metrics do not match the source cell axis")
        if not metrics[batch_key].equals(labels):
            raise ValueError("Mounted QC metrics do not match the source batch labels")
        if "qc_features" in paths:
            features = read_table(paths["qc_features"])
            if not features.index.equals(source.var.index):
                raise ValueError("Mounted QC features do not match the source features")
            _verify_library_totals(source, metrics, labels.nunique())

    def stage(folder: Path) -> None:
        shutil.copyfile(paths["qc_metrics"], folder / "metrics.csv")
        if "qc_features" in paths:
            shutil.copyfile(paths["qc_features"], folder / "features.csv")

    store.run("qc", stage)
    return metrics.index[metrics.qc_pass]


def _verify_library_totals(source, metrics: pd.DataFrame, batches: int) -> None:
    import numpy as np

    from devbench.analysis.singlecell import validate_counts

    if batches * source.shape[1] * 64 > MAX_MATRIX_BYTES:
        raise ValueError("Mounted HVG batch-feature moments exceed their memory budget")
    if "total_counts" not in metrics or metrics.total_counts.dtype.kind not in "iuf":
        raise ValueError("Mounted QC metrics require numerical full-library totals")
    totals = metrics.total_counts.to_numpy(dtype=np.float64)
    if not np.isfinite(totals).all() or np.any(totals[metrics.qc_pass] <= 0):
        raise ValueError("Mounted QC metrics have invalid full-library totals")
    start = 0
    for block in source.iter_rows(block_rows=128, max_bytes=_BLOCK_BYTES):
        validate_counts(block.X)
        stop = start + block.n_obs
        observed = np.asarray(block.X.sum(axis=1, dtype=np.float64)).ravel()
        if not np.array_equal(totals[start:stop], observed):
            raise ValueError("Mounted QC totals do not match the raw counts layer")
        start = stop


def _doublets(
    paths: dict[str, Path],
    store: CheckpointStore,
    parameters: LaneDoubletParameters,
) -> _StageResult:
    from devbench.analysis.annotation import run_doublets

    retained = _bind_qc_stage(paths, store, parameters.batch_key)
    if parameters.method == "scdblfinder":
        from devbench.core.expression_io import open_h5ad

        from .scrna_capture_doublets import predict_captures

        if parameters.capture_key is None:
            return _StageResult({}, "refused", "capture_library_role_not_declared")
        with open_h5ad(paths["expression"], layer=COUNT_LAYER) as source:
            frame, lanes = predict_captures(
                source,
                source.obs.index.isin(retained),
                parameters.capture_key,
                parameters.batch_key,
                method=parameters.method,
                expected_rates=None,
                default_rate=parameters.expected_rate,
                allow_fallback=False,
                seed=parameters.seed,
                scratch=store.root,
            )
        frame = frame.loc[retained]
        scored = int(frame.doublet_score.count())
        status = (
            "completed"
            if scored and scored == len(frame)
            else ("partial" if scored else "unavailable")
        )
        folder = store.root / "scdblfinder"
        folder.mkdir()
        frame.to_csv(folder / "predictions.csv")
        (folder / "report.json").write_bytes(
            canonical_json(
                dict(
                    status=status,
                    method=parameters.method,
                    capture_key=parameters.capture_key,
                    batches=lanes,
                    filtering="flag_only",
                )
            )
        )
        return _StageResult(
            {
                "predictions": folder / "predictions.csv",
                "doublet_report": folder / "report.json",
            },
            status,
            None if scored else "no_admissible_lane_predictions",
        )
    folder = run_doublets(
        paths["expression"],
        store,
        layer=COUNT_LAYER,
        batch_key=parameters.batch_key,
        capture_key=parameters.capture_key,
        seed=parameters.seed,
        expected_rate=parameters.expected_rate,
        max_batch_bytes=MAX_MATRIX_BYTES,
        require_declared_capture=parameters.capture_key is not None,
    )
    report = read_bounded_json(folder / "report.json", MAX_TABLE_BYTES)
    reason = report.get("reason")
    if report["status"] == "unavailable" and reason is None:
        reason = "no_admissible_lane_predictions"
    return _StageResult(
        {
            "predictions": folder / "predictions.csv",
            "doublet_report": folder / "report.json",
        },
        report["status"],
        reason,
    )


def _prepare(
    paths: dict[str, Path],
    store: CheckpointStore,
    parameters: PreparationParameters,
) -> _StageResult:
    from devbench.analysis.streaming import stream_hvg, stream_selected

    from .scrna_scran import run_scran_factors

    retained = _bind_qc_stage(paths, store, parameters.batch_key)
    use_scran = parameters.normalization == "scran"
    artifacts: dict[str, Path] = {}
    if use_scran:
        folder = run_scran_factors(paths["expression"], store, retained)
        report = artifacts["normalization_report"] = folder / "report.json"
        disposition = read_bounded_json(report, MAX_SUMMARY_BYTES)
        if disposition["status"] != "completed":
            return _StageResult(artifacts, disposition["status"], disposition["reason"])
    hvg = stream_hvg(
        paths["expression"],
        store,
        layer=COUNT_LAYER,
        batch_key=parameters.batch_key,
        n_top_genes=parameters.n_top_genes,
        use_scran_factors=use_scran,
    )
    selected = stream_selected(
        paths["expression"],
        store,
        layer=COUNT_LAYER,
        use_scran_factors=use_scran,
    )
    artifacts.update(selected=selected / "selected.h5ad", hvg=hvg / "features.csv")
    return _StageResult(artifacts)


def _droplets(
    request: MountedSingleCellRequest,
    paths: dict[str, Path],
    directory: Path,
    parameters: DropletParameters,
) -> _StageResult:
    from .scrna_droplets import (
        CellBenderAction,
        DropletAction,
        DropletRuntime,
        EmptyDropsAction,
        RawDropletProvenance,
        run_droplet_action,
    )

    provenance_file = paths["droplet_provenance"]
    raw = read_bounded_file(provenance_file, 16_384)
    record = decode_receipt_json(raw)
    if (
        not isinstance(record, dict)
        or canonical_json(record) != raw
        or record.pop("format", None) != "devbench.raw-droplet-provenance/1"
    ):
        raise ValueError("Mounted droplet provenance must use its canonical format")
    try:
        provenance = RawDropletProvenance(**record)
    except TypeError as error:
        raise ValueError("Mounted droplet provenance fields are invalid") from error
    action: DropletAction
    action_params = parameters.model_dump(exclude={"method"})
    if isinstance(parameters, EmptyDropsParameters):
        action = EmptyDropsAction(**action_params)
        package_version = "1.32.1"
    else:
        action = CellBenderAction(**action_params)
        package_version = "0.4.0"
    runtime = None
    if request.runtime_image_id is not None:
        runtime = DropletRuntime(
            parameters.method,
            request.runtime_image_id,
            package_version,
        )
    outputs = directory / "droplets"
    outputs.mkdir()
    result = run_droplet_action(
        paths["droplets"],
        outputs,
        provenance,
        expected_provenance_sha256=request.inputs["droplet_provenance"],
        action=action,
        runtime=runtime,
    )
    report = directory / "droplet_report.json"
    report.write_bytes(canonical_json(result))
    artifacts = {"droplet_report": report}
    ports = SCRNA_OPERATIONS["scrna_droplets"].outputs
    for name, item in result["artifacts"].items():
        if item["filename"] != ports[name].filename:
            raise ValueError("Unexpected mounted droplet product")
        artifacts[name] = outputs / ports[name].filename
    return _StageResult(artifacts, result["status"], result.get("reason"))


def _bind_selected_stage(
    selected: Path,
    store: CheckpointStore,
    parameters: IntegrationParameters,
) -> None:
    import numpy as np
    from scipy import sparse

    from devbench.analysis.scrna_qc import declared_groups
    from devbench.analysis.singlecell import validate_counts
    from devbench.core.expression_io import open_h5ad

    if parameters.batch_key in {"UMAP_1", "UMAP_2", "leiden"}:
        raise ValueError("Integration batch name conflicts with embedding columns")
    with open_h5ad(selected, layer=COUNT_LAYER) as counts:
        declared_groups(
            counts.obs,
            parameters.batch_key,
            "Declared batch identities required",
        )
        for block in counts.iter_rows(block_rows=128, max_bytes=_BLOCK_BYTES):
            validate_counts(block.X)
    with open_h5ad(selected) as values:
        for block in values.iter_rows(block_rows=128, max_bytes=_BLOCK_BYTES):
            data = block.X.data if sparse.issparse(block.X) else np.asarray(block.X)
            if not np.isfinite(data).all() or (data < 0).any():
                raise ValueError("Selected expression must be finite and nonnegative")

    def stage(folder: Path) -> None:
        shutil.copyfile(selected, folder / "selected.h5ad")

    store.run("selected", stage)


def _integration_outcome(
    parameters: IntegrationParameters,
    report: dict[str, Any],
    accepted: dict[str, str],
) -> tuple[str | None, str, str | None]:
    candidate = parameters.correction
    if candidate in accepted:
        return candidate, "completed", None
    if "uncorrected" not in accepted:
        return None, "rejected", "invalid_uncorrected_embedding"
    if candidate == "none":
        return "uncorrected", "completed", None
    availability = report["integration_status"]["status"]
    if availability != "applicable":
        return "uncorrected", "partial", availability
    status = report["conditions"][candidate]["status"]
    return (
        "uncorrected",
        "partial",
        (
            "requested_correction_unavailable"
            if status == "unavailable"
            else "requested_correction_rejected"
        ),
    )


def _integrate(
    paths: dict[str, Path],
    store: CheckpointStore,
    parameters: IntegrationParameters,
    verified_labels: ExternalLabelEvidence | None,
) -> _StageResult:
    from devbench.analysis.embedding import run_embedding_stages

    _bind_selected_stage(paths["selected"], store, parameters)
    try:
        accepted = run_embedding_stages(
            store,
            **parameters.model_dump(),
            governed=True,
            verified_labels=verified_labels,
        )
    except ValueError as error:
        if str(error) != "invalid_uncorrected_embedding":
            raise
        accepted = {}
    assessment_path = store.path("integration_assessment") / "assessment.json"
    report = read_bounded_json(assessment_path, MAX_TABLE_BYTES)
    condition, status, reason = _integration_outcome(parameters, report, accepted)
    report["published_condition"] = condition
    report["scib"] = _scib_status(report, condition, verified_labels)
    capsule = store.root / "integration_report.json"
    artifacts = {"integration_report": capsule}
    if condition is not None:
        folder = store.path(condition + "_graph")
        artifacts.update(
            embedding=folder / "embedding.csv",
            graph=folder / "neighbors.npz",
        )
        provenance = report["conditions"][condition]["provenance"]
        report["plot"] = _render_figure(
            store.root,
            parameters,
            condition,
            artifacts,
            provenance["embedding_csv_sha256"],
        )
    capsule.write_bytes(canonical_json(report))
    return _StageResult(artifacts, status, reason)


def _scib_status(
    report: dict[str, Any],
    condition: str | None,
    qualification: ExternalLabelEvidence | None,
) -> dict[str, Any]:
    if condition is None:
        return {"status": "unavailable", "reason": "no_admitted_embedding"}
    if qualification is not None:
        conservation = report["conditions"][condition]["biological_conservation"]
        return {**conservation, **conservation.get("benchmark", {})}
    reason = "scib_runtime_unavailable"
    if find_spec("scib_metrics") is not None:
        reason = "verified_external_labels_required"
    return {"status": "unavailable", "reason": reason}


def _render_figure(
    root: Path,
    parameters: IntegrationParameters,
    condition: str,
    artifacts: dict[str, Path],
    assessed_coordinates_sha256: str,
) -> dict[str, Any]:
    from devbench.analysis.embedding import render_integration_embedding

    image = root / "embedding.png"
    plot = render_integration_embedding(
        artifacts["embedding"],
        image,
        batch_key=parameters.batch_key,
        condition=condition,
    )
    if plot["status"] == "rendered":
        if plot["data_sha256"] != assessed_coordinates_sha256:
            raise ValueError("Rendered coordinates do not match the assessed embedding")
        artifacts["figure"] = image
    return plot


def _donor_trajectory(
    request: MountedSingleCellRequest,
    paths: dict[str, Path],
    root: Path,
) -> _StageResult:
    from devbench.core.scrna_donor_trajectory import (
        DonorTrajectorySpec,
        fit_donor_trajectory,
    )

    expression = paths["trajectory_expression"]
    coordinates = paths["trajectory_coordinates"]
    evidence = request.trajectory_evidence
    try:
        raw = read_bounded_file(paths["trajectory_spec"], 8192)
        spec = DonorTrajectorySpec.model_validate_json(raw)
        if (
            canonical_json(spec.model_dump(mode="json")) != raw
            or spec.expression_sha256 != request.inputs["trajectory_expression"]
            or spec.coordinates_sha256 != request.inputs["trajectory_coordinates"]
        ):
            raise ValueError("trajectory_specification_input_mismatch")
        _check_hdf5_storage(
            expression,
            prepared=True,
            matrix_path="X" if spec.layer is None else f"layers/{spec.layer}",
        )
        result = fit_donor_trajectory(
            expression,
            coordinates,
            spec,
            verify_inference=lambda _: evidence is not None,
            evidence=evidence,
        )
    except ValueError as error:
        reason = str(error).split(":", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9_]{1,128}", reason):
            reason = "invalid_donor_trajectory_input"
        return _StageResult({}, "refused", reason)
    output = (
        root / SCRNA_OPERATIONS[DONOR_TRAJECTORY].outputs["trajectory_result"].filename
    )
    output.write_bytes(
        canonical_json(
            {
                **result,
                "verified_mounted_inputs": request.inputs,
                "upstream_receipt_status": (
                    "independently_reviewed"
                    if evidence is not None
                    else "supplied_only"
                ),
                **(
                    {"trajectory_evidence_sha256": evidence.sha256()}
                    if evidence
                    else {}
                ),
            }
        )
    )
    artifacts = {"trajectory_result": output}
    if result["status"] == "no_estimable_genes":
        return _StageResult(artifacts, "refused", "trajectory_no_estimable_genes")
    return _StageResult(artifacts)


def _execute(
    request: MountedSingleCellRequest,
    paths: dict[str, Path],
    store: CheckpointStore,
) -> _StageResult:
    parameters = request.parameters
    if isinstance(parameters, (EmptyDropsParameters, CellBenderParameters)):
        return _droplets(request, paths, store.root, parameters)
    if isinstance(parameters, IntegrationParameters):
        _check_hdf5_storage(paths["selected"], prepared=True)
        return _integrate(paths, store, parameters, _label_input(request, paths))
    if isinstance(parameters, CountSplitClusteringParameters):
        from .scrna_nb_clustering import cluster_mounted_fold

        _check_hdf5_storage(paths["clustering_counts"], prepared=False, matrix_path="X")
        return _StageResult(cluster_mounted_fold(request, paths, store, parameters))
    if isinstance(parameters, CountSplitMarkerFitParameters):
        from .scrna_nb_marker_fit import fit_mounted_markers

        return _StageResult(fit_mounted_markers(request, paths, store.root, parameters))
    if isinstance(parameters, CountSplitPseudobulkParameters):
        from .scrna_nb_pseudobulk import aggregate_mounted_fold

        _check_hdf5_storage(paths["inference_counts"], prepared=False, matrix_path="X")
        artifacts = aggregate_mounted_fold(request, paths, store.root, parameters)
        return _StageResult(artifacts)
    _check_hdf5_storage(paths["expression"], prepared=False)
    if isinstance(parameters, CountSplitParameters):
        from .scrna_nb_countsplit import split_mounted_counts

        artifacts = split_mounted_counts(request, paths, store.root, parameters)
        return _StageResult(artifacts)
    if isinstance(parameters, BatchQCParameters):
        return _batch_qc(paths, store, parameters)
    if isinstance(parameters, LaneDoubletParameters):
        return _doublets(paths, store, parameters)
    if isinstance(parameters, PreparationParameters):
        return _prepare(paths, store, parameters)
    raise ValueError("Operation requires a different mounted worker")


def _label_input(
    request: MountedSingleCellRequest,
    paths: dict[str, Path],
) -> ExternalLabelEvidence | None:
    from devbench.analysis.integration_metrics import ExternalLabelEvidence

    if request.operation != "scrna_integrate_qualified":
        return None
    return ExternalLabelEvidence(
        paths["labels"],
        paths["label_qualification"],
        request.inputs["label_qualification"],
    )


def _publish(
    request: MountedSingleCellRequest,
    request_sha256: str,
    result: _StageResult,
    outputs: Path,
    temporary: Path,
) -> dict[str, Any]:
    ports = SCRNA_OPERATIONS[request.operation].outputs
    summary = {
        "format": SUMMARY_FORMAT,
        "operation": request.operation,
        "request_sha256": request_sha256,
        "claim_scope": "checked_observation",
        "status": result.status,
        "output_sha256": {
            ports[port].filename: hash_bounded_file(path, ports[port].max_bytes)
            for port, path in result.artifacts.items()
        },
    }
    if result.reason is not None:
        summary["reason"] = result.reason
    raw = canonical_json(summary)
    if len(raw) > MAX_SUMMARY_BYTES:
        raise ValueError("Mounted single-cell result summary exceeds its byte bound")
    manifest = temporary / "summary.json"
    manifest.write_bytes(raw)
    published = []
    try:
        for port, path in result.artifacts.items():
            published.append(path.rename(outputs / ports[port].filename))
        manifest.rename(outputs / "summary.json")
    except OSError:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    return summary


def run_mounted_scrna(inputs: Path, outputs: Path, request_sha256: str) -> dict:
    """Admit a canonical request, run its operation and publish verified outputs."""
    from devbench.analysis.checkpoints import CheckpointStore

    if (
        not inputs.is_absolute()
        or not outputs.is_absolute()
        or inputs.resolve() != inputs
        or outputs.resolve() != outputs
        or inputs.is_relative_to(outputs)
        or outputs.is_relative_to(inputs)
        or not inputs.is_dir()
        or not outputs.is_dir()
        or any(outputs.iterdir())
        or not _SHA256.fullmatch(request_sha256)
    ):
        raise ValueError("Expected canonical input/output mounts and a request digest")
    request, paths, source_states = _required_inputs(inputs, request_sha256)
    with tempfile.TemporaryDirectory(prefix=".scrna-", dir=outputs) as directory:
        temporary = Path(directory)
        store = CheckpointStore(
            temporary / "checkpoints",
            {"request_sha256": request_sha256},
        )
        result = (
            _donor_trajectory(request, paths, store.root)
            if request.operation == DONOR_TRAJECTORY
            else _execute(request, paths, store)
        )
        if any(_file_state(path) != state for path, state in source_states.items()):
            raise ValueError("Mounted single-cell input changed during execution")
        return _publish(request, request_sha256, result, outputs, temporary)


def run_m2(
    operation: str,
    paths: dict[str, Path],
    params: dict,
    outputs: Path,
    *,
    primary_count_admitted: bool = False,
) -> dict[str, Path]:
    """Execute an A2 kernel after the coordinator binds its input representations."""
    from .scrna_ambient import ambient
    from .scrna_capture_doublets import capture_doublets
    from .scrna_qc_audit import nuclear_qc
    from .scrna_upstream_contract import M2_QC_PARAMETERS

    kernels = {
        "snrna_droplet_qc": (nuclear_qc, {"expression", "bam_summary"}),
        "scrna_doublets": (capture_doublets, {"expression", "qc_metrics"}),
        "scrna_ambient_decontamination": (ambient, {"expression", "raw_droplets"}),
    }
    kernel, allowed = kernels[operation]
    if "expression" not in paths or set(paths) - allowed:
        raise ValueError("invalid_A2_input_ports")
    _check_hdf5_storage(paths["expression"], prepared=False, matrix_path="X")
    settings = M2_QC_PARAMETERS[operation].model_validate(params)
    if operation == "scrna_ambient_decontamination":
        return ambient(
            paths, settings, outputs, primary_count_admitted=primary_count_admitted
        )
    return kernel(paths, settings, outputs)


def main() -> None:
    from devbench.analysis.resources import configure_threads

    configure_threads()
    if sys.platform == "linux":
        # XLA sizes compile pools from affinity. A CPU quota alone leaves the
        # full host affinity visible and can exhaust the OCI PID limit.
        available_cpus = sorted(os.sched_getaffinity(0))
        os.sched_setaffinity(0, available_cpus[:2])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_sha256")
    arguments = parser.parse_args()
    run_mounted_scrna(Path("/inputs"), Path("/outputs"), arguments.request_sha256)


if __name__ == "__main__":
    main()
