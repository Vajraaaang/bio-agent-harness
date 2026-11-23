"""Legacy sealed bytes retain their historical failure and inference meanings."""

import hashlib
import json

import pytest


def test_legacy_readonly_verifier_never_upgrades_failure(workspace_tmp_path):
    from devbench.analysis import bundles

    root = workspace_tmp_path / "legacy"
    root.mkdir()
    (root / "parameters.json").write_text(
        json.dumps({"execution_tier": "local", "data_sha256": "a" * 64})
    )
    (root / "source.zip").write_bytes(b"historical source evidence, never execute")
    (root / "result.json").write_text(
        json.dumps({"status": "scientific_failure", "q_value": 0.4})
    )
    receipt = bundles.seal_bundle(root, status="scientific_failure")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    assert callable(getattr(bundles, "verify_legacy_readonly", None)), (
        "Bounded legacy read-only import is required"
    )
    report = bundles.verify_legacy_readonly(root, receipt["manifest_sha256"])
    assert report["status"] == "scientific_failure"
    assert report["qualified_in_regime"] is False
    assert report["current_use"] == "unavailable"
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert "family_allocation" in report["missing_semantics"]
    assert report["q_scope"] == "historical_unmodified"


def _legacy(root):
    from devbench.analysis import bundles

    root.mkdir(mode=0o700)
    (root / "parameters.json").write_text(
        json.dumps(
            {
                "execution_tier": "local",
                "data_sha256": "a" * 64,
                "q_scope": "legacy_local",
                "prespecified": False,
            }
        )
    )
    (root / "source.zip").write_bytes(b"original source, do not execute")
    (root / "failure.json").write_text(
        json.dumps(
            {"status": "scientific_failure", "q_value": 0.4, "reason": "old fit failed"}
        )
    )
    return bundles.seal_bundle(root, status="scientific_failure")["manifest_sha256"]


def test_episode_cli_verification_needs_no_execution_or_output(
    tmp_path, monkeypatch, capsys
):
    from devbench.analysis.episode_cli import main
    from devbench.engine import execution_snapshot

    root = tmp_path / "legacy"
    receipt = _legacy(root)
    monkeypatch.setattr(
        execution_snapshot,
        "launch_episode",
        lambda *a, **k: pytest.fail("Verification launched execution"),
    )
    assert main(["--verify-bundle", str(root), "--expected-root", receipt]) == 0
    report = json.loads(capsys.readouterr().out)
    assert (
        report["status"] == "scientific_failure"
        and report["numerical_stage_launches"] == 0
    )


@pytest.mark.parametrize("member", ["unrecorded.json", ".hidden", "nested/extra.json"])
def test_legacy_unrecorded_files_are_not_silently_ignored(tmp_path, member):
    from devbench.analysis import bundles

    root = tmp_path / "legacy"
    receipt = _legacy(root)
    extra = root / member
    extra.parent.mkdir(exist_ok=True)
    extra.write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        bundles.verify_legacy_readonly(root, receipt)


def test_legacy_import_retains_exact_bytes_without_new_scientific_semantics(
    workspace_tmp_path,
):
    from devbench.analysis import bundles
    from devbench.analysis.scientific_publication import AuditReplayEntitlement
    from tests.test_rna_v24_replay import _broker

    root = workspace_tmp_path / "legacy"
    receipt = _legacy(root)
    before = {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    raw = bundles._zip_replay(before)
    broker = _broker(workspace_tmp_path)
    broker.register_source(
        "legacy",
        content_sha256=hashlib.sha256(raw).hexdigest(),
        roots=("a" * 64, receipt),
        tags=("audit_replay",),
        classification="target_outcome",
    )
    entitlement = AuditReplayEntitlement(
        broker.delivery("audit", "legacy"), hashlib.sha256(raw).hexdigest()
    )
    output = workspace_tmp_path / "imported"
    report = bundles.import_legacy_readonly(
        raw, output, receipt, entitlement=entitlement
    )
    assert {
        str(p.relative_to(output)): p.read_bytes()
        for p in output.rglob("*")
        if p.is_file()
    } == before
    assert (
        report["status"] == "scientific_failure"
        and report["q_scope"] == "historical_unmodified"
    )
    assert (
        report["qualified_in_regime"] is False
        and "prespecification" in report["missing_semantics"]
    )
    assert not (output / "lineage.json").exists()


def test_legacy_reader_bounds_source_bytes_before_hashing(tmp_path):
    from devbench.adaptive.legacy import LegacyRejected, import_legacy_source

    source = tmp_path / "oversize.json"
    with source.open("wb") as stream:
        stream.truncate(16 * 1024**2 + 1)
    with pytest.raises(LegacyRejected, match="BOUND"):
        import_legacy_source(
            {
                "source_kind": "episode",
                "source_path": str(source),
                "source_sha256": "a" * 64,
                "original_claim_status": "failed",
            }
        )
