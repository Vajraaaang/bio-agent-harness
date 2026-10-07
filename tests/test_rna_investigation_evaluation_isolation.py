"""The fixture interpreter has explicit read grants and no arbitrary callback."""
import json
from pathlib import Path

import pytest

from tests.test_rna_investigation_evaluation import api, storage, prepare


@pytest.mark.parametrize("target", ["evaluator", "parent", "sibling", "ancestor"])
def test_participant_reader_denies_other_roles_before_opening_a_canary(storage, target):
    public = storage / "participant"; public.mkdir()
    fact = public / "facts.json"; fact.write_text('{"data":"public"}')
    protected = storage / target; protected.mkdir()
    canary = protected / "canary.json"; canary.write_text('{"secret":"do-not-read"}')
    reader = api().ParticipantReader([fact], denied_roots=[protected])
    assert reader.read_json(fact) == {"data": "public"}
    with pytest.raises(PermissionError):
        reader.read_json(canary)
    with pytest.raises(PermissionError):
        reader.read_json(storage / "participant" / ".." / target / "canary.json")


def test_symlink_is_not_a_read_grant(storage):
    secret = storage / "secret.json"; secret.write_text('{"secret":true}')
    alias = storage / "public.json"; alias.symlink_to(secret)
    with pytest.raises(ValueError, match="symlink|regular"):
        api().ParticipantReader([alias], denied_roots=[])


def test_offline_campaign_refuses_executable_participants(storage):
    prepared = prepare(storage)
    called = []
    with pytest.raises(TypeError, match="OfflineParticipant|declarative"):
        api().run_offline_campaign(prepared, lambda request: called.append(request))
    assert not called


def test_fixture_read_injection_is_retained_without_truth_leakage(storage):
    prepared = prepare(storage)
    secret = prepared.root / "evaluator" / "canary.json"
    secret.write_text('{"secret":"evaluation-canary"}')
    fixture = api().OfflineParticipant({"case0:conditional_1:investigator_1": {"read_requests": [str(secret)]}})
    seal = api().run_offline_campaign(prepared, fixture)
    attempt = next(item for item in api().verified_attempts(prepared, seal)
                   if item["case_id"] == "case0" and item["arm"] == "conditional_1")
    assert attempt["status"] == "blocked"
    assert attempt["calls"][-1]["error_type"] == "PermissionError"
    participant_files = list((prepared.root / "participants").rglob("*.json"))
    assert participant_files
    assert all("evaluation-canary" not in path.read_text() and "fixture-reference-canary" not in path.read_text()
               for path in participant_files)


def test_native_campaign_requires_new_explicit_admission_before_access(storage):
    with pytest.raises(PermissionError, match="admission|authorized"):
        api().run_native_campaign(None, admission=None, actions_factory=None, backend=None)


def test_offline_fixture_does_not_accept_an_executable_instance_hook():
    participant = api().OfflineParticipant()
    with pytest.raises(AttributeError):
        participant._respond = lambda *args: {"status": "completed"}
