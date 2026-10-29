"""C4 fakes: real artifact/planner integration without network/provider/data trial."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis.methods import MethodService
from devbench.analysis.method_planner import ScientificMethodPlanner
from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
from devbench.analysis.rna_publication_evidence import prepare_publication_evidence
from devbench.analysis.rna_publication_comparison import prepare_publication_comparison
from devbench.analysis.rna_scientific_trial_contract import trial_binding
from devbench.analysis.rna_scientific_trial import (
    inspect_readiness, prepare_trial, load_trial, generate_question_workflow,
    execute_trial, workflow_snapshot,
)
from tests.test_extensible_methods import custom, FixtureExecutor
from tests.test_method_planner import Selector, candidate, trial_spec

NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def source(path, text):
    path.write_text(text)
    body = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}


def approve(request, purpose):
    request["approvals"][purpose] = {"purpose": purpose, "binding_sha256": trial_binding(request, purpose),
        "approver_id": "operator", "evidence": request["dataset"]["access_record"],
        "expires_at": "2026-10-08T00:00:00Z"}


class FakeAuthority:
    """An injected test authority; it does not qualify a real backend or approval."""
    def __init__(self):
        self.calls = []
        self.consumed = set()

    def authorize(self, request, *, purpose, binding_sha256):
        assert trial_binding(request, purpose) == binding_sha256
        self.calls.append(purpose)

    def consume(self, request, *, purpose, binding_sha256):
        if binding_sha256 in self.consumed:
            raise PermissionError("one-use authority already consumed")
        self.consumed.add(binding_sha256)


def fixture(tmp_path, *, independent=True, repeated=False, confounded=False):
    methods = MethodService(tmp_path / "methods")
    declaration = {"format": "devbench.bulk-rna-input/1", "representation": "normalized_expression", "axes": ["gene", "sample"],
        "provenance": {"source": "Synthetic software fixture"}, "biological_independence": {"independent_units": independent},
        "metadata_roles": {"condition": "group", "biological_unit": "unit", "batch": "batch"}}
    matrix = source(tmp_path / "matrix.json", json.dumps({"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2", "s3", "s4"],
                                             "matrix": [[1, 2, 3, 4], [5, 6, 7, 8]]}))
    rows = [{"sample_id": "s" + str(i + 1), "group": "A" if i < 2 else "B", "unit": "u" + str(i if repeated and i == 1 else i + 1),
             "batch": ("b1" if i < 2 else "b2") if confounded else ("b1" if i % 2 == 0 else "b2")} for i in range(4)]
    metadata = source(tmp_path / "metadata.json", json.dumps({"samples": rows}))
    inputs = {port: methods.import_artifact(ref["path"], artifact_type="json", actor="fixture")["artifact_id"]
              for port, ref in (("matrix", matrix), ("metadata", metadata))}
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": inputs["matrix"],
               "metadata_artifact_id": inputs["metadata"], "declarations": declaration}
    question = "Describe the supplied synthetic expression data"
    qsource = source(tmp_path / "question.txt", question)
    access = source(tmp_path / "access.json", '{"scope":"offline synthetic fake control flow only"}')
    identity = {"doi": "10.1234/synthetic", "pmid": None, "pmcid": None, "title": "Synthetic Software Fixture", "version": "1"}
    url = "https://example.invalid/synthetic"
    paper = "10.1234/synthetic\nSynthetic Software Fixture\n1\nRetain every measurement.\nFixture output 4.0"
    psource = source(tmp_path / "paper.txt", paper)
    body = paper.encode()
    def locator(text):
        start = body.index(text.encode())
        return {"start_byte": start, "end_byte": start + len(text.encode()), "text": text, "location": "synthetic fixture"}
    exposure = {"prior_outcome_access": "unknown", "public_outcomes_accessible": "yes", "workflow_author_outcome_access": "unknown",
                "limitations": "Unblinded synthetic software fixture; no held-out scientific evidence"}
    publication = prepare_publication_evidence(tmp_path / "publication", {"identity": identity, "approved_urls": [url]},
        [{"source_id": "paper", "path": psource["path"], "sha256": psource["sha256"], "source_url": url, "identity": identity,
          "access": "extracted_text", "identity_locators": {key: locator(value) for key, value in identity.items() if value is not None},
          "identity_reviewed_by": "evaluator", "limitations": "Invented paper used only to test exact matching"}],
        [{"excerpt_id": "method_quote", "source_id": "paper", "locator": locator("Retain every measurement."), "visibility": "participant", "purpose": "methods"},
         {"excerpt_id": "output_quote", "source_id": "paper", "locator": locator("Fixture output 4.0"), "visibility": "evaluator", "purpose": "outcome"}],
        exposure, curator_id="evaluator", participant_id="participant", workspace=tmp_path, allow_evidence_read=True)
    request = {"format": "devbench.rna-scientific-trial/1", "trial_id": "offline-c4", "question": {"text": question, "source": qsource, "source_excerpt": question},
        "task": {"scientific_objective": question, "computational_task": "quality_control"}, "requirements": [],
        "dataset": {"binding": binding, "classification": "synthetic", "access_record": access,
                    "inspection_sha256": digest(inspect_registered_bulk_rna_input(methods, binding))},
        "objective": {"mode": "independent_method_selection", "estimand": "Describe measurements only", "claim_scope": "Synthetic software check", "intent": "descriptive"},
        "design": {"contrasts": [], "covariates": [], "chronological_field": None, "chronological_order": []},
        "publication": publication, "comparison": None, "workflow": None,
        "engineering": {"source_digest": "a" * 64, "runtime_digest": "b" * 64, "backend_digest": "c" * 64,
            "model": "recorded_fixture", "route": "offline_fixture", "platform": "test_only", "qualification_refs": [access], "hosted_trace_required": False,
            "baseline_decisions": [{"identity": name, "status": "not_dependent", "evidence": access} for name in ("T14-effect-definition-mismatch", "Harmony-missing-dependency")]},
        "privacy": {"full_data_local": True, "participant_fields": ["question", "bounded_bulk_inspection", "verified_method_excerpts"], "trace_fields": []},
        "allocation": {"total_tokens": 1000, "requests": 3, "memory_mb": 256, "seconds": 120, "storage_bytes": 5_000_000,
                       "max_runs": 1, "cost_basis": "No provider calls in fake", "cost_ceiling": None},
        "roles": {"implementation_owner": "implementer", "operator": "operator", "security_owner": "security", "reviewer": "reviewer", "evaluator": "evaluator", "participant": "participant"},
        "approvals": {"workflow_generation": None, "trial_execution": None}}
    approve(request, "workflow_generation")
    contract, code, lock = custom(tmp_path)
    contract["inputs"] = {port: {"artifact_type": "json", "required": True} for port in inputs}
    method = methods.register_method(contract, source=code, environment_lock=lock, actor="fixture")
    analysis = methods.create_analysis("C4 synthetic", actor="fixture")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="fixture")
    c = candidate()
    c["source"] = {"type": "primary_paper", "reference": "doi:10.1234/synthetic"}
    c["assumptions"][0]["field"] = "bulk_rna_input.measurements_finite"
    spec = trial_spec({"inputs": inputs})
    selector = Selector({"answer": {"candidates": [c]}, "citations": [{"candidate": "Unknown QC", "excerpt_id": "method_quote", "quote": "Retain every measurement."}]},
        {"answer": {"bindings": [{"candidate": "Unknown QC", "method_version_id": method["version_id"], "discovery": None}]}, "citations": []},
        {"answer": {"experiments": [spec]}, "citations": []})
    return request, ScientificMethodPlanner(methods), selector, branch, method, exposure


def generated(tmp_path, mode="independent_method_selection"):
    request, planner, selector, branch, method, exposure = fixture(tmp_path)
    request["objective"]["mode"] = mode
    approve(request, "workflow_generation")
    request["workflow"] = generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
        branch_id=branch["branch_id"], version_ids=[method["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    alignment = {"gene": "g1", "statistic": "fixture_scalar", "units": "fixture", "contrast": "none", "cohort": "synthetic", "preprocessing": "none", "hypothesis_family": "fixture"}
    protocol = {"format": "devbench.rna-publication-comparison/1", "mode": mode,
        "question_sha256": digest(request["question"]), "dataset_sha256": request["dataset"]["inspection_sha256"], "workflow_sha256": request["workflow"]["sha256"],
        "participant_id": "participant", "curator_id": "evaluator", "exposure_sha256": digest(exposure),
        "endpoints": [{"endpoint_id": "fixture_endpoint", "alignment": alignment, "absolute_tolerance": 0.0, "relative_tolerance": 0.0,
                       "tolerance_rationale": "Exact fixture arithmetic only", "required": True, "uncertainty_policy": "report_only", "direction_policy": "report_only"}],
        "missingness_policy": "report_and_abstain", "decision_rule": "all_required_endpoints", "abstention_policy": "Missing results abstain", "limitations": "Synthetic numerical control flow only"}
    request["comparison"] = prepare_publication_comparison(tmp_path / "comparison", protocol,
        [{"endpoint_id": "fixture_endpoint", "alignment": alignment, "excerpt_id": "output_quote", "value": 4.0, "value_text": "4.0", "uncertainty": None,
          "extraction_reviewed_by": "evaluator", "extraction_method": "Exact fixture token", "limitations": "Artificial value"}],
        request["publication"]["manifest_path"], request["publication"]["root_hash"], allow_evidence_read=True)
    approve(request, "trial_execution")
    return request, planner, selector


@pytest.mark.parametrize("mode", ["exact_reproduction", "independent_method_selection", "external_validation"])
def test_question_creates_real_frozen_governed_workflow_and_matched_protocol(tmp_path, mode):
    request, planner, selector = generated(tmp_path, mode)
    result = inspect_readiness(request, methods=planner.methods, workspace=tmp_path, planner=planner, now=NOW)
    assert result["local_status"] == "locally_consistent"
    assert result["live_admission"] == "unverified_requires_trusted_authority"
    assert result["scientific_review"] == "not_performed"
    assert all("output_quote" not in str(context) and "paper.txt" not in str(context) for context in selector.contexts)
    assert "registry" not in selector.contexts[0]
    assert selector.contexts[0]["publication_evidence"]["excerpts"][0]["text"] == "Retain every measurement."
    assert not planner.get(request["workflow"]["planner_session_id"])["results"]


def test_separate_execution_approval_and_trusted_authority_before_dispatch(tmp_path):
    request, planner, _ = generated(tmp_path)
    with pytest.raises(PermissionError, match="trusted"):
        execute_trial(request, planner=planner, workspace=tmp_path, actor="participant", executor=FixtureExecutor(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    request["approvals"]["trial_execution"] = None
    authority = FakeAuthority()
    with pytest.raises(PermissionError, match="Missing separate"):
        execute_trial(request, planner=planner, workspace=tmp_path, actor="participant", authority=authority, executor=FixtureExecutor(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert authority.calls == []
    assert planner.get(request["workflow"]["planner_session_id"])["results"] == []


def test_governed_fake_execution_preserves_outputs_and_does_not_claim_comparison(tmp_path):
    request, planner, _ = generated(tmp_path)
    receipt = execute_trial(request, planner=planner, workspace=tmp_path, actor="participant", authority=FakeAuthority(), executor=FixtureExecutor(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert receipt["status"] == "execution_completed"
    assert receipt["results"][0]["provenance"]["evidence_root"]
    assert receipt["comparison"] == receipt["scientific_review"] == "not_performed"
    assert receipt["clinical_release"] == "held"
    with pytest.raises(PermissionError, match="already attempted"):
        execute_trial(request, planner=planner, workspace=tmp_path, actor="participant", authority=FakeAuthority(), executor=FixtureExecutor(), now=NOW, scientific_workflow=RecordingScientificRuntime())


def test_fake_execution_failure_is_retained(tmp_path):
    class Fail:
        def execute(self, *args):
            raise RuntimeError("Deliberate fake failure")
    request, planner, _ = generated(tmp_path)
    receipt = execute_trial(request, planner=planner, workspace=tmp_path, actor="participant", authority=FakeAuthority(), executor=Fail(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert receipt["status"] == "completed_with_failures"
    assert receipt["results"][0]["failures"]
    assert receipt["results"][0]["metric_values"] == {}


@pytest.mark.parametrize("change", ["question", "dataset", "expiry", "native", "allocation", "baseline"])
def test_gate_changes_prevent_model_call(tmp_path, change):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    if change == "question":
        request["question"]["source_excerpt"] = "Fabricated user statement"
    elif change == "dataset":
        request["dataset"]["inspection_sha256"] = "d" * 64
    elif change == "expiry":
        request["approvals"]["workflow_generation"]["expires_at"] = "2026-10-06T00:00:00Z"
    elif change == "native":
        request["engineering"]["route"] = "native"
        approve(request, "workflow_generation")
    elif change == "allocation":
        request["allocation"]["seconds"] = 0
    else:
        request["engineering"]["baseline_decisions"][0]["status"] = "blocked"
        approve(request, "workflow_generation")
    with pytest.raises((PermissionError, ValueError)):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector, branch_id=branch["branch_id"],
                                  version_ids=[method["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert selector.contexts == []


@pytest.mark.parametrize("bad_quote,bad_excerpt", [("Invented methods", "method_quote"), ("Fixture output 4.0", "output_quote")])
def test_hallucinated_or_held_out_quote_is_rejected_before_trial_declaration(tmp_path, bad_quote, bad_excerpt):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    selector.answers[0]["citations"][0].update(quote=bad_quote, excerpt_id=bad_excerpt)
    with pytest.raises(ValueError, match="citation"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector, branch_id=branch["branch_id"],
                                  version_ids=[method["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert len(selector.contexts) == 1


@pytest.mark.parametrize("options,blocker", [({"independent": False}, "biological_independence_declaration_missing"),
    ({"repeated": True}, "independent_biological_units_unknown_or_repeated"),
    ({"confounded": True}, "design_rank_deficient_or_complete_confounding")])
def test_inferential_invalid_units_or_design_fail_before_dispatch(tmp_path, options, blocker):
    request, planner, selector, branch, method, _ = fixture(tmp_path, **options)
    request["objective"]["intent"] = "infer"
    request["design"].update(contrasts=[{"field": "group", "reference": "A", "target": "B"}], covariates=["batch"])
    approve(request, "workflow_generation")
    result = inspect_readiness(request, methods=planner.methods, workspace=tmp_path, purpose="workflow_generation", now=NOW)
    assert blocker in result["blockers"]
    with pytest.raises(PermissionError):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector, branch_id=branch["branch_id"],
                                  version_ids=[method["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert selector.contexts == []


def test_sealed_preparation_revalidates_data_and_question_source(tmp_path):
    request, planner, _ = generated(tmp_path)
    descriptor = prepare_trial(tmp_path / "prepared", request, methods=planner.methods, workspace=tmp_path, planner=planner, now=NOW)
    assert "--validate" in descriptor["invocation"]
    assert load_trial(descriptor["manifest_path"], descriptor["root_hash"], methods=planner.methods, workspace=tmp_path, planner=planner, now=NOW)["readiness"]["local_status"] == "locally_consistent"
    from pathlib import Path
    Path(request["question"]["source"]["path"]).write_text("Changed source")
    with pytest.raises(ValueError, match="byte count|digest"):
        load_trial(descriptor["manifest_path"], descriptor["root_hash"], methods=planner.methods, workspace=tmp_path, planner=planner, now=NOW)


def test_wrong_publication_identity_blocks_workflow_generation(tmp_path):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    selector.answers[0]["answer"]["candidates"][0]["source"]["reference"] = "doi:10.1234/wrong"
    with pytest.raises(ValueError, match="matched publication"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector, branch_id=branch["branch_id"],
                                  version_ids=[method["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())


def test_dropping_declared_batch_does_not_identify_confounded_inference(tmp_path):
    request, planner, *_ = fixture(tmp_path, confounded=True)
    request["objective"]["intent"] = "infer"
    request["design"]["contrasts"] = [{"field": "group", "reference": "A", "target": "B"}]
    assert request["design"]["covariates"] == []
    approve(request, "workflow_generation")
    readiness = inspect_readiness(request, methods=planner.methods, workspace=tmp_path, purpose="workflow_generation", now=NOW)
    assert "design_rank_deficient_or_complete_confounding" in readiness["blockers"]


def test_multi_candidate_aggregate_timeout_rejected_before_declarations(tmp_path):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    contract = deepcopy(planner.methods.method(method["version_id"])["contract"])
    contract.update(method_id="fixture.alternative", name="Alternative QC")
    alternate = planner.methods.register_method(contract, source=tmp_path / "source.py", environment_lock=tmp_path / "environment.lock", actor="fixture")
    c = deepcopy(selector.answers[0]["answer"]["candidates"][0])
    c["name"] = "Alternative QC"
    selector.answers[0]["answer"]["candidates"].append(c)
    selector.answers[0]["citations"].append({"candidate": "Alternative QC", "excerpt_id": "method_quote", "quote": "Retain every measurement."})
    selector.answers[1]["answer"]["bindings"].append({"candidate": "Alternative QC", "method_version_id": alternate["version_id"], "discovery": None})
    spec = selector.answers[2]["answer"]["experiments"][0]
    spec["candidates"].append("Alternative QC")
    spec["inputs"]["Alternative QC"] = deepcopy(spec["inputs"]["Unknown QC"])
    spec["parameters"]["Alternative QC"] = deepcopy(spec["parameters"]["Unknown QC"])
    spec["resource_limits"]["max_runs"] = 2
    request["allocation"].update(max_runs=2, seconds=30)
    approve(request, "workflow_generation")
    with pytest.raises(PermissionError, match="allocation"):
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector, branch_id=branch["branch_id"],
                                  version_ids=[method["version_id"], alternate["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert len(selector.contexts) == 3


def test_grounded_selector_matches_canonical_doi_without_rewriting_source_bytes(tmp_path):
    from devbench.analysis.rna_scientific_trial import GroundedSelector
    answer = candidate()
    answer["source"] = {"type": "primary_paper", "reference": "doi:10.1234/paper"}
    selector = Selector({"answer": {"candidates": [answer]}, "citations": [
        {"candidate": "Unknown QC", "excerpt_id": "method", "quote": "Retain every measurement."}]})
    context = {"publication": {"doi": "https://doi.org/10.1234/PAPER", "pmid": None, "pmcid": None,
        "title": "Exact paper title", "version": "1"}, "excerpts": [{"excerpt_id": "method", "purpose": "methods", "text": "Retain every measurement."}]}
    result = GroundedSelector(selector, context).select({"decision_kind": "method_scientific_planning"}, tmp_path)
    assert result.action["candidates"][0]["source"]["reference"] == "doi:10.1234/paper"
    assert context["publication"]["doi"] == "https://doi.org/10.1234/PAPER"


def test_rejected_citation_response_remains_in_existing_artifact_authority(tmp_path):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    selector.answers[0]["citations"][0]["quote"] = "Invented unsupported statement"
    original = deepcopy(selector.answers[0])
    with pytest.raises(ValueError, match="retained_response_artifact=") as failure:
        generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector, branch_id=branch["branch_id"],
                                  version_ids=[method["version_id"]], actor="participant", authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    artifact_id = str(failure.value).split("retained_response_artifact=")[-1]
    retained = json.loads(planner.methods.artifact_path(artifact_id).read_text())
    assert retained == original
    with planner.methods._db() as db:
        sessions = [row[0] for row in db.execute("SELECT id FROM records WHERE kind='method_plan'")]
    assert len(sessions) == 1
    state = planner.get(sessions[0])
    assert state["selection_history"][0]["status"] == "failed"
    assert artifact_id in state["selection_history"][0]["error"]
    assert state["experiments"] == state["results"] == []
