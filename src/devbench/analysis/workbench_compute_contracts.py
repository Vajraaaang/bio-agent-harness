"""Offline Workbench compute contracts. No providers or service entry points."""

import json
from datetime import datetime
from hashlib import sha256

from cryptography.exceptions import InvalidSignature
from jsonschema import ValidationError

from devbench.engine.provenance import canonical_json


MAX_FILE_BYTES = 8_000_000
MAX_JOB_BYTES = 16_000_000
MEMORY_RESERVE = 256 * 1024 * 1024
MANIFEST_DOMAIN = b"devbench.run-manifest/1\x00"
RECEIPT_DOMAIN = b"devbench.compute-verification/1\x00"


def digest_json(value):
    return sha256(canonical_json(value)).hexdigest()


def sign_manifest(manifest, *, issuer_key_id, private_key):
    """Freeze a JSON snapshot before producing a signed approval envelope."""
    body = canonical_json(manifest)
    return {
        "format": "devbench.manifest-signature/1",
        "manifest": json.loads(body),
        "manifest_sha256": sha256(body).hexdigest(),
        "issuer_key_id": issuer_key_id,
        "signature_ed25519_hex": private_key.sign(MANIFEST_DOMAIN + body).hex(),
    }


def _verify_signature(record, body, domain, trusted_keys):
    key = trusted_keys[record["issuer_key_id"]]
    key.verify(bytes.fromhex(record["signature_ed25519_hex"]), domain + body)


def verify_manifest(approved, *, trusted_keys, validator, now):
    """Verify the approved record and return a detached manifest snapshot."""
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise PermissionError("INVALID_APPROVAL")
    try:
        approved = json.loads(canonical_json(approved))
        validator.validate(approved)
        manifest = approved["manifest"]
        body = canonical_json(manifest)
        if sha256(body).hexdigest() != approved["manifest_sha256"]:
            raise ValueError("Manifest digest mismatch")
        _verify_signature(approved, body, MANIFEST_DOMAIN, trusted_keys)
        issued, expires, deadline = map(datetime.fromisoformat, (
            manifest["issued_at_utc"], manifest["approval_expires_at_utc"],
            manifest["compute_profile"]["deadline_at_utc"],
        ))
    except (InvalidSignature, ValidationError, KeyError, TypeError, ValueError) as error:
        raise PermissionError("INVALID_APPROVAL") from error
    if not issued <= now < expires <= deadline:
        raise PermissionError("APPROVAL_EXPIRED")
    if (deadline - issued).total_seconds() != manifest["compute_profile"]["deadline_seconds"]:
        raise PermissionError("INVALID_WATCHDOG_DEADLINE")
    if manifest["parameters_sha256"] != digest_json(manifest["effective_parameters"]):
        raise PermissionError("INVALID_PARAMETER_DIGEST")
    for records, key in ((manifest["inputs"], "role"), (manifest["allowed_outputs"], "name")):
        if len({record[key] for record in records}) != len(records):
            raise PermissionError("INVALID_APPROVAL")
    return manifest


def check_direct_upload(sizes, available_ram, *, per_file_bytes=MAX_FILE_BYTES, total_input_bytes=MAX_JOB_BYTES):
    sizes = tuple(sizes)
    if (
        type(available_ram) is not int or available_ram < 0
        or type(per_file_bytes) is not int or not 0 < per_file_bytes <= MAX_FILE_BYTES
        or type(total_input_bytes) is not int or not 0 < total_input_bytes <= MAX_JOB_BYTES
        or any(type(size) is not int or not 0 <= size <= per_file_bytes for size in sizes)
        or sum(sizes) > total_input_bytes
    ):
        raise ValueError("DIRECT_UPLOAD_LIMIT_EXCEEDED")
    usable = max(0, available_ram - MEMORY_RESERVE)
    for size in sizes:
        base64_bytes = 4 * ((size + 2) // 3)
        if size + 2 * (base64_bytes + 65_536) > usable:
            raise ValueError("DIRECT_UPLOAD_LIMIT_EXCEEDED")


def verify_input_chunks(entry, chunks):
    """Model the VM's bounded stream check before adapter invocation."""
    checksum = sha256()
    size = 0
    for chunk in chunks:
        size += len(chunk)
        if size > entry["size_bytes"]:
            raise ValueError("INPUT_INTEGRITY_FAILURE")
        checksum.update(chunk)
    if size != entry["size_bytes"] or checksum.hexdigest() != entry["sha256"]:
        raise ValueError("INPUT_INTEGRITY_FAILURE")


def verify_staged_result(
    manifest, result, staged, *, validator, expected_dispatch_id, expected_runtime_id,
    expected_runtime_observation_sha256, validate_output,
):
    """Verify a synthetic staged inventory against the originating invocation."""
    validator.validate(result)
    if (
        result["manifest_sha256"] != digest_json(manifest)
        or result["job_id"] != manifest["job_id"]
        or result["execution_tier"] != manifest["backend"]
        or result["dispatch_id"] != expected_dispatch_id
        or result["runtime_id"] != expected_runtime_id
        or result["runtime_observation_sha256"] != expected_runtime_observation_sha256
    ):
        raise ValueError("RESULT_BINDING_MISMATCH")
    ports = {port["name"]: port for port in manifest["allowed_outputs"]}
    receipts = {entry["name"]: entry for entry in result["outputs"]}
    required = {name for name, port in ports.items() if port["required"]}
    if (
        len(receipts) != len(result["outputs"])
        or not required <= staged.keys() <= ports.keys()
        or receipts.keys() != staged.keys()
    ):
        raise ValueError("RESULT_INVENTORY_MISMATCH")
    for name, body in staged.items():
        receipt = receipts[name]
        if (
            type(body) is not bytes
            or not len(body) == receipt["size_bytes"] <= ports[name]["max_bytes"]
            or sha256(body).hexdigest() != receipt["sha256"]
        ):
            raise ValueError("RESULT_INTEGRITY_FAILURE")
    for name, body in staged.items():
        validate_output(ports[name], body)
    return json.loads(canonical_json(result))


def sign_verification_receipt(unsigned_receipt, *, private_key):
    """Sign every receipt field, including the tier and output inventory."""
    if "signature_ed25519_hex" in unsigned_receipt:
        raise ValueError("Expected an unsigned verification receipt")
    body = canonical_json(unsigned_receipt)
    return {
        **json.loads(body),
        "signature_ed25519_hex": private_key.sign(RECEIPT_DOMAIN + body).hex(),
    }


def verify_verification_receipt(receipt, result, *, trusted_keys, validator):
    try:
        receipt = json.loads(canonical_json(receipt))
        validator.validate(receipt)
        unsigned = {name: value for name, value in receipt.items() if name != "signature_ed25519_hex"}
        _verify_signature(receipt, canonical_json(unsigned), RECEIPT_DOMAIN, trusted_keys)
        if (
            receipt["result_root_sha256"] != digest_json(result)
            or receipt["manifest_sha256"] != result["manifest_sha256"]
            or receipt["execution_tier"] != result["execution_tier"]
            or receipt["outputs"] != result["outputs"]
        ):
            raise ValueError("Verification receipt binding mismatch")
    except (InvalidSignature, ValidationError, KeyError, TypeError, ValueError) as error:
        raise PermissionError("INVALID_VERIFICATION_RECEIPT") from error
    return receipt
