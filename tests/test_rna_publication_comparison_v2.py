"""Actual retained synthetic receipts drive /2 comparison; never scientific proof."""
from copy import deepcopy
import hashlib

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis import rna_publication_comparison as api
from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis import rna_reference_extraction as extraction
from devbench.analysis.rna_comparison_outputs import extract_comparison_outputs
from devbench.analysis.rna_execution_inputs import prepare_execution_inputs
from devbench.analysis.rna_publication_evidence import prepare_publication_evidence
from tests.test_rna_comparison_outputs import prepared, RetainedTableExecutor, bound_run
from tests.test_rna_execution_inputs import ports
from tests.test_extensible_methods import custom
from tests.test_rna_publication_metrics import gene_table
from tests.test_rna_publication_profiles import fixture as intent_fixture, source


def synthetic_case(tmp_path, *, failure=False, changed_effect=False, required_formula="gene_effect_absolute_difference", selected_profile="mean_expression", reference_complete=True, extra_locator=False, actual_context_change=None, frozen_applicability=None, universe_change=None, frozen_estimand_change=None, missing_eligible_effect=False, malformed_parameters=False, empty_reference=False):
    service, step, prior_plan_ref = prepared(tmp_path)
    prior_plan = profiles.load_assessment_plan(prior_plan_ref, workspace=tmp_path, service=service)
    case = tmp_path / "comparison-evidence"
    case.mkdir()
    intent = intent_fixture(case)
    intent["profile_specs"][0]["profile_id"] = selected_profile
    intent["profile_specs"][0]["required_input_capabilities"] = deepcopy(profiles.PROFILE_REGISTRY[selected_profile]["capabilities"])
    relationship = profiles.PROFILE_REGISTRY[selected_profile]["relationships"][0]
    intent["estimand"]["relationship"]["kind"] = relationship
    intent["profile_specs"][0]["applicability_rules"] = [{"predicate_id": "relationship_in", "values": [relationship]},
        {"predicate_id": "entity_type_in", "values": ["gene"]}]
    prior_intent = profiles.load_assessment_intent(prior_plan["intent_ref"], workspace=tmp_path, service=service)
    intent["dataset_ref"] = prior_intent["dataset_ref"]
    execution_inputs_ref = prior_plan["execution_inputs_ref"]
    if frozen_applicability != "unsupported_input":
        dataset = profiles.read_reference_json(intent["dataset_ref"], workspace=tmp_path, service=service)
        execution_inputs_ref = prepare_execution_inputs(dataset["binding"], ports(infer=True), artifact_service=service,
            output=case / "inferential-inputs")["record_ref"]
    if frozen_applicability == "not_applicable":
        intent["profile_specs"][0]["applicability_rules"][0]["values"] = ["association"]
    table = gene_table()
    table["family"]["eligible_ids"] = ["g1", "g2"]
    table["rows"][0]["q_value"], table["rows"][1]["q_value"] = .02, .04
    table["rows"][2].update(tested=False, status="filtered")
    intent["estimand"]["estimand_id"] = table["alignment"]["estimand_id"]
    intent["estimand"]["measurement"]["units"] = table["alignment"]["effect_scale"]
    intent["estimand"]["population"] = table["alignment"]["cohort"]
    intent["estimand"]["biological_unit"]["independent_unit"] = table["alignment"]["biological_unit"]
    intent["estimand"]["relationship"]["definition"] = table["alignment"]["contrast"]
    if frozen_estimand_change is not None:
        field = frozen_estimand_change.removesuffix("_unknown")
        owner = intent["estimand"]
        parts = field.split(".")
        for part in parts[:-1]:
            owner = owner[part]
        owner[parts[-1]] = None if frozen_estimand_change.endswith("_unknown") else "different-frozen-context"
        if owner[parts[-1]] is None:
            intent["estimand"]["unknowns"].append({"field": field, "reason": "Explicit unresolved fixture context"})
    for requirement in intent["estimand"]["requirement_sources"]:
        requirement["origin"] = "fixture"
    estimand_ref = source(case / "reference-estimand.json", intent["estimand"])
    family = deepcopy(table["family"])
    family.update(complete=reference_complete, probability_semantics={"p_value": "not_applicable", "q_value": "not_applicable"},
        failed_hypothesis_policy="unknown")
    family_ref = source(case / "reference-family.json", family)
    identity_record = {"doi": "10.1234/comparison-fixture", "pmid": None, "pmcid": None,
        "title": "Synthetic Comparison Software Fixture", "version": "fixture-v1"}
    texts = {row["feature_id"]: row["feature_id"] + " synthetic B versus A effect " + str(row["effect"])
        + " log2_ratio; independent_fixture_unit; no shrinkage; interval absent." for row in table["rows"][:2]}
    extra_text = "Unreviewed synthetic source statement: effect 99"
    paper = "\n".join([identity_record["doi"], identity_record["title"], identity_record["version"], *texts.values(),
        *([extra_text] if extra_locator else [])])
    body = paper.encode()
    path = case / "selected-paper.txt"
    path.write_bytes(body)
    body_ref = {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    def source_locator(text):
        start = body.index(text.encode())
        return {"start_byte": start, "end_byte": start + len(text.encode()), "text": text, "location": "Synthetic selected publication"}
    excerpts = [{"excerpt_id": key + "-quote", "source_id": "synthetic-paper", "locator": source_locator(text),
        "visibility": "evaluator", "purpose": "outcome"} for key, text in texts.items()]
    if extra_locator:
        excerpts.append({"excerpt_id": "unreviewed-quote", "source_id": "synthetic-paper", "locator": source_locator(extra_text),
            "visibility": "evaluator", "purpose": "outcome"})
    publication = prepare_publication_evidence(case / "selected-publication", {"identity": identity_record,
        "approved_urls": ["https://example.invalid/comparison-fixture"]},
        [{"source_id": "synthetic-paper", "path": str(path), "sha256": body_ref["sha256"],
            "source_url": "https://example.invalid/comparison-fixture", "identity": identity_record, "access": "extracted_text",
            "identity_locators": {key: source_locator(value) for key, value in identity_record.items() if value is not None},
            "identity_reviewed_by": "independent-curator", "limitations": "One invented selected paper retained only for engineering checks"}],
        excerpts, profiles.read_reference_json(intent["exposure_ref"], workspace=tmp_path), curator_id="independent-curator",
        participant_id="workflow-author", workspace=tmp_path, allow_evidence_read=True)
    findings = []
    for row in table["rows"][:2]:
        identity = row["feature_id"]
        number = str(row["effect"])
        text = texts[identity]
        excerpt = deepcopy(next(item for item in excerpts if item["excerpt_id"] == identity + "-quote"))
        locator = excerpt["locator"]
        review = {"format": extraction.CONTEXT_REVIEW_FORMAT, "review_id": identity + "-review", "excerpt_id": excerpt["excerpt_id"],
            "source_sha256": body_ref["sha256"], "claim": text, "proposal_origin": "human",
            "proposal_author_id": "workflow-author", "reviewed_by": "independent-curator",
            "checks": {key: {"state": "supported", "evidence_locators": [deepcopy(locator)],
                "explanation": "Synthetic contextual evidence only"} for key in
                ("entity", "population", "contrast", "measurement", "uncertainty", "negation", "scope")},
            "conclusion": "supported", "limitations": "Declared synthetic curation; not authenticated scientific approval"}
        entity = {"format": "devbench.rna-reference-gene/2", "feature_id": identity,
            "alignment": deepcopy(table["alignment"]), "mapping_namespace": table["mapping"]["namespace"],
            "annotation_ref": table["mapping"]["annotation_ref"]}
        findings.append({"claim_id": identity + "-effect", "estimand_ref": estimand_ref,
            "entity_refs": [source(case / (identity + "-entity.json"), entity)],
            "source_locator_refs": [source(case / (identity + "-locator.json"), {"source_ref": body_ref, "excerpt": excerpt})],
            "extraction_record_ref": source(case / (identity + "-number.json"), extraction.canonicalize_reference_number(number, rule="identity", reviewed=True)),
            "value_or_censoring": {"kind": "exact", "value": row["effect"], "units": intent["estimand"]["measurement"]["units"]},
            "uncertainty_ref": None, "tested_family_ref": family_ref, "reporting_completeness": "complete" if reference_complete else "partial",
            "context_review_ref": source(case / (identity + "-review.json"), review), "limitations": ["Selected source rows; not exhaustive truth"]})
        if extra_locator and identity == "g1":
            extra_excerpt = deepcopy(next(item for item in excerpts if item["excerpt_id"] == "unreviewed-quote"))
            findings[-1]["source_locator_refs"].append(source(case / "unreviewed-locator.json", {"source_ref": body_ref, "excerpt": extra_excerpt}))
    reference = {"format": profiles.REFERENCE_RESULT_SET_FORMAT, "reference_set_id": "synthetic-source-results",
        "publication_ref": publication, "source_collection_ref": None,
        "estimand_ref": estimand_ref, "findings": findings, "universe_ref": family_ref,
        "exposure_ref": intent["exposure_ref"], "limitations": ["Artificial reference; retained eligibility is explicit"]}
    intent["reference_bundle_ref"] = source(case / "source-results.json", reference)
    intent["required_claim_ids"] = [item["claim_id"] for item in findings]
    metric = intent["profile_specs"][0]["metric_specs"][0]
    metric.update(metric_id=required_formula, units=intent["estimand"]["measurement"]["units"],
        parameter_record_ref=source(case / "selected-parameters.json", {"format": "devbench.rna-gene-comparison-parameters/2",
            "actual_table_id": table["table_id"], "reference_claim_ids": intent["required_claim_ids"], "selection_rule": None}))
    metric["universe_rule_id"] = "complete_eligible_gene_family"
    universe = {"format": "devbench.rna-gene-universe-policy/2", "rule_id": metric["universe_rule_id"],
        "eligible_ids": ["g1", "g2"], "excluded_ids": ["g3"], "mapping_namespace": table["mapping"]["namespace"],
        "annotation_ref": table["mapping"]["annotation_ref"], "mapping_records": deepcopy(table["mapping"]["records"][:2])}
    if universe_change == "different_ids":
        universe.update(eligible_ids=["g1"], excluded_ids=["g2", "g3"], mapping_records=universe["mapping_records"][:1])
    elif universe_change == "different_exclusions":
        universe["excluded_ids"] = []
    elif universe_change == "mapping":
        universe["mapping_records"][0]["target_id"] = "different-target"
    review = {"format": "devbench.rna-gene-universe-review/2", **{key: deepcopy(value) for key, value in universe.items() if key != "format"},
        "reviewed_by": "independent-curator", "conclusion": "supported",
        "limitations": "Declared fixture family review; not authenticated scientific authority"}
    if universe_change == "different_review_ids":
        review["eligible_ids"] = ["g1"]
    universe["review_record_ref"] = None if universe_change == "missing_review" else source(case / "universe-review.json", review)
    if universe_change == "unknown":
        universe = {"rule_id": "unknown_rule"}
    elif universe_change == "rule_id":
        metric["universe_rule_id"] = "different_frozen_rule"
    intent["universe_policy_ref"] = source(case / "universe.json", universe)
    if empty_reference:
        reference.update(findings=[], universe_ref=intent["universe_policy_ref"])
        intent["reference_bundle_ref"] = source(case / "source-results.json", reference)
    if required_formula.startswith("gene_set_"):
        parameters = profiles.read_reference_json(metric["parameter_record_ref"], workspace=tmp_path)
        parameters["selection_rule"] = {"field": "q_value", "operator": "less_equal", "threshold": .1,
            "probability_semantics": "benjamini_hochberg", "family_id": table["family"]["family_id"]}
        metric["parameter_record_ref"] = source(case / "selected-parameters.json", parameters)
        metric["units"] = "fraction"
    if malformed_parameters:
        metric["parameter_record_ref"] = source(case / "selected-parameters.json",
            {"format": "devbench.rna-gene-comparison-parameters/2"})
    metric["acceptance_policy"] = {"kind": "agreement_bound", "operator": "less_equal", "bound": .25,
        "rationale": "Explicit fixture bound, not a scientific default", "source_ref": metric["parameter_record_ref"]}
    if required_formula.startswith("gene_set_"):
        metric["acceptance_policy"].update(operator="greater_equal", bound=.8)
    intent["decision_policy"] = {"kind": "all_required_metrics", "rationale": "Frozen engineering fixture rule"}
    analyst = profiles.read_reference_json(intent["analyst_projection_ref"], workspace=tmp_path)
    analyst.update(estimand=intent["estimand"], dataset_ref=intent["dataset_ref"])
    intent["analyst_projection_ref"] = source(case / "analyst.json", analyst)
    evaluator = profiles.read_reference_json(intent["evaluator_projection_ref"], workspace=tmp_path)
    evaluator.update(reference_bundle_ref=intent["reference_bundle_ref"], required_claim_ids=intent["required_claim_ids"])
    intent["evaluator_projection_ref"] = source(case / "evaluator.json", evaluator)
    frozen = profiles.prepare_assessment_intent(case / "intent", intent, workspace=tmp_path, service=service)
    governed = {"steps": [deepcopy(step)]}
    workflow = source(case / "workflow.json", {"format": "devbench.rna-assessment-workflow/2", "assessment_intent_ref": frozen,
        "assessment_rules_sha256": profiles.assessment_rules_digest(intent), "governed_workflow": governed,
        "governed_workflow_sha256": digest(governed)})
    plan = profiles.finalize_assessment_plan(frozen, workflow, execution_inputs_ref,
        output=case / "finalization", workspace=tmp_path, service=service)
    plan_ref = profiles.prepare_assessment_plan(case / "plan", plan, workspace=tmp_path, service=service)
    if changed_effect:
        table["rows"][0]["effect"] = 4.0
    if missing_eligible_effect:
        table["rows"][1].update(status="not_estimable", effect=None, p_value=None, q_value=None, uncertainty=None)
    if actual_context_change is not None:
        table["alignment"][actual_context_change] = "different-context"
    run = bound_run(service, step, plan_ref, RetainedTableExecutor(table, fail=failure))
    bundle_ref = extract_comparison_outputs([run], plan_ref, artifact_service=service)
    return service, plan_ref, bundle_ref


def assess(tmp_path, **fixture_options):
    assert hasattr(api, "prepare_assessment_comparison") and hasattr(api, "compare_assessment_outputs")
    service, plan_ref, bundle_ref = synthetic_case(tmp_path, **fixture_options)
    comparison = api.prepare_assessment_comparison(tmp_path / "frozen-comparison", plan_ref,
        workspace=tmp_path, artifact_service=service)
    result = api.compare_assessment_outputs(comparison["manifest_path"], comparison["root_hash"],
        output_bundle_ref=bundle_ref["record_ref"], workspace=tmp_path, artifact_service=service,
        output=tmp_path / "assessment", allow_evidence_read=True)
    return service, comparison, bundle_ref, result


def test_v2_compares_real_governed_receipts_and_retains_missing_renderer_review(tmp_path):
    service, comparison, bundle, result = assess(tmp_path)
    record = result["record"]
    assert record["format"] == "devbench.rna-comparison-assessment/2"
    assert record["output_bundle_ref"] == bundle["record_ref"]
    assert record["metric_results"][0]["status"] == "computed"
    assert record["metric_results"][0]["value"] == 0
    assert record["metric_results"][0]["denominators"]["assessed_rows"] == 2
    assert record["numeric_assessments"][0]["decision"] == "agreement"
    assert record["access_control_status"] == "not_established"
    assert "required_renderer_unavailable" in record["limitations"]
    assert "independent_review_unavailable" in record["limitations"]
    assert result["report"] is not None
    loaded = api.load_comparison_assessment(result["manifest_path"], result["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True)
    assert loaded == record


def test_v2_retains_disagreement_under_the_frozen_bound(tmp_path):
    _, _, _, result = assess(tmp_path, changed_effect=True)
    assert result["record"]["metric_results"][0]["value"] == 1
    assert result["record"]["numeric_assessments"][0]["decision"] == "disagreement"


def test_v2_failed_receipt_remains_not_assessable_without_invented_estimates(tmp_path):
    _, _, _, result = assess(tmp_path, failure=True)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert "actual_table_unavailable" in metric["reason_codes"]
    assert result["record"]["numeric_assessments"][0]["decision"] == "not_assessable"
    assert any(item["status"] == "unknown" and "retained_run_failed" in item["reason_codes"]
        for item in result["record"]["diagnostics"])


def test_empty_reference_with_aliased_universe_retains_abstention_and_unique_evidence(tmp_path):
    service, _, bundle, result = assess(tmp_path, empty_reference=True)
    record = result["record"]
    plan = profiles.load_assessment_plan(record["assessment_plan_ref"], workspace=tmp_path, service=service)
    intent = profiles.load_assessment_intent(plan["intent_ref"], workspace=tmp_path, service=service)
    reference = profiles.load_reference_result_set(record["reference_result_set_ref"],
        workspace=tmp_path, service=service, allow_evidence_read=True)
    assert reference["findings"] == []
    assert reference["universe_ref"] == intent["universe_policy_ref"]
    metric = record["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert metric["denominators"] == {"assessed_rows": 0}
    assert metric["reason_codes"] == ["required_reference_claim_unavailable"]
    assert record["numeric_assessments"][0]["decision"] == "not_assessable"
    spec = intent["profile_specs"][0]["metric_specs"][0]
    universe = profiles.read_reference_json(intent["universe_policy_ref"], workspace=tmp_path, service=service)
    assert metric["evidence_refs"] == [bundle["record_ref"], record["reference_result_set_ref"],
        spec["parameter_record_ref"], spec["comparability_policy_ref"], plan["resolved_capabilities_ref"],
        intent["universe_policy_ref"], universe["review_record_ref"]]
    assert api.load_comparison_assessment(result["manifest_path"], result["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True) == record


def test_v2_rejects_self_reported_outputs_before_reference_release(tmp_path, monkeypatch):
    assert hasattr(api, "compare_assessment_outputs")
    service, plan_ref, _ = synthetic_case(tmp_path)
    comparison = api.prepare_assessment_comparison(tmp_path / "frozen-comparison", plan_ref,
        workspace=tmp_path, artifact_service=service)
    def forbidden(*args, **kwargs):
        pytest.fail("Reference outcomes were read before trusted output validation")
    monkeypatch.setattr(profiles, "load_reference_result_set", forbidden)
    with pytest.raises(ValueError, match="reference|immutable|bundle"):
        api.compare_assessment_outputs(comparison["manifest_path"], comparison["root_hash"],
            output_bundle_ref={"value": 99, "status": "success"}, workspace=tmp_path, artifact_service=service,
            output=tmp_path / "assessment", allow_evidence_read=True)


def test_review_projection_contains_actual_and_source_evidence_not_status_labels(tmp_path):
    assert hasattr(api, "assessment_review_evidence")
    service, _, _, result = assess(tmp_path)
    evidence = api.assessment_review_evidence(result["manifest_path"], result["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True)
    assert {row["role"] for row in evidence["gene_tables"]} == {"actual", "reference"}
    actual = next(row for row in evidence["gene_tables"] if row["role"] == "actual")
    reference = next(row for row in evidence["gene_tables"] if row["role"] == "reference")
    assert actual["table"]["rows"][0]["effect"] == reference["table"]["rows"][0]["effect"] == 2.0
    assert actual["table"]["rows"][2]["status"] == "filtered"
    assert reference["table"]["family"]["complete"] is True
    assert reference["table"]["rows"][0]["p_value"] is None
    assert evidence["source_contexts"][0]["excerpt"]["locator"]["text"]
    assert evidence["source_contexts"][0]["context_review"]["checks"]["negation"]["state"] == "supported"
    assert all(row["source_evidence_refs"] for row in evidence["gene_tables"])


def test_unimplemented_question_profile_is_not_forced_into_gene_effect_comparison(tmp_path):
    _, _, _, result = assess(tmp_path, selected_profile="measured_time")
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert "unsupported_profile_comparator" in metric["reason_codes"]


def test_incomplete_reference_family_stays_not_assessable_under_current_formula(tmp_path):
    _, _, _, result = assess(tmp_path, reference_complete=False)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert "incomplete_tested_family" in metric["reason_codes"]


def test_review_projection_does_not_borrow_a_review_for_unmatched_fabricated_quote(tmp_path):
    service, _, _, result = assess(tmp_path, extra_locator=True)
    evidence = api.assessment_review_evidence(result["manifest_path"], result["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True)
    assert all(item["excerpt"]["excerpt_id"] != "unreviewed-quote" for item in evidence["source_contexts"])
    assert any(item["kind"] == "source_claim" and "unmatched" in item["reason"] for item in evidence["omitted_evidence"])


@pytest.mark.parametrize("field", ["contrast", "cohort", "processing", "shrinkage"])
def test_gene_set_branch_checks_context_before_attempting_selection(tmp_path, field):
    _, _, _, result = assess(tmp_path, required_formula="gene_set_f1", actual_context_change=field)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    reason = "frozen_estimand_alignment:" + field if field in {"contrast", "cohort"} else "incompatible:" + field
    assert reason in metric["reason_codes"]


@pytest.mark.parametrize("applicability", ["not_applicable", "unsupported_input"])
def test_frozen_resolved_profile_applicability_cannot_be_overridden_by_matching_outputs(tmp_path, applicability):
    service, _, _, result = assess(tmp_path, frozen_applicability=applicability)
    plan = profiles.load_assessment_plan(result["record"]["assessment_plan_ref"], workspace=tmp_path, service=service)
    resolved = profiles.read_reference_json(plan["resolved_capabilities_ref"], workspace=tmp_path, service=service)
    assert resolved["profiles"][0]["applicability"] == applicability
    metric = result["record"]["metric_results"][0]
    assert metric["status"] != "computed" and metric["value"] is None
    assert "frozen_profile_applicability:" + applicability in metric["reason_codes"]
    assert result["record"]["numeric_assessments"][0]["decision"] == "not_assessable"


@pytest.mark.parametrize("change,reason", [
    ("unknown", "unsupported_universe_policy_adapter"), ("rule_id", "frozen_universe_rule_id_differs"),
    ("different_ids", "frozen_universe_eligible_family_differs"),
    ("different_exclusions", "frozen_universe_excluded_membership_differs"),
    ("mapping", "frozen_universe_mapping_differs"), ("missing_review", "frozen_universe_review_unavailable"),
    ("different_review_ids", "frozen_universe_review_differs"),
])
def test_unsupported_or_conflicting_frozen_universe_rules_abstain(tmp_path, change, reason):
    _, _, _, result = assess(tmp_path, universe_change=change)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert reason in metric["reason_codes"]
    assert result["record"]["numeric_assessments"][0]["decision"] == "not_assessable"


@pytest.mark.parametrize("field,alignment_field", [
    ("estimand_id", "estimand_id"), ("population", "cohort"),
    ("biological_unit.independent_unit", "biological_unit"), ("relationship.definition", "contrast"),
    ("measurement.units", "effect_scale"), ("population_unknown", "cohort"),
])
def test_mutually_matching_tables_cannot_relabel_a_different_or_unresolved_frozen_estimand(tmp_path, field, alignment_field):
    _, _, _, result = assess(tmp_path, frozen_estimand_change=field)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert "frozen_estimand_alignment:" + alignment_field in metric["reason_codes"]
    assert result["record"]["numeric_assessments"][0]["decision"] == "not_assessable"


def test_full_family_policy_abstains_when_an_eligible_effect_is_missing(tmp_path):
    _, _, _, result = assess(tmp_path, missing_eligible_effect=True)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and metric["value"] is None
    assert "frozen_universe_missing_eligible_estimate" in metric["reason_codes"]
    assert result["record"]["numeric_assessments"][0]["decision"] == "not_assessable"


def test_review_evidence_includes_frozen_question_methods_and_actual_design_context(tmp_path):
    service, _, _, result = assess(tmp_path)
    evidence = api.assessment_review_evidence(result["manifest_path"], result["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True)
    assert "method_context" in evidence
    context = evidence["method_context"]
    assert context["status"] == "available"
    assert context["question"]["text"] == "Compare treatment-associated expression"
    assert context["requirements"][0]["origin"] == "fixture"
    plan = profiles.load_assessment_plan(result["record"]["assessment_plan_ref"], workspace=tmp_path, service=service)
    step = profiles.read_reference_json(plan["workflow_ref"], workspace=tmp_path, service=service)["governed_workflow"]["steps"][0]
    assert context["methods"][0]["method_version_id"] == step["method_version_id"]
    assert context["methods"][0]["parameters"] == step["parameters"]
    assert context["input_design"]["design"]["contrasts"] == [{"field": "group", "reference": "A", "target": "B"}]
    assert context["input_design"]["audit"]["blockers"] == []
    assert context["scope"] == "retained_method_context_not_independent_scientific_validation"
    def no_path_keys(value):
        if isinstance(value, dict):
            return "path" not in value and all(no_path_keys(child) for child in value.values())
        return not isinstance(value, list) or all(no_path_keys(child) for child in value)
    assert no_path_keys(context)


def optional_roles_method_context(tmp_path, *, method_parameters=None, omit_roles=True, return_receipt=False):
    """Verified /1 optional-role data and a source-frozen descriptive context draft.

    This internal helper fixture supplies the two consumed refs, without claiming
    final-plan admission: trial finalization has its own stricter role descriptor.
    """
    from tests.test_rna_execution_inputs import inputs
    service, binding, _, _ = inputs(tmp_path)
    if omit_roles:
        binding["declarations"].pop("metadata_roles")
    bridge = prepare_execution_inputs(binding, ports(), artifact_service=service, output=tmp_path / "bridge")
    record = profiles.read_reference_json(bridge["record_ref"], workspace=tmp_path, service=service)
    contract, code, lock = custom(tmp_path)
    contract["inputs"] = {name: {"artifact_type": "json", "required": True} for name in record["execution_refs"]}
    method_parameters = {} if method_parameters is None else deepcopy(method_parameters)
    properties = {}
    for name, value in method_parameters.items():
        properties[name] = {"type": "integer"} if type(value) is int else {"type": "object", "additionalProperties": False,
            "properties": {key: {"type": "integer" if type(child) is int else "string"} for key, child in value.items()},
            "required": list(value)}
    contract["parameters"] = {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}
    method = service.register_method(contract, source=code, environment_lock=lock, actor="synthetic-test")
    analysis = service.create_analysis("Optional-role context fixture", actor="synthetic-test")
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-test")
    step = service.add_step(branch["branch_id"], method["version_id"],
        {name: reference["artifact_id"] for name, reference in record["execution_refs"].items()}, method_parameters, actor="synthetic-test")
    intent = intent_fixture(tmp_path, dataset_binding=binding)
    frozen = profiles.prepare_assessment_intent(tmp_path / "intent", intent, workspace=tmp_path, service=service)
    governed = {"steps": [deepcopy(step)]}
    workflow = source(tmp_path / "workflow.json", {"format": "devbench.rna-assessment-workflow/2",
        "assessment_intent_ref": frozen, "assessment_rules_sha256": profiles.assessment_rules_digest(intent),
        "governed_workflow": governed, "governed_workflow_sha256": digest(governed)})
    plan = {"workflow_ref": workflow, "execution_inputs_ref": bridge["record_ref"]}
    context = api._assessment_method_context(plan, intent, workspace=tmp_path, artifact_service=service)
    assert service.list_records("run") == []
    if return_receipt:
        assert service.get("step", step["step_id"]) == step
        return context, step, service.method(method["version_id"])["contract"]
    return context


def test_optional_unknown_metadata_roles_project_incomplete_compatible_method_context(tmp_path):
    from devbench.analysis.scientific_review import _method_context, _method_context_available
    context = optional_roles_method_context(tmp_path)
    assert context["input_design"]["metadata_roles"] == {}
    assert context["status"] == "not_assessed"
    assert any(item["reason"] == "actual_input_metadata_roles_unavailable" for item in context["omitted_evidence"])
    retained = _method_context(context)
    assert retained == context
    assert _method_context_available(retained) is False


def test_retained_unsupported_parameter_adapter_yields_review_omissions_instead_of_key_error(tmp_path):
    service, _, _, result = assess(tmp_path, malformed_parameters=True)
    metric = result["record"]["metric_results"][0]
    assert metric["status"] == "not_assessable" and "unsupported_metric_parameter_adapter" in metric["reason_codes"]
    evidence = api.assessment_review_evidence(result["manifest_path"], result["root_hash"],
        workspace=tmp_path, artifact_service=service, allow_evidence_read=True)
    assert evidence["gene_tables"] == []
    assert any(item["reason"] == "unsupported_metric_parameter_adapter" for item in evidence["omitted_evidence"])


@pytest.mark.parametrize("parameters", [{"seed": 7}, {"settings": {"expected_scope": "Synthetic fixture only"}}])
def test_review_parameter_projection_applies_existing_public_guard_and_preserves_actual_receipt(tmp_path, parameters):
    from devbench.analysis.scientific_review import _method_context, _method_context_available
    context, step, contract = optional_roles_method_context(tmp_path, method_parameters=parameters,
        omit_roles=False, return_receipt=True)
    assert context["methods"][0]["parameters"] == {}
    assert context["methods"][0]["omitted_parameters"] == sorted(parameters)
    assert context["status"] == "not_assessed"
    assert any(item["reason"].startswith("method_parameters_omitted:") for item in context["omitted_evidence"])
    assert _method_context_available(_method_context(context)) is False
    assert step["parameters"] == parameters
    assert set(contract["parameters"]["properties"]) == set(parameters)
