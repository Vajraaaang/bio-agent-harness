"""Receipt and scope tests for the supervised motor program comparison."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import anndata
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from devbench.analysis.workbench_inputs import InputDiscovery
from devbench.analysis.workbench_motor_programs import MARKERS
from devbench.engine.workflow_operations import OperationBlocked


def _profile(root, filename, day, *, somatic=False, omit=None):
    genes = [gene for gene in MARKERS if gene != omit]
    motor = (
        ("Isl1", "Tubb3", "Mnx1", "Lhx3")
        if somatic
        else (
            "Isl1",
            "Tubb3",
            "Phox2b",
            "Tbx20",
        )
    )
    programs = [motor, ("Isl1", "Tubb3"), motor]
    counts = csr_matrix(
        np.array([[int(gene in row) for gene in genes] for row in programs])
    )
    adata = anndata.AnnData(
        counts.copy(),
        obs=pd.DataFrame(
            {
                "age": [day] * 3,
                "celltypes": ["Neuron: Motor neuron", "Other", "Other"],
                "predicted_labels": ["Other", "Neuroblast: Motor neuron", "Other"],
            },
            index=[f"cell-{i}" for i in range(3)],
        ),
        var=pd.DataFrame(
            {"gene_ids": [f"ENSMUSG{i:011}" for i in range(len(genes))]},
            index=genes,
        ),
        layers={"counts": counts},
    )
    path = root / filename
    adata.write_h5ad(path)
    return {
        **InputDiscovery([root]).inspect(path),
        "role": "expression",
        "data_classification": "private",
    }


@pytest.fixture
def comparison(workspace_tmp_path):
    from devbench.platform.plugin_motor_programs import motor_program_runtime

    root = workspace_tmp_path / "inputs"
    root.mkdir()
    workspace = workspace_tmp_path / "plugin"
    workspace.mkdir()
    profiles = [
        _profile(root, "day-10.h5ad", "e10", somatic=True),
        _profile(root, "day-9.h5ad", "e9"),
    ]
    plan = {
        "operation": "motor_program_comparison",
        "data_classification": "private",
        "input_root": str(root),
        "allow_external_input_files": False,
        "profiles": profiles,
        "design": {"parameters": {}},
        "runtime": motor_program_runtime(),
    }
    stages = []
    jobs = SimpleNamespace(
        workspace=workspace, stage=lambda _rid, **step: stages.append(step)
    )
    return jobs, plan, stages


def test_motor_program_comparison_publishes_source_bound_observations(comparison):
    from devbench.platform.plugin_motor_programs import run_motor_program_comparison

    jobs, plan, stages = comparison
    rid = "a" * 32
    result = run_motor_program_comparison(jobs, rid, plan)
    assert result["metrics"]["observations_screened"] == 6
    assert result["metrics"]["sources"] == 2
    assert {stage["step"] for stage in stages} >= {
        "screening_motor_programs",
        "comparing_motor_programs",
    }
    proportions, denominators, marker_support = result["summary_tables"]
    assert proportions["columns"][1:3] == ["Day 9 (%)", "Day 10 (%)"]
    assert [row[1:3] for row in proportions["rows"][:2]] == [
        [33.3333, 0.0],
        [0.0, 33.3333],
    ]
    assert [row[2] for row in denominators["rows"]] == [3, 3]
    assert marker_support["rows"]
    artifact = result["result_artifact"]
    raw = (jobs.workspace / artifact["path"]).read_bytes()
    assert len(raw) == artifact["size_bytes"]
    assert hashlib.sha256(raw).hexdigest() == artifact["sha256"]
    output = json.loads(raw)
    assert output["clinical_release_allowed"] is False
    assert output["runtime"] == plan["runtime"]
    assert [source["sha256"] for source in output["sources"]] == [
        profile["sha256"] for profile in plan["profiles"]
    ]
    assert all(source["screen"]["row_codes_base64"] for source in output["sources"])
    for profile in plan["profiles"]:
        assert (
            hashlib.sha256(Path(profile["source"]).read_bytes()).hexdigest()
            == profile["sha256"]
        )


def test_motor_program_comparison_refuses_changed_runtime_before_screening(comparison):
    from devbench.platform.plugin_motor_programs import run_motor_program_comparison

    jobs, plan, stages = comparison
    plan["runtime"]["implementation_sha256"] = "0" * 64
    with pytest.raises(OperationBlocked, match="motor_program_runtime_changed"):
        run_motor_program_comparison(jobs, "a" * 32, plan)
    assert not stages
    assert not (jobs.workspace / "runs").exists()


@pytest.mark.parametrize(
    ("issue", "reason"),
    [
        ("count", "motor_program_requires_two_to_eight_expression_inputs"),
        ("duplicate", "motor_program_requires_distinct_input_contents"),
        ("layer", "motor_program_missing_counts_layer"),
        ("annotations", "motor_program_missing_annotation_columns"),
    ],
)
def test_motor_program_profiles_require_exact_compatible_sources(
    comparison, issue, reason
):
    from devbench.platform.plugin_motor_programs import validate_motor_program_profiles

    _, plan, _ = comparison
    profiles = plan["profiles"]
    if issue == "count":
        profiles.pop()
    elif issue == "duplicate":
        profiles[1]["sha256"] = profiles[0]["sha256"]
    elif issue == "layer":
        profiles[0]["observed"]["layers"] = []
    else:
        profiles[0]["observed"]["obs_columns"] = ["celltypes", "age"]
    with pytest.raises(OperationBlocked, match=reason):
        validate_motor_program_profiles(profiles)


def test_missing_marker_panel_does_not_seal_partial_comparison(comparison):
    from devbench.platform.plugin_motor_programs import run_motor_program_comparison

    jobs, plan, _ = comparison
    root = Path(plan["input_root"])
    plan["profiles"][1] = _profile(root, "incomplete.h5ad", "e9", omit="Tbx20")
    rid = "a" * 32
    with pytest.raises(OperationBlocked, match="motor_program_marker_panel_incomplete"):
        run_motor_program_comparison(jobs, rid, plan)
    assert not (
        jobs.workspace / "runs" / rid / "motor-program-comparison.json"
    ).exists()


def test_motor_program_comparison_refuses_source_changes_after_planning(comparison):
    from devbench.platform.plugin_motor_programs import run_motor_program_comparison

    jobs, plan, _ = comparison
    path = Path(plan["profiles"][0]["source"])
    with path.open("ab") as handle:
        handle.write(b"changed after planning")
    with pytest.raises(OperationBlocked, match="motor_program_source_changed"):
        run_motor_program_comparison(jobs, "a" * 32, plan)
    assert not (jobs.workspace / "runs").exists()


def test_motor_program_comparison_does_not_pool_two_sources_from_one_day(comparison):
    from devbench.platform.plugin_motor_programs import run_motor_program_comparison

    jobs, plan, _ = comparison
    root = Path(plan["input_root"])
    plan["profiles"][1] = _profile(root, "same-day.h5ad", "e10.5")
    with pytest.raises(
        OperationBlocked, match="motor_program_requires_distinct_recorded_days"
    ):
        run_motor_program_comparison(jobs, "a" * 32, plan)
    assert not (jobs.workspace / "runs").exists()
