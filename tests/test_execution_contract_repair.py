"""Offline regressions for public execution interfaces; no truth or provider calls."""
from copy import deepcopy
import pytest
from tests.test_method_planner import resolved, trial_spec, Selector


def declaration(representation="raw_counts"):
    return {"format": "devbench.bulk-rna-input/1", "representation": representation,
            "axes": ["gene", "sample"], "provenance": None, "biological_independence": None}


def inspect_input(representation="raw_counts", **changes):
    from devbench.analysis.rna_input_contract import validate_bulk_rna_input
    values = dict(gene_ids=["g1", "g2"], sample_ids=["s1", "s2"],
                  metadata_rows=[{"sample_id": "s1"}, {"sample_id": "s2"}], matrix=[[1, 2], [3, 4]])
    values.update(changes)
    return validate_bulk_rna_input(declaration(representation), **values)


def test_bulk_contract_distinguishes_representations_and_unknowns():
    record = inspect_input()
    assert record["representation"] == "raw_counts"
    assert record["shape"] == [2, 2]
    assert record["biological_independence"] is None
    assert "biological_independence" in record["unknowns"]
    assert inspect_input("normalized_expression", matrix=[[1.5, -2.0], [0.0, 2.2]])["representation"] == "normalized_expression"
    assert inspect_input(None)["representation"] is None
    with pytest.raises(ValueError, match="raw counts"):
        inspect_input(matrix=[[1.5, 2], [3, 4]])


@pytest.mark.parametrize("changes", [
    {"sample_ids": ["s1", "s1"]}, {"gene_ids": ["g1", "g1"]},
    {"metadata_rows": [{"sample_id": "s1"}, {"sample_id": "other"}]},
    {"matrix": [[1, 2, 3], [3, 4, 5]]}, {"matrix": [[True, 2], [3, 4]]},
])
def test_bulk_contract_rejects_axis_or_typed_value_mismatch(changes):
    with pytest.raises(ValueError):
        inspect_input(**changes)


def test_explicit_bulk_contract_reaches_planning_without_observation_qc(tmp_path):
    from tests.test_extensible_methods import prepared
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods, _, _, step, *_ = prepared(tmp_path)
    planner = ScientificMethodPlanner(methods)
    import json
    matrix_path = tmp_path / "bulk.json"
    matrix_path.write_text(json.dumps({"gene_ids": ["g1", "g2"], "sample_ids": ["s1", "s2"], "matrix": [[1, 2], [3, 4]]}))
    metadata_path = tmp_path / "samples.json"
    metadata_path.write_text(json.dumps({"samples": [{"sample_id": "s1"}, {"sample_id": "s2"}]}))
    matrix_ref = methods.import_artifact(matrix_path, artifact_type="json", actor="scientist")
    metadata_ref = methods.import_artifact(metadata_path, artifact_type="json", actor="scientist")
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": matrix_ref["artifact_id"],
               "metadata_artifact_id": metadata_ref["artifact_id"], "declarations": declaration()}
    state = planner.create({"scientific_objective": "Discover expression patterns", "computational_task": "bulk RNA"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [matrix_ref["artifact_id"], metadata_ref["artifact_id"]]},
        actor="scientist", rna_input_contract=binding)
    selector = Selector({"candidates": []})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    context = selector.contexts[0]
    contract = context["dataset_summary"]["observed"]["bulk_rna_input"]
    assert contract["shape"] == [2, 2]
    assert contract["input_artifact_refs"]["matrix"]["sha256"] == matrix_ref["sha256"]
    assert context["prerequisite_inspection"]["inspection_kind"] == "bulk_rna_input"
    assert not context["prerequisite_inspection"]["failures"]
    retained = methods.get("method_prerequisite_inspection", context["prerequisite_inspection"]["inspection_id"])
    assert retained["inspection_kind"] == "bulk_rna_input"
    assert context["dataset_summary"]["unknowns"] == []


@pytest.mark.parametrize("seed", [None, 0, 17, 2**32 - 1])
def test_scientific_parameter_seed_is_public(seed):
    from devbench.analysis.episodes import _public_only
    _public_only({"decision_kind": "method_implementation_draft", "parameters": {"seed": seed}})


@pytest.mark.parametrize("seed", [True, -1, 2**32, "17", 1.5])
def test_scientific_parameter_seed_rejects_invalid_values(seed):
    from devbench.analysis.episodes import _public_only
    with pytest.raises(ValueError):
        _public_only({"decision_kind": "method_implementation_draft", "parameters": {"seed": seed}})


@pytest.mark.parametrize("hidden", [{"seed": 17}, {"generation_seed": 17}, {"evaluator_seed": 17},
                                     {"private_truth": {"parameters": {"seed": 17}}}])
def test_seed_exception_cannot_expose_evaluator_namespaces(hidden):
    from devbench.analysis.episodes import _public_only
    with pytest.raises(ValueError):
        _public_only({"decision_kind": "method_implementation_draft", "nested": hidden})


def structured_ref(planner, identity):
    artifact = planner.methods.get("artifact", identity)
    return {"format": "devbench.trial-input/1", **{key: artifact[key] for key in
            ("artifact_id", "sha256", "size_bytes", "artifact_type")}}


def test_trial_prompt_carries_exact_structured_refs_and_accepts_binding(tmp_path):
    planner, state, _, _, branch, step = resolved(tmp_path)
    spec = trial_spec(step)
    identity = step["inputs"]["dataset"]
    ref = structured_ref(planner, identity)
    spec["inputs"]["Unknown QC"]["dataset"] = ref
    selector = Selector({"experiments": [spec]})
    proposals = planner.design_trials(state["session_id"], selector, actor="scientist")
    assert selector.contexts[0]["available_input_artifact_refs"][identity] == ref
    assert proposals[0]["inputs"]["Unknown QC"]["dataset"] == identity
    assert proposals[0]["input_artifact_refs"]["Unknown QC"]["dataset"] == ref
    planner.declare_trial(state["session_id"], branch["branch_id"], proposals[0], actor="scientist")


@pytest.mark.parametrize("field,value", [("artifact_id", "truncated"), ("sha256", "a" * 64),
                                         ("size_bytes", 1), ("artifact_type", "file")])
def test_trial_design_rejects_altered_ref_before_admission(tmp_path, field, value):
    planner, state, _, _, _, step = resolved(tmp_path)
    spec = trial_spec(step)
    ref = structured_ref(planner, step["inputs"]["dataset"])
    ref[field] = value
    spec["inputs"]["Unknown QC"]["dataset"] = ref
    with pytest.raises(ValueError):
        planner.design_trials(state["session_id"], Selector({"experiments": [spec]}), actor="scientist")


def test_empty_unexplained_trial_is_operational_failure():
    from devbench.analysis.method_authoring_contract import trial_terminal_outcome
    assert trial_terminal_outcome([], []) == "operational_failure"
    assert trial_terminal_outcome([{"id": "p"}], [{"status": "failed"}]) == "operational_failure"
    assert trial_terminal_outcome([{"id": "p"}], [{"status": "success", "artifacts": [{}]}]) == "executed"
    assert trial_terminal_outcome([{"id": "p"}], [{"status": "success", "artifacts": []}]) == "operational_failure"
    assert trial_terminal_outcome([], [], refusal={"reason": "No identifiable contrast", "evidence_artifact_ids": ["proof"]}) == "evidence_linked_refusal_pending_review"
    assert trial_terminal_outcome([], [], refusal={"reason": "No data", "evidence_artifact_ids": []}) == "operational_failure"


@pytest.mark.parametrize("value", [-1, True, "17", 2**32])
def test_scientific_named_seed_parameters_are_typed(value):
    from devbench.analysis.episodes import _public_only
    with pytest.raises(ValueError):
        _public_only({"decision_kind": "method_scientific_planning",
                      "parameter_candidates": [{"name": "seed", "value": value}]})


def test_bulk_inspection_cannot_be_injected_as_caller_assertion(tmp_path):
    from tests.test_extensible_methods import prepared
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods, _, _, step, *_ = prepared(tmp_path)
    with pytest.raises(ValueError, match="binding"):
        ScientificMethodPlanner(methods).create(
            {"scientific_objective": "Inspect RNA", "computational_task": "bulk RNA"},
            {"observed": {"bulk_rna_input": inspect_input()}, "inferred": {}, "unknowns": [],
             "artifact_ids": list(step["inputs"].values())}, actor="scientist")


def test_declared_raw_counts_may_have_whole_float_storage_without_conversion():
    report = inspect_input(matrix=[[1.0, 2.0], [3.0, 4.0]])
    assert report["numeric_storage"] == "real"
    assert report["representation"] == "raw_counts"
    assert report["count_values_valid"] is True


def test_bulk_report_carries_declared_sample_metadata_without_independence_inference():
    rows = [{"sample_id": "s1", "condition": "A", "donor": "d1"},
            {"sample_id": "s2", "condition": "B", "donor": "d1"}]
    report = inspect_input(metadata_rows=rows)
    assert report["sample_metadata"] == rows
    assert report["biological_independence"] is None
    assert report["sample_metadata_complete"] is True


@pytest.mark.parametrize("intent,expected", [("retain", "eligible"), ("diagnostic_flagging", "eligible"),
                                             ("infer", "blocked_unknown"), ("exclude", "blocked_unknown")])
def test_bulk_applicability_uses_axis_facts_and_keeps_scientific_unknowns(intent, expected):
    from devbench.analysis.method_planner import _applicability
    from tests.test_method_planner import candidate
    report = inspect_input()
    report["input_integrity_valid"] = True
    item = candidate()
    item["assumptions"] = []
    item["action_scope"] = {"format": "devbench.method-action/1", "intent": intent,
                            "interpretation": "numerical_atypicality" if intent == "diagnostic_flagging" else "none",
                            "exclusions": intent == "exclude"}
    assert _applicability(item, {"observed": {"bulk_rna_input": report}, "unknowns": []})["applicability"] == expected


def test_metadata_catalog_exposes_bounded_declared_roles_and_confounding():
    from devbench.analysis.rna_input_contract import validate_bulk_rna_input
    supplied = declaration()
    supplied["metadata_roles"] = {"condition": "condition", "batch": "batch", "biological_unit": "donor"}
    rows = [{"sample_id": "s1", "condition": "A", "batch": "b1", "donor": "d1"},
            {"sample_id": "s2", "condition": "B", "batch": "b2", "donor": "d1"}]
    record = validate_bulk_rna_input(supplied, gene_ids=["g"], sample_ids=["s1", "s2"], metadata_rows=rows, matrix=[[1, 2]])
    assert record["metadata_catalog"]["donor"]["distinct_count"] == 1
    assert record["metadata_catalog"]["condition"]["missing_count"] == 0
    assert record["metadata_role_cross_tabs"]["condition:batch"]["complete"] is True
    assert record["biological_independence"] is None


@pytest.mark.parametrize("intent,expected", [("retain", "eligible"), ("diagnostic_flagging", "eligible"), ("infer", "blocked_unknown")])
def test_8000_gene_registered_bulk_path_resolves_nonempty_candidates(tmp_path, intent, expected):
    import json
    from tests.test_extensible_methods import prepared
    from tests.test_method_planner import candidate
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods, *_ = prepared(tmp_path)
    matrix_path = tmp_path / "bulk-8000.json"
    matrix_path.write_text(json.dumps({"gene_ids": [f"gene{i}" for i in range(8000)], "sample_ids": ["s1", "s2"],
                                       "matrix": [[1, 2] for _ in range(8000)]}))
    metadata_path = tmp_path / "metadata-8000.json"
    metadata_path.write_text(json.dumps({"samples": [{"sample_id": "s1", "condition": "A", "donor": "d1"},
                                                     {"sample_id": "s2", "condition": "B", "donor": "d1"}]}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="scientist") for path in (matrix_path, metadata_path)]
    declared = declaration()
    declared["metadata_roles"] = {"condition": "condition", "biological_unit": "donor"}
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
               "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declared}
    planner = ScientificMethodPlanner(methods)
    state = planner.create({"scientific_objective": "Describe supplied expression", "computational_task": "bulk RNA"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
        actor="scientist", rna_input_contract=binding)
    item = candidate("Bulk candidate")
    item["assumptions"] = []
    item["action_scope"] = {"format": "devbench.method-action/1", "intent": intent,
                            "interpretation": "numerical_atypicality" if intent == "diagnostic_flagging" else "none", "exclusions": False}
    selector = Selector({"candidates": [item]}, {"bindings": [{"candidate": item["name"], "method_version_id": None, "discovery": None}]})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    choices = planner.resolve_capabilities(state["session_id"], selector, [], actor="scientist")
    assert choices[0]["applicability"] == expected
    report = selector.contexts[0]["dataset_summary"]["observed"]["bulk_rna_input"]
    assert report["shape"] == [8000, 2]
    assert report["metadata_catalog"]["donor"]["distinct_count"] == 1
    assert report["biological_independence"] is None
    assert "observation_ids_unique" not in selector.contexts[0]["prerequisite_inspection"]["observed"]
    matrix_object = methods.artifact_path(refs[0]["artifact_id"])
    matrix_object.chmod(0o644)
    matrix_object.write_bytes(b"{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        planner.create({"scientific_objective": "Describe expression", "computational_task": "bulk RNA"},
            {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
            actor="scientist", rna_input_contract=binding)


@pytest.mark.parametrize("observed", [
    {"annotations": {"parameters": {"nested": {"seed": 17}}}},
    {"parameter_candidates": [{"name": "seed", "value": 17}]},
    {"contract": {"parameters": {"properties": {"seed": {"type": "integer"}}}}},
])
def test_algorithm_seed_exception_cannot_be_spoofed_by_metadata_labels(observed):
    from devbench.analysis.episodes import _public_only
    with pytest.raises(ValueError):
        _public_only({"decision_kind": "method_scientific_planning", "dataset_summary": {"observed": observed}})


def test_actual_implementation_seed_schema_and_frozen_candidate_pass_public_guard():
    from devbench.analysis.episodes import _public_only
    _public_only({"decision_kind": "method_implementation_draft",
        "candidate": {"parameter_candidates": [{"name": "seed", "value": 17}]},
        "interface": {"parameters": {"properties": {"seed": {"type": "integer", "default": 17}}}}})


def test_terminal_outcomes_follow_actual_planner_execution_and_reject_unregistered_refusal(tmp_path):
    from tests.test_extensible_methods import FixtureExecutor
    planner, state, _, _, branch, step = resolved(tmp_path)
    assert planner.terminal_outcome(state["session_id"]) == "operational_failure"
    with pytest.raises(ValueError, match="Unknown artifact"):
        planner.terminal_outcome(state["session_id"], refusal={"reason": "Unidentifiable design", "evidence_artifact_ids": ["truncated"]})
    planner.declare_trial(state["session_id"], branch["branch_id"], trial_spec(step), actor="scientist")
    planner.run_trial(state["session_id"], "qc-pilot", actor="scientist", executor=FixtureExecutor(), allow_data_execution=True)
    assert planner.terminal_outcome(state["session_id"]) == "executed"


def test_reordered_metadata_aligns_by_exact_ids_with_retained_source_mapping():
    rows = [{"sample_id": "s2", "condition": "B"}, {"sample_id": "s1", "condition": "A"}]
    report = inspect_input(metadata_rows=rows)
    assert report["sample_metadata"] == [rows[1], rows[0]]
    assert report["metadata_alignment"]["source_order_sample_ids"] == ["s2", "s1"]
    assert report["metadata_alignment"]["matrix_order_sample_ids"] == ["s1", "s2"]
    assert report["metadata_alignment"]["source_indices_in_matrix_order"] == [1, 0]
    assert report["metadata_sha256"] != report["aligned_metadata_sha256"]
    assert rows[0]["sample_id"] == "s2"  # supplied metadata is read-only


@pytest.mark.parametrize("context_limit", [128_000, 1_000_000])
def test_valid_large_metadata_reaches_candidate_selector_with_immutable_complete_inspection(tmp_path, context_limit):
    import json
    from devharness_core.provenance import canonical_json
    from devbench.analysis.episode_agent import AgentLimits
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from tests.test_extensible_methods import prepared
    methods, *_ = prepared(tmp_path)
    samples = [f"s{index}" for index in range(24)]
    matrix_path = tmp_path / "large-metadata-matrix.json"
    matrix_path.write_text(json.dumps({"gene_ids": ["g"], "sample_ids": samples, "matrix": [[1] * 24]}))
    rows = [{"sample_id": sample, "condition": "A" if index < 12 else "B", "donor": f"d{index // 2}",
             **{f"covariate{field}": f"{index}-{field}-" + "x" * 350 for field in range(40)}}
            for index, sample in enumerate(samples)]
    metadata_path = tmp_path / "large-metadata.json"
    metadata_path.write_text(json.dumps({"samples": rows}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="scientist") for path in (matrix_path, metadata_path)]
    declared = declaration()
    declared["metadata_roles"] = {"condition": "condition", "biological_unit": "donor"}
    binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
               "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declared}
    planner = ScientificMethodPlanner(methods)
    state = planner.create({"scientific_objective": "Assess supplied expression", "computational_task": "bulk RNA"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
        actor="scientist", rna_input_contract=binding, limits=AgentLimits(max_context_bytes=context_limit))
    selector = Selector({"candidates": []})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert len(selector.contexts) == 1
    context = selector.contexts[0]
    assert len(canonical_json(context)) < 128_000
    summary = context["dataset_summary"]["observed"]["bulk_rna_input"]
    assert summary["sample_metadata_complete"] is False
    assert summary["metadata_catalog"]["covariate0"]["distinct_count"] == 24
    assert summary["metadata_catalog"]["covariate0"]["missing_count"] == 0
    assert summary["metadata_catalog"]["covariate0"]["types"] == ["str"]
    assert summary["metadata_catalog"]["covariate0"]["levels_complete"] is False
    assert summary["metadata_catalog"]["covariate0"]["levels"][0]["value_complete"] is False
    assert summary["metadata_catalog"]["condition"]["levels"][0]["value"] == "A"
    assert len(summary["sample_metadata_fields"]) == 43
    assert summary["metadata_role_cross_tabs"]["condition:biological_unit"]["complete"] is True
    reference = summary["inspection_artifact_ref"]
    full = json.loads(methods.artifact_path(reference["artifact_id"]).read_bytes())
    assert full["sample_metadata"] == rows
    assert full["metadata_catalog"]["covariate0"]["levels"][0]["value"] == rows[0]["covariate0"]
    assert full["input_artifact_refs"]["metadata"]["sha256"] == refs[1]["sha256"]
    assert summary["provenance"] is None and summary["biological_independence"] is None
    assert context["prerequisite_inspection"]["bulk_rna_inspection_artifact_ref"] == reference
    assert "bulk_rna_input" not in context["prerequisite_inspection"]


def test_unrelated_registered_artifact_does_not_establish_verified_scientific_refusal(tmp_path):
    planner, state, _, _, _, step = resolved(tmp_path)
    refusal = {"reason": "Claimed scientific refusal", "evidence_artifact_ids": list(step["inputs"].values())}
    assert planner.terminal_outcome(state["session_id"], refusal=refusal) == "evidence_linked_refusal_pending_review"


@pytest.mark.parametrize("column", ["cohort_id", "role"])
def test_typed_metadata_column_names_cannot_trigger_configuration_privacy_guard(tmp_path, column):
    import json
    from tests.test_extensible_methods import prepared
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.episodes import _public_only
    methods, *_ = prepared(tmp_path)
    matrix_path = tmp_path / "typed-column-matrix.json"
    matrix_path.write_text(json.dumps({"gene_ids": ["g"], "sample_ids": ["s1", "s2"], "matrix": [[1, 2]]}))
    metadata_path = tmp_path / "typed-columns.json"
    metadata_path.write_text(json.dumps({"samples": [{"sample_id": "s1", column: "A"}, {"sample_id": "s2", column: "B"}]}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="scientist") for path in (matrix_path, metadata_path)]
    planner = ScientificMethodPlanner(methods)
    state = planner.create({"scientific_objective": "Describe expression", "computational_task": "bulk RNA"},
        {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
        actor="scientist", rna_input_contract={"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
            "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declaration()})
    class PublicSelector(Selector):
        def select(self, context, *args, **kwargs):
            _public_only(context)
            return super().select(context, *args, **kwargs)
    selector = PublicSelector({"candidates": []})
    planner.generate_candidates(state["session_id"], selector, actor="scientist")
    columns = selector.contexts[0]["dataset_summary"]["observed"]["bulk_rna_input"]["metadata_columns"]
    assert {row["name"] for row in columns} == {"sample_id", column}
    assert next(row for row in columns if row["name"] == column)["distinct_count"] == 2


@pytest.mark.parametrize("column", ["evaluator_seed", "generation_seed", "private_truth", "expected_scope", "latent_truth"])
def test_private_metadata_fields_are_rejected_before_projection_or_selector(tmp_path, column):
    import json
    from tests.test_extensible_methods import prepared
    from devbench.analysis.method_planner import ScientificMethodPlanner
    methods, *_ = prepared(tmp_path)
    matrix_path = tmp_path / "private-field-matrix.json"
    matrix_path.write_text(json.dumps({"gene_ids": ["g"], "sample_ids": ["s1", "s2"], "matrix": [[1, 2]]}))
    metadata_path = tmp_path / "private-fields.json"
    metadata_path.write_text(json.dumps({"samples": [{"sample_id": "s1", column: "withheld"}, {"sample_id": "s2", column: "withheld"}]}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="scientist") for path in (matrix_path, metadata_path)]
    planner = ScientificMethodPlanner(methods)
    selector = Selector({"candidates": []})
    with pytest.raises(ValueError, match="private|evaluator"):
        planner.create({"scientific_objective": "Describe expression", "computational_task": "bulk RNA"},
            {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
            actor="scientist", rna_input_contract={"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
                "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declaration()})
    assert selector.contexts == []


@pytest.mark.parametrize("column", ["reward", "rewards", "reference_answer", "reference_answers", "evaluation_feedback",
                                    "heldout_feedback", "grader_feedback", "evaluation_scores", "expected_accuracy"])
def test_typed_metadata_cannot_launder_episode_evaluator_fields(tmp_path, column):
    import json
    from tests.test_extensible_methods import prepared
    from devbench.analysis.method_planner import ScientificMethodPlanner
    from devbench.analysis.episodes import _public_only
    methods, *_ = prepared(tmp_path)
    matrix_path = tmp_path / "typed-private-matrix.json"
    matrix_path.write_text(json.dumps({"gene_ids": ["g"], "sample_ids": ["s1", "s2"], "matrix": [[1, 2]]}))
    metadata_path = tmp_path / "typed-private-metadata.json"
    metadata_path.write_text(json.dumps({"samples": [{"sample_id": "s1", "role": "control", column: "withheld"},
                                                     {"sample_id": "s2", "role": "case", column: "withheld"}]}))
    refs = [methods.import_artifact(path, artifact_type="json", actor="scientist") for path in (matrix_path, metadata_path)]
    planner = ScientificMethodPlanner(methods)
    selector = Selector({"candidates": []})
    with pytest.raises(ValueError, match="private|evaluator"):
        state = planner.create({"scientific_objective": "Describe expression", "computational_task": "bulk RNA"},
            {"observed": {}, "inferred": {}, "unknowns": [], "artifact_ids": [ref["artifact_id"] for ref in refs]},
            actor="scientist", rna_input_contract={"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs[0]["artifact_id"],
                "metadata_artifact_id": refs[1]["artifact_id"], "declarations": declaration()})
        _public_only(state["context"])
        planner.generate_candidates(state["session_id"], selector, actor="scientist")
    assert selector.contexts == []
