"""Verified, bounded clarification state; never deserialize executable objects."""
from __future__ import annotations

from dataclasses import asdict
import math
import os
from pathlib import Path
import threading
import time

from .checkpoints import CheckpointStore, atomic_json, digest, sha256

MAX_BYTES = 512_000_000
MAX_ENTRIES = 50_000


def _wall():
    return time.time()


def _read(path, limit=16_000_000):
    from devbench.study.evidence import _read as read_bytes, decode_json
    value = decode_json(read_bytes(path, limit))
    if not isinstance(value, dict):
        raise ValueError("Handoff records must be JSON objects")
    return value


def _inside(root, name):
    if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
        raise ValueError("Handoff paths must remain bundle-relative")
    path = root / name
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("Handoff paths must not traverse symlinks")
    return path


def _inventory(root, current):
    """Bind working files; finalized audit copies are verified separately."""
    files, directories, total = {}, [], 0
    ignored = {current / "index.json", current / "receipt.json"}
    for base, dirs, names in os.walk(root, followlinks=False):
        folder = Path(base)
        relative = folder.relative_to(root)
        for name in list(dirs):
            child = folder / name
            if child.is_symlink():
                raise ValueError("Handoff inventory contains a symlink")
            if (folder == root and name == "audit") or (len(relative.parts) == 2 and relative.parts[0] == "handoffs" and name == "audit"):
                dirs.remove(name)
            else:
                directories.append(child.relative_to(root).as_posix())
        for name in names:
            path = folder / name
            if path in ignored:
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError("Handoff requires regular working files")
            size = path.stat().st_size
            total += size
            if size > 128_000_000 or total > MAX_BYTES or len(files)+len(directories) >= MAX_ENTRIES:
                raise ValueError("Handoff inventory exceeds its bound")
            files[path.relative_to(root).as_posix()] = {"sha256": sha256(path), "size_bytes": size}
    return {"files": files, "directories": sorted(directories)}


def _remaining(clock, saved_at, now):
    if clock is None:
        return None
    if (not isinstance(clock, dict) or set(clock) != {"deadline_utc", "remaining_seconds"}
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in clock.values())
            or clock["remaining_seconds"] < 0):
        raise ValueError("Invalid saved deadline")
    if now < saved_at:
        raise ValueError("Wall clock precedes the handoff; do not extend a saved budget")
    return max(0, min(clock["remaining_seconds"], clock["deadline_utc"]-now))


def _clock(deadline, now_wall, now_monotonic):
    if deadline is None:
        return None
    remaining = max(0, deadline-now_monotonic)
    return {"deadline_utc": now_wall+remaining, "remaining_seconds": remaining}


def validate_clarification(response):
    from .episodes import _public_only
    if (not isinstance(response, dict) or set(response) != {"message", "preserves_frozen_inputs_and_settings"}
            or response["preserves_frozen_inputs_and_settings"] is not True
            or not isinstance(response["message"], str) or not response["message"].strip()
            or len(response["message"]) > 4000):
        raise ValueError("Clarification must preserve frozen inputs/settings; changes require a new reviewed snapshot")
    _public_only(response)


def apply_clarification(episode, response):
    """Trusted operator event, separate from model-selected action history."""
    from .episodes import _json_copy
    validate_clarification(response)
    if episode.state != "awaiting_information" or episode.outcome is not None or episode.handoff is not None:
        raise ValueError("Clarification requires an active, verified information request")
    if episode.revisions >= episode.limits.max_revisions:
        raise ValueError("Clarification revision budget exhausted")
    record = {"after_action": len(episode.actions), "response": _json_copy(response)}
    episode._record("episode.operator_clarification", {**record,
        "scope": "operator_annotation_not_experimental_verification_or_evaluation_feedback"})
    episode.clarifications.append(record)
    episode.revisions += 1
    episode.state = "ready"


def save_handoff(episode, *, driver=None):
    """Checkpoint an information request; does not grant resume authorization."""
    from .episodes import _json_copy
    with episode.lock:
        if episode.state != "awaiting_information" or episode.outcome is not None or episode.handoff is not None:
            raise ValueError("Only an unsuspended information request can be checkpointed")
        episode._check_inputs()
        number = episode.handoff_count + 1
        folder = episode.root / "handoffs" / f"{number:04d}"
        folder.mkdir(mode=0o700, parents=True, exist_ok=False)
        now_wall, now_monotonic = _wall(), time.monotonic()
        broker_state = None
        if episode.broker is not None:
            broker = episode.broker
            receipt = broker.close()
            if receipt.get("capture_complete") is not True or receipt.get("record_format") != "devbench.study.tool-receipts/1":
                raise ValueError("Continuation requires complete, unscoped broker receipts")
            broker_state = {"policy": asdict(broker.policy), "receipt": receipt,
                "directory": broker.output_dir.relative_to(episode.root).as_posix(),
                "evidence_bytes": broker.evidence_bytes,
                "clock": _clock(None if broker.started is None else broker.started+broker.policy.timeout_sec, now_wall, now_monotonic)}
        result_paths = []
        results = []
        for path in sorted((episode.root / "checkpoints").glob("action-*/observation.json")):
            response = _read(path)
            if response.get("status") == "succeeded":
                results.append(response["result"])
                result_paths.append(path.relative_to(episode.root).as_posix())
        if results != episode.results:
            raise ValueError("Saved numerical observations differ from episode history")
        selection_paths = []
        for index, evidence in enumerate(episode.agent_selections, 1):
            path = episode.root / "agent" / f"selection-{index:04d}" / "selection.json"
            if _read(path, 8_000_000).get("evidence") != evidence:
                raise ValueError("Saved selector records differ from episode history")
            selection_paths.append(path.relative_to(episode.root).as_posix())
        controller_clock = _clock(episode.controller_deadline, now_wall, now_monotonic)
        driver = _json_copy(driver)
        if driver is not None:
            driver["deadline_utc"] = controller_clock["deadline_utc"]
        state = {"format": "devbench.analysis.handoff/1", "number": number, "saved_at_utc": now_wall,
            "elapsed_seconds": now_monotonic-episode.created, "config_sha256": episode.config_id,
            "snapshot_id": episode.snapshot_id, "source_sha256": episode.source_id,
            "instance_id": episode.instance_id, "evaluation_plan_sha256": episode.evaluation_plan_sha256,
            "revisions": episode.revisions, "actions": episode.actions, "measurements": episode.measurements,
            "clarifications": episode.clarifications,
            "review_binding_sha256": getattr(episode, "review_binding_sha256", None),
            "result_paths": result_paths, "selection_paths": selection_paths, "agent_policy": episode.agent_policy,
            "broker": broker_state, "driver": driver, "controller_clock": controller_clock}
        atomic_json(folder / "state.json", state)
        inventory = _inventory(episode.root, folder)
        atomic_json(folder / "index.json", inventory)
        for name in inventory["files"]:
            episode.audit.add_artifact(episode.root / name, max_bytes=128_000_000)
        state_ref = episode.audit.add_artifact(folder / "state.json", max_bytes=16_000_000)
        index_ref = episode.audit.add_artifact(folder / "index.json", max_bytes=16_000_000)
        episode._record("episode.handoff", {"number": number, "state": state_ref, "index": index_ref,
            "scope": "pending_information_not_completed_analysis"})
        receipt = episode.audit.checkpoint(folder / "audit", max_bytes=MAX_BYTES, max_entries=MAX_ENTRIES)
        episode.handoff_count, episode.handoff = number, receipt
        # Convenience receipt; the independently retained root remains required.
        atomic_json(folder / "receipt.json", receipt)
        return receipt


def _verified_broker(episode, state):
    from devbench.study.evidence import verify_receipts
    broker_state = state["broker"]
    if broker_state is None:
        return None
    policy, task, aliases = episode._broker_spec()
    if digest(asdict(policy)) != digest(broker_state["policy"]):
        raise ValueError("Saved broker policy differs from the frozen episode")
    previous = _inside(episode.root, broker_state["directory"])
    evidence = verify_receipts(previous, expected_receipt=broker_state["receipt"], expected_input_sha256=episode.snapshot_id,
        expected_implementation_sha256=episode.source_id, allow_evidence_read=True)
    if evidence.execution_scoping or evidence.condition != policy.condition or len(evidence.events) > policy.max_calls:
        raise ValueError("Unsupported or mismatched broker continuation")
    used = broker_state["evidence_bytes"]
    if type(used) is not int or not 0 <= used <= policy.max_evidence_bytes:
        raise ValueError("Invalid cumulative broker byte accounting")
    return policy, task, aliases, previous, evidence, used


def _restore_broker(episode, state, deadline, verified):
    from devbench.study.evidence import _read as read_bytes
    if verified is None:
        return None
    policy, task, aliases, previous, evidence, used = verified
    from devbench.study.biology_broker import BiologyBroker
    target = episode.root / f"tool-evidence-resume-{state['number']:04d}"
    broker = BiologyBroker(policy, aliases, target, public_task=task, allow_data_execution=True, defer_start=True)
    episode.broker = broker  # Preserve partial reconstruction on later failure.
    broker.evidence_bytes = used
    broker.events, broker.results = list(evidence.events), dict(evidence.results)
    names = {"events.jsonl"} if evidence.events else set()
    for event in evidence.events:
        if event["status"] == "succeeded":
            names.add(event["receipt_id"] + ".json")
        if event.get("backend_evidence"):
            names.add(event["backend_evidence"]["file"])
    try:
        for name in sorted(names):
            # Prefix copies are real writes and count toward the same byte ceiling.
            broker._store(name, read_bytes(previous / name, policy.max_evidence_bytes-broker.evidence_bytes))
    except BaseException as error:
        broker.capture_error = "Verified prefix restoration failed: " + type(error).__name__
        raise
    if deadline is not None:
        broker.started = deadline-policy.timeout_sec
    return broker


def resume_handoff(root, checkpoint_manifest, *, expected_root, response=None, abort_reason=None, abort_scientific=False,
                   expected_mode=None, selector_configuration=None, selector_request=None, allow_data_execution=False):
    """Restore a verified original episode; no model or scientific tool is run."""
    if allow_data_execution is not True:
        raise PermissionError("Handoff restoration requires explicit data authorization")
    from .episodes import AnalysisEpisode, EpisodeLimits, _sources, _support
    from .runtime import observe_runtime
    from devharness_core.provenance import ExecutionDAG, verify_execution
    from devbench.study.biology_broker import biology_tool_menu
    if type(abort_scientific) is not bool or (abort_scientific and abort_reason is None):
        raise ValueError("Scientific abort requires an explicit reason")
    if abort_reason is not None and (not isinstance(abort_reason, str) or not abort_reason.strip() or len(abort_reason) > 2000):
        raise ValueError("A bounded abort reason is required")
    if abort_reason is None:
        validate_clarification(response)
    root, checkpoint = Path(root), Path(checkpoint_manifest)
    if any(not path.is_absolute() or path.resolve() != path for path in (root, checkpoint)):
        raise ValueError("Handoff paths must be canonical and absolute")
    if not checkpoint.is_relative_to(root / "handoffs") or checkpoint.name != "manifest.json":
        raise ValueError("Use this episode's handoff manifest")
    report = verify_execution(checkpoint, expected_root)
    if not report["valid"]:
        raise ValueError("Handoff checkpoint failed verification")
    manifest = _read(checkpoint)
    node = _read(checkpoint.parent / "nodes" / (manifest["node_ids"][-1]+".json"))
    if node["kind"] != "episode.handoff":
        raise ValueError("The retained checkpoint is not an information request")
    payload, folder = node["payload"], checkpoint.parent.parent
    if folder != root / "handoffs" / f"{payload['number']:04d}":
        raise ValueError("Handoff generation path mismatch")
    if sha256(folder / "state.json") != payload["state"]["sha256"] or sha256(folder / "index.json") != payload["index"]["sha256"]:
        raise ValueError("Handoff state/index changed")
    state, index = _read(folder / "state.json"), _read(folder / "index.json")
    if _inventory(root, folder) != index:
        raise ValueError("Working handoff inventory changed")
    if state["format"] != "devbench.analysis.handoff/1" or state["number"] != payload["number"]:
        raise ValueError("Invalid handoff state")
    if expected_mode is not None:
        mode = "agent" if state["driver"] is not None else "manual"
        if expected_mode != mode:
            raise ValueError("The handoff's manual/agent mode is frozen")
    if selector_configuration is not None and (state["driver"] is None
            or selector_configuration != state["driver"]["configuration"]):
        raise ValueError("The handoff selector configuration is frozen")
    if selector_request is not None:
        if (not isinstance(selector_request, dict) or set(selector_request) != {"backend", "requested_model"}
                or state["driver"] is None or any(state["driver"]["configuration"].get(key) != value for key, value in selector_request.items())):
            raise ValueError("The handoff selector configuration is frozen")
    config, snapshot, runtime = _read(root / "parameters.json"), _read(root / "snapshot.json"), _read(root / "runtime.json")
    if digest(config) != state["config_sha256"] or digest(snapshot) != state["snapshot_id"] or digest(_sources()) != state["source_sha256"]:
        raise ValueError("Episode configuration, snapshot or implementation changed")
    observed = observe_runtime(config["execution_tier"], detect_gpu=False)
    if any(runtime["runtime"][key] != observed["runtime"][key] for key in ("python", "platform", "all_package_versions")):
        raise ValueError("Episode runtime changed; use a newly reviewed analysis")
    now_wall, now_mono = _wall(), time.monotonic()
    saved_at = state["saved_at_utc"]
    if not isinstance(saved_at, (int, float)) or not math.isfinite(saved_at) or now_wall < saved_at:
        raise ValueError("Invalid or reversed handoff wall clock")
    controller_remaining = _remaining(state["controller_clock"], saved_at, now_wall)
    broker_remaining = _remaining(state["broker"]["clock"], saved_at, now_wall) if state["broker"] else None
    episode = AnalysisEpisode()
    episode.root, episode.limits = root, EpisodeLimits(**config["limits"])
    episode.config, episode.config_id = config, state["config_sha256"]
    episode.snapshot, episode.snapshot_id, episode.source_id = snapshot, state["snapshot_id"], state["source_sha256"]
    episode.input_paths = {key: _inside(root, value["path"]) for key, value in snapshot.items()}
    episode.runtime = runtime
    episode.created = now_mono-(state["elapsed_seconds"]+now_wall-saved_at)
    episode.finished, episode.outcome, episode.receipt = None, None, None
    episode.state, episode.revisions = "awaiting_information", state["revisions"]
    episode.actions, episode.measurements = state["actions"], state["measurements"]
    episode.clarifications = state["clarifications"]
    episode.results = [_read(_inside(root, path))["result"] for path in state["result_paths"]]
    episode.last_result = episode.results[-1] if episode.results else None
    episode.agent_selections = [_read(_inside(root, path), 8_000_000)["evidence"] for path in state["selection_paths"]]
    episode.agent_policy = state["agent_policy"]
    episode.instance_id, episode.evaluation_plan_sha256 = state["instance_id"], state["evaluation_plan_sha256"]
    if episode.instance_id != manifest["run_id"]:
        raise ValueError("Episode and ledger identities differ")
    episode.supported = _support(config["assay"], config["input_kind"], config["public_task"])
    episode.menu = biology_tool_menu(config["public_task"]) if episode.supported else {}
    episode.lock = threading.RLock()
    episode.started, episode.broker = None, None
    episode.handoff, episode.handoff_count = None, state["number"]
    episode.retained_broker_receipt = state["broker"]["receipt"] if state["broker"] else None
    episode.retained_broker_directory = state["broker"]["directory"] if state["broker"] else None
    episode.review_binding_sha256 = state.get("review_binding_sha256")
    episode.controller_deadline = None if controller_remaining is None else now_mono+controller_remaining
    episode.resume_driver = state["driver"]
    episode.checkpoints = CheckpointStore(root / "checkpoints", {"config": episode.config_id,
        "snapshot": episode.snapshot_id, "source": episode.source_id,
        "python": runtime["runtime"]["python"], "packages": runtime["runtime"]["all_package_versions"]})
    episode._check_inputs()
    verified_broker = _verified_broker(episode, state)
    episode.audit = ExecutionDAG.resume(root / "audit", checkpoint, expected_root=expected_root)
    episode.last_node = episode.audit._node_ids[-1]
    try:
        atomic_json(folder / "resume-runtime.json", observed)
        episode._record("episode.restored", {"handoff_root": expected_root, "generation": state["number"],
            "clock_scope": "original_wall_deadlines_not_restarted", "model_execution_authorized": False})
        if abort_reason is not None:
            episode.abort(abort_reason, scientific=abort_scientific)
            return episode
        if controller_remaining == 0 or broker_remaining == 0:
            episode.abort("handoff_deadline_exhausted")
            return episode
        if episode.revisions >= episode.limits.max_revisions:
            episode.abort("revision_budget_exhausted", scientific=True)
            return episode
        episode.broker = _restore_broker(episode, state, None if broker_remaining is None else now_mono+broker_remaining, verified_broker)
        episode.started = episode.broker.started if episode.broker else None
        if ((episode.controller_deadline is not None and time.monotonic() >= episode.controller_deadline)
                or (episode.started is not None and time.monotonic() >= episode.started+episode.limits.timeout_seconds)):
            episode.abort("handoff_deadline_exhausted")
            return episode
        apply_clarification(episode, response)
        if episode.resume_driver is not None:
            episode.resume_driver["history"].append({"operator_message": response["message"],
                "scope": "clarification_only_inputs_and_settings_unchanged"})
        return episode
    except BaseException as error:
        atomic_json(folder / "restore-failure.json", {"error_type": type(error).__name__, "message": str(error)[:2000]})
        if episode.outcome is None:
            episode.abort("handoff_restore_failed:" + type(error).__name__)
        if not isinstance(error, Exception):
            raise
        return episode
