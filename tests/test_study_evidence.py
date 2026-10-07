"""Receipt/configuration checks only; no numerical tool or scoring execution."""
import importlib.util
from pathlib import Path

import pytest


def module():
    assert importlib.util.find_spec("devbench.study.evidence"), "Evidence verifier is missing"
    from devbench.study import evidence
    return evidence


def empty_receipt():
    return {"record_format": "devbench.study.tool-receipts/1", "input_sha256": "a" * 64,
            "implementation_sha256": "b" * 64, "condition": "optional", "event_count": 0,
            "root_receipt": None, "capture_complete": True, "capture_error": None,
            "scope": "coordinator_tool_execution", "clinical_qualification": False}


def test_receipt_read_requires_explicit_authorization():
    with pytest.raises(PermissionError, match="disabled"):
        module().verify_receipts(Path("/missing"), expected_receipt={},
            expected_input_sha256="a" * 64, expected_implementation_sha256="b" * 64)


def test_empty_capture_proves_zero_calls_only(tmp_path):
    from devbench.study.contracts import canonical_json_bytes
    root = empty_receipt()
    (tmp_path / "root.json").write_bytes(canonical_json_bytes(root))
    verified = module().verify_receipts(tmp_path, expected_receipt=root,
        expected_input_sha256="a" * 64, expected_implementation_sha256="b" * 64,
        allow_evidence_read=True)
    assert verified.events == ()
    assert verified.results == {}
    assert verified.root_receipt is None


def test_saved_receipt_cannot_replace_external_expectation(tmp_path):
    from devbench.study.contracts import canonical_json_bytes
    root = empty_receipt()
    (tmp_path / "root.json").write_bytes(canonical_json_bytes({**root, "condition": "enforced"}))
    with pytest.raises(ValueError, match="retained receipt"):
        module().verify_receipts(tmp_path, expected_receipt=root,
            expected_input_sha256="a" * 64, expected_implementation_sha256="b" * 64,
            allow_evidence_read=True)


def test_answer_binding_requires_explicit_authorization():
    with pytest.raises(PermissionError, match="disabled"):
        module().evaluate_workflow(None, None, task_context={})


def test_duplicate_keys_and_nonfinite_json_are_rejected():
    for raw in (b'{"x":1,"x":2}', b'{"x":1e999}', b'{"x":NaN}'):
        with pytest.raises(ValueError):
            module().decode_json(raw)
