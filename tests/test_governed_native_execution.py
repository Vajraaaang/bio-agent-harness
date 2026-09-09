"""Governed admission checks; no numerical result doubles or clinical approvals."""
import base64
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devharness_controlled.authority_contracts import AuthorityAdministrator,AuthorityGrant,AuthorityPolicy,SignedAuthorityPolicy
from devharness_controlled.authority_store import AuthorityStore
from devharness_controlled.native_contracts import GovernedNativePlan
from devharness_controlled.trust import Approval,ApprovalPayload,TrustedSigner
from devharness_controlled.native_executor import execute_native_plan
from devharness_controlled.verification import verify_controlled_execution
from tests.test_native_execution import plan as base_plan


def fixture(tmp_path):
    now=datetime.now(timezone.utc);store=AuthorityStore.create(tmp_path.resolve()/"authority","test-authority")
    admin_key=Ed25519PrivateKey.generate()
    pem=lambda key:key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    admin=AuthorityAdministrator(authority_id="test-authority",registry_id=store.registry_id,key_id="administrator",subject_id="fixture-administrator",
        public_key_pem=pem(admin_key),valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=2))
    keys={role:Ed25519PrivateKey.generate() for role in ("author","reviewer")}
    signers=[TrustedSigner(key_id=role,subject_id="fixture-"+role,role=role,public_key_pem=pem(key)) for role,key in keys.items()]
    policy=AuthorityPolicy(authority_id="test-authority",registry_id=store.registry_id,administrator_key_id=admin.key_id,revision=1,previous_sha256=None,
        issued_at=now-timedelta(seconds=1),expires_at=now+timedelta(days=1),change_reason="Synthetic admission fixture",
        grants=tuple(AuthorityGrant(grant_id=s.role,signer=s,study_id="test-fixture",enrollment_ref="synthetic-only",
            valid_from=now-timedelta(seconds=1),valid_until=now+timedelta(hours=1)) for s in signers))
    sign=lambda p:SignedAuthorityPolicy(policy=p,signature=base64.b64encode(admin_key.sign(p.canonical_bytes())).decode())
    checkpoint=store.publish(sign(policy),[admin],expected_tip=None)
    plan=GovernedNativePlan(**{**base_plan().model_dump(),"schema_version":"controlled_native_analysis/3",
        "authority_id":"test-authority","authority_tip_sha256":checkpoint["tip_sha256"]})
    approvals=[]
    for s in signers:
        payload=ApprovalPayload(plan_sha256=plan.sha256(),plan_id=plan.plan_id,revision=plan.revision,study_id=plan.study_id,
            key_id=s.key_id,signer_id=s.subject_id,meaning=s.role,issued_at=now-timedelta(seconds=1),expires_at=now+timedelta(hours=1))
        approvals.append(Approval(payload=payload,signature=base64.b64encode(keys[s.role].sign(payload.canonical_bytes())).decode()))
    return SimpleNamespace(store=store,admin=admin,plan=plan,approvals=approvals,signers=signers,policy=policy,checkpoint=checkpoint,sign=sign,keys=keys)


def run(tmp_path,case,**config):
    absent=tmp_path.resolve()/"absent.csv"
    return execute_native_plan(case.plan,case.approvals,case.signers,counts_path=absent,metadata_path=absent,
        full_design_path=absent,reduced_design_path=absent,output_dir=tmp_path.resolve()/"execution",**config)


def test_missing_governance_configuration_refuses_before_docker(tmp_path,monkeypatch):
    import devharness_controlled.native_executor as executor
    case=fixture(tmp_path)
    monkeypatch.setattr(executor,"LocalDocker",lambda:pytest.fail("Missing authority cannot reach Docker"))
    result=run(tmp_path,case)
    assert result["status"]=="failed" and "authority" in result["error"]
    assert not (tmp_path/"execution/inputs").exists()


def test_valid_governance_reaches_native_boundary_and_requires_external_review_trust(tmp_path,monkeypatch):
    import devharness_controlled.native_executor as executor
    case=fixture(tmp_path)
    def boundary():raise RuntimeError("Synthetic native boundary reached; no model executed")
    monkeypatch.setattr(executor,"LocalDocker",boundary)
    result=run(tmp_path,case,authority_store_dir=case.store.root,authority_administrators=[case.admin],authority_checkpoint=case.checkpoint)
    assert result["status"]=="failed" and "boundary reached" in result["error"]
    assert result["controlled_verification"]["valid"]
    manifest=tmp_path.resolve()/"execution/audit/manifest.json";root=result["receipt"]["root_hash"]
    assert not verify_controlled_execution(manifest,root,case.signers)["valid"]
    assert verify_controlled_execution(manifest,root,case.signers,authority_administrators=[case.admin])["valid"]


def test_changed_policy_is_not_replaced_by_the_old_checkpoint(tmp_path,monkeypatch):
    import devharness_controlled.native_executor as executor
    case=fixture(tmp_path)
    revoked=AuthorityPolicy(**{**case.policy.model_dump(),"revision":2,"previous_sha256":case.checkpoint["tip_sha256"],
                              "issued_at":datetime.now(timezone.utc),"grants":(),"change_reason":"Revoke fixture grants"})
    case.store.publish(case.sign(revoked),[case.admin],expected_tip=case.checkpoint["tip_sha256"])
    monkeypatch.setattr(executor,"LocalDocker",lambda:pytest.fail("Stale authority cannot reach Docker"))
    result=run(tmp_path,case,authority_store_dir=case.store.root,authority_administrators=[case.admin],authority_checkpoint=case.checkpoint)
    assert result["status"]=="failed" and "tip changed" in result["error"]
    assert not (tmp_path/"execution/inputs").exists()


def test_governed_recovery_adopts_sealed_failure_without_analysis_retry(tmp_path,monkeypatch):
    import devharness_controlled.native_executor as executor
    from devharness_controlled.operated_native import submit_native_plan
    from devharness_controlled.native_recovery import recover_native_attempt
    from devharness_controlled.run_registry import RunRegistry
    case=fixture(tmp_path); absent=tmp_path.resolve()/"absent.csv"
    def boundary(): raise RuntimeError("Synthetic stop before native execution")
    monkeypatch.setattr(executor,"LocalDocker",boundary)
    append=RunRegistry.append_event
    def lost_commit(self,attempt_id,kind,*args,**kwargs):
        if kind=="failed": raise OSError("Synthetic lost terminal commit")
        return append(self,attempt_id,kind,*args,**kwargs)
    monkeypatch.setattr(RunRegistry,"append_event",lost_commit)
    result=submit_native_plan(case.plan,case.approvals,case.signers,
        registry_dir=tmp_path.resolve()/"operations",request_id="recovery-fixture",actor_id="synthetic",
        counts_path=absent,metadata_path=absent,full_design_path=absent,reduced_design_path=absent,
        output_dir=tmp_path.resolve()/"execution",authority_store_dir=case.store.root,
        authority_administrators=[case.admin],authority_checkpoint=case.checkpoint)
    assert result["status"]=="requires_reconciliation"
    monkeypatch.setattr(RunRegistry,"append_event",append)
    recovered=recover_native_attempt(tmp_path.resolve()/"operations",result["attempt_id"],case.signers,
        actor_id="synthetic",output_dir=tmp_path.resolve()/"recovery",authority_administrators=[case.admin])
    assert recovered["status"]=="reconciled",recovered
    assert recovered["registry_status_after"]=="failed"
    assert recovered["analysis_completed"] is False
    for name in ("authority-snapshot.json","authority-administrators.json","authority-checkpoint.json"):
        assert (tmp_path/"recovery/original-snapshot"/name).read_bytes()==(tmp_path/"execution"/name).read_bytes()
