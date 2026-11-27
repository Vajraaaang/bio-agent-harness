"""Verify the portable local package without using the user's Codex state."""

import asyncio
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from jsonschema import Draft202012Validator

from devbench.platform import plugin_workflows
from tests.conftest import _workspace
from tests.test_mcp_13_tool_control_plane import NAMES
from tests.test_mcp_conformance import _exercise
from tests.test_research_daemon import _stop_when_idle

SOURCE = Path(__file__).resolve().parents[1] / "devharness"
CANONICAL = SOURCE.parents[2] / ".agents" / "skills"
SKILLS = {
    "rna-research-core",
    "scrna-snrna-qc-integration",
    "pseudobulk-and-de",
    "composition-and-da",
    "trajectory-and-programs",
    "spatial-and-ccc",
    "method-benchmarking-and-critic",
    "private-data-studio",
}
SCHEMAS = Path(__file__).parent / "schemas"


def _read(path):
    return json.loads(path.read_bytes())


def _validate(directory, name):
    spec = _read(SCHEMAS / f"{name}.schema.json")
    document = _read(directory / f"{name}.json")
    assert document["$schema"] == spec["$id"]
    Draft202012Validator(spec).validate(document)
    return document


def _cli(*args, check=True):
    return subprocess.run(
        [sys.executable, str(SOURCE / "scripts" / "configure.py"), *map(str, args)],
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
    )


def api():
    spec = importlib.util.spec_from_file_location(
        "devharness_plugin_configure", SOURCE / "scripts" / "configure.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def paths(pytestconfig):
    with _workspace(pytestconfig) as root:
        inputs, workspace = root / "inputs", root / "workspace"
        inputs.mkdir()
        workspace.mkdir()
        yield inputs, workspace, root / "marketplace" / "devharness"


def test_manifest_and_inactive_template():
    manifest = _validate(SOURCE, "plugin")
    assert _validate(SOURCE, "mcp")["mcpServers"] == {}
    assert _read(SOURCE / ".mcp.json") == {"mcpServers": {}}
    assert manifest["name"] == "devharness" and manifest["version"] == "0.3.0"
    compat = _read(SOURCE / ".codex-plugin" / "plugin.json")
    assert (compat["name"], compat["version"]) == (
        manifest["name"],
        manifest["version"],
    )
    assert compat["skills"] == "./skills/" and compat["mcpServers"] == "./.mcp.json"
    assert compat["interface"] == manifest["extensions"]["com.openai"]["interface"]
    assert compat["interface"]["displayName"] == "DevHarness RNA Research"


def test_pathway_and_spatial_payload_contracts():
    pathway = (CANONICAL / "trajectory-and-programs" / "SKILL.md").read_text()
    spatial = (CANONICAL / "spatial-and-ccc" / "SKILL.md").read_text()
    assert "`fgsea` or `decoupleR`" in pathway
    (formula,) = re.findall(r"The score is `([^`]+)`", pathway)
    # Only evaluate the reviewed arithmetic expression from the documentation.
    assert (
        formula
        == "math.copysign(-math.log10(max(p, 1e-300)), effect) if effect else 0.0"
    )
    cases = [
        ("positive", 2.0, 0.05),
        ("negative", -0.5, 0.01),
        ("zero_effect", 0.0, 0.001),
        ("zero_p", -0.5, 0.0),
        ("below_floor", 0.5, 1e-320),
        ("unit_p", -1.0, 1.0),
    ]
    rows = pa.Table.from_pylist(
        [
            dict(
                gene_id=gene,
                effect=effect,
                p_value=p,
                q_value=1.0,
                estimate_status="estimated",
            )
            for gene, effect, p in cases
        ]
    )
    parquet = BytesIO()
    pq.write_table(rows, parquet)
    executor = SimpleNamespace(open_output=lambda _: BytesIO(parquet.getvalue()))
    source = {
        "receipt_sha256": "a" * 64,
        "artifact": {
            "version_id": "fixture",
            "sha256": "b" * 64,
            "representation": "gene_statistics",
            "biological_unit": "donor",
            "kind": "table",
            "unit_membership_sha256": "c" * 64,
        },
    }
    _, scores = plugin_workflows._pathway_universe(
        executor, source, q_max=None, ranked=True
    )
    assert scores == {
        gene: eval(
            formula,
            {"__builtins__": {}, "math": math, "max": max},
            {"p": p, "effect": effect},
        )
        for gene, effect, p in cases
    }

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
    assert all(name in readme for name in NAMES)


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
        Path(sys.executable),
        inputs,
        workspace,
        output,
        allow_external_input_files=allow_external,
    )
    mcp = json.loads((output / ".mcp.json").read_text())
    server = mcp["mcpServers"]["devharness"]
    assert server["command"] == sys.executable
    assert server["cwd"] == str(workspace)
    assert ("--allow-external-input-files" in server["args"]) is allow_external
    assert server["args"][:10] == [
        "-I",
        "-B",
        "-m",
        "devbench.platform.cli",
        "mcp",
        "--input-root",
        str(inputs),
        "--workspace",
        str(workspace),
        "--no-auto-browser",
    ]
    assert all(server["env"][key] == value for key, value in api().THREADS.items())
    assert (SOURCE / ".mcp.json").read_bytes() == original
    portable = _validate(output, "mcp")["mcpServers"]["devharness"]
    assert portable["type"] == "stdio"
    assert portable["command"].startswith("./")
    assert portable["args"] == server["args"] and portable["env"] == server["env"]
    assert _validate(output, "plugin") == _read(SOURCE / "plugin.json")
    for name in ("plugin.json", ".codex-plugin/plugin.json"):
        assert (output / name).read_bytes() == (SOURCE / name).read_bytes()
    assert {d.name for d in (output / "skills").iterdir()} == SKILLS
    for skill in SKILLS:
        assert (output / "skills" / skill / "SKILL.md").read_bytes() == (
            CANONICAL / skill / "SKILL.md"
        ).read_bytes()
    assert not any(p.is_symlink() for p in output.rglob("*"))
    assert result["runtime"]["sqlite_version"]
    assert tuple(map(int, result["runtime"]["python_version"].split(".")[:2])) >= (
        3,
        13,
    )
    assert result["installed_into_codex"] is False


@pytest.mark.parametrize("link", ["directory", "file", "dangling", "hardlink"])
def test_package_rejects_linked_skills(tmp_path, monkeypatch, link):
    module = api()
    source = tmp_path / "skills"
    shutil.copytree(CANONICAL, source)
    skill = source / "spatial-and-ccc"
    outside = tmp_path / "outside.md"
    outside.write_text("local fixture")
    if link == "directory":
        shutil.rmtree(skill)
        skill.symlink_to(CANONICAL / skill.name, target_is_directory=True)
    elif link == "hardlink":
        os.link(outside, skill / "extra.md")
    else:
        (skill / "extra.md").symlink_to(
            outside if link == "file" else tmp_path / "absent"
        )
    monkeypatch.setattr(module, "SKILL_ROOT", source)
    with pytest.raises((ValueError, OSError)):
        module._source_package()


def test_existing_destination_is_preserved(paths):
    inputs, workspace, output = paths
    output.mkdir(parents=True)
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="new"):
        api().configure(Path(sys.executable), inputs, workspace, output)
    assert marker.read_text() == "keep"


@pytest.mark.parametrize(
    ("platform", "variable"),
    [("darwin", "DYLD_LIBRARY_PATH"), ("linux", "LD_LIBRARY_PATH")],
)
def test_unpatched_system_runtime_retries_and_reports_remediation(
    paths, monkeypatch, platform, variable
):
    inputs, workspace, output = paths
    module = api()
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(
            command, 78, "", "Scientific WAL requires patched SQLite"
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module, "_SQLITE_LIBS", (inputs,))
    monkeypatch.setattr(module.sys, "platform", platform)
    monkeypatch.setenv("DEVBENCH_SQLITE_LIB", str(inputs))
    with pytest.raises(ValueError, match="uv python install"):
        module.configure(Path(sys.executable), inputs, workspace, output)
    assert variable not in calls[0] and calls[1][variable] == str(inputs)
    assert not output.exists()


def test_manifest_opt_out_and_local_marketplace(paths):
    inputs, workspace, output = paths
    _cli(
        "--python",
        sys.executable,
        "--input-root",
        inputs,
        "--workspace",
        workspace,
        "--output",
        output,
        "--write-marketplace",
        output.parent,
        "--no-legacy-codex-manifest",
    )
    assert not (output / ".codex-plugin").exists()
    assert _validate(output, "plugin")["version"] == "0.3.0"
    market = _read(output.parent / ".agents/plugins/marketplace.json")
    assert market["name"] == "devharness-local"
    (entry,) = market["plugins"]
    assert entry["name"] == "devharness" and entry["source"]["path"] == "./devharness"
    assert not (workspace / ".claude").exists()
    assert not (workspace / ".cursor").exists()


@pytest.mark.parametrize(
    ("clients", "legacy", "targets"),
    [
        ("claude", False, [".claude/skills"]),
        ("cursor", False, [".agents/skills"]),
        ("all", True, [".agents/skills", ".claude/skills", ".cursor/skills"]),
    ],
)
def test_client_mirrors_roundtrip(tmp_path, clients, legacy, targets):
    root = tmp_path.resolve()
    cmd = ["--mirror-root", root, "--mirror-clients", clients]
    if legacy:
        cmd.append("--legacy-cursor-skills")
    _cli(*cmd)
    record = root / ".devharness-mirror-manifest.json"
    original = record.read_bytes()
    _cli(*cmd)
    assert record.read_bytes() == original
    saved = _read(record)
    assert saved["targets"] == targets
    assert not (root / ".cursor/rules").exists()
    assert set(saved["files"]) == {
        f"{target}/{skill}/SKILL.md" for target in targets for skill in SKILLS
    }
    for path, checksums in saved["files"].items():
        dest = root / path
        source = CANONICAL / dest.parent.name / dest.name
        digest = sha256(source.read_bytes()).hexdigest()
        assert sha256(dest.read_bytes()).hexdigest() == digest
        assert not source.samefile(dest)
        assert checksums == {"mirror_sha256": digest, "source_sha256": digest}
    assert not any(p.is_symlink() for p in root.rglob("*"))
    _cli("--check-mirrors", "--mirror-root", root)


@pytest.mark.parametrize(
    "damage",
    ["bytes", "file-symlink", "directory-symlink", "manifest-symlink", "hardlink"],
)
def test_check_mirrors_refuses_drift(tmp_path, damage):
    root = tmp_path.resolve() / "client"
    _cli("--mirror-root", root, "--mirror-clients", "claude")
    dest = root / ".claude/skills/rna-research-core/SKILL.md"
    if damage == "bytes":
        dest.write_text("drift")
    elif damage == "directory-symlink":
        shutil.rmtree(dest.parent)
        dest.parent.symlink_to(CANONICAL / dest.parent.name, target_is_directory=True)
    elif damage == "manifest-symlink":
        manifest = root / ".devharness-mirror-manifest.json"
        manifest.rename(tmp_path / "manifest")
        manifest.symlink_to(tmp_path / "manifest")
    else:
        external = tmp_path / "same-bytes"
        dest.rename(external)
        if damage == "hardlink":
            os.link(external, dest)
        else:
            dest.symlink_to(external)
    assert _cli("--check-mirrors", "--mirror-root", root, check=False).returncode != 0


@pytest.mark.parametrize("portable", [True, False], ids=["portable", "codex"])
def test_configured_server_round_trips_over_real_stdio(paths, portable):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    inputs, workspace, output = paths
    api().configure(Path(sys.executable), inputs, workspace, output)
    manifest = "mcp.json" if portable else ".mcp.json"
    server = _read(output / manifest)["mcpServers"]["devharness"]
    if portable:
        server = {
            **server,
            "command": str(output / server["command"]),
            "cwd": str(output),
        }
    counts = inputs / "counts.csv"
    counts.write_text("gene,A,B\nred,1,2\nblue,3,4\ngreen,2,7\n")

    async def exercise():
        async with (
            stdio_client(StdioServerParameters(**server)) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            await _exercise(session, counts)

    try:
        asyncio.run(exercise())
    finally:
        _stop_when_idle(workspace)


def test_detect_image_prefers_routed_de_over_older_voom(tmp_path, monkeypatch):
    import platform as sys_platform

    from devharness_controlled.native_contracts import (
        LIMMA_VOOM_KERNEL_IMAGES,
        ROUTED_DE_KERNEL_IMAGES,
        limma_voom_kernel_images,
        routed_de_kernel_images,
    )

    configure = api()
    pinned = sorted(limma_voom_kernel_images())
    preferred = sorted(routed_de_kernel_images())
    routed_arm = ROUTED_DE_KERNEL_IMAGES["linux/arm64"]
    legacy_arm = LIMMA_VOOM_KERNEL_IMAGES["linux/arm64"]

    monkeypatch.setattr(sys_platform, "machine", lambda: "arm64")
    monkeypatch.setattr(configure.shutil, "which", lambda _name: "/usr/bin/docker")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            stdout=f"{routed_arm} arm64\n{legacy_arm} arm64\n"
        ),
    )
    assert configure._detect_image(pinned, tmp_path, preferred=preferred) == routed_arm

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(stdout=f"{legacy_arm} arm64\n"),
    )
    assert configure._detect_image(pinned, tmp_path, preferred=preferred) == legacy_arm


def test_external_file_permission_is_bound_to_configured_shim(paths):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from devbench.platform.workbench_ui import _detect_mounted_image
    from tests.test_research_daemon import _admission

    inputs, workspace, output = paths
    image = _detect_mounted_image()
    api().configure(
        Path(sys.executable),
        inputs,
        workspace,
        output,
        allow_external_input_files=True,
        mounted_image_id=image,
    )
    server = _read(output / ".mcp.json")["mcpServers"]["devharness"]
    args = server["args"]
    if image:
        assert args[args.index("--mounted-image-id") + 1] == image
    external = inputs.parent / "external"
    external.mkdir()
    metadata = external / "metadata.csv"
    metadata.write_text("sample,group\nA,one\nB,two\n")
    internal = workspace / "internal.csv"

    async def exercise():
        async with (
            stdio_client(StdioServerParameters(**server)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            internal.write_bytes(metadata.read_bytes())
            reg = _admission(metadata)
            reg["input"]["file_kind"] = "metadata"
            reg["representation_class"] = "metadata_only"
            accepted = await client.call_tool("register_investigation_input", reg)
            assert not accepted.isError, accepted.structuredContent
            grant = accepted.structuredContent
            ref = grant["artifact"]
            reply = await client.call_tool(
                "query_artifact_table",
                {
                    "session_id": grant["session_id"],
                    "artifact_id": ref["artifact_id"],
                    "artifact_sha256": ref["sha256"],
                    "columns": ["sample"],
                },
            )
            assert not reply.isError and reply.structuredContent["rows"] == [
                ["A"],
                ["B"],
            ]
            for path in (external, internal):
                declined = await client.call_tool(
                    "register_investigation_input",
                    {**reg, "input": {**reg["input"], "path": str(path)}},
                )
                assert declined.isError
                assert declined.structuredContent["category"] == "input_scope_violation"
            default = [flag for flag in args if flag != "--allow-external-input-files"]
            denied = await asyncio.create_subprocess_exec(
                server["command"],
                *default,
                cwd=workspace,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(denied.communicate(), 10)
            assert denied.returncode != 0 and not stdout
            assert b"workspace daemon connection failed" in stderr

    try:
        asyncio.run(exercise())
    finally:
        _stop_when_idle(workspace)


def test_mirror_upgrade_removes_only_unchanged_owned_files(tmp_path, monkeypatch):
    module = api()
    sources = tmp_path.resolve() / "sources"
    shutil.copytree(CANONICAL, sources)
    monkeypatch.setattr(module, "SKILL_ROOT", sources)
    root = tmp_path.resolve() / "project"
    skill = Path("rna-research-core")
    ref = skill / "references" / "old.md"
    (sources / ref).parent.mkdir()
    (sources / ref).write_text("old release")
    module.mirror_skills(root, "all")
    prior = root / module.MIRROR_MANIFEST
    saved_manifest = prior.read_bytes()
    changed = root / ".claude/skills" / ref
    changed.write_text("local edits")
    personal = root / ".claude/skills" / skill / "mine.md"
    personal.write_text("personal")
    (sources / ref).unlink()
    (sources / skill / "new.md").write_text("new release")
    with pytest.raises(ValueError):
        module.mirror_skills(root, "all")
    assert prior.read_bytes() == saved_manifest
    assert changed.read_text() == "local edits"
    assert personal.read_text() == "personal"
    personal.unlink()
    changed.write_text("old release")
    record = module.mirror_skills(root, "all")
    assert module.check_mirrors(root) == record
    for target in record["targets"]:
        assert not (root / target / ref).exists()
        assert (root / target / skill / "new.md").read_text() == "new release"


def test_mirror_manifest_rejects_escaping_obsolete_entry(tmp_path):
    module = api()
    root = tmp_path.resolve() / "project"
    saved = module.mirror_skills(root, "claude")
    outside = root / "personal.txt"
    outside.write_text("personal")
    digest = sha256(outside.read_bytes()).hexdigest()
    saved["files"][".claude/skills/rna-research-core/../../../personal.txt"] = {
        "mirror_sha256": digest,
        "source_sha256": digest,
    }
    (root / module.MIRROR_MANIFEST).write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="manifest"):
        module.mirror_skills(root, "claude")
    assert outside.read_text() == "personal"


def test_backend_package_origin_refused_as_input(paths):
    from importlib.metadata import distribution

    _, workspace, output = paths
    scripts = distribution("devharness").entry_points
    package = next(e for e in scripts if e.name == "devharness").module.split(".")[0]
    origin = Path(importlib.util.find_spec(package).origin).resolve().parent
    with pytest.raises(ValueError, match="backend source"):
        api().configure(Path(sys.executable), origin, workspace, output)
    assert not output.exists()


def test_managed_probe_requires_pinned_floor_even_for_wal_backport(monkeypatch):
    import sqlite3
    import sysconfig

    from devbench.engine.scientific_authority import require_patched_wal

    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 50, 7))
    monkeypatch.setitem(sysconfig.get_config_vars(), "PYTHON_BUILD_STANDALONE", 1)
    require_patched_wal()
    with pytest.raises(SystemExit, match="Managed Python requires SQLite >= 3.51.3"):
        exec(api()._QUERY, {})


def test_bundled_quickstart_data_can_be_bound(paths):
    _, workspace, output = paths
    inputs = SOURCE.parents[2] / "examples" / "bulk-rnaseq" / "data"
    assert (inputs / "counts.csv").is_file() and (inputs / "design.csv").is_file()
    binding = api().configure(Path(sys.executable), inputs, workspace, output)
    assert binding["input_root"] == str(inputs)
