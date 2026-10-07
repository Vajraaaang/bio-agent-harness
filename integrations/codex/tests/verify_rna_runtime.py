"""Actual local STDIO and tiny RNA workflow checks; no model or private data."""
import asyncio
import csv
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write_json(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def inputs(folder, *, cellular=False):
    folder.mkdir()
    n = 32 if cellular else 8
    ids = [f"s{i}" for i in range(n)]
    rows = [{"sample_id": sample, "cell_id": sample,
        "donor_id": f"d{i // 4}" if cellular else f"d{i}",
        "specimen_id": f"p{i // 4}" if cellular else f"p{i}",
        "library_id": f"l{i // 4}" if cellular else f"l{i}",
        "condition": "A" if i < n // 2 else "B", "embryonic_age": f"E{8+i}",
        ("capture_batch" if cellular else "batch"): "one"} for i, sample in enumerate(ids)]
    with (folder / "metadata.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    genes = (["markerA", "markerB"] if cellular else []) + [f"g{i}" for i in range(12)]
    with (folder / "expression.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["gene_id", *ids])
        for j, gene in enumerate(genes):
            values = ([80 if (i % 4 < 2) == (j == 0) else 1 for i in range(n)] if cellular and j < 2
                      else [10 + (i * 3 + j * 5) % 9 + (j % 3 if i >= n // 2 else 0) for i in range(n)])
            writer.writerow([gene, *values])
    annotations = {"cell_annotation_reference": {"reference_identity": "public-software-fixture",
        "reference_scope": "artificial broad-type markers", "markers": [
            {"label": "typeA", "gene_ids": ["markerA"]}, {"label": "typeB", "gene_ids": ["markerB"]}],
        "query_cell_labels_supplied": False}} if cellular else {}
    write_json(folder / "annotations.json", annotations)
    task = {"measurement": "umi_counts" if cellular else "raw_counts", "population_unit_label": "donor_id",
        "observation_id_label": "sample_id", "context": {"biological_unit": "donor", "condition_labels": ["A", "B"]},
        "analysis_columns": {"condition": "condition", "time": "numeric_time", "dose": None, "covariate": None,
                             "population_unit": "donor_id", "batches": ["capture_batch" if cellular else "batch"]},
        "gene_ids": genes, "questions": [{"target_id": "cell_state_effect" if cellular else "mean_contrast",
            "estimand": "Software-fixture donor contrast", "units": "log2 relative expression", "contrast": "B minus A",
            "reference_population": "Declared artificial donors", "prediction_reference": "Observed fixture population"}]}
    return {"question": "Estimate B minus A on the declared software fixture", "assay": "scrna" if cellular else "bulk",
            "public_task": task, "inputs": {"expression": str(folder / "expression.csv"),
            "metadata": str(folder / "metadata.csv"), "annotations": str(folder / "annotations.json")}}


async def check_mcp(server, bulk, output):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    parameters = StdioServerParameters(command=server["command"], args=server["args"],
                                      cwd=server["cwd"], env=server["env"])
    with (output / "mcp.stderr").open("w") as errors:
        async with stdio_client(parameters, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                write_json(output / "mcp-tools.json", [tool.model_dump(mode="json") for tool in tools])
                assert len(tools) == 10
                qc = await session.call_tool("audit_expression_quality", {
                    "counts_path": bulk["inputs"]["expression"], "metadata_path": bulk["inputs"]["metadata"]})
                write_json(output / "mcp-qc.json", qc.model_dump(mode="json"))
                assert not qc.isError and qc.structuredContent is not None
                assert json.loads(qc.content[0].text) == qc.structuredContent
                assert qc.structuredContent["metrics"]["sample_count"] == 8
                assert qc.structuredContent["metrics"]["nonnegative_integer_expression"] is True
                assert qc.structuredContent["status"] != "fail"
                design = await session.call_tool("validate_design_matrix", {
                    "metadata_csv_path": bulk["inputs"]["metadata"], "formula_str": "1 + numeric_time"})
                write_json(output / "mcp-design.json", design.model_dump(mode="json"))
                assert not design.isError and design.structuredContent["is_full_rank"] is True
                refused = await session.call_tool("validate_design_matrix", {
                    "metadata_csv_path": str(output / "outside-workspace.csv"), "formula_str": "1 + numeric_time"})
                write_json(output / "mcp-refusal.json", refused.model_dump(mode="json"))
                assert refused.isError and "outside the assigned workspace" in refused.content[0].text
    return {"registered_tools": len(tools), "real_qc": True, "real_design_check": True, "out_of_scope_refused": True}


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "validation"
    output.mkdir(exist_ok=False)
    workspace = output / "analysis"
    workspace.mkdir()
    script = root / "devharness" / "scripts" / "configure.py"
    spec = importlib.util.spec_from_file_location("plugin_setup", script)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    binding = setup.configure(Path(sys.executable), workspace, output / "configured" / "devharness")
    write_json(output / "binding.json", binding)
    server = json.loads((Path(binding["plugin"]) / ".codex-plugin" / "plugin.json").read_text())["mcpServers"]["devharness"]
    bulk = inputs(workspace / "bulk")
    cellular = inputs(workspace / "cellular", cellular=True)
    environment = dict(os.environ, **setup.THREADS,
        MPLCONFIGDIR=str(output / "matplotlib-cache"), XDG_CACHE_HOME=str(output / "cache"))
    server["env"].update({"MPLCONFIGDIR": environment["MPLCONFIGDIR"], "XDG_CACHE_HOME": environment["XDG_CACHE_HOME"]})
    started = time.monotonic()
    result = {"scope": "local_plugin_components_and_synthetic_RNA_fixtures_not_live_Codex_or_biological_validation",
        "live_model_calls": 0, "codex_app_installation_tested": False, "peak_rss_mb": None, "cost_usd": None}
    try:
        result["mcp"] = asyncio.run(asyncio.wait_for(check_mcp(server, bulk, output), timeout=90))
        for label, data in (("bulk", bulk), ("cellular", cellular)):
            input_dir = workspace / label
            write_json(input_dir / "spec.json", data)
            actions = [{"kind": "tool", "tool": name} for name in (
                "audit_expression_quality", "validate_biological_units",
                "pseudobulk_membership_audit" if label == "cellular" else "validate_design_matrix",
                "cell_type_state_analysis" if label == "cellular" else "mean_condition_analysis")]
            actions.append({"kind": "finish"})
            write_json(input_dir / "actions.json", actions)
            command = [server["command"], "-I", "-B", "-m", "devbench.analysis.episode_cli",
                "--spec", str(input_dir / "spec.json"), "--actions", str(input_dir / "actions.json"),
                "--output", str(workspace / (label + "-episode")), "--allow-data-execution"]
            write_json(output / (label + "-command.json"), {"argv": command, "timeout_seconds": 120})
            completed = subprocess.run(command, cwd=workspace, env=environment, capture_output=True, text=True, timeout=120)
            (output / (label + ".stdout")).write_text(completed.stdout)
            (output / (label + ".stderr")).write_text(completed.stderr)
            assert completed.returncode == 0, label + " RNA fixture failed; original outputs preserved"
            response = json.loads(completed.stdout)
            assert response["observation"]["outcome"] == "completed"
            from devbench.analysis.episodes import verify_episode
            checked = verify_episode(workspace / (label + "-episode"), response["receipt"]["root_hash"])
            assert checked["valid"]
            result[label] = {"outcome": response["observation"]["outcome"], "receipt": response["receipt"], "verified": True}
        result["passed"] = True
    except BaseException as error:
        result.update(passed=False, failure={"type": type(error).__name__, "message": str(error)[:2000]})
        raise
    finally:
        result["wall_seconds"] = time.monotonic()-started
        write_json(output / "verification.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
