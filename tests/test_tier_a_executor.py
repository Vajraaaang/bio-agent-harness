"""Synthetic verification of Tier A dispatch and retained result custody."""

import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tests.test_investigation_invalidation import digest, make

pytestmark = pytest.mark.wal


def _fixture(
    tmp_path,
    *,
    input_format=None,
    format_port="expression",
    mounted_image_id=None,
    divergent=False,
    omnibus=None,
    step_id="bulk-fit",
    insert=True,
    case_expression_g1=(4, 5, 3, 7),
):
    assert importlib.util.find_spec("devbench.engine.tier_a_executor") is not None
    from devbench.core.contrast_design import (
        ContrastSpec,
        DesignSpec,
        MountedModelSpec,
        PipelineSpec,
    )
    from devbench.core.publication_families import FamilySpec
    from devbench.engine.provenance import canonical_json
    from devbench.engine.tier_a_executor import TierAExecutor
    from devbench.platform.adaptive_workflow import AdaptiveStep, ArtifactVersion

    flow = make(tmp_path)
    executor = TierAExecutor(
        flow.ledger,
        allow_data_execution=True,
        mounted_image_id=mounted_image_id,
    )
    measurement = "raw_counts" if omnibus == "lrt" else "continuous"
    samples = [f"s{i}" for i in range(100 if divergent or omnibus else 8)]
    genes = ("g1", "g2")
    expression = ",".join(["gene_id", *samples]) + "\n"
    case_values = ",".join(map(str, case_expression_g1))
    expression += f"g1,1,3,2,4,{case_values}\ng2,1,3,1,2,3,4,2,5\n"
    metadata = (
        "\n".join(
            [
                "sample_id,condition,donor_id,specimen_id,library_id",
                *(
                    f"{sample},{'control' if i < len(samples) // 2 else 'case'},"
                    f"d{i},b{i},l{i}"
                    for i, sample in enumerate(samples)
                ),
            ]
        )
        + "\n"
    )
    source_bytes = {"expression": expression.encode(), "metadata": metadata.encode()}
    expression_representation = "continuous"
    zarr_sha = None
    if input_format == "tsv":
        source_bytes[format_port] = source_bytes[format_port].replace(b",", b"\t")
    elif input_format == "csv.gz":
        source_bytes[format_port] = gzip.compress(source_bytes[format_port], mtime=0)
    elif input_format in {"h5", "zarr"}:
        import pandas as pd

        from devharness_core.storage import ChunkedExpressionStore

        binary = tmp_path / f"expression.{input_format}"
        if divergent:
            import numpy as np

            genes = tuple(f"g{i}" for i in range(20))
            data = np.tile(np.linspace(-1, 1, 100), (len(genes), 1))
            data[:, 50:] = -10
            for index in range(len(genes)):
                data[index, 50 : 85 - index] = 100 * (index + 1) ** 2
        elif omnibus:
            import numpy as np

            genes = tuple(f"g{i}" for i in range(96))
            data = np.random.default_rng(50).negative_binomial(
                15,
                0.35,
                (len(genes), len(samples)),
            )
            data[:12, len(samples) // 2 :] *= 4
        else:
            data = [[1, 3, 2, 4, *case_expression_g1], [1, 3, 1, 2, 3, 4, 2, 5]]
        matrix = pd.DataFrame(data, columns=samples, index=list(genes))
        if input_format == "h5":
            ChunkedExpressionStore.from_dataframe_to_hdf5(matrix, binary).close()
            source_bytes["expression"] = binary.read_bytes()
        else:
            ChunkedExpressionStore.from_dataframe_to_zarr(matrix, binary).close()
            zarr_sha = executor.mounted_zarr.put(binary)
        expression_representation = f"{measurement}_{input_format}"
    family = FamilySpec(
        family_id="synthetic-family",
        population="bulk",
        numerator="case",
        denominator="control",
        feature_ids=genes,
    )
    spec = PipelineSpec(
        format=(
            "devbench.tier-a-bulk-pipeline/2"
            if input_format in {"h5", "zarr"}
            else "devbench.tier-a-bulk-pipeline/1"
        ),
        design=DesignSpec("B-I", "case", "control"),
        contrast=ContrastSpec(numerator="case", denominator="control"),
        family=family,
        measurement=measurement,
        biological_unit="donor",
        condition_column="condition",
        population_unit_column="donor_id",
        specimen_column="specimen_id",
        library_column="library_id",
        expression_sha256=zarr_sha or executor.training.put(source_bytes["expression"]),
        metadata_sha256=executor.training.put(source_bytes["metadata"]),
        family_sha256=executor.training.put(
            canonical_json(family.model_dump(mode="json"))
        ),
        mounted_model=(
            MountedModelSpec(comparison=omnibus, units="independent")
            if omnibus
            else None
        ),
    )
    inputs = {}
    for port, kind, representation, identity in (
        (
            "expression",
            "counts" if measurement == "raw_counts" else "continuous_matrix",
            expression_representation,
            spec.expression_sha256,
        ),
        ("metadata", "table", "metadata_csv", spec.metadata_sha256),
    ):
        version = ArtifactVersion(
            version_id=port,
            sha256=identity,
            kind=kind,
            representation=representation,
            biological_unit="donor",
            unit_membership_sha256=digest("synthetic-membership"),
            permitted_uses=("checked_observation", "admitted_research_inference"),
            measurement_history=("raw_counts",) if kind == "counts" else (),
        )
        flow.register_artifact(version)
        inputs[port] = version.model_dump(mode="json")
    step = AdaptiveStep(
        step_id=step_id,
        branch_id="bulk",
        operation_id="bulk_condition_contrast",
        profile={
            "profile_id": "synthetic-tier-a",
            "version": 1,
            "operations": [
                {
                    "operation_id": "bulk_condition_contrast",
                    "output_kinds": ["table"],
                    "max_memory_mb": 2048,
                    "timeout_seconds": 60,
                }
            ],
            "input_representations": [expression_representation, "metadata_csv"],
            "biological_units": ["donor"],
            "design_scope": ["B-I"],
            "permitted_claims": ["admitted_research_inference"],
        },
        environment_sha256=executor.environment_sha256,
        parameters_sha256=executor.register_pipeline(spec),
        inputs=[
            {
                "port": {"name": port, "artifact_kind": row["kind"]},
                "source_id": row["version_id"],
            }
            for port, row in inputs.items()
        ],
        outputs=[{"name": "result", "artifact_kind": "table"}],
        purpose="admitted_research_inference",
        regime="B-I",
        primary=True,
    )
    if insert:
        flow.insert(step)
    envelope = {
        "mode": "adaptive/1",
        "lineage_id": flow.lineage_id,
        "actor": "fixture",
        "generation": 1,
        "environment_sha256": executor.environment_sha256,
        "design_review": {"research_admission": {"fixture": "trusted gate fixture"}},
        "step": step.model_dump(mode="json", exclude={"oci_method_version_id"}),
        "inputs": inputs,
        "basis": {},
        "pins": [
            {
                "port": name,
                **{key: row[key] for key in ("version_id", "sha256", "graph_sha256")},
            }
            for name, row in sorted(inputs.items())
        ],
    }
    return flow, executor, envelope


def _submit(flow, executor, envelope, *, legacy_dispatch=False):
    from devbench.platform.adaptive_workflow import request_digest

    execution = flow.authority.execution(flow.lineage_id)

    def retain_dispatch(db, value):
        record = {"token": value, "envelope": envelope}
        if not legacy_dispatch:
            record["worker_custody_format"] = "devbench.tier-a-worker-custody/1"
        flow._event(db, "dispatch", record)

    token = flow.authority.reserve_attempt(
        flow.lineage_id,
        publication_unit="synthetic-fit",
        request_sha256=request_digest(envelope),
        environment_sha256=envelope["environment_sha256"],
        backend_id=executor.backend_id,
        expected_epoch=execution["epoch"],
        binding_id=execution["binding_id"],
        expected_revision=execution["revision"],
        _adaptive_reservation=retain_dispatch,
    )
    receipts = []

    def execute(_):
        receipt = executor.run(envelope)
        receipts.append(receipt)
        return receipt.job_id

    # The backend unit test uses an authorized synthetic gate. Workbench and
    # WorkflowActions tests cover the real diagnostic and independent reviews.
    flow.authority.dispatch_attempt(
        token,
        execute,
        _adaptive_guard=lambda db: executor._record_custody(db, token, "prepared"),
    )
    return token, receipts[0]


def _require_mounted_image() -> str:
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if not image:
        pytest.skip("Set the exact local RNA kernel image ID")
    return image


@pytest.mark.parametrize(
    ("runtime", "file_format"),
    [
        ("local", "h5"),
        ("local", "zarr"),
        ("oci", "h5"),
    ],
)
def test_mounted_tier_a_artifact_and_ledger_receipt(
    workspace_tmp_path, runtime, file_format
):
    import pyarrow.parquet as pq

    image = _require_mounted_image() if runtime == "oci" else None
    flow, executor, envelope = _fixture(
        workspace_tmp_path,
        input_format=file_format,
        mounted_image_id=image,
    )
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded", receipt.reason
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    with executor.open_output(receipt.outputs["result"]) as stream:
        table = pq.read_table(stream)
    assert table["gene_id"].to_pylist() == ["g1", "g2"]
    assert table["effect"].to_pylist() == pytest.approx([2.25, 1.75])
    assert not (executor.runs / token["attempt_id"] / ".work").exists()
    if runtime == "oci":
        saved = json.loads(
            (executor.runs / token["attempt_id"] / "io.json").read_bytes()
        )
        assert saved["tool"]["oci"]["cleanup_confirmed"] is True
        assert saved["tool"]["oci"]["image_id"] == image


@pytest.mark.parametrize("runtime", ["local", "oci"])
def test_mounted_rank_divergence_seals_evidence_and_holds_step(
    workspace_tmp_path,
    runtime,
):
    image = _require_mounted_image() if runtime == "oci" else None
    flow, executor, envelope = _fixture(
        workspace_tmp_path,
        input_format="h5",
        mounted_image_id=image,
        divergent=True,
    )
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "failed" and receipt.reason == "Q-DESIGN"
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    result = flow.complete(token, receipt, verify=executor.verify)
    assert result["reason"] == "Q-DESIGN" and result["outputs"] == {}
    hold = flow._read_projection(lambda db: flow._state(db).holds["bulk-fit"])
    assert hold["evidence_sha256"] == result["receipt_sha256"]
    saved = json.loads((executor.runs / token["attempt_id"] / "io.json").read_bytes())
    rank = saved["tool"]["rank_sensitivity"]
    assert rank["status"] == "completed" and rank["n_units"] == 100
    assert rank["method"] == "wilcoxon" and rank["effect_spearman"] < 0
    digests = (rank["artifact_sha256"], saved["tool"]["held_primary_artifact_sha256"])
    for identity in digests:
        with executor._outputs.open(identity):
            pass


@pytest.mark.parametrize("comparison", ["nested", "lrt"])
def test_mounted_generalized_omnibus_publishes_without_rank(
    workspace_tmp_path,
    comparison,
):
    import pyarrow.parquet as pq

    image = _require_mounted_image()
    flow, executor, envelope = _fixture(
        workspace_tmp_path,
        input_format="h5",
        mounted_image_id=image,
        omnibus=comparison,
    )
    plan_sha = envelope["step"]["parameters_sha256"]
    preflight = executor.mounted_design_preflight(plan_sha)
    assert preflight["family"] == "Q-DESIGN"
    assert preflight["metrics"]["reduced_columns"] == 1
    assert preflight["scientific_validity_established"] is False
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded", receipt.reason
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    saved = json.loads((executor.runs / token["attempt_id"] / "io.json").read_bytes())
    rank = saved["tool"]["rank_sensitivity"]
    assert rank["status"] == "not_applicable" and rank["n_units"] == 100
    assert rank["reason"] and not rank.get("hold") and "artifact_sha256" not in rank
    assert "held_primary_artifact_sha256" not in saved["tool"]
    with executor.open_output(receipt.outputs["result"]) as stream:
        table = pq.read_table(stream)
    assert table["effect"].null_count == table.num_rows == 96
    fitted = [
        (p, q)
        for p, q in zip(
            table["p_value"].to_pylist(), table["q_value"].to_pylist(), strict=True
        )
        if p is not None
    ]
    assert len(fitted) >= 80
    assert all(0 <= p <= q <= 1 for p, q in fitted)


@pytest.mark.parametrize(
    "module_name",
    [
        "devbench.core.family_fitting",
        "devbench.core.estimators",
        "devbench.core.design",
        "devbench.core.observed_data",
        "devbench.core.bulk_fit_evidence",
        "devbench.core.publication_families",
        "devbench.study.biology_tool_io",
        "devbench.study.biology_input_limits",
        "devbench.study.biology_answers",
        "rna_kernels.normalization",
        "rna_kernels.units",
        "devharness_core.units",
        "devharness_server.dispatcher",
        "devharness_server.tools_stat",
        "devharness_core.watchdog",
    ],
)
def test_tier_a_transitive_source_changes_invalidate_identity(
    tmp_path,
    monkeypatch,
    module_name,
):
    from devbench.engine import tier_a_executor

    flow, executor, envelope = _fixture(tmp_path)
    package, _, filename = module_name.rpartition(".")
    package_dir = Path(importlib.util.find_spec(package).origin).parent
    source = (package_dir / (filename + ".py")).resolve()
    read = tier_a_executor.read_bounded_file

    def changed(path, limit):
        raw = read(path, limit)
        return raw + b"\n# synthetic source change" if path == source else raw

    monkeypatch.setattr(tier_a_executor, "read_bounded_file", changed)
    updated = tier_a_executor.TierAExecutor(flow.ledger)
    assert updated.code_sha256 != executor.code_sha256
    assert updated.environment_sha256 != executor.environment_sha256
    with pytest.raises(PermissionError, match="code or runtime identity changed"):
        executor.validate(envelope)
    with pytest.raises(PermissionError, match="admitted registered research contrast"):
        updated.validate(envelope)


@pytest.mark.parametrize("package", ["pydeseq2", "pydantic"])
def test_tier_a_runtime_version_changes_invalidate_environment(
    tmp_path,
    monkeypatch,
    package,
):
    from devbench.engine import tier_a_executor

    flow, executor, envelope = _fixture(tmp_path)
    version = tier_a_executor.version

    def changed(name):
        return "0.0.0" if name == package else version(name)

    monkeypatch.setattr(tier_a_executor, "version", changed)
    updated = tier_a_executor.TierAExecutor(flow.ledger)
    assert updated.code_sha256 == executor.code_sha256
    assert updated.environment_sha256 != executor.environment_sha256
    with pytest.raises(PermissionError, match="code or runtime identity changed"):
        executor.validate(envelope)
    with pytest.raises(PermissionError, match="admitted registered research contrast"):
        updated.validate(envelope)


@pytest.mark.parametrize("input_format", ["tsv", "csv.gz"])
@pytest.mark.parametrize("port", ["expression", "metadata"])
def test_tier_a_never_stages_non_csv_bytes_as_csv(
    tmp_path,
    monkeypatch,
    input_format,
    port,
):
    from devbench.engine import tier_a_executor

    flow, executor, envelope = _fixture(
        tmp_path,
        input_format=input_format,
        format_port=port,
    )

    def unexpected_worker(*args, **kwargs):
        pytest.fail("Non-CSV bytes reached the CSV-only registered worker")

    monkeypatch.setattr(tier_a_executor, "run_sandboxed", unexpected_worker)
    with pytest.raises(ValueError, match="requires uncompressed CSV"):
        _submit(flow, executor, envelope)


def test_tier_a_fit_retains_hash_bound_parquet_and_replay_receipt(tmp_path):
    import pyarrow.parquet as pq

    flow, executor, envelope = _fixture(tmp_path)
    token, receipt = _submit(flow, executor, envelope)
    assert receipt.status == "succeeded"
    assert receipt.job_id == token["attempt_id"]
    artifact = receipt.outputs["result"]
    assert artifact.representation == "parquet"
    assert artifact.sha256 == hashlib.sha256(executor.load_output(artifact)).hexdigest()
    with executor.open_output(artifact) as source:
        table = pq.read_table(source)
    metadata = json.loads(table.schema.metadata[b"devbench.result"])
    rows = table.to_pylist()
    assert metadata["row_count"] == 2
    assert metadata["claim_scope"] == "admitted_research_inference"
    assert [row["gene_id"] for row in rows] == ["g1", "g2"]
    assert all(0 <= row["p_value"] <= row["q_value"] <= 1 for row in rows)
    assert executor.verify(envelope, receipt)
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
    altered = receipt.model_copy(update={"wall_millis": receipt.wall_millis + 1})
    assert not executor.verify(envelope, altered)
    imported = artifact.model_copy(
        update={"sha256": executor.training.put(b"input only")}
    )
    with pytest.raises(PermissionError, match="retained Tier A artifact"):
        executor.load_output(imported)


def test_tier_a_rejects_missing_submission_and_repeated_launch(tmp_path):
    flow, executor, envelope = _fixture(tmp_path)
    with pytest.raises(PermissionError, match="submission intent"):
        executor.run(envelope)
    _submit(flow, executor, envelope)
    with pytest.raises(PermissionError, match="submission intent"):
        executor.run(envelope)


@pytest.mark.parametrize("protected", ["expression", "family", "plan"])
def test_tier_a_verification_rechecks_protected_digests(tmp_path, protected):
    from devbench.engine.partition_authority import PartitionAuthority
    from devbench.engine.workflow_operations import OperationConflict

    flow, executor, envelope = _fixture(tmp_path)
    token, receipt = _submit(flow, executor, envelope)
    assert executor.verify(envelope, receipt)
    plan = envelope["step"]["parameters_sha256"]
    with executor._parameters.open(plan) as stream:
        family = json.load(stream)["family_sha256"]
    sha = {
        "expression": envelope["inputs"]["expression"]["sha256"],
        "family": family,
        "plan": plan,
    }[protected]
    PartitionAuthority(flow.ledger, digest("synthetic embargo")).protect_outputs((sha,))
    assert not executor.verify(envelope, receipt)
    with pytest.raises(OperationConflict, match="receipt failed verification"):
        executor.load_receipt(envelope, attempt_id=token["attempt_id"])


def test_tier_a_interrupted_record_write_leaves_no_published_candidate(
    tmp_path, monkeypatch
):
    from devbench.engine import tier_a_executor

    destination = tmp_path / "receipt.json"

    def interrupt(_):
        raise OSError("synthetic record fsync failure")

    monkeypatch.setattr(tier_a_executor.os, "fsync", interrupt)
    with pytest.raises(OSError, match="synthetic record fsync failure"):
        tier_a_executor._write_record(destination, {"receipt": "fixture"})
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


def test_tier_a_record_publication_keeps_the_original_bytes(tmp_path):
    from devbench.engine.provenance import canonical_json
    from devbench.engine.tier_a_executor import _write_record

    destination = tmp_path / "io.json"
    original = {"receipt": "retained"}
    _write_record(destination, original)
    with pytest.raises(FileExistsError):
        _write_record(destination, {"receipt": "replacement"})
    assert destination.read_bytes() == canonical_json(original)
    assert destination.stat().st_mode & 0o777 == 0o400
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize("corruption", ["partial", "symlink"])
def test_tier_a_load_receipt_blocks_unverified_candidate(tmp_path, corruption):
    from devbench.engine.workflow_operations import OperationBlocked

    flow, executor, envelope = _fixture(tmp_path)
    token, receipt = _submit(flow, executor, envelope)
    destination = executor._directory(receipt.job_id) / "receipt.json"
    if corruption == "partial":
        destination.chmod(0o600)
        destination.write_bytes(b'{"job_id":')
    else:
        target = tmp_path / "synthetic-receipt.json"
        target.write_bytes(destination.read_bytes())
        destination.unlink()
        destination.symlink_to(target)
    with pytest.raises(OperationBlocked) as error:
        executor.load_receipt(envelope, attempt_id=token["attempt_id"])
    assert error.value.state == "adaptive_tier_a_receipt_unverified"
    assert flow.authority.attempt(token)["execution_state"] == "submission_uncertain"


@pytest.mark.parametrize("corruption", ["missing_phases", "wrong_launch_digest"])
def test_tier_a_receipt_with_unverified_wal_proof_stays_fenced(
    tmp_path,
    monkeypatch,
    corruption,
):
    from devbench.engine.workflow_operations import OperationBlocked

    flow, executor, envelope = _fixture(tmp_path)
    token, receipt = _submit(flow, executor, envelope)
    prepared, launched, stopped = executor._read_custody(token)
    invalid = (
        (None, None, None)
        if corruption == "missing_phases"
        else (prepared, launched, {**stopped, "launch_sha256": digest("wrong launch")})
    )
    monkeypatch.setattr(executor, "_read_custody", lambda _: invalid)

    assert not executor.verify(envelope, receipt)
    with pytest.raises(OperationBlocked, match="adaptive_tier_a_receipt_unverified"):
        executor.load_receipt(envelope, attempt_id=token["attempt_id"])
    assert flow.reconcile_pending(executor) == []
    (attempt,) = flow.authority.attempts(flow.lineage_id)
    assert attempt["execution_state"] == "submission_uncertain"
    assert not flow.history(envelope["step"]["step_id"])


def test_tier_a_historical_receipt_replays_under_legacy_retention(
    tmp_path,
    monkeypatch,
):
    flow, executor, envelope = _fixture(tmp_path)
    token, receipt = _submit(flow, executor, envelope, legacy_dispatch=True)
    monkeypatch.setattr(executor, "_read_custody", lambda _: (None, None, None))

    assert executor.verify(envelope, receipt)
    assert executor.load_receipt(envelope, attempt_id=token["attempt_id"]) == receipt
