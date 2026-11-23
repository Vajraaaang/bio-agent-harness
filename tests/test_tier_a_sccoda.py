"""Exercise scCODA through the registered single-cell action path."""

import json
import os

import pytest

from devbench.engine.tier_a_executor import TierAExecutor
from devbench.platform.adaptive_workflow import ArtifactVersion
from devbench.platform.workflow_actions import WorkflowActions
from rna_kernels.canonical import canonical_json
from tests.test_investigation_invalidation import digest, make
from tests.test_p3_scrna_composition import _planted_input

pytestmark = pytest.mark.wal
_REGISTRY = {"operation": "scrna_sccoda", "parameters": {}}
_SOURCES = {
    "composition_input": ("test-composition", None),
    "composition_spec": ("test-composition-spec", None),
}


def _action(executor):
    return executor.register_single_cell_step(
        _REGISTRY,
        step_id="fit-composition",
        branch_id="composition",
        biological_unit="donor",
        sources=_SOURCES,
    )


def test_sccoda_requires_its_own_pinned_runtime(workspace_tmp_path):
    from devharness_controlled.native_contracts import SCRNA_KERNEL_IMAGES

    workflow = make(workspace_tmp_path)
    executor = TierAExecutor(
        workflow.ledger,
        mounted_image_id=SCRNA_KERNEL_IMAGES.get("linux/arm64"),
    )
    with pytest.raises(PermissionError, match="pinned scCODA runtime"):
        _action(executor)


def test_sccoda_workflow_retains_a_bayesian_result(workspace_tmp_path):
    from devbench.core.scrna_composition import CompositionSpec
    from devharness_controlled.native_contracts import sccoda_kernel_images

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in sccoda_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to a pinned scCODA image")
    workflow = make(workspace_tmp_path)
    executor = TierAExecutor(
        workflow.ledger,
        allow_data_execution=True,
        mounted_image_id=image,
    )
    data = _planted_input()
    spec = CompositionSpec(
        reference_cell_type=data.cell_types[0],
        baseline_condition=data.samples[0].condition,
        target_est_fdr=0.05,
        seed=1989,
        num_warmup=700,
        num_samples=450,
        target_accept_probability=0.97,
    )
    documents = (
        ("test-composition", "scrna_composition_input_json", data),
        ("test-composition-spec", "scrna_composition_spec_json", spec),
    )
    for version_id, representation, model in documents:
        workflow.register_artifact(
            ArtifactVersion(
                version_id=version_id,
                sha256=executor.training.put(
                    canonical_json(model.model_dump(mode="json"))
                ),
                kind="table",
                representation=representation,
                biological_unit="donor",
                unit_membership_sha256=digest("composition-ref-design"),
            )
        )
    actions = WorkflowActions(
        None,
        workflow.ledger,
        actor="operator",
        selector=None,
        tier_a_executor=executor,
        allow_data_execution=True,
    )
    workflow.insert(_bind_composition_aim(workflow, executor, data))
    result = actions.perform_adaptive(workflow, "fit-composition")
    assert result["status"] == "succeeded", result
    artifact = ArtifactVersion.model_validate(result["outputs"]["composition_result"])
    with executor.open_output(artifact) as stream:
        report = json.load(stream)
    assert report["status"] == "succeeded"
    assert report["reference_cell_type"] == spec.reference_cell_type
    assert report["inference_scope"] == "bayesian_relative_cell_type_composition"
    assert any(row["credible_effect"] for row in report["effects"])
    assert 0 <= report["est_fdr"] <= spec.target_est_fdr
    assert "gene_statistics" not in report


def _bind_composition_aim(workflow, executor, data):
    from collections import defaultdict

    from devbench.core.rna_answerability import (
        AnswerabilityRequest,
        AttestedMeasurement,
        DesignEvidence,
        RuntimeEvidence,
    )
    from devbench.platform.thesis_aims import ThesisAimLifecycle
    from devbench.platform.thesis_workflow import ThesisWorkflow
    from tests.test_thesis_aims import _aim, _approve

    inputs = workflow._read_projection(lambda db: workflow._state(db).artifacts)
    source = inputs["test-composition"][0]["sha256"]
    aims = ThesisAimLifecycle(
        workflow.ledger,
        workflow.lineage_id,
        authorize_operator=lambda actor: actor == "researcher",
    )
    aims.propose(
        _aim(
            "composition",
            question="Does relative cell composition change by condition?",
            estimand="Donor-level differences in relative cell-type composition",
            input_sha256s=[source],
        )
    )
    _approve(aims, "composition")
    axis = data.biological_unit_mapping_sha256
    donors = defaultdict(set)
    for sample in data.samples:
        donors[sample.condition].add(sample.biological_unit_id)
    measurements = (
        AttestedMeasurement(
            kind="cell_type_counts",
            artifact_sha256=source,
            receipt_sha256=digest("fixture composition input receipt"),
            observation_axis_sha256=axis,
        ),
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=digest("fixture design preflight"),
        observation_axis_sha256=axis,
        estimability="estimable",
        independent_units_per_group=tuple(len(group) for group in donors.values()),
    )
    runtime = RuntimeEvidence(
        question="compositional_abundance",
        tier="tier_a",
        operation_id="scrna_sccoda",
        runtime_sha256=executor.environment_sha256,
    )
    evidence = (*measurements, design, runtime)
    lifecycle = ThesisWorkflow(
        workflow.ledger,
        workflow.lineage_id,
        verify_attestation=lambda _db, item: item in evidence,
    )
    step, decision = lifecycle.bind_step(
        _action(executor),
        aim_id="composition",
        request=AnswerabilityRequest(
            family_id=3,
            question="compositional_abundance",
            assay="single_nucleus_rna_seq",
            entry_point="annotated_object",
        ),
        measurements=measurements,
        design=design,
        runtime=runtime,
        backend_id=executor.backend_id,
    )
    assert decision.status == "ready" and decision.execution_route == "tier_a"
    return step
