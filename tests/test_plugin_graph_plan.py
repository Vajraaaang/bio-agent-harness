"""Exact graph dependencies on the existing MCP planning surface."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.plugin_jobs import digest
from devbench.platform.plugin_mcp import Input, Plan, Plugin, run_frozen_plan


def _request(operation):
    request = {"operation": operation, "objective": "Check retained synthetic inputs"}
    if operation == "shared_motor_umap":
        request["inputs"] = [{"path": f"/data/day-{index}.h5ad"} for index in range(3)]
    return request


@pytest.mark.parametrize(
    "operation", ["matrix_audit", "shared_motor_umap", "motor_program_comparison"]
)
def test_graph_plans_freeze_exact_same_session_dependencies(operation):
    dependency = {"plan_id": "b" * 32, "plan_sha256": "c" * 64}
    parsed = Plan.model_validate(
        {**_request(operation), "session_id": "a" * 32, "depends_on": [dependency]}
    )
    assert parsed.root.depends_on[0].model_dump() == dependency
    with pytest.raises(
        ValidationError, match="dependencies require an existing session"
    ):
        Plan.model_validate({**_request(operation), "depends_on": [dependency]})
    with pytest.raises(ValidationError, match="duplicate graph dependency"):
        Plan.model_validate(
            {
                **_request(operation),
                "session_id": "a" * 32,
                "depends_on": [dependency, dependency],
            }
        )


@pytest.mark.parametrize(
    "operation", ["matrix_audit", "qc-salmon", "shared_motor_umap", "scrna_ccc"]
)
def test_legacy_unstarted_graph_route_requires_replanning(operation):
    with pytest.raises(OperationBlocked, match="plugin_graph_replan_required"):
        run_frozen_plan(None, "a" * 32, {"operation": operation})


def test_followup_checks_original_scope_and_dependency_digest():
    session = "a" * 32
    source = Input(path="/data/source.csv", data_classification="synthetic")
    metadata = Input(
        path="/data/design.csv", data_classification="synthetic", role="metadata"
    )
    source_profile = {
        "sha256": "1" * 64,
        "size_bytes": 32,
        "data_classification": "synthetic",
    }
    metadata_profile = {
        "sha256": "2" * 64,
        "size_bytes": 32,
        "data_classification": "synthetic",
    }
    previous = {
        "session_id": session,
        "plan_id": "b" * 32,
        "input_root": "/data",
        "input_declarations": [source.model_dump(), metadata.model_dump()],
        "profiles": [source_profile, metadata_profile],
        "design": {},
        "data_classification": "synthetic",
        "graph_runtime": {},
    }
    lookup = {source.path: source_profile, metadata.path: metadata_profile}
    plugin = object.__new__(Plugin)
    plugin.input_root = Path("/data")
    plugin.jobs = SimpleNamespace(plan=lambda *_: previous)
    plugin._profiles = lambda inputs, **_: [lookup[item.path] for item in inputs]
    request = Plan.model_validate(
        {
            **_request("matrix_audit"),
            "session_id": session,
            "inputs": [source.model_dump()],
            "depends_on": [
                {"plan_id": previous["plan_id"], "plan_sha256": digest(previous)}
            ],
        }
    ).root
    profiles, before = plugin._plan_inputs(request)
    retained = {"profiles": profiles}
    plugin._retain_plan_scope(retained, request, before)
    assert retained["session_roots"] == [
        source_profile["sha256"],
        metadata_profile["sha256"],
    ]
    assert retained["dependencies"][0]["plan_sha256"] == digest(previous)

    request.depends_on[0].plan_sha256 = "c" * 64
    with pytest.raises(
        OperationBlocked, match="graph_dependency_plan_identity_mismatch"
    ):
        plugin._retain_plan_scope({"profiles": profiles}, request, before)

    lookup[source.path] = {**source_profile, "sha256": "3" * 64}
    with pytest.raises(OperationBlocked, match="session_input_scope_mismatch"):
        plugin._plan_inputs(request)
