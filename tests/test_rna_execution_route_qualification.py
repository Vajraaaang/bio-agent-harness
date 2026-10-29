"""Bounded synthetic route evidence; admitted /2 execution remains unqualified.

The admitted trial API remains blocked.  A lower-level MethodService run of the
exact generated registered step exercises the trusted dispatcher on a lossless
bridge, without treating that call as admitted /2 execution.  Recorded selectors
make no model/provider calls.  All choices are test-author fixture choices, and
the evidence establishes no scientific winner or clinical release.
"""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy
import csv
import json
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from devbench.analysis.checkpoints import digest
from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis import rna_scientific_trial as trial_api
from devbench.analysis import rna_publication_comparison as comparison_api
from devbench.analysis import figure_artifacts as figures
from devbench.analysis import rna_publication_report as reports
from devbench.analysis.rna_comparison_outputs import (
    assessment_run_binding, extract_comparison_outputs, load_comparison_outputs,
    load_comparison_gene_table,
)
from devbench.analysis.rna_execution_inputs import prepare_execution_inputs, load_execution_inputs
from devbench.analysis.rna_scientific_trial_contract import trial_binding
from devbench.analysis.rna_trace_receipts import LocalTraceObserver, observe_boundary
from tests.test_rna_execution_inputs import ports, registered_ports
from tests.test_rna_publication_profiles import fixture as intent_fixture, source
from tests.test_rna_scientific_trial import NOW, fixture
from tests.test_rna_trial_authority import signed_approval


def _registered_answers(selector, service, selected, inputs):
    """Bind recorded scientific nomination and execution to the same exact name."""
    name = service.method(selected["method_version_id"])["contract"]["name"]
    selector.answers[0]["answer"]["candidates"][0]["name"] = name
    for citation in selector.answers[0]["citations"]:
        citation["candidate"] = name
    selector.answers[1]["answer"]["bindings"][0].update(candidate=name, method_version_id=selected["method_version_id"])
    spec = selector.answers[2]["answer"]["experiments"][0]
    spec.update(candidates=[name], inputs={name: deepcopy(inputs)}, parameters={name: deepcopy(selected["parameters"])})
    spec["resource_limits"].update(memory_mb=4096, timeout_seconds=300)
    return name


def _case(root, *, governed_bridge=False, trace_observer=None):
    """Retain a bounded synthetic intent before generating any exact workflow."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    request, planner, selector, branch, method, exposure = fixture(root)
    service = planner.methods
    samples = ["s" + str(i) for i in range(8)]
    matrix = {"gene_ids": ["g1", "g2"], "sample_ids": samples,
        "matrix": [[float(i) + (.15 if i % 2 else -.1) for i in range(8)], [1.0] * 8]}
    rows = [{"sample_id": sample, "group": "A" if i < 4 else "B", "biological_unit_id": "u" + str(i),
        "batch": "b" + str(i % 2), "time": i, "numeric_time": float(i)} for i, sample in enumerate(samples)]
    for name, value in (("matrix", matrix), ("metadata", {"samples": rows})):
        ref = source(root / ("eight-" + name + ".json"), value)
        request["dataset"]["binding"][name + "_artifact_id"] = service.import_artifact(
            ref["path"], artifact_type="json", actor="synthetic-test")["artifact_id"]
    binding = request["dataset"]["binding"]
    binding["declarations"]["metadata_roles"]["biological_unit"] = "biological_unit_id"
    binding["declarations"]["metadata_roles"]["time"] = "time"
    request["design"] = {"contrasts": [], "covariates": [], "chronological_field": "time",
        "chronological_order": [str(i) for i in range(8)]}
    request["objective"]["intent"] = "infer"
    request["dataset"]["inspection_sha256"] = digest(trial_api.inspect_trial_data(service, request))
    request["allocation"].update(memory_mb=4096, seconds=900, storage_bytes=50_000_000)
    original_inputs = {name: binding[name + "_artifact_id"] for name in ("matrix", "metadata")}
    selector.answers[2]["answer"]["experiments"][0]["inputs"]["Unknown QC"] = original_inputs

    selected = registered_ports(service)
    selected["parameters"].update(formula_str="~ numeric_time", time_col="time")
    selected["objective"] = {"intent": "infer"}
    selected["design"] = deepcopy(request["design"])
    selected["string_columns"] = ["sample_id", "biological_unit_id"]
    bridge = prepare_execution_inputs(binding, selected, artifact_service=service, output=root / "registered-bridge")
    bridge_record = load_execution_inputs(bridge["record_ref"], artifact_service=service, dataset_binding=binding)
    execution_inputs = {name: ref["artifact_id"] for name, ref in bridge_record["execution_refs"].items()}
    run_id = uuid.uuid4().hex

    intent = intent_fixture(root, dataset_binding=binding)
    intent["question_ref"] = request["question"]["source"]
    intent["dataset_ref"] = source(root / "exact-trial-dataset.json", {"format": "devbench.rna-trial-dataset/2", **request["dataset"]})
    intent["exposure_ref"] = source(root / "exact-exposure.json", exposure)
    intent["profile_specs"][0]["metric_specs"][0]["parameter_record_ref"] = source(root / "exact-metric-parameters.json", {
        "format": "devbench.rna-gene-comparison-parameters/2", "actual_table_id": "temporal-" + run_id,
        "reference_claim_ids": intent["required_claim_ids"], "selection_rule": None})
    intent["reference_bundle_ref"] = source(root / "grounded-empty-reference.json", {
        "format": profiles.REFERENCE_RESULT_SET_FORMAT, "reference_set_id": "synthetic-grounding-no-reference-effects",
        "publication_ref": request["publication"], "source_collection_ref": None,
        "estimand_ref": source(root / "exact-reference-estimand.json", intent["estimand"]), "findings": [],
        "universe_ref": intent["universe_policy_ref"], "exposure_ref": intent["exposure_ref"],
        "limitations": ["Synthetic publication custody only; no extracted comparable reference effect exists"]})
    analyst = profiles.read_reference_json(intent["analyst_projection_ref"], workspace=root)
    analyst.update(question_ref=intent["question_ref"], dataset_ref=intent["dataset_ref"], estimand=intent["estimand"])
    intent["analyst_projection_ref"] = source(root / "analyst.json", analyst)
    evaluator = profiles.read_reference_json(intent["evaluator_projection_ref"], workspace=root)
    evaluator.update(exposure_ref=intent["exposure_ref"], reference_bundle_ref=intent["reference_bundle_ref"])
    intent["evaluator_projection_ref"] = source(root / "evaluator.json", evaluator)
    frozen = profiles.prepare_assessment_intent(root / "intent", intent, workspace=root, service=service)
    request.update(format="devbench.rna-scientific-trial/2", assessment_intent=frozen, assessment_plan=None, scientific_context=None)
    if governed_bridge:
        # The explicit immutable lineage must precede generation authorization.
        request["execution_inputs_ref"] = bridge["record_ref"]
        registered_name = _registered_answers(selector, service, selected, execution_inputs)
    request["engineering"].update(source_digest=trial_api.trial_enforcement_source_digest(),
        runtime_digest=trial_api.trial_enforcement_runtime_digest())
    key = Ed25519PrivateKey.generate()
    runtime = {name: request["engineering"][name] for name in
        ("route", "source_digest", "runtime_digest", "backend_digest", "model", "platform")}

    def authority():
        def verify(context):
            if (context is None or context.get("planner") is not planner or context.get("actor") != "participant"
                    or context.get("selector", selector) is not selector):
                return {}
            # Recompute local enforcement bytes; the remaining identities describe
            # only this explicitly offline recorded fixture, never a live backend.
            return {**runtime, "source_digest": trial_api.trial_enforcement_source_digest(),
                "runtime_digest": trial_api.trial_enforcement_runtime_digest()}
        return trial_api.AuthenticatedTrialAuthority(root / "authority.sqlite", workspace=root,
            issuer_keys={"fixture-issuer": key.public_key()}, runtime_verifier=verify, clock=lambda: NOW)

    signed_approval(request, key, root, grant_id="generation")
    generation_request = deepcopy(request)
    trusted = authority()
    request["workflow"] = trial_api.generate_question_workflow(request, planner=planner, workspace=root, selector=selector,
        branch_id=branch["branch_id"], version_ids=[selected["method_version_id"] if governed_bridge else method["version_id"]], actor="participant", authority=trusted,
        now=NOW, output=root / "generated-workflow", **({"trace_observer": trace_observer} if trace_observer is not None else {}), scientific_workflow=RecordingScientificRuntime())

    original_ports = ports(encoding="json")
    original_ports["ports"]["matrix"] = original_ports["ports"].pop("counts")
    for port in original_ports["ports"].values():
        port["parameter"] = None
    original_bridge = bridge if governed_bridge else prepare_execution_inputs(binding, original_ports,
        artifact_service=service, output=root / "generated-inputs")
    original_plan = profiles.finalize_assessment_plan(frozen, request["workflow"]["workflow_ref"], original_bridge["record_ref"],
        output=root / "generated-finalization", workspace=root, service=service)
    request["assessment_plan"] = profiles.prepare_assessment_plan(root / "generated-plan", original_plan, workspace=root, service=service)
    request["comparison"] = comparison_api.prepare_assessment_comparison(root / "generated-comparison", request["assessment_plan"],
        workspace=root, artifact_service=service)
    request["scientific_context"] = {"project_id": uuid.uuid4().hex, "scientific_run_id": uuid.uuid4().hex,
        "attempt_id": uuid.uuid4().hex, "dataset_version_ids": [digest(binding)]}
    signed_approval(request, key, root, purpose="trial_execution", grant_id="execution")

    if governed_bridge:
        snapshot = trial_api.workflow_snapshot(planner, request["workflow"])
        step_id = snapshot["experiments"][0]["step_ids"][registered_name]
        step = service.get("step", step_id)
        real_plan_ref = request["assessment_plan"]
    else:
        step = service.add_step(branch["branch_id"], selected["method_version_id"], execution_inputs,
            selected["parameters"], actor="separate-synthetic-engineering")
        governed = {"steps": [step]}
        manual = source(root / "registered-workflow.json", {"format": "devbench.rna-assessment-workflow/2",
            "assessment_intent_ref": frozen, "assessment_rules_sha256": profiles.assessment_rules_digest(intent),
            "governed_workflow": governed, "governed_workflow_sha256": digest(governed)})
        real_plan = profiles.finalize_assessment_plan(frozen, manual, bridge["record_ref"], output=root / "registered-finalization",
            workspace=root, service=service)
        real_plan_ref = profiles.prepare_assessment_plan(root / "registered-plan", real_plan, workspace=root, service=service)
    return {"request": request, "generation_request": generation_request, "planner": planner, "selector": selector,
        "authority": authority, "key": key, "intent": intent, "binding": binding, "matrix": matrix, "rows": rows,
        "bridge": bridge, "bridge_record": bridge_record, "step": step, "plan": real_plan_ref,
        "selected": selected, "branch": branch, "run_id": run_id}


def test_authenticated_v2_denies_unqualified_route_without_dispatch_and_rejects_replay(tmp_path):
    case = _case(tmp_path)
    request, planner = case["request"], case["planner"]
    service = planner.methods
    readiness = trial_api.inspect_readiness(request, methods=service, workspace=tmp_path, planner=planner, now=NOW)
    assert readiness["local_status"] == "blocked"
    assert "unqualified_adapters_metrics_renderers_execution_route" in readiness["blockers"]
    assert service.list_records("run") == []
    before = planner.get(request["workflow"]["planner_session_id"])
    with pytest.raises(PermissionError, match="Trial admission blocked.*unqualified_adapters"):
        trial_api.execute_trial(request, planner=planner, workspace=tmp_path, actor="participant", authority=case["authority"](), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert service.list_records("run") == []
    assert planner.get(request["workflow"]["planner_session_id"]) == before

    authority = case["authority"]()
    authority.bind_runtime_context({"planner": planner, "selector": case["selector"], "actor": "participant"})
    generation = case["generation_request"]
    with pytest.raises(PermissionError, match="used|consumed|replay"):
        authority.consume(generation, purpose="workflow_generation", binding_sha256=trial_binding(generation, "workflow_generation"))
    swapped = deepcopy(request)
    swapped["approvals"]["trial_execution"] = deepcopy(generation["approvals"]["workflow_generation"])
    swapped["approvals"]["trial_execution"].update(purpose="trial_execution", binding_sha256=trial_binding(request, "trial_execution"))
    with pytest.raises((ValueError, PermissionError), match="purpose|subject"):
        authority.consume(swapped, purpose="trial_execution", binding_sha256=trial_binding(request, "trial_execution"))
    assert service.list_records("run") == []


def test_generated_workflow_cannot_silently_adopt_derived_bridge_artifacts(tmp_path):
    case = _case(tmp_path)
    request = deepcopy(case["generation_request"])
    request["trial_id"] = "registered-bridge-generation-negative"
    selector = case["selector"]
    # New recorded answers are fixture choices, with no model/provider launch.
    from tests.test_rna_scientific_trial import fixture as original_fixture
    answer_root = tmp_path / "fresh-recorded-answers"
    answer_root.mkdir()
    _, _, fresh, _, _, _ = original_fixture(answer_root)
    selector.answers = deepcopy(fresh.answers)
    _registered_answers(selector, case["planner"].methods, case["selected"], case["step"]["inputs"])
    signed_approval(request, case["key"], tmp_path, grant_id="bridge-generation")
    with pytest.raises((ValueError, PermissionError), match="exact artifact supplied in the prompt|Binding does not match the scientific candidate"):
        trial_api.generate_question_workflow(request, planner=case["planner"], workspace=tmp_path, selector=selector,
            branch_id=case["branch"]["branch_id"], version_ids=[case["selected"]["method_version_id"]], actor="participant",
            authority=case["authority"](), now=NOW, output=tmp_path / "denied-registered-generation", scientific_workflow=RecordingScientificRuntime())
    assert case["planner"].methods.list_records("run") == []
    assert not (tmp_path / "denied-registered-generation").exists()


def test_generated_registered_step_runs_only_as_lower_level_engineering_evidence_with_comparison_abstention(tmp_path):
    case = _case(tmp_path, governed_bridge=True)
    service, step, plan = case["planner"].methods, case["step"], case["plan"]
    bridge = case["bridge_record"]
    assert bridge["original_refs"]["matrix"]["artifact_id"] == case["binding"]["matrix_artifact_id"]
    with service.artifact_path(bridge["execution_refs"]["counts"]["artifact_id"]).open(newline="") as handle:
        converted = list(csv.reader(handle))
    assert converted[0] == ["gene_id", *case["matrix"]["sample_ids"]]
    assert [[json.loads(x) for x in row[1:]] for row in converted[1:]] == case["matrix"]["matrix"]
    frozen = profiles.load_assessment_plan(plan, workspace=tmp_path, service=service)
    prerequisites = profiles.read_reference_json(frozen["execution_prerequisites_ref"], workspace=tmp_path, service=service)
    assert prerequisites["computational_admission"] == "blocked"
    assert case["request"]["execution_inputs_ref"] == frozen["execution_inputs_ref"]
    assert frozen["workflow_ref"] == case["request"]["workflow"]["workflow_ref"]
    observer = LocalTraceObserver(tmp_path / "engineering-observer.sqlite",
        source_digest=case["request"]["engineering"]["source_digest"],
        runtime_digest=case["request"]["engineering"]["runtime_digest"],
        policy_digest=digest({"assessment_plan": plan, "scope": "lower_level_synthetic_engineering"}))
    engineering_id = "synthetic-engineering-" + case["run_id"]
    assert engineering_id != case["request"]["trial_id"]
    assert service.list_records("run") == []
    with pytest.raises(PermissionError, match="Trial admission blocked.*unqualified_adapters") as denied:
        trial_api.execute_trial(case["request"], planner=case["planner"], workspace=tmp_path, actor="participant",
            authority=case["authority"](), now=NOW, trace_observer=observer, scientific_workflow=RecordingScientificRuntime())
    assert service.list_records("run") == []
    allocated = assessment_run_binding(plan, artifact_service=service, **case["request"]["scientific_context"],
        controller_id=case["run_id"], step_id=step["step_id"])
    run = observe_boundary(observer, engineering_id, "run", lambda: service.run_step(step["step_id"],
        actor="separate-synthetic-engineering", run_id=allocated["controller_id"], correlation=allocated["correlation"]),
        role="numerical", binding_digest=digest({"plan": plan, "step": step, "preallocated_execution": allocated}))
    assert run["status"] == "succeeded", run.get("error")
    assert run["execution"]["runtime"] == "trusted_dispatcher"
    assert service.provenance(run["run_id"])["valid"] is True
    raw = json.loads(service.artifact_path(run["outputs"]["result"]).read_bytes())
    assert raw["family"] == "gaussian"
    retained = observe_boundary(observer, engineering_id, "verify",
        lambda: extract_comparison_outputs([run["run_id"]], plan, artifact_service=service),
        binding_digest=digest({"plan": plan, "run_id": run["run_id"], "run_evidence": run["evidence"]}))
    bundle = load_comparison_outputs(retained, artifact_service=service)
    table = load_comparison_gene_table(bundle["gene_tables"][0]["table_ref"], artifact_service=service)
    assert table["table_id"] == "temporal-" + case["run_id"]
    assert table["family"]["eligible_ids"] == ["g1", "g2"] and table["family"]["complete"] is True
    for row in table["rows"]:
        actual = raw["genes"][row["feature_id"]]
        assert row["p_value"] == actual["p_value"] and row["q_value"] == actual["q_value"]
        assert row["q_value"] >= row["p_value"]
        # Preserve an explicitly reported endpoint difference only.  Coefficients
        # alone never authorize inventing an effect or its scientific alignment.
        expected_effect = actual.get("effect_difference") if actual.get("effect_scale") == "expression_units" and actual["status"] == "ok" else None
        assert row["effect"] == expected_effect
    assert table["alignment"]["estimand_id"] is None and table["alignment"]["contrast"] is None
    assert any("unknown_effect_comparability" in d["reason_codes"] for d in bundle["diagnostics"])
    assert any("authenticated_admission_not_established_by_output_custody" in d["reason_codes"] for d in bundle["diagnostics"])
    assert bundle["runs"][0]["frozen_comparison_eligible"] is True
    assert case["planner"].get(case["request"]["workflow"]["planner_session_id"])["results"] == []
    with pytest.raises(ValueError, match="incomparable"):
        figures.prepare_gene_effect_plot_payload(tmp_path / "denied-effect-plot", reference_table_ref=bundle["gene_tables"][0]["table_ref"],
            actual_table_ref=bundle["gene_tables"][0]["table_ref"], uncertainty_policy="report_absence", limitations=["Unknown effect alignment"])

    protocol = comparison_api.prepare_assessment_comparison(tmp_path / "registered-comparison", plan, workspace=tmp_path, artifact_service=service)
    assessment = observe_boundary(observer, engineering_id, "synthesis", lambda: comparison_api.compare_assessment_outputs(
        protocol["manifest_path"], protocol["root_hash"], output_bundle_ref=retained["record_ref"],
        workspace=tmp_path, artifact_service=service, output=tmp_path / "registered-assessment", allow_evidence_read=True),
        binding_digest=digest({"plan": plan, "comparison": protocol, "output_bundle": retained["record_ref"]}))
    metric = assessment["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert assessment["record"]["numeric_assessments"][0]["decision"] == "not_assessable"
    report = observe_boundary(observer, engineering_id, "execution_review", lambda: reports.load_assessment_report(
        assessment["report"]["manifest_path"], assessment["report"]["root_hash"], workspace=tmp_path,
        artifact_service=service, allow_evidence_read=True), binding_digest=digest(assessment["report"]))
    review = observe_boundary(observer, engineering_id, "review", lambda: comparison_api.assessment_review_evidence(
        assessment["manifest_path"], assessment["root_hash"], workspace=tmp_path, artifact_service=service, allow_evidence_read=True),
        binding_digest=digest({"comparison": {key: assessment[key] for key in ("manifest_path", "root_hash")}, "report": assessment["report"]}))
    assert review["gene_tables"][0]["role"] == "actual" and review["gene_tables"][0]["table"] == table
    assert report["clinical_release_allowed"] is False
    assert report["dimensions"]["qualified_review"]["state"] == "not_established"
    assert report["dimensions"]["publication_agreement"]["state"] == "not_assessed"
    assert assessment["record"]["plots"] == []
    assert "profile_execution_qualification_not_established" in assessment["record"]["limitations"]

    # An actual diagnostic plot of supplied observations is useful, while the
    # comparison-specific projection is correctly unavailable for unknown effects.
    data = {"format": "devbench.numeric-plot-source/2", "plot_kind": "measured_curve", "entity_kind": "gene_time",
        "axes": {axis: {"field": field, "label": label, "unit": unit, "transform": "identity", "direction": "increasing",
            "range_policy": "saved_full_range_no_clipping"} for axis, field, label, unit in
            (("x", "time", "Synthetic collection time", "fixture units"), ("y", "expression", "Observed normalized expression", "expression units"))},
        "membership": [[gene, sample, "observed"] for gene in case["matrix"]["gene_ids"] for sample in case["matrix"]["sample_ids"]],
        "rows": [{"entity_id": gene, "coordinate_id": sample, "group_id": "observed", "x": {"status": "exact", "value": i, "censoring": None},
            "y": {"status": "exact", "value": case["matrix"]["matrix"][j][i], "censoring": None}, "interval": None}
            for j, gene in enumerate(case["matrix"]["gene_ids"]) for i, sample in enumerate(case["matrix"]["sample_ids"])],
        "uncertainty_policy": "report_absence", "limitations": ["Observed synthetic inputs only; no comparison or independent scientific review"]}
    numeric = source(tmp_path / "observed-numeric-source.json", data)
    payload = {"format": "devbench.figure-data/2", "source_ref": numeric, "data": data}
    figure = observe_boundary(observer, engineering_id, "synthesis", lambda: figures.export_publication_figure(
        tmp_path / "observed-figure", payload, figure_id="observed-synthetic-inputs",
        analysis_binding={"input_sha256": digest(bundle["runs"][0]["input_refs"]), "parameters_sha256": digest(run["parameters"]),
            "analysis_source_sha256": service.get("artifact", service.method(run["method_version_id"])["source_artifact"])["sha256"],
            "runtime_sha256": None, "output_bundle_ref": {key: retained[key] for key in ("manifest_path", "root_hash")}}),
        binding_digest=digest({"numeric_source": numeric, "output_bundle": retained["record_ref"], "run_id": run["run_id"]}))
    figure_record, saved = observe_boundary(observer, engineering_id, "verify", lambda: figures.load_publication_figure(
        figure["manifest_path"], figure["root_hash"]), binding_digest=digest(figure), finalize_root=True)
    assert saved == payload and figure_record["comparison_binding_status"] == "not_comparison_bound"
    assert figure_record["scientific_validity_established"] is False and figure_record["pixels_status"] == "not_assessed"
    assert (tmp_path / "observed-figure/figure.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (tmp_path / "observed-figure/figure.pdf").read_bytes().startswith(b"%PDF-")
    with pytest.raises(ValueError, match="already started|already.*run|executed|started"):
        profiles.finalize_assessment_plan(frozen["intent_ref"], frozen["workflow_ref"], frozen["execution_inputs_ref"],
            output=tmp_path / "forbidden-postrun-freeze", workspace=tmp_path, service=service)
    with pytest.raises((ValueError, KeyError), match="run|receipt|retained"):
        extract_comparison_outputs([{**run, "status": "failed"}], plan, artifact_service=service)
    events = observer.read_events(engineering_id)
    denied_events = observer.read_events(case["request"]["trial_id"])
    assert {event["phase"] for event in events} >= {"run", "verify", "synthesis", "review", "execution_review"}
    assert len({event["trace_id"] for event in events}) == 1
    assert {event["trace_id"] for event in events}.isdisjoint({event["trace_id"] for event in denied_events})
    contexts = observer.read_contexts(engineering_id)
    roots = [context for context in contexts if context["parent_span_id"] is None]
    assert len(roots) == 1 and events[-1]["span_id"] == roots[0]["span_id"]
    assert all(event["parent_span_id"] == roots[0]["span_id"] for event in events if event["phase"] != "workflow")
    assert events[-1]["source_status"] == "completed" and observer.failure_count == 0
    assert not any(event["phase"] == "permission_preflight" for event in events)
    assert any(event["phase"] == "permission_preflight" and event["source_status"] == "blocked"
        and event["dispatch_status"] == "not_dispatched" for event in denied_events)
    trace_ref = source(tmp_path / "engineering-trace-events.json", {"events": events, "contexts": contexts,
        "denied_trial_events": denied_events, "scope": "actual_local_operations_not_model_review_or_admitted_trial"})
    source(tmp_path / "route-proof.json", {
        "format": "devbench.synthetic-route-qualification-evidence/1",
        "scope": "authenticated_generated_step_lower_level_engineering_execution_only_not_admitted_trial",
        "model_calls": 0, "provider_calls": 0, "study_data_used": False,
        "generated_workflow": {"workflow": case["request"]["workflow"],
            "assessment_intent": case["request"]["assessment_intent"],
            "assessment_plan": case["request"]["assessment_plan"], "trial_api_dispatched": False,
            "execution_denial": str(denied.value)},
        "lower_level_registered_worker": {"execution_inputs": case["bridge"], "assessment_plan": plan,
            "exact_generated_step": True, "admitted_trial_execution": False,
            "step_id": step["step_id"], "run_id": run["run_id"], "run_evidence": run["evidence"],
            "raw_output_artifact_id": run["outputs"]["result"],
            "raw_output_path": str(service.artifact_path(run["outputs"]["result"])),
            "output_bundle": retained, "comparison": {key: assessment[key] for key in ("manifest_path", "root_hash")},
            "report": assessment["report"], "comparison_status": metric["status"],
            "comparison_reason_codes": metric["reason_codes"]},
        "observed_input_figure": {"receipt": figure, "png_path": str(tmp_path / "observed-figure/figure.png"),
            "pdf_path": str(tmp_path / "observed-figure/figure.pdf"), "numeric_source": numeric,
            "comparison_binding_status": figure_record["comparison_binding_status"],
            "pixels_status": figure_record["pixels_status"]},
        "scientific_validity_established": False, "clinical_release_allowed": False,
        "qualified_independent_review": "not_established",
        "local_observations": {"store_path": str(observer.path), "events_ref": trace_ref,
            "engineering_workflow_id": engineering_id, "engineering_trace_id": roots[0]["trace_id"],
            "denied_trial_id": case["request"]["trial_id"], "observer_failure_count": observer.failure_count,
            "review_scope": "deterministic_evidence_projection_no_model_review"},
    })


def test_native_backend_capability_denies_both_policies_without_launching_a_model(tmp_path):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.episode_codex import CodexActionSelector
    from devbench.analysis.rna_investigation_backend import AuditedInvestigationBackend
    service = MethodService(tmp_path / "private")
    selector = CodexActionSelector(requested_model="qualification-no-dispatch", workspace_parent=tmp_path / "actors",
        private_root=service.root, memory_mb=256)
    backend = AuditedInvestigationBackend(service, selector)
    capability = backend.capabilities()
    assert capability["backend"] == "codex-cli" and capability["qualification"] == "unqualified"
    assert capability["enforced_total_token_cap"] is False and capability["parallel_boundary_qualified"] is False
    assert capability["verified_usage_capture"] is False and capability["usage_capture"] == "posthoc_optional"
    assert "aggregate_token_ceiling_unavailable" in capability["limitations"]
    for name in ("source_digest", "runtime_digest", "backend_digest", "capability_digest"):
        assert len(capability[name]) == 64
    for policy in ("hard_total_tokens", "bounded_observed_tokens"):
        with pytest.raises(PermissionError, match="blocked_backend_capability:" + policy):
            backend.require_admission(policy)
    assert service.list_records("run") == [] and not (tmp_path / "actors").exists()
