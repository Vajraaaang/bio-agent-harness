"""Offline reports preserve missing evidence and never execute artifact content."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from devbench_engine.visualizer import load_audit_nodes, main, render_report
from devharness_core.provenance import canonical_json


def interrupted_run():
    return {"task_id": "fixture_interrupted", "passed": False, "score": 0,
            "grader_result": None, "metadata": {
                "condition": "assisted", "track": "adversarial",
                "failure_type": "resource_interrupt", "error": "300 second deadline",
                "elapsed_sec": 300.1, "usage_complete": False, "cost_usd": None,
                "actual_model": "observed-model", "tokens": 120,
                "turns": [{"phase": "exploration", "elapsed_sec": 20,
                           "peak_rss_mb": 200, "memory_measurement": "sampled_process_tree_rss"}],
                "tool_capture_complete": True,
                "tool_calls": [{"tool_name": "audit_expression_quality", "succeeded": False,
                                "error": "user cancelled MCP tool call"}]}}


def audit_fixture(tmp_path):
    folder = tmp_path / "audit"
    (folder / "nodes").mkdir(parents=True)
    previous = None
    ids = []
    for index, kind in enumerate(("run_started", "tool_failed", "run_finished"), 1):
        node = {"schema_version": "devharness.execution-dag/1", "run_id": "test-run",
                "sequence": index, "kind": kind, "parents": [previous] if previous else [],
                "previous": previous, "payload": {"status": "failed", "tool_name": "test_tool"}}
        digest = hashlib.sha256(canonical_json(node)).hexdigest()
        node["node_id"] = digest
        (folder / "nodes" / (digest + ".json")).write_bytes(canonical_json(node))
        ids.append(digest)
        previous = digest
    manifest = {"schema_version": "devharness.execution-dag/1",
                "canonicalization": "devharness.canonical-json/1", "run_id": "test-run",
                "node_ids": ids, "node_count": len(ids),
                "artifacts": [{"name": "task_spec.json", "sha256": "f" * 64}]}
    manifest["root_hash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    path = folder / "manifest.json"
    path.write_bytes(canonical_json(manifest))
    return {"manifest_path": str(path), "root_hash": manifest["root_hash"]}, folder


def test_interruption_is_not_a_scientific_score_and_input_is_unchanged():
    run = interrupted_run()
    before = copy.deepcopy(run)
    report = render_report([run])
    assert run == before
    assert "Not graded" in report and "N/A" in report
    assert "user cancelled MCP tool call" in report
    assert "0 succeeded" in report and "1 canceled" in report
    assert "Partial usage" in report and "Unknown cost" in report
    assert "Phase peaks only" in report
    assert 'class="memory-peak"' in report
    assert 'class="memory-series"' not in report
    assert "observed-model" in report


def test_empty_missing_and_nonfinite_measurements():
    report = render_report([])
    assert "No runs supplied" in report
    report = render_report([{"task_id": "missing", "metadata": {"elapsed_sec": float("nan")}}])
    assert "Not graded" in report and "Audit unavailable" in report
    assert "NaN" not in report


def test_html_injection_is_escaped_and_private_payloads_are_omitted():
    run = interrupted_run()
    attack = '</script><img src=x onerror="alert(1)">'
    run["task_id"] = attack
    run["metadata"].update(error=attack, actual_model=attack,
                           stdout_tail="PRIVATE_STDOUT", api_key="PRIVATE_SECRET",
                           answer={"private_clinical_input": "PRIVATE_ANSWER"})
    run["metadata"]["tool_calls"][0]["arguments"] = {"secret": "PRIVATE_ARGUMENT"}
    report = render_report([run], title=attack)
    assert attack not in report
    assert "&lt;img" in report
    for secret in ("PRIVATE_STDOUT", "PRIVATE_SECRET", "PRIVATE_ARGUMENT", "PRIVATE_ANSWER"):
        assert secret not in report
    assert "innerHTML" not in report
    assert "connect-src 'none'" in report


def test_actual_grades_render_gates_and_vetoes():
    run = interrupted_run()
    run["grader_result"] = {"passed": False, "score": .25, "score_max": 1, "children": [
        {"passed": True, "score": 1, "role": "gate", "reasoning": "Valid design"},
        {"passed": False, "score": 0, "role": "veto", "reasoning": "Unsupported inference"}]}
    report = render_report([run])
    assert "Valid design" in report and "Unsupported inference" in report
    assert "veto" in report and "0.25" in report


def test_observations_supplement_but_do_not_replace_measured_values():
    run = interrupted_run()
    run["metadata"]["session_id"] = "session-1"
    observation = {"task_id": run["task_id"], "condition": "assisted", "session_id": "session-1",
                   "model_contexts": [{"model": "context-only-model"}],
                   "last_token_usage": {"total_tokens": 99000}}
    report = render_report([run], session_observations=[observation], observations_source="context.json")
    assert "observed-model" in report and "context-only-model" in report
    assert "Supplemental session observations" in report
    assert "99,000" in report and "120" in report
    assert "context.json" in report
    run["metadata"].pop("actual_model")
    report = render_report([run], session_observations=[observation])
    assert "Adapter model unreported" in report
    assert "Model unobserved" not in report
    observation["session_id"] = "different-session"
    assert "context-only-model" not in render_report([run], session_observations=[observation])


def test_sampled_memory_has_units_and_uses_observed_points():
    run = interrupted_run()
    run["metadata"]["memory_samples"] = [{"elapsed_sec": 0, "rss_bytes": 1048576},
                                         {"elapsed_sec": 2, "rss_bytes": 3145728}]
    run["metadata"]["memory_sampling"] = {"scope": "runner_and_recursive_children", "unit": "bytes",
                                          "interval_sec": .1, "successful_sample_count": 4,
                                          "retained_stride": 2, "complete": False}
    report = render_report([run])
    assert 'class="memory-series"' in report
    assert "MiB" in report and "2 recorded samples" in report
    assert "runner_and_recursive_children" in report and "0.1 s" in report
    assert "retained stride 2" in report and "incomplete" in report
    assert "double-count shared pages" in report


def test_hard_fail_veto_true_means_triggered_not_a_pass():
    run = interrupted_run()
    run["grader_result"] = {"passed": False, "score": 0, "children": [
        {"passed": True, "score": 1, "role": "hard_fail", "reasoning": "A violation was found"}]}
    report = render_report([run])
    assert "hard_fail</th><td>Triggered" in report


def test_invalid_parent_links_and_oversized_artifacts_fail_closed(tmp_path):
    receipt, folder = audit_fixture(tmp_path)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["node_ids"] = list(reversed(manifest["node_ids"]))
    manifest.pop("root_hash")
    manifest["root_hash"] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    receipt["root_hash"] = manifest["root_hash"]
    manifest_path.write_bytes(canonical_json(manifest))
    assert load_audit_nodes(receipt, source_path=tmp_path / "runs.json")["status"] == "invalid"
    manifest_path.write_text(" " * 2_000_001)
    assert load_audit_nodes(receipt, source_path=tmp_path / "runs.json")["status"] == "invalid"


def test_audit_hashes_and_graph_are_checked_without_opening_objects(tmp_path):
    receipt, folder = audit_fixture(tmp_path)
    # A task/golden object is deliberately absent; the report must never open it.
    info = load_audit_nodes(receipt, source_path=tmp_path / "runs.json")
    assert info["status"] == "checked"
    assert len(info["nodes"]) == 3
    run = interrupted_run()
    run["metadata"]["audit"] = receipt
    report = render_report([run], source_path=tmp_path / "runs.json")
    assert 'class="dag-node"' in report and "tool_failed" in report
    assert "Artifacts not verified" in report
    assert "not a compliance certificate" in report
    assert not (folder / "objects").exists()


def test_audit_tampering_and_missing_receipts_are_not_valid(tmp_path):
    receipt, folder = audit_fixture(tmp_path)
    path = next((folder / "nodes").iterdir())
    data = json.loads(path.read_text())
    data["kind"] = "forged_success"
    path.write_text(json.dumps(data))
    info = load_audit_nodes(receipt, source_path=tmp_path / "runs.json")
    assert info["status"] == "invalid" and not info["nodes"]
    assert load_audit_nodes({}, source_path=tmp_path / "runs.json")["status"] == "unavailable"


def test_audit_references_cannot_escape_source_directory_or_follow_symlinks(tmp_path):
    parent = tmp_path / "reports"
    parent.mkdir()
    receipt, folder = audit_fixture(tmp_path)
    assert load_audit_nodes(receipt, source_path=parent / "runs.json")["status"] == "unavailable"
    (parent / "alias").symlink_to(folder, target_is_directory=True)
    receipt["manifest_path"] = "alias/manifest.json"
    assert load_audit_nodes(receipt, source_path=parent / "runs.json")["status"] == "unavailable"


def test_cli_writes_offline_file_and_rejects_overwriting_input(tmp_path):
    source, output = tmp_path / "runs.json", tmp_path / "report.html"
    source.write_text(json.dumps([interrupted_run()]))
    assert main([str(source), "--output", str(output)]) == 0
    report = output.read_text()
    assert '<html lang="en">' in report
    assert "fetch(" not in report and 'src="http' not in report
    with pytest.raises(SystemExit):
        main([str(source), "--output", str(source)])


def test_optional_science_panels_only_use_explicit_observations():
    run = interrupted_run()
    absent = render_report([run])
    assert 'class="expression-chart"' not in absent
    assert "Expression curves and confidence intervals unavailable" in absent
    assert "Lineage graph unavailable" in absent
    run["metadata"]["report_artifacts"] = {
        "expression_curves": [{"gene": "ObservedGene", "x_label": "Collection age", "y_label": "Normalized expression",
                               "points": [{"x": 1, "prediction": 2, "ci_lower": 1, "ci_upper": 3},
                                          {"x": 2, "prediction": 4, "ci_lower": 3, "ci_upper": 5}]}],
        "lineage": {"nodes": [{"id": "a"}, {"id": "b"}], "edges": [{"source": "a", "target": "b"}]}}
    report = render_report([run])
    assert 'class="expression-chart"' in report and 'class="confidence-band"' in report
    assert 'class="lineage-chart"' in report and "ObservedGene" in report


def test_sealed_pilot_remains_ungraded_and_canceled():
    source = Path(__file__).resolve().parents[1] / "results/codex-live.gj3jlH/runs.json"
    if not source.is_file():
        pytest.skip("Sealed pilot is an optional local read-only fixture")
    runs = json.loads(source.read_text())
    observations_path = source.with_name("session-observations.json")
    observations = json.loads(observations_path.read_text())
    report = render_report(runs, source_path=source, session_observations=observations,
                           observations_source=observations_path)
    assert len(runs) == 8 and all(run["grader_result"] is None for run in runs)
    assert report.count('data-state="not-graded"') == 8
    assert "21 attempts" in report and "21 canceled" in report and "0 succeeded" in report
    assert "gpt-5.6-sol" in report and "Phase peaks only" in report
    assert 'class="memory-series"' not in report


def matrix_oracle_run():
    # Representative public result shape from MatrixOracleAdapter / make demo.
    return {"task_id": "synthetic_infrastructure_control", "passed": True, "score": 1.0,
            "grader_result": {"passed": True, "score": 1.0, "score_max": 1.0},
            "metadata": {"backend": "mock", "condition": "assisted", "track": "adversarial",
                         "source_kind": "synthetic", "oracle": True,
                         "comparison_eligible": True, "billing_mode": "synthetic_oracle",
                         "cost_usd": 0.0, "adapter_observations": {
                             "oracle": True, "billing_mode": "synthetic_oracle",
                             "inference_performed": False}}}


def test_matrix_oracle_report_labels_control_grades_and_privileged_answers():
    report = render_report([matrix_oracle_run()])
    assert "Privileged synthetic oracle" in report
    assert "Model unobserved" not in report
    assert "Control evaluation" in report and "Control passed" in report
    assert "1 control runs · 1 graded · 1 passed" in report
    assert "do not measure live-model performance" in report
    assert "0 non-control runs" in report
    assert '<td class="numeric">1.000</td>' in report


def test_mixed_report_excludes_control_grades_from_non_control_summary():
    control = matrix_oracle_run()
    live = interrupted_run()
    report = render_report([control, live])
    assert "Non-control evaluation" in report
    assert "0 / 1" in report
    assert "1 control runs · 1 graded · 1 passed" in report
    assert 'data-evidence-kind="oracle-control"' in report
    assert 'data-evidence-kind="non-control"' in report
    assert "Control passed" in report and "Not graded" in report


@pytest.mark.parametrize("fields, label", [
    ({"oracle_mode": True}, "Privileged synthetic oracle"),
    ({"oracle_mode": "matrix"}, "Privileged synthetic oracle"),
    ({"model": "synthetic-oracle"}, "Privileged synthetic oracle"),
    ({"adapter_observations": {"oracle": True}}, "Privileged synthetic oracle"),
    ({"backend": "mock"}, "Mock infrastructure control"),
    ({"source_kind": "synthetic", "actual_model": "observed-live-model"}, "observed-live-model"),
])
def test_control_identity_uses_oracle_evidence_not_synthetic_data_alone(fields, label):
    report = render_report([{"task_id": "control_identity", "metadata": fields}])
    assert label in report
    if label == "observed-live-model":
        assert 'data-evidence-kind="non-control"' in report
