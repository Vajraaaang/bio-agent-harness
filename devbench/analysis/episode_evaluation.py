"""Coordinator-only evaluation of sealed episodes; never episode feedback.

Freeze the reference/configuration before the first action. These records use
the existing canonical hashes and artifact copy/verification. They are not a
reward API, training system, or replacement for DevBench's study evaluators.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

from .checkpoints import atomic_json, digest, sha256
from .episodes import _json_copy, verify_episode


def _evaluator_identity(evaluator):
    if not inspect.isfunction(evaluator) or evaluator.__closure__:
        raise ValueError("Use a source-backed evaluator function with explicit configuration")
    source = inspect.getsourcefile(evaluator)
    if not source or not Path(source).is_file():
        raise ValueError("Evaluator source identity is unavailable")
    return {"callable": evaluator.__module__ + "." + evaluator.__qualname__,
            "source_sha256": sha256(Path(source)),
            "scope": "callable_name_and_source_file_not_authenticated_independence"}


def prepare_evaluation(episode, output, *, reference_path, reference_scope, evaluator,
                       configuration=None, allow_reference_access=False):
    if allow_reference_access is not True:
        raise PermissionError("Protected reference access is disabled")
    from devbench.study.permissions import require_protected_storage
    from devharness_core.provenance import copy_artifact
    if episode.state != "ready" or episode.actions or (episode.broker and episode.broker.events):
        raise ValueError("Evaluation configuration must be frozen before episode actions")
    if reference_scope not in {"synthetic_truth", "public_reference", "experimental_ground_truth"}:
        raise ValueError("Declare a supported reference scope")
    output, reference = Path(output), Path(reference_path)
    if not output.is_absolute() or not reference.is_absolute() or output.is_symlink() or reference.is_symlink():
        raise ValueError("Evaluation storage requires regular absolute paths")
    output, reference = output.resolve(), reference.resolve()
    if (output.is_relative_to(episode.root) or episode.root.is_relative_to(output)
            or reference.is_relative_to(episode.root)):
        raise ValueError("Protected evaluation storage and references must remain outside the episode")
    require_protected_storage(output)
    identity = _evaluator_identity(evaluator)
    configuration = _json_copy(configuration or {})
    if not isinstance(configuration, dict):
        raise ValueError("Evaluation configuration must be an object")
    if not reference.is_file() or reference.stat().st_size > 1_000_000:
        raise ValueError("Use a bounded reference JSON file")
    parsed = _json_copy(json.loads(reference.read_text()))
    if not isinstance(parsed, dict):
        raise ValueError("Reference JSON must be an object")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    preserved = output / "reference.json"
    copy_artifact(reference, preserved, max_bytes=1_000_000, max_entries=1)
    preserved.chmod(0o400)
    plan = {"format": "devbench.analysis.episode-evaluation-plan/1", "episode_config_sha256": episode.config_id,
            "episode_instance_id": episode.instance_id,
            "snapshot_id": episode.snapshot_id, "reference_scope": reference_scope,
            "reference_sha256": sha256(preserved), "configuration": configuration,
            "evaluator": identity, "frozen_before_actions": True,
            "scope": "coordinator_owned_reference_assertion_not_independent_validation_certificate"}
    path = output / "plan.json"
    atomic_json(path, plan)
    path.chmod(0o400)
    identity = sha256(path)
    episode.register_evaluation(identity)
    return {"plan_path": str(path), "plan_sha256": identity}


def _validated_scores(scores, reference_scope):
    scores = _json_copy(scores)
    booleans = {"scientific_correctness", "appropriate_refusal", "recovered_from_tool_error"}
    counts = {"supported_findings_recovered", "false_discoveries", "unsupported_claims"}
    if not isinstance(scores, dict) or set(scores) - booleans - counts - {"concordance"}:
        raise ValueError("Evaluator returned unsupported score fields")
    result = {name: scores.get(name) for name in sorted(booleans | counts)}
    for name, value in result.items():
        if value is not None and (type(value) is not bool if name in booleans else type(value) is not int or value < 0):
            raise ValueError("Invalid typed evaluation score: " + name)
    concordance = scores.get("concordance", {})
    if not isinstance(concordance, dict) or len(concordance) > 50 or any(
            not isinstance(name, str) or len(name) > 100 or isinstance(value, bool)
            or not isinstance(value, (int, float)) for name, value in concordance.items()):
        raise ValueError("Concordance requires bounded named finite numerical values")
    result["concordance"] = concordance
    if reference_scope == "public_reference" and result["scientific_correctness"] is not None:
        raise ValueError("Public-reference concordance cannot certify scientific accuracy")
    return result


def evaluate_episode(root, expected_root, *, plan_path, expected_plan, evaluator, allow_reference_access=False):
    if allow_reference_access is not True:
        raise PermissionError("Protected reference access is disabled")
    root, plan_path = Path(root), Path(plan_path)
    if (not root.is_absolute() or not plan_path.is_absolute() or plan_path.is_symlink()
            or plan_path.resolve().is_relative_to(root.resolve())):
        raise ValueError("Use a coordinator evaluation plan outside the episode")
    verify_episode(root, expected_root)
    if sha256(plan_path) != expected_plan:
        raise ValueError("Evaluation plan identity changed")
    plan = json.loads(plan_path.read_text())
    config = json.loads((root / "parameters.json").read_text())
    manifest = json.loads((root / "audit" / "manifest.json").read_text())
    if (plan.get("format") != "devbench.analysis.episode-evaluation-plan/1"
            or plan["episode_config_sha256"] != digest(config) or plan["snapshot_id"] != config["snapshot_id"]
            or plan.get("episode_instance_id") != manifest["run_id"]
            or plan["evaluator"] != _evaluator_identity(evaluator)):
        raise ValueError("Evaluation identity does not match the sealed episode")
    committed = False
    for node_id in manifest["node_ids"]:
        node = json.loads((root / "audit" / "nodes" / (node_id + ".json")).read_text())
        if node["kind"] == "episode.evaluation_registered":
            if node["payload"] != {"plan_sha256": expected_plan, "instance_id": manifest["run_id"]} or committed:
                raise ValueError("Episode evaluation commitment mismatch")
            committed = True
        elif node["kind"] in {"episode.action", "episode.action_rejected"} and not committed:
            raise ValueError("Episode has no evaluation commitment before its actions")
    if not committed:
        raise ValueError("Episode evaluation commitment is missing")
    reference = plan_path.parent / "reference.json"
    if reference.is_symlink() or sha256(reference) != plan["reference_sha256"]:
        raise ValueError("Protected reference identity changed")
    terminal = json.loads((root / "episode.json").read_text())
    results = []
    for path in sorted(root.glob("checkpoints/action-*/observation.json")):
        observation = json.loads(path.read_text())
        if observation.get("status") == "succeeded":
            results.append(observation["result"])
    output = plan_path.parent / (expected_root + ".json")
    if output.exists():
        raise FileExistsError("An evaluation attempt already exists; preserve it")
    record = {"format": "devbench.analysis.episode-evaluation/1", "episode_root_hash": expected_root,
              "evaluation_plan_sha256": expected_plan, "reference_scope": plan["reference_scope"],
              "reference_sha256": plan["reference_sha256"], "evaluator": plan["evaluator"],
              "outcome": terminal["outcome"], "harness_accepted": terminal["harness_accepted"],
              "usage": terminal["usage"], "record_path": str(output), "agent_feedback": False}
    # Reserve this attempt before running any evaluator code. A competing call
    # cannot replace a failure or an interrupted/running attempt with success.
    with output.open("x") as handle:
        json.dump({**record, "status": "running"}, handle, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    output.chmod(0o600)
    try:
        observed = {"terminal": terminal, "tool_results": results,
                    "actions": json.loads((root / "actions.json").read_text())}
        scores = _validated_scores(evaluator(_json_copy(observed), json.loads(reference.read_text()),
                                             _json_copy(plan["configuration"])), plan["reference_scope"])
        correctness = scores["scientific_correctness"]
        record.update(status="completed", **scores,
            invalid_results_accepted=None if correctness is None else bool(terminal["harness_accepted"] and not correctness),
            valid_results_rejected=None if correctness is None else bool(not terminal["harness_accepted"] and correctness))
    except BaseException as error:
        record.update(status="failed", error_type=type(error).__name__, error=str(error)[:2000],
                      scientific_correctness=None, invalid_results_accepted=None, valid_results_rejected=None)
        atomic_json(output, record)
        raise
    atomic_json(output, record)
    return record
