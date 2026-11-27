"""Bounded live capability probes. Only locally installed digest pins are listed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module, metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic

import psutil

from devbench.engine.dispatch_deadline import remaining
from devbench.engine.scientific_authority import require_patched_wal
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devharness_controlled.native_runtime import LocalDocker

_PROBES = ThreadPoolExecutor(max_workers=2, thread_name_prefix="runtime-probe")
_PROBE_LOCK = Lock()


def _engine_inventory(ctx):
    with _PROBE_LOCK:
        cached = getattr(ctx, "_engine_probe", None)
        if not cached or (cached[1].done() and monotonic() - cached[0] >= 2):
            cached = ctx._engine_probe = (monotonic(), _PROBES.submit(_probe_engines))
    try:
        return cached[1].result(timeout=remaining(2))
    except TimeoutError:
        raise OperationBlocked("runtime_prerequisite_missing") from None


def _probe_engines():
    try:
        backends = import_module("devbench.engine.container_backend")
    except ModuleNotFoundError as exc:
        if exc.name != "devbench.engine.container_backend":
            raise
        backends = None
    probe = getattr(backends, "probe_container_backends", None)
    rows = _normalize_probe(probe()) if callable(probe) else [_docker_probe()]
    return rows


def _normalize_probe(observed):
    if isinstance(observed, dict):
        observed = [
            dict(value, name=key)
            for key, value in observed.items()
            if isinstance(value, dict)
        ]
    fields = {
        "name",
        "available",
        "oci_isolated",
        "isolation_backend",
        "cgroups_v2",
        "cgroups_v2_delegated",
        "rootless",
        "local_image_ids",
    }
    return [
        {k: v for k, v in row.items() if k in fields}
        for row in observed
        if isinstance(row, dict)
    ]


def _docker_probe():
    try:
        runtime = LocalDocker()
        with TemporaryDirectory() as tmp:
            runtime.isolate_client(Path(tmp) / "client")
            version = runtime._call(
                ["version", "--format", "{{.Server.Version}}"], timeout=1
            )
            ids = runtime._call(
                ["image", "ls", "--no-trunc", "--quiet"], timeout=1
            ).splitlines()
        return {
            "name": "docker",
            "available": bool(version),
            "version": version,
            "isolation_backend": "docker",
            "oci_isolated": bool(version),
            "local_image_ids": ids,
        }
    except (ValueError, OSError):
        return {
            "name": "docker",
            "available": False,
            "oci_isolated": False,
            "isolation_backend": "docker",
            "local_image_ids": [],
        }


def available_images(ctx, *, backend=None):
    from .registry import registered_image_ids

    registered = registered_image_ids()
    found = set()
    for row in _engine_inventory(ctx):
        if row.get("available") and (backend is None or row.get("name") == backend):
            found.update(row.get("local_image_ids", ()))
    return [registered[sha] for sha in sorted(found & registered.keys())]


def _local_command(command):
    if not shutil.which(command[0]):
        return ""
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
            timeout=0.6,
        ).stdout[:8192]
    except (OSError, subprocess.SubprocessError):
        return ""


def _packages():
    packages = {}
    for package in (
        "numpy",
        "pyarrow",
        "anndata",
        "scipy",
        "pandas",
        "pydeseq2",
        "h5py",
    ):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    r = _local_command(
        [
            "Rscript",
            "--vanilla",
            "-e",
            'p<-c("edgeR","DESeq2","limma");'
            "for(x in p)if(requireNamespace(x,quietly=TRUE))"
            ' cat(x,as.character(packageVersion(x)),"\\n")',
        ]
    )
    return {
        "python": packages,
        "r": dict(line.split() for line in r.splitlines() if len(line.split()) == 2),
    }


def _gpu_devices():
    gpu = _local_command(
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
    ).splitlines()
    if gpu or sys.platform != "darwin":
        return gpu
    raw = _local_command(
        ["system_profiler", "-json", "-detailLevel", "mini", "SPDisplaysDataType"]
    )
    if raw:
        try:
            return [
                g["sppci_model"]
                for g in json.loads(raw).get("SPDisplaysDataType", [])
                if isinstance(g.get("sppci_model"), str)
            ]
        except (ValueError, TypeError):
            return []
    return []


def inspect_runtime_capabilities(ctx, args):
    try:
        require_patched_wal()
        wal = True
    except OperationConflict:
        wal = False
    gpu = _gpu_devices()
    cores = os.cpu_count() or 1
    supervisor = ctx.supervisor
    slots = getattr(supervisor, "slots", {"cpu": min(4, max(1, cores // 2)), "gpu": 0})
    result = {
        "daemon_version": metadata.version("devharness"),
        "sqlite_wal_patched": wal,
        "container_engines": [
            {k: v for k, v in row.items() if k != "local_image_ids"}
            for row in _engine_inventory(ctx)
        ],
        "available_images": available_images(ctx)
        if args.get("include_images", True)
        else [],
        "hardware": {
            "cpu_cores": cores,
            "available_ram_mb": psutil.virtual_memory().available // 2**20,
            "gpus": gpu,
            "free_disk_gb": shutil.disk_usage(ctx.workspace).free / 2**30,
        },
        "pools": {
            "cpu_slots": slots["cpu"],
            "gpu_slots": slots["gpu"],
            "max_concurrent_runs": getattr(supervisor, "max_concurrent_runs", 1),
        },
        "studio": {"running": bool(ctx.studio_url), "offline_only": ctx.offline_only},
    }
    if args.get("include_packages", True):
        result["packages"] = _packages()
    return result
