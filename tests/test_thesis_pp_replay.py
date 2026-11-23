"""Replay a qualified population pair from its original QC and label inputs."""

import json
import os

import pytest

from devbench.analysis.thesis_replay import replay_thesis_package
from devbench.analysis.thesis_synthesis import ThesisSynthesisSpec, write_thesis_package
from devbench.engine.partition_authority import PartitionAuthority
from devbench.engine.tier_a_executor import TierAExecutor
from devbench.engine.workflow_operations import OperationBlocked, OperationConflict
from devbench.platform.scrna_general_pseudobulk import (
    QC_LABEL_MAP,
    QC_LABEL_QUALIFICATION,
    register_general_pseudobulk,
    stage_general_pseudobulk,
)
from devbench.platform.scrna_qc_handoff import accepted_qc_selection, current_qc_parents
from devbench.platform.workbench_thesis import _retain_pseudobulk_inputs
from devbench.platform.workflow_actions import WorkflowActions
from devharness_controlled.native_contracts import single_cell_kernel_images
from devharness_core.provenance import copy_artifact
from rna_kernels.canonical import canonical_json
from tests.test_investigation_invalidation import digest, make
from tests.test_p3_general_pseudobulk import _labels, _population_pair_spec, _source
from tests.test_thesis_bulk_acceptance import _insert_fit, _research_actions
from tests.test_thesis_single_cell_acceptance import _qc_parameters, _register, _sha
from tests.test_tier_a_scrna import _run_stage

pytestmark = pytest.mark.wal


def _fit_population_pairs(root, image):
    source, obs, _, _, _ = _source(root, paired=True, mitochondrial=True)
    evidence = _labels(root, source, obs)
    flow = make(root)
    executor = TierAExecutor(
        flow.ledger, mounted_image_id=image, allow_data_execution=True,
    )
    actions = WorkflowActions(
        None, flow.ledger, actor="operator", selector=None,
        tier_a_executor=executor, allow_data_execution=True,
    )
    count = _register(
        flow, executor, name="original", source=source,
        role=("scrna_batch_qc", "expression"), membership=digest("paired membership"),
    )
    _run_stage(
        flow, executor, actions, "pp-qc", "scrna_batch_qc", _qc_parameters(),
        {"expression": (count.version_id, None)},
    )
    selection = accepted_qc_selection(
        flow, executor, "pp-qc", source_sha256=count.sha256,
    )
    staged = stage_general_pseudobulk(
        source, _population_pair_spec(source), root / "pp-capsule",
        label_evidence=evidence, qc_selection=selection,
    )
    assert staged.pipeline is not None, staged.summary["refusals"]
    _, inputs = register_general_pseudobulk(executor, staged, workflow=flow)
    with pytest.raises(OperationConflict, match="identity differs from history"):
        with flow.authority._execution_tx(flow.lineage_id) as db:
            parents = current_qc_parents(flow, selection.binding, connection=db)
            flow.register_artifact(inputs["expression"], parents=parents, connection=db)
            _retain_pseudobulk_inputs(flow, db, staged, inputs)
    with flow.authority._execution_tx(flow.lineage_id) as db:
        _retain_pseudobulk_inputs(flow, db, staged, inputs)
    preflight = _insert_fit(flow, executor, staged.pipeline, inputs)
    reviewed = _research_actions(flow, executor, inputs, preflight)
    result = reviewed.perform_adaptive(flow, "fit")
    assert result["status"] == "succeeded", result["reason"]
    return flow, executor, count.sha256, staged


def _write_package(root, flow, executor):
    schema = root / "pp-answer-schema.json"
    schema.write_bytes(canonical_json({
        "type": "object",
        "required": ["gene_statistics"],
        "properties": {"gene_statistics": {"type": "array"}},
        "additionalProperties": False,
    }))
    spec = ThesisSynthesisSpec.model_validate({
        "question": "Do the qualified populations differ in the paired specimens?",
        "outcome": "completed",
        "expected_revision": flow.status()["revision"],
        "answer_schema_sha256": _sha(schema),
        "fields": {
            "gene_statistics": {
                "step_id": "fit", "port": "result",
                "projection": "tier_a_gene_statistics",
            },
        },
    })
    package = root / "pp-thesis"
    seal = write_thesis_package(
        package, flow, spec, answer_schema=schema, open_artifact=executor.open_output,
        tier_a_executor=executor,
    )
    return package, seal["root_sha256"]


def _copy_input(executor, destination, identity):
    copied = copy_artifact(
        executor.training.root / identity, destination / identity,
        max_bytes=16 * 1024**2, max_entries=1,
    )
    assert copied["sha256"] == identity


def _replay_with_label_checks(root, executor, package, expected_root, raw, labels):
    inputs = root / "pp-original-inputs"
    inputs.mkdir(mode=0o700)
    for identity in (raw, labels[QC_LABEL_QUALIFICATION]):
        _copy_input(executor, inputs, identity)
    label_digest = labels[QC_LABEL_MAP]
    for name, message in (
        ("missing", "Missing qualified label replay input"),
        ("changed", "Qualified label replay input differs"),
    ):
        destination = root / ("pp-replay-" + name)
        with pytest.raises(ValueError, match=message):
            replay_thesis_package(
                package, expected_root, output=destination,
                input_root=inputs, allow_data_execution=True,
            )
        assert not any(destination.iterdir())
        if name == "missing":
            (inputs / label_digest).write_bytes(b"{}")
    (inputs / label_digest).unlink()
    _copy_input(executor, inputs, label_digest)
    assert {item.name for item in inputs.iterdir()} == {raw, *labels.values()}
    result = replay_thesis_package(
        package, expected_root, output=root / "pp-replay",
        input_root=inputs, allow_data_execution=True,
    )
    assert result["exact_genes_parquet_match"] is True
    assert result["exact_single_cell_artifacts_match"] is True
    assert result["exact_qc_derived_inputs_match"] is True
    assert not any((root / "pp-replay").rglob(QC_LABEL_MAP))
    assert not any((root / "pp-replay").rglob(QC_LABEL_QUALIFICATION))


def _check_label_embargo(flow, identity):
    cohort = digest("new protected label cohort")
    authority = PartitionAuthority(flow.ledger, cohort)
    assert authority.seal({
        "cohort_sha256": cohort, "source_sha256": identity,
        "inventory_sha256": digest("protection fixture"),
        "count_path": "X",
        "training": {"sha256": digest("train"), "units_sha256": digest("train units")},
        "evaluation": {"sha256": digest("eval"), "units_sha256": digest("eval units")},
    }) == "compromised"
    with pytest.raises(OperationBlocked, match="protected_partition_delivery_denied"):
        flow.current_use("fit", "result", purpose="export")


def test_qualified_population_pairs_replay_from_originals(workspace_tmp_path):
    image = os.environ.get("DEVBENCH_RNA_KERNEL_TEST_IMAGE")
    if image not in single_cell_kernel_images():
        pytest.skip("Requires a pinned combined single-cell image")
    root = workspace_tmp_path
    flow, executor, raw, staged = _fit_population_pairs(root, image)
    qualification = staged.summary["label_qualification"]
    labels = {
        QC_LABEL_MAP: qualification["labels_sha256"],
        QC_LABEL_QUALIFICATION: qualification["qualification_sha256"],
    }
    package, expected_root = _write_package(root, flow, executor)
    index = json.loads((package / "replay_bundle/index.json").read_bytes())
    recipe, = index["qc_derivations"]
    assert recipe["label_inputs"] == labels
    assert not any(package.rglob(QC_LABEL_MAP))
    assert not any(package.rglob(QC_LABEL_QUALIFICATION))
    provenance = flow.disclosure_provenance("fit", "result")
    assert {qualification["source_sha256"], *labels.values()} <= set(
        provenance["dependencies"]
    )
    _replay_with_label_checks(root, executor, package, expected_root, raw, labels)
    _check_label_embargo(flow, qualification["source_sha256"])
