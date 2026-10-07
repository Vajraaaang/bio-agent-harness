"""Local packaging tests; these do not install into Codex or run model sessions."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "devharness"


def api():
    script = SOURCE / "scripts" / "configure.py"
    assert script.is_file(), "Plugin runtime configuration helper is missing"
    spec = importlib.util.spec_from_file_location("devharness_plugin_configure", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_template_contains_a_real_rna_skill_and_no_machine_specific_mcp_connection():
    manifest = json.loads((SOURCE / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == SOURCE.name == "devharness"
    assert "mcpServers" not in manifest
    skill = (SOURCE / "skills" / "rna-workbench" / "SKILL.md").read_text()
    assert "count" in skill and "donor" in skill and "refuse" in skill


@pytest.mark.parametrize("workspace", [Path("relative-data"), Path("/"), Path.home()])
def test_workspace_scope_is_explicit_and_not_the_whole_host(tmp_path, workspace):
    module = api()
    with pytest.raises(ValueError):
        module.configure(Path(sys.executable), workspace, tmp_path / "devharness")
    assert not (tmp_path / "devharness").exists()


def test_missing_runtime_does_not_write_a_half_configured_plugin(tmp_path):
    module = api()
    data = tmp_path / "analysis"
    data.mkdir()
    with pytest.raises(ValueError):
        module.configure(tmp_path / "missing-python", data, tmp_path / "devharness")
    assert not (tmp_path / "devharness").exists()


def test_configured_copy_uses_absolute_runtime_and_workspace_without_changing_template(tmp_path):
    module = api()
    data = tmp_path / "analysis"
    data.mkdir()
    original = (SOURCE / ".codex-plugin" / "plugin.json").read_bytes()
    output = tmp_path / "configured" / "devharness"
    result = module.configure(Path(sys.executable), data, output)
    manifest = json.loads((output / ".codex-plugin" / "plugin.json").read_text())
    server = manifest["mcpServers"]["devharness"]
    assert Path(server["command"]).is_absolute()
    assert server["command"] == str(Path(sys.executable).absolute())
    assert server["cwd"] == str(data.resolve())
    assert server["args"] == ["-I", "-B", "-m", "devharness_server.server"]
    assert all(value == "1" for value in server["env"].values())
    assert (SOURCE / ".codex-plugin" / "plugin.json").read_bytes() == original
    assert result["runtime"]["backend_version"]
    assert result["installed_into_codex"] is False


def test_existing_destination_is_preserved(tmp_path):
    module = api()
    data, output = tmp_path / "analysis", tmp_path / "devharness"
    data.mkdir()
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="new"):
        module.configure(Path(sys.executable), data, output)
    assert marker.read_text() == "keep"


def test_plugin_cannot_be_configured_to_serve_its_own_package(tmp_path):
    module = api()
    with pytest.raises(ValueError, match="workspace"):
        module.configure(Path(sys.executable), SOURCE, tmp_path / "devharness")


def test_backend_version_mismatch_requires_an_explicit_package_choice(tmp_path):
    module = api()
    data = tmp_path / "analysis"
    data.mkdir()
    with pytest.raises(ValueError, match="version"):
        module.configure(Path(sys.executable), data, tmp_path / "devharness", expected_version="not-installed")
    assert not (tmp_path / "devharness").exists()
