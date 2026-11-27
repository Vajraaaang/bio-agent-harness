"""Bind a two-arm bulk contrast to the reviewed mounted count or continuous worker."""

from __future__ import annotations

import csv
import io
import json
import math
import shutil
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pyarrow.parquet as pq
from scipy.stats import false_discovery_control

from devbench.core._files import hash_bounded_file, read_bounded_file
from devbench.engine.mounted_result_validation import verify_mounted_solver
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import canonical
from devharness_core.storage import ChunkedExpressionStore
from rna_kernels.mounted_bulk import (
    MAX_FEATURES,
    MAX_MATRIX_BYTES,
    _check_matrix_storage,
)
from rna_kernels.mounted_model import MAX_SUMMARY_BYTES

from ._sources import pinned_file


def _raw_value(value):
    parsed = Decimal(value)
    if (
        not parsed.is_finite()
        or parsed < 0
        or parsed > 2**53 - 1
        or parsed != parsed.to_integral_value()
    ):
        raise OperationBlocked("scientific_refusal")
    return int(parsed)


def _csv_expression(binding, stage, measurement):
    from .registry_execution import _table_rows

    stream = iter(_table_rows(binding, MAX_MATRIX_BYTES))
    header = next(stream, None)
    if not header or not 2 <= len(header) <= 4097 or len(set(header)) != len(header):
        raise OperationBlocked("scientific_refusal")
    destination = stage / "expression.h5"
    genes = []
    columns = len(header) - 1
    parser = _raw_value if measurement == "raw_counts" else float
    with h5py.File(destination, "x") as h5:
        string = h5py.string_dtype("utf-8")
        h5.create_dataset("sample_names", data=header[1:], dtype=string)
        counts = h5.create_dataset(
            "expression",
            shape=(0, columns),
            maxshape=(None, columns),
            chunks=(min(256, max(1, 131072 // columns)), columns),
            dtype="f8",
        )
        batch = []
        for row in stream:
            if len(row) != len(header) or not row[0]:
                raise OperationBlocked("scientific_refusal")
            genes.append(row[0])
            if (
                len(genes) > MAX_FEATURES
                or len(genes) * columns * 8 >= MAX_MATRIX_BYTES
            ):
                raise OperationBlocked("resource_quota_exceeded")
            values = tuple(map(parser, row[1:]))
            if not all(map(math.isfinite, values)):
                raise OperationBlocked("scientific_refusal")
            batch.append(values)
            if len(batch) >= counts.chunks[0]:
                counts.resize((len(genes), columns))
                counts[-len(batch) :] = batch
                batch.clear()
        if batch:
            counts.resize((len(genes), columns))
            counts[-len(batch) :] = batch
        if not genes or len(set(genes)) != len(genes):
            raise OperationBlocked("scientific_refusal")
        h5.create_dataset("gene_names", data=genes, dtype=string)
    return destination, genes


def _expression(binding, inputs, measurement):
    if (
        measurement == "raw_counts"
        and binding["detail"].get("count_1_verified") is not True
    ):
        raise OperationBlocked("scientific_refusal")
    if binding["ref"]["format"] in {"csv", "tsv"}:
        return _csv_expression(binding, inputs, measurement)
    if binding["ref"]["format"] != "h5ad":
        raise OperationBlocked("runtime_prerequisite_missing")
    destination = inputs / "expression.h5ad"
    with pinned_file(
        Path(binding["path"]), math.inf, expected=binding["ref"]["sha256"]
    ) as (stream, _, size):
        if size > MAX_MATRIX_BYTES:
            raise OperationBlocked("resource_quota_exceeded")
        with destination.open("xb") as sink:
            shutil.copyfileobj(stream, sink, 1024**2)
    _check_matrix_storage(destination)
    with ChunkedExpressionStore(destination) as matrix:
        return destination, list(matrix.gene_names)


def _metadata(binding):
    from .registry_execution import _table_rows

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for row in _table_rows(binding, 5_000_000):
        writer.writerow(row)
    return stream.getvalue().encode()


def _request(jobs, plan, bindings, stage):
    from .registry_execution import _ports, _write

    counts = plan["kernel"]["kernel_id"] == "bulk_de"
    representation = "count_1_raw_counts" if counts else "size_factor_normalized"
    _ports(bindings, {"expression": representation, "metadata": "metadata_only"})
    measure = "raw_counts" if counts else "continuous"
    inputs = stage / "inputs"
    expression, genes = _expression(bindings["expression"], inputs, measure)
    params = plan["kernel"]["parameters"]
    family = {
        "family_id": "bulk-" + plan["plan_id"],
        "feature_ids": genes,
        "numerator": params["numerator"],
        "denominator": params["denominator"],
        "primary": True,
    }
    request = {
        "format": "devbench.mounted-bulk/1",
        "expression": expression.name,
        "expression_sha256": hash_bounded_file(expression, MAX_MATRIX_BYTES),
        "metadata_sha256": _write(
            inputs / "metadata.csv", _metadata(bindings["metadata"])
        ),
        "family_sha256": _write(inputs / "family.json", canonical(family)),
        "measurement": measure,
        "layer": None,
        "biological_unit": params["biological_unit"],
        "unit_column": params["biological_unit_column"],
        "condition_column": params["condition_column"],
        "technical_factors": params.get("technical_factors", []),
        "continuous_covariates": params.get("continuous_covariates", []),
        "numerator": params["numerator"],
        "denominator": params["denominator"],
    }
    if counts or params.get("unit_design") == "paired_fixed":
        request["model"] = {
            "units": params.get("unit_design", "independent"),
            "comparison": "coefficient",
        }
    return request, family


def _verify_parquet(table, summary, family):
    labels, probabilities, adjusted = [], [], []
    with table.open("rb") as stream:
        reader = pq.ParquetFile(
            stream,
            thrift_string_size_limit=65536,
            thrift_container_size_limit=16384,
            arrow_extensions_enabled=False,
        )
        footer = json.loads(reader.schema_arrow.metadata[b"devbench.result"])
        for key in (
            "row_count",
            "tested_count",
            "request_sha256",
            "status_counts",
            "family_sha256",
        ):
            if summary[key] != footer[key]:
                raise OperationBlocked("plan_identity_mismatch")
        for batch in reader.iter_batches(
            batch_size=512,
            columns=[
                "gene_id",
                "estimate_status",
                "p_value",
                "q_value",
                "missing_reason",
            ],
        ):
            for row in batch.to_pylist():
                labels.append(row["gene_id"])
                p, q = row["p_value"], row["q_value"]
                if row["estimate_status"] == "estimated":
                    if (
                        p is None
                        or q is None
                        or not 0 <= p <= q <= 1
                        or row["missing_reason"] is not None
                    ):
                        raise OperationBlocked("scientific_refusal")
                    probabilities.append(p)
                    adjusted.append(q)
                elif p is not None or q is not None or not row["missing_reason"]:
                    raise OperationBlocked("scientific_refusal")
    if (
        labels != family["feature_ids"]
        or len(probabilities) != summary["tested_count"]
        or len(labels) != summary["row_count"]
        or footer["family_id"] != family["family_id"]
    ):
        raise OperationBlocked("plan_identity_mismatch")
    if probabilities and not np.allclose(
        false_discovery_control(probabilities), adjusted, rtol=1e-10, atol=1e-14
    ):
        raise OperationBlocked("scientific_refusal")


def _verified_table(source, dest, expected):
    with pinned_file(source, math.inf, expected=expected) as (stream, _, size):
        if size > 64 * 1024**2:
            raise OperationBlocked("resource_quota_exceeded")
        with dest.open("xb") as sink:
            shutil.copyfileobj(stream, sink, 1024**2)
    return {
        "path": dest,
        "kind": "table",
        "format": "parquet",
        "representation_class": "derived_summary",
    }


def execute_bulk(jobs, rid, plan, bindings, output_dir, stage, image):
    from .registry_execution import _dispatch

    request, family = _request(jobs, plan, bindings, stage)
    sha = _dispatch(
        jobs, rid, plan, stage, canonical(request), "rna_kernels.mounted_bulk", image
    )
    outputs = stage / "outputs"
    summary = json.loads(read_bounded_file(outputs / "summary.json", MAX_SUMMARY_BYTES))
    if (
        summary["request_sha256"] != sha
        or summary["family_sha256"] != request["family_sha256"]
    ):
        raise OperationBlocked("plan_identity_mismatch")
    spec = SimpleNamespace(
        measurement=request["measurement"],
        family=SimpleNamespace(primary=True),
        mounted_model=SimpleNamespace(**request["model"])
        if "model" in request
        else None,
        biological_unit=request["biological_unit"],
        metadata_sha256=request["metadata_sha256"],
        design=SimpleNamespace(technical_factors=request["technical_factors"]),
        continuous_covariates=request["continuous_covariates"],
    )
    verify_mounted_solver(summary, spec, inputs=stage / "inputs")
    table = _verified_table(
        outputs / "genes.parquet", output_dir / "genes.parquet", summary["genes_sha256"]
    )
    _verify_parquet(table["path"], summary, family)
    files = {"statistics": table}
    sensitivity = summary.get("rank_sensitivity") or {}
    if sensitivity.get("artifact_sha256"):
        files["rank_sensitivity"] = _verified_table(
            outputs / "sensitivity.parquet",
            output_dir / "sensitivity.parquet",
            sensitivity["artifact_sha256"],
        )
    return {
        "metrics": {
            "genes": summary["row_count"],
            "tested": summary["tested_count"],
            "design_rank": summary["design_rank"],
            "residual_df": summary["residual_df"],
        },
        "files": files,
        "tables": [],
        "limitations": [
            "The fit is conditional on the frozen family, contrast and "
            "declared biological units."
        ],
    }
