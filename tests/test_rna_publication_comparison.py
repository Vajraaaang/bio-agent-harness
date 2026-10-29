"""Artificial curated reference values, never scientific/public-data trial proof."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from devbench.analysis.checkpoints import digest
from devbench.analysis import rna_publication_comparison as api
from devbench.analysis import rna_publication_evidence as evidence_api
from tests.test_rna_publication_evidence import evidence_record, seal_evidence, unit_storage


HASH = "a" * 64
ALIGNMENT = {"gene": "GENE1", "statistic": "effect", "units": "log2 fold change", "contrast": "B-minus-A",
             "cohort": "fixture-cohort", "preprocessing": "fixture-v1", "hypothesis_family": "all-fixture-genes"}
INTERVAL = {"lower": 1.0, "upper": 3.0, "level": .95, "kind": "confidence"}


def protocol():
    from tests.test_rna_publication_evidence import EXPOSURE
    return {"format": api.FORMAT, "mode": "independent_method_selection", "question_sha256": HASH,
        "dataset_sha256": HASH, "workflow_sha256": HASH, "participant_id": "workflow-author",
        "curator_id": "independent-curator", "exposure_sha256": digest(EXPOSURE),
        "endpoints": [{"endpoint_id": "effect1", "alignment": deepcopy(ALIGNMENT),
            "absolute_tolerance": .2, "relative_tolerance": .05, "tolerance_rationale": "Fixture arithmetic tolerance only.",
            "required": True, "uncertainty_policy": "require_overlap", "direction_policy": "require_same_sign"}],
        "missingness_policy": "report_and_abstain", "decision_rule": "all_required_endpoints",
        "abstention_policy": "Missing/failed/incomparable required endpoints abstain.",
        "limitations": "Artificial endpoint comparison only; no biological validation."}


def references():
    return [{"endpoint_id": "effect1", "alignment": deepcopy(ALIGNMENT), "excerpt_id": "outcome1",
        "value": 2.0, "value_text": "2.0", "uncertainty": deepcopy(INTERVAL),
        "extraction_reviewed_by": "independent-curator", "extraction_method": "Independent fixture-table transcription.",
        "limitations": "Numeric transcription correspondence cannot establish semantic correctness."}]


def outputs():
    return {"format": "devbench.rna-publication-outputs/1", "workflow_sha256": HASH,
        "endpoints": [{"endpoint_id": "effect1", "alignment": deepcopy(ALIGNMENT), "status": "success",
            "value": 2.1, "uncertainty": deepcopy(INTERVAL), "reason": "Artificial retained output."}],
        "plots": [], "operator_interventions": [], "limitations": "Fixture; no rendered scientific plot."}


def prepare(unit_storage, *, selected_protocol=None, selected_references=None):
    _, publication = seal_evidence(unit_storage)
    binding = api.prepare_publication_comparison(unit_storage / "comparison", selected_protocol or protocol(),
        selected_references or references(), publication["manifest_path"], publication["root_hash"], allow_evidence_read=True)
    return binding


def compare(binding, actual=None, **kwargs):
    actual = actual or outputs()
    return api.compare_publication_outputs(binding["manifest_path"], binding["root_hash"], outputs=actual,
        workflow_sha256=HASH, outputs_sha256=digest(actual), allow_evidence_read=True, **kwargs)


def test_independent_frozen_comparison_reports_agreement_and_limits(unit_storage):
    binding = prepare(unit_storage)
    result = compare(binding)
    assert result["status"] == "agreement" and result["denominator"] == result["assessed_count"] == 1
    assert result["endpoints"][0]["allowed_difference"] == pytest.approx(.3)
    assert result["exposure_scope"] == "exploratory_unblinded" and result["blinding"] == "not_established"
    assert result["plot_assessment"] == "not_performed" and result["plots"] == []
    assert result["independent_scientific_review"] == "pending"
    assert result["scientific_optimality_established"] is False and result["clinical_release_allowed"] is False


def test_supported_numeric_tokens_preserve_sign_exponent_and_sentence_punctuation():
    assert api._numeric_tokens("effect -1.2; standard error 1e-3; level 0.95.") == ["-1.2", "1e-3", "0.95"]
    assert api._numeric_tokens("effect 2.0, lower 1.0, upper 3.0.") == ["2.0", "1.0", "3.0"]


@pytest.mark.parametrize("mode", ["exact_reproduction", "independent_method_selection", "external_validation"])
def test_each_explicit_comparison_mode_is_supported(mode):
    selected = protocol()
    selected["mode"] = mode
    assert api.validate_publication_protocol(selected)["mode"] == mode


@pytest.mark.parametrize("attack", ["self_review", "unknown", "nan", "negative_tolerance", "no_required", "change_missingness"])
def test_invalid_protocols_rejected(attack):
    selected = protocol()
    if attack == "self_review":
        selected["curator_id"] = selected["participant_id"]
    elif attack == "unknown":
        selected["extra"] = True
    elif attack == "nan":
        selected["endpoints"][0]["relative_tolerance"] = float("nan")
    elif attack == "negative_tolerance":
        selected["endpoints"][0]["absolute_tolerance"] = -1
    elif attack == "no_required":
        selected["endpoints"][0]["required"] = False
    else:
        selected["missingness_policy"] = "drop_failures"
    with pytest.raises(ValueError):
        api.validate_publication_protocol(selected)


@pytest.mark.parametrize("field", list(ALIGNMENT))
def test_actual_endpoint_alignment_mismatch_is_incomparable_abstention(unit_storage, field):
    binding = prepare(unit_storage)
    actual = outputs()
    actual["endpoints"][0]["alignment"][field] = "different"
    result = compare(binding, actual)
    assert result["status"] == "abstained" and result["endpoints"][0]["status"] == "incomparable"
    assert result["endpoints"][0]["absolute_difference"] is None


@pytest.mark.parametrize("status", ["missing", "failed", "abstained", "not_estimable", "tool_error", "budget_exhausted"])
def test_missing_or_failed_estimates_are_not_successes(unit_storage, status):
    binding = prepare(unit_storage)
    actual = outputs()
    actual["endpoints"][0].update(status=status, value=None, uncertainty=None, reason="Retained fixture failure.")
    result = compare(binding, actual)
    assert result["status"] == "abstained" and result["assessed_count"] == 0
    assert result["endpoints"][0]["status"] == status


def test_absent_output_and_uncertainty_remain_explicit(unit_storage):
    binding = prepare(unit_storage)
    actual = outputs()
    actual["endpoints"] = []
    result = compare(binding, actual)
    assert result["status"] == "abstained" and result["denominator"] == 1
    actual = outputs()
    actual["endpoints"][0]["uncertainty"] = None
    assert compare(binding, actual)["endpoints"][0]["status"] == "missing_uncertainty"


def test_disagreement_preserves_actual_values_without_invalidity_claim(unit_storage):
    binding = prepare(unit_storage)
    actual = outputs()
    actual["endpoints"][0]["value"] = -2
    result = compare(binding, actual)
    assert result["status"] == "differences_require_review"
    assert result["endpoints"][0]["direction_agrees"] is False
    assert result["endpoints"][0]["actual_value"] == -2


def test_tampered_protocol_source_and_output_seal_are_rejected(unit_storage):
    binding = prepare(unit_storage)
    actual = outputs()
    with pytest.raises(ValueError, match="pre-reference seal"):
        api.compare_publication_outputs(binding["manifest_path"], binding["root_hash"], outputs=actual,
            workflow_sha256=HASH, outputs_sha256="b" * 64, allow_evidence_read=True)
    actual["workflow_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="workflow"):
        compare(binding, actual)
    path = unit_storage / "comparison/publication-comparison.json"
    path.write_text(path.read_text().replace("Fixture arithmetic", "Altered arithmetic"))
    with pytest.raises(ValueError):
        compare(binding)


@pytest.mark.parametrize("attack", ["forged_value", "alignment", "unknown_excerpt", "reviewer", "interval"])
def test_forged_or_unreviewed_reference_values_rejected(unit_storage, attack):
    selected = references()
    if attack == "forged_value":
        selected[0].update(value=9, value_text="9")
    elif attack == "alignment":
        selected[0]["alignment"]["units"] = "fold change"
    elif attack == "unknown_excerpt":
        selected[0]["excerpt_id"] = "methods1"
    elif attack == "reviewer":
        selected[0]["extraction_reviewed_by"] = "workflow-author"
    else:
        selected[0]["uncertainty"]["upper"] = 99
    with pytest.raises(ValueError):
        prepare(unit_storage, selected_references=selected)


@pytest.mark.parametrize("quote,forged,token", [("effect=12.34", 2, "2"), ("effect=-1.2", 1.2, "1.2"),
    ("effect=1e-3", 1, "1"), ("effect=1e-3", -3, "-3"), ("effect=−1.2", 1.2, "1.2"),
    ("effect=–1.2", 1.2, "1.2"), ("effect=—1.2", 1.2, "1.2"), ("effect=1,234.5", 1, "1"),
    ("effect=1,234.5", 234.5, "234.5"), ("effect=1e−3", 1, "1"), ("effect=1.234,5", 1.234, "1.234")])
def test_numeric_substring_sign_and_exponent_forgery_rejected(tmp_path, quote, forged, token):
    record = evidence_record(tmp_path)
    path = Path(record["sources"][0]["path"])
    body = path.read_bytes() + quote.encode()
    path.write_bytes(body)
    record["sources"][0]["sha256"] = hashlib.sha256(body).hexdigest()
    record["excerpts"][1]["locator"] = {"start_byte": len(body) - len(quote.encode()),
        "end_byte": len(body), "text": quote, "location": "artificial outcome"}
    evidence_api.validate_publication_evidence(record, allow_evidence_read=True)
    selected = references()
    selected[0].update(value=forged, value_text=token, uncertainty=None)
    with pytest.raises(ValueError, match="exact excerpt"):
        api._references(protocol(), selected, record)


def test_unqualified_percent_confidence_level_is_explicitly_rejected(tmp_path):
    record = evidence_record(tmp_path)
    record["excerpts"][1]["locator"]["text"] = "effect 2.0; 95% confidence interval lower 1.0 upper 3.0"
    with pytest.raises(ValueError, match="interval values lack exact"):
        api._references(protocol(), references(), record)


def test_failure_values_unknown_endpoint_and_nonfinite_output_rejected(unit_storage):
    binding = prepare(unit_storage)
    for change in ({"status": "failed"}, {"endpoint_id": "invented"}, {"value": float("inf")}):
        actual = outputs()
        actual["endpoints"][0].update(change)
        with pytest.raises(ValueError):
            compare(binding, actual)


def test_comparator_evidence_authority_is_default_denied(unit_storage):
    binding = prepare(unit_storage)
    with pytest.raises(PermissionError):
        api.load_publication_comparison(binding["manifest_path"], binding["root_hash"])
