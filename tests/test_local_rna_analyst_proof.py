"""The reusable first proof has bounded fixtures and explicit execution consent."""
import csv
import json
from pathlib import Path
import tempfile
from uuid import uuid4

import pytest

from tests.test_analysis_episodes import workspace


def test_fixture_preserves_independent_donors_and_no_time_requirement(tmp_path):
    from scripts.run_local_rna_analyst_proof import prepare_fixture
    task, paths = prepare_fixture(tmp_path / "source", measurement="raw_counts")
    rows = list(csv.DictReader(paths["metadata"].open()))
    assert len(rows) == len({row["donor_id"] for row in rows}) == 8
    assert task["analysis_columns"]["time"] is None
    assert task["context"]["condition_labels"] == ["A", "B"]
    assert len(task["gene_ids"]) == 12
    assert task["questions"][0]["contrast"] == "B minus A"
    matrix = list(csv.reader(paths["expression"].open()))
    assert len(matrix) == 13 and len(matrix[0]) == 9
    assert all(int(value) >= 0 for row in matrix[1:] for value in row[1:])
    assert json.loads(paths["annotations"].read_text()) == {}
    assert all(path.is_absolute() for path in paths.values())


def test_fixture_continuous_values_are_not_converted_to_counts(tmp_path):
    from scripts.run_local_rna_analyst_proof import prepare_fixture
    task, paths = prepare_fixture(tmp_path / "source", measurement="continuous")
    rows = list(csv.reader(paths["expression"].open()))
    assert task["measurement"] == "continuous"
    assert any(float(value) != int(float(value)) for row in rows[1:] for value in row[1:])


def test_script_requires_execution_flag_before_output_or_input_access(tmp_path):
    from scripts.run_local_rna_analyst_proof import main
    output = tmp_path / "not-created"
    with pytest.raises(SystemExit) as exc:
        main(["--output", str(output)])
    assert exc.value.code == 2
    assert not output.exists()


def test_proof_refuses_outside_repository_even_with_execution_flag():
    from scripts.run_local_rna_analyst_proof import run_proof
    output = Path(tempfile.gettempdir()).resolve() / f"rna-proof-refused-{uuid4().hex}"
    assert not output.is_relative_to(Path(__file__).resolve().parents[1])
    assert not output.exists()
    with pytest.raises(ValueError, match="repository"):
        run_proof(output, allow_data_execution=True)
    assert not output.exists()


def test_paired_refusal_is_supported_by_design_audit_not_unit_inventory(tmp_path):
    from tests.test_rna_design_audit import design, prepared_plan
    rows = [{"sample_id": f"s{i + 1}", "donor_id": f"line{i % 4}",
             "library_id": f"library{i}", "condition": "A" if i < 4 else "B", "batch": "x"}
            for i in range(8)]
    declaration = design([row["sample_id"] for row in rows])
    declaration["source"]["statement"] = "Eight libraries from four repeated donor-derived cell lines; no block declared."
    _, plan, actions = prepared_plan(tmp_path, rows=rows, declaration=declaration)
    final = actions.perform("audit", actions.create_state(plan["session_id"]))
    assert final["status"] == "abstained"
    retained = actions.ledger.get(plan["session_id"], "audit", 0)["result"]["evidence"]
    assert retained["status"] == "refused"
    assert "repeated_biological_units:donor_id" in retained["blockers"]
    assert retained["sample_count"] == 8
    assert retained["unit_count"] == 4
    assert retained["selected_sample_ids"] == declaration["selected_sample_ids"]
    assert actions.verify_terminal(final) == final


def test_replay_integrity_is_required_even_when_numerical_values_match():
    from scripts.run_local_rna_analyst_proof import software_proof_passed
    result = {"actual_outcome": "completed", "expected_outcome": "completed", "artifact_integrity": True,
        "input_bytes_unchanged": True, "model_sessions_attempted": 0, "diagnostic_refusal": "not_applicable",
        "numerical_replay": "pass", "replay_artifact_integrity": False}
    assert not software_proof_passed([result])
    assert software_proof_passed([{**result, "replay_artifact_integrity": True}])


@pytest.mark.parametrize("outcome", ["completed", "refusal"])
def test_software_acceptance_and_self_match_are_not_scientific_passes(outcome):
    from scripts.run_local_rna_analyst_proof import proof_passed
    result = {"actual_outcome": outcome, "expected_outcome": outcome, "artifact_integrity": True,
        "input_bytes_unchanged": True, "model_sessions_attempted": 0, "harness_accepted": True,
        "diagnostic_refusal": "pass" if outcome == "refusal" else "not_applicable",
        "numerical_replay": "pass", "numerical_reference": "same_implementation_self_match",
        "replay_artifact_integrity": True, "scientific_correctness": None,
        "model_execution_authorized": False}
    assert not proof_passed([result])
    assert not proof_passed([{**result, "scientific_correctness": True}])
    if outcome == "refusal":
        assert not proof_passed([{**result, "scientific_correctness": True,
            "scientific_correctness_source": "independent_evaluation",
            "scientific_evaluation_reference": "separately verified refusal evaluation"}])
    assert result["model_execution_authorized"] is False
    assert result["model_sessions_attempted"] == 0


def test_refusal_observation_keeps_software_and_science_explicit(workspace):
    from devbench.analysis.episodes import AnalysisEpisode
    from scripts.run_local_rna_analyst_proof import prepare_fixture
    task, paths = prepare_fixture(workspace / "source", measurement="raw_counts")
    episode = AnalysisEpisode.create(workspace / "episode", question="Unsupported assay fixture",
        assay="snrna", public_task=task, inputs=paths, allow_data_execution=True)
    result = episode.step({"kind": "refuse", "reason": "Unsupported assay"})
    assert result["harness_accepted"] is True
    assert result["harness_acceptance_scope"] == "software_execution_or_supported_refusal"
    assert result["scientific_correctness"] is None
    assert result["scientific_trial_passed"] is False


def test_cli_exit_only_reports_software_acceptance(monkeypatch, tmp_path, capsys):
    from scripts import run_local_rna_analyst_proof as proof
    monkeypatch.setattr(proof, "run_proof", lambda *args, **kwargs: {
        "software_accepted": True, "scientific_trial_passed": False})
    assert proof.main(["--output", str(tmp_path / "proof"), "--allow-data-execution"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["scientific_trial_passed"] is False
