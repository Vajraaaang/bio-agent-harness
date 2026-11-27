"""Layer 4. Observe every outgoing wire byte for synthetic protected inputs."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devbench.core.protected_partitions import PartitionVault
from devbench.daemon import ipc
from devbench.daemon.auth import read_mcp_token
from devbench.engine.partition_authority import PartitionAuthority
from devbench.engine.workflow_operations import LEDGER_NAME, WorkflowOperationLedger
from tests.test_mcp_13_tool_control_plane import NAMES
from tests.test_research_daemon import _admission, _audit_plan, _daemon, _http, _rpc

pytestmark = pytest.mark.wal


def _browser_instrumentation(marker):
    return (
        "import webbrowser; from pathlib import Path; "
        "from devbench.platform import workbench_ui; "
        f"attempt=lambda *_a, **_kw: Path({str(marker)!r}).touch(); "
        "webbrowser.open=webbrowser.open_new=webbrowser.open_new_tab=attempt; "
        "workbench_ui._open_browser_async=attempt; "
    )


def _observe_daemon(monkeypatch, marker, stderr):
    def spawn(workspace, config, lock_fd):
        # Instrument the owner of the Studio service before starting its real
        # lifecycle. Keep its stderr too, independently of the forwarding shim.
        script = (
            _browser_instrumentation(marker) + "import json, os, sys; os.umask(0o077); "
            "from devbench.daemon.server import serve; "
            "serve(Path(sys.argv[1]), json.loads(sys.argv[2]), int(sys.argv[3]))"
        )
        with stderr.open("wb") as errors:
            return subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    script,
                    str(workspace),
                    json.dumps(config),
                    str(lock_fd),
                ],
                cwd=workspace,
                pass_fds=(lock_fd,),
                start_new_session=True,
                env={
                    k: os.environ[k]
                    for k in ("HOME", "PATH", "LANG", "TMPDIR", "XDG_RUNTIME_DIR")
                    if k in os.environ
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errors,
            )

    monkeypatch.setattr(ipc, "_spawn_daemon", spawn)


@asynccontextmanager
async def _wire(workspace, inputs, captured, stderr, browser_log):
    # Instrument both browser entry points in the shim. Run its ordinary CLI with
    # no browser flag and an actual pipes-based MCP session, including UI resources.
    script = _browser_instrumentation(browser_log) + (
        "from devbench.platform import cli; raise SystemExit(cli.main())"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
        "mcp",
        "--workspace",
        str(workspace),
        "--input-root",
        str(inputs),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        limit=1024 * 1024,
    )

    class Capture:
        async def readline(self):
            line = await process.stdout.readline()
            captured.extend(line)
            return line

    wire = SimpleNamespace(stdout=Capture(), stdin=process.stdin)
    try:
        await _rpc(
            wire,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {"extensions": {"io.modelcontextprotocol/ui": {}}},
                "clientInfo": {"name": "privacy-receiver", "version": "1"},
            },
        )
        await _rpc(wire, "notifications/initialized", {}, notify=True)
        yield wire
    finally:
        process.stdin.close()
        try:
            rest, _ = await asyncio.wait_for(process.communicate(), 10)
            captured.extend(rest)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


def _plant(inputs, workspace):
    tokens = []
    entries = []
    vault = PartitionVault(workspace / "partition-vault")
    ledger = WorkflowOperationLedger(
        workspace / "methods" / LEDGER_NAME, scientific=True
    )
    custody = PartitionAuthority(ledger, sha256(b"synthetic partition").hexdigest())
    for policy in ("private", "clinical", "embargoed"):
        for kind in ("expression", "metadata"):
            filename, column, value = [f"S{uuid4().hex}" for _ in range(3)]
            tokens.extend([filename, column, value])
            path = inputs / (filename + ".csv")
            path.write_text(f"feature,{column}\n{value},4\n")
            entry = _admission(path, "synthetic" if policy == "embargoed" else policy)
            entry["input"]["file_kind"] = kind
            if kind == "metadata":
                entry["representation_class"] = "metadata_only"
            if policy == "embargoed":
                checksum = vault.put_file(path)
                custody.protect_outputs([checksum])
            entries.append((policy, entry))
    return tokens, entries


async def _probe(wire, entries, secret):
    tools = (await _rpc(wire, "tools/list", {}))["tools"]
    assert tuple(t["name"] for t in tools) == NAMES

    async def call(name, arguments):
        return await _rpc(wire, "tools/call", {"name": name, "arguments": arguments})

    for name in NAMES:
        invalid = await call(name, {secret: [secret]})
        assert invalid["isError"] and invalid["structuredContent"]["validation_errors"]
    grants = []
    for policy, reg in entries:
        entry = {k: v for k, v in reg["input"].items() if k != "investigation_role"}
        inspected = await call("inspect_inputs", {"inputs": [entry]})
        assert inspected["isError"]
        registered = await call("register_investigation_input", reg)
        if policy == "embargoed":
            assert registered["isError"]
            assert (
                registered["structuredContent"]["category"] == "input_scope_violation"
            )
        else:
            assert not registered["isError"]
            grant = registered["structuredContent"]
            assert grant["private_tainted"] and grant["artifact"] is None
            grants.append(grant)
    grant = grants[0]
    session = {"session_id": grant["session_id"]}
    # A locally admissible protected plan still cannot run over MCP.
    frozen = await call("freeze_analysis_plan", _audit_plan(grant))
    plan = frozen["structuredContent"]
    assert not frozen["isError"] and plan["status"] == "local_studio_only"
    assert "--offline-only" in plan["launch_hint"]
    assert (
        await call(
            "submit_reviewed_run",
            {k: plan[k] for k in ("session_id", "plan_id", "plan_sha256")},
        )
    )["isError"]
    state = await call("list_investigation_state", session)
    assert state["structuredContent"]["redacted"]
    opaque = grant["opaque_artifact"]
    run = {**session, "run_id": "a" * 32}
    requests = {
        "query_artifact_table": {
            **session,
            "artifact_id": opaque["artifact_id"],
            "artifact_sha256": opaque["sha256"],
            "columns": [secret],
        },
        "check_answerability": {
            **session,
            "question_text": secret,
            "assay": "bulk_rna_seq",
            "entry_point": "count_matrix",
        },
        "get_run_receipt": run,
        "get_run_status": run,
        "cancel_run": {**run, "reason": secret},
    }
    for name, args in requests.items():
        assert (await call(name, args))["isError"]
    await call("list_kernels_and_methods", session)
    await call("manage_research_aim", {**session, "action": "list"})
    runtime = await call(
        "inspect_runtime_capabilities",
        {"include_images": False, "include_packages": False},
    )
    assert not runtime["isError"]
    resources = (await _rpc(wire, "resources/list", {}))["resources"]
    assert len(resources) == 3
    for resource in resources:
        result = await _rpc(wire, "resources/read", {"uri": resource["uri"]})
        item = result["contents"][0]
        assert item["mimeType"] == "text/html;profile=mcp-app" and item["text"]


def test_protected_sentinels_across_mcp_http_and_stderr(
    workspace_tmp_path, monkeypatch
):
    opened = workspace_tmp_path / "opened-browser"
    daemon_errors = workspace_tmp_path / "daemon-stderr"
    _observe_daemon(monkeypatch, opened, daemon_errors)
    with _daemon(workspace_tmp_path, enable_streamable_http=True) as (
        workspace,
        inputs,
        daemon,
    ):
        tokens, entries = _plant(inputs, workspace)
        raw = bytearray()
        errors = workspace_tmp_path / "stderr.log"

        async def run():
            with errors.open("wb") as log:
                async with _wire(workspace, inputs, raw, log, opened) as wire:
                    await _probe(wire, entries, tokens[0])

        asyncio.run(run())
        assert not opened.exists()
        bearer = {"Authorization": "Bearer " + read_mcp_token(workspace)}
        for path in ("/mcp", "/api/config"):
            for headers, expected in (
                ({}, 401),
                ({**bearer, "Origin": "https://other.invalid"}, 403),
                ({**bearer, "Host": "other.invalid"}, 403),
            ):
                status, network_headers, content = _http(
                    daemon["http_url"],
                    path,
                    headers=headers,
                    method="POST" if path == "/mcp" else "GET",
                    body=json.dumps({"probe": tokens[0]}),
                )
                assert status == expected
                raw.extend(json.dumps(network_headers).encode())
                raw.extend(content)
        raw.extend(errors.read_bytes())
    raw.extend(daemon_errors.read_bytes())
    assert not opened.exists()
    for token in tokens:
        assert token.encode() not in raw
