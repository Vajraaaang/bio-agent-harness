"""Configuration and empty reporting only; no biology result fixtures may run."""
import builtins
import csv
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path

import pytest


def configuration(full=True):
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_planning import BiologyStudyPlan, default_biology_recipe
    from devbench.study.contracts import content_sha256
    catalog = load_biology_catalog()
    recipes = tuple(default_biology_recipe(case.case_id) for template in catalog.templates
                    for case in (template.cases if full else template.cases[:1]))
    return BiologyStudyPlan(study_id="biology-dashboard-config", blinding_nonce="a" * 64,
                            catalog_sha256=content_sha256(catalog.model_dump(mode="json")), recipes=recipes), catalog


def build():
    from devbench.study.biology_dashboard import build_biology_dashboard
    return build_biology_dashboard


def test_empty_biology_dashboard_module_exists():
    assert importlib.util.find_spec("devbench.study.biology_dashboard") is not None


@pytest.mark.parametrize("full,cases,attempts", [(True, 97, 291), (False, 20, 60)])
def test_empty_biology_report_separates_catalog_plan_registration_and_evidence(tmp_path, full, cases, attempts):
    plan, catalog = configuration(full)
    result = build()(plan, None, tmp_path / "report", catalog=catalog)
    document = Path(result["paths"]["html"]).read_text()
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    assert result["status"] == "not_run" and result["privacy_status"] == "public_summary"
    assert result["figure_count"] == 0
    assert exported["planned_attempt_count"] == attempts
    assert exported["planned_case_count"] == exported["planned_cohort_count"] == cases
    assert exported["planned_template_count"] == 20
    assert exported["catalog_coverage"] == {"case_count": 97, "template_count": 20, "effect_family_count": 30}
    assert exported["attempted_count"] == 0
    assert exported["headline"]["task_success_rate"] is None
    assert exported["headline"]["primary_eligible_attempt_count"] is None
    assert exported["bootstrap"]["performed"] is False
    assert exported["registration_status"] == "not_inspected"
    assert exported["qualification_status"] == "not_established"
    for phrase in ("Configured", "Callable registered", "Measured", "Qualified", "Registry not supplied",
                   "Raw full-contract correctness", "Accepted-correct reports", "Available-target correctness",
                   "Unknown reference coverage", "Table-only filters", "RSS_MB", "subscription",
                   "Not run", "97 cases", "20 templates", "30 effect families"):
        assert phrase in document
    assert f'data-planned-attempts="{attempts}"' in document
    assert 'data-executed-attempts="0"' in document
    for key, path in result["paths"].items():
        content = Path(path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == result["hashes"][key]
        assert plan.blinding_nonce.encode() not in content
        assert b"generation_seed" not in content and b"private_truth_objects" not in content
        assert b"blinding_nonce" not in content and b"recipe_id" not in content
    for key in ("runs", "metrics", "comparisons", "target_results", "safe_estimates"):
        assert list(csv.DictReader(io.StringIO(Path(result["paths"][key]).read_text()))) == []


def test_empty_biology_path_never_imports_generators_numeric_libraries_or_actors(tmp_path, monkeypatch):
    plan, catalog = configuration()
    render = build()
    original = builtins.__import__

    def guard(name, *args, **kwargs):
        assert name.split(".")[0] not in {"numpy", "pandas", "scipy", "sklearn", "matplotlib", "random", "subprocess"}
        assert name not in {"devbench.study.biology_bulk", "devbench.study.biology_singlecell",
                            "devbench.study.biology_datasets", "devbench.study.client"}
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    render(plan, None, tmp_path / "report", catalog=catalog)


def test_empty_supplied_biology_analysis_is_not_reanalyzed(tmp_path, monkeypatch):
    from devbench.study import biology_metrics
    plan, catalog = configuration()
    analysis = biology_metrics.analyze_biology_attempts([], plan)
    monkeypatch.setattr(biology_metrics, "analyze_biology_attempts", lambda *a, **k: pytest.fail("reanalyzed"))
    build()(plan, analysis, tmp_path / "report", catalog=catalog)


def test_empty_callable_registration_is_inspected_without_invoking_or_qualifying(tmp_path):
    from devbench.study.biology import CapabilityRegistration, RuntimeSupportRegistry
    plan, catalog = configuration()
    template = catalog.templates[0]
    case, target = template.cases[0], template.targets[0]
    scope = next(item for item in case.target_scopes if item.target_id == target.target_id)

    def never_called(*args, **kwargs):
        pytest.fail("A registered method was invoked")

    requirements = [("generator", template.generator_capability_id), ("scorer", target.scorer_capability_id)]
    requirements.extend(("tool", tool) for tool in (scope.tool_requirements or target.tool_requirements))
    registry = RuntimeSupportRegistry(tuple(CapabilityRegistration(
        kind=kind, capability_id=capability, template_id=template.template_id, case_ids=(case.case_id,),
        target_ids=(target.target_id,), measurement=case.measurement, handler=never_called)
        for kind, capability in requirements))
    result = build()(plan, None, tmp_path / "report", catalog=catalog, registry=registry,
                     method_metadata=[{"tool_name": "configured-method", "driver": "bulk",
                                       "target_ids": [target.target_id], "qualification": "unvalidated_numerical_implementation",
                                       "estimation_limits": {target.target_id: "This method does not implement repeated-unit inference."}}])
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    assert exported["registration_status"] == "inspected_callable_metadata_only"
    assert exported["callable_case_target_count"] == 1
    assert exported["qualification_status"] == "not_established"
    document = Path(result["paths"]["html"]).read_text()
    assert "does not implement repeated-unit inference" in document
    assert "Callable registration does not establish case-level method adequacy" in document


def test_empty_biology_dashboard_accepts_the_pure_runtime_inventory(tmp_path):
    from devbench.study.biology_registry import biology_method_metadata, biology_runtime_registry
    plan, catalog = configuration()
    registry = biology_runtime_registry(catalog)
    methods = biology_method_metadata()
    result = build()(plan, None, tmp_path / "report", catalog=catalog, registry=registry, method_metadata=methods)
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    capabilities = list(csv.DictReader(io.StringIO(Path(result["paths"]["capabilities"]).read_text())))
    assert 0 <= exported["callable_case_target_count"] <= len(capabilities)
    assert exported["qualification_status"] == "not_established"
    assert result["figure_count"] == 0 and exported["attempted_count"] == 0
    assert "unsupported_estimation_targets" in Path(result["paths"]["methods"]).read_text()
    assert "successful_intervention" in Path(result["paths"]["methods"]).read_text()


def test_empty_biology_public_projection_drops_private_payloads_and_escapes_labels(tmp_path):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    analysis = analyze_biology_attempts([], plan)
    analysis["private_plan"] = {"nonce": "PRIVATE-PLAN"}
    analysis["headline"]["raw_answer"] = "PRIVATE-ANSWER"
    analysis["headline"]["scope"] = {"unexpected_blob": "PRIVATE-NESTED"}
    analysis["problems"] = [{"code": '</script><script>alert("x")</script>', "private_reference": "PRIVATE-REFERENCE",
                             "source_path": "/private/biology/answers.json"}]
    result = build()(plan, analysis, tmp_path / "report", catalog=catalog)
    exported = "\n".join(Path(path).read_text() for path in result["paths"].values())
    assert all(secret not in exported for secret in ("PRIVATE-PLAN", "PRIVATE-ANSWER", "PRIVATE-NESTED", "PRIVATE-REFERENCE", "/private/biology"))
    document = Path(result["paths"]["html"]).read_text()
    assert '<script>alert("x")</script>' not in document and "&lt;/script&gt;" in document
    assert 'src="http' not in document and 'href="http' not in document


def test_empty_biology_filters_have_exact_accessible_names(tmp_path):
    class Controls(HTMLParser):
        def __init__(self):
            super().__init__()
            self.selects = []
            self.labels = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "select":
                self.selects.append(attrs)
            if tag == "label":
                self.labels.add(attrs.get("for"))

    plan, catalog = configuration()
    result = build()(plan, None, tmp_path / "report", catalog=catalog)
    parser = Controls()
    parser.feed(Path(result["paths"]["html"]).read_text())
    assert {item["aria-label"] for item in parser.selects} >= {"Template", "Case", "Target", "Condition"}
    assert all(item["id"] in parser.labels for item in parser.selects)


def test_empty_biology_output_rejects_existing_relative_symlink_and_wrong_identity(tmp_path):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    with pytest.raises(ValueError, match="absolute"):
        build()(plan, None, Path("relative"), catalog=catalog)
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises((ValueError, FileExistsError)):
        build()(plan, None, target, catalog=catalog)
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        build()(plan, None, tmp_path / "link" / "report", catalog=catalog)
    analysis = analyze_biology_attempts([], plan)
    analysis["plan_identity"] = "b" * 64
    with pytest.raises(ValueError, match="plan"):
        build()(plan, analysis, tmp_path / "wrong", catalog=catalog)


@pytest.mark.parametrize("payload", [
    {"run_rows": [object()]}, {"headline": {"task_success_rate": object()}},
    {"paired_comparisons": [{"mean_delta": object(), "groups": []}]},
])
def test_default_biology_result_reporting_denied_before_result_inspection(tmp_path, payload):
    plan, catalog = configuration()
    with pytest.raises(PermissionError, match="allow_result_reporting"):
        build()(plan, payload, tmp_path / "report", catalog=catalog)
    assert not (tmp_path / "report").exists()


def test_empty_biology_rejects_inconsistent_planned_counts_and_bounded_output(tmp_path, monkeypatch):
    from devbench.study import biology_dashboard
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    analysis = analyze_biology_attempts([], plan)
    wrong = {**analysis, "planned_attempt_count": 1}
    with pytest.raises(ValueError, match="planned"):
        build()(plan, wrong, tmp_path / "wrong", catalog=catalog)
    unqualified_interval = {**analysis, "bootstrap": {**analysis["bootstrap"], "minimum_independent_cohorts": 1}}
    with pytest.raises(ValueError, match="independent cohorts"):
        build()(plan, unqualified_interval, tmp_path / "low-minimum", catalog=catalog)
    monkeypatch.setattr(biology_dashboard, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(ValueError, match="bound"):
        build()(plan, analysis, tmp_path / "too-large", catalog=catalog)
    assert not (tmp_path / "too-large").exists()


def test_empty_biology_exports_unknown_report_decision_denominators(tmp_path):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    analysis = analyze_biology_attempts([], plan)
    names = ("correct_answer_report_decision_count", "incorrect_answer_report_decision_count")
    analysis["headline"].update({name: None for name in names})
    result = build()(plan, analysis, tmp_path / "report", catalog=catalog)
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    assert all(name in exported["headline"] and exported["headline"][name] is None for name in names)


def test_empty_biology_storage_receipt_distinguishes_unloaded_prediction_details(tmp_path):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    analysis = analyze_biology_attempts([], plan)
    metadata = {"safe_detail_storage": "per_attempt", "safe_rows_omitted_from_aggregate": True,
                "retained_safe_row_count": 0, "retained_safe_scalar_count": 0,
                "loaded_safe_row_count": 0, "loaded_safe_scalar_count": 0,
                "detail_task_id": None, "execution_manifest_sha256": "b" * 64,
                "retained_root_verified": False, "receipt_scope": "configuration_only",
                "clinical_qualification": False}
    analysis.update(metadata, source_detail_path="/private/biology/source-details.json")
    result = build()(plan, analysis, tmp_path / "report", catalog=catalog)
    exported = json.loads(Path(result["paths"]["summary"]).read_text())
    assert all(exported.get(key) == value for key, value in metadata.items())
    assert "source_detail_path" not in exported
    document = Path(result["paths"]["html"]).read_text()
    assert '<section id="prediction-details">' in document
    detail_section = document.split('<section id="prediction-details">', 1)[1].split('</section>', 1)[0]
    assert "Prediction details stored separately / not loaded" in detail_section
    assert "Loaded rows: 0" in detail_section and "Retained scalar values: 0" in detail_section
    assert "Not run" not in detail_section
    assert "Receipt integrity is separate from scientific or clinical qualification" in detail_section
    assert "/private/biology" not in document
    assert exported["qualification_status"] == "not_established"
    assert list(csv.DictReader(io.StringIO(Path(result["paths"]["safe_estimates"]).read_text()))) == []


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/report fixtures require opt-in")
def test_future_biology_result_figures_preserve_source_scope_and_unknown_ci(tmp_path):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    analysis = analyze_biology_attempts([], plan)
    analysis.update(status="observed_records", attempted_count=1)
    analysis["group_summaries"] = [{"condition": "baseline", "source_kind": "synthetic",
        "actual_model": "observed", "actor_type": "model_participant", "model_identity_verified": True,
        "task_success_rate": 0.5, "attempted_count": 1, "primary_eligible_attempt_count": 1}]
    result = build()(plan, analysis, tmp_path / "report", catalog=catalog, allow_result_reporting=True)
    assert result["figure_count"] > 0
    assert "interval unavailable" in Path(result["paths"]["html"]).read_text()


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical/report fixtures require opt-in")
def test_future_biology_report_suppresses_supplied_ci_with_too_few_independent_cohorts(tmp_path):
    from devbench.study.biology_metrics import analyze_biology_attempts
    plan, catalog = configuration()
    analysis = analyze_biology_attempts([], plan)
    analysis["paired_comparisons"][0]["groups"] = [{"source_kind": "synthetic", "actual_model": "observed",
        "actor_type": "model_participant", "independent_group_count": 2, "mean_delta": 0.1,
        "confidence_interval": [0.05, 0.15], "interval_reason": None}]
    result = build()(plan, analysis, tmp_path / "report", catalog=catalog, allow_result_reporting=True)
    rows = list(csv.DictReader(io.StringIO(Path(result["paths"]["comparisons"]).read_text())))
    assert all(row["ci_low"] == row["ci_high"] == "" for row in rows)
    assert all(row["interval_reason"] == "insufficient_independent_cohorts" for row in rows)
