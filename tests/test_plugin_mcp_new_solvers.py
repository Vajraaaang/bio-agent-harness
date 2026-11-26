"""Round trips through the public MCP server and its durable worker."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from devbench.platform.plugin_jobs import canonical
from tests.test_bulk_deconvolution import _mixture_files
from tests.test_bulk_dtu import _dtu_inputs
from tests.test_perturbation_analysis import _perturbation_inputs
from tests.test_phenotype_prediction import _inputs as prediction_inputs
from tests.test_phenotype_prediction import _locked_eval
from tests.test_scrna_ccc import _fixture as ccc_inputs
from tests.test_scrna_neighborhood_da import _inputs as neighborhood_inputs
from tests.test_spatial_analysis import _spatial_inputs


@pytest.mark.parametrize(
    ("entry_point", "expected"),
    [
        ("transcript_quantification", {"estimated_gene_counts", "effective_lengths"}),
        ("normalized_expression", {"normalized_expression"}),
    ],
)
def test_bulk_mcp_answerability_admits_prepared_noninteger_expression(
    workspace_tmp_path, entry_point, expected
):
    from devbench.platform import plugin_mcp
    from devharness_controlled.native_contracts import limma_voom_kernel_images

    root = workspace_tmp_path
    inputs = root / "inputs"
    inputs.mkdir(mode=0o700)
    work = root / "scratch"
    work.mkdir(mode=0o700)
    samples = [f"s{i}" for i in range(6)]
    header = "gene_id," + ",".join(samples) + "\n"
    expression = inputs / "expression.csv"
    expression.write_text(
        header + "g1,0.4,1.2,1.1,3.9,4.5,4.4\ng2,1.5,2.2,1.8,3.2,3.1,3.6\n"
    )
    lengths = inputs / "lengths.csv"
    lengths.write_text(
        header + "g1,150.1,150,151,150,152,151\ng2,330,330,329,329,330,331\n"
    )
    metadata = inputs / "metadata.csv"
    metadata.write_text(
        "sample_id,donor_id,condition\n"
        + "\n".join(
            f"{s},d{i},{'control' if i < 3 else 'treated'}"
            for i, s in enumerate(samples)
        )
        + "\n"
    )

    def item(path, role):
        return {"path": str(path), "role": role, "data_classification": "synthetic"}

    image = sorted(limma_voom_kernel_images())[0]
    plugin = plugin_mcp.Plugin(work, inputs, mounted_image_id=image)
    payload = plugin_mcp.Answerability.model_validate(
        {
            "request": {
                "family_id": 1,
                "question": "differential_expression",
                "assay": "bulk_rna_seq",
                "entry_point": entry_point,
            },
            "inputs": [item(expression, "expression")]
            + ([item(lengths, "reference")] if "effective_lengths" in expected else []),
            "local_design": {
                "metadata": item(metadata, "metadata"),
                "formula": "~ condition",
                "biological_unit": "donor_id",
                "contrast": {
                    "factor": "condition",
                    "numerator": "treated",
                    "denominator": "control",
                },
            },
        }
    )
    answer = plugin.answer(payload)
    assert answer["status"] == "ready", answer
    assert {
        measurement["kind"] for measurement in answer["attested_measurements"]
    } == expected
    if "effective_lengths" in expected:
        lengths.write_text(lengths.read_text().replace("150.1", "0"))
        assert plugin.answer(payload)["missing_measurements"] == (
            ("effective_lengths",),
        )


def _payload(root, operation):
    root.mkdir(mode=0o700)
    roles = ["expression", "metadata"]
    if operation == "scrna_ccc":
        paths, spec = ccc_inputs(root)
        roles.append("reference")
    elif operation == "scrna_neighborhood_da":
        embedding, metadata, spec = neighborhood_inputs(root, expansion=True)
        paths = [embedding, metadata]
    elif operation == "bulk_deconvolution":
        paths, spec, _, _ = _mixture_files(root, add_outliers=True, cpm=True)
        roles = ["expression", "reference", "metadata"]
    elif operation == "bulk_dtu":
        paths, spec = _dtu_inputs(root)
        roles.append("reference")
    elif operation == "spatial_pattern":
        paths, spec = _spatial_inputs(root)
    elif operation == "perturbation_response":
        paths, spec, _, _ = _perturbation_inputs(root)
    else:
        paths, spec, counts, metadata = prediction_inputs(root)
        evaluation, spec = _locked_eval(root, spec, counts, metadata)
        paths.append(evaluation)
        roles.append("reference")
    parameters = spec.model_dump(mode="json")
    annotation = {}
    if operation == "bulk_dtu":
        annotation["transcript_reference"] = {
            "organism": "synthetic",
            "genome_build": "fixture-v1",
            "annotation_source": "synthetic annotation",
            "annotation_release": "1",
            "reference_sha256": parameters["mapping_sha256"],
        }
    return {
        **annotation,
        "operation": operation,
        "inputs": [
            {"path": str(path), "role": role, "data_classification": "synthetic"}
            for path, role in zip(paths, roles, strict=True)
        ],
        "parameters": parameters,
        "objective": f"Exercise the synthetic {operation} route.",
    }


def test_local_solvers_round_trip(workspace_tmp_path):
    operations = {
        "scrna_ccc": {"cell_type_expression", "ligand_receptor_reference"},
        "scrna_neighborhood_da": {"latent_embedding"},
        "bulk_deconvolution": {"normalized_expression", "cell_type_reference"},
        "bulk_dtu": {"transcript_counts"},
        "spatial_pattern": {"gene_counts", "spatial_coordinates"},
        "perturbation_response": {"perturbation_assignments", "control_assignments"},
        "phenotype_prediction": {
            "phenotype_outcomes",
            "independent_evaluation_partition",
        },
    }
    inputs, workspace = (workspace_tmp_path / name for name in ("inputs", "workspace"))
    inputs.mkdir(mode=0o700)
    payloads = {
        operation: _payload(inputs / operation, operation) for operation in operations
    }
    spatial_without_types = _payload(inputs / "spatial_without_types", "spatial_pattern")
    metadata = Path(spatial_without_types["inputs"][1]["path"])
    pd.read_csv(metadata).drop(columns="cell_type").to_csv(metadata, index=False)
    spatial_without_types["parameters"]["metadata_sha256"] = hashlib.sha256(
        metadata.read_bytes()
    ).hexdigest()
    options = StdioServerParameters(
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
            if key
            in {
                "PATH",
                "PYTHONPATH",
                "DYLD_LIBRARY_PATH",
                "OPENBLAS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "MKL_NUM_THREADS",
                "OMP_NUM_THREADS",
            }
        },
    )

    async def roundtrip():
        async with (
            stdio_client(options) as (reader, writer),
            ClientSession(reader, writer) as client,
        ):
            await client.initialize()
            tools = {
                tool.name: tool.inputSchema
                for tool in (await client.list_tools()).tools
            }
            assert len(tools) == 7
            assert (
                set(operations)
                <= tools["plan_rna_workflow"]["discriminator"]["mapping"].keys()
            )

            async def call(name, args, *, error=False):
                result = await client.call_tool(name, args)
                assert result.isError is error, result
                return result.structuredContent

            ccc = payloads["scrna_ccc"]
            await call(
                "plan_rna_workflow", {**ccc, "inputs": ccc["inputs"][:2]}, error=True
            )
            perturbation = payloads["perturbation_response"]
            await call(
                "plan_rna_workflow",
                {
                    **perturbation,
                    "parameters": {
                        **perturbation["parameters"],
                        "non_targeting_control": "missing-control",
                    },
                },
                error=True,
            )
            phenotype = payloads["phenotype_prediction"]
            await call(
                "plan_rna_workflow",
                {
                    **phenotype,
                    "inputs": phenotype["inputs"][:2],
                    "parameters": {
                        **phenotype["parameters"],
                        "independent_evaluation_partition_sha256": None,
                    },
                },
                error=True,
            )
            dtu = payloads["bulk_dtu"]
            confounded = _payload(inputs / "dtu_refusal", "bulk_dtu")
            paths, spec = _dtu_inputs(inputs / "dtu_refusal", confounded=True)
            assert [item["path"] for item in confounded["inputs"]] == list(
                map(str, paths)
            )
            await call(
                "plan_rna_workflow",
                {
                    **confounded,
                    "parameters": spec.model_dump(mode="json"),
                    "transcript_reference": {
                        **confounded["transcript_reference"],
                        "reference_sha256": spec.mapping_sha256,
                    },
                },
                error=True,
            )
            await call(
                "plan_rna_workflow",
                {
                    **dtu,
                    "parameters": {**dtu["parameters"], "unknown": True},
                },
                error=True,
            )

            for payload in [*payloads.values(), spatial_without_types]:
                operation = payload["operation"]
                plan = await call("plan_rna_workflow", payload)
                assert plan["status"] == "planned"
                assert plan["runtime"]["oci_digest"] is None
                assert len(plan["runtime"]["runtime_sha256"]) == 64
                answer = await call(
                    "check_answerability",
                    {
                        "request": plan["answerability"]["request"],
                        "inputs": payload["inputs"],
                        "local_solver": {
                            key: payload[key]
                            for key in (
                                "operation",
                                "parameters",
                                "transcript_reference",
                            )
                            if key in payload
                        },
                    },
                )
                assert answer["status"] == "ready"
                assert operations[operation] <= {
                    item["kind"] for item in answer["attested_measurements"]
                }
                if operation == "spatial_pattern":
                    coordinates = next(
                        item
                        for item in answer["attested_measurements"]
                        if item["kind"] == "spatial_coordinates"
                    )
                    assert coordinates["coordinate_frame"] == "registered-tissue"
                    assert coordinates["coordinate_units"] == "um"
                identity = {
                    key: plan[key] for key in ("session_id", "plan_id", "plan_sha256")
                }
                await call(
                    "execute_reviewed_plan",
                    {
                        **identity,
                        "plan_sha256": "0" * 64,
                    },
                    error=True,
                )
                run = await call("execute_reviewed_plan", identity)
                run_key = {key: run[key] for key in ("session_id", "run_id")}
                async with asyncio.timeout(120):
                    while run["status"] in {"queued", "running"}:
                        await asyncio.sleep(0.08)
                        run = await call("get_run_status", run_key)
                assert run["status"] == "completed", run
                receipt = await call("get_execution_receipt", run_key)
                assert receipt["operation"] == operation
                assert receipt["clinical_release_allowed"] is False
                expected = (
                    "not_evaluated"
                    if operation in {"bulk_deconvolution", "phenotype_prediction"}
                    else "verified"
                )
                assert receipt["probability_bounds_status"] == expected
                assert len(canonical(receipt)) <= 64 * 1024
                assert all(
                    value is None or isinstance(value, (int, float, str, bool))
                    for value in receipt["metrics"].values()
                )
                assert receipt["summary_tables"]
                assert all(
                    len(table["rows"]) <= 20 for table in receipt["summary_tables"]
                )
                artifact = receipt["result_artifact"]
                path = workspace / artifact["path"]
                assert path.is_relative_to(workspace / "runs" / run["run_id"])
                data = path.read_bytes()
                assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
                result = json.loads(data)
                assert result["clinical_release_allowed"] is False
                if operation == "spatial_pattern":
                    assert result["status"] == "completed"
                    assert result["gene_statistics"]
                    assert result["input_sha256s"]["metadata"] == (
                        payload["parameters"]["metadata_sha256"]
                    )
                    assert bool(result["neighborhood_enrichment"]) == (
                        payload is not spatial_without_types
                    )

                if operation == "scrna_ccc":
                    path.chmod(0o600)
                    path.write_bytes(data + b" ")
                    refusal = await call("get_execution_receipt", run_key, error=True)
                    assert refusal["reason"] == "solver_result_artifact_changed"

            planned = await call("plan_rna_workflow", dtu)
            identity = {
                key: planned[key] for key in ("session_id", "plan_id", "plan_sha256")
            }
            changed = Path(dtu["inputs"][0]["path"])
            with changed.open("ab") as output:
                output.write(b"\n")
            run = await call("execute_reviewed_plan", identity)
            run_key = {key: run[key] for key in ("session_id", "run_id")}
            async with asyncio.timeout(120):
                while run["status"] in {"queued", "running"}:
                    await asyncio.sleep(0.08)
                    run = await call("get_run_status", run_key)
            assert run["status"] == "failed"
            assert run["warnings"]
            await call("get_execution_receipt", run_key, error=True)

    asyncio.run(roundtrip())


def test_h5ad_attestation_checks_values_and_declarations(workspace_tmp_path):
    import numpy as np
    from anndata import AnnData

    from devbench.platform.plugin_mcp import Answerability, Plugin

    inputs = workspace_tmp_path / "inputs"
    inputs.mkdir(mode=0o700)
    path = inputs / "observations.h5ad"
    annotated = AnnData(np.array([[2], [4], [2], [1]], dtype=np.int32))
    annotated.obsm["spatial"] = np.array([[-1, 0], [0, 0], [1, 1], [0, 2]])
    annotated.layers["lognorm"] = np.log1p(annotated.X)
    annotated.obs["cell_type"] = ["A", "B", "A", "B"]
    annotated.obs["donor_id"] = ["d1", "d2", "d3", "d4"]
    annotated.obs["label"] = [0, 1, 0, 1]
    annotated.obs["perturbation"] = ["control", "guide", "control", "guide"]
    annotated.write_h5ad(path)
    plugin = Plugin(workspace_tmp_path / "workspace", inputs, None)

    def measured(family, question, assay, **hints):
        answer = plugin.answer(
            Answerability.model_validate(
                {
                    "request": {
                        "family_id": family,
                        "question": question,
                        "assay": assay,
                        "entry_point": "annotated_object",
                    },
                    "inputs": [{"path": str(path), "data_classification": "synthetic"}],
                    **hints,
                }
            )
        )
        assert answer["status"] != "ready"
        return {m["kind"]: m for m in answer["attested_measurements"]}

    spatial = measured(
        9,
        "spatial_pattern",
        "spatial_transcriptomics",
        coordinate_frame="registered-tissue",
        coordinate_units="um",
    )
    assert spatial["spatial_coordinates"]["slot"] == "obsm/spatial"
    assert "spatial_coordinates" not in measured(
        9,
        "spatial_pattern",
        "spatial_transcriptomics",
        coordinate_frame="UMAP",
        coordinate_units="um",
    )
    assert "phenotype_outcomes" in measured(18, "phenotype_prediction", "bulk_rna_seq")
    assert "control_assignments" not in measured(
        11,
        "perturbation_response",
        "single_cell_rna_seq",
        non_targeting_control="nonexistent",
    )
    assert "control_assignments" in measured(
        11,
        "perturbation_response",
        "single_cell_rna_seq",
        non_targeting_control="control",
    )
    assert "cell_type_expression" in measured(
        10,
        "cell_communication",
        "single_cell_rna_seq",
        normalized_layer="lognorm",
        normalization="library_size_log1p",
    )
