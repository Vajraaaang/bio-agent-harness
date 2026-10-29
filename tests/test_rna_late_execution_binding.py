"""Attach sealed execution transport after selection, without changing its data."""
from copy import deepcopy
from pathlib import Path
import tempfile

import pytest

from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.rna_execution_inputs import prepare_execution_inputs, load_execution_inputs
from devbench.analysis.workflow_actions import WorkflowActions
from devbench.analysis.workflow_entrypoints import LEDGER_NAME
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from tests.test_method_planner import Selector, candidate, trial_spec
from tests.test_rna_execution_inputs import inputs, registered_ports


@pytest.fixture
def workspace():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix=".late-bind-test-", dir=root) as folder:
        yield Path(folder)


def prepared(workspace, *, graph=False):
    methods, binding, matrix, _ = inputs(workspace)
    ports = registered_ports(methods)
    planner = ScientificMethodPlanner(methods)
    declaration = {"format": "devbench.rna-design/1", "objective": {"intent": "descriptive"},
        "design": {"contrasts": [], "covariates": [], "chronological_field": None, "chronological_order": []},
        "selected_sample_ids": matrix["sample_ids"],
        "source": {"kind": "fixture", "reference": "late-transport-control",
                   "statement": "Descriptive transport control, no statistical execution"}}
    plan = planner.create({"scientific_objective": "Inspect explicit synthetic RNA transport", "computational_task": "bulk RNA"},
        {"observed": {"independent_samples": True}, "inferred": {}, "unknowns": [],
         "artifact_ids": [binding["matrix_artifact_id"], binding["metadata_artifact_id"]]},
        actor="fixture", data_classification="synthetic", evaluation_purpose="execution_verification",
        rna_input_contract=binding, rna_design=declaration)
    actions = WorkflowActions(planner, WorkflowOperationLedger(methods.root / LEDGER_NAME), actor="fixture", selector=None)
    if not graph:
        actions.perform("audit", actions.create_state(plan["session_id"]))
    proposal = candidate("fit temporal trajectory"); proposal["parameter_candidates"] = []
    selector = Selector({"candidates": [proposal]}, {"bindings": [{"candidate": "fit temporal trajectory",
        "method_version_id": ports["method_version_id"], "discovery": None}]})
    if graph:
        from devbench.analysis.workflow_entrypoints import workflow_action
        from tests.support.workflow_science_fixtures import RecordingScientificRuntime
        for node in ("discovery", "generate", "candidate_review", "resolve", "implementation"):
            workflow_action(planner, plan["session_id"], node, actor="fixture", selector=selector,
                version_ids=[ports["method_version_id"]], scientific_workflow=RecordingScientificRuntime())
    else:
        planner.generate_candidates(plan["session_id"], selector, actor="fixture")
        planner.resolve_capabilities(plan["session_id"], selector, [ports["method_version_id"]], actor="fixture")
    retained = prepare_execution_inputs(binding, ports, artifact_service=methods, output=workspace / "bridge")
    record = load_execution_inputs(retained, artifact_service=methods)
    spec = trial_spec({"inputs": {key: ref["artifact_id"] for key, ref in record["execution_refs"].items()}})
    spec["candidates"] = ["fit temporal trajectory"]
    spec["inputs"] = {"fit temporal trajectory": next(iter(spec["inputs"].values()))}
    spec["parameters"] = {"fit temporal trajectory": ports["parameters"]}
    return planner, plan, retained, spec


def test_late_binding_is_atomic_with_accepted_trial_design(workspace):
    planner, plan, retained, spec = prepared(workspace)
    session = plan["session_id"]
    assert "rna_execution_inputs" not in planner.get(session)
    selector = Selector({"experiments": [spec]})
    assert planner.design_trials(session, selector, actor="fixture", execution_inputs_ref=retained,
        execution_workspace=workspace) == [spec]
    state = planner.get(session)
    assert state["rna_execution_inputs"]["record_ref"] == retained
    assert state["trial_designs"][-1]["proposals"] == [spec]
    assert set(selector.contexts[0]["available_artifacts"]) == set(spec["inputs"]["fit temporal trajectory"].values())
    assert state["results"] == []
    assert planner.methods.list_records("run") == []


def test_invalid_proposal_never_persists_late_bridge(workspace):
    planner, plan, retained, spec = prepared(workspace)
    wrong = deepcopy(spec)
    wrong["parameters"]["fit temporal trajectory"]["formula_str"] = "~ C(batch)"
    with pytest.raises(ValueError, match="parameter|binding|reader"):
        planner.design_trials(plan["session_id"], Selector({"experiments": [wrong]}), actor="fixture",
            execution_inputs_ref=retained, execution_workspace=workspace)
    assert "rna_execution_inputs" not in planner.get(plan["session_id"])
    assert not planner.get(plan["session_id"]).get("trial_designs")


def test_late_binding_requires_explicit_workspace_before_selection(workspace):
    planner, plan, retained, spec = prepared(workspace)
    selector = Selector({"experiments": [spec]})
    with pytest.raises(ValueError, match="workspace"):
        planner.design_trials(plan["session_id"], selector, actor="fixture", execution_inputs_ref=retained)
    assert selector.contexts == []


def test_late_binding_cannot_replace_a_frozen_bridge(workspace):
    planner, plan, retained, spec = prepared(workspace)
    planner.design_trials(plan["session_id"], Selector({"experiments": [spec]}), actor="fixture",
        execution_inputs_ref=retained, execution_workspace=workspace)
    selector = Selector({"experiments": [spec]})
    with pytest.raises(ValueError, match="frozen|already|bound"):
        planner.design_trials(plan["session_id"], selector, actor="fixture",
            execution_inputs_ref=retained, execution_workspace=workspace)
    assert selector.contexts == []


def test_graph_retains_attachment_request_and_revalidates_it(workspace):
    from devbench.analysis.workflow_entrypoints import workflow_action, CHECKPOINT_NAME
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    planner, plan, retained, spec = prepared(workspace, graph=True)
    session = plan["session_id"]
    workflow_action(planner, session, "design", actor="fixture", selector=Selector({"experiments": [spec]}),
        scientific_workflow=RecordingScientificRuntime(), execution_inputs_ref=retained, execution_workspace=workspace)
    ledger = WorkflowOperationLedger(planner.methods.root / LEDGER_NAME)
    record = ledger.get(session, "design", 0)
    assert record["status"] == "completed"
    assert record["request"]["execution_inputs"] == {"record_ref": retained, "workspace": str(workspace)}
    actions = WorkflowActions(planner, ledger, actor="fixture", selector=None,
        scientific_workflow=RecordingScientificRuntime())
    with SerialRNAWorkflow(actions, planner.methods.root / CHECKPOINT_NAME, stepwise=True) as flow:
        cursor = flow.graph.get_state(flow._config(session)).values
        assert actions.check(cursor)
    missing = deepcopy(record["request"]); missing.pop("execution_inputs")
    with pytest.raises(ValueError, match="without a retained attachment"):
        actions._verify_design_attachment(record["result"], missing)
    swapped = deepcopy(record["request"])
    swapped["execution_inputs"]["record_ref"]["root_hash"] = "0" * 64
    with pytest.raises(ValueError):
        actions._verify_design_attachment(record["result"], swapped)
    assert planner.methods.list_records("run") == []


def test_preparation_denies_before_workspace_or_request_access(workspace):
    from devbench.analysis.method_planner_cli import main
    output = workspace / "denied"
    with pytest.raises(PermissionError, match="allow-data-execution"):
        main(["--workspace", str(workspace / "missing-methods"), "prepare-inputs", "--plan", "missing",
            "--ports", str(workspace / "missing.json"), "--output", str(output)])
    assert not output.exists() and not (workspace / "missing-methods").exists()


def test_preparation_requires_resolution_and_keeps_plan_unchanged(workspace, capsys):
    import json
    from devbench.analysis.method_planner_cli import main
    planner, plan, _, _ = prepared(workspace)
    selected = registered_ports(planner.methods)
    before = planner.get(plan["session_id"])
    config = workspace / "ports.json"; config.write_text(json.dumps(selected))
    assert main(["--workspace", str(planner.methods.root), "prepare-inputs", "--plan", plan["session_id"],
        "--ports", str(config), "--output", str(workspace / "prepared-cli"), "--allow-data-execution"]) == 0
    retained = json.loads(capsys.readouterr().out)
    assert load_execution_inputs(retained, artifact_service=planner.methods)
    assert planner.get(plan["session_id"]) == before
    assert planner.methods.list_records("run") == []
    binding = planner._original_rna_binding(plan)
    summary = deepcopy(plan["context"]["dataset_summary"])
    summary["observed"].pop("bulk_rna_input")
    unresolved = planner.create(plan["context"]["task"], summary, actor="fixture",
        rna_input_contract=binding, rna_design=plan["rna_design"], data_classification="synthetic")
    actions = WorkflowActions(planner, WorkflowOperationLedger(planner.methods.root / LEDGER_NAME), actor="fixture", selector=None)
    actions.perform("audit", actions.create_state(unresolved["session_id"]))
    with pytest.raises(ValueError, match="Resolve an eligible method"):
        planner.prepare_execution_inputs(unresolved["session_id"], selected,
            output=workspace / "unresolved", allow_data_execution=True)
    assert not (workspace / "unresolved").exists()
