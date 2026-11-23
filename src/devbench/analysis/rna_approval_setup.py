"""Inert public-only setup for synthetic workflow-generation grant verification.

Private key creation and signing belong to the human's external tool. Nothing
here enrolls an issuer, constructs an authority or qualifies/dispatches a route.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from devharness_core.provenance import canonical_json
from devbench.study.evidence import _read, decode_json
from .checkpoints import digest
from .episode_proposals import _absolute, require_disjoint_output
from .rna_scientific_trial_contract import (
    _instant, trial_binding, trial_purpose_allocation, validate_trial_request,
)

_PROPOSAL_FORMAT = "devbench.rna-research-public-issuer-proposal/1"
_GRANT_FORMAT = "devbench.rna-trial-authority-grant/1"
_SCOPE = {"dataset_classification": "synthetic", "purpose": "workflow_generation"}
_PROPOSAL_FIELDS = {"format", "issuer_id", "public_key_hex", "fingerprint_sha256", "enabled", "scope"}
_UNSIGNED_FIELDS = {"format", "issuer_id", "grant_id", "trial_id", "purpose", "binding_sha256",
    "approver_id", "roles_sha256", "allocation_sha256", "engineering_sha256", "expires_at"}
_PRIVATE = re.compile(r"-----\s*(?:BEGIN|END)|PRIVATE[\s_-]*KEY", re.IGNORECASE)


def _public_material(value):
    if type(value) is str and _PRIVATE.search(value):
        raise ValueError("Only public research setup material is accepted")
    if type(value) is dict:
        for key, item in value.items():
            _public_material(key)
            _public_material(item)
    elif type(value) is list:
        for item in value:
            _public_material(item)


def _name(value):
    if type(value) is not str or not value.strip() or len(value) > 200:
        raise ValueError("A bounded public research identity is required")
    _public_material(value)
    return value


def _hex(value, size):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{" + str(size * 2) + r"}", value) is None:
        raise ValueError("A public Ed25519 value in exact lowercase hex is required")
    return bytes.fromhex(value)


def propose_public_issuer(*, issuer_id, public_key_hex):
    """Describe a declared public key; the proposal remains disabled/uninstalled."""
    issuer_id = _name(issuer_id)
    key = Ed25519PublicKey.from_public_bytes(_hex(public_key_hex, 32))
    fingerprint = hashlib.sha256(key.public_bytes(serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    return {"format": _PROPOSAL_FORMAT, "issuer_id": issuer_id, "public_key_hex": public_key_hex,
        "fingerprint_sha256": fingerprint, "enabled": False, "scope": dict(_SCOPE)}


def load_public_issuer(proposal, *, expected_issuer_id, accepted_fingerprint_sha256):
    """Return a verification key only after independently supplied root acceptance."""
    if (type(proposal) is not dict or set(proposal) != _PROPOSAL_FIELDS
            or proposal["enabled"] is not False or type(proposal["scope"]) is not dict):
        raise ValueError("A closed disabled public issuer proposal is required")
    _public_material(proposal)
    expected = propose_public_issuer(issuer_id=proposal["issuer_id"], public_key_hex=proposal["public_key_hex"])
    if proposal != expected:
        raise PermissionError("Public issuer proposal scope or fingerprint differs")
    if proposal["issuer_id"] != _name(expected_issuer_id):
        raise PermissionError("Public issuer differs from the independently expected issuer")
    accepted = _hex(accepted_fingerprint_sha256, 32).hex()
    if not hmac.compare_digest(accepted, expected["fingerprint_sha256"]):
        raise PermissionError("Public issuer fingerprint lacks independent acceptance")
    return Ed25519PublicKey.from_public_bytes(_hex(proposal["public_key_hex"], 32))


def prepare_generation_grant(request, *, issuer_id, grant_id, expires_at, now=None):
    """Prepare the existing unsigned subject, without adding approval/qualification."""
    if type(request) is not dict:
        raise ValueError("A closed synthetic research trial request is required")
    _public_material(request)
    try:
        request = validate_trial_request(request)
    except (ValueError, TypeError, KeyError):
        raise ValueError("A closed synthetic research trial request is required") from None
    if request["dataset"]["classification"] != "synthetic":
        raise PermissionError("Research setup is restricted to synthetic workflow generation")
    issuer_id, grant_id = _name(issuer_id), _name(grant_id)
    expires_at = _name(expires_at)
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or _instant(expires_at) <= current:
        raise PermissionError("Generation grant expired or validation time lacks timezone")
    return {"format": _GRANT_FORMAT, "issuer_id": issuer_id, "grant_id": grant_id,
        "trial_id": request["trial_id"], "purpose": "workflow_generation",
        "binding_sha256": trial_binding(request, "workflow_generation"),
        "approver_id": request["roles"]["operator"], "roles_sha256": digest(request["roles"]),
        "allocation_sha256": digest(trial_purpose_allocation(request, "workflow_generation")),
        "engineering_sha256": digest(request["engineering"]), "expires_at": expires_at}


def import_generation_signature(payload, signature_hex, *, request, proposal,
                                expected_issuer_id, accepted_fingerprint_sha256, now=None):
    """Verify an external detached signature and exact subject; never admit a run."""
    if type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS:
        raise ValueError("A closed unsigned generation grant is required")
    _public_material(payload)
    key = load_public_issuer(proposal, expected_issuer_id=expected_issuer_id,
        accepted_fingerprint_sha256=accepted_fingerprint_sha256)
    expected = prepare_generation_grant(request, issuer_id=expected_issuer_id,
        grant_id=payload["grant_id"], expires_at=payload["expires_at"], now=now)
    if payload != expected:
        raise PermissionError("Generation grant exact subject or purpose differs")
    try:
        key.verify(_hex(signature_hex, 64), canonical_json(payload))
    except (InvalidSignature, ValueError, TypeError):
        raise PermissionError("Detached generation signature is invalid") from None
    return {**expected, "signature": signature_hex}


class _PublicParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Public-only approval setup arguments are invalid.\n")


def _workspace_file(value, workspace):
    path = _absolute(value)
    if not path.is_relative_to(workspace):
        raise ValueError("Public setup files must remain inside the assigned workspace")
    return path


def main(argv=None):
    """Public artifact CLI; failures deliberately omit input and exception bodies."""
    parser = _PublicParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("propose-public-issuer", "prepare-generation", "import-generation-signature"):
        modes.add_argument("--" + mode, action="store_true")
    for name in ("workspace", "issuer-id", "output"):
        parser.add_argument("--" + name, required=True)
    for name in ("public-key-hex", "request", "grant-id", "expires-at", "proposal", "unsigned-payload",
                 "signature", "accepted-fingerprint"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    try:
        workspace = _absolute(args.workspace)
        if not workspace.is_dir():
            raise ValueError("An existing assigned workspace is required")
        if args.propose_public_issuer:
            needed = {"public_key_hex"}
        elif args.prepare_generation:
            needed = {"request", "grant_id", "expires_at"}
        else:
            needed = {"request", "proposal", "unsigned_payload", "signature", "accepted_fingerprint"}
        options = {"public_key_hex", "request", "grant_id", "expires_at", "proposal", "unsigned_payload",
            "signature", "accepted_fingerprint"}
        if any((getattr(args, name) is not None) != (name in needed) for name in options):
            raise ValueError("Use the exact public setup arguments for the selected operation")
        paths = {name: _workspace_file(getattr(args, name), workspace)
            for name in needed & {"request", "proposal", "unsigned_payload", "signature"}}
        output = _workspace_file(args.output, workspace)
        require_disjoint_output(output, paths.values())
        if args.propose_public_issuer:
            result = propose_public_issuer(issuer_id=args.issuer_id, public_key_hex=args.public_key_hex)
        else:
            request = decode_json(_read(paths["request"], 1_000_000))
            if args.prepare_generation:
                result = prepare_generation_grant(request, issuer_id=args.issuer_id,
                    grant_id=args.grant_id, expires_at=args.expires_at)
            else:
                signature = _read(paths["signature"], 64)
                if len(signature) != 64:
                    raise ValueError("A raw 64-byte detached signature is required")
                result = import_generation_signature(decode_json(_read(paths["unsigned_payload"], 1_000_000)),
                    signature.hex(), request=request, proposal=decode_json(_read(paths["proposal"], 16_000)),
                    expected_issuer_id=args.issuer_id, accepted_fingerprint_sha256=args.accepted_fingerprint)
        with output.open("xb") as handle:
            handle.write(canonical_json(result))
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        print("Public-only approval setup rejected; no artifact accepted.", file=sys.stderr)
        return 2
    print(json.dumps({"status": "public_artifact_prepared", "model_calls": 0, "data_runs": 0}))
    return 0
