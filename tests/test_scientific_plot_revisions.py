"""Portable saved-geometry receipts; synthetic fixtures do not validate science."""

import hashlib
import importlib
import importlib.util
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from devbench.analysis.scientific_plot_contract import panel, row


def api():
    name = "devbench.analysis.scientific_plot_revisions"
    assert importlib.util.find_spec(name), (
        "Scientific presentation revisions are missing"
    )
    return importlib.import_module(name)


@pytest.fixture
def source(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir(mode=0o700)
    artifact = parent / "saved-source.json"
    artifact.write_text('{"saved_values":[1,2],"synthetic":true}')
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    rows = [row("gene-1", 1, 2), row("gene-2", 3, 4)]
    p = panel(
        "saved_embedding",
        "Synthetic saved coordinates",
        rows,
        axes={
            "x": {
                "label": "X",
                "unit": "embedding_coordinate",
                "transform": "identity",
            },
            "y": {
                "label": "Y",
                "unit": "embedding_coordinate",
                "transform": "identity",
            },
        },
        limitations=["Synthetic drawing only."],
    )
    workspace = {
        "format": "devbench.scientific-plot-workspace/1",
        "title": "Synthetic workspace",
        "source": {
            "sha256": digest,
            "run_id": "synthetic-revision",
            "kind": "synthetic",
        },
        "panels": [p],
        "entities": [],
        "limitations": ["No biological validation."],
    }
    request = {
        "panel_id": p["id"],
        "scope": "current",
        "bounds": [0, 5, 0, 5],
        "selected_link": {"kind": "gene", "id": "gene-1"},
        "reference_lines": [{"axis": "x", "value": 2, "label": "Display guide"}],
    }
    ref = {
        "source_sha256": digest,
        "run_id": "synthetic-revision",
        "artifact_id": "saved-source",
        "artifact_path": str(artifact),
    }
    return workspace, request, ref


@pytest.fixture
def revision(source, workspace_tmp_path):
    workspace, request, ref = source
    root = workspace_tmp_path / "revision"
    receipt = api().create_scientific_plot_revision(
        root, workspace, request, parent_ref=ref
    )
    return root, receipt


def test_revision_seals_exact_workspace_request_and_unchanged_parent(
    source, workspace_tmp_path, monkeypatch
):
    from devbench.analysis import workbench

    workspace, request, ref = source
    originals = deepcopy((workspace, request, ref))
    before = Path(ref["artifact_path"]).read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Presentation rendering launched a scientific stage")

    for name in ("run_stage", "execute_bundle", "prepare_bundle"):
        monkeypatch.setattr(workbench, name, forbidden)
    root = workspace_tmp_path / "revision"
    receipt = api().create_scientific_plot_revision(
        root, workspace, request, parent_ref=ref
    )
    verified = api().load_scientific_revision_bundle(root, receipt["root_hash"])
    assert verified["workspace"] == workspace and verified["request"] == request
    assert verified["source_binding"]["verification"] == "local_file_hash_checked"
    assert verified["source_binding"]["source_sha256"] == workspace["source"]["sha256"]
    assert receipt["numerical_stage_launches"] == receipt["model_sessions"] == 0
    assert receipt["status"] == "completed_presentation"
    assert verified["manifest"]["operation_kind"] == "presentation"
    assert (root / "payload/figure.svg").read_bytes().startswith(b"<?xml")
    assert (root / "payload/figure.pdf").read_bytes().startswith(b"%PDF-")
    assert (root / "payload/figure.png").read_bytes().startswith(b"\x89PNG")
    assert (workspace, request, ref) == originals
    assert Path(ref["artifact_path"]).read_bytes() == before
    assert (
        str(workspace_tmp_path)
        not in (root / "payload/source-binding.json").read_text()
    )


def test_export_relocate_and_replay_uses_only_included_geometry(
    source, workspace_tmp_path
):
    workspace, request, ref = source
    bundle = api().export_scientific_revision_bundle(
        workspace, request, parent_ref=ref, storage_root=workspace_tmp_path
    )
    assert isinstance(bundle["bytes"], bytes)
    Path(ref["artifact_path"]).parent.rename(workspace_tmp_path / "parent-unavailable")
    loaded = api().load_scientific_revision_bundle(
        bundle["bytes"], bundle["root_hash"], storage_root=workspace_tmp_path
    )
    assert loaded["workspace"] == workspace
    output = workspace_tmp_path / "replayed"
    result = api().replay_scientific_plot_revision(
        bundle["bytes"], bundle["root_hash"], output
    )
    verified = api().load_scientific_revision_bundle(output, result["root_hash"])
    assert verified["manifest"]["replay_of"] == bundle["root_hash"]
    assert verified["source_binding"] == loaded["source_binding"]
    with zipfile.ZipFile(io.BytesIO(bundle["bytes"])) as archive:
        for suffix in ("svg", "pdf", "png"):
            assert (output / f"payload/figure.{suffix}").read_bytes() == archive.read(
                f"payload/figure.{suffix}"
            )


@pytest.mark.parametrize(
    "name",
    [
        "payload/workspace.json",
        "payload/request.json",
        "payload/figure.png",
        "payload/source-binding.json",
        "payload/manifest.json",
        "root-receipt.json",
        "evidence/root.sha256",
        "evidence/manifest.json",
    ],
)
def test_tampering_any_binding_or_output_is_rejected(revision, name):
    root, receipt = revision
    path = root / name
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        api().load_scientific_revision_bundle(root, receipt["root_hash"])


@pytest.mark.parametrize(
    "extra",
    [
        {"normalization": "new"},
        {"fit": {}},
        {"bandwidth": 0.2},
        {"source_path": "/tmp/no"},
        {"parameters": {"alpha": 0.05}},
    ],
)
def test_analysis_settings_cannot_enter_revision(source, tmp_path, extra):
    workspace, request, ref = source
    with pytest.raises(ValueError):
        api().create_scientific_plot_revision(
            tmp_path / "forbidden", workspace, {**request, **extra}, parent_ref=ref
        )
    assert not (tmp_path / "forbidden").exists()


def test_declared_parent_is_not_labeled_independently_verified(
    source, workspace_tmp_path
):
    workspace, request, ref = source
    del ref["artifact_path"]
    receipt = api().create_scientific_plot_revision(
        workspace_tmp_path / "declared", workspace, request, parent_ref=ref
    )
    loaded = api().load_scientific_revision_bundle(
        workspace_tmp_path / "declared", receipt["root_hash"]
    )
    assert (
        loaded["source_binding"]["verification"] == "caller_asserted_source_reference"
    )
    assert loaded["source_binding"]["source_artifact"] is None


def test_parent_mismatch_and_overlapping_output_fail_before_creation(source, tmp_path):
    workspace, request, ref = source
    for invalid in (
        {**ref, "source_sha256": "0" * 64},
        {**ref, "run_id": "other"},
        {**ref, "workspace_sha256": "0" * 64},
    ):
        with pytest.raises(ValueError):
            api().create_scientific_plot_revision(
                tmp_path / "bad", workspace, request, parent_ref=invalid
            )
        assert not (tmp_path / "bad").exists()
    with pytest.raises(ValueError):
        api().create_scientific_plot_revision(
            Path(ref["artifact_path"]).parent / "child",
            workspace,
            request,
            parent_ref=ref,
        )


def test_parent_change_during_render_cannot_publish_receipt(
    source, workspace_tmp_path, monkeypatch
):
    from devbench.analysis import scientific_plot_artifacts

    workspace, request, ref = source
    render = scientific_plot_artifacts.render_scientific_plot

    def mutating(*args, **kwargs):
        Path(ref["artifact_path"]).write_bytes(b"changed parent")
        return render(*args, **kwargs)

    monkeypatch.setattr(scientific_plot_artifacts, "render_scientific_plot", mutating)
    output = workspace_tmp_path / "mutation"
    with pytest.raises(ValueError, match="parent|source"):
        api().create_scientific_plot_revision(
            output, workspace, request, parent_ref=ref
        )
    assert not (output / "root-receipt.json").exists()
    assert (output / "failure.json").exists()


def test_wrong_expected_root_and_installed_renderer_drift_refuse(
    revision, workspace_tmp_path, monkeypatch
):
    root, receipt = revision
    with pytest.raises(ValueError):
        api().load_scientific_revision_bundle(root, "0" * 64)
    monkeypatch.setattr(
        api(), "scientific_renderer_identity", lambda: {"changed": True}
    )
    with pytest.raises(ValueError, match="renderer"):
        api().replay_scientific_plot_revision(
            root, receipt["root_hash"], workspace_tmp_path / "drift"
        )
    assert not (workspace_tmp_path / "drift").exists()


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "payload/unknown.json", "payload/../escape"]
)
def test_zip_member_allowlist_prevents_traversal(source, workspace_tmp_path, name):
    workspace, request, ref = source
    bundle = api().export_scientific_revision_bundle(
        workspace, request, parent_ref=ref, storage_root=workspace_tmp_path
    )
    buffer = io.BytesIO(bundle["bytes"])
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr(name, b"unsafe")
    with pytest.raises(ValueError):
        api().load_scientific_revision_bundle(
            buffer.getvalue(), bundle["root_hash"], storage_root=workspace_tmp_path
        )


def test_local_root_receipt_requires_exact_bytes(revision):
    root, receipt = revision
    path = root / "root-receipt.json"
    changed = json.loads(path.read_text())
    changed["root_hash"] = "0" * 64
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError):
        api().load_scientific_revision_bundle(root, receipt["root_hash"])


def test_embedded_image_and_nullable_artifact_identity_survive_portable_replay(
    source, workspace_tmp_path
):
    from devbench.analysis.scientific_plot_suite import (
        prepare_scientific_plot_workspace,
    )

    fixture_path = Path(__file__).with_name("test_plot_adapters_spatial.py")
    spec = importlib.util.spec_from_file_location(
        "revision_spatial_fixture", fixture_path
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    workspace, _, ref = source
    packet = next(
        p
        for p in fixtures.fixture_packets()
        if p["capability"] == "spatial_tissue_overlay"
    )
    workspace = prepare_scientific_plot_workspace(
        {"format": "devbench.scientific-plot-input/1", "plots": [packet]},
        source=workspace["source"],
    )
    request = {"panel_id": workspace["panels"][0]["id"], "scope": "complete"}
    ref["artifact_id"] = None
    bundle = api().export_scientific_revision_bundle(
        workspace, request, parent_ref=ref, storage_root=workspace_tmp_path
    )
    Path(ref["artifact_path"]).parent.rename(workspace_tmp_path / "parent-unavailable")
    receipt = api().replay_scientific_plot_revision(
        bundle["bytes"], bundle["root_hash"], workspace_tmp_path / "replayed"
    )
    loaded = api().load_scientific_revision_bundle(
        workspace_tmp_path / "replayed", receipt["root_hash"]
    )
    assert loaded["source_binding"]["artifact_id"] is None
    assert loaded["workspace"] == workspace
    assert (
        b"<image" in (workspace_tmp_path / "replayed/payload/figure.svg").read_bytes()
    )
    image = next(
        mark["image"]
        for mark in loaded["workspace"]["panels"][0]["marks"]
        if mark["type"] == "image"
    )
    assert image == packet["data"]["image"]


def test_archive_member_byte_bound_is_enforced_before_extraction(
    revision, workspace_tmp_path, monkeypatch
):
    root, receipt = revision
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("payload/workspace.json", b"x" * 32)
    monkeypatch.setattr(api(), "MAX_MEMBER_BYTES", 16)
    with pytest.raises(ValueError, match="unsafe_archive_member"):
        api().load_scientific_revision_bundle(
            buffer.getvalue(), receipt["root_hash"], storage_root=workspace_tmp_path
        )


def test_symlink_and_duplicate_archive_members_are_rejected(
    revision, workspace_tmp_path
):
    _, receipt = revision
    for symlink in (False, True):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            entry = zipfile.ZipInfo("payload/workspace.json")
            if symlink:
                entry.external_attr = 0o120777 << 16
            archive.writestr(entry, b"{}")
            if not symlink:
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.writestr(entry, b"{}")
        with pytest.raises(ValueError):
            api().load_scientific_revision_bundle(
                buffer.getvalue(), receipt["root_hash"], storage_root=workspace_tmp_path
            )


def test_workspace_above_metadata_limit_keeps_exact_source_custody_and_replays(
    source, workspace_tmp_path, monkeypatch
):
    """Byte-limit/custody regression with stubbed rendering; not image proof."""
    from devbench.analysis import scientific_plot_artifacts

    workspace, request, ref = source
    retained = "bounded synthetic saved detail " + "x" * 16_100_000
    artifact = Path(ref["artifact_path"])
    artifact.write_text(json.dumps({"synthetic_saved_detail": retained}))
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    workspace["source"]["sha256"] = ref["source_sha256"] = digest
    workspace["panels"][0]["rows"][0]["details"]["retained"] = retained
    calls = []

    def stub_render(saved, saved_request, *, format):
        assert saved["panels"][0]["rows"][0]["details"]["retained"] == retained
        assert saved_request == request
        calls.append(format)
        return ("stub-render-" + format).encode()

    monkeypatch.setattr(
        scientific_plot_artifacts, "render_scientific_plot", stub_render
    )
    bundle = api().export_scientific_revision_bundle(
        workspace, request, parent_ref=ref, storage_root=workspace_tmp_path
    )
    with zipfile.ZipFile(io.BytesIO(bundle["bytes"])) as archive:
        payload = archive.read("payload/workspace.json")
    assert 16_000_000 < len(payload) < api().MAX_MEMBER_BYTES
    loaded = api().load_scientific_revision_bundle(
        bundle["bytes"], bundle["root_hash"], storage_root=workspace_tmp_path
    )
    assert loaded["workspace"] == workspace
    assert (
        loaded["source_binding"]["workspace_sha256"]
        == hashlib.sha256(payload).hexdigest()
    )
    assert loaded["source_binding"]["source_artifact"]["sha256"] == digest
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest
    artifact.parent.rename(workspace_tmp_path / "parent-unavailable")
    output = workspace_tmp_path / "large-replay"
    result = api().replay_scientific_plot_revision(
        bundle["bytes"], bundle["root_hash"], output
    )
    assert (
        api().load_scientific_revision_bundle(output, result["root_hash"])["workspace"]
        == workspace
    )
    assert calls == ["svg", "pdf", "png"] * 2
