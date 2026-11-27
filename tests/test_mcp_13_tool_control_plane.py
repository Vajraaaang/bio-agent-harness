"""Wire, custody, lifecycle and dispatch regression tests for M1-2 (L1)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from devbench.mcp.context import ControlPlane
from devbench.mcp.schemas import validator
from devbench.mcp.server import dispatch
from devbench.platform.plugin_jobs import JobStore, digest
from tests.test_research_daemon import (
    _admission,
    _audit_plan,
    _daemon,
    _rpc,
    _stdio_session,
)

pytestmark = pytest.mark.wal

NAMES = (
    "inspect_runtime_capabilities",
    "inspect_inputs",
    "register_investigation_input",
    "check_answerability",
    "list_kernels_and_methods",
    "list_investigation_state",
    "query_artifact_table",
    "get_run_status",
    "get_run_receipt",
    "manage_research_aim",
    "freeze_analysis_plan",
    "submit_reviewed_run",
    "cancel_run",
)


@pytest.mark.parametrize("ui", [False, True])
def test_stdio_negotiates_exact_thirteen_and_sanitizes_all_errors(
    workspace_tmp_path, ui
):
    async def probe(workspace, inputs, log):
        capabilities = {"extensions": {"io.modelcontextprotocol/ui": {}}} if ui else {}
        async with _stdio_session(
            workspace, inputs, log, capabilities=capabilities
        ) as process:
            tools = (await _rpc(process, "tools/list", {}))["tools"]
            assert [t["name"] for t in tools] == list(NAMES)
            for i, tool in enumerate(tools, 1):
                name = tool["name"]
                assert tool["inputSchema"]["additionalProperties"] is False
                Draft202012Validator.check_schema(tool["inputSchema"])
                Draft202012Validator.check_schema(tool["outputSchema"])
                ann = tool["annotations"]
                assert ann["readOnlyHint"] is (i in {1, 2, 4, 5, 6, 7, 8, 9})
                assert ann["idempotentHint"] is (i not in {10, 11})
                assert ann["openWorldHint"] is ann["destructiveHint"] is False
                assert bool(tool.get("_meta")) is (ui and i in {8, 9, 10, 11, 12, 13})
                secret = "CLINICAL_SENTINEL_NEVER_ECHO"
                packet = await _rpc(
                    process,
                    "tools/call",
                    {"name": name, "arguments": {secret: {secret: secret}}},
                )
                details = packet["structuredContent"]
                assert packet["isError"] and secret not in json.dumps(packet)
                assert json.loads(packet["content"][0]["text"]) == details
                Draft202012Validator(tool["outputSchema"]).validate(details)
            resources = (await _rpc(process, "resources/list", {}))["resources"]
            assert len(resources) == (3 if ui else 0)
            for resource in resources:
                card = await _rpc(process, "resources/read", {"uri": resource["uri"]})
                assert "submit_reviewed_run" not in card["contents"][0]["text"]
            packet = await _rpc(
                process, "tools/call", {"name": NAMES[0], "arguments": {}}
            )
            assert (
                not packet.get("isError")
                and packet["structuredContent"]["sqlite_wal_patched"]
            )
            assert len(json.dumps(packet).encode()) < 65536

    with (
        _daemon(workspace_tmp_path) as (workspace, inputs, _),
        (workspace_tmp_path / "wire.log").open("wb") as log,
    ):
        asyncio.run(probe(workspace, inputs, log))
    assert (
        "CLINICAL_SENTINEL_NEVER_ECHO"
        not in (workspace_tmp_path / "wire.log").read_text()
    )


@pytest.fixture
def control(workspace_tmp_path):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    jobs = JobStore(workspace_tmp_path / "workspace")
    return ControlPlane(
        SimpleNamespace(jobs=jobs, input_root=inputs, mounted_image_id=None),
        config={"offline_only": True},
        studio_url="http://127.0.0.1:4217",
    )


def _ok(ctx, name, arguments):
    packet = dispatch(ctx, name, arguments)
    assert not packet.isError, packet
    result = packet.structuredContent
    assert validator(name, output=True).is_valid(result)
    assert json.loads(packet.content[0].text) == result
    return result


def test_mid_session_taint_retroactively_redacts_aims_receipts_and_aliases(control):
    counts = control.input_root / "counts.csv"
    counts.write_text("gene,s1,s2\na,1,2\n")
    reg = _ok(control, NAMES[2], _admission(counts))
    sid = reg["session_id"]
    secret = "SENTINEL_LOCAL_TEXT"
    aim = {
        "aim_id": secret,
        "kind": "exploratory",
        "question": secret + "?",
        "estimand": "RNA feature count",
    }
    manage = {
        "session_id": sid,
        "action": "propose_aim",
        "expected_revision": reg["revision"],
        "definition": aim,
    }
    saved = _ok(control, NAMES[9], manage)
    for i in range(5):
        saved = _ok(
            control,
            NAMES[9],
            {
                **manage,
                "action": "revise_aim",
                "aim_id": secret,
                "expected_revision": saved["revision"],
                "definition": {**aim, "question": f"{secret} {i}?"},
            },
        )
    stale = dispatch(
        control,
        NAMES[9],
        {
            **manage,
            "action": "fork_branch",
            "parent_branch_id": "primary",
            "branch_id": "other",
            "branch_kind": "sensitivity",
        },
    )
    assert stale.isError
    private = control.input_root / ("patient_" + secret + ".csv")
    private.write_bytes(counts.read_bytes())
    declaration = _admission(private, "public")
    declaration.update(session_id=sid, expected_revision=saved["revision"])
    tainted = _ok(control, NAMES[2], declaration)
    assert tainted["private_tainted"] and tainted["artifact"] is None
    assert set(tainted["opaque_artifact"]) == {"artifact_id", "sha256"}
    listing = _ok(control, NAMES[5], {"session_id": sid})
    assert listing["redacted"] and listing["artifacts"][0]["artifact"] is None
    assert all(
        word not in json.dumps(listing)
        for word in (secret, "schema_preview", "size_bytes", "counts.csv")
    )
    ref = reg["artifact"]
    for name, body in [
        (NAMES[1], {"inputs": [_admission(counts)["input"] | {}]}),
        (
            NAMES[6],
            {
                "session_id": sid,
                "artifact_id": ref["artifact_id"],
                "artifact_sha256": ref["sha256"],
            },
        ),
        (NAMES[8], {"session_id": sid, "run_id": "f" * 32}),
    ]:
        if name == NAMES[1]:
            body["inputs"][0].pop("investigation_role")
        packet = dispatch(control, name, body)
        assert (
            packet.isError
            and packet.structuredContent["category"] == "private_data_local_studio_only"
        )
        assert secret not in packet.model_dump_json()
    assert "session_input_scope_mismatch" not in json.dumps(tainted)


def test_freeze_and_state_read_have_distinct_ledger_effects(control):
    path = control.input_root / "counts.csv"
    path.write_text("gene,s1,s2\na,1,2\n")
    first = _ok(control, NAMES[2], _admission(path))
    sid = first["session_id"]
    second_path = control.input_root / "reference.csv"
    second_path.write_text("gene,set\na,category\n")
    second = _admission(second_path)
    second["input"].update(
        file_kind="reference", investigation_role="gene_set_collection"
    )
    second.update(
        session_id=sid,
        expected_revision=first["revision"],
        representation_class="reference_only",
    )
    joined = _ok(control, NAMES[2], second)
    assert joined["session_id"] == sid and joined["revision"] > first["revision"]
    frozen = _ok(control, NAMES[10], _audit_plan(first))
    assert frozen["status"] == "planned" and frozen["approval_required"] == "none"

    def versions():
        with sqlite3.connect(control.authority.ledger.path) as db:
            return [
                db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in (
                    "scientific_records",
                    "scientific_outbox",
                    "scientific_disclosures",
                    "scientific_daemon_runs",
                )
            ]

    before = versions()
    state = _ok(control, NAMES[5], {"session_id": sid})
    assert (
        len(state["artifacts"]) == 2
        and not state["runs"]
        and state["revision"] > joined["revision"]
    )
    assert versions() == before
    assert control.authority.get(sid, "intake-" + sid)["lineage_id"] == sid
    assert frozen["plan_sha256"] == digest(control.jobs.plan(sid, frozen["plan_id"]))


def test_figure_sidecar_package_and_read_only_receipt_on_real_oci(control):
    import hashlib
    import time

    from devbench.daemon import ipc
    from devbench.engine.method_executor import EXPLORATORY_IMAGE_ID
    from devbench.mcp.planning import get_run_status
    from devbench.mcp.runtime import available_images
    from tests.test_research_daemon import _stop_when_idle

    ctx = control
    if EXPLORATORY_IMAGE_ID not in {item["image_id"] for item in available_images(ctx)}:
        pytest.skip("Pinned exploratory image unavailable")
    matrix = ctx.input_root / "counts.csv"
    matrix.write_text("gene,one,two\na,1,2\n")
    reg = _ok(ctx, NAMES[2], _admission(matrix))
    sid = reg["session_id"]
    sidecar = {
        "axis_labels": {"x": "group", "y": "mean"},
        "sample_count": 2,
        "donor_count": 1,
        "transformations": ["mean"],
        "columns": ["group", "mean"],
        "rows": [["A", 1.5]],
    }
    code = (
        "from pathlib import Path\n"
        "Path('/outputs/chart.svg').write_text('<svg xmlns=\"http://www.w3.org/2000/svg\"/>')\n"
        f"Path('/outputs/points.json').write_text({json.dumps(sidecar)!r})"
    )
    req = _audit_plan(reg)
    del req["kernel"]
    req.update(
        plan_kind="tier2_exploratory_code",
        epistemic_purpose="exploratory",
        objective="Synthetic α plot",
        exploratory_code={
            "language": "python",
            "base_image_id": EXPLORATORY_IMAGE_ID,
            "source_code": code,
            "expected_outputs": [
                {
                    "port": "chart",
                    "path": "chart.svg",
                    "kind": "figure",
                    "sidecar_port": "points",
                },
                {"port": "points", "path": "points.json", "kind": "plot_sidecar"},
            ],
        },
    )
    try:
        ipc.start_daemon(ctx.workspace, input_root=ctx.input_root, offline_only=True)
        frozen = _ok(ctx, NAMES[10], req)
        keys = {k: frozen[k] for k in ("session_id", "plan_id", "plan_sha256")}
        ctx.jobs.approve(
            keys["session_id"],
            keys["plan_id"],
            keys["plan_sha256"],
            actor_id="test-operator",
            surface="cli",
            auth_method="local_os_user",
        )
        run = ipc.request(ctx.workspace, "submit", **keys)
        rid = run["run_id"]
        ids = {"session_id": sid, "run_id": rid}
        end = time.monotonic() + 45
        while time.monotonic() < end:
            state = get_run_status(ctx, ids)
            if state["status"] not in {"running", "queued"}:
                break
            time.sleep(0.1)
        assert state["status"] == "completed", ctx.jobs.managed_run(rid)["error_text"]

        def counts():
            with sqlite3.connect(ctx.authority.ledger.path) as db:
                return tuple(
                    db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                    for table in (
                        "scientific_records",
                        "scientific_outbox",
                        "scientific_disclosures",
                        "scientific_daemon_runs",
                    )
                )

        before = counts()
        start = time.monotonic()
        assert get_run_status(ctx, ids)["checkpoint_available"]
        assert time.monotonic() - start < 0.1
        receipt = _ok(ctx, NAMES[8], ids)
        assert receipt["figure_artifacts"] == [
            {
                "artifact": receipt["outputs"]["chart"],
                "sidecar": receipt["outputs"]["points"],
                "verification_state": "produced_unverified",
            }
        ]
        assert (
            receipt["claim_tier"] == "L1_execution_verified"
            and not receipt["ai_critic_verified"]
        )
        ref = receipt["outputs"]["points"]
        page = _ok(
            ctx,
            NAMES[6],
            {
                **{
                    "session_id": sid,
                    "artifact_id": ref["artifact_id"],
                    "artifact_sha256": ref["sha256"],
                },
                "table_slot": "plot_sidecar",
            },
        )
        assert page["rows"] == [["A", 1.5]]
        assert counts() == before
        pkg = _ok(
            ctx,
            NAMES[9],
            {
                "action": "build_research_package",
                "session_id": sid,
                "expected_revision": ctx.session(sid)["revision"],
                "spec": {"title": "Synthetic α"},
            },
        )
        root = pkg["package_root_sha256"]
        assert (
            hashlib.sha256(
                (
                    ctx.workspace / "research-packages" / sid / (root + ".json")
                ).read_bytes()
            ).hexdigest()
            == root
        )
        evidence = ctx.artifact(sid, ref["artifact_id"])["path"]
        from pathlib import Path

        Path(evidence).chmod(0o600)
        Path(evidence).write_bytes(b"{}")
        assert (
            dispatch(ctx, NAMES[8], ids).structuredContent["category"]
            == "plan_identity_mismatch"
        )
    finally:
        _stop_when_idle(ctx.workspace)


def test_verified_sweep_resumes_only_unfinished_units(control):
    import psutil

    from devbench.daemon import ipc
    from devbench.engine.method_executor import EXPLORATORY_IMAGE_ID
    from devbench.mcp.runtime import available_images
    from tests.test_research_daemon import _stop_when_idle, _until

    ctx = control
    if EXPLORATORY_IMAGE_ID not in {x["image_id"] for x in available_images(ctx)}:
        pytest.skip("Pinned exploratory image unavailable")
    path = ctx.input_root / "matrix.csv"
    path.write_text("gene,a,b\nx,1,2\n")
    admitted = _ok(ctx, NAMES[2], _admission(path))
    request = _audit_plan(admitted)
    del request["kernel"]
    code = (
        "import json,time\n"
        "from pathlib import Path\n"
        "params=json.loads(Path('/inputs/parameters.json').read_bytes())\n"
        "time.sleep(params['seconds'])\n"
        "Path('/outputs/info.json').write_text(json.dumps(params))\n"
    )
    request.update(
        plan_kind="tier2_exploratory_code",
        epistemic_purpose="exploratory",
        exploratory_code={
            "base_image_id": EXPLORATORY_IMAGE_ID,
            "language": "python",
            "source_code": code,
            "parameters": {"seconds": 0},
            "expected_outputs": ["info.json"],
        },
        sweep_spec={
            "kind": "parameter_grid",
            "parameter_sets": [{"seconds": 0}, {"seconds": 3}],
        },
    )
    try:
        ipc.start_daemon(
            ctx.workspace, input_root=ctx.input_root, offline_only=True, grace_sec=0.5
        )
        plan = _ok(ctx, NAMES[10], request)
        keys = {k: plan[k] for k in ("session_id", "plan_id", "plan_sha256")}
        ctx.jobs.approve(
            *keys.values(),
            actor_id="operator",
            surface="cli",
            auth_method="local_os_user",
        )
        original = ipc.request(ctx.workspace, "submit", **keys)["run_id"]

        def partial():
            row = ctx.jobs.managed_run(original)
            return (
                row if row["checkpoint_sha256"] and row["status"] == "running" else None
            )

        row = _until(partial)
        proc = psutil.Process(row["pid"])
        assert abs(proc.create_time() - row["create_time"]) < 0.01
        proc.kill()
        _until(lambda: ctx.jobs.managed_run(original)["status"] == "interrupted")
        retry = ipc.request(
            ctx.workspace, "submit", **keys, resume_from_run_id=original
        )["run_id"]
        assert retry != original

        def terminal():
            r = ctx.jobs.managed_run(retry)
            return r if r["cleanup_state"] == "confirmed" else None

        result = _until(terminal, timeout=45)
        assert result["status"] == "completed", result["error_text"]
        replay = ctx.workspace / "runs" / retry / "control-plane" / "0-root"
        assert (replay / "verified-info").is_file()
        assert not (replay / "stage").exists()
        receipt = _ok(
            ctx, NAMES[8], {"session_id": keys["session_id"], "run_id": retry}
        )
        assert set(receipt["outputs"]) == {"0-root.info", "1-root.info"}
    finally:
        _stop_when_idle(ctx.workspace)
