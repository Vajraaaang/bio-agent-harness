"""CLI contract checks; no workflow, data fit or model session is launched."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _cli():
    return importlib.import_module("devbench.analysis.fastq_cli")


def _record_refusal(error, output_root):
    return {"status": "refused", "stage": "plan_input", "engine_launch_count": 0,
            "scientific_correctness": None, "reason": str(error), "attempt_dir": str(output_root / "attempt")}


def test_cli_forwards_explicit_plan_and_runtime_options(tmp_path, monkeypatch, capsys):
    cli = _cli()
    plan = tmp_path / "plan.json"
    value = {"formula": "~ batch + condition", "contrast": {"factor": "condition", "numerator": "treated", "denominator": "control"}}
    plan.write_text(json.dumps(value))
    calls = []
    result = {"status": "preflight_passed", "attempt_dir": str(tmp_path / "attempt"), "scientific_correctness": None}
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: calls.append((args, kwargs)) or result)
    output = tmp_path / "output"
    executable = tmp_path / "nextflow"
    assert cli.main(["--plan", str(plan), "--output", str(output), "--nextflow", str(executable), "--timeout-sec", "120", "--preflight-only"]) == 0
    assert calls == [((value, output), {"nextflow_executable": executable, "timeout_sec": 120, "preflight_only": True,
                                     "resources": {"cpus": 2, "memory_gb": 6}, "execution_profile": "standard"})]
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize(("status", "success"), [("completed", True), ("preflight_passed", True), ("refused", False), ("failed", False), ("unknown", False)])
def test_cli_exit_codes_describe_execution_status_only(tmp_path, monkeypatch, capsys, status, success):
    cli = _cli()
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    result = {"status": status, "scientific_correctness": None, "biological_agreement": "not_run", "model_sessions": 0}
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: result)
    assert (cli.main(["--plan", str(plan), "--output", str(tmp_path / "output")]) == 0) is success
    assert json.loads(capsys.readouterr().out)["scientific_correctness"] is None


@pytest.mark.parametrize(("cpus", "memory_gb"), [(1, 1), (8, 64), (64, 1024)])
def test_cli_forwards_explicit_resource_limits(tmp_path, monkeypatch, capsys, cpus, memory_gb):
    cli = _cli()
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    calls = []
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: calls.append(kwargs) or {"status": "preflight_passed"})
    assert cli.main(["--plan", str(plan), "--output", str(tmp_path / "output"), "--preflight-only",
                     "--cpus", str(cpus), "--memory-gb", str(memory_gb)]) == 0
    assert calls[0]["resources"] == {"cpus": cpus, "memory_gb": memory_gb}
    capsys.readouterr()


@pytest.mark.parametrize("profile", ["standard", "minimal-counts"])
def test_cli_forwards_explicit_execution_profile(tmp_path, monkeypatch, capsys, profile):
    cli = _cli()
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    calls = []
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: calls.append(kwargs) or {"status": "preflight_passed"})
    assert cli.main(["--plan", str(plan), "--output", str(tmp_path / "output"), "--preflight-only",
                     "--execution-profile", profile]) == 0
    assert calls[0]["execution_profile"] == profile
    capsys.readouterr()


def test_unknown_execution_profile_cannot_reach_execution(tmp_path, monkeypatch):
    cli = _cli()
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: pytest.fail("unknown profile reached runner"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--plan", str(tmp_path / "plan.json"), "--output", str(tmp_path / "output"),
                  "--execution-profile", "silent-skip-qc"])
    assert error.value.code != 0


@pytest.mark.parametrize(("option", "value"), [
    ("--cpus", "0"), ("--cpus", "65"), ("--cpus", "1.5"),
    ("--memory-gb", "0"), ("--memory-gb", "1025"), ("--memory-gb", "1.5"),
])
def test_invalid_resource_limits_cannot_reach_execution(tmp_path, monkeypatch, option, value):
    cli = _cli()
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: pytest.fail("invalid resources reached runner"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--plan", str(tmp_path / "plan.json"), "--output", str(tmp_path / "output"), option, value])
    assert error.value.code != 0


@pytest.mark.parametrize("body", ["{", "[]", '{"alpha":NaN}', '{"alpha":1e400}', '{"formula":"~ condition","formula":"~ batch + condition"}'])
def test_invalid_json_plan_does_not_launch(tmp_path, monkeypatch, capsys, body):
    cli = _cli()
    plan = tmp_path / "plan.json"
    plan.write_text(body)
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: pytest.fail("invalid input reached runner"))
    monkeypatch.setattr(cli, "record_input_refusal", _record_refusal)
    assert cli.main(["--plan", str(plan), "--output", str(tmp_path / "output")]) != 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused"
    assert result["engine_launch_count"] == 0
    assert result["scientific_correctness"] is None
    assert result["stage"] == "plan_input"


def test_missing_plan_does_not_launch(tmp_path, monkeypatch, capsys):
    cli = _cli()
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: pytest.fail("missing plan reached runner"))
    monkeypatch.setattr(cli, "record_input_refusal", _record_refusal)
    assert cli.main(["--plan", str(tmp_path / "missing.json"), "--output", str(tmp_path / "output")]) != 0
    assert json.loads(capsys.readouterr().out)["engine_launch_count"] == 0


@pytest.mark.parametrize("kind", ["symlink", "oversized"])
def test_unbounded_or_noncanonical_plan_does_not_launch(tmp_path, monkeypatch, capsys, kind):
    cli = _cli()
    plan = tmp_path / "plan.json"
    if kind == "symlink":
        source = tmp_path / "source.json"
        source.write_text("{}")
        plan.symlink_to(source)
    else:
        plan.write_bytes(b" " * (cli.MAX_PLAN_BYTES + 1))
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: pytest.fail("invalid input reached runner"))
    monkeypatch.setattr(cli, "record_input_refusal", _record_refusal)
    assert cli.main(["--plan", str(plan), "--output", str(tmp_path / "output")]) != 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "refused"
    assert result["stage"] == "plan_input"


@pytest.mark.parametrize("option", ["--plan", "--output", "--nextflow"])
def test_relative_cli_paths_are_rejected(tmp_path, monkeypatch, option):
    cli = _cli()
    monkeypatch.setattr(cli, "run_fastq_plan", lambda *args, **kwargs: pytest.fail("relative path reached runner"))
    arguments = {"--plan": str(tmp_path / "plan.json"), "--output": str(tmp_path / "output")}
    arguments[option] = "relative-path"
    with pytest.raises(SystemExit) as error:
        cli.main([piece for pair in arguments.items() for piece in pair])
    assert error.value.code != 0


@pytest.mark.parametrize("timeout", ["0", "-1", "86401", "1.5"])
def test_invalid_timeout_is_rejected(tmp_path, timeout):
    with pytest.raises(SystemExit) as error:
        _cli().main(["--plan", str(tmp_path / "plan.json"), "--output", str(tmp_path / "output"), "--timeout-sec", timeout])
    assert error.value.code != 0


def test_cli_entrypoint_registered():
    import tomllib
    config = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(config.read_text())["project"]["scripts"]["devbench-fastq-de"] == "devbench.analysis.fastq_cli:main"
