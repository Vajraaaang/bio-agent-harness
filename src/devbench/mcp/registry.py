"""Research kernel catalog. The benchmark dispatcher is not a discovery source."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from functools import cache

from devbench.engine.method_executor import EXPLORATORY_IMAGE_ID
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import JobStore
from devharness_controlled import native_contracts as pins

from .registry_promotions import workspace_kernels
from .schemas import ID, array, enum, integer, obj

COUNTS = "count_1_raw_counts"
SUMMARY = "derived_summary"
REFERENCE = "reference_only"
NORMALIZED = "size_factor_normalized"
_MATRICES = (COUNTS, NORMALIZED, "ambient_adjusted_counts", "aggregated_unit_counts")
_BULK = frozenset({"bulk", "bulk_rnaseq", "bulk_rna_seq", "bulk_rna"})
_CELL = frozenset(
    {
        "scrna",
        "scrna_seq",
        "snrna_seq",
        "single_cell",
        "single_nucleus",
        "single_cell_rna_seq",
        "single_nucleus_rna_seq",
    }
)


@dataclass(frozen=True)
class Kernel:
    axes: tuple[str, ...]
    representations: tuple[str, ...]
    parameters: dict
    images: frozenset[str]
    assays: frozenset[str] = _BULK | _CELL


def _network_parameters():
    return obj(
        {
            "tested_step_id": ID,
            "foreground_q_max": {"type": "number", "minimum": 0, "maximum": 1},
            "min_set_size": integer(2, 100_000),
            "max_set_size": integer(2, 100_000),
        },
        ("tested_step_id", "foreground_q_max"),
    )


def _other_research_kernels():
    # These repository implementations currently run via Workbench. Discovery
    # must not grant a host-Python fallback when the isolated image is absent.
    from devbench.platform.plugin_rna_solvers import (
        CCCChoice,
        DeconvolutionChoice,
        DTUChoice,
        NeighborhoodChoice,
        PerturbationChoice,
        PredictionChoice,
        SpatialChoice,
    )

    descriptors = (
        ("scrna_ccc", "B9", (NORMALIZED, "metadata_only", REFERENCE), _CELL, CCCChoice),
        (
            "scrna_neighborhood_da",
            "B3",
            ("integrated_latent_or_graph", "metadata_only"),
            _CELL,
            NeighborhoodChoice,
        ),
        (
            "bulk_deconvolution",
            "B3",
            (NORMALIZED, REFERENCE, "metadata_only"),
            _BULK,
            DeconvolutionChoice,
        ),
        ("bulk_dtu", "B4", (COUNTS, REFERENCE, "metadata_only"), _BULK, DTUChoice),
        (
            "spatial_pattern",
            "B8",
            (COUNTS, "spatial_geometry_and_residuals"),
            {"spatial_transcriptomics"},
            SpatialChoice,
        ),
        (
            "perturbation_response",
            "B10",
            (COUNTS, "metadata_only"),
            _CELL,
            PerturbationChoice,
        ),
        (
            "phenotype_prediction",
            "B12",
            (COUNTS, "metadata_only"),
            _BULK,
            PredictionChoice,
        ),
    )
    return {
        name: Kernel(
            (axis,),
            reps,
            choice.model_fields["parameters"].annotation.model_json_schema(),
            frozenset(),
            frozenset(assays),
        )
        for name, axis, reps, assays, choice in descriptors
    }


@cache
def catalog():
    from devbench.core.scrna_composition import CompositionSpec

    from .registry_m2 import m2_kernels

    camera = obj(
        {
            "condition_column": ID,
            "biological_unit_column": ID,
            "numerator": ID,
            "denominator": ID,
            "min_set_size": integer(2, 100_000),
            "max_set_size": integer(2, 100_000),
        },
        ("condition_column", "biological_unit_column", "numerator", "denominator"),
    )
    camera["description"] = (
        "Ports: expression (normalized gene-by-sample CSV), metadata "
        "(CSV with sample_id, independent biological-unit and condition columns), "
        "and reference (JSON mapping set IDs to lists of measured genes)."
    )
    composition = CompositionSpec.model_json_schema()
    composition["description"] = (
        "Port: composition (JSON CompositionInput with independently declared "
        "biological-unit profiles and complete cell-type counts)."
    )
    network = _network_parameters()
    network["description"] = (
        "Ports: statistics (the current same-lineage Tier A DE Parquet output) "
        "and reference (a two-column TF/target TSV or a gene/motif/hit TSV). "
        "The complete filtered tested family and foreground are re-derived "
        "from the accepted execution receipt and frozen q threshold."
    )
    enrichment = Kernel(
        ("B5",), (SUMMARY, REFERENCE), network, pins.pathway_kernel_images()
    )
    audit = obj({}, ())
    audit["description"] = (
        "One expression, matrix, or counts input; structural QC only."
    )
    bulk = obj(
        {
            "biological_unit": enum(
                "donor", "organism", "experimental_culture", "donor_derived_cell_line"
            ),
            "biological_unit_column": ID,
            "condition_column": ID,
            "numerator": ID,
            "denominator": ID,
            "technical_factors": array(ID, 16),
            "continuous_covariates": array(ID, 16),
            "unit_design": enum("independent", "paired_fixed"),
        },
        (
            "biological_unit",
            "biological_unit_column",
            "condition_column",
            "numerator",
            "denominator",
        ),
    )
    bulk["description"] = (
        "Ports: expression (a gene-by-sample CSV/TSV or annotated H5AD X) "
        "and metadata (sample_id, biological units, condition, covariates). "
        "The complete ordered feature family is sealed before fitting."
    )
    return {
        **_other_research_kernels(),
        **m2_kernels(
            _CELL,
            frozenset((*pins.M2_QC_IMAGES.values(), *pins.M2_CUDA_IMAGES.values())),
        ),
        "bulk_de": Kernel(
            ("B1",),
            (COUNTS, "metadata_only"),
            bulk,
            pins.routed_de_kernel_images(),
            _BULK,
        ),
        "bulk_continuous_contrast": Kernel(
            ("B1",),
            (NORMALIZED, "metadata_only"),
            bulk,
            pins.routed_de_kernel_images(),
            _BULK,
        ),
        "matrix_audit": Kernel(
            ("A0", "A1", "A2"),
            _MATRICES,
            audit,
            pins.pathway_kernel_images(),
            frozenset(),
        ),
        "rna_pathway_camera": Kernel(
            ("B5",),
            (NORMALIZED, "metadata_only", REFERENCE),
            camera,
            pins.pathway_kernel_images(),
        ),
        "scrna_sccoda": Kernel(
            ("B3",), (SUMMARY,), composition, pins.sccoda_kernel_images(), _CELL
        ),
        "network_regulon_enrichment": enrichment,
        "network_motif_enrichment": enrichment,
    }


def tier2_base_images():
    images = [
        {
            "image_id": EXPLORATORY_IMAGE_ID,
            "language": "python",
            "packages_summary": "Pinned CPython standard-library image.",
            "gpu_supported": False,
        }
    ]
    for image in sorted(set(pins.ROUTED_DE_KERNEL_IMAGES.values())):
        images.append(
            {
                "image_id": image,
                "language": "r",
                "packages_summary": (
                    "R and Bioconductor: edgeR, limma, DESeq2, variancePartition."
                ),
                "gpu_supported": False,
            }
        )
    return images


def registered_image_ids():
    """Map immutable offline catalog IDs to the capabilities built into each image."""
    result = {}
    for kernel_id, kernel in catalog().items():
        for image in kernel.images:
            row = result.setdefault(
                image,
                {
                    "image_id": image,
                    "lane": "tier1_curated",
                    "architecture": pins.kernel_test_platform(image),
                    "capabilities": [],
                },
            )
            row["capabilities"].append(kernel_id)
    for base in tier2_base_images():
        row = result.setdefault(
            base["image_id"],
            {
                "image_id": base["image_id"],
                "lane": "tier2_exploratory",
                "architecture": pins.kernel_test_platform(base["image_id"])
                or "linux/arm64",
                "capabilities": [],
            },
        )
        row["capabilities"].append("tier2_" + base["language"])
    return result


def _live_images(ctx, backend="docker"):
    from .runtime import available_images

    return {row["image_id"] for row in available_images(ctx, backend=backend)}


def _image(ctx, kernel, live):
    compatible = kernel.images & live
    selected = getattr(ctx.plugin, "mounted_image_id", None)
    if selected is not None:
        return selected if selected in compatible else None
    native_arch = "linux/" + {"x86_64": "amd64", "aarch64": "arm64"}.get(
        platform.machine(), platform.machine()
    )
    native = [
        image for image in compatible if pins.kernel_test_platform(image) == native_arch
    ]
    return min(native or compatible, default=None)


def select_image(ctx, kernel_id, *, session_id=None, version=None, require_gpu=False):
    kernel = catalog().get(kernel_id)
    if kernel is None and session_id:
        promoted = workspace_kernels(ctx, session_id)
        images = {
            source["exploratory_code"]["base_image_id"]
            for (name, revision), (_, source) in promoted.items()
            if name == kernel_id and (version is None or revision == version)
        }
        return next(iter(images)) if len(images) == 1 else None
    if not kernel or not kernel.images:
        return None
    live = _live_images(ctx, _image_backend(kernel_id))
    if require_gpu:
        live &= set(pins.M2_CUDA_IMAGES.values())
    return _image(ctx, kernel, live)


def _image_backend(name):
    # Scripts and matrix_audit use the Docker-specific governed script runner.
    from rna_kernels.m2_contract import KERNEL_AXES

    return "mounted" if name in KERNEL_AXES else "docker"


def _profile(mounted):
    return {
        "default_cpus": 2 if mounted else 1,
        "max_cpus": 2 if mounted else 1,
        "default_memory_mb": 2048,
        "max_memory_mb": 4096,
        "default_timeout_sec": 14400 if mounted else 300,
        "max_timeout_sec": 259200,
        "scratch_mb": 1024,
    }


def _repository_row(ctx, name, kernel, live, include_schema):
    from rna_kernels.m2_contract import KERNEL_AXES

    profile = _profile(bool(kernel.images))
    if name == "scrna_ambient_decontamination":
        profile["gpu_supported"] = (
            _image(ctx, kernel, live & set(pins.M2_CUDA_IMAGES.values())) is not None
        )
    extra = {}
    if name in KERNEL_AXES:
        extra["output_representations"] = [SUMMARY]
        if name == "scrna_ambient_decontamination":
            extra["output_representations"].append("ambient_adjusted_counts")
        extra["supports_plot_sidecar"] = True
        extra["optional_representations"] = {
            "donor_forensics": ["raw_reads_or_droplets", "metadata_only", REFERENCE],
            "snrna_droplet_qc": ["metadata_only"],
            "scrna_doublets": [SUMMARY],
            "scrna_ambient_decontamination": ["raw_reads_or_droplets"],
        }[name]
    return {
        **extra,
        "kernel_id": name,
        "version": "1",
        "lane": "tier1_curated",
        "promotion_scope": "repository",
        "capability_axes": list(kernel.axes),
        "required_representations": list(kernel.representations),
        "calibration_status": "uncalibrated_L1",
        "calibrated_regimes": [],
        "runtime_ready": _image(ctx, kernel, live) is not None,
        "resource_profile": profile,
        "parameter_schema": kernel.parameters if include_schema else None,
    }


def _matches(row, args):
    subset = args.get("kernel_ids")
    axis = args.get("axis_b_class")
    return (
        (subset is None or row["kernel_id"] in subset)
        and (not axis or axis in row["capability_axes"])
        and args.get("lane", "all") in ("all", row["lane"])
    )


def _exploratory_row(key, version, image, live, schema=None):
    return {
        "kernel_id": key,
        "version": version,
        "lane": "tier2_exploratory",
        "promotion_scope": None,
        "capability_axes": ["B_OPEN"],
        "required_representations": [],
        "calibration_status": "uncalibrated_L1",
        "calibrated_regimes": [],
        "runtime_ready": image in live,
        "resource_profile": _profile(True),
        "parameter_schema": schema,
    }


def _registered_scripts(ctx, sid, live):
    """Publish immutable source identities only for this admitted investigation."""
    from .server import check_deadline

    def read(db):
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='mcp_frozen_plans'"
        ).fetchone():
            return []
        query = db.execute(
            "SELECT * FROM mcp_frozen_plans WHERE session_id=? AND "
            "json_extract(plan_json,'$.plan_kind')='tier2_exploratory_code' "
            "AND json_extract(plan_json,'$.governance_action') IS NULL "
            "ORDER BY rowid DESC LIMIT 100",
            (sid,),
        )
        result = {}
        for record in query:
            check_deadline()
            plan = JobStore._plan(record)
            sha = plan["script_sha256"]
            result[sha] = _exploratory_row(
                "script-" + sha, sha, plan["exploratory_code"]["base_image_id"], live
            )
        return list(result.values())

    return ctx.authority.daemon_read(read)


def _planner_methods(ctx, sid, live):
    """Read verified MethodPlanner registrations for this investigation."""
    from devbench.engine.methods import MethodService, exploratory_method
    from devbench.platform.method_planner import ScientificMethodPlanner

    root = ctx.workspace / "methods"
    if not (root / "methods.sqlite3").exists():
        return []
    methods = MethodService(root, readonly=True)
    with methods._db() as db:
        if (
            db.execute(
                "SELECT 1 FROM records WHERE kind='method_plan' AND id=?", (sid,)
            ).fetchone()
            is None
        ):
            return []
    state = ScientificMethodPlanner(methods).get(sid)
    if state.get("data_classification") not in {"synthetic", "public"}:
        raise OperationBlocked("private_data_local_studio_only")
    result = []
    for version, binding in list(state.get("registry", {}).items())[:64]:
        if binding.get("lifecycle") != "custom":
            continue
        record = methods.method(version)
        if record["contract"] != binding:
            raise OperationBlocked("plan_identity_mismatch")
        try:
            exploratory_method(binding)
        except PermissionError:
            continue
        pins = (
            binding["implementation"]["source_sha256"],
            binding["environment"]["lock_sha256"],
        )
        ctx.require_public(sid, pins)
        # Discovery exposes only opaque version IDs for workspace-authored methods.
        result.append(
            _exploratory_row("method-" + version, version, EXPLORATORY_IMAGE_ID, live)
        )
    return result


def _promotions(ctx, sid, live):
    return [
        {
            "kernel_id": name,
            "version": version,
            "promotion_scope": "workspace",
            "lane": "tier1_curated",
            "capability_axes": descriptor["capability_axes"],
            "required_representations": list(
                dict.fromkeys(descriptor["input_ports"].values())
            ),
            # The M1 audit verifies review custody. Only an independent numerical
            # scorer for this exact executable/holdout can raise this ceiling.
            "calibration_status": "uncalibrated_L1",
            "calibrated_regimes": [],
            "runtime_ready": source["exploratory_code"]["base_image_id"] in live,
            "resource_profile": _profile(True),
            "parameter_schema": descriptor["parameter_schema"],
        }
        for (name, version), (descriptor, source) in workspace_kernels(ctx, sid).items()
    ]


def list_kernels_and_methods(ctx, args):
    sid = args.get("session_id")
    if sid:
        ctx.session(sid)
        ctx.require_public(sid)
    include_schema = args.get("include_parameter_schema", False)
    live = _live_images(ctx)
    assay = (args.get("assay") or "").casefold().replace("-", "_")
    mounted = _live_images(ctx, "mounted")
    rows = [
        _repository_row(
            ctx,
            name,
            kernel,
            mounted if _image_backend(name) == "mounted" else live,
            include_schema,
        )
        for name, kernel in catalog().items()
        if not assay or not kernel.assays or assay in kernel.assays
    ]
    if sid:
        rows.extend(_registered_scripts(ctx, sid, live))
        rows.extend(_planner_methods(ctx, sid, live))
    rows.extend(_promotions(ctx, sid, live))
    result = [row for row in rows if _matches(row, args)]
    if not include_schema:
        result = [{**row, "parameter_schema": None} for row in result]
    images = [] if args.get("lane") == "tier1_curated" else tier2_base_images()
    return {"kernels": result, "tier2_base_images": images}


def resolve_kernel(ctx, kernel_id, version, *, session_id=None):
    kernel = catalog().get(kernel_id)
    if kernel is not None and version == "1":
        live = _live_images(ctx, _image_backend(kernel_id)) if kernel.images else set()
        return _repository_row(ctx, kernel_id, kernel, live, True)
    if session_id:
        ctx.require_public(session_id)
        for row in _promotions(ctx, session_id, _live_images(ctx)):
            if row["kernel_id"] == kernel_id and row["version"] == version:
                return row
    raise OperationBlocked("runtime_prerequisite_missing")


def execute_kernel(jobs, rid, plan, bindings, output_dir, *, ctx=None):
    """Launch only registered worker adapters with sealed, hash-bound inputs."""
    from .intake import verify_artifact_identity

    if ctx is None:
        raise OperationBlocked("input_scope_violation")
    local_private = False
    if ctx.protected(plan["session_id"]):
        from .approval import _manual_grants

        def locally_approved(db):
            run = JobStore._owned(db, rid)
            frozen = jobs.plan(plan["session_id"], run["plan_id"])
            return bool(_manual_grants(db, frozen, run["plan_sha256"], private=True))

        local_private = ctx.offline_only and ctx.authority.daemon_read(locally_approved)
        if not local_private:
            raise OperationBlocked("private_data_local_studio_only")
    for binding in bindings.values():
        verify_artifact_identity(
            ctx, binding, until=float("inf"), allow_private=local_private
        )
    if plan["kernel"]["kernel_id"] not in catalog():
        from .execution import _script
        from .registry_execution import _ports

        if plan["epistemic_purpose"] == "confirmatory":
            raise OperationBlocked("scientific_refusal")
        key = plan["kernel"]["kernel_id"], plan["kernel"]["version"]
        retained = workspace_kernels(ctx, plan["session_id"]).get(key)
        if retained is None:
            raise OperationBlocked("runtime_prerequisite_missing")
        descriptor, source = retained
        _ports(bindings, descriptor["input_ports"])
        code = dict(source["exploratory_code"], parameters=plan["kernel"]["parameters"])
        if code["base_image_id"] != plan["runtime_pin"]["oci_image_digest"]:
            raise OperationBlocked("plan_identity_mismatch")
        admitted = {
            **plan,
            "exploratory_code": code,
            "script_sha256": source["script_sha256"],
        }
        return _script(ctx, jobs, rid, admitted, bindings, output_dir)
    from .registry_execution import execute_mounted_kernel

    return execute_mounted_kernel(jobs, rid, plan, bindings, output_dir, ctx=ctx)
