"""Real signature/history checks for dispatch claims; no numerical execution."""
import base64
from datetime import datetime, timedelta, timezone

import pytest

from devharness_controlled.authority_contracts import AuthorityGrant, AuthorityPolicy
from devharness_controlled.authority_store import authorize_snapshot
from devharness_controlled.native_contracts import GovernedNativePlan
from devharness_controlled.native_verification import _verify_governed_dispatch
from devharness_controlled.trust import Approval, ApprovalPayload, verify_plan_approvals
from tests.test_governed_native_execution import fixture


def dispatch_case(tmp_path, expiry=None):
    case=fixture(tmp_path); now=datetime.now(timezone.utc); at=now+timedelta(seconds=1)
    end=at+timedelta(milliseconds=100)
    grants=tuple(AuthorityGrant(**{**g.model_dump(),"valid_until":end}) for g in case.policy.grants) if expiry=="grant" else case.policy.grants
    policy=AuthorityPolicy(**{**case.policy.model_dump(),"revision":2,"previous_sha256":case.checkpoint["tip_sha256"],
        "issued_at":now,"grants":grants,"change_reason":"Synthetic expiry check"})
    checkpoint=case.store.publish(case.sign(policy),[case.admin],expected_tip=case.checkpoint["tip_sha256"])
    plan=GovernedNativePlan(**{**case.plan.model_dump(),"authority_tip_sha256":checkpoint["tip_sha256"]})
    approvals=[]
    for old in case.approvals:
        payload=ApprovalPayload(**{**old.payload.model_dump(),"plan_sha256":plan.sha256(),
            "expires_at":end if expiry=="approval" else old.payload.expires_at})
        approvals.append(Approval(payload=payload,signature=base64.b64encode(case.keys[payload.meaning].sign(payload.canonical_bytes())).decode()))
    snapshot=case.store.capture()
    context={"snapshot":snapshot,"checkpoint":checkpoint,"signers":case.signers,"administrators":[case.admin]}
    decision=authorize_snapshot(plan,case.signers,[case.admin],snapshot,now=at,expected_checkpoint=checkpoint)
    observed={"Id":"b"*64,"State":{"Status":"running","Running":True,"StartedAt":(at+timedelta(milliseconds=200)).isoformat()}}
    transport={"status":"ok","created":{"Id":"b"*64},"launch_authority":decision,
        "launch_approvals":verify_plan_approvals(plan,approvals,case.signers,now=at),"dispatch_observed":observed}
    journal=[{"event":"start_authorized","recorded_at":at.isoformat(),"payload":{"authority":decision,"receipts":transport["launch_approvals"],"container_id":"b"*64}},
        {"event":"attach_started","recorded_at":(at+timedelta(milliseconds=5)).isoformat(),"payload":{}},
        {"event":"authority_dispatch_observed","recorded_at":(at+timedelta(milliseconds=210)).isoformat(),"payload":{"container":observed}}]
    return plan,transport,journal,context,at,at+timedelta(milliseconds=220),approvals


@pytest.mark.parametrize("expiry",["grant","approval"])
def test_actual_start_must_be_authorized_even_after_valid_prelaunch_check(tmp_path,expiry):
    with pytest.raises(ValueError,match="grant|expired|validity"):
        _verify_governed_dispatch(*dispatch_case(tmp_path,expiry))


def test_actual_start_with_live_grants_and_approvals_is_verified(tmp_path):
    _verify_governed_dispatch(*dispatch_case(tmp_path))


def test_failed_history_cannot_claim_observation_without_authority(tmp_path):
    values=dispatch_case(tmp_path); values[1].update(status="failed",launch_authority=None)
    values[2].clear()
    with pytest.raises(ValueError): _verify_governed_dispatch(*values)


def test_failed_history_cannot_claim_forged_launch_approval(tmp_path):
    values=dispatch_case(tmp_path); values[1]["status"]="failed"
    values[1]["launch_approvals"][0]["signer_id"]="forged"
    with pytest.raises(ValueError,match="approval"):
        _verify_governed_dispatch(*values)
