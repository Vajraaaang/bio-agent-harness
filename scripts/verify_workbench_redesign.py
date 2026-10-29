"""Prepare a fresh synthetic browser-verification workspace, without model calls.

This is software verification data, not a biological analysis or qualification.
The numerical seed executes an immutable source snapshot so concurrent UI edits
cannot change its source identity. The serving process uses current application
code and verifies the original retained bundles before exposing them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "reports/workbench-redesign/2026-10-28/browser-fixture"
EXCLUDED_PARAMETERS = {"data_sha256", "workflow", "prompt", "execution_tier", "parent_root", "model_path", "model_sha256"}


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Use an absolute canonical path")
    return path


def wait_for_operation(app, job, timeout=210):
    from devbench.analysis.workbench_ui import ACTIVE
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = app.status(job)
        if record["status"] not in ACTIVE and not record.get("operation_pending"):
            return record
        time.sleep(.1)
    raise TimeoutError("Synthetic verification operation exceeded its observation deadline")


def make_input(directory):
    """Reproduce the existing 80-cell / 60-gene integer-count test fixture."""
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.workbench import default_parameters
    directory.mkdir(mode=0o700)
    rng = np.random.default_rng(128)
    values = rng.poisson(2, (80, 60)).astype(np.int32)
    values[:40, 1:11] += 20
    values[40:, 11:21] += 20
    symbols = ["mt-Co1", "Cd3d", "Cd3e", "Trac", "Il7r"] + [f"Gene{i}" for i in range(5, 60)]
    symbols[11:15] = ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]
    source = directory / "synthetic-cells.h5ad"
    ad.AnnData(sparse.csr_matrix(values),
        obs=pd.DataFrame({"batch": ["one", "two"] * 40}, index=[f"c{i}" for i in range(80)]),
        var=pd.DataFrame({"gene_symbols": symbols}, index=[f"ENSMUSG{i}" for i in range(60)])).write_h5ad(source)
    parameters = default_parameters(data_sha256=sha256(source), counts_layer=None,
        min_genes=3, mad_limit=None, n_top_genes=30, n_pcs=8, seed=128,
        stage_timeout_seconds=90, memory_mb=1024, correction="none",
        markers={"T": ["Cd3d", "Cd3e", "Trac", "Il7r"], "B": ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]})
    return source, parameters


def seed(root):
    from devbench.analysis.checkpoints import atomic_json, sha256
    from devbench.analysis.workbench_ui import WorkbenchService, CONFIRMATIONS
    if (root / "seed-result.json").exists():
        raise ValueError("Synthetic seed already exists; use --serve to inspect it, or choose a fresh --root")
    if (root / "service").exists() or (root / "inputs").exists():
        raise ValueError("An earlier attempt exists; preserve it and choose a fresh --root")
    source, parameters = make_input(root / "inputs")
    overrides = {key: value for key, value in parameters.items() if key not in EXCLUDED_PARAMETERS}
    declarations = {"assay": "scrna", "measurement": "umi_counts",
        "count_provenance": "Synthetic integer-count software fixture; 80 cells and 60 genes, seed 128",
        "capture_unit_key": "batch", "capture_units_confirmed": True,
        "annotation_provenance": "Toy marker panel for software verification; no experimental annotation validation"}
    request = {"source": str(source), "expected_sha256": sha256(source),
        "question": "Synthetic verification: inspect QC and uncorrected UMAP; no biological conclusions",
        "declarations": declarations, "proposal_mode": "operator"}
    app = WorkbenchService(root / "service", [root / "inputs"], parameter_overrides=overrides,
        allow_data_execution=True, run_timeout_seconds=180)
    try:
        saved = app.workspace_state()
        app.workspace_state({"expected_revision": saved["revision"], "name": "Synthetic verification · 80 cells", "ui": {"view": "overview"}})
        completed = app.register(request)["id"]
        record = wait_for_operation(app, completed)
        if record["status"] != "awaiting_operator_review":
            raise RuntimeError("Synthetic proposal did not reach review: " + json.dumps(record.get("error", record["status"])))
        app.review(completed, {"reviewer": "Synthetic verification operator", "decision": "approve",
            "rationale": "Scripted software verification only; not independent scientific review or biological validation",
            "confirmations": {key: True for key in CONFIRMATIONS}})
        if wait_for_operation(app, completed)["status"] != "approved":
            raise RuntimeError("Synthetic operator review failed")
        app.run(completed)
        record = wait_for_operation(app, completed)
        if record["status"] != "completed_review_passed":
            raise RuntimeError("Synthetic numerical execution did not pass: " + json.dumps(record.get("error", record.get("execution", record["status"]))))
        results = app.results(completed)
        figures = app.figures(completed)
        if results["verification"].get("verified") is not True or results["report"]["cells_input"] != 80:
            raise RuntimeError("Synthetic result verification failed")
        pending = app.register({**request, "question": "Synthetic verification: review this separate QC plan before execution"})["id"]
        if wait_for_operation(app, pending)["status"] != "awaiting_operator_review":
            raise RuntimeError("Synthetic approval-state preparation failed")
        failed = app.register({**request, "question": "Synthetic failure control: deliberately mismatched input checksum",
            "expected_sha256": "0" * 64})["id"]
        failed_record = wait_for_operation(app, failed)
        if failed_record["status"] != "operation_failed" or failed_record["completed_analysis"]:
            raise RuntimeError("Input-mismatch failure control did not fail explicitly")
        if sha256(source) != request["expected_sha256"]:
            raise RuntimeError("Synthetic input changed")
        evidence = {"format": "devbench.workbench-redesign-synthetic-verification/1",
            "scope": "Synthetic software and browser verification only; no live model, cloud execution, biological or clinical validation",
            "completed_job": completed, "approval_job": pending, "failure_job": failed,
            "completed_status": record["status"], "approval_status": app.status(pending)["status"],
            "failure_status": failed_record["status"], "failure_type": failed_record.get("error", {}).get("type"),
            "input_sha256": request["expected_sha256"], "cells": 80, "genes": 60,
            "verification": results["verification"], "figure_ids": [item["figure_id"] for item in figures["figures"]],
            "model_sessions_attempted": 0, "data_execution": "Only newly generated synthetic H5AD in this dedicated workspace",
            "runtime_source": str(Path(sys.modules[WorkbenchService.__module__].__file__).resolve().parents[2]),
            "input_source": str(source), "service_workspace": str(root / "service"),
            "declared_input": declarations, "parameter_overrides": overrides}
        atomic_json(root / "seed-result.json", evidence)
        print(json.dumps({key: evidence[key] for key in ("completed_job", "approval_job", "failure_job", "completed_status", "figure_ids")}))
    finally:
        app.close()


def serve(root, port):
    from devbench.analysis.workbench_ui import WorkbenchService, make_server
    evidence = json.loads((root / "seed-result.json").read_text())
    app = WorkbenchService(root / "service", [root / "inputs"],
        parameter_overrides=evidence["parameter_overrides"], allow_data_execution=True, run_timeout_seconds=180)
    app.results(evidence["completed_job"])
    server = make_server(app, port=port)
    # The session credential is deliberately terminal-only, never retained in a report.
    print(f"Open http://127.0.0.1:{server.server_address[1]}/#{app.token}", flush=True)
    print("Synthetic verification workspace only. No model selector, network discovery or cloud route configured.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=absolute, default=DEFAULT_ROOT)
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--serve", action="store_true", help="Serve a previously verified synthetic workspace")
    parser.add_argument("--seed", action="store_true", help="Create and execute a fresh synthetic numerical control")
    parser.add_argument("--runtime-source", type=absolute, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.seed == args.serve or not 0 <= args.port <= 65535:
        parser.error("Choose exactly one of --seed or --serve and a valid port")
    if args.runtime_source:
        sys.path.insert(0, str(args.runtime_source))
    else:
        sys.path.insert(0, str(PROJECT))
    from devbench.analysis.resources import configure_threads
    configure_threads()
    args.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.serve:
        serve(args.root, args.port)
    elif args.runtime_source:
        seed(args.root)
    else:
        from scripts.build_colab_notebook import build_source_bundle, extract_source_bundle
        snapshot = build_source_bundle(PROJECT)
        source = args.root / "numerical-source"
        extract_source_bundle(snapshot["base64"], source, snapshot["sha256"])
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--seed", "--root", str(args.root),
            "--runtime-source", str(source)], cwd=source)
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
