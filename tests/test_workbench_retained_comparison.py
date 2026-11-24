"""Retained comparison admission, custody and offline readback."""

import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from devbench.platform.plugin_jobs import JobStore
from devbench.platform.workbench_retained_comparison import (
    local_comparison_figure,
    local_comparison_plotly,
    local_comparison_view,
    retain_local_comparison,
)


@pytest.fixture
def comparison(workspace_tmp_path, monkeypatch):
    workspace = workspace_tmp_path / "studio"
    workspace.mkdir(mode=0o700)
    bundle = workspace_tmp_path / "bundle"
    bundle.mkdir()
    session_id, plan_id, investigation_id = "1" * 32, "2" * 32, "3" * 32
    profiles = [
        {"sha256": "f" * 64, "size_bytes": 172, "filename": "e09.h5ad"},
        {"sha256": "c" * 64, "size_bytes": 294, "filename": "e10.h5ad"},
    ]
    plan = {
        "session_id": session_id,
        "plan_id": plan_id,
        "data_classification": "private",
        "profiles": profiles,
    }
    _, plan_sha256 = JobStore(workspace).freeze(plan)
    binding = SimpleNamespace(
        delivery=SimpleNamespace(context_id=f"plugin-scientist-{session_id}"),
        require_owner=lambda scientific: (plan, None),
    )
    monkeypatch.setattr(
        "devbench.platform.workbench_retained_comparison.ensure_adaptive_binding",
        lambda app, requested_id: binding if requested_id == session_id else None,
    )
    result_ref = "a" * 64
    app = SimpleNamespace(
        workspace=workspace,
        offline_only=True,
        scientific=SimpleNamespace(),
        _path=lambda *parts: workspace.joinpath(*parts),
    )
    answer = {
        "binding": {"result_ref": result_ref},
        "completion": "completed",
        "tables": [
            {
                "source_ref": "motor_program_group_denominators",
                "rows": [
                    ["Day 9", "e09.h5ad", 100, "e9"],
                    ["Day 10", "e10.h5ad", 200, "e10"],
                ],
            },
            {
                "source_ref": "motor_program_group_proportions",
                "rows": [
                    ["Branchio-visceromotor-like", 25.0, 15.0, -10.0],
                    ["Somatic-motor-like", 10.0, 30.0, 20.0],
                ],
            },
            {
                "source_ref": "motor_program_marker_positive_observations",
                "rows": [
                    ["Day 9", "Branchio-visceromotor-like", 25],
                    ["Day 10", "Branchio-visceromotor-like", 30],
                    ["Day 9", "Somatic-motor-like", 10],
                    ["Day 10", "Somatic-motor-like", 60],
                ],
            },
        ],
    }
    record = {
        "id": investigation_id,
        "status": "completed",
        "request": {"motor_program_comparison": True},
        "description_operation": {
            "result_ref": result_ref,
            "worker_cleanup_verified": True,
        },
        "source_profiles": profiles.copy(),
        "turns": [{"kind": "dataset_answer", "content": answer}],
    }
    app.investigations = SimpleNamespace(get=lambda identity: record)
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A"
        "AwMCAO+a6foAAAAASUVORK5CYII="
    )
    (bundle / "comparison.png").write_bytes(png)
    sha256 = hashlib.sha256(png).hexdigest()
    manifest = {
        "format": "devbench.retained-comparison/1",
        "session_id": session_id,
        "plan_id": plan_id,
        "plan_sha256": plan_sha256,
        "investigation_id": investigation_id,
        "result_ref": result_ref,
        "title": "Motor-neuron comparison",
        "states": [
            {"label": "Postmitotic motor-like", "counts": [15, 30]},
            {"label": "Cholinergic motor-like", "counts": [5, 70]},
        ],
        "figures": [
            {"title": "Shared UMAP", "filename": "comparison.png", "sha256": sha256}
        ],
        "limitations": ["The sampled stages have different batch compositions."],
    }
    path = bundle / "comparison.json"
    path.write_text(json.dumps(manifest))
    return app, manifest, path, png, record


def test_retained_comparison_survives_source_bundle_removal(comparison):
    app, manifest, path, png, _ = comparison
    sid, sha = manifest["session_id"], manifest["figures"][0]["sha256"]
    assert local_comparison_view(app, sid) is None
    retain_local_comparison(app, path)
    retain_local_comparison(app, path)
    path.unlink()
    (path.parent / "comparison.png").unlink()
    view = local_comparison_view(app, sid)
    assert view["title"] == manifest["title"]
    assert view["stages"] == [
        {"label": "E09", "dataset": "e09.h5ad", "cells": 100},
        {"label": "E10", "dataset": "e10.h5ad", "cells": 200},
    ]
    programs, states = view["tables"]
    assert programs["rows"][0] == [
        "Branchio-visceromotor-like",
        "25 (25.0%)",
        "30 (15.0%)",
    ]
    assert states["rows"][1] == ["Cholinergic motor-like", "5 (25.0%)", "70 (70.0%)"]
    assert (
        view["figures"][0]["download_path"]
        == f"/api/scientific/adaptive/{sid}/comparison/{sha}.png"
    )
    assert str(path.parent) not in json.dumps(view)
    assert local_comparison_figure(app, sid, sha) == png
    assert local_comparison_view(app, "0" * 32) is None


def test_retained_comparison_rejects_wrong_source_and_offline_only(comparison):
    app, manifest, path, _, record = comparison
    original = record["source_profiles"]
    record["source_profiles"] = list(reversed(original))
    with pytest.raises(PermissionError):
        retain_local_comparison(app, path)
    record["source_profiles"] = original
    retain_local_comparison(app, path)
    app.offline_only = False
    assert local_comparison_view(app, manifest["session_id"]) is None
    with pytest.raises(PermissionError):
        local_comparison_figure(
            app, manifest["session_id"], manifest["figures"][0]["sha256"]
        )


def test_figures_reject_digest_tampering_and_nonmember_readback(comparison):
    app, manifest, path, png, _ = comparison
    sid, sha = manifest["session_id"], manifest["figures"][0]["sha256"]
    (path.parent / "comparison.png").write_bytes(png + b"tampered")
    with pytest.raises(ValueError, match="digest"):
        retain_local_comparison(app, path)
    (path.parent / "comparison.png").write_bytes(png)
    retain_local_comparison(app, path)
    with pytest.raises(KeyError):
        local_comparison_figure(app, sid, "9" * 64)
    (stored,) = app.workspace.rglob(f"{sha}.png")
    stored.write_bytes(png + b"tampered")
    with pytest.raises(ValueError, match="digest"):
        local_comparison_figure(app, sid, sha)


def test_retained_comparison_rejects_manifest_tampering_and_withheld_source(
    comparison, monkeypatch
):
    app, manifest, path, _, _ = comparison
    sid = manifest["session_id"]
    retain_local_comparison(app, path)
    with monkeypatch.context() as patch:
        patch.setattr(
            "devbench.platform.workbench_ui_projection._publication_hashes",
            lambda app: {"f" * 64},
        )
        with pytest.raises(PermissionError):
            local_comparison_view(app, sid)
    (stored,) = app.workspace.rglob("manifest.json")
    raw = stored.read_text()
    stored.write_text(raw.replace("15,", "16,", 1))
    with pytest.raises(ValueError, match="manifest digest"):
        local_comparison_view(app, sid)


def _add_plotly(manifest, path, figure):
    name = "comparison.figure.json"
    raw = json.dumps(figure).encode()
    sha = hashlib.sha256(raw).hexdigest()
    (path.parent / name).write_bytes(raw)
    manifest["figures"][0]["plotly"] = {"filename": name, "sha256": sha}
    path.write_text(json.dumps(manifest))
    return path.parent / name, sha


def test_plotly_companion_is_scoped_and_persists_with_its_png(comparison):
    app, manifest, path, png, _ = comparison
    sid = manifest["session_id"]
    png_sha = manifest["figures"][0]["sha256"]
    figure = {
        "data": [{"type": "scatter", "mode": "markers", "x": [1, 2], "y": [3, 4]}]
    }
    source, plotly_sha = _add_plotly(manifest, path, figure)

    keep = retain_local_comparison(app, path)
    route = f"/api/scientific/adaptive/{sid}/comparison"
    assert keep["figures"] == [
        {
            "title": "Shared UMAP",
            "download_path": f"{route}/{png_sha}.png",
            "plotly_path": f"{route}/{plotly_sha}/plotly",
        }
    ]
    source.unlink()
    (path.parent / "comparison.png").unlink()
    assert retain_local_comparison(app, path) == keep
    path.unlink()
    assert local_comparison_view(app, sid) == keep
    assert local_comparison_figure(app, sid, png_sha) == png
    assert local_comparison_plotly(app, sid, plotly_sha)["data"][0]["x"] == [1, 2]
    with pytest.raises(KeyError):
        local_comparison_plotly(app, sid, png_sha)
    with pytest.raises(KeyError):
        local_comparison_plotly(app, "0" * 32, plotly_sha)

    app.offline_only = False
    with pytest.raises(PermissionError):
        local_comparison_plotly(app, sid, plotly_sha)


def test_plotly_companion_fails_closed_on_invalid_data_or_digest(
    comparison,
    monkeypatch,
):
    app, manifest, path, png, _ = comparison
    sid = manifest["session_id"]
    png_sha = manifest["figures"][0]["sha256"]
    _, _ = _add_plotly(
        manifest,
        path,
        {
            "data": [{"type": "scatter", "mode": "markers", "x": [1], "y": [2]}],
            "layout": {"images": [{"source": "https://example.invalid/image.png"}]},
        },
    )
    with pytest.raises(ValueError):
        retain_local_comparison(app, path)
    assert local_comparison_view(app, sid) is None

    source, plotly_sha = _add_plotly(
        manifest,
        path,
        {
            "data": [{"type": "bar", "x": [1], "y": [2]}],
        },
    )
    raw = source.read_bytes()
    source.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="digest"):
        retain_local_comparison(app, path)
    assert local_comparison_view(app, sid) is None

    source.write_bytes(raw)
    retain_local_comparison(app, path)
    with monkeypatch.context() as patch:
        patch.setattr(
            "devbench.platform.workbench_retained_comparison._publication_hashes",
            lambda app: {plotly_sha},
        )
        with pytest.raises(PermissionError):
            local_comparison_plotly(app, sid, plotly_sha)

    stored = app.workspace / "retained-comparisons" / sid / (plotly_sha + ".json")
    stored.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="digest"):
        local_comparison_plotly(app, sid, plotly_sha)
    assert local_comparison_figure(app, sid, png_sha) == png


def test_plotly_companion_requires_local_json_basename(comparison):
    app, manifest, path, _, _ = comparison
    manifest["figures"][0]["plotly"] = {
        "filename": "../figure.json",
        "sha256": "9" * 64,
    }
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Plotly companion"):
        retain_local_comparison(app, path)
    assert local_comparison_view(app, manifest["session_id"]) is None
