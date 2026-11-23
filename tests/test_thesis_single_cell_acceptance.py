"""L3 execution acceptance on one synthetic, original-count single-cell cohort."""

import io
import json
import os
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from anndata import AnnData, read_h5ad
from scipy import sparse

from devbench.analysis.checkpoints import digest
from devbench.core.contrast_design import ContrastSpec, MountedModelSpec
from devbench.core.scrna_composition import (
    CompositionObservationSpec,
    CompositionResult,
    CompositionSpec,
)
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import OperationBlocked
from devbench.platform.adaptive_workflow_state import ArtifactVersion, BasisUse
from devbench.platform.scrna_general_pseudobulk import (
    GeneralPseudobulkSpec,
    register_general_pseudobulk,
    stage_general_pseudobulk,
)
from devbench.platform.scrna_qc_handoff import (
    accepted_qc_selection,
    register_composition_after_qc,
    stage_composition_after_qc,
)
from devbench.platform.workbench_thesis import _retain_pseudobulk_inputs
from devbench.platform.workflow_actions import WorkflowActions
from devharness_controlled.native_contracts import sccoda_kernel_images
from devharness_core.provenance import copy_artifact, hash_artifact
from rna_kernels.canonical import canonical_json
from rna_kernels.scrna_upstream_contract import SCRNA_OPERATIONS
from tests.test_investigation_invalidation import make
from tests.test_thesis_bulk_acceptance import _insert_fit, _research_actions
from tests.test_tier_a_scrna import _failure_evidence, _run_stage

pytestmark = pytest.mark.wal
_GROUPS = ("reference", "case", "recovery")
_DONORS_PER_GROUP = 12
_ABSENT = "reference-00"
_SPARSE = "reference-01"


def _sha(path: Path) -> str:
    return hash_artifact(path)["sha256"]


def _count_layer_sha(values) -> str:
    counts = sparse.csr_matrix(values, copy=True)
    counts.sum_duplicates()
    counts.eliminate_zeros()
    counts.sort_indices()
    digest_bytes = sha256()
    for array in (counts.shape, counts.indptr, counts.indices):
        digest_bytes.update(np.asarray(array, dtype="<i8").tobytes())
    digest_bytes.update(counts.data.astype("<f8").tobytes())
    return digest_bytes.hexdigest()


def _observations(rng: np.random.Generator) -> pd.DataFrame:
    concentrations = ((18, 4), (18, 22), (18, 9))
    records = []
    for group_index, (group, concentration) in enumerate(
        zip(_GROUPS, concentrations, strict=True),
    ):
        for index in range(_DONORS_PER_GROUP):
            donor = f"{group}-{index:02}"
            lane = (index + group_index) % 4
            total = 70 + 3 * index
            p_cells = int(rng.binomial(total, rng.dirichlet(concentration)[1]))
            p_cells = {_ABSENT: 0, _SPARSE: 9}.get(
                donor, max(1, min(total - 1, p_cells)),
            )
            for population, size in (("N", total - p_cells), ("P", p_cells)):
                records.extend([{
                    "donor": donor,
                    "specimen": f"sample-{donor}",
                    "group": group,
                    "population": population,
                    "batch": f"technical-{lane // 2}",
                    "capture": f"lane-{lane}",
                }] * size)
    index = pd.Index([f"cell-{i:05d}" for i in range(len(records))])
    return pd.DataFrame(records, index=index)


def _make_source(root: Path) -> tuple[Path, AnnData, set[str]]:
    rng = np.random.default_rng(2194)
    obs = _observations(rng)
    n_genes = 144
    latent = rng.normal(size=len(obs)) + 0.55 * obs["population"].eq("P").to_numpy()
    loadings = rng.normal(0, 0.22, n_genes)
    means = rng.uniform(1.0, 4.0, n_genes) * np.exp(latent[:, None] * loadings)
    target = obs["population"].eq("P")
    means[target & obs["group"].eq("case"), 1:13] *= 2.8
    means[target & obs["group"].eq("recovery"), 1:13] *= 1.35
    means[obs["batch"].eq("technical-1"), 45:77] *= 1.22
    values = rng.poisson(means)
    bad_cells = set(obs.groupby("capture", sort=True).head(1).index)
    values[obs.index.isin(bad_cells)] = 0
    counts = sparse.csr_matrix(values)
    var = pd.DataFrame(index=["MT-ND1", *(f"g-{i:03d}" for i in range(n_genes - 1))])
    data = AnnData(sparse.csr_matrix(np.log1p(values)), obs=obs, var=var)
    data.layers["counts"] = counts
    path = root / "original_counts.h5ad"
    data.write_h5ad(path)
    return path, data, bad_cells


def _register(
    flow, executor, *, name, source, role, membership, label_qualification=None,
) -> ArtifactVersion:
    descriptor = SCRNA_OPERATIONS[role[0]].inputs[role[1]]
    history = ("raw_counts",) if role == ("scrna_batch_qc", "expression") else ()
    version = ArtifactVersion(
        version_id=name,
        sha256=executor.training.put_file(source),
        kind=descriptor.kind,
        representation=descriptor.representation,
        biological_unit="donor",
        unit_membership_sha256=membership,
        measurement_history=history,
        label_origin="independent" if label_qualification is not None else "none",
        label_qualification_sha256=label_qualification,
    )
    flow.register_artifact(version)
    return version


def _output(executor, result, port) -> bytes:
    version = ArtifactVersion.model_validate(result["outputs"][port])
    with executor.open_output(version) as stream:
        raw = stream.read(16 * 1024**2 + 1)
    assert len(raw) <= 16 * 1024**2
    assert sha256(raw).hexdigest() == version.sha256
    return raw


def _qc_parameters(*, min_genes: int = 10):
    return {
        "batch_key": "batch", "qc_group_key": "batch",
        "retention_group_key": "donor", "mad_definition": "scaled_mad",
        "mad_limit": 12.0, "min_genes": min_genes,
    }


def _qc(flow, executor, actions, data, bad_cells):
    result = _run_stage(
        flow, executor, actions, "l3-qc", "scrna_batch_qc",
        _qc_parameters(),
        {"expression": ("l3-original", None)},
    )
    report = json.loads(_output(executor, result, "qc_report"))
    metrics = pd.read_csv(
        io.BytesIO(_output(executor, result, "qc_metrics")), index_col=0,
    )
    assert metrics.index.equals(data.obs_names)
    assert set(metrics.index[~metrics["qc_pass"]]) == bad_cells
    assert report["cells_retained"] == data.n_obs - len(bad_cells)
    assert {row["batch"] for row in report["thresholds"]} == set(data.obs["batch"])
    assert {row["group"] for row in report["group_retention"]["groups"]} == (
        set(data.obs["donor"])
    )
    return result, metrics["qc_pass"]


def _check_doublets(flow, executor, actions, retained: AnnData) -> None:
    result = _run_stage(
        flow, executor, actions, "l3-doublets", "scrna_lane_doublets",
        {"batch_key": "batch", "capture_key": "capture", "seed": 9},
        {"expression": ("l3-original", None), "qc_metrics": ("l3-qc", "qc_metrics")},
    )
    report = json.loads(_output(executor, result, "doublet_report"))
    assert report["filtering"] == "flag_only"
    assert report["capture_key"] == "capture"
    assert report["method"] == "scanpy_scrublet_per_declared_capture"
    lanes = {row["batch"]: row for row in report["batches"]}
    assert set(lanes) == set(retained.obs["capture"])
    assert all(row["status"] == "completed" for row in lanes.values())
    assert {lane: row["cells"] for lane, row in lanes.items()} == (
        retained.obs["capture"].value_counts().to_dict()
    )
    predictions = pd.read_csv(
        io.BytesIO(_output(executor, result, "predictions")), index_col=0,
    )
    assert predictions.index.equals(retained.obs_names)
    assert np.isfinite(predictions["doublet_score"]).all()
    assert predictions["predicted_doublet"].dtype.kind == "b"


def _prepare(flow, executor, actions, retained: AnnData, root: Path) -> Path:
    result = _run_stage(
        flow, executor, actions, "l3-prepare", "scrna_prepare",
        {"batch_key": "batch", "n_top_genes": 72, "normalization": "shifted_log"},
        {
            "expression": ("l3-original", None),
            "qc_metrics": ("l3-qc", "qc_metrics"),
            "qc_features": ("l3-qc", "qc_features"),
        },
    )
    path = root / "selected.h5ad"
    path.write_bytes(_output(executor, result, "selected"))
    prepared = read_h5ad(path)
    assert prepared.obs_names.equals(retained.obs_names)
    assert prepared.obs["donor"].astype(str).equals(retained.obs["donor"].astype(str))
    assert prepared.n_vars == 72
    expected = retained[:, prepared.var_names].layers["counts"]
    assert _count_layer_sha(prepared.layers["counts"]) == _count_layer_sha(expected)
    assert _count_layer_sha(prepared.X) != _count_layer_sha(expected)
    return path


def _register_labels(
    flow, executor, root, selected, retained, membership, label_source,
):
    labels = root / "integration-labels.json"
    labels.write_bytes(canonical_json(retained.obs["population"].to_dict()))
    qualification = root / "integration-qualification.json"
    qualification.write_bytes(canonical_json({
        "format": "devbench.integration-label-verification/1",
        "actor": "synthetic-fixture-reviewer",
        "rationale": "Independent fixture population assignments on retained cells.",
        "label_origin": "independent_assignment",
        "source_sha256": label_source,
        "labels_sha256": _sha(labels),
        "selected_sha256": _sha(selected),
        "cell_axis_sha256": digest(retained.obs_names.tolist()),
    }))
    proof_sha = _sha(qualification)
    for name, role, path in (
        ("l3-labels", "labels", labels),
        ("l3-label-qualification", "label_qualification", qualification),
    ):
        _register(
            flow, executor, name=name, source=path, membership=membership,
            role=("scrna_integrate_qualified", role), label_qualification=proof_sha,
        )
    flow.record_basis("l3-label-review", "review_basis", proof_sha)
    return proof_sha, _sha(labels)


def _check_integration(flow, executor, actions, proof_sha, label_sha, retained) -> None:
    step = executor.register_single_cell_step(
        {
            "operation": "scrna_integrate_qualified",
            "parameters": {
                "batch_key": "batch", "correction": "harmony", "n_pcs": 12, "seed": 8,
            },
        },
        step_id="l3-harmony", branch_id="single-cell", biological_unit="donor",
        sources={
            "selected": ("l3-prepare", "selected"),
            "labels": ("l3-labels", None),
            "label_qualification": ("l3-label-qualification", None),
        },
        basis=(BasisUse(basis_id="l3-label-review"),),
    )
    flow.insert(step)
    result = actions.perform_adaptive(flow, step.step_id)
    assert result["status"] == "succeeded", _failure_evidence(executor, result)
    report = json.loads(_output(executor, result, "integration_report"))
    assert report["published_condition"] == "harmony"
    assert report["conditions"]["harmony"]["embedding_valid"] is True
    assert report["label_qualification"]["qualification_sha256"] == proof_sha
    assert report["scib"]["status"] == "measured"
    assert report["scib"]["provenance"]["labels_sha256"] == label_sha
    coordinates = _output(executor, result, "embedding")
    graph = _output(executor, result, "graph")
    image = _output(executor, result, "figure")
    assert report["plot"]["plot_state"]["render_verified"] is True
    assert report["plot"]["data_sha256"] == sha256(coordinates).hexdigest()
    assert report["plot"]["image_sha256"] == sha256(image).hexdigest()
    assert report["conditions"]["harmony"]["provenance"]["graph_sha256"] == (
        sha256(graph).hexdigest()
    )
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert sparse.load_npz(io.BytesIO(graph)).shape == (retained.n_obs, retained.n_obs)
    embedding = pd.read_csv(io.BytesIO(coordinates), index_col=0)
    assert embedding.index.equals(retained.obs_names)
    assert np.isfinite(embedding[["UMAP_1", "UMAP_2"]]).all().all()


def _pseudobulk(root, source, retained, count_provenance, selection, *, suffix=""):
    spec = GeneralPseudobulkSpec(
        source_sha256=_sha(source), assay="scrna", measurement="umi_counts",
        count_provenance=count_provenance, family_id="l3-pseudobulk" + suffix,
        regime="S-I", unit_column="donor", specimen_column="specimen",
        condition_column="group", population_column="population", groups=_GROUPS,
        population="P",
        contrast=ContrastSpec(numerator="case", denominator="reference"),
        technical_factors=("batch",),
        model=MountedModelSpec(comparison="lrt", units="independent"),
    )
    staged = stage_general_pseudobulk(
        source, spec, root / ("pseudobulk" + suffix), qc_selection=selection,
    )
    assert staged.pipeline is not None, staged.summary.get("refusals")
    assert staged.summary["source_sha256"] == _sha(source)
    assert staged.summary["qc_selection"] == selection.binding.model_dump(mode="json")
    assert staged.summary["qc_mask_sha256"] == selection.mask_sha256()
    assert staged.summary["source_cells"] == len(selection.cell_ids)
    assert staged.summary["qc_retained_cells"] == retained.n_obs
    assert staged.summary["counts_layer"] == "layers/counts"
    assert staged.summary["low_cell_threshold"] == 20
    assert staged.summary["missing_profile_count"] == 1
    assert staged.summary["missing_combinations"] == "absent_profiles_never_zero_filled"
    assert staged.summary["artifact_sha256"]["expression.h5"] == (
        _sha(staged.directory / "expression.h5")
    )
    profiles = pd.read_csv(staged.directory / "metadata.csv")
    assert profiles["donor"].nunique() == len(profiles) == 3 * _DONORS_PER_GROUP - 1
    assert set(profiles["donor"]) == set(retained.obs["donor"]) - {_ABSENT}
    sparse_cells = (
        retained.obs["donor"].eq(_SPARSE) & retained.obs["population"].eq("P")
    )
    assert profiles.loc[profiles["donor"].eq(_SPARSE), "cell_count"].item() == (
        sparse_cells.sum()
    )
    assert set(staged.summary["low_cell_profile_ids"]) == set(
        profiles.loc[profiles["cell_count"] < 20, "sample_id"]
    )
    assert staged.summary["low_cell_profile_count"] > 0
    _check_profile_counts(staged.directory / "expression.h5", profiles, retained)
    return staged


def _check_profile_counts(path, profiles, retained) -> None:
    counts = retained.layers["counts"]
    target = retained.obs["population"].eq("P")
    expected = []
    for row in profiles.itertuples(index=False):
        observed = (target & retained.obs["donor"].eq(row.donor)).to_numpy()
        assert observed.sum() == row.cell_count
        expected.append(np.asarray(counts[observed].sum(axis=0)).ravel())
    with h5py.File(path, "r") as handle:
        assert handle["missing_profiles/specimen"].asstr()[:].tolist() == [
            f"sample-{_ABSENT}",
        ]
        assert handle["sample_names"].asstr()[:].tolist() == (
            profiles["sample_id"].tolist()
        )
        assert handle["gene_names"].asstr()[:].tolist() == retained.var_names.tolist()
        assert _count_layer_sha(handle["expression"][:]) == (
            _count_layer_sha(np.column_stack(expected))
        )


def _check_edger(flow, executor, staged, retained, *, suffix="") -> WorkflowActions:
    with pytest.raises(PermissionError, match="owning workflow"):
        register_general_pseudobulk(executor, staged)
    _, inputs = register_general_pseudobulk(executor, staged, workflow=flow)
    with flow.authority._execution_tx(flow.lineage_id) as db:
        _retain_pseudobulk_inputs(flow, db, staged, inputs)
    assert staged.pipeline is not None
    step_id = "fit" + suffix
    preflight = _insert_fit(flow, executor, staged.pipeline, inputs, step_id=step_id)
    assert preflight["metrics"]["declared_unit_count"] == 3 * _DONORS_PER_GROUP - 1
    assert preflight["metrics"]["columns"] == 4
    actions = _research_actions(
        flow, executor, inputs, preflight, step_ids=(step_id,), suffix=suffix,
    )
    result = actions.perform_adaptive(flow, step_id)
    assert result["status"] == "succeeded", result["reason"]
    io_record = executor.runs / result["attempt_id"] / "io.json"
    method = json.loads(io_record.read_bytes())["tool"]["method"]
    assert method["solver"] == "edger_v4"
    assert method["test"] == "lrt" and method["test_df"] == 2
    current = flow.current_use(step_id, "result", purpose="query", use="claim")
    with executor.open_output(current) as stream:
        genes = pq.read_table(stream)
    assert genes["gene_id"].to_pylist() == retained.var_names.tolist()
    p = genes["p_value"].to_numpy()
    q = genes["q_value"].to_numpy()
    assert np.all((0 <= p) & (p <= q) & (q <= 1))
    assert np.count_nonzero(q[1:13] < 0.05) >= 8
    return actions


def _check_unverified_mask_refuses_registration(
    flow, executor, root, source, staged, selection,
) -> None:
    spec = GeneralPseudobulkSpec.model_validate_json(
        canonical_json(staged.summary["specification"])
    )
    flags = (not selection.passed[0], *selection.passed[1:])
    changed = replace(selection, passed=flags)
    unverified = stage_general_pseudobulk(
        source, spec, root / "unverified-mask", qc_selection=changed,
    )
    with pytest.raises(OperationBlocked, match="qc_handoff_receipt_changed"):
        register_general_pseudobulk(executor, unverified, workflow=flow)


def _composition_declaration(source: Path) -> CompositionObservationSpec:
    return CompositionObservationSpec(
        source_sha256=_sha(source), biological_unit="donor",
        biological_unit_column="donor", condition_column="group",
        cell_type_column="population",
    )


def _check_sc_coda(
    flow, executor, actions, root, source, retained, *, suffix="",
) -> None:
    data, composition = register_composition_after_qc(
        flow, executor, "l3-qc", source, _composition_declaration(source),
        version_id="l3-composition-input" + suffix,
    )
    assert data.source_sha256 == _sha(source)
    assert data.cell_types == ("N", "P")
    donors = {sample.biological_unit_id: sample for sample in data.samples}
    assert len(donors) == len(data.samples) == 3 * _DONORS_PER_GROUP
    assert set(donors) == set(retained.obs["donor"])
    for donor, cells in retained.obs.groupby("donor", observed=True):
        sample = donors[donor]
        assert cells["group"].unique().tolist() == [sample.condition]
        assert sample.cells_observed == len(cells)
        assert sample.counts == tuple(
            int(cells["population"].eq(label).sum()) for label in data.cell_types
        )
    assert donors[_ABSENT].counts[1] == 0
    assert donors[_SPARSE].counts[1] == (
        retained.obs["donor"].eq(_SPARSE) & retained.obs["population"].eq("P")
    ).sum()
    analysis = CompositionSpec(
        reference_cell_type="N", baseline_condition="reference",
        target_est_fdr=0.05, seed=1989, num_warmup=1_500,
        num_samples=450, target_accept_probability=0.99,
    )
    name = "l3-composition-spec" + suffix
    path = root / f"{name}.json"
    path.write_bytes(canonical_json(analysis.model_dump(mode="json")))
    spec_artifact = _register(
        flow, executor, name=name, source=path,
        role=("scrna_sccoda", "composition_spec"),
        membership=data.biological_unit_mapping_sha256,
    )
    sources = {
        "composition_input": (composition.version_id, None),
        "composition_spec": (spec_artifact.version_id, None),
    }
    result = _run_stage(
        flow, executor, actions, "l3-composition" + suffix, "scrna_sccoda", {}, sources,
    )
    raw = _output(executor, result, "composition_result")
    verified = CompositionResult.model_validate_json(raw)
    assert verified.status == "succeeded"
    assert verified.inference_scope == "bayesian_relative_cell_type_composition"
    assert verified.condition_biological_units == dict.fromkeys(
        _GROUPS, _DONORS_PER_GROUP,
    )
    assert verified.measured_zero_count == 1
    assert verified.sampling is not None
    assert verified.sampling.draws == analysis.num_samples
    assert verified.sampling.algorithm == "NUTS"
    assert verified.runtime is not None
    assert verified.runtime.image_id == executor.mounted_image_id
    assert verified.runtime.pertpy_version == "1.5.0"
    assert verified.est_fdr is not None and verified.est_fdr <= analysis.target_est_fdr
    case = next(row for row in verified.effects if row.condition == "case")
    assert case.cell_type == "P" and case.credible_effect
    assert case.relative_log_coefficient is not None
    assert case.relative_log_coefficient > 0
    prohibited = {"p_value", "q_value", "gene_statistics"}
    report = json.loads(raw)
    assert prohibited.isdisjoint(report)
    assert all(prohibited.isdisjoint(row) for row in report["effects"])


def test_l3_mounted_single_cell_acceptance(workspace_tmp_path):
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in sccoda_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to the pinned scCODA image")
    root = workspace_tmp_path
    source, data, rejected = _make_source(root)
    source_sha = _sha(source)
    original_counts_sha = _count_layer_sha(data.layers["counts"])
    membership = digest([
        (cell, row.donor, row.specimen, row.group)
        for cell, row in zip(data.obs_names, data.obs.itertuples(), strict=True)
    ])
    original_labels = root / "independent-fixture-labels.json"
    original_labels.write_bytes(canonical_json(data.obs["population"].to_dict()))
    flow = make(root)
    executor = TierAExecutor(
        flow.ledger, mounted_image_id=image, allow_data_execution=True,
    )
    actions = WorkflowActions(
        None, flow.ledger, actor="operator", selector=None,
        tier_a_executor=executor, allow_data_execution=True,
    )
    version = _register(
        flow, executor, name="l3-original", source=source, membership=membership,
        role=("scrna_batch_qc", "expression"),
    )
    assert version.sha256 == source_sha
    qc, keep = _qc(flow, executor, actions, data, rejected)
    retained = data[keep.to_numpy(), :].copy()
    _check_doublets(flow, executor, actions, retained)
    selected = _prepare(flow, executor, actions, retained, root)
    proof_sha, label_sha = _register_labels(
        flow, executor, root, selected, retained, membership, _sha(original_labels),
    )
    _check_integration(flow, executor, actions, proof_sha, label_sha, retained)

    with pytest.raises(OperationBlocked, match="handoff_source_changed"):
        accepted_qc_selection(flow, executor, "l3-qc", source_sha256=digest("other"))
    selection = accepted_qc_selection(flow, executor, "l3-qc", source_sha256=source_sha)
    assert selection.passed == tuple(keep.tolist())
    assert selection.binding.receipt_sha256 == qc["receipt_sha256"]
    assert selection.binding.metrics_sha256 == qc["outputs"]["qc_metrics"]["sha256"]
    provenance = (
        f"Synthetic original counts {source_sha}, QC mask {selection.mask_sha256()}, "
        f"independent fixture labels {_sha(original_labels)}."
    )
    staged = _pseudobulk(root, source, retained, provenance, selection)
    _check_unverified_mask_refuses_registration(
        flow, executor, root, source, staged, selection,
    )
    reviewed = _check_edger(flow, executor, staged, retained)
    _check_sc_coda(flow, executor, reviewed, root, source, retained)
    assert _sha(source) == source_sha
    assert _count_layer_sha(read_h5ad(source).layers["counts"]) == original_counts_sha
    _check_qc_reanalysis(flow, executor, actions, root, source, data, staged, selection)
    assert _sha(source) == source_sha


def _check_qc_reanalysis(
    flow, executor, actions, root, source, data, staged, selection,
):
    branches = {"fit": "result", "l3-composition": "composition_result"}
    history = {step: flow.history(step) for step in branches}
    graph = {row["step_id"]: row for row in flow.projection_snapshot()["steps"]}
    for step, port in branches.items():
        assert "l3-qc" in graph[step]["dependencies"]
        provenance = flow.disclosure_provenance(step, port)
        assert {selection.binding.metrics_sha256, _sha(source)} <= set(
            provenance["dependencies"]
        )
    min_genes = 115
    qc_step = executor.register_single_cell_step(
        {
            "operation": "scrna_batch_qc",
            "parameters": _qc_parameters(min_genes=min_genes),
        },
        step_id="l3-qc", branch_id="single-cell", biological_unit="donor",
        sources={"expression": ("l3-original", None)},
    )
    flow.revise(
        qc_step, reason="diagnostic_revisit", evidence_sha256=digest("revisit"),
    )
    for step, port in branches.items():
        with pytest.raises(OperationBlocked, match="current_use_unavailable"):
            flow.current_use(step, port, purpose="export")
        assert flow.history(step) == history[step]
    with pytest.raises(OperationBlocked, match="current_use_unavailable"):
        register_general_pseudobulk(executor, staged, workflow=flow)
    with pytest.raises(OperationBlocked, match="current_use_unavailable"):
        stage_composition_after_qc(
            flow, executor, "l3-qc", source, _composition_declaration(source),
        )

    result = actions.perform_adaptive(flow, "l3-qc")
    assert result["status"] == "succeeded", result["reason"]
    revised = accepted_qc_selection(
        flow, executor, "l3-qc", source_sha256=selection.binding.source_sha256,
    )
    assert revised.binding.generation == selection.binding.generation + 1
    assert revised.binding.receipt_sha256 == result["receipt_sha256"]
    assert revised.binding.metrics_sha256 != selection.binding.metrics_sha256
    observed_genes = np.asarray((data.layers["counts"] > 0).sum(axis=1)).ravel()
    expected = np.asarray(selection.passed) & (observed_genes >= min_genes)
    np.testing.assert_array_equal(revised.passed, expected)
    assert 0 < int(expected.sum()) < sum(selection.passed)

    retained = data[expected, :].copy()
    suffix = "-qc2"
    next_stage = _pseudobulk(
        root, source, retained, f"Accepted QC receipt {revised.binding.receipt_sha256}",
        revised, suffix=suffix,
    )
    reviewed = _check_edger(flow, executor, next_stage, retained, suffix=suffix)
    _check_sc_coda(flow, executor, reviewed, root, source, retained, suffix=suffix)
    for step, port in branches.items():
        dependencies = flow.disclosure_provenance(step + suffix, port)["dependencies"]
        assert revised.binding.metrics_sha256 in dependencies
        assert selection.binding.metrics_sha256 not in dependencies
        assert flow.history(step) == history[step]
        with pytest.raises(OperationBlocked, match="current_use_unavailable"):
            flow.current_use(step, port, purpose="export")
    _check_revised_package(flow, executor, root)


def _check_revised_package(flow, executor, root):
    from devbench.analysis.thesis_synthesis import (
        ThesisSynthesisSpec,
        verify_thesis_package,
        write_thesis_package,
    )

    schema = root / "reanalysis-answer-schema.json"
    schema.write_bytes(canonical_json({
        "type": "object",
        "required": ["gene_statistics", "composition"],
        "additionalProperties": False,
        "properties": {
            "gene_statistics": {"type": "array"},
            "composition": {"type": "object"},
        },
    }))
    spec = ThesisSynthesisSpec.model_validate({
        "question": "Do the donor-level findings persist after revised QC?",
        "outcome": "completed",
        "expected_revision": flow.status()["revision"],
        "answer_schema_sha256": _sha(schema),
        "fields": {
            "gene_statistics": {
                "step_id": "fit-qc2", "port": "result",
                "projection": "tier_a_gene_statistics",
            },
            "composition": {
                "step_id": "l3-composition-qc2", "port": "composition_result",
            },
        },
    })
    package = root / "revised-thesis"
    seal = write_thesis_package(
        package, flow, spec, answer_schema=schema, open_artifact=executor.open_output,
        tier_a_executor=executor,
    )
    assert seal["answer_status"] == "validated"
    assert verify_thesis_package(package, seal["root_sha256"])["valid"]
    manifest = json.loads((package / "provenance_manifest.json").read_bytes())
    assert manifest["clinical_release_allowed"] is False
    for field, step_name in (
        ("gene_statistics", "fit-qc2"), ("composition", "l3-composition-qc2"),
    ):
        assert manifest["fields"][field]["receipt_sha256"] == (
            flow.history(step_name)[-1]["receipt_sha256"]
        )
    answer = json.loads((package / "final_discovery.json").read_bytes())
    composition = CompositionResult.model_validate_json(
        canonical_json(answer["composition"])
    )
    assert composition.status == "succeeded"
    assert "gene_statistics" not in answer["composition"]
    index = json.loads((package / "replay_bundle/index.json").read_bytes())
    assert {recipe["step_id"] for recipe in index["steps"]} == {"fit-qc2"}
    assert {recipe["step_id"] for recipe in index["single_cell_steps"]} == {
        "l3-qc", "l3-composition-qc2",
    }
    _check_replay_from_original_inputs(root, package, seal, executor, index)


def _check_replay_from_original_inputs(root, package, seal, executor, index):
    from devbench.analysis.thesis_replay import replay_thesis_package

    assert len(index["qc_derivations"]) == 2
    current = {recipe["step_id"]: recipe for recipe in index["single_cell_steps"]}
    original = current["l3-qc"]["inputs"]["expression.h5ad"]
    declaration = current["l3-composition-qc2"]["inputs"]["composition_spec.json"]
    source = root / "original-replay-inputs"
    source.mkdir(mode=0o700)
    for identity in (original, declaration):
        supplied = copy_artifact(
            executor.training.root / identity, source / identity,
            max_bytes=16 * 1024**2, max_entries=1,
        )
        assert supplied["sha256"] == identity
    replay = replay_thesis_package(
        package, seal["root_sha256"], output=root / "replayed-from-originals",
        input_root=source, allow_data_execution=True,
    )
    assert replay["exact_genes_parquet_match"] is True
    assert replay["exact_single_cell_artifacts_match"] is True
    assert replay["exact_qc_derived_inputs_match"] is True
    assert len(replay["qc_derivations"]) == 2
