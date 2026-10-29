"""Authenticated local fixture grants are finite and durably one-use."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import sqlite3
import base64
import csv
import hashlib
from importlib.metadata import PathDistribution
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devharness_core.provenance import canonical_json
from devbench.analysis.checkpoints import digest
from devbench.analysis import rna_scientific_trial as api
from devbench.analysis import rna_trial_authority as authority_api
from devbench.analysis.rna_scientific_trial_contract import trial_binding
from tests.test_rna_scientific_trial import NOW, fixture, source


def authority_fixture(tmp_path):
    assert hasattr(api, "AuthenticatedTrialAuthority")
    request, *_ = fixture(tmp_path)
    request["engineering"]["source_digest"] = api.trial_enforcement_source_digest()
    request["engineering"]["runtime_digest"] = api.trial_enforcement_runtime_digest()
    private_key = Ed25519PrivateKey.generate()
    runtime = {field: request["engineering"][field] for field in
        ("route", "source_digest", "runtime_digest", "backend_digest", "model", "platform")}
    current = [NOW]
    def construct():
        return api.AuthenticatedTrialAuthority(tmp_path / "trusted-authority.sqlite", workspace=tmp_path,
            issuer_keys={"fixture-issuer": private_key.public_key()}, runtime_verifier=lambda *args, **kwargs: deepcopy(runtime),
            clock=lambda: current[0])
    return request, private_key, runtime, current, construct


def signed_approval(request, private_key, tmp_path, *, purpose="workflow_generation", grant_id="fixture-grant"):
    payload = {"format": "devbench.rna-trial-authority-grant/1", "issuer_id": "fixture-issuer", "grant_id": grant_id,
        "trial_id": request["trial_id"], "purpose": purpose, "binding_sha256": trial_binding(request, purpose),
        "approver_id": request["roles"]["operator"], "roles_sha256": digest(request["roles"]),
        "allocation_sha256": digest(request["allocation"]), "engineering_sha256": digest(request["engineering"]),
        "expires_at": "2026-10-08T00:00:00Z"}
    signature = private_key.sign(canonical_json(payload)).hex()
    path = tmp_path / (purpose + "-" + grant_id + "-" + request["trial_id"] + ".json")
    evidence = source(path, canonical_json({**payload, "signature": signature}).decode())
    request["approvals"][purpose] = {"purpose": purpose, "binding_sha256": payload["binding_sha256"],
        "approver_id": payload["approver_id"], "evidence": evidence, "expires_at": payload["expires_at"]}
    return trial_binding(request, purpose)


def test_authentication_is_required_and_json_approval_is_insufficient(tmp_path):
    request, private_key, _, _, construct = authority_fixture(tmp_path)
    binding = signed_approval(request, private_key, tmp_path)
    authority = construct()
    authority.authorize(request, purpose="workflow_generation", binding_sha256=binding)
    receipt = Path(request["approvals"]["workflow_generation"]["evidence"]["path"])
    import json
    malformed = json.loads(receipt.read_text())
    malformed["signature"] = "00" * 64
    request["approvals"]["workflow_generation"]["evidence"] = source(receipt, canonical_json(malformed).decode())
    with pytest.raises(PermissionError, match="signature|authenticated"):
        authority.consume(request, purpose="workflow_generation", binding_sha256=binding)


def test_one_use_grant_replay_is_rejected_across_authority_instances(tmp_path):
    request, private_key, _, _, construct = authority_fixture(tmp_path)
    binding = signed_approval(request, private_key, tmp_path)
    construct().consume(request, purpose="workflow_generation", binding_sha256=binding)
    with pytest.raises(PermissionError, match="used|consumed|replay"):
        construct().consume(request, purpose="workflow_generation", binding_sha256=binding)


def test_global_issuer_grant_identity_cannot_be_reused_for_changed_trial_copy(tmp_path):
    request, private_key, _, _, construct = authority_fixture(tmp_path)
    binding = signed_approval(request, private_key, tmp_path)
    construct().consume(request, purpose="workflow_generation", binding_sha256=binding)
    copied = deepcopy(request)
    copied["trial_id"] = "new-copy"
    copied_binding = signed_approval(copied, private_key, tmp_path)
    with pytest.raises(PermissionError, match="used|consumed|replay"):
        construct().consume(copied, purpose="workflow_generation", binding_sha256=copied_binding)


def test_signed_grant_purpose_swap_is_rejected(tmp_path):
    request, private_key, _, _, construct = authority_fixture(tmp_path)
    signed_approval(request, private_key, tmp_path)
    execution_binding = trial_binding(request, "trial_execution")
    request["approvals"]["trial_execution"] = deepcopy(request["approvals"]["workflow_generation"])
    request["approvals"]["trial_execution"].update(purpose="trial_execution", binding_sha256=execution_binding)
    with pytest.raises((ValueError, PermissionError), match="purpose|subject"):
        construct().consume(request, purpose="trial_execution", binding_sha256=execution_binding)


def test_each_dispatch_checks_fresh_expiry_and_runtime_identity(tmp_path):
    request, private_key, runtime, current, construct = authority_fixture(tmp_path)
    binding = signed_approval(request, private_key, tmp_path)
    authority = construct()
    authority.consume(request, purpose="workflow_generation", binding_sha256=binding)
    allocation = {"total_tokens": 0, "requests": 1, "memory_mb": 256, "seconds": 1,
        "storage_bytes": 0, "max_runs": 0}
    authority.reserve_dispatch(request, purpose="workflow_generation", binding_sha256=binding,
        dispatch_id="first", allocation=allocation)
    runtime["runtime_digest"] = "f" * 64
    with pytest.raises(PermissionError, match="identity|runtime"):
        authority.reserve_dispatch(request, purpose="workflow_generation", binding_sha256=binding,
            dispatch_id="drift", allocation=allocation)
    runtime["runtime_digest"] = request["engineering"]["runtime_digest"]
    current[0] = NOW + timedelta(days=2)
    with pytest.raises(PermissionError, match="expired"):
        authority.reserve_dispatch(request, purpose="workflow_generation", binding_sha256=binding,
            dispatch_id="expired", allocation=allocation)


def test_preventive_caps_and_uncertain_spend_cannot_be_reset(tmp_path):
    request, private_key, _, _, construct = authority_fixture(tmp_path)
    request["allocation"]["requests"] = 1
    binding = signed_approval(request, private_key, tmp_path)
    authority = construct()
    authority.consume(request, purpose="workflow_generation", binding_sha256=binding)
    allocation = {"total_tokens": 0, "requests": 1, "memory_mb": 256, "seconds": 1,
        "storage_bytes": 0, "max_runs": 0}
    authority.reserve_dispatch(request, purpose="workflow_generation", binding_sha256=binding,
        dispatch_id="uncertain-call", allocation=allocation)
    with pytest.raises(PermissionError, match="allocation|cap"):
        construct().reserve_dispatch(request, purpose="workflow_generation", binding_sha256=binding,
            dispatch_id="retry", allocation=allocation)
    with pytest.raises(PermissionError, match="used|consumed|replay"):
        construct().consume(request, purpose="workflow_generation", binding_sha256=binding)


@pytest.mark.parametrize("route", ["native", "mediated"])
def test_no_live_route_is_enabled_by_authenticated_fixture_grant(tmp_path, route):
    request, private_key, runtime, _, construct = authority_fixture(tmp_path)
    request["engineering"]["route"] = runtime["route"] = route
    binding = signed_approval(request, private_key, tmp_path)
    with pytest.raises(PermissionError, match="disabled|unavailable|offline"):
        construct().consume(request, purpose="workflow_generation", binding_sha256=binding)


def test_existing_ledger_cannot_omit_replay_constraints(tmp_path):
    _, _, _, _, construct = authority_fixture(tmp_path)
    with sqlite3.connect(tmp_path / "trusted-authority.sqlite") as db:
        db.execute("CREATE TABLE grants (issuer_id TEXT, grant_id TEXT, purpose TEXT, binding_sha256 TEXT, grant_json TEXT, grant_sha256 TEXT)")
        db.execute("CREATE TABLE dispatches (issuer_id TEXT, grant_id TEXT, dispatch_id TEXT, allocation_json TEXT, allocation_sha256 TEXT)")
        db.execute("PRAGMA user_version=1")
    with pytest.raises(PermissionError, match="schema|constraint|ledger"):
        construct()


def test_existing_ledger_cannot_have_a_replay_erasing_trigger(tmp_path):
    _, _, _, _, construct = authority_fixture(tmp_path)
    construct()
    with sqlite3.connect(tmp_path / "trusted-authority.sqlite") as db:
        db.execute("CREATE TRIGGER erase_grant AFTER INSERT ON grants BEGIN DELETE FROM grants; END")
    with pytest.raises(PermissionError, match="schema|constraint|ledger"):
        construct()


def test_concurrent_authenticated_grant_consumption_admits_at_most_once(tmp_path):
    request, private_key, _, _, construct = authority_fixture(tmp_path)
    binding = signed_approval(request, private_key, tmp_path)
    authorities = [construct(), construct()]
    barrier = Barrier(2)
    def consume(authority):
        barrier.wait()
        try:
            authority.consume(request, purpose="workflow_generation", binding_sha256=binding)
            return "consumed"
        except PermissionError:
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(consume, authorities))
    assert outcomes.count("consumed") == 1 and outcomes.count("denied") == 1


def test_runtime_custody_checks_actual_disposable_installed_bytes(tmp_path):
    assert hasattr(authority_api, "verify_installed_distribution_custody")
    root = tmp_path / "disposable-runtime"
    package = root / "fixture_package"
    metadata = root / "fixture_package-1.dist-info"
    package.mkdir(parents=True)
    metadata.mkdir()
    helper = package / "control.py"
    helper.write_bytes(b"# Synthetic control helper\n")
    omitted = package / "recovery.py"
    omitted.write_bytes(b"# Synthetic transitive recovery helper\n")
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: fixture-package\nVersion: 1\n")
    rows = []
    for path in (helper, omitted, metadata / "METADATA"):
        body = path.read_bytes()
        encoded = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
        rows.append([str(path.relative_to(root)), "sha256=" + encoded, str(len(body))])
    rows.append([str((metadata / "RECORD").relative_to(root)), "", ""])
    with (metadata / "RECORD").open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    distribution = PathDistribution(metadata)
    verified = authority_api.verify_installed_distribution_custody(distribution, runtime_root=root)
    assert verified["file_count"] == 3 and verified["record_sha256"]
    omitted.write_bytes(b"# Mutated transitive recovery helper\n")
    with pytest.raises(PermissionError, match="digest|custody|RECORD|bytes"):
        authority_api.verify_installed_distribution_custody(distribution, runtime_root=root,
            required_paths=["fixture_package/control.py"])
    omitted.write_bytes(b"# Synthetic transitive recovery helper\n")
    helper.write_bytes(b"# Mutated synthetic helper\n")
    with pytest.raises(PermissionError, match="digest|custody|RECORD|bytes"):
        authority_api.verify_installed_distribution_custody(distribution, runtime_root=root)


def test_actual_runtime_digest_is_required_for_authenticated_dispatch(tmp_path):
    assert hasattr(api, "trial_enforcement_runtime_digest")
    request, private_key, runtime, _, construct = authority_fixture(tmp_path)
    request["engineering"]["runtime_digest"] = runtime["runtime_digest"] = "b" * 64
    binding = signed_approval(request, private_key, tmp_path)
    with pytest.raises(PermissionError, match="runtime"):
        construct().consume(request, purpose="workflow_generation", binding_sha256=binding)


def test_actual_runtime_objects_are_rechecked_before_each_dispatch(tmp_path):
    request, private_key, runtime, current, _ = authority_fixture(tmp_path)
    expected_selector = object()
    active = {"selector": expected_selector, "planner": object(), "actor": "participant"}
    observed = []
    def verify(context):
        observed.append(context)
        return deepcopy(runtime) if context is not None and context.get("selector") is expected_selector else {}
    authority = api.AuthenticatedTrialAuthority(tmp_path / "bound-runtime.sqlite", workspace=tmp_path,
        issuer_keys={"fixture-issuer": private_key.public_key()}, runtime_verifier=verify, clock=lambda: current[0])
    assert hasattr(authority, "bind_runtime_context")
    authority.bind_runtime_context(active)
    binding = signed_approval(request, private_key, tmp_path)
    authority.consume(request, purpose="workflow_generation", binding_sha256=binding)
    allocation = {"total_tokens": 0, "requests": 1, "memory_mb": 256, "seconds": 1, "storage_bytes": 0, "max_runs": 0}
    authority.bind_runtime_context({**active, "selector": object()})
    with pytest.raises(PermissionError, match="runtime|identity"):
        authority.reserve_dispatch(request, purpose="workflow_generation", binding_sha256=binding,
            dispatch_id="changed-selector", allocation=allocation)
    assert observed[-1]["selector"] is not expected_selector


def test_runtime_custody_recurses_into_active_separate_dependency_distributions(tmp_path):
    assert hasattr(authority_api, "verify_trial_runtime_dependencies")
    root = tmp_path / "dependency-runtime"
    root.mkdir()
    distributions = {}
    def retained_distribution(name, requirements=()):
        package = root / name.replace("-", "_")
        metadata = root / (name + "-1.dist-info")
        package.mkdir()
        metadata.mkdir()
        module = package / "__init__.py"
        module.write_bytes(b"# Synthetic independent dependency\n")
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: " + name + "\nVersion: 1\n"
            + "".join("Requires-Dist: " + value + "\n" for value in requirements))
        rows = []
        for path in (module, metadata / "METADATA"):
            body = path.read_bytes()
            encoded = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
            rows.append([str(path.relative_to(root)), "sha256=" + encoded, str(len(body))])
        rows.append([str((metadata / "RECORD").relative_to(root)), "", ""])
        with (metadata / "RECORD").open("w", newline="") as handle:
            csv.writer(handle).writerows(rows)
        distributions[name] = PathDistribution(metadata)
        return module
    retained_distribution("fixture-schema", ["fixture-recovery>=1", "fixture-optional; extra == 'unselected'"])
    dependency = retained_distribution("fixture-recovery")
    result = authority_api.verify_trial_runtime_dependencies(["fixture-schema"], runtime_root=root,
        distribution_resolver=lambda name: distributions[name])
    assert set(result) == {"fixture-schema", "fixture-recovery"}
    dependency.write_bytes(b"# Modified independent dependency\n")
    with pytest.raises(PermissionError, match="RECORD|digest|custody|bytes"):
        authority_api.verify_trial_runtime_dependencies(["fixture-schema"], runtime_root=root,
            distribution_resolver=lambda name: distributions[name])


@pytest.mark.parametrize("dependency,relative", [
    ("psutil", "psutil/__init__.py"), ("pydantic", "pydantic/type_adapter.py"),
])
def test_runtime_digest_covers_actual_product_dependency_bytes(tmp_path, monkeypatch, dependency, relative):
    """Mutate disposable copies of actual separate wheels, never the frozen runtime."""
    import importlib.metadata
    import shutil
    from packaging.requirements import Requirement

    root = tmp_path / "product-dependency-runtime"
    root.mkdir()
    distributions = {}
    installed_resolver = importlib.metadata.distribution

    def copy_active_distribution(name):
        name = name.lower().replace("_", "-")
        if name in distributions:
            return
        installed = installed_resolver(name)
        files = list(installed.files or ())
        assert 0 < len(files) <= 10_000
        record = next(value for value in files if str(value).endswith(".dist-info/RECORD"))
        for value in files:
            path = Path(str(value))
            assert not path.is_absolute() and ".." not in path.parts
            source_path = Path(installed.locate_file(value))
            if not source_path.is_file() or str(value).endswith(".pyc"):
                continue
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
        distributions[name] = PathDistribution((root / str(record)).parent)
        for text in installed.requires or ():
            requirement = Requirement(text)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                copy_active_distribution(requirement.name)

    def fixture_distribution(name, requirements=()):
        package = root / name.replace("-", "_")
        metadata = root / (name + "-1.dist-info")
        package.mkdir()
        metadata.mkdir()
        module = package / "__init__.py"
        module.write_bytes(b"# Engineering dependency-root fixture\n")
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: " + name + "\nVersion: 1\n"
            + "".join("Requires-Dist: " + value + "\n" for value in requirements))
        rows = []
        for path in (module, metadata / "METADATA"):
            body = path.read_bytes()
            encoded = base64.urlsafe_b64encode(hashlib.sha256(body).digest()).decode().rstrip("=")
            rows.append([str(path.relative_to(root)), "sha256=" + encoded, str(len(body))])
        rows.append([str((metadata / "RECORD").relative_to(root)), "", ""])
        with (metadata / "RECORD").open("w", newline="") as handle:
            csv.writer(handle).writerows(rows)
        distributions[name] = PathDistribution(metadata)

    copy_active_distribution(dependency)
    fixture_distribution("devharness", [dependency + ">=1", "unused-analysis; extra == 'analysis'"])
    for name in ("cryptography", "jsonschema", "referencing", "numpy", "packaging"):
        fixture_distribution(name)
    original_body = Path(installed_resolver(dependency).locate_file(relative)).read_bytes()
    copied_module = root / relative
    assert copied_module.read_bytes() == original_body
    copied_python = root / "python"
    shutil.copyfile(authority_api.sys.executable, copied_python)
    monkeypatch.setattr(authority_api.importlib.metadata, "distribution", lambda name: distributions[name])
    monkeypatch.setattr(authority_api.sys, "prefix", str(root))
    monkeypatch.setattr(authority_api.sys, "executable", str(copied_python))
    assert len(authority_api.trial_enforcement_runtime_digest()) == 64
    copied_module.write_bytes(original_body + b"\n# Disposable mutation of actual dependency bytes\n")
    with pytest.raises(PermissionError, match="RECORD|digest|custody|bytes"):
        authority_api.trial_enforcement_runtime_digest()
    assert Path(installed_resolver(dependency).locate_file(relative)).read_bytes() == original_body


def approval_setup_api():
    """Keep the pre-feature installed-wheel failure an explicit assertion."""
    import importlib.util
    import importlib
    assert importlib.util.find_spec("devbench.analysis.rna_approval_setup") is not None, "Public-only approval setup is unavailable"
    return importlib.import_module("devbench.analysis.rna_approval_setup")


def approval_setup_fixture(tmp_path):
    from cryptography.hazmat.primitives import serialization
    setup = approval_setup_api()
    request, *_ = fixture(tmp_path)
    request["approvals"] = {"workflow_generation": None, "trial_execution": None}
    # Disposable test material only; no local owner signer is created/enrolled.
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key()
    proposal = setup.propose_public_issuer(issuer_id="fixture-research-owner",
        public_key_hex=public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
    fingerprint = hashlib.sha256(public.public_bytes(serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()
    return setup, request, private, proposal, fingerprint


def test_approval_setup_proposal_is_public_only_disabled_and_generation_scoped(tmp_path):
    setup, request, _, proposal, fingerprint = approval_setup_fixture(tmp_path)
    assert proposal == {"format": "devbench.rna-research-public-issuer-proposal/1",
        "issuer_id": "fixture-research-owner", "public_key_hex": proposal["public_key_hex"],
        "fingerprint_sha256": fingerprint, "enabled": False,
        "scope": {"dataset_classification": "synthetic", "purpose": "workflow_generation"}}
    assert request["approvals"] == {"workflow_generation": None, "trial_execution": None}
    assert not hasattr(setup, "generate_private_key") and not hasattr(setup, "sign_grant")


@pytest.mark.parametrize("public_hex", ["00" * 31, "00" * 33, "gg" * 32, "-----BEGIN PRIVATE KEY-----"])
def test_approval_setup_rejects_non_public_raw_key_input(public_hex):
    with pytest.raises(ValueError, match="public|Ed25519|hex"):
        approval_setup_api().propose_public_issuer(issuer_id="fixture-owner", public_key_hex=public_hex)


@pytest.mark.parametrize("change", ["fingerprint", "issuer", "enabled", "scope", "unknown_field", "key"])
def test_approval_setup_requires_independent_fingerprint_and_closed_proposal(tmp_path, change):
    setup, _, _, proposal, fingerprint = approval_setup_fixture(tmp_path)
    expected_issuer = "fixture-research-owner"
    if change == "fingerprint":
        fingerprint = "0" * 64
    elif change == "issuer":
        expected_issuer = "different-owner"
    elif change == "enabled":
        proposal["enabled"] = True
    elif change == "scope":
        proposal["scope"]["purpose"] = "trial_execution"
    elif change == "unknown_field":
        proposal["accepted_fingerprint_sha256"] = fingerprint
    else:
        proposal["public_key_hex"] = "00" * 32
    with pytest.raises((ValueError, PermissionError), match="fingerprint|issuer|proposal|scope|disabled"):
        setup.load_public_issuer(proposal, expected_issuer_id=expected_issuer, accepted_fingerprint_sha256=fingerprint)


def test_approval_setup_proposal_cannot_supply_its_own_acceptance(tmp_path):
    setup, _, _, proposal, _ = approval_setup_fixture(tmp_path)
    with pytest.raises(TypeError):
        setup.load_public_issuer(proposal)


def test_approval_setup_prepares_existing_exact_unsigned_subject_without_mutation(tmp_path):
    setup, request, _, _, _ = approval_setup_fixture(tmp_path)
    before = deepcopy(request)
    payload = setup.prepare_generation_grant(request, issuer_id="fixture-research-owner", grant_id="fixture-generation",
        expires_at="2026-10-08T00:00:00Z", now=NOW)
    assert payload == {"format": "devbench.rna-trial-authority-grant/1", "issuer_id": "fixture-research-owner",
        "grant_id": "fixture-generation", "trial_id": request["trial_id"], "purpose": "workflow_generation",
        "binding_sha256": trial_binding(request, "workflow_generation"), "approver_id": request["roles"]["operator"],
        "roles_sha256": digest(request["roles"]), "allocation_sha256": digest(request["allocation"]),
        "engineering_sha256": digest(request["engineering"]), "expires_at": "2026-10-08T00:00:00Z"}
    assert request == before


def test_approval_setup_signedin_generation_does_not_bind_numeric_allocation(tmp_path):
    setup, request, _, _, _ = approval_setup_fixture(tmp_path)
    from devbench.analysis.rna_scientific_trial_contract import FORMAT_V3, trial_purpose_allocation
    request.update(format=FORMAT_V3, assessment_intent=request["publication"], assessment_plan=None, scientific_context=None)
    request["engineering"].update(route="codex_chatgpt", client={"kind": "official_codex_cli", "identity_sha256": "1" * 64},
        authentication={"method": "chatgpt_signin", "observation_sha256": "2" * 64},
        budget_semantics="generation_jobs", qualified_boundary=None)
    request["allocation"] = {"kind": "generation_jobs", "generation": {"jobs": 3, "seconds": 120,
        "context_bytes": 4096, "output_bytes": 4096}, "numeric": {"memory_mb": 256, "seconds": 60,
        "storage_bytes": 100000, "max_runs": 1}}
    options = dict(issuer_id="fixture-research-owner", grant_id="fixture-generation", expires_at="2026-10-08T00:00:00Z", now=NOW)
    prepared = setup.prepare_generation_grant(request, **options)
    assert prepared["allocation_sha256"] == digest(trial_purpose_allocation(request, "workflow_generation"))
    assert prepared["allocation_sha256"] != digest(request["allocation"])
    request["allocation"]["numeric"]["max_runs"] = 2
    assert setup.prepare_generation_grant(request, **options) == prepared
    assert request["engineering"]["qualified_boundary"] is None


@pytest.mark.parametrize("classification", ["public", "private", "private_clinical", "unclassified"])
def test_approval_setup_rejects_real_study_subjects(tmp_path, classification):
    setup, request, _, _, _ = approval_setup_fixture(tmp_path)
    request["dataset"]["classification"] = classification
    with pytest.raises(PermissionError, match="synthetic"):
        setup.prepare_generation_grant(request, issuer_id="fixture-research-owner", grant_id="fixture-generation",
            expires_at="2026-10-08T00:00:00Z", now=NOW)


@pytest.mark.parametrize("expiry", ["2026-10-06T00:00:00Z", "2026-10-08T00:00:00", "invalid"])
def test_approval_setup_rejects_expired_or_ambiguous_expiry(tmp_path, expiry):
    setup, request, _, _, _ = approval_setup_fixture(tmp_path)
    with pytest.raises((ValueError, PermissionError), match="expir|timezone|timestamp"):
        setup.prepare_generation_grant(request, issuer_id="fixture-research-owner", grant_id="fixture-generation", expires_at=expiry, now=NOW)


def test_approval_setup_import_verifies_detached_signature_without_admitting_or_mutating(tmp_path):
    setup, request, private, proposal, fingerprint = approval_setup_fixture(tmp_path)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    key = setup.load_public_issuer(proposal, expected_issuer_id="fixture-research-owner", accepted_fingerprint_sha256=fingerprint)
    assert isinstance(key, Ed25519PublicKey)
    payload = setup.prepare_generation_grant(request, issuer_id="fixture-research-owner", grant_id="fixture-generation",
        expires_at="2026-10-08T00:00:00Z", now=NOW)
    before = deepcopy((request, payload, proposal))
    signature = private.sign(canonical_json(payload)).hex()
    grant = setup.import_generation_signature(payload, signature, request=request, proposal=proposal,
        expected_issuer_id="fixture-research-owner", accepted_fingerprint_sha256=fingerprint, now=NOW)
    assert grant == {**payload, "signature": signature}
    assert set(grant) == authority_api._GRANT_FIELDS
    key.verify(bytes.fromhex(grant["signature"]), canonical_json(payload))
    assert (request, payload, proposal) == before
    assert not list(tmp_path.glob("*.sqlite"))


@pytest.mark.parametrize("change", ["purpose", "subject", "issuer", "trial_id", "binding_sha256", "approver_id",
    "roles_sha256", "allocation_sha256", "engineering_sha256", "signature", "short_signature", "nonhex_signature", "payload_field", "expiry"])
def test_approval_setup_import_rejects_purpose_subject_signature_or_expiry_drift(tmp_path, change):
    setup, request, private, proposal, fingerprint = approval_setup_fixture(tmp_path)
    payload = setup.prepare_generation_grant(request, issuer_id="fixture-research-owner", grant_id="fixture-generation",
        expires_at="2026-10-08T00:00:00Z", now=NOW)
    signature = private.sign(canonical_json(payload)).hex()
    current = NOW
    if change == "purpose":
        payload["purpose"] = "trial_execution"
    elif change == "subject":
        request["roles"]["operator"] = "another-owner"
    elif change == "issuer":
        payload["issuer_id"] = "another-owner"
    elif change in {"trial_id", "approver_id"}:
        payload[change] = "another-subject"
    elif change in {"binding_sha256", "roles_sha256", "allocation_sha256", "engineering_sha256"}:
        payload[change] = "0" * 64
    elif change == "signature":
        signature = "00" * 64
    elif change == "short_signature":
        signature = "00" * 63
    elif change == "nonhex_signature":
        signature = "zz" * 64
    elif change == "payload_field":
        payload["qualification_accepted"] = True
    else:
        current = NOW + timedelta(days=2)
    if change not in {"signature", "short_signature", "nonhex_signature"}:
        signature = private.sign(canonical_json(payload)).hex()
    with pytest.raises((ValueError, PermissionError), match="subject|purpose|signature|issuer|closed|expir"):
        setup.import_generation_signature(payload, signature, request=request, proposal=proposal,
            expected_issuer_id="fixture-research-owner", accepted_fingerprint_sha256=fingerprint, now=current)


def test_approval_setup_cli_writes_only_fresh_public_workspace_artifacts(tmp_path, capsys):
    import json
    setup, _, _, proposal, _ = approval_setup_fixture(tmp_path)
    output = tmp_path / "public-issuer-proposal.json"
    args = ["--propose-public-issuer", "--workspace", str(tmp_path), "--issuer-id", proposal["issuer_id"],
        "--public-key-hex", proposal["public_key_hex"], "--output", str(output)]
    assert setup.main(args) == 0
    assert json.loads(output.read_bytes()) == proposal
    before = output.read_bytes()
    assert setup.main(args) == 2
    assert output.read_bytes() == before
    args[-1] = "relative-output.json"
    assert setup.main(args) == 2
    assert "model_calls" in capsys.readouterr().out


def test_approval_setup_cli_rejects_symlink_reads_and_suppresses_input_text(tmp_path, capsys):
    setup = approval_setup_api()
    marker = "PRIVATE-MATERIAL-MUST-NOT-BE-ECHOED"
    target = tmp_path / "untrusted-request.json"
    target.write_text('{"private_key":"' + marker + '"}')
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    args = ["--prepare-generation", "--workspace", str(tmp_path), "--request", str(alias),
        "--issuer-id", "fixture-owner", "--grant-id", "fixture-generation", "--expires-at", "2026-10-08T00:00:00Z",
        "--output", str(tmp_path / "must-not-exist.json")]
    assert setup.main(args) == 2
    args[4] = str(target)
    assert setup.main(args) == 2
    with pytest.raises(SystemExit) as error:
        setup.main(["--" + marker])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert marker not in captured.out + captured.err
    assert not (tmp_path / "must-not-exist.json").exists()


@pytest.mark.parametrize("identity_field", ["issuer_id", "grant_id"])
def test_approval_setup_rejects_private_pem_in_public_identity_fields(tmp_path, identity_field):
    setup, request, _, proposal, _ = approval_setup_fixture(tmp_path)
    marker = "-----BEGIN PRIVATE KEY-----\nDO-NOT-SERIALIZE\n-----END PRIVATE KEY-----"
    if identity_field == "issuer_id":
        with pytest.raises(ValueError) as error:
            setup.propose_public_issuer(issuer_id=marker, public_key_hex=proposal["public_key_hex"])
    else:
        with pytest.raises(ValueError) as error:
            setup.prepare_generation_grant(request, issuer_id=proposal["issuer_id"], grant_id=marker,
                expires_at="2026-10-08T00:00:00Z", now=NOW)
    assert marker not in str(error.value)
