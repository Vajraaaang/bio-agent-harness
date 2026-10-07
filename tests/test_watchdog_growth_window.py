"""An opt-in averaging window must preserve absolute and coordinator limits."""
import os
import time

import pytest

from devharness_core import watchdog


def return_value(value):
    return value


def test_default_growth_rule_still_rejects_a_short_burst():
    monitor = watchdog.MemoryWatchdog(os.getpid(), max_mem_mb=8192)
    assert not monitor.observe(100, timestamp=0)
    assert monitor.observe(500, timestamp=.1)
    assert 'growth' in monitor.reason


def test_one_second_window_admits_a_bounded_burst_but_not_sustained_growth():
    monitor = watchdog.MemoryWatchdog(os.getpid(), max_mem_mb=8192, growth_window_seconds=1)
    for when, memory in [(0, 100), (.1, 500), (.5, 500), (1, 500)]:
        assert not monitor.observe(memory, timestamp=when)
    assert not monitor.observe(1500, timestamp=1.5)
    assert monitor.observe(2500, timestamp=2)
    assert 'growth' in monitor.reason


def test_absolute_memory_limit_applies_before_a_growth_window_is_available():
    monitor = watchdog.MemoryWatchdog(os.getpid(), max_mem_mb=8192, growth_window_seconds=1)
    assert not monitor.observe(100, timestamp=0)
    assert monitor.observe(8193, timestamp=.05)
    assert 'Worker memory limit exceeded' in monitor.reason


@pytest.mark.parametrize('value', [True, -1, 1.01, float('nan'), float('inf'), None, '1'])
def test_growth_window_rejects_invalid_or_overlong_values(value):
    with pytest.raises(ValueError, match='growth_window_seconds'):
        watchdog.MemoryWatchdog(os.getpid(), growth_window_seconds=value)


def test_child_cannot_lengthen_the_coordinator_growth_window():
    with watchdog.worker_limits(deadline=time.monotonic()+10, max_mem_mb=8192,
                                growth_window_seconds=.25):
        assert watchdog._effective_growth_window(1) == .25
        with watchdog.worker_limits(deadline=time.monotonic()+10, max_mem_mb=8192,
                                    growth_window_seconds=1):
            assert watchdog._effective_growth_window(1) == .25
        assert watchdog._effective_growth_window(0) == 0
    with watchdog.worker_limits(deadline=time.monotonic()+10, max_mem_mb=8192):
        assert watchdog._effective_growth_window(1) == 0


def test_growth_window_history_stays_bounded():
    monitor = watchdog.MemoryWatchdog(os.getpid(), growth_window_seconds=1)
    for index in range(200):
        assert not monitor.observe(100, timestamp=index*.1)
    assert len(monitor._growth_history) <= 12


def test_spawned_worker_uses_the_stricter_coordinator_window(monkeypatch):
    observed = []
    real_monitor = watchdog.MemoryWatchdog

    def capture(*args, **kwargs):
        monitor = real_monitor(*args, **kwargs)
        observed.append(monitor)
        return monitor

    monkeypatch.setattr(watchdog, 'MemoryWatchdog', capture)
    with watchdog.worker_limits(deadline=time.monotonic()+10, max_mem_mb=512,
                                growth_window_seconds=.25):
        assert watchdog.run_sandboxed(return_value, (17,), growth_window_seconds=1) == 17
    assert observed[0].growth_window_seconds == .25
    assert observed[0].max_mem_mb == 512
