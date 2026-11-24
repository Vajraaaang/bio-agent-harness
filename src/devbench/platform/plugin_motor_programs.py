"""Versioned, private motor-neuron marker comparison over frozen AnnData sources."""

from __future__ import annotations

import hashlib
import os
import platform
import re
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path

from devbench.analysis.workbench_descriptions import LIMITS
from devbench.analysis.workbench_inputs import InputDiscovery
from devbench.analysis.workbench_motor_programs import SOURCE, compare_motor_programs
from devbench.analysis.workbench_request_contract import MOTOR_PROGRAM_TOPIC
from devbench.core._files import hash_bounded_file
from devbench.engine.workflow_operations import OperationBlocked
from devharness_core.watchdog import RemoteWorkerError, WorkerCancelled

from .plugin_jobs import canonical, digest

_OPERATION = "motor_program_comparison"
_FORMAT = "devbench.motor-program-comparison/1"
_RESULT = "motor-program-comparison.json"
_SOURCE_MODULES = (
    __name__,
    "devbench.analysis.checkpoints",
    "devbench.analysis.resources",
    "devbench.analysis.rna_scrna_input_contract",
    "devbench.analysis.singlecell",
    "devbench.analysis.workbench_count_layer_audit",
    "devbench.analysis.workbench_descriptions",
    "devbench.analysis.workbench_inputs",
    "devbench.analysis.workbench_motor_programs",
    "devbench.analysis.workbench_request_contract",
    "devbench.core._files",
    "devbench.core._hashing",
    "devbench.core.h5ad_gene_blocks",
    "devbench.core.input_files",
    "devbench.core.population_data",
    "devbench.platform.plugin_jobs",
    "devharness_core.watchdog",
)
_LIMITATIONS = [
    "Candidate groups reflect broad marker programs detected in the stored counts.",
    "Anatomical origin, named nuclei and independent embryos need separate evidence.",
    "Differences describe captured observations. Developmental effects and statistical "
    "significance were untested.",
]


def motor_program_runtime() -> dict:
    """Pin the implementation, numerical libraries, and worker-side checks."""
    try:
        modules = {}
        for module in _SOURCE_MODULES:
            spec = find_spec(module)
            if spec is None or not spec.origin:
                raise ValueError("Required motor program module is unavailable")
            modules[module] = hash_bounded_file(Path(spec.origin).resolve(), 1024**2)
        packages = {name: version(name) for name in ("h5py", "numpy", "scipy")}
    except (PackageNotFoundError, ImportError, OSError, ValueError) as error:
        raise OperationBlocked("motor_program_runtime_unavailable") from error
    return {
        "format": "devbench.motor-program-runtime/1",
        "compute_target": "local_worker",
        "python": platform.python_version(),
        "packages": packages,
        "implementation_sha256": digest(modules),
        "network_policy": "offline",
    }


def validate_motor_program_profiles(profiles) -> None:
    """Refuse unsupported headers and incomplete source identities at plan time."""
    if not 2 <= len(profiles) <= 8 or any(
        p.get("role") != "expression" for p in profiles
    ):
        raise OperationBlocked("motor_program_requires_two_to_eight_expression_inputs")
    paths = [p.get("source") for p in profiles]
    fingerprints = [p.get("sha256") for p in profiles]
    if any(
        not isinstance(path, str) or not Path(path).is_absolute() for path in paths
    ) or any(
        not isinstance(sha, str) or re.fullmatch(r"[a-f0-9]{64}", sha) is None
        for sha in fingerprints
    ):
        raise OperationBlocked("motor_program_source_identity_unavailable")
    if len(set(paths)) != len(profiles) or len(set(fingerprints)) != len(profiles):
        raise OperationBlocked("motor_program_requires_distinct_input_contents")
    for profile in profiles:
        observed = profile.get("observed", {})
        if observed.get("file_type") != "h5ad":
            raise OperationBlocked("motor_program_requires_h5ad_inputs")
        if "counts" not in observed.get("layers", ()):
            raise OperationBlocked("motor_program_missing_counts_layer")
        if not {"age", "celltypes", "predicted_labels"} <= set(
            observed.get("obs_columns", ())
        ):
            raise OperationBlocked("motor_program_missing_annotation_columns")
        if "gene_ids" not in observed.get("var_columns", ()):
            raise OperationBlocked("motor_program_missing_gene_ids")
        cells, genes = observed.get("cells"), observed.get("genes")
        if (
            type(cells) is not int
            or type(genes) is not int
            or not 1 <= genes <= 100_000
            or not 1 <= cells <= LIMITS["max_rows"]
        ):
            raise OperationBlocked("motor_program_source_axis_out_of_bounds")


def _unavailable_screen(answer) -> str:
    for problem, reason in (
        ("complete marker panel", "motor_program_marker_panel_incomplete"),
        (
            "supporting mouse gene identifiers",
            "motor_program_mouse_reference_unverified",
        ),
        ("recorded embryonic day", "motor_program_recorded_embryonic_day_required"),
        ("header-matched CSR layer", "motor_program_csr_counts_required"),
    ):
        if any(problem in note for note in answer["limitations"]):
            return reason
    return "motor_program_source_screen_incomplete"


def _describe(discovery, profile):
    try:
        answer = discovery.describe(
            profile["source"],
            expected_sha256=profile["sha256"],
            topics=[MOTOR_PROGRAM_TOPIC],
        )
    except WorkerCancelled:
        raise
    except RemoteWorkerError as error:
        if "Input checksum changed" in str(error):
            raise OperationBlocked("motor_program_source_changed") from error
        raise OperationBlocked("motor_program_source_description_failed") from error
    except (OSError, ValueError, RuntimeError) as error:
        raise OperationBlocked("motor_program_source_description_failed") from error
    if any(
        answer["observed"][axis] != profile["observed"][axis]
        for axis in ("cells", "genes")
    ):
        raise OperationBlocked("motor_program_source_axis_changed")
    screens = [table for table in answer["tables"] if table.get("source_ref") == SOURCE]
    if len(screens) != 1 or not answer["inspection"]["analysis_executed"]:
        raise OperationBlocked(_unavailable_screen(answer))
    return answer, screens[0]


def _retain_artifact(workspace, rid, raw):
    run_root = workspace / "runs" / rid
    if run_root.resolve() != run_root:
        raise OperationBlocked("motor_program_result_path_unavailable")
    run_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = run_root / _RESULT
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "wb"
    ) as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    return {
        "path": path.relative_to(workspace).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def run_motor_program_comparison(jobs, rid: str, plan: dict) -> dict:
    """Compare source-bound marker screens after exact graph approval."""
    if (
        plan.get("operation") != _OPERATION
        or plan.get("data_classification") != "private"
    ):
        raise OperationBlocked("motor_program_requires_private_offline_plan")
    runtime = motor_program_runtime()
    if plan.get("runtime") != runtime:
        raise OperationBlocked("motor_program_runtime_changed")
    if plan["design"]["parameters"] != {}:
        raise OperationBlocked("motor_program_uses_fixed_marker_panel")
    if re.fullmatch(r"[a-f0-9]{32}", rid) is None:
        raise OperationBlocked("motor_program_run_identity_invalid")
    profiles = plan["profiles"]
    validate_motor_program_profiles(profiles)
    discovery = InputDiscovery(
        [Path(plan["input_root"])],
        allow_external_files=plan.get("allow_external_input_files") is True,
        timeout_seconds=60,
    )

    answers, sources = [], []
    for profile in profiles:
        jobs.stage(rid, step="screening_motor_programs")
        answer, screen = _describe(discovery, profile)
        answers.append(answer)
        sources.append({"sha256": profile["sha256"], "screen": screen})
    days = {item["screen"]["observed_embryonic_day"] for item in sources}
    if len(days) != len(profiles):
        raise OperationBlocked("motor_program_requires_distinct_recorded_days")
    jobs.stage(rid, step="comparing_motor_programs")
    try:
        steps, tables, message, status = compare_motor_programs(profiles, answers)
    except ValueError as error:
        raise OperationBlocked("motor_program_comparison_evidence_mismatch") from error
    if status != "completed":
        raise OperationBlocked("motor_program_comparison_incomplete")
    if motor_program_runtime() != runtime:
        raise OperationBlocked("motor_program_runtime_changed")

    summary_tables = [
        {
            "caption": table["table_title"],
            "columns": table["columns"],
            "rows": table["rows"],
        }
        for table in tables
    ]
    raw = canonical(
        {
            "format": _FORMAT,
            "operation": _OPERATION,
            "runtime": runtime,
            "sources": sources,
            "steps": steps,
            "summary_tables": summary_tables,
            "message": message,
            "limitations": _LIMITATIONS,
            "clinical_release_allowed": False,
        }
    )
    return {
        "metrics": {
            "sources": len(sources),
            "observations_screened": sum(
                item["screen"]["denominator"] for item in sources
            ),
        },
        "summary_tables": summary_tables,
        "limitations": _LIMITATIONS,
        "result_artifact": _retain_artifact(jobs.workspace, rid, raw),
    }
