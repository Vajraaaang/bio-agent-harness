"""Exercise donor trajectories through real mounted workflow receipts."""

import json
import os
from pathlib import Path
from typing import Literal

import pytest

from devbench.core.rna_answerability import (
    AnswerabilityRequest,
    AttestedMeasurement,
    DesignEvidence,
    RuntimeEvidence,
)
from devbench.core.scrna_donor_trajectory import fit_donor_trajectory
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.platform.adaptive_workflow import ArtifactVersion
from devbench.platform.thesis_aims import ThesisAimLifecycle
from devbench.platform.thesis_workflow import ThesisWorkflow
from devbench.platform.workflow_actions import WorkflowActions
from rna_kernels.canonical import canonical_json
from rna_kernels.mounted_scrna import run_mounted_scrna
from tests.test_investigation_invalidation import digest, make
from tests.test_mounted_scrna import _mounts
from tests.test_p3_donor_trajectory import _cohort
from tests.test_thesis_aims import _aim, _approve

_OPERATION = "fit_donor_trajectory"
_REPRESENTATIONS = {
    "trajectory_expression": ("continuous_matrix", "scrna_prepared_h5ad"),
    "trajectory_coordinates": ("table", "scrna_trajectory_coordinates_csv"),
    "trajectory_spec": ("table", "scrna_trajectory_spec_json"),
}
_REFUSALS = [
    ({"method": "tradeSeq"}, "cell_level_tradeSeq_refused"),
    ({"intent": "confirmatory"}, "trajectory_inference_authorization_required"),
    ({"design": "independent"}, "repeated_donors_require_paired_design"),
]


def _sources(
    root: Path, changes: dict[str, str], *, donor_count: int = 8,
) -> tuple[dict[str, Path], dict]:
    expression, coordinates, original, _, _ = _cohort(
        root, paired=True, donor_count=donor_count,
    )
    spec = original.model_copy(update={"intent": "exploratory", **changes})
    spec_path = root / "trajectory.json"
    spec_path.write_bytes(canonical_json(spec.model_dump(mode="json")))
    sources = {
        "trajectory_expression": expression,
        "trajectory_coordinates": coordinates,
        "trajectory_spec": spec_path,
    }
    expected = (
        fit_donor_trajectory(expression, coordinates, spec) if not changes else {}
    )
    return sources, expected


def test_mounted_donor_trajectory_matches_existing_donor_fit(workspace_tmp_path):
    sources, expected = _sources(workspace_tmp_path / "dataset", {})
    inputs, outputs, request = _mounts(
        workspace_tmp_path / "mount", _OPERATION, sources
    )
    summary = run_mounted_scrna(inputs, outputs, request)
    assert summary["status"] == "completed"
    assert summary["output_sha256"].keys() == {"trajectory_result.json"}
    result = json.loads((outputs / "trajectory_result.json").read_bytes())
    assert result["upstream_receipt_status"] == "supplied_only"
    assert result["verified_mounted_inputs"] == json.loads(
        (inputs / "request.json").read_bytes()
    )["inputs"]
    assert {key: result[key] for key in expected} == expected
    assert result["n_donors"] == 8
    assert result["support"]["knot_count"] == 2
    assert result["claim_scope"] == "exploratory_only"
    assert result["clinical_release_allowed"] is False
    assert all(row["p"] is row["q"] is None for row in result["gene_statistics"])


@pytest.mark.parametrize(("changes", "reason"), _REFUSALS)
def test_mounted_donor_trajectory_retains_precise_refusal(
    workspace_tmp_path, changes, reason
):
    sources, _ = _sources(workspace_tmp_path / "dataset", changes)
    inputs, outputs, request = _mounts(
        workspace_tmp_path / "mount", _OPERATION, sources
    )
    summary = run_mounted_scrna(inputs, outputs, request)
    assert summary["status"] == "refused"
    assert summary["reason"] == reason
    assert summary["output_sha256"] == {}
    assert {path.name for path in outputs.iterdir()} == {"summary.json"}


def test_mounted_donor_trajectory_reports_insufficient_complete_pairs(
    workspace_tmp_path,
):
    sources, _ = _sources(
        workspace_tmp_path / "dataset", {"axis_selection": "unknown"}, donor_count=4,
    )
    inputs, outputs, request = _mounts(
        workspace_tmp_path / "mount", _OPERATION, sources,
    )
    summary = run_mounted_scrna(inputs, outputs, request)
    assert summary["status"] == "refused"
    assert summary["reason"] == "trajectory_needs_more_complete_pairs_for_frozen_spline"
    assert summary["output_sha256"] == {}


def _bind_aim(
    workflow, executor, step, pins, *, trajectory_evidence=None, verify_review=True,
):
    aims = ThesisAimLifecycle(
        workflow.ledger,
        workflow.lineage_id,
        authorize_operator=lambda actor: actor == "researcher",
    )
    proposed = {}
    if trajectory_evidence is not None:
        proposed = {
            "kind": "confirmatory",
            "evaluation_sha256s": [pins["trajectory_expression"]],
            "multiplicity": {
                "family_id": "trajectory-testing",
                "aim_ids": ["trajectory"],
                "hypotheses_sha256": trajectory_evidence.hypotheses_sha256(),
                "fdr": 0.05,
            },
        }
    aims.propose(_aim(
        "trajectory",
        question="Does expression change differently along a shared pseudotime axis?",
        estimand="Difference between paired donor curves along the shared axis",
        input_sha256s=tuple(pins.values()),
        **proposed,
    ))
    _approve(aims, "trajectory")
    axis = digest("fixture observation axis")
    measurements = tuple(
        AttestedMeasurement(
            kind=kind,
            artifact_sha256=pins[port],
            receipt_sha256="2" * 64 if kind == "normalized_expression" else "3" * 64,
            observation_axis_sha256=axis,
            feature_axis_sha256=(
                trajectory_evidence.feature_axis_sha256
                if trajectory_evidence and kind == "normalized_expression"
                else None
            ),
        )
        for kind, port in _TRAJECTORY_MEASUREMENTS
    )
    design = DesignEvidence(
        biological_unit="donor",
        receipt_sha256=digest("fixture paired real donor roster"),
        observation_axis_sha256=axis,
        estimability="estimable",
        independent_units_per_group=(8, 8),
    )
    runtime = RuntimeEvidence(
        question="donor_trajectory",
        tier="tier_a",
        operation_id=_OPERATION,
        runtime_sha256=executor.environment_sha256,
    )
    evidence = (*measurements, design, runtime)
    if verify_review and trajectory_evidence is not None:
        evidence = (*evidence, trajectory_evidence)
    lifecycle = ThesisWorkflow(
        workflow.ledger,
        workflow.lineage_id,
        verify_attestation=lambda _db, item: item in evidence,
    )
    scoped, answer = lifecycle.bind_step(
        step,
        aim_id="trajectory",
        request=AnswerabilityRequest(
            family_id=7,
            question="donor_trajectory",
            assay="single_nucleus_rna_seq",
            entry_point="annotated_object",
        ),
        measurements=measurements,
        design=design,
        runtime=runtime,
        trajectory_evidence=trajectory_evidence,
        backend_id=executor.backend_id,
    )
    assert answer.status == "ready" and answer.execution_route == "tier_a"
    return scoped


_TRAJECTORY_MEASUREMENTS: tuple[
    tuple[Literal["normalized_expression", "inferred_pseudotime"], str], ...
] = (
    ("normalized_expression", "trajectory_expression"),
    ("inferred_pseudotime", "trajectory_coordinates"),
)


@pytest.mark.wal
@pytest.mark.parametrize(
    ("changes", "reason"),
    [({}, None), *_REFUSALS],
    ids=("paired-donors", "cell-level-trade-seq", "unverified-axis", "repeated-donors"),
)
def test_curated_workflow_dispatches_donor_trajectory(
    workspace_tmp_path, changes, reason
):
    from devharness_controlled.native_contracts import trajectory_kernel_images

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
    if image not in trajectory_kernel_images():
        pytest.skip("Select a pinned donor trajectory kernel image")

    source_dir = workspace_tmp_path / "dataset"
    sources, _ = _sources(source_dir, changes)
    workflow, executor, step, expected_pins = _trajectory_step(
        workspace_tmp_path, image, sources
    )
    workflow.insert(_bind_aim(workflow, executor, step, expected_pins))
    receipt = _dispatch_trajectory(workflow, executor)
    assert {pin["port"]: pin["sha256"] for pin in receipt["consumed"]} == expected_pins
    if reason is not None:
        assert receipt["status"] == "failed"
        assert receipt["reason"] == reason
        assert receipt["outputs"] == {}
        return
    result = _published_trajectory(executor, receipt)
    assert result["n_donors"] == 8
    assert result["claim_scope"] == "exploratory_only"
    assert all(row["p"] is row["q"] is None for row in result["gene_statistics"])


def _trajectory_step(root, image, sources):
    workflow = make(root)
    executor = TierAExecutor(
        workflow.ledger, allow_data_execution=True, mounted_image_id=image
    )
    links = {}
    expected_pins = {}
    for port, (kind, representation) in _REPRESENTATIONS.items():
        artifact = ArtifactVersion(
            version_id=port,
            sha256=executor.training.put_file(sources[port]),
            kind=kind,
            representation=representation,
            biological_unit="donor",
            unit_membership_sha256=digest("trajectory test donors"),
        )
        workflow.register_artifact(artifact)
        links[port] = (artifact.version_id, None)
        expected_pins[port] = artifact.sha256
    step = executor.register_single_cell_step(
        {"operation": _OPERATION, "parameters": {}},
        step_id="fit-trajectory",
        branch_id="trajectory",
        biological_unit="donor",
        sources=links,
    )
    return workflow, executor, step, expected_pins


def _dispatch_trajectory(workflow, executor):
    actions = WorkflowActions(
        None, workflow.ledger, actor="operator", selector=None,
        tier_a_executor=executor, allow_data_execution=True,
    )
    return actions.perform_adaptive(workflow, "fit-trajectory")


def _published_trajectory(executor, receipt):
    assert receipt["status"] == "succeeded", receipt
    artifact = ArtifactVersion.model_validate(receipt["outputs"]["trajectory_result"])
    assert artifact.permitted_uses == ("checked_observation", "exploratory")
    with executor.open_output(artifact) as stream:
        return json.load(stream)


def _candidate_evidence(sources):
    from devbench.core._hashing import canonical_sha256
    from devbench.core.scrna_donor_trajectory import DonorTrajectorySpec
    from devbench.core.trajectory_evidence import DonorTrajectoryEvidence

    data = sources["trajectory_spec"].read_bytes()
    model = DonorTrajectorySpec.model_validate_json(data)
    return DonorTrajectoryEvidence(
        specification_sha256=canonical_sha256(model.model_dump(mode="json")),
        normalized_expression_sha256=model.expression_sha256,
        coordinates_sha256=model.coordinates_sha256,
        original_counts_sha256=model.original_counts_sha256,
        normalization_receipt_sha256=model.normalization_receipt_sha256,
        axis_receipt_sha256=model.axis_receipt_sha256,
        design_receipt_sha256=digest("fixture paired real donor roster"),
        feature_axis_sha256=canonical_sha256(["g_signal", "g_null", "g_constant"]),
        independence_basis="orthogonal_assay",
        independence_receipt_sha256=digest("fixture tracked independent measurement"),
        review_receipt_sha256=digest("fixture review receipt"),
        reviewer="methods-reviewer",
        rationale="Verified paired donors, independent ordering and normalization.",
    )


def _reviewed_mount(root, *, wrong_features=False):
    from hashlib import sha256

    sources, _ = _sources(root / "dataset", {"intent": "confirmatory"})
    evidence = _candidate_evidence(sources)
    if wrong_features:
        evidence = evidence.model_copy(update={"feature_axis_sha256": digest("other")})
    inputs, outputs, _ = _mounts(root / "mount", _OPERATION, sources)
    request_path = inputs / "request.json"
    request = json.loads(request_path.read_bytes())
    request["trajectory_evidence"] = evidence.model_dump(mode="json")
    payload = canonical_json(request)
    request_path.write_bytes(payload)
    return inputs, outputs, sha256(payload).hexdigest(), evidence


@pytest.mark.parametrize("wrong_features", (False, True))
def test_mounted_reviewed_trajectory_pins_the_tested_feature_family(
    workspace_tmp_path, wrong_features,
):
    inputs, outputs, request, evidence = _reviewed_mount(
        workspace_tmp_path, wrong_features=wrong_features
    )
    summary = run_mounted_scrna(inputs, outputs, request)
    if wrong_features:
        assert summary["status"] == "refused"
        assert summary["reason"] == "trajectory_feature_axis_changed"
        assert summary["output_sha256"] == {}
        return
    assert summary["status"] == "completed"
    result = json.loads((outputs / "trajectory_result.json").read_bytes())
    assert result["claim_scope"] == "candidate_research_inference"
    assert result["clinical_release_allowed"] is False
    assert result["trajectory_evidence_sha256"] == evidence.sha256()
    assert result["bh_family_size"] == 3
    fit = result["gene_statistics"][0]
    assert 0 <= fit["p"] <= fit["q"] <= 1
    assert result["gene_statistics"][2]["q"] is None


@pytest.mark.wal
@pytest.mark.parametrize(
    ("verify_review", "new_receipt", "reason"),
    [
        (False, None, "trajectory_evidence_unverified"),
        (True, "4" * 64, "trajectory_evidence_receipts_changed"),
    ],
)
def test_trajectory_workflow_blocks_unverified_or_changed_review(
    workspace_tmp_path, verify_review, new_receipt, reason,
):
    from devbench.engine.workflow_operations import OperationBlocked
    from devharness_controlled.native_contracts import TRAJECTORY_KERNEL_IMAGES

    sources, _ = _sources(workspace_tmp_path / "dataset", {"intent": "confirmatory"})
    evidence = _candidate_evidence(sources)
    if new_receipt:
        evidence = evidence.model_copy(
            update={"normalization_receipt_sha256": new_receipt}
        )
    flow, executor, step, pins = _trajectory_step(
        workspace_tmp_path, TRAJECTORY_KERNEL_IMAGES["linux/arm64"], sources
    )
    with pytest.raises(OperationBlocked, match=reason):
        _bind_aim(
            flow, executor, step, pins,
            trajectory_evidence=evidence, verify_review=verify_review,
        )
    assert flow.history("fit-trajectory") == ()


@pytest.mark.wal
def test_curated_candidate_trajectory_requires_and_retains_verified_review(
    workspace_tmp_path,
):
    from hashlib import sha256

    from devbench.analysis.thesis_replay import replay_thesis_package
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )
    from devbench.engine.workflow_operations import OperationBlocked
    from devharness_controlled.native_contracts import reviewed_trajectory_kernel_images

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
    if image not in reviewed_trajectory_kernel_images():
        pytest.skip("Select a pinned reviewed donor trajectory kernel image")
    sources, _ = _sources(workspace_tmp_path / "dataset", {"intent": "confirmatory"})
    evidence = _candidate_evidence(sources)
    flow, executor, step, pins = _trajectory_step(workspace_tmp_path, image, sources)
    flow.insert(_bind_aim(flow, executor, step, pins, trajectory_evidence=evidence))
    retained = ThesisWorkflow(flow.ledger, flow.lineage_id)
    summary = retained.scientific_context()
    assert summary["raw_matrix_rows"] == 0
    assert summary["answerability"][0]["trajectory_review"] == {
        "evidence_sha256": evidence.sha256(),
        "review_receipt_sha256": evidence.review_receipt_sha256,
        "reviewer": evidence.reviewer,
    }

    receipt = _dispatch_trajectory(flow, executor)
    assert {pin["port"]: pin["sha256"] for pin in receipt["consumed"]} == pins
    result = _published_trajectory(executor, receipt)
    assert result["claim_scope"] == "candidate_research_inference"
    assert result["trajectory_evidence_sha256"] == evidence.sha256()
    fit = result["gene_statistics"][0]
    assert 0 <= fit["p"] <= fit["q"] <= 1
    with pytest.raises(OperationBlocked, match="evidence_artifact_unadmitted"):
        retained.aims.record_outcome(
            "trajectory",
            version=1,
            status="supported",
            rationale="The candidate spline produced an effect.",
            uncertainty="Independent review of the resulting inference is pending.",
            evidence=[{
                "step_id": "fit-trajectory",
                "receipt_sha256": receipt["receipt_sha256"],
                "artifact_sha256": receipt["outputs"]["trajectory_result"]["sha256"],
            }],
        )

    schema = workspace_tmp_path / "answer-schema.json"
    schema.write_bytes(canonical_json({
        "type": "object",
        "properties": {"n_donors": {"type": "integer"}},
        "required": ["n_donors"],
    }))
    spec = ThesisSynthesisSpec.model_validate({
        "question": "How many paired donors support the candidate trajectory?",
        "outcome": "partial",
        "expected_revision": flow.status()["revision"],
        "answer_schema_sha256": sha256(schema.read_bytes()).hexdigest(),
        "fields": {
            "n_donors": {
                "step_id": step.step_id,
                "port": "trajectory_result",
                "path": ["n_donors"],
            },
        },
    })
    package = workspace_tmp_path / "trajectory-thesis"
    release = write_thesis_package(
        package, flow, spec, answer_schema=schema, open_artifact=executor.open_output,
        aim_lifecycle=retained.aims, tier_a_executor=executor,
    )
    answer = json.loads((package / "final_discovery.json").read_bytes())
    assert answer == {"n_donors": 8}
    replayed = replay_thesis_package(
        package, release["root_sha256"], output=workspace_tmp_path / "replay",
        input_root=executor.training.root, allow_data_execution=True,
    )
    digest = receipt["outputs"]["trajectory_result"]["sha256"]
    output = replayed["single_cell_steps"][step.step_id]
    assert output["trajectory_result.json"] == digest


def test_thesis_synthesis_refuses_candidate_trajectory_probability_alias():
    from contextlib import contextmanager
    from hashlib import sha256
    from io import BytesIO

    from devbench.analysis.thesis_synthesis_evidence import (
        AnswerFieldRef,
        VerifiedField,
        extract_field,
    )

    raw = canonical_json({
        "claim_scope": "candidate_research_inference",
        "n_donors": 8,
        "gene_statistics": [{"gene_id": "g1", "p": 0.02, "q": 0.04}],
    })
    artifact = ArtifactVersion(
        version_id="trajectory-output",
        sha256=sha256(raw).hexdigest(),
        kind="table",
        representation="scrna_donor_trajectory_result_json",
        biological_unit="donor",
        unit_membership_sha256=digest("fixture roster"),
        permitted_uses=("checked_observation", "exploratory"),
    )
    binding = VerifiedField(
        branch_id="trajectory",
        generation=1,
        receipt_sha256=digest("checked receipt"),
        attempt_id="trajectory-attempt",
        step_id="fit-trajectory",
        artifact=artifact,
        claim_scope="checked_observation",
    )

    @contextmanager
    def open_artifact(_artifact):
        with BytesIO(raw) as handle:
            yield handle

    notes = AnswerFieldRef(
        step_id="fit-trajectory", port="trajectory_result", path=("n_donors",)
    )
    assert extract_field(notes, binding, open_artifact) == 8
    alias = notes.model_copy(update={"path": ("gene_statistics", 0, "q")})
    with pytest.raises(PermissionError, match="require current research admission"):
        extract_field(alias, binding, open_artifact)
