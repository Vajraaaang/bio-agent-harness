"""Offline question-to-proposal fixtures; no live model or semantic qualification."""
import copy
import json

import pytest

from tests.test_analysis_episodes import inputs, run_contrast, workspace


def api():
    from devbench.analysis.episode_planning import propose_analysis, proposal_schema
    return propose_analysis, proposal_schema


class Selector:
    def __init__(self, decision=None, error=None):
        self.decision = decision or {"kind": "propose", "workflow": "bulk_mean_contrast",
            "reference_condition": "A", "comparison_condition": "B",
            "rationale": "Compare declared independent groups on the supplied scale."}
        self.error = error
        self.calls = []

    def configuration(self):
        return {"backend": "offline_planning_fixture"}

    def select(self, context, output, **kwargs):
        from devbench.analysis.episode_agent import Selection
        assert kwargs["allow_model_execution"] is False
        self.calls.append(copy.deepcopy(context))
        if self.error:
            raise self.error
        return Selection(copy.deepcopy(self.decision), {"backend": "offline_planning_fixture",
            "model_sessions_attempted": 0, "tokens": None, "cost_usd": None,
            "actual_model": None, "model_identity_verified": False})


def dataset(root, *, cellular=False, continuous=True):
    task, paths = inputs(root, cellular=cellular, continuous=continuous)
    task.pop("questions")
    return {"assay": "scrna" if cellular else "bulk", "public_task": task,
            "inputs": {name: str(path) for name, path in paths.items()}}


@pytest.mark.parametrize("permission", [False, None, 0, 1, "true"])
def test_planning_denies_before_reading_inputs_or_selector(permission):
    propose, _ = api()
    with pytest.raises(PermissionError):
        propose(None, question=None, dataset=None, selector=None, allow_data_read=permission)


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_question_becomes_unreviewed_proposal_then_existing_review_and_execution(workspace, cellular, continuous):
    propose, _ = api()
    from devbench.analysis.episode_proposals import record_review, begin_reviewed_episode
    from devbench.analysis.episodes import verify_episode
    from devharness_core.provenance import verify_execution

    data = dataset(workspace, cellular=cellular, continuous=continuous)
    original = {name: __import__("pathlib").Path(path).read_bytes() for name, path in data["inputs"].items()}
    selector = Selector()
    if cellular:
        selector.decision["workflow"] = "cellular_state_contrast"
    result = propose(workspace / "planning", question="How does B differ from A?",
        dataset=data, selector=selector, allow_data_read=True)
    assert result["status"] == "proposal_ready_for_review"
    assert result["scientific_appropriateness"] == "not_established"
    assert result["analysis_executed"] is False
    assert len(selector.calls) == 1
    context = selector.calls[0]
    assert context["decision_kind"] == "analysis_proposal"
    assert context["observed"]["gene_count"] == (14 if cellular else 12)
    assert context["observed"]["biological_units"]["biological_unit_count"] == 8
    assert context["declarations"]["measurement"] == ("umi_counts" if cellular else "continuous" if continuous else "raw_counts")
    assert "source_sha256" not in json.dumps(context)
    assert str(workspace) not in json.dumps(context)
    proposal = result["proposal_receipt"]
    record = json.loads((workspace / "planning/proposal/proposal.json").read_text())
    assert record["status"] == "awaiting_operator_review"
    assert record["configuration"]["public_task"]["context"]["requested_condition_contrast"] == {
        "numerator": "B", "denominator": "A"}
    assert record["authoring"]["scope"] == "unreviewed_agent_suggestion"
    review = record_review(workspace / "review", proposal["manifest_path"], expected_root=proposal["root_hash"],
        reviewer="fixture-operator", decision="approve", rationale="Software fixture; not independent scientific review.",
        confirmations={key: True for key in ("question_and_contrast", "input_provenance", "biological_units_and_design", "limitations")},
        allow_review=True)
    episode = begin_reviewed_episode(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    run_contrast(episode, cellular=cellular)
    assert episode.step({"kind": "finish"})["outcome"] == "completed"
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]
    assert verify_execution(result["receipt"]["manifest_path"], result["receipt"]["root_hash"])["valid"]
    assert original == {name: __import__("pathlib").Path(path).read_bytes() for name, path in data["inputs"].items()}


@pytest.mark.parametrize("decision,status", [
    ({"kind": "request_information", "message": "Which condition should be the reference?"}, "awaiting_information"),
    ({"kind": "refuse", "reason": "The requested causal claim is unsupported."}, "refusal"),
])
def test_information_and_refusal_are_retained_without_inventing_a_proposal(workspace, decision, status):
    propose, _ = api()
    selector = Selector(decision)
    result = propose(workspace / "planning", question="Explain the effect", dataset=dataset(workspace),
                     selector=selector, allow_data_read=True)
    assert result["status"] == status
    assert result["decision"] == decision
    assert result["proposal_receipt"] is None
    assert result["refusal_correctness"] is None
    assert not (workspace / "planning/proposal").exists()


@pytest.mark.parametrize("change", [
    {"comparison_condition": "not_observed"}, {"workflow": "unqualified_method"},
    {"minimum_units": 1}, {"kind": "finish"}, {"reference_condition": "B"},
])
def test_invalid_model_proposal_is_preserved_and_never_reviewable(workspace, change):
    propose, _ = api()
    selector = Selector()
    selector.decision.update(change)
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace),
                     selector=selector, allow_data_read=True)
    assert result["status"] == "invalid_proposal"
    assert result["proposal_receipt"] is None
    assert (workspace / "planning/selection.json").is_file()
    assert result["receipt"] is not None


def test_scientific_and_resource_options_are_not_actor_controlled(workspace):
    propose, _ = api()
    data = dataset(workspace)
    selector = Selector()
    result = propose(workspace / "planning", question="B minus A", dataset=data, selector=selector,
        fixed_options={"mean_condition_analysis": {"minimum_units": 4}}, allow_data_read=True)
    assert result["status"] == "proposal_ready_for_review"
    config = json.loads((workspace / "planning/proposal/proposal.json").read_text())["configuration"]
    assert config["fixed_options"]["mean_condition_analysis"]["minimum_units"] == 4


def test_unverified_units_remove_inferential_workflow_from_public_menu(workspace):
    propose, _ = api()
    data = dataset(workspace)
    data["public_task"]["unit_hierarchy"] = {"population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id", "identity_status": "unverified"}
    selector = Selector({"kind": "request_information", "message": "Please supply donor identity provenance."})
    result = propose(workspace / "planning", question="B minus A", dataset=data, selector=selector, allow_data_read=True)
    assert result["status"] == "awaiting_information"
    assert selector.calls[0]["allowed_workflows"] == []
    assert selector.calls[0]["observed"]["biological_units"]["biological_unit_count"] is None


def test_actor_cannot_turn_unknown_units_into_an_inferential_proposal(workspace):
    propose, _ = api()
    data = dataset(workspace)
    data["public_task"]["context"]["biological_units"] = {"population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id", "identity_status": "unverified"}
    result = propose(workspace / "planning", question="B minus A", dataset=data, selector=Selector(), allow_data_read=True)
    assert result["status"] == "invalid_proposal" and result["proposal_receipt"] is None


def test_no_hidden_evaluation_fields_or_pre_authored_question_targets(workspace):
    propose, _ = api()
    data = dataset(workspace)
    data["public_task"]["questions"] = [{"target_id": "mean_contrast"}]
    with pytest.raises(ValueError, match="question targets"):
        propose(workspace / "planning", question="B minus A", dataset=data, selector=Selector(), allow_data_read=True)
    data["public_task"].pop("questions")
    data["public_task"]["evaluation_feedback"] = "do not expose"
    with pytest.raises(ValueError):
        propose(workspace / "planning", question="B minus A", dataset=data, selector=Selector(), allow_data_read=True)


def test_live_selector_requires_explicit_permission_and_public_data_scope_before_reads(workspace):
    propose, _ = api()
    from devbench.analysis.episode_codex import CodexActionSelector
    selector = CodexActionSelector(requested_model="not-launched", workspace_parent=workspace / "actors")
    for flags in ({}, {"allow_model_execution": True}):
        with pytest.raises(PermissionError):
            propose(workspace / "planning", question="B minus A", dataset=None, selector=selector,
                    allow_data_read=True, **flags)
    assert not (workspace / "actors").exists()


def test_input_mutation_during_selection_blocks_proposal(workspace):
    propose, _ = api()
    data = dataset(workspace)
    class MutatingSelector(Selector):
        def select(self, *args, **kwargs):
            from pathlib import Path
            path = Path(data["inputs"]["metadata"])
            path.write_bytes(path.read_bytes() + b"\n")
            return super().select(*args, **kwargs)
    result = propose(workspace / "planning", question="B minus A", dataset=data,
                     selector=MutatingSelector(), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    assert "input" in result["reason"]
    assert result["proposal_receipt"] is None


def test_interrupted_selector_seals_failure_and_preserves_intake(workspace):
    propose, _ = api()
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace),
        selector=Selector(error=InterruptedError("fixture interrupted")), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    assert result["error_type"] == "InterruptedError"
    assert (workspace / "planning/context.json").is_file()
    assert result["receipt"] is not None


def test_oversized_public_context_never_reaches_selector(workspace):
    propose, _ = api()
    from devbench.analysis.episode_agent import AgentLimits
    selector = Selector()
    result = propose(workspace / "planning", question="Long question " * 200, dataset=dataset(workspace),
        selector=selector, agent_limits=AgentLimits(max_context_bytes=1024), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    assert "context_budget" in result["reason"]
    assert not selector.calls


def test_output_cannot_overlap_an_input_before_any_write(workspace):
    propose, _ = api()
    data = dataset(workspace)
    from pathlib import Path
    path = Path(data["inputs"]["expression"])
    before = path.read_bytes()
    with pytest.raises(ValueError, match="disjoint"):
        propose(path, question="B minus A", dataset=data, selector=Selector(), allow_data_read=True)
    assert path.read_bytes() == before


def test_evidence_capture_failure_cannot_leave_a_successful_result_record(workspace):
    propose, _ = api()
    from pathlib import Path
    from devharness_core.provenance import verify_execution
    data = dataset(workspace)
    class InvalidEvidence(Selector):
        def select(self, context, output, **kwargs):
            (output / "outside-link").symlink_to(Path(data["inputs"]["metadata"]))
            return super().select(context, output, **kwargs)
    result = propose(workspace / "planning", question="B minus A", dataset=data,
                     selector=InvalidEvidence(), allow_data_read=True)
    saved = json.loads((workspace / "planning/result.json").read_text())
    assert saved["status"] == result["status"] == "infrastructure_failure"
    assert saved["proposal_receipt"] is None
    assert saved["evidence_capture_complete"] is False
    assert not (workspace / "planning/proposal").exists()
    assert verify_execution(result["receipt"]["manifest_path"], result["receipt"]["root_hash"])["valid"]


def test_input_change_during_proposal_publication_is_not_promoted(workspace, monkeypatch):
    from devbench.analysis import episode_planning
    from pathlib import Path
    data = dataset(workspace)
    original = episode_planning.prepare_proposal
    def changed(*args, **kwargs):
        path = Path(data["inputs"]["metadata"])
        path.write_bytes(path.read_bytes() + b"\n")
        return original(*args, **kwargs)
    monkeypatch.setattr(episode_planning, "prepare_proposal", changed)
    result = episode_planning.propose_analysis(workspace / "planning", question="B minus A", dataset=data,
                                               selector=Selector(), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    assert result["proposal_receipt"] is None
    assert not (workspace / "planning/proposal").exists()


def test_cli_exposes_offline_question_to_proposal_path(workspace):
    import subprocess
    import sys
    request = {"question": "How does B differ from A?", "dataset": dataset(workspace)}
    (workspace / "request.json").write_text(json.dumps(request))
    (workspace / "answer.json").write_text(json.dumps(Selector().decision))
    command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--plan-request", str(workspace / "request.json"),
        "--plan-answer", str(workspace / "answer.json"), "--output", str(workspace / "planning"), "--allow-data-execution"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "proposal_ready_for_review"
    assert result["model_sessions_attempted"] == 0 and result["analysis_executed"] is False


def test_planning_cli_requires_model_scope_before_request_read(workspace):
    import subprocess
    import sys
    command = [sys.executable, "-m", "devbench.analysis.episode_cli", "--plan-request", str(workspace / "absent.json"),
        "--codex-model", "not-launched", "--agent-workspaces", str(workspace / "actors"),
        "--output", str(workspace / "planning"), "--allow-data-execution", "--allow-model-execution"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert completed.returncode == 2
    assert "model-data-scope" in completed.stderr
    assert not (workspace / "planning").exists()


def test_unreadable_selector_evidence_cannot_publish_a_proposal(workspace):
    propose, _ = api()
    class BrokenEvidence(Selector):
        def select(self, context, output, **kwargs):
            (output / "evidence.json").write_text("[]")
            return super().select(context, output, **kwargs)
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace),
                     selector=BrokenEvidence(), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    assert result["proposal_receipt"] is None
    assert not (workspace / "planning/proposal").exists()


def test_operator_approval_checks_authoring_records_again(workspace):
    propose, _ = api()
    from devbench.analysis.episode_proposals import record_review
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace),
                     selector=Selector(), allow_data_read=True)
    proposal = result["proposal_receipt"]
    context = workspace / "planning/context.json"
    context.write_bytes(context.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="authoring record changed"):
        record_review(workspace / "review", proposal["manifest_path"], expected_root=proposal["root_hash"],
            reviewer="fixture", decision="approve", rationale="Must fail on changed authoring evidence",
            confirmations={key: True for key in ("question_and_contrast", "input_provenance", "biological_units_and_design", "limitations")},
            allow_review=True)
    assert not (workspace / "review").exists()


def test_reviewed_episode_replay_retains_authoring_reference_without_original_logs(workspace):
    propose, _ = api()
    from devbench.analysis.episode_proposals import record_review, begin_reviewed_episode
    from devbench.analysis.episodes import verify_episode
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace),
                     selector=Selector(), allow_data_read=True)
    proposal = result["proposal_receipt"]
    review = record_review(workspace / "review", proposal["manifest_path"], expected_root=proposal["root_hash"],
        reviewer="fixture", decision="approve", rationale="Replay fixture, not scientific review",
        confirmations={key: True for key in ("question_and_contrast", "input_provenance", "biological_units_and_design", "limitations")},
        allow_review=True)
    episode = begin_reviewed_episode(workspace / "episode", proposal["manifest_path"], review["manifest_path"],
        expected_proposal_root=proposal["root_hash"], expected_review_root=review["root_hash"], allow_data_execution=True)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    (workspace / "planning").rename(workspace / "retained-authoring-archive")
    replay = episode.replay(workspace / "replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "completed"
    assert verify_episode(replay.root, replay.receipt["root_hash"])["valid"]
    review_record = json.loads((replay.root / "plan-review/review/review.json").read_text())
    assert review_record["authoring_root_verified"] == result["authoring_receipt"]["root_hash"]


def test_real_inspection_timeout_stops_before_selecting(workspace):
    propose, _ = api()
    from devbench.analysis.episodes import EpisodeLimits
    selector = Selector()
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace), selector=selector,
        episode_limits=EpisodeLimits(stage_timeout_seconds=.001), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    assert not selector.calls and result["proposal_receipt"] is None
    supervision = json.loads((workspace / "planning/inspection-supervision.json").read_text())
    assert supervision["supervisor_status"] == "failed"


def test_authoring_manifest_swap_cannot_promote_a_failed_run(workspace, monkeypatch):
    propose, _ = api()
    from pathlib import Path
    import hashlib
    from devbench.analysis import episode_proposals as records
    from devbench.analysis.checkpoints import digest
    class BrokenEvidence(Selector):
        def select(self, context, output, **kwargs):
            (output / "evidence.json").write_text("[]")
            return super().select(context, output, **kwargs)
    result = propose(workspace / "planning", question="B minus A", dataset=dataset(workspace),
                     selector=BrokenEvidence(), allow_data_read=True)
    assert result["status"] == "infrastructure_failure"
    root = workspace / "planning"
    read = lambda name: json.loads((root / name).read_text())
    selection = read("selection.json")
    authoring = {"scope": "unreviewed_agent_suggestion", "manifest_path": result["receipt"]["manifest_path"],
        "root_hash": result["receipt"]["root_hash"], "request_sha256": digest(read("request.json")),
        "context_sha256": digest(read("context.json")), "decision_sha256": digest(selection["decision"]),
        "selector_evidence_sha256": digest(selection["evidence"])}
    original = records.verify_execution
    original_read = records._read
    substituted_reads = {}
    def swap(path, expected):
        verified = original(path, expected)
        if Path(path) == Path(authoring["manifest_path"]):
            assert verified["valid"]
            substituted = read("result.json")
            substituted.update(status="proposal_selected", evidence_capture_complete=True)
            content = json.dumps(substituted).encode()
            manifest = json.loads(Path(path).read_text())
            for artifact in manifest["artifacts"]:
                if artifact["name"] == "result.json":
                    artifact["sha256"] = hashlib.sha256(content).hexdigest()
                    artifact["size_bytes"] = len(content)
            # Simulate files being replaced between the verifier and the
            # consumer's reads without modifying the frozen fixture proof.
            substituted_reads[root / "result.json"] = content
            substituted_reads[Path(path)] = json.dumps(manifest).encode()
        return verified
    def changed_read(path, limit):
        return substituted_reads[path] if path in substituted_reads else original_read(path, limit)
    monkeypatch.setattr(records, "verify_execution", swap)
    monkeypatch.setattr(records, "_read", changed_read)
    with pytest.raises(ValueError, match="[Mm]anifest"):
        records.prepare_proposal(workspace / "must-not-publish", read("compiled-spec.json"),
                                 allow_data_read=True, authoring=authoring)
    assert not (workspace / "must-not-publish").exists()


def test_public_projection_omits_extra_declaration_metadata(workspace):
    propose, _ = api()
    data = dataset(workspace)
    data["public_task"]["unit_hierarchy"] = {"population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id",
        "provenance_note": {"raw_identifiers": ["not-for-model-donor-123"], "location": str(workspace / "operator-notes")}}
    data["public_task"]["analysis_columns"]["provenance_note"] = "not-for-model-column-notes"
    data["public_task"]["analysis_columns"]["time"] = "not-for-model-unused-time-column"
    selector = Selector({"kind": "request_information", "message": "Confirm the intended comparison."})
    result = propose(workspace / "planning", question="B minus A", dataset=data, selector=selector, allow_data_read=True)
    assert result["status"] == "awaiting_information"
    context = selector.calls[0]
    assert "not-for-model" not in json.dumps(context)
    assert str(workspace) not in json.dumps(context)
    assert set(context["declarations"]["column_roles"]) == {"condition", "population_unit", "batches"}
