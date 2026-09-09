import json
from pathlib import Path

import pytest


def task(tmp_path, **changes):
    spec = {"task_id": "small", "instruction": "Return a number", "grader": {
        "type": "numeric_tolerance", "field": "value", "expected": 4,
        "tolerances": {"type": "absolute", "value": 0}},
        "answer_schema": {"type": "object", "required": ["value"], "properties": {"value": {"type": "number"}}}}
    spec.update(changes)
    path = tmp_path / "task.json"
    path.write_text(json.dumps(spec))
    return str(path)


def test_runner_stages_and_cleans_isolated_workspaces(tmp_path):
    from devbench_engine.runner import EvalRunner
    seen = []
    def agent(instruction, work_dir):
        work = Path(work_dir)
        assert work.is_absolute() and work.exists()
        assert instruction == "Return a number"
        seen.append(work)
        (work / "eval_answer.json").write_text(json.dumps({"value": 4}))
        return None
    runner = EvalRunner(task(tmp_path))
    assert runner.run(agent)["passed"]
    assert runner.run(agent)["passed"]
    assert seen[0] != seen[1] and all(not p.exists() for p in seen)


def test_runner_schema_errors_and_agent_failures(tmp_path):
    from devbench_engine.runner import EvalRunner
    runner = EvalRunner(task(tmp_path))
    assert not runner.run(lambda *_: {"value": "4"})["passed"]
    result = runner.run(lambda *_: {"answer": {"value": 4}, "metadata": {"tokens": 12, "cost_usd": .01}})
    assert result["passed"] and result["metadata"]["tokens"] == 12
    assert result["metadata"]["peak_memory_mb"] > 0
    def broken(*_):
        raise ValueError("agent failed")
    failed = runner.run(broken)
    assert not failed["passed"] and failed["metadata"]["failure_type"] == "agent_error"


def test_traversal_rejected(tmp_path):
    from devbench_engine.runner import EvalRunner
    (tmp_path / "asset.txt").write_text("asset")
    spec = task(tmp_path, files=[{"source": "asset.txt", "destination": "../outside"}])
    result = EvalRunner(spec).run(lambda *_: {"value": 4})
    assert not result["passed"]
    assert result["metadata"]["failure_type"] == "staging_error"


def test_synthetic_fixtures_and_sweep(tmp_path):
    from experiments.run_sweep import run_sweep
    from experiments.compile_metrics import compile_metrics
    specs = [task(tmp_path)]
    runs = run_sweep({"mock": lambda *_: {"answer": {"value": 4}, "metadata": {"tokens": 30}}}, specs, repetitions=2)
    assert len(runs) == 2 and all(r["passed"] for r in runs)
    report = compile_metrics(runs)
    assert "100.0%" in report and "mock" in report and "unknown" in report


def test_collinear_fixture_requires_captured_tool_calls(tmp_path):
    from devbench_engine.runner import EvalRunner
    from benchmarks import TASK_DIRECTORY
    from devharness_server.dispatcher import ToolDispatcher
    runner = EvalRunner(str(TASK_DIRECTORY / "collinear_trap.json"))
    forged = {"answer": {"rank_deficient": True, "corrected_formula": "1 + cr(numeric_time, df=3, constraints='center')"},
              "metadata": {"tool_calls": [{"tool_name": "validate_design_matrix"}]}}
    assert not runner.run(lambda *_: forged)["passed"]
    def agent(_, work_dir):
        work = Path(work_dir)
        dispatcher = ToolDispatcher()
        bad = json.loads(dispatcher.execute("validate_design_matrix", {
            "metadata_csv_path": str(work / "metadata.csv"),
            "formula_str": "1 + numeric_time + C(batch)"}))
        corrected = "1 + cr(numeric_time, df=3, constraints='center')"
        dispatcher.execute("fit_temporal_trajectory", {"counts_path": str(work / "counts.zarr"),
            "metadata_path": str(work / "metadata.csv"), "formula_str": corrected,
            "time_col": "embryonic_age", "gene_list": ["Sox2", "Tbr1"]})
        return {"rank_deficient": not bad["is_full_rank"], "corrected_formula": corrected,
                "stage_kinetics": {stage: 1.0 for stage in ["E8.5", "E10.5", "E12.5", "E14.5"]}, "master_regulators": ["Sox2", "Pax6", "Neurog2", "Tbr1"],
                "condition_number_validated": True, "biological_rationale": "Validated and corrected the design."}
    result = runner.run(agent)
    assert result["passed"], result


def test_invalid_metadata_is_an_answer_error(tmp_path):
    from devbench_engine.runner import EvalRunner
    result = EvalRunner(task(tmp_path)).run(lambda *_: {"answer": {"value": 4}, "metadata": {"tokens": object()}})
    assert not result["passed"]
    assert result["metadata"]["failure_type"] == "answer_error"
    json.dumps(result, allow_nan=False)


def test_resource_interrupt_taxonomy(tmp_path):
    from devbench_engine.runner import EvalRunner
    from devharness_core.watchdog import HarnessResourceInterrupt
    def resource_error(*_):
        raise HarnessResourceInterrupt("budget exceeded")
    result = EvalRunner(task(tmp_path)).run(resource_error)
    assert result["metadata"]["failure_type"] == "resource_interrupt"


def test_process_inspection_denied_is_reported_without_crashing(tmp_path, monkeypatch):
    from devbench_engine.runner import EvalRunner
    import psutil
    def denied(*_, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(psutil.Process, "children", denied)
    result = EvalRunner(task(tmp_path)).run(lambda *_: {"value": 4})
    assert result["passed"]
    assert result["metadata"]["memory_measurement_error"]


def test_tool_evidence_rejects_irrelevant_analysis():
    from devbench_engine.runner import _tool_evidence
    events = [{"tool_name": "validate_design_matrix", "arguments": {"metadata_csv_path": "/tmp/trap/metadata.csv", "formula_str": "numeric_time + I(numeric_time)"},
               "result": {"is_full_rank": False}, "succeeded": True},
              {"tool_name": "fit_temporal_trajectory", "arguments": {"metadata_path": "/tmp/trap/metadata.csv", "counts_path": "/tmp/trap/counts.zarr", "gene_list": ["Invariant"], "formula_str": "numeric_time"},
               "result": {"formula": "cr(numeric_time, df=3)", "design": {"is_full_rank": True}, "genes": {"Invariant": {}}}, "succeeded": True}]
    assert not _tool_evidence(events, "/tmp/trap", {"corrected_formula": "bad"})


def test_updated_mock_adapter_all_benchmarks():
    from benchmarks import TASK_DIRECTORY
    from devbench_engine.adapters import InProcessMockAdapter
    from devbench_engine.runner import EvalRunner
    for spec in sorted(TASK_DIRECTORY.glob("*.json")):
        result = EvalRunner(str(spec)).run(InProcessMockAdapter())
        assert result["passed"], result
        assert result["metadata"]["tool_calls"]


def test_guidance_and_schema_are_staged_for_adapter(tmp_path):
    from devbench_engine.adapters import InProcessMockAdapter
    from devbench_engine.runner import EvalRunner
    def callback(instruction, workspace):
        assert (workspace / "AGENTS.md").exists()
        assert json.loads((workspace / "answer_schema.json").read_text())["required"] == ["value"]
        return {"value": 4}
    assert EvalRunner(task(tmp_path)).run(InProcessMockAdapter(callback))["passed"]


def test_latch_style_task_and_grader_config(tmp_path):
    from devbench_engine.runner import EvalRunner
    spec = {"id": "latch_style", "task": "Return a number", "grader": {"type": "numeric_tolerance", "config": {
        "field": "value", "expected": 4, "tolerances": {"type": "absolute", "value": 0}}}}
    path = tmp_path / "latch.json"
    path.write_text(json.dumps(spec))
    result = EvalRunner(str(path)).run(lambda *_: {"value": 4})
    assert result["task_id"] == "latch_style" and result["passed"]


def test_exited_child_does_not_invalidate_memory_measurement(monkeypatch):
    import psutil
    from devbench_engine.runner import _MemorySampler
    class Exited:
        def memory_info(self):
            raise psutil.NoSuchProcess(123456)
    class Running:
        def children(self, recursive):
            return [Exited()]
        def memory_info(self):
            return type("Memory", (), {"rss": 2_000_000})()
    monkeypatch.setattr(psutil, "Process", lambda _: Running())
    sampler = _MemorySampler()
    sampler._sample()
    assert sampler.error is None and sampler.peak_mb == 2
