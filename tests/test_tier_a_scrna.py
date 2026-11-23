"""Single-cell Tier A admission, handoff and real mounted execution."""

import json
import os

import numpy as np
import pandas as pd
import pytest
from anndata import AnnData
from scipy.sparse import csr_matrix

from devbench.engine.tier_a_executor import TierAExecutor
from devbench.platform.adaptive_workflow import ArtifactVersion
from devbench.platform.workflow_actions import WorkflowActions
from tests.test_investigation_invalidation import digest, make

pytestmark = pytest.mark.wal

_IMAGE = "sha256:" + "1" * 64
_QC = {
    "operation": "scrna_batch_qc",
    "parameters": {"batch_key": "batch", "qc_group_key": "batch", "min_genes": 1},
}


def _source(root):
    rng = np.random.default_rng(73)
    cells_per_lane, genes = 160, 400
    means = rng.uniform(0.5, 4, (cells_per_lane, genes))
    midpoint = cells_per_lane // 2
    means[:midpoint, :80] *= 5
    means[midpoint:, 80:160] *= 5
    values = np.vstack((rng.poisson(means), rng.poisson(means * 1.25)))
    expression = AnnData(
        X=csr_matrix(np.log1p(values)),
        obs=pd.DataFrame(
            {
                "batch": np.repeat(["A", "B"], cells_per_lane),
                "capture": np.repeat(["lane-A", "lane-B"], cells_per_lane),
                "donor": [f"donor-{index % 8}" for index in range(len(values))],
                "specimen": [f"specimen-{index % 8}" for index in range(len(values))],
                "condition": "fixed",
            },
            index=[f"cell-{i}" for i in range(len(values))],
        ),
        var=pd.DataFrame(index=["MT-ND1", *(f"gene-{i}" for i in range(genes - 1))]),
        layers={"counts": csr_matrix(values)},
    )
    path = root / "expression.h5ad"
    expression.write_h5ad(path)
    return path


def _workflow(root, image, *, history=("raw_counts",)):
    flow = make(root)
    executor = TierAExecutor(
        flow.ledger, allow_data_execution=True, mounted_image_id=image
    )
    source = _source(root)
    artifact = ArtifactVersion(
        version_id="count-source",
        sha256=executor.training.put_file(source),
        kind="counts",
        representation="raw_counts_h5ad",
        biological_unit="donor",
        unit_membership_sha256=digest("declared-donor-membership"),
        measurement_history=history,
    )
    flow.register_artifact(artifact)
    actions = WorkflowActions(
        None,
        flow.ledger,
        actor="operator",
        selector=None,
        tier_a_executor=executor,
        allow_data_execution=True,
    )
    return flow, executor, actions


def _qc_step(executor):
    return executor.register_single_cell_step(
        _QC,
        step_id="qc-lanes",
        branch_id="single-cell",
        biological_unit="donor",
        sources={"expression": ("count-source", None)},
    )


def test_single_cell_step_rejects_unlocked_runtime(workspace_tmp_path):
    _, executor, _ = _workflow(workspace_tmp_path, _IMAGE)
    with pytest.raises(PermissionError, match="pinned single-cell runtime"):
        _qc_step(executor)


def test_single_cell_dispatch_refuses_corrected_count_history(
    workspace_tmp_path,
    monkeypatch,
):
    from devharness_controlled import native_contracts

    monkeypatch.setitem(native_contracts.SCRNA_KERNEL_IMAGES, "linux/arm64", _IMAGE)
    flow, executor, actions = _workflow(
        workspace_tmp_path,
        _IMAGE,
        history=("ambient_corrected",),
    )
    flow.insert(_qc_step(executor))
    assert (
        flow.status()["blocked"]["qc-lanes"] == "adaptive_COUNT-1_measurement_history"
    )
    assert not flow.authority.attempts(flow.lineage_id)


def test_single_cell_mounted_qc_and_replayed_receipt(workspace_tmp_path):
    from devharness_controlled.native_contracts import single_cell_kernel_images

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in single_cell_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to a pinned single-cell image")
    flow, executor, actions = _workflow(workspace_tmp_path, image)
    step = _qc_step(executor)
    flow.insert(step)
    result = actions.perform_adaptive(flow, step.step_id)
    assert result["status"] == "succeeded"
    assert set(result["outputs"]) == {"qc_metrics", "qc_features", "qc_report"}
    assert all(
        item["permitted_uses"] == ["checked_observation", "exploratory"]
        for item in result["outputs"].values()
    )
    artifact = ArtifactVersion.model_validate(result["outputs"]["qc_report"])
    retained = TierAExecutor(flow.ledger, mounted_image_id=image)
    with retained.open_output(artifact) as handle:
        report = handle.read(65_537)
    assert json.loads(report)["thresholds"]

    doublets = _run_stage(
        flow,
        executor,
        actions,
        "doublets",
        "scrna_lane_doublets",
        {"batch_key": "batch", "capture_key": "capture"},
        {
            "expression": ("count-source", None),
            "qc_metrics": ("qc-lanes", "qc_metrics"),
        },
    )
    assert set(doublets["outputs"]) == {"predictions", "doublet_report"}
    selected = _run_stage(
        flow,
        executor,
        actions,
        "prepare",
        "scrna_prepare",
        {"batch_key": "batch", "n_top_genes": 24, "normalization": "shifted_log"},
        {
            "expression": ("count-source", None),
            "qc_metrics": ("qc-lanes", "qc_metrics"),
            "qc_features": ("qc-lanes", "qc_features"),
        },
    )
    assert set(selected["outputs"]) == {"selected", "hvg"}
    integrated = _run_stage(
        flow,
        executor,
        actions,
        "check-pca",
        "scrna_integrate",
        {"batch_key": "batch", "correction": "none", "n_pcs": 12},
        {"selected": ("prepare", "selected")},
    )
    assert set(integrated["outputs"]) == {
        "embedding",
        "figure",
        "graph",
        "integration_report",
    }


def test_raw_droplet_action_requires_current_review_basis(
    workspace_tmp_path, monkeypatch
):
    from devharness_controlled import native_contracts

    monkeypatch.setitem(native_contracts.SCRNA_KERNEL_IMAGES, "linux/arm64", _IMAGE)
    flow, executor, actions = _workflow(workspace_tmp_path, _IMAGE)
    sources = {
        "droplets": ("droplet-source", "droplet_matrix", "raw_cellranger_v3_h5"),
        "droplet_provenance": ("droplet-proof", "table", "raw_droplet_provenance_json"),
    }
    for version_id, kind, representation in sources.values():
        flow.register_artifact(
            ArtifactVersion(
                version_id=version_id,
                kind=kind,
                representation=representation,
                sha256=executor.training.put(version_id.encode()),
                biological_unit="donor",
                unit_membership_sha256=digest("declared-donor-membership"),
            )
        )
    step = executor.register_single_cell_step(
        {"operation": "scrna_droplets", "parameters": {"method": "emptydrops"}},
        step_id="call-cells",
        branch_id="single-cell",
        biological_unit="donor",
        sources={port: (value[0], None) for port, value in sources.items()},
    )
    flow.insert(step)
    with pytest.raises(PermissionError, match="current review basis"):
        actions.perform_adaptive(flow, step.step_id)
    assert not flow.authority.attempts(flow.lineage_id)


def _run_stage(flow, executor, actions, step_id, operation, parameters, sources):
    plan = executor.register_single_cell_step(
        {"operation": operation, "parameters": parameters},
        step_id=step_id,
        branch_id="single-cell",
        biological_unit="donor",
        sources=sources,
    )
    flow.insert(plan)
    result = actions.perform_adaptive(flow, plan.step_id)
    assert result["status"] == "succeeded", _failure_evidence(executor, result)
    return result


def test_nb_countsplit_tier_a_requires_review_and_seals_inference(workspace_tmp_path):
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.adaptive_workflow_state import BasisUse
    from devharness_controlled.native_contracts import nb_fit_kernel_images
    from tests.test_mounted_scrna import _split_dispersion

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in nb_fit_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to the pinned NB fit image")
    flow, executor, actions = _workflow(workspace_tmp_path, image)
    path = _split_dispersion(
        workspace_tmp_path / "expression.h5ad",
        workspace_tmp_path / "theta.json",
    )
    dispersion = ArtifactVersion(
        version_id="split-dispersion",
        sha256=executor.training.put_file(path),
        kind="table",
        representation="nb_countsplit_dispersion_json",
        biological_unit="donor",
        unit_membership_sha256=digest("declared-donor-membership"),
    )
    flow.register_artifact(dispersion)
    spec = {
        "operation": "scrna_nb_countsplit",
        "parameters": {
            "clustering_fraction": 0.4,
            "seed": 17,
            "marker_cohort_columns": {
                "donor_key": "donor",
                "specimen_key": "specimen",
                "condition_key": "condition",
            },
        },
    }
    sources = {
        "expression": ("count-source", None),
        "split_dispersion": (dispersion.version_id, None),
    }
    unreviewed = executor.register_single_cell_step(
        spec,
        step_id="unreviewed",
        branch_id="split",
        biological_unit="donor",
        sources=sources,
    )
    flow.insert(unreviewed)
    with pytest.raises(PermissionError, match="current review basis"):
        actions.perform_adaptive(flow, "unreviewed")
    assert not flow.authority.attempts(flow.lineage_id)

    flow.record_basis("reviewed-dispersion", "review_basis", dispersion.sha256)
    step = executor.register_single_cell_step(
        spec,
        step_id="reviewed",
        branch_id="split",
        biological_unit="donor",
        sources=sources,
        basis=(BasisUse(basis_id="reviewed-dispersion"),),
    )
    flow.insert(step)
    outcome = actions.perform_adaptive(flow, "reviewed")
    assert outcome["status"] == "succeeded", _failure_evidence(executor, outcome)
    assert len(flow.authority.attempts(flow.lineage_id)) == 1
    sealed = ArtifactVersion.model_validate(outcome["outputs"]["inference_counts"])
    assert sealed.permitted_uses == ()
    with pytest.raises(OperationBlocked, match="current_use_unavailable"):
        flow.current_use("reviewed", "inference_counts", purpose="query")
    with pytest.raises(PermissionError, match="remains sealed"):
        executor.load_output(sealed)
    attempt = outcome["attempt_id"]
    envelope = flow._read_projection(lambda db: flow._state(db).dispatches[attempt])
    assert executor.load_receipt(envelope["envelope"], attempt_id=attempt)

    cluster = _run_stage(
        flow,
        executor,
        actions,
        "cluster-reviewed-fold",
        "scrna_nb_fold_cluster",
        {"seed": 72, "n_top_genes": 64, "n_pcs": 12, "resolution": 1.2},
        {
            "clustering_counts": ("reviewed", "clustering_counts"),
            "split_report": ("reviewed", "split_report"),
        },
    )
    labels = ArtifactVersion.model_validate(cluster["outputs"]["cluster_labels"])
    assert labels.label_origin == "rna_derived"
    produced = json.loads(executor.load_output(labels))
    assert len(produced["labels"]) == 320
    assert len({item["cluster_id"] for item in produced["labels"]}) >= 2
    assert (
        produced["clustering_counts_sha256"]
        == (outcome["outputs"]["clustering_counts"]["sha256"])
    )
    assert (
        produced["split_report_sha256"] == outcome["outputs"]["split_report"]["sha256"]
    )
    assert produced["inference_performed"] is False
    report = ArtifactVersion.model_validate(cluster["outputs"]["clustering_report"])
    checked = json.loads(executor.load_output(report))
    assert (
        checked["graph_assessment_sha256"]
        == (cluster["outputs"]["graph_assessment"]["sha256"])
    )
    from devbench.analysis.thesis_single_cell_replay import capture_single_cell_replay

    replay_files, replay_steps, omitted = capture_single_cell_replay(
        flow, executor, flow.status()["revision"],
    )
    assert [recipe["step_id"] for recipe in replay_steps] == ["cluster-reviewed-fold"]
    assert omitted == {"reviewed": "sealed_artifact"}
    assert all(
        sealed.sha256.encode() not in payload for payload in replay_files.values()
    )

    numerator, denominator = _synthetic_marker_contrast(produced["labels"])
    aggregate = _run_stage(
        flow,
        executor,
        actions,
        "aggregate-held-out-fold",
        "scrna_nb_fold_pseudobulk",
        {"condition": "fixed", "numerator": numerator, "denominator": denominator},
        {
            "inference_counts": ("reviewed", "inference_counts"),
            "donor_roster": ("reviewed", "donor_roster"),
            "split_report": ("reviewed", "split_report"),
            "cluster_labels": ("cluster-reviewed-fold", "cluster_labels"),
        },
    )
    report = ArtifactVersion.model_validate(aggregate["outputs"]["aggregation_report"])
    aggregation = json.loads(executor.load_output(report))
    assert aggregation["paired_donors"] == 8
    assert aggregation["observed_profiles"] == 16
    assert aggregation["missing_profiles"] == 0
    assert aggregation["design_status"] == "ready"
    assert aggregation["low_cell_profiles"] > 0
    assert aggregation["exclusions_applied_to_low_cell_profiles"] is False
    assert aggregation["marker_inference_performed"] is False
    for port in ("pseudobulk_counts", "pseudobulk_metadata"):
        private = ArtifactVersion.model_validate(aggregate["outputs"][port])
        with pytest.raises(PermissionError, match="remains sealed"):
            executor.load_output(private)

    _check_sealed_marker_fit(
        flow,
        executor,
        actions,
        report.sha256,
        numerator,
        denominator,
    )

    alternate = executor.register_single_cell_step(
        {**spec, "parameters": {"clustering_fraction": 0.4, "seed": 701}},
        step_id="different-fold",
        branch_id="split",
        biological_unit="donor",
        sources=sources,
        basis=(BasisUse(basis_id="reviewed-dispersion"),),
    )
    flow.insert(alternate)
    assert actions.perform_adaptive(flow, alternate.step_id)["status"] == "succeeded"
    mismatched = executor.register_single_cell_step(
        {"operation": "scrna_nb_fold_cluster", "parameters": {"seed": 72}},
        step_id="mismatched-fold-report",
        branch_id="split",
        biological_unit="donor",
        sources={
            "clustering_counts": ("reviewed", "clustering_counts"),
            "split_report": (alternate.step_id, "split_report"),
        },
    )
    flow.insert(mismatched)
    with pytest.raises(PermissionError, match="share one split receipt"):
        actions.perform_adaptive(flow, mismatched.step_id)
    different_inference = executor.register_single_cell_step(
        {
            "operation": "scrna_nb_fold_pseudobulk",
            "parameters": {
                "condition": "fixed",
                "numerator": numerator,
                "denominator": denominator,
            },
        },
        step_id="mismatched-inference-fold",
        branch_id="split",
        biological_unit="donor",
        sources={
            "inference_counts": (alternate.step_id, "inference_counts"),
            "donor_roster": ("reviewed", "donor_roster"),
            "split_report": ("reviewed", "split_report"),
            "cluster_labels": ("cluster-reviewed-fold", "cluster_labels"),
        },
    )
    flow.insert(different_inference)
    with pytest.raises(PermissionError, match="share one split receipt"):
        actions.perform_adaptive(flow, different_inference.step_id)
    assert len(flow.authority.attempts(flow.lineage_id)) == 5
    _check_native_marker_confirmation(flow, executor, actions)


def _check_sealed_marker_fit(
    flow, executor, actions, aggregate_sha, numerator, denominator
):
    import pyarrow.parquet as pq

    from devbench.engine.tier_a_scrna import _open_retained_single_cell_output
    from devbench.engine.workflow_operations import OperationBlocked
    from devbench.platform.adaptive_workflow_state import BasisUse
    from rna_kernels.scrna_upstream_contract import marker_fit_basis_sha256

    spec = {
        "operation": "scrna_nb_marker_fit",
        "parameters": {
            "condition": "fixed",
            "numerator": numerator,
            "denominator": denominator,
            "fdr_threshold": 0.05,
        },
    }
    sources = {
        port: ("aggregate-held-out-fold", port)
        for port in ("pseudobulk_counts", "pseudobulk_metadata", "aggregation_report")
    }
    node = executor.register_single_cell_step(
        spec,
        step_id="fit-markers",
        branch_id="split",
        biological_unit="donor",
        sources=sources,
        basis=(BasisUse(basis_id="marker-fit-review"),),
    )
    flow.record_basis("marker-fit-review", "review_basis", digest("unrelated plan"))
    flow.insert(node)
    with pytest.raises(PermissionError, match="reviewed marker fit plan"):
        actions.perform_adaptive(flow, node.step_id)
    assert len(flow.authority.attempts(flow.lineage_id)) == 3

    flow.record_basis(
        "marker-fit-review",
        "review_basis",
        marker_fit_basis_sha256(
            node.parameters_sha256,
            aggregate_sha,
        ),
    )
    result = actions.perform_adaptive(flow, node.step_id)
    assert result["status"] == "succeeded", _failure_evidence(executor, result)
    sealed = ArtifactVersion.model_validate(result["outputs"]["marker_fit"])
    assert sealed.permitted_uses == ()
    with pytest.raises(OperationBlocked, match="current_use_unavailable"):
        flow.current_use(node.step_id, "marker_fit", purpose="query")
    with pytest.raises(PermissionError, match="remains sealed"):
        executor.load_output(sealed)
    with _open_retained_single_cell_output(executor, sealed) as handle:
        fit = pq.ParquetFile(handle)
        scope = json.loads(fit.schema_arrow.metadata[b"devbench.result"])
        probabilities = fit.read(columns=["p_value", "q_value"]).to_pylist()
    assert scope["claim_scope"] == "checked_observation"
    assert len(probabilities) == 400
    assert any(row["p_value"] is not None for row in probabilities)
    for row in probabilities:
        p, q = row["p_value"], row["q_value"]
        assert (p is None and q is None) or 0 <= p <= q <= 1
    report = ArtifactVersion.model_validate(result["outputs"]["marker_fit_report"])
    details = json.loads(executor.load_output(report))
    assert details["solver"] == "edger_v4"
    assert details["test"] == "qlf"
    assert details["marker_scope"] == "awaiting_independent_confirmation"
    assert details["marker_fit_sha256"] == sealed.sha256


def _synthetic_marker_contrast(labels):
    from collections import defaultdict

    # The fixture plants opposing profiles in each 160-cell lane. Choose the
    # contrast from planted memberships and clustering-fold labels only.
    memberships = defaultdict(list)
    for row in labels:
        cell = int(row["cell_id"].removeprefix("cell-"))
        if row["cluster_id"] is not None:
            memberships[row["cluster_id"]].append(cell % 160 < 80)
    fractions = {key: sum(values) / len(values) for key, values in memberships.items()}
    return max(fractions, key=fractions.get), min(fractions, key=fractions.get)


def _check_native_marker_confirmation(flow, executor, actions):
    from devbench.core.rna_pathway_analysis import PathwayPlan, run_ora
    from devbench.engine.nb_marker_confirmation import (
        IndependentMarkerReview,
        NBMarkerConfirmationAuthority,
    )
    from devbench.engine.workflow_operations import OperationBlocked

    authority = NBMarkerConfirmationAuthority(flow, executor, lambda _: None)
    with pytest.raises(
        OperationBlocked, match="independent_marker_confirmation_required"
    ):
        authority.confirm("fit-markers")

    def external_review(subject):
        assert subject.fit_actor == "operator"
        assert subject.paired_donors == 8
        return IndependentMarkerReview(
            subject_sha256=subject.sha256(),
            review_receipt_sha256=digest("fixture independent review receipt"),
            reviewer="fixture-independent-reviewer",
            rationale="Synthetic external review response for exact-subject wiring.",
            checks={
                name: digest("fixture-" + name)
                for name in (
                    "design",
                    "dispersion",
                    "exposure",
                    "calibration",
                )
            },
        )

    self_review = NBMarkerConfirmationAuthority(
        flow,
        executor,
        lambda subject: external_review(subject).model_copy(
            update={"reviewer": subject.fit_actor}
        ),
    )
    with pytest.raises(PermissionError, match="independent exact-fit review"):
        self_review.confirm("fit-markers")

    authority = NBMarkerConfirmationAuthority(flow, executor, external_review)
    result, universe = authority.confirm("fit-markers")
    assert result.marker_scope == "confirmatory_marker_eligible"
    assert len(result.gene_statistics) == 400
    assert universe.selected
    assert authority.verify_marker(result.proof)
    assert authority.verify_universe(universe)
    reopened = NBMarkerConfirmationAuthority(
        type(flow)(flow.ledger, flow.lineage_id), executor, external_review
    )
    assert reopened.verify_marker(result.proof)
    assert reopened.verify_universe(universe)
    withdrawn = NBMarkerConfirmationAuthority(flow, executor, lambda _: None)
    assert not withdrawn.verify_marker(result.proof)
    assert not withdrawn.verify_universe(universe)
    fit_attempt = next(
        attempt["attempt_id"]
        for attempt in flow.history("fit-markers")
        if attempt["status"] == "succeeded"
    )
    receipt = executor.runs / fit_attempt / "receipt.json"
    backup = receipt.with_name("temporary-test-receipt.json")
    receipt.rename(backup)
    try:
        assert not authority.verify_marker(result.proof)
    finally:
        backup.rename(receipt)
    plan = PathwayPlan.model_validate(
        {
            "method": "ora",
            "gene_sets": ({"set_id": "synthetic-set", "genes": universe.selected},),
            "collection_id": "synthetic-empty-context",
            "collection_version": "fixture",
            "min_set_size": 2,
            "max_set_size": 400,
        }
    )
    outcome = run_ora(
        plan,
        universe,
        foreground=universe.selected,
        verify_universe=authority.verify_universe,
        marker_result=result,
        verify_marker=authority.verify_marker,
    )
    assert outcome.status == "completed"
    mounted, retained = _check_mounted_marker_ora(
        flow,
        executor,
        actions,
        plan,
        universe,
        external_review,
    )
    flow.retire_branch("split", reason="scope_change", evidence_sha256=digest("retire"))
    assert not authority.verify_marker(result.proof)
    assert not authority.verify_universe(universe)
    with pytest.raises(OperationBlocked):
        flow.current_use("confirmed-ora", "pathway_result", purpose="query")
    with pytest.raises(OperationBlocked):
        mounted.load_output(retained)


def _check_mounted_marker_ora(flow, executor, actions, plan, universe, accepted_review):
    from devbench.engine.workflow_operations import OperationBlocked

    current_review = True

    def verifier(subject):
        return accepted_review(subject) if current_review else None

    mounted = TierAExecutor(
        flow.ledger,
        allow_data_execution=True,
        mounted_image_id=executor.mounted_image_id,
        marker_review_verifier=verifier,
    )
    step = mounted.register_confirmed_nb_marker_ora(
        flow,
        plan,
        fit_step_id="fit-markers",
        step_id="confirmed-ora",
        branch_id="ora",
    )
    flow.insert(step)
    actions.tier_a_executor = mounted
    current_review = False
    with pytest.raises(OperationBlocked, match="nb_marker_confirmation_unavailable"):
        actions.perform_adaptive(flow, step.step_id)
    current_review = True
    run = actions.perform_adaptive(flow, step.step_id)
    assert run["status"] == "succeeded", _failure_evidence(mounted, run)
    retained = ArtifactVersion.model_validate(run["outputs"]["pathway_result"])
    result = json.loads(mounted.load_output(retained))
    assert result["method"] == "ora"
    assert result["tested_set_count"] == 1
    assert result["tested_gene_count"] == len(universe.genes)
    current_review = False
    with pytest.raises(OperationBlocked, match="nb_marker_confirmation_unavailable"):
        mounted.load_output(retained)
    current_review = True
    return mounted, retained


def _failure_evidence(executor, result):
    io_path = executor._directory(result["attempt_id"]) / "io.json"
    manifest = json.loads(io_path.read_text())
    reports = {}
    for name, content_sha in manifest["tool"].get("retained_evidence", {}).items():
        if name.endswith("report.json"):
            with executor._outputs.open(content_sha) as stream:
                reports[name] = stream.read(3000).decode()
    return result, reports


def test_thesis_replays_single_cell_artifacts_and_reuses_parent_outputs(
    workspace_tmp_path,
):
    from hashlib import sha256

    from devbench.analysis.thesis_replay import replay_thesis_package
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        write_thesis_package,
    )
    from devharness_controlled.native_contracts import single_cell_kernel_images
    from rna_kernels.canonical import canonical_json

    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE", "")
    if image not in single_cell_kernel_images():
        pytest.skip("Select a pinned single-cell image for thesis replay")
    flow, executor, actions = _workflow(workspace_tmp_path, image)
    qc = _qc_step(executor)
    flow.insert(qc)
    checked = actions.perform_adaptive(flow, qc.step_id)
    assert checked["status"] == "succeeded", checked
    prepared = _run_stage(
        flow,
        executor,
        actions,
        "a-prepare",
        "scrna_prepare",
        {"batch_key": "batch", "n_top_genes": 24, "normalization": "shifted_log"},
        {
            "expression": ("count-source", None),
            "qc_metrics": (qc.step_id, "qc_metrics"),
            "qc_features": (qc.step_id, "qc_features"),
        },
    )
    doublets = _run_stage(
        flow, executor, actions, "b-doublets", "scrna_lane_doublets",
        {"batch_key": "batch", "capture_key": "capture"},
        {
            "expression": ("count-source", None),
            "qc_metrics": (qc.step_id, "qc_metrics"),
        },
    )
    integrated = _run_stage(
        flow, executor, actions, "c-graph", "scrna_integrate",
        {"batch_key": "batch", "correction": "none", "n_pcs": 12},
        {"selected": ("a-prepare", "selected")},
    )
    schema = workspace_tmp_path / "answer_schema.json"
    schema.write_bytes(canonical_json({
        "type": "object",
        "properties": {"thresholds": {}},
        "required": ["thresholds"],
    }))
    spec = ThesisSynthesisSpec.model_validate({
        "question": "What per-batch thresholds supported the retained profiles?",
        "outcome": "completed",
        "expected_revision": flow.status()["revision"],
        "answer_schema_sha256": sha256(schema.read_bytes()).hexdigest(),
        "fields": {
            "thresholds": {
                "step_id": qc.step_id, "port": "qc_report", "path": ["thresholds"],
            },
        },
    })
    package = workspace_tmp_path / "thesis"
    seal = write_thesis_package(
        package, flow, spec, answer_schema=schema,
        open_artifact=executor.open_output, tier_a_executor=executor,
    )
    index = json.loads((package / "replay_bundle/index.json").read_bytes())
    assert index["target_comparison"] == "exact_artifact_bytes_same_architecture"
    assert [
        item["step_id"] for item in index["single_cell_steps"]
    ] == ["qc-lanes", "a-prepare", "b-doublets", "c-graph"]
    metrics_digest = checked["outputs"]["qc_metrics"]["sha256"]
    assert not (executor.training.root / metrics_digest).exists()
    replay = replay_thesis_package(
        package,
        seal["root_sha256"],
        output=workspace_tmp_path / "single-cell-replay",
        input_root=executor.training.root,
        allow_data_execution=True,
    )
    assert replay["exact_single_cell_artifacts_match"] is True
    assert replay["single_cell_steps"]["qc-lanes"]["qc_report.json"] == (
        checked["outputs"]["qc_report"]["sha256"]
    )
    assert replay["single_cell_steps"]["a-prepare"]["selected.h5ad"] == (
        prepared["outputs"]["selected"]["sha256"]
    )
    assert replay["single_cell_steps"]["b-doublets"]["predictions.csv"] == (
        doublets["outputs"]["predictions"]["sha256"]
    )
    assert replay["single_cell_steps"]["c-graph"]["embedding.png"] == (
        integrated["outputs"]["figure"]["sha256"]
    )

    bad = workspace_tmp_path / "tampered-inputs"
    bad.mkdir()
    request = index["single_cell_steps"][0]
    (bad / request["inputs"]["expression.h5ad"]).write_bytes(b"changed count bytes")
    with pytest.raises(ValueError, match="single-cell replay input differs"):
        replay_thesis_package(
            package,
            seal["root_sha256"],
            output=workspace_tmp_path / "rejected-replay",
            input_root=bad,
            allow_data_execution=True,
        )
