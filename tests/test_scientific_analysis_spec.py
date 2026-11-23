"""Executable binding checks with public synthetic inputs; no model evaluation."""
import copy
import importlib.util
import json

import pytest

from tests.test_analysis_episodes import create, workspace

__all__ = ['workspace']


def spec_api():
    assert importlib.util.find_spec("devbench.analysis.scientific_analysis_spec") is not None, "Executable scientific specification is missing"
    from devbench.analysis import scientific_analysis_spec
    return scientific_analysis_spec


def bound_spec(episode, *, options=None, decision=None, tool="mean_condition_analysis"):
    from devbench.analysis.episodes import resolve_scientific_options
    resolved = resolve_scientific_options(tool, options, episode.config["fixed_options"].get(tool, {}),
        episode.config["public_task"], episode.menu[tool]["driver"])
    return spec_api().bind_episode_spec(episode, tool, resolved, decision)


@pytest.mark.parametrize("method,continuous", [("invented_engine", True), ("DESeq2", False), ("gaussian", False), ("negative_binomial", True)])
def test_invented_unavailable_or_mismatched_method_never_dispatches(workspace, method, continuous):
    episode, _ = create(workspace, continuous=continuous)
    response = episode.step({"kind": "tool", "tool": "validate_design_matrix", "decision": {
        "method": method, "why": "A supplied candidate; availability must be checked.", "parameters": {}}})
    assert response["status"] == "rejected"
    assert "method" in response["diagnostic"]
    assert not episode.broker.events
    assert episode.actions[-1]["decision"]["method"] == method


@pytest.mark.parametrize("options", [{"spline_df": 4}, {"spline_candidates": [3, 4]}, {"correlation_threshold": .5}, {"test": "LRT"}])
def test_mean_contrast_rejects_inapplicable_options_before_dispatch(workspace, options):
    episode, _ = create(workspace)
    response = episode.step({"kind": "tool", "tool": "validate_design_matrix", "options": options})
    assert response["status"] == "rejected"
    assert not episode.broker.events


def test_specification_has_canonical_content_identity_and_attributed_parameters(workspace):
    episode, _ = create(workspace)
    spec = bound_spec(episode, options={"selection_criterion": "bic"}, decision={
        "method": "gaussian", "why": "BIC is transported for the single fixed procedure; it does not compare models.",
        "parameters": {"selection_criterion": "bic"}})
    value = spec.to_dict()
    assert spec.specification_id == spec_api().ScientificAnalysisSpec.from_dict(value).specification_id
    assert "attempt_id" not in value and "result_id" not in value
    assert value["method"]["identifier"] == "mean_condition_analysis"
    assert value["method"]["version"] == "observed-bulk-1"
    assert value["model_comparison"]["performed"] is False
    assert value["model_comparison"]["criterion_scope"] == "single_fixed_model_score_only"
    assert value["normalization"]["method"] == "unchanged_continuous_input"
    assert value["families"]["testing"] == episode.config["public_task"]["gene_ids"]
    origins = {item["name"]: item for item in value["parameter_origins"]}
    assert origins["selection_criterion"]["origin"] == "assistant"
    assert origins["condition_col"]["origin"] == "supplied_declaration"
    assert all(item["source"] for item in origins.values())
    value["parameters"]["selection_criterion"] = "aic"
    assert spec.to_dict()["parameters"]["selection_criterion"] == "bic"


@pytest.mark.parametrize("section,key,value", [("method", "engine", "DESeq2"),
    ("normalization", "method", "invented_normalization"), ("parameters", "spline_df", 4)])
def test_specification_parser_rejects_inconsistent_executable_semantics(workspace, section, key, value):
    episode, _ = create(workspace)
    spec = bound_spec(episode).to_dict()
    spec[section][key] = value
    if section == "parameters":
        spec["parameter_origins"].append({"name": key, "value": value, "origin": "assistant", "rationale": "Changed", "source": "fixture"})
    with pytest.raises(ValueError):
        spec_api().ScientificAnalysisSpec.from_dict(spec)


def test_all_supplied_task_declarations_are_content_bound(workspace):
    episode, _ = create(workspace)
    original = bound_spec(episode)
    episode.config["public_task"]["context"]["population_scope"] = "a changed population declaration"
    changed = bound_spec(episode)
    assert changed.specification_id != original.specification_id
    assert changed.to_dict()["inputs"]["public_task_sha256"] != original.to_dict()["inputs"]["public_task_sha256"]


def test_user_parameter_origin_requires_exact_retained_requirement(workspace):
    episode, _ = create(workspace)
    decision = {"method": "gaussian", "why": "Requested score transport.", "parameters": {"selection_criterion": "bic"},
        "parameter_origins": [{"name": "selection_criterion", "value": "bic", "origin": "user", "rationale": "Claimed user choice"}]}
    with pytest.raises(ValueError, match="exact retained requirement"):
        bound_spec(episode, options=decision["parameters"], decision=decision)


@pytest.mark.parametrize("origin", ["registry_default", "fixture", "literature_default", "trial_selected"])
def test_agent_cannot_invent_a_nonagent_parameter_source(workspace, origin):
    episode, _ = create(workspace)
    decision = {"method": "gaussian", "why": "A proposed score criterion.", "parameters": {"selection_criterion": "bic"},
        "parameter_origins": [{"name": "selection_criterion", "value": "bic", "origin": origin, "rationale": "Claimed source"}]}
    with pytest.raises(ValueError, match="parameter_origin"):
        bound_spec(episode, options=decision["parameters"], decision=decision)


@pytest.mark.parametrize("field", ["inputs", "cohort", "design", "contrast", "source", "environment"])
def test_changed_dependency_cannot_reuse_original_design_receipt(workspace, field):
    episode, _ = create(workspace)
    original = bound_spec(episode).to_dict()
    changed = copy.deepcopy(original)
    changed[field]["changed_for_test"] = True
    api = spec_api()
    receipt = {"dependency_sha256": api.prerequisite_dependency(original, "validate_design_matrix")}
    with pytest.raises(ValueError, match="stale_scientific_prerequisite"):
        api.validate_prerequisite_binding(changed, receipt, "validate_design_matrix")
    assert original == bound_spec(episode).to_dict()


def test_typed_additive_design_rejects_formula_and_undeclared_terms():
    api = spec_api()
    roles = {"condition": "condition", "population_unit": "donor", "batches": ["batch"], "blocking": []}
    valid = {"format": "devbench.additive-design/1", "intercept": True,
        "terms": [{"role": "condition", "column": "condition"}, {"role": "batch", "column": "batch"}], "reduced": None}
    assert api.validate_additive_design(valid, roles) == valid
    for bad in ["~ condition + system('bad')", {**valid, "terms": [{"role": "condition", "column": "unknown"}]},
                {**valid, "terms": [{"role": "interaction", "column": "condition:batch"}]}]:
        with pytest.raises(ValueError):
            api.validate_additive_design(bad, roles)


def test_rank_estimability_and_lrt_nesting_are_checked():
    api = spec_api()
    full = [[1, 0], [1, 0], [1, 0], [1, 1], [1, 1], [1, 1]]
    assert api.validate_design_matrices(full, [0, 1], reduced=[[1]] * 6)["nested"] is True
    with pytest.raises(ValueError, match="rank_deficient"):
        api.validate_design_matrices([[1, 1]] * 6, [0, 1])
    with pytest.raises(ValueError, match="contrast"):
        api.validate_design_matrices(full, [0, 0])
    with pytest.raises(ValueError, match="nested"):
        api.validate_design_matrices(full, [0, 1], reduced=[[0], [1], [0], [1], [0], [1]])


def test_normal_episode_dispatch_receipt_and_decision_bind_one_specification(workspace):
    episode, _ = create(workspace)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix", "mean_condition_analysis"):
        response = episode.step({"kind": "tool", "tool": tool})
        assert response["status"] == "succeeded", response
    binding = response["scientific_execution"]
    checkpoint = episode.root / "checkpoints" / "action-0004"
    value = json.loads((checkpoint / "scientific-specification.json").read_text())
    receipt = json.loads((checkpoint / "scientific-execution.json").read_text())
    assert binding == receipt
    assert receipt["specification_id"] == spec_api().ScientificAnalysisSpec.from_dict(value).specification_id
    assert receipt["broker_receipt_id"] == response["receipt_id"]
    assert receipt["decision_sha256"]
    assert value["parameters"] == episode.broker.events[-1]["arguments"]["options"]
    assert receipt["result_id"] != receipt["specification_id"]
    assert receipt["prerequisites"]["validate_design_matrix"]["specification_id"]
    assert episode.step({"kind": "finish"})["outcome"] == "completed"


def test_nonidentifiable_design_is_rejected_before_fit_dispatch(workspace):
    episode, _ = create(workspace, confounded=True)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix"):
        episode.step({"kind": "tool", "tool": tool})
    response = episode.step({"kind": "tool", "tool": "mean_condition_analysis"})
    assert response["status"] == "rejected"
    assert not any(event["tool"] == "mean_condition_analysis" for event in episode.broker.events)


def test_synthesis_accepts_bound_agent_options_and_legacy_frozen_reader_remains(workspace):
    episode, _ = create(workspace)
    for tool in ("audit_expression_quality", "validate_biological_units"):
        assert episode.step({"kind": "tool", "tool": tool})["status"] == "succeeded"
    for tool in ("validate_design_matrix", "mean_condition_analysis"):
        assert episode.step({"kind": "tool", "tool": tool, "decision": {
            "method": "gaussian", "why": "Single-model BIC transport only.",
            "parameters": {"selection_criterion": "bic"}}})["status"] == "succeeded"
    assert episode.step({"kind": "finish"})["outcome"] == "completed"


def test_legacy_episode_with_no_specification_still_reads_frozen_options(workspace, monkeypatch):
    api = spec_api()
    monkeypatch.setattr(api, "resolve_episode_options", lambda tool, options, task, driver: options)
    monkeypatch.setattr(api, "bind_episode_spec", lambda *args: None)
    episode, _ = create(workspace)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix", "mean_condition_analysis"):
        assert episode.step({"kind": "tool", "tool": tool})["status"] == "succeeded"
    assert episode.step({"kind": "finish"})["outcome"] == "completed"


def test_receipt_rejects_execution_that_reports_a_different_engine(workspace, monkeypatch):
    episode, _ = create(workspace)
    execute = episode.broker.execute

    def mismatched(tool, arguments):
        response = execute(tool, arguments)
        response["result"]["method"]["version"] = "invented-other-engine"
        return response

    monkeypatch.setattr(episode.broker, "execute", mismatched)
    result = episode.step({"kind": "tool", "tool": "audit_expression_quality"})
    assert result["outcome"] == "infrastructure_failure"
    assert "scientific_execution" in result["reason"]


def test_explicit_option_transport_keeps_its_source_without_calling_it_a_default(workspace):
    episode, _ = create(workspace)
    response = episode.step({"kind": "tool", "tool": "validate_design_matrix", "options": {"selection_criterion": "bic"}})
    assert response["status"] == "succeeded"
    spec = json.loads((episode.root / "checkpoints/action-0001/scientific-specification.json").read_text())
    parameter = next(item for item in spec["parameter_origins"] if item["name"] == "selection_criterion")
    assert parameter["origin"] == "coordinator_action"
    assert parameter["source"] == "episode.action.options.selection_criterion"


def test_changed_dispatched_options_are_detected(workspace, monkeypatch):
    episode, _ = create(workspace)
    execute = episode.broker.execute

    def different_options(tool, arguments):
        arguments = copy.deepcopy(arguments)
        arguments["options"]["selection_criterion"] = "bic"
        return execute(tool, arguments)

    monkeypatch.setattr(episode.broker, "execute", different_options)
    result = episode.step({"kind": "tool", "tool": "validate_design_matrix"})
    assert result["outcome"] == "infrastructure_failure"
    assert "scientific_execution_method_or_options_receipt_mismatch" in result["reason"]


def test_prerequisite_rewritten_outside_audit_binding_cannot_admit_fit(workspace):
    from devbench.analysis.checkpoints import atomic_json, sha256
    episode, _ = create(workspace)
    for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix"):
        assert episode.step({"kind": "tool", "tool": tool})["status"] == "succeeded"
    folder = episode.root / "checkpoints/action-0003"
    receipt = json.loads((folder / "scientific-execution.json").read_text())
    receipt["result_sha256"] = "f" * 64
    atomic_json(folder / "scientific-execution.json", receipt)
    checkpoint = json.loads((folder / "receipt.json").read_text())
    checkpoint["artifacts"]["scientific-execution.json"] = sha256(folder / "scientific-execution.json")
    atomic_json(folder / "receipt.json", checkpoint)
    response = episode.step({"kind": "tool", "tool": "mean_condition_analysis"})
    assert response["status"] == "rejected"
    assert "scientific_prerequisite" in response["diagnostic"]
    assert not any(event["tool"] == "mean_condition_analysis" for event in episode.broker.events)


def test_capability_maturity_never_promotes_experimental_fit(workspace):
    episode, _ = create(workspace)
    capability = episode.observation()["tool_catalog"]["mean_condition_analysis"]["execution_capability"]
    assert capability["implemented"] is True
    assert capability["admission"] == "requires_matching_prerequisites"
    assert capability["scientifically_plausible"] == "requires_data_and_question_assessment"
    assert capability["numerically_checked"] is False
    assert capability["scientifically_qualified"] is False


def test_compact_context_retains_rejection_assumptions_and_measures_actual_prompt(workspace):
    from devbench.analysis.episode_agent import drive_episode
    from devbench.analysis.episode_codex import selection_instruction
    from tests.test_episode_agent import ScriptedSelector, sequence
    episode, _ = create(workspace)
    selector = ScriptedSelector([{"kind": "tool", "tool": "mean_condition_analysis"},
        {"kind": "revise", "reason": "Inspect prerequisites."}, *sequence()])
    assert drive_episode(episode, selector, allow_data_execution=True)["outcome"] == "completed"
    state = selector.contexts[-1]["scientific_state"]
    assert state["unresolved_assumptions"]
    assert state["failed_or_rejected_attempts"][0]["reason"].startswith("required_diagnostic_not_completed")
    assert state["cumulative_limits"]["actions_used"] == 6
    for index, context in enumerate(selector.contexts, 1):
        budget = json.loads((episode.root / "agent" / f"selection-{index:04d}" / "context-budget.json").read_text())
        assert budget["full_prompt_bytes"] == len(selection_instruction(context).encode("utf-8"))


def test_prompt_projects_older_history_to_receipt_references_and_retains_coordinator_evidence():
    from devbench.analysis.episode_codex import prompt_context
    catalog = {"fit": {"capability": {"description": "bounded registered capability " * 100}}}
    failed = {"action": {"kind": "tool", "tool": "fit"}, "observation": {
        "tool_catalog": copy.deepcopy(catalog), "allowed_tools": ["fit"], "status": "rejected",
        "receipt_id": "fit-1", "diagnostic": "unresolved_donor_identity",
        "result_summary": {"result_sha256": "a" * 64, "diagnostics": {"analysis_error": "rank_deficient"}}}}
    changed_catalog = {"action": {"kind": "tool", "tool": "old_fit"}, "observation": {
        "tool_catalog": {"old_fit": {"capability": {"description": "historically different"}}}, "status": "failed"}}
    context = {"observation": {"allowed_tools": ["fit"], "tool_catalog": catalog},
        "scientific_state": {"unresolved_assumptions": ["donor identity declared but unverified"]},
        "history": [failed, changed_catalog]}
    compact = prompt_context(context)
    assert "tool_catalog" not in compact["history"][0]["observation"]
    assert "allowed_tools" not in compact["history"][0]["observation"]
    assert compact["history"][0]["observation"] == {
        "receipt_id": "fit-1", "status": "rejected", "result_summary": {"result_sha256": "a" * 64}}
    assert compact["history"][1] == {"action": changed_catalog["action"], "observation": {"status": "failed"}}
    assert compact["scientific_state"] == context["scientific_state"]
    assert len(json.dumps(compact)) < len(json.dumps(context))
    assert "tool_catalog" in context["history"][0]["observation"]
    assert context["history"][0]["observation"]["diagnostic"] == "unresolved_donor_identity"


@pytest.mark.parametrize("tampered", [False, True])
def test_authoritative_context_retains_bound_prior_analyst_concerns(workspace, tampered):
    from devbench.analysis.checkpoints import atomic_json, sha256
    from devbench.analysis.episode_agent import authoritative_scientific_context
    episode, _ = create(workspace)
    folder = episode.root / "agent/decisions"
    folder.mkdir(parents=True)
    record = {"question": "Is donor support adequate?", "concern": "Sparse support remains unresolved.",
        "competing_explanations": ["Sparse observations", "A stable contrast"],
        "expected_discriminating_observation": "Check per-donor observed support.", "evidence_refs": ["action:1"]}
    path = folder / "selection-0001.json"
    atomic_json(path, {"record": record, "selection": 1})
    episode.decision_records = [{"selection": 1, "pre_action_sha256": sha256(path), "outcome_sha256": "a" * 64}]
    if tampered:
        atomic_json(path, {"record": {**record, "concern": "Silently replaced"}, "selection": 1})
        with pytest.raises(ValueError, match="scientific_context_decision_binding"):
            authoritative_scientific_context(episode, [], context_bytes_used=0)
    else:
        state = authoritative_scientific_context(episode, [], context_bytes_used=0)
        assert state["unresolved_assessments"][0]["record"] == record
        assert state["unresolved_assessments"][0]["status"] == "retained_concern_not_resolved_by_tool_success"
