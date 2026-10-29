"""Offline authorization controls; selector spies never start model processes."""
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_analysis_episodes import create, workspace


def authority_api():
    assert importlib.util.find_spec("devbench.analysis.model_authority") is not None, "Durable model-authority reader is missing"
    from devbench.analysis.model_authority import read_model_authority, reservation_binding
    return read_model_authority, reservation_binding


def reservation(path, *, authorized=False, backend="remote_spy"):
    _, binding = authority_api()
    configuration = {"backend": backend}
    attempt = {"sequence": 1, "context": "synthetic fixture"}
    record = {"model_execution_authorized": authorized, **binding(configuration, attempt)}
    path.write_text(json.dumps(record))
    return configuration, attempt, record


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true"])
def test_durable_authority_is_an_exact_boolean(tmp_path, value):
    read, _ = authority_api()
    path = tmp_path.resolve() / "reservation.json"
    configuration, attempt, _ = reservation(path, authorized=value)
    with pytest.raises(PermissionError, match="authority"):
        read(path, configuration=configuration, attempt=attempt, maximum_authority=True)


def test_durable_false_blocks_unknown_selector_and_self_match_cannot_grant(tmp_path):
    read, _ = authority_api()
    path = tmp_path.resolve() / "reservation.json"
    configuration, attempt, record = reservation(path)
    record.update(numerical_replay="pass", numerical_reference="same_implementation_self_match")
    path.write_text(json.dumps(record))
    with pytest.raises(PermissionError, match="authorization"):
        read(path, configuration=configuration, attempt=attempt, maximum_authority=True)
    assert json.loads(path.read_text())["model_execution_authorized"] is False


@pytest.mark.parametrize("defect", ["missing", "malformed", "symlink", "selector", "attempt", "duplicate", "oversized"])
def test_invalid_reservation_cannot_authorize_a_selector(tmp_path, defect):
    read, _ = authority_api()
    path = tmp_path.resolve() / "reservation.json"
    configuration, attempt, record = reservation(path, authorized=True)
    if defect == "missing":
        path.unlink()
    elif defect == "malformed":
        path.write_text("{")
    elif defect == "symlink":
        retained = path.with_name("retained.json")
        path.rename(retained)
        path.symlink_to(retained)
    elif defect == "selector":
        configuration = {"backend": "different_selector"}
    elif defect == "attempt":
        attempt = {"sequence": 2, "context": "synthetic fixture"}
    elif defect == "duplicate":
        path.write_text('{"model_execution_authorized": false, "model_execution_authorized": true}')
    else:
        path.write_text(json.dumps({**record, "padding": "x" * 65_536}))
    with pytest.raises(PermissionError, match="authority"):
        read(path, configuration=configuration, attempt=attempt, maximum_authority=True)


def test_reservation_cannot_escalate_current_permission(tmp_path):
    read, _ = authority_api()
    path = tmp_path.resolve() / "reservation.json"
    configuration, attempt, _ = reservation(path, authorized=True)
    with pytest.raises(PermissionError, match="authorization"):
        read(path, configuration=configuration, attempt=attempt, maximum_authority=False)


def test_explicit_offline_selector_has_no_model_authority(tmp_path):
    read, _ = authority_api()
    path = tmp_path.resolve() / "reservation.json"
    configuration, attempt, _ = reservation(path, backend="offline_scripted_fixture")
    assert read(path, configuration=configuration, attempt=attempt, maximum_authority=False) is False


def test_bound_explicit_permission_is_read_and_returned(tmp_path):
    read, _ = authority_api()
    path = tmp_path.resolve() / "reservation.json"
    configuration, attempt, _ = reservation(path, authorized=True)
    assert read(path, configuration=configuration, attempt=attempt, maximum_authority=True) is True


class UnknownSelector:
    def __init__(self):
        self.calls = []

    def configuration(self):
        return {"backend": "unknown_remote_selector"}

    def select(self, context, output, **kwargs):
        from devbench.analysis.episode_agent import Selection
        self.calls.append(kwargs)
        return Selection({"kind": "request_information", "message": "Offline spy reached"},
                         {"model_sessions_attempted": 0, "tokens": None, "actual_model": None, "cost_usd": None})


def test_episode_unknown_selector_is_blocked_before_entry(workspace):
    from devbench.analysis.episode_agent import drive_episode
    episode, _ = create(workspace)
    selector = UnknownSelector()
    result = drive_episode(episode, selector, allow_data_execution=True)
    assert selector.calls == []
    assert result["agent_execution"]["model_sessions_attempted"] == 0
    failure = json.loads((episode.root / "agent/selection-0001/failure.json").read_text())
    assert failure["selector_entered"] is False


def test_episode_reservation_is_bound_to_the_absolute_attempt_path(workspace):
    from devbench.analysis.episode_agent import drive_episode
    from devbench.analysis.model_authority import reservation_binding
    episode, _ = create(workspace)
    selector = UnknownSelector()
    drive_episode(episode, selector, allow_data_execution=True)
    output = episode.root / "agent/selection-0001"
    record = json.loads((output / "reservation.json").read_text())
    attempt = {"episode_config": episode.config_id, "selection": 1,
        "context": json.loads((output / "context.json").read_text()), "attempt_path": str(output)}
    expected = reservation_binding(selector.configuration(), attempt)
    assert record["attempt_sha256"] == expected["attempt_sha256"]


@pytest.mark.parametrize("route", ["episode", "proposal", "review"])
def test_every_selector_route_reads_revoked_reservation(workspace, monkeypatch, route):
    import devbench.analysis.episode_agent as episode_module
    import devbench.analysis.episode_planning as proposal_module
    import devbench.analysis.scientific_review_agent as review_module
    module = {"episode": episode_module, "proposal": proposal_module, "review": review_module}[route]
    original_write = module.atomic_json

    def revoke(path, value):
        if Path(path).name in {"reservation.json", "selection-reservation.json"}:
            value = {**value, "model_execution_authorized": False}
        return original_write(path, value)

    monkeypatch.setattr(module, "atomic_json", revoke)
    selector = UnknownSelector()
    if route == "episode":
        episode, _ = create(workspace)
        result = module.drive_episode(episode, selector, allow_data_execution=True, allow_model_execution=True)
        sessions = result["agent_execution"]["model_sessions_attempted"]
    elif route == "proposal":
        from tests.test_episode_planning import dataset
        result = module.propose_analysis(workspace / "proposal", question="B minus A", dataset=dataset(workspace),
            selector=selector, allow_data_read=True, allow_model_execution=True, model_data_scope="synthetic")
        sessions = result["model_sessions_attempted"]
    else:
        from tests.test_scientific_review_agent import packet, request
        receipt, _ = packet(workspace)
        result = request(workspace, receipt, selector, allow_model_execution=True, model_data_scope="synthetic")
        sessions = result["model_sessions_attempted"]
    assert selector.calls == []
    assert sessions == 0
    assert result["reason"].lower().find("permission") >= 0 or result.get("error_type") == "PermissionError"


@pytest.mark.parametrize("route", ["episode", "proposal", "review"])
def test_deadline_expiry_after_authority_read_does_not_claim_selector_entry(workspace, monkeypatch, route):
    import devbench.analysis.episode_agent as episode_module
    import devbench.analysis.episode_planning as proposal_module
    import devbench.analysis.scientific_review_agent as review_module
    module = {"episode": episode_module, "proposal": proposal_module, "review": review_module}[route]
    original_read = module.read_model_authority
    original_clock = module.time.monotonic

    def expire_after_read(*args, **kwargs):
        authority = original_read(*args, **kwargs)
        monkeypatch.setattr(module.time, "monotonic", lambda: original_clock() + 10_000)
        return authority

    monkeypatch.setattr(module, "read_model_authority", expire_after_read)
    selector = UnknownSelector()
    if route == "episode":
        episode, _ = create(workspace)
        result = module.drive_episode(episode, selector, allow_data_execution=True, allow_model_execution=True)
        sessions = result["agent_execution"]["model_sessions_attempted"]
        assert selector.calls == []
        entered = json.loads((episode.root / "agent/selection-0001/failure.json").read_text())["selector_entered"]
    elif route == "proposal":
        from tests.test_episode_planning import dataset
        result = module.propose_analysis(workspace / "proposal", question="B minus A", dataset=dataset(workspace),
            selector=selector, allow_data_read=True, allow_model_execution=True, model_data_scope="synthetic")
        sessions, entered = result["model_sessions_attempted"], result["selector_entered"]
    else:
        from tests.test_scientific_review_agent import packet, request
        receipt, _ = packet(workspace)
        result = request(workspace, receipt, selector, allow_model_execution=True, model_data_scope="synthetic")
        sessions, entered = result["model_sessions_attempted"], result["selector_entered"]
    assert selector.calls == []
    assert entered is False
    assert sessions == 0
