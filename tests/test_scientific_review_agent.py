"""Offline reviewer protocol/control fixtures, not scientific review qualification."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_analysis_episodes import workspace
from tests.test_scientific_review import packet, assessment


def api():
    from devbench.analysis.scientific_review_agent import review_context, review_schema, request_review
    return review_context, review_schema, request_review


def public_packet():
    return {"subject": {"kind": "episode", "original_location": "/sensitive/location"},
        "sections": {"configuration": {"question": "Assess the condition contrast", "assay": "bulk",
            "public_task": {"measurement": "raw_counts", "analysis_columns": {"condition": "condition", "batches": ["batch"]},
                "unit_hierarchy": {"population_unit": "donor", "identity_status": "declared", "private_note": "not for a model"}},
            "input_paths": {"expression": "/sensitive/expression.csv"}, "limits": {"memory_mb": 2048}, "fixed_options": {}},
            "admission": {"scientific_status": {"identifiability": "not_established"}},
            "design": [], "runtime": {"runtime": {"python": "3.12", "executable": "/sensitive/python", "packages": {"numpy": "fixture"}}},
            "numerical_results": {"format": "devbench.episode-synthesis/1", "outcome": "completed", "reason": None,
                "outputs_complete": True, "harness_accepted": True, "scientific_correctness": None,
                "targets": [], "findings": [], "tool_events": []},
            "diagnostics": [], "limitations": ["Conditional inference"], "provenance": {"private_path": "/sensitive/data"}},
        "research": {"researcher_label": "blinded-curator", "sources": [], "claims": []}}


def test_projection_omits_private_paths_runtime_executables_and_previous_acceptance():
    context = api()[0](public_packet())
    encoded = json.dumps(context)
    assert "/sensitive" not in encoded and "private_note" not in encoded
    assert "harness_accepted" not in encoded and "scientific_correctness" not in encoded
    assert "blinded-curator" not in encoded
    assert context["observed_versions"]["packages"]["numpy"] == "fixture"
    assert context["decision_kind"] == "scientific_review"


def test_schema_binds_claim_ids_and_forbids_numerical_or_authority_overrides():
    import jsonschema
    context = api()[0](public_packet())
    schema = api()[1](context)
    value = {"kind": "scientific_review", **assessment()}
    value["findings"][0]["claim_id"] = None
    jsonschema.validate(value, schema)
    for extra in ({"p_value": .01}, {"clinical_release_allowed": True}, {"kind": "finish"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**value, **extra}, schema)
    value["findings"][0]["claim_id"] = "invented"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(value, schema)


def test_native_metric_status_is_visible_but_annotation_file_details_are_not():
    value = public_packet()
    value["subject"]["kind"] = "workbench"
    value["sections"]["numerical_results"] = {"conditions": {"uncorrected": {
        "scope": "concordance_only", "clustering": {"ARI": None, "NMI": None, "status": "unavailable"},
        "annotation_method": {"method": "marker_panel", "status": "completed", "chunk_reports": ["/sensitive/labels.json"],
                              "model_path": "/sensitive/model.pkl"}, "batch_mixing": {"scope": "descriptive", "batch_ASW": None}}}}
    context = api()[0](value)
    assert "/sensitive" not in json.dumps(context)
    condition = context["sections"]["numerical_results"]["conditions"]["uncorrected"]
    assert condition["clustering"]["status"] == "unavailable"
    assert condition["batch_mixing"]["scope"] == "descriptive"
    assert condition["annotation_method"] == {"method": "marker_panel", "status": "completed"}


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
        if self.mutate: self.mutate(output)
        return Selection(self.decision, {"backend": "recorded_fixture", "model_sessions_attempted": 0,
            "actual_model": None, "tokens": None, "cost_usd": None})


def request(root, receipt, selector, **kwargs):
    return api()[2](root / "review-run", receipt["manifest_path"], expected_root=receipt["root_hash"],
        reviewer_label="offline-reviewer", selector=selector, allow_evidence_read=True, allow_review=True, **kwargs)


def test_recorded_selector_produces_a_verified_origin_bound_review(workspace):
    from devbench.analysis.scientific_review import verify_scientific_review
    receipt, _ = packet(workspace)
    selector = Selector()
    result = request(workspace, receipt, selector)
    assert result["status"] == "review_recorded", result
    assert len(selector.calls) == 1 and result["model_sessions_attempted"] == 0
    review = result["review_receipt"]
    record = json.loads((workspace / "review-run/review/scientific-review.json").read_text())
    assert record["selection_origin"]["root_hash"] == result["selection_receipt"]["root_hash"]
    assert record["qualification_status"] == "not_established" and record["independence_verified"] is False
    assert verify_scientific_review(review["manifest_path"], review["root_hash"], packet_manifest=receipt["manifest_path"],
        packet_root=receipt["root_hash"], allow_evidence_read=True)["valid"]


def test_live_reviewer_denies_missing_permission_and_private_scope_before_reads(workspace):
    from devbench.analysis.episode_codex import CodexActionSelector
    selector = CodexActionSelector(requested_model="not-launched", workspace_parent=workspace / "actors")
    for kwargs in ({}, {"allow_model_execution": True, "model_data_scope": "private"}):
        with pytest.raises(PermissionError):
            api()[2](workspace / "run", None, expected_root=None, reviewer_label="fixture", selector=selector,
                allow_evidence_read=True, allow_review=True, **kwargs)
    assert not (workspace / "actors").exists() and not (workspace / "run").exists()


def test_packet_mutation_after_selection_is_preserved_without_publishing_review(workspace):
    receipt, _ = packet(workspace)
    def mutate(_):
        path = workspace / "packet/packet.json"; path.write_bytes(path.read_bytes() + b"\n")
    result = request(workspace, receipt, Selector(mutate=mutate))
    assert result["status"] == "infrastructure_failure" and result["review_receipt"] is None
    assert (workspace / "review-run/selection.json").is_file()
    assert not (workspace / "review-run/review").exists()


def test_invalid_answer_never_publishes_a_review(workspace):
    receipt, _ = packet(workspace)
    selector = Selector({"kind": "scientific_review", **assessment(), "clinical_release_allowed": True})
    result = request(workspace, receipt, selector)
    assert result["status"] == "invalid_review" and result["review_receipt"] is None


def test_failed_evidence_capture_cannot_publish_a_review(workspace):
    receipt, _ = packet(workspace)
    def mutate(output):
        (output / "outside-link").symlink_to(workspace / "packet/packet.json")
    result = request(workspace, receipt, Selector(mutate=mutate))
    assert result["status"] == "infrastructure_failure" and result["evidence_capture_complete"] is False
    assert result["review_receipt"] is None and not (workspace / "review-run/review").exists()


def test_interrupted_selector_without_usage_evidence_does_not_report_zero_attempts(workspace):
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
    result = request(workspace, receipt, selector, limits=AgentLimits(max_context_bytes=1024))
    assert result["status"] == "infrastructure_failure" and not selector.calls
    assert "context_budget" in result["reason"]


def test_cli_uses_an_explicit_recorded_review_with_zero_provider_sessions(workspace):
    receipt, _ = packet(workspace)
    request_path, answer_path = workspace / "request.json", workspace / "answer.json"
    request_path.write_text(json.dumps({"packet_manifest": receipt["manifest_path"], "expected_root": receipt["root_hash"],
                                      "reviewer_label": "recorded-CLI-reviewer"}))
    answer_path.write_text(json.dumps({"kind": "scientific_review", **assessment()}))
    args = [sys.executable, "-m", "devbench.analysis.episode_cli", "--agent-review-request", str(request_path),
        "--review-answer", str(answer_path), "--allow-evidence-read", "--allow-review-record", "--output", str(workspace / "run")]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "review_recorded" and result["model_sessions_attempted"] == 0
