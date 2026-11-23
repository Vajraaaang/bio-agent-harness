"""Separate, bounded assessment dimensions over retained comparison evidence.

Caller declarations and checksums alone cannot promote a positive dimension.
Positive coordinator states require the existing governed artifact service to
revalidate a typed comparison receipt. Recorded states are research evidence,
not independent scientific qualification or an execution grant.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .checkpoints import atomic_json, digest
from .episode_proposals import _absolute, _bind_manifest_snapshot, _retained, require_disjoint_output
from .rna_publication_evidence import _copy, _identifier, _keys, _list, _sha, _text, validate_exposure
from devbench.study.evidence import _read, decode_json
from devharness_core.provenance import ExecutionDAG, verify_execution

FORMAT = "devbench.rna-assessment-report/2"
SCOPE = "recorded_separate_assessments_not_scientific_or_clinical_qualification"
EVIDENCE_KINDS = {"execution_receipt", "diagnostic", "tested_family", "numeric_assessment",
                  "reviewed_plot", "source_claim", "input_lineage", "operator_intervention", "qualified_review"}
DIMENSION_STATES = {"execution": {"verified", "failed", "not_assessed"},
    "comparability": {"comparable", "incomparable", "not_assessed"},
    "methodology": {"adequate", "invalid", "not_assessed"},
    "publication_agreement": {"agreement", "disagreement", "not_assessed"},
    "interpretation": {"supported", "unsupported", "not_assessed"},
    "qualified_review": {"not_established"}}
_AUTHORITY = {"clinical_release_allowed": False, "data_execution_granted": False,
              "model_execution_granted": False, "scientific_validity_established": False}


def _reference_shape(ref):
    if type(ref) is not dict:
        raise ValueError("Assessment evidence requires a closed artifact reference")
    if set(ref) in ({"manifest_path", "root_hash"}, {"manifest_path", "root_hash", "record_ref"}):
        _absolute(ref["manifest_path"])
        _sha(ref["root_hash"])
        if "record_ref" in ref:
            _keys(ref["record_ref"], {"path", "sha256", "size_bytes"})
            _reference_shape(ref["record_ref"])
    elif set(ref) == {"path", "sha256", "size_bytes"}:
        _absolute(ref["path"]); _sha(ref["sha256"])
        if type(ref["size_bytes"]) is not int or not 0 <= ref["size_bytes"] <= 16_000_000:
            raise ValueError("Assessment artifact exceeds its bound")
    else:
        raise ValueError("Assessment evidence requires an existing closed reference type")
    return ref


def _source_bytes(ref):
    """Return the exact bounded SourceRef bytes whose size/hash were checked."""
    _reference_shape(ref)
    _keys(ref, {"path", "sha256", "size_bytes"})
    data = _read(_absolute(ref["path"]), 16_000_000)
    if len(data) != ref["size_bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("Assessment evidence hash or size mismatch")
    return data


def _reference(ref):
    """Validate the two existing reference types without confusing their hashes."""
    _reference_shape(ref)
    if "manifest_path" in ref:
        path = _absolute(ref["manifest_path"])
        manifest = decode_json(_read(path, 1_000_000))
        _bind_manifest_snapshot(manifest, ref["root_hash"])
        if not verify_execution(path, ref["root_hash"])["valid"]:
            raise ValueError("Assessment retained receipt failed verification")
        if "record_ref" in ref:
            _reference(ref["record_ref"])
            if not any(item.get("kind") == "file" and item.get("sha256") == ref["record_ref"]["sha256"]
                       for item in manifest["artifacts"]):
                raise ValueError("Assessment record reference is absent from the retained receipt")
    elif set(ref) == {"path", "sha256", "size_bytes"}:
        _source_bytes(ref)
    return ref


def _subject(value, *, verify_refs=True):
    value = _copy(value)
    _keys(value, {"kind", "subject_ref", "plan_ref", "output_bundle_ref", "comparison_ref"})
    if value["kind"] not in {"trial", "comparison"}:
        raise ValueError("Assessment subject must be trial or comparison")
    for key in ("subject_ref", "plan_ref", "output_bundle_ref", "comparison_ref"):
        if value[key] is not None:
            if key not in {"output_bundle_ref", "plan_ref"}:
                _keys(value[key], {"manifest_path", "root_hash"})
            if verify_refs:
                _reference(value[key])
            else:
                _reference_shape(value[key])
        elif key == "subject_ref":
            raise ValueError("Assessment requires its retained subject")
    return value


def _texts(value, maximum=64):
    for item in _list(value, maximum): _text(item)
    if len(value) != len(set(value)):
        raise ValueError("Assessment reason and evidence identifiers must be distinct")


def _evidence(value, *, workspace=None, artifact_service=None, verify_refs=True):
    from .rna_publication_profiles import validate_metric_result, METRIC_RESULT_SCHEMA
    value = _copy(value)
    _keys(value, {"artifact_registry", "execution", "diagnostics", "tested_families", "metric_results",
        "numeric_assessments", "claims", "plots", "exposure", "access_control_status", "omitted_evidence", "required_evidence"})
    registry = {}
    for artifact in _list(value["artifact_registry"], 256):
        _keys(artifact, {"evidence_id", "kind", "artifact_ref", "summary"})
        key = _identifier(artifact["evidence_id"])
        if key in registry or artifact["kind"] not in EVIDENCE_KINDS:
            raise ValueError("Assessment evidence identities or kinds are invalid")
        if verify_refs:
            _reference(artifact["artifact_ref"])
        else:
            _reference_shape(artifact["artifact_ref"])
        _text(artifact["summary"])
        registry[key] = artifact
    def refs(row, *, kind=None):
        _texts(row["evidence_ids"], 128)
        if not set(row["evidence_ids"]) <= registry.keys():
            raise ValueError("Assessment citation does not resolve an actual artifact")
        if kind and not any(registry[key]["kind"] == kind for key in row["evidence_ids"]):
            raise ValueError("Assessment evidence kind does not match its observation")
    execution = value["execution"]
    _keys(execution, {"status", "reason_codes", "evidence_ids"})
    if execution["status"] not in DIMENSION_STATES["execution"]:
        raise ValueError("Unknown execution assessment")
    _texts(execution["reason_codes"]); refs(execution)
    for diagnostic in _list(value["diagnostics"], 128):
        _keys(diagnostic, {"diagnostic_id", "status", "reason_codes", "evidence_ids"})
        _identifier(diagnostic["diagnostic_id"])
        if diagnostic["status"] not in {"valid", "invalid", "unknown"}:
            raise ValueError("Unknown methodological diagnostic status")
        _texts(diagnostic["reason_codes"]); refs(diagnostic, kind="diagnostic")
    for family in _list(value["tested_families"], 128):
        _keys(family, {"family_id", "complete", "evidence_ids"})
        try:
            _identifier(family["family_id"])
        except ValueError:
            # Registered tested families also retain exact SHA-256 fingerprints.
            _sha(family["family_id"])
        if type(family["complete"]) is not bool:
            raise ValueError("Testing family completeness must remain explicit")
        refs(family, kind="tested_family")
    metrics = {}
    for metric in _list(value["metric_results"], 128):
        if verify_refs:
            validated = validate_metric_result(metric, workspace=workspace, service=artifact_service)
        else:
            import jsonschema
            try:
                jsonschema.Draft202012Validator(METRIC_RESULT_SCHEMA).validate(metric)
            except jsonschema.ValidationError as error:
                raise ValueError("Invalid recorded metric result") from error
            if ((metric["status"] == "computed" and metric["value"] is None)
                    or (metric["status"] != "computed" and metric["value"] is not None)):
                raise ValueError("Recorded noncomputed metric cannot carry a result")
            validated = metric
        key = validated["metric_id"]
        if key in metrics:
            raise ValueError("Duplicate metric result")
        metrics[key] = validated
    decisions = set()
    for assessment in _list(value["numeric_assessments"], 128):
        _keys(assessment, {"metric_id", "decision", "reason_codes", "evidence_ids"})
        key = _identifier(assessment["metric_id"])
        if key not in metrics or key in decisions or assessment["decision"] not in {"agreement", "disagreement", "report_only", "not_assessable"}:
            raise ValueError("Numeric assessments must bind distinct actual metric results")
        decisions.add(key)
        if assessment["decision"] in {"agreement", "disagreement"} and metrics[key]["status"] != "computed":
            raise ValueError("Noncomputed metrics cannot supply an agreement verdict")
        _texts(assessment["reason_codes"]); refs(assessment, kind="numeric_assessment")
    claim_ids = set()
    for claim in _list(value["claims"], 128):
        _keys(claim, {"claim_id", "text", "evidence_class", "scope", "status", "evidence_ids"})
        key = _identifier(claim["claim_id"])
        if key in claim_ids or claim["evidence_class"] not in {"source_statement", "numerical_association", "hypothesis"}:
            raise ValueError("Claim identities and evidence classes must be explicit")
        claim_ids.add(key); _text(claim["text"])
        if claim["scope"] not in {"descriptive", "association", "hypothesis", "causal"} or claim["status"] not in {"supported", "unsupported", "not_assessed"}:
            raise ValueError("Unsupported interpretation scope or status")
        if claim["scope"] == "causal" and claim["status"] == "supported":
            raise ValueError("Bulk association evidence cannot establish causality")
        refs(claim)
    figure_ids = set()
    for plot in _list(value["plots"], 64):
        _keys(plot, {"figure_id", "pixels_status", "numeric_status", "reason_codes", "evidence_ids"})
        key = _identifier(plot["figure_id"])
        if key in figure_ids or plot["pixels_status"] not in {"inspected", "not_assessed"} or plot["numeric_status"] not in {"assessed", "not_assessed"}:
            raise ValueError("Plot pixels and numerical geometry must be assessed separately")
        figure_ids.add(key); _texts(plot["reason_codes"]); refs(plot, kind="reviewed_plot")
    validate_exposure(value["exposure"])
    if value["access_control_status"] not in {"enforced", "not_established", "failed"}:
        raise ValueError("Projection alone does not establish access controls")
    for omitted in _list(value["omitted_evidence"], 128):
        _keys(omitted, {"kind", "reason", "count"})
        if omitted["kind"] not in EVIDENCE_KINDS or type(omitted["count"]) is not int or not 1 <= omitted["count"] <= 100000:
            raise ValueError("Omitted evidence requires a bounded explicit count and kind")
        _text(omitted["reason"])
    _texts(value["required_evidence"])
    if not set(value["required_evidence"]) <= EVIDENCE_KINDS:
        raise ValueError("Unknown required evidence kind")
    return value


def _trusted_comparison(subject, evidence, workspace, artifact_service):
    if workspace is None and artifact_service is not None:
        raise ValueError("A trusted artifact service requires its canonical workspace")
    if workspace is None or artifact_service is None or subject["plan_ref"] is None or subject["output_bundle_ref"] is None:
        return None
    from .rna_publication_comparison import load_comparison_assessment
    receipt = load_comparison_assessment(subject["subject_ref"]["manifest_path"], subject["subject_ref"]["root_hash"],
        workspace=_absolute(workspace), artifact_service=artifact_service, allow_evidence_read=True)
    if any(subject[key] != receipt[field] for key, field in (("plan_ref", "assessment_plan_ref"),
           ("output_bundle_ref", "output_bundle_ref"), ("comparison_ref", "comparison_ref"))):
        raise ValueError("Report subject does not bind its trusted comparison receipt")
    for field in ("execution", "metric_results", "numeric_assessments", "diagnostics", "tested_families", "exposure", "access_control_status"):
        if evidence[field] != receipt[field]:
            raise ValueError("Report projection differs from independently revalidated comparison: " + field)
    if any(item not in evidence["omitted_evidence"] for item in receipt["omitted_evidence"]):
        raise ValueError("Report cannot conceal omissions from the retained comparison")
    # A correctly hashed unrelated file is not a diagnostic/result citation.
    allowed = [subject[key] for key in ("subject_ref", "plan_ref", "output_bundle_ref", "comparison_ref") if subject[key]]
    allowed.append(receipt["reference_result_set_ref"])
    for metric in receipt["metric_results"]:
        allowed.extend(metric["evidence_refs"])
        allowed.extend(metric[key] for key in ("universe_ref", "exclusions_ref", "uncertainty_ref") if metric[key] is not None)
    for item in evidence["artifact_registry"]:
        if item["kind"] not in {"reviewed_plot", "source_claim"} and item["artifact_ref"] not in allowed:
            raise ValueError("Report artifact is unrelated to the trusted comparison evidence")
        expected = {"execution_receipt": subject["output_bundle_ref"], "diagnostic": subject["output_bundle_ref"],
                    "input_lineage": subject["plan_ref"], "operator_intervention": subject["output_bundle_ref"]}
        if item["kind"] in expected and item["artifact_ref"] != expected[item["kind"]]:
            raise ValueError("Report evidence label does not match its typed receipt role")
    return receipt


def _dimension(state, reasons, evidence_ids=()):
    return {"state": state, "reason_codes": sorted(set(reasons)), "evidence_ids": sorted(set(evidence_ids))}


def _derive_dimensions(evidence, *, revalidated, available, omissions, reviewed_claims, reviewed_plots):
    """State aggregation after the public builder has enforced source trust."""
    ids = lambda key: [ref for item in evidence[key] for ref in item["evidence_ids"]]
    dimensions = {key: _dimension("not_assessed", ["trusted_comparison_not_revalidated"]) for key in DIMENSION_STATES if key != "qualified_review"}
    if revalidated:
        dimensions["execution"] = _dimension(evidence["execution"]["status"], evidence["execution"]["reason_codes"], evidence["execution"]["evidence_ids"])
        metrics = evidence["metric_results"]
        dimensions["comparability"] = _dimension("comparable" if metrics and all(item["status"] == "computed" for item in metrics)
            else "incomparable" if any(item["status"] in {"not_applicable", "failed"} for item in metrics) else "not_assessed",
            ["metric_specific_comparability"], ids("numeric_assessments"))
        diagnostics = evidence["diagnostics"]
        method = "invalid" if any(item["status"] == "invalid" for item in diagnostics) else "adequate" if available["diagnostic"] and available["tested_family"] else "not_assessed"
        dimensions["methodology"] = _dimension(method, [reason for item in diagnostics for reason in item["reason_codes"]] or ["diagnostics_absent"], ids("diagnostics"))
        decisions = {item["decision"] for item in evidence["numeric_assessments"]}
        agreement = "disagreement" if "disagreement" in decisions else "agreement" if decisions == {"agreement"} else "not_assessed"
        dimensions["publication_agreement"] = _dimension(agreement, [reason for item in evidence["numeric_assessments"] for reason in item["reason_codes"]] or ["no_agreement_decision"], ids("numeric_assessments"))
        interpretation = "unsupported" if method == "invalid" or (reviewed_claims and any(item["status"] == "unsupported" for item in evidence["claims"])) else (
            "supported" if method == "adequate" and not omissions and reviewed_claims and reviewed_plots else "not_assessed")
        dimensions["interpretation"] = _dimension(interpretation, ["invalid_method_or_unsupported_claim"] if interpretation == "unsupported"
            else ["scope_limited_reviewed_claim_and_plot_support"] if interpretation == "supported"
            else ["reviewed_source_and_plot_interpretation_context_absent"], ids("claims") + ids("plots"))
    dimensions["qualified_review"] = _dimension("not_established", ["authenticated_independent_adjudication_absent"])
    return dimensions


def _reviewed_claims(evidence, receipt, workspace, artifact_service):
    if not evidence["claims"]:
        return False
    from .rna_publication_profiles import load_reference_result_set, read_reference_json
    references = load_reference_result_set(receipt["reference_result_set_ref"], workspace=workspace,
        service=artifact_service, allow_evidence_read=True)
    findings = {item["claim_id"]: item for item in references["findings"]}
    registry = {item["evidence_id"]: item for item in evidence["artifact_registry"]}
    for claim in evidence["claims"]:
        finding = findings.get(claim["claim_id"])
        if finding is None:
            return False
        review = read_reference_json(finding["context_review_ref"], workspace=workspace, service=artifact_service)
        refs = [registry[key]["artifact_ref"] for key in claim["evidence_ids"]]
        if (review["claim"] != claim["text"] or finding["context_review_ref"] not in refs
                or claim["scope"] == "causal" or claim["status"] != {"supported": "supported",
                    "contradicted": "unsupported", "not_assessable": "not_assessed"}[review["conclusion"]]):
            return False
        if claim["evidence_class"] == "numerical_association" and not any(
                registry[key]["kind"] == "numeric_assessment" for key in claim["evidence_ids"]):
            return False
    return True


def _comparison_plot_tables_bound(payload, context, computed_metric_ids):
    """Match saved plot tables to revalidated deterministic comparison inputs."""
    if "projection_ref" not in payload:
        return False
    from .episode_proposals import _load
    from .rna_publication_metrics import validate_gene_table
    _, projection, _ = _load(payload["projection_ref"]["manifest_path"], payload["projection_ref"]["root_hash"], "figure-projection.json")
    reference = validate_gene_table(decode_json(_source_bytes(projection["reference_table_ref"])))
    actual = validate_gene_table(decode_json(_source_bytes(projection["actual_table_ref"])))
    matched_metrics = {metric_id for descriptor in context["gene_tables"]
        if descriptor["role"] == "reference" and descriptor["table"] == reference
        for metric_id in descriptor["metric_ids"] if metric_id in computed_metric_ids}
    return bool(matched_metrics) and any(descriptor["role"] == "actual"
        and descriptor["table"] == actual and projection["actual_table_ref"] in descriptor["source_evidence_refs"]
        and set(descriptor["metric_ids"]) & matched_metrics for descriptor in context["gene_tables"])


def _reviewed_plots(evidence, subject, artifact_service, *, workspace=None):
    if not evidence["plots"]:
        return False
    from .episode_proposals import _load
    from .figure_artifacts import load_publication_figure
    from .rna_publication_comparison import assessment_review_evidence
    if workspace is None or artifact_service is None:
        return False
    context = assessment_review_evidence(subject["subject_ref"]["manifest_path"], subject["subject_ref"]["root_hash"],
        workspace=workspace, artifact_service=artifact_service, allow_evidence_read=True)
    computed = {metric["metric_id"] for metric in evidence["metric_results"] if metric["status"] == "computed"}
    registry = {item["evidence_id"]: item for item in evidence["artifact_registry"]}
    for plot in evidence["plots"]:
        checked = False
        for key in plot["evidence_ids"]:
            item = registry[key]
            if item["kind"] != "reviewed_plot" or "manifest_path" not in item["artifact_ref"]:
                continue
            reference = item["artifact_ref"]
            try:
                _, review, _ = _load(reference["manifest_path"], reference["root_hash"], "pixel-review.json")
                _keys(review, {"format", "figure_ref", "figure_id", "image_sha256", "pixels_status", "reviewer_label",
                    "observations", "qualified_review", "scientific_validity_established"})
                figure, payload = load_publication_figure(review["figure_ref"]["manifest_path"], review["figure_ref"]["root_hash"],
                    artifact_service=artifact_service)
                comparison_bound = _comparison_plot_tables_bound(payload, context, computed)
                checked = (review["format"] == "devbench.figure-pixel-review/2" and review["figure_id"] == plot["figure_id"]
                    and review["image_sha256"] == figure["artifacts"]["png"]["sha256"]
                    and review["pixels_status"] == plot["pixels_status"] == "inspected" and plot["numeric_status"] == "assessed"
                    and figure["analysis_binding"]["output_bundle_ref"] == subject["output_bundle_ref"]
                    and figure["governed_output_status"] == "revalidated" and "projection_ref" in payload
                    and comparison_bound
                    and review["qualified_review"] == "not_established" and review["scientific_validity_established"] is False)
            except (ValueError, KeyError, OSError):
                checked = False
            if checked:
                break
        if not checked:
            return False
    return True


def build_assessment_report(subject, evidence, *, workspace=None, artifact_service=None):
    """Build distinct states; unverified declarations remain not assessed."""
    subject, evidence = _subject(subject), _evidence(evidence, workspace=workspace, artifact_service=artifact_service)
    trusted = _trusted_comparison(subject, evidence, workspace, artifact_service)
    reviewed_claims = trusted is not None and _reviewed_claims(evidence, trusted, workspace, artifact_service)
    reviewed_plots = trusted is not None and _reviewed_plots(evidence, subject, artifact_service, workspace=workspace)
    omissions = deepcopy(evidence["omitted_evidence"])
    if evidence["plots"] and not reviewed_plots:
        omissions.append({"kind": "reviewed_plot", "reason": "not_comparison_bound", "count": len(evidence["plots"])})
    available = {"execution_receipt": evidence["execution"]["status"] == "verified",
        "diagnostic": bool(evidence["diagnostics"]) and all(item["status"] != "unknown" for item in evidence["diagnostics"]),
        "tested_family": bool(evidence["tested_families"]) and all(item["complete"] for item in evidence["tested_families"]),
        "numeric_assessment": bool(evidence["numeric_assessments"]),
        "reviewed_plot": reviewed_plots,
        "source_claim": reviewed_claims and all(item["status"] != "not_assessed" for item in evidence["claims"])}
    for kind in evidence["required_evidence"]:
        if not available.get(kind, False) and not any(item["kind"] == kind for item in omissions):
            omissions.append({"kind": kind, "reason": "required_evidence_absent_or_unassessed", "count": 1})
    if trusted is None:
        omissions.append({"kind": "execution_receipt", "reason": "trusted_comparison_not_revalidated", "count": 1})
    dimensions = _derive_dimensions(evidence, revalidated=trusted is not None, available=available, omissions=omissions,
        reviewed_claims=reviewed_claims, reviewed_plots=reviewed_plots)
    return {"format": FORMAT, "subject": subject, "evidence": evidence, "dimensions": dimensions,
        "omitted_evidence": omissions, "evidence_completeness": {"complete": not omissions, "omitted_count": sum(item["count"] for item in omissions)},
        "comparison_verification": "revalidated" if trusted is not None else "not_assessed",
        "scope": SCOPE, **_AUTHORITY}


def _validate_assessment_report(record, *, workspace=None, artifact_service=None, recorded_only=False):
    record = _copy(record)
    _keys(record, {"format", "subject", "evidence", "dimensions", "omitted_evidence", "evidence_completeness", "comparison_verification", "scope", *_AUTHORITY})
    if record["format"] != FORMAT or record["scope"] != SCOPE or any(record[key] is not value for key, value in _AUTHORITY.items()):
        raise ValueError("Unsupported assessment authority or format")
    revalidate = not recorded_only or artifact_service is not None
    _subject(record["subject"], verify_refs=revalidate)
    _evidence(record["evidence"], workspace=workspace, artifact_service=artifact_service, verify_refs=revalidate)
    _keys(record["dimensions"], DIMENSION_STATES)
    known = {item["evidence_id"] for item in record["evidence"]["artifact_registry"]}
    for name, dimension in record["dimensions"].items():
        _keys(dimension, {"state", "reason_codes", "evidence_ids"})
        if dimension["state"] not in DIMENSION_STATES[name]:
            raise ValueError("Unknown separate assessment state")
        _texts(dimension["reason_codes"]); _texts(dimension["evidence_ids"])
        if not set(dimension["evidence_ids"]) <= known:
            raise ValueError("Assessment dimension cites fabricated evidence")
    if record["comparison_verification"] not in {"revalidated", "not_assessed"}:
        raise ValueError("Unknown comparison verification state")
    _keys(record["evidence_completeness"], {"complete", "omitted_count"})
    if (type(record["evidence_completeness"]["complete"]) is not bool
            or type(record["evidence_completeness"]["omitted_count"]) is not int
            or record["evidence_completeness"]["omitted_count"] < 0):
        raise ValueError("Invalid assessment evidence completeness")
    for omitted in _list(record["omitted_evidence"], 256):
        _keys(omitted, {"kind", "reason", "count"})
        if omitted["kind"] not in EVIDENCE_KINDS or type(omitted["count"]) is not int or omitted["count"] <= 0:
            raise ValueError("Invalid omitted assessment evidence")
        _text(omitted["reason"])
    if record["evidence_completeness"] != {"complete": not record["omitted_evidence"],
            "omitted_count": sum(item["count"] for item in record["omitted_evidence"])}:
        raise ValueError("Assessment evidence completeness conceals omissions")
    if not recorded_only or artifact_service is not None:
        rebuilt = build_assessment_report(record["subject"], record["evidence"], workspace=workspace, artifact_service=artifact_service)
        if rebuilt != record:
            raise ValueError("Assessment states differ from revalidated evidence; trusted context required")
    return record


def validate_assessment_report(record, *, workspace=None, artifact_service=None):
    """Revalidate states at a write boundary; no caller can waive the checks."""
    return _validate_assessment_report(record, workspace=workspace, artifact_service=artifact_service)


def _render_text(record):
    lines = ["RNA publication assessment", "Scope: " + record["scope"]]
    for name, state in record["dimensions"].items():
        lines.append(name + ": " + state["state"] + " (" + ", ".join(state["reason_codes"]) + ")")
    for metric in record["evidence"]["metric_results"]:
        lines.append("metric " + metric["metric_id"] + ": " + json.dumps({key: metric[key] for key in ("status", "value", "units", "denominators", "reason_codes")}, allow_nan=False))
    prefix = "verified comparator decision" if record["comparison_verification"] == "revalidated" else "supplied numeric assessment"
    for assessment in record["evidence"]["numeric_assessments"]:
        lines.append(prefix + " " + assessment["metric_id"] + ": " + assessment["decision"]
                     + " (" + ", ".join(assessment["reason_codes"]) + ")")
    for omitted in record["omitted_evidence"]:
        lines.append("omitted " + omitted["kind"] + ": " + omitted["reason"] + " (" + str(omitted["count"]) + ")")
    lines.append("Qualified independent review: not established. Clinical release: held.")
    return "\n".join(lines) + "\n"


def render_assessment_report(report, output, *, allow_evidence_read=False, workspace=None, artifact_service=None):
    if allow_evidence_read is not True:
        raise PermissionError("Report rendering requires evidence-reading authorization")
    report = validate_assessment_report(report, workspace=workspace, artifact_service=artifact_service)
    protected = [Path(ref["manifest_path"]).parent.parent if "manifest_path" in ref else Path(ref["path"])
                 for ref in report["subject"].values() if isinstance(ref, dict)]
    protected.extend(item["artifact_ref"].get("path", item["artifact_ref"].get("manifest_path")) for item in report["evidence"]["artifact_registry"])
    output = require_disjoint_output(_absolute(output), protected)
    from devbench.study.permissions import require_protected_storage
    require_protected_storage(output)
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "report.json", report)
    (output / "report.txt").write_text(_render_text(report), encoding="utf-8")
    audit = ExecutionDAG(output / "audit", actor_id="assessment-report-coordinator")
    for name in ("report.json", "report.txt"): audit.add_artifact(output / name, max_bytes=1_000_000)
    audit.append("analysis.rna_assessment_report", {"scope": SCOPE, "qualified_review": "not_established"})
    receipt = audit.finalize(analysis_fingerprint=digest(report))
    atomic_json(output / "root-receipt.json", receipt)
    return {"manifest_path": receipt["manifest_path"], "root_hash": receipt["root_hash"]}


def load_assessment_report(manifest, expected_root, *, allow_evidence_read=False, workspace=None, artifact_service=None):
    """Read a sealed recorded report; source rechecks require trusted context."""
    if allow_evidence_read is not True:
        raise PermissionError("Report loading requires evidence-reading authorization")
    ref = {"manifest_path": str(_absolute(manifest)), "root_hash": _retained(expected_root)}
    _reference(ref)
    path = Path(ref["manifest_path"])
    declared = decode_json(_read(path, 1_000_000))
    _bind_manifest_snapshot(declared, ref["root_hash"])
    artifacts = {item["name"]: item for item in declared["artifacts"]}
    if set(artifacts) != {"report.json", "report.txt"}:
        raise ValueError("Unexpected assessment report proof structure")
    bodies = {name: _read(path.parent.parent / name, 1_000_000) for name in artifacts}
    if any(hashlib.sha256(bodies[name]).hexdigest() != artifacts[name]["sha256"] for name in artifacts):
        raise ValueError("Assessment report changed after sealing")
    record = decode_json(bodies["report.json"])
    _validate_assessment_report(record, workspace=workspace, artifact_service=artifact_service, recorded_only=True)
    if digest(record) != declared["analysis_fingerprint"] or bodies["report.txt"] != _render_text(record).encode("utf-8"):
        raise ValueError("Assessment rendered report does not match its JSON record")
    return record
