"""Spawn-based isolation with process-tree RSS monitoring and safe transport."""

import multiprocessing
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
    """

    def __init__(self, target_pid: int, max_mem_mb: float = 8000,
                 max_growth_mb_per_sec: float = 1500, poll_interval: float = 0.1) -> None:
        self.target_pid = target_pid
        self.max_mem_mb = _positive_finite(max_mem_mb, "max_mem_mb")
        self.max_growth_mb_per_sec = _positive_finite(max_growth_mb_per_sec, "max_growth_mb_per_sec")
        self.poll_interval = _positive_finite(poll_interval, "poll_interval")
        self.triggered = False
        self.reason = ""
        self.peak_rss_mb = 0.0
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
        self.peak_rss_mb = max(self.peak_rss_mb, rss_mb)
        if rss_mb > self.max_mem_mb:
            self.reason = "Worker memory limit exceeded ({:.1f} > {:.1f} MB)".format(rss_mb, self.max_mem_mb)
        elif self._previous is not None:
            old_time, old_rss = self._previous
            elapsed = now - old_time
            growth = (rss_mb - old_rss) / elapsed if elapsed > 0 else 0.0
            if growth > self.max_growth_mb_per_sec:
                self.reason = "Worker memory growth limit exceeded ({:.1f} MB/s)".format(growth)
        self._previous = (now, rss_mb)
        if self.reason:
            self.triggered = True
        return self.triggered

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

    def terminate_tree(self) -> None:
        try:
            self.sample_rss_mb()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            pass
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
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        _terminate_processes(processes, self.target_pid)


def _worker_entry(target_fn: Callable[..., Any], args: Tuple[Any, ...], kwargs: Dict[str, Any],
                  results: Any, spool_path: str, release_event: Optional[Any] = None) -> None:
    output = Path(spool_path).resolve()
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
        release_event.wait(timeout=30)


def run_sandboxed(target_fn: Callable[..., Any], args: Tuple[Any, ...] = (),
                  kwargs: Optional[Dict[str, Any]] = None, max_mem_mb: float = 8000,
                  timeout_sec: float = 300, max_growth_mb_per_sec: float = 1500) -> Any:
    """Run an importable callable in a spawned child and return its result.

    A Queue carries a bounded result envelope; large serialized results use a
    parent-owned temporary file, avoiding partial-pipe deadlocks on interruption.
    Functions must be importable module-level callables, as required by spawn;
    notebook users should call the dispatcher, whose workers are importable.
    This is resource isolation, not a security boundary for untrusted Python.
    """
    max_mem_mb = _positive_finite(max_mem_mb, "max_mem_mb")
    timeout_sec = _positive_finite(timeout_sec, "timeout_sec")
    max_growth_mb_per_sec = _positive_finite(max_growth_mb_per_sec, "max_growth_mb_per_sec")
    configure_worker_environment()
    context = multiprocessing.get_context("spawn")
    results = context.Queue(maxsize=1)
    release_event = context.Event()
    spool_directory = tempfile.TemporaryDirectory(prefix="devharness-result-")
    spool_path = Path(spool_directory.name).resolve() / "result.pickle"
    worker = context.Process(target=_worker_entry,
                             args=(target_fn, tuple(args), kwargs or {}, results, str(spool_path), release_event))
    monitor: Optional[MemoryWatchdog] = None
    started = time.monotonic()
    try:
        worker.start()
        monitor = MemoryWatchdog(worker.pid, max_mem_mb, max_growth_mb_per_sec).start()
        message = None
        while message is None:
            if monitor.triggered:
                raise HarnessResourceInterrupt(monitor.reason)
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
        try:
            final_rss = monitor.sample_rss_mb()
        except (psutil.Error, OSError) as exc:
            raise HarnessResourceInterrupt("Worker memory monitoring failed: " + str(exc)) from exc
        if final_rss > max_mem_mb:
            raise HarnessResourceInterrupt("Worker memory limit exceeded during final process-tree inspection")
        release_event.set()
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
        return record[1]
    finally:
        if monitor is not None:
            monitor.stop()
            monitor.terminate_tree()
        release_event.set()
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=1)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
            worker.close()
        results.close()
        results.cancel_join_thread()
        spool_directory.cleanup()
