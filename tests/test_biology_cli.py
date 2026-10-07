"""CLI configuration/empty-report checks; no observations or agent turns."""
import json
from pathlib import Path
import tempfile

import pytest

from devbench.study.cli import main


@pytest.mark.parametrize("arguments", [
    ["biology-prepare", "--plan", "/missing", "--output", "/missing"],
    ["biology-run", "--plan", "/missing", "--bundles", "/missing", "--output", "/missing",
     "--workspace-root", "/missing"],
    ["biology-report", "--plan", "/missing", "--records", "/missing", "--output", "/missing"],
])
def test_execution_commands_deny_before_reading_missing_paths(arguments, capsys):
    assert main(arguments) == 2
    assert "authorization flag" in capsys.readouterr().err


def test_biology_answer_schema_is_configuration_only(capsys):
    assert main(["biology-schema", "answer"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["additionalProperties"] is False
    assert "target_results" in schema["properties"]


def test_biology_report_loader_default_denial():
    from devbench.study.biology_reporting import load_biology_report_records
    with pytest.raises(PermissionError, match="disabled"):
        load_biology_report_records(object(), object())


def test_plan_only_command_writes_private_config_and_public_empty_report(capsys):
    parent = Path(__file__).resolve().parents[1] / "tmp"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="biology-cli-config-", dir=parent) as temporary:
        destination = Path(temporary) / "planned"
        assert main(["biology-plan", "--coverage", "full", "--output", str(destination)]) == 0
        result = json.loads(capsys.readouterr().out)
        plan = json.loads((destination / "private/plan.json").read_text())
        assert len(plan["recipes"]) == 97
        assert all(recipe["generation_seed"] is None for recipe in plan["recipes"])
        assert result["data_executed"] is False and result["model_participants_launched"] == 0
        assert result["report"]["figure_count"] == 0
        html = (destination / "report/index.html").read_text()
        assert plan["blinding_nonce"] not in html
        assert "No measured" in html or "No results" in html
        assert not (destination / "data").exists()
