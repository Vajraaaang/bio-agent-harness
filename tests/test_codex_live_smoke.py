"""Exercise an isolated Codex installation using only generated synthetic workspaces."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from devbench.daemon.ipc import daemon_status, stop_daemon
from devbench.engine.permissions import PermissionBoundary, probe_boundary
from devbench.engine.workspace import runtime_read_roots
from devbench.mcp.schemas import INPUTS

ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "integrations/codex/devharness/scripts/configure.py"
CORE = "rna-research-core"


def _run(*argv, cwd, env, **kwargs):
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        **kwargs,
    ).stdout


@pytest.fixture(scope="module")
def isolated(module_workspace_tmp_path):
    root = module_workspace_tmp_path
    codex = shutil.which("codex")
    assert codex, "Install the Codex CLI to run the Tier 2 smoke"
    env = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SystemRoot")
        if key in os.environ
    }
    for key in ("HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        location = root / key.lower()
        location.mkdir(mode=0o700)
        env[key] = str(location)
    project = root / "project"
    data, workspace = root / "inputs", root / "workspace"
    for folder in (project, data, workspace):
        folder.mkdir()
    # Scope Codex project discovery to the fixture, even inside a parent checkout.
    _run("git", "init", "--quiet", project, cwd=root, env=env)
    shutil.copyfile(ROOT / "AGENTS.md", project / "AGENTS.md")
    marketplace = root / "marketplace"
    package = marketplace / "devharness"
    _run(
        sys.executable,
        CONFIGURE,
        "--python",
        sys.executable,
        "--input-root",
        data,
        "--workspace",
        workspace,
        "--output",
        package,
        "--write-marketplace",
        marketplace,
        env=env,
        cwd=root,
    )
    try:
        yield codex, project, package, env
    finally:
        if daemon_status(workspace)["status"] == "running":
            stop_daemon(workspace)


def _register_mcp(codex, project, package, env):
    server = json.loads((package / ".mcp.json").read_text())["mcpServers"]["devharness"]
    variables = []
    for key, value in server.get("env", {}).items():
        variables.extend(["--env", f"{key}={value}"])
    command = server["command"]
    if command.startswith("./"):
        command = str(package / command)
    _run(
        codex,
        "mcp",
        "add",
        "devharness",
        *variables,
        "--",
        command,
        *server["args"],
        cwd=project,
        env=env,
    )
    servers = json.loads(_run(codex, "mcp", "list", "--json", cwd=project, env=env))
    (entry,) = [server for server in servers if server["name"] == "devharness"]
    assert entry["enabled"]
    assert entry["transport"]["command"] == command
    assert entry["transport"]["args"] == server["args"]


async def _discovery(codex, project, env):
    proc = await asyncio.create_subprocess_exec(
        codex,
        "app-server",
        "--listen",
        "stdio://",
        cwd=project,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        limit=2**20,
    )

    async def request(identifier, method, params):
        message = {"id": identifier, "method": method, "params": params}
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        await proc.stdin.drain()
        while line := await proc.stdout.readline():
            response = json.loads(line)
            if response.get("id") == identifier:
                assert "error" not in response, response
                return response["result"]
        raise AssertionError("Codex app-server exited before replying")

    try:
        async with asyncio.timeout(45):
            client = {"name": "devharness-smoke", "version": "1"}
            await request(1, "initialize", {"clientInfo": client})
            proc.stdin.write(b'{"method":"initialized"}\n')
            result = await request(2, "skills/list", {"cwds": [str(project)]})
            servers = await request(3, "mcpServerStatus/list", {})
            return result["data"][0]["skills"], servers["data"]
    finally:
        proc.stdin.close()
        if proc.returncode is None:
            proc.terminate()
        await asyncio.wait_for(proc.wait(), 10)


def test_marketplace_mcp_and_skill_discovery(isolated):
    codex, project, package, env = isolated
    result = json.loads(
        _run(
            codex,
            "plugin",
            "marketplace",
            "add",
            package.parent,
            "--json",
            cwd=project,
            env=env,
        )
    )
    assert result["marketplaceName"] == "devharness-local"
    commands = _run(codex, "plugin", "--help", cwd=project, env=env)
    if re.search(r"^\s+add\s+", commands, re.MULTILINE):
        _run(
            codex,
            "plugin",
            "add",
            "devharness@devharness-local",
            "--json",
            cwd=project,
            env=env,
        )
    else:
        # Older builds support project skills and direct MCP registration.
        shutil.copytree(package / "skills", project / ".agents" / "skills")
    _register_mcp(*isolated)
    skills, servers = asyncio.run(_discovery(codex, project, env))
    (direct,) = [server for server in servers if server["name"] == "devharness"]
    assert set(direct["tools"]) == set(INPUTS)
    matches = [s for s in skills if s["name"].split(":")[-1] == CORE and s["enabled"]]
    assert matches, [(s["name"], s["scope"]) for s in skills]
    canonical = (ROOT / ".agents" / "skills" / CORE / "SKILL.md").read_bytes()
    for skill in matches:
        discovered = Path(skill["path"]).resolve()
        assert discovered.is_relative_to(Path(env["CODEX_HOME"])) or (
            discovered.is_relative_to(project) or discovered.is_relative_to(package)
        )
        assert discovered.read_bytes() == canonical
    size = (project / "AGENTS.md").stat().st_size
    assert size < 32 * 1024, "Codex project instruction hard cap"
    assert size < 16 * 1024, "Nested instruction budget"
    assert size < 8 * 1024, "Repository instruction budget"


def test_named_sandbox_boundary(isolated, monkeypatch):
    codex, project, _, env = isolated
    for key in tuple(os.environ):
        if key not in env:
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    private = project.parent / "private-probe"
    private.mkdir()
    roots = runtime_read_roots(project, {"linked_dependency_entries": []})
    boundary = PermissionBoundary(
        project, private_roots=(private,), runtime_roots=roots
    )
    proof = probe_boundary(
        boundary, codex, sys.executable, allow_configuration_probe=True
    )
    assert proof["verified"], (proof["error"], proof["checks"])


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY absent")
def test_exec_discovers_and_calls_runtime(isolated):
    codex, project, package, env = isolated
    _register_mcp(*isolated)
    # Explicitly scoped project skill for an independent direct-MCP run.
    source = package / "skills" / CORE
    shutil.copytree(source, project / ".agents" / "skills" / CORE, dirs_exist_ok=True)
    prompt = (
        "Use $rna-research-core. Call the devharness inspect_runtime_capabilities "
        "MCP tool once and report whether its SQLite WAL is patched."
    )
    events = _run(
        codex,
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-c",
        'approval_policy="never"',
        prompt,
        cwd=project,
        env={**env, "CODEX_API_KEY": os.environ["OPENAI_API_KEY"]},
        stdin=subprocess.DEVNULL,
    )
    completed = [
        event["item"]
        for line in events.splitlines()
        if (event := json.loads(line)).get("type") == "item.completed"
    ]
    assert any(
        item.get("type") == "mcp_tool_call"
        and item.get("tool") == "inspect_runtime_capabilities"
        and item.get("status") == "completed"
        and (item.get("result") or {}).get("isError") is not True
        for item in completed
    ), "Codex did not complete the registered runtime MCP call"
