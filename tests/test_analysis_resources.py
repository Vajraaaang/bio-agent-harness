import pytest


def test_stage_measurement_preserves_failure_and_records_scope():
    from devbench.analysis.resources import StageMeasurement
    with pytest.raises(RuntimeError, match="intentional"):
        with StageMeasurement("failure_test") as measured:
            raise RuntimeError("intentional")
    assert measured.result["status"] == "failed"
    assert measured.result["error_type"] == "RuntimeError"
    assert measured.result["wall_seconds"] >= 0
    assert measured.result["sampled_peak_rss_bytes"] >= measured.result["baseline_rss_bytes"] > 0
    assert "RSS" in measured.result["memory_scope"]


def test_stage_memory_includes_numerical_child_process():
    import subprocess
    import sys
    from devbench.analysis.resources import StageMeasurement
    with StageMeasurement("child_test", sample_interval=0.005) as measured:
        subprocess.run([sys.executable, "-c", "import time; data=b'x'*(64*1024**2); time.sleep(.15)"], check=True)
    assert measured.result["sampled_peak_rss_bytes"] - measured.result["baseline_rss_bytes"] > 32*1024**2
