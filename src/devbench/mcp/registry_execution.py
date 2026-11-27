"""Stage curated kernel inputs and validate outputs from the retained OCI workers."""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import numpy as np

from devbench.core._files import read_bounded_file
from devbench.core._hashing import contract_json_bytes
from devbench.core.rna_pathway_analysis import (
    CameraInput,
    GeneSet,
    PathwayPlan,
    PathwayResult,
    admit_pathway_request,
)
from devbench.core.scrna_composition import (
    MAX_COMPOSITION_BYTES,
    CompositionInput,
    CompositionSpec,
    ScCODARuntime,
    read_composition_receipt,
)
from devbench.engine.mounted_kernel_oci import run_mounted_container
from devbench.engine.workflow_operations import OperationBlocked
from devharness_controlled.native_contracts import SCCODA_VERSIONS
from rna_kernels.canonical import canonical_json
from rna_kernels.pathways import MAX_MATRIX_BYTES, MAX_RESULT_BYTES
from rna_kernels.scrna_composition import CompositionRequest

from ._sources import pinned_file
from .registry import catalog

_PATHWAY_WORKER = "devharness_server.tools_rna_pathways"
_SCCODA_WORKER = "devharness_server.tools_scrna_composition"


def _input_bytes(binding, limit):
    with pinned_file(
        Path(binding["path"]), math.inf, expected=binding["ref"]["sha256"]
    ) as (stream, _, size):
        if size > limit:
            raise OperationBlocked("resource_quota_exceeded")
        return stream.read(limit + 1)


def _ports(bindings, expected):
    if bindings.keys() != expected.keys():
        raise OperationBlocked("awaiting_information")
    for port, rep in expected.items():
        if bindings[port]["ref"]["representation_class"] != rep:
            raise OperationBlocked("scientific_refusal")


def _write(path, data):
    with path.open("xb") as stream:
        stream.write(data)
    return sha256(data).hexdigest()


def _table_rows(binding, limit):
    fmt = binding["ref"]["format"]
    if fmt not in ("csv", "tsv"):
        raise OperationBlocked("scientific_refusal")
    with pinned_file(
        Path(binding["path"]), math.inf, expected=binding["ref"]["sha256"]
    ) as (stream, _, size):
        if size > limit:
            raise OperationBlocked("resource_quota_exceeded")
        reader = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        try:
            yield from csv.reader(reader, delimiter="," if fmt == "csv" else "\t")
        finally:
            reader.detach()


def _camera_expression(binding, inputs):
    iterator = iter(_table_rows(binding, MAX_MATRIX_BYTES))
    header = next(iterator, None)
    if not header or len(header) < 5:
        raise OperationBlocked("scientific_refusal")
    samples = header[1:]
    if len(samples) > 4096 or len(set(samples)) != len(samples):
        raise OperationBlocked("scientific_refusal")
    genes = []
    hasher = sha256()
    with (inputs / "expression.bin").open("xb") as stream:
        for row in iterator:
            if len(row) != len(header):
                raise OperationBlocked("scientific_refusal")
            values = np.asarray(row[1:], dtype="<f8")
            if not np.isfinite(values).all():
                raise OperationBlocked("scientific_refusal")
            raw = values.tobytes()
            stream.write(raw)
            hasher.update(raw)
            genes.append(row[0])
            if len(genes) * len(samples) * 8 > MAX_MATRIX_BYTES:
                raise OperationBlocked("resource_quota_exceeded")
    return tuple(genes), tuple(samples), hasher.hexdigest()


def _camera_design(binding, samples, params, inputs):
    iterator = iter(_table_rows(binding, 8 * 1024**2))
    header = next(iterator, [])
    unit, condition = params["biological_unit_column"], params["condition_column"]
    if (
        len(set(header)) != len(header)
        or unit == condition
        or not {"sample_id", unit, condition} <= set(header)
    ):
        raise OperationBlocked("awaiting_information")
    sample_col, unit_col, condition_col = map(
        header.index, ("sample_id", unit, condition)
    )
    rows = {}
    for row in iterator:
        if len(row) != len(header) or row[sample_col] in rows:
            raise OperationBlocked("scientific_refusal")
        rows[row[sample_col]] = row[unit_col], row[condition_col]
    if set(rows) != set(samples):
        raise OperationBlocked("scientific_refusal")
    units = tuple(rows[s][0] for s in samples)
    groups = [rows[s][1] for s in samples]
    counts = Counter(groups)
    num, den = params["numerator"], params["denominator"]
    if (
        num == den
        or set(counts) != {num, den}
        or min(counts.values()) < 2
        or len(set(units)) != len(units)
        or any(not u.strip() for u in units)
    ):
        raise OperationBlocked("scientific_refusal")
    matrix = np.asarray([(1, group == num) for group in groups], dtype="<f8")
    design_sha = _write(inputs / "design.bin", matrix.tobytes())
    receipt = {
        "metadata_sha256": binding["ref"]["sha256"],
        "samples": samples,
        "biological_units": units,
        "condition": condition,
        "groups": groups,
        "contrast": [num, den],
        "design_sha256": design_sha,
    }
    return units, design_sha, sha256(contract_json_bytes(receipt)).hexdigest()


def _collection_params(ref, params, method):
    return {
        "collection_id": "collection-" + ref["sha256"],
        "collection_version": ref["sha256"],
        "method": method,
        "min_set_size": params.get("min_set_size", 2),
        "max_set_size": params.get("max_set_size", 100_000),
    }


def _camera(bindings, inputs, params):
    _ports(
        bindings,
        {
            "expression": "size_factor_normalized",
            "metadata": "metadata_only",
            "reference": "reference_only",
        },
    )
    genes, samples, expression_sha = _camera_expression(bindings["expression"], inputs)
    units, design_sha, design_binding = _camera_design(
        bindings["metadata"], samples, params, inputs
    )
    camera = CameraInput(
        expression_sha256=expression_sha,
        design_sha256=design_sha,
        design_receipt_sha256=design_binding,
        genes=genes,
        samples=samples,
        biological_units=units,
        contrast=(0.0, 1.0),
    )
    collection = json.loads(_input_bytes(bindings["reference"], 8 * 1024**2))
    if not isinstance(collection, dict) or any(
        not isinstance(v, list) for v in collection.values()
    ):
        raise OperationBlocked("scientific_refusal")
    sets = tuple(GeneSet(set_id=k, genes=tuple(v)) for k, v in collection.items())
    plan = PathwayPlan(
        gene_sets=sets,
        **_collection_params(bindings["reference"]["ref"], params, "camera"),
    )
    return admit_pathway_request(
        plan, camera=camera, verify_design=lambda value: value == camera
    )


def _canonical_universe(ctx, sid, binding, params):
    import pyarrow.parquet as pq

    from devbench.core.rna_pathway_analysis import TestedGeneUniverse
    from devbench.platform.plugin_jobs import digest

    from .receipts import verified_receipt

    rid = binding["producer_step_id"]
    if ctx is None or rid != params["tested_step_id"]:
        raise OperationBlocked("input_scope_violation")
    receipt = ctx.authority.daemon_read(
        lambda db: verified_receipt(ctx, sid, rid, db, local_offline=ctx.offline_only)
    )
    plan = ctx.jobs.plan(sid, receipt["plan_id"])
    if (
        receipt["status"] != "completed"
        or receipt["outputs"].get("statistics") != binding["ref"]
        or plan["plan_kind"] != "tier1_kernel"
        or plan.get("graph_steps")
        or plan.get("sweep_spec")
        or plan["kernel"]["kernel_id"] not in {"bulk_de", "bulk_continuous_contrast"}
    ):
        raise OperationBlocked("input_scope_violation")
    rows, foreground = [], []
    sha = binding["ref"]["sha256"]
    with pinned_file(Path(binding["path"]), math.inf, expected=sha) as (stream, _, _):
        parquet = pq.ParquetFile(stream, arrow_extensions_enabled=False)
        if parquet.metadata.num_rows > 100_000:
            raise OperationBlocked("resource_quota_exceeded")
        for batch in parquet.iter_batches(
            batch_size=512, columns=["estimate_status", "gene_id", "p_value", "q_value"]
        ):
            for row in batch.to_pylist():
                if row["estimate_status"] == "estimated":
                    if not 0 <= row["p_value"] <= row["q_value"] <= 1:
                        raise OperationBlocked("scientific_refusal")
                    rows.append(row["gene_id"])
                    if row["q_value"] <= params["foreground_q_max"]:
                        foreground.append(row["gene_id"])
    return TestedGeneUniverse(
        result_receipt_sha256=digest(receipt),
        gene_statistics_sha256=sha,
        genes=tuple(rows),
        selected=tuple(foreground),
        filter_receipt_sha256=digest(
            {
                "artifact": sha,
                "q_max": params["foreground_q_max"],
                "tested": rows,
                "foreground": foreground,
            }
        ),
    )


def _tested_universe(jobs, sid, binding, params):
    # The DE step, accepted attempt, output version and receipt must all be
    # current in this lineage. Values in an uploaded gene list grant no custody.
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.plugin_mcp import _pathway_de_source, _pathway_universe

    ledger = jobs.authority.ledger
    flow = AdaptiveWorkflow(ledger, sid)
    with flow._read_connection() as db:
        state = flow._state(db)
        source = _pathway_de_source(flow, db, state, params["tested_step_id"])
        if source["artifact"]["sha256"] != binding["ref"]["sha256"]:
            raise OperationBlocked("input_scope_violation")
        executor = TierAExecutor(ledger)
        universe, _ = _pathway_universe(
            executor, source, q_max=params["foreground_q_max"], ranked=False
        )
    return universe


def _network(jobs, plan, bindings, stage, ctx):
    from rna_kernels.network import admit_verified_enrichment

    _ports(bindings, {"statistics": "derived_summary", "reference": "reference_only"})
    params = plan["kernel"]["parameters"]
    result = bindings["statistics"]
    universe = (
        _canonical_universe(ctx, plan["session_id"], result, params)
        if result.get("producer_step_id")
        else _tested_universe(jobs, plan["session_id"], result, params)
    )
    ref = bindings["reference"]
    kind = (
        "motif"
        if plan["kernel"]["kernel_id"] == "network_motif_enrichment"
        else "regulon"
    )
    reference = stage.parent / "reference.tsv"
    _write(reference, _input_bytes(ref, 20 * 1024**2))
    settings = _collection_params(ref["ref"], params, "ora")
    del settings["method"]
    return admit_verified_enrichment(
        universe,
        verify_universe=lambda value: value == universe,
        reference=reference,
        kind=kind,
        **settings,
    )


def _composition(bindings, inputs, image, params):
    _ports(bindings, {"composition": "derived_summary"})
    data = CompositionInput.model_validate_json(
        _input_bytes(bindings["composition"], MAX_COMPOSITION_BYTES)
    )
    spec = CompositionSpec.model_validate_json(contract_json_bytes(params))
    runtime = ScCODARuntime(image_id=image, **SCCODA_VERSIONS)
    raw = canonical_json(data.model_dump(mode="json"))
    config = canonical_json(spec.model_dump(mode="json"))
    request = CompositionRequest(
        input_sha256=_write(inputs / "composition_input.json", raw),
        spec_sha256=_write(inputs / "composition_spec.json", config),
        runtime=runtime,
    )
    return canonical_json(request.model_dump(mode="json")), (data, spec, runtime)


def _dispatch(jobs, rid, plan, stage, request, module, image):
    attempt = uuid4().hex
    request_sha = _write(stage / "inputs/request.json", request)
    binding = {
        "stage": str(stage),
        "request_sha": request_sha,
        "image_id": image,
        "attempt_id": attempt,
    }
    jobs.stage(rid, step="curated_kernel", mounted_container=binding)
    override = plan.get("resource_override") or {}
    result = run_mounted_container(
        stage,
        request_sha=request_sha,
        image_id=image,
        attempt_id=attempt,
        worker_module=module,
        timeout=override.get("timeout_sec", 14_400),
        memory_mb=override.get("memory_mb", 2048),
    )
    if (
        result.get("cleanup_confirmed") is not True
        or result.get("error_type") is not None
    ):
        raise OperationBlocked("runtime_prerequisite_missing")
    return request_sha


def _read_output(stage, request_sha, name, limit):
    root = stage / "outputs"
    if {p.name for p in root.iterdir()} != {"summary.json", name}:
        raise OperationBlocked("plan_identity_mismatch")
    summary = json.loads(read_bounded_file(root / "summary.json", 65536))
    raw = read_bounded_file(root / name, limit)
    actual = sha256(raw).hexdigest()
    if (
        summary["output_sha256"] != {name: actual}
        or summary["request_sha256"] != request_sha
    ):
        raise OperationBlocked("plan_identity_mismatch")
    if summary.get("status") != "completed":
        raise OperationBlocked("scientific_refusal")
    return raw, actual


def _pathway_results(stage, request_sha):
    raw, _ = _read_output(stage, request_sha, "results.json", MAX_RESULT_BYTES)
    result = PathwayResult.model_validate_json(raw)
    if result.status != "completed" or result.request_sha256 != request_sha:
        raise OperationBlocked("plan_identity_mismatch")
    metrics = {
        "tested_genes": result.tested_gene_count,
        "tested_sets": result.tested_set_count,
    }
    return raw, metrics, list(result.assumptions)


def _composition_results(stage, request_sha, context):
    data, spec, runtime = context
    raw, actual = _read_output(
        stage, request_sha, "composition_result.json", MAX_COMPOSITION_BYTES
    )
    result = read_composition_receipt(
        stage / "outputs/composition_result.json",
        actual,
        data=data,
        spec=spec,
        runtime=runtime,
    )
    if result.status != "succeeded":
        raise OperationBlocked("scientific_refusal")
    return (
        raw,
        {"cell_types": len(data.cell_types), "biological_units": len(data.samples)},
        [result.closure_note],
    )


def execute_mounted_kernel(jobs, rid, plan, bindings, output_dir, *, ctx=None):
    name = plan["kernel"]["kernel_id"]
    kernel = catalog().get(name)
    image = plan["runtime_pin"].get("oci_image_digest")
    if kernel is None or image not in kernel.images:
        raise OperationBlocked("runtime_prerequisite_missing")
    stage = jobs.workspace / "runs" / rid / ("kernel-" + uuid4().hex) / "stage"
    inputs = stage / "inputs"
    inputs.mkdir(mode=0o700, parents=True)
    (stage / "outputs").mkdir(mode=0o700)
    params = plan["kernel"]["parameters"]
    if name in {"bulk_de", "bulk_continuous_contrast"}:
        from .registry_bulk import execute_bulk

        return execute_bulk(jobs, rid, plan, bindings, output_dir, stage, image)
    if name == "scrna_sccoda":
        request, context = _composition(bindings, inputs, image, params)
        request_sha = _dispatch(jobs, rid, plan, stage, request, _SCCODA_WORKER, image)
        raw, metrics, limits = _composition_results(stage, request_sha, context)
    else:
        assay = (
            _camera(bindings, inputs, params)
            if name == "rna_pathway_camera"
            else _network(jobs, plan, bindings, stage, ctx)
        )
        request = contract_json_bytes(assay.model_dump(mode="json"))
        request_sha = _dispatch(jobs, rid, plan, stage, request, _PATHWAY_WORKER, image)
        raw, metrics, limits = _pathway_results(stage, request_sha)
    result = output_dir / "result.json"
    _write(result, raw)
    return {
        "metrics": metrics,
        "tables": [],
        "files": {
            "result": {
                "path": result,
                "kind": "table",
                "format": "json",
                "representation_class": "derived_summary",
            }
        },
        "limitations": limits,
    }
