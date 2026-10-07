"""Generic supervision of a trusted, isolated coordinator process tree.

This module knows process budgets and raw artifacts, never scientific records.
Callers supply an already imported coordinator function and trusted in-memory
arguments. Participant output must never select that function or restore code.
Function provenance checks prevent accidental misuse; filesystem isolation of
participants remains the caller's separately verified operating-system policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import FunctionType

from .contracts import RunBudget, canonical_json_bytes
from .permissions import require_protected_storage


_COORDINATOR_SOURCE = Path(__file__).resolve().parent
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_LIMITS = {"result": 64_000_000, "error": 10_000, "checkpoint": 32_000_000}
_FAILURE_KINDS = {None, "memory_monitor", "shared_deadline", "coordinator_interrupted", "supervision_failure",
                  "cleanup_failure", "worker_error", "worker_exit_failure", "worker_result_missing",
                  "artifact_capture_failure"}
_THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS")


def _path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Coordinator paths must be absolute without traversal")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Coordinator paths must not contain symlinks")
    return require_protected_storage(path.resolve())


def validate_supervision_configuration(worker, args, output_dir, budget, *, result_name="attempt_result.json",
                                       checkpoint_names=("dispatch_checkpoint.json", "staging_checkpoint.json")):
    """Validate configuration without opening artifacts or starting processes."""
    if (not isinstance(worker, FunctionType) or worker.__qualname__ != worker.__name__
            or worker.__closure__ is not None or not worker.__module__.startswith("devbench.study.")):
        raise TypeError("Worker must be an existing top-level trusted coordinator function")
    module = sys.modules.get(worker.__module__)
    source_name = getattr(module, "__file__", None)
    if module is None or getattr(module, worker.__name__, None) is not worker or source_name is None:
        raise ValueError("Worker must be registered in its already imported coordinator module")
    source = Path(source_name).resolve()
    if (not source.is_relative_to(_COORDINATOR_SOURCE)
            or Path(worker.__code__.co_filename).resolve() != source):
        raise ValueError("Worker must originate in the installed coordinator source")
    if not isinstance(args, tuple) or len(args) > 1000:
        raise TypeError("Worker arguments must be a trusted in-memory tuple")
    if not isinstance(budget, RunBudget):
        raise TypeError("A validated RunBudget is required")
    budget = RunBudget.model_validate_json(budget.model_dump_json())
    if budget.token_budget is not None:
        raise ValueError("The process supervisor cannot enforce a token ceiling")
    if not isinstance(checkpoint_names, tuple) or len(checkpoint_names) > 8:
        raise ValueError("Checkpoint names must be a bounded tuple")
    names = (result_name, "worker_error.json", *checkpoint_names)
    if (any(not isinstance(name, str) or _NAME.fullmatch(name) is None for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("Result/error/checkpoint filenames must be distinct safe basenames")
    return budget, _path(output_dir)


def write_coordinator_json(path, value, *, replace=False, allow_data_execution=False):
    """Bounded, durable atomic JSON writer for an authorized coordinator job."""
    if allow_data_execution is not True:
        raise PermissionError("Coordinator artifact writes are disabled without explicit authorization")
    path = _path(path)
    payload = canonical_json_bytes(value) + b"\n"
    if len(payload) > 64_000_000:
        raise ValueError("Coordinator JSON output exceeds its bounded budget")
    descriptor, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ArtifactReceipt:
    name: str
    kind: str
    path: Path
    sha256: str
    bytes: int

    def __post_init__(self):
        if (not isinstance(self.name, str) or _NAME.fullmatch(self.name) is None
                or self.kind not in _ARTIFACT_LIMITS or not isinstance(self.path, Path)
                or not self.path.is_absolute() or ".." in self.path.parts or self.path.name != self.name
                or not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None
                or type(self.bytes) is not int or not 0 <= self.bytes <= _ARTIFACT_LIMITS[self.kind]):
            raise ValueError("Raw artifact receipts require bounded paths, hashes and byte counts")

    def as_dict(self):
        return {"name": self.name, "kind": self.kind, "path": str(self.path),
                "sha256": self.sha256, "bytes": self.bytes}


@dataclass(frozen=True)
class SupervisionResult:
    exitcode: int | None
    failure_kind: str | None
    error: str | None
    interrupted: bool
    resource_failure: bool
    elapsed_sec: float
    RSS_MB: float | None
    rss_coverage: str
    result_present: bool
    artifacts: tuple[ArtifactReceipt, ...]
    artifact_errors: tuple[str, ...]

    def __post_init__(self):
        if (self.exitcode is not None and type(self.exitcode) is not int
                or self.failure_kind not in _FAILURE_KINDS
                or any(type(value) is not bool for value in (self.interrupted, self.resource_failure, self.result_present))
                or not isinstance(self.artifacts, tuple) or not all(isinstance(item, ArtifactReceipt) for item in self.artifacts)
                or not isinstance(self.artifact_errors, tuple) or not all(isinstance(value, str) for value in self.artifact_errors)
                or self.error is not None and not isinstance(self.error, str)):
            raise ValueError("Supervision results require typed immutable control-plane observations")
        if (type(self.elapsed_sec) not in {int, float} or not math.isfinite(self.elapsed_sec)
                or self.elapsed_sec < 0 or len(self.artifacts) > 10 or len(self.artifact_errors) > 10):
            raise ValueError("Supervision results must have bounded finite observations")
        if self.RSS_MB is not None and (type(self.RSS_MB) not in {int, float}
                                       or not math.isfinite(self.RSS_MB) or self.RSS_MB < 0):
            raise ValueError("RSS observations must be finite and nonnegative")
        if self.rss_coverage != ("partial" if self.RSS_MB is not None else "unavailable"):
            raise ValueError("RSS coverage must agree with observed availability")
        if any(len(value) > 2000 for value in (*self.artifact_errors, *([self.error] if self.error else []))):
            raise ValueError("Supervision errors must remain bounded")
        if (len({item.name for item in self.artifacts}) != len(self.artifacts)
                or not self.result_present and any(item.kind == "result" for item in self.artifacts)):
            raise ValueError("Raw artifact identities must be distinct and match result presence")

    def as_dict(self):
        return {"record_format": "devbench.study.process-supervision/1", "exitcode": self.exitcode,
                "failure_kind": self.failure_kind, "error": self.error, "interrupted": self.interrupted,
                "resource_failure": self.resource_failure, "elapsed_sec": self.elapsed_sec, "RSS_MB": self.RSS_MB,
                "rss_coverage": self.rss_coverage, "result_present": self.result_present,
                "artifacts": [item.as_dict() for item in self.artifacts], "artifact_errors": list(self.artifact_errors),
                "memory_ceiling_scope": "isolated_coordinator_and_all_observed_descendants",
                "measurement": "sampled_process_tree_RSS", "parent_artifact_capture_RSS_MB": None,
                "limitations": ["Sampling may miss short lived allocations and process descendants",
                                "Parent artifact capture is included in elapsed time; its memory is unmeasured"]}


def _coordinator_entry(worker, args, output, budget, completion, release, failed, *, allow_data_execution=False):
    if allow_data_execution is not True:
        raise PermissionError("Coordinator worker execution is disabled without explicit authorization")
    try:
        budget, output = validate_supervision_configuration(worker, args, output, budget)
        os.environ.update({key: str(budget.cpu_threads) for key in _THREAD_KEYS})
        worker(*args, allow_data_execution=True)
    except BaseException as error:
        failed.set()
        error_path = output / "worker_error.json"
        if not error_path.exists():
            write_coordinator_json(error_path, {"error": f"{type(error).__name__}: {error}"[:2000]},
                                   allow_data_execution=True)
    finally:
        completion.set()
        # Keep this root discoverable until its parent samples the final tree.
        release.wait(timeout=10)


def supervise_coordinator(worker, args, output_dir, budget, *, result_name="attempt_result.json",
                          checkpoint_names=("dispatch_checkpoint.json", "staging_checkpoint.json"),
                          allow_data_execution=False):
    """Run one trusted job under a shared owned-tree memory/deadline ceiling.

    The job writes its own result. This function hashes raw files and never
    interprets their scientific meaning, restores executable code, or discards
    a persisted result merely because shutdown or supervision later failed.
    Standalone callers require a module-level __main__ guard for spawn.
    """
    if allow_data_execution is not True:
        raise PermissionError("Coordinator process execution is disabled without explicit authorization")
    budget, output = validate_supervision_configuration(worker, args, output_dir, budget,
        result_name=result_name, checkpoint_names=checkpoint_names)
    if not output.is_dir():
        raise ValueError("A prepared coordinator output directory is required")
    names = (result_name, "worker_error.json", *checkpoint_names)
    if any(os.path.lexists(output / name) for name in names):
        raise FileExistsError("Owned-process evidence filenames must be fresh")
    import multiprocessing
    from devharness_core.watchdog import MemoryWatchdog
    from .evidence import _read

    context = multiprocessing.get_context("spawn")
    completion, release, failed = context.Event(), context.Event(), context.Event()
    process = context.Process(target=_coordinator_entry, args=(worker, args, output, budget, completion, release, failed),
                              kwargs={"allow_data_execution": True})
    monitor = None
    started = time.monotonic()
    interrupted = resource_failure = False
    failure_kind = error = None
    try:
        process.start()
        monitor = MemoryWatchdog(process.pid, max_mem_mb=budget.max_memory_mb).start()
        while process.is_alive():
            if monitor.triggered:
                resource_failure, failure_kind, error = True, "memory_monitor", monitor.reason
                break
            if time.monotonic() - started >= budget.timeout_sec:
                resource_failure, failure_kind, error = True, "shared_deadline", "Shared coordinator deadline exhausted"
                break
            if completion.is_set():
                monitor.observe(monitor.sample_rss_mb())
                release.set()
                process.join(timeout=2)
                break
            process.join(timeout=0.05)
    except KeyboardInterrupt:
        interrupted, failure_kind, error = True, "coordinator_interrupted", "Coordinator interrupted"
    except Exception as caught:
        failure_kind, error = "supervision_failure", f"{type(caught).__name__}: {caught}"[:2000]
    finally:
        if monitor is not None:
            for cleanup in (monitor.stop, monitor.terminate_tree):
                try:
                    cleanup()
                except Exception as caught:
                    if failure_kind is None:
                        failure_kind, error = "cleanup_failure", f"{type(caught).__name__}: {caught}"[:2000]
        if process.pid is not None:
            try:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
            except Exception as caught:
                if failure_kind is None:
                    failure_kind, error = "cleanup_failure", f"{type(caught).__name__}: {caught}"[:2000]
        if monitor is not None and monitor.triggered:
            resource_failure, failure_kind, error = True, "memory_monitor", monitor.reason

    result_present = os.path.lexists(output / result_name)
    if failure_kind is None:
        if failed.is_set():
            failure_kind, error = "worker_error", "Trusted coordinator worker failed; inspect its raw error artifact"
        elif process.exitcode != 0:
            failure_kind, error = "worker_exit_failure", "Owned coordinator exited without orderly finalization"
        elif not result_present:
            failure_kind, error = "worker_result_missing", "Owned coordinator did not persist its expected result"
    receipts, artifact_errors = [], []
    specifications = [(result_name, "result", 64_000_000), ("worker_error.json", "error", 10_000),
                      *((name, "checkpoint", 32_000_000) for name in checkpoint_names)]
    for name, kind, limit in specifications:
        path = output / name
        if not os.path.lexists(path):
            continue
        try:
            payload = _read(path, limit)
            receipts.append(ArtifactReceipt(name, kind, path, hashlib.sha256(payload).hexdigest(), len(payload)))
        except Exception as caught:
            artifact_errors.append(f"{name}: {type(caught).__name__}: {caught}"[:2000])
    if artifact_errors and failure_kind is None:
        failure_kind, error = "artifact_capture_failure", "Raw coordinator artifact capture is incomplete"
    rss = monitor.peak_rss_mb if monitor is not None and monitor.peak_rss_mb > 0 else None
    return SupervisionResult(process.exitcode, failure_kind, error[:2000] if error else None,
        interrupted, resource_failure, time.monotonic() - started, rss,
        "partial" if rss is not None else "unavailable", result_present, tuple(receipts), tuple(artifact_errors))
