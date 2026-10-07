"""Spawn-based isolation with process-tree RSS monitoring and safe transport."""

import multiprocessing
import contextvars
from contextlib import contextmanager
import ctypes
import errno
from collections import deque
from functools import lru_cache
from itertools import chain
import os
import math
import pickle
import queue
import threading
import time
import traceback
import tempfile
import sys
from pathlib import Path
from numbers import Real
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import psutil

_MB = 1024.0 * 1024.0
_INITIAL_CHILD_PID_CAPACITY = 64
_MAX_DESCENDANT_PROCESSES = 4096
THREAD_ENVIRONMENT_KEYS = ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
_WORKER_LIMITS = contextvars.ContextVar("coordinator_worker_limits", default=None)
_WORKER_SCRATCH = contextvars.ContextVar("coordinator_worker_scratch", default=None)
_WORKER_GROWTH_WINDOW = contextvars.ContextVar("coordinator_growth_window", default=None)
_WORKER_CANCELLATION = contextvars.ContextVar("coordinator_cancellation", default=())


class WorkerCancelled(RuntimeError):
    """The owning coordinator requested cancellation, not a resource failure."""


@contextmanager
def cancel_workers_on(event):
    """Add a coordinator stop event without relaxing any parent resource policy."""
    if not isinstance(event, threading.Event):
        raise TypeError("Cancellation requires a coordinator threading.Event")
    record = {"workers": []}
    token = _WORKER_CANCELLATION.set((*_WORKER_CANCELLATION.get(), (event, record)))
    try:
        yield record
    finally:
        _WORKER_CANCELLATION.reset(token)


def _raise_if_cancelled():
    if any(event.is_set() for event, _ in _WORKER_CANCELLATION.get()):
        raise WorkerCancelled("Execution cancelled by its owning coordinator")


def configure_worker_environment() -> dict:
    """Bound fresh numerical workers before spawn imports; retain user overrides.

    This does not reconfigure BLAS libraries already loaded in the parent.
    CLI launchers should also export these defaults before starting Python.
    """
    return {key: os.environ.setdefault(key, "1") for key in THREAD_ENVIRONMENT_KEYS}


def _positive_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(name + " must be a finite positive number")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError(name + " must be a finite positive number") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(name + " must be a finite positive number")
    return number


class HarnessResourceInterrupt(Exception):
    """A worker exceeded its resource budget and was stopped."""


def _validated_growth_window(value):
    if isinstance(value, Real) and not isinstance(value, bool) and value == 0:
        return 0.0
    number = _positive_finite(value, "growth_window_seconds")
    if number > 1:
        raise ValueError("growth_window_seconds must be a finite number from 0 to 1")
    return number


def _effective_growth_window(requested):
    window = _validated_growth_window(requested)
    parent = _WORKER_GROWTH_WINDOW.get()
    return window if parent is None else min(window, parent)


@contextmanager
def worker_limits(*, deadline: float, max_mem_mb: float, max_growth_mb_per_sec: float = 1500,
                  scratch_dir: Optional[Path] = None, growth_window_seconds: float = 0):
    """Apply stricter coordinator limits to workers started in this context.

    The deadline is an absolute monotonic time. Nested contexts cannot relax
    an existing limit. This does not start a process or configure an OS sandbox.
    """
    limits = (_positive_finite(deadline, "deadline"),
              _positive_finite(max_mem_mb, "max_mem_mb"),
              _positive_finite(max_growth_mb_per_sec, "max_growth_mb_per_sec"))
    growth_window = _effective_growth_window(growth_window_seconds)
    parent = _WORKER_LIMITS.get()
    if parent is not None:
        limits = tuple(min(a, b) for a, b in zip(limits, parent))
    scratch = _WORKER_SCRATCH.get()
    if scratch_dir is not None:
        requested = Path(scratch_dir)
        if not requested.is_absolute() or not requested.is_dir() or requested.is_symlink():
            raise ValueError("Worker scratch must be an existing absolute controlled directory")
        requested = requested.resolve()
        if scratch is not None and not requested.is_relative_to(scratch):
            raise ValueError("Nested worker scratch cannot leave its controlled parent")
        scratch = requested
    token = _WORKER_LIMITS.set(limits)
    scratch_token = _WORKER_SCRATCH.set(scratch)
    growth_token = _WORKER_GROWTH_WINDOW.set(growth_window)
    try:
        yield
    finally:
        _WORKER_LIMITS.reset(token)
        _WORKER_SCRATCH.reset(scratch_token)
        _WORKER_GROWTH_WINDOW.reset(growth_token)


def _effective_worker_limits(max_mem_mb, timeout_sec, max_growth_mb_per_sec):
    limits = _WORKER_LIMITS.get()
    if limits is None:
        return max_mem_mb, timeout_sec, max_growth_mb_per_sec
    remaining = limits[0] - time.monotonic()
    if remaining <= 0:
        raise HarnessResourceInterrupt("Coordinator deadline exhausted before worker launch")
    return min(max_mem_mb, limits[1]), min(timeout_sec, remaining), min(max_growth_mb_per_sec, limits[2])


class RemoteWorkerError(RuntimeError):
    """A worker failed; remote exception objects are never blindly reconstructed."""


@lru_cache(maxsize=1)
def _darwin_listchildpids():
    """Bind only Darwin's parent-scoped libproc enumeration entry point."""
    try:
        function = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_listchildpids
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "Darwin proc_listchildpids is unavailable") from exc
    function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_int
    return function


def _darwin_child_pids(parent_pid: int) -> List[int]:
    """Read a complete, bounded direct-child list, or fail closed.

    Apple's libproc.c divides PROC_PPID_ONLY's byte result by sizeof(int),
    so proc_listchildpids returns a PID count while buffersize is in bytes.
    proc_listpids can return zero on error with errno set. A full buffer is
    ambiguous: retry with bounded growth, never accept possible truncation.
    https://github.com/apple-oss-distributions/xnu/blob/main/libsyscall/wrappers/libproc/libproc.c
    """
    if isinstance(parent_pid, bool) or not isinstance(parent_pid, int) or not 0 < parent_pid <= 2 ** 31 - 1:
        raise OSError(errno.EINVAL, "proc_listchildpids requires a positive parent PID")
    function = _darwin_listchildpids()
    capacity = min(_INITIAL_CHILD_PID_CAPACITY, _MAX_DESCENDANT_PROCESSES + 1)
    while True:
        buffer = (ctypes.c_int * capacity)()
        ctypes.set_errno(0)
        count = function(parent_pid, buffer, ctypes.sizeof(buffer))
        error = ctypes.get_errno()
        if error or count < 0:
            raise OSError(error or errno.EIO, "proc_listchildpids failed for monitored parent " + str(parent_pid))
        if count > capacity:
            raise OSError(errno.EOVERFLOW, "proc_listchildpids returned an invalid PID count")
        if count == capacity:
            if capacity >= _MAX_DESCENDANT_PROCESSES + 1:
                raise OSError(errno.EOVERFLOW, "proc_listchildpids may be truncated at the process-tree limit")
            capacity = min(2 * capacity, _MAX_DESCENDANT_PROCESSES + 1)
            continue
        pids = list(buffer[:count])
        if len(set(pids)) != len(pids) or any(pid <= 0 or pid == parent_pid for pid in pids):
            raise OSError(errno.EIO, "proc_listchildpids returned invalid or duplicate child PIDs")
        return pids


def _darwin_descendants(root: psutil.Process) -> Iterator[psutil.Process]:
    """Walk only the monitored ancestry, retaining psutil's PID-reuse checks."""
    pending = deque([root])
    seen = {root.pid}
    while pending:
        parent = pending.popleft()
        try:
            if not parent.is_running():
                continue
            parent_created = parent.create_time()
        except psutil.NoSuchProcess:
            continue
        for pid in _darwin_child_pids(parent.pid):
            if pid in seen:
                raise OSError(errno.EAGAIN, "Process ancestry changed or contains duplicate PIDs")
            if len(seen) > _MAX_DESCENDANT_PROCESSES:
                raise OSError(errno.EOVERFLOW, "Monitored process-tree limit exceeded")
            try:
                child = psutil.Process(pid)
                if child.create_time() < parent_created or child.ppid() != parent.pid or not parent.is_running():
                    raise OSError(errno.EAGAIN, "Process ancestry changed during child enumeration")
            except psutil.NoSuchProcess:
                # A child which exited between listing and inspection has no RSS.
                continue
            seen.add(pid)
            pending.append(child)
            # Stream discovered children so the caller retains known processes
            # for cleanup even when a later descendant cannot be enumerated.
            yield child


def _recursive_children(process: psutil.Process):
    """Keep psutil normally; on Darwin enumeration denial use scoped libproc."""
    try:
        return process.children(recursive=True)
    except (psutil.AccessDenied, OSError) as exc:
        denied = isinstance(exc, psutil.AccessDenied) or getattr(exc, "errno", None) in {errno.EPERM, errno.EACCES}
        if sys.platform != "darwin" or not denied:
            raise
        return _darwin_descendants(process)


def _terminate_processes(processes: List[psutil.Process], root_pid: int) -> None:
    """Terminate descendants first, then escalate to kill after a short grace."""
    live: List[psutil.Process] = []
    for process in reversed(processes):
        try:
            process.terminate()
            live.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    # multiprocessing owns waitpid() for its direct child. Reaping that child
    # through psutil would prevent Process.join() from learning its exit code.
    descendants = [process for process in live if process.pid != root_pid]
    _, remaining = psutil.wait_procs(descendants, timeout=0.3)
    remaining.extend(process for process in live if process.pid == root_pid and process.is_running())
    for process in remaining:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    psutil.wait_procs([process for process in remaining if process.pid != root_pid], timeout=0.3)


class MemoryWatchdog:
    """Monitor the worker and recursive children from an interruptible thread.

    ``observe`` contains the deterministic budget decision. A triggered monitor
    terminates its observed process tree; the supervising caller raises the
    public exception on its own thread.

    Growth uses consecutive samples by default. An explicit window up to one
    second measures sustained growth once that duration is observed; absolute
    RSS is still checked on every sample, including before the window is full.
    """

    def __init__(self, target_pid: int, max_mem_mb: float = 8000,
                 max_growth_mb_per_sec: float = 1500, poll_interval: float = 0.1,
                 *, growth_window_seconds: float = 0, record_samples: bool = False) -> None:
        self.target_pid = target_pid
        self.max_mem_mb = _positive_finite(max_mem_mb, "max_mem_mb")
        self.max_growth_mb_per_sec = _positive_finite(max_growth_mb_per_sec, "max_growth_mb_per_sec")
        self.poll_interval = _positive_finite(poll_interval, "poll_interval")
        self.growth_window_seconds = _validated_growth_window(growth_window_seconds)
        self._growth_history = deque()
        self.triggered = False
        self.reason = ""
        self.peak_rss_mb = 0.0
        self.sample_count = 0
        self.legacy_exceeding_intervals = 0
        self.peak_legacy_growth = None
        self.peak_active_growth = None
        self._first_observed_time = None
        self._recorded_samples = [] if record_samples else None
        self._trace_truncated = False
        self._previous: Optional[Tuple[float, float]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._known: Dict[int, psutil.Process] = {}
        self._lock = threading.Lock()

    def sample_rss_mb(self) -> float:
        """Return RSS for the target PID plus its recursive descendants."""
        try:
            root = psutil.Process(self.target_pid)
            with self._lock:
                self._known[root.pid] = root
            processes = chain((root,), _recursive_children(root))
        except psutil.NoSuchProcess:
            return 0.0
        total = 0
        for process in processes:
            try:
                total += process.memory_info().rss
                with self._lock:
                    self._known[process.pid] = process
            except psutil.NoSuchProcess:
                continue
        return total / _MB

    def observe(self, rss_mb: float, timestamp: Optional[float] = None) -> bool:
        """Record one observation and set an interruption flag if necessary."""
        now = time.monotonic() if timestamp is None else timestamp
        if self._first_observed_time is None:
            self._first_observed_time = now
        self.sample_count += 1
        if self._recorded_samples is not None:
            if len(self._recorded_samples) < 40000:
                self._recorded_samples.append([now-self._first_observed_time, rss_mb])
            else:
                self._trace_truncated = True
        if self._previous is not None and now > self._previous[0]:
            legacy_growth = (rss_mb-self._previous[1])/(now-self._previous[0])
            self.peak_legacy_growth = legacy_growth if self.peak_legacy_growth is None else max(self.peak_legacy_growth, legacy_growth)
            if rss_mb <= self.max_mem_mb and legacy_growth > self.max_growth_mb_per_sec:
                self.legacy_exceeding_intervals += 1
        self.peak_rss_mb = max(self.peak_rss_mb, rss_mb)
        if rss_mb > self.max_mem_mb:
            self.reason = "Worker memory limit exceeded ({:.1f} > {:.1f} MB)".format(rss_mb, self.max_mem_mb)
        else:
            growth = None
            if self.growth_window_seconds:
                self._growth_history.append((now, rss_mb))
                cutoff = now-self.growth_window_seconds
                while len(self._growth_history) > 1 and self._growth_history[1][0] <= cutoff:
                    self._growth_history.popleft()
                old_time, old_rss = self._growth_history[0]
                elapsed = now-old_time
                if elapsed >= self.growth_window_seconds:
                    growth = (rss_mb-old_rss)/elapsed
            elif self._previous is not None:
                old_time, old_rss = self._previous
                elapsed = now-old_time
                growth = (rss_mb-old_rss)/elapsed if elapsed > 0 else 0.0
            if growth is not None:
                self.peak_active_growth = growth if self.peak_active_growth is None else max(self.peak_active_growth, growth)
                if growth > self.max_growth_mb_per_sec:
                    self.reason = "Worker memory growth limit exceeded ({:.1f} MB/s)".format(growth)
        self._previous = (now, rss_mb)
        if self.reason:
            self.triggered = True
        return self.triggered

    def observation_report(self):
        return {"sample_count": self.sample_count,
            "sampled_peak_RSS_MiB": self.peak_rss_mb if self.sample_count else None,
            "RSS_limit_MiB": self.max_mem_mb,
            "growth_limit_MiB_per_second": self.max_growth_mb_per_sec,
            "growth_window_seconds": self.growth_window_seconds,
            "poll_interval_seconds": self.poll_interval,
            "active_growth_guard_fired": self.triggered and self.reason.startswith("Worker memory growth limit exceeded"),
            "RSS_guard_fired": self.triggered and self.reason.startswith("Worker memory limit exceeded"),
            "monitor_triggered": self.triggered, "reason": self.reason or None,
            "peak_active_growth_MiB_per_second": self.peak_active_growth,
            "peak_legacy_interval_growth_MiB_per_second": self.peak_legacy_growth,
            "legacy_exceeding_intervals": self.legacy_exceeding_intervals,
            "legacy_rule_would_reject_observed_trace": bool(self.legacy_exceeding_intervals),
            "samples_elapsed_seconds_RSS_MiB": self._recorded_samples,
            "trace_truncated": self._trace_truncated,
            "scope": "legacy growth rule evaluated on the same observed RSS samples; not an independently executed counterfactual run"}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self.observe(self.sample_rss_mb()):
                    self.terminate_tree()
                    return
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError) as exc:
                self.reason = "Worker memory monitoring failed: " + str(exc)
                self.triggered = True
                self.terminate_tree()
                return
            self._stop.wait(min(self.poll_interval, threading.TIMEOUT_MAX))

    def start(self) -> "MemoryWatchdog":
        if self._thread is not None:
            raise RuntimeError("Watchdog has already started")
        self._thread = threading.Thread(target=self._run, name="devharness-memory-watchdog", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def terminate_tree(self) -> dict:
        inventory_unknown = []
        try:
            self.sample_rss_mb()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
        except (psutil.AccessDenied, OSError):
            inventory_unknown.append(self.target_pid)
        with self._lock:
            processes = list(self._known.values())
        # The root may already have exited after acknowledging its result. Its
        # recorded children can still acquire descendants before teardown.
        known_pids = {process.pid for process in processes}
        for process in list(processes):
            try:
                for child in _recursive_children(process):
                    if child.pid not in known_pids:
                        processes.append(child)
                        known_pids.add(child.pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError):
                inventory_unknown.append(process.pid)
        _terminate_processes(processes, self.target_pid)
        remaining, unknown = [], inventory_unknown
        for process in processes:
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    remaining.append(process.pid)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
            except (psutil.AccessDenied, OSError):
                unknown.append(process.pid)
        return {"coverage":"observed_process_tree", "observed_pids":[p.pid for p in processes],
                "remaining_pids":remaining, "unknown_pids":unknown,
                "verified_stopped":not remaining and not unknown}


def _worker_entry(target_fn: Callable[..., Any], args: Tuple[Any, ...], kwargs: Dict[str, Any],
                  results: Any, spool_path: str, release_event: Optional[Any] = None) -> None:
    output = Path(spool_path).resolve()
    # Keep numerical libraries' temporary products beside the coordinator-owned
    # result spool rather than in shared platform scratch directories.
    for name in ("TMPDIR", "TMP", "TEMP", "JOBLIB_TEMP_FOLDER"):
        os.environ[name] = str(output.parent)
    tempfile.tempdir = None
    try:
        value = target_fn(*args, **kwargs)
        try:
            with output.open("wb") as handle:
                pickle.dump(("ok", value), handle, protocol=pickle.HIGHEST_PROTOCOL)
        except BaseException as exc:
            raise TypeError("Worker result could not be serialized: " + str(exc)) from exc
    except BaseException as exc:
        try:
            detail = str(exc)[:1000]
        except BaseException:
            detail = "Exception message unavailable"
        record = (("resource", detail) if isinstance(exc, HarnessResourceInterrupt)
                  else ("error", type(exc).__name__, detail, traceback.format_exc(limit=12)[-4000:]))
        with output.open("wb") as handle:
            pickle.dump(record, handle, protocol=pickle.HIGHEST_PROTOCOL)
    # A killed sender cannot leave the parent waiting for a huge partial pipe
    # frame. Large results and errors stay in the parent-owned temporary file;
    # Queue carries a bounded envelope, or an inline result under 256 bytes.
    message = ("inline", output.read_bytes()) if output.stat().st_size <= 256 else ("file",)
    results.put(message)
    results.close()
    results.join_thread()
    if release_event is not None:
        # Keep the root discoverable until the parent captures its final child
        # tree. Bound the wait so a disappeared parent cannot retain this worker
        # indefinitely. The normal acknowledgement arrives immediately.
        # A Semaphore release never waits for a receiver acknowledgement.
        # Event.set() can deadlock if cleanup kills its registered waiter.
        release_event.acquire(timeout=30)


def run_sandboxed(target_fn: Callable[..., Any], args: Tuple[Any, ...] = (),
                  kwargs: Optional[Dict[str, Any]] = None, max_mem_mb: float = 8000,
                  timeout_sec: float = 300, max_growth_mb_per_sec: float = 1500,
                  growth_window_seconds: float = 0, diagnostics: Optional[Dict[str, Any]] = None) -> Any:
    """Run an importable callable in a spawned child and return its result.

    A Queue carries a bounded result envelope; large serialized results use a
    parent-owned temporary file, avoiding partial-pipe deadlocks on interruption.
    Functions must be importable module-level callables, as required by spawn;
    notebook users should call the dispatcher, whose workers are importable.
    This is resource isolation, not a security boundary for untrusted Python.
    """
    _raise_if_cancelled()
    max_mem_mb = _positive_finite(max_mem_mb, "max_mem_mb")
    timeout_sec = _positive_finite(timeout_sec, "timeout_sec")
    max_growth_mb_per_sec = _positive_finite(max_growth_mb_per_sec, "max_growth_mb_per_sec")
    max_mem_mb, timeout_sec, max_growth_mb_per_sec = _effective_worker_limits(
        max_mem_mb, timeout_sec, max_growth_mb_per_sec)
    growth_window_seconds = _effective_growth_window(growth_window_seconds)
    if diagnostics is not None and type(diagnostics) is not dict:
        raise ValueError("diagnostics must be a dictionary or None")
    configure_worker_environment()
    context = multiprocessing.get_context("spawn")
    results = context.Queue(maxsize=1)
    release_event = context.Semaphore(0)
    spool_directory = tempfile.TemporaryDirectory(prefix="devharness-result-", dir=_WORKER_SCRATCH.get())
    spool_path = Path(spool_directory.name).resolve() / "result.pickle"
    worker = context.Process(target=_worker_entry,
                             args=(target_fn, tuple(args), kwargs or {}, results, str(spool_path), release_event))
    monitor: Optional[MemoryWatchdog] = None
    started = time.monotonic()
    final_rss = None
    scoped_worker = None
    try:
        worker.start()
        scoped_worker = {"pid": worker.pid, "cleanup": {"verified_stopped":False, "coverage":"not_observed"}}
        for _, scope in _WORKER_CANCELLATION.get():
            scope["workers"].append(scoped_worker)
        monitor_options = ({"growth_window_seconds": growth_window_seconds} if growth_window_seconds else {})
        if diagnostics is not None:
            monitor_options["record_samples"] = True
        monitor = MemoryWatchdog(worker.pid, max_mem_mb, max_growth_mb_per_sec, **monitor_options).start()
        message = None
        while message is None:
            if monitor.triggered:
                raise HarnessResourceInterrupt(monitor.reason)
            _raise_if_cancelled()
            if time.monotonic() - started >= timeout_sec:
                raise HarnessResourceInterrupt("Worker timed out after {:.3g} seconds".format(timeout_sec))
            try:
                message = results.get(timeout=min(0.05, timeout_sec))
            except queue.Empty:
                if not worker.is_alive():
                    if monitor.triggered:
                        raise HarnessResourceInterrupt(monitor.reason)
                    try:
                        message = results.get(timeout=0.1)
                    except queue.Empty:
                        raise RemoteWorkerError("Worker exited without a result (exit code {})".format(worker.exitcode)) from None
        if monitor.triggered:
            raise HarnessResourceInterrupt(monitor.reason)
        _raise_if_cancelled()
        try:
            final_rss = monitor.sample_rss_mb()
        except (psutil.Error, OSError) as exc:
            raise HarnessResourceInterrupt("Worker memory monitoring failed: " + str(exc)) from exc
        if final_rss > max_mem_mb:
            raise HarnessResourceInterrupt("Worker memory limit exceeded during final process-tree inspection")
        release_event.release()
        worker.join(timeout=0.2)
        try:
            if message[0] == "inline":
                record = pickle.loads(message[1])
            else:
                with spool_path.open("rb") as handle:
                    record = pickle.load(handle)
        except BaseException as exc:
            raise RemoteWorkerError("Unable to deserialize worker result: " + str(exc)) from exc
        if record[0] == "resource":
            raise HarnessResourceInterrupt(record[1])
        if record[0] == "error":
            raise RemoteWorkerError("{}: {}\n{}".format(record[1], record[2], record[3]))
        if diagnostics is not None:
            diagnostics["supervisor_status"] = "completed"
        return record[1]
    except BaseException as error:
        if diagnostics is not None:
            diagnostics.update(supervisor_status="cancelled" if isinstance(error, WorkerCancelled) else "failed",
                               exception_type=type(error).__name__)
        raise
    finally:
        if monitor is not None:
            monitor.stop()
            if diagnostics is not None:
                diagnostics.update(monitor=monitor.observation_report(),
                    final_inspection_RSS_MiB=final_rss,
                    supervisor_elapsed_seconds=time.monotonic()-started)
            cleanup = monitor.terminate_tree()
            if scoped_worker is not None:
                scoped_worker["cleanup"] = cleanup
            if diagnostics is not None:
                diagnostics["cleanup"] = cleanup
        release_event.release()
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=1)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
            if scoped_worker is not None:
                cleanup = scoped_worker["cleanup"]
                if worker.is_alive():
                    cleanup["verified_stopped"] = False
                elif cleanup.get("coverage") == "observed_process_tree":
                    # join() is authoritative for our direct child. Preserve
                    # unknown inventories/descendants rather than clearing them.
                    cleanup["remaining_pids"] = [pid for pid in cleanup["remaining_pids"] if pid != worker.pid]
                    cleanup["verified_stopped"] = not cleanup["remaining_pids"] and not cleanup["unknown_pids"]
            worker.close()
        results.close()
        results.cancel_join_thread()
        spool_directory.cleanup()
