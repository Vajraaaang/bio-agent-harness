"""Real installed-plugin acceptance for two linked synthetic RNA families."""

import asyncio
import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from devbench.core.scrna_ccc import CellCommunicationSpec
from devbench.platform.plugin_jobs import JobStore, digest
from devbench.platform.rna_execution_coverage import (
    ExecutorBinding,
    ReceiptReadback,
    VerifiedRun,
    execution_inventory,
)
from tests.test_installed_plugin_private_graph import (
    _install_configured_plugin,
    _matched_outcome,
    _offline_studio,
)
from tests.test_perturbation_analysis import _perturbation_inputs
from tests.test_plugin_mcp_new_solvers import _payload
from tests.test_scientific_workspace_http import call

pytestmark = pytest.mark.wal


def _fixture(inputs):
    (counts_path, metadata_path), perturbation, _, _ = _perturbation_inputs(inputs)
    counts = pd.read_csv(counts_path).set_index("cell_id")
    metadata = pd.read_csv(metadata_path).set_index("cell_id")
    sender = np.arange(len(metadata)) % 2 == 0
    metadata["cell_type"] = np.where(sender, "sender", "receiver")
    counts.loc[sender, "gene-4"] += 150
    counts.loc[~sender, "gene-5"] += 150
    # This fixed-depth fixture is unchanged when normalized to its known depth.
    # The exact same bytes are suitable for both registered measurement roles.
    depth = 2500
    padding = depth - counts.sum(axis=1)
    assert (padding > 0).all()
    counts["library_fill"] = padding
    assert (counts.sum(axis=1) == depth).all()
    counts.to_csv(counts_path)
    metadata.to_csv(metadata_path)
    reference = inputs / "ligand-receptor.csv"
    reference.write_text("interaction_id,ligand,receptor\nplanted-pair,gene-4,gene-5\n")

    def declaration(path, role):
        return {"path": str(path), "role": role, "data_classification": "synthetic"}

    declarations = [
        declaration(counts_path, "expression"),
        declaration(metadata_path, "metadata"),
    ]
    sources = [counts_path, metadata_path, reference]
    counts_hash, metadata_hash, reference_hash = (
        sha256(path.read_bytes()).hexdigest() for path in sources
    )
    perturbation = perturbation.model_copy(
        update={"counts_sha256": counts_hash, "metadata_sha256": metadata_hash}
    )
    communication = CellCommunicationSpec(
        expression_sha256=counts_hash,
        metadata_sha256=metadata_hash,
        ligand_receptor_sha256=reference_hash,
        normalization="library_size_normalized",
        mode="within_condition_specificity",
        min_cells_per_donor_type=10,
        n_perm=99,
        seed=35,
    )

    def payload(operation, declarations, spec):
        return {
            "operation": operation,
            "objective": f"Verify synthetic {operation} through the installed plugin",
            "inputs": declarations,
            "parameters": spec.model_dump(mode="json"),
        }

    return (
        payload(
            "scrna_ccc",
            [*declarations, declaration(reference, "reference")],
            communication,
        ),
        payload("perturbation_response", declarations, perturbation),
        sources,
    )


def _approved_plan(plan):
    return {key: plan[key] for key in ("session_id", "plan_id", "plan_sha256")}


async def _rpc(client, method, args, *, blocked=None):
    result = await client.call_tool(method, args)
    assert result.isError is (blocked is not None), result.structuredContent
    if blocked is not None:
        assert result.structuredContent["reason"] == blocked
    return result.structuredContent


async def _complete(client, plan):
    approval = _approved_plan(plan)
    reply = await _rpc(client, "execute_reviewed_plan", approval)
    run_key = {key: reply[key] for key in ("session_id", "run_id")}
    run = reply
    while run["status"] in {"queued", "running"}:
        await asyncio.sleep(0.1)
        run = await _rpc(client, "get_run_status", run_key)
    assert run["status"] == "completed", run
    receipt = await _rpc(client, "get_execution_receipt", run_key)
    return receipt, digest({"approved_request": approval, "launch_reply": reply})


def _readback(port, workspace, plan, receipt):
    sid = plan["session_id"]
    status, raw, _ = call(port, f"/api/scientific/adaptive/{sid}")
    assert status == 200, raw[:1000]
    studio = json.loads(raw)["plugin_session"]
    presented_plan = next(p for p in studio["plans"] if p["plan_id"] == plan["plan_id"])
    presented_run = next(r for r in studio["runs"] if r["run_id"] == receipt["run_id"])
    assert presented_plan["plan_sha256"] == plan["plan_sha256"]
    assert presented_run["status"] == "completed"
    assert presented_run["plan_id"] == presented_plan["plan_id"]
    assert (
        presented_run["operation"]
        == presented_plan["operation"]
        == receipt["operation"]
    )
    assert "graph_execution" in receipt, sorted(receipt)
    assert "graph_execution" in presented_run, (
        sorted(presented_run),
        presented_run.get("warnings"),
    )
    assert presented_run["graph_execution"] == receipt["graph_execution"]
    assert presented_run["summary_tables"] == receipt["summary_tables"]
    outcome, raw, artifact = _verified_material(workspace, sid, presented_run)
    assert artifact == receipt["result_artifact"]
    binding, graph, studio_receipt = _receipt_proofs(
        workspace, plan, presented_plan, presented_run, outcome
    )
    return outcome, raw, binding, graph, studio_receipt


def _verified_material(workspace, sid, presented_run):
    outcome, material = _matched_outcome(workspace, sid, presented_run)
    assert material["summary_tables"] == presented_run["summary_tables"]
    artifact = material["result_artifact"]
    output = workspace / artifact["path"]
    assert output.is_relative_to(workspace / "runs" / presented_run["run_id"])
    raw = output.read_bytes()
    assert len(raw) == artifact["size_bytes"]
    assert (
        sha256(raw).hexdigest()
        == artifact["sha256"]
        == outcome["outputs"]["analysis"]["sha256"]
    )
    return outcome, json.loads(raw), artifact


def _bound_executor(workspace, plan):
    frozen = JobStore(workspace).plan(plan["session_id"], plan["plan_id"])
    assert digest(frozen) == plan["plan_sha256"]
    runtime = frozen["graph_runtime"]
    answer = frozen["answerability"]
    request = answer["request"]
    measurements = {m["kind"] for m in answer["attested_measurements"]}
    route = next(
        route
        for route in execution_inventory()
        if route.family_id == request["family_id"]
        and route.question == request["question"]
        and route.assay == request["assay"]
        and route.entry_point == request["entry_point"]
        and route.operation_id == frozen["operation"]
        and set(route.measurements) <= measurements
    )
    binding = ExecutorBinding(
        route=route,
        operation_id=frozen["operation"],
        version=runtime["format"].rsplit("/", 1)[1],
        runtime_sha256=runtime["environment_sha256"],
        executor_sha256=runtime["code_sha256"],
        input_formats=tuple(
            sorted({p["observed"]["file_type"] for p in frozen["profiles"]})
        ),
    )
    return frozen, binding


def _receipt_proofs(workspace, plan, presented_plan, presented_run, outcome):
    sid = plan["session_id"]
    approved = JobStore(workspace).run(sid, presented_run["run_id"])
    frozen, binding = _bound_executor(workspace, plan)
    assert approved["plan_sha256"] == digest(frozen) == presented_plan["plan_sha256"]
    assert approved["status"] == "completed"
    summary = outcome["outputs"]["result"]
    graph = ReceiptReadback(
        sid,
        frozen["plan_id"],
        digest(frozen),
        f"plugin-{frozen['plan_id']}",
        outcome["attempt_id"],
        outcome["accepted_record_id"],
        frozen["operation"],
        binding.runtime_sha256,
        binding.executor_sha256,
        outcome["receipt_sha256"],
        ((summary["version_id"], summary["sha256"]),),
    )
    proof = presented_run["graph_execution"]
    studio_receipt = ReceiptReadback(
        sid,
        presented_plan["plan_id"],
        presented_plan["plan_sha256"],
        proof["step_id"],
        proof["attempt_id"],
        proof["accepted_record_id"],
        presented_run["operation"],
        binding.runtime_sha256,
        binding.executor_sha256,
        proof["receipt_sha256"],
        ((f"plugin-{proof['attempt_id']}-result", proof["result_sha256"]),),
    )
    assert studio_receipt == graph
    return binding, graph, studio_receipt


def _expect_route(request, workspace, plan):
    gate = request.config.pluginmanager.getplugin("devharness-rna-release")
    if gate is not None:
        _, binding = _bound_executor(workspace, plan)
        gate.expect(binding)


def _record_observed(
    request,
    observed,
    approval,
    installed_sha,
    fixture_sha,
    *,
    resume=None,
    scope="synthetic",
):
    gate = request.config.pluginmanager.getplugin("devharness-rna-release")
    if gate is None:
        return
    binding, graph, studio = observed
    gate.record(
        binding,
        VerifiedRun(
            binding=binding,
            installed_plugin_sha256=installed_sha,
            fixture_sha256=fixture_sha,
            approved_plan_sha256=graph.plan_sha256,
            approval_receipt_sha256=approval,
            graph=graph,
            studio=studio,
            scope=scope,
            dependency_receipts=(resume,) if resume else (),
            resumed_from_receipt_sha256=resume,
        ),
    )


def test_installed_plugin_reconnect_resumes_a_multifamily_graph(
    workspace_tmp_path, request
):
    inputs = workspace_tmp_path / "inputs"
    workspace = workspace_tmp_path / "workspace"
    inputs.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    first_payload, second_payload, inputs_bound = _fixture(inputs)
    settings, manifest_path = _install_configured_plugin(
        workspace_tmp_path, inputs, workspace
    )
    installed_sha = sha256(manifest_path.read_bytes()).hexdigest()
    fixture_sha = digest(
        [sha256(path.read_bytes()).hexdigest() for path in inputs_bound]
    )
    parameters = StdioServerParameters(
        **{**settings, "args": [*settings["args"], "--no-auto-studio"]}
    )

    async def exercise(port):
        origin = f"http://127.0.0.1:{port}"
        async with asyncio.timeout(180):
            async with (
                stdio_client(parameters) as (reader, writer),
                ClientSession(reader, writer) as client,
            ):
                await client.initialize()
                first_plan = await _rpc(client, "plan_rna_workflow", first_payload)
                upstream = _approved_plan(first_plan)
                sid = upstream.pop("session_id")
                second_plan = await _rpc(
                    client,
                    "plan_rna_workflow",
                    {**second_payload, "session_id": sid, "depends_on": [upstream]},
                )
                for plan in (first_plan, second_plan):
                    assert plan["session_id"] == sid
                    assert (
                        plan["studio_url"]
                        == f"{origin}/?session_id={sid}&view=workflows"
                    )
                    _expect_route(request, workspace, plan)
                await _rpc(
                    client,
                    "execute_reviewed_plan",
                    _approved_plan(second_plan),
                    blocked="plugin_dependency_not_completed",
                )
                await _rpc(
                    client,
                    "execute_reviewed_plan",
                    {**_approved_plan(first_plan), "plan_sha256": "0" * 64},
                    blocked="plan_identity_mismatch",
                )
                first_receipt, first_approval = await _complete(client, first_plan)
                producer, communication, *first_read = _readback(
                    port, workspace, first_plan, first_receipt
                )
                assert any(
                    row["interaction_id"] == "planted-pair"
                    for row in communication["interaction_statistics"]
                )

            # A fresh transport must use the accepted dependency and existing run.
            async with (
                stdio_client(parameters) as (reader, writer),
                ClientSession(reader, writer) as client,
            ):
                await client.initialize()
                resumed = await _rpc(
                    client, "execute_reviewed_plan", _approved_plan(first_plan)
                )
                assert resumed["run_id"] == first_receipt["run_id"]
                assert resumed["status"] == "completed"
                second_receipt, second_approval = await _complete(client, second_plan)
                consumer, perturbation, *second_read = _readback(
                    port, workspace, second_plan, second_receipt
                )
                prior = next(
                    pin
                    for pin in consumer["consumed"]
                    if pin["port"] == "prerequisite_0"
                )
                assert (
                    prior["version_id"] == producer["outputs"]["result"]["version_id"]
                )
                assert prior["sha256"] == producer["outputs"]["result"]["sha256"]
                assert perturbation["perturbations"][0]["energy_test"]["distance"] > 0

                _record_observed(
                    request, first_read, first_approval, installed_sha, fixture_sha
                )
                _record_observed(
                    request,
                    second_read,
                    second_approval,
                    installed_sha,
                    fixture_sha,
                    resume=producer["receipt_sha256"],
                )

    with _offline_studio(settings, inputs, workspace) as port:
        asyncio.run(exercise(port))


def test_installed_plugin_projects_existing_native_solvers(workspace_tmp_path, request):
    inputs = workspace_tmp_path / "inputs"
    workspace = workspace_tmp_path / "workspace"
    inputs.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    operations = (
        "scrna_neighborhood_da",
        "bulk_deconvolution",
        "bulk_dtu",
        "spatial_pattern",
        "phenotype_prediction",
    )
    settings, manifest_path = _install_configured_plugin(
        workspace_tmp_path, inputs, workspace
    )
    installed_sha = sha256(manifest_path.read_bytes()).hexdigest()
    parameters = StdioServerParameters(
        **{**settings, "args": [*settings["args"], "--no-auto-studio"]}
    )

    async def exercise(port):
        async with (
            asyncio.timeout(240),
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            for operation in operations:
                payload = _payload(inputs / operation, operation)
                fixture_sha = digest(
                    [
                        sha256(Path(source["path"]).read_bytes()).hexdigest()
                        for source in payload["inputs"]
                    ]
                )
                plan = await _rpc(client, "plan_rna_workflow", payload)
                _expect_route(request, workspace, plan)
                receipt, approval = await _complete(client, plan)
                _, result, *observed = _readback(port, workspace, plan, receipt)
                assert result["clinical_release_allowed"] is False
                _record_observed(
                    request, observed, approval, installed_sha, fixture_sha
                )

    with _offline_studio(settings, inputs, workspace) as port:
        asyncio.run(exercise(port))


def test_installed_plugin_completes_a_registered_private_route(
    workspace_tmp_path, request
):
    inputs = workspace_tmp_path / "inputs"
    workspace = workspace_tmp_path / "workspace"
    inputs.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    payload = _payload(inputs / "private-spatial-assay", "spatial_pattern")
    hashes = []
    for source, parameter in zip(
        payload["inputs"], ("counts_sha256", "metadata_sha256"), strict=True
    ):
        path = Path(source["path"])
        frame = pd.read_csv(path)
        frame["spot_id"] = "offline-" + frame["spot_id"]
        content = frame.to_csv(index=False).encode()
        assert path.read_bytes() != content
        path.write_bytes(content)
        checksum = sha256(content).hexdigest()
        source["data_classification"] = "private"
        payload["parameters"][parameter] = checksum
        hashes.append(checksum)
    settings, manifest_path = _install_configured_plugin(
        workspace_tmp_path, inputs, workspace
    )
    installed_sha = sha256(manifest_path.read_bytes()).hexdigest()
    parameters = StdioServerParameters(
        **{**settings, "args": [*settings["args"], "--no-auto-studio"]}
    )

    async def exercise(port):
        origin = f"http://127.0.0.1:{port}"
        async with (
            asyncio.timeout(180),
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            plan = await _rpc(client, "plan_rna_workflow", payload)
            sid = plan["session_id"]
            assert plan["status"] == "local_studio_only"
            assert plan["execution_route"] == "offline_studio"
            assert plan["studio_url"] == f"{origin}/?session_id={sid}&view=workflows"
            assert all(
                key not in plan for key in ("inputs", "answerability", "metrics")
            )
            _expect_route(request, workspace, plan)
            await _rpc(
                client,
                "execute_reviewed_plan",
                _approved_plan(plan),
                blocked="private_data_local_studio_only",
            )

            api = f"/api/scientific/adaptive/{sid}"
            approval_path = api + "/plugin-execute"
            approval = {key: plan[key] for key in ("plan_id", "plan_sha256")}
            approval["allow_data_execution"] = True

            async def current():
                while True:
                    status, raw, _ = call(port, api)
                    if status == 200:
                        break
                    assert (status, json.loads(raw).get("error")) == (
                        409,
                        "OperationConflict",
                    ), raw[:1000]
                    await asyncio.sleep(0.1)
                (presented,) = json.loads(raw)["private_workflow"]["plans"]
                return presented

            before = await current()
            assert before["status"] == "awaiting_review" and before["can_run"]
            assert (
                call(
                    port,
                    approval_path,
                    origin=origin,
                    body={**approval, "allow_data_execution": False},
                )[0]
                == 403
            )
            bad_plan = {**approval, "plan_sha256": "0" * 64}
            assert call(port, approval_path, origin=origin, body=bad_plan)[0] != 202
            status, raw, _ = call(port, approval_path, origin=origin, body=approval)
            assert status == 202, raw[:1000]
            launched = json.loads(raw)
            while (done := await current())["status"] in {"queued", "running"}:
                await asyncio.sleep(0.1)
            assert done["status"] == "completed", (done["status"], done.get("error"))
            assert done["run_id"] == launched["run_id"]
            assert done["plan_sha256"] == plan["plan_sha256"]
            assert done["operation"] == "spatial_pattern"
            outcome, result, _ = _verified_material(workspace, sid, done)
            observed = _receipt_proofs(workspace, plan, done, done, outcome)
            statistic = next(
                row for row in result["gene_statistics"] if row["gene_id"] == "gradient"
            )
            assert statistic["moran_i"] > 0.7
            assert result["clinical_release_allowed"] is False
            await _rpc(
                client,
                "get_execution_receipt",
                {"session_id": sid, "run_id": done["run_id"]},
                blocked="private_data_local_studio_only",
            )
            _record_observed(
                request,
                observed,
                digest({"approved_request": approval, "launch_reply": launched}),
                installed_sha,
                digest(hashes),
                scope="offline_private",
            )

    with _offline_studio(settings, inputs, workspace) as port:
        asyncio.run(exercise(port))
