"""Local stage measurements; sampled RSS is an observation, not a hard limit."""
from __future__ import annotations

import os
import threading
import time

import psutil


def configure_threads():
    """Call at process entry, before importing numerical libraries."""
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                 "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMBA_NUM_THREADS"):
        os.environ[name] = "1"


class StageMeasurement:
    def __init__(self, name, sample_interval=0.02):
        self.name, self.interval = name, sample_interval
        self._stop = threading.Event()
        self.result = {}

    def _rss(self):
        total = self.process.memory_info().rss
        try:
            children = self.process.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            self.incomplete = True
            children = []
        for child in children:
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                self.incomplete = True
        return total

    def _sample(self):
        while not self._stop.wait(self.interval):
            self.peak = max(self.peak, self._rss())

    def __enter__(self):
        self.process = psutil.Process()
        self.incomplete = False
        self.start = time.monotonic()
        self.baseline = self.peak = self._rss()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, error_type, error, traceback):
        self.peak = max(self.peak, self._rss())
        self._stop.set()
        self.thread.join()
        self.result = {"stage": self.name, "wall_seconds": time.monotonic() - self.start,
                       "baseline_rss_bytes": self.baseline, "sampled_peak_rss_bytes": self.peak,
                       "sample_interval_seconds": self.interval,
                       "memory_scope": "process_tree_sum_RSS_not_unique_physical_memory_or_GPU",
                       "memory_coverage": "sampled_partial" if self.incomplete else "sampled",
                       "status": "failed" if error_type else "completed",
                       "error_type": error_type.__name__ if error_type else None}
