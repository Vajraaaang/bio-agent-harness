"""Run the synthetic RNA-seq walkthroughs over local STDIO MCP."""

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXAMPLES = Path(__file__).resolve().parent


def input_file(path: Path, role: str = "expression") -> dict:
    return {"path": str(path), "role": role, "data_classification": "synthetic"}


async def bulk(call, input_root: Path) -> dict:
    counts = input_file(input_root / "counts.csv")
    design = input_file(input_root / "design.csv", "metadata")
    await call("inspect_rna_inputs", {"inputs": [counts, design]})
    answer = await call(
        "check_answerability",
        {
            "request": {
                "family_id": 1,
                "question": "differential_expression",
                "assay": "bulk_rna_seq",
                "entry_point": "count_matrix",
            },
            "inputs": [counts, design],
            "local_design": {
                "metadata": design,
                "formula": "~ condition + batch",
                "contrast": {
                    "factor": "condition",
                    "numerator": "treated",
                    "denominator": "control",
                },
                "biological_unit": "donor_id",
                "batch_columns": ["batch"],
            },
        },
    )
    if answer["reason"] != "rank_deficient_or_confounded_design":
        raise RuntimeError(f"Unexpected bulk design outcome ({answer['reason']})")
    plan = await call(
        "plan_rna_workflow",
        {
            "operation": "matrix_audit",
            "objective": "Describe the stored synthetic bulk counts.",
            "measurement": "non_umi_counts",
            "inputs": [counts],
        },
    )
    identity = {key: plan[key] for key in ("session_id", "plan_id", "plan_sha256")}
    run = await call("execute_reviewed_plan", identity)
    run_key = {key: run[key] for key in ("session_id", "run_id")}
    while run["status"] in {"queued", "running"}:
        await asyncio.sleep(0.5)
        run = await call("get_run_status", run_key)
    if run["status"] != "completed":
        raise RuntimeError(
            f"Matrix audit ended with status {run['status']}. See trace.json."
        )
    receipt = await call("get_execution_receipt", run_key)
    return {
        "bulk_de": {"status": answer["status"], "reason": answer["reason"]},
        "matrix_audit": {
            "status": run["status"],
            "metrics": receipt["metrics"],
            "replay_bundle_sha256": receipt["replay_bundle_sha256"],
        },
    }


def write_single_cell_fixture(input_root: Path) -> dict:
    import numpy as np
    import pandas as pd
    from anndata import AnnData

    observations = []
    for condition in ("control", "treated"):
        for donor_index in range(1, 4):
            donor = f"{condition}_{donor_index}"
            for cell_index in range(12):
                extra_capture = donor_index == 1 and cell_index >= 6
                label = (
                    "Target"
                    if cell_index < (3 if condition == "control" else 5)
                    else "Reference"
                )
                observations.append(
                    {
                        "donor_id": donor,
                        "capture_id": f"{donor}_{'b' if extra_capture else 'a'}",
                        "condition": condition,
                        "cell_type": label,
                        "batch": "batch_b"
                        if extra_capture or donor_index == 2
                        else "batch_a",
                    }
                )
    obs = pd.DataFrame(observations)
    obs.index = [f"cell_{index}" for index in range(len(obs))]
    counts = np.random.default_rng(13).poisson(4, (len(obs), 6)).astype("int32")
    cells = AnnData(counts, obs=obs)
    cells.layers["counts"] = counts.copy()
    input_root.mkdir(mode=0o700)
    cells.write_h5ad(input_root / "cells.h5ad")
    return {
        "cells": cells.n_obs,
        "captures": int(obs["capture_id"].nunique()),
        "donors_by_condition": obs.groupby("condition")["donor_id"].nunique().to_dict(),
        "target_cells_by_condition": obs[obs["cell_type"] == "Target"]
        .groupby("condition")
        .size()
        .to_dict(),
    }


async def single_cell(call, input_root: Path) -> dict:
    inputs = [input_file(input_root / "cells.h5ad")]
    await call("inspect_rna_inputs", {"inputs": inputs})
    results = {}
    for name, family, question in (
        ("pseudobulk", 1, "differential_cell_state"),
        ("composition", 3, "compositional_abundance"),
    ):
        answer = await call(
            "check_answerability",
            {
                "request": {
                    "family_id": family,
                    "question": question,
                    "assay": "single_cell_rna_seq",
                    "entry_point": "annotated_object",
                },
                "inputs": inputs,
                "measurement": "umi_counts",
                "count_provenance": "Integer counts generated by this example.",
                "counts_layer": "counts",
            },
        )
        results[name] = {key: answer[key] for key in ("status", "reason")}
    return results


async def demo(scenario: str, output: Path) -> dict:
    input_root = (
        EXAMPLES / "bulk-rnaseq" / "data" if scenario == "bulk" else output / "inputs"
    )
    fixture = (
        write_single_cell_fixture(input_root) if scenario == "single-cell" else None
    )
    workspace = output / "workspace"
    options = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "devbench.platform.cli",
            "plugin-mcp",
            "--input-root",
            str(input_root),
            "--workspace",
            str(workspace),
        ],
        env={
            key: os.environ[key]
            for key in ("PATH", "PYTHONPATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH")
            if key in os.environ
        },
    )
    trace = []
    try:
        async with asyncio.timeout(90), stdio_client(options) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()

                async def call(name, arguments):
                    reply = await client.call_tool(name, arguments)
                    response = dict(reply.structuredContent or {})
                    preview = response.pop("figure_preview", None)
                    if preview and preview.startswith("data:image/png;base64,"):
                        image = base64.b64decode(
                            preview.split(",", 1)[1], validate=True
                        )
                        (output / "figure.png").write_bytes(image)
                        response["figure_preview"] = "figure.png"
                    trace.append(
                        {
                            "tool": name,
                            "arguments": arguments,
                            "response": response,
                            "is_error": reply.isError,
                        }
                    )
                    if reply.isError or not response:
                        reason = response.get("reason", "missing response")
                        raise RuntimeError(f"{name} failed ({reason}).")
                    return response

                if scenario == "bulk":
                    return await bulk(call, input_root)
                return {"fixture": fixture, **(await single_cell(call, input_root))}
    finally:
        (output / "trace.json").write_text(
            json.dumps(trace, indent=2) + "\n", encoding="utf-8"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=("bulk", "single-cell"))
    parser.add_argument(
        "--output", required=True, type=Path, help="New absolute output directory"
    )
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.exists():
        parser.error("--output must be a new absolute directory")
    args.output.mkdir(mode=0o700, parents=True)
    summary = asyncio.run(demo(args.scenario, args.output.resolve()))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
