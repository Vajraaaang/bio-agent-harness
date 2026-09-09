"""Bind preserved authority evidence to independently supplied administrator trust."""
from .authority_contracts import AuthorityAdministrator, AUTHORITY_JSON_LIMIT_BYTES, parse_administrators_bytes, _key_fingerprint
from .authority_store import authorize_snapshot
from .contracts import canonical_json_bytes
from .trust import TrustedSigner
from .verification import _require, _time


def administrator_values(values):
    """Serialize supplied public configuration, without declaring it trusted."""
    _require(isinstance(values,(list,tuple)),"Administrator configuration must be an explicit list")
    raw=[value.model_dump(mode="json") if isinstance(value,AuthorityAdministrator) else value for value in values]
    _require(len(canonical_json_bytes(raw))<=AUTHORITY_JSON_LIMIT_BYTES,"Administrator configuration exceeds bound")
    return raw


def selected_signers(signers, approval_receipts):
    """Select only keys already accepted by verify_plan_approvals, in receipt order."""
    raw=list(signers.values()) if isinstance(signers,dict) else signers
    entries=[TrustedSigner.model_validate(value) for value in raw]
    by_id={s.key_id:s for s in entries}
    ids=[r["key_id"] for r in approval_receipts]
    _require(len(ids)==2 and len(set(ids))==2 and set(ids)<=set(by_id),"Two verified approval keys are required")
    return [by_id[key] for key in ids]


def matching_administrators(saved, supplied):
    _require(supplied is not None,"Independent administrator trust is required for governed evidence")
    actual=parse_administrators_bytes(canonical_json_bytes(administrator_values(saved)))
    trusted=parse_administrators_bytes(canonical_json_bytes(administrator_values(supplied)))
    def identity(value):
        fields=value.model_dump(mode="json");fields.pop("public_key_pem")
        fields["public_key_sha256"]=_key_fingerprint(value.public_key_pem)
        return fields
    indexed={(a.authority_id,a.registry_id,a.key_id):identity(a) for a in trusted}
    _require(actual and all(indexed.get((a.authority_id,a.registry_id,a.key_id))==identity(a) for a in actual),
             "Preserved administrator configuration is not covered by external trust")
    return actual


def verify_admission(plan, snapshot, checkpoint, saved_administrators, saved_decision,
                     supplied_administrators, signers, approval_receipts, before, after):
    administrators=matching_administrators(saved_administrators,supplied_administrators)
    chosen=selected_signers(signers,approval_receipts)
    _require(type(saved_decision) is dict,"Missing governed admission decision")
    at=_time(saved_decision["checked_at"])
    _require(before<=at<=after,"Authority decision lies outside its preserved event interval")
    expected=authorize_snapshot(plan,chosen,administrators,snapshot,now=at,expected_checkpoint=checkpoint)
    _require(saved_decision==expected,"Preserved authority decision differs from verified policy and grants")
    return {"snapshot":snapshot,"checkpoint":checkpoint,"administrators":administrators,"signers":chosen,
            "decision":expected}
