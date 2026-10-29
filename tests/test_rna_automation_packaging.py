"""Offline reproduction must retain exact community runtime preparation assets."""
import base64
import zipfile
import io

from scripts.build_colab_notebook import allowed_bundle_path, build_source_bundle, extract_source_bundle

ASSETS = ("Dockerfile", "README.md", "build_local.py", "collect_inventory.py",
    "environment-explicit.linux-arm64.txt", "nextflow-launcher", "package-lock.linux-arm64.json",
    "prepare_context.py", "software_preflight.py")
PREFIX = "devbench/analysis/assets/community-workflow/"


def test_community_assets_roundtrip_from_explicit_allowlist(tmp_path):
    source = tmp_path / "source"
    (source / "scripts").mkdir(parents=True)
    (source / "scripts/colab_run.py").write_text("print('fixture')\n")
    (source / "pyproject.toml").write_text("[project]\nname='fixture'\nversion='1'\n")
    (source / "README.md").write_text("Synthetic packaging fixture\n")
    for name in ASSETS:
        member = source / PREFIX / name
        member.parent.mkdir(parents=True, exist_ok=True)
        member.write_bytes(("synthetic " + name + "\n").encode())
    (source / PREFIX / "credentials.json").write_text("not-real-do-not-package")
    (source / PREFIX / "unlisted.txt").write_text("unlisted")
    result = build_source_bundle(source)
    archive = zipfile.ZipFile(io.BytesIO(base64.b64decode(result["base64"])))
    assert set(PREFIX + name for name in ASSETS).issubset(archive.namelist())
    assert PREFIX + "credentials.json" not in archive.namelist()
    assert PREFIX + "unlisted.txt" not in archive.namelist()
    restored = tmp_path / "restored"
    extract_source_bundle(result["base64"], restored, result["sha256"])
    for name in ASSETS:
        assert (source / PREFIX / name).read_bytes() == (restored / PREFIX / name).read_bytes()


def test_noncode_community_asset_allowlist_stays_exact():
    for name in ASSETS:
        assert allowed_bundle_path(PREFIX + name)
    for name in ("unlisted.txt", "credentials.json", "package-lock.other-platform.json", "../README.md"):
        assert not allowed_bundle_path(PREFIX + name)
