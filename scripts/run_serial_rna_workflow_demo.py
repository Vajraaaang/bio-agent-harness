"""Offline serial LangGraph example with actual registered bulk RNA artifacts.

Recorded decisions and unavailable methods intentionally end in abstention. This
demonstrates orchestration and evidence reuse, not discovery or model performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from devharness_core.provenance import canonical_json


def run_demo(workspace, *, representation="raw_counts"):
    from devbench.analysis.method_executor import _path
    from devbench.analysis.methods import MethodService
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.method_planner_cli import RecordedMethodSelector
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow

    root = _path(workspace)
    if representation not in {"raw_counts", "normalized_expression"}:
        raise ValueError("Choose the explicit raw-count or normalized-expression fixture")
    if root.exists():
        raise FileExistsError("Use a fresh example directory; retained evidence is not overwritten")
    root.mkdir(parents=True)
    matrix = [[10, 20, 4, 8], [0, 3, 0, 1]] if representation == "raw_counts" else [[0.2, 0.8, -0.4, 1.3], [-0.1, 0.05, 0.6, 0.01]]
    inputs = {
        "matrix": {"gene_ids": ["gene_1", "gene_2"], "sample_ids": ["s1", "s2", "s3", "s4"], "matrix": matrix},
        "metadata": {"samples": [
            {"sample_id": "s3", "condition": "B", "donor": "d2", "batch": "b2"},
            {"sample_id": "s1", "condition": "A", "donor": "d1", "batch": "b1"},
            {"sample_id": "s4", "condition": "B", "donor": "d2", "batch": "b2"},
            {"sample_id": "s2", "condition": "A", "donor": "d1", "batch": "b1"}]}}
    methods = MethodService(root / "harness", reconcile=False)
    references, original = {}, {}
    for key, value in inputs.items():
        path = root / (key + ".json")
        path.write_bytes(canonical_json(value))
        original[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        references[key] = methods.import_artifact(path, artifact_type="json", actor="offline-demo")
    binding = {"format": "devbench.bulk-rna-binding/1",
        "matrix_artifact_id": references["matrix"]["artifact_id"],
        "metadata_artifact_id": references["metadata"]["artifact_id"],
        "declarations": {"format": "devbench.bulk-rna-input/1", "representation": representation,
            "axes": ["gene", "sample"], "provenance": {"source": "assistant-authored synthetic orchestration fixture"},
            "biological_independence": None,
            "metadata_roles": {"condition": "condition", "biological_unit": "donor", "batch": "batch"}}}
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Inspect a synthetic RNA workflow and retain uncertainty about condition effects",
                           "computational_task": "workflow_verification"},
        {"observed": {}, "inferred": {}, "unknowns": [],
         "artifact_ids": [references[key]["artifact_id"] for key in ("matrix", "metadata")]},
        actor="offline-demo", data_classification="synthetic", evaluation_purpose="execution_verification",
        rna_input_contract=binding)
    names = ["Negative-binomial condition model", "Gaussian condition model"]
    candidates = [{"name": name, "method_family": "condition_analysis",
        "source": {"type": "generated", "reference": "Recorded software fixture; no scientific method comparison"},
        "assumptions": [], "expected_inputs": ["gene_by_sample_expression", "sample_metadata"],
        "dataset_fit": "plausible", "objective_fit": "plausible",
        "known_failure_modes": ["Unknown independence and condition-batch confounding require review"],
        "parameter_candidates": []} for name in names]
    selector = RecordedMethodSelector([
        {"candidates": candidates},
        {"bindings": [{"candidate": name, "method_version_id": None, "discovery": None} for name in names]},
        {"choice": None, "confidence": "no_recommendation", "scientific_confidence": "no_recommendation",
         "implementation_confidence": "unknown", "rationale": "No implemented alternative or independently validated inference is available",
         "params": {}, "approval_actions": []}])
    ledger = WorkflowOperationLedger(root / "operations.sqlite")
    actions = WorkflowActions(planner, ledger, actor="offline-demo", selector=selector)
    state = actions.create_state(plan["session_id"])
    with SerialRNAWorkflow(actions, root / "checkpoints.sqlite", enabled=True) as flow:
        paused = flow.start(state)
        if "__interrupt__" not in paused:
            raise ValueError("Expected a reviewable planning pause")
        final = flow.resume(state["workflow_id"])
        if final["status"] != "abstained":
            raise ValueError("Offline example must retain scientific abstention")
        if flow.resume(state["workflow_id"]) != final:
            raise ValueError("Terminal checkpoint replay changed the retained outcome")
    retained = planner.get(plan["session_id"])
    inspection = retained["context"]["dataset_summary"]["observed"]["bulk_rna_input"]
    readonly = WorkflowOperationLedger(root / "operations.sqlite", readonly=True)
    operations = readonly.replay(state["workflow_id"])
    result = {"format": "devbench.serial-rna-offline-example/1", "status": final["status"],
        "workflow_id": state["workflow_id"], "session_id": plan["session_id"],
        "representation": inspection["representation"], "metadata_reordered": inspection["metadata_alignment"]["reordered"],
        "biological_independence": inspection["biological_independence"],
        "recorded_selections": len(retained["selection_history"]), "model_provider_calls": 0,
        "method_runs": len(methods.list_records("run")), "approvals": retained["approvals"],
        "input_bytes_unchanged": all(hashlib.sha256(path.read_bytes()).hexdigest() == sha for path, sha in original.items()),
        "evidence_replay_calls": 0, "retained_operations": len(operations),
        "scope": "offline orchestration example; no fit, native model call, discovery score or method recommendation"}
    (root / "result.json").write_bytes(canonical_json(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--representation", choices=("raw_counts", "normalized_expression"), default="raw_counts")
    args = parser.parse_args(argv)
    print(json.dumps(run_demo(args.workspace, representation=args.representation), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
