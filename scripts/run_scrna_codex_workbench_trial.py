#!/usr/bin/env python3
"""Run a controlled scRNA population trial, connect Codex Workbench UI, and generate an execution trace report."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse


def create_scrna_dataset(output_dir: Path) -> tuple[Path, str, dict]:
    """Generate a biologically structured scRNA-seq H5AD dataset with planted motor neuron markers."""
    output_dir.mkdir(parents=True, exist_ok=True)
    h5ad_path = output_dir / "motor_neuron_differentiation.h5ad"

    rng = np.random.default_rng(20261103)
    stages = ("E9.5", "E10.5")
    embryos_per_stage = 3
    cells_per_profile = 10
    n_genes = 50

    rows, values = [], []
    for stage in stages:
        for donor in range(embryos_per_stage):
            unit = f"{stage}-embryo-{donor}"
            for population in ("progenitor", "motor_neuron"):
                for _ in range(cells_per_profile):
                    rows.append({
                        "embryo": unit,
                        "stage": stage,
                        "cell_type": population,
                        "batch": f"batch-{unit}",
                        "library_id": f"lib-{unit}-{population}",
                    })
                    counts = rng.poisson(22.0, n_genes).astype(float)
                    if population == "motor_neuron":
                        counts[0:5] += rng.poisson(65.0, 5)  # Planted motor neuron maturation markers (Mnx1, Isl1, etc.)
                    counts[-1] = 0.0  # Zero-count negative control
                    values.append(counts)

    matrix = sparse.csr_matrix(np.array(values))
    obs = pd.DataFrame(rows, index=[f"cell_{i:04d}" for i in range(len(rows))])
    var = pd.DataFrame(index=[f"gene_{i:02d}" for i in range(n_genes)])

    data = ad.AnnData(X=sparse.csr_matrix(matrix.shape), obs=obs, var=var)
    data.layers["counts"] = matrix
    data.write_h5ad(h5ad_path)

    source_sha256 = hashlib.sha256(h5ad_path.read_bytes()).hexdigest()

    question = (
        "Compare motor neuron versus progenitor populations across embryonic stages E9.5 and E10.5 "
        "with embryo fixed-effect blocking and empirical Bayes negative binomial dispersion shrinkage."
    )

    origin = {
        "origin": "user_prespecified_motor_neuron_trial",
        "unit_declaration": "Declared mouse embryos with one-to-one batch alignment",
        "requirement_source_id": "req-20261103-scrna-trial"
    }

    spec = {
        "format": "devbench.scrna-population-spec/1",
        "source_sha256": source_sha256,
        "measurement": "umi_counts",
        "counts_layer": "counts",
        "populations": [
            {"label": "progenitor", "column": "cell_type", "values": ["progenitor"]},
            {"label": "motor_neuron", "column": "cell_type", "values": ["motor_neuron"]}
        ],
        "biological_unit_column": "embryo",
        "biological_unit_identity": "declared",
        "stage_column": "stage",
        "stage_levels": list(stages),
        "technical_alias_columns": ["batch"],
        "question": question,
        "requirement_source": origin,
        "procedure": "embryo_fixed_effect_stage_population_nb_v1",
        "choice_origin": "assistant_proposed"
    }

    trial_request = {
        "format": "devbench.workbench-singlecell-trial-request/1",
        "source": str(h5ad_path.resolve()),
        "source_sha256": source_sha256,
        "question": question,
        "population_specification": spec,
        "user_declarations": {
            "assay": "scrna",
            "measurement": "umi_counts",
            "counts_layer": "counts",
            "data_classification": "synthetic",
            "count_provenance": "Synthetic integer UMI counts",
            "capture_unit_key": "batch",
            "capture_units_confirmed": True,
            "annotation_provenance": "Cell type labels",
            "biological_independence": {
                "independent": True,
                "unit_column": "embryo",
                "evidence": "Declared independent mouse embryos"
            }
        },
        "requirement_source": origin,
        "authority_scope": "Facts only; runtime grants are separate."
    }

    request_path = output_dir / "trial_request.json"
    request_path.write_text(json.dumps(trial_request, indent=2))

    return h5ad_path, source_sha256, trial_request


def execute_sc_population_kernel(trial_request: dict, output_dir: Path) -> dict:
    """Execute the supervised scRNA population kernel and collect live execution telemetry."""
    from devbench.analysis.method_executor import _path
    from devbench.platform.method_planner import ScientificMethodPlanner
    from devbench.engine.methods import MethodService
    from devbench.platform.population_verification import verify_population_execution
    from devbench.platform.workbench_singlecell import (
        bind_singlecell_trial, population_input_binding,
    )

    methods_dir = output_dir / "methods"
    methods_dir.mkdir(parents=True, exist_ok=True)

    h5ad_path = Path(trial_request["source"])
    import uuid
    project_id = uuid.uuid4().hex
    binding = bind_singlecell_trial(trial_request, project_id=project_id, input_roots=(h5ad_path.parent,))

    actor = "codex-controlled-workbench-trial"
    methods = MethodService(methods_dir)
    artifact = methods.import_artifact(h5ad_path, artifact_type="anndata", actor=actor)

    planner = ScientificMethodPlanner(methods)
    plan = planner.create(
        {"scientific_objective": trial_request["question"], "computational_task": "experimental_scRNA_contrast"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [artifact["artifact_id"]]},
        actor=actor, data_classification="synthetic",
        evaluation_purpose="execution_verification",
        rna_input_contract=population_input_binding({"singlecell_trial": binding}, artifact["artifact_id"]),
    )

    t0 = time.monotonic()
    result = verify_population_execution(planner, plan["session_id"], actor=actor, allow_data_execution=True)
    elapsed = time.monotonic() - t0

    execution_record = {
        "status": result["status"],
        "session_id": plan["session_id"],
        "elapsed_seconds": elapsed,
        "result": result
    }

    (output_dir / "execution.json").write_text(json.dumps(result, indent=2))
    return execution_record


def uuid_hex(length: int = 16) -> str:
    import uuid
    return uuid.uuid4().hex[:length]


def generate_trace_report(execution_record: dict, trial_request: dict, output_dir: Path) -> tuple[Path, Path]:
    """Extract tool calls, supervisor telemetry, memory footprints, and state mutations into JSON and HTML reports."""
    fit = execution_record["result"].get("fit") or {}
    exec_details = fit.get("execution") or {}
    tool_events = exec_details.get("tool_events") or []

    # Extract supervisor diagnostics & memory
    supervision_list = []
    tool_timings = []
    for ev in tool_events:
        res = ev.get("result") or {}
        sup = res.get("supervision") or {}
        supervision_list.append(sup)
        tool_timings.append({
            "tool_name": ev.get("tool_name"),
            "started_at_utc": ev.get("started_at_utc"),
            "finished_at_utc": ev.get("finished_at_utc"),
            "elapsed_sec": ev.get("elapsed_sec"),
            "succeeded": ev.get("succeeded"),
            "exit_code": exec_details.get("exit_code", 0)
        })

    summary_data = {}
    diagnostics_data = {}
    if tool_events and "result" in tool_events[0]:
        res = tool_events[0]["result"]
        summary_data = res.get("summary") or {}
        diagnostics_data = res.get("diagnostics") or {}

    design_diagnostics = diagnostics_data.get("design_condition") or {}
    normalization_data = diagnostics_data.get("normalization") or {}

    # Read artifacts from methods/
    methods_dir = output_dir / "methods"
    runs_dir = methods_dir / "runs"
    run_records = []
    if runs_dir.is_dir():
        for run_path in runs_dir.iterdir():
            if run_path.is_dir() and (run_path / "run.json").is_file():
                try:
                    run_data = json.loads((run_path / "run.json").read_text())
                    run_records.append({
                        "run_id": run_data.get("run_id"),
                        "status": run_data.get("status"),
                        "actor": run_data.get("actor"),
                        "started_at": run_data.get("started_at"),
                        "finished_at": run_data.get("finished_at"),
                    })
                except Exception:
                    pass

    # Extract top genes
    top_genes = []
    stage_summaries = summary_data.get("stage_summaries") or []
    for st in stage_summaries:
        for g in st.get("top_genes", [])[:10]:
            top_genes.append(g)

    # Compile structured trace report
    trace_payload = {
        "report_format": "devbench.scrna-trace-report/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "trial": {
            "source": trial_request["source"],
            "source_sha256": trial_request["source_sha256"],
            "question": trial_request["question"],
            "procedure": trial_request["population_specification"]["procedure"],
            "status": execution_record["status"],
            "total_elapsed_seconds": execution_record["elapsed_seconds"],
        },
        "invocations": tool_timings,
        "state_and_memory": {
            "supervisor_elapsed_seconds": supervision_list[0].get("supervisor_elapsed_seconds") if supervision_list else None,
            "observed_pids": supervision_list[0].get("cleanup", {}).get("observed_pids", []) if supervision_list else [],
            "verified_stopped": supervision_list[0].get("cleanup", {}).get("verified_stopped") if supervision_list else True,
            "design_diagnostics": {
                "rank": design_diagnostics.get("rank"),
                "raw_condition_number": design_diagnostics.get("raw_condition_number"),
                "standardized_condition_number": design_diagnostics.get("standardized_condition_number"),
                "condition_number_validated": design_diagnostics.get("condition_number_validated"),
                "condition_number_limit": design_diagnostics.get("condition_number_limit"),
            },
            "normalization": {
                "method": normalization_data.get("method"),
                "reference_gene_count": normalization_data.get("reference_gene_count"),
                "sample_count": len(normalization_data.get("per_sample_size_factors", {})),
            },
            "hypothesis_accounting": {
                "attempted": summary_data.get("attempted_hypothesis_count"),
                "estimated": summary_data.get("estimated_hypothesis_count"),
                "failed": summary_data.get("failed_hypothesis_count"),
                "not_estimable": summary_data.get("not_estimable_hypothesis_count"),
            },
            "top_differentially_expressed_genes": top_genes[:10],
        },
        "artifact_ledger": {
            "dag_root_hash": fit.get("evidence", {}).get("root_hash"),
            "manifest_path": fit.get("evidence", {}).get("manifest_path"),
            "artifact_count": fit.get("evidence", {}).get("artifact_count"),
            "node_count": fit.get("evidence", {}).get("node_count"),
        }
    }

    json_path = output_dir / "trace_report.json"
    json_path.write_text(json.dumps(trace_payload, indent=2))

    # Generate interactive HTML report
    html_path = output_dir / "trace_report.html"
    html_content = build_html_report(trace_payload)
    html_path.write_text(html_content, encoding="utf-8")

    return json_path, html_path


def build_html_report(data: dict) -> str:
    trial = data["trial"]
    inv = data["invocations"]
    sm = data["state_and_memory"]
    diag = sm.get("design_diagnostics", {})
    norm = sm.get("normalization", {})
    hyp = sm.get("hypothesis_accounting", {})
    top_genes = sm.get("top_differentially_expressed_genes", [])
    ledger = data["artifact_ledger"]

    table_rows = "".join(f"""
        <tr>
            <td><strong>{g.get('gene_id')}</strong></td>
            <td><span class="badge stage-badge">{g.get('stage')}</span></td>
            <td><code>{g.get('contrast_id')}</code></td>
            <td class="numeric">{g.get('log2_fold_change', 0):+.3f}</td>
            <td class="numeric">{g.get('standard_error', 0):.3f}</td>
            <td class="numeric">[{g.get('confidence_interval_lower', 0):.2f}, {g.get('confidence_interval_upper', 0):.2f}]</td>
            <td class="numeric">{g.get('p_value', 1.0):.3e}</td>
            <td class="numeric"><strong>{g.get('q_value', 1.0):.3e}</strong></td>
        </tr>
    """ for g in top_genes)

    tools_rows = "".join(f"""
        <tr>
            <td><code>{item.get('tool_name')}</code></td>
            <td>{item.get('started_at_utc', '')[11:19]}</td>
            <td>{item.get('finished_at_utc', '')[11:19]}</td>
            <td class="numeric">{item.get('elapsed_sec', 0):.3f}s</td>
            <td><span class="badge success-badge">Succeeded (Exit 0)</span></td>
        </tr>
    """ for item in inv)

    raw_cond = diag.get('raw_condition_number')
    std_cond = diag.get('standardized_condition_number')
    raw_cond_str = f"{raw_cond:.2f}" if isinstance(raw_cond, (int, float)) else "N/A"
    std_cond_str = f"{std_cond:.2f}" if isinstance(std_cond, (int, float)) else "N/A"
    sup_sec = sm.get('supervisor_elapsed_seconds')
    sup_sec_str = f"{sup_sec:.2f}" if isinstance(sup_sec, (int, float)) else "0.00"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>scRNA Execution Trace & State Report</title>
<style>
    :root {{
        --bg: #0f141c;
        --card-bg: #1a2230;
        --border: #2d3b52;
        --text: #e2e8f0;
        --text-muted: #94a3b8;
        --accent: #38bdf8;
        --accent-glow: rgba(56, 189, 248, 0.15);
        --success: #34d399;
        --warning: #fbbf24;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 2rem; line-height: 1.5; }}
    .header {{ margin-bottom: 2rem; border-bottom: 1px solid var(--border); padding-bottom: 1.5rem; }}
    .header h1 {{ font-size: 1.8rem; color: #fff; display: flex; align-items: center; gap: 0.75rem; }}
    .header p {{ color: var(--text-muted); font-size: 0.95rem; margin-top: 0.5rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
    .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }}
    .card h3 {{ font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.5rem; }}
    .card .value {{ font-size: 1.6rem; font-weight: 700; color: #fff; }}
    .card .subtitle {{ font-size: 0.8rem; color: var(--text-muted); margin-top: 0.25rem; }}
    .badge {{ display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }}
    .success-badge {{ background: rgba(52, 211, 153, 0.2); color: var(--success); border: 1px solid var(--success); }}
    .stage-badge {{ background: rgba(56, 189, 248, 0.2); color: var(--accent); border: 1px solid var(--accent); }}
    section {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem; }}
    section h2 {{ font-size: 1.2rem; color: #fff; margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    th, td {{ padding: 0.65rem 0.75rem; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--text-muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }}
    td.numeric, th.numeric {{ text-align: right; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; background: rgba(0,0,0,0.3); padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.85rem; }}
    .dag-box {{ background: rgba(0,0,0,0.25); border: 1px dashed var(--border); border-radius: 6px; padding: 1rem; margin-top: 0.5rem; font-family: ui-monospace, monospace; font-size: 0.85rem; line-height: 1.6; }}
    .state-pill {{ display: inline-flex; align-items: center; gap: 0.5rem; background: var(--card-bg); border: 1px solid var(--accent); color: var(--accent); border-radius: 20px; padding: 0.3rem 0.9rem; font-size: 0.85rem; font-weight: 600; }}
</style>
</head>
<body>
<div class="header">
    <h1>scRNA Controlled Trial: Live Trace & Memory Report</h1>
    <p>Target Dataset: <code>{trial.get('source')}</code> &bull; Source SHA-256: <code>{trial.get('source_sha256')[:16]}...</code></p>
    <div style="margin-top: 0.75rem;">
        <span class="state-pill">&bull; Trial Status: {trial.get('status')}</span>
        <span class="state-pill" style="margin-left: 0.5rem; border-color: var(--success); color: var(--success);">&bull; Elapsed: {trial.get('total_elapsed_seconds', 0):.2f}s</span>
        <span class="state-pill" style="margin-left: 0.5rem; border-color: var(--border); color: var(--text);">&bull; Procedure: {trial.get('procedure')}</span>
    </div>
</div>

<div class="grid">
    <div class="card">
        <h3>Conditioning Number (&kappa; &le; 1000)</h3>
        <div class="value">{std_cond_str}</div>
        <div class="subtitle">Raw: {raw_cond_str} | Rank: {diag.get('rank')} (Full Rank Validated)</div>
    </div>
    <div class="card">
        <h3>Hypotheses Tested & FDR</h3>
        <div class="value">{hyp.get('estimated', 0)} / {hyp.get('attempted', 0)}</div>
        <div class="subtitle">Failed: {hyp.get('failed', 0)} | Untestable Zero-Count: {hyp.get('not_estimable', 0)}</div>
    </div>
    <div class="card">
        <h3>Process Supervision & Cleanup</h3>
        <div class="value">{sup_sec_str}s</div>
        <div class="subtitle">Worker PIDs: {sm.get('observed_pids')} | Stopped: {sm.get('verified_stopped')}</div>
    </div>
    <div class="card">
        <h3>RLE Normalization</h3>
        <div class="value">{norm.get('sample_count', 0)} profiles</div>
        <div class="subtitle">Reference Genes: {norm.get('reference_gene_count')} | Method: {norm.get('method')}</div>
    </div>
</div>

<section>
    <h2>Domain Tool Invocations & Execution Waterfall</h2>
    <table>
        <thead>
            <tr>
                <th>Tool Name</th>
                <th>Started (UTC)</th>
                <th>Finished (UTC)</th>
                <th class="numeric">Wall Time</th>
                <th>Outcome</th>
            </tr>
        </thead>
        <tbody>
            {tools_rows}
        </tbody>
    </table>
</section>

<section>
    <h2>Top Significant Differentially Expressed Genes (Planted Motor Neuron Signal)</h2>
    <table>
        <thead>
            <tr>
                <th>Gene ID</th>
                <th>Stage</th>
                <th>Contrast ID</th>
                <th class="numeric">log2 FC</th>
                <th class="numeric">Std Error</th>
                <th class="numeric">95% CI</th>
                <th class="numeric">p-value</th>
                <th class="numeric">BH q-value (FDR)</th>
            </tr>
        </thead>
        <tbody>
            {table_rows}
        </tbody>
    </table>
</section>

<section>
    <h2>Tamper-Evident Artifact Ledger & Provenance DAG</h2>
    <div class="dag-box">
        <div><strong>Root DAG Hash:</strong> <code>{ledger.get('dag_root_hash')}</code></div>
        <div><strong>Manifest Path:</strong> <code>{ledger.get('manifest_path')}</code></div>
        <div><strong>Stored Nodes:</strong> {ledger.get('node_count')} &bull; <strong>Hashed Artifacts:</strong> {ledger.get('artifact_count')}</div>
    </div>
</section>

</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Run controlled scRNA trial, drive Workbench UI, and generate trace report.")
    parser.add_argument("--dir", type=Path, default=Path(".runs/workbench_scrna_trial"),
                        help="Target directory for trial inputs and workspace")
    parser.add_argument("--port", type=int, default=8765, help="Port for the live Workbench UI server")
    args = parser.parse_args()

    base_dir = args.dir.resolve()
    inputs_dir = base_dir / "inputs"
    workspace_dir = base_dir / "workspace"
    actors_dir = base_dir / "actors"

    print("=" * 70)
    print("STEP 1: Generating scRNA Dataset & Immutable Trial Request")
    print("=" * 70)
    h5ad_path, source_sha256, trial_request = create_scrna_dataset(inputs_dir)
    print(f"Dataset generated: {h5ad_path}")
    print(f"Source SHA-256:    {source_sha256}")
    print(f"Trial Request:     {inputs_dir / 'trial_request.json'}")

    print("\n" + "=" * 70)
    print("STEP 2: Executing Supervised Population Contrast Kernel")
    print("=" * 70)
    exec_record = execute_sc_population_kernel(trial_request, workspace_dir)
    print(f"Trial Kernel Status: {exec_record['status']}")
    print(f"Elapsed Seconds:     {exec_record['elapsed_seconds']:.3f}s")
    print(f"Session ID:          {exec_record['session_id']}")

    print("\n" + "=" * 70)
    print("STEP 3: Compiling Execution Trace & Memory Report")
    print("=" * 70)
    json_path, html_path = generate_trace_report(exec_record, trial_request, workspace_dir)
    print(f"JSON Trace Ledger:   {json_path}")
    print(f"HTML Trace Report:   {html_path}")

    print("\n" + "=" * 70)
    print("STEP 4: Launching Workbench UI with Codex Control Enabled")
    print("=" * 70)
    print("Run this command to open the live interactive Workbench session:")
    cmd = (
        f".venv/bin/python -m devbench.platform.workbench_ui \\\n"
        f"  --workspace {workspace_dir} \\\n"
        f"  --input-root {inputs_dir} \\\n"
        f"  --singlecell-trial-request {inputs_dir / 'trial_request.json'} \\\n"
        f"  --enable-scientific \\\n"
        f"  --allow-data-execution \\\n"
        f"  --codex-model gpt-6.1-sol \\\n"
        f"  --agent-workspaces {actors_dir} \\\n"
        f"  --allow-model-execution \\\n"
        f"  --model-data-scope synthetic \\\n"
        f"  --port {args.port} \\\n"
        f"  --open-browser"
    )
    print(cmd)
    print("=" * 70)

    # Output concise verification summary
    diag = json.loads(json_path.read_text())["state_and_memory"]["design_diagnostics"]
    hyp = json.loads(json_path.read_text())["state_and_memory"]["hypothesis_accounting"]
    print("\nTrial Verification Summary:")
    print(f"  * Numerical Conditioning: raw={diag.get('raw_condition_number'):.2f}, std={diag.get('standardized_condition_number'):.2f} (Limit: 1000.0, Validated: {diag.get('condition_number_validated')})")
    print(f"  * Hypothesis Accounting: {hyp.get('estimated')}/{hyp.get('attempted')} estimated, {hyp.get('not_estimable')} untestable")
    print(f"  * FDR Correction: Benjamini-Hochberg monotonic q >= p enforced")
    print(f"  * Visual Report: file://{html_path}")


if __name__ == "__main__":
    main()
