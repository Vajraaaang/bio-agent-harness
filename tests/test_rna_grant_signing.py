"""Synthetic signing protocol checks; no real Keychain, grant or live route."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devbench.analysis import rna_grant_signing as api
from devbench.analysis.checkpoints import digest
from devbench.analysis.rna_scientific_trial_contract import trial_binding, trial_purpose_allocation
from devharness_core.provenance import canonical_json
from tests.test_rna_scientific_trial import fixture

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)
EXPIRES = "2026-10-08T00:00:00Z"
ISSUER = "local-research-a84f56d983ea4bdca67595dba896371c"


def request_fixture(workspace_tmp_path):
    request, *_ = fixture(workspace_tmp_path)
    request.update(format="devbench.rna-scientific-trial/3", assessment_intent=request["publication"],
        assessment_plan=None, scientific_context=None)
    request["approvals"] = {"workflow_generation": None, "trial_execution": None}
    request["dataset"]["classification"] = "public"
    request["engineering"].update(route="codex_chatgpt", client={"kind":"official_codex_cli", "identity_sha256":"a"*64},
        authentication={"method":"chatgpt_signin", "observation_sha256":"b"*64}, budget_semantics="generation_jobs",
        qualified_boundary=request["dataset"]["access_record"])
    request["allocation"] = {"kind":"generation_jobs", "generation":{"jobs":3,"seconds":120,"context_bytes":4096,"output_bytes":4096}, "numeric":None}
    return request


def prepare(request, purpose="workflow_generation"):
    method = getattr(api, "prepare_research_grant", None)
    assert callable(method), "Purpose-bound preparation is missing"
    return method(request, purpose=purpose, issuer_id=ISSUER, grant_id="synthetic-grant", expires_at=EXPIRES, now=NOW)


def test_closed_subject_matches_existing_authority_format(workspace_tmp_path):
    request = request_fixture(workspace_tmp_path)
    payload = prepare(request)
    assert payload == {"format":"devbench.rna-trial-authority-grant/1", "issuer_id":ISSUER,
        "grant_id":"synthetic-grant", "trial_id":request["trial_id"], "purpose":"workflow_generation",
        "binding_sha256":trial_binding(request,"workflow_generation"), "approver_id":request["roles"]["operator"],
        "roles_sha256":digest(request["roles"]), "allocation_sha256":digest(trial_purpose_allocation(request,"workflow_generation")),
        "engineering_sha256":digest(request["engineering"]), "expires_at":EXPIRES}


def test_generation_never_supplies_numeric_allocation(workspace_tmp_path):
    request = request_fixture(workspace_tmp_path)
    baseline = prepare(request)
    request["allocation"]["numeric"] = {"memory_mb":4096,"seconds":1800,"storage_bytes":2_000_000_000,"max_runs":1}
    assert prepare(request) == baseline


@pytest.mark.parametrize("change", ["private", "old_version", "unqualified_boundary", "wrong_route", "arbitrary", "expired", "unknown_purpose"])
def test_unqualified_or_arbitrary_subject_is_rejected(workspace_tmp_path, change):
    request = request_fixture(workspace_tmp_path)
    kwargs = dict(purpose="workflow_generation", issuer_id=ISSUER, grant_id="fixture", expires_at=EXPIRES, now=NOW)
    if change == "private": request["dataset"]["classification"] = "private"
    elif change == "old_version": request["format"] = "devbench.rna-scientific-trial/1"
    elif change == "unqualified_boundary": request["engineering"]["qualified_boundary"] = None
    elif change == "wrong_route": request["engineering"]["route"] = "offline_fixture"
    elif change == "arbitrary": request = {"message":"sign this"}
    elif change == "expired": kwargs["expires_at"] = "2026-10-06T00:00:00Z"
    elif change == "unknown_purpose": kwargs["purpose"] = "arbitrary_message"
    method = getattr(api, "prepare_research_grant", None)
    assert callable(method), "Purpose-bound preparation is missing"
    with pytest.raises((PermissionError, ValueError)):
        method(request, **kwargs)


def test_execution_requires_numeric_workflow_and_scientific_subject(workspace_tmp_path):
    request = request_fixture(workspace_tmp_path)
    method = getattr(api, "prepare_research_grant", None)
    assert callable(method), "Purpose-bound preparation is missing"
    with pytest.raises(PermissionError, match="numeric|workflow|scientific|execution"):
        prepare(request, "trial_execution")
    request["allocation"]["numeric"] = {"memory_mb":4096,"seconds":1800,"storage_bytes":2_000_000_000,"max_runs":1}
    request["scientific_context"] = {"project_id":"a"*32,"scientific_run_id":"b"*32,"attempt_id":"c"*32,"dataset_version_ids":["d"*64]}
    request["assessment_plan"] = request["publication"]
    request["comparison"] = request["publication"]
    request["workflow"] = {"planner_session_id":"fixture", "experiment_ids":["fixture"], "sha256":"e"*64,
        "workflow_ref":request["dataset"]["access_record"]}
    payload = prepare(request, "trial_execution")
    assert payload["purpose"] == "trial_execution"
    assert payload["binding_sha256"] != prepare(request)["binding_sha256"]


def test_signature_verifier_rejects_wrong_key_and_subject(workspace_tmp_path):
    request = request_fixture(workspace_tmp_path)
    payload = prepare(request)
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = key.public_key()
    method = getattr(api, "verify_research_signature", None)
    assert callable(method), "Purpose-bound signature verification is missing"
    signed = method(payload, key.sign(canonical_json(payload)).hex(), request=request,
        purpose="workflow_generation", public_key=public, expected_issuer_id=ISSUER, now=NOW)
    assert signed["signature"] and len(signed["signature"]) == 128
    with pytest.raises(PermissionError):
        method(payload, "00"*64, request=request, purpose="workflow_generation", public_key=public, expected_issuer_id=ISSUER, now=NOW)
    payload["purpose"] = "trial_execution"
    with pytest.raises((PermissionError, ValueError)):
        method(payload, key.sign(canonical_json(payload)).hex(), request=request, purpose="workflow_generation", public_key=public, expected_issuer_id=ISSUER, now=NOW)


def test_signing_has_no_callback_or_arbitrary_message_parameter():
    import inspect
    method = getattr(api, "sign_approved_research_grant", None)
    assert callable(method), "Exact approved grant signing is missing"
    assert set(inspect.signature(method).parameters) == {"request", "payload", "authority", "proposal",
        "approved_subject_sha256", "accepted_fingerprint_sha256"}


def test_boolean_or_report_authority_cannot_touch_keychain(workspace_tmp_path, monkeypatch):
    request = request_fixture(workspace_tmp_path)
    payload = prepare(request)
    method = getattr(api, "sign_approved_research_grant", None)
    assert callable(method), "Exact approved grant signing is missing"
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", lambda: pytest.fail("Keychain touched before exact authority checks"))
    for authority in (True, {"qualified": True}, object()):
        with pytest.raises((PermissionError, TypeError)):
            method(request, payload, authority=authority, proposal={}, approved_subject_sha256=digest(payload),
                accepted_fingerprint_sha256="a"*64)


def test_bridge_native_source_cannot_create_update_or_export_keys():
    source = Path(api.__file__).read_text()
    assert "class _MacOSExistingResearchItem" in source, "Read-only existing-item adapter is missing"
    for forbidden in ("SecItemAdd", "SecItemUpdate", "SecItemDelete", "SecKeychainUnlock", "os.urandom", "Ed25519PrivateKey.generate", "security find", "subprocess"):
        assert forbidden not in source
    import ast
    assert not any(isinstance(node, ast.Attribute) and node.attr == "private_bytes"
        for node in ast.walk(ast.parse(source)))
    assert '"kSecMatchSearchList"' in source
    assert '"kSecUseAuthenticationUIFail"' in source
    assert '"kSecAttrService"' in source and '"kSecAttrAccount"' in source


def test_published_authority_without_signing_preflight_cannot_access_keychain(workspace_tmp_path, monkeypatch):
    from devbench.analysis import rna_trial_authority as canonical_authority
    from devbench.analysis.rna_approval_setup import propose_public_issuer
    request = request_fixture(workspace_tmp_path); payload = prepare(request)
    request["approvals"]["workflow_generation"] = {"purpose":"workflow_generation", "binding_sha256":payload["binding_sha256"],
        "approver_id":payload["approver_id"], "evidence":request["dataset"]["access_record"], "expires_at":EXPIRES}
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_hex = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    proposal = propose_public_issuer(issuer_id=ISSUER, public_key_hex=public_hex)
    authority = canonical_authority.AuthenticatedTrialAuthority(workspace_tmp_path / "denied.sqlite", workspace=workspace_tmp_path,
        issuer_keys={ISSUER:key.public_key()}, runtime_verifier=lambda _: request["engineering"], clock=lambda:NOW)
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", lambda: pytest.fail("Unqualified backend reached Keychain"))
    with pytest.raises(PermissionError, match="preflight is unavailable"):
        api.sign_approved_research_grant(request,payload,authority=authority,proposal=proposal,
            approved_subject_sha256=digest(payload),accepted_fingerprint_sha256=proposal["fingerprint_sha256"])


def test_current_authority_preserves_existing_offline_consume(workspace_tmp_path, monkeypatch):
    from devbench.analysis import rna_trial_authority as canonical_authority
    from tests.test_rna_trial_authority import signed_approval
    request, *_ = fixture(workspace_tmp_path)
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    monkeypatch.setattr(canonical_authority,"trial_enforcement_source_digest",lambda:request["engineering"]["source_digest"])
    monkeypatch.setattr(canonical_authority,"trial_enforcement_runtime_digest",lambda:request["engineering"]["runtime_digest"])
    identity = {k:request["engineering"][k] for k in ("route","source_digest","runtime_digest","backend_digest","model","platform")}
    authority = canonical_authority.AuthenticatedTrialAuthority(workspace_tmp_path/"legacy.sqlite", workspace=workspace_tmp_path,
        issuer_keys={"fixture-issuer":key.public_key()},runtime_verifier=lambda _:deepcopy(identity),clock=lambda:NOW)
    binding = signed_approval(request,key,workspace_tmp_path)
    authority.authorize(request,purpose="workflow_generation",binding_sha256=binding)
    authority.consume(request,purpose="workflow_generation",binding_sha256=binding)
    with pytest.raises(PermissionError,match="consumed|replay"):
        authority.consume(request,purpose="workflow_generation",binding_sha256=binding)


@pytest.mark.parametrize("code,length,type_matches", [(0,32,True),(-25300,32,True),(-25308,32,True),(0,31,True),(0,32,False)])
def test_native_read_query_is_fixed_scoped_and_fails_closed(code, length, type_matches):
    """ctypes buffer is synthetic; no frameworks are loaded or called."""
    import ctypes
    from types import SimpleNamespace
    store = object.__new__(api._MacOSExistingResearchItem)
    store._ct, store._refs, store._keychain, store._true = ctypes, [], 99, "true"
    store._constant = lambda name:name
    store._string = lambda value:value
    store._array = lambda value:("only",value)
    queries = []
    store._dictionary = lambda pairs:queries.append(pairs) or "synthetic-query"
    buffer = (ctypes.c_ubyte * 32)(*range(32))
    def matching(query, result):
        ctypes.cast(result,ctypes.POINTER(ctypes.c_void_p)).contents.value = 1
        return code
    store._sec = SimpleNamespace(SecItemCopyMatching=matching)
    store._cf = SimpleNamespace(CFGetTypeID=lambda value:1 if type_matches else 2, CFDataGetTypeID=lambda:1,
        CFDataGetLength=lambda value:length, CFDataGetBytePtr=lambda value:ctypes.addressof(buffer))
    if code==0 and length==32 and type_matches:
        assert store._read_existing_seed() == bytearray(range(32))
    else:
        with pytest.raises(PermissionError): store._read_existing_seed()
    pairs = dict(queries[0])
    assert set(pairs)=={"kSecClass","kSecAttrService","kSecAttrAccount","kSecUseAuthenticationUI",
        "kSecMatchSearchList","kSecMatchLimit","kSecReturnData"}
    assert pairs["kSecMatchSearchList"] == ("only",99)
    assert pairs["kSecUseAuthenticationUI"] == "kSecUseAuthenticationUIFail"
    assert pairs["kSecAttrService"] == "devbench.local-research.ed25519.20261020.a84f56d983ea4bdca67595dba896371c"
    assert pairs["kSecAttrAccount"] == "agent-provisioned-user-authorized-research-a84f56d983ea4bdca67595dba896371c"
    assert pairs["kSecMatchLimit"] == "kSecMatchLimitOne"


def test_shared_verifier_retains_existing_signature_hex_compatibility(workspace_tmp_path):
    request = request_fixture(workspace_tmp_path); payload = prepare(request)
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    uppercase = key.sign(canonical_json(payload)).hex().upper()
    assert api.verify_authority_signature(payload,uppercase,public_key=key.public_key())["signature"] == uppercase
