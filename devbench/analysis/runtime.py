"""Runtime-observed provenance; Colab never inherits local permission claims."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib import metadata
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


def observe_runtime(execution_tier, *, detect_gpu=True):
    import psutil

    if execution_tier not in {"colab", "local_supervised"}:
        raise ValueError("Unsupported execution tier")
    packages = {}
    for name in ("anndata", "scanpy", "numpy", "scipy", "pandas", "statsmodels", "pydeseq2",
                 "scikit-learn", "numba", "h5py", "matplotlib",
                 "scvi-tools", "torch", "harmonypy", "igraph", "leidenalg", "umap-learn", "celltypist", "nbclient",
                 "nbconvert", "plotly"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    gpu = {"status": "not_inspected", "devices": []}
    if detect_gpu:
        command = shutil.which("nvidia-smi")
        if command:
            try:
                observed = subprocess.run([command, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10, check=False)
                gpu = {"status": "observed" if observed.returncode == 0 else "unavailable",
                       "devices": [line.strip() for line in observed.stdout.splitlines() if line.strip()],
                       "exit_code": observed.returncode}
            except (OSError, subprocess.TimeoutExpired) as error:
                gpu = {"status": "inspection_failed", "devices": [], "error": type(error).__name__}
        else:
            gpu = {"status": "nvidia_runtime_not_present", "devices": []}
    memory = psutil.virtual_memory()
    return {"execution_tier": execution_tier,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "tier_scope": "runner_declared_not_platform_attestation",
            "local_permission_preflight_inherited": False,
            "runtime": {"python": sys.version, "executable": sys.executable,
                        "platform": platform.platform(), "CPU": platform.processor() or platform.machine(),
                        "CPU_logical_count": psutil.cpu_count(), "RAM_total_bytes": memory.total,
                        "RAM_available_bytes": memory.available, "GPU": gpu, "packages": packages,
                        "all_package_versions":dict(sorted((item.metadata['Name'],item.version)
                            for item in metadata.distributions() if item.metadata.get('Name')))}}


def verify_data_identity(path, expected_sha256):
    path = Path(path)
    if not path.is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("An absolute data path and retained SHA-256 are required")
    with path.open("rb") as handle:
        observed = hashlib.file_digest(handle, "sha256").hexdigest()
    if observed != expected_sha256:
        raise ValueError(f"data_sha256_mismatch: expected {expected_sha256}, observed {observed}")
    return {"data_sha256": observed, "expected_local_sha256": expected_sha256,
            "matched": True, "bytes": path.stat().st_size,
            "scope": "computed_on_the_executing_runtime_from_the_original_data_path"}


def verify_runtime_compatibility(identity, observed, *, recorded_runtime=None):
    """Reject changed numerical environments before executing or reusing state.

    Python patch versions may differ; major/minor and the recorded package map
    must agree. Full distribution inventories also agree when one was retained.
    This is a compatibility check, not platform or execution attestation.
    """
    if not isinstance(identity, dict) or not isinstance(observed, dict):
        raise ValueError("runtime_identity_missing")
    runtime = observed.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("runtime_observation_missing")
    version = runtime.get("python")
    match = re.match(r"^(\d+)\.(\d+)(?:\.|\s|$)", version) if isinstance(version, str) else None
    expected_python = identity.get("python_minor")
    if (not match or not isinstance(expected_python, list) or len(expected_python) != 2
            or any(type(value) is not int or value < 0 for value in expected_python)
            or [int(value) for value in match.groups()] != expected_python):
        raise ValueError("runtime_python_changed_since_checkpoint_creation")
    packages = runtime.get("packages")
    if not isinstance(packages, dict) or packages != identity.get("packages"):
        raise ValueError("runtime_packages_changed_since_checkpoint_creation")
    if recorded_runtime is not None:
        recorded = recorded_runtime.get("runtime") if isinstance(recorded_runtime, dict) else None
        if not isinstance(recorded, dict):
            raise ValueError("runtime_record_missing")
        # A legacy identity without a full inventory retains its original
        # package contract. Do not invent new identity fields or old digests.
        inventory = recorded.get("all_package_versions")
        if inventory is not None and (not isinstance(inventory, dict)
                or inventory != runtime.get("all_package_versions")):
            raise ValueError("runtime_distribution_inventory_changed")
    return {"compatible": True, "python_minor": expected_python,
            "scope": "recorded_python_and_package_versions_not_platform_attestation"}
