"""Only schemas, empty configurations and admission guards may run on data hold.

Numerical regressions are deliberately skipped, not enabled by an environment
variable. Removing the skip requires a separately authorized qualification run.
"""
from __future__ import annotations

import builtins
import importlib
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError


def test_biology_scoring_module_exists():
    assert importlib.util.find_spec("devbench.study.biology_scoring") is not None


def modules():
    return (importlib.import_module("devbench.study.biology_answers"),
            importlib.import_module("devbench.study.biology_scoring"))


def test_passive_import_has_no_numerical_dependencies():
    # Reloading these producer-type modules in the pytest process invalidates
    # class identities already imported by consumers. Check a genuinely cold
    # import in isolation instead of poisoning subsequent integration tests.
    code = '''import builtins, importlib
original = builtins.__import__
def guarded(name, *args, **kwargs):
    assert name.split('.')[0] not in {'numpy', 'pandas', 'scipy', 'networkx'}
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
importlib.import_module('devbench.study.biology_answers')
importlib.import_module('devbench.study.biology_scoring')
'''
    result = subprocess.run([sys.executable, '-c', code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("permission", [None, False, 0, 1, "true", [], {}])
def test_grade_denies_before_inspecting_nonempty_inputs(permission):
    _, scoring = modules()

    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("input inspected before authorization")

    with pytest.raises(PermissionError, match="allow_result_analysis=True"):
        scoring.grade_biology_answer(Unreadable(), reference=Unreadable(),
                                      allow_result_analysis=permission)


def test_reference_conversion_denies_before_inspecting_inputs():
    _, scoring = modules()

    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("input inspected before authorization")

    with pytest.raises(PermissionError, match="allow_result_analysis=True"):
        scoring.build_biology_reference(
            template=Unreadable(), private_truth=Unreadable(), public_context=Unreadable(),
            public_annotations=Unreadable(), task_id="contract-only", input_sha256="0" * 64,
            gene_ids=Unreadable())


@pytest.mark.parametrize("helper,kwargs", [
    ("circular_distance", {}), ("root_set_error", {}), ("partition_scores", {}),
    ("graph_scores", {"directed": False}), ("rank_agreement", {"orientation_invariant": True}),
])
def test_public_metric_helpers_are_denied_before_inspection(helper, kwargs):
    _, scoring = modules()

    class Unreadable:
        def __getattribute__(self, name):
            raise AssertionError("metric input inspected before authorization")

    with pytest.raises(PermissionError, match="allow_result_analysis=True"):
        getattr(scoring, helper)(Unreadable(), Unreadable(), **kwargs)


def test_every_catalog_target_has_an_explicit_registered_contract():
    from devbench.study.biology import load_biology_catalog
    answers, scoring = modules()
    catalog = load_biology_catalog()
    target_ids = {t.target_id for template in catalog.templates for t in template.targets}
    assert set(answers.TARGET_PAYLOAD_KINDS) == target_ids
    registrations = scoring.biology_scoring_registrations(catalog)
    registered = {(r.template_id, c, t) for r in registrations for c in r.case_ids for t in r.target_ids}
    expected = {(template.template_id, case.case_id, target.target_id)
                for template in catalog.templates for case in template.cases for target in template.targets}
    assert registered == expected
    assert all(r.kind == "scorer" and callable(r.handler) for r in registrations)
    assert scoring.BiologyScoringThresholds().status == "provisional_uncalibrated_locked"


def test_schema_has_target_payload_discriminator_and_no_unbounded_extra_fields():
    answers, _ = modules()
    schema = answers.BiologyAnswer.model_json_schema()
    assert schema["additionalProperties"] is False
    target = schema["$defs"]["TargetAnswer"]
    assert target["additionalProperties"] is False
    assert target["properties"]["payload"]["anyOf"][0]["discriminator"]["propertyName"] == "kind"
    assert {"task_id", "input_sha256", "gene_universe_sha256", "target_results"} <= set(schema["required"])


def test_empty_answer_cannot_imply_scientific_success():
    answers, scoring = modules()
    answer = answers.BiologyAnswer(task_id="contract-only", input_sha256="0" * 64,
                                   gene_universe_sha256="0" * 64, target_results=())
    reference = scoring.BiologyReference(task_id="contract-only", input_sha256="0" * 64,
                                         gene_universe_sha256="0" * 64, targets=())
    result = scoring.grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
    assert result.scientific_success is None
    assert result.per_target_results == ()
    assert result.metric_missingness["scientific_success"] == "no_targets"
    assert result.safe_rows == ()
    assert result.task_id == reference.task_id
    assert result.input_sha256 == reference.input_sha256
    assert result.gene_universe_sha256 == reference.gene_universe_sha256


def test_grade_schema_carries_answer_independent_reference_availability():
    _, scoring = modules()
    schema = scoring.BiologyGrade.model_json_schema()
    assert {"target_reference_availability", "target_reference_missingness", "available_target_scientific_success"} <= set(schema["properties"])
    target = schema["$defs"]["TargetGrade"]
    assert {"reference_availability", "reference_scope", "scorable_components"} <= set(target["properties"])


def test_grade_schema_requires_reference_identity_binding():
    _, scoring = modules()
    schema = scoring.BiologyGrade.model_json_schema()
    assert {"task_id", "input_sha256", "gene_universe_sha256"} <= set(schema["required"])


def test_rhythm_schema_requires_unambiguous_trigonometric_convention():
    answers, _ = modules()
    schema = answers.RhythmPayload.model_json_schema()
    assert "phase_convention" in schema["required"]
    assert schema["properties"]["phase_convention"]["const"] == "cosine_plus_phase"


def test_public_metric_allowlist_covers_catalog_without_private_keys():
    from devbench.study.biology import load_biology_catalog
    _, scoring = modules()
    catalog = load_biology_catalog()
    assert all(metric.metric_id in scoring.PUBLIC_METRIC_IDS
               for template in catalog.templates for target in template.targets for metric in target.scientific_metrics)
    assert not {"private_truth", "true_effect", "generation_seed"} & scoring.PUBLIC_METRIC_IDS
    assert scoring.PUBLIC_SAFE_ROW_FIELDS == frozenset(scoring.SafeEstimateRow.model_fields)


def test_unknown_metric_key_is_rejected_by_schema_even_with_no_value():
    _, scoring = modules()
    with pytest.raises(ValidationError):
        scoring.TargetGrade(target_id="binding", outcome="unsupported_reference", scientific_success=None,
                             metrics={"private_seed": None})


def test_thresholds_are_frozen_and_cannot_claim_calibration():
    _, scoring = modules()
    thresholds = scoring.BiologyScoringThresholds()
    with pytest.raises(ValidationError):
        thresholds.alpha = 0.2
    with pytest.raises(ValidationError):
        scoring.BiologyScoringThresholds(status="calibrated")


def test_safe_export_schema_has_no_private_reference_fields():
    _, scoring = modules()
    schema = scoring.SafeEstimateRow.model_json_schema()
    assert schema["additionalProperties"] is False
    assert not {"truth", "reference_value", "nonnull", "eligible", "private_truth"} & set(schema["properties"])


def test_public_pair_rule_accepts_empty_configuration_only():
    _, scoring = modules()
    assert scoring.association_pair_universe(()) == ()


def test_nonempty_numeric_fields_are_not_required_for_empty_schema_validation():
    answers, scoring = modules()
    assert answers.biology_answer_schema()["title"] == "BiologyAnswer"
    assert scoring.BiologyGrade.model_json_schema()["additionalProperties"] is False


def test_all_known_catalog_claim_codes_are_schema_enumerated():
    from typing import get_args
    from devbench.study.biology import load_biology_catalog
    answers, _ = modules()
    known = set(get_args(answers.ClaimCode))
    catalog = load_biology_catalog()
    assert all(set(target.forbidden_claims) <= known for template in catalog.templates for target in template.targets)


@pytest.mark.parametrize("payload", ["GraphPayload", "OrderingPayload"])
def test_graph_and_ordering_schema_require_shared_process_projection(payload):
    answers, _ = modules()
    schema = getattr(answers, payload).model_json_schema()
    assert {"shared_process_projection", "analysis_layer", "retained_feature_universe", "excluded_feature_ids"} <= set(schema["required"])


def test_target_schema_has_separate_tool_receipt_references():
    answers, _ = modules()
    schema = answers.TargetAnswer.model_json_schema()
    receipts = schema["properties"]["tool_evidence_ids"]
    assert receipts["default"] == []
    assert receipts["maxItems"] == 100
    assert receipts["items"]["pattern"] == "^[0-9a-f]{64}$"
    assert receipts["uniqueItems"] is True


@pytest.mark.skipif(__import__("os").environ.get("DEVBENCH_RUN_LOCAL_FIXTURES") != "1", reason="User data hold: numerical grading qualification not authorized")
class TestNumericalBiologyScoring:
    """Source-only regression specifications; no arrays or generators execute."""

    def test_foreign_answer_binding_fails(self):
        from devbench.study.biology_answers import BiologyAnswer
        from devbench.study.biology_scoring import BiologyReference, grade_biology_answer
        reference = BiologyReference(task_id="one", input_sha256="0" * 64,
                                      gene_universe_sha256="0" * 64, targets=())
        answer = BiologyAnswer(task_id="two", input_sha256="0" * 64,
                               gene_universe_sha256="0" * 64, target_results=())
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is None  # no requested targets define a scientific denominator
        assert "task_binding_mismatch" in result.errors

    def test_circular_distance_accepts_full_rotations(self):
        import math
        from devbench.study.biology_scoring import circular_distance
        assert circular_distance(0.2, 0.2 + 2 * math.pi, allow_result_analysis=True) < 1e-12

    def test_graph_node_permutation_preserves_topology(self):
        from devbench.study.biology_scoring import graph_scores
        scores = graph_scores((("a", "b"), ("b", "c")), (("z", "x"), ("x", "y")), directed=False, allow_result_analysis=True)
        assert scores["topology_score"] == 1.0

    def test_root_set_order_does_not_change_error(self):
        from devbench.study.biology_scoring import root_set_error
        assert root_set_error((2.0, 8.0), (8.0, 2.0), allow_result_analysis=True) == 0.0

    def test_reversed_unanchored_order_is_equivalent(self):
        from devbench.study.biology_scoring import rank_agreement
        assert rank_agreement((0.0, 1.0, 2.0), (2.0, 1.0, 0.0), orientation_invariant=True, allow_result_analysis=True) == 1.0

    def test_clone_names_do_not_determine_equivalence(self):
        from devbench.study.biology_scoring import partition_scores
        assert partition_scores(("a", "a", "b"), ("x", "x", "y"), allow_result_analysis=True)["partition_f1"] == 1.0

    def test_failed_estimate_cannot_carry_fake_zero(self):
        from devbench.study.biology_answers import HypothesisEstimate
        with pytest.raises(ValidationError):
            HypothesisEstimate(hypothesis_id="g", estimate_status="unavailable", values=(0.0,), missing_reason="all_zero")

    def test_q_must_not_be_less_than_p(self):
        from devbench.study.biology_answers import HypothesisEstimate
        with pytest.raises(ValidationError):
            HypothesisEstimate(hypothesis_id="g", estimate_status="estimated", values=(1.0,), p_value=0.1, q_value=0.01)

    def test_structural_zero_is_never_a_null_hypothesis(self):
        from devbench.study.biology_scoring import HypothesisReference
        with pytest.raises(ValidationError):
            HypothesisReference(hypothesis_id="g", values=(None,), testable=False, nonnull=False)

    @staticmethod
    def effect_case(*, target_id="mean_contrast", values=(1.0,), reported=(1.0,),
                    nonnull=True, p_value=0.01, q_value=0.01, intervals=()):
        """Unexecuted scalar contrast fixture, not a generator or oracle run."""
        from devbench.study.biology_answers import BiologyAnswer, EffectsPayload, HypothesisEstimate, TargetAnswer
        from devbench.study.biology_scoring import BiologyReference, HypothesisReference, TargetReference
        from devbench.study.contracts import content_sha256
        common = dict(target_id=target_id, estimand="declared contrast", units="log2 ratio",
                      contrast="treated minus control", reference_population="donors", prediction_reference="observed grid")
        hypothesis = HypothesisReference(hypothesis_id="g", gene_id="g", coordinate_ids=tuple(f"coordinate_{i}" for i in range(len(values))),
                                         values=values, nonnull=nonnull)
        target = TargetReference(**common, scope="supported", evidence_scope="population_mean",
                                 numerical_reference_available=True, measurement="raw_counts", hypotheses=(hypothesis,))
        reference = BiologyReference(task_id="one", input_sha256="0" * 64,
                                      gene_universe_sha256=content_sha256(("g",)), gene_ids=("g",), targets=(target,))
        estimate = HypothesisEstimate(hypothesis_id="g", gene_id="g", estimate_status="estimated",
                                      coordinate_ids=hypothesis.coordinate_ids, values=reported, intervals=intervals,
                                      p_value=p_value, q_value=q_value)
        payload = EffectsPayload(analysis_performed=True, gene_statistics=(estimate,),
                                  multiplicity_scope=("g",) if p_value is not None else (), selection_conditional_inference=False,
                                  biological_unit="donor", analysis_layer="raw_counts", effect_definition=common["contrast"])
        answer = BiologyAnswer(task_id="one", input_sha256="0" * 64, gene_universe_sha256=reference.gene_universe_sha256,
                                target_results=(TargetAnswer(**common, claim_type="supported_estimate", evidence_scope="population_mean",
                                                            estimate_status="estimated", payload=payload),))
        return reference, answer

    def test_missing_answer_is_a_failure_not_refusal(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, _ = self.effect_case()
        result = grade_biology_answer(None, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is False
        assert result.per_target_results[0].outcome == "missing_answer"

    def test_null_result_requires_actual_test_accounting(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(values=(0.0,), reported=(0.0,), nonnull=False, p_value=None, q_value=None)
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is False

    def test_valid_all_null_family_has_undefined_recall(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(values=(0.0,), reported=(0.0,), nonnull=False, p_value=1.0, q_value=1.0)
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is True
        assert result.per_target_results[0].metrics["signed_recall"] is None
        assert result.per_target_results[0].metrics["precision"] is None
        assert result.per_target_results[0].metrics["realized_fdp"] == 0.0

    def test_nonzero_effect_cannot_pass_by_reported_nonsignificance(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(values=(0.1,), reported=(0.0,), nonnull=True, p_value=1.0, q_value=1.0)
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is False
        assert result.per_target_results[0].metrics["signed_recall"] == 0.0

    def test_returning_endpoint_only_misses_reversal(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(target_id="time_interaction", values=(0.0, 1.0, -1.0, 0.0), reported=(0.0, 0.0, 0.0, 0.0))
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is False

    def test_variance_null_cannot_be_replaced_by_mean_contrast(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(target_id="variability", values=(1.0,), reported=(0.0,))
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.scientific_success is False

    def test_arbitrarily_wide_interval_does_not_pass_uncertainty(self):
        from devbench.study.biology_answers import Interval
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(intervals=(Interval(lower=-1000.0, upper=1000.0),))
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.per_target_results[0].metrics["interval_coverage"] == 1.0
        assert result.scientific_success is False

    def test_threshold_override_does_not_change_locked_run(self):
        from devbench.study.biology_scoring import BiologyScoringThresholds, grade_biology_answer
        reference, answer = self.effect_case()
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True,
                                       thresholds=BiologyScoringThresholds(alpha=0.1))
        assert result.scientific_success is False
        assert "threshold_configuration_mismatch" in result.errors

    def test_grade_export_does_not_include_private_gene_truth(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case()
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        row = result.safe_rows[0].model_dump(mode="json")
        assert row["values"] == [1.0]
        assert not {"nonnull", "truth", "testable", "reference_values"} & set(row)

    def test_schema_preserves_failure_vs_refusal(self):
        from devbench.study.biology_answers import TargetAnswer
        with pytest.raises(ValidationError):
            TargetAnswer(target_id="binding", estimand="binding", units="edges", contrast="binding",
                         reference_population="loci", prediction_reference="assay", claim_type="refusal",
                         evidence_scope="not_identified", estimate_status="failed", limitation_codes=("fit_failed",))

    def test_bh_arithmetic_uses_entire_submitted_family(self):
        from devbench.study.biology_scoring import _bh_minimum
        assert _bh_minimum({"a": 0.01, "b": 1.0}) == {"b": 1.0, "a": 0.02}

    def test_point_event_is_not_sample_bracket_precision(self):
        from devbench.study.biology_answers import EventEstimate, EventsPayload, Interval
        from devbench.study.biology_scoring import BiologyScoringThresholds, TargetReference, _event_metrics
        target = TargetReference(target_id="event_timing", estimand="peak", units="hours", contrast="peak",
                                 reference_population="donors", prediction_reference="sample brackets", scope="supported",
                                 evidence_scope="population_mean", numerical_reference_available=True,
                                 event_hypothesis_ids=("g",), events=(EventEstimate(hypothesis_id="g", kind="peak", interval=Interval(lower=1.0, upper=3.0)),))
        payload = EventsPayload(event_intervals=(EventEstimate(hypothesis_id="g", kind="peak", interval=Interval(lower=2.0, upper=2.0)),),
                                 eligible_hypotheses=("g",), timing_definition="observed_grid_brackets")
        metrics, errors, success = _event_metrics(target, payload, BiologyScoringThresholds())
        assert not success and metrics["event_f1"] == 0.0

    def test_curation_unknown_edges_never_become_false_negatives(self):
        from devbench.study.biology_answers import EnrichmentEstimate, EnrichmentPayload
        from devbench.study.biology_scoring import BiologyScoringThresholds, MembershipReference, TargetReference, _enrichment_metrics
        target = TargetReference(target_id="regulon_membership", estimand="membership", units="overlap", contrast="set overlap",
                                 reference_population="genes", prediction_reference="annotation", scope="limited_reference",
                                 evidence_scope="curated_membership", numerical_reference_available=True,
                                 memberships=(MembershipReference(regulator_id="tf", gene_ids=("a",)),), annotation_id="regulon_annotation")
        payload = EnrichmentPayload(enrichment_results=(EnrichmentEstimate(regulator_id="tf", selected_gene_ids=(), overlap_gene_ids=(), p_value=1.0, q_value=1.0,
                                                                          odds_ratio_status="undefined"),),
                                     feature_universe=("a", "b"), annotation_provenance="regulon_annotation", test_definition="one_sided_hypergeometric_membership",
                                     selection_conditional_inference=True)
        metrics, errors, unsupported, success = _enrichment_metrics(target, payload, ("a", "b"), BiologyScoringThresholds())
        assert metrics["enrichment_arithmetic_valid"] is True
        assert metrics["enrichment_calibration"] is None
        assert success is None  # no publicly bound query, no biological-success shortcut

    def test_all_gene_geometry_cannot_use_the_shared_process_reference(self):
        from devbench.study.biology_answers import SharedProcessProjection
        from devbench.study.biology_scoring import _declared_projection
        projection = SharedProcessProjection(excluded_feature_ids=("other",), retained_feature_universe=("marker", "process"))
        record = projection.model_dump(mode="json")
        with pytest.raises(ValueError, match="exactly the public marker union"):
            _declared_projection({"shared_process_projection": record},
                {"cell_annotation_reference": {"markers": ({"gene_ids": ("marker",)},)}},
                {"shared_process_projection": record}, ("marker", "process"))

    def test_projection_feature_order_is_bound_to_full_input_order(self):
        from devbench.study.biology_answers import SharedProcessProjection
        from devbench.study.biology_scoring import _declared_projection
        projection = SharedProcessProjection(excluded_feature_ids=("marker",), retained_feature_universe=("p1", "p2"))
        record = projection.model_dump(mode="json")
        with pytest.raises(ValueError, match="retain input order"):
            _declared_projection({"shared_process_projection": record},
                {"cell_annotation_reference": {"markers": ({"gene_ids": ("marker",)},)}},
                {"shared_process_projection": record}, ("marker", "p2", "p1"))

    def test_receipt_references_do_not_change_raw_scientific_grading(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case()
        without = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        target = answer.target_results[0].model_copy(update={"tool_evidence_ids": ("1" * 64,)})
        with_receipt = answer.model_copy(update={"target_results": (target,)})
        with_result = grade_biology_answer(with_receipt, reference=reference, allow_result_analysis=True)
        assert without.model_dump(exclude={"answer_sha256"}) == with_result.model_dump(exclude={"answer_sha256"})
        assert without.answer_sha256 != with_result.answer_sha256

    def test_missing_and_invalid_answers_preserve_reference_availability(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case()
        completed = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        missing = grade_biology_answer(None, reference=reference, allow_result_analysis=True)
        invalid = grade_biology_answer({"record_format": "invalid"}, reference=reference, allow_result_analysis=True)
        assert completed.target_reference_availability == missing.target_reference_availability == invalid.target_reference_availability
        assert missing.target_reference_availability == {"mean_contrast": "scorable"}
        assert missing.scientific_success is False and invalid.scientific_success is False

    def test_unavailable_target_prevents_full_primary_even_when_known_part_fails(self):
        from devbench.study.biology_scoring import grade_biology_answer
        reference, answer = self.effect_case(reported=(0.0,), p_value=1.0, q_value=1.0)
        unavailable = reference.targets[0].model_copy(update={"target_id": "variability", "hypotheses": (),
            "numerical_reference_available": False, "unsupported_reference_reason": "target_component_identifiers_missing"})
        reference = reference.model_copy(update={"targets": (*reference.targets, unavailable)})
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.target_reference_availability == {"mean_contrast": "scorable", "variability": "unavailable"}
        assert result.scientific_success is None
        assert result.available_target_scientific_success is False

    def test_intentional_refusal_is_scorable_without_numerical_truth(self):
        from devbench.study.biology_answers import BiologyAnswer, TargetAnswer, SupportCheck
        from devbench.study.biology_scoring import BiologyReference, TargetReference, grade_biology_answer
        common = dict(target_id="binding", estimand="measured binding", units="binding assay", contrast="binding evidence",
                      reference_population="loci", prediction_reference="supplied independent assays")
        target = TargetReference(**common, scope="refusal", evidence_scope="not_identified")
        reference = BiologyReference(task_id="one", input_sha256="0" * 64, gene_universe_sha256="0" * 64, targets=(target,))
        answer = BiologyAnswer(task_id="one", input_sha256="0" * 64, gene_universe_sha256="0" * 64,
            target_results=(TargetAnswer(**common, claim_type="refusal", evidence_scope="not_identified", estimate_status="unavailable",
                                         limitation_codes=("no_binding_assay",), support_checks=(SupportCheck(
                                             code="binding_assay_supplied", status="unknown", evidence_ids=("public_annotations",),
                                             explanation="Only expression and motif annotation were supplied; a binding assay is absent."),)),))
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.target_reference_availability == {"binding": "scorable"}
        assert result.scientific_success is True
        wrong = answer.model_copy(update={"target_results": (answer.target_results[0].model_copy(update={
            "support_checks": (SupportCheck(code="independent_biological_units", status="violated",
                evidence_ids=("metadata",), explanation="This is a different, unsupported reason."),)}),)})
        wrong_grade = grade_biology_answer(wrong, reference=reference, allow_result_analysis=True)
        assert wrong_grade.scientific_success is False

    def test_auxiliary_calibration_does_not_blank_protocol_and_restraint_correctness(self):
        from devbench.study.biology_answers import BiologyAnswer, DiagnosticValue, DiagnosticsPayload, TargetAnswer
        from devbench.study.biology_scoring import BiologyReference, TargetReference, grade_biology_answer
        common = dict(target_id="measurement_diagnostics", estimand="assay-qualified diagnosis", units="assay metadata",
                      contrast="measurement assumptions", reference_population="donors", prediction_reference="supplied protocol")
        values = dict(measurement_type="umi_counts", protocol="UMI molecule sampling", analysis_layer="counts",
                      biological_unit="donor", individual_artifact_identification="not_identified")
        target = TargetReference(**common, scope="supported", evidence_scope="measurement_diagnostic", numerical_reference_available=True,
                                 diagnostic_values=tuple(DiagnosticValue(code=key, value=value) for key, value in values.items()))
        reference = BiologyReference(task_id="one", input_sha256="0" * 64, gene_universe_sha256="0" * 64, targets=(target,))
        answer = BiologyAnswer(task_id="one", input_sha256="0" * 64, gene_universe_sha256="0" * 64,
            target_results=(TargetAnswer(**common, claim_type="supported_estimate", evidence_scope="measurement_diagnostic", estimate_status="estimated",
                                         payload=DiagnosticsPayload(**values)),))
        result = grade_biology_answer(answer, reference=reference, allow_result_analysis=True)
        assert result.target_reference_availability == {"measurement_diagnostics": "scorable"}
        assert result.scientific_success is True
        assert result.per_target_results[0].metrics["contamination_precision_recall"] is None

    def test_cosine_phase_is_transformed_between_equivalent_time_origins(self):
        import math
        from devbench.study.biology_scoring import _phase_at_reference, circular_distance
        # cos(2π*(t-1)/4) == cos(2π*t/4 - π/2).
        phase = _phase_at_reference(0.0, 4.0, 1.0, 0.0)
        assert circular_distance(phase, -math.pi / 2, allow_result_analysis=True) < 1e-12

    def test_unchanged_phase_at_half_period_origin_is_not_the_same_signal(self):
        import math
        from devbench.study.biology_scoring import _phase_at_reference, circular_distance
        phase = _phase_at_reference(0.0, 4.0, 2.0, 0.0)
        assert abs(circular_distance(phase, 0.0, allow_result_analysis=True) - math.pi) < 1e-12

    @staticmethod
    def rhythm_reference_arguments(convention):
        from devbench.study.biology import load_biology_catalog
        target = {"scope": "supported", "nonnull": (True,), "informative_gene_indices": (0,),
                  "period": 4.0, "phase_radians_by_condition": {"control": (0.0,)},
                  "phase_reference_time": 0.0, "damping_per_time_unit": 0.0, "amplitude_convention": "nonnegative"}
        if convention is not None:
            target["phase_convention"] = convention
        return dict(template=load_biology_catalog().template("T02"), private_truth={"targets": {"rhythm": target}},
                    public_context={"measurement": "raw_counts", "design": {"time_grid": (0.0, 4.0)},
                                    "rhythm_analysis_domain": {"frequency_cycles_per_time_unit": (0.125, 0.375)}},
                    public_annotations={}, task_id="one", input_sha256="0" * 64, gene_ids=("g",))

    def test_declared_sine_reference_converts_to_cosine_plus_phase(self):
        import math
        from devbench.study.biology_scoring import build_biology_reference, circular_distance
        reference = build_biology_reference(**self.rhythm_reference_arguments("sine_plus_phase"), allow_result_analysis=True)
        target = next(x for x in reference.targets if x.target_id == "rhythm")
        assert target.phase_convention == "cosine_plus_phase"
        assert circular_distance(target.rhythms[0].phases[0].phase_radians, -math.pi / 2, allow_result_analysis=True) < 1e-12

    def test_undeclared_reference_basis_is_not_guessed(self):
        from devbench.study.biology_scoring import build_biology_reference
        reference = build_biology_reference(**self.rhythm_reference_arguments(None), allow_result_analysis=True)
        target = next(x for x in reference.targets if x.target_id == "rhythm")
        assert target.numerical_reference_available is False
        assert target.unsupported_reference_reason == "rhythm_phase_convention_missing"
