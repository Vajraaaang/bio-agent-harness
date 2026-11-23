"""Receipt-bound reporting over tiny local fixtures; no model participants."""
import json
import subprocess
import sys

import pytest

from tests.test_analysis_episodes import create, inputs, run_contrast, workspace


def verify_report(episode):
    from devbench.analysis.episode_synthesis import verify_synthesis
    return verify_synthesis(episode.root, episode.receipt["root_hash"], allow_evidence_read=True)


def reference(observation, action=4):
    target = observation["result"]["target_results"][0]
    row = target["payload"]["gene_statistics"][0]
    return {"action": action, "target_id": target["target_id"], "hypothesis_id": row["hypothesis_id"]}


@pytest.mark.parametrize("cellular,continuous", [(False, True), (False, False), (True, False)])
def test_finish_resolves_finding_from_verified_result_and_replays(workspace, cellular, continuous):
    episode, _ = create(workspace, cellular=cellular, continuous=continuous)
    observed = run_contrast(episode, cellular)
    selected = reference(observed)
    final = episode.step({"kind": "finish", "findings": [selected]})
    assert final["outcome"] == "completed", final
    report = json.loads((episode.root / "synthesis.json").read_text())
    target = observed["result"]["target_results"][0]
    row = target["payload"]["gene_statistics"][0]
    assert report["evidence"]["status"] == "verified"
    assert report["scientific_correctness"] is None
    assert report["findings"][0]["estimate"] == row
    assert report["findings"][0]["source"]["action"] == 4
    assert report["findings"][0]["source"]["receipt_id"] == observed["receipt_id"]
    summary = report["targets"][0]
    assert summary["computed_contrast"] == target["payload"]["computed_contrast"]
    assert summary["limitation_codes"] == target["limitation_codes"]
    assert summary["hypothesis_family"]["attempted_count"] == len(target["payload"]["gene_statistics"])
    assert summary["hypothesis_family"]["tested_count"] == len(target["payload"]["multiplicity_scope"])
    assert summary["selection_conditional_inference"] == target["payload"]["selection_conditional_inference"]
    text = (episode.root / "summary.md").read_text()
    assert "B minus A" in text and "95%" in text and "q=" in text and "p=" in text
    assert "Scientific correctness has not been scored" in text
    assert verify_report(episode)["evidence_verified"] is True
    if continuous:
        import shutil

        from devbench.analysis.episode_synthesis import verify_synthesis
        from devbench.analysis.episodes import replay_episode
        relocated = workspace / "relocated"
        shutil.copytree(episode.root, relocated)
        assert verify_synthesis(relocated, episode.receipt["root_hash"], allow_evidence_read=True)["valid"]
        replay = replay_episode(relocated, episode.receipt["root_hash"], workspace / "replay", allow_data_execution=True)
        assert replay.outcome == "completed"
        repeated = json.loads((replay.root / "synthesis.json").read_text())
        assert repeated["findings"][0]["estimate"] == report["findings"][0]["estimate"]
        assert repeated["findings"][0]["source"]["receipt_id"] != observed["receipt_id"]
        assert verify_report(replay)["valid"]


def test_request_wording_is_escaped_and_separate_from_computed_units(workspace):
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = inputs(workspace)
    task["questions"][0].update(contrast="A minus B <script>alert(1)</script>", units="absolute molecules")
    episode = AnalysisEpisode.create(workspace / "episode", question="[Open](https://invalid.example) <img src=x>",
        assay="bulk", public_task=task, inputs=paths, allow_data_execution=True)
    observed = run_contrast(episode)
    episode.step({"kind": "finish", "findings": [reference(observed)]})
    report = json.loads((episode.root / "synthesis.json").read_text())
    target = report["targets"][0]
    assert target["requested"]["units"] == "absolute molecules"
    assert target["computed_contrast"]["effect_scale"] == "unchanged_input_expression"
    text = (episode.root / "summary.md").read_text()
    assert "<script>" not in text and "<img" not in text and "[Open](" not in text
    assert "Requested wording" in text and "unchanged input-expression" in text
    assert verify_report(episode)["valid"]


@pytest.mark.parametrize("findings", [None, "g0", [None], [{"action": True, "target_id": "mean_contrast", "hypothesis_id": "g0"}],
    [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0", "estimate": 900}],
    [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0"}] * 2,
    [{"action": i + 1, "target_id": "mean_contrast", "hypothesis_id": "g0"} for i in range(21)]])
def test_finding_schema_and_controller_reject_unbounded_or_invented_claims(findings):
    from jsonschema import Draft202012Validator

    from devbench.analysis.episode_codex import action_schema
    from devbench.analysis.episode_synthesis import validate_finding_requests
    with pytest.raises(ValueError):
        validate_finding_requests(findings)
    assert not Draft202012Validator(action_schema([])).is_valid({"kind": "finish", "findings": findings})


def test_selector_schema_still_accepts_plain_finish_and_bounded_references():
    from jsonschema import Draft202012Validator

    from devbench.analysis.episode_codex import action_schema
    validator = Draft202012Validator(action_schema([]))
    assert validator.is_valid({"kind": "finish"})
    assert validator.is_valid({"kind": "finish", "findings": []})
    assert validator.is_valid({"kind": "finish", "findings": [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0"}]})


def test_a_diagnostic_or_stale_action_cannot_supply_a_finding(workspace):
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    invalid = episode.step({"kind": "finish", "findings": [reference(observed, action=1)]})
    assert invalid["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Select the actual fitted result"})
    final = episode.step({"kind": "finish", "findings": [reference(observed)]})
    assert final["outcome"] == "completed"
    assert verify_report(episode)["valid"]


def test_corrupt_tool_result_withholds_report_and_prevents_acceptance(workspace):
    from devbench.analysis.episodes import verify_episode
    episode, _ = create(workspace)
    observed = run_contrast(episode)
    path = episode.broker.output_dir / (observed["receipt_id"] + ".json")
    path.write_bytes(path.read_bytes() + b" ")
    final = episode.step({"kind": "finish"})
    assert final["outcome"] == "infrastructure_failure"
    assert final["harness_accepted"] is False
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld"
    assert not report["findings"] and not report["targets"]
    assert verify_episode(episode.root, episode.receipt["root_hash"])["valid"]
    verified = verify_report(episode)
    assert verified["valid"] and not verified["evidence_verified"]


@pytest.mark.parametrize("filename", [
    "synthesis.json", "summary.md", "provenance_manifest.json",
    "replay_bundle/parameters.json",
])
def test_modified_sealed_report_is_not_verified(workspace, filename):
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.step({"kind": "finish"})
    path = episode.root / filename
    path.write_text(path.read_text() + " altered")
    with pytest.raises(ValueError, match="artifact_changed"):
        verify_report(episode)


def test_refusal_report_and_no_tool_interruption_remain_distinct(workspace, monkeypatch):
    episode, _ = create(workspace, confounded=True)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix"):
        episode.step({"kind": "tool", "tool": tool})
    episode.step({"kind": "refuse", "reason": "No identifiable adjusted contrast"})
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["outcome"] == "refusal" and not report["findings"]
    design_event = next(event for event in report["tool_events"] if event["tool"] == "validate_design_matrix")
    assert design_event["design_check"]["condition_number_validated"] is False
    assert report["scientific_correctness"] is None
    assert verify_report(episode)["valid"]
    other = workspace / "interrupted"
    other.mkdir()
    interrupted, _ = create(other)
    def stop(*args, **kwargs):
        raise KeyboardInterrupt("injected interruption")
    monkeypatch.setattr(interrupted.broker, "execute", stop)
    with pytest.raises(KeyboardInterrupt):
        interrupted.step({"kind": "tool", "tool": "audit_expression_quality"})
    report = json.loads((interrupted.root / "synthesis.json").read_text())
    assert report["outcome"] == "infrastructure_failure" and not report["findings"]
    assert verify_report(interrupted)["valid"]


def test_resumed_generation_is_not_counted_twice(workspace):
    from devbench.analysis.episode_handoff import resume_handoff
    from tests.test_episode_handoff import REPLY, paused
    original, handoff = paused(workspace)
    restored = resume_handoff(original.root, handoff["manifest_path"], expected_root=handoff["root_hash"],
                             response=REPLY, allow_data_execution=True)
    restored.step({"kind": "tool", "tool": "validate_design_matrix"})
    observed = restored.step({"kind": "tool", "tool": "mean_condition_analysis"})
    restored.step({"kind": "finish", "findings": [reference(observed, action=5)]})
    report = json.loads((restored.root / "synthesis.json").read_text())
    assert len(report["tool_events"]) == 4
    assert report["findings"][0]["source"]["action"] == 5
    assert report["findings"][0]["source"]["result_path"].startswith("tool-evidence-resume-")
    assert verify_report(restored)["valid"]


def test_report_verification_requires_evidence_authorization():
    from devbench.analysis.episode_synthesis import verify_synthesis
    with pytest.raises(PermissionError):
        verify_synthesis(None, None)


def test_older_computation_cannot_replace_latest_result(workspace):
    episode, _ = create(workspace)
    old = run_contrast(episode)
    new = episode.step({"kind": "tool", "tool": "mean_condition_analysis"})
    refused = episode.step({"kind": "finish", "findings": [reference(old)]})
    assert refused["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Use the latest matching result"})
    assert episode.step({"kind": "finish", "findings": [reference(new, action=5)]})["outcome"] == "completed"
    assert verify_report(episode)["valid"]


def _sensitivity_fit(episode):
    options = {"selection_criterion": "bic"}
    assert (
        episode.step(
            {
                "kind": "tool",
                "tool": "validate_design_matrix",
                "branch_id": "sensitivity",
                "options": options,
            }
        )["status"]
        == "succeeded"
    )
    observed = episode.step(
        {
            "kind": "tool",
            "tool": "mean_condition_analysis",
            "branch_id": "sensitivity",
            "options": options,
        }
    )
    assert observed["status"] == "succeeded", observed
    return observed


def test_primary_and_sensitivity_have_independent_receipts_and_replay(
    workspace_tmp_path,
):
    from devbench.analysis.episode_synthesis import render_synthesis

    episode, _ = create(workspace_tmp_path)
    primary = run_contrast(episode)
    sensitivity = _sensitivity_fit(episode)
    final = episode.step(
        {
            "kind": "finish",
            "findings": [
                reference(primary),
                {**reference(sensitivity, action=6), "branch_id": "sensitivity"},
            ],
        }
    )
    assert final["outcome"] == "completed", final
    assert final["primary_branch_id"] == "primary"
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["format"] == "devbench.episode-synthesis/2"
    assert report["primary_branch_id"] == "primary"
    branches = {branch["branch_id"]: branch for branch in report["branches"]}
    expected = {"primary": primary, "sensitivity": sensitivity}
    assert set(branches) == set(expected)
    assert report["targets"] == branches["primary"]["targets"]
    for branch_id, observed in expected.items():
        assert branches[branch_id]["outputs_complete"] is True
        (target,) = branches[branch_id]["targets"]
        assert target["source"]["receipt_id"] == observed["receipt_id"]
        (finding,) = [
            item
            for item in report["findings"]
            if item["source"]["branch_id"] == branch_id
        ]
        assert finding["source"] == target["source"]
        assert (
            finding["estimate"]
            == observed["result"]["target_results"][0]["payload"]["gene_statistics"][0]
        )
    assert (
        branches["primary"]["targets"][0]["source"]
        != branches["sensitivity"]["targets"][0]["source"]
    )
    summary = (episode.root / "summary.md").read_text()
    assert summary == render_synthesis(report)
    assert [line for line in summary.splitlines() if line.startswith("## ")] == [
        "## What data do I have?",
        "## What question am I investigating?",
        "## Which methods are appropriate, and why?",
        "## What happened?",
        "## What can I conclude, and what remains uncertain?",
    ]
    manifest = json.loads((episode.root / "provenance_manifest.json").read_text())
    assert manifest["inputs"] == json.loads(
        (episode.root / "snapshot.json").read_text()
    )
    assert manifest["primary_branch_id"] == "primary"
    assert (
        manifest["execution_dag"]["audit_prefix_head_sha256"]
        == final["audit_prefix_node_ids"][-1]
    )
    assert manifest["execution_dag"]["wal_root_sha256"] is None
    assert manifest["requirement_sources"]["explicit_requirements"] == []
    assert verify_report(episode)["finding_count"] == 2

    replay = subprocess.run(
        [
            sys.executable,
            "-P",
            str(episode.root / "replay_bundle/replay.py"),
            "--expected-root",
            episode.receipt["root_hash"],
            "--allow-data-execution",
            "--output",
            str(workspace_tmp_path / "branch-replay"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["exact_target_hash_match"] is True


@pytest.mark.parametrize("declared_primary,complete", [
    ("sensitivity", True), ("pending", False),
])
def test_v2_selects_the_declared_primary_branch(
    workspace_tmp_path, monkeypatch, declared_primary, complete,
):
    from devbench.analysis import episode_synthesis

    episode, _ = create(workspace_tmp_path)
    default = run_contrast(episode)
    sensitivity = _sensitivity_fit(episode)
    original = episode_synthesis.safe_synthesis

    def select_primary(root, config, final, actions):
        final["primary_branch_id"] = declared_primary
        return original(root, config, final, actions)

    with monkeypatch.context() as context:
        context.setattr(episode_synthesis, "safe_synthesis", select_primary)
        final = episode.step({"kind": "finish"})

    assert final["outcome"] == ("completed" if complete else "scientific_failure")
    report = json.loads((episode.root / "synthesis.json").read_text())
    branches = {branch["branch_id"]: branch for branch in report["branches"]}
    assert report["primary_branch_id"] == declared_primary
    assert report["targets"] == branches[declared_primary]["targets"]
    assert report["outputs_complete"] is complete
    assert branches[declared_primary]["outputs_complete"] is complete
    for branch, observation in (("primary", default), ("sensitivity", sensitivity)):
        (target,) = branches[branch]["targets"]
        assert target["source"]["receipt_id"] == observation["receipt_id"]
    provenance = json.loads((episode.root / "provenance_manifest.json").read_text())
    assert provenance["primary_branch_id"] == declared_primary
    assert verify_report(episode)["evidence_verified"] is True


def _retain_diagnostic_plot(episode, monkeypatch):
    folder = episode.checkpoints.path("action-0001")
    plot = folder / "diagnostic.svg"
    numeric = folder / "diagnostic.csv"
    original = episode.broker._invoke_tool

    def tool_with_plot(tool, arguments):
        result = original(tool, arguments)
        plot.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<title>Synthetic diagnostic</title>'
            '<path d="M 1 9 L 9 1" stroke="black"/></svg>'
        )
        numeric.write_text("x,y\n1,9\n9,1\n")
        result["diagnostics"]["diagnostic_plots"] = [
            {"plot": plot.name, "numeric_sidecar": numeric.name},
        ]
        return result

    monkeypatch.setattr(episode.broker, "_invoke_tool", tool_with_plot)
    observation = episode.step({"kind": "tool", "tool": "audit_expression_quality"})
    assert observation["status"] == "succeeded"
    return plot, numeric


def test_v2_reports_verified_diagnostic_pair(workspace_tmp_path, monkeypatch):
    import hashlib

    episode, _ = create(workspace_tmp_path)
    plot, numeric = _retain_diagnostic_plot(episode, monkeypatch)
    episode.step({"kind": "finish"})

    report = json.loads((episode.root / "synthesis.json").read_text())
    (record,) = report["tool_events"][0]["diagnostic_plots"]
    summary = (episode.root / "summary.md").read_text()
    for role, path in (("plot", plot), ("numeric_sidecar", numeric)):
        relative = path.relative_to(episode.root).as_posix()
        assert record[role] == {
            "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        assert f"]({relative})" in summary
    assert "No verified diagnostic plots" not in summary
    assert verify_report(episode)["evidence_verified"] is True


def test_v2_withholds_tampered_diagnostic_pair(workspace_tmp_path, monkeypatch):
    episode, _ = create(workspace_tmp_path)
    _, numeric = _retain_diagnostic_plot(episode, monkeypatch)
    numeric.write_text("x,y\n1,9\n9,5\n")

    final = episode.step({"kind": "finish"})
    assert final["outcome"] == "infrastructure_failure"
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld"
    assert "diagnostic_plot" in report["evidence"]["reason"]
    summary = (episode.root / "summary.md").read_text()
    assert "](checkpoints/action-0001/diagnostic.csv)" not in summary
    assert verify_report(episode)["evidence_verified"] is False


def test_sensitivity_cannot_satisfy_primary_or_be_cited_as_primary(workspace_tmp_path):
    episode, _ = create(workspace_tmp_path)
    for tool in ("audit_expression_quality", "validate_biological_units"):
        assert episode.step({"kind": "tool", "tool": tool})["status"] == "succeeded"
    sensitivity = _sensitivity_fit(episode)
    selected = reference(sensitivity, action=4)
    invalid = episode.step(
        {"kind": "finish", "findings": [{**selected, "branch_id": "primary"}]}
    )
    assert invalid["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Cite the retained sensitivity branch"})
    final = episode.step({"kind": "finish", "findings": [selected]})
    assert final["outcome"] == "scientific_failure", final
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["outputs_complete"] is False
    assert report["targets"] == []
    assert report["findings"][0]["source"]["branch_id"] == "sensitivity"
    assert verify_report(episode)["valid"]


def test_sensitivity_design_failure_cannot_support_primary_refusal(workspace_tmp_path):
    episode, _ = create(workspace_tmp_path, confounded=True)
    for tool in ("audit_expression_quality", "validate_biological_units"):
        assert episode.step({"kind": "tool", "tool": tool})["status"] == "succeeded"
    diagnostic = episode.step(
        {"kind": "tool", "tool": "validate_design_matrix", "branch_id": "sensitivity"}
    )
    assert (
        diagnostic["result"]["diagnostics"]["design"]["condition_number_validated"]
        is False
    )
    final = episode.step({"kind": "refuse", "reason": "The sensitivity design failed"})
    assert final["outcome"] == "scientific_failure"
    assert final["harness_accepted"] is False
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["tool_events"][-1]["branch_id"] == "sensitivity"
    assert verify_report(episode)["valid"]


@pytest.mark.parametrize(
    ("write_observation", "worker_fails"),
    [(True, True), (False, True), (False, False)],
    ids=["bound-failure", "unbound-failure", "unbound-success"],
)
def test_sensitivity_abort_requires_bound_estimates(
    workspace_tmp_path,
    monkeypatch,
    write_observation,
    worker_fails,
):
    from devbench.platform import episodes

    episode, _ = create(workspace_tmp_path)
    primary = run_contrast(episode)
    options = {"selection_criterion": "bic"}
    assert episode.step({
        "kind": "tool", "tool": "validate_design_matrix",
        "branch_id": "sensitivity", "options": options,
    })["status"] == "succeeded"

    def fail(tool, arguments):
        raise RuntimeError("Injected sensitivity worker failure")

    if not write_observation:
        write_json = episodes.atomic_json

        def abort_observation(path, value):
            if path.name == "observation.json":
                raise OSError("Injected observation write failure")
            return write_json(path, value)

        monkeypatch.setattr(episodes, "atomic_json", abort_observation)

    if worker_fails:
        monkeypatch.setattr(episode.broker, "_invoke_tool", fail)
    final = episode.step({
        "kind": "tool", "tool": "mean_condition_analysis",
        "branch_id": "sensitivity", "options": options,
    })
    assert final["outcome"] == "infrastructure_failure"
    report = json.loads((episode.root / "synthesis.json").read_text())
    if not worker_fails:
        assert not report["targets"]
        assert report["evidence"]["status"] == "withheld"
        assert report["evidence"]["reason"] == (
            "synthesis_successful_event_has_no_action_checkpoint"
        )
        assert verify_report(episode)["evidence_verified"] is False
        return
    assert report["targets"][0]["source"]["receipt_id"] == primary["receipt_id"]
    failed = report["tool_events"][-1]
    assert failed["status"] == "failed"
    assert failed.get("branch_id") == ("sensitivity" if write_observation else None)
    assert failed["action"] == (6 if write_observation else None)
    assert "Injected sensitivity worker failure" in failed["error"]
    assert verify_report(episode)["evidence_verified"] is True


def test_unavailable_optional_declarations_do_not_prevent_refusal(workspace_tmp_path):
    from devbench.analysis.episodes import AnalysisEpisode

    task, paths = inputs(workspace_tmp_path)
    task["context"]["design"] = None
    task["user_parameter_requirements"] = None
    episode = AnalysisEpisode.create(
        workspace_tmp_path / "episode",
        question="Analyze raw reads",
        assay="bulk",
        public_task=task,
        inputs=paths,
        input_kind="raw_reads",
        allow_data_execution=True,
    )
    assert episode.step({"kind": "refuse"})["outcome"] == "refusal"
    assert verify_report(episode)["valid"]


def test_legacy_report_keeps_its_layout_and_read_only_verification(
    workspace_tmp_path,
    monkeypatch,
):
    from devbench.analysis import episode_synthesis

    episode, _ = create(workspace_tmp_path)
    observed = run_contrast(episode)
    original = episode_synthesis.safe_synthesis

    def legacy(root, config, final, actions):
        final.pop("synthesis_format", None)
        final.pop("primary_branch_id", None)
        return original(root, config, final, actions)

    with monkeypatch.context() as context:
        context.setattr(episode_synthesis, "safe_synthesis", legacy)
        final = episode.step({"kind": "finish", "findings": [reference(observed)]})
    assert final["outcome"] == "completed"
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["format"] == "devbench.episode-synthesis/1"
    assert "branches" not in report
    summary = (episode.root / "summary.md").read_text()
    assert summary.startswith("# RNA-seq analysis episode\n\nQuestion supplied:")
    assert "## What data do I have?" not in summary
    assert not (episode.root / "provenance_manifest.json").exists()
    assert verify_report(episode)["valid"]


def test_failed_hypothesis_is_reported_without_an_invented_estimate(workspace):
    import csv

    from devbench.analysis.episodes import AnalysisEpisode

    task, paths = inputs(workspace, continuous=False)
    with paths["expression"].open(newline="") as handle:
        rows = list(csv.reader(handle))
    rows[-1][1:] = ["0"] * (len(rows[-1]) - 1)
    with paths["expression"].open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    episode = AnalysisEpisode.create(workspace / "episode", question="Inspect all attempted hypotheses", assay="bulk",
        public_task=task, inputs=paths, allow_data_execution=True)
    observed = run_contrast(episode)
    failed = observed["result"]["target_results"][0]["payload"]["gene_statistics"][-1]
    assert failed["estimate_status"] != "estimated"
    final = episode.step({"kind": "finish", "findings": [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": failed["hypothesis_id"]}]})
    assert final["outcome"] == "completed" and final["harness_accepted"]
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["outputs_complete"] is True
    assert failed["p_value"] is None and failed["q_value"] is None and failed["missing_reason"]
    assert report["targets"][0]["hypothesis_family"]["attempted_count"] == len(task["gene_ids"])
    assert report["targets"][0]["hypothesis_family"]["tested_count"] == len(task["gene_ids"]) - 1
    assert report["findings"][0]["estimate"] == failed
    assert "No estimate or test reported" in (episode.root / "summary.md").read_text()
    assert verify_report(episode)["valid"]


def test_reporting_failure_preserves_the_original_infrastructure_reason(workspace):
    episode, _ = create(workspace)
    run_contrast(episode)
    episode.broker.capture_error = "injected capture failure"
    final = episode.abort("provider_interrupted_before_synthesis")
    assert final["outcome"] == "infrastructure_failure"
    assert final["reason"] == "tool_evidence_capture_incomplete"
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld"
    assert verify_report(episode)["valid"]


def test_observation_cannot_be_transplanted_to_an_earlier_rejected_action(workspace):
    episode, _ = create(workspace)
    assert episode.step({"kind": "tool", "tool": "mean_condition_analysis"})["status"] == "rejected"
    episode.step({"kind": "revise", "reason": "Run prerequisites"})
    observed = run_contrast(episode)
    old = episode.root / "checkpoints/action-0006/observation.json"
    false = episode.root / "checkpoints/action-0001/observation.json"
    false.parent.mkdir()
    false.write_bytes(old.read_bytes())
    old.unlink()
    result = episode.step({"kind": "finish", "findings": [reference(observed, action=1)]})
    assert result.get("outcome") != "completed"
    assert result.get("status") == "rejected" or result.get("outcome") == "infrastructure_failure"
    if episode.outcome is None:
        episode.abort("checkpoint_observation_tampered")
    assert not episode.observation()["harness_accepted"]


def test_malformed_observation_withholds_report_but_seals_failure(workspace):
    episode, _ = create(workspace)
    run_contrast(episode)
    (episode.root / "checkpoints/action-0004/observation.json").write_text("[]")
    result = episode.step({"kind": "finish"})
    assert result["outcome"] == "infrastructure_failure" and not result["harness_accepted"]
    assert episode.receipt is not None
    report = json.loads((episode.root / "synthesis.json").read_text())
    assert report["evidence"]["status"] == "withheld" and not report["findings"]
    assert verify_report(episode)["valid"]


def _family_target():
    return {"target_id": "mean_contrast", "claim_type": "restricted_estimate", "estimate_status": "estimated",
        "payload": {"gene_statistics": [
            {"hypothesis_id": "g0", "gene_id": "g0", "estimate_status": "estimated", "p_value": 0.01, "q_value": 0.02},
            {"hypothesis_id": "g1", "gene_id": "g1", "estimate_status": "failed", "p_value": None, "q_value": None,
             "missing_reason": "dispersion_unavailable_or_nonconverged"}]}}


def _family_variant(case):
    target = _family_target()
    rows = target["payload"]["gene_statistics"]
    row_updates = {
        "no_estimates": (0, {
            "estimate_status": "failed", "p_value": None, "q_value": None,
            "missing_reason": "not_estimable",
        }),
        "estimated_p_missing": (0, {"p_value": None}),
        "estimated_q_missing": (0, {"q_value": None}),
        "failed_p": (1, {"p_value": 0.5}),
        "failed_q": (1, {"q_value": 0.5}),
        "missing_reason": (1, {"missing_reason": None}),
        "blank_reason": (1, {"missing_reason": " "}),
        "not_tested": (1, {"estimate_status": "not_tested"}),
        "nonfinite_p": (0, {"p_value": float("nan")}),
        "q_below_p": (0, {"q_value": 0.001}),
    }
    if case in row_updates:
        position, updates = row_updates[case]
        rows[position].update(updates)
    elif case == "target_failed":
        target.update(claim_type="failed", estimate_status="failed")
    elif case == "missing_gene":
        rows.pop()
    elif case == "extra_gene":
        rows.append({**rows[1], "hypothesis_id": "g2", "gene_id": "g2"})
    elif case == "duplicate_hypothesis":
        rows.append(dict(rows[0]))
    elif case == "missing_public_null":
        del rows[1]["p_value"]
    else:
        raise ValueError(case)
    return target


_INVALID_FAMILY_CASES = ["target_failed", "no_estimates", "estimated_p_missing", "estimated_q_missing",
    "failed_p", "failed_q", "missing_reason", "blank_reason", "not_tested", "missing_gene", "extra_gene",
    "duplicate_hypothesis", "nonfinite_p", "q_below_p", "missing_public_null"]


@pytest.mark.parametrize("status", ["failed", "unavailable"])
def test_complete_family_accepts_honest_nonestimates(status):
    from devbench.analysis.episode_synthesis import target_family_complete
    target = _family_target()
    target["payload"]["gene_statistics"][1]["estimate_status"] = status
    assert target_family_complete(target, ["g0", "g1"])


@pytest.mark.parametrize("case", _INVALID_FAMILY_CASES)
def test_complete_family_rejects_unsupported_or_invalid_rows(case):
    from devbench.analysis.episode_synthesis import target_family_complete
    assert not target_family_complete(_family_variant(case), ["g0", "g1"])


def test_complete_family_keeps_component_specific_hypotheses():
    from devbench.analysis.episode_synthesis import target_family_complete
    target = _family_target()
    target["target_id"] = "cell_state_effect"
    rows = target["payload"]["gene_statistics"]
    target["payload"]["gene_statistics"] = [
        {**row, "hypothesis_id": component + "_" + row["gene_id"], "component_id": component}
        for component in ("type_a", "type_b") for row in rows]
    assert target_family_complete(target, ["g0", "g1"])


@pytest.mark.parametrize("case", ["target_failed", "no_estimates", "estimated_p_missing", "estimated_q_missing",
    "failed_p", "failed_q", "missing_gene", "duplicate_hypothesis"])
def test_finish_gate_rejects_unsupported_family_without_manufacturing_receipts(workspace, monkeypatch, case):
    # In-memory invalid-output fixtures test the gate, not the retained evidence validator.
    episode, _ = create(workspace)
    task_genes = episode.config["public_task"]["gene_ids"]
    target = _family_variant(case)
    for row in target["payload"]["gene_statistics"]:
        if row["gene_id"] in {"g0", "g1"}:
            original = row["gene_id"]
            row["gene_id"] = task_genes[int(original[-1])]
            row["hypothesis_id"] = task_genes[int(original[-1])]
    rows = target["payload"]["gene_statistics"]
    if case in {"estimated_p_missing", "estimated_q_missing"}:
        rows[1].update(estimate_status="estimated", p_value=0.1, q_value=0.2)
        rows[1].pop("missing_reason", None)
    for gene in task_genes[2:]:
        rows.append({"hypothesis_id": gene, "gene_id": gene, "estimate_status": "failed" if case == "no_estimates" else "estimated",
            "p_value": None if case == "no_estimates" else 0.1, "q_value": None if case == "no_estimates" else 0.2,
            **({"missing_reason": "not_estimable"} if case == "no_estimates" else {})})
    episode.results = [{"target_results": [target]}]
    monkeypatch.setattr(episode, "_refusal_supported", lambda: False)
    monkeypatch.setattr(episode, "_finish", lambda outcome, reason: {"outcome": outcome, "reason": reason})
    try:
        assert episode.step({"kind": "finish"})["outcome"] == "scientific_failure"
    finally:
        episode.broker.close()
