"""Caller mode selection and bounded independent graph execution, all offline."""
import importlib
from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from devharness_core.provenance import canonical_json
from devbench.analysis.workflow_state import digest
from tests.test_rna_investigation_parent_integration import CapturedFixtureBackend, CapturedFixtureSelector


class WorkflowFixtureBackend(CapturedFixtureBackend):
    """Real immutable fixture captures, private calls, and shared ledger receipts."""
    def __init__(self, methods, selector):
        super().__init__(methods, selector)
        self.child_contexts = []
        self.fail_children = set()

    def verify_capture(self, capture_ref, reservation=None):
        if reservation is None:
            manifest = json.loads(self.methods.artifact_path(capture_ref["artifact_id"]).read_bytes())
            record = self.ledger.get_model_reservation(manifest["reservation_ref"])
            reservation = self.ledger._reservation(record, self.ledger.get_campaign(record["campaign_id"]))
        return super().verify_capture(capture_ref, reservation)

    def prepare_requests(self, packet_refs, *, budget_policy, reservation_refs):
        from devbench.analysis.rna_investigation_contract import investigation_context, validate_investigation_request
        packets = [self.read_packet(reference) for reference in packet_refs]
        roots = [self.methods.root / "offline-fixture-children" / packet["investigation_id"] for packet in packets]
        for root in roots:
            root.mkdir(mode=0o700, parents=True)
        prepared = []
        for packet, reference, reservation_ref in zip(packets, packet_refs, reservation_refs):
            request = {"format": "devbench.rna-investigation-request/1", "investigation_id": packet["investigation_id"],
                "parent_session_id": packet["parent_session_id"], "stage": packet["stage"], "packet_ref": reference,
                "backend_digest": self.capability["backend_digest"], "runtime_digest": self.capability["runtime_digest"],
                "budget_policy": budget_policy, "reservation_ref": reservation_ref}
            validate_investigation_request(request, packet=packet)
            context = investigation_context(packet)
            identity = digest({"request": request, "reservation": reservation_ref})[:32]
            binding = {"format": "devbench.model-call-request/1", "parent_session_id": packet["parent_session_id"],
                "session_id": packet["investigation_id"], "invocation_id": identity,
                "backend_digest": self.capability["backend_digest"], "runtime_digest": self.capability["runtime_digest"],
                "context_digest": digest(context), "prompt_digest": digest("offline child instructions"),
                "schema_digest": packet["return_schema_digest"], "request": request,
                "investigation_request_digest": digest(request), "packet_ref": reference}
            descriptor = {"format": "offline-fixture.invocation/1", "request": request, "binding": binding,
                          "child_roots": [str(root) for root in roots]}
            invocation_ref = self._retain(descriptor, "json")
            with self.methods._db() as db:
                self.methods._put(db, "fixture_invocation", digest(binding), {"invocation_ref": invocation_ref})
            prepared.append({"request": request, "binding": binding, "invocation_ref": invocation_ref})
        return prepared

    def read_dispatch(self, reference):
        from devbench.analysis.rna_investigations import read_investigation_json
        value = read_investigation_json(self.methods, reference)
        assert value["format"] == "offline-fixture.invocation/1"
        self.read_packet(value["request"]["packet_ref"])
        return value

    def investigate(self, request, *, execution_authority, reservation):
        from devbench.analysis.rna_investigation_contract import investigation_context, validate_investigation_result
        from tests.test_rna_investigation_contract import result
        execution_authority.require(request)
        assert self.ledger.get_model_reservation(reservation)["status"] == "active"
        assert reservation.request["request"] == request
        packet = self.read_packet(request["packet_ref"])
        context = investigation_context(packet)
        self.child_contexts.append(deepcopy(context))
        action = result(packet, transported=False)
        output = self.methods.root / "offline-fixture-children" / packet["investigation_id"] / "capture"
        output.mkdir()
        selector = CapturedFixtureSelector(action)
        selector.select(context, output, allow_model_execution=False)
        capture_ref, _ = self.verify_selection_capture(output, bindings=reservation.request, reservation=reservation)
        receipt = self.verify_capture(capture_ref, reservation)
        usage_ref = self._retain({"format": "devbench.rna-investigation-usage/1", "capture_ref": capture_ref,
                                  "receipt": receipt}, "rna_investigation_usage")
        # Independently resolve the actual captured response. A fixture selector
        # may produce malformed content; the planned response is not evidence.
        captured = json.loads((output / "response.json").read_bytes())
        result_ref, child_error = None, None
        try:
            validate_investigation_result(captured, packet=packet, transport_pending=True)
            transported = {**captured, "capture_ref": capture_ref, "usage_ref": usage_ref}
            validate_investigation_result(transported, packet=packet)
            result_ref = self._retain(transported, "rna_investigation_result")
        except ValueError as error:
            child_error = {"type": "InvalidFixtureResponse", "message": str(error)[:500]}
        invocation_ref = self.methods.get("fixture_invocation", digest(reservation.request))["invocation_ref"]
        outcome = {"status": "completed" if result_ref else "incomplete", "invocation_ref": invocation_ref, "result_ref": result_ref,
                   "capture_ref": capture_ref, "usage_ref": usage_ref, "error": child_error}
        if packet["investigation_id"] in self.fail_children:
            outcome.update(status="incomplete", result_ref=None, error={"type": "MalformedFixture", "message": "Fixture failure retained"})
        with self.methods._db() as db:
            self.methods._put(db, "fixture_completion", digest(invocation_ref), outcome)
        return outcome

    def reconcile_capture(self, invocation_ref):
        self.read_dispatch(invocation_ref)
        try:
            outcome = self.methods.get("fixture_completion", digest(invocation_ref))
        except (KeyError, ValueError):
            return {"status": "uncertain", "invocation_ref": invocation_ref, "result_ref": None,
                    "capture_ref": None, "usage_ref": None, "error": None}
        if outcome["capture_ref"] is not None:
            self.verify_capture(outcome["capture_ref"])
        return deepcopy(outcome)

    def verify_child_result(self, packet_ref, result_ref):
        from devbench.analysis.rna_investigation_contract import validate_investigation_result
        packet, result = self.read_packet(packet_ref), self.read_result(result_ref)
        validate_investigation_result(result, packet=packet)
        self.verify_capture(result["capture_ref"])
        manifest = json.loads(self.methods.artifact_path(result["capture_ref"]["artifact_id"]).read_bytes())
        response = json.loads(self.methods.artifact_path(manifest["response_ref"]["artifact_id"]).read_bytes())
        context = json.loads(self.methods.artifact_path(manifest["context_ref"]["artifact_id"]).read_bytes())
        if response != {**result, "capture_ref": None, "usage_ref": None} or context["packet"] != packet:
            raise ValueError("Fixture child response or neutral packet changed")
        return result

    def verify_incomplete_child(self, packet_ref, dispatch_ref):
        assert self.read_dispatch(dispatch_ref)["request"]["packet_ref"] == packet_ref
        outcome = self.reconcile_capture(dispatch_ref)
        return {"status": outcome["status"]}


def independent_setup(tmp_path, allocation=1, *, stage="pre_fit_methods"):
    from tests.test_extensible_methods import prepared
    from tests.test_execution_contract_repair import declaration
    from tests.test_method_planner import candidate, recommendation
    from tests.test_rna_investigation_contract import fixture
    from tests.test_rna_investigation_budget import _policy
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.rna_investigation_backend import InvestigationExecutionAuthority
    from devbench.analysis.rna_investigations import retain_investigation_json
    methods, *_ = prepared(tmp_path)
    samples = ["s" + str(index) for index in range(8)]
    donors = ["virtual-donor-" + str(index) for index in range(8)]
    matrix_path, metadata_path = tmp_path / "counts.json", tmp_path / "metadata.json"
    matrix_path.write_bytes(canonical_json({"gene_ids": ["g1", "g2"], "sample_ids": samples,
        "matrix": [[10, 12, 13, 13, 20, 21, 24, 27], [30, 27, 24, 23, 20, 16, 10, 8]]}))
    metadata_path.write_bytes(canonical_json({"samples": [{"sample_id": sample, "donor": donor,
        "collection_time": index // 2, "batch": "A" if index % 2 == 0 else "B"}
        for index, (sample, donor) in enumerate(zip(samples, donors))]}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="offline-fixture")
            for path in (matrix_path, metadata_path)]
    declared = declaration()
    declared.update(provenance={"origin": "synthetic software fixture; not biological validation"},
        biological_independence={"origin": "explicit virtual donor construction", "independent": True},
        metadata_roles={"biological_unit": "donor", "time": "collection_time", "batch": "batch"})
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Compare temporal raw-count trajectories and valid nuisance strategies",
        "computational_task": "bulk RNA temporal method investigation"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [row["artifact_id"] for row in refs]},
        actor="offline-fixture", data_classification="synthetic", rna_input_contract={"format": "devbench.bulk-rna-binding/1",
            "matrix_artifact_id": refs[0]["artifact_id"], "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declared})
    proposals = [candidate("Fixture NB linear"), candidate("Fixture NB spline"),
                 candidate("Fixture NB shared nuisance"), candidate("Fixture NB stratified nuisance")]
    params = (("df", 1), ("df", 3), ("nuisance_strategy", "shared"), ("nuisance_strategy", "stratified"))
    for row, (name, value) in zip(proposals, params):
        row["method_family"] = "negative-binomial temporal trajectory"
        row["assumptions"] = [{"statement": "The explicitly declared measurement is raw counts", "status": "satisfied",
            "evidence": "Registered synthetic raw-count input", "hard_precondition": True,
            "field": "bulk_rna_input.representation", "operator": "equals", "expected": "raw_counts"}]
        row["parameter_candidates"] = [{"name": name, "value": value, "origin": "fixture",
            "rationale": "A fixture-authored scientific comparison alternative, not a user requirement"}]
    answers = [{"candidates": proposals}]
    if allocation and stage == "pre_fit_methods":
        answers.append({"candidates": proposals})
    answers.extend([{"bindings": [{"candidate": row["name"], "method_version_id": None, "discovery": None}
                                  for row in proposals]}, recommendation(None)])
    selector = CapturedFixtureSelector(*answers)
    backend = WorkflowFixtureBackend(planner.methods, selector)
    _, evidence, _ = fixture(action="infer")
    audit = evidence["verified_artifacts"][0]["value"]
    audit.update(sample_count=8, biological_units=donors, biological_independence=True)
    audit_ref = retain_investigation_json(planner.methods, audit, actor="offline-fixture")
    for category, records in evidence["facts"].items():
        for row in records:
            row["value"] = audit[row["field"]]
            row["source"]["reference"] = audit_ref["artifact_id"] + "#/" + row["field"]
            row["evidence_refs"] = [audit_ref]
    evidence.update(diagnostic_refs=[audit_ref], evidence_refs=[audit_ref],
                    verified_artifacts=[{"ref": audit_ref, "value": audit}])
    evidence.pop("investigation_id")
    evidence["question_record"]["objective"] = plan["context"]["task"]["scientific_objective"]
    evidence["question_record"]["evidence_refs"] = [audit_ref]
    rules = [{"id": "fixture-temporal-complexity", "axis": "temporal_shape_or_complexity",
              "evidence_refs": [audit_ref], "discriminating_criterion": "Compare frozen predictive loss and residual diagnostics"}]
    if allocation == 2:
        rules.append({**deepcopy(rules[0]), "id": "fixture-nuisance-model", "axis": "valid_nuisance_model",
                      "discriminating_criterion": "Compare frozen nuisance residual and design diagnostics"})
    limits = {"tokens": 40, "calls": 2, "seconds": 20, "context_bytes": 128_000, "memory_mb": 512}
    policy = {"format": "devbench.rna-investigation-policy/1", "stage": stage, "allocation": allocation,
              "max_context_bytes": 128_000, "ambiguity_rules": rules,
              "budget_policy": _policy(max_total_tokens=1000, max_calls=30, max_total_seconds=500,
                  max_context_bytes=128_000, reconciliation={"tokens": 160, "calls": 8, "seconds": 80, "context_bytes": 128_000}),
              "parent_call_limits": {"default": limits}, "child_call_limits": limits}
    ledger = WorkflowOperationLedger(planner.methods.root / "independent-operations.sqlite")
    actions = WorkflowActions(planner, ledger, actor="offline-fixture-coordinator", selector=selector,
        allow_model_execution=True, investigation_policy=policy, investigation_evidence=evidence,
        investigation_authority=InvestigationExecutionAuthority(check=lambda request: True,
            data_classification="synthetic", budget_policy="hard_total_tokens"))
    return planner, plan, backend, ledger, actions


def factory():
    module = importlib.import_module("devbench.analysis.workflow_actions")
    assert hasattr(module, "create_rna_workflow"), "The executable RNA workflow factory is missing"
    return module.create_rna_workflow


def test_single_agent_is_default_and_factory_construction_is_inert(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.langgraph_workflow import SerialRNAWorkflow
    class Actions:
        calls = []
        def perform(self, *args):
            self.calls.append(args)
            raise AssertionError("Construction dispatched work")
    actions = Actions()
    with factory()(actions=actions, checkpoint_path=tmp_path / "serial.sqlite") as workflow:
        assert isinstance(workflow, SerialRNAWorkflow)
        assert actions.calls == []


def test_independent_mode_requires_explicit_backend_and_has_no_bypass(tmp_path):
    selected = factory()
    with pytest.raises(ValueError, match="backend"):
        selected(mode="independent_investigations", actions=None, checkpoint_path=tmp_path / "independent.sqlite")
    with pytest.raises(TypeError):
        selected(mode="independent_investigations", actions=None, checkpoint_path=tmp_path / "independent.sqlite",
                 bypass_graph=True)
    assert not (tmp_path / "independent.sqlite").exists()


def test_unknown_mode_does_not_fall_back_or_dispatch(tmp_path):
    with pytest.raises(ValueError, match="mode"):
        factory()(mode="all_agents", actions=None, checkpoint_path=tmp_path / "unknown.sqlite")
    assert not (tmp_path / "unknown.sqlite").exists()


def test_unsupported_or_unpinned_graph_fails_before_backend_admission(tmp_path, monkeypatch):
    from importlib import metadata
    calls = []
    class Backend:
        def require_admission(self, policy):
            calls.append(policy)
            raise AssertionError("Graph gate must run first")
    monkeypatch.setattr(metadata, "version", lambda name: "0.0.0")
    with pytest.raises(ValueError, match="pinned|graph"):
        factory()(mode="independent_investigations", actions=None, checkpoint_path=tmp_path / "unsupported.sqlite",
                  investigation_backend=Backend())
    assert calls == [] and not (tmp_path / "unsupported.sqlite").exists()


def test_explicit_independent_mode_requires_policy_before_dispatch(tmp_path):
    pytest.importorskip("langgraph")
    class Backend:
        calls = []
        def require_admission(self, policy):
            self.calls.append(policy)
            raise AssertionError("Policy must be explicitly configured first")
    backend = Backend()
    class Actions:
        investigation_policy = None
    with pytest.raises(ValueError, match="policy"):
        factory()(mode="independent_investigations", actions=Actions(), checkpoint_path=tmp_path / "policy.sqlite",
                  investigation_backend=backend)
    assert backend.calls == []


def test_qualified_offline_construction_compiles_private_child_and_parent_graphs(tmp_path):
    pytest.importorskip("langgraph")
    from devbench.analysis.rna_investigation_backend import AuditedInvestigationBackend
    class Backend(AuditedInvestigationBackend):
        def __init__(self):
            pass
        def require_admission(self, policy):
            assert policy == "bounded_observed_tokens"
            return {"source_digest": "a" * 64, "runtime_digest": "b" * 64}
    class Actions:
        investigation_policy = {"format": "devbench.rna-investigation-policy/1", "stage": "pre_fit_methods",
                                "allocation": 0, "budget_policy": {"kind": "bounded_observed_tokens"}}
    workflow = factory()(mode="independent_investigations", actions=Actions(),
                         checkpoint_path=tmp_path / "qualified.sqlite", investigation_backend=Backend())
    try:
        assert hasattr(workflow, "graph"), "The independent controller must compile an executable graph"
        assert hasattr(workflow, "child_graph"), "Each child needs an isolated subgraph mapping"
        assert {"start", "resume", "reconcile", "replay"} <= set(dir(workflow))
    finally:
        if hasattr(workflow, "close"):
            workflow.close()


def test_independent_factory_rejects_unaudited_capability_provider(tmp_path):
    pytest.importorskip("langgraph")
    class Backend:
        def require_admission(self, policy):
            raise AssertionError("Unaudited capability provider reached admission")
    class Actions:
        investigation_policy = {"format": "devbench.rna-investigation-policy/1", "stage": "pre_fit_methods",
                                "allocation": 0, "budget_policy": {"kind": "hard_total_tokens"}}
    with pytest.raises(TypeError, match="audited|protected"):
        factory()(mode="independent_investigations", actions=Actions(), checkpoint_path=tmp_path / "forged.sqlite",
                  investigation_backend=Backend())
    assert not (tmp_path / "forged.sqlite").exists()


@pytest.mark.parametrize("allocation", [0, 1, 2])
def test_real_planner_factory_charges_parent_and_private_children_and_preserves_pause(tmp_path, allocation):
    pytest.importorskip("langgraph")
    from devbench.analysis.rna_investigation_contract import validate_independent_cursor
    from devbench.analysis.workflow_state import validate_workflow_state
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, allocation)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "independent.sqlite", investigation_backend=backend) as workflow:
        assert backend.child_contexts == [] and planner.get(plan["session_id"]) == plan
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["status"] == "review_required", result.get("failure")
        validate_independent_cursor(result)
        validate_workflow_state(result["parent_cursor"])
        assert result["allocated_children"] == allocation
        assert len(backend.child_contexts) == allocation
        if allocation:
            assert result["reconciliation_ref"] is not None
            saved = planner.get(plan["session_id"])
            assert saved["results"] == [] and saved["approvals"] == {} and saved["registry"] == {}
            assert len(saved["investigation_evidence"]) == 1
            assert "investigation_evidence" in actions.selector.contexts[1]
            parent_names = [row["name"] for row in saved["scientific_candidates"]]
            assert all(not any(name in json.dumps(context) for name in parent_names) for context in backend.child_contexts)
        final = workflow.resume(result["workflow_id"])
        assert final["status"] == "abstained", final.get("failure")
        campaign = ledger.get_campaign(result["workflow_id"])
        assert campaign["verified_calls"] == 2 * (3 + allocation + bool(allocation))
        assert campaign["unresolved_count"] == 0
        assert planner.get(plan["session_id"])["results"] == []
        assert workflow.resume(result["workflow_id"]) == final
        assert len(backend.child_contexts) == allocation


def test_postfit_without_actual_fit_has_zero_children_and_no_preliminary_model_call(tmp_path):
    pytest.importorskip("langgraph")
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1, stage="post_fit_explanation")
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "postfit-empty.sqlite", investigation_backend=backend) as workflow:
        paused = workflow.start(actions.create_state(plan["session_id"]))
        assert paused["status"] == "review_required"
        final = workflow.resume(plan["session_id"])
        assert final["status"] == "abstained", final.get("failure")
        assert final["allocated_children"] == 0
        assert final["decision"]["status"] == "not_triggered"
        assert backend.child_contexts == []
        assert [row["decision_kind"] for row in actions.selector.contexts] == ["method_scientific_planning",
            "method_capability_resolution", "method_recommendation"]


@pytest.mark.parametrize("kind", ["one_candidate_facts", "same_choices_different_names", "wrong_representation", "wrong_family"])
def test_compatible_facts_names_and_ineligible_models_cannot_nominate_scientific_choices(tmp_path, kind):
    from tests.test_method_planner import recommendation
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    left, right = deepcopy(actions.selector.answers[0]["candidates"][:2])
    if kind == "one_candidate_facts":
        left["assumptions"] = [{"statement": field, "status": "satisfied", "evidence": "Registered audit",
            "hard_precondition": True, "field": "bulk_rna_input." + field, "operator": "equals", "expected": True}
            for field in ("alignment_valid", "measurements_finite")]
        candidates = [left]
    elif kind == "same_choices_different_names":
        right = {**deepcopy(left), "name": "A different label for the same planned procedure"}
        candidates = [left, right]
    elif kind == "wrong_representation":
        right["method_family"] = "Gaussian temporal splines"
        right["assumptions"][0]["expected"] = "normalized_expression"
        candidates = [left, right]
    else:
        right["method_family"] = "Gaussian temporal splines"
        candidates = [left, right]
    actions.selector.answers = [{"candidates": candidates}, {"bindings": [{"candidate": row["name"],
        "method_version_id": None, "discovery": None} for row in candidates]}, recommendation(None)]
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "unjustified.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["status"] == "review_required", result.get("failure")
        assert result["allocated_children"] == 0
        assert result["decision"]["status"] == "not_triggered"
        assert backend.child_contexts == []


def test_one_method_with_two_explicit_feasible_parameter_variants_can_nominate(tmp_path):
    from tests.test_method_planner import recommendation
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    left, right = deepcopy(actions.selector.answers[0]["candidates"][:2])
    left["parameter_candidates"].extend(right["parameter_candidates"])
    actions.selector.answers = [{"candidates": [left]}, {"candidates": [left]},
        {"bindings": [{"candidate": left["name"], "method_version_id": None, "discovery": None}]}, recommendation(None)]
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "parameter-variants.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["status"] == "review_required", result.get("failure")
        assert result["allocated_children"] == 1
        assert len(set(result["decision"]["features"]["ambiguity_items"][0]["hypothesis_ids"])) == 2
        assert len(backend.child_contexts) == 1


def test_two_child_ceiling_admits_one_when_only_one_consequential_axis_is_available(tmp_path):
    from tests.test_method_planner import recommendation
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 2)
    temporal = deepcopy(actions.selector.answers[0]["candidates"][:2])
    actions.selector.answers = [{"candidates": temporal}, {"candidates": temporal},
        {"bindings": [{"candidate": row["name"], "method_version_id": None, "discovery": None}
                      for row in temporal]}, recommendation(None)]
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "one-admissible-axis.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["allocated_children"] == 1
        assert len(backend.child_contexts) == 1
        assert result["status"] == "review_required", result.get("failure")


@pytest.mark.parametrize("allocation", [1, 2])
def test_private_packets_receive_exact_scoped_briefs_bound_to_charged_nomination(tmp_path, allocation):
    from devbench.analysis.rna_investigations import read_investigation_json
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, allocation)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "briefs.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        tasks = [context["packet"]["task"] for context in backend.child_contexts]
        assert len(set(tasks)) == allocation, "Each admitted gap requires a distinct scoped researcher brief"
        for context in backend.child_contexts:
            packet = context["packet"]
            assert "brief=" in packet["task"], "Task must carry its immutable scoped assignment reference"
            brief_id = packet["task"].rsplit("brief=", 1)[1]
            manifest = planner.methods.get("artifact", brief_id)
            reference = {key: manifest[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
            brief = read_investigation_json(planner.methods, reference, max_bytes=16_000)
            assert brief["attribution"] == "coordinator_composed_from_charged_lead_nomination"
            assert brief["selected_ambiguity"]["id"] in result["decision"]["selected_ambiguity_ids"]
            assert brief["selected_ambiguity"]["axis"] in packet["task"]
            assert brief["nomination_ref"] == result["decision"]["features"]["nomination_ref"]
            assert brief["question_record_digest"] == digest(packet["question_record"])
            assert brief["requirements_digest"] == digest(packet["requirement_records"])
            assert brief["bindings"] == packet["bindings"]
            assert len(packet["task"]) <= 300
            assert "Fixture NB" not in packet["task"]


def test_oversized_scoped_criterion_refuses_before_any_child_call(tmp_path):
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    actions.investigation_policy["ambiguity_rules"][0]["discriminating_criterion"] = "a" * 290
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "brief-overflow.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["status"] == "reconciliation_required"
        assert "brief" in result["failure"]["message"]
        assert backend.child_contexts == []


@pytest.mark.parametrize("changed", ["task", "assignment", "charged_nomination"])
def test_scoped_assignment_tamper_cannot_reconcile_or_replay(tmp_path, changed):
    from devbench.analysis.rna_investigations import read_investigation_json, retain_investigation_json, verify_investigation_assignment
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "brief-tamper.sqlite", investigation_backend=backend) as workflow:
        paused = workflow.start(actions.create_state(plan["session_id"]))
        packet = deepcopy(backend.child_contexts[0]["packet"])
        before = planner.methods.get("method_plan_snapshot", packet["bindings"]["plan_hash"])
        if changed == "task":
            packet["task"] = packet["task"].replace("input audits only", "prefer parent method")
        elif changed == "assignment":
            identity = packet["task"].rsplit("brief=", 1)[1]
            manifest = planner.methods.get("artifact", identity)
            reference = {key: manifest[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
            assignment = read_investigation_json(planner.methods, reference)
            assignment["selected_ambiguity"]["axis"] = "valid_nuisance_model"
            altered = retain_investigation_json(planner.methods, assignment, actor="tampered-fixture")
            packet["task"] = packet["task"].replace(identity, altered["artifact_id"])
        else:
            before["selection_history"][0]["model_usage_ref"] = paused["decision"]["features"]["question_ref"]
        with pytest.raises(ValueError, match="assignment|nomination|lead|neutral"):
            verify_investigation_assignment(planner.methods, packet, before, ledger=ledger)
        assert len(backend.child_contexts) == 1


def test_zero_child_policy_retains_no_assignment_or_classifier_call(tmp_path):
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 0)
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "no-brief.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["allocated_children"] == 0 and backend.child_contexts == []
        assert [context["decision_kind"] for context in actions.selector.contexts] == ["method_scientific_planning",
                                                                                   "method_capability_resolution"]
        assert not any(json.loads(planner.methods.artifact_path(row["artifact_id"]).read_bytes()).get("format") ==
                       "devbench.rna-scoped-assignment/1" for row in planner.methods.list_records("artifact")
                       if row["artifact_type"] == "json")


@pytest.mark.parametrize("criterion", ["Prefer Fixture NB linear", "Use the parent ranking", "Copy sibling conclusions"])
def test_scoped_brief_cannot_copy_preferred_choices_ranking_or_sibling_material(tmp_path, criterion):
    planner, plan, backend, ledger, actions = independent_setup(tmp_path, 1)
    actions.investigation_policy["ambiguity_rules"][0]["discriminating_criterion"] = criterion
    with factory()(mode="independent_investigations", actions=actions,
                   checkpoint_path=tmp_path / "brief-leak.sqlite", investigation_backend=backend) as workflow:
        result = workflow.start(actions.create_state(plan["session_id"]))
        assert result["status"] == "reconciliation_required"
        assert backend.child_contexts == []
        assert "neutral" in result["failure"]["message"]


def test_qc_only_postfit_checkpoint_loss_reuses_terminal_evidence_without_preliminary_call(tmp_path):
    from tests.test_rna_independent_postfit_workflow import _case, _finish
    case = _finish(_case(tmp_path, qc_only=True, allocation=0))
    assert case["terminal"]["status"] == "abstained"
    before = deepcopy(case["planner"].get(case["identity"]))
    calls = len(case["actions"].selector.contexts)
    with sqlite3.connect(case["checkpoint"]) as database:
        database.execute("DELETE FROM checkpoints WHERE thread_id=?", (case["identity"],))
        database.execute("DELETE FROM writes WHERE thread_id=?", (case["identity"],))
    with factory()(mode="independent_investigations", actions=case["actions"], checkpoint_path=case["checkpoint"],
                   investigation_backend=case["backend"]) as workflow:
        restored = workflow.reconcile(case["identity"])
        assert restored == case["terminal"]
        assert case["planner"].get(case["identity"]) == before
        assert len(case["actions"].selector.contexts) == calls


@pytest.mark.parametrize("phase,answer_index,tokens", [("generate", 0, 12), ("resolve", 1, 24), ("recommend", 2, 36)])
def test_captured_failed_parent_binds_verified_failure_snapshot_and_replays_readonly(tmp_path, monkeypatch, phase, answer_index, tokens):
    from tests.test_rna_investigation_recovery import recovery_setup
    from tests.test_rna_investigation_replay import replay_actions, deny_effects, fingerprint
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=0)
    identity = plan["session_id"]
    actions.selector.answers[answer_index] = RuntimeError("Captured fixture " + phase + " failure")
    try:
        failed = workflow.start(actions.create_state(identity))
        if phase == "recommend":
            assert failed["status"] == "review_required"
            failed = workflow.resume(identity)
        assert failed["phase"] == phase and failed["status"] == "reconciliation_required"
        actual = deepcopy(planner.get(identity))
        assert actual["selection_history"][-1]["status"] == "failed"
        assert actual["selection_history"][-1]["model_capture_ref"]
        assert actual["recommendation"] == plan["recommendation"] and actual["approvals"] == plan["approvals"]
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == tokens
        assert failed["expected_plan_hash"] == digest(actual), "Failure cursor must bind only its verified retained failed snapshot"
        assert workflow.resume(identity) == failed
        assert workflow.reconcile(identity) == failed
        assert ledger.get(identity, phase, 0)["status"] == "uncertain"
        assert len(actions.selector.contexts) == answer_index
        checkpoint = workflow.checkpoint_path
    finally:
        workflow.close()
    readonly, reader_backend = replay_actions(planner, ledger, actions)
    before = fingerprint(tmp_path)
    effects = deny_effects(monkeypatch, readonly, reader_backend)
    with IndependentRNAWorkflow.open_replay(readonly, checkpoint, investigation_backend=reader_backend) as replay:
        assert replay.replay(identity) == failed
        assert replay.replay(identity) == failed
    assert readonly.planner.get(identity) == actual
    assert readonly.ledger.get_campaign(identity)["verified_spent_tokens"] == tokens
    assert effects == [] and fingerprint(tmp_path) == before


def test_historical_failed_cursor_replay_derives_verified_snapshot_without_migration(tmp_path, monkeypatch):
    from tests.test_rna_investigation_recovery import recovery_setup
    from tests.test_rna_investigation_replay import replay_actions, deny_effects, fingerprint
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=0)
    identity = plan["session_id"]
    actions.selector.answers[0] = RuntimeError("Captured generation failure")
    try:
        failed = workflow.start(actions.create_state(identity))
        original_hash = ledger.get(identity, "generate", 0)["request"]["expected_planner_hash"]
        legacy = {**failed, "expected_plan_hash": original_hash,
                  "parent_cursor": {**failed["parent_cursor"], "expected_planner_hash": original_hash}}
        workflow.graph.update_state(workflow._config(identity), {"cursor": legacy}, as_node="generate")
        checkpoint = workflow.checkpoint_path
    finally:
        workflow.close()
    readonly, reader_backend = replay_actions(planner, ledger, actions)
    before = fingerprint(tmp_path)
    effects = deny_effects(monkeypatch, readonly, reader_backend)
    with IndependentRNAWorkflow.open_replay(readonly, checkpoint, investigation_backend=reader_backend) as replay:
        projected = replay.replay(identity)
        assert projected["expected_plan_hash"] == digest(planner.get(identity))
        assert projected["status"] == "reconciliation_required"
    assert effects == [] and fingerprint(tmp_path) == before
@pytest.mark.parametrize("edit", ["approvals", "candidates", "objective"])
def test_failed_parent_cannot_bind_arbitrary_concurrent_planner_edits(tmp_path, monkeypatch, edit):
    from tests.test_rna_investigation_recovery import recovery_setup
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=0)
    identity = plan["session_id"]
    actions.selector.answers[0] = RuntimeError("Captured generation failure")
    perform = actions.perform
    def tampered(node, state):
        result = perform(node, state)
        if node == "generate":
            before = planner.get(identity)
            altered = deepcopy(before)
            if edit == "approvals":
                altered["approvals"] = {"forged": {"approved": True}}
            elif edit == "candidates":
                altered["scientific_candidates"] = [{"name": "Uncaptured fabricated choice"}]
            else:
                altered["context"]["task"]["scientific_objective"] = "Another unrequested objective"
            planner._save(before, altered, actor="tampered-fixture", action="fixture_arbitrary_edit")
        return result
    monkeypatch.setattr(actions, "perform", tampered)
    try:
        failed = workflow.start(actions.create_state(identity))
        assert failed["status"] == "reconciliation_required"
        assert failed["expected_plan_hash"] != digest(planner.get(identity))
        with pytest.raises(ValueError, match="stale|binding|changed|outside"):
            workflow.replay(identity)
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == 12
        assert backend.launches == []
    finally:
        workflow.close()


@pytest.mark.parametrize("phase,tokens", [("reassess", 36), ("preliminary_explanation", 48)])
def test_all_charged_special_parent_failures_keep_verified_snapshot_and_zero_effect_replay(tmp_path, monkeypatch, phase, tokens):
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    from tests.test_rna_investigation_recovery import recovery_setup
    from tests.test_rna_investigation_replay import replay_actions, deny_effects, fingerprint
    if phase == "reassess":
        planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=1)
        actions.selector.answers[1] = RuntimeError("Captured reassessment failure")
        executor = None
    else:
        from tests.test_rna_independent_postfit_workflow import _case
        case = _case(tmp_path, allocation=0)
        planner, plan, backend, ledger, actions, executor = [case[key] for key in
            ("planner", "plan", "backend", "ledger", "actions", "executor")]
        select = actions.selector.select
        def fail_preliminary(context, output, **kwargs):
            if context["decision_kind"] == "method_preliminary_explanation":
                actions.selector.answers.insert(0, RuntimeError("Captured preliminary failure"))
            return select(context, output, **kwargs)
        monkeypatch.setattr(actions.selector, "select", fail_preliminary)
        workflow = factory()(mode="independent_investigations", actions=actions,
            checkpoint_path=tmp_path / "failed-special.sqlite", investigation_backend=backend)
    identity = plan["session_id"]
    try:
        failed = workflow.start(actions.create_state(identity))
        if phase == "preliminary_explanation":
            assert failed["status"] == "review_required"
            assert workflow.resume(identity)["status"] == "review_required"
            failed = workflow.resume(identity)
        assert failed["phase"] == phase and failed["status"] == "reconciliation_required"
        actual = deepcopy(planner.get(identity))
        before = planner.methods.get("method_plan_snapshot", ledger.get(identity, phase, 0)["request"]["expected_plan_hash"])
        assert actual["selection_history"][:-1] == before["selection_history"]
        assert actual["selection_history"][-1]["status"] == "failed"
        assert actual["scientific_candidates"] == before["scientific_candidates"]
        assert actual["recommendation"] == before["recommendation"] and actual["approvals"] == before["approvals"]
        assert actual["results"] == before["results"]
        assert not actual.get("candidate_reassessment") and not actual.get("preliminary_explanation")
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == tokens
        assert failed["expected_plan_hash"] == digest(actual), "Every charged special parent failure needs the same verified snapshot proof"
        calls = len(actions.selector.contexts)
        assert workflow.resume(identity) == failed and workflow.reconcile(identity) == failed
        assert len(actions.selector.contexts) == calls
        assert ledger.get(identity, phase, 0)["status"] == "uncertain"
        if executor is not None:
            assert executor.executions == 1 and executor.calls == ["fit_temporal_trajectory"]
        checkpoint = workflow.checkpoint_path
    finally:
        workflow.close()
    readonly, reader_backend = replay_actions(planner, ledger, actions)
    tree_before = fingerprint(tmp_path)
    effects = deny_effects(monkeypatch, readonly, reader_backend)
    with IndependentRNAWorkflow.open_replay(readonly, checkpoint, investigation_backend=reader_backend) as replay:
        assert replay.replay(identity) == failed
    assert readonly.planner.get(identity) == actual
    assert readonly.ledger.get_campaign(identity)["verified_spent_tokens"] == tokens
    assert effects == [] and fingerprint(tmp_path) == tree_before


def test_captured_failed_trial_design_uses_common_parent_proof_without_fitting(tmp_path, monkeypatch):
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    from tests.test_rna_independent_postfit_workflow import _case
    from tests.test_rna_investigation_replay import replay_actions, deny_effects, fingerprint
    case = _case(tmp_path, allocation=0)
    actions, planner, ledger = [case[key] for key in ("actions", "planner", "ledger")]
    select = actions.selector.select
    def fail_design(context, output, **kwargs):
        if context["decision_kind"] == "method_trial_design":
            actions.selector.answers.insert(0, RuntimeError("Captured trial design failure"))
        return select(context, output, **kwargs)
    monkeypatch.setattr(actions.selector, "select", fail_design)
    identity = case["plan"]["session_id"]
    checkpoint = tmp_path / "failed-design.sqlite"
    with factory()(mode="independent_investigations", actions=actions,
            checkpoint_path=checkpoint, investigation_backend=case["backend"]) as workflow:
        failed = workflow.start(actions.create_state(identity))
        assert failed["phase"] == "design" and failed["status"] == "reconciliation_required"
        actual = deepcopy(planner.get(identity))
        before = planner.methods.get("method_plan_snapshot", ledger.get(identity, "design", 0)["request"]["expected_planner_hash"])
        assert actual["selection_history"][-1]["phase"] == "method_trial_design"
        assert actual["selection_history"][-1]["status"] == "failed"
        assert all(actual.get(key) == before.get(key) for key in
            (set(actual) | set(before)) - {"selection_history", "selection_in_progress"})
        assert failed["expected_plan_hash"] == digest(actual)
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == 36
        assert workflow.resume(identity) == failed and workflow.reconcile(identity) == failed
    readonly, reader_backend = replay_actions(planner, ledger, actions)
    tree_before = fingerprint(tmp_path)
    effects = deny_effects(monkeypatch, readonly, reader_backend)
    with IndependentRNAWorkflow.open_replay(readonly, checkpoint, investigation_backend=reader_backend) as replay:
        assert replay.replay(identity) == failed
    assert case["executor"].executions == 0 and not case["backend"].child_contexts
    assert readonly.planner.get(identity) == actual
    assert ledger.get(identity, "design", 0)["status"] == "uncertain"
    assert effects == [] and fingerprint(tmp_path) == tree_before


@pytest.mark.parametrize("phase,index,allocation", [("generate", 0, 0), ("resolve", 1, 0), ("reassess", 1, 1)])
@pytest.mark.parametrize("tamper", ["inserted", "replaced", "removed"])
def test_failed_parent_response_requires_its_actual_capture(tmp_path, monkeypatch, phase, index, allocation, tamper):
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    from tests.test_rna_investigation_recovery import recovery_setup
    from tests.test_rna_investigation_replay import replay_actions, deny_effects, fingerprint
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=allocation)
    identity = plan["session_id"]
    actions.selector.answers[index] = (RuntimeError("Captured transport failure") if tamper == "inserted"
                                       else {"unexpected_schema": "Captured but scientifically invalid"})
    verify = workflow._failed_parent_snapshot
    altered_hash = []
    def alter_response(cursor, node):
        if node == phase and not altered_hash:
            current = planner.get(identity)
            selection = current["selection_history"][-1]
            manifest = json.loads(planner.methods.artifact_path(selection["model_capture_ref"]["artifact_id"]).read_bytes())
            assert (manifest["response_ref"] is None) == (tamper == "inserted")
            altered = deepcopy(current)
            if tamper == "removed":
                del altered["selection_history"][-1]["response"]
            else:
                altered["selection_history"][-1]["response"] = {"uncaptured_claim": "Not returned by this invocation"}
            planner._save(current, altered, actor="offline-integrity-tamper", action="fixture_response_tamper")
            altered_hash.append(digest(altered))
        return verify(cursor, node)
    monkeypatch.setattr(workflow, "_failed_parent_snapshot", alter_response)
    try:
        failed = workflow.start(actions.create_state(identity))
        assert failed["phase"] == phase and failed["status"] == "reconciliation_required"
        assert altered_hash and failed["expected_plan_hash"] != altered_hash[0]
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == (36 if allocation else 12 * (index + 1))
        assert not planner.get(identity)["results"] and not planner.get(identity)["approvals"]
        checkpoint = workflow.checkpoint_path
    finally:
        workflow.close()
    readonly, reader_backend = replay_actions(planner, ledger, actions)
    tree_before = fingerprint(tmp_path)
    effects = deny_effects(monkeypatch, readonly, reader_backend)
    with IndependentRNAWorkflow.open_replay(readonly, checkpoint, investigation_backend=reader_backend) as replay:
        with pytest.raises(ValueError, match="response|capture|history|snapshot|stale"):
            replay.replay(identity)
    assert effects == [] and fingerprint(tmp_path) == tree_before


@pytest.mark.parametrize("phase,index,allocation", [("generate", 0, 0), ("resolve", 1, 0), ("reassess", 1, 1)])
def test_genuine_captured_schema_invalid_parent_response_remains_readonly_replayable(tmp_path, monkeypatch, phase, index, allocation):
    from devbench.analysis.langgraph_investigations import IndependentRNAWorkflow
    from tests.test_rna_investigation_recovery import recovery_setup
    from tests.test_rna_investigation_replay import replay_actions, deny_effects, fingerprint
    planner, plan, backend, ledger, actions, workflow = recovery_setup(tmp_path, allocation=allocation)
    identity = plan["session_id"]
    actual_response = {"unexpected_schema": "Retained exactly; no scientific acceptance"}
    actions.selector.answers[index] = deepcopy(actual_response)
    try:
        failed = workflow.start(actions.create_state(identity))
        actual = deepcopy(planner.get(identity))
        selection = actual["selection_history"][-1]
        assert failed["phase"] == phase and failed["status"] == "reconciliation_required"
        assert selection["status"] == "returned_unvalidated" and selection["response"] == actual_response
        manifest = json.loads(planner.methods.artifact_path(selection["model_capture_ref"]["artifact_id"]).read_bytes())
        captured = json.loads(planner.methods.artifact_path(manifest["response_ref"]["artifact_id"]).read_bytes())
        assert captured == actual_response and manifest["response_digest"] == digest(actual_response)
        assert failed["expected_plan_hash"] == digest(actual)
        assert workflow.resume(identity) == failed and workflow.reconcile(identity) == failed
        assert ledger.get(identity, phase, 0)["status"] == "uncertain"
        assert not actual["results"] and not actual["approvals"]
        assert ledger.get_campaign(identity)["verified_spent_tokens"] == (36 if allocation else 12 * (index + 1))
        checkpoint = workflow.checkpoint_path
    finally:
        workflow.close()
    readonly, reader_backend = replay_actions(planner, ledger, actions)
    tree_before = fingerprint(tmp_path)
    effects = deny_effects(monkeypatch, readonly, reader_backend)
    with IndependentRNAWorkflow.open_replay(readonly, checkpoint, investigation_backend=reader_backend) as replay:
        assert replay.replay(identity) == failed
    assert readonly.planner.get(identity) == actual
    assert effects == [] and fingerprint(tmp_path) == tree_before
