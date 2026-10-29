"""Bounded, trusted orchestration of existing episode actions.

Selectors receive public observations only. They never receive the episode object
or its broker. This module is not an OS sandbox or a protected evaluator.
"""
from dataclasses import asdict, dataclass
import json
import math
import time

from .checkpoints import atomic_json
from .episodes import _json_copy, _public_only
from .model_authority import read_model_authority, reservation_binding


@dataclass(frozen=True)
class AgentLimits:
    max_selections: int = 8
    timeout_seconds: float = 600
    session_timeout_seconds: float = 90
    memory_mb: int = 2048
    max_context_bytes: int = 128_000

    def __post_init__(self):
        for name, low, high in (("max_selections", 1, 32), ("memory_mb", 128, 4096),
                                ("max_context_bytes", 1024, 1_000_000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} exceeds selector policy")
        for name, maximum in (("timeout_seconds", 3600), ("session_timeout_seconds", 300)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= maximum):
                raise ValueError(f"{name} exceeds selector policy")


@dataclass(frozen=True)
class Selection:
    action: dict
    evidence: dict


class InvalidSelection(ValueError):
    """The provider completed but did not supply an admissible action envelope."""


def selector_configuration(selector):
    method = getattr(selector, "configuration", None)
    value = method() if callable(method) else {"backend": "trusted_custom_selector",
        "type_name": type(selector).__module__ + "." + type(selector).__qualname__}
    return _json_copy(value)


def drive_episode(episode, selector, *, limits=None, allow_data_execution=False,
                  allow_model_execution=False):
    """Run one fresh episode; return immediately on a request for information.

    ``selector`` is trusted coordinator code, never supplied by a participant.
    A live selector receives authority read from its persisted reservation.
    A returned information request requires the operator, not an automatic retry.
    This entry point deliberately cannot reopen a used selection budget.
    """
    if allow_data_execution is not True:
        raise PermissionError("Episode action driving requires data-execution authorization")
    resumed = episode.resume_driver if episode is not None else None
    limits = limits or (AgentLimits(**episode.agent_policy) if resumed is not None else AgentLimits())
    if not isinstance(limits, AgentLimits):
        raise TypeError("Use AgentLimits for selector resource settings")
    configuration = selector_configuration(selector)
    root = episode.root / "agent"
    if resumed is None:
        if episode.state != "ready" or episode.actions or root.exists():
            raise ValueError("The action driver requires a fresh episode; budgets cannot be restarted")
        episode.register_agent(asdict(limits))
        root.mkdir(mode=0o700)
        atomic_json(root / "policy.json", asdict(limits))
        atomic_json(root / "selector.json", configuration)
        episode.controller_deadline = time.monotonic() + limits.timeout_seconds
        history = []
    else:
        if asdict(limits) != episode.agent_policy or configuration != resumed["configuration"]:
            raise ValueError("The resumed selector configuration and budgets are frozen")
        if episode.state != "ready" or episode.outcome is not None:
            raise ValueError("The restored episode is not ready for continuation")
        history = _json_copy(resumed["history"])
        episode.resume_driver = None
    deadline = episode.controller_deadline
    for number in range(len(episode.agent_selections)+1, limits.max_selections + 1):
        if len(episode.actions) >= episode.limits.max_steps:
            return episode.abort("action_budget_exhausted", scientific=True)
        if time.monotonic() >= deadline:
            return episode.abort("selector_deadline_exhausted")
        try:
            episode._check_inputs()
        except (OSError, ValueError) as error:
            return episode.abort("episode_inputs_changed:" + type(error).__name__)
        context = {"observation": episode.observation(), "history": _json_copy(history),
                   "action_kinds": ["tool", "finish", "refuse", "revise", "request_information"],
                   "selection_budget": {"decisions_remaining": limits.max_selections-number+1,
                       "seconds_remaining": max(0, deadline-time.monotonic()),
                       "per_selection_seconds": limits.session_timeout_seconds, "memory_mb": limits.memory_mb}}
        try:
            _public_only(context)
            encoded = json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode()) > limits.max_context_bytes:
                return episode.abort("selector_context_budget_exhausted_no_dispatch")
        except (TypeError, ValueError):
            return episode.abort("selector_context_not_public_or_finite")
        output = root / f"selection-{number:04d}"
        selector_entered = False
        try:
            output.mkdir(mode=0o700)
            atomic_json(output / "context.json", context)
            # Reservation persists before any selector or permission-preflight call.
            attempt = {"episode_config": episode.config_id, "selection": number, "context": context,
                "attempt_path": str(output)}
            atomic_json(output / "reservation.json", {"selection": number,
                "max_provider_sessions": 2, "model_execution_authorized": allow_model_execution is True,
                "scope": "one_permission_preflight_and_one_action_session",
                **reservation_binding(configuration, attempt)})
            model_authority = read_model_authority(output / "reservation.json",
                configuration=configuration, attempt=attempt, maximum_authority=allow_model_execution)
            selection_timeout = min(limits.session_timeout_seconds, deadline-time.monotonic())
            if selection_timeout <= 0:
                raise TimeoutError("selector_deadline_exhausted_before_dispatch")
            selector_entered = True
            choice = selector.select(_json_copy(context), output,
                timeout_seconds=selection_timeout,
                memory_mb=limits.memory_mb,
                allow_model_execution=model_authority)
            if not isinstance(choice, Selection):
                raise InvalidSelection("Selector must return a Selection")
            atomic_json(output / "selection.json", {"action": choice.action, "evidence": choice.evidence})
            episode.record_agent_selection(choice.evidence, output / "selection.json")
            if not isinstance(choice.action, dict):
                raise InvalidSelection("Selected action is not a JSON object")
            _public_only(choice.action)
            if time.monotonic() >= deadline:
                return episode.abort("selector_deadline_exhausted")
        except BaseException as error:
            failure_path = (output / "failure.json" if output.is_dir() and not output.is_symlink()
                            else root / f"selection-{number:04d}-failure.json")
            atomic_json(failure_path, {"error_type": type(error).__name__, "message": str(error)[:2000],
                "selector_entered": selector_entered})
            if len(episode.agent_selections) < number:
                evidence_path = output / "evidence.json"
                evidence = {
                    "backend": "unknown_selector", "model_sessions_attempted": None if selector_entered else 0,
                    "actual_model": None, "tokens": None, "cost_usd": None}
                if evidence_path.exists():
                    try:
                        from devbench_engine.adapters import _read_answer
                        evidence = _read_answer(output, "evidence.json", max_bytes=4_000_000)
                    except (OSError, TypeError, ValueError):
                        evidence["evidence_capture_error"] = "invalid_or_unreadable_session_record"
                try:
                    episode.record_agent_selection(evidence, failure_path)
                except (TypeError, ValueError):
                    episode.record_agent_selection({"backend": "unknown_selector", "model_sessions_attempted": None,
                        "actual_model": None, "tokens": None, "cost_usd": None,
                        "evidence_capture_error": "invalid_session_accounting"}, failure_path)
            result = episode.abort("invalid_selector_response" if isinstance(error, InvalidSelection)
                                   else "selector_failed:" + type(error).__name__,
                                   scientific=isinstance(error, InvalidSelection))
            if not isinstance(error, Exception):
                raise
            return result
        response = episode.step(choice.action, deadline=deadline)
        if episode.outcome is not None:
            return response  # The episode is sealed: no further artifact writes.
        history.append({"action": _json_copy(choice.action), "observation": _json_copy(response)})
        if response["state"] == "awaiting_information":
            try:
                atomic_json(root / "awaiting-information.json", response)
                from .episode_handoff import save_handoff
                receipt = save_handoff(episode, driver={"history": history, "configuration": configuration})
                return {**response, "handoff": receipt}
            except BaseException as error:
                if episode.handoff is None:
                    atomic_json(root / f"handoff-failure-{number:04d}.json", {
                        "error_type": type(error).__name__, "message": str(error)[:2000]})
                result = episode.abort(("handoff_capture_failed:" + type(error).__name__ + ": " + str(error))[:2000])
                if not isinstance(error, Exception):
                    raise
                return result
    return episode.abort("selector_decision_budget_exhausted")
