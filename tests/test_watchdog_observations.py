"""Guard attribution uses the same observed RSS trace for both policies."""
import os

from devharness_core.watchdog import MemoryWatchdog, run_sandboxed


def return_value(value):
    return value


def test_no_active_trigger_does_not_imply_the_legacy_rule_would_accept():
    monitor = MemoryWatchdog(os.getpid(), max_mem_mb=8192,
                            growth_window_seconds=1, record_samples=True)
    for when, rss in [(0, 100), (.1, 400), (.5, 400), (1, 400)]:
        assert not monitor.observe(rss, timestamp=when)
    report = monitor.observation_report()
    assert not report['active_growth_guard_fired']
    assert report['legacy_rule_would_reject_observed_trace']
    assert report['legacy_exceeding_intervals'] == 1
    assert report['peak_legacy_interval_growth_MiB_per_second'] == 3000
    assert report['sampled_peak_RSS_MiB'] == 400
    assert report['samples_elapsed_seconds_RSS_MiB'] == [[0, 100], [.1, 400], [.5, 400], [1, 400]]


def test_active_growth_rejection_is_recorded_separately_from_RSS_rejection():
    growth = MemoryWatchdog(os.getpid(), max_mem_mb=8192, growth_window_seconds=1)
    growth.observe(100, timestamp=0)
    assert growth.observe(2000, timestamp=1)
    assert growth.observation_report()['active_growth_guard_fired']
    memory = MemoryWatchdog(os.getpid(), max_mem_mb=8192, growth_window_seconds=1)
    memory.observe(100, timestamp=0)
    assert memory.observe(8193, timestamp=.05)
    assert not memory.observation_report()['active_growth_guard_fired']
    assert memory.observation_report()['RSS_guard_fired']


def test_unobserved_rates_remain_unknown():
    report = MemoryWatchdog(os.getpid()).observation_report()
    assert report['sampled_peak_RSS_MiB'] is None
    assert report['peak_legacy_interval_growth_MiB_per_second'] is None
    assert report['peak_active_growth_MiB_per_second'] is None


def test_supervisor_returns_observations_without_changing_the_result():
    observations = {}
    assert run_sandboxed(return_value, (17,), max_mem_mb=512,
                         growth_window_seconds=1, diagnostics=observations) == 17
    assert observations['monitor']['growth_window_seconds'] == 1
    assert observations['monitor']['sample_count'] > 0
    assert observations['monitor']['samples_elapsed_seconds_RSS_MiB']
    assert observations['supervisor_status'] == 'completed'
