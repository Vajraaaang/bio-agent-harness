"""Notebook postflight checks must never repair missing or changed source."""
import ast
import base64
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from scripts.build_colab_notebook import build_source_bundle, extract_source_bundle


def _fixture(tmp_path):
    source = tmp_path / "original"
    (source / "scripts").mkdir(parents=True)
    (source / "pyproject.toml").write_text("[project]\nname='synthetic-source'\nversion='1'\n")
    (source / "README.md").write_text("Synthetic source verification fixture.\n")
    (source / "scripts/colab_run.py").write_text("print('x')\n")
    bundle = build_source_bundle(source)
    output = tmp_path / "extracted"
    extract_source_bundle(bundle["base64"], output, bundle["sha256"])
    return bundle, output


def test_checkout_and_wheel_stage_share_the_git_source_inventory(tmp_path):
    from scripts.verify_wheel_custody import _copy_sources

    project = tmp_path / "checkout"
    (project / "scripts").mkdir(parents=True)
    (project / "benchmarks").mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='synthetic-source'\nversion='1'\n")
    (project / "README.md").write_text("Synthetic source verification fixture.\n")
    (project / "scripts/colab_run.py").write_text("print('x')\n")
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    (project / ".git/info/exclude").write_text("/benchmarks/planted_discovery.py\n")
    (project / "benchmarks/planted_discovery.py").write_text("# ignored local fixture\n")
    (project / "benchmarks/visible.py").write_text("# eligible source\n")
    (project / "benchmarks/credential_sample.py").write_text("# denied synthetic fixture\n")

    staging = tmp_path / "stage"
    _copy_sources(project, staging)
    checkout = build_source_bundle(project)
    assert "benchmarks/planted_discovery.py" not in checkout["files"]
    assert "benchmarks/credential_sample.py" not in checkout["files"]
    assert "benchmarks/visible.py" in checkout["files"]
    assert checkout == build_source_bundle(staging)


def test_source_resource_consistency_check_preserves_existing_bytes(tmp_path):
    from scripts.build_source_resource import build_source_resource

    _fixture(tmp_path)
    project = tmp_path / "original"
    built = build_source_resource(project)
    assert build_source_resource(project, check=True) == built
    asset = Path(built["path"])
    asset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match"):
        build_source_resource(project, check=True)
    assert asset.read_bytes() == b"changed"


def _state(path):
    if not path.exists(): return None
    result = {}
    for member in path.rglob("*"):
        mode = member.lstat().st_mode
        result[member.relative_to(path).as_posix()] = (stat.S_IFMT(mode), member.read_bytes() if stat.S_ISREG(mode) else None)
    return result


def _wheel_resource_fixture(tmp_path):
    from importlib.metadata import PackagePath
    from types import SimpleNamespace

    from scripts.build_source_resource import build_source_resource
    project = tmp_path / "epoch"
    (project / "scripts").mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='devharness'\nversion='1'\n")
    (project / "README.md").write_text("Frozen source epoch\n")
    (project / "uv.lock").write_text("version = 1\n")
    (project / "scripts/colab_run.py").write_text("print('epoch')\n")
    generated = build_source_resource(project)
    installed = tmp_path / "site-packages"
    asset = installed / "devbench/analysis/assets/source-bundle.zip"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(Path(generated["path"]).read_bytes())
    code = installed / "scripts/colab_run.py"
    code.parent.mkdir()
    code.write_bytes((project / "scripts/colab_run.py").read_bytes())
    record_asset = PackagePath("devbench/analysis/assets/source-bundle.zip")
    record_asset.hash = SimpleNamespace(mode="sha256", value=base64.urlsafe_b64encode(hashlib.sha256(asset.read_bytes()).digest()).decode().rstrip("="))
    record_asset.size = asset.stat().st_size
    record_code = PackagePath("scripts/colab_run.py")
    distribution = SimpleNamespace(files=[record_asset, record_code], locate_file=lambda member: installed / member)
    return project, asset, code, distribution


def test_wheel_resource_matches_frozen_source_and_excludes_itself(tmp_path):
    from scripts.build_colab_notebook import _installed_source_bundle
    project, asset, _, distribution = _wheel_resource_fixture(tmp_path)
    actual = _installed_source_bundle(asset, distribution)
    assert actual == build_source_bundle(project)
    assert "devbench/analysis/assets/source-bundle.zip" not in actual["files"]


@pytest.mark.parametrize("mutation", ["missing", "tampered", "code_changed", "missing_record", "missing_code"])
def test_wheel_resource_rejects_missing_tampered_or_mixed_epoch(tmp_path, mutation):
    from scripts.build_colab_notebook import _installed_source_bundle
    project, asset, code, distribution = _wheel_resource_fixture(tmp_path)
    if mutation == "missing":
        asset.unlink()
    elif mutation == "tampered":
        asset.write_bytes(asset.read_bytes() + b"tampered")
    elif mutation == "code_changed":
        code.write_text("print('other epoch')\n")
    elif mutation == "missing_record":
        distribution.files = []
    else:
        code.unlink()
    with pytest.raises((ValueError, OSError)):
        _installed_source_bundle(asset, distribution)


def test_default_installed_identity_uses_verified_resource_and_explicit_root_stays_explicit(tmp_path, monkeypatch):
    from importlib import metadata, resources

    from devbench.analysis.workbench_proposals import _source_identity
    from scripts import build_colab_notebook as builder
    project, asset, _, distribution = _wheel_resource_fixture(tmp_path)
    monkeypatch.setattr(builder, "__file__", str(asset.parents[3] / "scripts/build_colab_notebook.py"))
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(resources, "files", lambda name: asset.parent.parent)
    expected = build_source_bundle(project)
    assert builder.default_source_bundle() == expected
    assert _source_identity() == expected["sha256"]
    assert builder.default_source_bundle(project) == expected
    with pytest.raises(ValueError, match="missing required"):
        builder.default_source_bundle(tmp_path / "absent-checkout")


def test_runtime_asset_must_match_checkout_bytes(tmp_path, monkeypatch):
    from devbench import _layout

    path = "docs/agentic-rna-v24-evaluation-protocol.md"
    checkout = tmp_path / "checkout"
    assets = tmp_path / "devbench" / "analysis" / "assets"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "docs").mkdir()
    assets.mkdir(parents=True)
    reference = checkout / path
    packaged = assets / Path(path).name
    reference.write_bytes(b"frozen protocol\n")
    packaged.write_bytes(reference.read_bytes())
    monkeypatch.setattr(_layout, "PROJECT_ROOT", checkout)
    monkeypatch.setattr(_layout, "ANALYSIS_ASSETS", assets)

    assert _layout.resolve_analysis_asset(path).read_bytes() == reference.read_bytes()
    reference.write_bytes(b"frozen protocol\r\n")
    with pytest.raises(ValueError, match="does not match"):
        _layout.resolve_analysis_asset(path)
    reference.unlink()
    with pytest.raises(FileNotFoundError):
        _layout.resolve_analysis_asset(path)

    monkeypatch.setattr(_layout, "PROJECT_ROOT", assets.parent.parent.parent)
    assert _layout.resolve_analysis_asset(path) == packaged


def test_ignored_report_mirror_works_in_clean_checkout(tmp_path, monkeypatch):
    from devbench import _layout

    (tmp_path / ".git").mkdir()
    assets = tmp_path / "src" / "devbench" / "analysis" / "assets"
    assets.mkdir(parents=True)
    mirror = assets / "owned-host-sentinel.txt"
    mirror.write_bytes(b"synthetic sentinel\n")
    reference = tmp_path / "reports" / mirror.name
    monkeypatch.setattr(_layout, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_layout, "ANALYSIS_ASSETS", assets)
    assert _layout.resolve_analysis_asset("reports/" + mirror.name) == mirror
    reference.parent.mkdir()
    reference.write_bytes(b"drift\n")
    with pytest.raises(ValueError, match="does not match"):
        _layout.resolve_analysis_asset("reports/" + mirror.name)


def test_default_preparation_retains_verified_wheel_source_and_environment_lock(tmp_path, monkeypatch):
    from importlib import metadata, resources

    from devbench.analysis import runtime, workbench
    from scripts import build_colab_notebook as builder
    project, asset, _, distribution = _wheel_resource_fixture(tmp_path)
    monkeypatch.setattr(builder, "__file__", str(asset.parents[3] / "scripts/build_colab_notebook.py"))
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(resources, "files", lambda name: asset.parent.parent)
    monkeypatch.setattr(workbench, "_validated_parameters", lambda parameters: parameters)
    monkeypatch.setattr(workbench, "make_notebook", lambda path: path.write_text("{}"))
    monkeypatch.setattr(runtime, "observe_runtime", lambda tier: {"runtime": {"packages": {}}})
    monkeypatch.setattr(runtime, "verify_data_identity", lambda source, digest: {"data_sha256": digest})
    source = tmp_path / "input.h5ad"
    source.write_bytes(b"synthetic identity-only fixture; no scientific fit")
    output = tmp_path / "prepared"
    parameters = {"execution_tier": "cpu", "data_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    prepared = workbench.prepare_bundle(source, output, parameters)
    assert prepared["source_sha256"] == build_source_bundle(project)["sha256"]
    assert (output / "source.zip").read_bytes() == asset.read_bytes()
    assert (output / "environment.lock").read_bytes() == (project / "uv.lock").read_bytes()
    assert (output / "source/scripts/colab_run.py").read_bytes() == (project / "scripts/colab_run.py").read_bytes()
    assert workbench.prepare_bundle(source, output, parameters) == prepared


def test_existing_source_verification_is_read_only_and_accepts_exact_members(tmp_path):
    bundle, output = _fixture(tmp_path)
    before = _state(output)
    assert extract_source_bundle(bundle["base64"], output, bundle["sha256"], verify_only=True) == output
    assert _state(output) == before


@pytest.mark.parametrize("limit", [0, 4])
def test_source_file_read_refuses_payload_larger_than_its_bound(tmp_path, limit):
    from scripts import build_colab_notebook as builder
    source = tmp_path / "source"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="bound"):
        builder._read_source_file(source, limit)
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("kind", ["agent", "controlled"])
def test_generated_bootstrap_source_verifier_runs_without_repository_imports(tmp_path, kind):
    from scripts import build_colab_notebook, build_controlled_notebook
    bundle, output = _fixture(tmp_path)
    module = build_colab_notebook if kind == "agent" else build_controlled_notebook
    notebook = module.build_notebook(tmp_path / "original")
    code = next("".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code")
    definitions = [
        node for node in ast.parse(code).body
        if isinstance(node, ast.FunctionDef)
        or (
            isinstance(node, ast.Assign)
            and ast.unparse(node.targets[0]) == "SOURCE_BUNDLE_MAX_FILES"
        )
    ]
    namespace = {"os":os, "stat":stat, "Path":Path, "PurePosixPath":PurePosixPath,
                 "base64":base64, "hashlib":hashlib, "io":io, "zipfile":zipfile}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "embedded-functions", "exec"), namespace)
    limit = build_colab_notebook.SOURCE_BUNDLE_MAX_FILES
    assert namespace["SOURCE_BUNDLE_MAX_FILES"] == limit
    verify = namespace["extract_source_bundle"]
    assert verify(bundle["base64"], output, bundle["sha256"], verify_only=True) == output
    (output / "scripts/colab_run.py").unlink()
    with pytest.raises(ValueError, match="changed"):
        verify(bundle["base64"], output, bundle["sha256"], verify_only=True)
    assert not (output / "scripts/colab_run.py").exists()


def test_bundled_test_dependencies_import_with_site_packages_and_checkout_disabled(tmp_path):
    from scripts import build_colab_notebook, build_controlled_notebook
    _fixture(tmp_path)
    project = tmp_path / "original"
    (project / "scripts/__init__.py").write_text("")
    for module in (build_colab_notebook, build_controlled_notebook):
        shutil.copyfile(module.__file__, project / "scripts" / Path(module.__file__).name)
    bundle = build_source_bundle(project)
    output = extract_source_bundle(bundle["base64"], tmp_path / "standalone", bundle["sha256"])
    code = """import sys\nfrom pathlib import Path\nroot=Path(sys.argv[1]).resolve()\nsys.path.insert(0,str(root))\nfrom scripts import build_colab_notebook, build_controlled_notebook\nassert all(Path(m.__file__).resolve().is_relative_to(root) for m in (build_colab_notebook,build_controlled_notebook))\n"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(output)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mutation", ["modify", "remove_root", "remove_member", "fifo_member"])
def test_postflight_verification_rejects_changes_without_reextracting_source(tmp_path, mutation):
    bundle, output = _fixture(tmp_path)
    member = output / "scripts/colab_run.py"
    if mutation == "modify": member.write_text("print('y')\n")
    elif mutation == "remove_root": shutil.rmtree(output)
    elif mutation == "remove_member": member.unlink()
    else:
        if not hasattr(os, "mkfifo"): pytest.skip("Requires POSIX FIFOs")
        member.unlink()
        os.mkfifo(member, 0o600)
    before = _state(output)
    try:
        with pytest.raises((ValueError, FileNotFoundError, FileExistsError)):
            extract_source_bundle(bundle["base64"], output, bundle["sha256"], verify_only=True)
        assert _state(output) == before
    finally:
        if mutation == "fifo_member": member.unlink(missing_ok=True)
