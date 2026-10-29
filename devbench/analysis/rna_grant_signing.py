"""Purpose-bound use of the existing local research signer.

This library is invoked only by the trusted coordinator, never from participant
JSON. It does not enroll a root, qualify a runtime, consume a grant, or launch a
model or numerical method. Subject digests and public-root acceptance must come
from deliberately accepted protected coordinator configuration. There is no CLI,
key creation, signing callback, arbitrary-message operation, or private-key file.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hmac

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from devharness_core.provenance import canonical_json
from .checkpoints import digest
from .rna_approval_setup import _UNSIGNED_FIELDS, _hex, _name, _public_material, load_public_issuer
from .rna_scientific_trial_contract import FORMAT_V3, _instant, trial_binding, trial_purpose_allocation, validate_trial_request

_ISSUER_ID = "local-research-a84f56d983ea4bdca67595dba896371c"
_SERVICE = "devbench.local-research.ed25519.20261020.a84f56d983ea4bdca67595dba896371c"
_ACCOUNT = "agent-provisioned-user-authorized-research-a84f56d983ea4bdca67595dba896371c"


def unsigned_trial_grant_subject(request, *, purpose, issuer_id, grant_id, expires_at, now=None):
    """Existing closed grant subject; this function confers no approval authority."""
    request = validate_trial_request(request)
    _public_material(request)
    expires_at = _name(expires_at)
    current = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or _instant(expires_at) <= current:
        raise PermissionError("Grant expired or validation time lacks timezone")
    return {"format": "devbench.rna-trial-authority-grant/1", "issuer_id": _name(issuer_id),
        "grant_id": _name(grant_id), "trial_id": request["trial_id"], "purpose": purpose,
        "binding_sha256": trial_binding(request, purpose), "approver_id": request["roles"]["operator"],
        "roles_sha256": digest(request["roles"]),
        "allocation_sha256": digest(trial_purpose_allocation(request, purpose)),
        "engineering_sha256": digest(request["engineering"]), "expires_at": _name(expires_at)}


def prepare_research_grant(request, *, purpose, issuer_id, grant_id, expires_at, now=None):
    """Prepare exact v3 local research scope, without treating JSON as qualification."""
    request = validate_trial_request(request)
    if (request["format"] != FORMAT_V3 or request["engineering"]["route"] != "codex_chatgpt"
            or request["dataset"]["classification"] not in {"synthetic", "public"}
            or request["engineering"]["qualified_boundary"] is None or issuer_id != _ISSUER_ID):
        raise PermissionError("Existing research signer requires an exact native v3 public or synthetic subject")
    if purpose == "trial_execution" and (request["allocation"]["numeric"] is None
            or request["scientific_context"] is None or request["assessment_plan"] is None
            or request["comparison"] is None or request["workflow"] is None):
        raise PermissionError("Execution requires separate numeric, workflow and scientific subject")
    return unsigned_trial_grant_subject(request, purpose=purpose, issuer_id=issuer_id,
        grant_id=grant_id, expires_at=expires_at, now=now)


def verify_authority_signature(payload, signature_hex, *, public_key):
    """Existing Ed25519 canonical closed-subject verification, with no admission."""
    if (type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS
            or payload["format"] != "devbench.rna-trial-authority-grant/1"
            or not isinstance(public_key, Ed25519PublicKey)):
        raise PermissionError("Authenticated closed issuer grant is required")
    try:
        if type(signature_hex) is not str or len(signature_hex) != 128:
            raise ValueError("Wrong signature length")
        signature = bytes.fromhex(signature_hex)
        if len(signature) != 64:
            raise ValueError("Wrong signature length")
        public_key.verify(signature, canonical_json(payload))
    except (InvalidSignature, ValueError, TypeError):
        raise PermissionError("Authenticated grant signature is invalid") from None
    return {**payload, "signature": signature_hex}


def verify_research_signature(payload, signature_hex, *, request, purpose, public_key,
                              expected_issuer_id, now=None):
    """Check the exact research subject through the shared signature verifier."""
    if type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS:
        raise PermissionError("An exact closed unsigned research grant is required")
    expected = prepare_research_grant(request, purpose=purpose, issuer_id=expected_issuer_id,
        grant_id=payload["grant_id"], expires_at=payload["expires_at"], now=now)
    if payload != expected:
        raise PermissionError("Research grant exact subject or purpose differs")
    return verify_authority_signature(expected, signature_hex, public_key=public_key)


def sign_approved_research_grant(request, payload, *, authority, proposal,
                                 approved_subject_sha256, accepted_fingerprint_sha256):
    """Sign one deliberately approved current subject using the existing item.

    The coordinator must release a bounded dedicated signing process before use.
    No caller-supplied signing or qualification callbacks are accepted. The
    authority transaction checks current admission and unconsumed state before
    private material is accessed. A returned grant still requires the ordinary
    authenticated authority consumption and dispatch path.
    """
    from .rna_trial_authority import AuthenticatedTrialAuthority
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import resource

    if type(authority) is not AuthenticatedTrialAuthority:
        raise TypeError("The actual registered authenticated trial authority is required")
    if not hasattr(authority, "grant_signing_preflight"):
        raise PermissionError("Reviewed exact-subject authority signing preflight is unavailable")
    # Freeze public inputs before validation; later caller mutations cannot alter
    # either the signed bytes or the runtime/subject checks.
    request = validate_trial_request(request)
    if type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS:
        raise PermissionError("A closed unsigned research grant subject is required")
    import json
    payload = json.loads(canonical_json(payload))
    approved = _hex(approved_subject_sha256, 32).hex()
    if not hmac.compare_digest(digest(payload), approved):
        raise PermissionError("Grant subject lacks separate exact deliberate approval")
    expected = prepare_research_grant(request, purpose=payload["purpose"], issuer_id=_ISSUER_ID,
        grant_id=payload["grant_id"], expires_at=payload["expires_at"], now=authority.clock())
    if expected != payload:
        raise PermissionError("Approved research grant subject differs")
    accepted_key = load_public_issuer(proposal, expected_issuer_id=_ISSUER_ID,
        accepted_fingerprint_sha256=accepted_fingerprint_sha256)
    accepted_der = accepted_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    seed = None
    try:
        with authority.grant_signing_preflight(request, payload=payload) as current_key:
            current_der = current_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            if not hmac.compare_digest(current_der, accepted_der):
                raise PermissionError("Current authority root differs from the deliberately accepted existing signer")
            # Prevent accidental core dumps containing the in-memory seed.
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            with _MacOSExistingResearchItem() as store:
                seed = store._read_existing_seed()
                if type(seed) is not bytearray or len(seed) != 32:
                    raise PermissionError("Existing research signer material is unavailable")
                private = Ed25519PrivateKey.from_private_bytes(seed)
                actual_der = private.public_key().public_bytes(serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo)
                if not hmac.compare_digest(actual_der, accepted_der):
                    raise PermissionError("Existing research signer public fingerprint differs")
                # Check expiry and exact subject again immediately before signing.
                current = prepare_research_grant(request, purpose=payload["purpose"], issuer_id=_ISSUER_ID,
                    grant_id=payload["grant_id"], expires_at=payload["expires_at"], now=authority.clock())
                if current != payload or not hmac.compare_digest(digest(current), approved):
                    raise PermissionError("Approved research subject drifted before signing")
                authority._validated_subject(request, purpose=current["purpose"], binding_sha256=current["binding_sha256"])
                authority._grant_signing_scientific_preflight(request, purpose=current["purpose"], binding_sha256=current["binding_sha256"])
                signature = private.sign(canonical_json(current)).hex()
                result = verify_research_signature(current, signature, request=request, purpose=current["purpose"],
                    public_key=accepted_key, expected_issuer_id=_ISSUER_ID, now=authority.clock())
        return result
    except Exception:
        raise PermissionError("Existing research signer operation failed; no grant accepted") from None
    finally:
        if type(seed) is bytearray:
            seed[:] = b"\0" * len(seed)
        # This clears the mutable application buffer only. Python/crypto/CFData
        # temporary copies have no guaranteed erasure; the dedicated process
        # must terminate after the one attempt.


class _MacOSExistingResearchItem:
    """Fixed read-only Keychain query; no namespace or private-output parameters."""
    def __init__(self):
        import ctypes
        import sys
        if sys.platform != "darwin":
            raise PermissionError("Existing local research signer requires its original macOS Keychain")
        self._ct = ctypes
        self._refs, self._previous_ui = [], None
        self._sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self._cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        ptr, integer = ctypes.c_void_p, ctypes.c_int32
        for name, args in {
            "SecKeychainGetUserInteractionAllowed": [ctypes.POINTER(ctypes.c_ubyte)],
            "SecKeychainSetUserInteractionAllowed": [ctypes.c_ubyte],
            "SecKeychainCopyDefault": [ctypes.POINTER(ptr)],
            "SecKeychainGetStatus": [ptr, ctypes.POINTER(ctypes.c_uint32)],
            "SecItemCopyMatching": [ptr, ctypes.POINTER(ptr)],
        }.items():
            getattr(self._sec, name).argtypes, getattr(self._sec, name).restype = args, integer
        for name, (args, result) in {
            "CFStringCreateWithCString": ([ptr, ctypes.c_char_p, ctypes.c_uint32], ptr),
            "CFArrayCreate": ([ptr, ctypes.POINTER(ptr), ctypes.c_long, ptr], ptr),
            "CFDictionaryCreate": ([ptr, ctypes.POINTER(ptr), ctypes.POINTER(ptr), ctypes.c_long, ptr, ptr], ptr),
            "CFDataGetLength": ([ptr], ctypes.c_long), "CFDataGetBytePtr": ([ptr], ptr),
            "CFDataGetTypeID": ([], ctypes.c_ulong), "CFGetTypeID": ([ptr], ctypes.c_ulong),
            "CFRelease": ([ptr], None),
        }.items():
            getattr(self._cf, name).argtypes, getattr(self._cf, name).restype = args, result
        self._true = ctypes.c_void_p.in_dll(self._cf, "kCFBooleanTrue").value

    def _constant(self, name):
        return self._ct.c_void_p.in_dll(self._sec, name).value

    def _own(self, value):
        if not value:
            raise PermissionError("Existing research signer native operation failed")
        self._refs.append(value)
        return value

    def _string(self, value):
        return self._own(self._cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100))

    def _array(self, value):
        return self._own(self._cf.CFArrayCreate(None, (self._ct.c_void_p * 1)(value), 1, None))

    def _dictionary(self, pairs):
        keys = (self._ct.c_void_p * len(pairs))(*(self._constant(k) for k, _ in pairs))
        values = (self._ct.c_void_p * len(pairs))(*(v for _, v in pairs))
        return self._own(self._cf.CFDictionaryCreate(None, keys, values, len(pairs), None, None))

    def __enter__(self):
        try:
            state = self._ct.c_ubyte()
            if self._sec.SecKeychainGetUserInteractionAllowed(self._ct.byref(state)) != 0:
                raise PermissionError("Noninteractive research signing is unavailable")
            self._previous_ui = state.value
            if self._sec.SecKeychainSetUserInteractionAllowed(0) != 0:
                raise PermissionError("Noninteractive research signing is unavailable")
            if self._sec.SecKeychainGetUserInteractionAllowed(self._ct.byref(state)) != 0 or state.value:
                raise PermissionError("Noninteractive research signing is unavailable")
            keychain, status = self._ct.c_void_p(), self._ct.c_uint32()
            if self._sec.SecKeychainCopyDefault(self._ct.byref(keychain)) != 0 or not keychain.value:
                raise PermissionError("Original research Keychain is unavailable")
            self._keychain = self._own(keychain.value)
            if self._sec.SecKeychainGetStatus(self._keychain, self._ct.byref(status)) != 0 or status.value & 1 != 1:
                raise PermissionError("Original research Keychain is unavailable or locked")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise PermissionError("Existing research signer access failed; no grant accepted") from None

    def _read_existing_seed(self):
        query = self._dictionary([
            ("kSecClass", self._constant("kSecClassGenericPassword")),
            ("kSecAttrService", self._string(_SERVICE)), ("kSecAttrAccount", self._string(_ACCOUNT)),
            ("kSecUseAuthenticationUI", self._constant("kSecUseAuthenticationUIFail")),
            ("kSecMatchSearchList", self._array(self._keychain)),
            ("kSecMatchLimit", self._constant("kSecMatchLimitOne")), ("kSecReturnData", self._true),
        ])
        result = self._ct.c_void_p()
        code = self._sec.SecItemCopyMatching(query, self._ct.byref(result))
        if result.value:
            self._own(result.value)
        if (code != 0 or not result.value or self._cf.CFGetTypeID(result) != self._cf.CFDataGetTypeID()
                or self._cf.CFDataGetLength(result) != 32):
            raise PermissionError("Existing exact research signer item is unavailable")
        pointer = self._cf.CFDataGetBytePtr(result)
        if not pointer:
            raise PermissionError("Existing exact research signer item is unavailable")
        seed = bytearray(32)
        self._ct.memmove((self._ct.c_ubyte * 32).from_buffer(seed), pointer, 32)
        return seed

    def __exit__(self, exc_type, exc, traceback):
        for reference in reversed(self._refs):
            self._cf.CFRelease(reference)
        self._refs.clear()
        if self._previous_ui is not None:
            code = self._sec.SecKeychainSetUserInteractionAllowed(self._previous_ui)
            self._previous_ui = None
            if code != 0:
                raise PermissionError("Research signing interaction policy restoration failed")


def sign_approved_colab_qualification(request, payload, *, authority, proposal,
        approved_subject_sha256, accepted_fingerprint_sha256):
    """Sign only the separately approved one-job synthetic remote bootstrap.

    This entry requires a dedicated released coordinator process. It never
    creates/exports a key, consumes an execution grant, launches a job, invokes
    a model, or confers general Colab/airway qualification.
    """
    from .rna_trial_authority import AuthenticatedTrialAuthority
    from .colab_qualification_contract import validate_colab_qualification_request, unsigned_colab_qualification_grant_subject
    from .rna_scientific_trial_contract import bounded_source
    from devbench.study.evidence import decode_json
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import json
    import resource

    if type(authority) is not AuthenticatedTrialAuthority:
        raise TypeError('Actual registered authenticated trial authority is required')
    request = validate_colab_qualification_request(request, workspace=authority.workspace, now=authority.clock())
    job = decode_json(bounded_source(request['job_ref'], authority.workspace))
    if type(payload) is not dict or set(payload) != _UNSIGNED_FIELDS:
        raise PermissionError('Closed unsigned Colab grant subject is required')
    payload = json.loads(canonical_json(payload))
    approved = _hex(approved_subject_sha256,32).hex()
    if not hmac.compare_digest(digest(payload), approved):
        raise PermissionError('Colab grant subject lacks separate exact deliberate approval')
    expected = unsigned_colab_qualification_grant_subject(request, issuer_id=_ISSUER_ID,
        grant_id=payload['grant_id'], now=authority.clock())
    if payload != expected:
        raise PermissionError('Approved Colab subject or existing issuer differs')
    accepted_key = load_public_issuer(proposal, expected_issuer_id=_ISSUER_ID,
        accepted_fingerprint_sha256=accepted_fingerprint_sha256)
    accepted_der = accepted_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    seed = None
    try:
        with authority.colab_qualification_signing_preflight(request,payload=payload,job=job) as current_key:
            current_der = current_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            if not hmac.compare_digest(current_der,accepted_der):
                raise PermissionError('Current Colab authority differs from the accepted existing public root')
            resource.setrlimit(resource.RLIMIT_CORE,(0,0))
            with _MacOSExistingResearchItem() as store:
                seed = store._read_existing_seed()
                if type(seed) is not bytearray or len(seed) != 32:
                    raise PermissionError('Existing research signer material is unavailable')
                private = Ed25519PrivateKey.from_private_bytes(seed)
                actual_der = private.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                if not hmac.compare_digest(actual_der,accepted_der):
                    raise PermissionError('Existing signer public fingerprint differs')
                authority.colab_qualification_context.verify(request,workspace=authority.workspace,job=job,now=authority.clock())
                current = unsigned_colab_qualification_grant_subject(request,issuer_id=_ISSUER_ID,
                    grant_id=payload['grant_id'],now=authority.clock())
                if current != payload or not hmac.compare_digest(digest(current),approved):
                    raise PermissionError('Approved Colab subject drifted before signing')
                signature = private.sign(canonical_json(current)).hex()
                result = verify_authority_signature(current,signature,public_key=accepted_key)
                authority._validated_colab_qualification(request,result,job=job)
        return result
    except Exception:
        raise PermissionError('Existing research signer operation failed; no Colab grant accepted') from None
    finally:
        if type(seed) is bytearray:
            seed[:] = b'\0' * len(seed)
