"""Real bounded synthetic post-fit workflows, recorded models, no provider calls.

The JSON-to-CSV adapter below is a test-only trusted executor on the existing
custom-method interface. It preserves immutable input values and calls the
registered ToolDispatcher fitter; it never supplies invented numerical results.
Fixed formula, gene family, projection and budgets are test-author choices.
"""
from copy import deepcopy
import csv
import hashlib
import inspect
import json
import math
from pathlib import Path

import pytest

from devharness_core.provenance import canonical_json, hash_artifact
from devharness_server.dispatcher import ToolDispatcher, capture_tool_events
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.methods import MethodService, _digest
from devbench.analysis.rna_investigation_backend import InvestigationExecutionAuthority
from devbench.analysis.rna_investigation_projection import verify_packet_projection
from devbench.analysis.rna_investigations import retain_investigation_json
from devbench.analysis.workflow_actions import WorkflowActions, create_rna_workflow
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from tests.test_execution_contract_repair import declaration
from tests.test_extensible_methods import custom
from tests.test_langgraph_investigations import WorkflowFixtureBackend
from tests.test_method_planner import candidate, recommendation
from tests.test_rna_investigation_budget import _policy
from tests.test_rna_investigation_parent_integration import CapturedFixtureSelector


GENES = ["zeta", "alpha", "middle"]
FORMULA = "1 + numeric_time + C(batch)"
PARAMETERS = {"formula_str": FORMULA, "time_col": "collection_time",
              "gene_list": GENES, "selection_criterion": "fixed",
              "spline_dfs": [2], "data_type": "continuous"}


def _ref(methods, identity):
    artifact = methods.get("artifact", identity)
    return {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}


def _write_csv(matrix, metadata, root):
    """Bounded lossless transport projection, without fitting or count conversion."""
    root.mkdir(parents=True, exist_ok=False)
    expression, samples = root / "expression.csv", root / "metadata.csv"
    with expression.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["gene", *matrix["sample_ids"]])
        writer.writerows([gene, *values] for gene, values in zip(matrix["gene_ids"], matrix["matrix"]))
    with samples.open("w", newline="") as stream:
        fields = ["sample_id", "collection_time", "donor_id", "batch"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(metadata["samples"])
    with expression.open(newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0][1:] == matrix["sample_ids"]
    assert [row[0] for row in rows[1:]] == matrix["gene_ids"]
    assert [[float(value) for value in row[1:]] for row in rows[1:]] == matrix["matrix"]
    return expression, samples


def _with_verified_bh_metadata(raw, gene_list):
    """Name an independently checked original correction; never repair statistics."""
    from devharness_server.tools_stat import bh_adjust
    assert raw["fdr_scope"] == "requested_genes"
    assert gene_list and len(gene_list) == len(set(gene_list))
    assert set(raw["genes"]) == set(gene_list)
    rows = [raw["genes"][gene] for gene in gene_list]
    adjusted = bh_adjust([row["p_value"] if row["p_value"] is not None else 1.0 for row in rows])
    for row, expected_q in zip(rows, adjusted):
        if row["p_value"] is None:
            assert row["q_value"] is None and row["padj"] is None
        else:
            assert 0 <= row["p_value"] <= row["q_value"] <= 1
            assert row["padj"] == row["q_value"]
            assert math.isclose(row["q_value"], expected_q, rel_tol=1e-12, abs_tol=0.0)
    metadata = {"q_value": "benjamini_hochberg", "padj": "benjamini_hochberg"}
    assert raw.get("fdr_methods", metadata) == metadata
    return {**deepcopy(raw), "fdr_methods": metadata}


class ActualDispatcherExecutor:
    """Test-only executor with one real supervised numerical call per workflow."""
    def __init__(self, *, qc_only=False):
        self.qc_only = qc_only
        self.calls = []
        self.executions = 0
        self.input_digests = []
        self.raw_output_bytes = []

    def execute(self, contract, inputs, parameters, source, lock, output_dir):
        self.executions += 1
        assert self.executions == 1, "Resume or replay redispatched numerical execution"
        matrix = json.loads(inputs["counts"].read_bytes())
        metadata = json.loads(inputs["metadata"].read_bytes())
        self.input_digests.append({name: hash_artifact(path)["sha256"] for name, path in inputs.items()})
        expression, samples = _write_csv(matrix, metadata, output_dir.parent / "synthetic-format-view")
        dispatcher = ToolDispatcher(allowed_root=output_dir.parent)
        with capture_tool_events() as events:
            if self.qc_only:
                tool, arguments = "audit_expression_quality", {"counts_path": str(expression),
                    "metadata_path": str(samples), "time_col": "collection_time",
                    "max_cells": 12, "top_genes": 3, "n_neighbors": 2}
            else:
                tool, arguments = "fit_temporal_trajectory", {**parameters,
                    "counts_path": str(expression), "metadata_path": str(samples)}
            self.calls.append(tool)
            raw_bytes = dispatcher.execute(tool, arguments).encode("utf-8")
            self.raw_output_bytes.append(raw_bytes)
            actual = json.loads(raw_bytes)
            if not self.qc_only:
                actual = _with_verified_bh_metadata(actual, parameters["gene_list"])
            (output_dir / "result.json").write_bytes(canonical_json(actual))
        assert events and events[-1]["tool_name"] == tool
        return {"exit_code": 0, "runtime": "trusted_synthetic_json_adapter",
                "tool_events": events}


class PostFitSelector(CapturedFixtureSelector):
    """Responses are recorded fixtures; all statistics enter via the real receipt."""
    def __init__(self, method_id, input_ids, allocation):
        super().__init__()
        self.method_id, self.input_ids, self.allocation = method_id, input_ids, allocation

    def select(self, context, output, **kwargs):
        phase = context["decision_kind"]
        name = "Fixture Gaussian association"
        if phase == "method_scientific_planning":
            proposal = candidate(name)
            proposal.update(method_family="Gaussian temporal association", assumptions=[{
                "statement": "The fixture explicitly declares continuous normalized measurements",
                "status": "satisfied", "evidence": "Immutable synthetic input declaration",
                "hard_precondition": True, "field": "bulk_rna_input.representation",
                "operator": "equals", "expected": "normalized_expression"}],
                parameter_candidates=[{"name": key, "value": value, "origin": "fixture",
                    "rationale": "Test-author execution control, not a user-prespecified scientific choice"}
                    for key, value in PARAMETERS.items()])
            answer = {"candidates": [proposal]}
        elif phase == "method_capability_resolution":
            answer = {"bindings": [{"candidate": name, "method_version_id": self.method_id, "discovery": None}]}
        elif phase == "method_trial_design":
            answer = {"experiments": [{"id": "bounded-shared-fit", "candidates": [name],
                "purpose": "A synthetic execution control; it cannot select the best scientific method",
                "subset_spec": {"kind": "full_dataset", "representation": "Unchanged normalized input"},
                "subset_validity": {"preserves_preconditions": True,
                    "rationale": "All explicitly constructed independent virtual units and values retained"},
                "commands": [], "metrics": [{"name": "runtime_seconds", "category": "runtime",
                    "direction": "lower_better", "interpretation": "Execution duration only"}],
                "metric_sources": {"runtime_seconds": {"kind": "runtime", "field": "elapsed_seconds"}},
                "resource_limits": {"memory_mb": 4096, "timeout_seconds": 300, "max_runs": 1},
                "inputs": {name: self.input_ids}, "parameters": {name: PARAMETERS}}]}
        elif phase == "method_preliminary_explanation":
            view = context["verified_output_view"]
            stats = next(row for row in view["facts"]["observed"] if row["field"] == "gene_statistics")
            coverage = next(row for row in view["facts"]["observed"] if row["field"] == "fitted_family_coverage")
            assert stats["value"]["tested_hypotheses"] == len(GENES)
            refs = stats["evidence_refs"]
            answer = {"hypotheses": [
                {"id": "composition", "statement": "Changing composition could explain the association",
                 "evidence_refs": refs, "limitations": ["Cell composition was not measured"]},
                {"id": "intrinsic", "statement": "An intrinsic response could explain the association; PARENT-PRIVATE-CANARY",
                 "evidence_refs": refs, "limitations": ["Association does not establish causality"]},
                {"id": "technical", "statement": "Technical effects could contribute to the association",
                 "evidence_refs": coverage["evidence_refs"], "limitations": ["Independent technical measurements are absent"]}],
                "ambiguity_items": [{"id": "composition-or-intrinsic", "axis": "composition_vs_intrinsic",
                    "hypothesis_ids": ["composition", "intrinsic"], "evidence_refs": refs,
                    "missing_information_status": "sufficient",
                    "discriminating_criterion": "Independent composition measurements in a new cohort"}],
                "uncertainty": ["These remain exploratory explanations"],
                "absent_assessments": ["Validated plot pixels", "Qualified independent scientific review"]}
            if self.allocation == 2:
                answer["ambiguity_items"].append({"id": "technical-or-intrinsic", "axis": "technical_vs_biological",
                    "hypothesis_ids": ["technical", "intrinsic"], "evidence_refs": coverage["evidence_refs"],
                    "missing_information_status": "sufficient",
                    "discriminating_criterion": "Independent technical covariates and a replication cohort"})
        elif phase == "method_recommendation":
            answer = recommendation(None)
        else:
            raise AssertionError("Unexpected unallocated model phase: " + phase)
        self.answers.append(answer)
        return super().select(context, output, **kwargs)


class ObservingBackend(WorkflowFixtureBackend):
    def __init__(self, methods, selector, *, cancel_after_child=False):
        super().__init__(methods, selector)
        self.before_children = []
        self.cancel_after_child = cancel_after_child

    def investigate(self, request, **kwargs):
        parent = self.methods.get("method_plan", request["parent_session_id"])
        nomination = [row for row in parent["selection_history"] if row["phase"] == "method_preliminary_explanation"]
        assert len(nomination) == 1 and nomination[0]["status"] == "accepted"
        charge = self.ledger.get_model_reservation(nomination[0]["model_reservation_ref"])
        assert charge["status"] == "completed" and charge["capture"]["usage"]["normalized_total_tokens"] > 0
        self.before_children.append(deepcopy(charge))
        outcome = super().investigate(request, **kwargs)
        if self.cancel_after_child:
            self.ledger.cancel_campaign(request["parent_session_id"])
        return outcome


def _case(root, *, allocation=2, qc_only=False, cancel_after_child=False):
    root = Path(root).resolve()
    methods = MethodService(root / "methods")
    samples = ["s" + str(index) for index in range(12)]
    matrix = {"gene_ids": GENES, "sample_ids": samples, "matrix": [
        [float(10 + gene * 2 + index * .35 + ((index + gene) % 3) * .4)
         for index in range(12)] for gene in range(len(GENES))]}
    metadata = {"samples": [{"sample_id": sample, "collection_time": 8 + index // 3,
        "donor_id": "virtual-independent-donor-" + str(index), "batch": "A" if index % 2 else "B"}
        for index, sample in enumerate(samples)]}
    paths = {"counts": root / "matrix.json", "metadata": root / "metadata.json"}
    for name, content in (("counts", matrix), ("metadata", metadata)):
        paths[name].write_bytes(canonical_json(content))
    inputs = {name: methods.import_artifact(path, artifact_type="json", actor="fixture-author")["artifact_id"]
              for name, path in paths.items()}
    declared = declaration("normalized_expression")
    declared.update(provenance={"origin": "Constructed synthetic software control, not biological validation"},
        biological_independence={"independent": True, "origin": "Explicit construction of separate virtual units"},
        metadata_roles={"biological_unit": "donor_id", "time": "collection_time", "batch": "batch"})
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Describe receipt-bound temporal associations and competing explanations",
        "computational_task": "Bounded synthetic post-fit explanation control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": list(inputs.values())},
        actor="fixture-author", data_classification="synthetic", evaluation_purpose="execution_verification",
        rna_input_contract={"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": inputs["counts"],
            "metadata_artifact_id": inputs["metadata"], "declarations": declared})
    contract, source, lock = custom(root)
    contract.update(method_id="custom.synthetic-json-temporal-control", name="Fixture Gaussian association",
        inputs={port: {"artifact_type": "json", "required": True} for port in inputs},
        parameters={"type": "object", "additionalProperties": False,
            "properties": {key: {"const": value} for key, value in PARAMETERS.items()}, "required": list(PARAMETERS)},
        outputs={"statistics": {"artifact_type": "json", "path": "/outputs/result.json", "required": True}})
    contract["execution"].update(memory_mb=4096, timeout_seconds=300)
    source.write_text("# Test-only trusted adapter, never provider or clinical qualification.\n"
        "import csv\nimport json\nimport math\nfrom copy import deepcopy\n"
        "from devharness_core.provenance import canonical_json, hash_artifact\n"
        "from devharness_server.dispatcher import ToolDispatcher, capture_tool_events\n\n"
        + inspect.getsource(_write_csv) + "\n" + inspect.getsource(_with_verified_bh_metadata)
        + "\n" + inspect.getsource(ActualDispatcherExecutor)
        + "\nif __name__ == '__main__':\n    raise SystemExit('Trusted test executor only')\n")
    contract["implementation"]["source_sha256"] = hash_artifact(source)["sha256"]
    method = methods.register_method(contract, source=source, environment_lock=lock, actor="fixture-author")
    analysis = methods.create_analysis("Synthetic exploratory post-fit control", actor="fixture-author")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="fixture-author")
    _, metadata_csv = _write_csv(matrix, metadata, root / "pre-fit-audit-view")
    with capture_tool_events() as audit_events:
        design = json.loads(ToolDispatcher(allowed_root=root).execute("validate_design_matrix", {
            "metadata_csv_path": str(metadata_csv), "formula_str": FORMULA, "time_col": "collection_time"}))
    assert design["is_full_rank"] is True and design["condition_number"] is not None
    audit = {"input_integrity_valid": True, "gene_ids_unique": True, "sample_ids_unique": True,
        "alignment_valid": True, "measurements_finite": True, "chronology_valid": True,
        "design_rank_valid": design["is_full_rank"], "contrast_valid": True,
        "age_batch_confounded": False, "sample_count": len(samples), "representation": "normalized_expression",
        "biological_units": [row["donor_id"] for row in metadata["samples"]],
        "biological_independence": True, "biological_unit_identity_known": True,
        "time_labels": [row["collection_time"] for row in metadata["samples"]],
        "batch_labels": [row["batch"] for row in metadata["samples"]],
        "design_diagnostics": design, "metadata_roles": declared["metadata_roles"],
        "source_input_refs": [_ref(methods, identity) for identity in inputs.values()],
        "actual_design_tool_events": audit_events}
    assert len(set(audit["biological_units"])) == len(samples)
    assert all({row["batch"] for row in metadata["samples"] if row["collection_time"] == age} == {"A", "B"}
               for age in set(audit["time_labels"]))
    audit_ref = retain_investigation_json(methods, audit, actor="fixture-author")
    declared_fields = {"representation", "biological_units", "biological_independence",
        "biological_unit_identity_known", "time_labels", "batch_labels", "metadata_roles"}
    facts = {key: [] for key in ("observed", "declared", "inferred", "unknown")}
    for field, value in audit.items():
        if field in {"source_input_refs", "actual_design_tool_events"}:
            continue
        category = "declared" if field in declared_fields else "observed"
        facts[category].append({"field": field, "value": value,
            "source": {"kind": "caller_declaration" if category == "declared" else "coordinator_artifact",
                       "reference": audit_ref["artifact_id"] + "#/" + field},
            "evidence_refs": [audit_ref], "limitation": "Synthetic declaration is not independent biological validation"})
    evidence = {"question_record": {"objective": plan["context"]["task"]["scientific_objective"],
        "estimand": "Association across declared collection ages among constructed independent units",
        "action": "infer", "source": {"kind": "caller_declaration", "reference": "synthetic-control-objective"},
        "evidence_refs": [audit_ref]}, "facts": facts, "evidence_refs": [audit_ref],
        "diagnostic_refs": [audit_ref], "allowed_read_selectors": [],
        "verified_artifacts": [{"ref": audit_ref, "value": audit}]}
    call_limits = {"tokens": 40, "calls": 2, "seconds": 20, "context_bytes": 128_000, "memory_mb": 512}
    policy = {"format": "devbench.rna-investigation-policy/1", "stage": "post_fit_explanation",
        "allocation": allocation, "max_context_bytes": 128_000,
        "statistics_projection": {"rule": "deterministic_head", "max_rows": 2, "requirement_name": None},
        "budget_policy": _policy(max_total_tokens=1000, max_calls=30, max_total_seconds=500,
            max_context_bytes=128_000, reconciliation={"tokens": 160, "calls": 8, "seconds": 80, "context_bytes": 128_000}),
        "parent_call_limits": {"default": call_limits}, "child_call_limits": call_limits}
    selector = PostFitSelector(method["version_id"], inputs, allocation)
    backend = ObservingBackend(methods, selector, cancel_after_child=cancel_after_child)
    ledger = WorkflowOperationLedger(root / "operations.sqlite")
    executor = ActualDispatcherExecutor(qc_only=qc_only)
    actions = WorkflowActions(planner, ledger, actor="fixture-coordinator", selector=selector,
        version_ids=[method["version_id"]], declarations=[{"draft_id": "bounded-shared-fit", "branch_id": branch["branch_id"]}],
        allow_model_execution=True, allow_data_execution=True, executor=executor,
        investigation_policy=policy, investigation_evidence=evidence,
        investigation_authority=InvestigationExecutionAuthority(check=lambda request: True,
            data_classification="synthetic", budget_policy="hard_total_tokens"))
    return {"root": root, "methods": methods, "planner": planner, "plan": plan, "backend": backend,
        "ledger": ledger, "actions": actions, "selector": selector, "executor": executor,
        "analysis": analysis, "inputs": inputs, "input_bytes": {name: path.read_bytes() for name, path in paths.items()}}


def _finish(case):
    checkpoint = case["root"] / "checkpoint.sqlite"
    with create_rna_workflow(mode="independent_investigations", actions=case["actions"],
            checkpoint_path=checkpoint, investigation_backend=case["backend"]) as workflow:
        state = case["actions"].create_state(case["plan"]["session_id"])
        first = workflow.start(state)
        assert first["phase"] == "review" and first["status"] == "review_required", first.get("failure")
        assert case["executor"].executions == 0 and not case["backend"].child_contexts
    with create_rna_workflow(mode="independent_investigations", actions=case["actions"],
            checkpoint_path=checkpoint, investigation_backend=case["backend"]) as workflow:
        second = workflow.resume(state["workflow_id"])
        assert second["phase"] == "execution_review" and second["status"] == "review_required", second.get("failure")
        assert case["executor"].executions == 0 and not case["backend"].child_contexts
    with create_rna_workflow(mode="independent_investigations", actions=case["actions"],
            checkpoint_path=checkpoint, investigation_backend=case["backend"]) as workflow:
        terminal = workflow.resume(state["workflow_id"])
    return {**case, "checkpoint": checkpoint, "terminal": terminal, "identity": state["workflow_id"]}


@pytest.fixture(scope="module")
def completed_postfit(tmp_path_factory):
    pytest.importorskip("langgraph")
    return _finish(_case(tmp_path_factory.mktemp("actual-postfit-workflow")))


def _packets(case):
    return [case["backend"].read_packet(case["backend"].read_dispatch(ref)["request"]["packet_ref"])
            for ref in case["terminal"]["dispatch_refs"].values()]


def test_actual_postfit_factory_shares_one_fit_and_verified_family(completed_postfit):
    case = completed_postfit
    terminal = case["terminal"]
    assert terminal["status"] == "abstained", terminal.get("failure")
    assert terminal["allocated_children"] == 2 and len(terminal["result_refs"]) == 2
    assert terminal["stage"] == "post_fit_explanation"
    assert case["executor"].executions == 1 and case["executor"].calls == ["fit_temporal_trajectory"]
    methods, planner = case["methods"], case["planner"]
    plan = planner.get(case["identity"])
    assert len(methods.list_records("run")) == len(plan["results"]) == 1
    run = methods.get("run", plan["results"][0]["provenance"]["run_id"])
    actual = json.loads(methods.artifact_path(run["outputs"]["statistics"]).read_bytes())
    original = json.loads(case["executor"].raw_output_bytes[0])
    assert original == run["execution"]["tool_events"][0]["result"]
    assert {key: actual[key] for key in original} == original
    assert set(actual) == set(original) | {"fdr_methods"}
    assert actual["fdr_methods"] == {"q_value": "benjamini_hochberg", "padj": "benjamini_hochberg"}
    assert actual["family"] == "gaussian" and actual["normalization_method"] == "none"
    assert actual["fdr_scope"] == "requested_genes" and set(actual["genes"]) == set(GENES)
    assert actual["design"]["is_full_rank"] is True
    assert run["parameters"] == PARAMETERS
    assert methods.provenance(run["run_id"])["valid"] is True
    packets = _packets(case)
    views = [next(row for row in packet["facts"]["observed"] if row["field"] == "gene_statistics") for packet in packets]
    assert views[0] == views[1]
    for view in views:
        stats = view["value"]
        assert stats["whole_family_refs"] == [_ref(methods, run["outputs"]["statistics"])]
        assert [row["hypothesis_id"] for row in stats["rows"]] == GENES[:2]
        assert stats["tested_hypotheses"] == 3 and stats["represented_hypotheses"] == 2
        assert stats["missingness"]["unrepresented_hypotheses"] == 1
        assert stats["failed_estimates"] == sum(row["p_value"] is None for row in actual["genes"].values())
        for row in stats["rows"]:
            original = actual["genes"][row["hypothesis_id"]]
            assert row["p_value"] == original["p_value"] <= row["q_value"] == original["q_value"]
        compact = json.loads(methods.artifact_path(view["evidence_refs"][0]["artifact_id"]).read_bytes())
        assert compact["gene_statistics"] == stats
        assert view["source"]["reference"].endswith("#/gene_statistics")
    for packet in packets:
        assert not packet["visual_content_supplied"] and packet["requirement_records"] == []
        assert "PARENT-PRIVATE-CANARY" not in json.dumps(packet)
        assert any(row["field"] == "visual_assessment" for row in packet["facts"]["unknown"])
        for field in ("time_labels", "batch_labels", "biological_units"):
            fact = next(row for row in packet["facts"]["declared"] if row["field"] == field)
            identity, pointer = fact["source"]["reference"].split("#/", 1)
            assert json.loads(methods.artifact_path(identity).read_bytes())[pointer] == fact["value"]
    assert len(case["backend"].before_children) == 2
    assert len({packet["investigation_id"] for packet in packets}) == 2
    for packet in packets:
        private_root = methods.root / "offline-fixture-children" / packet["investigation_id"]
        assert private_root.is_dir() and private_root.stat().st_mode & 0o077 == 0
    assert all(packet["scope"]["execution_authority_status"] == "not_supplied" for packet in packets)
    phases = [context["decision_kind"] for context in case["selector"].contexts]
    assert phases == ["method_scientific_planning", "method_capability_resolution", "method_trial_design",
                      "method_preliminary_explanation", "method_recommendation"]
    assert "verified_output_view" in case["selector"].contexts[-2]
    assert case["selector"].contexts[-1]["investigation_evidence"][0]["stage"] == "post_fit_explanation"
    assert plan["approvals"] == {} and plan["recommendation"]["choice"] is None
    assert methods.analysis(case["analysis"]["analysis_id"]) == case["analysis"]
    for name, identity in case["inputs"].items():
        assert methods.artifact_path(identity).read_bytes() == case["input_bytes"][name]
    campaign = case["ledger"].get_campaign(case["identity"])
    assert campaign["fork"]["stage"] == "post_fit_explanation" and campaign["fork"]["request"]["child_ids"] == terminal["child_ids"]
    charges = case["ledger"].campaign_reservations(case["identity"])
    assert len(charges) == len(phases) + 2 and all(row["status"] == "completed" for row in charges)
    assert all(row["request"]["runtime_digest"] == campaign["capability"]["runtime_digest"] for row in charges)
    assert campaign["verified_spent_tokens"] > 0 and campaign["unresolved_count"] == 0
    recommendation_charge = next(row for row in charges if row["node"] == "parent:method_recommendation")
    assert recommendation_charge["purpose"] == "parent_reconciliation"
    assert recommendation_charge["limits"] == campaign["identity"]["protected_parent_allocations"]["method_recommendation"]


def test_actual_postfit_resume_and_readonly_replay_have_zero_fit_or_model_calls(completed_postfit, monkeypatch):
    case = completed_postfit
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    with create_rna_workflow(mode="independent_investigations", actions=case["actions"],
            checkpoint_path=case["checkpoint"], investigation_backend=case["backend"]) as workflow:
        assert workflow.resume(case["identity"]) == case["terminal"]
    methods = MethodService(case["methods"].root, readonly=True)
    ledger = WorkflowOperationLedger(case["ledger"].path, readonly=True)
    backend = WorkflowFixtureBackend(methods, case["selector"])
    reader = WorkflowActions(ScientificMethodPlanner(methods), ledger, actor="readonly-fixture-replay",
        selector=case["selector"], investigation_policy=case["actions"].investigation_policy,
        investigation_evidence=case["actions"].investigation_evidence)
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in
              (case["checkpoint"], case["ledger"].path, methods.database) if path.is_file()}
    def denied(*args, **kwargs):
        raise AssertionError("Read-only replay attempted a new effect")
    monkeypatch.setattr(ToolDispatcher, "execute", denied)
    monkeypatch.setattr(case["selector"], "select", denied)
    monkeypatch.setattr(backend, "investigate", denied)
    monkeypatch.setattr(methods, "_put", denied)
    monkeypatch.setattr(methods, "import_artifact", denied)
    with IndependentRNAWorkflow.open_replay(reader, case["checkpoint"], investigation_backend=backend) as replay:
        assert replay.replay(case["identity"]) == case["terminal"]
        assert replay.replay(case["identity"]) == case["terminal"]
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before
    assert case["executor"].calls == ["fit_temporal_trajectory"]


@pytest.mark.parametrize("mutation", ["value", "membership", "order", "family", "metadata"])
def test_real_postfit_packet_rejects_hash_valid_scientific_forgery(completed_postfit, mutation):
    case = completed_postfit
    packet = deepcopy(_packets(case)[0])
    before = case["methods"].get("method_plan_snapshot", packet["bindings"]["plan_hash"])
    fact = next(row for row in packet["facts"]["observed"] if row["field"] == "gene_statistics")
    if mutation == "value":
        fact["value"]["rows"][0]["p_value"] /= 2
    elif mutation == "membership":
        fact["value"]["rows"][0]["hypothesis_id"] = GENES[-1]
    elif mutation == "order":
        fact["value"]["rows"].reverse()
    elif mutation == "family":
        fact["value"]["whole_family_refs"] = [_ref(case["methods"], case["inputs"]["counts"])]
    else:
        fact = next(row for row in packet["facts"]["declared"] if row["field"] == "time_labels")
        fact["value"][0] = 99
    forged = retain_investigation_json(case["methods"], {fact["field"]: fact["value"]}, actor="adversarial-fixture")
    packet["evidence_refs"].append(forged)
    if mutation != "metadata":
        fact["source"]["reference"] = forged["artifact_id"] + "#/" + fact["field"]
        fact["evidence_refs"] = [forged]
    with pytest.raises(ValueError):
        verify_packet_projection(case["methods"], packet, before)
    assert case["executor"].calls == ["fit_temporal_trajectory"]


def test_actual_qc_only_success_cannot_trigger_a_postfit_fork(tmp_path):
    pytest.importorskip("langgraph")
    case = _finish(_case(tmp_path, qc_only=True))
    assert case["terminal"]["status"] == "abstained", case["terminal"].get("failure")
    assert case["terminal"]["allocated_children"] == 0 and case["terminal"]["dispatch_refs"] == {}
    assert case["executor"].calls == ["audit_expression_quality"]
    assert "fdr_methods" not in json.loads(case["executor"].raw_output_bytes[0])
    assert case["planner"].get(case["identity"])["results"][0]["status"] == "success"
    assert not case["backend"].child_contexts
    assert "method_preliminary_explanation" not in [row["decision_kind"] for row in case["selector"].contexts]


def test_model_budget_allocation_cannot_authorize_scientific_execution(tmp_path):
    pytest.importorskip("langgraph")
    prepared = _case(tmp_path, allocation=1)
    prepared["actions"].allow_data_execution = False
    case = _finish(prepared)
    assert case["terminal"]["status"] == "failed", case["terminal"].get("failure")
    assert case["terminal"]["failure"]["kind"] == "PermissionError"
    assert case["executor"].executions == 0 and not case["backend"].child_contexts
    assert case["planner"].get(case["identity"])["results"] == []
    assert not list(case["root"].rglob("final_discovery.json"))


def test_known_child_capture_settled_after_cancellation_retains_charge_without_recommendation(tmp_path):
    pytest.importorskip("langgraph")
    case = _finish(_case(tmp_path, allocation=1, cancel_after_child=True))
    assert case["terminal"]["status"] == "cancelled", case["terminal"].get("failure")
    assert len(case["terminal"]["child_ids"]) == 1 and case["executor"].calls == ["fit_temporal_trajectory"]
    assert "method_recommendation" not in [row["decision_kind"] for row in case["selector"].contexts]
    charges = case["ledger"].campaign_reservations(case["identity"])
    child = next(row for row in charges if row["purpose"] == "independent_investigation")
    assert child["capture"] is not None and child["capture"]["usage"]["normalized_total_tokens"] > 0
    campaign = case["ledger"].get_campaign(case["identity"])
    assert campaign["status"] == "cancelled" and campaign["verified_spent_tokens"] > 0
    assert case["planner"].get(case["identity"])["recommendation"]["choice"] is None
