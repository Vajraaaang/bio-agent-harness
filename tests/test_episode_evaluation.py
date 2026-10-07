"""Coordinator-only evaluation of frozen episode outputs, with fixture truth."""
import importlib.util
import json
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_analysis_episodes import create, run_contrast, workspace


def api():
    assert importlib.util.find_spec("devbench.analysis.episode_evaluation") is not None, "Separate episode evaluation boundary is missing"
    from devbench.analysis.episode_evaluation import prepare_evaluation, evaluate_episode
    return prepare_evaluation, evaluate_episode


def contrast_evaluator(observed, reference, configuration):
    result = next(r for r in reversed(observed["tool_results"]) if r.get("target_results"))
    value = result["target_results"][0]["payload"]["gene_statistics"][0]["values"][0]
    correct = abs(value-reference["effect"]) <= configuration["absolute_tolerance"]
    return {"scientific_correctness": correct, "supported_findings_recovered": int(correct),
            "false_discoveries": int(not correct), "unsupported_claims": 0}


def prepare(episode, workspace, *, scope="synthetic_truth", effect=4):
    prepare_evaluation, _ = api()
    path = workspace / "protected-reference.json"
    path.write_text(json.dumps({"effect": effect, "private_sentinel": "reference-only-do-not-export"}))
    return prepare_evaluation(episode, workspace / "evaluation", reference_path=path,
        reference_scope=scope, evaluator=contrast_evaluator,
        configuration={"absolute_tolerance": 1e-10}, allow_reference_access=True)


def test_evaluation_requires_explicit_reference_access_before_any_read():
    prepare_evaluation, evaluate_episode = api()
    with pytest.raises(PermissionError):
        prepare_evaluation(None, None, reference_path=None, reference_scope=None, evaluator=None)
    with pytest.raises(PermissionError):
        evaluate_episode(None, None, plan_path=None, expected_plan=None, evaluator=None)


def test_correctness_is_scored_after_sealing_and_never_returned_to_episode(workspace):
    _, evaluate_episode = api()
    episode, _ = create(workspace)
    plan = prepare(episode, workspace)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    record = evaluate_episode(episode.root, episode.receipt["root_hash"], plan_path=plan["plan_path"],
        expected_plan=plan["plan_sha256"], evaluator=contrast_evaluator, allow_reference_access=True)
    assert record["scientific_correctness"] is True
    assert record["harness_accepted"] is True
    assert record["invalid_results_accepted"] is False
    assert episode.observation()["scientific_correctness"] is None
    assert not (episode.root / "evaluation").exists()
    assert "reference-only-do-not-export" not in (episode.root / "actions.json").read_text()
    assert Path(record["record_path"]).is_relative_to(workspace / "evaluation")


def test_frozen_reference_can_detect_accepted_but_wrong_result(workspace):
    _, evaluate_episode = api()
    episode, _ = create(workspace)
    # Intentional mismatched reference is a plumbing negative control, not a
    # claim that the real contrast calculation is incorrect.
    plan = prepare(episode, workspace, effect=100)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    record = evaluate_episode(episode.root, episode.receipt["root_hash"], plan_path=plan["plan_path"],
        expected_plan=plan["plan_sha256"], evaluator=contrast_evaluator, allow_reference_access=True)
    assert record["scientific_correctness"] is False
    assert record["harness_accepted"] is True
    assert record["invalid_results_accepted"] is True


def test_public_concordance_cannot_be_promoted_to_accuracy(workspace):
    _, evaluate_episode = api()
    episode, _ = create(workspace)
    plan = prepare(episode, workspace, scope="public_reference")
    run_contrast(episode)
    episode.step({"kind": "finish"})
    with pytest.raises(ValueError, match="concordance"):
        evaluate_episode(episode.root, episode.receipt["root_hash"], plan_path=plan["plan_path"],
            expected_plan=plan["plan_sha256"], evaluator=contrast_evaluator, allow_reference_access=True)


def test_evaluation_plan_must_be_frozen_before_actions(workspace):
    episode, _ = create(workspace)
    episode.step({"kind": "request_information", "message": "What are the biological replicates?"})
    with pytest.raises(ValueError, match="before"):
        prepare(episode, workspace)


def test_evaluation_storage_cannot_overlap_episode(workspace):
    prepare_evaluation, _ = api()
    episode, _ = create(workspace)
    reference = workspace / "private.json"
    reference.write_text('{"effect":4}')
    with pytest.raises(ValueError, match="outside"):
        prepare_evaluation(episode, episode.root / "evaluation", reference_path=reference,
            reference_scope="synthetic_truth", evaluator=contrast_evaluator,
            configuration={"absolute_tolerance":1e-10}, allow_reference_access=True)


def test_plan_from_later_identical_episode_cannot_score_prior_episode(workspace):
    _, evaluate_episode = api()
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    other = workspace / "later"
    other.mkdir()
    second, _ = create(other)
    assert second.config_id == episode.config_id
    plan = prepare(second, other)
    with pytest.raises(ValueError, match="episode|instance|commitment"):
        evaluate_episode(episode.root, episode.receipt["root_hash"], plan_path=plan["plan_path"],
            expected_plan=plan["plan_sha256"], evaluator=contrast_evaluator, allow_reference_access=True)


_race_entered = threading.Event()
_race_release = threading.Event()
_race_calls = []


def race_evaluator(observed, reference, configuration):
    _race_calls.append(threading.get_ident())
    index = len(_race_calls)
    _race_entered.set()
    _race_release.wait(timeout=2)
    if index == 1:
        raise ValueError("deliberate first evaluation failure")
    time.sleep(.1)
    return {"scientific_correctness": True}


def test_concurrent_evaluation_cannot_overwrite_failed_attempt(workspace):
    prepare_evaluation, evaluate_episode = api()
    episode, _ = create(workspace)
    reference = workspace / "private.json"
    reference.write_text('{"effect":4}')
    plan = prepare_evaluation(episode, workspace / "evaluation", reference_path=reference,
        reference_scope="synthetic_truth", evaluator=race_evaluator, allow_reference_access=True)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    _race_calls.clear()
    _race_entered.clear()
    _race_release.clear()
    kwargs = dict(plan_path=plan["plan_path"], expected_plan=plan["plan_sha256"],
                  evaluator=race_evaluator, allow_reference_access=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(evaluate_episode, episode.root, episode.receipt["root_hash"], **kwargs)
        assert _race_entered.wait(timeout=5)
        second = pool.submit(evaluate_episode, episode.root, episode.receipt["root_hash"], **kwargs)
        time.sleep(.2)
        _race_release.set()
        errors = []
        for future in (first, second):
            try:
                future.result(timeout=10)
            except (ValueError, FileExistsError) as error:
                errors.append(type(error).__name__)
    assert len(_race_calls) == 1
    assert sorted(errors) == ["FileExistsError", "ValueError"]
    result = json.loads((Path(plan["plan_path"]).parent / (episode.receipt["root_hash"] + ".json")).read_text())
    assert result["status"] == "failed"
