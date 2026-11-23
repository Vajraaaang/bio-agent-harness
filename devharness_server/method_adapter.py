"""Adapt existing registered tools without opening a custom host-code pathway.

This is a trusted in-process compatibility adapter, not hostile-code isolation.
``dispatcher_source`` resolves only live registered source modules. Contract
creation hashes those files plus known transitive harness sources and reads
installed distribution metadata; it never reads data, credentials, or secrets.
Execution binds every filesystem argument to an explicitly supplied artifact.
"""

import importlib.metadata
import inspect
import json
import os
import sys
import sysconfig
import time
from collections.abc import Iterator
from pathlib import Path

from devharness_core.methods import METHOD_FORMAT, MethodContract
from devharness_core.provenance import _digest, _stream_digest, canonical_json
from devharness_core.watchdog import worker_limits
from devharness_server._runtime_scope import require_coordinator_runtime
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events

_DISTRIBUTIONS = (
    "devharness",
    "numpy",
    "pandas",
    "scipy",
    "scikit-learn",
    "statsmodels",
    "patsy",
    "zarr",
    "numcodecs",
    "h5py",
    "pydeseq2",
    "anndata",
    "psutil",
    "jsonschema",
    "pydantic",
)
_POPULATION_CAPSULE = "scrna_population_pseudobulk_hdf5"
_POPULATION_PARQUET = "scrna_population_contrast_parquet"
_BINARY_OUTPUTS = {
    _POPULATION_CAPSULE: "pseudobulk.h5",
    _POPULATION_PARQUET: "result.parquet",
}
_POPULATION_CONTRASTS = {"scrna_population_contrast", _POPULATION_PARQUET}


def _registered_tool(tool_name):
    require_coordinator_runtime()
    if not isinstance(tool_name, str):
        raise ValueError("Unknown registered dispatcher tool")
    dispatcher = (
        ToolDispatcher(Path(__file__).resolve().parent, allow_artifact_writes=True)
        if tool_name in _BINARY_OUTPUTS
        else ToolDispatcher()
    )
    if tool_name not in dispatcher.tools:
        raise ValueError("Unknown registered dispatcher tool")
    return dispatcher, dispatcher.tools[tool_name]


def dispatcher_source(tool_name) -> Path:
    (
        "Resolve a registered implementation's absolute source path, "
        "without reading it."
    )
    _, function = _registered_tool(tool_name)
    source_name = inspect.getsourcefile(function)
    if source_name is None:
        raise ValueError("Registered dispatcher tool has no verifiable source file")
    source = Path(source_name).resolve()
    if source.parent != Path(__file__).resolve().parent or not source.is_file():
        raise ValueError(
            "Registered dispatcher tool source is outside its trusted package"
        )
    return source


def _bundled_source_inventory(root: Path) -> tuple[dict[str, str], int]:
    """Bind the bundled implementations, lazy imports and package initializers."""
    require_coordinator_runtime()
    sources: dict[str, str] = {}
    total_bytes, directories = 0, 0
    # Include lazy imports and package initializers without a hand-maintained
    # dependency list. Bulk's additional lazy owners are bound explicitly below.
    for package in (
        root / "devharness_core",
        root / "devharness_server",
        root / "rna_kernels",
    ):
        for directory, children, filenames in os.walk(package, followlinks=False):
            directories += 1
            children[:] = [
                name
                for name in children
                if name != "__pycache__" and not name.startswith(".")
            ]
            if directories > 64 or any(
                (Path(directory) / name).is_symlink() for name in children
            ):
                raise ValueError(
                    "Trusted source inventory exceeds its bounds or contains "
                    "directory symlinks"
                )
            for name in filenames:
                path = Path(directory) / name
                if path.suffix not in {".py", ".R"}:
                    continue
                if len(sources) >= 256:
                    raise ValueError("Trusted source inventory exceeds its file bound")
                digest, size = _stream_digest(
                    path, max_bytes=min(2_000_000, 8_000_000 - total_bytes)
                )
                sources[path.relative_to(root).as_posix()] = digest
                total_bytes += size
    return sources, total_bytes


def _runtime_identity(tool_name=None):
    (
        "Read bounded trusted source files and package versions; "
        "no environment variables."
    )
    require_coordinator_runtime()
    root = Path(__file__).resolve().parent.parent
    from devbench._layout import package_root

    canonical = package_root(root, "devbench")
    sources, total_bytes = _bundled_source_inventory(root)
    for relative in (
        "devbench/__init__.py",
        "devbench/engine/__init__.py",
        "devbench/engine/provenance.py",
        "devbench/engine/watchdog.py",
    ):
        path = canonical / relative.removeprefix("devbench/")
        if len(sources) >= 256 or path.is_symlink() or path.resolve() != path:
            raise ValueError(
                "Trusted provenance source inventory exceeds its bounds or "
                "contains symlinks"
            )
        digest, size = _stream_digest(
            path, max_bytes=min(2_000_000, 8_000_000 - total_bytes)
        )
        sources[relative] = digest
        total_bytes += size
    if tool_name == "bulk_condition_contrast" or tool_name in {
        "scrna_population_audit",
        "scrna_population_design",
        "scrna_population_pseudobulk",
        _POPULATION_CAPSULE,
        *_POPULATION_CONTRASTS,
    }:
        # Closed owner inventory for the bulk wrapper's lazy study imports.
        # Never enumerate recipes, reference answers, scoring or graders.
        owners = (
            "devbench/__init__.py",
            "devbench/study/__init__.py",
            "devbench/study/contracts.py",
            "devbench/study/planning.py",
            "devbench/study/biology_tools_bulk.py",
            "devbench/study/biology_tool_io.py",
            "devbench/study/biology_input_limits.py",
            "devbench/study/biology_answers.py",
            "devbench/study/biology.py",
            "devbench/core/__init__.py",
            "devbench/core/family_fitting.py",
            "devbench/core/design.py",
            "devbench/core/observed_data.py",
            "devbench/core/estimators.py",
            "devbench/core/bulk_fit_evidence.py",
        )
        if tool_name != "bulk_condition_contrast":
            owners += (
                "devbench/analysis/__init__.py",
                "devbench/analysis/scrna_population_contract.py",
                "devbench/core/population_contract.py",
                "devbench/analysis/scrna_population_analysis.py",
                "devbench/platform/__init__.py",
                "devbench/platform/scrna_population_analysis.py",
                "devbench/analysis/io.py",
                "devbench/analysis/workbench_inputs.py",
                "devbench/analysis/bulk_plot_data.py",
                "devbench/core/input_files.py",
                "devbench/core/expression_io.py",
                "devbench/core/population_data.py",
                "devbench/core/population_statistics.py",
                "devbench/core/tenx.py",
                "devbench/platform/population_parquet.py",
            )
        for relative in owners:
            path = canonical / relative.removeprefix("devbench/")
            if path.is_symlink() or path.resolve() != path:
                raise ValueError("Trusted bulk source cannot be a symlink")
            digest, size = _stream_digest(
                path, max_bytes=min(2_000_000, 8_000_000 - total_bytes)
            )
            sources[relative] = digest
            total_bytes += size
    versions = {}
    distributions = (
        (*_DISTRIBUTIONS, "pyarrow")
        if tool_name == _POPULATION_PARQUET
        else _DISTRIBUTIONS
    )
    for name in distributions:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return _digest(
        {
            "sources": sources,
            "packages": versions,
            "python": list(sys.version_info),
            "implementation": sys.implementation.name,
            "platform": sysconfig.get_platform(),
        }
    )


def dispatcher_contract(tool_name) -> MethodContract:
    """Snapshot a live built-in tool's schema, source digest, and runtime identity."""
    dispatcher, _ = _registered_tool(tool_name)
    schema = next(
        item["inputSchema"]
        for item in dispatcher.get_schemas()
        if item["name"] == tool_name
    )
    inputs = {}
    required = schema.get("required", [])
    for parameter in list(schema.get("properties", {})):
        if parameter.endswith("_path"):
            name = parameter.removesuffix("_path")
            if not (tool_name in _BINARY_OUTPUTS and parameter == "output_path"):
                inputs[name] = {
                    "artifact_type": "anndata"
                    if name == "anndata"
                    else "file"
                    if "counts" in name
                    else "table",
                    "required": parameter in required,
                    "parameter": parameter,
                }
            del schema["properties"][parameter]
    schema["required"] = [name for name in required if not name.endswith("_path")]
    schema["additionalProperties"] = False
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return MethodContract.from_dict(
        {
            "format": METHOD_FORMAT,
            "method_id": "devharness." + tool_name,
            **(
                {
                    "action_scope": {
                        "format": "devbench.method-action/1",
                        "intent": "infer",
                        "interpretation": "none",
                        "exclusions": False,
                    }
                }
                if tool_name in _POPULATION_CONTRASTS
                else {}
            ),
            "name": "scrna population contrast"
            if tool_name == _POPULATION_PARQUET
            else tool_name.replace("_", " "),
            "lifecycle": "registered",
            "implementation_trust": "reviewed",
            "inputs": inputs,
            "parameters": schema,
            "outputs": {
                "result": {
                    "artifact_type": "file"
                    if tool_name == _POPULATION_PARQUET
                    else "json",
                    "path": "/outputs/result.parquet"
                    if tool_name == _POPULATION_PARQUET
                    else "/outputs/result.json",
                    "required": True,
                },
                **(
                    {
                        "summary": {
                            "artifact_type": "json",
                            "path": "/outputs/summary.json",
                            "required": True,
                        }
                    }
                    if tool_name in _POPULATION_CONTRASTS
                    else {}
                ),
                **(
                    {
                        "capsule": {
                            "artifact_type": "file",
                            "path": "/outputs/pseudobulk.h5",
                            "required": True,
                        }
                    }
                    if tool_name == _POPULATION_CAPSULE
                    else {}
                ),
            },
            "implementation": {
                "kind": "dispatcher",
                "tool_name": tool_name,
                "source_sha256": _stream_digest(
                    dispatcher_source(tool_name), max_bytes=2_000_000
                )[0],
            },
            "environment": {
                "runtime": "trusted_dispatcher",
                "identity_sha256": _runtime_identity(tool_name),
            },
            "execution": {
                "cpu": 1,
                "memory_mb": 4096,
                "timeout_seconds": 300,
                "pids": 256,
                "max_output_bytes": (
                    96_000_000
                    if tool_name
                    in {
                        "scrna_population_audit",
                        "scrna_population_design",
                        "scrna_population_pseudobulk",
                        _POPULATION_CAPSULE,
                        *_POPULATION_CONTRASTS,
                    }
                    else 16_000_000
                ),
                "network": "deny",
            },
            "validation": {
                "comparator": "json",
                "invariants": [],
                "counts_layer": "X",
                "qc_metrics": [],
            },
            "provenance": {"origin": "registered_dispatcher"},
        }
    )


def _absolute_existing(value, *, directory=False):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError(
            "Dispatcher paths must be absolute, canonical, and not symlinks"
        )
    if not path.exists() or (directory and not path.is_dir()):
        raise ValueError("Dispatcher path does not exist or has the wrong type")
    if not directory and not (path.is_file() or path.is_dir()):
        raise ValueError(
            "Dispatcher inputs must be regular files or artifact directories"
        )
    return path


def _population_result_summary(result, artifact):
    """Project saved evidence for native state; full tables remain in the artifact."""

    def select(value, names):
        return {name: value[name] for name in names if name in value}

    diagnostics = result.get("diagnostics", {})
    normalization = diagnostics.get("normalization", {})
    calibration = diagnostics.get("calibration", {})
    plot = diagnostics.get("plot_evidence", {})
    compact_diagnostics = select(
        diagnostics,
        (
            "analysis_error",
            "design_condition",
            "model_family_attempted",
            "conditional_nb_execution",
        ),
    )
    compact_diagnostics.update(
        normalization=select(
            normalization,
            (
                "method",
                "reference_gene_count",
                "factor_centering",
                "scope",
                "assumption",
                "per_sample_size_factors",
                "per_sample_library_sizes",
                "observation_ids",
                "observation_scope",
                "evidence_source",
                "gene_universe",
                "library_size_gene_count",
            ),
        ),
        calibration=select(
            calibration,
            (
                "method",
                "package",
                "version",
                "panel_selection",
                "panel_gene_count",
                "total_gene_count",
                "n_cpus",
                "low_memory",
                "refit_cooks",
                "design_columns",
                "prior_log_dispersion_variance",
                "trend_type",
                "trend_coefficients",
                "min_disp",
                "max_disp",
                "min_mu",
                "beta_tol",
                "fit_type",
                "block_optimizer",
            ),
        ),
        optimizer_retry_count=len(calibration.get("optimizer_retries", [])),
        gene_fit_failure_count=diagnostics.get(
            "gene_fit_failures_count", len(diagnostics.get("gene_fit_failures", {}))
        ),
        model_warning_gene_count=diagnostics.get(
            "model_warnings_count", len(diagnostics.get("model_warnings", {}))
        ),
        calibration_warning_count=len(diagnostics.get("calibration_warnings", [])),
        calibration_warnings=[
            str(value)[:500]
            for value in diagnostics.get("calibration_warnings", [])[:20]
        ],
        plot_evidence=select(
            plot,
            ("format", "status", "reason", "gene_count", "sample_count", "pairing"),
        ),
    )
    compact_diagnostics["calibration_diagnostics"] = [
        {
            **select(item, ("code", "severity", "message")),
            "affected_gene_count": len(item.get("gene_ids", [])),
        }
        for item in diagnostics.get("calibration_diagnostics", [])[:50]
    ]
    audit = {
        key: value
        for key, value in result.get("audit", {}).items()
        if key not in {"observation_ids", "population_membership"}
    }
    design = {
        key: value for key, value in result.get("design", {}).items() if key != "matrix"
    }
    pseudobulk = result.get("pseudobulk")
    pseudobulk = (
        select(
            pseudobulk,
            (
                "pseudobulk_sha256",
                "orientation",
                "aggregation",
                "missing_combinations",
                "panel_bytes",
                "profile_ids",
                "profile_library_sizes",
                "count_validation",
            ),
        )
        if pseudobulk
        else None
    )
    summary = {
        "format": "devbench.scrna-population-summary/1",
        "result_format": result.get("format"),
        **select(
            result,
            (
                "status",
                "source_sha256",
                "specification_sha256",
                "procedure",
                "summary",
                "testing_family",
                "runtime_events",
                "limitations",
            ),
        ),
        "audit": audit,
        "design": design,
        "pseudobulk": pseudobulk,
        "diagnostics": compact_diagnostics,
        "result_artifact": artifact,
        "projection_scope": (
            "bounded_saved_evidence_full_tables_in_hashed_result_artifact"
        ),
    }
    supervision = result.get("supervision", {})
    if supervision:
        summary["supervision"] = select(
            supervision, ("supervisor_status", "supervisor_elapsed_seconds", "cleanup")
        )
    if len(canonical_json(summary)) > 256_000:
        raise ValueError("Population summary exceeds its 256000-byte evidence bound")
    return summary


def _bind_dispatcher_inputs(ports: dict, inputs: object, arguments: dict) -> None:
    require_coordinator_runtime()
    if not isinstance(inputs, dict) or set(inputs) - set(ports):
        raise ValueError("Unexpected dispatcher input binding")
    for name, binding in ports.items():
        if name not in inputs:
            if binding["required"]:
                raise ValueError("Missing required dispatcher input: " + name)
            continue
        if binding["parameter"] in arguments:
            raise ValueError("Parameters cannot override a bound artifact input")
        arguments[binding["parameter"]] = str(_absolute_existing(inputs[name]))


def _write_dispatcher_artifact(
    tool_name: str, result: dict, destination: Path, maximum: int,
) -> None:
    require_coordinator_runtime()
    if tool_name == _POPULATION_PARQUET:
        return
    if tool_name == _POPULATION_CAPSULE:
        result = {
            "format": "devbench.scrna-population-pseudobulk/2",
            "status": "completed",
            **{
                name: result[name]
                for name in ("source_sha256", "specification_sha256", "supervision")
            },
            "pseudobulk": {
                **{key: value for key, value in result.items() if key != "supervision"},
                "pseudobulk_sha256": result["matrix"]["sha256"],
                "orientation": result["matrix"]["orientation"],
                "panel_bytes": result["matrix"]["decoded_bytes"],
            },
        }
    # Full population contrast families may exceed the generic evidence ceiling.
    encoded = (
        json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if tool_name in _POPULATION_CONTRASTS
        else canonical_json(result)
    )
    if len(encoded) > maximum:
        raise ValueError("Dispatcher result exceeds the output quota")
    with destination.open("xb") as output:
        output.write(encoded)


def _population_events(events: list[dict], tool_name: str) -> Iterator[dict]:
    return (
        event
        for event in events
        if event.get("tool_name") == tool_name and "result" in event
    )


def _write_population_summary(
    tool_name: str, result: dict, destination: Path, summary_destination: Path,
    maximum: int, events: list[dict],
) -> None:
    require_coordinator_runtime()
    digest, size = _stream_digest(destination, max_bytes=maximum)
    artifact = {"sha256": digest, "size_bytes": size}
    if tool_name == _POPULATION_PARQUET and result["result_artifact"] != artifact:
        raise ValueError("Population Parquet changed after supervised validation")
    summary = _population_result_summary(result, artifact)
    summary_bytes = canonical_json(summary)
    if size + len(summary_bytes) > maximum:
        raise ValueError(
            "Population result and summary exceed their aggregate output quota"
        )
    with summary_destination.open("xb") as output:
        output.write(summary_bytes)
    for event in _population_events(events, tool_name):
        event["result"] = summary


def _record_population_failure(
    events: list[dict], tool_name: str, destination: Path, error: Exception,
) -> None:
    require_coordinator_runtime()
    for event in _population_events(events, tool_name):
        event["result"] = {
            "format": "devbench.scrna-population-transport-failure/1",
            "full_result_written": destination.is_file(),
            "failure": str(error)[:500],
        }


def execute_dispatcher(contract, inputs, parameters, output_dir) -> dict:
    """Run an exact live registered contract and save a fresh JSON result.

Limits are propagated to the existing supervised workers. This compatibility
path does not claim container/network/PID isolation for the trusted parent
process; custom code must use the OCI executor instead.
    """
    if not isinstance(contract, MethodContract):
        raise ValueError("Expected an immutable MethodContract")
    data = contract.to_dict()
    if data["implementation"]["kind"] != "dispatcher":
        raise ValueError("Only registered dispatcher contracts can use this adapter")
    tool_name = data["implementation"]["tool_name"]
    live = dispatcher_contract(tool_name)
    if contract.version_id != live.version_id:
        raise ValueError("Registered method identity changed or contract was forged")
    arguments = contract.validate_parameters(parameters)
    _bind_dispatcher_inputs(data["inputs"], inputs, arguments)
    directory = _absolute_existing(output_dir, directory=True)
    destination = directory / Path(data["outputs"]["result"]["path"]).name
    if destination.exists() or destination.is_symlink():
        raise ValueError("Dispatcher outputs must use a fresh output directory")
    if tool_name in _BINARY_OUTPUTS:
        arguments["output_path"] = str(directory / _BINARY_OUTPUTS[tool_name])
        dispatcher = ToolDispatcher(directory.parent, allow_artifact_writes=True)
    else:
        dispatcher, _ = _registered_tool(tool_name)
    policy = data["execution"]
    population_contrast = tool_name in _POPULATION_CONTRASTS
    summary_destination = destination.parent / "summary.json"
    if population_contrast and (
        summary_destination.exists() or summary_destination.is_symlink()
    ):
        raise ValueError("Dispatcher summary outputs must use a fresh output directory")
    with capture_tool_events() as events:
        try:
            with worker_limits(
                deadline=time.monotonic() + policy["timeout_seconds"],
                max_mem_mb=policy["memory_mb"],
            ):
                result = json.loads(dispatcher.execute(tool_name, arguments))
            maximum = policy["max_output_bytes"]
            _write_dispatcher_artifact(tool_name, result, destination, maximum)
            if population_contrast:
                _write_population_summary(
                    tool_name,
                    result,
                    destination,
                    summary_destination,
                    maximum,
                    events,
                )
        except Exception as error:
            if population_contrast:
                _record_population_failure(events, tool_name, destination, error)
            return {
                "exit_code": 1,
                "runtime": "trusted_dispatcher",
                "tool_events": events,
                "error": f"{type(error).__name__}: {error}",
            }
    return {"exit_code": 0, "runtime": "trusted_dispatcher", "tool_events": events}
