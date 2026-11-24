"""Exercise installed Plugin bulk DE and tested-background pathways in Studio."""

import asyncio
import csv
import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from devbench.core.rna_pathway_analysis import PathwayResult
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.platform.adaptive_workflow import AdaptiveWorkflow, request_digest
from devbench.platform.plugin_jobs import digest
from devbench.platform.plugin_mcp import _ledger
from devbench.platform.rna_execution_coverage import (
    ExecutorBinding,
    ReceiptReadback,
    VerifiedRun,
    execution_inventory,
)
from tests.test_installed_plugin_private_graph import (
    _install_configured_plugin,
    _offline_studio,
    _projection,
)
from tests.test_scientific_workspace_http import call

pytestmark = pytest.mark.wal


def _bulk_input(inputs):
    rng = np.random.default_rng(4471)
    genes = [f"gene-{index:02d}" for index in range(96)]
    names = [f"sample-{index:02d}" for index in range(12)]
    means = rng.integers(100, 400, size=(96, 1)) * np.ones((1, 12))
    means[:12, 6:] *= 8
    counts = rng.negative_binomial(36, 36 / (36 + means))
    expression, metadata = inputs / "counts.csv", inputs / "design.csv"
    with expression.open("w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["gene", *names])
        out.writerows(
            [gene, *values] for gene, values in zip(genes, counts, strict=True)
        )
    with metadata.open("w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["sample_id", "donor", "condition"])
        out.writerows(
            [name, f"donor-{index:02d}", "A" if index < 6 else "B"]
            for index, name in enumerate(names)
        )
    return genes, [
        {
            "path": str(expression),
            "role": "expression",
            "data_classification": "synthetic",
        },
        {"path": str(metadata), "role": "metadata", "data_classification": "synthetic"},
    ]


async def _rpc(client, name, args, *, blocked=None):
    result = await client.call_tool(name, args)
    assert result.isError is (blocked is not None), result.structuredContent
    if blocked:
        assert result.structuredContent.get("reason") == blocked, (
            result.structuredContent
        )
    return result.structuredContent


async def _execute(client, plan):
    exact = {key: plan[key] for key in ("session_id", "plan_id", "plan_sha256")}
    launch = await _rpc(client, "execute_reviewed_plan", exact)
    key = {key: launch[key] for key in ("session_id", "run_id")}
    current = launch
    while current["status"] in {"queued", "running"}:
        await asyncio.sleep(0.25)
        current = await _rpc(client, "get_run_status", key)
    return key, current, digest({"approved_request": exact, "launch_reply": launch})


async def _review_and_run(port, api):
    view = await _projection(port, api)
    operator = view["operator_bulk"]
    assert operator["preflight"]["rank"] == operator["preflight"]["columns"]
    assert operator["preflight"]["residual_df"] == 10
    assert operator["review"] == ["design"]
    assert operator["run"] is False
    approvals = []
    for touchpoint in ("design", "publication"):
        submitted = {
            "action": "review_operator_bulk",
            "expected_revision": view["revision"],
            "allow_data_execution": True,
            "touchpoint": touchpoint,
            "assessment": {
                "initial": "The fixed synthetic contrast uses independent units.",
                "final": "The synthetic design supports a provisional count fit.",
            },
        }
        status, raw, _ = call(
            port,
            api,
            origin=f"http://127.0.0.1:{port}",
            body=submitted,
        )
        assert status == 200, raw[:900]
        approvals.append((submitted, json.loads(raw)))
        view = await _projection(port, api)
    operator = view["operator_bulk"]
    assert operator["run"] is True
    assert operator["completed_reviews"] == ["design", "publication"]
    submitted = {
        "action": "run_operator_step",
        "expected_revision": view["revision"],
        "allow_data_execution": True,
        "step_id": operator["step_id"],
        "generation": operator["generation"],
    }
    status, raw, _ = call(
        port,
        api,
        origin=f"http://127.0.0.1:{port}",
        body=submitted,
    )
    assert status == 200, raw[:900]
    return digest([*approvals, (submitted, json.loads(raw))])


def _checked_readback(flow, executor, tool_receipt, plan):
    step_id = tool_receipt["step"]

    def read(db):
        state = flow._state(db)
        outcome = state.outcome(step_id)
        assert outcome["status"] == "succeeded"
        assert outcome["receipt_sha256"] == tool_receipt["receipt_sha256"]
        return state.dispatches[outcome["attempt_id"]]["envelope"], outcome

    envelope, outcome = flow._read_projection(read)
    retained = executor.load_receipt(envelope, attempt_id=outcome["attempt_id"])
    assert request_digest(retained.model_dump(mode="json")) == outcome["receipt_sha256"]
    assert retained.request_sha256 == request_digest(envelope)
    materials, artifacts = {}, []
    for port, artifact in sorted(retained.outputs.items()):
        assert artifact == flow.current_use(step_id, port, purpose="query")
        with executor.open_output(artifact) as handle:
            raw = handle.read(4_000_001)
        assert len(raw) <= 4_000_000
        assert sha256(raw).hexdigest() == artifact.sha256
        materials[port] = raw
        artifacts.append((artifact.version_id, artifact.sha256))
    return ReceiptReadback(
        session_id=flow.lineage_id,
        plan_id=plan["plan_id"],
        plan_sha256=plan["plan_sha256"],
        step_id=step_id,
        attempt_id=outcome["attempt_id"],
        accepted_record_id=outcome["accepted_record_id"],
        operation_id=envelope["step"]["operation_id"],
        runtime_sha256=envelope["environment_sha256"],
        executor_sha256=executor.code_sha256,
        receipt_sha256=outcome["receipt_sha256"],
        artifacts=tuple(sorted(artifacts)),
    ), materials


def test_installed_plugin_bulk_de_and_tested_gene_pathway(workspace_tmp_path, request):
    inputs, workspace = (workspace_tmp_path / name for name in ("inputs", "workspace"))
    inputs.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    genes, declarations = _bulk_input(inputs)
    settings, manifest = _install_configured_plugin(
        workspace_tmp_path, inputs, workspace
    )
    if "--mounted-image-id" not in settings["args"]:
        pytest.skip(
            "A native pinned mounted RNA image must already be installed locally"
        )
    parameters = StdioServerParameters(
        **{**settings, "args": [*settings["args"], "--no-auto-studio"]}
    )
    image = settings["args"][settings["args"].index("--mounted-image-id") + 1]
    fixture_sha = digest(
        [sha256(Path(x["path"]).read_bytes()).hexdigest() for x in declarations]
    )
    installed_sha = sha256(manifest.read_bytes()).hexdigest()

    async def exercise(port):
        async with (
            asyncio.timeout(160),
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            bulk = await _rpc(
                client,
                "plan_rna_workflow",
                {
                    "operation": "bulk_de",
                    "objective": "Estimate B minus A in synthetic bulk counts",
                    "inputs": declarations,
                    "measurement": "non_umi_counts",
                    "count_provenance": "Original synthetic count fixture",
                    "parameters": {
                        "bulk_declarations": {
                            "biological_unit": "donor",
                            "population_unit_column": "donor",
                            "specimen_column": "donor",
                            "library_column": "sample_id",
                            "observation_column": "sample_id",
                            "condition_column": "condition",
                            "identity_status": "declared_from_experimental_records",
                            "dependence": "independent",
                            "batch_columns": "none",
                            "contrast_numerator": "B",
                            "contrast_denominator": "A",
                            "declaration_provenance": "Synthetic fixture records",
                        }
                    },
                },
            )
            run_key, waiting, bulk_approval = await _execute(client, bulk)
            assert waiting["status"] == "awaiting_review", waiting
            await _rpc(
                client,
                "get_execution_receipt",
                run_key,
                blocked="bulk_fit_requires_current_studio_review_and_run",
            )
            sid = bulk["session_id"]
            api = f"/api/scientific/adaptive/{sid}"
            review = await _review_and_run(port, api)
            de_receipt = await _rpc(client, "get_execution_receipt", run_key)
            assert de_receipt["probability_bounds_status"] == "verified"
            assert de_receipt["metrics"]["tested_feature_rows"] == len(genes)
            pathway = await _rpc(
                client,
                "plan_rna_workflow",
                {
                    "operation": "pathways",
                    "session_id": sid,
                    "objective": "Test the planted synthetic program over tested genes",
                    "parameters": {
                        "step_id": "operator-bulk-de",
                        "foreground_q_max": 0.05,
                        "pathway_plan": {
                            "method": "ora",
                            "collection_id": "synthetic",
                            "collection_version": "1",
                            "min_set_size": 2,
                            "gene_sets": [
                                {"set_id": "planted", "genes": genes[:12]},
                                {"set_id": "control", "genes": genes[18:30]},
                            ],
                        },
                    },
                },
            )
            pathway_key, completed, pathway_approval = await _execute(client, pathway)
            assert completed["status"] == "completed", completed
            result = await _rpc(client, "get_execution_receipt", pathway_key)
            assert result["metrics"] == {"method": "ora", "result_rows": 2}
            assert result["probability_bounds_status"] == "verified"
            ledger = _ledger(workspace)
            graph = AdaptiveWorkflow(ledger, sid)
            executor = TierAExecutor(ledger, mounted_image_id=image)
            steps = [receipt["tool_receipts"][0] for receipt in (de_receipt, result)]
            earlier, later = graph._read_projection(
                lambda db: tuple(
                    graph._state(db).outcome(step["step"]) for step in steps
                )
            )
            assert [outcome["receipt_sha256"] for outcome in (earlier, later)] == [
                step["receipt_sha256"] for step in steps
            ]
            assert later["consumed"][0]["sha256"] in {
                item["sha256"] for item in earlier["outputs"].values()
            }
            view = await _projection(port, api)
            studio = view["plugin_session"]
            approvals = (digest((bulk_approval, review)), pathway_approval)
            bindings = (
                ("differential_expression", "count_matrix", ("gene_counts",), ("csv",)),
                (
                    "overrepresentation",
                    "prior_results",
                    ("foreground_gene_list", "filtered_tested_background", "gene_sets"),
                    ("json", "parquet"),
                ),
            )
            for index, (plan, receipt, approved, scope) in enumerate(
                zip(
                    (bulk, pathway),
                    (de_receipt, result),
                    approvals,
                    bindings,
                    strict=True,
                )
            ):
                presented = next(
                    item
                    for item in studio["runs"]
                    if item["run_id"] == receipt["run_id"]
                )
                assert presented["status"] == "completed", presented
                assert presented["summary_tables"] == receipt["summary_tables"]
                studio_plan = next(
                    item
                    for item in studio["plans"]
                    if item["plan_id"] == presented["plan_id"]
                )
                actual, materials = _checked_readback(
                    graph,
                    executor,
                    receipt["tool_receipts"][0],
                    plan,
                )
                observed, _ = _checked_readback(
                    graph,
                    executor,
                    presented["tool_receipts"][0],
                    studio_plan,
                )
                assert actual == observed
                assert any(
                    entry["entry_kind"] == "attempt"
                    and entry["step_id"] == observed.step_id
                    and entry["receipt_sha256"] == observed.receipt_sha256
                    for entry in view["graph"]["entries"]
                )
                if index == 1:
                    ora = PathwayResult.model_validate_json(materials["pathway_result"])
                    assert ora.tested_gene_count == len(genes)
                    rows = {row.set_id: row for row in ora.results}
                    assert rows["planted"].overlap_count == 12
                    assert rows["planted"].q_value < rows["control"].q_value
                gate = request.config.pluginmanager.getplugin("devharness-rna-release")
                if gate is None:
                    continue
                question, entry, measurements, formats = scope
                binding = ExecutorBinding(
                    route=next(
                        route
                        for route in execution_inventory()
                        if (
                            route.question,
                            route.assay,
                            route.entry_point,
                            route.measurements,
                            route.operation_id,
                        )
                        == (
                            question,
                            "bulk_rna_seq",
                            entry,
                            measurements,
                            actual.operation_id,
                        )
                    ),
                    operation_id=actual.operation_id,
                    version=executor.backend_id,
                    runtime_sha256=actual.runtime_sha256,
                    executor_sha256=actual.executor_sha256,
                    input_formats=formats,
                )
                gate.record(
                    binding,
                    VerifiedRun(
                        binding=binding,
                        installed_plugin_sha256=installed_sha,
                        fixture_sha256=fixture_sha,
                        approved_plan_sha256=plan["plan_sha256"],
                        approval_receipt_sha256=approved,
                        graph=actual,
                        studio=observed,
                        scope="synthetic",
                        dependency_receipts=(earlier["receipt_sha256"],)
                        if index
                        else (),
                    ),
                )

    with _offline_studio(settings, inputs, workspace) as port:
        asyncio.run(exercise(port))
