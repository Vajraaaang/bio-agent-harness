"""Source custody checks must never repair missing or changed source."""

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

from scripts.source_bundle import build_source_bundle, extract_source_bundle


def _fixture(tmp_path):
    source = tmp_path / "original"
    (source / "scripts").mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        "[project]\nname='synthetic-source'\nversion='1'\n"
    )
    (source / "README.md").write_text("Synthetic source verification fixture.\n")
    (source / "scripts/source_bundle.py").write_text("print('x')\n")
    core = source / "src/devharness_core"
    core.mkdir(parents=True)
    (core / "storage.py").write_text("# synthetic module\n")
    bundle = build_source_bundle(source)
    output = tmp_path / "extracted"
    extract_source_bundle(bundle["base64"], output, bundle["sha256"])
    return bundle, output


def test_checkout_and_wheel_stage_share_the_git_source_inventory(tmp_path):
    from scripts.verify_wheel_custody import _copy_sources

    project = tmp_path / "checkout"
    (project / "scripts").mkdir(parents=True)
    (project / "benchmarks").mkdir()
    driver = project / "src/devharness_server/r/native_nb.R"
    driver.parent.mkdir(parents=True)
    driver.write_text("# synthetic driver\n")
    (project / "pyproject.toml").write_text(
        "[project]\nname='synthetic-source'\nversion='1'\n"
    )
    (project / "README.md").write_text("Synthetic source verification fixture.\n")
    (project / "scripts/source_bundle.py").write_text("print('x')\n")
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    (project / ".git/info/exclude").write_text("/benchmarks/planted_discovery.py\n")
    (project / "benchmarks/planted_discovery.py").write_text(
        "# ignored local fixture\n"
    )
    (project / "benchmarks/visible.py").write_text("# eligible source\n")
    (project / "benchmarks/credential_sample.py").write_text(
        "# denied synthetic fixture\n"
    )

    staging = tmp_path / "stage"
    _copy_sources(project, staging)
    checkout = build_source_bundle(project)
    assert "benchmarks/planted_discovery.py" not in checkout["files"]
    assert "benchmarks/credential_sample.py" not in checkout["files"]
    assert "benchmarks/visible.py" in checkout["files"]
    assert "src/devharness_server/r/native_nb.R" in checkout["files"]
    assert checkout == build_source_bundle(staging)


def test_flat_execution_snapshot_exports_rebuildable_src_layout(tmp_path):
    project = tmp_path / "snapshot"
    (project / "scripts").mkdir(parents=True)
    (project / "devharness_core").mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.setuptools.package-dir]\n"" = "src"\n'
    )
    (project / "README.md").write_text("Synthetic execution snapshot.\n")
    (project / "scripts/source_bundle.py").write_text("# source exporter\n")
    (project / "devharness_core/storage.py").write_text("# synthetic module\n")

    bundle = build_source_bundle(project)
    assert "src/devharness_core/storage.py" in bundle["files"]
    extracted = tmp_path / "extracted"
    extract_source_bundle(bundle["base64"], extracted, bundle["sha256"])
    assert build_source_bundle(extracted) == bundle


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
    if not path.exists():
        return None
    result = {}
    for member in path.rglob("*"):
        mode = member.lstat().st_mode
        result[member.relative_to(path).as_posix()] = (
            stat.S_IFMT(mode),
            member.read_bytes() if stat.S_ISREG(mode) else None,
        )
    return result


def _wheel_resource_fixture(tmp_path, *, core_prefix=""):
    from importlib.metadata import PackagePath
    from types import SimpleNamespace

    from scripts.build_source_resource import build_source_resource

    project = tmp_path / "epoch"
    (project / "scripts").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[project]\nname='devharness'\nversion='1'\n"
    )
    (project / "README.md").write_text("Frozen source epoch\n")
    (project / "uv.lock").write_text("version = 1\n")
    (project / "scripts/source_bundle.py").write_text("print('epoch')\n")
    core_member = "devharness_core/storage.py"
    core_source = project / core_prefix / core_member
    core_source.parent.mkdir(parents=True)
    core_source.write_text("# frozen runtime module\n")
    generated = build_source_resource(project)
    installed = tmp_path / "site-packages"
    asset = installed / "devbench/analysis/assets/source-bundle.zip"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(Path(generated["path"]).read_bytes())
    code = installed / "scripts/source_bundle.py"
    code.parent.mkdir()
    code.write_bytes((project / "scripts/source_bundle.py").read_bytes())
    record_asset = PackagePath("devbench/analysis/assets/source-bundle.zip")
    record_asset.hash = SimpleNamespace(
        mode="sha256",
        value=base64.urlsafe_b64encode(hashlib.sha256(asset.read_bytes()).digest())
        .decode()
        .rstrip("="),
    )
    record_asset.size = asset.stat().st_size
    record_code = PackagePath("scripts/source_bundle.py")
    installed_core = installed / core_member
    installed_core.parent.mkdir()
    installed_core.write_bytes(core_source.read_bytes())
    distribution = SimpleNamespace(
        files=[record_asset, record_code, PackagePath(core_member)],
        locate_file=lambda member: installed / member,
    )
    return project, asset, code, distribution


@pytest.mark.parametrize("core_prefix", ["", "src"])
def test_wheel_resource_matches_frozen_source_and_excludes_itself(
    tmp_path, core_prefix
):
    from scripts.source_bundle import _installed_source_bundle

    project, asset, _, distribution = _wheel_resource_fixture(
        tmp_path, core_prefix=core_prefix
    )
    actual = _installed_source_bundle(asset, distribution)
    assert actual == build_source_bundle(project)
    assert "devbench/analysis/assets/source-bundle.zip" not in actual["files"]


@pytest.mark.parametrize(
    "mutation",
    ["missing", "tampered", "code_changed", "missing_record", "missing_code"],
)
def test_wheel_resource_rejects_missing_tampered_or_mixed_epoch(tmp_path, mutation):
    from scripts.source_bundle import _installed_source_bundle

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


def test_installed_identity_uses_verified_resource_and_preserves_explicit_root(
    tmp_path, monkeypatch
):
    from importlib import metadata, resources

    from devbench.analysis.workbench_proposals import _source_identity
    from scripts import source_bundle as builder

    project, asset, _, distribution = _wheel_resource_fixture(tmp_path)
    monkeypatch.setattr(
        builder, "__file__", str(asset.parents[3] / "scripts/source_bundle.py")
    )
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


def test_default_preparation_retains_verified_wheel_source_and_environment_lock(
    tmp_path, monkeypatch
):
    from importlib import metadata, resources

    from devbench.analysis import runtime, workbench
    from scripts import source_bundle as builder

    project, asset, _, distribution = _wheel_resource_fixture(tmp_path)
    monkeypatch.setattr(
        builder, "__file__", str(asset.parents[3] / "scripts/source_bundle.py")
    )
    monkeypatch.setattr(metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(resources, "files", lambda name: asset.parent.parent)
    monkeypatch.setattr(
        workbench, "_validated_parameters", lambda parameters: parameters
    )
    monkeypatch.setattr(workbench, "make_notebook", lambda path: path.write_text("{}"))
    monkeypatch.setattr(
        runtime, "observe_runtime", lambda tier: {"runtime": {"packages": {}}}
    )
    monkeypatch.setattr(
        runtime, "verify_data_identity", lambda source, digest: {"data_sha256": digest}
    )
    source = tmp_path / "input.h5ad"
    source.write_bytes(b"synthetic identity-only fixture; no scientific fit")
    output = tmp_path / "prepared"
    parameters = {
        "execution_tier": "cpu",
        "data_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    prepared = workbench.prepare_bundle(source, output, parameters)
    assert prepared["source_sha256"] == build_source_bundle(project)["sha256"]
    assert (output / "source.zip").read_bytes() == asset.read_bytes()
    assert (output / "environment.lock").read_bytes() == (
        project / "uv.lock"
    ).read_bytes()
    assert (output / "source/scripts/source_bundle.py").read_bytes() == (
        project / "scripts/source_bundle.py"
    ).read_bytes()
    assert workbench.prepare_bundle(source, output, parameters) == prepared


def test_existing_source_verification_is_read_only_and_accepts_exact_members(tmp_path):
    bundle, output = _fixture(tmp_path)
    before = _state(output)
    assert (
        extract_source_bundle(
            bundle["base64"], output, bundle["sha256"], verify_only=True
        )
        == output
    )
    assert _state(output) == before


def test_retired_notebook_paths_remain_readable_in_historical_source_archives(tmp_path):
    archive_bytes = io.BytesIO()
    retired = (
        "scripts/colab_run.py",
        "scripts/build_colab_notebook.py",
        "scripts/build_controlled_notebook.py",
    )
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        for name in retired:
            member = zipfile.ZipInfo(name)
            member.create_system = 3
            member.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(member, b"# frozen source\n")
    raw = archive_bytes.getvalue()
    expected = hashlib.sha256(raw).hexdigest()
    encoded = base64.b64encode(raw).decode("ascii")
    output = tmp_path / "legacy-source"
    extract_source_bundle(encoded, output, expected)
    before = _state(output)
    assert all((output / name).read_bytes() == b"# frozen source\n" for name in retired)
    assert extract_source_bundle(encoded, output, expected, verify_only=True) == output
    assert _state(output) == before


@pytest.mark.parametrize("limit", [0, 4])
def test_source_file_read_refuses_payload_larger_than_its_bound(tmp_path, limit):
    from scripts import source_bundle as builder

    source = tmp_path / "source"
    source.write_bytes(b"original")
    with pytest.raises(ValueError, match="bound"):
        builder._read_source_file(source, limit)
    assert source.read_bytes() == b"original"


def test_standalone_source_verifier_runs_without_repository_imports(tmp_path):
    from scripts import source_bundle

    bundle, output = _fixture(tmp_path)
    namespace = {
        "os": os,
        "stat": stat,
        "Path": Path,
        "PurePosixPath": PurePosixPath,
        "base64": base64,
        "hashlib": hashlib,
        "io": io,
        "zipfile": zipfile,
    }
    definitions = source_bundle.source_bundle_extractor_source()
    exec(compile(definitions, "standalone-source-verifier", "exec"), namespace)
    assert (
        namespace["SOURCE_BUNDLE_MAX_FILES"]
        == source_bundle.SOURCE_BUNDLE_MAX_FILES
        == 832
    )
    verify = namespace["extract_source_bundle"]
    assert (
        verify(bundle["base64"], output, bundle["sha256"], verify_only=True) == output
    )
    assert (output / "src/devharness_core/storage.py").is_file()
    (output / "scripts/source_bundle.py").unlink()
    with pytest.raises(ValueError, match="changed"):
        verify(bundle["base64"], output, bundle["sha256"], verify_only=True)
    assert not (output / "scripts/source_bundle.py").exists()


def test_bundled_source_verifier_imports_with_site_packages_and_checkout_disabled(
    tmp_path,
):
    from scripts import source_bundle

    _fixture(tmp_path)
    project = tmp_path / "original"
    (project / "scripts/__init__.py").write_text("")
    shutil.copyfile(source_bundle.__file__, project / "scripts/source_bundle.py")
    bundle = build_source_bundle(project)
    output = extract_source_bundle(
        bundle["base64"], tmp_path / "standalone", bundle["sha256"]
    )
    code = """import sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
from scripts import source_bundle
assert Path(source_bundle.__file__).resolve().is_relative_to(root)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(output)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "mutation", ["modify", "remove_root", "remove_member", "fifo_member"]
)
def test_postflight_verification_rejects_changes_without_reextracting_source(
    tmp_path, mutation
):
    bundle, output = _fixture(tmp_path)
    member = output / "scripts/source_bundle.py"
    if mutation == "modify":
        member.write_text("print('y')\n")
    elif mutation == "remove_root":
        shutil.rmtree(output)
    elif mutation == "remove_member":
        member.unlink()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("Requires POSIX FIFOs")
        member.unlink()
        os.mkfifo(member, 0o600)
    before = _state(output)
    try:
        with pytest.raises((ValueError, FileNotFoundError, FileExistsError)):
            extract_source_bundle(
                bundle["base64"], output, bundle["sha256"], verify_only=True
            )
        assert _state(output) == before
    finally:
        if mutation == "fifo_member":
            member.unlink(missing_ok=True)
