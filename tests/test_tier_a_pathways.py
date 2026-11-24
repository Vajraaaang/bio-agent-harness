"""Pathway registration on the normal durable WorkflowActions route."""

from __future__ import annotations

import os

import pytest

from devbench.core.rna_pathway_analysis import (
    GeneSet,
    PathwayPlan,
    PathwayResult,
    admit_pathway_request,
    ranked_scores_sha256,
)
from devbench.core.rna_pathway_analysis import (
    TestedGeneUniverse as GeneUniverse,
)
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.platform.adaptive_workflow import request_digest
from devbench.platform.adaptive_workflow_state import ArtifactVersion, BasisUse
from devbench.platform.workflow_actions import WorkflowActions
from devharness_controlled.native_contracts import pathway_kernel_images
from tests.test_investigation_invalidation import digest, make
from tests.test_p3_rna_pathways import (
    _camera_input,
    _mounts,
    _plan,
    _run_pinned,
    _universe,
)
from tests.test_tier_a_executor import _fixture, _submit

pytestmark = pytest.mark.wal
_FIXTURE_IMAGE = "sha256:" + "f" * 64


def _workflow(root, image):
    flow = make(root)
    executor = TierAExecutor(
        flow.ledger,
        allow_data_execution=True,
        mounted_image_id=image,
    )
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="operator",
        selector=None,
        tier_a_executor=executor,
        allow_data_execution=True,
    )
    return flow, executor, actions


def _camera_artifacts(root, flow, executor):
    expression, design, camera = _camera_input(root)
    for path, version, kind, representation in (
        (
            expression,
            "pathway-expression",
            "continuous_matrix",
            "pathway_expression_f64",
        ),
        (design, "pathway-design", "table", "pathway_design_f64"),
    ):
        flow.register_artifact(
            ArtifactVersion(
                version_id=version,
                sha256=executor.training.put_file(path),
                kind=kind,
                representation=representation,
                biological_unit="donor",
                unit_membership_sha256=digest("aligned-donors"),
            )
        )
    return camera


def _register(executor, flow, camera, *, basis=(), verify_design=None):
    return executor.register_pathway_step(
        flow,
        _plan("auto"),
        step_id="camera",
        branch_id="pathway",
        biological_unit="donor",
        sources={
            "pathway_expression": ("pathway-expression", None),
            "pathway_design": ("pathway-design", None),
        },
        basis=basis,
        camera=camera,
        verify_design=verify_design,
    )


def _fixture_image(monkeypatch):
    from devharness_controlled import native_contracts

    monkeypatch.setitem(
        native_contracts.PATHWAY_KERNEL_IMAGES, "linux/arm64", _FIXTURE_IMAGE
    )
    return _FIXTURE_IMAGE


def test_pathway_registration_requires_independent_design_and_current_basis(
    workspace_tmp_path,
    monkeypatch,
):
    flow, executor, _ = _workflow(workspace_tmp_path, _fixture_image(monkeypatch))
    camera = _camera_artifacts(workspace_tmp_path, flow, executor)
    with pytest.raises(PermissionError, match="independently checked design receipt"):
        _register(executor, flow, camera)
    with pytest.raises(PermissionError, match="current pathway evidence basis"):
        _register(executor, flow, camera, verify_design=lambda actual: actual == camera)
    assert not flow.authority.attempts(flow.lineage_id)


def test_ranked_and_directional_recipes_check_the_retained_gene_family(monkeypatch):
    from io import BytesIO

    import pyarrow as pa
    import pyarrow.parquet as pq

    from devbench.engine import tier_a_pathways
    from devbench.engine.tier_a_scrna import mounted_single_cell_request

    universe = _universe()
    effects = [1.0, -1.0, *([0.0] * 6), 8.0]
    rows = pa.table(
        {
            "gene_id": [*universe.genes, "untested"],
            "estimate_status": [*(["estimated"] * 8), "filtered"],
            "p_value": [*([0.01] * 8), None],
            "q_value": [*([0.04] * 8), None],
            "effect": effects,
        }
    )
    handle = BytesIO()
    pq.write_table(rows, handle)
    monkeypatch.setattr(
        tier_a_pathways, "_verify_tested_receipt", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        tier_a_pathways, "open_pathway_input", lambda *_: BytesIO(handle.getvalue())
    )
    ranked_plan = _plan("fgsea")
    scores = dict(zip(universe.genes, effects, strict=False))
    ranked = universe.model_copy(
        update={
            "ranked_scores_sha256": ranked_scores_sha256(
                universe.genes, tuple(scores.values())
            )
        }
    )
    accepted = admit_pathway_request(
        ranked_plan,
        universe=ranked,
        verify_universe=lambda _: True,
        ranked_scores=scores,
    )
    tier_a_pathways._verify_retained_tested_family(None, None, None, accepted)
    foreign = dict(scores)
    foreign["untested"] = foreign.pop("g7")
    with pytest.raises(ValueError, match="scores for every tested gene"):
        admit_pathway_request(
            ranked_plan,
            universe=ranked,
            verify_universe=lambda _: True,
            ranked_scores=foreign,
        )
    altered_axis = ranked.model_copy(
        update={
            "genes": tuple(foreign),
            "ranked_scores_sha256": ranked_scores_sha256(
                tuple(foreign), tuple(foreign.values())
            ),
        }
    )
    altered = admit_pathway_request(
        ranked_plan,
        universe=altered_axis,
        verify_universe=lambda _: True,
        ranked_scores=foreign,
    )
    with pytest.raises(
        PermissionError, match="exact ordered filtered tested background"
    ):
        tier_a_pathways._verify_retained_tested_family(None, None, None, altered)

    plan = _plan("ora").model_copy(update={"foreground_direction": "up_only"})
    accepted = admit_pathway_request(
        plan, universe=universe, verify_universe=lambda _: True, foreground=("g0",)
    )
    tier_a_pathways._verify_retained_tested_family(None, None, None, accepted)
    wrong_sign = admit_pathway_request(
        plan, universe=universe, verify_universe=lambda _: True, foreground=("g1",)
    )
    with pytest.raises(PermissionError, match="retained effect direction"):
        tier_a_pathways._verify_retained_tested_family(None, None, None, wrong_sign)
    spec = tier_a_pathways.PathwayStageSpec(
        operation="rna_pathway_tested", lineage_id="fixture", request=accepted
    )
    reloaded = tier_a_pathways.PathwayStageSpec.model_validate_json(
        spec.model_dump_json()
    )
    assert reloaded.admitted_request.plan.foreground_direction == "up_only"
    assert reloaded.admitted_request.universe.selected == ("g0", "g1")
    mounted = mounted_single_cell_request(None, reloaded, {}, envelope={})
    assert "foreground_direction" not in mounted["plan"]
    assert mounted["universe"]["genes"] == list(universe.genes)
    assert mounted["universe"]["selected"] == mounted["foreground"] == ["g0"]


def test_pinned_ora_worker_runs_directional_selection(workspace_tmp_path):
    from devbench.engine.tier_a_pathways import PathwayStageSpec

    inputs, outputs = _mounts(workspace_tmp_path)
    universe = _universe()
    request = admit_pathway_request(
        _plan("ora").model_copy(update={"foreground_direction": "down_only"}),
        universe=universe,
        verify_universe=lambda actual: actual == universe,
        foreground=("g1",),
    )
    recipe = PathwayStageSpec(
        operation="rna_pathway_tested", lineage_id="fixture", request=request
    )
    result = _run_pinned(inputs, outputs, recipe.request)
    assert result.status == "completed"
    assert result.tested_gene_count == 8
    assert result.results[0].p_value == pytest.approx(3 / 8)


def test_registered_camera_refuses_stale_evidence_before_dispatch(
    workspace_tmp_path,
    monkeypatch,
):
    flow, executor, actions = _workflow(workspace_tmp_path, _fixture_image(monkeypatch))
    camera = _camera_artifacts(workspace_tmp_path, flow, executor)
    flow.record_basis(
        "pathway-design-review", "review_basis", camera.design_receipt_sha256
    )
    step = _register(
        executor,
        flow,
        camera,
        verify_design=lambda actual: actual == camera,
        basis=(BasisUse(basis_id="pathway-design-review"),),
    )
    flow.insert(step)
    flow.record_basis("pathway-design-review", "review_basis", digest("revised-design"))
    with pytest.raises(PermissionError, match="current pathway evidence basis"):
        actions.perform_adaptive(flow, step.step_id)
    assert not flow.authority.attempts(flow.lineage_id)


def test_pathway_registration_refuses_genome_background(
    workspace_tmp_path, monkeypatch
):
    flow, executor, _ = _workflow(workspace_tmp_path, _fixture_image(monkeypatch))
    universe = _universe().model_copy(update={"background_scope": "whole_genome"})
    with pytest.raises(PermissionError, match="filtered tested background"):
        executor.register_pathway_step(
            flow,
            _plan("ora"),
            step_id="sets",
            branch_id="pathway",
            biological_unit="donor",
            sources={},
            universe=universe,
            verify_universe=lambda _: True,
            foreground=universe.selected,
        )
    assert not flow.authority.attempts(flow.lineage_id)


def test_registered_camera_executes_pinned_oci_and_reopens_receipt(workspace_tmp_path):
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in pathway_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to a pinned pathway image")
    flow, executor, actions = _workflow(workspace_tmp_path, image)
    camera = _camera_artifacts(workspace_tmp_path, flow, executor)
    flow.record_basis(
        "pathway-design-review", "review_basis", camera.design_receipt_sha256
    )
    step = _register(
        executor,
        flow,
        camera,
        verify_design=lambda actual: actual == camera,
        basis=(BasisUse(basis_id="pathway-design-review"),),
    )
    flow.insert(step)
    output = actions.perform_adaptive(flow, step.step_id)
    assert output["status"] == "succeeded", output
    artifact = ArtifactVersion.model_validate(output["outputs"]["pathway_result"])
    assert artifact.permitted_uses == ("checked_observation", "exploratory")
    retained = TierAExecutor(flow.ledger, mounted_image_id=image)
    with retained.open_output(artifact) as handle:
        result = PathwayResult.model_validate_json(handle.read(8_000_001))
    assert result.package_version == "3.68.5"
    assert result.method == "camera"
    assert 0 <= result.results[0].p_value <= result.results[0].q_value <= 1
    assert result.results[0].variance_inflation > 1
    assert result.clinical_release_allowed is False


def test_registered_ora_checks_retained_product_axis_before_mounted_dispatch(
    workspace_tmp_path,
):
    import pyarrow.parquet as pq

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in pathway_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to a pinned pathway image")
    flow, executor, envelope = _fixture(
        workspace_tmp_path,
        mounted_image_id=image,
        input_format="h5",
        omnibus="lrt",
    )
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded", receipt.reason
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    artifact = receipt.outputs["result"]
    flow.register_artifact(artifact)
    with executor.open_output(artifact) as handle:
        rows = pq.read_table(handle).to_pylist()
    tested = tuple(
        row["gene_id"] for row in rows if row["estimate_status"] == "estimated"
    )
    selected = tuple(
        row["gene_id"]
        for row in rows
        if row["estimate_status"] == "estimated" and row["q_value"] <= 0.05
    )
    assert 3 <= len(selected) < len(tested)
    universe = GeneUniverse(
        result_receipt_sha256=request_digest(receipt.model_dump(mode="json")),
        gene_statistics_sha256=artifact.sha256,
        filter_receipt_sha256=digest("frozen-filter-receipt"),
        genes=tested,
        selected=selected,
    )
    flow.record_basis("tested-de", "source", universe.result_receipt_sha256)
    flow.record_basis("tested-filter", "review_basis", universe.filter_receipt_sha256)
    plan = PathwayPlan(
        method="ora",
        collection_id="synthetic",
        collection_version="1",
        min_set_size=2,
        gene_sets=(GeneSet(set_id="planted", genes=tuple(f"g{i}" for i in range(12))),),
    )

    def register(declared):
        return executor.register_pathway_step(
            flow,
            plan,
            step_id="ora",
            branch_id="enrichment",
            biological_unit="donor",
            sources={"pathway_statistics": (artifact.version_id, None)},
            basis=(BasisUse(basis_id="tested-de"), BasisUse(basis_id="tested-filter")),
            universe=declared,
            verify_universe=lambda actual: actual == declared,
            foreground=declared.selected,
        )

    expanded = universe.model_copy(update={"genes": (*tested, "unmeasured")})
    with pytest.raises(
        PermissionError, match="exact ordered filtered tested background"
    ):
        register(expanded)
    step = register(universe)
    flow.insert(step)
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="operator",
        selector=None,
        tier_a_executor=executor,
        allow_data_execution=True,
    )
    outcome = actions.perform_adaptive(flow, step.step_id)
    assert outcome["status"] == "succeeded", outcome
    result_artifact = ArtifactVersion.model_validate(
        outcome["outputs"]["pathway_result"]
    )
    with executor.open_output(result_artifact) as handle:
        result = PathwayResult.model_validate_json(handle.read(8_000_001))
    assert result.method == "ora"
    assert result.tested_gene_count == len(tested)
    assert 0 <= result.results[0].p_value <= result.results[0].q_value <= 1
