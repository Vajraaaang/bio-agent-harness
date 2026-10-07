"""Small timing/policy regressions; no live model calls or cohort analyses."""
from pathlib import Path
import tempfile
import time

import pytest


@pytest.fixture
def broker_factory(monkeypatch):
    from devbench.study.broker import BrokerPolicy, ToolBroker
    root = Path(__file__).resolve().parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="phase-") as folder:
        directory = Path(folder)
        data = directory / "input.txt"
        data.write_text("local fixture")
        clock = [100.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        def make(condition, budget=60):
            policy = BrokerPolicy(condition=condition, input_sha256="a"*64, implementation_sha256="b"*64,
                                  measurement_type="continuous", gene_ids=("g",), population_inference_allowed=False,
                                  timeout_sec=budget)
            return ToolBroker(policy, {str(data): data}, directory / (condition+str(budget)),
                              allow_data_execution=True, defer_start=True)
        yield make, clock


@pytest.mark.parametrize("condition", ["optional", "enforced"])
@pytest.mark.parametrize("budget", [30, 60, 600])
def test_preflight_does_not_consume_broker_analysis_clock(broker_factory, condition, budget, monkeypatch):
    make, clock = broker_factory
    broker = make(condition, budget)
    clock[0] += 45
    assert broker.started is None
    with pytest.raises(RuntimeError, match="analysis.*not started"):
        broker.execute("inspect_study_design", {})
    window = broker.start_analysis()
    assert window["started_monotonic"] == 145
    assert broker.started + broker.policy.timeout_sec - clock[0] == budget
    # Exercise dispatch/deadline handling, not biological inference.
    monkeypatch.setattr(broker, "_invoke_tool", lambda *args: {"protocol_fixture": True})
    assert broker.execute("inspect_study_design", {})["status"] == "succeeded"
    with pytest.raises(RuntimeError, match="already started"):
        broker.start_analysis()
    clock[0] += budget
    failed = broker.execute("inspect_study_design", {})
    assert failed["status"] == "failed" and "deadline" in failed["error"]
    broker.close()


def test_request_deadline_is_independent_during_preflight_and_bounded_during_analysis():
    from devbench.study.transport import request_deadline
    from types import SimpleNamespace
    broker = SimpleNamespace(started=None, policy=SimpleNamespace(timeout_sec=60))
    assert request_deadline(broker, now=145) == 155
    broker.started = 150
    assert request_deadline(broker, now=150) == 160
    assert request_deadline(broker, now=207) == 210
    assert request_deadline(broker, now=211) == 210


@pytest.mark.parametrize("enforced", [False, True])
@pytest.mark.parametrize("name", ["minimum_units", "minimum_donors", "min_donors"])
def test_both_harness_conditions_share_the_minimum_unit_floor(enforced, name):
    from devbench.study.biology_broker import validate_biology_request
    import runpy
    public_task = runpy.run_path(str(Path(__file__).with_name("test_biology_broker_contracts.py").resolve()))["public_task"]
    with pytest.raises(ValueError, match="minimum|threshold"):
        validate_biology_request("mean_condition_analysis", {"options": {name: 2}}, public_task(), enforced=enforced)
    assert validate_biology_request("mean_condition_analysis", {"options": {name: 3}}, public_task(), enforced=enforced) == {name: 3}


def test_direct_numerical_option_parsers_share_the_same_floor():
    from devbench.study.biology_tools_bulk import validate_bulk_options
    from devbench.study.biology_tools_singlecell import validate_singlecell_options
    for validate in (validate_bulk_options, validate_singlecell_options):
        with pytest.raises(ValueError):
            validate({"minimum_units": 2})
        assert validate({"minimum_units": 3})["minimum_units"] == 3
