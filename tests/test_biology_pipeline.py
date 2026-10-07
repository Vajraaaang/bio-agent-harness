"""Small real numerical integration; never launches an agent or model provider."""
import os
from pathlib import Path
import tempfile

import pytest
from devbench.study.biology_planning import default_biology_study


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical fixtures require opt-in")
@pytest.mark.parametrize("case_id", [recipe.case_id for recipe in default_biology_study(coverage="full").recipes])
def test_every_case_builds_a_reference_from_small_actual_generated_inputs(case_id):
    from devbench.study.biology_planning import default_biology_recipe
    from devbench.study.biology_bulk import generate_biology_bulk
    from devbench.study.biology_singlecell import generate_biology_singlecell
    from devbench.study.biology_datasets import _context_and_annotations
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_scoring import build_biology_reference, grade_biology_answer
    from devbench.study.biology_answers import public_output_layout
    recipe = default_biology_recipe(case_id, num_genes=120).model_copy(update={"generation_seed": 371})
    cellular = bool(recipe.parameters.cells_per_specimen)
    generate = generate_biology_singlecell if cellular else generate_biology_bulk
    bundle = generate(recipe, allow_data_execution=True)
    catalog = load_biology_catalog()
    public, annotations = _context_and_annotations(bundle, recipe, catalog)
    reference = build_biology_reference(template=catalog.template(recipe.template_id),
        private_truth=bundle.private_truth, public_context=public["context"], public_annotations=annotations,
        task_id="b"*64, input_sha256="a"*64, gene_ids=bundle.gene_ids,
        cell_ids=bundle.observation_ids if cellular else (), allow_result_analysis=True)
    grade = grade_biology_answer(None, reference=reference, allow_result_analysis=True)
    assert set(grade.target_reference_availability) == {target.target_id for target in catalog.template(recipe.template_id).targets}
    layouts = public_output_layout(public, annotations)
    for target in reference.targets:
        layout = layouts[target.target_id]
        for hypothesis in target.hypotheses:
            expected = (layout["coordinate_ids_by_component"][hypothesis.component_id]
                        if hypothesis.component_id is not None else layout["effect_coordinate_ids"])
            assert list(hypothesis.coordinate_ids) == expected, target.target_id
        for curve in target.curves:
            assert list(curve.coordinate_ids) == layout["curve_coordinate_ids"], target.target_id


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical fixtures require opt-in")
@pytest.mark.parametrize("case_id,tool", [("T01-dense", "fit_temporal_trajectory"),
                                          ("T03-baseline-only", "condition_time_analysis"),
                                          ("T14-state-only", "cell_type_state_analysis"),
                                          ("T19-motif-supported", "motif_enrichment")])
def test_prepared_inputs_tool_answer_and_grader_share_a_contract(case_id, tool):
    import json
    from devbench.study.biology import load_biology_catalog
    from devbench.study.biology_planning import default_biology_recipe
    from devbench.study.biology_datasets import prepare_biology_dataset
    from devbench.study.biology_execution import project_biology_task
    from devbench.study.biology_answers import BiologyAnswer
    from devbench.study.biology_scoring import build_biology_reference, grade_biology_answer
    from devbench.study.biology_tools_bulk import _analyze_bulk_worker
    from devbench.study.biology_tools_singlecell import _analyze_singlecell_worker
    from devbench.study.contracts import canonical_json_bytes
    root = Path(__file__).resolve().parents[1]
    (root / "tmp").mkdir(exist_ok=True)
    recipe = default_biology_recipe(case_id, num_genes=120).model_copy(update={"generation_seed": 371})
    with tempfile.TemporaryDirectory(dir=root / "tmp", prefix="biology-integration-") as temporary:
        directory = Path(temporary)
        bundle = prepare_biology_dataset(recipe, directory / "bundle", plan_identity="a"*64,
                                         cohort_id=recipe.recipe_id, allow_data_execution=True)
        public = project_biology_task(bundle, recipe, "b"*64, directory / "bundle", allow_data_execution=True)
        inputs = directory / "bundle" / "inputs"
        analyze = _analyze_singlecell_worker if recipe.parameters.cells_per_specimen else _analyze_bulk_worker
        result = analyze(tool, public, str(inputs / "counts.zarr"), str(inputs / "metadata.csv"),
                         str(inputs / "public_annotations.json"), {}, True)
        assert all(row["claim_type"] != "failed" for row in result["target_results"]), result["diagnostics"]
        answer = BiologyAnswer.model_validate_json(canonical_json_bytes({"task_id": "b"*64,
            "input_sha256": bundle.input_sha256, "gene_universe_sha256": public["gene_universe_sha256"],
            "target_results": result["target_results"]}))
        reference = build_biology_reference(template=load_biology_catalog().template(recipe.template_id),
            private_truth=json.loads((directory / "bundle/private/truth.json").read_text()),
            public_context=public["context"], public_annotations=json.loads((inputs / "public_annotations.json").read_text()),
            task_id="b"*64, input_sha256=bundle.input_sha256, gene_ids=tuple(public["gene_ids"]),
            cell_ids=tuple(public["observation_ids"]) if recipe.parameters.cells_per_specimen else (),
            allow_result_analysis=True)
        grade = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        reported_targets = {row["target_id"] for row in result["target_results"]}
        errors = {error for target in grade.per_target_results if target.target_id in reported_targets for error in target.errors}
        assert not errors, errors
        # Deliberately no assertion that random scientific estimates pass a
        # calibrated-performance threshold: this checks interfaces and execution.


@pytest.mark.skipif(os.environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="Small local numerical fixtures require opt-in")
def test_enforced_broker_executes_workers_and_verifies_real_receipts():
    from devbench.study.biology_planning import default_biology_recipe
    from devbench.study.biology_datasets import prepare_biology_dataset
    from devbench.study.biology_execution import project_biology_task
    from devbench.study.biology_broker import BiologyBroker, verify_biology_workflow
    from devbench.study.broker import BrokerPolicy
    from devbench.study.evidence import verify_receipts
    root = Path(__file__).resolve().parents[1]
    recipe = default_biology_recipe("T03-baseline-only", num_genes=120).model_copy(update={"generation_seed": 371})
    with tempfile.TemporaryDirectory(dir=root / "tmp", prefix="biology-broker-") as temporary:
        directory = Path(temporary)
        bundle = prepare_biology_dataset(recipe, directory / "bundle", plan_identity="a"*64,
                                         cohort_id=recipe.recipe_id, allow_data_execution=True)
        public = project_biology_task(bundle, recipe, "b"*64, directory / "bundle", allow_data_execution=True)
        aliases = {public[key]: public[key] for key in ("expression_path", "metadata_path", "public_annotations_path")}
        policy = BrokerPolicy(condition="enforced", input_sha256=bundle.input_sha256, measurement_type="raw_counts",
            gene_ids=tuple(public["gene_ids"]), population_inference_allowed=False, timeout_sec=90,
            implementation_sha256="c"*64, max_calls=4, max_memory_mb=2048)
        broker = BiologyBroker(policy, aliases, directory / "audit", public_task=public, allow_data_execution=True)
        try:
            for tool in ("audit_expression_quality", "validate_biological_units", "validate_design_matrix", "condition_time_analysis"):
                response = broker.execute(tool, {"options": {}})
                assert response["status"] == "succeeded", response.get("error")
            targets = response["result"]["target_results"]
            for target in targets:
                target["tool_evidence_ids"] = [response["receipt_id"]]
        finally:
            receipt = broker.close()
        evidence = verify_receipts(directory / "audit", expected_receipt=receipt,
            expected_input_sha256=bundle.input_sha256, expected_implementation_sha256="c"*64,
            allow_evidence_read=True)
        answer = {"task_id": public["task_id"], "input_sha256": bundle.input_sha256,
                  "gene_universe_sha256": public["gene_universe_sha256"], "target_results": targets}
        checked = verify_biology_workflow(answer, evidence, public, allow_evidence_read=True)
        assert checked["workflow_passed"], checked["errors"]
