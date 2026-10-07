"""Pure episode specification controls; no inputs, workers or providers run."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest


def api():
    from devbench.analysis import episodes
    assert hasattr(episodes, "normalize_episode_configuration"), "Pure episode normalization is missing"
    return episodes, episodes.normalize_episode_configuration


def public_task(*, cellular=False, measurement=None):
    return {
        "measurement": measurement or ("umi_counts" if cellular else "continuous"),
        "population_unit_label": "donor_id",
        "context": {"biological_unit": "donor", "condition_labels": ["control", "treated"]},
        "gene_ids": ["g1", "g2"],
        "questions": [{"target_id": "cell_state_effect" if cellular else "mean_contrast",
            "estimand": "Declared donor condition contrast", "units": "declared input units",
            "contrast": "treated minus control", "reference_population": "declared donors",
            "prediction_reference": "observed donor population"}],
    }


def specification(**changes):
    return {"question": "Estimate the declared contrast", "assay": "bulk",
            "public_task": public_task(), **changes}


def test_normalized_fields_match_the_existing_episode_configuration_contract():
    episodes, normalize = api()
    task = public_task()
    original = deepcopy(task)
    result = normalize(**specification(public_task=task))
    assert set(result) == {"question", "assay", "public_task", "limits", "fixed_options",
                           "input_kind", "execution_tier"}
    assert result["limits"] == asdict(episodes.EpisodeLimits())
    assert result["input_kind"] == "normalized_expression"
    assert result["execution_tier"] == "local_supervised"
    assert result["public_task"]["unit_hierarchy"] == {
        "population_unit": "donor", "population_unit_label": "donor_id",
        "specimen_label": "specimen_id", "library_label": "library_id"}
    assert result["public_task"]["questions"][0]["measurement_units"] == "declared input units"
    assert "units" not in result["public_task"]["questions"][0]
    assert result["fixed_options"] == {
        "validate_design_matrix": {"condition_col": "condition"},
        "mean_condition_analysis": {"condition_col": "condition"}}
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert task == original
    repeated = dict(result, limits=episodes.EpisodeLimits(**result["limits"]))
    assert normalize(**repeated) == result


@pytest.mark.parametrize("cellular", [False, True])
def test_frozen_column_batch_and_contrast_options_are_preserved(cellular):
    _, normalize = api()
    task = public_task(cellular=cellular)
    task["analysis_columns"] = {"condition": "arm", "time": "numeric_time", "dose": None,
        "covariate": None, "population_unit": "donor_id", "batches": ["site"]}
    tool = "cell_type_state_analysis" if cellular else "mean_condition_analysis"
    options = {tool: {"reference_condition": "control", "comparison_condition": "treated"}}
    before = deepcopy((task, options))
    result = normalize(**specification(assay="scrna" if cellular else "bulk", public_task=task),
                       fixed_options=options)
    assert result["fixed_options"][tool] == {
        "reference_condition": "control", "comparison_condition": "treated",
        "condition_col": "arm", "batch_cols": ["site"]}
    assert (task, options) == before
    result["fixed_options"][tool]["batch_cols"].append("other")
    assert task["analysis_columns"]["batches"] == ["site"]


@pytest.mark.parametrize("option,value", [("condition_col", "other"), ("batch_cols", []),
    ("reference_condition", "treated"), ("comparison_condition", "control")])
def test_conflicting_frozen_design_options_are_rejected(option, value):
    _, normalize = api()
    task = public_task()
    task["analysis_columns"] = {"condition": "arm", "time": "numeric_time", "dose": None,
        "covariate": None, "population_unit": "donor_id", "batches": ["site"]}
    with pytest.raises(ValueError):
        normalize(**specification(public_task=task), fixed_options={"mean_condition_analysis": {option: value}})


@pytest.mark.parametrize("location,key", [("task", "private_truth"), ("task", "reference_answer"),
    ("options", "evaluation_feedback"), ("options", "seed")])
def test_private_fields_are_rejected_at_any_nested_level(location, key):
    _, normalize = api()
    task, options = public_task(), {}
    if location == "task":
        task["context"]["nested"] = {key: {"value": "not public"}}
    else:
        options["mean_condition_analysis"] = {key: "not public"}
    with pytest.raises(ValueError):
        normalize(**specification(public_task=task), fixed_options=options)


@pytest.mark.parametrize("genes", [None, [], "g1", ["g1", "g1"], [f"g{i}" for i in range(5001)]])
def test_existing_bounded_feature_family_gate_is_preserved(genes):
    _, normalize = api()
    task = public_task()
    task["gene_ids"] = genes
    with pytest.raises(ValueError, match="bounded feature universe"):
        normalize(**specification(public_task=task))


@pytest.mark.parametrize("limits", [{"max_steps": 2}, 1])
def test_resource_configuration_requires_the_existing_episode_limits_type(limits):
    _, normalize = api()
    with pytest.raises(TypeError, match="Use EpisodeLimits"):
        normalize(**specification(), limits=limits)


@pytest.mark.parametrize("settings", [{"max_steps": 101}, {"memory_mb": 4097},
    {"max_revisions": 3}, {"max_input_bytes": 100_000_001}, {"stage_timeout_seconds": 301},
    {"timeout_seconds": float("nan")}])
def test_episode_resource_ceilings_are_unchanged(settings):
    episodes, _ = api()
    with pytest.raises(ValueError, match="episode policy"):
        episodes.EpisodeLimits(**settings)


@pytest.mark.parametrize("options", [{"minimum_units": 2}, {"timeout_sec": 301},
    {"max_mem_mb": 4097}, {"spline_df": 2}, {"unregistered_option": True}])
def test_existing_scientific_option_limits_are_reused(options):
    _, normalize = api()
    with pytest.raises(ValueError):
        normalize(**specification(), fixed_options={"mean_condition_analysis": options})


@pytest.mark.parametrize("assay,measurement,kind,cellular", [
    ("snrna", "umi_counts", "count_matrix", True),
    ("scrna", "continuous", "normalized_expression", True),
    ("bulk", "raw_counts", "raw_reads", False)])
def test_unsupported_routes_are_preserved_without_registered_options(assay, measurement, kind, cellular):
    episodes, normalize = api()
    result = normalize(**specification(assay=assay,
        public_task=public_task(cellular=cellular, measurement=measurement)), input_kind=kind)
    assert result["assay"] == assay and result["input_kind"] == kind
    assert result["fixed_options"] == {}
    assert not episodes._support(result["assay"], result["input_kind"], result["public_task"])
    with pytest.raises(ValueError, match="registered actions"):
        normalize(**dict(result, limits=episodes.EpisodeLimits(**result["limits"]),
                         fixed_options={"audit_expression_quality": {}}))


def test_normalization_does_not_access_files_data_workers_or_runtime(monkeypatch):
    episodes, normalize = api()
    from devbench.analysis import resources, runtime
    from devbench.study import biology_tool_io, biology_tools_bulk, biology_tools_singlecell
    from devharness_core import provenance

    def forbidden(*args, **kwargs):
        raise AssertionError("Pure normalization reached an execution or filesystem boundary")

    with monkeypatch.context() as guard:
        for module, name in ((episodes, "_sources"), (resources, "configure_threads"),
            (runtime, "observe_runtime"), (biology_tool_io, "load_observed_data"),
            (biology_tools_bulk, "analyze_bulk_tool"), (biology_tools_singlecell, "analyze_singlecell_tool"),
            (provenance, "ExecutionDAG"), (provenance, "copy_artifact"), (provenance, "hash_artifact")):
            guard.setattr(module, name, forbidden)
        for name in ("open", "mkdir", "stat", "resolve"):
            guard.setattr(Path, name, forbidden)
        guard.setattr("builtins.open", forbidden)
        result = normalize(**specification())
    assert result["public_task"]["measurement"] == "continuous"


def test_create_denies_before_configuration_or_filesystem_access(monkeypatch):
    episodes, _ = api()
    from devbench.analysis import resources

    def forbidden(*args, **kwargs):
        raise AssertionError("Unauthorized create crossed its execution boundary")

    monkeypatch.setattr(episodes, "normalize_episode_configuration", forbidden)
    monkeypatch.setattr(episodes, "Path", forbidden)
    monkeypatch.setattr(resources, "configure_threads", forbidden)
    with pytest.raises(PermissionError, match="data execution is disabled"):
        episodes.AnalysisEpisode.create(None, question=None, assay=None, public_task=None, inputs=None)


def test_create_reuses_normalization_before_input_storage(monkeypatch):
    episodes, normalize = api()
    from devbench.analysis import resources
    expected = normalize(**specification())
    observed = []

    def capture(**kwargs):
        value = normalize(**kwargs)
        observed.append(value)
        return value

    monkeypatch.setattr(episodes, "normalize_episode_configuration", capture)
    monkeypatch.setattr(resources, "configure_threads", lambda: None)
    with pytest.raises(ValueError, match="Bind expression, metadata and public annotations"):
        episodes.AnalysisEpisode.create(None, **specification(), inputs={}, allow_data_execution=True)
    assert observed == [expected]
