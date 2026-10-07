"""Small offline fixtures for the actual participant-side answer validator."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest
from pydantic import ValidationError

from devbench.staging import stage_python
from devbench.study.answers import StudyAnswer
from devbench.study.biology_answers import BiologyAnswer
from devbench.study.public_answers import PublicAnswer


@pytest.fixture(scope="module")
def participant_runtime(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("submission-runtime")
    stage_python(workspace, assisted=False)
    return workspace / ".venv"


@pytest.fixture
def workspace(tmp_path, participant_runtime):
    # Reuse third-party dependencies, with no editable-install or coordinator hook.
    (tmp_path / ".venv").symlink_to(participant_runtime, target_is_directory=True)
    return tmp_path


def stage(workspace, kind="biology", **options):
    from devbench.study.submission_validation import stage_submission_validator
    return stage_submission_validator(workspace, kind, **options)


def run_validator(staged, workspace):
    result = subprocess.run(staged.argv, cwd=workspace, capture_output=True, text=True,
                            timeout=15, env={"PATH": os.defpath, "HOME": str(workspace)})
    assert result.stderr == "", result.stderr
    report = json.loads(result.stdout)
    assert report["scope"] == "public_answer_contract"
    assert result.returncode == (0 if report["valid"] else 1)
    return report


def biology_answer(payload=None, target_id="event_timing"):
    target = dict(target_id=target_id, estimand="Observed event timing", units="time",
                  contrast="observed grid", reference_population="observed organisms",
                  prediction_reference="observed times", claim_type="restricted_estimate",
                  evidence_scope="population_mean", estimate_status="estimated",
                  payload=payload or {"kind": "events", "eligible_hypotheses": ["gene1"],
                      "event_intervals": [{"hypothesis_id": "gene1", "kind": "peak",
                                           "interval": {"lower": 1.0, "upper": 2.0}}],
                      "timing_definition": "observed_grid_brackets"})
    return dict(task_id="fixture", input_sha256="a" * 64, gene_universe_sha256="b" * 64,
                target_results=[target])


def public_answer():
    return dict(task_id="a" * 64, input_sha256="b" * 64,
                payload={"kind": "developmental_observation", "stage_labels": ["t1", "t2", "t3"],
                         "curves": [{"gene_id": "gene1", "means": [1.0, 2.0, 3.0]}]},
                limitations=["Descriptive observed libraries only."])


@pytest.mark.parametrize("kind, model, answer", [
    ("biology", BiologyAnswer, biology_answer()),
    ("study", StudyAnswer, {"task_id": "fixture", "input_sha256": "a" * 64,
                           "decision": "no_supported_discovery", "claim_scope": "descriptive"}),
    ("public", PublicAnswer, public_answer()),
])
def test_staged_validator_runs_without_coordinator_and_preserves_contract(workspace, kind, model, answer):
    staged = stage(workspace, kind)
    artifact = workspace / "final_discovery.json"
    content = json.dumps(answer).encode()
    artifact.write_bytes(content)
    model.model_validate_json(content)
    assert run_validator(staged, workspace) == {
        "valid": True, "scope": "public_answer_contract", "errors": [], "errors_truncated": False}
    assert artifact.read_bytes() == content
    assert shlex.split(staged.command) == list(staged.argv)
    assert staged.argv[0] == str(workspace / ".venv/bin/python")
    assert all(path.is_absolute() and path.stat().st_mode & 0o222 == 0 for path in staged.protected_paths)
    assert staged.hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in staged.protected_paths}

    # A real isolated Python process cannot resolve coordinator packages, and its
    # extracted model exposes exactly the same public schema as the source model.
    probe = """import importlib.util,json,runpy,sys
for name in ('devbench', 'devbench_engine', 'benchmarks', 'experiments', 'scripts'):
    assert importlib.util.find_spec(name) is None, name
namespace = runpy.run_path(sys.argv[1])
assert not any(name.startswith(('devbench', 'devbench_engine')) for name in sys.modules)
print(json.dumps(namespace['_ANSWER_MODEL'].model_json_schema()))
"""
    result = subprocess.run([staged.argv[0], "-I", "-B", "-c", probe, str(staged.protected_paths[0])],
                            cwd=workspace, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == model.model_json_schema()
    source = staged.protected_paths[0].read_text()
    for forbidden in ("BiologyCatalog", "load_biology_catalog", "DatasetRecipe", "StudyPlan",
                      "grade_biology_answer", "score_answer", "private/truth", "IdentificationFinding"):
        assert forbidden not in source


@pytest.mark.parametrize("case, expected_path, expected_message", [
    ("event_family", "$.target_results[0].payload", "Unavailable event hypotheses must belong"),
    ("coordinate_values", "$.target_results[0].payload.gene_statistics[0]", "coordinate ids must match values"),
])
def test_actual_semantic_failures_are_field_errors_and_never_repaired(workspace, case, expected_path, expected_message):
    answer = biology_answer()
    if case == "event_family":
        answer["target_results"][0]["payload"]["unavailable_hypotheses"] = {"gene2": "no_information"}
    else:
        answer = biology_answer({"kind": "effects", "analysis_performed": True,
            "gene_statistics": [{"hypothesis_id": "gene1", "estimate_status": "estimated",
                                 "coordinate_ids": ["t1", "t2"], "values": [1.0]}],
            "selection_conditional_inference": False, "biological_unit": "organism",
            "analysis_layer": "continuous", "effect_definition": "observed difference"}, "sampled_mean")
    original = json.dumps(answer).encode()
    with pytest.raises(ValidationError, match=expected_message):
        BiologyAnswer.model_validate_json(original)
    staged = stage(workspace)
    artifact = workspace / "final_discovery.json"
    artifact.write_bytes(original)
    report = run_validator(staged, workspace)
    assert report["valid"] is False
    assert any(error["path"] == expected_path and expected_message in error["message"]
               for error in report["errors"])
    assert artifact.read_bytes() == original
    assert set(report) == {"valid", "scope", "errors", "errors_truncated"}


@pytest.mark.parametrize("content, message", [
    (b'{"value": NaN}', "finite"),
    (b'{"value": Infinity}', "finite"),
    (b'{"value": 1e999}', "finite"),
    (b'{"task_id":"one","task_id":"two"}', "Duplicate"),
    (b'{}{}', "Invalid JSON"),
    (b'[]', "JSON object"),
])
def test_nonfinite_or_nonplain_json_is_rejected(workspace, content, message):
    staged = stage(workspace)
    (workspace / "final_discovery.json").write_bytes(content)
    report = run_validator(staged, workspace)
    assert report["valid"] is False
    assert message in report["errors"][0]["message"]
    if b"value" in content:
        assert report["errors"][0]["path"] == "$.value"


def test_byte_limit_and_regular_file_requirement(workspace):
    staged = stage(workspace, max_answer_bytes=64)
    artifact = workspace / "final_discovery.json"
    artifact.write_bytes(b" " * 65)
    assert "64 bytes" in run_validator(staged, workspace)["errors"][0]["message"]
    artifact.unlink()
    artifact.symlink_to(staged.protected_paths[0])
    assert run_validator(staged, workspace)["valid"] is False
    artifact.unlink()
    os.mkfifo(artifact)
    assert run_validator(staged, workspace)["valid"] is False


def test_large_error_lists_remain_concise(workspace):
    staged = stage(workspace)
    (workspace / "final_discovery.json").write_text(json.dumps({f"extra_{i}": "secret-value" for i in range(100)}))
    report = run_validator(staged, workspace)
    assert report["valid"] is False and report["errors_truncated"] is True
    assert len(report["errors"]) == 20
    assert "secret-value" not in json.dumps(report)


def test_oversized_field_names_do_not_expand_error_reports(workspace):
    staged = stage(workspace)
    (workspace / "final_discovery.json").write_text(json.dumps({"x" * 10_000: 1.0}))
    report = run_validator(staged, workspace)
    assert report["valid"] is False
    assert all(len(error["path"]) <= 240 for error in report["errors"])


def test_staging_is_deterministic_and_does_not_overwrite(workspace, tmp_path):
    staged = stage(workspace)
    second = tmp_path / "second"
    second.mkdir()
    duplicate = stage(second)
    assert staged.protected_paths[0].read_bytes() == duplicate.protected_paths[0].read_bytes()
    with pytest.raises(FileExistsError):
        stage(workspace)


@pytest.mark.parametrize("options", [{"max_answer_bytes": True}, {"max_answer_bytes": 0},
                                     {"max_answer_bytes": 8_000_001}])
def test_invalid_byte_bounds_are_rejected_before_staging(workspace, options):
    with pytest.raises(ValueError):
        stage(workspace, **options)
    assert not (workspace / "validate_submission.py").exists()


def test_unknown_contract_and_relative_workspace_are_rejected(workspace):
    with pytest.raises(ValueError):
        stage(workspace, "unknown")
    with pytest.raises(ValueError):
        stage(Path("relative"))
