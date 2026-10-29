"""Additive assessment bindings; fixtures establish engineering behavior only."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy

import pytest

from devbench.analysis.rna_scientific_trial_contract import (
    trial_binding, validate_approval, validate_trial_request,
)
from devbench.analysis.rna_scientific_trial import generate_question_workflow
from devbench.analysis.checkpoints import digest
from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis import rna_scientific_trial as trial_api
from tests.test_rna_scientific_trial import NOW, FakeAuthority, approve, fixture
from tests.test_rna_publication_profiles import fixture as assessment_fixture, source as json_source
from tests.test_rna_trial_authority import signed_approval
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def request_v2(tmp_path):
    request, *_ = fixture(tmp_path)
    request.update(format="devbench.rna-scientific-trial/2",
        assessment_intent={"manifest_path": str(tmp_path / "intent/audit/manifest.json"), "root_hash": "d" * 64},
        assessment_plan=None, scientific_context=None)
    request["approvals"]["workflow_generation"] = None
    return request


def test_v2_execution_input_reference_is_bound_before_generation_approval(tmp_path):
    request = request_v2(tmp_path)
    request["execution_inputs_ref"] = json_source(tmp_path / "execution-input-reference.json", {"fixture": True})
    assert validate_trial_request(request) == request
    approve(request, "workflow_generation")
    validate_approval(request, "workflow_generation", now=NOW)
    original = trial_binding(request, "trial_execution")
    request["execution_inputs_ref"]["sha256"] = "e" * 64
    assert trial_binding(request, "trial_execution") != original
    with pytest.raises(PermissionError, match="binding"):
        validate_approval(request, "workflow_generation", now=NOW)
    request["format"] = "devbench.rna-scientific-trial/1"
    with pytest.raises(ValueError, match="closed"):
        validate_trial_request(request)


def test_v2_execution_input_reference_cannot_be_attached_after_approval(tmp_path):
    request = request_v2(tmp_path)
    approve(request, "workflow_generation")
    request["execution_inputs_ref"] = json_source(tmp_path / "late-input-reference.json", {"fixture": True})
    with pytest.raises(PermissionError, match="binding"):
        validate_approval(request, "workflow_generation", now=NOW)


def _governed_bridge_planner(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs, load_execution_inputs
    from tests.test_rna_execution_inputs import inputs, registered_ports
    from tests.test_method_planner import candidate, Selector, trial_spec
    service, binding, _, _ = inputs(tmp_path)
    selected = registered_ports(service)
    bridge = prepare_execution_inputs(binding, selected, artifact_service=service, output=tmp_path / "bridge")
    record = load_execution_inputs(bridge["record_ref"], artifact_service=service, dataset_binding=binding)
    planner = ScientificMethodPlanner(service)
    original_ids = [binding[key] for key in ("matrix_artifact_id", "metadata_artifact_id")]
    state = planner.create({"scientific_objective": "Verify the synthetic execution-input route", "computational_task": "quality_control"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": original_ids},
        actor="fixture", data_classification="synthetic", evaluation_purpose="execution_verification",
        rna_input_contract=binding, rna_execution_inputs_ref=bridge["record_ref"])
    name = service.method(selected["method_version_id"])["contract"]["name"]
    scientific = candidate(name)
    scientific["assumptions"][0]["field"] = "bulk_rna_input.measurements_finite"
    scientific["parameter_candidates"] = []
    answers = Selector({"candidates": [scientific]}, {"bindings": [
        {"candidate": name, "method_version_id": selected["method_version_id"], "discovery": None}]})
    planner.generate_candidates(state["session_id"], answers, actor="fixture")
    planner.resolve_capabilities(state["session_id"], answers, [selected["method_version_id"]], actor="fixture")
    inputs = {port: ref["artifact_id"] for port, ref in record["execution_refs"].items()}
    spec = trial_spec({"inputs": inputs})
    spec.update(candidates=[name], inputs={name: inputs}, parameters={name: selected["parameters"]})
    spec["resource_limits"].update(memory_mb=4096, timeout_seconds=300)
    analysis = service.create_analysis("Bounded bridge fixture", actor="fixture")
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="fixture")
    return planner, state, binding, bridge, record, selected, name, spec, branch


def test_planner_governed_bridge_retains_original_identity_and_exact_derived_ports(tmp_path):
    from tests.test_method_planner import Selector
    planner, state, binding, bridge, record, _, name, spec, branch = _governed_bridge_planner(tmp_path)
    assert set(state["context"]["dataset_summary"]["artifact_ids"]) == {
        binding["matrix_artifact_id"], binding["metadata_artifact_id"]}
    selector = Selector({"experiments": [spec]})
    proposed = planner.design_trials(state["session_id"], selector, actor="fixture")
    available = selector.contexts[0]["available_artifacts"]
    assert set(spec["inputs"][name].values()) <= set(available)
    declared = planner.declare_trial(state["session_id"], branch["branch_id"], proposed[0], actor="fixture")
    assert declared["inputs"][name] == {port: ref["artifact_id"] for port, ref in record["execution_refs"].items()}
    assert planner.get(state["session_id"])["rna_execution_inputs"]["record_ref"] == bridge["record_ref"]
    assert "workspace" not in state["rna_execution_inputs"]
    assert planner.methods.list_records("run") == []


@pytest.mark.parametrize("attack", ["parameters", "ports", "metadata", "subset"])
def test_planner_governed_bridge_rejects_unsealed_trial_bindings(tmp_path, attack):
    planner, state, binding, _, _, _, name, spec, branch = _governed_bridge_planner(tmp_path)
    if attack == "parameters":
        spec["parameters"][name]["data_type"] = "counts"
    elif attack == "ports":
        spec["inputs"][name] = dict(reversed(list(spec["inputs"][name].items())))
        spec["inputs"][name]["counts"], spec["inputs"][name]["metadata"] = (
            spec["inputs"][name]["metadata"], spec["inputs"][name]["counts"])
    elif attack == "metadata":
        spec["inputs"][name]["metadata"] = binding["metadata_artifact_id"]
    else:
        spec["subset_spec"]["kind"] = "representative_pilot"
    with pytest.raises(ValueError, match="bridge|reader|execution|full.dataset"):
        planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="fixture")
    assert planner.get(state["session_id"])["experiments"] == []
    assert planner.methods.list_records("step") == []


def test_planner_governed_bridge_rejects_foreign_original_binding(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
    from tests.test_rna_execution_inputs import inputs, registered_ports
    service, binding, _, _ = inputs(tmp_path)
    bridge = prepare_execution_inputs(binding, registered_ports(service), artifact_service=service, output=tmp_path / "bridge")
    changed = deepcopy(binding)
    changed["declarations"]["provenance"] = {"source": "different original declaration"}
    with pytest.raises(ValueError, match="Original dataset binding"):
        ScientificMethodPlanner(service).create(
            {"scientific_objective": "Bounded fixture", "computational_task": "quality_control"},
            {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [binding["matrix_artifact_id"], binding["metadata_artifact_id"]]},
            actor="fixture", rna_input_contract=changed, rna_execution_inputs_ref=bridge["record_ref"])
    assert service.list_records("method_plan") == []


@pytest.mark.parametrize("port,original,replacement", [("counts", b"1.0", b"9.0"), ("metadata", b"u1", b"u9")])
def test_planner_governed_bridge_rechecks_derived_bytes_after_declaration_before_first_dispatch(
        tmp_path, monkeypatch, port, original, replacement):
    planner, state, _, _, record, _, _, spec, branch = _governed_bridge_planner(tmp_path)
    planner.declare_trial(state["session_id"], branch["branch_id"], spec, actor="fixture")
    runs_before = planner.methods.list_records("run")
    assert runs_before == []
    path = planner.methods.artifact_path(record["execution_refs"][port]["artifact_id"])
    body = path.read_bytes()
    assert original in body
    path.chmod(0o600)
    path.write_bytes(body.replace(original, replacement, 1))
    dispatched = []

    def forbidden_dispatch(*args, **kwargs):
        dispatched.append((args, kwargs))
        raise AssertionError("Tampered derived bytes must not reach MethodService/tool dispatch")

    monkeypatch.setattr(planner.methods, "run_step", forbidden_dispatch)
    results = planner.run_trial(state["session_id"], spec["id"], actor="fixture", allow_data_execution=True)
    assert len(results) == 1 and results[0]["status"] == "failed"
    assert any("artifact" in failure.lower() for failure in results[0]["failures"])
    assert "run_id" not in results[0]["provenance"]
    assert dispatched == []
    assert planner.methods.list_records("run") == runs_before
    assert planner.get(state["session_id"])["results"] == results


def test_execution_input_loader_preserves_default_and_rejects_service_outside_explicit_scope_before_read(tmp_path, monkeypatch):
    from devbench.analysis import rna_execution_inputs as execution_api
    from tests.test_rna_execution_inputs import inputs, registered_ports
    service, binding, _, _ = inputs(tmp_path)
    bridge = execution_api.prepare_execution_inputs(binding, registered_ports(service), artifact_service=service,
        output=tmp_path / "bridge")
    original = execution_api.load_execution_inputs(bridge["record_ref"], artifact_service=service, dataset_binding=binding)
    assert execution_api.load_execution_inputs(bridge["record_ref"], artifact_service=service,
        dataset_binding=binding, workspace=tmp_path) == original
    other_workspace = tmp_path / "other-assigned-workspace"
    other_workspace.mkdir()
    reads = []

    def forbidden_read(*args, **kwargs):
        reads.append((args, kwargs))
        raise AssertionError("A service outside the assigned workspace must be rejected before source reads")

    monkeypatch.setattr(execution_api, "_read", forbidden_read)
    with pytest.raises(ValueError, match="service.*outside.*workspace"):
        execution_api.load_execution_inputs(bridge["record_ref"], artifact_service=service, workspace=other_workspace)
    assert reads == []


@pytest.mark.parametrize("consumer", ["loader", "profiles"])
def test_execution_input_manifest_body_must_be_inside_caller_workspace_before_load(tmp_path, monkeypatch, consumer):
    from pathlib import Path
    from devbench.analysis.methods import MethodService
    from devbench.analysis import rna_execution_inputs as execution_api, episode_proposals
    from devbench.study import evidence
    sealed = episode_proposals._seal(tmp_path / "bundle", "execution-inputs.json", {}, "synthetic_scope_sentinel")
    assigned = Path(sealed["manifest_path"]).parent
    service = MethodService(assigned / "methods")
    reference = {key: sealed[key] for key in ("manifest_path", "root_hash")}
    outside_reads = []
    original_read = evidence._read

    def observed_read(path, *args, **kwargs):
        if not Path(path).resolve().is_relative_to(assigned):
            outside_reads.append(str(path))
        return original_read(path, *args, **kwargs)

    for module in (execution_api, profiles, evidence, episode_proposals):
        monkeypatch.setattr(module, "_read", observed_read)
    rejection = None
    try:
        if consumer == "loader":
            execution_api.load_execution_inputs(reference, artifact_service=service, workspace=assigned)
        else:
            profiles.read_reference_json(reference, workspace=assigned, service=service)
    except ValueError as error:
        rejection = error
    assert outside_reads == [], "A manifest inside the caller workspace caused its outside record body to be read"
    assert rejection is not None and "workspace" in str(rejection).lower()


def test_planner_rejects_explicit_scope_with_foreign_service_before_original_artifact_reads(tmp_path, monkeypatch):
    from pathlib import Path
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis import rna_execution_inputs as execution_api
    from devharness_core import provenance
    from tests.test_rna_execution_inputs import inputs, registered_ports
    service, binding, _, _ = inputs(tmp_path)
    bridge = execution_api.prepare_execution_inputs(binding, registered_ports(service), artifact_service=service,
        output=tmp_path / "bridge")
    assigned = tmp_path / "other-assigned-workspace"
    assigned.mkdir()
    original_reads = []
    open_regular = provenance._open_regular

    def observed_open(path):
        if Path(path).is_relative_to(service.root / "objects"):
            original_reads.append(str(path))
        return open_regular(path)

    monkeypatch.setattr(provenance, "_open_regular", observed_open)
    rejection = None
    try:
        ScientificMethodPlanner(service).create(
            {"scientific_objective": "Bounded synthetic fixture", "computational_task": "quality_control"},
            {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [binding["matrix_artifact_id"], binding["metadata_artifact_id"]]},
            actor="fixture", rna_input_contract=binding, rna_execution_inputs_ref=bridge["record_ref"],
            rna_execution_workspace=assigned)
    except ValueError as error:
        rejection = error
    assert original_reads == [], "Original artifact bytes were read before rejecting the foreign service scope"
    assert rejection is not None and "workspace" in str(rejection).lower()
    assert service.list_records("method_plan") == []


def test_v2_adds_immutable_intent_without_relaxing_v1(tmp_path):
    request = request_v2(tmp_path)
    assert validate_trial_request(request) == request
    request["format"] = "devbench.rna-scientific-trial/1"
    with pytest.raises(ValueError, match="closed"):
        validate_trial_request(request)


def test_v2_generation_binding_retains_intent_before_workflow_exists(tmp_path):
    request = request_v2(tmp_path)
    original = trial_binding(request, "workflow_generation")
    request["assessment_intent"]["root_hash"] = "e" * 64
    assert trial_binding(request, "workflow_generation") != original


def test_v2_execution_plan_cannot_backfill_generation_subject(tmp_path):
    request = request_v2(tmp_path)
    generation = trial_binding(request, "workflow_generation")
    execution = trial_binding(request, "trial_execution")
    request["assessment_plan"] = {"manifest_path": str(tmp_path / "plan/audit/manifest.json"), "root_hash": "f" * 64}
    assert trial_binding(request, "workflow_generation") == generation
    assert trial_binding(request, "trial_execution") != execution


def test_v2_generation_approval_rejects_intent_swap(tmp_path):
    request = request_v2(tmp_path)
    approve(request, "workflow_generation")
    validate_approval(request, "workflow_generation", now=NOW)
    request["assessment_intent"]["root_hash"] = "e" * 64
    with pytest.raises(PermissionError, match="binding"):
        validate_approval(request, "workflow_generation", now=NOW)


def test_v2_purpose_swap_stays_closed(tmp_path):
    request = request_v2(tmp_path)
    approve(request, "workflow_generation")
    approve(request, "trial_execution")
    copied = deepcopy(request)
    copied["approvals"]["trial_execution"] = copied["approvals"]["workflow_generation"]
    with pytest.raises(ValueError, match="purpose"):
        validate_trial_request(copied)


@pytest.mark.parametrize("field", ["assessment_intent", "assessment_plan"])
def test_v2_request_rejects_self_reported_inline_assessment_objects(tmp_path, field):
    request = request_v2(tmp_path)
    request[field] = {"format": "devbench.rna-assessment-intent/2", "qualified": True}
    with pytest.raises(ValueError, match="closed"):
        validate_trial_request(request)


def test_v2_generation_requires_retention_destination_before_admission(tmp_path):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    request.update(format="devbench.rna-scientific-trial/2",
        assessment_intent={"manifest_path": str(tmp_path / "intent/audit/manifest.json"), "root_hash": "d" * 64},
        assessment_plan=None, scientific_context=None)
    authority = FakeAuthority()
    with pytest.raises(ValueError, match="output"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
            branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="participant",
            authority=authority, now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert authority.calls == []
    assert selector.contexts == []


def test_v2_workflow_descriptor_requires_bound_generated_source(tmp_path):
    request = request_v2(tmp_path)
    request["workflow"] = {"planner_session_id": "unrelated", "experiment_ids": ["old"], "sha256": "b" * 64}
    with pytest.raises(ValueError, match="closed"):
        validate_trial_request(request)


def configured_v2(tmp_path, *, numeric_confounded=False, wrong_publication=False):
    request, planner, selector, branch, method, exposure = fixture(tmp_path)
    if numeric_confounded:
        samples = ["s" + str(i + 1) for i in range(8)]
        documents = {"matrix": {"gene_ids": ["g1", "g2"], "sample_ids": samples,
            "matrix": [[float(i + 1) for i in range(8)], [float(8 - i) for i in range(8)]]},
            "metadata": {"samples": [{"sample_id": sample, "group": "A" if i < 4 else "B", "unit": "u" + str(i + 1),
                "time": 1 + i // 2, "batch": 2 ** (i // 2)} for i, sample in enumerate(samples)]}}
        for port, document in documents.items():
            retained = json_source(tmp_path / ("numeric-" + port + ".json"), document)
            reference = planner.methods.import_artifact(retained["path"], artifact_type="json", actor="synthetic-test")
            request["dataset"]["binding"][port + "_artifact_id"] = reference["artifact_id"]
        request["dataset"]["binding"]["declarations"]["metadata_roles"]["time"] = "time"
        request["objective"]["intent"] = "infer"
        request["design"] = {"contrasts": [], "covariates": [], "chronological_field": "time",
            "chronological_order": ["1", "2", "3", "4"]}
        request["dataset"]["inspection_sha256"] = digest(trial_api.inspect_trial_data(planner.methods, request))
    intent = assessment_fixture(tmp_path)
    intent["question_ref"] = request["question"]["source"]
    intent["dataset_ref"] = json_source(tmp_path / "trial-dataset.json", {
        "format": "devbench.rna-trial-dataset/2", **deepcopy(request["dataset"])})
    intent["exposure_ref"] = json_source(tmp_path / "exposure.json", exposure)
    requirement = json_source(tmp_path / "trial-requirement-record.json", {"format": "devbench.rna-requirement-record/2",
        "requirements": [{"requirement_id": "question", "origin": "fixture", "name": "scientific_objective",
            "value": request["question"]["text"], "evidence": request["question"]["source_excerpt"]}]})
    intent["estimand"]["requirement_sources"] = [{"origin": "fixture", "record_ref": requirement, "requirement_ids": ["question"]}]
    selected_publication = request["publication"]
    if wrong_publication:
        from devbench.analysis.rna_publication_evidence import prepare_publication_evidence
        import hashlib
        identity = {"doi": "10.1234/unrelated-fixture", "pmid": None, "pmcid": None,
            "title": "Unrelated Synthetic Publication", "version": "1"}
        body = (identity["doi"] + "\n" + identity["title"] + "\n1\nDifferent selected publication.").encode()
        path = tmp_path / "unrelated-paper.txt"
        path.write_bytes(body)
        def locator(text):
            start = body.index(text.encode())
            return {"start_byte": start, "end_byte": start + len(text.encode()), "text": text, "location": "Synthetic unrelated publication"}
        selected_publication = prepare_publication_evidence(tmp_path / "unrelated-publication", {"identity": identity,
            "approved_urls": ["https://example.invalid/unrelated"]},
            [{"source_id": "unrelated-paper", "path": str(path), "sha256": hashlib.sha256(body).hexdigest(),
                "source_url": "https://example.invalid/unrelated", "identity": identity, "access": "extracted_text",
                "identity_locators": {key: locator(value) for key, value in identity.items() if value is not None},
                "identity_reviewed_by": "evaluator", "limitations": "Unrelated invented paper for control regression"}],
            [{"excerpt_id": "unrelated-quote", "source_id": "unrelated-paper", "locator": locator("Different selected publication."),
                "visibility": "evaluator", "purpose": "outcome"}], exposure, curator_id="evaluator", participant_id="participant",
            workspace=tmp_path, allow_evidence_read=True)
    intent["reference_bundle_ref"] = json_source(tmp_path / "trial-reference-grounding.json", {
        "format": profiles.REFERENCE_RESULT_SET_FORMAT, "reference_set_id": "generation-grounding-only",
        "publication_ref": selected_publication, "source_collection_ref": None,
        "estimand_ref": json_source(tmp_path / "trial-reference-estimand.json", intent["estimand"]),
        "findings": [], "universe_ref": intent["universe_policy_ref"], "exposure_ref": intent["exposure_ref"],
        "limitations": ["Selected synthetic publication custody only; no numeric reference claims or execution qualification"]})
    analyst = profiles.read_reference_json(intent["analyst_projection_ref"], workspace=tmp_path)
    analyst.update(question_ref=intent["question_ref"], dataset_ref=intent["dataset_ref"], estimand=intent["estimand"])
    intent["analyst_projection_ref"] = json_source(tmp_path / "analyst.json", analyst)
    evaluator = profiles.read_reference_json(intent["evaluator_projection_ref"], workspace=tmp_path)
    evaluator.update(exposure_ref=intent["exposure_ref"], reference_bundle_ref=intent["reference_bundle_ref"])
    intent["evaluator_projection_ref"] = json_source(tmp_path / "evaluator.json", evaluator)
    frozen = profiles.prepare_assessment_intent(tmp_path / "intent", intent, workspace=tmp_path, service=planner.methods)
    request.update(format="devbench.rna-scientific-trial/2", assessment_intent=frozen, assessment_plan=None, scientific_context=None)
    request["engineering"]["source_digest"] = trial_api.trial_enforcement_source_digest()
    request["engineering"]["runtime_digest"] = trial_api.trial_enforcement_runtime_digest()
    request["approvals"]["workflow_generation"] = None
    key = Ed25519PrivateKey.generate()
    signed_approval(request, key, tmp_path)
    runtime = {key: request["engineering"][key] for key in
        ("route", "source_digest", "runtime_digest", "backend_digest", "model", "platform")}
    authority = trial_api.AuthenticatedTrialAuthority(tmp_path / "trusted-authority.sqlite", workspace=tmp_path,
        issuer_keys={"fixture-issuer": key.public_key()}, runtime_verifier=lambda context: deepcopy(runtime) if context is not None
            and context.get("planner") is planner and context.get("selector") is selector and context.get("actor") == "participant" else {},
        clock=lambda: NOW)
    return request, planner, selector, branch, method, authority, intent


@pytest.mark.parametrize("attack", ["outer_source", "declarations_ref", "transformation_receipt_ref"])
def test_v2_generation_rejects_bridge_sources_outside_caller_workspace_before_read(tmp_path, monkeypatch, attack):
    from pathlib import Path
    from devbench.analysis.methods import MethodService
    from devbench.analysis import rna_execution_inputs as execution_api
    from devbench.study import evidence
    from tests import test_rna_scientific_trial as original_fixture_module
    from tests.test_rna_execution_inputs import ports
    assigned = tmp_path / "assigned-workspace"
    assigned.mkdir()
    # Exercise the supported equality boundary using a real service, with every
    # original data/publication/intent source still inside the assigned root.
    monkeypatch.setattr(original_fixture_module, "MethodService", lambda supplied: MethodService(assigned))
    request, planner, *_ = configured_v2(assigned)
    assert planner.methods.root == assigned
    bridge = execution_api.prepare_execution_inputs(request["dataset"]["binding"], ports(encoding="json"),
        artifact_service=planner.methods, output=assigned / "bridge")
    record = profiles.read_reference_json(bridge["record_ref"], workspace=assigned, service=planner.methods)
    if attack == "outer_source":
        reference = json_source(tmp_path / "sibling-input-record.json", record)
    else:
        previous = record[attack]
        document = profiles.read_reference_json(previous, workspace=assigned, service=planner.methods)
        record[attack] = json_source(tmp_path / ("sibling-" + attack + ".json"), document)
        if attack == "declarations_ref":
            for prerequisite in record["prerequisites"]:
                prerequisite["evidence_refs"] = [record[attack] if ref == previous else ref
                    for ref in prerequisite["evidence_refs"]]
        reference = json_source(assigned / "nested-sibling-input-record.json", record)
    request["execution_inputs_ref"] = reference
    approve(request, "workflow_generation")
    outside_reads = []
    original_read = evidence._read

    def observed_read(path, *args, **kwargs):
        if not Path(path).resolve().is_relative_to(assigned):
            outside_reads.append(str(path))
        return original_read(path, *args, **kwargs)

    for module in (execution_api, profiles, evidence):
        monkeypatch.setattr(module, "_read", observed_read)
    rejection = None
    try:
        trial_api.inspect_readiness(request, methods=planner.methods, workspace=assigned,
            purpose="workflow_generation", now=NOW)
    except ValueError as error:
        rejection = error
    assert outside_reads == [], "Bridge validation read a source outside the actual caller workspace"
    assert rejection is not None and "workspace" in str(rejection).lower()
    assert planner.methods.list_records("method_plan") == []
    assert planner.methods.list_records("run") == []


@pytest.mark.parametrize("binding_location", ["outside", "inside"])
def test_v2_finalization_rejects_legacy_planner_bridge_outside_caller_workspace_before_read(tmp_path, monkeypatch, binding_location):
    from pathlib import Path
    from devbench.analysis.methods import MethodService
    from devbench.analysis import rna_execution_inputs as execution_api
    from devbench.study import evidence
    from tests import test_rna_scientific_trial as original_fixture_module
    from tests.test_rna_execution_inputs import ports
    from tests.test_method_planner import Selector
    assigned = tmp_path / "assigned-workspace"
    assigned.mkdir()
    monkeypatch.setattr(original_fixture_module, "MethodService", lambda supplied: MethodService(assigned))
    request, planner, selector, branch, method, _, intent = configured_v2(assigned)
    method_ports = ports(encoding="json")
    method_ports["ports"]["matrix"] = method_ports["ports"].pop("counts")
    for port in method_ports["ports"].values():
        port["parameter"] = None
    bridge = execution_api.prepare_execution_inputs(request["dataset"]["binding"], method_ports,
        artifact_service=planner.methods, output=assigned / "bridge")
    record = profiles.read_reference_json(bridge["record_ref"], workspace=assigned, service=planner.methods)
    sibling_ref = (json_source(tmp_path / "legitimate-broader-session-inputs.json", record)
        if binding_location == "outside" else bridge["record_ref"])
    binding = request["dataset"]["binding"]
    original_ids = [binding[key] for key in ("matrix_artifact_id", "metadata_artifact_id")]
    # This is an ordinary direct planner with the legacy service-parent scope,
    # not a fabricated DB row or a mutation of the retained planner binding.
    state = planner.create(request["task"], {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": original_ids},
        actor="fixture", data_classification="synthetic", rna_input_contract=binding, rna_execution_inputs_ref=sibling_ref)
    assert "workspace" not in state["rna_execution_inputs"]
    recorded = Selector(*(answer["answer"] for answer in selector.answers))
    planner.generate_candidates(state["session_id"], recorded, actor="fixture")
    planner.resolve_capabilities(state["session_id"], recorded, [method["version_id"]], actor="fixture")
    drafts = planner.design_trials(state["session_id"], recorded, actor="fixture")
    declared = planner.declare_trial(state["session_id"], branch["branch_id"], drafts[0], actor="fixture")
    descriptor = {"planner_session_id": state["session_id"], "experiment_ids": [declared["id"]]}
    snapshot = trial_api.workflow_snapshot(planner, descriptor)
    wrapper = json_source(assigned / "standalone-workflow.json", {"format": "devbench.rna-assessment-workflow/2",
        "assessment_intent_ref": request["assessment_intent"], "assessment_rules_sha256": profiles.assessment_rules_digest(intent),
        "governed_workflow": snapshot, "governed_workflow_sha256": digest(snapshot)})
    outside_reads = []
    original_read = evidence._read

    def observed_read(path, *args, **kwargs):
        if not Path(path).resolve().is_relative_to(assigned):
            outside_reads.append(str(path))
        return original_read(path, *args, **kwargs)

    for module in (execution_api, profiles, evidence):
        monkeypatch.setattr(module, "_read", observed_read)
    rejection = None
    finalized = None
    try:
        finalized = profiles.finalize_assessment_plan(request["assessment_intent"], wrapper, bridge["record_ref"],
            output=assigned / "rejected-finalization", workspace=assigned, service=planner.methods)
    except ValueError as error:
        rejection = error
    assert outside_reads == [], "Finalization reloaded a retained planner bridge outside the current caller workspace"
    assert planner.methods.list_records("run") == []
    if binding_location == "outside":
        assert rejection is not None and "workspace" in str(rejection).lower()
        assert not (assigned / "rejected-finalization").exists()
    else:
        assert rejection is None and finalized["execution_inputs_ref"] == bridge["record_ref"]
        assert "workspace" not in planner.get(state["session_id"])["rna_execution_inputs"]


def test_v2_generation_retains_actual_intent_bound_governed_workflow(tmp_path):
    request, planner, selector, branch, method, authority, intent = configured_v2(tmp_path)
    workflow = generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
        branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="participant",
        authority=authority, now=NOW, output=tmp_path / "generated-workflow", scientific_workflow=RecordingScientificRuntime())
    assert "workflow_ref" in workflow
    wrapper = profiles.read_reference_json(workflow["workflow_ref"], workspace=tmp_path, service=planner.methods)
    assert wrapper["assessment_intent_ref"] == request["assessment_intent"]
    assert wrapper["assessment_rules_sha256"] == profiles.assessment_rules_digest(intent)
    assert wrapper["governed_workflow_sha256"] == workflow["sha256"] == digest(wrapper["governed_workflow"])
    assert not planner.get(workflow["planner_session_id"])["results"]
    assert all("expected_effects" not in str(context) and "reference.json" not in str(context) for context in selector.contexts)


def test_v2_nonexistent_intent_blocks_before_authority_or_selector(tmp_path):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    request.update(format="devbench.rna-scientific-trial/2",
        assessment_intent={"manifest_path": str(tmp_path / "missing/audit/manifest.json"), "root_hash": "d" * 64},
        assessment_plan=None, scientific_context=None)
    approve(request, "workflow_generation")
    authority = FakeAuthority()
    with pytest.raises((ValueError, FileNotFoundError), match="intent|reference|manifest|missing"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
            branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="participant",
            authority=authority, now=NOW, output=tmp_path / "generated-workflow", scientific_workflow=RecordingScientificRuntime())
    assert authority.calls == [] and selector.contexts == []


def test_v2_generation_rejects_actor_outside_authenticated_participant_role(tmp_path):
    request, planner, selector, branch, method, authority, _ = configured_v2(tmp_path)
    with pytest.raises(PermissionError, match="participant|actor"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
            branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="unrelated-caller",
            authority=authority, now=NOW, output=tmp_path / "generated-workflow", scientific_workflow=RecordingScientificRuntime())
    assert selector.contexts == []


def test_v2_execution_context_is_bound_without_fabricating_generation_ids(tmp_path):
    request = request_v2(tmp_path)
    generation = trial_binding(request, "workflow_generation")
    execution = trial_binding(request, "trial_execution")
    request["scientific_context"] = {"project_id": "1" * 32, "scientific_run_id": "2" * 32,
        "attempt_id": "3" * 32, "dataset_version_ids": ["4" * 64]}
    assert validate_trial_request(request) == request
    assert trial_binding(request, "workflow_generation") == generation
    assert trial_binding(request, "trial_execution") != execution


def test_v2_execution_requires_real_context_before_execution_approval(tmp_path):
    request = request_v2(tmp_path)
    approve(request, "trial_execution")
    with pytest.raises(PermissionError, match="scientific context|Scientific context"):
        validate_approval(request, "trial_execution", now=NOW)


def test_v2_scientific_correlation_context_stays_closed(tmp_path):
    request = request_v2(tmp_path)
    request["scientific_context"] = {"project_id": "1" * 32, "scientific_run_id": "2" * 32,
        "attempt_id": "3" * 32, "dataset_version_ids": ["4" * 64], "analysis_version_id": {"root_hash": "f" * 64}}
    with pytest.raises(ValueError, match="closed"):
        validate_trial_request(request)


def test_v2_readiness_and_preparation_reject_numeric_batch_complete_confounding(tmp_path):
    request, planner, selector, _, _, _, _ = configured_v2(tmp_path, numeric_confounded=True)
    readiness = trial_api.inspect_readiness(request, methods=planner.methods, workspace=tmp_path,
        planner=planner, purpose="workflow_generation", now=NOW)
    assert any("complete_confounding" in warning for warning in readiness["design_audit"]["blockers"])
    assert readiness["local_status"] == "blocked"
    retained = trial_api.prepare_trial(tmp_path / "preparation", request, methods=planner.methods,
        workspace=tmp_path, planner=planner, purpose="workflow_generation", now=NOW)
    assert retained["readiness"]["design_audit"] == readiness["design_audit"]
    assert retained["readiness"]["local_status"] == "blocked"
    assert selector.contexts == []


def test_v2_generation_cannot_adopt_reference_grounding_from_a_different_selected_publication(tmp_path):
    request, planner, selector, branch, method, authority, _ = configured_v2(tmp_path, wrong_publication=True)
    with pytest.raises(ValueError, match="publication|Publication"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
            branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="participant",
            authority=authority, now=NOW, output=tmp_path / "generated-workflow", scientific_workflow=RecordingScientificRuntime())
    assert selector.contexts == []
    assert planner.methods.list_records("run") == []
