"""Preserve declared assays and avoid inventing single-cell coordinates."""
from copy import deepcopy

import pytest

from devbench.analysis.episodes import EpisodeLimits, normalize_episode_configuration
from devbench.study.biology_tool_io import validate_public_task
from tests.test_episode_configuration import public_task


@pytest.mark.parametrize("placement", ["measurement", "context", "measurement_type"])
@pytest.mark.parametrize("layer", ["counts", "layers/counts"])
def test_episode_preserves_selected_layer_before_second_validation(placement, layer):
    task = public_task(cellular=True)
    declaration = {"measurement": "umi_counts", "layer": layer}
    if placement == "context":
        task["context"]["measurement"] = declaration
    else:
        task.pop("measurement")
        task[placement] = declaration
    task["transformation_history"] = [{"operation": "log1p", "output_layer": "X"}]
    original = deepcopy(task)
    assert validate_public_task(task)["measurement_layer"] == declaration
    result = normalize_episode_configuration(question="Declared condition contrast", assay="scrna", public_task=task)
    assert result["public_task"]["measurement_layer"] == declaration
    assert validate_public_task(result["public_task"])["measurement_layer"] == declaration
    assert normalize_episode_configuration(**dict(result, limits=EpisodeLimits(**result["limits"]))) == result
    assert task == original


@pytest.mark.parametrize("assay", ["scrna", "snrna"])
def test_new_singlecell_specification_does_not_declare_unprovided_time(assay):
    result = normalize_episode_configuration(question="Declared condition contrast", assay=assay,
        public_task=public_task(cellular=True))
    assert result["public_task"]["analysis_columns"]["time"] is None


def test_legacy_bulk_default_and_plain_assay_serialization_are_preserved():
    task = public_task()
    result = normalize_episode_configuration(question="Declared condition contrast", assay="bulk", public_task=task)
    assert result["public_task"]["analysis_columns"]["time"] == "numeric_time"
    assert "measurement_layer" not in result["public_task"]


def test_explicit_singlecell_coordinate_is_not_removed():
    task = public_task(cellular=True)
    task["context"]["analysis_columns"] = {"time": "visit_day"}
    result = normalize_episode_configuration(question="Declared condition contrast", assay="scrna", public_task=task)
    assert result["public_task"]["analysis_columns"]["time"] == "visit_day"


def test_selected_layer_transformation_contradiction_still_refuses():
    task = public_task(cellular=True)
    task["measurement"] = {"measurement": "umi_counts", "layer": "counts"}
    task["transformation_history"] = [{"operation": "log1p", "output_layer": "layers/counts"}]
    with pytest.raises(ValueError, match="Transformation history"):
        normalize_episode_configuration(question="Declared condition contrast", assay="scrna", public_task=task)
