"""Typed immutable transport evidence; synthetic bytes and no provider calls."""
import base64
import hashlib
import json

import pytest


def test_mediated_transport_capture_accepts_its_bounded_long_wire_strings(tmp_path):
    from devbench.analysis.methods import MethodService
    service = MethodService(tmp_path / "methods")
    payload = {"format": "devbench.mediated-wire-capture/1",
        "provider_request": {"input": "x" * 48_000},
        "raw_response_base64": base64.b64encode(b"x" * 64_000).decode()}
    body = json.dumps(payload).encode()
    source = tmp_path / "synthetic-wire.json"
    source.write_bytes(body)
    retained = service.import_artifact(source, artifact_type="rna_mediated_capture", actor="engineering-fixture")
    assert retained["artifact_type"] == "rna_mediated_capture"
    assert retained["size_bytes"] == len(body) < 1_000_000
    assert retained["sha256"] == hashlib.sha256(body).hexdigest()
    assert service.artifact_path(retained["artifact_id"]).read_bytes() == body
    assert source.read_bytes() == body


@pytest.mark.parametrize("body", [b'{"x":NaN}', b'{"x":1,"x":2}', b"[]"])
def test_mediated_capture_still_requires_finite_unique_json_object(tmp_path, body):
    from devbench.analysis.methods import MethodService
    service = MethodService(tmp_path / "methods")
    source = tmp_path / "invalid-wire.json"
    source.write_bytes(body)
    with pytest.raises(ValueError):
        service.import_artifact(source, artifact_type="rna_mediated_capture", actor="engineering-fixture")
    assert service.list_records("artifact") == []


def test_mediated_capture_has_one_megabyte_limit_before_retention(tmp_path):
    from devbench.analysis.methods import MethodService
    service = MethodService(tmp_path / "methods")
    source = tmp_path / "oversize-wire.json"
    source.write_bytes(b" " * 1_000_000 + b"{}")
    with pytest.raises(ValueError):
        service.import_artifact(source, artifact_type="rna_mediated_capture", actor="engineering-fixture")
    assert service.list_records("artifact") == []


def test_existing_investigation_capture_keeps_its_original_planner_string_bound(tmp_path):
    from devbench.analysis.methods import MethodService
    service = MethodService(tmp_path / "methods")
    source = tmp_path / "old-capture.json"
    source.write_text(json.dumps({"text": "x" * 17_000}))
    with pytest.raises(ValueError, match="finite bounded JSON"):
        service.import_artifact(source, artifact_type="rna_investigation_capture", actor="engineering-fixture")
    assert service.list_records("artifact") == []
