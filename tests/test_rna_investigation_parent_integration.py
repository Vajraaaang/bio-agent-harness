"""Offline parent integration controls; fixtures are not scientific validation."""
from copy import deepcopy
import hashlib
import json
import os
import threading
import time

import pytest

from devbench.analysis.methods import _digest
from devharness_core.provenance import canonical_json
from devbench.analysis.rna_investigation_backend import AuditedInvestigationBackend
from tests.test_method_planner import setup, Selector, candidate, recommendation, trial_spec
from tests.test_rna_investigation_budget import _policy, _capability
from tests.test_analysis_episodes import workspace


INVESTIGATION_ARTIFACTS = {
    "rna_investigation_packet": 48_000,
    "rna_investigation_result": 24_000,
    "rna_investigation_capture": 64_000,
    "rna_investigation_usage": 24_000,
    "rna_investigation_reconciliation": 48_000,
}


@pytest.mark.parametrize("artifact_type", INVESTIGATION_ARTIFACTS)
def test_investigation_artifacts_use_existing_immutable_store(tmp_path, artifact_type):
    planner, _, _, _, _, _ = setup(tmp_path)
    source = tmp_path / "bounded-evidence.json"
    source.write_text('{"scope":"synthetic software fixture"}', encoding="utf-8")
    imported = planner.methods.import_artifact(source, artifact_type=artifact_type, actor="coordinator")
    assert imported["artifact_type"] == artifact_type
    retained = planner.methods.artifact_path(imported["artifact_id"])
    assert retained.parent == planner.methods.root / "objects"
    assert retained.read_bytes() == source.read_bytes()
    assert not retained.stat().st_mode & 0o222


@pytest.mark.parametrize("value", ['{"x":NaN}', '{"x":Infinity}', "not JSON", '[]', '{"x":1,"x":2}'])
def test_investigation_artifacts_require_finite_json_objects(tmp_path, value):
    planner, _, _, _, _, _ = setup(tmp_path)
    source = tmp_path / "malformed-evidence.json"
    source.write_text(value, encoding="utf-8")
    before = planner.methods.list_records("artifact")
    with pytest.raises(ValueError, match="JSON|finite|object"):
        planner.methods.import_artifact(source, artifact_type="rna_investigation_result", actor="coordinator")
    assert planner.methods.list_records("artifact") == before


@pytest.mark.parametrize("artifact_type,limit", INVESTIGATION_ARTIFACTS.items())
def test_investigation_artifact_bounds_are_checked_before_import(tmp_path, artifact_type, limit):
    planner, _, _, _, _, _ = setup(tmp_path)
    source = tmp_path / "oversized-evidence.json"
    source.write_text(json.dumps({"text": "x" * limit}), encoding="utf-8")
    with pytest.raises(ValueError, match="bound|size|bytes|limit"):
        planner.methods.import_artifact(source, artifact_type=artifact_type, actor="coordinator")


def test_attachment_cannot_trust_a_caller_success_flag(tmp_path):
    planner, state, _, _, _, _ = setup(tmp_path)
    forged = {"artifact_id": "0" * 64, "sha256": "0" * 64,
              "artifact_type": "rna_investigation_reconciliation", "size_bytes": 1}
    with pytest.raises(ValueError, match="campaign|backend|capture"):
        planner.attach_investigation_evidence(state["session_id"], forged,
            expected_plan_hash=_digest(state), actor="child-claims-authority")
    assert planner.get(state["session_id"]) == state


def test_single_agent_default_still_uses_the_existing_selection_history(tmp_path):
    planner, state, _, _, _, _ = setup(tmp_path)
    selector = Selector({"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    updated = planner.get(state["session_id"])
    assert "model_campaign" not in updated
    assert len(updated["selection_history"]) == 1
    assert updated["selection_history"][0]["status"] == "accepted"
    assert "investigation_evidence" not in selector.contexts[0]


def test_campaign_cannot_start_after_an_unmetered_parent_call(tmp_path):
    planner, state, _, _, _, _ = setup(tmp_path)
    planner.generate_candidates(state["session_id"], Selector({"candidates": []}), actor="scientist")
    current = planner.get(state["session_id"])
    with pytest.raises(ValueError, match="first|history|already|before"):
        planner.attach_model_campaign(state["session_id"], None, "late-campaign", None,
            expected_plan_hash=_digest(current), actor="coordinator")
    assert planner.get(state["session_id"]) == current


def preliminary_explanation(ref):
    return {"hypotheses": [
        {"id": "composition", "statement": "Composition might explain the observed association",
         "evidence_refs": [ref], "limitations": ["Cell composition was not measured"]},
        {"id": "intrinsic", "statement": "An intrinsic response might explain the association",
         "evidence_refs": [ref], "limitations": ["Bulk association does not establish causality"]}],
        "ambiguity_items": [{"id": "composition_or_intrinsic", "axis": "composition_vs_intrinsic",
            "hypothesis_ids": ["composition", "intrinsic"], "evidence_refs": [ref],
            "missing_information_status": "sufficient", "discriminating_criterion": "Independent composition measurement"}],
        "uncertainty": ["Both explanations remain hypotheses"], "absent_assessments": ["Validated plot pixels"]}


def test_preliminary_explanation_contract_has_no_approval_or_numerical_mutation_fields():
    from devbench.analysis import method_planner_contract as contracts
    ref = {"artifact_id": "1" * 64, "sha256": "2" * 64, "artifact_type": "json", "size_bytes": 10}
    response = preliminary_explanation(ref)
    assert contracts.validate_preliminary_explanation(response) == response
    for forbidden in ("approvals", "gene_statistics", "recommendation", "params"):
        altered = deepcopy(response)
        altered[forbidden] = {}
        with pytest.raises(ValueError):
            contracts.validate_preliminary_explanation(altered)


@pytest.mark.parametrize("mutation", ["missing_hypothesis", "duplicate", "wrong_stage", "nonfinite", "unbounded"])
def test_preliminary_explanation_rejects_invalid_ambiguity(mutation):
    from devbench.analysis import method_planner_contract as contracts
    ref = {"artifact_id": "1" * 64, "sha256": "2" * 64, "artifact_type": "json", "size_bytes": 10}
    response = preliminary_explanation(ref)
    if mutation == "missing_hypothesis":
        response["ambiguity_items"][0]["hypothesis_ids"][1] = "not_retained"
    elif mutation == "duplicate":
        response["hypotheses"][1]["id"] = "composition"
    elif mutation == "wrong_stage":
        response["ambiguity_items"][0]["axis"] = "temporal_shape_or_complexity"
    elif mutation == "nonfinite":
        response["hypotheses"][0]["evidence_refs"][0]["size_bytes"] = float("nan")
    else:
        response["hypotheses"] *= 4
    with pytest.raises(ValueError):
        contracts.validate_preliminary_explanation(response)


class CapturedFixtureSelector(Selector):
    """Test-only protected capture simulation, zero provider calls."""
    def select(self, context, output, **kwargs):
        self.forwarded_limits = deepcopy(kwargs)
        (output / "context.json").write_bytes(canonical_json(context))
        native_usage = ({"exact_accounting_available": False} if getattr(self, "unknown_usage", False) else
            {"input_tokens": 8, "output_tokens": 4, "cached_input_tokens": 3, "reasoning_tokens": 2})
        (output / "usage.json").write_bytes(canonical_json(native_usage))
        if self.answers and isinstance(self.answers[0], BaseException):
            error = self.answers.pop(0)
            (output / "failure.json").write_bytes(canonical_json({"error": type(error).__name__}))
            raise error
        response = super().select(context, output, **kwargs)
        (output / "response.json").write_bytes(canonical_json(response.action))
        return response


class CapturedFixtureBackend(AuditedInvestigationBackend):
    """Explicit offline fixture verifier; never qualifies a native backend."""
    def __init__(self, methods, selector):
        self.methods, self.selector = methods, selector
        self.capability = _capability()
        self.unknown_usage = False

    def capabilities(self):
        return deepcopy(self.capability)

    def bind_ledger(self, ledger):
        self.ledger = ledger

    def parent_selection_binding(self, selector, context, *, parent_session_id, session_id, invocation_id):
        if selector is not self.selector:
            raise ValueError("A raw selector cannot bypass the admitted protected backend")
        selector.unknown_usage = self.unknown_usage
        return {"parent_session_id": parent_session_id, "session_id": session_id,
            "invocation_id": invocation_id, "backend_digest": self.capability["backend_digest"],
            "runtime_digest": self.capability["runtime_digest"], "context_digest": _digest(context),
            "prompt_digest": _digest("offline fixture instructions"),
            "schema_digest": _digest(context["decision_kind"])}

    def parent_call_limits(self, context, campaign, planner_limits):
        allocation = deepcopy(campaign["identity"]["parent_call_limits"]["default"])
        allocation["seconds"] = min(allocation["seconds"], planner_limits["session_timeout_seconds"])
        return allocation

    def verify_selection_capture(self, output, *, bindings, reservation):
        def retain(path):
            record = self.methods.import_artifact(path, artifact_type="json", actor="offline-fixture-capture")
            return {key: record[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
        context = retain(output / "context.json")
        if context["sha256"] != bindings["context_digest"]:
            raise ValueError("Fixture context capture binding differs")
        response = retain(output / "response.json") if (output / "response.json").exists() else None
        usage = retain(output / "usage.json")
        request = reservation.request
        manifest = {"format": "offline-fixture.model-capture/1", "scope": "fixture_only",
            "reservation_ref": reservation.ref, "request_digest": reservation.request_digest,
            "bindings": {key: request[key] for key in ("session_id", "invocation_id", "backend_digest",
                "runtime_digest", "prompt_digest", "schema_digest", "context_digest")},
            "context_ref": context, "response_ref": response, "usage_ref": usage,
            "response_digest": response["sha256"] if response else None,
            "status": "completed" if response else "failed", "unknown_usage": self.unknown_usage}
        path = output / "capture.json"
        path.write_bytes(canonical_json(manifest))
        retained = self.methods.import_artifact(path, artifact_type="rna_investigation_capture", actor="offline-fixture-capture")
        capture = {key: retained[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
        return capture, {**self.verify_capture(capture, reservation), "response_digest": manifest["response_digest"]}

    def verify_capture(self, capture_ref, reservation=None):
        path = self.methods.artifact_path(capture_ref["artifact_id"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != capture_ref["sha256"]:
            raise ValueError("Capture manifest hash changed")
        manifest = json.loads(path.read_bytes())
        if reservation is None:
            record = self.ledger.get_model_reservation(manifest["reservation_ref"])
            reservation = self.ledger._reservation(record, self.ledger.get_campaign(record["campaign_id"]))
        if manifest["scope"] != "fixture_only" or manifest["request_digest"] != reservation.request_digest:
            raise ValueError("Fixture capture request differs")
        for name in ("context_ref", "response_ref", "usage_ref"):
            ref = manifest[name]
            if ref is not None:
                actual = self.methods.artifact_path(ref["artifact_id"])
                if hashlib.sha256(actual.read_bytes()).hexdigest() != ref["sha256"]:
                    raise ValueError("Fixture raw capture changed")
        raw_usage = json.loads(self.methods.artifact_path(manifest["usage_ref"]["artifact_id"]).read_bytes())
        from devbench.study.model_telemetry import normalize_usage
        usage = normalize_usage(raw_usage)
        return {"format": "devbench.model-call-capture/1", "reservation_ref": reservation.ref,
            "request_digest": reservation.request_digest, **manifest["bindings"], "status": manifest["status"],
            "capture_ref": capture_ref, "usage": usage, "actual_calls": 2,
            "native_session_ids": ["offline-preflight-" + reservation.ref[-32:], "offline-action-" + reservation.ref[-32:]],
            "elapsed_seconds": 1, "launch_confirmed": True}

    def verify_child_result(self, packet_ref, result_ref):
        from devbench.analysis.rna_investigation_contract import validate_investigation_result, investigation_context
        packet = self.read_packet(packet_ref)
        result = validate_investigation_result(self.read_result(result_ref), packet=packet)
        receipt = self.verify_capture(result["capture_ref"])
        capture = self._document(result["capture_ref"])
        raw = json.loads(self.methods.artifact_path(capture["response_ref"]["artifact_id"]).read_bytes())
        if ({**raw, "capture_ref": result["capture_ref"], "usage_ref": result["usage_ref"]} != result
                or receipt["context_digest"] != _digest(investigation_context(packet))
                or receipt["schema_digest"] != packet["return_schema_digest"]
                or packet["bindings"]["source_digest"] != self.capability["source_digest"]
                or packet["bindings"]["runtime_digest"] != self.capability["runtime_digest"]):
            raise ValueError("Fixture child actual capture, schema or runtime binding differs")
        usage = self._document(result["usage_ref"], artifact_type="rna_investigation_usage", max_bytes=24_000)
        charge = self.ledger.get_model_reservation(capture["reservation_ref"])
        if usage != {"format": "devbench.rna-investigation-usage/1", "capture_ref": result["capture_ref"], "receipt": receipt}:
            raise ValueError("Fixture child usage differs from actual capture")
        if charge["status"] != "completed" or charge["capture"] != receipt:
            raise ValueError("Fixture child was not charged through the retained ledger")
        return result

    def verify_incomplete_child(self, packet_ref, dispatch_ref):
        from devbench.analysis.rna_investigation_contract import investigation_context
        packet = self.read_packet(packet_ref)
        receipt = self.verify_capture(dispatch_ref)
        if receipt["status"] != "failed" or receipt["context_digest"] != _digest(investigation_context(packet)):
            raise ValueError("Fixture missing child has no failed exact capture")
        return {"status": "incomplete"}


def metered_setup(tmp_path, *answers, policy_kind="hard_total_tokens"):
    from devbench.analysis.workflow_operations import WorkflowOperationLedger
    from devbench.analysis.rna_investigations import retain_investigation_json
    from tests.test_rna_investigation_contract import fixture
    planner, state, _, analysis, branch, step = setup(tmp_path)
    selector = CapturedFixtureSelector(*answers)
    backend = CapturedFixtureBackend(planner.methods, selector)
    ledger = WorkflowOperationLedger(tmp_path / "campaign-operations.sqlite")
    limits = {"tokens": 20, "calls": 2, "seconds": 10, "context_bytes": 65536, "memory_mb": 512}
    policy = _policy(policy_kind, max_total_tokens=300 if policy_kind == "hard_total_tokens" else None,
        soft_token_target=300 if policy_kind != "hard_total_tokens" else None, max_calls=24,
        reconciliation={"tokens": 20, "calls": 2, "seconds": 10, "context_bytes": 65536})
    _, evidence, _ = fixture()
    audit_ref = retain_investigation_json(planner.methods, evidence["verified_artifacts"][0]["value"],
        actor="offline-fixture-audit-before-parent")
    question = {**deepcopy(evidence["question_record"]),
        "objective": state["context"]["task"]["scientific_objective"],
        "estimand": "Compare diagnostic QC approaches on the supplied synthetic observations",
        "evidence_refs": [audit_ref]}
    frozen = {"source_digest": backend.capability["source_digest"],
        "runtime_digest": backend.capability["runtime_digest"], "max_context_bytes": 128_000,
        "question_record": question}
    ledger.open_campaign("parent-campaign", session_id=state["session_id"], policy=policy,
        capability=backend.capabilities(), identity={"expected_plan_hash": _digest(state),
            "source_digest": backend.capability["source_digest"], "runtime_digest": backend.capability["runtime_digest"],
            "parent_call_limits": {"default": limits}, "investigation_policy": frozen,
            "investigation_policy_digest": _digest(frozen)})
    attached = planner.attach_model_campaign(state["session_id"], ledger, "parent-campaign", backend,
        expected_plan_hash=_digest(state), actor="coordinator")
    return planner, attached, selector, backend, ledger, analysis, branch, step


def test_parent_only_campaign_charges_generation_resolution_design_and_recommendation(tmp_path):
    planner, state, selector, backend, ledger, _, _, step = metered_setup(tmp_path)
    selector.answers = [{"candidates": [candidate()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": step["method_version_id"], "discovery": None}]},
        {"experiments": [trial_spec(step)]}, recommendation(None)]
    session = state["session_id"]
    planner.generate_candidates(session, selector, actor="scientist")
    planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    planner.design_trials(session, selector, actor="scientist")
    planner.recommend(session, selector, actor="scientist")
    campaign = ledger.get_campaign("parent-campaign")
    assert campaign["verified_spent_tokens"] == 48
    assert campaign["verified_calls"] == 8
    assert campaign["unresolved_count"] == 0
    history = planner.get(session)["selection_history"]
    assert len(history) == 4 and all(item["status"] == "accepted" for item in history)
    assert all(item["model_capture_ref"] and item["model_reservation_ref"] for item in history)
    assert all("investigation_evidence" not in context for context in selector.contexts)


def test_campaign_blocks_raw_selector_bypass_before_invocation(tmp_path):
    planner, state, _, _, ledger, _, _, _ = metered_setup(tmp_path)
    unauthorized = Selector({"candidates": []})
    with pytest.raises(ValueError, match="bypass|backend|selector"):
        planner.generate_candidates(state["session_id"], unauthorized, actor="scientist")
    assert unauthorized.contexts == []
    assert ledger.get_campaign("parent-campaign")["verified_calls"] == 0


def test_runtime_restoration_requires_fresh_exact_campaign_binding(tmp_path):
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": []})
    restored = type(planner)(planner.methods)
    with pytest.raises(ValueError, match="runtime|backend|authority"):
        restored.generate_candidates(state["session_id"], selector, actor="scientist")
    assert selector.contexts == []
    restored.attach_model_campaign(state["session_id"], ledger, "parent-campaign", backend,
        expected_plan_hash=_digest(state), actor="fresh-current-coordinator")
    restored.generate_candidates(state["session_id"], selector, actor="scientist")
    assert ledger.get_campaign("parent-campaign")["verified_calls"] == 2


def test_failed_parent_attempt_is_charged_and_retry_has_new_reservation(tmp_path):
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path,
        RuntimeError("observable offline failure"), {"candidates": []})
    with pytest.raises(RuntimeError, match="offline failure"):
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 12
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 24
    history = planner.get(state["session_id"])["selection_history"]
    assert history[0]["status"] == "failed" and history[1]["status"] == "accepted"
    assert history[0]["model_reservation_ref"] != history[1]["model_reservation_ref"]


def test_unknown_parent_usage_stops_future_calls_without_a_zero_charge(tmp_path):
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": []})
    backend.unknown_usage = True
    with pytest.raises((ValueError, RuntimeError), match="usage|uncertain|unresolved"):
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    campaign = ledger.get_campaign("parent-campaign")
    assert campaign["unresolved_count"] == 1 and campaign["outstanding_reserved_tokens"] == 20
    with pytest.raises((ValueError, RuntimeError)):
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert len(selector.contexts) == 1


def test_preliminary_explanation_is_a_charged_nomination_over_verified_outputs(tmp_path):
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, selector, _, ledger, _, branch, step = metered_setup(tmp_path)
    selector.answers = [{"candidates": [candidate()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": step["method_version_id"], "discovery": None}]}]
    session = state["session_id"]
    planner.generate_candidates(session, selector, actor="scientist")
    planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    planner.declare_trial(session, branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(session, "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    numerical = deepcopy(planner.get(session)["results"])
    identity = numerical[0]["artifacts"][0]["artifact_id"]
    artifact = planner.methods.get("artifact", identity)
    ref = {key: artifact[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}
    selector.answers = [preliminary_explanation(ref)]
    response = planner.nominate_explanation(session, selector, actor="scientist")
    updated = planner.get(session)
    assert response["hypotheses"] and updated["preliminary_explanation"]["response"] == response
    assert updated["results"] == numerical
    assert updated["recommendation"]["choice"] is None and updated["approvals"] == {}
    assert updated["selection_history"][-1]["phase"] == "method_preliminary_explanation"
    assert updated["selection_history"][-1]["model_capture_ref"]
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 36
    assert selector.contexts[-1]["visual_content_supplied"] is False


def test_preliminary_explanation_cannot_claim_unbound_artifact_evidence(tmp_path):
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, selector, _, ledger, _, branch, step = metered_setup(tmp_path)
    selector.answers = [{"candidates": [candidate()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": step["method_version_id"], "discovery": None}]}]
    session = state["session_id"]
    planner.generate_candidates(session, selector, actor="scientist")
    planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    planner.declare_trial(session, branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(session, "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    forged = {"artifact_id": "9" * 64, "sha256": "8" * 64, "artifact_type": "json", "size_bytes": 10}
    selector.answers = [preliminary_explanation(forged)]
    with pytest.raises(ValueError, match="evidence|bound|reference"):
        planner.nominate_explanation(session, selector, actor="scientist")
    assert "preliminary_explanation" not in planner.get(session)
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 36


def test_candidate_reassessment_requires_verified_pre_fit_evidence(tmp_path):
    planner, state, selector, _, _, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    with pytest.raises(ValueError, match="evidence|pre-fit|investigation"):
        planner.reassess_candidates(state["session_id"], selector, actor="scientist")
    assert len(selector.contexts) == 1


def neutral_fixture_packet(planner, backend, *, stage="pre_fit_methods"):
    from devbench.analysis.rna_investigation_contract import build_investigation_packet
    from devbench.analysis.rna_investigations import read_investigation_json
    from tests.test_rna_investigation_contract import fixture
    plan = planner.get(next(iter(planner._model_campaigns)))
    _, evidence, _ = fixture(stage)
    campaign = backend.ledger.get_campaign(plan["model_campaign"]["campaign_id"])
    frozen = deepcopy(campaign["identity"]["investigation_policy"])
    policy = {**frozen, "policy_digest": campaign["identity"]["investigation_policy_digest"]}
    audit_ref = frozen["question_record"]["evidence_refs"][0]
    audit = read_investigation_json(planner.methods, audit_ref)
    for category, facts in evidence["facts"].items():
        for fact in facts:
            fact["evidence_refs"] = [audit_ref]
            if category in {"observed", "declared"}:
                fact["source"]["reference"] = audit_ref["artifact_id"] + "#/" + fact["field"]
    evidence.update(diagnostic_refs=[audit_ref], evidence_refs=[audit_ref],
        verified_artifacts=[{"ref": audit_ref, "value": audit}])
    evidence["question_record"] = deepcopy(frozen["question_record"])
    packet = build_investigation_packet(plan, evidence, stage=stage,
        task="independent_method_investigation" if stage == "pre_fit_methods" else "independent_explanation_investigation",
        policy=policy)
    return plan, packet, audit_ref


def retained_bundle(planner, backend, ledger, *, stage="pre_fit_methods", incomplete=False):
    """Build actual fixture artifact/capture chains; never fake native execution."""
    from devbench.analysis.rna_investigation_contract import investigation_context
    from devbench.analysis.rna_investigations import retain_investigation_json, reconcile_investigations
    from tests.test_rna_investigation_contract import result as child_answer
    plan, packet, audit_ref = neutral_fixture_packet(planner, backend, stage=stage)
    packet_ref = retain_investigation_json(planner.methods, packet, actor="offline-fixture-packet")
    child_id = packet["investigation_id"]
    ledger.reserve_investigation_fork("parent-campaign", fork_id="fixture-fork", stage=stage,
        child_ids=[child_id], identity={"packet_ref": packet_ref})
    context = investigation_context(packet)
    bindings = {"format": "devbench.model-call-request/1", "parent_session_id": plan["session_id"],
        "session_id": child_id, "invocation_id": "fixture-child-" + child_id,
        "backend_digest": backend.capability["backend_digest"], "runtime_digest": backend.capability["runtime_digest"],
        "prompt_digest": packet["bindings"]["prompt_digest"], "schema_digest": packet["return_schema_digest"],
        "context_digest": _digest(context)}
    reservation = ledger.reserve_model_call("parent-campaign", "fixture-child", 0, bindings,
        limits={"tokens": 20, "calls": 2, "seconds": 10, "context_bytes": 65536, "memory_mb": 512},
        purpose="rna_investigation", child_id=child_id)
    ledger.activate_model_call(reservation)
    output = planner.methods.root / "offline-child-captures" / child_id
    output.mkdir(mode=0o700, parents=True)
    response = child_answer(packet, transported=False)
    child_selector = CapturedFixtureSelector(RuntimeError("retained child failure") if incomplete else response)
    try:
        child_selector.select(context, output)
    except RuntimeError:
        pass
    capture_ref, receipt = backend.verify_selection_capture(output, bindings=bindings, reservation=reservation)
    ledger.settle_model_call(reservation, capture=capture_ref, verify_capture=backend.verify_capture)
    usage_ref = retain_investigation_json(planner.methods,
        {"format": "devbench.rna-investigation-usage/1", "capture_ref": capture_ref,
         "receipt": backend.verify_capture(capture_ref, reservation)}, actor="offline-fixture-usage",
        artifact_type="rna_investigation_usage", max_bytes=24_000)
    transported = {**response, "capture_ref": capture_ref, "usage_ref": usage_ref}
    result_ref = None if incomplete else retain_investigation_json(planner.methods, transported,
        actor="offline-fixture-result", max_bytes=24_000)
    bundle = reconcile_investigations(plan, [] if incomplete else [{"ref": result_ref, "value": transported}],
        packets=[{"ref": packet_ref, "value": packet, "dispatch_ref": capture_ref,
                  "error": "Retained child failure" if incomplete else None}], verified_evidence=[audit_ref])
    bundle_ref = retain_investigation_json(planner.methods, bundle, actor="offline-fixture-bundle", max_bytes=24_000)
    return plan, bundle_ref, bundle, result_ref


def test_attachment_returns_actual_snapshot_and_only_adds_verified_evidence_links(tmp_path):
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    before, bundle_ref, _, _ = retained_bundle(planner, backend, ledger)
    updated = planner.attach_investigation_evidence(state["session_id"], bundle_ref,
        expected_plan_hash=_digest(before), actor="coordinator")
    assert {key for key in updated if key not in before} == {"investigation_evidence"}
    assert {key: value for key, value in updated.items() if key != "investigation_evidence"} == before
    assert planner.methods.get("method_plan_snapshot", _digest(updated)) == updated
    assert planner.get(state["session_id"]) == updated
    repeated = planner.attach_investigation_evidence(state["session_id"], bundle_ref,
        expected_plan_hash=_digest(before), actor="coordinator")
    assert repeated == updated
    assert len(updated["investigation_evidence"]) == 1


def test_pre_fit_parent_reconciliation_is_registry_blind_and_numerical_trial_runs_once(tmp_path):
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, selector, backend, ledger, _, branch, step = metered_setup(tmp_path, {"candidates": [candidate()]})
    session = state["session_id"]
    planner.generate_candidates(session, selector, actor="scientist")
    before, bundle_ref, _, _ = retained_bundle(planner, backend, ledger)
    planner.attach_investigation_evidence(session, bundle_ref, expected_plan_hash=_digest(before), actor="coordinator")
    with pytest.raises(ValueError, match="Reconcile|pre-fit"):
        planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    selector.answers = [{"candidates": [candidate("Independent alternative"), candidate()]},
        {"bindings": [{"candidate": "Independent alternative", "method_version_id": None, "discovery": None},
                      {"candidate": "Unknown QC", "method_version_id": step["method_version_id"], "discovery": None}]},
        recommendation(None)]
    candidates = planner.reassess_candidates(session, selector, actor="scientist")
    reconciliation = selector.contexts[-1]
    assert candidates[0]["name"] == "Independent alternative"
    assert "registry" not in reconciliation and "availability" not in str(reconciliation)
    assert reconciliation["investigation_evidence"][0]["children"][0]["proposals"]
    assert planner.get(session)["candidate_reassessment"]["previous_scientific_candidates"] == [candidate()]
    planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    class CountedExecutor(FixtureExecutor):
        calls = 0
        def execute(self, *args):
            self.calls += 1
            return super().execute(*args)
    executor = CountedExecutor()
    planner.declare_trial(session, branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(session, "qc-pilot", actor="scientist", executor=executor, allow_data_execution=True)
    result_before = deepcopy(planner.get(session)["results"])
    planner.recommend(session, selector, actor="scientist")
    assert executor.calls == 1 and planner.get(session)["results"] == result_before
    assert planner.get(session)["approvals"] == {} and planner.get(session)["recommendation"]["choice"] is None
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 60
    with pytest.raises(ValueError, match="frozen|reconciliation"):
        planner.reassess_candidates(session, selector, actor="scientist")


@pytest.mark.parametrize("mutation", ["stale_plan", "forged_result", "forged_capture", "changed_source", "changed_input", "schema"])
def test_attachment_rejects_stale_or_forged_evidence_before_parent_mutation(tmp_path, mutation):
    from devbench.analysis.rna_investigations import retain_investigation_json, read_investigation_json
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    before, bundle_ref, bundle, result_ref = retained_bundle(planner, backend, ledger)
    expected = _digest(before)
    if mutation == "stale_plan":
        expected = "f" * 64
    elif mutation == "changed_source":
        backend.capability["source_digest"] = "a" * 64
    elif mutation == "changed_input":
        identity = before["context"]["dataset_summary"]["artifact_ids"][0]
        path = planner.methods.artifact_path(identity)
        path.chmod(0o600)
        path.write_text("changed input bytes")
    elif mutation == "schema":
        packet = read_investigation_json(planner.methods, bundle["children"][0]["packet_ref"])
        packet["return_schema_digest"] = "f" * 64
        altered = retain_investigation_json(planner.methods, packet, actor="fixture-forgery")
        bundle["packet_refs"] = [altered]
        bundle["children"][0]["packet_ref"] = altered
        bundle_ref = retain_investigation_json(planner.methods, bundle, actor="fixture-forgery")
    else:
        result = read_investigation_json(planner.methods, result_ref)
        if mutation == "forged_result":
            result["proposals"]["tradeoffs"] = ["Caller claims a winner without actual capture"]
        else:
            result["capture_ref"]["sha256"] = "f" * 64
        altered = retain_investigation_json(planner.methods, result, actor="fixture-forgery")
        bundle["result_refs"] = [altered]
        bundle["children"][0]["result_ref"] = altered
        bundle_ref = retain_investigation_json(planner.methods, bundle, actor="fixture-forgery")
    with pytest.raises((ValueError, RuntimeError), match="hash|stale|binding|differ|changed|schema|manifest"):
        planner.attach_investigation_evidence(state["session_id"], bundle_ref,
            expected_plan_hash=expected, actor="child-claims-authority")
    assert planner.get(state["session_id"]) == before


def test_missing_child_stays_visible_in_parent_projection_without_success_or_approval(tmp_path):
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    before, bundle_ref, _, _ = retained_bundle(planner, backend, ledger, incomplete=True)
    planner.attach_investigation_evidence(state["session_id"], bundle_ref,
        expected_plan_hash=_digest(before), actor="coordinator")
    selector.answers = [{"candidates": []}]
    planner.reassess_candidates(state["session_id"], selector, actor="scientist")
    evidence = selector.contexts[-1]["investigation_evidence"][0]
    assert evidence["children"][0]["status"] == "incomplete"
    assert evidence["children"][0]["result_ref"] is None and evidence["uncertainty"]
    assert planner.get(state["session_id"])["scientific_candidates"] == []
    assert planner.get(state["session_id"])["approvals"] == {}


def test_parent_timeout_is_clamped_to_shared_campaign_deadline_before_dispatch(tmp_path, monkeypatch):
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": []})
    monkeypatch.setattr(ledger, "model_call_remaining", lambda reservation: 3)
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert selector.forwarded_limits["timeout_seconds"] <= 3


def test_post_fit_evidence_reaches_synthesis_without_refit_or_family_changes(tmp_path):
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, selector, backend, ledger, _, branch, step = metered_setup(tmp_path)
    selector.answers = [{"candidates": [candidate()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": step["method_version_id"], "discovery": None}]}]
    session = state["session_id"]
    planner.generate_candidates(session, selector, actor="scientist")
    planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    class CountedExecutor(FixtureExecutor):
        calls = 0
        def execute(self, *args):
            self.calls += 1
            return super().execute(*args)
    executor = CountedExecutor()
    planner.declare_trial(session, branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(session, "qc-pilot", actor="scientist", executor=executor, allow_data_execution=True)
    numerical = deepcopy(planner.get(session)["results"])
    ref = {key: value for key, value in planner._trial_input_reference(numerical[0]["artifacts"][0]["artifact_id"]).items() if key != "format"}
    selector.answers = [preliminary_explanation(ref), recommendation(None)]
    planner.nominate_explanation(session, selector, actor="scientist")
    before, bundle_ref, _, _ = retained_bundle(planner, backend, ledger, stage="post_fit_explanation")
    planner.attach_investigation_evidence(session, bundle_ref, expected_plan_hash=_digest(before), actor="coordinator")
    planner.recommend(session, selector, actor="scientist")
    projection = selector.contexts[-1]["investigation_evidence"]
    assert projection[0]["stage"] == "post_fit_explanation"
    assert projection[0]["children"][0]["proposals"]["explanations"]
    assert len(canonical_json(projection)) <= 24_000
    after = planner.get(session)
    assert executor.calls == 1 and after["results"] == numerical and after["experiments"] == before["experiments"]
    assert after["approvals"] == {} and after["recommendation"]["choice"] is None
    changed_family = trial_spec(step)
    changed_family["id"] = "postfit-added-family"
    changed_family["parameters"]["Unknown QC"]["genes"] = ["new_gene"]
    with pytest.raises(ValueError, match="frozen|criteria|results"):
        planner.declare_trial(session, branch["branch_id"], changed_family, actor="child-proposes-genes")
    with pytest.raises(ValueError, match="frozen|trial|pre-fit"):
        planner.reassess_candidates(session, selector, actor="child-proposes-contrasts")
    assert executor.calls == 1


def test_conflicting_duplicate_bundle_cannot_overwrite_first_evidence(tmp_path):
    from devbench.analysis.rna_investigations import retain_investigation_json
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    before, bundle_ref, bundle, _ = retained_bundle(planner, backend, ledger)
    attached = planner.attach_investigation_evidence(state["session_id"], bundle_ref,
        expected_plan_hash=_digest(before), actor="coordinator")
    bundle["uncertainty"].append("Conflicting altered bundle")
    conflict = retain_investigation_json(planner.methods, bundle, actor="fixture-conflict")
    with pytest.raises(ValueError, match="Conflicting"):
        planner.attach_investigation_evidence(state["session_id"], conflict,
            expected_plan_hash=_digest(attached), actor="new-label")
    assert planner.get(state["session_id"]) == attached


@pytest.mark.parametrize("phase", ["generation", "recommendation"])
def test_cancellation_during_parent_call_retains_charge_but_publishes_no_decision(tmp_path, monkeypatch, phase):
    from devbench.analysis.workflow_operations import OperationBlocked
    planner, state, selector, _, ledger, _, _, step = metered_setup(tmp_path)
    session = state["session_id"]
    if phase == "recommendation":
        selector.answers = [{"candidates": [candidate()]}, {"bindings": [{"candidate": "Unknown QC",
            "method_version_id": step["method_version_id"], "discovery": None}]}]
        planner.generate_candidates(session, selector, actor="scientist")
        planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    before = planner.get(session)
    selector.answers = [recommendation(None) if phase == "recommendation" else {"candidates": [candidate()]}]
    original = selector.select
    def cancel_after_capture(context, output, **kwargs):
        response = original(context, output, **kwargs)
        ledger.cancel_campaign("parent-campaign")
        return response
    monkeypatch.setattr(selector, "select", cancel_after_capture)
    with pytest.raises(OperationBlocked, match="cancelled"):
        (planner.recommend if phase == "recommendation" else planner.generate_candidates)(session, selector, actor="scientist")
    after = planner.get(session)
    assert after["scientific_candidates"] == before["scientific_candidates"]
    assert after["recommendation"] == before["recommendation"] and after["approvals"] == before["approvals"]
    history = after["selection_history"][-1]
    assert history["status"] == "failed"
    assert history["model_capture_ref"] and history["model_usage_ref"] and history["response"]
    campaign = ledger.get_campaign("parent-campaign")
    assert campaign["status"] == "cancelled"
    assert campaign["verified_spent_tokens"] == (36 if phase == "recommendation" else 12)


@pytest.mark.parametrize("state_change", ["deadline", "unresolved", "violation"])
def test_settled_parent_action_is_blocked_by_current_campaign_state(tmp_path, monkeypatch, state_change):
    from devbench.analysis.workflow_operations import OperationBlocked
    import time
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    original_select, original_campaign = selector.select, ledger.get_campaign
    def invalidate_after_capture(context, output, **kwargs):
        response = original_select(context, output, **kwargs)
        def current_campaign(identity):
            campaign = original_campaign(identity)
            if state_change == "deadline":
                campaign["deadline_epoch"] = time.time() - 1
            elif state_change == "unresolved":
                campaign["unresolved_count"] = 1
            else:
                campaign["qualification"] = "violated"
            return campaign
        monkeypatch.setattr(ledger, "get_campaign", current_campaign)
        return response
    monkeypatch.setattr(selector, "select", invalidate_after_capture)
    with pytest.raises(OperationBlocked, match="deadline|unresolved|violation"):
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    after = planner.get(state["session_id"])
    assert after["scientific_candidates"] is None and after["selection_history"][-1]["model_usage_ref"]
    assert original_campaign("parent-campaign")["verified_spent_tokens"] == 12


def test_parent_request_purpose_is_coordinator_owned_even_when_backend_returns_an_override(tmp_path):
    planner, state, selector, backend, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": []})
    original_binding = backend.parent_selection_binding
    def forged_purpose(*args, **kwargs):
        return {**original_binding(*args, **kwargs), "purpose": "rna_investigation"}
    backend.parent_selection_binding = forged_purpose
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    history = planner.get(state["session_id"])["selection_history"][-1]
    request = ledger.get_model_reservation(history["model_reservation_ref"])["request"]
    assert request["purpose"] == "method_scientific_planning"


def test_verified_overrun_retains_actual_usage_artifact_and_no_scientific_choice(tmp_path, monkeypatch):
    from devbench.analysis.workflow_operations import OperationBlocked
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    original = selector.select
    def overrun_after_capture(context, output, **kwargs):
        response = original(context, output, **kwargs)
        (output / "usage.json").write_bytes(canonical_json({"input_tokens": 400, "output_tokens": 0,
            "cached_input_tokens": 0, "reasoning_tokens": 0}))
        return response
    monkeypatch.setattr(selector, "select", overrun_after_capture)
    with pytest.raises(OperationBlocked, match="violation|exceeded"):
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    after = planner.get(state["session_id"])
    assert after["scientific_candidates"] is None
    assert after["selection_history"][-1]["model_usage_ref"]
    assert after["selection_history"][-1]["model_usage"]["normalized_total_tokens"] == 400
    campaign = ledger.get_campaign("parent-campaign")
    assert campaign["verified_spent_tokens"] == 400 and campaign["qualification"] == "violated"


def prepare_postfit_nomination(tmp_path):
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, selector, backend, ledger, _, branch, step = metered_setup(tmp_path)
    selector.answers = [{"candidates": [candidate()]}, {"bindings": [{"candidate": "Unknown QC",
        "method_version_id": step["method_version_id"], "discovery": None}]}]
    session = state["session_id"]
    planner.generate_candidates(session, selector, actor="scientist")
    planner.resolve_capabilities(session, selector, [step["method_version_id"]], actor="scientist")
    planner.declare_trial(session, branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(session, "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    current, packet, ref = neutral_fixture_packet(planner, backend, stage="post_fit_explanation")
    return planner, current, selector, backend, ledger, packet, ref


def test_preliminary_explanation_receives_only_a_verified_bounded_postfit_view(tmp_path):
    planner, current, selector, _, ledger, packet, ref = prepare_postfit_nomination(tmp_path)
    selector.answers = [preliminary_explanation(ref)]
    original_results = deepcopy(current["results"])
    planner.nominate_explanation(current["session_id"], selector, actor="scientist", verified_evidence=packet)
    context = selector.contexts[-1]
    assert context["verified_output_view"] == packet and context["visual_content_supplied"] is False
    updated = planner.get(current["session_id"])
    assert updated["results"] == original_results
    assert updated["preliminary_explanation"]["verified_output_packet_sha256"] == _digest(packet)
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 36


@pytest.mark.parametrize("mutation", ["source_pointer", "plan_hash", "runtime", "source", "policy", "family"])
def test_unverified_postfit_view_is_rejected_before_a_parent_model_call(tmp_path, mutation):
    from devbench.analysis.rna_investigations import retain_investigation_json
    planner, current, selector, _, ledger, packet, ref = prepare_postfit_nomination(tmp_path)
    if mutation == "source_pointer":
        packet["facts"]["observed"][0]["value"] = "not the actual source value"
    elif mutation == "plan_hash":
        packet["bindings"]["plan_hash"] = "f" * 64
    elif mutation == "runtime":
        packet["bindings"]["runtime_digest"] = "f" * 64
    elif mutation == "source":
        packet["bindings"]["source_digest"] = "f" * 64
    elif mutation == "policy":
        packet["bindings"]["policy_digest"] = "f" * 64
    else:
        output = current["results"][0]["artifacts"][0]["artifact_id"]
        whole = {key: value for key, value in planner._trial_input_reference(output).items() if key != "format"}
        stats = {"coverage": "whole_family", "selection": {"rule": "all", "origin": "coordinator", "requirement_name": None},
            "whole_family_refs": [whole], "tested_hypotheses": 1, "represented_hypotheses": 1,
            "failed_estimates": 0, "missingness": {"unrepresented_hypotheses": 0, "failed_estimates_retained": True},
            "rows": [{"hypothesis_id": "fabricated_gene", "estimate_status": "estimated", "p_value": 1e-40,
                "q_value": 1e-40, "effect": 99}]}
        compact = retain_investigation_json(planner.methods, {"gene_statistics": stats}, actor="fixture-forgery")
        packet["facts"]["observed"].append({"field": "gene_statistics", "value": stats,
            "source": {"kind": "coordinator_artifact", "reference": compact["artifact_id"] + "#/gene_statistics"},
            "evidence_refs": [compact], "limitation": "Software attack fixture"})
        packet["evidence_refs"].extend([whole, compact])
    selector.answers = [preliminary_explanation(ref)]
    calls_before = len(selector.contexts)
    with pytest.raises(ValueError, match="source|fact|stale|binding|runtime|policy|family|projection|pointer|different"):
        planner.nominate_explanation(current["session_id"], selector, actor="scientist", verified_evidence=packet)
    assert len(selector.contexts) == calls_before
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 24
    assert "preliminary_explanation" not in planner.get(current["session_id"])


@pytest.mark.parametrize("phase", ["generation", "recommendation"])
@pytest.mark.parametrize("window", ["validation", "selection_returned"])
def test_cancellation_after_response_check_blocks_final_scientific_commit(tmp_path, monkeypatch, phase, window):
    from devbench.analysis import method_planner as module
    from devbench.analysis.workflow_operations import OperationBlocked
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": []})
    session = state["session_id"]
    if phase == "recommendation":
        planner.generate_candidates(session, selector, actor="scientist")
        response = recommendation(None)
        response["rationale"] = "This cancelled rationale must never be accepted"
        selector.answers = [response]
    before = planner.get(session)
    if window == "validation":
        symbol = "validate_scientific_candidates" if phase == "generation" else "validate_recommendation"
        original_validate = getattr(module, symbol)
        def cancel_after_validation(value):
            checked = original_validate(value)
            ledger.cancel_campaign("parent-campaign")
            return checked
        monkeypatch.setattr(module, symbol, cancel_after_validation)
    else:
        original_save = planner._save
        def cancel_during_returned_save(original, updated, *, actor, action):
            if action == "planner_selection_returned":
                ledger.cancel_campaign("parent-campaign")
            return original_save(original, updated, actor=actor, action=action)
        monkeypatch.setattr(planner, "_save", cancel_during_returned_save)
    with pytest.raises(OperationBlocked, match="cancel"):
        (planner.generate_candidates if phase == "generation" else planner.recommend)(session, selector, actor="scientist")
    after = planner.get(session)
    assert after["scientific_candidates"] == before["scientific_candidates"]
    assert after["recommendation"] == before["recommendation"] and after["approvals"] == before["approvals"]
    history = after["selection_history"][-1]
    assert history["status"] != "accepted" and history["response"]
    assert history["model_capture_ref"] and history["model_usage_ref"]
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == (24 if phase == "recommendation" else 12)


@pytest.mark.parametrize("purpose", ["method_scientific_planning", "method_candidates_reassessment",
    "method_capability_resolution", "method_trial_design", "method_implementation_draft",
    "method_preliminary_explanation", "method_recommendation"])
def test_every_model_phase_acceptance_requires_current_publication_authority(tmp_path, purpose):
    from devbench.analysis.workflow_operations import OperationBlocked
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": []})
    _, returned = planner._select(state, selector, {"decision_kind": purpose}, actor="scientist", allow_model_execution=False)
    ledger.cancel_campaign("parent-campaign")
    claimed = deepcopy(returned)
    claimed["selection_history"][-1]["status"] = "accepted"
    with pytest.raises(OperationBlocked, match="cancel"):
        planner._save(returned, claimed, actor="scientist", action="model_phase_accepted")
    assert planner.get(state["session_id"])["selection_history"][-1]["status"] == "returned_unvalidated"
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 12


def test_protected_selector_receives_the_frozen_full_context_bound(tmp_path):
    from devbench.analysis.episode_codex import CodexActionSelector
    class ProtectedFixtureSelector(CapturedFixtureSelector, CodexActionSelector):
        """Offline selector with the protected transport signature, no CLI use."""
    planner, state, _, backend, ledger, _, _, _ = metered_setup(tmp_path)
    selector = ProtectedFixtureSelector({"candidates": []})
    backend.selector = selector
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert selector.forwarded_limits["max_context_bytes"] == 65536
    gate = selector.forwarded_limits["protected_model_gate"]
    history = planner.get(state["session_id"])["selection_history"][-1]
    reserved = ledger.get_model_reservation(history["model_reservation_ref"])
    assert gate["format"] == "devbench.model-dispatch-gate/1" and gate["ledger_path"] == str(ledger.path)
    assert gate["reservation_ref"] == history["model_reservation_ref"]
    assert gate["request_digest"] == reserved["request_digest"]
    assert gate["context_digest"] == history["context_sha256"]
    assert "ledger_path" not in str(selector.contexts[-1])
    assert ledger.get_campaign("parent-campaign")["verified_spent_tokens"] == 12


def test_scientific_cas_serializes_with_cancellation_from_another_ledger_instance(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from devbench.analysis.workflow_operations import WorkflowOperationLedger, OperationBlocked
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    cancelling = WorkflowOperationLedger(ledger.path)
    publication_entered, cancel_attempted, cancelled, busy = Event(), Event(), Event(), Event()
    original_put = planner.methods._put
    def observe_scientific_commit(db, kind, identity, value, *, update=False):
        if (kind == "method_plan" and update and value["selection_history"]
                and value["selection_history"][-1]["status"] == "accepted"):
            publication_entered.set()
            assert cancel_attempted.wait(2), "Independent cancellation did not attempt the publication window"
            assert not cancelled.wait(.1), "Cancellation committed between the science gate and planner CAS"
        return original_put(db, kind, identity, value, update=update)
    monkeypatch.setattr(planner.methods, "_put", observe_scientific_commit)
    def concurrent_cancel():
        assert publication_entered.wait(3), "Scientific publication did not reach its guarded CAS"
        cancel_attempted.set()
        try:
            cancelling.cancel_campaign("parent-campaign")
            cancelled.set()
        except OperationBlocked as error:
            assert error.state == "ledger_busy"
            busy.set()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(concurrent_cancel)
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
        future.result(timeout=3)
    # Cross-instance contention is a visible refusal, never a claimed cancel.
    # Retry only after the scientific commit has released its short guard.
    if busy.is_set():
        cancelling.cancel_campaign("parent-campaign")
        cancelled.set()
    assert cancelled.is_set() and ledger.get_campaign("parent-campaign")["status"] == "cancelled"
    assert planner.get(state["session_id"])["selection_history"][-1]["status"] == "accepted"
    assert planner.get(state["session_id"])["scientific_candidates"] == [candidate()]


def test_repeated_accepted_status_cannot_bypass_a_scientific_publication_gate(tmp_path):
    from devbench.analysis.workflow_operations import OperationBlocked
    planner, state, selector, _, ledger, _, _, _ = metered_setup(tmp_path, {"candidates": [candidate()]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    current = planner.get(state["session_id"])
    ledger.cancel_campaign("parent-campaign")
    changed = deepcopy(current)
    changed["scientific_candidates"] = []
    with pytest.raises(OperationBlocked, match="cancel"):
        planner._save(current, changed, actor="new-label", action="scientific_candidates_frozen")
    assert planner.get(state["session_id"])["scientific_candidates"] == [candidate()]


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Explicit local FakeCLI capture opt-in")
@pytest.mark.parametrize("transition", ["uncertain", "cancelled"])
def test_parent_fake_cli_shared_revocation_after_preflight_stops_action_and_retains_usage(workspace, monkeypatch, transition):
    """Actual offline processes and capture; never a paid/native qualification."""
    from pathlib import Path
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_operations import model_reservation_ref
    from tests.test_rna_investigation_backend import protected_fixture
    backend, _, _, _ = protected_fixture(workspace, monkeypatch)
    backend.ledger.cancel_campaign("offline-campaign")
    # The reused fixture's binary must produce the parent schema before the
    # new parent campaign freezes its exact executable/runtime capability.
    executable = Path(backend.selector.executable)
    script = executable.read_text()
    start = script.index("else:\n body=prompt.split")
    end = script.index("\nPath(args[args.index('--output-last-message')", start)
    script = script[:start] + "else:\n answer=json.dumps({'candidates':[]})" + script[end:]
    executable.write_text(script)
    planner = ScientificMethodPlanner(backend.methods)
    state = planner.create({"scientific_objective": "Verify protected planning transport",
        "computational_task": "Offline software capture qualification"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": []},
        actor="offline-fixture", data_classification="synthetic", evaluation_purpose="execution_verification")
    session = state["session_id"]
    capability = backend.capabilities()
    allocation = {"tokens": 300, "calls": 2, "seconds": 15, "context_bytes": 128000, "memory_mb": 256}
    backend.ledger.open_campaign(session, session_id=session,
        policy=_policy("bounded_observed_tokens", soft_token_target=1000, max_context_bytes=128000, memory_mb=256),
        capability=capability, identity={"expected_plan_hash": _digest(state),
            "source_digest": capability["source_digest"], "runtime_digest": capability["runtime_digest"],
            "parent_call_limits": {"default": allocation}})
    planner.attach_model_campaign(session, backend.ledger, session, backend,
        expected_plan_hash=_digest(state), actor="offline-fixture")
    sibling_request = {"format": "devbench.model-call-request/1", "parent_session_id": session,
        "session_id": "offline-unresolved-sibling", "invocation_id": "offline-sibling-invocation",
        "backend_digest": capability["backend_digest"], "runtime_digest": capability["runtime_digest"],
        "prompt_digest": "a" * 64, "schema_digest": "b" * 64, "context_digest": "c" * 64}
    sibling = backend.ledger.reserve_model_call(session, "offline-sibling", 0, sibling_request,
        limits={**allocation, "tokens": 100}, purpose="fixture_other_attempt")
    backend.ledger.activate_model_call(sibling)
    observed = {}
    output_root = planner.methods.root / "planner-selections" / session
    def revoke():
        deadline = time.monotonic() + 12
        checkpoints = []
        while time.monotonic() < deadline:
            checkpoints = list(output_root.glob("*/exec-preflight/dispatch_checkpoint.json"))
            if checkpoints:
                break
            time.sleep(.001)
        observed["preflight_seen"] = bool(checkpoints)
        if not checkpoints:
            return
        output = checkpoints[0].parent.parent
        observed["output"] = output
        observed["action_before_revocation"] = (output / "selection-dispatch.json").exists()
        if transition == "uncertain":
            backend.ledger.mark_model_call_uncertain(sibling, error_type="OfflineFixtureUnknownSiblingSpend")
        else:
            backend.ledger.cancel_campaign(session)
    worker = threading.Thread(target=revoke)
    worker.start()
    try:
        with pytest.raises((ValueError, RuntimeError, PermissionError)) as failure:
            planner.generate_candidates(session, backend.selector, actor="offline-fixture", allow_model_execution=True)
    finally:
        worker.join(timeout=13)
    preflight_errors = {}
    for path in output_root.glob("*/exec-preflight/evidence.json"):
        summary = json.loads(path.read_text())
        preflight_errors = {key: summary.get(key) for key in ("verified", "checks", "validation_errors", "dispatch_stage")}
    assert not worker.is_alive() and observed["preflight_seen"], str({"failure": str(failure.value)[:400], "preflight": preflight_errors})
    assert not observed["action_before_revocation"] and not (observed["output"] / "selection-dispatch.json").exists()
    after = planner.get(session)
    assert after["scientific_candidates"] is None and after["selection_history"][-1]["status"] != "accepted"
    selection = after["selection_history"][-1]
    record = backend.ledger.get_model_reservation(selection["model_reservation_ref"])
    receipt = backend.verify_capture(selection["model_capture_ref"])
    assert receipt["actual_calls"] == 1 and len(receipt["native_session_ids"]) == 1
    assert receipt["usage"]["normalized_total_tokens"] == 130
    assert backend.ledger.get_campaign(session)["verified_spent_tokens"] == 130
    assert selection["model_usage_ref"] and record["capture"] == receipt
    manifest = backend._document(selection["model_capture_ref"])
    if "actor/context.json" in manifest["files"]:
        context = json.loads(backend._artifact(manifest["files"]["actor/context.json"], max_bytes=128000))
        assert "ledger_path" not in json.dumps(context) and "protected_model_gate" not in context
