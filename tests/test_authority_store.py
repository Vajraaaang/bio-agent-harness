"""Durable configured authority; fixtures do not establish human administration."""
from datetime import timedelta
import importlib
import json
import os
from pathlib import Path

import pytest

from tests.test_authority_contracts import bundle, changed, signed, NOW
from tests.test_governed_native_contracts import governed


def api():
    path=Path(__file__).resolve().parents[1]/"devharness_controlled/authority_store.py"
    assert path.is_file(),"Authority store implementation is missing"
    return importlib.import_module("devharness_controlled.authority_store")


def opened(tmp_path,bundle):
    store=api().AuthorityStore.create(tmp_path.resolve()/"authority",bundle.policy.authority_id,now=NOW-timedelta(hours=2))
    bundle.administrator=changed(bundle.administrator,registry_id=store.registry_id)
    bundle.policy=changed(bundle.policy,registry_id=store.registry_id)
    bundle.envelope=signed(bundle.policy,bundle.administrator_key)
    first=store.publish(bundle.envelope,[bundle.administrator],expected_tip=None,now=NOW)
    return store,first


def revoke(store,first,bundle):
    policy=changed(bundle.policy,revision=2,previous_sha256=first["tip_sha256"],issued_at=NOW+timedelta(seconds=1),
                   grants=(),change_reason="Revoke all fixture grants")
    return store.publish(signed(policy,bundle.administrator_key),[bundle.administrator],
                         expected_tip=first["tip_sha256"],now=NOW+timedelta(seconds=2))


def test_signed_history_is_retained_and_checkpoint_detects_rollback(tmp_path,bundle):
    store,first=opened(tmp_path,bundle);before=store.capture();old_database=store.database_path.read_bytes();second=revoke(store,first,bundle)
    snapshot=store.capture();checkpoint=store.checkpoint([bundle.administrator])
    verdict=api().verify_authority_snapshot(snapshot,[bundle.administrator],expected_checkpoint=checkpoint)
    assert verdict["valid"] and verdict["policy_signatures_verified"]==2
    assert snapshot["records"][0]==before["records"][0]
    assert second["tip_sha256"]!=first["tip_sha256"]
    store.database_path.write_bytes(old_database)
    with pytest.raises(ValueError,match="checkpoint|rollback"):
        api().verify_authority_snapshot(store.capture(),[bundle.administrator],expected_checkpoint=checkpoint)


def test_wrong_signature_or_predecessor_never_commits(tmp_path,bundle):
    store,first=opened(tmp_path,bundle)
    bad=changed(bundle.policy,revision=2,previous_sha256="0"*64,issued_at=NOW+timedelta(seconds=1))
    with pytest.raises(ValueError,match="predecessor|tip"):
        store.publish(signed(bad,bundle.administrator_key),[bundle.administrator],expected_tip=first["tip_sha256"],now=NOW+timedelta(seconds=2))
    assert len(store.capture()["records"])==1
    with pytest.raises(ValueError,match="signature"):
        store.publish(changed(bundle.envelope,signature="A"*86+"=="),[bundle.administrator],expected_tip=first["tip_sha256"],now=NOW)
    assert len(store.capture()["records"])==1


def test_policy_change_invalidates_queued_plan_without_old_policy_fallback(tmp_path,bundle):
    store,first=opened(tmp_path,bundle)
    plan=governed(authority_id=bundle.policy.authority_id,authority_tip_sha256=first["tip_sha256"])
    with store.launch_guard(plan,bundle.signers,[bundle.administrator],now=NOW) as proof:
        assert proof["decision"]["authority_tip_sha256"]==first["tip_sha256"]
        assert len(proof["decision"]["grants"])==2
    revoke(store,first,bundle)
    with pytest.raises(ValueError,match="tip|changed"):
        with store.launch_guard(plan,bundle.signers,[bundle.administrator],now=NOW+timedelta(seconds=3)):pass


def test_publication_cannot_race_a_held_launch_guard(tmp_path,bundle):
    store,first=opened(tmp_path,bundle)
    plan=governed(authority_id=bundle.policy.authority_id,authority_tip_sha256=first["tip_sha256"])
    with store.launch_guard(plan,bundle.signers,[bundle.administrator],now=NOW):
        with pytest.raises(api().AuthorityBusyError):revoke(store,first,bundle)
    revoke(store,first,bundle)
    assert len(store.capture()["records"])==2


def test_historical_policy_receipt_does_not_authorize_an_expired_launch(tmp_path,bundle):
    store,first=opened(tmp_path,bundle)
    assert api().verify_authority_snapshot(store.capture(),[bundle.administrator])["valid"]
    plan=governed(authority_id=bundle.policy.authority_id,authority_tip_sha256=first["tip_sha256"])
    with pytest.raises(ValueError,match="expired|active"):
        with store.launch_guard(plan,bundle.signers,[bundle.administrator],now=bundle.policy.expires_at):pass


@pytest.mark.parametrize("change",["symlink","hardlink","public_database","public_directory","unexpected_name"])
def test_unsafe_or_ambiguous_store_objects_are_refused(tmp_path,bundle,change):
    store,_=opened(tmp_path,bundle);path=store.database_path
    if change=="symlink":
        saved=tmp_path/"saved.json";path.rename(saved);path.symlink_to(saved)
    elif change=="hardlink":os.link(path,tmp_path/"alias.json")
    elif change=="public_database":path.chmod(0o644)
    elif change=="public_directory":store.root.chmod(0o755)
    else:(store.root/"unordered.json").write_text("{}")
    with pytest.raises(ValueError):store.capture()


def test_genesis_policy_cannot_be_replayed_into_another_registry(tmp_path,bundle):
    store,first=opened(tmp_path,bundle)
    other=api().AuthorityStore.create(tmp_path.resolve()/"other",bundle.policy.authority_id,now=NOW-timedelta(hours=2))
    assert other.registry_id!=store.registry_id
    with pytest.raises(ValueError,match="registry"):
        other.publish(bundle.envelope,[bundle.administrator],expected_tip=None,now=NOW)
    assert other.capture()["records"]==[]


def test_empty_history_still_validates_explicit_administrator_trust(tmp_path,bundle):
    store=api().AuthorityStore.create(tmp_path.resolve()/"authority",bundle.policy.authority_id,now=NOW)
    for roots in ([],[{}],[bundle.administrator]):
        with pytest.raises(ValueError):api().verify_authority_snapshot(store.capture(),roots)


def test_append_only_triggers_and_schema_checks_protect_normal_store_api(tmp_path,bundle):
    import sqlite3
    store,_=opened(tmp_path,bundle)
    with sqlite3.connect(store.database_path) as connection:
        with pytest.raises(sqlite3.DatabaseError,match="append-only"):
            connection.execute("DELETE FROM policies")
        connection.execute("DROP TRIGGER policies_no_update")
    with pytest.raises(ValueError,match="schema|triggers"):store.capture()


@pytest.mark.parametrize("selection",["empty","one","duplicate"])
def test_plan_authorization_requires_the_complete_distinct_approval_pair(tmp_path,bundle,selection):
    store,first=opened(tmp_path,bundle)
    plan=governed(authority_id=bundle.policy.authority_id,authority_tip_sha256=first["tip_sha256"])
    signers=[] if selection=="empty" else [bundle.signers[0]] if selection=="one" else [bundle.signers[0]]*2
    with pytest.raises(ValueError,match="two|pair|distinct"):
        with store.launch_guard(plan,signers,[bundle.administrator],now=NOW):pass
