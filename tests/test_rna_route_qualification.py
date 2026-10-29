"""Synthetic engineering tests of an injected trust seam, never real acceptance.

The fixture's acceptance store and executor inspector stand in for separately
trusted deployment dependencies. They establish no issuer trust, numerical
method qualification, provider execution, or scientific validity.
"""
from copy import deepcopy
from datetime import timedelta

import pytest

from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis import rna_trial_authority as authority_api
from devbench.analysis.checkpoints import digest
from tests.test_rna_execution_route_qualification import _case
from tests.test_rna_publication_profiles import source
from tests.test_rna_scientific_trial import NOW


class FixtureExecutor:
    def execute(self, *args):
        raise AssertionError("Qualification inspection must never dispatch")


class FixtureBackend:
    def __init__(self, methods, identity):
        self.methods, self.identity = methods, identity

    def runtime_identity(self):
        return deepcopy(self.identity)


def qualification_case(tmp_path, monkeypatch, *, intent_change=None, temporal=False, output_contract=None):
    from devbench.analysis import rna_route_qualification as api
    monkeypatch.setattr(authority_api, "trial_enforcement_source_digest", lambda: "a" * 64)
    monkeypatch.setattr(authority_api, "trial_enforcement_runtime_digest", lambda: "b" * 64)
    from devbench.analysis import rna_scientific_trial as trial_api
    monkeypatch.setattr(trial_api, "trial_enforcement_source_digest", lambda: "a" * 64)
    monkeypatch.setattr(trial_api, "trial_enforcement_runtime_digest", lambda: "b" * 64)
    if output_contract is not None:
        from tests import test_rna_scientific_trial as trial_fixtures
        original_custom = trial_fixtures.custom

        def custom_outputs(*args, **kwargs):
            contract, code, lock = original_custom(*args, **kwargs)
            contract["outputs"] = deepcopy(output_contract)
            return contract, code, lock

        monkeypatch.setattr(trial_fixtures, "custom", custom_outputs)
    case = _case(tmp_path, governed_bridge=temporal)
    request, planner = case["request"], case["planner"]
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
    from tests.test_rna_execution_inputs import ports
    bridge = case["bridge"]
    if not temporal:
        selected_ports = ports(infer=True)
        selected_ports["ports"]["matrix"] = selected_ports["ports"].pop("counts")
        for port in selected_ports["ports"].values():
            port["parameter"] = None
        selected_ports["string_columns"] = ["sample_id", "group", "biological_unit_id", "batch"]
        bridge = prepare_execution_inputs(request["dataset"]["binding"], selected_ports,
            artifact_service=planner.methods, output=tmp_path / "qualified-inferential-bridge")
        request["design"] = deepcopy(selected_ports["design"])
    old = profiles.load_assessment_plan(request["assessment_plan"], workspace=tmp_path, service=planner.methods)
    intent = profiles.load_assessment_intent(old["intent_ref"], workspace=tmp_path, service=planner.methods)
    intent["profile_specs"][0]["required_plot_kinds"] = []
    metric = intent["profile_specs"][0]["metric_specs"][0]
    metric["parameter_record_ref"] = source(tmp_path / "canonical-parameters.json", {
        "format": "devbench.rna-gene-comparison-parameters/2", "actual_table_id": "synthetic-table",
        "reference_claim_ids": intent["required_claim_ids"], "selection_rule": None})
    metric["universe_rule_id"] = "complete_eligible_gene_family"
    universe = {"format": "devbench.rna-gene-universe-policy/2", "rule_id": "complete_eligible_gene_family",
        "eligible_ids": ["g1", "g2"], "excluded_ids": [], "mapping_namespace": "fixture-ids", "annotation_ref": None,
        "mapping_records": [{"source_id": key, "target_id": key, "status": "mapped"} for key in ("g1", "g2")]}
    review = {**deepcopy(universe), "format": "devbench.rna-gene-universe-review/2", "reviewed_by": "reviewer",
        "conclusion": "supported", "limitations": "Synthetic engineering fixture; no scientific approval"}
    universe["review_record_ref"] = source(tmp_path / "canonical-universe-review.json", review)
    intent["universe_policy_ref"] = source(tmp_path / "canonical-universe.json", universe)
    if intent_change:
        intent_change(intent)
    analyst = profiles.read_reference_json(intent["analyst_projection_ref"], workspace=tmp_path)
    analyst["required_output_schemas"] = list(dict.fromkeys(schema for item in intent["profile_specs"]
        for schema in item["required_output_schemas"]))
    intent["analyst_projection_ref"] = source(tmp_path / "qualified-analyst.json", analyst)
    frozen = profiles.prepare_assessment_intent(tmp_path / "qualification-intent", intent, workspace=tmp_path, service=planner.methods)
    wrapper = profiles.read_reference_json(old["workflow_ref"], workspace=tmp_path, service=planner.methods)
    wrapper.update(assessment_intent_ref=frozen, assessment_rules_sha256=profiles.assessment_rules_digest(intent))
    workflow = source(tmp_path / "qualification-workflow.json", wrapper)
    request["assessment_intent"] = frozen
    request["workflow"]["workflow_ref"] = workflow
    plan = profiles.finalize_assessment_plan(frozen, workflow, bridge["record_ref"],
        output=tmp_path / "qualification-finalization", workspace=tmp_path, service=planner.methods)
    request["assessment_plan"] = profiles.prepare_assessment_plan(tmp_path / "qualification-plan", plan,
        workspace=tmp_path, service=planner.methods)
    request["engineering"]["route"] = "mediated"
    request["engineering"]["qualification_refs"] = [source(tmp_path / "independent-evidence-locator.json", {
        "origin": "synthetic_engineering_fixture", "scope": "test dependency only; not real acceptance"})]
    identity = {key: request["engineering"][key] for key in
        ("route", "source_digest", "runtime_digest", "backend_digest", "model", "platform")}
    executor = FixtureExecutor()
    backend = FixtureBackend(planner.methods, identity)
    context = {"planner": planner, "executor": executor, "actor": "participant", "backend": backend,
        "scientific_context": request["scientific_context"]}
    accepted = []
    calls = []

    def inspect_runtime(actual):
        assert actual["planner"] is planner and actual["executor"] is executor and actual["backend"] is backend
        return {"identity": backend.runtime_identity(), "executor_sha256": "e" * 64}

    def accepted_proof(reference, *, workspace, now):
        calls.append(deepcopy(reference))
        assert reference == request["engineering"]["qualification_refs"][0] and workspace == tmp_path and now == NOW
        if not accepted:
            raise PermissionError("No independently accepted fixture root")
        return deepcopy(accepted[0])

    resolver = api.TrustedExecutionQualification(workspace=tmp_path, accepted_proof_verifier=accepted_proof,
        runtime_verifier=inspect_runtime, clock=lambda: NOW)
    kwargs = {"methods": planner.methods, "planner": planner, "purpose": "trial_execution", "runtime_context": context}
    return request, resolver, kwargs, accepted, calls


def accept_fixture(request, resolver, kwargs, accepted):
    """Detached test registry entry, outside the request-hashed locator bytes."""
    binding = resolver.observe_binding(request, **kwargs)
    accepted.append({"format": "devbench.rna-execution-qualification-acceptance/1",
        "issuer_id": "test-only-independent-issuer", "reviewer_id": request["roles"]["reviewer"],
        "accepted_root_sha256": "f" * 64, "origin": "synthetic_engineering_fixture",
        "scope": "synthetic_engineering_execution_only", "purpose": "trial_execution",
        "expires_at": (NOW + timedelta(hours=1)).isoformat(), "binding": binding,
        "rendering": {"required": False, "qualified": False}})
    return binding


def test_detached_exact_acceptance_returns_narrow_stable_overlay_without_mutation(tmp_path, monkeypatch):
    request, resolver, kwargs, accepted, calls = qualification_case(tmp_path, monkeypatch)
    binding = accept_fixture(request, resolver, kwargs, accepted)
    before = deepcopy(request)
    result = resolver.verify(request, **kwargs)
    assert set(result) == {"format", "binding_sha256", "satisfied_blockers", "qualification_sha256", "rendering"}
    assert result == {"format": "devbench.rna-execution-qualification/1", "binding_sha256": digest(binding),
        "satisfied_blockers": ["mean_expression:unsupported_profile", "unqualified_adapters_metrics_renderers_execution_route"],
        "qualification_sha256": digest(accepted[0]), "rendering": {"required": False, "qualified": False}}
    assert resolver.verify(request, **kwargs) == result
    assert request == before and len(calls) == 2
    assert kwargs["methods"].list_records("run") == []
    plan = profiles.load_assessment_plan(request["assessment_plan"], workspace=tmp_path, service=kwargs["methods"])
    prerequisites = profiles.read_reference_json(plan["execution_prerequisites_ref"], workspace=tmp_path)
    assert prerequisites["computational_admission"] == "blocked"
    assert profiles.select_applicable_profiles(accepted[0]["binding"]["estimand"],
        accepted[0]["binding"]["capabilities"])[0]["computationally_supported"] is False


def test_locator_bytes_and_caller_json_do_not_construct_trust(tmp_path, monkeypatch):
    request, resolver, kwargs, accepted, _ = qualification_case(tmp_path, monkeypatch)
    with pytest.raises(PermissionError, match="independently accepted"):
        resolver.verify(request, **kwargs)
    assert accepted == []
    request["qualified"] = True
    with pytest.raises(ValueError, match="closed"):
        resolver.verify(request, **kwargs)


@pytest.mark.parametrize("attack", ["expiry", "scope", "purpose", "origin", "root", "extra", "reviewer", "rendering",
    "trial", "plan", "executor", "adapter", "metric", "universe"])
def test_incomplete_expired_or_drifted_detached_acceptance_fails_closed(tmp_path, monkeypatch, attack):
    request, resolver, kwargs, accepted, _ = qualification_case(tmp_path, monkeypatch)
    accept_fixture(request, resolver, kwargs, accepted)
    proof = accepted[0]
    if attack == "expiry":
        proof["expires_at"] = NOW.isoformat()
    elif attack in {"scope", "purpose", "origin", "reviewer"}:
        proof[{"reviewer": "reviewer_id"}.get(attack, attack)] = "unsupported"
    elif attack == "root":
        proof["accepted_root_sha256"] = "not-a-root"
    elif attack == "extra":
        proof["qualified"] = True
    elif attack == "rendering":
        proof["rendering"]["qualified"] = True
    elif attack == "trial":
        request["trial_id"] = "different-current-subject"
    elif attack == "plan":
        proof["binding"]["assessment_plan_sha256"] = "c" * 64
    elif attack == "executor":
        proof["binding"]["executor_sha256"] = "c" * 64
    elif attack == "adapter":
        proof["binding"]["adapter_sha256"] = "c" * 64
    elif attack == "metric":
        proof["binding"]["profile_sha256"] = "c" * 64
    else:
        proof["binding"]["universe_sha256"] = "c" * 64
    with pytest.raises(PermissionError):
        resolver.verify(request, **kwargs)


@pytest.mark.parametrize("change", [lambda x: x["profile_specs"][0]["required_plot_kinds"].append("effect_scatter"),
    lambda x: x["profile_specs"][0]["required_output_schemas"].append("devbench.rna-selected-set/2"),
    lambda x: x["profile_specs"][0]["metric_specs"][0].update(metric_id="gene_effect_rmse"),
    lambda x: x["profile_specs"][0]["metric_specs"][0]["uncertainty_policy"].update(kind="require_interval"),
    lambda x: x["profile_specs"][0]["metric_specs"].append({**deepcopy(x["profile_specs"][0]["metric_specs"][0]), "metric_id": "gene_effect_rmse"})])
def test_requested_scope_cannot_exceed_singleton_supported_subset(tmp_path, monkeypatch, change):
    request, resolver, kwargs, _, calls = qualification_case(tmp_path, monkeypatch, intent_change=change)
    with pytest.raises((PermissionError, ValueError)):
        resolver.verify(request, **kwargs)
    assert calls == []


@pytest.mark.parametrize("attack", ["missing_executor", "copied_context", "foreign_backend", "source_drift", "duplicate_refs", "generation", "public"])
def test_current_runtime_purpose_scope_and_single_proof_required(tmp_path, monkeypatch, attack):
    request, resolver, kwargs, accepted, calls = qualification_case(tmp_path, monkeypatch)
    accept_fixture(request, resolver, kwargs, accepted)
    if attack == "missing_executor":
        kwargs["runtime_context"].pop("executor")
    elif attack == "copied_context":
        kwargs["runtime_context"]["planner"] = {"methods": str(kwargs["methods"].root)}
    elif attack == "foreign_backend":
        kwargs["runtime_context"]["backend"].methods = object()
    elif attack == "source_drift":
        monkeypatch.setattr(authority_api, "trial_enforcement_source_digest", lambda: "d" * 64)
    elif attack == "duplicate_refs":
        request["engineering"]["qualification_refs"].append(source(tmp_path / "partial-second-proof.json", {"scope": "partial"}))
    elif attack == "generation":
        kwargs["purpose"] = "workflow_generation"
    else:
        request["dataset"]["classification"] = "public"
    with pytest.raises((PermissionError, ValueError)):
        resolver.verify(request, **kwargs)
    assert calls == []


def test_authentication_error_is_not_recovered_by_another_proof(tmp_path, monkeypatch):
    request, resolver, kwargs, accepted, calls = qualification_case(tmp_path, monkeypatch)
    accept_fixture(request, resolver, kwargs, accepted)
    accepted.clear()
    with pytest.raises(PermissionError, match="independently accepted"):
        resolver.verify(request, **kwargs)
    assert len(calls) == 1


def test_temporal_omnibus_cannot_be_relabeled_mean_expression(tmp_path, monkeypatch):
    request, resolver, kwargs, _, calls = qualification_case(tmp_path, monkeypatch, temporal=True)
    with pytest.raises(PermissionError, match="temporal|omnibus"):
        resolver.verify(request, **kwargs)
    assert calls == [] and kwargs["methods"].list_records("run") == []


def test_single_required_json_output_uses_its_actual_declared_port(tmp_path, monkeypatch):
    """A port name is not the canonical table's content/schema identity."""
    request, resolver, kwargs, accepted, _ = qualification_case(tmp_path, monkeypatch)
    from devbench.analysis.rna_scientific_trial import workflow_snapshot
    snapshot = workflow_snapshot(kwargs["planner"], request["workflow"], workspace=tmp_path)
    experiment = snapshot["experiments"][0]
    step = kwargs["methods"].get("step", experiment["step_ids"][experiment["candidates"][0]])
    outputs = kwargs["methods"].method(step["method_version_id"])["contract"]["outputs"]
    assert outputs == {"dataset": {"artifact_type": "json", "path": "/outputs/result.json", "required": True}}
    # The trusted test registry still binds the complete actual method contract;
    # it never infers canonical contents from the port name or artifact type.
    binding = accept_fixture(request, resolver, kwargs, accepted)
    result = resolver.verify(request, **kwargs)
    assert result["binding_sha256"] == digest(binding)
    assert result["rendering"] == {"required": False, "qualified": False}


@pytest.mark.parametrize("outputs", [
    {"dataset": {"artifact_type": "json", "path": "/outputs/result.json", "required": True},
     "extra": {"artifact_type": "json", "path": "/outputs/extra.json", "required": True}},
    {"dataset": {"artifact_type": "table", "path": "/outputs/result.json", "required": True}},
    {"dataset": {"artifact_type": "json", "path": "/outputs/result.json", "required": False}},
])
def test_output_port_name_does_not_waive_single_required_json_contract(tmp_path, monkeypatch, outputs):
    request, resolver, kwargs, _, calls = qualification_case(tmp_path, monkeypatch, output_contract=outputs)
    with pytest.raises(PermissionError, match="canonical gene-table output adapter"):
        resolver.verify(request, **kwargs)
    assert calls == [] and kwargs["methods"].list_records("run") == []
