"""Scientific refusal, attribution, coverage and evidence-scope controls."""
from copy import deepcopy

import pytest

from tests.test_rna_investigation_contract import contract, fixture, packet, ref, result


@pytest.mark.parametrize("representation,model", [("raw_counts", "negative_binomial_rle_empirical_bayes"),
    ("normalized_expression", "gaussian_splines")])
def test_declared_representation_preserves_appropriate_measurement_model(representation, model):
    p = packet(changes={"representation": representation})
    decision = contract().investigation_prerequisites(p)
    assert decision["inference_allowed"] is True
    assert decision["representation_model"] == model


@pytest.mark.parametrize("changes,field", [({"representation": None}, "representation"),
    ({"biological_independence": None}, "biological_independence"),
    ({"biological_unit_identity_known": False}, "biological_unit_identity_known"),
    ({"biological_units": ["a", "b"]}, "biological_units"),
    ({"biological_units": ["a", "a", "b", "b"]}, "biological_units"),
    ({"chronology_valid": False}, "chronology_valid"), ({"design_rank_valid": False}, "design_rank_valid"),
    ({"contrast_valid": False}, "contrast_valid"), ({"age_batch_confounded": True}, "age_batch_confounded"),
    ({"alignment_valid": False}, "alignment_valid"), ({"count_values_valid": False}, "count_values_valid")])
def test_failed_or_unknown_inference_prerequisites_are_not_dispatchable(changes, field):
    decision = contract().investigation_prerequisites(packet(changes=changes))
    assert decision["inference_allowed"] is False
    assert field in decision["blocking"]


def test_missing_donor_identity_is_not_repaired_by_unique_sample_ids():
    plan, evidence, policy = fixture()
    evidence["facts"]["unknown"].append({"field": "biological_unit_identity_known", "value": None,
        "source": {"kind": "unknown", "reference": "metadata"}, "evidence_refs": [],
        "limitation": "Unique libraries do not establish unique animals"})
    p = contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)
    assert contract().investigation_prerequisites(p)["inference_allowed"] is False


def test_model_inferences_cannot_satisfy_coordinator_design_checks():
    p = packet()
    observed = p["facts"]["observed"]
    design = next(item for item in observed if item["field"] == "design_rank_valid")
    observed.remove(design)
    p["facts"]["inferred"].append({**design, "source": {"kind": "assistant_inference", "reference": "belief"}})
    assert "design_rank_valid" in contract().investigation_prerequisites(p)["blocking"]


def test_diagnostic_only_scope_does_not_claim_inferential_independence():
    p = packet(action="diagnostic_flagging", changes={"representation": None, "biological_independence": None})
    decision = contract().investigation_prerequisites(p)
    assert decision["allowed_scope"] == "diagnostic_flagging"
    assert decision["inference_allowed"] is False
    assert decision["representation_model"] is None


def test_complete_unknowns_and_inspection_limitations_survive_neutral_projection():
    plan, evidence, policy = fixture()
    plan["context"]["dataset_summary"]["unknowns"] = [
        {"field": "collection_versus_inferred_time", "impact": "Ordering evidence is unresolved", "blocking": True},
        {"field": "technical_replicate_relationships", "impact": "Cannot aggregate libraries", "blocking": True}]
    evidence["facts"]["unknown"] = [{"field": "metadata_preview_complete", "value": None,
        "source": {"kind": "unknown", "reference": "audit"}, "evidence_refs": [],
        "limitation": "Only a bounded metadata preview was inspected"}]
    p = contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)
    assert {item["field"] for item in p["facts"]["unknown"]} == {"metadata_preview_complete", "collection_versus_inferred_time", "technical_replicate_relationships"}


def test_additional_blocking_unknown_and_incomplete_inspection_are_preserved():
    plan, evidence, policy = fixture()
    plan["context"]["dataset_summary"]["unknowns"] = [
        {"field": "required_assay_calibration", "impact": "Calibration was not inspected", "blocking": True}]
    plan["context"]["dataset_summary"]["observed"]["bulk_rna_input"] = {
        "sample_metadata_complete": False, "sample_metadata_fields_complete": False}
    p = contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)
    assert "required_assay_calibration" in contract().investigation_prerequisites(p)["blocking"]
    assert {"bulk_rna_input.sample_metadata_complete", "bulk_rna_input.sample_metadata_fields_complete"} <= {f["field"] for f in p["facts"]["unknown"]}


def test_supplied_nonblocking_unknown_cannot_shadow_a_retained_critical_unknown():
    plan, evidence, policy = fixture()
    plan["context"]["dataset_summary"]["unknowns"] = [{"field": "required_assay_calibration", "impact": "A required calibration is absent", "blocking": True}]
    evidence["facts"]["unknown"] = [{"field": "required_assay_calibration", "value": None,
        "source": {"kind": "unknown", "reference": "compact-view"}, "evidence_refs": [],
        "limitation": "The coordinator supplied a compact view", "blocking_for": []}]
    p = contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)
    assert contract().investigation_prerequisites(p)["inference_allowed"] is False
    unknown = next(f for f in p["facts"]["unknown"] if f["field"] == "required_assay_calibration")
    assert "A required calibration is absent" in unknown["limitation"]
    assert "compact view" in unknown["limitation"]


def test_metadata_derived_unit_declaration_cannot_replace_repeated_source_units():
    plan, evidence, policy = fixture(changes={"biological_units": ["a", "a", "b", "b"]})
    units = next(f for f in evidence["facts"]["declared"] if f["field"] == "biological_units")
    units["value"] = ["invented-a", "invented-b", "invented-c", "invented-d"]
    with pytest.raises(ValueError, match="source|evidence|declaration"):
        contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)


@pytest.mark.parametrize("field,kind", [("fit_outputs", "numerical_result"), ("quality_summary", "json")])
def test_fitted_prefit_findings_cannot_be_renamed_as_audit_fields(field, kind):
    plan, evidence, policy = fixture()
    findings = {"p_value": .001, "q_value": .002, "effect": 42.0}
    source = {field: findings}
    r = ref("renamed-output", source, kind)
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": source})
    evidence["facts"]["observed"].append({"field": field, "value": findings,
        "source": {"kind": "coordinator_artifact", "reference": "renamed-output#/" + field},
        "evidence_refs": [r], "limitation": None})
    with pytest.raises(ValueError, match="pre.fit|fitted|audit"):
        contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)


def test_user_parameter_origin_requires_an_exact_matching_retained_requirement():
    p = packet()
    value = result(p)
    value["proposals"]["candidates"][0]["parameter_candidates"][0]["origin"] = "user"
    with pytest.raises(ValueError, match="requirement|origin"):
        contract().validate_investigation_result(value, packet=p)
    plan, evidence, policy = fixture()
    plan["user_parameter_requirements"] = [{"name": "df", "value": 3, "evidence": "User explicitly requested df=3"}]
    p = contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)
    value = result(p)
    value["proposals"]["candidates"][0]["parameter_candidates"][0]["origin"] = "user"
    assert contract().validate_investigation_result(value, packet=p) == value


def claim(p, scope="observation", *, pointer="/sample_count", value=4):
    return {"claim": "Bounded coordinator measurement", "scope": scope, "evidence_refs": p["evidence_refs"],
        "numerical_assertions": [{"evidence_id": "audit", "pointer": pointer, "value": value}],
        "competing_explanations": []}


def test_verified_prefit_numeric_audit_is_permitted_but_invented_output_is_rejected():
    p = packet()
    value = result(p)
    value["evidence_claims"] = [claim(p)]
    assert contract().validate_investigation_result(value, packet=p) == value
    value["evidence_claims"][0]["numerical_assertions"][0].update(pointer="/p_value", value=0.0001)
    with pytest.raises(ValueError, match="numeric|evidence|pre.fit"):
        contract().validate_investigation_result(value, packet=p)


def test_postfit_results_cannot_edit_existing_numerical_fits():
    p = packet("post_fit_explanation")
    value = result(p)
    value["evidence_claims"] = [claim(p, value=999)]
    with pytest.raises(ValueError, match="numeric|evidence"):
        contract().validate_investigation_result(value, packet=p)
    value = result(p)
    value["proposals"]["refitted_genes"] = {"gene": {"p_value": 0}}
    with pytest.raises(ValueError):
        contract().validate_investigation_result(value, packet=p)


@pytest.mark.parametrize("scope", ["direct_causality", "promoter_binding", "visual_assessment", "regulon_enrichment", "motif_enrichment", "curated_membership"])
def test_association_or_reference_alone_cannot_establish_stronger_evidence(scope):
    p = packet("post_fit_explanation")
    value = result(p)
    value["evidence_claims"] = [{**claim(p, scope=scope), "numerical_assertions": []}]
    with pytest.raises(ValueError, match="scope|evidence|visual"):
        contract().validate_investigation_result(value, packet=p)


def test_plot_reference_is_not_supplied_pixels_or_visual_inspection():
    plan, evidence, policy = fixture()
    evidence["evidence_refs"].append(ref("plot", kind="png"))
    p = contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)
    assert p["visual_content_supplied"] is False


def statistics_projection(*, coverage="whole_family"):
    return {"coverage": coverage, "selection": {"rule": "all", "origin": "coordinator", "requirement_name": None},
        "whole_family_refs": [ref("whole-family", kind="numerical_result")],
        "tested_hypotheses": 2, "represented_hypotheses": 2, "failed_estimates": 1,
        "missingness": {"unrepresented_hypotheses": 0, "failed_estimates_retained": True},
        "rows": [{"hypothesis_id": "gene-a", "estimate_status": "estimated", "p_value": .01, "q_value": .02},
                 {"hypothesis_id": "gene-b", "estimate_status": "failed", "p_value": None, "q_value": None,
                  "reason": "Nonconvergence retained"}]}


def stats_packet(stats, *, field="gene_statistics", family_refs=None, requirements=None):
    plan, evidence, policy = fixture("post_fit_explanation")
    if requirements is not None:
        plan["user_parameter_requirements"] = requirements
    r = ref("statistics", {"gene_statistics": stats})
    evidence["evidence_refs"].extend([r, *(stats["whole_family_refs"] if family_refs is None else family_refs)])
    evidence["diagnostic_refs"].append(r)
    evidence["facts"]["observed"].append({"field": field, "value": stats,
        "source": {"kind": "coordinator_artifact", "reference": "statistics#/gene_statistics"},
        "evidence_refs": [r], "limitation": "A bounded projection is not whole-transcriptome inspection"})
    evidence["verified_artifacts"].append({"ref": r, "value": {"gene_statistics": stats}})
    return contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)


def test_fitted_gene_projection_retains_family_coverage_failed_estimates_and_exact_values():
    p = stats_packet(statistics_projection())
    value = result(p)
    value["evidence_claims"] = [{"claim": "An actual fitted association", "scope": "association",
        "evidence_refs": [next(r for r in p["evidence_refs"] if r["artifact_id"] == "statistics")],
        "numerical_assertions": [{"evidence_id": "statistics", "pointer": "/gene_statistics/rows/0/q_value", "value": .02}],
        "competing_explanations": ["Technical composition may differ"]}]
    assert contract().validate_investigation_result(value, packet=p) == value


@pytest.mark.parametrize("field", ["rna.gene_statistics", "gene_statistics_projection"])
@pytest.mark.parametrize("mutation", [lambda s: s["rows"][0].update(q_value=.001),
    lambda s: s["rows"][0].update(p_value=False, q_value=True),
    lambda s: s["rows"][1].update(p_value=0, q_value=0, effect=0),
    lambda s: s.update(failed_estimates=2), lambda s: s.update(failed_estimates=0),
    lambda s: s.update(tested_hypotheses=3), lambda s: s["rows"][0].update(effect=None),
    lambda s: s["selection"].update(rule="parent_preferred"),
    lambda s: s["rows"][0].update(selection_conditional=1)])
def test_fitted_statistics_aliases_cannot_bypass_the_typed_scientific_guards(field, mutation):
    stats = statistics_projection()
    mutation(stats)
    with pytest.raises(ValueError):
        stats_packet(stats, field=field)


@pytest.mark.parametrize("field", ["rna.gene_statistics", "gene_statistics_projection"])
def test_valid_controlled_statistics_aliases_retain_the_same_validated_values(field):
    p = stats_packet(statistics_projection(), field=field)
    assert next(f["value"] for f in p["facts"]["observed"] if f["field"] == field)["rows"][0]["q_value"] == .02


def test_postfit_observations_require_a_finite_scientific_field_identity():
    plan, evidence, policy = fixture("post_fit_explanation")
    source = {"arbitrary_output_container": {"p_value": .01, "q_value": .001}}
    r = ref("untyped-output", source)
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": source})
    evidence["facts"]["observed"].append({"field": "arbitrary_output_container", "value": source["arbitrary_output_container"],
        "source": {"kind": "coordinator_artifact", "reference": "untyped-output#/arbitrary_output_container"},
        "evidence_refs": [r], "limitation": None})
    with pytest.raises(ValueError, match="scientific|field|projection"):
        contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)


@pytest.mark.parametrize("field", ["quality_summary", "fitted_family_coverage", "execution_summary"])
def test_known_observation_fields_cannot_disguise_a_fitted_gene_table(field):
    stats = statistics_projection()
    stats["rows"][0]["q_value"] = .001
    with pytest.raises(ValueError, match="fitted|statistics|projection"):
        stats_packet(stats, field=field)


@pytest.mark.parametrize("field", ["regulon_enrichment", "motif_enrichment"])
@pytest.mark.parametrize("kind", ["invalid", "valid", "nested"])
def test_gene_statistics_cannot_be_substituted_for_enrichment_evidence(field, kind):
    stats = statistics_projection()
    if kind != "valid":
        stats["rows"][0]["q_value"] = .001
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {"nested": {"gene_statistics": stats}} if kind == "nested" else stats
    source = {field: view}
    r = ref("enrichment-source", source)
    evidence["evidence_refs"].extend([r, *stats["whole_family_refs"]])
    evidence["verified_artifacts"].append({"ref": r, "value": source})
    evidence["facts"]["observed"].append({"field": field, "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "enrichment-source#/" + field},
        "evidence_refs": [r], "limitation": None})
    with pytest.raises(ValueError, match="gene|statistics|projection|scope"):
        contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)


@pytest.mark.parametrize("field", ["regulon_enrichment", "motif_enrichment"])
def test_genuine_enrichment_summaries_keep_their_distinct_evidence_scope(field):
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {"tested_sets": 2, "background_genes": 20,
            "results": [{"regulator": "TF-a", "p_value": .05, "q_value": .10}]}
    r = ref("enrichment-source", {field: view})
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": {field: view}})
    evidence["facts"]["observed"].append({"field": field, "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "enrichment-source#/" + field},
        "evidence_refs": [r], "limitation": "Enrichment does not establish direct binding"})
    p = contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)
    value = result(p)
    value["evidence_claims"] = [{"claim": "An actual scoped enrichment observation", "scope": field,
        "evidence_refs": [r], "numerical_assertions": [], "competing_explanations": []}]
    assert contract().validate_investigation_result(value, packet=p) == value


@pytest.mark.parametrize("name", ["p_values", "q_values", "effects", "effect_sizes", "effect_differences",
    "fold_changes", "log2_fold_changes", "test_statistics", "wald_statistics", "ｑ＿ｖａｌｕｅｓ",
    "pvals", "qvals", "adjusted_p_values", "padj_values"])
def test_plural_fitted_statistics_cannot_enter_a_nonfitted_quality_summary(name):
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {name: [1e-40], "hypothesis_ids": ["invented-gene"]}
    r = ref("quality-source", {"quality_summary": view})
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": {"quality_summary": view}})
    evidence["facts"]["observed"].append({"field": "quality_summary", "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "quality-source#/quality_summary"},
        "evidence_refs": [r], "limitation": None})
    with pytest.raises(ValueError, match="fitted|projection|statistics"):
        contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)


def test_genuine_quality_arrays_and_precise_gene_ids_remain_source_bound():
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {"library_sizes": [100, 200], "missing_value_counts": [0, 1], "zero_fractions": [.1, .2],
            "warning_flags": [False, True], "gene_ids": ["ＧｅｎｅＡ", "GeneA"],
            "statistic_names": ["p_value", "q_value", "effect"], "effects": ["Possible heterogeneity, not assessed"],
            "q_value": "Benjamini-Hochberg adjustment description", "assessment": "QC measurements only"}
    r = ref("quality-source", {"quality_summary": view})
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": {"quality_summary": view}})
    evidence["facts"]["observed"].append({"field": "quality_summary", "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "quality-source#/quality_summary"},
        "evidence_refs": [r], "limitation": "QC is not an inferential fit"})
    p = contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)
    assert next(f["value"] for f in p["facts"]["observed"] if f["field"] == "quality_summary") == view


@pytest.mark.parametrize("value", [[[1e-40]], [False, True], {"nested": [1e-40]}])
def test_plural_fitted_slots_reject_nested_numbers_and_malformed_booleans(value):
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {"q_values": value, "hypothesis_ids": ["invented-gene"]}
    r = ref("quality-source", {"quality_summary": view})
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": {"quality_summary": view}})
    evidence["facts"]["observed"].append({"field": "quality_summary", "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "quality-source#/quality_summary"},
        "evidence_refs": [r], "limitation": None})
    with pytest.raises(ValueError, match="fitted|projection|statistics"):
        contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)


@pytest.mark.parametrize("field", ["quality_summary", "regulon_enrichment", "motif_enrichment"])
def test_structured_hypothesis_vectors_cannot_hide_in_other_evidence_scopes(field):
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {"hypothesis_ids": ["invented"], "values": [1e-40], "weights": [99]}
    r = ref("statistics-source", {field: view})
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": {field: view}})
    evidence["facts"]["observed"].append({"field": field, "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "statistics-source#/" + field},
        "evidence_refs": [r], "limitation": None})
    with pytest.raises(ValueError, match="fitted|projection|statistics|scope"):
        contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)


def test_hypothesis_id_labels_without_quantities_remain_descriptive_metadata():
    plan, evidence, policy = fixture("post_fit_explanation")
    view = {"hypothesis_ids": ["Ｇene", "Gene"], "labels": ["not assessed", "possible interpretation"]}
    r = ref("quality-source", {"quality_summary": view})
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": {"quality_summary": view}})
    evidence["facts"]["observed"].append({"field": "quality_summary", "value": view,
        "source": {"kind": "coordinator_artifact", "reference": "quality-source#/quality_summary"},
        "evidence_refs": [r], "limitation": "Labels alone are not numerical findings"})
    p = contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)
    assert next(f["value"] for f in p["facts"]["observed"] if f["field"] == "quality_summary") == view


@pytest.mark.parametrize("value,expected", [({"q_values": [1e-40]}, True),
    ({"hypothesis_ids": ["invented"], "values": [1e-40], "weights": [99]}, True),
    ({"q_value": "BH description", "effects": ["not assessed"]}, False),
    ({"gene_ids": ["Ｇene", "Gene"], "library_sizes": [12, 18], "warning_flags": [False, True]}, False)])
def test_shared_detector_distinguishes_fitted_vectors_from_descriptive_qc(value, expected):
    assert contract().contains_fitted_numerical_findings(value) is expected


@pytest.mark.parametrize("invalid", [None, [], {}, "invalid-reference", {"artifact_id": []}])
def test_whole_family_ref_members_are_validated_before_identity_lookup(invalid):
    stats = statistics_projection()
    actual_family_refs = stats["whole_family_refs"]
    stats["whole_family_refs"] = [invalid]
    with pytest.raises(ValueError):
        stats_packet(stats, family_refs=actual_family_refs)


@pytest.mark.parametrize("invalid", [None, 4, "invalid-row"])
def test_user_selected_rows_are_validated_before_gene_identity_lookup(invalid):
    stats = statistics_projection(coverage="bounded_projection")
    stats["selection"] = {"rule": "user_requirement", "origin": "user", "requirement_name": "genes"}
    stats["rows"][0] = invalid
    requirements = [{"name": "genes", "value": ["gene-a", "gene-b"], "evidence": "An exact retained gene requirement"}]
    with pytest.raises(ValueError):
        stats_packet(stats, requirements=requirements)


def test_valid_source_refs_and_user_selected_rows_retain_exact_requirement_matching():
    stats = statistics_projection(coverage="bounded_projection")
    stats["selection"] = {"rule": "user_requirement", "origin": "user", "requirement_name": "genes"}
    requirements = [{"name": "genes", "value": ["gene-a", "gene-b"], "evidence": "An exact retained gene requirement"}]
    p = stats_packet(stats, requirements=requirements)
    actual = next(f["value"] for f in p["facts"]["observed"] if f["field"] == "gene_statistics")
    assert actual["whole_family_refs"] == stats["whole_family_refs"]
    assert [row["hypothesis_id"] for row in actual["rows"]] == requirements[0]["value"]


@pytest.mark.parametrize("invalid", [[], {}])
def test_representation_containers_are_explicit_value_rejections(invalid):
    p = packet()
    next(f for f in p["facts"]["declared"] if f["field"] == "representation")["value"] = invalid
    with pytest.raises(ValueError):
        contract().investigation_prerequisites(p)


@pytest.mark.parametrize("invalid", [[], {}])
@pytest.mark.parametrize("field", ["coverage", "rule", "origin", "estimate_status"])
def test_statistics_container_mistakes_are_explicit_value_rejections(field, invalid):
    stats = statistics_projection()
    if field in {"rule", "origin"}:
        stats["selection"][field] = invalid
    elif field == "estimate_status":
        stats["rows"][0][field] = invalid
    else:
        stats[field] = invalid
    with pytest.raises(ValueError):
        stats_packet(stats)


@pytest.mark.parametrize("change", [lambda s: s["rows"][0].update(q_value=.001),
    lambda s: s["rows"][1].update(p_value=0, q_value=0), lambda s: s.update(failed_estimates=0),
    lambda s: s.update(whole_family_refs=[]), lambda s: s["missingness"].update(failed_estimates_retained=False),
    lambda s: s.update(represented_hypotheses=1), lambda s: s["selection"].update(rule="lead_preferred_genes")])
def test_false_significance_failure_dropping_and_unbound_family_are_rejected(change):
    stats = statistics_projection()
    change(stats)
    with pytest.raises(ValueError, match="statistics|family|estimate|projection|selection"):
        stats_packet(stats)


def test_user_specific_gene_selection_requires_matching_requirement():
    stats = statistics_projection(coverage="bounded_projection")
    stats["selection"] = {"rule": "user_requirement", "origin": "user", "requirement_name": "genes"}
    with pytest.raises(ValueError, match="requirement|selection"):
        stats_packet(stats)


def test_opaque_coordinator_selected_gene_subset_is_not_a_neutral_projection():
    stats = statistics_projection(coverage="bounded_projection")
    stats.update(represented_hypotheses=1)
    stats["rows"] = stats["rows"][:1]
    stats["missingness"]["unrepresented_hypotheses"] = 1
    stats["selection"] = {"rule": "coordinator_declared", "origin": "coordinator", "requirement_name": None}
    with pytest.raises(ValueError, match="projection|selection"):
        stats_packet(stats)


@pytest.mark.parametrize("represented", [1, 2])
def test_global_failed_estimates_cannot_exceed_observed_failures_plus_unrepresented_rows(represented):
    stats = statistics_projection(coverage="bounded_projection")
    stats.update(represented_hypotheses=represented, failed_estimates=2)
    stats["rows"] = stats["rows"][:represented]
    stats["missingness"]["unrepresented_hypotheses"] = 2 - represented
    if represented == 1:
        stats["selection"]["rule"] = "deterministic_head"
    with pytest.raises(ValueError, match="failed|estimate|family"):
        stats_packet(stats)


def test_unrepresented_hypotheses_may_contain_actual_failed_estimates():
    stats = statistics_projection(coverage="bounded_projection")
    stats["rows"] = stats["rows"][:1]
    stats["represented_hypotheses"] = 1
    stats["missingness"]["unrepresented_hypotheses"] = 1
    stats["selection"]["rule"] = "deterministic_head"
    p = stats_packet(stats)
    assert next(f["value"] for f in p["facts"]["observed"] if f["field"] == "gene_statistics")["failed_estimates"] == 1


def test_unestimated_gene_cannot_retain_a_successful_numeric_effect():
    stats = statistics_projection()
    stats["rows"][1].update(estimate_status="not_estimated", effect=42.0)
    with pytest.raises(ValueError, match="estimate|effect|statistics"):
        stats_packet(stats)


def test_explicitly_unavailable_optional_effect_requires_a_reason():
    stats = statistics_projection()
    stats["rows"][0]["effect"] = None
    with pytest.raises(ValueError, match="effect|reason|estimate"):
        stats_packet(stats)


def test_optional_effect_may_be_omitted_or_null_with_a_concrete_reason():
    stats = statistics_projection()
    assert stats_packet(stats)["stage"] == "post_fit_explanation"
    stats["rows"][0].update(effect=None, reason="No scalar effect is defined for this fitted curve")
    p = stats_packet(stats)
    row = next(f["value"] for f in p["facts"]["observed"] if f["field"] == "gene_statistics")["rows"][0]
    assert row["effect"] is None and row["reason"]


def test_a_plain_qualified_flag_cannot_admit_direct_causal_claims():
    plan, evidence, policy = fixture("post_fit_explanation")
    source = {"qualified_causal_evidence": {"qualified": True, "scope": "direct_causality"}}
    r = ref("causal-source", source)
    evidence["evidence_refs"].append(r)
    evidence["verified_artifacts"].append({"ref": r, "value": source})
    evidence["facts"]["observed"].append({"field": "qualified_causal_evidence", "value": source["qualified_causal_evidence"],
        "source": {"kind": "coordinator_artifact", "reference": "causal-source#/qualified_causal_evidence"}, "evidence_refs": [r], "limitation": None})
    p = contract().build_investigation_packet(plan, evidence, stage="post_fit_explanation", task="independent_explanation_investigation", policy=policy)
    value = result(p)
    value["evidence_claims"] = [{"claim": "Direct causal claim", "scope": "direct_causality", "evidence_refs": [r],
        "numerical_assertions": [], "competing_explanations": []}]
    with pytest.raises(ValueError, match="scope|evidence|unavailable"):
        contract().validate_investigation_result(value, packet=p)


def test_gene_statistics_are_not_prefit_audit_outputs():
    plan, evidence, policy = fixture()
    stats = statistics_projection()
    r = ref("statistics", {"gene_statistics": stats})
    evidence["evidence_refs"].extend([r, *stats["whole_family_refs"]])
    evidence["facts"]["observed"].append({"field": "gene_statistics", "value": stats,
        "source": {"kind": "coordinator_artifact", "reference": "statistics#/gene_statistics"}, "evidence_refs": [r], "limitation": None})
    evidence["verified_artifacts"].append({"ref": r, "value": {"gene_statistics": stats}})
    with pytest.raises(ValueError, match="pre.fit|fitted"):
        contract().build_investigation_packet(plan, evidence, stage="pre_fit_methods", task="independent_method_investigation", policy=policy)
