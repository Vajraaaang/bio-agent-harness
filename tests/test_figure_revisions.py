"""Presentation-only bundle contracts; saved-table fixtures are not analyses."""
import importlib.util
import json
from pathlib import Path

import pytest


def revision_api():
    assert importlib.util.find_spec("devbench.analysis.figure_revisions"), "Presentation revision API is missing"
    from devbench.analysis import figure_revisions
    return figure_revisions


def test_revision_module_exists():
    revision_api()


def test_unknown_bundle_kind_cannot_be_sealed(tmp_path):
    from devbench.analysis.bundles import seal_bundle
    (tmp_path / "parameters.json").write_text(json.dumps({"artifact_kind": "arbitrary", "execution_tier": "local_supervised", "data_sha256": "a" * 64}))
    (tmp_path / "source.zip").write_bytes(b"source fixture")
    with pytest.raises(ValueError, match="kind"):
        seal_bundle(tmp_path, status="completed")


def test_presentation_is_rejected_before_numerical_configuration(tmp_path):
    from devbench.analysis.workbench import _frozen_configuration
    (tmp_path / "parameters.json").write_text('{"artifact_kind":"presentation"}')
    with pytest.raises(ValueError, match="presentation"):
        _frozen_configuration(tmp_path)


@pytest.fixture
def figure_parent(tmp_path):
    from devbench.analysis.checkpoints import CheckpointStore, atomic_json, sha256
    from devbench.analysis.reporting import render_concordance_figures
    from devbench.analysis.bundles import seal_bundle
    root = tmp_path / "parent"
    root.mkdir()
    (root / "source.zip").write_bytes(b"saved-table test source; no numerical execution")
    parameters = {"execution_tier": "local_supervised", "data_sha256": "a" * 64,
                  "source_sha256": sha256(root / "source.zip"), "counts_layer": None, "min_genes": 3}
    identity = {"parameters": parameters, "data_sha256": parameters["data_sha256"], "source_sha256": parameters["source_sha256"]}
    atomic_json(root / "parameters.json", parameters)
    atomic_json(root / "checkpoint-identity.json", identity)
    store = CheckpointStore(root / "checkpoints", identity)

    def qc(path):
        (path / "metrics.csv").write_text("cell_id,log1p_total_counts,log1p_n_genes_by_counts,pct_counts_mt,qc_pass,batch\n001,2.0,1.5,0.0,True,A\nNA,3.0,2.0,5.0,True,B\n")

    def embedding(path):
        (path / "embedding.csv").write_text("cell_id,UMAP_1,UMAP_2,batch,leiden\n001,0.0,1.0,A,0\nNA,2.0,0.0,B,1\n")

    store.run("qc", qc)
    store.run("uncorrected_graph", embedding, dependencies=("qc",))
    render_concordance_figures(store, {"uncorrected": "pca"}, root / "figures", batch_key="batch")
    receipt = seal_bundle(root, status="completed_review_failed")
    return root, receipt["manifest_sha256"]


def test_style_export_import_replay_preserves_science_without_matrix(figure_parent, tmp_path, monkeypatch):
    api = revision_api()
    from devbench.analysis.bundles import verify_bundle, pack_bundle, import_bundle
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis import workbench, workbench_proposals
    parent, parent_root = figure_parent
    before = {str(p.relative_to(parent)): sha256(p) for p in parent.rglob("*") if p.is_file()}
    def forbidden(*args, **kwargs):
        pytest.fail("Presentation operation launched a numerical/model stage")
    for name in ("run_stage", "execute_bundle", "prepare_bundle"):
        monkeypatch.setattr(workbench, name, forbidden)
    monkeypatch.setattr(workbench_proposals, "execute_reviewed_workbench", forbidden)
    output = tmp_path / "revision"
    prepared = api.prepare_figure_revision(parent, parent_root, "qc", {"point_size": 12}, output)
    result = api.execute_figure_revision(output)
    assert result["status"] == "completed_presentation"
    assert result["numerical_stage_launches"] == result["model_sessions"] == 0
    assert prepared["parent_bundle_root"] == parent_root
    manifest = json.loads((output / "receipts.json").read_text())
    assert manifest["artifact_kind"] == "presentation" and manifest["external_input"] is None
    assert json.loads((output / "revision.json").read_text())["parent_status"] == "completed_review_failed"
    assert before == {str(p.relative_to(parent)): sha256(p) for p in parent.rglob("*") if p.is_file()}
    archive = tmp_path / "presentation.zip"
    pack_bundle(output, archive, result["manifest_sha256"])
    imported = tmp_path / "imported"
    import_bundle(archive, imported, result["manifest_sha256"])
    parent.rename(tmp_path / "parent-unavailable")
    repeated = api.replay_figure_revision(imported, result["manifest_sha256"], tmp_path / "replayed")
    assert repeated["status"] == "completed_presentation"
    verify_bundle(tmp_path / "replayed", repeated["manifest_sha256"])
    assert sha256(imported / "figures/qc.png") == sha256(tmp_path / "replayed/figures/qc.png")
    with pytest.raises(ValueError, match="binding|presentation"):
        import_bundle(archive, tmp_path / "with-data", result["manifest_sha256"], data_path=tmp_path / "missing.h5ad")


def test_stale_parent_and_semantic_edits_fail_before_output(figure_parent, tmp_path):
    api = revision_api()
    parent, parent_root = figure_parent
    for expected, options in [("0" * 64, {}), (parent_root, {"layer": "normalized"})]:
        output = tmp_path / "must-not-exist"
        with pytest.raises(ValueError):
            api.prepare_figure_revision(parent, expected, "qc", options, output)
        assert not output.exists()


def test_prepared_data_tamper_and_reexecution_are_rejected(figure_parent, tmp_path):
    api = revision_api()
    parent, parent_root = figure_parent
    output = tmp_path / "revision"
    api.prepare_figure_revision(parent, parent_root, "qc", {}, output)
    (output / "figures/qc-data.json").write_text('{}')
    with pytest.raises(ValueError):
        api.execute_figure_revision(output)
    assert not (output / "receipts.json").exists()
    assert (output / "failure.json").exists()
    with pytest.raises(ValueError, match="attempt|failed|exists|sealed"):
        api.execute_figure_revision(output)


def test_unknown_worker_cleanup_cannot_publish_success(figure_parent, tmp_path, monkeypatch):
    api = revision_api()
    from devharness_core import watchdog
    from devbench.analysis import workbench, workbench_proposals
    parent, parent_root = figure_parent
    output = tmp_path / "uncertain-cleanup"
    api.prepare_figure_revision(parent, parent_root, "qc", {"point_size": 10}, output)
    def numerical_forbidden(*args, **kwargs):
        pytest.fail("Frozen-data renderer reached a scientific execution boundary")
    for name in ("run_stage", "execute_bundle", "prepare_bundle"):
        monkeypatch.setattr(workbench, name, numerical_forbidden)
    monkeypatch.setattr(workbench_proposals, "execute_reviewed_workbench", numerical_forbidden)
    def uncertain(target, args, **kwargs):
        assert target is api._render_worker
        result = target(*args)  # Same-process sentinels also cover the render worker.
        kwargs["diagnostics"]["cleanup"] = {"verified_stopped": False, "unknown_pids": [123]}
        return result
    monkeypatch.setattr(watchdog, "run_sandboxed", uncertain)
    with pytest.raises(RuntimeError, match="cleanup"):
        api.execute_figure_revision(output)
    assert not (output / "receipts.json").exists()
    assert (output / "failure.json").exists()


@pytest.mark.parametrize("failure_kind", ["renderer", "cancel", "seal", "partial_seal"])
def test_failed_attempt_has_truthful_terminal_revision_status(figure_parent, tmp_path, monkeypatch, failure_kind):
    api = revision_api()
    from devharness_core import watchdog
    from devbench.analysis import bundles
    parent, parent_root = figure_parent
    output = tmp_path / "failure"
    api.prepare_figure_revision(parent, parent_root, "qc", {}, output)
    def controlled(target, args, **kwargs):
        kwargs["diagnostics"]["cleanup"] = {"verified_stopped": True}
        if failure_kind == "cancel":
            raise watchdog.WorkerCancelled("Fixture cancellation")
        if failure_kind == "renderer":
            raise ValueError("Fixture renderer failure")
        return target(*args)
    def cannot_seal(*args, **kwargs):
        if failure_kind == "partial_seal":
            (output / "receipts.json").write_text('{"unfinished":true}')
        raise OSError("Fixture seal failure")
    monkeypatch.setattr(watchdog, "run_sandboxed", controlled)
    if failure_kind in {"seal", "partial_seal"}:
        monkeypatch.setattr(bundles, "seal_bundle", cannot_seal)
    with pytest.raises((ValueError, OSError, watchdog.WorkerCancelled)):
        api.execute_figure_revision(output)
    revision = json.loads((output / "revision.json").read_text())
    assert revision["status"] == ("cancelled" if failure_kind == "cancel" else "operation_failed")
    assert not (output / "receipts.json").exists()
    assert (output / "failure.json").exists()
    with pytest.raises(ValueError, match="attempt|failed|exists|sealed"):
        api.execute_figure_revision(output)
