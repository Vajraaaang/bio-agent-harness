"""Small local software fixtures, not public-cohort analyses or agent results."""
import json
import os
from pathlib import Path
import tempfile

import pytest

from devbench.study.public_study import default_public_plan, _materialize, verify_public_task
from devbench.study.public_tools import analyze_public_tool, AUDIT, SUMMARY, NORMALIZE
from devbench.study.public_grading import grade_public_answer


@pytest.fixture
def fixtures():
    if os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1":
        pytest.skip("Small numerical fixture opt-in is required")
    import numpy as np
    root = Path(__file__).resolve().parents[1] / "tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix="public-fixtures-") as temporary:
        directory = Path(temporary)
        plan = default_public_plan(endogenous_genes=32)
        genes = ["gene1", "gene2", "gene3", "gene4"]
        values = np.arange(36, dtype=float).reshape(4, 9)
        metadata = [{"sample_id": f"sample{i}", "stage": f"E{10 + i // 3}", "organ": "Brain", "batch": "unknown",
                     "specimen_label": f"sample{i}", "independent_animal_id": ""} for i in range(9)]
        reference = {"stage_labels": ["E10", "E11", "E12"], "gene_ids": genes,
                     "means": values.reshape(4, 3, 3).mean(axis=2).tolist()}
        development = _materialize(plan, "developmental_general", directory / "dev", values, genes, metadata,
                                   {}, reference, {"fixture": True}, allow_data_execution=True)
        mixtures = list("AABBCCDD")
        depth = np.array([1, 2, 2, 4, 1, 2, 2, 4])
        nominal = np.array([[100, 100, 200, 200, 125, 125, 175, 175],
                            [200, 200, 100, 100, 175, 175, 125, 125]])
        values = np.vstack([nominal, nominal * 2, np.full((32, 8), 100)]) * depth
        genes = [f"g{i}" for i in range(len(values))]
        metadata = [{"sample_id": f"library{i}", "library_id": f"library{i}", "mixture": group,
                     "site": "fixture", "platform": "fixture", "replicate_kind": "technical", "independent_patient_id": ""}
                    for i, group in enumerate(mixtures)]
        calibration = {"expected_inputs": {genes[j]: {row["sample_id"]: int(nominal[j, i]) for i, row in enumerate(metadata)}
                                            for j in range(2)}}
        reference = {"evaluation_ratios_A_over_B": {"g2": .5, "g3": 2.},
                     "eligible_evaluation_controls": ["g2", "g3"], "endogenous_gene_ids": genes[4:]}
        seqc = _materialize(plan, "seqc_controls", directory / "seqc", values, genes, metadata,
                            calibration, reference, {"fixture": True}, allow_data_execution=True)
        yield plan, directory, development, seqc


def _answer(bundle, tool, **options):
    result = analyze_public_tool(tool, Path(bundle.root) / "inputs", options=options, allow_data_execution=True)
    return {key: result[key] for key in ("task_id", "input_sha256", "payload", "limitations")}


def _grade(plan, bundle, answer):
    reference = json.loads((Path(bundle.root) / "private/reference.json").read_text())
    return grade_public_answer(answer, input_root=Path(bundle.root) / "inputs", reference=reference,
                               plan=plan, allow_data_execution=True)


def test_all_execution_entrypoints_deny_before_reading():
    from devbench.study.public_study import prepare_public_task
    from devbench.study.public_io import load_public_inputs
    for invoke in (lambda: load_public_inputs("/does-not-exist"), lambda: prepare_public_task(None, None, None, None),
                   lambda: _materialize(None, None, None, None, None, None, None, None, None),
                   lambda: analyze_public_tool(AUDIT, "/does-not-exist"),
                   lambda: grade_public_answer(None, input_root=None, reference=None, plan=None)):
        with pytest.raises(PermissionError):
            invoke()


def test_observations_keep_limited_truth_and_reject_wrong_results(fixtures):
    plan, _, development, _ = fixtures
    assert verify_public_task(development, plan, allow_data_execution=True) == development
    answer = _answer(development, SUMMARY)
    grade = _grade(plan, development, answer)
    assert grade["reference_task_success"] is True
    assert grade["complete_biological_correctness"] is None
    assert grade["metrics"]["independent_animal_count"] is None
    answer["payload"]["curves"][0]["means"][0] += 1
    assert _grade(plan, development, answer)["reference_task_success"] is False
    answer["payload"]["independent_animal_count"] = 9
    assert _grade(plan, development, answer)["errors"] == ["invalid_or_missing_answer"]


def test_seqc_factor_scale_and_fixed_held_out_controls(fixtures):
    plan, _, _, seqc = fixtures
    assert verify_public_task(seqc, plan, allow_data_execution=True) == seqc
    answer = _answer(seqc, NORMALIZE)
    grade = _grade(plan, seqc, answer)
    assert grade["reference_task_success"] is True
    assert grade["metrics"]["eligible_evaluation_controls"] == 2
    assert grade["metrics"]["median_absolute_control_log2_error"] < 1e-12
    for row in answer["payload"]["factors"]:
        row["factor"] *= 10
    assert _grade(plan, seqc, answer)["reference_task_success"] is True
    for row in answer["payload"]["factors"]:
        if row["library_id"] in {"library0", "library1"}:
            row["factor"] *= 16
    assert _grade(plan, seqc, answer)["reference_task_success"] is False
    assert _grade(plan, seqc, None)["reference_task_success"] is False
    reference = json.loads((Path(seqc.root) / "private/reference.json").read_text())
    reference["eligible_evaluation_controls"] = []
    assert grade_public_answer(None, input_root=Path(seqc.root) / "inputs", reference=reference,
                              plan=plan, allow_data_execution=True)["reference_task_success"] is None


@pytest.mark.parametrize("method", ["rle", "library_total"])
def test_other_public_factor_methods_return_complete_finite_family(fixtures, method):
    _, _, _, seqc = fixtures
    answer = _answer(seqc, NORMALIZE, method=method)
    assert len(answer["payload"]["factors"]) == 8
    assert all(row["factor"] > 0 for row in answer["payload"]["factors"])


def test_input_mutation_and_missing_axis_are_detected(fixtures):
    plan, _, development, _ = fixtures
    from devbench.study.public_io import load_public_inputs
    path = Path(development.root) / "inputs/metadata.csv"
    path.chmod(0o600)
    path.write_text(path.read_text().replace("sample0", "wrong0"))
    path.chmod(0o400)
    with pytest.raises(ValueError):
        verify_public_task(development, plan, allow_data_execution=True)
    with pytest.raises(ValueError, match="align"):
        load_public_inputs(path.parent, allow_data_execution=True)


def test_public_broker_real_execution_receipts_and_answer_binding(fixtures):
    plan, directory, _, seqc = fixtures
    from devbench.study.broker import BrokerPolicy
    from devbench.study.evidence import verify_receipts
    from devbench.study.public_tools import PublicToolBroker, verify_public_workflow
    task = json.loads((Path(seqc.root) / "inputs/task_inputs.json").read_text())
    task["input_sha256"] = seqc.input_sha256
    inputs = Path(seqc.root) / "inputs"
    policy = BrokerPolicy(condition="enforced", input_sha256=seqc.input_sha256, implementation_sha256="b"*64,
                          measurement_type="raw_counts", gene_ids=tuple(task["gene_ids"]), timeout_sec=30,
                          population_inference_allowed=False)
    broker = PublicToolBroker(policy, {str(inputs/"counts.zarr"): str(inputs/"counts.zarr")}, directory/"receipts",
                              public_task=task, public_input_root=inputs, allow_data_execution=True)
    assert broker.execute(NORMALIZE, {})["status"] == "failed"
    assert broker.execute(AUDIT, {})["status"] == "succeeded"
    modeled = broker.execute(NORMALIZE, {})
    assert modeled["status"] == "succeeded", modeled["error"]
    root_receipt = broker.close()
    evidence = verify_receipts(directory/"receipts", expected_receipt=root_receipt,
        expected_input_sha256=seqc.input_sha256, expected_implementation_sha256="b"*64, allow_evidence_read=True)
    answer = {key: modeled["result"][key] for key in ("task_id", "input_sha256", "payload", "limitations")}
    answer["tool_evidence_ids"] = [modeled["receipt_id"]]
    assert verify_public_workflow(answer, evidence, task, allow_evidence_read=True)["workflow_passed"]
    answer["payload"]["factors"][0]["factor"] *= 2
    assert not verify_public_workflow(answer, evidence, task, allow_evidence_read=True)["workflow_passed"]


def test_staged_assets_and_tool_menus_are_equal_except_condition_guidance(fixtures):
    from devbench.study.public_execution import _stage_public_inputs
    from devbench.study.public_io import input_inventory
    _, directory, _, bundle = fixtures
    task = json.loads((Path(bundle.root) / "inputs/task_inputs.json").read_text())
    inventories = []
    for condition in ("baseline", "optional", "enforced"):
        workspace = directory / condition
        workspace.mkdir()
        public, _, _, _ = _stage_public_inputs(bundle, workspace, condition, task, allow_data_execution=True)
        assert public["input_sha256"] == bundle.input_sha256
        inventories.append(input_inventory(workspace / "inputs"))
        assert not (workspace / "private").exists()
        assert (workspace / "tool_menu.json").exists() == (condition != "baseline")
        assert not any("reference.json" in str(path) for path in workspace.rglob("*"))
    assert inventories[0] == inventories[1] == inventories[2]


def test_seqc_preparation_aggregates_lanes_and_blinds_held_out_controls(fixtures, monkeypatch):
    import csv
    import gzip
    from devbench.study import public_tracks
    from devbench.study.public_study import prepare_public_task
    from devbench.study.public_io import load_public_inputs
    _, directory, _, _ = fixtures
    plan = default_public_plan(datasets=("seqc_controls",), endogenous_genes=32, calibration_per_ratio_group=2)
    cache = directory / "cache" / "seqc"
    cache.mkdir(parents=True)
    genes = [f"ERCC-{i:05d}" for i in range(12)] + [f"gene{i}" for i in range(32)]
    samples = [f"SEQC_ILM_NYG_{group}_{library}_L{lane}_barcode_flowcell" for group in "ABCD" for library in (1, 2) for lane in (1, 2)]
    counts = cache / "GSE47774_SEQC_ILM_NYG_all_counts.txt.gz"
    with gzip.open(counts, "wt", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["GeneSymbol", *samples])
        for gene in genes:
            writer.writerow([gene, *([10] * len(samples))])
    controls = cache / "ERCC_Controls_Analysis.txt"
    with controls.open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["ERCC ID", "subgroup", "concentration in Mix 1 (attomoles/ul)", "concentration in Mix 2 (attomoles/ul)"])
        for i, gene in enumerate(genes[:12]):
            writer.writerow([gene, str(i // 3), 100, 100])
    info = {"counts_path": str(counts), "source_sha256": public_tracks._hash(counts, 20_000_000),
            "control_reference_sha256": public_tracks._hash(controls, 1_000_000),
            "sample_metadata": [public_tracks.seqc_sample_metadata(name) for name in samples], "fixture": True}
    # Only replace the source-pin admission for this explicitly synthetic fixture.
    # The actual lane aggregation, blinding, storage, binding and verification run.
    monkeypatch.setattr(public_tracks, "inspect_seqc_cache", lambda *a, **k: info)
    bundle = prepare_public_task(plan, "seqc_controls", cache.parent, directory / "prepared-seqc", allow_data_execution=True)
    task, values, metadata, calibration, _ = load_public_inputs(Path(bundle.root) / "inputs", allow_data_execution=True)
    reference = json.loads((Path(bundle.root) / "private/reference.json").read_text())
    assert values.shape == (44, 8) and (values == 20).all()
    assert all(row["lane_count"] == "2" and row["replicate_kind"] == "technical" for row in metadata)
    assert len(calibration["expected_inputs"]) == 8
    assert len(reference["eligible_evaluation_controls"]) == 4
    assert not set(calibration["expected_inputs"]) & set(reference["evaluation_ratios_A_over_B"])
    assert all(gene.startswith("g_") for gene in task["gene_ids"])
    assert verify_public_task(bundle, plan, allow_data_execution=True) == bundle
