"""One declared local analyst route to the real bulk core, with offline decisions.

Review receipts below are explicitly engineering fixtures. They establish neither
qualified review nor biological agreement. No custom numeric executor is used.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile

import pytest

from devbench.analysis.episode_agent import Selection
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.method_planner_cli import main as planner_cli
from devbench.analysis.methods import MethodService
from devbench.analysis.rna_execution_inputs import load_execution_inputs
from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_entrypoints import (
    audit_workflow_design, workflow_action, CHECKPOINT_NAME, LEDGER_NAME,
)
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from devharness_core.provenance import canonical_json, verify_execution
from tests.support.workflow_science_fixtures import RecordingScientificRuntime


GENES = [f"gene_{index}" for index in range(12)]
SAMPLES = [f"sample_{index}" for index in range(8)]
ACTOR = "hand-authored-engineering-fixture"


@pytest.fixture
def proof_workspace():
    root = Path(__file__).resolve().parents[1] / "reports" / "local-rna-analyst-2026-10-28" / "increment-02"
    root.mkdir(parents=True, exist_ok=True)
    # Retain successes and failures; do not replace protected storage with TMPDIR.
    return Path(tempfile.mkdtemp(prefix="proof-declared-bulk-", dir=root))


def _inputs(root, *, continuous, all_failed=False):
    methods = MethodService(root / "methods", reconcile=False)
    hierarchy = {"population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id", "identity_status": "declared"}
    rows = [{"sample_id": sample, "donor_id": f"donor_{i}", "specimen_id": f"specimen_{i}",
        "library_id": f"library_{i}", "condition": "A" if i < 4 else "B", "batch": "batch_x"}
        for i, sample in enumerate(SAMPLES)]
    matrix = {"gene_ids": GENES, "sample_ids": SAMPLES, "matrix": [
        [0.0] * 8 if all_failed else
        [1.0 + i % 2 + (4 if i >= 4 else 0) + j * .1 for i in range(8)] if continuous else
        [10 + (i * 3 + j * 5) % 9 + (j % 3 if i >= 4 else 0) for i in range(8)]
        for j in range(12)]}
    refs, originals = {}, {}
    for role, value in (("matrix", matrix), ("metadata", {"samples": rows})):
        path = root / (role + ".json")
        path.write_bytes(canonical_json(value))
        originals[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        refs[role] = methods.import_artifact(path, artifact_type="json", actor=ACTOR)
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
        "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {
        "format": "devbench.bulk-rna-input/1", "representation": "normalized_expression" if continuous else "raw_counts",
        "axes": ["gene", "sample"], "provenance": {"origin": "hand-authored synthetic software fixture"},
        "biological_independence": {"independent_units": True, "unit_hierarchy": deepcopy(hierarchy)},
        "metadata_roles": {"biological_unit": "donor_id", "condition": "condition", "batch": "batch"}}}
    design = {"format": "devbench.rna-design/1", "objective": {"intent": "infer"},
        "design": {"contrasts": [{"field": "condition", "reference": "A", "target": "B"}],
            "covariates": ["batch"], "chronological_field": None, "chronological_order": []},
        "selected_sample_ids": SAMPLES,
        "source": {"kind": "fixture", "reference": "test_declared_bulk_analyst_workflow",
            "statement": "Independent constructed donors; B minus A retaining declared batch."}}
    public_task = {"measurement": "continuous" if continuous else "raw_counts", "observation_id_label": "sample_id",
        "gene_ids": GENES, "unit_hierarchy": hierarchy,
        "analysis_columns": {"condition": "condition", "population_unit": "donor_id", "batches": ["batch"],
            "time": None, "dose": None, "covariate": None},
        "context": {"requested_condition_contrast": {"denominator": "A", "numerator": "B"}, "condition_labels": ["A", "B"]},
        "questions": [{"target_id": "mean_contrast", "estimand": "Condition mean contrast over observed biological units",
            "units": "input units" if continuous else "log2 count-rate contrast", "contrast": "B minus A",
            "reference_population": "observed biological units", "prediction_reference": "observed biological units"}]}
    parameters = {"public_task": public_task, "options": {"condition_col": "condition", "reference_condition": "A",
        "comparison_condition": "B", "minimum_units": 3, "condition_number_limit": 1000.0, "batch_cols": ["batch"]}}
    return methods, binding, design, parameters, originals


class DeclaredBulkSelector:
    def __init__(self, continuous):
        self.name = "bulk condition contrast"
        self.continuous = continuous
        self.version = None
        self.inputs = None
        self.parameters = None
        self.contexts = []

    def configuration(self):
        return {"backend": "recorded_fixture", "model_execution": False}

    def select(self, context, output, **kwargs):
        assert kwargs["allow_model_execution"] is False
        self.contexts.append(deepcopy(context))
        phase = context["decision_kind"]
        if phase == "method_scientific_planning":
            assert self.version is None and self.parameters is None
            assert "registry" not in context
            answer = {"candidates": [{"name": self.name,
                "method_family": "Gaussian condition contrast" if self.continuous else "Negative-binomial condition contrast",
                "source": {"type": "generated", "reference": "Hand-authored engineering candidate; not scientific comparison"},
                "assumptions": [], "expected_inputs": ["gene_by_sample_expression", "sample_metadata"],
                "dataset_fit": "plausible", "objective_fit": "plausible",
                "known_failure_modes": ["Synthetic execution does not qualify this method for biological inference"],
                "parameter_candidates": []}]}
        elif phase == "method_capability_resolution":
            answer = {"bindings": [{"candidate": self.name, "method_version_id": self.version, "discovery": None}]}
        elif phase == "method_trial_design":
            assert self.inputs is not None and self.parameters is not None
            answer = {"experiments": [{"id": "declared-bulk-proof", "candidates": [self.name],
                "purpose": "Bounded fixture-authored software proof, not a best-method experiment",
                "subset_spec": {"kind": "full_dataset", "representation": "All original samples and genes unchanged"},
                "subset_validity": {"preserves_preconditions": True, "rationale": "Exact complete declared cohort and family"},
                "commands": [], "metrics": [{"name": "runtime_seconds", "category": "runtime", "direction": "lower_better",
                    "interpretation": "Execution time only; no biological accuracy interpretation"}],
                "metric_sources": {"runtime_seconds": {"kind": "runtime", "field": "elapsed_seconds"}},
                "resource_limits": {"memory_mb": 4096, "timeout_seconds": 300, "max_runs": 1},
                "inputs": {self.name: self.inputs}, "parameters": {self.name: self.parameters}}]}
        elif phase == "method_recommendation":
            answer = {"choice": None, "confidence": "no_recommendation", "scientific_confidence": "no_recommendation",
                "implementation_confidence": "unknown", "confidence_basis": "Software fixture only",
                "rationale": "A successful bounded fit does not establish the scientifically best method",
                "tradeoffs": [], "alternatives": [], "params": {}, "tests_run": [], "approval_actions": []}
        else:
            raise AssertionError("Unexpected selector phase: " + phase)
        return Selection(answer, {"backend": "recorded_fixture", "model_execution": False,
            "model_sessions_attempted": 0, "origin": "fixture_not_user_prespecified"})


@pytest.mark.parametrize("continuous,all_failed", [(False, False), (True, False), (True, True)],
    ids=["counts", "continuous", "all-failed-continuous"])
def test_declared_analyst_reaches_real_bulk_fit_and_retains_terminal_replay(proof_workspace, continuous, all_failed, capsys, monkeypatch):
    root = proof_workspace
    methods, binding, declaration, parameters, originals = _inputs(root, continuous=continuous, all_failed=all_failed)
    request = {"task": {"scientific_objective": "Estimate B minus A for explicitly constructed independent donors",
        "computational_task": "Bounded synthetic execution proof"},
        "dataset_summary": {"observed": {}, "inferred": {}, "unknowns": [],
            "artifact_ids": [binding["matrix_artifact_id"], binding["metadata_artifact_id"]]},
        "data_classification": "synthetic", "evaluation_purpose": "execution_verification",
        "rna_input_contract": binding, "rna_design": declaration}
    request_path = root / "create-request.json"
    request_path.write_bytes(canonical_json(request))
    assert planner_cli(["--workspace", str(methods.root), "--actor", ACTOR,
        "create-analyst", "--request", str(request_path)]) == 0
    initial = json.loads(capsys.readouterr().out)
    session = initial["session_id"]
    assert initial["scientific_candidates"] is None and "rna_execution_inputs" not in initial
    assert initial["user_parameter_requirements"] == []
    planner = ScientificMethodPlanner(methods)
    audit = audit_workflow_design(planner, session, actor=ACTOR)
    assert audit["status"] == "ready", audit
    assert methods.list_records("run") == []
    selector = DeclaredBulkSelector(continuous)
    runtime = RecordingScientificRuntime()
    options = {"actor": ACTOR, "selector": selector, "scientific_workflow": runtime}
    trace = ["create-analyst", "audit"]

    def act(node, **extra):
        result = workflow_action(planner, session, node, **options, **extra)
        trace.append(node)
        (root / "progress.json").write_bytes(canonical_json({"session_id": session, "trace": trace}))
        return result

    act("discovery")
    act("generate")
    assert len(selector.contexts) == 1
    assert "rna_execution_inputs" not in planner.get(session)
    act("candidate_review")
    method = methods.register_dispatcher("bulk_condition_contrast", actor=ACTOR)
    selector.version = method["version_id"]
    act("resolve", version_ids=[selector.version])
    act("implementation")
    ports = {"format": "devbench.rna-execution-ports/2", "ports": {
        "counts": {"source": "matrix", "artifact_type": "file", "encoding": "csv", "required": True, "parameter": "counts_path"},
        "metadata": {"source": "metadata", "artifact_type": "table", "encoding": "csv", "required": True, "parameter": "metadata_path"}},
        "accepted_representations": [binding["declarations"]["representation"]],
        "objective": declaration["objective"], "design": declaration["design"],
        "string_columns": ["batch", "condition", "donor_id", "library_id", "sample_id", "specimen_id"],
        "method_version_id": selector.version, "parameters": parameters}
    ports_path = root / "execution-ports.json"
    ports_path.write_bytes(canonical_json(ports))
    assert planner_cli(["--workspace", str(methods.root), "--actor", ACTOR, "prepare-inputs",
        "--plan", session, "--ports", str(ports_path), "--output", str(root / "execution-inputs"),
        "--allow-data-execution"]) == 0
    bridge = json.loads(capsys.readouterr().out)
    record = load_execution_inputs(bridge, artifact_service=methods, dataset_binding=binding)
    selector.inputs = {key: ref["artifact_id"] for key, ref in record["execution_refs"].items()}
    selector.parameters = parameters
    drafts = act("design", execution_inputs_ref=bridge, execution_workspace=root)
    assert len(drafts) == 1 and methods.list_records("run") == []
    act("design_review")
    analysis = methods.create_analysis("Declared synthetic bulk proof", actor=ACTOR)
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor=ACTOR)
    act("declare", branch_id=branch["branch_id"], spec=drafts[0])
    with pytest.raises(PermissionError, match="authorization|authority"):
        act("run", experiment_id="declared-bulk-proof")
    assert methods.list_records("run") == []
    results = act("run", experiment_id="declared-bulk-proof", allow_data_execution=True)
    assert len(results) == 1 and results[0]["status"] == "success", results
    runs = methods.list_records("run")
    assert len(runs) == 1 and runs[0]["status"] == "succeeded"
    run = runs[0]
    events = run["execution"]["tool_events"]
    assert len(events) == 1 and events[0]["tool_name"] == "bulk_condition_contrast" and events[0]["succeeded"] is True
    raw = json.loads(methods.artifact_path(run["outputs"]["result"]).read_bytes())
    assert events[0]["result"] == raw
    target = raw["target_results"][0]
    stats = (raw["diagnostics"]["failed_gene_statistics"]["mean_contrast"] if all_failed
             else target["payload"]["gene_statistics"])
    assert [row["gene_id"] for row in stats] == GENES
    assert raw["diagnostics"]["biological_units"]["analysis_unit_count"] == 8
    if all_failed:
        assert target["payload"] is None and target["claim_type"] == "failed"
        assert all(row["estimate_status"] != "estimated" and row["p_value"] is None and row["q_value"] is None for row in stats)
    else:
        assert raw["diagnostics"]["gene_test_adjustment_family"]["attempted_hypothesis_ids"] == GENES
        assert all(row["p_value"] is not None and 0 <= row["p_value"] <= row["q_value"] <= 1 for row in stats)
    if continuous and not all_failed:
        assert all(row["values"][0] == pytest.approx(4.0) for row in stats)
    elif not continuous:
        assert raw["method"]["count_model"] == "negative_binomial_empirical_bayes_dispersion"
    evidence_path = methods.root / "runs" / run["run_id"] / "evidence" / "manifest.json"
    assert verify_execution(evidence_path, run["evidence"]["root_hash"])["valid"]
    assessment = planner.evidence_assessment(session)
    assert assessment["evaluation_purpose"] == "execution_verification"
    assert all(row["meets_evidence_floor"] is False for row in assessment["candidates"])
    with pytest.raises(ValueError, match="cannot produce a non-null scientific recommendation"):
        planner._check_evidence_floor(planner.get(session), {"choice": selector.name}, assessment)
    from devharness_server.dispatcher import ToolDispatcher
    monkeypatch.setattr(ToolDispatcher, "execute", lambda *a, **k: pytest.fail("Terminal synthesis/replay refit statistics"))
    act("recommend")
    terminal = act("conclusion_review")
    assert terminal["final"] is True and terminal["status"] == "abstained"
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    ledger = WorkflowOperationLedger(methods.root / LEDGER_NAME)
    actions = WorkflowActions(planner, ledger, actor=ACTOR, selector=selector, scientific_workflow=runtime)
    calls_before = len(selector.contexts)
    with SerialRNAWorkflow(actions, methods.root / CHECKPOINT_NAME, stepwise=True) as flow:
        resumed = flow.resume(session)
    assert resumed["status"] == "abstained" and len(selector.contexts) == calls_before
    assert len(methods.list_records("run")) == 1
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == original for path, original in originals.items())
    assert all(row["evidence"]["model_execution"] is False for row in planner.get(session)["selection_history"])
    summary = {"format": "test.declared-local-rna-proof/1", "session_id": session,
        "measurement": "continuous" if continuous else "raw_counts", "all_failed_control": all_failed, "trace": trace,
        "execution": "pass", "estimated_gene_count": sum(row["estimate_status"] == "estimated" for row in stats),
        "failed_gene_count": sum(row["estimate_status"] != "estimated" for row in stats),
        "scientific_method_comparison": "not_run", "retained_terminal_replay_without_refit": "pass", "numerical_reference_comparison": "not_run",
        "biological_agreement": "not_run", "qualified_scientific_review": "not_established",
        "model_sessions_attempted": 0, "gene_count": len(stats), "independent_fixture_donors": 8,
        "run_id": run["run_id"], "evidence_root": run["evidence"]["root_hash"], "terminal_status": terminal["status"],
        "scope": "hand_authored_fixture_execution_control_not_agent_efficacy_or_method_superiority"}
    (root / "proof.json").write_bytes(canonical_json(summary))
