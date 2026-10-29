"""Synthetic signing protocol checks; no real Keychain, grant or live route."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import hashlib
import json

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


def request_fixture(tmp_path):
    request, *_ = fixture(tmp_path)
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


def test_closed_subject_matches_existing_authority_format(tmp_path):
    request = request_fixture(tmp_path)
    payload = prepare(request)
    assert payload == {"format":"devbench.rna-trial-authority-grant/1", "issuer_id":ISSUER,
        "grant_id":"synthetic-grant", "trial_id":request["trial_id"], "purpose":"workflow_generation",
        "binding_sha256":trial_binding(request,"workflow_generation"), "approver_id":request["roles"]["operator"],
        "roles_sha256":digest(request["roles"]), "allocation_sha256":digest(trial_purpose_allocation(request,"workflow_generation")),
        "engineering_sha256":digest(request["engineering"]), "expires_at":EXPIRES}


def test_generation_never_supplies_numeric_allocation(tmp_path):
    request = request_fixture(tmp_path)
    baseline = prepare(request)
    request["allocation"]["numeric"] = {"memory_mb":4096,"seconds":1800,"storage_bytes":2_000_000_000,"max_runs":1}
    assert prepare(request) == baseline


@pytest.mark.parametrize("change", ["private", "old_version", "unqualified_boundary", "wrong_route", "arbitrary", "expired", "unknown_purpose"])
def test_unqualified_or_arbitrary_subject_is_rejected(tmp_path, change):
    request = request_fixture(tmp_path)
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


def test_execution_requires_numeric_workflow_and_scientific_subject(tmp_path):
    request = request_fixture(tmp_path)
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


def test_signature_verifier_rejects_wrong_key_and_subject(tmp_path):
    request = request_fixture(tmp_path)
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


def test_boolean_or_report_authority_cannot_touch_keychain(tmp_path, monkeypatch):
    request = request_fixture(tmp_path)
    payload = prepare(request)
    method = getattr(api, "sign_approved_research_grant", None)
    assert callable(method), "Exact approved grant signing is missing"
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", lambda: pytest.fail("Keychain touched before exact authority checks"))
    for authority in (True, {"qualified": True}, object()):
        with pytest.raises((PermissionError, TypeError)):
            method(request, payload, authority=authority, proposal={}, approved_subject_sha256=digest(payload),
                accepted_fingerprint_sha256="a"*64)


def test_bridge_native_source_cannot_create_update_or_export_keys():
    source = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/devbench/analysis/rna_grant_signing.py").read_text()
    assert "class _MacOSExistingResearchItem" in source, "Read-only existing-item adapter is missing"
    for forbidden in ("SecItemAdd", "SecItemUpdate", "SecItemDelete", "SecKeychainUnlock", "os.urandom", "Ed25519PrivateKey.generate", "security find", "subprocess"):
        assert forbidden not in source
    import ast
    assert not any(isinstance(node, ast.Attribute) and node.attr == "private_bytes"
        for node in ast.walk(ast.parse(source)))
    assert '"kSecMatchSearchList"' in source
    assert '"kSecUseAuthenticationUIFail"' in source
    assert '"kSecAttrService"' in source and '"kSecAttrAccount"' in source


@pytest.fixture
def signing_fixture(tmp_path, monkeypatch):
    """Real staged ledger plus a clearly synthetic current-runtime test seam."""
    from devbench.analysis import rna_trial_authority as canonical_authority
    from devbench.analysis.rna_approval_setup import propose_public_issuer
    from devbench.analysis.rna_scientific_trial_contract import validate_approval
    import resource
    staged_path = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-airway-implementation/2026-10-22-v1/grant-bridge/staged-rna_trial_authority-v2.py")
    spec = importlib.util.spec_from_file_location("devbench.analysis._test_grant_staged_authority", staged_path)
    staged = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(staged)
    assert hasattr(staged.AuthenticatedTrialAuthority, "grant_signing_preflight"), "Unsigned live preflight is missing"
    request = request_fixture(tmp_path)
    payload = prepare(request)
    request["approvals"]["workflow_generation"] = {"purpose":"workflow_generation", "binding_sha256":payload["binding_sha256"],
        "approver_id":payload["approver_id"], "evidence":request["dataset"]["access_record"], "expires_at":EXPIRES}
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_hex = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    proposal = propose_public_issuer(issuer_id=ISSUER, public_key_hex=public_hex)
    events = []
    def synthetic_subject(self, value, *, purpose, binding_sha256):
        assert value["trial_id"] == request["trial_id"]
        assert binding_sha256 == trial_binding(value, purpose)
        events.append("synthetic_runtime_recheck")
        return deepcopy(value), validate_approval(value, purpose, now=self.clock())
    monkeypatch.setattr(staged.AuthenticatedTrialAuthority, "_validated_subject", synthetic_subject)
    staged._synthetic_original_signing_readiness = staged.AuthenticatedTrialAuthority._grant_signing_scientific_preflight
    monkeypatch.setattr(staged.AuthenticatedTrialAuthority, "_grant_signing_scientific_preflight", lambda *args, **kwargs: None)
    authority = staged.AuthenticatedTrialAuthority(tmp_path / "signing-ledger.sqlite", workspace=tmp_path,
        issuer_keys={ISSUER:key.public_key()}, runtime_verifier=lambda _: {}, clock=lambda:NOW)
    monkeypatch.setattr(canonical_authority, "AuthenticatedTrialAuthority", staged.AuthenticatedTrialAuthority)
    seed = bytearray(range(32))
    class SyntheticStore:
        def __enter__(self): events.append("synthetic_store_open"); return self
        def _read_existing_seed(self): events.append("synthetic_seed_read"); return seed
        def __exit__(self, *args): events.append("synthetic_store_close")
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", SyntheticStore)
    monkeypatch.setattr(resource, "setrlimit", lambda *args: events.append("synthetic_no_core"))
    kwargs = {"authority":authority,"proposal":proposal,"approved_subject_sha256":digest(payload),
        "accepted_fingerprint_sha256":proposal["fingerprint_sha256"]}
    return request, payload, kwargs, events, seed, staged


def test_exact_approved_subject_uses_one_memory_read_and_does_not_consume(signing_fixture):
    request, payload, kwargs, events, seed, _ = signing_fixture
    signed = api.sign_approved_research_grant(request, payload, **kwargs)
    assert api.verify_research_signature(payload, signed["signature"], request=request, purpose="workflow_generation",
        public_key=kwargs["authority"].issuer_keys[ISSUER], expected_issuer_id=ISSUER, now=NOW) == signed
    assert events == ["synthetic_runtime_recheck","synthetic_no_core","synthetic_store_open","synthetic_seed_read",
        "synthetic_runtime_recheck","synthetic_store_close","synthetic_runtime_recheck"]
    assert seed == bytearray(32)
    with kwargs["authority"]._database() as db:
        assert db.execute("SELECT COUNT(*) FROM grants").fetchone() == (0,)
    assert "secret" not in canonical_json(signed).decode()


@pytest.mark.parametrize("change", ["approved_sha", "accepted_root", "payload_extra", "payload_binding", "expiry", "authority_key", "consumed_binding", "consumed_id"])
def test_all_preconditions_reject_before_store_access(signing_fixture, change):
    request, payload, kwargs, events, _, _ = signing_fixture
    if change == "approved_sha": kwargs["approved_subject_sha256"] = "0"*64
    elif change == "accepted_root": kwargs["accepted_fingerprint_sha256"] = "0"*64
    elif change == "payload_extra": payload["message"] = "arbitrary"
    elif change == "payload_binding": payload["binding_sha256"] = "0"*64; kwargs["approved_subject_sha256"] = digest(payload)
    elif change == "expiry": kwargs["authority"].clock = lambda:datetime(2026,10,9,tzinfo=timezone.utc)
    elif change == "authority_key": kwargs["authority"].issuer_keys[ISSUER] = Ed25519PrivateKey.from_private_bytes(b"x"*32).public_key()
    elif change in {"consumed_binding", "consumed_id"}:
        with kwargs["authority"]._database() as db:
            db.execute("INSERT INTO grants VALUES(?,?,?,?,?,?)", (ISSUER, payload["grant_id"] if change=="consumed_id" else "other",
                payload["purpose"], payload["binding_sha256"] if change=="consumed_binding" else "f"*64, "{}", "0"*64))
    with pytest.raises((ValueError, PermissionError)):
        api.sign_approved_research_grant(request, payload, **kwargs)
    assert "synthetic_store_open" not in events


def test_native_failure_is_sanitized_rolls_back_and_clears_seed(signing_fixture, monkeypatch):
    request, payload, kwargs, events, seed, _ = signing_fixture
    class FailingStore:
        def __enter__(self): return self
        def _read_existing_seed(self): return seed
        def __exit__(self, *args): raise ValueError("PRIVATE_SEED_DO_NOT_LOG")
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", FailingStore)
    with pytest.raises(PermissionError) as caught:
        api.sign_approved_research_grant(request, payload, **kwargs)
    assert "PRIVATE_SEED_DO_NOT_LOG" not in str(caught.value)
    assert seed == bytearray(32)
    with kwargs["authority"]._database() as db:
        assert db.execute("SELECT COUNT(*) FROM grants").fetchone() == (0,)


def test_consumption_lock_is_held_through_signature(signing_fixture, monkeypatch):
    import sqlite3
    request, payload, kwargs, events, seed, _ = signing_fixture
    class CheckedStore:
        def __enter__(self):
            with sqlite3.connect(str(kwargs["authority"].path), timeout=0) as db:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    db.execute("BEGIN IMMEDIATE")
            return self
        def _read_existing_seed(self): return seed
        def __exit__(self, *args): pass
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", CheckedStore)
    assert api.sign_approved_research_grant(request, payload, **kwargs)["signature"]


def test_current_runtime_failure_rejects_before_key_access(signing_fixture, monkeypatch):
    request, payload, kwargs, events, _, staged = signing_fixture
    def deny(*args, **kwargs): raise PermissionError("synthetic runtime drift")
    monkeypatch.setattr(staged.AuthenticatedTrialAuthority, "_validated_subject", deny)
    with pytest.raises(PermissionError): api.sign_approved_research_grant(request, payload, **kwargs)
    assert "synthetic_store_open" not in events


def test_after_signature_drift_returns_no_grant(signing_fixture, monkeypatch):
    request, payload, kwargs, events, seed, staged = signing_fixture
    original = staged.AuthenticatedTrialAuthority._validated_subject
    count = [0]
    def drift(*args, **kwargs):
        count[0] += 1
        if count[0] > 2: raise PermissionError("synthetic after-sign drift")
        return original(*args, **kwargs)
    monkeypatch.setattr(staged.AuthenticatedTrialAuthority, "_validated_subject", drift)
    with pytest.raises(PermissionError): api.sign_approved_research_grant(request, payload, **kwargs)
    assert seed == bytearray(32)


def test_runtime_rechecked_immediately_before_signing(signing_fixture):
    request, payload, kwargs, events, _, _ = signing_fixture
    api.sign_approved_research_grant(request, payload, **kwargs)
    assert events.count("synthetic_runtime_recheck") == 3
    assert events.index("synthetic_seed_read") < max(i for i,event in enumerate(events)
        if event == "synthetic_runtime_recheck" and i < events.index("synthetic_store_close"))


def test_actual_native_route_is_rejected_without_qualified_backend(tmp_path, monkeypatch):
    from devbench.analysis import rna_trial_authority as canonical_authority
    from devbench.analysis.rna_approval_setup import propose_public_issuer
    staged_path = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-airway-implementation/2026-10-22-v1/grant-bridge/staged-rna_trial_authority-v2.py")
    spec = importlib.util.spec_from_file_location("devbench.analysis._test_unqualified_staged_authority", staged_path)
    staged = importlib.util.module_from_spec(spec); spec.loader.exec_module(staged)
    request = request_fixture(tmp_path); payload = prepare(request)
    request["approvals"]["workflow_generation"] = {"purpose":"workflow_generation", "binding_sha256":payload["binding_sha256"],
        "approver_id":payload["approver_id"], "evidence":request["dataset"]["access_record"], "expires_at":EXPIRES}
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_hex = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    proposal = propose_public_issuer(issuer_id=ISSUER, public_key_hex=public_hex)
    authority = staged.AuthenticatedTrialAuthority(tmp_path / "denied.sqlite", workspace=tmp_path,
        issuer_keys={ISSUER:key.public_key()}, runtime_verifier=lambda _: request["engineering"], clock=lambda:NOW)
    monkeypatch.setattr(canonical_authority, "AuthenticatedTrialAuthority", staged.AuthenticatedTrialAuthority)
    monkeypatch.setattr(api, "_MacOSExistingResearchItem", lambda: pytest.fail("Unqualified backend reached Keychain"))
    with pytest.raises(PermissionError):
        api.sign_approved_research_grant(request,payload,authority=authority,proposal=proposal,
            approved_subject_sha256=digest(payload),accepted_fingerprint_sha256=proposal["fingerprint_sha256"])


def test_staged_shared_verifier_preserves_existing_offline_consume(tmp_path, monkeypatch):
    from tests.test_rna_trial_authority import signed_approval
    staged_path = Path("/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/rna-airway-implementation/2026-10-22-v1/grant-bridge/staged-rna_trial_authority-v2.py")
    spec = importlib.util.spec_from_file_location("devbench.analysis._test_legacy_staged_authority", staged_path)
    staged = importlib.util.module_from_spec(spec); spec.loader.exec_module(staged)
    request, *_ = fixture(tmp_path)
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    monkeypatch.setattr(staged,"trial_enforcement_source_digest",lambda:request["engineering"]["source_digest"])
    monkeypatch.setattr(staged,"trial_enforcement_runtime_digest",lambda:request["engineering"]["runtime_digest"])
    identity = {k:request["engineering"][k] for k in ("route","source_digest","runtime_digest","backend_digest","model","platform")}
    authority = staged.AuthenticatedTrialAuthority(tmp_path/"legacy.sqlite", workspace=tmp_path,
        issuer_keys={"fixture-issuer":key.public_key()},runtime_verifier=lambda _:deepcopy(identity),clock=lambda:NOW)
    binding = signed_approval(request,key,tmp_path)
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


def test_wrong_existing_seed_is_rejected_and_cleared(signing_fixture, monkeypatch):
    request, payload, kwargs, _, _, _ = signing_fixture
    wrong = bytearray(b"x"*32)
    class WrongStore:
        def __enter__(self):return self
        def _read_existing_seed(self):return wrong
        def __exit__(self,*args):pass
    monkeypatch.setattr(api,"_MacOSExistingResearchItem",WrongStore)
    with pytest.raises(PermissionError):api.sign_approved_research_grant(request,payload,**kwargs)
    assert wrong==bytearray(32)


def test_shared_verifier_retains_existing_signature_hex_compatibility(tmp_path):
    request = request_fixture(tmp_path); payload = prepare(request)
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    uppercase = key.sign(canonical_json(payload)).hex().upper()
    assert api.verify_authority_signature(payload,uppercase,public_key=key.public_key())["signature"] == uppercase


def test_unsigned_readiness_preserves_bindings_and_clears_no_other_blocker(signing_fixture, monkeypatch):
    from devbench.analysis import rna_scientific_trial as trial
    from types import SimpleNamespace
    request, payload, kwargs, events, _, staged = signing_fixture
    method = getattr(staged,"_synthetic_original_signing_readiness",None)
    assert callable(method), "Actual unsigned scientific-root readiness preflight is missing"
    authority = kwargs["authority"]
    authority._runtime_context = {"planner":SimpleNamespace(methods=object())}
    seen = []
    def inspect(value, **options):
        seen.append(deepcopy(value))
        return {"binding_sha256":trial_binding(value,"workflow_generation"),
            "blockers":["Missing separate workflow_generation approval"]}
    monkeypatch.setattr(trial,"inspect_readiness",inspect)
    method(authority,request,purpose="workflow_generation",binding_sha256=payload["binding_sha256"])
    assert seen[0]["approvals"]["workflow_generation"] is None
    assert request["approvals"]["workflow_generation"] is not None
    assert trial_binding(seen[0],"workflow_generation") == payload["binding_sha256"]
    def blocked(value,**options):
        return {"binding_sha256":payload["binding_sha256"],"blockers":["Missing separate workflow_generation approval","actual_design_invalid"]}
    monkeypatch.setattr(trial,"inspect_readiness",blocked)
    with pytest.raises(PermissionError):
        method(authority,request,purpose="workflow_generation",binding_sha256=payload["binding_sha256"])


@pytest.mark.parametrize("classification", ["public", "synthetic"])
def test_execution_unsigned_readiness_uses_original_pending_subject_for_qualification(signing_fixture, monkeypatch, classification):
    """Tests resolver wiring with a synthetic exact-class fixture, not acceptance."""
    from devbench.analysis import rna_scientific_trial as trial
    from devbench.analysis.rna_route_qualification import TrustedExecutionQualification
    from devbench.analysis.rna_public_execution_qualification import TrustedPublicExecutionQualification
    from types import SimpleNamespace
    request, _, kwargs, _, _, staged = signing_fixture
    request["dataset"]["classification"] = classification
    expected_type = TrustedPublicExecutionQualification if classification == "public" else TrustedExecutionQualification
    request["allocation"]["numeric"] = {"memory_mb":4096,"seconds":1800,"storage_bytes":2_000_000_000,"max_runs":1}
    request["scientific_context"] = {"project_id":"a"*32,"scientific_run_id":"b"*32,"attempt_id":"c"*32,"dataset_version_ids":["d"*64]}
    request["assessment_plan"] = request["publication"]; request["comparison"] = request["publication"]
    request["workflow"] = {"planner_session_id":"fixture","experiment_ids":["fixture"],"sha256":"e"*64,"workflow_ref":request["dataset"]["access_record"]}
    payload = prepare(request,"trial_execution")
    request["approvals"]["trial_execution"] = {"purpose":"trial_execution","binding_sha256":payload["binding_sha256"],
        "approver_id":payload["approver_id"],"evidence":request["dataset"]["access_record"],"expires_at":EXPIRES}
    authority = kwargs["authority"]
    authority._runtime_context = {"planner":SimpleNamespace(methods=object())}
    authority.execution_qualification = object.__new__(expected_type)
    seen = []
    def inspect(value,**options):
        assert value["approvals"]["trial_execution"] is None
        assert trial_binding(value,"trial_execution")==payload["binding_sha256"]
        assert "execution_qualification" not in options
        return {"binding_sha256":payload["binding_sha256"],"blockers":["Missing separate trial_execution approval","synthetic_qualified_adapter_blocker"]}
    def verify(self,value,**options):
        seen.append(deepcopy(value))
        assert value["approvals"]["trial_execution"] is not None
        return {"satisfied_blockers":["synthetic_qualified_adapter_blocker"]}
    monkeypatch.setattr(trial,"inspect_readiness",inspect)
    monkeypatch.setattr(expected_type,"verify",verify)
    method = staged._synthetic_original_signing_readiness
    method(authority,request,purpose="trial_execution",binding_sha256=payload["binding_sha256"])
    assert seen == [request]
    wrong_type = TrustedExecutionQualification if classification == "public" else TrustedPublicExecutionQualification
    authority.execution_qualification = object.__new__(wrong_type)
    with pytest.raises(PermissionError):
        method(authority,request,purpose="trial_execution",binding_sha256=payload["binding_sha256"])
    authority.execution_qualification = {"qualified":True}
    with pytest.raises(PermissionError):
        method(authority,request,purpose="trial_execution",binding_sha256=payload["binding_sha256"])


@pytest.mark.parametrize("blockers", [[], ["Missing separate workflow_generation approval appended"],
    ["Missing separate workflow_generation approval","Missing separate workflow_generation approval"],
    ["Missing separate workflow_generation approval","actual_data_failure"]])
def test_unsigned_preflight_does_not_accept_blocker_prefixes_or_reports(signing_fixture, monkeypatch, blockers):
    from devbench.analysis import rna_scientific_trial as trial
    from types import SimpleNamespace
    request,payload,kwargs,_,_,staged = signing_fixture
    kwargs["authority"]._runtime_context={"planner":SimpleNamespace(methods=object())}
    monkeypatch.setattr(trial,"inspect_readiness",lambda *args,**options:{"binding_sha256":payload["binding_sha256"],"blockers":blockers})
    with pytest.raises(PermissionError):
        staged._synthetic_original_signing_readiness(kwargs["authority"],request,purpose="workflow_generation",binding_sha256=payload["binding_sha256"])
