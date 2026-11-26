"""Wire and custody tests for the independent local RNA plugin."""

import asyncio
import http.client
import json
import os
import sqlite3
import sys
import threading
from io import BytesIO
from zipfile import ZipFile

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

pytestmark = pytest.mark.wal

TOOLS = {
    "inspect_rna_inputs",
    "check_answerability",
    "plan_rna_workflow",
    "execute_reviewed_plan",
    "get_run_status",
    "get_execution_receipt",
    "manage_research_aim",
}


@pytest.mark.parametrize(
    ("status", "view"), [("completed", "overview"), ("running", "workflows")]
)
def test_run_card_lands_on_results_after_completion(status, view):
    from types import SimpleNamespace

    from devbench.platform.plugin_mcp import Plugin

    sid = "a" * 32
    owner = SimpleNamespace(
        _handoff=lambda session_id, tab: {
            "studio_url": f"http://127.0.0.1:8765/?session_id={session_id}&view={tab}",
        }
    )
    row = {
        "stage_json": "{}",
        "status": status,
        "session_id": sid,
        "run_id": "b" * 32,
        "started_at": 1,
        "finished_at": 2,
        "error_text": None,
    }
    assert Plugin._status(owner, row, {})["studio_url"] == (
        f"http://127.0.0.1:8765/?session_id={sid}&view={view}"
    )


def test_bulk_replay_links_the_exact_verified_tier_a_receipt(workspace_tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.plugin_jobs import digest
    from devbench.platform.plugin_mcp import _seal_bulk_replay

    root = workspace_tmp_path
    run_id = "a" * 32
    plan = {"operation": "bulk_de", "profiles": [{"sha256": "b" * 64}]}
    receipt = {
        "run_id": run_id,
        "tool_receipts": [{"step": "operator-bulk-de", "receipt_sha256": "c" * 64}],
        "clinical_release_allowed": False,
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        readers = [
            pool.submit(_seal_bulk_replay, root, run_id, plan, digest(plan), receipt)
            for _ in range(2)
        ]
        sealed, concurrent = (reader.result() for reader in readers)
    assert sealed == concurrent
    assert sealed == _seal_bulk_replay(root, run_id, plan, digest(plan), receipt)
    bundle = root / "runs" / run_id / "replay_bundle" / digest(receipt)
    assert len(sealed["replay_bundle_sha256"]) == 64
    assert json.loads((bundle / "receipt.json").read_bytes()) == receipt
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["plan_sha256"] == digest(plan)

    (bundle / "receipt.json").write_text("{}")
    with pytest.raises(OperationBlocked, match="replay_bundle_identity_mismatch"):
        _seal_bulk_replay(root, run_id, plan, digest(plan), receipt)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("1.0,2.0,3.0,4.0", "suspected_transformed_integer_counts"),
        ("-1.5,2,3,4", "raw_count_slot_unverified"),
        ("1.00000000000000001,2,3,4", "raw_count_slot_unverified"),
        ("1e1,2.0,0,4", None),
    ],
)
def test_raw_bulk_plan_checks_exact_decimals_without_assessment(
    workspace_tmp_path, row, reason
):
    import hashlib

    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.plugin_mcp import Plan, _check_unassessed_plan_counts

    path = workspace_tmp_path / "counts.csv"
    path.write_text(f"gene,s1,s2,s3,s4\ng1,{row}\ng2,4,3,2,1\n")
    args = Plan.model_validate(
        {
            "operation": "bulk_de",
            "objective": "Check declared counts",
            "measurement": "raw_counts",
            "inputs": [{"path": str(path), "data_classification": "synthetic"}],
        }
    ).root
    profile = {
        "role": "expression",
        "source": str(path),
        "observed": {"file_type": "csv"},
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }
    if reason:
        with pytest.raises(OperationBlocked, match=reason):
            _check_unassessed_plan_counts(args, [profile])
    else:
        _check_unassessed_plan_counts(args, [profile])


def test_job_store_migrates_the_legacy_session_constraint(workspace_tmp_path):
    from devbench.platform.plugin_jobs import JobStore, canonical, digest

    sid, pid, rid = "a" * 32, "b" * 32, "c" * 32
    initial = {"session_id": sid, "plan_id": pid}
    path = workspace_tmp_path / "plugin-jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE plugin_plans (
                plan_id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
                plan_sha256 TEXT NOT NULL, plan_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE plugin_runs (
                run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                plan_id TEXT NOT NULL UNIQUE REFERENCES plugin_plans(plan_id),
                plan_sha256 TEXT NOT NULL, status TEXT NOT NULL, pid INTEGER,
                stage_json TEXT NOT NULL, receipt_json TEXT, error_text TEXT,
                started_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL
            );
        """)
        db.execute(
            "INSERT INTO plugin_plans VALUES (?, ?, ?, ?, 1)",
            (pid, sid, digest(initial), canonical(initial).decode()),
        )
        db.execute(
            "INSERT INTO plugin_runs VALUES (?, ?, ?, ?, 'completed', NULL, '{}', "
            "'{}', NULL, 1, 1, 1)",
            (rid, sid, pid, digest(initial)),
        )
    path.chmod(0o600)

    jobs = JobStore(workspace_tmp_path)
    follow_up = {**initial, "plan_id": "d" * 32}
    jobs.freeze(follow_up)
    jobs = JobStore(workspace_tmp_path)
    assert jobs.plan(sid, pid) == initial
    assert jobs.plan(sid) == follow_up
    assert jobs.launch(sid, pid, digest(initial))["run_id"] == rid
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA foreign_key_list(plugin_runs)").fetchone()[2] == (
            "plugin_plans"
        )
    assert path.stat().st_mode & 0o777 == 0o600


def test_pathway_queue_reads_a_json_frozen_method(monkeypatch):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform import plugin_mcp

    def stop_after_decoding(*_args, **_kwargs):
        raise OperationBlocked("test_reached_retained_tested_background")

    monkeypatch.setattr(plugin_mcp, "_pathway_universe", stop_after_decoding)
    plan = {
        "design": {
            "parameters": {
                "pathway_plan": {
                    "method": "ora",
                    "collection_id": "synthetic",
                    "collection_version": "1",
                    "gene_sets": [{"set_id": "a", "genes": ["gene1", "gene2"]}],
                },
                "foreground_q_max": 0.05,
            },
        },
    }
    with pytest.raises(
        OperationBlocked,
        match="test_reached_retained_tested_background",
    ):
        plugin_mcp._queue_reviewed_pathway(None, None, plan, {"de_source": {}})


def test_pathway_review_ranks_by_signed_evidence_and_derives_directional_foreground():
    from io import BytesIO
    from types import SimpleNamespace

    import pyarrow as pa
    import pyarrow.parquet as pq

    from devbench.platform import plugin_mcp

    rows = pa.table(
        {
            "gene_id": ["noisy", "strong", "negative", "excluded", "underflow"],
            "estimate_status": [
                "estimated",
                "estimated",
                "estimated",
                "filtered",
                "estimated",
            ],
            "effect": [100.0, 0.5, -0.4, None, -0.2],
            "p_value": [0.5, 0.0001, 0.001, None, 0.0],
            "q_value": [0.6, 0.001, 0.01, None, 0.0],
        }
    )
    buffer = BytesIO()
    pq.write_table(rows, buffer)
    executor = SimpleNamespace(open_output=lambda _: BytesIO(buffer.getvalue()))
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
    universe, scores = plugin_mcp._pathway_universe(
        executor, source, q_max=None, ranked=True
    )
    assert universe.genes == ("noisy", "strong", "negative", "underflow")
    assert scores == pytest.approx(
        {"noisy": 0.30103, "strong": 4, "negative": -3, "underflow": -300}
    )
    universe, effects = plugin_mcp._pathway_universe(
        executor, source, q_max=0.05, ranked=False
    )
    assert universe.selected == ("strong", "negative", "underflow")
    for direction, expected in [
        ("up_only", ("strong",)),
        ("down_only", ("negative", "underflow")),
    ]:
        assert plugin_mcp._pathway_foreground(universe, effects, direction) == expected


def test_stdio_real_async_matrix_audit(workspace_tmp_path):
    from jsonschema import Draft202012Validator

    from devharness_controlled.native_contracts import limma_voom_kernel_images

    root = workspace_tmp_path
    inputs = root / "inputs"
    inputs.mkdir(mode=0o700)
    matrix = inputs / "counts.csv"
    matrix.write_text("gene,s1,s2,s3,s4\ngene1,1,2,6,7\ngene2,3,5,2,3\ngene3,9,5,6,4\n")
    workspace = root / "workspace"
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "devbench.platform.cli",
            "plugin-mcp",
            "--no-auto-studio",
            "--input-root",
            str(inputs),
            "--workspace",
            str(workspace),
            "--mounted-image-id",
            sorted(limma_voom_kernel_images())[0],
        ],
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "PYTHONPATH", "DYLD_LIBRARY_PATH"}
        },
    )

    async def roundtrip():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {item.name: item for item in (await client.list_tools()).tools}
                assert set(tools) == TOOLS
                mutating = {
                    "plan_rna_workflow",
                    "execute_reviewed_plan",
                    "manage_research_aim",
                }
                for name, tool in tools.items():
                    assert tool.annotations.readOnlyHint is (name not in mutating)
                    assert tool.outputSchema and tool.outputSchema["type"] == "object"
                    Draft202012Validator.check_schema(tool.outputSchema)
                    bad = await client.call_tool(name, {})
                    assert (
                        bad.isError
                        and bad.structuredContent["reason"] == "invalid_request"
                    )
                    Draft202012Validator(tool.outputSchema).validate(
                        bad.structuredContent
                    )
                execute = tools["execute_reviewed_plan"]
                plan_schema = tools["plan_rna_workflow"].inputSchema
                assert plan_schema["discriminator"]["propertyName"] == "operation"
                aim_schema = tools["manage_research_aim"].inputSchema
                assert aim_schema["discriminator"]["propertyName"] == "action"
                assert execute.annotations.readOnlyHint is False
                assert execute.annotations.idempotentHint is True
                assert execute.meta["ui"]["resourceUri"] == "ui://devharness/run-card"
                resources = (await client.list_resources()).resources
                assert len(resources) == 3
                for resource in resources:
                    assert resource.mimeType == "text/html;profile=mcp-app"
                    result = await client.read_resource(resource.uri)
                    assert "innerHTML" not in result.contents[0].text
                    assert result.contents[0].meta["ui"]["csp"]["connectDomains"] == []

                async def call(name, args, *, error=False):
                    result = await client.call_tool(name, args)
                    assert result.isError is error, result
                    Draft202012Validator(tools[name].outputSchema).validate(
                        result.structuredContent
                    )
                    assert (
                        json.loads(result.content[0].text) == result.structuredContent
                    )
                    return result.structuredContent

                selected = {"path": str(matrix), "data_classification": "synthetic"}
                inspected = await call("inspect_rna_inputs", {"inputs": [selected]})
                assert inspected["inputs"][0]["observed"]["file_type"] == "csv"
                assert "gene1" not in json.dumps(inspected)
                assert "s1" not in json.dumps(inspected["inputs"][0]["observed"])
                assert len(inspected["inputs"][0]["inspection_receipt_sha256"]) == 64
                assert len(inspected["inputs"][0]["observation_axis_sha256"]) == 64
                transcript_matrix = inputs / "transcripts.csv"
                transcript_matrix.write_text(
                    "transcript_id,subject_a,subject_b\nt1,2,4\n"
                )
                projected = await call(
                    "inspect_rna_inputs",
                    {
                        "inputs": [
                            {
                                **selected,
                                "path": str(transcript_matrix),
                                "role": "metadata",
                            }
                        ]
                    },
                )
                assert "subject_a" not in json.dumps(projected)
                import numpy as np
                from anndata import AnnData

                annotated = AnnData(np.ones((1600, 900), dtype=np.int32))
                annotated.obs["batch"] = ["lane_a"] * 800 + ["lane_b"] * 800
                for index in range(65):
                    annotated.obs[f"field_{index}"] = ["SENSITIVE_SENTINEL"] * 1600
                annotated.obsm["X_pca"] = np.ones((1600, 2))
                h5ad = inputs / "cells.h5ad"
                annotated.write_h5ad(h5ad)
                assert 4 * 1024**2 < h5ad.stat().st_size < 20 * 1024**2
                cells = await call(
                    "inspect_rna_inputs", {"inputs": [{**selected, "path": str(h5ad)}]}
                )
                inspected_h5ad = cells["inputs"][0]
                observed = inspected_h5ad["observed"]
                assert len(inspected_h5ad["observation_axis_sha256"]) == 64
                assert len(inspected_h5ad["feature_axis_sha256"]) == 64
                assert observed["layers"] == []
                assert observed["obsm_keys"] == ["X_pca"]
                assert len(observed["obs_columns"]) == 64
                assert observed["obs_columns_truncated"] is True
                assert "SENSITIVE_SENTINEL" not in json.dumps(cells)
                from devharness_controlled.native_contracts import (
                    single_cell_kernel_images,
                )

                assert len(single_cell_kernel_images()) > 0
                cell_request = {
                    "family_id": 8,
                    "question": "atlas_integration",
                    "assay": "single_cell_rna_seq",
                    "entry_point": "annotated_object",
                }
                local_cells = {"inputs": [{**selected, "path": str(h5ad)}]}
                cell_answer = await call(
                    "check_answerability",
                    {
                        "request": cell_request,
                        "batch_column": "batch",
                        "capture_units_confirmed": True,
                        "measurement": "umi_counts",
                        "count_provenance": "Original synthetic counts in X",
                        **local_cells,
                    },
                )
                assert cell_answer["status"] == "ready", cell_answer
                assert cell_answer["execution_route"] == "tier_a"
                assert cell_answer["attested_measurements"] == [
                    {"kind": "gene_counts", "slot": "X"},
                    {"kind": "batch_membership", "slot": "obs/batch"},
                ]
                for slot in ("spliced", "unspliced"):
                    annotated.layers[slot] = annotated.X.copy()
                annotated.write_h5ad(h5ad)
                unsupported = await call(
                    "check_answerability",
                    {
                        "request": {
                            "family_id": 15,
                            "question": "rna_velocity",
                            "assay": "single_cell_rna_seq",
                            "entry_point": "annotated_object",
                        },
                        "inputs": [{**selected, "path": str(h5ad)}],
                    },
                )
                assert unsupported["status"] == "unavailable"
                assert unsupported["reason"] == "runtime_unavailable"
                annotated.obs["patient_id"] = ["opaque"] * 1600
                annotated.write_h5ad(h5ad)
                blocked_cells = await call(
                    "inspect_rna_inputs",
                    {"inputs": [{**selected, "path": str(h5ad)}]},
                    error=True,
                )
                assert blocked_cells["reason"] == "private_data_local_studio_only"
                assert "patient_id" not in json.dumps(blocked_cells)
                clinical_alias = inputs / "ambiguous.tsv"
                clinical_alias.write_text(
                    "sample_id\tmedicalRecordNumber\ns1\topaque\n"
                )
                privacy = await call(
                    "inspect_rna_inputs",
                    {"inputs": [{**selected, "path": str(clinical_alias)}]},
                    error=True,
                )
                assert privacy["reason"] == "private_data_local_studio_only"
                malformed = await call(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "inputs": [selected],
                        "objective": "Synthetic audit",
                        "parameters": {
                            "PRIVATE_FIELD_SENTINEL": "PRIVATE_VALUE_SENTINEL"
                        },
                    },
                    error=True,
                )
                assert malformed["reason"] == "invalid_request"
                assert malformed["validation_errors"]
                assert "SENTINEL" not in json.dumps(malformed)
                clinical = inputs / "opaque.csv"
                clinical.write_text("patient_id,outcome\nZZ001,1\n")
                hidden = await call(
                    "inspect_rna_inputs",
                    {
                        "inputs": [
                            {
                                "path": str(clinical),
                                "data_classification": "public",
                            }
                        ]
                    },
                    error=True,
                )
                assert hidden["reason"] == "private_data_local_studio_only"
                assert "ZZ001" not in json.dumps(hidden)
                answer = await call(
                    "check_answerability",
                    {
                        "request": {
                            "family_id": 1,
                            "question": "differential_expression",
                            "assay": "bulk_rna_seq",
                            "entry_point": "count_matrix",
                        }
                    },
                )
                assert answer["status"] == "awaiting_information"
                assert answer["registered_family_count"] == 20
                design = inputs / "design.csv"
                design.write_text(
                    "sample_id,donor,condition,batch\ns1,a,B,A\ns2,b,B,A\n"
                    "s3,c,A,B\ns4,d,A,B\n"
                )
                metadata = {
                    "path": str(design),
                    "data_classification": "synthetic",
                    "role": "metadata",
                }
                design_request = {
                    "request": {
                        "family_id": 1,
                        "question": "differential_expression",
                        "assay": "bulk_rna_seq",
                        "entry_point": "count_matrix",
                    },
                    "local_design": {
                        "metadata": metadata,
                        "formula": "~ condition + batch",
                        "contrast": {
                            "factor": "condition",
                            "numerator": "B",
                            "denominator": "A",
                        },
                        "biological_unit": "donor",
                        "batch_columns": ["batch"],
                    },
                }
                confounded = await call("check_answerability", design_request)
                assert confounded["reason"] == "rank_deficient_or_confounded_design"
                inspected_design = await call(
                    "inspect_rna_inputs", {"inputs": [metadata]}
                )
                assert inspected_design["inputs"][0]["observed"]["columns"] == [
                    "sample_id",
                    "donor",
                    "condition",
                    "batch",
                ]
                checked_plan = await call(
                    "plan_rna_workflow",
                    {
                        "operation": "bulk_de",
                        "inputs": [selected, metadata],
                        "measurement": "raw_counts",
                        "objective": "Check the confounded synthetic design.",
                        "answerability": design_request,
                    },
                )
                assert checked_plan["answerability"] == confounded
                plan = await call(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "inputs": [selected],
                        "measurement": "non_umi_counts",
                        "objective": "Audit the synthetic expression matrix.",
                    },
                )
                assert plan["status"] == "planned"
                assert plan["steps"][0]["state"] == "planned"
                identity = {
                    key: plan[key] for key in ("session_id", "plan_id", "plan_sha256")
                }
                bad = await call(
                    "execute_reviewed_plan",
                    {
                        **identity,
                        "plan_sha256": "0" * 64,
                    },
                    error=True,
                )
                assert bad["reason"] == "plan_identity_mismatch"
                with sqlite3.connect(workspace / "plugin-jobs.sqlite3") as db:
                    assert db.execute(
                        "select count(*) from plugin_runs"
                    ).fetchone() == (0,)
                run = await call("execute_reviewed_plan", identity)
                assert (await call("execute_reviewed_plan", identity))["run_id"] == run[
                    "run_id"
                ]
                run_key = {"session_id": run["session_id"], "run_id": run["run_id"]}
                async with asyncio.timeout(60):
                    while run["status"] in {"queued", "running"}:
                        await asyncio.sleep(0.15)
                        run = await call("get_run_status", run_key)
                diagnostic = workspace / "runs" / run["run_id"] / "worker-error.log"
                assert run["status"] == "completed", (
                    diagnostic.read_text() if diagnostic.exists() else run["warnings"]
                )
                assert run["progress"] == {"completed": 1, "total": 1}
                receipt = await call("get_execution_receipt", run_key)
                assert receipt["metrics"]["genes"] == 3
                assert receipt["summary_tables"][0]["rows"]
                assert len(json.dumps(receipt).encode()) <= 64 * 1024
                assert receipt["clinical_release_allowed"] is False
                assert len(receipt["replay_bundle_sha256"]) == 64
                replay = workspace / "runs" / run["run_id"] / "replay_bundle"
                manifest = replay / "manifest.json"
                original = manifest.read_bytes()
                manifest.write_bytes(b"{}")
                tampered = await call("get_execution_receipt", run_key, error=True)
                assert tampered["reason"] == "replay_bundle_identity_mismatch"
                manifest.write_bytes(original)
                next_plan = await call(
                    "plan_rna_workflow",
                    {
                        "session_id": plan["session_id"],
                        "operation": "matrix_audit",
                        "inputs": [selected],
                        "measurement": "non_umi_counts",
                        "objective": "Run a second audit in the same investigation.",
                    },
                )
                assert next_plan["session_id"] == plan["session_id"]
                assert next_plan["plan_id"] != plan["plan_id"]
                assert (await call("execute_reviewed_plan", identity))["run_id"] == run[
                    "run_id"
                ]
                assert (await call("get_execution_receipt", run_key)) == receipt
                next_run = await call(
                    "execute_reviewed_plan",
                    {key: next_plan[key] for key in identity},
                )
                next_key = {
                    "session_id": plan["session_id"],
                    "run_id": next_run["run_id"],
                }
                async with asyncio.timeout(60):
                    while next_run["status"] in {"queued", "running"}:
                        await asyncio.sleep(0.15)
                        next_run = await call("get_run_status", next_key)
                assert next_run["status"] == "completed", next_run
                assert (await call("get_execution_receipt", next_key))["summary_tables"]

                eligible_matrix = inputs / "eligible-counts.csv"
                eligible_matrix.write_text(
                    "gene,s1,s2,s3,s4,s5,s6\ngene1,1,2,6,7,3,4\ngene2,3,5,2,3,5,6\n"
                )
                eligible = inputs / "eligible-design.csv"
                eligible.write_text(
                    "sample_id,donor,condition\n"
                    "s1,a,A\ns2,b,A\ns3,c,A\ns4,d,B\ns5,e,B\ns6,f,B\n"
                )
                ready = await call(
                    "check_answerability",
                    {
                        **design_request,
                        "inputs": [
                            {**selected, "path": str(eligible_matrix)},
                            {**metadata, "path": str(eligible)},
                        ],
                        "local_design": {
                            **design_request["local_design"],
                            "formula": "~ condition",
                            "metadata": {**metadata, "path": str(eligible)},
                            "batch_columns": [],
                        },
                    },
                )
                assert ready["status"] == "ready"
                assert ready["execution_route"] == "tier_a"
                assert ready["evidence_provenance"] == "server_attested_local_inputs"
                bulk = await call(
                    "plan_rna_workflow",
                    {
                        "operation": "bulk_de",
                        "inputs": [
                            {**selected, "path": str(eligible_matrix)},
                            {**metadata, "path": str(eligible)},
                        ],
                        "measurement": "non_umi_counts",
                        "count_provenance": "Original synthetic count fixture",
                        "objective": "Estimate B minus A in synthetic counts.",
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
                bulk_run = await call(
                    "execute_reviewed_plan",
                    {
                        key: bulk[key]
                        for key in ("session_id", "plan_id", "plan_sha256")
                    },
                )
                bulk_key = {key: bulk_run[key] for key in ("session_id", "run_id")}
                async with asyncio.timeout(60):
                    while bulk_run["status"] in {"queued", "running"}:
                        await asyncio.sleep(0.15)
                        bulk_run = await call("get_run_status", bulk_key)
                assert bulk_run["status"] == "awaiting_review", bulk_run["warnings"]
                assert bulk_run["progress"] == {"completed": 1, "total": 3}
                assert "--allow-data-execution" in bulk_run["launch_hint"]
                assert "--mounted-image-id" in bulk_run["launch_hint"]
                waiting = await call("get_execution_receipt", bulk_key, error=True)
                assert waiting["reason"] == (
                    "bulk_fit_requires_current_studio_review_and_run"
                )

                aim_args = {"session_id": plan["session_id"], "action": "list"}
                before = await call("manage_research_aim", aim_args)
                proposed = await call(
                    "manage_research_aim",
                    {
                        **aim_args,
                        "action": "propose_aim",
                        "request": {
                            "expected_revision": before["revision"],
                            "definition": {
                                "aim_id": "describe-coverage",
                                "kind": "exploratory",
                                "question": "Which profiles have nonzero values?",
                                "estimand": "Genes with finite nonzero expression",
                            },
                        },
                    },
                )
                await call(
                    "manage_research_aim",
                    {
                        **aim_args,
                        "action": "approve_aim",
                        "request": {
                            "expected_revision": proposed["revision"],
                            "aim_id": "describe-coverage",
                            "rationale": "Use these observations for intake.",
                        },
                    },
                )
                after = await call("manage_research_aim", aim_args)
                assert after["aims"] == [
                    {
                        "aim_id": "describe-coverage",
                        "version": 1,
                        "status": "approved",
                    }
                ]
                local_only = inputs / "local-values.csv"
                local_only.write_text("gene,s1,s2\nfeature1,12,14\n")
                private_input = {**selected, "path": str(local_only)}
                private = await call(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "inputs": [{**private_input, "data_classification": "private"}],
                        "objective": "Local-only material",
                    },
                )
                blocked = await call(
                    "get_execution_receipt",
                    {
                        "session_id": private["session_id"],
                        "run_id": "f" * 32,
                    },
                    error=True,
                )
                assert blocked["reason"] == "private_data_local_studio_only"
                assert set(blocked) <= {"error", "reason", "studio_url", "launch_hint"}
                reclassified = await call(
                    "inspect_rna_inputs",
                    {"inputs": [private_input]},
                    error=True,
                )
                assert reclassified["reason"] == "private_data_local_studio_only"
                downgrade = await call(
                    "plan_rna_workflow",
                    {
                        "session_id": private["session_id"],
                        "operation": "matrix_audit",
                        "inputs": [selected],
                        "objective": "Synthetic audit",
                    },
                    error=True,
                )
                assert downgrade["reason"] == "private_data_local_studio_only"
                package = await call(
                    "manage_research_aim",
                    {
                        "session_id": plan["session_id"],
                        "action": "build_research_package",
                        "request": {
                            "spec": {
                                "question": "What can the current evidence support?",
                                "outcome": "partial",
                                "expected_revision": after["revision"],
                            },
                        },
                    },
                )
                assert package["status"] == "verified"
                assert len(package["package_root_sha256"]) == 64
                from devbench.engine.workflow_operations import LEDGER_NAME

                with sqlite3.connect(workspace / "methods" / LEDGER_NAME) as db:
                    assert db.execute("PRAGMA user_version").fetchone() == (1,)
                return plan

    plan = asyncio.run(roundtrip())
    assert plan["session_id"]
    _studio_readback(workspace, inputs, plan["session_id"])
    database = workspace / "plugin-jobs.sqlite3"
    assert database.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(database) as db:
        assert db.execute("pragma journal_mode").fetchone() == ("wal",)


def _studio_readback(workspace, inputs, sid):
    from devbench.platform.workbench_ui import WorkbenchService, make_server

    app = WorkbenchService(
        workspace,
        [inputs],
        enable_scientific=True,
        offline_only=True,
        interactive_adaptive=True,
    )
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from contextlib import closing

        with closing(
            http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        ) as conn:
            conn.request("GET", f"/api/scientific/adaptive/{sid}")
            response = conn.getresponse()
            assert response.status == 200
            assert response.getheader("X-Frame-Options") == "DENY"
            adaptive = json.loads(response.read())
            assert adaptive["graph_status"] == "available"
            plugin = adaptive["plugin_session"]
            assert plugin["dataset_label"] == "counts.csv"
            assert plugin["plans"] and plugin["runs"]
            assert any(run.get("summary_tables") for run in plugin["runs"])
            assert str(inputs) not in json.dumps(plugin)
            (package,) = adaptive["packages"]
            assert package["root_sha256"] and package["replay_script"]
            assert package["summary_md"] and package["methods_md"]
            conn.request("GET", package["download_path"])
            response = conn.getresponse()
            assert response.status == 200
            assert response.getheader("Content-Type") == "application/zip"
            with ZipFile(BytesIO(response.read())) as archive:
                assert "provenance_manifest.json" in archive.namelist()
            conn.request("GET", "/api/scientific/sessions")
            response = conn.getresponse()
            assert response.status == 200
            sessions = json.loads(response.read())["sessions"]
            entry = next(row for row in sessions if row["session_id"] == sid)
            assert entry["origin"] == "plugin" and entry["package_count"] == 1
            assert entry["dataset_label"] == "counts.csv"
            summaries = workspace / "runs" / package["package_id"] / "research_package"
            summary = summaries / "summary.md"
            before = summary.read_bytes()
            try:
                summary.write_bytes(before + b"\nChanged after publication\n")
                conn.request("GET", package["download_path"])
                response = conn.getresponse()
                assert response.status == 400
                assert response.getheader("Content-Type") != "application/zip"
                response.read()
            finally:
                summary.write_bytes(before)
            seal = summaries.parent / "research-package-root.sha256"
            seal_backup = seal.with_suffix(".held")
            seal.rename(seal_backup)
            try:
                conn.request("GET", package["download_path"])
                response = conn.getresponse()
                assert response.status == 409
                assert response.getheader("Content-Type") != "application/zip"
                response.read()
            finally:
                seal_backup.rename(seal)
            route = f"/api/scientific/thesis/{sid}"
            conn.request("GET", route)
            response = conn.getresponse()
            assert response.status == 200
            thesis = json.loads(response.read())
            assert thesis["actions"]["propose_aim"]["enabled"] is True
            conn.request(
                "POST",
                route,
                json.dumps(
                    {
                        "action": "propose_aim",
                        "expected_revision": thesis["revision"],
                        "definition": {
                            "aim_id": "local-studio-follow-up",
                            "kind": "exploratory",
                            "question": "What is the synthetic count distribution?",
                            "estimand": "Nonzero counts per observation",
                        },
                    }
                ),
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{server.server_port}",
                },
            )
            response = conn.getresponse()
            assert response.status == 200, response.read()
            response.read()
            conn.request("GET", route)
            response = conn.getresponse()
            assert response.status == 200
            assert "local-studio-follow-up" in [
                row["aim_id"] for row in json.loads(response.read())["aims"]
            ]

            from uuid import uuid4

            from devbench.platform.plugin_jobs import JobStore

            jobs = JobStore(workspace)
            original = jobs.plan(sid)
            jobs.freeze(
                {
                    **original,
                    "plan_id": uuid4().hex,
                    "session_id": uuid4().hex,
                    "data_classification": "private",
                }
            )
            conn.request("GET", f"/api/scientific/adaptive/{sid}")
            response = conn.getresponse()
            assert response.status == 200
            withheld = json.loads(response.read())
            assert "plugin_session" not in withheld and withheld["packages"] == []
            conn.request("GET", package["download_path"])
            response = conn.getresponse()
            assert response.status == 403
            response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        app.close()


def test_stdio_large_private_h5ad_retains_reference_without_copy(workspace_tmp_path):
    import hashlib

    import h5py

    from devbench.analysis.method_validation import MAX_DATASET_BYTES
    from devbench.engine.methods import MethodService
    from devbench.platform.plugin_jobs import JobStore
    from devbench.platform.workbench_ui import WorkbenchService, make_server

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    matrix = inputs / "counts.h5ad"
    with h5py.File(matrix, "w") as handle:
        handle.create_dataset("X", data=[[5]])
        handle.create_group("obs").create_dataset("_index", data=[b"cell"])
        handle.create_group("var").create_dataset("_index", data=[b"gene"])
    size = MAX_DATASET_BYTES + 1
    with matrix.open("r+b") as handle:
        handle.truncate(size)
    with matrix.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()

    workspace = workspace_tmp_path / "workspace"
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "devbench.platform.cli",
            "plugin-mcp",
            "--no-auto-studio",
            "--input-root",
            str(inputs),
            "--workspace",
            str(workspace),
        ],
        env={
            key: value
            for key, value in os.environ.items()
            if key in {"PATH", "PYTHONPATH", "DYLD_LIBRARY_PATH"}
        },
    )
    selected = {"path": str(matrix), "data_classification": "private"}

    async def roundtrip():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                planned = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "inputs": [selected],
                        "objective": "Inspect matrix in offline Studio",
                    },
                )
                assert not planned.isError, planned.structuredContent
                handoff = planned.structuredContent
                assert handoff["status"] == "local_studio_only"
                assert checksum not in json.dumps(handoff)
                assert str(matrix) not in json.dumps(handoff)
                for tool, arguments in [
                    (
                        "execute_reviewed_plan",
                        {
                            key: handoff[key]
                            for key in (
                                "session_id",
                                "plan_id",
                                "plan_sha256",
                            )
                        },
                    ),
                    (
                        "inspect_rna_inputs",
                        {
                            "inputs": [{**selected, "data_classification": "public"}],
                        },
                    ),
                ]:
                    denied = await client.call_tool(tool, arguments)
                    assert denied.isError
                    assert (
                        denied.structuredContent["reason"]
                        == "private_data_local_studio_only"
                    )
                return handoff["session_id"]

    app = WorkbenchService(
        workspace,
        [inputs],
        offline_only=True,
        enable_scientific=True,
        interactive_adaptive=True,
    )
    server = make_server(app, port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        sid = asyncio.run(roundtrip())
        plan = JobStore(workspace).plan(sid)
        profile = plan["profiles"][0]
        assert (profile["source"], profile["sha256"], profile["size_bytes"]) == (
            str(matrix),
            checksum,
            size,
        )
        methods = MethodService(workspace / "methods", readonly=True)
        assert not (methods.root / "objects" / checksum).exists()
        (reference,) = methods.list_records("artifact")
        assert reference["artifact_type"] == "rna_local_input_reference"
        reference_path = methods.artifact_path(reference["artifact_id"])
        assert json.loads(reference_path.read_text()) == {
            "format": "devbench.local-input-reference/1",
            "file_type": "h5ad",
            "sha256": checksum,
            "size_bytes": size,
        }

        connection.request("GET", "/api/scientific/sessions")
        response = connection.getresponse()
        assert response.status == 200
        (session,) = json.loads(response.read())["sessions"]
        assert session["session_id"] == sid
        assert session["data_classification"] == "private"
        connection.request("GET", f"/api/scientific/adaptive/{sid}")
        response = connection.getresponse()
        assert response.status == 200
        graph = json.loads(response.read())
        assert graph["status"] == "available"
        assert "plugin_session" not in graph
        anchors = app.scientific._plan(sid)["scientific_anchors"]
        (dataset_ref,) = anchors["dataset_versions"]
        store = app.scientific.store()
        dataset = store.get("dataset", dataset_ref["object_id"])
        assert dataset["locations"] == {"expression-0": str(matrix)}
        member = store.resolve(dataset_ref)["manifest"]["members"]["expression-0"]
        assert member["artifact_id"] == reference["artifact_id"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        app.close()


def test_stdio_explicit_files_outside_input_root(workspace_tmp_path):
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir()
    external = workspace_tmp_path / "other-folder"
    external.mkdir()
    counts = external / "counts.csv"
    counts.write_text("gene,sample-a,sample-b\ng1,2,4\ng2,1,3\n")
    symlink = external / "linked.csv"
    symlink.symlink_to(counts)
    workspace = workspace_tmp_path / "workspace"

    async def roundtrip(allow_external):
        args = [
            "-m",
            "devbench.platform.plugin_mcp",
            "--no-auto-studio",
            "--input-root",
            str(inputs),
            "--workspace",
            str(workspace),
        ]
        if allow_external:
            args.append("--allow-external-input-files")
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env={
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "PYTHONPATH", "DYLD_LIBRARY_PATH"}
            },
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                selected = {"path": str(counts), "data_classification": "synthetic"}
                inspected = await client.call_tool(
                    "inspect_rna_inputs", {"inputs": [selected]}
                )
                if not allow_external:
                    assert inspected.isError
                    return
                assert not inspected.isError, inspected.structuredContent
                profile = inspected.structuredContent["inputs"][0]
                assert profile["size_bytes"] == counts.stat().st_size
                cases = (
                    ({"path": str(counts)}, "private_data_local_studio_only"),
                    ({**selected, "path": str(symlink)}, "invalid_request"),
                )
                for raw, expected in cases:
                    denied = await client.call_tool(
                        "inspect_rna_inputs", {"inputs": [raw]}
                    )
                    assert denied.isError
                    assert denied.structuredContent["reason"] == expected
                planned = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "objective": "Inspect synthetic counts",
                        "measurement": "non_umi_counts",
                        "inputs": [selected],
                    },
                )
                assert not planned.isError, planned.structuredContent
                frozen = planned.structuredContent
                assert frozen["status"] == "planned"
                started = await client.call_tool(
                    "execute_reviewed_plan",
                    {
                        key: frozen[key]
                        for key in ("session_id", "plan_id", "plan_sha256")
                    },
                )
                assert not started.isError, started.structuredContent
                run = started.structuredContent
                key = {field: run[field] for field in ("session_id", "run_id")}
                async with asyncio.timeout(60):
                    while run["status"] in {"queued", "running"}:
                        await asyncio.sleep(0.15)
                        status = await client.call_tool("get_run_status", key)
                        assert not status.isError, status.structuredContent
                        run = status.structuredContent
                assert run["status"] == "completed", run["warnings"]
                private = await client.call_tool(
                    "plan_rna_workflow",
                    {
                        "operation": "matrix_audit",
                        "objective": "Inspect locally",
                        "inputs": [{"path": str(counts)}],
                    },
                )
                assert not private.isError, private.structuredContent
                assert private.structuredContent["status"] == "local_studio_only"
                assert str(counts) not in json.dumps(private.structuredContent)

    asyncio.run(roundtrip(False))
    asyncio.run(roundtrip(True))


@pytest.mark.parametrize("classification", ["public", "synthetic", "private"])
def test_selected_h5ad_does_not_inherit_discovery_cap(
    workspace_tmp_path, classification
):
    import h5py

    from devbench.analysis.workbench_inputs import InputDiscovery
    from devbench.platform.plugin_mcp import Input, _profile

    root = workspace_tmp_path
    matrix = root / "cells.h5ad"
    with h5py.File(matrix, "w") as handle:
        handle.create_dataset("X", data=[[7]])
        handle.create_group("obs").create_dataset("_index", data=[b"cell"])
        handle.create_group("var").create_dataset("_index", data=[b"gene"])
    discovery = InputDiscovery([root], max_file_bytes=1024)
    item = Input(path=str(matrix), data_classification=classification)
    assert _profile(discovery, root, item)["observed"]["cells"] == 1
    with pytest.raises(ValueError, match="inspection byte budget"):
        discovery.inspect(matrix)


def test_single_cell_summary_preserves_safe_labels(monkeypatch):
    from devbench.platform import plugin_mcp

    def retention_row(label):
        return {"group": label, "cells_input": 16, "cells_retained": 14}

    reports = {
        "qc_report": {
            "group_retention": {
                "groups": [
                    retention_row("treated"),
                    retention_row("patient_01"),
                    retention_row("someone@example.org"),
                ]
            }
        },
        "doublet_report": {
            "batches": [{"batch": "lane_b", "cells": 14, "predicted_doublets": 1}]
        },
        "composition_result": {
            "condition_biological_units": {"treated": 4, "clinical_case": 4},
            "effects": [
                {
                    "condition": "treated",
                    "cell_type": "Monocyte",
                    "posterior_inclusion_probability": 0.9,
                    "credible_effect": True,
                    "relative_log_coefficient": 0.4,
                },
                {
                    "condition": "clinical_case",
                    "cell_type": "x" * 129,
                    "posterior_inclusion_probability": 0.1,
                    "credible_effect": False,
                    "relative_log_coefficient": None,
                },
            ],
        },
    }
    monkeypatch.setattr(
        plugin_mcp, "_step_report", lambda _f, _e, _s, port: reports[port]
    )
    monkeypatch.setattr(plugin_mcp, "_cell_qc_quantiles", lambda *_: [])
    tables = plugin_mcp._cell_summaries(None, None, "test-step", {"outputs": reports})
    assert tables[1]["rows"] == [
        ["treated", 16, 14, 2],
        ["Group 2", 16, 14, 2],
        ["Group 3", 16, 14, 2],
    ]
    assert tables[2]["rows"][0][0] == "lane_b"
    assert tables[3]["rows"] == [["treated", 4], ["Condition 2", 4]]
    assert tables[4]["columns"][:2] == ["Cell type", "Covariate"]
    assert tables[4]["rows"][0][:2] == ["Monocyte", "treated"]
    assert "clinical_case" not in json.dumps(tables)
    assert "patient_01" not in json.dumps(tables)
    assert "someone@example.org" not in json.dumps(tables)
    assert "x" * 129 not in json.dumps(tables)


def test_salmon_answerability_checks_staged_manifest_and_tx2gene(workspace_tmp_path):
    from devbench.platform.plugin_mcp import Answerability, Plugin
    from devharness_controlled.native_contracts import limma_voom_kernel_images
    from tests.test_fastq_qc_salmon_bridge import fixture_inputs

    source, inputs, mapping = fixture_inputs(workspace_tmp_path)
    manifest = inputs / "salmon-plan.json"
    manifest.write_text(json.dumps(source))
    workspace = workspace_tmp_path / "workbench"
    plugin = Plugin(workspace, inputs, sorted(limma_voom_kernel_images())[0])
    request = Answerability.model_validate(
        {
            "request": {
                "family_id": 1,
                "question": "qc_salmon",
                "assay": "bulk_rna_seq",
                "entry_point": "reads",
            },
            "inputs": [
                {
                    "path": str(manifest),
                    "data_classification": "synthetic",
                    "role": "manifest",
                }
            ],
            "salmon": {"tx2gene": mapping, "quantification_only": True},
        }
    )
    result = plugin.answer(request)
    assert result["status"] == "ready"
    assert result["runtime"]["operation_id"] == "qc-salmon"
    assert len(result["workflow_evidence_sha256"]) == 64


def test_restart_reconciles_lost_worker_and_rejects_external_studio_link(
    workspace_tmp_path,
):
    import time

    from devbench.platform.plugin_jobs import JobStore, studio_handoff

    workspace = workspace_tmp_path / "workspace"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    store = JobStore(workspace)
    sid, pid, rid = "a" * 32, "b" * 32, "c" * 32
    _, sha = store.freeze({"session_id": sid, "plan_id": pid})
    now = time.time()
    with sqlite3.connect(store.path) as db:
        db.execute(
            "INSERT INTO plugin_runs VALUES (?, ?, ?, ?, 'running', ?, ?, "
            "NULL, NULL, ?, ?, NULL)",
            (rid, sid, pid, sha, os.getpid(), '{"process_created_at": 0}', now, now),
        )
    restarted = JobStore(workspace).run(sid, rid)
    assert restarted["status"] == "interrupted"
    assert restarted["error_text"] == "worker_not_running"

    state = workspace / "studio-server.json"

    def handoff(url):
        state.write_text(
            json.dumps({"workspace": str(workspace), "pid": os.getpid(), "url": url})
        )
        return studio_handoff(workspace, inputs, sid, "findings")

    for url in ("https://example.com:8765/", "http://127.0.0.1:8765@example.com/"):
        blocked = handoff(url)
        assert blocked["studio_url"] is None
        assert "--offline-only" in blocked["launch_hint"]
    assert handoff("http://127.0.0.1:8765/")["studio_url"] == (
        f"http://127.0.0.1:8765/?session_id={sid}&view=findings"
    )


def test_plugin_queues_verified_tenx_without_asserting_independent_units(
    workspace_tmp_path,
    monkeypatch,
):
    from dataclasses import asdict
    from types import SimpleNamespace

    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.adaptive_workflow import AdaptiveWorkflow
    from devbench.platform.plugin_mcp import (
        Answerability,
        Plan,
        Plugin,
        run_frozen_plan,
    )
    from devharness_controlled.native_contracts import single_cell_kernel_images
    from tests.test_p3_scrna_droplets import _provenance, _write_tenx

    workspace = workspace_tmp_path / "workbench"
    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    raw = inputs / "raw_feature_bc_matrix.h5"
    _write_tenx(raw)
    plugin = Plugin(workspace, inputs, sorted(single_cell_kernel_images())[0])
    droplets = plugin.answer(
        Answerability.model_validate(
            {
                "request": {
                    "family_id": 2,
                    "question": "droplet_qc",
                    "assay": "single_cell_rna_seq",
                    "entry_point": "count_matrix",
                },
                "inputs": [{"path": str(raw), "data_classification": "synthetic"}],
                "capture_units_confirmed": True,
                "measurement": "umi_counts",
                "count_provenance": "Original synthetic Cell Ranger counts",
                "raw_droplet_provenance": asdict(_provenance(raw)),
            }
        )
    )
    assert droplets["status"] == "ready"
    assert droplets["attested_measurements"] == [
        {"kind": "gene_counts", "slot": "matrix"}
    ]
    selected = plugin.plan(
        Plan.model_validate(
            {
                "operation": "single_cell",
                "inputs": [{"path": str(raw), "data_classification": "synthetic"}],
                "measurement": "umi_counts",
                "objective": "Check exploratory droplet QC on synthetic 10x counts.",
                "parameters": {
                    "capture_units_confirmed": True,
                    "raw_droplet_provenance": asdict(_provenance(raw)),
                    "steps": [
                        {
                            "operation": "scrna_droplets",
                            "parameters": {"method": "emptydrops"},
                            "sources": {
                                "droplets": {"id": "input", "port": None},
                                "droplet_provenance": {
                                    "id": "droplet_provenance",
                                    "port": None,
                                },
                            },
                        }
                    ],
                },
            }
        )
    )

    def stop_before_mounted_execution(flow, step_id, _executor, *, actor):
        state = flow._read_projection(lambda db: flow._state(db))
        step = state.nodes[step_id]
        assert step.profile.biological_units == ("unverified_exploratory",)
        assert {link.port.name for link in step.inputs} == {
            "droplets",
            "droplet_provenance",
        }
        assert not flow.authority.attempts(flow.lineage_id)
        raise OperationBlocked("test_stopped_before_mounted_execution")

    monkeypatch.setattr(AdaptiveWorkflow, "dispatch", stop_before_mounted_execution)
    jobs = SimpleNamespace(workspace=workspace, stage=lambda *_args, **_fields: None)
    with pytest.raises(OperationBlocked, match="test_stopped_before_mounted_execution"):
        run_frozen_plan(jobs, "a" * 32, plugin.jobs.plan(selected["session_id"]))
    frozen = plugin.jobs.plan(selected["session_id"])
    request = Plan.model_validate(
        {
            "session_id": selected["session_id"],
            "operation": "single_cell",
            "objective": "A different layer should require a separate admitted input.",
            "counts_layer": "different_layer",
            "parameters": frozen["design"]["parameters"],
        }
    )
    follow_up = plugin.plan(request)
    with pytest.raises(OperationBlocked, match="single_cell_count_layer_changed"):
        run_frozen_plan(
            jobs,
            "b" * 32,
            plugin.jobs.plan(selected["session_id"], follow_up["plan_id"]),
        )
