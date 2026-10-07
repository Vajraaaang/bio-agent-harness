"""CLI configuration and no-execution contracts; no datasets or participants."""
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


def cli():
    assert importlib.util.find_spec("devbench.study.cli"), "Study CLI is missing"
    from devbench.study import cli
    return cli


@pytest.fixture
def protected_tmp():
    parent = Path(__file__).resolve().parents[1] / "tmp"
    parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="study-cli-config-", dir=parent) as directory:
        yield Path(directory)


def test_plan_command_creates_only_configuration_and_empty_dashboard(protected_tmp, capsys):
    destination = protected_tmp / "planned"
    assert cli().main(["plan", "--stage", "smoke", "--output", str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data_executed"] is False
    assert result["model_participants_launched"] == 0
    assert (destination / "private" / "plan.json").is_file()
    assert (destination / "report" / "index.html").is_file()
    assert not (destination / "data").exists()


@pytest.mark.parametrize("args", [
    ["prepare-data", "--plan", "/missing", "--output", "/missing"],
    ["import-public", "--plan", "/missing", "--input-bundle", "/missing", "--output", "/missing",
     "--provenance", "/missing", "--cohort-id", "missing"],
    ["run", "--plan", "/missing", "--bundles", "/missing", "--output", "/missing", "--workspace-parent", "/missing"],
    ["report", "--plan", "/missing", "--records", "/missing", "--output", "/missing"],
])
def test_data_commands_reject_without_permission_before_file_reads(args, capsys):
    assert cli().main(args) == 2
    assert "authorization flag" in capsys.readouterr().err


def test_answer_schema_command_is_configuration_only(capsys):
    assert cli().main(["schema", "answer"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["additionalProperties"] is False
    assert "gene_statistics" in value["properties"]
