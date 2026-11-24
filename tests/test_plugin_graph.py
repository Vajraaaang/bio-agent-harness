"""Native Plugin computation is accepted only through a verified graph attempt."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.adaptive_workflow import AdaptiveWorkflow
from devbench.platform.plugin_mcp import Input, Plugin, _admit_session, _ledger

pytestmark = pytest.mark.wal


@pytest.fixture
def graph_plugin(workspace_tmp_path, monkeypatch):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    source = inputs / "counts.csv"
    source.write_text("gene,a,b\ng1,1,4\n")
    plugin = Plugin(workspace_tmp_path / "out", inputs, mounted_image_id=None)
    item = Input(path=str(source), data_classification="synthetic", role="expression")
    profiles = plugin._profiles([item])
    monkeypatch.setattr(
        "devbench.platform.plugin_jobs._spawn_worker",
        lambda *_args: SimpleNamespace(pid=os.getpid(), wait=lambda: None),
    )
    return plugin, item, profiles


def _stage(plugin, item, profiles, *, prior=None):
    from devbench.platform.plugin_graph import registered_plugin_runtime
    from devbench.platform.plugin_mcp import _lineage

    plan = {
        "format": "devbench.plugin-plan/1",
        "operation": "matrix_audit",
        "plan_id": uuid4().hex,
        "objective": "Audit nonzero features",
        "data_classification": "synthetic",
        "input_root": str(plugin.input_root),
        "profiles": profiles,
        "input_declarations": [item.model_dump(mode="json")],
        "design": {"measurement": "raw_counts"},
        "runtime": {"target": "local_worker"},
    }
    if prior is None:
        plan["session_id"] = _admit_session(
            plugin.jobs.workspace, plan, plugin.discovery
        )
    else:
        old, digest = prior
        plan["session_id"] = old["session_id"]
        plan["session_intake"] = _lineage(old["session_id"], old)["intake"]
        plan["dependencies"] = [{"plan_id": old["plan_id"], "plan_sha256": digest}]
    plan["graph_runtime"] = registered_plugin_runtime(plan)
    _, digest = plugin.jobs.freeze(plan)
    return plan, digest


def test_native_worker_requires_approval_and_binds_real_graph_receipt(graph_plugin):
    from devbench.platform.plugin_graph import (
        run_registered_plugin_step,
        verify_registered_plugin_result,
    )

    plugin, item, profiles = graph_plugin
    plan, digest = _stage(plugin, item, profiles)
    sid, pid = plan["session_id"], plan["plan_id"]
    calls = []

    def execute():
        calls.append("execute")
        return {"metrics": {"genes": 1}}

    def verify_sources():
        calls.append("verify")

    with pytest.raises(OperationBlocked):
        run_registered_plugin_step(
            plugin.jobs,
            uuid4().hex,
            plan,
            execute=execute,
            verify_sources=verify_sources,
        )
    assert calls == []
    rid = plugin.jobs.launch(sid, pid, digest)["run_id"]
    assert plugin.jobs.start(rid) == plan
    result = run_registered_plugin_step(
        plugin.jobs, rid, plan, execute=execute, verify_sources=verify_sources
    )
    assert (
        calls.count("execute") == 1 and calls[0] == "verify" and calls[-1] == "verify"
    )
    proof = result["graph_execution"]
    flow = AdaptiveWorkflow(_ledger(plugin.jobs.workspace), sid)
    current = flow._read_projection(
        lambda db: flow._state(db).outcome(proof["step_id"])
    )
    assert current["status"] == "succeeded"
    assert current["accepted_record_id"] == proof["accepted_record_id"]
    assert current["receipt_sha256"] == proof["receipt_sha256"]
    assert current["outputs"]["result"]["sha256"] == proof["result_sha256"]
    assert current["consumed"][0]["sha256"] == profiles[0]["sha256"]
    verify_registered_plugin_result(plugin.jobs.workspace, rid, plan, result)
    forged = {**result, "metrics": {"genes": 999}}
    with pytest.raises(OperationBlocked, match="plugin_graph_result_changed"):
        verify_registered_plugin_result(plugin.jobs.workspace, rid, plan, forged)
    retained = (
        plugin.jobs.workspace / "plugin-graph" / proof["attempt_id"] / "result.json"
    )
    raw = retained.read_bytes()
    retained.chmod(0o600)
    retained.write_bytes(raw + b"\n")
    with pytest.raises(OperationBlocked, match="plugin_graph_result_changed"):
        verify_registered_plugin_result(plugin.jobs.workspace, rid, plan, result)


def test_native_worker_links_verified_predecessor_and_reuses_receipt(graph_plugin):
    from devbench.platform.plugin_graph import (
        require_plugin_dependencies,
        run_registered_plugin_step,
    )

    plugin, item, profiles = graph_plugin
    old, old_sha = _stage(plugin, item, profiles)
    nxt, next_sha = _stage(plugin, item, profiles, prior=(old, old_sha))
    with pytest.raises(OperationBlocked, match="plugin_dependency_not_completed"):
        require_plugin_dependencies(plugin.jobs, nxt)

    def run(plan, checksum, marker):
        sid, pid = plan["session_id"], plan["plan_id"]
        rid = plugin.jobs.launch(sid, pid, checksum)["run_id"]
        plugin.jobs.start(rid)
        result = run_registered_plugin_step(
            plugin.jobs,
            rid,
            plan,
            execute=lambda: {"metrics": {"marker": marker}},
            verify_sources=lambda: None,
        )
        assert (
            run_registered_plugin_step(
                plugin.jobs,
                rid,
                plan,
                execute=lambda: pytest.fail("already accepted graph step ran again"),
                verify_sources=lambda: None,
            )
            == result
        )
        plugin.jobs.finish(rid, receipt=result)
        return result

    first = run(old, old_sha, "producer")
    require_plugin_dependencies(plugin.jobs, nxt)
    result = run(nxt, next_sha, "consumer")
    flow = AdaptiveWorkflow(_ledger(plugin.jobs.workspace), old["session_id"])
    state = flow._read_projection(lambda db: flow._state(db))
    outgoing = state.nodes[result["graph_execution"]["step_id"]]
    upstream = [item for item in outgoing.inputs if item.source_port == "result"]
    assert [item.source_id for item in upstream] == [
        first["graph_execution"]["step_id"]
    ]
    pins = state.outcome(outgoing.step_id)["consumed"]
    assert first["graph_execution"]["result_sha256"] in {
        item["sha256"] for item in pins
    }


def test_native_worker_retains_specific_failure_without_accepted_outputs(graph_plugin):
    from devbench.platform.plugin_graph import run_registered_plugin_step

    plugin, item, profiles = graph_plugin
    plan, digest = _stage(plugin, item, profiles)
    sid, pid = plan["session_id"], plan["plan_id"]
    rid = plugin.jobs.launch(sid, pid, digest)["run_id"]
    plugin.jobs.start(rid)

    def execute():
        raise OperationBlocked("missing_scientific_prerequisite")

    with pytest.raises(OperationBlocked, match="missing_scientific_prerequisite"):
        run_registered_plugin_step(
            plugin.jobs,
            rid,
            plan,
            execute=execute,
            verify_sources=lambda: None,
        )
    flow = AdaptiveWorkflow(_ledger(plugin.jobs.workspace), sid)
    outcome = flow._read_projection(lambda db: flow._state(db).outcome("plugin-" + pid))
    assert outcome["status"] == "failed"
    assert outcome["reason"] == "missing_scientific_prerequisite"
    assert outcome["accepted_record_id"] is None and not outcome["outputs"]


def test_studio_revision_race_before_reservation_does_not_lose_run(
    graph_plugin, monkeypatch
):
    from devbench.engine.scientific_authority import ScientificAuthority
    from devbench.platform.plugin_graph import run_registered_plugin_step

    plugin, item, profiles = graph_plugin
    plan, checksum = _stage(plugin, item, profiles)
    sid, pid = plan["session_id"], plan["plan_id"]
    rid = plugin.jobs.launch(sid, pid, checksum)["run_id"]
    plugin.jobs.start(rid)
    original = ScientificAuthority.reserve_attempt
    calls = []

    def conflict_once(authority, *args, **kwargs):
        if not calls:
            calls.append("conflict")
            raise OperationConflict("Scientific revision changed before reservation")
        calls.append("reserved")
        return original(authority, *args, **kwargs)

    def execute():
        calls.append("execute")
        return {"metrics": {"genes": 1}}

    monkeypatch.setattr(ScientificAuthority, "reserve_attempt", conflict_once)
    result = run_registered_plugin_step(
        plugin.jobs,
        rid,
        plan,
        execute=execute,
        verify_sources=lambda: None,
    )
    assert result["graph_execution"]["accepted_record_id"]
    assert calls == ["conflict", "reserved", "execute"]


def test_native_runtime_pin_includes_core_implementation(monkeypatch):
    from devbench.platform import plugin_graph

    plan = {"operation": "bulk_deconvolution", "runtime": {"target": "local_worker"}}
    original = plugin_graph.hash_bounded_file
    before = plugin_graph.registered_plugin_runtime(plan)

    def simulate_edit(path, limit):
        if path.parent.name == "core" and path.name == "bulk_deconvolution.py":
            return "8" * 64
        return original(path, limit)

    monkeypatch.setattr(plugin_graph, "hash_bounded_file", simulate_edit)
    after = plugin_graph.registered_plugin_runtime(plan)
    assert after["code_sha256"] != before["code_sha256"]
    assert after["environment_sha256"] != before["environment_sha256"]
