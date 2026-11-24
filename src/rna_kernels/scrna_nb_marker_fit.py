"""Run the locked paired edgeR procedure without releasing marker statistics."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from shutil import copyfile
from typing import Any

import h5py

from devbench.core._files import read_bounded_file
from devbench.core.scientific_types import content_sha256

from .canonical import canonical_json
from .mounted_bulk import (
    REQUEST_FORMAT,
    _aligned_rows,
    _check_matrix_storage,
    run_mounted_bulk,
)
from .scrna_nb_pseudobulk import _document, _paired_design
from .scrna_upstream_contract import (
    CountSplitMarkerFitParameters,
    MountedSingleCellRequest,
)


def _require_frozen_aggregation(
    request: MountedSingleCellRequest,
    paths: Mapping[str, Path],
    parameters: CountSplitMarkerFitParameters,
) -> dict[str, Any]:
    report = _document(paths["aggregation_report"], "devbench.nb-fold-pseudobulk/1")
    if (
        report.get("pseudobulk_counts_sha256") != request.inputs["pseudobulk_counts"]
        or report.get("pseudobulk_metadata_sha256")
        != request.inputs["pseudobulk_metadata"]
        or report.get("contrast") != parameters.model_dump(exclude={"fdr_threshold"})
        or report.get("marker_inference_performed") is not False
    ):
        raise ValueError("NB marker fit differs from its frozen donor aggregation")
    if (
        report.get("design_status") != "ready"
        or report.get("refusal_codes") != []
        or report.get("missing_profiles") != 0
        or not isinstance(report.get("design"), dict)
    ):
        raise ValueError("NB marker fit requires a complete paired donor design")
    return report


def _verify_design(
    paths: Mapping[str, Path],
    report: dict[str, Any],
    parameters: CountSplitMarkerFitParameters,
) -> list[str]:
    matrix = paths["pseudobulk_counts"]
    _check_matrix_storage(matrix)
    with h5py.File(matrix, "r") as handle:
        features = handle["gene_names"].asstr()[:].tolist()
        sample_ids = handle["sample_names"].asstr()[:].tolist()
    rows = _aligned_rows(
        read_bounded_file(paths["pseudobulk_metadata"], 5_000_000),
        sample_ids,
        ("sample_id", "donor", "specimen", "condition", "library"),
    )
    if (
        content_sha256(features) != report.get("feature_ids_sha256")
        or len(rows) != report.get("observed_profiles")
        or len({row["donor"] for row in rows}) != report.get("paired_donors")
        or _paired_design(rows, parameters) != report["design"]
    ):
        raise ValueError(
            "NB marker fit design or feature axis differs from its receipt"
        )
    return features


def fit_mounted_markers(
    request: MountedSingleCellRequest,
    paths: Mapping[str, Path],
    root: Path,
    parameters: CountSplitMarkerFitParameters,
) -> dict[str, Path]:
    report = _require_frozen_aggregation(request, paths, parameters)
    features = _verify_design(paths, report, parameters)
    inputs, outputs = root / "marker_inputs", root / "marker_outputs"
    inputs.mkdir(mode=0o700)
    outputs.mkdir(mode=0o700)
    copyfile(paths["pseudobulk_counts"], inputs / "expression.h5")
    copyfile(paths["pseudobulk_metadata"], inputs / "metadata.csv")
    contrast = {
        "numerator": parameters.numerator,
        "denominator": parameters.denominator,
    }
    family = canonical_json(
        {
            "family_id": "nb-marker-" + request.inputs["aggregation_report"][:24],
            "feature_ids": features,
            "primary": True,
            **contrast,
        }
    )
    (inputs / "family.json").write_bytes(family)
    native = canonical_json(
        {
            "format": REQUEST_FORMAT,
            "expression": "expression.h5",
            "expression_sha256": request.inputs["pseudobulk_counts"],
            "metadata_sha256": request.inputs["pseudobulk_metadata"],
            "family_sha256": sha256(family).hexdigest(),
            "measurement": "raw_counts",
            "layer": None,
            "biological_unit": "donor",
            "unit_column": "donor",
            "condition_column": "condition",
            "technical_factors": [],
            "model": {"comparison": "coefficient", "units": "paired_fixed"},
            **contrast,
        }
    )
    (inputs / "request.json").write_bytes(native)
    native_sha = sha256(native).hexdigest()
    result = run_mounted_bulk(
        inputs, outputs, native_sha, claim_scope="checked_observation"
    )
    if (
        result["solver"] != "edger_v4"
        or result["test"] != "qlf"
        or result["tested_count"] < 1
    ):
        raise ValueError("NB marker fit requires at least one tested edgeR hypothesis")
    sidecar = root / "marker_fit_report.json"
    sidecar.write_bytes(
        canonical_json(
            {
                "format": "devbench.nb-marker-fit/1",
                "marker_scope": "awaiting_independent_confirmation",
                "parameters": parameters.model_dump(mode="json"),
                "inputs": request.inputs,
                "native_request_sha256": native_sha,
                "design_sha256": report["design"]["design_sha256"],
                "feature_ids_sha256": report["feature_ids_sha256"],
                "family_sha256": result["family_sha256"],
                "marker_fit_sha256": result["genes_sha256"],
                "solver_receipt_sha256": sha256(canonical_json(result)).hexdigest(),
                "solver": result["solver"],
                "solver_version": result["solver_version"],
                "test": result["test"],
                "clinical_release_allowed": False,
            }
        )
    )
    return {
        "marker_fit": outputs / "genes.parquet",
        "marker_fit_report": sidecar,
        "marker_solver_receipt": outputs / "summary.json",
    }
