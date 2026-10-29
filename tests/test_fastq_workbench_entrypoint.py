"""The existing workbench exposes the same explicit FASTQ procedure CLI.

These are command-routing controls, not Nextflow or scientific evidence.
"""
import json

import pytest


def test_fastq_help_is_available_from_existing_workbench(capsys):
    from devbench.analysis.workbench import main

    with pytest.raises(SystemExit) as stopped:
        main(["fastq", "--help"])

    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "--plan" in output
    assert "--output" in output
    assert "--preflight-only" in output
    assert "independent scientific correctness" in output


def test_workbench_preserves_fastq_preflight_arguments_and_failed_exit(tmp_path, monkeypatch, capsys):
    from devbench.analysis import fastq_cli
    from devbench.analysis.workbench import main

    plan_path = tmp_path / "plan.json"
    plan_path.write_text('{"declared": "command-routing-fixture"}', encoding="utf-8")
    output_root = tmp_path / "attempts"
    seen = []

    def stopped_before_engine(plan, destination, **kwargs):
        seen.append((plan, destination, kwargs))
        return {"status": "refused", "reason": "command-routing-fixture", "engine_launches": 0,
                "scientific_correctness": None, "model_sessions": 0}

    monkeypatch.setattr(fastq_cli, "run_fastq_plan", stopped_before_engine)
    code = main(["fastq", "--plan", str(plan_path), "--output", str(output_root),
                 "--timeout-sec", "42", "--preflight-only"])

    assert code == 1
    assert seen == [({"declared": "command-routing-fixture"}, output_root,
                     {"nextflow_executable": None, "timeout_sec": 42, "preflight_only": True,
                      "resources": {"cpus": 2, "memory_gb": 6}, "execution_profile": "standard"})]
    assert json.loads(capsys.readouterr().out)["scientific_correctness"] is None
    assert not output_root.exists()
