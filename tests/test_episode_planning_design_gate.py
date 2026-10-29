"""Planning uses the executable bulk design checks before exposing a route."""
import csv
from pathlib import Path

import pytest

from tests.test_analysis_episodes import inputs


def preparation(tmp_path, *, confounded=False, options=None):
    from devbench.analysis.episode_planning import _dataset
    from devbench.analysis.episodes import EpisodeLimits
    task, paths = inputs(tmp_path, confounded=confounded)
    task.pop("questions")
    dataset = {"assay": "bulk", "public_task": task,
               "inputs": {key: str(value) for key, value in paths.items()}}
    assay, kind, task, paths = _dataset("Compare B with A", dataset, EpisodeLimits(), options or {}, "local_supervised")
    return assay, kind, task, paths


def update_metadata(paths, change):
    path = Path(paths["metadata"])
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    change(rows)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def inspect_context(prepared, options=None):
    from devbench.analysis.episode_planning import _inspect_worker, _context
    assay, kind, task, paths = prepared
    inspection = (_inspect_worker(task, paths, assay, options) if options
                  else _inspect_worker(task, paths, assay))
    task["gene_ids"] = inspection["gene_ids"]
    return _context("Compare B with A", assay, kind, task, inspection, options or {})


@pytest.mark.parametrize("defect,blocker", [
    ("confounded", "rank_deficient_design"),
    ("paired", "repeated_units_across_design_coordinates_require_cluster_model"),
    ("missing_batch", "missing_declared_batch_column"),
    ("residual_df", "insufficient_independent_residual_degrees_of_freedom"),
])
def test_bad_design_never_offers_inferential_method(tmp_path, defect, blocker):
    prepared = preparation(tmp_path, confounded=defect == "confounded")
    if defect == "paired":
        update_metadata(prepared[3], lambda rows: [row.update(donor_id=f"d{i % 4}") for i, row in enumerate(rows)])
    elif defect == "missing_batch":
        prepared[2]["analysis_columns"]["batches"] = ["absent_batch"]
    elif defect == "residual_df":
        labels = ["a", "b", "c", "d", "a", "b", "e", "f"]
        update_metadata(prepared[3], lambda rows: [row.update(batch=labels[i]) for i, row in enumerate(rows)])
        # Crossed nuisance levels leave only one residual degree of freedom.
    context = inspect_context(prepared)
    assert context["allowed_workflows"] == []
    assert blocker in context["observed"]["design_audit"]["blockers"]
    assert "propose" not in context["admission"]["available_agent_actions"]


def test_supported_design_is_audited_without_statistical_fit(tmp_path, monkeypatch):
    from devbench.study import biology_tools_bulk
    def forbidden(*args, **kwargs):
        pytest.fail("Planning must never fit expression")
    monkeypatch.setattr(biology_tools_bulk, "_fit_family", forbidden)
    context = inspect_context(preparation(tmp_path))
    audit = context["observed"]["design_audit"]
    assert context["allowed_workflows"] == ["bulk_mean_contrast"]
    assert audit["status"] == "passed"
    assert audit["blockers"] == []
    assert audit["design"]["rank"] == audit["design"]["column_count"] == 2
    assert audit["biological_units"]["analysis_unit_count"] == 8
    assert context["admission"]["scientific_status"]["statistical_calibration"] == "not_established"


def test_technical_libraries_aggregate_before_design_replication_check(tmp_path):
    prepared = preparation(tmp_path)
    # Eight columns are only four independent donors; neither group reaches 3.
    update_metadata(prepared[3], lambda rows: [row.update(donor_id=f"d{i // 2}") for i, row in enumerate(rows)])
    context = inspect_context(prepared)
    assert context["allowed_workflows"] == []
    assert "insufficient_independent_units_in_observed_design_strata" in context["observed"]["design_audit"]["blockers"]


def test_coordinator_minimum_is_used_by_preselection_design_gate(tmp_path):
    options = {"mean_condition_analysis": {"minimum_units": 5}}
    context = inspect_context(preparation(tmp_path, options=options), options)
    assert context["allowed_workflows"] == []
    assert "insufficient_independent_units_in_observed_design_strata" in context["observed"]["design_audit"]["blockers"]


def test_extra_condition_is_not_silently_audited_as_supported_bulk_subset(tmp_path):
    prepared = preparation(tmp_path)
    prepared[2]["context"]["requested_condition_contrast"] = {"numerator": "B", "denominator": "A"}
    update_metadata(prepared[3], lambda rows: rows[-1].update(condition="C"))
    context = inspect_context(prepared)
    assert context["allowed_workflows"] == []
    assert "condition_contrast_requires_two_distinct_supported_levels" in context["observed"]["design_audit"]["blockers"]


@pytest.mark.parametrize("column", ["condition", "batch"])
def test_missing_design_value_is_not_a_new_factor_level(tmp_path, column):
    prepared = preparation(tmp_path)
    update_metadata(prepared[3], lambda rows: rows[0].update({column: ""}))
    context = inspect_context(prepared)
    assert context["allowed_workflows"] == []
    assert "missing_declared_design_values:" + column in context["observed"]["design_audit"]["blockers"]


def test_context_only_roles_are_retained_without_invented_time(tmp_path):
    from devbench.analysis.episode_planning import _dataset
    from devbench.analysis.episodes import EpisodeLimits
    task, paths = inputs(tmp_path)
    task.pop("questions")
    roles = task.pop("analysis_columns")
    roles["time"] = None
    roles["condition"] = "arm"
    task["context"]["analysis_columns"] = roles.copy()
    _, _, normalized, _ = _dataset("Compare B with A", {"assay": "bulk", "public_task": task,
        "inputs": {key: str(path) for key, path in paths.items()}}, EpisodeLimits(), {}, "local_supervised")
    assert normalized["analysis_columns"] == roles
    assert normalized["context"]["analysis_columns"] == roles


def test_explicit_requested_direction_is_exposed_and_cannot_be_reversed(tmp_path):
    import jsonschema
    from devbench.analysis.episode_planning import _compile, proposal_schema
    from devbench.analysis.episodes import EpisodeLimits
    prepared = preparation(tmp_path)
    explicit = {"numerator": "A", "denominator": "B"}
    prepared[2]["context"]["requested_condition_contrast"] = explicit.copy()
    context = inspect_context(prepared)
    reversed_decision = {"kind": "propose", "workflow": "bulk_mean_contrast",
        "reference_condition": "A", "comparison_condition": "B", "rationale": "Reversed request"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(proposal_schema(context)).validate(reversed_decision)
    with pytest.raises(ValueError, match="declared_condition_contrast"):
        _compile("A minus B", *prepared[:3], prepared[3], reversed_decision,
                 EpisodeLimits(), {}, "local_supervised")
    assert context["declarations"]["requested_condition_contrast"] == explicit
    assert context["declarations"]["evidence_scope"] == "supplied_declarations_not_independently_authenticated"
    assert prepared[2]["context"]["requested_condition_contrast"] == explicit


def test_matching_explicit_direction_survives_compilation(tmp_path):
    from devbench.analysis.episode_planning import _compile
    from devbench.analysis.episodes import EpisodeLimits
    prepared = preparation(tmp_path)
    explicit = {"numerator": "A", "denominator": "B"}
    prepared[2]["context"]["requested_condition_contrast"] = explicit.copy()
    decision = {"kind": "propose", "workflow": "bulk_mean_contrast",
        "reference_condition": "B", "comparison_condition": "A", "rationale": "Follow supplied contrast"}
    spec = _compile("A minus B", *prepared[:3], prepared[3], decision,
                    EpisodeLimits(), {}, "local_supervised")
    assert spec["public_task"]["context"]["requested_condition_contrast"] == explicit


def test_incomplete_explicit_contrast_needs_information_not_a_default(tmp_path):
    from devbench.analysis.episode_planning import proposal_schema
    prepared = preparation(tmp_path)
    prepared[2]["context"]["requested_condition_contrast"] = {"numerator": "B"}
    context = inspect_context(prepared)
    assert context["allowed_workflows"] == []
    assert "explicit_condition_contrast_incomplete" in context["observed"]["design_audit"]["blockers"]
    assert len(proposal_schema(context)["oneOf"]) == 2
