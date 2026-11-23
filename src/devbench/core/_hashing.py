"""Shared digest and JSON encoding rules for retained coordinator records."""

import hashlib
import json
import math
from typing import Any

from rna_kernels.canonical import canonical_json


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def utf8_sha256(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def contract_json_bytes(value: Any) -> bytes:
    """The coordinator-contract encoding, including its legacy JSON coercions."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def decode_receipt_json(content: bytes | bytearray | str) -> Any:
    """Read receipt JSON while rejecting duplicate keys and nonfinite numbers."""
    def unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate receipt JSON keys")
            result[key] = value
        return result

    value = json.loads(content, object_pairs_hook=unique)
    contract_json_bytes(value)  # Check NaN, Infinity and overflowed exponents.
    return value


def workflow_json(value: Any) -> str:
    """Encode the finite, depth- and size-bounded operation ledger payload."""
    count = 0

    def check(item: Any, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if depth > 32 or count > 4096:
            raise ValueError("Workflow JSON structure exceeds bounds")
        if (item is None or type(item) in (str, bool, int)
                or type(item) is float and math.isfinite(item)):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValueError("Workflow payload must contain finite JSON values")

    check(value)
    try:
        encoded = contract_json_bytes(value)
        if len(encoded) > 65536:
            raise ValueError("Workflow JSON exceeds 64 KiB")
    except (UnicodeError, OverflowError) as exc:
        raise ValueError("Invalid workflow JSON") from exc
    return encoded.decode("utf-8")
