"""Identity pinning protocol tests; no Codex model/version subprocesses run."""
import hashlib
import json

import pytest


@pytest.mark.parametrize("bad", [None, "version", "exit", "oversized", "changed_binary"])
def test_version_identity_is_bounded_and_binds_binary_bytes(tmp_path, monkeypatch, bad):
    from devbench.study.cli_identity import read_codex_identity
    from devbench_engine.adapters import CodexCLIAdapter
    binary = tmp_path / "codex"
    binary.write_bytes(b"fixture executable")
    def run(self, command, workspace, environment, instruction, timeout_sec, parse_events):
        assert command == [str(binary), "--version"] and parse_events is False
        assert timeout_sec == 5
        self._last_process_measurements = {"stdout_bytes": 513 if bad == "oversized" else 20}
        if bad == "changed_binary":
            binary.write_bytes(b"changed executable")
        return (1 if bad == "exit" else 0), ("unknown" if bad == "version" else "codex-cli 0.147.0\n"), ""
    monkeypatch.setattr(CodexCLIAdapter, "_run_process", run)
    if bad is not None:
        with pytest.raises(ValueError):
            read_codex_identity(binary)
    else:
        result = read_codex_identity(binary)
        assert result["version"] == "codex-cli 0.147.0"
        assert result["executable_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
        assert result["runtime_path_prepends"] == []


def test_npm_wrapper_identity_includes_and_rechecks_native_payload(tmp_path, monkeypatch):
    from devbench.study import cli_identity
    package = tmp_path / "package"
    (package / "bin").mkdir(parents=True)
    launcher = package / "bin/codex.js"
    launcher.write_text('findCodexExecutable "bin"')
    (package / "package.json").write_text('{"name":"@openai/codex"}')
    native, node = tmp_path / "native", tmp_path / "node"
    native.write_bytes(b"native fixture")
    node.write_bytes(b"node fixture")
    monkeypatch.setattr(cli_identity.shutil, "which", lambda name: str(node))
    monkeypatch.setattr(cli_identity, "_output", lambda executable, command, limit:
                        json.dumps([str(node), str(native)]) if "-e" in command else "codex-cli 0.147.0")
    result = cli_identity.read_codex_identity(launcher)
    assert {str(launcher), str(native), str(node)} <= result["component_sha256"].keys()
    native.write_bytes(b"changed native fixture")
    with pytest.raises(ValueError, match="component changed"):
        cli_identity.verify_codex_identity(result)


def test_all_runtime_fingerprints_change_when_native_payload_changes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from devbench.study import cli_identity
    from devbench.study.contracts import RunBudget
    from devbench.study.execution import _environment_identity
    from devbench.study.biology_execution import _runtime_identity as biology_identity
    from devbench.study.public_execution import _runtime_identity as public_identity
    entry, native = tmp_path / "entry", tmp_path / "native"
    entry.write_bytes(b"launcher fixture")
    native.write_bytes(b"native fixture")
    monkeypatch.setattr(cli_identity, "_components", lambda path: [entry, native])
    plan = SimpleNamespace(budgets=RunBudget(), budget=RunBudget(), reasoning_effort=None)
    functions = (_environment_identity, biology_identity, public_identity)
    before = [function((), plan, entry) for function in functions]
    native.write_bytes(b"changed native fixture")
    assert all(function((), plan, entry) != old for function, old in zip(functions, before))


def _package_identity_fixture(tmp_path, monkeypatch, *, npm=False):
    from devbench.study import cli_identity
    package = tmp_path / "native-package"
    (package / "bin").mkdir(parents=True)
    native = package / "bin/codex"
    native.write_bytes(b"native fixture")
    metadata = package / "codex-package.json"
    metadata.write_text('{"version":"0.147.0"}')
    runtime = package / "codex-path"
    runtime.mkdir()
    (runtime / "rg").write_bytes(b"packaged rg fixture")
    executable = native
    if npm:
        wrapper = tmp_path / "npm-package"
        (wrapper / "bin").mkdir(parents=True)
        executable = wrapper / "bin/codex.js"
        executable.write_text('findCodexExecutable "bin"')
        (wrapper / "package.json").write_text('{"name":"@openai/codex"}')
        node = tmp_path / "node"
        node.write_bytes(b"node fixture")
        monkeypatch.setattr(cli_identity.shutil, "which", lambda name: str(node))
    monkeypatch.setattr(cli_identity, "_output", lambda executable, command, limit:
                        json.dumps([str(node), str(native)]) if "-e" in command else "codex-cli 0.147.0")
    return executable, metadata, runtime


@pytest.mark.parametrize("npm", [False, True])
def test_identity_pins_native_package_path_and_contents(tmp_path, monkeypatch, npm):
    from devbench.study import cli_identity
    executable, metadata, runtime = _package_identity_fixture(tmp_path, monkeypatch, npm=npm)
    identity = cli_identity.read_codex_identity(executable)
    assert identity["runtime_path_prepends"] == [str(runtime)]
    for path in (metadata, runtime / "rg"):
        assert identity["component_sha256"][str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
    cli_identity.verify_codex_identity(identity)


@pytest.mark.parametrize("mutation", ["metadata", "runtime_bytes", "added_file", "removed_file"])
def test_package_changes_invalidate_cli_identity(tmp_path, monkeypatch, mutation):
    from devbench.study import cli_identity
    executable, metadata, runtime = _package_identity_fixture(tmp_path, monkeypatch)
    identity = cli_identity.read_codex_identity(executable)
    if mutation == "metadata":
        metadata.write_text('{"version":"changed"}')
    elif mutation == "runtime_bytes":
        (runtime / "rg").write_bytes(b"changed helper")
    elif mutation == "added_file":
        (runtime / "python").write_bytes(b"unrecorded executable")
    else:
        (runtime / "rg").unlink()
    with pytest.raises((ValueError, OSError)):
        cli_identity.verify_codex_identity(identity)


@pytest.mark.parametrize("unsafe", ["directory", "symlink", "oversized", "too_many"])
def test_package_path_inventory_is_bounded_and_regular(tmp_path, monkeypatch, unsafe):
    from devbench.study import cli_identity
    executable, _, runtime = _package_identity_fixture(tmp_path, monkeypatch)
    if unsafe == "directory":
        (runtime / "nested").mkdir()
    elif unsafe == "symlink":
        (runtime / "alias").symlink_to(runtime / "rg")
    elif unsafe == "oversized":
        with (runtime / "rg").open("wb") as handle:
            handle.truncate(250_000_001)
    else:
        for number in range(33):
            (runtime / str(number)).write_bytes(b"helper")
    with pytest.raises(ValueError):
        cli_identity.read_codex_identity(executable)


def test_effective_child_environment_matches_package_prepend_without_inheritance(tmp_path, monkeypatch):
    from devbench.study import cli_identity
    executable, _, runtime = _package_identity_fixture(tmp_path, monkeypatch)
    identity = cli_identity.read_codex_identity(executable)
    original = {"PATH": f"/usr/bin::{runtime}:/bin:/usr/bin:", "TMPDIR": str(tmp_path), "OMP_NUM_THREADS": "1"}
    monkeypatch.setenv("PATH", "/host/path/must/not/be/inherited")
    effective = cli_identity.effective_child_environment(original, identity)
    assert effective == {**original, "PATH": f"{runtime}:/usr/bin:/bin:/usr/bin"}
    assert original["PATH"] == f"/usr/bin::{runtime}:/bin:/usr/bin:"
    effective["OMP_NUM_THREADS"] = "99"
    assert original["OMP_NUM_THREADS"] == "1"
    assert cli_identity.effective_child_environment({}, identity) == {"PATH": str(runtime)}


def test_effective_child_environment_without_package_layout_is_unchanged():
    from devbench.study.cli_identity import effective_child_environment
    original = {"PATH": ":/bin::/bin:", "TMPDIR": "/absolute/synthetic/tmp"}
    effective = effective_child_environment(original, {"runtime_path_prepends": []})
    assert effective == original and effective is not original
    assert effective_child_environment({}, {}) == {}
