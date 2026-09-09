"""Ed25519 approval verification against an explicitly supplied public-key registry.

The caller must provision this registry through an independently controlled channel.
These checks establish key possession and payload integrity, not human identity,
clinical qualification, an authorization-provider integration or Part 11 compliance.
No private keys or trust defaults are created by this module.
Signatures cover payload.canonical_bytes(), including schema/algorithm defaults and
timestamps normalized to UTC by ApprovalPayload validation.
"""
import base64
import binascii
from datetime import datetime, timezone
import hashlib
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import field_validator, model_validator

from .contracts import AnalysisPlan, Digest, FrozenContract, NonemptyText, Revision, Role, canonical_json_bytes
from .native_contracts import NativeAnalysisPlan, NATIVE_PLAN_MODELS, parse_native_plan_bytes
from .ed25519_validation import validate_public_point

VERIFICATION_SCOPE = "configured-key signatures only; human identity/clinical qualification not established"


def _public_key(pem: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as error:
        raise ValueError("Invalid Ed25519 PEM public key") from error
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("Trusted key must be Ed25519")
    validate_public_point(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return key


def _aware_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Approval time must have an explicit timezone")
    return value.astimezone(timezone.utc)


class TrustedSigner(FrozenContract):
    key_id: NonemptyText
    subject_id: NonemptyText
    role: Role
    public_key_pem: NonemptyText

    @field_validator("public_key_pem")
    @classmethod
    def valid_public_key(cls, value):
        _public_key(value)
        return value


class ApprovalPayload(FrozenContract):
    schema_version: Literal["controlled_approval/1"] = "controlled_approval/1"
    algorithm: Literal["Ed25519"] = "Ed25519"
    plan_sha256: Digest
    plan_id: NonemptyText
    revision: Revision
    study_id: NonemptyText
    key_id: NonemptyText
    signer_id: NonemptyText
    meaning: Role
    issued_at: datetime
    expires_at: datetime

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value):
        return _aware_time(value)

    @model_validator(mode="after")
    def chronological_validity(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("Approval expiry must be after issue time")
        return self


class Approval(FrozenContract):
    payload: ApprovalPayload
    signature: NonemptyText

    @field_validator("signature")
    @classmethod
    def canonical_signature(cls, value):
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Signature must be canonical base64") from error
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError("Signature must encode exactly 64 Ed25519 bytes as canonical base64")
        return value


def verify_plan_approvals(plan, approvals, signers, now=None) -> list[dict]:
    """Verify exactly one author and reviewer for this immutable plan, or raise.

Approvals accept validated models or JSON-compatible dictionaries. Signers accept
a sequence or a mapping keyed by key_id. The registry itself is never derived from
the approvals. ``now`` is a timezone-aware injectable clock for reproducible tests;
production callers are responsible for the integrity of their clock and registry.
"""
    # Native and Python plans have distinct versioned semantics. Never accept
    # an arbitrary object merely because it has matching signature attributes.
    if isinstance(plan, NativeAnalysisPlan) or (type(plan) is dict and
            plan.get("schema_version") in NATIVE_PLAN_MODELS):
        analysis_plan = parse_native_plan_bytes(plan.canonical_bytes() if isinstance(plan, NativeAnalysisPlan) else canonical_json_bytes(plan))
    else:
        analysis_plan = AnalysisPlan.model_validate(plan)
    verified_at = _aware_time(datetime.now(timezone.utc) if now is None else now)
    if not isinstance(approvals, (list, tuple)) or len(approvals) != 2:
        raise ValueError("Exactly one author and one reviewer approval are required")
    if not isinstance(signers, (list, tuple, dict)) or not signers:
        raise ValueError("A nonempty externally configured signer registry is required")
    entries = list(signers.values()) if isinstance(signers, dict) else signers
    registry, key_fingerprints = {}, set()
    for entry in entries:
        signer = TrustedSigner.model_validate(entry)
        if isinstance(signers, dict) and signers.get(signer.key_id) is not entry:
            raise ValueError("Signer registry mapping keys must match key_id")
        public_key = _public_key(signer.public_key_pem)
        raw_key = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        fingerprint = hashlib.sha256(raw_key).hexdigest()
        if signer.key_id in registry or fingerprint in key_fingerprints:
            raise ValueError("Signer registry keys must be distinct, including public-key aliases")
        registry[signer.key_id] = (signer, public_key, fingerprint)
        key_fingerprints.add(fingerprint)

    digest = analysis_plan.sha256()
    receipts, roles, subjects = [], set(), set()
    for supplied in approvals:
        approval = (Approval.model_validate_json(canonical_json_bytes(supplied))
                    if type(supplied) is dict else Approval.model_validate(supplied))
        payload = approval.payload
        if (payload.plan_sha256, payload.plan_id, payload.revision, payload.study_id) != (
                digest, analysis_plan.plan_id, analysis_plan.revision, analysis_plan.study_id):
            raise ValueError("Approval does not bind the supplied plan")
        if payload.issued_at > verified_at or payload.expires_at <= verified_at:
            raise ValueError("Approval is expired or issued in the future")
        if payload.key_id not in registry:
            raise ValueError("Approval key is absent from the configured registry")
        signer, public_key, fingerprint = registry[payload.key_id]
        if payload.signer_id != signer.subject_id or payload.meaning != signer.role:
            raise ValueError("Approval signer or meaning does not match configured identity and role")
        if signer.subject_id in subjects or signer.role in roles:
            raise ValueError("Author and reviewer must have distinct subjects and roles")
        try:
            public_key.verify(base64.b64decode(approval.signature, validate=True), payload.canonical_bytes())
        except InvalidSignature as error:
            raise ValueError("Invalid Ed25519 approval signature") from error
        roles.add(signer.role)
        subjects.add(signer.subject_id)
        receipts.append({**payload.model_dump(mode="json"),
                         "approval_schema_version": payload.schema_version,
                         "schema_version": "controlled_approval_receipt/1",
                         "approval_sha256": approval.sha256(), "public_key_sha256": fingerprint,
                         "signature": approval.signature,
                         "verified_at": verified_at.isoformat(), "scope": VERIFICATION_SCOPE})
    if roles != {"author", "reviewer"}:
        raise ValueError("Both author and reviewer roles are required")
    return receipts
