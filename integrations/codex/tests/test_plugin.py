"""Verify the portable local package without using the user's Codex state."""

import asyncio
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "devharness"
REFERENCES = Path("skills", "rna-workbench", "references")


def api():
    spec = importlib.util.spec_from_file_location(
        "devharness_plugin_configure", SOURCE / "scripts" / "configure.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def paths(tmp_path):
    inputs, workspace = tmp_path / "inputs", tmp_path / "workspace"
    inputs.mkdir()
    workspace.mkdir()
    return inputs, workspace, tmp_path / "marketplace" / "devharness"


def test_manifest_and_inactive_template():
    manifest = json.loads((SOURCE / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == SOURCE.name == "devharness"
    assert manifest["version"] == "0.2.0"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["skills"] == "./skills/"
    assert manifest["interface"]["displayName"] == "DevHarness RNA Research"
    assert json.loads((SOURCE / ".mcp.json").read_text()) == {"mcpServers": {}}


def test_pathway_and_spatial_payload_contracts():
    contracts = (SOURCE / REFERENCES / "mcp-payload-contracts.md").read_text()
    pathway = contracts.partition("### Gene-list pathway staging after DE\n")[2]
    pathway = pathway.partition("\n### ")[0]
    spatial = contracts.partition("#### Spatial pattern and colocalization\n")[2]
    spatial = spatial.partition("\n#### ")[0]
    assert "`fgsea` or `decoupleR`" in pathway
    score = "math.copysign(-math.log10(max(p, 1e-300)), effect) if effect else 0.0"
    assert score in pathway
    assert "`spot_id,donor_id,section_id,x,y`" in spatial
    assert "`cell_type` column is optional" in spatial
    assert "cell-type colocalization" in spatial


def test_retired_codex_package_is_absent():
    retired = (
        "devharness-0.1.0.zip",
        "validation",
        "package-evidence.json",
        "package-evidence.sha256",
        "tests/verify_rna_runtime.py",
    )
    assert not [name for name in retired if (SOURCE.parent / name).exists()]
    readme = (SOURCE.parent / "README.md").read_text()
    assert "devharness_server" not in readme
    assert not re.search(r"\b(?:10|ten)[\s-]+tools?\b", readme, re.I)


@pytest.mark.parametrize(
    "overlap", ["relative", "root", "home", "plugin", "input", "descendant"]
)
def test_workspace_scope_is_explicit(paths, overlap):
    inputs, workspace, output = paths
    descendant = inputs / "workspace"
    descendant.mkdir()
    override = {
        "relative": Path("relative-data"),
        "root": Path("/"),
        "home": Path.home(),
        "plugin": SOURCE,
        "input": inputs,
        "descendant": descendant,
    }
    with pytest.raises(ValueError):
        api().configure(Path(sys.executable), inputs, override[overlap], output)
    assert not output.exists()


def test_missing_runtime_preserves_destination(paths):
    inputs, workspace, output = paths
    with pytest.raises(ValueError, match="executable"):
        api().configure(inputs / "missing-python", inputs, workspace, output)
    assert not output.exists()


def test_backend_version_mismatch_preserves_destination(paths):
    inputs, workspace, output = paths
    with pytest.raises(ValueError, match="version"):
        api().configure(
            Path(sys.executable), inputs, workspace, output, expected_version="unknown"
        )
    assert not output.exists()


@pytest.mark.parametrize("allow_external", [False, True])
def test_configured_plugin_keeps_template(paths, allow_external):
    inputs, workspace, output = paths
    original = (SOURCE / ".mcp.json").read_bytes()
    result = api().configure(
        Path(sys.executable), inputs, workspace, output,
        allow_external_input_files=allow_external,
    )
    mcp = json.loads((output / ".mcp.json").read_text())
    server = mcp["mcpServers"]["devharness"]
    assert server["command"] == sys.executable
    assert server["cwd"] == str(workspace)
    assert ("--allow-external-input-files" in server["args"]) is allow_external
    assert server["args"][:8] == [
        "-I",
        "-B",
        "-m",
        "devbench.platform.plugin_mcp",
        "--input-root",
        str(inputs),
        "--workspace",
        str(workspace),
    ]
    assert all(server["env"][key] == value for key, value in api().THREADS.items())
    assert (SOURCE / ".mcp.json").read_bytes() == original
    reference = REFERENCES / "mcp-payload-contracts.md"
    assert (output / reference).read_bytes() == (SOURCE / reference).read_bytes()
    assert result["runtime"]["sqlite_version"]
    assert tuple(map(int, result["runtime"]["python_version"].split(".")[:2])) >= (
        3,
        13,
    )
    assert result["installed_into_codex"] is False


@pytest.mark.parametrize("link", ["directory", "file", "dangling"])
def test_package_rejects_symlink_references(tmp_path, monkeypatch, link):
    module = api()
    source = tmp_path / "source"
    shutil.copytree(SOURCE, source)
    references = source / REFERENCES
    external = tmp_path / "external"
    external.mkdir()
    outside = external / "outside.md"
    outside.write_text("Outside the plugin")
    if link == "directory":
        shutil.rmtree(references)
        references.symlink_to(external, target_is_directory=True)
    else:
        target = outside if link == "file" else external / "missing.md"
        (references / "linked.md").symlink_to(target)
    monkeypatch.setattr(module, "PLUGIN_ROOT", source)
    with pytest.raises(ValueError, match="reference"):
        module._source_package()


def test_existing_destination_is_preserved(paths):
    inputs, workspace, output = paths
    output.mkdir(parents=True)
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="new"):
        api().configure(Path(sys.executable), inputs, workspace, output)
    assert marker.read_text() == "keep"


def test_unpatched_runtime_is_refused_and_darwin_can_retry(paths, monkeypatch):
    inputs, workspace, output = paths
    module = api()
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(
            command, 1, "", "Scientific WAL requires patched SQLite"
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "_SQLITE_LIB", inputs)
    monkeypatch.setattr(module.sys, "platform", "darwin")
    with pytest.raises(ValueError, match="patched SQLite"):
        module.configure(Path(sys.executable), inputs, workspace, output)
    assert calls[-1]["DYLD_LIBRARY_PATH"] == str(inputs)
    assert not output.exists()


def test_marketplace_can_be_registered_in_isolated_codex_home(
    paths, tmp_path, monkeypatch
):
    inputs, workspace, output = paths
    codex = shutil.which("codex")
    if not codex:
        pytest.skip("Codex CLI unavailable")
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    result = api().configure(
        Path(sys.executable), inputs, workspace, output, write_marketplace=output.parent
    )
    manifest = Path(result["marketplace"])
    assert manifest == output.parent / ".agents" / "plugins" / "marketplace.json"
    added = subprocess.run(
        [codex, "plugin", "marketplace", "add", str(output.parent), "--json"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert json.loads(added.stdout)["marketplaceName"] == "devharness-local"
    assert (output / REFERENCES / "mcp-payload-contracts.md").is_file()


def test_configured_server_round_trips_over_real_stdio(paths):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    inputs, workspace, output = paths
    api().configure(Path(sys.executable), inputs, workspace, output)
    server = json.loads((output / ".mcp.json").read_text())["mcpServers"]["devharness"]
    server["args"].append("--no-auto-studio")

    async def exercise():
        async with (
            asyncio.timeout(25),
            stdio_client(StdioServerParameters(**server)) as (read, write),
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == {
                    "inspect_rna_inputs",
                    "check_answerability",
                    "plan_rna_workflow",
                    "execute_reviewed_plan",
                    "get_run_status",
                    "get_execution_receipt",
                    "manage_research_aim",
                }
                rejected = await session.call_tool(
                    "get_run_status", {"session_id": "0" * 32, "run_id": "1" * 32}
                )
                assert rejected.isError is True
                assert rejected.structuredContent == {
                    "error": "OperationBlocked",
                    "reason": "plan_identity_mismatch",
                }
                resources = {
                    str(resource.uri)
                    for resource in (await session.list_resources()).resources
                }
                assert resources == {
                    f"ui://devharness/{kind}-card"
                    for kind in ("plan", "run", "receipt")
                }
                card = await session.read_resource("ui://devharness/plan-card")
                assert card.contents[0].mimeType == "text/html;profile=mcp-app"

    asyncio.run(exercise())
