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
from pathlib import Path

from devharness_core.methods import METHOD_FORMAT, MethodContract
from devharness_core.provenance import _digest, _stream_digest, canonical_json
from devharness_core.watchdog import worker_limits
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events

_DISTRIBUTIONS = (
    "devharness", "numpy", "pandas", "scipy", "scikit-learn", "statsmodels", "patsy",
    "zarr", "numcodecs", "h5py", "pydeseq2", "anndata", "psutil", "jsonschema", "pydantic",
)


def _registered_tool(tool_name):
    dispatcher = ToolDispatcher()
    if not isinstance(tool_name, str) or tool_name not in dispatcher.tools:
        raise ValueError("Unknown registered dispatcher tool")
    return dispatcher, dispatcher.tools[tool_name]


def dispatcher_source(tool_name) -> Path:
    """Resolve a registered implementation's absolute source path, without reading it."""
    _, function = _registered_tool(tool_name)
    source_name = inspect.getsourcefile(function)
    if source_name is None:
        raise ValueError("Registered dispatcher tool has no verifiable source file")
    source = Path(source_name).resolve()
    if source.parent != Path(__file__).resolve().parent or not source.is_file():
        raise ValueError("Registered dispatcher tool source is outside its trusted package")
    return source


def _runtime_identity():
    """Read bounded trusted source files and package versions; no environment variables."""
    root = Path(__file__).resolve().parent.parent
    sources, total_bytes, directories = {}, 0, 0
    # Include lazy imports and package initializers without a hand-maintained
    # dependency list. Only these two trusted source packages are inspected.
    for package in (root / "devharness_core", root / "devharness_server"):
        for directory, children, filenames in os.walk(package, followlinks=False):
            directories += 1
            children[:] = [name for name in children if name != "__pycache__" and not name.startswith(".")]
            if directories > 64 or any((Path(directory) / name).is_symlink() for name in children):
                raise ValueError("Trusted source inventory exceeds its bounds or contains directory symlinks")
            for name in filenames:
                path = Path(directory) / name
                if path.suffix not in {".py", ".R"}:
                    continue
                if len(sources) >= 256:
                    raise ValueError("Trusted source inventory exceeds its file bound")
                digest, size = _stream_digest(path, max_bytes=min(2_000_000, 8_000_000 - total_bytes))
                sources[path.relative_to(root).as_posix()] = digest
                total_bytes += size
    versions = {}
    for name in _DISTRIBUTIONS:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return _digest({"sources": sources, "packages": versions,
                    "python": list(sys.version_info), "implementation": sys.implementation.name,
                    "platform": sysconfig.get_platform()})


def dispatcher_contract(tool_name) -> MethodContract:
    """Snapshot a live built-in tool's schema, source digest, and runtime identity."""
    dispatcher, _ = _registered_tool(tool_name)
    schema = next(item["inputSchema"] for item in dispatcher.get_schemas() if item["name"] == tool_name)
    inputs = {}
    required = schema.get("required", [])
    for parameter in list(schema.get("properties", {})):
        if parameter.endswith("_path"):
            name = parameter.removesuffix("_path")
            inputs[name] = {"artifact_type": "file" if "counts" in name else "table",
                            "required": parameter in required, "parameter": parameter}
            del schema["properties"][parameter]
    schema["required"] = [name for name in required if not name.endswith("_path")]
    schema["additionalProperties"] = False
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return MethodContract.from_dict({
        "format": METHOD_FORMAT,
        "method_id": "devharness." + tool_name,
        "name": tool_name.replace("_", " "),
        "lifecycle": "registered", "implementation_trust": "reviewed",
        "inputs": inputs, "parameters": schema,
        "outputs": {"result": {"artifact_type": "json", "path": "/outputs/result.json", "required": True}},
        "implementation": {"kind": "dispatcher", "tool_name": tool_name,
                           "source_sha256": _stream_digest(dispatcher_source(tool_name), max_bytes=2_000_000)[0]},
        "environment": {"runtime": "trusted_dispatcher", "identity_sha256": _runtime_identity()},
        "execution": {"cpu": 1, "memory_mb": 4096, "timeout_seconds": 300, "pids": 256,
                      "max_output_bytes": 16_000_000, "network": "deny"},
        "validation": {"comparator": "json", "invariants": [], "counts_layer": "X", "qc_metrics": []},
        "provenance": {"origin": "registered_dispatcher"},
    })


def _absolute_existing(value, *, directory=False):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError("Dispatcher paths must be absolute, canonical, and not symlinks")
    if not path.exists() or (directory and not path.is_dir()):
        raise ValueError("Dispatcher path does not exist or has the wrong type")
    if not directory and not (path.is_file() or path.is_dir()):
        raise ValueError("Dispatcher inputs must be regular files or artifact directories")
    return path


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
    if not isinstance(inputs, dict) or set(inputs) - set(data["inputs"]):
        raise ValueError("Unexpected dispatcher input binding")
    for name, binding in data["inputs"].items():
        if name not in inputs:
            if binding["required"]:
                raise ValueError("Missing required dispatcher input: " + name)
            continue
        if binding["parameter"] in arguments:
            raise ValueError("Parameters cannot override a bound artifact input")
        arguments[binding["parameter"]] = str(_absolute_existing(inputs[name]))
    destination = _absolute_existing(output_dir, directory=True) / "result.json"
    if destination.exists() or destination.is_symlink():
        raise ValueError("Dispatcher outputs must use a fresh output directory")
    dispatcher, _ = _registered_tool(tool_name)
    policy = data["execution"]
    with capture_tool_events() as events:
        try:
            with worker_limits(deadline=time.monotonic() + policy["timeout_seconds"],
                               max_mem_mb=policy["memory_mb"]):
                result = json.loads(dispatcher.execute(tool_name, arguments))
            encoded = canonical_json(result)
            if len(encoded) > policy["max_output_bytes"]:
                raise ValueError("Dispatcher result exceeds the output quota")
            with destination.open("xb") as output:
                output.write(encoded)
        except Exception as error:
            return {"exit_code": 1, "runtime": "trusted_dispatcher", "tool_events": events,
                    "error": f"{type(error).__name__}: {error}"}
    return {"exit_code": 0, "runtime": "trusted_dispatcher", "tool_events": events}
