"""Resume verified native work and retain a real, balanced negative control."""

import io
import json
import os
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from anndata import AnnData
from scipy.sparse import csr_matrix

from devbench.analysis.checkpoints import digest
from devbench.core.contrast_design import ContrastSpec, MountedModelSpec
from devbench.engine.tier_a_executor import TIER_A_INTERRUPTED, TierAExecutor
from devbench.engine.workflow_operations import (
    OperationBlocked,
    WorkflowOperationLedger,
)
from devbench.platform.adaptive_workflow import AdaptiveWorkflow, ArtifactVersion
from devbench.platform.scrna_general_pseudobulk import (
    GeneralPseudobulkSpec,
    register_general_pseudobulk,
    stage_general_pseudobulk,
)
from devbench.platform.workflow_actions import WorkflowActions
from devharness_controlled.native_contracts import single_cell_kernel_images
from devharness_core.provenance import hash_artifact
from devharness_core.watchdog import RemoteWorkerError
from tests.test_investigation_invalidation import make
from tests.test_thesis_bulk_acceptance import _insert_fit, _research_actions

pytestmark = pytest.mark.wal
_ARM_SIZE = 50
_BATCH_ARM_SIZE = 25
_CELLS_PER_DONOR = 20
_GENES = ("MT-ND1", *(f"gene-{i:03d}" for i in range(95)))
_VARYING = 40
_OWNED_WORKER_EXIT = 73


def _file_sha(path: Path) -> str:
    return hash_artifact(path)["sha256"]


def _null_counts(rng: np.random.Generator) -> np.ndarray:
    """Distinct donor profiles with identical per-gene distributions by arm."""
    const = rng.integers(200, 501, len(_GENES))
    matrix = np.repeat(const[:, None], 2 * _ARM_SIZE, axis=1)
    for gene in range(0, _VARYING, 2):
        for left in (0, _BATCH_ARM_SIZE):
            reference = slice(left, left + _BATCH_ARM_SIZE)
            case = slice(left + _ARM_SIZE, left + _ARM_SIZE + _BATCH_ARM_SIZE)
            counts = rng.integers(75, 326, _BATCH_ARM_SIZE)
            matrix[gene, reference] = counts
            matrix[gene, case] = rng.permutation(counts)
        matrix[gene + 1] = 400 - matrix[gene]
    assert np.unique(matrix, axis=1).shape[1] == 2 * _ARM_SIZE
    assert np.unique(matrix.sum(axis=0)).size == 1
    for left in (0, _BATCH_ARM_SIZE):
        reference_values = matrix[:, left:left + _BATCH_ARM_SIZE]
        case_values = matrix[:, left + _ARM_SIZE:left + _ARM_SIZE + _BATCH_ARM_SIZE]
        np.testing.assert_array_equal(
            np.sort(reference_values, axis=1), np.sort(case_values, axis=1),
        )
    return matrix


def _null_source(root: Path) -> tuple[Path, AnnData, np.ndarray]:
    rng = np.random.default_rng(1618)
    pooled = _null_counts(rng)
    split_prob = np.full(_CELLS_PER_DONOR, 1 / _CELLS_PER_DONOR)
    rows = []
    blocks = []
    for i, library in enumerate(pooled.T):
        donor = f"donor-{i:03d}"
        condition = "reference" if i < _ARM_SIZE else "case"
        batch = i % _ARM_SIZE // _BATCH_ARM_SIZE
        rows.extend([{
            "donor": donor, "specimen": f"specimen-{i:03d}",
            "condition": condition, "batch": f"batch-{batch}", "population": "P",
        }] * _CELLS_PER_DONOR)
        blocks.append(rng.multinomial(library, split_prob).T)
    nodes = [f"cell-{i:04d}" for i in range(len(rows))]
    obs = pd.DataFrame(rows, index=nodes)
    counts = np.vstack(blocks)
    obs.loc["bad-qc-cell"] = rows[0]
    counts = np.vstack((counts, np.zeros(len(_GENES), dtype=counts.dtype)))
    source = AnnData(
        X=csr_matrix(np.log1p(counts)), obs=obs, var=pd.DataFrame(index=list(_GENES)),
    )
    source.layers["counts"] = csr_matrix(counts)
    path = root / "balanced-null.h5ad"
    source.write_h5ad(path)
    return path, source, pooled


def _actions(flow, executor):
    return WorkflowActions(
        None, flow.ledger, actor="synthetic-operator", selector=None,
        tier_a_executor=executor, allow_data_execution=True,
    )


def _reopen(flow, image):
    ledger = WorkflowOperationLedger(flow.ledger.path, scientific=True)
    reopened = AdaptiveWorkflow(ledger, flow.lineage_id)
    executor = TierAExecutor(ledger, allow_data_execution=True, mounted_image_id=image)
    return reopened, executor


def _step(flow, executor, step_id, operation, parameters, sources):
    step = executor.register_single_cell_step(
        {"operation": operation, "parameters": parameters},
        step_id=step_id, branch_id="resume", biological_unit="donor", sources=sources,
    )
    flow.insert(step)


def _qc_with_lost_acknowledgement(flow, executor, monkeypatch):
    real_run = executor.run
    observed: dict[str, Any] = {}

    def interrupt(envelope):
        receipt = real_run(envelope)
        assert receipt.status == "succeeded"
        observed["receipt"] = receipt
        raise KeyboardInterrupt("fixture drops the QC acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(executor, "run", interrupt)
        with pytest.raises(KeyboardInterrupt, match="drops the QC acknowledgement"):
            _actions(flow, executor).perform_adaptive(flow, "resume-qc")
    receipt = observed["receipt"]
    (attempt,) = flow.authority.attempts(flow.lineage_id)
    assert attempt["token"]["attempt_id"] == receipt.job_id
    assert attempt["execution_state"] == "submission_uncertain"
    assert flow.history("resume-qc") == ()
    saved = executor.runs / receipt.job_id / "receipt.json"
    assert saved.is_file()
    return attempt["token"], _file_sha(saved)


def _preparation_step(flow, executor):
    _step(
        flow, executor, "resume-interrupted", "scrna_prepare",
        {"batch_key": "batch", "n_top_genes": 24},
        {
            "expression": ("original", None),
            "qc_features": ("resume-qc", "qc_features"),
            "qc_metrics": ("resume-qc", "qc_metrics"),
        },
    )


def _stop_before_preparation_receipt(flow, executor, monkeypatch):
    _preparation_step(flow, executor)
    stopped: dict[str, Any] = {}

    def interrupt(
        token, envelope, _directory, _family, outputs, failure, tool, _started,
    ):
        assert envelope["step"]["step_id"] == "resume-interrupted"
        assert failure is None and set(outputs) == {"selected", "hvg"}
        assert tool["oci"]["cleanup_confirmed"] is True
        assert executor._read_custody(token)[2]["phase"] == "worker_stopped"
        stopped.update(token)
        raise KeyboardInterrupt("fixture stops before the preparation receipt")

    with monkeypatch.context() as patch:
        patch.setattr(executor, "_finish_receipt", interrupt)
        with pytest.raises(KeyboardInterrupt, match="before the preparation receipt"):
            _actions(flow, executor).perform_adaptive(flow, "resume-interrupted")
    assert flow.authority.attempt(stopped)["execution_state"] == "submission_uncertain"
    assert not (executor.runs / stopped["attempt_id"] / "receipt.json").exists()
    return stopped


def _crash_after_owned_oci_start(stage, request, identity):
    """Spawn target that exits only after starting its verified owned container."""
    from devbench.engine import mounted_kernel_oci as oci
    from devbench.engine.tier_a_scrna import execute_mounted_scrna

    class CrashAfterStart(oci.LocalDocker):
        def _call(self, arguments, **kwargs):
            if arguments[:2] == ["start", "--attach"]:
                # The production launcher has already verified the container
                # identity and mounts and sealed the create record.
                super()._call(["start", arguments[2]], **kwargs)
                os._exit(_OWNED_WORKER_EXIT)
            return super()._call(arguments, **kwargs)

    oci.LocalDocker = CrashAfterStart
    return execute_mounted_scrna(stage, request, identity)


def _exit_during_preparation(flow, executor, monkeypatch):
    from devbench.engine import tier_a_executor

    _preparation_step(flow, executor)
    run_sandboxed = tier_a_executor.run_sandboxed
    observed: dict[str, str] = {}

    def dispatch(worker, args, **kwargs):
        request = args[1]
        assert worker is tier_a_executor.execute_mounted_scrna
        assert request["operation"] == "scrna_prepare"
        observed["attempt_id"] = request["attempt_id"]
        return run_sandboxed(_crash_after_owned_oci_start, args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(tier_a_executor, "run_sandboxed", dispatch)
        with pytest.raises(RemoteWorkerError, match=f"exit code {_OWNED_WORKER_EXIT}"):
            _actions(flow, executor).perform_adaptive(flow, "resume-interrupted")
    token = next(
        attempt["token"]
        for attempt in flow.authority.attempts(flow.lineage_id)
        if attempt["token"]["attempt_id"] == observed["attempt_id"]
    )
    assert flow.authority.attempt(token)["execution_state"] == "submission_uncertain"
    return token


def _assert_owned_crash_cleanup(executor, token, image):
    from devbench.engine import mounted_kernel_oci as oci

    _, launch, stopped = executor._read_custody(token)
    created_path = executor.runs / token["attempt_id"] / oci._CREATED
    created = json.loads(created_path.read_bytes())
    proof = stopped["mounted_oci_cleanup"]
    assert proof["disposition"] == "removed" and proof["cleanup_confirmed"] is True
    assert proof["image_id"] == launch["mounted_oci"]["image_id"] == image
    expected_name = f"devbench-rna-tier-a-{token['attempt_id']}"
    assert proof["name"] == created["name"] == expected_name
    assert proof["container_id"] == created["container_id"]
    assert proof["request_sha256"] == launch["mounted_oci"]["request_sha256"]


def _verified_mask(flow, executor, data):
    metrics = flow.current_use("resume-qc", "qc_metrics", purpose="query")
    with executor.open_output(metrics) as stream:
        raw = stream.read(1024**2 + 1)
    assert len(raw) <= 1024**2 and sha256(raw).hexdigest() == metrics.sha256
    frame = pd.read_csv(io.BytesIO(raw), index_col=0)
    assert frame.index.equals(data.obs_names)
    assert frame.index[~frame["qc_pass"]].tolist() == ["bad-qc-cell"]
    return frame["qc_pass"].to_numpy(), metrics


def _staged_null(root, data, pooled, source_sha, mask, qc_metrics):
    retained = root / "qc-retained-original-counts.h5ad"
    data[mask, :].copy().write_h5ad(retained)
    spec = GeneralPseudobulkSpec(
        source_sha256=_file_sha(retained),
        assay="scrna",
        measurement="umi_counts",
        count_provenance=(
            f"Synthetic counts {source_sha} and QC mask {qc_metrics.sha256}."
        ),
        family_id="deterministic-null",
        regime="S-I",
        unit_column="donor",
        specimen_column="specimen",
        condition_column="condition",
        population_column="population",
        groups=("reference", "case"),
        population="P",
        technical_factors=("batch",),
        contrast=ContrastSpec(numerator="case", denominator="reference"),
        model=MountedModelSpec(comparison="coefficient", units="independent"),
    )
    staged = stage_general_pseudobulk(retained, spec, root / "null-pseudobulk")
    assert staged.pipeline is not None, staged.summary["refusals"]
    assert staged.summary["profile_count"] == 2 * _ARM_SIZE
    assert staged.summary["missing_profile_count"] == 0
    assert staged.summary["low_cell_profile_count"] == 0
    profiles = pd.read_csv(staged.directory / "metadata.csv")
    columns = [int(donor.removeprefix("donor-")) for donor in profiles["donor"]]
    assert len(set(columns)) == 2 * _ARM_SIZE
    with h5py.File(staged.directory / "expression.h5", "r") as capsule:
        np.testing.assert_array_equal(capsule["expression"][:], pooled[:, columns])
    return staged


def _fit_null(flow, executor, staged, qc_metrics):
    _, artifacts = register_general_pseudobulk(executor, staged)
    assert staged.pipeline is not None
    flow.record_basis("retained-qc-mask", "source", qc_metrics.sha256)
    for artifact in artifacts.values():
        flow.register_artifact(artifact, basis=("retained-qc-mask",))
    preflight = _insert_fit(flow, executor, staged.pipeline, artifacts)
    assert preflight["metrics"]["declared_unit_count"] == 2 * _ARM_SIZE
    actions = _research_actions(flow, executor, artifacts, preflight)
    fit = actions.perform_adaptive(flow, "fit")
    assert fit["status"] == "succeeded", fit["reason"]
    with pytest.raises(OperationBlocked):
        actions.perform_adaptive(flow, "fit")
    return fit


def _assert_null_statistics(table):
    assert table["gene_id"].to_pylist() == list(_GENES)
    rows = table.to_pylist()
    fitted = [row for row in rows if row["estimate_status"] == "estimated"]
    assert set(_GENES[:_VARYING]) <= {row["gene_id"] for row in fitted}
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in fitted)
    assert all(row["q_value"] > 0.05 for row in fitted)


def _assert_primary_and_sensitivity(executor, fit, staged, image):
    manifest = executor.runs / fit["attempt_id"] / "io.json"
    io_record = json.loads(manifest.read_bytes())
    tool = io_record["tool"]
    assert tool["oci"]["image_id"] == image and tool["oci"]["cleanup_confirmed"]
    assert tool["method"]["solver"] == "edger_v4"
    assert tool["method"]["test"] == "qlf"
    assert io_record["inputs"] == fit["consumed"]
    expression = next(pin for pin in fit["consumed"] if pin["port"] == "expression")
    assert expression["sha256"] == staged.summary["artifact_sha256"]["expression.h5"]
    primary = ArtifactVersion.model_validate(fit["outputs"]["result"])
    assert primary.sha256 == io_record["outputs"]["result"]["sha256"]
    with executor.open_output(primary) as stream:
        _assert_null_statistics(pq.read_table(stream))
    rank = tool["rank_sensitivity"]
    assert rank["status"] == "completed" and rank["hold"] is None
    assert rank["method"] == "van_elteren" and rank["n_units"] == 2 * _ARM_SIZE
    assert rank["primary_discoveries"] == rank["sensitivity_discoveries"] == 0
    assert rank["artifact_sha256"] != primary.sha256
    with executor._outputs.open(rank["artifact_sha256"]) as stream:
        _assert_null_statistics(pq.read_table(stream))


def _assert_single_dispatch(flow, executor, qc, failed, fit):
    dispatches = flow._read_projection(lambda db: flow._state(db).dispatches)
    dispatch_count = Counter(
        entry["envelope"]["step"]["step_id"] for entry in dispatches.values()
    )
    assert dispatch_count == {"resume-qc": 1, "resume-interrupted": 1, "fit": 1}
    assert flow.history("resume-qc") == (qc,)
    assert flow.history("resume-interrupted") == (failed,)
    assert flow.history("fit") == (fit,)
    for outcome in (qc, fit):
        entry = dispatches[outcome["attempt_id"]]
        token = entry["token"]
        saved = executor.load_receipt(
            entry["envelope"], attempt_id=token["attempt_id"],
        )
        assert saved is not None and saved.cleanup_state == "confirmed"
        state = flow.authority.attempt(token)
        assert state["execution_state"] == "succeeded"
        assert state["acceptance_state"] == "accepted"
    assert flow.reconcile_pending(executor) == []


@pytest.mark.parametrize(
    "crash_worker", [True, False], ids=["worker-exit", "lost-receipt"],
)
def test_l3_resume_preserves_exact_dispatches_and_real_null_control(
    workspace_tmp_path, monkeypatch, crash_worker,
):
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in single_cell_kernel_images():
        pytest.skip("Set DEVBENCH_RNA_KERNEL_TEST_IMAGE to a pinned combined image")
    root = workspace_tmp_path
    source, data, pooled = _null_source(root)
    source_sha = _file_sha(source)
    flow = make(root)
    executor = TierAExecutor(
        flow.ledger, allow_data_execution=True, mounted_image_id=image,
    )
    identities = list(zip(data.obs_names, data.obs["donor"], strict=True))
    raw = ArtifactVersion(
        version_id="original",
        sha256=executor.training.put_file(source),
        kind="counts",
        representation="raw_counts_h5ad",
        biological_unit="donor",
        unit_membership_sha256=digest(identities),
        measurement_history=("raw_counts",),
    )
    assert raw.sha256 == source_sha
    flow.register_artifact(raw)
    _step(
        flow, executor, "resume-qc", "scrna_batch_qc",
        {"batch_key": "batch", "min_genes": 10, "mad_limit": None},
        {"expression": ("original", None)},
    )
    qc_token, qc_seal = _qc_with_lost_acknowledgement(flow, executor, monkeypatch)
    flow, executor = _reopen(flow, image)
    (qc,) = flow.reconcile_pending(executor)
    assert qc["attempt_id"] == qc_token["attempt_id"]
    assert qc["status"] == "succeeded"

    interrupt = (
        _exit_during_preparation if crash_worker else _stop_before_preparation_receipt
    )
    interrupted = interrupt(flow, executor, monkeypatch)
    flow, executor = _reopen(flow, image)
    (failed,) = flow.reconcile_pending(executor)
    assert failed["attempt_id"] == interrupted["attempt_id"]
    assert failed["status"] == "failed" and failed["reason"] == TIER_A_INTERRUPTED
    assert failed["outputs"] == {}
    assert flow.authority.attempt(interrupted)["cleanup_state"] == "confirmed"
    assert executor.recover(flow, interrupted) == failed
    if crash_worker:
        _assert_owned_crash_cleanup(executor, interrupted, image)
    assert flow.history("resume-qc") == (qc,)
    assert _file_sha(executor.runs / qc_token["attempt_id"] / "receipt.json") == qc_seal

    mask, qc_metrics = _verified_mask(flow, executor, data)
    staged = _staged_null(root, data, pooled, source_sha, mask, qc_metrics)
    fit = _fit_null(flow, executor, staged, qc_metrics)
    _assert_primary_and_sensitivity(executor, fit, staged, image)
    _assert_single_dispatch(flow, executor, qc, failed, fit)
    assert _file_sha(source) == source_sha
