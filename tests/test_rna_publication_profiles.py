"""Synthetic closed-contract, freeze and information-projection regressions."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from devbench.analysis import rna_publication_profiles as profiles
from devbench.analysis.checkpoints import digest


def source(path, value):
    path = Path(path).resolve()
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    path.write_bytes(body)
    return {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}


def estimand(tmp_path, *, relationship="contrast", entity="gene"):
    requirement = source(tmp_path / "requirement.json", {"format": "devbench.rna-requirement-record/2", "requirements": [
        {"requirement_id": "question", "origin": "fixture", "name": "question", "value": "Compare treatment-associated expression",
         "evidence": "Synthetic engineering fixture; not a user scientific requirement"}]})
    return {
        "format": "devbench.rna-estimand/2", "estimand_id": "treatment-contrast",
        "requirement_sources": [{"origin": "fixture", "record_ref": requirement,
                                 "requirement_ids": ["question"]}],
        "assay": "bulk RNA sequencing",
        "measurement": {"quantity": "gene expression", "units": "log2 fold change", "scale": "normalized_expression"},
        "population": "synthetic independent donors", "biological_unit": {
            "independent_unit": "donor", "dependent_unit": "specimen", "dependence": "independent"},
        "entity_type": entity, "relationship": {"kind": relationship, "definition": "treated versus control"},
        "time_reference": "collection day" if relationship in {"temporal", "composed"} else None, "denominator": "all eligible measured genes",
        "adjustment": {"covariates": [], "assumptions": ["identifiable design"], "unknowns": []},
        "claim_scope": "association", "unknowns": [] if relationship in {"temporal", "composed"} else [{"field": "time_reference", "reason": "No time objective"}],
    }


def metric(tmp_path):
    return {
        "metric_id": "gene_effect_absolute_difference", "formula_version": "1",
        "units": "log2 fold change", "parameter_record_ref": source(tmp_path / "parameters.json", {"parameters": {}}),
        "comparability_policy_ref": source(tmp_path / "comparability.json", {"permitted_differences": []}),
        "universe_rule_id": "jointly_estimable", "uncertainty_policy": {
            "kind": "report_only", "rationale": "Report source uncertainty without inventing intervals"},
        "acceptance_policy": {"kind": "report_only", "operator": None, "bound": None, "rationale": "Descriptive comparison only", "source_ref": None},
        "required": True,
    }


def profile(tmp_path, profile_id="mean_expression"):
    return {
        "profile_id": profile_id, "profile_version": "2",
        "required_input_capabilities": ["bulk_gene_expression", "independent_biological_units", "identifiable_design"],
        "applicability_rules": [{"predicate_id": "relationship_in", "values": ["contrast"]},
                                {"predicate_id": "entity_type_in", "values": ["gene"]}],
        "metric_specs": [metric(tmp_path)], "required_output_schemas": ["devbench.rna-gene-table/2"],
        "required_plot_kinds": ["effect_scatter"], "permitted_claim_scopes": ["association"],
        "missingness_policy": "report_and_abstain",
    }


def fixture(tmp_path, *, dataset_binding=None):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input

    service = MethodService(tmp_path / "methods")
    if dataset_binding is None:
        samples = ["s1", "s2", "s3", "s4"]
        matrix = source(tmp_path / "matrix.json", {"gene_ids": ["g1", "g2"], "sample_ids": samples,
            "matrix": [[1.0, 2.25, 3.5, 4.0], [5, 6, 7, 8]]})
        metadata = source(tmp_path / "metadata.json", {"samples": [{"sample_id": key, "group": "A" if i < 2 else "B",
            "unit": "u" + str(i), "batch": "b" + str(i % 2)} for i, key in enumerate(samples)]})
        refs = {name: service.import_artifact(Path(ref["path"]), artifact_type="json", actor="synthetic-test")
                for name, ref in (("matrix", matrix), ("metadata", metadata))}
        dataset_binding = {"format": "devbench.bulk-rna-binding/1", "matrix_artifact_id": refs["matrix"]["artifact_id"],
            "metadata_artifact_id": refs["metadata"]["artifact_id"], "declarations": {
                "format": "devbench.bulk-rna-input/1", "representation": "normalized_expression", "axes": ["gene", "sample"],
                "provenance": {"source": "synthetic engineering fixture"}, "biological_independence": {"independent_units": True},
                "metadata_roles": {"condition": "group", "biological_unit": "unit", "batch": "batch"}}}
    estimate = estimand(tmp_path)
    question = source(tmp_path / "question.json", {"text": "Compare treatment-associated expression"})
    data = source(tmp_path / "dataset.json", {"format": "devbench.rna-trial-dataset/2", "binding": dataset_binding,
        "classification": "synthetic", "inspection_sha256": digest(inspect_registered_bulk_rna_input(service, dataset_binding)),
        "access_record": source(tmp_path / "data-access.json", {"scope": "synthetic fixture access; no execution authority"})})
    exposure = source(tmp_path / "exposure.json", {"prior_outcome_access": "unknown",
        "public_outcomes_accessible": "yes", "workflow_author_outcome_access": "unknown", "limitations": "Projection does not establish blinding"})
    analyst = source(tmp_path / "analyst.json", {
        "format": "devbench.rna-analyst-projection/2", "mode": "independent_method_selection",
        "question_ref": question, "dataset_ref": data, "estimand": estimate,
        "permitted_method_excerpts": [], "required_output_schemas": ["devbench.rna-gene-table/2"],
        "omitted_evidence": ["reference outcomes", "evaluator source paths"],
        "scope": "context_projection_not_os_isolation", "limitations": "Public outcomes remain accessible"})
    reference = source(tmp_path / "reference.json", {"format": "synthetic-reference/2", "expected_effects": {"ref-gene": 99}})
    evaluator = source(tmp_path / "evaluator.json", {
        "format": "devbench.rna-evaluator-projection/2", "reference_bundle_ref": reference,
        "exposure_ref": exposure, "required_claim_ids": ["effect-claim"],
        "omitted_evidence": [], "scope": "context_projection_not_os_isolation",
        "limitations": "Reference is synthetic and not scientific validation"})
    intent = {
        "format": "devbench.rna-assessment-intent/2", "intent_id": "synthetic-intent", "mode": "independent_method_selection",
        "question_ref": question, "dataset_ref": data, "reference_bundle_ref": reference, "exposure_ref": exposure,
        "estimand": estimate, "profile_specs": [profile(tmp_path)],
        "universe_policy_ref": source(tmp_path / "universe.json", {"rule_id": "jointly_estimable"}),
        "required_claim_ids": ["effect-claim"], "analyst_projection_ref": analyst, "evaluator_projection_ref": evaluator,
        "decision_policy": {"kind": "report_only", "rationale": "No winner or clinical promotion"},
        "limitations": ["Synthetic closed contracts do not qualify a live route"],
    }
    return intent


def finalization_inputs(tmp_path, intent_ref, intent):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.rna_execution_inputs import prepare_execution_inputs

    from tests.test_extensible_methods import custom
    service = MethodService(tmp_path / "methods")
    binding = json.loads(Path(intent["dataset_ref"]["path"]).read_bytes())["binding"]
    ports = {"format": "devbench.rna-execution-ports/2", "ports": {
        "counts": {"source": "matrix", "artifact_type": "json", "encoding": "json", "required": True, "parameter": "counts_path"},
        "metadata": {"source": "metadata", "artifact_type": "json", "encoding": "json", "required": True, "parameter": "metadata_path"}},
        "accepted_representations": ["raw_counts", "normalized_expression"], "objective": {"intent": "descriptive"},
        "design": {"contrasts": [], "covariates": [], "chronological_field": None, "chronological_order": []},
        "string_columns": ["sample_id", "group", "unit", "batch"]}
    bridge = prepare_execution_inputs(binding, ports, artifact_service=service, output=tmp_path / "bridge")
    bridge_record = json.loads(Path(bridge["record_ref"]["path"]).read_bytes())
    contract, code, lock = custom(tmp_path)
    contract["inputs"] = {name: {"artifact_type": "json", "required": True} for name in bridge_record["execution_refs"]}
    contract["parameters"] = {"type": "object", "additionalProperties": False, "properties": {}, "required": []}
    method = service.register_method(contract, source=code, environment_lock=lock, actor="synthetic-test")
    analysis = service.create_analysis("Synthetic assessment freeze", actor="synthetic-test")
    branch = service.create_branch(analysis["analysis_id"], analysis["revision_id"], actor="synthetic-test")
    step = service.add_step(branch["branch_id"], method["version_id"],
        {name: ref["artifact_id"] for name, ref in bridge_record["execution_refs"].items()}, {}, actor="synthetic-test")
    governed = {"steps": [step]}
    workflow = {"format": "devbench.rna-assessment-workflow/2", "assessment_intent_ref": intent_ref,
        "assessment_rules_sha256": profiles.assessment_rules_digest(intent), "governed_workflow": governed,
        "governed_workflow_sha256": digest(governed)}
    return source(tmp_path / "workflow.json", workflow), bridge["record_ref"], service


def test_intent_is_closed_and_has_no_workflow_or_future_output_identity(tmp_path):
    value = fixture(tmp_path)
    assert profiles.validate_assessment_intent(value, workspace=tmp_path) == value
    for field in ("workflow_ref", "output_sha256", "chosen_method", "threshold"):
        bad = deepcopy(value)
        bad[field] = "premature"
        with pytest.raises(ValueError):
            profiles.validate_assessment_intent(bad, workspace=tmp_path)


@pytest.mark.parametrize("mode", ["exact_reproduction", "independent_method_selection", "external_validation"])
def test_objective_modes_are_explicit_without_method_or_threshold_defaults(tmp_path, mode):
    value = fixture(tmp_path)
    value["mode"] = mode
    analyst = json.loads(Path(value["analyst_projection_ref"]["path"]).read_bytes())
    analyst["mode"] = mode
    value["analyst_projection_ref"] = source(tmp_path / "analyst.json", analyst)
    validated = profiles.validate_assessment_intent(value, workspace=tmp_path)
    assert validated["decision_policy"]["kind"] == "report_only"
    assert validated["profile_specs"][0]["metric_specs"][0]["acceptance_policy"]["bound"] is None


@pytest.mark.parametrize("change", ["unknown_profile", "unknown_formula", "unknown_predicate", "absent_units"])
def test_unregistered_profiles_formulas_rules_and_absent_units_fail(tmp_path, change):
    value = fixture(tmp_path)
    selected = value["profile_specs"][0]
    if change == "unknown_profile":
        selected["profile_id"] = "model_created_family"
    elif change == "unknown_formula":
        selected["metric_specs"][0]["formula_version"] = "eval(reference*999)"
    elif change == "unknown_predicate":
        selected["applicability_rules"][0]["predicate_id"] = "eval"
    else:
        selected["metric_specs"][0]["units"] = ""
    with pytest.raises(ValueError):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_agreement_bound_requires_finite_value_rationale_and_retained_source(tmp_path):
    value = metric(tmp_path)
    value["acceptance_policy"] = {"kind": "agreement_bound", "operator": "less_equal", "bound": .25, "rationale": "", "source_ref": None}
    with pytest.raises(ValueError):
        profiles.validate_metric_spec(value, workspace=tmp_path)
    value["acceptance_policy"] = {"kind": "agreement_bound", "operator": "less_equal", "bound": .25,
        "rationale": "An explicit synthetic measurement precision policy", "source_ref": value["parameter_record_ref"]}
    assert profiles.validate_metric_spec(value, workspace=tmp_path)["acceptance_policy"]["bound"] == .25
    value["acceptance_policy"]["operator"] = None
    with pytest.raises(ValueError, match="bound|operator"):
        profiles.validate_metric_spec(value, workspace=tmp_path)
    value["acceptance_policy"]["operator"] = "less_equal"
    value["acceptance_policy"]["bound"] = float("nan")
    with pytest.raises(ValueError):
        profiles.validate_metric_spec(value, workspace=tmp_path)


@pytest.mark.parametrize("family,kind,entity,capabilities", [
    ("mean_expression", "contrast", "gene", ["bulk_gene_expression", "independent_biological_units", "identifiable_design"]),
    ("measured_time", "temporal", "gene", ["bulk_gene_expression", "measured_time", "independent_biological_units", "identifiable_design"]),
    ("pathway", "enrichment", "pathway", ["ranked_gene_statistics", "tested_gene_universe", "versioned_gene_sets"]),
    ("composed", "composed", "gene", ["bulk_gene_expression", "measured_time", "independent_biological_units", "identifiable_design", "ranked_gene_statistics", "tested_gene_universe", "versioned_gene_sets"]),
])
def test_scientific_applicability_never_claims_computational_qualification(tmp_path, family, kind, entity, capabilities):
    estimate = estimand(tmp_path, relationship=kind, entity=entity)
    results = profiles.select_applicable_profiles(estimate, capabilities)
    result = next(item for item in results if item["profile_id"] == family)
    assert result["applicability"] == "applicable"
    assert result["computationally_supported"] is False
    assert result["status"] == "unsupported_profile"
    assert result["reason_codes"]
    assert not any("winner" in item for item in results)


@pytest.mark.parametrize("family,kind,entity", [("splicing", "contrast", "exon"), ("cell_abundance", "composition", "cell")])
def test_gene_totals_cannot_satisfy_usage_or_per_cell_capabilities(tmp_path, family, kind, entity):
    results = profiles.select_applicable_profiles(estimand(tmp_path, relationship=kind, entity=entity), ["bulk_gene_expression"])
    result = next(item for item in results if item["profile_id"] == family)
    assert result["status"] == "unsupported_input"
    assert result["applicability"] == "unsupported_input"
    assert result["missing_capabilities"]
    assert result["computationally_supported"] is False


def test_unknown_measurement_units_remain_not_assessable(tmp_path):
    estimate = estimand(tmp_path)
    estimate["measurement"]["units"] = None
    estimate["unknowns"].append({"field": "measurement.units", "reason": "Unknown source measurement"})
    result = next(item for item in profiles.select_applicable_profiles(estimate, ["bulk_gene_expression"])
                  if item["profile_id"] == "mean_expression")
    assert result["status"] == "not_assessable"
    assert "unknown_estimand" in result["reason_codes"]


def test_predicate_value_domains_are_registered_and_fraction_units_cannot_be_relabelled(tmp_path):
    spec = profile(tmp_path)
    spec["applicability_rules"].append({"predicate_id": "measurement_scale_in", "values": ["TPM_as_raw_counts"]})
    with pytest.raises(ValueError):
        profiles.validate_profile_spec(spec, workspace=tmp_path)
    spec = metric(tmp_path)
    spec["metric_id"] = "gene_set_precision"
    spec["units"] = "log2 fold change"
    with pytest.raises(ValueError, match="units"):
        profiles.validate_metric_spec(spec, workspace=tmp_path)


def test_intent_and_plan_seal_loading_retains_unqualified_route(tmp_path):
    intent = fixture(tmp_path)
    frozen = profiles.prepare_assessment_intent(tmp_path / "intent-seal", intent, workspace=tmp_path)
    assert profiles.load_assessment_intent(frozen, workspace=tmp_path) == intent
    workflow, bridge, service = finalization_inputs(tmp_path, frozen, intent)
    plan = profiles.finalize_assessment_plan(frozen, workflow, bridge, output=tmp_path / "final", workspace=tmp_path, service=service)
    assert set(plan) == {"format", "plan_id", "intent_ref", "workflow_ref", "execution_inputs_ref",
                         "resolved_capabilities_ref", "finalization_receipt_ref", "execution_prerequisites_ref"}
    assert profiles.validate_assessment_plan(plan, workspace=tmp_path, service=service) == plan
    prereqs = json.loads(Path(plan["execution_prerequisites_ref"]["path"]).read_bytes())
    assert prereqs["computational_admission"] == "blocked"
    assert prereqs["blockers"]
    sealed = profiles.prepare_assessment_plan(tmp_path / "plan-seal", plan, workspace=tmp_path, service=service)
    assert profiles.load_assessment_plan(sealed, workspace=tmp_path, service=service) == plan


def test_raw_intent_does_not_silently_become_frozen_after_workflow(tmp_path):
    intent = fixture(tmp_path)
    identity = source(tmp_path / "intent.json", intent)
    workflow, bridge, service = finalization_inputs(tmp_path, identity, intent)
    with pytest.raises(ValueError, match="reference"):
        profiles.finalize_assessment_plan(intent, workflow, bridge, output=tmp_path / "late", workspace=tmp_path, service=service)


@pytest.mark.parametrize("change", ["threshold", "formula"])
def test_workflow_cannot_change_frozen_rules(tmp_path, change):
    intent = fixture(tmp_path)
    identity = source(tmp_path / "intent.json", intent)
    workflow, bridge, service = finalization_inputs(tmp_path, identity, intent)
    altered = deepcopy(intent)
    if change == "threshold":
        altered["profile_specs"][0]["metric_specs"][0]["acceptance_policy"]["bound"] = .01
    else:
        altered["profile_specs"][0]["metric_specs"][0]["formula_version"] = "changed/999"
    body = json.loads(Path(workflow["path"]).read_bytes())
    body["assessment_rules_sha256"] = digest(altered)
    workflow = source(tmp_path / "workflow.json", body)
    with pytest.raises(ValueError, match="rules"):
        profiles.finalize_assessment_plan(identity, workflow, bridge, output=tmp_path / "changed", workspace=tmp_path, service=service)


def test_plan_receipt_detects_hash_consistent_rule_replacement(tmp_path):
    intent = fixture(tmp_path)
    identity = source(tmp_path / "intent.json", intent)
    workflow, bridge, service = finalization_inputs(tmp_path, identity, intent)
    plan = profiles.finalize_assessment_plan(identity, workflow, bridge, output=tmp_path / "final", workspace=tmp_path, service=service)
    altered = deepcopy(intent)
    altered["decision_policy"]["rationale"] = "changed after results"
    replacement = source(tmp_path / "replacement-intent.json", altered)
    plan["intent_ref"] = replacement
    with pytest.raises(ValueError, match="binding|rules|intent"):
        profiles.validate_assessment_plan(plan, workspace=tmp_path, service=service)


@pytest.mark.parametrize("where", ["workflow", "bridge"])
def test_finalization_rejects_post_result_payloads(tmp_path, where):
    intent = fixture(tmp_path)
    identity = source(tmp_path / "intent.json", intent)
    workflow, bridge, service = finalization_inputs(tmp_path, identity, intent)
    ref = workflow if where == "workflow" else bridge
    body = json.loads(Path(ref["path"]).read_bytes())
    body["metric_results"] = {"best_bound": .99}
    changed = source(tmp_path / (where + ".json"), body)
    if where == "workflow":
        workflow = changed
    else:
        bridge = changed
    with pytest.raises(ValueError, match="result|closed|fields"):
        profiles.finalize_assessment_plan(identity, workflow, bridge, output=tmp_path / "after-results", workspace=tmp_path, service=service)


def test_independent_analyst_projection_has_no_evaluator_outcomes_or_source_paths(tmp_path):
    value = fixture(tmp_path)
    analyst = profiles.load_analyst_projection(value["analyst_projection_ref"], workspace=tmp_path)
    serialized = json.dumps(analyst)
    assert "ref-gene" not in serialized
    assert "expected_effects" not in serialized
    assert value["reference_bundle_ref"]["path"] not in serialized
    assert value["evaluator_projection_ref"]["path"] not in serialized
    assert analyst["scope"] == "context_projection_not_os_isolation"
    leaked = deepcopy(analyst)
    leaked["expected_effects"] = {"ref-gene": 99}
    value["analyst_projection_ref"] = source(tmp_path / "leak.json", leaked)
    with pytest.raises(ValueError):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_projection_cannot_alias_evaluator_reference_in_a_permitted_field(tmp_path):
    value = fixture(tmp_path)
    leaked = json.loads(Path(value["analyst_projection_ref"]["path"]).read_bytes())
    leaked["question_ref"] = value["reference_bundle_ref"]
    value["analyst_projection_ref"] = source(tmp_path / "leak.json", leaked)
    with pytest.raises(ValueError, match="projection|binding|leak"):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_nested_evaluator_source_path_cannot_leak_through_permitted_excerpt_text(tmp_path):
    value = fixture(tmp_path)
    # This custody regression is independent of the acceptance-policy refinement.
    value["profile_specs"][0]["metric_specs"] = []
    outcome_source = source(tmp_path / "outcome-source.json", {"reference_gene": "g-hidden", "value": 7})
    value["reference_bundle_ref"] = source(tmp_path / "reference.json", {"retained_outcome_source": outcome_source})
    evaluator = json.loads(Path(value["evaluator_projection_ref"]["path"]).read_bytes())
    evaluator["reference_bundle_ref"] = value["reference_bundle_ref"]
    value["evaluator_projection_ref"] = source(tmp_path / "evaluator.json", evaluator)
    analyst = json.loads(Path(value["analyst_projection_ref"]["path"]).read_bytes())
    analyst["permitted_method_excerpts"] = [{"excerpt_id": "methods", "purpose": "methods", "citation_id": "paper",
        "text": "Methods representation at " + outcome_source["path"], "limitations": "Synthetic path alias"}]
    value["analyst_projection_ref"] = source(tmp_path / "analyst.json", analyst)
    with pytest.raises(ValueError, match="evaluator.*path|leak"):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_ref_shapes_hashes_symlinks_and_scope_are_validated(tmp_path):
    value = fixture(tmp_path)
    for replacement in [
        {**value["question_ref"], "root_hash": "a" * 64},
        {"path": "relative.json", "sha256": "a" * 64, "size_bytes": 1},
        {**value["question_ref"], "sha256": "a" * 64},
    ]:
        altered = deepcopy(value)
        altered["question_ref"] = replacement
        with pytest.raises(ValueError):
            profiles.validate_assessment_intent(altered, workspace=tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(value["question_ref"]["path"])
    value["question_ref"]["path"] = str(link)
    with pytest.raises(ValueError, match="symlink|canonical|regular"):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_ref_content_changed_without_identity_update_fails(tmp_path):
    value = fixture(tmp_path)
    Path(value["exposure_ref"]["path"]).write_text("changed evidence")
    with pytest.raises(ValueError, match="digest|byte|hash"):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_hostile_nesting_and_container_bounds_are_rejected_before_schema_walk(tmp_path):
    value = fixture(tmp_path)
    nested = {}
    cursor = nested
    for _ in range(100):
        cursor["nested"] = {}
        cursor = cursor["nested"]
    value["limitations"] = nested
    with pytest.raises(ValueError, match="bound|nesting|JSON"):
        profiles.validate_assessment_intent(value, workspace=tmp_path)
    value = fixture(tmp_path)
    value["profile_specs"] *= 1000
    with pytest.raises(ValueError, match="bound|JSON"):
        profiles.validate_assessment_intent(value, workspace=tmp_path)


def test_noncomputed_metric_value_stays_null_and_typed_direction_is_permitted(tmp_path):
    ref = source(tmp_path / "evidence.json", {"evidence": "synthetic"})
    result = {"metric_id": "gene_effect_direction_match", "profile_id": "mean_expression", "estimand_id": "treatment-contrast",
        "status": "computed", "value": {"kind": "direction", "value": "same"}, "units": "direction",
        "denominators": {"eligible": 1, "matched": 1}, "universe_ref": ref, "exclusions_ref": ref,
        "uncertainty_ref": None, "reason_codes": [], "evidence_refs": [ref]}
    assert profiles.validate_metric_result(result, workspace=tmp_path) == result
    result["status"] = "not_assessable"
    with pytest.raises(ValueError):
        profiles.validate_metric_result(result, workspace=tmp_path)
    result["value"] = None
    result["reason_codes"] = ["missing_uncertainty"]
    assert profiles.validate_metric_result(result, workspace=tmp_path)["value"] is None


def test_fraction_result_cannot_replace_numeric_agreement_with_a_typed_direction(tmp_path):
    ref = source(tmp_path / "evidence.json", {"evidence": "synthetic"})
    result = {"metric_id": "gene_effect_direction_agreement", "profile_id": "mean_expression", "estimand_id": "treatment-contrast",
        "status": "computed", "value": {"kind": "direction", "value": "same"}, "units": "fraction",
        "denominators": {"eligible": 1}, "universe_ref": ref, "exclusions_ref": ref,
        "uncertainty_ref": None, "reason_codes": [], "evidence_refs": [ref]}
    with pytest.raises(ValueError, match="fraction|numeric"):
        profiles.validate_metric_result(result, workspace=tmp_path)
    result["value"] = 1.01
    with pytest.raises(ValueError, match="fraction"):
        profiles.validate_metric_result(result, workspace=tmp_path)


def test_direction_descriptor_has_no_hidden_agreement_threshold(tmp_path):
    spec = metric(tmp_path)
    spec.update(metric_id="gene_effect_direction_match", units="direction")
    assert profiles.validate_metric_spec(spec, workspace=tmp_path) == spec
    spec["acceptance_policy"] = {"kind": "agreement_bound", "operator": "less_equal", "bound": .2,
        "rationale": "A numeric bound cannot decide a category", "source_ref": spec["parameter_record_ref"]}
    with pytest.raises(ValueError, match="direction|report"):
        profiles.validate_metric_spec(spec, workspace=tmp_path)


def test_reference_estimand_mismatch_rejects_comparison_eligibility(tmp_path):
    intended = estimand(tmp_path)
    wrong = deepcopy(intended)
    wrong["relationship"]["definition"] = "another contrast"
    reference = source(tmp_path / "reference-estimand.json", wrong)
    evidence = source(tmp_path / "evidence.json", {"synthetic": True})
    finding = {"claim_id": "effect-claim", "estimand_ref": reference, "entity_refs": [evidence],
        "source_locator_refs": [evidence], "extraction_record_ref": evidence,
        "value_or_censoring": {"kind": "exact", "value": 1.2, "units": "log2 fold change"},
        "uncertainty_ref": None, "tested_family_ref": None, "reporting_completeness": "highlights_only",
        "context_review_ref": evidence, "limitations": ["No complete tested family"]}
    assert profiles.validate_reference_finding(finding, workspace=tmp_path)["tested_family_ref"] is None
    with pytest.raises(ValueError, match="estimand"):
        profiles.validate_reference_finding(finding, estimand=intended, workspace=tmp_path)


def test_reference_set_default_denial_and_incomplete_scope_are_explicit(tmp_path):
    estimate = estimand(tmp_path)
    estimate_ref = source(tmp_path / "estimand.json", estimate)
    from tests.test_rna_publication_evidence import EXPOSURE
    exposure = source(tmp_path / "exposure.json", EXPOSURE)
    result = {"format": "devbench.rna-reference-result-set/2", "reference_set_id": "synthetic-reference",
              "publication_ref": reference_publication(tmp_path)[0], "source_collection_ref": None,
              "estimand_ref": estimate_ref, "findings": [], "universe_ref": None, "exposure_ref": exposure,
              "limitations": ["No source findings assessed; no exhaustive family"]}
    retained = source(tmp_path / "result-set.json", result)
    with pytest.raises(PermissionError, match="authority"):
        profiles.load_reference_result_set(retained, workspace=tmp_path)
    assert profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True) == result
    for name in ("method_winner", "complete", "truth_accuracy"):
        changed = deepcopy(result)
        changed[name] = True
        with pytest.raises(ValueError):
            profiles.validate_reference_result_set(changed, workspace=tmp_path)


def reference_publication(tmp_path, *, suffix="one"):
    """Explicit artificial article grounding, retained before any numerical comparison."""
    from devbench.analysis import rna_publication_evidence as api
    from tests.test_rna_publication_evidence import locator, EXPOSURE

    identity = {"doi": "10.1234/profile-" + suffix, "pmid": "123401" if suffix == "one" else "123402",
                "pmcid": "PMC123401" if suffix == "one" else "PMC123402", "version": "fixture-v1",
                "title": "Artificial profile article " + suffix}
    url = "https://publication.example.invalid/profile-" + suffix
    text = "g1 effect 1.25 in synthetic donors"
    body = ("\n".join(identity.values()) + "\n" + text + "\n").encode()
    path = tmp_path / ("pub-" + suffix + ".txt")
    path.write_bytes(body)
    body_ref = {"path": str(path.resolve()), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}
    excerpt = {"excerpt_id": "effect", "source_id": "source1", "locator": locator(body, text, "Synthetic outcome"),
               "visibility": "evaluator", "purpose": "outcome"}
    binding = api.prepare_publication_evidence(tmp_path / ("pub-" + suffix), {"identity": identity, "approved_urls": [url]},
        [{"source_id": "source1", "path": body_ref["path"], "sha256": body_ref["sha256"], "source_url": url,
          "identity": identity, "access": "full_text", "identity_locators": {
              key: locator(body, item, "Synthetic identity") for key, item in identity.items()},
          "identity_reviewed_by": "independent-curator", "limitations": "Artificial source; no publisher authenticity"}],
        [excerpt], EXPOSURE, curator_id="independent-curator", participant_id="workflow-author", workspace=tmp_path,
        allow_evidence_read=True)
    return binding, body_ref, excerpt


def reference_packet(tmp_path, *, value=1.25, publication_suffix="one"):
    from devbench.analysis.rna_reference_extraction import canonicalize_reference_number, CONTEXT_REVIEW_FORMAT

    estimate_ref = source(tmp_path / "estimand.json", estimand(tmp_path))
    fake = source(tmp_path / "fake.json", {"self_report": "Value is source backed"})
    publication_ref, body_ref, excerpt = reference_publication(tmp_path, suffix=publication_suffix)
    locator = excerpt["locator"]
    locator_ref = source(tmp_path / "locator.json", {"source_ref": body_ref, "excerpt": excerpt})
    number_ref = source(tmp_path / "number.json", canonicalize_reference_number("1.25", rule="identity", reviewed=True))
    context = {"format": CONTEXT_REVIEW_FORMAT, "review_id": "synthetic-review", "excerpt_id": "effect",
        "source_sha256": body_ref["sha256"], "claim": "g1 effect is 1.25", "proposal_origin": "model_assisted",
        "proposal_author_id": "workflow-author", "reviewed_by": "independent-curator", "checks": {
            key: {"state": "supported", "evidence_locators": [locator], "explanation": "Synthetic independent declaration"}
            for key in ("entity", "population", "contrast", "measurement", "uncertainty", "negation", "scope")},
        "conclusion": "supported", "limitations": "Not authenticated scientific approval"}
    context_ref = source(tmp_path / "context.json", context)
    from tests.test_rna_publication_evidence import EXPOSURE
    exposure = source(tmp_path / "exposure.json", EXPOSURE)
    finding = {"claim_id": "effect-claim", "estimand_ref": estimate_ref, "entity_refs": [fake],
        "source_locator_refs": [locator_ref], "extraction_record_ref": number_ref,
        "value_or_censoring": {"kind": "exact", "value": value, "units": "log2 fold change"},
        "uncertainty_ref": None, "tested_family_ref": None, "reporting_completeness": "highlights_only",
        "context_review_ref": context_ref, "limitations": ["Synthetic self-report disagrees with actual source number"]}
    result = {"format": "devbench.rna-reference-result-set/2", "reference_set_id": "synthetic-reference",
        "publication_ref": publication_ref, "source_collection_ref": None,
        "estimand_ref": estimate_ref, "findings": [finding], "universe_ref": None, "exposure_ref": exposure,
        "limitations": ["No verified scientific evidence"]}
    return source(tmp_path / "result-set.json", result)


def test_unbound_other_article_quotes_cannot_be_trusted_as_reference_results(tmp_path):
    # Baseline-shaped /2 input proves the old accepted-unbound-source behavior, not new-field schema absence.
    reference_packet(tmp_path, publication_suffix="one")
    retained = reference_packet(tmp_path, publication_suffix="two")
    packet = json.loads(Path(retained["path"]).read_bytes())
    packet.pop("publication_ref")
    packet.pop("source_collection_ref")
    packet["limitations"] = ["Other article quotes supplied without any selected publication binding"]
    retained = source(tmp_path / "unbound-result-set.json", packet)
    with pytest.raises(ValueError, match="publication|grounding"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True)


def test_missing_selected_publication_cannot_be_replaced_by_matching_context_labels(tmp_path):
    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    packet.pop("publication_ref")
    packet.pop("source_collection_ref")
    with pytest.raises(ValueError, match="publication|grounding"):
        profiles.validate_reference_result_set(packet, workspace=tmp_path, for_comparison=True)


def test_unbound_reference_exposure_cannot_override_its_retained_article_context(tmp_path):
    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    packet.pop("publication_ref")
    packet.pop("source_collection_ref")
    exposure = json.loads(Path(packet["exposure_ref"]["path"]).read_bytes())
    exposure["workflow_author_outcome_access"] = "yes"
    packet["exposure_ref"] = source(tmp_path / "relabeled-exposure.json", exposure)
    retained = source(tmp_path / "unbound-relabeled-reference.json", packet)
    with pytest.raises(ValueError, match="publication|grounding|exposure"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True)


def test_reference_source_must_belong_to_its_bound_selected_publication(tmp_path):
    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    packet["publication_ref"] = reference_publication(tmp_path, suffix="two")[0]
    retained = source(tmp_path / "wrong-publication.json", packet)
    with pytest.raises(ValueError, match="source|publication|member"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True)


def test_expected_selected_publication_and_collection_membership_are_exact(tmp_path):
    from devbench.analysis import rna_publication_sources as api

    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    selected = packet["publication_ref"]
    other = reference_publication(tmp_path, suffix="two")[0]
    with pytest.raises(ValueError, match="selected publication"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True, expected_publication_ref=other)
    collection = {"format": api.COLLECTION_FORMAT, "collection_id": "other-publication", "curator_id": "independent-curator",
        "participant_id": "workflow-author", "publications": [{"publication_id": "other", "reference": other}],
        "assets": [], "dataset_links": [], "scope": api.COLLECTION_SCOPE}
    packet["source_collection_ref"] = api.prepare_publication_collection(tmp_path / "other-collection", collection,
        workspace=tmp_path, allow_evidence_read=True)
    retained = source(tmp_path / "wrong-collection.json", packet)
    with pytest.raises(ValueError, match="(?i)(collection.*selected publication|publication reference)"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True, expected_publication_ref=selected)


def test_selected_publication_exposure_and_curation_identities_cannot_be_relabelled(tmp_path):
    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    altered = json.loads(Path(packet["exposure_ref"]["path"]).read_bytes())
    altered["workflow_author_outcome_access"] = "yes"
    packet["exposure_ref"] = source(tmp_path / "different-exposure.json", altered)
    retained = source(tmp_path / "conflicting-exposure.json", packet)
    with pytest.raises(ValueError, match="exposure"):
        profiles.load_reference_grounding_binding(retained, workspace=tmp_path, allow_evidence_read=True)
    # Restore the same retained publication exposure rather than modifying the publication artifact.
    from tests.test_rna_publication_evidence import EXPOSURE
    packet["exposure_ref"] = source(tmp_path / "exposure.json", EXPOSURE)
    finding = packet["findings"][0]
    review = json.loads(Path(finding["context_review_ref"]["path"]).read_bytes())
    review["reviewed_by"] = "other-curator"
    finding["context_review_ref"] = source(tmp_path / "other-context.json", review)
    retained = source(tmp_path / "conflicting-curator.json", packet)
    with pytest.raises(ValueError, match="curator|participant|identity"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True)


def test_grounding_metadata_does_not_traverse_unadmitted_finding_refs(tmp_path, monkeypatch):
    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    finding_paths = {ref["path"] for finding in packet["findings"] for ref in (
        finding["extraction_record_ref"], finding["context_review_ref"], *finding["entity_refs"], *finding["source_locator_refs"])}
    original = profiles._read
    def refuse_findings(path, limit):
        if str(path) in finding_paths:
            pytest.fail("Grounding metadata seam traversed an unadmitted finding artifact")
        return original(path, limit)
    monkeypatch.setattr(profiles, "_read", refuse_findings)
    with pytest.raises(PermissionError):
        profiles.load_reference_grounding_binding(retained, workspace=tmp_path)
    assert profiles.load_reference_grounding_binding(retained, workspace=tmp_path, allow_evidence_read=True,
        expected_publication_ref=packet["publication_ref"]) == {
            "publication_ref": packet["publication_ref"], "source_collection_ref": None}


def test_grounding_checks_admitted_locator_overlap_without_finding_traversal(tmp_path, monkeypatch):
    from devbench.analysis import rna_publication_sources as api, rna_publication_evidence as legacy
    from tests.test_rna_publication_sources import asset

    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    finding = packet["findings"][0]
    locator = json.loads(Path(finding["source_locator_refs"][0]["path"]).read_bytes())
    selected = legacy.load_publication_evidence(packet["publication_ref"]["manifest_path"], packet["publication_ref"]["root_hash"],
        workspace=tmp_path, allow_evidence_read=True)
    extra = tmp_path / "overlap-assets"
    extra.mkdir()
    body_asset = asset(extra)
    body_asset.update(asset_id="bodyAsset", source=deepcopy(locator["source_ref"]),
        requested_identity=deepcopy(selected["publication"]["identity"]), resolved_identity=deepcopy(selected["publication"]["identity"]),
        source_url=selected["publication"]["approved_urls"][0], identity_locators=deepcopy(selected["sources"][0]["identity_locators"]))
    for field in ("rights", "status"):
        body_asset[field]["reviewed_by"] = selected["curator_id"]
    body_asset["identity_reviewed_by"] = selected["curator_id"]
    locator["excerpt"]["source_id"] = body_asset["asset_id"]
    locator_ref = source(extra / "admitted-locator.json", locator)
    locator_asset = deepcopy(body_asset)
    locator_asset.update(asset_id="locatorAsset", asset_kind="extracted_json", content_type="application/json",
        source=locator_ref, identity_locators=None, lineage={"parent_source_ref": deepcopy(body_asset["source"]),
            "parent_access": "full_text", "extractor": "reviewed-locator", "extractor_version": "1", "reviewed_by": selected["curator_id"]})
    collection = {"format": api.COLLECTION_FORMAT, "collection_id": "overlap", "curator_id": selected["curator_id"],
        "participant_id": selected["participant_id"], "publications": [{"publication_id": "paper1", "reference": packet["publication_ref"]}],
        "assets": [body_asset, locator_asset], "dataset_links": [], "scope": api.COLLECTION_SCOPE}
    packet["source_collection_ref"] = api.prepare_publication_collection(tmp_path / "overlap-collection", collection,
        workspace=tmp_path, allow_evidence_read=True)
    finding["source_locator_refs"] = [locator_ref]
    finding["tested_family_ref"] = source(tmp_path / "unadmitted-family.json", {"eligible": ["g1"]})
    retained = source(tmp_path / "overlap-result-set.json", packet)
    blocked = {ref["path"] for ref in (finding["extraction_record_ref"], finding["context_review_ref"],
                                     finding["tested_family_ref"], *finding["entity_refs"])}
    read_paths = set()
    for module in (profiles, api, legacy):
        original = module._read
        def track_read(path, limit, original=original):
            read_paths.add(str(path))
            if str(path) in blocked:
                pytest.fail("Grounding metadata traversed an unadmitted numeric/family/context/entity artifact")
            return original(path, limit)
        monkeypatch.setattr(module, "_read", track_read)
    result = profiles.load_reference_grounding_binding(retained, workspace=tmp_path, allow_evidence_read=True,
        expected_publication_ref=packet["publication_ref"])
    assert locator_ref["path"] in read_paths  # Source-integrity verification legitimately reads this admitted overlap.
    assert not blocked & read_paths
    assert result == {"publication_ref": packet["publication_ref"], "source_collection_ref": packet["source_collection_ref"]}
    assert "findings" not in result and "value_or_censoring" not in result and "1.25" not in json.dumps(result)


def test_publication_bound_source_paths_remain_evaluator_only_in_analyst_inputs(tmp_path):
    reference_directory = tmp_path / "reference"
    reference_directory.mkdir()
    retained = reference_packet(reference_directory)
    intent = fixture(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    locator = json.loads(Path(packet["findings"][0]["source_locator_refs"][0]["path"]).read_bytes())
    # Remove finding refs so only the new selected publication binding exposes the source to the scanner.
    packet["findings"] = []
    intent["reference_bundle_ref"] = source(tmp_path / "grounded-reference.json", packet)
    intent["exposure_ref"] = source(tmp_path / "grounded-exposure.json", json.loads(Path(packet["exposure_ref"]["path"]).read_bytes()))
    evaluator = json.loads(Path(intent["evaluator_projection_ref"]["path"]).read_bytes())
    evaluator["reference_bundle_ref"] = intent["reference_bundle_ref"]
    evaluator["exposure_ref"] = intent["exposure_ref"]
    intent["evaluator_projection_ref"] = source(tmp_path / "grounded-evaluator.json", evaluator)
    analyst = json.loads(Path(intent["analyst_projection_ref"]["path"]).read_bytes())
    intent["question_ref"] = source(tmp_path / "leaking-question.json", {"text": "Synthetic question", "source_path": locator["source_ref"]["path"]})
    analyst["question_ref"] = intent["question_ref"]
    intent["analyst_projection_ref"] = source(tmp_path / "grounded-analyst.json", analyst)
    for reference in (intent["exposure_ref"], intent["evaluator_projection_ref"], intent["question_ref"], intent["analyst_projection_ref"]):
        body = Path(reference["path"]).read_bytes()
        assert len(body) == reference["size_bytes"] and hashlib.sha256(body).hexdigest() == reference["sha256"]
    with pytest.raises(ValueError, match="leak|evaluator.*path"):
        profiles.prepare_assessment_intent(tmp_path / "rejected-intent", intent, workspace=tmp_path)
    assert not (tmp_path / "rejected-intent").exists()


def test_reference_set_does_not_trust_hash_valid_self_reported_values(tmp_path):
    retained = reference_packet(tmp_path, value=1.2)
    with pytest.raises(ValueError, match="value|extraction"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True)


@pytest.mark.parametrize("conclusion,state", [("contradicted", "contradicted"), ("not_assessable", "unknown")])
def test_exact_numeric_source_does_not_rescue_contradicted_or_unknown_context(tmp_path, conclusion, state):
    retained = reference_packet(tmp_path)
    packet = json.loads(Path(retained["path"]).read_bytes())
    finding = packet["findings"][0]
    review = json.loads(Path(finding["context_review_ref"]["path"]).read_bytes())
    review["checks"]["scope"]["state"] = state
    review["conclusion"] = conclusion
    finding["context_review_ref"] = source(tmp_path / "context.json", review)
    retained = source(tmp_path / "result-set.json", packet)
    # Structural custody can retain disagreements; scientific comparison cannot trust them.
    assert profiles.validate_reference_result_set(packet, workspace=tmp_path) == packet
    with pytest.raises(ValueError, match="context|assessable|supported"):
        profiles.load_reference_result_set(retained, workspace=tmp_path, allow_evidence_read=True)


@pytest.mark.parametrize("granted_record", ["question", "requirement"])
def test_known_evaluator_paths_cannot_hide_inside_analyst_granted_source_records(tmp_path, granted_record):
    intent = fixture(tmp_path)
    intent["profile_specs"][0]["metric_specs"] = []
    outcome = source(tmp_path / ("e.json" if granted_record.endswith("_key") else "evaluator-outcome.json"),
                     {"reference_gene": "not-an-analyst-input"})
    intent["reference_bundle_ref"] = source(tmp_path / "reference.json", {"retained_outcome_source": outcome})
    evaluator = json.loads(Path(intent["evaluator_projection_ref"]["path"]).read_bytes())
    evaluator["reference_bundle_ref"] = intent["reference_bundle_ref"]
    intent["evaluator_projection_ref"] = source(tmp_path / "evaluator.json", evaluator)
    analyst = json.loads(Path(intent["analyst_projection_ref"]["path"]).read_bytes())
    payload = {"question": "Synthetic question", "resource": outcome["path"]}
    if granted_record.endswith("_key"):
        assert len(outcome["path"]) <= 200, "Key-path fixture must satisfy the existing JSON key bound"
        payload = {"question": "Synthetic question", outcome["path"]: "No evaluator path in values"}
    if granted_record.startswith("question"):
        intent["question_ref"] = source(tmp_path / "question.json", payload)
        analyst["question_ref"] = intent["question_ref"]
    else:
        requirement = json.loads(Path(intent["estimand"]["requirement_sources"][0]["record_ref"]["path"]).read_bytes())
        requirement["requirements"][0]["value"] = payload
        intent["estimand"]["requirement_sources"][0]["record_ref"] = source(tmp_path / "requirement.json", requirement)
        analyst["estimand"] = deepcopy(intent["estimand"])
    intent["analyst_projection_ref"] = source(tmp_path / "analyst.json", analyst)
    with pytest.raises(ValueError, match="projection.*path|leak"):
        profiles.validate_assessment_intent(intent, workspace=tmp_path)


@pytest.mark.parametrize("granted_record", ["question", "requirement"])
def test_json_key_leak(tmp_path, granted_record):
    # The short test name keeps actual fixture paths inside the 200-character key limit.
    test_known_evaluator_paths_cannot_hide_inside_analyst_granted_source_records(tmp_path, granted_record + "_key")


def test_streamed_artifact_custody_does_not_consume_metadata_budget_or_load_matrix(tmp_path, monkeypatch):
    from devbench.analysis.methods import MethodService
    from devbench.analysis.rna_input_contract import MAX_MATRIX_BYTES

    service = MethodService(tmp_path / "methods")
    data = tmp_path / "bounded-matrix.dat"
    data.write_bytes(b"x" * 96)
    reference = service.import_artifact(data, artifact_type="file", actor="synthetic-test")
    monkeypatch.setattr(profiles, "MAX_AGGREGATE_BYTES", 32)
    monkeypatch.setattr(profiles, "MAX_AGGREGATE_ARTIFACT_BYTES", 96, raising=False)
    artifact_path = service.artifact_path(reference["artifact_id"])
    original = profiles._read
    def forbid_matrix_load(path, limit):
        if path == artifact_path:
            pytest.fail("Artifact custody attempted to load matrix bytes into the metadata cache")
        return original(path, limit)
    monkeypatch.setattr(profiles, "_read", forbid_matrix_load)
    assert profiles.validate_immutable_reference(reference, workspace=tmp_path, service=service) == reference
    context = profiles._References(tmp_path, service)
    context.read(reference)
    context.read(reference)  # Original/derived aliases count the same retained bytes once.
    assert context.remaining == 32
    assert profiles.MAX_ARTIFACT_BYTES == MAX_MATRIX_BYTES == 100_000_000
    monkeypatch.setattr(profiles, "MAX_ARTIFACT_BYTES", 24)
    with pytest.raises(ValueError, match="artifact|input|data"):
        profiles.validate_immutable_reference(reference, workspace=tmp_path, service=service)


def test_artifact_budget_rejects_fifth_distinct_input_before_hashing(tmp_path, monkeypatch):
    from devbench.analysis.methods import MethodService

    service = MethodService(tmp_path / "methods")
    references = []
    for index in range(5):
        path = tmp_path / ("input-" + str(index) + ".dat")
        path.write_bytes(bytes([index]) * 32)
        references.append(service.import_artifact(path, artifact_type="file", actor="synthetic-test"))
    monkeypatch.setattr(profiles, "MAX_AGGREGATE_ARTIFACT_BYTES", 128)
    original = service.artifact_path
    streamed = []
    def track_hash(identity):
        streamed.append(identity)
        return original(identity)
    monkeypatch.setattr(service, "artifact_path", track_hash)
    context = profiles._References(tmp_path, service)
    for reference in references[:4]:
        context.read(reference)
    context.read(references[0])  # A cached alias remains free after exhausting the distinct-byte budget.
    with pytest.raises(ValueError, match="aggregate"):
        context.read(references[4])
    assert streamed == [reference["artifact_id"] for reference in references[:4]]
    assert context.remaining_artifacts == 0
    assert len(context.artifact_bytes) == len(context.cache) == 4


def test_artifact_physical_size_and_failed_hash_leave_budget_uncommitted(tmp_path, monkeypatch):
    from devbench.analysis.methods import MethodService

    service = MethodService(tmp_path / "methods")
    path = tmp_path / "input.dat"
    path.write_bytes(b"x" * 32)
    reference = service.import_artifact(path, artifact_type="file", actor="synthetic-test")
    retained = service.artifact_path(reference["artifact_id"])
    retained.chmod(0o644)
    retained.write_bytes(b"z" * 33)
    monkeypatch.setattr(profiles, "MAX_AGGREGATE_ARTIFACT_BYTES", 64)
    original = service.artifact_path
    streamed = []
    def track_hash(identity):
        streamed.append(identity)
        return original(identity)
    monkeypatch.setattr(service, "artifact_path", track_hash)
    context = profiles._References(tmp_path, service)
    with pytest.raises(ValueError, match="artifact|byte|size"):
        context.read(reference)
    assert not streamed  # A size mismatch is observable without streaming the retained file.
    assert context.remaining_artifacts == 64
    assert not context.artifact_bytes and not context.cache
    retained.write_bytes(b"z" * 32)  # Matching size still needs the actual service digest check.
    with pytest.raises(ValueError, match="artifact|hash"):
        context.read(reference)
    assert streamed == [reference["artifact_id"]]
    assert context.remaining_artifacts == 64
    assert not context.artifact_bytes and not context.cache


def test_late_finalization_refuses_an_already_retained_attempt_even_when_failed(tmp_path):
    intent = fixture(tmp_path)
    frozen = source(tmp_path / "intent.json", intent)
    workflow, bridge, service = finalization_inputs(tmp_path, frozen, intent)
    step = json.loads(Path(workflow["path"]).read_bytes())["governed_workflow"]["steps"][0]
    class FailedBeforeNumericalWork:
        def execute(self, contract, inputs, parameters, method_source, lock, output_dir):
            return {"exit_code": 1, "runtime": "synthetic_fixture", "error": "Stopped before numerical work"}
    run = service.run_step(step["step_id"], actor="synthetic-test", executor=FailedBeforeNumericalWork())
    assert run["status"] == "failed"
    with pytest.raises(ValueError, match="already|started|pre.run|execut"):
        profiles.finalize_assessment_plan(frozen, workflow, bridge, output=tmp_path / "late-finalization", workspace=tmp_path, service=service)
    assert not (tmp_path / "late-finalization").exists()
    assert service.get("run", run["run_id"]) == run


@pytest.mark.parametrize("attack", ["origin", "missing_id"])
def test_requirement_attribution_must_match_the_retained_record(tmp_path, attack):
    estimate = estimand(tmp_path)
    if attack == "origin":
        estimate["requirement_sources"][0]["origin"] = "user"
    else:
        estimate["requirement_sources"][0]["requirement_ids"] = ["absent-id"]
    with pytest.raises(ValueError, match="requirement|origin|attribution"):
        profiles.validate_estimand(estimate, workspace=tmp_path)


@pytest.mark.parametrize("metric_id,units,operator,bound", [
    ("gene_effect_absolute_difference", "log2 fold change", "greater_equal", .2),
    ("gene_effect_rmse", "log2 fold change", "greater_equal", .2),
    ("gene_set_precision", "fraction", "less_equal", .2),
    ("gene_set_recall", "fraction", "greater_equal", 1.2),
])
def test_agreement_bound_cannot_reverse_formula_meaning_or_exceed_fraction_range(tmp_path, metric_id, units, operator, bound):
    spec = metric(tmp_path)
    spec.update(metric_id=metric_id, units=units)
    spec["acceptance_policy"] = {"kind": "agreement_bound", "operator": operator, "bound": bound,
        "rationale": "An invalid synthetic acceptance rule", "source_ref": spec["parameter_record_ref"]}
    with pytest.raises(ValueError, match="operator|bound|fraction"):
        profiles.validate_metric_spec(spec, workspace=tmp_path)


@pytest.mark.parametrize("field", ["independent_unit", "dependence", "claim_scope", "composed_time"])
def test_supplied_capabilities_cannot_rescue_unknown_scientific_descriptors(tmp_path, field):
    estimate = estimand(tmp_path, relationship="composed" if field == "composed_time" else "contrast")
    if field == "independent_unit":
        estimate["biological_unit"][field] = None
        estimate["unknowns"].append({"field": "biological_unit.independent_unit", "reason": "Unknown unit"})
    elif field == "dependence":
        estimate["biological_unit"][field] = "unknown"
        estimate["unknowns"].append({"field": "biological_unit.dependence", "reason": "Unknown dependence"})
    elif field == "claim_scope":
        estimate[field] = "unknown"
        estimate["unknowns"].append({"field": field, "reason": "Unknown claim scope"})
    else:
        estimate["time_reference"] = None
        estimate["unknowns"].append({"field": "time_reference", "reason": "Unknown composed time reference"})
    capabilities = ["bulk_gene_expression", "independent_biological_units", "identifiable_design", "measured_time",
                    "ranked_gene_statistics", "tested_gene_universe", "versioned_gene_sets"]
    target = "composed" if field == "composed_time" else "mean_expression"
    result = next(item for item in profiles.select_applicable_profiles(estimate, capabilities) if item["profile_id"] == target)
    assert result["status"] == "not_assessable"


def test_frozen_profile_cannot_silently_admit_a_disallowed_claim_scope(tmp_path):
    intent = fixture(tmp_path)
    intent["profile_specs"][0]["permitted_claim_scopes"] = ["descriptive"]
    with pytest.raises(ValueError, match="claim.scope|scope"):
        profiles.validate_assessment_intent(intent, workspace=tmp_path)


@pytest.mark.parametrize("attack", ["negative_difference", "negative_rmse", "zero_denominator", "missing_denominator"])
def test_computed_metric_rejects_impossible_value_or_denominator(tmp_path, attack):
    evidence = source(tmp_path / "evidence.json", {"synthetic": True})
    result = {"metric_id": "gene_effect_absolute_difference", "profile_id": "mean_expression", "estimand_id": "treatment-contrast",
        "status": "computed", "value": 1, "units": "log2 fold change", "denominators": {"assessed_rows": 1},
        "universe_ref": evidence, "exclusions_ref": evidence, "uncertainty_ref": None, "reason_codes": [], "evidence_refs": [evidence]}
    if attack.startswith("negative"):
        result["value"] = -1
        if attack == "negative_rmse":
            result["metric_id"] = "gene_effect_rmse"
    else:
        result.update(metric_id="gene_effect_direction_agreement", units="fraction", value=1)
        result["denominators"] = {"assessed_rows": 0} if attack == "zero_denominator" else {"irrelevant": 1}
    with pytest.raises(ValueError, match="negative|nonnegative|denominator"):
        profiles.validate_metric_result(result, workspace=tmp_path)


@pytest.mark.parametrize("attack", ["arbitrary_workflow", "foreign_input", "dataset_binding", "dataset_declarations", "dataset_access", "dataset_classification", "nested_output_hash"])
def test_finalization_rejects_self_digested_unbound_workflow_or_result_hash(tmp_path, attack):
    intent = fixture(tmp_path)
    if attack in {"dataset_binding", "dataset_declarations", "dataset_access", "dataset_classification"}:
        descriptor = json.loads(Path(intent["dataset_ref"]["path"]).read_bytes())
        if attack == "dataset_binding":
            identity = descriptor["binding"]["matrix_artifact_id"]
            descriptor["binding"]["matrix_artifact_id"] = descriptor["binding"]["metadata_artifact_id"]
            descriptor["binding"]["metadata_artifact_id"] = identity
        elif attack == "dataset_access":
            descriptor["access_record"]["sha256"] = "a" * 64
        elif attack == "dataset_declarations":
            descriptor["binding"]["declarations"]["metadata_roles"] = {"condition": "batch", "biological_unit": "unit", "batch": "group"}
        else:
            descriptor["classification"] = True
        # Capture correct inputs first; the changed dataset descriptor is separately frozen below.
        original_ref = source(tmp_path / "original-intent.json", intent)
        workflow, bridge, service = finalization_inputs(tmp_path, original_ref, intent)
        if attack == "dataset_declarations":
            from devbench.analysis.rna_input_contract import inspect_registered_bulk_rna_input
            descriptor["inspection_sha256"] = digest(inspect_registered_bulk_rna_input(service, descriptor["binding"]))
        intent["dataset_ref"] = source(tmp_path / "dataset.json", descriptor)
        analyst = json.loads(Path(intent["analyst_projection_ref"]["path"]).read_bytes())
        analyst["dataset_ref"] = intent["dataset_ref"]
        intent["analyst_projection_ref"] = source(tmp_path / "analyst.json", analyst)
        frozen = source(tmp_path / "intent.json", intent)
    else:
        frozen = source(tmp_path / "intent.json", intent)
        workflow, bridge, service = finalization_inputs(tmp_path, frozen, intent)
    wrapper = json.loads(Path(workflow["path"]).read_bytes())
    wrapper["assessment_intent_ref"] = frozen
    wrapper["assessment_rules_sha256"] = profiles.assessment_rules_digest(intent)
    if attack == "arbitrary_workflow":
        wrapper["governed_workflow"] = {"steps": [{"method": "unretained synthetic name"}]}
    elif attack == "foreign_input":
        wrapper["governed_workflow"]["steps"][0]["inputs"]["counts"] = "a" * 64
    elif attack == "nested_output_hash":
        wrapper["governed_workflow"]["steps"][0]["output_sha256"] = "a" * 64
    wrapper["governed_workflow_sha256"] = digest(wrapper["governed_workflow"])
    workflow = source(tmp_path / "workflow.json", wrapper)
    with pytest.raises(ValueError, match="workflow|snapshot|step|input|dataset|result|fields|digest"):
        profiles.finalize_assessment_plan(frozen, workflow, bridge, output=tmp_path / "bad-finalization", workspace=tmp_path, service=service)
