"""Portable contract tests; all signing keys here are ephemeral synthetic fixtures."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError


def api(name="contracts"):
    path = Path(__file__).resolve().parents[1] / "devharness_controlled" / f"{name}.py"
    assert path.is_file(), f"Controlled {name} implementation is missing"
    return importlib.import_module(f"devharness_controlled.{name}")


def plan_values(**updates):
    values = dict(schema_version="controlled_analysis/1", plan_id="synthetic-plan", revision=1,
                  intended_use_ref="synthetic-validation-only", study_id="synthetic-study",
                  data_classification="synthetic", data_type="continuous",
                  selection_criterion="fixed", spline_df=3, time_column="collection_age",
                  time_unit="days", categorical_covariates=("batch",), continuous_covariates=(),
                  hypothesis_genes=("Sox2", "β-actin"), calibration_genes=(), normalization="none",
                  biological_unit_column="donor_id", reference_sample_id="sample-1",
                  counts_sha256="a" * 64, metadata_sha256="b" * 64,
                  implementation_sha256="c" * 64, environment_sha256="d" * 64,
                  max_memory_mb=256, timeout_sec=60)
    return {**values, **updates}


def plan(**updates):
    return api().AnalysisPlan(**plan_values(**updates))


def test_plan_roundtrips_exact_canonical_utf8_and_digest(tmp_path):
    contract = api()
    original = plan()
    encoded = original.canonical_bytes()
    assert b"\\u03b2" not in encoded
    assert "β".encode() in encoded
    assert encoded == json.dumps(original.model_dump(mode="json"), ensure_ascii=False,
                                 sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    assert original.sha256() == hashlib.sha256(encoded).hexdigest()
    assert contract.parse_plan_bytes(encoded) == original
    source = tmp_path / "plan.json"
    source.write_bytes(encoded)
    assert contract.load_plan(source) == original


def test_plan_nested_collections_and_fields_are_immutable():
    original = plan()
    assert isinstance(original.hypothesis_genes, tuple)
    with pytest.raises(ValidationError):
        original.revision = 2
    with pytest.raises(TypeError):
        original.hypothesis_genes[0] = "changed"
    with pytest.raises(ValidationError):
        plan(hypothesis_genes=["Sox2"])


@pytest.mark.parametrize("updates", [
    {"unexpected": True}, {"formula": "arbitrary_code()"}, {"selection_criterion": "bic"},
    {"selection_criterion": "aic"}, {"selection_criterion": "gcv"}, {"spline_df": True},
    {"spline_df": "3"}, {"spline_df": 1}, {"spline_df": 6}, {"revision": 0},
    {"revision": True}, {"intended_use_ref": " "}, {"study_id": ""},
    {"data_classification": "clinical"}, {"data_type": "auto"},
    {"time_column": "age + batch"}, {"time_column": "age\n"}, {"time_column": "class"},
    {"time_unit": " "}, {"biological_unit_column": ""},
    {"biological_unit_column": "unknown"}, {"reference_sample_id": ""},
    {"biological_unit_column": "sample_id"}, {"biological_unit_column": "collection_age"},
    {"biological_unit_column": "batch"},
    {"categorical_covariates": ("batch", "batch")},
    {"continuous_covariates": ("batch",)}, {"categorical_covariates": ("bad-name",)},
    {"continuous_covariates": ("collection_age",)},
    {"hypothesis_genes": ()}, {"hypothesis_genes": ("Sox2", "Sox2")},
    {"hypothesis_genes": tuple(f"gene{i}" for i in range(101))},
    {"calibration_genes": ("Sox2", "Sox2")},
    {"calibration_genes": tuple(f"gene{i}" for i in range(5001))},
    {"normalization": "rle"}, {"counts_sha256": "A" * 64},
    {"metadata_sha256": "b" * 63}, {"metadata_sha256": "b" * 64 + "\n"},
    {"implementation_sha256": "C" * 64}, {"environment_sha256": "d" * 63},
    {"max_memory_mb": 0}, {"max_memory_mb": 65537},
    {"max_memory_mb": 2.5}, {"timeout_sec": 0}, {"timeout_sec": 86401},
    {"timeout_sec": float("inf")}, {"timeout_sec": True},
])
def test_plan_rejects_unlocked_or_ambiguous_inputs(updates):
    with pytest.raises(ValidationError):
        plan(**updates)


@pytest.mark.parametrize("missing", ["biological_unit_column", "reference_sample_id", "intended_use_ref",
                                     "implementation_sha256", "environment_sha256"])
def test_plan_requires_explicit_study_contract(missing):
    values = plan_values()
    del values[missing]
    with pytest.raises(ValidationError):
        api().AnalysisPlan(**values)


@pytest.mark.parametrize("identifier", ["numeric_time", "C", "cr", "bs", "I", "Q", "__", "__import__", "x__y"])
@pytest.mark.parametrize("field", ["time_column", "biological_unit_column", "categorical_covariates",
                                   "continuous_covariates"])
def test_reserved_formula_identifiers_are_rejected(identifier, field):
    value = (identifier,) if field.endswith("covariates") else identifier
    with pytest.raises(ValidationError):
        plan(**{field: value})


def test_count_plan_requires_locked_calibration_family():
    count = dict(data_type="counts", normalization="rle")
    for panel in ((), ("Sox2",)):
        with pytest.raises(ValidationError):
            plan(**count, calibration_genes=panel)
    with pytest.raises(ValidationError):
        plan(data_type="counts", normalization="none", calibration_genes=("Sox2", "β-actin"))
    assert plan(**count, calibration_genes=("Sox2", "β-actin", "other")).normalization == "rle"


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}',
                                  b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}',
                                  b'{"a":1e999}', b'\xff', b'\xef\xbb\xbf{}'])
def test_json_boundary_rejects_duplicate_keys_nonfinite_and_invalid_utf8(raw):
    with pytest.raises((ValueError, UnicodeError)):
        api().parse_json_bytes(raw)


@pytest.mark.parametrize("value", [{1: "coercion"}, {"a": float("nan")},
                                    {"a": (float("inf"),)}, {"a": "\ud800"}])
def test_canonical_json_rejects_ambiguous_values(value):
    with pytest.raises((TypeError, ValueError, UnicodeError)):
        api().canonical_json_bytes(value)


def test_plan_load_is_size_bounded_and_requires_absolute_path(tmp_path):
    source = tmp_path / "large.json"
    source.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="limit|large|1 MB"):
        api().load_plan(source)
    with pytest.raises(ValueError, match="absolute"):
        api().load_plan("relative.json")


NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def signing_fixture(role, subject=None, key_id=None, private_key=None):
    trust = api("trust")
    # Deliberately generated only in this synthetic test fixture, never production code.
    private_key = private_key or Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    signer = trust.TrustedSigner(key_id=key_id or f"synthetic-{role}-key",
                                subject_id=subject or f"synthetic-{role}", role=role,
                                public_key_pem=public_key)
    return signer, private_key


def signed_approval(analysis_plan, signer, private_key, **updates):
    trust = api("trust")
    payload = trust.ApprovalPayload(
        **{**dict(schema_version="controlled_approval/1", algorithm="Ed25519",
                  plan_sha256=analysis_plan.sha256(), plan_id=analysis_plan.plan_id,
                  revision=analysis_plan.revision, study_id=analysis_plan.study_id,
                  key_id=signer.key_id, signer_id=signer.subject_id, meaning=signer.role,
                  issued_at=NOW - timedelta(minutes=5), expires_at=NOW + timedelta(days=1)), **updates})
    signature = base64.b64encode(private_key.sign(payload.canonical_bytes())).decode("ascii")
    return trust.Approval(payload=payload, signature=signature)


def approved_plan():
    analysis_plan = plan()
    fixtures = [signing_fixture("author"), signing_fixture("reviewer")]
    signers = [item[0] for item in fixtures]
    approvals = [signed_approval(analysis_plan, *item) for item in fixtures]
    return analysis_plan, fixtures, signers, approvals


def test_real_ed25519_approvals_bind_plan_and_produce_bounded_scope_receipts():
    analysis_plan, _, signers, approvals = approved_plan()
    receipts = api("trust").verify_plan_approvals(analysis_plan, approvals, signers, now=NOW)
    assert len(receipts) == 2
    assert {r["meaning"] for r in receipts} == {"author", "reviewer"}
    assert all(r["schema_version"] == "controlled_approval_receipt/1" for r in receipts)
    assert all(r["plan_sha256"] == analysis_plan.sha256() for r in receipts)
    assert all(r["scope"] == "configured-key signatures only; human identity/clinical qualification not established"
               for r in receipts)
    assert all("clinical_release_allowed" not in r for r in receipts)
    json.dumps(receipts, allow_nan=False)
    parsed = [api("trust").Approval.model_validate_json(a.model_dump_json()) for a in approvals]
    assert api("trust").verify_plan_approvals(analysis_plan, parsed, {s.key_id: s for s in signers}, now=NOW)
    assert api("trust").verify_plan_approvals(analysis_plan, [a.model_dump(mode="json") for a in approvals],
                                             [s.model_dump() for s in signers], now=NOW)


@pytest.mark.parametrize("change", [{"revision": 2}, {"study_id": "another-study"},
                                    {"hypothesis_genes": ("different",)}, {"counts_sha256": "c" * 64},
                                    {"implementation_sha256": "e" * 64}, {"environment_sha256": "f" * 64}])
def test_approval_cannot_be_replayed_for_a_different_plan(change):
    _, _, signers, approvals = approved_plan()
    with pytest.raises(ValueError):
        api("trust").verify_plan_approvals(plan(**change), approvals, signers, now=NOW)


def test_payload_tamper_and_signature_tamper_fail():
    analysis_plan, _, signers, approvals = approved_plan()
    for update in ({"signature": base64.b64encode(b"x" * 64).decode()},
                   {"payload": approvals[0].payload.model_copy(update={"expires_at": NOW + timedelta(days=2)})}):
        tampered = approvals[0].model_copy(update=update)
        with pytest.raises(ValueError):
            api("trust").verify_plan_approvals(analysis_plan, [tampered, approvals[1]], signers, now=NOW)


@pytest.mark.parametrize("updates", [{"issued_at": NOW + timedelta(seconds=1)},
                                     {"expires_at": NOW}, {"expires_at": NOW - timedelta(seconds=1)},
                                     {"signer_id": "somebody-else"}, {"key_id": "unknown-key"},
                                     {"meaning": "reviewer"}, {"plan_id": "wrong-plan"},
                                     {"revision": 2}, {"study_id": "wrong-study"}])
def test_valid_signature_does_not_override_time_identity_role_or_binding(updates):
    analysis_plan, fixtures, signers, approvals = approved_plan()
    approvals[0] = signed_approval(analysis_plan, *fixtures[0], **updates)
    with pytest.raises(ValueError):
        api("trust").verify_plan_approvals(analysis_plan, approvals, signers, now=NOW)


def test_approval_dates_require_timezone_and_order():
    analysis_plan, fixtures, _, _ = approved_plan()
    for update in ({"issued_at": NOW.replace(tzinfo=None)}, {"expires_at": NOW.replace(tzinfo=None)},
                   {"expires_at": NOW - timedelta(days=1)}):
        with pytest.raises(ValueError):
            signed_approval(analysis_plan, *fixtures[0], **update)
    _, _, signers, approvals = approved_plan()
    with pytest.raises(ValueError, match="timezone"):
        api("trust").verify_plan_approvals(analysis_plan, approvals, signers, now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize("same_subject,same_key", [(True, False), (False, True), (True, True)])
def test_author_and_reviewer_require_distinct_subjects_and_actual_keys(same_subject, same_key):
    analysis_plan = plan()
    author, private_key = signing_fixture("author")
    reviewer, reviewer_key = signing_fixture("reviewer", subject=author.subject_id if same_subject else None,
                                            private_key=private_key if same_key else None)
    approvals = [signed_approval(analysis_plan, author, private_key),
                 signed_approval(analysis_plan, reviewer, reviewer_key)]
    with pytest.raises(ValueError):
        api("trust").verify_plan_approvals(analysis_plan, approvals, [author, reviewer], now=NOW)


def test_missing_duplicate_and_unsigned_trust_fail_closed():
    analysis_plan, _, signers, approvals = approved_plan()
    trust = api("trust")
    for supplied_approvals, supplied_signers in (([], signers), (approvals[:1], signers),
                                               (approvals, []), (approvals, signers[:1]),
                                               ([approvals[0], approvals[0]], signers),
                                               (approvals, signers + [signers[0]]),
                                               ([{"status": "verified"}] * 2, signers),
                                               (approvals, {"wrong-key": signers[0]})):
        with pytest.raises((TypeError, ValueError)):
            trust.verify_plan_approvals(analysis_plan, supplied_approvals, supplied_signers, now=NOW)


def test_strict_approval_models_reject_extra_fields_wrong_algorithm_and_invalid_key():
    analysis_plan, fixtures, signers, approvals = approved_plan()
    trust = api("trust")
    for update in ({"algorithm": "none"}, {"extra": "ignored"}, {"revision": True}):
        with pytest.raises(ValueError):
            signed_approval(analysis_plan, *fixtures[0], **update)
    for signature in ("", "unsigned", base64.b64encode(b"x" * 63).decode(), approvals[0].signature + "\n"):
        with pytest.raises(ValueError):
            trust.Approval(payload=approvals[0].payload, signature=signature)
    with pytest.raises(ValueError):
        trust.TrustedSigner(**{**signers[0].model_dump(), "public_key_pem": "not a public key"})
    invalid = approvals[0].model_dump(mode="json")
    invalid["status"] = "verified"
    with pytest.raises(ValueError):
        trust.verify_plan_approvals(analysis_plan, [invalid, approvals[1]], signers, now=NOW)


def test_unvalidated_model_copy_cannot_bypass_contracts():
    analysis_plan, _, signers, approvals = approved_plan()
    invalid_plan = analysis_plan.model_copy(update={"selection_criterion": "bic"})
    with pytest.raises(ValueError):
        api("trust").verify_plan_approvals(invalid_plan, approvals, signers, now=NOW)
