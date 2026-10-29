"""Multiple originals reach real governed planning without a fabricated merge."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_scientific_workspace import tmp_path
from devbench.analysis.rna_input_set_contract import (BINDING_FORMAT, aggregate_profile,
    inspect_registered_input_set, source_set_identity, verify_input_set_inspection)


def originals(tmp_path):
    from devbench.analysis.methods import MethodService
    methods = MethodService(tmp_path / "registered")
    members = []
    for index, value in enumerate(({"samples": ["one"], "condition": "a"}, {"samples": ["two"], "condition": "b"}), 1):
        path = tmp_path / ("cohort-" + str(index) + ".json")
        path.write_text(json.dumps(value))
        artifact = methods.import_artifact(path, artifact_type="json", actor="synthetic-software-check")
        members.append({"name": "source_" + str(index), "label": path.name,
            "artifact_id": artifact["artifact_id"], "declarations": {"role": "metadata"}})
    return methods, {"format": BINDING_FORMAT, "members": members}


def test_registered_set_keeps_both_exact_originals_and_no_joint_claim(tmp_path):
    methods, binding = originals(tmp_path)
    result = inspect_registered_input_set(methods, binding)
    assert result["source_count"] == 2
    assert set(result["input_artifact_refs"]) == {"source_1", "source_2"}
    assert {row["artifact_id"] for row in result["input_artifact_refs"].values()} == {
        row["artifact_id"] for row in binding["members"]}
    assert result["observed"]["combined_matrix_created"] is False
    assert result["observed"]["expression_values_read"] is False
    assert {row["field"] for row in result["unknowns"]} >= {
        "feature_alignment", "observation_identity", "biological_independence", "measurement_compatibility"}
    assert verify_input_set_inspection(methods, result) == result


def test_real_planner_and_graph_audit_bind_all_sources_without_upgrading_unknowns(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_actions import WorkflowActions
    from devbench.analysis.workflow_entrypoints import is_rna_plan
    methods, binding = originals(tmp_path)
    ids = [row["artifact_id"] for row in binding["members"]]
    supplied = {"observed": {"metrics_finite": True, "biological_units_independent": True},
        "inferred": {}, "unknowns": [], "artifact_ids": ids}
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Compare the attached cohorts appropriately", "computational_task": "RNA comparison"},
        supplied, actor="synthetic-software-check", data_classification="synthetic", rna_input_contract=binding)
    assert is_rna_plan(plan)
    summary = plan["context"]["dataset_summary"]
    assert summary["artifact_ids"] == ids
    assert summary["observed"]["rna_input_set"]["source_count"] == 2
    assert summary["observed"]["metrics_finite"] is None
    assert summary["observed"]["biological_units_independent"] is None
    assert summary["inferred"]["unverified_supplied_observed_claims"]["values"]["metrics_finite"] is True
    assert supplied["observed"]["metrics_finite"] is True
    WorkflowActions._audit(SimpleNamespace(planner=planner), plan)
    observed, _, receipt = planner._observation_inspection(summary, ids, {"metrics_finite"}, actor="synthetic-software-check")
    assert observed["observed"]["metrics_finite"] is None and receipt["expression_values_read"] is False
    with pytest.raises(ValueError, match="every selected original artifact"):
        planner._observation_inspection(summary, ids[:1], set(), actor="synthetic-software-check")
    assert methods.list_records("run") == []


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "swapped_names", "unknown", "forged_observation"])
def test_missing_duplicate_swapped_or_fabricated_set_binding_is_rejected(tmp_path, mutation):
    methods, binding = originals(tmp_path)
    if mutation == "forged_observation":
        result = inspect_registered_input_set(methods, binding)
        result["sources"][0]["observed"]["fabricated_independence"] = True
        with pytest.raises(ValueError, match="changed"):
            verify_input_set_inspection(methods, result)
        return
    if mutation == "missing":
        binding["members"].pop()
    elif mutation == "duplicate":
        binding["members"][1]["artifact_id"] = binding["members"][0]["artifact_id"]
    elif mutation == "swapped_names":
        binding["members"].reverse()
    else:
        binding["members"][1]["artifact_id"] = "f" * 64
    with pytest.raises((ValueError, KeyError)):
        inspect_registered_input_set(methods, binding)


def test_source_set_identity_is_ordered_and_does_not_add_gene_or_cell_counts():
    profiles = [{"source": "/approved/a.h5ad", "filename": "a.h5ad", "sha256": "a" * 64, "size_bytes": 2,
        "observed": {"cells": 10, "genes": 5}},
        {"source": "/approved/b.h5ad", "filename": "b.h5ad", "sha256": "b" * 64, "size_bytes": 3,
        "observed": {"cells": 20, "genes": 8}}]
    profile = aggregate_profile(profiles)
    assert profile["sha256"] != source_set_identity(list(reversed(profiles)))
    assert profile["filename"] == "2 datasets" and profile["size_bytes"] == 5
    assert "cells" not in profile["observed"] and "genes" not in profile["observed"]


def test_source_set_uses_the_real_mandatory_graph_and_stops_at_review(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_entrypoints import advance_workflow_to_gate
    from tests.test_method_planner import Selector, candidate
    from tests.support.workflow_science_fixtures import RecordingScientificRuntime
    methods, binding = originals(tmp_path)
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Assess appropriate joint RNA cohort methods", "computational_task": "RNA method selection"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [row["artifact_id"] for row in binding["members"]]},
        actor="synthetic-software-check", data_classification="synthetic", rna_input_contract=binding)
    alternative = candidate("Scientifically considered joint alternative with no installed implementation")
    alternative["assumptions"] = []
    selector = Selector({"candidates": [alternative]}, {"bindings": [{"candidate": alternative["name"],
        "method_version_id": None, "discovery": None}]})
    result = advance_workflow_to_gate(planner, plan["session_id"], actor="synthetic-software-check", selector=selector,
        scientific_workflow=RecordingScientificRuntime())
    assert result["gate"] == "review" and result["status"] == "awaiting_operator_review"
    assert [row["node"] for row in result["steps"]] == ["audit", "question", "discovery", "generate",
        "candidate_review", "resolve", "implementation", "design", "design_review"]
    context = selector.contexts[0]["dataset_summary"]
    assert len(context["artifact_ids"]) == 2
    assert context["observed"]["rna_input_set"]["source_count"] == 2
    assert planner.get(plan["session_id"])["experiments"] == []
    assert methods.list_records("run") == []


def test_proposal_only_runtime_generates_over_both_sources_then_requires_reviewer(tmp_path):
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.workflow_entrypoints import advance_workflow_to_gate
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    from tests.test_method_planner import Selector, candidate
    methods, binding = originals(tmp_path)
    planner = ScientificMethodPlanner(methods)
    plan = planner.create({"scientific_objective": "Plan an appropriate cross-cohort RNA comparison", "computational_task": "RNA method selection"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [row["artifact_id"] for row in binding["members"]]},
        actor="synthetic-software-check", data_classification="synthetic", rna_input_contract=binding)
    alternative = candidate("Joint-data alternative requiring substantive review")
    alternative["assumptions"] = []
    selector = Selector({"candidates": [alternative]})
    runtime = ScientificWorkflowRuntime(discovery_provider=None, discovery_request={"mode": "supplied_only",
        "reason": "Software test uses supplied evidence only, without external discovery.",
        "query": None, "allowed_hosts": [], "acquire_indices": []}, reviewer=None, review_policy={})
    result = advance_workflow_to_gate(planner, plan["session_id"], actor="synthetic-software-check",
        selector=selector, scientific_workflow=runtime)
    assert result["status"] == "awaiting_information"
    assert result["required_action"] == "configure_scientific_reviewer"
    assert [row["node"] for row in result["steps"]] == ["audit", "question", "discovery", "generate"]
    assert len(selector.contexts) == 1
    assert selector.contexts[0]["dataset_summary"]["observed"]["rna_input_set"]["source_count"] == 2
    assert planner.get(plan["session_id"])["experiments"] == []
    assert methods.list_records("run") == []
    assert runtime.configuration()["reviewer"] is None


def app_and_sources(tmp_path):
    from devbench.analysis.workbench_ui import WorkbenchService
    directory = tmp_path / "inputs"
    directory.mkdir()
    left, right = directory / "cohort-a.json", directory / "cohort-b.csv"
    left.write_text('{"samples":["a"],"condition":"one"}')
    right.write_text("sample,condition\nb,two\n")
    app = WorkbenchService(tmp_path / "workbench", [directory], enable_scientific=True)
    return app, [str(left), str(right)]


def test_actual_investigation_retains_compound_manifest_and_fresh_followup_scope(tmp_path):
    app, paths = app_and_sources(tmp_path)
    try:
        record = app.investigations.create({"question": "Compare the attached synthetic cohort metadata", "sources": paths,
            "source_declarations": [{"role": "metadata"}, {"role": "annotations"}]})
        assert record["status"] == "awaiting_information", record.get("error")
        assert record["scientific_session_id"] and record["job_id"] is None and app.jobs() == []
        assert record["sources"] == paths and len(record["source_profiles"]) == 2
        assert record["profile"]["observed"]["file_type"] == "input_set"
        context = record["scientific_context"]
        dataset = app.scientific.store().resolve(context["dataset_ref"])
        assert set(dataset["manifest"]["members"]) == {"source_1", "source_2"}
        plan = app.scientific._plan(record["scientific_session_id"])
        inspection = plan["context"]["dataset_summary"]["observed"]["rna_input_set"]
        assert inspection["input_set_sha256"] == record["input_set_sha256"]
        assert len(inspection["sources"]) == 2 and len(plan["context"]["dataset_summary"]["artifact_ids"]) == 2
        assert [row["artifact_ref"]["sha256"] for row in inspection["sources"]] == [row["sha256"] for row in record["source_profiles"]]
        swapped = deepcopy(dataset)
        swapped["manifest"]["members"]["source_1"], swapped["manifest"]["members"]["source_2"] = (
            swapped["manifest"]["members"]["source_2"], swapped["manifest"]["members"]["source_1"])
        with pytest.raises(ValueError, match="member order"):
            app.investigations._input_set_binding(record, swapped)
        child = app.investigations.action(record["id"], {"action": "follow_up", "expected_revision": record["revision"],
            "question": "What alignment evidence is needed before comparison?"})
        assert child["sources"] == paths and child["input_set_sha256"] == record["input_set_sha256"]
        assert child["scientific_context"]["dataset_ref"] == context["dataset_ref"]
        assert child["scientific_context"]["analysis_ref"] != context["analysis_ref"]
        assert child["scientific_session_id"] != record["scientific_session_id"]
        assert not child["scientific_context"].get("allow_model_execution")
        assert not child["scientific_context"].get("auto_advance") and not child["scientific_context"].get("auto_synthesis")
        with pytest.raises(ValueError, match="Trial input bindings must match"):
            app.scientific._declare_trial({"session_id": record["scientific_session_id"], "spec": {
                "candidates": ["one-input-only"], "inputs": {"one-input-only": {"source_1": dataset["manifest"]["members"]["source_1"]["artifact_id"]}}}}, "synthetic-check")
        assert app.jobs() == [] and app.scientific.catalog()["operations"] == []
    finally:
        app.close()


def test_two_actual_h5ad_headers_keep_layers_and_replication_unknowns(tmp_path):
    from tests.test_rna_scrna_input_contract import registered
    import anndata as ad
    methods, first, single_binding = registered(tmp_path)
    original = tmp_path / "declared-cells.h5ad"
    other = tmp_path / "other-cohort.h5ad"
    fixture = ad.read_h5ad(original)
    fixture.obs_names = ["other-" + str(index) for index in range(fixture.n_obs)]
    fixture.obs["capture"] = ["C"] * 3 + ["D"] * 3
    fixture.write_h5ad(other)
    second = methods.import_artifact(other, artifact_type="anndata", actor="synthetic-software-check")
    declaration = {key: value for key, value in single_binding["declarations"].items() if key != "biological_independence"}
    declaration["role"] = "expression"
    binding = {"format": BINDING_FORMAT, "members": [{"name": "source_" + str(index), "label": label,
        "artifact_id": artifact["artifact_id"], "declarations": deepcopy(declaration)}
        for index, (label, artifact) in enumerate(((original.name, first), (other.name, second)), 1)]}
    inspection = inspect_registered_input_set(methods, binding)
    assert [row["observed"]["cells"] for row in inspection["sources"]] == [6, 6]
    assert all(row["observed"]["layers"] == ["counts"] for row in inspection["sources"])
    assert all(row["observed"]["observation_ids_read"] is False for row in inspection["sources"])
    assert inspection["observed"]["combined_matrix_created"] is False
    assert any(row["field"] == "biological_independence" for row in inspection["unknowns"])
    assert original.read_bytes() != other.read_bytes()


def test_multi_clarifications_are_per_source_and_never_route_first_file_to_native(tmp_path):
    from tests.test_workbench_investigations import App
    from devbench.analysis.workbench_inputs import InputDiscovery
    from devbench.analysis.workbench_investigations import InvestigationService
    app = App(tmp_path)
    left, right = app.input_roots[0] / "a.json", app.input_roots[0] / "b.csv"
    left.write_text('{"samples":["a"]}'); right.write_text("sample\nb\n")
    service = InvestigationService(app, InputDiscovery(app.input_roots))
    record = service.create({"question": "Integrate these datasets", "sources": [str(left), str(right)]})
    assert record["status"] == "awaiting_information" and not app.registered
    assert {(field["source_index"], field["field"], field["scope"]) for field in record["clarification_fields"]} == {
        (0, "role", "source_declarations"), (1, "role", "source_declarations")}
    record = service.action(record["id"], {"action": "clarify", "expected_revision": record["revision"],
        "source_declarations": [{"role": "metadata"}, {"role": "reference"}]})
    assert record["required_action"] == "configure_scientific_workspace" and app.registered == []
    assert [row["role"] for row in record["source_declarations"]] == ["metadata", "reference"]
    left.write_text('{"samples":["changed"]}')
    with pytest.raises(ValueError, match="source changed"):
        service.action(record["id"], {"action": "clarify", "expected_revision": record["revision"]})
    assert not app.registered


def test_foreign_missing_contradictory_and_wrong_expected_sources_fail_closed(tmp_path):
    app, paths = app_and_sources(tmp_path)
    try:
        foreign = tmp_path / "foreign.json"; foreign.write_text("{}")
        with pytest.raises(PermissionError, match="outside"):
            app.investigations.create({"question": "Compare", "sources": [paths[0], str(foreign)]})
        with pytest.raises((ValueError, FileNotFoundError)):
            app.investigations.create({"question": "Compare", "sources": [paths[0], str(app.input_roots[0] / "missing.json")]})
        with pytest.raises(ValueError, match="contradictory"):
            app.investigations.create({"question": "Compare", "source": paths[0], "sources": paths})
        record = app.investigations.create({"question": "Compare", "sources": paths, "expected_sha256s": ["0" * 64, "1" * 64]})
        assert record["status"] == "operation_failed" and record["scientific_session_id"] is None
        assert app.jobs() == []
    finally:
        app.close()


def test_actual_thread_surfaces_missing_review_after_joint_model_planning(tmp_path):
    from devbench.analysis.workbench_ui import WorkbenchService
    from devbench.analysis.workflow_science import ScientificWorkflowRuntime
    from tests.test_method_planner import Selector, candidate
    directory = tmp_path / "inputs"
    directory.mkdir()
    paths = []
    for index in (1, 2):
        source = directory / ("cohort-" + str(index) + ".json")
        source.write_text(json.dumps({"source": index}))
        paths.append(str(source))
    alternative = candidate("Joint planning proposal needing independent review")
    alternative["assumptions"] = []
    selector = Selector({"candidates": [alternative]})
    runtime = ScientificWorkflowRuntime(discovery_provider=None, discovery_request={"mode": "supplied_only",
        "reason": "Synthetic software check uses supplied records only.", "query": None,
        "allowed_hosts": [], "acquire_indices": []}, reviewer=None, review_policy={})
    app = WorkbenchService(tmp_path / "workbench", [directory], enable_scientific=True,
        method_selector=selector, scientific_workflow=runtime)
    try:
        record = app.investigations.create({"question": "Compare the attached synthetic metadata", "sources": paths,
            "source_declarations": [{"role": "metadata"}, {"role": "metadata"}],
            "scientific_context": {"data_classification": "synthetic", "auto_advance": True}})
        assert record["scientific_operations"], record.get("error")
        app.scientific.futures[record["scientific_operations"][0]].result(timeout=30)
        record = app.investigations.get(record["id"])
        assert record["status"] == "awaiting_information"
        assert record["required_action"] == "configure_scientific_reviewer"
        assert "scientific-review adapter" in record["scientific_blocker_reason"]
        assert len(selector.contexts) == 1
        assert selector.contexts[0]["dataset_summary"]["observed"]["rna_input_set"]["source_count"] == 2
        assert app.jobs() == [] and app.scientific.catalog()["runs"] == []
        assert not record["gate_options"]["can_submit"]
    finally:
        app.close()
