"""Administrator-signed study grants against explicitly supplied Ed25519 roots.

Enrollment references are configured assertions, not proof of human authentication
or clinical qualification. Policy verification and grant authorization are separate
checks; neither is a clinical release authorization. Historical verification uses
the supplied historical clock and does not authorize a current execution.
No private keys, default administrators or fallback grants are created here.
"""
import base64
from datetime import datetime, timezone
import hashlib
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from pydantic import AfterValidator, Field, StringConstraints, TypeAdapter, field_validator, model_validator

from .contracts import Digest, FrozenContract, NonemptyText, Revision, canonical_json_bytes, parse_json_bytes
from .trust import Approval, TrustedSigner, _aware_time, _public_key


AUTHORITY_JSON_LIMIT_BYTES = 64 * 1024
AUTHORITY_VERIFICATION_SCOPE = ("configured administrative keys and study grants; "
                                "human identity and clinical qualification not established")


def _registry_id(value: str) -> str:
    identifier = UUID(value)
    if identifier.version != 4 or identifier.hex != value:
        raise ValueError("registry_id must be a canonical lowercase UUID4 hex string")
    return value


RegistryID = Annotated[str, StringConstraints(strict=True, min_length=32, max_length=32),
                       AfterValidator(_registry_id)]


def _key_fingerprint(pem: str) -> str:
    raw = _public_key(pem).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()


class AuthorityAdministrator(FrozenContract):
    """One externally configured administrative public key and validity interval."""

    authority_id: NonemptyText
    registry_id: RegistryID
    key_id: NonemptyText
    subject_id: NonemptyText
    public_key_pem: NonemptyText
    valid_from: datetime
    valid_until: datetime

    @field_validator("public_key_pem")
    @classmethod
    def valid_public_key(cls, value):
        _public_key(value)
        return value

    @field_validator("valid_from", "valid_until")
    @classmethod
    def timezone_required(cls, value):
        return _aware_time(value)

    @model_validator(mode="after")
    def chronological_validity(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("Administrator validity must end after it starts")
        return self


class AuthorityGrant(FrozenContract):
    """A configured study/role grant; enrollment_ref does not authenticate a human."""

    grant_id: NonemptyText
    signer: TrustedSigner
    study_id: NonemptyText
    enrollment_ref: NonemptyText
    valid_from: datetime
    valid_until: datetime

    @field_validator("valid_from", "valid_until")
    @classmethod
    def timezone_required(cls, value):
        return _aware_time(value)

    @model_validator(mode="after")
    def chronological_validity(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("Grant validity must end after it starts")
        return self


class AuthorityPolicy(FrozenContract):
    """Immutable policy content; signatures and predecessor history are verified separately."""

    schema_version: Literal["approval_authority_policy/1"] = "approval_authority_policy/1"
    algorithm: Literal["Ed25519"] = "Ed25519"
    authority_id: NonemptyText
    registry_id: RegistryID
    administrator_key_id: NonemptyText
    revision: Revision
    previous_sha256: Digest | None
    issued_at: datetime
    expires_at: datetime
    change_reason: NonemptyText
    grants: Annotated[tuple[AuthorityGrant, ...], Field(max_length=256)]

    @field_validator("issued_at", "expires_at")
    @classmethod
    def timezone_required(cls, value):
        return _aware_time(value)

    @model_validator(mode="after")
    def coherent_policy(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("Policy expiry must be after issue time")
        if (self.revision == 1) != (self.previous_sha256 is None):
            raise ValueError("Revision 1 requires no predecessor; later revisions require its digest")
        grant_ids, scopes = set(), set()
        identities, public_keys = {}, {}
        for grant in self.grants:
            if grant.valid_until > self.expires_at:
                raise ValueError("A grant must not outlive its policy")
            signer = grant.signer
            scope = (grant.study_id, signer.key_id, signer.role)
            if grant.grant_id in grant_ids or scope in scopes:
                raise ValueError("Grant IDs and study/key/role scopes must be unambiguous")
            fingerprint = _key_fingerprint(signer.public_key_pem)
            identity = (fingerprint, signer.subject_id)
            if signer.key_id in identities and identities[signer.key_id] != identity:
                raise ValueError("A repeated key_id must retain the same public key and subject")
            if fingerprint in public_keys and public_keys[fingerprint] != signer.key_id:
                raise ValueError("Distinct approval key IDs must not alias the same public key")
            grant_ids.add(grant.grant_id)
            scopes.add(scope)
            identities[signer.key_id] = identity
            public_keys[fingerprint] = signer.key_id
        return self


class SignedAuthorityPolicy(FrozenContract):
    """A canonical 64-byte Ed25519 signature over policy.canonical_bytes()."""

    policy: AuthorityPolicy
    signature: NonemptyText

    @field_validator("signature")
    @classmethod
    def canonical_signature(cls, value):
        return Approval.canonical_signature(value)


def _check_json_boundary(raw: bytes):
    if type(raw) is not bytes:
        raise TypeError("Authority JSON input must be bytes")
    if len(raw) > AUTHORITY_JSON_LIMIT_BYTES:
        raise ValueError("Authority JSON input exceeds the 64 KiB size limit")
    parse_json_bytes(raw)


def parse_signed_policy_bytes(raw: bytes) -> SignedAuthorityPolicy:
    """Parse at most 64 KiB, rejecting duplicate fields, nonfinite values and coercions."""
    _check_json_boundary(raw)
    return SignedAuthorityPolicy.model_validate_json(raw)


def _validated_administrators(administrators) -> tuple[AuthorityAdministrator, ...]:
    if not isinstance(administrators, (list, tuple)):
        raise ValueError("Externally configured administrators must be a list or tuple")
    entries, key_ids, public_keys = [], set(), set()
    for supplied in administrators:
        administrator = (AuthorityAdministrator.model_validate_json(canonical_json_bytes(supplied))
                         if type(supplied) is dict else AuthorityAdministrator.model_validate(supplied))
        fingerprint = _key_fingerprint(administrator.public_key_pem)
        if administrator.key_id in key_ids or fingerprint in public_keys:
            raise ValueError("Administrator keys must be distinct, including public-key aliases")
        key_ids.add(administrator.key_id)
        public_keys.add(fingerprint)
        entries.append(administrator)
    return tuple(entries)


def parse_administrators_bytes(raw: bytes) -> tuple[AuthorityAdministrator, ...]:
    """Parse an externally supplied JSON array of distinct roots, at most 64 KiB."""
    _check_json_boundary(raw)
    administrators = TypeAdapter(tuple[AuthorityAdministrator, ...]).validate_json(raw, strict=True)
    return _validated_administrators(administrators)


def verify_policy_signature(envelope, administrators, verified_at=None) -> dict:
    """Verify signature and validity against the supplied roots at one explicit clock.

Models are revalidated using strict Python semantics. Plain dictionaries must be
JSON-compatible; only that explicit JSON boundary accepts serialized timestamps.
The receipt establishes configured-key policy integrity, not human authentication
or authorization to run a study now when a historical verified_at was supplied.
"""
    signed_policy = (parse_signed_policy_bytes(canonical_json_bytes(envelope))
                     if type(envelope) is dict else SignedAuthorityPolicy.model_validate(envelope))
    policy = signed_policy.policy
    at = _aware_time(datetime.now(timezone.utc) if verified_at is None else verified_at)
    roots = _validated_administrators(administrators)
    matches = [root for root in roots if (root.authority_id, root.registry_id, root.key_id) ==
               (policy.authority_id, policy.registry_id, policy.administrator_key_id)]
    if len(matches) != 1:
        raise ValueError("Policy administrator is absent or ambiguous in the externally configured roots")
    administrator = matches[0]
    if not policy.issued_at <= at < policy.expires_at:
        raise ValueError("Policy is expired or issued in the future")
    if (not administrator.valid_from <= policy.issued_at < administrator.valid_until
            or not administrator.valid_from <= at < administrator.valid_until
            or policy.expires_at > administrator.valid_until):
        raise ValueError("Administrator validity must cover policy issue, verification and expiry")
    approval_keys = {_key_fingerprint(grant.signer.public_key_pem) for grant in policy.grants}
    if any(_key_fingerprint(root.public_key_pem) in approval_keys for root in roots):
        raise ValueError("Administrative keys must not alias analysis-approval keys")
    public_key = _public_key(administrator.public_key_pem)
    try:
        public_key.verify(base64.b64decode(signed_policy.signature, validate=True), policy.canonical_bytes())
    except InvalidSignature as error:
        raise ValueError("Invalid Ed25519 authority policy signature") from error
    return {
        "schema_version": "authority_signature_verification/1",
        "authority_id": policy.authority_id,
        "registry_id": policy.registry_id,
        "revision": policy.revision,
        "administrator_key_id": administrator.key_id,
        "administrator_subject_id": administrator.subject_id,
        "policy_sha256": policy.sha256(),
        "envelope_sha256": signed_policy.sha256(),
        "administrator_public_key_sha256": _key_fingerprint(administrator.public_key_pem),
        "verified_at": at.isoformat(),
        "scope": AUTHORITY_VERIFICATION_SCOPE,
        "clinical_release_allowed": False,
    }


def authorize_signers(policy, study_id, signers, at) -> list[dict]:
    """Return active configured grants in signer order; signature verification is separate.

This does not replace verify_plan_approvals, which requires distinct author and
reviewer subjects and keys and verifies the actual plan approval signatures.
"""
    policy = (AuthorityPolicy.model_validate_json(canonical_json_bytes(policy))
              if type(policy) is dict else AuthorityPolicy.model_validate(policy))
    at = _aware_time(at)
    if not policy.issued_at <= at < policy.expires_at:
        raise ValueError("Policy is expired or issued in the future")
    if type(study_id) is not str or not study_id.strip():
        raise ValueError("A nonblank study_id is required")
    if not isinstance(signers, (list, tuple)):
        raise ValueError("Signers must be supplied in a list or tuple")
    receipts = []
    for supplied in signers:
        signer = (TrustedSigner.model_validate_json(canonical_json_bytes(supplied))
                  if type(supplied) is dict else TrustedSigner.model_validate(supplied))
        matches = [grant for grant in policy.grants
                   if (grant.study_id, grant.signer.key_id, grant.signer.role) ==
                   (study_id, signer.key_id, signer.role) and grant.valid_from <= at < grant.valid_until]
        if len(matches) != 1:
            raise ValueError("Signer requires exactly one active grant for the supplied study, key and role")
        grant = matches[0]
        fingerprint = _key_fingerprint(signer.public_key_pem)
        if (signer.subject_id != grant.signer.subject_id
                or fingerprint != _key_fingerprint(grant.signer.public_key_pem)):
            raise ValueError("Signer subject and public key must match the configured grant")
        values = grant.model_dump(mode="json")
        receipts.append({
            "grant_id": grant.grant_id, "study_id": grant.study_id,
            "key_id": signer.key_id, "subject_id": signer.subject_id, "role": signer.role,
            "public_key_sha256": fingerprint, "enrollment_ref": grant.enrollment_ref,
            "valid_from": values["valid_from"], "valid_until": values["valid_until"],
        })
    return receipts
