"""Bind forensic and QC inputs to the immutable single-cell worker."""

from __future__ import annotations

from pathlib import Path

from devbench.core._files import hash_bounded_file, read_bounded_json
from devbench.engine.workflow_operations import OperationBlocked
from rna_kernels.m2_contract import KERNEL_AXES, OUTPUTS

from ._sources import canonical

RAW = "raw_reads_or_droplets"
COUNTS = "count_1_raw_counts"
WORKER = "rna_kernels.mounted_m2"

# Only named data ports are staged. No tool path, command line or arbitrary
# reference directory is ever read from a kernel parameter.
GENETIC_INPUTS = {
    "bam": (RAW, {"bam"}),
    "bam_index": ("reference_only", {"bai"}),
    "barcodes": ("metadata_only", {"tsv", "txt"}),
    "reference_fasta": ("reference_only", {"fa", "fasta"}),
    "reference_fai": ("reference_only", {"fai"}),
    "donor_vcf": ("reference_only", {"vcf"}),
    "donor_vcf_index": ("reference_only", {"tbi", "csi"}),
    "cell_vcf": (RAW, {"vcf"}),
    "svd_ud": ("reference_only", {"ud"}),
    "svd_mu": ("reference_only", {"mu"}),
    "svd_bed": ("reference_only", {"bed"}),
}


def m2_kernels(cell_assays, images):
    from devbench.core.donor_forensics import DonorForensicsParameters
    from rna_kernels.scrna_upstream_contract import M2_QC_PARAMETERS

    from .registry import Kernel

    models = {"donor_forensics": DonorForensicsParameters, **M2_QC_PARAMETERS}
    return {
        name: Kernel(
            (axis,),
            (COUNTS,),
            models[name].model_json_schema(),
            images,
            cell_assays,
        )
        for name, axis in KERNEL_AXES.items()
    }


def _admitted_ports(name, bindings, params, purpose):
    from devbench.core.donor_forensics import GENETIC_PORTS

    if name == "donor_forensics":
        if purpose == "confirmatory":
            raise OperationBlocked("scientific_refusal")
        method = params.get("genetic_method", "expression_only")
        required = set(GENETIC_PORTS.get(method, ()))
        optional = {p: GENETIC_INPUTS[p] for p in required}
    elif name == "scrna_ambient_decontamination":
        required = set()
        optional = {"raw_droplets": (RAW, {"h5", "h5ad"})}
        if purpose == "confirmatory":
            raise OperationBlocked("scientific_refusal")
        if "raw_droplets" not in bindings and params.get("requested_method") in {
            "cellbender",
            "soupx",
        }:
            raise OperationBlocked("missing_raw_droplet_matrix")
    elif name == "snrna_droplet_qc":
        required = set()
        optional = {"bam_summary": ("metadata_only", {"csv"})}
    else:
        required = set()
        optional = {"qc_metrics": ("derived_summary", {"parquet"})}
    formats = {"h5ad", "zarr"} if name == "donor_forensics" else {"h5ad"}
    allowed = {"expression": (COUNTS, formats), **optional}
    return required, allowed


def validate_bindings(name, bindings, params, purpose):
    required, allowed = _admitted_ports(name, bindings, params, purpose)
    if (
        not {"expression", *required} <= bindings.keys()
        or bindings.keys() - allowed.keys()
    ):
        raise OperationBlocked("awaiting_information")
    for port, record in bindings.items():
        rep, fmts = allowed[port]
        if (
            record["ref"]["representation_class"] != rep
            or record["detail"].get("source_format", record["ref"]["format"])
            not in fmts
        ):
            raise OperationBlocked("scientific_refusal")
    if name != "donor_forensics" and not bindings["expression"]["detail"].get(
        "count_1_verified"
    ):
        raise OperationBlocked("scientific_refusal")
    if "raw_droplets" in bindings:
        raw = bindings["raw_droplets"]["detail"]
        if not raw.get("raw_unfiltered_declared") or raw.get(
            "count_1_forensics", {}
        ).get("suspected_transformed"):
            raise OperationBlocked("scientific_refusal")
    if "qc_metrics" in bindings:
        qc = bindings["qc_metrics"]
        if (
            not qc["producer_step_id"]
            or qc["detail"].get("qc_expression_sha256")
            != bindings["expression"]["ref"]["sha256"]
        ):
            raise OperationBlocked("input_scope_violation")


def execute_m2(ctx, jobs, rid, plan, bindings, directory, stage, image):
    from .registry_execution import _dispatch
    from .script_runtime import _stage_inputs

    kernel = plan["kernel"]
    name, params = kernel["kernel_id"], kernel["parameters"]
    validate_bindings(name, bindings, params, plan["epistemic_purpose"])
    filenames = {}
    for port, rec in bindings.items():
        fmt = rec["detail"].get("source_format", rec["ref"]["format"])
        extension = "." + fmt
        if Path(rec["path"]).suffix == ".gz":
            extension += ".gz"
        filenames[port] = port + extension
    _stage_inputs(ctx, {filenames[k]: r for k, r in bindings.items()}, stage / "inputs")
    request = {
        "operation": name,
        "parameters": params,
        "primary_count_admitted": bool(
            bindings["expression"]["detail"].get("count_1_verified")
            and not bindings["expression"]["detail"].get("donor_conflict_halted")
        ),
        "inputs": {
            port: {
                "filename": filenames[port],
                "sha256": rec["detail"].get("source_sha256") or rec["ref"]["sha256"],
            }
            for port, rec in bindings.items()
        },
    }
    request_sha = _dispatch(jobs, rid, plan, stage, canonical(request), WORKER, image)
    files = _collect_m2(stage / "outputs", directory, name, request_sha)
    return _outcome(directory, name, params, request, files)


def _collect_m2(outputs, directory, name, request_sha):
    summary = read_bounded_json(outputs / "summary.json", 65536)
    hashes = summary["output_sha256"]
    allowed = {p.filename for p in OUTPUTS.values()}
    if (
        summary["request_sha256"] != request_sha
        or summary["operation"] != name
        or not {"result.json"} <= hashes.keys() <= allowed
        or {p.name for p in outputs.iterdir()} != {"summary.json", *hashes}
    ):
        raise OperationBlocked("plan_identity_mismatch")
    files = {}
    for port, spec in OUTPUTS.items():
        if spec.filename not in hashes:
            continue
        path = outputs / spec.filename
        if (
            path.stat().st_nlink != 1
            or hash_bounded_file(path, 64 * 1024**2) != hashes[spec.filename]
        ):
            raise OperationBlocked("plan_identity_mismatch")
        target = path.rename(directory / spec.filename)
        files[port] = {
            "path": target,
            "kind": spec.kind,
            "format": spec.format,
            "representation_class": spec.representation,
        }
    return files


def _outcome(directory, name, params, request, files):
    result = read_bounded_json(directory / "result.json", 16 * 1024**2)
    limits = []
    if name == "donor_forensics":
        from devbench.core._hashing import canonical_sha256
        from devbench.core.donor_forensics import (
            DonorForensicsParameters,
            DonorForensicsReceipt,
        )

        receipt = DonorForensicsReceipt.model_validate(result)
        validated = DonorForensicsParameters.model_validate(params)
        if receipt.input_sha256 != {
            p: v["sha256"] for p, v in request["inputs"].items()
        } or receipt.parameters_sha256 != canonical_sha256(
            validated.model_dump(mode="json")
        ):
            raise OperationBlocked("plan_identity_mismatch")
        if receipt.status == "conflict_halted":
            files["result"]["forensic_conflict_sha256"] = request["inputs"][
                "expression"
            ]["sha256"]
        limits.append(receipt.caveat)
    elif result.get("status") == "runtime_unavailable":
        raise OperationBlocked("runtime_prerequisite_missing")
    elif result.get("status") not in {"completed", "partial"}:
        raise OperationBlocked("scientific_refusal")
    if name == "scrna_ambient_decontamination":
        _verify_ambient(result, files, request)
        limits.append(
            "The adjusted sibling supports exploratory ambient sensitivity only."
        )
    return {
        "metrics": {"output_artifacts": len(files)},
        "files": files,
        "tables": [],
        "limitations": limits,
    }


def _verify_ambient(result, files, request):
    import h5py

    from devbench.core.hdf5_custody import validate_hdf5_tree
    from rna_kernels.scrna_droplets import AmbientRoute, cellbender_supported

    inputs = request["inputs"]
    tier = "R0" if "raw_droplets" in inputs else "R1"
    expected = {
        "r1_sha256": inputs["expression"]["sha256"],
        "primary_de_representation": COUNTS,
        "representation_class": "ambient_adjusted_counts",
        "epistemic_purpose": "E3",
        "input_matrix_tier": tier,
    }
    if tier == "R0":
        expected["r0_sha256"] = inputs["raw_droplets"]["sha256"]
    if (
        any(result.get(k) != v for k, v in expected.items())
        or result.get("count_1_eligible") is not False
    ):
        raise OperationBlocked("plan_identity_mismatch")
    route = AmbientRoute(**{k: result[k] for k in AmbientRoute.__dataclass_fields__})
    methods = {
        ("R0", "r0_cellbender"): {"cellbender"},
        ("R0", "r0_cpu_soupx_decontx"): {"soupx", "decontx"},
        ("R1", "r1_filtered_only_decontx_heuristic"): {"decontx"},
    }
    if route.ambient_method not in methods.get(
        (tier, route.decontamination_regime), set()
    ):
        raise OperationBlocked("scientific_refusal")
    if route.ambient_method == "cellbender":
        if not cellbender_supported(
            route.cellbender_version
        ) or route.hardware_backend not in {"cpu", "cuda"}:
            raise OperationBlocked("scientific_refusal")
        if route.hardware_backend == "cpu" and not request["parameters"].get(
            "allow_cellbender_cpu"
        ):
            raise OperationBlocked("scientific_refusal")
    elif route.hardware_backend != "cpu" or not route.cellbender_deferred_reason:
        raise OperationBlocked("scientific_refusal")
    with h5py.File(files["adjusted"]["path"], "r") as handle:
        validate_hdf5_tree(handle)
        attrs = {k: result[k] or "" for k in AmbientRoute.__dataclass_fields__}
        attrs["r1_sha256"] = expected["r1_sha256"]
        if tier == "R0":
            attrs["r0_sha256"] = expected["r0_sha256"]
        if (
            any(handle.attrs.get(k) != v for k, v in attrs.items())
            or handle["X"].attrs.get("representation") != "ambient_adjusted_counts"
        ):
            raise OperationBlocked("plan_identity_mismatch")
