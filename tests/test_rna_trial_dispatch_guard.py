"""Preventive candidate guard refusal is retained and terminates dispatch."""
from tests.support.workflow_science_fixtures import RecordingScientificRuntime
from copy import deepcopy
import inspect

from devbench.analysis.rna_scientific_trial import generate_question_workflow
from devbench.analysis import rna_scientific_trial as trial_api
from devbench.analysis import rna_publication_profiles as profiles
from tests.test_rna_comparison_outputs import prepared
from tests.test_extensible_methods import FixtureExecutor, custom
from tests.test_rna_scientific_trial import NOW, FakeAuthority, approve, fixture


def declared_two_candidates(tmp_path):
    request, planner, selector, branch, method, _ = fixture(tmp_path)
    request["allocation"]["max_runs"] = 2
    approve(request, "workflow_generation")
    first = selector.answers[0]
    candidate = deepcopy(first["answer"]["candidates"][0])
    candidate["name"] = "Other QC"
    first["answer"]["candidates"].append(candidate)
    first["citations"].append({**first["citations"][0], "candidate": "Other QC"})
    other_contract, code, lock = custom(tmp_path)
    other_contract.update(name="Other QC", method_id="custom.other")
    other_contract["inputs"] = deepcopy(planner.methods.method(method["version_id"])["contract"]["inputs"])
    other_method = planner.methods.register_method(other_contract, source=code, environment_lock=lock, actor="fixture")
    selector.answers[1]["answer"]["bindings"].append({"candidate": "Other QC", "method_version_id": other_method["version_id"], "discovery": None})
    spec = selector.answers[2]["answer"]["experiments"][0]
    spec["candidates"].append("Other QC")
    spec["inputs"]["Other QC"] = deepcopy(spec["inputs"]["Unknown QC"])
    spec["parameters"]["Other QC"] = deepcopy(spec["parameters"]["Unknown QC"])
    spec["resource_limits"].update(max_runs=2, timeout_seconds=60)
    workflow = generate_question_workflow(request, planner=planner, workspace=tmp_path, selector=selector,
        branch_id=branch["branch_id"], version_ids=[method["version_id"], other_method["version_id"]], actor="participant",
        authority=FakeAuthority(), now=NOW, scientific_workflow=RecordingScientificRuntime())
    return planner, workflow


def test_candidate_guard_refusal_retains_evidence_and_stops_later_candidates(tmp_path):
    planner, workflow = declared_two_candidates(tmp_path)
    assert "before_dispatch" in inspect.signature(planner.run_trial).parameters
    seen = []
    def deny(descriptor):
        seen.append(descriptor)
        raise PermissionError("Synthetic authenticated allocation exhausted")
    results = planner.run_trial(workflow["planner_session_id"], workflow["experiment_ids"][0], actor="participant",
        allow_data_execution=True, executor=FixtureExecutor(), before_dispatch=deny)
    assert len(seen) == len(results) == 1
    assert results[0]["status"] == "failed"
    assert results[0]["warnings"] == ["trial_admission_denied_no_dispatch"]
    assert results[0]["metric_values"] == {} and results[0]["artifacts"] == []
    assert "run_id" not in results[0]["provenance"]
    assert planner.get(workflow["planner_session_id"])["results"] == results
    assert planner.export(workflow["planner_session_id"])["results"] == results
    assert "allocation exhausted" in results[0]["failures"][0]


def test_candidate_guard_gets_exact_copy_immediately_before_governed_run(tmp_path):
    planner, workflow = declared_two_candidates(tmp_path)
    assert "before_dispatch" in inspect.signature(planner.run_trial).parameters
    seen = []
    def observe(descriptor):
        seen.append(deepcopy(descriptor))
        descriptor["parameters"]["threshold"] = 999
        descriptor["input_artifact_ids"].clear()
    results = planner.run_trial(workflow["planner_session_id"], workflow["experiment_ids"][0], actor="participant",
        allow_data_execution=True, executor=FixtureExecutor(), before_dispatch=observe)
    assert len(results) == len(seen) == 2
    assert all(row["status"] == "success" for row in results)
    assert all(row["provenance"]["parameters"]["threshold"] == 4 for row in results)
    assert all(row["provenance"]["input_artifact_ids"] for row in results)
    assert all(set(item) == {"session_id", "experiment_id", "candidate", "step_id", "method_version_id",
        "input_artifact_ids", "parameters", "declaration_sha256", "resource_limits"} for item in seen)


def test_guard_preserves_success_before_second_candidate_refusal(tmp_path):
    planner, workflow = declared_two_candidates(tmp_path)
    assert "before_dispatch" in inspect.signature(planner.run_trial).parameters
    seen = []
    def deny_second(descriptor):
        seen.append(descriptor["candidate"])
        if len(seen) == 2:
            raise PermissionError("Synthetic second dispatch authorization expired")
    results = planner.run_trial(workflow["planner_session_id"], workflow["experiment_ids"][0], actor="participant",
        allow_data_execution=True, executor=FixtureExecutor(), before_dispatch=deny_second)
    assert len(results) == 2 and results[0]["status"] == "success"
    assert results[0]["provenance"]["run_id"] and results[0]["artifacts"]
    assert results[1]["status"] == "failed" and results[1]["warnings"] == ["trial_admission_denied_no_dispatch"]
    assert "run_id" not in results[1]["provenance"] and results[1]["artifacts"] == []
    assert planner.export(workflow["planner_session_id"])["results"] == results


def captured_execution_dispatch_id(tmp_path, monkeypatch, experiment_id, candidate):
    """Inspect the real guard key after isolated unit admission; never dispatch a worker."""
    request, *_ = fixture(tmp_path / "request")
    service, step, sealed_plan = prepared(tmp_path / "retained")
    plan = profiles.load_assessment_plan(sealed_plan, workspace=tmp_path, service=service)
    intent = profiles.load_assessment_intent(plan["intent_ref"], workspace=tmp_path, service=service)
    dataset = profiles.read_reference_json(intent["dataset_ref"], workspace=tmp_path, service=service)
    request.update(format="devbench.rna-scientific-trial/2", assessment_intent=plan["intent_ref"],
        assessment_plan=sealed_plan, scientific_context={"project_id": "1" * 32, "scientific_run_id": "2" * 32,
            "attempt_id": "3" * 32, "dataset_version_ids": ["4" * 64]},
        dataset={key: value for key, value in dataset.items() if key != "format"},
        workflow={"planner_session_id": "s" * 64, "experiment_ids": [experiment_id], "sha256": "a" * 64,
            "workflow_ref": plan["workflow_ref"]})
    request["approvals"] = {"workflow_generation": None, "trial_execution": None}
    snapshot = {"experiments": [{"id": experiment_id, "candidates": [candidate],
        "step_ids": {candidate: step["step_id"]}}]}
    readiness = {"binding_sha256": "b" * 64}
    # These two seams deliberately bypass qualification only in this key unit test.
    monkeypatch.setattr(trial_api, "inspect_readiness", lambda *args, **kwargs: deepcopy(readiness))
    monkeypatch.setattr(trial_api, "_admit", lambda *args, **kwargs: None)
    monkeypatch.setattr(trial_api, "workflow_snapshot", lambda *args, **kwargs: deepcopy(snapshot))

    class RecordingAuthority(trial_api.AuthenticatedTrialAuthority):
        def __init__(self):
            self.identifiers = []
            self.clock = lambda: NOW
        def bind_runtime_context(self, context):
            pass
        def reserve_dispatch(self, request, *, purpose, binding_sha256, dispatch_id, allocation):
            assert purpose == "trial_execution"
            if len(dispatch_id) > 256:
                raise ValueError("Dispatch identity exceeds the authority's 256-character bound")
            self.identifiers.append(dispatch_id)

    class KeyOnlyPlanner:
        methods = service
        def get(self, session_id):
            return {"context": {"dataset_summary": {"observed": {}}},
                    "experiments": [{"id": experiment_id, "status": "declared"}]}
        def run_trial(self, session_id, identity, *, before_dispatch, **kwargs):
            before_dispatch({"session_id": session_id, "experiment_id": identity, "candidate": candidate,
                "step_id": step["step_id"], "method_version_id": step["method_version_id"],
                "input_artifact_ids": deepcopy(step["inputs"]), "parameters": deepcopy(step["parameters"]),
                "declaration_sha256": "c" * 64, "resource_limits": {}})
            # Stop the outer controller without producing a fake run or result artifact.
            return [{"status": "failed", "warnings": ["trial_admission_denied_no_dispatch"], "provenance": {}}]

    authority = RecordingAuthority()
    receipt = trial_api.execute_trial(request, planner=KeyOnlyPlanner(), workspace=tmp_path,
        actor="participant", authority=authority, now=NOW, scientific_workflow=RecordingScientificRuntime())
    assert receipt["status"] == "admission_denied" and receipt["output_bundle_ref"] is None
    assert len(authority.identifiers) == 1
    assert service.list_records("run") == []
    return authority.identifiers[0]


def test_execution_dispatch_identity_accepts_maximum_legal_labels(tmp_path, monkeypatch):
    identity = captured_execution_dispatch_id(tmp_path, monkeypatch, "e" * 200, "c" * 200)
    assert len(identity) <= 256


def test_execution_dispatch_identity_distinguishes_colon_pairs_and_is_stable(tmp_path, monkeypatch):
    left = captured_execution_dispatch_id(tmp_path / "left", monkeypatch, "e:a", "b")
    right = captured_execution_dispatch_id(tmp_path / "right", monkeypatch, "e", "a:b")
    repeated = captured_execution_dispatch_id(tmp_path / "repeated", monkeypatch, "e:a", "b")
    assert left != right
    assert left == repeated
