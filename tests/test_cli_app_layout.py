"""Official app-package identity fixtures; no native/model subprocesses."""
import hashlib

import pytest


def app_package(tmp_path, monkeypatch):
    from devbench.study import cli_identity
    package = tmp_path / "codex-package"
    (package / "bin").mkdir(parents=True)
    native = package / "CodexCLI.app/Contents/MacOS/codex"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"native app fixture")
    metadata = package / "codex-package.json"
    metadata.write_text('{"version":"0.159.2"}')
    runtime = package / "codex-path"
    runtime.mkdir()
    (runtime / "rg").write_bytes(b"runtime helper fixture")
    monkeypatch.setattr(cli_identity, "_output", lambda *args: "codex-cli 0.159.2")
    return native, metadata, runtime


def test_app_bundle_identity_pins_outer_package_and_exact_path(tmp_path, monkeypatch):
    from devbench.study import cli_identity
    native, metadata, runtime = app_package(tmp_path, monkeypatch)
    identity = cli_identity.read_codex_identity(native)
    assert identity["runtime_path_prepends"] == [str(runtime)]
    assert identity["component_sha256"] == {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (native, metadata, runtime / "rg")}
    environment = {"PATH": "/isolated/python:/usr/bin:/bin", "OMP_NUM_THREADS": "1"}
    assert cli_identity.effective_child_environment(environment, identity) == {
        **environment, "PATH": str(runtime) + ":" + environment["PATH"]}
    cli_identity.verify_codex_identity(identity)


@pytest.mark.parametrize("change", ["metadata", "helper_bytes", "added_helper", "removed_helper"])
def test_app_package_drift_invalidates_identity(tmp_path, monkeypatch, change):
    from devbench.study import cli_identity
    native, metadata, runtime = app_package(tmp_path, monkeypatch)
    identity = cli_identity.read_codex_identity(native)
    if change == "metadata":
        metadata.write_text('{"version":"changed"}')
    elif change == "helper_bytes":
        (runtime / "rg").write_bytes(b"changed helper")
    elif change == "added_helper":
        (runtime / "new").write_bytes(b"unrecorded helper")
    else:
        (runtime / "rg").unlink()
    with pytest.raises((ValueError, OSError)):
        cli_identity.verify_codex_identity(identity)


@pytest.mark.parametrize("change", ["different_bundle", "different_contents", "different_native", "missing_bin", "missing_metadata"])
def test_app_lookalike_without_official_layout_has_no_package_prepend(tmp_path, monkeypatch, change):
    from devbench.study import cli_identity
    native, metadata, runtime = app_package(tmp_path, monkeypatch)
    if change == "different_bundle":
        replacement = native.parents[2].with_name("Other.app")
        native.parents[2].rename(replacement)
        native = replacement / "Contents/MacOS/codex"
    elif change == "different_contents":
        replacement = native.parents[1].with_name("OtherContents")
        native.parents[1].rename(replacement)
        native = replacement / "MacOS/codex"
    elif change == "different_native":
        replacement = native.with_name("other")
        native.rename(replacement)
        native = replacement
    elif change == "missing_bin":
        (metadata.parent / "bin").rmdir()
    else:
        metadata.unlink()
    assert cli_identity.read_codex_identity(native)["runtime_path_prepends"] == []


def test_app_runtime_directory_cannot_bypass_regular_file_inventory(tmp_path, monkeypatch):
    from devbench.study import cli_identity
    native, _, runtime = app_package(tmp_path, monkeypatch)
    (runtime / "alias").symlink_to(runtime / "rg")
    with pytest.raises(ValueError, match="regular files"):
        cli_identity.read_codex_identity(native)
