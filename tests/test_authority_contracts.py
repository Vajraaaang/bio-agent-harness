"""Configured authority contracts; every signing key is an ephemeral synthetic fixture."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from pydantic import ValidationError

from devharness_controlled.contracts import canonical_json_bytes
from devharness_controlled.trust import TrustedSigner


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
REGISTRY_ID = "123456789abc4def8abc123456789abc"
OTHER_REGISTRY_ID = "fedcba98765443218abc0123456789ab"
SCOPE = ("configured administrative keys and study grants; "
         "human identity and clinical qualification not established")


def api():
    path = Path(__file__).resolve().parents[1] / "devharness_controlled" / "authority_contracts.py"
    assert path.is_file(), "Authority contract implementation is missing"
    return importlib.import_module("devharness_controlled.authority_contracts")


def pem(private_key):
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def fingerprint(public_key_pem):
    public_key = serialization.load_pem_public_key(public_key_pem.encode("ascii"))
    return hashlib.sha256(public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest()


def changed(model, **updates):
    return type(model)(**{**model.model_dump(), **updates})


def signed(policy, private_key):
    return api().SignedAuthorityPolicy(policy=policy, signature=base64.b64encode(
        private_key.sign(policy.canonical_bytes())).decode("ascii"))


@pytest.fixture
def bundle():
    module = api()
    administrator_key = Ed25519PrivateKey.generate()
    administrator = module.AuthorityAdministrator(
        authority_id="synthetic-authority", registry_id=REGISTRY_ID, key_id="administrator-key",
        subject_id="configured-administrator", public_key_pem=pem(administrator_key),
        valid_from=NOW - timedelta(days=2), valid_until=NOW + timedelta(days=30))
    signers = tuple(TrustedSigner(key_id=f"{role}-key", subject_id=f"configured-{role}",
                                 role=role, public_key_pem=pem(Ed25519PrivateKey.generate()))
                    for role in ("author", "reviewer"))
    grants = tuple(module.AuthorityGrant(
        grant_id=f"{signer.role}-grant", signer=signer, study_id="synthetic-study",
        enrollment_ref=f"configured-enrollment-{signer.role}",
        valid_from=NOW - timedelta(days=1), valid_until=NOW + timedelta(days=3))
        for signer in signers)
    policy = module.AuthorityPolicy(authority_id=administrator.authority_id,
                                    registry_id=REGISTRY_ID,
                                    administrator_key_id=administrator.key_id, revision=1,
                                    previous_sha256=None, issued_at=NOW - timedelta(hours=1),
                                    expires_at=NOW + timedelta(days=10),
                                    change_reason="Synthetic study enrollment", grants=grants)
    return SimpleNamespace(administrator=administrator, administrator_key=administrator_key,
                           signers=signers, grants=grants, policy=policy,
                           envelope=signed(policy, administrator_key))


def test_real_signature_receipt_binds_policy_envelope_root_and_scope(bundle):
    receipt = api().verify_policy_signature(bundle.envelope, (bundle.administrator,), verified_at=NOW)
    assert receipt == {
        "schema_version": "authority_signature_verification/1",
        "authority_id": bundle.policy.authority_id,
        "registry_id": REGISTRY_ID,
        "revision": 1,
        "administrator_key_id": bundle.administrator.key_id,
        "administrator_subject_id": bundle.administrator.subject_id,
        "policy_sha256": bundle.policy.sha256(),
        "envelope_sha256": bundle.envelope.sha256(),
        "administrator_public_key_sha256": fingerprint(bundle.administrator.public_key_pem),
        "verified_at": NOW.isoformat(),
        "scope": SCOPE,
        "clinical_release_allowed": False,
    }
    json.dumps(receipt, allow_nan=False)


@pytest.mark.parametrize("model_name", ["administrator", "policy"])
def test_administrator_and_policy_require_an_explicit_registry_id(bundle, model_name):
    model = getattr(bundle, model_name)
    values = model.model_dump()
    values.pop("registry_id", None)
    with pytest.raises(ValidationError, match="registry_id"):
        type(model)(**values)


@pytest.mark.parametrize("model_name", ["administrator", "policy"])
@pytest.mark.parametrize("registry_id", [
    "", " " * 32, "g" * 32, "0" * 32, REGISTRY_ID.upper(), str(UUID(REGISTRY_ID)),
    "{" + REGISTRY_ID + "}", REGISTRY_ID[:12] + "1" + REGISTRY_ID[13:],
    REGISTRY_ID[:16] + "0" + REGISTRY_ID[17:],
    123, True, None, REGISTRY_ID.encode("ascii"), UUID(REGISTRY_ID),
])
def test_registry_id_requires_canonical_lowercase_uuid4_hex(bundle, model_name, registry_id):
    with pytest.raises(ValidationError, match="registry_id"):
        changed(getattr(bundle, model_name), registry_id=registry_id)


@pytest.mark.parametrize("model_name", ["administrator", "policy"])
def test_registry_id_is_validated_at_the_json_boundary(bundle, model_name):
    model = getattr(bundle, model_name)
    for registry_id in (REGISTRY_ID.upper(), str(UUID(REGISTRY_ID)), "0" * 32, True):
        values = {**model.model_dump(mode="json"), "registry_id": registry_id}
        with pytest.raises(ValidationError, match="registry_id"):
            type(model).model_validate_json(canonical_json_bytes(values))


def test_valid_policy_signature_cannot_cross_registry_bindings(bundle):
    module = api()
    other_root = changed(bundle.administrator, registry_id=OTHER_REGISTRY_ID)
    with pytest.raises(ValueError):
        module.verify_policy_signature(bundle.envelope, (other_root,), verified_at=NOW)
    other_policy = changed(bundle.policy, registry_id=OTHER_REGISTRY_ID)
    other_envelope = signed(other_policy, bundle.administrator_key)
    with pytest.raises(ValueError):
        module.verify_policy_signature(other_envelope, (bundle.administrator,), verified_at=NOW)
    receipt = module.verify_policy_signature(other_envelope, (other_root,), verified_at=NOW)
    assert receipt["registry_id"] == OTHER_REGISTRY_ID


def test_registry_binding_is_covered_by_the_policy_signature(bundle):
    other_policy = changed(bundle.policy, registry_id=OTHER_REGISTRY_ID)
    other_root = changed(bundle.administrator, registry_id=OTHER_REGISTRY_ID)
    tampered = changed(bundle.envelope, policy=other_policy)
    assert other_policy.sha256() != bundle.policy.sha256()
    with pytest.raises(ValueError, match="signature"):
        api().verify_policy_signature(tampered, (other_root,), verified_at=NOW)


def test_canonical_roundtrip_and_frozen_nested_contracts(bundle):
    module = api()
    encoded = bundle.envelope.canonical_bytes()
    assert encoded == canonical_json_bytes(bundle.envelope.model_dump(mode="json"))
    assert bundle.envelope.sha256() == hashlib.sha256(encoded).hexdigest()
    assert module.parse_signed_policy_bytes(encoded) == bundle.envelope
    administrators = canonical_json_bytes([bundle.administrator.model_dump(mode="json")])
    assert module.parse_administrators_bytes(administrators) == (bundle.administrator,)
    assert bundle.policy.schema_version == "approval_authority_policy/1"
    assert bundle.policy.algorithm == "Ed25519"
    assert isinstance(bundle.policy.grants, tuple)
    with pytest.raises(ValidationError):
        bundle.policy.revision = 2
    with pytest.raises(ValidationError):
        bundle.grants[0].signer.subject_id = "changed"


def test_json_dict_verification_is_explicit_and_python_models_remain_strict(bundle):
    module = api()
    expected = module.verify_policy_signature(bundle.envelope, (bundle.administrator,), verified_at=NOW)
    assert module.verify_policy_signature(
        bundle.envelope.model_dump(mode="json"), [bundle.administrator.model_dump(mode="json")],
        verified_at=NOW) == expected
    with pytest.raises(ValidationError):
        module.AuthorityPolicy.model_validate(bundle.policy.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        changed(bundle.policy, grants=list(bundle.grants))
    with pytest.raises(TypeError):
        module.verify_policy_signature(bundle.envelope.model_dump(), (bundle.administrator,), verified_at=NOW)


def test_timestamps_normalize_to_utc_before_signing_and_receipts(bundle):
    offset = timezone(timedelta(hours=-7))
    administrator = changed(bundle.administrator, valid_from=bundle.administrator.valid_from.astimezone(offset))
    grant = changed(bundle.grants[0], valid_from=bundle.grants[0].valid_from.astimezone(offset))
    policy = changed(bundle.policy, issued_at=bundle.policy.issued_at.astimezone(offset),
                     grants=(grant, bundle.grants[1]))
    assert administrator.canonical_bytes() == bundle.administrator.canonical_bytes()
    assert policy.canonical_bytes() == bundle.policy.canonical_bytes()
    receipt = api().verify_policy_signature(bundle.envelope, (administrator,), verified_at=NOW.astimezone(offset))
    assert receipt["verified_at"] == NOW.isoformat()


@pytest.mark.parametrize("model_name,field", [
    ("administrator", "valid_from"), ("administrator", "valid_until"),
    ("grant", "valid_from"), ("grant", "valid_until"),
    ("policy", "issued_at"), ("policy", "expires_at"),
])
def test_every_contract_timestamp_requires_a_timezone(bundle, model_name, field):
    model = bundle.grants[0] if model_name == "grant" else getattr(bundle, model_name)
    with pytest.raises(ValidationError):
        changed(model, **{field: NOW.replace(tzinfo=None)})


@pytest.mark.parametrize("model_name,start,end", [
    ("administrator", "valid_from", "valid_until"),
    ("grant", "valid_from", "valid_until"), ("policy", "issued_at", "expires_at"),
])
def test_contract_intervals_are_nonempty(bundle, model_name, start, end):
    model = bundle.grants[0] if model_name == "grant" else getattr(bundle, model_name)
    for invalid in (getattr(model, start), getattr(model, start) - timedelta(microseconds=1)):
        with pytest.raises(ValidationError):
            changed(model, **{end: invalid})


@pytest.mark.parametrize("model_name", ["administrator", "grant", "policy", "envelope"])
def test_contracts_forbid_unknown_fields(bundle, model_name):
    model = bundle.grants[0] if model_name == "grant" else getattr(bundle, model_name)
    with pytest.raises(ValidationError):
        changed(model, clinical_release_allowed=True)


@pytest.mark.parametrize("field,value", [("revision", True), ("revision", 1.0), ("revision", "1"),
                                          ("revision", 0), ("algorithm", "none"),
                                          ("schema_version", "approval_authority_policy/2"),
                                          ("change_reason", " "), ("previous_sha256", "x" * 64)])
def test_policy_rejects_coerced_and_unsupported_fields(bundle, field, value):
    with pytest.raises(ValidationError):
        changed(bundle.policy, **{field: value})


def test_policy_predecessor_shape_and_explicit_revoke_all(bundle):
    module = api()
    with pytest.raises(ValidationError):
        changed(bundle.policy, previous_sha256="a" * 64)
    with pytest.raises(ValidationError):
        changed(bundle.policy, revision=2)
    revoked = changed(bundle.policy, revision=2, previous_sha256=bundle.policy.sha256(), grants=())
    assert revoked.previous_sha256 == bundle.policy.sha256()
    assert module.verify_policy_signature(signed(revoked, bundle.administrator_key),
                                          (bundle.administrator,), verified_at=NOW)
    with pytest.raises(ValueError):
        module.authorize_signers(revoked, "synthetic-study", bundle.signers, NOW)
    values = bundle.policy.model_dump()
    del values["grants"]
    with pytest.raises(ValidationError):
        module.AuthorityPolicy(**values)


def test_grants_can_precede_issue_but_cannot_outlive_the_policy(bundle):
    assert bundle.grants[0].valid_from < bundle.policy.issued_at
    ending_with_policy = changed(bundle.grants[0], valid_until=bundle.policy.expires_at)
    assert changed(bundle.policy, grants=(ending_with_policy,))
    outliving_policy = changed(bundle.grants[0], valid_until=bundle.policy.expires_at + timedelta(microseconds=1))
    with pytest.raises(ValidationError):
        changed(bundle.policy, grants=(outliving_policy,))


def test_policy_rejects_duplicate_grants_and_ambiguous_scope(bundle):
    for duplicate in (bundle.grants[0], changed(bundle.grants[0], grant_id="other-grant"),
                      changed(bundle.grants[1], grant_id=bundle.grants[0].grant_id)):
        with pytest.raises(ValidationError):
            changed(bundle.policy, grants=(*bundle.grants, duplicate))


def test_key_reuse_requires_identical_subject_and_public_key_across_studies_and_roles(bundle):
    author = bundle.signers[0]
    another_study = changed(bundle.grants[0], grant_id="another-study-grant", study_id="another-study")
    another_role = changed(bundle.grants[0], grant_id="another-role-grant", signer=changed(author, role="reviewer"))
    assert changed(bundle.policy, grants=(*bundle.grants, another_study, another_role))
    for replacement in (changed(author, subject_id="substituted-subject"),
                        changed(author, public_key_pem=pem(Ed25519PrivateKey.generate()))):
        with pytest.raises(ValidationError):
            changed(bundle.policy, grants=(*bundle.grants, changed(another_study, signer=replacement)))


def test_distinct_key_ids_cannot_alias_the_same_approval_public_key(bundle):
    alias = changed(bundle.signers[0], key_id="alias-key")
    grant = changed(bundle.grants[0], grant_id="alias-grant", study_id="another-study", signer=alias)
    with pytest.raises(ValidationError):
        changed(bundle.policy, grants=(*bundle.grants, grant))


def test_grant_count_bound_is_enforced(bundle):
    grants = tuple(changed(bundle.grants[0], grant_id=f"grant-{i}", study_id=f"study-{i}") for i in range(257))
    assert len(changed(bundle.policy, grants=grants[:256]).grants) == 256
    with pytest.raises(ValidationError):
        changed(bundle.policy, grants=grants)


@pytest.mark.parametrize("invalid", ["not-pem", " ", "é"])
def test_invalid_administrator_public_keys_are_rejected(bundle, invalid):
    with pytest.raises(ValidationError):
        changed(bundle.administrator, public_key_pem=invalid)


def test_non_ed25519_administrator_keys_are_rejected(bundle):
    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValidationError):
        changed(bundle.administrator, public_key_pem=pem(rsa_key))


@pytest.mark.parametrize("signature", ["bad-base64", base64.b64encode(b"x" * 63).decode(),
                                      base64.b64encode(b"x" * 65).decode(),
                                      base64.b64encode(b"x" * 64).decode() + "\n"])
def test_signed_policy_rejects_noncanonical_or_wrong_length_signatures(bundle, signature):
    with pytest.raises(ValidationError):
        changed(bundle.envelope, signature=signature)


def test_noncanonical_base64_pad_bits_are_rejected(bundle):
    signature = base64.b64encode(b"x" * 64).decode()
    replacement = signature[:-3] + "B=="
    assert base64.b64decode(replacement) == base64.b64decode(signature)
    with pytest.raises(ValidationError):
        changed(bundle.envelope, signature=replacement)


def test_policy_and_signature_tampering_fail_real_crypto_verification(bundle):
    for tampered in (
        changed(bundle.envelope, policy=changed(bundle.policy, change_reason="Tampered reason")),
        changed(bundle.envelope, signature=base64.b64encode(b"x" * 64).decode()),
    ):
        with pytest.raises(ValueError, match="signature"):
            api().verify_policy_signature(tampered, (bundle.administrator,), verified_at=NOW)


@pytest.mark.parametrize("field,value", [("authority_id", "another-authority"),
                                          ("key_id", "another-administrator")])
def test_administrator_selection_binds_authority_and_key_id(bundle, field, value):
    with pytest.raises(ValueError):
        api().verify_policy_signature(bundle.envelope, (changed(bundle.administrator, **{field: value}),),
                                      verified_at=NOW)


def test_same_administrator_labels_with_the_wrong_key_do_not_verify(bundle):
    root = changed(bundle.administrator, public_key_pem=pem(Ed25519PrivateKey.generate()))
    with pytest.raises(ValueError, match="signature"):
        api().verify_policy_signature(bundle.envelope, (root,), verified_at=NOW)


def test_administrator_registry_is_external_and_rejects_duplicates_and_aliases(bundle):
    module = api()
    assert module.parse_administrators_bytes(b"[]") == ()
    with pytest.raises(ValueError):
        module.verify_policy_signature(bundle.envelope, (), verified_at=NOW)
    for extra in (bundle.administrator, changed(bundle.administrator, key_id="alias-administrator"),
                  changed(bundle.administrator, public_key_pem=pem(Ed25519PrivateKey.generate()))):
        entries = (bundle.administrator, extra)
        with pytest.raises(ValueError):
            module.parse_administrators_bytes(canonical_json_bytes([entry.model_dump(mode="json") for entry in entries]))
        with pytest.raises(ValueError):
            module.verify_policy_signature(bundle.envelope, entries, verified_at=NOW)


def test_any_configured_administrator_key_cannot_alias_an_approval_key(bundle):
    module = api()
    alias_root = changed(bundle.administrator, key_id="aliased-other-root", authority_id="other-authority",
                         public_key_pem=bundle.signers[0].public_key_pem)
    with pytest.raises(ValueError, match="alias|separate|distinct"):
        module.verify_policy_signature(bundle.envelope, (bundle.administrator, alias_root), verified_at=NOW)
    root_as_signer = changed(bundle.signers[0], public_key_pem=bundle.administrator.public_key_pem)
    grant = changed(bundle.grants[0], signer=root_as_signer)
    policy = changed(bundle.policy, grants=(grant, bundle.grants[1]))
    with pytest.raises(ValueError, match="alias|separate|distinct"):
        module.verify_policy_signature(signed(policy, bundle.administrator_key), (bundle.administrator,), verified_at=NOW)


def test_verification_policy_time_is_start_inclusive_and_end_exclusive(bundle):
    module = api()
    assert module.verify_policy_signature(bundle.envelope, (bundle.administrator,), verified_at=bundle.policy.issued_at)
    assert module.verify_policy_signature(bundle.envelope, (bundle.administrator,),
                                          verified_at=bundle.policy.expires_at - timedelta(microseconds=1))
    for at in (bundle.policy.issued_at - timedelta(microseconds=1), bundle.policy.expires_at):
        with pytest.raises(ValueError):
            module.verify_policy_signature(bundle.envelope, (bundle.administrator,), verified_at=at)


def test_administrator_validity_must_cover_issue_verification_and_entire_policy(bundle):
    module = api()
    matching = changed(bundle.administrator, valid_from=bundle.policy.issued_at, valid_until=bundle.policy.expires_at)
    assert module.verify_policy_signature(bundle.envelope, (matching,), verified_at=NOW)
    for root in (changed(matching, valid_from=bundle.policy.issued_at + timedelta(microseconds=1)),
                 changed(matching, valid_until=bundle.policy.expires_at - timedelta(microseconds=1)),
                 changed(matching, valid_until=NOW), changed(matching, valid_from=NOW + timedelta(seconds=1))):
        with pytest.raises(ValueError):
            module.verify_policy_signature(bundle.envelope, (root,), verified_at=NOW)


def test_historical_verification_uses_only_the_supplied_clock(bundle):
    old_issue = datetime(2000, 1, 1, tzinfo=timezone.utc)
    policy = changed(bundle.policy, issued_at=old_issue, expires_at=old_issue + timedelta(days=2), grants=())
    administrator = changed(bundle.administrator, valid_from=old_issue, valid_until=old_issue + timedelta(days=3))
    envelope = signed(policy, bundle.administrator_key)
    receipt = api().verify_policy_signature(envelope, (administrator,), verified_at=old_issue + timedelta(days=1))
    assert receipt["clinical_release_allowed"] is False
    with pytest.raises(ValueError):
        api().verify_policy_signature(envelope, (administrator,))


def test_grant_authorization_returns_exact_input_order_and_configured_assertions(bundle):
    receipt = api().authorize_signers(bundle.policy, "synthetic-study", tuple(reversed(bundle.signers)), NOW)
    expected = [{"grant_id": grant.grant_id, "study_id": grant.study_id,
                 "key_id": grant.signer.key_id, "subject_id": grant.signer.subject_id,
                 "role": grant.signer.role, "public_key_sha256": fingerprint(grant.signer.public_key_pem),
                 "enrollment_ref": grant.enrollment_ref,
                 "valid_from": grant.model_dump(mode="json")["valid_from"],
                 "valid_until": grant.model_dump(mode="json")["valid_until"]}
                for grant in reversed(bundle.grants)]
    assert receipt == expected
    assert api().authorize_signers(bundle.policy.model_dump(mode="json"), "synthetic-study",
                                   [signer.model_dump(mode="json") for signer in reversed(bundle.signers)], NOW) == expected


@pytest.mark.parametrize("substitution", ["key_id", "subject_id", "role", "public_key_pem", "study"])
def test_authorization_rejects_key_subject_role_and_study_substitution(bundle, substitution):
    replacements = {"key_id": "unknown-key", "subject_id": "someone-else", "role": "reviewer",
                    "public_key_pem": pem(Ed25519PrivateKey.generate())}
    signer = bundle.signers[0] if substitution == "study" else changed(
        bundle.signers[0], **{substitution: replacements[substitution]})
    study = "wrong-study" if substitution == "study" else "synthetic-study"
    with pytest.raises(ValueError):
        api().authorize_signers(bundle.policy, study, (signer,), NOW)


def test_authorization_compares_underlying_key_not_pem_whitespace(bundle):
    signer = changed(bundle.signers[0], public_key_pem=bundle.signers[0].public_key_pem + "\n")
    assert api().authorize_signers(bundle.policy, "synthetic-study", (signer,), NOW)[0]["grant_id"] == "author-grant"


def test_authorization_enforces_policy_and_grant_lifetimes(bundle):
    module = api()
    grant = changed(bundle.grants[0], valid_from=NOW, valid_until=NOW + timedelta(hours=1))
    policy = changed(bundle.policy, grants=(grant,))
    assert module.authorize_signers(policy, "synthetic-study", (bundle.signers[0],), NOW)
    assert module.authorize_signers(policy, "synthetic-study", (bundle.signers[0],),
                                    grant.valid_until - timedelta(microseconds=1))
    for at in (NOW - timedelta(microseconds=1), grant.valid_until,
               policy.issued_at - timedelta(microseconds=1), policy.expires_at):
        with pytest.raises(ValueError):
            module.authorize_signers(policy, "synthetic-study", (bundle.signers[0],), at)


def test_authorization_requires_no_signature_but_makes_no_release_claim(bundle):
    unsigned_policy = changed(bundle.policy, change_reason="Configured grants examined without signature proof")
    receipts = api().authorize_signers(unsigned_policy, "synthetic-study", bundle.signers, NOW)
    assert all("clinical_release_allowed" not in receipt and "signature" not in receipt for receipt in receipts)


def test_unvalidated_model_copies_are_revalidated_at_both_boundaries(bundle):
    module = api()
    invalid_policy = bundle.policy.model_copy(update={"revision": True})
    forged = bundle.envelope.model_copy(update={"policy": invalid_policy})
    with pytest.raises(ValidationError):
        module.verify_policy_signature(forged, (bundle.administrator,), verified_at=NOW)
    with pytest.raises(ValidationError):
        module.authorize_signers(invalid_policy, "synthetic-study", bundle.signers, NOW)
    invalid_root = bundle.administrator.model_copy(update={"valid_from": NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError):
        module.verify_policy_signature(bundle.envelope, (invalid_root,), verified_at=NOW)
    invalid_signer = bundle.signers[0].model_copy(update={"role": "administrator"})
    with pytest.raises(ValidationError):
        module.authorize_signers(bundle.policy, "synthetic-study", (invalid_signer,), NOW)


def test_verification_and_authorization_reject_naive_or_string_clocks(bundle):
    for at in (NOW.replace(tzinfo=None), NOW.isoformat(), 0, True):
        with pytest.raises(ValueError):
            api().verify_policy_signature(bundle.envelope, (bundle.administrator,), verified_at=at)
        with pytest.raises(ValueError):
            api().authorize_signers(bundle.policy, "synthetic-study", bundle.signers, at)


@pytest.mark.parametrize("parser_name", ["parse_signed_policy_bytes", "parse_administrators_bytes"])
@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}',
                                b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                                b'{"x":1e999}', b'{"x":"\\ud800"}', b'\xff',
                                b'[]' + b' ' * (64 * 1024), b'"wrong-shape"'])
def test_json_parsers_reject_unsafe_or_oversized_payloads(parser_name, raw):
    with pytest.raises((ValueError, UnicodeError)):
        getattr(api(), parser_name)(raw)


@pytest.mark.parametrize("parser_name", ["parse_signed_policy_bytes", "parse_administrators_bytes"])
def test_json_parser_boundaries_are_bytes_only(parser_name):
    for raw in ("[]", bytearray(b"[]"), {"x": 1}):
        with pytest.raises(TypeError):
            getattr(api(), parser_name)(raw)


def test_json_unknown_and_nonfinite_fields_do_not_reach_signature_checks(bundle):
    module = api()
    for value in (float("nan"), float("inf"), float("-inf")):
        supplied = {**bundle.envelope.model_dump(mode="json"), "unexpected": value}
        with pytest.raises(ValueError):
            module.verify_policy_signature(supplied, (bundle.administrator,), verified_at=NOW)
    unknown = {**bundle.administrator.model_dump(mode="json"), "trusted": True}
    with pytest.raises(ValidationError):
        module.parse_administrators_bytes(canonical_json_bytes([unknown]))
    envelope = {**bundle.envelope.model_dump(mode="json"), "trusted": True}
    with pytest.raises(ValidationError):
        module.parse_signed_policy_bytes(canonical_json_bytes(envelope))


def test_valid_envelopes_with_duplicate_json_keys_are_rejected_before_validation(bundle):
    encoded = bundle.envelope.canonical_bytes()
    duplicate_revision = encoded.replace(b'"revision":1', b'"revision":1,"revision":1')
    duplicate_signature = encoded[:-1] + b',"signature":' + json.dumps(bundle.envelope.signature).encode() + b'}'
    for raw in (duplicate_revision, duplicate_signature):
        with pytest.raises(ValueError, match="Duplicate JSON key"):
            api().parse_signed_policy_bytes(raw)
    administrator = bundle.administrator.canonical_bytes()
    duplicate_root = administrator.replace(b'"key_id":"administrator-key"',
                                          b'"key_id":"administrator-key","key_id":"administrator-key"')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        api().parse_administrators_bytes(b'[' + duplicate_root + b']')


def test_json_byte_limit_is_inclusive_for_valid_content(bundle):
    for parser, encoded in (
        (api().parse_signed_policy_bytes, bundle.envelope.canonical_bytes()),
        (api().parse_administrators_bytes, b'[' + bundle.administrator.canonical_bytes() + b']'),
    ):
        padded = encoded + b' ' * (64 * 1024 - len(encoded))
        assert parser(padded)
        with pytest.raises(ValueError, match="64 KiB"):
            parser(padded + b' ')


def test_sequential_grants_cannot_make_one_scope_ambiguous(bundle):
    later = changed(bundle.grants[0], grant_id="later-author-grant",
                    valid_from=bundle.grants[0].valid_until, valid_until=bundle.policy.expires_at)
    with pytest.raises(ValidationError, match="unambiguous"):
        changed(bundle.policy, grants=(*bundle.grants, later))
