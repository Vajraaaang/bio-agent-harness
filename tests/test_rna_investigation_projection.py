"""Receipt-backed bounded views; synthetic controls, no model/provider calls."""
from copy import deepcopy
import csv
import importlib
import json
from pathlib import Path

import pytest

from devharness_core.provenance import canonical_json
from devbench.analysis.methods import MethodService, _digest
from devbench.analysis.rna_investigation_contract import build_investigation_packet
from devbench.analysis.rna_investigations import retain_investigation_json
from devbench.analysis.workflow_operations import WorkflowOperationLedger
from tests.test_rna_investigation_budget import _policy, _capability
from tests.test_extensible_methods import custom


def projection():
    assert Path(__file__).resolve().parents[1].joinpath(
        "devbench/analysis/rna_investigation_projection.py").is_file(), "Receipt projection implementation is absent"
    return importlib.import_module("devbench.analysis.rna_investigation_projection")


def _ref(methods, identity):
    record = methods.get("artifact", identity)
    return {key: record[key] for key in ("artifact_id", "sha256", "artifact_type", "size_bytes")}


def _store_plan(methods, plan):
    with methods._db() as db:
        exists = db.execute("SELECT 1 FROM records WHERE kind='method_plan' AND id=?", (plan["session_id"],)).fetchone()
        methods._put(db, "method_plan", plan["session_id"], plan, update=bool(exists))
        identity = _digest(plan)
        if not db.execute("SELECT 1 FROM records WHERE kind='method_plan_snapshot' AND id=?", (identity,)).fetchone():
            methods._put(db, "method_plan_snapshot", identity, plan)


def _freeze_policy(methods, plan, root, selection, *, stage=None):
    capability = _capability()
    policy = {"policy_digest": "3" * 64, "source_digest": capability["source_digest"],
              "runtime_digest": capability["runtime_digest"], "max_context_bytes": 128_000,
              "question_record": _question_record(plan)}
    if selection is not None:
        policy["statistics_projection"] = selection
    if stage is not None:
        policy["stage"] = stage
    frozen = {key: value for key, value in policy.items() if key != "policy_digest"}
    policy["policy_digest"] = _digest(frozen)
    _store_plan(methods, plan)
    ledger = WorkflowOperationLedger(root / "operations.sqlite")
    campaign = ledger.open_campaign("fixture-campaign", session_id=plan["session_id"], policy=_policy(),
        capability=capability, identity={"investigation_policy": frozen,
            "investigation_policy_digest": policy["policy_digest"], "expected_plan_hash": _digest(plan)})
    plan["model_campaign"] = {"format": "devbench.planner-model-campaign/1", "campaign_id": campaign["campaign_id"],
        "ledger_path": str(ledger.path), **{key: campaign[key] for key in
            ("policy_digest", "capability_digest", "identity_digest")},
        **{key: campaign["capability"][key] for key in ("backend_digest", "source_digest", "runtime_digest")}}
    _store_plan(methods, plan)
    return policy


def _base_plan(artifact_ids=()):
    return {"session_id": "1" * 32, "context": {"task": {
        "scientific_objective": "Describe temporal associations in synthetic measurements",
        "computational_task": "Inspect actual shared outputs"}, "dataset_summary": {
            "observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": list(artifact_ids)}},
        "data_classification": "synthetic", "limits": {"max_context_bytes": 128_000},
        "user_parameter_requirements": [], "experiments": [], "results": []}


def _declare(plan, step, name="synthetic-fit"):
    experiment = {"id": name, "status": "declared", "candidates": [name],
        "step_ids": {name: step["step_id"]}, "parameters": {name: step["parameters"]},
        "inputs": {name: step["inputs"]}, "metrics": [], "metric_sources": {}}
    experiment["declaration_sha256"] = _digest(experiment)
    plan["experiments"].append(experiment)


def _complete(methods, plan, run):
    name = plan["experiments"][-1]["id"]
    plan["experiments"][-1]["status"] = "completed"
    plan["results"].append({"experiment_id": name, "candidate": name, "status": "success",
        "metric_values": {}, "artifacts": [{"artifact_id": identity, "role": role, "immutable": True}
            for role, identity in run["outputs"].items()], "provenance": {
                "run_id": run["run_id"], "evidence_root": run["evidence"]["root_hash"],
                "parameters": run["parameters"]}})
    _store_plan(methods, plan)


@pytest.fixture(scope="module")
def actual_fit(tmp_path_factory):
    """One shared small ToolDispatcher fit supplies every read-only adversarial view."""
    root = tmp_path_factory.mktemp("actual-projection-fit")
    methods = MethodService(root / "methods")
    counts, metadata = root / "expression.csv", root / "metadata.csv"
    genes = ["zeta", "alpha", "middle"]
    samples = ["s" + str(i) for i in range(12)]
    with counts.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["gene", *samples])
        for offset, gene in enumerate(genes):
            writer.writerow([gene, *[float(10 + offset * 2 + i * .35 + ((i + offset) % 3) * .4) for i in range(12)]])
    with metadata.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sample_id", "embryonic_age", "donor_id"])
        for i, sample in enumerate(samples):
            writer.writerow([sample, str(8 + i // 3), "donor-" + str(i)])
    inputs = {"counts": methods.import_artifact(counts, artifact_type="file", actor="fixture")["artifact_id"],
              "metadata": methods.import_artifact(metadata, artifact_type="table", actor="fixture")["artifact_id"]}
    method = methods.register_dispatcher("fit_temporal_trajectory", actor="fixture")
    analysis = methods.create_analysis("Synthetic projection receipt", actor="fixture")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="fixture")
    step = methods.add_step(branch["branch_id"], method["version_id"], inputs,
        {"formula_str": "1 + numeric_time", "time_col": "embryonic_age", "gene_list": genes,
         "selection_criterion": "fixed", "spline_dfs": [2], "data_type": "continuous"}, actor="fixture")
    plan = _base_plan(inputs.values())
    _declare(plan, step)
    policy = _freeze_policy(methods, plan, root,
        {"rule": "deterministic_head", "max_rows": 2, "requirement_name": None})
    run = methods.run_step(step["step_id"], actor="fixture")
    assert run["status"] == "succeeded", run.get("error")
    _complete(methods, plan, run)
    return methods, plan, policy, run, genes


def _packet(methods, plan, policy, overlay):
    evidence = {"question_record": deepcopy(policy.get("question_record", _question_record(plan))),
        "facts": {key: [] for key in ("observed", "declared", "inferred", "unknown")},
        "evidence_refs": overlay["evidence_refs"], "diagnostic_refs": overlay["diagnostic_refs"],
        "allowed_read_selectors": [], "verified_artifacts": overlay["verified_artifacts"]}
    for category, facts in overlay["facts"].items():
        evidence["facts"][category].extend(facts)
    return build_investigation_packet(plan, evidence, stage="post_fit_explanation",
        task="independent_explanation_investigation", policy=policy)


def _question_record(plan):
    return {"objective": plan["context"]["task"]["scientific_objective"],
        "estimand": "Association with declared collection time", "action": "infer",
        "source": {"kind": "caller_declaration", "reference": "synthetic-fixture-question"}, "evidence_refs": []}


def _stats(packet):
    return next(fact for fact in packet["facts"]["observed"] if fact["field"] == "gene_statistics")


def test_actual_fit_projects_declared_head_without_rerunning(actual_fit):
    methods, plan, policy, run, genes = actual_fit
    module = projection()
    overlay = module.project_verified_outputs(methods, plan, policy=policy)
    packet = _packet(methods, plan, policy, overlay)
    assert overlay["assessment_available"] is True
    stats = _stats(packet)["value"]
    assert [row["hypothesis_id"] for row in stats["rows"]] == genes[:2]
    assert stats["tested_hypotheses"] == 3 and stats["represented_hypotheses"] == 2
    assert stats["whole_family_refs"] == [_ref(methods, run["outputs"]["result"])]
    original = json.loads(methods.artifact_path(run["outputs"]["result"]).read_bytes())
    assert original["family"] == "gaussian" and original["normalization_method"] == "none"
    assert stats["rows"][0]["p_value"] == original["genes"][genes[0]]["p_value"]
    assert module.verify_packet_projection(methods, packet, plan, policy=policy) == packet
    assert len(methods.list_records("run")) == 1


@pytest.mark.parametrize("change", ["membership", "order", "value", "total", "failed_count", "missing_count"])
def test_hash_valid_forged_compact_views_reject(actual_fit, change):
    methods, plan, policy, _, _ = actual_fit
    module = projection()
    packet = _packet(methods, plan, policy, module.project_verified_outputs(methods, plan, policy=policy))
    fact = _stats(packet)
    stats = fact["value"]
    if change == "membership":
        stats["rows"][0]["hypothesis_id"] = "middle"
    elif change == "order":
        stats["rows"].reverse()
    elif change == "value":
        stats["rows"][0]["p_value"] /= 2
    elif change == "total":
        stats["tested_hypotheses"] += 1
        stats["missingness"]["unrepresented_hypotheses"] += 1
    elif change == "failed_count":
        stats["failed_estimates"] += 1
    else:
        stats["missingness"]["unrepresented_hypotheses"] -= 1
    # The fake compact artifact is genuine and has a matching hash; its science is false.
    reference = retain_investigation_json(methods, {"gene_statistics": stats}, actor="fixture-forgery")
    packet["evidence_refs"].append(reference)
    fact["source"]["reference"] = reference["artifact_id"] + "#/gene_statistics"
    fact["evidence_refs"] = [reference]
    with pytest.raises(ValueError):
        module.verify_packet_projection(methods, packet, plan, policy=policy)


@pytest.mark.parametrize("change", ["hash", "size", "type", "unexecuted", "foreign", "transport"])
def test_whole_family_refs_must_be_exact_current_run_outputs(actual_fit, change):
    methods, plan, policy, run, _ = actual_fit
    module = projection()
    packet = _packet(methods, plan, policy, module.project_verified_outputs(methods, plan, policy=policy))
    reference = deepcopy(_stats(packet)["value"]["whole_family_refs"][0])
    if change == "hash":
        reference["sha256"] = "0" * 64
    elif change == "size":
        reference["size_bytes"] += 1
    elif change == "type":
        reference["artifact_type"] = "table"
    elif change == "unexecuted":
        reference = _ref(methods, run["inputs"]["counts"])
    elif change == "foreign":
        reference = retain_investigation_json(methods, {"genes": {"outside": {"p_value": .1, "q_value": .1}}}, actor="fixture")
    else:
        reference = retain_investigation_json(methods, {"observations": "not scientific output"},
            actor="fixture", artifact_type="rna_investigation_capture")
    _stats(packet)["value"]["whole_family_refs"] = [reference]
    if reference not in packet["evidence_refs"]:
        packet["evidence_refs"].append(reference)
    with pytest.raises(ValueError):
        module.verify_packet_projection(methods, packet, plan, policy=policy)


def test_projection_limit_cannot_change_after_results(actual_fit):
    methods, plan, policy, _, _ = actual_fit
    changed = deepcopy(policy)
    changed["statistics_projection"]["max_rows"] = 1
    with pytest.raises(ValueError, match="frozen|policy|projection"):
        projection().project_verified_outputs(methods, plan, policy=changed)


def test_missing_projection_policy_explicitly_retains_absent_assessment(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics(), selection=False)
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    assert overlay["assessment_available"] is False
    assert not any(fact["field"] == "gene_statistics" for fact in overlay["facts"]["observed"])
    assert overlay["facts"]["unknown"]


def test_absent_results_never_become_ready(tmp_path):
    methods = MethodService(tmp_path / "methods")
    plan = _base_plan()
    _store_plan(methods, plan)
    overlay = projection().project_verified_outputs(methods, plan,
        policy={"statistics_projection": {"rule": "all", "max_rows": 16, "requirement_name": None}})
    assert overlay["assessment_available"] is False
    assert overlay["facts"]["observed"] == []


def test_read_only_verification_has_no_retaining_path(actual_fit):
    methods, plan, policy, _, _ = actual_fit
    module = projection()
    packet = _packet(methods, plan, policy, module.project_verified_outputs(methods, plan, policy=policy))
    before = len(methods.list_records("artifact"))
    reader = MethodService(methods.root, readonly=True)
    assert module.verify_packet_projection(reader, packet, plan, policy=policy) == packet
    assert len(methods.list_records("artifact")) == before
    with pytest.raises(PermissionError):
        module.project_verified_outputs(reader, plan, policy=policy)


def _fixture_receipt(tmp_path, document, *, genes=None, selection=None, requirements=None, kind="json", declare_gene_order=True, stage=None):
    """Static software-control output with a real immutable execution receipt."""
    genes = genes or ["third", "first", "second"]
    methods = MethodService(tmp_path / "methods")
    contract, source, lock = custom(tmp_path)
    contract["parameters"]["properties"]["genes"] = {"type": "array", "items": {"type": "string"}}
    contract["outputs"] = {"result": {"artifact_type": kind, "path": "/outputs/result." + ("json" if kind == "json" else "csv"), "required": True}}
    method = methods.register_method(contract, source=source, environment_lock=lock, actor="fixture")
    input_path = tmp_path / "fixture-input.json"
    input_path.write_bytes(canonical_json({"gene_order": genes, "fixture": "software-control"}))
    input_ref = methods.import_artifact(input_path, artifact_type="json", actor="fixture")
    analysis = methods.create_analysis("Static statistics software control", actor="fixture")
    branch = methods.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="fixture")
    parameters = {"threshold": 4, **({"genes": genes} if declare_gene_order else {})}
    step = methods.add_step(branch["branch_id"], method["version_id"], {"dataset": input_ref["artifact_id"]},
        parameters, actor="fixture")
    plan = _base_plan([input_ref["artifact_id"]])
    plan["user_parameter_requirements"] = requirements or []
    _declare(plan, step)
    selected_policy = None if selection is False else selection or {"rule": "all", "max_rows": 16, "requirement_name": None}
    policy = _freeze_policy(methods, plan, tmp_path, selected_policy, stage=stage)

    class StaticExecutor:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            name = Path(contract["outputs"]["result"]["path"]).name
            encoded = canonical_json(document) if kind == "json" else document.encode()
            (output_dir / name).write_bytes(encoded)
            return {"exit_code": 0, "runtime": "static_statistics_software_fixture"}

    executor = StaticExecutor()
    run = methods.run_step(step["step_id"], actor="fixture", executor=executor)
    assert run["status"] == "succeeded", run.get("error")
    _complete(methods, plan, run)
    return methods, plan, policy, run, step, executor


def _fixture_statistics():
    return {"genes": {"third": {"status": "ok", "p_value": .2, "q_value": .6, "effect_difference": -.5},
        "first": {"status": "failed", "failure_code": "optimizer_nonconverged", "p_value": None, "q_value": None},
        "second": {"status": "not_estimated", "reason": "Missing estimate", "p_value": None, "q_value": None}},
        "selection_criterion": "fixed", "family": "gaussian", "normalization_method": "none",
        "fdr_scope": "requested_genes", "fdr_methods": {"q_value": "benjamini_hochberg", "padj": "benjamini_hochberg"},
        "inference_scope": "Software-control association scope only; no p-value calibration qualification"}


def test_whole_family_keeps_failed_nonconverged_and_missing_counts(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    packet = _packet(methods, plan, policy, overlay)
    stats = _stats(packet)["value"]
    assert stats["tested_hypotheses"] == 3 and stats["failed_estimates"] == 2
    assert stats["rows"][1]["p_value"] is None and stats["rows"][1]["q_value"] is None
    assert "effect" not in stats["rows"][1] and "nonconverged" in stats["rows"][1]["reason"]
    summary = next(fact["value"] for fact in overlay["facts"]["observed"] if fact["field"] == "fitted_family_coverage")
    assert summary["nonconverged_estimates"] == 1 and summary["missing_estimates"] == 2
    assert projection().verify_packet_projection(methods, packet, plan, policy=policy) == packet


@pytest.mark.parametrize("bad", ["q_less_than_p", "boolean_p", "numeric_failure", "invented_effect", "alias_disagreement"])
def test_actual_malformed_statistical_outputs_fail_explicitly(tmp_path, bad):
    document = _fixture_statistics()
    if bad == "q_less_than_p":
        document["genes"]["third"]["q_value"] = .1
    elif bad == "boolean_p":
        document["genes"]["third"]["p_value"] = True
    elif bad == "numeric_failure":
        document["genes"]["first"].update(p_value=0, q_value=0)
    elif bad == "invented_effect":
        document["genes"]["first"]["effect_difference"] = 0
    else:
        document["genes"]["third"]["padj"] = .7
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_direct_user_gene_record_preserves_exact_requested_order(tmp_path):
    requirement = {"name": "report_genes", "value": ["second", "third"],
        "evidence": "Direct user request: report second and third, in that order"}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics(),
        selection={"rule": "user_requirement", "max_rows": 2, "requirement_name": "report_genes"},
        requirements=[requirement])
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    assert [row["hypothesis_id"] for row in _stats(packet)["value"]["rows"]] == requirement["value"]
    assert projection().verify_packet_projection(methods, packet, plan, policy=policy) == packet


@pytest.mark.parametrize("evidence", ["Assistant suggestion", "Fixture authored genes", "Default genes"])
def test_assistant_fixture_default_cannot_become_direct_user_gene_requirement(tmp_path, evidence):
    requirement = {"name": "report_genes", "value": ["third"], "evidence": evidence}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics(),
        selection={"rule": "user_requirement", "max_rows": 1, "requirement_name": "report_genes"},
        requirements=[requirement])
    with pytest.raises(ValueError, match="user|requirement"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_shared_qc_receipt_is_not_an_actual_fitted_gene_family(tmp_path):
    methods, plan, policy, run, *_ = _fixture_receipt(tmp_path, {"threshold": 4})
    assert projection().project_verified_outputs(methods, plan, policy=policy)["assessment_available"] is False
    raw = _ref(methods, run["outputs"]["result"])
    value = {"coverage": "whole_family", "selection": {"rule": "all", "origin": "coordinator", "requirement_name": None},
        "whole_family_refs": [raw], "tested_hypotheses": 1, "represented_hypotheses": 1, "failed_estimates": 0,
        "missingness": {"unrepresented_hypotheses": 0, "failed_estimates_retained": True},
        "rows": [{"hypothesis_id": "invented", "estimate_status": "estimated", "p_value": 1e-40, "q_value": 1e-40, "effect": 99}]}
    doc = {"gene_statistics": value}
    compact = retain_investigation_json(methods, doc, actor="fixture-forgery")
    fact = {"field": "gene_statistics", "value": value, "source": {"kind": "coordinator_artifact",
        "reference": compact["artifact_id"] + "#/gene_statistics"}, "evidence_refs": [compact], "limitation": None}
    overlay = {"facts": {"observed": [fact]}, "evidence_refs": [raw, compact], "diagnostic_refs": [compact],
        "verified_artifacts": [{"ref": compact, "value": doc}]}
    packet = _packet(methods, plan, policy, overlay)
    with pytest.raises(ValueError, match="family|fitted"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy)


def test_all_current_fitted_branches_remain_distinct_without_new_bh(tmp_path):
    methods, plan, policy, _, step, executor = _fixture_receipt(tmp_path, _fixture_statistics())
    _declare(plan, step, "second-fit")
    _store_plan(methods, plan)
    second = methods.run_step(step["step_id"], actor="fixture", executor=executor)
    _complete(methods, plan, second)
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    packet = _packet(methods, plan, policy, overlay)
    stats = _stats(packet)["value"]
    assert stats["tested_hypotheses"] == 6 and stats["failed_estimates"] == 4
    assert len(stats["whole_family_refs"]) == 2
    assert len({row["hypothesis_id"] for row in stats["rows"]}) == 6
    assert stats["rows"][0]["q_value"] == stats["rows"][3]["q_value"] == .6
    summary = next(fact["value"] for fact in overlay["facts"]["observed"] if fact["field"] == "fitted_family_coverage")
    assert "no combined" in summary["multiplicity_scope"] and len(summary["per_run"]) == 2
    assert projection().verify_packet_projection(methods, packet, plan, policy=policy) == packet


def test_whole_family_overflow_is_explicit_not_truncated(tmp_path, monkeypatch):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    monkeypatch.setattr(projection(), "FULL_FAMILY_MAX_BYTES", 64)
    with pytest.raises(ValueError, match="byte bound"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_all_failed_outputs_are_not_ready_for_explanation(tmp_path):
    document = _fixture_statistics()
    document["genes"]["third"] = {"status": "failed", "reason": "No fitted estimate", "p_value": None, "q_value": None}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    assert overlay["assessment_available"] is False
    assert not overlay["facts"]["observed"]


def test_retained_pre_attachment_snapshot_can_be_verified_after_parent_advancement(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    current = deepcopy(plan)
    current["investigation_evidence"] = [{"status": "attached"}]
    current["selection_history"] = [{"phase": "reconciliation", "status": "accepted"}]
    _store_plan(methods, current)
    assert projection().verify_packet_projection(methods, packet, plan, policy=policy) == packet


def test_unretained_or_changed_numerical_snapshot_is_rejected(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    current = deepcopy(plan)
    current["results"][0]["status"] = "failed"
    _store_plan(methods, current)
    with pytest.raises(ValueError, match="snapshot|numerical|trial|changed"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy)


def test_fitted_numbers_cannot_be_smuggled_under_another_observed_field(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, {"threshold": 4})
    value = {"p_value": 1e-40, "q_value": 1e-40, "effect": 99}
    document = {"renamed_findings": value}
    reference = retain_investigation_json(methods, document, actor="fixture-forgery")
    fact = {"field": "renamed_findings", "value": value, "source": {"kind": "coordinator_artifact",
        "reference": reference["artifact_id"] + "#/renamed_findings"}, "evidence_refs": [reference], "limitation": None}
    packet = _packet(methods, plan, policy, {"facts": {}, "evidence_refs": [reference],
        "diagnostic_refs": [], "verified_artifacts": [{"ref": reference, "value": document}]})
    packet["facts"]["observed"].append(fact)
    with pytest.raises(ValueError, match="fitted|statistics|projection"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy)


def test_missing_branch_statistics_cannot_be_called_complete_coverage(tmp_path):
    methods, plan, policy, _, step, _ = _fixture_receipt(tmp_path, _fixture_statistics())
    _declare(plan, step, "unavailable-fit")
    _store_plan(methods, plan)

    class MissingOutputExecutor:
        def execute(self, contract, inputs, parameters, source, lock, output_dir):
            (output_dir / "result.json").write_bytes(canonical_json({"status": "failed", "reason": "No fitted output available"}))
            return {"exit_code": 0, "runtime": "missing_statistics_software_fixture"}

    run = methods.run_step(step["step_id"], actor="fixture", executor=MissingOutputExecutor())
    _complete(methods, plan, run)
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    assert overlay["assessment_available"] is True
    assert _stats(_packet(methods, plan, policy, overlay))["value"]["coverage"] == "bounded_projection"
    assert any(fact["field"] == "statistics_projection_assessment" for fact in overlay["facts"]["unknown"])


@pytest.mark.parametrize("field,value", [("q_value", 1e-40), ("fitted_family_coverage", {"p_value": 1e-40})])
def test_scalar_or_coverage_only_fitted_number_smuggling_is_rejected(tmp_path, field, value):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, {"threshold": 4})
    document = {field: value}
    reference = retain_investigation_json(methods, document, actor="fixture-forgery")
    fact = {"field": field, "value": value, "source": {"kind": "coordinator_artifact",
        "reference": reference["artifact_id"] + "#/" + field}, "evidence_refs": [reference], "limitation": None}
    packet = _packet(methods, plan, policy, {"facts": {}, "evidence_refs": [reference],
        "diagnostic_refs": [], "verified_artifacts": [{"ref": reference, "value": document}]})
    packet["facts"]["observed"].append(fact)
    with pytest.raises(ValueError, match="fitted|statistics|projection|coverage"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy)


def test_outcome_selected_model_cannot_deny_conditional_inference(tmp_path):
    document = _fixture_statistics()
    document["selection_criterion"] = "aic"
    document["genes"]["third"]["selection_conditional"] = False
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="conditional|selection"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_other_adjustments_cannot_be_called_bh_q_values(tmp_path):
    document = _fixture_statistics()
    document["fdr_methods"] = {"q_value": "holm"}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="BH|adjustment|q-value"):
        projection().project_verified_outputs(methods, plan, policy=policy)


@pytest.mark.parametrize("layout", ["list", "table"])
def test_typed_lists_and_statistical_tables_preserve_declared_order(tmp_path, layout):
    if layout == "list":
        document = {"gene_statistics": [{"gene_id": "first", "status": "ok", "p_value": .4, "q_value": .6},
            {"gene_id": "second", "status": "ok", "p_value": .1, "q_value": .3},
            {"gene_id": "third", "status": "ok", "p_value": .8, "q_value": .8}],
            "fdr_methods": {"q_value": "benjamini_hochberg"}, "fdr_scope": "requested_genes"}
        kind = "json"
    else:
        document = "gene_id,status,p_value,q_value,q_value_method,fdr_scope\nfirst,ok,0.4,0.6,benjamini_hochberg,requested_genes\nsecond,ok,0.1,0.3,benjamini_hochberg,requested_genes\nthird,ok,0.8,0.8,benjamini_hochberg,requested_genes\n"
        kind = "table"
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document, kind=kind,
        selection={"rule": "deterministic_head", "max_rows": 2, "requirement_name": None})
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    assert [row["hypothesis_id"] for row in _stats(packet)["value"]["rows"]] == ["third", "first"]
    assert projection().verify_packet_projection(methods, packet, plan, policy=policy) == packet


def test_damaged_original_whole_family_bytes_are_rejected(tmp_path):
    methods, plan, policy, run, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    path = methods.artifact_path(run["outputs"]["result"])
    path.chmod(0o600)
    path.write_bytes(b'{"genes":{}}')
    with pytest.raises(ValueError, match="artifact|hash|evidence"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy)


def test_policy_created_after_actual_run_is_rejected(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    plan.pop("model_campaign")
    late = _freeze_policy(methods, plan, tmp_path / "late-policy",
        {"rule": "deterministic_head", "max_rows": 2, "requirement_name": None})
    with pytest.raises(ValueError, match="after|frozen"):
        projection().project_verified_outputs(methods, plan, policy=late)


@pytest.mark.parametrize("binding", ["binding_digest", "policy_digest"])
def test_projection_packet_binding_must_match_plan_and_frozen_policy(tmp_path, binding):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    packet["bindings"][binding] = "0" * 64
    with pytest.raises(ValueError, match="binding|policy|packet"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy)


def test_head_uses_immutable_input_gene_order_not_postfit_order_label(tmp_path):
    document = _fixture_statistics()
    document["gene_order"] = ["first", "second", "third"]
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document, declare_gene_order=False,
        selection={"rule": "deterministic_head", "max_rows": 2, "requirement_name": None})
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    packet = _packet(methods, plan, policy, overlay)
    assert [row["hypothesis_id"] for row in _stats(packet)["value"]["rows"]] == ["third", "first"]


def test_whole_family_bound_precedes_planner_metric_reads(tmp_path, monkeypatch):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    calls = []
    original = ScientificMethodPlanner._verified_results

    def observe(self, state):
        calls.append("existing-verifier")
        return original(self, state)

    monkeypatch.setattr(ScientificMethodPlanner, "_verified_results", observe)
    monkeypatch.setattr(projection(), "FULL_FAMILY_MAX_BYTES", 64)
    with pytest.raises(ValueError, match="byte bound"):
        projection().project_verified_outputs(methods, plan, policy=policy)
    assert calls == [], "The inherited metric reader ran before the scientific output cap"


@pytest.mark.parametrize("binding", ["source_digest", "runtime_digest"])
@pytest.mark.parametrize("supply_policy", [True, False])
def test_packet_source_runtime_are_bound_even_without_caller_policy(tmp_path, binding, supply_policy):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    packet["bindings"][binding] = "0" * 64
    with pytest.raises(ValueError, match="identity|source|runtime|binding|policy"):
        projection().verify_packet_projection(methods, packet, plan, policy=policy if supply_policy else None)


@pytest.mark.parametrize("field,value", [("source_digest", "0" * 64), ("runtime_digest", "0" * 64),
    ("max_context_bytes", 90000), ("extra_flag", True)])
def test_entire_supplied_policy_matches_frozen_body_not_only_its_label(tmp_path, field, value):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    forged = deepcopy(policy)
    forged[field] = value
    with pytest.raises(ValueError, match="policy|frozen|binding|identity"):
        projection().verify_packet_projection(methods, packet, plan, policy=forged)


@pytest.mark.parametrize("field,value", [("objective", "Choose a clinically approved causal intervention"),
    ("estimand", "Causal effect in all patients"), ("action", "exclude"),
    ("source", {"kind": "user_requirement", "reference": "Invented user instruction"})])
def test_question_record_retains_exact_frozen_objective_estimand_action_and_source(tmp_path, field, value):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    packet["question_record"][field] = value
    with pytest.raises(ValueError, match="question|objective|scope|binding"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


@pytest.mark.parametrize("which", ["input", "source", "environment"])
def test_current_method_store_inputs_and_sources_are_reverified(tmp_path, which):
    methods, plan, policy, run, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    method = methods.get("method", run["method_version_id"])
    identity = (next(iter(run["inputs"].values())) if which == "input" else
                method["source_artifact"] if which == "source" else method["environment_artifact"])
    path = methods.artifact_path(identity)
    path.chmod(0o600)
    path.write_bytes(b'{"tampered":true}')
    with pytest.raises(ValueError, match="hash|artifact|source|environment|digest|changed"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


@pytest.mark.parametrize("key", ["ｐ_value", "ｑ_value", "ｅffect", "𝐩_value", "𝑞_value"])
def test_nfkc_fitted_aliases_cannot_smuggle_qc_findings(tmp_path, key):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, {"threshold": 4})
    document = {"renamed_findings": {key: .01}}
    reference = retain_investigation_json(methods, document, actor="fixture-forgery")
    fact = {"field": "renamed_findings", "value": document["renamed_findings"], "source": {"kind": "coordinator_artifact",
        "reference": reference["artifact_id"] + "#/renamed_findings"}, "evidence_refs": [reference], "limitation": None}
    packet = _packet(methods, plan, policy, {"facts": {}, "evidence_refs": [reference],
        "diagnostic_refs": [], "verified_artifacts": [{"ref": reference, "value": document}]})
    packet["facts"]["observed"].append(fact)
    with pytest.raises(ValueError, match="fitted|statistics|projection|field"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


def _prefit_packet(methods, plan, policy):
    before = deepcopy(plan)
    before["experiments"][0]["status"] = "declared"
    before["results"] = []
    evidence = {"question_record": deepcopy(policy["question_record"]),
        "facts": {key: [] for key in ("observed", "declared", "inferred", "unknown")},
        "evidence_refs": [], "diagnostic_refs": [], "allowed_read_selectors": [], "verified_artifacts": []}
    packet = build_investigation_packet(before, evidence, stage="pre_fit_methods",
        task="independent_method_investigation", policy=policy)
    return before, packet


def test_historical_prefit_packet_survives_verified_trial_progress(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    before, packet = _prefit_packet(methods, plan, policy)
    reader = MethodService(methods.root, readonly=True)
    assert projection().verify_packet_projection(reader, packet, before, policy=None) == packet


def test_historical_prefit_packet_cannot_survive_changed_data_or_requirements(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    before, packet = _prefit_packet(methods, plan, policy)
    changed = deepcopy(plan)
    changed["context"]["task"]["scientific_objective"] = "A different scientific question"
    _store_plan(methods, changed)
    with pytest.raises(ValueError, match="snapshot|binding|changed|question"):
        projection().verify_packet_projection(methods, packet, before, policy=None)


def test_statistical_metadata_aliases_preserve_exact_biological_gene_ids(tmp_path):
    genes = ["Ｇene", "Gene", "δ"]
    records = {gene: {"status": "ok", "ｐ_value": .1, "ｑ_value": .1, "ｅffect": .5} for gene in genes}
    document = {"genes": records, "selection_criterion": "fixed", "fdr_scope": "requested_genes",
        "fdr_methods": {"ｑ_value": "benjamini_hochberg"}}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document, genes=genes)
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    rows = _stats(packet)["value"]["rows"]
    assert [row["hypothesis_id"] for row in rows] == genes
    assert all(row["p_value"] == .1 and row["q_value"] == .1 and row["effect"] == .5 for row in rows)
    assert projection().verify_packet_projection(methods, packet, plan, policy=None) == packet


def test_normalized_statistical_field_collisions_are_rejected(tmp_path):
    document = _fixture_statistics()
    document["genes"]["third"]["ｐ_value"] = document["genes"]["third"]["p_value"]
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="canonical|alias|statistic"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_canonical_gene_statistics_fact_alias_still_receives_full_family_verification(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    _stats(packet)["field"] = "rna.gene_statistics_projection"
    assert projection().verify_packet_projection(methods, packet, plan, policy=None) == packet


def test_historical_postfit_packet_rejects_even_legitimate_new_fitted_branch(tmp_path):
    methods, plan, policy, _, step, executor = _fixture_receipt(tmp_path, _fixture_statistics())
    before = deepcopy(plan)
    packet = _packet(methods, before, policy, projection().project_verified_outputs(methods, before, policy=policy))
    _declare(plan, step, "later-fit")
    _store_plan(methods, plan)
    run = methods.run_step(step["step_id"], actor="fixture", executor=executor)
    _complete(methods, plan, run)
    with pytest.raises(ValueError, match="post-fit|snapshot|numerical|changed"):
        projection().verify_packet_projection(methods, packet, before, policy=None)


def test_packet_data_classification_cannot_differ_from_actual_plan(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, _fixture_statistics())
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    packet["scope"]["data_classification"] = "public"
    with pytest.raises(ValueError, match="classification|scope|binding"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


def test_packet_investigation_stage_must_match_the_frozen_policy(tmp_path):
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, {"threshold": 4}, stage="post_fit_explanation")
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    packet = _packet(methods, plan, policy, overlay)
    packet["stage"] = "pre_fit_methods"
    packet["task"] = "independent_method_investigation"
    with pytest.raises(ValueError, match="stage|scope|policy|binding"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


def _qc_summary_packet(tmp_path, value):
    document = {"quality_summary": value}
    genes = value.get("gene_ids") if type(value) is dict else None
    methods, plan, policy, run, *_ = _fixture_receipt(tmp_path, document, genes=genes)
    reference = _ref(methods, run["outputs"]["result"])
    packet = _packet(methods, plan, policy, {"facts": {}, "evidence_refs": [reference],
        "diagnostic_refs": [], "verified_artifacts": [{"ref": reference, "value": document}]})
    packet["facts"]["observed"].append({"field": "quality_summary", "value": value,
        "source": {"kind": "coordinator_artifact", "reference": reference["artifact_id"] + "#/quality_summary"},
        "evidence_refs": [reference], "limitation": "QC execution and diagnostic values are not qualified gene inference"})
    return methods, plan, policy, packet


@pytest.mark.parametrize("value", [
    {"p_values": [1e-40], "q_values": [1e-40], "effects": [99], "hypothesis_ids": ["invented"]},
    {"pvals": [1e-40]}, {"q_values": {"nested": [1e-40]}}, {"effect_sizes": [99]},
    {"effect_differences": [99]}, {"fold_changes": [99]}, {"log2_fold_changes": [99]},
    {"wald_statistics": [99]}, {"test_statistics": [99]},
    {"ｐ_values": [1e-40]}, {"q_values": [False]},
    {"hypothesis_ids": ["invented"], "values": [1e-40], "weights": [99]},
])
def test_qc_receipt_cannot_qualify_plural_or_structured_hypothesis_statistics(tmp_path, value):
    methods, plan, _, packet = _qc_summary_packet(tmp_path, value)
    with pytest.raises(ValueError, match="fitted|statistic|family|numerical|scope"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


def test_projection_guard_checks_statistical_arrays_independently_of_packet_shape_validation(tmp_path, monkeypatch):
    value = {"hypothesis_ids": ["invented"], "p_values": [1e-40], "q_values": [1e-40], "effects": [99]}
    methods, plan, _, packet = _qc_summary_packet(tmp_path, value)
    monkeypatch.setattr(projection(), "validate_investigation_packet", deepcopy)
    with pytest.raises(ValueError, match="fitted|statistic|family|numerical|scope"):
        projection().verify_packet_projection(methods, packet, plan, policy=None)


def test_qc_array_diagnostics_and_statistic_labels_keep_limited_scope_and_exact_ids(tmp_path):
    value = {"gene_ids": ["Ｇene", "Gene"], "library_sizes": [12, 18], "zero_fractions": [.1, .2],
        "warning_flags": [False, True], "statistic_names": ["p_value", "q_value", "effect"],
        "p_values": ["not supplied"], "effects": ["not assessed"],
        "q_value": "Benjamini-Hochberg adjustment description"}
    methods, plan, _, packet = _qc_summary_packet(tmp_path, value)
    verified = projection().verify_packet_projection(methods, packet, plan, policy=None)
    assert verified == packet
    assert verified["facts"]["observed"][0]["value"]["gene_ids"] == ["Ｇene", "Gene"]
    assert "not qualified gene inference" in verified["facts"]["observed"][0]["limitation"]


def test_hypothesis_labels_without_numerical_findings_are_not_gene_estimates(tmp_path):
    value = {"hypothesis_ids": ["future-QC-question"], "labels": ["unresolved"], "assessment": "not fitted"}
    methods, plan, _, packet = _qc_summary_packet(tmp_path, value)
    assert projection().verify_packet_projection(methods, packet, plan, policy=None) == packet


@pytest.mark.parametrize("alias", ["qvalue", "ｑ_value", "Q Value", "padj"])
def test_fdr_metadata_alias_cannot_relabel_holm_as_bh(tmp_path, alias):
    document = _fixture_statistics()
    document["fdr_methods"] = {alias: "holm"}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="BH|adjustment|correction"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_fdr_metadata_canonical_collision_rejects_even_equal_labels(tmp_path):
    document = _fixture_statistics()
    document["fdr_methods"] = {"q_value": "benjamini_hochberg", "ｑ_value": "benjamini_hochberg"}
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="canonical|alias|correction|adjustment"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_reported_bh_values_must_match_existing_adjustment_over_the_full_declared_family(tmp_path):
    document = _fixture_statistics()
    document["genes"]["third"]["q_value"] = .9
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="BH|adjustment|whole|family"):
        projection().project_verified_outputs(methods, plan, policy=policy)


def test_unqualified_missing_correction_metadata_does_not_assert_bh(tmp_path):
    document = _fixture_statistics()
    document.pop("fdr_methods")
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="BH|correction|scope|qualified"):
        projection().project_verified_outputs(methods, plan, policy=policy)


@pytest.mark.parametrize("field,value", [("converged", 0), ("converged", "false"),
    ("dispersion_converged", 0), ("dispersion_converged", "false"),
    ("status", []), ("status", {}), ("status", "unknown"), ("status", None)])
def test_malformed_or_unknown_fit_status_never_falls_back_to_estimated_success(tmp_path, field, value):
    document = _fixture_statistics()
    document["genes"]["third"][field] = value
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    with pytest.raises(ValueError, match="status|converg|typed|unknown|estimate"):
        projection().project_verified_outputs(methods, plan, policy=policy)


@pytest.mark.parametrize("layout", ["dict", "list", "dict_envelope", "list_envelope", "table"])
def test_multifamily_ids_identify_exact_raw_rows_and_values_for_every_layout(tmp_path, layout):
    from devbench.analysis.rna_investigation_contract import _pointer
    import csv
    import io
    genes = ["0", "１"]
    records = {"0": {"status": "ok", "p_value": .2, "q_value": .2},
               "１": {"status": "ok", "p_value": .1, "q_value": .2}}
    reverse = [{"gene_id": gene, **records[gene]} for gene in reversed(genes)]
    document = {"genes": records, "fdr_methods": {"q_value": "benjamini_hochberg"},
                "fdr_scope": "requested_genes", "selection_criterion": "fixed"}
    kind = "json"
    if layout == "list":
        document["gene_statistics"] = reverse
        document.pop("genes")
    elif layout == "dict_envelope":
        document["gene_statistics"] = {"rows": records}
        document.pop("genes")
    elif layout == "list_envelope":
        document["gene_statistics"] = {"rows": reverse}
        document.pop("genes")
    elif layout == "table":
        document = "gene_id,status,p_value,q_value,q_value_method,fdr_scope\n１,ok,0.1,0.2,benjamini_hochberg,requested_genes\n0,ok,0.2,0.2,benjamini_hochberg,requested_genes\n"
        kind = "table"
    methods, plan, policy, _, step, executor = _fixture_receipt(tmp_path, document, kind=kind, genes=genes)
    _declare(plan, step, "second-static-family")
    _store_plan(methods, plan)
    second = methods.run_step(step["step_id"], actor="fixture", executor=executor)
    _complete(methods, plan, second)
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    packet = _packet(methods, plan, policy, overlay)
    families, _, _ = projection()._families(methods, plan)
    raw = {family["ref"]["artifact_id"]: family["document"] for family in families}
    for row in _stats(packet)["value"]["rows"]:
        identity, pointer = row["hypothesis_id"].split("#", 1)
        if layout == "table":
            import re
            matched = re.fullmatch(r"csv:L([0-9]+)-L([0-9]+);row=([0-9]+)", pointer)
            assert matched is not None, "A CSV selector must not masquerade as a physical JSON pointer"
            start, end, index = (int(value) for value in matched.groups())
            selector = raw[identity]["_csv_row_selectors"][index]
            assert selector == {"start_line": start, "end_line": end}
            actual = raw[identity]["gene_statistics"][index]
            retained = methods.artifact_path(identity).read_text().splitlines()
            parsed = list(csv.DictReader(io.StringIO(retained[0] + "\n" + "\n".join(retained[start - 1:end]))))[0]
            assert parsed["gene_id"] == actual["gene_id"] and float(parsed["p_value"]) == actual["p_value"]
        else:
            actual = _pointer(raw[identity], pointer)
        assert actual.get("gene_id", pointer.rsplit("/", 1)[-1]) in genes
        assert actual["p_value"] == row["p_value"] and actual["q_value"] == row["q_value"]
    assert projection().verify_packet_projection(methods, packet, plan, policy=None) == packet


def test_bh_arithmetic_validation_retains_original_q_and_discloses_calibration_limits(tmp_path):
    document = _fixture_statistics()
    methods, plan, policy, run, *_ = _fixture_receipt(tmp_path, document)
    before = methods.artifact_path(run["outputs"]["result"]).read_bytes()
    overlay = projection().project_verified_outputs(methods, plan, policy=policy)
    summary = next(fact["value"] for fact in overlay["facts"]["observed"] if fact["field"] == "fitted_family_coverage")
    assurance = summary["per_run"][0]["correction_assurance"]
    assert assurance["arithmetic_verified"] is True
    assert assurance["p_value_calibration"] == "not_established"
    assert methods.artifact_path(run["outputs"]["result"]).read_bytes() == before


@pytest.mark.parametrize("field", ["converged", "dispersion_converged"])
def test_exact_false_convergence_keeps_null_failed_estimate_and_reason(tmp_path, field):
    document = _fixture_statistics()
    document["genes"]["first"][field] = False
    methods, plan, policy, *_ = _fixture_receipt(tmp_path, document)
    packet = _packet(methods, plan, policy, projection().project_verified_outputs(methods, plan, policy=policy))
    row = _stats(packet)["value"]["rows"][1]
    assert row["estimate_status"] == "failed" and row["p_value"] is None and row["q_value"] is None
    assert row["reason"] and _stats(packet)["value"]["failed_estimates"] == 2
