"""Versioned binary bulk transport for a mounted expression and metadata cohort."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import re
import tempfile
from itertools import islice
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy import stats

from devharness_core.provenance import canonical_json, hash_artifact
from devharness_core.storage import ChunkedExpressionStore, _h5_member

from .bulk_solver_policy import freeze_bulk_solver
from .mounted_count import _REASONS, _fit_count
from .mounted_model import (
    MAX_SUMMARY_BYTES,
    MountedCohort,
    _scaled_covariates,
    compile_mounted_model,
    fit_mounted_model,
    validate_mounted_model,
)
from .mounted_rank import _MIN_UNITS, write_rank_sensitivity

REQUEST_FORMAT = "devbench.mounted-bulk/1"
SUMMARY_FORMAT = "devbench.mounted-bulk-summary/1"
MAX_MATRIX_BYTES = 512 * 1024**2
MAX_PARQUET_BYTES = 256 * 1024**2
MAX_FEATURES = 100_000
_REQUEST_FIELDS = frozenset(
    {
        "format",
        "expression",
        "expression_sha256",
        "metadata_sha256",
        "family_sha256",
        "measurement",
        "layer",
        "biological_unit",
        "unit_column",
        "condition_column",
        "technical_factors",
        "numerator",
        "denominator",
    }
)
_SHA256 = re.compile(r"[a-f0-9]{64}")
_EXPRESSION = re.compile(r"expression\.(h5|h5ad|zarr)")
_RESULT_SCHEMA = pa.schema(
    [
        pa.field("gene_id", pa.string(), nullable=False),
        pa.field("hypothesis_id", pa.string(), nullable=False),
        pa.field("estimate_status", pa.string(), nullable=False),
        pa.field("coordinate_ids", pa.list_(pa.string()), nullable=False),
        pa.field("values", pa.list_(pa.float64()), nullable=False),
        pa.field(
            "intervals",
            pa.list_(
                pa.struct(
                    [
                        ("lower", pa.float64()),
                        ("upper", pa.float64()),
                        ("confidence_level", pa.float64()),
                    ]
                )
            ),
            nullable=False,
        ),
        pa.field("effect", pa.float64()),
        pa.field("p_value", pa.float64()),
        pa.field("q_value", pa.float64()),
        pa.field("missing_reason", pa.string()),
    ]
)


def _read_bound_file(path, expected, maximum):
    identity = hash_artifact(path, max_bytes=maximum, max_entries=1)
    if identity["kind"] != "file" or identity["sha256"] != expected:
        raise ValueError(f"Mounted {path.stem} hash mismatch")
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Mounted {path.stem} hash mismatch")
    return data


def _unique_pairs(items):
    result = dict(items)
    if len(result) != len(items):
        raise ValueError("Duplicate mounted manifest key")
    return result


def _object(raw):
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if type(value) is not dict:
        raise ValueError("Expected a mounted JSON object")
    return value


def _label(value):
    return type(value) is str and 0 < len(value) <= 200 and value == value.strip()


def _request(inputs, expected):
    record = _object(
        _read_bound_file(inputs / "request.json", expected, MAX_SUMMARY_BYTES)
    )
    if (
        set(record) - {"continuous_covariates", "model"} != _REQUEST_FIELDS
        or record["format"] != REQUEST_FORMAT
    ):
        raise ValueError("Unsupported mounted bulk request")
    if (
        type(record["expression"]) is not str
        or not _EXPRESSION.fullmatch(record["expression"])
        or record["measurement"]
        not in ("continuous", "raw_counts", "tximport_estimated_counts")
        or record["biological_unit"]
        not in ("donor", "organism", "experimental_culture", "donor_derived_cell_line")
        or record["layer"] is not None
        and (not _label(record["layer"]) or not record["expression"].endswith(".h5ad"))
        or not all(
            _label(record[key])
            for key in ("condition_column", "unit_column", "numerator", "denominator")
        )
        or record["numerator"] == record["denominator"]
    ):
        raise ValueError("Unsupported mounted matrix, measurement, unit or contrast")
    factors = record["technical_factors"]
    covariates = record.setdefault("continuous_covariates", [])
    if not all(
        type(terms) is list and len(terms) <= 16 and all(map(_label, terms))
        for terms in (factors, covariates)
    ):
        raise ValueError("Unsupported mounted technical factors")
    roles = [
        "sample_id",
        record["unit_column"],
        record["condition_column"],
        *factors,
        *covariates,
    ]
    if len(set(roles)) != len(roles):
        raise ValueError("Mounted metadata roles must be distinct")
    if not all(
        type(record[key]) is str and _SHA256.fullmatch(record[key])
        for key in ("expression_sha256", "metadata_sha256", "family_sha256")
    ):
        raise ValueError("Invalid mounted artifact hash")
    if "model" in record or record["measurement"] == "tximport_estimated_counts":
        validate_mounted_model(record.get("model"), record["measurement"])
    return record


def _feature_axis(raw, request):
    family = _object(raw)
    genes = family.get("feature_ids")
    if (
        type(genes) is not list
        or not 0 < len(genes) <= MAX_FEATURES
        or not all(map(_label, genes))
        or len(set(genes)) != len(genes)
        or not _label(family.get("family_id"))
        or type(family.get("primary")) is not bool
        or any(family.get(key) != request[key] for key in ("numerator", "denominator"))
    ):
        raise ValueError(
            "Mounted family requires a distinct feature axis and the same contrast"
        )
    return family["family_id"], genes, family["primary"]


def _aligned_rows(raw, sample_ids, roles):
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), strict=True)
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not set(roles).issubset(fields):
        raise ValueError("Mounted metadata is missing distinct design roles")
    rows = {}
    for row in reader:
        if (
            None in row
            or not all(_label(row.get(role)) for role in roles)
            or row["sample_id"] in rows
            or len(rows) == len(sample_ids)
        ):
            raise ValueError("Invalid mounted metadata row")
        rows[row["sample_id"]] = row
    if set(rows) != set(sample_ids):
        raise ValueError("Mounted metadata and expression sample axes differ")
    return [rows[sample] for sample in sample_ids]


def _metadata(raw, sample_ids, request):
    condition = request["condition_column"]
    unit = request["unit_column"]
    factors = request["technical_factors"]
    numeric = request["continuous_covariates"]
    ordered = _aligned_rows(
        raw, sample_ids, ("sample_id", condition, unit, *factors, *numeric)
    )
    if "model" in request:
        model = compile_mounted_model(ordered, request)
        return (
            (model.design, model.unit_ids, model.strata, model.covariates),
            model.condition_number,
            model,
        )
    if len({row[unit] for row in ordered}) != len(ordered):
        raise ValueError(
            "Independent bulk requires one profile per declared biological unit"
        )
    labels = np.asarray([row[condition] for row in ordered])
    denominator, numerator = request["denominator"], request["numerator"]
    if set(labels) != {denominator, numerator}:
        raise ValueError("Mounted metadata requires the two frozen condition levels")
    target = labels == numerator
    if min(np.count_nonzero(target), np.count_nonzero(~target)) < 3:
        raise ValueError(
            "Mounted contrast requires at least three biological units per condition"
        )
    covariates = (
        _scaled_covariates(
            np.asarray([[float(row[name]) for name in numeric] for row in ordered])
        )
        if numeric
        else None
    )
    strata = (
        [json.dumps([row[name] for name in factors]) for row in ordered]
        if factors
        else None
    )
    design, condition_number = _condition_design(ordered, target, factors, covariates)
    return (
        (design, [row[unit] for row in ordered], strata, covariates),
        condition_number,
        None,
    )


def _condition_design(rows, target, factors, covariates):
    columns = [np.ones(len(target)), target]
    for factor in factors:
        codes = np.asarray([row[factor] for row in rows])
        categories = sorted(set(codes))[1:]
        if len(columns) + len(categories) >= len(target):
            raise ValueError("Mounted design has no residual support")
        for category in categories:
            columns.append(codes == category)
    if covariates is not None:
        columns.extend(covariates.T)
    design = np.column_stack(columns).astype(float)
    rank = int(np.linalg.matrix_rank(design))
    condition_number = float(np.linalg.cond(design))
    if rank != design.shape[1] or rank >= len(target) or condition_number > 1000:
        raise ValueError("Mounted design rank, conditioning or residual support failed")
    return design, condition_number


def _bh(probabilities, codes):
    eligible = codes != 1
    family = probabilities[eligible]
    padded = np.where(codes[eligible] == 0, family, 1.0)
    adjusted = np.full_like(probabilities, np.nan)
    adjusted[eligible] = np.maximum(family, stats.false_discovery_control(padded))
    return adjusted


def _gaussian_fit(source, design, *, contrast=None, reduced=None):
    inverse = np.linalg.pinv(design)
    df = design.shape[0] - design.shape[1]
    target = inverse[1] if contrast is None else contrast @ inverse
    variance_multiplier = float(target @ target) / df
    effects, errors = np.empty(source.shape[0]), np.empty(source.shape[0])
    filtered = np.empty(source.shape[0], dtype=bool)
    if reduced is not None:
        reduced_inverse = np.linalg.pinv(reduced)
        test_df = design.shape[1] - reduced.shape[1]
        probabilities = np.full(source.shape[0], np.nan)
    start = 0
    for _, data in source.iter_gene_chunks(256):
        if data.dtype.kind not in "iuf" or not np.isfinite(data).all():
            raise ValueError("Continuous mounted expression must be finite and numeric")
        fitted = data @ inverse.T
        residuals = data - fitted @ design.T
        stop = start + len(data)
        effects[start:stop] = fitted[:, 1] if contrast is None else data @ target
        sum_squares = np.einsum("ij,ij->i", residuals, residuals)
        scale = np.max(np.abs(data), axis=1)
        filtered[start:stop] = scale == 0
        residual_resolution = np.finfo(float).eps * scale * len(design)
        errors[start:stop] = np.where(
            sum_squares > residual_resolution**2,
            np.sqrt(sum_squares * variance_multiplier),
            0,
        )
        if reduced is not None:
            null_residuals = data - (data @ reduced_inverse.T) @ reduced.T
            null_ss = np.einsum("ij,ij->i", null_residuals, null_residuals)
            valid = (sum_squares > residual_resolution**2) & np.isfinite(null_ss)
            probability = probabilities[start:stop]
            statistic = (
                np.maximum(null_ss[valid] - sum_squares[valid], 0)
                / test_df
                / (sum_squares[valid] / df)
            )
            probability[valid] = stats.f.sf(statistic, test_df, df)
        start = stop
    if start != source.shape[0]:
        raise ValueError("Mounted matrix scan did not cover the family")
    if reduced is None:
        valid = np.isfinite(effects) & np.isfinite(errors) & (errors > 0)
        probabilities = np.full(len(effects), np.nan)
        probabilities[valid] = 2 * stats.t.sf(
            np.abs(effects[valid] / errors[valid]), df
        )
        confidence = float(stats.t.ppf(0.975, df))
    else:
        effects.fill(np.nan)
        errors = confidence = None
    codes = np.where(np.isfinite(probabilities), 0, np.where(filtered, 1, 4))
    return effects, errors, probabilities, confidence, codes


def _rows(
    genes,
    effects,
    probabilities,
    adjusted,
    codes,
    *,
    measurement=None,
    errors=None,
    confidence=None,
    quantities=None,
):
    for index, gene in enumerate(genes):
        effect, p_value, code = effects[index], probabilities[index], codes[index]
        attempted = code == 0
        directed = attempted and np.isfinite(effect)
        status, reason = ("estimated", None) if attempted else _REASONS[code]
        if code == 1 and measurement == "continuous":
            reason = "all_zero_gene"
        intervals = []
        if directed and confidence is not None and errors is not None:
            half_width = confidence * errors[index]
            intervals = [
                {
                    "lower": effect - half_width,
                    "upper": effect + half_width,
                    "confidence_level": 0.95,
                }
            ]
        yield {
            "gene_id": gene,
            "hypothesis_id": gene,
            "estimate_status": status,
            "coordinate_ids": ["mean_contrast"] if directed else [],
            "values": [effect] if directed else [],
            "intervals": intervals,
            "effect": effect if directed else None,
            "p_value": p_value if attempted else None,
            "q_value": adjusted[index] if attempted else None,
            "missing_reason": reason,
            **{
                key: None if np.isnan(values[index]) else values[index]
                for key, values in (quantities or {}).items()
            },
        }


def _expression_identity(path, expected):
    identity = hash_artifact(path, max_bytes=MAX_MATRIX_BYTES, max_entries=100_000)
    if identity["sha256"] != expected or (identity["kind"] == "directory") != (
        path.suffix == ".zarr"
    ):
        raise ValueError("Mounted expression hash or file type mismatch")


def _zarr_header(path):
    with path.open("rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("Mounted Zarr metadata exceeds its byte bound")
    return _object(raw)


def _check_zarr_array(path, name, dimensions, types, axis_maximum):
    spec = _zarr_header(path / name / ".zarray")
    for field in ("shape", "chunks"):
        values = spec.get(field)
        if (
            type(values) is not list
            or len(values) != dimensions
            or any(
                type(number) is not int or not 0 < number <= axis_maximum
                for number in values
            )
        ):
            raise ValueError("Mounted Zarr dimensions exceed their bounds")
    encoding = spec.get("dtype")
    if type(encoding) is not str or len(encoding) > 12:
        raise ValueError("Unsupported mounted Zarr dtype")
    dtype = np.dtype(encoding)
    if (
        dtype.kind not in types
        or math.prod(spec["chunks"]) * dtype.itemsize > 16 * 1024**2
    ):
        raise ValueError("Unsupported mounted Zarr dtype or chunk size")
    compressor = spec.get("compressor")
    if (
        spec.get("zarr_format") != 2
        or spec.get("filters") not in (None, [])
        or compressor is not None
        and (
            type(compressor) is not dict
            or compressor.get("id") not in {"blosc", "gzip", "zlib", "zstd"}
        )
    ):
        raise ValueError("Unsupported mounted Zarr codec")


def _check_matrix_storage(path):
    if path.suffix == ".h5":
        import h5py

        with h5py.File(path, "r") as handle:
            for name, dimensions, shape_bound in (
                ("expression", 2, (MAX_FEATURES, 4096)),
                ("gene_names", 1, (MAX_FEATURES,)),
                ("sample_names", 1, (4096,)),
            ):
                node = _h5_member(handle, name, field=name)
                if (
                    not isinstance(node, h5py.Dataset)
                    or node.ndim != dimensions
                    or any(
                        not 0 < size <= limit
                        for size, limit in zip(node.shape, shape_bound, strict=True)
                    )
                ):
                    raise ValueError("Mounted HDF5 dimensions exceed their bounds")
    elif path.suffix == ".zarr":
        if _zarr_header(path / ".zgroup").get("zarr_format") != 2:
            raise ValueError("Mounted Zarr requires a v2 group")
        _check_zarr_array(path, "expression", 2, "iuf", MAX_FEATURES)
        _check_zarr_array(path, "gene_names", 1, "US", MAX_FEATURES)
        _check_zarr_array(path, "sample_names", 1, "US", 4096)


def _fit_python_counts(source, genes, design, unit_ids, request, outputs):
    from .bulk_pydeseq2_wald import fit_pydeseq2_wald

    if math.prod(source.shape) * 8 > 100_000_000:
        raise ValueError(
            "Python-only Wald matrix exceeds the 100 MB analytical input bound"
        )
    values = np.empty(source.shape, dtype=float)
    start = 0
    for _, block in source.iter_gene_chunks(256):
        values[start : start + len(block)] = block
        start += len(block)
    if start != source.shape[0]:
        raise ValueError("Python-only Wald scan did not cover the measured family")
    numerator, denominator = request["numerator"], request["denominator"]
    conditions = np.where(design[:, 1] == 1, numerator, denominator).tolist()
    effects, probabilities, adjusted, codes, method = fit_pydeseq2_wald(
        values,
        genes,
        conditions,
        unit_ids,
        numerator=numerator,
        denominator=denominator,
        measurement="raw_counts",
        design=design,
        scratch_dir=outputs,
    )
    return effects, probabilities, codes, method, None, None, adjusted


def _fit(source, genes, design, unit_ids, request, outputs, role, runtime):
    if request["measurement"] == "continuous":
        effects, errors, probabilities, confidence, codes = _gaussian_fit(
            source, design
        )
        return (
            effects,
            probabilities,
            codes,
            {"solver": "gaussian_ols"},
            errors,
            confidence,
            None,
        )
    choice = freeze_bulk_solver(
        measurement="raw_counts",
        runtime=runtime,
        comparison="coefficient",
        units="independent",
        primary=role,
        single_factor=not (
            request["technical_factors"] or request["continuous_covariates"]
        ),
    )
    if choice.solver == "pydeseq2":
        return _fit_python_counts(source, genes, design, unit_ids, request, outputs)
    result = _fit_count(source, design, outputs, solver=choice.solver)
    return (*result, None, None, None)


def _fit_general(source, model, request, outputs, primary, runtime, lengths):
    options = request["model"]
    if request["measurement"] == "continuous" and options["units"] != "mixed":
        choice = freeze_bulk_solver(
            measurement="continuous",
            runtime=runtime,
            comparison=options["comparison"],
            units=options["units"],
            primary=primary,
            single_factor=not (
                request["technical_factors"] or request["continuous_covariates"]
            ),
        )
        effects, errors, probabilities, confidence, codes = _gaussian_fit(
            source,
            model.design,
            contrast=model.contrast,
            reduced=model.reduced,
        )
        df_test = (
            1
            if model.reduced is None
            else model.design.shape[1] - model.reduced.shape[1]
        )
        return (
            effects,
            probabilities,
            codes,
            {"solver": choice.solver, "test": choice.test, "test_df": df_test},
            errors,
            confidence,
            None,
        )
    if runtime == "python_only" and options == {
        "comparison": "coefficient",
        "units": "independent",
    }:
        return _fit(
            source,
            source.gene_names,
            model.design,
            model.unit_ids,
            request,
            outputs,
            primary,
            runtime,
        )
    effects, probabilities, adjusted, codes, evidence = fit_mounted_model(
        source,
        model,
        request,
        primary,
        runtime,
        outputs,
        lengths,
    )
    return effects, probabilities, codes, evidence, None, None, adjusted


def _rank_evidence(primary, request, cohort, location, expression, solver, primary_fit):
    n_units = len(set(cohort[1]))
    if solver.get("rank_sensitivity_planned") and n_units < _MIN_UNITS:
        return {
            "status": "planned",
            "method": "rank_or_permutation",
            "n_units": n_units,
            "execution_minimum_units": _MIN_UNITS,
            "reason": "The frozen rank method requires 100 distinct units",
        }
    if not primary or n_units < _MIN_UNITS:
        return None
    model = request.get("model", {})
    units = model.get("units", "independent")
    comparison = model.get("comparison", "coefficient")
    measurement = request["measurement"]
    if (
        comparison != "coefficient"
        or units != "independent"
        or measurement not in {"raw_counts", "continuous"}
    ):
        return {
            "status": "not_applicable",
            "reason": (
                "Frozen rank sensitivity requires an independent condition "
                "coefficient with raw counts or continuous expression. "
                f"This model uses {measurement}, {comparison}, and {units}."
            ),
            "n_units": n_units,
        }
    normalization = solver.get("normalization", {})
    sizes = normalization.get("size_factors")
    with ChunkedExpressionStore(expression, layer=request["layer"]) as source:
        return write_rank_sensitivity(
            source,
            location,
            request,
            cohort,
            primary_fit,
            factors=np.asarray(sizes) if sizes is not None else None,
            normalization_method="TMM"
            if normalization.get("method") == "TMM"
            else "RLE",
        )


def _fit_mounted_inputs(inputs, outputs, request, genes, primary, runtime):
    metadata = _read_bound_file(
        inputs / "metadata.csv", request["metadata_sha256"], 5_000_000
    )
    expression = inputs / request["expression"]
    lengths_digest = request.get("model", {}).get("lengths_sha256")
    lengths = inputs / "lengths.h5" if lengths_digest is not None else None
    if lengths is not None:
        _expression_identity(lengths, lengths_digest)
        _check_matrix_storage(lengths)
    _expression_identity(expression, request["expression_sha256"])
    _check_matrix_storage(expression)
    with ChunkedExpressionStore(expression, layer=request["layer"]) as source:
        if source.gene_names != genes or source.shape[1] > 4096:
            raise ValueError(
                "Mounted expression differs from the complete ordered feature axis "
                "or sample bound"
            )
        cohort, condition, model = _metadata(metadata, source.sample_names, request)
        design, unit_ids, _, _ = cohort
        fit_request = request
        if (
            model is None
            and primary
            and runtime == "bioconductor"
            and request["measurement"] == "raw_counts"
        ):
            # The original two-arm protocol carries its contrast implicitly.
            # Retain its exact design compiler and forward the admitted design
            # to the same pre-fit arbitration and R transport as explicit models.
            fit_request = {
                **request,
                "model": {"comparison": "coefficient", "units": "independent"},
            }
            contrast = np.zeros(design.shape[1])
            contrast[1] = 1.0
            labels = np.where(
                design[:, 1] == 1, request["numerator"], request["denominator"]
            ).tolist()
            model = MountedCohort(
                design,
                condition,
                unit_ids,
                labels,
                cohort[2],
                cohort[3],
                None,
                contrast,
            )
        fit = (
            _fit_general(source, model, fit_request, outputs, primary, runtime, lengths)
            if model is not None
            else _fit(
                source, genes, design, unit_ids, request, outputs, primary, runtime
            )
        )
    _expression_identity(expression, request["expression_sha256"])
    if lengths is not None:
        _expression_identity(lengths, lengths_digest)
    return expression, cohort, condition, fit


def _latent_score_metadata(solver, samples):
    scores = solver.pop("_latent_score_bytes", None)
    if scores is None:
        return {}
    # Keep each encoded value below the governed Parquet reader's 64 KiB limit.
    chunk_size = 32 * 1024
    prefix = "devbench.latent_scores."
    chunks = {
        f"{prefix}{index}": base64.b64encode(scores[start : start + chunk_size])
        for index, start in enumerate(range(0, len(scores), chunk_size))
    }
    latent = solver["latent_diagnostics"]
    latent["scores"] = {
        "payload_sha256": hashlib.sha256(scores).hexdigest(),
        "parquet_metadata_prefix": prefix,
        "chunks": len(chunks),
        "encoding": "base64_float64_little_endian_column_major",
        "shape": [samples, latent["n_factors"]],
        "sample_order": "expression_columns",
    }
    return chunks


def run_mounted_bulk(
    inputs: Path,
    outputs: Path,
    request_sha256: str,
    *,
    runtime="bioconductor",
    claim_scope="admitted_research_inference",
):
    """Check the mount identities and publish the Parquet and summary together."""
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
        or claim_scope not in {"admitted_research_inference", "checked_observation"}
    ):
        raise ValueError(
            "Expected disjoint canonical mounts, empty outputs and request hash"
        )
    request = _request(inputs, request_sha256)
    family_id, genes, primary = _feature_axis(
        _read_bound_file(inputs / "family.json", request["family_sha256"], 16_000_000),
        request,
    )
    expression, cohort, condition, fit = _fit_mounted_inputs(
        inputs,
        outputs,
        request,
        genes,
        primary,
        runtime,
    )
    design = cohort[0]
    effects, probabilities, codes, solver, errors, confidence, adjusted = fit

    if adjusted is None:
        adjusted = _bh(probabilities, codes)
    score_metadata = _latent_score_metadata(solver, design.shape[0])
    tested = int(np.count_nonzero(codes == 0))
    filtered = int(np.count_nonzero(codes == 1))
    status_counts = {
        key: count
        for key, count in (
            ("estimated", tested),
            ("filtered", filtered),
            ("failed", len(genes) - tested - filtered),
        )
        if count
    }
    footer = {
        "format": "devbench.tier-a-result/1",
        "target_id": "mean_contrast",
        "family_id": family_id,
        "row_count": len(genes),
        "tested_count": tested,
        "status_counts": status_counts,
        "request_sha256": request_sha256,
        "family_sha256": request["family_sha256"],
        "measurement": request["measurement"],
        "biological_unit": request["biological_unit"],
        "claim_scope": claim_scope,
    }
    if "model" in request:
        footer["comparison"] = request["model"]["comparison"]
    quantities = solver.pop("_model_quantities", {})
    schema = pa.schema(
        [*_RESULT_SCHEMA, *(pa.field(key, pa.float64()) for key in quantities)],
        metadata={b"devbench.result": canonical_json(footer)},
    )
    with tempfile.TemporaryDirectory(dir=outputs) as stage:
        table = Path(stage) / "genes.parquet"
        sensitivity_file = Path(stage) / "sensitivity.parquet"
        rank_sensitivity = _rank_evidence(
            primary,
            request,
            cohort,
            sensitivity_file,
            expression,
            solver,
            (effects, adjusted),
        )
        if rank_sensitivity is not None:
            _expression_identity(expression, request["expression_sha256"])
        rows = _rows(
            genes,
            effects,
            probabilities,
            adjusted,
            codes,
            measurement=request["measurement"],
            errors=errors,
            confidence=confidence,
            quantities=quantities,
        )
        with pq.ParquetWriter(table, schema, compression="snappy") as writer:
            writer.add_key_value_metadata(score_metadata)
            while batch := list(islice(rows, 2048)):
                writer.write_batch(pa.RecordBatch.from_pylist(batch, schema=schema))
        summary = {
            **footer,
            **solver,
            "format": SUMMARY_FORMAT,
            "design_rank": design.shape[1],
            "residual_df": design.shape[0] - design.shape[1],
            "condition_number": condition,
            "genes_sha256": hash_artifact(table, max_bytes=MAX_PARQUET_BYTES)["sha256"],
        }
        if rank_sensitivity is not None:
            summary["rank_sensitivity"] = rank_sensitivity
        raw = canonical_json(summary)
        if len(raw) > MAX_SUMMARY_BYTES:
            raise ValueError("Mounted result summary exceeds its byte bound")
        manifest = Path(stage) / "summary.json"
        manifest.write_bytes(raw)
        table.rename(outputs / table.name)
        if sensitivity_file.is_file():
            sensitivity_file.rename(outputs / sensitivity_file.name)
        manifest.rename(outputs / manifest.name)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_sha256")
    arguments = parser.parse_args()
    run_mounted_bulk(Path("/inputs"), Path("/outputs"), arguments.request_sha256)


if __name__ == "__main__":
    main()
