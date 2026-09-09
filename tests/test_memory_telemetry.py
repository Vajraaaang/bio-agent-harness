"""Bounded process-tree observations with explicit missing-data behavior."""
from types import SimpleNamespace

import psutil

from devbench_engine.runner import _MemorySampler, _environment


def test_trace_is_bounded_but_peak_includes_discarded_samples(monkeypatch):
    rss = [100]
    class Process:
        def children(self, recursive):
            assert recursive
            return [SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=50))]

        def memory_info(self):
            return SimpleNamespace(rss=rss[0])

    monkeypatch.setattr(psutil, "Process", lambda _: Process())
    sampler = _MemorySampler()
    sampler.MAX_SAMPLES = 8
    for index in range(100):
        rss[0] = 10000 if index == 7 else 100
        sampler._sample()
    assert 0 < len(sampler.samples) <= 8
    assert sampler.sample_count == 100
    assert sampler.stride > 1
    assert sampler.peak_mb == 10050 / 1_000_000
    assert sampler.samples[0]["rss_bytes"] == 150
    assert [item["elapsed_sec"] for item in sampler.samples] == sorted(item["elapsed_sec"] for item in sampler.samples)


def test_denied_process_does_not_fabricate_low_memory(monkeypatch):
    def denied():
        raise psutil.AccessDenied(123)
    process = SimpleNamespace(children=lambda recursive: [], memory_info=denied)
    monkeypatch.setattr(psutil, "Process", lambda _: process)
    sampler = _MemorySampler()
    sampler._sample()
    assert sampler.samples == []
    assert sampler.sample_count == 0
    assert sampler.error


def test_environment_records_effective_worker_defaults(monkeypatch):
    from devharness_core.watchdog import THREAD_ENVIRONMENT_KEYS
    for key in THREAD_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    settings = _environment()["thread_settings"]
    assert settings["OMP_NUM_THREADS"] == "3"
    assert settings["VECLIB_MAXIMUM_THREADS"] == "1"
