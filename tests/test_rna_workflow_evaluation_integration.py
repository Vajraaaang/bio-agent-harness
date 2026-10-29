"""P5 local executable controls and tamper checks; no scientific qualification."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_rna_workflow_evaluation import (
    api, cases, complete, prepare, protocol, run, checked_outcomes,
)


def test_uncertainty_uses_locked_dataset_clusters_not_callback_repetitions(tmp_path):
    _, _, _, _, report = complete(tmp_path)
    numeric = report["paired_effects"]["numeric_correctness"]
    assert numeric["split"] == "locked_test"
    assert numeric["independent_clusters"] == 1
    assert numeric["interval"] is None
    assert numeric["status"] == "insufficient_independent_clusters"
    assert report["qualified_independent_review"] is False


def test_same_split_related_views_form_one_cluster(tmp_path):
    data = cases()
    view = deepcopy(data[-1])
    view["case_id"] = "locked-second-view"
    view["family_id"] = "different-label-same-dataset"
    data.append(view)
    _, _, _, _, report = complete(tmp_path, data=data)
    assert report["denominator"] == 24
    assert report["paired_effects"]["numeric_correctness"]["independent_clusters"] == 1


def test_mutated_returned_receipt_cannot_change_retained_attempts(tmp_path):
    module, _, prepared = prepare(tmp_path)
    receipt = run(module, prepared)
    receipt["attempts"][0]["status"] = "blocked"
    with pytest.raises(ValueError, match="receipt|attempt|changed|binding"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=[])


def test_duplicate_or_nonfinite_coordinator_checks_cannot_inflate_scores(tmp_path):
    module, _, prepared = prepare(tmp_path)
    receipt = run(module, prepared)
    outcomes = checked_outcomes(receipt)
    with pytest.raises(ValueError, match="duplicate|outcome"):
        module.summarize_comparison(prepared, receipt, checked_outcomes=outcomes + outcomes[:1])
    outcomes[0]["endpoints"]["numeric_correctness"]["value"] = float("nan")
    with pytest.raises(ValueError):
        module.summarize_comparison(prepared, receipt, checked_outcomes=outcomes)


def test_attempt_artifact_tampering_prevents_replay(tmp_path):
    module, root, _, _, report = complete(tmp_path)
    path = next((root / "attempts").glob("*/attempt.json"))
    value = json.loads(path.read_text())
    value["status"] = "blocked"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="attempt|hash|changed|seal"):
        module.replay_comparison(root, report["report_seal_sha256"])


def test_cli_runs_arithmetic_campaign_and_readonly_replay(tmp_path):
    module = api()
    root = tmp_path / "cli-campaign"
    project = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", module.__name__, "--offline-example", "--output", str(root)]
    result = subprocess.run(command, cwd=project, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["provider_calls"] == 0
    assert data["denominator"] == 18
    assert data["scientific_improvement_claim"] is False
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    replay = subprocess.run([sys.executable, "-m", module.__name__, "--replay", "--output", str(root),
        "--expected-seal", data["report_seal_sha256"]], cwd=project, text=True,
        capture_output=True, timeout=30)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["valid"] is True
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_cli_cannot_admit_native_execution(tmp_path):
    module = api()
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", module.__name__, "--native", "--output",
        str(tmp_path / "not-created")], cwd=project, text=True, capture_output=True, timeout=30)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked"
    assert not (tmp_path / "not-created").exists()
