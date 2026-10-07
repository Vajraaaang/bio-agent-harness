"""Offline, synthetic software demonstration through the existing reviewed controller.

This is a fixed-procedure execution check, not a method-selection evaluation.
Run with the checkout's analysis interpreter and retain the compact evidence.
Full inputs and runtime state stay in the local .runtime directory.
"""
import json
from pathlib import Path
import shutil
import time


def wait(app, job):
    from devbench.analysis.workbench_ui import ACTIVE
    deadline = time.monotonic() + 200
    while time.monotonic() < deadline:
        state = app.status(job)
        if state["status"] not in ACTIVE:
            return state
        time.sleep(.1)
    raise TimeoutError("Bounded software demo did not reach a terminal controller state")


def main():
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse
    from devbench.analysis.checkpoints import sha256
    from devbench.analysis.workbench_ui import WorkbenchService, CONFIRMATIONS, make_server

    output = Path(__file__).resolve().parent
    runtime = output / ".runtime"
    inputs = runtime / "inputs"
    inputs.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(128)
    values = rng.poisson(2, (80, 60)).astype(np.int32)
    values[:40, 1:11] += 20
    values[40:, 11:21] += 20
    symbols = ["mt-Co1", "Cd3d", "Cd3e", "Trac", "Il7r"] + [f"Gene{i}" for i in range(5, 60)]
    symbols[11:15] = ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]
    source = inputs / "synthetic-cells.h5ad"
    ad.AnnData(sparse.csr_matrix(values),
        obs=pd.DataFrame({"batch": ["one", "two"] * 40}, index=[f"c{i}" for i in range(80)]),
        var=pd.DataFrame({"gene_symbols": symbols}, index=[f"ENSMUSG{i}" for i in range(60)])).write_h5ad(source)
    source.chmod(0o444)
    original_hash = sha256(source)
    overrides = {"counts_layer": None, "min_genes": 3, "mad_limit": None, "n_top_genes": 30, "n_pcs": 8,
        "seed": 128, "stage_timeout_seconds": 90, "memory_mb": 1024, "correction": "none",
        "markers": {"T": ["Cd3d", "Cd3e", "Trac", "Il7r"], "B": ["Ms4a1", "Cd79a", "Cd79b", "Cd19"]}}
    app = WorkbenchService(runtime / "service", [inputs], parameter_overrides=overrides,
        allow_data_execution=True, run_timeout_seconds=180)
    evidence = {"scope": "Synthetic software demonstration; not biological validation or method comparison",
        "data": {"cells": 80, "genes": 60, "measurement": "artificial integer counts", "seed": 128,
            "units": "Generated cells; no independent donor or animal claims", "groups": "Two planted marker blocks of 40 cells each; alternating synthetic capture labels",
            "quality_concerns": "Tiny idealized fixture; doublet estimates may be unavailable; toy markers are not experimental identity validation",
            "input_sha256": original_hash},
        "objective": "Show the current reviewed local UI workflow and retain actual QC/embedding/report evidence",
        "requirement_source": "User authorized demonstration experiments; exact fixture/profile are assistant-selected from documented tests",
        "method_scope": "Fixed existing backed scRNA workflow, no model calls, no scientific method recommendation",
        "parameters": overrides, "run_timeout_seconds": 180, "operator_interventions": [], "model_sessions_attempted": 0,
        "previous_attempt": "ui-demo-attempt01.json records a retained missing counts-layer setup failure and correction"}
    try:
        job = app.register({"source": str(source), "expected_sha256": original_hash,
            "question": "Synthetic software demo: inspect QC, clustering and toy marker annotation",
            "declarations": {"assay": "scrna", "measurement": "umi_counts",
                "count_provenance": "Generated artificial integer counts; documented software fixture",
                "capture_unit_key": "batch", "capture_units_confirmed": True,
                "annotation_provenance": "Toy marker lists; no biological validation"}})["id"]
        prepared = wait(app, job)
        evidence["job_id"] = job
        evidence["proposal_status"] = prepared["status"]
        print(json.dumps({"phase": "proposal", "status": prepared["status"]}), flush=True)
        if prepared["status"] != "awaiting_operator_review":
            evidence["controller_failure"] = prepared.get("error")
            raise RuntimeError("Synthetic demo proposal did not become reviewable")
        rationale = "Assistant-created offline software demonstration; confirms generated inputs/profile/scope; no independent scientific approval"
        app.review(job, {"reviewer": "synthetic-demo-operator", "decision": "approve", "rationale": rationale,
            "confirmations": {key: True for key in CONFIRMATIONS}})
        reviewed = wait(app, job)
        evidence["operator_interventions"].append({"decision": "approve", "rationale": rationale, "review_status": reviewed["status"]})
        if reviewed["status"] != "approved":
            raise RuntimeError("Synthetic demo review was not approved")
        started = time.monotonic()
        app.run(job)
        state = wait(app, job)
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
        evidence["terminal_status"] = state["status"]
        evidence["input_unchanged"] = sha256(source) == original_hash
        evidence["scientific_review"] = state.get("scientific_review")
        if state.get("completed_analysis"):
            result = app.results(job)
            evidence["verification"] = result["verification"]
            evidence["report"] = result["report"]
            evidence["deterministic_review"] = result["deterministic_review"]
            evidence["scientific_review"] = result["scientific_review"]
            evidence["artifacts"] = result["artifacts"]
            images = output / "images"
            images.mkdir(exist_ok=True)
            for artifact in result["artifacts"]:
                name = artifact["name"]
                if name.endswith(".png"):
                    path, _, _ = app.download(job, name)
                    shutil.copyfile(path, images / path.name)
            archive, _, _ = app.download(job, "archive")
            evidence["archive_sha256"] = sha256(archive)
            evidence["archive_bytes"] = archive.stat().st_size
        else:
            evidence["failure"] = state.get("error") or state.get("execution")
        (output / "ui-demo-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"phase": "execution", "status": state["status"], "input_unchanged": evidence["input_unchanged"], "elapsed_seconds": evidence["elapsed_seconds"]}), flush=True)
        server = make_server(app, port=18876)
        # Token is printed only for local browser use, never retained in public evidence.
        print(f"Open http://127.0.0.1:18876/#{app.token}", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)}
        (output / "ui-demo-evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
        raise
    finally:
        app.close()


if __name__ == "__main__":
    main()
