"""Versioned canonical JSON encoding for scientific evidence."""
import json
import math

MAX_JSON_BYTES = 16_000_000


def canonical_json(value) -> bytes:
    """Encode strict, finite JSON deterministically; this is not RFC 8785 JCS.

    The versioned contract uses Python's JSON number representation, sorted string
    keys, compact separators and UTF-8 without Unicode normalization. Lists retain
    their order. Non-string keys and implicit Python-to-JSON coercions are rejected.
    """
    def check(item):
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("Nonfinite JSON numbers are not permitted")
        elif item is None or type(item) in (str, int, bool):
            return
        elif type(item) is list:
            for child in item:
                check(child)
        elif type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise TypeError("JSON object keys must be strings")
                check(child)
        else:
            raise TypeError(f"Unsupported JSON value: {type(item).__name__}")

    try:
        check(value)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except RecursionError as error:
        raise ValueError("Cyclic or excessively nested JSON") from error
    if len(encoded) > MAX_JSON_BYTES:
        raise ValueError("JSON evidence exceeds the 16 MB bound")
    return encoded
