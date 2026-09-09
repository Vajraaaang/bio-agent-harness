"""Public key admission must exclude algebraically degenerate signing keys."""
from datetime import datetime, timedelta, timezone
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey, Ed25519PrivateKey
from devharness_controlled.trust import TrustedSigner, _public_key
from devharness_controlled.authority_contracts import AuthorityAdministrator

IDENTITY=b"\x01"+bytes(31)
P=2**255-19


def pem(raw):
    return Ed25519PublicKey.from_public_bytes(raw).public_bytes(
        serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.mark.parametrize("raw",[bytes(32),IDENTITY,(P-1).to_bytes(32,"little"),P.to_bytes(32,"little"),b"\xff"*32])
def test_weak_or_noncanonical_approval_key_is_rejected_at_admission(raw):
    with pytest.raises(ValueError,match="Ed25519"):
        TrustedSigner(key_id="key",subject_id="subject",role="author",public_key_pem=pem(raw))


def test_constant_signature_identity_key_cannot_be_an_administrator():
    now=datetime.now(timezone.utc)
    with pytest.raises(ValueError,match="Ed25519"):
        AuthorityAdministrator(authority_id="authority",key_id="admin",subject_id="administrator",public_key_pem=pem(IDENTITY),
                               valid_from=now-timedelta(days=1),valid_until=now+timedelta(days=1))


def test_generated_keys_still_verify_the_original_signature_algorithm():
    for _ in range(16):
        private=Ed25519PrivateKey.generate();raw=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        _public_key(pem(raw)).verify(private.sign(b"preserved approval bytes"),b"preserved approval bytes")


def test_mixed_order_key_is_rejected():
    from nacl.bindings import crypto_core_ed25519_add
    private=Ed25519PrivateKey.generate()
    prime=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    mixed=crypto_core_ed25519_add(prime,(P-1).to_bytes(32,"little"))
    assert mixed!=prime
    with pytest.raises(ValueError,match="Ed25519"):_public_key(pem(mixed))


def test_documented_subgroup_check_handles_older_permissive_backend(monkeypatch):
    from devharness_controlled import ed25519_validation as validator
    from nacl.bindings import crypto_core_ed25519_add
    private=Ed25519PrivateKey.generate()
    prime=private.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    torsion=(P-1).to_bytes(32,"little")
    mixed=crypto_core_ed25519_add(prime,torsion)
    multiply=validator.crypto_scalarmult_ed25519_noclamp
    # Reproduce an older backend accepting the mixed-order input. L-1 is
    # even, so multiplication removes this order-two torsion component.
    def permissive_multiply(scalar,point):
        assert scalar==validator._ORDER_MINUS_ONE and point==mixed
        return multiply(scalar,prime)
    monkeypatch.setattr(validator,"crypto_core_ed25519_is_valid_point",lambda point:True)
    monkeypatch.setattr(validator,"crypto_scalarmult_ed25519_noclamp",permissive_multiply)
    with pytest.raises(ValueError,match="Ed25519"):validator.validate_public_point(mixed)


def test_point_backend_failure_does_not_fall_back_to_signature_only(monkeypatch):
    from devharness_controlled import ed25519_validation as validator
    from nacl.exceptions import UnavailableError
    raw=Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    def missing_backend(point):raise UnavailableError("synthetic unavailable group API")
    monkeypatch.setattr(validator,"crypto_core_ed25519_is_valid_point",missing_backend)
    with pytest.raises(ValueError,match="Ed25519"):validator.validate_public_point(raw)
