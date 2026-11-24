"""Admit only canonical nonzero prime-order Ed25519 public points.

Signature verification remains the existing cryptography Ed25519 algorithm.
Group checks use libsodium; no private scalar or homemade point arithmetic is used.
The L-1/add check also addresses older libsodium mixed-order point validation:
https://doc.libsodium.org/advanced/point-arithmetic
"""
from nacl.bindings import (crypto_core_ed25519_add, crypto_core_ed25519_is_valid_point,
                           crypto_scalarmult_ed25519_noclamp)
from nacl.exceptions import CryptoError

_IDENTITY = b"\x01" + bytes(31)
_ORDER_MINUS_ONE = (2**252 + 27742317777372353535851937790883648493 - 1).to_bytes(32, "little")


def validate_public_point(raw: bytes) -> None:
    if type(raw) is not bytes or len(raw) != 32:
        raise ValueError("Ed25519 public point must contain exactly 32 bytes")
    try:
        valid = crypto_core_ed25519_is_valid_point(raw)
        if valid:
            # noclamp requires a scalar strictly below L. (L-1)P + P = LP.
            inverse = crypto_scalarmult_ed25519_noclamp(_ORDER_MINUS_ONE, raw)
            valid = crypto_core_ed25519_add(inverse, raw) == _IDENTITY
    except CryptoError as error:
        raise ValueError("Ed25519 public point validation failed") from error
    if not valid:
        raise ValueError("Ed25519 public key must be a canonical nonzero prime-order point")
