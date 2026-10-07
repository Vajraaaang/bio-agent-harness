"""Spawn and resource-interruption tests with bounded real processes."""

import multiprocessing
import ctypes
import errno
import json
import os
import pickle
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest

from devharness_core.watchdog import (
    HarnessResourceInterrupt, MemoryWatchdog, RemoteWorkerError, run_sandboxed,
)


def worker_identity():
    return os.getpid(), multiprocessing.get_start_method()


def worker_thread_settings():
    from devharness_core.watchdog import THREAD_ENVIRONMENT_KEYS
    import numpy as np
    from threadpoolctl import threadpool_info
    np.ones((2, 2)) @ np.ones((2, 2))
    return {key: os.environ.get(key) for key in THREAD_ENVIRONMENT_KEYS}, threadpool_info()


def test_thread_defaults_reach_spawned_blas_before_import(monkeypatch):
    from devharness_core.watchdog import THREAD_ENVIRONMENT_KEYS
    for key in THREAD_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    settings, pools = run_sandboxed(worker_thread_settings, timeout_sec=20)
    assert settings == dict.fromkeys(THREAD_ENVIRONMENT_KEYS, "1")
    assert all(pool["num_threads"] == 1 for pool in pools if pool["user_api"] == "blas")


def test_thread_defaults_preserve_explicit_overrides(monkeypatch):
    from devharness_core.watchdog import configure_worker_environment, THREAD_ENVIRONMENT_KEYS
    for key in THREAD_ENVIRONMENT_KEYS:
        monkeypatch.setenv(key, "2")
    assert configure_worker_environment() == dict.fromkeys(THREAD_ENVIRONMENT_KEYS, "2")


def worker_large_result():
    return b"x" * (3 * 1024 * 1024)


def worker_exception():
    raise ValueError("worker failed deliberately")


def worker_unpickleable():
    return lambda: 1


def worker_sleep():
    time.sleep(20)


def worker_resource_guard():
    raise HarnessResourceInterrupt("array allocation exceeds active-memory budget")


def worker_return_with_descendant():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    return child.pid


def worker_report_with_descendant(pid_path, raises):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    Path(pid_path).write_text(json.dumps({"worker": os.getpid(), "child": child.pid}))
    if raises:
        raise ValueError("reported failure before supervisor interruption")
    return child.pid


def reported_worker_cleanup_scenario(pid_path, interruption, raises):
    """Run in an outer subprocess so a cleanup deadlock cannot hang pytest."""
    import devharness_core.watchdog as module

    context = multiprocessing.get_context("spawn")
    make_queue, make_process = context.Queue, context.Process
    start_monitor = module.MemoryWatchdog.start
    state, diagnostics = {}, {}
    cancellation = threading.Event()

    def start(monitor):
        state["monitor"] = monitor
        return start_monitor(monitor)

    def process(*args, **kwargs):
        state["release"] = kwargs["args"][-1]
        state["worker"] = make_process(*args, **kwargs)
        return state["worker"]

    def result_queue(*args, **kwargs):
        results = make_queue(*args, **kwargs)
        receive = results.get

        def get(*args, **kwargs):
            message = receive(*args, **kwargs)
            signal = state["release"]
            # In the original Event implementation, prove the real worker has
            # registered a Condition waiter before interrupting its parent.
            if hasattr(signal, "_cond"):
                end = time.monotonic() + 5
                while signal._cond._sleeping_count._semlock._get_value() < 1:
                    if time.monotonic() >= end:
                        raise AssertionError("Worker did not register its release waiter")
                    time.sleep(.005)
            assert state["worker"].is_alive()
            if interruption == "timeout":
                raise module.HarnessResourceInterrupt("Worker timed out at reported-result boundary")
            if interruption == "cancelled":
                cancellation.set()
            if interruption == "monitor":
                raise module.HarnessResourceInterrupt("Worker memory monitoring failed at reported-result boundary")
            if interruption == "worker_killed":
                # A dead root cannot be enumerated. Retain its observed tree
                # before simulating abrupt death, as the real sampler does.
                state["monitor"].sample_rss_mb()
                state["worker"].kill()
                state["worker"].join(timeout=2)
                assert not state["worker"].is_alive()
            return message

        results.get = get
        return results

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(context, "Queue", result_queue)
        patch.setattr(context, "Process", process)
        patch.setattr(module.MemoryWatchdog, "start", start)
        with module.cancel_workers_on(cancellation):
            try:
                value = module.run_sandboxed(worker_report_with_descendant,
                    (pid_path, raises), timeout_sec=15, max_mem_mb=512, diagnostics=diagnostics)
                outcome = {"value": value}
            except (module.HarnessResourceInterrupt, module.WorkerCancelled, module.RemoteWorkerError) as exc:
                outcome = {"exception_type": type(exc).__name__, "message": str(exc)}
    print(json.dumps({"outcome": outcome, "diagnostics": diagnostics}, allow_nan=False), flush=True)


@pytest.mark.parametrize("interruption", ["timeout", "cancelled", "monitor", "worker_killed"])
@pytest.mark.parametrize("raises", [False, True], ids=["reported_result", "reported_error"])
def test_reported_worker_cleanup_is_bounded_after_interruption(tmp_path, interruption, raises):
    pid_path = tmp_path / "pids.json"
    code = (
        f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); "
        "from test_watchdog import reported_worker_cleanup_scenario; "
        f"reported_worker_cleanup_scenario({str(pid_path)!r}, {interruption!r}, {raises!r})"
    )
    live_before_fixture_cleanup = []
    try:
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                   text=True, timeout=10, check=True)
    except subprocess.TimeoutExpired:
        pytest.fail("Supervisor cleanup hung after a worker reported and entered its release wait")
    finally:
        # Cleanup is scoped to this fixture's exact worker/descendant PIDs even
        # when the outer deadline catches the original deadlock.
        if pid_path.exists():
            for pid in json.loads(pid_path.read_text()).values():
                try:
                    process = psutil.Process(pid)
                    if process.status() != psutil.STATUS_ZOMBIE:
                        live_before_fixture_cleanup.append(pid)
                        process.kill()
                        process.wait(timeout=2)
                except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                    pass
    report = json.loads(completed.stdout)
    assert live_before_fixture_cleanup == [], "Supervisor left fixture processes running"
    observations = report["diagnostics"]
    assert observations["cleanup"]["verified_stopped"] is True, report
    assert observations["cleanup"]["coverage"] == "observed_process_tree"
    assert observations["supervisor_elapsed_seconds"] < 8
    expected = ("WorkerCancelled" if interruption == "cancelled" else
                "RemoteWorkerError" if interruption == "worker_killed" and raises else
                None if interruption == "worker_killed" else "HarnessResourceInterrupt")
    assert report["outcome"].get("exception_type") == expected, report
    assert observations["supervisor_status"] == ("cancelled" if interruption == "cancelled" else
                                                "failed" if expected else "completed")
    for pid in json.loads(pid_path.read_text()).values():
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def worker_return_with_grandchild():
    leaf_code = "import time; payload = bytearray(16 * 1024 * 1024); print('ready', flush=True); time.sleep(30)"
    child_code = (
        "import subprocess, sys, time; "
        f"leaf = subprocess.Popen([sys.executable, '-c', {leaf_code!r}], stdout=subprocess.PIPE, text=True); "
        "leaf.stdout.readline(); print(leaf.pid, flush=True); time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, text=True)
    try:
        grandchild_pid = int(child.stdout.readline())
    finally:
        child.stdout.close()
    monitor = MemoryWatchdog(os.getpid())
    tree_rss_mb = monitor.sample_rss_mb()
    expected_rss_mb = sum(psutil.Process(pid).memory_info().rss for pid in
                          (os.getpid(), child.pid, grandchild_pid)) / (1024 * 1024)
    return child.pid, grandchild_pid, tree_rss_mb, expected_rss_mb


def worker_child(pid_file):
    child = multiprocessing.get_context("spawn").Process(target=worker_sleep)
    child.start()
    with open(pid_file, "w", encoding="utf-8") as handle:
        handle.write(str(child.pid))
    time.sleep(20)


def test_runs_in_spawned_child():
    pid, start_method = run_sandboxed(worker_identity, timeout_sec=20)
    assert pid != os.getpid()
    assert start_method == "spawn"


def test_large_queue_result_does_not_deadlock():
    assert len(run_sandboxed(worker_large_result, timeout_sec=20)) == 3 * 1024 * 1024


@pytest.mark.parametrize("worker,match", [
    (worker_exception, "ValueError.*worker failed deliberately"),
    (worker_unpickleable, "serializ"),
])
def test_remote_errors_are_safe_and_prompt(worker, match):
    with pytest.raises(RemoteWorkerError, match=match):
        run_sandboxed(worker, timeout_sec=20)


def test_memory_limit_interrupts_child():
    with pytest.raises(HarnessResourceInterrupt, match="memory"):
        run_sandboxed(worker_sleep, max_mem_mb=1, timeout_sec=20)


def test_timeout_kills_descendants(tmp_path):
    pid_path = tmp_path / "child.pid"
    with pytest.raises(HarnessResourceInterrupt, match="timed out"):
        run_sandboxed(worker_child, args=(str(pid_path),), timeout_sec=3)
    assert pid_path.exists()
    child_pid = int(pid_path.read_text())
    for _ in range(50):
        try:
            if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.02)
    else:
        pytest.fail("descendant remains alive after sandbox timeout")


def test_watchdog_growth_and_child_rss_accounting(monkeypatch):
    class FakeProcess:
        pid = 900001

        def memory_info(self):
            return type("Memory", (), {"rss": 40 * 1024 * 1024})()

        def children(self, recursive=False):
            assert recursive
            return [FakeChild()]

    class FakeChild(FakeProcess):
        pid = 900002

    watchdog = MemoryWatchdog(900001, max_mem_mb=1000, max_growth_mb_per_sec=10)
    monkeypatch.setattr(psutil, "Process", lambda pid: FakeProcess())
    assert watchdog.sample_rss_mb() == 80
    assert watchdog.observe(80, timestamp=1.0) is False
    assert watchdog.observe(82, timestamp=1.1) is True
    assert watchdog.triggered
    assert "growth" in watchdog.reason


def test_rejects_invalid_limits():
    with pytest.raises(ValueError):
        run_sandboxed(worker_identity, timeout_sec=0)


def test_monitoring_denial_fails_closed(monkeypatch):
    watchdog = MemoryWatchdog(900001)
    terminated = []

    def denied():
        raise PermissionError("process enumeration denied")

    monkeypatch.setattr(watchdog, "sample_rss_mb", denied)
    monkeypatch.setattr(watchdog, "terminate_tree", lambda: terminated.append(True))
    watchdog._run()
    assert watchdog.triggered
    assert "monitoring failed" in watchdog.reason
    assert terminated == [True]


def test_queue_envelope_stays_bounded_for_large_results(tmp_path):
    from devharness_core.watchdog import _worker_entry

    class Transport:
        def put(self, message):
            self.message = message

        def close(self):
            pass

        def join_thread(self):
            pass

    transport = Transport()
    spool_path = tmp_path / "result.pickle"
    _worker_entry(worker_large_result, (), {}, transport, str(spool_path))
    assert len(pickle.dumps(transport.message)) < 4096
    assert transport.message[0] == "file"
    with spool_path.open("rb") as handle:
        record = pickle.load(handle)
        assert record[0] == "ok"
        assert len(record[1]) == 3 * 1024 * 1024


def test_worker_resource_guard_preserves_interrupt_type():
    with pytest.raises(HarnessResourceInterrupt, match="active-memory budget"):
        run_sandboxed(worker_resource_guard, timeout_sec=20)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -1, True, "100"])
@pytest.mark.parametrize("field", ["max_mem_mb", "max_growth_mb_per_sec", "poll_interval"])
def test_watchdog_rejects_invalid_numeric_limits(field, value):
    with pytest.raises(ValueError, match="finite positive"):
        MemoryWatchdog(900001, **{field: value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0, -1, True, "100"])
@pytest.mark.parametrize("field", ["max_mem_mb", "max_growth_mb_per_sec", "timeout_sec"])
def test_sandbox_rejects_invalid_numeric_limits_before_spawn(field, value, monkeypatch):
    def never_spawn(*args, **kwargs):
        pytest.fail("Invalid resource limit reached process creation")

    monkeypatch.setattr(multiprocessing, "get_context", never_spawn)
    with pytest.raises(ValueError, match="finite positive"):
        run_sandboxed(worker_identity, **{field: value})


def test_success_cleans_immediately_spawned_subprocess():
    child_pid = run_sandboxed(worker_return_with_descendant, timeout_sec=20)
    try:
        child = psutil.Process(child_pid)
    except psutil.NoSuchProcess:
        return
    try:
        assert child.status() == psutil.STATUS_ZOMBIE, "Successful worker left a running descendant"
    finally:
        try:
            child.kill()
            child.wait(timeout=2)
        except (psutil.NoSuchProcess, psutil.TimeoutExpired):
            pass


def test_darwin_child_pid_count_grows_ambiguous_full_buffer(monkeypatch):
    import devharness_core.watchdog as module
    capacities = []

    def list_children(parent_pid, buffer, size):
        assert parent_pid == 900001
        capacity = size // ctypes.sizeof(ctypes.c_int)
        capacities.append(capacity)
        for index in range(min(capacity, 3)):
            buffer[index] = 900002 + index
        return min(capacity, 3)

    monkeypatch.setattr(module, "_darwin_listchildpids", lambda: list_children, raising=False)
    monkeypatch.setattr(module, "_INITIAL_CHILD_PID_CAPACITY", 2, raising=False)
    assert module._darwin_child_pids(900001) == [900002, 900003, 900004]
    assert capacities == [2, 4]


@pytest.mark.parametrize("return_count,error", [(0, errno.EPERM), (-1, errno.EACCES), (-1, 0)])
def test_darwin_child_pid_errors_are_not_empty_trees(monkeypatch, return_count, error):
    import devharness_core.watchdog as module

    def list_children(parent_pid, buffer, size):
        ctypes.set_errno(error)
        return return_count

    monkeypatch.setattr(module, "_darwin_listchildpids", lambda: list_children, raising=False)
    with pytest.raises(OSError, match="proc_listchildpids"):
        module._darwin_child_pids(900001)


def test_darwin_child_pid_saturation_fails_closed(monkeypatch):
    import devharness_core.watchdog as module
    capacities = []

    def full(parent_pid, buffer, size):
        capacity = size // ctypes.sizeof(ctypes.c_int)
        capacities.append(capacity)
        return capacity

    monkeypatch.setattr(module, "_darwin_listchildpids", lambda: full, raising=False)
    monkeypatch.setattr(module, "_INITIAL_CHILD_PID_CAPACITY", 2, raising=False)
    monkeypatch.setattr(module, "_MAX_DESCENDANT_PROCESSES", 3, raising=False)
    with pytest.raises(OSError, match="truncated|limit"):
        module._darwin_child_pids(900001)
    assert capacities == [2, 4]


def test_darwin_fallback_recursively_counts_all_descendant_rss(monkeypatch):
    import devharness_core.watchdog as module
    parents = {900001: None, 900002: 900001, 900003: 900002}

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def children(self, recursive=False):
            raise PermissionError(errno.EPERM, "global enumeration denied")

        def memory_info(self):
            return type("Memory", (), {"rss": 40 * 1024 * 1024})()

        def is_running(self):
            return True

        def create_time(self):
            return float(self.pid)

        def ppid(self):
            return parents[self.pid]

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(module, "_darwin_child_pids", lambda pid: [child for child, parent in parents.items() if parent == pid], raising=False)
    monkeypatch.setattr(module.psutil, "Process", FakeProcess)
    watchdog = MemoryWatchdog(900001)
    assert watchdog.sample_rss_mb() == 120
    assert set(watchdog._known) == set(parents)


def test_descendant_fallback_does_not_hide_unrelated_errors(monkeypatch):
    import devharness_core.watchdog as module

    class FakeProcess:
        def children(self, recursive=False):
            raise OSError(errno.EIO, "unrelated I/O failure")

    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(OSError, match="unrelated I/O failure"):
        module._recursive_children(FakeProcess())


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin parent-scoped libproc API")
def test_darwin_real_two_child_pid_count_and_parent_scope():
    from devharness_core.watchdog import _darwin_child_pids
    children = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"]) for _ in range(2)]
    try:
        assert {child.pid for child in children} <= set(_darwin_child_pids(os.getpid()))
        assert _darwin_child_pids(children[0].pid) == []
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=3)


def test_actual_grandchild_rss_and_cleanup():
    child_pid, grandchild_pid, tree_rss, expected_rss = run_sandboxed(worker_return_with_grandchild, timeout_sec=20)
    assert tree_rss == pytest.approx(expected_rss, abs=4)
    assert tree_rss > 16
    for pid in (child_pid, grandchild_pid):
        try:
            child = psutil.Process(pid)
        except psutil.NoSuchProcess:
            continue
        try:
            assert child.status() == psutil.STATUS_ZOMBIE, "Worker left a running descendant"
        finally:
            try:
                child.kill()
                child.wait(timeout=2)
            except (psutil.NoSuchProcess, psutil.TimeoutExpired):
                pass


def test_darwin_parent_exit_during_enumeration_is_not_thread_failure(monkeypatch):
    import devharness_core.watchdog as module

    class ExitedParent:
        pid = 900001

        def is_running(self):
            return True

        def create_time(self):
            raise psutil.NoSuchProcess(self.pid)

    assert list(module._darwin_descendants(ExitedParent())) == []


def test_darwin_total_tree_bound_fails_closed(monkeypatch):
    import devharness_core.watchdog as module

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def is_running(self):
            return True

        def create_time(self):
            return float(self.pid)

        def ppid(self):
            return self.pid - 1

    monkeypatch.setattr(module.psutil, "Process", FakeProcess)
    monkeypatch.setattr(module, "_MAX_DESCENDANT_PROCESSES", 2)
    monkeypatch.setattr(module, "_darwin_child_pids", lambda pid: [pid + 1])
    descendants = module._darwin_descendants(FakeProcess(900001))
    assert next(descendants).pid == 900002
    assert next(descendants).pid == 900003
    with pytest.raises(OSError, match="process-tree limit"):
        next(descendants)
