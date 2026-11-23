"""Frozen, independently curated scalar publication endpoint comparisons.

This module checks declared endpoint alignment and frozen numerical tolerances.
It never chooses the best method, authenticates a curator, validates biological
causality or reviews pixels. Public exposure and absent assessments stay visible.
References live in evaluator storage, outside participant projection.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
import hashlib

from .checkpoints import digest
from .episode_proposals import _absolute, _load, _seal, require_disjoint_output
from .rna_publication_evidence import (_copy, _identifier, _keys, _list, _sha, _text,
    load_publication_evidence, validate_exposure)


FORMAT = "devbench.rna-publication-comparison/1"
FORMAT_V2 = "devbench.rna-publication-comparison/2"
ASSESSMENT_FORMAT_V2 = "devbench.rna-comparison-assessment/2"
_ALIGNMENT = {"gene", "statistic", "units", "contrast", "cohort", "preprocessing", "hypothesis_family"}
_MODES = {"exact_reproduction", "independent_method_selection", "external_validation"}
_FAILURES = {"missing", "failed", "abstained", "not_estimable", "tool_error", "budget_exhausted"}
_SCOPE = "matched_publication_endpoints_not_optimality_or_clinical_validation"


def _number(value, *, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0):
        raise ValueError("Use finite publication endpoint numbers")
    return value


def _alignment(value):
    _keys(value, _ALIGNMENT)
    for field in _ALIGNMENT:
        _text(value[field], 1000)


def _uncertainty(value):
    if value is None:
        return
    _keys(value, {"lower", "upper", "level", "kind"})
    _number(value["lower"])
    _number(value["upper"])
    if value["lower"] > value["upper"] or not 0 < _number(value["level"]) < 1:
        raise ValueError("Invalid declared publication uncertainty interval")
    if value["kind"] not in {"confidence", "credible", "range"}:
        raise ValueError("Declare the actual interval kind")


def _numeric_tokens(text):
    # First consume complete lexical numeric runs, including common publication
    # typography. Then fail closed on unsupported signs/grouping, never stripping
    # a Unicode minus or selecting a fragment from a comma-separated magnitude.
    # Locale/grouped values and percent confidence-level conversion require a
    # separately qualified extraction contract; no silent normalization occurs.
    signs = r"+\-\u2212\u2013\u2014\ufe63\uff0d\uff0b\u00b1\u2213"
    pattern = (rf"(?<![\w.,{signs}])[{signs}]?"
               rf"(?:[0-9]+(?:[.,][0-9]+)*|\.[0-9]+)(?:[eE][{signs}]?[0-9]+)?"
               rf"(?![\w{signs}]|[.,][0-9])")
    tokens = re.findall(pattern, text)
    ascii_numeric = r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
    return [token for token in tokens if re.fullmatch(ascii_numeric, token)]


def validate_publication_protocol(protocol):
    protocol = _copy(protocol)
    _keys(protocol, {"format", "mode", "question_sha256", "dataset_sha256", "workflow_sha256",
        "participant_id", "curator_id", "exposure_sha256", "endpoints", "missingness_policy",
        "decision_rule", "abstention_policy", "limitations"})
    if protocol["format"] != FORMAT or protocol["mode"] not in _MODES:
        raise ValueError("Unsupported publication comparison format or objective")
    for field in ("question_sha256", "dataset_sha256", "workflow_sha256", "exposure_sha256"):
        _sha(protocol[field])
    if _identifier(protocol["participant_id"]) == _identifier(protocol["curator_id"]):
        raise ValueError("Publication endpoint curation must be independent of the participant")
    if protocol["missingness_policy"] != "report_and_abstain" or protocol["decision_rule"] != "all_required_endpoints":
        raise ValueError("Use explicit missingness and all-required-endpoint comparison rules")
    _text(protocol["abstention_policy"])
    _text(protocol["limitations"])
    seen = set()
    for endpoint in _list(protocol["endpoints"], 128, nonempty=True):
        _keys(endpoint, {"endpoint_id", "alignment", "absolute_tolerance", "relative_tolerance",
            "tolerance_rationale", "required", "uncertainty_policy", "direction_policy"})
        key = _identifier(endpoint["endpoint_id"])
        if key in seen:
            raise ValueError("Duplicate publication comparison endpoint")
        seen.add(key)
        _alignment(endpoint["alignment"])
        for field in ("absolute_tolerance", "relative_tolerance"):
            _number(endpoint[field], nonnegative=True)
        _text(endpoint["tolerance_rationale"])
        if type(endpoint["required"]) is not bool:
            raise ValueError("Declare endpoint necessity")
        if endpoint["uncertainty_policy"] not in {"report_only", "require_overlap"}:
            raise ValueError("Freeze supported uncertainty criteria")
        if endpoint["direction_policy"] not in {"report_only", "require_same_sign"}:
            raise ValueError("Freeze supported effect direction criteria")
    if not any(endpoint["required"] for endpoint in protocol["endpoints"]):
        raise ValueError("Freeze at least one required endpoint")
    return protocol


def _references(protocol, references, evidence):
    expected = {endpoint["endpoint_id"]: endpoint for endpoint in protocol["endpoints"]}
    quotes = {item["excerpt_id"]: item for item in evidence["excerpts"]}
    seen = set()
    for reference in _list(references, 128, nonempty=True):
        _keys(reference, {"endpoint_id", "alignment", "excerpt_id", "value", "value_text", "uncertainty",
            "extraction_reviewed_by", "extraction_method", "limitations"})
        key = _identifier(reference["endpoint_id"])
        if key in seen or key not in expected:
            raise ValueError("Duplicate or unknown curated publication endpoint")
        seen.add(key)
        _alignment(reference["alignment"])
        if reference["alignment"] != expected[key]["alignment"]:
            raise ValueError("Curated endpoint units, gene, statistic, contrast or cohort mismatch")
        quote = quotes.get(reference["excerpt_id"])
        if quote is None or quote["purpose"] != "outcome" or quote["visibility"] != "evaluator":
            raise ValueError("Reference outputs require evaluator-held exact outcome excerpts")
        _number(reference["value"])
        text = _text(reference["value_text"], 100)
        try:
            value = float(text)
        except ValueError as error:
            raise ValueError("Use the verbatim finite numeric token from the reference") from error
        tokens = _numeric_tokens(quote["locator"]["text"])
        if not math.isfinite(value) or value != reference["value"] or text not in tokens:
            raise ValueError("Publication value differs from its independently curated exact excerpt")
        _uncertainty(reference["uncertainty"])
        if reference["uncertainty"] is not None and any(
                reference["uncertainty"][field] not in [float(token) for token in tokens]
                for field in ("lower", "upper", "level")):
            raise ValueError("Reference interval values lack exact outcome excerpt numeric evidence")
        if reference["extraction_reviewed_by"] != protocol["curator_id"]:
            raise ValueError("Reference extraction lacks the declared independent review")
        _text(reference["extraction_method"], 1000)
        _text(reference["limitations"])
    if seen != set(expected):
        raise ValueError("Freeze every reference endpoint before workflow outputs")


def prepare_publication_comparison(output, protocol, references, publication_manifest, publication_root, *,
                                   allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Publication comparison preparation requires evidence-read authority")
    protocol, references = validate_publication_protocol(protocol), _copy(references)
    evidence = load_publication_evidence(publication_manifest, publication_root, allow_evidence_read=True)
    if (protocol["curator_id"] != evidence["curator_id"] or protocol["participant_id"] != evidence["participant_id"]
            or protocol["exposure_sha256"] != digest(evidence["exposure"])):
        raise ValueError("Publication comparison curator, participant or exposure binding mismatch")
    _references(protocol, references, evidence)
    record = {"format": FORMAT, "protocol": protocol, "references": references,
        "publication_manifest": str(_absolute(publication_manifest)), "publication_root": _sha(publication_root),
        "scope": _SCOPE}
    output = require_disjoint_output(_absolute(output), [_absolute(publication_manifest).parent.parent,
                                                       *[source["path"] for source in evidence["sources"]]])
    receipt = _seal(output, "publication-comparison.json", record, "analysis.publication_comparison", scope=_SCOPE)
    return {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"]}


def load_publication_comparison(manifest, expected_root, *, allow_evidence_read=False, workspace=None, artifact_service=None):
    if allow_evidence_read is not True:
        raise PermissionError("Publication comparison reading requires evidence-read authority")
    _, record, _ = _load(_absolute(manifest), _sha(expected_root), "publication-comparison.json")
    if type(record) is dict and record.get("format") == FORMAT_V2:
        return _assessment_protocol(record, workspace=workspace, artifact_service=artifact_service)
    _keys(record, {"format", "protocol", "references", "publication_manifest", "publication_root", "scope"})
    if record["format"] != FORMAT or record["scope"] != _SCOPE:
        raise ValueError("Unsupported publication comparison scope")
    protocol = validate_publication_protocol(record["protocol"])
    evidence = load_publication_evidence(record["publication_manifest"], record["publication_root"], allow_evidence_read=True)
    if (protocol["curator_id"] != evidence["curator_id"] or protocol["participant_id"] != evidence["participant_id"]
            or protocol["exposure_sha256"] != digest(evidence["exposure"])):
        raise ValueError("Publication comparison curator, participant or exposure binding mismatch")
    _references(protocol, record["references"], evidence)
    return record


def _outputs(outputs):
    outputs = _copy(outputs)
    _keys(outputs, {"format", "workflow_sha256", "endpoints", "plots", "operator_interventions", "limitations"})
    if outputs["format"] != "devbench.rna-publication-outputs/1":
        raise ValueError("Unsupported publication output format")
    _sha(outputs["workflow_sha256"])
    _text(outputs["limitations"])
    for field in ("plots", "operator_interventions"):
        for item in _list(outputs[field], 64):
            _text(item)
    seen = set()
    for output in _list(outputs["endpoints"], 128):
        _keys(output, {"endpoint_id", "alignment", "status", "value", "uncertainty", "reason"})
        key = _identifier(output["endpoint_id"])
        if key in seen:
            raise ValueError("Duplicate actual output endpoint")
        seen.add(key)
        _alignment(output["alignment"])
        _text(output["reason"])
        if output["status"] == "success":
            _number(output["value"])
            _uncertainty(output["uncertainty"])
        elif output["status"] in _FAILURES:
            if output["value"] is not None or output["uncertainty"] is not None:
                raise ValueError("Failed/abstained outputs cannot carry successful-looking estimates")
        else:
            raise ValueError("Declare actual endpoint output status")
    return outputs


def compare_publication_outputs(manifest, expected_root, *, outputs, workflow_sha256, outputs_sha256,
                                allow_evidence_read=False):
    """Apply frozen tolerances to output bytes sealed by the coordinator.

    The caller supplies an independently retained digest of outputs sealed before
    reference access, plus the accepted workflow hash. Hash consistency cannot
    prove chronology, custodial isolation, authorship or validity of outputs.
    """
    outputs = _outputs(outputs)
    if digest(outputs) != _sha(outputs_sha256):
        raise ValueError("Actual outputs differ from the independently retained pre-reference seal")
    record = load_publication_comparison(manifest, expected_root, allow_evidence_read=allow_evidence_read)
    protocol = record["protocol"]
    if protocol["workflow_sha256"] != _sha(workflow_sha256) or outputs["workflow_sha256"] != workflow_sha256:
        raise ValueError("Publication comparison workflow binding mismatch")
    observed = {output["endpoint_id"]: output for output in outputs["endpoints"]}
    expected = {endpoint["endpoint_id"] for endpoint in protocol["endpoints"]}
    if set(observed) - expected:
        raise ValueError("Unexpected output endpoints cannot change the frozen comparison denominator")
    references = {reference["endpoint_id"]: reference for reference in record["references"]}
    assessments, required_statuses = [], []
    for endpoint in protocol["endpoints"]:
        key = endpoint["endpoint_id"]
        output, reference = observed.get(key), references[key]
        item = {"endpoint_id": key, "required": endpoint["required"], "reference_value": reference["value"],
                "actual_value": None, "absolute_difference": None, "allowed_difference": None,
                "direction_agrees": None, "uncertainty_overlap": None, "status": "missing",
                "reason": "No actual output was retained for this frozen endpoint."}
        if output is not None:
            item["reason"] = output["reason"]
            if output["alignment"] != endpoint["alignment"]:
                item["status"] = "incomparable"
                item["reason"] = "Actual gene/statistic/units/contrast/cohort/preprocessing/family differs from the frozen endpoint."
            elif output["status"] != "success":
                item["status"] = output["status"]
            else:
                actual, target = output["value"], reference["value"]
                difference = abs(actual - target)
                tolerance = endpoint["absolute_tolerance"] + endpoint["relative_tolerance"] * abs(target)
                if not math.isfinite(difference) or not math.isfinite(tolerance):
                    raise ValueError("Publication endpoint arithmetic overflow")
                direction = (actual > 0) - (actual < 0) == (target > 0) - (target < 0)
                uncertainty = None
                if output["uncertainty"] is not None and reference["uncertainty"] is not None:
                    left, right = output["uncertainty"], reference["uncertainty"]
                    if left["level"] == right["level"] and left["kind"] == right["kind"]:
                        uncertainty = max(left["lower"], right["lower"]) <= min(left["upper"], right["upper"])
                required_missing = endpoint["uncertainty_policy"] == "require_overlap" and uncertainty is None
                agrees = (difference <= tolerance and (endpoint["direction_policy"] != "require_same_sign" or direction)
                          and (endpoint["uncertainty_policy"] != "require_overlap" or uncertainty is True))
                item.update(actual_value=actual, absolute_difference=difference, allowed_difference=tolerance,
                            direction_agrees=direction, uncertainty_overlap=uncertainty,
                            status="missing_uncertainty" if required_missing else "agrees" if agrees else "differs")
        assessments.append(item)
        if endpoint["required"]:
            required_statuses.append(item["status"])
    exposure = load_publication_evidence(record["publication_manifest"], record["publication_root"], allow_evidence_read=True)["exposure"]
    validate_exposure(exposure)
    status = ("abstained" if any(value not in {"agrees", "differs"} for value in required_statuses)
              else "agreement" if all(value == "agrees" for value in required_statuses) else "differences_require_review")
    return {"format": "devbench.rna-publication-assessment/1", "comparison_root": expected_root,
        "workflow_sha256": workflow_sha256, "outputs_sha256": outputs_sha256,
        "status": status, "endpoints": assessments, "denominator": len(assessments),
        "assessed_count": sum(item["status"] in {"agrees", "differs"} for item in assessments),
        "exposure": exposure, "blinding": "not_established",
        "exposure_scope": "exploratory_unblinded" if any(exposure[field] != "no" for field in
            ("prior_outcome_access", "public_outcomes_accessible", "workflow_author_outcome_access")) else "declared_no_exposure_not_independently_verified",
        "plots": outputs["plots"], "plot_assessment": "not_performed",
        "operator_interventions": outputs["operator_interventions"],
        "independent_scientific_review": "pending", "scientific_optimality_established": False,
        "clinical_release_allowed": False, "scope": _SCOPE,
        "limitations": [protocol["limitations"], outputs["limitations"],
            "Scalar alignment and exact numeric excerpt correspondence do not establish interpretation, scientific truth, causal effects or qualified reviewer identity.",
            "Hash seals alone do not prove that reference access occurred after output freeze."]}


_V2_PROTOCOL_KEYS = {"format", "assessment_plan_ref", "assessment_intent_ref", "assessment_rules_sha256",
    "reference_result_set_ref", "scope"}
_V2_ASSESSMENT_KEYS = {"format", "comparison_ref", "assessment_plan_ref", "output_bundle_ref", "reference_result_set_ref",
    "assessment_rules_sha256", "metric_results", "numeric_assessments", "execution", "diagnostics", "tested_families",
    "plots", "exposure", "access_control_status", "operator_interventions", "omitted_evidence", "limitations"}
_V2_SCOPE = "governed_output_source_reference_agreement_not_scientific_or_clinical_qualification"


def _assessment_protocol(record, *, workspace, artifact_service):
    from .rna_publication_profiles import assessment_rules_digest, load_assessment_intent, load_assessment_plan
    _keys(record, _V2_PROTOCOL_KEYS)
    if record["format"] != FORMAT_V2 or record["scope"] != _V2_SCOPE or workspace is None or artifact_service is None:
        raise ValueError("Version 2 comparison requires its exact trusted workspace and artifact service")
    plan = load_assessment_plan(record["assessment_plan_ref"], workspace=workspace, service=artifact_service)
    intent = load_assessment_intent(plan["intent_ref"], workspace=workspace, service=artifact_service)
    if (record["assessment_intent_ref"] != plan["intent_ref"]
            or record["assessment_rules_sha256"] != assessment_rules_digest(intent)
            or record["reference_result_set_ref"] != intent["reference_bundle_ref"]):
        raise ValueError("Frozen version 2 comparison intent, plan or reference identity differs")
    return deepcopy(record)


def prepare_assessment_comparison(output, assessment_plan_ref, *, workspace, artifact_service):
    """Freeze only plan/reference identities; no actual result is added to the plan."""
    from .rna_publication_profiles import assessment_rules_digest, load_assessment_intent, load_assessment_plan
    plan = load_assessment_plan(assessment_plan_ref, workspace=workspace, service=artifact_service)
    intent = load_assessment_intent(plan["intent_ref"], workspace=workspace, service=artifact_service)
    record = {"format": FORMAT_V2, "assessment_plan_ref": deepcopy(assessment_plan_ref), "assessment_intent_ref": plan["intent_ref"],
        "assessment_rules_sha256": assessment_rules_digest(intent), "reference_result_set_ref": intent["reference_bundle_ref"], "scope": _V2_SCOPE}
    _assessment_protocol(record, workspace=workspace, artifact_service=artifact_service)
    output = _absolute(output)
    if not output.is_relative_to(_absolute(workspace)):
        raise ValueError("Assessment comparison output lies outside the assigned workspace")
    protected = [ref.get("path", ref.get("manifest_path")) for ref in
        (assessment_plan_ref, plan["intent_ref"], intent["reference_bundle_ref"], plan["workflow_ref"], plan["execution_inputs_ref"])]
    require_disjoint_output(output, protected)
    receipt = _seal(output, "publication-comparison.json", record, "analysis.publication_comparison_v2", scope=_V2_SCOPE)
    return {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"]}


def compare_metric_to_policy(metric, policy):
    """Apply the explicit pre-result operator; no metric-direction default exists."""
    if metric["status"] != "computed":
        return "not_assessable", list(metric["reason_codes"])
    if policy["kind"] == "report_only":
        return "report_only", ["frozen_report_only_policy"]
    if type(metric["value"]) not in (int, float) or policy["operator"] not in {"less_equal", "greater_equal"}:
        return "not_assessable", ["unsupported_acceptance_value_or_operator"]
    agrees = metric["value"] <= policy["bound"] if policy["operator"] == "less_equal" else metric["value"] >= policy["bound"]
    return "agreement" if agrees else "disagreement", ["frozen_agreement_bound_applied"]


def _source_record(path, value):
    from .checkpoints import atomic_json
    from devbench.study.evidence import _read
    atomic_json(path, value)
    body = _read(path, 1_000_000)
    path.chmod(0o444)
    return {"path": str(path), "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body)}


def _reference_gene_table(reference, claim_ids, *, workspace, artifact_service):
    """Build source-verified effect rows without inventing absent probabilities."""
    from .rna_publication_profiles import read_reference_json
    from .rna_publication_metrics import validate_gene_table
    findings = {item["claim_id"]: item for item in reference["findings"]}
    if not claim_ids or len(set(claim_ids)) != len(claim_ids) or set(claim_ids) - set(findings):
        return None, ["required_reference_claim_unavailable"]
    rows, mappings, alignment, family, namespace, annotation = [], [], None, None, None, None
    for claim in claim_ids:
        finding = findings[claim]
        if finding["value_or_censoring"]["kind"] != "exact":
            return None, ["reference_value_censored_or_unknown"]
        if len(finding["entity_refs"]) != 1 or finding["tested_family_ref"] is None:
            return None, ["reference_entity_or_tested_family_unavailable"]
        entity = read_reference_json(finding["entity_refs"][0], workspace=workspace, service=artifact_service)
        if type(entity) is not dict or set(entity) != {"format", "feature_id", "alignment", "mapping_namespace", "annotation_ref"} or entity["format"] != "devbench.rna-reference-gene/2":
            return None, ["unsupported_reference_entity_adapter"]
        current_family = read_reference_json(finding["tested_family_ref"], workspace=workspace, service=artifact_service)
        if alignment is not None and (alignment != entity["alignment"] or namespace != entity["mapping_namespace"] or annotation != entity["annotation_ref"] or family != current_family):
            return None, ["reference_claim_alignment_or_family_differs"]
        alignment, namespace, annotation, family = entity["alignment"], entity["mapping_namespace"], entity["annotation_ref"], current_family
        review = read_reference_json(finding["context_review_ref"], workspace=workspace, service=artifact_service)
        if review["conclusion"] != "supported":
            return None, ["reference_context_not_supported"]
        uncertainty = (read_reference_json(finding["uncertainty_ref"], workspace=workspace, service=artifact_service)
                       if finding["uncertainty_ref"] is not None else None)
        identity = entity["feature_id"]
        rows.append({"feature_id": identity, "tested": True, "status": "ok", "effect": finding["value_or_censoring"]["value"],
            "p_value": None, "q_value": None, "uncertainty": uncertainty})
        mappings.append({"source_id": identity, "target_id": identity, "status": "mapped"})
    if any(findings[claim]["reporting_completeness"] != "complete" for claim in claim_ids):
        family = {**family, "complete": False}
    table = {"format": "devbench.rna-gene-table/2", "table_id": "reference-" + digest([reference["reference_set_id"], claim_ids])[:32],
        "alignment": alignment, "family": family, "mapping": {"namespace": namespace, "annotation_ref": annotation, "records": mappings},
        "rows": rows, "limitations": "Source-reviewed effect claims; absent p/q remain null; selected source rows do not establish exhaustive truth."}
    try:
        return validate_gene_table(table), []
    except ValueError:
        return None, ["reference_table_semantics_not_assessable"]


def _gene_comparison_parameters(value):
    """Recognize the same closed retained adapter for calculation and review."""
    from .rna_publication_metrics import _ids
    if (type(value) is not dict or set(value) != {"format", "actual_table_id", "reference_claim_ids", "selection_rule"}
            or value["format"] != "devbench.rna-gene-comparison-parameters/2"
            or type(value["reference_claim_ids"]) is not list or len(value["reference_claim_ids"]) > 128):
        return None
    try:
        _ids([value["actual_table_id"]], allow_empty=False)
        _ids(value["reference_claim_ids"])
        rule = value["selection_rule"]
        if rule is not None:
            _keys(rule, {"field", "operator", "threshold", "probability_semantics", "family_id"})
            _ids([rule["probability_semantics"]], allow_empty=False)
            _ids([rule["family_id"]], allow_empty=False)
            if (rule["field"] not in {"p_value", "q_value"} or rule["operator"] != "less_equal"
                    or type(rule["threshold"]) not in (int, float) or not 0 <= rule["threshold"] <= 1):
                return None
    except (ValueError, TypeError):
        return None
    return value


def _frozen_estimand_alignment(estimand, table):
    if table is None:
        return []
    expected = {"estimand_id": estimand["estimand_id"], "biological_unit": estimand["biological_unit"]["independent_unit"],
        "contrast": estimand["relationship"]["definition"], "cohort": estimand["population"],
        "effect_scale": estimand["measurement"]["units"]}
    return ["frozen_estimand_alignment:" + key for key, value in expected.items()
        if value is None or table["alignment"][key] != value]


def _frozen_gene_universe(intent, spec, reference, reference_table, actual, *, workspace, artifact_service):
    """One explicit reviewed full-family adapter; no post-result intersection rule."""
    from .rna_publication_profiles import read_reference_json
    from .rna_publication_metrics import _ids
    fields = {"format", "rule_id", "eligible_ids", "excluded_ids", "mapping_namespace", "annotation_ref", "mapping_records", "review_record_ref"}
    policy = read_reference_json(intent["universe_policy_ref"], workspace=workspace, service=artifact_service)
    if (type(policy) is not dict or set(policy) != fields or policy["format"] != "devbench.rna-gene-universe-policy/2"
            or policy["rule_id"] != "complete_eligible_gene_family"):
        return ["unsupported_universe_policy_adapter"], None
    reasons = []
    if policy["rule_id"] != spec["universe_rule_id"]:
        reasons.append("frozen_universe_rule_id_differs")
    try:
        if type(policy["eligible_ids"]) is not list or type(policy["excluded_ids"]) is not list:
            raise ValueError("Frozen universe IDs must be explicit arrays")
        eligible, excluded = set(_ids(policy["eligible_ids"], allow_empty=False)), set(_ids(policy["excluded_ids"]))
        records = policy["mapping_records"]
        if (eligible & excluded or type(records) is not list or len(records) != len(eligible)
                or type(policy["mapping_namespace"]) is not str or not policy["mapping_namespace"].strip()
                or len(policy["mapping_namespace"]) > 200
                or any(type(row) is not dict or set(row) != {"source_id", "target_id", "status"}
                       or row["status"] != "mapped" for row in records)
                or set(_ids([row["source_id"] for row in records], allow_empty=False)) != eligible):
            raise ValueError("Frozen universe mapping is unsupported")
        _ids([row["target_id"] for row in records], allow_empty=False)
    except ValueError:
        return [*reasons, "unsupported_universe_policy_adapter"], None
    if policy["review_record_ref"] is None:
        reasons.append("frozen_universe_review_unavailable")
    else:
        review = read_reference_json(policy["review_record_ref"], workspace=workspace, service=artifact_service)
        review_fields = (fields - {"review_record_ref"}) | {"reviewed_by", "conclusion", "limitations"}
        if (type(review) is not dict or set(review) != review_fields or review.get("format") != "devbench.rna-gene-universe-review/2"
                or review.get("conclusion") != "supported" or type(review.get("reviewed_by")) is not str
                or not review["reviewed_by"].strip() or len(review["reviewed_by"]) > 200
                or type(review.get("limitations")) is not str or not review["limitations"].strip()):
            reasons.append("frozen_universe_review_unavailable")
        elif any(review[key] != policy[key] for key in fields - {"format", "review_record_ref"}):
            reasons.append("frozen_universe_review_differs")
    if reference_table is None or actual is None:
        return reasons, policy
    if reference["universe_ref"] is None:
        reasons.append("reference_universe_unavailable")
    elif read_reference_json(reference["universe_ref"], workspace=workspace, service=artifact_service) != reference_table["family"]:
        reasons.append("reference_universe_differs_from_tested_family")
    expected_mapping = {row["source_id"]: row for row in records}
    for table in (reference_table, actual):
        if not table["family"]["complete"]:
            reasons.append("incomplete_tested_family")
        if set(table["family"]["eligible_ids"]) != eligible:
            reasons.append("frozen_universe_eligible_family_differs")
        effect_rows = {row["feature_id"]: row for row in table["rows"] if row["feature_id"] in eligible}
        if (set(effect_rows) != eligible or any(row["status"] != "ok" or row["effect"] is None
                for row in effect_rows.values())):
            reasons.append("frozen_universe_missing_eligible_estimate")
        observed_mapping = {row["source_id"]: row for row in table["mapping"]["records"] if row["source_id"] in eligible}
        if (table["mapping"]["namespace"] != policy["mapping_namespace"] or table["mapping"]["annotation_ref"] != policy["annotation_ref"]
                or observed_mapping != expected_mapping):
            reasons.append("frozen_universe_mapping_differs")
        extra = {row["feature_id"] for row in table["rows"]} - eligible
        if not extra <= excluded or (table is actual and extra != excluded):
            reasons.append("frozen_universe_excluded_membership_differs")
        if any(row["feature_id"] in excluded and (row["tested"] or row["status"] not in {"filtered", "missing", "not_estimable"}
                or any(row[key] is not None for key in ("effect", "p_value", "q_value", "uncertainty"))) for row in table["rows"]):
            reasons.append("frozen_universe_excluded_membership_differs")
    return sorted(set(reasons)), policy


def _v2_compute(comparison_ref, protocol, bundle_ref, bundle, *, workspace, artifact_service, metric_evidence=None, evidence_output=None):
    from .rna_publication_profiles import (
        load_assessment_intent, load_assessment_plan, load_reference_grounding_binding, load_reference_result_set, read_reference_json, validate_metric_result,
    )
    from .rna_publication_metrics import compare_gene_effects, gene_set_agreement, select_gene_ids, validate_gene_comparability
    from .rna_comparison_outputs import load_comparison_gene_table
    plan = load_assessment_plan(protocol["assessment_plan_ref"], workspace=workspace, service=artifact_service)
    intent = load_assessment_intent(plan["intent_ref"], workspace=workspace, service=artifact_service)
    grounding = load_reference_grounding_binding(intent["reference_bundle_ref"], workspace=workspace, service=artifact_service, allow_evidence_read=True)
    reference = load_reference_result_set(protocol["reference_result_set_ref"], workspace=workspace, service=artifact_service,
        expected_publication_ref=grounding["publication_ref"], allow_evidence_read=True)
    if (read_reference_json(reference["estimand_ref"], workspace=workspace, service=artifact_service) != intent["estimand"]
            or reference["exposure_ref"] != intent["exposure_ref"]):
        raise ValueError("Reference result set estimand or exposure differs from the frozen intent")
    resolved = read_reference_json(plan["resolved_capabilities_ref"], workspace=workspace, service=artifact_service)
    applicability = {item["profile_id"]: item for item in resolved["profiles"]}
    metric_results, numeric_assessments = [], []
    seen = set()
    for profile in intent["profile_specs"]:
        for spec in profile["metric_specs"]:
            key = spec["metric_id"]
            if key in seen:
                raise ValueError("Composed assessment must use distinct explicit metric identities")
            seen.add(key)
            parameters = _gene_comparison_parameters(read_reference_json(spec["parameter_record_ref"], workspace=workspace, service=artifact_service))
            reasons, calculated = [], None
            frozen_profile = applicability[profile["profile_id"]]
            if frozen_profile["applicability"] != "applicable":
                reasons.extend(["frozen_profile_applicability:" + frozen_profile["applicability"], *frozen_profile["reason_codes"]])
            if profile["profile_id"] != "mean_expression":
                reasons.append("unsupported_profile_comparator")
            if parameters is None:
                reasons.append("unsupported_metric_parameter_adapter")
                parameters = {"actual_table_id": None, "reference_claim_ids": [], "selection_rule": None}
            if set(intent["required_claim_ids"]) - set(parameters["reference_claim_ids"]):
                reasons.append("required_reference_claim_omitted")
            policy = read_reference_json(spec["comparability_policy_ref"], workspace=workspace, service=artifact_service)
            if policy != {"permitted_differences": []}:
                reasons.append("unsupported_comparability_transform")
            if spec["uncertainty_policy"]["kind"] != "report_only":
                reasons.append("required_uncertainty_adapter_unavailable")
            selected = [table for table in bundle["gene_tables"] if table["table_id"] == parameters["actual_table_id"]]
            if len(selected) != 1:
                reasons.append("actual_table_unavailable" if not selected else "actual_table_identity_ambiguous")
            elif not selected[0]["frozen_comparison_eligible"]:
                reasons.append("retrospective_run_not_frozen_comparison_eligible")
            reference_table, reference_reasons = _reference_gene_table(reference, parameters["reference_claim_ids"], workspace=workspace, artifact_service=artifact_service)
            reasons.extend(reference_reasons)
            actual = load_comparison_gene_table(selected[0]["table_ref"], artifact_service=artifact_service) if len(selected) == 1 else None
            reasons.extend(_frozen_estimand_alignment(intent["estimand"], reference_table))
            reasons.extend(_frozen_estimand_alignment(intent["estimand"], actual))
            universe_reasons, universe = _frozen_gene_universe(intent, spec, reference, reference_table, actual,
                workspace=workspace, artifact_service=artifact_service)
            reasons.extend(universe_reasons)
            if not reasons:
                if key in {"gene_effect_absolute_difference", "gene_effect_direction_agreement", "gene_effect_rmse", "gene_table_coverage"}:
                    calculated = compare_gene_effects(reference_table, actual, metric_id=key)
                elif key in {"gene_set_precision", "gene_set_recall", "gene_set_f1", "gene_set_jaccard"}:
                    rule = parameters["selection_rule"]
                    comparability = validate_gene_comparability(reference_table, actual, comparison_kind="selected_gene_set")
                    if comparability["status"] != "comparable":
                        reasons.extend(comparability["reason_codes"])
                    elif rule is None:
                        reasons.append("frozen_selection_rule_unavailable")
                    else:
                        try:
                            left = select_gene_ids(reference_table, rule, frozen_rule=parameters["selection_rule"])
                            right = select_gene_ids(actual, rule, frozen_rule=parameters["selection_rule"])
                            agreement = gene_set_agreement(left, right, reference_table["family"]["eligible_ids"], reference_complete=reference_table["family"]["complete"])
                            calculated = {"status": agreement["status"], "value": agreement[key.removeprefix("gene_set_")],
                                "units": "fraction", "denominators": agreement["denominators"], "reason_codes": agreement["reason_codes"]}
                        except ValueError:
                            reasons.append("selected_set_evidence_incomplete_or_incompatible")
                else:
                    reasons.append("unsupported_metric_adapter")
            calculated = calculated or {"status": "not_assessable", "value": None, "units": spec["units"],
                "denominators": {"assessed_rows": 0}, "reason_codes": sorted(set(reasons))}
            if calculated["units"] != spec["units"]:
                calculated.update(status="not_assessable", value=None, reason_codes=["metric_units_differ_from_frozen_spec"])
            universe_ref = intent["universe_policy_ref"]
            exclusions = {"format": "devbench.rna-metric-exclusions/2", "metric_id": key, "denominators": calculated["denominators"],
                "reason_codes": calculated["reason_codes"], "universe_rule_id": spec["universe_rule_id"], "universe_policy_ref": universe_ref,
                "eligible_ids": universe["eligible_ids"] if universe is not None else None,
                "excluded_ids": universe["excluded_ids"] if universe is not None else None,
                "scope": "frozen_full_family_no_missing_estimate_replacement"}
            if evidence_output is not None:
                exclusion_ref = _source_record(evidence_output / (key + "-exclusions.json"), exclusions)
            else:
                exclusion_ref = metric_evidence[key]
                if read_reference_json(exclusion_ref, workspace=workspace, service=artifact_service) != exclusions:
                    raise ValueError("Retained metric exclusions differ from actual comparison evidence")
            evidence_refs = []
            for ref in [deepcopy(bundle_ref), protocol["reference_result_set_ref"], spec["parameter_record_ref"],
                    spec["comparability_policy_ref"], plan["resolved_capabilities_ref"], universe_ref,
                    *([reference["universe_ref"]] if reference["universe_ref"] is not None else []),
                    *([universe["review_record_ref"]] if universe is not None and universe["review_record_ref"] is not None else [])]:
                if ref not in evidence_refs:
                    evidence_refs.append(ref)
            metric = {"metric_id": key, "profile_id": profile["profile_id"], "estimand_id": intent["estimand"]["estimand_id"],
                **{field: calculated[field] for field in ("status", "value", "units", "denominators", "reason_codes")},
                "universe_ref": universe_ref, "exclusions_ref": exclusion_ref, "uncertainty_ref": None,
                "evidence_refs": evidence_refs}
            metric = validate_metric_result(metric, workspace=workspace, service=artifact_service)
            metric_results.append(metric)
            decision, decision_reasons = compare_metric_to_policy(metric, spec["acceptance_policy"])
            numeric_assessments.append({"metric_id": key, "decision": decision, "reason_codes": decision_reasons, "evidence_ids": ["metric-" + key]})
    diagnostics = [{"diagnostic_id": "diagnostic-" + str(index),
        "status": "valid" if item["status"] == "computed" else "unknown",
        "reason_codes": (["retained_run_failed", *item["reason_codes"]] if item["status"] == "failed" else item["reason_codes"]),
        "evidence_ids": ["diagnostic-" + str(index)]} for index, item in enumerate(bundle["diagnostics"])]
    diagnostics.append({"diagnostic_id": "scientific-design", "status": "unknown",
        "reason_codes": ["independent_scientific_design_review_unavailable"], "evidence_ids": ["scientific-design"]})
    families = []
    for index, table in enumerate(bundle["gene_tables"]):
        actual = load_comparison_gene_table(table["table_ref"], artifact_service=artifact_service)
        families.append({"family_id": actual["family"]["family_id"], "complete": actual["family"]["complete"], "evidence_ids": ["family-" + str(index)]})
    required_plots = {kind for profile in intent["profile_specs"] for kind in profile["required_plot_kinds"]}
    failed = any(run["status"] != "succeeded" for run in bundle["runs"])
    execution = {"status": "failed" if failed else "verified", "reason_codes": ["retained_governed_receipts_revalidated"], "evidence_ids": ["execution"]}
    omitted = ([{"kind": "reviewed_plot", "reason": "required_renderer_unavailable", "count": len(required_plots)}] if required_plots else [])
    return {"format": ASSESSMENT_FORMAT_V2, "comparison_ref": deepcopy(comparison_ref), "assessment_plan_ref": protocol["assessment_plan_ref"],
        "output_bundle_ref": deepcopy(bundle_ref), "reference_result_set_ref": protocol["reference_result_set_ref"],
        "assessment_rules_sha256": protocol["assessment_rules_sha256"], "metric_results": metric_results, "numeric_assessments": numeric_assessments,
        "execution": execution, "diagnostics": diagnostics, "tested_families": families, "plots": [],
        "exposure": read_reference_json(intent["exposure_ref"], workspace=workspace, service=artifact_service), "access_control_status": "not_established",
        "operator_interventions": deepcopy(bundle["operator_interventions"]), "omitted_evidence": omitted,
        "limitations": ["profile_execution_qualification_not_established", "independent_review_unavailable",
            "source_context_review_is_declared_not_authenticated_scientific_validation", "reference_access_order_not_os_isolation_proof",
            *( ["required_renderer_unavailable"] if required_plots else [])]}


def validate_comparison_assessment(record, *, workspace, artifact_service, allow_evidence_read=False):
    if allow_evidence_read is not True:
        raise PermissionError("Version 2 assessment validation requires evaluator evidence-read authority")
    _keys(record, _V2_ASSESSMENT_KEYS)
    if record["format"] != ASSESSMENT_FORMAT_V2:
        raise ValueError("Unsupported typed comparison assessment")
    from .rna_comparison_outputs import load_comparison_outputs
    bundle = load_comparison_outputs(record["output_bundle_ref"], artifact_service=artifact_service)
    comparison = load_publication_comparison(record["comparison_ref"]["manifest_path"], record["comparison_ref"]["root_hash"],
        allow_evidence_read=True, workspace=workspace, artifact_service=artifact_service)
    if bundle["assessment_plan_ref"] != comparison["assessment_plan_ref"]:
        raise ValueError("Actual output bundle is bound to a different frozen assessment plan")
    expected = _v2_compute(record["comparison_ref"], comparison, record["output_bundle_ref"], bundle,
        workspace=workspace, artifact_service=artifact_service, metric_evidence={metric["metric_id"]: metric["exclusions_ref"] for metric in record["metric_results"]})
    if expected != record:
        raise ValueError("Typed comparison differs from independently recomputed actual metrics, decisions or diagnostics")
    return deepcopy(record)


def load_comparison_assessment(manifest, expected_root, *, workspace, artifact_service, allow_evidence_read=False):
    _, record, _ = _load(_absolute(manifest), _sha(expected_root), "comparison-assessment.json")
    return validate_comparison_assessment(record, workspace=workspace, artifact_service=artifact_service, allow_evidence_read=allow_evidence_read)


def _assessment_method_context(plan, intent, *, workspace, artifact_service):
    """Project verified frozen method/design context without files or execution code."""
    import json
    from devharness_core.provenance import canonical_json
    from .rna_publication_profiles import read_reference_json
    from .rna_execution_inputs import load_execution_inputs
    from .rna_scientific_trial_contract import bounded_source
    from .episodes import _public_only
    omitted = []
    def omission(reason, count=1):
        omitted.append({"kind": "method_context", "reason": reason, "count": count})
    def reviewable(value, depth=0):
        if depth > 4:
            return False
        if type(value) in (bool, int, float) or value is None:
            return True
        if type(value) is str:
            return len(value) <= 2000 and "/" not in value and "\\" not in value and "\0" not in value
        if type(value) is list:
            return len(value) <= 64 and all(reviewable(child, depth + 1) for child in value)
        if type(value) is dict:
            return len(value) <= 64 and all(type(key) is str and len(key) <= 200 and not sensitive(key)
                and reviewable(child, depth + 1) for key, child in value.items())
        return False
    def sensitive(name):
        return re.search(r"(?:^|_)(?:path|file|code|script|command|secret|token|credential)(?:_|$)", name) is not None
    def public_parameter(name, value):
        try:
            _public_only({"parameters": {name: value}})
        except ValueError:
            return False
        return True
    raw_question = bounded_source(intent["question_ref"], workspace).decode("utf-8")
    try:
        question = json.loads(raw_question)
    except ValueError:
        question = None
    question_text = question["text"] if type(question) is dict and set(question) == {"text"} and type(question["text"]) is str else raw_question
    if len(question_text) > 8000:
        question_text = None
        omission("question_context_exceeds_review_bound")
    estimate, requirements = deepcopy(intent["estimand"]), []
    for descriptor in estimate["requirement_sources"]:
        source = read_reference_json(descriptor["record_ref"], workspace=workspace, service=artifact_service)
        for requirement in source["requirements"]:
            if requirement["requirement_id"] not in descriptor["requirement_ids"]:
                continue
            value = deepcopy(requirement["value"])
            if not reviewable(value) or len(canonical_json(value)) > 16_000:
                value = None
                omission("requirement_value_omitted:" + requirement["requirement_id"])
            requirements.append({**{key: requirement[key] for key in ("requirement_id", "origin", "name", "evidence")},
                "value": value, "record_sha256": descriptor["record_ref"]["sha256"]})
    estimate["requirement_sources"] = [{"origin": item["origin"], "requirement_ids": item["requirement_ids"],
        "record_sha256": item["record_ref"]["sha256"]} for item in estimate["requirement_sources"]]
    workflow = read_reference_json(plan["workflow_ref"], workspace=workspace, service=artifact_service)["governed_workflow"]
    if "steps" in workflow:
        steps = workflow["steps"]
    else:
        identifiers = {identity for experiment in workflow["experiments"] for identity in experiment["step_ids"].values()}
        steps = [artifact_service.get("step", identity) for identity in sorted(identifiers)]
    methods = []
    for step in steps:
        contract = artifact_service.method(step["method_version_id"])["contract"]
        allowed = set(contract["parameters"].get("properties", {}))
        parameters, missing = {}, []
        for name, value in step["parameters"].items():
            if (name not in allowed or sensitive(name) or not reviewable(value) or len(canonical_json(value)) > 16_000
                    or not public_parameter(name, value)):
                missing.append(name)
            else:
                parameters[name] = deepcopy(value)
        if missing:
            omission("method_parameters_omitted:" + step["step_id"], len(missing))
        methods.append({"method_version_id": step["method_version_id"], "method_id": contract["method_id"],
            "name": contract["name"], "step_id": step["step_id"], "parameters": parameters, "omitted_parameters": sorted(missing)})
    bridge = load_execution_inputs(plan["execution_inputs_ref"], artifact_service=artifact_service)
    receipt = read_reference_json(bridge["transformation_receipt_ref"], workspace=workspace, service=artifact_service)
    declarations = receipt["input_binding"]["declarations"]
    metadata_roles = declarations.get("metadata_roles")
    if metadata_roles is None:
        omission("actual_input_metadata_roles_unavailable")
        metadata_roles = {}
    context = {"format": "devbench.rna-assessment-method-context/2", "status": "not_assessed" if omitted else "available",
        "question": {"text": question_text, "source_sha256": intent["question_ref"]["sha256"]}, "mode": intent["mode"],
        "estimand": estimate, "requirements": requirements, "methods": methods,
        "input_design": {"representation": declarations["representation"], "metadata_roles": metadata_roles,
            "design": receipt["method_ports"]["design"], "audit": receipt["design_audit"]},
        "omitted_evidence": omitted, "scope": "retained_method_context_not_independent_scientific_validation"}
    if len(canonical_json(context)) > 128_000:
        omission("method_context_projection_exceeds_byte_bound")
        context.update(status="not_assessed", question={**context["question"], "text": None}, estimand=None,
            requirements=[], methods=[], input_design=None)
    return context


def assessment_review_evidence(manifest, expected_root, *, workspace, artifact_service, allow_evidence_read=False):
    """Supply actual values and exact source context to the existing review path."""
    record = load_comparison_assessment(manifest, expected_root, workspace=workspace, artifact_service=artifact_service,
        allow_evidence_read=allow_evidence_read)
    from .rna_publication_profiles import load_assessment_plan, load_assessment_intent, load_reference_result_set, read_reference_json
    from .rna_comparison_outputs import load_comparison_outputs, load_comparison_gene_table
    plan = load_assessment_plan(record["assessment_plan_ref"], workspace=workspace, service=artifact_service)
    intent = load_assessment_intent(plan["intent_ref"], workspace=workspace, service=artifact_service)
    bundle = load_comparison_outputs(record["output_bundle_ref"], artifact_service=artifact_service)
    reference = load_reference_result_set(record["reference_result_set_ref"], workspace=workspace, service=artifact_service, allow_evidence_read=True)
    tables, contexts, omitted = [], [], []
    seen_tables = set()
    for profile in intent["profile_specs"]:
        for metric in profile["metric_specs"]:
            parameters = _gene_comparison_parameters(read_reference_json(metric["parameter_record_ref"], workspace=workspace, service=artifact_service))
            if parameters is None:
                omitted.append({"kind": "numeric_assessment", "reason": "unsupported_metric_parameter_adapter", "count": 1})
                continue
            actual = [table for table in bundle["gene_tables"] if table["table_id"] == parameters["actual_table_id"]]
            if len(actual) == 1:
                key = ("actual", actual[0]["run_id"], actual[0]["port"])
                if key not in seen_tables:
                    tables.append({"role": "actual", "metric_ids": [metric["metric_id"]],
                        "table": load_comparison_gene_table(actual[0]["table_ref"], artifact_service=artifact_service),
                        "source_evidence_refs": [record["output_bundle_ref"], actual[0]["table_ref"]]})
                    seen_tables.add(key)
                else:
                    next(table for table in tables if table["role"] == "actual" and table["table"]["table_id"] == actual[0]["table_id"])["metric_ids"].append(metric["metric_id"])
            else:
                omitted.append({"kind": "numeric_assessment", "reason": "actual_table_unavailable_or_ambiguous", "count": 1})
            target, reasons = _reference_gene_table(reference, parameters["reference_claim_ids"], workspace=workspace, artifact_service=artifact_service)
            if target is None:
                omitted.append({"kind": "source_claim", "reason": ";".join(reasons), "count": 1})
            else:
                tables.append({"role": "reference", "metric_ids": [metric["metric_id"]], "table": target,
                    "source_evidence_refs": [record["reference_result_set_ref"]]})
    for finding in reference["findings"]:
        review = read_reference_json(finding["context_review_ref"], workspace=workspace, service=artifact_service)
        for locator_ref in finding["source_locator_refs"]:
            locator = read_reference_json(locator_ref, workspace=workspace, service=artifact_service)
            from .rna_scientific_trial_contract import bounded_source
            from .rna_publication_evidence import _locator
            from .rna_reference_extraction import validate_context_review
            try:
                body = bounded_source(locator["source_ref"], workspace)
                _locator(locator["excerpt"]["locator"], body)
                if locator["excerpt"]["excerpt_id"] != review["excerpt_id"]:
                    raise ValueError("unmatched_context_review_locator")
                validate_context_review(review, excerpt=locator["excerpt"], body=body, participant_id=review["proposal_author_id"])
            except ValueError:
                omitted.append({"kind": "source_claim", "reason": "unmatched_or_invalid_source_context_locator", "count": 1})
                continue
            contexts.append({"claim_id": finding["claim_id"], "claim_text": review["claim"],
                "context_review": deepcopy(review), "excerpt": deepcopy(locator["excerpt"])})
    return {"gene_tables": tables, "source_contexts": contexts,
        "method_context": _assessment_method_context(plan, intent, workspace=workspace, artifact_service=artifact_service),
        "omitted_evidence": omitted}


def compare_assessment_outputs(manifest, expected_root, *, output_bundle_ref, workspace, artifact_service,
                               output, allow_evidence_read=False):
    """Validate actual receipts before loading outcome references, then seal a report."""
    if allow_evidence_read is not True:
        raise PermissionError("Version 2 comparison requires evaluator evidence-read authority")
    from .rna_comparison_outputs import load_comparison_outputs
    # This is deliberately first: prose/self-reports never release references.
    bundle = load_comparison_outputs(output_bundle_ref, artifact_service=artifact_service)
    comparison_ref = {"manifest_path": str(_absolute(manifest)), "root_hash": _sha(expected_root)}
    comparison = load_publication_comparison(manifest, expected_root, allow_evidence_read=True, workspace=workspace, artifact_service=artifact_service)
    if comparison["format"] != FORMAT_V2 or bundle["assessment_plan_ref"] != comparison["assessment_plan_ref"]:
        raise ValueError("Version 2 outputs require their exact finalized comparison plan")
    output = _absolute(output)
    if not output.is_relative_to(_absolute(workspace)):
        raise ValueError("Assessment output lies outside the assigned workspace")
    require_disjoint_output(output, [manifest, output_bundle_ref.get("path", output_bundle_ref.get("manifest_path"))])
    evidence_output = output.with_name(output.name + "-metric-evidence")
    evidence_output.mkdir(mode=0o700, parents=True, exist_ok=False)
    record = _v2_compute(comparison_ref, comparison, output_bundle_ref, bundle, workspace=workspace, artifact_service=artifact_service, evidence_output=evidence_output)
    validate_comparison_assessment(record, workspace=workspace, artifact_service=artifact_service, allow_evidence_read=True)
    receipt = _seal(output, "comparison-assessment.json", record, "analysis.publication_assessment_v2", scope=_V2_SCOPE)
    result = {"manifest_path": str(output / "audit/manifest.json"), "root_hash": receipt["root_hash"], "record": record, "report": None}
    from .rna_publication_report import build_assessment_report, render_assessment_report
    subject_ref = {key: result[key] for key in ("manifest_path", "root_hash")}
    registry = [{"evidence_id": "execution", "kind": "execution_receipt", "artifact_ref": output_bundle_ref, "summary": "Revalidated governed receipts"}]
    registry.extend({"evidence_id": row["evidence_ids"][0], "kind": "diagnostic", "artifact_ref": output_bundle_ref, "summary": "Deterministic diagnostic or disclosed missing review"} for row in record["diagnostics"])
    registry.extend({"evidence_id": row["evidence_ids"][0], "kind": "tested_family", "artifact_ref": subject_ref, "summary": "Actual retained tested-family completeness"} for row in record["tested_families"])
    registry.extend({"evidence_id": row["evidence_ids"][0], "kind": "numeric_assessment", "artifact_ref": subject_ref, "summary": "Recomputed metric and explicit frozen acceptance policy"} for row in record["numeric_assessments"])
    evidence = {"artifact_registry": registry, **{key: record[key] for key in ("execution", "diagnostics", "tested_families", "metric_results",
        "numeric_assessments", "plots", "exposure", "access_control_status", "omitted_evidence")}, "claims": [],
        "required_evidence": ["execution_receipt", "diagnostic", "tested_family", "numeric_assessment", "reviewed_plot"]}
    subject = {"kind": "comparison", "subject_ref": subject_ref, "plan_ref": record["assessment_plan_ref"],
        "output_bundle_ref": output_bundle_ref, "comparison_ref": comparison_ref}
    report = build_assessment_report(subject, evidence, workspace=workspace, artifact_service=artifact_service)
    result["report"] = render_assessment_report(report, output.with_name(output.name + "-report"),
        allow_evidence_read=True, workspace=workspace, artifact_service=artifact_service)
    return result
