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
