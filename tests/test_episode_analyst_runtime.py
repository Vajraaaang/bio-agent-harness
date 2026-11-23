"""Offline analyst loop: retained queries and decisions, never scientific qualification."""
import json
from pathlib import Path
import pytest
from tests.test_analysis_episodes import create, workspace
from tests.test_episode_agent import ScriptedSelector, sequence, verify
from devbench.analysis.episode_agent import AgentLimits, Selection, drive_episode


def decision(context):
    return {"question": "What supports this contrast?", "concern": "Evidence remains conditional",
        "evidence_refs": [("query:" if item["action"]["kind"] == "query_evidence" else "action:") + str(i + 1)
            for i, item in enumerate(context["history"]) if "action" in item],
        "competing_explanations": ["group effect", "limited information"],
        "expected_discriminating_observation": "Inspect retained evidence without refitting"}


class AnalystSelector(ScriptedSelector):
    def select(self, context, output, **kwargs):
        choice = super().select(context, output, **kwargs)
        return Selection(choice.action, choice.evidence, decision_record=decision(context))


def finish(disposition="completed"):
    return {"kind": "finish", "disposition": disposition, "reason": "Scoped evidence inspected",
        "followup_proposals": []}


def test_profile_real_query_loop_is_bound_before_dispatch_and_replays(workspace):
    episode, _ = create(workspace)
    selector = AnalystSelector(sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4,
        "query": {"mode": "gene_statistics", "limit": 2}}, finish()])
    final = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "completed"
    assert final["analyst"]["disposition"] == "completed"
    assert len(final["analyst"]["decision_records"]) == 6
    assert len(episode.results) == 4  # Queries cannot impersonate new numerical results.
    queried = selector.contexts[-1]["history"][-1]["observation"]
    assert queried["query_receipt"]["source_action"] == 4
    assert len(json.dumps(queried, separators=(",", ":")).encode()) <= 12 * 1024
    assert "result" not in selector.contexts[1]["history"][0]["observation"]
    paths = episode.root / "agent" / "decisions"
    assert (paths / "selection-0005.json").exists()
    verify(episode)
    replay = episode.replay(workspace / "analyst-replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "completed"
    assert len(replay.results) == 4
    verify(replay)


def test_missing_decision_record_prevents_dispatch(workspace):
    episode, _ = create(workspace)
    selector = ScriptedSelector(sequence())
    final = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["reason"] == "invalid_selector_response"
    assert not episode.actions and not episode.broker.events


def test_partial_stop_does_not_claim_scientific_success(workspace):
    episode, _ = create(workspace)
    final = drive_episode(episode, AnalystSelector([finish("needs_followup")]),
        analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "incomplete"
    assert final["harness_accepted"] is False
    assert final["analyst"]["stopping_reason"] == "Scoped evidence inspected"
    verify(episode)


@pytest.mark.parametrize("stop", ["refuse", "abort"])
def test_rejected_finish_metadata_does_not_survive_later_stop(workspace, stop):
    episode, _ = create(workspace)
    rejected_finish = {**finish(), "reason": "Rejected completion claim",
        "findings": [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "absent_gene"}],
        "followup_proposals": [{"question": "Inspect another contrast", "inputs_or_contrast": "B minus A",
            "candidate_procedure": "Independent review", "desired_evidence": "Qualified comparison",
            "status": "proposed"}]}
    class StoppingSelector(AnalystSelector):
        def select(self, *args, **kwargs):
            selected = super().select(*args, **kwargs)
            selected.decision_record["evidence_refs"] = []
            return selected
    terminal = {"kind": "refuse", "reason": "Unsupported finding"} if stop == "refuse" else RuntimeError("Controlled abort")
    selector = StoppingSelector(sequence()[:-1] + [rejected_finish, terminal])
    final = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == ("scientific_failure" if stop == "refuse" else "infrastructure_failure")
    assert final["reason"] == ("unsupported_refusal_claim" if stop == "refuse" else "selector_failed:RuntimeError")
    assert final["analyst"]["disposition"] == "partial"
    assert final["analyst"]["stopping_reason"] == final["reason"]
    assert final["analyst"]["followup_proposals"] == []
    assert final["analyst"]["findings"] == []
    assert final["finding_requests"] == []
    assert len(episode.results) == 4
    assert episode.actions[4] == rejected_finish
    assert selector.contexts[-1]["history"][-1]["observation"]["diagnostic"] == "finding_reference_not_supported_by_current_results"
    retained = json.loads((episode.root / "agent/selection-0005/selection.json").read_text())
    assert retained["action"] == rejected_finish
    verify(episode)


def test_forged_evidence_reference_stops_before_action(workspace):
    class Forged(AnalystSelector):
        def select(self, context, output, **kwargs):
            choice = super().select(context, output, **kwargs)
            record = choice.decision_record
            record["evidence_refs"] = ["query:999"]
            return Selection(choice.action, choice.evidence, record)
    episode, _ = create(workspace)
    final = drive_episode(episode, Forged(sequence()), analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["reason"] == "invalid_selector_response"
    assert not episode.actions


def test_full_prompt_cumulative_budget_is_enforced_before_selection(workspace):
    episode, _ = create(workspace)
    limits = AgentLimits(max_selections=14, max_context_bytes=256000, max_total_context_bytes=1024)
    selector = AnalystSelector(sequence())
    final = drive_episode(episode, selector, limits=limits, analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["reason"] == "selector_cumulative_context_budget_exhausted_no_dispatch"
    assert not selector.contexts and not episode.actions


def test_analyst_handoff_preserves_query_and_decision_journal(workspace):
    from devbench.analysis.episode_handoff import resume_handoff
    episode, _ = create(workspace)
    actions = sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4, "query": {"mode": "aggregate"}},
        {"kind": "request_information", "message": "Confirm interpretation scope"}, finish()]
    selector = AnalystSelector(actions)
    pending = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    restored = resume_handoff(episode.root, pending["handoff"]["manifest_path"],
        expected_root=pending["handoff"]["root_hash"], response={"message": "Exploratory research only",
            "preserves_frozen_inputs_and_settings": True}, allow_data_execution=True)
    final = drive_episode(restored, selector, allow_data_execution=True)
    assert final["outcome"] == "completed"
    assert len(final["analyst"]["query_receipts"]) == 1
    assert len(final["analyst"]["decision_records"]) == 7
    verify(restored)


def test_synthesis_rejects_query_receipt_source_mutation(workspace):
    from devbench.analysis.episode_synthesis import build_synthesis
    episode, _ = create(workspace)
    actions = sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4,
        "query": {"mode": "aggregate"}}, finish()]
    final = drive_episode(episode, AnalystSelector(actions), analyst_profile="analyst_v1", allow_data_execution=True)
    final["analyst"]["query_receipts"][0]["source_action"] = 3
    with pytest.raises(ValueError, match="analyst"):
        build_synthesis(episode.root, episode.config, final, episode.actions, allow_evidence_read=True)


def test_preaction_record_exists_at_first_tool_dispatch(workspace):
    episode, _ = create(workspace)
    original = episode.step
    def checked(action, **kwargs):
        path = episode.root / "agent" / "decisions" / "selection-0001.json"
        assert path.is_file()
        pre = json.loads(path.read_text())
        assert pre["action"] == action and pre["origin"] == "agent_selected"
        return original(action, **kwargs)
    episode.step = checked
    final = drive_episode(episode, AnalystSelector([finish("partial")]), analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "incomplete"


def test_query_backed_typed_gene_finding_retains_unavailable_scope(workspace):
    class Classified(AnalystSelector):
        def select(self, context, output, **kwargs):
            choice = super().select(context, output, **kwargs)
            if choice.action.get("kind") == "finish":
                receipt = context["history"][-1]["observation"]["query_receipt"]
                choice.action["findings"] = [{"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0",
                    "result_sha256": receipt["source"]["result_sha256"], "disposition": "not_assessable",
                    "evidence_refs": ["query:5"], "field_refs": ["query:5/query_result/status", "query:5/query_result/missing_reason"],
                    "qualification": "Count support unavailable for continuous expression; no count conclusion"}]
            return choice
    episode, _ = create(workspace)
    final = drive_episode(episode, Classified(sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4,
        "query": {"mode": "counts", "gene_ids": ["g0"]}}, finish("exploratory")]),
        analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "incomplete"
    assert final["analyst"]["findings"][0]["disposition"] == "not_assessable"
    synthesis = json.loads((episode.root / "synthesis.json").read_text())
    assert synthesis["analyst_findings"][0]["observations"][0]["value"] == "unavailable"
    verify(episode)


class TypedFindingSelector(AnalystSelector):
    def __init__(self, actions, *, disposition="candidate", mutate=None, include_legacy=False):
        super().__init__(actions)
        self.disposition, self.mutate, self.include_legacy = disposition, mutate, include_legacy
        self.classified = False

    def select(self, context, output, **kwargs):
        choice = super().select(context, output, **kwargs)
        if choice.action.get("kind") == "finish" and not self.classified:
            self.classified = True
            query = next(item["observation"] for item in reversed(context["history"]) if item["action"].get("kind") == "query_evidence")
            request = {"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0",
                "result_sha256": query["query_receipt"]["source"]["result_sha256"], "disposition": self.disposition,
                "evidence_refs": ["query:5"], "field_refs": ["query:5/query_result/rows/0/values", "query:5/query_result/rows/0/q_value"],
                "qualification": "Exploratory retained effect; no frozen discovery reporting rule"}
            if self.mutate:
                self.mutate(request)
            choice.action["findings"] = [request]
            if self.include_legacy:
                choice.action["findings"].append({"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g1"})
        return choice


def typed_actions(*, repair=False):
    return sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4,
        "query": {"mode": "gene_statistics", "limit": 2}}, finish("exploratory")] + ([finish("partial")] if repair else [])


def test_typed_candidate_and_legacy_reference_coexist_and_replay(workspace):
    episode, _ = create(workspace)
    final = drive_episode(episode, TypedFindingSelector(typed_actions(), include_legacy=True),
        analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "incomplete"
    synthesis = json.loads((episode.root / "synthesis.json").read_text())
    finding = synthesis["analyst_findings"][0]
    assert finding["gene_id"] == "g0" and finding["disposition"] == "candidate"
    assert finding["observations"][0]["value"] == episode.results[-1]["target_results"][0]["payload"]["gene_statistics"][0]["values"]
    assert synthesis["findings"][0]["estimate"]["gene_id"] == "g1"
    assert finding["reporting_scope"] == "exploratory_description_no_significance_or_discovery_claim"
    verify(episode)
    replay = episode.replay(workspace / "typed-replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    verify(replay)
    replayed = json.loads((replay.root / "synthesis.json").read_text())["analyst_findings"][0]
    assert replayed["observations"] == finding["observations"]
    assert replayed["disposition"] == "candidate"


@pytest.mark.parametrize("mutation", [
    lambda item: item.update(result_sha256="0" * 64),
    lambda item: item.update(field_refs=["query:5/query_result/rows/1/q_value"]),
    lambda item: item.update(field_refs=["query:5/query_result/rows/0/absent"]),
    lambda item: item.update(evidence_refs=["query:3"]),
    lambda item: item.update(p_value=.001),
    lambda item: item.update(disposition="limited_count"),
    lambda item: item.update(disposition="significant_discovery"),
])
def test_typed_finding_rejects_fabrication_cross_gene_and_unsupported_labels(workspace, mutation):
    episode, _ = create(workspace)
    final = drive_episode(episode, TypedFindingSelector(typed_actions(repair=True), mutate=mutation),
        analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "incomplete"
    assert final["analyst"]["findings"] == []
    assert final["finding_requests"] == []
    assert len(episode.actions) == 7
    assert json.loads((episode.root / "synthesis.json").read_text())["analyst_findings"] == []
    verify(episode)


def test_typed_disposition_and_query_evidence_tampering_fail_independent_rebuild(workspace):
    import copy
    from devbench.analysis.episode_synthesis import build_synthesis
    episode, _ = create(workspace)
    final = drive_episode(episode, TypedFindingSelector(typed_actions()), analyst_profile="analyst_v1", allow_data_execution=True)
    changed = copy.deepcopy(final)
    changed["analyst"]["findings"][0]["disposition"] = "limited_count"
    with pytest.raises(ValueError, match="analyst_findings"):
        build_synthesis(episode.root, episode.config, changed, episode.actions, allow_evidence_read=True)
    query_path = episode.root / "checkpoints/query-0005/query-observation.json"
    retained = json.loads(query_path.read_text())
    retained["query_result"]["rows"][0]["q_value"] = .000001
    query_path.write_text(json.dumps(retained))
    with pytest.raises(ValueError, match="checkpoint|analyst|artifacts"):
        build_synthesis(episode.root, episode.config, final, episode.actions, allow_evidence_read=True)


def test_typed_limited_count_uses_actual_zero_counts_and_keeps_failed_family_row(workspace):
    import csv
    from tests.test_analysis_episodes import inputs, api
    task, paths = inputs(workspace, continuous=False)
    with paths["expression"].open(newline="") as handle:
        rows = list(csv.reader(handle))
    rows[1][1:] = ["0"] * (len(rows[0]) - 1)
    with paths["expression"].open("w", newline="") as handle:
        csv.writer(handle).writerows(rows)
    Episode, _, _ = api()
    episode = Episode.create(workspace / "episode", question="Inspect retained zero-count limitations",
        assay="bulk", public_task=task, inputs=paths, allow_data_execution=True)
    actions = sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4,
        "query": {"mode": "counts", "gene_ids": ["g0"]}}, finish("exploratory")]
    selector = TypedFindingSelector(actions, disposition="limited_count",
        mutate=lambda request: request.update(field_refs=["query:5/query_result/rows/0/counts"]))
    final = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "incomplete"
    synthesis = json.loads((episode.root / "synthesis.json").read_text())
    finding = synthesis["analyst_findings"][0]
    assert finding["disposition"] == "limited_count"
    assert set(finding["observations"][0]["value"].values()) == {0}
    source_row = episode.results[-1]["target_results"][0]["payload"]["gene_statistics"][0]
    assert source_row["p_value"] is None and source_row["q_value"] is None
    assert source_row["estimate_status"] in {"failed", "unavailable"}
    assert "limited\\_count" in (episode.root / "summary.md").read_text()
    verify(episode)


def test_typed_paired_candidates_require_distinct_declared_units(tmp_path):
    import hashlib
    from devbench.analysis.checkpoints import digest
    from devbench.analysis.episode_synthesis import _analyst_findings
    metadata = tmp_path / "metadata.csv"
    metadata.write_text("sample_id,donor_id,condition\na,d1,A\nb,d1,B\nc,d2,A\nd,d2,B\n")
    (tmp_path / "snapshot.json").write_text(json.dumps({"metadata": {"path": "metadata.csv", "sha256": hashlib.sha256(metadata.read_bytes()).hexdigest()}}))
    config = {"public_task": {"observation_id_label": "sample_id", "population_unit_label": "donor_id", "analysis_columns": {"condition": "condition"}}}
    pairs = [{"reference": "a", "comparison": "b"}, {"reference": "c", "comparison": "d"}]
    payload = {"layer": "normalized", "rows": [{"gene_id": "plantX", "descriptive_dominance": .9,
        "pairs": [{"reference_value": 0, "comparison_value": 9}, {"reference_value": 0, "comparison_value": 1}]}]}
    receipt = {"action": 5, "source": {"result_sha256": "a" * 64}, "response_sha256": digest(payload)}
    folder = tmp_path / "checkpoints/query-0005"; folder.mkdir(parents=True)
    (folder / "query-observation.json").write_text(json.dumps({"query_receipt": receipt, "query_result": payload}))
    actions = [{}] * 4 + [{"kind": "query_evidence", "source_action": 4, "query": {"mode": "paired", "pairs": pairs}}]
    request = {"action": 4, "target_id": "mean_contrast", "hypothesis_id": "plantX", "result_sha256": "a" * 64,
        "disposition": "single_unit_dominance_candidate", "evidence_refs": ["query:5"],
        "field_refs": ["query:5/query_result/rows/0/descriptive_dominance"], "qualification": "Contribution candidate conditional on supplied identities"}
    resolved = [(request, {"event": {"result_sha256": "a" * 64}}, {"gene_id": "plantX", "hypothesis_id": "plantX", "estimate_status": "estimated"})]
    report = _analyst_findings(tmp_path, config, actions, resolved, [receipt])[0]
    assert report["declared_pairing"]["distinct_declared_units"] == 2
    assert "not_independent_identity_validation" in report["declared_pairing"]["scope"]
    request["disposition"] = "condition_restricted_candidate"
    request["field_refs"] = ["query:5/query_result/rows/0/pairs"]
    assert _analyst_findings(tmp_path, config, actions, resolved, [receipt])[0]["disposition"] == "condition_restricted_candidate"
    actions[-1]["query"]["pairs"] = [pairs[0], pairs[0]]
    with pytest.raises(ValueError, match="disjoint|distinct"):
        _analyst_findings(tmp_path, config, actions, resolved, [receipt])


def test_typed_schema_preserves_strict_legacy_ids_and_numeric_field_rejection():
    from devbench.analysis.episode_synthesis import validate_finding_requests
    legacy = {"action": 4, "target_id": "mean_contrast", "hypothesis_id": "g0"}
    assert validate_finding_requests([legacy]) == [legacy]
    with pytest.raises(ValueError):
        validate_finding_requests([dict(legacy, hypothesis_id="g0\n")])
    with pytest.raises(ValueError):
        validate_finding_requests([dict(legacy, action=4.0)])


def test_declared_pairing_rejects_empty_comparison_condition(tmp_path):
    import hashlib
    from devbench.analysis.episode_synthesis import _declared_pairing
    path = tmp_path / "metadata.csv"
    path.write_text("sample_id,donor_id,condition\na,d1,A\nb,d1,\nc,d2,A\nd,d2,\n")
    (tmp_path / "snapshot.json").write_text(json.dumps({"metadata": {"path": "metadata.csv", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}))
    config = {"public_task": {"observation_id_label": "sample_id", "population_unit_label": "donor_id", "analysis_columns": {"condition": "condition"}}}
    with pytest.raises(ValueError, match="conditions"):
        _declared_pairing(tmp_path, config, [{"reference": "a", "comparison": "b"}, {"reference": "c", "comparison": "d"}])

@pytest.mark.parametrize("handoff", [False, True])
def test_rejected_query_can_revise_retry_finish_and_replay(workspace, handoff):
    from devbench.analysis.episode_handoff import resume_handoff
    from devbench.analysis.episode_synthesis import build_synthesis
    episode, paths = create(workspace)
    before = {key: path.read_bytes() for key, path in paths.items()}
    rejected = {"kind": "query_evidence", "source_action": 4, "query": {"limit": 21}}
    choices = sequence()[:-1] + [rejected, {"kind": "revise", "reason": "Reduce query page size"}]
    if handoff:
        choices += [{"kind": "request_information", "message": "Confirm exploratory scope"}]
    choices += [{"kind": "query_evidence", "source_action": 4, "query": {"limit": 2}}, finish()]
    class RecoverySelector(AnalystSelector):
        def select(self, *args, **kwargs):
            selected = super().select(*args, **kwargs)
            selected.decision_record["evidence_refs"] = []
            return selected
    selector = RecoverySelector(choices)
    final = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    if handoff:
        episode = resume_handoff(episode.root, final["handoff"]["manifest_path"],
            expected_root=final["handoff"]["root_hash"], response={"message": "Exploratory only",
                "preserves_frozen_inputs_and_settings": True}, allow_data_execution=True)
        final = drive_episode(episode, selector, allow_data_execution=True)
    assert final["outcome"] == "completed", final
    assert len(final["analyst"]["query_receipts"]) == 1
    assert selector.contexts[5]["history"][-1]["observation"]["status"] == "rejected"
    assert before == {key: path.read_bytes() for key, path in paths.items()}
    verify(episode)
    replay = episode.replay(workspace / "rejected-query-replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "completed"
    verify(replay)
    changed = json.loads(json.dumps(episode.actions))
    changed[4]["query"] = {"limit": 20}
    with pytest.raises(ValueError, match="analyst_query"):
        build_synthesis(episode.root, episode.config, final, changed, allow_evidence_read=True)


def test_episode_passes_declared_pairing_bindings_and_replays_normalized_query(workspace):
    import csv
    from tests.test_analysis_episodes import inputs
    from devbench.analysis.episodes import AnalysisEpisode
    task, paths = inputs(workspace, continuous=False)
    task["context"].update(dependence="paired", requested_condition_contrast={"numerator": "B", "denominator": "A"})
    task["analysis_columns"]["blocking"] = ["donor_id"]
    with paths["metadata"].open(newline="") as handle:
        reader = csv.DictReader(handle); columns = reader.fieldnames; rows = list(reader)
    for index, row in enumerate(rows): row["donor_id"] = "d" + str(index % 4)
    with paths["metadata"].open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns); writer.writeheader(); writer.writerows(rows)
    before = {key: path.read_bytes() for key, path in paths.items()}
    episode = AnalysisEpisode.create(workspace / "episode", question="Compare paired declared donors",
        assay="bulk", public_task=task, inputs=paths, allow_data_execution=True)
    choices = sequence()[:-1] + [{"kind": "query_evidence", "source_action": 4,
        "query": {"mode": "paired", "layer": "normalized", "target_id": "mean_contrast", "gene_ids": ["g0"]}}, finish()]
    selector = AnalystSelector(choices)
    final = drive_episode(episode, selector, analyst_profile="analyst_v1", allow_data_execution=True)
    assert final["outcome"] == "completed", final
    query = selector.contexts[-1]["history"][-1]["observation"]["query_result"]
    assert query["status"] == "available" and query["layer"] == "normalized"
    assert [(pair["reference"], pair["comparison"]) for pair in query["rows"][0]["pairs"]] == [("s0", "s4"), ("s1", "s5"), ("s2", "s6"), ("s3", "s7")]
    assert before == {key: path.read_bytes() for key, path in paths.items()}
    verify(episode)
    replay = episode.replay(workspace / "derived-pair-replay", expected_root=episode.receipt["root_hash"], allow_data_execution=True)
    assert replay.outcome == "completed"
    verify(replay)
