"""Checked observations for the public PBMC investigation route."""

import pytest

from devbench.analysis.rna_local_interactive import local_controller_payload
from devbench.platform.workbench_qc_findings import _mitochondrial_summary, _rows


def test_checked_metric_reducer_handles_tied_complexity_and_reports_bounds():
    rows = _rows(
        b"n_genes_by_counts,pct_counts_mt,qc_pass\n"
        b"10,9,False\n20,8,False\n30,7,True\n30,6,True\n"
        b"40,5,True\n50,4,True\n60,3,True\n70,2,True\n"
    )
    assert _mitochondrial_summary(rows) == {
        "lowest_n": 2,
        "highest_n": 2,
        "lowest_p95": 8.95,
        "highest_p95": 2.95,
        "spearman_rho": -0.994,
    }
    with pytest.raises(ValueError, match="invalid facts"):
        _rows(b"n_genes_by_counts,pct_counts_mt,qc_pass\n23,nan,True\n")


def test_codex_gets_candidate_identity_but_no_input_values_or_claims():
    context = {
        "task": {
            "scientific_objective": "Do low-complexity profiles have more mito RNA?",
            "computational_task": "qc_and_integration",
        },
        "dataset_summary": {"observed": {"cells": 2700}, "unknowns": []},
        "ready": [],
        "evidence": {},
        "graph": {"mode": "adaptive/1", "steps": [], "sweep_members": []},
        "action_catalog": {
            "inspect-capture-qc": {
                "step_id": "inspect-capture-qc",
                "operation_id": "scrna_batch_qc",
                "purpose": "checked_observation",
                "parameters_sha256": "a" * 64,
                "inputs": ["must stay local"],
                "result": "must stay local",
            }
        },
    }
    projected = local_controller_payload(context)
    assert projected["action_catalog"]["inspect-capture-qc"] == {
        "step_id": "inspect-capture-qc",
        "operation_id": "scrna_batch_qc",
        "purpose": "checked_observation",
        "parameters_sha256": "a" * 64,
    }
