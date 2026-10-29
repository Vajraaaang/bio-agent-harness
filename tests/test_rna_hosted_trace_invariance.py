"""Retained bounded software workflows, never native or biological qualification."""
from pathlib import Path
import pytest

from devbench.study.rna_hosted_fixtures import fixture_manifest, run_fixture, scientific_accounting


@pytest.mark.parametrize("stage", ["pre_fit_methods", "post_fit_explanation"])
@pytest.mark.parametrize("allocation", [0, 1, 2])
def test_installed_fixture_real_off_on_invariance(tmp_path, stage, allocation):
    off = run_fixture(root=tmp_path / "off", stage=stage, allocation=allocation, trace=False)
    on = run_fixture(root=tmp_path / "on", stage=stage, allocation=allocation, trace=True)
    assert scientific_accounting(off) == scientific_accounting(on)
    assert len(on["backend"].child_contexts) == allocation
    events = on["observer"].read_events(on["identity"])
    assert events and any(e["phase"] == "recommend" for e in events)
    if stage == "post_fit_explanation":
        assert on["executor"].executions == 1
        assert on["executor"].calls == ["fit_temporal_trajectory"]
        assert scientific_accounting(on)["gene_statistics"]
    else:
        assert scientific_accounting(on)["fit_calls"] == 0


def test_fixture_manifest_has_only_reviewed_embedded_controls():
    manifest = fixture_manifest()
    assert len(manifest["fixtures"]) == 6
    assert manifest["data_scope"] == "synthetic"
    assert len(manifest["builder_sha256"]) == 64
    assert all(len(row["asset_sha256"]) == 64 for row in manifest["fixtures"])


def test_fixture_rejects_relative_or_arbitrary_inputs(tmp_path):
    with pytest.raises(ValueError):
        run_fixture(root=Path("relative"), stage="pre_fit_methods", allocation=0)
    with pytest.raises(ValueError):
        run_fixture(root=tmp_path / "bad", stage="clinical", allocation=1)


# Runner controls are software admission tests, independent of fixture science.
@pytest.fixture
def verification_selection(tmp_path):
    import json
    from scripts import run_rna_hosted_tracing_verification as runner
    epoch = tmp_path / "epoch"
    epoch.mkdir()
    names = runner.FOCUSED_BASE | runner.NEW_TESTS
    files = {}
    for name in names:
        path = epoch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# bounded test placeholder\n")
        files[name] = runner.sha(path)
    manifest = {"format": runner.SOURCE_FORMAT, "files": files}
    (epoch / "source-manifest.json").write_text(json.dumps(manifest))
    selection = {"format": runner.SELECTION_FORMAT, "scope": "software", "kind": "focused",
        "source_manifest_sha256": runner.sha(epoch / "source-manifest.json"),
        "prior_selection_sha256": runner.PRIOR_SELECTION_SHA256,
        "allowed_fixture_classes": ["synthetic", "local"],
        "tests": [{"path": name, "sha256": files[name]} for name in sorted(names)]}
    return runner, epoch, manifest, selection


def test_verification_runner_selection_contains_exact_seventeen_modules(verification_selection):
    runner, epoch, manifest, selection = verification_selection
    additions = {
        "tests/test_rna_hosted_preflight.py",
        "tests/test_rna_publication_evidence.py",
        "tests/test_rna_publication_comparison.py",
        "tests/test_rna_scientific_trial_contract.py",
        "tests/test_rna_scientific_trial.py",
    }
    assert additions <= runner.NEW_TESTS
    assert len(runner.NEW_TESTS) == 17
    assert len(runner.HISTORICAL_TESTS) == 112
    assert len(runner.validate_selection(epoch, manifest, selection)) == 24


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unapproved", "hash", "source", "scope"])
def test_verification_runner_rejects_selection_drift(verification_selection, mutation):
    runner, epoch, manifest, selection = verification_selection
    if mutation == "missing":
        selection["tests"] = [row for row in selection["tests"]
            if row["path"] != "tests/test_rna_hosted_qualification.py"]
    elif mutation == "duplicate":
        selection["tests"].append(dict(selection["tests"][0]))
    elif mutation == "unapproved":
        selection["tests"].append({"path": "tests/test_hidden_campaign.py", "sha256": "0" * 64})
    elif mutation == "hash":
        (epoch / selection["tests"][0]["path"]).write_text("# changed\n")
    elif mutation == "source":
        selection["source_manifest_sha256"] = "0" * 64
    else:
        selection["scope"] = "hosted"
    with pytest.raises(ValueError):
        runner.validate_selection(epoch, manifest, selection)


def test_verification_runner_broad_is_exact_retained_names(verification_selection):
    import json
    runner, epoch, manifest, selection = verification_selection
    selection["kind"] = "broad"
    with pytest.raises(ValueError):
        runner.validate_selection(epoch, manifest, selection)
    names = runner.HISTORICAL_TESTS | runner.NEW_TESTS
    for name in names:
        path = epoch / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# bounded test placeholder\n")
        manifest["files"][name] = runner.sha(path)
    selection["tests"] = [{"path": name, "sha256": manifest["files"][name]} for name in sorted(names)]
    (epoch / "source-manifest.json").write_text(json.dumps(manifest))
    selection["source_manifest_sha256"] = runner.sha(epoch / "source-manifest.json")
    assert len(runner.validate_selection(epoch, manifest, selection)) == 129


def test_verification_runner_source_drift_is_rejected(verification_selection):
    runner, epoch, manifest, selection = verification_selection
    runner.source_state(epoch, manifest)
    (epoch / selection["tests"][0]["path"]).write_text("# source changed\n")
    with pytest.raises(ValueError, match="Source hash mismatch"):
        runner.source_state(epoch, manifest)


def test_verification_runner_binds_untracked_checkout_and_generated_resource(tmp_path, monkeypatch):
    from scripts import run_rna_hosted_tracing_verification as runner
    monkeypatch.setattr(runner, "WORKSPACE", tmp_path)
    monkeypatch.setattr(runner.historical, "WORKSPACE", tmp_path)
    source = tmp_path / "devbench/study/new_untracked_module.py"
    source.parent.mkdir(parents=True)
    source.write_text("# untracked source\n")
    manifest = {"files": {"devbench/study/new_untracked_module.py": runner.sha(source),
                          runner.GENERATED_RESOURCE: "0" * 64}}
    assert set(runner.checkout_state(manifest)) == {"devbench/study/new_untracked_module.py"}
    source.write_text("# changed source\n")
    with pytest.raises(ValueError, match="Checkout changed"):
        runner.checkout_state(manifest)


def test_verification_runner_rejects_borrowed_product_helper(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import run_rna_hosted_tracing_verification as runner
    origin_map = {str(Path(module.__file__).resolve()): runner.sha(Path(module.__file__).resolve())
        for name, module in sys.modules.copy().items()
        if name.split(".")[0] in runner.PACKAGE_ROOTS and getattr(module, "__file__", None)}
    monkeypatch.setitem(sys.modules, "scripts.borrowed_verification_helper",
                        SimpleNamespace(__file__=str(tmp_path / "borrowed.py")))
    with pytest.raises(ValueError, match="Product module borrowed"):
        runner.module_origins(origin_map, tmp_path / "epoch", {"files": {}})


def test_verification_runner_dependency_record_drift_is_rejected(tmp_path, monkeypatch):
    import base64
    from scripts import run_rna_hosted_tracing_verification as runner
    prefix = tmp_path / "runtime"
    site = prefix / "lib/python/site-packages"
    record = site / "bounded_dependency.dist-info/RECORD"
    record.parent.mkdir(parents=True)
    asset = site / "bounded_dependency.py"
    asset.write_text("# installed dependency\n")
    encoded = base64.urlsafe_b64encode(bytes.fromhex(runner.sha(asset))).decode().rstrip("=")
    text = f"bounded_dependency.py,sha256={encoded},{asset.stat().st_size}\n"
    record.write_text(text)
    class Distribution:
        metadata = {"Name": "bounded_dependency"}
        version = "1"
        files = ["bounded_dependency.dist-info/RECORD"]
        def read_text(self, name):
            return text if name == "RECORD" else None
        def locate_file(self, name):
            return site / name
    monkeypatch.setattr(runner.sys, "prefix", str(prefix))
    monkeypatch.setattr(runner.importlib.metadata, "distributions", lambda: [Distribution()])
    before = {"files": {str(asset.relative_to(prefix)): runner.sha(asset)}}
    assert runner.dependency_records(before)[0]["verified_files"] == 1
    before["files"][str(asset.relative_to(prefix))] = "0" * 64
    with pytest.raises(ValueError, match="RECORD content mismatch"):
        runner.dependency_records(before)


def test_verification_runner_rejects_pth_hooks(tmp_path, monkeypatch):
    from scripts import run_rna_hosted_tracing_verification as runner
    monkeypatch.setattr(runner.sys, "prefix", str(tmp_path))
    (tmp_path / "borrow.pth").write_text("import mutable_source\n")
    with pytest.raises(ValueError, match="hooks prohibited"):
        runner.reject_import_hooks()


@pytest.mark.parametrize("copies", [0, 1, 2])
def test_verification_runner_requires_exact_installed_resource(tmp_path, copies):
    from scripts import run_rna_hosted_tracing_verification as runner
    digest = "a" * 64
    origin_map = {str(tmp_path / str(i) / runner.GENERATED_RESOURCE): digest for i in range(copies)}
    source = {runner.GENERATED_RESOURCE: digest}
    if copies == 1:
        assert len(runner.resource_binding(origin_map, source)[runner.GENERATED_RESOURCE]["installed_files"]) == 1
    else:
        with pytest.raises(ValueError, match="Exactly one"):
            runner.resource_binding(origin_map, source)
    mismatched = {str(tmp_path / runner.GENERATED_RESOURCE): "b" * 64}
    with pytest.raises(ValueError, match="Exactly one"):
        runner.resource_binding(mismatched, source)
@pytest.mark.parametrize('workspace,report,epoch', [
    ('relative', '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/unused', 'unused'),
    ('/private/tmp', '/private/tmp/unused', 'unused'),
    ('/Users/vajraang/Documents/ChatGPT/bio-agent-harness', '/private/tmp/unused', 'unused'),
    ('/Users/vajraang/Documents/ChatGPT/bio-agent-harness', '/Users/vajraang/Documents/ChatGPT/bio-agent-harness/reports/unused', '../escape'),
])
def test_hosted_freezer_rejects_scope_before_reading_history(workspace, report, epoch):
    from pathlib import Path
    from scripts.freeze_rna_hosted_tracing_epoch import freeze
    with pytest.raises(ValueError):
        freeze(Path(workspace), Path(report), epoch)
