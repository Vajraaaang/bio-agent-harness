"""Only empty/configuration tests may run before study execution is authorized."""
import builtins
import csv
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path

import pytest


def api():
    from devbench.study.dashboard import render_dashboard
    return render_dashboard


def plan(stage="exploratory"):
    from devbench.study.planning import default_study_plan
    return default_study_plan(stage=stage)


def test_empty_dashboard_module_exists():
    assert importlib.util.find_spec("devbench.study.dashboard") is not None


@pytest.mark.parametrize("stage,count", [("smoke", 6), ("diagnostic", 36), ("exploratory", 216)])
def test_empty_plan_has_real_counts_unknown_performance_and_public_exports(tmp_path, stage, count):
    proposed = plan(stage)
    result = api()(proposed, None, tmp_path / "report")
    assert result["privacy_status"] == "public_summary"
    assert result["figure_count"] == 0
    assert result["status"] == "not_run"
    summary = json.loads(Path(result["paths"]["summary"]).read_text())
    assert summary["planned_attempt_count"] == count
    assert summary["attempted_count"] == 0
    assert summary["headline"]["accepted_scientific_success_rate"] is None
    assert summary["headline"]["fdr"] is None
    assert summary["bootstrap"]["performed"] is False
    document = Path(result["paths"]["html"]).read_text()
    assert f'data-planned-attempts="{count}"' in document
    assert 'data-executed-attempts="0"' in document
    assert "Not run" in document and "Table-only filters" in document
    assert "Raw scientific correctness" in document
    assert "95%" in document and "no_attempts" in document
    assert "FDR" in document and "RSS_MB" in document
    assert "runtime-defined source units" in document
    assert "subscription" in document
    for name, path in result["paths"].items():
        payload = Path(path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == result["hashes"][name]
        assert proposed.blinding_nonce.encode() not in payload
        assert b"generation_seed" not in payload and b"blinding_nonce" not in payload
        assert b"recipe_id" not in payload
    for csv_name in ("runs", "genes", "metrics", "comparisons", "planned_scope"):
        rows = list(csv.DictReader(io.StringIO(Path(result["paths"][csv_name]).read_text())))
        if csv_name != "planned_scope":
            assert rows == []


def test_empty_path_never_imports_data_numeric_plot_or_actor_modules(tmp_path, monkeypatch):
    proposed = plan("smoke")
    render = api()
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith(("matplotlib", "numpy", "pandas", "scipy", "subprocess",
                                    "devbench.study.data", "benchmarks", "devbench.study.client"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    render(proposed, None, tmp_path / "report")


def test_empty_supplied_summary_does_not_call_analyzer(tmp_path, monkeypatch):
    from devbench.study import metrics
    proposed = plan("smoke")
    empty = metrics.analyze_attempts([], proposed)
    monkeypatch.setattr(metrics, "analyze_attempts", lambda *a, **k: pytest.fail("unexpected analysis"))
    api()(proposed, empty, tmp_path / "report")


def test_empty_public_report_redacts_private_metadata_and_escapes_labels(tmp_path):
    from devbench.study.metrics import analyze_attempts
    proposed = plan("smoke")
    empty = analyze_attempts([], proposed)
    empty["blinding_nonce"] = "PRIVATE-NONCE"
    empty["unregistered_coordinator_metadata"] = "PRIVATE-UNKNOWN-FIELD"
    empty["error_rows"] = [{"code": "configuration_only", "attempt_id": "not-executed",
                            "disposition": "quarantined", "original_record": {"label": "PRIVATE-ORIGINAL"}}]
    empty["problems"] = [{"code": '</script><script>alert("unsafe")</script>',
                           "source_path": "/private/coordinator/answers.json",
                           "reference_truth": {"gene": "PRIVATE-GENE"}}]
    result = api()(proposed, empty, tmp_path / "report")
    exported = "\n".join(Path(p).read_text() for p in result["paths"].values())
    assert "PRIVATE-NONCE" not in exported and "PRIVATE-GENE" not in exported
    assert "PRIVATE-UNKNOWN-FIELD" not in exported and "PRIVATE-ORIGINAL" not in exported
    assert "/private/coordinator" not in exported
    document = Path(result["paths"]["html"]).read_text()
    assert '<script>alert("unsafe")</script>' not in document
    assert "&lt;/script&gt;" in document
    assert 'src="http' not in document and 'href="http' not in document


def test_empty_configured_confidence_level_is_not_mislabeled(tmp_path):
    proposed = plan("smoke")
    thresholds = proposed.thresholds.model_copy(update={"confidence_level": 0.90})
    proposed = proposed.model_copy(update={"thresholds": thresholds})
    result = api()(proposed, None, tmp_path / "report")
    document = Path(result["paths"]["html"]).read_text()
    assert "90% CI" in document
    assert "95% CI" not in document


def test_empty_configuration_inference_block_is_visible(tmp_path):
    from devbench.study.metrics import analyze_attempts
    proposed = plan("smoke")
    empty = analyze_attempts([], proposed)
    empty["headline_inference_blocked"] = True
    result = api()(proposed, empty, tmp_path / "report")
    assert "Comparative inference blocked" in Path(result["paths"]["html"]).read_text()


def test_empty_configuration_primary_is_raw_correctness_and_acceptance_is_secondary(tmp_path):
    result = api()(plan("smoke"), None, tmp_path / "report")
    document = Path(result["paths"]["html"]).read_text()
    assert "Primary: paired raw scientific correctness gain · 95% CI" in document
    assert "Secondary: paired accepted-correct report gain · 95% CI" in document
    assert "A change in report acceptance alone does not establish improved scientific correctness." in document
    assert document.index("Primary: paired raw") < document.index("Secondary: paired accepted-correct")
    headers = next(csv.reader(io.StringIO(Path(result["paths"]["comparisons"]).read_text())))
    assert "outcome_role" in headers and "contrast_role" in headers
    assert "Paired accepted scientific success gain · 95% CI" not in document
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    assert exported["primary_outcome"] == "raw_scientific_success"
    assert exported["headline"]["primary_estimand"] == "raw_scientific_success"
    for comparison in exported["paired_comparisons"]:
        assert comparison["estimand"] == "raw_scientific_success"
        assert comparison["mean_delta"] is None
        assert comparison["accepted_scientific_mean_delta"] is None
        assert comparison["accepted_scientific_estimand_role"].startswith("secondary")


def test_empty_filters_have_explicit_exact_accessible_labels_and_three_arm_heading(tmp_path):
    class FilterMarkup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.labels = {}
            self.controls = []
            self.label_for = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "label":
                self.label_for = attrs.get("for")
                self.labels[self.label_for] = ""
            if tag == "select":
                self.controls.append(attrs)

        def handle_endtag(self, tag):
            if tag == "label":
                self.label_for = None

        def handle_data(self, data):
            if self.label_for:
                self.labels[self.label_for] += data

    result = api()(plan("smoke"), None, tmp_path / "report")
    document = Path(result["paths"]["html"]).read_text()
    markup = FilterMarkup()
    markup.feed(document)
    expected = {"source-kind": "Source Kind", "actual-model": "Actual Model", "actor-type": "Actor Type",
                "condition": "Condition", "scenario": "Scenario"}
    assert len(markup.controls) == len(expected)
    assert len({control.get("id") for control in markup.controls}) == len(expected)
    for control in markup.controls:
        label = expected[control["data-filter"]]
        assert control.get("aria-label") == label
        assert markup.labels.get(control.get("id")) == label
    assert "Does DevHarness<br>improve scientific outcomes?" in document
    assert "Does required tool use" not in document


def test_empty_configuration_distinguishes_unloaded_gene_details_from_not_run(tmp_path):
    from devbench.study.metrics import analyze_attempts
    proposed = plan("smoke")
    empty = analyze_attempts([], proposed)
    empty.update(gene_detail_storage="per_attempt", retained_gene_row_count=0, loaded_gene_row_count=0,
                 gene_rows_omitted_from_aggregate=True,
                 gene_detail_artifacts=[{"path": "/private/coordinator/record.json"}])
    result = api()(proposed, empty, tmp_path / "report")
    document = Path(result["paths"]["html"]).read_text()
    gene_section = document.split('<section id="gene-details">', 1)[1].split('</section>', 1)[0]
    assert "Gene details stored separately / not loaded" in gene_section
    assert "No gene details loaded into this report" in gene_section
    assert "Retained rows: 0" in gene_section and "Loaded rows: 0" in gene_section
    assert "Not run" not in gene_section and "no genes analyzed" not in gene_section
    assert '<strong data-executed-attempts="0">0</strong>' in document
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    assert exported["gene_detail_storage"] == "per_attempt"
    assert exported["retained_gene_row_count"] == exported["loaded_gene_row_count"] == 0
    assert exported["gene_rows_omitted_from_aggregate"] is True
    assert exported["display_contract"]["gene_csv_scope"] == "loaded_gene_rows_only"
    assert "gene_detail_artifacts" not in exported
    assert "/private/coordinator" not in "\n".join(Path(path).read_text() for path in result["paths"].values())
    assert list(csv.DictReader(io.StringIO(Path(result["paths"]["genes"]).read_text()))) == []


@pytest.mark.parametrize("changed", [
    {"gene_detail_storage": "external_path"}, {"retained_gene_row_count": -1},
    {"loaded_gene_row_count": 1}, {"retained_gene_row_count": True},
    {"gene_rows_omitted_from_aggregate": "yes"},
])
def test_empty_configuration_rejects_invalid_gene_storage_metadata(tmp_path, changed):
    from devbench.study.metrics import analyze_attempts
    proposed = plan("smoke")
    empty = analyze_attempts([], proposed)
    empty.update(gene_detail_storage="per_attempt", retained_gene_row_count=0, loaded_gene_row_count=0,
                 gene_rows_omitted_from_aggregate=True)
    empty.update(changed)
    with pytest.raises(ValueError, match="gene"):
        api()(proposed, empty, tmp_path / "report")
    assert not (tmp_path / "report").exists()


def test_empty_configuration_rejects_relative_existing_and_symlink_output(tmp_path):
    proposed = plan("smoke")
    with pytest.raises(ValueError, match="absolute"):
        api()(proposed, None, Path("relative-report"))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    marker = occupied / "preserve.txt"
    marker.write_text("unchanged")
    with pytest.raises((ValueError, FileExistsError)):
        api()(proposed, None, occupied)
    assert marker.read_text() == "unchanged"
    link = tmp_path / "alias"
    link.symlink_to(occupied, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        api()(proposed, None, link / "report")
    assert not (occupied / "report").exists()


def test_empty_private_rows_require_explicit_permission_without_iteration(tmp_path):
    class Unreadable:
        def __iter__(self):
            pytest.fail("Private rows were touched without authorization")

    with pytest.raises(ValueError, match="include_private_reference"):
        api()(plan("smoke"), None, tmp_path / "report", private_reference_rows=Unreadable())
    assert not (tmp_path / "report").exists()


def test_empty_private_mode_has_coordinator_label_and_no_reference_figures(tmp_path):
    result = api()(plan("smoke"), None, tmp_path / "report",
                   private_reference_rows=[], include_private_reference=True)
    assert result["privacy_status"] == "coordinator_reference_report"
    assert result["figure_count"] == 0
    assert "coordinator_reference_report" in Path(result["paths"]["html"]).read_text()


def test_empty_summary_rejects_wrong_plan_nonfinite_and_output_limits(tmp_path, monkeypatch):
    from devbench.study import dashboard
    from devbench.study.metrics import analyze_attempts
    proposed = plan("smoke")
    empty = analyze_attempts([], proposed)
    wrong = {**empty, "study_id": "another-study"}
    with pytest.raises(ValueError, match="study"):
        api()(proposed, wrong, tmp_path / "wrong")
    with pytest.raises(ValueError, match="finite"):
        api()(proposed, {**empty, "headline": {"fdr": float("nan")}}, tmp_path / "nan")
    monkeypatch.setattr(dashboard, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(ValueError, match="bound"):
        api()(proposed, empty, tmp_path / "too-large")
    assert not (tmp_path / "too-large").exists()


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/report fixtures require opt-in")
def test_future_observed_figures_keep_sources_actors_missingness_and_common_axes(tmp_path):
    """Hand-authored performance rows are deliberately never evaluated in this task."""
    from devbench.study.metrics import analyze_attempts
    proposed = plan("smoke")
    summary = analyze_attempts([], proposed)
    summary.update(status="observed_attempt_records", attempted_count=2, record_count=2)
    summary["group_summaries"] = [
        {"source_kind": "synthetic", "actual_model": "observed-model", "actor_type": "model_participant",
         "condition": condition, "attempted_count": 1,
         "metrics": {"signed_recall": {"mean": value, "available_count": 1, "denominator": 1}},
         "resources": {}, "accepted_scientific_success_rate": None, "raw_scientific_success_rate": None}
        for condition, value in (("baseline", 0.4), ("enforced", 0.8))]
    result = api()(proposed, summary, tmp_path / "report")
    document = Path(result["paths"]["html"]).read_text()
    assert result["figure_count"] > 0
    assert "synthetic" in document and "observed-model" in document
    assert "fixed bounds: 0 to 1" in document


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/report fixtures require opt-in")
def test_future_private_reference_scatter_and_trajectory_are_explicitly_gated(tmp_path):
    proposed = plan("smoke")
    references = [{"source_kind": "synthetic", "condition": "enforced", "actual_model": "observed-model",
                   "gene_id": "example", "true_effect": 1.0, "estimated_effect": 0.8,
                   "effect_units": "log2", "time_grid": [0.0, 1.0, 2.0],
                   "true_curve": [0.0, 1.0, 1.5], "predicted_curve": [0.1, 0.8, 1.4]}]
    result = api()(proposed, None, tmp_path / "private", private_reference_rows=references,
                   include_private_reference=True)
    assert result["privacy_status"] == "coordinator_reference_report"
    assert result["figure_count"] == 2
    assert "true_effect" in Path(result["paths"]["private_reference"]).read_text()
