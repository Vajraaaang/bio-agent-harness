"""Offline reviewer protocol/control fixtures, not scientific review qualification."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests import test_analysis_episodes
from tests.test_scientific_review import assessment, packet

workspace = test_analysis_episodes.workspace


def api():
    from devbench.analysis.scientific_review_agent import (
        request_review,
        review_context,
        review_schema,
    )

    return review_context, review_schema, request_review


def public_packet():
    return {
        "subject": {"kind": "episode", "original_location": "/sensitive/location"},
        "sections": {
            "configuration": {
                "question": "Assess the condition contrast",
                "assay": "bulk",
                "public_task": {
                    "measurement": "raw_counts",
                    "analysis_columns": {
                        "condition": "condition",
                        "batches": ["batch"],
                    },
                    "unit_hierarchy": {
                        "population_unit": "donor",
                        "identity_status": "declared",
                        "private_note": "not for a model",
                    },
                },
                "input_paths": {"expression": "/sensitive/expression.csv"},
                "limits": {"memory_mb": 2048},
                "fixed_options": {},
            },
            "admission": {"scientific_status": {"identifiability": "not_established"}},
            "design": [],
            "runtime": {
                "runtime": {
                    "python": "3.12",
                    "executable": "/sensitive/python",
                    "packages": {"numpy": "fixture"},
                }
            },
            "numerical_results": {
                "format": "devbench.episode-synthesis/1",
                "outcome": "completed",
                "reason": None,
                "outputs_complete": True,
                "harness_accepted": True,
                "scientific_correctness": None,
                "targets": [],
                "findings": [],
                "tool_events": [],
            },
            "diagnostics": [],
            "limitations": ["Conditional inference"],
            "provenance": {"private_path": "/sensitive/data"},
        },
        "research": {
            "researcher_label": "blinded-curator",
            "sources": [],
            "claims": [],
        },
    }


def test_projection_omits_private_paths_runtime_executables_and_previous_acceptance():
    context = api()[0](public_packet())
    encoded = json.dumps(context)
    assert "/sensitive" not in encoded and "private_note" not in encoded
    assert "harness_accepted" not in encoded and "scientific_correctness" not in encoded
    assert "blinded-curator" not in encoded
    assert context["observed_versions"]["packages"]["numpy"] == "fixture"
    assert context["decision_kind"] == "scientific_review"


def test_review_service_cannot_supply_publication_outcomes_to_an_analyst():
    with pytest.raises(PermissionError, match="analyst"):
        api()[0](None, projection_role="analyst")


def test_comparison_model_cannot_cite_fabricated_evidence_or_self_qualify(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_review import prepare_packet
    from tests.test_rna_publication_report import api as report_api
    from tests.test_rna_publication_report import prepared
    from tests.test_scientific_review import research

    report, _ = prepared(workspace_tmp_path)
    sealed = report_api().render_assessment_report(
        report,
        workspace_tmp_path / "report",
        allow_evidence_read=True,
        workspace=workspace_tmp_path,
    )
    packet = prepare_packet(
        workspace_tmp_path / "packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
    )
    answer = {
        "kind": "scientific_review",
        "disposition": "needs_information",
        "rationale": "Advisory only",
        "findings": [
            {
                "claim_id": "C1",
                "section": "numerical_results",
                "status": "concern",
                "reason": "Unknown cited artifact",
                "evidence_ids": ["fabricated"],
                "claim_scope": "hypothesis",
            }
        ],
        "questions": [],
        "limitations": ["Incomplete context"],
    }
    result = request(workspace_tmp_path, packet, Selector(answer))
    assert result["status"] == "invalid_review" and result["review_receipt"] is None
    assert (
        result["qualification_status"] == "not_established"
        and result["model_sessions_attempted"] == 0
    )


def test_large_gene_projection_discloses_every_omitted_row(workspace_tmp_path):
    from devbench.analysis.scientific_review import prepare_packet
    from tests.test_rna_publication_figures import file_ref
    from tests.test_rna_publication_metrics import gene_table
    from tests.test_rna_publication_report import api as report_api
    from tests.test_rna_publication_report import evidence, subject
    from tests.test_scientific_review import research

    table = gene_table()
    genes = ["g" + str(index) for index in range(140)]
    table["family"]["eligible_ids"] = genes
    table["rows"] = [
        {
            "feature_id": gene,
            "tested": True,
            "status": "ok",
            "effect": 1,
            "p_value": 1,
            "q_value": 1,
            "uncertainty": None,
        }
        for gene in genes
    ]
    table["mapping"]["records"] = [
        {"source_id": gene, "target_id": gene, "status": "mapped"} for gene in genes
    ]
    source = workspace_tmp_path / "table.json"
    source.write_text(json.dumps(table))
    values = evidence(workspace_tmp_path)
    values["metric_results"][0]["evidence_refs"] = [file_ref(source)]
    module = report_api()
    report = module.build_assessment_report(
        subject(workspace_tmp_path), values, workspace=workspace_tmp_path
    )
    sealed = module.render_assessment_report(
        report,
        workspace_tmp_path / "report",
        allow_evidence_read=True,
        workspace=workspace_tmp_path,
    )
    prepare_packet(
        workspace_tmp_path / "packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
    )
    context = api()[0](
        json.loads((workspace_tmp_path / "packet/packet.json").read_text())
    )
    tables = context["sections"]["numerical_results"]["gene_tables"]
    assert len(tables[0]["rows"]) == 128 and tables[0]["omitted_rows"] == 12
    assert any(
        item["reason"] == "per_feature_review_projection_limit" and item["count"] == 12
        for item in context["omitted_evidence"]
    )
    assert context["evidence_completeness"]["complete"] is False


def test_direct_metric_source_projects_the_verified_byte_snapshot(
    workspace_tmp_path, monkeypatch
):
    from devbench.analysis import (
        rna_publication_report as reports,
    )
    from devbench.analysis import (
        scientific_review_agent as agent,
    )
    from devbench.analysis.scientific_review import prepare_packet
    from tests.test_rna_publication_figures import file_ref
    from tests.test_rna_publication_metrics import gene_table
    from tests.test_rna_publication_report import api as report_api
    from tests.test_rna_publication_report import evidence, subject
    from tests.test_scientific_review import research

    table = gene_table()
    source = workspace_tmp_path / "direct-source.json"
    source.write_text(json.dumps(table))
    values = evidence(workspace_tmp_path)
    values["metric_results"][0]["evidence_refs"] = [file_ref(source)]
    report = report_api().build_assessment_report(
        subject(workspace_tmp_path), values, workspace=workspace_tmp_path
    )
    sealed = report_api().render_assessment_report(
        report,
        workspace_tmp_path / "report",
        allow_evidence_read=True,
        workspace=workspace_tmp_path,
    )
    prepare_packet(
        workspace_tmp_path / "packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
    )
    packet = json.loads((workspace_tmp_path / "packet/packet.json").read_text())
    checked = source.read_bytes()
    substituted = copy.deepcopy(table)
    substituted["rows"][0]["effect"] = 99
    replacement = json.dumps(substituted, allow_nan=False).encode()
    original, reads = reports._read, []

    def swap(path, maximum):
        if Path(path) == source:
            reads.append(str(path))
            return checked if len(reads) == 1 else replacement
        return original(path, maximum)

    monkeypatch.setattr(reports, "_read", swap)
    monkeypatch.setattr(agent, "_read", swap)
    context = agent.review_context(packet)
    projected = context["sections"]["numerical_results"]["gene_tables"][0]
    assert projected["rows"][0]["effect"] == table["rows"][0]["effect"]
    assert len(reads) == 1


def test_real_comparison_reviewer_sees_actual_reference_rows_and_source_context(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_review import prepare_packet
    from tests.test_rna_publication_comparison_v2 import assess
    from tests.test_scientific_review import research

    service, _, _, assessed = assess(workspace_tmp_path, reference_complete=False)
    sealed = assessed["report"]
    prepare_packet(
        workspace_tmp_path / "scientific-packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
        assessment_workspace=workspace_tmp_path,
        assessment_artifact_service=service,
    )
    context = api()[0](
        json.loads((workspace_tmp_path / "scientific-packet/packet.json").read_text())
    )
    tables = context["sections"]["numerical_results"]["gene_tables"]
    assert {table["table_role"] for table in tables} == {"actual", "reference"}
    assert (
        next(table for table in tables if table["table_role"] == "actual")["rows"][0][
            "q_value"
        ]
        == 0.02
    )
    assert (
        next(table for table in tables if table["table_role"] == "reference")["family"][
            "complete"
        ]
        is False
    )
    assert context["source_contexts"][0]["excerpt"]["locator"]["text"].startswith(
        "g1 synthetic"
    )
    assert (
        "Declared synthetic curation"
        in context["source_contexts"][0]["context_review"]["limitations"]
    )
    assert context["evidence_completeness"]["complete"] is False
    assert context["deterministic_restrictions"]["model_pixel_review_allowed"] is False


def test_comparison_reviewer_sees_frozen_objective_methods_and_actual_design(
    workspace_tmp_path,
):
    from devbench.analysis.rna_comparison_outputs import load_comparison_outputs
    from devbench.analysis.scientific_review import prepare_packet
    from tests.test_rna_publication_comparison_v2 import assess
    from tests.test_scientific_review import research

    service, _, bundle_ref, assessed = assess(workspace_tmp_path)
    sealed = assessed["report"]
    prepare_packet(
        workspace_tmp_path / "method-packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
        assessment_workspace=workspace_tmp_path,
        assessment_artifact_service=service,
    )
    captured = json.loads(
        (workspace_tmp_path / "method-packet/packet.json").read_text()
    )
    context = api()[0](captured)
    configuration = context["sections"]["configuration"]
    assert configuration["question"] == "Compare treatment-associated expression"
    assert configuration["mode"] == "independent_method_selection"
    assert configuration["requirements"][0]["origin"] == "fixture"
    assert (
        "not a user scientific requirement"
        in configuration["requirements"][0]["evidence"]
    )
    bundle = load_comparison_outputs(bundle_ref, artifact_service=service)
    run = service.get("run", bundle["runs"][0]["run_id"])
    method = configuration["methods"][0]
    assert method["method_version_id"] == run["method_version_id"]
    assert method["parameters"] == {
        key: value
        for key, value in run["parameters"].items()
        if key not in method["omitted_parameters"]
    }
    design = context["sections"]["design"]["input_design"]
    assert design["representation"] == "normalized_expression"
    assert design["metadata_roles"]["biological_unit"] == "unit"
    assert isinstance(design["audit"], dict) and isinstance(design["design"], dict)
    assert "record_ref" not in json.dumps(configuration)
    assert str(workspace_tmp_path) not in json.dumps(context)
    assert (
        context["deterministic_restrictions"]["qualified_review"] == "not_established"
    )


def test_missing_method_context_precludes_confident_appropriateness_verdict(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_review import _assessment, prepare_packet
    from tests.test_rna_publication_report import api as report_api
    from tests.test_rna_publication_report import prepared
    from tests.test_scientific_review import research

    report, _ = prepared(workspace_tmp_path)
    sealed = report_api().render_assessment_report(
        report,
        workspace_tmp_path / "report",
        allow_evidence_read=True,
        workspace=workspace_tmp_path,
    )
    prepare_packet(
        workspace_tmp_path / "method-missing",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
    )
    captured = json.loads(
        (workspace_tmp_path / "method-missing/packet.json").read_text()
    )
    # Exercise the independent guard even if a supplied flag conceals missing context.
    captured["assessment"]["omitted_evidence"] = []
    captured["assessment"]["evidence_completeness"] = {
        "complete": True,
        "omitted_count": 0,
    }
    context = api()[0](captured)
    assert any(item["kind"] == "method_context" for item in context["omitted_evidence"])
    assert (
        context["deterministic_restrictions"]["method_appropriateness"]
        == "not_assessed"
    )
    answer = {
        "kind": "scientific_review",
        "disposition": "accept",
        "rationale": "Confident but missing methods",
        "findings": [],
        "questions": [],
        "limitations": [],
    }
    with pytest.raises(ValueError, match="incomplete|method context"):
        _assessment(
            {key: value for key, value in answer.items() if key != "kind"}, captured
        )


def test_actual_optional_unknown_roles_remain_unassessed_despite_supplied_completeness(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_review import (
        _assessment,
        _method_context,
        prepare_packet,
    )
    from tests.test_rna_publication_comparison_v2 import optional_roles_method_context
    from tests.test_rna_publication_report import api as report_api
    from tests.test_rna_publication_report import prepared
    from tests.test_scientific_review import research

    role_case = workspace_tmp_path / "roles"
    role_case.mkdir()
    retained = _method_context(optional_roles_method_context(role_case))
    assert retained["input_design"]["metadata_roles"] == {}
    assert retained["status"] == "not_assessed"
    packet_case = workspace_tmp_path / "packet-case"
    packet_case.mkdir()
    report, _ = prepared(packet_case)
    sealed = report_api().render_assessment_report(
        report, packet_case / "report", allow_evidence_read=True, workspace=packet_case
    )
    prepare_packet(
        packet_case / "packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
    )
    captured = json.loads((packet_case / "packet/packet.json").read_text())
    captured["assessment"]["review_evidence"]["method_context"] = retained
    captured["assessment"]["omitted_evidence"] = []
    captured["assessment"]["evidence_completeness"] = {
        "complete": True,
        "omitted_count": 0,
    }
    projected = api()[0](captured)
    assert (
        projected["sections"]["configuration"]["question"]
        == "Compare treatment-associated expression"
    )
    assert (
        projected["deterministic_restrictions"]["method_appropriateness"]
        == "not_assessed"
    )
    assert any(
        item["kind"] == "method_context" for item in projected["omitted_evidence"]
    )
    answer = {
        "disposition": "accept",
        "rationale": "Missing roles cannot justify appropriateness",
        "findings": [],
        "questions": [],
        "limitations": [],
    }
    with pytest.raises(ValueError, match="incomplete|method context"):
        _assessment(answer, captured)


def test_reserved_parameters_are_omitted_without_changing_frozen_step(
    workspace_tmp_path,
):
    from devbench.analysis.scientific_review import (
        _assessment,
        _method_context,
        prepare_packet,
    )
    from tests.test_rna_publication_comparison_v2 import optional_roles_method_context
    from tests.test_rna_publication_report import api as report_api
    from tests.test_rna_publication_report import prepared
    from tests.test_scientific_review import research

    method_case = workspace_tmp_path / "method"
    method_case.mkdir()
    original = {"seed": 7, "settings": {"expected_scope": "synthetic fixture"}}
    projected, step, contract = optional_roles_method_context(
        method_case, method_parameters=original, omit_roles=False, return_receipt=True
    )
    retained = _method_context(projected)
    assert step["parameters"] == original and set(
        contract["parameters"]["properties"]
    ) == set(original)
    assert retained["methods"][0]["parameters"] == {}
    assert set(retained["methods"][0]["omitted_parameters"]) == {"seed", "settings"}
    packet_case = workspace_tmp_path / "packet-case"
    packet_case.mkdir()
    report, _ = prepared(packet_case)
    sealed = report_api().render_assessment_report(
        report, packet_case / "report", allow_evidence_read=True, workspace=packet_case
    )
    prepare_packet(
        packet_case / "packet",
        subject_kind="comparison",
        subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"],
        research=research(),
        allow_evidence_read=True,
    )
    captured = json.loads((packet_case / "packet/packet.json").read_text())
    captured["assessment"]["review_evidence"]["method_context"] = retained
    captured["assessment"]["omitted_evidence"] = []
    captured["assessment"]["evidence_completeness"] = {
        "complete": True,
        "omitted_count": 0,
    }
    context = api()[0](captured)
    assert context["sections"]["configuration"]["methods"][0]["parameters"] == {}
    assert any(item["kind"] == "method_context" for item in context["omitted_evidence"])
    assert (
        context["deterministic_restrictions"]["method_appropriateness"]
        == "not_assessed"
    )
    answer = {
        "disposition": "accept",
        "rationale": "Omitted parameters cannot justify a verdict",
        "findings": [],
        "questions": [],
        "limitations": [],
    }
    with pytest.raises(ValueError, match="incomplete|method context"):
        _assessment(answer, captured)


def test_actual_captured_source_claim_can_be_cited_by_schema_and_recording(
    workspace_tmp_path,
):
    import jsonschema

    from devbench.analysis.scientific_review import (
        prepare_packet,
        record_scientific_review,
    )
    from tests.test_rna_publication_comparison_v2 import assess
    from tests.test_scientific_review import research

    service, _, _, assessed = assess(workspace_tmp_path)
    packet = prepare_packet(
        workspace_tmp_path / "scientific-packet",
        subject_kind="comparison",
        subject_path=assessed["report"]["manifest_path"],
        expected_root=assessed["report"]["root_hash"],
        research=research(),
        allow_evidence_read=True,
        assessment_workspace=workspace_tmp_path,
        assessment_artifact_service=service,
    )
    value = json.loads(
        (workspace_tmp_path / "scientific-packet/packet.json").read_text()
    )
    # Independently reach the citation seam on prior wheels, whose table-role
    # key used the existing public-structure guard's reserved coordinator name.
    for table in value["assessment"]["review_evidence"]["gene_tables"]:
        if "role" in table:
            table["table_role"] = table.pop("role")
    context = api()[0](value)
    answer = {
        "kind": "scientific_review",
        "disposition": "needs_information",
        "rationale": "Declared source review is unqualified",
        "findings": [
            {
                "claim_id": context["source_contexts"][0]["claim_id"],
                "section": "numerical_results",
                "status": "concern",
                "reason": (
                    "This source is a partial reference, not exhaustive "
                    "biological truth."
                ),
                "claim_scope": "association",
                "evidence_ids": ["metric-gene_effect_absolute_difference"],
            }
        ],
        "questions": [],
        "limitations": ["Images unavailable to this text selector"],
    }
    jsonschema.validate(answer, api()[1](context))
    receipt = record_scientific_review(
        workspace_tmp_path / "recorded-source-citation",
        packet["manifest_path"],
        expected_root=packet["root_hash"],
        reviewer_label="synthetic advisory fixture",
        assessment={key: value for key, value in answer.items() if key != "kind"},
        qualification_statement="not established",
        independence_statement="not authenticated",
        allow_review=True,
    )
    assert receipt["root_hash"]


def test_schema_binds_claim_ids_and_forbids_numerical_or_authority_overrides():
    import jsonschema

    context = api()[0](public_packet())
    schema = api()[1](context)
    value = {"kind": "scientific_review", **assessment()}
    value["findings"][0]["claim_id"] = None
    jsonschema.validate(value, schema)
    for extra in (
        {"p_value": 0.01},
        {"clinical_release_allowed": True},
        {"kind": "finish"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**value, **extra}, schema)
    value["findings"][0]["claim_id"] = "invented"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)


def test_active_review_schema_is_strict_with_closed_evidence_and_literature_requests():
    from jsonschema import Draft202012Validator

    from devbench.analysis.scientific_review_agent import active_review_schema
    from devbench_engine.lifecycle import strict_schema_compatible

    schema = active_review_schema(
        {
            "query_handles": [{"handle_id": "numeric"}],
            "literature_handles": [
                {"handle_id": "literature", "claims": [{"claim_id": "claim"}]}
            ],
            "phase": "narrative_reveal",
        }
    )
    assert strict_schema_compatible(schema)
    validator = Draft202012Validator(schema)
    query = {
        "kind": "query_evidence",
        "handle_id": "numeric",
        "query": {"mode": "family_summary", "target_id": "family"},
    }
    validator.validate({"action": query})
    assert not validator.is_valid({"action": {**query, "handle_id": "not-entitled"}})
    assert not validator.is_valid(
        {"action": {**query, "query": {**query["query"], "file": "local"}}}
    )
    literature = next(
        variant
        for variant in schema["properties"]["action"]["anyOf"]
        if variant["properties"]["kind"]["const"] == "query_literature"
    )
    request = dict.fromkeys(literature["properties"]["request"]["properties"])
    request.update(operation="fetch", provider="pubmed", identifier="123")
    fetch = {
        "kind": "query_literature",
        "handle_id": "literature",
        "claim_id": "claim",
        "request": request,
    }
    validator.validate({"action": fetch})
    assert not validator.is_valid(
        {"action": {**fetch, "request": {**request, "url": "https://example.net"}}}
    )


def test_active_review_handle_page_preserves_total_inventory_digest_under_byte_budget():
    import json

    from devbench.analysis.checkpoints import digest
    from devbench.analysis.scientific_review_agent import (
        _page_review_query_handles,
        active_review_schema,
    )

    handles = [
        {
            "handle_id": f"handle-{index}",
            "source_id": "evidence",
            "family_ids": ["family-" + "x" * 80],
        }
        for index in range(10)
    ]
    context = {
        "query_handles": handles,
        "phase": "evidence_first",
        "question": "Inspect all entitled evidence",
    }
    remaining, visited = 0, []
    while remaining is not None:
        page = _page_review_query_handles(context, remaining, 600)
        assert len(json.dumps(page, allow_nan=False).encode()) <= 600
        assert page["query_handle_page"]["inventory_sha256"] == digest(handles)
        assert page["query_handle_page"]["total_handles"] == len(handles)
        visited.extend(page["query_handles"])
        remaining = page["query_handle_page"]["next_offset"]
        assert len(visited) <= len(handles)
        assert active_review_schema(page)["properties"]["action"]["anyOf"]
    assert visited == handles


def test_native_metric_status_is_visible_but_annotation_file_details_are_not():
    value = public_packet()
    value["subject"]["kind"] = "workbench"
    value["sections"]["numerical_results"] = {
        "conditions": {
            "uncorrected": {
                "scope": "concordance_only",
                "clustering": {"ARI": None, "NMI": None, "status": "unavailable"},
                "annotation_method": {
                    "method": "marker_panel",
                    "status": "completed",
                    "chunk_reports": ["/sensitive/labels.json"],
                    "model_path": "/sensitive/model.pkl",
                },
                "batch_mixing": {"scope": "descriptive", "batch_ASW": None},
            }
        }
    }
    context = api()[0](value)
    assert "/sensitive" not in json.dumps(context)
    condition = context["sections"]["numerical_results"]["conditions"]["uncorrected"]
    assert condition["clustering"]["status"] == "unavailable"
    assert condition["batch_mixing"]["scope"] == "descriptive"
    assert condition["annotation_method"] == {
        "method": "marker_panel",
        "status": "completed",
    }


class Selector:
    def __init__(self, decision=None, mutate=None):
        self.decision = decision or {"kind": "scientific_review", **assessment()}
        self.mutate = mutate
        self.calls = []

    def configuration(self):
        return {"backend": "recorded_scientific_review_fixture"}

    def select(self, context, output, **kwargs):
        from devbench.analysis.episode_agent import Selection

        assert kwargs["allow_model_execution"] is False
        self.calls.append(copy.deepcopy(context))
        if self.mutate:
            self.mutate(output)
        return Selection(
            self.decision,
            {
                "backend": "recorded_fixture",
                "model_sessions_attempted": 0,
                "actual_model": None,
                "tokens": None,
                "cost_usd": None,
            },
        )


def request(root, receipt, selector, **kwargs):
    return api()[2](
        root / "review-run",
        receipt["manifest_path"],
        expected_root=receipt["root_hash"],
        reviewer_label="offline-reviewer",
        selector=selector,
        allow_evidence_read=True,
        allow_review=True,
        **kwargs,
    )


def test_recorded_selector_produces_a_verified_origin_bound_review(workspace):
    from devbench.analysis.scientific_review import verify_scientific_review

    receipt, _ = packet(workspace)
    selector = Selector()
    result = request(workspace, receipt, selector)
    assert result["status"] == "review_recorded", result
    assert len(selector.calls) == 1 and result["model_sessions_attempted"] == 0
    review = result["review_receipt"]
    record = json.loads(
        (workspace / "review-run/review/scientific-review.json").read_text()
    )
    assert (
        record["selection_origin"]["root_hash"]
        == result["selection_receipt"]["root_hash"]
    )
    assert (
        record["qualification_status"] == "not_established"
        and record["independence_verified"] is False
    )
    assert verify_scientific_review(
        review["manifest_path"],
        review["root_hash"],
        packet_manifest=receipt["manifest_path"],
        packet_root=receipt["root_hash"],
        allow_evidence_read=True,
    )["valid"]


def test_live_reviewer_denies_missing_permission_and_private_scope_before_reads(
    workspace,
):
    from devbench.analysis.episode_codex import CodexActionSelector

    selector = CodexActionSelector(
        requested_model="not-launched", workspace_parent=workspace / "actors"
    )
    for kwargs in ({}, {"allow_model_execution": True, "model_data_scope": "private"}):
        with pytest.raises(PermissionError):
            api()[2](
                workspace / "run",
                None,
                expected_root=None,
                reviewer_label="fixture",
                selector=selector,
                allow_evidence_read=True,
                allow_review=True,
                **kwargs,
            )
    assert not (workspace / "actors").exists() and not (workspace / "run").exists()


def test_packet_mutation_after_selection_is_preserved_without_publishing_review(
    workspace,
):
    receipt, _ = packet(workspace)

    def mutate(_):
        path = workspace / "packet/packet.json"
        path.write_bytes(path.read_bytes() + b"\n")

    result = request(workspace, receipt, Selector(mutate=mutate))
    assert (
        result["status"] == "infrastructure_failure"
        and result["review_receipt"] is None
    )
    assert (workspace / "review-run/selection.json").is_file()
    assert not (workspace / "review-run/review").exists()


def test_invalid_answer_never_publishes_a_review(workspace):
    receipt, _ = packet(workspace)
    selector = Selector(
        {"kind": "scientific_review", **assessment(), "clinical_release_allowed": True}
    )
    result = request(workspace, receipt, selector)
    assert result["status"] == "invalid_review" and result["review_receipt"] is None


def test_failed_evidence_capture_cannot_publish_a_review(workspace):
    receipt, _ = packet(workspace)

    def mutate(output):
        (output / "outside-link").symlink_to(workspace / "packet/packet.json")

    result = request(workspace, receipt, Selector(mutate=mutate))
    assert (
        result["status"] == "infrastructure_failure"
        and result["evidence_capture_complete"] is False
    )
    assert (
        result["review_receipt"] is None
        and not (workspace / "review-run/review").exists()
    )


def test_interrupted_selector_without_usage_evidence_does_not_report_zero_attempts(
    workspace,
):
    receipt, _ = packet(workspace)

    def interrupt(_):
        raise TimeoutError("Recorded selector interrupted before returning evidence")

    result = request(workspace, receipt, Selector(mutate=interrupt))
    assert result["status"] == "infrastructure_failure"
    assert result["model_sessions_attempted"] is None
    assert result["tokens"] is None and result["review_receipt"] is None


def test_context_budget_stops_before_reviewer_dispatch(workspace):
    from devbench.analysis.episode_agent import AgentLimits

    receipt, _ = packet(workspace)
    selector = Selector()
    result = request(
        workspace, receipt, selector, limits=AgentLimits(max_context_bytes=1024)
    )
    assert result["status"] == "infrastructure_failure" and not selector.calls
    assert "context_budget" in result["reason"]


def test_cli_uses_an_explicit_recorded_review_with_zero_provider_sessions(workspace):
    receipt, _ = packet(workspace)
    request_path, answer_path = workspace / "request.json", workspace / "answer.json"
    request_path.write_text(
        json.dumps(
            {
                "packet_manifest": receipt["manifest_path"],
                "expected_root": receipt["root_hash"],
                "reviewer_label": "recorded-CLI-reviewer",
            }
        )
    )
    answer_path.write_text(json.dumps({"kind": "scientific_review", **assessment()}))
    args = [
        sys.executable,
        "-m",
        "devbench.analysis.episode_cli",
        "--agent-review-request",
        str(request_path),
        "--review-answer",
        str(answer_path),
        "--allow-evidence-read",
        "--allow-review-record",
        "--output",
        str(workspace / "run"),
    ]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert (
        result["status"] == "review_recorded"
        and result["model_sessions_attempted"] == 0
    )
