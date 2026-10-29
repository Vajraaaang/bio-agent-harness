"""Bounded deterministic assessment reports and advisory review projections."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from tests.test_rna_publication_figures import file_ref


def api():
    name = "devbench.analysis.rna_publication_report"
    assert importlib.util.find_spec(name) is not None, "Independent assessment dimensions are unavailable"
    return __import__(name, fromlist=["build_assessment_report"])


def subject(root):
    from devbench.analysis.episode_proposals import _seal
    receipt = _seal(root / "subject", "comparison.json", {"fixture": "deterministic comparison"},
                    "analysis.synthetic_comparison")
    ref = {"manifest_path": receipt["manifest_path"], "root_hash": receipt["root_hash"]}
    return {"kind": "comparison", "subject_ref": ref, "plan_ref": None,
            "output_bundle_ref": None, "comparison_ref": ref}


def evidence(root):
    path = root / "observations.json"
    path.write_text(json.dumps({"complete_family": ["G1", "G2"], "p": [.01, .5], "q": [.02, .5]}))
    registry = [{"evidence_id": key, "kind": kind, "artifact_ref": file_ref(path),
        "summary": "Synthetic retained " + kind} for key, kind in [
            ("E1", "execution_receipt"), ("D1", "diagnostic"), ("F1", "tested_family"),
            ("M1", "numeric_assessment"), ("P1", "reviewed_plot"), ("S1", "source_claim")]]
    return {"artifact_registry": registry,
        "execution": {"status": "verified", "reason_codes": ["retained_output_receipts"], "evidence_ids": ["E1"]},
        "diagnostics": [{"diagnostic_id": "design", "status": "valid", "reason_codes": ["identifiable_design"], "evidence_ids": ["D1"]}],
        "tested_families": [{"family_id": "all_genes", "complete": True, "evidence_ids": ["F1"]}],
        "metric_results": [{"metric_id": "gene_effect_rmse", "profile_id": "mean_expression",
            "estimand_id": "effect1", "status": "computed", "value": .2, "units": "log2_ratio",
            "denominators": {"reference_rows": 2, "actual_rows": 2, "matched_rows": 2, "assessed_rows": 2},
            "universe_ref": file_ref(path), "exclusions_ref": file_ref(path), "uncertainty_ref": None,
            "reason_codes": [], "evidence_refs": [file_ref(path)]}],
        "numeric_assessments": [{"metric_id": "gene_effect_rmse", "decision": "agreement",
            "reason_codes": ["frozen_bounds_met"], "evidence_ids": ["M1"]}],
        "claims": [{"claim_id": "C1", "text": "The fixture shows a measured association.",
            "evidence_class": "numerical_association", "scope": "association", "status": "supported",
            "evidence_ids": ["M1", "S1", "P1"]}],
        "plots": [{"figure_id": "effects", "pixels_status": "inspected", "numeric_status": "assessed",
            "reason_codes": ["axes_and_membership_checked"], "evidence_ids": ["P1"]}],
        "exposure": {"prior_outcome_access": "unknown", "public_outcomes_accessible": "yes",
            "workflow_author_outcome_access": "unknown", "limitations": "Public synthetic fixture; no blinding established."},
        "access_control_status": "not_established",
        "omitted_evidence": [],
        "required_evidence": ["diagnostic", "tested_family", "numeric_assessment", "reviewed_plot", "source_claim"]}


def prepared(root, *, change=None):
    module = api()
    values = evidence(root)
    if change: change(values)
    return module.build_assessment_report(subject(root), values, workspace=root), values


def test_caller_invalid_method_and_high_agreement_are_preserved_as_unverified_declarations(tmp_path):
    report, _ = prepared(tmp_path, change=lambda value: value["diagnostics"][0].update(
        status="invalid", reason_codes=["repeated_biological_units"]))
    assert report["dimensions"]["methodology"]["state"] == "not_assessed"
    assert report["dimensions"]["publication_agreement"]["state"] == "not_assessed"
    assert report["evidence"]["diagnostics"][0]["status"] == "invalid"
    assert report["evidence"]["numeric_assessments"][0]["decision"] == "agreement"
    assert report["dimensions"]["interpretation"]["state"] != "supported"
    assert report["dimensions"]["qualified_review"]["state"] == "not_established"
    assert report["clinical_release_allowed"] is False


def test_declared_disagreement_and_abstention_survive_json_and_text_render(tmp_path):
    module = api()
    report, _ = prepared(tmp_path, change=lambda value: value["numeric_assessments"][0].update(
        decision="disagreement", reason_codes=["frozen_bounds_exceeded"]))
    assert report["dimensions"]["methodology"]["state"] == "not_assessed"
    assert report["dimensions"]["publication_agreement"]["state"] == "not_assessed"
    receipt = module.render_assessment_report(report, tmp_path / "report", allow_evidence_read=True, workspace=tmp_path)
    text = (tmp_path / "report/report.txt").read_text()
    saved = json.loads((tmp_path / "report/report.json").read_text())
    assert "disagreement" in text and "not_assessed" in text
    assert saved == report
    assert module.load_assessment_report(receipt["manifest_path"], receipt["root_hash"], allow_evidence_read=True) == report


def test_digit_leading_tested_family_fingerprint_survives_report_and_review_projection(tmp_path):
    from devbench.analysis.rna_publication_metrics import validate_gene_table
    from devbench.analysis.scientific_review import prepare_packet
    from devbench.analysis.scientific_review_agent import review_context
    from tests.test_rna_publication_metrics import gene_table
    from tests.test_scientific_review import research
    # Actual registered temporal output identity retained by the route-3 qualification.
    family_id = "2f355675be36ac9bacd74dd6911828d90dd690c403f04285272b7b7f2f8b3291"
    table = gene_table()
    table["family"]["family_id"] = family_id
    assert validate_gene_table(table)["family"]["family_id"] == family_id
    module = api()
    report, _ = prepared(tmp_path, change=lambda value: value["tested_families"][0].update(family_id=family_id))
    sealed = module.render_assessment_report(report, tmp_path / "report", allow_evidence_read=True, workspace=tmp_path)
    loaded = module.load_assessment_report(sealed["manifest_path"], sealed["root_hash"], allow_evidence_read=True)
    assert loaded == report
    assert loaded["evidence"]["tested_families"][0]["family_id"] == family_id
    text = (tmp_path / "report/report.txt").read_text()
    assert "supplied numeric assessment" in text and "Clinical release: held." in text
    prepare_packet(tmp_path / "packet", subject_kind="comparison", subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"], research=research(), allow_evidence_read=True)
    context = review_context(json.loads((tmp_path / "packet/packet.json").read_text()))
    assert context["sections"]["design"]["tested_families"][0]["family_id"] == family_id
    assert context["evidence_completeness"]["complete"] is False
    assert report["comparison_verification"] == "not_assessed"
    assert report["dimensions"]["qualified_review"]["state"] == "not_established"
    assert report["clinical_release_allowed"] is False


@pytest.mark.parametrize("family_id", ["2family", "2" * 63, "2" * 65, "2" + "g" * 63, "", None])
def test_malformed_tested_family_fingerprints_remain_invalid(tmp_path, family_id):
    with pytest.raises(ValueError):
        prepared(tmp_path, change=lambda value: value["tested_families"][0].update(family_id=family_id))


@pytest.mark.parametrize("field", ["evidence_id", "claim_id"])
def test_tested_family_fingerprint_does_not_widen_other_identifiers(tmp_path, field):
    values = evidence(tmp_path)
    key = "artifact_registry" if field == "evidence_id" else "claims"
    values[key][0][field] = "2f355675be36ac9bacd74dd6911828d90dd690c403f04285272b7b7f2f8b3291"
    with pytest.raises(ValueError):
        api().build_assessment_report(subject(tmp_path), values, workspace=tmp_path)


def test_report_loader_cannot_return_a_replacement_manifest_under_original_root(tmp_path, monkeypatch):
    module = api()
    values = evidence(tmp_path)
    retained_subject = subject(tmp_path)
    first = module.build_assessment_report(retained_subject, values, workspace=tmp_path)
    second_values = deepcopy(values)
    second_values["numeric_assessments"][0]["decision"] = "disagreement"
    second = module.build_assessment_report(retained_subject, second_values, workspace=tmp_path)
    first_ref = module.render_assessment_report(first, tmp_path / "first", allow_evidence_read=True, workspace=tmp_path)
    second_ref = module.render_assessment_report(second, tmp_path / "second", allow_evidence_read=True, workspace=tmp_path)
    original, manifest_reads = module._read, []
    def swap(path, maximum):
        path = Path(path)
        if path == Path(first_ref["manifest_path"]):
            manifest_reads.append(str(path))
            return original(path, maximum) if len(manifest_reads) == 1 else original(Path(second_ref["manifest_path"]), maximum)
        if path in {tmp_path / "first/report.json", tmp_path / "first/report.txt"}:
            return original(tmp_path / "second" / path.name, maximum)
        return original(path, maximum)
    monkeypatch.setattr(module, "_read", swap)
    with pytest.raises(ValueError):
        module.load_assessment_report(first_ref["manifest_path"], first_ref["root_hash"], allow_evidence_read=True)


def test_unrelated_reference_table_is_explicitly_omitted_from_comparison_plot_support(tmp_path):
    from tests.test_rna_publication_comparison_v2 import assess
    from devbench.analysis import figure_artifacts as figures
    from devbench.analysis.rna_comparison_outputs import load_comparison_outputs, load_comparison_gene_table
    service, _, _, assessed = assess(tmp_path)
    module = api()
    report_ref = assessed["report"]
    record = module.load_assessment_report(report_ref["manifest_path"], report_ref["root_hash"],
        allow_evidence_read=True, workspace=tmp_path, artifact_service=service)
    output_ref = record["subject"]["output_bundle_ref"]
    bundle = load_comparison_outputs(output_ref, artifact_service=service)
    actual_ref = bundle["gene_tables"][0]["table_ref"]
    unrelated = load_comparison_gene_table(actual_ref, artifact_service=service)
    unrelated["rows"][0]["effect"] += 99
    unrelated["rows"][0]["uncertainty"] = None
    source = tmp_path / "unrelated-saved-reference.json"
    source.write_text(json.dumps(unrelated, allow_nan=False))
    projected = figures.prepare_gene_effect_plot_payload(tmp_path / "wrong-reference-projection",
        reference_table_ref=file_ref(source), actual_table_ref=actual_ref,
        uncertainty_policy="report_absence", limitations=["Synthetic unrelated reference-table regression"])
    derived = figures.derive_publication_figure_binding(projected, output_bundle_ref=output_ref, artifact_service=service)
    retained = figures.export_publication_figure(tmp_path / "wrong-reference-figure", projected,
        figure_id="wrong-reference", analysis_binding=derived, artifact_service=service)
    reviewed = figures.record_publication_figure_review(tmp_path / "wrong-reference-review", retained["manifest_path"], retained["root_hash"],
        reviewer_label="Synthetic declaration; no independent qualification", observations=["Fixture inspection declaration only"], allow_review=True)
    values = deepcopy(record["evidence"])
    values["artifact_registry"].append({"evidence_id": "wrong-reference-plot", "kind": "reviewed_plot",
        "artifact_ref": {key: reviewed[key] for key in ("manifest_path", "root_hash")}, "summary": "Saved unrelated reference plot"})
    values["plots"] = [{"figure_id": "wrong-reference", "pixels_status": "inspected", "numeric_status": "assessed",
        "reason_codes": ["supplied_declaration"], "evidence_ids": ["wrong-reference-plot"]}]
    checked = module.build_assessment_report(record["subject"], values, workspace=tmp_path, artifact_service=service)
    assert any(item["kind"] == "reviewed_plot" and item["reason"] == "not_comparison_bound" for item in checked["omitted_evidence"])
    assert checked["dimensions"]["interpretation"]["state"] == "not_assessed"


@pytest.mark.parametrize("drift", ["reference_value", "actual_source", "metric_pair", "not_computed"])
def test_post_validation_plot_binding_requires_exact_tables_sources_and_same_computed_metric(tmp_path, drift):
    from tests.test_rna_publication_metrics import gene_table
    from devbench.analysis.figure_artifacts import prepare_gene_effect_plot_payload
    module = api()
    assert hasattr(module, "_comparison_plot_tables_bound"), "Comparison plot reference binding is unavailable"
    table = gene_table()
    reference_path, actual_path = tmp_path / "reference.json", tmp_path / "actual.json"
    for path in (reference_path, actual_path): path.write_text(json.dumps(table, allow_nan=False))
    reference_ref, actual_ref = file_ref(reference_path), file_ref(actual_path)
    payload = prepare_gene_effect_plot_payload(tmp_path / "projected", reference_table_ref=reference_ref,
        actual_table_ref=actual_ref, uncertainty_policy="report_absence", limitations=["Synthetic post-validation binding check"])
    context = {"gene_tables": [
        {"role": "reference", "metric_ids": ["gene_effect_rmse"], "table": deepcopy(table), "source_evidence_refs": [reference_ref]},
        {"role": "actual", "metric_ids": ["gene_effect_rmse"], "table": deepcopy(table), "source_evidence_refs": [actual_ref]}]}
    computed = {"gene_effect_rmse"}
    # This private arithmetic/source seam follows trusted comparison loading; it grants no authority.
    assert module._comparison_plot_tables_bound(payload, context, computed) is True
    if drift == "reference_value": context["gene_tables"][0]["table"]["rows"][0]["effect"] += 99
    elif drift == "actual_source": context["gene_tables"][1]["source_evidence_refs"] = [reference_ref]
    elif drift == "metric_pair": context["gene_tables"][1]["metric_ids"] = ["gene_table_coverage"]
    else: computed.clear()
    assert module._comparison_plot_tables_bound(payload, context, computed) is False


@pytest.mark.parametrize("missing", ["plots", "tested_families", "numeric_assessments"])
def test_missing_statistical_or_pixel_context_abstains(tmp_path, missing):
    report, _ = prepared(tmp_path, change=lambda value: value.update({missing: []}))
    assert report["dimensions"]["interpretation"]["state"] == "not_assessed"
    assert report["omitted_evidence"]
    receipt = api().render_assessment_report(report, tmp_path / "report", allow_evidence_read=True, workspace=tmp_path)
    assert receipt["root_hash"] and "not_assessed" in (tmp_path / "report/report.txt").read_text()


def test_unreviewed_pixels_and_absent_numeric_geometry_remain_not_assessed(tmp_path):
    report, _ = prepared(tmp_path, change=lambda value: value["plots"][0].update(
        pixels_status="not_assessed", numeric_status="not_assessed"))
    assert report["dimensions"]["interpretation"]["state"] == "not_assessed"


@pytest.mark.parametrize("mutation", ["unknown_ref", "causality", "qualification", "nonfinite", "unknown_field"])
def test_closed_report_evidence_rejects_fabricated_citations_and_authority(tmp_path, mutation):
    module = api()
    value = evidence(tmp_path)
    if mutation == "unknown_ref": value["claims"][0]["evidence_ids"].append("fabricated")
    elif mutation == "causality": value["claims"][0].update(scope="causal", status="supported")
    elif mutation == "qualification": value["qualified_review"] = {"state": "qualified", "reviewer_id": "different_actor"}
    elif mutation == "nonfinite": value["numeric_assessments"][0]["value"] = float("nan")
    else: value["source_search_granted"] = True
    with pytest.raises(ValueError): module.build_assessment_report(subject(tmp_path), value, workspace=tmp_path)


def test_trial_comparison_packet_projects_resolved_citations_without_paths_or_outcomes(tmp_path):
    module = api()
    report, _ = prepared(tmp_path)
    sealed = module.render_assessment_report(report, tmp_path / "report", allow_evidence_read=True, workspace=tmp_path)
    from devbench.analysis.scientific_review import prepare_packet
    from devbench.analysis.scientific_review_agent import review_context, review_schema
    from tests.test_scientific_review import research
    packet = prepare_packet(tmp_path / "packet", subject_kind="comparison", subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"], research=research(), allow_evidence_read=True)
    stored = json.loads((tmp_path / "packet/packet.json").read_text())
    assert stored["subject"]["kind"] == "comparison"
    context = review_context(stored)
    assert str(tmp_path) not in json.dumps(context)
    assert context["evidence_completeness"]["complete"] is False
    assert "qualified" not in context.get("qualification_status", "")
    import jsonschema
    schema = review_schema(context)
    answer = {"kind": "scientific_review", "disposition": "needs_information", "rationale": "Advisory only",
        "findings": [{"claim_id": "C1", "section": "numerical_results", "status": "concern",
            "reason": "Investigate discrepancy", "evidence_ids": ["M1"], "claim_scope": "hypothesis"}],
        "questions": [], "limitations": ["No qualified scientific judgment."]}
    jsonschema.validate(answer, schema)
    answer["findings"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(answer, schema)
    answer["findings"][0]["evidence_ids"] = ["M1"]
    answer["findings"][0]["effect"] = 999
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(answer, schema)
    assert packet["root_hash"]


def test_well_hashed_unrelated_artifacts_and_positive_labels_cannot_promote_report(tmp_path):
    report, _ = prepared(tmp_path)
    assert report["comparison_verification"] == "not_assessed"
    assert all(item["state"] in {"not_assessed", "not_established"} for item in report["dimensions"].values())
    assert any(item["reason"] == "trusted_comparison_not_revalidated" for item in report["omitted_evidence"])


@pytest.mark.parametrize("method,agreement,expected", [("invalid", "agreement", "unsupported"),
    ("valid", "disagreement", "not_assessed")])
def test_post_validation_state_aggregation_keeps_method_and_agreement_separate(tmp_path, method, agreement, expected):
    # This checks only aggregation after trust validation. The public API tests
    # above show that these raw declarations alone cannot reach this stage.
    module = api()
    value = evidence(tmp_path)
    value["diagnostics"][0]["status"] = method
    value["numeric_assessments"][0]["decision"] = agreement
    result = module._derive_dimensions(value, revalidated=True, available={"diagnostic": True, "tested_family": True},
        omissions=[], reviewed_claims=False, reviewed_plots=False)
    assert result["methodology"]["state"] == ("invalid" if method == "invalid" else "adequate")
    assert result["publication_agreement"]["state"] == agreement
    assert result["interpretation"]["state"] == expected
    assert result["qualified_review"]["state"] == "not_established"


@pytest.mark.parametrize("disposition", ["accept", "reject"])
def test_truncated_projection_forbids_confident_model_verdict(tmp_path, disposition):
    report, _ = prepared(tmp_path, change=lambda value: value["omitted_evidence"].append(
        {"kind": "diagnostic", "reason": "projection_byte_limit", "count": 1}))
    module = api()
    sealed = module.render_assessment_report(report, tmp_path / "report", allow_evidence_read=True, workspace=tmp_path)
    from devbench.analysis.scientific_review import prepare_packet, record_scientific_review
    from tests.test_scientific_review import research, assessment
    packet = prepare_packet(tmp_path / "packet", subject_kind="comparison", subject_path=sealed["manifest_path"],
        expected_root=sealed["root_hash"], research=research(), allow_evidence_read=True)
    answer = assessment(); answer.update(disposition=disposition, findings=[])
    with pytest.raises(ValueError, match="incomplete"):
        record_scientific_review(tmp_path / "bad-review", packet["manifest_path"], expected_root=packet["root_hash"],
            reviewer_label="a-different-id-does-not-qualify", assessment=answer,
            qualification_statement="qualified by myself", independence_statement="different ID",
            allow_review=True)
