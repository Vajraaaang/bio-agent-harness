"""ADR-19 tests of protocol custody, plan identity, and admission decisions (L1)."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest

from devbench.daemon import auth
from devbench.daemon.supervisor import Supervisor
from devbench.engine.method_executor import EXPLORATORY_IMAGE_ID
from devbench.engine.scientific_authority import _LEGACY_APPROVAL_SQL, check_schema
from devbench.engine.workflow_operations import OperationBlocked
from devbench.mcp import approval, planning
from devbench.mcp.context import ControlPlane
from devbench.platform.plugin_jobs import JobStore, digest, require_execution_policy

pytestmark = pytest.mark.wal


@pytest.fixture
def control(workspace_tmp_path):
    root = workspace_tmp_path
    inputs, workspace = root / "inputs", root / "workspace"
    inputs.mkdir(mode=0o700)
    jobs = JobStore(workspace)
    supervisor = Supervisor(workspace, offline_only=True)
    ctx = ControlPlane(
        SimpleNamespace(jobs=jobs, input_root=inputs, mounted_image_id=None),
        supervisor,
        {"offline_only": True},
    )
    path = inputs / "counts.csv"
    raw = b"gene,one,two\na,1,2\n"
    path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    ref = {
        "artifact_id": "counts-" + sha,
        "sha256": sha,
        "kind": "expression_matrix",
        "format": "csv",
        "representation_class": "count_1_raw_counts",
        "size_bytes": len(raw),
        "claim_tier": "none",
    }
    rec = ctx.admit(None, None, ref, path, "synthetic", {"count_1_verified": True})
    ctx.sid = rec["session_id"]
    ctx.binding = {"artifact_id": ref["artifact_id"], "sha256": sha}
    try:
        yield ctx
    finally:
        supervisor.close()


def _args(ctx, *, script=True):
    base = {
        "session_id": ctx.sid,
        "branch_id": "primary",
        "objective": "Inspect the synthetic specimen inventory",
        "plan_kind": "tier2_exploratory_code" if script else "tier1_kernel",
        "epistemic_purpose": "exploratory" if script else "qc_diagnostic",
        "input_bindings": {"expression": ctx.binding},
    }
    if script:
        base["exploratory_code"] = {
            "source_code": "print(123)",
            "language": "python",
            "base_image_id": EXPLORATORY_IMAGE_ID,
            "expected_outputs": [],
        }
    else:
        base["kernel"] = {"kernel_id": "matrix_audit", "version": "1", "parameters": {}}
    return base


def _frozen(ctx, args):
    summary = planning.freeze_analysis_plan(ctx, args)
    ident = {k: summary[k] for k in ("session_id", "plan_id", "plan_sha256")}
    return summary, ident, ctx.jobs.plan(ident["session_id"], ident["plan_id"])


def _admit(ctx, plan, sha, **overrides):
    require_execution_policy(
        ctx.authority, plan, sha, False, **{"offline_only": True, **overrides}
    )


def _observed(ctx):
    stage = ctx.workspace / "prepared"
    stage.mkdir(mode=0o700)
    (stage / "inputs").mkdir(mode=0o700)
    (stage / "outputs").mkdir(mode=0o700)
    if os.getuid() == 0:
        os.chown(stage / "outputs", 65532, 65532)
    return {
        "stage": str(stage),
        "document": {
            "Image": EXPLORATORY_IMAGE_ID,
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(stage / "inputs"),
                    "Destination": "/inputs",
                    "RW": False,
                },
                {
                    "Type": "bind",
                    "Source": str(stage / "outputs"),
                    "Destination": "/outputs",
                    "RW": True,
                },
            ],
        },
    }


def test_owner_only_startup_policy_is_content_addressed_and_out_of_band(
    workspace_tmp_path,
):
    workspace = workspace_tmp_path / "workspace"
    with auth.state_directory(workspace, create=True):
        pass
    relative = workspace / ".devbench-daemon" / "policy.json"
    (workspace / "policy.json").write_text('{"auto_approve_tier2_public":true}')
    assert approval.load_policy(workspace) is None
    relative.write_bytes(b'{"auto_approve_tier2_public":true}\n')
    for mode in (0o666, 0o640, 0o644, 0o400):
        relative.chmod(mode)
        assert approval.load_policy(workspace) is None
    relative.chmod(0o600)
    assert (
        approval.load_policy(workspace)
        == hashlib.sha256(relative.read_bytes()).hexdigest()
    )
    relative.unlink()
    relative.symlink_to(workspace / "policy.json")
    assert approval.load_policy(workspace) is None
    relative.unlink()
    cli = approval.load_policy(workspace, cli_enabled=True)
    assert (
        cli
        == hashlib.sha256(
            (relative.parent / "launch-policy.json").read_bytes()
        ).hexdigest()
    )
    assert approval.load_policy(workspace) is None


def test_default_grant_binds_immutable_script_bytes_and_exact_plan(control):
    ctx = control
    summary, ident, plan = _frozen(ctx, _args(ctx))
    assert summary["approval_required"] == "codex_native"
    assert plan["script_sha256"] == hashlib.sha256(b"print(123)").hexdigest()
    assert ctx.authority.plan_approval(**ident) is None
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        _admit(ctx, plan, ident["plan_sha256"])
    grant = ctx.jobs.approve(
        *ident.values(),
        actor_id="local-operator",
        surface="cli",
        auth_method="local_os_user",
        require_public=True,
    )
    assert (
        grant["script_sha256"] == plan["script_sha256"]
        and grant["policy_sha256"] is None
    )
    _admit(ctx, plan, ident["plan_sha256"])
    _, ident2, another = _frozen(ctx, _args(ctx))
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        _admit(ctx, another, ident2["plan_sha256"])


def test_path_snapshot_checks_every_component_and_utf8_byte_limit(control):
    ctx = control
    sub = ctx.workspace / "script-folder"
    sub.mkdir(mode=0o700)
    file = sub / "trial.py"
    raw = b"#" + b"x" * (512 * 1024 - 1)
    file.write_bytes(raw)
    args = _args(ctx)
    del args["exploratory_code"]["source_code"]
    args["exploratory_code"]["script_path"] = "script-folder/trial.py"
    _, ident, plan = _frozen(ctx, args)
    file.write_text("pass")
    assert plan["script_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (
        ctx.jobs.plan(ident["session_id"], ident["plan_id"])["exploratory_code"][
            "source_code"
        ].encode()
        == raw
    )
    (ctx.workspace / "linked").symlink_to(sub, target_is_directory=True)
    (sub / "alias.py").symlink_to(file)
    for path in ("../trial.py", "linked/trial.py", "script-folder/alias.py", str(file)):
        args["exploratory_code"]["script_path"] = path
        with pytest.raises(OperationBlocked, match="input_scope_violation"):
            planning.freeze_analysis_plan(ctx, args)
    args = _args(ctx)
    args["exploratory_code"]["source_code"] = "é" * (256 * 1024 + 1)
    with pytest.raises(OperationBlocked, match="resource_quota_exceeded"):
        planning.freeze_analysis_plan(ctx, args)


@pytest.mark.parametrize(
    "key,value",
    [
        ("NetworkMode", "bridge"),
        ("ReadonlyRootfs", False),
        ("CapDrop", []),
        ("SecurityOpt", []),
        ("Privileged", True),
        ("CapAdd", ["SYS_ADMIN"]),
    ],
)
def test_auto_requires_observed_isolation_flags(control, key, value):
    ctx = control
    _, ident, plan = _frozen(ctx, _args(ctx))
    observation = _observed(ctx)
    observation["document"]["HostConfig"][key] = value
    assert not approval.auto_grant(
        ctx.jobs, plan, ident["plan_sha256"], "a" * 64, lambda *_: observation
    )
    assert ctx.authority.plan_approval(**ident) is None


def test_auto_requires_exact_mount_inventory_and_all_governance_gates(control):
    ctx = control
    _, ident, plan = _frozen(ctx, _args(ctx))
    observation = _observed(ctx)
    for mounts in (
        [],
        observation["document"]["Mounts"] * 2,
        [{**x, "RW": True} for x in observation["document"]["Mounts"]],
    ):
        changed = deepcopy(observation)
        changed["document"]["Mounts"] = mounts
        assert not approval.auto_grant(
            ctx.jobs,
            plan,
            ident["plan_sha256"],
            "a" * 64,
            lambda *_, value=changed: value,
        )
    for name, value in (
        ("data_classification", "clinical"),
        ("epistemic_purpose", "confirmatory"),
        ("claim_tier", "L2_statistically_calibrated"),
        ("exploratory_only", False),
    ):
        assert not approval.auto_grant(
            ctx.jobs,
            {**plan, name: value},
            ident["plan_sha256"],
            "a" * 64,
            lambda *_: observation,
        )
    assert ctx.authority.plan_approval(**ident) is None
    assert approval.auto_grant(
        ctx.jobs, plan, ident["plan_sha256"], "a" * 64, lambda *_: observation
    )
    grant = ctx.authority.plan_approval(**ident)
    assert (grant["auth_method"], grant["script_sha256"], grant["policy_sha256"]) == (
        approval.AUTO_METHOD,
        plan["script_sha256"],
        "a" * 64,
    )
    _admit(ctx, plan, ident["plan_sha256"], policy_sha="a" * 64)
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        _admit(ctx, plan, ident["plan_sha256"], policy_sha="b" * 64)
    with ctx.authority.daemon_transaction() as db:
        db.execute(
            "INSERT INTO mcp_private_digests VALUES (?)", (ctx.binding["sha256"],)
        )
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        _admit(ctx, plan, ident["plan_sha256"], policy_sha="a" * 64)
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        _admit(ctx, plan, ident["plan_sha256"], offline_only=False, policy_sha="a" * 64)
    grant = ctx.jobs.approve(
        *ident.values(),
        actor_id="offline-human",
        surface="cli",
        auth_method="local_os_user",
        offline_approval=True,
    )
    assert grant["offline_approval"] == 1
    _admit(ctx, plan, ident["plan_sha256"])


def test_partition_embargo_prevents_even_manual_public_disclosure(control):
    ctx = control
    _, ident, plan = _frozen(ctx, _args(ctx))
    with ctx.authority.daemon_transaction() as db:
        state = {"sha256": ctx.binding["sha256"]}
        ctx.authority._append_execution_record(
            db,
            ctx.sid,
            {
                "kind": "execution_event",
                "record_id": "embargo-" + ctx.binding["sha256"],
                "event": "protected_artifact",
                "state": state,
                "state_sha256": digest(state),
            },
        )
    with pytest.raises(OperationBlocked, match="input_scope_violation"):
        approval.auto_grant(
            ctx.jobs, plan, ident["plan_sha256"], "a" * 64, lambda *_: None
        )


def test_public_diagnostic_and_independent_confirmatory_gate(control):
    ctx = control
    summary, ident, plan = _frozen(ctx, _args(ctx, script=False))
    assert summary["approval_required"] == "none"
    _admit(ctx, plan, ident["plan_sha256"])
    args = {**_args(ctx, script=False), "epistemic_purpose": "confirmatory"}
    _, ident, plan = _frozen(ctx, args)
    ctx.jobs.approve(
        *ident.values(),
        actor_id="operator",
        surface="cli",
        auth_method="local_os_user",
        require_public=True,
    )
    with pytest.raises(OperationBlocked, match="outcome_blind_critic_required"):
        _admit(ctx, plan, ident["plan_sha256"])
    packet = approval.gate1_subject(plan)
    assert "results" not in packet and packet["plan_sha256"] == ident["plan_sha256"]
    common = {
        "subject_sha256": digest(packet),
        "rationale": "Reviewed preregistered units.",
        "status": "passed",
        "offline": False,
    }
    with pytest.raises(OperationBlocked, match="critic_review_subject_mismatch"):
        approval.record_gate1(ctx.jobs, ident, reviewer=plan["author_id"], **common)
    approval.record_gate1(ctx.jobs, ident, reviewer="operator", **common)
    with pytest.raises(OperationBlocked, match="outcome_blind_critic_required"):
        _admit(ctx, plan, ident["plan_sha256"])
    approval.record_gate1(ctx.jobs, ident, reviewer="independent", **common)
    _admit(ctx, plan, ident["plan_sha256"])


def test_native_confirmation_is_one_time_bound_and_cannot_authorize_private(control):
    ctx = control
    with auth.state_directory(ctx.workspace, create=True) as directory:
        auth.write_file(directory, "daemon.token", b"a" * 64)
    _, ident, plan = _frozen(ctx, _args(ctx))
    token = approval.issue_native_confirmation(ctx.jobs, ident, "human-native")
    for invalid in ("yes", token[:-12], auth.read_token(ctx.workspace), "x" * 43):
        with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
            approval.accept_native_confirmation(ctx, ident, invalid)
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        approval.accept_native_confirmation(ctx, {**ident, "plan_id": "another"}, token)
    _, other_ident, _ = _frozen(ctx, _args(ctx))
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        approval.accept_native_confirmation(ctx, other_ident, token)
    assert ctx.authority.plan_approval(**other_ident) is None
    approval.accept_native_confirmation(ctx, ident, token)
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        approval.accept_native_confirmation(ctx, ident, token)
    assert ctx.authority.plan_approval(**ident)["surface"] == "codex_native"
    token = approval.issue_native_confirmation(ctx.jobs, ident, "human-native")
    _admit(ctx, plan, ident["plan_sha256"])
    with ctx.authority.daemon_transaction() as db:
        db.execute(
            "UPDATE mcp_sessions SET classification='unclassified' WHERE session_id=?",
            (ctx.sid,),
        )
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        approval.accept_native_confirmation(ctx, ident, token)
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        _admit(ctx, plan, ident["plan_sha256"])


def test_historical_grant_table_remains_readable_and_migrates_atomically(control):
    authority = control.authority
    with sqlite3.connect(authority.ledger.path) as db:
        db.execute("DROP TABLE scientific_plan_approvals")
        db.execute(_LEGACY_APPROVAL_SQL)
        db.execute(
            "INSERT INTO scientific_plan_approvals VALUES(?,?,?,?,?,?,?,?)",
            ("s", "p", "a" * 64, "operator", "cli", "local_uid", "2026-11-25", "prior"),
        )
        assert check_schema(db)
    assert authority.plan_approval("s", "p", "a" * 64)["rationale"] == "prior"
    authority.initialize_daemon()
    old = authority.plan_approval("s", "p", "a" * 64)
    assert old["policy_sha256"] is None and old["script_sha256"] is None


def test_reviewed_submit_and_cancel_report_existing_queue_without_read_writes(
    control, monkeypatch
):
    from devbench.mcp import runtime
    from devbench.mcp.receipts import get_run_receipt

    monkeypatch.setattr(
        runtime,
        "available_images",
        lambda _, **kwargs: [{"image_id": EXPLORATORY_IMAGE_ID}],
    )
    ctx = control
    _, ident, plan = _frozen(ctx, _args(ctx))
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        planning.submit_reviewed_run(ctx, ident)
    ctx.jobs.approve(
        *ident.values(),
        actor_id="operator",
        surface="cli",
        auth_method="local_os_user",
        require_public=True,
    )
    started = planning.submit_reviewed_run(ctx, ident)
    key = {"session_id": ctx.sid, "run_id": started["run_id"]}
    assert planning.submit_reviewed_run(ctx, ident)["run_id"] == key["run_id"]

    def snapshot():
        return ctx.authority.daemon_read(
            lambda db: [
                tuple(x)
                for x in db.execute(
                    "SELECT status,updated_at FROM scientific_daemon_runs"
                )
            ]
        )

    before = snapshot()
    assert planning.get_run_status(ctx, key)["status"] == "queued"
    assert before == snapshot()
    cancelled = planning.cancel_run(
        ctx, {**key, "reason": "Synthetic cancellation check"}
    )
    assert cancelled["status"] == "cancelled"
    ctx.supervisor.cancel_run(
        key["run_id"]
    )  # Wait for the independent cleanup verifier.
    assert planning.cancel_run(ctx, {**key, "reason": "Check terminal state"}) == {
        **key,
        "status": "already_terminal",
        "cleanup_state": "confirmed",
        "checkpoint_preserved": False,
    }
    row = get_run_receipt(ctx, key)
    assert row["status"] == "cancelled" and row["claim_tier"] == "none"
    assert row["clinical_release_allowed"] is False
    assert len(row["replay_bundle_sha256"]) == 64


def test_daemon_literature_provider_and_startup_flag_are_operator_scoped(
    workspace_tmp_path,
):
    from devbench.daemon import ipc
    from tests.test_research_daemon import _cli

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    try:
        result = _cli(
            "daemon",
            "--auto-approve-tier2-public",
            "--workspace",
            workspace,
            "--input-root",
            inputs,
            "--literature-provider",
            "europe_pmc",
        )
        assert result.returncode == 0
        owner = ipc.daemon_status(workspace)
        assert (
            owner["auto_approve_tier2_public"]
            and owner["literature_provider"] == "europe_pmc"
        )
        assert owner["enable_streamable_http"] is False
        with pytest.raises(ipc.DaemonError):
            ipc.request(workspace, "configure_policy", auto_approve_tier2_public=False)
        assert (
            not _cli(
                "daemon",
                "start",
                "--workspace",
                workspace,
                "--literature-provider",
                "network",
                check=False,
            ).returncode
            == 0
        )
    finally:
        ipc.stop_daemon(workspace)


def test_legacy_exploratory_worker_requires_the_reviewed_script_digest(control):
    ctx = control
    legacy = {
        "session_id": ctx.sid,
        "plan_id": "1" * 32,
        "operation": "registered_exploratory",
        "data_classification": "synthetic",
        "script_sha256": "b" * 64,
    }
    pid, sha = ctx.jobs.freeze(legacy)
    with pytest.raises(OperationBlocked, match="explicit_plan_approval_required"):
        _admit(ctx, legacy, sha)
    ctx.jobs.approve(
        ctx.sid,
        pid,
        sha,
        actor_id="reviewer",
        surface="cli",
        auth_method="local_os_user",
        require_public=True,
    )
    _admit(ctx, legacy, sha)
    del legacy["script_sha256"]
    with pytest.raises(OperationBlocked, match="registered_method_replan_required"):
        _admit(ctx, legacy, digest(legacy))


def test_real_stdio_accepts_exact_512kib_utf8_and_snapshots_multiple_steps(
    workspace_tmp_path,
):
    import asyncio
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from devbench.daemon import ipc

    workspace, inputs = workspace_tmp_path / "workspace", workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    source = inputs / "study.csv"
    source.write_text("condition,name\none,a\n")
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "devbench.platform.cli",
            "mcp",
            "--workspace",
            str(workspace),
            "--input-root",
            str(inputs),
            "--no-auto-browser",
        ],
        env={
            k: v
            for k, v in os.environ.items()
            if k in {"DYLD_LIBRARY_PATH", "PATH", "PYTHONPATH"}
        },
    )
    code = "# " + "é" * (256 * 1024 - 1)
    assert len(code.encode()) == 512 * 1024

    async def roundtrip():
        async with stdio_client(params) as io, ClientSession(*io) as client:
            await client.initialize()
            registration = await client.call_tool(
                "register_investigation_input",
                {
                    "input": {
                        "path": str(source),
                        "file_kind": "metadata",
                        "investigation_role": "primary_cohort",
                        "data_classification": "synthetic",
                    },
                    "representation_class": "metadata_only",
                    "provenance_declaration": {
                        "source": "fixture",
                        "origin": "user_declaration",
                    },
                },
            )
            assert not registration.isError, registration
            sid = registration.structuredContent["session_id"]
            binding = registration.structuredContent["artifact"]
            program = {
                "source_code": code,
                "language": "python",
                "base_image_id": EXPLORATORY_IMAGE_ID,
                "expected_outputs": [],
            }
            response = await client.call_tool(
                "freeze_analysis_plan",
                {
                    "session_id": sid,
                    "branch_id": "primary",
                    "epistemic_purpose": "exploratory",
                    "objective": "Freeze a synthetic UTF-8 script",
                    "plan_kind": "tier2_exploratory_code",
                    "exploratory_code": program,
                    "input_bindings": {
                        "meta": {k: binding[k] for k in ("artifact_id", "sha256")}
                    },
                    "graph_steps": [
                        {
                            "step_key": "repeat",
                            "plan_kind": "tier2_exploratory_code",
                            "exploratory_code": program,
                        }
                    ],
                },
            )
            assert not response.isError, response
            reply = response.structuredContent
            assert (
                len(reply["plan_id"]) == 32
                and reply["approval_required"] == "codex_native"
            )
            saved = JobStore(workspace).plan(sid, reply["plan_id"])
            assert (
                saved["exploratory_code"]["source_code"]
                == saved["graph_steps"][0]["exploratory_code"]["source_code"]
                == code
            )
            assert saved["script_sha256"] == digest(
                [hashlib.sha256(code.encode()).hexdigest()] * 2
            )

    try:
        asyncio.run(roundtrip())
    finally:
        ipc.stop_daemon(workspace)


@pytest.mark.parametrize(
    "private", [False, True], ids=["policy-public", "offline-private"]
)
def test_real_isolated_script_uses_only_the_authorized_local_surface(control, private):
    import json
    import time

    from devbench.daemon import ipc
    from devbench.mcp.receipts import get_run_receipt
    from devbench.mcp.runtime import available_images
    from tests.test_research_daemon import _cli, _stop_when_idle

    ctx = control
    if EXPLORATORY_IMAGE_ID not in {i["image_id"] for i in available_images(ctx)}:
        pytest.skip("Pinned local exploratory OCI image is unavailable")
    if private:
        current = ctx.artifact(ctx.sid, ctx.binding["artifact_id"])
        ctx.admit(
            ctx.sid,
            ctx.session(ctx.sid)["revision"],
            current["ref"],
            current["path"],
            "private",
            current["detail"],
        )
    args = _args(ctx)
    args["exploratory_code"].update(
        source_code=(
            "from pathlib import Path\n"
            "Path('/outputs/info.json').write_text('{\"n\":1}')"
        ),
        expected_outputs=["info.json"],
    )
    try:
        ipc.start_daemon(
            ctx.workspace,
            input_root=ctx.input_root,
            offline_only=True,
            auto_approve_tier2_public=True,
        )
        _, ident, _ = _frozen(ctx, args)
        if private:
            with pytest.raises(
                OperationBlocked, match="private_data_local_studio_only"
            ):
                planning.submit_reviewed_run(ctx, ident)
            local = _cli(
                "approve",
                "--offline-only",
                "--submit",
                "--actor",
                "local-scientist",
                "--workspace",
                ctx.workspace,
                "--session-id",
                ctx.sid,
                "--plan-id",
                ident["plan_id"],
                "--plan-sha256",
                ident["plan_sha256"],
            )
            row = json.loads(local.stdout)
        else:
            row = ipc.request(ctx.workspace, "submit", **ident)
        key = {"session_id": ctx.sid, "run_id": row["run_id"]}
        end = time.monotonic() + 45
        while time.monotonic() < end:
            status = planning.get_run_status(ctx, key)["status"]
            if status not in {"queued", "running"}:
                break
            time.sleep(0.075)
        assert status == "completed", ctx.jobs.managed_run(row["run_id"])["error_text"]
        grant = ctx.authority.plan_approval(**ident)
        if private:
            assert grant["surface"] == "cli" and grant["offline_approval"]
            with pytest.raises(
                OperationBlocked, match="private_data_local_studio_only"
            ):
                get_run_receipt(ctx, key)
        else:
            assert grant["auth_method"] == approval.AUTO_METHOD
            result = get_run_receipt(ctx, key)
            assert result["claim_tier"] == "L1_execution_verified"
            assert result["clinical_release_allowed"] is False
            assert result["outputs"]["info"]["kind"] == "table"
    finally:
        _stop_when_idle(ctx.workspace)


def test_registered_private_digest_blocks_preexisting_plugin_paths_before_freeze(
    control,
):
    from contextlib import closing

    from devbench.platform.plugin_mcp import Input, Inspect, Plugin

    ctx = control
    record = ctx.artifact(ctx.sid, ctx.binding["artifact_id"])
    ctx.admit(
        ctx.sid,
        ctx.session(ctx.sid)["revision"],
        record["ref"],
        record["path"],
        "clinical",
        record["detail"],
    )
    with closing(ctx.jobs._connect(readonly=True)) as legacy:
        assert legacy.execute("SELECT count(*) FROM plugin_plans").fetchone()[0] == 0
        assert JobStore.protected_inputs(
            legacy,
            {
                "session_id": "f" * 32,
                "data_classification": "public",
                "profiles": [{"sha256": ctx.binding["sha256"]}],
            },
        )
    assert ctx.jobs.has_private_input({ctx.binding["sha256"]})
    old = Plugin(ctx.workspace, ctx.input_root, None)
    with pytest.raises(OperationBlocked, match="private_data_local_studio_only"):
        old.inspect(
            Inspect(inputs=[Input(path=record["path"], data_classification="public")])
        )


def test_mcp_only_credential_has_no_studio_session_or_approval_capability(
    workspace_tmp_path,
):
    import json
    import stat

    from tests.test_research_daemon import _daemon, _http

    with _daemon(workspace_tmp_path, enable_streamable_http=True) as (
        workspace,
        _,
        owner,
    ):
        origin = owner["http_url"].rstrip("/")
        token = auth.read_mcp_token(workspace)
        studio = auth.read_token(workspace)
        assert not token == studio and len(token) == len(studio) == 64
        assert (
            stat.S_IMODE((workspace / ".devbench-daemon/mcp.token").stat().st_mode)
            == 0o600
        )
        headers = {"Authorization": "Bearer " + token, "Origin": origin}
        for route in (
            "/api/studio/session",
            "/api/approve",
            "/api/scientific/adaptive/x/plugin-execute",
        ):
            code, *_ = _http(
                origin,
                route,
                method="POST",
                headers=headers,
                body=None if route.endswith("/session") else json.dumps({}),
            )
            assert code == 401
        assert (
            _http(
                origin,
                "/mcp",
                method="POST",
                headers={"Authorization": "Bearer " + studio, "Origin": origin},
            )[0]
            == 401
        )
    assert not (workspace / ".devbench-daemon/mcp.token").exists()


@pytest.mark.parametrize(
    "update,reason",
    [
        ({"source_code": "def f("}, "invalid_request"),
        ({"source_code": "open('/proc/1/status')"}, "input_scope_violation"),
        ({"expected_outputs": [{"path": "../escape.json"}]}, "invalid_request"),
        (
            {
                "expected_outputs": [
                    {"path": "result.json", "claim_tier": "L3_scientifically_supported"}
                ]
            },
            "invalid_request",
        ),
        ({"expected_outputs": [{"path": "figure.svg"}]}, "invalid_request"),
    ],
)
def test_static_preflight_refuses_invalid_plans_before_persistence(
    control, update, reason
):
    ctx = control
    baseline = ctx.session(ctx.sid)["revision"]
    args = _args(ctx)
    args["exploratory_code"].update(update)
    with pytest.raises(OperationBlocked, match=reason):
        planning.freeze_analysis_plan(ctx, args)
    assert ctx.session(ctx.sid)["revision"] == baseline


def test_failure_to_publish_studio_plan_rolls_back_wal_and_revision(control):
    from contextlib import closing

    ctx = control
    baseline = ctx.session(ctx.sid)["revision"]
    with closing(ctx.jobs._connect()) as db, db:
        db.execute(
            "CREATE TRIGGER test_failure BEFORE INSERT ON plugin_plans "
            "BEGIN SELECT RAISE(ABORT,'synthetic projection failure'); END"
        )
    with pytest.raises(RuntimeError):
        planning.freeze_analysis_plan(ctx, _args(ctx))
    assert ctx.session(ctx.sid)["revision"] == baseline
    with closing(ctx.jobs._connect(readonly=True)) as db:
        assert db.execute("SELECT count(*) FROM plugin_plans").fetchone()[0] == 0


def test_missing_runtime_returns_a_hold_with_zero_queued_work(control, monkeypatch):
    from devbench.mcp import runtime

    ctx = control
    monkeypatch.setattr(runtime, "available_images", lambda _, **kwargs: [])
    _, ident, _ = _frozen(ctx, _args(ctx, script=False))
    with pytest.raises(OperationBlocked, match="runtime_prerequisite_missing"):
        planning.submit_reviewed_run(ctx, ident)
    count = ctx.authority.daemon_read(
        lambda db: db.execute("SELECT count(*) FROM scientific_daemon_runs").fetchone()[
            0
        ]
    )
    assert count == 0


@pytest.mark.parametrize("key", ["root", "../outside", "a/b", ".hidden", "a" * 129])
def test_graph_step_keys_are_unique_scoped_work_units(control, key):
    args = _args(control)
    args["graph_steps"] = [
        {
            "step_key": key,
            "plan_kind": args["plan_kind"],
            "exploratory_code": args["exploratory_code"],
        }
    ]
    revision = control.session(control.sid)["revision"]
    with pytest.raises(OperationBlocked, match="invalid_request"):
        planning.freeze_analysis_plan(control, args)
    assert control.session(control.sid)["revision"] == revision


def test_sweep_validates_each_kernel_parameter_set_before_freeze(control):
    args = _args(control, script=False)
    args["sweep_spec"] = {
        "kind": "parameter_grid",
        "parameter_sets": [{}, {"not_a_parameter": 2}],
    }
    revision = control.session(control.sid)["revision"]
    with pytest.raises(OperationBlocked, match="invalid_request"):
        planning.freeze_analysis_plan(control, args)
    assert control.session(control.sid)["revision"] == revision
