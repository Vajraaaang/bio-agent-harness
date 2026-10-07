"""RNA previews use bounded software evidence and never dispatch scientific work."""
import hashlib
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest


def synthetic_snapshot():
    identity = str(uuid4())
    event = {"format": "devbench.rna-trace-event/1", "event_id": str(uuid4()),
        "trace_id": identity, "span_id": identity, "parent_span_id": None,
        "sequence": 0, "attempt": 1, "role": "coordinator", "phase": "workflow",
        "kind": "completion", "source_status": "completed", "dispatch_status": "not_dispatched",
        "capture_status": "unavailable", "reason_code": "none",
        "origin": "retained_authoritative_evidence",
        "clock": {"utc": None, "monotonic_ns": None, "process_alias": str(uuid4()), "timing_basis": "unavailable"},
        "bindings": {"workflow": "a"*64, "test_family": "b"*64},
        "metrics": {"input_tokens": None, "output_tokens": None, "total_tokens": None,
                    "elapsed_seconds": None, "peak_rss_mb": None, "usage_basis": "unavailable",
                    "provider_cost": {"value": None, "currency": None, "source_alias": None, "basis": "unavailable"}}}
    return {"format": "devbench.rna-trace-snapshot/1", "workflow_binding": "a"*64,
            "events": [event], "capability": {"backend_kind": "offline_replay",
                "qualification_status": "offline_qualified", "admitted": False},
            "allocation": 0, "actual_children": 0, "complete": True, "gaps": []}


@pytest.fixture
def snapshot_file(tmp_path):
    source = tmp_path / "source" / "snapshot.json"
    source.parent.mkdir()
    source.write_text(json.dumps(synthetic_snapshot()))
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def preview(source, digest, output, **options):
    from devbench.study.rna_langsmith_export import prepare_rna_export
    return prepare_rna_export(snapshot_path=source, expected_snapshot_sha256=digest,
        output_dir=output, data_scope="synthetic", project="software-pilot",
        allow_evidence_read=True, **options)


@pytest.mark.parametrize("scope", ["clinical", "unclassified"])
def test_scope_rejects_before_any_evidence_read(tmp_path, monkeypatch, scope):
    from devbench.study import rna_langsmith_export as module
    monkeypatch.setattr(module._Evidence, "read", lambda *a, **k: pytest.fail("evidence was read"))
    with pytest.raises(ValueError):
        module.prepare_rna_export(snapshot_path=tmp_path / "absent", output_dir=tmp_path / "out",
            data_scope=scope, project="pilot", expected_snapshot_sha256="a"*64, allow_evidence_read=True)


def test_authorization_rejects_before_read(tmp_path, monkeypatch):
    from devbench.study import rna_langsmith_export as module
    monkeypatch.setattr(module._Evidence, "read", lambda *a, **k: pytest.fail("evidence was read"))
    with pytest.raises(PermissionError):
        module.prepare_rna_export(snapshot_path=tmp_path / "absent", output_dir=tmp_path / "out",
            data_scope="synthetic", project="pilot", expected_snapshot_sha256="a"*64)


def test_exact_hash_rejects_without_writing(snapshot_file, tmp_path):
    source, _ = snapshot_file
    with pytest.raises(ValueError, match="SHA-256"):
        preview(source, "0"*64, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_namespace_repreview_identical_and_source_inert(snapshot_file, tmp_path, monkeypatch):
    source, digest = snapshot_file
    before = source.stat(), source.read_bytes()
    from devbench.study import rna_langsmith_export as module
    monkeypatch.setattr(module.os.environ, "get", lambda *a, **k: pytest.fail("credentials were inspected"))
    first = preview(source, digest, tmp_path / "first")
    second = preview(source, digest, tmp_path / "second", export_namespace=first["export_namespace"])
    assert first["status"] == "prepared" and not first["uploaded"]
    assert Path(first["payload_path"]).read_bytes() == Path(second["payload_path"]).read_bytes()
    serialized = Path(first["payload_path"]).read_text()
    assert "a"*64 not in serialized and "b"*64 not in serialized and str(tmp_path) not in serialized
    assert source.stat().st_mtime_ns == before[0].st_mtime_ns and source.read_bytes() == before[1]
    assert set(p.name for p in source.parent.iterdir()) == {"snapshot.json"}
    assert set(p.name for p in (tmp_path / "first").iterdir()) == {
        "payload.json", "local-evidence.json", "export-manifest.json"}


def test_observer_preview_preserves_live_namespace_and_span_ids(tmp_path, monkeypatch):
    snapshot = synthetic_snapshot()
    root = snapshot["events"][0]
    snapshot["observer_events"] = [copy.deepcopy(root)]
    source = tmp_path / "source" / "snapshot.json"
    source.parent.mkdir()
    source.write_text(json.dumps(snapshot))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    from devbench.study import rna_langsmith_export as module
    monkeypatch.setattr(module.os.environ, "get", lambda *a, **k: pytest.fail("credentials inspected"))
    first = preview(source, digest, tmp_path / "first")
    second = preview(source, digest, tmp_path / "second")
    assert first["export_namespace"] == root["trace_id"]
    assert Path(first["payload_path"]).read_bytes() == Path(second["payload_path"]).read_bytes()
    run = json.loads(Path(first["payload_path"]).read_text())["runs"][0]
    assert run["id"] == root["span_id"] and run["trace_id"] == root["trace_id"]
    with pytest.raises(ValueError, match="live namespace"):
        preview(source, digest, tmp_path / "mismatch", export_namespace=str(uuid4()))
    assert not (tmp_path / "mismatch").exists()


@pytest.mark.parametrize("fault", ["text", "tag", "input", "metadata", "timestamp", "parent"])
def test_remote_payload_tampering_rejected(snapshot_file, tmp_path, fault):
    from devbench.study.rna_langsmith_export import validate_rna_payload
    source, digest = snapshot_file
    prepared = preview(source, digest, tmp_path / "out")
    payload = json.loads(Path(prepared["payload_path"]).read_text())
    span = payload["runs"][0]
    if fault == "text":
        span["name"] = "SECRET_PATIENT"
    elif fault == "tag":
        span["tags"].append("SECRET_PATIENT")
    elif fault == "input":
        span["inputs"] = {"objective": "SECRET_PATIENT"}
    elif fault == "metadata":
        span["extra"]["metadata"]["sample_hash"] = "b"*64
    elif fault == "timestamp":
        span["start_time"] = "SECRET_PATIENT"
    else:
        span["parent_run_id"] = str(uuid4())
    with pytest.raises(ValueError):
        validate_rna_payload(payload)


def test_fresh_disjoint_and_symlink_constraints(snapshot_file, tmp_path):
    source, digest = snapshot_file
    with pytest.raises(ValueError):
        preview(source, digest, source.parent / "out")
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError):
        preview(link, digest, tmp_path / "out")


@pytest.mark.parametrize("fault", ["two_roots", "different_trace"])
def test_delivery_tree_validation_rejects_forged_roots(snapshot_file, tmp_path, fault):
    from devbench.study.rna_langsmith_export import validate_rna_payload
    source, digest = snapshot_file
    prepared = preview(source, digest, tmp_path / "out")
    payload = json.loads(Path(prepared["payload_path"]).read_text())
    child = copy.deepcopy(payload["runs"][0])
    child["id"] = str(uuid4())
    if fault == "different_trace":
        child["parent_run_id"] = payload["runs"][0]["id"]
        child["trace_id"] = str(uuid4())
    payload["runs"].append(child)
    with pytest.raises(ValueError, match="root and trace"):
        validate_rna_payload(payload)


def run_cli(argv, capsys):
    from devbench.study.cli import main
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_preview_and_missing_status(snapshot_file, tmp_path, capsys):
    source, digest = snapshot_file
    result = run_cli(["rna-langsmith-preview", "--snapshot", str(source),
        "--expected-snapshot-sha256", digest, "--output", str(tmp_path / "preview"),
        "--data-scope", "synthetic", "--project", "software-pilot", "--authorize-evidence-read"], capsys)
    assert result["status"] == "prepared"
    from devbench.study.cli import main
    absent = tmp_path / "absent.sqlite"
    assert main(["rna-langsmith-status", "--outbox", str(absent)]) == 2
    assert not absent.exists()


def test_delivery_requires_exact_approval_before_credentials(tmp_path, monkeypatch):
    from devbench.study import rna_langsmith_export as module
    monkeypatch.setattr(module.os.environ, "get", lambda *a, **k: pytest.fail("credentials inspected"))
    with pytest.raises(PermissionError):
        module.deliver_rna_outbox(outbox_path=tmp_path / "missing.sqlite", expected_sha256="0"*64)


def test_outbox_hash_and_payload_gate_before_credentials(snapshot_file, tmp_path, monkeypatch):
    from devbench.study import rna_langsmith_export as module
    from devbench.study.langsmith_outbox import ExportOutbox
    source, digest = snapshot_file
    prepared = preview(source, digest, tmp_path / "preview")
    box_path = tmp_path / "delivery" / "outbox.sqlite"
    ExportOutbox.create(box_path, payload_path=prepared["payload_path"],
        expected_sha256=prepared["payload_sha256"], endpoint="https://api.smith.langchain.com", project="software-pilot")
    monkeypatch.setattr(module.os.environ, "get", lambda *a, **k: pytest.fail("credentials inspected"))
    with pytest.raises(ValueError):
        module.deliver_rna_outbox(outbox_path=box_path, expected_sha256="0"*64, allow_upload=True)
    Path(prepared["payload_path"]).write_text("{}")
    with pytest.raises(ValueError):
        module.deliver_rna_outbox(outbox_path=box_path, expected_sha256=prepared["payload_sha256"], allow_upload=True)
